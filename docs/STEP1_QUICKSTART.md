# Step 1 quick start: confirmations in, raw-data-format out

One double-click reads every confirmation in the contracts folder, writes the trades in the
firm's `raw-data-format` (**one row per trade**, with a checks sheet for Risk), checks them
against the **master trade sheet**, sorts out the **bank swaps**, and shows a **risk dashboard**
in the browser. No database.

## Once: install

Double-click **`SETUP.bat`** (in `C:\RiskCode\KM-RiskReporting`, or downloaded from GitHub on
a new computer: see the README). It needs no administrator rights and nothing installed
beforehand:

1. finds the program folder. Run from inside the program folder, it updates that copy. Run
   from anywhere else (for example Downloads), it asks where to install: press Enter for
   `C:\RiskCode\KM-RiskReporting` or type another folder, then confirm with Y. Next time it
   suggests the folder you chose. The folder must be on this computer: a network drive
   (such as L:) is refused, because building the environment there is very slow or hangs.
   It then asks for three folders, which can all be on the network (L:):
   - **contracts**: where the confirmations are;
   - **results**: each run makes a new dated folder in it, for example
     `L:\...\Risk Directory\2. Raw Data`;
   - **monthly trade sheets**: the folder holding the `<Month> Trading <Year>` workbooks,
     for example `L:\...\Risk Directory\4. Trade Sheet` (type `none` to skip the check).

   It shows all three and asks Y to confirm. On later runs it shows them and asks Y to keep
   them or N to change them. Those are the last questions: the rest runs by itself;
