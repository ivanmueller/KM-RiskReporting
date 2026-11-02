"""Confirmation-family parsers, merging and validation (synthetic replicas only)."""

from datetime import date
from decimal import Decimal

import pytest

from confirm_fixtures import (
    US,
    calrock_msg,
    isda_pdf,
    marex_pdf,
    marex_xls,
    modern_xlsx,
    ox_pdf,
)
from doc_fixtures import build_pdf
from riskparse.confirms import (
    detect_family,
    merge_trades,
    month_bounds,
    month_term,
    parse_date,
    parse_document,
    parse_number,
)
from riskparse.extract.dispatch import extract_bytes


def _parse(data, ftype, name="f"):
    fam, trades = parse_document(extract_bytes(data, ftype, filename=name), ftype, "D001", name)
    return fam, trades


# --- helpers --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("-$6.10 USD / BBL", "-6.10"), ("2,500 M3 / Month", "2500"), ("-6.50 USD/Barrel", "-6.50"),
    ("5,000m3", "5000"), ("CAD 106.12 per BBL", "106.12"), ("2500 cbm", "2500"),
    ("10,000.00 BBL", "10000.00")])
def test_parse_number(raw, expected):
    assert parse_number(raw) == Decimal(expected)


def test_parse_date_formats_and_terms():
    assert parse_date("November  01, 2026", "%B %d, %Y") == date(2026, 11, 1)
    assert parse_date("05-Oct-26 14:53:13", "%d-%b-%y") == date(2026, 10, 5)
    assert parse_date("2026-10-02 08:59:46 AM", "%Y-%m-%d") == date(2026, 10, 2)
    assert parse_date("not a date", "%Y-%m-%d") is None
    assert month_bounds("Nov-26") == (date(2026, 11, 1), date(2026, 11, 30))
    assert month_term(date(2026, 11, 1), date(2026, 11, 30)) == "NOV-26"
    assert month_term(date(2026, 11, 1), date(2026, 12, 31)) == "NOV-26 to DEC-26"
    assert month_term(date(2026, 11, 5), date(2026, 11, 30)) == ""


# --- each family ----------------------------------------------------------------------

def test_modern_buy_and_sell():
    fam, [t] = _parse(modern_xlsx(side="Buy"), "XLSX")
    assert fam == "modern"
    assert (t.contract_no, t.side, t.trader, t.counterparty, t.seller) == (
        "90001", "BUY", "Libby Smith", "SYN Refining Inc.", "SYN Refining Inc.")
    assert (t.trade_date, t.start_date, t.end_date, t.term) == (
        date(2026, 10, 2), date(2026, 11, 1), date(2026, 11, 30), "NOV-26")
    assert (t.quantity, t.unit, t.grade, t.primary_index, t.fixed_differential, t.currency) == (
        Decimal("2500"), "m3/month", "MID", "WTI CMA", Decimal("-8.75"), "USD")
    assert t.evidence["fixed_differential"].startswith("Trade Report!J2")
    _, [s] = _parse(modern_xlsx(side="Sell", product="LSB"), "XLSX")
    assert (s.side, s.seller, s.counterparty) == ("SELL", US, "SYN Refining Inc.")


def test_modern_not_finalized_is_flagged():
    _, [t] = _parse(modern_xlsx(state="Pending"), "XLSX")
    assert any("not Finalized" in f for f in t.flags)


def test_ox_pdf():
    fam, [t] = _parse(ox_pdf(), "PDF")
    assert fam == "ox"
    assert (t.contract_no, t.trade_date, t.start_date, t.end_date, t.term) == (
        "2099001", date(2026, 10, 5), date(2026, 11, 1), date(2026, 11, 30), "NOV-26")
    assert (t.side, t.trader, t.counterparty, t.seller) == ("SELL", "Libby Smith",
                                                            "SYN Resources Inc.", US)
    assert (t.quantity, t.unit, t.grade, t.primary_index) == (Decimal("2500"), "m3/month",
                                                             "LSB", "WTI CMA")
    assert (t.fixed_differential, t.price_unit, t.currency) == (Decimal("-6.10"), "USD/bbl", "CAD")


def test_ox_layout_shift_does_not_matter():
    _, [t] = _parse(ox_pdf(extra_address_line=True), "PDF")
    assert t.trader == "Libby Smith" and t.seller == US


def test_marex_pdf_derives_dates_from_contract_month():
    fam, [t] = _parse(marex_pdf(), "PDF")
    assert fam == "marex_pdf"
    assert (t.contract_no, t.trade_date, t.start_date, t.end_date, t.term) == (
        "119999001", date(2026, 10, 5), date(2026, 11, 1), date(2026, 11, 30), "NOV-26")
    assert (t.side, t.trader, t.counterparty) == ("SELL", "Tyler Demers", "SYN Products Inc")
    assert (t.quantity, t.unit, t.grade, t.fixed_differential) == (
        Decimal("5000"), "m3/month", "LSB", Decimal("-6.50"))
    assert "derived" in t.evidence["start_date"]


def test_marex_xls_summary():
    fam, [t] = _parse(marex_xls(), "XLS", "x.xls")
    assert fam == "marex_xls"
    assert (t.contract_no, t.side, t.grade, t.term, t.quantity, t.fixed_differential) == (
        "119999001", "SELL", "LSB", "NOV-26", Decimal("5000"), Decimal("-6.5"))


