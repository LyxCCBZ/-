from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from db import get_db
from security import login_required, utcnow
from services import question_bank as bank_service
from services.validation import ValidationError
from utils import json_safe, normalize_username, parse_object_id


bp = Blueprint("question_bank", __name__)


def _load_bank(bank_id, manage=False):
    oid = parse_object_id(bank_id)
    if oid is None:
        abort(404)
    bank = get_db().question_bank.find_one({"_id": oid})
    if bank is None:
        abort(404)
    if not bank_service.can_access(bank, g.current_user["_id"]):
        abort(404)
    if manage and not bank_service.can_manage(bank, g.current_user["_id"]):
        abort(403)
    return bank


def _owned_draft_survey(survey_id):
    oid = parse_object_id(survey_id)
    if oid is None:
        abort(404)
    survey = get_db().surveys.find_one({"_id": oid, "owner_id": g.current_user["_id"]})
    if survey is None:
        abort(404)
    return survey


def _next_order(db, survey_id):
    last = db.questions.find_one({"survey_id": survey_id}, sort=[("order", -1)])
    return (last["order"] + 1) if last else 1


@bp.get("/question-bank")
@login_required
def index():
    db = get_db()
    user_id = g.current_user["_id"]
    owned = list(db.question_bank.find({"owner_id": user_id}).sort("updated_at", -1))
    shared = list(db.question_bank.find({"shared_with": user_id}).sort("updated_at", -1))
    usage_rows = db.questions.aggregate(
        [
            {"$match": {"library_question_id": {"$ne": None}}},
            {"$group": {"_id": "$library_question_id", "count": {"$sum": 1}}},
        ]
    )
    usage_counts = {row["_id"]: row["count"] for row in usage_rows}
    owner_ids = {bank["owner_id"] for bank in shared}
    owners = {
        user["_id"]: user["username"] for user in db.users.find({"_id": {"$in": list(owner_ids)}})
    }
    version_counts = {}
    for bank in owned + shared:
        version_counts[bank["_id"]] = db.question_versions.count_documents({"bank_id": bank["_id"]})
    return render_template(
        "question_bank.html",
        owned=owned,
        shared=shared,
        usage_counts=usage_counts,
        version_counts=version_counts,
        owners=owners,
    )


@bp.route("/question-bank/new", methods=["GET", "POST"])
@login_required
def new_question():
    if request.method == "POST":
        try:
            bank = bank_service.create_bank_question(get_db(), g.current_user["_id"], request.form)
        except ValidationError as exc:
            flash(exc.message, "error")
        else:
            flash("题目已保存到题库，并创建了版本 v1。", "success")
            return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))
    return render_template("bank_question_new.html")


@bp.post("/question-bank")
@login_required
def create_question():
    try:
        bank = bank_service.create_bank_question(get_db(), g.current_user["_id"], request.form)
    except ValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("question_bank.new_question"))
    flash("题目已保存到题库，并创建了版本 v1。", "success")
    return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))


@bp.get("/question-bank/<bank_id>")
@login_required
def detail(bank_id):
    bank = _load_bank(bank_id)
    db = get_db()
    versions = bank_service.versions_for(db, bank["_id"])
    by_id = {version["_id"]: version for version in versions}
    for version in versions:
        parent = by_id.get(version.get("parent_version_id"))
        version["parent_label"] = f"v{parent['version_number']}" if parent else "无（初始版本）"
        restored = by_id.get(version.get("restored_from_version_id"))
        version["restored_label"] = f"v{restored['version_number']}" if restored else None
    current = by_id.get(bank["current_version_id"])
    shared_users = list(
        db.users.find({"_id": {"$in": bank.get("shared_with", [])}}).sort("username", 1)
    )
    return render_template(
        "bank_question_detail.html",
        bank=bank,
        current=current,
        versions=versions,
        usages=bank_service.question_usages(db, bank, g.current_user["_id"]),
        shared_users=shared_users,
        is_owner=bank_service.can_manage(bank, g.current_user["_id"]),
    )


@bp.post("/question-bank/<bank_id>/versions")
@login_required
def create_version(bank_id):
    bank = _load_bank(bank_id, manage=True)
    try:
        version = bank_service.create_bank_version(get_db(), bank, request.form, g.current_user["_id"])
    except ValidationError as exc:
        flash(exc.message, "error")
    else:
        flash(
            f"已创建新版本 v{version['version_number']}。此前引用旧版本的问卷不受影响。",
            "success",
        )
    return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))


