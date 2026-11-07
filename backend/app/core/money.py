"""Money helpers. All monetary values are `Decimal` with exactly 2 fraction digits,
matching the NUMERIC(15, 2) columns. Floats are never accepted."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.exceptions import InvalidAmountError

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# NUMERIC(15, 2) => 13 integer digits.
MAX_MONEY = Decimal("9999999999999.99")


def to_money(value: Decimal | int | str) -> Decimal:
    if isinstance(value, float):
        raise InvalidAmountError("Float values are not allowed for money; use Decimal or str")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidAmountError(f"Invalid money value: {value!r}") from exc
    if not amount.is_finite():
        raise InvalidAmountError("Money value must be finite")
    if amount != amount.quantize(CENT):
        raise InvalidAmountError("Money value must have at most 2 decimal places")
    amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    if abs(amount) > MAX_MONEY:
        raise InvalidAmountError("Money value is out of range for NUMERIC(15, 2)")
    return amount


def to_positive_money(value: Decimal | int | str) -> Decimal:
    amount = to_money(value)
    if amount <= ZERO:
        raise InvalidAmountError("Amount must be greater than zero")
    return amount


def round_money(value: Decimal) -> Decimal:
    """Round a computed value (e.g. price * quantity) to cents."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
