from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, make_response, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from app.audit import LOTO_CREATED, LOTO_UPDATED, SWP_CREATED, SWP_UPDATED, log
from app.db import get_db
from app.loto import BOUNDARY_COUNT, VERIFIER_DEFAULT, LotoForm, _save_loto
from app.roles import ADMIN, IT_ADMIN
from app.swp import SWPForm, _available_lotos, _save_swp, _staff_and_contractors

combined_bp = Blueprint("combined", __name__, url_prefix="/combined")


@combined_bp.route("/new", methods=["GET"])
@login_required
def new():
    swp_form = SWPForm()
    loto_form = LotoForm()
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    swp_form.date.data = today
    swp_form.time.data = now
    loto_form.date.data = today
    swp_form.company.data = current_app.config["COMPANY_NAME"]
    swp_form.tha_loto.data = True

    if current_user.first_name or current_user.last_name:
        name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()
    else:
        name = current_user.username
    swp_form.work_lead.data = name
    loto_form.group_leader.data = name

    staff_names, contractor_choices = _staff_and_contractors()
    return render_template(
        "combined/form.html",
        swp_form=swp_form,
        loto_form=loto_form,
        swp=None,
        loto=None,
        attendees_by_pos={},
        attendee_count=4,
        boundaries_by_pos={},
        verifier_count=VERIFIER_DEFAULT,
        verifiers_by_pos={},
        staff_names=staff_names,
        contractor_choices=contractor_choices,
        available_lotos=_available_lotos(),
        cur_equip=None,
        work_log=None,
    )


@combined_bp.route("/<int:swp_id>-<int:loto_id>/edit", methods=["GET"])
@login_required
def edit(swp_id, loto_id):
    db = get_db()
    swp  = db.execute("SELECT * FROM swp_permits  WHERE id = ?", (swp_id,)).fetchone()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if not swp or not loto:
        flash("Permit pair not found.", "error")
        return redirect(url_for("main.dashboard"))

    is_admin   = current_user.role in (ADMIN, IT_ADMIN)
    is_creator = swp["created_by"] == current_user.id or loto["created_by"] == current_user.id
    if not is_creator and not is_admin:
        abort(403)

    if swp["status"] != "draft" or loto["status"] not in ("draft", "released"):
        flash("This permit pair is no longer in draft. Use the individual forms to continue.", "info")
        return redirect(url_for("swp.edit", swp_id=swp_id))

    attendees  = db.execute(
        "SELECT * FROM swp_attendees  WHERE swp_id  = ? ORDER BY position", (swp_id,)
    ).fetchall()
    boundaries = db.execute(
        "SELECT * FROM loto_boundaries WHERE loto_id = ? ORDER BY position", (loto_id,)
    ).fetchall()
    verifiers  = db.execute(
        "SELECT * FROM loto_verifiers  WHERE loto_id = ? ORDER BY position", (loto_id,)
    ).fetchall()

    cur_equip = None
    work_log  = None
    if swp["equip_id"]:
        cur_equip = db.execute(
            "SELECT id, plant, tag_id, process_name, make, model FROM equipment WHERE id = ?",
            (swp["equip_id"],),
        ).fetchone()
        work_log = db.execute(
            "SELECT comments, start_date, end_date FROM equipment_work_log"
            " WHERE permit_type='swp' AND permit_id=?",
            (swp_id,),
        ).fetchone()

    swp_form  = SWPForm(data=dict(swp))
    loto_form = LotoForm(data=dict(loto))

    attendees_by_pos  = {a["position"]: a for a in attendees}
    attendee_count    = max(4, len(attendees) + 1)
    boundaries_by_pos = {b["position"]: b for b in boundaries}
    verifiers_by_pos  = {v["position"]: v for v in verifiers}
    verifier_count    = max(VERIFIER_DEFAULT, len(verifiers) + 1)

    staff_names, contractor_choices = _staff_and_contractors()
    return render_template(
        "combined/form.html",
        swp_form=swp_form,
        loto_form=loto_form,
        swp=swp,
        loto=loto,
        attendees_by_pos=attendees_by_pos,
        attendee_count=attendee_count,
        boundaries_by_pos=boundaries_by_pos,
        verifier_count=verifier_count,
        verifiers_by_pos=verifiers_by_pos,
        staff_names=staff_names,
        contractor_choices=contractor_choices,
        available_lotos=_available_lotos(),
        cur_equip=cur_equip,
        work_log=work_log,
    )


@combined_bp.route("/autosave", methods=["POST"])
@login_required
def autosave():
    swp_form  = SWPForm()
    loto_form = LotoForm()

    swp_id_raw  = request.form.get("swp_id",  "").strip()
    loto_id_raw = request.form.get("loto_id", "").strip()

    db = get_db()

    swp_id = None
    if swp_id_raw.isdigit():
        swp_id = int(swp_id_raw)
        if not db.execute(
            "SELECT id FROM swp_permits WHERE id = ? AND created_by = ? AND status = 'draft'",
            (swp_id, current_user.id),
        ).fetchone():
            abort(403)

    loto_id = None
    if loto_id_raw.isdigit():
        loto_id = int(loto_id_raw)
        loto_row = db.execute(
            "SELECT id, status FROM loto_permits WHERE id = ? AND created_by = ?",
            (loto_id, current_user.id),
        ).fetchone()
        if not loto_row or loto_row["status"] not in ("draft", "released"):
            abort(403)

    # Mirror shared fields that have different names on each form
    loto_form.group_leader.data  = request.form.get("work_lead", "").strip() or loto_form.group_leader.data
    loto_form.job_description.data = request.form.get("scope_of_work", "").strip() or loto_form.job_description.data
    loto_form.is_historical.data = swp_form.is_backdated.data

    is_new_swp  = swp_id is None
    is_new_loto = loto_id is None

    saved_swp_id         = _save_swp(swp_form, swp_id=swp_id, target_status="draft")
    saved_loto_id, _     = _save_loto(loto_form, loto_id=loto_id, from_swp=saved_swp_id)

    log(
        SWP_CREATED if is_new_swp else SWP_UPDATED,
        user_id=current_user.id, username=current_user.username,
        resource="swp_permits", resource_id=saved_swp_id,
        detail={"source": "combined"},
    )
    log(
        LOTO_CREATED if is_new_loto else LOTO_UPDATED,
        user_id=current_user.id, username=current_user.username,
        resource="loto_permits", resource_id=saved_loto_id,
        detail={"source": "combined"},
    )

    resp = make_response(
        render_template("combined/_save_oob.html",
                        swp_id=saved_swp_id, loto_id=saved_loto_id)
    )
    resp.headers["HX-Push-Url"] = url_for(
        "combined.edit", swp_id=saved_swp_id, loto_id=saved_loto_id
    )
    return resp
