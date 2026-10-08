"""PDF generation and direct printing for LOTO and SWP permits."""
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import openpyxl
import pypdf
import pypdf.generic as pdf_gen
from pypdf.generic import NameObject
from flask import Blueprint, abort, current_app, request, send_file
from flask_login import current_user, login_required
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.properties import PageSetupProperties

from app.db import get_db
from app.roles import ADMIN, IT_ADMIN

# ── Helvetica glyph widths (AFM, units per 1000-unit em) ─────────────────────
_HELV_W: dict[str, int] = {
    ' ':278, '!':278, '"':355, '#':556, '$':556, '%':889, '&':667, "'":191,
    '(':333, ')':333, '*':389, '+':584, ',':278, '-':278, '.':278, '/':278,
    '0':556, '1':278, '2':556, '3':556, '4':556, '5':556, '6':556, '7':556,
    '8':556, '9':556, ':':278, ';':278, '<':584, '=':584, '>':584, '?':556,
    '@':1015, 'A':667, 'B':667, 'C':722, 'D':722, 'E':667, 'F':611, 'G':778,
    'H':722, 'I':278, 'J':500, 'K':667, 'L':611, 'M':833, 'N':722, 'O':778,
    'P':667, 'Q':778, 'R':722, 'S':667, 'T':611, 'U':722, 'V':667, 'W':944,
    'X':667, 'Y':667, 'Z':611, 'a':556, 'b':556, 'c':500, 'd':556, 'e':556,
    'f':278, 'g':556, 'h':556, 'i':222, 'j':222, 'k':500, 'l':222, 'm':833,
    'n':556, 'o':556, 'p':556, 'q':556, 'r':333, 's':500, 't':278, 'u':556,
    'v':500, 'w':722, 'x':500, 'y':500, 'z':500,
}


def _helv_w(text: str, size: float) -> float:
    return sum(_HELV_W.get(c, 556) for c in text) / 1000.0 * size


def _best_size(text: str, field_w: float, field_h: float, padding: float = 4) -> float:
    """Return the largest font size where text fits inside the field rectangle."""
    avail_w = field_w - padding
    size = field_h * 0.72        # start from height-based auto-size
    while size > 4 and _helv_w(text, size) > avail_w:
        size -= 0.25
    return round(size, 2)


def _escape(text: str) -> str:
    """Escape a string for use in a PDF literal string (parentheses notation)."""
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _tx_ap_stream(text: str, rect: list, multiline: bool = False) -> bytes:
    """
    Build a correct PDF appearance stream for a text field.

    Uses single-byte literal string encoding so Helvetica (Type1, single-byte)
    renders with correct glyph metrics.  pypdf's auto_regenerate uses UTF-16BE
    hex strings which produce wrong glyph widths and weird character spacing.
    """
    x0, y0, x1, y1 = [float(v) for v in rect]
    fw = x1 - x0
    fh = y1 - y0

    pad_x, pad_y = 2.0, 1.0
    clip_w = fw - pad_x
    clip_h = fh - pad_y * 2

    if not multiline:
        size = _best_size(text, fw, fh)
        # Vertical centre: baseline sits at (clip_h - size)/2 + descender offset
        descender = size * 0.2
        ty = (clip_h - size) / 2 + descender
        content = (
            f"q\n/Tx BMC \nq\n"
            f"{pad_x} {pad_y} {clip_w:.3f} {clip_h:.3f} re\nW n\n"
            f"BT\n/Helv {size:.2f} Tf 0 g\n"
            f"{pad_x:.2f} {ty:.3f} Td\n"
            f"({_escape(text)}) Tj\n"
            f"ET\nQ\nEMC\nQ\n"
        )
    else:
        # For multiline fields, wrap at word boundaries
        size = min(9.0, fh * 0.25)          # fixed readable size for big fields
        line_h = size * 1.3
        avail_w = fw - pad_x * 2

        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            if _helv_w(test, size) <= avail_w:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

        # Start from near the top, writing down
        start_y = clip_h - size - pad_y
        parts = [
            f"q\n/Tx BMC \nq\n"
            f"{pad_x} {pad_y} {clip_w:.3f} {clip_h:.3f} re\nW n\n"
            f"BT\n/Helv {size:.2f} Tf 0 g\n"
            f"{pad_x:.2f} {start_y:.3f} Td\n"
        ]
        for i, line in enumerate(lines):
            if i == 0:
                parts.append(f"({_escape(line)}) Tj\n")
            else:
                parts.append(f"0 -{line_h:.3f} Td\n({_escape(line)}) Tj\n")
        parts.append("ET\nQ\nEMC\nQ\n")
        content = "".join(parts)

    return content.encode("latin-1")


