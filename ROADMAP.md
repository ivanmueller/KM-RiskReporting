# Roadmap and task tracker

Source: *Risk Reporting Automation — Project Game Plan v2.0*, §1.3 (stages), §14 (weeks),
§14.2 (Week 1 actions), §17 (sign-off gates). Last updated **2026-10-31** (after the B-fixes).

**Legend:** `[x]` done · `[~]` partly done (the note says what is left) · `[ ]` not started ·
**(blocked: B-xx)** see `docs/blockers.md` · **(owner)** people other than the developer.

A box is ticked only when the work exists in this repository **and** has been tested, or when the
named owner has signed it off. Code tested on synthetic data counts as done for the code, but never
for a gate that needs real data or sign-off.

---

## Current position

- **Stage 1, Phase 1 (foundation):** done.
- **Week 1 discovery:** in progress. The October trade zip has been received and needs to be inventoried.
- **Engineering:** B1–B5, B7 and B8 are done. Contract **text extraction** (`.pdf`, `.xlsx`, `.msg` with attachments), `profile-documents` and layout fingerprints are built (264 tests). The Codex review (B6) is in progress.
- **Your next step:** run `docs/contract_extraction.md` steps 1–4 on the October zip and send back the profile **summary** (counts only). It decides which counterparty layouts get a parser first.

## NOW — next actions (in order)

### A. October trade zip (Risk analyst + developer, on the controlled machine)

- [ ] **A1. Keep the zip off GitHub and out of unapproved AI tools.** Store the original zip, unchanged, in the restricted
  L drive location, or a local folder **outside** this repo until IT gives the UNC path. Don't
  upload it to GitHub or to a cloud AI session (that includes this one) unless security has approved it (B-10).
- [ ] **A2. Note who sent the zip, and when.** You'll record this on the inventory row for the zip.
- [ ] **A3–A5. Run the safe intake** (tooling ready, B8). This one command hashes the zip as received, extracts it into a
  new empty folder, hashes every file, refuses unsafe paths, links and encrypted members, flags macro-enabled
  files, nested zips, duplicate names and case collisions, and writes an inventory manifest:
  `riskparse intake-zip <zip> --extract-to <L-drive>\Oct2026_extracted --manifest <L-drive>\intake_2026-10.csv`
  Keep the original zip as the evidence copy. Don't enable macros or editing when you open anything.
  Review every finding the command prints.
- [ ] **A6. Fill in the inventory workbook** (`inventory/templates/October_2026_Source_Inventory_TEMPLATE.xlsx`,
  saved as a working copy on the L drive):
  - paste the manifest rows into `Contract_Documents` (the zip is row `INV-ZIP-0001`; refused members appear as `EXCEPTED`);
  - classify each one **from its content**, not from its filename;
  - flag scans, emails and amendments.
- [ ] **A7. Count and size the work** (tooling ready: `riskparse profile-documents`, counts plus layout groups): documents per format (PDF text / PDF scan / Excel / `.msg` / `.eml`) and
  documents per trade. Check this against the roughly 35 trades. These counts decide which parsers get built first (Week 3).
- [ ] **A8. Find what's missing:**
  - does the zip contain Egistix exports? All 2–3 trader sheets? Amendments? Evergreen agreements
    signed **before** October that still apply?
  - log every gap in `Open_Questions`.
- [ ] **A9. Hand-key every October trade into `Trade_Crosswalk`** (raw values only). A second person verifies each row.
- [ ] **A10. Validate the workbook:** `riskparse inventory-validate <workbook> --strict`. Fix every ERROR and fill every MISSING.
- [ ] **A11. Risk manager signs off the inventory** (the Week 1 exit gate).

### B. Engineering (no real data needed)

- [x] **B1. Fix D-1:** type-check every config field, so that `authorized = "false"` can't let a connection through. Add a regression test.
- [x] **B2. Fix D-2:** strict decimal parsing, so that `"2,65"`, `NaN` and `Infinity` are rejected rather than turned into numbers. Add a regression test.
- [x] **B3. Fix D-3:** accept real Excel date cells in the inventory validator.
- [x] **B4. Fix D-4:** make the creator/approver identity checks case-insensitive. *(identity normalization plus migration `0002`; also fixed D-6, a migration failure on SQLite databases holding linked rows)*
- [x] **B5. Fix D-5:** reject unknown config keys and sections cleanly.
- [ ] **B6. Run the Codex review** (you run it; prompt updated for the fixes) (`docs/review/codex_stress_test_prompt.md`) and triage its findings into `HANDOFF.md`.
- [x] **B7. Fix S-1** (a row compared on nothing must not be `MATCHED`) and **S-4** (re-check the file hash before importing). *Also fixed S-5 (CSV shape errors) and S-13 (data dictionary generator).*
- [x] **B8. Add a safe "register intake zip" command** (`riskparse intake-zip`, with `--register` to also write to the DB): hash the zip, extract it without zip-slip,
  register each member with `parent_document_id` pointing at the zip, and record nested archives as exceptions.

