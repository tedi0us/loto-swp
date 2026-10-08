"""SWP-02 (PJSM continuation) and SWP-05 (work continuation) routes."""
from flask import Blueprint, abort, make_response, render_template, request, url_for
from flask_login import current_user, login_required

from app.db import get_db

swp_cont_bp = Blueprint("swp_cont", __name__)


def _get_swp(swp_id):
    """Return SWP row or 404. Abort 403 if status is closed."""
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id=?", (swp_id,)).fetchone()
    if not swp:
        abort(404)
    if swp["status"] == "closed":
        abort(403)
    return swp


def _get_cont(cont_id, swp_id):
    """Return PJSM continuation row, aborting 403 if swp_id doesn't match."""
    row = get_db().execute(
        "SELECT * FROM swp_pjsm_continuations WHERE id=?", (cont_id,)
    ).fetchone()
    if not row or row["swp_id"] != swp_id:
        abort(403)
    return row


# ── SWP-02: PJSM Continuation Sheet ───────────────────────────────────────

@swp_cont_bp.route("/swp/<int:swp_id>/continuations/pjsm/new")
@login_required
def pjsm_new(swp_id):
    swp = _get_swp(swp_id)
    return render_template("swp_cont/pjsm_form.html", swp=swp, cont=None, attendees=[])


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/pjsm/<int:cont_id>")
@login_required
def pjsm_edit(swp_id, cont_id):
    swp = _get_swp(swp_id)
    cont = _get_cont(cont_id, swp_id)
    attendees = get_db().execute(
        "SELECT * FROM swp_pjsm_cont_attendees WHERE continuation_id=? ORDER BY position",
        (cont_id,),
    ).fetchall()
    return render_template("swp_cont/pjsm_form.html", swp=swp, cont=cont, attendees=attendees)


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/pjsm/autosave", methods=["POST"])
@login_required
def pjsm_autosave(swp_id):
    swp = _get_swp(swp_id)
    db = get_db()

    cont_id_raw = request.form.get("cont_id", "").strip()
    is_new = not cont_id_raw.isdigit()

    fields = dict(
        host_employee=request.form.get("host_employee", "").strip() or None,
        location=request.form.get("location", "").strip() or None,
        work_lead=request.form.get("work_lead", "").strip() or None,
        work_lead_sig=request.form.get("work_lead_sig", "").strip() or None,
        date_time=request.form.get("date_time", "").strip() or None,
        additional_comments=request.form.get("additional_comments", "").strip() or None,
        further_continuation_req=1 if "further_continuation_req" in request.form else 0,
    )

    if is_new:
        cols = ", ".join(list(fields.keys()) + ["swp_id", "created_by"])
        placeholders = ", ".join(["?"] * (len(fields) + 2))
        db.execute(
            f"INSERT INTO swp_pjsm_continuations ({cols}) VALUES ({placeholders})",
            list(fields.values()) + [swp_id, current_user.id],
        )
        cont_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    else:
        cont_id = int(cont_id_raw)
        _get_cont(cont_id, swp_id)
        sets = ", ".join(f"{k}=?" for k in fields)
        db.execute(
            f"UPDATE swp_pjsm_continuations SET {sets} WHERE id=?",
            list(fields.values()) + [cont_id],
        )
        db.execute("DELETE FROM swp_pjsm_cont_attendees WHERE continuation_id=?", (cont_id,))

    # Re-insert attendees
    idx = 0
    while idx < 20:
        name = request.form.get(f"att_name_{idx}", "").strip()
        sig  = request.form.get(f"att_sig_{idx}",  "").strip()
        comp = request.form.get(f"att_company_{idx}", "").strip()
        att_date = request.form.get(f"att_date_{idx}", "").strip()
        if any([name, sig, comp]):
            db.execute(
                "INSERT INTO swp_pjsm_cont_attendees"
                " (continuation_id, position, name, signature, company, date)"
                " VALUES (?,?,?,?,?,?)",
                (cont_id, idx, name or None, sig or None, comp or None, att_date or None),
            )
        idx += 1

    db.commit()

    response = make_response(
        render_template("swp_cont/_pjsm_save_oob.html", swp_id=swp_id, cont_id=cont_id)
    )
    if is_new:
        response.headers["HX-Push-Url"] = url_for(
            "swp_cont.pjsm_edit", swp_id=swp_id, cont_id=cont_id
        )
    return response


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/pjsm/<int:cont_id>/attendee-row")
@login_required
def pjsm_attendee_row(swp_id, cont_id):
    _get_swp(swp_id)
    idx = request.args.get("idx", "0")
    idx = int(idx) if idx.isdigit() else 0
    return render_template("swp_cont/_pjsm_attendee_row.html", idx=idx)


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/pjsm/list")
@login_required
def pjsm_list(swp_id):
    _get_swp(swp_id)
    rows = get_db().execute(
        "SELECT * FROM swp_pjsm_continuations WHERE swp_id=? ORDER BY created_at",
        (swp_id,),
    ).fetchall()
    return render_template("swp_cont/_pjsm_cont_list.html", swp_id=swp_id, continuations=rows)


