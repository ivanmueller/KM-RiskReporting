"""Fill the firm's ``raw-data-format`` sheet from the proposed fields.

The target is the firm's own template (``raw-data-format.xlsx``): one sheet with the
column headers below and one row per trade. Each document with at least one value for
these columns becomes one row. Values are copied **exactly as written** (no date, number
or unit conversion). Every value's location is listed on a separate evidence sheet, so the
format sheet stays clean enough to paste into the library while Risk can still verify each
cell against the document.

When a document has several different candidates for one column, the first is used and
all of them are listed in the evidence. When no counterparty label is found but buyer and
seller are, and one of them matches the firm's own name (``--us``), the other party is
proposed as the counterparty and marked ``derived``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

DEFAULT_HEADERS = ["Contract #", "Trade Date", "Start Date", "End Date", "Term", "Trader",
                   "Counterparty", "Seller", "Quantity", "Unit", "Grade", "Primary Index",
                   "Secondary Index", "Fixed Differential"]

# format header (normalized) -> canonical field from riskparse.fields
HEADER_FIELDS = {
    "contract #": "deal_reference", "contract": "deal_reference",
    "contract number": "deal_reference", "trade date": "trade_date",
    "start date": "start_date", "end date": "end_date", "term": "delivery_period",
    "trader": "trader", "counterparty": "counterparty", "seller": "seller", "buyer": "buyer",
    "quantity": "quantity", "volume": "quantity", "unit": "unit", "grade": "grade",
    "primary index": "price_index", "index": "price_index",
    "secondary index": "secondary_index", "fixed differential": "differential",
    "differential": "differential", "price": "price", "delivery point": "delivery_point",
    "commodity": "commodity", "currency": "currency", "broker": "broker",
}


def _norm_header(h: str) -> str:
    return " ".join(str(h).strip().lower().split())


def read_format_headers(path: str | Path) -> list[str]:
    """Header row of the first sheet of the firm's format workbook."""
    wb = load_workbook(path, read_only=True)
    try:
        ws = wb.worksheets[0]
        row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    finally:
        wb.close()
    headers = [str(v) for v in row if v is not None and str(v).strip()]
    if not headers:
        raise ValueError(f"{path}: first row of the first sheet has no column headers")
    return headers


@dataclass
class FormatRow:
    doc: str
    file: str
    attached_to: str
    values: dict[str, str] = field(default_factory=dict)      # header -> value as written
    evidence: dict[str, str] = field(default_factory=dict)    # header -> "loc (how)"
    others: dict[str, str] = field(default_factory=dict)      # header -> other candidates


def _matches_us(value: str, us: list[str]) -> bool:
    v = " ".join(value.lower().split())
    return any(u and " ".join(u.lower().split()) in v for u in us)


def build_rows(documents: list[dict], proposals: dict[str, list], headers: list[str],
               us: list[str] | None = None) -> tuple[list[FormatRow], list[str]]:
    """Return (rows, unmapped headers)."""
    us = us or []
    unmapped = [h for h in headers if _norm_header(h) not in HEADER_FIELDS]
    rows: list[FormatRow] = []
    for d in documents:
        props = proposals.get(d["doc"], [])
        row = FormatRow(d["doc"], d["file"], d.get("attached_to", ""))
        for h in headers:
            fname = HEADER_FIELDS.get(_norm_header(h))
            if fname is None:
                continue
            cands = [p for p in props if p.field == fname and p.value.strip()]
            if fname == "counterparty" and not cands and us:
                parties = [p for p in props if p.field in ("buyer", "seller") and p.value.strip()]
                others = [p for p in parties if not _matches_us(p.value, us)]
                if others and len(others) < len(parties):
                    row.values[h] = others[0].value.strip()
                    row.evidence[h] = (f"{others[0].location} (derived: the {others[0].field} "
                                       "that is not us)")
                continue
            if not cands:
                continue
            first = cands[0]
            row.values[h] = first.value.strip()
            row.evidence[h] = f"{first.location} ({first.how})"
            distinct = []
            for p in cands[1:]:
                if p.value.strip() != first.value.strip() and p.value.strip() not in distinct:
                    distinct.append(p.value.strip())
            if distinct:
                row.others[h] = " | ".join(distinct[:5])
        if row.values:
            rows.append(row)
    return rows, unmapped
