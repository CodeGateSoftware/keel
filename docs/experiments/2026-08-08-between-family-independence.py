#!/usr/bin/env python
"""§80.16 BETWEEN-FAMILY independence — the reusable harness, shaken down on a refuted arm.

Every empirical claim in `docs/experiments/2026-08-08-between-family-independence.md` comes from
this script. It is strictly read-only: it opens the candle cache with `mode=ro`, places no
orders, touches no rails, and writes nothing but stdout.

WHAT THIS IS. §80.16 is the KB's one confirmed hole — four papers, 30,000+ tested rules, two
asset classes, 114 years, and **zero** measurements of signal correlation BETWEEN rule families.
It is a build task, not a sourcing task, and §73.5 makes it non-optional: a second rule class
that turns out correlated is *strictly worse than adding nothing*, because it spends trials
budget and returns one observation's worth of information. `keel/research/independence.py`
implements the five measurements; nothing in the repo drives them over two real rules. The
2026-07-20 cross-HORIZON run (PR #103) was ad hoc and left no script behind. This is that script.

⚠️ WHAT THIS IS NOT — read before quoting any number below.

`rsi_meanrev` is a SETTLED-REFUTED rule class (§74.3): three independent refutations, three
methods, three markets — our own sim (16% win, PF 0.17); §58.10a Katz & McCormick, worst model
in a 36-market 14-year book, worse than random; §74.3 Hudson & Urquhart, −6.33 bp vs
buy-and-hold on Bitcoin at p < 0.01. The KB's instruction is *"Treat RSI mean-reversion as
settled and closed. Build no further variants."*

So this run **cannot and does not test rsi_meanrev's candidacy as a second rule class.**
Independence is a NECESSARY condition under §73.5, never a sufficient one — an uncorrelated
stream with negative edge is still worse than nothing. Arm B is here because it is the only
other risk-defined rule already implemented, which makes it a free shakedown of the pipeline.
The result is a HARNESS CHECK. Ledger status: `diagnostic_only`.

The real candidate §80.16 names is §80.10's weekly time-series momentum (Liu & Tsyvinski),
promoted after §80.14 closed the MACD family. It is not implemented. When it is, it becomes
arm B here and the run becomes a candidacy test.

THREE PASSES, in increasing order of what they settle:

    main()        BETWEEN-FAMILY  turtle vs rsi_meanrev — degenerate, arm B never trades
    calibrate()   CROSS-HORIZON   turtle 40/20 vs 80/40 — reproduces PR #103, validates the pipeline
    cross_asset() CROSS-ASSET     turtle vs ITSELF on another asset — the one that returns a finding

The third is the same five measurements with the underlying, rather than the family or the
horizon, as the only thing varying. It asks whether keel's five live rules are five independent
confirmations of one hypothesis or one hypothesis counted five times, and §73.5's arithmetic
applies to the answer unchanged.

METHOD (§80.16 steps 1–5, verbatim, via `keel.research.independence.compare`):

    1. daily POSITION vector s_t ∈ {0,1}   (long-only ⇒ binary)
    2. Jaccard overlap      #{both}/#{either}
    3. position correlation Pearson over the two position vectors
    4. trade timing         for each A-entry, bars to the nearest B-entry
    5. P&L correlation      Pearson over the two daily P&L series

BOTH ARMS RUN ON `ONE_DAY`. `RsiMeanReversion` defaults to `timeframe=ONE_HOUR` and
`TurtleBreakout` is fixed `ONE_DAY`. Running them on native timeframes would confound FAMILY
separation with HORIZON separation — and horizon was already measured on these assets at 0.508
mean P&L correlation (`2026-07-20-horizon-independence.md`). Holding the bar clock constant is
what isolates the variable §80.16 asks about. Setting `timeframe` is the only departure from
`RsiMeanReversion`'s constructor defaults, and it is declared here rather than tuned.

TWO P&L CONVENTIONS, both reported, neither used to select anything:

- `closed` — the whole net P&L attributed to the exit bar, 0 elsewhere. This is the convention
             `2026-07-20-first-pbo-run.md` used for its CSCV columns. It is a sparse spike
             series and a weak correlation estimator when two rules hold for different lengths.
- `mtm`    — daily mark-to-market: qty·(close_t − close_{t−1}) inside the hold, seeded at
             entry_fill and closed at exit_fill, with the trade's fee drag booked on the exit
             day so the series sums EXACTLY to `Trade.pnl`. Denser, and the returns-correlation
             reading of "daily P&L series".

⭐ The `calibrate()` pass below SETTLED WHICH ONE PR #103 USED, which its write-up never states:
`closed` reproduces `2026-07-20-horizon-independence.md` to three decimals on both assets it
published (BTC 0.802, ETH 0.934), while `mtm` gives 0.813 / 0.759. So `closed` is the figure to
quote for cross-experiment comparison, and any future §80.16 table should stay on it for
consistency. That is a comparability choice fixed by prior work, not a selection on this run's
outcome — both columns are printed either way.

`backtest()` sizes every trade at `qty = 1` unit, so P&L is quote-currency per unit and is NOT
comparable across assets. Correlation is scale-invariant, so per-asset figures are sound; raw
P&L is never pooled across assets here.

Run against the dev venv, reading the deployment's candle cache:

    .venv/bin/python docs/experiments/2026-08-08-between-family-independence.py
    .venv/bin/python docs/experiments/2026-08-08-between-family-independence.py --db path/to.db
"""

