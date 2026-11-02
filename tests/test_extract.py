"""Text extraction from synthetic .xlsx, .pdf and .msg files."""

import io
from datetime import UTC, datetime

import pytest
from pypdf import PdfReader, PdfWriter

from doc_fixtures import build_msg, build_pdf, build_xlsx
from riskparse.extract import base
from riskparse.extract.dispatch import extract_bytes


def _seg(result):
    return {s.location: s.text for s in result.segments}


# --- Excel ---------------------------------------------------------------------------

def test_xlsx_cells_keep_location_raw_value_and_format():
    r = extract_bytes(build_xlsx(), "XLSX")
    seg = {s.location: s for s in r.segments}
    assert seg["Confirm!B2"].text == "2.65"
    assert seg["Confirm!B3"].text == "0.05" and seg["Confirm!B3"].meta["number_format"] == "0%"
    assert seg["Confirm!B4"].meta["type"] == "datetime"
    assert "Confirm!A6" not in seg                      # whitespace-only cell skipped


def test_xlsx_flags_hidden_sheets_and_uncalculated_formulas():
    r = extract_bytes(build_xlsx(), "XLSX")
    assert r.status == base.PARTIAL_TEXT                 # B5 formula has no saved value
    assert r.metadata["formulas_without_cached_value"] == 1
    assert r.metadata["hidden_sheets"] == ["Workings (hidden)"]
    hidden = next(s for s in r.segments if s.location == "Workings!A1")
    assert hidden.meta["sheet_state"] == "hidden"        # extracted, and flagged
    assert any("Confirm!B5" in w for w in r.warnings)


