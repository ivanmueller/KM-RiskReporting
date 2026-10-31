# Handoff — Risk Reporting Automation, Stage 1 / Phase 1

**Date:** 2026-10-31 · **Branch:** `claude/risk-reporting-phase-1-x8u26q` · **Package:** `riskparse` 0.1.0
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
read-only scanner, a CSV import that keeps raw rows unchanged, a reconciliation that matches only on
exact references, and an end-to-end smoke demo. 97 tests pass and `ruff` is clean. **No real data,
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

Windows/PowerShell equivalents are in `README.md`. Expected result: `97 passed` (3 repository-hygiene
tests skip outside a git checkout), and the demo ends with `SMOKE DEMO: PASS`.

## 3. Code map and responsibilities

| Module | Responsibility | Notes for maintainers |
| --- | --- | --- |
| `config.py` | Load TOML from `--config` → `RISKPARSE_CONFIG` → `config/settings.local.toml` → defaults; reject credential keys; SQLite only for `local`/`test` | See defect D-1 (no type validation) |
| `db/connection.py` | Build URLs from settings; refuse `prod`; refuse server backends unless `authorized`; turn on SQLite foreign keys | mssql/postgresql builders have **never** connected to anything |
| `db/schema.py` | SQLAlchemy Core metadata, naming convention, `UTCDateTime` type, CHECK rules | Changing it requires a **new** migration |
| `db/migrations/versions/0001_initial.py` | Frozen literal DDL; seeds the single `active_publication` row | Must not import `riskparse` (a test enforces this) |
| `db/migrate.py` | Programmatic upgrade/downgrade/current/head | Used by the CLI and tests |
| `evidence/hashing.py` | Streaming SHA-256; check that the file did not change while hashing (size + mtime) | Never parses content |
| `evidence/archive.py` | Content-addressed copy (`sha256/<aa>/<hash><ext>`), verified after copy, never overwritten, made read-only | Read-only is best effort; the real control is IT's NTFS permissions |
| `evidence/registry.py` | NEW / DUPLICATE (same bytes) / REVISION (same filename, case-insensitive, new bytes); every sighting recorded; audit event on each | Economic duplicates are **not** detected here |
| `audit.py` | Append-only events, each holding the previous event's hash; `verify_chain`; actor ≠ approver | Tamper-evident, not tamper-proof; writers must be serialized |
| `imports.py` | CSV → `source_imports` + `source_records`, raw row JSON + hash, mapped normalized fields, idempotent per (doc, system, parser) | See D-2, S-4, S-5 |
| `reconcile/exact.py` | Exact-reference reconciliation slice producing the §7.3 statuses | Demo-grade; see S-1, S-2, S-3 |
| `inventory/template.py` | Sheet/column definitions (single source for the xlsx, CSV and validator) | Seed rows: 3 traders, Egistix, contracts, evergreen, 10 open questions |
| `inventory/workbook.py` | Writes the xlsx/CSV templates; validator with ERROR / MISSING / WARNING levels | See D-3 |
| `inventory/scan.py` | Read-only folder hashing → Contract_Documents rows + duplicate/revision findings | |
| `demo/smoke.py` | Synthetic end-to-end flow; creates a CANDIDATE publication only | Refuses to delete a folder unless it carries the demo marker file |
| `cli.py` | `config-check`, `db-upgrade`, `db-current`, `register-document`, `audit-verify`, `inventory-*`, `demo-smoke` | Exit 3 = configuration refused |
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

## 5. Confirmed defects (found during handoff prep, **not yet fixed**)

Each one was reproduced on 2026-10-31 with a short script against the committed code.

