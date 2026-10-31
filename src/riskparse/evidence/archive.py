"""Content-addressed evidence archive (local/demo implementation).

Layout: ``<archive_root>/sha256/<first two hex>/<sha256><original extension>``.
Archived bytes are never overwritten; a pre-existing archive object is re-hashed and
must match. For production the archive root must be the IT-provided UNC path with
restricted, immutable-by-policy permissions (game plan 4, 13.1) - that is not
configured here.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

from riskparse.evidence.hashing import sha256_file


class ArchiveIntegrityError(RuntimeError):
    """Archived bytes do not match their content address."""


def archive_path_for(archive_root: str | os.PathLike[str], sha256: str, suffix: str) -> Path:
    return Path(archive_root) / "sha256" / sha256[:2] / f"{sha256}{suffix.lower()}"


def store(source: str | os.PathLike[str], archive_root: str | os.PathLike[str],
          expected_sha256: str) -> Path:
    """Copy ``source`` into the archive, verifying the hash. Idempotent."""
    src = Path(source)
    target = archive_path_for(archive_root, expected_sha256, src.suffix)
    if target.exists():
        verify(target, expected_sha256)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        actual = sha256_file(tmp)
        if actual != expected_sha256:
            raise ArchiveIntegrityError(
                f"copied bytes hash {actual} != expected {expected_sha256} for {src}"
            )
        if target.exists():  # lost a race with another writer of identical content
            verify(target, expected_sha256)
        else:
            os.replace(tmp, target)
            os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def verify(path: str | os.PathLike[str], expected_sha256: str) -> None:
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ArchiveIntegrityError(f"archive object {path} hash {actual} != {expected_sha256}")
