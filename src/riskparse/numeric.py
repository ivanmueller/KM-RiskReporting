"""Exact decimal storage guard (Codex review #1).

``NUMERIC(28,10)`` holds at most 18 integer digits and 10 decimal places. Engines either
round or raise when a value exceeds that. The SQLite test adapter goes further: it
stores NUMERIC as a 64-bit float, so ``9007199254740992.5`` comes back as
``9007199254740992.0``, and two different prices can then reconcile as equal.

:func:`storage_problem` says whether a value would come back unchanged.
:class:`ExactNumeric` applies it to every write, so no code path can store a silently
changed number. Imports call :func:`storage_problem` first, so a bad value is reported
as unparsed rather than failing the whole file.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator

PRECISION = 28
SCALE = 10
MAX_INTEGER_DIGITS = PRECISION - SCALE


def storage_problem(value: Decimal, dialect_name: str | None = None) -> str | None:
    """Why ``value`` cannot be stored exactly in NUMERIC(28,10), or None if it can."""
    if not isinstance(value, Decimal):
        return f"expected Decimal, got {type(value).__name__}"
    if not value.is_finite():
        return "not a finite number"
    _sign, digits, exponent = value.as_tuple()
    frac_digits = max(0, -exponent)
    # trailing zeros in the fraction are harmless ("2.5000")
    significant_frac = frac_digits
    trimmed = list(digits)
    while significant_frac > 0 and trimmed and trimmed[-1] == 0:
        trimmed.pop()
        significant_frac -= 1
    if significant_frac > SCALE:
        return f"more than {SCALE} decimal places would be rounded"
    int_digits = len(digits) - frac_digits
    if int_digits > MAX_INTEGER_DIGITS and any(digits[:int_digits]):
        return f"more than {MAX_INTEGER_DIGITS} integer digits"
    if dialect_name == "sqlite":
        try:
            round_trip = Decimal(f"{float(value):.{SCALE}f}")
        except (InvalidOperation, OverflowError):
            return "cannot be represented by the SQLite test adapter"
        if round_trip != value:
            return ("the SQLite test adapter stores NUMERIC as a float and would change this "
                    f"value to {round_trip.normalize()}")
    return None


class ExactNumeric(TypeDecorator):
    """NUMERIC(28,10) that refuses any value it cannot store and return unchanged."""

    impl = Numeric
    cache_ok = True

    def __init__(self):
        super().__init__(PRECISION, SCALE)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("float values are not accepted for money/quantity; use Decimal")
        if isinstance(value, int) and not isinstance(value, bool):
            value = Decimal(value)
        problem = storage_problem(value, dialect.name)
        if problem:
            raise ValueError(f"refusing to store {value}: {problem}")
        return value
