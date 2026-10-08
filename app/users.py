import secrets
from datetime import datetime

from argon2 import PasswordHasher
from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField, TelField
from wtforms.validators import DataRequired, Email, Length

from app.audit import log, USER_CREATED
from app.auth import _e164_phone
from app.db import get_db
from app.roles import require_role, IT_ADMIN

users_bp = Blueprint("users", __name__, url_prefix="/users")
ph = PasswordHasher()

ROLE_CHOICES = [
    ("operator", "Operator"),
    ("mechanic", "Mechanic"),
    ("ei_c",     "EI&C"),
    ("user",     "User"),
    ("admin",    "Admin"),
    ("it_admin", "IT Admin"),
]

def _generate_temp_password():
    return secrets.token_urlsafe(12)


def _plants_from_form(form) -> str | None:
    """Combine plant_nv / plant_se checkboxes into a stored string."""
    plants = []
    if getattr(form, "plant_nv", None) and form.plant_nv.data:
        plants.append("NV")
    if getattr(form, "plant_se", None) and form.plant_se.data:
        plants.append("SE")
    return ",".join(plants) or None


class CreateUserForm(FlaskForm):
    username   = StringField("Username",    validators=[DataRequired(), Length(max=64)])
    first_name = StringField("First Name",  validators=[DataRequired(), Length(max=64)])
    last_name  = StringField("Last Name",   validators=[DataRequired(), Length(max=64)])
    email      = StringField("Email",       validators=[DataRequired(), Email(), Length(max=128)])
    phone      = TelField(  "Phone Number", validators=[DataRequired(), Length(max=32), _e164_phone])
    title      = StringField("Title",       validators=[DataRequired(), Length(max=128)])
    plant_nv   = BooleanField("NV")
    plant_se   = BooleanField("SE")
    role       = SelectField("Role",        choices=ROLE_CHOICES, default="user")
    notify_sms = BooleanField("Receive SMS notifications")
    submit     = SubmitField("Create User")


@users_bp.route("/")
@login_required
@require_role(IT_ADMIN)
def index():
    users = get_db().execute(
        """SELECT id, username, first_name, last_name, email, phone, title,
                  role, must_change_password, created_at
           FROM users ORDER BY created_at DESC"""
    ).fetchall()
    return render_template("users/index.html", users=users)


@users_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_role(IT_ADMIN)
def new():
    form = CreateUserForm()
    if form.validate_on_submit():
        db = get_db()

        # Check username not already taken
        existing = db.execute(
            "SELECT id FROM users WHERE username = ?", (form.username.data.strip(),)
        ).fetchone()
        if existing:
            form.username.errors.append("Username already exists.")
            return render_template("users/new.html", form=form, temp_password=None)

        temp_pw = _generate_temp_password()
        db.execute(
            """INSERT INTO users
                   (username, password_hash, role, must_change_password,
                    first_name, last_name, email, phone, title, plant, notify_sms)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
            (
                form.username.data.strip(),
                ph.hash(temp_pw),
                form.role.data,
                form.first_name.data.strip(),
                form.last_name.data.strip(),
                form.email.data.strip(),
                form.phone.data.strip(),
                form.title.data.strip(),
                _plants_from_form(form),
                1 if form.notify_sms.data else 0,
            ),
        )
        db.commit()

        new_id = db.execute(
            "SELECT id FROM users WHERE username = ?", (form.username.data.strip(),)
        ).fetchone()["id"]

        from flask_login import current_user
        log(USER_CREATED, user_id=current_user.id, username=current_user.username,
            resource="user", resource_id=new_id,
            detail={"created_username": form.username.data.strip(), "role": form.role.data})

        return render_template("users/created.html",
                               username=form.username.data.strip(),
                               temp_password=temp_pw)

    return render_template("users/new.html", form=form, temp_password=None)
