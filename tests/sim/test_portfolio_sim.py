"""Tests for `keel.sim.portfolio_sim` -- the bar-by-bar, multi-asset portfolio simulator.

Drives `run()` with tiny, hand-built candle series and test-only `Rule` subclasses (the same
convention `tests/strategy/test_backtest.py` uses) so every outcome is known exactly, with no
real market data, network access, or wall-clock reads.

Mirrors the plan's Task 6 test list (`docs/superpowers/plans/2026-07-17-engine-validation-
simulation.md`): no-lookahead, one-position-per-asset, exit-on-stop-and-on-target, monthly
contributions, idle-span-on-a-silent-move.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from keel.config import (
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    SubscriptionConfig,
)
from keel.sim.portfolio_sim import MOVE_THRESHOLD_PCT, run
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

_HOUR = 3600
_DAY = 86400


def _candle(ts: int, o: str, h: str, l: str, c: str, v: str = "1") -> Candle:  # noqa: E741
    return Candle(
        ts=ts, open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=Decimal(v)
    )


def _ts(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, 0, 0, tzinfo=UTC).timestamp())


def _config(**overrides) -> Config:
    defaults: dict = dict(
        allowlist=["BTC", "ETH"],
        target_weights={},
        risk_pct=Decimal("0.02"),
        caps=Caps(
            max_per_order_usd=Decimal("1000000"),
            max_per_day_usd=Decimal("1000000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        subscription=SubscriptionConfig(
            monthly_allowance_usd=Decimal("1000000"), pacing="opportunistic"
        ),
        dca=DcaConfig(budget_usd=Decimal("50")),
    )
    defaults.update(overrides)
    return Config(**defaults)


# ---------------------------------------------------------------------------
# Test-only rules
# ---------------------------------------------------------------------------


class _SpyRule(Rule):
    """Never fires; records every candle ts handed to `detect()` across both timeframes,
    alongside the as-of ts (the last ONE_HOUR candle) -- used to prove `run()` never hands a
    rule a candle from beyond the current bar (no lookahead)."""

    name = "spy"
    params: dict = {}

    def __init__(self, product_id: str) -> None:
        self.product_id = product_id
        self.calls: list[tuple[int, list[int]]] = []

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        hourly = candles_by_tf.get(Granularity.ONE_HOUR, [])
        if not hourly:
            return None
        as_of = hourly[-1].ts
        all_ts = [c.ts for series in candles_by_tf.values() for c in series]
        self.calls.append((as_of, all_ts))
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _FirstBarRule(Rule):
    """Fires exactly once, on the asset's very first bar (where the choppy/higher-TF-bias gates
    auto-pass on a <2-candle window) -- lets stop/target-exit tests avoid crafting real swing
    structure just to clear `engine.evaluate`'s regime gates."""

    name = "first_bar"
    params: dict = {}

    def __init__(self, product_id: str, entry: Decimal, stop: Decimal, target: Decimal) -> None:
        self.product_id = product_id
        self.entry = entry
        self.stop = stop
        self.target = target
        self.detect_calls = 0

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        hourly = candles_by_tf[Granularity.ONE_HOUR]
        self.detect_calls += 1
        if len(hourly) != 1:
            return None
        latest = hourly[-1]
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=self.entry,
            stop=self.stop,
            target=self.target,
            context={},
            ts=latest.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _AlwaysOnDcaRule(Rule):
    """Fires a no-stop, DCA-exempt setup on every bar while flat (bypassing the regime/kill-zone
    gates -- out of scope for this test) and exits on the very next bar it's checked. Used only
    to prove `run()` never opens a second position in an asset while one is already held."""

    name = "always_on"
    params: dict = {}

    def __init__(self, product_id: str) -> None:
        self.product_id = product_id

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        hourly = candles_by_tf[Granularity.ONE_HOUR]
        latest = hourly[-1]
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=latest.close,
            stop=Decimal("0"),
            target=latest.close,
            context={"no_stop": True, "order_class": "dca"},
            ts=latest.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return True

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _NeverFiresRule(Rule):
    """Never detects a setup -- used to prove idle-span telemetry fires when price moves a lot
    while the assigned rule stays silent."""

    name = "never"
    params: dict = {}

    def __init__(self, product_id: str) -> None:
        self.product_id = product_id

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


