from decimal import Decimal

import pytest

from checkout import orders


def request(**changes):
    body = {"order_id": "ord-1", "client_id": "cl-00001", "amount": "12.50", "currency_code": "SEK"}
    body.update(changes)
    return {k: v for k, v in body.items() if v is not None}


def test_a_valid_order():
    order = orders.parse_order(request())
    assert order == orders.Order("ord-1", "cl-00001", Decimal("12.50"), "SEK", 1)


def test_the_currency_code_is_upper_case():
    assert orders.parse_order(request(currency_code="eur")).currency_code == "EUR"


def test_a_retry_is_attempt_two():
    assert orders.parse_order(request(attempt=2)).attempt == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"order_id": ""},
        {"client_id": "x" * 65},
        {"amount": "0"},
        {"amount": "10000.01"},
        {"amount": "1.234"},
        {"amount": "abc"},
        {"amount": True},
        {"currency_code": "XYZ"},
        {"attempt": 3},
        {"attempt": "1"},
    ],
)
def test_an_invalid_request_is_refused(changes):
    with pytest.raises(orders.OrderError):
        orders.parse_order(request(**changes))


def test_yen_has_no_decimals():
    with pytest.raises(orders.OrderError):
        orders.parse_order(request(currency_code="JPY", amount="100.5"))
    assert orders.parse_order(request(currency_code="JPY", amount=100)).amount == Decimal(100)
