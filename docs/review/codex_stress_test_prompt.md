# Codex review prompt — adversarial stress test of `riskparse` Phase 1

Copy everything below the line into Codex (or any code-review agent) with the repository checked
out at branch `claude/risk-reporting-phase-1-x8u26q`. The prompt is self-contained: the reviewer
does not need the game-plan document.

---

## Role and goal

You are an adversarial senior reviewer for a risk-reporting system at an energy-trading firm.
Your job is to **find defects** in the Python package `riskparse` (Stage 1, Phase 1): bugs, wrong
numbers, silent data loss, bypassable controls, crashes on realistic input, portability
traps and tests that pass for the wrong reason. Do not praise the code or summarise what it does.
Every finding must be backed by a reproduction you actually ran.

In this domain, a wrong number that looks plausible is worse than a crash. Prioritise accordingly.

## Ground rules

- Work only inside the repository and a scratch directory. **No network calls**, no real
  company data, no credentials. Use synthetic inputs only.
- Do **not** change files under `src/`. Put your reproductions under `review_scratch/`
  (git-ignored is fine) or as new test files named `tests/review/test_review_<topic>.py`. Each
  reproduction must **fail on the current code** (or print the wrong value) and say what the correct
  behaviour would be.
- Setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  pip install -r requirements/lock.txt
  pip install -e . --no-deps
  python -m pytest -q
  ruff check src tests
  riskparse demo-smoke --workdir var/demo
  ```
  The baseline should be 97 passed and lint clean. Report it if it isn't.
- If something is only a suspicion you could not reproduce, list it separately as
  *unconfirmed*, with the reason you could not reproduce it. Never present a guess as confirmed.

## What the system must guarantee (the spec, condensed)

Treat each of these as a requirement. A violation is a finding.

1. **No confidential material in Git.** Contracts (`.pdf/.msg/.eml/...`), exports, trader sheets,
   databases and credentials must never be tracked. Only blank templates live in
   `inventory/templates/`.
2. **SQL engine is unconfirmed.** SQLite is a *local test adapter only* (`local`/`test`
   environments). Server backends (`mssql`, `postgresql`) must refuse to connect unless
   `database.authorized` is true, and **`prod` must always be refused**. Config files must never
   contain passwords or connection strings.
3. **Document identity comes from bytes (SHA-256)**, never from the filename or folder. Identical bytes
   mean the same document (record the new sighting). The same filename with different bytes is a new
   *revision* document. Archived bytes are never overwritten or edited, and tampering must be
   detectable.
4. **Raw values are preserved.** Imports keep every source row unchanged, and a value that cannot be
   parsed must be left NULL and *reported*, never coerced or guessed. No unit, currency or
   locale conversion without an explicit approved basis.
5. **A genuine `0` is not "missing".** `PRESENT` + 0 is legal, while `MISSING`, `PENDING`,
   `NOT_APPLICABLE` and `UNRESOLVED` must carry no number. An evergreen deal shown as `$0` in
   Egistix must never be treated as a confirmed price of zero.
6. **Reconciliation never assumes.** Absence from Egistix does not make a trade invalid. A
   confirmation with no booking is `NOT_BOOKED`. A booking with no confirmation is `UNCONFIRMED`.
   Disagreeing material fields (side, volume, price, dates) are `MATCHED_BREAK`. Nothing is silently
   skipped. `MATCHED` must mean the material terms really were compared and agree.
7. **Separation of duties.** The creator of a publication cannot approve it, and an audit actor cannot
   be the approver. Evergreen interpretation needs two *different* approvers (Risk and Finance).
   Whoever hand-keys a crosswalk row cannot also verify it.
8. **Publication.** Exactly one active-publication pointer row exists and Phase 1 code never
   changes it. A demo candidate stays `CANDIDATE`.
9. **Audit trail** is append-only and hash-chained, so edits, deletions and reordering must be
   detected by `audit.verify_chain`.
10. **Timestamps** are timezone-aware UTC, and naive datetimes are rejected.
11. **Migrations** are frozen literal DDL, portable across engines, and must match
    `src/riskparse/db/schema.py` exactly.

## Attack plan — work through every item

### A. Configuration and connection (`config.py`, `db/connection.py`, `cli.py`)
- Try wrong TOML **types** for every field: strings for booleans (`authorized = "false"`,
  `"0"`, `"no"`), strings or negatives for `port`, lists or tables where scalars are expected. Does any
  value that *looks* false end up enabling a connection?
- Change the case or spacing of the environment name (`"Prod"`, `"PROD"`, `" prod"`), and use unknown
  environment names. Can a production-like environment slip through?
- Hide credentials: `Password`, `PASSWORD`, `db_password`, `pass`, credentials in
  `host` (`user:pw@host`), in `odbc_driver`, in `database`, or in a top-level key outside any section.
  Which ones get through?
- Unknown keys or sections in `[paths]`, `[environment]` and the top level: are they rejected
  cleanly as `ConfigError` (CLI exit 3) or do they cause a traceback?
- `RISKPARSE_CONFIG` pointing at a directory, an empty file, or invalid TOML.
- `describe_url` / `config-check`: can any secret value from the environment appear in printed
  output or in exception messages?
- Is `schema_translate_map` (the `database.schema` setting) applied consistently, including where
  Alembic puts its `alembic_version` table?

### B. Schema and migrations (`db/schema.py`, `db/migrations/`)
- Run `compare_metadata` yourself, and also compare CHECK constraint text between the migration and
  the metadata. Alembic does not compare CHECKs on SQLite, so a drift there would be invisible to
  the existing test.
- For each CHECK constraint, try to insert a row that breaks the *intent* but satisfies the
  *expression*. Examples: SHA-256 `LIKE '____...'` with non-hex or uppercase characters,
  `reporting_month = '2026-13'` or `'abcd-ef'`, case variants of approver identities
  (`Pat` vs `pat`), whitespace variants (`'pat '`), `side = 'buy'`.
- Value/status rule: try a negative price, `NaN` / `Infinity` reaching a NUMERIC column, and
  `price_status='PRESENT'` with an implausible value.
- Portability: flag any SQL expression, default or type that would behave differently or fail on
  SQL Server or PostgreSQL. Examples: `LIKE` collation, Boolean `server_default '0'`, `DateTime`
  becoming SQL Server `DATETIME` (precision), constraint-name lengths, `Text` used in a unique or
  indexed column, self-referencing FK cascade rules. Explain the concrete failure each would cause.
- Downgrade then upgrade with data present: what happens?
- Can a second `active_publication` row exist, or can the pointer be set to a publication that is
  not `APPROVED`/`PUBLISHED`? Is that enforced anywhere?

### C. Evidence identity (`evidence/hashing.py`, `evidence/archive.py`, `evidence/registry.py`)
- Edge-case files: empty files (0 bytes), very large files (simulate with a sparse file),
  filenames containing Unicode, very long paths, names differing only by case or Unicode
  normalisation (NFC vs NFD), files without an extension, and two different extensions with
  identical bytes.
- A file modified *during* hashing: does `UnstableFileError` fire reliably? Are there cases with
  coarse mtime granularity where it does not?
- Duplicate path: register file X without an archive, then register the same bytes with an archive.
  Is the evidence ever archived? Are `category`/`sender` from the second call lost?
- Revision path: register A, then A′ (same name, new bytes), then A again (original bytes). Is the
  revision chain correct and unambiguous? What does `revision_of_document_id` point to after several
  revisions registered in the same second?
- Archive: pre-create a *wrong* file at the content address and check that it is detected rather
  than trusted. Try a symlink at the target path and a read-only archive directory. On failure, are
  temporary files left behind?
- Transaction failure: force the database insert to fail after the archive copy. What state is left?
- Does `verify_archive` catch a missing file, a truncated file, and a file replaced with identical
  size but different bytes?

### D. Audit trail (`audit.py`)
- Tamper tests beyond the existing ones: swap two events, change `occurred_at_utc` by under a
  second, change `actor` letter case, delete the *last* event (is truncation detectable at all?),
  and insert a forged event with a correctly recomputed hash (is there anything an attacker with
  write access cannot recompute?).
- Two connections writing concurrently (threads against a file-backed SQLite database): does the
  chain fork, and is it reported?
- JSON canonicalisation: do `before`/`after` payloads containing `Decimal`, `datetime`, sets or
  non-ASCII text produce stable hashes, or can the same logical event hash differently?

### E. Source import (`imports.py`)
This is the highest-risk area for wrong numbers. Probe `_parse` and `import_csv_source` with:
- Numbers: `"2,65"` (European decimal), `"1,234.56"`, `"1.234,56"`, `"$2.50"`, `"(100)"`
  (accounting negative), `"-0"`, `" 5 "`, `"1e3"`, `"NaN"`, `"Infinity"`, `"٣"` (non-ASCII
  digits), and empty versus whitespace-only. Which ones become a *wrong number* instead of NULL plus a report?
- Dates: `"2026-10-05T00:00:00"`, `"10/05/2026"`, `"2026-02-30"`, and Excel serial numbers.
- Side: `"B"`, `"S"`, `"Purchase"`, `"Sale"`, `" buy "`, plus our-perspective ambiguity.
- CSV shape: duplicate headers, a header row with a BOM, cp1252 encoding, embedded newlines,
  rows longer or shorter than the header, a completely empty file, and a header-only file.
- A repeated `source_record_id` within one file: is the error clear, and is partial data left behind?
- Integrity: modify the archived bytes after registration, then import. Does the import notice
  that it is parsing bytes that no longer match the registered SHA-256?
- Idempotency: is a re-import after a *failed* import possible? Does the same file registered
  under two different `source_system` values double count?

### F. Reconciliation (`reconcile/exact.py`)
Build small synthetic scenarios (copy the pattern in `src/riskparse/demo/smoke.py`) and check
the status each one gets:
- A source row that carries only a reference (no side/volume/price/dates). Is it reported as
  `MATCHED` even though nothing economic was compared?
- Trades from another month (for example a September trade) when reconciling `2026-10`. Are they
  falsely `NOT_BOOKED`/`UNCONFIRMED`? Older evergreen trades must still be included, so check both.
- A trade whose only link is an `AMENDMENT` or `CANCELLATION` document. A `CANCELLED` trade still
  present in Egistix.
- Two source rows from *different* imports (Egistix and a trader sheet) that disagree with each other
  but where each matches the terms on some fields.
- The same trader reference used by two different trader sheets.
- Multiple term versions and legs: is the latest version per leg used, and are legs other than 1
  ignored silently?
- A volume of `0` versus NULL versus `UNRESOLVED`, and a price of `0` with `PRESENT` status versus Egistix `0`.
- Precedence: a trade that is both a duplicate *and* unconfirmed. Is the more severe or more informative
  status lost?
- Side effects: `linked_trade_uid` is written back into `source_records`. Re-run after deleting an
  alias. Is the stale link reused?
- Determinism: run twice on the same data. Are the results identical (apart from IDs and timestamps)?

### G. Inventory workbook, validator and scanner (`inventory/`)
- Open the generated template, then enter values the way a real user would in Excel: real date
  cells, numbers typed into ID columns, a SHA-256 pasted with trailing spaces or in uppercase,
  formulas, merged cells, a hidden extra column, renamed sheets, reordered columns, and 1,001 or more rows
  (data validation stops at row 1000). Does the validator give wrong ERRORs or wrong passes?
- Validation rules: is there any way to get an evergreen row `APPROVED` with the same person
  as both approvers (case, whitespace or Unicode look-alikes)? Or `keyed_by == verified_by`? Or
  a crosswalk trader missing from `Source_Coverage` that is not flagged?
- Does `--strict` give the documented exit codes (0 valid and complete, 2 missing values, 1 errors)?
- Scanner: unreadable subdirectories, symlink loops, files locked or deleted during the scan, Office
  lock files (`~$`), `Thumbs.db`, and a root that is a file. Confirm the scan **never writes** inside
  the scanned tree.
- Is the committed `inventory/templates/*.xlsx` identical in structure to what
  `riskparse inventory-template` produces now?

### H. Demo, CLI and tests themselves
- `run_smoke`: can it delete anything other than its own work directory (marker spoofing,
  symlinked workdir, a relative path such as `.`)?
- Find tests that **pass for the wrong reason**: assertions that are always true, `pytest.raises`
  catching a broader error than the rule under test, fixtures that hide failures (for example the
  `autouse` `chdir`), and checks that are skipped on SQLite (CHECK comparison). For each one, show the
  mutation of `src/` that should fail the test but does not (mutation-test by hand on a scratch
  copy, never on `src/`).
- Repository hygiene: try committing (in a scratch clone) `contract.PDF` (uppercase), `x.pdf.txt`,
  `notes.xlsx` in `docs/`, or a `.env.prod`. Which ones get past `.gitignore` and the hygiene tests?

## Severity rubric

| Severity | Meaning in this system |
| --- | --- |
| **Critical** | A wrong economic value, sign, status or identity can reach SQL looking valid; or a safety guard (prod refusal, authorization, no-credentials, separation of duties) can be bypassed |
| **High** | Silent data loss or silent skip; tampering not detected; a control that exists only on paper; something that breaks on the likely target engine |
| **Medium** | A crash or misleading error on realistic input; a non-deterministic result; a test that passes for the wrong reason |
| **Low** | Hardening, clarity, portability nits, documentation that disagrees with the code |

## Required output

1. **Baseline**: the test and lint results you observed.
2. **Findings table**, sorted with the most severe first:

   | # | Severity | Category | `file:line` | Defect (one sentence) | Reproduction (command or test path) | Expected vs actual | Suggested fix | Confidence |
   | --- | --- | --- | --- | --- | --- | --- | --- | --- |

   Category is one of: correctness, data-integrity, security-control, portability, robustness,
   test-quality, docs.
3. **Reproduction code**: every new `tests/review/test_review_*.py` or `review_scratch/*` file,
   listed with the exact command to run it and its failing output.
4. **Unconfirmed suspicions**: listed separately, with why each could not be reproduced.
5. **Spec-violation map**: for each of guarantees 1–11 above, mark *held*, *violated (finding #)*
   or *untested*.
6. **Top 5 fixes to make first**, ordered by risk reduction per hour of work.

Do the independent pass above **before** reading `HANDOFF.md`. Then open `HANDOFF.md` sections 5–6
and, for each listed defect D-1…D-5 and suspicion S-1…S-13, mark it *confirmed*, *refuted (with
evidence)* or *not checked*. Findings that match a D-item count only if you found a new variant
or a wider impact.
