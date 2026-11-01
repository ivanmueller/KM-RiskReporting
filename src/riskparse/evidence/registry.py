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
    container_document_id: str | None = None,
) -> RegistrationResult:
    """Register the file at ``path``. Must be called inside a transaction.

    ``original_path`` always stores the real file location, so the bytes can be
    re-verified later. ``observed_path`` (e.g. ``x.zip!/a.pdf``) and the container are
    recorded on the observation, once per sighting, so a file seen in two archives keeps
    both (Codex review).
    """
    if category is not None and category not in vocab.DOCUMENT_CATEGORIES:
        raise ValueError(f"unknown document category {category!r}")
    if received_at is not None and (received_at.tzinfo is None
                                    or received_at.utcoffset() is None):
        raise ValueError("received_at must be timezone-aware")
    container = container_document_id or parent_document_id
    fp = fingerprint(path)
    now = datetime.now(UTC)
    real_path = str(Path(path))
    shown_path = observed_path or real_path

    existing = conn.execute(
        select(source_documents.c.document_id, source_documents.c.archive_uri,
               source_documents.c.document_category)
        .where(source_documents.c.sha256 == fp.sha256)
    ).first()
    if existing is not None:
        _observe(conn, existing.document_id, shown_path, fp.filename, now, fp.modified_at_utc,
                 container)
        changes: dict = {}
        archive_uri = existing.archive_uri
        if archive_uri is None and archive_root is not None:
            # A later request to keep an evidence copy must not be dropped (Codex #19).
            archive_uri = str(archive.store(fp.path, archive_root, fp.sha256))
            changes["archive_uri"] = archive_uri
        if existing.document_category is None and category is not None:
            changes["document_category"] = category
        if changes:
            conn.execute(source_documents.update()
                         .where(source_documents.c.document_id == existing.document_id)
                         .values(**changes))
        audit.record(
            conn, actor=actor, action="DOCUMENT_DUPLICATE_OBSERVED",
            entity_type="source_document", entity_id=existing.document_id,
            document_id=existing.document_id,
            after={"observed_path": shown_path, "sha256": fp.sha256,
                   "container_document_id": container, **changes},
        )
        return RegistrationResult(DUPLICATE, existing.document_id, fp.sha256,
                                  archive_uri=archive_uri)

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
        original_path=real_path,
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
    _observe(conn, document_id, shown_path, fp.filename, now, fp.modified_at_utc, container)

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
             observed_at: datetime, modified_at: datetime,
             container_document_id: str | None = None) -> None:
    conn.execute(insert(document_observations).values(
        document_id=document_id, observed_path=path, observed_filename=filename,
        observed_at_utc=observed_at, file_modified_at_utc=modified_at,
        container_document_id=container_document_id,
    ))


def verify_archive(conn: Connection) -> list[tuple[str, str]]:
    """Re-hash the retained copy of every registered document.

    The archive copy is checked when there is one; otherwise the original file. A
    document with neither a readable archive copy nor a readable, matching original has
    lost its evidence, and is reported (Codex review #19). Returns ``(document_id,
    problem)`` failures.
    """
    import hashlib
    problems: list[tuple[str, str]] = []
    rows = conn.execute(select(source_documents.c.document_id, source_documents.c.sha256,
                               source_documents.c.archive_uri,
                               source_documents.c.original_path))
    for r in rows:
        if r.archive_uri:
            p = Path(r.archive_uri)
            if not p.is_file():
                problems.append((r.document_id, "archive object missing"))
                continue
            try:
                archive.verify(p, r.sha256)
            except archive.ArchiveIntegrityError as exc:
                problems.append((r.document_id, str(exc)))
            continue
        p = Path(r.original_path)
        if not p.is_file():
            problems.append((r.document_id, "no archive copy and the original file is missing; "
                                            "the evidence is not retained"))
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() != r.sha256:
            problems.append((r.document_id, "no archive copy and the original file has changed"))
    return problems


class EvidenceIntegrityError(RuntimeError):
    """Stored bytes no longer match the registered SHA-256."""


def read_verified_bytes(doc: dict) -> bytes:
    """Read a registered document (archive copy first) and verify it against its SHA-256.

    Returns the verified bytes so callers parse exactly what was checked.
    """
    import hashlib
    for candidate in (doc.get("archive_uri"), doc.get("original_path")):
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != doc["sha256"]:
            raise EvidenceIntegrityError(
                f"{path} does not match registered SHA-256 {doc['sha256']} for "
                f"{doc['document_id']}; refusing to process altered evidence")
        return data
    raise EvidenceIntegrityError(f"no readable copy of {doc['document_id']} (archive or original)")
