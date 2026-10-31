# Contract intake, profiling and text extraction — runbook

**What this part of the system does:** takes the received contracts ZIP, records it as
evidence, extracts every file safely and measures the document population. It then pulls
the raw text out of every `.pdf`, `.xlsx` and `.msg` (including email attachments), with an
exact location for every piece of text, and stores it in SQL.

**What it does not do (yet):** decide what any text *means*. No price, volume, side, date or
counterparty is read into trade terms. That is the next step: counterparty-specific templates
built from the profile results, every value citing a segment location as evidence, and Risk
verifying each one.

All commands run on **your controlled Windows machine**. The contracts never leave it, and
must not be uploaded to GitHub or to an AI tool without security approval (blocker B-10).

## 1. One-time setup (PowerShell)

```powershell
cd <your KM-RiskReporting checkout>
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements\lock.txt
pip install -e . --no-deps
```

Create `config\settings.local.toml`. It is git-ignored and loaded automatically. Use a short
working folder: Windows path-length limits can break very deep extractions.

```toml
[environment]
name = "local"

[database]
backend = "sqlite"            # provisional local prototype until IT confirms the SQL engine
sqlite_path = "C:/RiskWork/riskparse_local.db"

[paths]
archive_root = "C:/RiskWork/archive"     # content-addressed evidence copies
work_root = "C:/RiskWork/work"           # email attachments written out before registration
```

`C:\RiskWork` holds confidential data. Keep it on the controlled machine (or the L drive once
IT gives the UNC path), never inside the repository folder.

```powershell
riskparse config-check
riskparse db-upgrade
```

## 2. Intake the ZIP as evidence

Point at the ZIP file itself (not at a folder inside it, as Windows Explorer shows it):

```powershell
riskparse intake-zip "C:\Users\ivanm\Downloads\Risk Program.zip" --extract-to "C:\RiskWork\Oct2026_extracted" --manifest "C:\RiskWork\intake_2026-10.csv" --register --by ivanm --received-at 2026-11-02T09:00-07:00
```

- Replace `--received-at` with when the ZIP was actually received, including the offset
  (`-07:00` is Mountain Standard Time), or leave it out if unknown.
- The ZIP is hashed and registered unchanged. Every file is extracted read-only and registered
  with the ZIP as its parent.
- Read every line it prints. `UNSAFE_PATH`, `ENCRYPTED`, `MACRO_ENABLED`, `NESTED_ARCHIVE`,
  `DUPLICATE_NAME` and `CASE_COLLISION` each need a human decision. Refused files are in the
  manifest as `EXCEPTED`.

## 3. Profile the contracts (counts only, no content)

```powershell
riskparse profile-documents --root "C:\RiskWork\Oct2026_extracted\Risk Program\1. Contracts" --out "C:\RiskWork\profile_2026-10.csv"
```

The printed summary gives counts of:
- documents by **folder class** (Physical / Financial / Evergreen, taken from the folder name
  as a hint only), format and status;
- email attachments, counted as documents in their own right;
- distinct **layouts** (`layout_key`, a fingerprint of field labels and not values) and how
  many documents share each one;
- documents that need OCR or have only partial text.

**What to send back to Claude:** the printed summary only (counts, statuses and layout
hashes). It contains no file names, values or text. Do not send the CSV, which lists file names.
The counts decide which layouts get a parser first.

| Status | Meaning | What to do |
| --- | --- | --- |
| `TEXT_EXTRACTED` | All text read | — |
| `PARTIAL_TEXT` | Some content not read: pages with no text layer, Excel formulas never recalculated, an email body stored only as RTF, embedded or linked attachments | Read the warning; handle that part manually |
| `NEEDS_OCR` | No text layer at all (a scan) | Manual keying from the original until an approved OCR route exists |
| `ENCRYPTED` | Password-protected | Get an unprotected copy from the sender |
| `UNSUPPORTED` | e.g. legacy `.xls` | Save as `.xlsx`, then register that copy as a new document |
| `FAILED` | Corrupt or not what the extension says (e.g. a renamed `.eml`) | Investigate the original |

## 4. Extract text into SQL

```powershell
riskparse extract --all
riskparse audit-verify
```

- Each registered PDF, workbook and email is re-hashed against its registration, then
  extracted. Altered evidence is refused (exit code 5).
- Email attachments are written to `C:\RiskWork\work\attachments\<email document id>\` and
  registered as child documents, then extracted in the same run.
- Re-running is safe: documents already extracted at the current extractor version are skipped.

To look at what was extracted from one file (nothing is stored):

```powershell
riskparse extract-preview "C:\RiskWork\Oct2026_extracted\Risk Program\1. Contracts\Physical Contracts\<file>.pdf"
```

Locations look like `p2:L14` (page 2, line 14), `Confirm!B7` (sheet and cell), `header:Subject`,
`body:L4` and `attachment:1`. Later templates cite these as evidence for every value.

## 5. Then fill the inventory

Paste the intake manifest rows into `Contract_Documents` in the inventory workbook
(`docs/source_inventory_guide.md`). Classify each document **from its content**, using
`extract-preview` and the original, not from its folder or file name.

## Safety notes

- No file is opened in Excel, Outlook or Acrobat. No macro, script, form or link is executed,
  and Excel formulas are not recalculated. The extractor reads only the value Excel last saved,
  and flags formulas that have none.
- Hidden sheets are extracted and flagged; they are never ignored.
- An email body is kept even when the email has an attachment: which text is legally operative
  is a Risk decision.
- `.msg` files are read with `olefile` (BSD). The GPL-licensed `extract-msg` is deliberately
  not used. PDFs are read with `pypdf` (BSD). Both still need IT package approval (B-08).
