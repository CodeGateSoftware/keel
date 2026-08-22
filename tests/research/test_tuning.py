"""Optuna parameter study harness (#476) — the guardrails are the load-bearing tests.

The acceptance test is `test_run_study_is_deterministic_under_a_fixed_seed`: a study that
cannot reproduce itself under a pinned seed is not evidence, it is noise with a citation.
Second is `test_importing_the_module_does_not_import_optuna`: the harness core must import
without the extra installed, because optuna rides the dev group and nothing runtime may
pull it. Third is `test_proposal_verdict_only_proposes_when_passed`: the refusal line is
the product — the sibling significance study already found no family distinguishable from
zero at the 120 bp fee, so this harness's honest expected output is "no candidate may be
proposed", and the tests pin that it stays sayable.
"""

from __future__ import annotations

import random
import subprocess
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from keel.agent import build_rule_from_params
from keel.research import cscv, tuning
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT
from keel.strategy.stats import BacktestResult
from keel.types import Candle

REPO_ROOT = Path(__file__).resolve().parents[2]


def _trending_candles(count: int, seed: int = 7) -> list[Candle]:
    """A deterministic always-up-with-pullbacks OHLCV walk on the hourly grid.

    The trend must be strong enough that a mid-range turtle breakout fires (close above the
    prior N-bar Donchian high with ADX above the gate) and the wiggles wide enough that ATR
    is non-zero, so stops and targets are real prices rather than degenerate ones.
    """
    rng = random.Random(seed)
    price = 100.0
    out: list[Candle] = []
    for index in range(count):
        close = price * (1.0 + rng.uniform(0.0, 0.012) + rng.uniform(-0.004, 0.004))
        # Brackets (~2%) deliberately WIDER than the per-bar move (~0.6%): the backtest
        # exits on a bar whose range SPANS the stop/target, so a series that gaps over
        # its own levels never closes a trade. Wide brackets make every crossed level a
        # touched one.
        high = close * (1 + rng.uniform(0.0, 0.02))
        low = close * (1 - rng.uniform(0.0, 0.02))
        out.append(
            Candle(
                ts=1_600_000_000 + index * 3_600,
                open=Decimal(f"{price:.2f}"),
                high=Decimal(f"{high:.2f}"),
                low=Decimal(f"{low:.2f}"),
                close=Decimal(f"{close:.2f}"),
                volume=Decimal("100"),
            )
        )
        price = close
    return out


class _PickSuggest:
    """Duck-typed optuna `Trial`: every suggestion resolves to the space's lo/mid/hi.

    Dispatches on the BOUNDS' types exactly the way the harness does -- integral bounds are
    suggested as ints, fractional ones as floats -- so a mid suggestion is (lo+hi)//2 for
    ints and (lo+hi)/2 for floats, and the exact-kwargs tests below can state expectations.
    """

    def __init__(self, pick: str) -> None:
        self._pick = pick

    def _choose(self, low: float, high: float) -> float:
        if self._pick == "lo":
            return low
        if self._pick == "hi":
            return high
        return (low + high) / 2

    def suggest_int(self, name: str, low: int, high: int) -> int:
        assert isinstance(low, int) and isinstance(high, int)
        return int(self._choose(low, high))

    def suggest_float(self, name: str, low: float, high: float) -> float:
        return float(self._choose(low, high))


class _TableSuggest:
    """Duck-typed optuna `Trial` that ignores the bounds and replays a fixed table.

    Used to feed suggestions OUTSIDE the pinned space (out-of-order EMAs) and pin that the
    harness clamps them deterministically rather than passing a fan the rule cannot trade.
    """

    def __init__(self, ints: dict[str, int], floats: dict[str, float]) -> None:
        self._ints = ints
        self._floats = floats

    def suggest_int(self, name: str, low: int, high: int) -> int:
        return self._ints[name]

    def suggest_float(self, name: str, low: float, high: float) -> float:
        return self._floats[name]


def _result(expectancy: Decimal, n_trades: int = 10) -> BacktestResult:
    """A `BacktestResult` standing in for a backtest, carrying only what the gate reads."""
    return BacktestResult(
        trades=[],
        n_trades=n_trades,
        win_rate=0.5,
        avg_win=Decimal("1"),
        avg_loss=Decimal("1"),
        expectancy=expectancy,
        profit_factor=Decimal("1"),
        max_drawdown=Decimal("0"),
        max_losing_streak=0,
        avg_mfe=Decimal("0"),
        avg_mae=Decimal("0"),
    )


