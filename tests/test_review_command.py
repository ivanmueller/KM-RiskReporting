"""`riskparse review`: the one-command Step 1 workbook (synthetic data only)."""

import zipfile

import pytest
from openpyxl import load_workbook

from doc_fixtures import build_msg, build_pdf, build_xlsx
from riskparse.review import ReviewError, build_review

BASE = "Risk Program/1. Contracts/"


def _zip(tmp_path):
    z = tmp_path / "Risk Program.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(BASE + "Physical Contracts/SYN_phys.pdf", build_pdf([[
            "SYNTHETIC physical confirmation", "Buyer: Alpha", "Seller: Us", "Price: 2.50"]]))
        zf.writestr(BASE + "Physical Contracts/SYN_scan.pdf", build_pdf([[]]))
        zf.writestr(BASE + "Financial Contracts/SYN_swap.xlsx", build_xlsx())
        zf.writestr(BASE + "Evergreen Contracts/SYN_evergreen.msg", build_msg(
            body="=HYPERLINK(\"http://x\")\nSYNTHETIC value clause",
            attachments=[("SYN_agreement.pdf",
                          build_pdf([["SYNTHETIC evergreen agreement text"]]), 1),
                         ("inner.msg", b"x", 5)]))
        zf.writestr("../escape.pdf", b"bad")
    return z


def test_review_builds_one_workbook_from_the_zip(tmp_path):
    z = _zip(tmp_path)
    before = z.read_bytes()
    r = build_review(z, tmp_path / "out")
    assert z.read_bytes() == before                         # input untouched
    wb = load_workbook(r.workbook)
    assert wb.sheetnames == ["Summary", "Documents", "Text", "Fields", "Labels", "Issues"]
    docs = list(wb["Documents"].iter_rows(min_row=2, values_only=True))
    files = {row[2]: row for row in docs}
    assert set(files) == {"SYN_phys.pdf", "SYN_scan.pdf", "SYN_swap.xlsx", "SYN_evergreen.msg",
                          "SYN_agreement.pdf", "inner.msg"}
    assert files["SYN_agreement.pdf"][3] == files["SYN_evergreen.msg"][0]   # attached_to
    assert files["SYN_scan.pdf"][6] == "NEEDS_OCR"
    assert files["SYN_phys.pdf"][4] == "PHYSICAL" and files["SYN_phys.pdf"][12]  # layout group
    issues = {(row[0], row[1]) for row in wb["Issues"].iter_rows(min_row=2, values_only=True)}
    assert ("../escape.pdf", "UNSAFE_PATH") in issues        # refused member is listed
    assert any(kind == "NEEDS_OCR" for _, kind in issues)
    assert any(kind == "ATTACHMENT_NOT_EXTRACTED" for _, kind in issues)


def test_text_sheet_has_locations_and_never_formulas(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out")
    ws = load_workbook(r.workbook)["Text"]
    rows = list(ws.iter_rows(min_row=2))
    by_loc = {(c[1].value, c[2].value): c[3] for c in rows}
    assert by_loc[("SYN_phys.pdf", "p1:L4")].value == "Price: 2.50"
    assert by_loc[("SYN_swap.xlsx", "Confirm!B2")].value == "2.65"
    formula_like = by_loc[("SYN_evergreen.msg", "body:L1")]
    assert formula_like.value.startswith("=HYPERLINK") and formula_like.data_type == "s"


def test_review_of_a_folder_and_refusals(tmp_path):
    folder = tmp_path / "contracts"
    (folder / "Physical Contracts").mkdir(parents=True)
    (folder / "Physical Contracts" / "a.pdf").write_bytes(build_pdf([["SYNTHETIC text line"]]))
    r = build_review(folder, tmp_path / "out")
    assert len(r.documents) == 1
    from riskparse.fswalk import UnsafeOutputError
    with pytest.raises(UnsafeOutputError):
        build_review(folder, tmp_path / "out")              # never overwrites
    with pytest.raises(UnsafeOutputError):
        build_review(folder, folder / "inside")             # never writes into the source
    junk = tmp_path / "x.txt"
    junk.write_text("x")
    with pytest.raises(ReviewError):
        build_review(junk, tmp_path / "out2")


def test_cli_review_default_output_next_to_source(tmp_path, capsys):
    from riskparse.cli import main
    z = _zip(tmp_path)
    assert main(["review", str(z)]) == 0
    out = capsys.readouterr().out
    assert "Open this workbook:" in out and "document(s)" in out
    made = [p for p in tmp_path.iterdir() if p.name.startswith("Risk Program_review_")]
    assert len(made) == 1 and (made[0] / "review.xlsx").is_file()


def test_fields_and_labels_sheets(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out")
    wb = load_workbook(r.workbook)
    fields = [row for row in wb["Fields"].iter_rows(min_row=2, values_only=True)]
    phys = {row[3]: row for row in fields if row[1] == "SYN_phys.pdf"}
    assert phys["price"][5] == "2.50" and phys["price"][6] == "p1:L4"
    assert phys["buyer"][5] == "Alpha" and phys["seller"][5] == "Us"
    assert all(row[8] is None for row in fields)            # Risk check columns left blank
    labels = list(wb["Labels"].iter_rows(min_row=2, values_only=True))
    assert labels and all(len(row) == 5 for row in labels)
    assert not any("2.50" in str(cell) or "Alpha" in str(cell) for row in labels for cell in row)


def test_summary_reports_field_coverage(tmp_path):
    from riskparse.review import format_summary
    text = format_summary(build_review(_zip(tmp_path), tmp_path / "out"))
    assert "Proposed fields" in text and "price" in text
    assert "Alpha" not in text and "2.50" not in text     # counts only
