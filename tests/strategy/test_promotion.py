"""Tests for keel.strategy.promotion: the promotion/demotion lifecycle (P2 Task 9).

Builds `BacktestResult` fixtures directly (this module must not depend on `backtest.py`'s
simulation internals, `paper.py`, or `engine.py` — it only consumes the `BacktestResult`
shape, per the Phase 2 plan) and exercises the gates + DB-backed `transition()` against a
real in-memory `Repository`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_core.config import ResearchConfig

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.research.cscv import PBOResult
from keel.strategy.backtest import BacktestResult
from keel.strategy.promotion import (
    DEFAULT_CLASS,
    FAILED,
    NOT_RUN,
    PASSED,
    TREND_FOLLOW,
    PBOGate,
    PromotionConfig,
    can_promote,
    check_floors,
    floor_for_class,
    g4_pbo_gate,
    next_status,
    pbo_gate_from_config,
    promotion_class_of,
    should_demote,
    transition,
)


def _stats(
    n_trades: int = 150,
    win_rate: float = 0.6,
    avg_win: Decimal = Decimal("30"),
    avg_loss: Decimal = Decimal("-10"),
    expectancy: Decimal = Decimal("14"),
) -> BacktestResult:
    """A `BacktestResult` fixture with sane, independently-overridable fields.

    Defaults comfortably clear the default `PromotionConfig` floors (rr = 30/10 = 3.0 >=
    1.5, win_rate 0.6 >= 0.55, expectancy 14 > 0, n_trades 150 >= 100).
    """
    return BacktestResult(
        trades=[],
        n_trades=n_trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=Decimal("2"),
        max_drawdown=Decimal("50"),
        max_losing_streak=4,
        avg_mfe=Decimal("20"),
        avg_mae=Decimal("8"),
    )


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _insert_rule(repo: Repository, kind: str, status: str = "candidate") -> int:
    cursor = repo._conn.execute(
        "INSERT INTO rules (kind, params, status, created_at) VALUES (?, ?, ?, ?)",
        (kind, "{}", status, 1_700_000_000),
    )
    repo._conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _rule_status(repo: Repository, rule_id: int) -> str:
    row = repo._conn.execute("SELECT status FROM rules WHERE id = ?", (rule_id,)).fetchone()
    assert row is not None
    return row["status"]


def _pbo(pbo: str = "0.01", slope: str = "-0.2") -> PBOResult:
    """A minimal `PBOResult` carrying only the two fields G4 reads.

    Built directly rather than by running `cscv.pbo` over synthetic columns: this module tests
    the GATE, and a real CSCV run would make these tests depend on the estimator's behaviour
    instead of on the threshold logic. `tests/research/test_cscv.py` owns the estimator.
    """
    return PBOResult(
        pbo=Decimal(pbo),
        n_combinations=20,
        n_columns=12,
        n_blocks=16,
        rows_used=800,
        rows_dropped=0,
        logits=[],
        is_performance=[],
        oos_performance=[],
        degradation_slope=Decimal(slope),
    )


# -- the overfitting axis: an ABSENT check must never read as a pass -----------


def test_can_promote_refuses_when_no_overfitting_evidence_was_supplied() -> None:
    """Clean on all four floors, but nobody ran CSCV -- so this is NOT a promotion.

    The whole point of #247. `research: pbo_max/slope_floor` shipped in every config and
    `g4_pbo_gate` shipped in this module, and nothing called either: promotion was decided on
    four performance floors while the overfitting gate sat dormant. A rule that clears
    expectancy/RR/win-rate on one in-sample parameter set is exactly the thing PBO exists to be
    suspicious of, so "we never checked" has to block, not wave through.
    """
    decision = can_promote(_stats(), PromotionConfig())

    assert decision.promotable is False
    assert decision.floors_pass is True  # the floors themselves were fine
    assert decision.overfitting == NOT_RUN
    assert any("not run" in r.lower() for r in decision.reasons)


def test_can_promote_passes_when_floors_and_the_overfitting_gate_both_clear() -> None:
    decision = can_promote(_stats(), PromotionConfig(), pbo=_pbo())

    assert decision.promotable is True
    assert decision.overfitting == PASSED
    assert decision.reasons == []


def test_can_promote_refuses_on_the_g4_signature_even_with_perfect_floors() -> None:
    """High PBO AND a steeply negative degradation slope -- §78.8's fitted-selection signature."""
    decision = can_promote(_stats(), PromotionConfig(), pbo=_pbo(pbo="0.80", slope="-0.90"))

    assert decision.promotable is False
    assert decision.floors_pass is True
    assert decision.overfitting == FAILED
    assert any("PBO" in r for r in decision.reasons)


