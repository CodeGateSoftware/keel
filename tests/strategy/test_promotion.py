"""Tests for keel.strategy.promotion: the promotion/demotion lifecycle (P2 Task 9).

Builds `BacktestResult` fixtures directly (this module must not depend on `backtest.py`'s
simulation internals, `paper.py`, or `engine.py` — it only consumes the `BacktestResult`
shape, per the Phase 2 plan) and exercises the gates + DB-backed `transition()` against a
real in-memory `Repository`.
"""

from __future__ import annotations

import json
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
    MIN_POOLED_PRODUCTS,
    MIN_TRADES_PER_PRODUCT_POOLED,
    NOT_RUN,
    PASSED,
    TREND_FOLLOW,
    PBOGate,
    ProductSample,
    PromotionConfig,
    can_promote,
    check_floors,
    floor_for_class,
    g4_pbo_gate,
    next_status,
    paper_sibling_rows,
    pbo_gate_from_config,
    pool_stats,
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


def _insert_rule(
    repo: Repository, kind: str, status: str = "candidate", params: dict | None = None
) -> int:
    cursor = repo._conn.execute(
        "INSERT INTO rules (kind, params, status, created_at) VALUES (?, ?, ?, ?)",
        (kind, json.dumps(params or {}), status, 1_700_000_000),
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


# -- cross-product pooling of min_trades (#338) ---------------------------------
#
# The gate's unit of evaluation, not its floors: `min_trades` stays 100, but the
# sample-size axis may be cleared EITHER by the rule's own backtest exactly as
# before OR by the same parameter set's pooled evidence across products in paper,
# discounted by a diversity floor (crypto assets correlate; a pool concentrated in
# few products overstates its power). Operator-approved 2026-08-17 (see #338) --
# the agreement a gate change requires.


def _pool(
    own_product: str,
    own_stats: BacktestResult,
    siblings: list[tuple[str, BacktestResult]],
) -> list[ProductSample]:
    """The full pooled sample: the candidate's own reading plus one per sibling."""
    return [ProductSample(own_product, own_stats)] + [
        ProductSample(pid, stats) for pid, stats in siblings
    ]


def _seven_paper_siblings() -> list[tuple[str, BacktestResult]]:
    """7 healthy same-parameter readings on other products, 16 trades each.

    16 is chosen so the pooled arithmetic is exact in floats and Decimals alike:
    win_rate 0.625 of 16 = 10 wins; pooled with the candidate's 4 wins in 16,
    that is 74/128 = 0.578125 -- above the 0.55 floor the candidate alone fails.
    """
    return [
        (f"ASSET-{i}-USD", _stats(n_trades=16, win_rate=0.625)) for i in range(1, 8)
    ]


def test_pool_stats_field_arithmetic_is_the_documented_weighting() -> None:
    """The docstring's field-by-field claims, asserted directly -- not only through the
    gate's pass/fail, which reads n/win-rate/expectancy and could stay green while
    avg_win/avg_loss/rr pool wrongly (a mis-weighted loss side that never crosses a
    floor is still a lie in the census an operator reads).

    Sibling: n=16, 10 wins (0.625), avg_win 30, avg_loss -10.
    Candidate: n=16, 4 wins (0.25), avg_win 60, avg_loss -20, expectancy -2.
    Pooled: n=32; wins 14; win_rate 14/32 = 0.4375; avg_win (10*30 + 4*60)/14 = 540/14;
    avg_loss (6*-10 + 12*-20)/18 = -300/18; gross_win 540, gross_loss 300, PF 1.8;
    expectancy (16*-2 + 16*14)/32 = 6 -- the trade-weighted mean, exact.
    """
    own = _stats(
        n_trades=16, win_rate=0.25, avg_win=Decimal("60"),
        avg_loss=Decimal("-20"), expectancy=Decimal("-2"),
    )
    pooled, reading = pool_stats(
        _pool("BTC-USD", own, _seven_paper_siblings()[:1])
    )
    assert reading.n_pooled == 32
    assert dict(reading.per_product) == {"BTC-USD": 16, "ASSET-1-USD": 16}
    assert pooled.n_trades == 32
    assert pooled.win_rate == 0.4375
    assert pooled.avg_win == Decimal("540") / 14
    assert pooled.avg_loss == Decimal("-300") / 18
    assert pooled.expectancy == Decimal("6")
    assert pooled.profit_factor == Decimal("1.8")


def test_no_pool_supplied_is_exactly_todays_decision() -> None:
    """Without pooled samples the decision carries no pooled reading -- the single-product
    path is byte-for-byte the pre-#338 behavior, not a special case of the pooled one."""
    ok = can_promote(_stats(), PromotionConfig(), pbo=_pbo())
    assert ok.promotable is True
    assert ok.reasons == []
    assert ok.pooled is None

    failing = can_promote(_stats(n_trades=10), PromotionConfig(), pbo=_pbo())
    assert failing.promotable is False
    assert failing.reasons == ["n_trades 10 < min_trades 100"]
    assert failing.pooled is None


def test_pooled_pass_when_the_rule_alone_is_short_but_the_parameter_set_is_not() -> None:
    """THE motivating case: a new asset's rule has 16 of its own trades, and the same
    parameters already have a paper track record on 7 other products.

    The per-rule reading fails on sample size AND on win rate (0.25 < 0.55) and
    expectancy -- and the pooled reading clears everything: 8 products x 16 = 128
    trades, 8 products each >= 10 trades, pooled win rate 74/128 = 0.578125,
    pooled expectancy (16*-2 + 112*14)/128 = 12. Quality is judged on the POOLED
    stats on this path, which is the point: the edge belongs to the parameter set,
    and the unit of evaluation is what #338 changed.
    """
    own = _stats(n_trades=16, win_rate=0.25, expectancy=Decimal("-2"))
    samples = _pool("BTC-USD", own, _seven_paper_siblings())

    decision = can_promote(own, PromotionConfig(), pbo=_pbo(), pooled_samples=samples)

    assert decision.promotable is True
    assert decision.floors_pass is True
    assert decision.reasons == []
    assert decision.pooled is not None
    assert decision.pooled.n_pooled == 128
    assert decision.pooled.products_contributing == 8
    assert decision.pooled.min_contribution == 16
    assert len(decision.pooled.per_product) == 8


def test_pooled_fail_on_total_n_names_its_path_and_the_census() -> None:
    """8 products x 10 trades: every product clears the per-product bar, but the pool
    totals 80 < 100. The reason must say WHICH reading failed and across how many
    products, so an operator knows the fix is more trades, not more assets."""
    own = _stats(n_trades=10, win_rate=0.6)
    siblings = [(f"ASSET-{i}-USD", _stats(n_trades=10, win_rate=0.6)) for i in range(1, 8)]

    decision = can_promote(
        own, PromotionConfig(), pbo=_pbo(), pooled_samples=_pool("BTC-USD", own, siblings)
    )

    assert decision.promotable is False
    assert any("pooled n 80 < min_trades 100 across 8 products" in r for r in decision.reasons)
    # the per-rule failure is still visible alongside the pooled one
    assert any("n_trades 10 < min_trades 100" in r for r in decision.reasons)
    assert decision.pooled is not None
    assert decision.pooled.n_pooled == 80


def test_pooled_fail_on_diversity_even_with_120_total_trades() -> None:
    """4 products x 30 = 120 trades clears the total, but 4 < MIN_POOLED_PRODUCTS.

    This is the correlation discount doing its job: 120 trades from 4 correlated
    assets are not 120 independent observations, and the floor refuses to price
    them as though they were.
    """
    own = _stats(n_trades=30)
    siblings = [(f"ASSET-{i}-USD", _stats(n_trades=30)) for i in range(1, 4)]

    decision = can_promote(
        own, PromotionConfig(), pbo=_pbo(), pooled_samples=_pool("BTC-USD", own, siblings)
    )

    assert decision.promotable is False
    assert any(
        f"pooled diversity 4 products < required {MIN_POOLED_PRODUCTS}" in r
        for r in decision.reasons
    )
    assert decision.pooled is not None
    assert decision.pooled.n_pooled == 120  # the total is NOT the problem
    assert decision.pooled.products_contributing == 4


def test_a_rule_with_its_own_full_sample_is_still_judged_on_its_own_stats() -> None:
    """Path selection: the pooled path is reached only when the per-rule SAMPLE is short.

    A rule whose own backtest clears min_trades has its own adequate sample, so its
    quality floors are judged on its own stats exactly as before -- a pool of healthy
    siblings must not rescue a rule that is itself unprofitable on 150 trades. This
    keeps the change a change of UNIT for rules that lack a sample, not a loosening
    for rules that have one.
    """
    own = _stats(n_trades=150, win_rate=0.40)  # own sample clears n; quality fails
    decision = can_promote(
        own,
        PromotionConfig(),
        pbo=_pbo(),
        pooled_samples=_pool("BTC-USD", own, _seven_paper_siblings()),
    )

    assert decision.promotable is False
    assert any("win_rate 0.4 < min_win_rate 0.55" in r for r in decision.reasons)
    # the pooled reading is still REPORTED, it just does not carry the decision
    assert decision.pooled is not None
    assert decision.pooled.n_pooled == 150 + 7 * 16


def test_pooled_reading_is_reported_even_when_the_rule_passes_alone() -> None:
    """Default behavior for promotions that already pass on one product is unchanged --
    and the cross-product reading is printed alongside, not hidden, because an operator
    approving the promotion is entitled to both readings."""
    own = _stats()  # 150 trades, clears everything alone
    decision = can_promote(
        own,
        PromotionConfig(),
        pbo=_pbo(),
        pooled_samples=_pool("BTC-USD", own, _seven_paper_siblings()),
    )

    assert decision.promotable is True
    assert decision.reasons == []
    assert decision.pooled is not None
    assert decision.pooled.n_pooled == 150 + 7 * 16


def test_pooled_quality_floors_are_judged_on_the_pooled_stats() -> None:
    """The pooled path checks expectancy/rr/win-rate on the POOLED aggregates, so a pool
    that is collectively under water is refused even with n and diversity both clear."""
    own = _stats(n_trades=16)
    # 7 siblings whose pooled expectancy is negative: 16*-2 + 112*(-3) = -368 over 128
    siblings = [
        (f"ASSET-{i}-USD", _stats(n_trades=16, expectancy=Decimal("-3"), win_rate=0.30))
        for i in range(1, 8)
    ]
    decision = can_promote(
        own, PromotionConfig(), pbo=_pbo(), pooled_samples=_pool("BTC-USD", own, siblings)
    )

    assert decision.promotable is False
    assert any(r.startswith("pooled expectancy") for r in decision.reasons), decision.reasons
    assert any(r.startswith("pooled win_rate") for r in decision.reasons), decision.reasons


def test_diversity_counts_only_products_meeting_the_per_product_bar() -> None:
    """A 3-trade product still contributes its 3 trades to the pooled total (honest
    pooling -- evidence is evidence), but it does not count toward the diversity floor:
    the floor's question is how many products have independently meaningful samples."""
    own = _stats(n_trades=40)
    # 4 substantive siblings (20 each) + 3 token ones (3 each): pooled = 40 + 80 + 9 = 129
    siblings = [(f"HEAVY-{i}-USD", _stats(n_trades=20)) for i in range(1, 5)]
    siblings += [(f"THIN-{i}-USD", _stats(n_trades=3)) for i in range(1, 4)]

    decision = can_promote(
        own, PromotionConfig(), pbo=_pbo(), pooled_samples=_pool("BTC-USD", own, siblings)
    )

    assert decision.pooled is not None
    assert decision.pooled.n_pooled == 129
    # own + the 4 heavy siblings clear MIN_TRADES_PER_PRODUCT_POOLED; the 3-trade rows do not
    over_bar = [n for _, n in decision.pooled.per_product if n >= MIN_TRADES_PER_PRODUCT_POOLED]
    assert decision.pooled.products_contributing == 5 == len(over_bar)
    assert decision.pooled.min_contribution == 3
    assert decision.promotable is True  # 129 >= 100 and exactly 5 products clear the bar


# -- paper_sibling_rows: which stored rows count as pooled evidence --------------


_CANDIDATE_PARAMS = {"entry_lookback": 55, "product_id": "BTC-USD"}


def test_paper_sibling_rows_matches_same_params_across_products(repo: Repository) -> None:
    _insert_rule(repo, "pullback_continuation", status="candidate", params=_CANDIDATE_PARAMS)
    _insert_rule(
        repo,
        "pullback_continuation",
        status="paper",
        params={"entry_lookback": 55, "product_id": "ETH-USD"},
    )

    rows = paper_sibling_rows(repo, "pullback_continuation", _CANDIDATE_PARAMS)

    assert len(rows) == 1
    assert rows[0]["params"]["product_id"] == "ETH-USD"


def test_params_mismatch_is_not_a_sibling(repo: Repository) -> None:
    """`entry_lookback: 20` is a DIFFERENT parameter set -- pooling its trades would
    launder another experiment's sample into this one's evidence."""
    _insert_rule(repo, "pullback_continuation", status="candidate", params=_CANDIDATE_PARAMS)
    _insert_rule(
        repo,
        "pullback_continuation",
        status="paper",
        params={"entry_lookback": 20, "product_id": "ETH-USD"},  # different params
    )
    _insert_rule(  # wrong kind, same params
        repo, "rsi_meanrev", status="paper", params={"entry_lookback": 55, "product_id": "SOL-USD"}
    )
    _insert_rule(  # the docstring's own example: "55" (str) vs 55 (int) is a real mismatch
        repo,
        "pullback_continuation",
        status="paper",
        params={"entry_lookback": "55", "product_id": "LINK-USD"},
    )

    assert paper_sibling_rows(repo, "pullback_continuation", _CANDIDATE_PARAMS) == []


def test_only_paper_siblings_count(repo: Repository) -> None:
    """A `live` row's stats are not paper evidence for a pooled promotion, and neither
    is another candidate's: the pooled path exists to count out-of-sample PAPER
    track record, which is what those statuses mean."""
    _insert_rule(repo, "pullback_continuation", status="candidate", params=_CANDIDATE_PARAMS)
    for status in ("candidate", "live", "disabled"):
        _insert_rule(
            repo,
            "pullback_continuation",
            status=status,
            params={"entry_lookback": 55, "product_id": f"{status.upper()}-USD"},
        )

    assert paper_sibling_rows(repo, "pullback_continuation", _CANDIDATE_PARAMS) == []


def test_candidates_own_product_is_counted_once_never_as_a_sibling(repo: Repository) -> None:
    """A duplicate paper row on the candidate's OWN product is not a sibling: its trades
    are the same trades the candidate's own backtest already put into the pool, and
    counting them twice would inflate pooled n out of nothing."""
    _insert_rule(repo, "pullback_continuation", status="candidate", params=_CANDIDATE_PARAMS)
    _insert_rule(  # duplicate on the candidate's own product, now paper
        repo,
        "pullback_continuation",
        status="paper",
        params={"entry_lookback": 55, "product_id": "BTC-USD"},
    )

    assert paper_sibling_rows(repo, "pullback_continuation", _CANDIDATE_PARAMS) == []


def test_duplicate_sibling_rows_on_one_product_pool_once(repo: Repository) -> None:
    """Two paper rows for the same (params, product) are one rule observed twice -- the
    most recent row is the sibling, and its trades enter the pool exactly once."""
    _insert_rule(repo, "pullback_continuation", status="candidate", params=_CANDIDATE_PARAMS)
    first = _insert_rule(
        repo,
        "pullback_continuation",
        status="paper",
        params={"entry_lookback": 55, "product_id": "ETH-USD"},
    )
    second = _insert_rule(
        repo,
        "pullback_continuation",
        status="paper",
        params={"entry_lookback": 55, "product_id": "ETH-USD"},
    )

    rows = paper_sibling_rows(repo, "pullback_continuation", _CANDIDATE_PARAMS)

    assert len(rows) == 1
    assert rows[0]["id"] == second  # the most recently inserted, like _fetch_rule
    assert rows[0]["id"] != first


# -- transition under pooling ----------------------------------------------------


def test_transition_promotes_via_the_pooled_path(repo: Repository) -> None:
    """The DB-writing path makes the same pooled decision the CLI prints -- a rule whose
    own backtest is short promotes on the parameter set's cross-product evidence."""
    rule_id = _insert_rule(repo, "pullback_continuation", status="candidate")
    own = _stats(n_trades=16, win_rate=0.25, expectancy=Decimal("-2"))

    new_status = transition(
        repo,
        "pullback_continuation",
        own,
        PromotionConfig(),
        pbo=_pbo(),
        pooled_samples=_pool("BTC-USD", own, _seven_paper_siblings()),
    )

    assert new_status == "paper"
    assert _rule_status(repo, rule_id) == "paper"


def test_transition_stays_put_when_the_pool_is_too_thin(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "pullback_continuation", status="candidate")
    own = _stats(n_trades=16)
    siblings = [(f"ASSET-{i}-USD", _stats(n_trades=10)) for i in range(1, 4)]  # 4 products total

    new_status = transition(
        repo,
        "pullback_continuation",
        own,
        PromotionConfig(),
        pbo=_pbo(),
        pooled_samples=_pool("BTC-USD", own, siblings),
    )

    assert new_status == "candidate"
    assert _rule_status(repo, rule_id) == "candidate"


def test_pooled_pass_still_requires_the_overfitting_check(repo: Repository) -> None:
    """Pooling widens the SAMPLE-SIZE axis only. The G4 gate is untouched by it -- it
    judges the parameter SELECTION (the trial matrix), not the sample, and it blocks
    exactly as before when no CSCV result was supplied."""
    rule_id = _insert_rule(repo, "pullback_continuation", status="candidate")
    own = _stats(n_trades=16, win_rate=0.25, expectancy=Decimal("-2"))

    new_status = transition(
        repo,
        "pullback_continuation",
        own,
        PromotionConfig(),
        pooled_samples=_pool("BTC-USD", own, _seven_paper_siblings()),
    )

    assert new_status == "candidate"
    assert _rule_status(repo, rule_id) == "candidate"


def test_transition_with_rule_id_targets_the_named_row_not_the_newest_sibling(
    repo: Repository,
) -> None:
    """`keel rules promote <id>` names a row; with same-kind sibling rows now the normal
    shape of the table (#338's pools are made of them), the kind-level "newest row"
    lookup would advance a sibling the operator never typed. `rule_id` pins the target;
    omitting it keeps the historical kind-level lookup for library callers."""
    named = _insert_rule(repo, "pullback_continuation", status="candidate")
    newer_sibling = _insert_rule(repo, "pullback_continuation", status="paper")

    status = transition(
        repo,
        "pullback_continuation",
        _stats(),
        PromotionConfig(),
        pbo=_pbo(),
        rule_id=named,
    )

    assert status == "paper"
    assert _rule_status(repo, named) == "paper"
    assert _rule_status(repo, newer_sibling) == "paper"  # untouched: candidate->paper only


def test_transition_without_rule_id_keeps_the_kind_level_lookup(repo: Repository) -> None:
    """The pre-existing behavior, pinned: no `rule_id` means the newest row of the kind."""
    _insert_rule(repo, "pullback_continuation", status="candidate")
    newest = _insert_rule(repo, "pullback_continuation", status="paper")

    status = transition(repo, "pullback_continuation", _stats(), PromotionConfig(), pbo=_pbo())

    assert status == "live"  # the newest row (paper) promoted, not the older candidate
    assert _rule_status(repo, newest) == "live"