@bp.post("/question-bank/<bank_id>/restore/<version_id>")
@login_required
def restore_version(bank_id, version_id):
    bank = _load_bank(bank_id, manage=True)
    version_oid = parse_object_id(version_id)
    version = bank_service.get_version(get_db(), bank["_id"], version_oid) if version_oid else None
    if version is None:
        abort(404)
    try:
        restored = bank_service.restore_bank_version(get_db(), bank, version, g.current_user["_id"])
    except ValidationError as exc:
        flash(exc.message, "error")
    else:
        flash(
            f"已基于 v{version['version_number']} 恢复为 v{restored['version_number']}，历史版本仍然保留。",
            "success",
        )
    return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))


@bp.post("/question-bank/<bank_id>/share")
@login_required
def share(bank_id):
    bank = _load_bank(bank_id, manage=True)
    raw = (request.form.get("usernames") or "").replace("，", ",")
    names = [normalize_username(item) for item in raw.split(",") if item.strip()]
    if not names:
        flash("请输入要共享的用户名。", "error")
        return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))

    db = get_db()
    users = list(db.users.find({"username": {"$in": names}}))
    found = {user["username"]: user for user in users}
    missing = [name for name in names if name not in found]
    targets = [
        user["_id"]
        for name, user in found.items()
        if user["_id"] != bank["owner_id"]
    ]
    if targets:
        db.question_bank.update_one(
            {"_id": bank["_id"]},
            {"$addToSet": {"shared_with": {"$each": targets}}, "$set": {"updated_at": utcnow()}},
        )
    if missing:
        flash("以下用户不存在：" + "、".join(missing), "warning")
    if targets:
        flash("题目已共享，对方可以在自己的问卷中直接选用。", "success")
    elif not missing:
        flash("不能将题目共享给自己。", "warning")
    return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))


@bp.post("/question-bank/<bank_id>/unshare")
@login_required
def unshare(bank_id):
    bank = _load_bank(bank_id, manage=True)
    user_oid = parse_object_id(request.form.get("user_id"))
    if user_oid is None:
        abort(404)
    get_db().question_bank.update_one(
        {"_id": bank["_id"]},
        {"$pull": {"shared_with": user_oid}, "$set": {"updated_at": utcnow()}},
    )
    flash("已取消共享。对方已创建的问卷仍保留原有题目快照。", "success")
    return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))


@bp.post("/question-bank/<bank_id>/fork")
@login_required
def fork(bank_id):
    bank = _load_bank(bank_id)
    if bank_service.can_manage(bank, g.current_user["_id"]):
        flash("这是你自己的题目，无需复制。", "warning")
        return redirect(url_for("question_bank.detail", bank_id=bank["_id"]))
    version = bank_service.current_version(get_db(), bank)
    if version is None:
        abort(404)
    content = {field: version[field] for field in bank_service.CONTENT_FIELDS}
    new_bank = bank_service.create_bank_from_content(
        get_db(), g.current_user["_id"], content
    )
    flash("已复制为你的题库题目，之后可以独立修改和创建版本。", "success")
    return redirect(url_for("question_bank.detail", bank_id=new_bank["_id"]))


@bp.get("/question-bank/<bank_id>/stats")
@login_required
def stats(bank_id):
    bank = _load_bank(bank_id)
    payload = bank_service.cross_survey_statistics(get_db(), bank, g.current_user["_id"])
    return render_template("bank_stats.html", **payload)


@bp.get("/api/question-bank/<bank_id>/stats")
@login_required
def stats_api(bank_id):
    bank = _load_bank(bank_id)
    payload = bank_service.cross_survey_statistics(get_db(), bank, g.current_user["_id"])
    return jsonify(json_safe(payload))


@bp.post("/surveys/<survey_id>/questions/from-bank")
@login_required
def add_from_bank(survey_id):
    survey = _owned_draft_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能修改题目，请先保持草稿状态编辑。", "error")
        return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))
    version_oid = parse_object_id(request.form.get("version_id"))
    if version_oid is None:
        abort(404)
    db = get_db()
    version = db.question_versions.find_one({"_id": version_oid})
    if version is None:
        abort(404)
    bank = db.question_bank.find_one({"_id": version["bank_id"]})
    if bank is None or not bank_service.can_access(bank, g.current_user["_id"]):
        abort(404)
    question = bank_service.survey_question_document(
        db, bank, version, survey["_id"], _next_order(db, survey["_id"])
    )
    db.questions.insert_one(question)
    flash(
        f"已从题库添加“{version['title']}”（v{version['version_number']}）。问卷使用独立快照，后续改版不影响它。",
        "success",
    )
    return redirect(url_for("surveys.survey_detail", survey_id=survey["_id"]))
