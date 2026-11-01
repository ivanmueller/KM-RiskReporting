"""Regression tests for the first Codex review (run against commit 070c321).

Each test names the Codex probe or finding it covers. All inputs are synthetic.
"""

import pytest

from riskparse.config import ConfigError, DatabaseConfig, Settings, load_settings
from riskparse.db.connection import build_url, create_db_engine

# --- settings_bypass (#5): the public connection boundary re-validates -------------


@pytest.mark.parametrize("settings", [
    Settings(environment="dev", database=DatabaseConfig(
        backend="postgresql", host="h", database="d", authorized="false")),
    Settings(environment="prod", database=DatabaseConfig(backend="sqlite",
                                                         sqlite_path=":memory:")),
    Settings(environment="dev", database=DatabaseConfig(
        backend="postgresql", host="h＠x", database="d", authorized=True)),
    Settings(environment="dev", database=DatabaseConfig(
        backend="postgresql", host="h\nx", database="d", authorized=True)),
])
def test_directly_built_settings_are_validated(settings):
    with pytest.raises(ConfigError):
        build_url(settings)
    with pytest.raises(ConfigError):
        create_db_engine(settings)


def test_named_instance_still_accepted():
    s = Settings(environment="dev", database=DatabaseConfig(
        backend="mssql", host="SERVER\\INSTANCE", database="d",
        odbc_driver="ODBC Driver 18 for SQL Server", authorized=True))
    assert build_url(s).host == "SERVER\\INSTANCE"


# --- invalid_toml: unreadable/empty/invalid config is a clean ConfigError ----------

@pytest.mark.parametrize("content", ["[database\n", "", "   \n", b"\xff\xfe".decode("latin-1")])
def test_bad_config_files_raise_config_error(tmp_path, content):
    p = tmp_path / "s.toml"
    p.write_text(content, encoding="latin-1")
    with pytest.raises(ConfigError):
        load_settings(p)


def test_config_directory_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(tmp_path)


def test_cli_invalid_toml_exits_3(tmp_path, capsys):
    from riskparse.cli import main
    p = tmp_path / "s.toml"
    p.write_text("[database\n")
    assert main(["--config", str(p), "config-check"]) == 3
    assert "not valid" in capsys.readouterr().err


# --- #3 invalid identities fail closed in the inventory validator -----------------

def test_invalid_approver_identity_is_an_error():
    from riskparse.inventory.template import SHEETS, SHEETS_BY_NAME
    from riskparse.inventory.workbook import ERROR, validate_rows
    rows = {s.name: [] for s in SHEETS}
    row = {h: "" for h in SHEETS_BY_NAME["Trade_Crosswalk"].headers}
    row.update(crosswalk_id="X1", keyed_by="pat", verified_by="pat​")
    rows["Trade_Crosswalk"] = [row]
    errs = [i for i in validate_rows(rows) if i.severity == ERROR and i.column == "verified_by"]
    assert errs and "invalid person identity" in errs[0].message


# --- #20 / #13 / #14 audit timestamps, sub-second edits, truncation anchor --------

def test_audit_rejects_naive_timestamp(conn):
    from datetime import datetime

    from riskparse import audit
    with pytest.raises(ValueError, match="timezone-aware"):
        audit.record(conn, actor="a", action="T", entity_type="x", entity_id="1",
                     occurred_at=datetime(2026, 10, 31, 12, 0))


def test_sub_second_timestamp_edit_is_detected(conn):
    from sqlalchemy import text

    from riskparse import audit
    audit.record(conn, actor="a", action="T", entity_type="x", entity_id="1")
    assert audit.verify_chain(conn).ok
    conn.execute(text("UPDATE audit_events SET occurred_at_utc = "
                      "strftime('%Y-%m-%d %H:%M:%S', occurred_at_utc) || '.000001'"))
    assert not audit.verify_chain(conn).ok


def test_truncation_detected_only_with_anchor(conn):
    from sqlalchemy import text

    from riskparse import audit
    for i in range(3):
        audit.record(conn, actor="a", action="T", entity_type="x", entity_id=str(i))
    count, head = audit.chain_head(conn)
    audit.record(conn, actor="a", action="T", entity_type="x", entity_id="later")
    assert audit.verify_chain(conn, expect_count=count, expect_head=head).ok  # growth is fine
    conn.execute(text("DELETE FROM audit_events WHERE event_id >= 3"))
    assert audit.verify_chain(conn).ok                       # undetectable without an anchor
    v = audit.verify_chain(conn, expect_count=count, expect_head=head)
    assert not v.ok and "truncation" in v.problem


