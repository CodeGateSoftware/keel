#!/usr/bin/env python
"""What does fixing `levels.is_round_number` do to live CTS scoring? -- issue #225.

Every empirical claim in `docs/experiments/2026-08-11-round-number-scale.md` comes from this
script. Strictly read-only: it opens the candle cache with `mode=ro`, drives no broker, writes
nothing but stdout, and changes no weight, gate or rule. The replay machinery is
`keel/research/cts_factors.py`, built for #208/#224 and REUSED here rather than reimplemented --
which is the point of having put it in the package.

WHAT IS BEING MEASURED. `round_number_proximity` is weight 1 of `DEFAULT_WEIGHTS`' 14. Before
this change `levels.is_round_number` compared price against an ABSOLUTE `step=Decimal("0.005")`,
and every 2dp-quoted price is an exact multiple of half a cent, so the check returned `True`
unconditionally on BTC-USD, ETH-USD and PAXG-USD. Fixing it removes a constant +1 from those
three assets wherever the factor does not legitimately apply. That is a change to live scoring,
so it gets a before/after on the same bars rather than an assertion.

HOW BEFORE AND AFTER ARE PUT ON THE SAME SAMPLE. `engine.assemble_cts_context` is called ONCE
per bar, under the shipped (fixed) function -- that is the AFTER sample. The BEFORE sample is
then reconstructed exactly rather than replayed a second time, because it can be:
`is_round_number` has exactly one caller in the package (`engine.assemble_cts_context`, line
288, verified by grep), it is a pure function of `setup.entry` alone, and `setup.entry` is the
bar's close. So the only cell of the context that moves is `round_number_proximity`, and the
only thing that moves in the total is its weight-1 contribution.

⚠️ That reconstruction is an argument, not a measurement, so ARM E does not take it on trust.
It re-runs the FULL replay on every asset with `levels.is_round_number` monkeypatched back to
the pre-#225 body, and asserts the replayed before-vectors and before-totals match the
reconstructed ones bar for bar. If the reconstruction were wrong -- if some other factor read
the round-number flag indirectly -- arm E is what would catch it. Run it; it is not optional
decoration, and the write-up quotes its result.

    .venv/bin/python docs/experiments/2026-08-11-round-number-scale.py
    .venv/bin/python docs/experiments/2026-08-11-round-number-scale.py --db path/to.db
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import statistics
from collections.abc import Callable
from decimal import Decimal

from keel.analysis import levels
from keel.research.cts_factors import FACTOR_NAMES, FactorSample, pool, replay_every_bar
from keel.strategy import indicators_cts
from keel.strategy.indicators_cts import DEFAULT_WEIGHTS, entry_technique
from keel.types import Candle, Granularity

# -- PRE-DECLARED CONFIGURATION -------------------------------------------------------------

DEFAULT_DB = "/Users/elmehdiaitbrahim/keel/keel.db"

#: The live allowlist (`config.live-sandbox.yaml`), matching #224 exactly so the BEFORE column
#: here is directly comparable to the base-rate table published there.
ASSETS = ("BTC-USD", "ETH-USD", "PAXG-USD", "ADA-USD", "XLM-USD")

PRIMARY_GRANULARITY = Granularity.ONE_DAY

#: The factor under repair, and its weight. Read from the shipped table, never hardcoded.
FACTOR = "round_number_proximity"
FACTOR_WEIGHT = DEFAULT_WEIGHTS[FACTOR]

#: `indicators_cts.entry_technique`'s band edges. These are THE ONLY thresholds in the package
#: that a CTS total is compared against -- `keel/strategy/engine.py:144` is the sole call site,
#: it passes no override, and no config key or promotion gate reads a CTS score at all
#: (`promotion.can_promote` runs off backtested trade statistics). Named here so the
#: threshold-impact arm cannot drift from the shipped defaults.
CTS_LOW, CTS_HIGH = 5, 8

#: Tolerance ladder for arm D. `0.02` is the shipped default; `0.10` is what the pre-#225
#: docstring claimed ("within 10% of the step size") and is included so the cost of the one
#: genuinely free parameter in the fix is visible rather than asserted.
TOLERANCE_LADDER = (Decimal("0.10"), Decimal("0.05"), Decimal("0.02"), Decimal("0.01"))


def legacy_is_round_number(price: Decimal, step: Decimal = Decimal("0.005")) -> bool:
    """`levels.is_round_number` exactly as it stood before #225, kept here verbatim.

    It lives in the experiment rather than the package because it is wrong: its only remaining
    job is to be the BEFORE arm of a comparison. Copied byte for byte from `levels.py` at
    commit f7318fd so the before-column is the real historical behaviour and not a paraphrase.
    """
    remainder = price % step
    distance = min(remainder, step - remainder)
    return distance <= step * Decimal("0.1")


def load_candles(db_path: str, product_id: str, granularity: Granularity) -> list[Candle]:
    """Ascending candles for one product/granularity, read-only.

    Byte-identical to `2026-08-09-cts-factor-collinearity.py`'s loader, deliberately: an
    experiment must not be able to write to the cache it reads, and `mode=ro` makes that
    structural rather than a promise.
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


