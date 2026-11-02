"""Step 1 in one command: ``riskparse review <zip or folder>``.

Builds one Excel workbook for Risk to review the October contracts, and nothing else:
no settings file, no database, no registration.

* **Summary**: document counts by folder (Physical / Financial / Evergreen), file type and
  whether the text could be read.
* **Documents**: one row per document, with email attachments as their own rows, showing
  status, pages, sheets, warnings, layout group and SHA-256.
* **Text**: every line or cell of readable text, with its exact location (``p2:L14``,
  ``Confirm!B7``, ``body:L4``), ready to filter in Excel.
* **Fields**: proposed trade fields (trade date, price, quantity, delivery point...) found
  as "label -> value" pairs, each with its exact location and blank columns for Risk to
  mark OK or wrong (see :mod:`riskparse.fields`).
* **Labels**: every label found, per layout group, with **no values**, used to improve the
  label mapping.
* **Issues**: everything that needs a human look (scans, refused ZIP members, unreadable
  folders, hidden sheets, embedded emails).

A ZIP is extracted into the output folder with the same protections as ``intake-zip``. The
input is never modified, and the output folder must be new. Nothing is interpreted as a
price, volume or date; that is the next step.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from riskparse.evidence.hashing import file_type_for
from riskparse.evidence.intake_zip import safe_extract
from riskparse.extract.dispatch import extract_bytes
from riskparse.extract.profile import folder_class, layout_key
from riskparse.fields import SYNONYMS, coverage, propose_fields
from riskparse.fswalk import check_output_location, walk_files

MAX_CELL = 32_000          # Excel's limit is 32,767 characters per cell
MAX_TEXT_ROWS = 1_000_000  # Excel's limit is 1,048,576 rows per sheet

DOC_COLUMNS = ["doc", "folder", "file", "attached_to", "folder_class", "type", "status",
               "pages", "pages_without_text", "sheets", "attachments", "text_lines",
               "layout_group", "warnings", "sha256", "path"]


class ReviewError(RuntimeError):
    pass


@dataclass
class ReviewResult:
    out_dir: Path
    workbook: Path
    documents: list[dict] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)   # (doc/file, kind, detail)
    text_rows: int = 0
    proposals: dict[str, list] = field(default_factory=dict)          # doc -> [FieldProposal]

    def counts(self) -> Counter:
        return Counter((d["folder_class"], d["type"], d["status"]) for d in self.documents)


def _clean(value):
    """Make a value safe for an Excel cell: text only, no control characters, length-capped."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = ILLEGAL_CHARACTERS_RE.sub("", value)
    if len(text) > MAX_CELL:
        text = text[:MAX_CELL] + " …[truncated]"
    return text


def _put(ws, row: int, values: list) -> None:
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=_clean(value))
        if isinstance(cell.value, str):
            cell.data_type = "s"     # never let document text become an Excel formula