def _noise_columns(n: int, t: int, seed: int) -> list[list[Decimal]]:
    """Pure-noise per-trial P&L columns -- `test_cscv.py`'s random-walk pattern."""
    rng = random.Random(seed)
    return [[Decimal(str(round(rng.gauss(0, 1), 4))) for _ in range(t)] for _ in range(n)]


def _consistent_columns(values: list[float], t: int = 32) -> list[list[Decimal]]:
    """Constant-drift columns -- `test_cscv.py`'s perfectly-consistent pattern (PBO 0)."""
    return [
        [Decimal(str(value)) + (Decimal("10") if i % 2 else Decimal("-10")) for i in range(t)]
        for value in values
    ]


# -- 1-3. SEARCH_SPACES integrity --------------------------------------------------------------


def test_search_spaces_pin_exactly_the_three_families() -> None:
    """The three tradable families with a stop (dca is out of scope -- it has none)."""
    assert set(tuning.SEARCH_SPACES) == {
        "turtle_breakout",
        "rsi_meanrev",
        "pullback_continuation",
    }
    for family, space in tuning.SEARCH_SPACES.items():
        assert 4 <= len(space) <= 7, family
        assert "granularity" not in space and "product_id" not in space, (
            f"{family}: the clock and the product are fixed by the caller, never searched"
        )


def test_search_space_bounds_are_well_formed() -> None:
    for family, space in tuning.SEARCH_SPACES.items():
        for name, bounds in space.items():
            assert isinstance(bounds, tuple) and len(bounds) == 2, (family, name)
            low, high = bounds
            assert low <= high, (family, name, bounds)
            if isinstance(low, int) and isinstance(high, int):
                assert low >= 1, (family, name, "lookbacks and periods must be positive")


def test_search_space_param_names_are_accepted_by_the_rule_constructors() -> None:
    """Mid-space params must construct each rule -- catches parameter-name drift.

    The rules' constructors are the ground truth; a search space naming a kwarg that no
    longer exists would explode only mid-study, 40 trials deep.
    """
    for family in tuning.SEARCH_SPACES:
        params = tuning.params_from_trial(family, _PickSuggest("mid"))
        rule = build_rule_from_params(family, {"product_id": "BTC-USD", **params})
        assert rule.product_id == "BTC-USD"
        assert rule.name == family


# -- 4-5. params_from_trial --------------------------------------------------------------------


@pytest.mark.parametrize("pick", ["lo", "mid", "hi"])
def test_params_from_trial_returns_the_exact_kwargs(pick: str) -> None:
    assert tuning.params_from_trial("turtle_breakout", _PickSuggest(pick)) == {
        "entry_lookback": {"lo": 20, "mid": 40, "hi": 60}[pick],
        "exit_lookback": {"lo": 10, "mid": 20, "hi": 30}[pick],
        "adx_threshold": {"lo": 20.0, "mid": 27.5, "hi": 35.0}[pick],
        "atr_stop_mult": {"lo": 1.5, "mid": 2.25, "hi": 3.0}[pick],
        "target_rr": {"lo": 3, "mid": 5, "hi": 8}[pick],
    }
    assert tuning.params_from_trial("rsi_meanrev", _PickSuggest(pick)) == {
        "oversold": {"lo": 15.0, "mid": 22.5, "hi": 30.0}[pick],
        "overbought": {"lo": 70.0, "mid": 77.5, "hi": 85.0}[pick],
        "atr_mult": {"lo": 1.0, "mid": 1.75, "hi": 2.5}[pick],
        "fixed_rr": {"lo": 1, "mid": 2, "hi": 3}[pick],
        "rsi_period": {"lo": 10, "mid": 15, "hi": 21}[pick],
    }
    assert tuning.params_from_trial("pullback_continuation", _PickSuggest(pick)) == {
        "ema_periods": {
            "lo": (5, 15, 40),
            "mid": (8, 22, 55),
            "hi": (12, 30, 70),
        }[pick],
        "buffer_ticks": {"lo": 0.01, "mid": (0.01 + 0.05) / 2, "hi": 0.05}[pick],
    }


def test_params_from_trial_rejects_an_unknown_family() -> None:
    with pytest.raises(ValueError, match="dca"):
        tuning.params_from_trial("dca", _PickSuggest("mid"))


