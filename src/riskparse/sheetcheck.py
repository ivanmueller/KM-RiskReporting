"""Validate the parsed confirmations against the master trade sheet.

How the two sources relate (from the October data and the desk's explanation):

* **Physical deals** (Modern Commodities, One Exchange, Marex, CalRock) are on both: the
  confirmation is the counterparty/broker record, the trade sheet is the desk's record. A
  pair is the same deal when the **counterparty, side, grade and delivery month** agree;
  the **differential, trade date and broker** then normally agree too and decide between
  several candidates.
* **Volumes**: buys agree exactly. Sales at Cromer are often lower on the trade sheet by
  the same percentage for every sale to the same counterparty in the same month (for
  example 91% of 5,000 m3 and 91% of 2,500 m3): the desk records the apportioned volume.
  A consistent percentage is reported as a note; a one-off or a higher trade sheet volume
  is a difference to check.
* **Financial swaps** (National Bank of Canada, ISDA) are not on the trade sheet by design.
  A **roll** is two swaps (four legs: a fixed and a floating leg each) on the same day, for
  the same volume, one buying and one selling consecutive months. A swap a quarter or more
  ahead of its trade date is a **forward hedge** (for example fixed-price barrels bought
  from a producer and hedged with the bank).
* A confirmation whose delivery month has no rows at all on a sheet (the monthly workbook
  for that month is not in the folder yet) is **month not on this sheet**, not a break.
* **Evergreen** rows (``Evergreen`` in the Date column: rolling agreements) and **Direct**
  rows (``Direct`` in Broker / Direct: trades done on ICE, with no broker confirm) have no
  contract by nature. They are not breaks: they are brought into the system from the
  trade sheet (:func:`sheet_only_trades`), taken as correct, and the direct ones wait for
  a person to confirm them with the counterparty - the only independent check there is.
* Brokered trade sheet deals dated inside the confirmations' date range but with no
  confirmation are **missing broker confirms**; Term / Spot rows, internal rows (KM,
  opening inventory) and deals outside that range are counted, not flagged.

Nothing here changes either source.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from riskparse.confirms import Trade
from riskparse.names import NameMap, norm
from riskparse.tradesheet import SheetTrade, TradeSheet

MATCHED = "MATCHED"
DIFFERENCES = "MATCHED - CHECK DIFFERENCES"
NOT_ON_SHEET = "NOT ON TRADE SHEET"
MONTH_NOT_COVERED = "MONTH NOT ON THIS SHEET"
NO_CONFIRMATION = "NO CONFIRMATION"
OUTSIDE_PERIOD = "OUTSIDE CONFIRMATION PERIOD"
# trade sheet rows that have no contract by nature: brought into the system from the sheet
EVERGREEN = "TRADE SHEET ONLY - EVERGREEN"
DIRECT_ICE = "TRADE SHEET ONLY - DIRECT (ICE)"
SHEET_ONLY = (EVERGREEN, DIRECT_ICE)
STATUS_ORDER = (DIFFERENCES, NOT_ON_SHEET, NO_CONFIRMATION, MATCHED, DIRECT_ICE, EVERGREEN,
                MONTH_NOT_COVERED, OUTSIDE_PERIOD)

ROLL = "ROLL"
FORWARD_HEDGE = "FORWARD HEDGE"
SWAP = "SWAP"
FORWARD_MONTHS = 3          # a swap this many months ahead of its trade date is a forward hedge

GRADES = {"MID": "MIDALE", "MIDALE": "MIDALE", "LSB": "LSB", "SW": "SW", "WCS": "WCS"}
BROKERS = {"modern": {"MODERN"}, "one exchange": {"OX", "ONE EXCHANGE"},
           "marex": {"MAREX"}, "calrock": {"CR", "CALROCK", "CAL ROCK"}}
DIRECT = {"DIRECT"}
MIN_SCORE = 4               # the differential alone, or the trade date and the broker
APPORTION_TOLERANCE = Decimal("0.005")


@dataclass
class CheckRow:
    status: str
    trade: Trade | None = None
    sheet: SheetTrade | None = None
    differences: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class FinancialRow:
    trade: Trade
    kind: str
    partner: Trade | None = None
    note: str = ""


@dataclass
class SheetCheck:
    sheet: TradeSheet
    rows: list[CheckRow] = field(default_factory=list)
    financial: list[FinancialRow] = field(default_factory=list)
    period: tuple[date, date] | None = None
    standing_rows: int = 0          # Term / Spot rows (not dated deals, not Evergreen)
    internal_rows: int = 0

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r.status == status)

    def for_trade(self, trade: Trade) -> CheckRow | None:
        return next((r for r in self.rows if r.trade is trade), None)


# --------------------------------------------------------------------------- helpers

def grade_key(grade: str) -> str:
    g = " ".join((grade or "").upper().split())
    return GRADES.get(g, g)


def month_of(d: date | None) -> date | None:
    return d.replace(day=1) if d else None


def month_label(d: date | None) -> str:
    return d.strftime("%b-%y").upper() if d else ""


def broker_key(family: str) -> set[str]:
    f = family.lower()
    return next((v for k, v in BROKERS.items() if f.startswith(k)), set())


def is_financial(t: Trade) -> bool:
    return t.family.upper().startswith("ISDA")


def is_evergreen(s: SheetTrade) -> bool:
    return s.trade_date is None and s.date_text.strip().lower().startswith("evergreen")


def is_direct(s: SheetTrade) -> bool:
    return s.broker.strip().upper() in DIRECT


def is_internal(s: SheetTrade) -> bool:
    c = s.counterparty.upper()
    return c in ("KM", "KM-APP") or c.startswith("KM ") or c.startswith("KM-") \
        or "INVENTORY" in s.stream.upper()


def resolve_counterparty(s: SheetTrade, nmap: NameMap | None, masters: set[str]) -> str:
    """The trade sheet's short name (BP, CIOL, WCP) as a master name, or ""."""
    if nmap is not None:
        master, _how = nmap.lookup(s.counterparty)
        if master:
            return master
    short = norm(s.counterparty)
    if not short:
        return ""
    hits = [m for m in masters if norm(m) == short or norm(m).startswith(short + " ")]
    return hits[0] if len(hits) == 1 else ""


