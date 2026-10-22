import secrets

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from db import get_db
from security import login_required, utcnow
from services.rules import describe_condition, ordered_questions
from services.statistics import response_counts_by_survey, survey_statistics
from services.validation import ValidationError, build_question_document, parse_jump_condition
from utils import (
    datetime_local_value,
    format_local_datetime,
    json_safe,
    parse_local_datetime,
    parse_object_id,
)


bp = Blueprint("surveys", __name__)


def _owned_survey(survey_id, allow_closed=True):
    oid = parse_object_id(survey_id)
    if oid is None:
        abort(404)
    survey = get_db().surveys.find_one({"_id": oid, "owner_id": g.current_user["_id"]})
    if survey is None:
        abort(404)
    if not allow_closed and survey["status"] == "closed":
        abort(400)
    return survey


def _refresh_deadline(survey):
    if survey.get("deadline") and survey["status"] == "published" and survey["deadline"] <= utcnow():
        get_db().surveys.update_one(
            {"_id": survey["_id"], "status": "published"},
            {"$set": {"status": "closed", "closed_at": utcnow(), "updated_at": utcnow()}},
        )
        survey["status"] = "closed"
        survey["closed_at"] = utcnow()
    return survey


def _new_public_id():
    db = get_db()
    while True:
        candidate = secrets.token_urlsafe(9)
        if db.surveys.find_one({"public_id": candidate}, {"_id": 1}) is None:
            return candidate


@bp.get("/dashboard")
@login_required
def dashboard():
    surveys = list(get_db().surveys.find({"owner_id": g.current_user["_id"]}).sort("created_at", -1))
    surveys = [_refresh_deadline(survey) for survey in surveys]
    counts = response_counts_by_survey(get_db(), [survey["_id"] for survey in surveys])
    return render_template("dashboard.html", surveys=surveys, response_counts=counts)


@bp.route("/surveys/new", methods=["GET", "POST"])
@login_required
def create_survey():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        if not title or len(title) > 200:
            flash("问卷标题不能为空且不能超过 200 字。", "error")
        elif len(description) > 2000:
            flash("问卷说明不能超过 2000 字。", "error")
        else:
            now = utcnow()
            survey = {
                "public_id": _new_public_id(),
                "owner_id": g.current_user["_id"],
                "title": title,
                "description": description,
                "allow_anonymous": bool(request.form.get("allow_anonymous")),
                "allow_multiple": bool(request.form.get("allow_multiple")),
                "status": "draft",
                "deadline": None,
                "created_at": now,
                "updated_at": now,
                "published_at": None,
                "closed_at": None,
            }
            result = get_db().surveys.insert_one(survey)
            flash("问卷草稿已创建，请继续添加题目。", "success")
            return redirect(url_for("surveys.survey_detail", survey_id=result.inserted_id))

    return render_template("survey_new.html")


@bp.get("/surveys/<survey_id>")
@login_required
def survey_detail(survey_id):
    survey = _refresh_deadline(_owned_survey(survey_id))
    questions = ordered_questions(get_db(), survey["_id"])
    by_id = {str(item["_id"]): item for item in questions}
    for question in questions:
        question["jump_targets"] = [item for item in questions if item["order"] > question["order"]]
        for rule in question.get("jumps", []):
            rule["condition_label"] = describe_condition(rule["condition"], question)
            target = rule.get("target_question_id")
            if target == "END":
                rule["target_label"] = "结束问卷"
            else:
                target_question = by_id.get(str(target))
                rule["target_label"] = (
                    f"第 {target_question['order']} 题" if target_question else "已删除的题目"
                )
    submission_count = get_db().responses.count_documents(
        {"survey_id": survey["_id"], "status": "submitted"}
    )
    draft_count = get_db().responses.count_documents(
        {"survey_id": survey["_id"], "status": "in_progress"}
    )
    return render_template(
        "survey_detail.html",
        survey=survey,
        questions=questions,
        submission_count=submission_count,
        draft_count=draft_count,
        deadline_value=datetime_local_value(survey.get("deadline")),
    )


