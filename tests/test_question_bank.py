from bson import ObjectId

from db import get_db
from services.rules import ordered_questions
from tests.conftest import add_question, create_survey, publish, register_and_login


def _bank_id(response):
    assert response.status_code == 302
    return response.headers["Location"].rstrip("/").split("/")[-1]


def create_bank_question(client, title="你的年龄段是？", type="single", options="18 岁以下\n18-25 岁\n26-35 岁", **overrides):
    data = {"title": title, "type": type, "options": options}
    data.update(overrides)
    return _bank_id(
        client.post("/question-bank", data=data, follow_redirects=False)
    )


def add_from_bank(client, survey_id, version_id):
    return client.post(
        f"/surveys/{survey_id}/questions/from-bank",
        data={"version_id": str(version_id)},
        follow_redirects=True,
    )


def bank_docs(app, bank_id):
    with app.app_context():
        db = get_db()
        bank = db.question_bank.find_one({"_id": ObjectId(bank_id)})
        versions = list(
            db.question_versions.find({"bank_id": ObjectId(bank_id)}).sort("version_number", 1)
        )
        return bank, versions


def survey_questions(app, survey_id):
    with app.app_context():
        return ordered_questions(get_db(), ObjectId(survey_id))


def survey_doc(app, survey_id):
    with app.app_context():
        return get_db().surveys.find_one({"_id": ObjectId(survey_id)})


def submit_answers(client, public_id, answers, anonymous=True):
    start = client.post(
        f"/survey/{public_id}/start",
        data={"anonymous": "on"} if anonymous else {},
        follow_redirects=False,
    )
    assert start.status_code == 302
    attempt_id = start.headers["Location"].rstrip("/").split("/")[-1]
    for value in answers:
        payload = {"answer": value}
        result = client.post(
            f"/attempt/{attempt_id}/answer", data=payload, follow_redirects=False
        )
        assert result.status_code == 302
    return attempt_id


def test_bank_question_is_copied_as_snapshot_and_new_version_keeps_old_survey(app, client):
    register_and_login(client)
    bank_id = create_bank_question(client, required="on")
    bank, versions = bank_docs(app, bank_id)
    assert bank["current_version"] == 1
    assert versions[0]["version_number"] == 1

    survey_id = create_survey(client, title="旧问卷")
    response = add_from_bank(client, survey_id, versions[0]["_id"])
    assert "已从题库添加" in response.get_data(as_text=True)
    snapshot = survey_questions(app, survey_id)[0]
    assert snapshot["library_question_id"] == ObjectId(bank_id)
    assert snapshot["library_version_number"] == 1
    assert snapshot["title"] == "你的年龄段是？"

    publish(client, survey_id)
    submit_answers(client, survey_doc(app, survey_id)["public_id"], ["A"])

    response = client.post(
        f"/question-bank/{bank_id}/versions",
        data={
            "title": "你的年龄是？",
            "type": "number",
            "minimum": "0",
            "maximum": "120",
            "integer_only": "on",
            "required": "on",
        },
        follow_redirects=True,
    )
    assert "已创建新版本 v2" in response.get_data(as_text=True)

    bank, versions = bank_docs(app, bank_id)
    assert bank["current_version"] == 2
    assert [item["version_number"] for item in versions] == [1, 2]
    assert versions[1]["parent_version_id"] == versions[0]["_id"]

    old_snapshot = survey_questions(app, survey_id)[0]
    assert old_snapshot["_id"] == snapshot["_id"]
    assert old_snapshot["title"] == "你的年龄段是？"
    assert old_snapshot["type"] == "single"
    with app.app_context():
        stored = get_db().responses.find_one({"survey_id": ObjectId(survey_id)})
        assert stored["status"] == "submitted"
        assert stored["answers"][0]["value"] == "A"

    new_survey_id = create_survey(client, title="新问卷")
    add_from_bank(client, new_survey_id, bank["current_version_id"])
    new_snapshot = survey_questions(app, new_survey_id)[0]
    assert new_snapshot["title"] == "你的年龄是？"
    assert new_snapshot["type"] == "number"
    assert new_snapshot["library_version_number"] == 2


