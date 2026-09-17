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


# -- the ratchet path, which is the one that matters most ---------------------------------------


def test_a_rolled_stop_is_also_quantized(repo):  # noqa: F811
    """`_roll_stop` is the SECOND `_bracket_spec` call site, and it was missed once already.

    It is the trailing ratchet -- the thing that tightens a stop as a trade runs -- so leaving it
    unquantized would keep #802 alive on the protective path that fires most often, while
    `place_bracket` looked fixed.
    """
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}BTC-USD",
        {"increment": "0.00000001", "quote_increment": "0.01", "fetched_at": NOW_TS},
    )
    broker = HeldBroker("BTC", available=Decimal("0.01"), total=Decimal("0.01"))
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    executor.roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("50000.004999"),  # a break-even stop off the tick
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    rolled = broker.place_calls[-1]["spec"]
    assert rolled.stop_trigger_price == Decimal("50000.01"), "the rolled stop reached the tick"
    assert str(rolled.stop_trigger_price) == "50000.01"


# -- _price_increment_for ----------------------------------------------------------------------


class _InstrumentBroker:
    """A broker that answers `get_instrument` and counts how often it is asked."""

    def __init__(self, quote_increment: str | None = "0.01") -> None:
        self.calls = 0
        self._quote_increment = quote_increment

    def get_instrument(self, product_id: str):  # noqa: ANN201
        from keel_broker_api.results import Instrument

        self.calls += 1
        return Instrument(
            product_id=product_id,
            base_increment=Decimal("0.00000001"),
            quote_increment=(
                None if self._quote_increment is None else Decimal(self._quote_increment)
            ),
        )


def test_price_increment_is_read_from_the_cached_record(repo):  # noqa: F811
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}PAXG-USD",
        {"increment": "0.00000001", "quote_increment": "0.01", "fetched_at": NOW_TS},
    )
    broker = _InstrumentBroker()

    assert executor._price_increment_for(broker, repo, "PAXG-USD", NOW_TS) == CENT
    assert broker.calls == 0, "a warm cache must not reach the venue inside the order path"


def test_both_increments_share_one_fetch(repo):  # noqa: F811
    """The per-product read exists to keep ONE venue round-trip in the order path. Asking for the
    price tick after the size one must not add a second."""
    broker = _InstrumentBroker()

    executor._base_increment_for(broker, repo, "PAXG-USD", NOW_TS)
    assert executor._price_increment_for(broker, repo, "PAXG-USD", NOW_TS) == CENT
    assert broker.calls == 1


def test_an_unparseable_quote_increment_reads_unknown(repo):  # noqa: F811
    """Unknown must never become a guessed `0.01`.

    This test used to assert that a record with the key ABSENT also read unknown. That was the
    defect, not the contract -- see `test_a_record_written_before_the_field_existed_is_refetched`
    below, and the live failure it names. What remains true is the narrower claim: a value that
    is present and unusable is unknown, and is not repaired by guessing the common tick.
    """
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}PAXG-USD",
        {"increment": "0.00000001", "quote_increment": "not-a-number", "fetched_at": NOW_TS},
    )

    assert executor._price_increment_for(_InstrumentBroker(), repo, "PAXG-USD", NOW_TS) is None


def test_a_venue_that_reports_no_tick_reads_unknown(repo):  # noqa: F811
    assert (
        executor._price_increment_for(
            _InstrumentBroker(quote_increment=None), repo, "X-USD", NOW_TS
        )
        is None
    )


def test_no_broker_reads_unknown_rather_than_raising(repo):  # noqa: F811
    """Paper mode passes no broker. `_base_increment_for` is documented as never raising, and
    this must not be the function that reintroduces one into the order path."""
    assert executor._price_increment_for(None, repo, "PAXG-USD", NOW_TS) is None


# -- a cache record that predates the field must not wait out its TTL --------------------------


def test_a_record_written_before_the_field_existed_is_refetched(repo):  # noqa: F811
    """The live failure this covers (2026-09-17, keel-live.db).

    `base_increment:PAXG-USD` was written by a pre-#802 build 24 hours before the cycle, so the
    7-day TTL counted it FRESH and `_price_increment_for` returned the absent key as "unknown".
    Prices went out unrounded and the venue rejected them -- for up to a week, on exactly the
    deployment the fix was cut for, while the position sat unprotected.

    "Unknown" is right when nothing knows the tick. It is wrong when the record simply predates
    the question, and the two are distinguishable: a record that was WRITTEN with knowledge of
    the field always carries the key, explicitly null when the venue reports none.
    """
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}PAXG-USD",
        {"increment": "0.00001", "fetched_at": NOW_TS},  # no `quote_increment` key at all
    )
    broker = _InstrumentBroker()

    assert executor._price_increment_for(broker, repo, "PAXG-USD", NOW_TS) == CENT
    assert broker.calls == 1, "an incomplete record must be refetched, not waited out"


def test_the_refetch_happens_once_and_then_the_record_is_complete(repo):  # noqa: F811
    """Self-healing, not a fetch on every cycle."""
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}PAXG-USD",
        {"increment": "0.00001", "fetched_at": NOW_TS},
    )
    broker = _InstrumentBroker()

    executor._price_increment_for(broker, repo, "PAXG-USD", NOW_TS)
    executor._price_increment_for(broker, repo, "PAXG-USD", NOW_TS)

    assert broker.calls == 1


def test_a_venue_that_reports_no_tick_is_not_refetched_every_cycle(repo):  # noqa: F811
    """The trap in the obvious fix.

    "Refetch when the key is missing" would refetch FOREVER for a product whose venue genuinely
    reports no tick, adding a venue round-trip to every order -- the latency the per-product read
    exists to avoid. The key is therefore always written, explicitly null, so "absent" means
    "written before the field" and nothing else.
    """
    broker = _InstrumentBroker(quote_increment=None)

    assert executor._price_increment_for(broker, repo, "X-USD", NOW_TS) is None
    assert executor._price_increment_for(broker, repo, "X-USD", NOW_TS) is None

    assert broker.calls == 1, "a venue's honest 'no tick' must be cached, not re-asked"
    record = repo.get_state(f"{executor.BASE_INCREMENT_PREFIX}X-USD")
    assert "quote_increment" in record, "the key must be written even when the value is unknown"
    assert record["quote_increment"] is None
