"""Tests for the combined SWP+LOTO blueprint — create, autosave, edit guards."""
from tests.conftest import make_user, login, raw_db


def _last_ids(app):
    """Return (swp_id, loto_id) of the most recently created pair."""
    conn = raw_db(app)
    swp  = conn.execute("SELECT id FROM swp_permits  ORDER BY id DESC LIMIT 1").fetchone()
    loto = conn.execute("SELECT id FROM loto_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return (swp[0] if swp else None), (loto[0] if loto else None)


def _autosave(client, extra=None):
    data = {"plant": "NV", "date": "2026-05-07", **(extra or {})}
    return client.post("/combined/autosave", data=data)


# ── GET /combined/new ─────────────────────────────────────────────────────────

def test_combined_new_loads(app, client):
    make_user(app, role="operator")
    login(client)
    assert client.get("/combined/new").status_code == 200


def test_mechanic_can_access_combined_new(app, client):
    # Mechanic is view-only for LOTO/SWP individual forms but combined has no guard
    make_user(app, role="mechanic")
    login(client)
    assert client.get("/combined/new").status_code == 200


# ── Autosave creates linked pair ──────────────────────────────────────────────

def test_autosave_creates_swp_and_loto(app, client):
    make_user(app, role="operator")
    login(client)
    rv = _autosave(client)
    assert rv.status_code == 200
    swp_id, loto_id = _last_ids(app)
    assert swp_id  is not None
    assert loto_id is not None


def test_autosave_links_loto_to_swp(app, client):
    make_user(app, role="operator")
    login(client)
    _autosave(client)
    swp_id, loto_id = _last_ids(app)
    conn = raw_db(app)
    row = conn.execute("SELECT swp_id FROM loto_permits WHERE id=?", (loto_id,)).fetchone()
    conn.close()
    assert row["swp_id"] == swp_id


def test_autosave_pushes_url_header(app, client):
    make_user(app, role="operator")
    login(client)
    rv = _autosave(client)
    assert "HX-Push-Url" in rv.headers
    assert "combined" in rv.headers["HX-Push-Url"]


def test_autosave_updates_existing_pair(app, client):
    make_user(app, role="operator")
    login(client)
    _autosave(client)
    swp_id, loto_id = _last_ids(app)
    rv = _autosave(client, extra={"swp_id": swp_id, "loto_id": loto_id, "plant": "SE"})
    assert rv.status_code == 200
    conn = raw_db(app)
    row = conn.execute("SELECT plant FROM swp_permits WHERE id=?", (swp_id,)).fetchone()
    conn.close()
    assert row["plant"] == "SE"


# ── Edit route ────────────────────────────────────────────────────────────────

def test_edit_loads_for_creator(app, client):
    make_user(app, role="operator")
    login(client)
    _autosave(client)
    swp_id, loto_id = _last_ids(app)
    rv = client.get(f"/combined/{swp_id}-{loto_id}/edit")
    assert rv.status_code == 200


def test_non_creator_blocked_from_edit(app, client):
    make_user(app, role="operator", username="owner")
    make_user(app, role="operator", username="other")

    login(client, username="owner")
    _autosave(client)
    swp_id, loto_id = _last_ids(app)
    client.post("/logout")

    login(client, username="other")
    rv = client.get(f"/combined/{swp_id}-{loto_id}/edit")
    assert rv.status_code == 403


def test_admin_can_edit_any_pair(app, client):
    make_user(app, role="operator", username="creator")
    make_user(app, role="admin",    username="adm")

    login(client, username="creator")
    _autosave(client)
    swp_id, loto_id = _last_ids(app)
    client.post("/logout")

    login(client, username="adm")
    rv = client.get(f"/combined/{swp_id}-{loto_id}/edit")
    assert rv.status_code == 200
