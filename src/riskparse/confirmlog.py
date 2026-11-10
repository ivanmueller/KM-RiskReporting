"""The register of human confirmations for direct (ICE) trades.

A direct trade has no broker confirm: the trade sheet is taken as correct, and the only
independent check is a person confirming it with the counterparty. That confirmation has to
outlive a single run, so it is kept in one workbook in the results folder:

    Direct trade confirmations.xlsx

Each run adds the direct trades it has not seen before (one row each) and reads back the
yellow columns a person fills in:

    Confirmed with counterparty (Y/N) | Confirmed by | Confirmed on | Notes

``Y`` = confirmed, ``N`` = the counterparty does not agree (a break), blank = awaiting.
A trade is identified by what was traded (delivery month, contract #, trade date,
counterparty, side, grade, volume, price): if the trade sheet row changes, the changed trade
is a new row awaiting confirmation, and the old row is marked as no longer on the trade
sheet. Rows are never deleted. The register is only rewritten by the run; a person only
fills the yellow columns. If it is open in Excel, the run still reads it (the new trades
are added next time).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

FILE = "Direct trade confirmations.xlsx"
SHEET = "direct trades"
COLUMNS = ["Key", "Delivery month", "Contract #", "Trade date", "Counterparty", "Side",
           "Volume (m3)", "Grade", "Location", "Price", "Trader", "Trade sheet row",
           "First seen", "Last seen", "On the trade sheet",
           "Confirmed with counterparty (Y/N)", "Confirmed by", "Confirmed on", "Notes"]
HUMAN = COLUMNS[-4:]
CONFIRMED, DISPUTED, AWAITING = "CONFIRMED", "COUNTERPARTY DISAGREES", "AWAITING CONFIRMATION"
YES = {"Y", "YES", "OK", "TRUE", "CONFIRMED", "1", "✓"}
NO = {"N", "NO", "FALSE", "DISPUTED", "0", "X"}


class ConfirmLogError(RuntimeError):
    pass


@dataclass
class Confirmation:
    status: str = AWAITING
    by: str = ""
    on: str = ""
    notes: str = ""

    def text(self) -> str:
        if self.status == AWAITING:
            return AWAITING
        who = " ".join(x for x in (f"by {self.by}" if self.by else "",
                                   f"on {self.on}" if self.on else "") if x)
        return f"{self.status}{' ' + who if who else ''}" + (
            f" - {self.notes}" if self.notes else "")


def _num(v) -> str:
    if v is None:
        return ""
    return format(v.normalize(), "f") if hasattr(v, "normalize") else str(v)


def trade_key(t) -> str:
    month = f"{t.start_date:%Y-%m}" if t.start_date else ""
    price = (f"{t.secondary_index or 'WTI CMA'} {_num(t.fixed_differential)}".strip()
             if t.primary_index else "")
    parts = [month, t.contract_no, f"{t.trade_date:%Y-%m-%d}" if t.trade_date else "",
             t.counterparty, t.side, t.grade, _num(t.quantity), price]
    return " | ".join(p or "-" for p in parts)


def _row_for(t, key: str, today: str) -> dict:
    return {"Key": key, "Delivery month": f"{t.start_date:%b-%y}".upper() if t.start_date
            else "", "Contract #": t.contract_no, "Trade date": t.trade_date,
            "Counterparty": t.counterparty, "Side": t.side,
            "Volume (m3)": float(t.quantity) if t.quantity is not None else None,
            "Grade": t.grade, "Location": t.location,
            "Price": key.rsplit(" | ", 1)[-1] if key else "", "Trader": t.trader,
            "Trade sheet row": t.documents[0] if t.documents else "",
            "First seen": today, "Last seen": today, "On the trade sheet": "yes"}


def _status(row: dict) -> Confirmation:
    answer = str(row.get("Confirmed with counterparty (Y/N)") or "").strip().upper()
    on = row.get("Confirmed on")
    on = f"{on:%Y-%m-%d}" if isinstance(on, (date, datetime)) else str(on or "").strip()
    c = Confirmation(by=str(row.get("Confirmed by") or "").strip(), on=on,
                     notes=str(row.get("Notes") or "").strip())
    c.status = CONFIRMED if answer in YES else DISPUTED if answer in NO else AWAITING
    return c


def read_log(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, data_only=True)
    except Exception as exc:
        raise ConfirmLogError(f"could not read {path.name}: {exc}") from exc
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    head = [str(h or "").strip() for h in rows[0]]
    return [dict(zip(head, r, strict=False)) for r in rows[1:] if any(v not in (None, "")
                                                                   for v in r)]


def _write(path: Path, rows: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(COLUMNS)
    for r in rows:
        ws.append([r.get(c) for c in COLUMNS])
    head, yellow = PatternFill("solid", fgColor="14213D"), PatternFill("solid", fgColor="FFF2CC")
    for i, c in enumerate(COLUMNS, start=1):
        cell = ws.cell(1, i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="F39A1E") if c in HUMAN else head
        if c in HUMAN:
            for row in range(2, ws.max_row + 1):
                ws.cell(row, i).fill = yellow
    for col, w in zip("ABCDEFGHIJKLMNOPQRS", [52, 10, 11, 11, 30, 6, 11, 8, 10, 16, 8, 38,
                                             11, 11, 10, 18, 16, 12, 40], strict=True):
        ws.column_dimensions[col].width = w
    for row in range(2, ws.max_row + 1):
        ws.cell(row, 4).number_format = "yyyy-mm-dd"
        ws.cell(row, 18).number_format = "yyyy-mm-dd"
    dv = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    dv.add(f"P2:P{max(ws.max_row, 2) + 500}")
    ws.add_data_validation(dv)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, path)            # fails while Excel has the register open
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def update_log(folder: Path, direct_trades: list, today: date | None = None
               ) -> tuple[dict[int, Confirmation], str]:
    """Read the register, add new direct trades, mark the ones gone from the trade sheet.
    Returns {id(trade): Confirmation} and a note ("" or why it could not be saved)."""
    path = Path(folder) / FILE
    stamp = f"{today or date.today():%Y-%m-%d}"
    rows = read_log(path)
    by_key = {str(r.get("Key") or ""): r for r in rows}
    seen, result = set(), {}
    for t in direct_trades:
        key = trade_key(t)
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            row = _row_for(t, key, stamp)
            rows.append(row)
            by_key[key] = row
        else:
            row["Last seen"] = stamp
            row["On the trade sheet"] = "yes"
        result[id(t)] = _status(row)
    for r in rows:
        if str(r.get("Key") or "") not in seen:
            r["On the trade sheet"] = "no longer"
    note = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, rows)
    except OSError as exc:
        note = (f"{FILE} could not be saved ({exc.__class__.__name__}: is it open in "
                "Excel?); the confirmations in it were still read")
    return result, note