| ID | Severity | Defect | Reproduction | Suggested fix |
| --- | --- | --- | --- | --- |
| D-1 | **High (safety guard bypass)** | Config values are not type-checked. `authorized = "false"` (a quoted string) is truthy, so the server-connection guard **lets it through**. `port = "1433"`, `encrypt = "no"` and similar strings are also accepted. | `settings_from_dict({"environment":{"name":"dev"},"database":{"backend":"postgresql","host":"h","database":"d","authorized":"false"}})` → `build_url` succeeds | Validate the types of every `DatabaseConfig` field (bool must be `bool`, port must be `int`) and raise `ConfigError` otherwise |
| D-2 | **High (silent wrong numbers)** | `imports._parse` strips commas, so a European-style `"2,65"` becomes `265`. `"NaN"`, `"Infinity"` and `"1e3"` are accepted as valid numbers. | `_parse("price_value", "2,65")` → `Decimal('265')`; `"NaN"` → `Decimal('NaN')` | Only accept plain decimals (`^-?\d+(\.\d+)?$`, plus thousands separators only when the mapping declares them); reject non-finite values; report anything else as unparsed |
| D-3 | Medium (false errors for users) | The inventory validator rejects real Excel date cells: `_as_text(datetime)` gives `"2026-10-05 00:00:00"`, which fails the `YYYY-MM-DD` check. Anyone typing a date in a cell Excel reformats gets an ERROR. | `_as_text(datetime(2026,10,5))` → fails `_DATE_RE` | For `KIND_DATE` columns, turn a midnight `datetime` into an ISO date |
| D-4 | Medium (separation of duties) | The creator ≠ approver CHECK and the audit actor ≠ approver check are case-sensitive, so `Pat` can approve a publication created by `pat`. | Insert `publication_runs` with `created_by="Pat", approved_by="pat"` → accepted | Normalize identities (lower-cased login / directory ID) before storing them; compare with `LOWER()` in the CHECK |
| D-5 | Low | Unknown keys in `[paths]` or `[environment]` raise a raw `TypeError` instead of `ConfigError` (the CLI shows a traceback instead of exit 3). Top-level keys outside any section are silently ignored. | `settings_from_dict({"paths":{"bogus":1}})` → `TypeError` | Apply the same unknown-key check used for `[database]` to every section, and reject unknown top-level sections |

## 6. Suspected weaknesses (from reading the code; confirm before fixing)

| ID | Area | Concern |
| --- | --- | --- |
| S-1 | `reconcile/exact.py` `_compare` | A source field that is NULL is skipped. A source row that carries **no** comparable fields (only a reference) therefore yields `MATCHED` without any economic comparison. It should need a minimum set of compared fields, or report "not compared". |
| S-2 | `reconcile/exact.py` | Every trade is reconciled no matter which month it belongs to; only the imports are filtered by `reporting_month`. Trades from other months would show up as false `NOT_BOOKED`/`UNCONFIRMED`. Evergreen trades from earlier years must still be included, so the scope rule needs care. |
| S-3 | `reconcile/exact.py` | The run writes `linked_trade_uid` back into `source_records`. That staging data then depends on reconciliation, and a link stays even if the reference is later removed. |
| S-4 | `imports.py` | Reads `archive_uri` (or `original_path`) **without re-checking the SHA-256** before parsing, so an import may not match the registered evidence. |
| S-5 | `imports.py` | Accepts only UTF-8 (a cp1252 export raises `UnicodeDecodeError`); duplicate CSV headers are silently merged by `DictReader`; a repeated `source_record_id` raises `IntegrityError` and aborts the whole transaction; a failed import cannot be retried with the same parser version (unique constraint). |
| S-6 | `evidence/registry.py` | On `DUPLICATE`, the `category`, `sender` and `archive_root` passed in are ignored. A document first registered without an archive is never archived later. |
| S-7 | `evidence/registry.py` | The archive copy happens before the database insert. If the insert fails, an orphaned archive object is left behind (harmless because it is content-addressed, but untracked). |
| S-8 | `audit.py` | Reading the previous hash and then inserting is not serialized. Two concurrent writers would fork the chain (it would be *detected*, but not prevented). Engine-specific locking is deferred. |
| S-9 | `evidence/hashing.py` | The stability check relies on `st_mtime_ns`; SMB/UNC shares may report coarse timestamps. A single check cannot prove a copy has finished. |
| S-10 | `inventory/scan.py` | A `PermissionError` raised by `rglob` while walking a directory (as opposed to reading a file) may crash the scan instead of becoming an `UNREADABLE` finding. |
| S-11 | `db/connection.py` | The `schema_translate_map` path (`database.schema`) and where Alembic puts `alembic_version` under it have never been exercised. |
| S-12 | `UTCDateTime` / Numeric | On SQL Server, `sa.DateTime` maps to legacy `DATETIME` (3.33 ms rounding), and SQLite stores NUMERIC as float. Exact decimals and timestamps must be re-tested on the confirmed engine. |
| S-13 | `docs/data_dictionary.md` | Says it is generated from `schema.py`, but the generator script was a one-off and **is not in the repo**. Update it by hand or add a `riskparse` doc-generation command. |

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

- **Schema change:** edit `db/schema.py`, then
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

1. Fix D-1 and D-2 (a safety-guard bypass and silent number corruption), then D-3 and D-4, adding a
   regression test for each.
2. Run the independent stress-test review in `docs/review/codex_stress_test_prompt.md` and
   triage its findings into this file.
3. When IT answers `docs/sql_platform_confirmation.md`: run `db-upgrade` on DEV/TEST, add tests
   specific to that engine (`DATETIME2`, exact decimals, CHECK rendering, collation), then write a reviewed
   engine-specific migration for roles/DENY/locks.
4. When the signed inventory and real exports arrive: write the real import mappings and the
   golden October records. Then move to Week 3 extraction, prioritised by actual document
   counts.