def _num(v: Decimal | None) -> str:
    return "" if v is None else format(v.normalize(), "f")


def _same_number(a: Decimal | None, b: Decimal | None, tol: str = "0.005") -> bool:
    return a is not None and b is not None and abs(a - b) <= Decimal(tol)


def _first_name(name: str) -> str:
    return (name or "").split()[0].lower() if (name or "").split() else ""


# --------------------------------------------------------------------------- matching

def _score(t: Trade, s: SheetTrade) -> int:
    score = 0
    if _same_number(t.fixed_differential, s.differential):
        score += 4
    if t.trade_date and s.trade_date:
        gap = abs((t.trade_date - s.trade_date).days)
        score += 3 if gap == 0 else 1 if gap <= 3 else 0
    if s.broker.upper() in broker_key(t.family):
        score += 2
    if t.quantity is not None and s.volume is not None:
        if _same_number(t.quantity, s.volume, "0.5"):
            score += 2
        elif t.side == "SELL" and Decimal("0.75") * t.quantity <= s.volume < t.quantity:
            score += 1
    if t.currency and s.currency and t.currency == s.currency:
        score += 1
    return score


def _keys_agree(t: Trade, s: SheetTrade) -> bool:
    return (t.side == s.side and month_of(t.start_date) == s.delivery_month
            and grade_key(t.grade) == grade_key(s.grade)
            and bool(s.master_counterparty) and s.master_counterparty == t.counterparty)


