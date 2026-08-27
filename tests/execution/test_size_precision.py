"""Order sizes must be serialised at the VENUE's precision, not the engine's (#513).

The live failure this covers: the first `turtle_breakout` entry ever to reach Coinbase was
rejected with `INVALID_SIZE_PRECISION` because `str(Decimal)` emitted every digit the sizing
arithmetic produced. The rails had already passed the order -- the defect is downstream of them,
at serialisation, which is precisely where a rail cannot look.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.execution import sizing
from keel.execution.executor import SizePrecisionUnavailable, _order_spec
from keel.execution.guards import OrderIntent
from keel.types import Side

#: The exact notional Coinbase rejected on 2026-08-22 (keel-live.db, order id 3, rule 5).
REJECTED_NOTIONAL = Decimal("23.00803473938010547532738517")


def _buy(product_id: str = "XLM-USD", notional: Decimal = REJECTED_NOTIONAL) -> OrderIntent:
    return OrderIntent(
        product_id=product_id,
        side=Side.BUY,
        qty=Decimal("114.0117873747800116713612474"),
        entry=Decimal("0.201804"),
        stop=None,
        notional=notional,
        is_dca=False,
        rule_kind="turtle_breakout",
    )


# -- quantize_down ------------------------------------------------------------------------


def test_quantize_down_rounds_down_never_nearest() -> None:
    """The direction is the safety property, not a rounding preference.

    `guards.check` approved a specific notional against the per-order and per-day caps. Rounding
    UP after the rails have run would spend more than they authorised.
    """
    # 23.999 is nearer to 24.00 than to 23.99; nearest-rounding would go UP. It must not.
    assert sizing.quantize_down(Decimal("23.999"), Decimal("0.01")) == Decimal("23.99")


def test_quantize_down_is_exact_on_an_already_round_value() -> None:
    assert sizing.quantize_down(Decimal("50.00"), Decimal("0.01")) == Decimal("50")


def test_quantize_down_never_emits_scientific_notation() -> None:
    """The bug this test was written for: `Decimal("50").normalize()` is `Decimal("5E+1")`.

    `str()` of that is `"5E+1"`, which would have gone on the wire for a round $50 DCA buy --
    breaking the one order shape that currently works.
    """
    assert str(sizing.quantize_down(Decimal("50.00"), Decimal("0.01"))) == "50.00"
    assert "E" not in str(sizing.quantize_down(Decimal("1000"), Decimal("0.01")))


def test_quantize_down_handles_a_padded_increment() -> None:
    """Venues pad their increments (`0.010000000000`); that exponent must not reach the wire."""
    assert str(sizing.quantize_down(Decimal("1.239"), Decimal("0.010000000000"))) == "1.23"


def test_quantize_down_handles_a_non_power_of_ten_increment() -> None:
    """Flooring to a multiple, not just truncating decimals -- increments need not be 10^-n."""
    assert sizing.quantize_down(Decimal("23.99"), Decimal("0.05")) == Decimal("23.95")


def test_quantize_down_handles_an_integral_increment() -> None:
    assert str(sizing.quantize_down(Decimal("1234.9"), Decimal("1000"))) == "1000"


def test_quantize_down_passes_through_a_non_positive_increment() -> None:
    """Callers that cannot establish an increment must refuse, not pass 0 here and hope."""
    assert sizing.quantize_down(Decimal("1.239"), Decimal("0")) == Decimal("1.239")


# -- quote_increment_for ------------------------------------------------------------------


@pytest.mark.parametrize("product_id", ["BTC-USD", "XLM-USD", "ETH-USDC", "BTC-EUR"])
def test_quote_increment_known_for_settlement_currencies(product_id: str) -> None:
    assert sizing.quote_increment_for(product_id) == Decimal("0.01")


@pytest.mark.parametrize("product_id", ["nonsense", "", "-USD", "BTC-"])
def test_quote_increment_unknown_is_none_not_a_guess(product_id: str) -> None:
    """Unknown must be representable. A guessed precision is a wrong number with money behind it."""
    assert sizing.quote_increment_for(product_id) is None


def test_quote_increment_unknown_for_an_unlisted_settlement_currency() -> None:
    assert sizing.quote_increment_for("BTC-XYZ") is None


def test_quote_increment_answers_about_the_currency_not_the_instrument_shape() -> None:
    """A derivative-shaped id still settles in USD, and this function is not the rail that cares.

    Rails 18/19 veto perps and dated futures long before serialisation. Answering `0.01` here is
    correct and narrow: the question asked is "how fine is this money leg", not "may we trade
    this". Overloading it with an instrument check would put the veto in two places and let the
    weaker one drift.
    """
    assert sizing.quote_increment_for("BTC-PERP-USD") == Decimal("0.01")


# -- _order_spec: the regression ---------------------------------------------------


def test_the_rejected_order_now_serialises_to_two_decimals() -> None:
    """Regression on the real payload. This exact string was answered INVALID_SIZE_PRECISION."""
    config = _order_spec(_buy())
    assert config.quote_size == Decimal("23.00")


def test_serialised_quote_size_never_exceeds_the_authorised_notional() -> None:
    """The rails approved `notional`; the wire value must not be larger than what they passed."""
    intent = _buy(notional=Decimal("23.999999999"))
    sent = Decimal(_order_spec(intent).quote_size)
    assert sent <= intent.notional


def test_dca_style_round_notional_is_unchanged() -> None:
    """Why this bug hid for weeks: a round budget was always already representable.

    Orders 1 and 2 sent 26 decimal places and filled, because the VALUE was exactly 50.
    """
    intent = _buy(product_id="BTC-USD", notional=Decimal("50.00000000000000000000000000"))
    assert _order_spec(intent).quote_size == Decimal("50.00")


def test_unknown_increment_refuses_rather_than_guessing() -> None:
    with pytest.raises(SizePrecisionUnavailable, match="no quote increment known"):
        _order_spec(_buy(product_id="BTC-XYZ"))


def test_a_notional_that_quantizes_to_zero_is_refused() -> None:
    """A size rounded to nothing must never be sent as an order."""
    with pytest.raises(SizePrecisionUnavailable, match="zero-size order"):
        _order_spec(_buy(notional=Decimal("0.004")))


def test_sell_is_deliberately_untouched_pending_base_increments() -> None:
    """Documents the intentional scope boundary, so a future change is a decision not an accident.

    `base_size` needs the product's `base_increment`, which varies per asset and which nothing on
    the Coinbase path fetches yet. An exit that cannot leave is worse than the bug being fixed.
    """
    intent = _buy()
    sell = OrderIntent(
        product_id=intent.product_id,
        side=Side.SELL,
        qty=intent.qty,
        entry=intent.entry,
        stop=None,
        notional=intent.notional,
        is_dca=False,
        rule_kind="turtle_breakout",
    )
    assert _order_spec(sell).base_size == intent.qty
