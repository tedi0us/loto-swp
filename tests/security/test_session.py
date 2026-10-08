"""Security: session cookie properties and invalidation on logout."""
import pytest

from tests.conftest import login, make_user, TEST_PW


def test_session_cookie_httponly(app, client):
    """Session cookie must have HttpOnly flag."""
    make_user(app)
    r = login(client)
    assert r.status_code == 302
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower(), (
        "Session cookie must be HttpOnly"
    )


def test_session_cookie_samesite(app, client):
    """Session cookie should have SameSite attribute."""
    make_user(app)
    r = login(client)
    assert r.status_code == 302
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "SameSite" in set_cookie or "samesite" in set_cookie.lower(), (
        "Session cookie should have SameSite attribute"
    )


def test_session_invalidated_after_logout(app, client):
    """After logout, previously protected routes must redirect to login."""
    make_user(app)
    login(client)
    # Confirm we're logged in
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 200

    # Logout
    client.post("/logout")
    # Now try to access a protected route
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_old_session_rejected_after_logout(app, client):
    """After logout the same client has no access to protected routes."""
    make_user(app)
    login(client)

    client.post("/logout")

    # Post-logout: the session cookie has been cleared by Flask-Login;
    # any subsequent request must be denied.
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