def _differences(t: Trade, s: SheetTrade) -> list[str]:
    out = []
    if t.fixed_differential is not None or s.differential is not None:
        if not _same_number(t.fixed_differential, s.differential):
            shown = _num(s.differential) or s.differential_text or "blank"
            out.append(f"differential: confirmation {_num(t.fixed_differential) or 'blank'}, "
                       f"trade sheet {shown}")
    if t.fixed_differential is not None and s.price_basis.upper().startswith("INDEX"):
        out.append(f"price basis: confirmation has a fixed differential, trade sheet says "
                   f"'{s.price_basis} {s.index_type}'".rstrip())
    if t.trade_date and s.trade_date and t.trade_date != s.trade_date:
        out.append(f"trade date: confirmation {t.trade_date}, trade sheet {s.trade_date}")
    if s.broker.upper() not in broker_key(t.family):
        out.append(f"broker: confirmation from {t.family.split(' (')[0]}, trade sheet says "
                   f"'{s.broker or 'blank'}'")
    if t.currency and s.currency and t.currency != s.currency:
        out.append(f"settlement currency: confirmation {t.currency}, trade sheet {s.currency}")
    if t.trader and s.trader and _first_name(t.trader) != _first_name(s.trader):
        out.append(f"trader: confirmation {t.trader}, trade sheet {s.trader}")
    return out


def _volume_check(rows: list[CheckRow]) -> None:
    """Equal volumes pass; consistent reductions on sales are apportionment notes."""
    reduced: dict[tuple[str, date], list[CheckRow]] = defaultdict(list)
    for r in rows:
        t, s = r.trade, r.sheet
        if t.quantity is None or s.volume is None or _same_number(t.quantity, s.volume, "0.5"):
            continue
        if t.side == "SELL" and s.volume < t.quantity:
            reduced[(t.counterparty, s.delivery_month)].append(r)
        else:
            r.differences.append(f"volume: confirmation {_num(t.quantity)} m3, trade sheet "
                                 f"{_num(s.volume)} m3")
    for (_cpty, month), group in reduced.items():
        ratios = [r.sheet.volume / r.trade.quantity for r in group]
        consistent = len(group) > 1 and max(ratios) - min(ratios) <= APPORTION_TOLERANCE
        for r, ratio in zip(group, ratios, strict=True):
            text = (f"volume: confirmation {_num(r.trade.quantity)} m3, trade sheet "
                    f"{_num(r.sheet.volume)} m3 ({ratio:.1%})")
            if consistent:
                r.notes.append(f"{text}: the same % on all {len(group)} sales to this "
                               f"counterparty for {month_label(month)} - apportioned volume")
            else:
                r.differences.append(f"{text}: lower on the trade sheet; not the same % as "
                                     "other sales to this counterparty, so check it")


def classify_financial(swaps: list[Trade]) -> list[FinancialRow]:
    rows: list[FinancialRow] = []
    used: set[int] = set()
    for i, a in enumerate(swaps):
        if i in used:
            continue
        for j in range(i + 1, len(swaps)):
            b = swaps[j]
            if j in used or not (a.start_date and b.start_date):
                continue
            months = abs((a.start_date.year - b.start_date.year) * 12
                         + a.start_date.month - b.start_date.month)
            if (a.counterparty == b.counterparty and a.trade_date == b.trade_date
                    and _same_number(a.quantity, b.quantity, "0.5") and a.side != b.side
                    and months == 1):
                near, far = sorted((a, b), key=lambda t: t.start_date)
                spread = (near.fixed_price - far.fixed_price
                          if near.fixed_price is not None and far.fixed_price is not None
                          else None)
                note = (f"roll: {near.side.lower()}s {near.term}, {far.side.lower()}s "
                        f"{far.term}, {_num(a.quantity)} {a.unit} each; two swaps = four "
                        f"legs (fixed and floating on each)"
                        + (f"; fixed {_num(near.fixed_price)} vs {_num(far.fixed_price)}, "
                           f"spread {_num(spread)} {near.price_unit}" if spread is not None
                           else ""))
                rows.append(FinancialRow(a, ROLL, b, note))
                rows.append(FinancialRow(b, ROLL, a, note))
                used |= {i, j}
                break
        if i in used:
            continue
        ahead = None
        if a.trade_date and a.start_date:
            ahead = ((a.start_date.year - a.trade_date.year) * 12
                     + a.start_date.month - a.trade_date.month)
        if ahead is not None and ahead >= FORWARD_MONTHS:
            rows.append(FinancialRow(a, FORWARD_HEDGE, None,
                                     f"forward hedge {ahead} months ahead ({a.term}): fixed "
                                     f"{_num(a.fixed_price)} {a.price_unit}"))
        else:
            rows.append(FinancialRow(a, SWAP, None, "single swap, not part of a roll and not "
                                                    "far forward: check what it hedges"))
        used.add(i)
    rows.sort(key=lambda r: (r.trade.trade_date or date.max, r.trade.contract_no))
    return rows