from __future__ import annotations

import argparse
import sqlite3
from decimal import Decimal

from keel.analysis.indicators import rsi
from keel.research.independence import IndependenceReport, compare
from keel.strategy.backtest import BacktestResult, backtest
from keel.strategy.rules.base import Rule, Trade
from keel.strategy.rules.rsi_meanrev import RsiMeanReversion
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Candle, Granularity

# -- PRE-DECLARED CONFIGURATION (§78.5: frozen before the run, not tuned during it) -------------

DEFAULT_DB = "/Users/elmehdiaitbrahim/keel/keel.db"

#: The live allowlist (`config.live-sandbox.yaml`), in the settlement currency keel actually
#: trades. PAXG-USD carries only ~456 daily bars (listed 2025-05-08) against ~1,830 for the
#: others; PAXG-USDT has the long history but §71.4a excludes the USDT quote.
ASSETS = ("BTC-USD", "ETH-USD", "PAXG-USD", "ADA-USD", "XLM-USD")

GRANULARITY = Granularity.ONE_DAY

#: Arm A: the SHIPPED turtle, byte-identical to `keel-live.db` rules 1-5.
TURTLE_PARAMS: dict[str, object] = {
    "entry_lookback": 40,
    "exit_lookback": 20,
    "atr_period": 20,
    "atr_stop_mult": Decimal("2"),
    "target_rr": Decimal("6"),
    "adx_period": 14,
    "adx_threshold": 25.0,
    "s1_filter": False,
    "use_macd_confirm": False,
    "min_volume_filter": False,
    "volume_ma_period": 20,
    "volume_mult": 1.2,
}

#: Arm B: `RsiMeanReversion` constructor defaults, with ONLY `timeframe` overridden (see the
#: module docstring on why the bar clock is held constant).
RSI_PARAMS: dict[str, object] = {"timeframe": GRANULARITY}

#: Matches `cli._SIM_FEE_PCT` / `_SIM_SLIPPAGE_PCT`, so figures line up with `keel simulate`.
FEE_PCT = Decimal("0.006")
SLIPPAGE_PCT = Decimal("0.0005")

