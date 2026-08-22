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
    MoneyMgmtConfig,
    SubscriptionConfig,
)
from keel.sim.portfolio_sim import MOVE_THRESHOLD_PCT, SimResult, run
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
            assumed_free_volume_usd=Decimal("1000000"), pacing="opportunistic"
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


def _zigzag_candle(i: int) -> Candle:
    """One bar of a rising zigzag (alternating pronounced peak/trough bars on a rising baseline)
    -- `analysis.regime.detect_condition` classifies this BULLISH (higher-highs, higher-lows)
    from a 6-bar window onward, unlike flat/monotonic candles, which it classifies CHOPPY (no
    swing structure) and would starve `engine.evaluate`'s choppy-regime gate."""
    if i % 2 == 0:
        base = 200 + i
        o, h, lo, c = base, base + 5, base - 2, base + 2
    else:
        base = 100 + i
        o, h, lo, c = base, base + 2, base - 5, base - 2
    return _candle(i * _HOUR, str(o), str(h), str(lo), str(c))


class _AlwaysOnRule(Rule):
    """Fires a real, risk-defined (non-DCA) setup on every bar while flat, with a stop/target set
    so far from price that neither is ever touched -- `exit_signal` (not a stop/target touch)
    always closes the position on the very next bar it's checked. Used to prove `run()` never
    opens a second RULE position in an asset while one is already held (DCA positions, tracked
    separately, coexisting with the rule slot is exercised by
    `test_dca_and_rule_positions_coexist_on_the_same_asset`)."""

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
            stop=Decimal("0.01"),  # far below any zigzag candle -- never touched
            target=Decimal("999999"),  # far above any zigzag candle -- never touched
            context={},
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


