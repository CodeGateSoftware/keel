"""Historical backtest engine.

Walks a single-instrument candle series bar-by-bar, driving a `Rule` to produce a
`BacktestResult`. Per spec §12 (and the supporting knowledge-base sources §2.3/§4.2/
§20.2/§20.5):

- **Intrabar resolution:** a rule's `Setup` (entry/stop/target) is a *pending* order;
  when a single bar's range spans two of these levels at once (e.g. both entry and
  stop, or both stop and target), the order they were touched in is ambiguous from
  that bar alone. We resolve it using `finer_candles` covering that bar's time span,
  falling back to the conservative outcome ("backtesting exists to lower
  expectations, not raise them") when finer data isn't available or is still
  ambiguous at that resolution: entry-vs-stop ambiguity **invalidates** the trade
  entirely (no fill ever happened); stop-vs-target ambiguity in an open position
  resolves to the stop (a loss).
- **No overlap:** `detect()` is only called while **flat** — one instrument, one position at a
  time. A rule whose condition would fire on every bar still yields only sequential,
  non-overlapping trades. What enforces that is the *open position* check, not the pending one:
  while flat with an unfilled `Setup`, the rule is re-asked every bar and the stale setup is
  replaced (#254). Carrying it instead meant a setup whose entry was never revisited pinned
  `pending` for the rest of the series and `detect()` was never called again — the engine
  switched its own detector off, indistinguishably from a rule that found no more setups.
  Re-detecting is not a tunable ("expire after N bars"): it is what production does, since
  `strategy/engine.py::evaluate` calls `detect()` once per cycle unconditionally and keeps no
  pending-setup state between cycles.
- **Costs:** `slippage_pct` worsens the fill price on both entry (paid) and exit
  (received); `fee_pct` is charged on both legs' notional. This models spread +
  slippage + fees (§4.2).

Position sizing (money management) is out of scope for this module (see the future
`money_mgmt.py`): every trade uses a fixed 1-unit notional, sufficient for computing
win-rate/expectancy/drawdown/R-multiples used by the promotion gate (task 9).

This module deliberately does not import any concrete `Rule` implementation — it is
driven purely through the `Rule` ABC from `keel.strategy.rules.base`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.strategy.rules.base import Rule, Setup, Trade
from keel.strategy.stats import BacktestResult, summarize
from keel.types import Candle, Granularity, Side

__all__ = ["TAKER_FEE_PCT", "BacktestResult", "backtest"]

# The default rate `backtest` charges per leg, and the reason it is the TAKER rate.
#
# This module fills market-style: a pending `Setup` fills the moment a later bar's range
# touches its entry, at that level plus slippage. That is a marketable order crossing the
# spread -- taker behaviour -- so the taker rate is the one that prices it. Until #247 this
# default was the MAKER rate (0.006) while the fill model was unchanged, and the two halves of
# the project disagreed **in writing**: `config.yaml`'s own `fees:` comment says "taker_pct is
# the sim's default -- it fills market-style at next-bar open", and `keel_core.config
# .FeesConfig` has carried `taker_pct = 0.012` as its default the whole time. The config was
# the half that was right about the execution model.
#
# The consequence was not cosmetic. Fees are charged on BOTH legs, so round-trip friction
# (2 x fee + 2 x slippage) ran at 1.30% of notional instead of 2.50% -- a 1.92x understatement
# of the dominant cost term for a strategy whose per-trade edge is the same order of magnitude
# as its costs. `promotion.can_promote` reads expectancy, win rate and realized R:R straight off
# these stats, so the promotion gate itself was evaluating rules at half the price of trading
# them (`docs/experiments/2026-08-11-hourly-backtest-turtle-breakout.md` §5).
#
# Where a caller HAS a loaded `Config`, it should thread `config.fees.taker_pct` in explicitly
# rather than lean on this constant -- a deployment on a different volume tier or venue must be
# able to move the rate without editing code. This default exists for callers that genuinely
# have no config (library use, tests, ad-hoc analysis). It is deliberately the conservative
# choice of the two published rates: a default that overstates cost cannot manufacture an edge
# that isn't there, and a default that understates it already did exactly that.
#
# `tests/strategy/test_backtest.py::test_taker_fee_constant_tracks_the_config_schema_default`
# fails if this drifts from `FeesConfig.taker_pct`.
TAKER_FEE_PCT = Decimal("0.012")

# Default key used to present the single candle series to `Rule.detect()`/
# `Rule.exit_signal()` in the `dict[Granularity, list[Candle]]` shape the interface
# expects, for a rule that doesn't declare its own trading timeframe (`Dca`). Rules that
# do declare one (`granularity`/`timeframe`) are keyed by that instead (see
# `_rule_trading_tf`), so a daily-native rule like `TurtleBreakout` receives its candles
# under `ONE_DAY`. True multi-timeframe backtesting is the evaluation engine's job (task 7),
# not this module's.
_TRADING_TF = Granularity.ONE_HOUR


def _rule_trading_tf(rule: Rule) -> Granularity:
    """The rule's trading timeframe, mirroring `engine._trading_granularity`'s attribute
    lookup order (`granularity` then `timeframe`), falling back to `_TRADING_TF` (ONE_HOUR)
    for a rule that declares neither. Keys the per-bar window so each rule receives its
    single series under the granularity it actually reads.
    """
    for attr in ("granularity", "timeframe"):
        value = getattr(rule, attr, None)
        if isinstance(value, Granularity):
            return value
    return _TRADING_TF


@dataclass
class _OpenPosition:
    setup: Setup
    entry_fill: Decimal
    entry_ts: int
    mfe: Decimal
    mae: Decimal


def _touches(candle: Candle, price: Decimal) -> bool:
    return candle.low <= price <= candle.high


def _resolve_order(
    idx: int,
    candles: list[Candle],
    finer_candles: list[Candle] | None,
    levels: dict[str, Decimal],
) -> str | None:
    """Determine which of two-or-more `levels` (both touched within `candles[idx]`)
    was touched first, by replaying `finer_candles` covering that bar's time span.

    Returns the winning level's key, or `None` if it cannot be resolved (no finer
    coverage, or still ambiguous even at that resolution).
    """
    if not finer_candles:
        return None

    span_start = candles[idx].ts
    span_end = candles[idx + 1].ts if idx + 1 < len(candles) else None
    window = sorted(
        (
            fc
            for fc in finer_candles
            if fc.ts >= span_start and (span_end is None or fc.ts < span_end)
        ),
        key=lambda c: c.ts,
    )
    for fc in window:
        touched = [name for name, price in levels.items() if _touches(fc, price)]
        if len(touched) == 1:
            return touched[0]
        if len(touched) > 1:
            return None  # still ambiguous even at finer resolution
    return None


def _close_trade(
    position: _OpenPosition,
    exit_price: Decimal,
    exit_ts: int,
    fee_pct: Decimal,
    slippage_pct: Decimal,
) -> Trade:
    qty = Decimal(1)
    entry_fill = position.entry_fill
    exit_fill = exit_price * (Decimal(1) - slippage_pct)
    entry_fee = entry_fill * qty * fee_pct
    exit_fee = exit_fill * qty * fee_pct
    pnl = (exit_fill - entry_fill) * qty - entry_fee - exit_fee

    risk = (entry_fill - position.setup.stop) * qty
    r_multiple = pnl / risk if risk != 0 else None

    if pnl > 0:
        outcome = "win"
    elif pnl < 0:
        outcome = "loss"
    else:
        outcome = "scratch"

    return Trade(
        entry_ts=position.entry_ts,
        exit_ts=exit_ts,
        entry=entry_fill,
        exit=exit_fill,
        qty=qty,
        side=Side.BUY,
        pnl=pnl,
        r_multiple=r_multiple,
        mfe=position.mfe,
        mae=position.mae,
        outcome=outcome,
    )


def _open_trade(position: _OpenPosition) -> Trade:
    return Trade(
        entry_ts=position.entry_ts,
        exit_ts=None,
        entry=position.entry_fill,
        exit=None,
        qty=Decimal(1),
        side=Side.BUY,
        pnl=None,
        r_multiple=None,
        mfe=position.mfe,
        mae=position.mae,
        outcome="open",
    )


def backtest(
    rule: Rule,
    candles: list[Candle],
    finer_candles: list[Candle] | None = None,
    fee_pct: Decimal = TAKER_FEE_PCT,
    slippage_pct: Decimal = Decimal("0.0005"),
) -> BacktestResult:
    """Simulate `rule` over `candles` (ascending by `ts`), one position at a time.

    `finer_candles` (optional) is a finer-granularity series covering the same
    period, consulted only to resolve intrabar ambiguity (a bar whose range spans
    two of entry/stop/target at once).

    `fee_pct` defaults to `TAKER_FEE_PCT`, the rate that matches this module's own
    market-style fill model; see that constant for why, and prefer threading
    `config.fees.taker_pct` in from a loaded `Config` when the caller has one. Whatever a
    caller passes, it should **report the rate alongside the result** -- a profit factor
    printed without its fee cannot be checked by the person reading it.
    """
    trades: list[Trade] = []
    position: _OpenPosition | None = None
    pending: Setup | None = None
    trading_tf = _rule_trading_tf(rule)

    for i, candle in enumerate(candles):
        candles_by_tf = {trading_tf: candles[: i + 1]}

        if position is None and pending is None:
            pending = rule.detect(candles_by_tf)
            continue

        if position is None and pending is not None:
            entry_touched = _touches(candle, pending.entry)
            stop_touched = _touches(candle, pending.stop)
            if not entry_touched:
                # Not filled this bar (whether or not the stop alone was touched — the pending
                # order never triggered, so the stop is irrelevant until entry is reached).
                #
                # RE-DETECT rather than carry the stale setup forward (#254). Keeping it meant a
                # setup whose entry was never revisited pinned `pending` for the rest of the
                # series, so the `pending is None` branch above never ran again and `detect()`
                # was never called again — the simulator switched its own detector off, silently,
                # and the output was indistinguishable from a rule that simply found no more
                # setups. Measured: `rsi_meanrev` on UNI-USD at oversold=35 stopped detecting in
                # November 2021 and sat dead for ~40,000 bars, reporting 9 trades against 309 at
                # the STRICTER oversold=30.
                #
                # Re-detecting is not a heuristic choice like "expire after N bars" — it is what
                # production does. `strategy/engine.py::evaluate` calls `rule.detect()` once per
                # cycle unconditionally and carries no pending-setup state between cycles, so an
                # unexecuted setup is simply re-derived from fresh data. N never existed live.
                #
                # `candles[: i + 1]` is the same window the `pending is None` branch would use on
                # this bar, and the fill attempt above already happened, so this introduces no
                # lookahead: a setup derived on bar i can still only fill on bar i+1 or later.
                pending = rule.detect(candles_by_tf)
                continue
            ambiguous_fill = stop_touched
            if ambiguous_fill:
                order = _resolve_order(
                    i, candles, finer_candles, {"entry": pending.entry, "stop": pending.stop}
                )
                if order != "entry":
                    # Stop breached before (or indistinguishably from) entry:
                    # invalidate — no fill ever happened.
                    pending = None
                    continue
            position = _OpenPosition(
                setup=pending,
                entry_fill=pending.entry * (Decimal(1) + slippage_pct),
                entry_ts=candle.ts,
                mfe=Decimal(0),
                mae=Decimal(0),
            )
            pending = None
            if ambiguous_fill:
                # Finer data was already spent to prove entry hit before stop.
                # Re-checking this same bar's raw (coarse) range for stop/target
                # would spuriously re-trigger the very stop level finer data just
                # showed was touched *before* entry. Defer stop/target resolution
                # for the remainder of this bar to the next bar's coarse data.
                position.mfe = max(position.mfe, candle.high - position.entry_fill)
                position.mae = max(position.mae, position.entry_fill - candle.low)
                continue
            # Stop is known clear this bar (simple, unambiguous fill); fall
            # through to the shared stop/target/exit-signal check below, which
            # for this bar only needs to consider the target.

        assert position is not None  # noqa: S101 - narrows type for the checks below
        position.mfe = max(position.mfe, candle.high - position.entry_fill)
        position.mae = max(position.mae, position.entry_fill - candle.low)

        stop = position.setup.stop
        target = position.setup.target
        stop_touched = _touches(candle, stop)
        target_touched = _touches(candle, target)

        exit_price: Decimal | None = None
        if stop_touched and target_touched:
            order = _resolve_order(i, candles, finer_candles, {"target": target, "stop": stop})
            exit_price = target if order == "target" else stop
        elif stop_touched:
            exit_price = stop
        elif target_touched:
            exit_price = target
        elif rule.exit_signal(position.setup, candles_by_tf):
            exit_price = candle.close

        if exit_price is not None:
            trades.append(_close_trade(position, exit_price, candle.ts, fee_pct, slippage_pct))
            position = None

    if position is not None:
        trades.append(_open_trade(position))

    return summarize(trades)
