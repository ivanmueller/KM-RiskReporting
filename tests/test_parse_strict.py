"""Regression tests for D-2 (lossy number/date parsing).

Kept in their own module, importing only ``_parse``, so they can also be run against
the pre-fix code (commit 694a11d) to show that they fail there.
"""

from decimal import Decimal

import pytest

from riskparse.imports import _parse


@pytest.mark.parametrize("raw", ["2,65", "1,234.56", "1.234,56", "1e3", "1E-2", "NaN", "nan",
                                 "Infinity", "-inf", "$2.50", "2.50 CAD", "(100)", "٣",
                                 "1 000", "--5", "+-5", ".5", "5."])
def test_d2_ambiguous_numbers_are_not_converted(raw):
    assert _parse("price_value", raw) == (None, False)


@pytest.mark.parametrize("raw,expected", [("2.65", "2.65"), ("-5", "-5"), ("+5", "5"),
                                          (" 0 ", "0"), ("0.0000", "0.0000"),
                                          ("1000", "1000")])
def test_d2_plain_decimals_accepted_exactly(raw, expected):
    assert _parse("price_value", raw) == (Decimal(expected), True)


def test_d2_thousands_separator_only_when_declared():
    assert _parse("volume_value", "1,234.5", ",") == (Decimal("1234.5"), True)
    assert _parse("volume_value", "12,34", ",") == (None, False)       # bad grouping
    assert _parse("volume_value", "1,234.5", None) == (None, False)


@pytest.mark.parametrize("raw", ["20261005", "2026-W41-1", "2026-10-05T00:00:00", "10/05/2026",
                                 "2026-02-30", "２０２６-10-05"])
def test_d2_only_plain_iso_dates(raw):
    assert _parse("trade_date", raw) == (None, False)
