# Blockers and open dependencies — as of 2026-10-31

Phase 1 was completed with **synthetic fixtures only**. The items below block real-data work.
None has been resolved by producing code; each needs a named owner's written answer.
The same list is seeded in the inventory workbook's `Open_Questions` sheet (Q-001 … Q-010).

| ID | Blocker | Owner (proposed) | Blocks | Workaround in place |
| --- | --- | --- | --- | --- |
| B-01 | **SQL product/edition/version not confirmed** | IT / DBA | Any connection beyond SQLite; engine-specific migrations, locks, permissions | Portable schema + SQLite test adapter; candidate URL builders refuse without `authorized = true` |
| B-02 | **No DEV/TEST database, credentials method or service identity issued** | IT | `db-upgrade` against a real instance | None — do not improvise credentials |
| B-03 | **Backup / recovery model / restore testing not agreed** | IT / DBA | Production readiness; recovery drill (§9.5) | None |
| B-04 | **Exact UNC path for the L drive Risk Reporting root and restricted `07_SourceArchive`; share+NTFS permissions** | IT | Real evidence archive; folder creation | Local `var/archive` for demo only |
| B-05 | **No actual October 2026 source data available to the developer** (contracts, emails, scans, amendments, evergreen agreements) | Risk analyst | Inventory completion, parser template priorities, golden set | Synthetic fixtures; `inventory-scan` ready to hash real folders on the controlled machine |
| B-06 | **Egistix export(s) and column dictionary not provided**; meaning of `$0` for evergreen deals unknown | Egistix owner + Risk | Real Egistix import mapping; evergreen reconciliation | Synthetic Egistix layout; `$0` never treated as a price (tested) |
| B-07 | **In-scope traders (2 or 3), their books and each October trader sheet not identified** | Risk + traders | Trader-sheet import mappings; completeness | Template seeds three trader rows; validator warns if fewer than two named |
| B-08 | **Approved Python environment / package review on the controlled machine** | IT security | Running tools on real data | `requirements/lock.txt` (all MIT/BSD/Apache/PSF licences) submitted for review. Added for extraction: `pypdf` 6.21.4 (BSD-3-Clause) and `olefile` 0.47 (BSD-2-Clause); the GPL-licensed `extract-msg` was deliberately avoided |
| B-09 | **Reconciliation status definitions, aging rule, escalation owner and hard-blocker list not approved** | Risk manager | Policy-driven reconciliation beyond the exact-alias slice | Conservative defaults; nothing auto-resolves |
| B-10 | **Enterprise AI approval for contract interpretation not granted** | Legal / IT security | AI-assisted clause review | Manual review; no confidential data sent to AI tools |
| B-11 | **Company Git host / remote for this repository and branch-protection rules not confirmed** | IT | Production change control (§12.3) | Code kept in the project repository only |

## What is *not* blocked

- Risk can start the source inventory **today** using
  `inventory/templates/October_2026_Source_Inventory_TEMPLATE.xlsx` and `riskparse inventory-scan`
  on the controlled machine (output stays on the L drive).
- IT can answer `docs/sql_platform_confirmation.md` in parallel.
