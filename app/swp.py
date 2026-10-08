from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, make_response, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.validators import Length, Optional

from app.audit import (SWP_APPROVED, SWP_CREATED, SWP_FORWARDED, SWP_RECALLED,
                       SWP_SUBMITTED, SWP_UPDATED, log)
from app.notifications import notify_admins, notify_user
from app.signatures import create_signature_request, find_user_by_name
from app.db import get_db
from app.roles import ADMIN, EICC, IT_ADMIN, MECHANIC, OPERATOR

swp_bp = Blueprint("swp", __name__, url_prefix="/swp")

YES_NO = [("", "—"), ("1", "Yes"), ("0", "No")]


class SWPForm(FlaskForm):
    # ── Plant ─────────────────────────────────────────────────────────────────
    plant = SelectField("Plant", choices=[("", "— Select —"), ("NV", "NV"), ("SE", "SE")])

    # ── Section 1: Permit info ────────────────────────────────────────────────
    permit_number  = StringField("Permit #",      validators=[Optional(), Length(max=64)])
    date           = StringField("Date",          validators=[Optional()])
    time           = StringField("Time",          validators=[Optional()])
    location       = StringField("Location",      validators=[Optional(), Length(max=128)])
    company        = StringField("Company",       validators=[Optional(), Length(max=128)], default=lambda: current_app.config.get("COMPANY_NAME", ""))
    work_lead      = StringField("Work Lead",     validators=[Optional(), Length(max=128)])
    scope_of_work  = TextAreaField("Scope of Work", validators=[Optional()])

    # ── Section 2: Associated permits ─────────────────────────────────────────
    permit_loto        = StringField("LOTO #",           validators=[Optional(), Length(max=64)])
    permit_hot_work    = StringField("Hot Work #",       validators=[Optional(), Length(max=64)])
    permit_electrical  = StringField("Electrical #",     validators=[Optional(), Length(max=64)])
    permit_confined_sp = StringField("Confined Space #", validators=[Optional(), Length(max=64)])
    permit_excavation  = StringField("Excavation #",     validators=[Optional(), Length(max=64)])
    permit_other       = StringField("Other #",          validators=[Optional(), Length(max=64)])

    # ── Section 3: Pre-job questions ──────────────────────────────────────────
    first_time   = SelectField("First time performing this work?", choices=YES_NO)
    training     = SelectField("Training complete?",               choices=YES_NO)
    inspection   = SelectField("Pre-job inspection done?",         choices=YES_NO)
    continuation = SelectField("Continuation Sheet Required?",      choices=YES_NO)

    # ── Section 4: Hazard energy types ────────────────────────────────────────
    he_electrical = BooleanField("Electrical")
    he_mechanical = BooleanField("Mechanical")
    he_thermal    = BooleanField("Thermal")
    he_pressure   = BooleanField("Pressure")
    he_motion     = BooleanField("Motion")
    he_gravity    = BooleanField("Gravity")
    he_chemical   = BooleanField("Chemical")
    he_radiation  = BooleanField("Radiation")
    he_biological = BooleanField("Biological")
    he_noise      = BooleanField("Noise")

    # ── Section 5: PPE ────────────────────────────────────────────────────────
    # Hard hat, safety glasses, gloves, FR clothing, and safety shoes are
    # pre-checked on new forms — they apply to virtually every job site task.
    ppe_hard_hat        = BooleanField("Hard Hat",        default=True)
    ppe_safety_glasses  = BooleanField("Safety Glasses",  default=True)
    ppe_gloves          = BooleanField("Gloves",          default=True)
    ppe_fr_clothing     = BooleanField("FR Clothing",     default=True)
    ppe_arc_flash_suit  = BooleanField("Arc Flash Suit")
    ppe_arc_flash_hood  = BooleanField("Arc Flash Hood")
    ppe_insul_gloves    = BooleanField("Insulating Gloves")
    ppe_face_shield     = BooleanField("Face Shield")
    ppe_hearing_prot    = BooleanField("Hearing Protection")
    ppe_respirator      = BooleanField("Respirator")
    ppe_safety_shoes    = BooleanField("Safety Shoes",    default=True)
    ppe_fall_protect    = BooleanField("Fall Protection")
    ppe_cut_protect     = BooleanField("Cut Protection")
    ppe_welding_hood    = BooleanField("Welding Hood")
    ppe_brine_gear      = BooleanField("Brine Gear")
    ppe_chem_res_cloth  = BooleanField("Chem-Resistant Clothing")
    ppe_chem_res_gloves = BooleanField("Chem-Resistant Gloves")
    ppe_chem_res_boot   = BooleanField("Chem-Resistant Boots")
    ppe_other1          = BooleanField("Other")
    ppe_other1_txt      = StringField("", validators=[Optional(), Length(max=64)])
    ppe_other2          = BooleanField("Other 2")
    ppe_other2_txt      = StringField("", validators=[Optional(), Length(max=64)])

    # ── Section 6: Task hazard assessment ─────────────────────────────────────
    tha_loto           = BooleanField("LOTO")
    tha_confined_space = BooleanField("Confined Space")
    tha_elevated_work  = BooleanField("Elevated Work")
    tha_hot_work       = BooleanField("Hot Work")
    tha_vehicles       = BooleanField("Vehicles")
    tha_mobile_equip   = BooleanField("Mobile Equipment")
    tha_heavy_equip    = BooleanField("Heavy Equipment")
    tha_power_tools    = BooleanField("Power Tools")
    tha_slips_trips    = BooleanField("Slips / Trips / Falls", default=True)
    tha_nip_pinch      = BooleanField("Nip / Pinch Points")
    tha_break_sys_integ= BooleanField("Breaking System Integrity")
    tha_ergonomics     = BooleanField("Ergonomics")
    tha_extreme_heat   = BooleanField("Extreme Heat")
    tha_extreme_cold   = BooleanField("Extreme Cold")
    tha_high_wind      = BooleanField("High Wind")
    tha_rain_snow      = BooleanField("Rain / Snow")
    tha_other          = BooleanField("Other")
    tha_other_txt      = StringField("", validators=[Optional(), Length(max=64)])

    # ── Section 7: Task safeguards ────────────────────────────────────────────
    ts_review_procedures = BooleanField("Review Procedures")
    ts_valve_positions   = BooleanField("Verify Valve Positions")
    ts_cross_connects    = BooleanField("Check Cross-Connects")
    ts_barricade         = BooleanField("Barricade Area")
    ts_adjacent_work     = BooleanField("Adjacent Work Review")
    ts_haz_comm          = BooleanField("Hazard Communication")
    ts_addl_permits      = BooleanField("Additional Permits")
    ts_equip_inspect     = BooleanField("Equipment Inspection")
    ts_high_temp_lines   = BooleanField("High-Temp Line Check")
    ts_min_approach      = BooleanField("Minimum Approach Distance")
    ts_safety_observer   = BooleanField("Safety Observer")
    ts_slings_lifting    = BooleanField("Slings / Lifting Devices")
    ts_other1            = BooleanField("Other")
    ts_other1_txt        = StringField("", validators=[Optional(), Length(max=64)])
    ts_other2            = BooleanField("Other 2")
    ts_other2_txt        = StringField("", validators=[Optional(), Length(max=64)])

    # ── Section 8: Free text ──────────────────────────────────────────────────
    hazard_mitigation   = TextAreaField("Hazard Mitigation",   validators=[Optional()],
                                        default="Eyes on task. Spotter required when using a vehicle. "
                                                 "Slips, trips, and falls awareness.")
    additional_comments = TextAreaField("Additional Comments", validators=[Optional()])

    # ── Historical record ─────────────────────────────────────────────────────
    is_backdated  = BooleanField("This is a historical record (entered from paper)")
    backdate_note = StringField("Source / Reference", validators=[Optional(), Length(max=256)])

    # ── PJSM / EHST ──────────────────────────────────────────────────────────
    pjsm_leader = StringField("PJSM Leader", validators=[Optional(), Length(max=128)])

    # ── Issued / Closed signatures ────────────────────────────────────────────
    issued_auth_name = StringField("Authorizer Name", validators=[Optional(), Length(max=128)])
    issued_wl_name   = StringField("Work Lead Name",  validators=[Optional(), Length(max=128)])
    closed_auth_name = StringField("Authorizer Name", validators=[Optional(), Length(max=128)])
    closed_wl_name   = StringField("Work Lead Name",  validators=[Optional(), Length(max=128)])


