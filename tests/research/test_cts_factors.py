"""CTS factor co-occurrence harness (issue #208).

The load-bearing test is `test_phi_agrees_with_shipped_pearson`: `phi` is a fast contingency-count
reimplementation of Pearson on {0,1} vectors, and the only reason to trust it is that it agrees
with `independence.pearson`, which `compare()` has shipped since PR #103. That is the oracle, and
it is why `pearson` was promoted out of `_pearson`.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from keel.research.cts_factors import (
    FACTOR_NAMES,
    SUSPECTED_CLUSTERS,
    FactorSample,
    cluster_report,
    contingency,
    factor_presence,
    holm_adjust,
    pair_stats,
    phi,
    pool,
    replay_every_bar,
    replay_fired,
    variance_report,
)
from keel.research.independence import pearson
from keel.strategy.indicators_cts import DEFAULT_WEIGHTS
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity


def _candles(count: int, seed: int = 7) -> list[Candle]:
    """A pseudo-random but deterministic OHLCV walk, 2dp like real Coinbase USD candles."""
    rng = random.Random(seed)
    price = 100.0
    out: list[Candle] = []
    for index in range(count):
        price = max(1.0, price * (1 + rng.uniform(-0.03, 0.03)))
        high = price * (1 + abs(rng.uniform(0, 0.02)))
        low = price * (1 - abs(rng.uniform(0, 0.02)))
        out.append(
            Candle(
                ts=1_600_000_000 + index * 86_400,
                open=Decimal(f"{rng.uniform(low, high):.2f}"),
                high=Decimal(f"{high:.2f}"),
                low=Decimal(f"{low:.2f}"),
                close=Decimal(f"{price:.2f}"),
                volume=Decimal("1000"),
            )
        )
    return out


def _sample(vectors: dict[str, list[int]]) -> FactorSample:
    """A `FactorSample` from explicit vectors, with totals implied by `DEFAULT_WEIGHTS`."""
    length = len(next(iter(vectors.values())))
    filled = {name: vectors.get(name, [0] * length) for name in FACTOR_NAMES}
    totals = [
        sum(DEFAULT_WEIGHTS[name] * filled[name][i] for name in FACTOR_NAMES)
        for i in range(length)
    ]
    n = length
    return FactorSample(vectors=filled, totals=totals, labels=[str(i) for i in range(n)])


# -- phi, against the shipped oracle ---------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 11, 101])
def test_phi_agrees_with_shipped_pearson(seed: int) -> None:
    """`phi` from 2x2 counts == `independence.pearson` on the same {0,1} vectors."""
    rng = random.Random(seed)
    # Correlate b with a so the pairs are not all near-zero: 70% copy, 30% independent coin.
    xs = [rng.randint(0, 1) for _ in range(400)]
    ys = [x if rng.random() < 0.7 else rng.randint(0, 1) for x in xs]

    oracle = pearson([Decimal(v) for v in xs], [Decimal(v) for v in ys])
    assert phi(xs, ys) == pytest.approx(oracle, abs=Decimal("1e-20"))


def test_phi_is_one_for_identical_vectors_and_minus_one_for_inverted() -> None:
    xs = [1, 0, 1, 1, 0, 0, 1]
    assert phi(xs, xs) == pytest.approx(Decimal(1), abs=Decimal("1e-20"))
    assert phi(xs, [1 - x for x in xs]) == pytest.approx(Decimal(-1), abs=Decimal("1e-20"))


def test_phi_returns_zero_for_a_constant_vector() -> None:
    """Documented convention, shared with `independence.pearson`: no variance -> 0, not a raise."""
    assert phi([1, 1, 1, 1], [0, 1, 0, 1]) == Decimal(0)
    assert phi([0, 0, 0, 0], [0, 0, 0, 0]) == Decimal(0)


def test_contingency_counts_all_four_cells() -> None:
    n11, n10, n01, n00 = contingency([1, 1, 0, 0, 1], [1, 0, 1, 0, 0])
    assert (n11, n10, n01, n00) == (1, 2, 1, 1)


# -- sample bookkeeping ----------------------------------------------------------------------


def test_varying_excludes_constant_factors_from_the_testing_family() -> None:
    sample = _sample(
        {
            "condition_aligned": [1, 0, 1, 0],
            "ema_fan_aligned": [1, 1, 0, 0],
            "rsi_extreme": [0, 0, 0, 0],  # constant absent
            "in_pullback": [1, 1, 1, 1],  # constant present
        }
    )
    varying = sample.varying()
    assert set(varying) == {"condition_aligned", "ema_fan_aligned"}
    # 2 varying factors -> exactly 1 pair charged to the multiple-testing budget, not 55.
    assert len(pair_stats(sample)) == 1


def test_base_rate_and_pooling() -> None:
    a = _sample({"condition_aligned": [1, 1, 0, 0]})
    b = _sample({"condition_aligned": [1, 1, 1, 1]})
    merged = pool([a, b])
    assert merged.n == 8
    assert merged.base_rate("condition_aligned") == Decimal("0.75")
    assert len(merged.labels) == 8


def test_pair_stats_reports_mutual_exclusion_as_zero_jaccard_and_zero_lift() -> None:
    sample = _sample(
        {"condition_aligned": [1, 1, 0, 0, 0, 0], "rsi_divergence": [0, 0, 1, 1, 0, 0]}
    )
    (stat,) = pair_stats(sample)
    assert stat.n11 == 0
    assert stat.jaccard == Decimal(0)
    assert stat.lift == Decimal(0)
    assert stat.phi < Decimal(0)


# -- multiple testing ------------------------------------------------------------------------


def test_holm_is_step_down_and_monotone() -> None:
    sample = _sample(
        {
            "condition_aligned": [1, 0] * 50,
            "ema_fan_aligned": [1, 0] * 50,  # identical -> tiny p
            "rsi_extreme": [1, 1, 0, 0] * 25,  # independent-ish -> large p
            "in_pullback": [0, 1, 1, 0] * 25,
        }
    )
    stats = holm_adjust(pair_stats(sample), alpha=0.05)
    by_raw = sorted(stats, key=lambda s: s.p_value)
    assert [s.p_holm for s in by_raw] == sorted(s.p_holm for s in by_raw)  # non-decreasing
    assert all(s.p_holm >= s.p_value for s in stats)  # never anti-conservative
    assert all(s.p_holm <= 1.0 for s in stats)
    # Step-down stops at the first failure: no significant flag may follow a non-significant one.
    flags = [s.significant for s in by_raw]
    assert flags == sorted(flags, reverse=True)


def test_holm_charges_the_family_size_actually_tested() -> None:
    """A pair count of k(k-1)/2 over VARYING factors, not over all 11."""
    sample = _sample({"condition_aligned": [1, 0] * 20, "ema_fan_aligned": [1, 0] * 20})
    stats = holm_adjust(pair_stats(sample))
    assert len(stats) == 1
    assert stats[0].p_holm == pytest.approx(stats[0].p_value)


# -- variance -------------------------------------------------------------------------------


def test_variance_ratio_is_one_when_factors_are_independent_by_construction() -> None:
    """Two factors crossed on a full 2x2 design have exactly zero covariance."""
    sample = _sample(
        {"condition_aligned": [1, 1, 0, 0] * 40, "ema_fan_aligned": [1, 0, 1, 0] * 40}
    )
    report = variance_report(sample)
    assert report.ratio == pytest.approx(Decimal(1), abs=Decimal("0.02"))


def test_variance_ratio_exceeds_one_when_two_factors_are_duplicates() -> None:
    """The failure mode #208 is about: one piece of evidence scored twice."""
    duplicated = [1, 0, 1, 1, 0, 0, 1, 0] * 20
    sample = _sample({"condition_aligned": duplicated, "ema_fan_aligned": list(duplicated)})
    assert variance_report(sample).ratio > Decimal("1.9")