def test_inline_question_can_be_saved_to_bank(client):
    register_and_login(client)
    assert client.get("/question-bank").status_code == 200
    assert client.get("/question-bank/new").status_code == 200
    survey_id = create_survey(client)
    response = add_question(
        client,
        survey_id,
        title="你常用的编程语言",
        type="single",
        options="Python\nJava\nGo",
        save_to_bank="on",
    )
    assert "同时保存到题库" in response.get_data(as_text=True)
    question = survey_questions(client.application, survey_id)[0]
    assert question["library_question_id"] is not None
    assert question["library_version_number"] == 1
    detail = client.get(f"/surveys/{survey_id}").get_data(as_text=True)
    assert "从题库选题" in detail
    assert "题库 v1" in detail


def test_shared_user_can_use_and_fork_but_cannot_manage_bank(app, client):
    register_and_login(client)
    bank_id = create_bank_question(client, title="共享题", options="甲\n乙")
    bank, versions = bank_docs(app, bank_id)
    alice_survey = create_survey(client, title="Alice 的私有问卷")
    add_from_bank(client, alice_survey, versions[0]["_id"])
    publish(client, alice_survey)

    bob = app.test_client()
    register_and_login(bob, username="bob", password="secret456")
    assert bob.get(f"/question-bank/{bank_id}").status_code == 404
    assert bob.post(
        f"/question-bank/{bank_id}/versions",
        data={"title": "越权改题", "type": "single", "options": "甲\n乙"},
    ).status_code == 404

    response = client.post(
        f"/question-bank/{bank_id}/share",
        data={"usernames": "bob"},
        follow_redirects=True,
    )
    assert "题目已共享" in response.get_data(as_text=True)

    assert bob.get(f"/question-bank/{bank_id}").status_code == 200
    bob_survey = create_survey(bob, title="Bob 的问卷")
    response = add_from_bank(bob, bob_survey, versions[0]["_id"])
    assert "已从题库添加" in response.get_data(as_text=True)
    assert survey_questions(app, bob_survey)[0]["title"] == "共享题"

    blocked = bob.post(
        f"/question-bank/{bank_id}/versions",
        data={"title": "越权改题", "type": "single", "options": "甲\n乙"},
    )
    assert blocked.status_code == 403
    blocked_share = bob.post(
        f"/question-bank/{bank_id}/share", data={"usernames": "alice"}
    )
    assert blocked_share.status_code == 403

    alice_page = client.get(f"/question-bank/{bank_id}").get_data(as_text=True)
    assert "Bob 的问卷" in alice_page
    bob_page = bob.get(f"/question-bank/{bank_id}").get_data(as_text=True)
    assert "Bob 的问卷" in bob_page
    assert "Alice 的私有问卷" not in bob_page

    bob_stats = bob.get(f"/api/question-bank/{bank_id}/stats").get_json()
    alice_stats = client.get(f"/api/question-bank/{bank_id}/stats").get_json()
    assert bob_stats["survey_count"] == 1
    assert alice_stats["survey_count"] == 2

    fork_response = bob.post(f"/question-bank/{bank_id}/fork", follow_redirects=False)
    fork_id = _bank_id(fork_response)
    fork_bank, fork_versions = bank_docs(app, fork_id)
    assert fork_bank["owner_id"] != bank["owner_id"]
    assert fork_bank["title"] == bank["title"]
    assert len(fork_versions) == 1

    bob.post(
        f"/question-bank/{fork_id}/versions",
        data={"title": "Bob 独立改版", "type": "single", "options": "丙\n丁"},
    )
    original, _ = bank_docs(app, bank_id)
    assert original["title"] == "共享题"
    assert original["current_version"] == 1


