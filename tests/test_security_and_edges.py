from datetime import timedelta

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from db import get_db
from security import utcnow, verify_password
from services.rules import ordered_questions
from services.validation import (
    ValidationError,
    build_question_document,
    parse_jump_condition,
    validate_answer,
)
from tests.conftest import add_question, create_survey, publish, register_and_login


def login(client, username, password):
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "我的问卷" in response.get_data(as_text=True)


def test_registration_rejects_duplicate_username_and_hashes_password(app, client):
    response = client.post(
        "/register",
        data={"username": "Alice", "password": "secret123", "confirm_password": "secret123"},
        follow_redirects=True,
    )
    assert "注册成功" in response.get_data(as_text=True)

    with app.app_context():
        user = get_db().users.find_one({"username": "alice"})
        assert user is not None
        assert "password" not in user
        assert user["password_hash"] != "secret123"
        assert verify_password(user["password_hash"], "secret123")

    duplicate = client.post(
        "/register",
        data={"username": "ALICE", "password": "another123", "confirm_password": "another123"},
        follow_redirects=True,
    )
    assert "已被注册" in duplicate.get_data(as_text=True)
    with app.app_context():
        assert get_db().users.count_documents({"username": "alice"}) == 1


def test_login_rejects_external_and_backslash_redirect_targets(app, client):
    register_and_login(client, username="redirect_user", password="secret123")

    for target in (
        "https://evil.example/phish",
        "//evil.example/phish",
        "/\\evil.example/phish",
    ):
        fresh = app.test_client()
        response = fresh.post(
            "/login",
            data={"username": "redirect_user", "password": "secret123", "next": target},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"] == "/dashboard"

    safe = app.test_client()
    response = safe.post(
        "/login",
        data={"username": "redirect_user", "password": "secret123", "next": "/dashboard"},
        follow_redirects=False,
    )
    assert response.headers["Location"] == "/dashboard"


def test_single_submission_cannot_be_bypassed_with_parallel_attempts(app, client):
    register_and_login(client, username="single_user", password="secret123")
    survey_id = create_survey(client, title="单次提交保护", anonymous=True, multiple=False)
    add_question(client, survey_id, title="唯一题目", type="single", options="是\n否", required="on")
    publish(client, survey_id)

    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})
        user = get_db().users.find_one({"username": "single_user"})

    first = client.post(
        f"/survey/{survey['public_id']}/start",
        data={"anonymous": "on"},
        follow_redirects=False,
    )
    assert first.status_code == 302
    attempt_path = first.headers["Location"]
    attempt_id = attempt_path.rstrip("/").split("/")[-1]

    resumed = client.post(
        f"/survey/{survey['public_id']}/start",
        data={"anonymous": "on"},
        follow_redirects=False,
    )
    assert resumed.status_code == 302
    assert resumed.headers["Location"] == attempt_path

    second_session = app.test_client()
    login(second_session, "single_user", "secret123")
    blocked = second_session.post(
        f"/survey/{survey['public_id']}/start",
        data={"anonymous": "on"},
        follow_redirects=True,
    )
    assert "未完成答卷" in blocked.get_data(as_text=True)

    with app.app_context():
        db = get_db()
        assert db.responses.count_documents({"survey_id": ObjectId(survey_id)}) == 1
        pending = db.responses.find_one({"_id": ObjectId(attempt_id)})
        assert pending["is_anonymous"] is True
        assert pending["respondent_id"] is None
        duplicate_document = dict(pending)
        duplicate_document.pop("_id")
        duplicate_document["access_token_hash"] = "f" * 64
        with pytest.raises(DuplicateKeyError):
            db.responses.insert_one(duplicate_document)

    completed = client.post(
        f"/attempt/{attempt_id}/answer",
        data={"answer": "A"},
        follow_redirects=False,
    )
    assert "complete" in completed.headers["Location"]

    duplicate = second_session.post(
        f"/survey/{survey['public_id']}/start",
        data={"anonymous": "on"},
        follow_redirects=True,
    )
    assert "不允许重复提交" in duplicate.get_data(as_text=True)
    with app.app_context():
        assert get_db().responses.count_documents(
            {"survey_id": ObjectId(survey_id), "status": "submitted"}
        ) == 1


