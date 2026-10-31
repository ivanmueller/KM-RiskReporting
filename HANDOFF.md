# Handoff — Risk Reporting Automation, Stage 1 / Phase 1

**Date:** 2026-10-31 (updated after the B-fixes) · **Branch:** `claude/risk-reporting-phase-1-x8u26q` · **Package:** `riskparse` 0.1.0
**Governing spec:** *Risk Reporting Automation — Project Game Plan v2.0 (2026-10-31)* (held outside the repo).

This document is for the next developer or reviewer picking up the code. Read it together with
`README.md` (setup/commands), `docs/phase1_scope.md` (what was built and why) and
`docs/blockers.md` (what is waiting on other people).

---

## 1. State in one paragraph

The Phase 1 foundation is in place, using **synthetic data only**. It includes a Python package with a
CLI, configuration that refuses unsafe setups, a connection layer, a 16-table schema that works on
any SQL engine with one frozen Alembic migration, SHA-256 document identity with a content-addressed
archive, a hash-chained audit trail, the October 2026 source-inventory workbook with a validator and a
read-only scanner, a safe intake command for a received ZIP, a CSV import that keeps raw rows
unchanged and re-checks their hash, a reconciliation that matches only on exact references, and an
end-to-end smoke demo. The migration head is `0002_sod_case_and_zip`. 226 tests pass and `ruff` is
clean. Defects D-1 to D-6 and suspicions S-1, S-4, S-5 and S-13 are fixed (sections 5–6). **No real data,
credentials, L drive or SQL server has been touched. Nothing publishes.** The SQL engine is
still unconfirmed, so SQLite is the only backend that has actually run.

