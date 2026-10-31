import os
import stat
from datetime import UTC, datetime

import pytest

from riskparse.evidence.registry import register_document
from riskparse.imports import (
    SourceFormatError,
    SourceIntegrityError,
    SourceMapping,
    import_csv_source,
)

MAP = SourceMapping(record_id_column="Ref",
                    columns={"trade_ref": "Ref", "volume_value": "Qty", "side": "Side",
                             "trade_date": "Date"},
                    parser_version="test-v0")


def _import(conn, doc_id):
    return import_csv_source(conn, document_id=doc_id, source_system="TRADER_SHEET",
                             source_owner="T", source_name="s", reporting_month="2026-10",
                             source_cutoff=datetime(2026, 11, 2, tzinfo=UTC), mapping=MAP,
                             actor="tester")


def test_unparseable_values_left_null_and_reported(conn, tmp_path):
    p = tmp_path / "sheet.csv"
    p.write_text("Ref,Qty,Side,Date\nR1,1000,Buy,2026-10-01\nR2,ten,Long,10/02/2026\n")
    doc = register_document(conn, p, actor="tester", source_channel="TEST")
    res = _import(conn, doc.document_id)
    assert res.row_count == 2
    assert {(f, raw) for _, f, raw in res.unparsed_values} == {
        ("volume_value", "ten"), ("side", "Long"), ("trade_date", "10/02/2026")}


def test_import_is_idempotent(conn, tmp_path):
    p = tmp_path / "sheet.csv"
    p.write_text("Ref,Qty,Side,Date\nR1,1,Buy,2026-10-01\n")
    doc = register_document(conn, p, actor="tester", source_channel="TEST")
    a = _import(conn, doc.document_id)
    b = _import(conn, doc.document_id)
    assert b.already_imported and a.import_id == b.import_id


# --- Regression tests: D-2 (lossy number parsing), S-4 (hash re-check), S-5 (CSV shape) --

def _register_csv(conn, tmp_path, text, name="sheet.csv", archive=True, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return register_document(conn, p, actor="tester", source_channel="TEST",
                             archive_root=(tmp_path / "arc") if archive else None)


def test_s4_import_refuses_bytes_that_differ_from_evidence(conn, tmp_path):
    doc = _register_csv(conn, tmp_path, "Ref,Qty,Side,Date\nR1,1,Buy,2026-10-01\n")
    os.chmod(doc.archive_uri, stat.S_IRUSR | stat.S_IWUSR)
    with open(doc.archive_uri, "w") as fh:
        fh.write("Ref,Qty,Side,Date\nR1,999,Buy,2026-10-01\n")
    with pytest.raises(SourceIntegrityError):
        _import(conn, doc.document_id)


def test_s4_original_path_also_verified(conn, tmp_path):
    doc = _register_csv(conn, tmp_path, "Ref,Qty,Side,Date\nR1,1,Buy,2026-10-01\n",
                        archive=False)
    (tmp_path / "sheet.csv").write_text("Ref,Qty,Side,Date\nR1,2,Buy,2026-10-01\n")
    with pytest.raises(SourceIntegrityError):
        _import(conn, doc.document_id)


@pytest.mark.parametrize("text,match", [
    ("Ref,Qty,Qty,Side,Date\nR1,1,2,Buy,2026-10-01\n", "duplicate column"),
    ("Ref,Qty,Side,Date\nR1,1,Buy,2026-10-01,EXTRA\n", "fields but"),
    ("Ref,Qty,Side,Date\nR1,1,Buy,2026-10-01\nR1,2,Sell,2026-10-02\n", "repeats"),
    ("Ref,Side,Date\nR1,Buy,2026-10-01\n", "missing mapped column"),
    ("", "no header"),
])
def test_s5_csv_shape_problems_fail_loudly(conn, tmp_path, text, match):
    doc = _register_csv(conn, tmp_path, text)
    with pytest.raises(SourceFormatError, match=match):
        _import(conn, doc.document_id)


def test_s5_non_utf8_is_reported_not_guessed(conn, tmp_path):
    doc = _register_csv(conn, tmp_path, "Ref,Qty,Side,Date\nR1,1,Buy,2026-10-01 café\n",
                        encoding="cp1252")
    with pytest.raises(SourceFormatError, match="not UTF-8"):
        _import(conn, doc.document_id)


def test_short_rows_and_blank_lines_kept_as_null(conn, tmp_path):
    doc = _register_csv(conn, tmp_path, "Ref,Qty,Side,Date\nR1,1\n\nR2,2,Sell,2026-10-02\n")
    res = _import(conn, doc.document_id)
    assert res.row_count == 2 and res.unparsed_values == []


def test_failed_import_leaves_no_partial_rows(engine, tmp_path):
    from sqlalchemy import func, select

    from riskparse.db.schema import source_imports, source_records
    with engine.begin() as c:
        doc = _register_csv(c, tmp_path, "Ref,Qty,Side,Date\nR1,1,Buy,2026-10-01\n"
                                         "R1,2,Buy,2026-10-01\n")
    with pytest.raises(SourceFormatError), engine.begin() as c:
        _import(c, doc.document_id)
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(source_imports)).scalar() == 0
        assert c.execute(select(func.count()).select_from(source_records)).scalar() == 0
