from bson import ObjectId

from db import get_db
from services.rules import ordered_questions
from tests.conftest import add_question, create_survey, publish, register_and_login


def survey_doc(app, survey_id):
    with app.app_context():
        return get_db().surveys.find_one({"_id": ObjectId(survey_id)})


def test_survey_creation_question_management_and_owner_isolation(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="账号隔离测试")
    survey = survey_doc(app, survey_id)
    assert survey["status"] == "draft"
    assert survey["owner_id"] is not None

    response = add_question(client, survey_id, title="只有一个选项", options="唯一选项")
    assert "至少需要两个选项" in response.get_data(as_text=True)

    response = add_question(
        client,
        survey_id,
        title="你的专业",
        type="single",
        options="计算机\n软件工程\n其他",
        required="on",
    )
    assert "题目已添加" in response.get_data(as_text=True)
    add_question(
        client,
        survey_id,
        title="年龄",
        type="number",
        minimum="0",
        maximum="120",
        integer_only="on",
    )

    with app.app_context():
        questions = ordered_questions(get_db(), ObjectId(survey_id))
    assert [item["order"] for item in questions] == [1, 2]
    assert questions[0]["type"] == "single"
    assert questions[1]["validation"]["integer_only"] is True

    other = app.test_client()
    register_and_login(other, username="bob", password="secret456")
    forbidden = other.get(f"/surveys/{survey_id}")
    assert forbidden.status_code == 404


def test_jump_fill_validation_and_statistics(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="跳转与统计测试", anonymous=True, multiple=True)
    add_question(client, survey_id, title="是否继续？", type="single", options="继续\n结束", required="on")
    add_question(client, survey_id, title="这道题应被跳过", type="text", min_length="1")
    add_question(client, survey_id, title="请输入年龄", type="number", minimum="0", maximum="120", integer_only="on", required="on")

    with app.app_context():
        db = get_db()
        survey = db.surveys.find_one({"_id": ObjectId(survey_id)})
        questions = ordered_questions(db, survey["_id"])
    q1, q2, q3 = questions

    response = client.post(
        f"/surveys/{survey_id}/questions/{q1['_id']}/jumps",
        data={"operator": "eq", "value": "A", "target": str(q3["_id"])},
        follow_redirects=True,
    )
    assert "跳转规则已添加" in response.get_data(as_text=True)
    response = client.post(
        f"/surveys/{survey_id}/questions/{q1['_id']}/jumps",
        data={"operator": "eq", "value": "B", "target": "END"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    publish(client, survey_id)

    start = client.post(
        f"/survey/{survey['public_id']}/start", data={"anonymous": "on"}, follow_redirects=False
    )
    assert start.status_code == 302
    attempt_path = start.headers["Location"]
    attempt_id = attempt_path.rstrip("/").split("/")[-1]

    page = client.get(attempt_path)
    assert "是否继续" in page.get_data(as_text=True)
    answer = client.post(
        f"/attempt/{attempt_id}/answer", data={"answer": "A"}, follow_redirects=False
    )
    assert answer.status_code == 302
    page = client.get(answer.headers["Location"])
    assert "请输入年龄" in page.get_data(as_text=True)
    assert "应被跳过" not in page.get_data(as_text=True)

    invalid = client.post(f"/attempt/{attempt_id}/answer", data={"answer": "121"})
    assert invalid.status_code == 422
    assert "不能大于 120" in invalid.get_data(as_text=True)
    valid = client.post(
        f"/attempt/{attempt_id}/answer", data={"answer": "25"}, follow_redirects=False
    )
    assert valid.status_code == 302
    assert "complete" in valid.headers["Location"]

    with app.app_context():
        db = get_db()
        stored = db.responses.find_one({"_id": ObjectId(attempt_id)})
        assert stored["status"] == "submitted"
        assert stored["is_anonymous"] is True
        assert stored.get("respondent_id") is None
        assert [str(item["question_id"]) for item in stored["answers"]] == [
            str(q1["_id"]),
            str(q3["_id"]),
        ]

    stats_page = client.get(f"/surveys/{survey_id}/stats")
    body = stats_page.get_data(as_text=True)
    assert stats_page.status_code == 200
    assert "1" in body and "25.0" in body
    assert "应被跳过" in body

    second = client.post(
        f"/survey/{survey['public_id']}/start", data={}, follow_redirects=False
    )
    assert second.status_code == 302
    second_id = second.headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/attempt/{second_id}/answer", data={"answer": "B"}, follow_redirects=False)
    with app.app_context():
        second_doc = get_db().responses.find_one({"_id": ObjectId(second_id)})
        assert second_doc["submitted_at"] is not None
        assert len(second_doc["answers"]) == 1


def test_multiple_choice_limits_and_required_text(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="多选校验", anonymous=False, multiple=True)
    add_question(
        client,
        survey_id,
        title="选择两项",
        type="multiple",
        options="甲\n乙\n丙\n丁",
        min_select="2",
        max_select="3",
        required="on",
    )
    add_question(client, survey_id, title="请写四个字", type="text", min_length="3", max_length="5", required="on")
    publish(client, survey_id)
    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})

    start = client.post(f"/survey/{survey['public_id']}/start", follow_redirects=False)
    attempt_id = start.headers["Location"].rstrip("/").split("/")[-1]
    too_few = client.post(f"/attempt/{attempt_id}/answer", data={"answer": ["A"]})
    assert too_few.status_code == 422
    assert "最少" in too_few.get_data(as_text=True)
    accepted = client.post(
        f"/attempt/{attempt_id}/answer", data={"answer": ["A", "C"]}, follow_redirects=False
    )
    assert accepted.status_code == 302
    short_text = client.post(f"/attempt/{attempt_id}/answer", data={"answer": "你好"})
    assert short_text.status_code == 422
    assert "至少" in short_text.get_data(as_text=True)
    done = client.post(
        f"/attempt/{attempt_id}/answer", data={"answer": "你好呀"}, follow_redirects=False
    )
    assert "complete" in done.headers["Location"]


