from tests.conftest import make_user, raw_db


def test_create_loto_happy_path(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-loto", "--data", (
        '{"plant":"NV","date":"2026-10-06","equipment_description":"Pump 1A",'
        '"location":"Brine Bldg","number_deferred":true,'
        '"boundaries":[{"equipment":"Pump1A","location":"MCC-1","energy_type":"Electrical",'
        '"isolation_device":"Breaker 4","device_position":"Off"}],'
        '"verifiers":[{"name":"Alex Rivera","date":"2026-10-06"}]}'
    ), "--user", "admin"])

    assert r.exit_code == 0, r.output
    assert "number=pending" in r.output

    db = raw_db(app)
    loto = db.execute("SELECT * FROM loto_permits").fetchone()
    assert loto["plant"] == "NV"
    assert loto["location"] == "Brine Bldg"
    assert loto["number_deferred"] == 1
    assert loto["loto_number"] is None
    assert loto["status"] == "draft"

    boundary = db.execute("SELECT * FROM loto_boundaries WHERE loto_id = ?", (loto["id"],)).fetchone()
    assert boundary["equipment"] == "Pump1A"
    assert boundary["energy_type"] == "Electrical"

    verifier = db.execute("SELECT * FROM loto_verifiers WHERE loto_id = ?", (loto["id"],)).fetchone()
    assert verifier["name"] == "Alex Rivera"

    audit = db.execute("SELECT * FROM audit_log WHERE resource = 'loto'").fetchone()
    assert audit["action"] == "LOTO_CREATED"
    assert audit["username"] == "admin"


def test_create_loto_assigns_number_when_not_deferred(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-loto", "--data", '{"plant":"NV"}', "--user", "admin"])

    assert r.exit_code == 0, r.output
    db = raw_db(app)
    loto = db.execute("SELECT * FROM loto_permits").fetchone()
    assert loto["loto_number"] and loto["loto_number"].startswith("NV-LOTO-")


def test_create_loto_rejects_non_admin(app):
    make_user(app, role="operator", username="jwhite")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-loto", "--data", '{"plant":"NV"}', "--user", "jwhite"])

    assert r.exit_code != 0
    assert "restricted to it_admin" in r.output
    assert raw_db(app).execute("SELECT COUNT(*) FROM loto_permits").fetchone()[0] == 0


def test_create_loto_rejects_unknown_user(app):
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-loto", "--data", '{"plant":"NV"}', "--user", "nobody"])

    assert r.exit_code != 0
    assert "No such user" in r.output


def test_create_loto_requires_data(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-loto", "--user", "admin"])

    assert r.exit_code != 0
    assert "Provide --data" in r.output


def test_create_swp_happy_path(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-swp", "--data", (
        '{"plant":"NV","date":"2026-10-06","location":"Brine Bldg",'
        '"work_lead":"Alex Rivera","scope_of_work":"Pump swap",'
        '"he_mechanical":true,"ppe_hard_hat":true,'
        '"attendees":[{"name":"Alex Rivera","company":"Acme Energy","date":"2026-10-06"}]}'
    ), "--user", "admin"])

    assert r.exit_code == 0, r.output
    db = raw_db(app)
    swp = db.execute("SELECT * FROM swp_permits").fetchone()
    assert swp["plant"] == "NV"
    assert swp["status"] == "draft"
    assert swp["he_mechanical"] == 1
    assert swp["ppe_hard_hat"] == 1

    attendee = db.execute("SELECT * FROM swp_attendees WHERE swp_id = ?", (swp["id"],)).fetchone()
    assert attendee["name"] == "Alex Rivera"
    assert attendee["company"] == "Acme Energy"

    audit = db.execute("SELECT * FROM audit_log WHERE resource = 'swp'").fetchone()
    assert audit["action"] == "SWP_CREATED"


def test_create_swp_rejects_non_admin(app):
    make_user(app, role="admin", username="jpavone")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-swp", "--data", '{"plant":"NV"}', "--user", "jpavone"])

    assert r.exit_code != 0
    assert "restricted to it_admin" in r.output


def test_create_swp_print_flag_handles_no_printer(app, monkeypatch):
    import app.pdf as pdf_mod
    monkeypatch.setattr(pdf_mod, "_printer_available", lambda: False)
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-swp", "--data", '{"plant":"NV"}', "--user", "admin", "--print"])

    assert r.exit_code == 0, r.output
    assert "Printer unavailable" in r.output
    db = raw_db(app)
    assert db.execute("SELECT COUNT(*) FROM swp_permits").fetchone()[0] == 1


