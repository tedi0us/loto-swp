"""Tests for SWP-02 (PJSM continuation) and SWP-05 (work continuation) schema + routes."""
import sqlite3

import pytest

from tests.conftest import make_user, login, raw_db


def _cols(db_path, table):
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


def _tables(db_path):
    conn = sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    return names


# ── PJSM continuation schema: swp_pjsm_continuations + swp_pjsm_cont_attendees ───────────────

def test_pjsm_continuation_tables_created(app):
    tables = _tables(app.config["DATABASE"])
    assert "swp_pjsm_continuations" in tables
    assert "swp_pjsm_cont_attendees" in tables


def test_pjsm_continuation_columns(app):
    cols = _cols(app.config["DATABASE"], "swp_pjsm_continuations")
    for col in ("id", "swp_id", "host_employee", "location", "work_lead",
                "work_lead_sig", "date_time", "additional_comments",
                "further_continuation_req", "created_by", "created_at"):
        assert col in cols, f"column '{col}' missing from swp_pjsm_continuations"


def test_pjsm_cont_attendees_columns(app):
    cols = _cols(app.config["DATABASE"], "swp_pjsm_cont_attendees")
    for col in ("id", "continuation_id", "position", "name", "signature", "company", "date"):
        assert col in cols, f"column '{col}' missing from swp_pjsm_cont_attendees"


def test_pjsm_tables_idempotent(app):
    """Re-running init_db does not raise on existing tables."""
    with app.app_context():
        from app.db import init_db
        init_db(app)
    tables = _tables(app.config["DATABASE"])
    assert "swp_pjsm_continuations" in tables
    assert "swp_pjsm_cont_attendees" in tables


def test_pjsm_cont_cascade_delete(app):
    """Deleting the parent SWP cascades to swp_pjsm_continuations."""
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO users (id,username,password_hash,role,must_change_password)"
        " VALUES (99,'u99','h','operator',0)"
    )
    conn.execute(
        "INSERT INTO swp_permits (id,plant,status,is_backdated,created_by)"
        " VALUES (99,'NV','draft',0,99)"
    )
    conn.execute(
        "INSERT INTO swp_pjsm_continuations (id,swp_id,created_by)"
        " VALUES (1,99,99)"
    )
    conn.commit()
    conn.execute("DELETE FROM swp_permits WHERE id=99")
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM swp_pjsm_continuations WHERE swp_id=99"
    ).fetchone()[0]
    conn.close()
    assert count == 0, "continuation row should have been cascade-deleted"


def test_pjsm_attendee_cascade_delete(app):
    """Deleting a continuation cascades to its attendee rows."""
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO users (id,username,password_hash,role,must_change_password)"
        " VALUES (98,'u98','h','operator',0)"
    )
    conn.execute(
        "INSERT INTO swp_permits (id,plant,status,is_backdated,created_by)"
        " VALUES (98,'NV','draft',0,98)"
    )
    conn.execute(
        "INSERT INTO swp_pjsm_continuations (id,swp_id,created_by)"
        " VALUES (2,98,98)"
    )
    conn.execute(
        "INSERT INTO swp_pjsm_cont_attendees (continuation_id,position,name)"
        " VALUES (2,0,'Test Person')"
    )
    conn.commit()
    conn.execute("DELETE FROM swp_pjsm_continuations WHERE id=2")
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM swp_pjsm_cont_attendees WHERE continuation_id=2"
    ).fetchone()[0]
    conn.close()
    assert count == 0, "attendee row should have been cascade-deleted"


# ── Work continuation schema: swp_work_continuations + swp_work_cont_entries ──────────────────

def test_work_continuation_tables_created(app):
    tables = _tables(app.config["DATABASE"])
    assert "swp_work_continuations" in tables
    assert "swp_work_cont_entries" in tables


def test_work_continuation_columns(app):
    cols = _cols(app.config["DATABASE"], "swp_work_continuations")
    for col in ("id", "swp_id", "created_by", "created_at"):
        assert col in cols, f"column '{col}' missing from swp_work_continuations"


def test_work_cont_entries_columns(app):
    cols = _cols(app.config["DATABASE"], "swp_work_cont_entries")
    for col in ("id", "continuation_id", "position", "entry_type",
                "date", "time", "work_lead_phone", "work_lead_name",
                "work_lead_sig", "authorizer_name", "authorizer_sig",
                "job_status_comments"):
        assert col in cols, f"column '{col}' missing from swp_work_cont_entries"


