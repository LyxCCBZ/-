import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from bson import ObjectId


def parse_object_id(value):
    if isinstance(value, ObjectId):
        return value
    if not value or not ObjectId.is_valid(str(value)):
        return None
    return ObjectId(str(value))


def parse_local_datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def format_local_datetime(value, fallback="-"):
    if not value:
        return fallback
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def datetime_local_value(value):
    if not value:
        return ""
    return value.astimezone().strftime("%Y-%m-%dT%H:%M")


def is_safe_relative_url(target):
    if not target:
        return False
    if "\\" in target or any(ord(char) < 32 for char in target):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and target.startswith("/") and not target.startswith("//")


def normalize_username(value):
    return re.sub(r"\s+", "", value or "").strip().lower()


def json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