def _bool(val):
    return 1 if val else 0


def _available_lotos():
    """Return all LOTO permits that have an assigned number, for datalist autocomplete."""
    return get_db().execute(
        "SELECT id, loto_number, status FROM loto_permits WHERE loto_number IS NOT NULL ORDER BY loto_number"
    ).fetchall()


def _linked_lotos(swp_id):
    """Return all LOTO permits that reference this SWP via FK."""
    return get_db().execute(
        "SELECT id, loto_number, status FROM loto_permits WHERE swp_id = ? ORDER BY created_at",
        (swp_id,),
    ).fetchall()


def _staff_and_contractors():
    """Return (staff_names, contractor_choices) for work_lead select."""
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

    staff_names = []
    if current_name:
        staff_names.append(current_name)
    staff_names.extend(others)

    contractors = db.execute(
        "SELECT name, company FROM contractors WHERE active=1 ORDER BY name"
    ).fetchall()
    contractor_choices = []
    for c in contractors:
        label = f"{c['name']} ({c['company']})" if c["company"] else c["name"]
        contractor_choices.append((c["name"], label))

    return staff_names, contractor_choices


def _generate_permit_number(plant):
    """Generate next sequential permit number for the given plant and current year."""
    db = get_db()
    year = datetime.now().strftime("%y")
    row = db.execute(
        """SELECT COUNT(*) FROM swp_permits
           WHERE plant = ?
             AND status IN ('issued', 'closed')
             AND strftime('%y', approved_at) = ?""",
        (plant, year),
    ).fetchone()
    seq = (row[0] if row else 0) + 1
    return f"{plant}-SWP-{year}-{seq:04d}"


