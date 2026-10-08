"""Security: HTTP security headers on every response."""
import pytest

from tests.conftest import login, make_user


REQUIRED_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def test_security_headers_on_login_page(client):
    r = client.get("/login")
    for header, expected in REQUIRED_HEADERS.items():
        assert r.headers.get(header) == expected, (
            f"Header {header!r} expected {expected!r}, got {r.headers.get(header)!r}"
        )


def test_security_headers_on_dashboard(app, client):
    make_user(app)
    login(client)
    r = client.get("/dashboard")
    for header, expected in REQUIRED_HEADERS.items():
        assert r.headers.get(header) == expected, (
            f"Header {header!r} missing on /dashboard"
        )


def test_csp_header_present_and_restrictive(client):
    r = client.get("/login")
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp, "Content-Security-Policy header must be set"
    assert "default-src 'self'" in csp, "CSP must include default-src 'self'"
    # script-src must not include 'unsafe-eval' or broad wildcards
    assert "unsafe-eval" not in csp


def test_x_frame_options_deny_on_protected_route(app, client):
    make_user(app)
    login(client)
    r = client.get("/swp/")
    assert r.headers.get("X-Frame-Options") == "DENY"
