"""SETUP.bat / RUN.bat: static checks (Windows is not available in CI)."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = ["SETUP.bat", "RUN.bat"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_crlf_and_plain_ascii(name):
    data = (REPO / name).read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b""), "every line must end in CRLF"
    assert all(b < 128 for b in data), "cmd.exe code pages mangle non-ASCII text"


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_goto_and_call_target_exists(name):
    text = (REPO / name).read_text()
    labels = {m.lower() for m in re.findall(r"^:(\w+)\s*$", text, re.M)}
    targets = {m.lower() for m in re.findall(r"\b(?:goto|call)\s+:(\w+)", text, re.I)}
    assert targets - {"eof"} <= labels, targets - labels
    every = [m.lower() for m in re.findall(r"^:(\w+)\s*$", text, re.M)]
    assert len(every) == len(set(every)), "a label defined twice: goto jumps to the first"


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_parenthesised_blocks(name):
    """Blocks expand %VAR% when parsed and break on ')' in text; the scripts use goto."""
    for line in (REPO / name).read_text().splitlines():
        if line.lstrip().lower().startswith(("rem", "echo")) or " in ('" in line \
                or " in (%" in line or " in (*)" in line or "powershell" in line:
            continue
        assert not re.search(r"\(\s*$|^\s*\)", line), line


def test_scripts_point_at_files_that_exist():
    setup = (REPO / "SETUP.bat").read_text()
    assert "requirements\\lock.txt" in setup and (REPO / "requirements" / "lock.txt").is_file()
    assert "github.com/ivanmueller/KM-RiskReporting.git" in setup
    run = (REPO / "RUN.bat").read_text()
    assert "riskparse.exe\" review \"%CONTRACTS%\" --results \"%RESULTS%\" %TS_ARG%" in run
    assert "riskparse.exe\" dashboard \"%RESULTS%\"" in run
    for name in ("contracts-folder.txt", "results-folder.txt", "tradesheet-folder.txt"):
        assert name in run and name in setup          # SETUP writes what RUN reads


def test_gitattributes_keeps_bat_bytes():
    assert "*.bat -text" in (REPO / ".gitattributes").read_text()
