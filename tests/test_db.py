"""Tests for DB initialisation, migrations, and schema integrity."""
import sqlite3

import pytest

from tests.conftest import _TEST_CONFIG

EXPECTED_TABLES = {
    "users", "audit_log", "swp_permits", "swp_attendees",
    "contractors", "loto_permits", "loto_boundaries", "loto_verifiers",
    "equipment", "equipment_work_log",
}


def _tables(db_path):
    conn = sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    return names


def _schema(db_path, table):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    conn.close()
    return row[0] if row else ""


def test_all_tables_created(app):
    assert EXPECTED_TABLES.issubset(_tables(app.config["DATABASE"]))


def test_init_db_idempotent(app):
    """Calling init_db a second time on an existing DB must not raise."""
    with app.app_context():
        from app.db import init_db
        init_db(app)
    assert EXPECTED_TABLES.issubset(_tables(app.config["DATABASE"]))


def test_no_stale_old_references(app):
    """No table DDL should still reference *_old tables after init."""
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    stale = conn.execute(
        """SELECT name FROM sqlite_master
           WHERE type='table'
             AND (sql LIKE '%users_old%'
                  OR sql LIKE '%loto_permits_old%'
                  OR sql LIKE '%swp_permits_old%')"""
    ).fetchall()
    conn.close()
    assert stale == [], f"stale FK refs in: {[r[0] for r in stale]}"


def test_fk_repair_patches_broken_schema(tmp_path):
    """init_db() must fix pre-existing *_old FK references via writable_schema."""
    db_path = tmp_path / "broken.db"
    # Manually create a DB that looks like a post-migration prod DB but has a
    # stale FK reference in audit_log pointing at "users_old" instead of "users".
    conn = sqlite3.connect(str(db_path))
    # Full current users schema — no user migrations should fire
    conn.execute(
        "CREATE TABLE users ("
        "  id                   INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  username             TEXT UNIQUE NOT NULL,"
        "  password_hash        TEXT NOT NULL,"
        "  role                 TEXT NOT NULL DEFAULT 'user'"
        "    CHECK(role IN ('it_admin','admin','operator','mechanic','ei_c','user')),"
        "  must_change_password INTEGER NOT NULL DEFAULT 0,"
        "  first_name TEXT, last_name TEXT, email TEXT,"
        "  phone TEXT, title TEXT, plant TEXT,"
        "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    # audit_log with full schema but broken FK to users_old
    conn.execute(
        'CREATE TABLE audit_log ('
        '  id          INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,'
        '  user_id     INTEGER REFERENCES "users_old"(id) ON DELETE SET NULL,'
        '  username    TEXT,'
        '  ip_address  TEXT,'
        '  user_agent  TEXT,'
        '  action      TEXT NOT NULL DEFAULT \'\','
        '  resource    TEXT,'
        '  resource_id TEXT,'
        '  detail      TEXT,'
        '  success     INTEGER NOT NULL DEFAULT 1'
        ')'
    )
    conn.commit()
    conn.close()

    from app import create_app
    create_app({**_TEST_CONFIG, "DATABASE": db_path})

    sql = _schema(db_path, "audit_log")
    assert "users_old" not in sql
    assert "users" in sql


def test_loto_permits_has_pending_status(app):
    sql = _schema(app.config["DATABASE"], "loto_permits")
    assert "'pending'" in sql


def test_equip_id_columns_exist(app):
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    loto_cols = {r[1] for r in conn.execute("PRAGMA table_info(loto_permits)")}
    swp_cols  = {r[1] for r in conn.execute("PRAGMA table_info(swp_permits)")}
    conn.close()
    assert "equip_id" in loto_cols
    assert "equip_id" in swp_cols


def test_users_role_check_includes_new_roles(app):
    sql = _schema(app.config["DATABASE"], "users")
    for role in ("operator", "mechanic", "ei_c"):
        assert role in sql, f"role '{role}' missing from users CHECK"


def test_swp_permits_has_issued_closed_name_columns(app):
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(swp_permits)")}
    conn.close()
    for col in ("issued_auth_name", "issued_wl_name", "closed_auth_name", "closed_wl_name"):
        assert col in cols, f"column '{col}' missing from swp_permits"


def test_swp_name_columns_migration_idempotent(app):
    """Re-running init_db when name columns already exist must not raise."""
    with app.app_context():
        from app.db import init_db
        init_db(app)
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(swp_permits)")}
    conn.close()
    for col in ("issued_auth_name", "issued_wl_name", "closed_auth_name", "closed_wl_name"):
        assert col in cols
