"""Case-insensitive separation-of-duties checks; allow ZIP as a source file type.

Revision ID: 0002_sod_case_and_zip
Revises: 0001_initial
Create Date: 2026-10-31

* ``publication_runs.ck_publication_runs_approver_sod`` and
  ``audit_events.ck_audit_events_approver_sod`` now compare with ``LOWER()`` so that
  ``Pat`` cannot approve work created by ``pat`` (handoff defect D-4).
* ``source_documents.ck_source_documents_file_type`` gains ``'ZIP'`` so a received
  archive can itself be registered as evidence (roadmap B8).

FROZEN: literal DDL only. ``LOWER()`` and ``IN (...)`` are portable across SQLite,
SQL Server and PostgreSQL. On SQLite, batch mode rebuilds each table; the tests
check that every other constraint survives the rebuild.
"""

from __future__ import annotations

from alembic import op

revision = "0002_sod_case_and_zip"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_TYPES_OLD = "'PDF', 'XLSX', 'XLS', 'XLSM', 'CSV', 'EML', 'MSG', 'DOCX', 'TIFF', 'OTHER'"
_TYPES_NEW = "'PDF', 'XLSX', 'XLS', 'XLSM', 'CSV', 'EML', 'MSG', 'DOCX', 'TIFF', 'ZIP', 'OTHER'"


def _replace_check(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(name), type_="check")
        batch.create_check_constraint(op.f(name), expression)


def upgrade() -> None:
    _replace_check("publication_runs", "ck_publication_runs_approver_sod",
                   "approved_by IS NULL OR LOWER(approved_by) <> LOWER(created_by)")
    _replace_check("audit_events", "ck_audit_events_approver_sod",
                   "approver IS NULL OR LOWER(approver) <> LOWER(actor)")
    _replace_check("source_documents", "ck_source_documents_file_type",
                   f"file_type IN ({_TYPES_NEW})")


def downgrade() -> None:
    op.execute("UPDATE source_documents SET file_type = 'OTHER' WHERE file_type = 'ZIP'")
    _replace_check("source_documents", "ck_source_documents_file_type",
                   f"file_type IN ({_TYPES_OLD})")
    _replace_check("audit_events", "ck_audit_events_approver_sod",
                   "approver IS NULL OR approver <> actor")
    _replace_check("publication_runs", "ck_publication_runs_approver_sod",
                   "approved_by IS NULL OR approved_by <> created_by")