2. uses Git if installed, otherwise downloads a private copy for your user;
3. downloads the latest version of the program;
4. uses Python 3.11 – 3.13 if installed, otherwise installs Python for your user only
   (the installer's signature is checked first);
5. creates the program environment (`.venv`) and installs the packages;
6. checks that the program starts.

**Run `SETUP.bat` again whenever you want the latest version.** It is safe to repeat.

## Each time: run it

1. Put the confirmations in the contracts folder you chose in SETUP: the
   `Physical Contracts`, `Financial Contracts` and `Evergreen Contracts` folders, as they are.
2. Double-click **`RUN.bat`** in the program folder.

The window prints a summary. A new folder `<contracts folder name>_<date>_<time>` appears in
the results folder with `raw-data.xlsx`, `review.xlsx` and `dashboard.html`; it opens, and the
**dashboard** opens in your browser at `http://127.0.0.1:8765/`. The dashboard stays available
while the RUN window is open; close the window when you are done (the page itself stays in
the results folder and opens with a double-click). Earlier runs are kept, never overwritten.
Nothing in the contracts folder or the trade sheet is changed. To change the folders, run
`SETUP.bat` again and press N.

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
- `--results <folder>`: the folder that collects every run (default: `contracts-data` next
  to the contracts).
- `--trade-sheet <folder or file>`: the master trade sheet to check against.
- `--out <folder>`: write this run into exactly this new folder.
- `riskparse dashboard <results folder>`: show the newest run's dashboard again.

</details>

## What you get

**`raw-data.xlsx`**

| Sheet | What it shows |
| --- | --- |
| **raw-data-format** | The firm's columns, one row per trade; dates as dates, numbers as numbers |
| **checks** | Per trade: our side (BUY/SELL), source, broker or issuer, settlement currency, how the price is quoted, the fixed price, location, every supporting document, anything to look at, the **trade sheet** result (e.g. `MATCHED 11824 (Nov CTS!row 50)`), and columns for Risk to mark the row OK or WRONG |

**`review.xlsx`** holds the same two sheets, plus:
- **evidence**: for every filled cell, the exact place in the document and the text it was read
  from;
- **monthly trade sheet**: every confirmation against its Trading tab, and every deal on it
  with no confirmation (see below);
- **financial**: the bank swaps, each marked ROLL (with its partner) or FORWARD HEDGE;
- **names**: every company spelling found, the master name it became, and `NOT IN MAP` for
  any spelling the counterparty map does not have yet;
- **documents**: which parser read each file;
- **text**: all readable text;
- **issues**.

**`dashboard.html`**: the run as a page to read, in the style of the risk report. On the
left is a narrow **checks** menu: a green checkmark when every check is OK, a red flag with
the number of failing checks when not. Click it to slide out each check, what failed and a
**Show me** link to the rows concerned. Then key figures, confirmed and trade sheet positions by delivery
month and grade (m3 and bbl, with weighted differentials), the reconciliation (filter by
status), the bank swaps, counterparties and everything that needs attention. It needs no
internet.

## Checking against the monthly trade sheet

The confirmations are checked against the desk's **monthly trade sheet**, its **Trading**
tab. The result is the `monthly trade sheet` sheet in `review.xlsx`, the Trade sheet column
of the checks sheet (`Monthly: MATCHED 11836 (October Trading 2026: Trading!row 83)`) and
the reconciliation in the dashboard. The workbooks are only read, never changed.

Each `<Month> Trading <Year>` workbook (`October Trading 2026.xlsm`)
holds one **delivery** month; only its **Trading** tab is read, down to the repeated header
row (the "what if" block below is not read). RUN.bat reads the **current month's** workbook,
and also the workbook of every delivery month in the confirmations when the folder has it
(October trades for November barrels are in `November Trading 2026`). A month the folder does
not have yet is listed as "not in the folder yet", and its confirmations are
`MONTH NOT ON THIS SHEET`, not breaks. (A folder holding a single workbook with any other
name is used as it is.)

Columns are found by their header text (`Counter Party`, `Buy/Sale`, `Volume`, `Grade`,
`Inj Month`, plus `Date`, `Diff'l`, `Broker / Direct`, `Internal Contract`, ...), in any
column order.

**Physical deals** (Modern Commodities, One Exchange, Marex, CalRock) are on both. A
confirmation and a trade sheet row are the same deal when the **counterparty, side, grade and
delivery month** agree; the **differential, trade date and broker** then decide between
candidates. Short names on the trade sheet (BP, CIOL, WCP, ...) are in the counterparty map.

| Status | Meaning |
| --- | --- |
| `MATCHED` | Everything agrees |
| `MATCHED - CHECK DIFFERENCES` | Same deal, but a differential, date, broker, currency, trader or volume differs: listed |
| `NOT ON TRADE SHEET` | A confirmation the desk has not recorded |
| `NO CONFIRMATION` | A trade sheet deal dated in the confirmations' date range with no confirmation (`(direct deal)` when it was not brokered) |
| `OUTSIDE CONFIRMATION PERIOD` | A trade sheet deal dated outside that range: listed, not flagged |
| `MONTH NOT ON THIS SHEET` | The sheet holds no rows for that delivery month (e.g. next month's workbook is not in the folder yet) |

Standing agreements (Evergreen / Term rows) and internal rows (KM, opening inventory) are
counted, not flagged.

**Volumes:** buys agree exactly. Sales at Cromer are often lower on the trade sheet by the
same percentage for every sale to the same counterparty in a month (for example 91% of the
confirmed volume): the trade sheet holds the apportioned volume. A consistent percentage is a note; a
one-off or a higher trade sheet volume is a difference to check. *(Please confirm this reading
with the desk.)*

**Bank swaps** (National Bank of Canada, ISDA) are not on the trade sheet by design:
- **ROLL**: two swaps on the same day for the same volume, one buying and one selling
  consecutive months: four legs (a fixed and a floating leg each), moving price exposure from
  one month to the next. The financial sheet shows the pair and the spread between the two
  fixed prices.
- **FORWARD HEDGE**: a swap three or more months ahead of its trade date, e.g. fixing the
  price of barrels bought at a fixed price from a producer.
- **SWAP**: anything else: check what it hedges.

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
| SETUP sits at "[5/6] creating the program environment" for 15+ minutes | It was installed on a network drive. Close it, end `python.exe` in Task Manager, delete that folder, and run `SETUP.bat` again with a folder on C:. |
| "The program is not set up yet" | Run `SETUP.bat` first. |
| "The contracts folder cannot be reached" | It is on L: and the network is not connected (VPN?). Connect and run again. |
| "Trade sheet: NOT READ" in the summary | The trade sheet folder has no workbook or more than one (an open copy is fine), or the workbook is damaged. The rest of the run is complete. |
| The dashboard did not open | Copy the `Dashboard: http://127.0.0.1:...` address from the window into the browser, or double-click `dashboard.html` in the results folder. |
| A column you expected is missing | Check the summary: its first line names the build, and the `Format:` line names the format file used. Run `SETUP.bat` to update. |
| A company is `NOT IN MAP` | Add it to `config\counterparty_map.csv` and run again. |

## What to send back

The **summary printed in the window**: trade counts per family, things to look at, and filled
cells per column. It contains no names or prices.

---

Everything else in this repository (database, registration, audit trail, reconciliation) is
groundwork for later stages and is **not needed for step 1**. The full controlled workflow is in
`docs/contract_extraction.md`.