def check_against_sheet(trades: list[Trade], sheet: TradeSheet,
                        nmap: NameMap | None) -> SheetCheck:
    """``sheet.label`` names the source in flags ("not on the monthly trade sheet")."""
    physical = [t for t in trades if not is_financial(t)]
    result = SheetCheck(sheet, financial=classify_financial(
        [t for t in trades if is_financial(t)]))
    masters = {t.counterparty for t in trades if t.counterparty}
    for s in sheet.trades:
        s.master_counterparty = resolve_counterparty(s, nmap, masters)
    dates = [t.trade_date for t in physical if t.trade_date]
    result.period = (min(dates), max(dates)) if dates else None

    pairs = []
    for t in physical:
        for s in sheet.trades:
            if _keys_agree(t, s):
                sc = _score(t, s)
                if sc >= MIN_SCORE:
                    gap = abs((t.trade_date - s.trade_date).days) \
                        if t.trade_date and s.trade_date else 999
                    pairs.append((sc, -gap, t, s))
    pairs.sort(key=lambda p: (p[0], p[1]), reverse=True)
    taken_t: set[int] = set()
    taken_s: set[int] = set()
    matched: list[CheckRow] = []
    for _sc, _gap, t, s in pairs:
        if id(t) in taken_t or id(s) in taken_s:
            continue
        taken_t.add(id(t))
        taken_s.add(id(s))
        matched.append(CheckRow(MATCHED, t, s, _differences(t, s)))
    _volume_check(matched)
    for r in matched:
        if r.differences:
            r.status = DIFFERENCES
    result.rows.extend(matched)

    covered = {s.delivery_month for s in sheet.trades}
    for t in physical:
        if id(t) not in taken_t:
            if month_of(t.start_date) not in covered:
                result.rows.append(CheckRow(MONTH_NOT_COVERED, t, notes=[
                    f"{sheet.named} has no {month_label(month_of(t.start_date))} rows"]))
                continue
            row = CheckRow(NOT_ON_SHEET, t)
            near = [s for s in sheet.trades if id(s) not in taken_s and s.side == t.side
                    and s.delivery_month == month_of(t.start_date)
                    and grade_key(s.grade) == grade_key(t.grade)
                    and _same_number(s.differential, t.fixed_differential)]
            if near:
                row.notes.append("closest trade sheet row(s) with the same side, month, grade "
                                 "and differential: " + ", ".join(
                                     f"{s.internal_contract or s.where} ({s.counterparty})"
                                     for s in near[:3]))
            result.rows.append(row)

    for s in sheet.trades:
        if id(s) in taken_s:
            continue
        if is_evergreen(s):
            result.rows.append(CheckRow(EVERGREEN, None, s, notes=[
                "rolling Evergreen agreement: no contract in the contracts folder; taken "
                "from the trade sheet"]))
            continue
        if is_direct(s):
            result.rows.append(CheckRow(DIRECT_ICE, None, s, notes=[
                "direct trade on ICE: no broker confirm; taken from the trade sheet as "
                "correct - confirm it with the counterparty"]))
            continue
        if is_internal(s):
            result.internal_rows += 1
            continue
        if s.trade_date is None:
            result.standing_rows += 1
            continue
        inside = result.period and result.period[0] <= s.trade_date <= result.period[1]
        result.rows.append(CheckRow(NO_CONFIRMATION if inside else OUTSIDE_PERIOD, None, s))

    # a matched confirmation takes the trade sheet's Location (column N)
    for r in matched:
        if r.sheet.location:
            r.trade.evidence["location"] = (f"{r.sheet.where}: '{r.sheet.location}' (trade "
                                            f"sheet Location; confirmation said "
                                            f"'{r.trade.location or 'nothing'}')")
            r.trade.location = r.sheet.location

    for r in result.rows:
        if r.trade is None:
            continue
        if r.status == NOT_ON_SHEET:
            r.trade.flags.append(f"not on {sheet.named}")
        elif r.status == DIFFERENCES:
            r.trade.flags.append(f"differs from {sheet.named}: " + "; ".join(r.differences))
    result.rows.sort(key=lambda r: (STATUS_ORDER.index(r.status),
                                    (r.trade.trade_date if r.trade else r.sheet.trade_date)
                                    or date.max))
    return result


