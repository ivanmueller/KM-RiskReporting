# SQL platform confirmation (IT checklist) — Stage 1, Week 1

**Status: NOT CONFIRMED.** The firm's existing SQL infrastructure is confirmed to exist, but
product, instance, access and backup arrangements have not been specified (game plan §1.1,
§3.1, §14.2 item 1). Phase 1 code therefore:

- uses **only portable DDL** (tables, PK/FK, UNIQUE, CHECK with `IN`/`LIKE`/comparisons, indexes);
- runs and tests migrations on **SQLite as a local test adapter with synthetic data only**;
- includes *candidate* URL builders for SQL Server/Azure SQL (`mssql+pyodbc`) and PostgreSQL
  (`postgresql+psycopg`) that are **unverified** and refuse to connect unless the configuration is
  marked `authorized = true`;
- **never connects to an environment named `prod`** in Phase 1;
- never reads a password or connection string from a config file.

## Questions for IT / DBA (please answer in writing)

| # | Question | Why it matters | Answer |
| --- | --- | --- | --- |
| 1 | SQL product, edition and version (e.g. SQL Server 2022 Standard, Azure SQL Database, other)? | Selects dialect, driver and which deferred features exist | |
| 2 | Server/instance names for DEV/TEST and PROD; are they physically or logically separated? | Phase 2 develops only against DEV/TEST | |
| 3 | Dedicated database or schema for risk reporting? Name? | `database.database` / `database.schema` settings | |
| 4 | Client driver and version approved for the controlled machine (e.g. *ODBC Driver 18 for SQL Server*)? | `database.odbc_driver`; package `pyodbc` | |
| 5 | Authentication: Windows integrated, managed identity, or SQL login? Service identity for later scheduled runs? | No hard-coded passwords (§13.2) | |
| 6 | TLS/encryption requirements and certificate trust? | `encrypt`, `trust_server_certificate` | |
| 7 | Database owner / DBA contact and change process for migrations? | Peer-reviewed migrations (§12.3) | |
| 8 | Role model: can we create roles for staging writer, read-only reporter, reviewer, restricted publisher? Can UPDATE/DELETE be denied on audit and published tables? | §13.1 role separation; immutability enforcement | |
| 9 | Recovery model, backup frequency, retention, restore-test schedule, RPO/RTO? | §9.5 — business rollback ≠ DR | |
| 10 | Is application locking available (e.g. `sp_getapplock` on SQL Server, advisory locks on PostgreSQL)? | Serialized publish/rollback (§9.3) | |
| 11 | Network path from the controlled Python machine to the DEV/TEST instance (firewall/ports)? | Connectivity test | |
| 12 | Any restriction on Python packages (`SQLAlchemy`, `alembic`, `openpyxl`, `pyodbc`) — see `requirements/lock.txt`? | §13.3 dependency approval | |

## Once IT answers

1. Copy `config/settings.example.toml` to `config/settings.local.toml` (git-ignored) and fill in
   the server fields **from IT-issued details only**. Leave `authorized = false` until IT confirms
   the configuration in writing, then set it to `true` for that DEV/TEST environment only.
2. Install the single confirmed driver extra, e.g. `pip install -e ".[mssql]"`.
3. `riskparse --config config/settings.local.toml config-check` (prints a redacted URL; no connection).
4. `riskparse --config config/settings.local.toml db-upgrade` against **DEV/TEST**.
5. Add an engine-specific CI/test job and review these items for that engine:
   - timestamp type (SQL Server: map `UTCDateTime` to `DATETIME2`, not legacy `DATETIME`);
   - exact `NUMERIC(28,10)` round-trip (SQLite does not guarantee this);
   - Boolean server defaults (`'0'`) and CHECK expressions render correctly;
   - constraint-name length limits (all current names ≤ 58 characters);
   - collation/case sensitivity of `LIKE` and `LOWER()` used for filename revision detection.
6. Only then design the engine-specific controls (new, reviewed migrations): role grants,
   `DENY UPDATE/DELETE` on `audit_events` and `published_trade_rows`, the transactional publisher
   with an application lock and expected-current-ID check, and backup/restore drills.

## Features intentionally deferred until the engine is confirmed

| Feature | Game plan | Reason deferred |
| --- | --- | --- |
| Publisher stored procedure / service with app lock | §9.3 | Lock primitives are engine-specific |
| Role grants / DENY on immutable tables | §13.1 | Permission model is engine- and IT-specific |
| Immutability triggers | §9.1 | Trigger syntax is engine-specific |
| Temporal tables | §17 [S3] | SQL Server-specific; not a substitute for snapshots |
| SQL Server Agent jobs | §17 [S4] | Scheduling is out of Phase 1 scope |
| Offline SQL script generation (`alembic --sql`) | §12.3 | Disabled until DDL can be reviewed for the confirmed dialect |
