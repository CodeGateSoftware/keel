"""`ReverseDca` -- a scheduled distribution, preview-only (spec §6, plan P9 Task 9.1).

Every case is a hand computation on synthetic candles: these are fidelity tests of the rule,
not a verdict about returns (spec §6, "Evidence status"; the research freeze holds).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.reduction import Holding, Lot, SellCosts
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.reverse_dca import ReverseDca
from keel.types import Granularity
from tests.strategy.test_dca import _candle

D = Decimal
COSTS = SellCosts(D("0.012"), D("0.0005"), "fallback:config.fees.taker_pct")


def _rule(**over):
    kw = dict(product_id="BTC-USD", target_usd=D("100"), min_price_floor=D("60000"))
    kw.update(over)
    return ReverseDca(**kw)


def _held(qty: str = "0.01") -> Holding:
    return Holding("BTC-USD", (Lot(1, "dca", 0, D(qty), D("50000"), D("0")),))


def _days(last_day: int, price: str = "100000", high: str | None = None, n: int = 5):
    return {
        Granularity.ONE_DAY: [
            _candle(d, price, high) for d in range(last_day - n + 1, last_day + 1)
        ]
    }


def test_it_fires_on_the_same_epoch_aligned_days_as_dca() -> None:
    fired = {}
    for day in (60, 61, 89, 90):
        fires = _rule().reduce_signal(_held(), _days(day), COSTS) is not None
        assert fires == (Dca("BTC-USD", cadence_days=30).detect(_days(day)) is not None), day
        fired[day] = fires
    # Not vacuous: both answers occur, so agreement is agreement on cadence, not on "never".
    assert fired == {60: True, 61: False, 89: False, 90: True}


def test_off_cadence_names_its_gate() -> None:
    rule = _rule()
    assert rule.reduce_signal(_held(), _days(61), COSTS) is None
    assert rule.last_rejection == {"gate": "off_cadence"}


def test_gross_is_sized_from_the_net_target() -> None:
    red = _rule().reduce_signal(_held(), _days(60), COSTS)
    gross = D("100") / (1 - D("0.012") - D("0.0005"))
    assert red is not None
    assert red.qty == gross / D("100000")
    assert (red.reason, red.expected_price, red.product_id) == (
        "reverse_dca",
        D("100000"),
        "BTC-USD",
    )
    assert red.ts == 60 * 86_400
    assert red.trigger == {
        "cadence_day": 60,
        "close": "100000",
        "high": "100000",
        "target_usd": "100",
        "gross_usd": str(gross),
        "fee_pct": "0.012",
        "slippage_pct": "0.0005",
        "fee_source": "fallback:config.fees.taker_pct",
    }


def test_gross_follows_the_costs_the_caller_resolved() -> None:
    """The rule sizes at whatever rate it is handed (plan R6): a cheaper fee sizes a smaller
    gross for the same net, and the trigger records which figure it was."""
    cheap = SellCosts(D("0.006"), D("0"), "venue_preview")
    red = _rule().reduce_signal(_held(), _days(60), cheap)
    assert red is not None
    assert red.qty == (D("100") / (1 - D("0.006"))) / D("100000")
    assert red.trigger["fee_source"] == "venue_preview"
    fallback = _rule().reduce_signal(_held(), _days(60), COSTS)
    assert fallback is not None and red.qty < fallback.qty


@pytest.mark.parametrize("close,fires", [("60000", True), ("59999.99", False)])
def test_the_price_floor_at_its_boundary(close, fires) -> None:
    rule = _rule()
    assert (rule.reduce_signal(_held(), _days(60, close), COSTS) is not None) is fires
    if not fires:
        assert rule.last_rejection == {
            "gate": "price_floor",
            "close": D(close),
            "floor": D("60000"),
        }


@pytest.mark.parametrize("close,fires", [("75000", True), ("74999.99", False)])
def test_the_drawdown_gate_at_its_boundary(close, fires) -> None:
    """25% below a 100000 high is 75000."""
    candles = _days(60, close)
    candles[Granularity.ONE_DAY][0] = _candle(56, close, high="100000")
    rule = _rule(min_price_floor=D("1"))
    assert (rule.reduce_signal(_held(), candles, COSTS) is not None) is fires
    if not fires:
        assert rule.last_rejection == {
            "gate": "drawdown",
            "close": D(close),
            "level": D("75000"),
        }


def test_the_drawdown_high_is_read_over_lookback_days_only() -> None:
    """A high older than `lookback_days` completed bars does not gate: with a 3-day lookback the
    100000 high on day 56 is out of the window (days 58-60), so a 50000 close sells."""
    candles = _days(60, "50000")
    candles[Granularity.ONE_DAY][0] = _candle(56, "50000", high="100000")
    assert (
        _rule(min_price_floor=D("1"), lookback_days=3).reduce_signal(_held(), candles, COSTS)
        is not None
    )
    assert (
        _rule(min_price_floor=D("1"), lookback_days=5).reduce_signal(_held(), candles, COSTS)
        is None
    )


def test_never_more_than_holding_minus_floor_qty_and_none_at_the_floor() -> None:
    red = _rule(target_usd=D("100000")).reduce_signal(_held("0.01"), _days(60), COSTS)
    assert red is not None and red.qty == D("0.01")
    small = _rule(floor_qty=D("0.009"), target_usd=D("100000")).reduce_signal(
        _held("0.01"), _days(60), COSTS
    )
    assert small is not None and small.qty == D("0.001")
    at_floor = _rule(floor_qty=D("0.01"))
    assert at_floor.reduce_signal(_held("0.01"), _days(60), COSTS) is None
    assert at_floor.last_rejection == {"gate": "floor_qty", "held": D("0.01")}


def test_no_carry_forward_after_a_gated_cadence_day() -> None:
    rule = _rule()
    assert rule.reduce_signal(_held(), _days(60, "50000"), COSTS) is None  # below the floor
    later = rule.reduce_signal(_held(), _days(90), COSTS)
    assert later is not None
    assert later.qty == (D("100") / (1 - D("0.012") - D("0.0005"))) / D("100000"), (
        "one target, not two"
    )


def test_no_daily_candles_is_none_with_a_named_reason() -> None:
    rule = _rule()
    assert rule.reduce_signal(_held(), {}, COSTS) is None
    assert rule.last_rejection == {"gate": "no_daily_candles"}


def test_a_firing_call_clears_the_last_rejection() -> None:
    rule = _rule()
    rule.reduce_signal(_held(), _days(61), COSTS)
    assert rule.last_rejection is not None
    assert rule.reduce_signal(_held(), _days(60), COSTS) is not None
    assert rule.last_rejection is None


def test_it_never_enters_and_never_exits_on_a_signal() -> None:
    assert _rule().detect(_days(60)) is None
    assert _rule().exit_signal(None, _days(60)) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad",
    [
        dict(target_usd=D("0")),
        dict(target_usd=D("-1")),
        dict(cadence_days=0),
        dict(min_price_floor=D("0")),
        dict(max_drawdown_pct=D("0")),
        dict(max_drawdown_pct=D("100.01")),
        dict(lookback_days=0),
        dict(floor_qty=D("-1")),
        dict(min_hold_days=-1),
        dict(execution="auto"),
    ],
)
def test_construction_refuses_nonsense(bad) -> None:
    with pytest.raises(ValueError):
        _rule(**bad)


@pytest.mark.parametrize(
    "edge",
    [dict(max_drawdown_pct=D("100")), dict(floor_qty=D("0")), dict(min_hold_days=0)],
)
def test_construction_accepts_the_inclusive_edges(edge) -> None:
    _rule(**edge)


def test_a_floor_above_the_current_close_is_allowed_it_is_a_choice() -> None:
    assert _rule(min_price_floor=D("1000000")).params["min_price_floor"] == D("1000000")


def test_params_carry_every_field_the_pipeline_reads() -> None:
    """`sleeve.sleeve_refusal` reads `min_hold_days` off `rule.params` (R12), so it must be
    there, with the default the spec states."""
    assert _rule().params == {
        "product_id": "BTC-USD",
        "target_usd": D("100"),
        "min_price_floor": D("60000"),
        "cadence_days": 30,
        "max_drawdown_pct": D("25"),
        "lookback_days": 200,
        "floor_qty": D("0"),
        "min_hold_days": 30,
        "execution": "preview",
    }


def test_it_is_a_sleeve_sell_accumulating_rule() -> None:
    from keel.strategy import promotion

    assert ReverseDca.promotion_class == promotion.SLEEVE_SELL
    assert ReverseDca.accumulates is True
    assert set(ReverseDca.decimal_params) == {
        "target_usd",
        "min_price_floor",
        "max_drawdown_pct",
        "floor_qty",
    }
    assert set(ReverseDca.PARAM_DOCS) == set(_rule().params) - {"product_id"}