# -- before/after construction ---------------------------------------------------------------


def replayed_prices(candles: list[Candle], warmup: int) -> list[Decimal]:
    """The `setup.entry` prices `replay_every_bar` scored, in the same order.

    `cts_factors._synthetic_setup` prices the setup at the bar's close and
    `replay_every_bar` walks `range(warmup, len(candles))`, so this reproduces the entry
    sequence without re-entering the replay. Kept as one function so the coupling to the
    harness's iteration order is stated in one place rather than assumed in three.
    """
    return [candle.close for candle in candles[warmup:]]


def before_from_after(sample: FactorSample, prices: list[Decimal]) -> FactorSample:
    """The pre-#225 sample, reconstructed from the post-#225 one on the same bars.

    Only `round_number_proximity` moves, so the before-vector is `legacy_is_round_number` over
    the same entry prices and the before-total is the after-total with this factor's weight
    subtracted where it is now present and added where it used to be. Arm E verifies this
    against a real replay.
    """
    if len(prices) != sample.n:
        raise AssertionError(f"price/sample length mismatch: {len(prices)} vs {sample.n}")

    legacy = [1 if legacy_is_round_number(price) else 0 for price in prices]
    current = sample.vectors[FACTOR]
    vectors = {name: list(vec) for name, vec in sample.vectors.items()}
    vectors[FACTOR] = legacy
    totals = [
        total + FACTOR_WEIGHT * (was - now)
        for total, was, now in zip(sample.totals, legacy, current)
    ]
    return FactorSample(vectors=vectors, totals=totals, labels=list(sample.labels))


def replay_with(
    predicate: Callable[[Decimal], bool],
    product_id: str,
    candles: list[Candle],
) -> FactorSample:
    """Replay every bar with `levels.is_round_number` temporarily swapped for `predicate`.

    Monkeypatching a shipped module is not something to do lightly, and it is done here for one
    reason: arm E has to drive the REAL `engine.assemble_cts_context` under the old predicate to
    prove the arithmetic reconstruction above is exact. Patching the definition is the only way
    to do that without a second checkout. The original is restored in a `finally`, and nothing
    downstream of this process is affected -- the script writes no state.
    """
    original = levels.is_round_number
    levels.is_round_number = predicate  # type: ignore[assignment]
    try:
        return replay_every_bar(product_id, candles, window=None)
    finally:
        levels.is_round_number = original  # type: ignore[assignment]


# -- report ----------------------------------------------------------------------------------


def _fmt(value: Decimal | float, places: str = "0.0001") -> str:
    return str(Decimal(str(value)).quantize(Decimal(places)))


def _rate(vector: list[int]) -> Decimal:
    return Decimal(sum(vector)) / Decimal(len(vector)) if vector else Decimal(0)