def test_a_failing_floor_and_an_absent_pbo_are_reported_together() -> None:
    """Both axes surface at once, same as the floors already do among themselves -- an operator
    fixing one problem should already be able to see the other."""
    decision = can_promote(_stats(win_rate=0.10), PromotionConfig())

    assert decision.promotable is False
    assert decision.floors_pass is False
    assert any("win_rate" in r for r in decision.reasons)
    assert any("not run" in r.lower() for r in decision.reasons)


def test_pbo_gate_thresholds_come_from_config_not_from_reinvented_constants() -> None:
    """`research.pbo_max`/`slope_floor` are the shipped thresholds; the gate must read THEM.

    KB §78.7's Strathern warning cuts both ways: thresholds must not be tuned to obtain a
    verdict, which also means the gate must not quietly use numbers other than the ones the
    deployment declared and can be audited against.
    """
    research = ResearchConfig(pbo_max=Decimal("0.9"), slope_floor=Decimal("-0.1"))
    gate = pbo_gate_from_config(research)

    assert gate.pbo_max == Decimal("0.9")
    assert gate.slope_floor == Decimal("-0.1")

    # A result that the DEFAULT gate would fail (0.80 > 0.05 and -0.90 < -0.5) still fails
    # under this stricter-sloped one, but the reason must quote the configured numbers.
    decision = can_promote(
        _stats(), PromotionConfig(), pbo=_pbo(pbo="0.95", slope="-0.5"), gate=gate
    )
    assert decision.promotable is False
    assert any("0.9" in r for r in decision.reasons)


def test_transition_does_not_promote_without_overfitting_evidence(repo: Repository) -> None:
    """The gap closes where it actually matters: the DB-writing path.

    `transition` is what moves a rule candidate->paper->live. Before #247 it advanced on the
    four floors alone; now an un-checked rule stays put. `rules promote --force` remains the
    deliberate, loud, audited bypass for the low-frequency case that can never reach the floor
    -- an operator who wants to override this has a documented door, and it leaves a record.
    """
    rule_id = _insert_rule(repo, "turtle_breakout", status="candidate")

    assert transition(repo, "turtle_breakout", _stats(), PromotionConfig()) == "candidate"
    assert _rule_status(repo, rule_id) == "candidate"

    # Same stats, now with a clean CSCV result behind them: promotes.
    assert (
        transition(repo, "turtle_breakout", _stats(), PromotionConfig(), pbo=_pbo()) == "paper"
    )
    assert _rule_status(repo, rule_id) == "paper"


def test_demotion_never_requires_overfitting_evidence(repo: Repository) -> None:
    """Demotion must stay reachable without a CSCV run.

    Asymmetric on purpose, and the asymmetry is the safety property: missing evidence blocks
    letting a rule ADVANCE toward real money, and must never block pulling one back from it.
    """
    rule_id = _insert_rule(repo, "decayed", status="live")

    assert transition(repo, "decayed", _stats(expectancy=Decimal("-5")), PromotionConfig()) == (
        "disabled"
    )
    assert _rule_status(repo, rule_id) == "disabled"


