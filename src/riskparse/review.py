"""Step 1 in one command: ``riskparse review <zip or folder>``.

Reads every confirmation and writes the trades in the firm's ``raw-data-format``.

``raw-data.xlsx``:

* **raw-data-format**: the firm's columns, one row per trade (dates as dates, numbers as
  numbers).
* **checks**: per trade, our buy/sell, settlement currency, price quote unit, fixed
  price, source family, supporting documents and anything that needs a look (amendments,
  duplicates, missing values), with columns for Risk to mark each row OK or WRONG.

``review.xlsx``: the same two sheets plus **evidence** (for every filled cell, the exact
place in the document and the text it was read from), **trade sheet** (each confirmation
against the master trade sheet, and trade sheet deals with no confirmation), **financial**
(the bank swaps: rolls and forward hedges), **documents** (which parser read each file),
**text** (all readable text with locations) and **issues**.

``dashboard.html``: the same results as a page to read (see :mod:`riskparse.dashboard`).

``Risk Report - CTT Position Summary - <date>.xlsx`` and ``.pdf``: the daily risk report
page (see :mod:`riskparse.riskreport`).

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
from riskparse.project import project_file
from riskparse.rawformat import (
    DEFAULT_FORMAT,
    DEFAULT_HEADERS,
    attr_for,
    cell_value,
    read_format_headers,
    resolve_format_path,
)
from riskparse.sheetcheck import (
    SheetCheck,
    check_against_sheet,
    classify_financial,
    is_financial,
    month_label,
    month_of,
    sheet_only_trades,
    tag_locations,
)
from riskparse.tradesheet import TradeSheetError, load_trade_sheet

MAX_CELL = 32_000          # Excel's limit is 32,767 characters per cell
MAX_TEXT_ROWS = 1_000_000  # Excel's limit is 1,048,576 rows per sheet

DOC_COLUMNS = ["doc", "folder", "file", "attached_to", "folder_class", "type", "status",
               "parsed_as", "trades", "pages", "sheets", "warnings", "sha256", "path"]
CHECK_COLUMNS = ["Contract #", "Our side", "Source", "Broker / issuer", "Settlement currency",
                 "Price quoted in", "Fixed price", "Location", "Documents",
                 "Needs attention", "Trade sheet", "Risk check (OK/WRONG)", "Comment",
                 "Checked by"]
_CHECK_WIDTHS = [22, 8, 24, 22, 10, 10, 10, 26, 60, 70, 40, 14, 24, 12]
SHEET_COLUMNS = ["Status", "Contract #", "Trade sheet contract", "Trade sheet row", "Side",
                 "Counterparty", "Trade sheet counterparty", "Grade", "Delivery month",
                 "Confirmed volume", "Trade sheet volume (m3)", "Differential (confirmation)",
                 "Differential (trade sheet)", "Trade date (confirmation)",
                 "Trade date (trade sheet)", "Broker (confirmation)", "Broker (trade sheet)",
                 "Settlement currency (confirmation)", "Currency (trade sheet)",
                 "Differences", "Notes"]
FINANCIAL_COLUMNS = ["Contract #", "Type", "Paired with", "Side", "Month", "Quantity", "Unit",
                     "Fixed price", "Price unit", "Counterparty", "Trade date", "What it is"]


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
    format_file: Path | None = None                          # None: built-in columns
    name_map: NameMap | None = None
    names: list[list] = field(default_factory=list)      # names sheet rows
    sheet_checks: list[SheetCheck] = field(default_factory=list)   # one per trade sheet read
    sheet_errors: dict[str, str] = field(default_factory=dict)      # label -> why not read
    financial: list = field(default_factory=list)        # FinancialRow per bank swap
    dashboard: Path | None = None
    sheet_trades: list[Trade] = field(default_factory=list)  # Evergreen / Term / Direct rows
    direct_confirmations: dict = field(default_factory=dict)  # id(trade) -> Confirmation
    confirm_log: Path | None = None                          # Direct trade confirmations.xlsx
    report_xlsx: Path | None = None                      # daily risk report (Excel)
    report_pdf: Path | None = None                       # daily risk report (PDF)
    source: str = ""
    build: str = ""

    def counts(self) -> Counter:
        return Counter((d["folder_class"], d["type"], d["status"]) for d in self.documents)

    @property
    def book(self) -> list[Trade]:
        """Every trade in raw-data: the confirmed ones, then the trade sheet rows with no
        contract (Evergreen agreements and Direct trades on ICE)."""
        return self.trades + self.sheet_trades

    @property
    def sheet_check(self) -> SheetCheck | None:
        """The first trade sheet compared (the monthly one when both are given)."""
        return self.sheet_checks[0] if self.sheet_checks else None

    @property
    def sheet_error(self) -> str:
        return "; ".join(f"{k}: {v}" for k, v in self.sheet_errors.items())


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


REVIEW_ROOT = "contracts-data"
MONTHLY_LABEL = "monthly trade sheet"
# Bump with every change to the output, so a printed summary shows which code produced it.
REVIEW_BUILD = "2026-11-10f (Term as Evergreen; broker month rule; direct confirmations)"


def default_out_dir(src: Path, now: datetime | None = None) -> Path:
    """``<source's folder>/contracts-data/<source name>_<yyyy-mm-dd_hhmmss>``: one master
    folder that collects every run, one new folder per run."""
    return src.parent / REVIEW_ROOT / f"{src.stem}_{(now or datetime.now()):%Y-%m-%d_%H%M%S}"


def build_review(source: str | Path, out_dir: str | Path | None = None, *,
                 format_path: str | Path | None = None,
                 us: list[str] | None = None,
                 names_path: str | Path | None = None,
                 use_names: bool = True,
                 results_root: str | Path | None = None,
                 trade_sheet: str | Path | None = None) -> ReviewResult:
    """``format_path`` defaults to ``config/raw-data-format.xlsx`` and ``names_path`` to
    ``config/counterparty_map.csv``, looked for in the current folder, then the repository.

    ``results_root``: the folder that collects every run (a new dated folder is made in it
    each time); default ``contracts-data`` next to the source. ``out_dir`` overrides both.
    ``trade_sheet``: the monthly trade sheet folder (``<Month> Trading <Year>`` workbooks,
    Trading tab), or one workbook: the desk's record the confirmations are checked against."""
    src = Path(source)
    if not src.exists():
        raise ReviewError(f"{src} does not exist")
    if format_path is None:
        format_path = project_file(DEFAULT_FORMAT)
    headers = read_format_headers(format_path) if format_path else list(DEFAULT_HEADERS)
    headers, added = _with_price_columns(headers)
    nmap = None
    if use_names:
        if names_path is None:
            names_path = project_file(DEFAULT_MAP)
        if names_path is not None:
            nmap = load_name_map(names_path)
    if out_dir is None:
        out_dir = (Path(results_root) / f"{src.stem}_{datetime.now():%Y-%m-%d_%H%M%S}"
                   if results_root is not None else default_out_dir(src))
        n, base = 2, Path(out_dir)
        while Path(out_dir).exists():            # two runs in the same second
            out_dir, n = base.with_name(f"{base.name}-{n}"), n + 1
    out = check_output_location(out_dir, *([src] if src.is_dir() else []))
    out.mkdir(parents=True)
    result = ReviewResult(out, out / "review.xlsx", out / "raw-data.xlsx",
                          format_headers=headers,
                          format_unmapped=[h for h in headers if attr_for(h) is None],
                          format_added=added,
                          format_file=resolve_format_path(format_path) if format_path else None)
    result.name_map = nmap
    result.source, result.build = str(src), REVIEW_BUILD
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
    result.financial = classify_financial([t for t in result.trades if is_financial(t)])
    months = {month_of(t.start_date) for t in result.trades
              if t.start_date and not is_financial(t)}
    for label, location, monthly in ((MONTHLY_LABEL, trade_sheet, True),):
        if location is None:
            continue
        try:
            sheet = load_trade_sheet(location, monthly=monthly, months=months, label=label)
            check = check_against_sheet(result.trades, sheet, nmap)
            result.sheet_checks.append(check)
            if monthly:
                result.sheet_trades = sheet_only_trades(check, nmap)
        except TradeSheetError as exc:
            result.sheet_errors[label] = str(exc)
            result.issues.append((str(location), "TRADE_SHEET_NOT_READ", f"{label}: {exc}"))
    tag_locations(result.trades)
    direct = [t for t in result.sheet_trades if "Direct" in t.family]
    if direct:
        from riskparse.confirmlog import DISPUTED, FILE, ConfirmLogError, update_log
        folder = Path(results_root) if results_root is not None else out.parent
        try:
            result.direct_confirmations, note = update_log(folder, direct)
            result.confirm_log = folder / FILE
            if note:
                result.issues.append((FILE, "CONFIRMATION_REGISTER_NOT_SAVED", note))
        except ConfirmLogError as exc:
            result.issues.append((FILE, "CONFIRMATION_REGISTER_NOT_READ", str(exc)))
        for t in direct:
            c = result.direct_confirmations.get(id(t))
            if c is not None and c.status == DISPUTED:
                t.flags.append(f"direct trade: the counterparty does not agree ({c.text()})")
    for f in result.financial:
        f.trade.flags.append(f"financial ({f.kind.lower()}): bank swap, not on the trade "
                             "sheets by design")
    _write_raw_data(result)
    _write_review(result, text_rows, src)
    from riskparse.dashboard import build_data, write_dashboard
    from riskparse.riskreport import write_risk_report
    data = build_data(result)
    try:
        result.report_xlsx, result.report_pdf = write_risk_report(result, data, out)
    except Exception as exc:          # the report must never stop the run's other outputs
        result.issues.append(("risk report", "RISK_REPORT_NOT_WRITTEN", str(exc)))
    result.dashboard = write_dashboard(result, out, data)
    return result