def test_version_history_and_restore_create_new_version(app, client):
    register_and_login(client)
    bank_id = create_bank_question(client, title="v1 题目", options="A1\nB1")
    for index, (title, options) in enumerate(
        [("v2 题目", "A2\nB2"), ("v3 题目", "A3\nB3")], start=2
    ):
        response = client.post(
            f"/question-bank/{bank_id}/versions",
            data={"title": title, "type": "single", "options": options},
            follow_redirects=True,
        )
        assert f"已创建新版本 v{index}" in response.get_data(as_text=True)

    bank, versions = bank_docs(app, bank_id)
    assert [item["version_number"] for item in versions] == [1, 2, 3]
    assert versions[1]["parent_version_id"] == versions[0]["_id"]
    assert versions[2]["parent_version_id"] == versions[1]["_id"]

    detail = client.get(f"/question-bank/{bank_id}").get_data(as_text=True)
    assert "v1" in detail and "v2" in detail and "v3" in detail
    assert "父版本：v1" in detail
    assert "父版本：v2" in detail

    response = client.post(
        f"/question-bank/{bank_id}/restore/{versions[0]['_id']}",
        follow_redirects=True,
    )
    assert "恢复为 v4" in response.get_data(as_text=True)

    bank, versions = bank_docs(app, bank_id)
    assert bank["current_version"] == 4
    assert len(versions) == 4
    assert versions[3]["title"] == "v1 题目"
    assert versions[3]["restored_from_version_id"] == versions[0]["_id"]
    assert versions[3]["parent_version_id"] == versions[2]["_id"]


def test_same_question_different_versions_used_by_different_surveys(app, client):
    register_and_login(client)
    bank_id = create_bank_question(client, title="满意度", options="满意\n不满意")
    bank, versions = bank_docs(app, bank_id)
    v1 = versions[0]

    client.post(
        f"/question-bank/{bank_id}/versions",
        data={"title": "满意度（新）", "type": "single", "options": "非常满意\n不满意"},
    )
    bank, versions = bank_docs(app, bank_id)
    v2 = versions[1]

    first = create_survey(client, title="使用旧版")
    second = create_survey(client, title="使用新版")
    add_from_bank(client, first, v1["_id"])
    add_from_bank(client, second, v2["_id"])

    assert survey_questions(app, first)[0]["title"] == "满意度"
    assert survey_questions(app, first)[0]["options"][0]["text"] == "满意"
    assert survey_questions(app, second)[0]["title"] == "满意度（新）"
    assert survey_questions(app, second)[0]["options"][0]["text"] == "非常满意"

    for survey_id in (first, second):
        publish(client, survey_id)
        submit_answers(client, survey_doc(app, survey_id)["public_id"], ["A"])

    payload = client.get(f"/api/question-bank/{bank_id}/stats").get_json()
    option_rows = {(row["key"], row["text"]): row["count"] for row in payload["option_totals"]}
    assert option_rows[("A", "满意")] == 1
    assert option_rows[("A", "非常满意")] == 1
    assert payload["survey_count"] == 2
    assert payload["total_answered"] == 2


def test_cross_survey_number_statistics(app, client):
    register_and_login(client)
    bank_id = create_bank_question(
        client,
        title="你的年龄",
        type="number",
        options="",
        minimum="0",
        maximum="120",
        integer_only="on",
        required="on",
    )
    bank, versions = bank_docs(app, bank_id)

    for index, value in enumerate(["20", "30"], start=1):
        survey_id = create_survey(client, title=f"年龄调研 {index}")
        add_from_bank(client, survey_id, versions[0]["_id"])
        publish(client, survey_id)
        submit_answers(client, survey_doc(app, survey_id)["public_id"], [value])

    payload = client.get(f"/api/question-bank/{bank_id}/stats").get_json()
    assert payload["survey_count"] == 2
    assert payload["response_count"] == 2
    assert payload["number_summary"]["count"] == 2
    assert payload["number_summary"]["average"] == 25
    assert payload["number_summary"]["minimum"] == 20
    assert payload["number_summary"]["maximum"] == 30

    page = client.get(f"/question-bank/{bank_id}/stats")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "跨问卷" in body and "25" in body


def test_published_survey_rejects_bank_question_changes(app, client):
    register_and_login(client)
    bank_id = create_bank_question(client)
    bank, versions = bank_docs(app, bank_id)
    survey_id = create_survey(client)
    add_from_bank(client, survey_id, versions[0]["_id"])
    publish(client, survey_id)

    response = add_from_bank(client, survey_id, versions[0]["_id"])
    assert "发布后不能修改题目" in response.get_data(as_text=True)
    assert len(survey_questions(app, survey_id)) == 1

    question = survey_questions(app, survey_id)[0]
    deleted = client.post(
        f"/surveys/{survey_id}/questions/{question['_id']}/delete",
        follow_redirects=True,
    )
    assert "发布后不能删除题目" in deleted.get_data(as_text=True)
    assert len(survey_questions(app, survey_id)) == 1
