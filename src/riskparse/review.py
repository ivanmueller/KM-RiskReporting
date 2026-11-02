"""Step 1 in one command: ``riskparse review <zip or folder>``.

Reads every confirmation and writes the trades in the firm's ``raw-data-format``.

``raw-data.xlsx``:

* **raw-data-format**: the firm's columns, one row per trade (dates as dates, numbers as
  numbers).
* **checks**: per trade, our buy/sell, settlement currency, price quote unit, fixed
  price, source family, supporting documents and anything that needs a look (amendments,
  duplicates, missing values), with columns for Risk to mark each row OK or WRONG.

``review.xlsx``: the same two sheets plus **evidence** (for every filled cell, the exact
place in the document and the text it was read from), **documents** (which parser read
each file), **text** (all readable text with locations) and **issues**.

Trades come only from the confirmation-family parsers (:mod:`riskparse.confirms`).
A document no parser recognises is listed as ``UNRECOGNISED``; it is not guessed at.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from riskparse.confirms import DEFAULT_US, Trade, merge_trades, parse_document
from riskparse.evidence.hashing import file_type_for
from riskparse.evidence.intake_zip import safe_extract
from riskparse.extract.dispatch import extract_bytes
from riskparse.extract.profile import folder_class
from riskparse.fswalk import check_output_location, walk_files
from riskparse.names import DEFAULT_MAP, NameMap, apply_name_map, load_name_map
from riskparse.rawformat import DEFAULT_HEADERS, attr_for, cell_value, read_format_headers

MAX_CELL = 32_000          # Excel's limit is 32,767 characters per cell
MAX_TEXT_ROWS = 1_000_000  # Excel's limit is 1,048,576 rows per sheet

DOC_COLUMNS = ["doc", "folder", "file", "attached_to", "folder_class", "type", "status",
               "parsed_as", "trades", "pages", "sheets", "warnings", "sha256", "path"]
CHECK_COLUMNS = ["Contract #", "Our side", "Source", "Broker / issuer", "Settlement currency",
                 "Price quoted in", "Fixed price", "Location", "Documents",
                 "Needs attention", "Risk check (OK/WRONG)", "Comment", "Checked by"]
_CHECK_WIDTHS = [22, 8, 24, 22, 10, 10, 10, 26, 60, 70, 14, 24, 12]


class ReviewError(RuntimeError):
    pass


@dataclass
class ReviewResult:
    out_dir: Path
    workbook: Path
    raw_data: Path
    documents: list[dict] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)   # (doc/file, kind, detail)
    text_rows: int = 0
    parsed: list[Trade] = field(default_factory=list)                 # before merging
    trades: list[Trade] = field(default_factory=list)                 # one per trade
    format_headers: list[str] = field(default_factory=list)
    format_unmapped: list[str] = field(default_factory=list)
    format_added: list[str] = field(default_factory=list)   # columns the tool added
    name_map: NameMap | None = None
    names: list[list] = field(default_factory=list)      # names sheet rows

    def counts(self) -> Counter:
        return Counter((d["folder_class"], d["type"], d["status"]) for d in self.documents)


def _clean(value):
    """Make a value safe for an Excel cell: no control characters, length-capped."""
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
        elif isinstance(value, date):
            cell.number_format = "yyyy-mm-dd"


REVIEW_ROOT = "contracts-review"


def default_out_dir(src: Path, now: datetime | None = None) -> Path:
    """``<source's folder>/contracts-review/<source name>_<yyyy-mm-dd_hhmmss>``: one master
    folder that collects every run, one new folder per run."""
    return src.parent / REVIEW_ROOT / f"{src.stem}_{(now or datetime.now()):%Y-%m-%d_%H%M%S}"


def build_review(source: str | Path, out_dir: str | Path | None = None, *,
                 format_path: str | Path | None = None,
                 us: list[str] | None = None,
                 names_path: str | Path | None = None,
                 use_names: bool = True) -> ReviewResult:
    """``names_path`` defaults to ``config/counterparty_map.csv`` when that file exists."""
    src = Path(source)
    if not src.exists():
        raise ReviewError(f"{src} does not exist")
    headers = read_format_headers(format_path) if format_path else list(DEFAULT_HEADERS)
    headers, added = _with_fixed_price(headers)
    nmap = None
    if use_names:
        if names_path is not None:
            nmap = load_name_map(names_path)
        elif DEFAULT_MAP.is_file():
            nmap = load_name_map(DEFAULT_MAP)
    if out_dir is None:
        out_dir = default_out_dir(src)
    out = check_output_location(out_dir, *([src] if src.is_dir() else []))
    out.mkdir(parents=True)
    result = ReviewResult(out, out / "review.xlsx", out / "raw-data.xlsx",
                          format_headers=headers,
                          format_unmapped=[h for h in headers if attr_for(h) is None],
                          format_added=added)
    result.name_map = nmap
    us_names = tuple(us) if us else DEFAULT_US

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
    for n, path in enumerate(files, start=1):
        rel = path.relative_to(root)
        doc_no = f"D{n:03d}"
        fclass = folder_class(rel)
        r = _add(result, text_rows, doc_no, rel.parent.as_posix(), path.name, "", fclass,
                 file_type_for(path), path.read_bytes(), str(path), us_names)
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
                 file_type_for(att.filename), att.data, f"{path}!/{att.filename}", us_names)

    if len(text_rows) > MAX_TEXT_ROWS:
        result.issues.append(("", "TEXT_TRUNCATED", f"{len(text_rows)} text rows; only the "
                              f"first {MAX_TEXT_ROWS} fit in Excel"))
        text_rows = text_rows[:MAX_TEXT_ROWS]
    result.text_rows = len(text_rows)
    result.trades = merge_trades(result.parsed)
    result.names = _name_rows(result.parsed, nmap)
    apply_name_map(result.trades, nmap)
    _price_flags(result)
    _write_raw_data(result)
    _write_review(result, text_rows, src)
    return result


def _with_fixed_price(headers: list[str]) -> tuple[list[str], list[str]]:
    """A format with a Fixed Differential column always gets a Fixed Price column right
    after it, so a swap's flat price is never lost or put in the differential."""
    attrs = [attr_for(h) for h in headers]
    if "fixed_price" in attrs or "fixed_differential" not in attrs:
        return headers, []
    i = attrs.index("fixed_differential") + 1
    return headers[:i] + ["Fixed Price"] + headers[i:], ["Fixed Price"]


