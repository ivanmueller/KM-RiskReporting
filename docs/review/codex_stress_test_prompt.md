# Codex review prompt — adversarial review of Claude's work on `riskparse`

Copy everything below the line into Codex (or any code-review agent) with the repository checked
out at branch `claude/risk-reporting-phase-1-x8u26q`. The prompt is self-contained: the reviewer
does not need the game-plan document.

*Revision 4 (2026-10-31): scoped strictly to the code and results Claude produced; adds the contract text-extraction slice (claim C15, guarantee 13, section I).*

---

## Role and goal

You are an adversarial senior reviewer. An AI assistant (Claude) wrote the Python package
`riskparse` for a risk-reporting system at an energy-trading firm. It also wrote the documents
claiming what that code does, what was tested and what was fixed. Your job is to check
**Claude's work and Claude's claims, and nothing else**:

1. **Claims**: is every result Claude reported actually true when you reproduce it?
2. **Code**: does Claude's code contain bugs, wrong numbers, silent data loss, bypassable
   controls, crashes on realistic input, portability traps, or tests that pass for the wrong reason?

Do not praise the work or summarise it. Every finding must be backed by a reproduction you
actually ran. In this domain, a wrong number that looks plausible is worse than a crash.

## Scope — what you review and what you don't

**In scope (Claude's output):**
- Every tracked file on this branch. Every commit on the branch, from `2948ee2` to `HEAD`, is
  authored by `Claude <noreply@anthropic.com>`. Confirm with `git log --format='%h %an <%ae> %s'`
  (report any commit with a different author, and leave its changes out of scope), and list the
  files with `git ls-files`. This covers `src/`, `tests/`, both migrations (including the Alembic-rendered `0001_initial.py`, which Claude
  committed), `config/`, `inventory/templates/`, `requirements/lock.txt`, `.gitignore`,
  `pyproject.toml`, `alembic.ini` and every Markdown document.
- Every claim in those documents and in every commit message: test counts, "fixed",
  "verified", "done", ticked roadmap boxes, documented commands and their expected output.
- How Claude's code *uses* third-party libraries (SQLAlchemy, Alembic, openpyxl, `zipfile`,
  `csv`, pytest). A wrong assumption by Claude about library behaviour is in scope.

**Out of scope. Do not report these as findings:**
- Bugs inside third-party libraries, Python itself or the operating system.
- The project's business decisions, timelines, estimates and policies (the governing game plan),
  and anything owned by IT, Risk, Finance or Legal (the blockers B-01…B-11 in `docs/blockers.md`).
  The exception is where Claude's documents *misstate* them.
- Features Claude deliberately did not build, listed under "Deliberately NOT built" in
  `docs/phase1_scope.md` (parsers, publishing, rollback, reports, scheduling, dashboards). A
  missing feature counts only if Claude's code or documents claim it exists, or its absence
  breaks a guarantee Claude claims below.
- Style, naming or structure preferences that cause no defect.
- The behaviour of a real SQL Server/PostgreSQL instance, which you cannot run here. Portability
  findings are in scope only as analysis of specific Claude DDL or code, with the concrete
  failure explained and confidence stated.

If you notice something out of scope, put it in a short "Noted, out of scope" appendix (one
line each). Do not put it in the findings table.

## Ground rules

- Work only inside the repository and a scratch directory. **No network calls** (apart from the
  `pip install` below), no real company data, no credentials. Use synthetic inputs only.
- Do **not** change Claude's files. Put reproductions under `review_scratch/` (git-ignored) or in
  new files `tests/review/test_review_<topic>.py`. Each reproduction must **fail on the current
  code** (or print the wrong value) and state the correct behaviour.
- Every finding must cite a **Claude artifact**: a `file:line` in Claude's code, or the exact
  location of a Claude claim (document and line, or commit hash and message line).
- If something is only a suspicion you could not reproduce, list it as *unconfirmed*, with the
  reason. Never present a guess as confirmed.
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

## Part 1 — Verify Claude's claimed results

