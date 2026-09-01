"""Orders: the checks of one payment request before the service stores it.

A request is a JSON object with the members order_id, client_id, amount and currency_code. The
client sends attempt 2 when it repeats a payment after an error. A request that fails a check is
not an order: the service answers 400 and stores nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# The ISO 4217 codes that the payment provider accepts, with their minor units.
CURRENCIES = {
    "CHF": 2,
    "CZK": 2,
    "DKK": 2,
    "EUR": 2,
    "GBP": 2,
    "HUF": 2,
    "JPY": 0,
    "NOK": 2,
    "PLN": 2,
    "SEK": 2,
    "USD": 2,
}

# The largest amount of one payment, in the major unit of its currency.
MAX_AMOUNT = Decimal(10000)

# The client repeats a failed payment one time.
MAX_ATTEMPT = 2

# The longest order ID and client ID.
MAX_ID_LENGTH = 64


class OrderError(ValueError):
    """The request is not a valid order."""


@dataclass(frozen=True)
class Order:
    """A checked payment request."""

    order_id: str
    client_id: str
    amount: Decimal
    currency_code: str
    attempt: int


def _identifier(order: dict, name: str) -> str:
    value = order.get(name)
    if not isinstance(value, str) or not value or len(value) > MAX_ID_LENGTH:
        raise OrderError(f"{name} must be a text of 1 to {MAX_ID_LENGTH} characters")
    return value


def _amount(order: dict, currency_code: str) -> Decimal:
    raw = order.get("amount")
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise OrderError("amount must be a decimal text or an integer")
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise OrderError("amount must be a decimal number") from exc
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        raise OrderError(f"amount must be more than 0 and at most {MAX_AMOUNT}")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > CURRENCIES[currency_code]:
        raise OrderError(f"{currency_code} has {CURRENCIES[currency_code]} decimals at most")
    return amount


def _attempt(order: dict) -> int:
    attempt = order.get("attempt", 1)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= MAX_ATTEMPT:
        raise OrderError(f"attempt must be an integer from 1 to {MAX_ATTEMPT}")
    return attempt


def parse_order(order: dict) -> Order:
    """Check one request and return the order. Raise OrderError for an invalid request."""
    order_id = _identifier(order, "order_id")
    client_id = _identifier(order, "client_id")
    # An older mobile client sends no currency_code. Its orders are in euro.
    currency_code = order.get("currency_code", "EUR")
    if not isinstance(currency_code, str) or currency_code.upper() not in CURRENCIES:
        raise OrderError("currency_code must be a currency that the provider accepts")
    currency_code = currency_code.upper()
    return Order(
        order_id=order_id,
        client_id=client_id,
        amount=_amount(order, currency_code),
        currency_code=currency_code,
        attempt=_attempt(order),
    )