def _save_swp(form, swp_id=None, target_status="draft"):
    """Insert or update an SWP record from form data. Returns swp_id."""
    db = get_db()

    # WTForms BooleanField sends "y" when checked via HTMX
    # form.field.data is already True/False because WTForms processes it
    is_backdated = _bool(form.is_backdated.data)

    # Historical records are always closed
    status = target_status
    if is_backdated and target_status == "issued":
        status = "closed"

    equip_id_raw = request.form.get("equip_id", "").strip()
    equip_id = int(equip_id_raw) if equip_id_raw.isdigit() else None
    if equip_id:
        if not db.execute("SELECT id FROM equipment WHERE id = ?", (equip_id,)).fetchone():
            equip_id = None

    fields = dict(
        plant=form.plant.data or None,
        permit_number=form.permit_number.data or None,
        date=form.date.data or None,
        time=form.time.data or None,
        location=form.location.data or None,
        company=form.company.data or None,
        work_lead=form.work_lead.data or None,
        scope_of_work=form.scope_of_work.data or None,
        permit_loto=form.permit_loto.data or None,
        permit_hot_work=form.permit_hot_work.data or None,
        permit_electrical=form.permit_electrical.data or None,
        permit_confined_sp=form.permit_confined_sp.data or None,
        permit_excavation=form.permit_excavation.data or None,
        permit_other=form.permit_other.data or None,
        first_time=form.first_time.data or None,
        training=form.training.data or None,
        inspection=form.inspection.data or None,
        continuation=form.continuation.data or None,
        he_electrical=_bool(form.he_electrical.data),
        he_mechanical=_bool(form.he_mechanical.data),
        he_thermal=_bool(form.he_thermal.data),
        he_pressure=_bool(form.he_pressure.data),
        he_motion=_bool(form.he_motion.data),
        he_gravity=_bool(form.he_gravity.data),
        he_chemical=_bool(form.he_chemical.data),
        he_radiation=_bool(form.he_radiation.data),
        he_biological=_bool(form.he_biological.data),
        he_noise=_bool(form.he_noise.data),
        ppe_hard_hat=_bool(form.ppe_hard_hat.data),
        ppe_safety_glasses=_bool(form.ppe_safety_glasses.data),
        ppe_gloves=_bool(form.ppe_gloves.data),
        ppe_fr_clothing=_bool(form.ppe_fr_clothing.data),
        ppe_arc_flash_suit=_bool(form.ppe_arc_flash_suit.data),
        ppe_arc_flash_hood=_bool(form.ppe_arc_flash_hood.data),
        ppe_insul_gloves=_bool(form.ppe_insul_gloves.data),
        ppe_face_shield=_bool(form.ppe_face_shield.data),
        ppe_hearing_prot=_bool(form.ppe_hearing_prot.data),
        ppe_respirator=_bool(form.ppe_respirator.data),
        ppe_safety_shoes=_bool(form.ppe_safety_shoes.data),
        ppe_fall_protect=_bool(form.ppe_fall_protect.data),
        ppe_cut_protect=_bool(form.ppe_cut_protect.data),
        ppe_welding_hood=_bool(form.ppe_welding_hood.data),
        ppe_brine_gear=_bool(form.ppe_brine_gear.data),
        ppe_chem_res_cloth=_bool(form.ppe_chem_res_cloth.data),
        ppe_chem_res_gloves=_bool(form.ppe_chem_res_gloves.data),
        ppe_chem_res_boot=_bool(form.ppe_chem_res_boot.data),
        ppe_other1=_bool(form.ppe_other1.data),
        ppe_other1_txt=form.ppe_other1_txt.data or None,
        ppe_other2=_bool(form.ppe_other2.data),
        ppe_other2_txt=form.ppe_other2_txt.data or None,
        tha_loto=_bool(form.tha_loto.data),
        tha_confined_space=_bool(form.tha_confined_space.data),
        tha_elevated_work=_bool(form.tha_elevated_work.data),
        tha_hot_work=_bool(form.tha_hot_work.data),
        tha_vehicles=_bool(form.tha_vehicles.data),
        tha_mobile_equip=_bool(form.tha_mobile_equip.data),
        tha_heavy_equip=_bool(form.tha_heavy_equip.data),
        tha_power_tools=_bool(form.tha_power_tools.data),
        tha_slips_trips=_bool(form.tha_slips_trips.data),
        tha_nip_pinch=_bool(form.tha_nip_pinch.data),
        tha_break_sys_integ=_bool(form.tha_break_sys_integ.data),
        tha_ergonomics=_bool(form.tha_ergonomics.data),
        tha_extreme_heat=_bool(form.tha_extreme_heat.data),
        tha_extreme_cold=_bool(form.tha_extreme_cold.data),
        tha_high_wind=_bool(form.tha_high_wind.data),
        tha_rain_snow=_bool(form.tha_rain_snow.data),
        tha_other=_bool(form.tha_other.data),
        tha_other_txt=form.tha_other_txt.data or None,
        ts_review_procedures=_bool(form.ts_review_procedures.data),
        ts_valve_positions=_bool(form.ts_valve_positions.data),
        ts_cross_connects=_bool(form.ts_cross_connects.data),
        ts_barricade=_bool(form.ts_barricade.data),
        ts_adjacent_work=_bool(form.ts_adjacent_work.data),
        ts_haz_comm=_bool(form.ts_haz_comm.data),
        ts_addl_permits=_bool(form.ts_addl_permits.data),
        ts_equip_inspect=_bool(form.ts_equip_inspect.data),
        ts_high_temp_lines=_bool(form.ts_high_temp_lines.data),
        ts_min_approach=_bool(form.ts_min_approach.data),
        ts_safety_observer=_bool(form.ts_safety_observer.data),
        ts_slings_lifting=_bool(form.ts_slings_lifting.data),
        ts_other1=_bool(form.ts_other1.data),
        ts_other1_txt=form.ts_other1_txt.data or None,
        ts_other2=_bool(form.ts_other2.data),
        ts_other2_txt=form.ts_other2_txt.data or None,
        hazard_mitigation=form.hazard_mitigation.data or None,
        additional_comments=form.additional_comments.data or None,
        is_backdated=is_backdated,
        backdate_note=form.backdate_note.data or None,
        pjsm_leader=form.pjsm_leader.data or None,
        issued_auth_name=form.issued_auth_name.data or None,
        issued_wl_name=form.issued_wl_name.data or None,
        closed_auth_name=form.closed_auth_name.data or None,
        closed_wl_name=form.closed_wl_name.data or None,
        status=status,
        equip_id=equip_id,
    )

    if swp_id is None:
        cols = ", ".join(list(fields.keys()) + ["created_by"])
        placeholders = ", ".join(["?"] * (len(fields) + 1))
        db.execute(
            f"INSERT INTO swp_permits ({cols}) VALUES ({placeholders})",
            list(fields.values()) + [current_user.id],
        )
        swp_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    else:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE swp_permits SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            list(fields.values()) + [swp_id],
        )
        db.execute("DELETE FROM swp_attendees WHERE swp_id = ?", (swp_id,))

    # Upsert equipment work log
    equip_comments = request.form.get("equip_comments", "").strip() or None
    equip_start    = request.form.get("equip_start_date", "").strip() or None
    equip_end      = request.form.get("equip_end_date", "").strip() or None
    db.execute("DELETE FROM equipment_work_log WHERE permit_type='swp' AND permit_id=?", (swp_id,))
    if equip_id:
        db.execute(
            """INSERT INTO equipment_work_log
                   (equipment_id, permit_type, permit_id, comments, start_date, end_date)
               VALUES (?, 'swp', ?, ?, ?, ?)""",
            (equip_id, swp_id, equip_comments, equip_start, equip_end),
        )

    # Save attendees (no signature field)
    idx = 0
    while idx < 24:
        name = request.form.get(f"attend_name_{idx}", "").strip()
        company = request.form.get(f"attend_company_{idx}", "").strip()
        att_date = request.form.get(f"attend_date_{idx}", "").strip()
        if any([name, company]):
            db.execute(
                "INSERT INTO swp_attendees (swp_id, position, name, company, date) VALUES (?,?,?,?,?)",
                (swp_id, idx, name, company, att_date),
            )
        idx += 1

    db.commit()
    return swp_id


