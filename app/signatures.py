import secrets
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from app.db import get_db

signatures_bp = Blueprint("signatures", __name__, url_prefix="/signatures")

TOKEN_TTL_HOURS = 72


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_user_by_name(name):
    """Match a free-text full name to a user row. Returns user id or None."""
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    db = get_db()
    # Try "First Last" match against first_name + last_name
    row = db.execute(
        "SELECT id FROM users WHERE LOWER(TRIM(first_name || ' ' || last_name)) = LOWER(?)",
        (name,),
    ).fetchone()
    if row:
        return row["id"]
    # Fall back to username
    row = db.execute(
        "SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (name,)
    ).fetchone()
    return row["id"] if row else None


def _generate_token(sig_request_id):
    """Create a signing token for the given signature request and return the token string."""
    db = get_db()
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)
    db.execute(
        """INSERT INTO signing_tokens (token, sig_request_id, expires_at)
           VALUES (?, ?, ?)""",
        (token, sig_request_id, expires.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return token


def create_signature_request(permit_type, permit_id, role, user_id, physical_only=False):
    """Insert a signature request (and signing token) if none is pending already."""
    db = get_db()
    existing = db.execute(
        """SELECT id FROM signature_requests
           WHERE permit_type=? AND permit_id=? AND signature_role=?
             AND requested_from=? AND status='pending'""",
        (permit_type, permit_id, role, user_id),
    ).fetchone()
    if not existing:
        db.execute(
            """INSERT INTO signature_requests
                   (permit_type, permit_id, signature_role, requested_from, physical_only)
               VALUES (?,?,?,?,?)""",
            (permit_type, permit_id, role, user_id, 1 if physical_only else 0),
        )
        sig_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        token = _generate_token(sig_id)
        db.commit()

        if not physical_only:
            _notify_signature_request(permit_type, permit_id, role, user_id, token)


def _notify_signature_request(permit_type, permit_id, role, user_id, token):
    """Send an SMS to the requestee with a direct signing link."""
    from app.notifications import notify_user
    db = get_db()
    if permit_type == "swp":
        row = db.execute("SELECT permit_number FROM swp_permits WHERE id=?", (permit_id,)).fetchone()
        pn = row["permit_number"] if row and row["permit_number"] else f"SWP #{permit_id}"
    else:
        row = db.execute("SELECT loto_number FROM loto_permits WHERE id=?", (permit_id,)).fetchone()
        pn = row["loto_number"] if row and row["loto_number"] else f"LOTO #{permit_id}"
    role_label = role.replace("_", " ").title()
    sign_url = url_for("signatures.sign_via_token", token=token, _external=True)
    body = f"Signature needed — {permit_type.upper()} {pn} ({role_label}). Sign: {sign_url}"
    notify_user(user_id, body)


def pending_count_for(user_id):
    """Return the count of pending signature requests for a given user."""
    return get_db().execute(
        "SELECT COUNT(*) FROM signature_requests WHERE requested_from=? AND status='pending'",
        (user_id,),
    ).fetchone()[0]


# ── Routes ────────────────────────────────────────────────────────────────────

@signatures_bp.route("/")
@login_required
def index():
    db = get_db()
    pending = db.execute(
        """SELECT s.id, s.permit_type, s.permit_id, s.signature_role,
                  s.physical_only, s.requested_at, s.status,
                  COALESCE(sw.permit_number, lo.loto_number) AS permit_number,
                  t.token
           FROM signature_requests s
           LEFT JOIN swp_permits  sw ON sw.id = s.permit_id AND s.permit_type = 'swp'
           LEFT JOIN loto_permits lo ON lo.id = s.permit_id AND s.permit_type = 'loto'
           LEFT JOIN signing_tokens t ON t.sig_request_id = s.id AND t.used_at IS NULL
                                      AND t.expires_at > DATETIME('now')
           WHERE s.requested_from = ? AND s.status = 'pending'
           ORDER BY s.requested_at DESC""",
        (current_user.id,),
    ).fetchall()

    recent = db.execute(
        """SELECT s.id, s.permit_type, s.permit_id, s.signature_role,
                  s.physical_only, s.requested_at, s.completed_at,
                  s.acknowledged_at, s.status,
                  COALESCE(sw.permit_number, lo.loto_number) AS permit_number
           FROM signature_requests s
           LEFT JOIN swp_permits  sw ON sw.id = s.permit_id AND s.permit_type = 'swp'
           LEFT JOIN loto_permits lo ON lo.id = s.permit_id AND s.permit_type = 'loto'
           WHERE s.requested_from = ?
             AND s.status != 'pending'
             AND COALESCE(s.completed_at, s.acknowledged_at) >= DATE('now', '-30 days')
           ORDER BY COALESCE(s.completed_at, s.acknowledged_at) DESC
           LIMIT 20""",
        (current_user.id,),
    ).fetchall()

    return render_template("signatures/index.html", pending=pending, recent=recent)


@signatures_bp.route("/<int:sig_id>")
@login_required
def view(sig_id):
    db = get_db()
    sig = db.execute(
        "SELECT * FROM signature_requests WHERE id=?", (sig_id,)
    ).fetchone()
    if not sig:
        abort(404)
    if sig["requested_from"] != current_user.id and not current_user.is_admin_or_above():
        abort(403)

    permit = None
    if sig["permit_type"] == "swp":
        permit = db.execute(
            "SELECT id, permit_number, plant, date, location, work_lead, scope_of_work, status"
            " FROM swp_permits WHERE id=?", (sig["permit_id"],)
        ).fetchone()
    else:
        permit = db.execute(
            "SELECT id, loto_number, plant, date, location, job_description, group_leader, status"
            " FROM loto_permits WHERE id=?", (sig["permit_id"],)
        ).fetchone()

    return render_template("signatures/view.html", sig=sig, permit=permit)


@signatures_bp.route("/<int:sig_id>/sign", methods=["POST"])
@login_required
def sign(sig_id):
    db = get_db()
    sig = db.execute("SELECT * FROM signature_requests WHERE id=?", (sig_id,)).fetchone()
    if not sig:
        abort(404)
    if sig["requested_from"] != current_user.id:
        abort(403)
    if sig["status"] != "pending":
        flash("This signature request has already been completed.", "error")
        return redirect(url_for("signatures.index"))

    signature_data = request.form.get("signature_data", "").strip()
    if not signature_data:
        flash("Please type your full name to sign.", "error")
        return redirect(url_for("signatures.view", sig_id=sig_id))

    db.execute(
        """UPDATE signature_requests
           SET status='signed', signature_data=?, completed_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (signature_data, sig_id),
    )
    db.commit()
    flash("Signature recorded.", "success")
    return redirect(url_for("signatures.index"))


@signatures_bp.route("/sign/<token>", methods=["GET", "POST"])
def sign_via_token(token):
    """Public signing page — no login required. Accessed via a shared link."""
    db = get_db()
    row = db.execute(
        """SELECT t.id AS token_id, t.sig_request_id, t.expires_at, t.used_at,
                  s.id AS sig_id, s.permit_type, s.permit_id,
                  s.signature_role, s.status AS sig_status, s.physical_only,
                  COALESCE(u.first_name || ' ' || u.last_name, '') AS recipient_name
           FROM signing_tokens t
           JOIN signature_requests s ON s.id = t.sig_request_id
           LEFT JOIN users u ON u.id = s.requested_from
           WHERE t.token = ?""",
        (token,),
    ).fetchone()

    if not row:
        return render_template("signatures/token_invalid.html",
                               reason="Link not found."), 404

    if row["used_at"]:
        return render_template("signatures/token_invalid.html",
                               reason="This link has already been used."), 410

    if row["expires_at"] < datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        return render_template("signatures/token_invalid.html",
                               reason="This link has expired."), 410

    if row["sig_status"] != "pending":
        return render_template("signatures/token_invalid.html",
                               reason="This signature request has already been completed."), 410

    permit = None
    if row["permit_type"] == "swp":
        permit = db.execute(
            "SELECT id, permit_number, plant, date, location, work_lead, scope_of_work, status"
            " FROM swp_permits WHERE id=?", (row["permit_id"],)
        ).fetchone()
    else:
        permit = db.execute(
            "SELECT id, loto_number, plant, date, location, job_description, group_leader, status"
            " FROM loto_permits WHERE id=?", (row["permit_id"],)
        ).fetchone()

    if request.method == "POST":
        signature_data = request.form.get("signature_data", "").strip()
        if not signature_data:
            flash("Please type your full name to sign.", "error")
            return render_template("signatures/token_sign.html",
                                   row=row, permit=permit, token=token)

        db.execute(
            """UPDATE signature_requests
               SET status='signed', signature_data=?, completed_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (signature_data, row["sig_id"]),
        )
        db.execute(
            "UPDATE signing_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["token_id"],),
        )
        db.commit()
        return render_template("signatures/token_done.html",
                               permit=permit, row=row, signature_data=signature_data)

    return render_template("signatures/token_sign.html",
                           row=row, permit=permit, token=token)


@signatures_bp.route("/<int:sig_id>/acknowledge", methods=["POST"])
@login_required
def acknowledge(sig_id):
    db = get_db()
    sig = db.execute("SELECT * FROM signature_requests WHERE id=?", (sig_id,)).fetchone()
    if not sig:
        abort(404)
    if sig["requested_from"] != current_user.id:
        abort(403)
    if not sig["physical_only"]:
        abort(400)
    if sig["status"] != "pending":
        flash("This request has already been completed.", "error")
        return redirect(url_for("signatures.index"))

    db.execute(
        """UPDATE signature_requests
           SET status='acknowledged', acknowledged_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (sig_id,),
    )
    db.commit()
    flash("Physical step acknowledged.", "success")
    return redirect(url_for("signatures.index"))
