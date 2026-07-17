"""Multi-asset portfolio simulator (plan Task 6,
`docs/superpowers/plans/2026-07-17-engine-validation-simulation.md`).

Walks the ascending UNION of `ONE_HOUR` timestamps across every asset in `candles_by_asset`
between `start_ts` and `end_ts`, driving each asset's assigned `Rule`(s) through
`strategy.engine.evaluate()` on a rolling, lookahead-free window (`WINDOW_BARS` hourly bars plus
every daily bar with `ts <= t`), opening/closing at most one position per asset on a single
shared `sim.account.SimAccount`, and recording per-trade (`SimTrade`) and portfolio-level
(`SimTelemetry`) data for the report/verdict stage (Task 7, `sim/report.py`).

**Interpretive notes** (the plan's Task 6 prose leaves a few specifics implicit):

- **`cts_factor_populated` / `rejected_for_missing_input`** (both `dict[str, int]`, keyed by CTS
  context-key name -- see `strategy.indicators_cts.DEFAULT_WEIGHTS`): for *every* ENTER signal
  `evaluate()` emits (whether or not it ends up opened), the engine's own CTS context assembly is
  reused verbatim (`engine._assemble_cts_context` + `indicators_cts.score`, not reimplemented) to
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
from keel.sim.account import OpenIntent, SimAccount
from keel.strategy import engine, indicators_cts
from keel.strategy.backtest import _resolve_order, _touches
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

__all__ = [
    "IDLE_SPAN_MIN_HOURS",
    "MOVE_THRESHOLD_PCT",
    "WINDOW_BARS",
    "SimResult",
    "SimTelemetry",
    "SimTrade",
    "run",
]

# Rolling ONE_HOUR window size handed to `Rule.detect`/`engine.evaluate` -- large enough for
# every existing rule's longest lookback (EMA-200, 90-bar-equivalent structure) without carrying
# an asset's entire multi-year history into every bar's evaluation.
WINDOW_BARS = 300

# Idle-span telemetry: a "no signal fired while price moved a lot" span is recorded once both
# (a) the gap since the last signal spans at least this many hours, and (b) the cumulative move
# across it exceeds MOVE_THRESHOLD_PCT (5%).
IDLE_SPAN_MIN_HOURS = 24
MOVE_THRESHOLD_PCT = Decimal("0.05")

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


def _window_1h(hourly: list[Candle], idx: int) -> list[Candle]:
    start = max(0, idx - WINDOW_BARS + 1)
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
) -> SimResult:
    """Simulate `rules` (each bound to one asset via `Rule.product_id`) over `candles_by_asset`.

    See the module docstring for the loop's exact semantics (no-lookahead window assembly,
    conservative stop-vs-target resolution, next-bar-open fills, monthly contributions, daily
    equity sampling, idle-span telemetry).
    """
    account = SimAccount(fee_pct, slippage_pct)
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

    held: dict[str, _Held] = {}
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

        for asset, hourly in hourly_by_asset.items():
            idx = hourly_index[asset].get(t)
            if idx is None:
                continue  # this asset has no bar at this timestamp

            current = hourly[idx]
            latest_price[asset] = current.close

            daily_idx = bisect.bisect_right(daily_ts_by_asset[asset], t)
            candles_by_tf: dict[Granularity, list[Candle]] = {
                Granularity.ONE_HOUR: _window_1h(hourly, idx),
                Granularity.ONE_DAY: daily_by_asset[asset][:daily_idx],
            }

            if asset in held:
                _process_held(
                    asset, idx, hourly, current, candles_by_tf, held, account, trades, telemetry
                )
                continue

            asset_rules = rules_by_asset.get(asset)
            if not asset_rules:
                continue

            fired = _process_flat(
                asset,
                idx,
                hourly,
                candles_by_tf,
                asset_rules,
                account,
                config,
                latest_price,
                held,
                telemetry,
                t,
            )
            _track_idle(asset, current, fired, idle, telemetry)

        day_start, _ = _utc_day_bounds(t)
        if day_start != last_day_start:
            equity_curve.append((t, account.mark_to_market(latest_price)))
            last_day_start = day_start

    for asset, h in held.items():
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
    )


# ---------------------------------------------------------------------------
# Per-bar processing
# ---------------------------------------------------------------------------


def _process_held(
    asset: str,
    idx: int,
    hourly: list[Candle],
    current: Candle,
    candles_by_tf: dict[Granularity, list[Candle]],
    held: dict[str, _Held],
    account: SimAccount,
    trades: list[SimTrade],
    telemetry: SimTelemetry,
) -> None:
    """Resolve `asset`'s held position against the current bar: conservative intrabar
    stop-vs-target resolution first (via `strategy.backtest`'s reused `_touches`/`_resolve_order`
    -- with no finer-than-hourly series ever available here, ambiguity always resolves to the
    stop, exactly matching `backtest.py`'s documented no-finer-data fallback), then
    `Rule.exit_signal` if neither level was touched. DCA-class (no-stop) positions skip the
    price-touch checks entirely -- they have no stop/target to resolve, per `dca.py`'s design."""
    h = held[asset]
    setup = h.setup
    is_dca = _is_dca_setup(setup)

    h.mfe = max(h.mfe, current.high - h.entry_fill)
    h.mae = max(h.mae, h.entry_fill - current.low)

    exit_price: Decimal | None = None

    if not is_dca:
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

    pnl = account.close(asset, exit_price, current.ts)
    exit_fill = exit_price * (Decimal(1) - account.slippage_pct)

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

    del held[asset]


def _process_flat(
    asset: str,
    idx: int,
    hourly: list[Candle],
    candles_by_tf: dict[Granularity, list[Candle]],
    asset_rules: list[Rule],
    account: SimAccount,
    config: Config,
    latest_price: dict[str, Decimal],
    held: dict[str, _Held],
    telemetry: SimTelemetry,
    now_ts: int,
) -> bool:
    """Evaluate `asset_rules` for `asset` on this bar and open at most one position (one position
    per asset -- `run()` only calls this while `asset` is flat).

    Returns `True` iff `engine.evaluate` produced at least one ENTER signal this bar (opened or
    not) -- the idle-span tracker resets its anchor whenever a signal fires, whether or not it
    was ultimately filled.
    """
    signals = engine.evaluate(asset_rules, candles_by_tf)
    telemetry.signals_emitted += len(signals)
    if not signals:
        return False

    window_1h = candles_by_tf[Granularity.ONE_HOUR]
    rules_by_name = {rule.name: rule for rule in asset_rules}
    opened = False

    for signal in signals:
        setup = signal.setup
        if setup is None:
            continue
        rule = rules_by_name.get(signal.rule_name)
        if rule is None:
            continue

        cts_result = indicators_cts.score(engine._assemble_cts_context(setup, window_1h))
        for factor in cts_result.factors:
            bucket = (
                telemetry.cts_factor_populated
                if factor.present
                else telemetry.rejected_for_missing_input
            )
            bucket[factor.name] = bucket.get(factor.name, 0) + 1

        if opened:
            continue  # only one position per asset per bar

        is_dca = _is_dca_setup(setup)
        try:
            if is_dca:
                budget = setup.context.get("size_usd") or config.dca.budget_usd
                qty = sizing.dca_size(budget, setup.entry)
            else:
                equity = account.mark_to_market(latest_price)
                qty = sizing.size(equity, config.risk_pct, setup.entry, setup.stop)
        except ValueError:
            continue

        notional = sizing.spend(qty, setup.entry)
        intent = OpenIntent(
            asset=asset,
            qty=qty,
            entry=setup.entry,
            stop=None if is_dca else setup.stop,
            notional=notional,
            is_dca=is_dca,
            rule_kind=rule.name,
        )

        ok, _reasons = account.can_open(intent, config, now_ts)
        if not ok:
            continue

        fill_idx = idx + 1
        if fill_idx >= len(hourly):
            continue  # no next bar to fill at -- the signal is lost, not a rejection

        fill_bar = hourly[fill_idx]
        account.open(intent, fill_bar.open, fill_bar.ts)
        pos = account.positions[asset]
        held[asset] = _Held(
            rule=rule,
            setup=setup,
            entry_ts=pos.entry_ts,
            entry_fill=pos.entry_fill,
            qty=pos.qty,
            cts_score=signal.cts_score,
            entry_technique=signal.entry_technique,
        )
        opened = True

    return True


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
