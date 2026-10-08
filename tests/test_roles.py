"""Tests for role guards, require_role decorator, and User model helpers."""
from tests.conftest import make_user, login


# ── require_role decorator ────────────────────────────────────────────────────

def test_it_admin_only_route_blocks_admin(app, client):
    make_user(app, role="admin", username="adm")
    login(client, username="adm")
    rv = client.get("/users/")
    assert rv.status_code == 403


def test_it_admin_only_route_allows_it_admin(app, client):
    make_user(app, role="it_admin", username="itadm")
    login(client, username="itadm")
    rv = client.get("/users/")
    assert rv.status_code == 200


def test_admin_route_allows_admin(app, client):
    make_user(app, role="admin", username="adm")
    login(client, username="adm")
    rv = client.get("/contractors/")
    assert rv.status_code == 200


def test_admin_route_blocks_mechanic(app, client):
    """Mechanics cannot access the contractor directory."""
    make_user(app, role="mechanic", username="mech2")
    login(client, username="mech2")
    rv = client.get("/contractors/")
    assert rv.status_code == 403


def test_operator_can_access_contractors(app, client):
    make_user(app, role="operator", username="op")
    login(client, username="op")
    rv = client.get("/contractors/")
    assert rv.status_code == 200


# ── LOTO view-only role guards ────────────────────────────────────────────────

def test_mechanic_cannot_get_new_loto(app, client):
    make_user(app, role="mechanic", username="mech")
    login(client, username="mech")
    rv = client.get("/loto/new")
    assert rv.status_code == 403


def test_ei_c_cannot_get_new_loto(app, client):
    make_user(app, role="ei_c", username="eic")
    login(client, username="eic")
    rv = client.get("/loto/new")
    assert rv.status_code == 403


def test_mechanic_cannot_post_loto_autosave(app, client):
    make_user(app, role="mechanic", username="mech2")
    login(client, username="mech2")
    rv = client.post("/loto/autosave", data={"plant": "NV"})
    assert rv.status_code == 403


def test_operator_can_get_new_loto(app, client):
    make_user(app, role="operator", username="op")
    login(client, username="op")
    rv = client.get("/loto/new")
    assert rv.status_code == 200


# ── User model helpers ────────────────────────────────────────────────────────

def test_is_it_admin_true_for_it_admin(app, client):
    make_user(app, role="it_admin", username="ita")
    login(client, username="ita")
    with client.application.test_request_context():
        from flask_login import current_user
    # Test via the User class directly
    from app.auth import User
    u = User(1, "x", "it_admin")
    assert u.is_it_admin() is True
    assert u.is_admin_or_above() is True


def test_is_admin_or_above_true_for_admin():
    from app.auth import User
    assert User(1, "x", "admin").is_admin_or_above() is True
    assert User(1, "x", "operator").is_admin_or_above() is False
    assert User(1, "x", "mechanic").is_admin_or_above() is False


def test_is_it_admin_false_for_others():
    from app.auth import User
    for role in ("admin", "operator", "mechanic", "ei_c", "user"):
        assert User(1, "x", role).is_it_admin() is False