# ── SWP-05: Work Continuation Addendum ────────────────────────────────────

def _get_work_cont(cont_id, swp_id):
    row = get_db().execute(
        "SELECT * FROM swp_work_continuations WHERE id=?", (cont_id,)
    ).fetchone()
    if not row or row["swp_id"] != swp_id:
        abort(403)
    return row


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/work/new")
@login_required
def work_new(swp_id):
    swp = _get_swp(swp_id)
    return render_template("swp_cont/work_form.html", swp=swp, cont=None, entries=[])


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/work/<int:cont_id>")
@login_required
def work_edit(swp_id, cont_id):
    swp = _get_swp(swp_id)
    cont = _get_work_cont(cont_id, swp_id)
    entries = get_db().execute(
        "SELECT * FROM swp_work_cont_entries WHERE continuation_id=? ORDER BY position",
        (cont_id,),
    ).fetchall()
    return render_template("swp_cont/work_form.html", swp=swp, cont=cont, entries=entries)


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/work/autosave", methods=["POST"])
@login_required
def work_autosave(swp_id):
    _get_swp(swp_id)
    db = get_db()

    cont_id_raw = request.form.get("cont_id", "").strip()
    is_new = not cont_id_raw.isdigit()

    if is_new:
        db.execute(
            "INSERT INTO swp_work_continuations (swp_id, created_by) VALUES (?,?)",
            (swp_id, current_user.id),
        )
        cont_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    else:
        cont_id = int(cont_id_raw)
        _get_work_cont(cont_id, swp_id)
        db.execute("DELETE FROM swp_work_cont_entries WHERE continuation_id=?", (cont_id,))

    # Re-insert entries (stop/restart pairs by position)
    idx = 0
    while idx < 20:
        entry_type = request.form.get(f"entry_type_{idx}", "").strip()
        if entry_type not in ("stop", "restart"):
            idx += 1
            continue
        db.execute(
            """INSERT INTO swp_work_cont_entries
               (continuation_id, position, entry_type, date, time,
                work_lead_phone, work_lead_name, work_lead_sig,
                authorizer_name, authorizer_sig, job_status_comments)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cont_id, idx, entry_type,
                request.form.get(f"date_{idx}", "").strip() or None,
                request.form.get(f"time_{idx}", "").strip() or None,
                request.form.get(f"wl_phone_{idx}", "").strip() or None,
                request.form.get(f"wl_name_{idx}", "").strip() or None,
                request.form.get(f"wl_sig_{idx}", "").strip() or None,
                request.form.get(f"auth_name_{idx}", "").strip() or None,
                request.form.get(f"auth_sig_{idx}", "").strip() or None,
                request.form.get(f"comments_{idx}", "").strip() or None,
            ),
        )
        idx += 1

    db.commit()

    response = make_response(
        render_template("swp_cont/_work_save_oob.html", swp_id=swp_id, cont_id=cont_id)
    )
    if is_new:
        response.headers["HX-Push-Url"] = url_for(
            "swp_cont.work_edit", swp_id=swp_id, cont_id=cont_id
        )
    return response


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/work/<int:cont_id>/cycle-row")
@login_required
def work_cycle_row(swp_id, cont_id):
    _get_swp(swp_id)
    idx = request.args.get("idx", "0")
    idx = int(idx) if idx.isdigit() else 0
    return render_template("swp_cont/_work_cycle_row.html", idx=idx)


@swp_cont_bp.route("/swp/<int:swp_id>/continuations/work/list")
@login_required
def work_list(swp_id):
    _get_swp(swp_id)
    rows = get_db().execute(
        "SELECT * FROM swp_work_continuations WHERE swp_id=? ORDER BY created_at",
        (swp_id,),
    ).fetchall()
    return render_template("swp_cont/_work_cont_list.html", swp_id=swp_id, continuations=rows)
