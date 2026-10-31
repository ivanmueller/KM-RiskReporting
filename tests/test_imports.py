from datetime import UTC, datetime

from riskparse.evidence.registry import register_document
from riskparse.imports import SourceMapping, import_csv_source

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
