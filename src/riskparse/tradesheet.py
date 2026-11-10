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

The folder holds exactly one workbook; its name does not matter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

WORKBOOK_SUFFIXES = (".xlsx", ".xlsm", ".xls")
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
        return f"{self.sheet}!row {self.row}"

    @property
    def signed_volume(self) -> Decimal | None:
        if self.volume is None:
            return None
        return -self.volume if self.side == "SELL" else self.volume


@dataclass
class TradeSheet:
    path: Path
    modified: datetime
    trades: list[SheetTrade] = field(default_factory=list)
    sheets_read: list[str] = field(default_factory=list)
    sheets_skipped: list[str] = field(default_factory=list)


def norm_header(text) -> str:
    return " ".join(str(text or "").replace("\n", " ").strip().lower().split())


def find_trade_sheet(location: str | Path) -> Path:
    """``location`` is the workbook, or the folder that holds exactly one workbook."""
    p = Path(location)
    if p.is_file():
        return p
    if not p.is_dir():
        raise TradeSheetError(f"trade sheet folder not found: {p}")
    found = sorted(f for f in p.iterdir() if f.is_file()
                   and f.suffix.lower() in WORKBOOK_SUFFIXES
                   and not f.name.startswith(("~$", ".")))
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


def _rows(path: Path):
    """(sheet name, list of row tuples) for every sheet, cached values only."""
    if path.suffix.lower() == ".xls":
        import xlrd
        book = xlrd.open_workbook(str(path))
        for sh in book.sheets():
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
        for ws in wb.worksheets:
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


def load_trade_sheet(location: str | Path) -> TradeSheet:
    path = find_trade_sheet(location)
    try:
        sheet = TradeSheet(path, datetime.fromtimestamp(path.stat().st_mtime))
        for name, rows in _rows(path):
            found = _header_map(rows)
            if found is None:
                sheet.sheets_skipped.append(name)
                continue
            sheet.sheets_read.append(name)
            hdr, cols = found
            for i, row in enumerate(rows[hdr + 1:], start=hdr + 2):
                def get(f, row=row, cols=cols):
                    j = cols.get(f)
                    return row[j] if j is not None and j < len(row) else None
                t = _sheet_trade(name, i, get)
                if t is not None:
                    sheet.trades.append(t)
    except TradeSheetError:
        raise
    except Exception as exc:    # a locked, damaged or unsupported workbook: say which
        raise TradeSheetError(f"could not read the trade sheet {path.name}: {exc}") from exc
    return sheet


def _sheet_trade(sheet: str, row: int, get) -> SheetTrade | None:
    cpty, side, grade = _text(get("counterparty")), _text(get("side")).upper(), _text(get("grade"))
    vol = _number(get("volume"))
    if side not in ("BUY", "SELL", "SALE", "B", "S") or not (cpty or vol):
        return None                          # blank, formula-only or note rows
    side = "BUY" if side.startswith("B") else "SELL"
    raw_date = get("trade_date")
    t = SheetTrade(
        sheet=sheet, row=row,
        internal_contract=_text(get("internal_contract")),
        trade_date=_excel_date(raw_date),
        counterparty=cpty, trader=_text(get("trader")), side=side,
        volume=abs(vol) if vol is not None else None,
        grade=grade, currency=_currency(get("currency")), location=_text(get("location")),
        delivery_month=_excel_date(get("delivery_month")),
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
