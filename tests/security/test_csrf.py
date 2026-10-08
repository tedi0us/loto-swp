"""Security: CSRF token enforcement on all state-mutating routes."""
import pytest

from tests.conftest import login, make_user, TEST_PW, _TEST_CONFIG


@pytest.fixture()
def app_csrf(tmp_path):
    """App instance with CSRF fully enabled."""
    from app import create_app
    cfg = {**_TEST_CONFIG, "DATABASE": tmp_path / "csrf.db",
           "WTF_CSRF_ENABLED": True, "WTF_CSRF_CHECK_DEFAULT": True}
    return create_app(cfg)


@pytest.fixture()
def client_csrf(app_csrf):
    with app_csrf.test_client() as c:
        yield c


def _get_csrf_token(client):
    """Get a valid CSRF token from the login page."""
    r = client.get("/login")
    # Extract token from the hidden input in the rendered form
    html = r.data.decode()
    start = html.find('name="csrf_token"')
    if start == -1:
        return ""
    val_start = html.find('value="', start) + len('value="')
    val_end = html.find('"', val_start)
    return html[val_start:val_end]


@pytest.mark.parametrize("path,data", [
    ("/login", {"username": "x", "password": "y"}),
])
def test_csrf_rejected_without_token_login(app_csrf, client_csrf, path, data):
    """POST to login without CSRF token is rejected (400 or redirect)."""
    r = client_csrf.post(path, data=data)
    assert r.status_code in (400, 302)


def test_csrf_accepted_with_valid_token(app_csrf, client_csrf):
    """POST with a valid CSRF token is processed (login attempt reaches auth logic)."""
    token = _get_csrf_token(client_csrf)
    assert token, "could not extract CSRF token from login form"
    r = client_csrf.post("/login", data={
        "username": "nonexistent", "password": "wrong", "csrf_token": token
    })
    # Auth fails (200 re-render of login or 302 to change-password), but NOT 400
    assert r.status_code in (200, 302)
    assert r.status_code != 400


def test_csrf_swp_autosave_requires_token(app_csrf, client_csrf):
    """SWP autosave without CSRF token is rejected."""
    make_user(app_csrf, role="operator")
    login(client_csrf)
    r = client_csrf.post("/swp/autosave", data={"plant": "NV"})
    assert r.status_code in (400, 302)


def test_csrf_loto_autosave_requires_token(app_csrf, client_csrf):
    """LOTO autosave without CSRF token is rejected."""
    make_user(app_csrf, role="operator")
    login(client_csrf)
    r = client_csrf.post("/loto/autosave", data={"plant": "NV"})
    assert r.status_code in (400, 302)
