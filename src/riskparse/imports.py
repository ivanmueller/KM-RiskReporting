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
import io
import json
import re
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, insert, select

from riskparse import audit, vocab
from riskparse.db.schema import source_documents, source_imports, source_records
from riskparse.numeric import storage_problem

NORMALIZED_FIELDS = ("trade_ref", "trade_date", "counterparty_raw", "side", "commodity",
                     "volume_value", "volume_unit", "price_value", "price_unit",
                     "delivery_start", "delivery_end")
_DECIMAL_FIELDS = {"volume_value", "price_value"}
_DATE_FIELDS = {"trade_date", "delivery_start", "delivery_end"}


@dataclass(frozen=True)
class SourceMapping:
    """Maps normalized field -> source column name, plus the record-ID column.

    ``thousands_separator`` must be declared explicitly (e.g. ``","``) for sources that
    write ``1,234.50``; otherwise any separator makes the value unparsed. The decimal
    separator is always ``"."`` - a source using decimal commas needs its own reviewed
    mapping rather than a guess.
    """
    record_id_column: str
    columns: dict[str, str]
    parser_version: str
    thousands_separator: str | None = None
    # Units a reviewed mapping declares for a source that has no unit column (e.g. a
    # trader sheet whose quantities are all GJ). Units only; raw rows are unchanged.
    fixed_units: dict[str, str] | None = None

    def __post_init__(self):
        unknown = set(self.columns) - set(NORMALIZED_FIELDS)
        if unknown:
            raise ValueError(f"unknown normalized fields in mapping: {sorted(unknown)}")
        bad = set(self.fixed_units or {}) - {"volume_unit", "price_unit"}
        if bad:
            raise ValueError(f"fixed_units may only declare volume_unit/price_unit: {sorted(bad)}")
        clash = set(self.fixed_units or {}) & set(self.columns)
        if clash:
            raise ValueError(f"{sorted(clash)} is both mapped to a column and fixed")
        if self.thousands_separator not in (None, ",", " ", "'"):
            raise ValueError("thousands_separator must be None, ',', ' ' or \"'\"")


class SourceFormatError(ValueError):
    """The source file cannot be imported without losing or guessing data."""


class SourceIntegrityError(RuntimeError):
    """The bytes about to be parsed do not match the registered SHA-256."""


