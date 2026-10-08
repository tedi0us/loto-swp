"""
End-to-end tests for /pdf/swp/<id> and /pdf/loto/<id>.

Verifies the full route: auth gate → data fetch → fill → GS flatten → send_file.
Requires Ghostscript and LibreOffice on PATH (same as production).
"""
import io
import sqlite3

import pypdf
import pytest

from tests.conftest import login, make_user, TEST_PW


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_swp(app, user_id: int, plant: str = "NV") -> int:
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    conn.execute(
        """INSERT INTO swp_permits
               (plant, permit_number, date, time, location, company, work_lead,
                scope_of_work, status, created_by,
                he_electrical, he_mechanical,
                ppe_hard_hat, ppe_safety_glasses, ppe_fr_clothing,
                tha_loto, tha_power_tools,
                ts_review_procedures, ts_valve_positions,
                hazard_mitigation, additional_comments,
                permit_loto)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (plant, f"{plant}-SWP-26-TEST", "2026-06-02", "08:00",
         "Pump Room A", "Acme Energy", "Test User",
         "Replace seal on pump.",
         "issued", user_id,
         1, 1, 1, 1, 1, 1, 1, 1, 1,
         "Zero-energy before start.", "Notify control room.",
         f"{plant}-LOTO-26-TEST"),
    )
    conn.commit()
    swp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO swp_attendees (swp_id, position, name, company, signature, date)
           VALUES (?, 0, ?, ?, ?, ?)""",
        (swp_id, "Test User", "Acme Energy", "Test User", "2026-06-02"),
    )
    conn.commit()
    conn.close()
    return swp_id