def test_params_from_trial_clamps_out_of_order_ema_suggestions() -> None:
    """The EMA fan must stay strictly ordered (fast < mid < slow) whatever was suggested.

    The pinned spaces are disjoint so an in-space suggestion can never invert the fan; the
    clamp exists so the invariant survives a future edit of the ranges, and does so by
    DETERMINISTIC clamping (no re-suggest, no raise) so the study stays reproducible.
    """
    wild = _TableSuggest(
        ints={"ema_fast": 60, "ema_mid": 10, "ema_slow": 50}, floats={"buffer_ticks": 0.02}
    )
    params = tuning.params_from_trial("pullback_continuation", wild)
    fast, mid, slow = params["ema_periods"]
    assert (fast, mid, slow) == (9, 10, 50)
    assert fast < mid < slow

    flat = _TableSuggest(
        ints={"ema_fast": 30, "ema_mid": 30, "ema_slow": 30}, floats={"buffer_ticks": 0.02}
    )
    fast, mid, slow = tuning.params_from_trial("pullback_continuation", flat)["ema_periods"]
    assert fast < mid < slow


# -- 6. split_chronologically ------------------------------------------------------------------


def test_split_chronologically_splits_in_ts_order_without_overlap() -> None:
    candles = _trending_candles(10, seed=3)
    train, test = tuning.split_chronologically(list(reversed(candles)), Decimal("0.7"))
    assert len(train) == 7 and len(test) == 3
    assert [c.ts for c in train] == sorted(c.ts for c in train)
    assert [c.ts for c in test] == sorted(c.ts for c in test)
    assert max(c.ts for c in train) < min(c.ts for c in test)  # no overlap, no shared bar
    assert train + test == sorted(candles, key=lambda c: c.ts)  # nothing dropped or doubled
    assert tuning.split_chronologically([], Decimal("0.7")) == ([], [])
    with pytest.raises(ValueError, match="train_frac"):
        tuning.split_chronologically(candles, Decimal("1.5"))


# -- 7. evaluate_params ------------------------------------------------------------------------


def test_evaluate_params_runs_a_real_backtest_and_turtle_finds_trades() -> None:
    candles = _trending_candles(600, seed=11)
    result = tuning.evaluate_params(
        "turtle_breakout",
        "BTC-USD",
        {
            "granularity": "ONE_HOUR",
            **tuning.params_from_trial("turtle_breakout", _PickSuggest("mid")),
        },
        candles,
        fee_pct=TAKER_FEE_PCT,
        slippage_pct=SLIPPAGE_FLOOR_PCT,
    )
    assert isinstance(result, BacktestResult)
    assert isinstance(result.expectancy, Decimal)  # fee-adjusted per-trade objective
    assert result.n_trades >= 1, "a trending series must give the mid-space turtle a trade"


# -- 8-9. the overfitting gate -----------------------------------------------------------------


def test_gate_fails_when_held_out_expectancy_is_not_positive() -> None:
    gate = tuning.evaluate_gate(
        _result(Decimal("0.05")),
        _result(Decimal("-0.01")),
        _consistent_columns([1.0, 2.0, 3.0]),
    )
    assert gate.held_out_positive is False
    assert gate.pbo == Decimal(0)  # consistent columns: the pbo term is not the blocker
    assert gate.passed is False
    assert any("held-out" in failure for failure in gate.failures)


def test_gate_pbo_is_wired_to_cscv_and_noise_does_not_pass() -> None:
    columns = _noise_columns(n=12, t=256, seed=1234)
    gate = tuning.evaluate_gate(_result(Decimal("0.05")), _result(Decimal("0.05")), columns)
    # The pbo is CSCV's own number on the same columns, not a reimplementation.
    assert gate.pbo == cscv.pbo(columns, s=8).pbo
    assert gate.pbo > Decimal("0.25")  # the test_cscv calibration: luck reads as luck
    assert gate.passed is (gate.pbo <= Decimal("0.5"))


