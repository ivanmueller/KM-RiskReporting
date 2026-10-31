"""Synthetic end-to-end smoke demo.

Everything here is FAKE: counterparties are ``SYNTHETIC-CPTY-*``, files contain a
"SYNTHETIC TEST FIXTURE" banner and every SQL row is flagged ``is_synthetic``. It runs
only against the local SQLite test adapter in a throw-away work directory.

Flow (mirrors game plan section 3, minus parsing and publishing):

1. generate synthetic confirms, a duplicate copy, a same-name revision, a synthetic
   Egistix export and two synthetic trader sheets;
2. migrate a fresh database;
3. register every file (hash, content-addressed archive copy, duplicate/revision
   detection, audit events);
4. load hand-keyed synthetic trade identities/terms (stand-in for Week 1 hand-keying;
   no PDF parsing in Phase 1);
5. import the Egistix and trader-sheet CSVs into staging with raw rows preserved;
6. run exact-alias reconciliation;
7. build a CANDIDATE publication record with its source-inventory digest and snapshot
   rows - it is NOT approved or activated (the active pointer stays empty);
8. verify audit hash chain and archive integrity.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, func, insert, select

from riskparse import __version__, audit
from riskparse.config import settings_from_dict
from riskparse.db import migrate
from riskparse.db.connection import create_db_engine
from riskparse.db.schema import (
    active_publication,
    publication_runs,
    publication_sources,
    published_trade_rows,
    reconciliation_items,
    source_documents,
    trade_aliases,
    trade_documents,
    trade_terms,
    trades,
)
from riskparse.evidence.registry import register_document, verify_archive
from riskparse.imports import SourceMapping, import_csv_source
from riskparse.reconcile.exact import run_exact_reconciliation

ANALYST = "synthetic.analyst"
MONTH = "2026-10"
CUTOFF = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)   # 2026-11-02 00:00 MST
BANNER = "SYNTHETIC TEST FIXTURE - NOT A REAL CONTRACT - NO REAL COUNTERPARTY OR PRICE\n"

# Hand-keyed synthetic "golden" trades. uid, trader, cpty, side, vol, price, evergreen
_TRADES = [
    # MATCHED: confirm + Egistix + trader sheet all agree
    dict(uid="SYN-T001", trader="SYN-TRADER-A", cpty="SYNTHETIC-CPTY-ALPHA", side="BUY",
         vol="1000", price="2.5000", evergreen=False, confirm="SYN_confirm_T001.pdf",
         egx="EGX-9001", ts="TA-001", trade_date=date(2026, 10, 5)),
    # MATCHED_BREAK: Egistix price differs from confirmed price
    dict(uid="SYN-T002", trader="SYN-TRADER-B", cpty="SYNTHETIC-CPTY-BRAVO", side="SELL",
         vol="500", price="2.6500", evergreen=False, confirm="SYN_confirm_T002.pdf",
         egx="EGX-9002", ts="TB-001", trade_date=date(2026, 10, 9)),
    # UNRESOLVED: confirmed + trader sheet, absent from Egistix
    dict(uid="SYN-T003", trader="SYN-TRADER-B", cpty="SYNTHETIC-CPTY-CHARLIE", side="BUY",
         vol="250", price="2.4000", evergreen=False, confirm="SYN_confirm_T003.pdf",
         egx=None, ts="TB-002", trade_date=date(2026, 10, 14)),
    # NOT_BOOKED: confirmation exists, no booking anywhere
    dict(uid="SYN-T004", trader=None, cpty="SYNTHETIC-CPTY-DELTA", side="SELL",
         vol="300", price="2.7000", evergreen=False, confirm="SYN_confirm_T004.pdf",
         egx=None, ts=None, trade_date=date(2026, 10, 20)),
    # Evergreen: price meaning UNRESOLVED; Egistix and trader sheet both display 0
    dict(uid="SYN-T005", trader="SYN-TRADER-A", cpty="SYNTHETIC-CPTY-ECHO", side="BUY",
         vol="2000", price=None, evergreen=True, confirm="SYN_evergreen_T005.pdf",
         egx="EGX-9005", ts="TA-002", trade_date=date(2024, 3, 1)),
]

EGISTIX_MAPPING = SourceMapping(
    record_id_column="DealID",
    columns={"trade_ref": "DealID", "trade_date": "TradeDate", "counterparty_raw": "Counterparty",
             "side": "BuySell", "volume_value": "Volume", "volume_unit": "VolUnit",
             "price_value": "Price", "price_unit": "PriceUnit",
             "delivery_start": "Start", "delivery_end": "End"},
    parser_version="synthetic-egistix-csv-v0",
)
TRADER_MAPPING = SourceMapping(
    record_id_column="Ref",
    columns={"trade_ref": "Ref", "trade_date": "Date", "counterparty_raw": "Cpty",
             "side": "Side", "volume_value": "Qty", "price_value": "Px"},
    parser_version="synthetic-trader-csv-v0",
)


def _write_fixtures(src: Path) -> dict[str, Path]:
    phys = src / "01_Contracts" / "2026" / "2026-10 October" / "Physical"
    ever = src / "01_Contracts" / "2026" / "2026-10 October" / "Evergreen"
    recs = src / "02_Trade Records" / "2026" / "2026-10 October"
    for d in (phys, ever, recs / "Egistix", recs / "Trader Sheets", src / "00_Intake"):
        d.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for t in _TRADES:
        folder = ever if t["evergreen"] else phys
        p = folder / t["confirm"]
        price = t["price"] or "SEE CLAUSE 4 - STATED VALUE (meaning unresolved)"
        p.write_text(BANNER + f"trade={t['uid']} cpty={t['cpty']} side={t['side']} "
                     f"vol={t['vol']} GJ/day price={price}\n", encoding="utf-8")
        files[t["uid"]] = p
    # exact duplicate bytes saved again into intake under another name
    dup = src / "00_Intake" / "copy_of_SYN_confirm_T001.pdf"
    shutil.copyfile(files["SYN-T001"], dup)
    files["dup"] = dup
    # same filename as T002's confirm but different bytes (a revision) in intake
    rev = src / "00_Intake" / "SYN_confirm_T002.pdf"
    rev.write_text(BANNER + "trade=SYN-T002 REVISED COPY - pending review\n", encoding="utf-8")
    files["rev"] = rev

    egx = recs / "Egistix" / "SYN_Egistix_Export_2026-10.csv"
    with egx.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["DealID", "TradeDate", "Counterparty", "BuySell", "Volume", "VolUnit",
                    "Price", "PriceUnit", "Start", "End"])
        w.writerow(["EGX-9001", "2026-10-05", "SYNTHETIC-CPTY-ALPHA", "BUY", "1000", "GJ/d",
                    "2.5000", "CAD/GJ", "2026-11-01", "2026-11-30"])
        w.writerow(["EGX-9002", "2026-10-09", "SYNTHETIC-CPTY-BRAVO", "SELL", "500", "GJ/d",
                    "2.6000", "CAD/GJ", "2026-11-01", "2026-11-30"])
        w.writerow(["EGX-9005", "2024-03-01", "SYNTHETIC-CPTY-ECHO", "BUY", "2000", "GJ/d",
                    "0", "CAD/GJ", "2024-04-01", ""])
    files["egistix"] = egx

    ta = recs / "Trader Sheets" / "SYN_TraderA_2026-10.csv"
    with ta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["Ref", "Date", "Cpty", "Side", "Qty", "Px"])
        w.writerow(["TA-001", "2026-10-05", "SYNTHETIC-CPTY-ALPHA", "Buy", "1000", "2.50"])
        w.writerow(["TA-002", "2024-03-01", "SYNTHETIC-CPTY-ECHO", "Buy", "2000", "0"])
        w.writerow(["TA-003", "2026-10-27", "SYNTHETIC-CPTY-FOXTROT", "Sell", "150", "2.55"])
    files["trader_a"] = ta
    tb = recs / "Trader Sheets" / "SYN_TraderB_2026-10.csv"
    with tb.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["Ref", "Date", "Cpty", "Side", "Qty", "Px"])
        w.writerow(["TB-001", "2026-10-09", "SYNTHETIC-CPTY-BRAVO", "Sell", "500", "2.65"])
        w.writerow(["TB-002", "2026-10-14", "SYNTHETIC-CPTY-CHARLIE", "Buy", "250", "2.40"])
    files["trader_b"] = tb
    return files


def _load_trades(conn: Connection, doc_ids: dict[str, str]) -> None:
    now = datetime.now(UTC)
    for t in _TRADES:
        conn.execute(insert(trades).values(
            trade_uid=t["uid"], trader=t["trader"], counterparty_raw_name=t["cpty"],
            commodity="NATURAL_GAS", product="FIXED_PRICE_PHYSICAL", physical_financial="PHYSICAL",
            is_evergreen=t["evergreen"], lifecycle_status="ACTIVE", trade_date=t["trade_date"],
            is_synthetic=True, created_at_utc=now))
        if t["egx"]:
            conn.execute(insert(trade_aliases).values(trade_uid=t["uid"], alias_system="EGISTIX",
                                                      alias_value=t["egx"], created_at_utc=now))
        if t["ts"]:
            conn.execute(insert(trade_aliases).values(trade_uid=t["uid"], alias_system="TRADER",
                                                      alias_value=t["ts"], created_at_utc=now))
        conn.execute(insert(trade_documents).values(
            trade_uid=t["uid"], document_id=doc_ids[t["uid"]],
            relationship="EVERGREEN_AGREEMENT" if t["evergreen"] else "CONFIRMATION",
            created_at_utc=now))
        conn.execute(insert(trade_terms).values(
            trade_uid=t["uid"], leg=1, term_version=1, side=t["side"],
            price_type="UNRESOLVED" if t["price"] is None else "FIXED",
            price_status="UNRESOLVED" if t["price"] is None else "PRESENT",
            price_value=None if t["price"] is None else Decimal(t["price"]),
            price_raw=t["price"] or "SEE CLAUSE 4 - STATED VALUE", price_unit="CAD/GJ",
            currency="CAD", volume_status="PRESENT", volume_value=Decimal(t["vol"]),
            volume_raw=f"{t['vol']} GJ/day", volume_unit="GJ", volume_basis="PER_DAY",
            delivery_location="SYNTHETIC-HUB",
            delivery_start=date(2024, 4, 1) if t["evergreen"] else date(2026, 11, 1),
            delivery_end=None if t["evergreen"] else date(2026, 11, 30),
            evidence_document_id=doc_ids[t["uid"]], evidence_location="synthetic line 2",
            review_status="EXTRACTED", created_at_utc=now))
        audit.record(conn, actor=ANALYST, action="TRADE_HAND_KEYED", entity_type="trade",
                     entity_id=t["uid"], document_id=doc_ids[t["uid"]],
                     reason="synthetic hand-keyed golden record (Phase 1 demo)")


def _candidate_publication(conn: Connection, recon_run_id: str) -> str:
    """Create an unapproved CANDIDATE snapshot. Never approves or activates it."""
    pub_id = "PUB-SYN-0001"
    docs = conn.execute(select(source_documents.c.document_id, source_documents.c.sha256)
                        .order_by(source_documents.c.document_id)).all()
    digest = hashlib.sha256("\n".join(f"{d.document_id}:{d.sha256}" for d in docs)
                            .encode()).hexdigest()
    status_by_trade = {r.trade_uid: r.recon_status for r in conn.execute(
        select(reconciliation_items.c.trade_uid, reconciliation_items.c.recon_status)
        .where(reconciliation_items.c.recon_run_id == recon_run_id,
               reconciliation_items.c.trade_uid.is_not(None)))}
    conn.execute(insert(publication_runs).values(
        publication_id=pub_id, reporting_month=MONTH, reporting_cutoff_utc=CUTOFF,
        reporting_timezone="America/Edmonton", recon_run_id=recon_run_id,
        source_inventory_digest=digest, code_version=f"riskparse-{__version__}",
        config_version="synthetic-demo", status="CANDIDATE", created_by=ANALYST,
        created_at_utc=datetime.now(UTC),
        totals_json=json.dumps(dict(Counter(status_by_trade.values())), sort_keys=True)))
    for d in docs:
        conn.execute(insert(publication_sources).values(
            publication_id=pub_id, document_id=d.document_id, sha256=d.sha256))
    rows = conn.execute(select(trade_terms, trades.c.trader, trades.c.counterparty_raw_name,
                               trades.c.commodity, trades.c.product, trades.c.physical_financial,
                               trades.c.is_evergreen)
                        .join(trades, trades.c.trade_uid == trade_terms.c.trade_uid)).mappings()
    for r in rows:
        signed = None
        if r["volume_status"] == "PRESENT":
            signed = r["volume_value"] if r["side"] == "BUY" else -r["volume_value"]
        status = status_by_trade.get(r["trade_uid"], "UNRESOLVED")
        conn.execute(insert(published_trade_rows).values(
            publication_id=pub_id, trade_uid=r["trade_uid"], leg=r["leg"], segment=1,
            term_id=r["term_id"], trader=r["trader"],
            counterparty_raw_name=r["counterparty_raw_name"], commodity=r["commodity"],
            product=r["product"], physical_financial=r["physical_financial"],
            is_evergreen=r["is_evergreen"], side=r["side"], signed_quantity=signed,
            quantity_unit=r["volume_unit"], volume_basis=r["volume_basis"],
            price_status=r["price_status"], price_value=r["price_value"],
            price_unit=r["price_unit"], currency=r["currency"],
            delivery_location=r["delivery_location"], delivery_start=r["delivery_start"],
            delivery_end=r["delivery_end"], recon_status=status,
            exposure_review_status="EXCLUDED_UNRESOLVED" if r["price_status"] != "PRESENT"
            or status != "MATCHED" else "PENDING_RISK_REVIEW"))
    audit.record(conn, actor=ANALYST, action="CANDIDATE_CREATED", entity_type="publication",
                 entity_id=pub_id, publication_id=pub_id,
                 after={"source_inventory_digest": digest, "documents": len(docs)})
    return pub_id


def run_smoke(workdir: str | Path) -> dict:
    """Run the full synthetic demo in ``workdir`` (recreated). Returns a summary."""
    work = Path(workdir)
    if work.exists():
        marker = work / ".riskparse-synthetic-demo"
        if not marker.exists():
            raise RuntimeError(f"{work} exists and is not a riskparse demo directory; refusing "
                               "to delete it")
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / ".riskparse-synthetic-demo").write_text("synthetic demo workdir\n")

    settings = settings_from_dict({
        "environment": {"name": "test"},
        "database": {"backend": "sqlite", "sqlite_path": str(work / "demo.db")},
        "paths": {"archive_root": str(work / "archive")},
    })
    engine = create_db_engine(settings)
    migrate.upgrade(engine)
    files = _write_fixtures(work / "source")
    archive_root = settings.paths.archive_root

    outcomes: dict[str, str] = {}
    doc_ids: dict[str, str] = {}
    with engine.begin() as conn:
        for key, path in files.items():
            category = {"egistix": "EGISTIX_EXPORT", "trader_a": "TRADER_SHEET",
                        "trader_b": "TRADER_SHEET"}.get(key, "CONFIRMATION")
            if key == "SYN-T005":
                category = "EVERGREEN_AGREEMENT"
            res = register_document(conn, path, actor=ANALYST, source_channel="SYNTHETIC",
                                    category=category, archive_root=archive_root,
                                    is_synthetic=True)
            outcomes[key] = res.outcome
            doc_ids[key] = res.document_id
        _load_trades(conn, doc_ids)
        imports = {
            "egistix": import_csv_source(
                conn, document_id=doc_ids["egistix"], source_system="EGISTIX",
                source_owner="SYN-EGISTIX-OWNER", source_name="Synthetic deal export",
                reporting_month=MONTH, source_cutoff=CUTOFF, mapping=EGISTIX_MAPPING,
                actor=ANALYST),
            "trader_a": import_csv_source(
                conn, document_id=doc_ids["trader_a"], source_system="TRADER_SHEET",
                source_owner="SYN-TRADER-A", source_name="Trader A sheet",
                reporting_month=MONTH, source_cutoff=CUTOFF, mapping=TRADER_MAPPING,
                actor=ANALYST),
            "trader_b": import_csv_source(
                conn, document_id=doc_ids["trader_b"], source_system="TRADER_SHEET",
                source_owner="SYN-TRADER-B", source_name="Trader B sheet",
                reporting_month=MONTH, source_cutoff=CUTOFF, mapping=TRADER_MAPPING,
                actor=ANALYST),
        }
        recon_run = run_exact_reconciliation(conn, reporting_month=MONTH, cutoff=CUTOFF,
                                             actor=ANALYST,
                                             code_version=f"riskparse-{__version__}")
        pub_id = _candidate_publication(conn, recon_run)

    with engine.connect() as conn:
        statuses = {r.trade_uid or f"unmatched:{r.item_id}": r.recon_status
                    for r in conn.execute(select(reconciliation_items).where(
                        reconciliation_items.c.recon_run_id == recon_run))}
        chain = audit.verify_chain(conn)
        archive_problems = verify_archive(conn)
        active = conn.execute(select(active_publication.c.publication_id)).scalar()
        pub_status = conn.execute(select(publication_runs.c.status).where(
            publication_runs.c.publication_id == pub_id)).scalar()
        snapshot_rows = conn.execute(select(func.count()).select_from(published_trade_rows)
                                     .where(published_trade_rows.c.publication_id == pub_id)
                                     ).scalar()
        documents = conn.execute(select(func.count()).select_from(source_documents)).scalar()
    engine.dispose()

    return {
        "workdir": str(work),
        "registration_outcomes": outcomes,
        "documents_registered": documents,
        "imports": {k: {"import_id": v.import_id, "rows": v.row_count,
                        "unparsed_values": v.unparsed_values} for k, v in imports.items()},
        "reconciliation_run": recon_run,
        "reconciliation_statuses": statuses,
        "candidate_publication": pub_id,
        "candidate_status": pub_status,
        "candidate_snapshot_rows": snapshot_rows,
        "active_publication": active,
        "audit_chain_ok": chain.ok,
        "audit_events": chain.events_checked,
        "archive_problems": archive_problems,
    }
