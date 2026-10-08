"""Tests for the equipment blueprint — tag_id generation, search, create, admin routes."""
import sqlite3

from tests.conftest import make_user, login, raw_db


def _insert_equipment(app, plant="NV", process_name="Pump A", tag_id=None):
    """Insert an equipment record directly and return its id."""
    if tag_id is None:
        tag_id = f"{plant}-PUMP-A"
    conn = raw_db(app)
    conn.execute(
        "INSERT INTO equipment (plant, process_name, tag_id, active, created_by) VALUES (?,?,?,1,1)",
        (plant, process_name, tag_id),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM equipment WHERE tag_id=?", (tag_id,)).fetchone()
    conn.close()
    return row[0]


# ── Tag-ID slug unit test ─────────────────────────────────────────────────────

def test_make_tag_id_slug():
    from app.equipment import _make_tag_id
    assert _make_tag_id("NV", "Pump A")          == "NV-PUMP-A"
    assert _make_tag_id("SE", "air compressor #1") == "SE-AIR-COMPRESSOR-1"
    assert _make_tag_id("NV", "fan/motor (main)") == "NV-FAN-MOTOR-MAIN"


# ── Role guards ───────────────────────────────────────────────────────────────

def test_operator_blocked_from_equipment_index(app, client):
    make_user(app, role="operator")
    login(client)
    assert client.get("/equipment/").status_code == 403


def test_admin_can_access_equipment_index(app, client):
    make_user(app, role="admin")
    login(client)
    assert client.get("/equipment/").status_code == 200


def test_operator_blocked_from_add(app, client):
    make_user(app, role="operator")
    login(client)
    assert client.get("/equipment/add").status_code == 403


# ── Search ────────────────────────────────────────────────────────────────────

def test_search_returns_results(app, client):
    make_user(app, role="operator")
    login(client)
    _insert_equipment(app, plant="NV", process_name="Coolant Pump", tag_id="NV-COOLANT-PUMP")
    rv = client.get("/equipment/search?q=Coolant&plant=NV")
    assert rv.status_code == 200
    assert b"NV-COOLANT-PUMP" in rv.data


def test_search_plant_filter(app, client):
    make_user(app, role="operator")
    login(client)
    _insert_equipment(app, plant="NV", process_name="Alpha", tag_id="NV-ALPHA")
    _insert_equipment(app, plant="SE", process_name="Beta",  tag_id="SE-BETA")
    rv = client.get("/equipment/search?plant=NV")
    assert b"NV-ALPHA" in rv.data
    assert b"SE-BETA"  not in rv.data


# ── HTMX create ───────────────────────────────────────────────────────────────

def test_create_inserts_record(app, client):
    make_user(app, role="operator")
    login(client)
    rv = client.post("/equipment/create",
                     data={"plant": "NV", "process_name": "Test Motor"})
    assert rv.status_code == 200
    conn = raw_db(app)
    row = conn.execute("SELECT tag_id FROM equipment WHERE tag_id='NV-TEST-MOTOR'").fetchone()
    conn.close()
    assert row is not None


def test_create_deduplicates_by_tag_id(app, client):
    make_user(app, role="operator")
    login(client)
    _insert_equipment(app, plant="NV", process_name="Fan", tag_id="NV-FAN")
    rv = client.post("/equipment/create", data={"plant": "NV", "process_name": "Fan"})
    assert rv.status_code == 200
    conn = raw_db(app)
    count = conn.execute("SELECT COUNT(*) FROM equipment WHERE tag_id='NV-FAN'").fetchone()[0]
    conn.close()
    assert count == 1


def test_create_missing_plant_returns_422(app, client):
    make_user(app, role="operator")
    login(client)
    rv = client.post("/equipment/create", data={"process_name": "Orphan"})
    assert rv.status_code == 422


# ── Admin edit ────────────────────────────────────────────────────────────────

def test_admin_edit_page_loads(app, client):
    make_user(app, role="admin")
    login(client)
    equip_id = _insert_equipment(app, plant="NV", process_name="Valve", tag_id="NV-VALVE")
    rv = client.get(f"/equipment/{equip_id}/edit")
    assert rv.status_code == 200
    assert b"NV-VALVE" in rv.data
