"""Minimal deterministic reconciliation: exact alias matching only (game plan 7.2 step 1).

This is deliberately the smallest useful slice, built to prove the schema end to end
with synthetic data. Composite/fuzzy matching, tolerances, amendment chains and
policy-driven statuses are later-phase work. Every decision here is conservative:

* A trade with no executed-confirmation document is ``UNCONFIRMED``.
* A confirmed trade not present in any booking source is ``NOT_BOOKED`` (critical).
* A confirmed trade absent from Egistix is ``UNRESOLVED`` - only Risk may classify it
  ``EGISTIX_NOT_APPLICABLE``.
* Any material field difference, or a value that is UNRESOLVED/MISSING on one side
  and present on another (e.g. Egistix showing $0 for an evergreen), is a break.
* More than one source row from the same import matching one trade is
  ``DUPLICATE_POSSIBLE``.
* A source row matching no trade is ``UNCONFIRMED`` (deal exists, no confirmation).
* A booking row missing side, volume or price, or the unit of a compared value, cannot
  make a trade ``MATCHED``; the trade is ``UNRESOLVED`` with the missing fields listed.
* Different units are a break, never converted.
* A ``CANCELLED``/``TERMINATED`` trade is ``CANCELLED_OR_AMENDED`` (HIGH if still booked).
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, func, insert, select

from riskparse import audit
from riskparse.db.schema import (
    reconciliation_item_sources,
    reconciliation_items,
    reconciliation_runs,
    source_documents,
    source_imports,
    source_records,
    trade_aliases,
    trade_documents,
    trade_terms,
    trades,
)

RULES_VERSION = "exact-alias-v0.3"
_ALIAS_FOR_SOURCE = {"EGISTIX": "EGISTIX", "TRADER_SHEET": "TRADER"}
_BOOKING_SOURCES = ("EGISTIX", "TRADER_SHEET")
# A booking-source row must carry each of these, or the trade cannot be MATCHED: a
# reference-only row proves the deal exists, not that its economics agree (handoff S-1).
_MATERIAL = ("side", "volume_value", "price_value")
_COMPARED = ("side", "volume_value", "price_value", "trade_date", "delivery_start",
             "delivery_end")
# A value is only comparable together with its unit (Codex review #2): 1000 MWh is not
# 1000 GJ. Units are compared as written (trimmed, case-insensitive); there is no
# conversion until an approved, versioned unit map exists.
_UNIT_FOR = {"volume_value": "volume_unit", "price_value": "price_unit"}
_CLOSED_LIFECYCLE = ("CANCELLED", "TERMINATED")


def _norm(v):
    if isinstance(v, Decimal):
        return v.normalize()
    return v


def _unit(u):
    return " ".join(u.split()).casefold() if isinstance(u, str) and u.strip() else None


def run_exact_reconciliation(conn: Connection, *, reporting_month: str, cutoff: datetime,
                             actor: str, code_version: str) -> str:
    """Reconcile every trade against the source rows and documents known at ``cutoff``.

    Eligibility (Codex review #6): only imports whose source cutoff is at or before
    ``cutoff``, confirmations not received after ``cutoff`` (documents without a received
    time are counted), and trade terms valid from at or before ``cutoff``.

    Matching uses the trade aliases as they stand now. Links from earlier runs are not
    reused or written back (Codex review #7); each run's links are recorded only in
    ``reconciliation_item_sources``.
    """
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    now = datetime.now(UTC)
    run_id = f"REC-{uuid.uuid4().hex[:12].upper()}"
    conn.execute(insert(reconciliation_runs).values(
        recon_run_id=run_id, reporting_month=reporting_month, cutoff_utc=cutoff,
        started_at_utc=now, status="RUNNING", code_version=code_version,
        rules_version=RULES_VERSION, created_by=actor))

    alias_map = {(r.alias_system, r.alias_value): r.trade_uid
                 for r in conn.execute(select(trade_aliases))}
    all_records = conn.execute(
        select(source_records, source_imports.c.source_system,
               source_imports.c.source_cutoff_utc)
        .join(source_imports, source_imports.c.import_id == source_records.c.import_id)
        .where(source_imports.c.reporting_month == reporting_month,
               source_imports.c.status == "LOADED")
    ).mappings().all()
    records = [r for r in all_records if r["source_cutoff_utc"] <= cutoff]
    late_imports = sorted({r["import_id"] for r in all_records if r["source_cutoff_utc"] > cutoff})

    matched: dict[str, list] = defaultdict(list)
    unmatched = []
    for rec in records:
        uid = alias_map.get((_ALIAS_FOR_SOURCE.get(rec["source_system"], ""), rec["trade_ref"]))
        if uid:
            matched[uid].append(rec)
        else:
            unmatched.append(rec)

    eligible_terms = (select(trade_terms)
                      .where((trade_terms.c.valid_from_utc.is_(None))
                             | (trade_terms.c.valid_from_utc <= cutoff))
                      .subquery())
    latest = (select(eligible_terms.c.trade_uid,
                     func.max(eligible_terms.c.term_version).label("v"))
              .where(eligible_terms.c.leg == 1).group_by(eligible_terms.c.trade_uid).subquery())
    terms = {r["trade_uid"]: r for r in conn.execute(
        select(eligible_terms).join(latest, (latest.c.trade_uid == eligible_terms.c.trade_uid)
                                    & (latest.c.v == eligible_terms.c.term_version))
        .where(eligible_terms.c.leg == 1)).mappings()}
    multi_leg = {r.trade_uid for r in conn.execute(
        select(trade_terms.c.trade_uid).where(trade_terms.c.leg > 1).distinct())}
    confirms = defaultdict(list)
    late_confirms = defaultdict(list)
    for r in conn.execute(
            select(trade_documents.c.trade_uid, trade_documents.c.document_id,
                   source_documents.c.received_at_utc)
            .join(source_documents,
                  source_documents.c.document_id == trade_documents.c.document_id)
            .where(trade_documents.c.relationship.in_(("CONFIRMATION",
                                                       "EVERGREEN_AGREEMENT")))):
        if r.received_at_utc is not None and r.received_at_utc > cutoff:
            late_confirms[r.trade_uid].append(r.document_id)
        else:
            confirms[r.trade_uid].append(r.document_id)

    for t in conn.execute(select(trades).order_by(trades.c.trade_uid)).mappings():
        uid = t["trade_uid"]
        recs = matched.get(uid, [])
        term = terms.get(uid)
        diffs: dict[str, dict] = {}
        notes: list[str] = []
        by_import = defaultdict(int)
        for rec in recs:
            by_import[rec["import_id"]] += 1
        systems = {rec["source_system"] for rec in recs}
        if late_confirms[uid]:
            notes.append(f"confirmation(s) {late_confirms[uid]} received after the cutoff were "
                         "not counted")

        if t["lifecycle_status"] in _CLOSED_LIFECYCLE:
            status = "CANCELLED_OR_AMENDED"
            severity = "HIGH" if systems else "INFO"
            if systems:
                notes.append(f"trade is {t['lifecycle_status']} but still present in "
                             f"{sorted(systems)}")
        elif any(n > 1 for n in by_import.values()):
            status, severity = "DUPLICATE_POSSIBLE", "HIGH"
        elif not confirms[uid]:
            status, severity = "UNCONFIRMED", "HIGH"
        elif not systems & set(_BOOKING_SOURCES):
            status, severity = "NOT_BOOKED", "CRITICAL"
        else:
            if term is not None:
                diffs, not_compared = _compare(term, t, recs)
            else:
                diffs, not_compared = {"terms": {"problem": "no trade terms valid at the "
                                                            "cutoff"}}, {}
            if diffs:
                status, severity = "MATCHED_BREAK", "HIGH"
            elif not_compared:
                status, severity = "UNRESOLVED", "HIGH"
                for src, fields in sorted(not_compared.items()):
                    notes.append(f"{src} does not carry material field(s) {fields}; economics "
                                 "not compared, so the trade cannot be MATCHED")
            elif uid in multi_leg:
                status, severity = "UNRESOLVED", "HIGH"
                notes.append("multi-leg trade: legs after leg 1 are not reconciled by this "
                             "rule version")
            elif "EGISTIX" not in systems:
                status, severity = "UNRESOLVED", "MEDIUM"
                notes.append("absent from Egistix; Risk to classify (EGISTIX_NOT_APPLICABLE "
                             "or booking break)")
            elif term is not None and (term["price_status"] != "PRESENT"
                                       or term["volume_status"] != "PRESENT"):
                status, severity = "UNRESOLVED", "HIGH"
                notes.append("economic value unresolved in trade terms")
            else:
                status, severity = "MATCHED", "INFO"
        if diffs and term is not None and (term["price_status"] != "PRESENT"
                                           or term["volume_status"] != "PRESENT"):
            status, severity = "UNRESOLVED", "HIGH"
            notes.append("economic value unresolved in trade terms; sources disagree")
        payload = {"differences": diffs, "notes": notes,
                   "sources_present": sorted(systems)} if (diffs or notes or systems) else None
        item_id = _item(conn, run_id, uid, status, severity,
                        "EXACT_ALIAS" if recs else "NONE", "EXACT" if recs else "NONE",
                        payload, now)
        for rec in recs:
            conn.execute(insert(reconciliation_item_sources).values(
                item_id=item_id, source_record_pk=rec["source_record_pk"]))
        for doc in confirms[uid]:
            conn.execute(insert(reconciliation_item_sources).values(
                item_id=item_id, document_id=doc))

    for rec in unmatched:
        item_id = _item(conn, run_id, None, "UNCONFIRMED", "HIGH", "NONE", "NONE",
                        {"notes": [f"{rec['source_system']} record {rec['source_record_id']} "
                                   "matches no known trade / confirmation"]}, now)
        conn.execute(insert(reconciliation_item_sources).values(
            item_id=item_id, source_record_pk=rec["source_record_pk"]))

    conn.execute(reconciliation_runs.update().where(reconciliation_runs.c.recon_run_id == run_id)
                 .values(status="COMPLETED", completed_at_utc=datetime.now(UTC)))
    audit.record(conn, actor=actor, action="RECONCILIATION_RUN_COMPLETED",
                 entity_type="reconciliation_run", entity_id=run_id,
                 after={"rules_version": RULES_VERSION, "records": len(records),
                        "unmatched_records": len(unmatched),
                        "imports_after_cutoff_excluded": late_imports})
    return run_id


def _compare(term, trade, recs) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Return ``(differences, not_compared)``.

    ``not_compared`` maps ``SYSTEM:record_id`` to the material fields (or units) that
    could not be compared. A blank is never treated as agreement.
    """
    expected = {
        "side": term["side"],
        "volume_value": term["volume_value"] if term["volume_status"] == "PRESENT"
        else term["volume_status"],
        "price_value": term["price_value"] if term["price_status"] == "PRESENT"
        else term["price_status"],
        "trade_date": trade["trade_date"],
        "delivery_start": term["delivery_start"],
        "delivery_end": term["delivery_end"],
    }
    diffs: dict[str, dict] = {}
    not_compared: dict[str, list[str]] = {}
    for rec in recs:
        key = f"{rec['source_system']}:{rec['source_record_id']}"
        missing = [f for f in _MATERIAL if rec[f] is None]
        for value_field, unit_field in _UNIT_FOR.items():
            if rec[value_field] is None:
                continue
            got_u, exp_u = _unit(rec[unit_field]), _unit(term[unit_field])
            if got_u is None or exp_u is None:
                missing.append(unit_field)
            elif got_u != exp_u:
                diffs.setdefault(unit_field, {"terms": term[unit_field]})[key] = rec[unit_field]
        if missing and rec["source_system"] in _BOOKING_SOURCES:
            not_compared[key] = missing
        for f in _COMPARED:
            got = rec[f]
            if got is None:
                continue  # source does not carry the field; recorded above if material
            if _norm(got) != _norm(expected[f]):
                diffs.setdefault(f, {"terms": str(expected[f])})[
                    key] = str(got)
    return diffs, not_compared


def _item(conn, run_id, trade_uid, status, severity, method, quality, payload, now) -> int:
    res = conn.execute(insert(reconciliation_items).values(
        recon_run_id=run_id, trade_uid=trade_uid, recon_status=status, match_method=method,
        match_quality=quality, severity=severity, item_state="OPEN", opened_at_utc=now,
        differing_fields=json.dumps(payload, sort_keys=True, default=str) if payload else None))
    return res.inserted_primary_key[0]
