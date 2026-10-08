import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _secret_key():
    if key := os.environ.get("SECRET_KEY"):
        return key
    key_file = BASE_DIR / "instance" / "secret_key"
    key_file.parent.mkdir(exist_ok=True)
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


class Config:
    SECRET_KEY = _secret_key()
    DATABASE = BASE_DIR / "instance" / "app.db"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"
    PREFERRED_URL_SCHEME = "https" if os.environ.get("FLASK_ENV") == "production" else "http"
    WTF_CSRF_TIME_LIMIT = 3600
    COMPANY_NAME = os.environ.get("COMPANY_NAME", "")
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_HEADERS_ENABLED = True
