"""Minimal, engine-neutral relational schema for Stage 1 (game plan section 5.1).

Written with SQLAlchemy Core using only portable features (tables, PK/FK, UNIQUE,
CHECK, indexes). Engine-specific controls - row/app locks for the publisher,
DENY UPDATE permissions, triggers enforcing immutability, temporal tables - are
deliberately **not** used until IT confirms the SQL product.

The authoritative history of the physical schema is the Alembic migration chain in
``riskparse/db/migrations``; ``tests/test_migrations.py`` asserts the two agree.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import TypeDecorator

from riskparse import vocab

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

MONEY = Numeric(28, 10)
# Portable shape checks (LIKE '_' wildcards work on SQLite, SQL Server and PostgreSQL;
# LENGTH()/LEN() differ between engines so they are avoided). Hex content is enforced
# in application code (riskparse.evidence.hashing.is_sha256_hex).
SHA256_LIKE = "_" * 64
MONTH_LIKE = "____-__"
QTY = Numeric(28, 10)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC; accepts only timezone-aware datetimes; returns aware UTC.

    Prevents silently storing local/ambiguous timestamps (game plan 11: timezone errors).
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError("UTCDateTime requires a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime rejected: supply a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _in(column: str, values: tuple[str, ...], nullable: bool = False) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    expr = f"{column} IN ({quoted})"
    return f"({column} IS NULL OR {expr})" if nullable else expr


def _value_status_rule(status_col: str, value_col: str) -> str:
    # PRESENT <=> numeric value supplied. A genuine zero is PRESENT with 0.
    return (
        f"(({status_col} = 'PRESENT' AND {value_col} IS NOT NULL) OR "
        f"({status_col} <> 'PRESENT' AND {value_col} IS NULL))"
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
source_documents = Table(
    "source_documents",
    metadata,
    Column("document_id", String(40), primary_key=True),
    Column("sha256", String(64), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("file_type", String(10), nullable=False),
    Column("document_category", String(30), nullable=True),
    Column("original_filename", String(400), nullable=False),
    Column("original_path", String(1000), nullable=False),
    Column("archive_uri", String(1000), nullable=True),
    Column("source_channel", String(40), nullable=False),
    Column("sender", String(320), nullable=True),
    Column("received_at_utc", UTCDateTime(), nullable=True),
    Column("recorded_at_utc", UTCDateTime(), nullable=False),
    Column("file_modified_at_utc", UTCDateTime(), nullable=True),
    Column("content_status", String(20), nullable=False, server_default="REGISTERED"),
    Column("parent_document_id", String(40),
           ForeignKey("source_documents.document_id"), nullable=True),
    Column("revision_of_document_id", String(40),
           ForeignKey("source_documents.document_id"), nullable=True),
    Column("is_synthetic", Boolean, nullable=False, server_default="0"),
    Column("evidence_note", Text, nullable=True),
    UniqueConstraint("sha256"),
    CheckConstraint(f"sha256 LIKE '{SHA256_LIKE}'", name="sha256_shape"),
    CheckConstraint("size_bytes >= 0", name="size_nonneg"),
    CheckConstraint(_in("file_type", vocab.DOCUMENT_FILE_TYPES), name="file_type"),
    CheckConstraint(_in("document_category", vocab.DOCUMENT_CATEGORIES, nullable=True),
                    name="category"),
    CheckConstraint(_in("content_status", vocab.DOCUMENT_CONTENT_STATUSES),
                    name="content_status"),
    CheckConstraint("parent_document_id IS NULL OR parent_document_id <> document_id",
                    name="parent_not_self"),
    CheckConstraint("revision_of_document_id IS NULL OR revision_of_document_id <> document_id",
                    name="revision_not_self"),
)

document_observations = Table(
    "document_observations",
    metadata,
    Column("observation_id", Integer, primary_key=True, autoincrement=True),
    Column("document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=False),
    Column("observed_path", String(1000), nullable=False),
    Column("observed_filename", String(400), nullable=False),
    Column("observed_at_utc", UTCDateTime(), nullable=False),
    Column("file_modified_at_utc", UTCDateTime(), nullable=True),
    Index("ix_document_observations_document_id", "document_id"),
)

# ---------------------------------------------------------------------------
# Trade identity and economics
# ---------------------------------------------------------------------------
trades = Table(
    "trades",
    metadata,
    Column("trade_uid", String(40), primary_key=True),
    Column("trader", String(100), nullable=True),
    Column("book", String(100), nullable=True),
    Column("counterparty_id", String(60), nullable=True),
    Column("counterparty_raw_name", String(300), nullable=True),
    Column("commodity", String(40), nullable=True),
    Column("product", String(80), nullable=True),
    Column("physical_financial", String(10), nullable=False, server_default="UNKNOWN"),
    Column("is_evergreen", Boolean, nullable=False, server_default="0"),
    Column("lifecycle_status", String(20), nullable=False, server_default="ACTIVE"),
    Column("trade_date", Date, nullable=True),
    Column("parent_trade_uid", String(40), ForeignKey("trades.trade_uid"), nullable=True),
    Column("is_synthetic", Boolean, nullable=False, server_default="0"),
    Column("created_at_utc", UTCDateTime(), nullable=False),
    CheckConstraint(_in("physical_financial", vocab.PHYSICAL_FINANCIAL), name="phys_fin"),
    CheckConstraint(_in("lifecycle_status", vocab.TRADE_LIFECYCLE), name="lifecycle"),
    CheckConstraint("parent_trade_uid IS NULL OR parent_trade_uid <> trade_uid",
                    name="parent_not_self"),
)

trade_aliases = Table(
    "trade_aliases",
    metadata,
    Column("alias_id", Integer, primary_key=True, autoincrement=True),
    Column("trade_uid", String(40), ForeignKey("trades.trade_uid"), nullable=False),
    Column("alias_system", String(20), nullable=False),
    Column("alias_value", String(120), nullable=False),
    Column("created_at_utc", UTCDateTime(), nullable=False),
    UniqueConstraint("alias_system", "alias_value"),
    CheckConstraint(_in("alias_system", vocab.ALIAS_SYSTEMS), name="alias_system"),
    Index("ix_trade_aliases_trade_uid", "trade_uid"),
)

trade_documents = Table(
    "trade_documents",
    metadata,
    Column("trade_uid", String(40), ForeignKey("trades.trade_uid"), nullable=False),
    Column("document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=False),
    Column("relationship", String(30), nullable=False),
    Column("effective_from", Date, nullable=True),
    Column("supersession_scope", Text, nullable=True),
    Column("created_at_utc", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("trade_uid", "document_id", "relationship"),
    CheckConstraint(_in("relationship", vocab.TRADE_DOCUMENT_RELATIONSHIPS),
                    name="relationship"),
)

trade_terms = Table(
    "trade_terms",
    metadata,
    Column("term_id", Integer, primary_key=True, autoincrement=True),
    Column("trade_uid", String(40), ForeignKey("trades.trade_uid"), nullable=False),
    Column("leg", Integer, nullable=False, server_default="1"),
    Column("term_version", Integer, nullable=False, server_default="1"),
    Column("side", String(4), nullable=False),
    Column("price_type", String(20), nullable=False),
    Column("price_status", String(20), nullable=False),
    Column("price_value", MONEY, nullable=True),
    Column("price_raw", String(300), nullable=True),
    Column("price_unit", String(40), nullable=True),
    Column("currency", String(3), nullable=True),
    Column("index_name", String(120), nullable=True),
    Column("price_differential", MONEY, nullable=True),
    Column("volume_status", String(20), nullable=False),
    Column("volume_value", QTY, nullable=True),
    Column("volume_raw", String(300), nullable=True),
    Column("volume_unit", String(40), nullable=True),
    Column("volume_basis", String(20), nullable=False, server_default="UNKNOWN"),
    Column("delivery_location", String(120), nullable=True),
    Column("delivery_timezone", String(60), nullable=True),
    Column("delivery_start", Date, nullable=True),
    Column("delivery_end", Date, nullable=True),
    Column("valid_from_utc", UTCDateTime(), nullable=True),
    Column("valid_to_utc", UTCDateTime(), nullable=True),
    Column("evidence_document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=True),
    Column("evidence_location", String(300), nullable=True),
    Column("review_status", String(20), nullable=False, server_default="UNREVIEWED"),
    Column("created_at_utc", UTCDateTime(), nullable=False),
    UniqueConstraint("trade_uid", "leg", "term_version"),
    CheckConstraint(_in("side", vocab.SIDES), name="side"),
    CheckConstraint(_in("price_type", vocab.PRICE_TYPES), name="price_type"),
    CheckConstraint(_in("price_status", vocab.VALUE_STATUSES), name="price_status"),
    CheckConstraint(_in("volume_status", vocab.VALUE_STATUSES), name="volume_status"),
    CheckConstraint(_in("volume_basis", vocab.VOLUME_BASES), name="volume_basis"),
    CheckConstraint(_in("review_status", vocab.REVIEW_STATUSES), name="review_status"),
    CheckConstraint(_value_status_rule("price_status", "price_value"), name="price_value"),
    CheckConstraint(_value_status_rule("volume_status", "volume_value"), name="volume_value"),
    CheckConstraint("volume_value IS NULL OR volume_value >= 0", name="volume_nonneg"),
    CheckConstraint("leg >= 1 AND term_version >= 1", name="leg_version_pos"),
    CheckConstraint("delivery_start IS NULL OR delivery_end IS NULL OR "
                    "delivery_end >= delivery_start", name="delivery_order"),
    CheckConstraint("valid_from_utc IS NULL OR valid_to_utc IS NULL OR "
                    "valid_to_utc > valid_from_utc", name="validity_order"),
)

# ---------------------------------------------------------------------------
# Source imports (Egistix exports, every trader sheet)
# ---------------------------------------------------------------------------
source_imports = Table(
    "source_imports",
    metadata,
    Column("import_id", String(40), primary_key=True),
    Column("source_system", String(20), nullable=False),
    Column("source_owner", String(100), nullable=False),
    Column("source_name", String(200), nullable=False),
    Column("document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=False),
    Column("reporting_month", String(7), nullable=False),
    Column("source_cutoff_utc", UTCDateTime(), nullable=False),
    Column("imported_at_utc", UTCDateTime(), nullable=False),
    Column("row_count", Integer, nullable=True),
    Column("status", String(20), nullable=False, server_default="STAGED"),
    Column("parser_version", String(60), nullable=False),
    UniqueConstraint("document_id", "source_system", "parser_version"),
    CheckConstraint(_in("source_system", vocab.SOURCE_SYSTEMS), name="source_system"),
    CheckConstraint(_in("status", vocab.IMPORT_STATUSES), name="status"),
    CheckConstraint(f"reporting_month LIKE '{MONTH_LIKE}'", name="month_fmt"),
    CheckConstraint("row_count IS NULL OR row_count >= 0", name="row_count_nonneg"),
)

source_records = Table(
    "source_records",
    metadata,
    Column("source_record_pk", Integer, primary_key=True, autoincrement=True),
    Column("import_id", String(40), ForeignKey("source_imports.import_id"), nullable=False),
    Column("source_record_id", String(120), nullable=False),
    Column("row_number", Integer, nullable=True),
    Column("raw_payload", Text, nullable=False),
    Column("raw_payload_sha256", String(64), nullable=False),
    Column("trade_ref", String(120), nullable=True),
    Column("trade_date", Date, nullable=True),
    Column("counterparty_raw", String(300), nullable=True),
    Column("side", String(4), nullable=True),
    Column("commodity", String(40), nullable=True),
    Column("volume_value", QTY, nullable=True),
    Column("volume_unit", String(40), nullable=True),
    Column("price_value", MONEY, nullable=True),
    Column("price_unit", String(40), nullable=True),
    Column("delivery_start", Date, nullable=True),
    Column("delivery_end", Date, nullable=True),
    Column("linked_trade_uid", String(40), ForeignKey("trades.trade_uid"), nullable=True),
    UniqueConstraint("import_id", "source_record_id"),
    CheckConstraint(_in("side", vocab.SIDES, nullable=True), name="side"),
    Index("ix_source_records_trade_ref", "trade_ref"),
)

# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
reconciliation_runs = Table(
    "reconciliation_runs",
    metadata,
    Column("recon_run_id", String(40), primary_key=True),
    Column("reporting_month", String(7), nullable=False),
    Column("cutoff_utc", UTCDateTime(), nullable=False),
    Column("started_at_utc", UTCDateTime(), nullable=False),
    Column("completed_at_utc", UTCDateTime(), nullable=True),
    Column("status", String(20), nullable=False, server_default="RUNNING"),
    Column("code_version", String(80), nullable=False),
    Column("rules_version", String(80), nullable=False),
    Column("created_by", String(100), nullable=False),
    CheckConstraint(_in("status", vocab.RECON_RUN_STATUSES), name="status"),
    CheckConstraint(f"reporting_month LIKE '{MONTH_LIKE}'", name="month_fmt"),
)

reconciliation_items = Table(
    "reconciliation_items",
    metadata,
    Column("item_id", Integer, primary_key=True, autoincrement=True),
    Column("recon_run_id", String(40), ForeignKey("reconciliation_runs.recon_run_id"),
           nullable=False),
    Column("trade_uid", String(40), ForeignKey("trades.trade_uid"), nullable=True),
    Column("recon_status", String(30), nullable=False),
    Column("match_method", String(30), nullable=False),
    Column("match_quality", String(10), nullable=False),
    Column("differing_fields", Text, nullable=True),
    Column("severity", String(10), nullable=False, server_default="INFO"),
    Column("item_state", String(20), nullable=False, server_default="OPEN"),
    Column("owner", String(100), nullable=True),
    Column("reviewer", String(100), nullable=True),
    Column("opened_at_utc", UTCDateTime(), nullable=False),
    Column("resolved_at_utc", UTCDateTime(), nullable=True),
    Column("resolution_note", Text, nullable=True),
    Column("resolution_document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=True),
    CheckConstraint(_in("recon_status", vocab.RECONCILIATION_STATUSES), name="recon_status"),
    CheckConstraint(_in("item_state", vocab.RECON_ITEM_STATES), name="item_state"),
    CheckConstraint(_in("match_method", ("EXACT_ALIAS", "COMPOSITE", "MANUAL", "NONE")),
                    name="match_method"),
    CheckConstraint(_in("match_quality", ("EXACT", "HIGH", "LOW", "NONE")),
                    name="match_quality"),
    CheckConstraint(_in("severity", ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")),
                    name="severity"),
    CheckConstraint("item_state NOT IN ('RESOLVED', 'EXCLUDED_DISCLOSED') OR "
                    "(resolved_at_utc IS NOT NULL AND reviewer IS NOT NULL)",
                    name="resolution_reviewed"),
    Index("ix_reconciliation_items_recon_run_id", "recon_run_id"),
)

reconciliation_item_sources = Table(
    "reconciliation_item_sources",
    metadata,
    Column("link_id", Integer, primary_key=True, autoincrement=True),
    Column("item_id", Integer, ForeignKey("reconciliation_items.item_id"), nullable=False),
    Column("source_record_pk", Integer, ForeignKey("source_records.source_record_pk"),
           nullable=True),
    Column("document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=True),
    CheckConstraint("(source_record_pk IS NULL AND document_id IS NOT NULL) OR "
                    "(source_record_pk IS NOT NULL AND document_id IS NULL)",
                    name="exactly_one_source"),
    Index("ix_reconciliation_item_sources_item_id", "item_id"),
)

# ---------------------------------------------------------------------------
# Publication versions (structure only - publish/rollback NOT implemented in Phase 1)
# ---------------------------------------------------------------------------
publication_runs = Table(
    "publication_runs",
    metadata,
    Column("publication_id", String(20), primary_key=True),
    Column("reporting_month", String(7), nullable=False),
    Column("reporting_cutoff_utc", UTCDateTime(), nullable=False),
    Column("reporting_timezone", String(60), nullable=False),
    Column("recon_run_id", String(40), ForeignKey("reconciliation_runs.recon_run_id"),
           nullable=True),
    Column("source_inventory_digest", String(64), nullable=False),
    Column("code_version", String(80), nullable=False),
    Column("config_version", String(80), nullable=False),
    Column("status", String(20), nullable=False, server_default="CANDIDATE"),
    Column("totals_json", Text, nullable=True),
    Column("created_by", String(100), nullable=False),
    Column("created_at_utc", UTCDateTime(), nullable=False),
    Column("approved_by", String(100), nullable=True),
    Column("approved_at_utc", UTCDateTime(), nullable=True),
    Column("published_at_utc", UTCDateTime(), nullable=True),
    CheckConstraint(_in("status", vocab.PUBLICATION_STATUSES), name="status"),
    CheckConstraint(f"reporting_month LIKE '{MONTH_LIKE}'", name="month_fmt"),
    # Separation of duties: the creator of a candidate cannot be its approver.
    CheckConstraint("approved_by IS NULL OR LOWER(approved_by) <> LOWER(created_by)",
                    name="approver_sod"),
    CheckConstraint("status NOT IN ('APPROVED', 'PUBLISHED', 'SUPERSEDED') OR "
                    "(approved_by IS NOT NULL AND approved_at_utc IS NOT NULL)",
                    name="approved_has_approver"),
    CheckConstraint("status NOT IN ('PUBLISHED', 'SUPERSEDED') OR published_at_utc IS NOT NULL",
                    name="published_has_time"),
)

publication_sources = Table(
    "publication_sources",
    metadata,
    Column("publication_id", String(20), ForeignKey("publication_runs.publication_id"),
           nullable=False),
    Column("document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=False),
    Column("sha256", String(64), nullable=False),
    PrimaryKeyConstraint("publication_id", "document_id"),
)

published_trade_rows = Table(
    "published_trade_rows",
    metadata,
    Column("publication_id", String(20), ForeignKey("publication_runs.publication_id"),
           nullable=False),
    Column("trade_uid", String(40), ForeignKey("trades.trade_uid"), nullable=False),
    Column("leg", Integer, nullable=False),
    Column("segment", Integer, nullable=False),
    Column("term_id", Integer, ForeignKey("trade_terms.term_id"), nullable=False),
    Column("trader", String(100), nullable=True),
    Column("counterparty_raw_name", String(300), nullable=True),
    Column("commodity", String(40), nullable=True),
    Column("product", String(80), nullable=True),
    Column("physical_financial", String(10), nullable=False),
    Column("is_evergreen", Boolean, nullable=False),
    Column("side", String(4), nullable=False),
    Column("signed_quantity", QTY, nullable=True),
    Column("quantity_unit", String(40), nullable=True),
    Column("volume_basis", String(20), nullable=False),
    Column("price_status", String(20), nullable=False),
    Column("price_value", MONEY, nullable=True),
    Column("price_unit", String(40), nullable=True),
    Column("currency", String(3), nullable=True),
    Column("delivery_location", String(120), nullable=True),
    Column("delivery_start", Date, nullable=True),
    Column("delivery_end", Date, nullable=True),
    Column("recon_status", String(30), nullable=False),
    Column("exposure_review_status", String(30), nullable=False),
    PrimaryKeyConstraint("publication_id", "trade_uid", "leg", "segment"),
    CheckConstraint(_in("side", vocab.SIDES), name="side"),
    CheckConstraint(_in("recon_status", vocab.RECONCILIATION_STATUSES), name="recon_status"),
    CheckConstraint(_value_status_rule("price_status", "price_value"), name="price_value"),
)

active_publication = Table(
    "active_publication",
    metadata,
    Column("slot", String(10), primary_key=True),
    Column("publication_id", String(20), ForeignKey("publication_runs.publication_id"),
           nullable=True),
    Column("pointer_version", Integer, nullable=False, server_default="0"),
    Column("updated_at_utc", UTCDateTime(), nullable=True),
    Column("updated_by", String(100), nullable=True),
    CheckConstraint("slot = 'ACTIVE'", name="single_slot"),
)

# ---------------------------------------------------------------------------
# Audit (append-only by application policy; hash-chained for tamper evidence)
# ---------------------------------------------------------------------------
audit_events = Table(
    "audit_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("occurred_at_utc", UTCDateTime(), nullable=False),
    Column("actor", String(100), nullable=False),
    Column("approver", String(100), nullable=True),
    Column("action", String(60), nullable=False),
    Column("entity_type", String(40), nullable=False),
    Column("entity_id", String(120), nullable=False),
    Column("document_id", String(40), ForeignKey("source_documents.document_id"),
           nullable=True),
    Column("publication_id", String(20), ForeignKey("publication_runs.publication_id"),
           nullable=True),
    Column("reason", Text, nullable=True),
    Column("before_json", Text, nullable=True),
    Column("after_json", Text, nullable=True),
    Column("prev_event_hash", String(64), nullable=True),
    Column("event_hash", String(64), nullable=False),
    UniqueConstraint("event_hash"),
    CheckConstraint("approver IS NULL OR LOWER(approver) <> LOWER(actor)", name="approver_sod"),
    Index("ix_audit_events_entity", "entity_type", "entity_id"),
)

ALL_TABLES = tuple(metadata.sorted_tables)
