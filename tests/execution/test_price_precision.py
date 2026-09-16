"""Order PRICES must be serialised at the venue's tick, not the engine's (#802).

The sibling of `test_size_precision.py`. That file covers the SIZE leg (#513, the XLM entry
rejected `INVALID_SIZE_PRECISION`); this one covers the PRICE leg, and the live failure is the
same shape one rung along:

    2026-09-16 03:44 UTC, keel-live.db order id 5
    executor.order_rejected  PAXG-USD SELL  error='Too many decimals in order price'

`_bracket_spec` quantized `base_size` and passed `stop`/`target` through untouched, so the
fourteen digits of the rule's ATR arithmetic went on the wire. Every protective bracket was
rejected, which is why every position in that deployment carried `bracket_order_id = NULL`.

Direction is the substance here, not a detail. `quantize_down` is documented as the safe
direction for a SIZE (rounding up spends more than the rails authorised). That reasoning does
NOT transfer to a protective stop: rounding a long's stop DOWN widens the loss it was sized
against. A stop rounds toward safety, a target toward reachability, and they are therefore
rounded in OPPOSITE directions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.execution import executor, sizing
from keel.execution.executor import BracketPricesUnplaceable, _bracket_spec, place_bracket
from keel.types import Side
from tests.execution.test_executor import NOW_TS, _config, _seed_open_position, repo  # noqa: F401
from tests.execution.test_sell_clamp import HeldBroker

#: The exact levels Coinbase rejected on 2026-09-16 (keel-live.db, order id 5, position 3).
LIVE_STOP = Decimal("4521.76390215979454")
LIVE_TARGET = Decimal("5582.02658704123276")
CENT = Decimal("0.01")


def test_quantize_up_rounds_to_a_multiple_and_never_down() -> None:
    assert sizing.quantize_up(Decimal("4521.76390215979454"), CENT) == Decimal("4521.77")
    # Already on the tick: unchanged, NOT bumped a tick higher.
    assert sizing.quantize_up(Decimal("4521.77"), CENT) == Decimal("4521.77")


def test_quantize_up_never_emits_scientific_notation() -> None:
    """`str()` of the result goes on the wire, so `5E+1` is a rejected order (`quantize_down`'s
    own docstring; the up-rounding twin must not reintroduce what that one was written to avoid)."""
    assert str(sizing.quantize_up(Decimal("50"), Decimal("10"))) == "50"
    assert str(sizing.quantize_up(Decimal("0.001"), CENT)) == "0.01"


def test_bracket_spec_quantizes_both_prices_to_the_tick() -> None:
    """The live rejection, reproduced and fixed: neither price may reach the venue unrounded."""
    spec = _bracket_spec(
        "PAXG-USD",
        Decimal("0.01320427"),
        LIVE_TARGET,
        LIVE_STOP,
        base_increment=Decimal("0.00000001"),
        price_increment=CENT,
    )

    assert spec.stop_trigger_price == Decimal("4521.77")
    assert spec.take_profit_price == Decimal("5582.02")
    # What actually goes on the wire is the string form.
    assert str(spec.stop_trigger_price) == "4521.77"
    assert str(spec.take_profit_price) == "5582.02"


def test_the_two_prices_round_in_opposite_directions() -> None:
    """A long's stop rounds UP (toward safety) and its target DOWN (toward reachability).

    This is the assertion that rejects the tempting mutation: reusing `quantize_down` for both,
    which passes a naive "is it on the tick?" check while quietly widening every stop-loss.
    Both live values round strictly away from `quantize_down`'s answer for the stop.
    """
    spec = _bracket_spec("PAXG-USD", Decimal("1"), LIVE_TARGET, LIVE_STOP, price_increment=CENT)

    assert spec.stop_trigger_price > LIVE_STOP, "a long's stop must not be widened by rounding"
    assert spec.take_profit_price < LIVE_TARGET, "a long's target must not be raised by rounding"
    assert spec.stop_trigger_price != sizing.quantize_down(LIVE_STOP, CENT)


def test_an_unknown_tick_sends_the_prices_unchanged() -> None:
    """Unknown means unknown. `_base_increment_for`'s contract for the size leg is "send
    unquantized, never refuse the exit", and the price leg must not be stricter -- refusing here
    would leave a filled position with no stop at all, which is the outcome #799 documents."""
    spec = _bracket_spec("PAXG-USD", Decimal("1"), LIVE_TARGET, LIVE_STOP, price_increment=None)

    assert spec.stop_trigger_price == LIVE_STOP
    assert spec.take_profit_price == LIVE_TARGET


