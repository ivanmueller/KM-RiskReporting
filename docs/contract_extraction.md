# Running riskparse on the October contracts — step by step (Windows)

**What this does:** it records the received contracts ZIP as evidence, extracts it safely, counts
and sizes the document population, and pulls the raw text out of every `.pdf`, `.xlsx` and `.msg`
(email attachments included) into a local database. Every piece of text keeps its exact location.

**What it does not do yet:** turn any text into a price, volume, side or date. That is the next
step, built from the profile you send back.

Everything runs on **your controlled Windows machine**. The contracts never leave it. Do not
upload them, the database or the CSV outputs to GitHub or to an AI tool (blocker B-10).

All commands are PowerShell, run from the repository folder unless stated otherwise.

---

## Step 1 — Prerequisites (once)

```powershell
py -3 --version
```
You need Python **3.11 or newer** (tested on 3.13). If `py` is not found, install Python from
python.org with "Add python.exe to PATH" ticked, or ask IT.

```powershell
git --version
```

## Step 2 — Get the latest code

First time:
```powershell
git clone https://github.com/ivanmueller/KM-RiskReporting.git C:\RiskCode\KM-RiskReporting
cd C:\RiskCode\KM-RiskReporting
git checkout claude/risk-reporting-phase-1-x8u26q
```

If you already have it:
```powershell
cd C:\RiskCode\KM-RiskReporting
git pull
```

Keep the code (`C:\RiskCode\...`) and the data (`C:\RiskWork`, step 4) in **separate** folders.

## Step 3 — Install (once, and after every `git pull` that changes `requirements\lock.txt`)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements\lock.txt
pip install -e . --no-deps
```

If `Activate.ps1` is blocked by execution policy, allow it for this window only:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
If you may not change the policy, skip activation and run every `riskparse ...` command below as
`.\.venv\Scripts\riskparse.exe ...` instead.

Open a new PowerShell window later? Run `cd C:\RiskCode\KM-RiskReporting` and
`.\.venv\Scripts\Activate.ps1` again.

## Step 4 — Check the install (synthetic data only)

```powershell
python -m pytest -q
riskparse demo-smoke --workdir C:\RiskWork\demo
```

Expect `... passed, ... skipped` with **no** `failed`; on Windows a few extra tests skip, because
some need symbolic links or Linux-only features. The demo must end with `SMOKE DEMO: PASS`. If
anything fails, stop and send the last 30 lines of output (they contain synthetic data only).

## Step 5 — Create the working folder and local settings (once)

```powershell
New-Item -ItemType Directory -Force C:\RiskWork | Out-Null
@'
[environment]
name = "local"

[database]
backend = "sqlite"
sqlite_path = "C:/RiskWork/riskparse_local.db"

