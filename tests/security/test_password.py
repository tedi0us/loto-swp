"""Security: password storage uses Argon2id; plaintext never appears in DB."""
import sqlite3

import pytest
from argon2 import PasswordHasher

from tests.conftest import make_user, login, TEST_PW


def test_password_stored_as_argon2id(app):
    make_user(app)
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    row = conn.execute("SELECT password_hash FROM users LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert row[0].startswith("$argon2id$"), (
        "Password must be hashed with Argon2id"
    )


def test_plaintext_not_in_db(app):
    make_user(app, password="supersecretpassword")
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    for row in rows:
        for cell in row:
            assert "supersecretpassword" not in str(cell), (
                "Plaintext password found in database"
            )


def test_wrong_password_rejected_with_200_not_redirect(app, client):
    """Failed login re-renders the form (200) rather than redirecting."""
    make_user(app)
    r = client.post(
        "/login",
        data={"username": "testuser", "password": "WRONG_PASSWORD"},
        follow_redirects=False,
    )
    # Login failure must re-render the form, not redirect to dashboard
    assert r.status_code == 200
    assert b"Invalid" in r.data or b"incorrect" in r.data or b"login" in r.data.lower()


def test_correct_password_redirects(app, client):
    """Correct credentials result in a redirect (302) to dashboard or onboarding."""
    make_user(app)
    r = login(client)
    assert r.status_code == 302
