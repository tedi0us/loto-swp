"""Tests for the LOTO blueprint — create, submit, activate, release, role guards."""
from tests.conftest import make_user, login, raw_db

# ── Realistic full-permit data ─────────────────────────────────────────────────

FULL_LOTO_DATA = {
    "plant":                "NV",
    "date":                 "2026-06-01",
    "location":             "Turbine Hall B — Pump Room",
    "equipment_id":         "FP-102",
    "equipment_description": "Feed Pump 102 — horizontal centrifugal, 480VAC motor",
    "job_description":      "Replace mechanical seal on FP-102. Drain brine loop before breaking containment.",
    "group_leader":         "Jane Smith",
    # Boundary row 1 — electrical isolation
    "b_equipment_1":         "FP-102 Motor",
    "b_location_1":          "MCC-4 Bucket 12",
    "b_energy_type_1":       "Electrical",
    "b_isolation_device_1":  "Breaker 12",
    "b_device_position_1":   "Open / Locked",
    # Boundary row 2 — process isolation
    "b_equipment_2":         "FP-102 Suction Valve",
    "b_location_2":          "Pump Room Floor",
    "b_energy_type_2":       "Pressure / Fluid",
    "b_isolation_device_2":  "Gate Valve GV-204",
    "b_device_position_2":   "Closed / Chained",
    # Verifiers
    "v_name_0": "Bob Technician",
    "v_date_0": "2026-06-01",
    "v_name_1": "Carol Inspector",
    "v_date_1": "2026-06-01",
}


