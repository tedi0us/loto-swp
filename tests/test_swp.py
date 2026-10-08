"""Tests for the SWP blueprint — create, submit, approve, recall, historical."""
import sqlite3

from tests.conftest import make_user, login, raw_db

# ── Realistic full-permit data ────────────────────────────────────────────────

FULL_SWP_DATA = {
    "plant":        "NV",
    "date":         "2026-06-01",
    "time":         "07:30",
    "location":     "Turbine Hall B",
    "company":      "Acme Contractors",
    "work_lead":    "Jane Smith",
    "scope_of_work": "Replace feed pump mechanical seal on FP-102. Isolate per LOTO NV-LOTO-26-0001.",
    # Associated permits
    "permit_electrical": "y",
    # Hazard energy
    "he_electrical": "y",
    "he_mechanical": "y",
    "he_pressure":   "y",
    # PPE
    "ppe_hard_hat":       "y",
    "ppe_safety_glasses": "y",
    "ppe_gloves":         "y",
    "ppe_fr_clothing":    "y",
    "ppe_insul_gloves":   "y",
    "ppe_face_shield":    "y",
    # Task hazard assessment
    "tha_loto":         "y",
    "tha_elevated_work": "y",
    "tha_power_tools":  "y",
    # Task safeguards
    "ts_review_procedures": "y",
    "ts_valve_positions":   "y",
    "ts_haz_comm":          "y",
    # Free text
    "hazard_mitigation":  "Verify zero energy state before breaking containment.",
    "additional_comments": "Notify control room before starting.",
    # PJSM
    "pjsm_leader": "Bob Manager",
}


