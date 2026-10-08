"""Security: SQL injection, XSS, and mass-assignment checks."""
import sqlite3

import pytest

from tests.conftest import login, make_user, TEST_PW


SQL_PAYLOADS = [
    "'; DROP TABLE swp_permits; --",
    "' OR '1'='1",
    "1 UNION SELECT username, password_hash FROM users--",
    "'; UPDATE users SET role='it_admin' WHERE '1'='1'; --",
]

XSS_PAYLOAD = "<script>alert('xss')</script>"


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sqli_scope_of_work(app, client, payload):
    """SQL injection payloads in scope_of_work must not destroy the DB."""
    make_user(app, role="operator")
    login(client)
    r = client.post("/swp/autosave", data={"plant": "NV", "scope_of_work": payload})
    assert r.status_code in (200, 400)
    # Table must still exist and be queryable
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    count = conn.execute("SELECT COUNT(*) FROM swp_permits").fetchone()[0]
    conn.close()
    assert count >= 0


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sqli_loto_job_description(app, client, payload):
    """SQL injection in LOTO job_description must not destroy the DB."""
    make_user(app, role="operator")
    login(client)
    r = client.post("/loto/autosave", data={"plant": "NV", "job_description": payload})
    assert r.status_code in (200, 400)
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    count = conn.execute("SELECT COUNT(*) FROM loto_permits").fetchone()[0]
    conn.close()
    assert count >= 0


def test_xss_escaped_in_swp_review(app, client):
    """XSS payload saved in scope_of_work must be HTML-escaped in the review page."""
    uid = make_user(app, role="admin")
    login(client)
    # Save a SWP with an XSS payload
    r = client.post("/swp/autosave", data={
        "plant": "NV", "scope_of_work": XSS_PAYLOAD
    })
    assert r.status_code == 200
    # Get the SWP ID from the DB
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    row = conn.execute("SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if row is None:
        return  # no permit saved — skip
    swp_id = row[0]
    r = client.get(f"/swp/{swp_id}/review")
    html = r.data.decode()
    # Raw <script> tag must not appear unescaped
    assert "<script>alert(" not in html
    # The escaped form should be present (Jinja2 auto-escapes)
    assert "&lt;script&gt;" in html or "alert" not in html


def test_xss_escaped_in_loto_form(app, client):
    """XSS payload in LOTO job_description must be escaped in the edit page."""
    make_user(app, role="operator")
    login(client)
    r = client.post("/loto/autosave", data={
        "plant": "NV", "job_description": XSS_PAYLOAD
    })
    assert r.status_code == 200
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    row = conn.execute("SELECT id FROM loto_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if row is None:
        return
    loto_id = row[0]
    r = client.get(f"/loto/{loto_id}/edit")
    html = r.data.decode()
    assert "<script>alert(" not in html


def test_mass_assign_role_via_swp_autosave(app, client):
    """Autosave must not allow role escalation via form data."""
    make_user(app, role="operator")
    login(client)
    client.post("/swp/autosave", data={"plant": "NV", "role": "it_admin"})
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    role = conn.execute(
        "SELECT role FROM users WHERE username='testuser'"
    ).fetchone()[0]
    conn.close()
    assert role == "operator"


def test_mass_assign_approved_by_via_swp_autosave(app, client):
    """Operator cannot forge approved_by by submitting it in autosave data."""
    uid = make_user(app, role="operator")
    login(client)
    client.post("/swp/autosave", data={"plant": "NV", "approved_by": "1"})
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    row = conn.execute(
        "SELECT approved_by FROM swp_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        assert row[0] is None, "approved_by must not be settable via autosave"
