from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from security import utcnow
from services.validation import ValidationError, build_question_document


CONTENT_FIELDS = ("title", "type", "required", "options", "validation")
TYPE_LABELS = {"single": "单选", "multiple": "多选", "text": "文本", "number": "数字"}


def question_content(form):
    document = build_question_document(form)
    return {field: document[field] for field in CONTENT_FIELDS}


def create_bank_question(db, owner_id, form, now=None):
    return create_bank_from_content(db, owner_id, question_content(form), now=now)


def create_bank_from_content(db, owner_id, content, now=None):
    now = now or utcnow()
    bank_id = ObjectId()
    version_id = ObjectId()
    bank = {
        "_id": bank_id,
        "owner_id": owner_id,
        "title": content["title"],
        "type": content["type"],
        "current_version_id": version_id,
        "current_version": 1,
        "shared_with": [],
        "created_at": now,
        "updated_at": now,
    }
    version = {
        "_id": version_id,
        "bank_id": bank_id,
        "version_number": 1,
        **content,
        "parent_version_id": None,
        "restored_from_version_id": None,
        "created_by": owner_id,
        "created_at": now,
    }
    db.question_versions.insert_one(version)
    db.question_bank.insert_one(bank)
    return bank


def append_version(db, bank, content, created_by, restored_from_version_id=None):
    for _attempt in range(5):
        latest = db.question_versions.find_one(
            {"bank_id": bank["_id"]}, sort=[("version_number", -1)]
        )
        version_number = (latest["version_number"] + 1) if latest else 1
        version_id = ObjectId()
        now = utcnow()
        current_bank = db.question_bank.find_one({"_id": bank["_id"]}) or bank
        version = {
            "_id": version_id,
            "bank_id": bank["_id"],
            "version_number": version_number,
            **content,
            "parent_version_id": current_bank.get("current_version_id"),
            "restored_from_version_id": restored_from_version_id,
            "created_by": created_by,
            "created_at": now,
        }
        try:
            db.question_versions.insert_one(version)
        except DuplicateKeyError:
            continue
        db.question_bank.update_one(
            {"_id": bank["_id"]},
            {
                "$set": {
                    "title": content["title"],
                    "type": content["type"],
                    "current_version_id": version_id,
                    "current_version": version_number,
                    "updated_at": now,
                }
            },
        )
        return version
    raise ValidationError("版本号生成冲突，请稍后重试")


def create_bank_version(db, bank, form, created_by):
    return append_version(db, bank, question_content(form), created_by)


def restore_bank_version(db, bank, version, created_by):
    content = {field: version[field] for field in CONTENT_FIELDS}
    return append_version(db, bank, content, created_by, restored_from_version_id=version["_id"])


def get_bank(db, bank_id):
    return db.question_bank.find_one({"_id": bank_id})


def get_version(db, bank_id, version_id):
    return db.question_versions.find_one({"_id": version_id, "bank_id": bank_id})


def can_access(bank, user_id):
    return bank["owner_id"] == user_id or user_id in bank.get("shared_with", [])


def can_manage(bank, user_id):
    return bank["owner_id"] == user_id


def current_version(db, bank):
    return db.question_versions.find_one({"_id": bank["current_version_id"]})


def versions_for(db, bank_id, newest_first=True):
    direction = -1 if newest_first else 1
    return list(db.question_versions.find({"bank_id": bank_id}).sort("version_number", direction))


def link_fields(bank, version):
    return {
        "library_question_id": bank["_id"],
        "library_version_id": version["_id"],
        "library_version_number": version["version_number"],
        "library_owner_id": bank["owner_id"],
    }


def survey_question_document(db, bank, version, survey_id, order, now=None):
    now = now or utcnow()
    return {
        "survey_id": survey_id,
        **link_fields(bank, version),
        "order": order,
        "title": version["title"],
        "type": version["type"],
        "required": version["required"],
        "options": version["options"],
        "validation": version["validation"],
        "jumps": [],
        "created_at": now,
        "updated_at": now,
    }


