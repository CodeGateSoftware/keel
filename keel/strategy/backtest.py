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
- **Gap-through-stop fills:** a bar that trades ENTIRELY below the protective stop (`high < stop`)
  still triggers the exit, filling at that bar's OPEN — worse than the stop, the honest fill for a
  level passed wholesale before any trade at it (the exit-side mirror of the market-order entry
  fill above). Containment alone (`low <= stop <= high`) let such a bar slip past silently,
  stranding a ratcheted trail above every later bar while the ratchet refused to lower it (#442
  review). The TARGET deliberately keeps containment-only touches: a bar entirely above it is not
  a touch, because filling that gap at its open would improve the exit — the flattering direction
  this engine refuses.
- **No overlap:** `detect()` is only called while **flat** — one instrument, one position at a
  time. A rule whose condition would fire on every bar still yields only sequential,
  non-overlapping trades. It is the *open position* that enforces this. (Under the touch-fill
  model an unfilled setup could pin `pending` forever and silently switch the detector off; #254
  fixed that by re-detecting each bar, and #257 makes the situation unreachable — every pending
  now fills on the very next bar.)
- **Costs:** `slippage_pct` worsens the fill price on both entry (paid) and exit
  (received); `fee_pct` is charged on both legs' notional. This models spread +
  slippage + fees (§4.2). Since #259 the slippage may be PER PRODUCT: pass
  `slippage_by_product` to price each product's fills from its own liquidity statistic
  (`slippage_for_quote_volume`), leaving the flat `slippage_pct` as the default and the
  fallback for products without one.
