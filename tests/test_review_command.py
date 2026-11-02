"""`riskparse review`: confirmations in, the firm's raw-data-format out (synthetic only)."""

import zipfile

import pytest
from openpyxl import Workbook, load_workbook

from confirm_fixtures import calrock_msg, isda_pdf, marex_pdf, marex_xls, modern_xlsx, ox_pdf
from doc_fixtures import build_pdf
from riskparse.rawformat import DEFAULT_HEADERS
from riskparse.review import ReviewError, build_review, format_summary

BASE = "1. Contracts/"


def _zip(tmp_path):
    z = tmp_path / "1. Contracts.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        p = BASE + "Physical Contracts/"
        zf.writestr(p + "Trade-Confirm-Buyer-90001-1.xlsx", modern_xlsx(number=90001))
        zf.writestr(p + "Trade-Confirm-Seller-90002-1.xlsx",
                    modern_xlsx(number=90002, side="Sell", product="LSB", volume=2500.0,
                                updated="2026-10-06 11:11:00 AM"))
        zf.writestr(p + "UPDATED-Trade-Confirm-Seller-90002-2.xlsx",
                    modern_xlsx(number=90002, side="Sell", product="LSB", volume=4000.0,
                                updated="2026-10-06 11:16:16 AM"))
        zf.writestr(p + "Seller Confirmation for Deal 2099001.pdf", ox_pdf())
        zf.writestr(p + "Canadian Crude_KM_119999001.pdf", marex_pdf())
        zf.writestr(p + "Canadian_Crude_summary.xls", marex_xls())
        zf.writestr(p + "CalRock Energy trade confirmation #82999001.msg", calrock_msg())
        zf.writestr(BASE + "Financial Contracts/Kingston 181000001.pdf", isda_pdf())
        zf.writestr(p + "random letter.pdf", build_pdf([["SYNTHETIC unrelated letter text"]]))
        zf.writestr("../escape.pdf", b"bad")
    return z


def test_review_produces_one_row_per_trade_in_the_firm_format(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out")
    assert len(r.trades) == 6           # 9 recognised docs: duplicate + amendment merged
    wb = load_workbook(r.raw_data)
    assert wb.sheetnames == ["raw-data-format", "checks"]
    rows = list(wb["raw-data-format"].iter_rows(values_only=True))
    assert list(rows[0]) == DEFAULT_HEADERS and len(rows) == 7
    by_no = {row[0]: dict(zip(DEFAULT_HEADERS, row, strict=True)) for row in rows[1:]}
    assert by_no["90002"]["Quantity"] == 4000            # amendment applied
    assert by_no["2099001"]["Fixed Differential"] == -6.1
    assert by_no["2099001"]["Trade Date"].year == 2026   # a real date, not text
    assert by_no["181000001"]["Fixed Differential"] is None
    assert by_no["181000001"]["Fixed Price"] == 106.12    # swap flat price in its own column
    assert by_no["2099001"]["Fixed Price"] is None
    swap = next(t for t in r.trades if t.contract_no == "181000001")
    assert not any("checks sheet only" in f for f in swap.flags)
    assert any(f.startswith("price in CAD/bbl") for f in swap.flags)


def test_format_without_fixed_price_column_flags_the_swap(tmp_path):
    wb = Workbook()
    wb.active.append(DEFAULT_HEADERS[:-1])                # the old 14-column format
    wb.save(tmp_path / "old-format.xlsx")
    r = build_review(_zip(tmp_path), tmp_path / "out", format_path=tmp_path / "old-format.xlsx")
    swap = next(t for t in r.trades if t.contract_no == "181000001")
    assert any("no Fixed Price column" in f for f in swap.flags)
    assert "fixed price on checks sheet only" in format_summary(r)


def test_checks_evidence_documents_and_issues(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out")
    wb = load_workbook(r.workbook)
    assert wb.sheetnames == ["summary", "raw-data-format", "checks", "evidence", "names",
                             "documents", "text", "issues"]
    checks = {row[0]: row for row in wb["checks"].iter_rows(min_row=2, values_only=True)}
    assert checks["181000001"][6] == 106.12        # swap fixed price
    assert "AMENDED" in checks["90002"][9]
    assert "key terms agree" in checks["119999001"][9]
    ev = list(wb["evidence"].iter_rows(min_row=2, values_only=True))
    assert any(row[1] == "2099001" and row[2] == "Fixed Differential" and "p1:" in row[4]
               for row in ev)
    issues = {row[1] for row in wb["issues"].iter_rows(min_row=2, values_only=True)}
    assert {"UNRECOGNISED", "UNSAFE_PATH"} <= issues
    parsed_as = {row[2]: row[7] for row in wb["documents"].iter_rows(min_row=2,
                                                                     values_only=True)}
    assert parsed_as["random letter.pdf"] == "UNRECOGNISED"


def test_summary_is_counts_only(tmp_path):
    text = format_summary(build_review(_zip(tmp_path), tmp_path / "out"))
    assert "Trades: 6" in text and "Unrecognised documents (not in raw-data): 1" in text
    assert "SYN" not in text and "Libby" not in text and "-6.1" not in text


def test_firm_format_file_headers_are_followed(tmp_path):
    wb = Workbook()
    for i, h in enumerate(["Contract #", "Quantity ", "Mystery Column"], start=1):
        wb.active.cell(row=1, column=i, value=h)
    wb.save(tmp_path / "raw-data-format.xlsx")
    r = build_review(_zip(tmp_path), tmp_path / "out", format_path=tmp_path / "raw-data-format")
    rows = list(load_workbook(r.raw_data)["raw-data-format"].iter_rows(values_only=True))
    assert list(rows[0]) == ["Contract #", "Quantity ", "Mystery Column"]
    assert r.format_unmapped == ["Mystery Column"]
    assert "[header not recognised]" in format_summary(r)


def test_refusals(tmp_path):
    folder = tmp_path / "contracts"
    folder.mkdir()
    build_review(folder, tmp_path / "out")
    from riskparse.fswalk import UnsafeOutputError
    with pytest.raises(UnsafeOutputError):
        build_review(folder, tmp_path / "out")
    with pytest.raises(UnsafeOutputError):
        build_review(folder, folder / "inside")
    junk = tmp_path / "x.txt"
    junk.write_text("x")
    with pytest.raises(ReviewError):
        build_review(junk, tmp_path / "out2")


def test_cli(tmp_path, capsys):
    from riskparse.cli import main
    z = _zip(tmp_path)
    assert main(["review", str(z)]) == 0
    out = capsys.readouterr().out
    assert "Trades: 6" in out and "raw-data (your format + checks):" in out
    assert main(["review", str(z), "--format", str(tmp_path / "nope.xlsx")]) == 4
    assert main(["review", str(z), "--us", "<our company name>"]) == 4