# ── Routes ────────────────────────────────────────────────────────────────────

@swp_bp.route("/")
@login_required
def index():
    db = get_db()
    uid = current_user.id
    is_admin = current_user.role in (ADMIN, IT_ADMIN)

    # Purge stale drafts owned by current user (older than 48 hours)
    db.execute(
        """DELETE FROM swp_permits
           WHERE status = 'draft'
             AND created_by = ?
             AND created_at < datetime('now', '-48 hours')""",
        (uid,),
    )
    db.commit()

    is_operator = current_user.role == OPERATOR
    is_field    = current_user.role in (MECHANIC, EICC)

    show = request.args.get("show", "active")
    valid_shows = ("drafts", "pending", "pending_operator", "active", "closed", "all")
    if show not in valid_shows:
        show = "active"

    base = """SELECT s.id, s.permit_number, s.plant, s.date, s.location, s.work_lead,
                     s.status, s.is_backdated, s.created_at, s.created_by,
                     u.first_name, u.last_name,
                     lp.id AS loto_id, lp.loto_number
              FROM swp_permits s
              LEFT JOIN users u ON u.id = s.created_by
              LEFT JOIN loto_permits lp ON lp.id = (
                  SELECT id FROM loto_permits WHERE swp_id = s.id ORDER BY created_at LIMIT 1
              )"""

    if show == "drafts":
        query = base + " WHERE s.status = 'draft' AND s.created_by = ?"
        params = (uid,)
    elif show == "pending_operator":
        if is_admin or is_operator:
            query = base + " WHERE s.status = 'pending_operator'"
            params = ()
        else:
            query = base + " WHERE s.status = 'pending_operator' AND s.created_by = ?"
            params = (uid,)
    elif show == "pending":
        if is_admin:
            query = base + " WHERE s.status = 'pending'"
            params = ()
        else:
            query = base + " WHERE s.status = 'pending' AND s.created_by = ?"
            params = (uid,)
    elif show == "active":
        if is_field:
            query = base + " WHERE s.status = 'issued' AND s.created_by = ?"
            params = (uid,)
        else:
            query = base + " WHERE s.status = 'issued'"
            params = ()
    elif show == "closed":
        if is_field:
            query = base + " WHERE s.status = 'closed' AND s.created_by = ?"
            params = (uid,)
        else:
            query = base + " WHERE s.status = 'closed'"
            params = ()
    else:  # all
        if is_admin or is_operator:
            query = base
            params = ()
        elif is_field:
            # Field roles see own permits + management-approved
            query = base + " WHERE s.created_by = ? OR s.status IN ('issued','closed')"
            params = (uid,)
        else:
            query = base + " WHERE s.status != 'draft' OR s.created_by = ?"
            params = (uid,)

    query += " ORDER BY s.date DESC, s.created_at DESC"
    permits = db.execute(query, params).fetchall()
    return render_template("swp/index.html", permits=permits, show=show,
                           is_admin=is_admin, is_operator=is_operator,
                           is_field=is_field, uid=uid)


