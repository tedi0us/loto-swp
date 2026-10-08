import re

from flask import (Blueprint, abort, flash, make_response, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.audit import EQUIPMENT_CREATED, EQUIPMENT_UPDATED, log
from app.db import get_db
from app.roles import ADMIN, EICC, IT_ADMIN, MECHANIC

equipment_bp = Blueprint("equipment", __name__, url_prefix="/equipment")


class EquipmentForm(FlaskForm):
    plant        = SelectField("Plant", choices=[("", "— Select —"), ("NV", "NV"), ("SE", "SE")],
                               validators=[DataRequired(message="Plant is required.")])
    process_name = StringField("Process Name", validators=[DataRequired(message="Process name is required."), Length(max=128)])
    make         = StringField("Make",     validators=[Optional(), Length(max=64)])
    model        = StringField("Model",    validators=[Optional(), Length(max=64)])
    location     = StringField("Location", validators=[Optional(), Length(max=128)])
    notes        = TextAreaField("Notes",  validators=[Optional()])
    active       = BooleanField("Active", default=True)


def _make_tag_id(plant, process_name):
    slug = re.sub(r"[^A-Z0-9]+", "-", process_name.upper()).strip("-")
    return f"{plant}-{slug}"


@equipment_bp.route("/picker")
@login_required
def picker():
    plant = request.args.get("plant", "")
    return render_template("equipment/picker.html", plant=plant)


@equipment_bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    plant = request.args.get("plant", "").strip()
    db = get_db()

    sql = "SELECT id, plant, tag_id, process_name, make, model FROM equipment WHERE active = 1"
    params = []
    if plant:
        sql += " AND plant = ?"
        params.append(plant)
    if q:
        like = f"%{q}%"
        sql += " AND (tag_id LIKE ? OR process_name LIKE ? OR make LIKE ? OR model LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY tag_id LIMIT 60"

    results = db.execute(sql, params).fetchall()
    return render_template("equipment/search_results.html", results=results, q=q)


@equipment_bp.route("/new-form")
@login_required
def new_form():
    plant = request.args.get("plant", "")
    return render_template("equipment/new_form.html", plant=plant)


@equipment_bp.route("/select/<int:equip_id>")
@login_required
def select(equip_id):
    db = get_db()
    eq = db.execute(
        "SELECT id, plant, tag_id, process_name, make, model FROM equipment WHERE id = ? AND active = 1",
        (equip_id,),
    ).fetchone()
    if not eq:
        return "", 404
    response = make_response(render_template("equipment/selected.html", eq=eq))
    response.headers["HX-Trigger"] = "closeEquipmentDialog"
    return response


@equipment_bp.route("/create", methods=["POST"])
@login_required
def create():
    plant = request.form.get("plant", "").strip()
    process_name = request.form.get("process_name", "").strip()
    make = request.form.get("make", "").strip() or None
    model = request.form.get("model", "").strip() or None
    location = request.form.get("location", "").strip() or None
    notes = request.form.get("notes", "").strip() or None

    if not plant or not process_name:
        return "<p style='color:var(--danger);padding:.5rem'>Plant and Process Name are required.</p>", 422

    tag_id = _make_tag_id(plant, process_name)
    db = get_db()

    existing = db.execute("SELECT id FROM equipment WHERE tag_id = ?", (tag_id,)).fetchone()
    if existing:
        eq = db.execute(
            "SELECT id, plant, tag_id, process_name, make, model FROM equipment WHERE id = ?",
            (existing["id"],),
        ).fetchone()
    else:
        db.execute(
            """INSERT INTO equipment
                   (plant, process_name, tag_id, make, model, location, notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (plant, process_name, tag_id, make, model, location, notes, current_user.id),
        )
        db.commit()
        new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        log(EQUIPMENT_CREATED, user_id=current_user.id, username=current_user.username,
            resource="equipment", resource_id=new_id, detail={"tag_id": tag_id})
        eq = db.execute(
            "SELECT id, plant, tag_id, process_name, make, model FROM equipment WHERE id = ?",
            (new_id,),
        ).fetchone()

    response = make_response(render_template("equipment/selected.html", eq=eq))
    response.headers["HX-Trigger"] = "closeEquipmentDialog"
    return response


# ── Admin equipment management ────────────────────────────────────────────────

@equipment_bp.route("/")
@login_required
def index():
    if current_user.role not in (ADMIN, IT_ADMIN, MECHANIC, EICC):
        abort(403)
    db = get_db()
    equipment = db.execute(
        "SELECT id, plant, tag_id, process_name, make, model, location, active FROM equipment ORDER BY plant, tag_id"
    ).fetchall()
    return render_template("equipment/index.html", equipment=equipment)


@equipment_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if current_user.role not in (ADMIN, IT_ADMIN, MECHANIC, EICC):
        abort(403)
    form = EquipmentForm()
    if form.validate_on_submit():
        tag_id = _make_tag_id(form.plant.data, form.process_name.data)
        db = get_db()
        existing = db.execute("SELECT id FROM equipment WHERE tag_id = ?", (tag_id,)).fetchone()
        if existing:
            flash(f"Equipment with tag {tag_id} already exists.", "error")
        else:
            db.execute(
                """INSERT INTO equipment
                       (plant, process_name, tag_id, make, model, location, notes, active, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (form.plant.data, form.process_name.data, tag_id,
                 form.make.data or None, form.model.data or None,
                 form.location.data or None, form.notes.data or None,
                 1 if form.active.data else 0, current_user.id),
            )
            db.commit()
            new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            log(EQUIPMENT_CREATED, user_id=current_user.id, username=current_user.username,
                resource="equipment", resource_id=new_id, detail={"tag_id": tag_id})
            flash(f"{tag_id} added.", "success")
            return redirect(url_for("equipment.index"))
    return render_template("equipment/admin_form.html", form=form, equipment=None)


@equipment_bp.route("/<int:equip_id>/edit", methods=["GET", "POST"])
@login_required
def edit_record(equip_id):
    if current_user.role not in (ADMIN, IT_ADMIN, MECHANIC, EICC):
        abort(403)
    db = get_db()
    eq = db.execute("SELECT * FROM equipment WHERE id = ?", (equip_id,)).fetchone()
    if not eq:
        flash("Equipment not found.", "error")
        return redirect(url_for("equipment.index"))

    form = EquipmentForm(data=dict(eq))
    if form.validate_on_submit():
        new_tag = _make_tag_id(form.plant.data, form.process_name.data)
        clash = db.execute(
            "SELECT id FROM equipment WHERE tag_id = ? AND id != ?", (new_tag, equip_id)
        ).fetchone()
        if clash:
            flash(f"Another record already uses tag {new_tag}.", "error")
        else:
            db.execute(
                """UPDATE equipment
                   SET plant=?, process_name=?, tag_id=?, make=?, model=?,
                       location=?, notes=?, active=?
                   WHERE id=?""",
                (form.plant.data, form.process_name.data, new_tag,
                 form.make.data or None, form.model.data or None,
                 form.location.data or None, form.notes.data or None,
                 1 if form.active.data else 0, equip_id),
            )
            db.commit()
            log(EQUIPMENT_UPDATED, user_id=current_user.id, username=current_user.username,
                resource="equipment", resource_id=equip_id,
                detail={"tag_id": new_tag, "active": form.active.data})
            flash(f"{new_tag} updated.", "success")
            return redirect(url_for("equipment.index"))
    return render_template("equipment/admin_form.html", form=form, equipment=eq)
