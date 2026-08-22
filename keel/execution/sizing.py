"""Position sizing (P3 Task 2).

Two sizing regimes, both `Decimal`-only (money/prices never touch `float`, and distances are
always expressed as %/price -- never pips):

- **Fixed-fractional risk sizing** (`size`): risk a fixed percentage of account equity per
  trade, sized off the stop distance in price. This is the sizing used by stop-bearing rules
  (breakout/pullback/mean-reversion) where a wrong entry has a defined invalidation price.
- **No-stop accumulation sizing** (`dca_size`): a fixed USD budget converted to quantity at the
  current price, for the DCA rule which has no stop by design (`strategy/rules/dca.py`).

`spend` is a small shared helper (qty * entry) used by callers that need to know how much
notional a sized quantity will actually spend, for order construction and per-order/per-day cap
checks (guards, P3 Task 3).

`quantize_down`/`quote_increment_for` (#513) are the LAST step before an order is serialised.
Everything above computes in full `Decimal` precision, which is correct arithmetically and
rejected by the venue: Coinbase enforces a per-product increment and answers
`INVALID_SIZE_PRECISION` to anything finer. The engine's precision is an internal property; the
wire has its own.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core.products import quote_currency_of

#: Quote increment per settlement currency -- the finest quote_size a venue will accept.
#:
#: Deliberately keyed on the CURRENCY, not the product: `quote_increment` is a property of the
#: money leg, and every fiat/stablecoin-quoted spot pair on this venue settles to the cent. Base
#: increments are NOT here and must never be guessed from this table -- they vary per asset
#: (BTC at 1e-8, XLM coarser) and are only knowable from venue product metadata, which nothing
#: on the Coinbase path fetches yet. See #513's follow-up.
QUOTE_INCREMENTS: dict[str, Decimal] = {
    "USD": Decimal("0.01"),
    "USDC": Decimal("0.01"),
    "USDT": Decimal("0.01"),
    "EUR": Decimal("0.01"),
    "GBP": Decimal("0.01"),
}


def quantize_down(value: Decimal, increment: Decimal) -> Decimal:
    """`value` rounded DOWN to a multiple of `increment`.

    **Down, never nearest, and the direction is the whole point.** Rounding a size UP spends more
    than was authorised -- and what authorised it was `guards.check`, which has already approved
    a specific notional against the per-order and per-day caps. Rounding up after the rails have
    run would let an order exceed a cap the rails passed, which is the one error this function
    must not make. Rounding down leaves dust; that is the strictly safe direction.

    (Same rule, same reasoning as `scripts/robinhood_order_probe.py::quantize_to`, promoted here
    because it is now needed on the live-money path rather than in a probe.)

    A non-positive `increment` is returned unquantized -- callers that cannot establish an
    increment must refuse the order rather than pass 0 here and hope.

    **The result is presented at the increment's own scale, and `.normalize()` alone must NOT be
    used to do it.** `Decimal("50").normalize()` is `Decimal("5E+1")`, whose `str()` is `"5E+1"`
    -- which is what would go on the wire for a round $50 DCA buy. Flooring to a multiple handles
    an arbitrary increment (0.05, 1000, ...); the final `quantize` fixes the presentation.
    """
    if increment <= 0:
        return value
    stepped = (value // increment) * increment
    scale = increment.normalize()
    exponent = scale.as_tuple().exponent
    # A fractional increment (0.01 -> -2) is presented at its own number of decimals; an integral
    # one (1, 1000) is presented as an integer. Either way, never in scientific notation.
    return stepped.quantize(scale if isinstance(exponent, int) and exponent < 0 else Decimal(1))


def quote_increment_for(product_id: str) -> Decimal | None:
    """The venue's finest acceptable `quote_size` for `product_id`, or `None` if unknown.

    `None` means UNKNOWN and callers must fail closed on it, exactly as rail 13 vetoes a BUY on
    an unknown balance -- `quote_currency_of`'s own docstring states that contract. Guessing a
    precision here would put a wrong number on the wire with real money behind it.
    """
    currency = quote_currency_of(product_id)
    if currency is None:
        return None
    return QUOTE_INCREMENTS.get(currency)


def size(equity: Decimal, risk_pct: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
    """Fixed-fractional position size: risk `risk_pct` of `equity` over the entry-to-stop
    distance.

    `qty = (equity * risk_pct) / abs(entry - stop)`. The stop distance is taken as an absolute
    value so callers don't need to know the trade direction. Raises `ValueError` if `entry` and
    `stop` are equal (a zero stop distance would risk-size to an infinite/undefined quantity).
    """
    stop_distance = abs(entry - stop)
    if stop_distance == 0:
        raise ValueError("size: stop distance is zero (entry == stop) -- cannot size a trade")

    return (equity * risk_pct) / stop_distance


def spend(qty: Decimal, entry: Decimal) -> Decimal:
    """Notional dollars spent buying `qty` at `entry`."""
    return qty * entry


def dca_size(budget_usd: Decimal, entry: Decimal) -> Decimal:
    """No-stop accumulation sizing: convert a fixed USD `budget_usd` into quantity at `entry`.

    Used by the DCA rule, which has no stop by design -- sizing is purely budget / price.
    Raises `ValueError` if `entry` is zero or negative.
    """
    if entry <= 0:
        raise ValueError("dca_size: entry must be positive")

    return budget_usd / entry
