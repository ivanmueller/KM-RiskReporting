"""Shared types for extractors."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

TEXT_EXTRACTED = "TEXT_EXTRACTED"
PARTIAL_TEXT = "PARTIAL_TEXT"     # some pages/parts have no text layer or were not decoded
NEEDS_OCR = "NEEDS_OCR"           # no usable text layer at all (scanned document)
ENCRYPTED = "ENCRYPTED"
UNSUPPORTED = "UNSUPPORTED"
FAILED = "FAILED"
STATUSES = (TEXT_EXTRACTED, PARTIAL_TEXT, NEEDS_OCR, ENCRYPTED, UNSUPPORTED, FAILED)

# Hard limits so a hostile or corrupt file cannot exhaust memory.
MAX_SEGMENTS = 200_000
MAX_TEXT_CHARS = 50_000_000


class ExtractionLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    seq: int
    location: str           # e.g. "Confirm!B7", "p3:L12", "header:Subject", "body:L4"
    kind: str               # cell | line | header | body_line | html_body_line | attachment
    text: str               # raw text exactly as extracted
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Attachment:
    """An attachment found inside an email. ``data`` is None if it was not extractable."""
    index: int
    filename: str
    data: bytes | None
    method: str             # BY_VALUE | EMBEDDED_MESSAGE | BY_REFERENCE | OLE | UNKNOWN
    note: str = ""


@dataclass
class ExtractionResult:
    extractor: str
    extractor_version: str
    status: str
    segments: list[Segment] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def text_chars(self) -> int:
        return sum(len(s.text) for s in self.segments)

    def text_sha256(self) -> str:
        """Hash of the ordered (location, text) pairs - changes if any segment changes."""
        h = hashlib.sha256()
        for s in self.segments:
            h.update(json.dumps([s.location, s.text], ensure_ascii=False).encode("utf-8"))
            h.update(b"\n")
        return h.hexdigest()


class SegmentBuilder:
    """Collects segments and enforces the size limits."""

    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self._chars = 0

    def add(self, location: str, kind: str, text: str, **meta: Any) -> None:
        if len(self.segments) >= MAX_SEGMENTS:
            raise ExtractionLimitExceeded(f"more than {MAX_SEGMENTS} segments")
        self._chars += len(text)
        if self._chars > MAX_TEXT_CHARS:
            raise ExtractionLimitExceeded(f"more than {MAX_TEXT_CHARS} characters of text")
        self.segments.append(Segment(len(self.segments) + 1, location, kind, text, meta))

    def add_lines(self, prefix: str, kind: str, text: str) -> int:
        """Split ``text`` into lines; blank lines are skipped but keep their numbers."""
        n = 0
        for i, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                self.add(f"{prefix}L{i}", kind, line.rstrip())
                n += 1
        return n