#: The internal comparator. There is no published between-family benchmark — that absence IS
#: §80.16. The meaningful contrast is against the same five measurements run across HORIZONS of
#: one family on these assets (`2026-07-20-horizon-independence.md`).
CROSS_HORIZON_PNL_CORR = Decimal("0.508")
CROSS_HORIZON_POSITION_CORR = Decimal("0.585")
CROSS_HORIZON_JACCARD = Decimal("0.510")

#: CALIBRATION. The shakedown's real check: re-measure a pair whose answer is already published
#: in-repo and see whether this harness reproduces it. `2026-07-20-horizon-independence.md`
#: reports the shipped 40/20 against 80/40 at P&L correlation 0.802 (BTC) / 0.934 (ETH), with a
#: MEDIAN ENTRY GAP OF ZERO DAYS. A between-family number is only worth reading if the pipeline
#: that produced it can reproduce a cross-horizon number that is already known.
CALIBRATION_ASSETS = ("BTC-USD", "ETH-USD", "PAXG-USD")
CALIBRATION_LADDER = (40, 20, 80, 40)  # (entry_a, exit_a, entry_b, exit_b)
CALIBRATION_EXPECTED = {"BTC-USD": Decimal("0.802"), "ETH-USD": Decimal("0.934")}


# -- data ---------------------------------------------------------------------------------------