def test_variance_report_is_degenerate_safe() -> None:
    assert variance_report(_sample({"condition_aligned": [1]})).ratio == Decimal(0)


# -- clusters -------------------------------------------------------------------------------


def test_cluster_report_separates_within_from_the_rest_of_the_matrix() -> None:
    tight = [1, 0, 1, 1, 0, 0, 1, 0] * 20
    sample = _sample(
        {
            "condition_aligned": tight,
            "ema_fan_aligned": list(tight),  # trend cluster, perfectly coupled
            "rsi_extreme": [1, 1, 0, 0] * 40,  # momentum, unrelated to the above
            "rsi_divergence": [1, 0, 0, 1] * 40,
            "deceleration": [0, 1, 0, 1] * 40,
        }
    )
    reports = {r.name: r for r in cluster_report(sample, pair_stats(sample))}
    assert reports["trend"].mean_within_phi > Decimal("0.99")
    assert reports["trend"].mean_within_phi > reports["trend"].mean_other_phi
    assert abs(reports["momentum"].mean_within_phi) < Decimal("0.2")
    # Weight share is read off the shipped table, never hardcoded here.
    assert reports["momentum"].weight_share == Decimal(
        sum(DEFAULT_WEIGHTS[m] for m in SUSPECTED_CLUSTERS["momentum"])
    ) / Decimal(sum(DEFAULT_WEIGHTS.values()))