@swp_bp.route("/new", methods=["GET"])
@login_required
def new():
    form = SWPForm()
    form.date.data = date.today().strftime("%Y-%m-%d")
    form.time.data = datetime.now().strftime("%H:%M")
    form.company.data = current_app.config["COMPANY_NAME"]
    if current_user.profile_complete():
        name = f"{current_user.first_name} {current_user.last_name}".strip()
        if not name:
            name = current_user.username
        form.work_lead.data = name

    # Pre-fill from a linked LOTO permit
    from_loto = request.args.get("from_loto")
    if from_loto and from_loto.isdigit():
        loto_row = get_db().execute(
            "SELECT id, loto_number, plant, location FROM loto_permits WHERE id = ?",
            (int(from_loto),),
        ).fetchone()
        if loto_row:
            form.tha_loto.data    = True
            form.permit_loto.data = loto_row["loto_number"] or ""
            form.plant.data       = loto_row["plant"] or ""
            if loto_row["location"] and not form.location.data:
                form.location.data = loto_row["location"]
    else:
        from_loto = None

    staff_names, contractor_choices = _staff_and_contractors()
    return render_template("swp/form.html", form=form, swp=None,
                           attendees_by_pos={}, attendee_count=4,
                           staff_names=staff_names,
                           contractor_choices=contractor_choices,
                           from_loto=from_loto,
                           available_lotos=_available_lotos(),
                           linked_lotos=[],
                           cur_equip=None,
                           work_log=None)