def test_publish_close_and_duplicate_submission_rules(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="重复提交限制", anonymous=True, multiple=False)
    empty_publish = publish(client, survey_id)
    assert "至少添加一道题目" in empty_publish.get_data(as_text=True)
    add_question(client, survey_id, title="唯一题目", type="single", options="好\n不好", required="on")
    publish(client, survey_id)

    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})
    first = client.post(f"/survey/{survey['public_id']}/start", data={"anonymous": "on"}, follow_redirects=False)
    attempt_id = first.headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/attempt/{attempt_id}/answer", data={"answer": "A"}, follow_redirects=False)

    duplicate = client.post(
        f"/survey/{survey['public_id']}/start", data={"anonymous": "on"}, follow_redirects=True
    )
    assert "不允许重复提交" in duplicate.get_data(as_text=True)

    closed = client.post(f"/surveys/{survey_id}/close", follow_redirects=True)
    assert "问卷已关闭" in closed.get_data(as_text=True)
    with app.app_context():
        assert get_db().surveys.find_one({"_id": ObjectId(survey_id)})["status"] == "closed"
    blocked = client.post(f"/survey/{survey['public_id']}/start", data={}, follow_redirects=True)
    assert "未开放填写" in blocked.get_data(as_text=True)


def test_published_question_structure_is_locked(app, client):
    register_and_login(client)
    survey_id = create_survey(client, title="发布后锁定")
    add_question(client, survey_id, title="第一题", type="single", options="A\nB")
    publish(client, survey_id)
    before = None
    with app.app_context():
        before = get_db().questions.count_documents({"survey_id": ObjectId(survey_id)})
    response = add_question(client, survey_id, title="发布后添加", type="text")
    assert "发布后不能修改题目" in response.get_data(as_text=True)
    with app.app_context():
        after = get_db().questions.count_documents({"survey_id": ObjectId(survey_id)})
    assert after == before