def _last_loto(app):
    conn = raw_db(app)
    row = conn.execute(
        "SELECT id, status, loto_number FROM loto_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _create_loto(client, app, extra=None):
    data = {"plant": "NV", "date": "2026-05-07", **(extra or {})}
    client.post("/loto/autosave", data=data)
    return _last_loto(app)["id"]


# ── Role guards ───────────────────────────────────────────────────────────────

def test_mechanic_cannot_get_new_loto(app, client):
    make_user(app, role="mechanic")
    login(client)
    assert client.get("/loto/new").status_code == 403


def test_ei_c_cannot_get_new_loto(app, client):
    make_user(app, role="ei_c")
    login(client)
    assert client.get("/loto/new").status_code == 403


def test_mechanic_cannot_autosave(app, client):
    make_user(app, role="mechanic")
    login(client)
    assert client.post("/loto/autosave", data={"plant": "NV"}).status_code == 403


def test_operator_can_get_new_loto(app, client):
    make_user(app, role="operator")
    login(client)
    assert client.get("/loto/new").status_code == 200


# ── Autosave / create ─────────────────────────────────────────────────────────

def test_autosave_creates_draft(app, client):
    make_user(app, role="operator")
    login(client)
    rv = client.post("/loto/autosave", data={"plant": "NV", "date": "2026-05-07"})
    assert rv.status_code == 200
    loto = _last_loto(app)
    assert loto["status"] == "draft"


def test_autosave_assigns_loto_number(app, client):
    make_user(app, role="operator")
    login(client)
    _create_loto(client, app)
    loto = _last_loto(app)
    assert loto["loto_number"] and loto["loto_number"].startswith("NV-LOTO-")


def test_autosave_defers_loto_number_when_requested(app, client):
    make_user(app, role="operator")
    login(client)
    client.post("/loto/autosave", data={
        "plant": "NV", "date": "2026-05-07", "number_deferred": "y",
    })
    loto = _last_loto(app)
    assert loto["loto_number"] is None


def test_unchecking_deferred_assigns_number_on_next_save(app, client):
    make_user(app, role="operator")
    login(client)
    loto_id = _create_loto(client, app, extra={"number_deferred": "y"})
    assert _last_loto(app)["loto_number"] is None
    client.post("/loto/autosave", data={"loto_id": loto_id, "plant": "NV"})
    loto = _last_loto(app)
    assert loto["loto_number"] and loto["loto_number"].startswith("NV-LOTO-")


def test_activate_assigns_deferred_number(app, client):
    make_user(app, role="admin")
    login(client)
    swp_id = _linked_swp_id(app, client)
    loto_id = _create_loto(client, app, extra={"from_swp": swp_id, "number_deferred": "y"})
    client.post("/loto/autosave", data={
        "loto_id": loto_id, "plant": "NV", "from_swp": swp_id, "number_deferred": "y",
    })
    assert _last_loto(app)["loto_number"] is None

    rv = client.post(f"/loto/{loto_id}/activate", follow_redirects=False)
    assert rv.status_code == 302
    loto = _last_loto(app)
    assert loto["status"] == "active"
    assert loto["loto_number"] and loto["loto_number"].startswith("NV-LOTO-")


def test_activate_blocks_when_no_plant_and_number_deferred(app, client):
    make_user(app, role="admin")
    login(client)
    swp_id = _linked_swp_id(app, client)
    # Create with no plant at all — deferred by nature (plant is required for a number anyway)
    client.post("/loto/autosave", data={"date": "2026-05-07", "from_swp": swp_id})
    loto_id = _last_loto(app)["id"]
    rv = client.post(f"/loto/{loto_id}/activate", follow_redirects=False)
    assert rv.status_code == 302
    assert _last_loto(app)["status"] == "draft"


def test_autosave_pushes_url_header(app, client):
    make_user(app, role="operator")
    login(client)
    rv = client.post("/loto/autosave", data={"plant": "NV"})
    assert "HX-Push-Url" in rv.headers


# ── Submit (operator → pending) ───────────────────────────────────────────────

def test_operator_submit_sets_pending(app, client):
    make_user(app, role="operator")
    login(client)
    loto_id = _create_loto(client, app)
    rv = client.post(f"/loto/{loto_id}/submit", follow_redirects=False)
    assert rv.status_code == 302
    assert _last_loto(app)["status"] == "pending"


def test_admin_cannot_submit_loto(app, client):
    # Admins skip the pending step — they activate directly
    make_user(app, role="admin")
    login(client)
    loto_id = _create_loto(client, app)
    rv = client.post(f"/loto/{loto_id}/submit")
    assert rv.status_code == 403


# ── Activate (admin only) ─────────────────────────────────────────────────────

def _linked_swp_id(app, client):
    """Create a minimal SWP as admin and return its id."""
    rv = client.post("/swp/autosave", data={"plant": "NV", "date": "2026-05-07"})
    conn = raw_db(app)
    row = conn.execute("SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row[0]


def test_admin_activates_draft_loto(app, client):
    make_user(app, role="admin")
    login(client)
    # Create a SWP first so activate doesn't block on missing SWP
    swp_id = _linked_swp_id(app, client)
    loto_id = _create_loto(client, app, extra={"from_swp": swp_id})
    # Manually link via autosave with from_swp
    client.post("/loto/autosave", data={"loto_id": loto_id, "plant": "NV", "from_swp": swp_id})
    rv = client.post(f"/loto/{loto_id}/activate", follow_redirects=False)
    assert rv.status_code == 302
    conn = raw_db(app)
    row = conn.execute("SELECT status FROM loto_permits WHERE id=?", (loto_id,)).fetchone()
    conn.close()
    assert row["status"] == "active"


def test_operator_cannot_activate(app, client):
    make_user(app, role="operator")
    login(client)
    loto_id = _create_loto(client, app)
    rv = client.post(f"/loto/{loto_id}/activate")
    assert rv.status_code == 403


def test_historical_loto_is_released_on_create(app, client):
    make_user(app, role="admin")
    login(client)
    loto_id = _create_loto(client, app, extra={"is_historical": "y"})
    conn = raw_db(app)
    row = conn.execute("SELECT status FROM loto_permits WHERE id=?", (loto_id,)).fetchone()
    conn.close()
    # Historical LOTOs skip the active stage and are stored directly as released
    assert row["status"] == "released"


# ── Release (admin only) ──────────────────────────────────────────────────────

def test_admin_releases_active_loto(app, client):
    make_user(app, role="admin")
    login(client)
    loto_id = _create_loto(client, app, extra={"is_historical": "y"})
    client.post(f"/loto/{loto_id}/activate")
    rv = client.post(f"/loto/{loto_id}/release", follow_redirects=False)
    assert rv.status_code == 302
    conn = raw_db(app)
    row = conn.execute("SELECT status FROM loto_permits WHERE id=?", (loto_id,)).fetchone()
    conn.close()
    assert row["status"] == "released"


def test_operator_cannot_release(app, client):
    make_user(app, role="operator")
    login(client)
    loto_id = _create_loto(client, app)
    rv = client.post(f"/loto/{loto_id}/release")
    assert rv.status_code == 403


# ── Index / tabs ──────────────────────────────────────────────────────────────

def test_loto_index_loads(app, client):
    make_user(app, role="operator")
    login(client)
    assert client.get("/loto/").status_code == 200


def test_view_only_index_has_no_new_button(app, client):
    make_user(app, role="mechanic")
    login(client)
    rv = client.get("/loto/")
    assert rv.status_code == 200
    assert b"+ New LOTO" not in rv.data


# ── Full realistic LOTO creation ───────────────────────────────────────────────

def test_full_loto_saves_core_fields(app, client):
    """Autosave a full LOTO permit and verify core fields persisted."""
    make_user(app, role="operator")
    login(client)

    rv = client.post("/loto/autosave", data=FULL_LOTO_DATA)
    assert rv.status_code == 200

    conn = raw_db(app)
    row = conn.execute("SELECT * FROM loto_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    assert row["plant"]                == "NV"
    assert row["location"]             == "Turbine Hall B — Pump Room"
    assert row["equipment_id"]         == "FP-102"
    assert row["equipment_description"].startswith("Feed Pump 102")
    assert row["job_description"].startswith("Replace mechanical seal")
    assert row["group_leader"]         == "Jane Smith"
    assert row["status"]               == "draft"
    assert row["loto_number"] is not None
    assert row["loto_number"].startswith("NV-LOTO-")


def test_full_loto_saves_boundary_rows(app, client):
    """Boundary rows posted with the form are stored in loto_boundaries."""
    make_user(app, role="operator")
    login(client)

    client.post("/loto/autosave", data=FULL_LOTO_DATA)
    loto_id = _last_loto(app)["id"]

    conn = raw_db(app)
    rows = conn.execute(
        "SELECT * FROM loto_boundaries WHERE loto_id=? ORDER BY position",
        (loto_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 2

    assert rows[0]["equipment"]         == "FP-102 Motor"
    assert rows[0]["location"]          == "MCC-4 Bucket 12"
    assert rows[0]["energy_type"]       == "Electrical"
    assert rows[0]["isolation_device"]  == "Breaker 12"
    assert rows[0]["device_position"]   == "Open / Locked"

    assert rows[1]["equipment"]         == "FP-102 Suction Valve"
    assert rows[1]["energy_type"]       == "Pressure / Fluid"
    assert rows[1]["isolation_device"]  == "Gate Valve GV-204"
    assert rows[1]["device_position"]   == "Closed / Chained"


def test_full_loto_saves_verifiers(app, client):
    """Verifier rows posted with the form are stored in loto_verifiers."""
    make_user(app, role="operator")
    login(client)

    client.post("/loto/autosave", data=FULL_LOTO_DATA)
    loto_id = _last_loto(app)["id"]

    conn = raw_db(app)
    rows = conn.execute(
        "SELECT name, date FROM loto_verifiers WHERE loto_id=? ORDER BY position",
        (loto_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0]["name"] == "Bob Technician"
    assert rows[0]["date"] == "2026-06-01"
    assert rows[1]["name"] == "Carol Inspector"


def test_boundary_rows_replaced_on_update(app, client):
    """Re-saving a LOTO replaces boundary rows rather than appending."""
    make_user(app, role="operator")
    login(client)

    client.post("/loto/autosave", data=FULL_LOTO_DATA)
    loto_id = _last_loto(app)["id"]

    # Resave with only one boundary row
    updated = {
        "loto_id": loto_id,
        "plant": "NV",
        "b_equipment_1":        "Different Motor",
        "b_location_1":         "MCC-5",
        "b_energy_type_1":      "Electrical",
        "b_isolation_device_1": "Breaker 5",
        "b_device_position_1":  "Open",
    }
    client.post("/loto/autosave", data=updated)

    conn = raw_db(app)
    rows = conn.execute(
        "SELECT equipment FROM loto_boundaries WHERE loto_id=?", (loto_id,)
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0]["equipment"] == "Different Motor"


def test_full_loto_workflow_operator_to_active(app, client):
    """Operator creates a LOTO linked to an existing SWP, submits; admin activates it."""
    make_user(app, role="operator", username="op",
              first_name="Test", last_name="Operator")

    # Admin creates and issues the SWP first — operator will link the LOTO to it
    make_user(app, role="admin", username="adm",
              first_name="Site", last_name="Manager")
    login(client, username="adm")
    client.post("/swp/autosave", data={"plant": "NV", "date": "2026-06-01"})
    conn = raw_db(app)
    swp_id = conn.execute("SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    client.post(f"/swp/{swp_id}/submit")  # admin self-issues
    client.post("/logout")

    # Operator creates the LOTO with from_swp set on the initial INSERT
    login(client, username="op")
    loto_data = dict(FULL_LOTO_DATA, from_swp=swp_id)
    client.post("/loto/autosave", data=loto_data)
    loto_id = _last_loto(app)["id"]

    # Operator submits → pending
    rv = client.post(f"/loto/{loto_id}/submit", follow_redirects=False)
    assert rv.status_code == 302
    assert _last_loto(app)["status"] == "pending"
    client.post("/logout")

    # Admin activates
    login(client, username="adm")
    rv = client.post(f"/loto/{loto_id}/activate", follow_redirects=False)
    assert rv.status_code == 302

    conn = raw_db(app)
    row = conn.execute(
        "SELECT status, loto_number FROM loto_permits WHERE id=?", (loto_id,)
    ).fetchone()
    conn.close()

    assert row["status"]       == "active"
    assert "NV-LOTO-" in row["loto_number"]


def test_loto_activation_creates_signature_requests_for_matched_verifiers(app, client):
    """Verifier names matching real users produce signature requests when LOTO is activated."""
    # Create users whose names match the verifiers in FULL_LOTO_DATA
    make_user(app, role="operator", username="bob",
              first_name="Bob", last_name="Technician")
    make_user(app, role="operator", username="carol",
              first_name="Carol", last_name="Inspector")

    make_user(app, role="admin", username="adm",
              first_name="Site", last_name="Manager")
    login(client, username="adm")

    # Admin creates and issues a SWP first, then creates the LOTO linked to it
    client.post("/swp/autosave", data={"plant": "NV", "date": "2026-06-01"})
    conn = raw_db(app)
    swp_id = conn.execute("SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    client.post(f"/swp/{swp_id}/submit")  # admin self-issues

    # Create LOTO with verifiers and the SWP link set on INSERT
    loto_data = dict(FULL_LOTO_DATA, from_swp=swp_id)
    client.post("/loto/autosave", data=loto_data)
    loto_id = _last_loto(app)["id"]

    client.post(f"/loto/{loto_id}/activate")

    conn = raw_db(app)
    reqs = conn.execute(
        """SELECT sr.signature_role, u.username
           FROM signature_requests sr
           JOIN users u ON u.id = sr.requested_from
           WHERE sr.permit_type='loto' AND sr.permit_id=?""",
        (loto_id,),
    ).fetchall()
    conn.close()

    usernames = {r["username"] for r in reqs}
    assert "bob"   in usernames
    assert "carol" in usernames
