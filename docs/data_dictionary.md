# Data dictionary - Stage 1 minimal schema (migration `0001_initial`)

Generated from `src/riskparse/db/schema.py`; the frozen migration is the physical source of truth and `tests/test_migrations.py` asserts they match.

Conventions: all timestamps are UTC (`*_utc`, timezone-aware values required; naive values are rejected). Money/quantity use `NUMERIC(28,10)`. JSON is stored as text for portability. Only portable DDL (PK, FK, UNIQUE, CHECK, index) is used until IT confirms the SQL engine.

> SQLite (local test adapter) stores NUMERIC as floating point; exact-decimal behaviour must be re-verified on the confirmed engine.

## `source_documents`

One immutable file revision. Identity = SHA-256 of bytes; `document_id` assigned at ingestion. Same bytes = same row; same filename + different bytes = new row with `revision_of_document_id`.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `document_id` | VARCHAR(40) | no | PK |
| `sha256` | VARCHAR(64) | no |  |
| `size_bytes` | BIGINT | no |  |
| `file_type` | VARCHAR(10) | no |  |
| `document_category` | VARCHAR(30) | yes |  |
| `original_filename` | VARCHAR(400) | no |  |
| `original_path` | VARCHAR(1000) | no |  |
| `archive_uri` | VARCHAR(1000) | yes |  |
| `source_channel` | VARCHAR(40) | no |  |
| `sender` | VARCHAR(320) | yes |  |
| `received_at_utc` | DATETIME (UTC) | yes |  |
| `recorded_at_utc` | DATETIME (UTC) | no |  |
| `file_modified_at_utc` | DATETIME (UTC) | yes |  |
| `content_status` | VARCHAR(20) | no | default `REGISTERED` |
| `parent_document_id` | VARCHAR(40) | yes | FK → source_documents.document_id |
| `revision_of_document_id` | VARCHAR(40) | yes | FK → source_documents.document_id |
| `is_synthetic` | BOOLEAN | no | default `0` |
| `evidence_note` | TEXT | yes |  |

Rules:

- CHECK `ck_source_documents_category`: `(document_category IS NULL OR document_category IN ('CONFIRMATION', 'AMENDMENT', 'CANCELLATION', 'TERMINATION', 'EVERGREEN_AGREEMENT', 'MASTER_AGREEMENT', 'E...`
- CHECK `ck_source_documents_content_status`: `content_status IN ('REGISTERED', 'PENDING_REVIEW', 'ACCEPTED', 'REJECTED', 'REMOVED', 'EXCEPTED')`
- CHECK `ck_source_documents_file_type`: `file_type IN ('PDF', 'XLSX', 'XLS', 'XLSM', 'CSV', 'EML', 'MSG', 'DOCX', 'TIFF', 'OTHER')`
- CHECK `ck_source_documents_parent_not_self`: `parent_document_id IS NULL OR parent_document_id <> document_id`
- CHECK `ck_source_documents_revision_not_self`: `revision_of_document_id IS NULL OR revision_of_document_id <> document_id`
- CHECK `ck_source_documents_sha256_shape`: `sha256 LIKE '________________________________________________________________'`
- CHECK `ck_source_documents_size_nonneg`: `size_bytes >= 0`
- UNIQUE (sha256)

## `document_observations`

Every place/time/filename a document's bytes were seen (duplicates keep all observed names and paths).

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `observation_id` | INTEGER | no | PK |
| `document_id` | VARCHAR(40) | no | FK → source_documents.document_id |
| `observed_path` | VARCHAR(1000) | no |  |
| `observed_filename` | VARCHAR(400) | no |  |
| `observed_at_utc` | DATETIME (UTC) | no |  |
| `file_modified_at_utc` | DATETIME (UTC) | yes |  |

## `trades`

