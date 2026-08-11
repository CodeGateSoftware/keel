"""Do keel's 11 CTS confluence factors carry independent evidence? -- issue #208.

CTS (`strategy/indicators_cts.py`) is an ADDITIVE score: 14 raw points spread over 11 named
factors, summed, and mapped to one of three entry techniques. Additivity is only defensible if
the factors are approximately independent. If three of them are three reads of the same momentum
series, then when they agree they agree BY CONSTRUCTION, and the score reports "strong
confluence" on what is really one piece of evidence counted three times. That is the
"confluence, not consensus" critique from the QuantCrawler teardown (#193 §1.5), turned into a
measurement.

⚠️ **This module measures. It does not score, gate, or change anything.** Nothing here is
imported by the live path; `DEFAULT_WEIGHTS` is read, never written.

WHY THIS NEEDED A REPLAY HARNESS AT ALL. The obvious data source is the `signals` table, which
has persisted a per-factor `cts_factors` breakdown since P3 Task 1 (`engine._persist_signal`).
It holds **one row** in `keel.db` and **zero** in `keel-live.db` -- the live book's rules are
daily turtles that fire a handful of times a year, and the audit trail only records signals that
cleared every gate. A correlation matrix needs thousands of observations, so the sample has to
be reconstructed. It can be: `engine.assemble_cts_context` is a pure function of
`(setup, candles)`, and `keel.db.candles` holds ~611k bars back to 2021.

TWO SAMPLES, BOTH REPORTED, BECAUSE THE OBVIOUS ONE IS BIASED:

- `replay_every_bar` -- UNCONDITIONAL. Every bar in the series gets a synthetic `Setup` priced
  at that bar's close, and the full context is assembled. This is the population the factors
  actually live in.
- `replay_fired` -- CONDITIONAL. Only bars where a real `Rule.detect()` produced a `Setup` that
  then cleared `engine.evaluate`'s choppy / higher-TF-bias / kill-zone gates. This is the exact
  population the live scorer sees.

The conditional sample is the tempting one ("correlate the factors on real signals"), and it is
the wrong one to draw the headline from. Rule detection and the engine's gates are functions of
the same regime and momentum state the factors read -- the choppy gate in particular admits a
bar only when `regime.detect_condition` is tradeable, which is most of what `condition_aligned`
measures. Conditioning on it truncates the joint distribution and moves every correlation
involving those factors by an unknown amount and sign (Berkson's paradox / collider
stratification). Reporting only the conditional matrix would be selecting on the outcome. Both
run; the unconditional one carries the conclusion.

THE WINDOW MATTERS, AND IT IS NOT A FREE PARAMETER. Several `analysis.*` calls behind the
context read the WHOLE list handed to them rather than a fixed lookback -- `levels.find_levels`
scans every pivot in the series, and `regime.detect_phase` compares the last close against
`candles[0]`. So factor presence depends on how much history the caller passes. The live path
(`agent.run_once` -> `repo.get_candles(product, gran)` with no bounds -> `engine.evaluate`)
passes the entire cached series, which `window=None` reproduces exactly (expanding window from
the first cached bar). A fixed `window` is offered because expanding is O(n^2) and infeasible on
44k hourly bars; when one is used, say so and check the answer against a second value.

MEASURES. For two boolean factor vectors the pairwise statistics are:

    phi     Pearson correlation of the two {0,1} vectors, computed exactly from the 2x2
            contingency counts. Identical to `research.independence.pearson` on the same input
            -- which is what `tests/research/test_cts_factors.py` asserts, using the shipped
            function as the oracle rather than trusting this module's arithmetic.
    Jaccard `research.independence.jaccard`, unchanged: #{both present} / #{either present}.
            Robust where phi is not, because a factor present on 2% of bars makes the
            joint-absence cell dominate phi's denominator.
    lift    P(both) / (P(a)P(b)). 1.0 is independence; it is the one measure whose scale does
            not shrink with rarity, so it catches "these two are rare, but always co-occur".

`independence.compare()` is deliberately NOT used. Its five §80.16 measurements assume daily
calendar-aligned in-market vectors plus a P&L series, and derive entries from the rising edges
of a held position. Factor presence is a per-bar flag with no holding period and no P&L, so
`pnl_correlation`, `entry_distances` and `median_entry_distance` have no analog. Only the two
primitives above transfer, and they are imported by name.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from keel.research.independence import jaccard
from keel.strategy import engine, indicators_cts
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

__all__ = [
    "FACTOR_NAMES",
    "SUSPECTED_CLUSTERS",
    "ClusterReport",
    "FactorSample",
    "PairStat",
    "VarianceReport",
    "cluster_report",
    "contingency",
    "factor_presence",
    "holm_adjust",
    "pair_stats",
    "phi",
    "pool",
    "replay_every_bar",
    "replay_fired",
    "variance_report",
]

#: The 11 factor names, in `DEFAULT_WEIGHTS` order. Read from the shipped table so a factor
#: added or removed there shows up here without an edit -- this module must never carry its own
#: copy of the scorer's shape.
FACTOR_NAMES: tuple[str, ...] = tuple(indicators_cts.DEFAULT_WEIGHTS)

#: The clusters issue #208 suspects, PRE-DECLARED before the run (§78.5) so the result cannot be
#: a cluster discovered by staring at the matrix. `momentum` is three reads downstream of one
#: price/velocity series -- `rsi_extreme` and `rsi_divergence` share the very same
#: `indicators.rsi(closes)` array (`engine.assemble_cts_context`), and `deceleration` is a third
#: momentum read on the same closes. `trend` is two structure reads. 4 of 14 raw points each.
SUSPECTED_CLUSTERS: dict[str, tuple[str, ...]] = {
    "momentum": ("rsi_extreme", "rsi_divergence", "deceleration"),
    "trend": ("condition_aligned", "ema_fan_aligned"),
}

#: Bars discarded at the head of every replay. The context's slowest input is the EMA fan
#: (`indicators.ema_fan`, longest period 200), so a shorter warm-up would score the opening
#: stretch of every series against a fan that has not converged.
DEFAULT_WARMUP = 200


# ---------------------------------------------------------------------------
# The sample
# ---------------------------------------------------------------------------


@dataclass
class FactorSample:
    """Per-bar presence vectors for every CTS factor, plus the CTS total on each bar.

    `vectors[name][i]` is 1 when factor `name` was present on observation `i`. Every vector has
    length `n`; `totals[i]` is the score `indicators_cts.score` awarded that observation under
    `DEFAULT_WEIGHTS`, kept so variance can be measured on the real total rather than on a
    reconstruction of it.
    """

    vectors: dict[str, list[int]] = field(default_factory=dict)
    totals: list[int] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.totals)

    def base_rate(self, name: str) -> Decimal:
        """Fraction of observations on which `name` was present; 0 for an empty sample."""
        vector = self.vectors.get(name, [])
        return Decimal(sum(vector)) / Decimal(len(vector)) if vector else Decimal(0)

    def varying(self) -> tuple[str, ...]:
        """Factor names that are neither always-present nor always-absent in this sample.

        A constant vector has no variance, so every correlation involving it is undefined and
        `phi` returns 0 by convention. Those pairs must be excluded from the multiple-testing
        budget rather than counted as 45 tests of which several were structurally guaranteed to
        return zero. `seasonality` is always in here: it is weighted 0 and hardcoded `False`.
        """
        out = []
        for name in FACTOR_NAMES:
            vector = self.vectors.get(name, [])
            if vector and 0 < sum(vector) < len(vector):
                out.append(name)
        return tuple(out)


def pool(samples: Iterable[FactorSample]) -> FactorSample:
    """Concatenate per-asset samples into one.

    Pooling across assets is safe in a way pooling P&L is not: presence is a dimensionless
    boolean, so there is no units problem (cf. `backtest()`'s per-unit quote-currency P&L, which
    `2026-08-08-between-family-independence.md` refuses to pool). It does assume the factor
    relationship is the same across assets, which is why the per-asset matrices are printed too.
    """
    merged = FactorSample(vectors={name: [] for name in FACTOR_NAMES})
    for sample in samples:
        for name in FACTOR_NAMES:
            merged.vectors[name].extend(sample.vectors.get(name, []))
        merged.totals.extend(sample.totals)
        merged.labels.extend(sample.labels)
    return merged


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def factor_presence(setup: Setup, candles: list[Candle]) -> tuple[dict[str, bool], int]:
    """`({factor name: present}, cts total)` for one `(setup, candles)` pair.

    Delegates to the SHIPPED `engine.assemble_cts_context` + `indicators_cts.score` -- nothing
    about the scoring path is reimplemented here, so a change to either is a change to this
    measurement, which is the point.
    """
    context = engine.assemble_cts_context(setup, candles)
    result = indicators_cts.score(context)
    return {f.name: f.present for f in result.factors}, result.total


def _synthetic_setup(product_id: str, candle: Candle) -> Setup:
    """A `Setup` standing in for "some rule proposed an entry at this bar's close".

    `assemble_cts_context` reads ONLY `setup.entry` (documented in its docstring), so the stop
    and target below are structurally inert -- they exist because `Setup` requires them. They
    are set to a plain 2:1 so the object is not a nonsense risk profile if anything downstream
    ever looks. Using the close as the entry is the neutral choice: it is the price a
    market-order rule would get on that bar, and it is what `round_number_proximity`,
    `fib_confluence` and the nearest-S/R-level lookup are measured against.
    """
    close = candle.close
    return Setup(
        product_id=product_id,
        direction="long",
        entry=close,
        stop=close * Decimal("0.98"),
        target=close * Decimal("1.04"),
        context={},
        ts=candle.ts,
    )


def _window(candles: list[Candle], index: int, window: int | None) -> list[Candle]:
    """History visible at bar `index`: expanding from bar 0 when `window` is None."""
    start = 0 if window is None else max(0, index - window + 1)
    return candles[start : index + 1]


def replay_every_bar(
    product_id: str,
    candles: list[Candle],
    *,
    warmup: int = DEFAULT_WARMUP,
    window: int | None = None,
    step: int = 1,
) -> FactorSample:
    """UNCONDITIONAL sample: assemble the CTS context on every bar from `warmup` onward.

    No rule is consulted and no gate is applied, so the resulting matrix describes the factors
    themselves rather than the factors as filtered by whatever fired. `step > 1` subsamples the
    bar index (thinning autocorrelation and cost at once); `window` is documented on the module.
    """
    sample = FactorSample(vectors={name: [] for name in FACTOR_NAMES})
    for index in range(warmup, len(candles), step):
        visible = _window(candles, index, window)
        presence, total = factor_presence(_synthetic_setup(product_id, candles[index]), visible)
        for name in FACTOR_NAMES:
            sample.vectors[name].append(1 if presence.get(name) else 0)
        sample.totals.append(total)
        sample.labels.append(f"{product_id}@{candles[index].ts}")
    return sample


def replay_fired(
    rule: Rule,
    granularity: Granularity,
    candles: list[Candle],
    *,
    warmup: int = DEFAULT_WARMUP,
    window: int | None = None,
    step: int = 1,
) -> FactorSample:
    """CONDITIONAL sample: only bars on which `engine.evaluate` actually emitted a `Signal`.

    Drives the real `engine.evaluate` rather than reimplementing its gate ladder, so the choppy,
    higher-TF-bias and kill-zone gates apply exactly as they do in production, and a DCA-class
    setup is exempted from them exactly as it is in production. The context is then re-assembled
    from the emitted signal's setup -- the same move `sim.portfolio_sim._record_cts_telemetry`
    makes, and necessary because `Signal` carries the CTS total but not the per-factor breakdown.

    ⚠️ See the module docstring: this sample is conditioned on quantities correlated with the
    factors being measured. It is reported for completeness and comparison, never alone.
    """
    sample = FactorSample(vectors={name: [] for name in FACTOR_NAMES})
    for index in range(warmup, len(candles), step):
        visible = _window(candles, index, window)
        for signal in engine.evaluate([rule], {granularity: visible}):
            if signal.setup is None:
                continue
            presence, total = factor_presence(signal.setup, visible)
            for name in FACTOR_NAMES:
                sample.vectors[name].append(1 if presence.get(name) else 0)
            sample.totals.append(total)
            sample.labels.append(f"{rule.name}@{candles[index].ts}")
    return sample


# ---------------------------------------------------------------------------
# Pairwise statistics
# ---------------------------------------------------------------------------


def contingency(xs: Sequence[int], ys: Sequence[int]) -> tuple[int, int, int, int]:
    """The 2x2 table `(n11, n10, n01, n00)` over two aligned boolean vectors."""
    n11 = n10 = n01 = n00 = 0
    for x, y in zip(xs, ys):
        if x:
            if y:
                n11 += 1
            else:
                n10 += 1
        elif y:
            n01 += 1
        else:
            n00 += 1
    return n11, n10, n01, n00


def phi(xs: Sequence[int], ys: Sequence[int]) -> Decimal:
    """Phi coefficient -- Pearson correlation of two {0,1} vectors, from the 2x2 table.

    Exact integer arithmetic up to one square root, and O(n) with no Decimal work in the loop,
    which matters at ~200k observations x 45 pairs. Returns 0 when either vector is constant,
    matching `independence.pearson`'s documented convention for a variance-free series (and for
    the same reason: one degenerate factor must not abort a whole matrix).
    """
    n11, n10, n01, n00 = contingency(xs, ys)
    with localcontext() as ctx:
        ctx.prec = 50
        numerator = Decimal(n11 * n00 - n10 * n01)
        product = Decimal((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
        if product <= 0:
            return Decimal(0)
        return +(numerator / product.sqrt())


def _two_sided_p(correlation: Decimal, n: int) -> float:
    """p-value for `phi` under H0 of independence, from the chi-square-of-1-df identity.

    `chi2 = n * phi^2` for a 2x2 table, and `sqrt(chi2_1)` is a standard normal deviate, so the
    two-sided p is `erfc(|phi| * sqrt(n/2))`. Asymptotic; at the sample sizes here the normal
    approximation is not what limits the conclusion (see `holm_adjust`).
    """
    if n < 2:
        return 1.0
    return math.erfc(abs(float(correlation)) * math.sqrt(n / 2))


@dataclass(frozen=True)
class PairStat:
    """One factor pair's co-occurrence, with the multiple-testing bookkeeping attached."""

    a: str
    b: str
    n: int
    n11: int
    n10: int
    n01: int
    n00: int
    phi: Decimal
    jaccard: Decimal
    lift: Decimal
    p_value: float
    p_holm: float = 1.0
    significant: bool = False


