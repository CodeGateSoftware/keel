"""Multi-asset portfolio simulator (plan Task 6,
`docs/superpowers/plans/2026-07-17-engine-validation-simulation.md`).

Walks the ascending UNION of `ONE_HOUR` timestamps across every asset in `candles_by_asset`
between `start_ts` and `end_ts`, driving each asset's assigned `Rule`(s) through
`strategy.engine.evaluate()` on a rolling, lookahead-free window (`WINDOW_BARS` hourly bars plus
every daily bar with `ts <= t`), opening/closing at most one RULE (risk-defined) position per
asset on a single shared `sim.account.SimAccount`, and recording per-trade (`SimTrade`) and
portfolio-level (`SimTelemetry`) data for the report/verdict stage (Task 7, `sim/report.py`).

**Sizing is CLAMPED, not rejected (Issue #85):** a rule signal's fixed-fractional risk size
(`execution.sizing.size`) is clamped down to `account.max_affordable_notional()` -- the tightest
of available USDC cash, per-asset concentration headroom, and total-exposure headroom -- before
`can_open` ever sees it, instead of being rejected outright whenever the raw risk-sized notional
happened to exceed one of those. `can_open` remains the hard safety veto (a clamped intent is
only skipped if it still doesn't pass, or clamps below `DUST_FLOOR`); it is never weakened.

**DCA is a separate sleeve, not a slot-occupant (Issue #85):** a DCA-class setup (`no_stop` /
`order_class == "dca"` context, `strategy/rules/dca.py`) is scheduled accumulation, not a
risk-defined trade -- it is evaluated and (re)bought on every bar regardless of whether that
asset's RULE slot is currently held, via `account.open(..., dca=True)`, which accumulates into a
separate per-asset DCA lot (`SimAccount.dca_positions`) that this simulator never closes (DCA has
no exit signal by design). Before this fix, DCA and rule trades shared the single per-asset
`held` slot, so an asset accumulating DCA (which never exits) permanently froze that asset's rule
evaluation.

**Interpretive notes** (the plan's Task 6 prose leaves a few specifics implicit):

- **`cts_factor_populated` / `rejected_for_missing_input`** (both `dict[str, int]`, keyed by CTS
  context-key name -- see `strategy.indicators_cts.DEFAULT_WEIGHTS`): for *every* ENTER signal
  `evaluate()` emits (whether or not it ends up opened), the engine's own CTS context assembly is
  reused verbatim (`engine.assemble_cts_context` + `indicators_cts.score`, not reimplemented) to
  determine, per factor, whether it was present or absent on that bar. Present factors increment
  `cts_factor_populated[name]`; absent ones increment `rejected_for_missing_input[name]`. This is
  symmetric by construction (`populated[k] + missing[k]` == the number of signals a given `k` was
  ever evaluated on) and feeds Task 7's "unfed CTS factors" gap-analysis detector directly. The
  plan text ties the second counter to `can_open`'s `not ok` outcome, but `can_open`'s rejection
  reasons are always spend-cap strings (never confluence-related, see `sim/account.py`), so a
  literal "not ok AND missing-confluence" condition could never fire; tallying every evaluated
  signal's absent factors is the reading that actually serves the stated purpose.
- **`per_bucket_pnl` regime key**: bucketed by the market `Condition` (`analysis.regime`) of the
  exit-time ONE_HOUR window -- the regime the trade *closed* into, not the one it opened into.
- **Idle-span gating**: the plan calls for a move-threshold (`MOVE_THRESHOLD_PCT`) AND "a gap
  exceeds a threshold span"; the latter is `IDLE_SPAN_MIN_HOURS` here (undocumented exact value
  in the plan prose), gating idle-span detection to genuinely quiet stretches rather than
  single-bar noise.
- **`SimResult.coverage`**: `run()`'s signature (per the plan) takes no coverage/history input,
  so this is always `{}` here -- a passthrough placeholder for the CLI (Task 8), which does have
  access to `data/history.py`'s per-asset `CoverageInfo` and can attach it after calling `run()`.

**No lookahead:** the per-bar `candles_by_tf` window handed to `Rule.detect`/`exit_signal` and to
`engine.evaluate` only ever contains candles with `ts <= t` (the current bar). The one deliberate
exception -- a fill-cost model, not a lookahead violation of the *decision* -- is that a passed
`can_open` check fills at the *next* hourly bar's `open` (a market order placed on bar `t` can't
fill at that same bar's price); if there is no next bar, the signal is dropped unfilled.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from keel.analysis import regime
from keel.config import Config
from keel.execution import sizing
from keel.execution.guards import _asset, _utc_day_bounds, _utc_month_bounds
from keel.sim.account import OpenIntent, OpenPosition, SimAccount
from keel.strategy import engine, indicators_cts
from keel.strategy.backtest import _resolve_order, _touches
from keel.strategy.rules.base import Rule, Setup, Signal
from keel.types import Candle, Granularity

__all__ = [
    "DUST_FLOOR",
    "IDLE_SPAN_MIN_HOURS",
    "MOVE_THRESHOLD_PCT",
    "WINDOW_BARS",
    "SimResult",
    "SimTelemetry",
    "SimTrade",
    "run",
]

# Floor for the rolling ONE_HOUR window size handed to `Rule.detect`/`engine.evaluate` -- large
# enough for every existing rule's longest lookback (EMA-200, 90-bar-equivalent structure) even
# when `config.market_data.history_days` is tiny. The window actually used by `run()` is
# `_window_bars(config)` (`max(WINDOW_BARS, history_days * 24)`), which for the default
# `history_days=365` grows to ~8760 hourly bars -- matching what the LIVE agent
# (`agent.run_once`) evaluates against, instead of hardcoding a much shorter, unfaithful cap.
WINDOW_BARS = 300

# Idle-span telemetry: a "no signal fired while price moved a lot" span is recorded once both
# (a) the gap since the last signal spans at least this many hours, and (b) the cumulative move
# across it exceeds MOVE_THRESHOLD_PCT (5%).
IDLE_SPAN_MIN_HOURS = 24
MOVE_THRESHOLD_PCT = Decimal("0.05")

# Issue #85: a risk-sized notional CLAMPED down to available headroom (cash / concentration /
# exposure) below this floor isn't worth opening -- fee+slippage would dominate a sub-$1 order.
# The entry is skipped, not rejected outright (matching every other clamp-not-reject in this
# module), so the next bar gets a fresh chance once headroom recovers.
DUST_FLOOR = Decimal("1")

_SECONDS_PER_HOUR = 3600


@dataclass
class SimTrade:
    """One round-trip: a closed trade has every field populated; a position still open at the
    end of the simulated window is recorded with `exit_ts=exit=pnl=r_multiple=None` and
    `outcome="open"` (mirrors `strategy.backtest`'s open-position convention)."""

    asset: str
    entry_ts: int
    exit_ts: int | None
    entry: Decimal
    exit: Decimal | None
    qty: Decimal
    pnl: Decimal | None
    r_multiple: Decimal | None
    mfe: Decimal
    mae: Decimal
    outcome: str
    rule_kind: str
    cts_score: int
    entry_technique: str


@dataclass
class SimTelemetry:
    """Portfolio-level bookkeeping alongside the trade log, feeding Task 7's gap analysis."""

    bars: int = 0
    signals_emitted: int = 0
    idle_spans: list[tuple[int, int, str, Decimal]] = field(default_factory=list)
    cts_factor_populated: dict[str, int] = field(default_factory=dict)
    rejected_for_missing_input: dict[str, int] = field(default_factory=dict)
    per_bucket_pnl: dict[tuple[str, str, str], Decimal] = field(default_factory=dict)
    mae_samples: list[Decimal] = field(default_factory=list)
    mfe_giveback_samples: list[Decimal] = field(default_factory=list)


@dataclass
class SimResult:
    trades: list[SimTrade]
    equity_curve: list[tuple[int, Decimal]]
    contributions: list[tuple[int, Decimal]]
    coverage: dict
    telemetry: SimTelemetry
    # DCA-sleeve holdings at the end of the run, keyed by asset (Issue #85) -- accumulated,
    # marked-to-market lots that never generate a `SimTrade` (DCA never exits by design, see the
    # module docstring); exposed here so callers/tests can see DCA and rule trades coexisting.
    dca_positions: dict[str, OpenPosition] = field(default_factory=dict)
    # Every opened AND closed order's notional (trading VOLUME, Issue #85's buys+sells
    # convention), aggregated by UTC calendar month and keyed by each month's start ts
    # (`SimAccount.monthly_volume`) -- feeds the Coinbase One tier/fee analysis (Issue #86,
    # `sim.tiers`), which needs per-month volume to compute over-cap fees correctly.
    monthly_volume: dict[int, Decimal] = field(default_factory=dict)


@dataclass
class _Held:
    """The sim-local record of a currently open position -- `SimAccount.OpenPosition` doesn't
    carry `target`, the originating `Rule`/`Setup`, or the entry `Signal`'s CTS grade, all needed
    for exit resolution and the closed `SimTrade`'s audit fields."""

    rule: Rule
    setup: Setup
    entry_ts: int
    entry_fill: Decimal
    qty: Decimal
    cts_score: int
    entry_technique: str
    mfe: Decimal = Decimal("0")
    mae: Decimal = Decimal("0")


@dataclass
class _IdleAnchor:
    start_ts: int
    start_price: Decimal


def _is_dca_setup(setup: Setup) -> bool:
    """Mirrors `engine._is_market_buy_class`: a no-stop, market-buy accumulation setup."""
    context = setup.context
    return bool(context.get("no_stop")) or context.get("order_class") == "dca"


def _union_hourly_ts(
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]], start_ts: int, end_ts: int
) -> list[int]:
    ts_set: set[int] = set()
    for per_tf in candles_by_asset.values():
        for candle in per_tf.get(Granularity.ONE_HOUR, []):
            if start_ts <= candle.ts <= end_ts:
                ts_set.add(candle.ts)
    return sorted(ts_set)