def print_presence(
    before: dict[str, FactorSample],
    after: dict[str, FactorSample],
) -> None:
    """ACCEPTANCE TABLE: P(round_number_proximity present), before vs after, per asset."""
    print("\nARM A -- P(round_number_proximity present), same bars, before vs after")
    header = f"{'asset':10} {'N':>6} {'before':>9} {'after':>9} {'delta':>9}"
    print(header)
    print("-" * len(header))
    for product_id in after:
        was = _rate(before[product_id].vectors[FACTOR])
        now = _rate(after[product_id].vectors[FACTOR])
        print(
            f"{product_id:10} {after[product_id].n:6d} {_fmt(was):>9} {_fmt(now):>9} "
            f"{_fmt(now - was):>9}"
        )
    pooled_before, pooled_after = pool(before.values()), pool(after.values())
    was, now = _rate(pooled_before.vectors[FACTOR]), _rate(pooled_after.vectors[FACTOR])
    print("-" * len(header))
    print(f"{'POOLED':10} {pooled_after.n:6d} {_fmt(was):>9} {_fmt(now):>9} {_fmt(now - was):>9}")

    rates = [_rate(s.vectors[FACTOR]) for s in after.values()]
    old_rates = [_rate(s.vectors[FACTOR]) for s in before.values()]
    print(
        f"\n  cross-asset spread (max/min):  before {_fmt(max(old_rates) / min(old_rates), '0.01')}x"
        f"   after {_fmt(max(rates) / min(rates), '0.01')}x"
    )
    print(
        "  The acceptance test in #225 is that this factor means the same thing at 65,000 as at\n"
        "  0.38. The spread column is that claim as a number."
    )


def print_all_factor_rates(after: dict[str, FactorSample]) -> None:
    """Where the repaired factor's base rate now sits among the other ten."""
    pooled = pool(after.values())
    print("\nARM A2 -- the repaired factor against the rest of the CTS panel (pooled, AFTER)")
    header = f"{'factor':24} {'wt':>3} {'P(present)':>11}"
    print(header)
    print("-" * len(header))
    for name in sorted(FACTOR_NAMES, key=lambda n: -float(_rate(pooled.vectors[n]))):
        mark = "  <-- repaired" if name == FACTOR else ""
        print(f"{name:24} {DEFAULT_WEIGHTS[name]:3d} {_fmt(_rate(pooled.vectors[name])):>11}{mark}")


def _describe(totals: list[int]) -> str:
    return (
        f"{statistics.mean(totals):>8.3f} {statistics.median(totals):>8.1f} "
        f"{statistics.pstdev(totals):>8.3f} {min(totals):>5d} {max(totals):>5d}"
    )


def print_distribution(
    before: dict[str, FactorSample],
    after: dict[str, FactorSample],
) -> None:
    """CTS TOTAL distribution shift. Fixing the factor removes a constant +1 from 3 of 5 assets."""
    print("\nARM B -- CTS total distribution, before vs after")
    header = (
        f"{'asset':10} {'arm':>7} {'mean':>8} {'median':>8} {'sd':>8} {'min':>5} {'max':>5} "
        f"{'d(mean)':>9}"
    )
    print(header)
    print("-" * len(header))
    for product_id in after:
        was, now = before[product_id].totals, after[product_id].totals
        delta = statistics.mean(now) - statistics.mean(was)
        print(f"{product_id:10} {'before':>7} {_describe(was)} {'':>9}")
        print(f"{'':10} {'after':>7} {_describe(now)} {delta:>9.3f}")
    pooled_before, pooled_after = pool(before.values()), pool(after.values())
    print("-" * len(header))
    print(f"{'POOLED':10} {'before':>7} {_describe(pooled_before.totals)} {'':>9}")
    print(
        f"{'':10} {'after':>7} {_describe(pooled_after.totals)} "
        f"{statistics.mean(pooled_after.totals) - statistics.mean(pooled_before.totals):>9.3f}"
    )