One economic deal with internal `trade_uid`. Egistix, counterparty and trader references live in `trade_aliases`, never as the key.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `trade_uid` | VARCHAR(40) | no | PK |
| `trader` | VARCHAR(100) | yes |  |
| `book` | VARCHAR(100) | yes |  |
| `counterparty_id` | VARCHAR(60) | yes |  |
| `counterparty_raw_name` | VARCHAR(300) | yes |  |
| `commodity` | VARCHAR(40) | yes |  |
| `product` | VARCHAR(80) | yes |  |
| `physical_financial` | VARCHAR(10) | no | default `UNKNOWN` |
| `is_evergreen` | BOOLEAN | no | default `0` |
| `lifecycle_status` | VARCHAR(20) | no | default `ACTIVE` |
| `trade_date` | DATE | yes |  |
| `parent_trade_uid` | VARCHAR(40) | yes | FK → trades.trade_uid |
| `is_synthetic` | BOOLEAN | no | default `0` |
| `created_at_utc` | DATETIME (UTC) | no |  |

Rules:

- CHECK `ck_trades_lifecycle`: `lifecycle_status IN ('ACTIVE', 'AMENDED', 'CANCELLED', 'TERMINATED', 'EXPIRED', 'UNRESOLVED')`
- CHECK `ck_trades_parent_not_self`: `parent_trade_uid IS NULL OR parent_trade_uid <> trade_uid`
- CHECK `ck_trades_phys_fin`: `physical_financial IN ('PHYSICAL', 'FINANCIAL', 'UNKNOWN')`

## `trade_aliases`

Many-to-one source references for a trade, unique per alias system.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `alias_id` | INTEGER | no | PK |
| `trade_uid` | VARCHAR(40) | no | FK → trades.trade_uid |
| `alias_system` | VARCHAR(20) | no |  |
| `alias_value` | VARCHAR(120) | no |  |
| `created_at_utc` | DATETIME (UTC) | no |  |

Rules:

- CHECK `ck_trade_aliases_alias_system`: `alias_system IN ('EGISTIX', 'COUNTERPARTY', 'TRADER', 'BROKER', 'EXCHANGE', 'INTERNAL')`
- UNIQUE (alias_system, alias_value)

## `trade_documents`

Trade-to-document relationship (confirmation, amendment, termination, evergreen agreement...) with effective date and supersession scope. An amendment does not replace a trade implicitly.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `trade_uid` | VARCHAR(40) | no | PK, FK → trades.trade_uid |
| `document_id` | VARCHAR(40) | no | PK, FK → source_documents.document_id |
| `relationship` | VARCHAR(30) | no | PK |
| `effective_from` | DATE | yes |  |
| `supersession_scope` | TEXT | yes |  |
| `created_at_utc` | DATETIME (UTC) | no |  |

Rules:

- CHECK `ck_trade_documents_relationship`: `relationship IN ('CONFIRMATION', 'AMENDMENT', 'CANCELLATION', 'TERMINATION', 'EVERGREEN_AGREEMENT', 'SUPPORTING_EMAIL', 'OTHER')`

## `trade_terms`

Versioned economic terms per trade leg, raw text alongside standardized values. `*_status` distinguishes a genuine zero (PRESENT, 0) from MISSING/PENDING/NOT_APPLICABLE/UNRESOLVED (value must be NULL).

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `term_id` | INTEGER | no | PK |
| `trade_uid` | VARCHAR(40) | no | FK → trades.trade_uid |
| `leg` | INTEGER | no | default `1` |
| `term_version` | INTEGER | no | default `1` |
| `side` | VARCHAR(4) | no |  |
| `price_type` | VARCHAR(20) | no |  |
| `price_status` | VARCHAR(20) | no |  |
| `price_value` | NUMERIC(28, 10) | yes |  |
| `price_raw` | VARCHAR(300) | yes |  |
| `price_unit` | VARCHAR(40) | yes |  |
| `currency` | VARCHAR(3) | yes |  |
| `index_name` | VARCHAR(120) | yes |  |
| `price_differential` | NUMERIC(28, 10) | yes |  |
| `volume_status` | VARCHAR(20) | no |  |
| `volume_value` | NUMERIC(28, 10) | yes |  |
| `volume_raw` | VARCHAR(300) | yes |  |
| `volume_unit` | VARCHAR(40) | yes |  |
| `volume_basis` | VARCHAR(20) | no | default `UNKNOWN` |
| `delivery_location` | VARCHAR(120) | yes |  |
| `delivery_timezone` | VARCHAR(60) | yes |  |
| `delivery_start` | DATE | yes |  |
| `delivery_end` | DATE | yes |  |
| `valid_from_utc` | DATETIME (UTC) | yes |  |
| `valid_to_utc` | DATETIME (UTC) | yes |  |
| `evidence_document_id` | VARCHAR(40) | yes | FK → source_documents.document_id |
| `evidence_location` | VARCHAR(300) | yes |  |
| `review_status` | VARCHAR(20) | no | default `UNREVIEWED` |
| `created_at_utc` | DATETIME (UTC) | no |  |

