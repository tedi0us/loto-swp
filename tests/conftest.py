import sqlite3

import pytest
from argon2 import PasswordHasher

TEST_PW = "testpass123"
_ph = PasswordHasher()
TEST_PW_HASH = _ph.hash(TEST_PW)

_TEST_CONFIG = {
    "TESTING": True,
    "COMPANY_NAME": "Acme Energy",
    "WTF_CSRF_ENABLED": False,
    "RATELIMIT_ENABLED": False,
    "SECRET_KEY": "test-secret-not-random",
}


@pytest.fixture()
def app(tmp_path):
    from app import create_app
    application = create_app({**_TEST_CONFIG, "DATABASE": tmp_path / "test.db"})
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


# ── Helpers (importable by test modules) ──────────────────────────────────────

def make_user(app, role="operator", username="testuser", password=None,
              first_name="Test", last_name="User",
              email="test@example.com", phone="+15551234567",
              title="Technician", must_change_password=0, plant="NV",
              notify_sms=0):
    """Insert a user directly into the test DB. Returns the new user id."""
    pw_hash = _ph.hash(password) if password else TEST_PW_HASH
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    conn.execute(
        """INSERT INTO users
               (username, password_hash, role, must_change_password,
                first_name, last_name, email, phone, title, plant, notify_sms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, pw_hash, role, must_change_password,
         first_name, last_name, email, phone, title, plant, notify_sms),
    )
    conn.commit()
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return uid


def login(client, username="testuser", password=None):
    """POST to /login and return the response (no redirect follow)."""
    return client.post(
        "/login",
        data={"username": username, "password": password or TEST_PW},
        follow_redirects=False,
    )


def raw_db(app):
    """Open a raw sqlite3 connection to the test DB for post-request inspection."""
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    conn.row_factory = sqlite3.Row
    return conn
