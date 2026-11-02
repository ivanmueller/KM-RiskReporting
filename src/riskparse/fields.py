"""Proposed trade fields from any layout: find "label -> value" pairs and map common labels.

Works on the located text from :mod:`riskparse.extract`, for any counterparty layout:

* **PDF lines and email body lines**: ``Label: value`` (several pairs on one line are split
  on runs of 2+ spaces). If nothing follows the colon, the next non-label line is taken as the
  value and marked ``next line``.
* **Excel**: a text cell that looks like a label (ends with ``:`` or is a known label) takes
  the nearest non-empty cell to its right (up to 3 columns), otherwise the one below
  (up to 2 rows).

Each pair is mapped to a canonical field when its label matches a known synonym (for example
"Contract Price" maps to ``price``). Everything else is kept as ``(unmapped)``, so nothing is
hidden.

These are **proposals**. Values are copied exactly as written: no unit conversion, no
number or date parsing, and no buy/sell inference. Risk verifies each one against the cited
location. Mappings improve by adding synonyms to ``SYNONYMS`` once the label list has been
reviewed.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from openpyxl.utils.cell import column_index_from_string, get_column_letter

from riskparse.extract.base import ExtractionResult

# canonical field -> label synonyms (normalized: lower case, no punctuation or units)
SYNONYMS: dict[str, tuple[str, ...]] = {
    "trade_date": ("trade date", "transaction date", "deal date", "date of trade",
                   "execution date", "date of transaction", "confirmation date", "trade dt"),
    "deal_reference": ("contract", "contract id", "deal number", "deal no", "deal id",
                       "deal #", "deal", "transaction id",
                       "transaction number", "trade id", "trade number", "ticket",
                       "ticket number", "confirmation number", "confirmation no",
                       "confirm number", "reference", "reference number", "ref", "our ref",
                       "your ref", "contract number", "contract no"),
    "buyer": ("buyer", "purchaser", "buyer name", "buying party"),
    "seller": ("seller", "seller name", "selling party", "vendor"),
    "counterparty": ("counterparty", "counter party", "counterparty name", "customer",
                     "customer name", "party b", "company", "client"),
    "commodity": ("commodity", "product", "product type", "commodity type"),
    "price": ("price", "contract price", "fixed price", "deal price", "purchase price",
              "sale price", "sales price", "unit price", "trade price", "strike price",
              "price per unit"),
    "price_index": ("index", "primary index", "first index", "price index", "pricing index",
                    "floating price", "index price",
                    "reference price", "pricing", "price basis"),
    "secondary_index": ("secondary index", "second index", "alternate index",
                        "secondary pricing index"),
    "differential": ("differential", "fixed differential", "fixed diff", "diff",
                     "differential price", "basis differential", "basis", "adder", "premium",
                     "discount", "spread"),
    "grade": ("grade", "crude grade", "product grade", "quality", "crude type", "crude",
              "stream", "crude stream", "specification", "spec"),
    "quantity": ("quantity", "volume", "contract quantity", "daily contract quantity", "dcq",
                 "daily quantity", "total quantity", "contract volume", "daily volume",
                 "notional quantity", "qty", "mdq", "maximum daily quantity"),
    "unit": ("unit", "units", "uom", "unit of measure", "volume unit", "quantity unit"),
    "currency": ("currency", "price currency", "settlement currency"),
    "delivery_point": ("delivery point", "delivery location", "point of delivery", "location",
                       "hub", "delivery hub", "receipt point", "pipeline", "delivery",
                       "interconnect", "meter"),
    "start_date": ("start date", "delivery start", "delivery start date", "term start",
                   "begin date", "beginning date", "effective date", "commencement date",
                   "from", "flow date", "start"),
    "end_date": ("end date", "delivery end", "delivery end date", "term end", "termination date",
                 "expiry date", "expiration date", "to", "end"),
    "delivery_period": ("term", "delivery period", "contract period", "period", "contract term",
                        "delivery term", "tenor"),
    "trader": ("trader", "trader name", "our trader"),
    "broker": ("broker", "broker name", "brokerage"),
    "payment_terms": ("payment terms", "payment", "payment date", "settlement"),
}
_LOOKUP = {syn: field for field, syns in SYNONYMS.items() for syn in syns}

_LABEL_COLON = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 /&().,#'_-]{0,48}?)\s*[:：]\s*(?P<value>.*)$")
_CHUNKS = re.compile(r"\s{2,}|\t")
_CELL = re.compile(r"^(?P<sheet>.*)!(?P<col>[A-Z]{1,3})(?P<row>[0-9]+)$")
_PAREN = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_DIGITS = re.compile(r"[0-9]+")
_NONWORD = re.compile(r"[^a-z# ]+")


@dataclass(frozen=True)
class FieldProposal:
    field: str             # canonical field, or "(unmapped)"
    label: str             # label exactly as written
    label_key: str         # normalized label (lower case, digits masked)
    value: str             # value exactly as written ("" if none found)
    location: str          # where the value is (or the label, if no value)
    how: str               # "same line" | "next line" | "cell right" | "cell below" | "no value"


def normalize_label(label: str) -> str:
    text = _PAREN.sub(" ", label.lower())
    text = _DIGITS.sub("#", text)
    text = _NONWORD.sub(" ", text)
    words = text.split()
    while words and words[-1] in ("#", "no"):     # "Delivery Point 1", "Leg No"
        if len(words) == 1:
            break
        words.pop()
    return " ".join(words)


def map_label(label: str) -> str:
    key = normalize_label(label)
    if key in _LOOKUP:
        return _LOOKUP[key]
    trimmed = re.sub(r"^(the|our|your|agreed|total|daily)\s+", "", key)
    return _LOOKUP.get(trimmed, "(unmapped)")


def _looks_like_label(text: str) -> bool:
    return bool(_LABEL_COLON.match(text)) or map_label(text) != "(unmapped)"


# Synonyms too short or too generic to trust without a colon ("to", "end", "term"...).
_NO_COLON_EXCLUDED = {"to", "from", "end", "start", "deal", "ref", "delivery", "location",
                      "period", "term", "index", "pricing", "payment", "settlement", "company",
                      "client", "basis", "discount", "premium", "spread", "unit", "units",
                      "product", "hub", "meter", "tenor", "reference", "customer"}
_NO_COLON_SYNS = sorted((syn for syn in _LOOKUP if syn not in _NO_COLON_EXCLUDED and len(syn) >= 4),
                        key=len, reverse=True)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*[0-9]"
_VALUE_START = re.compile(rf"^(?:[A-Za-z]{{3}}\s*)?[$€£]?\s*[-(]?[0-9]|^{_MONTH}", re.I)
_KNOWN_NO_COLON = re.compile(
    r"^\s*(?P<label>" + "|".join(re.escape(x) for x in _NO_COLON_SYNS) + r")"
    r"(?:\s*\([^)]*\))?\s+(?P<value>\S.*)$", re.I)


def _known_label_no_colon(text: str) -> tuple[str, str] | None:
    """'Price 2.50 CAD/GJ' -> ('Price', '2.50 CAD/GJ') for known labels only.

    The value must start like a number, an amount or a date, so sentences such as
    'Price is subject to...' are not taken.
    """
    m = _KNOWN_NO_COLON.match(text)
    if not m or not _VALUE_START.match(m["value"].strip()):
        return None
    label = text[: m.end("label")].strip()
    rest = text[m.end("label"):].strip()
    if rest.startswith("("):                      # keep "(CAD/GJ)" with the label
        close = rest.find(")")
        label, rest = f"{label} {rest[: close + 1]}", rest[close + 1:].strip()
    return label, rest


def _value_like(cell: tuple[str, str]) -> bool:
    text, vtype = cell
    return vtype in ("number", "date", "datetime") or bool(_DIGITS.search(text))


def _is_label_cell(cell: tuple[str, str]) -> bool:
    text, vtype = cell
    return vtype == "text" and not _DIGITS.search(text) and _looks_like_label(text.strip())


def _from_lines(segments) -> list[FieldProposal]:
    out: list[FieldProposal] = []
    seq = [s for s in segments if s.kind in ("line", "body_line", "html_body_line")]
    for i, s in enumerate(seq):
        chunks = [c for c in _CHUNKS.split(s.text) if c.strip()]
        for j, chunk in enumerate(chunks):
            m = _LABEL_COLON.match(chunk)
            if not m:
                # 'Price 2.50' in a sentence, or 'Price | 2.50' as two table chunks
                pair = _known_label_no_colon(chunk)
                # table form: a known label, a wide gap, then the value (any text)
                if pair is None and map_label(chunk) != "(unmapped)" and j + 1 < len(chunks) \
                        and normalize_label(chunk) not in _NO_COLON_EXCLUDED \
                        and not _LABEL_COLON.match(chunks[j + 1]) \
                        and map_label(chunks[j + 1]) == "(unmapped)":
                    pair = (chunk.strip(), chunks[j + 1].strip())
                if pair is not None:
                    out.append(FieldProposal(map_label(pair[0]), pair[0],
                                             normalize_label(pair[0]), pair[1], s.location,
                                             "no colon"))
                continue
            label, value = m["label"].strip(), m["value"].strip()
            how, loc = "same line", s.location
            if not value:
                nxt = next((t for t in seq[i + 1: i + 3] if t.text.strip()), None)
                if nxt is not None and not _LABEL_COLON.match(nxt.text.strip()) \
                        and _known_label_no_colon(nxt.text.strip()) is None \
                        and map_label(_CHUNKS.split(nxt.text.strip())[0]) == "(unmapped)":
                    value, how, loc = nxt.text.strip(), "next line", nxt.location
                else:
                    how = "no value"
            out.append(FieldProposal(map_label(label), label, normalize_label(label), value,
                                     loc, how))
    return out


def _from_cells(segments) -> list[FieldProposal]:
    grid: dict[tuple[str, int, int], tuple[str, str]] = {}
    for s in segments:
        m = _CELL.match(s.location) if s.kind == "cell" else None
        if m:
            grid[(m["sheet"], int(m["row"]), column_index_from_string(m["col"]))] = (
                s.text, s.meta.get("type", "text"))
    out: list[FieldProposal] = []
    for (sheet, row, col), (text, vtype) in sorted(grid.items()):
        if vtype != "text":
            continue
        if not _looks_like_label(text.strip()):
            # Possible label we do not know yet (e.g. "Date", "Sold To"): short text with no
            # digits and a value-like cell to its right. Listed as (unmapped) so the label
            # list shows it; it is never mapped to a field automatically.
            words = text.split()
            if 0 < len(words) <= 5 and len(text) <= 40 and not _DIGITS.search(text):
                right = next((grid[(sheet, row, col + dc)] for dc in (1, 2, 3)
                              if (sheet, row, col + dc) in grid), None)
                if right is not None and _value_like(right):
                    dc = next(dc for dc in (1, 2, 3) if (sheet, row, col + dc) in grid)
                    out.append(FieldProposal("(unmapped)", text.strip(),
                                             normalize_label(text), right[0],
                                             f"{sheet}!{get_column_letter(col + dc)}{row}",
                                             "cell right (candidate label)"))
            continue
        cm = _LABEL_COLON.match(text)
        label = cm["label"].strip() if cm else text.strip()
        inline = cm["value"].strip() if cm else ""
        if inline:
            out.append(FieldProposal(map_label(label), label, normalize_label(label), inline,
                                     f"{sheet}!{get_column_letter(col)}{row}", "same cell"))
            continue
        found = None
        for dc in (1, 2, 3):
            v = grid.get((sheet, row, col + dc))
            if v is not None:
                if not _is_label_cell(v):        # a label to the right = header row
                    found = (v[0], f"{sheet}!{get_column_letter(col + dc)}{row}", "cell right")
                break
        if found is None:
            for dr in (1, 2):
                v = grid.get((sheet, row + dr, col))
                if v is not None:
                    if not _is_label_cell(v):
                        found = (v[0], f"{sheet}!{get_column_letter(col)}{row + dr}",
                                 "cell below")
                    break
        if found is None:
            found = ("", f"{sheet}!{get_column_letter(col)}{row}", "no value")
        out.append(FieldProposal(map_label(label), label, normalize_label(label), *found))
    return out


def propose_fields(result: ExtractionResult) -> list[FieldProposal]:
    return _from_lines(result.segments) + _from_cells(result.segments)


def coverage(proposals_by_doc: dict[str, list[FieldProposal]]) -> dict[str, int]:
    """Canonical field -> number of documents with at least one proposed value."""
    counts: dict[str, int] = defaultdict(int)
    for props in proposals_by_doc.values():
        for f in {p.field for p in props if p.field != "(unmapped)" and p.value}:
            counts[f] += 1
    return dict(counts)