def print_thresholds(
    before: dict[str, FactorSample],
    after: dict[str, FactorSample],
) -> None:
    """THRESHOLD IMPACT -- the arm that matters. How many bars change entry technique?

    `entry_technique(total, low=5, high=8)` is the only threshold comparison a CTS total feeds
    in the whole package. It is a POSTURE selector, not an admission gate: a bar that drops
    below `low` is not rejected, it is entered with `confirm_3bar` (3-bar confirmation, smaller
    size, wider stop) instead of `signal_candle`. Nothing here rejects a setup that previously
    qualified, because there is no CTS floor to fall through.
    """
    print("\nARM C -- threshold impact: entry_technique bands (low=5, high=8), before vs after")
    print(
        "  `engine.py:144` -> `indicators_cts.entry_technique(cts_result.total)` is the SOLE\n"
        "  CTS threshold comparison in the package. It selects posture, not admission: no\n"
        "  setup is rejected for a low CTS, so no previously-qualifying setup can be gated out."
    )
    techniques = ("confirm_3bar", "signal_candle", "aggressive")
    header = (
        f"{'asset':10} {'arm':>7} " + " ".join(f"{t:>14}" for t in techniques) + f" {'moved':>7}"
    )
    print(header)
    print("-" * len(header))
    for product_id in after:
        was = [entry_technique(t, CTS_LOW, CTS_HIGH) for t in before[product_id].totals]
        now = [entry_technique(t, CTS_LOW, CTS_HIGH) for t in after[product_id].totals]
        moved = sum(1 for a, b in zip(was, now) if a != b)
        n = len(now)
        print(f"{product_id:10} {'before':>7} " + " ".join(f"{was.count(t):14d}" for t in techniques))
        print(
            f"{'':10} {'after':>7} " + " ".join(f"{now.count(t):14d}" for t in techniques)
            + f" {moved:7d}"
        )
        print(
            f"{'':10} {'':>7} " + " ".join(
                f"{(Decimal(now.count(t) - was.count(t)) / Decimal(n)):>+13.4f} " for t in techniques
            )
            + f" {Decimal(moved) / Decimal(n):>6.4f}"
        )
    all_was = [entry_technique(t, CTS_LOW, CTS_HIGH) for s in before.values() for t in s.totals]
    all_now = [entry_technique(t, CTS_LOW, CTS_HIGH) for s in after.values() for t in s.totals]
    moved = sum(1 for a, b in zip(all_was, all_now) if a != b)
    print("-" * len(header))
    print(f"{'POOLED':10} {'before':>7} " + " ".join(f"{all_was.count(t):14d}" for t in techniques))
    print(
        f"{'':10} {'after':>7} " + " ".join(f"{all_now.count(t):14d}" for t in techniques)
        + f" {moved:7d}"
    )
    print(
        f"\n  bars whose entry technique changes: {moved} of {len(all_now)} "
        f"({Decimal(moved) / Decimal(len(all_now)):.4f})"
    )
    print("  every move is one step DOWN the ladder (a removed point cannot raise a total).")


def print_tolerance_ladder(candles_by_asset: dict[str, list[Candle]]) -> None:
    """ARM D -- the one free parameter. P(present) over closes at four tolerances.

    Computed directly over closes rather than through the replay: `is_round_number` reads only
    the entry price, so the base rate is a property of the close series alone and does not need
    a context assembly per bar. Arm A's numbers are the ones that came through the real replay;
    these agree with them at `tolerance=0.02` to within the 200-bar warm-up, which is the check.
    """
    print("\nARM D -- tolerance sensitivity (fraction of handle spacing), over daily closes")
    header = f"{'tolerance':>10} " + " ".join(
        f"{a.removesuffix('-USD'):>8}" for a in candles_by_asset
    ) + f" {'pooled':>8} {'spread':>7} {'64975.78':>9}"
    print(header)
    print("-" * len(header))
    for tolerance in TOLERANCE_LADDER:
        cells, rates, hits, total = [], [], 0, 0
        for candles in candles_by_asset.values():
            closes = [c.close for c in candles]
            hit = sum(1 for p in closes if levels.is_round_number(p, tolerance))
            rate = Decimal(hit) / Decimal(len(closes))
            cells.append(f"{_fmt(rate):>8}")
            rates.append(rate)
            hits += hit
            total += len(closes)
        marker = "*" if tolerance == levels.DEFAULT_HANDLE_TOLERANCE else " "
        print(
            f"{str(tolerance):>9}{marker} " + " ".join(cells)
            + f" {_fmt(Decimal(hits) / Decimal(total)):>8}"
            + f" {_fmt(max(rates) / min(rates), '0.01'):>7}"
            + f" {str(levels.is_round_number(Decimal('64975.78'), tolerance)):>9}"
        )
    print("  * = shipped default. The last column is the correctness case #225 names.")


