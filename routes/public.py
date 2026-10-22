import hashlib
import hmac
import secrets

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError

from db import get_db
from security import login_required, utcnow
from services.rules import next_question, ordered_questions
from services.validation import ValidationError, validate_answer
from utils import format_local_datetime, parse_object_id


bp = Blueprint("public", __name__)


def _load_survey(public_id):
    survey = get_db().surveys.find_one({"public_id": public_id})
    if survey is None:
        abort(404)
    if survey["status"] == "published" and survey.get("deadline") and survey["deadline"] <= utcnow():
        get_db().surveys.update_one(
            {"_id": survey["_id"]},
            {"$set": {"status": "closed", "closed_at": utcnow(), "updated_at": utcnow()}},
        )
        survey["status"] = "closed"
    return survey


def _respondent_key(survey_id, user_id):
    message = f"{survey_id}:{user_id}".encode("utf-8")
    secret = current_app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _attempt_token(attempt_id):
    return (session.get("attempt_tokens") or {}).get(str(attempt_id))


def _load_attempt(attempt_id):
    oid = parse_object_id(attempt_id)
    if oid is None:
        abort(404)
    response = get_db().responses.find_one({"_id": oid})
    if response is None:
        abort(404)
    token = _attempt_token(oid)
    if not token or not hmac.compare_digest(_token_hash(token), response.get("access_token_hash", "")):
        abort(404)
    survey = get_db().surveys.find_one({"_id": response["survey_id"]})
    if survey is None:
        abort(404)
    return response, survey


def _store_attempt_token(attempt_id, token):
    tokens = dict(session.get("attempt_tokens") or {})
    tokens[str(attempt_id)] = token
    session["attempt_tokens"] = tokens


@bp.get("/survey/<public_id>")
@login_required
def survey_intro(public_id):
    survey = _load_survey(public_id)
    question_count = get_db().questions.count_documents({"survey_id": survey["_id"]})
    return render_template(
        "survey_intro.html",
        survey=survey,
        question_count=question_count,
        deadline_text=format_local_datetime(survey.get("deadline"), "无截止时间"),
    )


@bp.post("/survey/<public_id>/start")
@login_required
def start_attempt(public_id):
    survey = _load_survey(public_id)
    if survey["status"] != "published":
        flash("该问卷当前未开放填写。", "error")
        return redirect(url_for("public.survey_intro", public_id=public_id))

    questions = ordered_questions(get_db(), survey["_id"])
    if not questions:
        flash("问卷暂时没有题目，无法填写。", "error")
        return redirect(url_for("public.survey_intro", public_id=public_id))

    is_anonymous = bool(survey.get("allow_anonymous")) and bool(request.form.get("anonymous"))
    respondent_key = _respondent_key(survey["_id"], g.current_user["_id"])
    db = get_db()
    if not survey.get("allow_multiple"):
        submitted = db.responses.find_one(
            {
                "survey_id": survey["_id"],
                "respondent_key": respondent_key,
                "status": "submitted",
            }
        )
        if submitted:
            flash("该问卷不允许重复提交，你已经提交过一份答卷。", "error")
            return redirect(url_for("public.survey_intro", public_id=public_id))

        pending_responses = list(
            db.responses.find(
                {
                    "survey_id": survey["_id"],
                    "respondent_key": respondent_key,
                    "status": "in_progress",
                }
            ).sort("started_at", -1)
        )
        for pending in pending_responses:
            token = _attempt_token(pending["_id"])
            if token and hmac.compare_digest(
                _token_hash(token), pending.get("access_token_hash", "")
            ):
                return redirect(url_for("public.take_attempt", attempt_id=pending["_id"]))
        if pending_responses:
            flash("你已有一份未完成答卷，请回到原浏览器继续填写。", "warning")
            return redirect(url_for("public.survey_intro", public_id=public_id))

    token = secrets.token_urlsafe(32)
    now = utcnow()
    response = {
        "survey_id": survey["_id"],
        "respondent_id": None if is_anonymous else g.current_user["_id"],
        "respondent_key": respondent_key,
        "access_token_hash": _token_hash(token),
        "single_response": not survey.get("allow_multiple"),
        "is_anonymous": is_anonymous,
        "answers": [],
        "status": "in_progress",
        "current_question_id": questions[0]["_id"],
        "started_at": now,
        "submitted_at": None,
    }
    try:
        inserted = db.responses.insert_one(response)
    except DuplicateKeyError:
        existing = db.responses.find_one(
            {"survey_id": survey["_id"], "respondent_key": respondent_key}
        )
        if existing and existing["status"] == "submitted":
            flash("该问卷不允许重复提交，你已经提交过一份答卷。", "error")
        else:
            flash("你已有一份未完成答卷，请回到原浏览器继续填写。", "warning")
        return redirect(url_for("public.survey_intro", public_id=public_id))
    _store_attempt_token(inserted.inserted_id, token)
    return redirect(url_for("public.take_attempt", attempt_id=inserted.inserted_id))


