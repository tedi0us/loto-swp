import sqlite3
from flask import g


def get_db():
    if "db" not in g:
        from flask import current_app
        db_path = current_app.config["DATABASE"]
        g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    db_path = app.config["DATABASE"]
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # ── Repair broken FK references from prior migrations ─────────────────────
    # SQLite 3.26.0+ auto-updates FK references when a table is renamed.
    # Earlier migrations didn't set PRAGMA legacy_alter_table=ON, so tables like
    # audit_log ended up referencing users_old/loto_permits_old/swp_permits_old
    # which were subsequently dropped.  Patch the stored DDL directly.
    broken = conn.execute(
        """SELECT COUNT(*) FROM sqlite_master
           WHERE type IN ('table','trigger')
             AND (sql LIKE '%"users_old"%'
                  OR sql LIKE '%"loto_permits_old"%'
                  OR sql LIKE '%"swp_permits_old"%')"""
    ).fetchone()[0]
    if broken:
        conn.execute("PRAGMA writable_schema=ON")
        for stale, fixed in [
            ('"users_old"',        'users'),
            ('"loto_permits_old"', 'loto_permits'),
            ('"swp_permits_old"',  'swp_permits'),
        ]:
            conn.execute(
                "UPDATE sqlite_master SET sql = REPLACE(sql, ?, ?) "
                "WHERE type IN ('table','trigger') AND sql LIKE ?",
                (stale, fixed, f'%{stale}%'),
            )
        schema_ver = conn.execute("PRAGMA schema_version").fetchone()[0]
        conn.execute(f"PRAGMA schema_version = {schema_ver + 1}")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()

    # Core tables that don't need migration logic
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            username             TEXT UNIQUE NOT NULL,
            password_hash        TEXT NOT NULL,
            role                 TEXT NOT NULL DEFAULT 'user'
                                   CHECK(role IN ('it_admin', 'admin', 'user')),
            must_change_password INTEGER NOT NULL DEFAULT 0,
            first_name           TEXT,
            last_name            TEXT,
            email                TEXT,
            phone                TEXT,
            title                TEXT,
            created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
            username    TEXT,
            ip_address  TEXT,
            user_agent  TEXT,
            action      TEXT NOT NULL,
            resource    TEXT,
            resource_id TEXT,
            detail      TEXT,
            success     INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_user      ON audit_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_action    ON audit_log(action);
    """)

    # Migrations: add columns to users if upgrading from older schema
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if "must_change_password" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    for col in ("first_name", "last_name", "email", "phone", "title", "plant"):
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")

    # Expand users CHECK constraint to include new field roles
    users_sql = (conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone() or ("",))[0]
    if "'operator'" not in users_sql:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript("""
            PRAGMA legacy_alter_table=ON;
            ALTER TABLE users RENAME TO users_old;
            CREATE TABLE users (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                username             TEXT UNIQUE NOT NULL,
                password_hash        TEXT NOT NULL,
                role                 TEXT NOT NULL DEFAULT 'user'
                                       CHECK(role IN ('it_admin','admin','operator','mechanic','ei_c','user')),
                must_change_password INTEGER NOT NULL DEFAULT 0,
                first_name           TEXT,
                last_name            TEXT,
                email                TEXT,
                phone                TEXT,
                title                TEXT,
                plant                TEXT,
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO users SELECT id,username,password_hash,role,must_change_password,
                first_name,last_name,email,phone,title,plant,created_at FROM users_old;
            DROP TABLE users_old;
        """)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()

    # ── swp_permits migration ──────────────────────────────────────────────────
    # Check if swp_permits exists and whether it has 'pending' in the CHECK constraint
    existing_swp = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='swp_permits'"
    ).fetchone()

    needs_full_migration = False
    if existing_swp is not None:
        table_sql = existing_swp[0] or ""
        # If the CHECK constraint doesn't include 'pending', we need a full migration
        if "'pending'" not in table_sql and "pending" not in table_sql:
            needs_full_migration = True

    if needs_full_migration:
        # Full table rename + recreate + copy + drop migration
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript("""
            PRAGMA legacy_alter_table=ON;
            ALTER TABLE swp_permits RENAME TO swp_permits_old;

            CREATE TABLE swp_permits (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                permit_number         TEXT,
                plant                 TEXT,
                date                  TEXT,
                time                  TEXT,
                location              TEXT,
                company               TEXT,
                work_lead             TEXT,
                scope_of_work         TEXT,
                permit_loto           TEXT,
                permit_hot_work       TEXT,
                permit_electrical     TEXT,
                permit_confined_sp    TEXT,
                permit_excavation     TEXT,
                permit_other          TEXT,
                first_time            INTEGER,
                training              INTEGER,
                inspection            INTEGER,
                continuation          INTEGER,
                he_electrical         INTEGER DEFAULT 0,
                he_mechanical         INTEGER DEFAULT 0,
                he_thermal            INTEGER DEFAULT 0,
                he_pressure           INTEGER DEFAULT 0,
                he_motion             INTEGER DEFAULT 0,
                he_gravity            INTEGER DEFAULT 0,
                he_chemical           INTEGER DEFAULT 0,
                he_radiation          INTEGER DEFAULT 0,
                he_biological         INTEGER DEFAULT 0,
                he_noise              INTEGER DEFAULT 0,
                ppe_hard_hat          INTEGER DEFAULT 0,
                ppe_safety_glasses    INTEGER DEFAULT 0,
                ppe_gloves            INTEGER DEFAULT 0,
                ppe_fr_clothing       INTEGER DEFAULT 0,
                ppe_arc_flash_suit    INTEGER DEFAULT 0,
                ppe_arc_flash_hood    INTEGER DEFAULT 0,
                ppe_insul_gloves      INTEGER DEFAULT 0,
                ppe_face_shield       INTEGER DEFAULT 0,
                ppe_hearing_prot      INTEGER DEFAULT 0,
                ppe_respirator        INTEGER DEFAULT 0,
                ppe_safety_shoes      INTEGER DEFAULT 0,
                ppe_fall_protect      INTEGER DEFAULT 0,
                ppe_cut_protect       INTEGER DEFAULT 0,
                ppe_welding_hood      INTEGER DEFAULT 0,
                ppe_brine_gear        INTEGER DEFAULT 0,
                ppe_chem_res_cloth    INTEGER DEFAULT 0,
                ppe_chem_res_gloves   INTEGER DEFAULT 0,
                ppe_chem_res_boot     INTEGER DEFAULT 0,
                ppe_other1            INTEGER DEFAULT 0,
                ppe_other1_txt        TEXT,
                ppe_other2            INTEGER DEFAULT 0,
                ppe_other2_txt        TEXT,
                tha_loto              INTEGER DEFAULT 0,
                tha_confined_space    INTEGER DEFAULT 0,
                tha_elevated_work     INTEGER DEFAULT 0,
                tha_hot_work          INTEGER DEFAULT 0,
                tha_vehicles          INTEGER DEFAULT 0,
                tha_mobile_equip      INTEGER DEFAULT 0,
                tha_heavy_equip       INTEGER DEFAULT 0,
                tha_power_tools       INTEGER DEFAULT 0,
                tha_slips_trips       INTEGER DEFAULT 0,
                tha_nip_pinch         INTEGER DEFAULT 0,
                tha_break_sys_integ   INTEGER DEFAULT 0,
                tha_ergonomics        INTEGER DEFAULT 0,
                tha_extreme_heat      INTEGER DEFAULT 0,
                tha_extreme_cold      INTEGER DEFAULT 0,
                tha_high_wind         INTEGER DEFAULT 0,
                tha_rain_snow         INTEGER DEFAULT 0,
                tha_other             INTEGER DEFAULT 0,
                tha_other_txt         TEXT,
                ts_review_procedures  INTEGER DEFAULT 0,
                ts_valve_positions    INTEGER DEFAULT 0,
                ts_cross_connects     INTEGER DEFAULT 0,
                ts_barricade          INTEGER DEFAULT 0,
                ts_adjacent_work      INTEGER DEFAULT 0,
                ts_haz_comm           INTEGER DEFAULT 0,
                ts_addl_permits       INTEGER DEFAULT 0,
                ts_equip_inspect      INTEGER DEFAULT 0,
                ts_high_temp_lines    INTEGER DEFAULT 0,
                ts_min_approach       INTEGER DEFAULT 0,
                ts_safety_observer    INTEGER DEFAULT 0,
                ts_slings_lifting     INTEGER DEFAULT 0,
                ts_other1             INTEGER DEFAULT 0,
                ts_other1_txt         TEXT,
                ts_other2             INTEGER DEFAULT 0,
                ts_other2_txt         TEXT,
                hazard_mitigation     TEXT,
                additional_comments   TEXT,
                issued_auth_sig       TEXT,
                issued_auth_name      TEXT,
                issued_wl_sig         TEXT,
                issued_wl_name        TEXT,
                issued_date           TEXT,
                issued_time           TEXT,
                closed_auth_sig       TEXT,
                closed_auth_name      TEXT,
                closed_wl_sig         TEXT,
                closed_wl_name        TEXT,
                closed_date           TEXT,
                closed_time           TEXT,
                pjsm_leader           TEXT,
                pjsm_leader_sig       TEXT,
                ehst_name             TEXT,
                ehst_signature        TEXT,
                ehst_date             TEXT,
                status                TEXT DEFAULT 'draft'
                                        CHECK(status IN ('draft','pending','issued','closed')),
                is_backdated          INTEGER NOT NULL DEFAULT 0,
                backdate_note         TEXT,
                submitted_at          TEXT,
                approved_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
                approved_at           TEXT,
                created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO swp_permits (
                id, permit_number, plant, date, time, location, company, work_lead,
                scope_of_work, permit_loto, permit_hot_work, permit_electrical,
                permit_confined_sp, permit_excavation, permit_other,
                first_time, training, inspection, continuation,
                he_electrical, he_mechanical, he_thermal, he_pressure, he_motion,
                he_gravity, he_chemical, he_radiation, he_biological, he_noise,
                ppe_hard_hat, ppe_safety_glasses, ppe_gloves, ppe_fr_clothing,
                ppe_arc_flash_suit, ppe_arc_flash_hood, ppe_insul_gloves, ppe_face_shield,
                ppe_hearing_prot, ppe_respirator, ppe_safety_shoes, ppe_fall_protect,
                ppe_cut_protect, ppe_welding_hood, ppe_brine_gear, ppe_chem_res_cloth,
                ppe_chem_res_gloves, ppe_chem_res_boot, ppe_other1, ppe_other1_txt,
                ppe_other2, ppe_other2_txt,
                tha_loto, tha_confined_space, tha_elevated_work, tha_hot_work,
                tha_vehicles, tha_mobile_equip, tha_heavy_equip, tha_power_tools,
                tha_slips_trips, tha_nip_pinch, tha_break_sys_integ, tha_ergonomics,
                tha_extreme_heat, tha_extreme_cold, tha_high_wind, tha_rain_snow,
                tha_other, tha_other_txt,
                ts_review_procedures, ts_valve_positions, ts_cross_connects, ts_barricade,
                ts_adjacent_work, ts_haz_comm, ts_addl_permits, ts_equip_inspect,
                ts_high_temp_lines, ts_min_approach, ts_safety_observer, ts_slings_lifting,
                ts_other1, ts_other1_txt, ts_other2, ts_other2_txt,
                hazard_mitigation, additional_comments,
                issued_auth_sig, issued_auth_name, issued_wl_sig, issued_wl_name,
                issued_date, issued_time,
                closed_auth_sig, closed_auth_name, closed_wl_sig, closed_wl_name,
                closed_date, closed_time,
                pjsm_leader, pjsm_leader_sig, ehst_name, ehst_signature, ehst_date,
                status, is_backdated, backdate_note,
                submitted_at, approved_by, approved_at,
                created_by, created_at, updated_at
            )
            SELECT
                id, permit_number, NULL, date, time, location, company, work_lead,
                scope_of_work, permit_loto, permit_hot_work, permit_electrical,
                permit_confined_sp, permit_excavation, permit_other,
                first_time, training, inspection, continuation,
                he_electrical, he_mechanical, he_thermal, he_pressure, he_motion,
                he_gravity, he_chemical, he_radiation, he_biological, he_noise,
                ppe_hard_hat, ppe_safety_glasses, ppe_gloves, ppe_fr_clothing,
                ppe_arc_flash_suit, ppe_arc_flash_hood, ppe_insul_gloves, ppe_face_shield,
                ppe_hearing_prot, ppe_respirator, ppe_safety_shoes, ppe_fall_protect,
                ppe_cut_protect, ppe_welding_hood, ppe_brine_gear, ppe_chem_res_cloth,
                ppe_chem_res_gloves, ppe_chem_res_boot, ppe_other1, ppe_other1_txt,
                ppe_other2, ppe_other2_txt,
                tha_loto, tha_confined_space, tha_elevated_work, tha_hot_work,
                tha_vehicles, tha_mobile_equip, tha_heavy_equip, tha_power_tools,
                tha_slips_trips, tha_nip_pinch, tha_break_sys_integ, tha_ergonomics,
                tha_extreme_heat, tha_extreme_cold, tha_high_wind, tha_rain_snow,
                tha_other, tha_other_txt,
                ts_review_procedures, ts_valve_positions, ts_cross_connects, ts_barricade,
                ts_adjacent_work, ts_haz_comm, ts_addl_permits, ts_equip_inspect,
                ts_high_temp_lines, ts_min_approach, ts_safety_observer, ts_slings_lifting,
                ts_other1, ts_other1_txt, ts_other2, ts_other2_txt,
                hazard_mitigation, additional_comments,
                issued_auth_sig, NULL, issued_wl_sig, NULL, issued_date, issued_time,
                closed_auth_sig, NULL, closed_wl_sig, NULL, closed_date, closed_time,
                pjsm_leader, pjsm_leader_sig, ehst_name, ehst_signature, ehst_date,
                status, is_backdated, backdate_note,
                NULL, NULL, NULL,
                created_by, created_at, updated_at
            FROM swp_permits_old;

            DROP TABLE swp_permits_old;
        """)
        conn.execute("PRAGMA foreign_keys=ON")

    elif existing_swp is None:
        # Fresh install: create swp_permits from scratch
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS swp_permits (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                permit_number         TEXT,
                plant                 TEXT,
                date                  TEXT,
                time                  TEXT,
                location              TEXT,
                company               TEXT,
                work_lead             TEXT,
                scope_of_work         TEXT,
                permit_loto           TEXT,
                permit_hot_work       TEXT,
                permit_electrical     TEXT,
                permit_confined_sp    TEXT,
                permit_excavation     TEXT,
                permit_other          TEXT,
                first_time            INTEGER,
                training              INTEGER,
                inspection            INTEGER,
                continuation          INTEGER,
                he_electrical         INTEGER DEFAULT 0,
                he_mechanical         INTEGER DEFAULT 0,
                he_thermal            INTEGER DEFAULT 0,
                he_pressure           INTEGER DEFAULT 0,
                he_motion             INTEGER DEFAULT 0,
                he_gravity            INTEGER DEFAULT 0,
                he_chemical           INTEGER DEFAULT 0,
                he_radiation          INTEGER DEFAULT 0,
                he_biological         INTEGER DEFAULT 0,
                he_noise              INTEGER DEFAULT 0,
                ppe_hard_hat          INTEGER DEFAULT 0,
                ppe_safety_glasses    INTEGER DEFAULT 0,
                ppe_gloves            INTEGER DEFAULT 0,
                ppe_fr_clothing       INTEGER DEFAULT 0,
                ppe_arc_flash_suit    INTEGER DEFAULT 0,
                ppe_arc_flash_hood    INTEGER DEFAULT 0,
                ppe_insul_gloves      INTEGER DEFAULT 0,
                ppe_face_shield       INTEGER DEFAULT 0,
                ppe_hearing_prot      INTEGER DEFAULT 0,
                ppe_respirator        INTEGER DEFAULT 0,
                ppe_safety_shoes      INTEGER DEFAULT 0,
                ppe_fall_protect      INTEGER DEFAULT 0,
                ppe_cut_protect       INTEGER DEFAULT 0,
                ppe_welding_hood      INTEGER DEFAULT 0,
                ppe_brine_gear        INTEGER DEFAULT 0,
                ppe_chem_res_cloth    INTEGER DEFAULT 0,
                ppe_chem_res_gloves   INTEGER DEFAULT 0,
                ppe_chem_res_boot     INTEGER DEFAULT 0,
                ppe_other1            INTEGER DEFAULT 0,
                ppe_other1_txt        TEXT,
                ppe_other2            INTEGER DEFAULT 0,
                ppe_other2_txt        TEXT,
                tha_loto              INTEGER DEFAULT 0,
                tha_confined_space    INTEGER DEFAULT 0,
                tha_elevated_work     INTEGER DEFAULT 0,
                tha_hot_work          INTEGER DEFAULT 0,
                tha_vehicles          INTEGER DEFAULT 0,
                tha_mobile_equip      INTEGER DEFAULT 0,
                tha_heavy_equip       INTEGER DEFAULT 0,
                tha_power_tools       INTEGER DEFAULT 0,
                tha_slips_trips       INTEGER DEFAULT 0,
                tha_nip_pinch         INTEGER DEFAULT 0,
                tha_break_sys_integ   INTEGER DEFAULT 0,
                tha_ergonomics        INTEGER DEFAULT 0,
                tha_extreme_heat      INTEGER DEFAULT 0,
                tha_extreme_cold      INTEGER DEFAULT 0,
                tha_high_wind         INTEGER DEFAULT 0,
                tha_rain_snow         INTEGER DEFAULT 0,
                tha_other             INTEGER DEFAULT 0,
                tha_other_txt         TEXT,
                ts_review_procedures  INTEGER DEFAULT 0,
                ts_valve_positions    INTEGER DEFAULT 0,
                ts_cross_connects     INTEGER DEFAULT 0,
                ts_barricade          INTEGER DEFAULT 0,
                ts_adjacent_work      INTEGER DEFAULT 0,
                ts_haz_comm           INTEGER DEFAULT 0,
                ts_addl_permits       INTEGER DEFAULT 0,
                ts_equip_inspect      INTEGER DEFAULT 0,
                ts_high_temp_lines    INTEGER DEFAULT 0,
                ts_min_approach       INTEGER DEFAULT 0,
                ts_safety_observer    INTEGER DEFAULT 0,
                ts_slings_lifting     INTEGER DEFAULT 0,
                ts_other1             INTEGER DEFAULT 0,
                ts_other1_txt         TEXT,
                ts_other2             INTEGER DEFAULT 0,
                ts_other2_txt         TEXT,
                hazard_mitigation     TEXT,
                additional_comments   TEXT,
                issued_auth_sig       TEXT,
                issued_auth_name      TEXT,
                issued_wl_sig         TEXT,
                issued_wl_name        TEXT,
                issued_date           TEXT,
                issued_time           TEXT,
                closed_auth_sig       TEXT,
                closed_auth_name      TEXT,
                closed_wl_sig         TEXT,
                closed_wl_name        TEXT,
                closed_date           TEXT,
                closed_time           TEXT,
                pjsm_leader           TEXT,
                pjsm_leader_sig       TEXT,
                ehst_name             TEXT,
                ehst_signature        TEXT,
                ehst_date             TEXT,
                status                TEXT DEFAULT 'draft'
                                        CHECK(status IN ('draft','pending','issued','closed')),
                is_backdated          INTEGER NOT NULL DEFAULT 0,
                backdate_note         TEXT,
                submitted_at          TEXT,
                approved_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
                approved_at           TEXT,
                created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
    else:
        # Table exists and already has pending — add any missing new columns
        swp_cols = {row[1] for row in conn.execute("PRAGMA table_info(swp_permits)")}
        if "plant" not in swp_cols:
            conn.execute("ALTER TABLE swp_permits ADD COLUMN plant TEXT")
        if "submitted_at" not in swp_cols:
            conn.execute("ALTER TABLE swp_permits ADD COLUMN submitted_at TEXT")
        if "approved_by" not in swp_cols:
            conn.execute("ALTER TABLE swp_permits ADD COLUMN approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
        if "approved_at" not in swp_cols:
            conn.execute("ALTER TABLE swp_permits ADD COLUMN approved_at TEXT")

    # Also add is_backdated / backdate_note to any schema that might be missing them
    swp_cols = {row[1] for row in conn.execute("PRAGMA table_info(swp_permits)")}
    if "is_backdated" not in swp_cols:
        conn.execute("ALTER TABLE swp_permits ADD COLUMN is_backdated INTEGER NOT NULL DEFAULT 0")
    if "backdate_note" not in swp_cols:
        conn.execute("ALTER TABLE swp_permits ADD COLUMN backdate_note TEXT")

    # Issued/closed name columns: authorizer and work lead names for each signature
    swp_cols = {row[1] for row in conn.execute("PRAGMA table_info(swp_permits)")}
    for col in ("issued_auth_name", "issued_wl_name", "closed_auth_name", "closed_wl_name"):
        if col not in swp_cols:
            conn.execute(f"ALTER TABLE swp_permits ADD COLUMN {col} TEXT")

    # ── swp_attendees ──────────────────────────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS swp_attendees (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            swp_id    INTEGER NOT NULL REFERENCES swp_permits(id) ON DELETE CASCADE,
            position  INTEGER NOT NULL,
            name      TEXT,
            company   TEXT,
            signature TEXT,
            date      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_swp_created_by ON swp_permits(created_by);
        CREATE INDEX IF NOT EXISTS idx_swp_status     ON swp_permits(status);
        CREATE INDEX IF NOT EXISTS idx_swp_attendees  ON swp_attendees(swp_id);
    """)

    # ── contractors ───────────────────────────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS contractors (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            company    TEXT,
            title      TEXT,
            phone      TEXT,
            email      TEXT,
            active     INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_contractors_active ON contractors(active);
    """)

    # ── loto_permits + related tables ─────────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS loto_permits (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            loto_number           TEXT,
            plant                 TEXT,
            swp_id                INTEGER REFERENCES swp_permits(id) ON DELETE SET NULL,
            is_historical         INTEGER NOT NULL DEFAULT 0,
            backdate_note         TEXT,
            date                  TEXT,
            equipment_id          TEXT,
            equipment_description TEXT,
            location              TEXT,
            job_description       TEXT,
            group_leader          TEXT,
            status                TEXT NOT NULL DEFAULT 'draft'
                                    CHECK(status IN ('draft','active','released')),
            equip_id              INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
            created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS loto_boundaries (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            loto_id          INTEGER NOT NULL REFERENCES loto_permits(id) ON DELETE CASCADE,
            position         INTEGER NOT NULL,
            equipment        TEXT,
            location         TEXT,
            energy_type      TEXT,
            isolation_device TEXT,
            device_position  TEXT
        );

        CREATE TABLE IF NOT EXISTS loto_verifiers (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            loto_id  INTEGER NOT NULL REFERENCES loto_permits(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            name     TEXT,
            date     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_loto_swp_id    ON loto_permits(swp_id);
        CREATE INDEX IF NOT EXISTS idx_loto_status    ON loto_permits(status);
        CREATE INDEX IF NOT EXISTS idx_loto_created   ON loto_permits(created_by);
        CREATE INDEX IF NOT EXISTS idx_loto_bounds    ON loto_boundaries(loto_id);
        CREATE INDEX IF NOT EXISTS idx_loto_verifiers ON loto_verifiers(loto_id);
    """)

    # ── equipment + work log ──────────────────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS equipment (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            plant        TEXT NOT NULL,
            process_name TEXT NOT NULL,
            tag_id       TEXT NOT NULL,
            make         TEXT,
            model        TEXT,
            location     TEXT,
            notes        TEXT,
            active       INTEGER NOT NULL DEFAULT 1,
            created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_equipment_tag   ON equipment(tag_id);
        CREATE INDEX        IF NOT EXISTS idx_equipment_plant ON equipment(plant);
        CREATE INDEX        IF NOT EXISTS idx_equipment_name  ON equipment(process_name);

        CREATE TABLE IF NOT EXISTS equipment_work_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
            permit_type  TEXT NOT NULL,
            permit_id    INTEGER NOT NULL,
            comments     TEXT,
            start_date   TEXT,
            end_date     TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_equip_wl_equip  ON equipment_work_log(equipment_id);
        CREATE INDEX IF NOT EXISTS idx_equip_wl_permit ON equipment_work_log(permit_type, permit_id);
    """)

    # ── signature_requests ────────────────────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signature_requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            permit_type     TEXT NOT NULL CHECK(permit_type IN ('swp','loto')),
            permit_id       INTEGER NOT NULL,
            signature_role  TEXT NOT NULL,
            requested_from  INTEGER REFERENCES users(id) ON DELETE CASCADE,
            requested_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at    DATETIME,
            signature_data  TEXT,
            physical_only   INTEGER NOT NULL DEFAULT 0,
            acknowledged_at DATETIME,
            status          TEXT NOT NULL DEFAULT 'pending'
                              CHECK(status IN ('pending','signed','acknowledged','cancelled'))
        );

        CREATE INDEX IF NOT EXISTS idx_sig_user   ON signature_requests(requested_from, status);
        CREATE INDEX IF NOT EXISTS idx_sig_permit ON signature_requests(permit_type, permit_id);

        CREATE TABLE IF NOT EXISTS signing_tokens (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            token           TEXT NOT NULL UNIQUE,
            sig_request_id  INTEGER NOT NULL REFERENCES signature_requests(id) ON DELETE CASCADE,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at      DATETIME NOT NULL,
            used_at         DATETIME
        );

        CREATE INDEX IF NOT EXISTS idx_token_lookup ON signing_tokens(token);
    """)

    # Add 'pending' to loto_permits status CHECK if missing
    loto_sql = (conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='loto_permits'"
    ).fetchone() or ("",))[0]
    if "'pending'" not in loto_sql:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript("""
            PRAGMA legacy_alter_table=ON;
            ALTER TABLE loto_permits RENAME TO loto_permits_old;
            CREATE TABLE loto_permits (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                loto_number           TEXT,
                plant                 TEXT,
                swp_id                INTEGER REFERENCES swp_permits(id) ON DELETE SET NULL,
                is_historical         INTEGER NOT NULL DEFAULT 0,
                backdate_note         TEXT,
                date                  TEXT,
                equipment_id          TEXT,
                equipment_description TEXT,
                location              TEXT,
                job_description       TEXT,
                group_leader          TEXT,
                status                TEXT NOT NULL DEFAULT 'draft'
                                        CHECK(status IN ('draft','pending','active','released')),
                equip_id              INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
                created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO loto_permits
                SELECT id,loto_number,plant,swp_id,is_historical,backdate_note,date,
                       equipment_id,equipment_description,location,job_description,
                       group_leader,status,equip_id,created_by,created_at,updated_at
                FROM loto_permits_old;
            DROP TABLE loto_permits_old;
            CREATE INDEX IF NOT EXISTS idx_loto_swp_id    ON loto_permits(swp_id);
            CREATE INDEX IF NOT EXISTS idx_loto_status    ON loto_permits(status);
            CREATE INDEX IF NOT EXISTS idx_loto_created   ON loto_permits(created_by);
        """)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()

    # Column migrations: equip_id FK on both permit tables
    loto_cols_m = {row[1] for row in conn.execute("PRAGMA table_info(loto_permits)")}
    if "equip_id" not in loto_cols_m:
        conn.execute(
            "ALTER TABLE loto_permits ADD COLUMN equip_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL"
        )
    if "number_deferred" not in loto_cols_m:
        conn.execute(
            "ALTER TABLE loto_permits ADD COLUMN number_deferred INTEGER NOT NULL DEFAULT 0"
        )

    swp_cols_m = {row[1] for row in conn.execute("PRAGMA table_info(swp_permits)")}
    if "equip_id" not in swp_cols_m:
        conn.execute(
            "ALTER TABLE swp_permits ADD COLUMN equip_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL"
        )

    # ── SWP-02: PJSM continuation sheets ──────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS swp_pjsm_continuations (
            id                       INTEGER PRIMARY KEY,
            swp_id                   INTEGER NOT NULL REFERENCES swp_permits(id) ON DELETE CASCADE,
            host_employee           TEXT,
            location                 TEXT,
            work_lead                TEXT,
            work_lead_sig            TEXT,
            date_time                TEXT,
            additional_comments      TEXT,
            further_continuation_req INTEGER DEFAULT 0,
            created_by               INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at               DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS swp_pjsm_cont_attendees (
            id              INTEGER PRIMARY KEY,
            continuation_id INTEGER NOT NULL REFERENCES swp_pjsm_continuations(id) ON DELETE CASCADE,
            position        INTEGER NOT NULL DEFAULT 0,
            name            TEXT,
            signature       TEXT,
            company         TEXT,
            date            TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pjsm_cont_swp ON swp_pjsm_continuations(swp_id);
        CREATE INDEX IF NOT EXISTS idx_pjsm_cont_att ON swp_pjsm_cont_attendees(continuation_id);
    """)

    # ── SWP-05: Work continuation addendum ─────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS swp_work_continuations (
            id          INTEGER PRIMARY KEY,
            swp_id      INTEGER NOT NULL REFERENCES swp_permits(id) ON DELETE CASCADE,
            created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS swp_work_cont_entries (
            id                  INTEGER PRIMARY KEY,
            continuation_id     INTEGER NOT NULL REFERENCES swp_work_continuations(id) ON DELETE CASCADE,
            position            INTEGER NOT NULL DEFAULT 0,
            entry_type          TEXT NOT NULL CHECK(entry_type IN ('stop', 'restart')),
            date                TEXT,
            time                TEXT,
            work_lead_phone     TEXT,
            work_lead_name      TEXT,
            work_lead_sig       TEXT,
            authorizer_name     TEXT,
            authorizer_sig      TEXT,
            job_status_comments TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_work_cont_swp ON swp_work_continuations(swp_id);
        CREATE INDEX IF NOT EXISTS idx_work_cont_ent ON swp_work_cont_entries(continuation_id, position);
    """)

    # notify_sms opt-in flag and first_sms_sent compliance tracker on users
    users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "notify_sms" not in users_cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN notify_sms INTEGER NOT NULL DEFAULT 0"
        )
    if "first_sms_sent" not in users_cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN first_sms_sent INTEGER NOT NULL DEFAULT 0"
        )

    # Add 'pending_operator' to swp_permits status CHECK if missing.
    # Use writable_schema to update the constraint text in-place — avoids
    # listing all 114 columns in a recreate migration.
    swp_sql2 = (conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='swp_permits'"
    ).fetchone() or ("",))[0]
    if swp_sql2 and "'pending_operator'" not in swp_sql2:
        new_sql = swp_sql2.replace(
            "CHECK(status IN ('draft','pending','issued','closed'))",
            "CHECK(status IN ('draft','pending_operator','pending','issued','closed'))"
        )
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='swp_permits'",
            (new_sql,),
        )
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()

    # Add 'pending_operator' to loto_permits status CHECK if missing.
    loto_sql2 = (conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='loto_permits'"
    ).fetchone() or ("",))[0]
    if loto_sql2 and "'pending_operator'" not in loto_sql2:
        new_loto_sql = loto_sql2.replace(
            "CHECK(status IN ('draft','pending','active','released'))",
            "CHECK(status IN ('draft','pending_operator','pending','active','released'))"
        )
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='loto_permits'",
            (new_loto_sql,),
        )
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()

    conn.commit()
    conn.close()
