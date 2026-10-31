"""Run extraction on registered documents and store the segments in SQL.

* Bytes are read from the archive copy (falling back to the original path) and verified
  against the registered SHA-256 before parsing.
* Results are idempotent per (document, extractor, extractor version).
* Email attachments are written to ``<work_root>/attachments/<document_id>/`` (never
  overwriting) and registered as their own documents with ``parent_document_id`` set
  to the email, so they get their own hash, archive copy and extraction.
* Every extraction and attachment registration is audited. Nothing here classifies a
  document, approves it or creates trades or terms.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import Connection, insert, select

from riskparse import audit
from riskparse.db.schema import document_extractions, document_segments, source_documents
from riskparse.evidence.intake_zip import _open_exclusive, _safe_relative
from riskparse.evidence.registry import read_verified_bytes, register_document
from riskparse.extract.dispatch import EXTRACTORS, extract_bytes, extractor_for
from riskparse.identity import normalize_actor

ATTACHMENT_CHANNEL = "MSG_ATTACHMENT"
MAX_PASSES = 4        # email -> attachment -> (attached email saved separately) ...


@dataclass
class ExtractionSummary:
    document_id: str
    status: str
    segments: int
    already_done: bool = False
    warnings: list[str] = field(default_factory=list)
    attachments_registered: list[tuple[str, str, str]] = field(default_factory=list)


def _safe_attachment_name(name: str, index: int) -> str:
    rel = _safe_relative(PurePosixPath(name.replace("\\", "/")).name or "")
    return rel.name if rel is not None else f"attachment_{index}.bin"


def extract_document(conn: Connection, document_id: str, *, actor: str,
                     work_root: str | os.PathLike[str],
                     archive_root: str | os.PathLike[str] | None) -> ExtractionSummary:
    doc = conn.execute(select(source_documents).where(
        source_documents.c.document_id == document_id)).mappings().first()
    if doc is None:
        raise LookupError(f"document {document_id} is not registered")
    ident = extractor_for(doc["file_type"])
    if ident is not None:
        existing = conn.execute(select(document_extractions).where(
            document_extractions.c.document_id == document_id,
            document_extractions.c.extractor == ident[0],
            document_extractions.c.extractor_version == ident[1])).mappings().first()
        if existing is not None:
            return ExtractionSummary(document_id, existing["status"], existing["segment_count"],
                                     already_done=True)

    data = read_verified_bytes(dict(doc))
    result = extract_bytes(data, doc["file_type"], filename=doc["original_filename"])
    now = datetime.now(UTC)
    res = conn.execute(insert(document_extractions).values(
        document_id=document_id, extractor=result.extractor,
        extractor_version=result.extractor_version, status=result.status,
        segment_count=len(result.segments), text_sha256=result.text_sha256(),
        metadata_json=json.dumps(result.metadata, sort_keys=True, default=str),
        warnings_json=json.dumps(result.warnings) if result.warnings else None,
        extracted_by=normalize_actor(actor), extracted_at_utc=now))
    extraction_id = res.inserted_primary_key[0]
    if result.segments:
        conn.execute(insert(document_segments), [
            {"extraction_id": extraction_id, "seq": s.seq, "location": s.location,
             "kind": s.kind, "text": s.text,
             "meta_json": json.dumps(s.meta, sort_keys=True, default=str) if s.meta else None}
            for s in result.segments])

    summary = ExtractionSummary(document_id, result.status, len(result.segments),
                                warnings=list(result.warnings))
    if result.attachments:
        out_dir = Path(work_root) / "attachments" / document_id
        out_dir.mkdir(parents=True, exist_ok=True)
        for att in result.attachments:
            if att.data is None:
                continue
            target, fh = _open_exclusive(out_dir / _safe_attachment_name(att.filename, att.index))
            with fh:
                fh.write(att.data)
            reg = register_document(conn, target, actor=actor, source_channel=ATTACHMENT_CHANNEL,
                                    parent_document_id=document_id, archive_root=archive_root,
                                    observed_path=f"{doc['original_path']}!/{att.filename}")
            summary.attachments_registered.append((att.filename, reg.document_id, reg.outcome))
    audit.record(conn, actor=actor, action="DOCUMENT_TEXT_EXTRACTED", entity_type="source_document",
                 entity_id=document_id, document_id=document_id,
                 after={"extractor": result.extractor, "version": result.extractor_version,
                        "status": result.status, "segments": len(result.segments),
                        "text_sha256": result.text_sha256(), "warnings": len(result.warnings),
                        "attachments_registered": [a[1] for a in summary.attachments_registered]})
    return summary


def pending_documents(conn: Connection) -> list[str]:
    """Registered documents of an extractable type with no extraction at their version."""
    out = []
    docs = conn.execute(select(source_documents.c.document_id, source_documents.c.file_type)
                        .where(source_documents.c.file_type.in_(list(EXTRACTORS)))
                        .order_by(source_documents.c.recorded_at_utc,
                                  source_documents.c.document_id)).all()
    done = {(r.document_id, r.extractor, r.extractor_version) for r in conn.execute(
        select(document_extractions.c.document_id, document_extractions.c.extractor,
               document_extractions.c.extractor_version))}
    for d in docs:
        name, version = extractor_for(d.file_type)
        if (d.document_id, name, version) not in done:
            out.append(d.document_id)
    return out


def extract_all_pending(conn: Connection, *, actor: str, work_root, archive_root
                        ) -> list[ExtractionSummary]:
    """Extract every pending document, then any attachments that registration produced."""
    summaries: list[ExtractionSummary] = []
    for _ in range(MAX_PASSES):
        todo = pending_documents(conn)
        if not todo:
            break
        for document_id in todo:
            summaries.append(extract_document(conn, document_id, actor=actor,
                                              work_root=work_root, archive_root=archive_root))
    return summaries
