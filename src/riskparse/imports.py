"""Import a registered tabular source (Egistix export / trader sheet) into SQL staging.

Phase 1 supports CSV only, with an explicit column mapping. The real Egistix and
trader-sheet layouts are unknown (blocker) so no production mapping exists yet; the
demo uses a synthetic layout. Every raw row is stored unchanged as JSON together with
its hash; normalized columns are filled only when the mapping names a column, and a
value that cannot be parsed is left NULL (never coerced).
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import Connection, insert, select

from riskparse import audit, vocab
from riskparse.db.schema import source_documents, source_imports, source_records

NORMALIZED_FIELDS = ("trade_ref", "trade_date", "counterparty_raw", "side", "commodity",
                     "volume_value", "volume_unit", "price_value", "price_unit",
                     "delivery_start", "delivery_end")
_DECIMAL_FIELDS = {"volume_value", "price_value"}
_DATE_FIELDS = {"trade_date", "delivery_start", "delivery_end"}


@dataclass(frozen=True)
class SourceMapping:
    """Maps normalized field -> source column name, plus the record-ID column."""
    record_id_column: str
    columns: dict[str, str]
    parser_version: str

    def __post_init__(self):
        unknown = set(self.columns) - set(NORMALIZED_FIELDS)
        if unknown:
            raise ValueError(f"unknown normalized fields in mapping: {sorted(unknown)}")


@dataclass(frozen=True)
class ImportResult:
    import_id: str
    row_count: int
    unparsed_values: list[tuple[int, str, str]]  # (row_number, field, raw)
    already_imported: bool = False


def _parse(field: str, raw: str):
    raw = (raw or "").strip()
    if raw == "":
        return None, True
    if field in _DECIMAL_FIELDS:
        try:
            return Decimal(raw.replace(",", "")), True
        except InvalidOperation:
            return None, False
    if field in _DATE_FIELDS:
        try:
            return date.fromisoformat(raw), True
        except ValueError:
            return None, False
    if field == "side":
        up = raw.upper()
        return (up, True) if up in vocab.SIDES else (None, False)
    return raw, True


def import_csv_source(conn: Connection, *, document_id: str, source_system: str,
                      source_owner: str, source_name: str, reporting_month: str,
                      source_cutoff: datetime, mapping: SourceMapping,
                      actor: str) -> ImportResult:
    if source_system not in vocab.SOURCE_SYSTEMS:
        raise ValueError(f"unknown source system {source_system}")
    doc = conn.execute(select(source_documents).where(
        source_documents.c.document_id == document_id)).mappings().first()
    if doc is None:
        raise LookupError(f"document {document_id} is not registered")
    existing = conn.execute(select(source_imports.c.import_id, source_imports.c.row_count).where(
        source_imports.c.document_id == document_id,
        source_imports.c.source_system == source_system,
        source_imports.c.parser_version == mapping.parser_version)).first()
    if existing is not None:   # idempotent: same bytes + same parser => same import
        return ImportResult(existing.import_id, existing.row_count or 0, [], True)

    path = Path(doc["archive_uri"] or doc["original_path"])
    import_id = f"IMP-{uuid.uuid4().hex[:16].upper()}"
    conn.execute(insert(source_imports).values(
        import_id=import_id, source_system=source_system, source_owner=source_owner,
        source_name=source_name, document_id=document_id, reporting_month=reporting_month,
        source_cutoff_utc=source_cutoff, imported_at_utc=datetime.now(UTC),
        status="STAGED", parser_version=mapping.parser_version,
    ))
    unparsed: list[tuple[int, str, str]] = []
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row_number, row in enumerate(csv.DictReader(fh), start=2):
            raw_json = json.dumps(row, sort_keys=True, ensure_ascii=False)
            values = {}
            for field, col in mapping.columns.items():
                parsed, ok = _parse(field, row.get(col, ""))
                if not ok:
                    unparsed.append((row_number, field, row.get(col, "")))
                values[field] = parsed
            conn.execute(insert(source_records).values(
                import_id=import_id,
                source_record_id=(row.get(mapping.record_id_column) or f"ROW{row_number}"),
                row_number=row_number,
                raw_payload=raw_json,
                raw_payload_sha256=hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
                **values,
            ))
            count += 1
    conn.execute(source_imports.update().where(source_imports.c.import_id == import_id)
                 .values(row_count=count, status="LOADED"))
    audit.record(conn, actor=actor, action="SOURCE_IMPORTED", entity_type="source_import",
                 entity_id=import_id, document_id=document_id,
                 after={"source_system": source_system, "owner": source_owner,
                        "rows": count, "unparsed_values": len(unparsed),
                        "parser_version": mapping.parser_version})
    return ImportResult(import_id, count, unparsed)
