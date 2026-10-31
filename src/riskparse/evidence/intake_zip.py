"""Safe intake of a received ZIP of trade documents (roadmap B8).

The ZIP as received is evidence in its own right: it is hashed (and optionally
registered) **before** extraction and is never modified. Members are extracted into
a new, empty directory with these protections:

* **Path safety** - absolute paths, drive letters, UNC prefixes, ``..`` segments and
  anything that would resolve outside the target directory are refused (zip-slip).
* **No overwrite** - files are created exclusively. A second member with the same
  name (or one that only differs by case on a case-insensitive file system) is kept
  under a ``__dupN`` suffix and reported; nothing is silently dropped or replaced.
* **No links, no encrypted members** - symlink entries and encrypted entries are not
  extracted; each is reported as a finding.
* **Size limits** - total declared size, per-member compression ratio and the bytes
  actually written are capped, so a zip bomb or a lying header aborts the run.
* **No content handling** - members are copied byte-for-byte and hashed. Nothing is
  opened, parsed or executed. Nested archives are extracted as files but not
  unpacked; macro-enabled Office files are flagged for careful handling.

The result is an inventory manifest in ``Contract_Documents`` format (one row for
the ZIP, one per member, with ``parent_doc_inventory_id`` pointing at the ZIP) plus a
list of findings. Registration into SQL is a separate, opt-in step so that the
inventory can be produced before the SQL engine is confirmed.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import stat
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import Connection

from riskparse.evidence.hashing import file_type_for, fingerprint
from riskparse.evidence.registry import RegistrationResult, register_document
from riskparse.inventory.template import CONTRACT_DOCUMENTS

DEFAULT_MAX_TOTAL_BYTES = 2 * 1024**3        # 2 GiB uncompressed
DEFAULT_MAX_MEMBERS = 10_000
DEFAULT_MAX_RATIO = 200                     # uncompressed / compressed, per member
_RATIO_FLOOR_BYTES = 10 * 1024**2           # ratio only enforced above 10 MiB
_CHUNK = 1024 * 1024

NESTED_ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
MACRO_SUFFIXES = {".xlsm", ".xlsb", ".xltm", ".docm", ".dotm", ".pptm", ".potm", ".xla",
                  ".xlam"}
_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)
_INVALID_CHARS = set('<>:"|?*') | {chr(c) for c in range(32)}


class ZipIntakeError(RuntimeError):
    """The archive cannot be processed safely; nothing should be trusted from it."""


@dataclass(frozen=True)
class Finding:
    kind: str      # UNSAFE_PATH | SYMLINK | ENCRYPTED | DUPLICATE_NAME | CASE_COLLISION |
    member: str    # NESTED_ARCHIVE | MACRO_ENABLED | EMPTY_FILE
    detail: str


@dataclass(frozen=True)
class ExtractedMember:
    member: str               # name as stored in the ZIP
    path: Path                # where it was written
    sha256: str
    size_bytes: int
    modified_in_zip: str      # ZIP-recorded timestamp (no timezone in the ZIP format)


@dataclass
class ZipIntakeResult:
    zip_path: Path
    zip_sha256: str
    zip_size_bytes: int
    extract_dir: Path
    members: list[ExtractedMember] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _safe_relative(name: str) -> PurePosixPath | None:
    """Return a safe relative path for a member name, or None if it must be refused."""
    cleaned = name.replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or cleaned.startswith("//"):
        return None
    if _DRIVE.match(cleaned):
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    for p in parts:
        if any(ch in _INVALID_CHARS for ch in p) or p.endswith((" ", ".")) \
                or _WINDOWS_RESERVED.match(p):
            return None
    return PurePosixPath(*parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _zip_timestamp(info: zipfile.ZipInfo) -> str:
    try:
        return datetime(*info.date_time).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def _open_exclusive(target: Path):
    """Create ``target`` exclusively; on a name clash use ``name__dupN.ext``."""
    candidate, n = target, 1
    while True:
        try:
            return candidate, open(candidate, "xb")
        except FileExistsError:
            n += 1
            candidate = target.with_name(f"{target.stem}__dup{n}{target.suffix}")
            if n > 1000:
                raise ZipIntakeError(f"too many members named {target.name}") from None


def safe_extract(zip_path: str | os.PathLike[str], extract_dir: str | os.PathLike[str], *,
                 max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
                 max_members: int = DEFAULT_MAX_MEMBERS,
                 max_ratio: int = DEFAULT_MAX_RATIO) -> ZipIntakeResult:
    src = Path(zip_path)
    dest = Path(extract_dir)
    fp = fingerprint(src)                       # hash the archive exactly as received
    if not zipfile.is_zipfile(src):
        raise ZipIntakeError(f"{src} is not a ZIP archive")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ZipIntakeError(f"{dest} must be a new or empty directory")
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    # Store absolute paths so the manifest is unambiguous wherever it is opened.
    result = ZipIntakeResult(src.resolve(), fp.sha256, fp.size_bytes, root)

    with zipfile.ZipFile(src) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > max_members:
            raise ZipIntakeError(f"{len(infos)} members exceeds limit {max_members}")
        declared = sum(i.file_size for i in infos)
        if declared > max_total_bytes:
            raise ZipIntakeError(f"declared uncompressed size {declared} exceeds limit "
                                 f"{max_total_bytes}")
        written_total = 0
        seen_casefold: dict[str, str] = {}
        for info in infos:
            name = info.filename
            if info.flag_bits & 0x1:
                result.findings.append(Finding("ENCRYPTED", name, "encrypted member not "
                                               "extracted; obtain an unencrypted copy"))
                result.skipped.append(name)
                continue
            if _is_symlink(info):
                result.findings.append(Finding("SYMLINK", name, "link entry not extracted"))
                result.skipped.append(name)
                continue
            rel = _safe_relative(name)
            if rel is None:
                result.findings.append(Finding("UNSAFE_PATH", name, "unsafe member path "
                                               "refused (absolute, drive, '..' or invalid)"))
                result.skipped.append(name)
                continue
            if (info.file_size > _RATIO_FLOOR_BYTES
                    and info.file_size / max(info.compress_size, 1) > max_ratio):
                raise ZipIntakeError(f"{name}: compression ratio exceeds {max_ratio}; "
                                     "possible zip bomb")
            target = root.joinpath(*rel.parts)
            if root not in target.resolve().parents:
                result.findings.append(Finding("UNSAFE_PATH", name, "resolves outside the "
                                               "extraction directory"))
                result.skipped.append(name)
                continue
            folded = str(rel).casefold()
            if folded in seen_casefold and seen_casefold[folded] != str(rel):
                result.findings.append(Finding(
                    "CASE_COLLISION", name, f"differs only by letter case from "
                    f"{seen_casefold[folded]}; they collide on Windows / the L drive"))
            seen_casefold.setdefault(folded, str(rel))
            target.parent.mkdir(parents=True, exist_ok=True)
            final, out = _open_exclusive(target)
            if final != target:
                result.findings.append(Finding("DUPLICATE_NAME", name,
                                               f"name already used; kept as {final.name}"))
            digest = hashlib.sha256()
            size = 0
            with out, zf.open(info) as member:   # zipfile verifies the CRC at EOF
                while chunk := member.read(_CHUNK):
                    size += len(chunk)
                    written_total += len(chunk)
                    if size > info.file_size or written_total > max_total_bytes:
                        raise ZipIntakeError(f"{name}: more bytes than declared; aborting")
                    digest.update(chunk)
                    out.write(chunk)
            os.chmod(final, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            suffix = final.suffix.lower()
            if suffix in NESTED_ARCHIVE_SUFFIXES:
                result.findings.append(Finding("NESTED_ARCHIVE", name, "extracted as a file, "
                                               "not unpacked; process it separately"))
            if suffix in MACRO_SUFFIXES:
                result.findings.append(Finding("MACRO_ENABLED", name, "macro-enabled file; "
                                               "open only with macros disabled"))
            if size == 0:
                result.findings.append(Finding("EMPTY_FILE", name, "zero-byte member"))
            result.members.append(ExtractedMember(name, final, digest.hexdigest(), size,
                                                  _zip_timestamp(info)))
    return result


def manifest_rows(result: ZipIntakeResult, *, inventoried_by: str = "",
                  inventoried_date: str = "") -> list[dict[str, str]]:
    """Contract_Documents-format rows: the ZIP first, then each extracted member."""
    findings_by_member: dict[str, list[str]] = {}
    for f in result.findings:
        findings_by_member.setdefault(f.member, []).append(f"{f.kind}: {f.detail}")
    blank = {h: "" for h in CONTRACT_DOCUMENTS.headers}
    zip_id = "INV-ZIP-0001"
    rows = [{**blank,
             "doc_inventory_id": zip_id, "file_name": result.zip_path.name,
             "unc_path": str(result.zip_path), "file_type": "ZIP",
             "document_category": "OTHER", "sha256": result.zip_sha256,
             "size_bytes": str(result.zip_size_bytes), "inventory_status": "LISTED",
             "inventoried_by": inventoried_by, "inventoried_date": inventoried_date,
             "notes": f"received archive; {len(result.members)} member(s) extracted, "
                      f"{len(result.skipped)} refused"}]
    for n, m in enumerate(result.members, start=1):
        rel = m.path.relative_to(result.extract_dir)
        rows.append({**blank,
                     "doc_inventory_id": f"INV-DOC-{n:04d}", "file_name": m.path.name,
                     "unc_path": str(m.path), "file_type": file_type_for(m.path),
                     "parent_doc_inventory_id": zip_id, "sha256": m.sha256,
                     "size_bytes": str(m.size_bytes),
                     "folder_hint": rel.parent.name if rel.parent.name else "",
                     "inventory_status": "LISTED", "inventoried_by": inventoried_by,
                     "inventoried_date": inventoried_date,
                     "notes": "; ".join([f"zip member: {m.member}",
                                         f"zip timestamp (no tz): {m.modified_in_zip}",
                                         *findings_by_member.get(m.member, [])])})
    for name in result.skipped:
        rows.append({**blank,
                     "doc_inventory_id": f"INV-DOC-{len(rows):04d}",
                     "file_name": PurePosixPath(name.replace("\\", "/")).name or name,
                     "unc_path": f"{result.zip_path}!/{name}", "file_type": "OTHER",
                     "parent_doc_inventory_id": zip_id, "inventory_status": "EXCEPTED",
                     "exception_reason": "; ".join(findings_by_member.get(name, [])),
                     "inventoried_by": inventoried_by, "inventoried_date": inventoried_date,
                     "notes": f"zip member not extracted: {name}"})
    return rows


def write_manifest(rows: list[dict[str, str]], out: str | os.PathLike[str]) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as fh:   # never overwrite
        w = csv.DictWriter(fh, fieldnames=CONTRACT_DOCUMENTS.headers, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def register_intake(conn: Connection, result: ZipIntakeResult, *, actor: str,
                    source_channel: str, archive_root: str | os.PathLike[str] | None,
                    sender: str | None = None,
                    received_at: datetime | None = None
                    ) -> tuple[RegistrationResult, list[RegistrationResult]]:
    """Register the ZIP and every extracted member (members point at the ZIP)."""
    if received_at is not None and received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    zip_reg = register_document(conn, result.zip_path, actor=actor,
                                source_channel=source_channel, category="OTHER",
                                sender=sender, received_at=received_at,
                                archive_root=archive_root)
    if zip_reg.sha256 != result.zip_sha256:
        raise ZipIntakeError("ZIP changed between extraction and registration")
    regs = []
    for m in result.members:
        reg = register_document(conn, m.path, actor=actor, source_channel=source_channel,
                                sender=sender, received_at=received_at,
                                parent_document_id=zip_reg.document_id,
                                archive_root=archive_root,
                                observed_path=f"{result.zip_path}!/{m.member}")
        if reg.sha256 != m.sha256:
            raise ZipIntakeError(f"{m.member} changed after extraction")
        regs.append(reg)
    return zip_reg, regs

