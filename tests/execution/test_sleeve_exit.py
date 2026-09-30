"""The pure sleeve exit monitor (#857, plan P15 Task 15.1, R26; spec §7).

`classify` reads completed daily bars and says where a product stands against its two structural
exit levels -- a trailing drawdown from the lookback high and a confirmed close under the SMA --
as `clear`, `near`, `breached` or `insufficient_history`. It places nothing and reads no state: the
cycle (`agent._watch_sleeve_exits`) owns the state, the transitions and the poll.
"""

from __future__ import annotations

import ast
from decimal import Decimal as D
from pathlib import Path

import pytest

from keel.execution import sleeve_exit
from keel.execution.sleeve_exit import (
    DEFAULT_CONFIRM_DAYS,
    DEFAULT_DD_PCT,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_SMA_PERIOD,
    DEFAULT_WARN_PCT,
    STATE_PREFIX,
    ExitWatch,
    classify,
)
from keel.types import Candle

DAY = 86_400


def _candle(day: int, close: str, high: str | None = None) -> Candle:
    c = D(close)
    return Candle(ts=day * DAY, open=c, high=D(high) if high else c, low=c, close=c, volume=D("1"))


def _flat(n: int, price: str = "100", high: str | None = None) -> list[Candle]:
    return [_candle(d, price, high) for d in range(n)]


def test_the_default_levels_are_the_specs() -> None:
    """R26: the spec's §7 values, as module constants -- no config key, no tuning."""
    assert (
        DEFAULT_DD_PCT,
        DEFAULT_LOOKBACK_DAYS,
        DEFAULT_SMA_PERIOD,
        DEFAULT_CONFIRM_DAYS,
        DEFAULT_WARN_PCT,
    ) == (D("35"), 200, 200, 3, D("5"))
    assert STATE_PREFIX == "sleeve_exit:"


def test_the_drawdown_arm_at_above_and_below_its_level() -> None:
    """high 100, dd 35% -> level 65. At the level is not a breach; a cent under it is."""
    base = _flat(10)
    at = classify("BTC-USD", [*base, _candle(10, "65")], arms=("drawdown",), warn_pct=D("0"))
    above = classify("BTC-USD", [*base, _candle(10, "66")], arms=("drawdown",), warn_pct=D("0"))
    below = classify("BTC-USD", [*base, _candle(10, "64.99")], arms=("drawdown",), warn_pct=D("0"))
    assert (at.level, at.dd_level, at.breached_arms) == ("clear", D("65"), ())
    assert (above.level, above.breached_arms) == ("clear", ())
    assert (below.level, below.breached_arms) == ("breached", ("drawdown",))
    assert below.close == D("64.99") and below.ts == 10 * DAY


def test_the_drawdown_high_is_the_bar_high_within_the_lookback_only() -> None:
    """A spike older than `lookback_days` no longer sets the trailing high."""
    series = [_candle(0, "100", high="1000"), *[_candle(d, "100") for d in range(1, 12)]]
    inside = classify("BTC-USD", series, arms=("drawdown",), lookback_days=12)
    outside = classify("BTC-USD", series, arms=("drawdown",), lookback_days=11)
    assert inside.dd_level == D("650") and inside.level == "breached"
    assert outside.dd_level == D("65") and outside.level == "clear"


def test_the_sma_arm_needs_confirm_days_consecutive_closes_below() -> None:
    series = [*_flat(200, "100"), _candle(200, "90"), _candle(201, "90")]
    two = classify("BTC-USD", series, arms=("sma",), confirm_days=3)
    assert two.level != "breached" and two.breached_arms == ()
    series.append(_candle(202, "90"))
    three = classify("BTC-USD", series, arms=("sma",), confirm_days=3)
    assert (three.level, three.breached_arms) == ("breached", ("sma",))
    # the SMA reported is the one ending at the last bar: 197 closes of 100 and three of 90
    assert three.sma == (D("100") * 197 + D("90") * 3) / 200


def test_one_close_back_above_the_sma_resets_the_confirmation() -> None:
    series = [*_flat(200, "100"), _candle(200, "90"), _candle(201, "101"), _candle(202, "90")]
    assert classify("BTC-USD", series, arms=("sma",), confirm_days=2).level != "breached"


def test_too_little_history_for_the_sma_is_reported_not_guessed() -> None:
    """Spec §7 failure mode (b): the arm needs `sma_period + confirm_days - 1` bars."""
    assert classify("BTC-USD", _flat(150), arms=("sma",)).level == "insufficient_history"
    assert classify("BTC-USD", _flat(201), arms=("sma",)).level == "insufficient_history"
    judged = classify("BTC-USD", _flat(202), arms=("sma",))
    assert judged.level == "near" and judged.sma == D("100"), "a close AT its SMA is within 5%"
    assert classify("BTC-USD", _flat(150), arms=("sma",)).sma is None


def test_an_insufficient_arm_does_not_hide_the_other() -> None:
    """Only when EVERY requested arm is insufficient is the product unjudged."""
    watch = classify("BTC-USD", [*_flat(10), _candle(10, "60")])
    assert (watch.level, watch.breached_arms, watch.sma) == ("breached", ("drawdown",), None)