@swp_bp.route("/<int:swp_id>/edit", methods=["GET"])
@login_required
def edit(swp_id):
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if not swp:
        flash("SWP not found.", "error")
        return redirect(url_for("swp.index"))

    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    is_creator = swp["created_by"] == current_user.id

    if not is_creator and not is_admin:
        abort(403)

    # Issued/closed permits are read-only
    read_only = swp["status"] in ("issued", "closed")

    attendees = db.execute(
        "SELECT * FROM swp_attendees WHERE swp_id = ? ORDER BY position", (swp_id,)
    ).fetchall()

    cur_equip = None
    work_log = None
    if swp["equip_id"]:
        cur_equip = db.execute(
            "SELECT id, plant, tag_id, process_name, make, model FROM equipment WHERE id = ?",
            (swp["equip_id"],),
        ).fetchone()
        work_log = db.execute(
            "SELECT comments, start_date, end_date FROM equipment_work_log WHERE permit_type='swp' AND permit_id=?",
            (swp_id,),
        ).fetchone()

    form = SWPForm(data=dict(swp))
    attendees_by_pos = {a["position"]: a for a in attendees}
    attendee_count = max(4, len(attendees) + 1)

    staff_names, contractor_choices = _staff_and_contractors()
    return render_template("swp/form.html", form=form, swp=swp,
                           attendees_by_pos=attendees_by_pos,
                           attendee_count=attendee_count,
                           staff_names=staff_names,
                           contractor_choices=contractor_choices,
                           read_only=read_only,
                           from_loto=None,
                           available_lotos=_available_lotos(),
                           linked_lotos=_linked_lotos(swp_id),
                           cur_equip=cur_equip,
                           work_log=work_log)


@swp_bp.route("/autosave", methods=["POST"])
@login_required
def autosave():
    form = SWPForm()
    # Do NOT call form.validate() — read data directly
    swp_id_raw   = request.form.get("swp_id", "").strip()
    from_loto_raw = request.form.get("from_loto", "").strip()

    if swp_id_raw.isdigit():
        swp_id = int(swp_id_raw)
        db = get_db()
        swp = db.execute(
            "SELECT * FROM swp_permits WHERE id = ? AND created_by = ? AND status = 'draft'",
            (swp_id, current_user.id),
        ).fetchone()
        if not swp:
            abort(403)
        saved_id = _save_swp(form, swp_id=swp_id, target_status="draft")
    else:
        saved_id = _save_swp(form, target_status="draft")

    # Back-link: if SWP was started from a LOTO, wire up loto_permits.swp_id
    if from_loto_raw.isdigit():
        db = get_db()
        db.execute(
            "UPDATE loto_permits SET swp_id = ? WHERE id = ? AND swp_id IS NULL",
            (saved_id, int(from_loto_raw)),
        )
        db.commit()

    response = make_response(
        render_template("swp/_save_oob.html", swp_id=saved_id,
                        is_new_save=not swp_id_raw.isdigit())
    )
    response.headers["HX-Push-Url"] = url_for("swp.edit", swp_id=saved_id)
    return response


