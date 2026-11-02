# Step 1 quick start: review the October contracts

One command turns the contracts ZIP into **one Excel workbook** listing every document and its
readable text. No settings file, no database.

## Once: install (PowerShell)

```powershell
git clone https://github.com/ivanmueller/KM-RiskReporting.git C:\RiskCode\KM-RiskReporting
cd C:\RiskCode\KM-RiskReporting
git checkout claude/risk-reporting-phase-1-x8u26q
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements\lock.txt
.\.venv\Scripts\pip install -e . --no-deps
```

(Python 3.11 or newer. Already cloned? Run `git pull` in that folder instead of the first three lines.)

## Each time: run it

```powershell
cd C:\RiskCode\KM-RiskReporting
.\.venv\Scripts\riskparse review "C:\Users\ivanm\Downloads\Risk Program.zip"
```

It prints a short summary and the path of `review.xlsx`. The workbook goes in a new
`Risk Program_review_<date-time>` folder next to the ZIP; add `--out C:\RiskWork\review1` to
put it somewhere else. The ZIP itself is never changed.

## What's in the workbook

| Sheet | What it shows |
| --- | --- |
| **Summary** | How many documents per folder (Physical / Financial / Evergreen), file type and status, plus groups of documents that share a layout |
| **Documents** | One row per document (email attachments get their own row, linked to the email): status, pages, warnings, SHA-256 |
| **Text** | Every readable line or cell, with where it came from (`p2:L14` = page 2, line 14; `Confirm!B7` = sheet and cell; `body:L4` = email body line 4). Use the filter to look at one document |
| **Fields** | **Proposed** trade fields (trade date, deal reference, buyer, seller, price, quantity, unit, delivery point, start/end dates...) found as "label → value" pairs, each with its location. Values are copied exactly as written. The last three columns are for Risk: mark each row OK or WRONG, write the correct value, and initial it |
| **Labels** | Every label found, per layout group, with how many documents use it and what it maps to. **No values** |
| **Issues** | What needs a human look: scanned PDFs (`NEEDS_OCR`), password-protected files, embedded emails, refused or corrupt ZIP files, hidden sheets |

## What to send back

Only the **summary printed in the window**: counts, layout groups and how many documents
each field was found in. It contains no names, prices or text. Keep the workbook confidential;
it holds the contract text.

To improve the mapping, the label wording helps most. Print it in the window:

```powershell
.\.venv\Scripts\riskparse review contracts --labels
```

It lists unmapped label wording such as "Sold To" or "Nomination Deadline" with document counts,
and no values. **Read it before sharing:** a "candidate" label in Excel is just text next to a
number, so it could be a company or person name. Delete those lines, then paste the rest.

A legacy `.xls` file is reported as `UNSUPPORTED`: open it in Excel, save it as `.xlsx` in
the same folder, and run the review again.

The next step uses those counts to build parsers for the most common layouts, turning the text
into trade fields that Risk checks against each source document.

---

Everything else in this repository (database, registration, audit trail, reconciliation) is
groundwork for later stages and is **not needed for step 1**. The full controlled workflow is in
`docs/contract_extraction.md`.
