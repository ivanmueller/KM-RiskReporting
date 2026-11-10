"""The firm's ``raw-data-format``: column headers and how each is filled from a Trade.

The target is the firm's template (``raw-data-format.xlsx``): one sheet whose first row
holds the column headers. One row is written per trade (see :mod:`riskparse.confirms`).
Dates are written as Excel dates and numbers as Excel numbers; text is copied from the
confirmation.

``--format`` reads the headers from the firm's own file, so a renamed or reordered
template is followed. A header with no known meaning is reported and left empty.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

# the firm's format file, kept with the counterparty map; --format overrides it
DEFAULT_FORMAT = Path("config") / "raw-data-format.xlsx"

DEFAULT_HEADERS = ["Contract #", "Trade Date", "Start Date", "End Date", "Term", "Trader",
                   "Counterparty", "Seller", "Quantity", "Unit", "Grade", "Primary Index",
                   "Secondary Index", "Fixed Differential", "Fixed Price", "Currency",
                   "Location", "Source"]

# format header (normalized) -> Trade attribute
HEADER_ATTR = {
    "contract #": "contract_no", "contract": "contract_no", "contract number": "contract_no",
    "trade #": "contract_no", "deal #": "contract_no", "trade date": "trade_date",
    "start date": "start_date", "end date": "end_date", "term": "term", "trader": "trader",
    "counterparty": "counterparty", "seller": "seller", "buyer": "buyer",
    "buy/sell": "side", "side": "side", "quantity": "quantity", "volume": "quantity",
    "unit": "unit", "grade": "grade", "product": "grade", "primary index": "primary_index",
    "index": "primary_index", "secondary index": "secondary_index",
    "fixed differential": "fixed_differential", "differential": "fixed_differential",
    "fixed price": "fixed_price", "flat price": "fixed_price",
    # "Currency" is the currency the price is quoted in (a -6.10 differential in USD/bbl stays
    # USD even when the trade settles in CAD); "Settlement Currency" is the payment currency.
    "currency": "price_currency", "price currency": "price_currency",
    "settlement currency": "currency", "location": "location", "broker": "broker",
    "source": "source_label", "trade source": "source_label",
}


class FormatFileError(ValueError):
    """The firm's format workbook could not be found or read."""


def norm_header(h: str) -> str:
    return " ".join(str(h).strip().lower().split())


def attr_for(header: str) -> str | None:
    return HEADER_ATTR.get(norm_header(header))


def cell_value(trade, attr: str):
    """Excel-ready value: dates stay dates, decimals become numbers, text as written."""
    value = getattr(trade, attr, None)
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, date):
        return value
    return str(value)


def resolve_format_path(path: str | Path) -> Path:
    """Accept the workbook, the same name without ``.xlsx``, or a folder holding one workbook."""
    p = Path(path)
    if p.is_dir():
        found = sorted(f for f in p.iterdir() if f.suffix.lower() in (".xlsx", ".xlsm")
                       and not f.name.startswith("~$"))
        if len(found) == 1:
            return found[0]
        names = ", ".join(f.name for f in found) or "none"
        raise FormatFileError(f"{p} is a folder with {len(found)} Excel workbook(s) ({names}); "
                              "pass the workbook itself with --format")
    if p.is_file():
        return p
    for suffix in (".xlsx", ".xlsm"):
        if p.with_name(p.name + suffix).is_file():
            return p.with_name(p.name + suffix)
    parent = p.parent if str(p.parent) else Path(".")
    nearby = sorted(f.name for f in parent.glob("*") if "format" in f.name.lower()) \
        if parent.is_dir() else []
    hint = f" Similar names in {parent.resolve()}: {', '.join(nearby)}." if nearby else ""
    raise FormatFileError(f"format file not found: {p}.{hint}")


def read_format_headers(path: str | Path) -> list[str]:
    """Header row of the first sheet of the firm's format workbook."""
    path = resolve_format_path(path)
    try:
        wb = load_workbook(path, read_only=True)
    except Exception as exc:
        raise FormatFileError(f"{path} could not be opened as an Excel workbook: {exc}") from exc
    try:
        ws = wb.worksheets[0]
        row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    finally:
        wb.close()
    headers = [str(v) for v in row if v is not None and str(v).strip()]
    if not headers:
        raise FormatFileError(f"{path}: first row of the first sheet has no column headers")
    return headers
