"""Initial Stage 1 schema: evidence, trade identity, terms, imports, reconciliation,
publication versions and audit events.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-31

This migration is FROZEN: it contains literal DDL rather than importing the live
schema module, so later edits to riskparse.db.schema require a new revision.
Only portable DDL is used (no engine-specific locks, triggers or permissions).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('reconciliation_runs',
    sa.Column('recon_run_id', sa.String(length=40), nullable=False),
    sa.Column('reporting_month', sa.String(length=7), nullable=False),
    sa.Column('cutoff_utc', sa.DateTime(), nullable=False),
    sa.Column('started_at_utc', sa.DateTime(), nullable=False),
    sa.Column('completed_at_utc', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='RUNNING', nullable=False),
    sa.Column('code_version', sa.String(length=80), nullable=False),
    sa.Column('rules_version', sa.String(length=80), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.CheckConstraint("reporting_month LIKE '____-__'", name=op.f('ck_reconciliation_runs_month_fmt')),
    sa.CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'FAILED')", name=op.f('ck_reconciliation_runs_status')),
    sa.PrimaryKeyConstraint('recon_run_id', name=op.f('pk_reconciliation_runs'))
    )
    op.create_table('source_documents',
    sa.Column('document_id', sa.String(length=40), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('file_type', sa.String(length=10), nullable=False),
    sa.Column('document_category', sa.String(length=30), nullable=True),
    sa.Column('original_filename', sa.String(length=400), nullable=False),
    sa.Column('original_path', sa.String(length=1000), nullable=False),
    sa.Column('archive_uri', sa.String(length=1000), nullable=True),
    sa.Column('source_channel', sa.String(length=40), nullable=False),
    sa.Column('sender', sa.String(length=320), nullable=True),
    sa.Column('received_at_utc', sa.DateTime(), nullable=True),
    sa.Column('recorded_at_utc', sa.DateTime(), nullable=False),
    sa.Column('file_modified_at_utc', sa.DateTime(), nullable=True),
    sa.Column('content_status', sa.String(length=20), server_default='REGISTERED', nullable=False),
    sa.Column('parent_document_id', sa.String(length=40), nullable=True),
    sa.Column('revision_of_document_id', sa.String(length=40), nullable=True),
    sa.Column('is_synthetic', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('evidence_note', sa.Text(), nullable=True),
    sa.CheckConstraint("(document_category IS NULL OR document_category IN ('CONFIRMATION', 'AMENDMENT', 'CANCELLATION', 'TERMINATION', 'EVERGREEN_AGREEMENT', 'MASTER_AGREEMENT', 'EMAIL', 'EGISTIX_EXPORT', 'TRADER_SHEET', 'OTHER'))", name=op.f('ck_source_documents_category')),
    sa.CheckConstraint("content_status IN ('REGISTERED', 'PENDING_REVIEW', 'ACCEPTED', 'REJECTED', 'REMOVED', 'EXCEPTED')", name=op.f('ck_source_documents_content_status')),
    sa.CheckConstraint("file_type IN ('PDF', 'XLSX', 'XLS', 'XLSM', 'CSV', 'EML', 'MSG', 'DOCX', 'TIFF', 'OTHER')", name=op.f('ck_source_documents_file_type')),
    sa.CheckConstraint("sha256 LIKE '________________________________________________________________'", name=op.f('ck_source_documents_sha256_shape')),
    sa.CheckConstraint('parent_document_id IS NULL OR parent_document_id <> document_id', name=op.f('ck_source_documents_parent_not_self')),
    sa.CheckConstraint('revision_of_document_id IS NULL OR revision_of_document_id <> document_id', name=op.f('ck_source_documents_revision_not_self')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_source_documents_size_nonneg')),
    sa.ForeignKeyConstraint(['parent_document_id'], ['source_documents.document_id'], name=op.f('fk_source_documents_parent_document_id')),
    sa.ForeignKeyConstraint(['revision_of_document_id'], ['source_documents.document_id'], name=op.f('fk_source_documents_revision_of_document_id')),
    sa.PrimaryKeyConstraint('document_id', name=op.f('pk_source_documents')),
    sa.UniqueConstraint('sha256', name=op.f('uq_source_documents_sha256'))
    )
    op.create_table('trades',
    sa.Column('trade_uid', sa.String(length=40), nullable=False),
    sa.Column('trader', sa.String(length=100), nullable=True),
    sa.Column('book', sa.String(length=100), nullable=True),
    sa.Column('counterparty_id', sa.String(length=60), nullable=True),
    sa.Column('counterparty_raw_name', sa.String(length=300), nullable=True),
    sa.Column('commodity', sa.String(length=40), nullable=True),
    sa.Column('product', sa.String(length=80), nullable=True),
    sa.Column('physical_financial', sa.String(length=10), server_default='UNKNOWN', nullable=False),
    sa.Column('is_evergreen', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('lifecycle_status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=True),
    sa.Column('parent_trade_uid', sa.String(length=40), nullable=True),
    sa.Column('is_synthetic', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at_utc', sa.DateTime(), nullable=False),
    sa.CheckConstraint("lifecycle_status IN ('ACTIVE', 'AMENDED', 'CANCELLED', 'TERMINATED', 'EXPIRED', 'UNRESOLVED')", name=op.f('ck_trades_lifecycle')),
    sa.CheckConstraint("physical_financial IN ('PHYSICAL', 'FINANCIAL', 'UNKNOWN')", name=op.f('ck_trades_phys_fin')),
    sa.CheckConstraint('parent_trade_uid IS NULL OR parent_trade_uid <> trade_uid', name=op.f('ck_trades_parent_not_self')),
    sa.ForeignKeyConstraint(['parent_trade_uid'], ['trades.trade_uid'], name=op.f('fk_trades_parent_trade_uid')),
    sa.PrimaryKeyConstraint('trade_uid', name=op.f('pk_trades'))
    )
    op.create_table('document_observations',
    sa.Column('observation_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.String(length=40), nullable=False),
    sa.Column('observed_path', sa.String(length=1000), nullable=False),
    sa.Column('observed_filename', sa.String(length=400), nullable=False),
    sa.Column('observed_at_utc', sa.DateTime(), nullable=False),
    sa.Column('file_modified_at_utc', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['source_documents.document_id'], name=op.f('fk_document_observations_document_id')),
    sa.PrimaryKeyConstraint('observation_id', name=op.f('pk_document_observations'))
    )
    op.create_index('ix_document_observations_document_id', 'document_observations', ['document_id'], unique=False)
    op.create_table('publication_runs',
    sa.Column('publication_id', sa.String(length=20), nullable=False),
    sa.Column('reporting_month', sa.String(length=7), nullable=False),
    sa.Column('reporting_cutoff_utc', sa.DateTime(), nullable=False),
    sa.Column('reporting_timezone', sa.String(length=60), nullable=False),
    sa.Column('recon_run_id', sa.String(length=40), nullable=True),
    sa.Column('source_inventory_digest', sa.String(length=64), nullable=False),
    sa.Column('code_version', sa.String(length=80), nullable=False),
    sa.Column('config_version', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='CANDIDATE', nullable=False),
    sa.Column('totals_json', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.Column('created_at_utc', sa.DateTime(), nullable=False),
    sa.Column('approved_by', sa.String(length=100), nullable=True),
    sa.Column('approved_at_utc', sa.DateTime(), nullable=True),
    sa.Column('published_at_utc', sa.DateTime(), nullable=True),
    sa.CheckConstraint("reporting_month LIKE '____-__'", name=op.f('ck_publication_runs_month_fmt')),
    sa.CheckConstraint("status IN ('CANDIDATE', 'VALIDATION_FAILED', 'VALIDATED', 'HELD', 'APPROVED', 'PUBLISHED', 'SUPERSEDED')", name=op.f('ck_publication_runs_status')),
    sa.CheckConstraint("status NOT IN ('APPROVED', 'PUBLISHED', 'SUPERSEDED') OR (approved_by IS NOT NULL AND approved_at_utc IS NOT NULL)", name=op.f('ck_publication_runs_approved_has_approver')),
    sa.CheckConstraint("status NOT IN ('PUBLISHED', 'SUPERSEDED') OR published_at_utc IS NOT NULL", name=op.f('ck_publication_runs_published_has_time')),
    sa.CheckConstraint('approved_by IS NULL OR approved_by <> created_by', name=op.f('ck_publication_runs_approver_sod')),
    sa.ForeignKeyConstraint(['recon_run_id'], ['reconciliation_runs.recon_run_id'], name=op.f('fk_publication_runs_recon_run_id')),
    sa.PrimaryKeyConstraint('publication_id', name=op.f('pk_publication_runs'))
    )
    op.create_table('reconciliation_items',
    sa.Column('item_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('recon_run_id', sa.String(length=40), nullable=False),
    sa.Column('trade_uid', sa.String(length=40), nullable=True),
    sa.Column('recon_status', sa.String(length=30), nullable=False),
    sa.Column('match_method', sa.String(length=30), nullable=False),
    sa.Column('match_quality', sa.String(length=10), nullable=False),
    sa.Column('differing_fields', sa.Text(), nullable=True),
    sa.Column('severity', sa.String(length=10), server_default='INFO', nullable=False),
    sa.Column('item_state', sa.String(length=20), server_default='OPEN', nullable=False),
    sa.Column('owner', sa.String(length=100), nullable=True),
    sa.Column('reviewer', sa.String(length=100), nullable=True),
    sa.Column('opened_at_utc', sa.DateTime(), nullable=False),
    sa.Column('resolved_at_utc', sa.DateTime(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolution_document_id', sa.String(length=40), nullable=True),
    sa.CheckConstraint("item_state IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'EXCLUDED_DISCLOSED')", name=op.f('ck_reconciliation_items_item_state')),
    sa.CheckConstraint("item_state NOT IN ('RESOLVED', 'EXCLUDED_DISCLOSED') OR (resolved_at_utc IS NOT NULL AND reviewer IS NOT NULL)", name=op.f('ck_reconciliation_items_resolution_reviewed')),
    sa.CheckConstraint("match_method IN ('EXACT_ALIAS', 'COMPOSITE', 'MANUAL', 'NONE')", name=op.f('ck_reconciliation_items_match_method')),
    sa.CheckConstraint("match_quality IN ('EXACT', 'HIGH', 'LOW', 'NONE')", name=op.f('ck_reconciliation_items_match_quality')),
    sa.CheckConstraint("recon_status IN ('MATCHED', 'MATCHED_BREAK', 'UNCONFIRMED', 'NOT_BOOKED', 'EGISTIX_NOT_APPLICABLE', 'DUPLICATE_POSSIBLE', 'CANCELLED_OR_AMENDED', 'UNRESOLVED')", name=op.f('ck_reconciliation_items_recon_status')),
    sa.CheckConstraint("severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')", name=op.f('ck_reconciliation_items_severity')),
    sa.ForeignKeyConstraint(['recon_run_id'], ['reconciliation_runs.recon_run_id'], name=op.f('fk_reconciliation_items_recon_run_id')),
    sa.ForeignKeyConstraint(['resolution_document_id'], ['source_documents.document_id'], name=op.f('fk_reconciliation_items_resolution_document_id')),
    sa.ForeignKeyConstraint(['trade_uid'], ['trades.trade_uid'], name=op.f('fk_reconciliation_items_trade_uid')),
    sa.PrimaryKeyConstraint('item_id', name=op.f('pk_reconciliation_items'))
    )
    op.create_index('ix_reconciliation_items_recon_run_id', 'reconciliation_items', ['recon_run_id'], unique=False)
    op.create_table('source_imports',
    sa.Column('import_id', sa.String(length=40), nullable=False),
    sa.Column('source_system', sa.String(length=20), nullable=False),
    sa.Column('source_owner', sa.String(length=100), nullable=False),
    sa.Column('source_name', sa.String(length=200), nullable=False),
    sa.Column('document_id', sa.String(length=40), nullable=False),
    sa.Column('reporting_month', sa.String(length=7), nullable=False),
    sa.Column('source_cutoff_utc', sa.DateTime(), nullable=False),
    sa.Column('imported_at_utc', sa.DateTime(), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='STAGED', nullable=False),
    sa.Column('parser_version', sa.String(length=60), nullable=False),
    sa.CheckConstraint("reporting_month LIKE '____-__'", name=op.f('ck_source_imports_month_fmt')),
    sa.CheckConstraint("source_system IN ('CONTRACT', 'EGISTIX', 'TRADER_SHEET', 'BROKER', 'EXCHANGE', 'OTHER')", name=op.f('ck_source_imports_source_system')),
    sa.CheckConstraint("status IN ('STAGED', 'LOADED', 'FAILED', 'SUPERSEDED')", name=op.f('ck_source_imports_status')),
    sa.CheckConstraint('row_count IS NULL OR row_count >= 0', name=op.f('ck_source_imports_row_count_nonneg')),
    sa.ForeignKeyConstraint(['document_id'], ['source_documents.document_id'], name=op.f('fk_source_imports_document_id')),
    sa.PrimaryKeyConstraint('import_id', name=op.f('pk_source_imports')),
    sa.UniqueConstraint('document_id', 'source_system', 'parser_version', name=op.f('uq_source_imports_document_id_source_system_parser_version'))
    )
    op.create_table('trade_aliases',
    sa.Column('alias_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('trade_uid', sa.String(length=40), nullable=False),
    sa.Column('alias_system', sa.String(length=20), nullable=False),
    sa.Column('alias_value', sa.String(length=120), nullable=False),
    sa.Column('created_at_utc', sa.DateTime(), nullable=False),
    sa.CheckConstraint("alias_system IN ('EGISTIX', 'COUNTERPARTY', 'TRADER', 'BROKER', 'EXCHANGE', 'INTERNAL')", name=op.f('ck_trade_aliases_alias_system')),
    sa.ForeignKeyConstraint(['trade_uid'], ['trades.trade_uid'], name=op.f('fk_trade_aliases_trade_uid')),
    sa.PrimaryKeyConstraint('alias_id', name=op.f('pk_trade_aliases')),
    sa.UniqueConstraint('alias_system', 'alias_value', name=op.f('uq_trade_aliases_alias_system_alias_value'))
    )
    op.create_index('ix_trade_aliases_trade_uid', 'trade_aliases', ['trade_uid'], unique=False)
    op.create_table('trade_documents',
    sa.Column('trade_uid', sa.String(length=40), nullable=False),
    sa.Column('document_id', sa.String(length=40), nullable=False),
    sa.Column('relationship', sa.String(length=30), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('supersession_scope', sa.Text(), nullable=True),
    sa.Column('created_at_utc', sa.DateTime(), nullable=False),
    sa.CheckConstraint("relationship IN ('CONFIRMATION', 'AMENDMENT', 'CANCELLATION', 'TERMINATION', 'EVERGREEN_AGREEMENT', 'SUPPORTING_EMAIL', 'OTHER')", name=op.f('ck_trade_documents_relationship')),
    sa.ForeignKeyConstraint(['document_id'], ['source_documents.document_id'], name=op.f('fk_trade_documents_document_id')),
    sa.ForeignKeyConstraint(['trade_uid'], ['trades.trade_uid'], name=op.f('fk_trade_documents_trade_uid')),
    sa.PrimaryKeyConstraint('trade_uid', 'document_id', 'relationship', name=op.f('pk_trade_documents'))
    )
    op.create_table('trade_terms',
    sa.Column('term_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('trade_uid', sa.String(length=40), nullable=False),
    sa.Column('leg', sa.Integer(), server_default='1', nullable=False),
    sa.Column('term_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('price_type', sa.String(length=20), nullable=False),
    sa.Column('price_status', sa.String(length=20), nullable=False),
    sa.Column('price_value', sa.Numeric(precision=28, scale=10), nullable=True),
    sa.Column('price_raw', sa.String(length=300), nullable=True),
    sa.Column('price_unit', sa.String(length=40), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('index_name', sa.String(length=120), nullable=True),
    sa.Column('price_differential', sa.Numeric(precision=28, scale=10), nullable=True),
    sa.Column('volume_status', sa.String(length=20), nullable=False),
    sa.Column('volume_value', sa.Numeric(precision=28, scale=10), nullable=True),
    sa.Column('volume_raw', sa.String(length=300), nullable=True),
    sa.Column('volume_unit', sa.String(length=40), nullable=True),
    sa.Column('volume_basis', sa.String(length=20), server_default='UNKNOWN', nullable=False),
    sa.Column('delivery_location', sa.String(length=120), nullable=True),
    sa.Column('delivery_timezone', sa.String(length=60), nullable=True),
    sa.Column('delivery_start', sa.Date(), nullable=True),
    sa.Column('delivery_end', sa.Date(), nullable=True),
    sa.Column('valid_from_utc', sa.DateTime(), nullable=True),
    sa.Column('valid_to_utc', sa.DateTime(), nullable=True),
    sa.Column('evidence_document_id', sa.String(length=40), nullable=True),
    sa.Column('evidence_location', sa.String(length=300), nullable=True),
    sa.Column('review_status', sa.String(length=20), server_default='UNREVIEWED', nullable=False),
    sa.Column('created_at_utc', sa.DateTime(), nullable=False),
    sa.CheckConstraint("((price_status = 'PRESENT' AND price_value IS NOT NULL) OR (price_status <> 'PRESENT' AND price_value IS NULL))", name=op.f('ck_trade_terms_price_value')),
    sa.CheckConstraint("((volume_status = 'PRESENT' AND volume_value IS NOT NULL) OR (volume_status <> 'PRESENT' AND volume_value IS NULL))", name=op.f('ck_trade_terms_volume_value')),
    sa.CheckConstraint("price_status IN ('PRESENT', 'MISSING', 'PENDING', 'NOT_APPLICABLE', 'UNRESOLVED')", name=op.f('ck_trade_terms_price_status')),
    sa.CheckConstraint("price_type IN ('FIXED', 'INDEX', 'INDEX_PLUS_DIFF', 'FORMULA', 'UNRESOLVED')", name=op.f('ck_trade_terms_price_type')),
    sa.CheckConstraint("review_status IN ('UNREVIEWED', 'EXTRACTED', 'VERIFIED', 'DISPUTED', 'REJECTED')", name=op.f('ck_trade_terms_review_status')),
    sa.CheckConstraint("side IN ('BUY', 'SELL')", name=op.f('ck_trade_terms_side')),
    sa.CheckConstraint("volume_basis IN ('PER_HOUR', 'PER_DAY', 'PER_MONTH', 'TOTAL', 'OTHER', 'UNKNOWN')", name=op.f('ck_trade_terms_volume_basis')),
    sa.CheckConstraint("volume_status IN ('PRESENT', 'MISSING', 'PENDING', 'NOT_APPLICABLE', 'UNRESOLVED')", name=op.f('ck_trade_terms_volume_status')),
    sa.CheckConstraint('delivery_start IS NULL OR delivery_end IS NULL OR delivery_end >= delivery_start', name=op.f('ck_trade_terms_delivery_order')),
    sa.CheckConstraint('leg >= 1 AND term_version >= 1', name=op.f('ck_trade_terms_leg_version_pos')),
    sa.CheckConstraint('valid_from_utc IS NULL OR valid_to_utc IS NULL OR valid_to_utc > valid_from_utc', name=op.f('ck_trade_terms_validity_order')),
    sa.CheckConstraint('volume_value IS NULL OR volume_value >= 0', name=op.f('ck_trade_terms_volume_nonneg')),
    sa.ForeignKeyConstraint(['evidence_document_id'], ['source_documents.document_id'], name=op.f('fk_trade_terms_evidence_document_id')),
    sa.ForeignKeyConstraint(['trade_uid'], ['trades.trade_uid'], name=op.f('fk_trade_terms_trade_uid')),
    sa.PrimaryKeyConstraint('term_id', name=op.f('pk_trade_terms')),
    sa.UniqueConstraint('trade_uid', 'leg', 'term_version', name=op.f('uq_trade_terms_trade_uid_leg_term_version'))
    )
    op.create_table('active_publication',
    sa.Column('slot', sa.String(length=10), nullable=False),
    sa.Column('publication_id', sa.String(length=20), nullable=True),
    sa.Column('pointer_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_at_utc', sa.DateTime(), nullable=True),
    sa.Column('updated_by', sa.String(length=100), nullable=True),
    sa.CheckConstraint("slot = 'ACTIVE'", name=op.f('ck_active_publication_single_slot')),
    sa.ForeignKeyConstraint(['publication_id'], ['publication_runs.publication_id'], name=op.f('fk_active_publication_publication_id')),
    sa.PrimaryKeyConstraint('slot', name=op.f('pk_active_publication'))
    )
    # Single active-publication pointer row; no publication is active until an
    # approved, controlled publish (not implemented in Phase 1).
    op.execute(
        "INSERT INTO active_publication (slot, publication_id, pointer_version) "
        "VALUES ('ACTIVE', NULL, 0)"
    )
    op.create_table('audit_events',
    sa.Column('event_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('occurred_at_utc', sa.DateTime(), nullable=False),
    sa.Column('actor', sa.String(length=100), nullable=False),
    sa.Column('approver', sa.String(length=100), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.String(length=120), nullable=False),
    sa.Column('document_id', sa.String(length=40), nullable=True),
    sa.Column('publication_id', sa.String(length=20), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('before_json', sa.Text(), nullable=True),
    sa.Column('after_json', sa.Text(), nullable=True),
    sa.Column('prev_event_hash', sa.String(length=64), nullable=True),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.CheckConstraint('approver IS NULL OR approver <> actor', name=op.f('ck_audit_events_approver_sod')),
    sa.ForeignKeyConstraint(['document_id'], ['source_documents.document_id'], name=op.f('fk_audit_events_document_id')),
    sa.ForeignKeyConstraint(['publication_id'], ['publication_runs.publication_id'], name=op.f('fk_audit_events_publication_id')),
    sa.PrimaryKeyConstraint('event_id', name=op.f('pk_audit_events')),
    sa.UniqueConstraint('event_hash', name=op.f('uq_audit_events_event_hash'))
    )
    op.create_index('ix_audit_events_entity', 'audit_events', ['entity_type', 'entity_id'], unique=False)
    op.create_table('publication_sources',
    sa.Column('publication_id', sa.String(length=20), nullable=False),
    sa.Column('document_id', sa.String(length=40), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['source_documents.document_id'], name=op.f('fk_publication_sources_document_id')),
    sa.ForeignKeyConstraint(['publication_id'], ['publication_runs.publication_id'], name=op.f('fk_publication_sources_publication_id')),
    sa.PrimaryKeyConstraint('publication_id', 'document_id', name=op.f('pk_publication_sources'))
    )
    op.create_table('published_trade_rows',
    sa.Column('publication_id', sa.String(length=20), nullable=False),
    sa.Column('trade_uid', sa.String(length=40), nullable=False),
    sa.Column('leg', sa.Integer(), nullable=False),
    sa.Column('segment', sa.Integer(), nullable=False),
    sa.Column('term_id', sa.Integer(), nullable=False),
    sa.Column('trader', sa.String(length=100), nullable=True),
    sa.Column('counterparty_raw_name', sa.String(length=300), nullable=True),
    sa.Column('commodity', sa.String(length=40), nullable=True),
    sa.Column('product', sa.String(length=80), nullable=True),
    sa.Column('physical_financial', sa.String(length=10), nullable=False),
    sa.Column('is_evergreen', sa.Boolean(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('signed_quantity', sa.Numeric(precision=28, scale=10), nullable=True),
    sa.Column('quantity_unit', sa.String(length=40), nullable=True),
    sa.Column('volume_basis', sa.String(length=20), nullable=False),
    sa.Column('price_status', sa.String(length=20), nullable=False),
    sa.Column('price_value', sa.Numeric(precision=28, scale=10), nullable=True),
    sa.Column('price_unit', sa.String(length=40), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('delivery_location', sa.String(length=120), nullable=True),
    sa.Column('delivery_start', sa.Date(), nullable=True),
    sa.Column('delivery_end', sa.Date(), nullable=True),
    sa.Column('recon_status', sa.String(length=30), nullable=False),
    sa.Column('exposure_review_status', sa.String(length=30), nullable=False),
    sa.CheckConstraint("((price_status = 'PRESENT' AND price_value IS NOT NULL) OR (price_status <> 'PRESENT' AND price_value IS NULL))", name=op.f('ck_published_trade_rows_price_value')),
    sa.CheckConstraint("recon_status IN ('MATCHED', 'MATCHED_BREAK', 'UNCONFIRMED', 'NOT_BOOKED', 'EGISTIX_NOT_APPLICABLE', 'DUPLICATE_POSSIBLE', 'CANCELLED_OR_AMENDED', 'UNRESOLVED')", name=op.f('ck_published_trade_rows_recon_status')),
    sa.CheckConstraint("side IN ('BUY', 'SELL')", name=op.f('ck_published_trade_rows_side')),
    sa.ForeignKeyConstraint(['publication_id'], ['publication_runs.publication_id'], name=op.f('fk_published_trade_rows_publication_id')),
    sa.ForeignKeyConstraint(['term_id'], ['trade_terms.term_id'], name=op.f('fk_published_trade_rows_term_id')),
    sa.ForeignKeyConstraint(['trade_uid'], ['trades.trade_uid'], name=op.f('fk_published_trade_rows_trade_uid')),
    sa.PrimaryKeyConstraint('publication_id', 'trade_uid', 'leg', 'segment', name=op.f('pk_published_trade_rows'))
    )
    op.create_table('source_records',
    sa.Column('source_record_pk', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('import_id', sa.String(length=40), nullable=False),
    sa.Column('source_record_id', sa.String(length=120), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=True),
    sa.Column('raw_payload', sa.Text(), nullable=False),
    sa.Column('raw_payload_sha256', sa.String(length=64), nullable=False),
    sa.Column('trade_ref', sa.String(length=120), nullable=True),
    sa.Column('trade_date', sa.Date(), nullable=True),
    sa.Column('counterparty_raw', sa.String(length=300), nullable=True),
    sa.Column('side', sa.String(length=4), nullable=True),
    sa.Column('commodity', sa.String(length=40), nullable=True),
    sa.Column('volume_value', sa.Numeric(precision=28, scale=10), nullable=True),
    sa.Column('volume_unit', sa.String(length=40), nullable=True),
    sa.Column('price_value', sa.Numeric(precision=28, scale=10), nullable=True),
    sa.Column('price_unit', sa.String(length=40), nullable=True),
    sa.Column('delivery_start', sa.Date(), nullable=True),
    sa.Column('delivery_end', sa.Date(), nullable=True),
    sa.Column('linked_trade_uid', sa.String(length=40), nullable=True),
    sa.CheckConstraint("(side IS NULL OR side IN ('BUY', 'SELL'))", name=op.f('ck_source_records_side')),
    sa.ForeignKeyConstraint(['import_id'], ['source_imports.import_id'], name=op.f('fk_source_records_import_id')),
    sa.ForeignKeyConstraint(['linked_trade_uid'], ['trades.trade_uid'], name=op.f('fk_source_records_linked_trade_uid')),
    sa.PrimaryKeyConstraint('source_record_pk', name=op.f('pk_source_records')),
    sa.UniqueConstraint('import_id', 'source_record_id', name=op.f('uq_source_records_import_id_source_record_id'))
    )
    op.create_index('ix_source_records_trade_ref', 'source_records', ['trade_ref'], unique=False)
    op.create_table('reconciliation_item_sources',
    sa.Column('link_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('source_record_pk', sa.Integer(), nullable=True),
    sa.Column('document_id', sa.String(length=40), nullable=True),
    sa.CheckConstraint('(source_record_pk IS NULL AND document_id IS NOT NULL) OR (source_record_pk IS NOT NULL AND document_id IS NULL)', name=op.f('ck_reconciliation_item_sources_exactly_one_source')),
    sa.ForeignKeyConstraint(['document_id'], ['source_documents.document_id'], name=op.f('fk_reconciliation_item_sources_document_id')),
    sa.ForeignKeyConstraint(['item_id'], ['reconciliation_items.item_id'], name=op.f('fk_reconciliation_item_sources_item_id')),
    sa.ForeignKeyConstraint(['source_record_pk'], ['source_records.source_record_pk'], name=op.f('fk_reconciliation_item_sources_source_record_pk')),
    sa.PrimaryKeyConstraint('link_id', name=op.f('pk_reconciliation_item_sources'))
    )
    op.create_index('ix_reconciliation_item_sources_item_id', 'reconciliation_item_sources', ['item_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_reconciliation_item_sources_item_id', table_name='reconciliation_item_sources')
    op.drop_table('reconciliation_item_sources')
    op.drop_index('ix_source_records_trade_ref', table_name='source_records')
    op.drop_table('source_records')
    op.drop_table('published_trade_rows')
    op.drop_table('publication_sources')
    op.drop_index('ix_audit_events_entity', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_table('active_publication')
    op.drop_table('trade_terms')
    op.drop_table('trade_documents')
    op.drop_index('ix_trade_aliases_trade_uid', table_name='trade_aliases')
    op.drop_table('trade_aliases')
    op.drop_table('source_imports')
    op.drop_index('ix_reconciliation_items_recon_run_id', table_name='reconciliation_items')
    op.drop_table('reconciliation_items')
    op.drop_table('publication_runs')
    op.drop_index('ix_document_observations_document_id', table_name='document_observations')
    op.drop_table('document_observations')
    op.drop_table('trades')
    op.drop_table('source_documents')
    op.drop_table('reconciliation_runs')
