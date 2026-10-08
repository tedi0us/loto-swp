import re
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField, TelField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, ValidationError

from app import limiter, login_manager
from app.audit import log, LOGIN, LOGIN_FAILED, LOGOUT, PASSWORD_CHANGED, PROFILE_COMPLETED
from app.db import get_db

auth_bp = Blueprint("auth", __name__)
ph = PasswordHasher()

# Endpoints that bypass the login-time gates
_GATE_EXEMPT = {"auth.change_password", "auth.complete_profile", "auth.logout", "static", "main.privacy"}


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    submit   = SubmitField("Sign In")


class ChangePasswordForm(FlaskForm):
    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=8, max=128, message="Password must be at least 8 characters.")],
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField("Set New Password")


def _e164_phone(form, field):
    val = (field.data or "").strip()
    if val and not re.match(r'^\+\d{7,15}$', val):
        raise ValidationError("Use E.164 format: +15551234567")


class CompleteProfileForm(FlaskForm):
    first_name = StringField("First Name", validators=[DataRequired(), Length(max=64)])
    last_name  = StringField("Last Name",  validators=[DataRequired(), Length(max=64)])
    email      = StringField("Email",       validators=[DataRequired(), Email(), Length(max=128)])
    phone      = TelField(  "Phone Number", validators=[DataRequired(), Length(max=32), _e164_phone])
    title      = StringField("Title",       validators=[DataRequired(), Length(max=128)])
    notify_sms = BooleanField("Receive SMS notifications")
    submit     = SubmitField("Save Profile")


class User:
    def __init__(self, id, username, role, must_change_password=False,
                 first_name=None, last_name=None, email=None, phone=None, title=None,
                 plant=None, notify_sms=0):
        self.id                   = id
        self.username             = username
        self.role                 = role
        self.must_change_password = bool(must_change_password)
        self.first_name           = first_name
        self.last_name            = last_name
        self.email                = email
        self.phone                = phone
        self.title                = title
        self.plant                = plant
        self.notify_sms           = bool(notify_sms)
        self.is_authenticated     = True
        self.is_active            = True
        self.is_anonymous         = False

    def get_id(self):
        return str(self.id)

    def profile_complete(self):
        return all([self.first_name, self.last_name, self.email, self.phone, self.title])

    def display_name(self):
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.username

    def is_it_admin(self):
        return self.role == "it_admin"

    def is_admin_or_above(self):
        return self.role in ("it_admin", "admin")


def _load_user_row(row):
    return User(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        must_change_password=row["must_change_password"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        email=row["email"],
        phone=row["phone"],
        title=row["title"],
        plant=row["plant"],
        notify_sms=row["notify_sms"],
    )


@login_manager.user_loader
def load_user(user_id):
    row = get_db().execute(
        """SELECT id, username, role, must_change_password,
                  first_name, last_name, email, phone, title, plant, notify_sms
           FROM users WHERE id = ?""",
        (user_id,),
    ).fetchone()
    return _load_user_row(row) if row else None


@auth_bp.before_app_request
def enforce_onboarding_gates():
    if not current_user.is_authenticated:
        return
    if request.endpoint in _GATE_EXEMPT:
        return
    # Password change takes priority
    if current_user.must_change_password:
        return redirect(url_for("auth.change_password"))
    # Then profile completion
    if not current_user.profile_complete():
        return redirect(url_for("auth.complete_profile"))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        row = get_db().execute(
            """SELECT id, username, role, password_hash, must_change_password,
                      first_name, last_name, email, phone, title, plant, notify_sms
               FROM users WHERE username = ?""",
            (username,),
        ).fetchone()

        valid = False
        try:
            if row:
                valid = ph.verify(row["password_hash"], form.password.data)
        except (VerifyMismatchError, InvalidHashError):
            pass

        if valid:
            user = _load_user_row(row)
            login_user(user)
            log(LOGIN, user_id=user.id, username=user.username, detail={"role": user.role})
            next_page = request.args.get("next")
            if next_page and urlsplit(next_page).netloc:
                next_page = None
            return redirect(next_page or url_for("main.dashboard"))

        log(LOGIN_FAILED, username=username, success=False, detail={"reason": "bad credentials"})
        flash("Invalid username or password.", "error")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        db = get_db()
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (ph.hash(form.new_password.data), current_user.id),
        )
        db.commit()
        current_user.must_change_password = False
        log(PASSWORD_CHANGED, user_id=current_user.id, username=current_user.username)
        flash("Password updated successfully.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/change_password.html", form=form)


@auth_bp.route("/complete-profile", methods=["GET", "POST"])
@login_required
def complete_profile():
    form = CompleteProfileForm()
    if form.validate_on_submit():
        db = get_db()
        db.execute(
            """UPDATE users
               SET first_name = ?, last_name = ?, email = ?, phone = ?, title = ?,
                   notify_sms = ?
               WHERE id = ?""",
            (
                form.first_name.data.strip(),
                form.last_name.data.strip(),
                form.email.data.strip(),
                form.phone.data.strip(),
                form.title.data.strip(),
                1 if form.notify_sms.data else 0,
                current_user.id,
            ),
        )
        db.commit()
        current_user.first_name = form.first_name.data.strip()
        current_user.last_name  = form.last_name.data.strip()
        current_user.email      = form.email.data.strip()
        current_user.phone      = form.phone.data.strip()
        current_user.title      = form.title.data.strip()
        current_user.notify_sms = bool(form.notify_sms.data)
        log(PROFILE_COMPLETED, user_id=current_user.id, username=current_user.username)
        flash("Profile saved.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/complete_profile.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log(LOGOUT, user_id=current_user.id, username=current_user.username)
    logout_user()
    return redirect(url_for("auth.login"))
