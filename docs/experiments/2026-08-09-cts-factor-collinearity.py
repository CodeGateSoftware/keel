#!/usr/bin/env python
"""Are keel's 11 CTS confluence factors collinear? -- issue #208, QuantCrawler teardown §1.5.

Every empirical claim in `docs/experiments/2026-08-09-cts-factor-collinearity.md` comes from this
script. Strictly read-only: it opens the candle cache with `mode=ro`, drives no broker, writes
nothing but stdout, and changes no weight, gate or rule. The measurement machinery lives in
`keel/research/cts_factors.py` (importable, unit-tested); this file is only the pre-declared
configuration and the report.

THE CRITIQUE UNDER TEST. The teardown's stated design rule for multi-indicator scoring is one
indicator per category with 3-of-4 agreement, plus a warning that stacking same-category
indicators manufactures false confirmation. ⚠️ **Their 3-of-4 ratio is unvalidated SEO-adjacent
content and is used here for exactly nothing** -- no threshold below is theirs, and no result is
compared against it. What transfers is the mechanism: an ADDITIVE score is only meaningful if its
terms are close to independent, and keel's CTS awards 4 of 14 raw points to three momentum reads
and another 4 to two trend reads. Whether that is a real problem is an empirical question about
keel's own code, and it had never been asked.

PRE-DECLARED BEFORE THE RUN (§78.5), so nothing below is a cluster found by staring at a matrix:

    clusters      `cts_factors.SUSPECTED_CLUSTERS`, taken verbatim from issue #208
    assets        the live allowlist (`config.live-sandbox.yaml`)
    primary arm   UNCONDITIONAL, ONE_DAY, expanding window
    alpha         0.05 family-wise, Holm-Bonferroni over the pairs actually tested

FOUR ARMS:

    1  UNCONDITIONAL / ONE_DAY / expanding window  -- the headline. Expanding from the first
       cached bar is not a convenience: it is exactly what the live path does
       (`agent.run_once` -> `repo.get_candles(product, gran)` with no bounds -> `engine.evaluate`),
       and `levels.find_levels` / `regime.detect_phase` both read the whole list they are given.
    2  UNCONDITIONAL / ONE_HOUR / rolling window   -- large-N robustness, ~25x arm 1's sample.
       Expanding is O(n^2) and infeasible over 44k hourly bars, so the window is fixed and
       arm 3 checks what that costs.
    3  WINDOW SENSITIVITY                          -- arm 2's structure at three window lengths.
    4  CONDITIONAL / fired signals                 -- the shipped turtle through the real
       `engine.evaluate`, gates and all. Reported for comparison and explicitly NOT the headline:
       detection and the gates are functions of the same regime state several factors read, so
       conditioning on them is a collider and moves the correlations by an unknown amount.

    .venv/bin/python docs/experiments/2026-08-09-cts-factor-collinearity.py
    .venv/bin/python docs/experiments/2026-08-09-cts-factor-collinearity.py --db path/to.db
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from decimal import Decimal

from keel.research.cts_factors import (
    FACTOR_NAMES,
    SUSPECTED_CLUSTERS,
    FactorSample,
    PairStat,
    cluster_report,
    holm_adjust,
    pair_stats,
    pool,
    replay_every_bar,
    replay_fired,
    variance_report,
)
from keel.strategy.indicators_cts import DEFAULT_WEIGHTS
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Candle, Granularity

# -- PRE-DECLARED CONFIGURATION -------------------------------------------------------------------

DEFAULT_DB = "/Users/elmehdiaitbrahim/keel/keel.db"

#: The live allowlist (`config.live-sandbox.yaml`), matching every prior experiment in this
#: directory so the sample is comparable across write-ups.
ASSETS = ("BTC-USD", "ETH-USD", "PAXG-USD", "ADA-USD", "XLM-USD")

PRIMARY_GRANULARITY = Granularity.ONE_DAY
HOURLY_GRANULARITY = Granularity.ONE_HOUR

#: Arm 2's fixed window, and arm 3's ladder around it.
HOURLY_WINDOW = 500
SENSITIVITY_WINDOWS = (250, 500, 1000)
SENSITIVITY_STEP = 20

ALPHA = 0.05

#: Arm 4's rule: the SHIPPED turtle, byte-identical to `keel-live.db` rules 1-5 and to
#: `2026-08-08-between-family-independence.py`'s arm A.
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


def load_candles(db_path: str, product_id: str, granularity: Granularity) -> list[Candle]:
    """Ascending candles for one product/granularity, read-only.

    Same shape as `2026-08-08-between-family-independence.py`'s loader, and deliberately stdlib
    `sqlite3` rather than `Repository`: an experiment must not be able to write to the cache it
    is reading, and `mode=ro` is the cheapest way to make that structural.
    """
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


# -- report -----------------------------------------------------------------------------------


def _fmt(value: Decimal | float, places: str = "0.001") -> str:
    return str(Decimal(str(value)).quantize(Decimal(places)))


def print_base_rates(title: str, samples: dict[str, FactorSample], pooled: FactorSample) -> None:
    print(f"\n{title} -- base rate P(factor present)")
    header = f"{'factor':24} {'wt':>3} {'pooled':>8} " + " ".join(
        f"{a.removesuffix('-USD'):>7}" for a in samples
    )
    print(header)
    print("-" * len(header))
    for name in FACTOR_NAMES:
        row = f"{name:24} {DEFAULT_WEIGHTS[name]:3d} {_fmt(pooled.base_rate(name)):>8} "
        row += " ".join(f"{_fmt(s.base_rate(name)):>7}" for s in samples.values())
        print(row)
    print(f"{'N (observations)':24} {'':3} {pooled.n:8d} " + " ".join(
        f"{s.n:7d}" for s in samples.values()
    ))


def print_pairs(title: str, stats: list[PairStat], limit: int | None = None) -> None:
    print(f"\n{title} -- pairwise co-occurrence, |phi| descending")
    header = (
        f"{'factor A':24} {'factor B':24} {'phi':>7} {'jaccard':>8} {'lift':>6} "
        f"{'p (Holm)':>10} {'FWER':>5}"
    )
    print(header)
    print("-" * len(header))
    shown = stats if limit is None else stats[:limit]
    for stat in shown:
        print(
            f"{stat.a:24} {stat.b:24} {_fmt(stat.phi):>7} {_fmt(stat.jaccard):>8} "
            f"{_fmt(stat.lift, '0.01'):>6} {stat.p_holm:10.2e} "
            f"{('sig' if stat.significant else '-'):>5}"
        )
    if limit is not None and len(stats) > limit:
        print(f"  ... {len(stats) - limit} further pairs, all |phi| <= {_fmt(shown[-1].phi)}")


def print_clusters(title: str, sample: FactorSample, stats: list[PairStat]) -> None:
    print(f"\n{title} -- do the pre-declared clusters cluster?")
    header = (
        f"{'cluster':10} {'members':>8} {'pairs':>6} {'mean phi':>9} {'max phi':>9} "
        f"{'mean phi':>9} {'mean J':>7} {'raw pts':>8}"
    )
    print(header)
    print(f"{'':10} {'':8} {'':6} {'(within)':>9} {'(within)':>9} {'(other)':>9} {'(within)':>7} "
          f"{'(share)':>8}")
    print("-" * len(header))
    for report in cluster_report(sample, stats):
        print(
            f"{report.name:10} {len(report.members):8d} {report.within_pairs:6d} "
            f"{_fmt(report.mean_within_phi):>9} {_fmt(report.max_within_phi):>9} "
            f"{_fmt(report.mean_other_phi):>9} {_fmt(report.mean_within_jaccard):>7} "
            f"{_fmt(report.weight_share):>8}"
        )


def print_variance(title: str, sample: FactorSample) -> None:
    report = variance_report(sample)
    print(f"\n{title} -- variance of the CTS total, observed vs. under independence")
    print(f"  mean CTS total                       {_fmt(report.mean_total, '0.01')}")
    print(f"  Var(total), observed                 {_fmt(report.observed, '0.01')}")
    print(f"  Var(total), independent same-marginals {_fmt(report.independent, '0.01')}")
    print(f"  inflation ratio                      {_fmt(report.ratio, '0.001')}")


def analyse(title: str, samples: dict[str, FactorSample], pair_limit: int | None = None) -> None:
    """Print every table for one arm: base rates, pairs, clusters, variance."""
    pooled = pool(samples.values())
    stats = holm_adjust(pair_stats(pooled), alpha=ALPHA)
    varying = pooled.varying()
    print(f"\n{'=' * 96}\n{title}\n{'=' * 96}")
    print(
        f"factors with variance: {len(varying)} of {len(FACTOR_NAMES)} "
        f"-> {len(stats)} pairwise tests in the family; Holm-Bonferroni at alpha={ALPHA}"
    )
    constant = [n for n in FACTOR_NAMES if n not in varying]
    if constant:
        print(
            "constant in this sample (untestable, excluded from the family): "
            + ", ".join(constant)
        )
    print_base_rates(title, samples, pooled)
    print_pairs(title, stats, limit=pair_limit)
    print_clusters(title, pooled, stats)
    print_variance(title, pooled)


def arm1(db_path: str) -> None:
    print("\nARM 1 -- UNCONDITIONAL, ONE_DAY, expanding window (the live path's own window)")
    samples: dict[str, FactorSample] = {}
    for product_id in ASSETS:
        candles = load_candles(db_path, product_id, PRIMARY_GRANULARITY)
        if not candles:
            print(f"  {product_id}: no {PRIMARY_GRANULARITY.value} candles cached -- skipped")
            continue
        samples[product_id] = replay_every_bar(product_id, candles, window=None)
    if samples:
        analyse("ARM 1: unconditional, ONE_DAY, expanding window", samples)
        _per_asset_clusters(samples)


def _per_asset_clusters(samples: dict[str, FactorSample]) -> None:
    """The pooled matrix assumes the relationship is the same on every asset. Check it."""
    print("\nPER-ASSET within-cluster mean phi (pooling is only honest if these agree)")
    header = f"{'cluster':10} " + " ".join(f"{a.removesuffix('-USD'):>9}" for a in samples)
    print(header)
    print("-" * len(header))
    per_asset = {
        name: holm_adjust(pair_stats(sample), alpha=ALPHA) for name, sample in samples.items()
    }
    for cluster in SUSPECTED_CLUSTERS:
        cells = []
        for product_id, stats in per_asset.items():
            reports = {r.name: r for r in cluster_report(samples[product_id], stats)}
            report = reports.get(cluster)
            cells.append(_fmt(report.mean_within_phi) if report else "n/a")
        print(f"{cluster:10} " + " ".join(f"{c:>9}" for c in cells))


def arm2(db_path: str) -> None:
    print(f"\nARM 2 -- UNCONDITIONAL, ONE_HOUR, rolling window={HOURLY_WINDOW}")
    samples: dict[str, FactorSample] = {}
    for product_id in ASSETS:
        candles = load_candles(db_path, product_id, HOURLY_GRANULARITY)
        if not candles:
            continue
        samples[product_id] = replay_every_bar(product_id, candles, window=HOURLY_WINDOW)
    if samples:
        analyse(
            f"ARM 2: unconditional, ONE_HOUR, rolling window={HOURLY_WINDOW}",
            samples,
            pair_limit=12,
        )


def arm3(db_path: str) -> None:
    print(f"\n{'=' * 96}")
    print("ARM 3 -- window sensitivity (BTC-USD ONE_HOUR, every "
          f"{SENSITIVITY_STEP}th bar)")
    print(f"{'=' * 96}")
    candles = load_candles(db_path, "BTC-USD", HOURLY_GRANULARITY)
    if not candles:
        return
    header = (
        f"{'window':>7} {'N':>7} {'momentum':>10} {'trend':>10} {'var ratio':>10} "
        f"{'P(pullback)':>12} {'strongest pair':>44}"
    )
    print(header)
    print(f"{'(bars)':>7} {'':7} {'(mean phi)':>10} {'(mean phi)':>10} {'':10} {'':12} "
          f"{'(|phi|, any of the 45)':>44}")
    print("-" * len(header))
    for window in SENSITIVITY_WINDOWS:
        sample = replay_every_bar("BTC-USD", candles, window=window, step=SENSITIVITY_STEP)
        stats = holm_adjust(pair_stats(sample), alpha=ALPHA)
        reports = {r.name: r for r in cluster_report(sample, stats)}
        top = stats[0]
        print(
            f"{window:7d} {sample.n:7d} "
            f"{_fmt(reports['momentum'].mean_within_phi) if 'momentum' in reports else 'n/a':>10} "
            f"{_fmt(reports['trend'].mean_within_phi) if 'trend' in reports else 'n/a':>10} "
            f"{_fmt(variance_report(sample).ratio):>10} "
            f"{_fmt(sample.base_rate('in_pullback')):>12} "
            f"{(top.a + ' x ' + top.b + ' ' + _fmt(top.phi)):>44}"
        )
    print(
        "\n  The three cluster columns are IDENTICAL by construction, not luck: every factor\n"
        "  either cluster reads a bounded lookback (`regime.detect_condition` 20 bars, RSI 14,\n"
        "  the EMA fan's longest period 200), so past the 200-bar warm-up the window cannot reach\n"
        "  them. `P(pullback)` is the control that proves the window is being applied at all --\n"
        "  `regime.detect_phase` compares against `candles[0]`, so it moves with window length,\n"
        "  and `levels.find_levels` scans every bar it is handed."
    )


def arm4(db_path: str) -> None:
    print(f"\n{'=' * 96}")
    print("ARM 4 -- CONDITIONAL: fired signals only (shipped turtle through engine.evaluate)")
    print("⚠️  Conditioned on rule detection AND the choppy / higher-TF / kill-zone gates, which")
    print("    read the same regime state several factors read. A collider. NOT the headline.")
    print(f"{'=' * 96}")
    samples: dict[str, FactorSample] = {}
    for product_id in ASSETS:
        candles = load_candles(db_path, product_id, PRIMARY_GRANULARITY)
        if not candles:
            continue
        rule = TurtleBreakout(product_id=product_id, **TURTLE_PARAMS)  # type: ignore[arg-type]
        sample = replay_fired(rule, PRIMARY_GRANULARITY, candles, window=None)
        print(f"  {product_id}: {sample.n} gate-cleared signals over {len(candles)} bars")
        if sample.n:
            samples[product_id] = sample
    if not samples:
        print("  no fired signals on any asset -- nothing to correlate")
        return
    analyse("ARM 4: conditional on a fired, gate-cleared signal", samples, pair_limit=12)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Candle cache (default: {DEFAULT_DB})")
    parser.add_argument("--arms", default="1,2,3,4", help="Comma-separated arm numbers to run")
    args = parser.parse_args()

    # `engine.evaluate` emits an INFO telemetry event per non-firing rule per bar; arm 4 drives it
    # ~9,000 times. Silencing INFO keeps the report readable and costs nothing measured.
    logging.disable(logging.INFO)

    print("CTS factor collinearity -- issue #208 (QuantCrawler teardown §1.5)")
    print(f"db={args.db}  assets={','.join(ASSETS)}")
    print("⚠️  RESEARCH ONLY. No weight, factor, gate or rule is changed by this run.")
    print("⚠️  The teardown's '3-of-4 agreement' ratio is unvalidated and is used for nothing")
    print("    here; only its mechanism (additivity presumes independence) is under test.")

    selected = {arm.strip() for arm in args.arms.split(",")}
    for name, fn in (("1", arm1), ("2", arm2), ("3", arm3), ("4", arm4)):
        if name in selected:
            fn(args.db)


if __name__ == "__main__":
    main()