### C. IT / owners (in parallel)

- [ ] **C1. Answer** `docs/sql_platform_confirmation.md`: engine, DEV/TEST instance, driver, sign-in method, roles, backups. **(IT)** (B-01, B-02, B-03)
- [ ] **C2. Provide the UNC paths and permissions** for the Risk Reporting root and `07_SourceArchive`. **(IT)** (B-04)
- [ ] **C3. Provide dated Egistix exports and the field dictionary**, and explain what `$0` means for evergreen deals. **(Egistix owner)** (B-06)
- [ ] **C4. Confirm the in-scope traders and books, and provide each trader's October sheet.** **(Risk + traders)** (B-07)
- [ ] **C5. Approve the Python environment and packages** in `requirements/lock.txt`. **(IT security)** (B-08)
- [ ] **C6. Approve the reconciliation status definitions, aging rule, escalation owner and hard blockers.** **(Risk manager)** (B-09)

---

## Stage 1 — Contract intelligence and reconciliation

### Phase 1 — Foundation (repository, inventory framework, minimal DB) — DONE

- [x] Inspect the project folder and game plan (the repo was empty)
- [x] Modular package `riskparse`, `pyproject.toml`, pinned `requirements/lock.txt`, CLI
- [x] `.gitignore` that keeps confidential files out, plus tests that check it
- [x] Config with credential and production guards *(D-1 and D-5 fixed)*
- [x] Connection layer: SQLite test adapter; SQL Server/PostgreSQL candidates that refuse to connect unless authorized *(the candidates are unverified)*
- [x] Minimal schema (16 tables) and frozen migrations `0001_initial` and `0002_sod_case_and_zip`; upgrade/downgrade tested; matches the schema definition, including CHECK text
- [x] Data dictionary (`docs/data_dictionary.md`), generated by `riskparse gen-data-dictionary` and drift-tested
- [x] SHA-256 identity, stable-file check, content-addressed archive, duplicate vs revision detection
- [x] Hash-chained audit events and `audit-verify`
- [x] Source-inventory template (xlsx + csv), validator, read-only folder scanner
- [x] CSV source import that keeps raw rows unchanged, with strict parsing and a hash re-check *(D-2, S-4, S-5 fixed)*
- [x] Safe intake of a received ZIP (`intake-zip`)
- [x] Exact-reference reconciliation slice
- [x] End-to-end smoke demo on synthetic data (candidate only, never published)
- [x] Docs: scope, SQL checklist, inventory guide, blockers, `HANDOFF.md`, review prompt
- [x] 226 tests passing at the end of the B-fixes, `ruff` clean, setup verified in a clean environment (264 after the extraction slice)

### Week 1 (Nov 2–6) — Discovery and economic scope

- [~] Inventory of October files and trades. *Done: template, scanner, zip intake and validator. Left: actually filling it in from the zip (A1–A10).*
- [ ] Every trader's sources enumerated (C4)
- [ ] Egistix columns and coverage documented (C3)
- [~] SQL environment and access. *Done: the IT checklist. Left: IT's answers (C1).*
- [ ] First evergreen clause analysis: exact quoted value, clause location, status left UNRESOLVED **(Risk/Finance)**
- [~] Golden-sample data dictionary. *Done: the schema dictionary. Left: golden October records (A9).*
- [ ] **Gate:** Risk approves the coverage inventory (A11)
- [ ] **Gate:** IT confirms the SQL path and permissions (C1, C2)
- [ ] **Gate:** unresolved evergreen meanings logged

**Week 1 actions (§14.2):**

- [~] 1. Confirm SQL engine, edition, server, owner, test/prod split, service identity and backups. *Checklist written; waiting on IT.*
- [~] 2. Inventory every October contract, including scans, emails, amendments and pre-October evergreens. *Zip received; `intake-zip` ready; A1–A8.*
- [ ] 3. Get dated Egistix exports and all 2–3 trader sheets; document what Egistix does not contain
- [ ] 4. Select the evergreen agreements and analyse their clauses (AI only in an approved environment; otherwise manual)
- [ ] 5. Hand-key the material fields of all ~35 October trades (A9)
- [ ] 6. Agree unique IDs, which source is authoritative for each field, status definitions and hard blockers (C6)
- [ ] 7. Sign off the demo scope; name the developer, the Risk reviewer, the Finance reviewer and the IT contact

