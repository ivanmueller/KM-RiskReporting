# KM Risk Reporting

Reads the trade confirmations (PDF, Excel and Outlook emails from Modern Commodities, One
Exchange, Marex, CalRock and the bank swap confirmations) and writes the trades into the
firm's **raw-data format**, one row per trade, with a checks sheet for Risk.

## Use it (Windows)

| | What to do |
| --- | --- |
| **Once** | Double-click **`SETUP.bat`**. It installs everything it needs for your Windows user, with no administrator rights and no Python needed beforehand. Run it again at any time to update. |
| **Each time** | Put the confirmations in the **`contracts`** folder and double-click **`RUN.bat`**. The results open when it finishes. |

**New computer:** download `SETUP.bat` from
[GitHub](https://github.com/ivanmueller/KM-RiskReporting/blob/claude/risk-reporting-phase-1-x8u26q/SETUP.bat)
(the "Download raw file" button), then double-click it. It installs the program into
`C:\RiskCode\KM-RiskReporting`. If Windows says "Windows protected your PC", click
**More info → Run anyway**.

## The folder

```text
C:\RiskCode\KM-RiskReporting\
  SETUP.bat                  install / update (double-click)
  RUN.bat                    process the contracts (double-click)
  contracts\                 YOU put the confirmations here     (never uploaded)
  contracts-data\            results, one new folder per run    (never uploaded)
    contracts_2026-11-02_143012\
      raw-data.xlsx          the trades in the firm's format + checks sheet
      review.xlsx            the same + evidence, names, documents, text, issues
  config\
    raw-data-format.xlsx     the output columns - edit the headers to change them
    counterparty_map.csv     company master names - add a row for each new spelling
  docs\                      guides (STEP1_QUICKSTART.md explains the output)
  src\, tests\, ...          the program itself - nothing to change here
```

Only `contracts\`, `contracts-data\` and the two files in `config\` are yours to use. Contracts
and results never leave the computer: git is set to ignore them.

More detail: [`docs/STEP1_QUICKSTART.md`](docs/STEP1_QUICKSTART.md) (what each column and sheet
means, the counterparty map, troubleshooting). For developers:
[`docs/DEVELOPER.md`](docs/DEVELOPER.md).
