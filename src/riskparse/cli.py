"""Command-line interface (Phase 1 commands only).

Commands that would publish, roll back, export reports or schedule runs do not exist
yet by design (game plan 9.6 lists them as a proposed interface).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date
from pathlib import Path

from riskparse import __version__


def _settings(args):
    from riskparse.config import load_settings
    return load_settings(args.config)


def cmd_config_check(args) -> int:
    from riskparse.db.connection import describe_url
    s = _settings(args)
    print(json.dumps(s.redacted(), indent=2))
    print("url:", describe_url(s))
    if s.database.backend == "sqlite" and s.environment == "test":
        print("NOTE: sqlite with environment 'test' is for synthetic data only.")
    elif s.database.backend == "sqlite":
        print("NOTE: local SQLite is a provisional prototype on this controlled machine, not the "
              "system of record. Keep the database file outside the repository.")
    return 0


def cmd_db_upgrade(args) -> int:
    from riskparse.db import migrate
    from riskparse.db.connection import create_db_engine
    engine = create_db_engine(_settings(args))
    try:
        migrate.upgrade(engine, args.revision)
        print("database at revision:", migrate.current_revision(engine))
    finally:
        engine.dispose()
    return 0


def cmd_db_current(args) -> int:
    from riskparse.db import migrate
    from riskparse.db.connection import create_db_engine
    engine = create_db_engine(_settings(args))
    try:
        print("current:", migrate.current_revision(engine), "| head:", migrate.head_revision())
    finally:
        engine.dispose()
    return 0


def cmd_register(args) -> int:
    from riskparse.db.connection import create_db_engine
    from riskparse.evidence.registry import register_document
    s = _settings(args)
    engine = create_db_engine(s)
    actor = args.actor or getpass.getuser()
    with engine.begin() as conn:
        for p in args.paths:
            r = register_document(conn, p, actor=actor, source_channel=args.channel,
                                  category=args.category,
                                  archive_root=None if args.no_archive else s.paths.archive_root)
            print(f"{r.outcome:<9} {r.document_id} {r.sha256} {p}"
                  + (f" (revision of {r.revision_of_document_id})"
                     if r.revision_of_document_id else ""))
    engine.dispose()
    return 0


def cmd_audit_verify(args) -> int:
    from sqlalchemy import text

    from riskparse import audit
    from riskparse.db.connection import create_db_engine
    from riskparse.evidence.registry import verify_archive
    engine = create_db_engine(_settings(args))
    with engine.connect() as conn:
        chain = audit.verify_chain(conn, expect_count=args.expect_count,
                                   expect_head=args.expect_head)
        problems = verify_archive(conn)
        count, head = audit.chain_head(conn)
        pointer_rows = conn.execute(text("SELECT COUNT(*) FROM active_publication")).scalar()
    engine.dispose()
    if pointer_rows != 1:
        problems.append(("active_publication", f"expected exactly 1 pointer row, found "
                                               f"{pointer_rows}"))
    print(f"anchor: --expect-count {count} --expect-head {head}  (record this outside the DB)")
    print(f"audit chain ok={chain.ok} events={chain.events_checked}"
          + (f" first_bad={chain.first_bad_event_id} ({chain.problem})" if not chain.ok else ""))
    for doc, problem in problems:
        print(f"ARCHIVE PROBLEM {doc}: {problem}")
    return 0 if chain.ok and not problems else 1


def cmd_inventory_template(args) -> int:
    from riskparse.inventory.workbook import write_csv_templates, write_xlsx_template
    out = Path(args.out)
    x = write_xlsx_template(out / "October_2026_Source_Inventory_TEMPLATE.xlsx")
    print("wrote", x)
    if args.csv:
        for p in write_csv_templates(out):
            print("wrote", p)
    return 0


def cmd_inventory_validate(args) -> int:
    from riskparse.inventory.workbook import ERROR, MISSING, validate_xlsx
    if Path(args.path).suffix.lower() not in (".xlsx", ".xlsm"):
        print("inventory-validate checks the completed .xlsx workbook; paste scan/manifest CSV "
              "rows into the workbook first", file=sys.stderr)
        return 1
    issues = validate_xlsx(args.path)
    for i in issues:
        print(i)
    errors = sum(i.severity == ERROR for i in issues)
    missing = sum(i.severity == MISSING for i in issues)
    print(f"\n{errors} error(s), {missing} missing required value(s), "
          f"{len(issues) - errors - missing} warning(s)")
    if errors:
        return 1
    if missing and args.strict:
        return 2
    return 0


def cmd_inventory_scan(args) -> int:
    from riskparse.fswalk import check_output_location
    from riskparse.inventory.scan import scan_folder, write_scan_csv
    check_output_location(args.out, args.root)        # refuse before reading anything
    rows, findings = scan_folder(args.root, inventoried_by=args.by or getpass.getuser(),
                                 inventoried_date=date.today().isoformat())
    out = write_scan_csv(rows, args.out, scanned_root=args.root)
    print(f"{len(rows)} file(s) hashed -> {out}")
    for f in findings:
        print(f"{f.kind}: {f.detail}")
    return 0


def cmd_intake_zip(args) -> int:
    from datetime import datetime

    from riskparse.evidence.intake_zip import (
        ZipIntakeError,
        manifest_rows,
        register_intake,
        safe_extract,
        write_manifest,
    )
    from riskparse.fswalk import check_output_location
    # Validate every argument BEFORE extracting anything (Codex review #23).
    check_output_location(args.manifest, args.extract_to)
    received = None
    if args.received_at:
        try:
            received = datetime.fromisoformat(args.received_at)
        except ValueError as exc:
            raise ZipIntakeError(f"--received-at is not an ISO timestamp: {exc}") from exc
        if received.tzinfo is None or received.utcoffset() is None:
            raise ZipIntakeError("--received-at must include a UTC offset, e.g. "
                                 "2026-11-02T09:15-07:00")
    if args.register:
        _settings(args)      # configuration must be valid before extraction starts
    result = safe_extract(args.zip, args.extract_to)
    print(f"ZIP {result.zip_path} sha256={result.zip_sha256} size={result.zip_size_bytes}")
    print(f"extracted {len(result.members)} member(s) to {result.extract_dir}; "
          f"refused {len(result.skipped)}")
    for f in result.findings:
        print(f"{f.kind}: {f.member} - {f.detail}")
    rows = manifest_rows(result, inventoried_by=args.by or getpass.getuser(),
                         inventoried_date=date.today().isoformat())
    print("manifest:", write_manifest(rows, args.manifest))
    if args.register:
        from riskparse.db.connection import create_db_engine
        s = _settings(args)
        engine = create_db_engine(s)
        try:
            with engine.begin() as conn:
                zip_reg, regs = register_intake(
                    conn, result, actor=args.by or getpass.getuser(),
                    source_channel=args.channel, archive_root=s.paths.archive_root,
                    sender=args.sender, received_at=received)
        finally:
            engine.dispose()
        print(f"registered ZIP as {zip_reg.document_id} ({zip_reg.outcome})")
        for r in regs:
            print(f"  {r.outcome:<9} {r.document_id} {r.sha256[:12]}")
    return 0


def cmd_gen_data_dictionary(args) -> int:
    from riskparse.docs_gen import render
    Path(args.out).write_text(render(), encoding="utf-8")
    print("wrote", args.out)
    return 0


def cmd_profile_documents(args) -> int:
    from riskparse.extract.profile import format_summary, profile_folder, write_profile
    from riskparse.fswalk import check_output_location
    check_output_location(args.out, args.root)
    result = profile_folder(args.root)
    out = write_profile(result, args.out, scanned_root=args.root)
    print(format_summary(result))
    print("profile:", out)
    return 0


def cmd_extract_preview(args) -> int:
    from riskparse.evidence.hashing import fingerprint
    from riskparse.extract.dispatch import extract_bytes
    fp = fingerprint(args.path)
    data = Path(args.path).read_bytes()
    r = extract_bytes(data, fp.file_type, filename=fp.filename)
    print(f"{fp.filename}  type={fp.file_type}  sha256={fp.sha256}")
    print(f"status={r.status}  extractor={r.extractor} v{r.extractor_version}  "
          f"segments={len(r.segments)}")
    for k, v in r.metadata.items():
        print(f"  {k}: {v}")
    for w in r.warnings:
        print(f"WARNING: {w}")
    print("-- segments (raw text, not interpreted) --")
    for s in r.segments[: args.limit]:
        print(f"{s.location:<24} {s.text}")
    if len(r.segments) > args.limit:
        print(f"... {len(r.segments) - args.limit} more (use --limit)")
    return 0


def cmd_extract(args) -> int:
    from riskparse.db.connection import create_db_engine
    from riskparse.extract.store import extract_all_pending, extract_document
    s = _settings(args)
    engine = create_db_engine(s)
    actor = args.actor or getpass.getuser()
    try:
        with engine.begin() as conn:
            if args.all:
                summaries = extract_all_pending(conn, actor=actor, work_root=s.paths.work_root,
                                                archive_root=s.paths.archive_root)
            else:
                summaries = [extract_document(conn, d, actor=actor, work_root=s.paths.work_root,
                                              archive_root=s.paths.archive_root)
                             for d in args.document_id]
    finally:
        engine.dispose()
    for sm in summaries:
        flag = " (already extracted)" if sm.already_done else ""
        print(f"{sm.status:<15} {sm.document_id} segments={sm.segments}{flag}")
        for w in sm.warnings:
            print(f"    WARNING: {w}")
        for name, doc_id, outcome in sm.attachments_registered:
            print(f"    attachment {name!r} -> {doc_id} ({outcome})")
    if not summaries:
        print("nothing to extract")
    return 0


def cmd_review(args) -> int:
    from riskparse.rawformat import read_format_headers
    from riskparse.review import build_review, format_summary
    if args.format:
        read_format_headers(args.format)        # fail fast, before anything is written
    if any("<" in u or ">" in u for u in args.us):
        print("--us still contains the placeholder text; replace it with our company's "
              "name as it appears on confirmations", file=sys.stderr)
        return 4
    result = build_review(args.source, args.out, format_path=args.format, us=args.us or None)
    print(format_summary(result))
    print(f"\nraw-data (your format + checks): {result.raw_data}")
    print(f"review workbook (evidence, text, documents): {result.workbook}")
    print("The summary above is counts only. The workbooks contain contract details.")
    return 0


def cmd_demo_smoke(args) -> int:
    from riskparse.demo import smoke
    summary = smoke.run_smoke(args.workdir)
    print(json.dumps(summary, indent=2, default=str))
    problems = smoke.check_summary(summary)
    for p in problems:
        print(f"MISMATCH: {p}")
    print("\nSMOKE DEMO:", "FAIL" if problems else "PASS")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="riskparse", description=__doc__)
    p.add_argument("--version", action="version", version=f"riskparse {__version__}")
    p.add_argument("--config", help="path to settings TOML (else RISKPARSE_CONFIG / "
                                    "config/settings.local.toml / defaults)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("config-check", help="show resolved, redacted configuration"
                   ).set_defaults(func=cmd_config_check)
    up = sub.add_parser("db-upgrade", help="apply migrations")
    up.add_argument("--revision", default="head")
    up.set_defaults(func=cmd_db_upgrade)
    sub.add_parser("db-current", help="show migration revision").set_defaults(func=cmd_db_current)

    reg = sub.add_parser("register-document", help="hash and register document(s)")
    reg.add_argument("paths", nargs="+")
    reg.add_argument("--channel", default="MANUAL_INTAKE")
    reg.add_argument("--category")
    reg.add_argument("--actor")
    reg.add_argument("--no-archive", action="store_true")
    reg.set_defaults(func=cmd_register)

    av = sub.add_parser("audit-verify", help="verify audit hash chain, evidence hashes and the "
                                             "active-publication pointer")
    av.add_argument("--expect-count", type=int, help="anchor from an earlier run: event count")
    av.add_argument("--expect-head", help="anchor from an earlier run: head event hash")
    av.set_defaults(func=cmd_audit_verify)

    it = sub.add_parser("inventory-template", help="write the blank source-inventory template")
    it.add_argument("--out", default="inventory/templates")
    it.add_argument("--csv", action="store_true", help="also write per-sheet CSV templates")
    it.set_defaults(func=cmd_inventory_template)

    iv = sub.add_parser("inventory-validate", help="validate a completed inventory workbook")
    iv.add_argument("path")
    iv.add_argument("--strict", action="store_true", help="exit 2 if required values missing")
    iv.set_defaults(func=cmd_inventory_validate)

    sc = sub.add_parser("inventory-scan", help="hash files under a folder (read-only)")
    sc.add_argument("--root", required=True)
    sc.add_argument("--out", required=True, help="CSV to write (keep it on the L drive)")
    sc.add_argument("--by", help="inventoried_by name")
    sc.set_defaults(func=cmd_inventory_scan)

    iz = sub.add_parser("intake-zip", help="hash a received ZIP, extract it safely and write "
                                           "an inventory manifest")
    iz.add_argument("zip")
    iz.add_argument("--extract-to", required=True, help="new or empty folder (keep on L drive)")
    iz.add_argument("--manifest", required=True, help="CSV to create (never overwritten)")
    iz.add_argument("--by", help="inventoried_by / actor name")
    iz.add_argument("--register", action="store_true",
                    help="also register the ZIP and members in the configured database")
    iz.add_argument("--channel", default="EMAIL_ZIP")
    iz.add_argument("--sender")
    iz.add_argument("--received-at", help="ISO timestamp WITH offset, e.g. "
                                          "2026-11-02T09:15-07:00")
    iz.set_defaults(func=cmd_intake_zip)

    rv = sub.add_parser("review", help="STEP 1: one Excel workbook listing every document in a "
                                       "ZIP or folder, with its readable text (no setup, no DB)")
    rv.add_argument("source", help="the contracts ZIP file, or a folder")
    rv.add_argument("--out", help="new output folder (default: next to the source, timestamped)")
    rv.add_argument("--format", help="the firm's raw-data-format workbook; its first-row "
                                     "headers define the output columns (default: built-in)")
    rv.add_argument("--us", action="append", default=[],
                    help="our company name as it appears on confirmations (repeatable; "
                         "default: Kingston Marketing)")
    rv.set_defaults(func=cmd_review)

    pf = sub.add_parser("profile-documents", help="count and size contract documents by "
                                                  "folder/format/status (no DB)")
    pf.add_argument("--root", required=True)
    pf.add_argument("--out", required=True, help="CSV to create (keep it on the L drive)")
    pf.set_defaults(func=cmd_profile_documents)

    ep = sub.add_parser("extract-preview", help="show the raw text segments of one file "
                                                "(non-publishing, no DB)")
    ep.add_argument("path")
    ep.add_argument("--limit", type=int, default=200)
    ep.set_defaults(func=cmd_extract_preview)

    ex = sub.add_parser("extract", help="extract text from registered documents into the DB")
    grp = ex.add_mutually_exclusive_group(required=True)
    grp.add_argument("--all", action="store_true", help="every pending document, then any "
                                                        "attachments that produced")
    grp.add_argument("--document-id", nargs="+")
    ex.add_argument("--actor")
    ex.set_defaults(func=cmd_extract)

    gd = sub.add_parser("gen-data-dictionary", help="regenerate docs/data_dictionary.md")
    gd.add_argument("--out", default="docs/data_dictionary.md")
    gd.set_defaults(func=cmd_gen_data_dictionary)

    dm = sub.add_parser("demo-smoke", help="run the synthetic end-to-end smoke demo")
    dm.add_argument("--workdir", default="var/demo")
    dm.set_defaults(func=cmd_demo_smoke)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # surface a clean message; tracebacks with --debug later
        from riskparse.config import ConfigError
        from riskparse.evidence.intake_zip import ZipIntakeError
        if isinstance(exc, ConfigError):
            print(f"configuration refused: {exc}", file=sys.stderr)
            return 3
        from riskparse.evidence.registry import EvidenceIntegrityError
        if isinstance(exc, EvidenceIntegrityError):
            print(f"evidence check failed: {exc}", file=sys.stderr)
            return 5
        from riskparse.fswalk import UnsafeOutputError
        from riskparse.rawformat import FormatFileError
        from riskparse.review import ReviewError
        if isinstance(exc, ZipIntakeError | FileExistsError | UnsafeOutputError | ReviewError
                      | FormatFileError):
            print(f"intake refused: {exc}", file=sys.stderr)
            return 4
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
