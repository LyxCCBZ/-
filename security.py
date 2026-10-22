import functools
from datetime import datetime, timezone

from flask import flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def login_user(user):
    session.clear()
    session["user_id"] = str(user["_id"])
    session.permanent = True
    g.current_user = user


def logout_user():
    session.clear()
    g.pop("current_user", None)


def load_current_user(db):
    from bson import ObjectId

    user_id = session.get("user_id")
    if not user_id:
        return None
    try:
        return db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("current_user") is None:
            flash("请先登录后再继续。", "warning")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def utcnow():
    return datetime.now(timezone.utc)

