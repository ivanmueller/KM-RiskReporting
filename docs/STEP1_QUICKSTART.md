# Step 1 quick start: confirmations in, raw-data-format out

One command reads every confirmation in the contracts folder or ZIP and writes the trades in
the firm's `raw-data-format`, **one row per trade**, with a checks sheet for Risk. No settings
file, no database.

## Once: install (PowerShell)

```powershell
git clone https://github.com/ivanmueller/KM-RiskReporting.git C:\RiskCode\KM-RiskReporting
cd C:\RiskCode\KM-RiskReporting
git checkout claude/risk-reporting-phase-1-x8u26q
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements\lock.txt
.\.venv\Scripts\pip install -e . --no-deps
```

(Python 3.11 or newer. Already cloned? Run `git pull` in that folder instead of the first three
lines. After a `git pull` that changes `requirements\lock.txt`, run the two `pip install`
lines again.)

## Each time: run it

```powershell
cd C:\RiskCode\KM-RiskReporting
.\.venv\Scripts\riskparse review contracts --format raw-data-format.xlsx
```

- `contracts` is the folder holding `Physical Contracts`, `Financial Contracts` and
  `Evergreen Contracts`. A ZIP file works too.
- `--format` points at the firm's format file. Its first-row headers decide the output
  columns, so a changed template is followed.
- Our company is taken to be **Kingston Marketing**. Add `--us "<name>"` only if
  confirmations use a different name.
- The output goes in a new `contracts_review_<date-time>` folder next to the source (or
  `--out <folder>`). Nothing in the source is changed.

## What you get

**`raw-data.xlsx`**

| Sheet | What it shows |
| --- | --- |
| **raw-data-format** | The firm's columns, one row per trade; dates as dates, numbers as numbers |
| **checks** | Per trade: our side (BUY/SELL), source, broker or issuer, settlement currency, how the price is quoted, a swap's fixed price, location, every supporting document, anything to look at, and columns for Risk to mark the row OK or WRONG |

**`review.xlsx`** holds the same two sheets, plus:
- **evidence**: for every filled cell, the exact place in the document and the text it was read
  from;
- **documents**: which parser read each file;
- **text**: all readable text;
- **issues**.

## How each confirmation family is read

| Family | Recognised by | Notes |
| --- | --- | --- |
| Modern Commodities `Trade-Confirm-*.xlsx` | header row with Trade Number / Bid Legal Name / Offer Legal Name | one trade per row; an `UPDATED-` report replaces the earlier one, and the changes are listed |
| One Exchange `Seller Confirmation for Deal *.pdf` | "BROKERED TRADE CONFIRMATION" | the index "Calendar average … WTI" is WTI CMA; settlement currency comes from Notes |
| Marex `Canadian Crude_KM_*.pdf` | "Marex Reference" | start and end dates are the contract month |
| Marex daily summary `*.xls` | Reference / Deal Date / Buy Company / Sell Company | the same trade as a Marex PDF: merged into one row, and disagreements flagged |
| CalRock `*.msg` emails | a "Trade confirmation" block | forwarded emails work too |
| National Bank of Canada swaps (ISDA) | "COMMODITY SWAP TRANSACTION" | the fixed-price payer is the buyer; the fixed price goes on the checks sheet because it is not a differential; the confirmation names no trader or grade |

Column conventions:
- **Counterparty** is always the other party.
- **Seller** is the selling party's legal name, as written.
- **Trader** is our trader.
- **Fixed Differential** is the price differential to the index.

A document that matches none of these layouts is listed as `UNRECOGNISED` and left out of
raw-data; it is never guessed at. A new broker or layout needs a new parser: send a sample.

## What to send back

The **summary printed in the window**: trade counts per family, things to look at, and filled
cells per column. It contains no names or prices.

---

Everything else in this repository (database, registration, audit trail, reconciliation) is
groundwork for later stages and is **not needed for step 1**. The full controlled workflow is in
`docs/contract_extraction.md`.