def print_reconstruction_check(
    reconstructed: dict[str, FactorSample],
    replayed: dict[str, FactorSample],
) -> None:
    """ARM E -- is the before-arm reconstruction exact? Replayed under the old predicate."""
    print("\nARM E -- reconstruction check: arithmetic before-arm vs a real replay of the old code")
    header = f"{'asset':10} {'N':>6} {'factor vec':>12} {'CTS totals':>12} {'other factors':>15}"
    print(header)
    print("-" * len(header))
    ok = True
    for product_id, replay in replayed.items():
        recon = reconstructed[product_id]
        vec_match = recon.vectors[FACTOR] == replay.vectors[FACTOR]
        tot_match = recon.totals == replay.totals
        others = all(
            recon.vectors[name] == replay.vectors[name] for name in FACTOR_NAMES if name != FACTOR
        )
        ok = ok and vec_match and tot_match and others
        print(
            f"{product_id:10} {replay.n:6d} {'MATCH' if vec_match else 'DIFFER':>12} "
            f"{'MATCH' if tot_match else 'DIFFER':>12} {'MATCH' if others else 'DIFFER':>15}"
        )
    print("-" * len(header))
    print(f"  reconstruction is {'EXACT' if ok else '*** WRONG -- do not quote arms A-C ***'}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Candle cache (default: {DEFAULT_DB})")
    parser.add_argument(
        "--skip-arm-e", action="store_true", help="Skip the (slow) reconstruction replay"
    )
    args = parser.parse_args()

    # `engine.evaluate` is not driven here, but `assemble_cts_context`'s callees log at INFO.
    logging.disable(logging.INFO)

    print("Round-number scale fix -- issue #225")
    print(f"db={args.db}  assets={','.join(ASSETS)}  granularity={PRIMARY_GRANULARITY.value}")
    print(
        f"factor={FACTOR} weight={FACTOR_WEIGHT} of {sum(DEFAULT_WEIGHTS.values())}  "
        f"handle tolerance={levels.DEFAULT_HANDLE_TOLERANCE}"
    )
    print("READ-ONLY. The candle cache is opened `mode=ro`; no weight, gate or rule is changed.")

    candles_by_asset: dict[str, list[Candle]] = {}
    after: dict[str, FactorSample] = {}
    before: dict[str, FactorSample] = {}
    for product_id in ASSETS:
        candles = load_candles(args.db, product_id, PRIMARY_GRANULARITY)
        if not candles:
            print(f"  {product_id}: no {PRIMARY_GRANULARITY.value} candles cached -- skipped")
            continue
        candles_by_asset[product_id] = candles
        sample = replay_every_bar(product_id, candles, window=None)
        after[product_id] = sample
        before[product_id] = before_from_after(
            sample, replayed_prices(candles, len(candles) - sample.n)
        )

    if not after:
        print("no candles on any asset -- nothing to measure")
        return

    print_presence(before, after)
    print_all_factor_rates(after)
    print_distribution(before, after)
    print_thresholds(before, after)
    print_tolerance_ladder(candles_by_asset)

    if args.skip_arm_e:
        print("\nARM E skipped (--skip-arm-e): arms A-C rest on an UNVERIFIED reconstruction.")
        return
    replayed = {
        product_id: replay_with(legacy_is_round_number, product_id, candles)
        for product_id, candles in candles_by_asset.items()
    }
    print_reconstruction_check(before, replayed)

    # Sanity: the module under test is the shipped one, not a stale import.
    assert indicators_cts.DEFAULT_WEIGHTS[FACTOR] == FACTOR_WEIGHT


if __name__ == "__main__":
    main()