def test_xlsx_formula_with_saved_value_reports_value_and_formula(tmp_path):
    from openpyxl import Workbook, load_workbook
    wb = Workbook()
    wb.active["A1"] = "=1+1"
    p = tmp_path / "f.xlsx"
    wb.save(p)
    # simulate Excel having saved a cached value by editing the XML
    import zipfile
    with zipfile.ZipFile(p) as z:
        files = {n: z.read(n) for n in z.namelist()}
    sheet = "xl/worksheets/sheet1.xml"
    files[sheet] = files[sheet].replace(b"<f>1+1</f><v></v>", b"<f>1+1</f><v>2</v>").replace(
        b"<f>1+1</f>", b"<f>1+1</f><v>2</v>") if b"<v>2</v>" not in files[sheet] else files[sheet]
    out = tmp_path / "g.xlsx"
    with zipfile.ZipFile(out, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    load_workbook(out)  # still a valid workbook
    r = extract_bytes(out.read_bytes(), "XLSX")
    seg = {s.location: s for s in r.segments}
    assert seg["Sheet!A1"].text == "2" and seg["Sheet!A1"].meta["formula"] == "=1+1"
    assert r.status == base.TEXT_EXTRACTED


def test_corrupt_workbooks_fail_cleanly():
    assert extract_bytes(b"\xd0\xcf\x11\xe0", "XLS", filename="old.xls").status == base.FAILED
    assert extract_bytes(b"not a zip", "XLSX").status == base.FAILED


def test_legacy_xls_cells_dates_and_numbers():
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Summary")
    ws.write(0, 0, "Reference")
    ws.write(0, 1, "Price")
    ws.write(1, 0, 119314923.0)
    ws.write(1, 1, -6.5)
    ws.write(1, 2, datetime(2026, 10, 5, 14, 53), xlwt.easyxf(num_format_str="YYYY-MM-DD"))
    hidden = wb.add_sheet("Hidden")
    hidden.write(0, 0, "SYNTHETIC")
    hidden.visibility = 1
    buf = io.BytesIO()
    wb.save(buf)
    r = extract_bytes(buf.getvalue(), "XLS", filename="x.xls")
    seg = {s.location: s for s in r.segments}
    assert r.status == base.TEXT_EXTRACTED
    assert seg["Summary!A2"].text == "119314923.0" and seg["Summary!B2"].text == "-6.5"
    assert seg["Summary!C2"].meta["type"] == "datetime"
    assert seg["Summary!C2"].text.startswith("2026-10-05")
    assert r.metadata["hidden_sheets"] == ["Hidden (hidden)"]


# --- PDF -----------------------------------------------------------------------------

def test_pdf_lines_with_page_locations():
    data = build_pdf([["SYNTHETIC confirmation page one", "Price (CAD/GJ): 2.65 \\ net"],
                      ["SYNTHETIC page two has enough text"]])
    r = extract_bytes(data, "PDF")
    seg = _seg(r)
    assert r.status == base.TEXT_EXTRACTED and r.metadata["pages"] == 2
    assert seg["p1:L1"] == "SYNTHETIC confirmation page one"
    assert seg["p1:L2"] == "Price (CAD/GJ): 2.65 \\ net"
    assert seg["p2:L1"] == "SYNTHETIC page two has enough text"


def test_pdf_scanned_pages_are_reported_not_skipped():
    partial = extract_bytes(build_pdf([["SYNTHETIC real text on page one"], []]), "PDF")
    assert partial.status == base.PARTIAL_TEXT
    assert partial.metadata["pages_without_text"] == [2]
    scanned = extract_bytes(build_pdf([[], []]), "PDF")
    assert scanned.status == base.NEEDS_OCR and scanned.segments == []


def _encrypt(data, user_password):
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    w.encrypt(user_password=user_password, owner_password="owner", algorithm="RC4-128")
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def test_pdf_encryption():
    data = build_pdf([["SYNTHETIC protected text line here"]])
    assert extract_bytes(_encrypt(data, "secret"), "PDF").status == base.ENCRYPTED
    open_copy = extract_bytes(_encrypt(data, ""), "PDF")
    assert open_copy.status == base.TEXT_EXTRACTED
    assert any("empty user password" in w for w in open_copy.warnings)


def test_pdf_corrupt():
    assert extract_bytes(b"%PDF-1.4 garbage", "PDF").status == base.FAILED


# --- Outlook .msg --------------------------------------------------------------------

def test_msg_headers_body_and_sent_time():
    sent = datetime(2026, 10, 14, 16, 5, tzinfo=UTC)
    r = extract_bytes(build_msg(subject="SYNTHETIC Confirm TC-1", body="Line 1\n\nLine 3",
                                sent=sent), "MSG")
    seg = _seg(r)
    assert r.status == base.PARTIAL_TEXT or r.status == base.TEXT_EXTRACTED
    assert seg["header:Subject"] == "SYNTHETIC Confirm TC-1"
    assert seg["header:From-Address"] == "sender@synthetic.invalid"
    assert seg["header:Sent"] == sent.isoformat()
    assert seg["body:L1"] == "Line 1" and seg["body:L3"] == "Line 3"   # blank line keeps numbering


def test_msg_ansi_strings_use_message_codepage():
    r = extract_bytes(build_msg(subject="SYNTHETIC café", body="Énergie", unicode=False,
                                codepage=1252), "MSG")
    seg = _seg(r)
    assert seg["header:Subject"] == "SYNTHETIC café" and seg["body:L1"] == "Énergie"


def test_msg_html_only_body_is_rendered_to_text():
    r = extract_bytes(build_msg(body=None, html="<html><head><style>x{}</style></head><body>"
                                "<p>SYNTHETIC price</p><p>2.65 &amp; more</p></body></html>"),
                      "MSG")
    lines = [s.text for s in r.segments if s.kind == "html_body_line"]
    assert lines == ["SYNTHETIC price", "2.65 & more"]
    assert r.metadata["body_source"] == "html"


def test_msg_rtf_only_body_is_reported():
    r = extract_bytes(build_msg(body=None, rtf_only=True), "MSG")
    assert r.status == base.PARTIAL_TEXT and any("RTF" in w for w in r.warnings)


def test_msg_attachments_by_value_embedded_and_reference():
    pdf = build_pdf([["SYNTHETIC attached confirmation"]])
    r = extract_bytes(build_msg(attachments=[("SYN_confirm.pdf", pdf, 1),
                                             ("inner.msg", b"x", 5),
                                             ("link.pdf", None, 7)]), "MSG")
    by_name = {a.filename: a for a in r.attachments}
    confirm = by_name["SYN_confirm.pdf"]
    assert confirm.data == pdf and confirm.method == "BY_VALUE"
    assert by_name["inner.msg"].data is None and by_name["inner.msg"].method == "EMBEDDED_MESSAGE"
    assert by_name["link.pdf"].data is None and by_name["link.pdf"].method == "BY_REFERENCE"
    assert r.status == base.PARTIAL_TEXT
    assert [s.location for s in r.segments if s.kind == "attachment"] == [
        "attachment:1", "attachment:2", "attachment:3"]


def test_msg_not_ole_is_failed():
    r = extract_bytes(b"From: x\r\nSubject: renamed eml\r\n\r\nbody", "MSG")
    assert r.status == base.FAILED and "renamed .eml" in r.warnings[0]


# --- Dispatch and limits -------------------------------------------------------------

def test_unknown_type_is_unsupported_with_reason():
    r = extract_bytes(b"x", "DOCX")
    assert r.status == base.UNSUPPORTED and r.warnings


def test_segment_limit_fails_safely(monkeypatch):
    monkeypatch.setattr(base, "MAX_SEGMENTS", 3)
    r = extract_bytes(build_pdf([["SYNTHETIC a line of text"] * 10]), "PDF")
    assert r.status == base.FAILED and "segments" in r.warnings[0]


def test_text_hash_changes_when_any_segment_changes():
    a = extract_bytes(build_pdf([["SYNTHETIC price line 2.65 here"]]), "PDF")
    b = extract_bytes(build_pdf([["SYNTHETIC price line 2.66 here"]]), "PDF")
    assert a.text_sha256() != b.text_sha256()


@pytest.mark.parametrize("ftype", ["PDF", "XLSX", "MSG"])
def test_random_bytes_never_raise(ftype):
    import os
    r = extract_bytes(os.urandom(4096), ftype)
    assert r.status == base.FAILED
