"""CSCV / PBO tests (spec §5). The power replication is the load-bearing one."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from keel.research import cscv


def _const_columns(values: list[float], t: int = 32) -> list[list[Decimal]]:
    """N columns, column n being a constant-drift series with a fixed alternating wiggle.

    The wiggle is +/-10 so that EVERY column has losing periods -- otherwise a column with no
    downside at all hits the zero-downside sentinel and the ordering under test would be a
    tie-break by index rather than a real ranking.
    """
    columns = []
    for value in values:
        column = [
            Decimal(str(value)) + (Decimal("10") if i % 2 else Decimal("-10")) for i in range(t)
        ]
        columns.append(column)
    return columns


def test_combination_count_is_c_16_8():
    assert cscv.combination_count(16) == 12870


def test_rejects_odd_s():
    with pytest.raises(ValueError, match="even"):
        cscv.pbo(_const_columns([1.0, 2.0]), s=7)


def test_perfectly_consistent_columns_give_pbo_zero():
    # Every block is identical, so column ordering is the same in every subsample and the
    # IS-best is always the OOS-best.
    result = cscv.pbo(_const_columns([1.0, 2.0, 3.0, 4.0, 5.0]), s=4)
    assert result.pbo == Decimal(0)


def test_pbo_is_deterministic():
    columns = _const_columns([1.0, 2.0, 3.0, 4.0, 5.0])
    assert cscv.pbo(columns, s=4).pbo == cscv.pbo(columns, s=4).pbo


def test_truncation_drops_oldest_rows():
    # 10 rows, s=4 -> 8 kept, the 2 OLDEST dropped.
    columns = [[Decimal(i) for i in range(10)], [Decimal(-i) for i in range(10)]]
    kept = cscv.truncate_to_blocks(columns, s=4)
    assert len(kept[0]) == 8
    assert kept[0][0] == Decimal(2)


def test_block_aggregate_sortino_matches_direct_computation():
    column = [Decimal(str(v)) for v in (1, -2, 3, -4, 5, -6, 7, -8)]
    aggregates = cscv.block_aggregates(column, s=4)
    total_n = sum(a[0] for a in aggregates)
    total_sum = sum((a[1] for a in aggregates), Decimal(0))
    total_dsq = sum((a[2] for a in aggregates), Decimal(0))

    fast = cscv.sortino_from_aggregates(total_n, total_sum, total_dsq)
    slow = cscv.sortino_series(column)
    assert fast == slow


def test_fast_path_matches_slow_path_on_the_same_metric():
    """The decomposition is an optimisation, not a different statistic."""
    columns = _const_columns([1.0, 2.0, 3.0, 4.0, 5.0], t=64)
    fast = cscv.pbo(columns, s=4)
    slow = cscv.pbo(columns, s=4, metric=cscv.sortino_series)
    assert fast.pbo == slow.pbo
    assert fast.logits == slow.logits


def _random_walk_columns(n: int, t: int, seed: int) -> list[list[Decimal]]:
    rng = random.Random(seed)
    return [[Decimal(str(round(rng.gauss(0, 1), 4))) for _ in range(t)] for _ in range(n)]


def test_power_replication_noise_versus_injected_signal():
    """§78.8's calibration: CSCV must have POWER, not merely conservatism.

    Pure noise should land near the paper's 0.55; the same matrix with a genuine signal
    injected into one column should drop sharply. An implementation that cannot separate
    these is wrong regardless of what it reports on the Turtle, so do NOT weaken these
    thresholds to make a failing implementation pass.
    """
    noise = _random_walk_columns(n=12, t=256, seed=1234)
    noise_pbo = cscv.pbo(noise, s=8).pbo

    signal = [list(column) for column in noise]
    # Give column 0 a persistent positive drift present in EVERY subsample.
    signal[0] = [value + Decimal("0.9") for value in signal[0]]
    signal_pbo = cscv.pbo(signal, s=8).pbo

    assert noise_pbo > Decimal("0.25")
    assert signal_pbo < Decimal("0.10")
    assert signal_pbo < noise_pbo


def test_result_exposes_no_configuration_field():
    """⛔ Strathern rail (spec §6): PBO may gate or report, never rank.

    If a caller could read the winning configuration out of a diagnostic run, CSCV becomes a
    selection tool -- the exact misuse §78.7 warns against. Guard it structurally so that
    adding such a field later fails the suite.
    """
    banned = {
        "best_config",
        "best_column",
        "best_index",
        "argmax",
        "selected",
        "params",
        "winner",
        "best_n",
        "n_star",
    }
    fields = set(cscv.PBOResult.__dataclass_fields__)
    assert not (fields & banned)