def _price_flags(result: ReviewResult) -> None:
    for t in result.trades:
        if t.fixed_price is None:
            continue
        if t.price_unit and not t.price_unit.upper().startswith("USD"):
            t.flags.append(f"price in {t.price_unit}: fixed price {_num(t.fixed_price)} is "
                           "not in USD")


def _num(v) -> str:
    return format(v.normalize(), "f") if hasattr(v, "normalize") else str(v)


def _add(result: ReviewResult, text_rows: list, doc_no: str, folder: str, name: str,
         parent: str, fclass: str, ftype: str, data: bytes, path: str, us):
    r = extract_bytes(data, ftype, filename=name)
    family, trades = parse_document(r, ftype, doc_no, name, us)
    result.parsed.extend(trades)
    md = r.metadata
    result.documents.append({
        "doc": doc_no, "folder": folder, "file": name, "attached_to": parent,
        "folder_class": fclass, "type": ftype, "status": r.status,
        "parsed_as": trades[0].family if trades else ("UNRECOGNISED" if r.segments else ""),
        "trades": len(trades), "pages": md.get("pages", ""),
        "sheets": len(md["sheets"]) if "sheets" in md else "",
        "warnings": " | ".join(r.warnings), "sha256": hashlib.sha256(data).hexdigest(),
        "path": path,
    })
    if r.status != "TEXT_EXTRACTED":
        result.issues.append((doc_no, r.status, " | ".join(r.warnings) or r.status))
    if family is None and r.segments and not parent:
        result.issues.append((doc_no, "UNRECOGNISED",
                              f"{name}: no confirmation parser matches this layout; "
                              "not included in raw-data"))
    for s in r.segments:
        if s.kind not in ("attachment", "transport_header"):
            text_rows.append([doc_no, name, s.location, s.text])
    return r


def _name_rows(parsed: list[Trade], nmap: NameMap | None) -> list[list]:
    """Every party spelling seen in any document, with its master name."""
    seen: Counter = Counter()
    for t in parsed:
        for name in {t.buyer, t.seller} - {""}:
            seen[name] += 1
    rows = []
    for name, n in sorted(seen.items(), key=lambda kv: kv[0].casefold()):
        master, how = nmap.lookup(name) if nmap else (None, "")
        rows.append([name, master or "", how or ("NOT IN MAP" if nmap else "no map loaded"),
                     n])
    return rows


# --------------------------------------------------------------------------- writers

def _format_rows(result: ReviewResult):
    for t in result.trades:
        yield [cell_value(t, a) if (a := attr_for(h)) else None for h in result.format_headers]


def _check_rows(result: ReviewResult):
    for t in result.trades:
        yield [t.contract_no, t.side, t.family, t.broker, t.currency, t.price_unit,
               cell_value(t, "fixed_price"), t.location, "\n".join(t.documents),
               "\n".join(t.flags), None, None, None]


def _fill(ws, headers: list[str], rows, widths: list[int] | None = None) -> None:
    _put(ws, 1, headers)
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i).font = Font(bold=True)
        w = widths[i - 1] if widths else max(12, len(h) + 4)
        ws.column_dimensions[get_column_letter(i)].width = w
    for r, values in enumerate(rows, start=2):
        _put(ws, r, values)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _write_raw_data(result: ReviewResult) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "raw-data-format"
    _fill(ws, result.format_headers, _format_rows(result))
    _fill(wb.create_sheet("checks"), CHECK_COLUMNS, _check_rows(result), _CHECK_WIDTHS)
    wb.save(result.raw_data)