def _window_bars(config: Config) -> int:
    """Rolling ONE_HOUR window length for the account pass: `history_days * 24` hourly bars/day
    (`ONE_HOUR` is the trading TF), floored at `WINDOW_BARS` so tiny configs still warm up
    indicators. Mirrors the LIVE agent's `agent.run_once`, which evaluates against
    `config.market_data.history_days` (default 365 -> ~8760 hourly bars) -- deriving this from
    config keeps the account pass faithful to production instead of a hardcoded, much shorter
    cap."""
    return max(WINDOW_BARS, config.market_data.history_days * 24)


def _window_1h(hourly: list[Candle], idx: int, window_bars: int) -> list[Candle]:
    start = max(0, idx - window_bars + 1)
    return hourly[start : idx + 1]


def run(
    rules: list[Rule],
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]],
    config: Config,
    start_ts: int,
    end_ts: int,
    monthly_contribution: Decimal,
    fee_pct: Decimal = Decimal("0.006"),
    slippage_pct: Decimal = Decimal("0.0005"),
    monthly_volume_cap: Decimal | None = None,
) -> SimResult:
    """Simulate `rules` (each bound to one asset via `Rule.product_id`) over `candles_by_asset`.

    See the module docstring for the loop's exact semantics (no-lookahead window assembly,
    conservative stop-vs-target resolution, next-bar-open fills, monthly contributions, daily
    equity sampling, idle-span telemetry).

    `monthly_volume_cap` (Issue #86, Coinbase One tier/fee analysis): when `None` (default), the
    account trades naturally -- sizing is clamped only by `SimAccount.max_affordable_notional`'s
    existing six caps (cash / concentration / exposure / etc, Issue #85), which can push a
    month's trading VOLUME (buys+sells) past any particular subscription tier's fee-free
    allowance. When set to a `Decimal`, every order's clamp ALSO floors headroom to the volume
    remaining before that ceiling this UTC month, so the account never trades enough in a month
    to exceed `monthly_volume_cap` -- i.e. it never owes a fee under a tier whose free allowance
    equals `monthly_volume_cap`. This throttles both the RULE-slot clamp and the DCA sleeve (DCA
    is skipped for the cycle, not partially filled, if it would breach the remaining volume --
    consistent with DCA's fixed-budget-per-cycle semantics elsewhere in this module).
    """
    account = SimAccount(fee_pct, slippage_pct)
    window_bars = _window_bars(config)
    telemetry = SimTelemetry()
    trades: list[SimTrade] = []
    equity_curve: list[tuple[int, Decimal]] = []
    contributions: list[tuple[int, Decimal]] = []

    rules_by_asset: dict[str, list[Rule]] = defaultdict(list)
    for rule in rules:
        rules_by_asset[_asset(rule.product_id)].append(rule)

    hourly_by_asset: dict[str, list[Candle]] = {
        asset: per_tf.get(Granularity.ONE_HOUR, []) for asset, per_tf in candles_by_asset.items()
    }
    hourly_index: dict[str, dict[int, int]] = {
        asset: {c.ts: i for i, c in enumerate(series)} for asset, series in hourly_by_asset.items()
    }
    daily_by_asset: dict[str, list[Candle]] = {
        asset: per_tf.get(Granularity.ONE_DAY, []) for asset, per_tf in candles_by_asset.items()
    }
    daily_ts_by_asset: dict[str, list[int]] = {
        asset: [c.ts for c in series] for asset, series in daily_by_asset.items()
    }

    # Keyed by (asset, rule_name): one position per RULE per asset, so an asset can hold
    # several concurrent rule positions. Deliberately NOT multiple positions from the SAME rule
    # -- that is pyramiding (§26.1), a separate feature with its own exposure-rail implications.
    held: dict[tuple[str, str], _Held] = {}
    latest_price: dict[str, Decimal] = {}
    idle: dict[str, _IdleAnchor] = {}

    last_month_start: int | None = None
    last_day_start: int | None = None

    timestamps = _union_hourly_ts(candles_by_asset, start_ts, end_ts)
    telemetry.bars = len(timestamps)

    for t in timestamps:
        month_start, _ = _utc_month_bounds(t)
        if month_start != last_month_start:
            account.deposit(monthly_contribution, t)
            contributions.append((t, monthly_contribution))
            last_month_start = month_start

        # Rail 11's inputs, refreshed BEFORE any signal evaluation this bar -- mirrors
        # `agent.run_once`'s reconcile -> equity -> entries ordering (there is no reconcile step
        # here; the sim has no broker to reconcile against). `latest_price` at this point in the
        # loop still holds the PREVIOUS bar's closes (it is only updated for an asset further
        # down, inside this same iteration's per-asset loop) -- that is deliberate, not an
        # off-by-one: marking to market against bar `t`'s own not-yet-seen close would be
        # lookahead, letting `can_open`'s drawdown check see price information this bar's signals
        # haven't been evaluated against yet. `latest_price` is empty on the very first bar (no
        # asset has been priced yet); `mark_to_market` sums over `positions`/`dca_positions`,
        # both empty at that point too (nothing can have opened before the first bar's signals
        # are even evaluated), so an empty `latest_price` is safe here, not merely assumed to be.
        account.update_equity(latest_price, t)

        for asset, hourly in hourly_by_asset.items():
            idx = hourly_index[asset].get(t)
            if idx is None:
                continue  # this asset has no bar at this timestamp

            current = hourly[idx]
            latest_price[asset] = current.close

            daily_idx = bisect.bisect_right(daily_ts_by_asset[asset], t)
            candles_by_tf: dict[Granularity, list[Candle]] = {
                Granularity.ONE_HOUR: _window_1h(hourly, idx, window_bars),
                Granularity.ONE_DAY: daily_by_asset[asset][:daily_idx],
            }

            # The RULE slot's exit check runs first and independently of DCA (Issue #85): DCA
            # positions are never in `held`, so this only ever resolves a risk-defined position.
            # Snapshot the keys first: `_process_held` mutates `held` when a position closes.
            for key in [key for key in held if key[0] == asset]:
                _process_held(
                    key, idx, hourly, current, candles_by_tf, held, account, config,
                    trades, telemetry,
                )

            asset_rules = rules_by_asset.get(asset)
            if not asset_rules:
                continue

            signals = engine.evaluate(asset_rules, candles_by_tf)
            telemetry.signals_emitted += len(signals)
            _record_cts_telemetry(signals, candles_by_tf, telemetry)

            # DCA continuation runs every bar regardless of the RULE slot's state -- a DCA
            # sleeve keeps buying on its own cadence even while a rule position is held.
            _process_dca_signals(
                asset, idx, hourly, signals, account, config, t, monthly_volume_cap
            )

            fired = _process_rule_signals(
                asset, idx, hourly, signals, rules_by_asset[asset], account, config,
                latest_price, held, t, monthly_volume_cap,
            )
            _track_idle(asset, current, fired, idle, telemetry)

        day_start, _ = _utc_day_bounds(t)
        if day_start != last_day_start:
            equity_curve.append((t, account.mark_to_market(latest_price)))
            last_day_start = day_start

    for (asset, _slot), h in held.items():
        trades.append(
            SimTrade(
                asset=asset,
                entry_ts=h.entry_ts,
                exit_ts=None,
                entry=h.entry_fill,
                exit=None,
                qty=h.qty,
                pnl=None,
                r_multiple=None,
                mfe=h.mfe,
                mae=h.mae,
                outcome="open",
                rule_kind=h.rule.name,
                cts_score=h.cts_score,
                entry_technique=h.entry_technique,
            )
        )

    return SimResult(
        trades=trades,
        equity_curve=equity_curve,
        contributions=contributions,
        coverage={},
        telemetry=telemetry,
        dca_positions=dict(account.dca_positions),
        monthly_volume=account.monthly_volume(),
    )