# columns every output gets, if the format file has the price columns but not these
_PRICE_COLUMNS = (("Fixed Price", "fixed_price", "fixed_differential"),
                  ("Currency", "price_currency", "fixed_price"),
                  ("Location", "location", "price_currency"),
                  ("Source", "source_label", "location"))


def _with_price_columns(headers: list[str]) -> tuple[list[str], list[str]]:
    """A format with Fixed Differential always gets Fixed Price right after it, then
    Currency, so a swap's flat price is never lost and every price has its currency."""
    headers, added = list(headers), []
    for header, attr, after in _PRICE_COLUMNS:
        attrs = [attr_for(h) for h in headers]
        if attr in attrs or after not in attrs:
            continue
        headers.insert(attrs.index(after) + 1, header)
        added.append(header)
    return headers, added


def _price_flags(result: ReviewResult) -> None:
    for t in result.trades:
        if (t.fixed_price is not None or t.fixed_differential is not None) \
                and not t.price_currency:
            t.flags.append("price currency not stated on the confirmation: Currency left blank")


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
    for t in result.book:
        yield [cell_value(t, a) if (a := attr_for(h)) else None for h in result.format_headers]


def _short(label: str) -> str:
    return label.split()[0].capitalize()          # "Monthly"


def _sheet_status(result: ReviewResult, t: Trade) -> str:
    fin = next((f for f in result.financial if f.trade is t), None)
    if fin is not None:
        return f"FINANCIAL: {fin.kind}" + (f" with {fin.partner.contract_no}" if fin.partner
                                            else "")
    if any(t is x for x in result.sheet_trades):
        kind = t.family.removeprefix("Trade sheet - ")
        where = t.documents[0] if t.documents else ""
        if kind.startswith("Direct"):
            c = result.direct_confirmations.get(id(t))
            human = c.text() if c is not None else "not recorded (register not available)"
            return (f"TRADE SHEET ONLY - Direct (ICE) ({where}): no broker confirm - the "
                    f"trade sheet is taken as correct. Human confirmation: {human}")
        return (f"TRADE SHEET ONLY - {kind} ({where}): standing agreement - no contract in "
                "the contracts folder")
    lines = []
    for c in result.sheet_checks:
        row = c.for_trade(t)
        if row is not None:
            lines.append(f"{_short(c.sheet.label)}: {row.status}"
                         + (f" {row.sheet.internal_contract or ''} ({row.sheet.where})"
                            if row.sheet else ""))
    for label, err in result.sheet_errors.items():
        lines.append(f"{_short(label)}: not read ({err})")
    return "\n".join(lines) or "not compared"


