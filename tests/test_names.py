"""Counterparty master-name map (synthetic names only)."""

import zipfile

import pytest
from openpyxl import load_workbook

from confirm_fixtures import calrock_msg, ox_pdf
from riskparse.cli import main
from riskparse.confirms import Trade
from riskparse.names import NameMapError, apply_name_map, load_name_map, norm
from riskparse.review import build_review, format_summary

HEADER = "name_as_written,master_name,notes\n"


def _map(tmp_path, body, name="map.csv"):
    p = tmp_path / name
    p.write_text(HEADER + body, encoding="utf-8")
    return p


def _trade(**kw):
    t = Trade(family="x", source_doc="D1", source_file="f")
    for k, v in kw.items():
        setattr(t, k, v)
        t.evidence[k] = f"p1: {v}"
    return t


def test_norm_ignores_case_punctuation_and_ampersand():
    assert norm("SYN Trading & Supply ULC.") == norm("syn trading and supply, ulc")
    assert norm("SYN Inc") == norm("SYN  Inc.")
    assert norm("SYN Oil") != norm("SYN Oils")


def test_exact_then_loose_lookup(tmp_path):
    nmap = load_name_map(_map(tmp_path, "SYN Energy Inc,SYN Energy Inc.,\n"
                                        "SYN Trading and Supply ULC,SYN Trading & Supply ULC,\n"))
    assert nmap.lookup("SYN Energy Inc") == ("SYN Energy Inc.", "exact")
    assert nmap.lookup("syn energy inc.") == ("SYN Energy Inc.", "ignoring case/punctuation")
    assert nmap.lookup("SYN Trading & Supply ULC")[0] == "SYN Trading & Supply ULC"
    assert nmap.lookup("SYN Other Ltd") == (None, "")


def test_apply_replaces_records_original_and_flags_unmapped(tmp_path):
    nmap = load_name_map(_map(tmp_path, "SYN Energy Inc,SYN Energy Inc.,\n"))
    mapped = _trade(counterparty="SYN Energy Inc", seller="SYN Energy Inc")
    unmapped = _trade(counterparty="SYN Unknown Ltd", seller="SYN Unknown Ltd")
    apply_name_map([mapped, unmapped], nmap)
    assert (mapped.counterparty, mapped.seller) == ("SYN Energy Inc.", "SYN Energy Inc.")
    assert mapped.names_as_written["counterparty"] == "SYN Energy Inc"
    assert "master name 'SYN Energy Inc.' (exact)" in mapped.evidence["counterparty"]
    assert mapped.flags == []
    assert unmapped.counterparty == "SYN Unknown Ltd"
    assert len(unmapped.flags) == 1 and "not in the counterparty map" in unmapped.flags[0]


def test_bad_maps_are_refused(tmp_path):
    with pytest.raises(NameMapError, match="maps to both"):
        load_name_map(_map(tmp_path, "SYN A,SYN A Inc.,\nsyn a.,SYN A Ltd.,\n"))
    with pytest.raises(NameMapError, match="both name_as_written and master_name"):
        load_name_map(_map(tmp_path, "SYN A,,\n", "m2.csv"))
    bad = tmp_path / "m3.csv"
    bad.write_text("name,master\nSYN A,SYN A Inc.\n", encoding="utf-8")
    with pytest.raises(NameMapError, match="needs the columns"):
        load_name_map(bad)
    with pytest.raises(NameMapError, match="not found"):
        load_name_map(tmp_path / "missing.csv")


def test_excel_utf8_bom_and_blank_rows_are_accepted(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(("﻿" + HEADER + "SYN A,SYN A Inc.,from Excel\n,,\n").encode("utf-8"))
    assert load_name_map(p).lookup("SYN A")[0] == "SYN A Inc."


def _zip(tmp_path):
    z = tmp_path / "c.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("c/Seller Confirmation for Deal 2099001.pdf", ox_pdf())
        zf.writestr("c/CalRock Energy trade confirmation #82999001.msg", calrock_msg())
    return z


def test_review_applies_map_and_lists_spellings(tmp_path):
    m = _map(tmp_path, "SYN Resources Inc.,SYN Resources Master,\n")
    r = build_review(_zip(tmp_path), tmp_path / "out", names_path=m)
    by_cp = {t.counterparty for t in r.trades}
    assert "SYN Resources Master" in by_cp
    assert "SYN Energy Marketing Inc." in by_cp            # unmapped: kept as written
    wb = load_workbook(r.workbook)
    names = {row[0]: row for row in wb["names"].iter_rows(min_row=2, values_only=True)}
    assert names["SYN Resources Inc."][1:3] == ("SYN Resources Master", "exact")
    assert names["SYN Energy Marketing Inc."][2] == "NOT IN MAP"
    assert "not in the map" in format_summary(r)


def test_no_names_keeps_spellings(tmp_path):
    m = _map(tmp_path, "SYN Resources Inc.,SYN Resources Master,\n")
    r = build_review(_zip(tmp_path), tmp_path / "out", names_path=m, use_names=False)
    assert "SYN Resources Master" not in {t.counterparty for t in r.trades}
    assert "Counterparty map: not loaded" in format_summary(r)


def test_cli_names_options(tmp_path):
    z = _zip(tmp_path)
    m = _map(tmp_path, "SYN Resources Inc.,SYN Resources Master,\n")
    assert main(["review", str(z), "--out", str(tmp_path / "o1"), "--names", str(m)]) == 0
    assert main(["review", str(z), "--out", str(tmp_path / "o2"), "--no-names"]) == 0
    assert main(["review", str(z), "--names", str(tmp_path / "missing.csv")]) == 4


def test_committed_map_loads():
    from pathlib import Path

    from riskparse.names import DEFAULT_MAP
    nmap = load_name_map(Path(__file__).resolve().parents[1] / DEFAULT_MAP)
    assert nmap.lookup("Kingston Marketing Limited")[0]
