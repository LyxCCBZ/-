from pymongo import ASCENDING, DESCENDING, MongoClient


def init_database(app):
    client = MongoClient(app.config["MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=3000)
    app.extensions["mongo_client"] = client
    app.extensions["mongo_db"] = client[app.config["MONGODB_DB"]]


def get_db():
    from flask import current_app

    return current_app.extensions["mongo_db"]


def ensure_database(db):
    validators = {
        "users": {
            "bsonType": "object",
            "required": ["username", "password_hash", "created_at"],
            "properties": {
                "username": {"bsonType": "string", "minLength": 3, "maxLength": 30},
                "password_hash": {"bsonType": "string", "minLength": 20},
                "created_at": {"bsonType": "date"},
            },
        },
        "surveys": {
            "bsonType": "object",
            "required": [
                "public_id",
                "owner_id",
                "title",
                "description",
                "allow_anonymous",
                "allow_multiple",
                "status",
                "created_at",
                "updated_at",
            ],
            "properties": {
                "public_id": {"bsonType": "string", "minLength": 8},
                "owner_id": {"bsonType": "objectId"},
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
                "description": {"bsonType": "string", "maxLength": 2000},
                "allow_anonymous": {"bsonType": "bool"},
                "allow_multiple": {"bsonType": "bool"},
                "status": {"enum": ["draft", "published", "closed"]},
                "deadline": {"bsonType": ["date", "null"]},
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
                "published_at": {"bsonType": ["date", "null"]},
                "closed_at": {"bsonType": ["date", "null"]},
            },
        },
        "questions": {
            "bsonType": "object",
            "required": ["survey_id", "order", "type", "title", "required", "options", "validation", "jumps"],
            "properties": {
                "survey_id": {"bsonType": "objectId"},
                "library_question_id": {"bsonType": ["objectId", "null"]},
                "library_version_id": {"bsonType": ["objectId", "null"]},
                "library_version_number": {"bsonType": ["int", "null"]},
                "library_owner_id": {"bsonType": ["objectId", "null"]},
                "order": {"bsonType": "int", "minimum": 1},
                "type": {"enum": ["single", "multiple", "text", "number"]},
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 300},
                "required": {"bsonType": "bool"},
                "options": {"bsonType": "array"},
                "validation": {"bsonType": "object"},
                "jumps": {"bsonType": "array"},
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
            },
        },
        "responses": {
            "bsonType": "object",
            "required": [
                "survey_id",
                "respondent_key",
                "is_anonymous",
                "answers",
                "status",
                "started_at",
            ],
            "properties": {
                "survey_id": {"bsonType": "objectId"},
                "respondent_id": {"bsonType": ["objectId", "null"]},
                "respondent_key": {"bsonType": "string", "minLength": 16},
                "access_token_hash": {"bsonType": "string", "minLength": 32},
                "single_response": {"bsonType": "bool"},
                "is_anonymous": {"bsonType": "bool"},
                "answers": {"bsonType": "array"},
                "status": {"enum": ["in_progress", "submitted"]},
                "current_question_id": {"bsonType": ["objectId", "null"]},
                "started_at": {"bsonType": "date"},
                "submitted_at": {"bsonType": ["date", "null"]},
            },
        },
        "question_bank": {
            "bsonType": "object",
            "required": [
                "owner_id",
                "title",
                "type",
                "current_version_id",
                "current_version",
                "shared_with",
                "created_at",
                "updated_at",
            ],
            "properties": {
                "owner_id": {"bsonType": "objectId"},
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 300},
                "type": {"enum": ["single", "multiple", "text", "number"]},
                "current_version_id": {"bsonType": "objectId"},
                "current_version": {"bsonType": "int", "minimum": 1},
                "shared_with": {"bsonType": "array", "items": {"bsonType": "objectId"}},
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
            },
        },
        "question_versions": {
            "bsonType": "object",
            "required": [
                "bank_id",
                "version_number",
                "title",
                "type",
                "required",
                "options",
                "validation",
                "created_by",
                "created_at",
            ],
            "properties": {
                "bank_id": {"bsonType": "objectId"},
                "version_number": {"bsonType": "int", "minimum": 1},
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 300},
                "type": {"enum": ["single", "multiple", "text", "number"]},
                "required": {"bsonType": "bool"},
                "options": {"bsonType": "array"},
                "validation": {"bsonType": "object"},
                "parent_version_id": {"bsonType": ["objectId", "null"]},
                "restored_from_version_id": {"bsonType": ["objectId", "null"]},
                "created_by": {"bsonType": "objectId"},
                "created_at": {"bsonType": "date"},
            },
        },
    }

    existing = set(db.list_collection_names())
    for name, schema in validators.items():
        validator = {"$jsonSchema": schema}
        if name not in existing:
            db.create_collection(name, validator=validator, validationLevel="strict", validationAction="error")
        else:
            db.command(
                "collMod",
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )

    db.users.create_index([("username", ASCENDING)], unique=True, name="uq_username")
    db.surveys.create_index([("public_id", ASCENDING)], unique=True, name="uq_public_id")
    db.surveys.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)], name="idx_owner_created")
    db.questions.create_index([("survey_id", ASCENDING), ("order", ASCENDING)], unique=True, name="uq_survey_order")
    db.questions.create_index([("library_question_id", ASCENDING)], sparse=True, name="idx_library_question")
    db.questions.create_index([("library_version_id", ASCENDING)], sparse=True, name="idx_library_version")
    db.question_bank.create_index([("owner_id", ASCENDING), ("updated_at", DESCENDING)], name="idx_bank_owner_updated")
    db.question_bank.create_index([("shared_with", ASCENDING), ("updated_at", DESCENDING)], name="idx_bank_shared")
    db.question_versions.create_index([("bank_id", ASCENDING), ("version_number", ASCENDING)], unique=True, name="uq_bank_version")
    db.question_versions.create_index([("bank_id", ASCENDING), ("created_at", DESCENDING)], name="idx_bank_version_created")
    db.responses.create_index([("survey_id", ASCENDING), ("status", ASCENDING)], name="idx_survey_status")
    if "idx_survey_respondent" in db.responses.index_information():
        db.responses.drop_index("idx_survey_respondent")
    db.responses.create_index(
        [("survey_id", ASCENDING), ("respondent_key", ASCENDING)],
        unique=True,
        partialFilterExpression={"single_response": True},
        name="uq_survey_single_response",
    )


def ping_database(db):
    db.client.admin.command("ping")

