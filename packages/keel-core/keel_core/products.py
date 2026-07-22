"""Facts derivable from a venue product id.

The one rule this module exists to enforce: **the currency an order spends is a property of the
PRODUCT, not of global configuration.** `BTC-USD` settles in USD whatever `config.quote_currency`
says. Conflating the two let rail 13 check a balance the order never touches, which could pass an
order the account had no settled funds for -- the exact case that rail exists to prevent.
"""

from __future__ import annotations


def quote_currency_of(product_id: str | None) -> str | None:
    """The settlement leg of `product_id` (`"BTC-USD"` -> `"USD"`), uppercased.

    Returns `None` for anything that does not resolve to a currency -- no separator, an empty
    base or quote leg, or a non-string. Callers must treat `None` as *unknown* and fail closed:
    rail 13 already vetoes a BUY on an unknown balance, and an unresolvable product id is
    precisely that.
    """
    if not isinstance(product_id, str):
        return None
    base, separator, quote = product_id.rpartition("-")
    if not separator or not base.strip() or not quote.strip():
        return None
    return quote.strip().upper()
