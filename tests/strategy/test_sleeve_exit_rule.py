"""`SleeveExit` -- the sleeve exit monitor's second layer: a whole-sleeve proposal when the
monitor says `breached`, preview-only (spec §7, plan P16 Task 16.1).

Every case is a hand computation on synthetic candles. These are fidelity tests of the rule's
wiring to the monitor (`keel.execution.sleeve_exit.classify`), not a verdict about returns: a
35% trailing stop on an accumulation sleeve is untested, spec §7 proposes no test of it, and the
research freeze (2026-09-27) holds. The levels are R26's spec constants, untuned.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from keel import agent
from keel.execution import sleeve_exit as monitor
from keel.strategy.reduction import Holding, Lot, SellCosts
from keel.strategy.rules import sleeve_exit as rule_mod
from keel.strategy.rules.sleeve_exit import SleeveExit
from keel.types import Candle, Granularity
from tests.strategy.test_dca import _candle

D = Decimal
COSTS = SellCosts(D("0.012"), D("0.0005"), "fallback:config.fees.taker_pct")


def _held() -> Holding:
    return Holding(
        "BTC-USD",
        (
            Lot(1, "dca", 0, D("0.002"), D("100000"), D("0")),
            Lot(2, "dca", 0, D("0.001"), D("90000"), D("0")),
        ),
    )


def _days(*closes: str) -> dict[Granularity, list[Candle]]:
    return {Granularity.ONE_DAY: [_candle(d, c) for d, c in enumerate(closes)]}


_CRASH = _days(*["100000"] * 10, "60000")  # 40% under the high: under 65000, breached
_NEAR = _days(*["100000"] * 10, "67000")  # 3.1% over 65000: near, not breached


# -- the decision -----------------------------------------------------------------------------


def test_it_proposes_the_whole_holding_only_on_breached() -> None:
    rule = SleeveExit("BTC-USD", arms=("drawdown",))
    red = rule.reduce_signal(_held(), _CRASH, COSTS)
    assert red is not None and red.qty == D("0.003") and red.reason == "sleeve_exit"
    assert (red.product_id, red.expected_price, red.ts) == ("BTC-USD", D("60000"), 10 * 86_400)
    assert rule.reduce_signal(_held(), _NEAR, COSTS) is None, "near alerts; only breached proposes"
    assert rule.last_rejection == {"gate": "level", "level": "near"}


def test_the_decision_is_the_monitors_own_classify_at_the_rules_params() -> None:
    """One definition of the levels: the rule asks `classify` at its own params, so the preview,
    the cycle's watch and the proposal can never disagree about a breach."""
    rule = SleeveExit("BTC-USD", dd_pct=D("20"), arms=("drawdown",))
    fall = _days(*["100"] * 10, "79")
    watch = monitor.classify(
        "BTC-USD", fall[Granularity.ONE_DAY], dd_pct=D("20"), arms=("drawdown",)
    )
    assert watch.level == "breached", "fixture: 79 is under the 20% level of 80"
    assert monitor.classify("BTC-USD", fall[Granularity.ONE_DAY]).level != "breached", (
        "fixture: at the default 35% the same fall is not a breach"
    )
    red = rule.reduce_signal(_held(), fall, COSTS)
    assert red is not None
    assert red.trigger == {
        "level": "breached",
        "breached_arms": ["drawdown"],
        "close": "79",
        "dd_level": str(watch.dd_level),
        "sma": None,
        "dd_pct": "20",
        "lookback_days": 200,
        "sma_period": 200,
        "confirm_days": 3,
        "arms": ["drawdown"],
    }


def test_a_confirmed_sma_break_proposes_on_the_sma_arm_alone() -> None:
    """Three closes under a 5-day SMA confirm a break the drawdown arm does not see (a 10% fall
    is far inside 35%)."""
    series = _days(*["100"] * 6, "90", "90", "90")
    rule = SleeveExit("BTC-USD", sma_period=5, confirm_days=3)
    red = rule.reduce_signal(_held(), series, COSTS)
    assert red is not None
    assert red.trigger["breached_arms"] == ["sma"]
    only_dd = SleeveExit("BTC-USD", sma_period=5, confirm_days=3, arms=("drawdown",))
    assert only_dd.reduce_signal(_held(), series, COSTS) is None


def test_too_little_history_proposes_nothing_and_says_so() -> None:
    """Spec §7 failure mode (b): an SMA arm without its bars is not judged, never guessed."""
    rule = SleeveExit("BTC-USD", arms=("sma",))
    assert rule.reduce_signal(_held(), _days(*["100"] * 5, "1"), COSTS) is None
    assert rule.last_rejection == {"gate": "level", "level": "insufficient_history"}


def test_it_decides_on_the_completed_daily_bar_not_a_forming_one() -> None:
    days = [*(_candle(d, "100000") for d in range(10)), _candle(10, "60000")]
    hourly = [_candle(10, "60000")]  # stamped at day 10's open: day 10 has not closed
    rule = SleeveExit("BTC-USD", arms=("drawdown",))
    candles = {Granularity.ONE_DAY: days, Granularity.ONE_HOUR: hourly}
    assert rule.reduce_signal(_held(), candles, COSTS) is None
    assert rule.last_rejection == {"gate": "level", "level": "clear"}