def load_candles(db_path: str, product_id: str, granularity: Granularity) -> list[Candle]:
    """Ascending daily candles for one product, read-only."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT ts, o, h, l, c, v FROM candles "
            "WHERE product_id = ? AND granularity = ? ORDER BY ts",
            (product_id, granularity.value),
        ).fetchall()
    finally:
        connection.close()
    return [
        Candle(
            ts=ts,
            open=Decimal(o),
            high=Decimal(h),
            low=Decimal(low),
            close=Decimal(c),
            volume=Decimal(v),
        )
        for ts, o, h, low, c, v in rows
    ]


# -- §80.16 step 1: position vector, and the two P&L series ---------------------------------------


def series_for(
    trades: list[Trade], candles: list[Candle]
) -> tuple[list[int], list[Decimal], list[Decimal], list[int]]:
    """`(positions, pnl_mtm, pnl_closed, entry_indices)` over the daily index of `candles`.

    A day is in-market from the entry bar through the exit bar INCLUSIVE; a still-open trade
    runs to the last bar. `pnl_mtm` sums exactly to each trade's net `pnl` — the fee drag
    (`Trade.pnl` minus the gross `exit_fill − entry_fill`) is booked on the exit bar, so the
    two conventions agree on totals and differ only in how they distribute them over time.
    """
    index_of = {candle.ts: i for i, candle in enumerate(candles)}
    n = len(candles)

    positions = [0] * n
    pnl_mtm = [Decimal(0)] * n
    pnl_closed = [Decimal(0)] * n
    entry_indices: list[int] = []

    for trade in trades:
        start = index_of.get(trade.entry_ts)
        if start is None:
            continue
        end = n - 1 if trade.exit_ts is None else index_of.get(trade.exit_ts, n - 1)
        entry_indices.append(start)

        for i in range(start, end + 1):
            positions[i] = 1

        # Mark-to-market: entry bar marks from the fill, interior bars close-to-close, and the
        # exit bar marks to the exit fill (or to the last close for a still-open trade).
        previous = trade.entry
        for i in range(start, end + 1):
            is_exit_bar = i == end and trade.exit is not None
            mark = trade.exit if is_exit_bar else candles[i].close
            pnl_mtm[i] += (mark - previous) * trade.qty
            previous = mark

        if trade.pnl is not None and trade.exit is not None:
            gross = (trade.exit - trade.entry) * trade.qty
            pnl_mtm[end] += trade.pnl - gross  # fee/slippage drag, booked at the close
            pnl_closed[end] += trade.pnl

    return positions, pnl_mtm, pnl_closed, entry_indices


def by_timestamp(
    trades: list[Trade], candles: list[Candle]
) -> dict[int, tuple[int, Decimal, Decimal]]:
    """`series_for` re-keyed by candle `ts` instead of by position, for cross-ASSET alignment.

    Two assets share a granularity but not an index — PAXG-USD listed in 2025 and carries ~456
    daily bars against ~1,830 for the others, and listings/outages leave holes. Comparing them
    positionally would silently pair BTC's day 400 with PAXG's day 400, which are years apart.
    """
    positions, mtm, closed, _ = series_for(trades, candles)
    return {
        candle.ts: (positions[i], mtm[i], closed[i]) for i, candle in enumerate(candles)
    }


def align(
    a: dict[int, tuple[int, Decimal, Decimal]], b: dict[int, tuple[int, Decimal, Decimal]]
) -> tuple[list[int], list[Decimal], list[Decimal], list[int], list[Decimal], list[Decimal]]:
    """Restrict two ts-keyed series to their common timestamps, ascending.

    Entry indices are deliberately NOT carried across: after intersection they would point into
    the old index. `compare` re-derives entries from the rising edges of the aligned position
    vectors instead, which is what it does by default.
    """
    common = sorted(a.keys() & b.keys())
    a_rows = [a[ts] for ts in common]
    b_rows = [b[ts] for ts in common]
    return (
        [row[0] for row in a_rows],
        [row[1] for row in a_rows],
        [row[2] for row in a_rows],
        [row[0] for row in b_rows],
        [row[1] for row in b_rows],
        [row[2] for row in b_rows],
    )


def effective_breadth(n: int, mean_correlation: Decimal) -> Decimal:
    """`n / (1 + (n−1)·ρ̄)` — effective independent streams under equicorrelation.

    ⚠️ The standard equicorrelated-portfolio approximation, NOT a KB formula and NOT §78.2's
    `N̂ = ρ̂ + (1−ρ̂)·M` (which corrects a TRIALS count, a different quantity). It answers only
    "how many independent evidence streams is a set of `n` correlated assets worth?" and is
    reported here for interpretation, never fed into MinBTL or any gate.
    """
    if n <= 1:
        return Decimal(n)
    denominator = Decimal(1) + Decimal(n - 1) * mean_correlation
    return Decimal(n) / denominator if denominator > 0 else Decimal(n)


def build_arms(product_id: str) -> tuple[TurtleBreakout, RsiMeanReversion]:
    """The two pre-declared arms for one product."""
    return (
        TurtleBreakout(product_id=product_id, **TURTLE_PARAMS),
        RsiMeanReversion(product_id=product_id, **RSI_PARAMS),
    )


def oversold_bounce_count(candles: list[Candle], period: int, oversold: float) -> tuple[int, float]:
    """`(bars firing rsi_meanrev's FIRST gate, min RSI over the series)`.

    `detect()` records no `last_rejection`, so when arm B returns nothing there is no built-in
    way to say which gate declined. This reproduces gate 1 only — `prev_rsi < oversold and
    curr_rsi > prev_rsi` — which is enough to distinguish "the threshold is unreachable on this
    bar clock" from "the support-level gate downstream is what's binding".
    """
    values = rsi([float(candle.close) for candle in candles], period=period)
    finite = [v for v in values if v is not None]
    fires = sum(
        1
        for i in range(1, len(values))
        if values[i - 1] is not None
        and values[i] is not None
        and values[i - 1] < oversold
        and values[i] > values[i - 1]
    )
    return fires, min(finite) if finite else float("nan")


def measure(
    a: Rule, b: Rule, candles: list[Candle]
) -> tuple[IndependenceReport, IndependenceReport, BacktestResult, BacktestResult]:
    """Backtest both rules over `candles` and run the five §80.16 measurements.

    Returns `(mtm_report, closed_report, a_result, b_result)`.
    """
    a_result = backtest(a, candles, fee_pct=FEE_PCT, slippage_pct=SLIPPAGE_PCT)
    b_result = backtest(b, candles, fee_pct=FEE_PCT, slippage_pct=SLIPPAGE_PCT)
    a_pos, a_mtm, a_closed, a_entries = series_for(a_result.trades, candles)
    b_pos, b_mtm, b_closed, b_entries = series_for(b_result.trades, candles)
    return (
        compare(a_pos, b_pos, a_mtm, b_mtm, a_entries, b_entries),
        compare(a_pos, b_pos, a_closed, b_closed, a_entries, b_entries),
        a_result,
        b_result,
    )


# -- report ---------------------------------------------------------------------------------------


def _fmt(value: Decimal | int | None, places: str = "0.001") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    return str(value.quantize(Decimal(places)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Candle cache (default: {DEFAULT_DB})")
    parser.add_argument(
        "--candidates",
        default=None,
        help="Comma-separated product ids to rank by marginal independence vs the live allowlist "
        "(e.g. ZEC-USD,SOL-USD). Measurement only — admits and attests nothing.",
    )
    args = parser.parse_args()

    print("§80.16 between-family independence — turtle_breakout vs rsi_meanrev")
    print(f"db={args.db}  granularity={GRANULARITY.value}  fee={FEE_PCT} slip={SLIPPAGE_PCT}")
    print("⚠️  DIAGNOSTIC / HARNESS SHAKEDOWN. Arm B is settled-refuted (§74.3); this run")
    print("    cannot test its candidacy as a second rule class. See the module docstring.\n")

    header = (
        f"{'asset':10} {'bars':>5} {'A trd':>6} {'B trd':>6} {'A days':>7} {'B days':>7} "
        f"{'both':>5} {'jaccard':>8} {'pos r':>7} {'pnl r':>7} {'pnl r':>7} {'gap':>5}"
    )
    print(header)
    print(f"{'':10} {'':5} {'':6} {'':6} {'':7} {'':7} {'':5} {'':8} {'':7} {'(mtm)':>7} "
          f"{'(clsd)':>7} {'(d)':>5}")
    print("-" * len(header))

    jaccards: list[Decimal] = []
    position_corrs: list[Decimal] = []
    pnl_corrs_mtm: list[Decimal] = []
    pnl_corrs_closed: list[Decimal] = []
    degenerate: list[str] = []

    gate_notes: list[str] = []

    for product_id in ASSETS:
        candles = load_candles(args.db, product_id, GRANULARITY)
        if not candles:
            degenerate.append(f"{product_id}: no {GRANULARITY.value} candles cached")
            continue

        turtle, rsi_rule = build_arms(product_id)
        report, closed_report, a, b = measure(turtle, rsi_rule, candles)

        if not b.trades:
            fires, rsi_min = oversold_bounce_count(
                candles, rsi_rule.rsi_period, rsi_rule.oversold
            )
            gate_notes.append(
                f"{product_id}: RSI({rsi_rule.rsi_period}) min {rsi_min:.2f} over "
                f"{len(candles)} daily bars; gate 1 (prev<{rsi_rule.oversold:g} and rising) "
                f"fires {fires}x"
            )

        print(
            f"{product_id:10} {len(candles):5d} {len(a.trades):6d} {len(b.trades):6d} "
            f"{report.a_active:7d} {report.b_active:7d} {report.both_active:5d} "
            f"{_fmt(report.jaccard):>8} {_fmt(report.position_correlation):>7} "
            f"{_fmt(report.pnl_correlation):>7} {_fmt(closed_report.pnl_correlation):>7} "
            f"{_fmt(report.median_entry_distance):>5}"
        )

        # A pair where either arm never traded has no relationship to measure; `_pearson`
        # returns 0 for a constant series by design, and averaging that 0 in would understate
        # correlation. Excluded from the means and named below — never silently dropped.
        if not a.trades or not b.trades:
            degenerate.append(
                f"{product_id}: arm A {len(a.trades)} trades, arm B {len(b.trades)} "
                "— excluded from means (no relationship to measure)"
            )
            continue

        jaccards.append(report.jaccard)
        position_corrs.append(report.position_correlation)
        pnl_corrs_mtm.append(report.pnl_correlation)
        pnl_corrs_closed.append(closed_report.pnl_correlation)

    if not pnl_corrs_mtm:
        print("\nNo asset produced trades on both arms — nothing to average.")
    else:
        n = len(pnl_corrs_mtm)
        print(f"\nMEAN across {n} asset(s) with trades on both arms:")
        print(f"  Jaccard overlap        {_fmt(sum(jaccards, Decimal(0)) / n)}"
              f"   (cross-horizon: {CROSS_HORIZON_JACCARD})")
        print(f"  position correlation   {_fmt(sum(position_corrs, Decimal(0)) / n)}"
              f"   (cross-horizon: {CROSS_HORIZON_POSITION_CORR})")
        print(f"  P&L correlation (mtm)  {_fmt(sum(pnl_corrs_mtm, Decimal(0)) / n)}"
              f"   (cross-horizon: {CROSS_HORIZON_PNL_CORR})")
        print(f"  P&L correlation (clsd) {_fmt(sum(pnl_corrs_closed, Decimal(0)) / n)}")

    if degenerate:
        print("\nExcluded / degenerate:")
        for note in degenerate:
            print(f"  - {note}")

    if gate_notes:
        print("\nWhy arm B is empty (gate 1 of rsi_meanrev.detect, reproduced):")
        for note in gate_notes:
            print(f"  - {note}")

    calibrate(args.db)
    cross_asset(args.db)
    if args.candidates:
        marginal_independence(args.db, [c.strip() for c in args.candidates.split(",") if c.strip()])

    print(
        "\nReminder: low correlation here is NOT evidence for rsi_meanrev. §73.5 makes"
        "\nindependence necessary, never sufficient, and §74.3 already closed the family."
    )


def calibrate(db_path: str) -> None:
    """Re-measure a cross-HORIZON pair whose answer is already published in-repo.

    This is the shakedown's actual validation. A between-family figure means nothing if the
    pipeline behind it cannot reproduce `2026-07-20-horizon-independence.md`'s numbers for the
    shipped 40/20 against 80/40. Running it also settles which P&L convention that experiment
    used, which its write-up does not state.
    """
    entry_a, exit_a, entry_b, exit_b = CALIBRATION_LADDER
    print(
        f"\nCALIBRATION — cross-horizon {entry_a}/{exit_a} vs {entry_b}/{exit_b}, "
        "against 2026-07-20-horizon-independence.md"
    )
    print(
        f"{'asset':10} {'A trd':>6} {'B trd':>6} {'jaccard':>8} {'pos r':>7} "
        f"{'pnl r':>7} {'pnl r':>7} {'gap':>5} {'published':>10}"
    )
    print(f"{'':10} {'':6} {'':6} {'':8} {'':7} {'(mtm)':>7} {'(clsd)':>7} {'(d)':>5} {'':10}")

    for product_id in CALIBRATION_ASSETS:
        candles = load_candles(db_path, product_id, GRANULARITY)
        if not candles:
            continue
        slow = dict(TURTLE_PARAMS, entry_lookback=entry_b, exit_lookback=exit_b)
        report, closed_report, a, b = measure(
            TurtleBreakout(product_id=product_id, **TURTLE_PARAMS),
            TurtleBreakout(product_id=product_id, **slow),
            candles,
        )
        published = CALIBRATION_EXPECTED.get(product_id)
        print(
            f"{product_id:10} {len(a.trades):6d} {len(b.trades):6d} "
            f"{_fmt(report.jaccard):>8} {_fmt(report.position_correlation):>7} "
            f"{_fmt(report.pnl_correlation):>7} {_fmt(closed_report.pnl_correlation):>7} "
            f"{_fmt(report.median_entry_distance):>5} "
            f"{(_fmt(published) if published else 'n/a'):>10}"
        )


def turtle_series(
    db_path: str, product_id: str
) -> dict[int, tuple[int, Decimal, Decimal]] | None:
    """The shipped turtle's ts-keyed series for one product, or None if no candles are cached."""
    candles = load_candles(db_path, product_id, GRANULARITY)
    if not candles:
        return None
    rule = TurtleBreakout(product_id=product_id, **TURTLE_PARAMS)
    result = backtest(rule, candles, fee_pct=FEE_PCT, slippage_pct=SLIPPAGE_PCT)
    return by_timestamp(result.trades, candles)