def list_accessible_bank_questions(db, user_id):
    banks = list(
        db.question_bank.find(
            {"$or": [{"owner_id": user_id}, {"shared_with": user_id}]}
        ).sort("updated_at", -1)
    )
    if not banks:
        return []
    version_ids = [bank["current_version_id"] for bank in banks]
    version_map = {
        version["_id"]: version
        for version in db.question_versions.find({"_id": {"$in": version_ids}})
    }
    owner_ids = {bank["owner_id"] for bank in banks}
    users = {
        user["_id"]: user["username"] for user in db.users.find({"_id": {"$in": list(owner_ids)}})
    }
    rows = []
    for bank in banks:
        version = version_map.get(bank["current_version_id"])
        if version is None:
            continue
        rows.append(
            {
                "bank": bank,
                "version": version,
                "owner_name": users.get(bank["owner_id"], "未知用户"),
                "is_owner": bank["owner_id"] == user_id,
                "version_count": db.question_versions.count_documents({"bank_id": bank["_id"]}),
            }
        )
    return rows


def bank_options_for_survey(db, user_id):
    banks = list(
        db.question_bank.find(
            {"$or": [{"owner_id": user_id}, {"shared_with": user_id}]}
        ).sort("updated_at", -1)
    )
    if not banks:
        return []
    bank_ids = [bank["_id"] for bank in banks]
    versions = list(
        db.question_versions.find({"bank_id": {"$in": bank_ids}}).sort(
            [("bank_id", 1), ("version_number", -1)]
        )
    )
    owner_ids = {bank["owner_id"] for bank in banks}
    users = {
        user["_id"]: user["username"] for user in db.users.find({"_id": {"$in": list(owner_ids)}})
    }
    banks_by_id = {bank["_id"]: bank for bank in banks}
    options = []
    for version in versions:
        bank = banks_by_id[version["bank_id"]]
        options.append(
            {
                "bank_id": bank["_id"],
                "version_id": version["_id"],
                "version_number": version["version_number"],
                "title": version["title"],
                "type": version["type"],
                "type_label": TYPE_LABELS.get(version["type"], version["type"]),
                "owner_name": users.get(bank["owner_id"], "未知用户"),
                "is_owner": bank["owner_id"] == user_id,
                "is_current": version["_id"] == bank["current_version_id"],
            }
        )
    return options


def question_usages(db, bank, viewer_id):
    snapshots = list(
        db.questions.find({"library_question_id": bank["_id"]}).sort("created_at", 1)
    )
    if not snapshots:
        return []
    survey_ids = list({snapshot["survey_id"] for snapshot in snapshots})
    surveys = {
        survey["_id"]: survey
        for survey in db.surveys.find({"_id": {"$in": survey_ids}})
    }
    is_owner = bank["owner_id"] == viewer_id
    rows = []
    for snapshot in snapshots:
        survey = surveys.get(snapshot["survey_id"])
        if survey is None:
            continue
        if not is_owner and survey["owner_id"] != viewer_id:
            continue
        rows.append(
            {
                "survey": survey,
                "question": snapshot,
                "answered_count": db.responses.count_documents(
                    {
                        "survey_id": survey["_id"],
                        "status": "submitted",
                        "answers.question_id": snapshot["_id"],
                    }
                ),
            }
        )
    rows.sort(key=lambda row: row["survey"].get("created_at"), reverse=True)
    return rows


def _summarize_values(question, values):
    summary = {
        "answered_count": len(values),
        "option_counts": [],
        "average": None,
        "minimum": None,
        "maximum": None,
    }
    if question["type"] in {"single", "multiple"}:
        counts = {option["key"]: 0 for option in question.get("options", [])}
        for value in values:
            selected = value if isinstance(value, list) else [value]
            for key in selected:
                if key in counts:
                    counts[key] += 1
        summary["option_counts"] = [
            {
                "key": option["key"],
                "text": option["text"],
                "count": counts[option["key"]],
                "percent": round(counts[option["key"]] * 100 / len(values), 1) if values else 0,
            }
            for option in question.get("options", [])
        ]
    if question["type"] == "number":
        numeric_values = [float(value) for value in values if isinstance(value, (int, float))]
        if numeric_values:
            summary["average"] = round(sum(numeric_values) / len(numeric_values), 2)
            summary["minimum"] = min(numeric_values)
            summary["maximum"] = max(numeric_values)
    return summary