Rules:

- CHECK `ck_trade_terms_delivery_order`: `delivery_start IS NULL OR delivery_end IS NULL OR delivery_end >= delivery_start`
- CHECK `ck_trade_terms_leg_version_pos`: `leg >= 1 AND term_version >= 1`
- CHECK `ck_trade_terms_price_status`: `price_status IN ('PRESENT', 'MISSING', 'PENDING', 'NOT_APPLICABLE', 'UNRESOLVED')`
- CHECK `ck_trade_terms_price_type`: `price_type IN ('FIXED', 'INDEX', 'INDEX_PLUS_DIFF', 'FORMULA', 'UNRESOLVED')`
- CHECK `ck_trade_terms_price_value`: `((price_status = 'PRESENT' AND price_value IS NOT NULL) OR (price_status <> 'PRESENT' AND price_value IS NULL))`
- CHECK `ck_trade_terms_review_status`: `review_status IN ('UNREVIEWED', 'EXTRACTED', 'VERIFIED', 'DISPUTED', 'REJECTED')`
- CHECK `ck_trade_terms_side`: `side IN ('BUY', 'SELL')`
- CHECK `ck_trade_terms_validity_order`: `valid_from_utc IS NULL OR valid_to_utc IS NULL OR valid_to_utc > valid_from_utc`
- CHECK `ck_trade_terms_volume_basis`: `volume_basis IN ('PER_HOUR', 'PER_DAY', 'PER_MONTH', 'TOTAL', 'OTHER', 'UNKNOWN')`
- CHECK `ck_trade_terms_volume_nonneg`: `volume_value IS NULL OR volume_value >= 0`
- CHECK `ck_trade_terms_volume_status`: `volume_status IN ('PRESENT', 'MISSING', 'PENDING', 'NOT_APPLICABLE', 'UNRESOLVED')`
- CHECK `ck_trade_terms_volume_value`: `((volume_status = 'PRESENT' AND volume_value IS NOT NULL) OR (volume_status <> 'PRESENT' AND volume_value IS NULL))`
- UNIQUE (trade_uid, leg, term_version)

## `source_imports`

One import of a registered Egistix export or trader sheet with its cutoff; idempotent per (document, source system, parser version).

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `import_id` | VARCHAR(40) | no | PK |
| `source_system` | VARCHAR(20) | no |  |
| `source_owner` | VARCHAR(100) | no |  |
| `source_name` | VARCHAR(200) | no |  |
| `document_id` | VARCHAR(40) | no | FK → source_documents.document_id |
| `reporting_month` | VARCHAR(7) | no |  |
| `source_cutoff_utc` | DATETIME (UTC) | no |  |
| `imported_at_utc` | DATETIME (UTC) | no |  |
| `row_count` | INTEGER | yes |  |
| `status` | VARCHAR(20) | no | default `STAGED` |
| `parser_version` | VARCHAR(60) | no |  |

Rules:

