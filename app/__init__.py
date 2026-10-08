from flask import Flask, render_template, request
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import Config

login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config is not None:
        app.config.update(test_config)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "info"

    csrf.init_app(app)
    limiter.init_app(app)

    @app.after_request
    def security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline';"
        )
        return response

    from app.auth import auth_bp
    from app.main import main_bp
    from app.users import users_bp
    from app.swp import swp_bp
    from app.loto import loto_bp
    from app.contractors import contractors_bp
    from app.equipment import equipment_bp
    from app.combined import combined_bp
    from app.signatures import signatures_bp
    from app.swp_continuations import swp_cont_bp
    from app.pdf import pdf_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(swp_bp)
    app.register_blueprint(loto_bp)
    app.register_blueprint(contractors_bp)
    app.register_blueprint(equipment_bp)
    app.register_blueprint(combined_bp)
    app.register_blueprint(signatures_bp)
    app.register_blueprint(swp_cont_bp)
    app.register_blueprint(pdf_bp)

    @app.context_processor
    def inject_pending_sig_count():
        if current_user.is_authenticated:
            try:
                from app.db import get_db
                count = get_db().execute(
                    "SELECT COUNT(*) FROM signature_requests"
                    " WHERE requested_from=? AND status='pending'",
                    (current_user.id,),
                ).fetchone()[0]
                return {"pending_sig_count": count}
            except Exception:
                pass
        return {"pending_sig_count": 0}

    @app.errorhandler(403)
    def forbidden(e):
        try:
            from app.audit import ACCESS_DENIED, log
            uid      = current_user.id       if current_user.is_authenticated else None
            uname    = current_user.username if current_user.is_authenticated else None
            log(ACCESS_DENIED, user_id=uid, username=uname,
                resource=request.endpoint, success=False,
                detail={"path": request.path, "method": request.method})
        except Exception:
            pass
        return render_template("403.html"), 403

    @app.errorhandler(500)
    def internal_error(e):
        import traceback, logging
        logging.getLogger(__name__).error("500 error: %s\n%s", e, traceback.format_exc())
        return render_template("500.html"), 500

    from app.db import init_db, close_db
    with app.app_context():
        init_db(app)
    app.teardown_appcontext(close_db)

    from app.cli import register_cli
    register_cli(app)

    return app
