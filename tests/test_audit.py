"""Tests for audit log — log() insertion, /logs filters, CSV export."""
from tests.conftest import make_user, login, raw_db


def _seed_log(app, action="LOGIN", username="alice"):
    """Insert an audit_log row directly for filter tests."""
    conn = raw_db(app)
    conn.execute(
        "INSERT INTO audit_log (action, username, success) VALUES (?,?,1)",
        (action, username),
    )
    conn.commit()
    conn.close()


# ── log() helper ──────────────────────────────────────────────────────────────

def test_log_inserts_row(app, client):
    make_user(app, role="operator")
    login(client)
    # Triggering login itself writes a LOGIN row
    conn = raw_db(app)
    count = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='LOGIN'"
    ).fetchone()[0]
    conn.close()
    assert count >= 1


def test_log_records_failed_login(app, client):
    make_user(app)
    client.post("/login", data={"username": "testuser", "password": "wrong"})
    conn = raw_db(app)
    count = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='LOGIN_FAILED'"
    ).fetchone()[0]
    conn.close()
    assert count >= 1


# ── Role guard on /logs ───────────────────────────────────────────────────────

def test_admin_blocked_from_logs(app, client):
    make_user(app, role="admin")
    login(client)
    assert client.get("/logs").status_code == 403


def test_operator_blocked_from_logs(app, client):
    make_user(app, role="operator")
    login(client)
    assert client.get("/logs").status_code == 403


def test_it_admin_can_access_logs(app, client):
    make_user(app, role="it_admin")
    login(client)
    assert client.get("/logs").status_code == 200


# ── Filters ───────────────────────────────────────────────────────────────────

def test_action_filter_shows_only_matching(app, client):
    make_user(app, role="it_admin")
    login(client)
    # Use unique usernames so we can tell which row is which
    _seed_log(app, action="EQUIPMENT_CREATED", username="alice_equip")
    _seed_log(app, action="SWP_CREATED",       username="bob_swp")
    rv = client.get("/logs?action=EQUIPMENT_CREATED")
    assert rv.status_code == 200
    # The filtered row for alice_equip should appear; bob_swp should not
    assert b"alice_equip" in rv.data
    assert b"bob_swp" not in rv.data


def test_username_filter_narrows_results(app, client):
    make_user(app, role="it_admin")
    login(client)
    _seed_log(app, username="alice_special")
    _seed_log(app, username="bob_other")
    rv = client.get("/logs?username=alice_special")
    assert rv.status_code == 200
    assert b"alice_special" in rv.data
    assert b"bob_other" not in rv.data


# ── CSV export ────────────────────────────────────────────────────────────────

def test_csv_export_content_type(app, client):
    make_user(app, role="it_admin")
    login(client)
    rv = client.get("/logs/export")
    assert rv.status_code == 200
    assert "text/csv" in rv.content_type


def test_csv_export_has_header_row(app, client):
    make_user(app, role="it_admin")
    login(client)
    rv = client.get("/logs/export")
    first_line = rv.data.decode().splitlines()[0]
    assert "timestamp" in first_line
    assert "action" in first_line


def test_csv_export_action_filter(app, client):
    make_user(app, role="it_admin")
    login(client)
    _seed_log(app, action="LOTO_ACTIVATED", username="charlie")
    rv = client.get("/logs/export?action=LOTO_ACTIVATED")
    text = rv.data.decode()
    assert "LOTO_ACTIVATED" in text