- CHECK `ck_source_imports_month_fmt`: `reporting_month LIKE '____-__'`
- CHECK `ck_source_imports_row_count_nonneg`: `row_count IS NULL OR row_count >= 0`
- CHECK `ck_source_imports_source_system`: `source_system IN ('CONTRACT', 'EGISTIX', 'TRADER_SHEET', 'BROKER', 'EXCHANGE', 'OTHER')`
- CHECK `ck_source_imports_status`: `status IN ('STAGED', 'LOADED', 'FAILED', 'SUPERSEDED')`
- UNIQUE (document_id, source_system, parser_version)

## `source_records`

One unchanged raw row (JSON + hash) from an import, with optional normalized fields and a link to a trade once matched.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `source_record_pk` | INTEGER | no | PK |
| `import_id` | VARCHAR(40) | no | FK → source_imports.import_id |
| `source_record_id` | VARCHAR(120) | no |  |
| `row_number` | INTEGER | yes |  |
| `raw_payload` | TEXT | no |  |
| `raw_payload_sha256` | VARCHAR(64) | no |  |
| `trade_ref` | VARCHAR(120) | yes |  |
| `trade_date` | DATE | yes |  |
| `counterparty_raw` | VARCHAR(300) | yes |  |
| `side` | VARCHAR(4) | yes |  |
| `commodity` | VARCHAR(40) | yes |  |
| `volume_value` | NUMERIC(28, 10) | yes |  |
| `volume_unit` | VARCHAR(40) | yes |  |
| `price_value` | NUMERIC(28, 10) | yes |  |
| `price_unit` | VARCHAR(40) | yes |  |
| `delivery_start` | DATE | yes |  |
| `delivery_end` | DATE | yes |  |
| `linked_trade_uid` | VARCHAR(40) | yes | FK → trades.trade_uid |

Rules:

- CHECK `ck_source_records_side`: `(side IS NULL OR side IN ('BUY', 'SELL'))`
- UNIQUE (import_id, source_record_id)

## `reconciliation_runs`

One reconciliation execution with code and rules versions.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `recon_run_id` | VARCHAR(40) | no | PK |
| `reporting_month` | VARCHAR(7) | no |  |
| `cutoff_utc` | DATETIME (UTC) | no |  |
| `started_at_utc` | DATETIME (UTC) | no |  |
| `completed_at_utc` | DATETIME (UTC) | yes |  |
| `status` | VARCHAR(20) | no | default `RUNNING` |
| `code_version` | VARCHAR(80) | no |  |
| `rules_version` | VARCHAR(80) | no |  |
| `created_by` | VARCHAR(100) | no |  |

Rules:

- CHECK `ck_reconciliation_runs_month_fmt`: `reporting_month LIKE '____-__'`
- CHECK `ck_reconciliation_runs_status`: `status IN ('RUNNING', 'COMPLETED', 'FAILED')`

## `reconciliation_items`

One match or break with status (game plan 7.3), severity, state, owner/reviewer and age. Resolution requires a reviewer and time.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `item_id` | INTEGER | no | PK |
| `recon_run_id` | VARCHAR(40) | no | FK → reconciliation_runs.recon_run_id |
| `trade_uid` | VARCHAR(40) | yes | FK → trades.trade_uid |
| `recon_status` | VARCHAR(30) | no |  |
| `match_method` | VARCHAR(30) | no |  |
| `match_quality` | VARCHAR(10) | no |  |
| `differing_fields` | TEXT | yes |  |
| `severity` | VARCHAR(10) | no | default `INFO` |
| `item_state` | VARCHAR(20) | no | default `OPEN` |
| `owner` | VARCHAR(100) | yes |  |
| `reviewer` | VARCHAR(100) | yes |  |
| `opened_at_utc` | DATETIME (UTC) | no |  |
| `resolved_at_utc` | DATETIME (UTC) | yes |  |
| `resolution_note` | TEXT | yes |  |
| `resolution_document_id` | VARCHAR(40) | yes | FK → source_documents.document_id |

Rules:

- CHECK `ck_reconciliation_items_item_state`: `item_state IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'EXCLUDED_DISCLOSED')`
- CHECK `ck_reconciliation_items_match_method`: `match_method IN ('EXACT_ALIAS', 'COMPOSITE', 'MANUAL', 'NONE')`
- CHECK `ck_reconciliation_items_match_quality`: `match_quality IN ('EXACT', 'HIGH', 'LOW', 'NONE')`
- CHECK `ck_reconciliation_items_recon_status`: `recon_status IN ('MATCHED', 'MATCHED_BREAK', 'UNCONFIRMED', 'NOT_BOOKED', 'EGISTIX_NOT_APPLICABLE', 'DUPLICATE_POSSIBLE', 'CANCELLED_OR_AMENDED', 'UNRESOLVED')`
- CHECK `ck_reconciliation_items_resolution_reviewed`: `item_state NOT IN ('RESOLVED', 'EXCLUDED_DISCLOSED') OR (resolved_at_utc IS NOT NULL AND reviewer IS NOT NULL)`
- CHECK `ck_reconciliation_items_severity`: `severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')`

## `reconciliation_item_sources`

Links a reconciliation item to each source record or document involved (supports one-to-many / many-to-one).

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `link_id` | INTEGER | no | PK |
| `item_id` | INTEGER | no | FK → reconciliation_items.item_id |
| `source_record_pk` | INTEGER | yes | FK → source_records.source_record_pk |
| `document_id` | VARCHAR(40) | yes | FK → source_documents.document_id |

Rules:

- CHECK `ck_reconciliation_item_sources_exactly_one_source`: `(source_record_pk IS NULL AND document_id IS NOT NULL) OR (source_record_pk IS NOT NULL AND document_id IS NULL)`

## `publication_runs`

One candidate/held/approved/published dataset version with cutoff, source-inventory digest and code/config versions. The creator cannot be the approver (DB CHECK).

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `publication_id` | VARCHAR(20) | no | PK |
| `reporting_month` | VARCHAR(7) | no |  |
| `reporting_cutoff_utc` | DATETIME (UTC) | no |  |
| `reporting_timezone` | VARCHAR(60) | no |  |
| `recon_run_id` | VARCHAR(40) | yes | FK → reconciliation_runs.recon_run_id |
| `source_inventory_digest` | VARCHAR(64) | no |  |
| `code_version` | VARCHAR(80) | no |  |
| `config_version` | VARCHAR(80) | no |  |
| `status` | VARCHAR(20) | no | default `CANDIDATE` |
| `totals_json` | TEXT | yes |  |
| `created_by` | VARCHAR(100) | no |  |
| `created_at_utc` | DATETIME (UTC) | no |  |
| `approved_by` | VARCHAR(100) | yes |  |
| `approved_at_utc` | DATETIME (UTC) | yes |  |
| `published_at_utc` | DATETIME (UTC) | yes |  |

Rules:

- CHECK `ck_publication_runs_approved_has_approver`: `status NOT IN ('APPROVED', 'PUBLISHED', 'SUPERSEDED') OR (approved_by IS NOT NULL AND approved_at_utc IS NOT NULL)`
- CHECK `ck_publication_runs_approver_sod`: `approved_by IS NULL OR approved_by <> created_by`
- CHECK `ck_publication_runs_month_fmt`: `reporting_month LIKE '____-__'`
- CHECK `ck_publication_runs_published_has_time`: `status NOT IN ('PUBLISHED', 'SUPERSEDED') OR published_at_utc IS NOT NULL`
- CHECK `ck_publication_runs_status`: `status IN ('CANDIDATE', 'VALIDATION_FAILED', 'VALIDATED', 'HELD', 'APPROVED', 'PUBLISHED', 'SUPERSEDED')`

## `publication_sources`

Source-document inventory (with hash at the time) included in a publication.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `publication_id` | VARCHAR(20) | no | PK, FK → publication_runs.publication_id |
| `document_id` | VARCHAR(40) | no | PK, FK → source_documents.document_id |
| `sha256` | VARCHAR(64) | no |  |

## `published_trade_rows`

