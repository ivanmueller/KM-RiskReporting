"""Master trade sheet: reading it, checking confirmations against it, swaps, dashboard.

Synthetic data only. The layout copies the trading-schedule tabs of the risk report.
"""

import json
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook

from riskparse.cli import main
from riskparse.confirms import Trade
from riskparse.dashboard import FILE, find_dashboard, make_server
from riskparse.review import build_review, format_summary
from riskparse.sheetcheck import (
    DIFFERENCES,
    DIRECT_ICE,
    EVERGREEN,
    FORWARD_HEDGE,
    MATCHED,
    MONTH_NOT_COVERED,
    NO_CONFIRMATION,
    NOT_ON_SHEET,
    OUTSIDE_PERIOD,
    ROLL,
    SWAP,
    check_against_sheet,
    classify_financial,
)
from riskparse.tradesheet import (
    SheetTrade,
    TradeSheet,
    TradeSheetError,
    find_trade_sheet,
    load_trade_sheet,
    monthly_month,
    select_monthly,
)
from test_review_command import _zip

HEAD = ["TEML\nPaper", "Internal Contract", "Credit", "Date", "Counter Party",
        "Counterparty\nTrader", "Trader", "Buy/Sale", "Volume ", "Grade", "Currency", "Pay\nDate",
        "Location", "Inj Month", "Blend   \nMonth", "Price Basis", "Index Type", "Diff'l",
        "Trans /\nTVM", "Additional\nCost", "Broker / Direct", "Comments", "Stream"]
NOV = datetime(2026, 11, 1)


def _row(no, when, cpty, trader, side, vol, grade, cur, month, basis, diff, broker,
         stream="Stream Sell"):
    r = dict.fromkeys(HEAD)
    r.update({"Internal Contract": no, "Date": when, "Counter Party": cpty, "Trader": trader,
              "Buy/Sale": side, "Volume ": vol, "Grade": grade, "Currency": cur,
              "Location": "Cromer", "Inj Month": month, "Price Basis": basis, "Diff'l": diff,
              "Broker / Direct": broker, "Stream": stream})
    return [r[h] for h in HEAD]


def write_trade_sheet(folder, name="October Trading 2026.xlsx"):
    """Rows that pair with the synthetic confirmations in test_review_command._zip."""
    folder.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Nov CTS"
    ws.append(["Cromer trading - November"])          # a title row: headers start on row 2
    ws.append(HEAD)
    rows = [
        _row(11001, datetime(2026, 10, 2), "SYN Refining", "Libby", "Buy", 2500, "Midale", "USD",
             NOV, "Fixed", -8.75, "Modern", "Stream Buy"),                    # = 90001
        _row(11002, datetime(2026, 10, 2), "SYN Refining", "Libby", "Sell", -3640, "LSB", "USD",
             NOV, "Fixed", -8.75, "Modern"),                     # = 90002, one-off 91%
        _row(11003, datetime(2026, 10, 5), "SYN Products", "Tyler", "Sell", -5000, "LSB", "USD",
             46327, "Fixed", -6.5, "Marex"),        # = 119999001; month as an Excel serial
        _row(11004, datetime(2026, 10, 5), "SYN Resources", "Libby", "Sell", -2500, "LSB", "Can",
             NOV, "Fixed", -6.2, "OX"),             # = 2099001 but the differential differs
        _row(11005, datetime(2026, 10, 4), "SYN Other", "Libby", "Sell", -1000, "LSB", "USD",
             NOV, "Fixed", -6, "CR"),                              # no confirmation
        _row(11006, datetime(2026, 10, 3), "SYN Direct Co", "Libby", "Buy", 700, "SW", "Can",
             NOV, "Fixed", 1, "Direct", "Stream Buy"),            # no confirmation, direct
        _row(None, "Evergreen", "SYN Lease", None, "Buy", 1000, "LSB", "Can", NOV, "Index",
             None, None, "Stream Buy"),                           # standing agreement
        _row("D", datetime(2026, 10, 3), "KM-APP", "Libby", "Buy", 5000, "LSB", "USD",
             datetime(2026, 10, 1), "Fixed", -7.9, "Direct", "Opening Inventory"),  # internal
    ]
    for r in rows:
        ws.append(r)
    dec = wb.create_sheet("Dec CTS")                   # columns in another order
    order = [HEAD[i] for i in (4, 3, 1, 7, 8, 9, 10, 13, 15, 17, 20, 6)]
    dec.append(order)
    old = dict(zip(HEAD, _row(10950, datetime(2026, 9, 15), "SYN Old", "Wade", "Sell", -4000,
                              "LSB", "USD", datetime(2026, 12, 1), "Index", None, "Marex"),
                   strict=True))
    dec.append([old[h] for h in order])               # outside the confirmation period
    wb.create_sheet("Notes")["A1"] = "free text, no trade table"
    wb.save(folder / name)
    return folder


# --------------------------------------------------------------------------- reading