def marginal_independence(db_path: str, candidates: list[str]) -> None:
    """Rank allowlist CANDIDATES by the independent evidence each would add to the live book.

    Five scout runs have ranked candidates on liquidity, history and shariah plausibility. None
    measured independence — and `2026-07-20-minbtl-sizing.md`'s expansion case is specifically an
    argument about INDEPENDENT evidence. Under §73.5 a sixth asset correlated with the five
    already held buys deployment, not knowability: it spends attestation effort and trades, and
    leaves the evidence horizon where it was.

    For each candidate this measures the shipped turtle on that asset against the shipped turtle
    on each of the five live assets, on common timestamps, and reports the mean. Lower is better.

    ⛔ PROPOSER, NEVER DECIDER. This admits nothing, attests nothing and writes to no keel DB or
    config. Independence is one axis among several — it says nothing about whether an asset is
    halal (attestation), liquid enough, or has edge (backtest). A low number here is not a
    licence to add an asset.
    """
    print("\nMARGINAL INDEPENDENCE — candidate vs the live allowlist (shipped turtle both sides)")

    live = {p: s for p in ASSETS if (s := turtle_series(db_path, p)) is not None}
    if not live:
        print("  no live-asset candles cached; skipped")
        return

    header = (
        f"{'candidate':11} {'bars':>5} {'vs':>3} {'jaccard':>8} {'pos r':>7} "
        f"{'pnl r':>7} {'worst pair':>22}"
    )
    print(header)
    print(f"{'':11} {'':5} {'':3} {'(mean)':>8} {'(mean)':>7} {'(mean,clsd)':>7} {'':22}")
    print("-" * len(header))

    for candidate in candidates:
        series = turtle_series(db_path, candidate)
        if series is None:
            print(f"{candidate.removesuffix('-USD'):11} {'—':>5}   no local candles — run "
                  f"`keel fetch --products {candidate}` first")
            continue

        jaccards: list[Decimal] = []
        position_corrs: list[Decimal] = []
        pnl_corrs: list[Decimal] = []
        worst = (Decimal(-2), "")
        bars = 0
        for name, live_series in live.items():
            a_pos, _a_mtm, a_closed, b_pos, _b_mtm, b_closed = align(series, live_series)
            report = compare(a_pos, b_pos, a_closed, b_closed)
            bars = max(bars, report.n_periods)
            jaccards.append(report.jaccard)
            position_corrs.append(report.position_correlation)
            pnl_corrs.append(report.pnl_correlation)
            if report.position_correlation > worst[0]:
                worst = (report.position_correlation, name.removesuffix("-USD"))

        n = len(live)
        print(
            f"{candidate.removesuffix('-USD'):11} {bars:5d} {n:3d} "
            f"{_fmt(sum(jaccards, Decimal(0)) / n):>8} "
            f"{_fmt(sum(position_corrs, Decimal(0)) / n):>7} "
            f"{_fmt(sum(pnl_corrs, Decimal(0)) / n):>7} "
            f"{(worst[1] + ' ' + _fmt(worst[0])):>22}"
        )