def _check_rows(result: ReviewResult):
    for t in result.book:
        yield [t.contract_no, t.side, t.source_label, t.broker, t.currency, t.price_unit,
               cell_value(t, "fixed_price"), t.location, "\n".join(t.documents),
               "\n".join(t.flags), _sheet_status(result, t), None, None, None]


def _sheet_rows(check: SheetCheck):
    for r in check.rows:
        t, s = r.trade, r.sheet
        yield [r.status, t.contract_no if t else None,
               s.internal_contract if s else None, s.where if s else None,
               (t or s).side, t.counterparty if t else (s.master_counterparty or None),
               s.counterparty if s else None, (t or s).grade,
               month_label(month_of(t.start_date) if t else s.delivery_month),
               (f"{cell_value(t, 'quantity')} {t.unit}" if t and t.quantity is not None
                else None),
               cell_value(s, "volume") if s else None,
               cell_value(t, "fixed_differential") if t else None,
               (cell_value(s, "differential") if s and s.differential is not None
                else (s.differential_text or None) if s else None),
               t.trade_date if t else None, s.trade_date if s else None,
               t.family if t else None, s.broker if s else None,
               t.currency if t else None, s.currency if s else None,
               "\n".join(r.differences), "\n".join(r.notes)]


def _financial_rows(result: ReviewResult):
    for f in result.financial:
        t = f.trade
        yield [t.contract_no, f.kind, f.partner.contract_no if f.partner else None, t.side,
               t.term, cell_value(t, "quantity"), t.unit, cell_value(t, "fixed_price"),
               t.price_unit, t.counterparty, t.trade_date, f.note]


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
    for n, t in enumerate(result.book, start=2):
        for h in result.format_headers:
            a = attr_for(h)
            if a and cell_value(t, a) is not None:
                ev.append([n, t.contract_no, h, cell_value(t, a), t.evidence.get(a, ""),
                           t.source_doc, t.source_file])
    _fill(wb.create_sheet("evidence"), ["row", "Contract #", "column", "value",
                                        "where it came from (location: text as written)",
                                        "doc", "file"], ev, [5, 22, 18, 22, 80, 7, 50])
    for check in result.sheet_checks:
        _fill(wb.create_sheet(check.sheet.label[:31]), SHEET_COLUMNS, _sheet_rows(check),
              [26, 14, 16, 30, 6, 34, 18, 8, 9, 16, 12, 11, 11, 12, 12, 22, 12, 10, 10, 60, 60])
    if result.financial:
        _fill(wb.create_sheet("financial"), FINANCIAL_COLUMNS, _financial_rows(result),
              [14, 14, 14, 6, 9, 10, 6, 10, 10, 26, 12, 90])
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
               ("financial (roll)", "bank swap: roll (not on the trade sheets by design)"),
               ("financial (forward hedge)", "bank swap: forward hedge (not on the trade "
                                             "sheets by design)"),
               ("financial (swap)", "bank swap: single swap - check what it hedges"),
               ("price currency", "price currency not stated"),
               ("counterparty '", "counterparty not in the counterparty map"),
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


