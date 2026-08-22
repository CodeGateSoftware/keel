"""Rolling-origin walk-forward validation (#445) -- the pure module and its CLI front-end.

The design constraint these tests pin: a walk-forward validator validates ONE GIVEN parameter
set across folds and reports stability. It never selects among folds, windows or parameter
sets -- a validator that reported a winning window would reintroduce exactly the ranking the
Strathern rail (spec §6, `keel/research/cscv.py`) exists to forbid.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

import keel.research.walkforward as wf
from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.research import ledger as trials_ledger
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

# -- fixtures: candles whose every trade is hand-computable ---------------------------------------
#
# `FixedBandRule` enters on every bar the engine offers it, at a stop 10 below and a target
# 20 above the signal close. On a RISING bar (range spanning the target, never the stop) every
# trade closes at +20 exactly; on a FALLING bar (range spanning the stop, never the target)
# every trade closes at -10 exactly; a "gap" between one close and the next open shifts the
# fill and so the pnl by exactly that gap. Fees and slippage are pinned to zero in every pure
# module test so those values are exact Decimals, hand-computable from the candle arithmetic.


class FixedBandRule(Rule):
    """Enters whenever flat: stop 10 below the last close, target 20 above it."""

    def __init__(self, stop_dist: str = "10", target_dist: str = "20") -> None:
        self.name = "fixed_band"
        self.params = {"stop_dist": stop_dist, "target_dist": target_dist}
        self.product_id = "TEST-USD"
        self._stop = Decimal(stop_dist)
        self._target = Decimal(target_dist)

    def detect(self, candles_by_tf) -> Setup | None:  # type: ignore[no-untyped-def]
        candles = candles_by_tf.get(Granularity.ONE_HOUR) or list(candles_by_tf.values())[-1]
        last = candles[-1]
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=last.close,
            stop=last.close - self._stop,
            target=last.close + self._target,
            context={},
            ts=last.ts,
        )

    def exit_signal(self, held, candles_by_tf) -> bool:  # type: ignore[no-untyped-def]
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class NeverEnterRule(FixedBandRule):
    """A rule that never fires -- the zero-trades-everywhere refusal shape."""

    def __init__(self) -> None:
        super().__init__()
        self.name = "never_enter"

    def detect(self, candles_by_tf) -> Setup | None:  # type: ignore[no-untyped-def]
        return None


def _rising(n: int, *, ts0: int = 1_700_000_000, p0: Decimal = Decimal(100), gap: int = 0):
    """`n` bars where every FixedBandRule trade closes at exactly `20 - gap`.

    close = open + 1, high = open + 30 (target reachable), low = open - 1 (stop never
    touched); each bar opens at the previous close plus `gap`."""
    bars = []
    p = p0
    for i in range(n):
        bars.append(
            Candle(
                ts=ts0 + i * 86400,
                open=p,
                high=p + 30,
                low=p - 1,
                close=p + 1,
                volume=Decimal(10),
            )
        )
        p = p + 1 + gap
    return bars


def _falling(n: int, *, ts0: int, p0: Decimal):
    """`n` bars where every FixedBandRule trade closes at exactly -10: the bar's range
    spans the stop and never reaches the target."""
    bars = []
    p = p0
    for i in range(n):
        bars.append(
            Candle(
                ts=ts0 + i * 86400,
                open=p,
                high=p + 1,
                low=p - 30,
                close=p - 1,
                volume=Decimal(10),
            )
        )
        p = p - 1
    return bars


#: Convenience: fold geometry reused across the aggregate tests. train=10, test=10,
#: default step=10 gives non-overlapping folds advancing one test window at a time.
_TRAIN, _TEST = 10, 10


# -- folds(): the rolling-origin geometry ---------------------------------------------------------


def test_folds_hand_case_exact_bounds():
    """100 bars, train 30, test 20, default step: starts 0/20/40 and nothing else."""
    out = wf.folds(100, train_bars=30, test_bars=20)
    assert out == [
        wf.FoldBounds(train_start=0, train_end=30, test_start=30, test_end=50),
        wf.FoldBounds(train_start=20, train_end=50, test_start=50, test_end=70),
        wf.FoldBounds(train_start=40, train_end=70, test_start=70, test_end=90),
    ]


def test_folds_default_step_is_test_bars():
    """Step omitted == step = test_bars: the non-overlapping-tests default."""
    assert wf.folds(50, train_bars=20, test_bars=10) == wf.folds(
        50, train_bars=20, test_bars=10, step_bars=10
    )


def test_folds_overlapping_step_advances_both_windows_by_step():
    """step < test_bars: consecutive TEST windows overlap; both windows advance by step."""
    out = wf.folds(40, train_bars=10, test_bars=10, step_bars=5)
    assert len(out) == 5
    assert out[0] == wf.FoldBounds(0, 10, 10, 20)
    assert out[1] == wf.FoldBounds(5, 15, 15, 25)
    assert out[4] == wf.FoldBounds(20, 30, 30, 40)
    # overlap is real: fold 1's test window re-covers bars fold 0 already tested.
    assert out[1].test_start < out[0].test_end


def test_folds_last_fold_never_exceeds_n_bars():
    """Whatever the geometry, test_end <= n_bars and train_end == test_start always."""
    for train, test, step in ((7, 3, 2), (10, 10, 10), (5, 11, 4), (1, 1, 1), (12, 5, 20)):
        for bounds in wf.folds(37, train_bars=train, test_bars=test, step_bars=step):
            assert bounds.train_start >= 0
            assert bounds.train_end == bounds.test_start
            assert bounds.test_end <= 37


def test_folds_rejects_impossible_geometry():
    import pytest

    with pytest.raises(ValueError, match="train_bars"):
        wf.folds(100, train_bars=0, test_bars=10)
    with pytest.raises(ValueError, match="test_bars"):
        wf.folds(100, train_bars=10, test_bars=-1)
    with pytest.raises(ValueError, match="step_bars"):
        wf.folds(100, train_bars=10, test_bars=10, step_bars=0)
    with pytest.raises(ValueError, match="n_bars"):
        wf.folds(0, train_bars=10, test_bars=10)
    # The message must NAME the windows against the available bars, not just refuse.
    with pytest.raises(ValueError, match="90.*exceeds.*50"):
        wf.folds(50, train_bars=80, test_bars=10)


# -- walk_forward(): per-fold metrics on synthetic candles -----------------------------------------


def test_walk_forward_per_fold_metrics_are_present_and_exact():
    """All-rising candles: every fold's test side closes 5 trades at exactly +20 each."""
    candles = _rising(40)
    bounds = wf.folds(40, train_bars=_TRAIN, test_bars=_TEST)
    report = wf.walk_forward(
        FixedBandRule(),
        candles,
        folds_bounds=bounds,
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    assert report.n_folds == 3 == len(report.fold_metrics)
    for m in report.fold_metrics:
        assert m.test_n_trades == 5
        assert m.test_expectancy == Decimal(20)
        assert m.test_win_rate == Decimal(1)
        assert m.test_max_drawdown == Decimal(0)
        assert m.test_trade_pnl == (Decimal(20),) * 5
        assert m.train_n_trades > 0  # the honesty table has an in-sample side too
        assert m.train_expectancy == Decimal(20)


def test_walk_forward_is_deterministic():
    """No randomness anywhere: two runs over the same inputs are equal reports."""
    candles = _rising(30) + _falling(10, ts0=1_700_000_000 + 30 * 86400, p0=Decimal(130))
    bounds = wf.folds(40, train_bars=_TRAIN, test_bars=_TEST)
    kwargs = {"folds_bounds": bounds, "fee_pct": Decimal(0), "slippage_pct": Decimal(0)}
    assert wf.walk_forward(FixedBandRule(), candles, **kwargs) == wf.walk_forward(
        FixedBandRule(), candles, **kwargs
    )


def test_walk_forward_test_trades_stay_inside_their_test_window():
    """No leakage: every test trade ENTERS inside its own test window, and the train run
    never records a trade entered outside the train window it reports against."""
    candles = _rising(40)
    bounds = wf.folds(40, train_bars=_TRAIN, test_bars=_TEST)
    report = wf.walk_forward(
        FixedBandRule(),
        candles,
        folds_bounds=bounds,
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    for b, m in zip(bounds, report.fold_metrics):
        assert b.train_end == b.test_start  # windows disjoint by construction
        assert b.test_end <= len(candles)
        assert m.test_n_trades == len(m.test_trade_pnl)
        # the pooled series cannot reach outside the fold's own test trades
        assert len(m.test_trade_pnl) == m.test_n_trades


# -- aggregates: exact medians and the pinned odd-fold halves rule ---------------------------------


def test_aggregates_even_median_is_exact():
    """Two folds, expectancies +20 then +18 (a 2-bar open gap shifts every fill): the
    even-count median is the exact mean of the two middles -- Decimal 19, not 19.0-ish."""
    # region A (bars 0..19, gap 0): +20 trades; region B (bars 20..29, gap 2): +18 trades.
    candles = _rising(20) + _rising(10, ts0=1_700_000_000 + 20 * 86400, p0=Decimal(120), gap=2)
    bounds = wf.folds(30, train_bars=_TRAIN, test_bars=_TEST)
    report = wf.walk_forward(
        FixedBandRule(),
        candles,
        folds_bounds=bounds,
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    assert report.n_folds == 2
    assert [m.test_expectancy for m in report.fold_metrics] == [Decimal(20), Decimal(18)]
    assert report.median_test_expectancy == Decimal(19)  # (20 + 18) / 2, exact
    assert report.n_folds_test_positive == 2
    assert report.early_half_median == Decimal(20)
    assert report.late_half_median == Decimal(18)
    assert report.degradation == Decimal(-2)


def test_aggregates_odd_folds_exclude_the_middle_fold():
    """PINNED RULE, documented in the module: with an odd fold count the MIDDLE fold joins
    neither half, so [+20, +20, -10] compares fold 0 against fold 2 -- early median +20,
    late median -10, degradation exactly -30. (Middle-in-late-half would give +5 instead;
    this test fails if that convention ever creeps in.)"""
    candles = _rising(30) + _falling(10, ts0=1_700_000_000 + 30 * 86400, p0=Decimal(130))
    bounds = wf.folds(40, train_bars=_TRAIN, test_bars=_TEST)
    report = wf.walk_forward(
        FixedBandRule(),
        candles,
        folds_bounds=bounds,
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    assert report.n_folds == 3
    assert [m.test_expectancy for m in report.fold_metrics] == [
        Decimal(20),
        Decimal(20),
        Decimal(-10),
    ]
    assert report.n_folds_test_positive == 2
    assert report.early_half_median == Decimal(20)
    assert report.late_half_median == Decimal(-10)
    assert report.degradation == Decimal(-30)


def test_single_fold_degradation_is_honestly_uncomputable():
    """One fold has no halves to compare: degradation is None, and the note says so
    instead of inventing a number."""
    candles = _rising(20)
    bounds = wf.folds(20, train_bars=_TRAIN, test_bars=_TEST)
    assert len(bounds) == 1
    report = wf.walk_forward(
        FixedBandRule(),
        candles,
        folds_bounds=bounds,
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    assert report.early_half_median is None
    assert report.late_half_median is None
    assert report.degradation is None
    assert "not computable" in report.stability_note


def test_walk_forward_refuses_zero_folds():
    import pytest

    with pytest.raises(ValueError, match="fold"):
        wf.walk_forward(FixedBandRule(), _rising(30), folds_bounds=[], fee_pct=Decimal(0))


# -- the refusal to rank, stated and enforced -----------------------------------------------------


def test_stability_note_and_render_describe_never_prescribe():
    """The note reports what happened ("positive in 2/3 folds; late-half median below the
    early half"); neither it nor the rendered table may contain comparative wording that
    points a reader at a fold, window or parameter set."""
    candles = _rising(30) + _falling(10, ts0=1_700_000_000 + 30 * 86400, p0=Decimal(130))
    report = wf.walk_forward(
        FixedBandRule(),
        candles,
        folds_bounds=wf.folds(40, train_bars=_TRAIN, test_bars=_TEST),
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    assert "positive in 2/3 folds" in report.stability_note
    assert "below the early half" in report.stability_note

    rendered = "\n".join(wf.render_lines(report)).lower()
    # the ALWAYS-statement: one parameter set, validation not ranking.
    assert "one parameter set" in rendered
    assert "does not rank" in rendered
    for word in ("best", "prefer", "recommend", "optimal", "winner", "superior", "chosen"):
        assert word not in rendered, word


def test_refusal_to_rank_enforced_by_source_scan():
    """The Strathern rail, mechanically: the module's own source may not contain ranking
    shapes -- no keyed sort (a keyed sort is a selection), no in-place sort, and none of
    the vocabulary of selection. Ordering for a MEDIAN (`sorted(values)` with no key) is
    allowed: a median is an aggregate, not a winner."""
    source = Path(wf.__file__).read_text(encoding="utf-8")
    assert "key=lambda" not in source
    assert ".sort(" not in source
    lowered = source.lower()
    for word in ("best", "winner", "optimal"):
        assert word not in lowered, word


def test_report_dataclass_has_no_selection_fields():
    names = {f.name for f in dataclasses.fields(wf.WalkForwardReport)}
    assert not any(name.startswith("best") for name in names), names
    assert not any("select" in name or "chosen" in name for name in names), names
    # what the report DOES carry: the given set's descriptor and per-fold test metrics
    for required in (
        "rule_name",
        "n_folds",
        "fold_metrics",
        "median_test_expectancy",
        "n_folds_test_positive",
        "degradation",
        "stability_note",
    ):
        assert required in names


# -- render: the report-only window-size guidance from deflate ------------------------------------


def test_render_carries_report_only_deflate_guidance_when_data_present():
    """Mixed-sign positive-mean candles make the guidance computable: the render states
    min_trades at the observed Sharpe/trade frequency and MinBTL in years, framed as a
    stopping-rule question about window SIZE -- report-only, never a gate."""
    candles = _rising(50) + _falling(20, ts0=1_700_000_000 + 50 * 86400, p0=Decimal(150))
    report = wf.walk_forward(
        FixedBandRule(),
        candles,
        folds_bounds=wf.folds(70, train_bars=_TRAIN, test_bars=_TEST),
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    rendered = "\n".join(wf.render_lines(report)).lower()
    assert "window-size guidance" in rendered
    assert "report-only" in rendered
    assert "minbtl" in rendered
    assert "min trades" in rendered
    assert "trades/year" in rendered


def test_render_says_not_computable_when_no_test_trades():
    """Honest MISSING, not a plausible default: a rule that never trades gets a refusal
    line, exactly as `trials deflate` refuses to invent a DSR."""
    report = wf.walk_forward(
        NeverEnterRule(),
        _rising(30),
        folds_bounds=wf.folds(30, train_bars=_TRAIN, test_bars=_TEST),
        fee_pct=Decimal(0),
        slippage_pct=Decimal(0),
    )
    rendered = "\n".join(wf.render_lines(report)).upper()
    assert "NOT COMPUTABLE" in rendered
    assert "MINBTL:" not in rendered


# -- CLI: `keel trials walk-forward` --------------------------------------------------------------


def _cli_candles(n: int) -> list[Candle]:
    """`n` daily bars in a 19-bar sawtooth (8-bar rally, 9-bar crash, 2-bar drift) so a
    small-lookback turtle rule enters AND gets stopped out within 10-bar test windows."""
    candles = []
    price = Decimal(100)
    for i in range(n):
        phase = i % 19
        if phase < 8:
            price += Decimal(4)
        elif phase < 17:
            price -= Decimal(9)
        else:
            price -= Decimal(1)
        open_ = price
        close = price + (Decimal("1.5") if i % 2 else Decimal("-1.5"))
        candles.append(
            Candle(
                ts=1_700_000_000 + i * 86400,
                open=open_,
                high=max(open_, close) + Decimal(1),
                low=min(open_, close) - Decimal(1),
                close=close,
                volume=Decimal("10"),
            )
        )
    return candles


def _wf_db(tmp_path: Path, *, candles: bool) -> Path:
    """A temp db holding one small-lookback turtle rule and (optionally) its candles."""
    conn = connect(str(tmp_path / "wf.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout",
        {
            "product_id": "BTC-USD",
            "entry_lookback": 5,
            "exit_lookback": 3,
            "atr_period": 5,
            "atr_stop_mult": "2",
        },
        status="candidate",
        now_ts=1_800_000_000,
    )
    if candles:
        repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _cli_candles(96))
    conn.close()
    return tmp_path / "wf.db"


def _invoke_wf(runner: CliRunner, db: Path, ledger: Path, *extra: str):
    """One `trials walk-forward` invocation against a real (if tiny) db; `--config` points
    at a missing path so the fee degrades to the library default rather than loading
    whatever deployment config surrounds the test run."""
    return runner.invoke(
        cli,
        [
            "--db",
            str(db),
            "--config",
            str(db.parent / "missing.yaml"),
            "trials",
            "walk-forward",
            "--rule",
            "1",
            "--ledger",
            str(ledger),
            *extra,
        ],
    )


def test_cli_help_lists_the_knobs_and_pins_the_absence_of_a_seed():
    result = CliRunner().invoke(cli, ["trials", "walk-forward", "--help"])
    assert result.exit_code == 0
    for needle in (
        "--rule",
        "--train-bars",
        "--test-bars",
        "--step-bars",
        "--granularity",
        "--ledger",
        "--session",
    ):
        assert needle in result.output
    # Deterministic by construction: nothing samples, so nothing takes a seed.
    assert "--seed" not in result.output


def test_cli_appends_exactly_one_row_per_fold_and_the_chain_verifies(tmp_path):
    db = _wf_db(tmp_path, candles=True)
    ledger = tmp_path / "trials.jsonl"
    result = _invoke_wf(CliRunner(), db, ledger, "--train-bars", "20", "--test-bars", "10")
    assert result.exit_code == 0, result.output
    assert "does not rank" in result.output
    assert "fee_pct" in result.output  # every printed number travels with its fee

    # 96 bars, train 20 + test 10 advancing by 10: folds at starts 0..66 -> exactly 7.
    rows = trials_ledger.read_trials(ledger)
    assert len(rows) == 7
    assert {r.kind for r in rows} == {"walk_forward"}
    assert {r.decision for r in rows} == {"diagnostic_only"}  # measurement, never a gate
    assert {r.provenance for r in rows} == {"a_priori"}
    assert len({r.trial_id for r in rows}) == 7  # one row per fold, ids never collide
    first, last = rows[0], rows[-1]
    assert first.params["train_start"] == 0 and first.params["test_end"] == 30
    assert last.params["train_start"] == 60 and last.params["test_end"] == 90
    assert first.params["granularity"] == "ONE_DAY"
    assert first.summary["test_n_trades"] >= 0  # fold metrics ride the summary
    assert "median_test_expectancy" in first.summary
    assert trials_ledger.verify_chain(ledger) == []


def test_cli_refusals_write_no_rows(tmp_path):
    db = _wf_db(tmp_path, candles=True)
    ledger = tmp_path / "trials.jsonl"

    # window larger than the cached series: ValueError -> ClickException, no rows.
    too_big = _invoke_wf(CliRunner(), db, ledger, "--train-bars", "90", "--test-bars", "90")
    assert too_big.exit_code != 0
    assert "exceeds" in too_big.output
    assert not ledger.exists() or trials_ledger.read_trials(ledger) == []

    # no candles cached: nothing to fold over.
    bare = tmp_path / "bare"
    bare.mkdir()
    empty_db = _wf_db(bare, candles=False)
    no_candles = _invoke_wf(
        CliRunner(), empty_db, ledger, "--train-bars", "20", "--test-bars", "10"
    )
    assert no_candles.exit_code != 0
    assert "no candles" in no_candles.output
    assert not ledger.exists() or trials_ledger.read_trials(ledger) == []
