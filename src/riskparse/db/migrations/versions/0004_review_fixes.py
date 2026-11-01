"""Fixes from the first Codex review.

Revision ID: 0004_review_fixes
Revises: 0003_document_extraction
Create Date: 2026-11-01

* ``source_imports.unparsed_json``: persist field-level parse exceptions so an idempotent
  retry still reports them (Codex #15).
* ``document_observations.container_document_id``: each sighting of a file records the
  archive or email it came from (Codex: repeated-member provenance).
* Trimmed-identity CHECKs on ``publication_runs`` and ``audit_events``, so ``'pat '``
  cannot pass the case-insensitive separation-of-duties check as a different person
  (Codex #4). ``LTRIM``/``RTRIM`` are portable across SQLite, SQL Server and PostgreSQL.

FROZEN: literal DDL only. SQLite applies these through batch table rebuilds, which
``riskparse.db.migrate.run_guarded`` wraps with foreign-key checks.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_review_fixes"
down_revision = "0003_document_extraction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_imports") as batch:
        batch.add_column(sa.Column("unparsed_json", sa.Text(), nullable=True))
    with op.batch_alter_table("document_observations") as batch:
        batch.add_column(sa.Column("container_document_id", sa.String(length=40),
                                   nullable=True))
        batch.create_foreign_key(op.f("fk_document_observations_container_document_id"),
                                 "source_documents", ["container_document_id"],
                                 ["document_id"])
    with op.batch_alter_table("publication_runs") as batch:
        batch.create_check_constraint(op.f("ck_publication_runs_created_by_trimmed"),
                                      "created_by = LTRIM(RTRIM(created_by))")
        batch.create_check_constraint(op.f("ck_publication_runs_approved_by_trimmed"),
                                      "approved_by IS NULL OR approved_by = "
                                      "LTRIM(RTRIM(approved_by))")
    with op.batch_alter_table("audit_events") as batch:
        batch.create_check_constraint(op.f("ck_audit_events_actor_trimmed"),
                                      "actor = LTRIM(RTRIM(actor))")
        batch.create_check_constraint(op.f("ck_audit_events_approver_trimmed"),
                                      "approver IS NULL OR approver = LTRIM(RTRIM(approver))")


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint(op.f("ck_audit_events_approver_trimmed"), type_="check")
        batch.drop_constraint(op.f("ck_audit_events_actor_trimmed"), type_="check")
    with op.batch_alter_table("publication_runs") as batch:
        batch.drop_constraint(op.f("ck_publication_runs_approved_by_trimmed"), type_="check")
        batch.drop_constraint(op.f("ck_publication_runs_created_by_trimmed"), type_="check")
    with op.batch_alter_table("document_observations") as batch:
        batch.drop_constraint(op.f("fk_document_observations_container_document_id"),
                              type_="foreignkey")
        batch.drop_column("container_document_id")
    with op.batch_alter_table("source_imports") as batch:
        batch.drop_column("unparsed_json")