## 2. Quick start (verified in a clean environment)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/lock.txt
pip install -e . --no-deps
python -m pytest
ruff check src tests
riskparse demo-smoke --workdir var/demo
```

Windows/PowerShell equivalents are in `README.md`. Expected result: `226 passed` (the
repository-hygiene tests skip outside a git checkout), and the demo ends with `SMOKE DEMO: PASS`.

## 3. Code map and responsibilities

| Module | Responsibility | Notes for maintainers |
| --- | --- | --- |
| `config.py` | Load TOML from `--config` → `RISKPARSE_CONFIG` → `config/settings.local.toml` → defaults; reject credential-like keys and unknown keys/sections; exact TOML type per field; no connection-attribute characters in host/database/driver; SQLite only for `local`/`test` | D-1 and D-5 fixed |
| `identity.py` | Normalize person identities (NFKC, whitespace, lower case; control characters rejected) for separation-of-duties checks | Typed names are a stopgap until directory logins are confirmed |
| `db/connection.py` | Build URLs from settings; refuse `prod`; refuse server backends unless `authorized`; turn on SQLite foreign keys | mssql/postgresql builders have **never** connected to anything |
| `db/schema.py` | SQLAlchemy Core metadata, naming convention, `UTCDateTime` type, CHECK rules | Changing it requires a **new** migration |
| `db/migrations/versions/0001_initial.py` | Frozen literal DDL; seeds the single `active_publication` row | Must not import `riskparse` (a test enforces this) |
| `db/migrations/versions/0002_sod_case_and_zip.py` | Case-insensitive approver checks (`LOWER()`); `ZIP` file type | Batch mode on SQLite; a test checks every other CHECK survives |
| `db/migrate.py` | Programmatic upgrade/downgrade/current/head; `run_guarded` handles SQLite foreign keys during table rebuilds | Used by the CLI, tests and `env.py` |
| `evidence/hashing.py` | Streaming SHA-256; check that the file did not change while hashing (size + mtime) | Never parses content |
| `evidence/archive.py` | Content-addressed copy (`sha256/<aa>/<hash><ext>`), verified after copy, never overwritten, made read-only | Read-only is best effort; the real control is IT's NTFS permissions |
| `evidence/registry.py` | NEW / DUPLICATE (same bytes) / REVISION (same filename, case-insensitive, new bytes); every sighting recorded; audit event on each | Economic duplicates are **not** detected here |
| `evidence/intake_zip.py` | Hash a received ZIP; extract safely (no zip-slip, no overwrite, no links or encrypted members, size and ratio limits); manifest in inventory format; optional registration with members linked to the ZIP | Nested archives and macro files flagged, not processed |
| `audit.py` | Append-only events, each holding the previous event's hash; `verify_chain`; normalized actor ≠ approver | Tamper-evident, not tamper-proof; writers must be serialized |
| `imports.py` | CSV → `source_imports` + `source_records`; bytes re-hashed against the registered SHA-256 first; strict ASCII decimals and ISO dates; clear errors for non-UTF-8, duplicate headers, extra fields and repeated record IDs | D-2, S-4 and S-5 fixed |
| `reconcile/exact.py` | Exact-reference reconciliation slice producing the §7.3 statuses; a booking row missing side/volume/price cannot make a trade `MATCHED` (rules `exact-alias-v0.2`) | Demo-grade; S-1 fixed; see S-2, S-3 |
| `inventory/template.py` | Sheet/column definitions (single source for the xlsx, CSV and validator) | Seed rows: 3 traders, Egistix, contracts, evergreen, 10 open questions |
| `inventory/workbook.py` | Writes the xlsx/CSV templates; validator with ERROR / MISSING / WARNING levels; understands real Excel date cells | D-3 fixed |
| `inventory/scan.py` | Read-only folder hashing → Contract_Documents rows + duplicate/revision findings | |
| `demo/smoke.py` | Synthetic end-to-end flow; creates a CANDIDATE publication only | Refuses to delete a folder unless it carries the demo marker file |
| `docs_gen.py` | Generates `docs/data_dictionary.md` | A test fails if the doc drifts from the schema |
| `cli.py` | `config-check`, `db-upgrade`, `db-current`, `register-document`, `audit-verify`, `inventory-*`, `intake-zip`, `gen-data-dictionary`, `demo-smoke` | Exit 3 = configuration refused, 4 = intake refused |
| `extract/`, `normalize/`, `publish/`, `reports/` | Placeholders only | Intentionally empty in Phase 1 |

## 4. Invariants that must survive future changes

1. No confidential files are committed (`.gitignore` + `tests/test_repo_hygiene.py`).
2. A document's identity comes from its **bytes**. The filename or folder is never a key, a classification or an approval.
3. Archived bytes are never overwritten. A new version means a new document row with `revision_of_document_id`.
4. A genuine `0` is `PRESENT` with value 0. `MISSING`, `PENDING`, `NOT_APPLICABLE` and `UNRESOLVED` must have a NULL value (enforced by a database CHECK).
5. A missing Egistix entry never makes a trade invalid. Only Risk may classify a trade as `EGISTIX_NOT_APPLICABLE`.
6. The person who creates a publication cannot approve it, and an audit actor cannot be the approver.
7. Exactly one `active_publication` row exists. Phase 1 code never changes it.
8. All timestamps are timezone-aware UTC; `UTCDateTime` rejects naive values.
9. Migrations are frozen literal DDL, and `compare_metadata` against `schema.py` must show no differences (tested).
10. No code connects to `prod`, and no config file holds a password.

## 5. Confirmed defects (found during handoff prep) — all FIXED

Each one was reproduced on 2026-10-31 against commit `694a11d` and is now fixed with regression tests.
Kept here as the record; the reviewer should try to break the fixes.

**How "the tests fail on the old code" was checked:** the new tests were run against `694a11d`'s
`src/`. Results:
- D-1/D-5 (`tests/test_config.py -k "d1 or d5"`): 17 of 17 fail.
- D-3 (`-k d3`): 1 of 1 fails.
- D-4 (`-k d4`): 3 of 4 fail. The one that passes is the exact-match control (`analyst` approving
  `analyst`), which the old database rule already blocked.
- D-2 (`tests/test_parse_strict.py`): 15 fail and 16 pass. The 16 are cases the old parser already
  handled correctly (for example `$2.50` rejected, `2.65` accepted). Of the 15, 14 are the real
  defects (12 ambiguous numbers converted, 2 non-ISO dates accepted). The thousands-separator test
  fails only because the old `_parse` had no such parameter.
- The other import tests (S-4, S-5) need names that did not exist before, so they cannot be
  collected against the old code at all.
- D-6 was shown by running the new migration with `run_guarded` bypassed.

| ID | Status | Severity | Defect | Reproduction | Fix applied |
| --- | --- | --- | --- | --- | --- |
| D-1 | **FIXED** (`tests/test_config.py`) | **High (safety guard bypass)** | Config values are not type-checked. `authorized = "false"` (a quoted string) is truthy, so the server-connection guard **lets it through**. `port = "1433"`, `encrypt = "no"` and similar strings are also accepted. | `settings_from_dict({"environment":{"name":"dev"},"database":{"backend":"postgresql","host":"h","database":"d","authorized":"false"}})` → `build_url` succeeds | Exact TOML type per field; port range; connection-attribute characters refused; `*_env` must be a variable name |
| D-2 | **FIXED** (`tests/test_imports.py`) | **High (silent wrong numbers)** | `imports._parse` strips commas, so a European-style `"2,65"` becomes `265`. `"NaN"`, `"Infinity"` and `"1e3"` are accepted as valid numbers. | `_parse("price_value", "2,65")` → `Decimal('265')`; `"NaN"` → `Decimal('NaN')` | ASCII-only plain decimals; thousands separator only when the mapping declares it; ISO `YYYY-MM-DD` dates only; everything else reported as unparsed |
| D-3 | **FIXED** (`tests/test_inventory.py`) | Medium (false errors for users) | The inventory validator rejects real Excel date cells: `_as_text(datetime)` gives `"2026-10-05 00:00:00"`, which fails the `YYYY-MM-DD` check. Anyone typing a date in a cell Excel reformats gets an ERROR. | `_as_text(datetime(2026,10,5))` → fails `_DATE_RE` | Column-aware rendering: midnight → date, first-of-month → month; timestamps still need a typed timezone |
| D-4 | **FIXED** (`tests/test_schema_constraints.py`, `tests/test_registry_audit.py`) | Medium (separation of duties) | The creator ≠ approver CHECK and the audit actor ≠ approver check are case-sensitive, so `Pat` can approve a publication created by `pat`. | Insert `publication_runs` with `created_by="Pat", approved_by="pat"` → accepted | `identity.normalize_actor` (NFKC, whitespace, lower case) in audit and the validator; migration 0002 makes both CHECKs use `LOWER()` |
| D-6 | **FIXED** (`tests/test_migrations.py`) | High (blocks upgrades) | Found while writing migration 0002: SQLite batch mode rebuilds a table with `DROP TABLE`, which fails with `FOREIGN KEY constraint failed` once linked rows exist (`create_db_engine` enforces foreign keys). Fresh databases were unaffected, so the existing tests missed it. | Upgrade a database at `0001` that holds a registered document to `0002` | `db/migrate.run_guarded`: foreign keys off for the migration, `PRAGMA foreign_key_check` must be empty before commit, then back on; used by the CLI and Alembic's `env.py` |
| D-5 | **FIXED** (`tests/test_config.py`) | Low | Unknown keys in `[paths]` or `[environment]` raise a raw `TypeError` instead of `ConfigError` (the CLI shows a traceback instead of exit 3). Top-level keys outside any section are silently ignored. | `settings_from_dict({"paths":{"bogus":1}})` → `TypeError` | Unknown keys rejected in every section and at top level, as `ConfigError` (exit 3) |

## 6. Suspected weaknesses (from reading the code; confirm before fixing)

| ID | Status | Area | Concern |
| --- | --- | --- | --- |
| S-1 | **FIXED** (`tests/test_reconcile.py`) | `reconcile/exact.py` `_compare` | A source field that is NULL is skipped. A source row that carries **no** comparable fields (only a reference) therefore yields `MATCHED` without any economic comparison. It should need a minimum set of compared fields, or report "not compared". |
| S-2 | Open | `reconcile/exact.py` | Every trade is reconciled no matter which month it belongs to; only the imports are filtered by `reporting_month`. Trades from other months would show up as false `NOT_BOOKED`/`UNCONFIRMED`. Evergreen trades from earlier years must still be included, so the scope rule needs care. |
| S-3 | Open | `reconcile/exact.py` | The run writes `linked_trade_uid` back into `source_records`. That staging data then depends on reconciliation, and a link stays even if the reference is later removed. |
| S-4 | **FIXED** (`tests/test_imports.py`) | `imports.py` | Reads `archive_uri` (or `original_path`) **without re-checking the SHA-256** before parsing, so an import may not match the registered evidence. |
| S-5 | **FIXED**, except that a failed import still cannot be retried with the same parser version (by design: bump the parser version) | `imports.py` | Accepts only UTF-8 (a cp1252 export raises `UnicodeDecodeError`); duplicate CSV headers are silently merged by `DictReader`; a repeated `source_record_id` raises `IntegrityError` and aborts the whole transaction; a failed import cannot be retried with the same parser version (unique constraint). |
| S-6 | Open | `evidence/registry.py` | On `DUPLICATE`, the `category`, `sender` and `archive_root` passed in are ignored. A document first registered without an archive is never archived later. |
| S-7 | Open | `evidence/registry.py` | The archive copy happens before the database insert. If the insert fails, an orphaned archive object is left behind (harmless because it is content-addressed, but untracked). |
| S-8 | Open | `audit.py` | Reading the previous hash and then inserting is not serialized. Two concurrent writers would fork the chain (it would be *detected*, but not prevented). Engine-specific locking is deferred. |
| S-9 | Open | `evidence/hashing.py` | The stability check relies on `st_mtime_ns`; SMB/UNC shares may report coarse timestamps. A single check cannot prove a copy has finished. |
| S-10 | Open | `inventory/scan.py` | A `PermissionError` raised by `rglob` while walking a directory (as opposed to reading a file) may crash the scan instead of becoming an `UNREADABLE` finding. |
| S-11 | Open | `db/connection.py` | The `schema_translate_map` path (`database.schema`) and where Alembic puts `alembic_version` under it have never been exercised. |
| S-12 | Open | `UTCDateTime` / Numeric | On SQL Server, `sa.DateTime` maps to legacy `DATETIME` (3.33 ms rounding), and SQLite stores NUMERIC as float. Exact decimals and timestamps must be re-tested on the confirmed engine. |
| S-13 | **FIXED**: `riskparse gen-data-dictionary` + `tests/test_docs.py` | `docs/data_dictionary.md` | Says it is generated from `schema.py`, but the generator script was a one-off and **is not in the repo**. Update it by hand or add a `riskparse` doc-generation command. |

## 7. Blockers (owners outside engineering)

Summary only; the detail is in `docs/blockers.md`:
- SQL product, instance, sign-in, roles and backups (IT/DBA).
- L drive UNC path and permissions (IT).
- Actual October contracts, emails, scans and evergreen agreements (Risk).
- Egistix exports, field dictionary and the meaning of `$0` (Egistix owner).
- Identity of the in-scope traders and their sheets (Risk/traders).
- Review of the Python environment and packages (IT security).
- Reconciliation policy (Risk manager).
- AI approval (Legal).
- The company Git host and branch protection (IT).

## 8. How to make common changes

- **Schema change:** edit `db/schema.py`, write a new frozen migration (see `0002` for a hand-written
  example; autogenerate does **not** detect CHECK changes), run `riskparse gen-data-dictionary`, then
  `RISKPARSE_CONFIG=config/settings.test.toml alembic upgrade head` followed by
  `RISKPARSE_CONFIG=config/settings.test.toml alembic revision --autogenerate -m "<what>"`.
  Then **hand-review** the generated file: replace any `riskparse` imports with literal types (for
  example `UTCDateTime` → `sa.DateTime()`), keep it portable, add a downgrade, and run `pytest`
  (`test_migrated_schema_matches_metadata` must pass). Never edit `0001_initial` after it has been
  applied anywhere.
- **New controlled value:** update `vocab.py`, then add a migration that drops and recreates the
  affected CHECK constraint, then regenerate the inventory template
  (`riskparse inventory-template --out inventory/templates --csv`) and commit it.
- **Inventory column change:** edit `inventory/template.py`, regenerate the templates, and update
  `docs/source_inventory_guide.md`.
- **Real source mapping (Egistix / a trader sheet):** add a `SourceMapping` only after the column
  layout is confirmed from the signed-off inventory. Never commit the sample export used to
  build it.

## 9. Recommended next steps (in order)

The live task and stage tracker is [`ROADMAP.md`](ROADMAP.md); keep it updated as items close.

1. ~~Fix D-1 to D-6~~ — done, with regression tests.
2. Run the independent stress-test review in `docs/review/codex_stress_test_prompt.md` and
   triage its findings into this file.
3. When IT answers `docs/sql_platform_confirmation.md`: run `db-upgrade` on DEV/TEST, add tests
   specific to that engine (`DATETIME2`, exact decimals, CHECK rendering, collation), then write a reviewed
   engine-specific migration for roles/DENY/locks.
4. When the signed inventory and real exports arrive: write the real import mappings and the
   golden October records. Then move to Week 3 extraction, prioritised by actual document
   counts.
