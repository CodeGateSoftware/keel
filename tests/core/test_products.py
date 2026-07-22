"""`keel_core.products` -- deriving an order's settlement leg from its product id.

The currency an order spends is a property of the PRODUCT, never of global config. Getting this
wrong let rail 13 guard a balance the order never touches.
"""

from __future__ import annotations

from keel_core.products import quote_currency_of


def test_the_quote_leg_is_the_part_after_the_last_dash():
    assert quote_currency_of("BTC-USD") == "USD"
    assert quote_currency_of("BTC-USDC") == "USDC"
    assert quote_currency_of("PAXG-USD") == "USD"


def test_it_is_case_normalised():
    assert quote_currency_of("eth-usd") == "USD"


def test_a_malformed_product_id_returns_None_so_callers_fail_closed():
    """`None` is what rail 13 already treats as 'unknown' and vetoes on."""
    for bad in ("BTC", "", "   ", "BTC-", "-USD", None):
        assert quote_currency_of(bad) is None, f"{bad!r} should not resolve to a currency"