def test_existing_whole_second_hashes_unchanged():
    """Chains written before this fix must still verify (whole-second format kept)."""
    from datetime import UTC, datetime

    from riskparse.audit import _event_hash
    row = {"occurred_at_utc": datetime(2026, 10, 31, 12, 0, tzinfo=UTC), "actor": "a"}
    legacy = _event_hash({**row})
    assert legacy == _event_hash({**row, "occurred_at_utc": datetime(2026, 10, 31, 12, 0,
                                                                      tzinfo=UTC)})
    assert legacy != _event_hash({**row, "occurred_at_utc": datetime(2026, 10, 31, 12, 0, 0, 1,
                                                                      tzinfo=UTC)})


# --- #1 numbers that storage would silently change ----------------------------------

@pytest.mark.parametrize("raw,reason", [
    ("9007199254740992.5", "SQLite test adapter"),
    ("12345678901234567.1234567890", "SQLite test adapter"),
    ("1.12345678901", "decimal places"),
    ("1234567890123456789", "integer digits"),
])
def test_storage_problem_detects_values_that_would_change(raw, reason):
    from decimal import Decimal

    from riskparse.numeric import storage_problem
    assert reason in storage_problem(Decimal(raw), "sqlite")


@pytest.mark.parametrize("raw", ["2.65", "0", "-5", "2.5000000000", "123456789012.5"])
def test_storage_problem_allows_exact_values(raw):
    from decimal import Decimal

    from riskparse.numeric import storage_problem
    assert storage_problem(Decimal(raw), "sqlite") is None


def test_db_refuses_to_store_a_changed_number(conn):
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import insert
    from sqlalchemy.exc import StatementError

    from riskparse.db.schema import trade_terms, trades
    now = datetime(2026, 10, 31, tzinfo=UTC)
    conn.execute(insert(trades).values(trade_uid="T1", created_at_utc=now))
    with pytest.raises(StatementError, match="refusing to store"):
        conn.execute(insert(trade_terms).values(
            trade_uid="T1", side="BUY", price_type="FIXED", price_status="PRESENT",
            price_value=Decimal("9007199254740992.5"), volume_status="MISSING",
            created_at_utc=now))
    with pytest.raises(StatementError, match="float"):
        conn.execute(insert(trade_terms).values(
            trade_uid="T1", side="BUY", price_type="FIXED", price_status="PRESENT",
            price_value=2.65, volume_status="MISSING", created_at_utc=now))


def _import_text(conn, tmp_path, text, name="s.csv"):
    from datetime import UTC, datetime

    from riskparse.evidence.registry import register_document
    from riskparse.imports import SourceMapping, import_csv_source
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    doc = register_document(conn, p, actor="tester", source_channel="TEST")
    mapping = SourceMapping(record_id_column="Ref", columns={"trade_ref": "Ref",
                                                              "price_value": "Px"},
                            parser_version="v1")
    return lambda: import_csv_source(
        conn, document_id=doc.document_id, source_system="TRADER_SHEET", source_owner="t",
        source_name="s", reporting_month="2026-10",
        source_cutoff=datetime(2026, 11, 2, tzinfo=UTC), mapping=mapping, actor="tester")


def test_import_reports_values_storage_would_change(conn, tmp_path):
    res = _import_text(conn, tmp_path, "Ref,Px\nR1,9007199254740992.5\nR2,2.65\n")()
    assert res.unparsed_values == [(2, "price_value", "9007199254740992.5")]
    assert "SQLite" in res.unparsed_details[0]["reason"]


# --- #12 malformed CSV quoting ---------------------------------------------------

def test_unterminated_quote_is_rejected(conn, tmp_path):
    from riskparse.imports import SourceFormatError
    run = _import_text(conn, tmp_path, 'Ref,Px\nR1,"2.5\n')
    with pytest.raises(SourceFormatError, match="well-formed"):
        run()


# --- #15 idempotent retry keeps the original exception report -------------------

def test_retry_returns_the_original_unparsed_report(conn, tmp_path):
    run = _import_text(conn, tmp_path, "Ref,Px\nR1,NaN\n")
    first = run()
    retry = run()
    assert retry.already_imported
    assert retry.unparsed_values == first.unparsed_values == [(2, "price_value", "NaN")]


# --- #18 (docs) a failed import CAN be retried with the same parser version -------