Immutable reporting rows per publication/trade/leg/segment. Immutability is application policy now; DB permissions/triggers after engine confirmation.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `publication_id` | VARCHAR(20) | no | PK, FK → publication_runs.publication_id |
| `trade_uid` | VARCHAR(40) | no | PK, FK → trades.trade_uid |
| `leg` | INTEGER | no | PK |
| `segment` | INTEGER | no | PK |
| `term_id` | INTEGER | no | FK → trade_terms.term_id |
| `trader` | VARCHAR(100) | yes |  |
| `counterparty_raw_name` | VARCHAR(300) | yes |  |
| `commodity` | VARCHAR(40) | yes |  |
| `product` | VARCHAR(80) | yes |  |
| `physical_financial` | VARCHAR(10) | no |  |
| `is_evergreen` | BOOLEAN | no |  |
| `side` | VARCHAR(4) | no |  |
| `signed_quantity` | NUMERIC(28, 10) | yes |  |
| `quantity_unit` | VARCHAR(40) | yes |  |
| `volume_basis` | VARCHAR(20) | no |  |
| `price_status` | VARCHAR(20) | no |  |
| `price_value` | NUMERIC(28, 10) | yes |  |
| `price_unit` | VARCHAR(40) | yes |  |
| `currency` | VARCHAR(3) | yes |  |
| `delivery_location` | VARCHAR(120) | yes |  |
| `delivery_start` | DATE | yes |  |
| `delivery_end` | DATE | yes |  |
| `recon_status` | VARCHAR(30) | no |  |
| `exposure_review_status` | VARCHAR(30) | no |  |

Rules:

- CHECK `ck_published_trade_rows_price_value`: `((price_status = 'PRESENT' AND price_value IS NOT NULL) OR (price_status <> 'PRESENT' AND price_value IS NULL))`
- CHECK `ck_published_trade_rows_recon_status`: `recon_status IN ('MATCHED', 'MATCHED_BREAK', 'UNCONFIRMED', 'NOT_BOOKED', 'EGISTIX_NOT_APPLICABLE', 'DUPLICATE_POSSIBLE', 'CANCELLED_OR_AMENDED', 'UNRESOLVED')`
- CHECK `ck_published_trade_rows_side`: `side IN ('BUY', 'SELL')`

## `active_publication`

Single-row active-publication pointer with `pointer_version` for optimistic expected-current checks. Seeded empty. No code in Phase 1 changes it.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `slot` | VARCHAR(10) | no | PK |
| `publication_id` | VARCHAR(20) | yes | FK → publication_runs.publication_id |
| `pointer_version` | INTEGER | no | default `0` |
| `updated_at_utc` | DATETIME (UTC) | yes |  |
| `updated_by` | VARCHAR(100) | yes |  |

Rules:

- CHECK `ck_active_publication_single_slot`: `slot = 'ACTIVE'`

## `audit_events`

Append-only, hash-chained audit trail (actor, approver, action, before/after, reason). `riskparse audit-verify` detects edits and deletions.

| Column | Type | Null | Key / default |
| --- | --- | --- | --- |
| `event_id` | INTEGER | no | PK |
| `occurred_at_utc` | DATETIME (UTC) | no |  |
| `actor` | VARCHAR(100) | no |  |
| `approver` | VARCHAR(100) | yes |  |
| `action` | VARCHAR(60) | no |  |
| `entity_type` | VARCHAR(40) | no |  |
| `entity_id` | VARCHAR(120) | no |  |
| `document_id` | VARCHAR(40) | yes | FK → source_documents.document_id |
| `publication_id` | VARCHAR(20) | yes | FK → publication_runs.publication_id |
| `reason` | TEXT | yes |  |
| `before_json` | TEXT | yes |  |
| `after_json` | TEXT | yes |  |
| `prev_event_hash` | VARCHAR(64) | yes |  |
| `event_hash` | VARCHAR(64) | no |  |

Rules:

- CHECK `ck_audit_events_approver_sod`: `approver IS NULL OR approver <> actor`
- UNIQUE (event_hash)