def test_create_swp_print_flag_sends_when_available(app, monkeypatch, tmp_path):
    import app.pdf as pdf_mod
    monkeypatch.setattr(pdf_mod, "_printer_available", lambda: True)
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(pdf_mod, "_fill_swp", lambda swp_id, tmp_dir: fake_pdf)
    sent = {}
    monkeypatch.setattr(pdf_mod, "_lp_print", lambda path: sent.setdefault("path", path) or True)
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-swp", "--data", '{"plant":"NV"}', "--user", "admin", "--print"])

    assert r.exit_code == 0, r.output
    assert "Sent to printer." in r.output
    assert sent["path"] == fake_pdf


def test_add_equipment_creates_records(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    data = (
        '[{"plant":"NV","process_name":"S1.R31.I6","location":"North Array"},'
        '{"plant":"NV","process_name":"S1.R24.I1","location":"North Array"}]'
    )
    r = runner.invoke(args=["add-equipment", "--data", data, "--user", "admin"])

    assert r.exit_code == 0, r.output
    assert "Created: NV-S1-R31-I6" in r.output
    assert "Created: NV-S1-R24-I1" in r.output

    db = raw_db(app)
    rows = db.execute("SELECT tag_id, process_name, location FROM equipment ORDER BY tag_id").fetchall()
    assert [r["tag_id"] for r in rows] == ["NV-S1-R24-I1", "NV-S1-R31-I6"]
    assert rows[0]["location"] == "North Array"

    audit = db.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'EQUIPMENT_CREATED'").fetchone()[0]
    assert audit == 2


def test_add_equipment_skips_existing_tag(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()
    data = '[{"plant":"NV","process_name":"S1.R31.I6"}]'

    runner.invoke(args=["add-equipment", "--data", data, "--user", "admin"])
    r2 = runner.invoke(args=["add-equipment", "--data", data, "--user", "admin"])

    assert r2.exit_code == 0, r2.output
    assert "Skipped (already exists): NV-S1-R31-I6" in r2.output
    assert raw_db(app).execute("SELECT COUNT(*) FROM equipment").fetchone()[0] == 1


def test_add_equipment_requires_plant_and_process_name(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["add-equipment", "--data", '[{"plant":"NV"}]', "--user", "admin"])

    assert r.exit_code != 0
    assert "plant and process_name are required" in r.output


def test_add_equipment_rejects_non_admin(app):
    make_user(app, role="operator", username="jwhite")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["add-equipment", "--data", '[{"plant":"NV","process_name":"X"}]', "--user", "jwhite"])

    assert r.exit_code != 0
    assert "restricted to it_admin" in r.output
    assert raw_db(app).execute("SELECT COUNT(*) FROM equipment").fetchone()[0] == 0


def test_create_swp_applies_form_defaults_when_unspecified(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-swp", "--data", '{"plant":"NV"}', "--user", "admin"])

    assert r.exit_code == 0, r.output
    swp = raw_db(app).execute("SELECT * FROM swp_permits").fetchone()
    assert swp["company"] == "Acme Energy"
    assert swp["tha_slips_trips"] == 1
    assert swp["ppe_hard_hat"] == 1
    assert swp["ppe_safety_glasses"] == 1
    assert swp["ppe_gloves"] == 1
    assert swp["ppe_fr_clothing"] == 1
    assert swp["ppe_safety_shoes"] == 1
    assert "Eyes on task" in swp["hazard_mitigation"]
    # Defaults shouldn't turn on anything not declared as a form default.
    assert swp["tha_power_tools"] == 0
    assert swp["ppe_respirator"] == 0


def test_create_swp_explicit_payload_overrides_defaults(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-swp", "--data",
                             '{"plant":"NV","company":"Acme","ppe_hard_hat":false}',
                             "--user", "admin"])

    assert r.exit_code == 0, r.output
    swp = raw_db(app).execute("SELECT * FROM swp_permits").fetchone()
    assert swp["company"] == "Acme"
    assert swp["ppe_hard_hat"] == 0


def test_create_swp_with_id_updates_in_place(app):
    make_user(app, role="it_admin", username="admin")
    runner = app.test_cli_runner()

    r1 = runner.invoke(args=["create-swp", "--data",
                              '{"plant":"NV","location":"North Array Trackers","work_lead":"Sam Carter"}',
                              "--user", "admin"])
    assert r1.exit_code == 0, r1.output
    swp_id = raw_db(app).execute("SELECT id FROM swp_permits").fetchone()["id"]

    r2 = runner.invoke(args=["create-swp", "--id", str(swp_id), "--data",
                              '{"plant":"NV","location":"North Array Trackers","work_lead":"Sam Carter",'
                              '"time":"07:30","tha_power_tools":true}',
                              "--user", "admin"])
    assert r2.exit_code == 0, r2.output

    db = raw_db(app)
    assert db.execute("SELECT COUNT(*) FROM swp_permits").fetchone()[0] == 1
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    assert swp["time"] == "07:30"
    assert swp["tha_power_tools"] == 1
    assert swp["location"] == "North Array Trackers"
    assert swp["company"] == "Acme Energy"