def test_failed_import_leaves_nothing_and_can_be_retried(engine, tmp_path):
    from datetime import UTC, datetime

    from sqlalchemy import func, select

    from riskparse.db.schema import source_imports
    from riskparse.evidence.registry import register_document
    from riskparse.imports import SourceFormatError, SourceMapping, import_csv_source
    p = tmp_path / "s.csv"
    p.write_text("Ref,Px\nR1,1\nR1,2\n")
    with engine.begin() as c:
        doc = register_document(c, p, actor="tester", source_channel="TEST")
    mapping = SourceMapping(record_id_column="Ref", columns={"price_value": "Px"},
                            parser_version="v1")

    def attempt():
        with engine.begin() as c:
            import_csv_source(c, document_id=doc.document_id, source_system="TRADER_SHEET",
                              source_owner="t", source_name="s", reporting_month="2026-10",
                              source_cutoff=datetime(2026, 11, 2, tzinfo=UTC), mapping=mapping,
                              actor="tester")
    for _ in range(2):   # the retry fails for the same reason, not "already imported"
        with pytest.raises(SourceFormatError, match="repeats"):
            attempt()
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(source_imports)).scalar() == 0


# --- scanner_overwrites / unreadable_directory / profile ---------------------------

def test_scan_refuses_output_inside_scanned_tree_and_existing_files(tmp_path, capsys):
    from riskparse.cli import main
    root = tmp_path / "evidence"
    root.mkdir()
    victim = root / "contract.pdf"
    victim.write_bytes(b"%PDF-SYNTHETIC evidence")
    assert main(["inventory-scan", "--root", str(root), "--out", str(victim)]) == 4
    assert victim.read_bytes() == b"%PDF-SYNTHETIC evidence"          # untouched
    assert main(["inventory-scan", "--root", str(root), "--out",
                 str(root / "sub" / "scan.csv")]) == 4
    assert not (root / "sub").exists()
    outside = tmp_path / "scan.csv"
    outside.write_text("existing")
    assert main(["inventory-scan", "--root", str(root), "--out", str(outside)]) == 4
    assert outside.read_text() == "existing"
    assert main(["profile-documents", "--root", str(root), "--out", str(victim)]) == 4


@pytest.mark.skipif(__import__("os").geteuid() == 0 if hasattr(__import__("os"), "geteuid")
                    else True, reason="root can read any directory")
def test_unreadable_directory_is_reported(tmp_path):
    import os
    import stat

    from riskparse.extract.profile import profile_folder
    from riskparse.inventory.scan import scan_folder
    root = tmp_path / "evidence"
    locked = root / "locked"
    locked.mkdir(parents=True)
    (locked / "a.pdf").write_bytes(b"x")
    os.chmod(locked, 0)
    try:
        _rows, findings = scan_folder(root)
        assert any(f.kind == "UNREADABLE" and "locked" in f.detail for f in findings)
        prof = profile_folder(root)
        assert any(r["status"] == "FAILED" and "locked" in r["warnings"] for r in prof.rows)
    finally:
        os.chmod(locked, stat.S_IRWXU)


def test_unreadable_directory_reported_via_walk_errors(tmp_path, monkeypatch):
    """Same check without relying on permissions (this sandbox runs as root)."""
    import os

    from riskparse.inventory.scan import scan_folder
    root = tmp_path / "evidence"
    (root / "locked").mkdir(parents=True)
    real_walk = os.walk

    def walk(top, onerror=None, followlinks=False):
        for entry in real_walk(top, onerror=onerror, followlinks=followlinks):
            if entry[0].endswith("locked"):
                onerror(PermissionError(13, "Permission denied", entry[0]))
                continue
            yield entry
    monkeypatch.setattr(os, "walk", walk)
    _rows, findings = scan_folder(root)
    assert [f.kind for f in findings] == ["UNREADABLE"]


def test_symlinked_directory_not_followed_and_reported(tmp_path):
    from riskparse.inventory.scan import scan_folder
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"x")
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    rows, findings = scan_folder(root)
    assert rows == [] and any("symbolic link" in f.detail for f in findings)


# --- invalid_inventory_time ---------------------------------------------------------

@pytest.mark.parametrize("value", ["2026-02-30 09:15 MST", "2026-11-03 25:00 MST",
                                   "2026-11-03 09:61 MST", "2026-11-03 09:15 XYZ",
                                   "2026-11-03 09:15 +15:00", "2026-11-03 09:15 Mars/Olympus",
                                   "2026-11-03 09:15"])
def test_invalid_timestamps_are_errors(value):
    from riskparse.inventory.workbook import timestamp_problem
    assert timestamp_problem(value)


@pytest.mark.parametrize("value", ["2026-11-03 09:15 MST", "2026-11-03T09:15:30Z",
                                   "2026-11-03 09:15 -07:00", "2026-11-03 09:15 America/Edmonton"])