@swp_bp.route("/<int:swp_id>/review", methods=["GET"])
@login_required
def review(swp_id):
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if not swp:
        flash("SWP not found.", "error")
        return redirect(url_for("swp.index"))

    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    is_creator = swp["created_by"] == current_user.id

    # Access control: creator can view own drafts/pending; admins can view pending
    if not is_creator and not is_admin:
        abort(403)

    attendees = db.execute(
        "SELECT * FROM swp_attendees WHERE swp_id = ? ORDER BY position", (swp_id,)
    ).fetchall()

    # Resolve linked LOTOs: FK-linked first, then try resolving permit_loto text
    linked_lotos = _linked_lotos(swp_id)
    linked_ids = {l["id"] for l in linked_lotos}
    if swp["permit_loto"]:
        text_match = db.execute(
            "SELECT id, loto_number, status FROM loto_permits WHERE loto_number = ?",
            (swp["permit_loto"],),
        ).fetchone()
        if text_match and text_match["id"] not in linked_ids:
            linked_lotos = list(linked_lotos) + [text_match]

    sig_requests = db.execute(
        """SELECT s.id, s.signature_role, s.status, s.signature_data, s.completed_at,
                  COALESCE(u.first_name || ' ' || u.last_name, '') AS recipient_name,
                  t.token
           FROM signature_requests s
           LEFT JOIN users u ON u.id = s.requested_from
           LEFT JOIN signing_tokens t ON t.sig_request_id = s.id AND t.used_at IS NULL
                                      AND t.expires_at > DATETIME('now')
           WHERE s.permit_type='swp' AND s.permit_id=?
           ORDER BY s.requested_at""",
        (swp_id,),
    ).fetchall()

    return render_template("swp/review.html", swp=swp, attendees=attendees,
                           is_admin=is_admin, is_creator=is_creator,
                           linked_lotos=linked_lotos, sig_requests=sig_requests)


@swp_bp.route("/<int:swp_id>/submit", methods=["POST"])
@login_required
def submit(swp_id):
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if not swp:
        flash("SWP not found.", "error")
        return redirect(url_for("swp.index"))

    is_creator = swp["created_by"] == current_user.id
    if not is_creator:
        abort(403)
    if swp["status"] != "draft":
        flash("Only draft SWPs can be submitted.", "error")
        return redirect(url_for("swp.index"))

    is_admin    = current_user.role in (ADMIN, IT_ADMIN)
    is_field    = current_user.role in (MECHANIC, EICC)

    if is_admin:
        plant = swp["plant"] or "XX"
        pn = _generate_permit_number(plant)
        final_status = "closed" if swp["is_backdated"] else "issued"
        db.execute(
            """UPDATE swp_permits
               SET status=?, permit_number=?, approved_by=?, approved_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (final_status, pn, current_user.id, swp_id),
        )
        db.commit()
        log(SWP_UPDATED, user_id=current_user.id, username=current_user.username,
            resource="swp", resource_id=swp_id,
            detail={"action": "submit_issued", "permit_number": pn})

        for field, role in [("work_lead", "swp_work_lead"), ("pjsm_leader", "pjsm_leader")]:
            uid = find_user_by_name(swp[field])
            if uid:
                create_signature_request("swp", swp_id, role, uid)

        flash(f"SWP {pn} issued.", "success")
    elif is_field:
        # EI&C / Mechanic → goes to operators first
        db.execute(
            """UPDATE swp_permits
               SET status='pending_operator', submitted_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (swp_id,),
        )
        db.commit()
        log(SWP_SUBMITTED, user_id=current_user.id, username=current_user.username,
            resource="swp", resource_id=swp_id,
            detail={"action": "submitted_to_operator"})
        flash("Submitted for operator review.", "info")
    else:
        # Operator → goes directly to management
        db.execute(
            """UPDATE swp_permits
               SET status='pending', submitted_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (swp_id,),
        )
        db.commit()
        log(SWP_SUBMITTED, user_id=current_user.id, username=current_user.username,
            resource="swp", resource_id=swp_id)
        pn = swp["permit_number"] or f"SWP #{swp_id}"
        notify_admins(f"SWP {pn} submitted for approval by {current_user.display_name()}.")
        flash("Submitted for management approval.", "info")

    return redirect(url_for("swp.index"))


@swp_bp.route("/<int:swp_id>/approve", methods=["POST"])
@login_required
def approve(swp_id):
    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    if not is_admin:
        abort(403)

    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if not swp:
        flash("SWP not found.", "error")
        return redirect(url_for("swp.index"))
    if swp["status"] != "pending":
        flash("Only pending SWPs can be approved.", "error")
        return redirect(url_for("swp.index"))

    plant = swp["plant"] or "XX"
    pn = _generate_permit_number(plant)
    final_status = "closed" if swp["is_backdated"] else "issued"

    approver = db.execute(
        "SELECT first_name, last_name FROM users WHERE id=?", (current_user.id,)
    ).fetchone()
    approver_name = f"{approver['first_name'] or ''} {approver['last_name'] or ''}".strip() or current_user.username

    db.execute(
        """UPDATE swp_permits
           SET status=?, permit_number=?, approved_by=?, approved_at=CURRENT_TIMESTAMP,
               issued_auth_name=COALESCE(issued_auth_name, ?),
               updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (final_status, pn, current_user.id, approver_name, swp_id),
    )
    db.commit()
    log(SWP_APPROVED, user_id=current_user.id, username=current_user.username,
        resource="swp", resource_id=swp_id,
        detail={"permit_number": pn})

    for field, role in [("work_lead", "swp_work_lead"), ("pjsm_leader", "pjsm_leader")]:
        name = swp[field]
        uid = find_user_by_name(name)
        if uid:
            create_signature_request("swp", swp_id, role, uid)

    notify_user(swp["created_by"], f"Your SWP {pn} has been approved and issued.")
    flash(f"SWP {pn} approved and issued.", "success")
    return redirect(url_for("swp.index", show="active"))