def test_an_empty_holding_proposes_nothing() -> None:
    rule = SleeveExit("BTC-USD")
    assert rule.reduce_signal(Holding("BTC-USD", ()), _CRASH, COSTS) is None
    assert rule.last_rejection == {"gate": "nothing_held"}


def test_no_daily_candles_is_none_with_a_named_reason() -> None:
    rule = SleeveExit("BTC-USD")
    assert rule.reduce_signal(_held(), {}, COSTS) is None
    assert rule.last_rejection == {"gate": "no_daily_candles"}


def test_a_firing_call_clears_the_last_rejection() -> None:
    rule = SleeveExit("BTC-USD", arms=("drawdown",))
    assert rule.reduce_signal(_held(), _NEAR, COSTS) is None
    assert rule.last_rejection is not None
    assert rule.reduce_signal(_held(), _CRASH, COSTS) is not None
    assert rule.last_rejection is None


def test_it_never_enters_and_never_exits_on_a_signal() -> None:
    rule = SleeveExit("BTC-USD")
    assert rule.detect(_CRASH) is None
    assert rule.exit_signal(None, _CRASH) is False  # type: ignore[arg-type]


# -- construction (P15's held item a) ---------------------------------------------------------


@pytest.mark.parametrize("arms", [(), ("trailing",), ("drawdown", "atr"), ("sma", "sma")])
def test_arms_are_a_closed_vocabulary(arms) -> None:
    with pytest.raises(ValueError):
        SleeveExit("BTC-USD", arms=arms)


@pytest.mark.parametrize("bare", ["sma", "drawdown"])
def test_a_bare_string_is_refused_as_one_not_split_into_letters(bare: str) -> None:
    """`tuple("sma")` is `("s", "m", "a")`: a JSON string where a list belongs must be refused
    for what it is, by the constructor AND through the JSON boundary `rules add` and the cycle
    build with (`agent.build_rule_from_params`, whose tuple coercion used to split it)."""
    expected = f"arms must be a list of arm names, not the bare string {bare!r}"
    with pytest.raises(ValueError) as direct:
        SleeveExit("BTC-USD", arms=bare)  # type: ignore[arg-type]
    assert str(direct.value) == expected
    with pytest.raises(ValueError) as built:
        agent.build_rule_from_params("sleeve_exit", {"product_id": "BTC-USD", "arms": bare})
    assert str(built.value) == expected


def test_a_json_list_of_arms_builds_to_a_tuple() -> None:
    rule = agent.build_rule_from_params("sleeve_exit", {"product_id": "X-USD", "arms": ["sma"]})
    assert isinstance(rule, SleeveExit) and rule.params["arms"] == ("sma",)


@pytest.mark.parametrize("name", ["lookback_days", "sma_period", "confirm_days"])
@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "90", None])
def test_the_windows_are_positive_ints(name: str, bad: object) -> None:
    with pytest.raises(ValueError) as refused:
        SleeveExit("BTC-USD", **{name: bad})  # type: ignore[arg-type]
    assert str(refused.value) == f"{name} must be a positive int, got {bad!r}"


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("dd_pct", D("0")),
        ("dd_pct", D("-1")),
        ("dd_pct", D("100")),
        ("dd_pct", D("NaN")),
        ("dd_pct", D("sNaN")),
        ("dd_pct", D("Infinity")),
        ("warn_pct", D("-0.01")),
        ("warn_pct", D("100")),
        ("warn_pct", D("NaN")),
        ("warn_pct", D("-Infinity")),
        ("dd_pct", 35),
        ("warn_pct", "5"),
    ],
)
def test_the_levels_are_finite_decimals_in_range(name: str, bad: object) -> None:
    with pytest.raises(ValueError) as refused:
        SleeveExit("BTC-USD", **{name: bad})  # type: ignore[arg-type]
    assert str(refused.value).startswith(f"{name} must be ")


def test_the_range_ends_that_are_allowed() -> None:
    """`warn_pct` 0 turns the `near` band off (R65); `dd_pct` just under 100 is a level."""
    SleeveExit("BTC-USD", warn_pct=D("0"), dd_pct=D("99.99"), confirm_days=1, sma_period=1)


def test_execution_other_than_preview_is_refused() -> None:
    with pytest.raises(ValueError):
        SleeveExit("BTC-USD", execution="auto")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("params", "named"),
    [
        ({"dd_pct": "0"}, "dd_pct"),
        ({"warn_pct": "NaN"}, "warn_pct"),
        ({"lookback_days": 0}, "lookback_days"),
        ({"arms": ["drawdown", "atr"]}, "arms"),
    ],
)
def test_the_json_boundary_refuses_what_the_constructor_refuses(params, named) -> None:
    """The cycle and `rules add` build through `build_rule_from_params`, which coerces the
    Decimal params from their JSON strings first -- the refusal still names the param."""
    with pytest.raises(ValueError) as refused:
        agent.build_rule_from_params("sleeve_exit", {"product_id": "BTC-USD", **params})
    assert str(refused.value).startswith(f"{named} must be ")


