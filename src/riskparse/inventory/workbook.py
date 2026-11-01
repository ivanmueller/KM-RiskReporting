"""Write the inventory template (xlsx/csv) and validate completed inventories."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from riskparse.evidence.hashing import is_sha256_hex
from riskparse.identity import InvalidIdentity, normalize_actor, same_person
from riskparse.inventory.template import (
    INSTRUCTIONS,
    KIND_DATE,
    KIND_INT,
    KIND_MONTH,
    KIND_SHA256,
    KIND_TIMESTAMP,
    SHEETS,
    SHEETS_BY_NAME,
    InvSheet,
)

LISTS_SHEET = "Lists"
README_SHEET = "README"
_FIXED_TIMESTAMP = datetime(2026, 10, 31, 0, 0, 0)
_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_REQUIRED_FILL = PatternFill("solid", fgColor="833C0B")


def write_xlsx_template(path: str | Path) -> Path:
    """Create the blank inventory workbook (deterministic metadata, no real data)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    readme = wb.active
    readme.title = README_SHEET
    for i, line in enumerate(INSTRUCTIONS, start=1):
        readme.cell(row=i, column=1, value=line)
    readme["A1"].font = Font(bold=True, size=14)
    readme.column_dimensions["A"].width = 120
    r = len(INSTRUCTIONS) + 2
    readme.cell(row=r, column=1, value="Sheets:").font = Font(bold=True)
    for sheet in SHEETS:
        r += 1
        readme.cell(row=r, column=1, value=f"{sheet.name}: {sheet.purpose}")
    r += 2
    readme.cell(row=r, column=1,
                value="Header colour: dark red = required, dark blue = optional.")

    lists = wb.create_sheet(LISTS_SHEET)
    list_ranges: dict[tuple[str, str], str] = {}
    col_idx = 0
    for sheet in SHEETS:
        for col in sheet.columns:
            if not col.allowed:
                continue
            col_idx += 1
            letter = get_column_letter(col_idx)
            lists.cell(row=1, column=col_idx, value=f"{sheet.name}.{col.name}").font = Font(
                bold=True)
            for j, v in enumerate(col.allowed, start=2):
                lists.cell(row=j, column=col_idx, value=v)
            list_ranges[(sheet.name, col.name)] = (
                f"{LISTS_SHEET}!${letter}$2:${letter}${len(col.allowed) + 1}"
            )
            lists.column_dimensions[letter].width = 28

    for sheet in SHEETS:
        ws = wb.create_sheet(sheet.name, index=wb.sheetnames.index(LISTS_SHEET))
        for c_i, col in enumerate(sheet.columns, start=1):
            cell = ws.cell(row=1, column=c_i, value=col.name)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = _REQUIRED_FILL if col.required else _HEADER_FILL
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            note = col.description + (" [REQUIRED]" if col.required else "")
            if col.allowed:
                note += " Allowed: " + ", ".join(col.allowed)
            cell.comment = Comment(note, "riskparse")
            ws.column_dimensions[get_column_letter(c_i)].width = col.width
            if col.allowed:
                dv = DataValidation(type="list", formula1=f"={list_ranges[(sheet.name, col.name)]}",
                                    allow_blank=True, showErrorMessage=True)
                dv.error = "Choose a value from the list (see Lists sheet)."
                ws.add_data_validation(dv)
                dv.add(f"{get_column_letter(c_i)}2:{get_column_letter(c_i)}1000")
            if col.kind in (KIND_DATE, KIND_MONTH, KIND_TIMESTAMP, KIND_SHA256):
                for row in range(2, 1001):
                    ws.cell(row=row, column=c_i).number_format = "@"  # keep as text
        for r_i, seed in enumerate(sheet.seed_rows, start=2):
            for c_i, col in enumerate(sheet.columns, start=1):
                if col.name in seed:
                    ws.cell(row=r_i, column=c_i, value=seed[col.name])
        ws.freeze_panes = "B2"

    wb.properties.creator = "riskparse"
    wb.properties.title = "October 2026 Source Inventory - TEMPLATE"
    wb.properties.created = _FIXED_TIMESTAMP
    wb.properties.modified = _FIXED_TIMESTAMP
    wb.save(out)
    return out