def test_valid_timestamps_pass(value):
    from riskparse.inventory.workbook import timestamp_problem
    assert timestamp_problem(value) is None


# --- formula_disappears ---------------------------------------------------------------

def test_formula_row_is_an_error_not_an_empty_row(tmp_path):
    from openpyxl import load_workbook

    from riskparse.inventory.workbook import ERROR, validate_xlsx, write_xlsx_template
    p = write_xlsx_template(tmp_path / "t.xlsx")
    wb = load_workbook(p)
    ws = wb["Contract_Documents"]
    ws["A2"] = '="INV-DOC-"&"0001"'
    ws["B2"] = '=CONCATENATE("contract",".pdf")'
    wb.save(p)
    errs = [i for i in validate_xlsx(p) if i.severity == ERROR and i.sheet == "Contract_Documents"]
    assert {i.column for i in errs} >= {"A2", "B2"}
    assert all("formula" in i.message for i in errs if i.column in ("A2", "B2"))


# --- #19 late archive request, lost originals ------------------------------------

def test_duplicate_registration_honours_a_late_archive_request(conn, tmp_path):
    from pathlib import Path

    from riskparse.evidence.registry import register_document, verify_archive
    p = tmp_path / "c.pdf"
    p.write_bytes(b"%PDF-SYNTHETIC")
    first = register_document(conn, p, actor="a", source_channel="T")
    assert first.archive_uri is None
    again = register_document(conn, p, actor="a", source_channel="T",
                              archive_root=tmp_path / "arc", category="CONFIRMATION")
    assert again.outcome == "DUPLICATE" and again.archive_uri
    assert Path(again.archive_uri).read_bytes() == b"%PDF-SYNTHETIC"
    p.unlink()                                       # original gone; archive still holds it
    assert verify_archive(conn) == []


def test_lost_original_without_archive_is_reported(conn, tmp_path):
    from riskparse.evidence.registry import register_document, verify_archive
    p = tmp_path / "c.pdf"
    p.write_bytes(b"%PDF-SYNTHETIC")
    reg = register_document(conn, p, actor="a", source_channel="T")
    assert verify_archive(conn) == []
    p.write_bytes(b"%PDF-CHANGED")
    assert verify_archive(conn) == [(reg.document_id, "no archive copy and the original file "
                                                      "has changed")]
    p.unlink()
    assert "missing" in verify_archive(conn)[0][1]


def test_register_rejects_naive_received_at(conn, tmp_path):
    from datetime import datetime

    from riskparse.evidence.registry import register_document
    p = tmp_path / "c.pdf"
    p.write_bytes(b"x")
    with pytest.raises(ValueError, match="timezone-aware"):
        register_document(conn, p, actor="a", source_channel="T",
                          received_at=datetime(2026, 11, 2, 9, 0))


# --- ZIP intake: #11 symlink race, #24 corrupt member, #23 argument checks ---------

def _zipfile(path, members):
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for name, data in members:
            z.writestr(name, data)
    return path


@pytest.mark.skipif(not (hasattr(__import__("os"), "O_NOFOLLOW")
                         and __import__("os").open in __import__("os").supports_dir_fd),
                    reason="dir_fd/O_NOFOLLOW not available on this platform")
