"""Profile a folder of contract documents (``riskparse profile-documents``).

Answers the Week 1 sizing questions before any parser template is written:

* how many documents per folder class (Physical / Financial / Evergreen) and format;
* which PDFs have no text layer (OCR needed) or only partial text;
* which workbooks have hidden sheets or formulas with no saved value;
* which emails carry attachments, and of what type, and which email parts could not be read.

Email attachments are profiled as their own rows (``parent`` = the email), so the counts
reflect every document, not just every file on disk. The folder class is a **hint**
from the path only; it is not a classification.

The output contains structure only: no subjects, bodies or cell values. It is still
confidential (file names), so write it to the L drive, not this repository.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from riskparse.evidence.hashing import UnstableFileError, file_type_for, fingerprint
from riskparse.extract.base import ExtractionResult
from riskparse.extract.dispatch import extract_bytes
from riskparse.fswalk import check_output_location, walk_files

COLUMNS = [
    "relative_path", "parent", "file_name", "folder_class", "file_type", "size_bytes", "sha256",
    "status", "layout_key", "pages", "pages_without_text", "sheets", "hidden_sheets",
    "formulas_without_cached_value", "segments", "text_chars", "email_body_source",
    "attachments", "attachment_types", "warnings",
]


_DIGITS = re.compile(r"[0-9]+")
_WS = re.compile(r"\s+")
MIN_LABELS = 3


def _norm(text: str) -> str:
    return _WS.sub(" ", _DIGITS.sub("#", text)).strip().lower()


def layout_key(result: ExtractionResult, file_type: str) -> str:
    """Fingerprint of a document's *layout*, not its values. Empty if unknown.

    * PDF / email body: the set of field labels on the first page or body, i.e. the text
      before a colon on each line (``Buyer``, ``Delivery Point``, ``Price``), digits masked.
    * Excel: the positions and text of label cells (text cells with no digits) on each sheet.
    * Email: also the sender's domain, so confirmations from one counterparty group together.

    Documents built from the same counterparty template usually share a key even though
    their prices, volumes and dates differ. The key is a hash, so the profile summary can
    be shared without revealing any content.
    """
    parts: list[str] = []
    if file_type == "PDF":
        lines = [s.text for s in result.segments if s.location.startswith("p1:")]
        parts = sorted({_norm(t.split(":", 1)[0]) for t in lines if ":" in t
                        and 0 < len(t.split(":", 1)[0].strip()) <= 40})
    elif file_type in ("XLSX", "XLSM"):
        parts = sorted(f"{s.location}={_norm(s.text)}" for s in result.segments
                       if s.meta.get("type") == "text" and not _DIGITS.search(s.text))[:200]
    elif file_type == "MSG":
        sender = next((s.text for s in result.segments if s.location == "header:From-Address"),
                      "")
        if "@" in sender:
            parts.append("domain:" + sender.rsplit("@", 1)[1].strip().lower())
        body = [s.text for s in result.segments if s.kind in ("body_line", "html_body_line")]
        parts += sorted({_norm(t.split(":", 1)[0]) for t in body if ":" in t
                         and 0 < len(t.split(":", 1)[0].strip()) <= 40})
    labels = [p for p in parts if not p.startswith("domain:")]
    if file_type != "MSG" and len(labels) < MIN_LABELS:
        return ""
    if file_type == "MSG" and not parts:
        return ""
    digest = hashlib.sha256("\n".join([file_type, *parts]).encode("utf-8")).hexdigest()
    return f"{file_type[:1]}-{digest[:10]}"


def folder_class(rel: Path) -> str:
    found = {c for part in rel.parent.parts for c in ("PHYSICAL", "FINANCIAL", "EVERGREEN")
             if c.lower() in part.lower()}
    if not found:
        return "UNKNOWN"
    return found.pop() if len(found) == 1 else "AMBIGUOUS:" + "+".join(sorted(found))


@dataclass
class ProfileResult:
    rows: list[dict[str, str]]

    def summary(self) -> Counter:
        return Counter((r["folder_class"], r["file_type"], r["status"]) for r in self.rows)


def _row(rel: str, parent: str, name: str, fclass: str, ftype: str, data: bytes) -> dict:
    r = extract_bytes(data, ftype, filename=name)
    md = r.metadata
    return {
        "relative_path": rel, "parent": parent, "file_name": name, "folder_class": fclass,
        "file_type": ftype, "size_bytes": str(len(data)),
        "sha256": hashlib.sha256(data).hexdigest(), "status": r.status,
        "layout_key": layout_key(r, ftype),
        "pages": str(md.get("pages", "")),
        "pages_without_text": " ".join(map(str, md.get("pages_without_text", []))),
        "sheets": str(len(md["sheets"])) if "sheets" in md else "",
        "hidden_sheets": "; ".join(md.get("hidden_sheets", [])),
        "formulas_without_cached_value": str(md.get("formulas_without_cached_value", "")),
        "segments": str(len(r.segments)), "text_chars": str(r.text_chars),
        "email_body_source": md.get("body_source") or "",
        "attachments": str(md["attachments"]) if "attachments" in md else "",
        "attachment_types": " ".join(sorted({file_type_for(a.filename) for a in r.attachments})),
        "warnings": " | ".join(r.warnings),
    }, r


def profile_folder(root: str | os.PathLike[str]) -> ProfileResult:
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(base)
    rows: list[dict[str, str]] = []
    files, walk_errors = walk_files(base)
    for err in walk_errors:   # unreadable folders/links are reported, never skipped silently
        rows.append({**{c: "" for c in COLUMNS}, "relative_path": err.split(": ", 1)[0],
                     "folder_class": "UNKNOWN", "file_type": "OTHER", "status": "FAILED",
                     "warnings": f"not read: {err}"})
    for p in files:
        rel = p.relative_to(base)
        fclass = folder_class(rel)
        try:
            fp = fingerprint(p)
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() != fp.sha256:
                raise UnstableFileError(f"{p} changed while being profiled")
        except (OSError, UnstableFileError) as exc:
            rows.append({**{c: "" for c in COLUMNS}, "relative_path": rel.as_posix(),
                         "file_name": p.name, "folder_class": fclass,
                         "file_type": file_type_for(p), "status": "FAILED",
                         "warnings": f"could not read: {exc}"})
            continue
        row, result = _row(rel.as_posix(), "", p.name, fclass, fp.file_type, data)
        rows.append(row)
        for att in result.attachments:            # one level: attachments of an email
            att_rel = f"{rel.as_posix()}!/{att.filename}"
            if att.data is None:
                rows.append({**{c: "" for c in COLUMNS}, "relative_path": att_rel,
                             "parent": rel.as_posix(), "file_name": att.filename,
                             "folder_class": fclass, "file_type": file_type_for(att.filename),
                             "status": "UNSUPPORTED", "warnings": att.note})
                continue
            att_row, _ = _row(att_rel, rel.as_posix(), att.filename, fclass,
                              file_type_for(att.filename), att.data)
            rows.append(att_row)
    return ProfileResult(rows)


def write_profile(result: ProfileResult, out: str | os.PathLike[str], *,
                  scanned_root: str | os.PathLike[str] | None = None) -> Path:
    path = check_output_location(out, *([scanned_root] if scanned_root else []))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as fh:     # never overwrite
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(result.rows)
    return path


def format_summary(result: ProfileResult) -> str:
    counts = result.summary()
    lines = [f"{'folder class':<14} {'type':<6} {'status':<15} count", "-" * 44]
    for (fclass, ftype, status), n in sorted(counts.items()):
        lines.append(f"{fclass:<14} {ftype:<6} {status:<15} {n}")
    top = sum(1 for r in result.rows if not r["parent"])
    att = len(result.rows) - top
    lines += ["-" * 44, f"{top} file(s) on disk, {att} email attachment(s), "
              f"{len(result.rows)} document(s) in total"]
    layouts = Counter((r["file_type"], r["layout_key"]) for r in result.rows if r["layout_key"])
    no_key = sum(1 for r in result.rows if not r["layout_key"])
    if layouts:
        lines.append(f"{len(layouts)} distinct layout(s); largest groups (type, layout, count):")
        for (ftype, key), n in layouts.most_common(10):
            lines.append(f"  {ftype:<5} {key:<14} {n}")
    lines.append(f"{no_key} document(s) without a layout key (scanned, unreadable or "
                 "too few labels)")
    needs = [r["relative_path"] for r in result.rows if r["status"] in ("NEEDS_OCR",
                                                                         "PARTIAL_TEXT")]
    if needs:
        lines.append(f"{len(needs)} document(s) need attention (OCR / partial text) - see the CSV")
    return "\n".join(lines)