def test_gate_refuses_a_positive_held_out_window_when_pbo_exceeds_half() -> None:
    """The pbo-only refusal, pinned on columns asserted to be over the ceiling FIRST.

    The held-out window is positive, so the refusal can only come from the overfitting
    term; asserting the COMPUTED pbo > 0.5 before checking the refusal keeps the test from
    ever silently drifting into the pass region and vacuously "passing".
    """
    # The same seeded noise `test_cscv.py` calibrates as luck: pbo 43/70 = 0.6142...,
    # deterministic in exact Decimal, comfortably over the ceiling.
    columns = _noise_columns(n=12, t=256, seed=1234)
    gate = tuning.evaluate_gate(_result(Decimal("0.05")), _result(Decimal("0.05")), columns)
    assert gate.held_out_positive is True  # the held-out window is NOT the blocker
    assert gate.pbo is not None and gate.pbo > Decimal("0.5")
    assert gate.passed is False
    assert any(failure.startswith(f"pbo {gate.pbo} >") for failure in gate.failures)


def test_gate_passes_consistent_columns_with_a_positive_held_out_window() -> None:
    gate = tuning.evaluate_gate(
        _result(Decimal("0.05")),
        _result(Decimal("0.02")),
        _consistent_columns([1.0, 2.0, 3.0, 4.0]),
    )
    assert gate.passed is True
    assert gate.failures == ()


def test_gate_skips_trials_with_too_few_trades_and_refuses_without_cscv_input() -> None:
    """Columns under the 10-trade minimum are excluded; fewer than 2 usable -> no proposal.

    A PBO computed over 3-trade columns is a number about nothing; the gate must rather say
    it could not certify overfitting than invent a certificate.
    """
    good = _consistent_columns([1.0, 2.0, 3.0])
    thin = [[Decimal("1")] * 3]  # 3 closed trades: below the floor
    gate = tuning.evaluate_gate(_result(Decimal("0.05")), _result(Decimal("0.05")), good + thin)
    assert gate.n_columns_used == 3
    assert gate.n_columns_skipped == 1

    starved = tuning.evaluate_gate(
        _result(Decimal("0.05")), _result(Decimal("0.05")), [thin[0], thin[0]]
    )
    assert starved.pbo is None
    assert starved.passed is False
    assert any("pbo unavailable" in failure for failure in starved.failures)


def test_gate_treats_columns_too_short_for_the_blocks_as_pbo_unavailable() -> None:
    """Columns too short to cut `s` blocks must REFUSE, not let `cscv.pbo` raise.

    With the pinned defaults (10-trade floor, s=8) the trade floor already covers the
    block count; the guard matters the moment a caller raises `pbo_blocks` (cscv's own
    default is s=16) and the shortest usable column cannot supply the rows.
    """
    # 3 columns of 12 trades each: above the 10-trade floor (so "usable"), under s=16
    # (so `truncate_to_blocks` would keep 0 rows and cscv would raise without the guard).
    stubby = _consistent_columns([1.0, 2.0, 3.0], t=12)
    gate = tuning.evaluate_gate(_result(Decimal("0.05")), _result(Decimal("0.05")), stubby, s=16)
    assert gate.n_columns_used == 3
    assert gate.pbo is None
    assert gate.passed is False
    assert any("pbo unavailable" in failure for failure in gate.failures)


# -- 10. the refusal ---------------------------------------------------------------------------


def test_proposal_verdict_only_proposes_when_the_gate_passes() -> None:
    passed = tuning.evaluate_gate(
        _result(Decimal("0.05")),
        _result(Decimal("0.02")),
        _consistent_columns([1.0, 2.0, 3.0, 4.0]),
    )
    proposing = tuning.proposal_verdict("turtle_breakout", "BTC-USD", passed)
    assert proposing.startswith("PROPOSE as candidate")

    refused = tuning.evaluate_gate(
        _result(Decimal("0.05")),
        _result(Decimal("-0.01")),
        _consistent_columns([1.0, 2.0, 3.0]),
    )
    rejection = tuning.proposal_verdict("turtle_breakout", "BTC-USD", refused)
    assert rejection.startswith("no candidate may be proposed")
    assert "held-out" in rejection
    assert "-0.01" in rejection  # the numbers ride with the refusal

    uncertified = tuning.evaluate_gate(
        _result(Decimal("0.05")), _result(Decimal("0.05")), [[Decimal("1")] * 3]
    )
    assert "no candidate may be proposed" in tuning.proposal_verdict(
        "rsi_meanrev", "ETH-USD", uncertified
    )


# -- 11. the pinned kwargs must win the merge ---------------------------------------------------