@bp.post("/surveys/<survey_id>/settings")
@login_required
def update_settings(survey_id):
    survey = _owned_survey(survey_id)
    if survey["status"] == "closed":
        flash("已关闭的问卷不能修改设置。", "error")
        return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))

    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    try:
        deadline = parse_local_datetime(request.form.get("deadline"))
    except ValueError:
        flash("截止时间格式不正确，请重新选择。", "error")
        return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))
    if not title or len(title) > 200:
        flash("问卷标题不能为空且不能超过 200 字。", "error")
    elif len(description) > 2000:
        flash("问卷说明不能超过 2000 字。", "error")
    elif deadline is not None and deadline <= utcnow():
        flash("截止时间必须晚于当前时间。", "error")
    else:
        update = {
            "title": title,
            "description": description,
            "deadline": deadline,
            "updated_at": utcnow(),
        }
        if survey["status"] == "draft":
            update["allow_anonymous"] = bool(request.form.get("allow_anonymous"))
            update["allow_multiple"] = bool(request.form.get("allow_multiple"))
        get_db().surveys.update_one({"_id": survey["_id"]}, {"$set": update})
        flash("问卷设置已保存。", "success")
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/publish")
@login_required
def publish_survey(survey_id):
    survey = _owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("只有草稿问卷可以发布。", "error")
    elif get_db().questions.count_documents({"survey_id": survey["_id"]}) == 0:
        flash("请至少添加一道题目后再发布。", "error")
    elif survey.get("deadline") and survey["deadline"] <= utcnow():
        flash("截止时间已过，请先修改截止时间。", "error")
    else:
        now = utcnow()
        get_db().surveys.update_one(
            {"_id": survey["_id"], "status": "draft"},
            {"$set": {"status": "published", "published_at": now, "updated_at": now}},
        )
        flash("问卷已发布，可以分发填写链接了。", "success")
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/close")
@login_required
def close_survey(survey_id):
    survey = _owned_survey(survey_id)
    if survey["status"] == "draft":
        flash("草稿问卷不能关闭，只有已发布问卷可以关闭。", "warning")
    elif survey["status"] == "closed":
        flash("问卷已经关闭。", "warning")
    else:
        now = utcnow()
        get_db().surveys.update_one(
            {"_id": survey["_id"], "status": "published"},
            {"$set": {"status": "closed", "closed_at": now, "updated_at": now}},
        )
        flash("问卷已关闭，新的填写将被阻止。", "success")
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/questions")
@login_required
def add_question(survey_id):
    survey = _owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能修改题目，请先保持草稿状态编辑。", "error")
        return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))
    try:
        question = build_question_document(request.form)
        last = get_db().questions.find_one({"survey_id": survey["_id"]}, sort=[("order", -1)])
        now = utcnow()
        question.update(
            {
                "survey_id": survey["_id"],
                "order": (last["order"] + 1) if last else 1,
                "created_at": now,
                "updated_at": now,
            }
        )
        get_db().questions.insert_one(question)
        flash("题目已添加。", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/questions/<question_id>/delete")
@login_required
def delete_question(survey_id, question_id):
    survey = _owned_survey(survey_id)
    question_oid = parse_object_id(question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能删除题目。", "error")
    elif question_oid is None or get_db().questions.find_one(
        {"_id": question_oid, "survey_id": survey["_id"]}
    ) is None:
        abort(404)
    else:
        db = get_db()
        db.questions.delete_one({"_id": question_oid, "survey_id": survey["_id"]})
        db.questions.update_many(
            {"survey_id": survey["_id"]},
            {"$pull": {"jumps": {"target_question_id": question_oid}}},
        )
        remaining = ordered_questions(db, survey["_id"])
        for index, item in enumerate(remaining, start=1):
            if item["order"] != index:
                db.questions.update_one({"_id": item["_id"]}, {"$set": {"order": index}})
        flash("题目已删除，相关跳转规则也已清理。", "success")
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/questions/<question_id>/jumps")
@login_required
def add_jump(survey_id, question_id):
    survey = _owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("只有草稿问卷可以配置跳转规则。", "error")
        return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))

    db = get_db()
    question_oid = parse_object_id(question_id)
    questions = ordered_questions(db, survey["_id"])
    question = next((item for item in questions if item["_id"] == question_oid), None)
    if question is None:
        abort(404)

    target_raw = request.form.get("target") or ""
    target = "END" if target_raw == "END" else parse_object_id(target_raw)
    if target != "END":
        target_question = next((item for item in questions if item["_id"] == target), None)
        if target_question is None or target_question["order"] <= question["order"]:
            flash("跳转目标必须是当前题目之后的题目，或结束问卷。", "error")
            return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))

    try:
        condition = parse_jump_condition(question, request.form.get("operator"), request.form.get("value"))
        db.questions.update_one(
            {"_id": question["_id"]},
            {
                "$push": {
                    "jumps": {
                        "condition": condition,
                        "target_question_id": target,
                    }
                },
                "$set": {"updated_at": utcnow()},
            },
        )
        flash("跳转规则已添加。规则按列表顺序匹配，命中第一条后生效。", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/questions/<question_id>/jumps/<int:rule_index>/delete")
@login_required
def delete_jump(survey_id, question_id, rule_index):
    survey = _owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("只有草稿问卷可以修改跳转规则。", "error")
        return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))
    question_oid = parse_object_id(question_id)
    question = get_db().questions.find_one({"_id": question_oid, "survey_id": survey["_id"]})
    if question is None:
        abort(404)
    jumps = question.get("jumps", [])
    if 0 <= rule_index < len(jumps):
        jumps.pop(rule_index)
        get_db().questions.update_one(
            {"_id": question["_id"]}, {"$set": {"jumps": jumps, "updated_at": utcnow()}}
        )
        flash("跳转规则已删除。", "success")
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))


@bp.get("/surveys/<survey_id>/stats")
@login_required
def stats(survey_id):
    survey = _refresh_deadline(_owned_survey(survey_id))
    only_question = parse_object_id(request.args.get("question"))
    statistics = survey_statistics(get_db(), survey["_id"], only_question)
    return render_template(
        "stats.html",
        survey=survey,
        statistics=statistics,
        only_question=only_question,
    )


@bp.get("/api/surveys/<survey_id>/stats")
@login_required
def stats_api(survey_id):
    survey = _owned_survey(survey_id)
    payload = survey_statistics(get_db(), survey["_id"])
    payload["survey"] = {
        "_id": survey["_id"],
        "title": survey["title"],
        "status": survey["status"],
        "public_id": survey["public_id"],
    }
    return jsonify(json_safe(payload))

