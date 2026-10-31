import csv

from openpyxl import load_workbook

from riskparse.inventory.scan import scan_folder, write_scan_csv
from riskparse.inventory.template import SHEETS, SHEETS_BY_NAME
from riskparse.inventory.workbook import (
    ERROR,
    MISSING,
    WARNING,
    validate_rows,
    validate_xlsx,
    write_csv_templates,
    write_xlsx_template,
)


def _blank_rows():
    return {s.name: [] for s in SHEETS}


def _row(sheet, **vals):
    r = {h: "" for h in SHEETS_BY_NAME[sheet].headers}
    r.update(vals)
    return r


def _coverage(*traders):
    rows = [_row("Source_Coverage", coverage_id="SRC-EGISTIX-01", source_type="EGISTIX",
                 trade_types_covered="x", source_location="Egistix", source_owner="o",
                 egistix_covers="PARTIAL", october_export_obtained="Y")]
    for i, t in enumerate(traders, 1):
        rows.append(_row("Source_Coverage", coverage_id=f"SRC-TRADER-0{i}",
                         source_type="TRADER_SHEET", trader_name=t, trade_types_covered="x",
                         source_location=r"\\srv\share", source_owner=t,
                         egistix_covers="UNKNOWN", october_export_obtained="Y"))
    return rows


def test_template_has_all_required_sheets(tmp_path):
    p = write_xlsx_template(tmp_path / "t.xlsx")
    wb = load_workbook(p)
    for s in SHEETS:
        assert s.name in wb.sheetnames
        assert [c.value for c in wb[s.name][1]][: len(s.headers)] == s.headers
    assert "README" in wb.sheetnames and "Lists" in wb.sheetnames


def test_template_covers_every_requested_source_type():
    names = set(SHEETS_BY_NAME)
    assert {"Contract_Documents", "Egistix_Exports", "Trader_Sheets",
            "Amendments_Cancellations", "Evergreen_Agreements", "Source_Coverage"} <= names
    # three trader rows seeded so no trader can be silently forgotten
    assert len(SHEETS_BY_NAME["Trader_Sheets"].seed_rows) == 3


def test_blank_template_is_structurally_valid_but_incomplete(tmp_path):
    issues = validate_xlsx(write_xlsx_template(tmp_path / "t.xlsx"))
    assert not [i for i in issues if i.severity == ERROR and i.row == 1]  # headers OK
    assert any(i.severity == MISSING for i in issues)
    assert any(i.severity == WARNING and "trader" in i.message for i in issues)


def test_template_generation_is_deterministic(tmp_path):
    a = write_xlsx_template(tmp_path / "a.xlsx")
    b = write_xlsx_template(tmp_path / "b.xlsx")
    wa, wb = load_workbook(a), load_workbook(b)
    assert wa.properties.created == wb.properties.created
    assert wa.sheetnames == wb.sheetnames


def test_csv_templates(tmp_path):
    paths = write_csv_templates(tmp_path)
    assert len(paths) == len(SHEETS)
    with paths[0].open() as fh:
        assert next(csv.reader(fh)) == SHEETS[0].headers


def test_header_mismatch_is_error():
    rows = _blank_rows()
    headers = {s.name: s.headers for s in SHEETS}
    headers["Trader_Sheets"] = ["sheet_id", "trader"]
    issues = validate_rows(rows, headers)
    assert any(i.severity == ERROR and i.sheet == "Trader_Sheets" for i in issues)


def test_invalid_values_detected():
    rows = _blank_rows()
    rows["Source_Coverage"] = _coverage("A", "B")
    rows["Contract_Documents"] = [_row(
        "Contract_Documents", doc_inventory_id="D1", file_name="f.pdf", unc_path=r"\\s\f.pdf",
        file_type="PDF", document_category="CONFIRMATION", physical_financial="PHYSICAL",
        evergreen_flag="Maybe", is_scanned="N", email_body_has_terms="NA",
        trade_date="14/10/2026", sha256="xyz", inventory_status="EXCEPTED",
        inventoried_by="a", inventoried_date="2026-11-02")]
    msgs = {(i.column, i.severity) for i in validate_rows(rows)}
    assert ("evergreen_flag", ERROR) in msgs
    assert ("trade_date", ERROR) in msgs
    assert ("sha256", ERROR) in msgs
    assert ("exception_reason", ERROR) in msgs