def _sheet_summary(result: ReviewResult) -> list[str]:
    from riskparse import sheetcheck as sc
    fin = Counter(f.kind for f in result.financial)
    lines = []
    if not result.sheet_checks and not result.sheet_errors:
        lines.append("Trade sheets: not compared (no trade sheet folder given)")
    for label, err in result.sheet_errors.items():
        lines.append(f"{label[0].upper()}{label[1:]}: NOT READ - {err}")
    for c in result.sheet_checks:
        lines.append(f"{c.sheet.label[0].upper()}{c.sheet.label[1:]}: {c.sheet.names} "
                     f"({len(c.sheet.trades)} rows from {len(c.sheet.sheets_read)} tab(s))")
        if c.sheet.missing:
            lines.append(f"  not in the folder yet: {', '.join(c.sheet.missing)}")
        lines.append(f"  physical confirmations matched           "
                     f"{c.count(sc.MATCHED) + c.count(sc.DIFFERENCES)}"
                     f" ({c.count(sc.DIFFERENCES)} with differences)")
        lines.append(f"  confirmations not on it                   {c.count(sc.NOT_ON_SHEET)}")
        if c.count(sc.MONTH_NOT_COVERED):
            lines.append(f"  confirmations for months it does not hold "
                         f"{c.count(sc.MONTH_NOT_COVERED)}")
        lines.append(f"  brokered deals with no broker confirm     "
                     f"{c.count(sc.NO_CONFIRMATION)} (in the confirmation period)")
        lines.append(f"  trade sheet only, added to raw-data       "
                     f"{c.count(sc.EVERGREEN)} Evergreen/Term, {c.count(sc.DIRECT_ICE)} "
                     "Direct (ICE)")
    if result.direct_confirmations:
        from riskparse.confirmlog import AWAITING, CONFIRMED, DISPUTED
        st = Counter(c.status for c in result.direct_confirmations.values())
        lines.append(f"Direct (ICE) trades confirmed by a person: {st[CONFIRMED]} of "
                     f"{len(result.direct_confirmations)} ({st[DISPUTED]} the counterparty "
                     f"disagrees with, {st[AWAITING]} awaiting) - register: "
                     f"{result.confirm_log.name if result.confirm_log else 'not available'}")
    lines.append(f"Bank swaps: {fin.get(sc.ROLL, 0) // 2} roll(s) of two swaps, "
                 f"{fin.get(sc.FORWARD_HEDGE, 0)} forward hedge(s), {fin.get(sc.SWAP, 0)} other")
    return lines