def _write_review(result: ReviewResult, text_rows: list, src: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "summary"
    lines = [f"Source: {src}  -  generated {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}",
             *format_summary(result).splitlines()]
    for i, line in enumerate(lines, start=1):
        _put(ws, i, [line])
    ws.column_dimensions["A"].width = 110
    ws["A1"].font = Font(bold=True)

    _fill(wb.create_sheet("raw-data-format"), result.format_headers, _format_rows(result))
    _fill(wb.create_sheet("checks"), CHECK_COLUMNS, _check_rows(result), _CHECK_WIDTHS)
    ev = []
    for n, t in enumerate(result.trades, start=2):
        for h in result.format_headers:
            a = attr_for(h)
            if a and cell_value(t, a) is not None:
                ev.append([n, t.contract_no, h, cell_value(t, a), t.evidence.get(a, ""),
                           t.source_doc, t.source_file])
    _fill(wb.create_sheet("evidence"), ["row", "Contract #", "column", "value",
                                        "where it came from (location: text as written)",
                                        "doc", "file"], ev, [5, 22, 18, 22, 80, 7, 50])
    _fill(wb.create_sheet("names"), ["name as written", "master name", "matched",
                                     "documents"], result.names, [60, 50, 26, 10])
    _fill(wb.create_sheet("documents"), DOC_COLUMNS,
          ([d.get(c, "") for c in DOC_COLUMNS] for d in result.documents),
          [7, 30, 50, 9, 11, 6, 15, 26, 7, 6, 7, 50, 66, 60])
    _fill(wb.create_sheet("text"), ["doc", "file", "location", "text"], text_rows,
          [8, 45, 18, 120])
    _fill(wb.create_sheet("issues"), ["doc / file", "issue", "detail"],
          (list(i) for i in result.issues), [30, 26, 120])
    wb.save(result.workbook)


_FLAG_KINDS = (("swap: no trader", "swap: no trader named on the confirmation"),
               ("price in", "price not quoted in USD"),
               ("flat price", "flat price with no index (put in Fixed Price)"),
               ("AMENDED", "amendment applied (earlier version superseded)"),
               ("also in", "duplicate document merged into one trade"),
               ("trade date is after", "trade date after start date (balance-of-period?)"),
               ("missing", "missing values"), ("cannot tell our side", "our side not found"),
               ("trade state", "trade not Finalized"), ("Side column", "side mismatch"))


def flag_kind(flag: str) -> str:
    for prefix, kind in _FLAG_KINDS:
        if flag.startswith(prefix):
            return kind + (" - DISAGREES" if "DISAGREES" in flag else "")
    return flag.split(":")[0]


def format_summary(result: ReviewResult) -> str:
    """Counts only (no names, prices or text): safe to share."""
    lines = [f"{'folder':<12} {'type':<6} {'status':<15} docs", "-" * 40]
    for (fclass, ftype, status), count in sorted(result.counts().items()):
        lines.append(f"{fclass:<12} {ftype:<6} {status:<15} {count}")
    unrec = [d for d in result.documents if d["parsed_as"] == "UNRECOGNISED"]
    by_family = Counter(t.family for t in result.trades)
    merged = sum(1 for t in result.trades for f in t.flags if f.startswith("also in "))
    amended = sum(1 for t in result.trades for f in t.flags if f.startswith("AMENDED"))
    attention = [t for t in result.trades if t.flags]
    lines += ["", f"Trades: {len(result.trades)} (from {len(result.parsed)} parsed records; "
                  f"{merged} duplicate document(s) merged, {amended} amendment(s) applied)"]
    for fam, n in by_family.most_common():
        lines.append(f"  {fam:<28} {n}")
    lines.append(f"Unrecognised documents (not in raw-data): {len(unrec)}")
    if result.name_map is None:
        lines.append("Counterparty map: not loaded (names kept as written)")
    else:
        missing = sum(1 for r in result.names if r[2] == "NOT IN MAP")
        lines.append(f"Counterparty map: {len(result.names) - missing} spelling(s) mapped, "
                     f"{missing} not in the map (see the names sheet)")
    lines.append(f"Trades with something to look at: {len(attention)} (see the checks sheet)")
    kinds = Counter(flag_kind(f) for t in result.trades for f in t.flags)
    for kind, n in kinds.most_common(8):
        lines.append(f"  {n:>3} x {kind}")
    lines += ["", f"raw-data-format: filled cells per column (of {len(result.trades)} trades):"]
    for h in result.format_headers:
        a = attr_for(h)
        filled = sum(1 for t in result.trades if a and cell_value(t, a) is not None)
        mark = ("  [header not recognised]" if a is None else
                "  [added: not in your format file]" if h in result.format_added else "")
        lines.append(f"  {h.strip():<20} {filled:>3}{mark}")
    return "\n".join(lines)
