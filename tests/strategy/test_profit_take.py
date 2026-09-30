"""`ProfitTake` -- a trim of a held sleeve on gain over its average entry, preview-only (spec §4,
plan P14 Task 14.1).

Every case is a hand computation on synthetic candles: these are fidelity tests of the rule's
arithmetic, not a verdict about returns. The gain-over-entry trim is UNTESTED as a policy (spec
§4, "Evidence status"), and nothing here is evidence for or against it; the research freeze
(2026-09-27) holds.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from keel.strategy.reduction import Holding, Lot, SellCosts
from keel.strategy.rules.profit_take import ProfitTake
from keel.types import Candle, Granularity
from tests.strategy.test_dca import _candle

D = Decimal
COSTS = SellCosts(D("0.012"), D("0.0005"), "fallback:config.fees.taker_pct")


def _held(fill: str = "100000", fee: str = "0.45", qty: str = "0.01") -> Holding:
    return Holding("BTC-USD", (Lot(1, "dca", 0, D(qty), D(fill), D(fee)),))


def _day(close: str, day: int = 60) -> dict[Granularity, list[Candle]]:
    return {Granularity.ONE_DAY: [_candle(day, close)]}


def test_the_trigger_is_gain_over_vwae_including_entry_fees() -> None:
    held = _held()  # vwae = (0.01 x 100000 + 0.45) / 0.01 = 100045
    assert held.vwae == D("100045")
    trigger = held.vwae * D("1.25")
    rule = ProfitTake("BTC-USD", min_net_usd=D("0"))
    assert rule.reduce_signal(held, _day(str(trigger)), COSTS) is not None
    assert rule.reduce_signal(held, _day(str(trigger - D("0.01"))), COSTS) is None
    assert rule.last_rejection == {
        "gate": "gain",
        "close": trigger - D("0.01"),
        "trigger_price": trigger,
    }


def test_without_the_entry_fee_the_same_close_would_trigger() -> None:
    """The fee is IN the basis (spec Q4): a close that clears 25% over the bare fill price does
    not clear 25% over the fee-inclusive average."""
    close = D("100000") * D("1.25")
    rule = ProfitTake("BTC-USD", min_net_usd=D("0"))
    assert rule.reduce_signal(_held(fee="0"), _day(str(close)), COSTS) is not None
    assert rule.reduce_signal(_held(fee="0.45"), _day(str(close)), COSTS) is None


def test_the_size_is_trim_pct_of_the_holding() -> None:
    red = ProfitTake("BTC-USD", trim_pct=D("15"), min_net_usd=D("0")).reduce_signal(
        _held(), _day("200000"), COSTS
    )
    assert red is not None and red.qty == D("0.0015")


@pytest.mark.parametrize("trim", ["10", "20"])
def test_the_trim_bounds_are_inclusive_and_size_the_sale(trim: str) -> None:
    red = ProfitTake("BTC-USD", trim_pct=D(trim), min_net_usd=D("0")).reduce_signal(
        _held(), _day("200000"), COSTS
    )
    assert red is not None and red.qty == D("0.01") * D(trim) / 100


def test_the_fee_gate_at_its_boundary() -> None:
    held, close = _held(), D("130000")
    qty = held.qty * D("0.15")
    assert held.vwae is not None
    net = qty * (close * (1 - COSTS.slippage_pct) - held.vwae) - qty * close * COSTS.fee_pct
    assert net == D("42.495"), "hand computation: 0.0015 x (129935 - 100045) - 2.34"
    assert ProfitTake("BTC-USD", min_net_usd=net).reduce_signal(held, _day(str(close)), COSTS)
    rule = ProfitTake("BTC-USD", min_net_usd=net + D("0.01"))
    assert rule.reduce_signal(held, _day(str(close)), COSTS) is None
    assert rule.last_rejection == {
        "gate": "fee_gate",
        "trigger_price": held.vwae * D("1.25"),
        "qty": qty,
        "fee_usd": qty * close * COSTS.fee_pct,
        "net_usd": net,
        "min_net_usd": net + D("0.01"),
    }


def test_the_fee_gate_prices_at_the_costs_the_caller_resolved() -> None:
    """A dearer fee closes the same gate: the rule reads `SellCosts`, never a literal rate."""
    held, close = _held(), D("130000")
    cheap = SellCosts(D("0.001"), D("0"), "test:cheap")
    dear = SellCosts(D("0.1"), D("0"), "test:dear")
    rule = ProfitTake("BTC-USD", min_net_usd=D("30"))
    assert rule.reduce_signal(held, _day(str(close)), cheap) is not None
    assert rule.reduce_signal(held, _day(str(close)), dear) is None
    assert rule.last_rejection is not None and rule.last_rejection["gate"] == "fee_gate"


def test_a_firing_reduction_carries_its_arithmetic_in_the_trigger() -> None:
    held, close = _held(), D("130000")
    red = ProfitTake("BTC-USD").reduce_signal(held, _day(str(close)), COSTS)
    assert red is not None
    qty = D("0.0015")
    assert (red.product_id, red.qty, red.reason, red.expected_price, red.ts) == (
        "BTC-USD",
        qty,
        "profit_take",
        close,
        60 * 86_400,
    )
    assert red.trigger == {
        "close": "130000",
        "vwae": "100045",
        "gain_pct": "25",
        "trigger_price": str(D("100045") * D("1.25")),
        "trim_pct": "15",
        "fee_usd": str(qty * close * COSTS.fee_pct),
        "net_usd": red.trigger["net_usd"],
        "min_net_usd": "5",
        "fee_pct": "0.012",
        "slippage_pct": "0.0005",
        "fee_source": "fallback:config.fees.taker_pct",
    }
    assert D(red.trigger["net_usd"]) == D("42.495")  # the fee gate's hand computation, above


def test_it_decides_on_the_completed_daily_bar_not_a_forming_one() -> None:
    """With an hourly series inside the newest daily bar, that bar is still forming
    (`completed_days`): the rule decides on the one before it."""
    days = [_candle(59, "100000"), _candle(60, "200000")]
    hourly = [_candle(60, "200000")]  # stamped at day 60's open: day 60 has not closed
    rule = ProfitTake("BTC-USD", min_net_usd=D("0"))
    candles = {Granularity.ONE_DAY: days, Granularity.ONE_HOUR: hourly}
    assert rule.reduce_signal(_held(), candles, COSTS) is None
    assert rule.last_rejection is not None and rule.last_rejection["close"] == D("100000")


@pytest.mark.parametrize(
    "bad",
    [
        dict(trim_pct=D("9.99")),
        dict(trim_pct=D("20.01")),
        dict(gain_pct=D("0")),
        dict(gain_pct=D("-1")),
        dict(min_net_usd=D("-1")),
        dict(cooldown_days=-1),
        dict(min_hold_days=-1),
        dict(execution="auto"),
    ],
)
def test_construction_refuses_nonsense(bad) -> None:
    with pytest.raises(ValueError):
        ProfitTake("BTC-USD", **bad)


def test_an_empty_holding_proposes_nothing() -> None:
    rule = ProfitTake("BTC-USD")
    assert rule.reduce_signal(Holding("BTC-USD", ()), _day("1"), COSTS) is None
    assert rule.last_rejection == {"gate": "nothing_held"}


def test_no_daily_candles_is_none_with_a_named_reason() -> None:
    rule = ProfitTake("BTC-USD")
    assert rule.reduce_signal(_held(), {}, COSTS) is None
    assert rule.last_rejection == {"gate": "no_daily_candles"}


def test_a_firing_call_clears_the_last_rejection() -> None:
    rule = ProfitTake("BTC-USD", min_net_usd=D("0"))
    assert rule.reduce_signal(_held(), _day("1"), COSTS) is None
    assert rule.last_rejection is not None
    assert rule.reduce_signal(_held(), _day("200000"), COSTS) is not None
    assert rule.last_rejection is None


def test_it_never_enters_and_never_exits_on_a_signal() -> None:
    rule = ProfitTake("BTC-USD")
    assert rule.detect(_day("200000")) is None
    assert rule.exit_signal(None, _day("200000")) is False  # type: ignore[arg-type]


def test_params_carry_every_field_the_pipeline_reads() -> None:
    """`sleeve.sleeve_refusal` reads `min_hold_days` (R12) and `cooldown_days` (R15) off
    `rule.params`, so both must be there, at their spec defaults."""
    assert ProfitTake("BTC-USD").params == {
        "product_id": "BTC-USD",
        "gain_pct": D("25"),
        "trim_pct": D("15"),
        "min_net_usd": D("5"),
        "cooldown_days": 30,
        "min_hold_days": 30,
        "execution": "preview",
    }


def test_the_drift_trigger_is_not_a_param() -> None:
    """Spec §4: weight drift is `band_rebalance`'s trigger (§5), kept apart so a trim reads as
    one thing. The constructor takes exactly the spec's params, and nothing else."""
    params = set(inspect.signature(ProfitTake).parameters)
    assert params == {
        "product_id",
        "gain_pct",
        "trim_pct",
        "min_net_usd",
        "cooldown_days",
        "min_hold_days",
        "execution",
        "name",
    }
    assert set(ProfitTake.PARAM_DOCS) == params - {"product_id", "name"}


def test_it_is_a_sleeve_sell_accumulating_rule_with_nothing_to_sweep() -> None:
    rule = ProfitTake("BTC-USD")
    assert (rule.promotion_class, rule.accumulates, rule.name) == (
        "sleeve_sell",
        True,
        "profit_take",
    )
    # The research freeze: no parameter space a sweep could explore.
    assert rule.param_space() == ()