# -- check_floors (the four performance floors, spec G2) ----------------------


def test_can_promote_true_when_all_floors_met() -> None:
    cfg = PromotionConfig()
    ok, reasons = check_floors(_stats(), cfg)
    assert ok is True
    assert reasons == []


def test_can_promote_false_on_failing_win_rate_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = check_floors(_stats(win_rate=0.40), cfg)
    assert ok is False
    assert any("win_rate" in r for r in reasons)


def test_can_promote_false_on_failing_expectancy_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = check_floors(_stats(expectancy=Decimal("-1")), cfg)
    assert ok is False
    assert any("expectancy" in r for r in reasons)


def test_can_promote_false_on_failing_rr_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = check_floors(_stats(avg_win=Decimal("5"), avg_loss=Decimal("-10")), cfg)
    assert ok is False
    assert any("rr" in r for r in reasons)


def test_can_promote_false_on_insufficient_sample_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = check_floors(_stats(n_trades=10), cfg)
    assert ok is False
    assert any("n_trades" in r for r in reasons)


def test_can_promote_accumulates_all_failing_reasons() -> None:
    cfg = PromotionConfig()
    ok, reasons = check_floors(
        _stats(n_trades=5, win_rate=0.1, avg_win=Decimal("1"), avg_loss=Decimal("-10")), cfg
    )
    assert ok is False
    assert len(reasons) >= 3


def test_can_promote_respects_custom_config_floors() -> None:
    cfg = PromotionConfig(min_trades=5, min_rr=Decimal("1.0"), min_win_rate=0.5)
    stats = _stats(n_trades=6, win_rate=0.5, avg_win=Decimal("10"), avg_loss=Decimal("-9"))
    ok, reasons = check_floors(stats, cfg)
    assert ok is True
    assert reasons == []


# -- per-rule-class promotion floors (KB §25.5) ---------------------------------


def test_trend_follow_floor_relaxes_win_rate_but_NOT_sample_size() -> None:
    """The trend class relaxes the WIN-RATE axis only. The two axes are independent.

    A trend-follower legitimately wins under half its trades (KB §25.5), so the flat 55%
    bar is wrong for it -- that relaxation is correct. But `min_trades` was originally
    relaxed 100 -> 30 in the same change, as if the two were one concession, and two
    independent lines of evidence say the sample-size axis needed no relaxation at all:

      * the 2026-07-20 random-entry control arm put the requirement at ~68 trades
        (`docs/experiments/2026-07-20-adx-ablation-and-random-entry-control.md`);
      * KB §73.3's Minimum Backtest Length independently reproduces that (~68 at N=26)
        and puts it at ~143 at our real trials count.

    At 30 a rule could promote on roughly HALF the sample its own edge would need to be
    distinguishable from random entries. See `docs/experiments/2026-07-20-guards-and-
    strategy-review.md` §A1.
    """
    floor = floor_for_class(TREND_FOLLOW)
    assert floor.min_trades == 100, "sample size must NOT be relaxed for trend-followers"
    assert floor.min_win_rate == 0.30, "the win-rate relaxation is the point of this class"
    assert floor.min_rr == Decimal("1.5")
    assert floor.min_expectancy == Decimal("0")


def test_floor_for_default_class_falls_back_to_supplied_default() -> None:
    default = PromotionConfig(min_trades=100, min_win_rate=0.55)
    assert floor_for_class(DEFAULT_CLASS, default) is default


def test_floor_for_unknown_class_falls_back_to_supplied_default() -> None:
    default = PromotionConfig(min_trades=100)
    assert floor_for_class("no-such-class", default) is default


def test_floor_for_unknown_class_without_default_is_canonical() -> None:
    assert floor_for_class("no-such-class") == PromotionConfig()


def test_promotion_class_of_reads_rule_attribute() -> None:
    class _Trend:
        promotion_class = TREND_FOLLOW

    class _Plain:
        pass

    assert promotion_class_of(_Trend()) == TREND_FOLLOW
    assert promotion_class_of(_Plain()) == DEFAULT_CLASS


