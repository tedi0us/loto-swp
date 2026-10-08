#!/usr/bin/env python3
"""Create a user from the command line (e.g. the first IT admin).

    python scripts/create_user.py
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from argon2 import PasswordHasher  # noqa: E402
from app import create_app  # noqa: E402
from app.db import get_db  # noqa: E402
from app.roles import ROLES  # noqa: E402


def main():
    username = input("Username: ").strip()
    if not username:
        sys.exit("Username cannot be empty.")

    print(f"Role options: {', '.join(ROLES)}")
    role = input("Role: ").strip().lower()
    if role not in ROLES:
        sys.exit(f"Invalid role. Choose from: {', '.join(ROLES)}")

    password = getpass.getpass("Password: ")
    confirm  = getpass.getpass("Confirm password: ")
    if password != confirm:
        sys.exit("Passwords do not match.")
    if len(password) < 12:
        sys.exit("Password must be at least 12 characters.")

    temp = input("Require password change on first login? [Y/n]: ").strip().lower()
    must_change = 0 if temp == "n" else 1

    app = create_app()
    with app.app_context():
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, ?, ?)",
                (username, PasswordHasher().hash(password), role, must_change),
            )
            db.commit()
            print(f"User '{username}' created with role '{role}'." +
                  (" Password change required on first login." if must_change else ""))
        except Exception as e:
            sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
