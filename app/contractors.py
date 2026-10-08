from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField
from wtforms.validators import DataRequired, Length, Optional

from app.audit import CONTRACTOR_CREATED, CONTRACTOR_UPDATED, log
from app.db import get_db
from app.roles import ADMIN, IT_ADMIN, OPERATOR, require_role

contractors_bp = Blueprint("contractors", __name__, url_prefix="/contractors")


class ContractorForm(FlaskForm):
    name    = StringField("Name",    validators=[DataRequired(), Length(max=128)])
    company = StringField("Company", validators=[Optional(), Length(max=128)])
    title   = StringField("Title",   validators=[Optional(), Length(max=128)])
    phone   = StringField("Phone",   validators=[Optional(), Length(max=32)])
    email   = StringField("Email",   validators=[Optional(), Length(max=128)])
    active  = BooleanField("Active", default=True)


@contractors_bp.route("/")
@login_required
@require_role(OPERATOR, ADMIN, IT_ADMIN)
def index():
    contractors = get_db().execute(
        "SELECT * FROM contractors ORDER BY name"
    ).fetchall()
    return render_template("contractors/index.html", contractors=contractors)


@contractors_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_role(OPERATOR, ADMIN, IT_ADMIN)
def new():
    form = ContractorForm()
    if form.validate_on_submit():
        db = get_db()
        db.execute(
            """INSERT INTO contractors (name, company, title, phone, email, active, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                form.name.data,
                form.company.data or None,
                form.title.data or None,
                form.phone.data or None,
                form.email.data or None,
                1 if form.active.data else 0,
                current_user.id,
            ),
        )
        db.commit()
        new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        log(CONTRACTOR_CREATED, user_id=current_user.id, username=current_user.username,
            resource="contractor", resource_id=new_id,
            detail={"name": form.name.data, "company": form.company.data or None})
        flash(f"Contractor {form.name.data!r} added.", "success")
        return redirect(url_for("contractors.index"))
    return render_template("contractors/form.html", form=form, contractor=None)


@contractors_bp.route("/<int:cid>/edit", methods=["GET", "POST"])
@login_required
@require_role(ADMIN, IT_ADMIN)
def edit(cid):
    db = get_db()
    contractor = db.execute("SELECT * FROM contractors WHERE id = ?", (cid,)).fetchone()
    if not contractor:
        flash("Contractor not found.", "error")
        return redirect(url_for("contractors.index"))

    form = ContractorForm(data=dict(contractor))
    if form.validate_on_submit():
        db.execute(
            """UPDATE contractors
               SET name=?, company=?, title=?, phone=?, email=?, active=?
               WHERE id=?""",
            (
                form.name.data,
                form.company.data or None,
                form.title.data or None,
                form.phone.data or None,
                form.email.data or None,
                1 if form.active.data else 0,
                cid,
            ),
        )
        db.commit()
        log(CONTRACTOR_UPDATED, user_id=current_user.id, username=current_user.username,
            resource="contractor", resource_id=cid,
            detail={"name": form.name.data, "active": form.active.data})
        flash(f"Contractor {form.name.data!r} updated.", "success")
        return redirect(url_for("contractors.index"))
    return render_template("contractors/form.html", form=form, contractor=contractor)