def test_a_tick_that_collapses_the_pair_is_refused_not_sent() -> None:
    """Rounding moves the two prices TOWARD each other, so a coarse tick can invert a pair that
    was valid before it. `BracketGTC` already refuses an inverted-or-equal pair; this asserts the
    refusal is raised as the executor's own precision error, so `place_bracket` routes it through
    the unbracketed-retry path rather than letting it escape (the #799 failure shape)."""
    with pytest.raises(BracketPricesUnplaceable):
        _bracket_spec(
            "FAKE-USD",
            Decimal("1"),
            Decimal("100.009"),  # -> 100.00
            Decimal("100.001"),  # -> 100.01, now ABOVE the target
            price_increment=CENT,
        )


def test_the_spec_is_still_a_sell_bracket_after_quantization() -> None:
    spec = _bracket_spec("PAXG-USD", Decimal("1"), LIVE_TARGET, LIVE_STOP, price_increment=CENT)
    assert spec.side is Side.SELL


# -- the call site: a bracket that cannot be BUILT must not escape `place_bracket` -------------


def test_place_bracket_records_the_retry_when_the_pair_cannot_be_expressed(repo):  # noqa: F811
    """A bracket that cannot be BUILT is the same event as one the venue REFUSES.

    `_bracket_spec` used to be an inline argument to `_run_order`, so anything it raised left
    `place_bracket` past the `if not result.placed` recovery -- the shape that stranded a filled
    PAXG position for three weeks in #799. The entry is already on the books by the time this
    runs, so the levels must reach `unbracketed:` for the sweep to retry from, and the call must
    return `None` rather than raise.

    Deleting the `try` around the spec build, or narrowing its `except`, fails this test.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("1.0"), Decimal("50000"))
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}BTC-USD",
        {"increment": "0.00000001", "quote_increment": "0.01", "fetched_at": NOW_TS},
    )
    broker = HeldBroker("BTC", available=Decimal("1.0"), total=Decimal("1.0"))

    # A tick that collapses the pair: stop -> 100.01, target -> 100.00.
    result = place_bracket(
        broker,
        repo,
        _config(),
        "BTC-USD",
        Decimal("1.0"),
        Decimal("100.001"),  # stop -- `place_bracket` takes stop BEFORE target
        Decimal("100.009"),  # target
        "turtle_breakout",
        NOW_TS,
    )

    assert result is None
    assert broker.place_calls == [], "a bracket that cannot be expressed must not be sent"
    retry = repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")
    assert retry is not None, "the sweep has nothing to retry from -- the position stays naked"
    assert retry["stop"] == Decimal("100.001"), "the retry must hold the RULE's levels, unrounded"
    assert retry["target"] == Decimal("100.009")


def test_place_bracket_sends_prices_on_the_tick(repo):  # noqa: F811
    """The live rejection, end to end: what reaches the broker carries cents, not
    fourteen digits."""
    _seed_open_position(repo, "PAXG-USD", Decimal("0.0132"), Decimal("4673.23"))
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}PAXG-USD",
        {"increment": "0.00000001", "quote_increment": "0.01", "fetched_at": NOW_TS},
    )
    broker = HeldBroker("PAXG", available=Decimal("0.0132"), total=Decimal("0.0132"))

    place_bracket(
        broker,
        repo,
        _config(),
        "PAXG-USD",
        Decimal("0.0132"),
        LIVE_STOP,  # `place_bracket` takes stop BEFORE target
        LIVE_TARGET,
        "turtle_breakout",
        NOW_TS,
    )

    spec = broker.place_calls[-1]["spec"]
    assert spec.stop_trigger_price == Decimal("4521.77")
    assert spec.take_profit_price == Decimal("5582.02")