def _sheet(wb: Workbook, title: str, headers: list[str], widths: list[int]):
    ws = wb.create_sheet(title)
    _put(ws, 1, headers)
    for i, w in enumerate(widths, start=1):
        ws.cell(row=1, column=i).font = Font(bold=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    return ws


def build_review(source: str | Path, out_dir: str | Path | None = None) -> ReviewResult:
    src = Path(source)
    if not src.exists():
        raise ReviewError(f"{src} does not exist")
    if out_dir is None:
        out_dir = src.parent / f"{src.stem}_review_{datetime.now():%Y%m%d-%H%M%S}"
    out = check_output_location(out_dir, *([src] if src.is_dir() else []))
    out.mkdir(parents=True)
    result = ReviewResult(out, out / "review.xlsx")

    if src.is_dir():
        root = src
    elif zipfile.is_zipfile(src):
        zr = safe_extract(src, out / "extracted")
        root = zr.extract_dir
        for f in zr.findings:
            result.issues.append((f.member, f.kind, f.detail))
    else:
        raise ReviewError(f"{src} is neither a folder nor a ZIP file")

    files, walk_errors = walk_files(root)
    for err in walk_errors:
        result.issues.append((err.split(": ", 1)[0], "NOT_READ", err))

    text_rows: list[list] = []
    n = 0
    for path in files:
        rel = path.relative_to(root)
        n += 1
        doc_no = f"D{n:03d}"
        data = path.read_bytes()
        fclass = folder_class(rel)
        r = _add(result, text_rows, doc_no, rel.parent.as_posix(), path.name, "", fclass,
                 file_type_for(path), data, str(path))
        for i, att in enumerate(r.attachments, start=1):
            att_no = f"{doc_no}.{i}"
            if att.data is None:
                result.documents.append({**dict.fromkeys(DOC_COLUMNS, ""), "doc": att_no,
                                         "folder": rel.parent.as_posix(), "file": att.filename,
                                         "attached_to": doc_no, "folder_class": fclass,
                                         "type": file_type_for(att.filename),
                                         "status": "NOT_EXTRACTED", "warnings": att.note})
                result.issues.append((att_no, "ATTACHMENT_NOT_EXTRACTED", att.note))
                continue
            _add(result, text_rows, att_no, rel.parent.as_posix(), att.filename, doc_no, fclass,
                 file_type_for(att.filename), att.data, f"{path}!/{att.filename}")

    if len(text_rows) > MAX_TEXT_ROWS:
        result.issues.append(("", "TEXT_TRUNCATED", f"{len(text_rows)} text rows; only the "
                              f"first {MAX_TEXT_ROWS} fit in Excel"))
        text_rows = text_rows[:MAX_TEXT_ROWS]
    result.text_rows = len(text_rows)
    _write_workbook(result, text_rows, src)
    return result


def _add(result: ReviewResult, text_rows: list, doc_no: str, folder: str, name: str,
         parent: str, fclass: str, ftype: str, data: bytes, path: str):
    r = extract_bytes(data, ftype, filename=name)
    md = r.metadata
    result.documents.append({
        "doc": doc_no, "folder": folder, "file": name, "attached_to": parent,
        "folder_class": fclass, "type": ftype, "status": r.status,
        "pages": md.get("pages", ""),
        "pages_without_text": " ".join(map(str, md.get("pages_without_text", []))),
        "sheets": len(md["sheets"]) if "sheets" in md else "",
        "attachments": md.get("attachments", ""),
        "text_lines": len([s for s in r.segments if s.kind != "attachment"]),
        "layout_group": layout_key(r, ftype), "warnings": " | ".join(r.warnings),
        "sha256": hashlib.sha256(data).hexdigest(), "path": path,
    })
    if r.status not in ("TEXT_EXTRACTED",):
        result.issues.append((doc_no, r.status, " | ".join(r.warnings) or r.status))
    for s in r.segments:
        if s.kind != "attachment":
            text_rows.append([doc_no, name, s.location, s.text])
    result.proposals[doc_no] = propose_fields(r)
    return r


def _write_workbook(result: ReviewResult, text_rows: list, src: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    tops = [d for d in result.documents if not d["attached_to"]]
    lines = [
        ["October contracts - document review"],
        [f"Source: {src}"],
        [f"Generated: {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}"],
        [f"{len(tops)} file(s), {len(result.documents) - len(tops)} email attachment(s), "
         f"{len(result.documents)} document(s); {len(result.issues)} issue(s) to review"],
        ["Nothing in this workbook is interpreted as a price, volume or date. CONFIDENTIAL: "
         "keep it on the controlled machine."],
        [],
        ["Folder", "Type", "Status", "Documents"],
    ]
    for (fclass, ftype, status), count in sorted(result.counts().items()):
        lines.append([fclass, ftype, status, count])
    lines += [[], ["Layout group", "Type", "Documents"]]
    groups = Counter((d["layout_group"], d["type"]) for d in result.documents
                     if d["layout_group"])
    for (key, ftype), count in groups.most_common():
        lines.append([key, ftype, count])
    for i, values in enumerate(lines, start=1):
        _put(ws, i, values)
    ws["A1"].font = Font(bold=True, size=14)
    for col, w in zip("ABCD", (28, 10, 18, 12), strict=True):
        ws.column_dimensions[col].width = w

    docs = _sheet(wb, "Documents", DOC_COLUMNS,
                  [7, 30, 40, 11, 12, 6, 15, 6, 10, 7, 11, 10, 14, 60, 66, 60])
    for i, d in enumerate(result.documents, start=2):
        _put(docs, i, [d[c] for c in DOC_COLUMNS])
    docs.auto_filter.ref = docs.dimensions

    text = _sheet(wb, "Text", ["doc", "file", "location", "text"], [8, 40, 18, 120])
    for i, row in enumerate(text_rows, start=2):
        _put(text, i, row)
    text.auto_filter.ref = text.dimensions

    by_doc = {d["doc"]: d for d in result.documents}
    fields_ws = _sheet(wb, "Fields", ["doc", "file", "layout_group", "field", "label as written",
                                      "value as written", "location", "found", "risk check (OK/"
                                      "WRONG)", "correct value", "checked by"],
                       [8, 34, 14, 16, 30, 40, 16, 11, 16, 24, 14])
    row = 2
    for doc_no, props in result.proposals.items():
        d = by_doc[doc_no]
        for p in sorted(props, key=lambda p: (p.field == "(unmapped)", p.field)):
            _put(fields_ws, row, [doc_no, d["file"], d["layout_group"], p.field, p.label,
                                  p.value, p.location, p.how, None, None, None])
            row += 1
    fields_ws.auto_filter.ref = fields_ws.dimensions

    labels_ws = _sheet(wb, "Labels", ["layout_group", "type", "label (digits masked)",
                                      "mapped to", "documents"], [14, 6, 40, 16, 10])
    label_docs: dict[tuple[str, str, str, str], set] = {}
    for doc_no, props in result.proposals.items():
        d = by_doc[doc_no]
        for p in props:
            key = (d["layout_group"] or "(none)", d["type"], p.label_key, p.field)
            label_docs.setdefault(key, set()).add(doc_no)
    for i, (key, docs_) in enumerate(sorted(label_docs.items()), start=2):
        _put(labels_ws, i, [*key, len(docs_)])
    labels_ws.auto_filter.ref = labels_ws.dimensions

    issues = _sheet(wb, "Issues", ["doc / file", "issue", "detail"], [30, 26, 120])
    for i, row in enumerate(result.issues, start=2):
        _put(issues, i, list(row))
    issues.auto_filter.ref = issues.dimensions
    wb.save(result.workbook)


def format_summary(result: ReviewResult) -> str:
    lines = [f"{'folder':<12} {'type':<6} {'status':<15} docs", "-" * 40]
    for (fclass, ftype, status), count in sorted(result.counts().items()):
        lines.append(f"{fclass:<12} {ftype:<6} {status:<15} {count}")
    groups = Counter((d["layout_group"], d["type"]) for d in result.documents
                     if d["layout_group"])
    lines += ["-" * 40, f"{len(result.documents)} document(s), {len(groups)} layout group(s), "
              f"{len(result.issues)} issue(s)"]
    for (key, ftype), count in groups.most_common(10):
        lines.append(f"  layout {key:<14} {ftype:<5} {count}")
    cov = coverage(result.proposals)
    n = len(result.documents)
    lines += ["", f"Proposed fields (documents with a value, out of {n}):"]
    for name in SYNONYMS:
        lines.append(f"  {name:<16} {cov.get(name, 0)}")
    unmapped = {p.label_key for props in result.proposals.values() for p in props
                if p.field == "(unmapped)"}
    lines.append(f"  {len(unmapped)} distinct label(s) not mapped yet (see the Labels sheet)")
    return "\n".join(lines)