def pair_stats(sample: FactorSample, names: Sequence[str] | None = None) -> list[PairStat]:
    """Every unordered pair of `names` (default: the sample's non-constant factors).

    Ordered by descending |phi|, which is a presentation choice only -- the multiple-testing
    correction in `holm_adjust` is applied over the whole family regardless of print order.
    """
    chosen = tuple(names) if names is not None else sample.varying()
    out: list[PairStat] = []
    for index, first in enumerate(chosen):
        for second in chosen[index + 1 :]:
            xs = sample.vectors.get(first, [])
            ys = sample.vectors.get(second, [])
            n11, n10, n01, n00 = contingency(xs, ys)
            n = n11 + n10 + n01 + n00
            correlation = phi(xs, ys)
            expected = Decimal((n11 + n10) * (n11 + n01))
            lift = (Decimal(n11) * Decimal(n)) / expected if expected > 0 else Decimal(0)
            out.append(
                PairStat(
                    a=first,
                    b=second,
                    n=n,
                    n11=n11,
                    n10=n10,
                    n01=n01,
                    n00=n00,
                    phi=correlation,
                    jaccard=jaccard(xs, ys),
                    lift=lift,
                    p_value=_two_sided_p(correlation, n),
                )
            )
    return sorted(out, key=lambda stat: abs(stat.phi), reverse=True)