def test_folder_swapped_for_symlink_mid_extraction_cannot_redirect_writes(tmp_path,
                                                                           monkeypatch):
    import os

    from riskparse.evidence import intake_zip
    outside = tmp_path / "outside"
    outside.mkdir()
    z = _zipfile(tmp_path / "z.zip", [("sub/a.pdf", b"SYNTHETIC")])
    real_mkdir = os.mkdir

    def racing_mkdir(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        if path == "sub":                       # attacker swaps the new folder for a link
            os.rmdir(path, dir_fd=dir_fd)
            os.symlink(outside, path, dir_fd=dir_fd)
    monkeypatch.setattr(os, "mkdir", racing_mkdir)
    with pytest.raises(intake_zip.ZipIntakeError, match="not a plain directory"):
        intake_zip.safe_extract(z, tmp_path / "out")
    assert list(outside.iterdir()) == []         # nothing written outside the root


def test_destination_symlink_refused(tmp_path):
    from riskparse.evidence.intake_zip import ZipIntakeError, safe_extract
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    z = _zipfile(tmp_path / "z.zip", [("a.pdf", b"x")])
    with pytest.raises(ZipIntakeError, match="symbolic link"):
        safe_extract(z, tmp_path / "link")


def _corrupt_crc(path, member_data):
    data = bytearray(path.read_bytes())
    i = data.index(member_data)
    data[i] ^= 0xFF                              # flip a byte of the stored member data
    path.write_bytes(bytes(data))


def test_corrupt_member_is_refused_cleaned_up_and_listed(tmp_path):
    from riskparse.evidence.intake_zip import manifest_rows, safe_extract
    z = _zipfile(tmp_path / "z.zip", [("good.pdf", b"GOOD-SYNTHETIC"),
                                      ("bad.pdf", b"BAD-SYNTHETIC-DATA")])
    _corrupt_crc(z, b"BAD-SYNTHETIC-DATA")
    r = safe_extract(z, tmp_path / "out")
    assert [m.member for m in r.members] == ["good.pdf"]
    assert any(f.kind == "CORRUPT_MEMBER" and f.member == "bad.pdf" for f in r.findings)
    assert not (tmp_path / "out" / "bad.pdf").exists()            # partial file removed
    rows = manifest_rows(r)
    bad = [row for row in rows if row["file_name"] == "bad.pdf"]
    assert bad and bad[0]["inventory_status"] == "EXCEPTED" and bad[0]["exception_reason"]


def test_cli_corrupt_zip_is_a_clean_refusal(tmp_path, capsys):
    from riskparse.cli import main
    z = tmp_path / "z.zip"
    z.write_bytes(b"PK\x03\x04 truncated synthetic garbage")
    assert main(["intake-zip", str(z), "--extract-to", str(tmp_path / "out"),
                 "--manifest", str(tmp_path / "m.csv")]) == 4


def test_cli_naive_received_at_refused_before_any_write(tmp_path, capsys):
    from riskparse.cli import main
    z = _zipfile(tmp_path / "z.zip", [("a.pdf", b"x")])
    out, manifest = tmp_path / "out", tmp_path / "m.csv"
    assert main(["intake-zip", str(z), "--extract-to", str(out), "--manifest", str(manifest),
                 "--received-at", "2026-11-02T09:15"]) == 4
    assert not out.exists() and not manifest.exists()
    assert "UTC offset" in capsys.readouterr().err


def test_cli_manifest_inside_extraction_dir_refused(tmp_path):
    from riskparse.cli import main
    z = _zipfile(tmp_path / "z.zip", [("a.pdf", b"x")])
    assert main(["intake-zip", str(z), "--extract-to", str(tmp_path / "out"),
                 "--manifest", str(tmp_path / "out" / "m.csv")]) == 4
    assert not (tmp_path / "out").exists()


def test_zip_changed_during_extraction_is_detected(tmp_path, monkeypatch):
    from riskparse.evidence import intake_zip
    z = _zipfile(tmp_path / "z.zip", [("a.pdf", b"x")])
    real = intake_zip.fingerprint
    calls = {"n": 0}

    def changing(path):
        calls["n"] += 1
        fp = real(path)
        if calls["n"] == 2:
            from dataclasses import replace
            return replace(fp, sha256="0" * 64)
        return fp
    monkeypatch.setattr(intake_zip, "fingerprint", changing)
    with pytest.raises(intake_zip.ZipIntakeError, match="changed while"):
        intake_zip.safe_extract(z, tmp_path / "out")


# --- smoke_false_pass (#25) and empty_pointer (#26) -------------------------------

def test_smoke_check_fails_on_any_wrong_status(tmp_path, monkeypatch, capsys):
    from riskparse.cli import main
    from riskparse.demo import smoke
    real = smoke.run_smoke

    def wrong(workdir):
        s = real(workdir)
        s["reconciliation_statuses"]["SYN-T002"] = "MATCHED"     # a break hidden as a match
        return s
    monkeypatch.setattr(smoke, "run_smoke", wrong)
    assert main(["demo-smoke", "--workdir", str(tmp_path / "d")]) == 1
    out = capsys.readouterr().out
    assert "SMOKE DEMO: FAIL" in out and "SYN-T002: expected MATCHED_BREAK" in out


def test_audit_verify_fails_when_pointer_row_deleted(tmp_path, capsys):
    from sqlalchemy import create_engine, text

    from riskparse.cli import main
    cfg = tmp_path / "s.toml"
    cfg.write_text(f'[environment]\nname="test"\n[database]\nbackend="sqlite"\n'
                   f'sqlite_path="{(tmp_path / "c.db").as_posix()}"\n')
    assert main(["--config", str(cfg), "db-upgrade"]) == 0
    assert main(["--config", str(cfg), "audit-verify"]) == 0
    eng = create_engine(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    with eng.begin() as c:
        c.execute(text("DELETE FROM active_publication"))
    eng.dispose()
    assert main(["--config", str(cfg), "audit-verify"]) == 1
    assert "exactly 1 pointer row" in capsys.readouterr().out