def cross_asset(db_path: str) -> None:
    """CROSS-ASSET: the shipped turtle against ITSELF on a different asset.

    Same family, same parameters, same bar clock — the only thing varying is the underlying.
    This is the measurement that tests whether keel's five live rules are five independent
    confirmations of one hypothesis or one hypothesis counted five times. §73.5's arithmetic
    applies unchanged: correlated streams inflate apparent trade count without adding evidence.

    Series are aligned on COMMON timestamps (see `align`), so PAXG-USD's short window shortens
    only the pairs it participates in.
    """
    print("\nCROSS-ASSET — shipped turtle 40/20 vs ITSELF on another asset (same params)")

    series: dict[str, dict[int, tuple[int, Decimal, Decimal]]] = {}
    for product_id in ASSETS:
        candles = load_candles(db_path, product_id, GRANULARITY)
        if not candles:
            continue
        rule = TurtleBreakout(product_id=product_id, **TURTLE_PARAMS)
        result = backtest(rule, candles, fee_pct=FEE_PCT, slippage_pct=SLIPPAGE_PCT)
        series[product_id] = by_timestamp(result.trades, candles)

    header = (
        f"{'pair':22} {'days':>6} {'jaccard':>8} {'pos r':>7} {'pnl r':>7} {'pnl r':>7} {'gap':>5}"
    )
    print(header)
    print(f"{'':22} {'':6} {'':8} {'':7} {'(mtm)':>7} {'(clsd)':>7} {'(d)':>5}")
    print("-" * len(header))

    names = [p for p in ASSETS if p in series]
    jaccards: list[Decimal] = []
    position_corrs: list[Decimal] = []
    pnl_mtm_corrs: list[Decimal] = []
    pnl_closed_corrs: list[Decimal] = []

    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            a_pos, a_mtm, a_closed, b_pos, b_mtm, b_closed = align(
                series[first], series[second]
            )
            mtm_report = compare(a_pos, b_pos, a_mtm, b_mtm)
            closed_report = compare(a_pos, b_pos, a_closed, b_closed)
            label = f"{first.removesuffix('-USD')} vs {second.removesuffix('-USD')}"
            print(
                f"{label:22} {mtm_report.n_periods:6d} {_fmt(mtm_report.jaccard):>8} "
                f"{_fmt(mtm_report.position_correlation):>7} "
                f"{_fmt(mtm_report.pnl_correlation):>7} "
                f"{_fmt(closed_report.pnl_correlation):>7} "
                f"{_fmt(mtm_report.median_entry_distance):>5}"
            )
            jaccards.append(mtm_report.jaccard)
            position_corrs.append(mtm_report.position_correlation)
            pnl_mtm_corrs.append(mtm_report.pnl_correlation)
            pnl_closed_corrs.append(closed_report.pnl_correlation)

    if not pnl_mtm_corrs:
        return

    pairs = len(pnl_mtm_corrs)
    mean_position = sum(position_corrs, Decimal(0)) / pairs
    mean_mtm = sum(pnl_mtm_corrs, Decimal(0)) / pairs
    mean_closed = sum(pnl_closed_corrs, Decimal(0)) / pairs
    print(f"\nMEAN across {pairs} pairs:")
    print(f"  Jaccard overlap        {_fmt(sum(jaccards, Decimal(0)) / pairs)}"
          f"   (cross-horizon: {CROSS_HORIZON_JACCARD})")
    print(f"  position correlation   {_fmt(mean_position)}"
          f"   (cross-horizon: {CROSS_HORIZON_POSITION_CORR})")
    print(f"  P&L correlation (mtm)  {_fmt(mean_mtm)}")
    print(f"  P&L correlation (clsd) {_fmt(mean_closed)}"
          f"   (cross-horizon: {CROSS_HORIZON_PNL_CORR})")
    print(
        f"\n  effective independent streams from {len(names)} assets, "
        f"n/(1+(n-1)ρ̄) on the position-correlation mean: "
        f"{_fmt(effective_breadth(len(names), mean_position), '0.01')}"
    )


if __name__ == "__main__":
    main()