@bp.get("/attempt/<attempt_id>")
@login_required
def take_attempt(attempt_id):
    response, survey = _load_attempt(attempt_id)
    if response["status"] == "submitted":
        return redirect(url_for("public.complete_attempt", attempt_id=response["_id"]))
    if survey["status"] != "published":
        return render_template("attempt_closed.html", survey=survey, response=response)

    questions = ordered_questions(get_db(), survey["_id"])
    current = next(
        (item for item in questions if item["_id"] == response.get("current_question_id")), None
    )
    if current is None:
        flash("答卷状态异常，请联系问卷创建者。", "error")
        return redirect(url_for("public.survey_intro", public_id=survey["public_id"]))
    return render_template(
        "take_question.html",
        survey=survey,
        response=response,
        question=current,
        question_number=current["order"],
        total_questions=len(questions),
        raw_value=None,
        error=None,
    )


@bp.post("/attempt/<attempt_id>/answer")
@login_required
def answer_attempt(attempt_id):
    response, survey = _load_attempt(attempt_id)
    if response["status"] == "submitted":
        return redirect(url_for("public.complete_attempt", attempt_id=response["_id"]))
    if survey["status"] != "published":
        return render_template("attempt_closed.html", survey=survey, response=response), 409

    db = get_db()
    questions = ordered_questions(db, survey["_id"])
    question = next(
        (item for item in questions if item["_id"] == response.get("current_question_id")), None
    )
    if question is None:
        abort(404)

    raw_value = request.form.getlist("answer") if question["type"] == "multiple" else request.form.get("answer")
    try:
        value = validate_answer(question, raw_value)
    except ValidationError as exc:
        return (
            render_template(
                "take_question.html",
                survey=survey,
                response=response,
                question=question,
                question_number=question["order"],
                total_questions=len(questions),
                raw_value=raw_value,
                error=exc.message,
            ),
            422,
        )

    answers = [item for item in response.get("answers", []) if item["question_id"] != question["_id"]]
    answers.append({"question_id": question["_id"], "value": value, "answered_at": utcnow()})
    following = next_question(question, value, questions)
    now = utcnow()
    if following is None:
        update = {
            "answers": answers,
            "status": "submitted",
            "current_question_id": None,
            "submitted_at": now,
        }
    else:
        update = {"answers": answers, "current_question_id": following["_id"]}

    result = db.responses.update_one(
        {"_id": response["_id"], "status": "in_progress"}, {"$set": update}
    )
    if result.matched_count == 0:
        flash("答卷状态已变化，请刷新查看。", "warning")
    if following is None:
        return redirect(url_for("public.complete_attempt", attempt_id=response["_id"]))
    return redirect(url_for("public.take_attempt", attempt_id=response["_id"]))


@bp.get("/attempt/<attempt_id>/complete")
@login_required
def complete_attempt(attempt_id):
    response, survey = _load_attempt(attempt_id)
    if response["status"] != "submitted":
        return redirect(url_for("public.take_attempt", attempt_id=response["_id"]))
    return render_template("attempt_complete.html", survey=survey, response=response)