def test_find_trade_sheet_needs_exactly_one_workbook(tmp_path):
    with pytest.raises(TradeSheetError, match="found 0"):
        find_trade_sheet(tmp_path)
    write_trade_sheet(tmp_path)
    (tmp_path / "~$October Trading 2026.xlsx").write_bytes(b"excel lock file")
    (tmp_path / "readme.txt").write_text("not a workbook")
    assert find_trade_sheet(tmp_path).name == "October Trading 2026.xlsx"   # any name works
    write_trade_sheet(tmp_path, "Copy.xlsx")
    with pytest.raises(TradeSheetError, match="exactly one"):
        find_trade_sheet(tmp_path)
    with pytest.raises(TradeSheetError, match="not found"):
        find_trade_sheet(tmp_path / "missing")


def test_load_reads_tabs_by_header_name(tmp_path):
    sheet = load_trade_sheet(write_trade_sheet(tmp_path))
    assert sheet.sheets_read == ["Nov CTS", "Dec CTS"] and sheet.sheets_skipped == ["Notes"]
    by_no = {t.internal_contract: t for t in sheet.trades}
    t = by_no["11002"]
    assert (t.side, t.volume, t.signed_volume, t.grade, t.differential, t.broker) == (
        "SELL", Decimal("3640"), Decimal("-3640"), "LSB", Decimal("-8.75"), "Modern")
    assert t.trade_date == date(2026, 10, 2) and t.where == "Nov CTS!row 4"
    assert by_no["11003"].delivery_month == date(2026, 11, 1)          # Excel serial date
    assert by_no["11004"].currency == "CAD"                            # "Can"
    assert by_no["10950"].counterparty == "SYN Old" and by_no["10950"].sheet == "Dec CTS"
    lease = next(t for t in sheet.trades if t.counterparty == "SYN Lease")
    assert lease.trade_date is None and lease.date_text == "Evergreen"


def test_unreadable_workbook_is_refused_with_its_name(tmp_path):
    (tmp_path / "broken.xlsx").write_bytes(b"not really a workbook")
    with pytest.raises(TradeSheetError, match="broken.xlsx"):
        load_trade_sheet(tmp_path)


# --------------------------------------------------------------------------- the check

def test_review_checks_confirmations_against_the_trade_sheet(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out",
                     trade_sheet=write_trade_sheet(tmp_path / "4. Trade Sheet"))
    c = r.sheet_check
    status = {row.trade.contract_no: row for row in c.rows if row.trade}
    assert status["90001"].status == MATCHED and status["90001"].sheet.internal_contract == "11001"
    assert status["119999001"].status == MATCHED
    assert status["90002"].status == DIFFERENCES          # a one-off lower volume: check it
    assert "91.0%" in status["90002"].differences[0]
    assert status["2099001"].status == DIFFERENCES
    assert status["2099001"].differences == [
        "differential: confirmation -6.1, trade sheet -6.2"]
    assert status["82999001"].status == NOT_ON_SHEET
    sheet_only = {row.sheet.internal_contract: row.status for row in c.rows if not row.trade}
    # brokered with no confirm; Direct (ICE) and Evergreen come in from the trade sheet
    assert sheet_only == {"11005": NO_CONFIRMATION, "11006": DIRECT_ICE, "D": DIRECT_ICE,
                          "": EVERGREEN, "10950": OUTSIDE_PERIOD}
    assert (c.standing_rows, c.internal_rows) == (0, 0)
    assert c.period == (date(2026, 10, 1), date(2026, 10, 31))     # whole months
    assert [(f.trade.contract_no, f.kind) for f in r.financial] == [("181000001", FORWARD_HEDGE)]

    wb = load_workbook(r.workbook)
    assert "monthly trade sheet" in wb.sheetnames and "financial" in wb.sheetnames
    checks = {row[0]: row for row in wb["checks"].iter_rows(min_row=2, values_only=True)}
    assert checks["90001"][10] == "Monthly: MATCHED 11001 (October Trading 2026: Nov CTS!row 3)"
    assert checks["181000001"][10] == "FINANCIAL: FORWARD HEDGE"
    rows = list(wb["monthly trade sheet"].iter_rows(min_row=2, values_only=True))
    assert any(x[0] == NO_CONFIRMATION and x[2] == "11005" for x in rows)

    text = format_summary(r)
    assert "physical confirmations matched           4 (2 with differences)" in text
    assert "confirmations not on it                   1" in text
    assert "brokered deals with no broker confirm     1" in text
    assert "trade sheet only, added to raw-data       1 Evergreen/Term, 2 Direct (ICE)" in text
    assert "SYN" not in text and "-6.2" not in text            # still counts only


