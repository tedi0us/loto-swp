"""IT-Admin-only CLI for creating LOTO/SWP permits and equipment without the web form.

Usage (inside the container or venv):
    flask --app "app:create_app()" create-loto --data '{"plant": "NV", ...}'
    flask --app "app:create_app()" create-swp  --data '{"plant": "NV", ...}' [--print]
    flask --app "app:create_app()" add-equipment --data '[{"plant": "NV", "process_name": "...", ...}, ...]'

create-swp's --print fills the PDF and spools it to the printer configured via
PRINTER_NAME/CUPS_SERVER (same mechanism as the web app's Print button), synchronously
so the CLI process doesn't exit before the print job is submitted.

--data accepts the same field names used by the web forms (see LotoForm /
SWPForm in app/loto.py / app/swp.py, plus the raw b_*/v_*/attend_* row
fields used by the form templates). Two conveniences on top of that:
  - "boundaries": [{"equipment":..., "location":..., "energy_type":...,
                     "isolation_device":..., "device_position":...}, ...]
    expands to b_equipment_1, b_location_1, ... (1-based).
  - "verifiers" / "attendees": [{"name":..., "date":...}, ...] /
    [{"name":..., "company":..., "date":...}, ...] expand to v_name_0,
    v_date_0, ... / attend_name_0, attend_company_0, attend_date_0, ...
    (0-based).
  - "from_swp" (create-loto only): int id of an SWP to link via FK.

Boolean fields (number_deferred, is_historical, is_backdated, he_*, ppe_*,
tha_*, ts_*) follow checkbox semantics: true -> included as "y", false/
absent -> omitted (unchecked). Fields with a WTForms-level default (e.g.
SWPForm's company, tha_slips_trips=True, the pre-checked PPE set,
hazard_mitigation boilerplate) are applied automatically when --data
doesn't mention them — same defaults the web form shows on a fresh "New"
page. Pass the field explicitly (including `false`/"") to override one.

create-swp --id <id> updates an existing draft in place instead of
creating a new one (same mechanism as autosave) — like autosave, it
replaces every field from --data/defaults, so repeat the fields you want
to keep, not just the ones you're changing.

Restricted to users with the it_admin role — resolved via --user (default
"admin") and verified against the users table before anything is saved.
"""
import json
import shutil
import tempfile
from pathlib import Path

import click
from flask_login import login_user

from app.audit import EQUIPMENT_CREATED, LOTO_CREATED, SWP_CREATED, log
from app.db import get_db
from app.roles import IT_ADMIN


def _resolve_it_admin(username):
    from app.auth import _load_user_row
    row = get_db().execute(
        """SELECT id, username, role, must_change_password,
                  first_name, last_name, email, phone, title, plant, notify_sms
           FROM users WHERE username = ?""",
        (username,),
    ).fetchone()
    if row is None:
        raise click.ClickException(f"No such user: {username!r}")
    if row["role"] != IT_ADMIN:
        raise click.ClickException(
            f"User {username!r} has role {row['role']!r} — this command is restricted to it_admin."
        )
    return _load_user_row(row)


def _wtform_defaults(form_cls):
    """Field-level defaults declared on a WTForms class, as payload-shaped values
    (True for a default-checked BooleanField, the string for a default text/select).
    Lets the CLI inherit whatever defaults the web form declares (e.g. pre-checked
    PPE) without duplicating them — payload values should always be merged on top."""
    unbound = form_cls(formdata=None, meta={"csrf": False})
    defaults = {}
    for field in unbound:
        if field.type == "BooleanField":
            if field.default:
                defaults[field.name] = True
        elif isinstance(field.default, str) and field.default:
            defaults[field.name] = field.default
    return defaults


def _load_payload(data, data_file):
    if data_file:
        with open(data_file) as f:
            return json.load(f)
    if data:
        return json.loads(data)
    raise click.ClickException("Provide --data '<json>' or --file <path>.")


def _flatten(payload, list_specs):
    """Pop list fields per list_specs {key: (prefix, field_order, start_idx)} and
    merge remaining scalar/bool fields into a flat form-field dict."""
    form_data = {}
    for key, (prefix, fields, start_idx) in list_specs.items():
        rows = payload.pop(key, None) or []
        for i, row in enumerate(rows, start=start_idx):
            for field in fields:
                val = row.get(field)
                if val not in (None, ""):
                    form_data[f"{prefix}{field}_{i}"] = str(val)
    for key, val in payload.items():
        if val is None:
            continue
        if isinstance(val, bool):
            if val:
                form_data[key] = "y"
        else:
            form_data[key] = str(val)
    return form_data


