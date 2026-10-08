import logging
import os

_log = logging.getLogger(__name__)


def send_sms(phone: str, body: str) -> None:
    """Send an SMS via Twilio. Silently skips if env vars are not set."""
    sid   = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_ = os.environ.get("TWILIO_FROM_NUMBER")
    if not (sid and token and from_):
        _log.debug("Twilio not configured — SMS skipped to %s", phone)
        return
    try:
        from twilio.rest import Client
        Client(sid, token).messages.create(body=body, from_=from_, to=phone)
    except Exception as exc:
        _log.error("SMS send failed to %s: %s", phone, exc)


_COMPLIANCE_FOOTER = (
    " Terms & Privacy: https://loto.example.com/privacy"
    " Reply STOP to unsubscribe."
)


def notify_user(user_id: int, body: str) -> None:
    """Send SMS to a user if they have opted in and have a phone number.

    Appends the compliance footer (T&C / opt-out) on the first message ever
    sent to this user, then marks first_sms_sent=1 so it is not repeated.
    """
    from app.db import get_db
    db = get_db()
    row = db.execute(
        "SELECT phone, notify_sms, first_sms_sent FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not (row and row["notify_sms"] and row["phone"]):
        return
    if not row["first_sms_sent"]:
        body = body + _COMPLIANCE_FOOTER
        db.execute("UPDATE users SET first_sms_sent=1 WHERE id=?", (user_id,))
        db.commit()
    send_sms(row["phone"], body)


def notify_admins(body: str) -> None:
    """Send SMS to all admin and it_admin users who have opted in."""
    from app.db import get_db
    rows = get_db().execute(
        """SELECT id FROM users
           WHERE role IN ('admin','it_admin') AND notify_sms=1
             AND phone IS NOT NULL AND phone != ''"""
    ).fetchall()
    for row in rows:
        notify_user(row["id"], body)
