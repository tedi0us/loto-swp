"""Tests for login, logout, and onboarding gates."""
from tests.conftest import TEST_PW, make_user, login


def test_login_page_loads(client):
    assert client.get("/login").status_code == 200


def test_login_success_redirects(app, client):
    make_user(app)
    rv = login(client)
    assert rv.status_code == 302
    assert "/dashboard" in rv.headers["Location"] or rv.headers["Location"] == "/"


def test_login_wrong_password(app, client):
    make_user(app)
    rv = client.post("/login", data={"username": "testuser", "password": "wrong"},
                     follow_redirects=False)
    assert rv.status_code == 200
    assert b"Invalid" in rv.data or b"incorrect" in rv.data.lower()


def test_login_unknown_user(client):
    rv = client.post("/login", data={"username": "nobody", "password": "x"},
                     follow_redirects=False)
    assert rv.status_code == 200


def test_session_persists_after_login(app, client):
    make_user(app)
    login(client)
    rv = client.get("/dashboard")
    assert rv.status_code == 200


def test_logout_clears_session(app, client):
    make_user(app)
    login(client)
    rv = client.post("/logout", follow_redirects=False)
    assert rv.status_code == 302
    # After logout, /dashboard should redirect to login
    rv2 = client.get("/dashboard", follow_redirects=False)
    assert rv2.status_code == 302
    assert "login" in rv2.headers["Location"]


def test_must_change_password_gate(app, client):
    make_user(app, must_change_password=1)
    login(client)
    rv = client.get("/dashboard", follow_redirects=False)
    assert rv.status_code == 302
    assert "change-password" in rv.headers["Location"]


def test_profile_incomplete_gate(app, client):
    make_user(app, first_name="", last_name="")
    login(client)
    rv = client.get("/dashboard", follow_redirects=False)
    assert rv.status_code == 302
    assert "complete-profile" in rv.headers["Location"]


def test_login_page_exempt_from_gates(app, client):
    """Unauthenticated GET /login must not redirect to onboarding."""
    rv = client.get("/login", follow_redirects=False)
    assert rv.status_code == 200
