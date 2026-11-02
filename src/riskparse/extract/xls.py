"""Legacy Excel 97-2003 (.xls) cell extraction with xlrd (BSD-3-Clause).

Same output as the .xlsx extractor: one segment per non-empty cell at ``Sheet!A1``.
Formulas are not evaluated; xlrd returns the value Excel last saved. Dates are rendered
as ISO text using the workbook's date system.
"""

from __future__ import annotations

from datetime import datetime

import xlrd

from riskparse.extract.base import (
    FAILED,
    TEXT_EXTRACTED,
    ExtractionResult,
    SegmentBuilder,
)

NAME = "xls-cells"
VERSION = "1"


def extract_xls(data: bytes, *, filename: str = "") -> ExtractionResult:
    result = ExtractionResult(NAME, VERSION, TEXT_EXTRACTED)
    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=True)
    except Exception as exc:
        result.status = FAILED
        result.warnings.append(f"could not open .xls workbook: {type(exc).__name__}: {exc}")
        return result
    seg = SegmentBuilder()
    sheets, hidden = [], []
    try:
        for sh in book.sheets():
            sheets.append(sh.name)
            state = {0: "visible", 1: "hidden", 2: "veryHidden"}.get(sh.visibility, "hidden")
            if state != "visible":
                hidden.append(f"{sh.name} ({state})")
            for r in range(sh.nrows):
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    if cell.ctype == xlrd.XL_CELL_NUMBER:
                        text, vtype = repr(cell.value), "number"
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        dt = datetime(*xlrd.xldate_as_tuple(cell.value, book.datemode))
                        text, vtype = dt.isoformat(sep=" "), "datetime"
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        text, vtype = ("TRUE" if cell.value else "FALSE"), "bool"
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        text, vtype = xlrd.error_text_from_code.get(cell.value, "#ERR"), "error"
                    else:
                        text, vtype = str(cell.value), "text"
                    if vtype == "text" and not text.strip():
                        continue
                    meta = {"type": vtype}
                    if state != "visible":
                        meta["sheet_state"] = state
                    seg.add(f"{sh.name}!{xlrd.formula.cellname(r, c)}", "cell", text, **meta)
    finally:
        book.release_resources()
    result.segments = seg.segments
    result.metadata = {"sheets": sheets, "hidden_sheets": hidden,
                       "formulas_without_cached_value": 0}
    if hidden:
        result.warnings.append(f"hidden sheet(s) extracted and flagged: {', '.join(hidden)}")
    return result
