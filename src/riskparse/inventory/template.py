"""Structured source-inventory template (game plan 2.3, 7.1, 8.1, 14.2).

The inventory is a Week 1 *human* deliverable completed by Risk and signed off before
any automated ingestion is trusted. It enumerates, independently of each other:

* every trade-record source (all 2-3 trader sheets, Egistix, contract folders),
* every October 2026 contract document (including scans, emails, attachments),
* Egistix exports and every trader sheet with their as-of timestamps,
* amendments / cancellations / terminations,
* active evergreen agreements (including pre-October ones) - with the stated
  "value" captured as **raw text only** and interpretation defaulting to UNRESOLVED,
* a hand-keyed trade crosswalk across sources,
* open questions / blockers.

This module defines the sheets as data so the same definition drives the generated
workbook, CSV templates and the validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from riskparse import vocab

YES_NO = ("Y", "N")
YES_NO_UNKNOWN = ("Y", "N", "UNKNOWN")
YES_NO_NA_UNKNOWN = ("Y", "N", "NA", "UNKNOWN")

INVENTORY_STATUSES = ("LISTED", "REGISTERED", "EXCEPTED", "MISSING", "OUT_OF_SCOPE")
COVERAGE_SOURCE_TYPES = ("TRADER_SHEET", "EGISTIX", "CONTRACT_FOLDER", "EVERGREEN_REGISTRY",
                         "BROKER", "EXCHANGE", "OTHER")
EGISTIX_COVERAGE = ("YES", "NO", "PARTIAL", "UNKNOWN")
AMENDMENT_EVENT_TYPES = ("AMENDMENT", "CANCELLATION", "TERMINATION", "NOVATION", "OTHER")
SUPERSESSION_SCOPES = ("PARTIAL", "FULL", "UNKNOWN")
ACTIVE_FLAGS = ("YES", "NO", "UNKNOWN")
QUESTION_STATUSES = ("OPEN", "ANSWERED", "CLOSED")
QUESTION_AREAS = ("IT_SQL", "IT_STORAGE", "IT_SECURITY", "EGISTIX", "TRADERS", "RISK",
                  "FINANCE", "LEGAL", "OTHER")

KIND_TEXT = "text"
KIND_DATE = "date"            # YYYY-MM-DD
KIND_TIMESTAMP = "timestamp"  # YYYY-MM-DD HH:MM[:SS] with explicit offset or zone
KIND_INT = "int"
KIND_SHA256 = "sha256"
KIND_MONTH = "month"          # YYYY-MM


@dataclass(frozen=True)
class InvColumn:
    name: str
    description: str
    required: bool = False
    allowed: tuple[str, ...] | None = None
    kind: str = KIND_TEXT
    width: int = 18


@dataclass(frozen=True)
class InvSheet:
    name: str
    purpose: str
    id_column: str
    columns: tuple[InvColumn, ...]
    seed_rows: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def headers(self) -> list[str]:
        return [c.name for c in self.columns]


C = InvColumn

SOURCE_COVERAGE = InvSheet(
    name="Source_Coverage",
    purpose=("One row per independent trade-record source. Every in-scope trader (all 2-3) "
             "must appear. Absence from Egistix does not make a trade invalid."),
    id_column="coverage_id",
    columns=(
        C("coverage_id", "Unique ID, e.g. SRC-TRADER-01", True, width=16),
        C("source_type", "Kind of source", True, COVERAGE_SOURCE_TYPES),
        C("trader_name", "Trader responsible (blank for system sources)", width=18),
        C("book", "Trading book(s)"),
        C("trade_types_covered", "e.g. physical gas, financial power", True, width=26),
        C("source_location", "UNC path or system name (no L: mappings)", True, width=40),
        C("file_or_report_name", "Sheet / report name", width=26),
        C("update_frequency", "How often the source is updated"),
        C("source_owner", "Accountable owner", True),
        C("egistix_covers", "Does Egistix contain these trades?", True, EGISTIX_COVERAGE),
        C("known_gaps", "Known exclusions or gaps", width=30),
        C("october_export_obtained", "Dated October copy obtained?", True, YES_NO),
        C("confirmed_by_risk", "Risk reviewer confirming this row"),
        C("confirmed_date", "Date confirmed", kind=KIND_DATE),
        C("notes", "Free text", width=30),
    ),
    seed_rows=(
        {"coverage_id": "SRC-TRADER-01", "source_type": "TRADER_SHEET"},
        {"coverage_id": "SRC-TRADER-02", "source_type": "TRADER_SHEET"},
        {"coverage_id": "SRC-TRADER-03", "source_type": "TRADER_SHEET",
         "notes": "Third trader - confirm whether in scope; delete row only with Risk sign-off"},
        {"coverage_id": "SRC-EGISTIX-01", "source_type": "EGISTIX",
         "notes": "Incomplete trade coverage - document exactly what is excluded"},
        {"coverage_id": "SRC-CONTRACTS-01", "source_type": "CONTRACT_FOLDER",
         "notes": "October 2026 Physical / Financial / Evergreen folders"},
        {"coverage_id": "SRC-EVERGREEN-01", "source_type": "EVERGREEN_REGISTRY",
         "notes": "Active evergreen agreements from prior years that affect October"},
    ),
)

CONTRACT_DOCUMENTS = InvSheet(
    name="Contract_Documents",
    purpose=("Every known October 2026 contract document, amendment, cancellation, email "
             "and scan - plus earlier documents still affecting October. Do not infer terms "
             "from file names."),
    id_column="doc_inventory_id",
    columns=(
        C("doc_inventory_id", "Unique ID, e.g. INV-DOC-0001", True, width=16),
        C("file_name", "Exact file name as found", True, width=36),
        C("unc_path", "Full UNC path (\\\\server\\share\\...)", True, width=50),
        C("file_type", "File type", True, vocab.DOCUMENT_FILE_TYPES),
        C("document_category", "Document category", True, vocab.DOCUMENT_CATEGORIES),
        C("physical_financial", "Economic type", True, vocab.PHYSICAL_FINANCIAL),
        C("evergreen_flag", "Evergreen lifecycle?", True, YES_NO_UNKNOWN),
        C("counterparty_raw_name", "Legal name exactly as on document", width=28),
        C("trade_date", "Execution date per document", kind=KIND_DATE),
        C("received_date", "Date received", kind=KIND_DATE),
        C("counterparty_reference", "Counterparty confirm / deal ref"),
        C("related_trade_refs", "Egistix / trader refs, ';'-separated", width=24),
        C("is_scanned", "Scanned image (OCR needed)?", True, YES_NO_UNKNOWN),
        C("email_body_has_terms", "Email body itself contains terms?", True, YES_NO_NA_UNKNOWN),
        C("parent_doc_inventory_id", "Parent email / original contract row"),
        C("sha256", "SHA-256 of bytes (from riskparse inventory-scan)", kind=KIND_SHA256,
          width=66),
        C("size_bytes", "Size in bytes", kind=KIND_INT),
        C("registered_document_id", "SQL document_id once registered", width=40),
        C("folder_hint", "Folder it was found in - a hint, NOT authority", width=20),
        C("inventory_status", "Inventory status", True, INVENTORY_STATUSES),
        C("exception_reason", "Required if EXCEPTED / MISSING / OUT_OF_SCOPE", width=30),
        C("inventoried_by", "Person who listed it", True),
        C("inventoried_date", "Date listed", True, kind=KIND_DATE),
        C("notes", "Free text", width=30),
    ),
)

EGISTIX_EXPORTS = InvSheet(
    name="Egistix_Exports",
    purpose="Each dated Egistix export used, unchanged, with its cutoff and coverage limits.",
    id_column="export_id",
    columns=(
        C("export_id", "Unique ID, e.g. EGX-2026-10-01", True, width=16),
        C("report_name", "Egistix report / view name", True, width=26),
        C("export_file_name", "Exported file name", True, width=32),
        C("unc_path", "Stored location (UNC)", True, width=50),
        C("exported_by", "User who ran the export", True),
        C("export_timestamp", "When exported, with timezone e.g. 2026-11-03 09:15 MST", True,
          kind=KIND_TIMESTAMP, width=24),
        C("reporting_period", "Period covered, YYYY-MM", True, kind=KIND_MONTH),
        C("filter_criteria", "Filters applied in Egistix", width=26),
        C("row_count", "Rows in export", kind=KIND_INT),
        C("sha256", "SHA-256 of export bytes", kind=KIND_SHA256, width=66),
        C("products_not_covered", "Trade types Egistix does not hold", width=28),
        C("zero_value_rows", "Count of rows showing $0 value", kind=KIND_INT),
        C("field_dictionary_obtained", "Egistix field definitions obtained?", True, YES_NO),
        C("egistix_owner", "Egistix system owner"),
        C("notes", "Free text", width=30),
    ),
)

TRADER_SHEETS = InvSheet(
    name="Trader_Sheets",
    purpose="Every trader's own record for October - all 2-3 traders, not only one sheet.",
    id_column="sheet_id",
    columns=(
        C("sheet_id", "Unique ID, e.g. TS-01", True, width=10),
        C("trader_name", "Trader", True),
        C("book", "Book(s)"),
        C("file_name", "Workbook file name", True, width=32),
        C("unc_path", "Stored copy location (UNC)", True, width=50),
        C("tab_name", "Worksheet tab(s) used", width=20),
        C("as_of_timestamp", "Copy taken at, with timezone", True, kind=KIND_TIMESTAMP,
          width=24),
        C("period_covered", "YYYY-MM", True, kind=KIND_MONTH),
        C("row_count", "Trade rows for the period", kind=KIND_INT),
        C("sha256", "SHA-256 of the copy", kind=KIND_SHA256, width=66),
        C("trade_types", "Trade types recorded"),
        C("maintained_by", "Who maintains the sheet", True),
        C("includes_non_egistix_trades", "Contains trades absent from Egistix?", True,
          YES_NO_UNKNOWN),
        C("notes", "Free text", width=30),
    ),
    seed_rows=(
        {"sheet_id": "TS-01", "period_covered": "2026-10"},
        {"sheet_id": "TS-02", "period_covered": "2026-10"},
        {"sheet_id": "TS-03", "period_covered": "2026-10",
         "notes": "Third trader - confirm whether in scope"},
    ),
)

AMENDMENTS = InvSheet(
    name="Amendments_Cancellations",
    purpose=("Amendments, cancellations and terminations as separate documents. Never assume "
             "an amendment supersedes the whole original contract."),
    id_column="event_id",
    columns=(
        C("event_id", "Unique ID, e.g. AMD-0001", True, width=12),
        C("doc_inventory_id", "Row in Contract_Documents", True),
        C("event_type", "Type of change", True, AMENDMENT_EVENT_TYPES),
        C("parent_contract_ref", "Reference of contract being changed", True, width=24),
        C("parent_doc_inventory_id", "Original contract row in Contract_Documents"),
        C("effective_date", "Effective date of the change", kind=KIND_DATE),
        C("fields_changed", "Which provisions actually changed", width=30),
        C("supersession_scope", "Does it replace all terms or only some?", True,
          SUPERSESSION_SCOPES),
        C("reviewed_by", "Risk reviewer"),
        C("notes", "Free text", width=30),
    ),
)

EVERGREEN = InvSheet(
    name="Evergreen_Agreements",
    purpose=("All active evergreen agreements, including ones signed before October. Record "
             "the stated value EXACTLY as written; do not interpret, convert or assume $0 means "
             "missing. Interpretation stays UNRESOLVED until Risk and Finance approve."),
    id_column="agreement_id",
    columns=(
        C("agreement_id", "Unique ID, e.g. EVG-0001", True, width=12),
        C("doc_inventory_id", "Row in Contract_Documents", True),
        C("counterparty_raw_name", "Legal name as written", True, width=28),
        C("original_execution_date", "Signature / execution date", kind=KIND_DATE),
        C("effective_start_date", "Stated start date", kind=KIND_DATE),
        C("stated_value_raw_text", "Exact quoted text of the 'value'", True, width=36),
        C("stated_value_location", "Page / section / clause of that text", True, width=22),
        C("term_end_text", "Exact end-date / term wording (blank != infinite)", width=30),
        C("renewal_clause_location", "Page / section"),
        C("notice_terms_text", "Exact termination notice wording", width=30),
        C("quantity_terms_text", "Firm / min / max quantity wording", width=30),
        C("pricing_terms_text", "Index / fixing / adjustment wording", width=30),
        C("egistix_displayed_value", "Value shown in Egistix, as shown"),
        C("trader_sheet_displayed_value", "Value shown in trader sheet(s), as shown"),
        C("active_for_2026_10", "Active in October 2026?", True, ACTIVE_FLAGS),
        C("interpretation_status", "Default UNRESOLVED", True, vocab.EVERGREEN_INTERPRETATION),
        C("proposed_meaning", "Proposed meaning with clause citation", width=36),
        C("risk_approver", "Risk approver (required for APPROVED)"),
        C("finance_approver", "Finance approver (required for APPROVED)"),
        C("approval_date", "Date approved", kind=KIND_DATE),
        C("notes", "Free text", width=30),
    ),
)

TRADE_CROSSWALK = InvSheet(
    name="Trade_Crosswalk",
    purpose=("Hand-keyed list of every October trade from ANY source, showing where each "
             "appears. 35 trades may map to a different number of documents. Raw values are "
             "copied as written - no unit or currency conversion."),
    id_column="crosswalk_id",
    columns=(
        C("crosswalk_id", "Unique ID, e.g. XW-0001", True, width=10),
        C("trader_name", "Trader", True),
        C("trade_date", "Execution date", True, kind=KIND_DATE),
        C("counterparty_raw_name", "Counterparty as written", True, width=28),
        C("commodity", "e.g. NATURAL_GAS, POWER, CRUDE"),
        C("physical_financial", "Economic type", True, vocab.PHYSICAL_FINANCIAL),
        C("side", "BUY / SELL from OUR firm's perspective", allowed=vocab.SIDES),
        C("volume_raw", "Volume exactly as written"),
        C("volume_unit", "Unit as written (GJ, MMBtu, MWh, bbl...)"),
        C("volume_basis", "Per day / total ...", allowed=vocab.VOLUME_BASES),
        C("price_raw", "Price / formula exactly as written", width=24),
        C("price_unit", "Price unit as written"),
        C("delivery_location", "Hub / location"),
        C("delivery_start", "Start date", kind=KIND_DATE),
        C("delivery_end", "End date", kind=KIND_DATE),
        C("trader_sheet_ref", "Ref in trader sheet (sheet_id:row)"),
        C("egistix_deal_id", "Egistix deal ID"),
        C("counterparty_confirm_ref", "Counterparty confirm ref"),
        C("doc_inventory_ids", "Contract_Documents IDs, ';'-separated", width=24),
        C("in_trader_sheet", "Found in trader sheet?", True, YES_NO),
        C("in_egistix", "Found in Egistix?", True, YES_NO_NA_UNKNOWN),
        C("confirm_found", "Executed confirmation found?", True, YES_NO),
        C("preliminary_status", "Preliminary reconciliation status", True,
          vocab.RECONCILIATION_STATUSES),
        C("evidence_location", "Page / cell of the keyed values", width=22),
        C("keyed_by", "Who keyed this row", True),
        C("verified_by", "Independent Risk verifier (must differ from keyed_by)"),
        C("notes", "Free text", width=30),
    ),
)

OPEN_QUESTIONS = InvSheet(
    name="Open_Questions",
    purpose="Blockers and open questions with an owner and due date.",
    id_column="question_id",
    columns=(
        C("question_id", "Unique ID, e.g. Q-001", True, width=10),
        C("area", "Area", True, QUESTION_AREAS),
        C("question", "Question / blocker", True, width=60),
        C("owner", "Accountable owner"),
        C("raised_date", "Date raised", kind=KIND_DATE),
        C("due_date", "Needed by", kind=KIND_DATE),
        C("is_blocker", "Blocks Phase 2 start?", True, YES_NO),
        C("status", "Status", True, QUESTION_STATUSES),
        C("answer", "Answer / evidence", width=40),
    ),
    seed_rows=tuple(
        {"question_id": qid, "area": area, "question": q, "is_blocker": "Y", "status": "OPEN",
         "raised_date": "2026-10-31"}
        for qid, area, q in (
            ("Q-001", "IT_SQL", "Which SQL product/edition and version hosts the risk database? "
             "(SQL Server / Azure SQL / other)"),
            ("Q-002", "IT_SQL", "Server/instance, database and schema names for DEV/TEST and "
             "PROD; are they separated?"),
            ("Q-003", "IT_SQL", "Client driver and authentication method (Windows integrated / "
             "managed identity / SQL login); service identity for scheduled runs."),
            ("Q-004", "IT_SQL", "Database owner/DBA, role model (staging writer, reporter, "
             "publisher), backup frequency, recovery model, restore testing."),
            ("Q-005", "IT_STORAGE", "Exact UNC path for the L drive Risk Reporting root and "
             "the restricted 07_SourceArchive; share+NTFS permissions and backup."),
            ("Q-006", "IT_SECURITY", "Approved Python environment/packages on the controlled "
             "machine; dependency licence/vulnerability review owner."),
            ("Q-007", "EGISTIX", "What does a $0 value in Egistix encode for evergreen deals? "
             "Field dictionary and export coverage."),
            ("Q-008", "TRADERS", "Confirm the 2 or 3 in-scope traders, their books and the "
             "location of each trader's October sheet."),
            ("Q-009", "RISK", "Approved reconciliation status definitions, aging rule and "
             "escalation owner."),
            ("Q-010", "LEGAL", "Is any enterprise AI service approved for contract "
             "interpretation? (Until then: manual review only, no confidential data to AI)."),
        )
    ),
)

SHEETS: tuple[InvSheet, ...] = (
    SOURCE_COVERAGE, CONTRACT_DOCUMENTS, EGISTIX_EXPORTS, TRADER_SHEETS, AMENDMENTS, EVERGREEN,
    TRADE_CROSSWALK, OPEN_QUESTIONS,
)
SHEETS_BY_NAME = {s.name: s for s in SHEETS}

INSTRUCTIONS = (
    "RISK REPORTING - OCTOBER 2026 SOURCE INVENTORY (TEMPLATE)",
    "",
    "Purpose: independently enumerate every source of October 2026 trade evidence before "
    "any automated ingestion is trusted (game plan sections 2.3, 7.1, 8, 14.2).",
    "",
    "Rules:",
    "1. Save completed copies ONLY on the controlled L drive / UNC share. Never commit a "
    "completed inventory, contract, export or trader sheet to Git or send it to an "
    "unapproved AI tool.",
    "2. Enumerate all 2-3 traders' sheets, Egistix exports and contract folders separately. "
    "Absence from Egistix does not make a trade invalid.",
    "3. Copy values exactly as written. Do not convert units or currencies; do not infer "
    "economics from file names or folders.",
    "4. Evergreen 'value': quote the exact text and clause location. interpretation_status "
    "stays UNRESOLVED until both Risk and Finance approve. $0 is not assumed to mean missing.",
    "5. Hashes: run `riskparse inventory-scan --root <UNC folder> --out <csv>` to obtain "
    "SHA-256 values without opening files; paste into Contract_Documents.",
    "6. Anything that cannot be listed or parsed is recorded as EXCEPTED / MISSING with a "
    "reason - never silently skipped.",
    "7. Validate with `riskparse inventory-validate <file.xlsx>` before sign-off.",
    "8. Dates: YYYY-MM-DD. Timestamps: YYYY-MM-DD HH:MM plus timezone (e.g. MST, -07:00).",
    "",
    "Sign-off (Risk manager): ____________________   Date: ____________",
)
