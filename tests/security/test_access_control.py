"""Security: authentication gates, role escalation, onboarding redirects."""
import pytest

from tests.conftest import login, make_user, TEST_PW


# ── Unauthenticated redirect ───────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/dashboard",
    "/swp/",
    "/swp/new",
    "/loto/",
    "/loto/new",
    "/users/",
    "/logs",
    "/contractors/",
    "/equipment/picker",
    "/signatures/",
])
def test_unauthed_redirect(client, path):
    r = client.get(path)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


# ── Role escalation ────────────────────────────────────────────────────────────

def test_operator_cannot_access_user_management(app, client):
    make_user(app, role="operator")
    login(client)
    r = client.get("/users/")
    assert r.status_code == 403


def test_operator_cannot_access_audit_log(app, client):
    make_user(app, role="operator")
    login(client)
    r = client.get("/logs")
    assert r.status_code == 403


def test_mechanic_cannot_approve_swp(app, client):
    uid = make_user(app, role="mechanic", username="mech1")
    login(client, username="mech1")
    r = client.post("/swp/1/approve")
    assert r.status_code in (403, 404)


def test_eicc_cannot_activate_loto(app, client):
    uid = make_user(app, role="ei_c", username="eicc1")
    login(client, username="eicc1")
    r = client.post("/loto/1/activate")
    assert r.status_code in (403, 404)


def test_non_admin_cannot_create_user(app, client):
    make_user(app, role="operator")
    login(client)
    r = client.get("/users/new")
    assert r.status_code == 403


def test_admin_can_approve_swp_path(app, client):
    """Admin reaching approve endpoint is NOT blocked with 403 (no SWP → redirect, not forbidden)."""
    make_user(app, role="admin")
    login(client)
    r = client.post("/swp/99999/approve")
    assert r.status_code != 403, "Admin should not get 403 on approve endpoint"


def test_it_admin_can_access_audit_log(app, client):
    make_user(app, role="it_admin")
    login(client)
    r = client.get("/logs")
    assert r.status_code == 200


# ── Onboarding gates ───────────────────────────────────────────────────────────

def test_must_change_password_gate(app, client):
    """User with must_change_password=1 is redirected to /change-password on any route."""
    make_user(app, role="operator", must_change_password=1)
    login(client)
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302
    assert "change-password" in r.headers["Location"]


def test_must_change_password_exempt_endpoints(app, client):
    """The change-password page itself is not redirected."""
    make_user(app, role="operator", must_change_password=1)
    login(client)
    r = client.get("/change-password", follow_redirects=False)
    assert r.status_code == 200


def test_incomplete_profile_gate(app, client):
    """User with missing profile fields is redirected to /complete-profile."""
    import sqlite3
    make_user(app, role="operator", first_name=None, last_name=None,
              email=None, phone=None, title=None)
    login(client)
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302
    assert "complete-profile" in r.headers["Location"]
