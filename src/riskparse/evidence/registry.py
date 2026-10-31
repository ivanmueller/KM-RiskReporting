"""Document registration: assign identity, detect duplicates and revisions.

Rules (game plan 4.1, 9.5, 11):

* ``document_id`` is assigned at ingestion and is never derived from the filename.
* Identical bytes (same SHA-256) => the *same* document. A new observation (path /
  filename / time seen) is recorded; no new document is created.
* Different bytes with a previously seen filename => a new document flagged as a
  **revision** of the most recent prior one. Old bytes are never overwritten.
* Similar *economic* content with different bytes is not detected here; economic
  duplicate detection is a separate reconciliation check.
* Every outcome writes a hash-chained audit event.
* Registration never opens or parses document content and never approves anything.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, func, insert, select

from riskparse import audit, vocab
from riskparse.db.schema import document_observations, source_documents
from riskparse.evidence import archive
from riskparse.evidence.hashing import fingerprint

NEW = "NEW"
DUPLICATE = "DUPLICATE"
REVISION = "REVISION"


@dataclass(frozen=True)
class RegistrationResult:
    outcome: str
    document_id: str
    sha256: str
    revision_of_document_id: str | None = None
    archive_uri: str | None = None


def new_document_id() -> str:
    return f"DOC-{uuid.uuid4().hex.upper()}"


def register_document(
    conn: Connection,
    path: str | os.PathLike[str],
    *,
    actor: str,
    source_channel: str,
    category: str | None = None,
    sender: str | None = None,
    received_at: datetime | None = None,
    parent_document_id: str | None = None,
    archive_root: str | os.PathLike[str] | None = None,
    is_synthetic: bool = False,
    observed_path: str | None = None,
) -> RegistrationResult:
    """Register the file at ``path``. Must be called inside a transaction."""
    if category is not None and category not in vocab.DOCUMENT_CATEGORIES:
        raise ValueError(f"unknown document category {category!r}")
    fp = fingerprint(path)
    now = datetime.now(UTC)
    shown_path = observed_path or str(Path(path))

    existing = conn.execute(
        select(source_documents.c.document_id, source_documents.c.archive_uri)
        .where(source_documents.c.sha256 == fp.sha256)
    ).first()
    if existing is not None:
        _observe(conn, existing.document_id, shown_path, fp.filename, now, fp.modified_at_utc)
        audit.record(
            conn, actor=actor, action="DOCUMENT_DUPLICATE_OBSERVED",
            entity_type="source_document", entity_id=existing.document_id,
            document_id=existing.document_id,
            after={"observed_path": shown_path, "sha256": fp.sha256},
        )
        return RegistrationResult(DUPLICATE, existing.document_id, fp.sha256,
                                  archive_uri=existing.archive_uri)

    prior = conn.execute(
        select(source_documents.c.document_id)
        .where(func.lower(source_documents.c.original_filename) == fp.filename.lower())
        .order_by(source_documents.c.recorded_at_utc.desc(),
                  source_documents.c.document_id.desc())
        .limit(1)
    ).scalar()

    archive_uri = None
    if archive_root is not None:
        archive_uri = str(archive.store(fp.path, archive_root, fp.sha256))

    document_id = new_document_id()
    conn.execute(insert(source_documents).values(
        document_id=document_id,
        sha256=fp.sha256,
        size_bytes=fp.size_bytes,
        file_type=fp.file_type,
        document_category=category,
        original_filename=fp.filename,
        original_path=shown_path,
        archive_uri=archive_uri,
        source_channel=source_channel,
        sender=sender,
        received_at_utc=received_at,
        recorded_at_utc=now,
        file_modified_at_utc=fp.modified_at_utc,
        content_status="REGISTERED",
        parent_document_id=parent_document_id,
        revision_of_document_id=prior,
        is_synthetic=is_synthetic,
    ))
    _observe(conn, document_id, shown_path, fp.filename, now, fp.modified_at_utc)

    outcome = REVISION if prior else NEW
    audit.record(
        conn, actor=actor,
        action="DOCUMENT_REVISION_DETECTED" if prior else "DOCUMENT_REGISTERED",
        entity_type="source_document", entity_id=document_id, document_id=document_id,
        reason=(f"same filename as {prior} with different bytes; review required"
                if prior else None),
        after={"sha256": fp.sha256, "filename": fp.filename, "size_bytes": fp.size_bytes,
               "source_channel": source_channel, "archive_uri": archive_uri,
               "revision_of": prior, "is_synthetic": is_synthetic},
    )
    return RegistrationResult(outcome, document_id, fp.sha256, prior, archive_uri)


def _observe(conn: Connection, document_id: str, path: str, filename: str,
             observed_at: datetime, modified_at: datetime) -> None:
    conn.execute(insert(document_observations).values(
        document_id=document_id, observed_path=path, observed_filename=filename,
        observed_at_utc=observed_at, file_modified_at_utc=modified_at,
    ))


def verify_archive(conn: Connection) -> list[tuple[str, str]]:
    """Re-hash every archived document. Returns ``(document_id, problem)`` failures."""
    problems: list[tuple[str, str]] = []
    rows = conn.execute(
        select(source_documents.c.document_id, source_documents.c.sha256,
               source_documents.c.archive_uri)
        .where(source_documents.c.archive_uri.is_not(None))
    )
    for r in rows:
        p = Path(r.archive_uri)
        if not p.is_file():
            problems.append((r.document_id, "archive object missing"))
            continue
        try:
            archive.verify(p, r.sha256)
        except archive.ArchiveIntegrityError as exc:
            problems.append((r.document_id, str(exc)))
    return problems