def write_csv_templates(directory: str | Path) -> list[Path]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for sheet in SHEETS:
        p = d / f"inventory_{sheet.name}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=sheet.headers, lineterminator="\n")
            w.writeheader()
            for seed in sheet.seed_rows:
                w.writerow({h: seed.get(h, "") for h in sheet.headers})
        written.append(p)
    return written


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
ERROR = "ERROR"          # structurally wrong or invalid value
MISSING = "MISSING"      # required value not yet supplied (incomplete inventory)
WARNING = "WARNING"      # suspicious; needs a human look

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_TS_RE = re.compile(
    r"^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})[ T](?P<h>[0-9]{2}):(?P<m>[0-9]{2})"
    r"(?::(?P<s>[0-9]{2}))?\s*(?P<tz>Z|[+-][0-9]{2}:?[0-9]{2}|[A-Z]{2,5}|"
    r"[A-Za-z]+(?:/[A-Za-z_+-]+)+)$", re.ASCII
)
# Timezone abbreviations accepted in the inventory. Abbreviations are ambiguous worldwide,
# so only the North American zones relevant to a Calgary desk, plus UTC/GMT, are allowed.
TZ_ABBREVIATIONS = {"UTC": 0, "GMT": 0, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
                    "CST": -6, "CDT": -5, "EST": -5, "EDT": -4}


def timestamp_problem(value: str) -> str | None:
    """Why ``value`` is not a real date/time with a real timezone, or None (Codex review)."""
    m = _TS_RE.match(value)
    if not m:
        return "expected 'YYYY-MM-DD HH:MM' plus an explicit timezone"
    try:
        date.fromisoformat(m["date"])
    except ValueError:
        return "invalid calendar date"
    if int(m["h"]) > 23 or int(m["m"]) > 59 or (m["s"] and int(m["s"]) > 59):
        return "invalid time of day"
    tz = m["tz"]
    if tz == "Z":
        return None
    if tz[0] in "+-":
        digits = tz[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            return f"invalid UTC offset {tz}"
        return None
    if "/" not in tz:
        return None if tz in TZ_ABBREVIATIONS else (
            f"unknown timezone abbreviation {tz}; use one of {sorted(TZ_ABBREVIATIONS)} or an "
            "offset such as -07:00")
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        return f"unknown timezone {tz}"
    except Exception:  # pragma: no cover - tz database unavailable (e.g. Windows w/o tzdata)
        return f"cannot verify timezone {tz} on this machine; use an offset such as -07:00"
    return None


@dataclass(frozen=True)
class Issue:
    severity: str
    sheet: str
    row: int | None
    column: str | None
    message: str

    def __str__(self) -> str:
        loc = self.sheet + (f" row {self.row}" if self.row else "") + (
            f" [{self.column}]" if self.column else "")
        return f"{self.severity:<7} {loc}: {self.message}"


def _as_text(v, kind: str | None = None) -> str:
    """Render a cell as text. ``kind`` lets real Excel date cells validate correctly.

    * date column + midnight datetime/date -> ``YYYY-MM-DD``
    * month column + first-of-month midnight (Excel turns a typed ``2026-10`` into
      1 Oct 2026) -> ``YYYY-MM``
    * timestamp column: a date/time cell carries no timezone, so it is rendered
      without one and fails validation - the user must type the timezone explicitly.
    """
    if v is None:
        return ""
    if isinstance(v, datetime):
        midnight = (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0)
        if kind == KIND_DATE and midnight:
            return v.date().isoformat()
        if kind == KIND_MONTH and midnight and v.day == 1:
            return v.strftime("%Y-%m")
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date) and kind == KIND_MONTH and v.day == 1:
        return v.strftime("%Y-%m")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _check_value(sheet: InvSheet, col, value: str, row: int, issues: list[Issue]) -> None:
    if col.allowed and value not in col.allowed:
        issues.append(Issue(ERROR, sheet.name, row, col.name,
                            f"'{value}' not in allowed values {list(col.allowed)}"))
    if col.kind == KIND_DATE:
        if not _DATE_RE.match(value):
            issues.append(Issue(ERROR, sheet.name, row, col.name, "expected YYYY-MM-DD"))
        else:
            try:
                date.fromisoformat(value)
            except ValueError:
                issues.append(Issue(ERROR, sheet.name, row, col.name, "invalid calendar date"))
    elif col.kind == KIND_MONTH and not _MONTH_RE.match(value):
        issues.append(Issue(ERROR, sheet.name, row, col.name, "expected YYYY-MM"))
    elif col.kind == KIND_TIMESTAMP and (problem := timestamp_problem(value)):
        issues.append(Issue(ERROR, sheet.name, row, col.name, problem))
    elif col.kind == KIND_INT and not value.isdigit():
        issues.append(Issue(ERROR, sheet.name, row, col.name, "expected a whole number"))
    elif col.kind == KIND_SHA256 and not is_sha256_hex(value.lower()):
        issues.append(Issue(ERROR, sheet.name, row, col.name, "expected 64 hex characters"))


