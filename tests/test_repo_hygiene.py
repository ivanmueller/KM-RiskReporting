"""Guard against committing confidential material (game plan 13.3; Codex review)."""

import csv
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = {".pdf", ".msg", ".eml", ".tif", ".tiff", ".docx", ".doc", ".xls", ".xlsm",
             ".xlsx", ".csv", ".zip", ".7z", ".rar", ".rtf", ".db", ".sqlite", ".sqlite3",
             ".pfx", ".p12", ".pem", ".key"}
TEMPLATE_DIR = "inventory/templates/"
# The counterparty name map is reference data the business keeps in the repo on purpose.
NAME_MAP = "config/counterparty_map.csv"
# The firm's raw-data format: one row of column headers, nothing else.
FORMAT_FILE = "config/raw-data-format.xlsx"


def _tracked():
    if not shutil.which("git") or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def _expected_template_names():
    from riskparse.inventory.template import SHEETS
    return {f"{TEMPLATE_DIR}October_2026_Source_Inventory_TEMPLATE.xlsx"} | {
        f"{TEMPLATE_DIR}inventory_{s.name}.csv" for s in SHEETS}


def test_no_confidential_file_types_tracked():
    """Every extension counts, case-insensitively: contract.PDF and contract.pdf.txt fail."""
    allowed = _expected_template_names() | {NAME_MAP, FORMAT_FILE}
    bad = [f for f in _tracked() if f not in allowed
           and {s.lower() for s in Path(f).suffixes} & FORBIDDEN]
    assert bad == []


def test_template_dir_holds_exactly_the_generated_templates():
    in_dir = {f for f in _tracked() if f.startswith(TEMPLATE_DIR)}
    assert in_dir == _expected_template_names()


def test_no_local_settings_or_env_files_tracked():
    bad = [f for f in _tracked() if f.endswith(".local.toml") or Path(f).name.startswith(".env")
           or f == "config/settings.toml"]
    assert bad == []


def test_committed_csv_templates_equal_generator_output(tmp_path):
    from riskparse.inventory.workbook import write_csv_templates
    for generated in write_csv_templates(tmp_path):
        committed = REPO / TEMPLATE_DIR / generated.name
        assert committed.read_bytes() == generated.read_bytes(), generated.name


def test_committed_xlsx_template_equals_generator_output(tmp_path):
    """Every cell and every data validation, not just the headers (Codex review C9)."""
    from openpyxl import load_workbook

    from riskparse.inventory.workbook import write_xlsx_template
    committed = REPO / TEMPLATE_DIR / "October_2026_Source_Inventory_TEMPLATE.xlsx"
    fresh = write_xlsx_template(tmp_path / "t.xlsx")
    a, b = load_workbook(committed), load_workbook(fresh)
    assert a.sheetnames == b.sheetnames
    for name in a.sheetnames:
        wa, wb = a[name], b[name]
        cells_a = {(c.coordinate, c.value) for row in wa.iter_rows() for c in row
                   if c.value is not None}
        cells_b = {(c.coordinate, c.value) for row in wb.iter_rows() for c in row
                   if c.value is not None}
        assert cells_a == cells_b, name
        dv_a = sorted((str(d.sqref), d.formula1) for d in wa.data_validations.dataValidation)
        dv_b = sorted((str(d.sqref), d.formula1) for d in wb.data_validations.dataValidation)
        assert dv_a == dv_b, name


def test_csv_templates_contain_no_data_beyond_seed_rows():
    from riskparse.inventory.template import SHEETS
    for s in SHEETS:
        with (REPO / TEMPLATE_DIR / f"inventory_{s.name}.csv").open(newline="",
                                                                     encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == len(s.seed_rows), s.name


def test_name_map_holds_only_names_and_notes():
    """The one tracked CSV outside the templates: names only, no trade data."""
    with (REPO / NAME_MAP).open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["name_as_written", "master_name", "notes"]
    assert all(len(r) <= 3 for r in rows)


def test_format_file_holds_only_the_header_row():
    from openpyxl import load_workbook

    from riskparse.rawformat import DEFAULT_HEADERS
    wb = load_workbook(REPO / FORMAT_FILE)
    assert len(wb.worksheets) == 1
    rows = list(wb.active.iter_rows(values_only=True))
    assert [list(r) for r in rows] == [DEFAULT_HEADERS]     # same as the built-in columns
