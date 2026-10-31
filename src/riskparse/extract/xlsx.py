"""Excel (.xlsx / .xlsm) cell extraction with exact cell locations.

Reads the workbook twice in read-only mode: once for the values Excel last saved
(``data_only=True``) and once for formulas. Formulas are **not** evaluated, macros are
never run, and external links are not followed. Each non-empty cell becomes one segment
at ``Sheet!A1`` with its raw value and number format, so a ``0.05`` shown as ``5%`` is
not misread later.
"""

from __future__ import annotations

import io
import warnings
from datetime import date, datetime, time

from openpyxl import load_workbook

from riskparse.extract.base import (
    FAILED,
    PARTIAL_TEXT,
    TEXT_EXTRACTED,
    UNSUPPORTED,
    ExtractionResult,
    SegmentBuilder,
)

NAME = "xlsx-cells"
VERSION = "1"


def _render(value) -> tuple[str, str]:
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), "bool"
    if isinstance(value, datetime):
        return value.isoformat(sep=" "), "datetime"
    if isinstance(value, date):
        return value.isoformat(), "date"
    if isinstance(value, time):
        return value.isoformat(), "time"
    if isinstance(value, int | float):
        return repr(value), "number"
    return str(value), "text"


def extract_xlsx(data: bytes, *, filename: str = "") -> ExtractionResult:
    result = ExtractionResult(NAME, VERSION, TEXT_EXTRACTED)
    if filename.lower().endswith(".xls"):
        result.status = UNSUPPORTED
        result.warnings.append("legacy .xls (BIFF) format is not supported; save as .xlsx or "
                               "approve a reader such as xlrd")
        return result
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            values = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            formulas = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
        result.warnings.extend(f"openpyxl: {w.message}" for w in caught)
    except Exception as exc:  # corrupt / not a workbook
        result.status = FAILED
        result.warnings.append(f"could not open workbook: {type(exc).__name__}: {exc}")
        return result

    seg = SegmentBuilder()
    sheets, hidden, missing_cache = [], [], []
    try:
        for ws_v in values.worksheets:
            ws_f = formulas[ws_v.title]
            state = getattr(ws_v, "sheet_state", "visible")
            sheets.append(ws_v.title)
            if state != "visible":
                hidden.append(f"{ws_v.title} ({state})")
            formula_at: dict[str, str] = {}
            for row in ws_f.iter_rows():
                for cell in row:
                    v = getattr(cell, "value", None)
                    if isinstance(v, str) and v.startswith("="):
                        formula_at[cell.coordinate] = v
            for row in ws_v.iter_rows():
                for cell in row:
                    coord = getattr(cell, "coordinate", None)
                    if coord is None:
                        continue
                    value = cell.value
                    formula = formula_at.get(coord)
                    if value is None:
                        if formula:
                            missing_cache.append(f"{ws_v.title}!{coord}")
                        continue
                    text, vtype = _render(value)
                    if vtype == "text" and not text.strip():
                        continue
                    meta = {"type": vtype, "number_format": getattr(cell, "number_format", None)}
                    if formula:
                        meta["formula"] = formula
                    if state != "visible":
                        meta["sheet_state"] = state
                    seg.add(f"{ws_v.title}!{coord}", "cell", text, **meta)
    finally:
        values.close()
        formulas.close()

    result.segments = seg.segments
    result.metadata = {"sheets": sheets, "hidden_sheets": hidden,
                       "formulas_without_cached_value": len(missing_cache)}
    if hidden:
        result.warnings.append(f"hidden sheet(s) extracted and flagged: {', '.join(hidden)}")
    if missing_cache:
        result.status = PARTIAL_TEXT
        shown = ", ".join(missing_cache[:10]) + (" ..." if len(missing_cache) > 10 else "")
        result.warnings.append(
            f"{len(missing_cache)} formula cell(s) have no saved value (the workbook was never "
            f"recalculated in Excel), so their values are unknown: {shown}")
    return result