# -- what the rule is -------------------------------------------------------------------------


def test_params_are_the_spec_defaults_r26() -> None:
    assert SleeveExit("BTC-USD").params == {
        "product_id": "BTC-USD",
        "dd_pct": monitor.DEFAULT_DD_PCT,
        "lookback_days": monitor.DEFAULT_LOOKBACK_DAYS,
        "sma_period": monitor.DEFAULT_SMA_PERIOD,
        "confirm_days": monitor.DEFAULT_CONFIRM_DAYS,
        "warn_pct": monitor.DEFAULT_WARN_PCT,
        "arms": ("drawdown", "sma"),
        "execution": "preview",
    }
    assert (D("35"), 200, 200, 3, D("5")) == (
        monitor.DEFAULT_DD_PCT,
        monitor.DEFAULT_LOOKBACK_DAYS,
        monitor.DEFAULT_SMA_PERIOD,
        monitor.DEFAULT_CONFIRM_DAYS,
        monitor.DEFAULT_WARN_PCT,
    )


def test_it_has_no_min_hold_or_cooldown_param() -> None:
    """R13: a whole-sleeve exit is exempt from `min_hold_days`, so the rule has no such knob --
    the pipeline's exemption (`sleeve.MIN_HOLD_EXEMPT_KINDS`) is the one place that says so."""
    params = set(inspect.signature(SleeveExit).parameters)
    assert params == {
        "product_id",
        "dd_pct",
        "lookback_days",
        "sma_period",
        "confirm_days",
        "warn_pct",
        "arms",
        "execution",
        "name",
    }
    assert set(SleeveExit.PARAM_DOCS) == params - {"product_id", "name"}


def test_the_monitor_kwargs_are_classifys_keywords() -> None:
    rule = SleeveExit("X-USD", dd_pct=D("20"), sma_period=50, arms=["sma"])  # type: ignore[arg-type]
    assert rule.monitor_kwargs() == {
        "dd_pct": D("20"),
        "lookback_days": 200,
        "sma_period": 50,
        "confirm_days": 3,
        "warn_pct": D("5"),
        "arms": ("sma",),
    }
    assert set(rule.monitor_kwargs()) == set(inspect.signature(monitor.classify).parameters) - {
        "product_id",
        "daily",
        "previous",
    }


def test_it_is_a_registered_sleeve_sell_accumulating_rule_with_nothing_to_sweep() -> None:
    rule = SleeveExit("BTC-USD")
    assert (rule.promotion_class, rule.accumulates, rule.name) == (
        "sleeve_sell",
        True,
        "sleeve_exit",
    )
    assert rule.param_space() == ()
    assert agent.RULE_REGISTRY["sleeve_exit"] is SleeveExit
    assert SleeveExit.tuple_params == ("arms",)
    assert set(SleeveExit.decimal_params) == {"dd_pct", "warn_pct"}


# -- the monitor's selector (R69 retired: the registered kind, not a name) ---------------------


class _NamedLikeSleeveExit:
    """Duck-typed as R69 read it: named `sleeve_exit`, on the product, with params. Not the
    registered class, so it no longer sets the levels."""

    name = "sleeve_exit"

    def __init__(self, product_id: str, rule_id: int) -> None:
        self.product_id, self.rule_id = product_id, rule_id
        self.params = {"product_id": product_id, "dd_pct": "10"}


def _exit(product: str, rule_id: int, **params: object) -> SleeveExit:
    rule = SleeveExit(product, **params)  # type: ignore[arg-type]
    rule.rule_id = rule_id
    return rule


def test_monitor_params_are_the_defaults_without_a_sleeve_exit_rule() -> None:
    impostor = (_NamedLikeSleeveExit("PAXG-USD", 1), "live")
    assert rule_mod.monitor_rule([impostor], "PAXG-USD") is None
    assert rule_mod.monitor_params([impostor], "PAXG-USD") == {}


def test_monitor_params_read_the_products_sleeve_exit_rule_live_first() -> None:
    """R26: the rule's params override the constants. A live rule is the one the cycle runs for
    real, so it wins over a paper one; the lowest id breaks a tie. Another product's rule is not
    this product's. The params are the constructed rule's -- already validated and typed."""
    paper = _exit("PAXG-USD", 7, dd_pct=D("20"), arms=("drawdown",))
    chosen = _exit("PAXG-USD", 8, dd_pct=D("30"), confirm_days=2)
    rules = [
        (paper, "paper"),
        (_exit("BTC-USD", 2, dd_pct=D("50")), "live"),
        (_exit("PAXG-USD", 9, dd_pct=D("25"), lookback_days=90), "live"),
        (chosen, "live"),
    ]
    assert rule_mod.monitor_rule(rules, "PAXG-USD") == (chosen, "live")
    assert rule_mod.monitor_params(rules, "PAXG-USD") == chosen.monitor_kwargs()
    assert rule_mod.monitor_params(rules[:1], "PAXG-USD") == paper.monitor_kwargs()
    assert rule_mod.monitor_params(rules, "PAXG-USD")["dd_pct"] == D("30")
