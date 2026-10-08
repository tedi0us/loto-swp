"""Security: rate limiting on login endpoint."""
import pytest

from tests.conftest import _TEST_CONFIG


@pytest.fixture()
def app_rl(tmp_path):
    """App with rate limiting enabled."""
    from app import create_app
    cfg = {**_TEST_CONFIG, "DATABASE": tmp_path / "rl.db",
           "RATELIMIT_ENABLED": True,
           "RATELIMIT_STORAGE_URI": "memory://",
           "RATELIMIT_DEFAULT": "200 per day"}
    return create_app(cfg)


@pytest.fixture()
def client_rl(app_rl):
    with app_rl.test_client() as c:
        yield c


def test_login_rate_limit_triggers_after_threshold(app_rl, client_rl):
    """11th failed login attempt to the same endpoint should return 429."""
    for i in range(10):
        client_rl.post("/login", data={"username": "x", "password": f"wrong{i}"})
    r = client_rl.post("/login", data={"username": "x", "password": "wrong11"})
    assert r.status_code == 429, (
        "Expected 429 after exceeding 10/minute login limit, got "
        f"{r.status_code}"
    )