def format_summary(result: ReviewResult) -> str:
    """Counts only (no names, prices or text): safe to share."""
    lines = [f"riskparse review build {REVIEW_BUILD}", "",
             f"{'folder':<12} {'type':<6} {'status':<15} docs", "-" * 40]
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
        lines.append(f"  map file: {result.name_map.path.resolve()}")
    lines += _sheet_summary(result)
    lines.append(f"Trades with something to look at: {len(attention)} (see the checks sheet)")
    kinds = Counter(flag_kind(f) for t in result.trades for f in t.flags)
    for kind, n in kinds.most_common(8):
        lines.append(f"  {n:>3} x {kind}")
    fmt = result.format_file.resolve() if result.format_file else "built-in columns"
    lines += ["", f"Format: {fmt}",
              f"raw-data-format: filled cells per column (of {len(result.book)} trades: "
              f"{len(result.trades)} confirmed, {len(result.sheet_trades)} trade sheet only):"]
    for h in result.format_headers:
        a = attr_for(h)
        filled = sum(1 for t in result.book if a and cell_value(t, a) is not None)
        mark = ("  [header not recognised]" if a is None else
                "  [added: not in your format file]" if h in result.format_added else "")
        lines.append(f"  {h.strip():<20} {filled:>3}{mark}")
    return "\n".join(lines)