def test_non_finite_numbers_are_rejected():
    numeric_question = {
        "_id": ObjectId(),
        "type": "number",
        "required": True,
        "options": [],
        "validation": {"minimum": 0, "maximum": 120, "integer_only": False},
        "jumps": [],
    }
    for raw_value in ("nan", "NaN", "inf", "-inf", "Infinity", "1e309"):
        with pytest.raises(ValidationError):
            validate_answer(numeric_question, raw_value)
        with pytest.raises(ValidationError):
            parse_jump_condition(numeric_question, "gt", raw_value)

    for raw_limit in ("nan", "inf", "-Infinity", "1e309"):
        with pytest.raises(ValidationError):
            build_question_document(
                {
                    "title": "非法数字范围",
                    "type": "number",
                    "minimum": raw_limit,
                }
            )


def test_draft_survey_cannot_be_closed(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="草稿关闭测试")

    response = client.post(f"/surveys/{survey_id}/close", follow_redirects=True)
    assert "不能关闭" in response.get_data(as_text=True)

    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})
        assert survey["status"] == "draft"
        assert survey.get("closed_at") is None


def test_expired_survey_auto_closes_and_updates_timestamp(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="自动关闭测试")
    add_question(client, survey_id, title="题目", type="single", options="A\nB")
    publish(client, survey_id)

    old_time = utcnow() - timedelta(days=1)
    with app.app_context():
        db = get_db()
        db.surveys.update_one(
            {"_id": ObjectId(survey_id)},
            {
                "$set": {
                    "deadline": utcnow() - timedelta(minutes=1),
                    "updated_at": old_time,
                }
            },
        )
        survey = db.surveys.find_one({"_id": ObjectId(survey_id)})

    response = client.get(f"/survey/{survey['public_id']}")
    assert response.status_code == 200

    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})
        assert survey["status"] == "closed"
        assert survey["closed_at"] is not None
        assert survey["updated_at"] > old_time


def test_anonymous_flag_is_ignored_when_survey_disallows_it(app, client):
    register_and_login(client, username="named_user", password="secret123")
    survey_id = create_survey(client, title="禁止匿名", anonymous=False, multiple=True)
    add_question(client, survey_id, title="题目", type="single", options="A\nB", required="on")
    publish(client, survey_id)

    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})
        user = get_db().users.find_one({"username": "named_user"})

    started = client.post(
        f"/survey/{survey['public_id']}/start",
        data={"anonymous": "on"},
        follow_redirects=False,
    )
    attempt_id = started.headers["Location"].rstrip("/").split("/")[-1]
    with app.app_context():
        response = get_db().responses.find_one({"_id": ObjectId(attempt_id)})
        assert response["is_anonymous"] is False
        assert response["respondent_id"] == user["_id"]


def test_closed_survey_blocks_in_progress_answer(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="中途关闭", anonymous=True, multiple=True)
    add_question(client, survey_id, title="题目", type="single", options="A\nB", required="on")
    publish(client, survey_id)
    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})

    started = client.post(
        f"/survey/{survey['public_id']}/start",
        data={"anonymous": "on"},
        follow_redirects=False,
    )
    attempt_id = started.headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/surveys/{survey_id}/close", follow_redirects=True)

    blocked = client.post(f"/attempt/{attempt_id}/answer", data={"answer": "A"})
    assert blocked.status_code == 409
    assert "问卷已关闭" in blocked.get_data(as_text=True)
    with app.app_context():
        response = get_db().responses.find_one({"_id": ObjectId(attempt_id)})
        assert response["status"] == "in_progress"
        assert response["answers"] == []


def test_question_deletion_cleans_jumps_and_compacts_order(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="题目删除清理")
    add_question(client, survey_id, title="第一题", type="single", options="A\nB")
    add_question(client, survey_id, title="第二题", type="single", options="A\nB")
    add_question(client, survey_id, title="第三题", type="single", options="A\nB")

    with app.app_context():
        questions = ordered_questions(get_db(), ObjectId(survey_id))
    q1, q2, q3 = questions
    response = client.post(
        f"/surveys/{survey_id}/questions/{q1['_id']}/jumps",
        data={"operator": "eq", "value": "A", "target": str(q3["_id"])},
        follow_redirects=True,
    )
    assert "跳转规则已添加" in response.get_data(as_text=True)

    client.post(f"/surveys/{survey_id}/questions/{q2['_id']}/delete", follow_redirects=True)
    with app.app_context():
        questions = ordered_questions(get_db(), ObjectId(survey_id))
        assert [item["order"] for item in questions] == [1, 2]
        assert questions[0]["jumps"][0]["target_question_id"] == q3["_id"]

    client.post(f"/surveys/{survey_id}/questions/{q3['_id']}/delete", follow_redirects=True)
    with app.app_context():
        questions = ordered_questions(get_db(), ObjectId(survey_id))
        assert len(questions) == 1
        assert questions[0]["order"] == 1
        assert questions[0]["jumps"] == []