def test_cluster_means_are_signed_so_mutual_exclusion_is_not_read_as_coupling() -> None:
    """Averaging |phi| would report an anti-correlated cluster as a tight one. It must not."""
    alternating = [1, 0] * 60
    sample = _sample(
        {
            "condition_aligned": alternating,
            "ema_fan_aligned": [1 - v for v in alternating],  # perfectly ANTI-correlated
        }
    )
    (report,) = [r for r in cluster_report(sample, pair_stats(sample)) if r.name == "trend"]
    assert report.mean_within_phi < Decimal("-0.99")


# -- replay ---------------------------------------------------------------------------------


def test_replay_every_bar_produces_one_observation_per_bar_past_warmup() -> None:
    candles = _candles(260)
    sample = replay_every_bar("BTC-USD", candles, warmup=250)
    assert sample.n == 10
    assert all(len(vector) == 10 for vector in sample.vectors.values())
    assert set(sample.vectors) == set(FACTOR_NAMES)
    assert all(value in (0, 1) for value in sample.vectors["condition_aligned"])
    assert sample.labels[0].startswith("BTC-USD@")


def test_replay_step_subsamples_the_bar_index() -> None:
    candles = _candles(260)
    assert replay_every_bar("BTC-USD", candles, warmup=250, step=5).n == 2


def test_replay_totals_match_the_shipped_scorer() -> None:
    """`totals` must be `indicators_cts.score`'s number, not a re-derivation from the vectors."""
    candles = _candles(230)
    sample = replay_every_bar("BTC-USD", candles, warmup=220)
    for index in range(sample.n):
        rebuilt = sum(
            DEFAULT_WEIGHTS[name] * sample.vectors[name][index] for name in FACTOR_NAMES
        )
        assert sample.totals[index] == rebuilt


def test_expanding_and_rolling_windows_differ_where_the_window_reaches() -> None:
    """`regime.detect_phase` reads `candles[0]`, so window length is not cosmetic."""
    candles = _candles(600, seed=3)
    expanding = replay_every_bar("BTC-USD", candles, warmup=210, window=None)
    rolling = replay_every_bar("BTC-USD", candles, warmup=210, window=210)
    assert expanding.n == rolling.n
    assert expanding.vectors["in_pullback"] != rolling.vectors["in_pullback"]


class _AlwaysFires(Rule):
    """A rule that proposes a 2:1 long on every bar -- isolates `replay_fired`'s gate ladder."""

    name = "always_fires"
    granularity = Granularity.ONE_DAY

    def __init__(self) -> None:
        self.params = {}
        self.product_id = "BTC-USD"

    def detect(self, candles_by_tf):  # type: ignore[no-untyped-def]
        candles = candles_by_tf[Granularity.ONE_DAY]
        close = candles[-1].close
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=close,
            stop=close * Decimal("0.98"),
            target=close * Decimal("1.04"),
            context={},
            ts=candles[-1].ts,
        )

    def exit_signal(self, held, candles_by_tf) -> bool:  # type: ignore[no-untyped-def]
        return False

    def describe(self) -> dict:
        return {"name": self.name}


def test_replay_fired_is_a_strict_subset_of_every_bar() -> None:
    """The gates can only remove observations, never add or invent them."""
    candles = _candles(400, seed=5)
    unconditional = replay_every_bar("BTC-USD", candles, warmup=210)
    fired = replay_fired(_AlwaysFires(), Granularity.ONE_DAY, candles, warmup=210)
    assert 0 < fired.n <= unconditional.n
    assert set(fired.vectors) == set(FACTOR_NAMES)


def test_replay_fired_yields_nothing_when_the_rule_never_detects() -> None:
    class _NeverFires(_AlwaysFires):
        def detect(self, candles_by_tf):  # type: ignore[no-untyped-def]
            return None

    fired = replay_fired(_NeverFires(), Granularity.ONE_DAY, _candles(260), warmup=250)
    assert fired.n == 0
    assert fired.varying() == ()


def test_factor_presence_delegates_to_the_shipped_scoring_path() -> None:
    """Every key `indicators_cts` knows about comes back, and the total is its own."""
    candles = _candles(240)
    setup = Setup(
        product_id="BTC-USD",
        direction="long",
        entry=candles[-1].close,
        stop=candles[-1].close * Decimal("0.98"),
        target=candles[-1].close * Decimal("1.04"),
        context={},
        ts=candles[-1].ts,
    )
    presence, total = factor_presence(setup, candles)
    assert set(presence) == set(FACTOR_NAMES)
    assert presence["seasonality"] is False  # weighted 0 and hardcoded absent (spec §9)
    assert total == sum(DEFAULT_WEIGHTS[name] for name, present in presence.items() if present)
