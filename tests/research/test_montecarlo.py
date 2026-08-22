"""The pure Monte Carlo / bootstrap math (#441), pinned BEFORE the module exists.

These tests are the contract for `keel/research/montecarlo.py`: two resampling nulls
(trade reshuffle, moving-block candle bootstrap), the exact-Decimal percentile that
reads an observed equity curve against them, and the report whose every rendering
carries the refusal line. The honesty requirements are pinned as ASSERTIONS, not
conventions:

* determinism under a seed is a keel constraint -- same seed, identical paths, twice;
* the bootstrap must never alias or mutate its input candles, and must re-anchor
  timestamps so downstream indicators see a well-formed series;
* `render_lines` must always name what this does NOT answer (significance,
  `keel/research/significance.py`) -- a measurement that lets itself be read as a
  verdict is a flattery tool.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.research.montecarlo import (
    MonteCarloReport,
    equity_curve,
    final_equities,
    max_drawdown,
    median,
    moving_block_bootstrap,
    percentile_of,
    reshuffle,
)
from keel.types import Candle

_PNLS = [
    Decimal("3"),
    Decimal("-1"),
    Decimal("5"),
    Decimal("-2"),
    Decimal("2"),
    Decimal("-4"),
    Decimal("7"),
    Decimal("-1"),
]


def _candles(n: int, *, start: int = 1_700_000_000, step: int = 3600) -> list[Candle]:
    """`n` hourly bars with DISTINCT OHLCV per bar, so a resampled bar is identifiable by value
    and a permutation cannot hide behind duplicated bars."""
    return [
        Candle(
            ts=start + i * step,
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal(10 + i),
        )
        for i in range(n)
    ]


# -- reshuffle: same trades, different order -----------------------------------------------------


def test_reshuffle_paths_are_permutations_of_the_input_multiset() -> None:
    paths = reshuffle(_PNLS, 25, seed=7)
    assert len(paths) == 25
    for path in paths:
        assert len(path) == len(_PNLS)
        assert sorted(path) == sorted(_PNLS)  # a permutation, never a resample with replacement


def test_reshuffle_is_deterministic_under_a_fixed_seed() -> None:
    assert reshuffle(_PNLS, 10, seed=123) == reshuffle(_PNLS, 10, seed=123)


def test_reshuffle_different_seed_moves_at_least_one_path() -> None:
    assert reshuffle(_PNLS, 10, seed=1) != reshuffle(_PNLS, 10, seed=2)


# -- equity curve / final equities / max drawdown / median ----------------------------------------


def test_equity_curve_is_exact_cumulative_arithmetic() -> None:
    pnls = [Decimal("0.1"), Decimal("-0.3"), Decimal("0.2")]
    assert equity_curve(pnls, Decimal("100")) == [
        Decimal("100"),
        Decimal("100.1"),
        Decimal("99.8"),
        Decimal("100"),
    ]
    assert equity_curve([], Decimal(5)) == [Decimal(5)]


def test_final_equities_and_median_handle_length_parity_exactly() -> None:
    paths = [[Decimal(1), Decimal(2)], [Decimal(3), Decimal(-1)], [Decimal(5)], []]
    assert final_equities(paths, Decimal(10)) == [
        Decimal(13),
        Decimal(12),
        Decimal(15),
        Decimal(10),
    ]
    # Odd length: the middle of the sorted values; even: the exact mean of the two middles.
    assert median([Decimal(13), Decimal(12), Decimal(15)]) == Decimal(13)
    assert median([Decimal(1), Decimal(2), Decimal(3), Decimal(10)]) == Decimal("2.5")


def test_max_drawdown_exact_hand_computed_cases() -> None:
    # [0, 3, 2, -1, 5]: peak 3, trough -1 -> 4; the recovery to 5 opens no new hole.
    curve = equity_curve([Decimal(3), Decimal(-1), Decimal(-3), Decimal(6)], Decimal(0))
    assert curve == [Decimal(0), Decimal(3), Decimal(2), Decimal(-1), Decimal(5)]
    assert max_drawdown(curve) == Decimal(4)
    # A monotonic curve is never underwater.
    assert max_drawdown([Decimal("0.5"), Decimal(1), Decimal("2.5")]) == Decimal(0)
    # Underwater from the first point: peak 10, trough 4 -> 6 (the rise to 6 only digs 4).
    assert max_drawdown([Decimal(10), Decimal(4), Decimal(6)]) == Decimal(6)
    # A single point has no decline; an empty curve is refused rather than zeroed.
    assert max_drawdown([Decimal(7)]) == Decimal(0)
    with pytest.raises(ValueError, match="empty"):
        max_drawdown([])


def test_max_drawdown_spreads_when_ordering_matters() -> None:
    """The statistic trades mode exists to measure: the SAME multiset, different orders,
    different max drawdowns -- clustered losses (6,5,-4,-4: dd 8) dig deeper than
    interleaved ones (6,-4,5,-4: dd 4), while every ordering still sums to 3."""
    pnls = [Decimal(6), Decimal(5), Decimal(-4), Decimal(-4)]
    paths = reshuffle(pnls, 40, seed=4)
    drawdowns = {max_drawdown(equity_curve(path, Decimal(0))) for path in paths}
    assert drawdowns == {Decimal(4), Decimal(8)}
    assert {equity_curve(path, Decimal(0))[-1] for path in paths} == {Decimal(3)}


# -- moving-block bootstrap ------------------------------------------------------------------------


def test_bootstrap_output_matches_input_length_and_reanchors_timestamps() -> None:
    source = _candles(12)
    for path in moving_block_bootstrap(source, block_len=5, n_paths=6, seed=9, step_sec=3600):
        assert len(path) == 12  # same total length, never longer or shorter
        assert [c.ts for c in path] == [1_700_000_000 + i * 3600 for i in range(12)]


def test_bootstrap_first_block_comes_contiguously_from_the_input() -> None:
    source = _candles(12)
    path = moving_block_bootstrap(source, block_len=5, n_paths=1, seed=3, step_sec=3600)[0]
    ohlcv = [(c.open, c.high, c.low, c.close, c.volume) for c in source]
    doubled = ohlcv + ohlcv  # a block may wrap the series tail INSIDE itself
    first_block = [(c.open, c.high, c.low, c.close, c.volume) for c in path[:5]]
    assert any(doubled[i : i + 5] == first_block for i in range(len(ohlcv)))


def test_bootstrap_builds_fresh_candles_and_never_mutates_its_input() -> None:
    source = _candles(12)
    snapshot = list(source)
    input_ids = {id(c) for c in source}
    paths = moving_block_bootstrap(source, block_len=4, n_paths=5, seed=11, step_sec=3600)
    assert source == snapshot  # the input list and its bars are untouched
    input_ohlcv = {(c.open, c.high, c.low, c.close, c.volume) for c in source}
    for path in paths:
        for candle in path:
            assert id(candle) not in input_ids  # fresh instances, never aliases
            # Decimal OHLCV carried verbatim from the bar that was sampled.
            assert (candle.open, candle.high, candle.low, candle.close, candle.volume) in (
                input_ohlcv
            )


def test_bootstrap_is_deterministic_under_a_fixed_seed() -> None:
    kwargs = {"block_len": 5, "n_paths": 4, "seed": 99, "step_sec": 3600}
    assert moving_block_bootstrap(_candles(10), **kwargs) == moving_block_bootstrap(
        _candles(10), **kwargs
    )


def test_bootstrap_returns_n_paths_paths() -> None:
    paths = moving_block_bootstrap(_candles(8), block_len=3, n_paths=9, seed=5, step_sec=60)
    assert len(paths) == 9


def test_bootstrap_refuses_degenerate_arguments() -> None:
    source = _candles(6)
    with pytest.raises(ValueError, match="block_len"):
        moving_block_bootstrap(source, block_len=0, n_paths=2, seed=1, step_sec=60)
    with pytest.raises(ValueError, match="n_paths"):
        moving_block_bootstrap(source, block_len=2, n_paths=0, seed=1, step_sec=60)
    with pytest.raises(ValueError, match="empty"):
        moving_block_bootstrap([], block_len=2, n_paths=2, seed=1, step_sec=60)
    with pytest.raises(ValueError, match="step_sec"):
        moving_block_bootstrap(source, block_len=2, n_paths=2, seed=1, step_sec=0)


# -- percentile_of ---------------------------------------------------------------------------------


def test_percentile_of_below_all_above_all_and_the_tie_convention() -> None:
    dist = [Decimal(1), Decimal(2), Decimal(2), Decimal(3)]
    assert percentile_of(Decimal("0.5"), dist) == Decimal(0)
    assert percentile_of(Decimal(4), dist) == Decimal(1)
    # One strictly below, two ties: (1 + 2 * 1/2) / 4 = 0.5 exactly.
    assert percentile_of(Decimal(2), dist) == Decimal("0.5")
    # All ties -- the trades-mode shape: exactly one half, in exact Decimal.
    assert percentile_of(Decimal(2), [Decimal(2)] * 5) == Decimal("0.5")
    with pytest.raises(ValueError, match="empty"):
        percentile_of(Decimal(1), [])


# -- the report ------------------------------------------------------------------------------------


def _report(**overrides: object) -> MonteCarloReport:
    base: dict[str, object] = dict(
        mode="candles",
        n_paths=7,
        seed=42,
        start=Decimal(0),
        n_trades=9,
        observed_final=Decimal("12.5"),
        distribution_min=Decimal("-3"),
        distribution_median=Decimal(9),
        distribution_max=Decimal(21),
        percentile=Decimal("0.714"),
        observed_drawdown=Decimal("2.25"),
        drawdown_min=Decimal("1"),
        drawdown_median=Decimal("4.5"),
        drawdown_max=Decimal("9"),
        drawdown_percentile=Decimal("0.286"),
        block_len=24,
    )
    base.update(overrides)
    return MonteCarloReport(**base)  # type: ignore[arg-type]


def test_render_lines_always_state_the_load_bearing_numbers_and_the_refusal() -> None:
    lines = "\n".join(_report().render_lines())
    for needle in (
        "candles",
        "42",
        "7",
        "12.5",
        "-3",
        "21",
        "0.714",
        "24",
        # The shape statistic rides along in both modes, with its own distribution and
        # percentile -- never only the endpoint.
        "max drawdown",
        "2.25",
        "drawdown percentile",
        "0.286",
    ):
        assert needle in lines
    assert "path luck" in lines
    # The one-line refusal: what this does NOT answer, naming where that question lives.
    assert "does not answer" in lines
    assert "significance" in lines


def test_render_lines_in_trades_mode_name_the_by_construction_invariance() -> None:
    """Reshuffling the SAME trades cannot move the final equity; the report must say so rather
    than let a degenerate 1/2 percentile read as 'perfectly median path luck' -- and point at
    the drawdown block as the statistic that ordering actually moves."""
    lines = "\n".join(
        _report(
            mode="trades",
            percentile=Decimal("0.5"),
            block_len=None,
            distribution_min=Decimal("12.5"),
            distribution_median=Decimal("12.5"),
            distribution_max=Decimal("12.5"),
        ).render_lines()
    )
    assert "by construction" in lines
    assert "max drawdown" in lines


def test_a_reshuffled_sample_reports_the_median_by_construction() -> None:
    """End-to-end through the pure pieces: fixed pnls, reshuffled, the observed final sits at
    exactly 1/2 of the resampled finals because every path sums to the same number."""
    pnls = [Decimal("3"), Decimal("-1"), Decimal("5"), Decimal("-2"), Decimal("2")]
    observed = equity_curve(pnls, Decimal(0))[-1]
    assert observed == sum(pnls, Decimal(0))
    finals = final_equities(reshuffle(pnls, 30, seed=8), Decimal(0))
    assert min(finals) == observed == max(finals)
    assert percentile_of(observed, finals) == Decimal("0.5")