def test_both_arms_breached_are_named_in_arm_order() -> None:
    series = [*_flat(200, "100"), *[_candle(d, "50") for d in range(200, 203)]]
    watch = classify("BTC-USD", series)
    assert watch.breached_arms == ("drawdown", "sma")


def test_near_has_hysteresis_in_at_warn_pct_out_at_twice_it() -> None:
    """dd level 65; warn 5% -> in at <= 68.25, out above 71.5 (spec §7 failure mode d)."""
    base = _flat(10)

    def level(close: str, previous=None) -> str:
        return classify(
            "BTC-USD", [*base, _candle(10, close)], arms=("drawdown",), previous=previous
        ).level

    assert level("68.25") == "near"
    assert level("68.26") == "clear"
    assert level("70", previous="near") == "near"
    assert level("70", previous="clear") == "clear"
    assert level("70", previous=None) == "clear"
    assert level("71.5", previous="near") == "near"
    assert level("72", previous="near") == "clear"


def test_a_recovery_from_a_breach_leaves_through_the_same_wide_band() -> None:
    """Coming back up over the level is the same flap as leaving `near`: a close a hair over it
    must not read `clear` and then `breached` again the next day."""
    base = _flat(10)
    back = classify("BTC-USD", [*base, _candle(10, "70")], arms=("drawdown",), previous="breached")
    assert back.level == "near"


def test_a_close_under_the_sma_but_not_yet_confirmed_is_near() -> None:
    series = [*_flat(200, "100"), _candle(200, "90")]
    series.insert(0, _candle(-1, "100"))
    watch = classify("BTC-USD", series, arms=("sma",), confirm_days=3)
    assert watch.level == "near"


def test_zero_warn_pct_turns_the_near_band_off() -> None:
    base = _flat(10)
    watch = classify("BTC-USD", [*base, _candle(10, "65.01")], arms=("drawdown",), warn_pct=D("0"))
    assert watch.level == "clear"


def test_no_candles_is_insufficient_history() -> None:
    watch = classify("PAXG-USD", [])
    assert watch == ExitWatch("PAXG-USD", "insufficient_history", None, None, None, (), None)


def test_previous_rides_on_the_result() -> None:
    watch = classify("BTC-USD", _flat(10), arms=("drawdown",), previous="near")
    assert (watch.level, watch.previous) == ("clear", "near")


@pytest.mark.parametrize("arms", [(), ("trailing",), ("drawdown", "drawdown")])
def test_an_unknown_or_empty_arm_set_is_refused(arms) -> None:
    with pytest.raises(ValueError, match="arms"):
        classify("BTC-USD", _flat(10), arms=arms)


def test_the_module_is_pure() -> None:
    """No repository, no broker, no clock: the cycle and the CLI hand it the bars. Pinned on the
    module's imports -- the only keel modules it may import are the value types."""
    tree = ast.parse(Path(sleeve_exit.__file__).read_text())
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    keel_imports = {name for name in imported if name and name.startswith("keel")}
    assert keel_imports == {"keel.types"}
    assert "time" not in imported


class _Rule:
    def __init__(self, name: str, product_id: str, rule_id: int, **params: object) -> None:
        self.name, self.product_id, self.rule_id = name, product_id, rule_id
        self.params = {"product_id": product_id, **params}


def test_monitor_params_are_the_defaults_without_a_sleeve_exit_rule() -> None:
    rules = [(_Rule("reverse_dca", "PAXG-USD", 1, dd_pct="10"), "live")]
    assert sleeve_exit.monitor_params(rules, "PAXG-USD") == {}


def test_monitor_params_read_the_products_sleeve_exit_rule_live_first() -> None:
    """R26: the rule's params override the constants, coerced to `classify`'s types. A live
    rule is the one the cycle runs for real, so it wins over a paper one; the lowest id breaks a
    tie. Another product's rule is not this product's."""
    rules = [
        (_Rule("sleeve_exit", "PAXG-USD", 7, dd_pct="20", arms=["drawdown"]), "paper"),
        (_Rule("sleeve_exit", "BTC-USD", 2, dd_pct="50"), "live"),
        (_Rule("sleeve_exit", "PAXG-USD", 9, dd_pct="25", lookback_days="90"), "live"),
        (_Rule("sleeve_exit", "PAXG-USD", 8, dd_pct="30", confirm_days=2), "live"),
    ]
    assert sleeve_exit.monitor_params(rules, "PAXG-USD") == {
        "dd_pct": D("30"),
        "confirm_days": 2,
    }
    assert sleeve_exit.monitor_params(rules[:1], "PAXG-USD") == {
        "dd_pct": D("20"),
        "arms": ("drawdown",),
    }
    both = sleeve_exit.monitor_params(
        [
            (
                _Rule("sleeve_exit", "X-USD", 1, sma_period=50, warn_pct="2.5", lookback_days=10),
                "live",
            )
        ],
        "X-USD",
    )
    assert both == {"sma_period": 50, "warn_pct": D("2.5"), "lookback_days": 10}
