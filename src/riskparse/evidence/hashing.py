"""SHA-256 hashing of document bytes (game plan 4.1).

Identity is computed from bytes only - never from filename, folder or content parsing.
Files are opened read-only in binary mode and their content is never interpreted.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EXTENSION_FILE_TYPES = {
    ".pdf": "PDF", ".xlsx": "XLSX", ".xls": "XLS", ".xlsm": "XLSM", ".csv": "CSV",
    ".eml": "EML", ".msg": "MSG", ".docx": "DOCX", ".tif": "TIFF", ".tiff": "TIFF",
    ".zip": "ZIP",
}


class UnstableFileError(RuntimeError):
    """The file changed while being hashed (e.g. still being copied)."""


@dataclass(frozen=True)
class FileFingerprint:
    path: Path
    sha256: str
    size_bytes: int
    modified_at_utc: datetime
    file_type: str

    @property
    def filename(self) -> str:
        return self.path.name


def is_sha256_hex(value: str) -> bool:
    return bool(_SHA256_RE.match(value or ""))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_type_for(path: str | os.PathLike[str]) -> str:
    return EXTENSION_FILE_TYPES.get(Path(path).suffix.lower(), "OTHER")


def fingerprint(path: str | os.PathLike[str]) -> FileFingerprint:
    """Hash a file and verify it did not change during hashing.

    A size/mtime change between the before and after ``stat`` calls raises
    :class:`UnstableFileError`. This is a necessary, not sufficient, stability check
    (game plan 13.2); callers handling live intake should repeat it over time.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    before = p.stat()
    digest = sha256_file(p)
    after = p.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise UnstableFileError(f"file changed while hashing: {p}")
    return FileFingerprint(
        path=p,
        sha256=digest,
        size_bytes=after.st_size,
        modified_at_utc=datetime.fromtimestamp(after.st_mtime, tz=UTC),
        file_type=file_type_for(p),
    )
