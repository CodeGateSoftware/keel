"""Historical backtest engine.

Walks a single-instrument candle series bar-by-bar, driving a `Rule` to produce a
`BacktestResult`. Per spec §12 (and the supporting knowledge-base sources §2.3/§4.2/
§20.2/§20.5):

- **Entries fill at the next bar's open (#257).** Production places MARKET orders —
  `execution/executor.py::_order_row` writes `order_type="market"`, `limit_price=None`, and keeps
  the setup's own price only as `expected_fill`. So a `Setup` detected on bar `i` is filled at
  `candles[i+1].open` plus slippage, the first price obtainable once the signal exists.
  **`Setup.entry` is informational here**, exactly as `expected_fill` is live; risk is measured
  from the achieved fill against the setup's stop, never from the quoted entry.

  The previous model waited for a later bar's range to *touch* `entry` and filled at that level.
  That gave the simulator free optionality on the entry price (unfavourable entries were silently
  declined, since a setup only became a trade if the market offered the chosen level) and
  unbounded patience — neither of which production has, and both of which flattered results.

  A consequence worth naming: a rule that encodes a *confirmation* condition in its entry price no
  longer gets one. `pullback_continuation` sets `entry = signal_candle.high + buffer` precisely to
  demand follow-through, and a market fill takes trades it meant to decline. That is not
  introduced here — it is what the live box already does, and modelling it faithfully is the
  point. Making the executor honour a stop/limit entry is the alternative, and belongs in the
  executor rather than in this module (#257, "Option B").
- **Intrabar resolution:** when a single bar's range spans both the stop and the target, the order
  they were touched in is ambiguous from that bar alone. We resolve it using `finer_candles`
  covering that bar's time span, falling back to the conservative outcome ("backtesting exists to
  lower expectations, not raise them") when finer data isn't available or is still ambiguous at
  that resolution: stop-vs-target ambiguity resolves to the **stop** (a loss). This applies on the
  fill bar too — filling at the open means the rest of that bar can reach either level.
- **No overlap:** `detect()` is only called while **flat** — one instrument, one position at a
  time. A rule whose condition would fire on every bar still yields only sequential,
  non-overlapping trades. It is the *open position* that enforces this. (Under the touch-fill
  model an unfilled setup could pin `pending` forever and silently switch the detector off; #254
  fixed that by re-detecting each bar, and #257 makes the situation unreachable — every pending
  now fills on the very next bar.)
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
# This module fills market-style: a `Setup` detected on bar i fills at bar i+1's OPEN plus
# slippage (#257), mirroring the market order `execution/executor.py` actually places. That is a
# marketable order crossing the spread -- taker behaviour -- so the taker rate prices it.
#
# #257 also removed the one wrinkle in this justification. Under the previous touch-fill model the
# claim held only for a rule whose entry sat ABOVE the market (a breakout, i.e. a stop order,
# genuinely marketable) and was wrong for one whose entry sat BELOW it (`rsi_meanrev` buying near
# support, which touching would make a resting MAKER fill). Now that every entry is a market
# order at the open, the taker rate is the right one for all of them, unconditionally.
#
# Until #247 this default was the MAKER rate (0.006) while the fill model was unchanged, and the
# two halves of
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
    #: Bars the current `pending` has survived. Since #257 a setup detected on bar i is filled
    #: unconditionally at bar i+1's open, so this can only ever reach 1 -- see the invariant
    #: check at the top of the loop.
    pending_age = 0

    for i, candle in enumerate(candles):
        candles_by_tf = {trading_tf: candles[: i + 1]}

        # ENGINE INVARIANT: a pending setup is never carried across more than one bar.
        #
        # This is an assertion, not a heuristic, and it has no false-positive mode: since #257
        # every pending fills at the next bar's open, so the correct value is exactly 1 for every
        # rule on every series. It is checked rather than thresholded, needs no per-asset tuning,
        # and cannot fire on legitimate behaviour.
        #
        # It exists because #254 was invisible for the life of the project. A setup whose entry
        # and stop were both never revisited pinned `pending` forever, `detect()` was never called
        # again, and the engine silently switched its own detector off. On UNI-USD this counter
        # would have read ~40,000. Nothing else caught it: 2,712 tests passed, and a frozen
        # backtest is indistinguishable from a selective one in any summary output.
        #
        # The obvious alternative -- warn when trades stop long before the series ends -- was
        # considered and REJECTED. #257 made the freeze structurally impossible, so a dead tail
        # now only ever means the rule genuinely stopped firing (a regime change, a threshold too
        # strict for recent volatility). Such a warning would fire exclusively on legitimate runs
        # and spend the attention budget a real alert needs.
        #
        # If resting entry orders are ever reintroduced (#260's "Option B"), this bound stops
        # being 1 -- and the value it becomes IS the cancel/replace policy, stated in one place.
        if pending is not None:
            pending_age += 1
            if pending_age > 1:
                raise AssertionError(
                    f"engine invariant violated: rule {rule.name!r} on "
                    f"{getattr(pending, 'product_id', '?')} carried a pending setup across "
                    f"{pending_age} bars (bar index {i}). Since #257 every pending fills at the "
                    f"next bar's open, so this cannot exceed 1 -- the fill path has regressed "
                    f"(cf. #254, where the detector silently stopped being called)."
                )
        else:
            pending_age = 0

        if position is None and pending is None:
            pending = rule.detect(candles_by_tf)
            continue

        if position is None and pending is not None:
            # PRODUCTION PLACES MARKET ORDERS (#257). `execution/executor.py::_order_row` writes
            # `order_type="market"`, `limit_price=None`, and records the setup's own price only as
            # `expected_fill`. So live never rests an order at `Setup.entry` and never waits for
            # price to come to it: when `engine.evaluate` emits a signal and the rails pass, the
            # executor buys at market on that cycle.
            #
            # This branch runs on the bar AFTER detection, so `candle.open` is the first price
            # obtainable once the signal exists — the faithful analogue of that market order, and
            # the earliest fill that involves no lookahead.
            #
            # What this replaces, and why it had to go: the previous model held the setup until a
            # later bar's range TOUCHED `entry`, then filled AT that level. That granted the
            # simulator two things production does not have — free optionality on the entry price
            # (a setup only became a trade if the market offered the chosen level, so unfavourable
            # entries were silently declined) and unbounded patience. Both flatter results, and
            # the bias runs opposite to #254's, which suppressed trades.
            #
            # `Setup.entry` is therefore now INFORMATIONAL in the backtest, exactly as
            # `expected_fill` is live. Risk is measured from the achieved fill against the setup's
            # stop (`_close_trade`), never from the quoted entry, so nothing downstream reads it.
            #
            # Consequence worth stating: a rule whose entry encodes a CONFIRMATION condition no
            # longer gets it. `pullback_continuation` sets `entry = signal_candle.high + buffer`
            # precisely to require follow-through, and under a market fill it takes trades it
            # meant to decline. That is not introduced here — it is what the live box already
            # does, and modelling it is the point. Fixing it belongs in the executor, not here
            # (the Option B discussion on #257).
            position = _OpenPosition(
                setup=pending,
                entry_fill=candle.open * (Decimal(1) + slippage_pct),
                entry_ts=candle.ts,
                mfe=Decimal(0),
                mae=Decimal(0),
            )
            pending = None
            # Fall through to the shared stop/target block for the REMAINDER of this bar. Unlike
            # the old unambiguous-fill path, the stop is NOT known clear here — we filled at the
            # open, so this bar's range can reach either level, and the shared block's
            # stop-vs-target `_resolve_order` is exactly the right adjudicator.


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
