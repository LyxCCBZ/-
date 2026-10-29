import logging

from flask import Flask, g, redirect, render_template, url_for
from pymongo.errors import PyMongoError

from config import Config
from db import ensure_database, get_db, init_database, ping_database
from security import load_current_user
from utils import format_local_datetime


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    init_database(app)

    from routes.auth import bp as auth_bp
    from routes.public import bp as public_bp
    from routes.question_bank import bp as question_bank_bp
    from routes.surveys import bp as surveys_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(surveys_bp)
    app.register_blueprint(question_bank_bp)
    app.register_blueprint(public_bp)

    @app.before_request
    def load_user():
        g.current_user = load_current_user(get_db())

    @app.context_processor
    def inject_globals():
        return {
            "current_user": g.get("current_user"),
            "format_local_datetime": format_local_datetime,
        }

    @app.get("/")
    def index():
        if g.get("current_user"):
            return redirect(url_for("surveys.dashboard"))
        return redirect(url_for("auth.login"))

    @app.get("/health")
    def health():
        try:
            ping_database(get_db())
        except PyMongoError:
            return {"status": "error", "database": "unavailable"}, 503
        return {"status": "ok", "database": "connected"}

    @app.errorhandler(400)
    def bad_request(_error):
        return render_template("error.html", code=400, message="请求参数不正确。"), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="页面或资源不存在。"), 404

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", code=403, message="你没有权限执行该操作。"), 403

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("error.html", code=500, message="服务器发生错误，请稍后再试。"), 500

    app.config.setdefault("AUTO_ENSURE_DB", True)
    if app.config["AUTO_ENSURE_DB"]:
        try:
            ensure_database(app.extensions["mongo_db"])
        except PyMongoError:
            logging.getLogger(__name__).warning("MongoDB unavailable; start mongod before using the app.")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=True)

