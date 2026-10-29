import hashlib
from datetime import timedelta
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from db import ensure_database, get_db  # noqa: E402
from security import hash_password, utcnow  # noqa: E402
from services import question_bank as bank_service  # noqa: E402


PUBLIC_ID = "demo-learning-survey"
CLUB_PUBLIC_ID = "demo-club-survey"
DEMO_BANK_KEY = "weekly-self-study-hours"


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        ensure_database(db)
        now = utcnow()

        user = db.users.find_one({"username": "demo"})
        if user is None:
            result = db.users.insert_one(
                {
                    "username": "demo",
                    "password_hash": hash_password("demo123"),
                    "created_at": now,
                }
            )
            user_id = result.inserted_id
        else:
            user_id = user["_id"]

        old = db.surveys.find_one({"public_id": PUBLIC_ID})
        if old:
            db.questions.delete_many({"survey_id": old["_id"]})
            db.responses.delete_many({"survey_id": old["_id"]})
            db.surveys.delete_one({"_id": old["_id"]})

        old_club = db.surveys.find_one({"public_id": CLUB_PUBLIC_ID})
        if old_club:
            db.questions.delete_many({"survey_id": old_club["_id"]})
            db.responses.delete_many({"survey_id": old_club["_id"]})
            db.surveys.delete_one({"_id": old_club["_id"]})

        old_banks = list(db.question_bank.find({"demo_key": DEMO_BANK_KEY}))
        for old_bank in old_banks:
            db.question_versions.delete_many({"bank_id": old_bank["_id"]})
            db.question_bank.delete_one({"_id": old_bank["_id"]})

        bank = bank_service.create_bank_from_content(
            db,
            user_id,
            {
                "title": "你每周自主学习多少小时？",
                "type": "number",
                "required": True,
                "options": [],
                "validation": {"minimum": 0, "maximum": 80, "integer_only": False},
            },
        )
        db.question_bank.update_one(
            {"_id": bank["_id"]}, {"$set": {"demo_key": DEMO_BANK_KEY}}
        )
        bank_version = db.question_versions.find_one({"_id": bank["current_version_id"]})

        survey = {
            "public_id": PUBLIC_ID,
            "owner_id": user_id,
            "title": "校园学习方式调查",
            "description": "这是一份用于演示统计与条件跳转的问卷。选择“独自学习”会跳过小组学习题。",
            "allow_anonymous": True,
            "allow_multiple": True,
            "status": "published",
            "deadline": now + timedelta(days=30),
            "created_at": now,
            "updated_at": now,
            "published_at": now,
            "closed_at": None,
        }
        survey_id = db.surveys.insert_one(survey).inserted_id

        questions = [
            {
                "survey_id": survey_id,
                "order": 1,
                "type": "single",
                "title": "你更常用的学习方式是？",
                "required": True,
                "options": [
                    {"key": "A", "text": "独自学习"},
                    {"key": "B", "text": "小组讨论"},
                ],
                "validation": {},
                "jumps": [],
                "created_at": now,
                "updated_at": now,
            },
            {
                "survey_id": survey_id,
                "order": 2,
                "type": "number",
                "title": "你每周自主学习多少小时？",
                "required": True,
                "options": [],
                "validation": {"minimum": 0, "maximum": 80, "integer_only": False},
                "jumps": [],
                "created_at": now,
                "updated_at": now,
                **bank_service.link_fields(bank, bank_version),
            },
            {
                "survey_id": survey_id,
                "order": 3,
                "type": "multiple",
                "title": "你认为学习中最需要改善的方面是？",
                "required": False,
                "options": [
                    {"key": "A", "text": "时间管理"},
                    {"key": "B", "text": "资料整理"},
                    {"key": "C", "text": "专注度"},
                ],
                "validation": {"min_select": 0, "max_select": 3},
                "jumps": [],
                "created_at": now,
                "updated_at": now,
            },
        ]
        question_ids = db.questions.insert_many(questions).inserted_ids
        db.questions.update_one(
            {"_id": question_ids[0]},
            {
                "$push": {
                    "jumps": {
                        "condition": {"operator": "eq", "value": "A"},
                        "target_question_id": question_ids[2],
                    }
                }
            },
        )

        responses = [
            {
                "survey_id": survey_id,
                "respondent_id": None,
                "respondent_key": hashlib.sha256(b"demo-response-1").hexdigest(),
                "access_token_hash": hashlib.sha256(b"demo-token-1").hexdigest(),
                "is_anonymous": True,
                "answers": [
                    {"question_id": question_ids[0], "value": "A", "answered_at": now},
                    {"question_id": question_ids[2], "value": ["A", "C"], "answered_at": now},
                ],
                "status": "submitted",
                "current_question_id": None,
                "started_at": now,
                "submitted_at": now,
            },
            {
                "survey_id": survey_id,
                "respondent_id": None,
                "respondent_key": hashlib.sha256(b"demo-response-2").hexdigest(),
                "access_token_hash": hashlib.sha256(b"demo-token-2").hexdigest(),
                "is_anonymous": True,
                "answers": [
                    {"question_id": question_ids[0], "value": "B", "answered_at": now},
                    {"question_id": question_ids[1], "value": 12, "answered_at": now},
                    {"question_id": question_ids[2], "value": ["B"], "answered_at": now},
                ],
                "status": "submitted",
                "current_question_id": None,
                "started_at": now,
                "submitted_at": now,
            },
        ]
        db.responses.insert_many(responses)

        club_survey = {
            "public_id": CLUB_PUBLIC_ID,
            "owner_id": user_id,
            "title": "社团活动与学习时间调查",
            "description": "用于演示同一道题库题目被多份问卷复用后的跨问卷统计。",
            "allow_anonymous": True,
            "allow_multiple": True,
            "status": "published",
            "deadline": now + timedelta(days=30),
            "created_at": now,
            "updated_at": now,
            "published_at": now,
            "closed_at": None,
        }
        club_survey_id = db.surveys.insert_one(club_survey).inserted_id
        club_questions = [
            bank_service.survey_question_document(
                db, bank, bank_version, club_survey_id, 1, now=now
            ),
            {
                "survey_id": club_survey_id,
                "order": 2,
                "type": "single",
                "title": "你参加过社团活动吗？",
                "required": False,
                "options": [
                    {"key": "A", "text": "经常参加"},
                    {"key": "B", "text": "偶尔参加"},
                    {"key": "C", "text": "没有参加"},
                ],
                "validation": {},
                "jumps": [],
                "created_at": now,
                "updated_at": now,
            },
        ]
        club_question_ids = db.questions.insert_many(club_questions).inserted_ids
        club_responses = [
            {
                "survey_id": club_survey_id,
                "respondent_id": None,
                "respondent_key": hashlib.sha256(b"demo-club-response-1").hexdigest(),
                "access_token_hash": hashlib.sha256(b"demo-club-token-1").hexdigest(),
                "is_anonymous": True,
                "answers": [
                    {"question_id": club_question_ids[0], "value": 8, "answered_at": now},
                    {"question_id": club_question_ids[1], "value": "A", "answered_at": now},
                ],
                "status": "submitted",
                "current_question_id": None,
                "started_at": now,
                "submitted_at": now,
            },
            {
                "survey_id": club_survey_id,
                "respondent_id": None,
                "respondent_key": hashlib.sha256(b"demo-club-response-2").hexdigest(),
                "access_token_hash": hashlib.sha256(b"demo-club-token-2").hexdigest(),
                "is_anonymous": True,
                "answers": [
                    {"question_id": club_question_ids[0], "value": 15, "answered_at": now},
                    {"question_id": club_question_ids[1], "value": "B", "answered_at": now},
                ],
                "status": "submitted",
                "current_question_id": None,
                "started_at": now,
                "submitted_at": now,
            },
        ]
        db.responses.insert_many(club_responses)

        print("演示数据已创建")
        print("账号：demo / demo123")
        print("管理后台：http://127.0.0.1:5000/login")
        print(f"公开填写链接：http://127.0.0.1:5000/survey/{PUBLIC_ID}")
        print(f"第二份演示问卷：http://127.0.0.1:5000/survey/{CLUB_PUBLIC_ID}")
        print(f"题库跨问卷统计：http://127.0.0.1:5000/question-bank/{bank['_id']}/stats")


if __name__ == "__main__":
    main()