# ---------------------------------------------------------------------------
# Per-bar processing
# ---------------------------------------------------------------------------


def _process_held(
    key: tuple[str, str],
    idx: int,
    hourly: list[Candle],
    current: Candle,
    candles_by_tf: dict[Granularity, list[Candle]],
    held: dict[tuple[str, str], _Held],
    account: SimAccount,
    config: Config,
    trades: list[SimTrade],
    telemetry: SimTelemetry,
) -> None:
    """Resolve `asset`'s held RULE position against the current bar: conservative intrabar
    stop-vs-target resolution first (via `strategy.backtest`'s reused `_touches`/`_resolve_order`
    -- with no finer-than-hourly series ever available here, ambiguity always resolves to the
    stop, exactly matching `backtest.py`'s documented no-finer-data fallback), then
    `Rule.exit_signal` if neither level was touched. `held` only ever contains risk-defined RULE
    positions (Issue #85) -- DCA setups are filtered out before ever reaching `held` (see
    `_process_rule_signals`), so every position resolved here has a real stop/target."""
    asset, slot = key
    h = held[key]
    setup = h.setup

    h.mfe = max(h.mfe, current.high - h.entry_fill)
    h.mae = max(h.mae, h.entry_fill - current.low)

    exit_price: Decimal | None = None

    stop_touched = _touches(current, setup.stop)
    target_touched = _touches(current, setup.target)
    if stop_touched and target_touched:
        order = _resolve_order(idx, hourly, None, {"target": setup.target, "stop": setup.stop})
        exit_price = setup.target if order == "target" else setup.stop
    elif stop_touched:
        exit_price = setup.stop
    elif target_touched:
        exit_price = setup.target

    if exit_price is None and h.rule.exit_signal(setup, candles_by_tf):
        exit_price = current.close

    if exit_price is None:
        return

    pnl = account.close(asset, exit_price, current.ts, slot=slot)
    exit_fill = exit_price * (Decimal(1) - account.slippage_pct)

    # Rail 16: feed the closed trade to the sim-side streak producer, so a `keel simulate` sweep
    # over `max_consecutive_losses` actually changes the backtest. `held` is RULE-slot only (DCA
    # never exits here, Issue #85), so `is_dca` is always False -- passed explicitly anyway to
    # keep the call site honest against `execution.streak.record_closed_trade`'s signature.
    account.record_trade_outcome(pnl, config, current.ts, is_dca=False)

    risk = (h.entry_fill - setup.stop) * h.qty
    r_multiple = pnl / risk if risk != 0 else None
    outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "scratch"

    trades.append(
        SimTrade(
            asset=asset,
            entry_ts=h.entry_ts,
            exit_ts=current.ts,
            entry=h.entry_fill,
            exit=exit_fill,
            qty=h.qty,
            pnl=pnl,
            r_multiple=r_multiple,
            mfe=h.mfe,
            mae=h.mae,
            outcome=outcome,
            rule_kind=h.rule.name,
            cts_score=h.cts_score,
            entry_technique=h.entry_technique,
        )
    )

    telemetry.mae_samples.append(h.mae)
    telemetry.mfe_giveback_samples.append(max(Decimal(0), h.mfe - max(pnl, Decimal(0))))

    condition = regime.detect_condition(candles_by_tf[Granularity.ONE_HOUR])
    bucket_key = (h.rule.name, asset, condition.value)
    telemetry.per_bucket_pnl[bucket_key] = (
        telemetry.per_bucket_pnl.get(bucket_key, Decimal(0)) + pnl
    )

    del held[key]