def test_work_cont_tables_idempotent(app):
    """Re-running init_db does not raise on existing tables."""
    with app.app_context():
        from app.db import init_db
        init_db(app)
    tables = _tables(app.config["DATABASE"])
    assert "swp_work_continuations" in tables
    assert "swp_work_cont_entries" in tables


def test_work_cont_entry_type_check_constraint(app):
    """entry_type must be 'stop' or 'restart' — other values are rejected."""
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO users (id,username,password_hash,role,must_change_password)"
        " VALUES (97,'u97','h','operator',0)"
    )
    conn.execute(
        "INSERT INTO swp_permits (id,plant,status,is_backdated,created_by)"
        " VALUES (97,'NV','draft',0,97)"
    )
    conn.execute(
        "INSERT INTO swp_work_continuations (id,swp_id,created_by)"
        " VALUES (1,97,97)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO swp_work_cont_entries (continuation_id,position,entry_type)"
            " VALUES (1,0,'invalid')"
        )
        conn.commit()
    conn.close()


def test_work_cont_cascade_delete_from_swp(app):
    """Deleting the parent SWP cascades to swp_work_continuations."""
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO users (id,username,password_hash,role,must_change_password)"
        " VALUES (96,'u96','h','operator',0)"
    )
    conn.execute(
        "INSERT INTO swp_permits (id,plant,status,is_backdated,created_by)"
        " VALUES (96,'NV','draft',0,96)"
    )
    conn.execute(
        "INSERT INTO swp_work_continuations (id,swp_id,created_by)"
        " VALUES (2,96,96)"
    )
    conn.commit()
    conn.execute("DELETE FROM swp_permits WHERE id=96")
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM swp_work_continuations WHERE swp_id=96"
    ).fetchone()[0]
    conn.close()
    assert count == 0, "work continuation should have been cascade-deleted"


def test_work_cont_entry_cascade_delete(app):
    """Deleting a work continuation cascades to its entries."""
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO users (id,username,password_hash,role,must_change_password)"
        " VALUES (95,'u95','h','operator',0)"
    )
    conn.execute(
        "INSERT INTO swp_permits (id,plant,status,is_backdated,created_by)"
        " VALUES (95,'NV','draft',0,95)"
    )
    conn.execute(
        "INSERT INTO swp_work_continuations (id,swp_id,created_by)"
        " VALUES (3,95,95)"
    )
    conn.execute(
        "INSERT INTO swp_work_cont_entries (continuation_id,position,entry_type)"
        " VALUES (3,0,'stop')"
    )
    conn.commit()
    conn.execute("DELETE FROM swp_work_continuations WHERE id=3")
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM swp_work_cont_entries WHERE continuation_id=3"
    ).fetchone()[0]
    conn.close()
    assert count == 0, "entry row should have been cascade-deleted"


# ── Continuation routes ──────────────────────────────────────────────────────

