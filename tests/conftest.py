import os
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from db import ensure_database, get_db  # noqa: E402


@pytest.fixture()
def app():
    db_name = f"questionnaire_test_{uuid.uuid4().hex[:10]}"
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "MONGODB_URI": os.getenv("TEST_MONGODB_URI", "mongodb://127.0.0.1:27018"),
            "MONGODB_DB": db_name,
            "AUTO_ENSURE_DB": False,
        }
    )
    with application.app_context():
        ensure_database(get_db())
    yield application
    application.extensions["mongo_client"].drop_database(db_name)
    application.extensions["mongo_client"].close()


@pytest.fixture()
def client(app):
    return app.test_client()


def register_and_login(client, username="alice", password="secret123"):
    response = client.post(
        "/register",
        data={"username": username, "password": password, "confirm_password": password},
        follow_redirects=True,
    )
    assert response.status_code == 200
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "我的问卷" in response.get_data(as_text=True)
    return username


def create_survey(client, title="测试问卷", anonymous=True, multiple=True):
    data = {"title": title, "description": "用于自动化测试的问卷"}
    if anonymous:
        data["allow_anonymous"] = "on"
    if multiple:
        data["allow_multiple"] = "on"
    response = client.post("/surveys/new", data=data, follow_redirects=False)
    assert response.status_code == 302
    return response.headers["Location"].rstrip("/").split("/")[-1]


def add_question(client, survey_id, **overrides):
    data = {"title": "测试题目", "type": "single", "options": "选项A\n选项B"}
    data.update(overrides)
    response = client.post(
        f"/surveys/{survey_id}/questions", data=data, follow_redirects=True
    )
    assert response.status_code == 200
    return response


def publish(client, survey_id):
    response = client.post(f"/surveys/{survey_id}/publish", follow_redirects=True)
    assert response.status_code == 200
    return response

