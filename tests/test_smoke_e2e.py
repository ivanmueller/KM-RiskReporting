"""Synthetic end-to-end smoke test (game plan section 3 flow, minus parsing/publishing)."""

import json

import pytest
from sqlalchemy import create_engine, select

from riskparse.db.schema import (
    published_trade_rows,
    reconciliation_items,
    source_documents,
    source_records,
    trades,
)
from riskparse.demo.smoke import run_smoke


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    work = tmp_path_factory.mktemp("smoke") / "demo"
    return run_smoke(work)


def _engine(smoke):
    return create_engine(f"sqlite:///{smoke['workdir']}/demo.db")


def test_registration_outcomes(smoke):
    o = smoke["registration_outcomes"]
    assert o["dup"] == "DUPLICATE"
    assert o["rev"] == "REVISION"
    assert sum(v == "NEW" for v in o.values()) == 8
    assert smoke["documents_registered"] == 9


def test_all_trader_sheets_and_egistix_imported(smoke):
    assert {k: v["rows"] for k, v in smoke["imports"].items()} == {
        "egistix": 3, "trader_a": 3, "trader_b": 2}
    assert all(not v["unparsed_values"] for v in smoke["imports"].values())


def test_reconciliation_statuses(smoke):
    s = smoke["reconciliation_statuses"]
    assert s["SYN-T001"] == "MATCHED"
    assert s["SYN-T002"] == "MATCHED_BREAK"
    assert s["SYN-T003"] == "UNRESOLVED"     # absent from Egistix: not assumed invalid
    assert s["SYN-T004"] == "NOT_BOOKED"
    assert s["SYN-T005"] == "UNRESOLVED"     # evergreen: $0 not treated as a price
    unmatched = [v for k, v in s.items() if k.startswith("unmatched:")]
    assert unmatched == ["UNCONFIRMED"]      # trader-sheet row with no confirmation


def test_break_details_name_the_disagreeing_source(smoke):
    eng = _engine(smoke)
    with eng.connect() as c:
        row = c.execute(select(reconciliation_items.c.differing_fields).where(
            reconciliation_items.c.trade_uid == "SYN-T002")).scalar()
    eng.dispose()
    diffs = json.loads(row)["differences"]
    assert "price_value" in diffs
    assert any(k.startswith("EGISTIX:") for k in diffs["price_value"])


def test_candidate_not_published(smoke):
    assert smoke["candidate_status"] == "CANDIDATE"
    assert smoke["active_publication"] is None
    assert smoke["candidate_snapshot_rows"] == 5


def test_snapshot_signs_and_exclusions(smoke):
    eng = _engine(smoke)
    with eng.connect() as c:
        rows = {r.trade_uid: r for r in c.execute(select(published_trade_rows))}
    eng.dispose()
    assert rows["SYN-T001"].signed_quantity > 0           # BUY positive
    assert rows["SYN-T002"].signed_quantity < 0           # SELL negative
    assert rows["SYN-T005"].price_value is None
    assert rows["SYN-T005"].exposure_review_status == "EXCLUDED_UNRESOLVED"
    assert rows["SYN-T001"].exposure_review_status == "PENDING_RISK_REVIEW"


def test_everything_flagged_synthetic_and_raw_rows_preserved(smoke):
    eng = _engine(smoke)
    with eng.connect() as c:
        assert all(r.is_synthetic for r in c.execute(select(source_documents.c.is_synthetic)))
        assert all(r.is_synthetic for r in c.execute(select(trades.c.is_synthetic)))
        raw = [json.loads(r.raw_payload) for r in c.execute(select(source_records.c.raw_payload))]
    eng.dispose()
    assert {"Ref": "TA-002", "Date": "2024-03-01", "Cpty": "SYNTHETIC-CPTY-ECHO",
            "Side": "Buy", "Qty": "2000", "Px": "0"} in raw


def test_integrity_checks(smoke):
    assert smoke["audit_chain_ok"] is True
    assert smoke["audit_events"] >= 15
    assert smoke["archive_problems"] == []


def test_refuses_to_delete_non_demo_directory(tmp_path):
    (tmp_path / "important.txt").write_text("keep")
    with pytest.raises(RuntimeError, match="refusing"):
        run_smoke(tmp_path)
    assert (tmp_path / "important.txt").exists()