def validate_rows(rows_by_sheet: dict[str, list[dict[str, str]]],
                  headers_by_sheet: dict[str, list[str]] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for sheet in SHEETS:
        if sheet.name not in rows_by_sheet:
            issues.append(Issue(ERROR, sheet.name, None, None, "sheet missing"))
            continue
        if headers_by_sheet is not None:
            got = headers_by_sheet.get(sheet.name, [])
            if got != sheet.headers:
                missing = [h for h in sheet.headers if h not in got]
                extra = [h for h in got if h not in sheet.headers]
                issues.append(Issue(ERROR, sheet.name, 1, None,
                                    f"header mismatch; missing={missing} extra={extra} "
                                    "(order must match template)"))
                continue
        seen_ids: dict[str, int] = {}
        for r_i, row in enumerate(rows_by_sheet[sheet.name], start=2):
            if not any(row.values()):
                continue
            rid = row.get(sheet.id_column, "")
            if rid:
                if rid in seen_ids:
                    issues.append(Issue(ERROR, sheet.name, r_i, sheet.id_column,
                                        f"duplicate ID {rid} (first at row {seen_ids[rid]})"))
                seen_ids.setdefault(rid, r_i)
            for col in sheet.columns:
                value = row.get(col.name, "")
                if not value:
                    if col.required:
                        issues.append(Issue(MISSING, sheet.name, r_i, col.name,
                                            "required value missing"))
                    continue
                _check_value(sheet, col, value, r_i, issues)
            _row_rules(sheet.name, row, r_i, issues)
    _cross_rules(rows_by_sheet, issues)
    return issues


# Columns that name a person. A value that cannot be normalized to a valid identity
# (invisible/control characters, blank after trimming, too long) is an ERROR rather than
# silently counting as "a different person" (Codex review #3).
PERSON_COLUMNS = ("confirmed_by_risk", "source_owner", "inventoried_by", "exported_by",
                  "maintained_by", "reviewed_by", "risk_approver", "finance_approver",
                  "keyed_by", "verified_by")


def _row_rules(sheet: str, row: dict[str, str], r_i: int, issues: list[Issue]) -> None:
    for col in PERSON_COLUMNS:
        value = row.get(col, "")
        if value:
            try:
                normalize_actor(value)
            except InvalidIdentity as exc:
                issues.append(Issue(ERROR, sheet, r_i, col, f"invalid person identity: {exc}"))
    if sheet == "Contract_Documents":
        if row.get("inventory_status") in ("EXCEPTED", "MISSING", "OUT_OF_SCOPE") and not row.get(
                "exception_reason"):
            issues.append(Issue(ERROR, sheet, r_i, "exception_reason",
                                "exception_reason required for this inventory_status"))
    if sheet == "Evergreen_Agreements" and row.get("interpretation_status") == "APPROVED":
        ra, fa = row.get("risk_approver"), row.get("finance_approver")
        if not ra or not fa:
            issues.append(Issue(ERROR, sheet, r_i, "interpretation_status",
                                "APPROVED requires both risk_approver and finance_approver"))
        elif same_person(ra, fa):
            issues.append(Issue(ERROR, sheet, r_i, "finance_approver",
                                "Risk and Finance approvers must be different people"))
        if not row.get("approval_date"):
            issues.append(Issue(ERROR, sheet, r_i, "approval_date",
                                "APPROVED requires an approval_date"))
    if sheet == "Trade_Crosswalk":
        kb, vb = row.get("keyed_by", ""), row.get("verified_by", "")
        if same_person(kb, vb):
            issues.append(Issue(ERROR, sheet, r_i, "verified_by",
                                "independent verifier must differ from keyed_by"))
        ds, de = row.get("delivery_start", ""), row.get("delivery_end", "")
        if _DATE_RE.match(ds) and _DATE_RE.match(de) and de < ds:
            issues.append(Issue(ERROR, sheet, r_i, "delivery_end", "delivery_end before start"))


def _cross_rules(rows_by_sheet: dict[str, list[dict[str, str]]], issues: list[Issue]) -> None:
    def filled(name):
        return [r for r in rows_by_sheet.get(name, []) if any(r.values())]

    trader_sources = [r for r in filled("Source_Coverage") if r.get("source_type") ==
                      "TRADER_SHEET" and r.get("trader_name")]
    if len(trader_sources) < 2:
        issues.append(Issue(WARNING, "Source_Coverage", None, "trader_name",
                            f"only {len(trader_sources)} trader source(s) named; the firm has "
                            "2-3 traders and every one must be inventoried"))
    if not [r for r in filled("Source_Coverage") if r.get("source_type") == "EGISTIX"]:
        issues.append(Issue(ERROR, "Source_Coverage", None, None, "no Egistix source row"))
    named_traders = {r["trader_name"].strip().lower() for r in trader_sources}
    for r_i, r in enumerate(rows_by_sheet.get("Trade_Crosswalk", []), start=2):
        t = (r.get("trader_name") or "").strip().lower()
        if t and t not in named_traders:
            issues.append(Issue(ERROR, "Trade_Crosswalk", r_i, "trader_name",
                                f"trader '{r['trader_name']}' has no TRADER_SHEET row in "
                                "Source_Coverage"))
    doc_ids = {r.get("doc_inventory_id") for r in filled("Contract_Documents")}
    for sheet in ("Amendments_Cancellations", "Evergreen_Agreements"):
        for r_i, r in enumerate(rows_by_sheet.get(sheet, []), start=2):
            ref = r.get("doc_inventory_id")
            if ref and ref not in doc_ids:
                issues.append(Issue(ERROR, sheet, r_i, "doc_inventory_id",
                                    f"{ref} not found in Contract_Documents"))
    hashes: dict[str, str] = {}
    for r in filled("Contract_Documents"):
        h = (r.get("sha256") or "").lower()
        if h:
            if h in hashes:
                issues.append(Issue(WARNING, "Contract_Documents", None, "sha256",
                                    f"{r.get('doc_inventory_id')} has identical bytes to "
                                    f"{hashes[h]} (duplicate file - register once)"))
            hashes.setdefault(h, r.get("doc_inventory_id", ""))


def read_xlsx(path: str | Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[str]]]:
    rows, headers, _formulas = _read_xlsx_with_formulas(path)
    return rows, headers


