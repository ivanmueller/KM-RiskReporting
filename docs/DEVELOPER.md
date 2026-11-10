# Developer guide (`riskparse`)

Controlled trade-confirmation intake, reconciliation and SQL master dataset for risk reporting.
Governed by *Risk Reporting Automation — Project Game Plan v2.0 (2026-10-31)*. Start with
[`HANDOFF.md`](HANDOFF.md) (state, invariants, code map) and [`ROADMAP.md`](ROADMAP.md) (task and
stage tracker); see also [`phase1_scope.md`](phase1_scope.md), [`blockers.md`](blockers.md) and the
review prompt [`review/codex_stress_test_prompt.md`](review/codex_stress_test_prompt.md).

## Ground rules

- **No confidential material in Git** — contracts, emails, Egistix exports, trader sheets,
  completed inventories, databases and credentials are git-ignored and checked by
  `tests/test_repo_hygiene.py`. Real evidence stays on the L drive / UNC share.
- **SQL engine not yet confirmed.** SQLite is used *only* as a local test adapter for synthetic
  data. Server connections are refused unless an IT-approved config sets `authorized = true`;
  `prod` is always refused in Phase 1. See [`docs/sql_platform_confirmation.md`](sql_platform_confirmation.md).
- **No passwords or connection strings in config files** (rejected at load time).
- Identity comes from file bytes (SHA-256), never from file names or folders.

## Layout

```text
SETUP.bat, RUN.bat          Windows: one-click install/update, and one-click step 1 run
pyproject.toml              package metadata, dependencies, pytest/ruff config
requirements/lock.txt       pinned versions used for Phase 1 tests
alembic.ini                 developer convenience (no DB URL inside)
config/
  raw-data-format.xlsx      the firm's raw-data columns (headers only) - step 1 output format
  counterparty_map.csv      company master names, maintained by Risk
  settings.example.toml     annotated example (copy to settings.local.toml - git-ignored)
  settings.test.toml        synthetic/test configuration
src/riskparse/
  cli.py, __main__.py       `riskparse` / `python -m riskparse`
  review.py                 step 1: `riskparse review` (confirmations -> raw-data.xlsx, review.xlsx)
  confirms.py               step 1: one parser per confirmation family, merge, amendments
  tradesheet.py             step 1: reads the master trade sheet (any tab, by header name)
  sheetcheck.py             step 1: confirmations vs trade sheet; bank swaps (roll / hedge)
  dashboard.py              step 1: dashboard.html and the local (127.0.0.1) server
  rawformat.py, names.py    step 1: output columns; counterparty master names
  project.py                finds config/ files from any working folder
  config.py                 TOML config loading + safety validation
  vocab.py                  controlled vocabularies (statuses, sides, categories)
  audit.py                  hash-chained append-only audit events
  imports.py                CSV source import, raw rows preserved
  db/connection.py          guarded engine creation (sqlite | mssql* | postgresql*)  *unverified
  db/schema.py              portable schema (16 tables)
  db/migrate.py             programmatic Alembic helpers
  db/migrations/            frozen Alembic migrations (0001_initial, 0002_sod_case_and_zip,
                            0003_document_extraction, 0004_review_fixes)
  identity.py               person-identity normalization for separation-of-duties checks
  docs_gen.py               generates docs/data_dictionary.md
  evidence/                 hashing, content-addressed archive, document registry, safe ZIP intake
  inventory/                inventory template definition, xlsx/csv writer, validator, scanner
  reconcile/exact.py        exact-alias reconciliation slice (synthetic demo)
  demo/smoke.py             synthetic end-to-end smoke demo
  extract/                  text extraction with evidence locations (.pdf, .xlsx, .msg),
                            folder profiler with layout fingerprints, SQL storage
  normalize/ publish/ reports/   placeholders - intentionally not implemented
inventory/templates/        blank October 2026 source-inventory template (xlsx + csv)
docs/                       HANDOFF, ROADMAP, step 1 guide, scope, data dictionary, SQL checklist,
                            inventory guide, blockers, review prompt
tests/                      unit, migration, constraint, inventory, hygiene and smoke tests
```

## Setup

Requires Python ≥ 3.11 (developed and tested on 3.13). On Windows, `SETUP.bat` in the project
folder does all of this (and installs Python and Git per user if missing); the manual steps are:

Windows (PowerShell, controlled machine):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements\lock.txt
pip install -e . --no-deps
```

Linux / macOS (bash):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements/lock.txt
pip install -e . --no-deps
```

## Run migrations (local SQLite test adapter)

```bash
riskparse --config config/settings.test.toml config-check
riskparse --config config/settings.test.toml db-upgrade
riskparse --config config/settings.test.toml db-current
```

This creates `var/riskparse_test.db` (git-ignored). Against a real server: only after IT
completes [`docs/sql_platform_confirmation.md`](sql_platform_confirmation.md).

## Run tests and lint

```bash
python -m pytest
ruff check src tests
```

## Verify the synthetic demo

```bash
riskparse demo-smoke --workdir var/demo
```

Expected: a JSON summary ending in `SMOKE DEMO: PASS`, with statuses
`SYN-T001 MATCHED`, `SYN-T002 MATCHED_BREAK`, `SYN-T003 UNRESOLVED`, `SYN-T004 NOT_BOOKED`,
`SYN-T005 UNRESOLVED`, one `UNCONFIRMED` unmatched trader row, `candidate_status: CANDIDATE`,
`active_publication: null`, `audit_chain_ok: true`, `archive_problems: []`.

## Source inventory (Risk, Week 1)

```bash
riskparse intake-zip "<received.zip>" --extract-to "<UNC folder>/Oct2026_extracted" --manifest "<UNC location>/intake_2026-10.csv"
riskparse inventory-template --out inventory/templates --csv
riskparse inventory-scan --root "<UNC folder>" --out "<UNC location>/scan.csv"
riskparse inventory-validate "<completed workbook>.xlsx" --strict
```

`intake-zip` hashes the ZIP as received, extracts it safely into a new empty folder (no path
tricks, no overwrites, no links or encrypted members, zip-bomb limits) and writes an
inventory manifest. Add `--register` to also record the ZIP and every member in the configured
database (only with an approved configuration; the local SQLite file is a provisional prototype).

See [`docs/source_inventory_guide.md`](source_inventory_guide.md).

## Contract intake, profiling and text extraction

See [`docs/contract_extraction.md`](contract_extraction.md) for the full Windows runbook.

```bash
riskparse profile-documents --root "<contracts folder>" --out "<outside the repo>/profile.csv"
riskparse extract --all
riskparse extract-preview "<one file>"
```

Extraction stores raw text with locations only. It does not turn any text into a price,
volume or date.

## Other commands

```bash
riskparse --config config/settings.test.toml register-document <file> --channel MANUAL_INTAKE
riskparse --config config/settings.test.toml audit-verify
riskparse gen-data-dictionary        # after any schema change
```

Publish, rollback, export and scheduling commands from game plan §9.6 do **not** exist yet.