### Week 2 (Nov 9–13) — L drive workflow and SQL base

- [ ] L drive folders and permissions created by IT (C2)
- [~] Evidence hash registry. *Built and tested on synthetic data, including ZIP intake with members linked to the ZIP; still needs a real UNC archive.*
- [~] Intake and approval events. *Done: audit events. Left: the accept/reject/approve workflow.*
- [~] Minimal SQL migrations. *Done: portable migration. Left: run it on the confirmed engine, then roles, DENY and locks.*
- [x] Audit and source-ID model
- [ ] Engine-specific tests (DATETIME2, exact decimals, CHECK rendering, collation)
- [ ] **Gate:** intake, SQL identity and source evidence demonstrated with real or redacted samples

### Week 3 (Nov 16–20) — Extract and normalize

- [ ] Choose which templates to build first, from the actual document counts and layout groups (A7). *Tooling ready; needs your profile summary.*
- [~] Priority Excel / text-PDF / email extractors, with field evidence locations. *Done: layout-neutral text extraction with exact locations for `.pdf`, `.xlsx` and `.msg` (attachments registered as child documents), stored in SQL (`0003`), verified against hashes, tested on synthetic files. Left: counterparty templates that turn located text into fields.*
- [ ] OCR route for scans, if any are present. *Scans are already detected (`NEEDS_OCR` / `PARTIAL_TEXT`); the OCR tool itself still needs approval.*
- [ ] Normalization reference maps (counterparty, hub, unit, calendar), versioned
- [ ] Real Egistix and trader-sheet import mappings
- [ ] Unit tests: negative and zero values, date formats, buy/sell ambiguity
- [ ] **Gate:** priority documents represented; omissions and ambiguities visible

### Week 4 (Nov 23–27) — Reconcile and verify

- [~] Matching. *Done: exact references; a trade can't be MATCHED without side, volume and price compared. Left: composite matching, amendment chains, duplicate detection, month scoping (S-2).*
- [ ] Imports of every trader sheet and of Egistix, with real data
- [ ] Break report and exception owners/aging
- [ ] Full manual verification of material terms for every October trade **(Risk)**
- [ ] **Gate:** every known October trade matched, classified or explicitly excepted

### Week 5 (Nov 30–Dec 4) — Versioning and economics sign-off

- [~] Full snapshots. *Done: tables and the candidate demo. Left: the real candidate build.*
- [ ] Transactional publisher (lock plus expected-current check), diff and rollback
- [ ] Evergreen approvals recorded **(Risk + Finance)**
- [ ] Excel outputs and publication manifest
- [ ] Negative tests
- [ ] **Gate:** recovery drill passes; no unsupported material economic values published

### Week 6 (Dec 7–11) — Historical demo and validation

- [ ] Reviewed October exports and exception pack
- [ ] Automated test run on the final code
- [ ] Demo to Risk, Finance and IT, with sign-off
- [ ] **Gate:** demo accepted; production pilot actions documented

### Weeks 7–10 (Dec–Jan) — Controlled production pilot

- [ ] Scheduled poller, heartbeat and alerts
- [ ] Access reviews; backup and restore drill
- [ ] Runbook
- [ ] Several parallel runs alongside the existing process
- [ ] **Gate:** formal operational acceptance

### Stage 1 sign-off gates (§17)

- [ ] Scope and trade-source coverage **(Risk manager)**
- [ ] Evergreen contract interpretation **(Risk + Finance)**
- [ ] SQL, security and backup readiness **(IT + security)**
- [ ] October economic validation **(Risk)**
- [ ] Snapshot and rollback drill **(Risk + IT)**
- [ ] Production auto-run acceptance **(Risk manager + IT)**

## Stage 2 — Reporting layer — NOT STARTED (after Stage 1 sign-off)

- [ ] Choose Power BI or an authenticated internal HTML report
- [ ] Approved SQL views: delivery-period exposure, concentration, exception aging, evergreen register
- [ ] Daily trading summary built from a specified approved publication

## Stage 3 — ETRM / MTM — NOT STARTED (separate build-vs-buy decision)

- [ ] Build-versus-buy assessment using the real trade population and Egistix integration needs