def test_without_a_trade_sheet_swaps_are_still_classified(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out")
    assert r.sheet_check is None and r.financial[0].kind == FORWARD_HEDGE
    assert "Trade sheets: not compared" in format_summary(r)


def test_a_bad_trade_sheet_folder_does_not_stop_the_run(tmp_path):
    folder = write_trade_sheet(tmp_path / "ts")
    write_trade_sheet(folder, "Second.xlsx")
    r = build_review(_zip(tmp_path), tmp_path / "out", trade_sheet=folder)
    assert r.sheet_check is None and "has no workbook named" in r.sheet_error
    assert r.raw_data.is_file() and r.dashboard.is_file()
    assert "Monthly trade sheet: NOT READ" in format_summary(r)
    assert any(i[1] == "TRADE_SHEET_NOT_READ" for i in r.issues)


def _trade(no, side, qty, cpty="SYN A Inc.", diff="-6", when=date(2026, 10, 1), **kw):
    t = Trade(family=kw.pop("family", "Modern Commodities (xlsx)"), source_doc="D1",
              source_file="f", contract_no=no, trade_date=when, start_date=date(2026, 11, 1),
              counterparty=cpty, side=side, quantity=Decimal(qty), unit="m3/month", grade="LSB",
              fixed_differential=Decimal(diff) if diff else None, currency="USD")
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _sheet(no, side, vol, cpty="SYN A", diff="-6", when=date(2026, 10, 1), broker="Modern"):
    return SheetTrade("Nov", 1, internal_contract=no, trade_date=when, counterparty=cpty,
                      side=side, volume=Decimal(vol), grade="LSB", currency="USD",
                      delivery_month=date(2026, 11, 1), price_basis="Fixed",
                      differential=Decimal(diff), broker=broker)


def test_consistent_reduction_on_sales_is_apportionment():
    trades = [_trade("1", "SELL", "5000"), _trade("2", "SELL", "2500", diff="-6.5"),
              _trade("3", "SELL", "2500", cpty="SYN B Inc.")]
    sheet = TradeSheet([], datetime(2026, 11, 1), [
        _sheet("11", "SELL", "4547"), _sheet("12", "SELL", "2274", diff="-6.5"),
        _sheet("13", "SELL", "2400", cpty="SYN B")])
    c = check_against_sheet(trades, sheet, None)
    by_no = {r.trade.contract_no: r for r in c.rows}
    assert by_no["1"].status == MATCHED and "apportioned" in by_no["1"].notes[0]
    assert by_no["2"].status == MATCHED
    assert by_no["3"].status == DIFFERENCES        # alone, so it cannot be told apart


def test_higher_volume_or_a_buy_mismatch_is_a_difference():
    c = check_against_sheet([_trade("1", "BUY", "5000"), _trade("2", "SELL", "2500")],
                            TradeSheet([], datetime(2026, 11, 1), [
                                _sheet("11", "BUY", "4500"), _sheet("12", "SELL", "2600")]),
                            None)
    assert {r.trade.contract_no: r.status for r in c.rows} == {"1": DIFFERENCES,
                                                               "2": DIFFERENCES}


def test_keys_must_agree_and_the_best_candidate_wins():
    t = _trade("1", "SELL", "2500", diff="-5.75", when=date(2026, 10, 8))
    sheet = TradeSheet([], datetime(2026, 11, 1), [
        _sheet("21", "SELL", "2500", diff="-5.75", when=date(2026, 10, 2), broker="CR"),
        _sheet("22", "SELL", "2500", diff="-5.75", when=date(2026, 10, 8)),     # best
        _sheet("23", "BUY", "2500", diff="-5.75", when=date(2026, 10, 8)),      # wrong side
        _sheet("24", "SELL", "2500", cpty="SYN Z", diff="-5.75", when=date(2026, 10, 8))])
    c = check_against_sheet([t], sheet, None)
    assert c.for_trade(t).sheet.internal_contract == "22"


def test_bank_swaps_rolls_and_forward_hedges():
    bank = dict(family="ISDA swap (PDF)", cpty="SYN Bank", diff=None, unit="bbl")
    a = _trade("A", "BUY", "31464", when=date(2026, 10, 8), **bank)
    b = _trade("B", "SELL", "31464", when=date(2026, 10, 8), **bank)
    a.start_date, b.start_date = date(2026, 10, 1), date(2026, 11, 1)
    a.fixed_price, b.fixed_price, a.price_unit = Decimal("91.25"), Decimal("90.63"), "USD/bbl"
    a.term, b.term = "OCT-26", "NOV-26"
    far = _trade("F", "SELL", "10000", when=date(2026, 10, 2), **bank)
    far.start_date, far.term = date(2027, 10, 1), "OCT-27"
    lone = _trade("L", "BUY", "1000", when=date(2026, 10, 2), **bank)
    lone.start_date = date(2026, 11, 1)
    rows = {f.trade.contract_no: f for f in classify_financial([a, b, far, lone])}
    assert rows["A"].kind == ROLL and rows["A"].partner is b and rows["B"].partner is a
    assert "spread 0.62 USD/bbl" in rows["A"].note and "four legs" in rows["A"].note
    assert rows["F"].kind == FORWARD_HEDGE and "12 months ahead" in rows["F"].note
    assert rows["L"].kind == SWAP


# --------------------------------------------------------------------------- dashboard

def _dashboard_data(path):
    html = path.read_text(encoding="utf-8")
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    return html, json.loads(m.group(1))


def test_dashboard_page_holds_the_results(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out",
                     trade_sheet=write_trade_sheet(tmp_path / "ts"))
    assert r.dashboard == tmp_path / "out" / FILE
    html, data = _dashboard_data(r.dashboard)
    assert "https://" not in html and "http://" not in html     # nothing loaded from outside
    src = data["sources"][0]
    assert data["kpi"]["physical"] == 5 and src["kpi"]["matched"] == 4
    assert src["kpi"]["missing"] == 1 and src["kpi"]["not_on_sheet"] == 1
    assert (data["kpi"]["sheet_direct"], data["kpi"]["sheet_evergreen"]) == (2, 1)
    assert src["files"] == ["October Trading 2026.xlsx"]
    assert src["named"] == "the monthly trade sheet"
    assert data["logo"].startswith("data:image/png;base64,")
    checks = {c["check"]: c for c in data["checks"]}
    assert not checks["Physical confirmations not on the monthly trade sheet"]["ok"]
    assert checks["Documents no parser recognised"]["ok"] is False      # the random letter
    assert {p["month"] for p in data["conf_position"]} == {"NOV-26"}
    assert data["financial"][0]["kind"] == FORWARD_HEDGE


def test_dashboard_data_cannot_break_out_of_its_script_tag(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out")
    r.trades[1].counterparty = "</script><script>alert(1)</script>"
    from riskparse.dashboard import write_dashboard
    html, data = _dashboard_data(write_dashboard(r, tmp_path))
    assert "</script><script>alert" not in html
    assert any(c["name"].startswith("</script>") for c in data["counterparties"])


def test_dashboard_server_is_local_and_serves_only_the_page_and_exports(tmp_path):
    from urllib.parse import quote
    r = build_review(_zip(tmp_path), tmp_path / "results" / "run1")
    assert find_dashboard(tmp_path / "results") == r.dashboard        # newest run
    (r.out_dir / "notes.txt").write_text("not an export")
    server, url = make_server(tmp_path / "results", port=18765)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200 and b"RISK DASHBOARD" in resp.read()
        with urllib.request.urlopen(url + quote(r.report_pdf.name)) as resp:
            assert resp.headers["Content-Type"] == "application/pdf"
            assert resp.read(5) == b"%PDF-"
        with urllib.request.urlopen(url + "raw-data.xlsx") as resp:
            assert "attachment" in resp.headers["Content-Disposition"]
        for other in ("notes.txt", "extracted", "../run1/raw-data.xlsx", "%2e%2e/x"):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(url + other)
        second, url2 = make_server(r.out_dir, port=server.server_address[1])
        assert url2 != url                                   # port taken: the next one
        second.server_close()
    finally:
        server.shutdown()
        server.server_close()


def test_cli_results_folder_trade_sheet_and_dashboard(tmp_path, capsys):
    z = _zip(tmp_path)
    results = tmp_path / "2. Raw Data"
    ts = write_trade_sheet(tmp_path / "4. Trade Sheet")
    assert main(["review", str(z), "--results", str(results), "--trade-sheet", str(ts)]) == 0
    out = capsys.readouterr().out
    runs = [p for p in results.iterdir() if p.is_dir()]
    assert (results / "Direct trade confirmations.xlsx").is_file()      # the register
    assert len(runs) == 1 and (runs[0] / "raw-data.xlsx").is_file()
    assert "Monthly trade sheet: October Trading 2026.xlsx" in out and "dashboard:" in out
    assert main(["dashboard", str(tmp_path / "nothing-here")]) == 4


# --------------------------------------------------------------------------- monthly sheets

def _wb(path, tabs):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in tabs.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)


def test_monthly_workbook_is_chosen_by_its_name(tmp_path):
    for name in ("September Trading 2026.xlsm", "October Trading 2026.xlsm",
                 "Nov Trading 2026.xlsx", "notes.xlsx"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "~$October Trading 2026.xlsm").write_bytes(b"")
    assert monthly_month(tmp_path / "Nov Trading 2026.xlsx") == date(2026, 11, 1)
    assert monthly_month(tmp_path / "notes.xlsx") is None
    paths, missing = select_monthly(tmp_path, {date(2026, 11, 1)}, today=date(2026, 10, 15))
    assert [p.name for p in paths] == ["October Trading 2026.xlsm", "Nov Trading 2026.xlsx"]
    assert missing == []
    paths, missing = select_monthly(tmp_path, {date(2026, 11, 1)}, today=date(2026, 12, 2))
    assert [p.name for p in paths] == ["Nov Trading 2026.xlsx"]
    assert missing == ["December Trading 2026"]
    with pytest.raises(TradeSheetError, match="no workbook named January Trading 2027"):
        select_monthly(tmp_path, (), today=date(2027, 1, 5))


def test_only_the_trading_tab_and_not_the_what_if_block(tmp_path):
    deal = _row(11836, datetime(2026, 10, 2), "SYN Refining", "Libby", "Sell", -2500, "LSB",
                "Can", datetime(2026, 10, 1), "Fixed", -7, "Modern")
    what_if = _row(None, None, None, None, "Buy", None, "LSB", "Can", None, "Fixed", -2,
                   "Direct", "What if  Buy")
    total = _row(None, None, None, None, "Buy", -1783.4, "LSB", None, None, "Index", None,
                 None, "Short Before What If Deals")
    after_header = _row(99999, datetime(2026, 10, 3), "SYN Late", "Libby", "Buy", 100, "LSB",
                        "Can", datetime(2026, 10, 1), "Fixed", -1, "Direct")
    _wb(tmp_path / "October Trading 2026.xlsm", {
        "Summary": [["not a trade table"]],
        "Trading": [HEAD, deal, total, [], HEAD, what_if, after_header],
        "Trading - MTR ": [HEAD, _row(5, datetime(2026, 10, 2), "SYN Mitsue", "Wade", "Buy",
                                      10, "WH", "Can", datetime(2026, 10, 1), "Index", None,
                                      "Direct")]})
    sheet = load_trade_sheet(tmp_path, monthly=True, months={date(2026, 10, 1)},
                             today=date(2026, 10, 20), label="monthly trade sheet")
    assert sheet.sheets_read == ["Trading (October Trading 2026)"]
    assert [t.internal_contract for t in sheet.trades] == ["11836"]
    assert sheet.trades[0].where == "October Trading 2026: Trading!row 2"


def test_validated_against_the_trading_tab_of_the_delivery_months_workbook(tmp_path):
    monthly = tmp_path / "4. Trade Sheet"
    monthly.mkdir()
    oct_deal = _row(11836, datetime(2026, 10, 2), "SYN Old Co", "Libby", "Sell", -2500, "LSB",
                    "Can", datetime(2026, 10, 1), "Fixed", -7, "Modern")
    _wb(monthly / "October Trading 2026.xlsm", {"Trading": [HEAD, oct_deal]})
    # only October in the folder: the November confirmations' month is not on it - no break
    r = build_review(_zip(tmp_path), tmp_path / "out1", trade_sheet=monthly)
    (check,) = r.sheet_checks
    assert {row.status for row in check.rows if row.trade} == {MONTH_NOT_COVERED}
    assert "November Trading 2026" in check.sheet.missing
    assert not any("not on the monthly" in f for t in r.trades for f in t.flags)
    assert "not in the folder yet: November Trading 2026" in format_summary(r)

    # with the November workbook (its Trading tab), the November confirmations are checked
    nov = load_workbook(write_trade_sheet(tmp_path / "src") / "October Trading 2026.xlsx")
    rows = [list(r) for r in nov["Nov CTS"].iter_rows(min_row=2, values_only=True)]
    _wb(monthly / "November Trading 2026.xlsx", {"Summary": [["totals"]], "Trading": rows})
    r = build_review(_zip(tmp_path), tmp_path / "out2", trade_sheet=monthly)
    (check,) = r.sheet_checks
    assert any(p.name == "November Trading 2026.xlsx" for p in check.sheet.paths)
    t90001 = next(t for t in r.trades if t.contract_no == "90001")
    assert check.for_trade(t90001).status == MATCHED
    assert check.for_trade(t90001).sheet.where == "November Trading 2026: Trading!row 2"
    wb = load_workbook(r.workbook)
    checks = {row[0]: row for row in wb["checks"].iter_rows(min_row=2, values_only=True)}
    assert checks["90001"][10] == "Monthly: MATCHED 11001 (November Trading 2026: Trading!row 2)"
    assert "not on the monthly trade sheet" in next(
        t for t in r.trades if t.contract_no == "82999001").flags
    html, data = _dashboard_data(r.dashboard)
    assert [s["named"] for s in data["sources"]] == ["the monthly trade sheet"]


def test_dashboard_checks_menu_lists_what_failed_and_where(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out",
                     trade_sheet=write_trade_sheet(tmp_path / "ts"))
    html, data = _dashboard_data(r.dashboard)
    assert 'id="railbtn"' in html and 'id="panel"' in html
    assert 'id="rail"' not in html and 'id="checks"' not in html
    by = {c["check"]: c for c in data["checks"]}
    not_on = by["Physical confirmations not on the monthly trade sheet"]
    assert not not_on["ok"] and not_on["items"][0].startswith("82999001 - ")
    assert not_on["jump"] == {"section": "reconsec", "source": 0,
                              "filter": "NOT ON TRADE SHEET"}
    missing = by["Brokered deals in the period without a broker confirm"]
    assert missing["value"] == 1 and missing["jump"]["filter"] == "MISSING"
    assert by["Documents no parser recognised"]["items"] == ["random letter.pdf"]
    assert by["Company names not in the counterparty map"]["ok"] in (True, False)
    assert all(c["ok"] == (c["value"] == 0) for c in data["checks"])


# --------------------------------------------------------------------------- risk report

def test_daily_risk_report_xlsx_and_pdf(tmp_path):
    from riskparse.riskreport import BRIDGE
    from riskparse.tradesheet import monthly_month
    monthly = tmp_path / "4. Trade Sheet"
    monthly.mkdir()
    nov = load_workbook(write_trade_sheet(tmp_path / "src") / "October Trading 2026.xlsx")
    rows = [list(r) for r in nov["Nov CTS"].iter_rows(min_row=2, values_only=True)]
    pricing = [["Trade Cycle:", datetime(2026, 11, 1), None, "WTI CMA:", 91.5],
               ["NOS Date:", datetime(2026, 10, 15), None, "FX:", 1.39], [], [],
               ["Grade", "Location", "COMM Price"], ["LSB", "Cromer, MB", -5.8],
               ["Midale", "Cromer, MB", -9.2]]
    curve = [["Date", "CommonGrade", "CommonLocation", "CommonPipeline", "Source", "Type",
              "Contract", "Price", "Unit", "Base"]]
    for i, (wti, lsb) in enumerate([(91.5, None), (88.0, -6.0), (85.0, -6.2)]):
        month = datetime(2026, 11 + i, 1) if i < 2 else datetime(2027, 1, 1)
        curve.append([datetime(2026, 10, 30), "WTI CMA", "Cushing, OK", None, "Calculated",
                      "Crude", month, wti, "USD/bbl", "Base"])
        curve.append([datetime(2026, 10, 30), "USD/CAD", "", None, "Refinitiv", "FX", month,
                      1.39, "USD/CAD", "Base"])
        if lsb is not None:
            for src, delta in (("Modern", 0.1), ("Marex", -0.1)):
                curve.append([datetime(2026, 10, 30), "LSB", "Cromer, MB", None, src, "Crude",
                              month, lsb + delta, "USD/bbl", "WTI CMA"])
    _wb(monthly / "November Trading 2026.xlsm",
        {"Trading": rows, "Pricing V2": pricing, "Forward Curve": curve})
    r = build_review(_zip(tmp_path), tmp_path / "out", trade_sheet=monthly)
    assert r.report_xlsx.name.startswith("Risk Report - CTT Position Summary - ")
    assert r.report_pdf.read_bytes()[:5] == b"%PDF-"
    assert monthly_month(monthly / "November Trading 2026.xlsm").month == 11

    wb = load_workbook(r.report_xlsx)
    ws = wb["CTT Position Summary"]
    cells = {c.value: c for row in ws.iter_rows() for c in row if isinstance(c.value, str)}
    assert "KINGSTON MARKETING LIMITED | RISK REPORT" in cells
    for label in BRIDGE:                             # scheduler inputs: yellow, left empty
        c = cells[label]
        inp = ws.cell(c.row, c.column + 1)
        assert inp.value is None and inp.fill.fgColor.rgb.endswith("FFF2CC")
    wti = next(c for v, c in cells.items() if v.startswith("WTICMA (Nov-26 to Dec-26)"))
    assert ws.cell(wti.row, 3).value == pytest.approx(91.5 * 1.39, abs=1e-3)
    assert ws.cell(wti.row, 4).value == pytest.approx(88.0 * 1.39, abs=1e-3)
    assert ws.cell(wti.row, 6).value.startswith("=IF(")         # roll = formula
    lsb = next(c for v, c in cells.items() if v.startswith("LSB COMM (Nov-26 to Dec-26)"))
    assert ws.cell(lsb.row, 3).value == pytest.approx(-5.8 * 1.39, abs=1e-3)  # COMM index
    assert ws.cell(lsb.row, 4).value == pytest.approx(-6.0 * 1.39, abs=1e-3)  # broker avg
    book = cells["Stream Buy"]
    assert ws.cell(book.row, 3 + 1).value == 2500                  # Midale buy 11001
    assert ws.print_area and ws.page_setup.fitToWidth == 1

    from pypdf import PdfReader
    text = "".join(p.extract_text() for p in PdfReader(r.report_pdf).pages)
    assert "CTT Position Summary" in text and "WTICMA" in text
    assert "scheduler" in text.lower()
    html, data = _dashboard_data(r.dashboard)
    assert [e["label"] for e in data["exports"]][:2] == ["Daily risk report (PDF)",
                                                        "Daily risk report (Excel)"]
    assert 'id="exportbtn"' in html
    assert "railbtn.className =" not in html       # would drop the floating-button style


def test_risk_report_without_a_trade_sheet_still_writes(tmp_path):
    r = build_review(_zip(tmp_path), tmp_path / "out")
    assert r.report_pdf.is_file() and r.report_xlsx.is_file()
    from pypdf import PdfReader
    text = "".join(p.extract_text() for p in PdfReader(r.report_pdf).pages)
    assert "No prices" in text


# --------------------------------------------------------------------------- source of truth

def test_evergreen_and_direct_rows_come_into_raw_data_with_their_location(tmp_path):
    from riskparse.rawformat import DEFAULT_HEADERS
    r = build_review(_zip(tmp_path), tmp_path / "out",
                     trade_sheet=write_trade_sheet(tmp_path / "ts"))
    assert len(r.trades) == 6 and len(r.sheet_trades) == 3 and len(r.book) == 9
    direct = next(t for t in r.sheet_trades if t.contract_no == "11006")
    assert direct.family == "Trade sheet - Direct (ICE)"
    assert (direct.side, direct.quantity, direct.grade, direct.location) == (
        "BUY", Decimal("700"), "SW", "Cromer")
    assert direct.counterparty == "SYN Direct Co" and direct.seller == "SYN Direct Co"
    assert (direct.primary_index, direct.fixed_differential, direct.term) == (
        "WTI CMA", Decimal("1"), "NOV-26")
    assert direct.evidence["quantity"].startswith("October Trading 2026: Nov CTS!row ")
    lease = next(t for t in r.sheet_trades if t.family == "Trade sheet - Evergreen")
    assert lease.trade_date is None and lease.secondary_index == "index"

    # a matched confirmation takes the trade sheet's Location (column N)
    t90001 = next(t for t in r.trades if t.contract_no == "90001")
    assert t90001.location == "Cromer" and "trade sheet Location" in t90001.evidence["location"]

    wb = load_workbook(r.raw_data)
    rows = list(wb["raw-data-format"].iter_rows(values_only=True))
    assert list(rows[0]) == DEFAULT_HEADERS and DEFAULT_HEADERS[-2:] == ["Location", "Source"]
    by_no = {row[0]: dict(zip(DEFAULT_HEADERS, row, strict=True)) for row in rows[1:]}
    assert by_no["11006"]["Source"] == "Trade sheet - Direct (ICE)"
    assert by_no["11006"]["Location"] == "Cromer" and by_no["11006"]["Quantity"] == 700
    assert by_no["90001"]["Source"] == "Confirmation - Modern Commodities"
    checks = {row[0]: row for row in wb["checks"].iter_rows(min_row=2, values_only=True)}
    assert checks["11006"][10].startswith("TRADE SHEET ONLY - Direct (ICE)")
    assert checks["11006"][10].endswith("Human confirmation: AWAITING CONFIRMATION")
    assert not any("not on the monthly" in f for t in r.sheet_trades for f in t.flags)

    html, data = _dashboard_data(r.dashboard)
    assert {x["type"] for x in data["sheet_only"]} == {"Direct (ICE)", "Evergreen"}
    assert any(x["location"] == "Cromer" for x in data["locations"])
    assert 'id="sheetonly"' in html and 'id="loctable"' in html


def test_old_format_gets_location_and_source_added(tmp_path):
    from riskparse.rawformat import DEFAULT_HEADERS
    wb = Workbook()
    wb.active.append(DEFAULT_HEADERS[:-4])                # before Fixed Price ... Source
    wb.save(tmp_path / "old.xlsx")
    r = build_review(_zip(tmp_path), tmp_path / "out", format_path=tmp_path / "old.xlsx")
    assert r.format_added == ["Fixed Price", "Currency", "Location", "Source"]


def test_unmatched_confirmation_locations_become_place_names():
    from riskparse.sheetcheck import place_name, tag_locations
    assert place_name("Cromer, MB (Enbridge)") == "Cromer"
    assert place_name("Cromer - Enbridge Transfer") == "Cromer"
    assert place_name("Hardisty - Husky") == "Hardisty" and place_name("Westspur") == "Westspur"
    t = _trade("1", "SELL", "2500")
    t.location, t.evidence["location"] = "Cromer, MB (Enbridge)", "p1: Cromer, MB (Enbridge)"
    tag_locations([t])
    assert t.location == "Cromer" and "Cromer, MB (Enbridge)" in t.evidence["location"]


# --------------------------------------------------------------------------- validation rules

def _rules_sheet(folder, extra_rows):
    folder.mkdir(parents=True, exist_ok=True)
    rows = [HEAD] + extra_rows
    _wb(folder / "October Trading 2026.xlsm", {"Trading": rows})
    return folder


def test_broker_rule_whole_month_blank_broker_and_term(tmp_path):
    from riskparse.sheetcheck import EVERGREEN, NO_CONFIRMATION, OUTSIDE_PERIOD
    nov = datetime(2026, 11, 1)
    ts = _rules_sheet(tmp_path / "ts", [
        # brokered, traded Oct 20: after the last confirmation (Oct 5) but in the same month
        _row(12001, datetime(2026, 10, 20), "SYN Late", "Libby", "Sell", -2500, "LSB", "USD",
             nov, "Fixed", -5, "Modern"),
        # dated, Broker / Direct blank
        _row(12002, datetime(2026, 10, 21), "SYN Blank", "Libby", "Sell", -2500, "LSB", "USD",
             nov, "Fixed", -5, None),
        # traded in September: its confirm belongs with September's contracts
        _row(12003, datetime(2026, 9, 20), "SYN Sept", "Libby", "Sell", -2500, "LSB", "USD",
             nov, "Fixed", -5, "Marex"),
        # a Term agreement: brought in like Evergreen
        _row(11120, "Term", "SYN C5 Co", "Libby", "Buy", 3534, "C5", "USD", nov, "Fixed",
             -0.9, None, "C5")])
    r = build_review(_zip(tmp_path), tmp_path / "out", trade_sheet=ts)
    rows = {row.sheet.internal_contract: row for row in r.sheet_check.rows if not row.trade}
    assert rows["12001"].status == NO_CONFIRMATION and "Modern" in rows["12001"].notes[0]
    assert rows["12002"].status == NO_CONFIRMATION and "blank" in rows["12002"].notes[0]
    assert rows["12003"].status == OUTSIDE_PERIOD and "Sep 2026" in rows["12003"].notes[0]
    assert rows["11120"].status == EVERGREEN
    term = next(t for t in r.sheet_trades if t.contract_no == "11120")
    assert term.family == "Trade sheet - Term" and term.source_label == "Trade sheet - Term"
    html, data = _dashboard_data(r.dashboard)
    by = {c["check"]: c for c in data["checks"]}
    assert by["Brokered deals in the period without a broker confirm"]["value"] == 2
    assert data["kpi"]["broker_missing"] == 2 and 'id="rulegrid"' in html
    assert 'id="infobtn"' in html and 'id="infopanel"' in html      # behind the "i" button
    assert 'id="rules"' not in html                                  # not on the page itself


def _direct_sheet(folder, volume=-2500):
    return _rules_sheet(folder, [
        _row(13001, datetime(2026, 10, 3), "SYN Ice Co", "Libby", "Sell", volume, "LSB", "USD",
             datetime(2026, 11, 1), "Fixed", -6, "Direct"),
        _row(13002, datetime(2026, 10, 4), "SYN Ice Two", "Libby", "Buy", 1000, "Midale",
             "CAD", datetime(2026, 11, 1), "Index", None, "Direct")])


def test_direct_trades_wait_for_a_person_and_the_register_remembers(tmp_path):
    from riskparse.confirmlog import AWAITING, CONFIRMED, DISPUTED, FILE
    results = tmp_path / "2. Raw Data"
    ts = _direct_sheet(tmp_path / "ts")
    r = build_review(_zip(tmp_path), results_root=results, trade_sheet=ts)
    register = results / FILE
    assert r.confirm_log == register and register.is_file()
    assert {c.status for c in r.direct_confirmations.values()} == {AWAITING}
    html, data = _dashboard_data(r.dashboard)
    by = {c["check"]: c for c in data["checks"]}
    assert by["Direct (ICE) trades not yet confirmed by a person"]["value"] == 2
    assert data["kpi"]["direct_awaiting"] == 2 and data["kpi"]["direct_confirmed"] == 0

    # a person confirms one (Y) and the counterparty disagrees with the other (N)
    wb = load_workbook(register)
    ws = wb.active
    head = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        rec = dict(zip(head, (c.value for c in row), strict=True))
        col = head.index("Confirmed with counterparty (Y/N)")
        row[col].value = "Y" if rec["Contract #"] == "13001" else "N"
        row[head.index("Confirmed by")].value = "A. Risk"
        row[head.index("Notes")].value = "" if rec["Contract #"] == "13001" else "volume 900"
    wb.save(register)
    r2 = build_review(_zip(tmp_path), results_root=results, trade_sheet=ts)
    st = {t.contract_no: r2.direct_confirmations[id(t)] for t in r2.sheet_trades}
    assert st["13001"].status == CONFIRMED and st["13001"].by == "A. Risk"
    assert st["13002"].status == DISPUTED
    disputed = next(t for t in r2.sheet_trades if t.contract_no == "13002")
    assert any("does not agree" in f and "volume 900" in f for f in disputed.flags)
    text = format_summary(r2)
    assert "Direct (ICE) trades confirmed by a person: 1 of 2 (1 the counterparty " \
           "disagrees with, 0 awaiting)" in text
    checks = {row[0]: row for row in load_workbook(r2.raw_data)["checks"].iter_rows(
        min_row=2, values_only=True)}
    assert "Human confirmation: CONFIRMED by A. Risk" in checks["13001"][10]

    # the trade sheet row changes: a new trade to confirm; the old row stays, marked
    ts2 = _direct_sheet(tmp_path / "ts2", volume=-3000)
    r3 = build_review(_zip(tmp_path), results_root=results, trade_sheet=ts2)
    st3 = {t.contract_no: r3.direct_confirmations[id(t)].status for t in r3.sheet_trades}
    assert st3 == {"13001": AWAITING, "13002": DISPUTED}
    ws = load_workbook(register).active
    head = [c.value for c in ws[1]]
    recs = [dict(zip(head, (c.value for c in row), strict=True))
            for row in ws.iter_rows(min_row=2)]
    assert len(recs) == 3
    old = next(x for x in recs if x["Contract #"] == "13001" and x["Volume (m3)"] == 2500)
    assert old["On the trade sheet"] == "no longer" and old["Confirmed by"] == "A. Risk"
