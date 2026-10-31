from pathlib import Path

from riskparse.docs_gen import render

REPO = Path(__file__).resolve().parents[1]


def test_data_dictionary_matches_schema():
    committed = (REPO / "docs" / "data_dictionary.md").read_text(encoding="utf-8")
    assert committed == render(), "run: riskparse gen-data-dictionary"
