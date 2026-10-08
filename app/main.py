import csv
import io
from datetime import date

from flask import Blueprint, Response, render_template, request
from flask_login import login_required

from app.audit import ALL_ACTIONS
from app.db import get_db
from app.roles import IT_ADMIN, require_role

main_bp = Blueprint("main", __name__)

PAGE_SIZE = 50


@main_bp.route("/")
@main_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("main/dashboard.html")


@main_bp.route("/privacy")
def privacy():
    return render_template("main/privacy.html")


def _log_query_parts(action, username, date_from, date_to):
    """Return (where_clauses, params) for log filters."""
    clauses, params = [], []
    if action:
        clauses.append("l.action = ?")
        params.append(action)
    if username:
        clauses.append("l.username LIKE ?")
        params.append(f"%{username}%")
    if date_from:
        clauses.append("DATE(l.timestamp) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("DATE(l.timestamp) <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@main_bp.route("/logs")
@login_required
@require_role(IT_ADMIN)
def logs():
    db = get_db()

    # January 1st retention purge: delete Jan–Nov of the previous year
    if date.today().strftime("%m") == "01":
        db.execute(
            "DELETE FROM audit_log WHERE timestamp < date('now', 'start of year', '-1 month')"
        )
        db.commit()

    action    = request.args.get("action", "").strip()
    username  = request.args.get("username", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to", "").strip()
    page      = max(1, request.args.get("page", 1, type=int))
    offset    = (page - 1) * PAGE_SIZE

    where, params = _log_query_parts(action, username, date_from, date_to)

    total = db.execute(f"SELECT COUNT(*) FROM audit_log l {where}", params).fetchone()[0]
    entries = db.execute(
        f"""SELECT l.id, l.timestamp, l.username, l.ip_address,
                   l.user_agent, l.action, l.resource, l.resource_id,
                   l.detail, l.success
            FROM audit_log l
            {where}
            ORDER BY l.timestamp DESC
            LIMIT ? OFFSET ?""",
        params + [PAGE_SIZE, offset],
    ).fetchall()

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template(
        "main/logs.html",
        entries=entries,
        page=page,
        pages=pages,
        total=total,
        all_actions=ALL_ACTIONS,
        action=action,
        username=username,
        date_from=date_from,
        date_to=date_to,
    )


@main_bp.route("/logs/export")
@login_required
@require_role(IT_ADMIN)
def logs_export():
    db = get_db()

    action    = request.args.get("action", "").strip()
    username  = request.args.get("username", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to", "").strip()

    where, params = _log_query_parts(action, username, date_from, date_to)

    rows = db.execute(
        f"""SELECT l.timestamp, l.username, l.ip_address, l.action,
                   l.resource, l.resource_id, l.detail, l.success, l.user_agent
            FROM audit_log l
            {where}
            ORDER BY l.timestamp DESC""",
        params,
    ).fetchall()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["timestamp", "username", "ip_address", "action",
                     "resource", "resource_id", "detail", "success", "user_agent"])
    for r in rows:
        writer.writerow([
            r["timestamp"], r["username"] or "", r["ip_address"] or "",
            r["action"], r["resource"] or "", r["resource_id"] or "",
            r["detail"] or "", "1" if r["success"] else "0",
            r["user_agent"] or "",
        ])

    filename = f"audit_log_{date.today().isoformat()}.csv"
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
