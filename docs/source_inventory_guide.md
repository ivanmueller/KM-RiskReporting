# Source inventory guide (Risk analyst) — October 2026

Template: `inventory/templates/October_2026_Source_Inventory_TEMPLATE.xlsx`
(regenerate with `riskparse inventory-template --out <folder>`). Per-sheet CSV versions sit
alongside it.

**Never** save a completed inventory into this repository or e-mail it outside the controlled
environment. Completed copies belong on the L drive (`\\<server>\<share>\Risk Reporting\05_Config`
or another IT-approved restricted location). The repository's `.gitignore` blocks
`inventory/completed/` and all spreadsheet types outside `inventory/templates/`.

## Sheets and what "complete" means

| Sheet | One row per | Complete when |
| --- | --- | --- |
| `Source_Coverage` | Independent trade-record source | Every trader (all 2–3), Egistix, the October contract folders and the evergreen registry are listed with an owner and Egistix coverage; Risk confirmed each row |
| `Contract_Documents` | File (contract, amendment, cancellation, email, scan) | Every known October document **and** earlier documents still affecting October has a row with `inventory_status`; nothing silently skipped |
| `Egistix_Exports` | Dated Egistix export | Export timestamp with timezone, filters, row count, hash, products **not** covered |
| `Trader_Sheets` | Trader sheet copy | One per in-scope trader, with as-of timestamp and hash |
| `Amendments_Cancellations` | Change document | Parent contract identified; `supersession_scope` stated (do not assume FULL) |
| `Evergreen_Agreements` | Active evergreen agreement | Stated "value" quoted **exactly** with clause location; `interpretation_status` remains `UNRESOLVED` unless Risk **and** Finance (two different people) approved |
| `Trade_Crosswalk` | October trade from any source | Each of the ~35 trades shows where it appears (trader sheet / Egistix / confirmation); hand-keyed values verified by a different person |
| `Open_Questions` | Question / blocker | Owner, due date, status |

## Procedure

0. **If the documents arrived as a ZIP**, keep the ZIP unchanged as evidence and run the safe
   intake first. It hashes the ZIP, extracts it into a new empty folder and writes a manifest
   in `Contract_Documents` format, with every member linked to the ZIP row `INV-ZIP-0001`.
   Members it refuses (unsafe paths, links, encrypted) appear as `EXCEPTED` with the reason:

   ```powershell
   riskparse intake-zip "\\server\share\Risk Reporting\00_Intake\Oct2026_trades.zip" --extract-to "\\server\share\Risk Reporting\00_Intake\Oct2026_extracted" --manifest "\\server\share\Risk Reporting\05_Config\inventory\intake_2026-10.csv"
   ```

   Then continue with step 2 using the manifest instead of a scan.

1. On the controlled machine, hash each folder **without opening files**:

   ```powershell
   riskparse inventory-scan --root "\\server\share\Risk Reporting\01_Contracts\2026\2026-10 October" --out "\\server\share\Risk Reporting\05_Config\inventory\scan_contracts_2026-10.csv"
   ```

   Repeat for `02_Trade Records\2026\2026-10 October` and for the evergreen folders of prior years.
   The scan lists `DUPLICATE_CONTENT` (identical bytes) and `SAME_NAME_DIFFERENT_CONTENT`
   (revisions — never overwrite) findings.
2. Paste the scan rows into `Contract_Documents`; complete category, physical/financial,
   evergreen flag, counterparty and dates **from the document**, not from the file name or folder.
3. Fill `Source_Coverage`, `Trader_Sheets` and `Egistix_Exports` from the sources' owners.
4. Fill `Evergreen_Agreements` with exact quotations; leave interpretation `UNRESOLVED`.
5. Build `Trade_Crosswalk`; a second person fills `verified_by`.
6. Validate:

   ```powershell
   riskparse inventory-validate "\\server\share\...\October_2026_Source_Inventory.xlsx" --strict
   ```

   Exit code 0 = valid and complete, 2 = valid but required values missing, 1 = errors.
7. Risk manager signs the README sheet; the signed copy is the gate for Week 2 ingestion.

## Controlled values

See the `Lists` sheet (drop-downs are applied to every controlled column). Values mirror
`src/riskparse/vocab.py`; reconciliation statuses follow game plan §7.3.
