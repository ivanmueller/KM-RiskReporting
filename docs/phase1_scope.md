# Phase 1 scope and traceability

Governing specification: *Risk Reporting Automation — Project Game Plan v2.0 (2026-10-31)*.
Phase 1 = project setup, source-inventory framework and minimal database foundation
(game plan Week 1 discovery tooling plus the parts of Week 2 that can be built safely without
real data or a confirmed SQL engine).

## Delivered

| Deliverable | Location | Game plan |
| --- | --- | --- |
| Modular Python package `riskparse`, `pyproject.toml`, pinned lockfile | `src/riskparse/`, `requirements/lock.txt` | §1, §13.3 |
| Confidentiality-first `.gitignore` + repository hygiene tests | `.gitignore`, `tests/test_repo_hygiene.py` | §6.3, §13.3 |
| Configuration with credential/production guards | `src/riskparse/config.py`, `config/*.toml` | §3.1, §13.2 |
| Configuration-driven connection layer (SQLite test adapter; unverified mssql/postgresql candidates gated by `authorized`) | `src/riskparse/db/connection.py` | §3.1 |
| Minimal portable schema (16 tables) and frozen Alembic migration `0001_initial` | `src/riskparse/db/schema.py`, `src/riskparse/db/migrations/` | §5.1, §9.1, §12.3 |
| Data dictionary | `docs/data_dictionary.md` | §2.1 |
| Document hashing, stable-file check, content-addressed archive, duplicate vs revision detection | `src/riskparse/evidence/` | §4.1, §9.5, §11 |
| Hash-chained audit events with verification | `src/riskparse/audit.py` | §12.1, §12.2 |
| CSV source import preserving raw rows (synthetic mappings only) | `src/riskparse/imports.py` | §7.1 |
| Exact-alias reconciliation slice | `src/riskparse/reconcile/exact.py` | §7.2 step 1, §7.3 |
| Source-inventory template (xlsx + csv), validator and read-only folder scanner | `src/riskparse/inventory/`, `inventory/templates/` | §2.3, §7.1, §8.1, §14.2 |
| Synthetic end-to-end smoke demo | `src/riskparse/demo/smoke.py`, `tests/test_smoke_e2e.py` | §3 |
| SQL confirmation checklist, blockers register, inventory guide | `docs/` | §14.2, §16 |

## Follow-up fixes (after Phase 1 handoff)

Defects D-1 to D-5 and suspicions S-1, S-4, S-5 and S-13 from `HANDOFF.md` were fixed with
regression tests. Migration `0002_sod_case_and_zip` was added, along with safe ZIP intake
(`riskparse intake-zip`) and the data-dictionary generator. See `ROADMAP.md` (B1–B8).

## Controls demonstrated by tests

- Identity from bytes only; filename never becomes `document_id`.
- Identical bytes ⇒ one document, every observed path/filename retained.
- Same filename, different bytes ⇒ new revision; archived bytes never overwritten; tampering detected.
- Genuine `0` price allowed; `UNRESOLVED`/`MISSING` values cannot carry a number (DB CHECK).
- Evergreen `$0` in Egistix/trader sheet is reported as a break/unresolved, not a price.
- A confirmed trade absent from Egistix is `UNRESOLVED`, not invalid; a confirmation with no booking is `NOT_BOOKED`.
- Creator of a publication cannot approve it; approved/published states require approver/time (DB CHECK).
- Single active-publication slot, seeded empty; demo leaves it empty (no publishing).
- Audit chain detects edited and deleted events; actor ≠ approver.
- Naive timestamps rejected; foreign keys enforced on SQLite.
- Inventory validator: Risk ≠ Finance approver for evergreen APPROVED; keyed_by ≠ verified_by; all crosswalk traders must be in coverage; timestamps need a timezone.
- Config refuses passwords/connection strings, refuses `prod`, refuses unauthorized server backends.

## Deliberately NOT built (per instruction and game plan)

- Economic parsing of PDF / Excel / `.msg` confirmations (turning text into prices, volumes,
  dates or sides), `.eml` support and OCR. *Update:* layout-neutral **text** extraction with
  evidence locations for `.pdf`, `.xlsx` and `.msg` was added after Phase 1; see
  `docs/contract_extraction.md`. It interprets nothing.
- Normalization reference maps, unit conversion, composite/fuzzy matching, amendment chains.
- Approval workflow, publication activation, diff, rollback, recovery drill.
- Excel report generation and manifests.
- Dashboards, scheduling/polling, alerts, Windows service configuration.
- Engine-specific SQL (locks, permissions, triggers, temporal tables).
- Any connection to company systems, L drive or real data.

## Synthetic demo walkthrough

`riskparse demo-smoke` creates a throw-away directory with fake files clearly bannered
`SYNTHETIC TEST FIXTURE`, then:

| Synthetic trade | Situation | Expected status |
| --- | --- | --- |
| SYN-T001 | Confirm, Egistix and trader sheet agree | `MATCHED` |
| SYN-T002 | Egistix price 2.60 vs confirmed 2.65 | `MATCHED_BREAK` |
| SYN-T003 | Confirm + trader sheet, not in Egistix | `UNRESOLVED` (Risk classifies) |
| SYN-T004 | Confirm only, booked nowhere | `NOT_BOOKED` |
| SYN-T005 | Evergreen, value meaning unresolved, Egistix/trader show 0 | `UNRESOLVED` |
| TA-003 | Trader-sheet row with no confirm/trade | `UNCONFIRMED` |

It also registers a byte-identical copy (`DUPLICATE`) and a same-name different-content file
(`REVISION`), creates a `CANDIDATE` publication with source-inventory digest and five snapshot
rows, and verifies that the active pointer is still empty, the audit chain is intact and every
archived object re-hashes correctly.