def test_duplicate_ids_detected():
    rows = _blank_rows()
    rows["Source_Coverage"] = _coverage("A", "B") + _coverage("C")[1:]
    issues = validate_rows(rows)
    assert any("duplicate ID" in i.message for i in issues)


def test_evergreen_approval_needs_two_distinct_approvers():
    rows = _blank_rows()
    rows["Source_Coverage"] = _coverage("A", "B")
    rows["Contract_Documents"] = [_row("Contract_Documents", doc_inventory_id="D1")]
    base = dict(agreement_id="E1", doc_inventory_id="D1", counterparty_raw_name="c",
                stated_value_raw_text='"Value: $0.15"', stated_value_location="p3 s4.2",
                active_for_2026_10="YES", interpretation_status="APPROVED",
                approval_date="2026-11-05")
    rows["Evergreen_Agreements"] = [_row("Evergreen_Agreements", risk_approver="R", **base)]
    assert any("both" in i.message for i in validate_rows(rows))
    rows["Evergreen_Agreements"] = [_row("Evergreen_Agreements", risk_approver="Pat",
                                         finance_approver="pat", **base)]
    assert any("different people" in i.message for i in validate_rows(rows))


def test_crosswalk_trader_must_be_in_coverage_and_verifier_independent():
    rows = _blank_rows()
    rows["Source_Coverage"] = _coverage("A", "B")
    rows["Trade_Crosswalk"] = [_row(
        "Trade_Crosswalk", crosswalk_id="X1", trader_name="Z", trade_date="2026-10-05",
        counterparty_raw_name="c", physical_financial="PHYSICAL", in_trader_sheet="Y",
        in_egistix="N", confirm_found="Y", preliminary_status="UNRESOLVED", keyed_by="k",
        verified_by="K")]
    msgs = [i.message for i in validate_rows(rows)]
    assert any("no TRADER_SHEET row" in m for m in msgs)
    assert any("independent verifier" in m for m in msgs)


def test_dangling_document_reference():
    rows = _blank_rows()
    rows["Source_Coverage"] = _coverage("A", "B")
    rows["Amendments_Cancellations"] = [_row("Amendments_Cancellations", event_id="A1",
                                             doc_inventory_id="NOPE")]
    assert any("not found in Contract_Documents" in i.message for i in validate_rows(rows))


def test_timestamp_requires_timezone():
    rows = _blank_rows()
    rows["Source_Coverage"] = _coverage("A", "B")
    good = _row("Egistix_Exports", export_id="E1", export_timestamp="2026-11-03 09:15 MST")
    bad = _row("Egistix_Exports", export_id="E2", export_timestamp="2026-11-03 09:15")
    rows["Egistix_Exports"] = [good, bad]
    ts_errors = [i for i in validate_rows(rows)
                 if i.column == "export_timestamp" and i.severity == ERROR]
    assert [i.row for i in ts_errors] == [3]


def test_scan_hashes_flags_duplicates_and_revisions(tmp_path):
    root = tmp_path / "Oct"
    (root / "Physical").mkdir(parents=True)
    (root / "Evergreen").mkdir()
    (root / "Physical" / "a.pdf").write_bytes(b"one")
    (root / "Evergreen" / "copy.pdf").write_bytes(b"one")
    (root / "Evergreen" / "a.pdf").write_bytes(b"two")
    (root / "Physical" / "~$lock.xlsx").write_bytes(b"lock")
    before = sorted((p.name, p.read_bytes()) for p in root.rglob("*") if p.is_file())

    rows, findings = scan_folder(root, inventoried_by="t", inventoried_date="2026-11-02")
    assert len(rows) == 3
    assert {r["folder_hint"] for r in rows} == {"Physical", "Evergreen"}
    kinds = {f.kind for f in findings}
    assert kinds == {"DUPLICATE_CONTENT", "SAME_NAME_DIFFERENT_CONTENT"}
    after = sorted((p.name, p.read_bytes()) for p in root.rglob("*") if p.is_file())
    assert before == after   # read-only

    out = write_scan_csv(rows, tmp_path / "out" / "scan.csv")
    with out.open(newline="", encoding="utf-8") as fh:
        rows_csv = list(csv.DictReader(fh))
    assert all(r["inventory_status"] == "LISTED" for r in rows_csv)
    # scan output validates against the template's Contract_Documents rules
    data = _blank_rows()
    data["Source_Coverage"] = _coverage("A", "B")
    data["Contract_Documents"] = rows_csv
    errs = [i for i in validate_rows(data) if i.severity == ERROR]
    assert errs == []