def test_low_win_rate_edge_passes_trend_floor_but_fails_canonical() -> None:
    """What the trend class is FOR: a 37.5%-win rule with asymmetric payoff is a legitimate
    trend-follower, not a broken one -- so it must clear the class floor while failing the
    global 55%-win bar (KB §25.5). Sample size is held ABOVE the floor here so that this
    test exercises the win-rate axis in isolation."""
    trend_edge = _stats(
        n_trades=120,
        win_rate=0.375,
        avg_win=Decimal("4343"),
        avg_loss=Decimal("-1938"),
        expectancy=Decimal("417"),
    )
    assert check_floors(trend_edge, PromotionConfig())[0] is False
    assert check_floors(trend_edge, floor_for_class(TREND_FOLLOW))[0] is True


def test_the_live_turtle_sample_now_FAILS_the_trend_floor_on_sample_size() -> None:
    """The behavioural consequence of un-relaxing `min_trades`, pinned deliberately.

    This is the real turtle-only edge sample -- 40 trades, 37.5% win, R:R ~2.24, positive
    expectancy. It used to clear the trend floor because that floor asked for only 30
    trades. It no longer does, and it SHOULD not: 40 trades is well under the ~68 the
    2026-07-20 random-entry experiment measured as the minimum for this edge to be
    distinguishable from random entries through the same exit.

    The rule may well be good. We simply do not yet have enough of it to know, and the
    gate's job is to say so rather than to lower the bar to what we happen to have.
    """
    turtle = _stats(
        n_trades=40,
        win_rate=0.375,
        avg_win=Decimal("4343"),
        avg_loss=Decimal("-1938"),
        expectancy=Decimal("417"),
    )
    ok, reasons = check_floors(turtle, floor_for_class(TREND_FOLLOW))
    assert ok is False
    assert any("n_trades" in r for r in reasons), reasons
    # ...and it fails ONLY on sample size -- the win-rate relaxation still works.
    assert not any("win_rate" in r for r in reasons), reasons


# -- should_demote --------------------------------------------------------------


def test_should_demote_false_when_rolling_stats_above_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(), cfg) is False


def test_should_demote_true_when_rolling_win_rate_below_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(win_rate=0.30), cfg) is True


def test_should_demote_true_when_rolling_expectancy_below_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(expectancy=Decimal("-5")), cfg) is True


def test_should_demote_true_when_rolling_rr_below_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(avg_win=Decimal("5"), avg_loss=Decimal("-10")), cfg) is True


def test_should_demote_ignores_min_trades_floor() -> None:
    # A live rule's rolling window is deliberately smaller than the initial promotion
    # sample; should_demote must still fire on a small-but-healthy rolling sample being
    # False, and small-but-decayed sample being True, without requiring min_trades.
    cfg = PromotionConfig()
    assert should_demote(_stats(n_trades=10), cfg) is False
    assert should_demote(_stats(n_trades=10, win_rate=0.1), cfg) is True


# -- transition -----------------------------------------------------------------


def test_transition_promotes_candidate_to_paper_on_passing_stats(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "pullback_continuation", status="candidate")
    cfg = PromotionConfig()

    # `pbo=` supplied because promotion now requires an overfitting check to have RUN (#247);
    # without it this same call correctly returns "candidate" -- see
    # test_transition_does_not_promote_without_overfitting_evidence.
    new_status = transition(repo, "pullback_continuation", _stats(), cfg, pbo=_pbo())

    assert new_status == "paper"
    assert _rule_status(repo, rule_id) == "paper"


def test_transition_keeps_candidate_when_stats_fail(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "pullback_continuation", status="candidate")
    cfg = PromotionConfig()

    new_status = transition(repo, "pullback_continuation", _stats(win_rate=0.1), cfg)

    assert new_status == "candidate"
    assert _rule_status(repo, rule_id) == "candidate"