# Large comment boxes wrap even if a replacement form omits the multiline /Ff bit.
_MULTILINE_OVERRIDE = {"scope_of_work", "hazard_mitigation", "additional_comments"}


def _inject_tx_appearances(writer: pypdf.PdfWriter, field_values: dict) -> None:
    """
    For every /Tx annotation whose /T is in field_values, replace /AP/N with
    a fresh Form XObject stream built from _tx_ap_stream.

    Appearance streams must be Form XObjects (/Type /XObject /Subtype /Form
    /BBox [...]).  Without those keys GS ignores the stream entirely.
    Resources must include /Helv; we reuse the existing reference from the
    AcroForm /DR so GS can resolve it without an embedded font program.
    """
    # Find the existing /Helv font reference from the AcroForm /DR
    root = writer._root_object
    acroform = root.get("/AcroForm")
    helv_ref = None
    if acroform:
        af = acroform.get_object()
        dr = af.get("/DR")
        if dr:
            font_dict = dr.get_object().get("/Font")
            if font_dict:
                helv_ref = font_dict.get_object().get("/Helv")

    if helv_ref is None:
        # Fallback: create a minimal standard font dict
        helv_ref = writer._add_object(pdf_gen.DictionaryObject({
            NameObject("/Type"):     NameObject("/Font"),
            NameObject("/Subtype"):  NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))

    resources_ref = writer._add_object(pdf_gen.DictionaryObject({
        NameObject("/Font"): pdf_gen.DictionaryObject({
            NameObject("/Helv"): helv_ref,
        })
    }))

    for page in writer.pages:
        for a_ref in page.get("/Annots", []):
            annot = a_ref.get_object()
            if annot.get("/FT") != NameObject("/Tx"):
                continue
            t = str(annot.get("/T", ""))
            if t not in field_values:
                continue
            text = field_values[t]
            if not isinstance(text, str):
                text = str(text)

            rect = [float(v) for v in annot.get("/Rect")]
            fw = rect[2] - rect[0]
            fh = rect[3] - rect[1]
            multiline = bool(int(annot.get("/Ff", 0)) & (1 << 12)) or t in _MULTILINE_OVERRIDE

            stream_data = _tx_ap_stream(text, rect, multiline=multiline)

            # Must be a Form XObject: /Type /XObject /Subtype /Form /BBox
            stream_obj = pdf_gen.DecodedStreamObject()
            stream_obj[NameObject("/Type")]      = NameObject("/XObject")
            stream_obj[NameObject("/Subtype")]   = NameObject("/Form")
            stream_obj[NameObject("/BBox")]      = pdf_gen.ArrayObject([
                pdf_gen.FloatObject(0), pdf_gen.FloatObject(0),
                pdf_gen.FloatObject(fw), pdf_gen.FloatObject(fh),
            ])
            stream_obj[NameObject("/Resources")] = resources_ref
            stream_obj.set_data(stream_data)

            ap_dict = pdf_gen.DictionaryObject()
            ap_dict[NameObject("/N")] = writer._add_object(stream_obj)
            annot[NameObject("/AP")] = ap_dict


def _checkbox_ap_stream(fw: float, fh: float, filled: bool) -> bytes:
    if not filled:
        return b""
    pad_x, pad_y = fw * 0.18, fh * 0.18
    bw, bh = fw - 2 * pad_x, fh - 2 * pad_y
    return f"0 0 0 rg\n{pad_x:.3f} {pad_y:.3f} {bw:.3f} {bh:.3f} re\nf\n".encode("latin-1")


def _inject_btn_appearances(writer: pypdf.PdfWriter, checkbox_map: dict) -> None:
    """
    Set /V and /AS on every /Btn annotation named in checkbox_map, replacing
    /AP/N with a freshly drawn two-state dict (filled black square / blank).

    Fillable forms often draw checkmarks with an unembedded ZapfDingbats
    glyph, which Ghostscript/Poppler may not have available, so the mark
    silently fails to render even though /V and /AS are set. Drawing every
    checkbox appearance as a plain vector square avoids the font dependency
    and gives one consistent checked style, matching _inject_tx_appearances.
    """
    for page in writer.pages:
        for a_ref in page.get("/Annots", []):
            annot = a_ref.get_object()
            if annot.get("/FT") != NameObject("/Btn"):
                continue
            t = str(annot.get("/T", ""))
            if t not in checkbox_map:
                continue
            checked = bool(checkbox_map[t])
            rect = [float(v) for v in annot.get("/Rect")]
            fw, fh = rect[2] - rect[0], rect[3] - rect[1]

            def _mk_stream(is_filled: bool):
                stream_obj = pdf_gen.DecodedStreamObject()
                stream_obj[NameObject("/Type")]    = NameObject("/XObject")
                stream_obj[NameObject("/Subtype")] = NameObject("/Form")
                stream_obj[NameObject("/BBox")]    = pdf_gen.ArrayObject([
                    pdf_gen.FloatObject(0), pdf_gen.FloatObject(0),
                    pdf_gen.FloatObject(fw), pdf_gen.FloatObject(fh),
                ])
                stream_obj.set_data(_checkbox_ap_stream(fw, fh, is_filled))
                return writer._add_object(stream_obj)

            n_dict = pdf_gen.DictionaryObject()
            n_dict[NameObject("/Off")] = _mk_stream(False)
            n_dict[NameObject("/Yes")] = _mk_stream(True)
            ap_dict = pdf_gen.DictionaryObject()
            ap_dict[NameObject("/N")] = n_dict
            annot[NameObject("/AP")] = ap_dict

            state = NameObject("/Yes") if checked else NameObject("/Off")
            annot[NameObject("/V")]  = state
            annot[NameObject("/AS")] = state


pdf_bp = Blueprint("pdf", __name__, url_prefix="/pdf")

TEMPLATE_DIR = Path(__file__).parent.parent / "forms"

_lo_lock = threading.Lock()

# ── Template cell dimensions (match forms/loto/loto-form.xlsx) ───────────
# Col widths in Excel char units; row heights in points.
_L1_COL = {"B": 16.11, "C": 71.34, "E": 34.66,   # left boundary (C+D merged)
            "H": 16.89, "I": 74.00, "K": 34.66,   # right boundary (I+J merged)
            "D4": 48.67, "KL4": 69.32,             # loto# / date headers
            "D_HDR": 238.6, "BC36": 38.78}         # D5:L5 spans / B36:C36
_L1_ROW_HDR  = 24.8    # rows 4-8
_L1_ROW_DATA = 39.8    # rows 11-31
_L1_ROW_QO   = 45.0    # row 36

_L2_COL = {"A": 55.67, "B": 55.33, "C": 15.88, "E": 55.67}
_L2_ROW_HDR = 45.0
_L2_ROW_VER = 45.0


def _cell_pts(col_chars: float) -> float:
    """Convert Excel column char-width to approximate points."""
    return (col_chars * 7 + 5) * 72 / 96


def _best_loto_size(text: str, col_chars: float, row_pts: float, fill: float = 0.80) -> int:
    """
    Largest integer font size where text fits the cell width,
    capped at fill × row height. Min 8, max 36.
    """
    if not text:
        return 11
    col_pts = _cell_pts(col_chars)
    cap = max(8, int(row_pts * fill))
    for size in range(min(cap, 36), 7, -1):
        if _helv_w(text, float(size)) <= col_pts:
            return size
    return 8


def _lf(text: str, col_chars: float, row_pts: float, bold: bool = False) -> Font:
    """Return a dynamically-sized Times New Roman Font for a LOTO cell."""
    return Font(name="Times New Roman",
                size=_best_loto_size(text, col_chars, row_pts),
                bold=bold)


def _la(horizontal: str = "center") -> Alignment:
    return Alignment(vertical="bottom", horizontal=horizontal, wrap_text=True)


def _fill_loto(loto_id: int, tmp_dir: Path) -> Path:
    db = get_db()

    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if loto is None:
        abort(404)

    boundaries = db.execute(
        "SELECT position, equipment, location, energy_type, isolation_device, device_position"
        " FROM loto_boundaries WHERE loto_id = ? ORDER BY position",
        (loto_id,),
    ).fetchall()

    verifiers = db.execute(
        "SELECT name, date FROM loto_verifiers WHERE loto_id = ? ORDER BY position",
        (loto_id,),
    ).fetchall()

    src = TEMPLATE_DIR / "loto" / "loto-form.xlsx"
    dest = tmp_dir / f"LOTO-{loto_id}.xlsx"
    shutil.copy2(src, dest)

    wb = openpyxl.load_workbook(dest)
    ws1 = wb["Lockout-Tagout Form Part I"]
    ws2 = wb["Lockout-Tagout Form Part II"]

    def w1(cell, text, col_chars, row_pts, bold=False, h="center"):
        ws1[cell] = text
        ws1[cell].font      = _lf(text, col_chars, row_pts, bold)
        ws1[cell].alignment = _la(h)

    def w2(cell, text, col_chars, row_pts, bold=False, h="center"):
        ws2[cell] = text
        ws2[cell].font      = _lf(text, col_chars, row_pts, bold)
        ws2[cell].alignment = _la(h)

    # ── Part I header fields ──────────────────────────────────────────────────
    # All header text is capped by the LOTO number row height (24.8pt → max 19pt)
    # so every header field is visually the same size. Equipment/job desc are
    # left-aligned and not bold so they read as data, not labels.
    w1("D4", loto["loto_number"] or "",                                    _L1_COL["D4"],    _L1_ROW_HDR, bold=True)
    w1("K4", loto["date"] or "",                                           _L1_COL["KL4"],   _L1_ROW_HDR, bold=True)
    w1("D5", loto["equipment_description"] or loto["equipment_id"] or "",  _L1_COL["D_HDR"], _L1_ROW_HDR, bold=False, h="left")
    w1("D6", loto["job_description"] or "",                                _L1_COL["D_HDR"], _L1_ROW_HDR, bold=False, h="left")

    # ── Part I boundary rows — one consistent size per row ────────────────────
    by_pos = {b["position"]: b for b in boundaries}
    for i, row_num in enumerate(range(11, 33)):
        rh   = _L1_ROW_DATA
        left  = by_pos.get(i + 1)
        right = by_pos.get(i + 23)

        # Compute the minimum size that fits every populated cell in this row,
        # then apply that single size to all cells so columns look consistent.
        sizes = []
        lt = rt = ""
        if left:
            lt = (left["equipment"] or "") + (" — " + left["location"] if left["location"] else "")
            sizes += [
                _best_loto_size(left["isolation_device"] or "", _L1_COL["B"], rh),
                _best_loto_size(lt,                             _L1_COL["C"], rh),
                _best_loto_size(left["device_position"] or "",  _L1_COL["E"], rh),
            ]
        if right:
            rt = (right["equipment"] or "") + (" — " + right["location"] if right["location"] else "")
            sizes += [
                _best_loto_size(right["isolation_device"] or "", _L1_COL["H"], rh),
                _best_loto_size(rt,                              _L1_COL["I"], rh),
                _best_loto_size(right["device_position"] or "",  _L1_COL["K"], rh),
            ]

        row_sz   = min(sizes) if sizes else 11
        row_font = Font(name="Times New Roman", size=row_sz)

        if left:
            ws1[f"B{row_num}"] = left["isolation_device"] or "";  ws1[f"B{row_num}"].font = row_font; ws1[f"B{row_num}"].alignment = _la()
            ws1[f"C{row_num}"] = lt;                               ws1[f"C{row_num}"].font = row_font; ws1[f"C{row_num}"].alignment = _la("left")
            ws1[f"E{row_num}"] = left["device_position"] or "";   ws1[f"E{row_num}"].font = row_font; ws1[f"E{row_num}"].alignment = _la()
        if right:
            ws1[f"H{row_num}"] = right["isolation_device"] or ""; ws1[f"H{row_num}"].font = row_font; ws1[f"H{row_num}"].alignment = _la()
            ws1[f"I{row_num}"] = rt;                               ws1[f"I{row_num}"].font = row_font; ws1[f"I{row_num}"].alignment = _la("left")
            ws1[f"K{row_num}"] = right["device_position"] or "";  ws1[f"K{row_num}"].font = row_font; ws1[f"K{row_num}"].alignment = _la()

    # ── Part I group leader — smaller, not bold ────────────────────────────────
    ws1["B36"] = loto["group_leader"] or ""
    ws1["B36"].font      = Font(name="Times New Roman", size=14)
    ws1["B36"].alignment = _la()

    # ── Part I page setup ──────────────────────────────────────────────────────
    ws1.page_setup.orientation = "landscape"

    # ── Part II header / lockout person — smaller, not bold ───────────────────
    w2("A6", loto["group_leader"] or "", _L2_COL["A"], _L1_ROW_HDR, bold=False)
    w2("E6", loto["date"] or "",         _L2_COL["E"], _L1_ROW_HDR, bold=False)

    # ── Part II verifier rows — consistent per-row size, not bold ────────────────
    for i, v in enumerate(verifiers[:20]):
        r = 18 + i
        row_sz = min(
            _best_loto_size(v["name"] or "", _L2_COL["B"], _L1_ROW_HDR),
            _best_loto_size(v["date"] or "", _L2_COL["C"], _L1_ROW_HDR),
        )
        ver_font = Font(name="Times New Roman", size=row_sz)
        ws2[f"B{r}"] = v["name"] or ""; ws2[f"B{r}"].font = ver_font; ws2[f"B{r}"].alignment = _la()
        ws2[f"C{r}"] = v["date"] or ""; ws2[f"C{r}"].font = ver_font; ws2[f"C{r}"].alignment = _la()

    # ── Part II page setup ─────────────────────────────────────────────────────
    ws2.page_setup.orientation = "portrait"
    ws2.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws2.page_setup.fitToWidth = 1
    ws2.page_setup.fitToHeight = 1

    wb.save(dest)

    lo_env = os.environ.copy()
    lo_env["HOME"] = "/tmp"   # LibreOffice needs a writable home for its user profile
    with _lo_lock:
        result = subprocess.run(
            [
                "libreoffice", "--headless",
                "--convert-to", "pdf",
                str(dest),
                "--outdir", str(tmp_dir),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=lo_env,
        )
    if result.returncode != 0:
        current_app.logger.error("LibreOffice failed: %s", result.stderr)
        abort(500)

    return tmp_dir / f"LOTO-{loto_id}.pdf"


def _fill_swp(swp_id: int, tmp_dir: Path) -> Path:
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if swp is None:
        abort(404)

    src    = TEMPLATE_DIR / "swp" / "swp-form.pdf"
    filled = tmp_dir / f"SWP-{swp_id}-filled.pdf"
    dest   = tmp_dir / f"SWP-{swp_id}.pdf"

    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    writer.append(reader)

    # ── Text fields ───────────────────────────────────────────────────────────
    # Form field names match swp_permits columns (see tools/make_templates.py).
    # Attendees are signed by hand on the printed form, so they aren't filled.
    text_cols = (
        "company", "permit_number", "date", "time", "location", "scope_of_work",
        "tha_other_txt", "hazard_mitigation", "additional_comments", "ts_other1_txt",
        "permit_loto", "permit_hot_work", "permit_confined_sp", "permit_excavation",
        "permit_other", "issued_date", "issued_time", "issued_auth_name",
        "closed_date", "closed_time", "closed_wl_name", "closed_auth_name",
    )
    text_fields = {col: swp[col] or "" for col in text_cols}
    # Work lead falls back to the top-level work lead for permits issued
    # before issued_wl_name was captured via the signature flow.
    text_fields["issued_wl_name"] = swp["issued_wl_name"] or swp["work_lead"] or ""

    # Set /V on every field (for PDF viewers that display live) and inject
    # correct single-byte appearance streams (for Ghostscript flatten).
    for page in writer.pages:
        writer.update_page_form_field_values(page, text_fields, auto_regenerate=False)
    _inject_tx_appearances(writer, text_fields)

    # ── Checkboxes ────────────────────────────────────────────────────────────
    checkbox_cols = [c for c in swp.keys()
                     if c.startswith(("he_", "tha_", "ppe_", "ts_")) and not c.endswith("_txt")]
    checkbox_map = {col: swp[col] for col in checkbox_cols}
    for q in ("first_time", "training", "inspection", "continuation"):
        checkbox_map[f"{q}_yes"] = bool(swp[q])
        checkbox_map[f"{q}_no"] = not swp[q]
    for permit in ("permit_loto", "permit_hot_work", "permit_confined_sp",
                   "permit_excavation", "permit_other"):
        checkbox_map[f"{permit}_chk"] = bool(swp[permit])

    _inject_btn_appearances(writer, checkbox_map)

    with open(filled, "wb") as fh:
        writer.write(fh)

    # ── Ghostscript flatten — bakes form fields into static page content ───────
    result = subprocess.run(
        [
            "gs", "-q", "-dBATCH", "-dNOPAUSE",
            "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
            f"-sOutputFile={dest}", str(filled),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        current_app.logger.error("Ghostscript failed: %s", result.stderr)
        abort(500)

    return dest


@pdf_bp.route("/loto/<int:loto_id>")
@login_required
def loto_pdf(loto_id):
    from app.roles import ADMIN, IT_ADMIN
    db = get_db()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if loto is None:
        abort(404)

    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    if not is_admin and loto["created_by"] != current_user.id:
        abort(403)
    # Non-management cannot download/print until management-approved
    if not is_admin and loto["status"] not in ("active", "released"):
        abort(403)

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = _fill_loto(loto_id, Path(tmp))
        filename = f"{loto['loto_number'] or ('LOTO-' + str(loto_id))}.pdf"
        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )


@pdf_bp.route("/swp/<int:swp_id>")
@login_required
def swp_pdf(swp_id):
    from app.roles import ADMIN, IT_ADMIN
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if swp is None:
        abort(404)

    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    if not is_admin and swp["created_by"] != current_user.id:
        abort(403)
    # Non-management cannot download/print until management-approved
    if not is_admin and swp["status"] not in ("issued", "closed"):
        abort(403)

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = _fill_swp(swp_id, Path(tmp))
        filename = f"{swp['permit_number'] or ('SWP-' + str(swp_id))}.pdf"
        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )


# ── Printer helpers ───────────────────────────────────────────────────────────

def _printer_name() -> str:
    return os.environ.get("PRINTER_NAME", "").strip()


def _cups_env() -> dict:
    """Build subprocess env with CUPS_SERVER forwarded if set."""
    env = os.environ.copy()
    sock = os.environ.get("CUPS_SERVER", "")
    if sock:
        env["CUPS_SERVER"] = sock
    return env


def _printer_available() -> bool:
    name = _printer_name()
    try:
        cmd = ["lpstat", "-p", name] if name else ["lpstat", "-d"]
        r = subprocess.run(cmd, capture_output=True, timeout=3, env=_cups_env())
        return r.returncode == 0
    except Exception:
        return False


def _lp_print(pdf_path: Path) -> bool:
    name = _printer_name()
    cmd = ["lp"] + (["-d", name] if name else []) + [str(pdf_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30, env=_cups_env())
        return r.returncode == 0
    except Exception:
        return False


def _bg_print(app, fill_fn, permit_id: int, tmp_dir: str) -> None:
    """Generate PDF and spool to printer in a background thread."""
    with app.app_context():
        try:
            pdf_path = fill_fn(permit_id, Path(tmp_dir))
            _lp_print(pdf_path)
        except Exception as exc:
            app.logger.error("Async print failed (permit %s): %s", permit_id, exc)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Printer status + print routes ─────────────────────────────────────────────

_BTN_PRINT = (
    '<button hx-post="{url}" hx-swap="outerHTML" class="btn-secondary"'
    ' style="width:auto;" title="Print to {name}">'
    'Print</button>'
)
_BTN_DISABLED = (
    '<button class="btn-secondary" disabled style="width:auto;opacity:.45;'
    'cursor:not-allowed;" title="Printer unavailable">Print</button>'
)


@pdf_bp.route("/printer-status")
@login_required
def printer_status():
    ptype = request.args.get("type", "swp")
    pid   = request.args.get("id",   "0")
    available = _printer_available()
    name = _printer_name() or "default printer"
    if available:
        url = f"/pdf/{ptype}/{pid}/print"
        html = _BTN_PRINT.format(url=url, name=name)
    else:
        html = _BTN_DISABLED
    return html, 200, {"Content-Type": "text/html"}


@pdf_bp.route("/swp/<int:swp_id>/print", methods=["POST"])
@login_required
def swp_print(swp_id):
    db = get_db()
    swp = db.execute("SELECT * FROM swp_permits WHERE id = ?", (swp_id,)).fetchone()
    if swp is None:
        abort(404)
    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    if not is_admin and swp["created_by"] != current_user.id:
        abort(403)

    tmp_dir = tempfile.mkdtemp()
    threading.Thread(
        target=_bg_print,
        args=(current_app._get_current_object(), _fill_swp, swp_id, tmp_dir),
        daemon=True,
    ).start()
    return '<span class="badge badge-ok">Sent to printer</span>', 200, {"Content-Type": "text/html"}


@pdf_bp.route("/loto/<int:loto_id>/print", methods=["POST"])
@login_required
def loto_print(loto_id):
    db = get_db()
    loto = db.execute("SELECT * FROM loto_permits WHERE id = ?", (loto_id,)).fetchone()
    if loto is None:
        abort(404)
    is_admin = current_user.role in (ADMIN, IT_ADMIN)
    if not is_admin and loto["created_by"] != current_user.id:
        abort(403)

    tmp_dir = tempfile.mkdtemp()
    threading.Thread(
        target=_bg_print,
        args=(current_app._get_current_object(), _fill_loto, loto_id, tmp_dir),
        daemon=True,
    ).start()
    return '<span class="badge badge-ok">Sent to printer</span>', 200, {"Content-Type": "text/html"}
