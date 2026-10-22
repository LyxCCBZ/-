def survey_statistics(db, survey_id, only_question_id=None):
    questions = list(db.questions.find({"survey_id": survey_id}).sort("order", 1))
    if only_question_id is not None:
        questions = [question for question in questions if question["_id"] == only_question_id]

    response_count = db.responses.count_documents({"survey_id": survey_id, "status": "submitted"})
    grouped = db.responses.aggregate(
        [
            {"$match": {"survey_id": survey_id, "status": "submitted"}},
            {"$unwind": "$answers"},
            {
                "$group": {
                    "_id": "$answers.question_id",
                    "values": {"$push": "$answers.value"},
                }
            },
        ]
    )
    values_by_question = {str(item["_id"]): item["values"] for item in grouped}

    results = []
    for question in questions:
        values = values_by_question.get(str(question["_id"]), [])
        item = {
            "question": question,
            "answered_count": len(values),
            "answer_values": values,
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
            item["option_counts"] = [
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
                item["average"] = round(sum(numeric_values) / len(numeric_values), 2)
                item["minimum"] = min(numeric_values)
                item["maximum"] = max(numeric_values)

        results.append(item)

    return {"survey_id": survey_id, "response_count": response_count, "questions": results}


def response_counts_by_survey(db, survey_ids):
    ids = list(survey_ids)
    if not ids:
        return {}
    rows = db.responses.aggregate(
        [
            {"$match": {"survey_id": {"$in": ids}, "status": "submitted"}},
            {"$group": {"_id": "$survey_id", "count": {"$sum": 1}}},
        ]
    )
    return {str(row["_id"]): row["count"] for row in rows}