def register_cli(app):
    @app.cli.command("create-loto")
    @click.option("--data", default=None, help="JSON object of LOTO fields.")
    @click.option("--file", "data_file", default=None, type=click.Path(exists=True),
                  help="Path to a JSON file of LOTO fields (alternative to --data).")
    @click.option("--user", default="admin", show_default=True,
                  help="Username to attribute as creator; must be an it_admin.")
    def create_loto_cmd(data, data_file, user):
        """Create a LOTO permit directly, bypassing the web form. IT Admins only."""
        from app.loto import LotoForm, _save_loto

        payload = {**_wtform_defaults(LotoForm), **_load_payload(data, data_file)}
        from_swp = payload.pop("from_swp", None)
        form_data = _flatten(payload, {
            "boundaries": ("b_", ("equipment", "location", "energy_type",
                                   "isolation_device", "device_position"), 1),
            "verifiers": ("v_", ("name", "date"), 0),
        })

        with app.test_request_context(method="POST", data=form_data):
            actor = _resolve_it_admin(user)
            login_user(actor)
            form = LotoForm()
            loto_id, _ = _save_loto(form, from_swp=from_swp)
            row = get_db().execute(
                "SELECT loto_number, status, number_deferred FROM loto_permits WHERE id = ?",
                (loto_id,),
            ).fetchone()
            log(LOTO_CREATED, user_id=actor.id, username=actor.username,
                resource="loto", resource_id=loto_id, detail={"via": "cli"})

        number = row["loto_number"] or ("pending" if row["number_deferred"] else "—")
        click.echo(f"Created LOTO id={loto_id} number={number} status={row['status']}")

    @app.cli.command("create-swp")
    @click.option("--data", default=None, help="JSON object of SWP fields.")
    @click.option("--file", "data_file", default=None, type=click.Path(exists=True),
                  help="Path to a JSON file of SWP fields (alternative to --data).")
    @click.option("--user", default="admin", show_default=True,
                  help="Username to attribute as creator; must be an it_admin.")
    @click.option("--print", "do_print", is_flag=True, default=False,
                  help="Fill the PDF and send it to the configured printer after creating.")
    @click.option("--id", "swp_id_opt", type=int, default=None,
                  help="Update this existing draft SWP in place instead of creating a new one. "
                       "Note: like autosave, this replaces every field from --data/defaults — "
                       "fields not repeated here will revert to their default/blank.")
    def create_swp_cmd(data, data_file, user, do_print, swp_id_opt):
        """Create (or update, with --id) an SWP permit draft directly, bypassing the
        web form. IT Admins only."""
        from app.swp import SWPForm, _save_swp

        payload = {**_wtform_defaults(SWPForm), **_load_payload(data, data_file)}
        form_data = _flatten(payload, {
            "attendees": ("attend_", ("name", "company", "date"), 0),
        })

        with app.test_request_context(method="POST", data=form_data):
            actor = _resolve_it_admin(user)
            login_user(actor)
            form = SWPForm()
            swp_id = _save_swp(form, swp_id=swp_id_opt, target_status="draft")
            row = get_db().execute(
                "SELECT permit_number, status FROM swp_permits WHERE id = ?",
                (swp_id,),
            ).fetchone()
            log(SWP_CREATED, user_id=actor.id, username=actor.username,
                resource="swp", resource_id=swp_id, detail={"via": "cli", "updated": swp_id_opt is not None})

            if do_print:
                from app.pdf import _fill_swp, _lp_print, _printer_available
                if not _printer_available():
                    click.echo("Printer unavailable — not sent.")
                else:
                    tmp_dir = tempfile.mkdtemp()
                    try:
                        pdf_path = _fill_swp(swp_id, Path(tmp_dir))
                        click.echo("Sent to printer." if _lp_print(pdf_path) else "Print submission failed.")
                    finally:
                        shutil.rmtree(tmp_dir, ignore_errors=True)

        click.echo(f"Created SWP id={swp_id} permit_number={row['permit_number'] or '—'} status={row['status']}")

    @app.cli.command("add-equipment")
    @click.option("--data", default=None, help="JSON array of equipment objects.")
    @click.option("--file", "data_file", default=None, type=click.Path(exists=True),
                  help="Path to a JSON file of equipment objects (alternative to --data).")
    @click.option("--user", default="admin", show_default=True,
                  help="Username to attribute as creator; must be an it_admin.")
    def add_equipment_cmd(data, data_file, user):
        """Bulk-add equipment records (plant + process_name required each). IT Admins only."""
        from app.equipment import _make_tag_id

        payload = _load_payload(data, data_file)
        if isinstance(payload, dict):
            payload = [payload]

        with app.test_request_context():
            actor = _resolve_it_admin(user)
            db = get_db()
            for item in payload:
                plant = (item.get("plant") or "").strip()
                process_name = (item.get("process_name") or "").strip()
                if not plant or not process_name:
                    raise click.ClickException(f"plant and process_name are required: {item!r}")
                tag_id = _make_tag_id(plant, process_name)
                if db.execute("SELECT id FROM equipment WHERE tag_id = ?", (tag_id,)).fetchone():
                    click.echo(f"Skipped (already exists): {tag_id}")
                    continue
                db.execute(
                    """INSERT INTO equipment
                           (plant, process_name, tag_id, make, model, location, notes, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (plant, process_name, tag_id, item.get("make"), item.get("model"),
                     item.get("location"), item.get("notes"), actor.id),
                )
                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                log(EQUIPMENT_CREATED, user_id=actor.id, username=actor.username,
                    resource="equipment", resource_id=new_id, detail={"tag_id": tag_id})
                click.echo(f"Created: {tag_id}")
