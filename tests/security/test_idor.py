"""Security: IDOR checks — user A cannot act on user B's resources."""
import sqlite3

import pytest

from tests.conftest import login, make_user, TEST_PW


def _make_swp(app, user_id, plant="NV", status="draft"):
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    conn.execute(
        "INSERT INTO swp_permits (plant,status,is_backdated,created_by) VALUES (?,?,0,?)",
        (plant, status, user_id),
    )
    conn.commit()
    swp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return swp_id


def _make_loto(app, user_id, plant="NV", status="draft"):
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    conn.execute(
        "INSERT INTO loto_permits (plant,status,created_by) VALUES (?,?,?)",
        (plant, status, user_id),
    )
    conn.commit()
    loto_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return loto_id


def test_idor_swp_edit(app, client):
    """User A cannot access the edit page for User B's SWP."""
    uid_a = make_user(app, role="operator", username="user_a")
    uid_b = make_user(app, role="operator", username="user_b")
    swp_b = _make_swp(app, uid_b)
    login(client, username="user_a")
    r = client.get(f"/swp/{swp_b}/edit")
    assert r.status_code == 403


def test_idor_swp_autosave(app, client):
    """User A cannot autosave User B's SWP."""
    uid_a = make_user(app, role="operator", username="user_a2")
    uid_b = make_user(app, role="operator", username="user_b2")
    swp_b = _make_swp(app, uid_b)
    login(client, username="user_a2")
    r = client.post("/swp/autosave", data={"swp_id": str(swp_b), "plant": "NV"})
    assert r.status_code == 403


def test_idor_swp_submit(app, client):
    """User A cannot submit User B's SWP for approval."""
    uid_a = make_user(app, role="operator", username="user_a3")
    uid_b = make_user(app, role="operator", username="user_b3")
    swp_b = _make_swp(app, uid_b, status="draft")
    login(client, username="user_a3")
    r = client.post(f"/swp/{swp_b}/submit")
    assert r.status_code == 403


def test_idor_loto_edit(app, client):
    """User A cannot access User B's LOTO edit page."""
    uid_a = make_user(app, role="operator", username="loto_a")
    uid_b = make_user(app, role="operator", username="loto_b")
    loto_b = _make_loto(app, uid_b)
    login(client, username="loto_a")
    r = client.get(f"/loto/{loto_b}/edit")
    assert r.status_code == 403


def test_idor_loto_activate(app, client):
    """Non-owner operator cannot activate another user's LOTO."""
    uid_a = make_user(app, role="operator", username="loto_act_a")
    uid_b = make_user(app, role="operator", username="loto_act_b")
    loto_b = _make_loto(app, uid_b, status="pending")
    login(client, username="loto_act_a")
    r = client.post(f"/loto/{loto_b}/activate")
    # Expect 403 (not owner) or 404 (no linked SWP) depending on check order
    assert r.status_code in (403, 404)


def test_idor_signature_sign(app, client):
    """User A cannot complete User B's signature request."""
    uid_a = make_user(app, role="operator", username="sig_a")
    uid_b = make_user(app, role="operator", username="sig_b")
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    conn.execute(
        "INSERT INTO signature_requests (permit_type,permit_id,signature_role,requested_from)"
        " VALUES ('swp',1,'swp_work_lead',?)",
        (uid_b,),
    )
    conn.commit()
    sig_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    login(client, username="sig_a")
    r = client.post(f"/signatures/{sig_id}/sign", data={"signature_data": "Attacker Name"})
    assert r.status_code == 403


def test_idor_equipment_non_admin_edit(app, client):
    """A non-admin cannot reach the equipment edit page (admin+ only)."""
    make_user(app, role="operator", username="equip_op")
    login(client, username="equip_op")
    r = client.get("/equipment/1/edit")
    assert r.status_code in (403, 404)
