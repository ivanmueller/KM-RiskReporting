"""Append-only, hash-chained audit events (game plan 12.1, 12.2).

Each event stores ``prev_event_hash`` and ``event_hash = SHA-256(canonical payload)``,
so any edit or deletion of an earlier row is detectable by :func:`verify_chain`.
This is tamper-*evident*, not tamper-*proof*: database permissions that deny
UPDATE/DELETE on ``audit_events`` must be configured by IT once the engine is
confirmed. Writers are expected to be serialized; a database lock for concurrent
writers is an engine-specific feature deferred until the platform is known.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, insert, select

from riskparse.db.schema import audit_events
from riskparse.identity import normalize_actor

_HASHED_FIELDS = (
    "occurred_at_utc", "actor", "approver", "action", "entity_type", "entity_id",
    "document_id", "publication_id", "reason", "before_json", "after_json",
    "prev_event_hash",
)


def _canonical_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _event_hash(row: dict[str, Any]) -> str:
    payload = {}
    for key in _HASHED_FIELDS:
        v = row.get(key)
        if isinstance(v, datetime):
            # Whole seconds keep the original format (so existing chains still verify);
            # any sub-second part is included, so a microsecond edit changes the hash
            # (Codex review #13).
            v = v.astimezone(UTC)
            v = v.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if v.microsecond else "%Y-%m-%dT%H:%M:%SZ")
        payload[key] = v
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def record(conn: Connection, *, actor: str, action: str, entity_type: str, entity_id: str,
           reason: str | None = None, approver: str | None = None,
           document_id: str | None = None, publication_id: str | None = None,
           before: Any = None, after: Any = None,
           occurred_at: datetime | None = None) -> str:
    """Append an audit event and return its hash."""
    if not actor:
        raise ValueError("audit events require an actor")
    actor = normalize_actor(actor)
    if approver is not None:
        approver = normalize_actor(approver)
        if approver == actor:
            raise ValueError("approver must differ from actor (separation of duties)")
    if occurred_at is not None and (occurred_at.tzinfo is None
                                    or occurred_at.utcoffset() is None):
        raise ValueError("occurred_at must be timezone-aware (Codex review #20)")
    ts = (occurred_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    prev = conn.execute(
        select(audit_events.c.event_hash).order_by(audit_events.c.event_id.desc()).limit(1)
    ).scalar()
    row = {
        "occurred_at_utc": ts,
        "actor": actor,
        "approver": approver,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "document_id": document_id,
        "publication_id": publication_id,
        "reason": reason,
        "before_json": _canonical_json(before),
        "after_json": _canonical_json(after),
        "prev_event_hash": prev,
    }
    row["event_hash"] = _event_hash(row)
    conn.execute(insert(audit_events).values(**row))
    return row["event_hash"]


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    events_checked: int
    first_bad_event_id: int | None = None
    problem: str | None = None


def chain_head(conn: Connection) -> tuple[int, str | None]:
    """(event count, hash of the last event). Record this OUTSIDE the database."""
    rows = conn.execute(select(audit_events.c.event_hash).order_by(audit_events.c.event_id))
    hashes = [r.event_hash for r in rows]
    return len(hashes), (hashes[-1] if hashes else None)


def verify_chain(conn: Connection, *, expect_count: int | None = None,
                 expect_head: str | None = None) -> ChainVerification:
    """Verify every link and hash.

    Edits, reordering and deletions *inside* the chain are always detected. Deleting the
    most recent events (truncation) can only be detected against an anchor recorded
    outside the database: pass the ``(count, head)`` from an earlier :func:`chain_head`.
    The chain may have grown since; the event at that position must still carry that
    hash (Codex review #14).
    """
    if (expect_count is None) != (expect_head is None):
        raise ValueError("expect_count and expect_head must be given together")
    prev: str | None = None
    count = 0
    anchor_seen = expect_count is None
    for r in conn.execute(select(audit_events).order_by(audit_events.c.event_id)).mappings():
        count += 1
        if r["prev_event_hash"] != prev:
            return ChainVerification(False, count, r["event_id"], "broken prev_event_hash link")
        if _event_hash(dict(r)) != r["event_hash"]:
            return ChainVerification(False, count, r["event_id"], "event content altered")
        if expect_count is not None and count == expect_count:
            if r["event_hash"] != expect_head:
                return ChainVerification(False, count, r["event_id"],
                                         "event at the anchor position does not match the "
                                         "recorded head hash")
            anchor_seen = True
        prev = r["event_hash"]
    if not anchor_seen:
        return ChainVerification(False, count, None,
                                 f"chain has {count} event(s) but the anchor recorded "
                                 f"{expect_count}: events were deleted (truncation)")
    return ChainVerification(True, count)