def holm_adjust(stats: Sequence[PairStat], alpha: float = 0.05) -> list[PairStat]:
    """Holm-Bonferroni step-down over the whole family of pairwise tests.

    Holm rather than plain Bonferroni: it controls the same family-wise error rate and is
    uniformly more powerful, and there is no reason to accept the weaker test. The family is
    every pair TESTED, `k(k-1)/2` over the non-constant factors -- not `11*10/2`, which would
    charge the budget for pairs no test was run on.

    ⚠️ Read the honest caveat in the write-up before quoting any of these. At n in the tens or
    hundreds of thousands, `phi = 0.01` clears any threshold; significance here answers "is this
    correlation exactly zero", which nobody asked, while the question actually asked is "is it
    big enough to distort an additive score". That is an effect-size question, and the p-values
    are reported to show the correction was applied, not to carry the argument.
    """
    ordered = sorted(stats, key=lambda stat: stat.p_value)
    total = len(ordered)
    adjusted: list[PairStat] = []
    running = 0.0
    still_rejecting = True
    for index, stat in enumerate(ordered):
        running = max(running, min(1.0, stat.p_value * (total - index)))
        still_rejecting = still_rejecting and running <= alpha
        adjusted.append(
            PairStat(
                **{
                    **stat.__dict__,
                    "p_holm": running,
                    "significant": still_rejecting,
                }
            )
        )
    return sorted(adjusted, key=lambda stat: abs(stat.phi), reverse=True)