def _record_cts_telemetry(
    signals: list[Signal], candles_by_tf: dict[Granularity, list[Candle]], telemetry: SimTelemetry
) -> None:
    """For *every* ENTER signal `evaluate()` emits this bar (DCA or rule-class, opened or not),
    tally which CTS confluence factors were present/absent -- feeds Task 7's "unfed CTS factors"
    gap-analysis detector. Runs once per bar regardless of the RULE slot's held/flat state."""
    window_1h = candles_by_tf[Granularity.ONE_HOUR]
    for signal in signals:
        setup = signal.setup
        if setup is None:
            continue
        cts_result = indicators_cts.score(engine.assemble_cts_context(setup, window_1h))
        for factor in cts_result.factors:
            bucket = (
                telemetry.cts_factor_populated
                if factor.present
                else telemetry.rejected_for_missing_input
            )
            bucket[factor.name] = bucket.get(factor.name, 0) + 1


def _process_dca_signals(
    asset: str,
    idx: int,
    hourly: list[Candle],
    signals: list[Signal],
    account: SimAccount,
    config: Config,
    now_ts: int,
    monthly_volume_cap: Decimal | None = None,
) -> None:
    """Buy every DCA-class signal this bar into the separate DCA sleeve (`account.dca_positions`,
    via `account.open(..., dca=True)`), regardless of whether the asset's RULE slot is currently
    held -- DCA is scheduled accumulation on its own cadence (`strategy/rules/dca.py`'s `detect`
    already gates *when* it fires), not a risk-defined trade competing for the rule slot. Skips a
    signal only when it fails `can_open`'s hard safety veto, there's no next bar to fill at, or
    (Issue #86) it would push this UTC month's trading volume past `monthly_volume_cap` -- DCA is
    SKIPPED for the cycle in that case, not partially filled, matching its fixed-budget-per-cycle
    semantics (unlike the RULE slot's risk-sized notional, which IS clamped, see
    `_process_rule_signals`)."""
    for signal in signals:
        setup = signal.setup
        if setup is None or not _is_dca_setup(setup):
            continue

        try:
            budget = setup.context.get("size_usd") or config.dca.budget_usd
            qty = sizing.dca_size(budget, setup.entry)
        except ValueError:
            continue

        notional = sizing.spend(qty, setup.entry)

        if monthly_volume_cap is not None:
            remaining = monthly_volume_cap - account.month_volume(now_ts)
            if notional > remaining:
                continue  # would exceed the fee-free monthly volume cap -- skip this cycle

        intent = OpenIntent(
            asset=asset,
            qty=qty,
            entry=setup.entry,
            stop=None,
            notional=notional,
            is_dca=True,
            rule_kind=signal.rule_name,
        )

        ok, _reasons = account.can_open(intent, config, now_ts)
        if not ok:
            continue

        fill_idx = idx + 1
        if fill_idx >= len(hourly):
            continue  # no next bar to fill at -- the signal is lost, not a rejection

        fill_bar = hourly[fill_idx]
        account.open(intent, fill_bar.open, fill_bar.ts, dca=True)