def _create_issued_swp(app, client, username="testuser"):
    """Create an SWP in 'issued' status via admin self-issue, return swp_id."""
    make_user(app, role="admin", username=username)
    login(client, username=username)
    client.post("/swp/autosave", data={"plant": "NV", "date": "2026-05-28"})
    conn = raw_db(app)
    swp_id = conn.execute(
        "SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()
    client.post(f"/swp/{swp_id}/submit")
    return swp_id


def test_pjsm_new_loads(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.get(f"/swp/{swp_id}/continuations/pjsm/new")
    assert rv.status_code == 200


def test_pjsm_autosave_creates_continuation(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.post(f"/swp/{swp_id}/continuations/pjsm/autosave",
                     data={"work_lead": "Jane Doe", "location": "Pump Room"})
    assert rv.status_code == 200
    conn = raw_db(app)
    row = conn.execute(
        "SELECT * FROM swp_pjsm_continuations WHERE swp_id=?", (swp_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["work_lead"] == "Jane Doe"
    assert row["location"] == "Pump Room"


def test_pjsm_autosave_updates_continuation(app, client):
    swp_id = _create_issued_swp(app, client)
    client.post(f"/swp/{swp_id}/continuations/pjsm/autosave",
                data={"work_lead": "Jane Doe"})
    conn = raw_db(app)
    cont_id = conn.execute(
        "SELECT id FROM swp_pjsm_continuations WHERE swp_id=?", (swp_id,)
    ).fetchone()[0]
    conn.close()
    rv = client.post(f"/swp/{swp_id}/continuations/pjsm/autosave",
                     data={"cont_id": cont_id, "work_lead": "Updated Name"})
    assert rv.status_code == 200
    conn = raw_db(app)
    row = conn.execute(
        "SELECT work_lead FROM swp_pjsm_continuations WHERE id=?", (cont_id,)
    ).fetchone()
    conn.close()
    assert row["work_lead"] == "Updated Name"


def test_pjsm_autosave_pushes_url_on_new(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.post(f"/swp/{swp_id}/continuations/pjsm/autosave",
                     data={"work_lead": "Jane"})
    assert "HX-Push-Url" in rv.headers


def test_pjsm_edit_loads(app, client):
    swp_id = _create_issued_swp(app, client)
    client.post(f"/swp/{swp_id}/continuations/pjsm/autosave", data={"work_lead": "J"})
    conn = raw_db(app)
    cont_id = conn.execute(
        "SELECT id FROM swp_pjsm_continuations WHERE swp_id=?", (swp_id,)
    ).fetchone()[0]
    conn.close()
    rv = client.get(f"/swp/{swp_id}/continuations/pjsm/{cont_id}")
    assert rv.status_code == 200


def test_pjsm_list_loads(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.get(f"/swp/{swp_id}/continuations/pjsm/list")
    assert rv.status_code == 200


def test_pjsm_attendee_row_loads(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.get(f"/swp/{swp_id}/continuations/pjsm/0/attendee-row?idx=0")
    assert rv.status_code == 200


def test_pjsm_closed_swp_blocked(app, client):
    """Continuations on a closed SWP should return 403."""
    make_user(app, role="admin", username="adm2")
    login(client, username="adm2")
    client.post("/swp/autosave", data={"plant": "NV", "is_backdated": "y"})
    conn = raw_db(app)
    swp_id = conn.execute(
        "SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()
    client.post(f"/swp/{swp_id}/submit")
    rv = client.get(f"/swp/{swp_id}/continuations/pjsm/new")
    assert rv.status_code == 403


def test_work_new_loads(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.get(f"/swp/{swp_id}/continuations/work/new")
    assert rv.status_code == 200


def test_work_autosave_creates_continuation(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.post(f"/swp/{swp_id}/continuations/work/autosave",
                     data={"entry_type_0": "stop", "date_0": "2026-05-28",
                           "wl_name_0": "Bob"})
    assert rv.status_code == 200
    conn = raw_db(app)
    row = conn.execute(
        "SELECT id FROM swp_work_continuations WHERE swp_id=?", (swp_id,)
    ).fetchone()
    conn.close()
    assert row is not None


def test_work_autosave_pushes_url_on_new(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.post(f"/swp/{swp_id}/continuations/work/autosave",
                     data={"entry_type_0": "stop"})
    assert "HX-Push-Url" in rv.headers


def test_work_list_loads(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.get(f"/swp/{swp_id}/continuations/work/list")
    assert rv.status_code == 200


def test_work_cycle_row_loads(app, client):
    swp_id = _create_issued_swp(app, client)
    rv = client.get(f"/swp/{swp_id}/continuations/work/0/cycle-row?idx=0")
    assert rv.status_code == 200


# ── Cross-user access (IDOR) ───────────────────────────────────────────────────────

def test_idor_pjsm_cont_wrong_swp(app, client):
    """User A's continuation cannot be edited via User B's swp_id."""
    swp_id_a = _create_issued_swp(app, client, username="user_a")
    client.post(f"/swp/{swp_id_a}/continuations/pjsm/autosave",
                data={"work_lead": "A"})
    conn = raw_db(app)
    cont_id = conn.execute(
        "SELECT id FROM swp_pjsm_continuations WHERE swp_id=?", (swp_id_a,)
    ).fetchone()[0]
    conn.close()
    client.post("/logout")

    swp_id_b = _create_issued_swp(app, client, username="user_b")
    rv = client.post(f"/swp/{swp_id_b}/continuations/pjsm/autosave",
                     data={"cont_id": cont_id, "work_lead": "Hijacked"})
    assert rv.status_code == 403


def test_idor_work_cont_wrong_swp(app, client):
    """User A's work continuation cannot be edited via User B's swp_id."""
    swp_id_a = _create_issued_swp(app, client, username="user_c")
    client.post(f"/swp/{swp_id_a}/continuations/work/autosave",
                data={"entry_type_0": "stop"})
    conn = raw_db(app)
    cont_id = conn.execute(
        "SELECT id FROM swp_work_continuations WHERE swp_id=?", (swp_id_a,)
    ).fetchone()[0]
    conn.close()
    client.post("/logout")

    swp_id_b = _create_issued_swp(app, client, username="user_d")
    rv = client.post(f"/swp/{swp_id_b}/continuations/work/autosave",
                     data={"cont_id": cont_id, "entry_type_0": "restart"})
    assert rv.status_code == 403


def test_idor_pjsm_cont_view_other_user(app, client):
    """Viewing a continuation for wrong swp_id returns 403."""
    swp_id_a = _create_issued_swp(app, client, username="user_e")
    client.post(f"/swp/{swp_id_a}/continuations/pjsm/autosave", data={"work_lead": "E"})
    conn = raw_db(app)
    cont_id = conn.execute(
        "SELECT id FROM swp_pjsm_continuations WHERE swp_id=?", (swp_id_a,)
    ).fetchone()[0]
    conn.close()
    client.post("/logout")

    swp_id_b = _create_issued_swp(app, client, username="user_f")
    rv = client.get(f"/swp/{swp_id_b}/continuations/pjsm/{cont_id}")
    assert rv.status_code == 403


def test_idor_work_cont_view_other_user(app, client):
    """Viewing a work continuation for wrong swp_id returns 403."""
    swp_id_a = _create_issued_swp(app, client, username="user_g")
    client.post(f"/swp/{swp_id_a}/continuations/work/autosave",
                data={"entry_type_0": "stop"})
    conn = raw_db(app)
    cont_id = conn.execute(
        "SELECT id FROM swp_work_continuations WHERE swp_id=?", (swp_id_a,)
    ).fetchone()[0]
    conn.close()
    client.post("/logout")

    swp_id_b = _create_issued_swp(app, client, username="user_h")
    rv = client.get(f"/swp/{swp_id_b}/continuations/work/{cont_id}")
    assert rv.status_code == 403


# ── Continuation panel on the review page ─────────────────────

def test_review_shows_continuation_panel_for_issued_swp(app, client):
    swp_id = _create_issued_swp(app, client, username="adm_4a")
    rv = client.get(f"/swp/{swp_id}/review")
    assert rv.status_code == 200
    assert b"Continuation Documents" in rv.data


def test_review_no_continuation_panel_for_draft(app, client):
    make_user(app, role="admin", username="adm_4b")
    login(client, username="adm_4b")
    client.post("/swp/autosave", data={"plant": "NV"})
    conn = raw_db(app)
    swp_id = conn.execute(
        "SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()
    rv = client.get(f"/swp/{swp_id}/review")
    assert rv.status_code == 200
    assert b"Continuation Documents" not in rv.data


# ── Continuation panel integration ──────────────────────

def test_pjsm_list_shows_link_after_creation(app, client):
    """After creating an SWP-02, pjsm/list returns a link to it."""
    swp_id = _create_issued_swp(app, client, username="adm_4c1")
    client.post(f"/swp/{swp_id}/continuations/pjsm/autosave",
                data={"work_lead": "Jane Doe"})
    rv = client.get(f"/swp/{swp_id}/continuations/pjsm/list")
    assert rv.status_code == 200
    assert b"PJSM Continuation #" in rv.data


def test_work_list_shows_link_after_creation(app, client):
    """After creating an SWP-05, work/list returns a link to it."""
    swp_id = _create_issued_swp(app, client, username="adm_4c2")
    client.post(f"/swp/{swp_id}/continuations/work/autosave",
                data={"entry_type_0": "stop"})
    rv = client.get(f"/swp/{swp_id}/continuations/work/list")
    assert rv.status_code == 200
    assert b"Work Continuation #" in rv.data


def test_review_continuation_required_banner(app, client):
    """continuation='1' on an issued SWP shows the warning banner on review."""
    make_user(app, role="admin", username="adm_4c3")
    login(client, username="adm_4c3")
    client.post("/swp/autosave", data={"plant": "NV", "continuation": "1"})
    conn = raw_db(app)
    swp_id = conn.execute(
        "SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()
    client.post(f"/swp/{swp_id}/submit")
    rv = client.get(f"/swp/{swp_id}/review")
    assert rv.status_code == 200
    assert b"Continuation Sheet Required" in rv.data


def test_review_no_continuation_panel_for_pending(app, client):
    """Continuation Documents panel is not shown on a pending SWP."""
    make_user(app, role="operator", username="op_4c4")
    login(client, username="op_4c4")
    client.post("/swp/autosave", data={"plant": "NV"})
    conn = raw_db(app)
    swp_id = conn.execute(
        "SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()
    client.post(f"/swp/{swp_id}/submit")
    rv = client.get(f"/swp/{swp_id}/review")
    assert rv.status_code == 200
    assert b"Continuation Documents" not in rv.data