@swp_bp.route("/<int:swp_id>/forward", methods=["POST"])
@login_required
def forward(swp_id):
    """Operator forwards a pending_operator SWP to management queue."""
    if current_user.role not in (OPERATOR, ADMIN, IT_ADMIN):
        abort(403)
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if not swp:
        flash("SWP not found.", "error")
        return redirect(url_for("swp.index"))
    if swp["status"] != "pending_operator":
        flash("Only operator-pending SWPs can be forwarded.", "error")
        return redirect(url_for("swp.index"))
    db.execute(
        """UPDATE swp_permits
           SET status='pending', updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (swp_id,),
    )
    db.commit()
    log(SWP_FORWARDED, user_id=current_user.id, username=current_user.username,
        resource="swp", resource_id=swp_id)
    pn = swp["permit_number"] or f"SWP #{swp_id}"
    notify_admins(f"SWP {pn} forwarded for management approval by {current_user.display_name()}.")
    flash("SWP forwarded to management for approval.", "success")
    return redirect(url_for("swp.index", show="pending"))


@swp_bp.route("/<int:swp_id>/recall", methods=["POST"])
@login_required
def recall(swp_id):
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if not swp:
        flash("SWP not found.", "error")
        return redirect(url_for("swp.index"))

    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    is_creator = swp["created_by"] == current_user.id

    if not is_creator and not is_admin:
        abort(403)
    if swp["status"] not in ("pending", "pending_operator"):
        flash("Only pending SWPs can be recalled.", "error")
        return redirect(url_for("swp.index"))

    db.execute(
        """UPDATE swp_permits
           SET status='draft', submitted_at=NULL, updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (swp_id,),
    )
    db.commit()
    log(SWP_RECALLED, user_id=current_user.id, username=current_user.username,
        resource="swp", resource_id=swp_id)
    pn = swp["permit_number"] or f"SWP #{swp_id}"
    notify_user(swp["created_by"], f"SWP {pn} has been recalled to draft.")
    flash("SWP recalled to draft.", "info")
    return redirect(url_for("swp.edit", swp_id=swp_id))


@swp_bp.route("/attendee-row")
@login_required
def attendee_row():
    idx = int(request.args.get("idx", 0))
    return render_template("swp/_attendee_row.html", idx=idx)


@swp_bp.route("/other-row")
@login_required
def other_row():
    section = request.args.get("section", "ppe")
    prefix = "ppe_other2" if section == "ppe" else "ts_other2"
    checked = request.args.get("checked") == "1"
    txt_value = request.args.get("txt", "")
    return render_template("swp/_other_row.html", prefix=prefix, section=section, checked=checked, txt_value=txt_value)