def _last_swp(app):
    conn = raw_db(app)
    row = conn.execute(
        "SELECT id, status, permit_number, is_backdated FROM swp_permits ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _create_swp(client, app, extra=None):
    """Autosave a minimal SWP and return its id."""
    data = {"plant": "NV", "date": "2026-05-07", **(extra or {})}
    client.post("/swp/autosave", data=data)
    return _last_swp(app)["id"]


# ── Basic access ──────────────────────────────────────────────────────────────

def test_new_swp_page_loads(app, client):
    make_user(app)
    login(client)
    assert client.get("/swp/new").status_code == 200


def test_swp_index_loads(app, client):
    make_user(app)
    login(client)
    assert client.get("/swp/").status_code == 200


# ── Autosave / create ─────────────────────────────────────────────────────────

def test_autosave_creates_draft(app, client):
    make_user(app)
    login(client)
    rv = client.post("/swp/autosave", data={"plant": "NV", "date": "2026-05-07"})
    assert rv.status_code == 200
    swp = _last_swp(app)
    assert swp is not None
    assert swp["status"] == "draft"


def test_autosave_updates_existing(app, client):
    make_user(app)
    login(client)
    swp_id = _create_swp(client, app)
    rv = client.post("/swp/autosave",
                     data={"swp_id": swp_id, "plant": "SE", "date": "2026-05-07"})
    assert rv.status_code == 200
    conn = raw_db(app)
    row = conn.execute("SELECT plant FROM swp_permits WHERE id=?", (swp_id,)).fetchone()
    conn.close()
    assert row["plant"] == "SE"


def test_autosave_pushes_url_header(app, client):
    make_user(app)
    login(client)
    rv = client.post("/swp/autosave", data={"plant": "NV"})
    assert "HX-Push-Url" in rv.headers


# ── Submit workflow ───────────────────────────────────────────────────────────

def test_operator_submit_sets_pending(app, client):
    make_user(app, role="operator")
    login(client)
    swp_id = _create_swp(client, app)
    rv = client.post(f"/swp/{swp_id}/submit", follow_redirects=False)
    assert rv.status_code == 302
    swp = _last_swp(app)
    assert swp["status"] == "pending"


def test_admin_self_issue_on_submit(app, client):
    make_user(app, role="admin")
    login(client)
    swp_id = _create_swp(client, app)
    rv = client.post(f"/swp/{swp_id}/submit", follow_redirects=False)
    assert rv.status_code == 302
    swp = _last_swp(app)
    assert swp["status"] == "issued"
    assert swp["permit_number"] is not None


def test_admin_approve_pending_swp(app, client):
    # Operator creates and submits, admin approves
    op_id = make_user(app, role="operator", username="op")
    login(client, username="op")
    swp_id = _create_swp(client, app)
    client.post(f"/swp/{swp_id}/submit")
    client.post("/logout")

    make_user(app, role="admin", username="adm")
    login(client, username="adm")
    rv = client.post(f"/swp/{swp_id}/approve", follow_redirects=False)
    assert rv.status_code == 302
    swp = _last_swp(app)
    assert swp["status"] == "issued"


def test_recall_sets_draft(app, client):
    make_user(app, role="operator")
    login(client)
    swp_id = _create_swp(client, app)
    client.post(f"/swp/{swp_id}/submit")  # → pending
    rv = client.post(f"/swp/{swp_id}/recall", follow_redirects=False)
    assert rv.status_code == 302
    conn = raw_db(app)
    row = conn.execute("SELECT status FROM swp_permits WHERE id=?", (swp_id,)).fetchone()
    conn.close()
    assert row["status"] == "draft"


def test_historical_swp_goes_to_closed(app, client):
    make_user(app, role="admin")
    login(client)
    swp_id = _create_swp(client, app, extra={"is_backdated": "y"})
    rv = client.post(f"/swp/{swp_id}/submit", follow_redirects=False)
    assert rv.status_code == 302
    swp = _last_swp(app)
    assert swp["status"] == "closed"


def test_non_owner_cannot_edit_draft(app, client):
    make_user(app, role="operator", username="owner")
    make_user(app, role="operator", username="other")
    login(client, username="owner")
    swp_id = _create_swp(client, app)
    client.post("/logout")

    login(client, username="other")
    # The edit route doesn't 403 non-owners on GET, but autosave on another's draft should
    rv = client.post("/swp/autosave", data={"swp_id": swp_id, "plant": "SE"})
    assert rv.status_code == 403


# ── Name fields and continuation label ────────────────────────────────

def test_autosave_saves_name_fields(app, client):
    make_user(app)
    login(client)
    swp_id = _create_swp(client, app, extra={
        "issued_auth_name": "Alice Authorizer",
        "issued_wl_name":   "Bob Lead",
        "closed_auth_name": "Carol Closer",
        "closed_wl_name":   "Dave Lead",
    })
    conn = raw_db(app)
    row = conn.execute(
        "SELECT issued_auth_name, issued_wl_name, closed_auth_name, closed_wl_name"
        " FROM swp_permits WHERE id=?", (swp_id,)
    ).fetchone()
    conn.close()
    assert row["issued_auth_name"] == "Alice Authorizer"
    assert row["issued_wl_name"]   == "Bob Lead"
    assert row["closed_auth_name"] == "Carol Closer"
    assert row["closed_wl_name"]   == "Dave Lead"


def test_continuation_field_saves(app, client):
    make_user(app)
    login(client)
    swp_id = _create_swp(client, app, extra={"continuation": "1"})
    conn = raw_db(app)
    row = conn.execute("SELECT continuation FROM swp_permits WHERE id=?", (swp_id,)).fetchone()
    conn.close()
    assert str(row["continuation"]) == "1"


def test_approve_prepopulates_issued_auth_name(app, client):
    make_user(app, role="operator", username="op",
              first_name="Test", last_name="Operator")
    login(client, username="op")
    swp_id = _create_swp(client, app)
    client.post(f"/swp/{swp_id}/submit")
    client.post("/logout")

    make_user(app, role="admin", username="adm",
              first_name="Admin", last_name="Approver")
    login(client, username="adm")
    client.post(f"/swp/{swp_id}/approve")

    conn = raw_db(app)
    row = conn.execute(
        "SELECT issued_auth_name FROM swp_permits WHERE id=?", (swp_id,)
    ).fetchone()
    conn.close()
    assert row["issued_auth_name"] == "Admin Approver"


# ── Full realistic SWP creation ───────────────────────────────────────────────

def test_full_swp_saves_all_sections(app, client):
    """Autosave a permit filled with realistic field data and verify every section persisted."""
    make_user(app, role="operator")
    login(client)

    rv = client.post("/swp/autosave", data=FULL_SWP_DATA)
    assert rv.status_code == 200

    conn = raw_db(app)
    row = conn.execute("SELECT * FROM swp_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    # Core identity fields
    assert row["plant"]     == "NV"
    assert row["location"]  == "Turbine Hall B"
    assert row["company"]   == "Acme Contractors"
    assert row["work_lead"] == "Jane Smith"
    assert row["scope_of_work"].startswith("Replace feed pump")
    assert row["pjsm_leader"] == "Bob Manager"

    # Associated permit flag (StringField — stores the value sent, "y" when checked)
    assert row["permit_electrical"]  # truthy when set

    # Hazard energy
    assert row["he_electrical"] == 1
    assert row["he_mechanical"] == 1
    assert row["he_pressure"]   == 1
    assert row["he_thermal"]    == 0  # not submitted

    # PPE
    assert row["ppe_hard_hat"]       == 1
    assert row["ppe_safety_glasses"] == 1
    assert row["ppe_insul_gloves"]   == 1
    assert row["ppe_face_shield"]    == 1
    assert row["ppe_arc_flash_suit"] == 0  # not submitted

    # THA
    assert row["tha_loto"]          == 1
    assert row["tha_elevated_work"] == 1
    assert row["tha_power_tools"]   == 1
    assert row["tha_confined_space"] == 0

    # Task safeguards
    assert row["ts_review_procedures"] == 1
    assert row["ts_valve_positions"]   == 1
    assert row["ts_haz_comm"]          == 1
    assert row["ts_barricade"]         == 0

    # Free text
    assert "zero energy state" in row["hazard_mitigation"]
    assert "control room" in row["additional_comments"]


def test_full_swp_saves_attendees(app, client):
    """Attendees posted with the form end up in swp_attendees."""
    make_user(app, role="operator")
    login(client)

    data = dict(FULL_SWP_DATA)
    data.update({
        "attend_name_0":    "Alice Wrench",
        "attend_company_0": "Acme Contractors",
        "attend_date_0":    "2026-06-01",
        "attend_name_1":    "Carlos Pipe",
        "attend_company_1": "Acme Contractors",
        "attend_date_1":    "2026-06-01",
    })
    client.post("/swp/autosave", data=data)

    swp_id = _last_swp(app)["id"]
    conn = raw_db(app)
    rows = conn.execute(
        "SELECT name, company, date FROM swp_attendees WHERE swp_id=? ORDER BY position",
        (swp_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0]["name"]    == "Alice Wrench"
    assert rows[0]["company"] == "Acme Contractors"
    assert rows[1]["name"]    == "Carlos Pipe"


def test_full_swp_workflow_operator_to_issued(app, client):
    """Operator fills full permit, submits; admin approves; permit is issued with number and auth name."""
    make_user(app, role="operator", username="op",
              first_name="Test", last_name="Operator")
    login(client, username="op")

    # Create with realistic data
    rv = client.post("/swp/autosave", data=FULL_SWP_DATA)
    assert rv.status_code == 200
    swp_id = _last_swp(app)["id"]

    # Submit
    rv = client.post(f"/swp/{swp_id}/submit", follow_redirects=False)
    assert rv.status_code == 302
    assert _last_swp(app)["status"] == "pending"

    client.post("/logout")

    # Admin approves
    make_user(app, role="admin", username="adm",
              first_name="Site", last_name="Manager")
    login(client, username="adm")
    rv = client.post(f"/swp/{swp_id}/approve", follow_redirects=False)
    assert rv.status_code == 302

    conn = raw_db(app)
    row = conn.execute(
        "SELECT status, permit_number, issued_auth_name FROM swp_permits WHERE id=?",
        (swp_id,),
    ).fetchone()
    conn.close()

    assert row["status"]          == "issued"
    assert row["permit_number"]   is not None
    assert "NV-SWP-" in row["permit_number"]
    assert row["issued_auth_name"] == "Site Manager"


def test_swp_approval_creates_signature_request_for_work_lead(app, client):
    """work_lead matching a real user gets a signature request when an admin approves.
    Note: admin self-issue via submit() does NOT create signature requests — only approve() does."""
    # Create the user whose name matches work_lead in FULL_SWP_DATA
    make_user(app, role="operator", username="wl",
              first_name="Jane", last_name="Smith")

    # Operator creates and submits the SWP (→ pending)
    make_user(app, role="operator", username="op",
              first_name="Test", last_name="Operator")
    login(client, username="op")
    client.post("/swp/autosave", data=FULL_SWP_DATA)
    swp_id = _last_swp(app)["id"]
    client.post(f"/swp/{swp_id}/submit")
    client.post("/logout")

    # Admin approves (→ issued); this path calls create_signature_request()
    make_user(app, role="admin", username="adm",
              first_name="Site", last_name="Manager")
    login(client, username="adm")
    client.post(f"/swp/{swp_id}/approve")

    conn = raw_db(app)
    reqs = conn.execute(
        "SELECT signature_role FROM signature_requests WHERE permit_type='swp' AND permit_id=?",
        (swp_id,),
    ).fetchall()
    conn.close()

    roles = {r["signature_role"] for r in reqs}
    assert "swp_work_lead" in roles


def test_pjsm_leader_saved_and_routed_on_approval(app, client):
    """pjsm_leader is saved by autosave and triggers a signature request when an admin approves."""
    make_user(app, role="operator", username="pjsm",
              first_name="Bob", last_name="Manager")

    make_user(app, role="operator", username="op",
              first_name="Test", last_name="Operator")
    login(client, username="op")
    client.post("/swp/autosave", data=FULL_SWP_DATA)
    swp_id = _last_swp(app)["id"]

    # Verify pjsm_leader was persisted
    conn = raw_db(app)
    row = conn.execute("SELECT pjsm_leader FROM swp_permits WHERE id=?", (swp_id,)).fetchone()
    conn.close()
    assert row["pjsm_leader"] == "Bob Manager"

    client.post(f"/swp/{swp_id}/submit")
    client.post("/logout")

    make_user(app, role="admin", username="adm",
              first_name="Site", last_name="Manager")
    login(client, username="adm")
    client.post(f"/swp/{swp_id}/approve")

    conn = raw_db(app)
    reqs = conn.execute(
        "SELECT signature_role FROM signature_requests WHERE permit_type='swp' AND permit_id=?",
        (swp_id,),
    ).fetchall()
    conn.close()

    roles = {r["signature_role"] for r in reqs}
    assert "pjsm_leader" in roles


def test_admin_self_issue_creates_signature_requests(app, client):
    """When an admin submits their own SWP (self-issue), signature requests are still created."""
    make_user(app, role="operator", username="wl",
              first_name="Jane", last_name="Smith")
    make_user(app, role="operator", username="pjsm",
              first_name="Bob", last_name="Manager")

    make_user(app, role="admin", username="adm",
              first_name="Site", last_name="Manager")
    login(client, username="adm")

    client.post("/swp/autosave", data=FULL_SWP_DATA)
    swp_id = _last_swp(app)["id"]
    client.post(f"/swp/{swp_id}/submit")  # admin self-issue path

    conn = raw_db(app)
    reqs = conn.execute(
        "SELECT signature_role FROM signature_requests WHERE permit_type='swp' AND permit_id=?",
        (swp_id,),
    ).fetchall()
    conn.close()

    roles = {r["signature_role"] for r in reqs}
    assert "swp_work_lead" in roles
    assert "pjsm_leader"   in roles