def _make_loto(app, user_id: int, swp_id: int | None = None, plant: str = "NV") -> int:
    conn = sqlite3.connect(str(app.config["DATABASE"]))
    conn.execute(
        """INSERT INTO loto_permits
               (plant, loto_number, swp_id, date, equipment_description,
                job_description, group_leader, status, created_by)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (plant, f"{plant}-LOTO-26-TEST", swp_id,
         "2026-06-02", "Feed Pump FP-102",
         "Replace mechanical seal.", "Test User", "active", user_id),
    )
    conn.commit()
    loto_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Add two boundary rows
    conn.executemany(
        """INSERT INTO loto_boundaries
               (loto_id, position, equipment, location, energy_type,
                isolation_device, device_position)
           VALUES (?,?,?,?,?,?,?)""",
        [
            (loto_id, 1, "Feed Pump FP-102", "Pump Room A",
             "Electrical", "CB-12 Breaker", "OPEN/LOCKED"),
            (loto_id, 2, "Feed Pump FP-102", "Pump Room A",
             "Mechanical", "Isolation Valve V-05", "CLOSED/LOCKED"),
        ],
    )
    # Add a verifier
    conn.execute(
        "INSERT INTO loto_verifiers (loto_id, position, name, date) VALUES (?,?,?,?)",
        (loto_id, 0, "Test User", "2026-06-02"),
    )
    conn.commit()
    conn.close()
    return loto_id


# ── SWP PDF tests ─────────────────────────────────────────────────────────────

class TestSwpPdf:
    def test_unauthenticated_redirects(self, client):
        r = client.get("/pdf/swp/1")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_missing_swp_returns_404(self, app, client):
        uid = make_user(app, role="operator")
        login(client, "testuser")
        r = client.get("/pdf/swp/99999")
        assert r.status_code == 404

    def test_other_users_swp_returns_403(self, app, client):
        uid_a = make_user(app, role="operator", username="user_a")
        uid_b = make_user(app, role="operator", username="user_b")
        swp_id = _make_swp(app, uid_a)
        login(client, "user_b")
        r = client.get(f"/pdf/swp/{swp_id}")
        assert r.status_code == 403

    def test_admin_can_download_any_swp(self, app, client):
        uid_a = make_user(app, role="operator", username="owner")
        uid_admin = make_user(app, role="admin", username="admin_user")
        swp_id = _make_swp(app, uid_a)
        login(client, "admin_user")
        r = client.get(f"/pdf/swp/{swp_id}")
        assert r.status_code == 200
        assert r.content_type == "application/pdf"

    def test_swp_pdf_is_valid_flattened_pdf(self, app, client):
        """Core test: the route returns a valid, flattened PDF with zero form fields."""
        uid = make_user(app, role="operator")
        swp_id = _make_swp(app, uid)
        login(client)
        r = client.get(f"/pdf/swp/{swp_id}")
        assert r.status_code == 200
        assert r.content_type == "application/pdf"

        reader = pypdf.PdfReader(io.BytesIO(r.data))
        # Must be flattened — no interactive form fields should survive GS
        fields = reader.get_fields()
        assert not fields, f"Expected 0 fields after flatten, got {len(fields)}"

    def test_swp_pdf_text_baked_in(self, app, client):
        """Permit number and work lead must appear in extracted page text."""
        uid = make_user(app, role="operator")
        swp_id = _make_swp(app, uid)
        login(client)
        r = client.get(f"/pdf/swp/{swp_id}")
        assert r.status_code == 200

        reader = pypdf.PdfReader(io.BytesIO(r.data))
        full_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "NV-SWP-26-TEST" in full_text, "Permit number not in extracted text"
        assert "Test User" in full_text, "Work lead not in extracted text"

    def test_swp_pdf_has_correct_filename(self, app, client):
        uid = make_user(app, role="operator")
        swp_id = _make_swp(app, uid)
        login(client)
        r = client.get(f"/pdf/swp/{swp_id}")
        assert r.status_code == 200
        cd = r.headers.get("Content-Disposition", "")
        assert "NV-SWP-26-TEST" in cd


# ── LOTO PDF tests ────────────────────────────────────────────────────────────

class TestLotoPdf:
    def test_unauthenticated_redirects(self, client):
        r = client.get("/pdf/loto/1")
        assert r.status_code == 302

    def test_missing_loto_returns_404(self, app, client):
        uid = make_user(app, role="operator")
        login(client)
        r = client.get("/pdf/loto/99999")
        assert r.status_code == 404

    def test_loto_pdf_is_valid_pdf(self, app, client):
        """Route returns a valid PDF (LibreOffice conversion succeeded)."""
        uid = make_user(app, role="operator")
        loto_id = _make_loto(app, uid)
        login(client)
        r = client.get(f"/pdf/loto/{loto_id}")
        assert r.status_code == 200
        assert r.content_type == "application/pdf"

        reader = pypdf.PdfReader(io.BytesIO(r.data))
        assert len(reader.pages) >= 1

    def test_loto_pdf_has_two_pages(self, app, client):
        """LOTO form has Part I (landscape) and Part II (portrait)."""
        uid = make_user(app, role="operator")
        loto_id = _make_loto(app, uid)
        login(client)
        r = client.get(f"/pdf/loto/{loto_id}")
        assert r.status_code == 200
        reader = pypdf.PdfReader(io.BytesIO(r.data))
        assert len(reader.pages) == 2

    def test_loto_pdf_has_correct_filename(self, app, client):
        uid = make_user(app, role="operator")
        loto_id = _make_loto(app, uid)
        login(client)
        r = client.get(f"/pdf/loto/{loto_id}")
        assert r.status_code == 200
        cd = r.headers.get("Content-Disposition", "")
        assert "NV-LOTO-26-TEST" in cd


# ── Printer status + print routes ─────────────────────────────────────────────

class TestPrinterStatus:
    def test_unauthenticated_redirects(self, client):
        r = client.get("/pdf/printer-status?type=swp&id=1")
        assert r.status_code == 302

    def test_returns_disabled_button_when_lpstat_fails(self, app, client, monkeypatch):
        monkeypatch.setenv("PRINTER_NAME", "NonExistentPrinter")
        uid = make_user(app, role="operator")
        login(client)
        r = client.get("/pdf/printer-status?type=swp&id=1")
        assert r.status_code == 200
        assert b"disabled" in r.data

    def test_returns_print_button_when_printer_available(self, app, client, monkeypatch):
        from unittest.mock import patch, MagicMock
        uid = make_user(app, role="operator")
        uid_swp = _make_swp(app, uid)
        login(client)
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            r = client.get(f"/pdf/printer-status?type=swp&id={uid_swp}")
        assert r.status_code == 200
        assert b"hx-post" in r.data
        assert b"disabled" not in r.data


class TestSwpPrint:
    def test_unauthenticated_redirects(self, client):
        r = client.post("/pdf/swp/1/print")
        assert r.status_code == 302

    def test_missing_swp_returns_404(self, app, client):
        make_user(app, role="operator")
        login(client)
        r = client.post("/pdf/swp/99999/print")
        assert r.status_code == 404

    def test_other_users_swp_returns_403(self, app, client):
        uid_a = make_user(app, role="operator", username="user_a")
        uid_b = make_user(app, role="operator", username="user_b")
        swp_id = _make_swp(app, uid_a)
        login(client, "user_b")
        r = client.post(f"/pdf/swp/{swp_id}/print")
        assert r.status_code == 403

    def test_successful_print_returns_immediately(self, app, client, monkeypatch):
        from unittest.mock import patch, MagicMock
        uid = make_user(app, role="operator")
        swp_id = _make_swp(app, uid)
        login(client)

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            r = client.post(f"/pdf/swp/{swp_id}/print")

        assert r.status_code == 200
        assert b"Sent" in r.data


class TestLotoPrint:
    def test_unauthenticated_redirects(self, client):
        r = client.post("/pdf/loto/1/print")
        assert r.status_code == 302

    def test_missing_loto_returns_404(self, app, client):
        make_user(app, role="operator")
        login(client)
        r = client.post("/pdf/loto/99999/print")
        assert r.status_code == 404

    def test_successful_print_returns_immediately(self, app, client, monkeypatch):
        from unittest.mock import patch, MagicMock
        uid = make_user(app, role="operator")
        loto_id = _make_loto(app, uid)
        login(client)

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            r = client.post(f"/pdf/loto/{loto_id}/print")

        assert r.status_code == 200
        assert b"Sent" in r.data
