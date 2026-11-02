"""Layout-neutral field proposals (synthetic layouts only)."""

import io

from openpyxl import Workbook

from doc_fixtures import build_msg, build_pdf
from riskparse.extract.dispatch import extract_bytes
from riskparse.fields import coverage, map_label, normalize_label, propose_fields


def _pdf(lines):
    return propose_fields(extract_bytes(build_pdf([lines]), "PDF"))


def _xlsx(cells):
    wb = Workbook()
    ws = wb.active
    ws.title = "Confirm"
    for ref, value in cells.items():
        ws[ref] = value
    b = io.BytesIO()
    wb.save(b)
    return propose_fields(extract_bytes(b.getvalue(), "XLSX"))


def _by_field(props):
    return {p.field: p for p in props}


def test_pdf_label_colon_pairs_with_locations_and_raw_values():
    f = _by_field(_pdf(["SYNTHETIC CONFIRMATION", "Trade Date: 2026-10-05    Deal No: TC-1",
                        "Contract Price (CAD/GJ): 2.50", "Quantity: 1,000 GJ/d",
                        "Delivery Point: AECO-NIT"]))
    assert f["trade_date"].value == "2026-10-05" and f["deal_reference"].value == "TC-1"
    assert f["price"].value == "2.50" and f["price"].label == "Contract Price (CAD/GJ)"
    assert f["quantity"].value == "1,000 GJ/d"          # raw: no number or unit parsing
    assert f["delivery_point"].location == "p1:L5"


def test_value_on_next_line():
    f = _by_field(_pdf(["SYNTHETIC", "Seller:", "Synthetic Us Ltd", "Buyer: Alpha"]))
    assert f["seller"].value == "Synthetic Us Ltd" and f["seller"].how == "next line"


def test_label_with_no_value_is_kept_and_marked():
    f = _by_field(_pdf(["SYNTHETIC", "Price:", "Buyer: Alpha"]))
    assert f["price"].value == "" and f["price"].how == "no value"


def test_unmapped_labels_are_kept_not_dropped():
    props = _pdf(["SYNTHETIC", "Nomination Deadline: 10:00 MST"])
    assert [(p.field, p.label) for p in props] == [("(unmapped)", "Nomination Deadline")]


def test_email_body_pairs():
    r = extract_bytes(build_msg(body="Hi,\nPrice: 2.55\nVolume: 500 GJ/d\nThanks"), "MSG")
    f = _by_field(propose_fields(r))
    assert f["price"].value == "2.55" and f["price"].location.startswith("body:")


def test_excel_label_right_below_and_header_rows():
    f = _by_field(_xlsx({"A3": "Trade Date", "B3": "2026-10-05", "A4": "Price:", "C4": 2.5,
                         "A5": "Volume", "A6": 1000, "A8": "Buyer", "B8": "Seller",
                         "A9": "Alpha", "B9": "Us"}))
    assert f["trade_date"].value == "2026-10-05" and f["trade_date"].how == "cell right"
    assert f["price"].value == "2.5" and f["price"].location == "Confirm!C4"
    assert f["quantity"].value == "1000" and f["quantity"].how == "cell below"
    assert f["buyer"].value == "Alpha" and f["seller"].value == "Us"   # header-row table


def test_label_normalization_and_mapping():
    assert normalize_label("Contract Price (USD/MMBtu)") == "contract price"
    assert map_label("DAILY CONTRACT QUANTITY") == "quantity"
    assert map_label("Delivery Point 1") == "delivery_point"
    assert map_label("Total Volume") == "quantity"
    assert map_label("Colour") == "(unmapped)"


def test_coverage_counts_documents_with_a_value():
    a = _pdf(["SYNTHETIC", "Price: 1", "Volume: 2"])
    b = _pdf(["SYNTHETIC", "Price:", "Buyer: X"])
    assert coverage({"D1": a, "D2": b}) == {"price": 1, "quantity": 1, "buyer": 1}


def test_known_labels_without_colon_sentence_and_table_forms():
    r = extract_bytes(build_msg(body="Please confirm the following:\nContract Price 2.50 CAD/GJ\n"
                                "Volume  5,000 GJ/d\nDelivery Point    AECO\n"
                                "Start Date Nov 1, 2026\nPrice is subject to change"), "MSG")
    f = {p.field: p for p in propose_fields(r)}
    assert f["price"].value == "2.50 CAD/GJ" and f["price"].how == "no colon"
    assert f["quantity"].value == "5,000 GJ/d"
    assert f["delivery_point"].value == "AECO"
    assert f["start_date"].value == "Nov 1, 2026"
    assert all(p.value != "is subject to change" for p in propose_fields(r))


def test_header_rows_are_not_paired():
    props = _pdf(["SYNTHETIC", "Buyer    Seller"])
    assert props == []


def test_excel_candidate_labels_are_listed_unmapped():
    props = _xlsx({"A1": "Date", "B1": "2026-10-05", "A2": "Sold To", "C2": "SYN Co 1",
                   "A3": "Notes", "B3": "free text"})
    got = {(p.field, p.label, p.how) for p in props}
    assert ("(unmapped)", "Date", "cell right (candidate label)") in got
    assert ("(unmapped)", "Sold To", "cell right (candidate label)") in got
    assert not any(p.label == "Notes" for p in props)          # right cell is not value-like