def cross_survey_statistics(db, bank, viewer_id):
    snapshots = list(
        db.questions.find({"library_question_id": bank["_id"]}).sort(
            [("library_version_number", 1), ("order", 1)]
        )
    )
    survey_ids = list({snapshot["survey_id"] for snapshot in snapshots})
    surveys = {
        survey["_id"]: survey
        for survey in db.surveys.find({"_id": {"$in": survey_ids}})
    } if survey_ids else {}

    is_owner = bank["owner_id"] == viewer_id
    if not is_owner:
        snapshots = [
            snapshot
            for snapshot in snapshots
            if surveys.get(snapshot["survey_id"], {}).get("owner_id") == viewer_id
        ]
        survey_ids = list({snapshot["survey_id"] for snapshot in snapshots})
        surveys = {survey_id: surveys[survey_id] for survey_id in survey_ids if survey_id in surveys}

    values_by_question = {}
    response_counts = {}
    if survey_ids:
        grouped = db.responses.aggregate(
            [
                {"$match": {"survey_id": {"$in": survey_ids}, "status": "submitted"}},
                {"$unwind": "$answers"},
                {
                    "$group": {
                        "_id": "$answers.question_id",
                        "values": {"$push": "$answers.value"},
                    }
                },
            ]
        )
        values_by_question = {str(row["_id"]): row["values"] for row in grouped}
        count_rows = db.responses.aggregate(
            [
                {"$match": {"survey_id": {"$in": survey_ids}, "status": "submitted"}},
                {"$group": {"_id": "$survey_id", "count": {"$sum": 1}}},
            ]
        )
        response_counts = {row["_id"]: row["count"] for row in count_rows}

    usages = []
    version_groups = {}
    option_totals = {}
    numeric_values = []
    text_answers = []
    total_answered = 0
    choice_answered = 0
    response_total = 0

    for survey_id in survey_ids:
        response_total += response_counts.get(survey_id, 0)

    for snapshot in snapshots:
        survey = surveys.get(snapshot["survey_id"])
        if survey is None:
            continue
        values = values_by_question.get(str(snapshot["_id"]), [])
        summary = _summarize_values(snapshot, values)
        version_number = snapshot.get("library_version_number") or 1
        row = {
            "survey": survey,
            "question": snapshot,
            "version_number": version_number,
            "answered_count": summary["answered_count"],
        }
        usages.append(row)
        total_answered += summary["answered_count"]
        if snapshot["type"] in {"single", "multiple"}:
            choice_answered += summary["answered_count"]
            for option in summary["option_counts"]:
                key = (option["key"], option["text"])
                option_totals[key] = option_totals.get(key, 0) + option["count"]
        if snapshot["type"] == "number":
            numeric_values.extend(
                float(value) for value in values if isinstance(value, (int, float))
            )
        if snapshot["type"] == "text":
            for value in values:
                if len(text_answers) < 200:
                    text_answers.append(
                        {
                            "value": value,
                            "survey_title": survey["title"],
                            "version_number": version_number,
                        }
                    )

        group = version_groups.setdefault(
            version_number,
            {
                "version_number": version_number,
                "title": snapshot["title"],
                "type": snapshot["type"],
                "usage_count": 0,
                "answered_count": 0,
                "option_counts": [],
                "average": None,
                "minimum": None,
                "maximum": None,
                "values": [],
            },
        )
        group["usage_count"] += 1
        group["answered_count"] += summary["answered_count"]
        group["values"].extend(values)

    for group in version_groups.values():
        sample = next(
            snapshot
            for snapshot in snapshots
            if (snapshot.get("library_version_number") or 1) == group["version_number"]
        )
        summary = _summarize_values(sample, group.pop("values"))
        group["option_counts"] = summary["option_counts"]
        group["average"] = summary["average"]
        group["minimum"] = summary["minimum"]
        group["maximum"] = summary["maximum"]

    number_summary = {"average": None, "minimum": None, "maximum": None, "count": 0}
    if numeric_values:
        number_summary = {
            "average": round(sum(numeric_values) / len(numeric_values), 2),
            "minimum": min(numeric_values),
            "maximum": max(numeric_values),
            "count": len(numeric_values),
        }

    option_rows = [
        {
            "key": key,
            "text": text,
            "count": count,
            "percent": round(count * 100 / choice_answered, 1) if choice_answered else 0,
        }
        for (key, text), count in option_totals.items()
    ]
    option_rows.sort(key=lambda row: (-row["count"], row["text"]))

    return {
        "bank": bank,
        "is_owner": is_owner,
        "usage_count": len(usages),
        "survey_count": len({row["survey"]["_id"] for row in usages}),
        "response_count": response_total,
        "total_answered": total_answered,
        "versions": sorted(version_groups.values(), key=lambda item: item["version_number"]),
        "usages": usages,
        "option_totals": option_rows,
        "number_summary": number_summary,
        "text_answers": text_answers,
    }
