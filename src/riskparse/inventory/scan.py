"""Read-only folder scan to pre-fill the Contract_Documents inventory sheet.

Only file *bytes* are hashed; files are never opened by an application, parsed or
executed, and nothing is moved, renamed or written in the scanned tree. The folder
name is recorded as ``folder_hint`` only - it is not a classification or approval.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from riskparse.evidence.hashing import UnstableFileError, fingerprint
from riskparse.inventory.template import CONTRACT_DOCUMENTS

_HINT_FOLDERS = ("physical", "financial", "evergreen", "egistix", "trader sheets")
_SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


@dataclass(frozen=True)
class ScanFinding:
    kind: str     # DUPLICATE_CONTENT | SAME_NAME_DIFFERENT_CONTENT | UNSTABLE | UNREADABLE
    detail: str


def _folder_hint(rel: Path) -> str:
    for part in reversed(rel.parent.parts):
        if part.lower() in _HINT_FOLDERS:
            return part
    return rel.parent.name


def scan_folder(root: str | os.PathLike[str], *, inventoried_by: str = "",
                inventoried_date: str = "") -> tuple[list[dict[str, str]], list[ScanFinding]]:
    root_path = Path(root)
    if not root_path.is_dir():
        raise NotADirectoryError(root_path)
    rows: list[dict[str, str]] = []
    findings: list[ScanFinding] = []
    by_hash: dict[str, list[str]] = defaultdict(list)
    by_name: dict[str, set[str]] = defaultdict(set)
    files = sorted(p for p in root_path.rglob("*")
                   if p.is_file() and not p.is_symlink() and p.name.lower() not in _SKIP_NAMES
                   and not p.name.startswith("~$"))
    for n, p in enumerate(files, start=1):
        rel = p.relative_to(root_path)
        try:
            fp = fingerprint(p)
        except UnstableFileError as exc:
            findings.append(ScanFinding("UNSTABLE", str(exc)))
            continue
        except OSError as exc:
            findings.append(ScanFinding("UNREADABLE", f"{p}: {exc}"))
            continue
        by_hash[fp.sha256].append(str(rel))
        by_name[p.name.lower()].add(fp.sha256)
        row = {h: "" for h in CONTRACT_DOCUMENTS.headers}
        row.update({
            "doc_inventory_id": f"INV-DOC-{n:04d}",
            "file_name": p.name,
            "unc_path": str(p),
            "file_type": fp.file_type,
            "sha256": fp.sha256,
            "size_bytes": str(fp.size_bytes),
            "folder_hint": _folder_hint(rel),
            "inventory_status": "LISTED",
            "inventoried_by": inventoried_by,
            "inventoried_date": inventoried_date,
        })
        rows.append(row)
    for digest, paths in by_hash.items():
        if len(paths) > 1:
            findings.append(ScanFinding("DUPLICATE_CONTENT",
                                        f"identical bytes {digest[:12]}... at: {'; '.join(paths)}"))
    for name, digests in by_name.items():
        if len(digests) > 1:
            findings.append(ScanFinding("SAME_NAME_DIFFERENT_CONTENT",
                                        f"'{name}' has {len(digests)} different contents "
                                        "(treat as revisions; never overwrite)"))
    return rows, findings


def write_scan_csv(rows: list[dict[str, str]], out: str | os.PathLike[str]) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CONTRACT_DOCUMENTS.headers, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path