def test_transition_promotes_paper_to_live_on_passing_stats(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "rsi_meanrev", status="paper")
    cfg = PromotionConfig()

    new_status = transition(repo, "rsi_meanrev", _stats(), cfg, pbo=_pbo())  # see #247

    assert new_status == "live"
    assert _rule_status(repo, rule_id) == "live"


def test_transition_demotes_live_to_disabled_on_decayed_stats(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "dca", status="live")
    cfg = PromotionConfig()

    new_status = transition(repo, "dca", _stats(win_rate=0.2, expectancy=Decimal("-3")), cfg)

    assert new_status == "disabled"
    assert _rule_status(repo, rule_id) == "disabled"


def test_transition_keeps_live_when_rolling_stats_healthy(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "dca", status="live")
    cfg = PromotionConfig()

    new_status = transition(repo, "dca", _stats(), cfg)

    assert new_status == "live"
    assert _rule_status(repo, rule_id) == "live"


def test_transition_disabled_is_terminal(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "dca", status="disabled")
    cfg = PromotionConfig()

    new_status = transition(repo, "dca", _stats(), cfg)

    assert new_status == "disabled"
    assert _rule_status(repo, rule_id) == "disabled"


def test_transition_unknown_rule_raises(repo: Repository) -> None:
    cfg = PromotionConfig()
    with pytest.raises(ValueError):
        transition(repo, "no-such-rule", _stats(), cfg)


# -- next_status: public un-gated-step helper (funded paper-forward, `rules promote --force`) --


def test_next_status_candidate_to_paper() -> None:
    assert next_status("candidate") == "paper"


def test_next_status_paper_to_live() -> None:
    assert next_status("paper") == "live"


def test_next_status_live_has_no_next_step() -> None:
    assert next_status("live") is None


def test_next_status_disabled_has_no_next_step() -> None:
    assert next_status("disabled") is None


# -- G4: PBO conjunction gate (spec §7) ----------------------------------------


def test_g4_passes_when_pbo_low_and_slope_shallow():
    ok, reasons = g4_pbo_gate(Decimal("0.01"), Decimal("-0.2"), PBOGate())
    assert ok is True
    assert reasons == []


def test_g4_passes_on_high_pbo_with_shallow_slope():
    """The plateau case (§78.7 limitation 4).

    A broad plateau is a set of near-identical configurations, which produces high PBO BY
    CONSTRUCTION -- and §54.10/§73.13 tell us to PREFER a broad plateau. A bare 0.05 gate
    would punish the robust choice, so the conjunction must let this through.
    """
    ok, reasons = g4_pbo_gate(Decimal("0.80"), Decimal("-0.10"), PBOGate())
    assert ok is True
    assert reasons == []


def test_g4_passes_on_steep_slope_with_low_pbo():
    ok, _ = g4_pbo_gate(Decimal("0.01"), Decimal("-0.90"), PBOGate())
    assert ok is True


def test_g4_fails_only_on_the_conjunction():
    ok, reasons = g4_pbo_gate(Decimal("0.80"), Decimal("-0.90"), PBOGate())
    assert ok is False
    assert len(reasons) == 1
    assert "0.80" in reasons[0]
    assert "-0.90" in reasons[0]


def test_g4_boundaries_are_strict_inequalities():
    # Exactly at both thresholds is a PASS: the gate fires on > and <, not >= and <=.
    ok, _ = g4_pbo_gate(Decimal("0.05"), Decimal("-0.5"), PBOGate())
    assert ok is True


def test_g4_default_thresholds_match_the_shipped_config():
    """The gate's defaults and config.yaml must not drift apart."""
    from keel_core.config import ResearchConfig

    gate = PBOGate()
    defaults = ResearchConfig()
    assert gate.pbo_max == defaults.pbo_max
    assert gate.slope_floor == defaults.slope_floor