# ---------------------------------------------------------------------------
# no-lookahead
# ---------------------------------------------------------------------------


def test_no_lookahead_spy_rule_never_sees_future_candles():
    hourly = [_candle(i * _HOUR, "100", "101", "99", "100") for i in range(50)]
    daily = [_candle(i * _DAY, "100", "105", "95", "100") for i in range(3)]
    spy = _SpyRule("BTC-USD")
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: daily}}
    config = _config()

    run(
        [spy],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("0"),
    )

    assert spy.calls, "the spy rule was never driven"
    for as_of, all_ts in spy.calls:
        assert all(ts <= as_of for ts in all_ts), f"future candle leaked into window as-of {as_of}"


# ---------------------------------------------------------------------------
# one-position-per-asset
# ---------------------------------------------------------------------------


def test_one_position_per_asset_never_overlaps():
    hourly = [_candle(i * _HOUR, "100", "101", "99", "100") for i in range(20)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    rule = _AlwaysOnDcaRule("BTC-USD")
    config = _config(dca=DcaConfig(budget_usd=Decimal("50")))

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )

    trades = sorted(result.trades, key=lambda tr: tr.entry_ts)
    assert len(trades) > 1
    for prev, nxt in zip(trades, trades[1:]):
        assert prev.exit_ts is not None
        assert prev.exit_ts <= nxt.entry_ts
    assert all(tr.outcome != "open" for tr in trades[:-1])


# ---------------------------------------------------------------------------
# exit on stop / exit on target
# ---------------------------------------------------------------------------


def _stop_target_setup(exit_kind: str):
    entry, stop, target = Decimal("100"), Decimal("90"), Decimal("120")
    trigger = _candle(0, "100", "101", "99", "100")
    fill_bar = _candle(_HOUR, "100", "102", "98", "101")
    if exit_kind == "stop":
        move_bar = _candle(2 * _HOUR, "101", "101", "85", "88")
    else:
        move_bar = _candle(2 * _HOUR, "101", "125", "99", "124")
    hourly = [trigger, fill_bar, move_bar]
    rule = _FirstBarRule("BTC-USD", entry=entry, stop=stop, target=target)
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config()
    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )
    return result


def test_exit_on_stop():
    result = _stop_target_setup("stop")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.outcome == "loss"
    assert trade.exit_ts is not None
    assert trade.pnl is not None and trade.pnl < 0


def test_exit_on_target():
    result = _stop_target_setup("target")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.outcome == "win"
    assert trade.exit_ts is not None
    assert trade.pnl is not None and trade.pnl > 0


# ---------------------------------------------------------------------------
# monthly contributions
# ---------------------------------------------------------------------------


def test_monthly_contributions_counted_once_per_utc_month():
    # Ten-day cadence across ~100 days -- crosses Jan/Feb/Mar/Apr 2024 UTC month boundaries.
    start = _ts(2024, 1, 5)
    hourly = [_candle(start + i * 10 * _DAY, "100", "101", "99", "100") for i in range(10)]
    months_touched = {datetime.fromtimestamp(c.ts, tz=UTC).month for c in hourly}
    rule = _NeverFiresRule("BTC-USD")
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config()
    contribution = Decimal("500")

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=contribution,
    )

    assert len(result.contributions) == len(months_touched)
    total = sum((amount for _ts_, amount in result.contributions), Decimal("0"))
    assert total == contribution * len(months_touched)


# ---------------------------------------------------------------------------
# idle-span telemetry
# ---------------------------------------------------------------------------


def test_idle_span_recorded_on_big_move_with_no_signal():
    hourly = []
    price = Decimal("100")
    for i in range(40):
        price *= Decimal("1.03")  # ~3%/bar compounding -- well past a 5% span within a day
        hourly.append(
            _candle(
                i * _HOUR,
                str(price),
                str(price * Decimal("1.001")),
                str(price * Decimal("0.999")),
                str(price),
            )
        )
    rule = _NeverFiresRule("BTC-USD")
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly}}
    config = _config()

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("0"),
    )

    assert result.telemetry.idle_spans
    start_ts, end_ts, asset, move_pct = result.telemetry.idle_spans[0]
    assert asset == "BTC"
    assert move_pct > MOVE_THRESHOLD_PCT
    assert start_ts < end_ts
