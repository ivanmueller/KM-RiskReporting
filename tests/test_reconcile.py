"""Reconciliation edge cases beyond the smoke demo."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import insert, select

from riskparse.db.schema import (
    reconciliation_items,
    source_documents,
    source_imports,
    source_records,
    trade_aliases,
    trade_documents,
    trade_terms,
    trades,
)
from riskparse.reconcile.exact import run_exact_reconciliation

NOW = datetime(2026, 10, 31, tzinfo=UTC)


def _setup(conn, egx_row: dict, ts_row: dict | None = None):
    for i, doc in enumerate(("DOC-C", "DOC-E", "DOC-T")):
        conn.execute(insert(source_documents).values(
            document_id=doc, sha256=str(i) * 64, size_bytes=1, file_type="CSV",
            original_filename=f"{doc}.csv", original_path=f"/x/{doc}.csv",
            source_channel="TEST", recorded_at_utc=NOW))
    conn.execute(insert(trades).values(trade_uid="T1", trade_date=date(2026, 10, 5),
                                       created_at_utc=NOW))
    conn.execute(insert(trade_documents).values(trade_uid="T1", document_id="DOC-C",
                                                relationship="CONFIRMATION", created_at_utc=NOW))
    conn.execute(insert(trade_terms).values(
        trade_uid="T1", side="BUY", price_type="FIXED", price_status="PRESENT",
        price_value=Decimal("2.5"), price_unit="CAD/GJ", volume_status="PRESENT",
        volume_value=Decimal("100"), volume_unit="GJ", created_at_utc=NOW))
    conn.execute(insert(trade_aliases).values(trade_uid="T1", alias_system="EGISTIX",
                                              alias_value="E1", created_at_utc=NOW))
    for imp, doc, system, row in (("I-E", "DOC-E", "EGISTIX", egx_row),
                                  ("I-T", "DOC-T", "TRADER_SHEET", ts_row)):
        if row is None:
            continue
        conn.execute(insert(source_imports).values(
            import_id=imp, source_system=system, source_owner="o", source_name="n",
            document_id=doc, reporting_month="2026-10", source_cutoff_utc=NOW,
            imported_at_utc=NOW, status="LOADED", parser_version="v"))
        conn.execute(insert(source_records).values(
            import_id=imp, source_record_id=row.get("trade_ref", "R"), raw_payload="{}",
            raw_payload_sha256="0" * 64, **row))
    if ts_row is not None:
        conn.execute(insert(trade_aliases).values(trade_uid="T1", alias_system="TRADER",
                                                  alias_value=ts_row["trade_ref"],
                                                  created_at_utc=NOW))


def _status(conn):
    run = run_exact_reconciliation(conn, reporting_month="2026-10", cutoff=NOW, actor="t",
                                   code_version="test")
    r = conn.execute(select(reconciliation_items).where(
        reconciliation_items.c.recon_run_id == run,
        reconciliation_items.c.trade_uid == "T1")).mappings().one()
    return r["recon_status"], json.loads(r["differing_fields"] or "{}")


FULL = dict(trade_ref="E1", side="BUY", volume_value=Decimal("100"), volume_unit="GJ",
            price_value=Decimal("2.5"), price_unit="CAD/GJ")


def test_full_agreement_matches(conn):
    _setup(conn, FULL)
    assert _status(conn)[0] == "MATCHED"


def test_s1_reference_only_row_is_not_matched(conn):
    _setup(conn, dict(trade_ref="E1"))
    status, payload = _status(conn)
    assert status == "UNRESOLVED"
    assert any("not compared" in n for n in payload["notes"])


def test_s1_missing_price_only_is_not_matched(conn):
    _setup(conn, dict(trade_ref="E1", side="BUY", volume_value=Decimal("100")))
    assert _status(conn)[0] == "UNRESOLVED"


def test_s1_one_sparse_source_blocks_match_even_if_other_is_full(conn):
    _setup(conn, FULL, ts_row=dict(trade_ref="TS1"))
    assert _status(conn)[0] == "UNRESOLVED"


def test_difference_still_wins_over_missing_fields(conn):
    _setup(conn, dict(trade_ref="E1", side="SELL"))
    status, payload = _status(conn)
    assert status == "MATCHED_BREAK" and "side" in payload["differences"]


# --- Codex review regressions -------------------------------------------------------

def test_codex2_different_unit_is_a_break_not_a_match(conn):
    _setup(conn, {**FULL, "volume_unit": "MWh"})
    status, payload = _status(conn)
    assert status == "MATCHED_BREAK" and "volume_unit" in payload["differences"]


def test_codex2_unit_case_and_spacing_are_not_breaks(conn):
    _setup(conn, {**FULL, "volume_unit": " gj ", "price_unit": "cad/gj"})
    assert _status(conn)[0] == "MATCHED"


def test_codex2_missing_unit_means_not_compared(conn):
    _setup(conn, {**FULL, "price_unit": None})
    status, payload = _status(conn)
    assert status == "UNRESOLVED" and any("price_unit" in n for n in payload["notes"])


def test_codex6_imports_after_cutoff_are_excluded(conn):
    from sqlalchemy import update

    from riskparse.db.schema import source_imports
    _setup(conn, FULL)
    conn.execute(update(source_imports).values(source_cutoff_utc=datetime(2026, 12, 1,
                                                                          tzinfo=UTC)))
    assert _status(conn)[0] == "NOT_BOOKED"          # the only booking is not yet known


def test_codex6_confirmation_received_after_cutoff_not_counted(conn):
    from sqlalchemy import update
    _setup(conn, FULL)
    conn.execute(update(source_documents).where(source_documents.c.document_id == "DOC-C")
                 .values(received_at_utc=datetime(2026, 12, 1, tzinfo=UTC)))
    status, payload = _status(conn)
    assert status == "UNCONFIRMED" and any("after the cutoff" in n for n in payload["notes"])


def test_codex6_terms_valid_only_after_cutoff_are_ignored(conn):
    _setup(conn, FULL)
    conn.execute(insert(trade_terms).values(
        trade_uid="T1", term_version=2, side="SELL", price_type="FIXED",
        price_status="PRESENT", price_value=Decimal("9"), price_unit="CAD/GJ",
        volume_status="PRESENT", volume_value=Decimal("100"), volume_unit="GJ",
        valid_from_utc=datetime(2026, 12, 1, tzinfo=UTC), created_at_utc=NOW))
    assert _status(conn)[0] == "MATCHED"             # version 1 is the one valid at cutoff


def test_codex7_reassigned_reference_follows_the_current_alias(conn):
    from sqlalchemy import update
    _setup(conn, FULL)
    first = _status(conn)[0]
    assert first == "MATCHED"
    conn.execute(insert(trades).values(trade_uid="T2", created_at_utc=NOW))
    conn.execute(update(trade_aliases).where(trade_aliases.c.alias_value == "E1")
                 .values(trade_uid="T2"))
    status, _ = _status(conn)
    assert status == "NOT_BOOKED"                    # T1 no longer owns Egistix E1
    linked = conn.execute(select(source_records.c.linked_trade_uid)).scalars().all()
    assert linked == [None]                          # runs never write links back


def test_cancelled_trade_still_booked_is_not_matched(conn):
    from sqlalchemy import update
    _setup(conn, FULL)
    conn.execute(update(trades).values(lifecycle_status="CANCELLED"))
    status, payload = _status(conn)
    assert status == "CANCELLED_OR_AMENDED" and "still present" in payload["notes"][0]


def test_multi_leg_trade_is_not_matched_on_leg_one_alone(conn):
    _setup(conn, FULL)
    conn.execute(insert(trade_terms).values(
        trade_uid="T1", leg=2, side="SELL", price_type="FIXED", price_status="PRESENT",
        price_value=Decimal("1"), volume_status="PRESENT", volume_value=Decimal("1"),
        created_at_utc=NOW))
    assert _status(conn)[0] == "UNRESOLVED"


def test_naive_cutoff_rejected(conn):
    import pytest
    _setup(conn, FULL)
    with pytest.raises(ValueError):
        run_exact_reconciliation(conn, reporting_month="2026-10",
                                 cutoff=datetime(2026, 10, 31), actor="t", code_version="t")