def test_one_position_per_RULE_never_overlaps():
    """A single rule still never layers on itself -- that is pyramiding (§26.1), not this."""
    hourly = [_zigzag_candle(i) for i in range(30)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    rule = _AlwaysOnRule("BTC-USD")
    config = _config()

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


def test_window_scales_up_with_history_days():
    """`history_days=365` (the default) should derive a rolling ONE_HOUR window of `365*24=8760`
    bars -- far above the old hardcoded 300 -- so with 400 available bars the spy rule should see
    every bar up to the current one (`min(available_bars, 365*24) == 400`), never truncated."""
    n = 400
    hourly = [_candle(i * _HOUR, "100", "101", "99", "100") for i in range(n)]
    spy = _SpyRule("BTC-USD")
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config(market_data=MarketDataConfig(granularities=[], history_days=365))

    run(
        [spy],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("0"),
    )

    assert spy.calls, "the spy rule was never driven"
    max_window = max(len(all_ts) for _, all_ts in spy.calls)
    assert max_window == n, (
        f"expected the window to grow to all {n} available bars, got {max_window}"
    )


def test_window_floors_at_300_bars_for_small_history_days():
    """A tiny `history_days` (well under `300/24 ~= 12.5` days) should still floor the rolling
    window at 300 bars so indicators have enough history to warm up."""
    n = 400
    hourly = [_candle(i * _HOUR, "100", "101", "99", "100") for i in range(n)]
    spy = _SpyRule("BTC-USD")
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config(market_data=MarketDataConfig(granularities=[], history_days=5))

    run(
        [spy],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("0"),
    )

    assert spy.calls, "the spy rule was never driven"
    max_window = max(len(all_ts) for _, all_ts in spy.calls)
    assert max_window == 300, f"expected the 300-bar floor to apply, got {max_window}"


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


# ---------------------------------------------------------------------------
# Issue #85: clamp-not-reject sizing, DCA sleeve separate from the rule slot
# ---------------------------------------------------------------------------


class _OnceDcaRule(Rule):
    """Fires a DCA-class setup exactly once (on the asset's first bar) -- used only to prove a
    DCA lot and a RULE position can coexist for the same asset within one `run()`."""

    name = "dca_once"
    params: dict = {}

    def __init__(self, product_id: str, budget_usd: Decimal = Decimal("50")) -> None:
        self.product_id = product_id
        self.budget_usd = budget_usd

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        hourly = candles_by_tf[Granularity.ONE_HOUR]
        if len(hourly) != 1:
            return None
        latest = hourly[-1]
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=latest.close,
            stop=Decimal("0"),
            target=latest.close,
            context={"no_stop": True, "order_class": "dca", "size_usd": self.budget_usd},
            ts=latest.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


def test_sufficient_usdc_opens_rule_positions_instead_of_zero_trades():
    """Issue #85's root-cause fix: a risk-sized notional that exceeds a cap used to be REJECTED
    outright (0 trades, ever -- the sim account rejected 100% of rule orders). It's now CLAMPED
    down to whatever headroom is available and still opens -- here the old Phase-1 fabricated
    $100 per-order cap is the binding constraint, so the trade opens at $100, not $0.
    """
    hourly = [
        _candle(0, "100", "101", "99", "100"),
        _candle(_HOUR, "100", "102", "98", "101"),
        _candle(2 * _HOUR, "101", "103", "100", "102"),
    ]
    rule = _FirstBarRule("BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("200"))
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config(
        caps=Caps(
            max_per_order_usd=Decimal("100"),  # the old Phase-1 fabricated placeholder
            max_per_day_usd=Decimal("300"),  # the old Phase-1 fabricated placeholder
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
    )

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )

    assert len(result.trades) > 0
    trade = result.trades[0]
    assert trade.qty == Decimal("1")  # clamped notional $100 / entry $100 = qty 1


def test_cash_constrained_account_clamps_notional_to_available_usdc():
    """A risk-sized notional bigger than the account's actual USDC cash is clamped down to
    exactly that cash headroom (not rejected) -- `entry=100, stop=99` (a tight 1% stop) sizes to
    2x equity at `risk_pct=0.02`, comfortably exceeding a small deposit."""
    hourly = [
        _candle(0, "100", "101", "99", "100"),
        _candle(_HOUR, "100", "102", "98", "101"),
        _candle(2 * _HOUR, "101", "103", "100", "102"),
    ]
    rule = _FirstBarRule("BTC-USD", entry=Decimal("100"), stop=Decimal("99"), target=Decimal("200"))
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config()  # every other cap roomy -- cash is the only binding constraint
    cash = Decimal("500")

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=cash,
    )

    assert len(result.trades) > 0
    trade = result.trades[0]
    assert trade.qty * Decimal("100") == cash  # clamped to exactly the deposited cash


def test_dca_and_rule_positions_coexist_on_the_same_asset():
    """The bug this issue retires: DCA used to occupy the single per-asset slot and (since it
    never exits) permanently freeze rule evaluation for that asset. A DCA lot and a RULE position
    now open independently for the same asset within one run."""
    hourly = [
        _candle(0, "100", "101", "99", "100"),
        _candle(_HOUR, "100", "102", "98", "101"),
        _candle(2 * _HOUR, "101", "103", "100", "102"),
    ]
    dca_rule = _OnceDcaRule("BTC-USD", budget_usd=Decimal("50"))
    risk_rule = _FirstBarRule(
        "BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("200")
    )
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config()

    result = run(
        [dca_rule, risk_rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )

    assert "BTC" in result.dca_positions
    assert result.dca_positions["BTC"].qty > 0
    assert any(tr.asset == "BTC" for tr in result.trades)  # the RULE position also opened


# ---------------------------------------------------------------------------
# Issue #86: monthly_volume_cap throttles the account to a fee-free tier's allowance
# ---------------------------------------------------------------------------


def _mild_zigzag_candle(i: int) -> Candle:
    """One bar of a rising zigzag with a HIGH baseline and TINY oscillation amplitude (~0.1%
    swings, not `_zigzag_candle`'s ~50% swings) -- still classifies BULLISH (real swing
    structure, clearing `analysis.regime.detect_condition`'s choppy gate) but keeps a round
    trip's exit price close to its entry price, so `_AlwaysOnRule`'s open-then-immediately-exit
    cadence exercises the `monthly_volume_cap` throttle without the close leg's notional (itself
    unclamped, see `SimAccount.max_affordable_notional`'s docstring) blowing past the cap."""
    base = 10_000 + i
    if i % 2 == 0:
        o, h, lo, c = base, base + 5, base - 2, base + 2
    else:
        o, h, lo, c = base, base + 2, base - 5, base - 2
    return _candle(i * _HOUR, str(o), str(h), str(lo), str(c))


def test_monthly_volume_cap_none_trades_naturally_and_can_exceed_a_small_allowance():
    """Baseline: with `monthly_volume_cap=None` (default), a well-funded account's trading
    volume is free to exceed a small allowance like $500/mo -- proves the throttle in the next
    test is actually doing something, not just naturally never binding."""
    hourly = [_mild_zigzag_candle(i) for i in range(30)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    rule = _AlwaysOnRule("BTC-USD")
    config = _config()

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("1000000"),
    )

    total_volume = sum(result.monthly_volume.values(), Decimal("0"))
    assert total_volume > Decimal("500")


def test_monthly_volume_cap_throttles_account_below_the_cap():
    """With `monthly_volume_cap=500`, the same well-funded, always-trading account never lets a
    single UTC month's trading volume (buys+sells) exceed $500 -- proving the cap actually binds
    every order's clamp, not just the first one."""
    hourly = [_mild_zigzag_candle(i) for i in range(30)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    rule = _AlwaysOnRule("BTC-USD")
    config = _config()
    cap = Decimal("500")

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("1000000"),
        monthly_volume_cap=cap,
    )

    assert result.monthly_volume, "expected at least one month of trading volume"
    for month_volume in result.monthly_volume.values():
        assert month_volume <= cap
    # the throttle should still have let SOME trading happen, not just zeroed it out entirely.
    assert sum(result.monthly_volume.values(), Decimal("0")) > Decimal("0")


def test_monthly_volume_cap_dust_below_floor_stops_all_new_rule_trades():
    """A `monthly_volume_cap` of 0 leaves zero headroom for any new RULE-slot order -- no trades
    open at all (every clamped notional is 0, below `DUST_FLOOR`)."""
    hourly = [_zigzag_candle(i) for i in range(30)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    rule = _AlwaysOnRule("BTC-USD")
    config = _config()

    result = run(
        [rule],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
        monthly_volume_cap=Decimal("0"),
    )

    assert result.trades == []
    assert sum(result.monthly_volume.values(), Decimal("0")) == Decimal("0")


def test_monthly_volume_aggregates_by_utc_month():
    """`SimResult.monthly_volume` buckets the volume ledger by UTC calendar month, and the sum
    across months equals the total of every trade's notional (entry + exit, Issue #85's
    buys+sells convention)."""
    hourly = [
        _candle(0, "100", "101", "99", "100"),
        _candle(_HOUR, "100", "102", "98", "101"),
        _candle(2 * _HOUR, "101", "103", "100", "102"),
    ]
    rule = _FirstBarRule("BTC-USD", entry=Decimal("100"), stop=Decimal("90"), target=Decimal("200"))
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

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.outcome == "open"  # never exits in this short series
    # the volume ledger records the CANDIDATE order's notional (qty * setup.entry, pre-slippage
    # -- see `SimAccount`'s module docstring), not `trade.entry` (post-slippage fill price).
    expected_volume = trade.qty * Decimal("100")  # only the entry leg has filled so far
    total_bucketed = sum(result.monthly_volume.values(), Decimal("0"))
    assert total_bucketed == expected_volume


# ---------------------------------------------------------------------------
# Rail 16: sweeping max_consecutive_losses must actually CHANGE the backtest
# ---------------------------------------------------------------------------


def _streak_backtest(threshold: int, cooloff_days: int = 1):
    """The same candles and the same rule every time -- only `max_consecutive_losses` varies.
    `_AlwaysOnRule` on the rising zigzag round-trips a position every other bar, producing a
    mix of wins and losses, so a low threshold has real losing streaks to bite on."""
    hourly = [_zigzag_candle(i) for i in range(60)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config(
        money_mgmt=MoneyMgmtConfig(
            max_consecutive_losses=threshold, streak_cooloff_days=cooloff_days
        )
    )
    return run(
        [_AlwaysOnRule("BTC-USD")],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )


def test_sweeping_max_consecutive_losses_changes_the_backtest():
    """THE point of the sim-side producer. Rail 16 ships DISABLED so that a sweep can set its
    threshold -- but a sweep is only meaningful if the sim actually HALTS. Before the producer
    was wired, `streak_halt_until` was initialised to 0 and never written by anything outside a
    test, so every threshold produced byte-identical results and the sweep was a provable no-op.

    Asserting on `trades` (not just some scalar) keeps this behavioural: a halt must suppress
    real entries, not merely flip a flag."""
    disabled = _streak_backtest(threshold=0)
    tripwire = _streak_backtest(threshold=1)

    assert len(disabled.trades) > len(tripwire.trades), (
        "max_consecutive_losses had no effect on the backtest -- the sim-side streak producer "
        "is not wired, so sweeping the threshold is a no-op"
    )


def test_disabled_threshold_never_halts_the_simulator():
    """Negative control for the test above: with the SHIPPED default of 0 the producer must be
    completely inert, so the result must match a run where rail 16 does not exist at all."""
    shipped_default = _streak_backtest(threshold=0)

    hourly = [_zigzag_candle(i) for i in range(60)]
    baseline = run(
        [_AlwaysOnRule("BTC-USD")],
        {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}},
        _config(),  # no money_mgmt override at all
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )

    assert len(shipped_default.trades) == len(baseline.trades)
    assert [t.pnl for t in shipped_default.trades] == [t.pnl for t in baseline.trades]


# ---------------------------------------------------------------------------
# Rail 11: sweeping max_total_dd_pct / max_weekly_dd_pct must actually CHANGE the backtest
# ---------------------------------------------------------------------------


def _drawdown_candle(i: int) -> Candle:
    """One bar of a REAL, sustained decline: every bar closes well below its open (a genuine
    intrabar loss for a round trip landing on either parity, unlike `_zigzag_candle`'s shallow
    ~2-unit swings, which only ever produce fee-drag-sized losses too small to move a drawdown
    scalar meaningfully within a short series). The alternating wick depth still gives
    `analysis.regime.detect_condition` real swing structure (BEARISH, not CHOPPY -- `_AlwaysOnRule`
    needs a tradeable regime to keep firing every bar it's flat)."""
    if i % 2 == 0:
        o, h, lo, c = Decimal(1000), Decimal(1010), Decimal(940), Decimal(950)
    else:
        o, h, lo, c = Decimal(1000), Decimal(1005), Decimal(900), Decimal(920)
    return _candle(i * _HOUR, str(o), str(h), str(lo), str(c))


def _drawdown_backtest(
    max_total_dd_pct: Decimal = Decimal("0.90"),
    max_weekly_dd_pct: Decimal = Decimal("0.90"),
    bars: int = 150,
):
    """The same declining candles and the same always-on rule every time -- only the rail 11
    thresholds vary. 150 hourly bars (~6.25 days, inside rail 11's 7-day weekly window, so
    `drawdown_weekly_pct` and `drawdown_total_pct` track identically here -- no equity has yet
    aged out of the rolling window) of `_drawdown_candle` round-trip a losing position roughly
    every other bar, driving realized, cumulative loss from 0% towards ~9-10% of the account's
    initial equity by the end of the run if nothing ever halts it -- see
    `test_sweeping_max_total_dd_pct_changes_the_backtest`'s docstring for why that range was
    chosen (it straddles a 5% tight threshold with room on both sides for the halt to bind
    partway through and still leave a materially different trade count from an unhalted run)."""
    hourly = [_drawdown_candle(i) for i in range(bars)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config(
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=max_total_dd_pct, max_weekly_dd_pct=max_weekly_dd_pct
        )
    )
    return run(
        [_AlwaysOnRule("BTC-USD")],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )


def test_sweeping_max_total_dd_pct_changes_the_backtest():
    """Rail 11 must be sweepable in the sim for the same reason rail 16 had to be: a threshold
    you cannot vary is a threshold you cannot choose. `_drawdown_backtest`'s losing pattern
    carries the account from 0% towards roughly 9-10% cumulative drawdown over the full run
    (unhalted); a 5% threshold must therefore bind partway through and block every entry after
    that point, while 90% never binds at all. If these are equal, the producer is not wired and
    the sweep is a no-op."""
    loose = _drawdown_backtest(max_total_dd_pct=Decimal("0.90"))
    tight = _drawdown_backtest(max_total_dd_pct=Decimal("0.05"))

    assert len(loose.trades) > len(tight.trades), (
        "max_total_dd_pct had no effect -- the sim-side equity/drawdown producer is not wired"
    )


def test_sweeping_max_weekly_dd_pct_changes_the_backtest():
    """The weekly leg, in isolation (total left at the loose 90% default so only the weekly rail
    can bind): the same losing pattern stays entirely inside rail 11's 7-day rolling window for
    this backtest's ~6.25-day span, so `drawdown_weekly_pct` tracks `drawdown_total_pct`
    identically here and a tight weekly threshold must suppress entries the same way the total
    leg does above. Arming only the total leg and leaving this one dormant is exactly the defect
    class this task exists to close (see the plan's SCOPE DECISION)."""
    loose = _drawdown_backtest(max_weekly_dd_pct=Decimal("0.90"))
    tight = _drawdown_backtest(max_weekly_dd_pct=Decimal("0.05"))

    assert len(loose.trades) > len(tight.trades), (
        "max_weekly_dd_pct had no effect -- the sim-side equity/drawdown producer is not wired "
        "for the weekly leg"
    )


def test_permissive_dd_thresholds_never_suppress_entries():
    """Negative control for the two tests above, mirroring
    `test_disabled_threshold_never_halts_the_simulator`: rail 11 has no `threshold > 0`-style
    off-switch (unlike rail 16) -- both `max_total_dd_pct`/`max_weekly_dd_pct` ship ENABLED, so
    there is no config that fully disables the check. The equivalent proof of inertness is that
    two thresholds on the permissive side of this scenario's actual drawdown ceiling
    (~9-10%, see `_drawdown_backtest`) -- one merely generous (90%) and one mathematically
    unreachable (equity would have to hit exactly zero) -- must produce byte-identical results.
    Any difference would mean the producer is spuriously reacting somewhere it has no real
    drawdown to react to."""
    generous = _drawdown_backtest(
        max_total_dd_pct=Decimal("0.90"), max_weekly_dd_pct=Decimal("0.90")
    )
    unreachable = _drawdown_backtest(max_total_dd_pct=Decimal("1"), max_weekly_dd_pct=Decimal("1"))

    assert len(generous.trades) == len(unreachable.trades)
    assert [t.pnl for t in generous.trades] == [t.pnl for t in unreachable.trades]


# ---------------------------------------------------------------------------
# concurrent RULE slots on one asset
# ---------------------------------------------------------------------------


def test_two_rules_hold_concurrently_in_the_same_asset():
    """The change: distinct rules get distinct slots on the same asset.

    The LIVE executor has been able to do this since PR #96's per-tranche `positions` table;
    the harness could not, which is why the S1+S2 ensemble was judged through a model that
    could not represent it.
    """
    hourly = [_zigzag_candle(i) for i in range(30)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    first = _AlwaysOnRule("BTC-USD")
    second = _AlwaysOnRule("BTC-USD")
    second.name = "always_on_2"
    config = _config()

    result = run(
        [first, second],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )

    solo = run(
        [_AlwaysOnRule("BTC-USD")],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )

    # The direct evidence of concurrency: two positions opened on the SAME bar in the same
    # asset. Under the old one-position-per-asset model the second rule was silently skipped,
    # so this was impossible by construction.
    from collections import Counter

    per_bar = Counter(trade.entry_ts for trade in result.trades)
    assert max(per_bar.values()) == 2, "expected two rules to open on the same bar"
    assert len(result.trades) == 2 * len(solo.trades)


def test_a_single_rule_run_is_unchanged_by_the_multi_slot_model():
    """Regression guard: adding slots must not alter single-rule behaviour."""
    hourly = [_zigzag_candle(i) for i in range(30)]
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    config = _config()

    result = run(
        [_AlwaysOnRule("BTC-USD")],
        candles_by_asset,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
    )
    trades = sorted(result.trades, key=lambda tr: tr.entry_ts)
    for prev, nxt in zip(trades, trades[1:]):
        assert prev.exit_ts is not None
        assert prev.exit_ts <= nxt.entry_ts


# ---------------------------------------------------------------------------
# #442: the ratchet-only exit policy (trailing stop / break-even roll)
# ---------------------------------------------------------------------------


class _ExitPolicyFirstBarRule(_FirstBarRule):
    """`_FirstBarRule` carrying a `params` dict -- the per-family exit-policy knobs
    (`trail_atr_mult` / `be_roll_rr` / `atr_period`) the simulator's exit resolution reads
    through `strategy.exit_policy.policy_for`. Detect/exit_signal behave exactly as the
    parent's (fires on the asset's very first bar)."""

    def __init__(
        self,
        product_id: str,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
        params: dict,
    ) -> None:
        super().__init__(product_id, entry=entry, stop=stop, target=target)
        self.params = dict(params)


def _run_exit_policy_sim(rule: Rule, hourly: list[Candle]) -> SimResult:
    candles_by_asset = {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: []}}
    return run(
        [rule],
        candles_by_asset,
        _config(),
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )


def test_trailing_exits_earlier_and_higher_than_the_static_stop():
    """Same shape as the single-rule backtester's test, driven through the PORTFOLIO sim:
    a 2xATR trail (period 2, so it arms on the third bar) ratchets 94 -> 98 -> 100 on the
    rising bars and exits the retrace at 100, where the static arm rides down to 94.

      bar0: trigger. bar1: fill at open 100.
      bar2: o100 h102 l100 c102 -> ATR(2)=2, trail 102-4=98
      bar3: o102 h104 l102 c104 -> trail 104-4=100
      bar4: o102 h103 l97  c98  -> low 97 touches the TRAILED stop 100 -> exit
      bar5: o98  h99  l93  c94  -> static arm exits at 94
    """
    hourly = [
        _candle(0, "100", "101", "99", "100"),
        _candle(_HOUR, "100", "101", "99", "100"),
        _candle(2 * _HOUR, "100", "102", "100", "102"),
        _candle(3 * _HOUR, "102", "104", "102", "104"),
        _candle(4 * _HOUR, "102", "103", "97", "98"),
        _candle(5 * _HOUR, "98", "99", "93", "94"),
    ]
    trailed = _run_exit_policy_sim(
        _ExitPolicyFirstBarRule(
            "BTC-USD",
            Decimal("100"),
            Decimal("94"),
            Decimal("130"),
            {"trail_atr_mult": Decimal("2"), "atr_period": 2},
        ),
        hourly,
    )
    static = _run_exit_policy_sim(
        _ExitPolicyFirstBarRule("BTC-USD", Decimal("100"), Decimal("94"), Decimal("130"), {}),
        hourly,
    )

    assert len(trailed.trades) == 1
    assert len(static.trades) == 1
    assert trailed.trades[0].exit == Decimal("100")
    assert trailed.trades[0].exit_ts == 4 * _HOUR
    assert static.trades[0].exit == Decimal("94")
    assert static.trades[0].exit_ts == 5 * _HOUR
    assert trailed.trades[0].pnl is not None and static.trades[0].pnl is not None
    assert trailed.trades[0].pnl > static.trades[0].pnl


def test_break_even_roll_exits_at_entry_once_and_never_widens():
    """`be_roll_rr=1`: bar2's high 111 clears entry + 1x the ORIGINAL risk (10), the stop
    rolls to the entry ONCE, and the next dip to 99 exits there -- a scratch. The static
    arm rides to the full 90 stop. Between the roll and the exit the retrace computes
    nothing above entry, and the stop must not move down -- that is the ratchet."""
    hourly = [
        _candle(0, "100", "101", "99", "100"),
        _candle(_HOUR, "100", "101", "99", "100"),
        _candle(2 * _HOUR, "100", "111", "100", "108"),  # +1R cleared -> roll to entry
        _candle(3 * _HOUR, "106", "107", "99", "100"),  # low 99 touches the rolled stop
        _candle(4 * _HOUR, "100", "101", "89", "90"),  # static arm exits at 90
    ]
    rolled = _run_exit_policy_sim(
        _ExitPolicyFirstBarRule(
            "BTC-USD",
            Decimal("100"),
            Decimal("90"),
            Decimal("130"),
            {"be_roll_rr": Decimal("1"), "atr_period": 2},
        ),
        hourly,
    )
    static = _run_exit_policy_sim(
        _ExitPolicyFirstBarRule("BTC-USD", Decimal("100"), Decimal("90"), Decimal("130"), {}),
        hourly,
    )

    assert len(rolled.trades) == 1
    assert len(static.trades) == 1
    assert rolled.trades[0].exit == Decimal("100")
    assert rolled.trades[0].exit_ts == 3 * _HOUR
    assert rolled.trades[0].pnl == Decimal("0")
    assert rolled.trades[0].outcome == "scratch"
    assert static.trades[0].exit == Decimal("90")
    assert static.trades[0].exit_ts == 4 * _HOUR


def test_a_rule_without_exit_params_trades_identically_in_the_sim():
    """The default-OFF guarantee on the portfolio path (the turtle guarantee in
    miniature: any rule whose params carry no trail/BE knobs -- turtle among them --
    must trade exactly as before the wiring existed)."""
    hourly = [
        _candle(0, "100", "101", "99", "100"),
        _candle(_HOUR, "100", "101", "99", "100"),
        _candle(2 * _HOUR, "100", "102", "100", "102"),
        _candle(3 * _HOUR, "102", "104", "102", "104"),
        _candle(4 * _HOUR, "102", "103", "97", "98"),
        _candle(5 * _HOUR, "98", "99", "93", "94"),
    ]
    unset = _run_exit_policy_sim(
        _FirstBarRule("BTC-USD", Decimal("100"), Decimal("94"), Decimal("130")), hourly
    )
    explicit_off = _run_exit_policy_sim(
        _ExitPolicyFirstBarRule(
            "BTC-USD",
            Decimal("100"),
            Decimal("94"),
            Decimal("130"),
            {"trail_atr_mult": None, "be_roll_rr": None},
        ),
        hourly,
    )

    def _shape(result):
        return [(t.entry_ts, t.exit_ts, t.entry, t.exit, t.pnl, t.outcome) for t in result.trades]

    assert _shape(unset) == _shape(explicit_off)