def _read_verified_csv(path: Path, expected_sha256: str, mapping: SourceMapping):
    """Read once into memory, verify the hash, then parse those same bytes.

    Reading once avoids a check-then-use gap where the file could change between
    the hash check and parsing.
    """
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise SourceIntegrityError(
            f"{path} hashes to {actual}, but the registered document is {expected_sha256}; "
            "refusing to import bytes that differ from the evidence"
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceFormatError(
            f"{path} is not UTF-8 ({exc.reason} at byte {exc.start}). Export as UTF-8, or add "
            "a reviewed mapping that declares the source encoding - encodings are not guessed."
        ) from exc
    # strict=True: malformed quoting (e.g. an unterminated quote swallowing the rest of
    # the file into one field) is an error, not silently accepted (Codex review #12).
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        return _rows_from_reader(reader, path, mapping)
    except csv.Error as exc:
        raise SourceFormatError(f"{path} is not well-formed CSV (line {reader.line_num}): "
                                f"{exc}") from exc


def _rows_from_reader(reader, path: Path, mapping: SourceMapping):
    header = next(reader, None)
    if not header:
        raise SourceFormatError(f"{path} has no header row")
    header = [h.strip() for h in header]
    dupes = sorted({h for h in header if header.count(h) > 1})
    if dupes:
        raise SourceFormatError(f"{path} has duplicate column name(s) {dupes}; values would "
                                "be overwritten")
    missing = sorted({mapping.record_id_column, *mapping.columns.values()} - set(header))
    if missing:
        raise SourceFormatError(f"{path} is missing mapped column(s) {missing}")
    rows = []
    seen: dict[str, int] = {}
    for row_number, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue  # blank line
        if len(values) > len(header):
            raise SourceFormatError(f"{path} row {row_number} has {len(values)} fields but "
                                    f"the header has {len(header)}")
        row = {h: (values[i] if i < len(values) else None) for i, h in enumerate(header)}
        record_id = (row.get(mapping.record_id_column) or "").strip() or f"ROW{row_number}"
        if record_id in seen:
            raise SourceFormatError(f"{path} record id {record_id!r} repeats at rows "
                                    f"{seen[record_id]} and {row_number}")
        seen[record_id] = row_number
        rows.append((row_number, row, record_id))
    return rows


@dataclass(frozen=True)
class ImportResult:
    import_id: str
    row_count: int
    unparsed_values: list[tuple[int, str, str]]  # (row_number, field, raw)
    already_imported: bool = False
    unparsed_details: list[dict] = dc_field(default_factory=list)  # adds "reason"


# ASCII-only patterns: Python's \\d would also accept non-Latin digits.
_PLAIN_DECIMAL = re.compile(r"^[+-]?[0-9]+(\.[0-9]+)?$", re.ASCII)
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", re.ASCII)


def _grouped_decimal(sep: str) -> re.Pattern[str]:
    s = re.escape(sep)
    return re.compile(rf"^[+-]?[0-9]{{1,3}}({s}[0-9]{{3}})+(\.[0-9]+)?$", re.ASCII)


def _parse(field: str, raw: str, thousands_separator: str | None = None):
    """Return ``(value, ok)``. Blank -> ``(None, True)``. Anything ambiguous -> not ok.

    Never guesses: ``2,65``, ``1e3``, ``NaN``, ``$2.50``, ``(100)`` and ``10/02/2026``
    are all reported as unparsed rather than converted.
    """
    raw = (raw or "").strip()
    if raw == "":
        return None, True
    if field in _DECIMAL_FIELDS:
        text = raw
        if thousands_separator and _grouped_decimal(thousands_separator).match(raw):
            text = raw.replace(thousands_separator, "")
        if not _PLAIN_DECIMAL.match(text):
            return None, False
        value = Decimal(text)
        if not value.is_finite():  # defensive; the pattern already excludes NaN/Inf
            return None, False
        return value, True
    if field in _DATE_FIELDS:
        if not _ISO_DATE.match(raw):
            return None, False
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
        # Return the exceptions recorded the first time, so a retry never looks clean
        # (Codex review #15).
        stored = conn.execute(select(source_imports.c.unparsed_json).where(
            source_imports.c.import_id == existing.import_id)).scalar()
        details = json.loads(stored) if stored else []
        return ImportResult(existing.import_id, existing.row_count or 0,
                            [(d["row"], d["field"], d["raw"]) for d in details], True, details)

    path = Path(doc["archive_uri"] or doc["original_path"])
    rows = _read_verified_csv(path, doc["sha256"], mapping)

    import_id = f"IMP-{uuid.uuid4().hex[:16].upper()}"
    conn.execute(insert(source_imports).values(
        import_id=import_id, source_system=source_system, source_owner=source_owner,
        source_name=source_name, document_id=document_id, reporting_month=reporting_month,
        source_cutoff_utc=source_cutoff, imported_at_utc=datetime.now(UTC),
        status="STAGED", parser_version=mapping.parser_version,
    ))
    unparsed: list[tuple[int, str, str]] = []
    details: list[dict] = []
    dialect = conn.dialect.name
    count = 0
    for row_number, row, record_id in rows:
        raw_json = json.dumps(row, sort_keys=True, ensure_ascii=False)
        values = {}
        for field, col in mapping.columns.items():
            raw = row.get(col) or ""
            parsed, ok = _parse(field, raw, mapping.thousands_separator)
            reason = None if ok else "not an accepted format"
            if ok and isinstance(parsed, Decimal):
                reason = storage_problem(parsed, dialect)   # would storage change it? (#1)
                if reason:
                    parsed, ok = None, False
            if not ok:
                unparsed.append((row_number, field, raw))
                details.append({"row": row_number, "field": field, "raw": raw,
                                "reason": reason})
            values[field] = parsed
        values.update(mapping.fixed_units or {})
        conn.execute(insert(source_records).values(
            import_id=import_id,
            source_record_id=record_id,
            row_number=row_number,
            raw_payload=raw_json,
            raw_payload_sha256=hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
            **values,
        ))
        count += 1
    conn.execute(source_imports.update().where(source_imports.c.import_id == import_id)
                 .values(row_count=count, status="LOADED",
                         unparsed_json=json.dumps(details) if details else None))
    audit.record(conn, actor=actor, action="SOURCE_IMPORTED", entity_type="source_import",
                 entity_id=import_id, document_id=document_id,
                 after={"source_system": source_system, "owner": source_owner,
                        "rows": count, "unparsed_values": len(unparsed),
                        "parser_version": mapping.parser_version})
    return ImportResult(import_id, count, unparsed, False, details)
