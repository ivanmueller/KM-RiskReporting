"""Text extraction tables: document_extractions and document_segments.

Revision ID: 0003_document_extraction
Revises: 0002_sod_case_and_zip
Create Date: 2026-10-31

Stores the raw, location-tagged text produced by riskparse.extract (no economic
interpretation). FROZEN: literal DDL only; portable types and constraints.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_document_extraction"
down_revision = "0002_sod_case_and_zip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_extractions",
        sa.Column("extraction_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.String(length=40), nullable=False),
        sa.Column("extractor", sa.String(length=40), nullable=False),
        sa.Column("extractor_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("extracted_by", sa.String(length=100), nullable=False),
        sa.Column("extracted_at_utc", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('TEXT_EXTRACTED', 'PARTIAL_TEXT', 'NEEDS_OCR', 'ENCRYPTED', "
            "'UNSUPPORTED', 'FAILED')", name=op.f("ck_document_extractions_status")),
        sa.CheckConstraint("segment_count >= 0",
                           name=op.f("ck_document_extractions_segment_count_nonneg")),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.document_id"],
                                name=op.f("fk_document_extractions_document_id")),
        sa.PrimaryKeyConstraint("extraction_id", name=op.f("pk_document_extractions")),
        sa.UniqueConstraint("document_id", "extractor", "extractor_version",
                            name=op.f("uq_document_extractions_doc_version")),
    )
    op.create_table(
        "document_segments",
        sa.Column("extraction_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.CheckConstraint("seq >= 1", name=op.f("ck_document_segments_seq_pos")),
        sa.ForeignKeyConstraint(["extraction_id"], ["document_extractions.extraction_id"],
                                name=op.f("fk_document_segments_extraction_id")),
        sa.PrimaryKeyConstraint("extraction_id", "seq", name=op.f("pk_document_segments")),
    )


def downgrade() -> None:
    op.drop_table("document_segments")
    op.drop_table("document_extractions")