# ---------------------------------------------------------------------------
# Cluster + variance summaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterReport:
    """Within-cluster vs. rest-of-matrix correlation for one pre-declared cluster."""

    name: str
    members: tuple[str, ...]
    within_pairs: int
    mean_within_phi: Decimal
    max_within_phi: Decimal
    mean_other_phi: Decimal
    mean_within_jaccard: Decimal
    weight_share: Decimal


def cluster_report(
    sample: FactorSample,
    stats: Sequence[PairStat],
    clusters: dict[str, tuple[str, ...]] | None = None,
    weights: dict[str, int] | None = None,
) -> list[ClusterReport]:
    """Does each pre-declared cluster actually cluster?

    The comparison that matters is `mean_within_phi` against `mean_other_phi` (every pair with
    at most one member in the cluster). A cluster whose internal correlation is indistinguishable
    from the rest of the matrix is not a cluster, however plausible its wiring diagram looks.
    Means are taken over SIGNED phi: taking |phi| would report a cluster whose members
    systematically EXCLUDE one another as tightly coupled, which is a different finding and must
    not be laundered into this one. `max_within_phi` is signed for the same reason.
    """
    clusters = SUSPECTED_CLUSTERS if clusters is None else clusters
    weights = indicators_cts.DEFAULT_WEIGHTS if weights is None else weights
    total_weight = sum(weights.values())
    out: list[ClusterReport] = []

    for name, members in clusters.items():
        inside = [s for s in stats if s.a in members and s.b in members]
        outside = [s for s in stats if not (s.a in members and s.b in members)]
        if not inside:
            continue
        out.append(
            ClusterReport(
                name=name,
                members=members,
                within_pairs=len(inside),
                mean_within_phi=_mean(s.phi for s in inside),
                max_within_phi=max(inside, key=lambda s: abs(s.phi)).phi,
                mean_other_phi=_mean(s.phi for s in outside),
                mean_within_jaccard=_mean(s.jaccard for s in inside),
                weight_share=(
                    Decimal(sum(weights.get(m, 0) for m in members)) / Decimal(total_weight)
                    if total_weight
                    else Decimal(0)
                ),
            )
        )
    # `sample` is accepted for symmetry with the other reporters and to keep call sites uniform;
    # every figure above is derivable from `stats` alone.
    del sample
    return out


