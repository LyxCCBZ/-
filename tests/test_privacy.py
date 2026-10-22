import hashlib
import json

from bson import ObjectId

from db import get_db
from tests.conftest import add_question, create_survey, publish, register_and_login


def start_and_complete(client, public_id, anonymous=False):
    data = {"anonymous": "on"} if anonymous else {}
    started = client.post(f"/survey/{public_id}/start", data=data, follow_redirects=False)
    assert started.status_code == 302
    attempt_path = started.headers["Location"]
    attempt_id = attempt_path.rstrip("/").split("/")[-1]
    completed = client.post(
        f"/attempt/{attempt_id}/answer",
        data={"answer": "A"},
        follow_redirects=False,
    )
    assert completed.status_code == 302
    return attempt_path, attempt_id


def test_anonymous_response_hides_identity_and_token(app, client):
    register_and_login(client, username="anonymous_owner", password="secret123")
    survey_id = create_survey(client, title="匿名隐私测试", anonymous=True, multiple=True)
    add_question(client, survey_id, title="题目", type="single", options="A\nB", required="on")
    publish(client, survey_id)

    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})
    attempt_path, attempt_id = start_and_complete(client, survey["public_id"], anonymous=True)

    with app.app_context():
        response = get_db().responses.find_one({"_id": ObjectId(attempt_id)})
        assert response["is_anonymous"] is True
        assert response["respondent_id"] is None
        assert response["respondent_key"]

    with client.session_transaction() as session:
        raw_token = session["attempt_tokens"][attempt_id]
    with app.app_context():
        response = get_db().responses.find_one({"_id": ObjectId(attempt_id)})
        assert response["access_token_hash"] == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        assert raw_token not in json.dumps(response, default=str)

    page = client.get(attempt_path).get_data(as_text=True)
    assert "respondent_key" not in page
    assert "access_token_hash" not in page

    other = app.test_client()
    register_and_login(other, username="anonymous_other", password="secret456")
    assert other.get(attempt_path).status_code == 404


def test_stats_and_management_do_not_expose_respondent_identity(app, client):
    register_and_login(client, username="stats_owner", password="secret123")
    survey_id = create_survey(client, title="统计隐私测试", anonymous=True, multiple=True)
    add_question(client, survey_id, title="题目", type="single", options="A\nB", required="on")
    publish(client, survey_id)
    with app.app_context():
        survey = get_db().surveys.find_one({"_id": ObjectId(survey_id)})

    respondent = app.test_client()
    register_and_login(respondent, username="stats_respondent", password="secret456")
    start_and_complete(respondent, survey["public_id"], anonymous=False)

    with app.app_context():
        respondent_id = str(get_db().users.find_one({"username": "stats_respondent"})["_id"])

    stats = client.get(f"/api/surveys/{survey_id}/stats")
    payload = stats.get_json()
    payload_text = json.dumps(payload, ensure_ascii=False, default=str)
    assert stats.status_code == 200
    assert payload["response_count"] == 1
    assert "respondent_id" not in payload_text
    assert "respondent_key" not in payload_text
    assert "access_token_hash" not in payload_text
    assert respondent_id not in payload_text
    assert "stats_respondent" not in payload_text

    assert respondent.get(f"/surveys/{survey_id}").status_code == 404
    assert respondent.get(f"/surveys/{survey_id}/stats").status_code == 404
    assert respondent.get(f"/api/surveys/{survey_id}/stats").status_code == 404
