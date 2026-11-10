"""The master trade sheet (the desk's "<Month> Trading <year>" workbook): read, never changed.

The trade sheet is the human record of every physical deal. Its tabs follow the trading
schedule layout used in the risk report (``Nov CTS``, ``Oct TS MTR``, ...):

    Internal Contract | Credit | Date | Counter Party | Counterparty Trader | Trader |
    Buy/Sale | Volume | Grade | Currency | Pay Date | Location | Inj Month | Blend Month |
    Price Basis | Index Type | Diff'l | ... | Broker / Direct | Comments | Stream | ...

Columns are found by their header text, on any tab and in any position (the tabs do not
all put them in the same column), so a re-ordered or extended sheet still reads. A tab
without the core headers (Counter Party, Buy/Sale, Volume, Grade, Inj Month) is skipped and
listed. Volumes are m3 and signed (sales negative). ``Date`` holds the trade date for a
spot deal and a word ("Evergreen", "Term", "Spot") for standing agreements.

Two kinds of source use this reader:

* the **monthly trade sheet** folder, holding workbooks named ``<Month> Trading <Year>``
  (``October Trading 2026.xlsm``), one per delivery month. Only the ``Trading`` tab is read
  (a workbook without one: every tab with the headers).
  The current month's workbook is used, plus the workbook of every delivery month the
  confirmations cover, when the folder has it. A folder with a single workbook of any name
  is used as it is.
* any other trade sheet (Libby's): one workbook, every tab with the headers above.

A tab ends at a repeated header row: the "what if" block below the deals is not read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

WORKBOOK_SUFFIXES = (".xlsx", ".xlsm", ".xls")
MONTHLY_TABS = ("Trading",)
_MONTHLY_NAME = re.compile(r"^(?P<month>[A-Za-z]+)\.?\s+Trading\s+(?P<year>\d{4})\b", re.I)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], start=1)}
REQUIRED = ("counterparty", "side", "volume", "grade", "delivery_month")

# normalised header text -> field
HEADERS = {
    "internal contract": "internal_contract", "contract": "internal_contract",
    "date": "trade_date", "trade date": "trade_date",
    "counter party": "counterparty", "counterparty": "counterparty",
    "trader": "trader", "buy/sale": "side", "buy/sell": "side",
    "volume": "volume", "grade": "grade", "currency": "currency",
    "location": "location", "inj month": "delivery_month", "delivery month": "delivery_month",
    "price basis": "price_basis", "index type": "index_type",
    "diff'l": "differential", "diffl": "differential", "differential": "differential",
    "broker / direct": "broker", "broker/direct": "broker", "broker": "broker",
    "comments": "comments", "stream": "stream", "credit": "credit",
}
HEADER_SEARCH_ROWS = 15
EXCEL_EPOCH = date(1899, 12, 30)


class TradeSheetError(ValueError):
    """The trade sheet could not be found or read."""


@dataclass
class SheetTrade:
    sheet: str
    row: int
    book: str = ""                      # the workbook's name, without the extension
    internal_contract: str = ""
    trade_date: date | None = None
    date_text: str = ""                 # "Evergreen", "Term", "Spot" when not a date
    counterparty: str = ""
    trader: str = ""
    side: str = ""                      # BUY / SELL
    volume: Decimal | None = None       # m3, positive
    grade: str = ""
    currency: str = ""                  # USD / CAD
    location: str = ""
    delivery_month: date | None = None  # first day of the month
    price_basis: str = ""               # Fixed / Index
    index_type: str = ""
    differential: Decimal | None = None
    differential_text: str = ""         # e.g. "34% of WTI" when not a number
    broker: str = ""
    stream: str = ""
    comments: str = ""
    master_counterparty: str = ""       # after the counterparty map

    @property
    def where(self) -> str:
        return (f"{self.book}: " if self.book else "") + f"{self.sheet}!row {self.row}"

    @property
    def signed_volume(self) -> Decimal | None:
        if self.volume is None:
            return None
        return -self.volume if self.side == "SELL" else self.volume


@dataclass
class TradeSheet:
    paths: list[Path]
    modified: datetime
    trades: list[SheetTrade] = field(default_factory=list)
    sheets_read: list[str] = field(default_factory=list)
    sheets_skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # monthly workbooks looked for, absent
    label: str = "trade sheet"

    @property
    def path(self) -> Path | None:
        return self.paths[0] if self.paths else None

    @property
    def named(self) -> str:
        """For sentences: "the monthly trade sheet", "Libby's trade sheet"."""
        return self.label if "'s " in self.label else f"the {self.label}"

    @property
    def names(self) -> str:
        return ", ".join(p.name for p in self.paths)


def norm_header(text) -> str:
    return " ".join(str(text or "").replace("\n", " ").strip().lower().split())


def workbooks_in(folder: Path) -> list[Path]:
    return sorted(f for f in folder.iterdir() if f.is_file()
                  and f.suffix.lower() in WORKBOOK_SUFFIXES
                  and not f.name.startswith(("~$", ".")))


def monthly_name(month: date) -> str:
    return f"{month:%B} Trading {month:%Y}"


def monthly_month(path: Path) -> date | None:
    """``October Trading 2026.xlsm`` -> 2026-10-01 (full or three-letter month)."""
    m = _MONTHLY_NAME.match(path.stem)
    if not m:
        return None
    name = m["month"].lower()
    month = _MONTHS.get(name) or next((v for k, v in _MONTHS.items()
                                       if len(name) >= 3 and k.startswith(name)), None)
    return date(int(m["year"]), month, 1) if month else None


def select_monthly(folder: str | Path, months=(), today: date | None = None
                   ) -> tuple[list[Path], list[str]]:
    """The monthly workbooks to read: the current month's and those of ``months`` (the
    confirmations' delivery months) that the folder has; and the names looked for but
    absent. A folder with one workbook of any other name: that workbook."""
    p = Path(folder)
    if p.is_file():
        return [p], []
    if not p.is_dir():
        raise TradeSheetError(f"trade sheet folder not found: {p}")
    files = workbooks_in(p)
    named = {}
    for f in files:
        m = monthly_month(f)
        if m and m not in named:
            named[m] = f
    wanted = sorted({(today or date.today()).replace(day=1)} | {m.replace(day=1) for m in months})
    chosen = [named[m] for m in wanted if m in named]
    missing = [monthly_name(m) for m in wanted if m not in named]
    if chosen:
        return chosen, missing
    if len(files) == 1:
        return files, []
    found = ", ".join(f.name for f in files) or "no workbooks"
    raise TradeSheetError(f"{p} has no workbook named {' or '.join(missing)} (found: {found})")


def find_trade_sheet(location: str | Path) -> Path:
    """``location`` is the workbook, or the folder that holds exactly one workbook."""
    p = Path(location)
    if p.is_file():
        return p
    if not p.is_dir():
        raise TradeSheetError(f"trade sheet folder not found: {p}")
    found = workbooks_in(p)
    if len(found) != 1:
        names = ", ".join(f.name for f in found) or "none"
        raise TradeSheetError(f"{p} should hold exactly one trade sheet workbook; found "
                              f"{len(found)} ({names})")
    return found[0]


def _excel_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:     # an Excel serial date
        return EXCEL_EPOCH + timedelta(days=int(value))
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y", "%b-%y", "%b %Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _number(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"[-+(]?\d+(\.\d+)?\)?", text):
        return None
    try:
        return Decimal(text.strip("()")) * (-1 if text.startswith("(") else 1)
    except InvalidOperation:
        return None


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _currency(value) -> str:
    t = _text(value).upper()
    return {"CAN": "CAD", "C$": "CAD", "CDN": "CAD", "US": "USD", "US$": "USD"}.get(t, t)


def _chosen(names: list[str], tabs) -> list[str]:
    """The tabs to read: those named in ``tabs`` when the workbook has them, else all."""
    if tabs is None:
        return names
    wanted = {t.casefold() for t in tabs}
    hit = [n for n in names if n.strip().casefold() in wanted]
    return hit or names


def _rows(path: Path, tabs=None):
    """(sheet name, list of row tuples) for every sheet (or those named in ``tabs``),
    cached values only."""
    if path.suffix.lower() == ".xls":
        import xlrd
        book = xlrd.open_workbook(str(path))
        keep = set(_chosen(book.sheet_names(), tabs))
        for sh in book.sheets():
            if sh.name not in keep:
                continue
            rows = []
            for r in range(sh.nrows):
                row = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                    elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        row.append(None)
                    else:
                        row.append(cell.value)
                rows.append(tuple(row))
            yield sh.name, rows
        return
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        keep = set(_chosen(wb.sheetnames, tabs))
        for ws in wb.worksheets:
            if ws.title in keep:
                yield ws.title, list(ws.iter_rows(values_only=True))
    finally:
        wb.close()


def _header_map(rows) -> tuple[int, dict[str, int]] | None:
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        cols: dict[str, int] = {}
        for j, cell in enumerate(row):
            f = HEADERS.get(norm_header(cell))
            if f and f not in cols:
                cols[f] = j
        if all(f in cols for f in REQUIRED):
            return i, cols
    return None


def load_trade_sheet(location: str | Path, *, monthly: bool = False, months=(),
                     today: date | None = None, label: str = "trade sheet") -> TradeSheet:
    """Read a trade sheet. ``monthly``: ``location`` is the monthly folder (see the module
    docstring) and only the Trading tab is read; otherwise one workbook, every trade tab."""
    if monthly:
        paths, missing = select_monthly(location, months, today)
        tabs = MONTHLY_TABS
    else:
        paths, missing, tabs = [find_trade_sheet(location)], [], None
    sheet = TradeSheet(paths, max(datetime.fromtimestamp(p.stat().st_mtime) for p in paths),
                       missing=missing, label=label)
    for path in paths:
        book = path.stem if monthly or len(paths) > 1 else ""
        try:
            read_any = False
            for name, rows in _rows(path, tabs):
                found = _header_map(rows)
                title = f"{name} ({path.stem})" if book else name
                if found is None:
                    sheet.sheets_skipped.append(title)
                    continue
                read_any = True
                sheet.sheets_read.append(title)
                hdr, cols = found
                for i, row in enumerate(rows[hdr + 1:], start=hdr + 2):
                    def get(f, row=row, cols=cols):
                        j = cols.get(f)
                        return row[j] if j is not None and j < len(row) else None
                    if norm_header(get("side")) in ("buy/sale", "buy/sell"):
                        break                   # a repeated header: the "what if" block
                    t = _sheet_trade(name, i, get)
                    if t is not None:
                        t.book = book
                        sheet.trades.append(t)
        except TradeSheetError:
            raise
        except Exception as exc:    # a locked, damaged or unsupported workbook: say which
            raise TradeSheetError(f"could not read the trade sheet {path.name}: {exc}") from exc
        if not read_any:
            raise TradeSheetError(f"{path.name} has no tab with the trade sheet headers "
                                  "(Counter Party, Buy/Sale, Volume, Grade, Inj Month)")
    return sheet


def _sheet_trade(sheet: str, row: int, get) -> SheetTrade | None:
    cpty, side, grade = _text(get("counterparty")), _text(get("side")).upper(), _text(get("grade"))
    vol = _number(get("volume"))
    if side not in ("BUY", "SELL", "SALE", "B", "S") or not (cpty or vol):
        return None                          # blank, formula-only or note rows
    month = _excel_date(get("delivery_month"))
    if month is None or "WHAT IF" in _text(get("stream")).upper():
        return None                          # totals and scenario rows have no month
    side = "BUY" if side.startswith("B") else "SELL"
    raw_date = get("trade_date")
    t = SheetTrade(
        sheet=sheet, row=row,
        internal_contract=_text(get("internal_contract")),
        trade_date=_excel_date(raw_date),
        counterparty=cpty, trader=_text(get("trader")), side=side,
        volume=abs(vol) if vol is not None else None,
        grade=grade, currency=_currency(get("currency")), location=_text(get("location")),
        delivery_month=month,
        price_basis=_text(get("price_basis")), index_type=_text(get("index_type")),
        broker=_text(get("broker")), stream=_text(get("stream")),
        comments=_text(get("comments")))
    if t.trade_date is None:
        t.date_text = _text(raw_date)
    if t.delivery_month is not None:
        t.delivery_month = t.delivery_month.replace(day=1)
    diff = get("differential")
    t.differential = _number(diff)
    if t.differential is None:
        t.differential_text = _text(diff)
    return t
