from datetime import date, datetime

from flask import (Blueprint, abort, flash, make_response, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.validators import Length, Optional

from app.audit import (LOTO_ACTIVATED, LOTO_CREATED, LOTO_FORWARDED, LOTO_RELEASED,
                       LOTO_SUBMITTED, LOTO_UPDATED, log)
from app.notifications import notify_user
from app.signatures import create_signature_request, find_user_by_name
from app.db import get_db
from app.roles import ADMIN, EICC, IT_ADMIN, MECHANIC, OPERATOR, USER

# Admins can activate/release; Operators submit for review only
_LOTO_ADMIN_ROLES  = (IT_ADMIN, ADMIN)
# These roles can create and edit LOTOs
_LOTO_CREATE_ROLES = (IT_ADMIN, ADMIN, OPERATOR, USER)
# Mechanics and EI&C are view-only
_LOTO_VIEW_ONLY    = (MECHANIC, EICC)

loto_bp = Blueprint("loto", __name__, url_prefix="/loto")

PLANT_CHOICES = [("", "— Select —"), ("NV", "NV"), ("SE", "SE")]
BOUNDARY_COUNT = 42
VERIFIER_DEFAULT = 5
VERIFIER_MAX = 20


class LotoForm(FlaskForm):
    plant                 = SelectField("Plant", choices=PLANT_CHOICES)
    date                  = StringField("Date",                       validators=[Optional()])
    equipment_id          = StringField("Equipment ID",               validators=[Optional(), Length(max=128)])
    equipment_description = StringField("Equipment Description",      validators=[Optional(), Length(max=256)])
    location              = StringField("Location",                   validators=[Optional(), Length(max=128)])
    job_description       = TextAreaField("Job Description",          validators=[Optional()])
    group_leader          = StringField("Group Leader / Coordinator", validators=[Optional(), Length(max=128)])
    is_historical         = BooleanField("Historical record (entered from paper)")
    backdate_note         = StringField("Source / Reference",         validators=[Optional(), Length(max=256)])
    number_deferred       = BooleanField("Defer LOTO number assignment")


def _staff_names():
    """Return list of staff display names, current user first."""
    db = get_db()
    users = db.execute(
        "SELECT id, first_name, last_name, username FROM users ORDER BY first_name, last_name, username"
    ).fetchall()
    current_name = None
    others = []
    for u in users:
        name = f"{u['first_name']} {u['last_name']}".strip() if u["first_name"] else u["username"]
        if u["id"] == current_user.id:
            current_name = name
        else:
            others.append(name)
    result = []
    if current_name:
        result.append(current_name)
    result.extend(others)
    return result


def _next_loto_number(plant):
    """Generate next sequential LOTO number using max-sequence scan (safe against deletions)."""
    db = get_db()
    year = datetime.now().strftime("%y")
    prefix = f"{plant}-LOTO-{year}-"
    rows = db.execute(
        "SELECT loto_number FROM loto_permits WHERE loto_number LIKE ?",
        (prefix + "%",),
    ).fetchall()
    max_seq = 0
    for row in rows:
        try:
            seq = int(row["loto_number"].split("-")[-1])
            if seq > max_seq:
                max_seq = seq
        except (ValueError, IndexError):
            pass
    return f"{plant}-LOTO-{year}-{max_seq + 1:04d}"


def _save_loto(form, loto_id=None, from_swp=None):
    """Insert or update a LOTO record from form data. Returns (loto_id, is_new)."""
    db = get_db()

    is_historical = 1 if form.is_historical.data else 0
    number_deferred = 1 if form.number_deferred.data else 0
    plant = form.plant.data or None

    equip_id_raw = request.form.get("equip_id", "").strip()
    equip_id = int(equip_id_raw) if equip_id_raw.isdigit() else None
    if equip_id:
        if not db.execute("SELECT id FROM equipment WHERE id = ?", (equip_id,)).fetchone():
            equip_id = None

    fields = dict(
        plant=plant,
        date=form.date.data or None,
        equipment_id=form.equipment_id.data or None,
        equipment_description=form.equipment_description.data or None,
        location=form.location.data or None,
        job_description=form.job_description.data or None,
        group_leader=form.group_leader.data or None,
        is_historical=is_historical,
        backdate_note=form.backdate_note.data or None,
        status="released" if is_historical else "draft",
        equip_id=equip_id,
        number_deferred=number_deferred,
    )

    if loto_id is None:
        # Generate LOTO number on first insert if plant is known, unless deferred
        if plant and not number_deferred:
            fields["loto_number"] = _next_loto_number(plant)
        if from_swp is not None:
            fields["swp_id"] = from_swp

        cols = ", ".join(list(fields.keys()) + ["created_by"])
        placeholders = ", ".join(["?"] * (len(fields) + 1))
        db.execute(
            f"INSERT INTO loto_permits ({cols}) VALUES ({placeholders})",
            list(fields.values()) + [current_user.id],
        )
        loto_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        is_new = True
    else:
        # If plant just became set and we still have no number, generate one now
        # (unless the number is explicitly deferred)
        existing = db.execute(
            "SELECT loto_number FROM loto_permits WHERE id = ?", (loto_id,)
        ).fetchone()
        if existing and not existing["loto_number"] and plant and not number_deferred:
            fields["loto_number"] = _next_loto_number(plant)

        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE loto_permits SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            list(fields.values()) + [loto_id],
        )
        is_new = False

    # Save boundary rows — delete + re-insert
    db.execute("DELETE FROM loto_boundaries WHERE loto_id = ?", (loto_id,))
    for pos in range(1, BOUNDARY_COUNT + 1):
        equipment        = request.form.get(f"b_equipment_{pos}", "").strip()
        b_location       = request.form.get(f"b_location_{pos}", "").strip()
        energy_type      = request.form.get(f"b_energy_type_{pos}", "").strip()
        isolation_device = request.form.get(f"b_isolation_device_{pos}", "").strip()
        device_position  = request.form.get(f"b_device_position_{pos}", "").strip()
        if any([equipment, b_location, energy_type, isolation_device, device_position]):
            db.execute(
                """INSERT INTO loto_boundaries
                       (loto_id, position, equipment, location, energy_type,
                        isolation_device, device_position)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (loto_id, pos, equipment or None, b_location or None,
                 energy_type or None, isolation_device or None, device_position or None),
            )

    # Save verifier rows — delete + re-insert
    db.execute("DELETE FROM loto_verifiers WHERE loto_id = ?", (loto_id,))
    for v_idx in range(VERIFIER_MAX):
        name   = request.form.get(f"v_name_{v_idx}", "").strip()
        v_date = request.form.get(f"v_date_{v_idx}", "").strip()
        if name:
            db.execute(
                "INSERT INTO loto_verifiers (loto_id, position, name, date) VALUES (?, ?, ?, ?)",
                (loto_id, v_idx, name, v_date or None),
            )

    # Upsert equipment work log
    equip_comments  = request.form.get("equip_comments", "").strip() or None
    equip_start     = request.form.get("equip_start_date", "").strip() or None
    equip_end       = request.form.get("equip_end_date", "").strip() or None
    db.execute("DELETE FROM equipment_work_log WHERE permit_type='loto' AND permit_id=?", (loto_id,))
    if equip_id:
        db.execute(
            """INSERT INTO equipment_work_log
                   (equipment_id, permit_type, permit_id, comments, start_date, end_date)
               VALUES (?, 'loto', ?, ?, ?, ?)""",
            (equip_id, loto_id, equip_comments, equip_start, equip_end),
        )

    # Back-link: if this LOTO is tied to a SWP, write our number into permit_loto
    loto_row = db.execute(
        "SELECT loto_number, swp_id FROM loto_permits WHERE id = ?", (loto_id,)
    ).fetchone()
    if loto_row and loto_row["loto_number"] and loto_row["swp_id"]:
        db.execute(
            """UPDATE swp_permits SET permit_loto = ?
               WHERE id = ? AND (permit_loto IS NULL OR permit_loto = '')""",
            (loto_row["loto_number"], loto_row["swp_id"]),
        )

    db.commit()
    return loto_id, is_new


# ── Routes ─────────────────────────────────────────────────────────────────────

@loto_bp.route("/")
@login_required
def index():
    db = get_db()
    uid = current_user.id
    is_admin = current_user.role in (ADMIN, IT_ADMIN)

    db.execute(
        """DELETE FROM loto_permits
           WHERE status = 'draft' AND created_by = ?
             AND created_at < datetime('now', '-48 hours')""",
        (uid,),
    )
    db.commit()

    is_view_only = current_user.role in _LOTO_VIEW_ONLY

    is_operator = current_user.role == OPERATOR

    show = request.args.get("show", "active")
    valid_shows = ("drafts", "pending_operator", "pending", "active", "released", "all")
    if show not in valid_shows:
        show = "active"

    base = """SELECT l.id, l.loto_number, l.plant, l.date, l.location,
                     l.equipment_description, l.status, l.is_historical,
                     l.number_deferred, l.swp_id, l.created_at, l.created_by,
                     u.first_name, u.last_name,
                     s.permit_number AS swp_number
              FROM loto_permits l
              LEFT JOIN users u ON u.id = l.created_by
              LEFT JOIN swp_permits s ON s.id = l.swp_id"""

    if is_view_only:
        # EI&C / Mechanic: own permits at any stage + management-approved
        if show == "released":
            query  = base + " WHERE l.status = 'released' AND l.created_by = ?"
            params = (uid,)
        elif show == "all":
            query  = base + " WHERE l.created_by = ? OR l.status IN ('active','released')"
            params = (uid,)
        else:
            query  = base + " WHERE (l.status = 'active' AND 1=1) OR (l.created_by = ? AND l.status = 'active')"
            query, params = base + " WHERE l.status = 'active'", ()
            show = "active"
    elif show == "drafts":
        query  = base + " WHERE l.status = 'draft' AND l.created_by = ?"
        params = (uid,)
    elif show == "pending_operator":
        if is_admin or is_operator:
            query, params = base + " WHERE l.status = 'pending_operator'", ()
        else:
            query  = base + " WHERE l.status = 'pending_operator' AND l.created_by = ?"
            params = (uid,)
    elif show == "pending":
        if is_admin:
            query, params = base + " WHERE l.status = 'pending'", ()
        else:
            query  = base + " WHERE l.status = 'pending' AND l.created_by = ?"
            params = (uid,)
    elif show == "active":
        query, params = base + " WHERE l.status = 'active'", ()
    elif show == "released":
        query, params = base + " WHERE l.status = 'released'", ()
    else:
        if is_admin or is_operator:
            query, params = base, ()
        else:
            query  = base + " WHERE l.status != 'draft' OR l.created_by = ?"
            params = (uid,)

    query += " ORDER BY l.date DESC, l.created_at DESC"
    lotos = db.execute(query, params).fetchall()
    return render_template("loto/index.html", lotos=lotos, show=show,
                           is_admin=is_admin, is_operator=is_operator,
                           is_view_only=is_view_only, uid=uid)


@loto_bp.route("/new", methods=["GET"])
@login_required
def new():
    if current_user.role in _LOTO_VIEW_ONLY:
        abort(403)
    from_swp = request.args.get("from_swp")
    form = LotoForm()
    form.date.data = date.today().strftime("%Y-%m-%d")

    if current_user.profile_complete():
        name = f"{current_user.first_name} {current_user.last_name}".strip()
        form.group_leader.data = name or current_user.username

    swp_row = None
    if from_swp and from_swp.isdigit():
        swp_row = get_db().execute(
            "SELECT id, permit_number, plant, location FROM swp_permits WHERE id = ?",
            (int(from_swp),),
        ).fetchone()
        if swp_row:
            form.plant.data    = swp_row["plant"] or ""
            form.location.data = swp_row["location"] or ""

    names = _staff_names()
    return render_template("loto/form.html", form=form, loto=None,
                           from_swp=from_swp, swp_row=swp_row,
                           names=names,
                           boundaries_by_pos={},
                           verifier_count=VERIFIER_DEFAULT,
                           verifiers_by_pos={},
                           cur_equip=None,
                           work_log=None,
                           can_edit=True,
                           is_admin=current_user.role in (ADMIN, IT_ADMIN))


@loto_bp.route("/<int:loto_id>/edit", methods=["GET"])
@login_required
def edit(loto_id):
    db = get_db()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if not loto:
        flash("LOTO not found.", "error")
        return redirect(url_for("loto.index"))

    is_admin     = current_user.role in (ADMIN, IT_ADMIN)
    is_creator   = loto["created_by"] == current_user.id
    is_view_only = current_user.role in _LOTO_VIEW_ONLY
    # View-only roles can see any LOTO; others must be creator or admin
    if not is_view_only and not is_creator and not is_admin:
        abort(403)
    can_edit = not is_view_only and (is_creator or is_admin) and loto["status"] in ("draft",)

    boundaries = db.execute(
        "SELECT * FROM loto_boundaries WHERE loto_id = ? ORDER BY position", (loto_id,)
    ).fetchall()
    verifiers = db.execute(
        "SELECT * FROM loto_verifiers WHERE loto_id = ? ORDER BY position", (loto_id,)
    ).fetchall()

    swp_row = None
    if loto["swp_id"]:
        swp_row = db.execute(
            "SELECT id, permit_number, plant, location FROM swp_permits WHERE id = ?",
            (loto["swp_id"],),
        ).fetchone()

    cur_equip = None
    work_log = None
    if loto["equip_id"]:
        cur_equip = db.execute(
            "SELECT id, plant, tag_id, process_name, make, model FROM equipment WHERE id = ?",
            (loto["equip_id"],),
        ).fetchone()
        work_log = db.execute(
            "SELECT comments, start_date, end_date FROM equipment_work_log WHERE permit_type='loto' AND permit_id=?",
            (loto_id,),
        ).fetchone()

    form = LotoForm(data=dict(loto))
    boundaries_by_pos = {b["position"]: b for b in boundaries}
    verifiers_by_pos  = {v["position"]: v for v in verifiers}
    verifier_count    = max(VERIFIER_DEFAULT, len(verifiers) + 1)

    names = _staff_names()
    return render_template("loto/form.html", form=form, loto=loto,
                           from_swp=loto["swp_id"], swp_row=swp_row,
                           names=names,
                           boundaries_by_pos=boundaries_by_pos,
                           verifier_count=verifier_count,
                           verifiers_by_pos=verifiers_by_pos,
                           cur_equip=cur_equip,
                           work_log=work_log,
                           can_edit=can_edit,
                           is_admin=is_admin)


@loto_bp.route("/autosave", methods=["POST"])
@login_required
def autosave():
    if current_user.role in _LOTO_VIEW_ONLY:
        abort(403)
    form = LotoForm()
    loto_id_raw  = request.form.get("loto_id", "").strip()
    from_swp_raw = request.form.get("from_swp", "").strip()
    from_swp = int(from_swp_raw) if from_swp_raw.isdigit() else None

    if loto_id_raw.isdigit():
        loto_id = int(loto_id_raw)
        loto = get_db().execute(
            "SELECT * FROM loto_permits WHERE id = ? AND created_by = ? AND status = 'draft'",
            (loto_id, current_user.id),
        ).fetchone()
        if not loto:
            abort(403)
        saved_id, _ = _save_loto(form, loto_id=loto_id, from_swp=from_swp)
        log(LOTO_UPDATED, user_id=current_user.id, username=current_user.username,
            resource="loto", resource_id=saved_id)
    else:
        saved_id, _ = _save_loto(form, from_swp=from_swp)
        log(LOTO_CREATED, user_id=current_user.id, username=current_user.username,
            resource="loto", resource_id=saved_id)

    loto_row = get_db().execute(
        "SELECT loto_number, swp_id, is_historical FROM loto_permits WHERE id = ?", (saved_id,)
    ).fetchone()

    response = make_response(
        render_template("loto/_save_oob.html", loto_id=saved_id, loto=loto_row)
    )
    response.headers["HX-Push-Url"] = url_for("loto.edit", loto_id=saved_id)
    return response


@loto_bp.route("/verifier-row")
@login_required
def verifier_row():
    idx   = int(request.args.get("idx", 0))
    names = _staff_names()
    return render_template("loto/_verifier_row.html", idx=idx, names=names)


@loto_bp.route("/<int:loto_id>/submit", methods=["POST"])
@login_required
def submit(loto_id):
    """Submit a draft LOTO. EI&C/Mechanic → pending_operator; Operator → pending."""
    if current_user.role in _LOTO_ADMIN_ROLES:
        abort(403)
    db = get_db()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if not loto or loto["created_by"] != current_user.id:
        abort(403)
    if loto["status"] != "draft":
        flash("Only draft LOTOs can be submitted for review.", "error")
        return redirect(url_for("loto.edit", loto_id=loto_id))

    if current_user.role in _LOTO_VIEW_ONLY:
        new_status = "pending_operator"
        msg = f"LOTO {loto['loto_number']} submitted for operator review."
        dest_show = "pending_operator"
    else:
        new_status = "pending"
        msg = f"LOTO {loto['loto_number']} submitted for management review."
        dest_show = "pending"

    db.execute(
        "UPDATE loto_permits SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (new_status, loto_id),
    )
    db.commit()
    log(LOTO_SUBMITTED, user_id=current_user.id, username=current_user.username,
        resource="loto", resource_id=loto_id,
        detail={"status": new_status})
    flash(msg, "success")
    return redirect(url_for("loto.index", show=dest_show))


@loto_bp.route("/<int:loto_id>/forward", methods=["POST"])
@login_required
def forward(loto_id):
    """Operator forwards a pending_operator LOTO to management queue."""
    if current_user.role not in (OPERATOR, ADMIN, IT_ADMIN):
        abort(403)
    db = get_db()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if not loto:
        flash("LOTO not found.", "error")
        return redirect(url_for("loto.index"))
    if loto["status"] != "pending_operator":
        flash("Only operator-pending LOTOs can be forwarded.", "error")
        return redirect(url_for("loto.index"))
    db.execute(
        "UPDATE loto_permits SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (loto_id,),
    )
    db.commit()
    log(LOTO_FORWARDED, user_id=current_user.id, username=current_user.username,
        resource="loto", resource_id=loto_id)
    flash(f"LOTO {loto['loto_number']} forwarded to management for review.", "success")
    return redirect(url_for("loto.index", show="pending"))


@loto_bp.route("/<int:loto_id>/activate", methods=["POST"])
@login_required
def activate(loto_id):
    if current_user.role not in _LOTO_ADMIN_ROLES:
        abort(403)
    db = get_db()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if not loto:
        flash("LOTO not found.", "error")
        return redirect(url_for("loto.index"))
    if loto["status"] not in ("draft", "pending", "pending_operator"):
        flash("Only draft or pending LOTOs can be activated.", "error")
        return redirect(url_for("loto.index"))
    if not loto["swp_id"] and not loto["is_historical"]:
        flash("Cannot activate: no SWP is linked. Create or link a SWP first.", "error")
        return redirect(url_for("loto.edit", loto_id=loto_id))

    loto_number = loto["loto_number"]
    if not loto_number:
        if not loto["plant"]:
            flash("Cannot activate: set a plant so a LOTO number can be assigned.", "error")
            return redirect(url_for("loto.edit", loto_id=loto_id))
        loto_number = _next_loto_number(loto["plant"])
        db.execute(
            "UPDATE loto_permits SET loto_number=?, number_deferred=0 WHERE id=?",
            (loto_number, loto_id),
        )
        if loto["swp_id"]:
            db.execute(
                """UPDATE swp_permits SET permit_loto = ?
                   WHERE id = ? AND (permit_loto IS NULL OR permit_loto = '')""",
                (loto_number, loto["swp_id"]),
            )

    db.execute(
        "UPDATE loto_permits SET status='active', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (loto_id,),
    )
    db.commit()
    log(LOTO_ACTIVATED, user_id=current_user.id, username=current_user.username,
        resource="loto", resource_id=loto_id)

    # Create signature requests for verifiers whose names match a user account
    verifiers = db.execute(
        "SELECT name FROM loto_verifiers WHERE loto_id=? AND name IS NOT NULL AND name != ''",
        (loto_id,),
    ).fetchall()
    for v in verifiers:
        uid = find_user_by_name(v["name"])
        if uid:
            create_signature_request("loto", loto_id, "loto_verifier", uid)

    flash(f"LOTO {loto_number} activated.", "success")
    return redirect(url_for("loto.index", show="active"))


@loto_bp.route("/<int:loto_id>/release", methods=["POST"])
@login_required
def release(loto_id):
    db = get_db()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if not loto:
        flash("LOTO not found.", "error")
        return redirect(url_for("loto.index"))

    if current_user.role not in _LOTO_ADMIN_ROLES:
        abort(403)
    if loto["status"] != "active":
        flash("Only active LOTOs can be released.", "error")
        return redirect(url_for("loto.index"))

    db.execute(
        "UPDATE loto_permits SET status='released', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (loto_id,),
    )
    db.commit()
    log(LOTO_RELEASED, user_id=current_user.id, username=current_user.username,
        resource="loto", resource_id=loto_id)

    if loto["swp_id"]:
        swp = get_db().execute(
            "SELECT work_lead, permit_number FROM swp_permits WHERE id=?", (loto["swp_id"],)
        ).fetchone()
        if swp:
            uid = find_user_by_name(swp["work_lead"])
            if uid:
                swp_pn = swp["permit_number"] or f"SWP #{loto['swp_id']}"
                notify_user(uid, f"LOTO {loto['loto_number']} (linked to {swp_pn}) has been released.")

    flash(f"LOTO {loto['loto_number']} released.", "success")
    return redirect(url_for("loto.index", show="released"))
