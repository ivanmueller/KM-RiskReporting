"""Guard against committing confidential material (game plan 13.3)."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = {".pdf", ".msg", ".eml", ".tif", ".tiff", ".docx", ".doc", ".xls", ".xlsm",
             ".db", ".sqlite", ".pfx", ".pem", ".key", ".zip"}
TEMPLATE_DIR = "inventory/templates/"


def _tracked():
    if not shutil.which("git") or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def test_no_confidential_file_types_tracked():
    bad = [f for f in _tracked() if Path(f).suffix.lower() in FORBIDDEN]
    assert bad == []


def test_spreadsheets_only_in_template_dir():
    bad = [f for f in _tracked() if Path(f).suffix.lower() in {".xlsx", ".csv"}
           and not f.startswith(TEMPLATE_DIR)]
    assert bad == []


def test_no_local_settings_or_env_files_tracked():
    bad = [f for f in _tracked() if f.endswith(".local.toml") or Path(f).name.startswith(".env")
           or f == "config/settings.toml"]
    assert bad == []


def test_committed_template_matches_generator(tmp_path):
    from openpyxl import load_workbook

    from riskparse.inventory.template import SHEETS
    committed = REPO / TEMPLATE_DIR / "October_2026_Source_Inventory_TEMPLATE.xlsx"
    if not committed.exists():
        pytest.skip("template not generated yet")
    wb = load_workbook(committed)
    for s in SHEETS:
        assert [c.value for c in wb[s.name][1]][: len(s.headers)] == s.headers