@pytest.mark.parametrize("forwarded", [False, True])
def test_calrock_email_direct_and_forwarded(forwarded):
    fam, [t] = _parse(calrock_msg(forwarded=forwarded), "MSG")
    assert fam == "calrock"
    assert (t.contract_no, t.trade_date, t.start_date, t.end_date, t.term) == (
        "82999001", date(2026, 10, 5), date(2026, 11, 1), date(2026, 11, 30), "NOV-26")
    assert (t.side, t.trader, t.counterparty, t.seller) == (
        "BUY", "Tyler Demers", "SYN Energy Marketing Inc.", "SYN Energy Marketing Inc.")
    assert (t.quantity, t.unit, t.grade, t.primary_index, t.fixed_differential) == (
        Decimal("5000"), "m3/month", "WCS", "WTI CMA", Decimal("-24.85"))
    assert (t.price_unit, t.currency) == ("USD/bbl", "USD")


def test_isda_swap_sides_and_fixed_price():
    fam, [t] = _parse(isda_pdf(), "PDF")
    assert fam == "isda_swap"
    assert (t.contract_no, t.trade_date, t.start_date, t.end_date, t.term) == (
        "181000001", date(2026, 10, 2), date(2027, 10, 1), date(2027, 10, 31),
        "OCT-27")
    assert (t.side, t.counterparty, t.seller) == ("SELL", "SYN Bank of Canada", US)
    assert (t.quantity, t.unit, t.primary_index) == (Decimal("10000.00"), "bbl", "OIL-WTI-NYMEX")
    assert t.fixed_differential is None and t.fixed_price == Decimal("106.12")
    assert (t.price_unit, t.currency, t.trader) == ("CAD/bbl", "CAD", "")
    _, [b] = _parse(isda_pdf(fixed_payer=US, floating_payer="SYN Bank of Canada",
                             fixed_price="USD 91.25 per BBL", payments_in="USD"), "PDF")
    assert (b.side, b.seller, b.currency) == ("BUY", "SYN Bank of Canada", "USD")


def test_unknown_layout_is_not_guessed():
    r = extract_bytes(build_pdf([["SYNTHETIC letter", "Price: 2.50", "Volume: 100"]]), "PDF")
    assert detect_family(r, "PDF") is None
    assert parse_document(r, "PDF", "D1", "x.pdf") == (None, [])


def test_our_side_not_found_is_flagged():
    _, [t] = _parse(ox_pdf(seller="SYN Other Ltd"), "PDF")
    assert t.side == "" and any("cannot tell our side" in f for f in t.flags)


# --- merging ----------------------------------------------------------------------------

def test_marex_pdf_and_summary_become_one_trade():
    _, a = _parse(marex_pdf(), "PDF", "pdf")
    _, b = _parse(marex_xls(), "XLS", "summary.xls")
    [t] = merge_trades(a + b)
    assert t.family == "Marex (PDF)" and any("key terms agree" in f for f in t.flags)
    assert len(t.documents) == 2


def test_marex_disagreement_is_flagged():
    _, a = _parse(marex_pdf(), "PDF", "pdf")
    _, b = _parse(marex_xls(price=-7.0), "XLS", "summary.xls")
    [t] = merge_trades(a + b)
    assert any("DISAGREES" in f and "fixed_differential" in f for f in t.flags)


def test_modern_update_supersedes_and_lists_changes():
    _, old = _parse(modern_xlsx(volume=2500.0, updated="2026-10-06 11:11:00 AM"), "XLSX",
                    "Trade-Confirm-Seller-90001-1.xlsx")
    _, new = _parse(modern_xlsx(volume=4000.0, updated="2026-10-06 11:16:16 AM"), "XLSX",
                    "UPDATED-Trade-Confirm-Seller-90001-2.xlsx")
    [t] = merge_trades(old + new)
    assert t.quantity == Decimal("4000") and t.source_file.startswith("UPDATED")
    assert any(f.startswith("AMENDED") and "quantity: 2500 -> 4000" in f for f in t.flags)


def test_different_brokers_with_same_number_stay_separate():
    _, a = _parse(modern_xlsx(number=12345), "XLSX")
    _, b = _parse(ox_pdf(deal="12345"), "PDF")
    assert len(merge_trades(a + b)) == 2


def test_missing_required_values_are_flagged():
    _, [t] = _parse(ox_pdf(qty="see attached"), "PDF")
    [m] = merge_trades([t])
    assert any(f.startswith("missing:") and "quantity" in f for f in m.flags)


def test_price_with_no_index_is_a_flat_price_not_a_differential():
    _, [t] = _parse(modern_xlsx(price=72.5, basis=""), "XLSX")
    assert (t.fixed_differential, t.fixed_price) == (None, Decimal("72.5"))
    assert t.evidence["fixed_price"].startswith("Trade Report!")
    assert any(f.startswith("flat price") for f in t.flags)
    _, [f] = _parse(modern_xlsx(price=72.5, basis="Fixed"), "XLSX")
    assert f.fixed_price == Decimal("72.5") and f.fixed_differential is None
    _, [d] = _parse(modern_xlsx(price=-8.75), "XLSX")              # index: a differential
    assert (d.fixed_differential, d.fixed_price) == (Decimal("-8.75"), None)