def test_run_study_fixed_params_shadow_a_colliding_searched_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fixed param beats a searched name on collision; the pin is what reaches the rule.

    The searched side is fed by the table-backed fake suggestor producing an
    `entry_lookback` that COLLIDES with the pin, `params_from_trial`/`evaluate_params` are
    stubbed so the capture is exactly the kwargs each trial trades, and the assertion is
    about those captured kwargs -- not about dict order by proxy.
    """
    searched = tuning.params_from_trial(
        "turtle_breakout",
        _TableSuggest(
            ints={"entry_lookback": 20, "exit_lookback": 10, "target_rr": 3},
            floats={"adx_threshold": 20.0, "atr_stop_mult": 1.5},
        ),
    )
    assert searched["entry_lookback"] == 20  # the searched value the pin must beat
    monkeypatch.setattr(tuning, "params_from_trial", lambda family, suggest: searched)

    seen: list[dict[str, object]] = []

    def fake_evaluate_params(
        rule_kind: str,
        product_id: str,
        params: dict[str, object],
        candles: Sequence[Candle],
        fee_pct: Decimal,
        slippage_pct: Decimal,
    ) -> BacktestResult:
        seen.append(dict(params))
        return _result(Decimal("0.01"))

    monkeypatch.setattr(tuning, "evaluate_params", fake_evaluate_params)

    report = tuning.run_study(
        "turtle_breakout",
        "BTC-USD",
        _trending_candles(50),
        n_trials=1,
        seed=1,
        fixed_params={"granularity": "ONE_HOUR", "entry_lookback": 55},
    )
    assert seen, "the objective must have run"
    assert all(row["entry_lookback"] == 55 for row in seen)  # the pin, not the search
    assert all(row["granularity"] == "ONE_HOUR" for row in seen)
    assert report.best_params["entry_lookback"] == 55  # the recorded winner carries the pin


# -- 12-13. determinism: the acceptance tests ---------------------------------------------------


def test_run_study_is_deterministic_under_a_fixed_seed() -> None:
    candles = _trending_candles(600, seed=11)
    kwargs: dict[str, object] = {
        "rule_kind": "turtle_breakout",
        "product_id": "BTC-USD",
        "candles": candles,
        "fixed_params": {"granularity": "ONE_HOUR"},
        "n_trials": 15,
    }
    first = tuning.run_study(**kwargs, seed=7)  # type: ignore[arg-type]
    second = tuning.run_study(**kwargs, seed=7)  # type: ignore[arg-type]

    assert first.best_params == second.best_params
    assert first.best_train_expectancy == second.best_train_expectancy
    # The WHOLE re-priced winner, not just its expectancy: n_trades, win_rate, drawdown,
    # every Trade -- identical inputs must reproduce the identical held-out backtest.
    assert first.held_out_result == second.held_out_result
    assert first.held_out_result.n_trades == second.held_out_result.n_trades
    assert first.gate == second.gate
    assert len(first.trials) == len(second.trials) == 15
    assert [t.params for t in first.trials] == [t.params for t in second.trials]
    assert [t.train_expectancy for t in first.trials] == [t.train_expectancy for t in second.trials]


def test_run_study_different_seed_explores_a_different_sequence() -> None:
    candles = _trending_candles(600, seed=11)
    base: dict[str, object] = {
        "rule_kind": "turtle_breakout",
        "product_id": "BTC-USD",
        "candles": candles,
        "fixed_params": {"granularity": "ONE_HOUR"},
        "n_trials": 15,
    }
    seed_a = tuning.run_study(**base, seed=7)  # type: ignore[arg-type]
    seed_b = tuning.run_study(**base, seed=8)  # type: ignore[arg-type]
    # TPE's startup trials are drawn from the sampler's own seeded RNG, so a different seed
    # must walk a different path through the space. If this ever fails, the sampler stopped
    # being seed-sensitive and the determinism guarantee above became vacuous.
    assert [t.params for t in seed_a.trials] != [t.params for t in seed_b.trials]


# -- 14-15. the optional dependency stays optional ----------------------------------------------


def test_importing_the_module_does_not_import_optuna() -> None:
    """The harness core is importable without the extra: optuna loads lazily, if ever."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, keel.research.tuning; assert 'optuna' not in sys.modules",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_run_study_names_the_extra_when_optuna_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "optuna", None)  # blocks the lazy import
    with pytest.raises(RuntimeError, match="optuna"):
        tuning.run_study(
            "turtle_breakout",
            "BTC-USD",
            _trending_candles(50),
            n_trials=1,
            seed=1,
        )
