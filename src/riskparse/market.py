"""Market prices from the monthly trading workbook: read, never changed.

Two tabs of ``<Month> Trading <Year>`` hold the prices the risk report needs:

* **Pricing V2**: the trade cycle month (``Trade Cycle:``), its WTI CMA and FX, and the
  COMM index price of each grade (``Grade`` / ``COMM Price`` columns).
* **Forward Curve**: one row per series and contract month (``Date``, ``CommonGrade``,
  ``CommonLocation``, ``Source``, ``Type``, ``Contract``, ``Price``, ``Unit``, ``Base``):
  WTI CMA (``Calculated``), USD/CAD (``FX``), Mont Belvieu butane, and broker quotes
  (Modern, CalRock, Marex) for each grade's differential to WTI CMA.

A grade's differential for a month is the COMM index price when that month is the trade
cycle, otherwise the average of the broker quotes on the latest curve date. Missing
prices stay missing (``None``): nothing is extrapolated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# report grade -> (forward curve grade, location)
GRADES = {"LSB": ("LSB", "Cromer, MB"), "Midale": ("Midale", "Cromer, MB"),
          "SW": ("SW", "Edmonton, AB"), "WCS": ("WCS", "Hardisty, AB")}
BROKERS = ("Modern", "CalRock", "Marex")


@dataclass
class Market:
    source: str = ""
    curve_date: date | None = None          # the forward curve's latest date
    trade_cycle: date | None = None         # Pricing V2's month
    wti_cma: dict[date, float] = field(default_factory=dict)
    fx: dict[date, float] = field(default_factory=dict)            # CAD per USD
    diffs: dict[str, dict[date, float]] = field(default_factory=dict)  # grade -> month
    diff_source: dict[tuple[str, date], str] = field(default_factory=dict)
    c4: dict[date, float] = field(default_factory=dict)            # MBV C4 USD/gal

    def diff(self, grade: str, month: date) -> float | None:
        return self.diffs.get(grade, {}).get(month)


def _month(v) -> date | None:
    if isinstance(v, datetime):
        v = v.date()
    return v.replace(day=1) if isinstance(v, date) else None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _tab(wb, *prefixes: str):
    for ws in wb.worksheets:
        if any(ws.title.strip().casefold().startswith(p) for p in prefixes):
            return ws
    return None


def load_market(path: str | Path) -> Market:
    """Read the prices from one trading workbook; tabs that are missing give no prices."""
    from openpyxl import load_workbook
    p = Path(path)
    m = Market(source=p.name)
    wb = load_workbook(p, read_only=True, data_only=True)
    try:
        pricing = _tab(wb, "pricing")
        if pricing is not None:
            _read_pricing(m, list(pricing.iter_rows(values_only=True, max_row=80)))
        curve = _tab(wb, "forward curve")
        if curve is not None:
            _read_curve(m, curve.iter_rows(values_only=True))
    finally:
        wb.close()
    return m


def _read_pricing(m: Market, rows) -> None:
    wti = fx = None
    for row in rows[:4]:
        cells = list(row)
        for i, v in enumerate(cells[:-1]):
            label = str(v or "").strip().lower()
            if label.startswith("trade cycle"):
                m.trade_cycle = _month(cells[i + 1])
            elif label.startswith("wti cma"):
                wti = _num(cells[i + 1])
            elif label.startswith("fx"):
                fx = _num(cells[i + 1])
    if m.trade_cycle is None:
        return
    if wti is not None:
        m.wti_cma[m.trade_cycle] = wti
    if fx is not None:
        m.fx[m.trade_cycle] = fx
    for i, row in enumerate(rows):
        heads = [str(c or "").strip().lower() for c in row]
        if "grade" in heads and "comm price" in heads:
            g, c = heads.index("grade"), heads.index("comm price")
            for r in rows[i + 1:]:
                grade, price = r[g] if g < len(r) else None, r[c] if c < len(r) else None
                name = str(grade or "").strip()
                if name in GRADES and _num(price) is not None \
                        and name not in m.diffs.get(name, {}):
                    m.diffs.setdefault(name, {})[m.trade_cycle] = _num(price)
                    m.diff_source[(name, m.trade_cycle)] = "COMM index"
            break


def _read_curve(m: Market, rows) -> None:
    it = iter(rows)
    heads = [str(c or "").strip().lower() for c in next(it, ())]
    need = ("date", "commongrade", "commonlocation", "source", "contract", "price")
    if not all(h in heads for h in need):
        return
    ix = {h: heads.index(h) for h in need}
    latest: dict[tuple, tuple] = {}            # (grade, location, source, month) -> (date, price)
    for r in it:
        d, price, month = r[ix["date"]], _num(r[ix["price"]]), _month(r[ix["contract"]])
        if price is None or month is None:
            continue
        d = d.date() if isinstance(d, datetime) else d
        key = (str(r[ix["commongrade"]] or "").strip(), str(r[ix["commonlocation"]] or "").strip(),
               str(r[ix["source"]] or "").strip(), month)
        if key not in latest or (d and latest[key][0] and d > latest[key][0]):
            latest[key] = (d, price)
        if isinstance(d, date) and (m.curve_date is None or d > m.curve_date):
            m.curve_date = d
    quotes = defaultdict(list)
    for (grade, loc, src, month), (_d, price) in latest.items():
        if grade == "WTI CMA" and src == "Calculated":
            m.wti_cma.setdefault(month, price)
        elif grade == "USD/CAD":
            m.fx.setdefault(month, price)
        elif grade == "MBV C4":
            m.c4[month] = price
        else:
            for name, (cg, cl) in GRADES.items():
                if grade == cg and loc == cl and src in BROKERS:
                    quotes[(name, month)].append(price)
    for (name, month), prices in quotes.items():
        if month not in m.diffs.get(name, {}):
            m.diffs.setdefault(name, {})[month] = sum(prices) / len(prices)
            m.diff_source[(name, month)] = f"broker average ({len(prices)})"