def _formula_cells(path: str | Path) -> list[tuple[str, int, str]]:
    """(sheet, row, coordinate) of every formula cell in the inventory sheets."""
    wb = load_workbook(path, read_only=True, data_only=False)
    found = []
    try:
        for name in SHEETS_BY_NAME:
            if name not in wb.sheetnames:
                continue
            for row in wb[name].iter_rows():
                for cell in row:
                    v = getattr(cell, "value", None)
                    if isinstance(v, str) and v.startswith("="):
                        found.append((name, cell.row, cell.coordinate))
    finally:
        wb.close()
    return found


def _read_xlsx_with_formulas(path: str | Path):
    formulas = _formula_cells(path)
    wb = load_workbook(path, read_only=True, data_only=True)
    rows_by_sheet: dict[str, list[dict[str, str]]] = {}
    headers_by_sheet: dict[str, list[str]] = {}
    for name in SHEETS_BY_NAME:
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        it = ws.iter_rows(values_only=True)
        header_row = next(it, ())
        headers = [_as_text(h) for h in header_row]
        while headers and not headers[-1]:
            headers.pop()
        headers_by_sheet[name] = headers
        kinds = {c.name: c.kind for c in SHEETS_BY_NAME[name].columns}
        rows = []
        for values in it:
            rows.append({h: _as_text(values[i], kinds.get(h)) if i < len(values) else ""
                         for i, h in enumerate(headers)})
        rows_by_sheet[name] = rows
    wb.close()
    return rows_by_sheet, headers_by_sheet, formulas


def validate_xlsx(path: str | Path) -> list[Issue]:
    rows, headers, formulas = _read_xlsx_with_formulas(path)
    # A formula cell has no saved value until Excel recalculates, so its row would read as
    # empty and vanish. The inventory is a record: values must be typed (Codex review).
    issues = [Issue(ERROR, sheet, row, coord, "cell contains a formula; enter the value "
                    "itself (formulas may have no saved value and the row would be lost)")
              for sheet, row, coord in formulas]
    return issues + validate_rows(rows, headers)