- **Managed stops (#442):** a rule whose `params` carry `trail_atr_mult`/`be_roll_rr` has
  its stop RATCHETED by `strategy.exit_policy` at each bar the position survives — touch
  checks run against the level carried INTO the bar, management runs at bar end on the
  completed bar, and the stop never widens (the sim-side statement of rail 9). A rule
  without the knobs (turtle by design, every pre-#442 row) trades exactly as before the
  wiring existed — identity pinned by the unit-identity tests plus the unchanged
  pre-existing suite with the wiring live (the later gap-through-stop fill above is a
  separate semantic that applies to static stops too, tested in its own right).

Position sizing (money management) is out of scope for this module (see the future
`money_mgmt.py`): every trade uses a fixed 1-unit notional, sufficient for computing
win-rate/expectancy/drawdown/R-multiples used by the promotion gate (task 9).

This module deliberately does not import any concrete `Rule` implementation — it is
driven purely through the `Rule` ABC from `keel.strategy.rules.base`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from keel.strategy.exit_policy import ExitPolicy, next_stop, policy_for, trailing_atr
from keel.strategy.rules.base import Rule, Setup, Trade, TradeOutcome
from keel.strategy.stats import BacktestResult, summarize
from keel.types import Candle, Granularity, Side

__all__ = [
    "SLIPPAGE_CAP_PCT",
    "SLIPPAGE_FLOOR_PCT",
    "SLIPPAGE_REFERENCE_QUOTE_VOLUME",
    "SLIPPAGE_TAIL_PRODUCT",
    "SLIPPAGE_TAIL_QUOTE_VOLUME",
    "TAKER_FEE_PCT",
    "BacktestResult",
    "SlippageAssumption",
    "backtest",
    "slippage_for_quote_volume",
]

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

# -- #259: per-product slippage scaled from liquidity -------------------------------------------
#
# Until #259 `backtest()` charged one global 5bp on both legs of every trade on every product.
# Since #257 made entries fill at the next bar's open as MARKET orders, that constant is the
# ONLY term modelling spread crossing and market impact -- precisely what a market order pays.
# The 24-product corpus spans orders of magnitude of liquidity (median daily quote volume,
# measured over the cached ONE_DAY bars on 2026-08-16: BTC $571M, ETH $337M, SOL $109M ...
# WLD $1.2M, TON $370K -- reproduce with, from a deployment root:
# `python -c "from decimal import Decimal; from keel.data.repository import Repository;
# from keel.compliance.screen import median_daily_quote_volume; import sqlite3;
# print(median_daily_quote_volume(Repository(sqlite3.connect('keel.db')).get_candles(
# 'BTC-USD', 'ONE_DAY')))"`, full cached history, upper median on even-length series),
# so a single rate was plausible for BTC and optimistic for the tail by
# an unmeasured factor -- and the error ran in the flattering direction on exactly the thin
# assets that kept surfacing as apparent outliers (TON gross PF 3.751 on n=9; WLD 2.810 on n=12).
#
# The mapping below is an ASSUMPTION, not a measurement, and is documented and reported as one.
# Its functional form is the standard practitioner prior -- cost scales with the SQUARE ROOT of
# the inverse volume ratio -- with every parameter a deliberate conservative choice:

#: The per-leg rate the most liquid products pay, and the FALLBACK for any product without a
#: liquidity statistic. Numerically the old global constant (5bp): the liquid end of the corpus
#: was never the problem, so the correction leaves it exactly where it was.
SLIPPAGE_FLOOR_PCT = Decimal("0.0005")

#: The thinnest product in the measured corpus, and the median daily quote volume that makes it
#: the tail (measured 2026-08-30 over full cached ONE_DAY history; 5,902 cached ONE_HOUR bars).
#: Named constants rather than a number left in prose, because `SLIPPAGE_CAP_PCT` below is
#: DERIVED from them and `tests/strategy/test_backtest.py::TestTheCapIsDerivedFromTheCorpusTail`
#: re-does the arithmetic -- the three numbers cannot drift apart silently, which is the whole
#: difference between a derived bound and a magic number.
#:
#: Re-derive from a deployment root, the same one-liner #259 documents for the anchor, pointed
#: at the tail and repeated across cached products to confirm nothing is thinner:
#: `python -c "from keel.data.repository import Repository;
#: from keel.compliance.screen import median_daily_quote_volume; import sqlite3;
#: print(median_daily_quote_volume(Repository(sqlite3.connect('keel.db')).get_candles(
#: 'TON-USD', 'ONE_DAY')))"`, full cached history, upper median on even-length series.
SLIPPAGE_TAIL_PRODUCT = "TON-USD"
SLIPPAGE_TAIL_QUOTE_VOLUME = Decimal("369944.11414")

#: The per-leg rate the thinnest products are charged: 183.8bp, and deliberately NOT a round
#: number (#523). It is this module's own curve evaluated at the corpus tail --
#: `SLIPPAGE_FLOOR_PCT * sqrt(SLIPPAGE_REFERENCE_QUOTE_VOLUME / SLIPPAGE_TAIL_QUOTE_VOLUME)`,
#: 183.8175bp, quantised to the tenth of a basis point every caller reports at (the 0.0176bp
#: discarded is 1e-4 of the value, an order of magnitude below what any consumer prints). So
#: the cap is no longer a second cost estimate chosen independently of the model: it IS the
#: model, read at the thinnest thing that has been measured, and it binds only for something
#: THINNER than anything measured.
#:
#: What it replaced, and why that could not stand. Until #523 this was 0.005 -- 50bp, "a round
#: cap in the tens of bp", chosen so it bound at exactly 100x below the anchor. That
#: relationship was legible, and it was also the defect. The clamp bound at $5M/day while
#: `compliance.screen.ScreenPolicy.min_median_daily_volume` admits from $1M/day, so every
#: product in that band was simultaneously admissible and capped: 17 of 30 cached products
#: capped, 16 of them admissible, all charged an identical 50.0bp across a 4.3x spread in
#: liquidity. Inside that band the square-root model degenerated into the flat-fee model
#: #259/#334 shipped to remove -- removed for the liquid half of the universe and silently
#: retained for the thin half, on the half where the error runs in the FLATTERING direction.
#: Measured cost of the correction (`docs/experiments/2026-08-30-slippage-cap-options.md`, 30
#: products x 7 arms, `turtle_breakout`, read at n >= 100): 0.3%-24.0% of net profit factor
#: across the cohort, every one of them in the conservative direction, and no verdict moved,
#: because nothing was net-positive at either rate. The cap was masking severity, not viability.
#:
#: At 183.8bp the clamp binds only below ~$370,015/day -- beneath the admission floor -- so no
#: ADMISSIBLE product is capped today and every admissible product is priced by its own
#: liquidity. The bound survives anyway, for the two jobs removing it outright would not do:
#: volume <= 0 (no evidence of liquidity) still needs a fail-closed answer, and a future asset
#: thinner than the tail still deserves a bound rather than an unbounded extrapolation of a
#: curve fitted nowhere near it. "Cap at the tail" and "no cap at all" are bit-identical on
#: every product cached today, so keeping the bound costs nothing measurable.
#:
#: What this does NOT fix, recorded here because the old cap was hiding it. The curve itself
#: still prices a clip this deployment does not trade: inverting the square-root impact law at
#: these rates implies an order of $26k-$165k, while the live deployment fills $50. The 50bp cap
#: was accidentally holding that overstatement down, so raising it removes one unjustified
#: number and leaves the larger one exposed. That is #626 (keel stores no spread data to price
#: the clip it actually trades) and this constant does not address it. An honest overstatement
#: beats two errors cancelling to a number nobody can defend -- but it is still an
#: overstatement, and this cap does not make the model right.
SLIPPAGE_CAP_PCT = Decimal("0.01838")

#: The median daily quote volume (USD) that maps to the floor: $500M/day. Measured corpus top
#: (BTC) is $571M, just above it -- BTC itself clamps to the floor, and so does anything more
#: liquid. A round number rather than BTC's exact median so the anchor stays honest as the
#: corpus re-measures itself; re-measured medians are reported beside results, this constant
#: is not silently re-derived.
SLIPPAGE_REFERENCE_QUOTE_VOLUME = Decimal("500000000")


def slippage_for_quote_volume(median_daily_quote_volume: Decimal) -> Decimal:
    """Map a product's `compliance.screen.median_daily_quote_volume` to an assumed per-leg
    slippage: `SLIPPAGE_FLOOR_PCT * sqrt(anchor / volume)`, clamped to
    [`SLIPPAGE_FLOOR_PCT`, `SLIPPAGE_CAP_PCT`].

    An ASSUMPTION, not a measurement -- keel stores no book snapshots or realised spreads, so
    liquidity is proxied by the one statistic it does compute. Every parameter is stated above
    as a conservative choice, because a number whose assumptions cannot be recovered is not
    evidence. The mapping is monotone (more liquid -> never more slippage) and bounded at both
    ends: the floor at/below the anchor, and the cap below the corpus tail (~$370,015/day since
    #523, beneath the $1M admission floor -- so the clamp no longer flattens the whole
    admissible-but-thin cohort onto one rate). Volume 0 -- what `median_daily_quote_volume`
    returns for empty input -- maps to the cap: no evidence of liquidity is maximally thin, the
    fail-closed direction.
    """
    if median_daily_quote_volume <= 0:
        return SLIPPAGE_CAP_PCT
    unclamped = (
        SLIPPAGE_FLOOR_PCT * (SLIPPAGE_REFERENCE_QUOTE_VOLUME / median_daily_quote_volume).sqrt()
    )
    return min(max(unclamped, SLIPPAGE_FLOOR_PCT), SLIPPAGE_CAP_PCT)


@dataclass(frozen=True)
class SlippageAssumption:
    """One row of the per-product slippage table a run must print beside its results (#259).

    `median_daily_quote_volume is None` means no statistic was available for the product and
    the FLAT fallback rate was applied -- `fallback` derives from that, so the flag and the
    number it explains cannot disagree. `capped` likewise derives from the rate, and marks the
    rows where the mapping's thin-end bound did the deciding.
    """

    product_id: str
    #: The liquidity statistic the rate was scaled from; `None` when no candles were available.
    median_daily_quote_volume: Decimal | None
    #: The per-leg rate actually applied to this product's fills.
    slippage_pct: Decimal

    @property
    def fallback(self) -> bool:
        return self.median_daily_quote_volume is None

    @property
    def capped(self) -> bool:
        return self.median_daily_quote_volume is not None and self.slippage_pct == (
            SLIPPAGE_CAP_PCT
        )


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
    #: The protective stop CURRENTLY in force -- starts at the setup's own stop and, per
    #: #442, is ratcheted by the rule's exit policy (`strategy.exit_policy`) at each bar
    #: the position survives. Touch checks always run against THIS, the level carried
    #: into the bar; `setup.stop` stays the ORIGINAL risk reference (`_close_trade`'s
    #: R-multiple denominator), never the managed level.
    stop: Decimal


def _touches(candle: Candle, price: Decimal) -> bool:
    return candle.low <= price <= candle.high


def _stop_touched(candle: Candle, stop: Decimal) -> bool:
    """A LONG position's protective-stop touch check: the bar's range spans the level, OR
    the bar gapped entirely below it (`high < stop`) -- the level was passed wholesale
    before any trade at it, and the stop triggered on the bar's first tradeable price.

    Containment alone let such a bar slip past the stop silently -- the gap-through hole
    the #442 review flagged: with a ratcheted trail sitting near price, one gap-down bar
    stranded the stop above every later bar while the ratchet refused to lower it,
    understating trailing losses. Both engines and the paper trader share this predicate,
    so the semantic lives in exactly one place. The TARGET deliberately keeps
    containment-only touches: a bar entirely ABOVE it is not a touch, because filling
    that gap at its open would improve the exit -- the flattering direction this engine
    refuses.
    """
    return _touches(candle, stop) or candle.high < stop


def _stop_exit_price(candle: Candle, stop: Decimal) -> Decimal | None:
    """The price a touched stop fills at on `candle`, or `None` if it was not touched.

    A bar that gapped entirely below the level fills at its OPEN -- worse than the stop,
    the honest fill convention: the first price obtainable after the level was passed
    (the exit-side mirror of #257's market-order entry logic). A bar whose range spans
    the level -- including one whose OPEN gapped above it and whose low then pierced it
    -- fills at the stop itself: the level traded.
    """
    if candle.high < stop:
        return candle.open
    return stop if _touches(candle, stop) else None


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
        touched = [
            # The stop level gets the gap-through semantics (`_stop_touched`); the target
            # stays containment-only -- see `_stop_touched` for why that asymmetry is the
            # conservative direction.
            name
            for name, price in levels.items()
            if (_stop_touched(fc, price) if name == "stop" else _touches(fc, price))
        ]
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

    # Annotated rather than inferred: without it the three branches widen `outcome` to plain
    # `str`, which `Trade.outcome` then rejects. Naming the alias also catches a typo in one of
    # these literals here, at the assignment, instead of at the constructor call below.
    outcome: TradeOutcome
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
    slippage_pct: Decimal = SLIPPAGE_FLOOR_PCT,
    slippage_by_product: Callable[[str], Decimal] | None = None,
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

    `slippage_pct` is the flat per-leg rate, unchanged from before #259 and still the default
    for every caller that does not opt in. `slippage_by_product`, when provided, OVERRIDES it
    per product: it is called once with `rule.product_id` and its return value prices both legs
    of every fill this run. A caller with liquidity statistics should pass a resolver built on
    `slippage_for_quote_volume` and answer `slippage_pct` for products it has no statistic for
    (the fallback), then report the assumptions -- see `SlippageAssumption`. A profit factor
    printed without its assumed slippage has the same problem a profit factor printed without
    its fee rate had.
    """
    #: One rate for the whole run: a rule is bound to exactly one product (`product_id`), so
    #: "per-product slippage" resolves once, here, rather than per fill.
    effective_slippage = (
        slippage_by_product(rule.product_id) if slippage_by_product is not None else slippage_pct
    )
    trades: list[Trade] = []
    position: _OpenPosition | None = None
    pending: Setup | None = None
    trading_tf = _rule_trading_tf(rule)
    #: The rule's per-family stop-management policy (#442): ratchet-only trailing /
    #: break-even roll, OFF for every rule whose params do not ask for it (turtle by
    #: design, and every pre-#442 rule row). See `strategy/exit_policy.py`.
    exit_policy: ExitPolicy = policy_for(rule)
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
                entry_fill=candle.open * (Decimal(1) + effective_slippage),
                entry_ts=candle.ts,
                mfe=Decimal(0),
                mae=Decimal(0),
                stop=pending.stop,
            )
            pending = None
            # Fall through to the shared stop/target block for the REMAINDER of this bar. Unlike
            # the old unambiguous-fill path, the stop is NOT known clear here — we filled at the
            # open, so this bar's range can reach either level, and the shared block's
            # stop-vs-target `_resolve_order` is exactly the right adjudicator.

        assert position is not None  # noqa: S101 - narrows type for the checks below
        position.mfe = max(position.mfe, candle.high - position.entry_fill)
        position.mae = max(position.mae, position.entry_fill - candle.low)

        # The MANAGED stop (the level carried into this bar), not the setup's original:
        # the exit policy (#442) may have ratcheted it on a prior bar. R-multiples still
        # divide by the ORIGINAL risk (`_close_trade` reads `position.setup.stop`).
        stop = position.stop
        target = position.setup.target
        #: The stop's exit fill: `None` when the bar never reached it, the bar's OPEN
        #: when it gapped entirely through it (`_stop_exit_price`).
        stop_exit = _stop_exit_price(candle, stop)
        stop_touched = stop_exit is not None
        target_touched = _touches(candle, target)

        exit_price: Decimal | None = None
        if stop_touched and target_touched:
            order = _resolve_order(i, candles, finer_candles, {"target": target, "stop": stop})
            # `stop_exit` is exactly `stop` in this branch: a bar that gapped through the
            # stop cannot also touch the target (target > stop for a long), so the
            # both-touched case is always an in-range touch.
            exit_price = target if order == "target" else stop_exit
        elif stop_touched:
            exit_price = stop_exit
        elif target_touched:
            exit_price = target
        elif rule.exit_signal(position.setup, candles_by_tf):
            exit_price = candle.close

        if exit_price is not None:
            trades.append(
                _close_trade(position, exit_price, candle.ts, fee_pct, effective_slippage)
            )
            position = None
        else:
            # Stop management runs at BAR END, on the bar that just completed, and the
            # resulting level binds from the NEXT bar (#442 -- see `exit_policy`'s
            # module docstring for the no-lookahead/live-parity sequencing argument).
            # Live, the bracket rests at the old level until a management cycle replaces
            # it; touch checks above ran against exactly that old level. An OFF policy
            # (every rule without the knobs) returns the stop unchanged, so only the
            # trailing arm pays for the ATR read.
            position.stop = next_stop(
                exit_policy,
                position.entry_fill,
                position.setup.stop,
                position.stop,
                candle,
                trailing_atr(candles_by_tf[trading_tf], exit_policy.atr_period)
                if exit_policy.trail_atr_mult is not None
                else None,
            )

    if position is not None:
        trades.append(_open_trade(position))

    return summarize(trades)