def _process_rule_signals(
    asset: str,
    idx: int,
    hourly: list[Candle],
    signals: list[Signal],
    asset_rules: list[Rule],
    account: SimAccount,
    config: Config,
    latest_price: dict[str, Decimal],
    held: dict[str, _Held],
    now_ts: int,
    monthly_volume_cap: Decimal | None = None,
) -> bool:
    """Open at most one risk-defined RULE position for `asset` this bar from `signals` (only
    called while `asset`'s rule slot is flat -- DCA-class signals are handled separately by
    `_process_dca_signals` and never compete for this slot).

    The risk-sized notional (`execution.sizing.size`) is CLAMPED down to
    `account.max_affordable_notional()` -- the tightest of available USDC cash, per-asset
    concentration headroom, and total-exposure headroom -- instead of being rejected outright
    when it exceeds one of those (Issue #85); `can_open` remains the hard safety veto a clamped
    intent must still pass. A signal is skipped (not opened) only if the clamped notional falls
    below `DUST_FLOOR`, or the clamped intent still somehow fails `can_open`, or there's no next
    bar to fill at.

    `monthly_volume_cap` (Issue #86), when set, additionally floors the clamp to the volume
    remaining before that ceiling this UTC month (`account.max_affordable_notional`'s own
    `monthly_volume_cap` param) -- see `run()`'s docstring.

    Returns `True` iff `signals` contained at least one non-DCA (rule-class) ENTER signal this
    bar (opened or not) -- the idle-span tracker resets its anchor whenever a rule signal fires,
    whether or not it was ultimately filled. DCA firing on its own cadence deliberately does NOT
    count here: a regular DCA heartbeat shouldn't mask a genuine rule-signal drought.
    """
    rules_by_name = {rule.name: rule for rule in asset_rules}
    rule_signal_fired = False

    for signal in signals:
        setup = signal.setup
        if setup is None or _is_dca_setup(setup):
            continue
        rule_signal_fired = True

        if (asset, signal.rule_name) in held:
            continue  # one position per RULE per asset; re-entry waits for this one to close

        rule = rules_by_name.get(signal.rule_name)
        if rule is None:
            continue

        try:
            equity = account.mark_to_market(latest_price)
            qty = sizing.size(equity, config.risk_pct, setup.entry, setup.stop)
        except ValueError:
            continue

        risk_notional = sizing.spend(qty, setup.entry)
        headroom = account.max_affordable_notional(
            asset, config, now_ts, monthly_volume_cap=monthly_volume_cap
        )
        clamped_notional = min(risk_notional, headroom)
        if clamped_notional < DUST_FLOOR:
            continue
        if clamped_notional < risk_notional:
            qty = clamped_notional / setup.entry

        intent = OpenIntent(
            asset=asset,
            qty=qty,
            entry=setup.entry,
            stop=setup.stop,
            notional=clamped_notional,
            is_dca=False,
            rule_kind=rule.name,
        )

        ok, _reasons = account.can_open(intent, config, now_ts)
        if not ok:
            continue

        fill_idx = idx + 1
        if fill_idx >= len(hourly):
            continue  # no next bar to fill at -- the signal is lost, not a rejection

        fill_bar = hourly[fill_idx]
        account.open(intent, fill_bar.open, fill_bar.ts, slot=signal.rule_name)
        pos = account.positions[(asset, signal.rule_name)]
        held[(asset, signal.rule_name)] = _Held(
            rule=rule,
            setup=setup,
            entry_ts=pos.entry_ts,
            entry_fill=pos.entry_fill,
            qty=pos.qty,
            cts_score=signal.cts_score,
            entry_technique=signal.entry_technique,
        )

    return rule_signal_fired


def _track_idle(
    asset: str,
    current: Candle,
    fired: bool,
    idle: dict[str, _IdleAnchor],
    telemetry: SimTelemetry,
) -> None:
    """Record an idle span once a signal-free gap spans at least `IDLE_SPAN_MIN_HOURS` AND the
    cumulative move since the anchor exceeds `MOVE_THRESHOLD_PCT`; a fired signal always resets
    the anchor to the current bar."""
    anchor = idle.get(asset)
    if anchor is None or fired:
        idle[asset] = _IdleAnchor(start_ts=current.ts, start_price=current.close)
        return

    if anchor.start_price == 0:
        return

    elapsed_hours = (current.ts - anchor.start_ts) / _SECONDS_PER_HOUR
    if elapsed_hours < IDLE_SPAN_MIN_HOURS:
        return

    move_pct = abs(current.close - anchor.start_price) / anchor.start_price
    if move_pct > MOVE_THRESHOLD_PCT:
        telemetry.idle_spans.append((anchor.start_ts, current.ts, asset, move_pct))
        idle[asset] = _IdleAnchor(start_ts=current.ts, start_price=current.close)
