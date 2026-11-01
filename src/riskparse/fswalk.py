"""Safe folder walking and output placement shared by the scanners (Codex review).

* :func:`walk_files` lists regular files under a root without following symlinks and
  **reports** every directory or entry it could not read, instead of silently skipping
  that part of the tree (``rglob`` swallows permission errors).
* :func:`check_output_location` refuses an output path inside a folder being scanned or
  extracted, or an output that already exists, so a report can never overwrite
  evidence.
"""

from __future__ import annotations

import os
from pathlib import Path

SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


class UnsafeOutputError(RuntimeError):
    """The requested output would overwrite something or land inside the evidence tree."""


def walk_files(root: str | os.PathLike[str]) -> tuple[list[Path], list[str]]:
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(base)
    errors: list[str] = []

    def onerror(exc: OSError) -> None:
        errors.append(f"{exc.filename}: {exc.strerror or exc}")

    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base, onerror=onerror, followlinks=False):
        d = Path(dirpath)
        for name in list(dirnames):
            if (d / name).is_symlink():
                errors.append(f"{d / name}: symbolic link to a directory not followed")
                dirnames.remove(name)
        for name in filenames:
            p = d / name
            if p.is_symlink():
                errors.append(f"{p}: symbolic link not followed")
                continue
            if name.lower() in SKIP_NAMES or name.startswith("~$"):
                continue
            files.append(p)
    return sorted(files), errors


def check_output_location(out: str | os.PathLike[str],
                          *protected_roots: str | os.PathLike[str]) -> Path:
    path = Path(out)
    if path.exists():
        raise UnsafeOutputError(f"{path} already exists; choose a new file name "
                                "(outputs are never overwritten)")
    resolved = path.resolve()
    for root in protected_roots:
        r = Path(root).resolve()
        if resolved == r or r in resolved.parents:
            raise UnsafeOutputError(f"{path} is inside {root}, the folder being read; write "
                                    "outputs somewhere else so evidence is never altered")
    return path