Reproduce each claim. Report each as **TRUE**, **FALSE (finding #)** or **NOT VERIFIABLE (why)**.

| # | Claim | Where Claude made it | How to check |
| --- | --- | --- | --- |
| C1 | A clean install from `requirements/lock.txt` works; `python -m pytest` gives **264 passed** at `HEAD`; `ruff check src tests` is clean | `README.md` Setup; `HANDOFF.md` §1–2; `ROADMAP.md` | Fresh venv, exact README commands. Note any skips, warnings or flaky tests |
| C2 | `riskparse demo-smoke` prints `SMOKE DEMO: PASS` with SYN-T001 `MATCHED`, SYN-T002 `MATCHED_BREAK`, SYN-T003 `UNRESOLVED`, SYN-T004 `NOT_BOOKED`, SYN-T005 `UNRESOLVED`, one `UNCONFIRMED` unmatched row, `CANDIDATE` status, `active_publication: null`, an intact audit chain and no archive problems | `README.md` "Verify the synthetic demo"; `docs/phase1_scope.md` walkthrough | Run it and compare field by field. Is the PASS check itself strict enough to fail when a status is wrong? |
| C3 | D-1…D-5 are fixed, and their regression tests fail on the code before the fix (commit `694a11d`), with the per-defect counts and exceptions Claude lists | `HANDOFF.md` §5 ("How the tests fail on the old code was checked"); commit `193868a` message | Run the new tests (D-2: `tests/test_parse_strict.py`) against `694a11d`'s `src/` in a scratch worktree. Confirm the counts, that each failure is for the stated reason rather than an import or signature error, and that every test that passes on old code really is a control case |
| C4 | D-6 is fixed: upgrading a database that holds linked rows from `0001` to `0002` works under enforced foreign keys, and FKs are re-enabled afterwards | `HANDOFF.md` §5 D-6; `ROADMAP.md` B4 | Reproduce the original failure by bypassing `run_guarded`, then confirm the fix and its tests |
| C5 | S-1, S-4, S-5 (with the stated exception) and S-13 are fixed; S-2, S-3 and S-6…S-12 are open | `HANDOFF.md` §6 | For each "fixed" item, try to break the fix. For each "open" item, confirm or refute that it really is a defect |
| C6 | The ten invariants hold | `HANDOFF.md` §4 | Try to violate each one through Claude's public functions and CLI |
| C7 | Every `[x]` in `ROADMAP.md` exists in the repo and is tested; every `[~]` note accurately states what is left; no item needing real data or sign-off is ticked | `ROADMAP.md` | Map each ticked line to code plus a test |
| C8 | Each "control demonstrated by tests" has a test that would **fail if the control were removed** | `docs/phase1_scope.md` "Controls demonstrated by tests" | Hand-mutate a scratch copy (remove the control) and run the cited test |
| C9 | `docs/data_dictionary.md` matches the schema; the committed inventory templates match what the generator produces | `docs/data_dictionary.md` header; `tests/test_docs.py`; `tests/test_repo_hygiene.py` | Regenerate both and diff. For the templates, compare all cells and data validations, not only headers |
| C10 | Only portable DDL; all constraint names ≤ 58 characters; Phase 1 never connects to `prod`; no passwords in config | `docs/sql_platform_confirmation.md`; `README.md` "Ground rules" | Inspect both migrations and `schema.py`; attack the config layer (Part 2-A) |
| C11 | `.gitignore` plus the hygiene tests stop confidential file types being committed | `.gitignore` header; `tests/test_repo_hygiene.py` | Try in a scratch clone (Part 2-J) |
| C12 | Locked dependencies are all MIT/BSD/Apache/PSF licensed | `docs/blockers.md` B-08 | Check the licence metadata of every package in `requirements/lock.txt` |
| C13 | Every command documented in `README.md`, `docs/source_inventory_guide.md` and `ROADMAP.md` runs as written and behaves as described (exit codes included: config refusal 3, intake refusal 4, `inventory-validate` 0/1/2) | Those documents | Run each with synthetic inputs (UNC paths replaced by local folders) |
| C14 | Each commit message accurately describes its diff, with no undisclosed changes and no claims beyond what the diff contains | Every commit from `2948ee2` to `HEAD` | `git show --stat <hash>` and read each diff against its message |
| C15 | Text extraction reads `.pdf`, `.xlsx` and `.msg` without interpreting anything; every segment carries an exact location; scans, hidden sheets, uncalculated formulas, RTF-only bodies, embedded or linked attachments are reported, never skipped; evidence is re-hashed before extraction; attachments become child documents; `layout_key` reveals no content | `docs/contract_extraction.md`; `src/riskparse/extract/*` docstrings; `HANDOFF.md` §3 | Part 2-I |

## Part 2 — Attack Claude's code

The guarantees below are what Claude's code and documents say the system does. A violation of
one is a finding against Claude's code.

1. **No confidential material in Git.** Contracts (`.pdf/.msg/.eml/...`), exports, trader sheets,
   databases and credentials can't be tracked; only blank templates live in `inventory/templates/`.
2. **SQL engine is unconfirmed.** SQLite is a *local test adapter only* (`local`/`test`). Server
   backends refuse to connect unless `database.authorized` is a real boolean `true`; **`prod` is
   always refused**; config files never contain passwords or connection strings.
3. **Document identity comes from bytes (SHA-256)**, never from the filename or folder. Identical bytes
   mean the same document (a new sighting is recorded). The same filename with different bytes is a new
   *revision*. Archived bytes are never overwritten, and tampering is detectable.
4. **Raw values are preserved.** Every source row is kept unchanged; an unparseable value is
   NULL and *reported*, never coerced or guessed; there is no unit, currency or locale conversion.
5. **A genuine `0` is not "missing".** `PRESENT` + 0 is legal; `MISSING`, `PENDING`,
   `NOT_APPLICABLE` and `UNRESOLVED` carry no number; an evergreen `$0` in Egistix is never a
   confirmed zero price.
6. **Reconciliation never assumes.** Absence from Egistix doesn't make a trade invalid. A confirmation
   with no booking is `NOT_BOOKED`. A booking with no confirmation is `UNCONFIRMED`. Disagreeing
   material fields are `MATCHED_BREAK`. `MATCHED` means side, volume and price really were compared
   and agree.
7. **Separation of duties.** A publication's creator can't approve it; an audit actor can't be
   the approver (case-, whitespace- and Unicode-insensitive). Evergreen approval needs two different
   people; whoever keys a crosswalk row can't also verify it.
8. **Publication.** Exactly one active-publication pointer row exists, Claude's code never changes it,
   and the demo candidate stays `CANDIDATE`.
9. **Audit trail** is append-only and hash-chained; edits, deletions and reordering are detected.
10. **Timestamps** are timezone-aware UTC; naive datetimes are rejected.
11. **Migrations** `0001_initial` and `0002_sod_case_and_zip` are frozen literal DDL, match
    `schema.py` exactly (including CHECK text), and upgrade/downgrade a database **that already
    holds data** without leaving foreign-key violations.
12. **Received archives are evidence.** `intake-zip` hashes the ZIP as received and never changes
    it. It never writes outside the extraction directory or overwrites a file, never follows links,
    never extracts encrypted members, stops zip bombs, and lists every refused member as
    `EXCEPTED` with its reason.

13. **Extraction is evidence-preserving and interprets nothing.** Every extracted text segment
    is the raw text with an exact location. Nothing becomes a price, volume, side or date.
    Partial reads are reported. Only bytes matching the registered SHA-256 are processed, and the
    profile output holds no document content.

### A. Configuration and connection (`config.py`, `db/connection.py`, `cli.py`)
- Wrong TOML types for every field: strings for booleans (`"false"`, `"0"`, `"no"`), integers for
  booleans (`authorized = 1`), strings, booleans or negatives for `port`, TOML datetimes, arrays or
  inline tables in string fields. Does any value that *looks* false enable a connection?
- Case and spacing variants of the environment name (`"Prod"`, `" prod"`), and unknown names.
- Hidden credentials:
  - key names Claude's word list misses (`pw`, `pass`, `passphrase`, `conn_str`);
  - credentials embedded in `host`/`database`/`odbc_driver`;
  - Unicode look-alikes of `@`, `;` and `=` (`＠`, `；`, `＝`);
  - newline or NUL characters in values.
  A legitimate SQL Server named instance `SERVER\INSTANCE` must still be accepted.
- Unknown keys or sections anywhere: is the result a clean `ConfigError` (exit 3), never a traceback?
  Also try `RISKPARSE_CONFIG` pointing at a directory, an empty file, or invalid TOML.
- Can any secret from the environment appear in `config-check` / `describe_url` output or in an
  exception message?
- Does Claude's `schema_translate_map` handling (`database.schema`) cover where Alembic puts
  `alembic_version`?

### B. Schema and migrations (`db/schema.py`, `db/migrations/`, `db/migrate.py`)
- Rows that break a CHECK's *intent* but satisfy its *expression*:
  - non-hex or uppercase SHA-256 values passing `LIKE '____...'`;
  - `reporting_month` of `'2026-13'` or `'abcd-ef'`;
  - `side = 'buy'`;
  - approver variants with whitespace (`'pat '`), or that SQL `LOWER()` and Python `.lower()`
    fold differently (`ß`, `İ`).
- Value/status rule: negative prices, `NaN`/`Infinity` reaching a NUMERIC column, and implausible
  `PRESENT` values.
- `0002` on SQLite: does the batch rebuild keep every index, FK, UNIQUE, server default and the seeded
  `active_publication` row? On downgrade, is mapping `ZIP` back to `OTHER` disclosed or silent?
- `run_guarded`:
  - can foreign keys stay **off** on a pooled connection after an exception?
  - can a violation slip past `foreign_key_check`?
  - does `alembic upgrade head` with `RISKPARSE_CONFIG` behave exactly like `riskparse db-upgrade`?
- Portability of Claude's DDL on SQL Server or PostgreSQL. Name the line and the concrete failure:
  - `LIKE` collation;
  - Boolean `server_default '0'`;
  - `DateTime` becoming SQL Server `DATETIME`;
  - `Text` columns;
  - self-referencing FKs.
- Can a second `active_publication` row exist, or can the pointer reference a non-approved publication?

### C. Evidence identity (`evidence/hashing.py`, `archive.py`, `registry.py`)
- File edge cases:
  - 0-byte, sparse/huge, Unicode or very long names;
  - names differing only by case or NFC/NFD normalisation;
  - no extension, or identical bytes under two extensions.
- A file modified *during* hashing; coarse mtime granularity defeating `UnstableFileError`.
- Duplicate path: register the same bytes first without, then with, an archive. Is the evidence
  ever archived? Are `category`/`sender` from the second call lost?
- Revision chains: A, then A′, then A again; several revisions within the same second.
- Archive: a wrong file pre-placed at the content address, a symlink at the target, a read-only
  directory, leftover temporary files, and a database insert that fails after the copy.
- Does `verify_archive` catch missing, truncated and same-size-different-bytes files?

### D. Audit trail (`audit.py`, `identity.py`)
- Swap two events; shift `occurred_at_utc` by under a second; change `actor` case; delete the
  **last** event (is truncation detectable at all?); forge an event with a recomputed hash.
- Concurrent writers on a file-backed SQLite database: does the chain fork, and is that reported?
- Canonical JSON with `Decimal`, `datetime`, sets and non-ASCII text in `before`/`after`.
- `normalize_actor`: invisible characters it misses; look-alikes NFKC does not fold (Cyrillic `а`
  versus Latin `a`); and whether the DB `LOWER()` check and the Python normalisation can disagree.

### E. Source import (`imports.py`)
Highest risk for wrong numbers. Claude claims D-2, S-4 and S-5 are fixed. **Break the fixes**:
- Values the strict pattern accepts that are still wrong:
  - `007`, `-0`;
  - digit strings beyond `NUMERIC(28,10)` precision or scale (silently rounded? especially on SQLite);
  - a declared `thousands_separator=" "` with a non-breaking space.
- Values that must stay NULL plus a report:
  - numbers: `2,65`, `1.234,56`, `$2.50`, `(100)`, `1e3`, `NaN`, `٣`;
  - dates: `2026-10-05T00:00:00`, `10/05/2026`, `2026-02-30`, Excel serials;
  - sides: `B`, `S`, `Purchase`, `Sale`.
- CSV shape: BOM, cp1252, embedded newlines, long/short rows, empty file, header-only file, repeated
  record IDs. Is the error clear, and is nothing partially written?
- Integrity: is there any path where unverified bytes are parsed (fallback to `original_path`,
  symlinks, a file replaced between read and parse)?
- Idempotency: retry after a failed import; the same file imported under two `source_system` values.

### F. Reconciliation (`reconcile/exact.py`)
Build small synthetic scenarios (see `tests/test_reconcile.py` and `demo/smoke.py`):
- Claude's S-1 fix:
  - a row with side, volume and price but no dates;
  - a value present in the CSV but unparsed (so NULL);
  - `price_value = 0` against `UNRESOLVED` terms;
  - `CONTRACT`/`OTHER` source systems;
  - rules version `exact-alias-v0.2` recorded on the run.
- Trades from another month when reconciling `2026-10` (S-2), while older evergreens still count.
- A trade linked only by `AMENDMENT`/`CANCELLATION`; a `CANCELLED` trade still in Egistix.
- Egistix and a trader sheet disagreeing with each other; one trader reference in two sheets.
- Multiple term versions and legs: is the latest version per leg used, and are legs other than 1
  ignored silently?
- Status precedence (duplicate *and* unconfirmed); stale `linked_trade_uid` reused after an alias
  is removed (S-3); determinism across two runs.

### G. Inventory template, validator and scanner (`inventory/`)
- Realistic Excel entry:
  - date cells, numbers in ID columns, SHA-256 values with spaces or in uppercase;
  - formulas, merged cells, hidden or reordered columns, renamed sheets;
  - more than 1,000 rows (data validation stops at row 1000).
  Look for wrong ERRORs and wrong passes.
- Same-person bypasses of the evergreen two-approver rule and the keyed_by ≠ verified_by rule;
  crosswalk traders missing from `Source_Coverage`.
- Exit codes of `inventory-validate` (0/1/2, and a CSV input is refused).
- Scanner: unreadable subdirectories, symlink loops, files locked or deleted mid-scan, `~$` lock files,
  a root that is a file. It must never write inside the scanned tree.

### H. Safe ZIP intake (`evidence/intake_zip.py`, `riskparse intake-zip`)
Build hostile archives with `zipfile` or by patching bytes:
- Path tricks Claude's `_safe_relative` may miss:
  - mixed separators and `./../`;
  - Unicode look-alikes of `/` and `.` (`∕`, `․`, `．．`);
  - NFC/NFD-dependent names; trailing dots or spaces on intermediate folders;
  - `CON` without an extension, `a.pdf:zone`, very long names.
- Header lies: wrong `file_size`, CRC mismatch, local versus central header disagreement,
  overlapping entries, duplicate central-directory entries, ZIP64.
- Bombs: many members just under the 10 MiB ratio floor; a large total from small members; nested
  archives (must not be unpacked).
- Race and state:
  - the destination created between the empty-check and the first write;
  - a destination that is a symlink;
  - the ZIP changing between hashing and reading;
  - a crash mid-extraction, then a retry.
- Manifest: are refused members always `EXCEPTED` with a reason? `INV-DOC-*` ID collisions between
  extracted and skipped rows; once pasted into the workbook, do the rows validate with no ERROR?
  `write_manifest` must never overwrite.
- `--register`: are members linked through `parent_document_id`? What happens with members identical
  to each other or to already-registered documents? Is a naive `--received-at` refused cleanly or
  with a traceback?

### I. Contract text extraction (`extract/*`, `profile-documents`, `extract`, `extract-preview`)
Build hostile or unusual synthetic files (see `tests/doc_fixtures.py` for `.msg`/PDF/`.xlsx`
builders):
- **PDF:** text in a different order than displayed; ligatures and hyphenation; rotated pages;
  multi-column layouts producing wrong line order (is the location still truthful?);
  incremental updates hiding earlier text; huge or compressed streams; a page under the
  20-character threshold that really is text (false `NEEDS_OCR`); scanned pages with an OCR text
  layer already embedded.
- **Excel:**
  - merged cells (do values appear once, at the right coordinate?);
  - shared or array formulas;
  - dates stored as numbers with a date format;
  - 1900-vs-1904 date systems;
  - `veryHidden` sheets; very large sheets (limits);
  - an `.xlsm` with macros (never run);
  - a cached value that disagrees with its formula.
- **.msg:**
  - ANSI strings with a missing or wrong codepage;
  - a sender with only an Exchange (X.500) address;
  - an HTML body containing script, hidden text or comments;
  - attachments with no filename, duplicate filenames or path tricks in the name;
  - nested embedded emails; a truncated or corrupt property stream;
  - a `.msg` that is really an `.eml`.
  Check the `__properties_version1.0` header sizes Claude assumed (32 bytes root, 8 bytes
  attachment) against MS-OXMSG.
- **Store and profile:**
  - is anything parsed from unverified bytes?
  - is re-extraction triggered when an extractor version changes, and only then?
  - can the same attachment registered from two emails be lost, or its parent link be wrong?
  - can `layout_key` leak content, or group different layouts together (or split one layout)
    enough to mislead parser prioritisation?
  - can `profile-documents` or `extract` write anything inside the scanned folder?

### J. Claude's tests, demo and repository hygiene
- Tests that **pass for the wrong reason**:
  - always-true assertions;
  - `pytest.raises` catching a broader error than the rule under test;
  - the `autouse` `chdir` fixture hiding path bugs.
  For each one, show the mutation of a scratch copy of `src/` that should fail the test but doesn't.
- `run_smoke`: can it delete anything other than its own work directory (spoofed marker, symlinked
  workdir, `.`)?
- In a scratch clone, try committing `contract.PDF`, `x.pdf.txt`, `notes.xlsx` in `docs/` or
  `.env.prod`. Which ones get past Claude's `.gitignore` and hygiene tests?

## Severity rubric

| Severity | Meaning |
| --- | --- |
| **Critical** | A wrong economic value, sign, status or identity can reach SQL looking valid; a safety guard (prod refusal, authorization, no credentials, separation of duties) can be bypassed; or a Claude claim of "fixed"/"verified" is false for a High-or-worse issue |
| **High** | Silent data loss or silent skip; undetected tampering; a control that exists only on paper; a claimed test that cannot fail; breakage on the likely target engine |
| **Medium** | A crash or misleading error on realistic input; non-determinism; a test that passes for the wrong reason; a documented command that doesn't work as written |
| **Low** | Hardening, clarity, portability nits; a document or commit message that overstates or disagrees with the code |

## Required output

1. **Baseline**: the test and lint results you observed (C1).
2. **Claims table (Part 1)**: C1–C15, each TRUE / FALSE (finding #) / NOT VERIFIABLE (why), with the
   command you ran.
3. **Findings table**, most severe first:

   | # | Severity | Category | Claude artifact (`file:line` or claim location) | Defect (one sentence) | Reproduction | Expected vs actual | Suggested fix | Confidence |
   | --- | --- | --- | --- | --- | --- | --- | --- | --- |

   Category is one of: false-claim, correctness, data-integrity, security-control, portability,
   robustness, test-quality, docs.
4. **Reproduction code**: each `tests/review/test_review_*.py` or `review_scratch/*` file, with the
   command to run it and its failing output.
5. **Unconfirmed suspicions**, each with why it could not be reproduced.
6. **Guarantee map**: guarantees 1–13, each *held*, *violated (finding #)* or *untested*.
7. **Top 5 fixes**, ordered by risk reduction per hour of work.
8. **Noted, out of scope**: one line per item, if any.

Do Part 2 independently **before** reading `HANDOFF.md` §5–6, so that Claude's own defect list
does not steer you. Then complete C3–C5 against those sections. A finding that only restates an
open S-item counts only if you show a new variant or a wider impact.
