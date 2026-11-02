# Step 1 quick start: confirmations in, raw-data-format out

One command reads every confirmation in the contracts folder or ZIP and writes the trades in
the firm's `raw-data-format`, **one row per trade**, with a checks sheet for Risk. No settings
file, no database.

## Once: install

Double-click **`SETUP.bat`** (in `C:\RiskCode\KM-RiskReporting`, or downloaded from GitHub on
a new computer: see the README). It needs no administrator rights and nothing installed
beforehand:

1. finds the program folder (or downloads the program into `C:\RiskCode\KM-RiskReporting`);
2. uses Git if installed, otherwise downloads a private copy for your user;
3. downloads the latest version of the program;
4. uses Python 3.11 – 3.13 if installed, otherwise installs Python for your user only
   (the installer's signature is checked first);
5. creates the program environment (`.venv`) and installs the packages;
6. creates the `contracts` folder and checks that the program starts.

**Run `SETUP.bat` again whenever you want the latest version.** It is safe to repeat.

## Each time: run it

1. Put the confirmations in **`contracts`**: the `Physical Contracts`, `Financial Contracts`
   and `Evergreen Contracts` folders, as they are.
2. Double-click **`RUN.bat`**.

The window prints a summary, and the new results folder opens:
`contracts-data\contracts_<date>_<time>\` with `raw-data.xlsx` and `review.xlsx`. Earlier
runs are kept, never overwritten. Nothing in `contracts` is changed, and neither folder is ever
committed to git.

- The output columns come from **`config\raw-data-format.xlsx`**: its first-row headers, in
  order. Edit the headers there to change the columns. Fixed Price and Currency are added if a
  format has Fixed Differential without them.
- Company names go through **`config\counterparty_map.csv`** (below).
- Our company is taken to be **Kingston Marketing**.

<details><summary>The same from PowerShell, with options</summary>

```powershell
cd C:\RiskCode\KM-RiskReporting
.\.venv\Scripts\riskparse review contracts
```

- A ZIP file works instead of the `contracts` folder.
- `--format <file>`: use another format file instead of `config\raw-data-format.xlsx`.
- `--names <file>` / `--no-names`: another counterparty map, or none.
- `--us "<name>"`: if confirmations name our company differently.
- `--out <folder>`: write the results somewhere else.

</details>

## What you get

**`raw-data.xlsx`**

| Sheet | What it shows |
| --- | --- |
| **raw-data-format** | The firm's columns, one row per trade; dates as dates, numbers as numbers |
| **checks** | Per trade: our side (BUY/SELL), source, broker or issuer, settlement currency, how the price is quoted, the fixed price, location, every supporting document, anything to look at, and columns for Risk to mark the row OK or WRONG |

**`review.xlsx`** holds the same two sheets, plus:
- **evidence**: for every filled cell, the exact place in the document and the text it was read
  from;
- **names**: every company spelling found, the master name it became, and `NOT IN MAP` for
  any spelling the counterparty map does not have yet;
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
| National Bank of Canada swaps (ISDA) | "COMMODITY SWAP TRANSACTION" | the fixed-price payer is the buyer; the swap price goes in **Fixed Price**, never in Fixed Differential; the confirmation names no trader or grade |

Column conventions:
- **Counterparty** is always the other party.
- **Seller** is the selling party's legal name, as written.
- **Trader** is our trader.
- **Fixed Differential** is the price differential to the index (physical trades priced as
  index + differential, e.g. WTI CMA -6.10).
- **Fixed Price** is a flat (outright) price: a swap's fixed price, or a physical price on a
  confirmation that names no index. A row has one or the other, never both.
- **Currency** is the currency the price is **quoted** in, so you know what the Fixed
  Differential or Fixed Price number means. It is not always the settlement currency: a WTI
  CMA differential of -6.10 is USD/bbl even when the trade settles in CAD (14 October trades
  do). Modern Commodities reports state only the settlement currency, so their WTI CMA
  differentials are taken as USD/bbl by market convention (the evidence sheet says so). The
  settlement currency is on the checks sheet; add a `Settlement Currency` column to your
  format file if you want it in raw-data too.
- If your format file has no Fixed Price or Currency column, the tool adds them after Fixed
  Differential and says so in the summary.

- A swap's **Contract #** is the transaction number: the second number of the bank's reference
  ("167034481 / 181749496" gives 181749496). The full reference stays on the evidence sheet.
- Swap **Primary Index** is kept as the bank writes it (e.g. `OIL-WTI-NYMEX`).

A document that matches none of these layouts is listed as `UNRECOGNISED` and left out of
raw-data; it is never guessed at. A new broker or layout needs a new parser: send a sample.

## Counterparty master names: `config\counterparty_map.csv`

Brokers spell the same company in different ways ("Secure" vs "SECURE", "Inc" vs "Inc.",
"&" vs "and"). The map turns every spelling into one master name in the **Counterparty** and
**Seller** columns. It is a plain CSV in the repository that you edit in Excel:

| name_as_written | master_name | notes |
| --- | --- | --- |
| Spur Petroleum Ltd | Spur Petroleum Ltd. | |

- A spelling is matched exactly first, then ignoring only case, full stops, commas,
  apostrophes, spacing and "&" vs "and".
- A spelling that is not in the map is **kept as written**, flagged on the checks sheet and
  listed as `NOT IN MAP` on the names sheet. To fix it, add a row and run again.
- One spelling cannot map to two master names; the run stops and names the line.
- Save as **CSV UTF-8** in Excel.
- It is picked up automatically. Use `--names <file>` to point at a copy kept elsewhere, or
  `--no-names` to keep every name exactly as written.
- If you edit it and an update also changes it, `SETUP.bat` says the update could not be
  applied and lists the file; your version keeps working. Tell the developer.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| "Windows protected your PC" | **More info → Run anyway** (the file came from the internet). |
| SETUP stops at Git, Python or packages | The computer is blocking a download (github.com, python.org or pypi.org). The message names it; ask IT to allow it, or to install Python 3.13 and Git for Windows, then run `SETUP.bat` again. |
| "The program is not set up yet" | Run `SETUP.bat` first. |
| A column you expected is missing | Check the summary: its first line names the build, and the `Format:` line names the format file used. Run `SETUP.bat` to update. |
| A company is `NOT IN MAP` | Add it to `config\counterparty_map.csv` and run again. |

## What to send back

The **summary printed in the window**: trade counts per family, things to look at, and filled
cells per column. It contains no names or prices.

---

Everything else in this repository (database, registration, audit trail, reconciliation) is
groundwork for later stages and is **not needed for step 1**. The full controlled workflow is in
`docs/contract_extraction.md`.