[paths]
archive_root = "C:/RiskWork/archive"
work_root = "C:/RiskWork/work"
'@ | Set-Content -Encoding utf8 config\settings.local.toml
```

- `config\settings.local.toml` is git-ignored, and it is loaded automatically **when you run
  commands from the repository folder**.
- `C:\RiskWork` holds confidential data: keep it on this controlled machine (or the L drive once
  IT gives the UNC path), never inside the repository.
- Local SQLite is a provisional prototype until IT confirms the SQL platform (blocker B-01).

Check it, then create or upgrade the database:
```powershell
riskparse config-check
riskparse db-upgrade
```
`config-check` must show `"source_file": "config\\settings.local.toml"` and
`"environment": "local"`. `db-upgrade` must end with `database at revision: 0004_review_fixes`.

> **Ran an earlier version already?** `db-upgrade` brings the existing database up to date. For a
> clean record with the current rules, start fresh instead: close everything, rename the old
> folder (`Rename-Item C:\RiskWork C:\RiskWork_before_2026-11-02`, keeping it, not deleting it),
> then repeat step 5.

## Step 6 — Intake the ZIP as evidence

```powershell
riskparse intake-zip "C:\Users\ivanm\Downloads\Risk Program.zip" --extract-to "C:\RiskWork\Oct2026_extracted" --manifest "C:\RiskWork\intake_2026-10.csv" --register --by ivanm --received-at 2026-11-02T09:00-07:00
```

- Point at the **ZIP file itself**, not at a folder inside it as Windows Explorer shows it.
- `--received-at` is when the ZIP was actually received, with its offset (`-07:00` = MST). Leave
  it out if unknown. A time without an offset is refused before anything is written.
- `--extract-to` must be a new or empty folder; `--manifest` must be a new file outside it.
- Read every line printed. Each of these needs a human decision, and refused files appear in the
  manifest as `EXCEPTED` with the reason:
  - `UNSAFE_PATH`, `SYMLINK`, `ENCRYPTED`, `CORRUPT_MEMBER`: the file was not extracted;
  - `MACRO_ENABLED`: open only with macros disabled;
  - `NESTED_ARCHIVE`: a ZIP inside the ZIP, not unpacked;
  - `DUPLICATE_NAME`, `CASE_COLLISION`: two files whose names clash on Windows.

## Step 7 — Profile the contracts (counts only)

```powershell
riskparse profile-documents --root "C:\RiskWork\Oct2026_extracted\Risk Program\1. Contracts" --out "C:\RiskWork\profile_2026-10.csv"
```

The printed summary shows documents by folder class (Physical / Financial / Evergreen, a hint
from the folder name only), format and status, counting email attachments as documents. It also
shows the distinct **layouts** (`layout_key`) and how many documents share each one.

**Send Claude the printed summary only.** It contains counts, statuses and layout hashes, but
no file names, values or text. Do **not** send the CSV, which lists file names.

| Status | Meaning | What to do |
| --- | --- | --- |
| `TEXT_EXTRACTED` | All text read | — |
| `PARTIAL_TEXT` | Part not read: pages with no text layer, Excel formulas never recalculated, an email body stored only as RTF, embedded or linked attachments | Read the warning; handle that part manually |
| `NEEDS_OCR` | A scan with no text layer | Key manually from the original until an approved OCR route exists |
| `ENCRYPTED` | Password-protected | Get an unprotected copy |
| `UNSUPPORTED` | e.g. legacy `.xls` | Save as `.xlsx` and register that copy |
| `FAILED` | Corrupt, unreadable, or not what the extension says | Investigate the original |

## Step 8 — Extract text into the database

```powershell
riskparse extract --all
```
Each registered PDF, workbook and email is re-checked against its hash, then extracted. Email
attachments are saved under `C:\RiskWork\work\attachments\` and registered as their own documents,
linked to the email, then extracted in the same run. Re-running is safe.

To look at the raw text of one file (nothing is stored):
```powershell
riskparse extract-preview "C:\RiskWork\Oct2026_extracted\Risk Program\1. Contracts\Physical Contracts\<file name>.pdf"
```
Locations look like `p2:L14` (page 2, line 14), `Confirm!B7` (sheet and cell), `header:Subject`,
`body:L4` and `attachment:1`.

## Step 9 — Verify and record the anchor

```powershell
riskparse audit-verify
```
It must print `audit chain ok=True` and no `ARCHIVE PROBLEM`. It also prints an **anchor** line:
```
anchor: --expect-count 42 --expect-head 9f3c...  (record this outside the DB)
```
Save that line **outside** `C:\RiskWork`, for example in an email to Risk or in the inventory
sign-off. Next time, run `riskparse audit-verify --expect-count 42 --expect-head 9f3c...` (your own
values): it fails if any recorded event has been deleted since.

## Step 10 — Fill in the source inventory

1. Copy `inventory\templates\October_2026_Source_Inventory_TEMPLATE.xlsx` to
   `C:\RiskWork\October_2026_Source_Inventory.xlsx` and work on the copy.
2. Paste the rows of `C:\RiskWork\intake_2026-10.csv` into `Contract_Documents`. The ZIP is
   `INV-ZIP-0001`.
3. Classify each document **from its content**, using `extract-preview` and the original, not
   from its folder or file name. Type values; do not use formulas.
4. Fill `Source_Coverage`, `Trader_Sheets`, `Egistix_Exports`, `Evergreen_Agreements` and
   `Trade_Crosswalk` (see `docs/source_inventory_guide.md`).
5. Validate:
   ```powershell
   riskparse inventory-validate "C:\RiskWork\October_2026_Source_Inventory.xlsx" --strict
   ```
   Exit code `0` = valid and complete, `2` = valid but required values missing, `1` = errors.

---

## If something goes wrong

| Message / exit code | Meaning | Fix |
| --- | --- | --- |
| `configuration refused` (exit 3) | Config file missing, invalid or unsafe | Check `config\settings.local.toml` against step 5; run from the repository folder |
| `intake refused` (exit 4) | Output already exists or would be inside the folder being read; extraction folder not empty; bad or corrupt ZIP; `--received-at` without offset | Use a new output name or folder, or fix the argument. Nothing was written |
| `evidence check failed` (exit 5) | A stored file no longer matches its registered hash | Do not continue; find out who changed the file |
| `audit chain ok=False` or `ARCHIVE PROBLEM` (exit 1) | Audit log or evidence altered or missing | Stop and report it |
| Path-too-long errors | Windows 260-character limit | Use short folders (`C:\RiskWork`) or ask IT to enable long paths |
| `riskparse` not recognised | Virtual environment not active | `.\.venv\Scripts\Activate.ps1`, or use `.\.venv\Scripts\riskparse.exe` |

## Safety notes

- No file is opened in Excel, Outlook or Acrobat. No macro, script, form or link is run, and Excel
  formulas are not recalculated: the value Excel last saved is used, and formulas with none are
  flagged.
- Hidden sheets are extracted and flagged. Email bodies are kept even when the email has an
  attachment.
- Outputs are never written inside a folder being read, and never overwrite an existing file.
- `.msg` files are read with `olefile` (BSD) and PDFs with `pypdf` (BSD). Both still need IT
  package approval (B-08).
