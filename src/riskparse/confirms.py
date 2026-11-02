"""Template parsers for the firm's confirmation families (one row per trade).

Built from the October 2026 confirmations. Each family has a fixed layout, so values are
read from known labels in known places, never guessed:

========================  ==========================================================
Family                    Recognised by
========================  ==========================================================
Modern Commodities xlsx   "Trade Report" sheet, header row with Trade Number / Bid Legal
                          Name / Offer Legal Name; one trade per data row
One Exchange (OX) PDF     "BROKERED TRADE CONFIRMATION" ... "One Exchange Corp"
Marex PDF                 "Marex Reference" (label line, value on the next line)
Marex daily summary xls   header row Reference / Deal Date / Buy Company / Sell Company
CalRock email (.msg)      "Trade confirmation" block with "Label : value" lines
                          (also when forwarded)
ISDA swap PDF (NBC)       "COMMODITY SWAP TRANSACTION" with Fixed / Floating Price Payer
========================  ==========================================================

Conventions (from the firm's raw-data-format):

* **Counterparty** is the party that is not us; **Seller** is the selling party's name;
  **Trader** is our trader; buy/sell is from our side.
* **Fixed Differential** is the physical price differential to the index. A swap's fixed
  price is not a differential: it is reported on the Checks sheet, not in that column.
* Dates and numbers are parsed with the exact format each family uses; anything that does
  not parse is left blank and flagged, never guessed.
* A unit is normalised only when the meaning is certain: "m3", "cbm" or "M3 / Month" for a
  single-month term is ``m3/month``. Otherwise the unit is kept as written and flagged.
* The same trade from two documents (Marex PDF and daily summary) becomes one row, with the
  confirmation preferred and any disagreement flagged. An ``UPDATED`` Modern Commodities
  report replaces the earlier one, and the changed fields are listed.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl.utils.cell import column_index_from_string, get_column_letter

from riskparse.extract.base import ExtractionResult

DEFAULT_US = ("Kingston Marketing",)

_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"(?P<sign>[-+])?\s*(?:[A-Z]{3}\s*)?\$?\s*(?P<num>[0-9][0-9,]*(?:\.[0-9]+)?)")
_CELL = re.compile(r"^(?P<sheet>.*)!(?P<col>[A-Z]{1,3})(?P<row>[0-9]+)$")


@dataclass
class Trade:
    family: str
    source_doc: str
    source_file: str
    contract_no: str = ""
    trade_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    term: str = ""
    trader: str = ""
    counterparty: str = ""
    seller: str = ""
    buyer: str = ""
    side: str = ""                      # BUY / SELL from our side
    quantity: Decimal | None = None
    unit: str = ""
    grade: str = ""
    primary_index: str = ""
    secondary_index: str = ""
    fixed_differential: Decimal | None = None
    fixed_price: Decimal | None = None
    price_unit: str = ""                # how the price is quoted, e.g. "USD/bbl"
    currency: str = ""                  # settlement currency
    location: str = ""
    broker: str = ""
    last_updated: str = ""
    evidence: dict[str, str] = field(default_factory=dict)   # field -> "location: raw text"
    flags: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)       # all supporting documents
    names_as_written: dict[str, str] = field(default_factory=dict)  # before the name map

    @property
    def price_currency(self) -> str:
        """The currency the price (differential or fixed price) is quoted in: "USD/bbl" -> USD."""
        return self.price_unit.split("/")[0].strip().upper() if self.price_unit else ""


# --------------------------------------------------------------------------- helpers

def clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def clean_person(text: str | None) -> str:
    """Trader names: 'Libby Smith.' -> 'Libby Smith'. Never used for legal entity names."""
    return clean(text).rstrip(".").strip()


def parse_date(text: str | None, *formats: str) -> date | None:
    t = clean(text)
    for fmt in formats:
        for candidate in (t, t.split(" ")[0] if " " in t and "%H" not in fmt else t):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def parse_number(text: str | None) -> Decimal | None:
    """First number in ``text``: '-$6.10 USD / BBL' -> -6.10, '2,500 M3' -> 2500."""
    m = _NUMBER.search(clean(text))
    if not m:
        return None
    try:
        value = Decimal(m["num"].replace(",", ""))
    except InvalidOperation:
        return None
    return -value if m["sign"] == "-" else value


def is_us(name: str, us: tuple[str, ...]) -> bool:
    n = clean(name).lower()
    return bool(n) and any(u.lower() in n for u in us)


def month_term(start: date | None, end: date | None) -> str:
    """'NOV-26' for a full calendar month; 'NOV-26 to DEC-26' for full months; else ''."""
    if not start or not end or start > end or start.day != 1:
        return ""
    if end.day != calendar.monthrange(end.year, end.month)[1]:
        return ""
    a = f"{calendar.month_abbr[start.month].upper()}-{start.year % 100:02d}"
    b = f"{calendar.month_abbr[end.month].upper()}-{end.year % 100:02d}"
    return a if a == b else f"{a} to {b}"


def month_bounds(term: str) -> tuple[date | None, date | None]:
    """'Nov-26' / 'NOV-26' / 'Nov26' -> (2026-11-01, 2026-11-30)."""
    m = re.match(r"^([A-Za-z]{3})-?([0-9]{2})$", clean(term))
    if not m:
        return None, None
    try:
        month = list(calendar.month_abbr).index(m[1].title())
    except ValueError:
        return None, None
    year = 2000 + int(m[2])
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def single_month(start: date | None, end: date | None) -> bool:
    return bool(start and end and (start.year, start.month) == (end.year, end.month))


def _lines(result: ExtractionResult, kinds=("line", "body_line", "html_body_line")):
    return [(s.location, s.text) for s in result.segments if s.kind in kinds]


def _colon(lines, label: str, *, nth: int = 0, start: int = 0):
    """Value after 'label :' (spaces around the colon allowed). Returns (value, location)."""
    pat = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.*)$", re.I)
    hits = [(m[1].strip(), loc) for loc, text in lines[start:] if (m := pat.match(text))]
    return hits[nth] if len(hits) > nth else (None, None)


def _next_line(lines, label: str):
    """Value on the line after a line that is exactly ``label`` (Marex layout)."""
    for i, (_loc, text) in enumerate(lines):
        if clean(text).lower() == label.lower() and i + 1 < len(lines):
            return lines[i + 1][1].strip(), lines[i + 1][0]
    return None, None


def _set(t: Trade, name: str, value, loc: str | None, raw: str | None) -> None:
    if value in (None, ""):
        return
    setattr(t, name, value)
    if loc:
        t.evidence[name] = f"{loc}: {clean(raw)}" if raw is not None else loc


def _assign_parties(t: Trade, buyer: str, seller: str, buyer_trader: str, seller_trader: str,
                    us: tuple[str, ...], loc_b: str | None, loc_s: str | None) -> None:
    t.buyer, t.seller = clean(buyer), clean(seller)        # legal names exactly as written
    if loc_s:
        t.evidence["seller"] = f"{loc_s}: {clean(seller)}"
    if is_us(buyer, us) and not is_us(seller, us):
        t.side, t.counterparty, t.trader = "BUY", t.seller, clean_person(buyer_trader)
        t.evidence["counterparty"] = t.evidence.get("seller", "")
    elif is_us(seller, us) and not is_us(buyer, us):
        t.side, t.counterparty, t.trader = "SELL", t.buyer, clean_person(seller_trader)
        if loc_b:
            t.evidence["counterparty"] = f"{loc_b}: {clean(buyer)}"
    else:
        t.flags.append(f"cannot tell our side: buyer '{clean(buyer)}', seller '{clean(seller)}'")


# --------------------------------------------------------------------------- families

def _grid(result: ExtractionResult) -> dict[tuple[str, int, int], str]:
    grid = {}
    for s in result.segments:
        m = _CELL.match(s.location) if s.kind == "cell" else None
        if m:
            grid[(m["sheet"], int(m["row"]), column_index_from_string(m["col"]))] = s.text
    return grid


def _header_rows(grid, required: set[str]):
    """Yield (sheet, header_row, {header: col}) for rows containing all required headers."""
    by_row: dict[tuple[str, int], dict[str, int]] = {}
    for (sheet, row, col), text in grid.items():
        by_row.setdefault((sheet, row), {})[clean(text)] = col
    for (sheet, row), headers in sorted(by_row.items()):
        if required <= set(headers):
            yield sheet, row, headers


def _num_text(text: str) -> str:
    t = clean(text)
    return t[:-2] if re.fullmatch(r"[0-9]+\.0", t) else t


def parse_modern_xlsx(result, doc, name, us) -> list[Trade]:
    grid = _grid(result)
    trades = []
    for sheet, hrow, cols in _header_rows(grid, {"Trade Number", "Bid Legal Name",
                                                 "Offer Legal Name"}):
        row = hrow + 1
        while (sheet, row, cols["Trade Number"]) in grid:
            def get(h, row=row, sheet=sheet, cols=cols):
                c = cols.get(h)
                v = grid.get((sheet, row, c)) if c else None
                return v, (f"{sheet}!{get_column_letter(c)}{row}" if c else None)
            t = Trade("Modern Commodities (xlsx)", doc, name)
            v, loc = get("Trade Number")
            _set(t, "contract_no", _num_text(v), loc, v)
            v, loc = get("Executed Timestamp")
            _set(t, "trade_date", parse_date(v, "%Y-%m-%d"), loc, v)
            for h, attr in (("Term Start", "start_date"), ("Term End", "end_date")):
                v, loc = get(h)
                _set(t, attr, parse_date(v, "%Y-%m-%d"), loc, v)
            v, loc = get("Term")
            _set(t, "term", clean(v).upper(), loc, v)
            v, loc = get("Volume")
            _set(t, "quantity", parse_number(v), loc, v)
            v, loc = get("Unit of Measure")
            _set(t, "unit", clean(v), loc, v)
            for h, attr in (("Product", "grade"), ("Price Basis", "primary_index"),
                            ("Settlement Currency", "currency"), ("Location", "location"),
                            ("Broker", "broker"), ("Last Updated Timestamp", "last_updated")):
                v, loc = get(h)
                _set(t, attr, clean(v), loc, v)
            v, loc = get("Price")
            _set(t, "fixed_differential", parse_number(v), loc, v)
            if re.search(r"\bWTI\b", t.primary_index, re.I):
                # The report gives only the settlement currency. WTI CMA differentials are
                # quoted in USD/bbl (the same price appears on USD- and CAD-settled trades).
                t.price_unit = "USD/bbl"
                t.evidence["price_currency"] = (f"{loc}: '{clean(v)}' with Price Basis "
                                                f"'{t.primary_index}': a WTI differential, "
                                                "quoted in USD/bbl by market convention")
            (bid, lb), (off, lo) = get("Bid Legal Name"), get("Offer Legal Name")
            (bt, _), (ot, _) = get("Bid Trader"), get("Offer Trader")
            _assign_parties(t, bid or "", off or "", bt or "", ot or "", us, lb, lo)
            state, _ = get("State")
            if clean(state) and clean(state).lower() != "finalized":
                t.flags.append(f"trade state is '{clean(state)}', not Finalized")
            side, _ = get("Side")
            if side and t.side and clean(side).upper() != t.side:
                t.flags.append(f"Side column says '{side}' but our party position says {t.side}")
            trades.append(t)
            row += 1
    return trades


def parse_ox_pdf(result, doc, name, us) -> list[Trade]:
    lines = _lines(result)
    t = Trade("One Exchange (PDF)", doc, name, broker="One Exchange Corp.")
    v, loc = _colon(lines, "Transaction ID")
    _set(t, "contract_no", clean(v), loc, v)
    v, loc = _colon(lines, "Trade Date")
    _set(t, "trade_date", parse_date(v, "%B %d, %Y"), loc, v)
    for label, attr in (("Start Date", "start_date"), ("End Date", "end_date")):
        v, loc = _colon(lines, label)
        _set(t, attr, parse_date(v, "%B %d, %Y"), loc, v)
    t.term = month_term(t.start_date, t.end_date)
    v, loc = _colon(lines, "Quantity")
    _set(t, "quantity", parse_number(v), loc, v)
    if v and re.search(r"m3\s*/\s*month", v, re.I):
        _set(t, "unit", "m3/month", loc, v)
    elif v:
        t.unit = clean(re.sub(r"^[-0-9.,\s]+", "", v))
        t.flags.append(f"quantity unit kept as written: '{t.unit}'")
    v, loc = _colon(lines, "Deal Type")
    if v:
        _set(t, "grade", clean(v).split(" ")[0].upper(), loc, v)
    v, loc = _colon(lines, "Price")
    _set(t, "fixed_differential", parse_number(v), loc, v)
    if v and re.search(r"USD\s*/\s*BBL", v, re.I):
        t.price_unit = "USD/bbl"
    v, loc = _colon(lines, "Delivery Point")
    _set(t, "location", clean(v), loc, v)
    pays, ploc = _colon(lines, "Buyer Pays")
    if pays and re.search(r"calendar average", pays, re.I) and "WTI" in pays.upper():
        _set(t, "primary_index", "WTI CMA", ploc, pays)
    elif pays:
        t.flags.append("index not recognised in 'Buyer Pays' wording; left blank")
    notes, nloc = _colon(lines, "Notes")
    last = clean(notes).split(";")[-1].strip().upper() if notes else ""
    if last in ("CAD", "USD"):
        _set(t, "currency", last, nloc, notes)        # "Kingston Paper; BP GTCs; CAD"
    else:
        t.flags.append("settlement currency not found in Notes")
    buyer, lb = _colon(lines, "Buyer")
    seller, ls = _colon(lines, "Seller")
    bt, _ = _colon(lines, "Trader", nth=0)      # buyer block's trader comes first
    st, _ = _colon(lines, "Trader", nth=1)
    _assign_parties(t, buyer or "", seller or "", bt or "", st or "", us, lb, ls)
    return [t]


def parse_marex_pdf(result, doc, name, us) -> list[Trade]:
    lines = _lines(result)
    t = Trade("Marex (PDF)", doc, name, broker="Marex Spectron")
    v, loc = _next_line(lines, "Marex Reference")
    _set(t, "contract_no", clean(v), loc, v)
    v, loc = _next_line(lines, "Deal Date")
    _set(t, "trade_date", parse_date(v, "%d-%b-%y"), loc, v)
    v, loc = _next_line(lines, "Contract")
    if v:
        _set(t, "term", clean(v).upper(), loc, v)
        s, e = month_bounds(v)
        if s:
            t.start_date, t.end_date = s, e
            t.evidence["start_date"] = t.evidence["end_date"] = (
                f"{loc}: {clean(v)} (derived: first/last day of the contract month)")
    v, loc = _next_line(lines, "Price")
    _set(t, "fixed_differential", parse_number(v), loc, v)
    if v and re.search(r"USD\s*/\s*Barrel", v, re.I):
        t.price_unit = "USD/bbl"
    v, loc = _next_line(lines, "Volume")
    _set(t, "quantity", parse_number(v), loc, v)
    if v and re.search(r"m3\s*$", v, re.I) and single_month(t.start_date, t.end_date):
        _set(t, "unit", "m3/month", loc, f"{v} (single-month contract)")
    elif v:
        t.flags.append(f"volume unit not normalised: '{clean(v)}'")
    for label, attr in (("Index", "primary_index"), ("Location", "location"),
                        ("Settlement Currency", "currency")):
        v, loc = _next_line(lines, label)
        _set(t, attr, clean(v), loc, v)
    v, loc = _next_line(lines, "Instrument")
    if v:
        _set(t, "grade", clean(v).split(" ")[0].upper(), loc, v)
    (buyer, lb), (seller, ls) = _next_line(lines, "Buyer"), _next_line(lines, "Seller")
    (bt, _), (st, _) = _next_line(lines, "Buy Trader"), _next_line(lines, "Sell Trader")
    _assign_parties(t, buyer or "", seller or "", bt or "", st or "", us, lb, ls)
    return [t]


def parse_marex_xls(result, doc, name, us) -> list[Trade]:
    grid = _grid(result)
    trades = []
    for sheet, hrow, cols in _header_rows(grid, {"Reference", "Deal Date", "Buy Company",
                                                 "Sell Company"}):
        row = hrow + 1
        while (sheet, row, cols["Reference"]) in grid:
            def get(h, row=row, sheet=sheet, cols=cols):
                c = cols.get(h)
                v = grid.get((sheet, row, c)) if c else None
                return v, (f"{sheet}!{get_column_letter(c)}{row}" if c else None)
            t = Trade("Marex (daily summary xls)", doc, name, broker="Marex Spectron")
            v, loc = get("Reference")
            _set(t, "contract_no", _num_text(v), loc, v)
            v, loc = get("Deal Date")
            _set(t, "trade_date", parse_date(v, "%d-%b-%Y"), loc, v)
            v, loc = get("Instrument")
            if v:
                parts = [clean(p) for p in v.split(" - ")]
                _set(t, "grade", parts[0].split(" ")[0].upper(), loc, v)
                if len(parts) > 1:
                    _set(t, "term", parts[-1].upper(), loc, v)
                    s, e = month_bounds(parts[-1])
                    if s:
                        t.start_date, t.end_date = s, e
                        t.evidence["start_date"] = t.evidence["end_date"] = (
                            f"{loc}: {clean(v)} (derived: contract month)")
            v, loc = get("Price")
            _set(t, "fixed_differential", parse_number(v), loc, v)
            v, loc = get("Volume")
            _set(t, "quantity", parse_number(v), loc, v)
            if t.quantity is not None and single_month(t.start_date, t.end_date):
                t.unit = "m3/month"
                t.evidence["unit"] = f"{loc}: m3 for a single-month contract"
            for h, attr in (("Index", "primary_index"), ("Settlement Currency", "currency"),
                            ("Pipeline", "location")):
                v, loc = get(h)
                _set(t, attr, clean(v), loc, v)
            (buyer, lb), (seller, ls) = get("Buy Company"), get("Sell Company")
            (bt, _), (st, _) = get("Buy Trader"), get("Sell Trader")
            _assign_parties(t, buyer or "", seller or "", bt or "", st or "", us, lb, ls)
            trades.append(t)
            row += 1
    return trades


def parse_calrock_msg(result, doc, name, us) -> list[Trade]:
    lines = _lines(result)
    start = next((i for i, (_l, txt) in enumerate(lines) if clean(txt).lower() ==
                  "trade confirmation"), 0)
    t = Trade("CalRock (email)", doc, name, broker="CalRock Brokers")
    v, loc = _colon(lines, "Trade ID", start=start)
    _set(t, "contract_no", clean(v), loc, v)
    v, loc = _colon(lines, "Trade Time", start=start)
    _set(t, "trade_date", parse_date(v, "%B %d, %Y %H:%M", "%B %d, %Y"), loc, v)
    for label, attr in (("Term Start", "start_date"), ("Term End", "end_date")):
        v, loc = _colon(lines, label, start=start)
        _set(t, attr, parse_date(v, "%B %d, %Y"), loc, v)
    t.term = month_term(t.start_date, t.end_date)
    v, loc = _colon(lines, "Product", start=start)
    if v:
        _set(t, "grade", clean(v).split(" ")[0].upper(), loc, v)
    pv, ploc = None, None
    for loc_, text in lines[start:]:
        m = re.match(r"^\s*Price\s*\(([^)]*)\)\s*:\s*(.*)$", text, re.I)
        if m:
            pv, ploc = m[2], loc_
            if re.fullmatch(r"\s*USD\s*/\s*bbl\s*", m[1], re.I):
                t.price_unit = "USD/bbl"
            break
    _set(t, "fixed_differential", parse_number(pv), ploc, pv)
    v, loc = _colon(lines, "Volume", start=start)
    _set(t, "quantity", parse_number(v), loc, v)
    if v and re.search(r"\b(cbm|m3)\b", v, re.I) and single_month(t.start_date, t.end_date):
        _set(t, "unit", "m3/month", loc, f"{v} (cbm = cubic metres; single-month term)")
    elif v:
        t.flags.append(f"volume unit not normalised: '{clean(v)}'")
    for label, attr in (("Index", "primary_index"), ("Hub", "location"),
                        ("Settle Currency", "currency")):
        v, loc = _colon(lines, label, start=start)
        _set(t, attr, clean(v), loc, v)
    (buyer, lb), (seller, ls) = (_colon(lines, "Buyer Company", start=start),
                                 _colon(lines, "Seller Company", start=start))
    (bt, _), (st, _) = (_colon(lines, "Buyer Trader", start=start),
                        _colon(lines, "Seller Trader", start=start))
    _assign_parties(t, buyer or "", seller or "", bt or "", st or "", us, lb, ls)
    return [t]


def parse_isda_swap(result, doc, name, us) -> list[Trade]:
    lines = _lines(result)
    t = Trade("ISDA swap (PDF)", doc, name)
    v, loc = _colon(lines, "Our Reference Number")
    # "167034481 / 181749496": the second number is the transaction (it is also the number
    # in the file name); the full reference stays in the evidence.
    _set(t, "contract_no", clean(v).split("/")[-1].strip() if v else "", loc, v)
    v, loc = _colon(lines, "Trade Date")
    _set(t, "trade_date", parse_date(v, "%B %d, %Y"), loc, v)
    for label, attr in (("Effective Date", "start_date"), ("Termination Date", "end_date")):
        v, loc = _colon(lines, label)
        _set(t, attr, parse_date(v, "%B %d, %Y"), loc, v)
    t.term = month_term(t.start_date, t.end_date)
    v, loc = _colon(lines, "Total Notional Quantity")
    _set(t, "quantity", parse_number(v), loc, v)
    if v and re.search(r"\bBBL\b", v, re.I):
        _set(t, "unit", "bbl", loc, v)
    v, loc = _colon(lines, "Fixed Price")
    _set(t, "fixed_price", parse_number(v), loc, v)
    if v:
        m = re.match(r"\s*([A-Z]{3})\b.*per\s+BBL", v, re.I)
        if m:
            t.price_unit = f"{m[1].upper()}/bbl"
    for label in ("Settlement Currency", "Payments in"):
        v, loc = _colon(lines, label)
        if v and clean(v).upper()[:3] in ("CAD", "USD"):
            _set(t, "currency", clean(v).upper()[:3], loc, v)
            break
    v, loc = _colon(lines, "Commodity Reference Price")
    _set(t, "primary_index", clean(v), loc, v)
    fixed, lf = _colon(lines, "Fixed Price Payer")
    floating, lfl = _colon(lines, "Floating Price Payer")
    # the fixed-price payer buys the swap (pays fixed, receives floating)
    _assign_parties(t, fixed or "", floating or "", "", "", us, lf, lfl)
    t.trader = ""
    t.flags.append("swap: no trader named on the confirmation")
    bank = re.search(r"\b([A-Z][A-Za-z&. ]+Bank[A-Za-z ]*)\b", " ".join(x for _l, x in lines))
    t.broker = clean(bank[1]) if bank else ""
    return [t]


# --------------------------------------------------------------------------- dispatch

def _text(result) -> str:
    return "\n".join(s.text for s in result.segments)


def detect_family(result: ExtractionResult, file_type: str) -> str | None:
    text = _text(result)
    if file_type in ("XLSX", "XLSM"):
        if {"Trade Number", "Bid Legal Name", "Offer Legal Name"} <= {
                clean(s.text) for s in result.segments if s.kind == "cell"}:
            return "modern"
    if file_type == "XLS":
        if {"Reference", "Deal Date", "Buy Company", "Sell Company"} <= {
                clean(s.text) for s in result.segments if s.kind == "cell"}:
            return "marex_xls"
    if file_type == "PDF":
        if "BROKERED TRADE CONFIRMATION" in text and "One Exchange" in text:
            return "ox"
        if re.search(r"^\s*Marex Reference\s*$", text, re.M):
            return "marex_pdf"
        if "COMMODITY SWAP TRANSACTION" in text and "Fixed Price Payer" in text:
            return "isda_swap"
    if file_type == "MSG":
        if re.search(r"^\s*Trade confirmation\s*$", text, re.M | re.I) and \
                "Buyer Company" in text and "Trade ID" in text:
            return "calrock"
    return None


PARSERS = {"modern": parse_modern_xlsx, "ox": parse_ox_pdf, "marex_pdf": parse_marex_pdf,
           "marex_xls": parse_marex_xls, "calrock": parse_calrock_msg,
           "isda_swap": parse_isda_swap}

# which families describe the same trade under the same contract number
_SAME_TRADE_GROUP = {"Marex (PDF)": "marex", "Marex (daily summary xls)": "marex"}
_PREFERENCE = {"Marex (PDF)": 0, "Marex (daily summary xls)": 1}
_COMPARED = ("trade_date", "start_date", "end_date", "side", "quantity", "unit", "grade",
             "primary_index", "fixed_differential", "fixed_price", "price_unit", "currency")
REQUIRED = ("contract_no", "trade_date", "start_date", "end_date", "counterparty", "seller",
            "side", "quantity", "unit", "primary_index", "currency")


def parse_document(result, file_type, doc, name, us=DEFAULT_US) -> tuple[str | None, list[Trade]]:
    fam = detect_family(result, file_type)
    if fam is None:
        return None, []
    trades = PARSERS[fam](result, doc, name, tuple(us))
    for t in trades:
        t.documents.append(f"{doc} {name}")
        _flat_price_check(t)
        if t.price_currency and "price_currency" not in t.evidence:
            price_ev = t.evidence.get("fixed_price") or t.evidence.get("fixed_differential")
            if price_ev:
                t.evidence["price_currency"] = f"{price_ev}  -> quoted in {t.price_unit}"
    return fam, trades


_NO_INDEX = re.compile(r"^\s*(fixed|flat|outright)\b", re.I)


def _flat_price_check(t: Trade) -> None:
    """A price with no index is a flat (fixed) price, not a differential: move it."""
    if t.fixed_differential is None or t.fixed_price is not None:
        return
    if t.primary_index and not _NO_INDEX.match(t.primary_index):
        return
    t.fixed_price = t.fixed_differential
    t.fixed_differential = None
    if "fixed_differential" in t.evidence:
        t.evidence["fixed_price"] = t.evidence.pop("fixed_differential")
    t.flags.append(f"flat price: no index on the confirmation, so {_fmt(t.fixed_price)} is "
                   "treated as a Fixed Price, not a differential")


def _fmt(v) -> str:
    """Readable value for change messages: Decimal('2500.0') -> '2500'."""
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    return str(v)


def _same(a, b) -> bool:
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return a == b                     # Decimal('2500.0') == Decimal('2500')
    return a == b


def merge_trades(trades: list[Trade]) -> list[Trade]:
    """One row per trade: merge duplicates, apply amendments, then validate."""
    groups: dict[tuple[str, str], list[Trade]] = {}
    for t in trades:
        key = (_SAME_TRADE_GROUP.get(t.family, t.family), t.contract_no or f"?{t.source_doc}")
        groups.setdefault(key, []).append(t)
    merged = []
    for (_fam, _no), items in groups.items():
        if len(items) == 1:
            merged.append(items[0])
            continue
        if all(i.family.startswith("Modern") for i in items):
            items.sort(key=lambda i: (i.last_updated, "UPDATED" in i.source_file.upper()))
            latest, older = items[-1], items[:-1]
            for o in older:
                changes = [f"{f}: {_fmt(getattr(o, f))} -> {_fmt(getattr(latest, f))}"
                           for f in _COMPARED if not _same(getattr(o, f), getattr(latest, f))]
                latest.flags.append(f"AMENDED: {o.source_file} superseded by "
                                    f"{latest.source_file}"
                                    + (f" ({'; '.join(changes)})" if changes else ""))
                latest.documents = o.documents + latest.documents
            merged.append(latest)
            continue
        items.sort(key=lambda i: _PREFERENCE.get(i.family, 5))
        best, rest = items[0], items[1:]
        for o in rest:
            diffs = [f"{f}: {_fmt(getattr(best, f))} vs {_fmt(getattr(o, f))}" for f in _COMPARED
                     if getattr(o, f) not in (None, "") and getattr(best, f) not in (None, "")
                     and not _same(getattr(o, f), getattr(best, f))]
            best.flags.append(f"also in {o.source_file}"
                              + (f": DISAGREES on {'; '.join(diffs)}" if diffs else
                                 " (key terms agree)"))
            best.documents += o.documents
        merged.append(best)
    for t in merged:
        missing = [f for f in REQUIRED if getattr(t, f) in (None, "")]
        if missing:
            t.flags.append(f"missing: {', '.join(missing)}")
        if t.start_date and t.end_date and t.start_date > t.end_date:
            t.flags.append("start date after end date")
        if t.trade_date and t.start_date and t.trade_date > t.start_date:
            t.flags.append("trade date is after the start date (balance-of-period trade?)")
        if not t.term:
            t.term = month_term(t.start_date, t.end_date)
    merged.sort(key=lambda t: (t.trade_date or date.max, t.family, t.contract_no))
    return merged