def place_name(location: str) -> str:
    """'Cromer, MB (Enbridge)' / 'Cromer - Enbridge Transfer' -> 'Cromer': the place, as the
    trade sheet's Location column writes it."""
    return re.split(r",|\(| - ", location or "", maxsplit=1)[0].strip()


def tag_locations(trades: list[Trade]) -> None:
    """Confirmed physical trades not matched to the trade sheet: the confirmation's location
    shortened to the place name (the full text stays in the evidence)."""
    for t in trades:
        if is_financial(t) or not t.location or "trade sheet Location" in t.evidence.get(
                "location", ""):
            continue
        short = place_name(t.location)
        if short and short != t.location:
            t.evidence["location"] = (t.evidence.get("location", t.location)
                                      + f"  -> '{short}' (the place, as on the trade sheet)")
            t.location = short


def sheet_only_trades(check: SheetCheck, nmap: NameMap | None,
                      us: str = "Kingston Marketing Limited") -> list[Trade]:
    """The Evergreen and Direct (ICE) rows of the trade sheet as trades: no contract, the
    trade sheet is the record. Every value's evidence is its trade sheet row."""
    from riskparse.confirms import month_term
    out = []
    for r in check.rows:
        if r.status not in SHEET_ONLY:
            continue
        s = r.sheet
        family = ("Trade sheet - Evergreen" if r.status == EVERGREEN
                  else "Trade sheet - Direct (ICE)")
        t = Trade(family=family, source_doc="trade sheet", source_file=s.book or "trade sheet",
                  contract_no=s.internal_contract)
        name = s.counterparty
        if nmap is not None and name:
            name = nmap.lookup(name)[0] or name
        start = s.delivery_month
        end = None
        if start:
            nxt = date(start.year + start.month // 12, start.month % 12 + 1, 1)
            end = date.fromordinal(nxt.toordinal() - 1)
        values = {
            "trade_date": s.trade_date, "start_date": start, "end_date": end,
            "term": month_term(start, end) if start else "", "trader": s.trader,
            "counterparty": name, "side": s.side, "quantity": s.volume,
            "unit": "m3/month" if s.volume is not None else "", "grade": s.grade,
            "currency": s.currency, "location": s.location, "broker": s.broker or "",
        }
        if name:
            values["seller"] = name if s.side == "BUY" else us
            values["buyer"] = us if s.side == "BUY" else name
        basis = s.price_basis.strip().upper()
        if basis.startswith("FIXED"):
            values["primary_index"] = "WTI CMA"
            values["fixed_differential"] = s.differential
            if s.differential is not None:
                t.price_unit = "USD/bbl"
        elif basis.startswith("INDEX"):
            values["primary_index"] = "WTI CMA"
            values["secondary_index"] = f"{s.index_type} index".strip() if s.index_type \
                else "index"
            values["fixed_differential"] = s.differential
            if s.differential is not None:
                t.price_unit = "USD/bbl"
        for attr, v in values.items():
            if v in (None, ""):
                continue
            setattr(t, attr, v)
            t.evidence[attr] = f"{s.where}: {v}"
        if s.differential_text:
            t.flags.append(f"price on the trade sheet: '{s.differential_text}' (as entered)")
        if t.price_unit:
            t.evidence["price_currency"] = (f"{s.where}: a differential to WTI CMA, quoted in "
                                            "USD/bbl by market convention")
        if s.stream:
            t.evidence["stream"] = f"{s.where}: {s.stream}"
        t.documents.append(f"{s.where}")
        out.append(t)
    return out
