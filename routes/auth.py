import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from pymongo.errors import DuplicateKeyError

from db import get_db
from security import hash_password, login_user, logout_user, utcnow, verify_password
from utils import is_safe_relative_url, normalize_username


bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.get("current_user"):
        return redirect(url_for("surveys.dashboard"))

    if request.method == "POST":
        username = normalize_username(request.form.get("username"))
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        error = None

        if not re.fullmatch(r"[a-z0-9_\u4e00-\u9fa5]{3,30}", username):
            error = "用户名需为 3-30 位，只能包含中文、字母、数字或下划线。"
        elif len(password) < 6:
            error = "密码至少需要 6 位。"
        elif password != confirm:
            error = "两次输入的密码不一致。"

        if error is None:
            user = {"username": username, "password_hash": hash_password(password), "created_at": utcnow()}
            try:
                get_db().users.insert_one(user)
            except DuplicateKeyError:
                error = "该用户名已被注册。"
            else:
                flash("注册成功，请登录。", "success")
                return redirect(url_for("auth.login"))

        flash(error, "error")

    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("current_user"):
        return redirect(url_for("surveys.dashboard"))

    next_url = request.args.get("next") or request.form.get("next")
    if request.method == "POST":
        username = normalize_username(request.form.get("username"))
        password = request.form.get("password") or ""
        user = get_db().users.find_one({"username": username})
        if user is None or not verify_password(user["password_hash"], password):
            flash("用户名或密码错误。", "error")
        else:
            login_user(user)
            flash("登录成功。", "success")
            if is_safe_relative_url(next_url):
                return redirect(next_url)
            return redirect(url_for("surveys.dashboard"))

    return render_template("login.html", next_url=next_url)


@bp.post("/logout")
def logout():
    logout_user()
    flash("已退出登录。", "success")
    return redirect(url_for("auth.login"))

