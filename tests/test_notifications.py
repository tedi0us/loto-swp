"""Tests for SMS notification triggers. Twilio client is always mocked."""
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import login, make_user, raw_db

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_swp(client, data=None):
    payload = {"plant": "NV", "date": "2026-06-01", **(data or {})}
    rv = client.post("/swp/autosave", data=payload)
    assert rv.status_code == 200
    return rv


def _last_swp_id(app):
    conn = raw_db(app)
    row = conn.execute("SELECT id FROM swp_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row["id"]


def _last_loto_id(app):
    conn = raw_db(app)
    row = conn.execute("SELECT id FROM loto_permits ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row["id"]


# ── send_sms ─────────────────────────────────────────────────────────────────

def test_send_sms_skips_when_unconfigured(app):
    """send_sms logs a debug message and returns without error when env vars are absent."""
    with app.app_context():
        from app.notifications import send_sms
        # No TWILIO_* env vars set — should not raise
        send_sms("+15550000001", "test body")


def test_send_sms_calls_twilio_when_configured(app, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with app.app_context(), patch("twilio.rest.Client", return_value=mock_client):
        from app.notifications import send_sms
        send_sms("+15550000001", "hello")

    mock_client.messages.create.assert_called_once_with(
        body="hello", from_="+15550000000", to="+15550000001"
    )


def test_send_sms_swallows_twilio_exception(app, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("network error")
    with app.app_context(), patch("twilio.rest.Client", return_value=mock_client):
        from app.notifications import send_sms
        send_sms("+15550000001", "hello")  # must not raise


# ── notify_user ───────────────────────────────────────────────────────────────

def test_notify_user_sends_to_opted_in_user(app, monkeypatch):
    uid = make_user(app, phone="+15550000001", notify_sms=1)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with app.test_request_context(), patch("twilio.rest.Client", return_value=mock_client):
        from app import create_app
        with app.app_context():
            from app.notifications import notify_user
            notify_user(uid, "test message")

    mock_client.messages.create.assert_called_once()
    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["to"] == "+15550000001"
    assert "test message" in kwargs["body"]


def test_notify_user_skips_opted_out_user(app, monkeypatch):
    uid = make_user(app, phone="+15550000001", notify_sms=0)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        with app.app_context():
            from app.notifications import notify_user
            notify_user(uid, "test message")

    mock_client.messages.create.assert_not_called()


def test_notify_user_skips_null_phone(app, monkeypatch):
    conn = raw_db(app)
    conn.execute(
        """INSERT INTO users (username, password_hash, role, notify_sms)
           VALUES ('nophone', 'x', 'operator', 1)"""
    )
    conn.commit()
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        with app.app_context():
            from app.notifications import notify_user
            notify_user(uid, "test")

    mock_client.messages.create.assert_not_called()


# ── notify_admins ─────────────────────────────────────────────────────────────

def test_notify_admins_reaches_opted_in_admins_only(app, monkeypatch):
    make_user(app, role="admin",    username="adm1", phone="+15550000011", notify_sms=1)
    make_user(app, role="it_admin", username="adm2", phone="+15550000012", notify_sms=1)
    make_user(app, role="admin",    username="adm3", phone="+15550000013", notify_sms=0)
    make_user(app, role="operator", username="op1",  phone="+15550000014", notify_sms=1)

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        with app.app_context():
            from app.notifications import notify_admins
            notify_admins("new submission")

    calls = mock_client.messages.create.call_args_list
    to_numbers = {c.kwargs["to"] for c in calls}
    assert "+15550000011" in to_numbers   # opted-in admin
    assert "+15550000012" in to_numbers   # opted-in it_admin
    assert "+15550000013" not in to_numbers  # opted-out admin
    assert "+15550000014" not in to_numbers  # not admin


# ── SWP submit notifies admins ────────────────────────────────────────────────

def test_swp_submit_notifies_opted_in_admins(app, client, monkeypatch):
    make_user(app, role="admin", username="adm", phone="+15550000020", notify_sms=1)
    make_user(app, role="operator", username="op")
    login(client, "op")
    _make_swp(client)
    swp_id = _last_swp_id(app)

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        client.post(f"/swp/{swp_id}/submit")

    calls = mock_client.messages.create.call_args_list
    to_numbers = {c.kwargs["to"] for c in calls}
    assert "+15550000020" in to_numbers


def test_swp_submit_no_sms_when_no_opted_in_admins(app, client, monkeypatch):
    make_user(app, role="admin", username="adm", phone="+15550000020", notify_sms=0)
    make_user(app, role="operator", username="op")
    login(client, "op")
    _make_swp(client)
    swp_id = _last_swp_id(app)

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        client.post(f"/swp/{swp_id}/submit")

    mock_client.messages.create.assert_not_called()


# ── SWP approve notifies creator ──────────────────────────────────────────────

def test_swp_approve_notifies_creator(app, client, monkeypatch):
    creator_id = make_user(app, role="operator", username="op",
                           phone="+15550000030", notify_sms=1)
    make_user(app, role="admin", username="adm")
    login(client, "op")
    _make_swp(client)
    swp_id = _last_swp_id(app)
    client.post(f"/swp/{swp_id}/submit")
    client.post("/logout")
    login(client, "adm")

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        client.post(f"/swp/{swp_id}/approve")

    calls = mock_client.messages.create.call_args_list
    to_numbers = {c.kwargs["to"] for c in calls}
    assert "+15550000030" in to_numbers


# ── SWP recall notifies creator ───────────────────────────────────────────────

def test_swp_recall_notifies_creator(app, client, monkeypatch):
    creator_id = make_user(app, role="operator", username="op",
                           phone="+15550000040", notify_sms=1)
    make_user(app, role="admin", username="adm")
    login(client, "op")
    _make_swp(client)
    swp_id = _last_swp_id(app)
    client.post(f"/swp/{swp_id}/submit")
    client.post("/logout")
    login(client, "adm")

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        client.post(f"/swp/{swp_id}/recall")

    calls = mock_client.messages.create.call_args_list
    to_numbers = {c.kwargs["to"] for c in calls}
    assert "+15550000040" in to_numbers


# ── Signature request notifies requestee ──────────────────────────────────────

def test_signature_request_notifies_work_lead(app, client, monkeypatch):
    """When an admin issues a SWP, the matched work_lead gets an SMS with a signing link."""
    make_user(app, role="operator", username="wl",
              first_name="Jane", last_name="Smith",
              phone="+15550000050", notify_sms=1)
    make_user(app, role="admin", username="adm")
    login(client, "adm")
    _make_swp(client, {"work_lead": "Jane Smith"})
    swp_id = _last_swp_id(app)

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        client.post(f"/swp/{swp_id}/submit")  # admin self-issue

    calls = mock_client.messages.create.call_args_list
    to_numbers = {c.kwargs["to"] for c in calls}
    assert "+15550000050" in to_numbers

    # Body should contain a signing URL
    wl_call = next(c for c in calls if c.kwargs["to"] == "+15550000050")
    assert "/signatures/sign/" in wl_call.kwargs["body"]


def test_signature_request_skips_physical_only(app, client, monkeypatch):
    """physical_only signature requests do not trigger SMS."""
    make_user(app, role="operator", username="wl",
              first_name="Jane", last_name="Smith",
              phone="+15550000060", notify_sms=1)
    make_user(app, role="admin", username="adm")

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        with app.app_context():
            from app.signatures import create_signature_request
            conn = raw_db(app)
            swp_id = conn.execute(
                "INSERT INTO swp_permits (plant, status, created_by) VALUES ('NV','issued',1)"
            ).lastrowid
            conn.commit()
            conn.close()
            conn2 = raw_db(app)
            wl_id = conn2.execute("SELECT id FROM users WHERE username='wl'").fetchone()[0]
            conn2.close()
            create_signature_request("swp", swp_id, "swp_work_lead", wl_id, physical_only=True)

    mock_client.messages.create.assert_not_called()


# ── LOTO release notifies work lead ──────────────────────────────────────────

def test_loto_release_notifies_swp_work_lead(app, client, monkeypatch):
    make_user(app, role="operator", username="wl",
              first_name="Jane", last_name="Smith",
              phone="+15550000070", notify_sms=1)
    make_user(app, role="admin", username="adm",
              first_name="Site", last_name="Manager")
    login(client, "adm")

    # Create an issued SWP with a known work_lead
    _make_swp(client, {"work_lead": "Jane Smith"})
    swp_id = _last_swp_id(app)
    client.post(f"/swp/{swp_id}/submit")  # admin self-issue

    # Create and activate a LOTO linked to that SWP
    client.post("/loto/autosave", data={
        "plant": "NV", "date": "2026-06-01",
        "job_description": "Test job", "group_leader": "Site Manager",
        "from_swp": swp_id,
    })
    loto_id = _last_loto_id(app)
    client.post(f"/loto/{loto_id}/activate")

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        client.post(f"/loto/{loto_id}/release")

    calls = mock_client.messages.create.call_args_list
    to_numbers = {c.kwargs["to"] for c in calls}
    assert "+15550000070" in to_numbers


def test_loto_release_no_sms_without_linked_swp(app, client, monkeypatch):
    """Historical LOTO (no SWP link) releases without any SMS."""
    make_user(app, role="admin", username="adm")
    login(client, "adm")

    client.post("/loto/autosave", data={
        "plant": "NV", "date": "2026-06-01",
        "job_description": "Historical", "group_leader": "Admin",
        "is_historical": "y",
    })
    loto_id = _last_loto_id(app)
    # Historical LOTOs skip the "must have SWP" check and go straight to released on activate
    client.post(f"/loto/{loto_id}/activate")

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        client.post(f"/loto/{loto_id}/release")

    mock_client.messages.create.assert_not_called()


# ── E.164 phone validation ────────────────────────────────────────────────────

def test_complete_profile_rejects_non_e164_phone(app, client):
    make_user(app, username="u1")
    login(client, "u1")
    rv = client.post("/complete-profile", data={
        "first_name": "Test", "last_name": "User",
        "email": "t@example.com",
        "phone": "5551234567",   # missing + prefix
        "title": "Tech",
    }, follow_redirects=False)
    assert rv.status_code == 200
    assert b"E.164" in rv.data


def test_complete_profile_accepts_valid_e164(app, client):
    make_user(app, username="u2", must_change_password=0,
              first_name="", last_name="", email="", phone="", title="")
    # Reset profile so gate fires
    conn = raw_db(app)
    conn.execute(
        "UPDATE users SET first_name=NULL, last_name=NULL, email=NULL, phone=NULL, title=NULL WHERE username='u2'"
    )
    conn.commit()
    conn.close()

    login(client, "u2")
    rv = client.post("/complete-profile", data={
        "first_name": "Test", "last_name": "User",
        "email": "t@example.com",
        "phone": "+15551234567",
        "title": "Tech",
    }, follow_redirects=False)
    assert rv.status_code == 302


def test_notify_sms_saved_on_profile(app, client):
    make_user(app, username="u3", must_change_password=0,
              first_name="", last_name="", email="", phone="", title="")
    conn = raw_db(app)
    conn.execute(
        "UPDATE users SET first_name=NULL, last_name=NULL, email=NULL, phone=NULL, title=NULL WHERE username='u3'"
    )
    conn.commit()
    conn.close()

    login(client, "u3")
    client.post("/complete-profile", data={
        "first_name": "Test", "last_name": "User",
        "email": "t@example.com",
        "phone": "+15551234567",
        "title": "Tech",
        "notify_sms": "y",
    })
    conn = raw_db(app)
    row = conn.execute("SELECT notify_sms FROM users WHERE username='u3'").fetchone()
    conn.close()
    assert row["notify_sms"] == 1


# ── First-message compliance footer ──────────────────────────────────────────

def test_first_sms_appends_compliance_footer(app, monkeypatch):
    uid = make_user(app, phone="+15550000080", notify_sms=1)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        with app.app_context():
            from app.notifications import notify_user
            notify_user(uid, "Your SWP NV-SWP-26-0001 has been approved.")

    body = mock_client.messages.create.call_args.kwargs["body"]
    assert "loto.example.com/privacy" in body
    assert "STOP" in body


def test_second_sms_has_no_footer(app, monkeypatch):
    uid = make_user(app, phone="+15550000081", notify_sms=1)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        with app.app_context():
            from app.notifications import notify_user
            notify_user(uid, "First message.")
            notify_user(uid, "Second message.")

    calls = mock_client.messages.create.call_args_list
    assert "loto.example.com/privacy" in calls[0].kwargs["body"]
    assert "loto.example.com/privacy" not in calls[1].kwargs["body"]


def test_first_sms_sent_flag_set_in_db(app, monkeypatch):
    uid = make_user(app, phone="+15550000082", notify_sms=1)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")

    mock_client = MagicMock()
    with patch("twilio.rest.Client", return_value=mock_client):
        with app.app_context():
            from app.notifications import notify_user
            notify_user(uid, "Test.")

    conn = raw_db(app)
    row = conn.execute("SELECT first_sms_sent FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    assert row["first_sms_sent"] == 1


# ── Privacy page ──────────────────────────────────────────────────────────────

def test_privacy_page_accessible_without_login(client):
    rv = client.get("/privacy")
    assert rv.status_code == 200
    assert b"STOP" in rv.data
    assert b"Alkali Systems" in rv.data


def test_privacy_page_accessible_during_onboarding(app, client):
    """User who hasn't completed profile can still reach /privacy."""
    make_user(app, username="onboard_user", first_name="", last_name="",
              email="", phone="", title="", must_change_password=0)
    conn = raw_db(app)
    conn.execute(
        "UPDATE users SET first_name=NULL,last_name=NULL,email=NULL,phone=NULL,title=NULL"
        " WHERE username='onboard_user'"
    )
    conn.commit()
    conn.close()
    login(client, "onboard_user")
    rv = client.get("/privacy")
    assert rv.status_code == 200
