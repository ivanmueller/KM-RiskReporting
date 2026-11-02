"""Filling the firm's raw-data-format from proposed fields (synthetic data only)."""

import zipfile

from openpyxl import Workbook, load_workbook

from doc_fixtures import build_pdf, build_xlsx
from riskparse.extract.dispatch import extract_bytes
from riskparse.fields import propose_fields
from riskparse.rawformat import DEFAULT_HEADERS, build_rows, read_format_headers
from riskparse.review import build_review

CONFIRM = ["SYNTHETIC CRUDE CONFIRMATION", "Contract #: SYN-001", "Trade Date: Oct 5, 2026",
           "Buyer: Synthetic Us Ltd", "Seller: SYN Counterparty Inc", "Grade: SYN Light",
           "Quantity: 5,000", "Unit: bbl/d", "Primary Index: SYN CMA",
           "Fixed Differential: -2.50", "Term: Nov 2026", "Start Date: Nov 1, 2026",
           "End Date: Nov 30, 2026", "Trader: SYN Trader"]


def _doc(lines, doc="D001", name="c.pdf"):
    props = propose_fields(extract_bytes(build_pdf([lines]), "PDF"))
    return [{"doc": doc, "file": name, "attached_to": ""}], {doc: props}


def test_every_format_column_filled_from_a_confirmation():
    docs, props = _doc(CONFIRM)
    rows, unmapped = build_rows(docs, props, DEFAULT_HEADERS, us=["Synthetic Us"])
    assert unmapped == [] and len(rows) == 1
    v = rows[0].values
    assert v == {"Contract #": "SYN-001", "Trade Date": "Oct 5, 2026",
                 "Start Date": "Nov 1, 2026", "End Date": "Nov 30, 2026", "Term": "Nov 2026",
                 "Trader": "SYN Trader", "Counterparty": "SYN Counterparty Inc",
                 "Seller": "SYN Counterparty Inc", "Quantity": "5,000", "Unit": "bbl/d",
                 "Grade": "SYN Light", "Primary Index": "SYN CMA",
                 "Fixed Differential": "-2.50"}
    assert "derived" in rows[0].evidence["Counterparty"]
    assert rows[0].evidence["Grade"].startswith("p1:L6")


def test_counterparty_not_derived_without_us_or_when_ambiguous():
    docs, props = _doc(CONFIRM)
    rows, _ = build_rows(docs, props, DEFAULT_HEADERS)
    assert "Counterparty" not in rows[0].values
    rows, _ = build_rows(docs, props, DEFAULT_HEADERS, us=["Nobody Matches"])
    assert "Counterparty" not in rows[0].values


def test_explicit_counterparty_label_wins():
    docs, props = _doc(CONFIRM + ["Counterparty: SYN Explicit Co"])
    rows, _ = build_rows(docs, props, DEFAULT_HEADERS, us=["Synthetic Us"])
    assert rows[0].values["Counterparty"] == "SYN Explicit Co"


def test_multiple_candidates_are_reported():
    docs, props = _doc(CONFIRM + ["Grade: SYN Heavy"])
    rows, _ = build_rows(docs, props, DEFAULT_HEADERS)
    assert rows[0].values["Grade"] == "SYN Light" and rows[0].others["Grade"] == "SYN Heavy"


def test_documents_without_values_get_no_row():
    docs, props = _doc(["SYNTHETIC free text only"])
    assert build_rows(docs, props, DEFAULT_HEADERS)[0] == []


def test_headers_read_from_the_firms_file_including_trailing_space(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "raw-data-format"
    for i, h in enumerate(["Contract #", "Quantity ", "Mystery Column"], start=1):
        ws.cell(row=1, column=i, value=h)
    p = tmp_path / "raw-data-format.xlsx"
    wb.save(p)
    headers = read_format_headers(p)
    assert headers == ["Contract #", "Quantity ", "Mystery Column"]
    docs, props = _doc(CONFIRM)
    rows, unmapped = build_rows(docs, props, headers)
    assert rows[0].values == {"Contract #": "SYN-001", "Quantity ": "5,000"}
    assert unmapped == ["Mystery Column"]


def test_review_writes_raw_data_workbook_with_exact_headers(tmp_path):
    folder = tmp_path / "contracts" / "Physical Contracts"
    folder.mkdir(parents=True)
    (folder / "c.pdf").write_bytes(build_pdf([CONFIRM]))
    (folder / "w.xlsx").write_bytes(build_xlsx())
    fmt = Workbook()
    for i, h in enumerate(DEFAULT_HEADERS, start=1):
        fmt.active.cell(row=1, column=i, value=h)
    fmt.save(tmp_path / "raw-data-format.xlsx")
    r = build_review(tmp_path / "contracts", tmp_path / "out",
                     format_path=tmp_path / "raw-data-format.xlsx", us=["Synthetic Us"])
    wb = load_workbook(r.raw_data)
    assert wb.sheetnames == ["raw-data-format"]
    rows = list(wb.active.iter_rows(values_only=True))
    assert list(rows[0]) == DEFAULT_HEADERS
    assert ("SYN-001" in rows[1]) or ("SYN-001" in rows[2])
    review = load_workbook(r.workbook)
    ev = list(review["raw-data-evidence"].iter_rows(min_row=2, values_only=True))
    assert any(row[4] == "Grade" and row[6].startswith("p1:L6") for row in ev)


def test_cli_format_and_us_options(tmp_path, capsys):
    from riskparse.cli import main
    folder = tmp_path / "contracts" / "Physical Contracts"
    folder.mkdir(parents=True)
    (folder / "c.pdf").write_bytes(build_pdf([CONFIRM]))
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Physical Contracts/c.pdf", build_pdf([CONFIRM]))
    assert main(["review", str(z), "--out", str(tmp_path / "o"), "--us", "Synthetic Us"]) == 0
    out = capsys.readouterr().out
    assert "raw-data-format: 1 row(s)" in out and "Counterparty" in out
    assert "SYN-001" not in out                                # counts only


def test_format_path_variants_and_clear_errors(tmp_path, capsys):
    import pytest

    from riskparse.cli import main
    from riskparse.rawformat import FormatFileError, resolve_format_path
    wb = Workbook()
    wb.active["A1"] = "Contract #"
    folder = tmp_path / "raw-data-format"
    folder.mkdir()
    wb.save(folder / "raw-data-format.xlsx")
    assert resolve_format_path(folder).name == "raw-data-format.xlsx"            # folder
    assert resolve_format_path(folder / "raw-data-format").suffix == ".xlsx"    # no extension
    with pytest.raises(FormatFileError, match="not found"):
        resolve_format_path(tmp_path / "missing.xlsx")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    assert main(["review", str(contracts), "--format", str(tmp_path / "nope.xlsx")]) == 4
    assert "format file not found" in capsys.readouterr().err
    assert not any(p.name.startswith("contracts_review_") for p in tmp_path.iterdir())
    assert main(["review", str(contracts), "--format", str(folder),
                 "--us", "<our company name>"]) == 4
    assert "placeholder" in capsys.readouterr().err
