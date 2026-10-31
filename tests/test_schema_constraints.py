"""Database-level controls that are portable across engines."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError, StatementError

from riskparse.db.schema import (
    active_publication,
    publication_runs,
    reconciliation_item_sources,
    reconciliation_items,
    reconciliation_runs,
    source_documents,
    trade_aliases,
    trade_terms,
    trades,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _doc(conn, doc_id="DOC-1", sha="a" * 64, **kw):
    vals = dict(document_id=doc_id, sha256=sha, size_bytes=1, file_type="PDF",
                original_filename="f.pdf", original_path="/x/f.pdf", source_channel="TEST",
                recorded_at_utc=NOW)
    vals.update(kw)
    conn.execute(insert(source_documents).values(**vals))


def _trade(conn, uid="T1"):
    conn.execute(insert(trades).values(trade_uid=uid, created_at_utc=NOW,
                                       trade_date=date(2026, 10, 1)))


def _term(conn, **kw):
    vals = dict(trade_uid="T1", side="BUY", price_type="FIXED", price_status="PRESENT",
                price_value=Decimal("2.5"), volume_status="PRESENT", volume_value=Decimal("10"),
                created_at_utc=NOW)
    vals.update(kw)
    conn.execute(insert(trade_terms).values(**vals))


def _pub(conn, **kw):
    vals = dict(publication_id="PUB0001", reporting_month="2026-10", reporting_cutoff_utc=NOW,
                reporting_timezone="America/Edmonton", source_inventory_digest="0" * 64,
                code_version="c", config_version="c", created_by="analyst",
                created_at_utc=NOW)
    vals.update(kw)
    conn.execute(insert(publication_runs).values(**vals))


def test_duplicate_sha_rejected(conn):
    _doc(conn)
    with pytest.raises(IntegrityError):
        _doc(conn, doc_id="DOC-2")


def test_bad_sha_shape_rejected(conn):
    with pytest.raises(IntegrityError):
        _doc(conn, sha="abc")


def test_bad_file_type_rejected(conn):
    with pytest.raises(IntegrityError):
        _doc(conn, file_type="EXE")


def test_naive_datetime_rejected(conn):
    with pytest.raises(StatementError):
        _doc(conn, recorded_at_utc=datetime(2026, 10, 31, 12, 0))


def test_foreign_keys_enforced(conn):
    with pytest.raises(IntegrityError):
        conn.execute(insert(trade_aliases).values(trade_uid="NOPE", alias_system="EGISTIX",
                                                  alias_value="E1", created_at_utc=NOW))


def test_alias_unique_per_system(conn):
    _trade(conn, "T1")
    _trade(conn, "T2")
    conn.execute(insert(trade_aliases).values(trade_uid="T1", alias_system="EGISTIX",
                                              alias_value="E1", created_at_utc=NOW))
    conn.execute(insert(trade_aliases).values(trade_uid="T2", alias_system="TRADER",
                                              alias_value="E1", created_at_utc=NOW))
    with pytest.raises(IntegrityError):
        conn.execute(insert(trade_aliases).values(trade_uid="T2", alias_system="EGISTIX",
                                                  alias_value="E1", created_at_utc=NOW))


def test_genuine_zero_price_is_allowed(conn):
    _trade(conn)
    _term(conn, price_value=Decimal("0"))


def test_unresolved_price_must_not_carry_a_value(conn):
    _trade(conn)
    with pytest.raises(IntegrityError):
        _term(conn, price_status="UNRESOLVED", price_value=Decimal("0"))


def test_present_price_requires_value(conn):
    _trade(conn)
    with pytest.raises(IntegrityError):
        _term(conn, price_value=None)


def test_invalid_side_rejected(conn):
    _trade(conn)
    with pytest.raises(IntegrityError):
        _term(conn, side="LONG")


def test_delivery_end_before_start_rejected(conn):
    _trade(conn)
    with pytest.raises(IntegrityError):
        _term(conn, delivery_start=date(2026, 11, 30), delivery_end=date(2026, 11, 1))


def test_term_version_unique(conn):
    _trade(conn)
    _term(conn)
    with pytest.raises(IntegrityError):
        _term(conn)


def test_publication_creator_cannot_approve(conn):
    with pytest.raises(IntegrityError):
        _pub(conn, approved_by="analyst", approved_at_utc=NOW)


def test_approved_publication_requires_approver(conn):
    with pytest.raises(IntegrityError):
        _pub(conn, status="APPROVED")


def test_published_requires_time(conn):
    with pytest.raises(IntegrityError):
        _pub(conn, status="PUBLISHED", approved_by="manager", approved_at_utc=NOW)


def test_only_one_active_pointer_slot(conn):
    with pytest.raises(IntegrityError):
        conn.execute(insert(active_publication).values(slot="SECOND"))


def test_active_pointer_must_reference_existing_publication(conn):
    with pytest.raises(IntegrityError):
        conn.execute(update(active_publication).values(publication_id="PUB9999"))


def _recon_run(conn):
    conn.execute(insert(reconciliation_runs).values(
        recon_run_id="R1", reporting_month="2026-10", cutoff_utc=NOW, started_at_utc=NOW,
        code_version="c", rules_version="r", created_by="a"))


def test_resolved_recon_item_requires_reviewer(conn):
    _recon_run(conn)
    with pytest.raises(IntegrityError):
        conn.execute(insert(reconciliation_items).values(
            recon_run_id="R1", recon_status="MATCHED_BREAK", match_method="EXACT_ALIAS",
            match_quality="EXACT", item_state="RESOLVED", opened_at_utc=NOW))


def test_invalid_recon_status_rejected(conn):
    _recon_run(conn)
    with pytest.raises(IntegrityError):
        conn.execute(insert(reconciliation_items).values(
            recon_run_id="R1", recon_status="PROBABLY_FINE", match_method="NONE",
            match_quality="NONE", opened_at_utc=NOW))


def test_recon_link_needs_exactly_one_source(conn):
    _recon_run(conn)
    res = conn.execute(insert(reconciliation_items).values(
        recon_run_id="R1", recon_status="UNRESOLVED", match_method="NONE", match_quality="NONE",
        opened_at_utc=NOW))
    with pytest.raises(IntegrityError):
        conn.execute(insert(reconciliation_item_sources).values(
            item_id=res.inserted_primary_key[0]))


def test_bad_reporting_month_rejected(conn):
    with pytest.raises(IntegrityError):
        _pub(conn, reporting_month="Oct-26")


# --- Regression tests: D-4 (case-insensitive separation of duties) ------------------

@pytest.mark.parametrize("approver", ["Analyst", "ANALYST", "analyst"])
def test_d4_case_variant_self_approval_rejected_by_db(conn, approver):
    with pytest.raises(IntegrityError):
        _pub(conn, approved_by=approver, approved_at_utc=NOW)


def test_d4_audit_case_variant_rejected_by_db(conn):
    from sqlalchemy import text
    with pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO audit_events (occurred_at_utc, actor, approver, action, entity_type, "
            "entity_id, event_hash) VALUES ('2026-10-31 00:00:00', 'Pat', 'pat', 'X', 'x', "
            "'1', :h)"), {"h": "f" * 64})


def test_zip_file_type_allowed(conn):
    _doc(conn, file_type="ZIP")