@dataclass(frozen=True)
class VarianceReport:
    """How much correlation inflates the spread of the CTS total.

    `observed` is the sample variance of the real `indicators_cts.score` totals. `independent`
    is what that variance would be if the factors were mutually independent with the SAME base
    rates -- `sum(w_i^2 * p_i * (1 - p_i))`, since each factor is Bernoulli. Their ratio is the
    single number that answers issue #208's actual worry: additivity assumes the cross terms
    vanish, and this is how much they do not.
    """

    observed: Decimal
    independent: Decimal
    ratio: Decimal
    mean_total: Decimal


def variance_report(sample: FactorSample, weights: dict[str, int] | None = None) -> VarianceReport:
    """Observed vs. independence-implied variance of the CTS total."""
    weights = indicators_cts.DEFAULT_WEIGHTS if weights is None else weights
    n = sample.n
    if n < 2:
        return VarianceReport(Decimal(0), Decimal(0), Decimal(0), Decimal(0))

    mean = Decimal(sum(sample.totals)) / Decimal(n)
    observed = sum(((Decimal(t) - mean) ** 2 for t in sample.totals), Decimal(0)) / Decimal(n - 1)

    independent = Decimal(0)
    for name, weight in weights.items():
        rate = sample.base_rate(name)
        independent += Decimal(weight) ** 2 * rate * (Decimal(1) - rate)

    ratio = observed / independent if independent > 0 else Decimal(0)
    return VarianceReport(
        observed=observed, independent=independent, ratio=ratio, mean_total=mean
    )


def _mean(values: Iterable[Decimal]) -> Decimal:
    collected = list(values)
    return sum(collected, Decimal(0)) / Decimal(len(collected)) if collected else Decimal(0)
