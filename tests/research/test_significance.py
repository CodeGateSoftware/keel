"""Rule-family significance (#475) — is a family's edge distinguishable from zero at the fee
actually paid?

Two load-bearing patterns, inherited from `test_throughput.py`:

1. **Reproducing published values.** The detection-boundary test below reproduces the
   cross-verification's headline (`docs/research/2026-08-20-quant-lab-note-cross-verification.md`
   §4): a pool of 100 herding trades carries n_eff ≈ 39 independent observations and can only
   detect a 20-point edge at 80% power. The exact boundary is the note's own sample-size
   constant: at break-even 1/2 (the maximal-variance case `(z₀.₉₅+z₀.₈₀)²/4 = 1.5464` assumes),
   a 20-point edge at n_eff 39 gives z ≈ z₀.₉₅ + z₀.₈₀ = 2.487, p ≈ 0.006 — NOT p = 0.05. A
   one-sided 5% test at 80% power crosses its α threshold well before it crosses its power
   threshold; conflating the two was the error in an earlier draft of this test. At the
   operative b=6 geometry (break-even 1/7) the null variance is lower still, so the same
   20-point edge is MORE significant — the published 20-point figure is the conservative case.
2. **The tool must be able to say no.** "not_distinguishable" is a first-class verdict with
   its own tests, because a significance tool that cannot refuse is a flattery tool.

Hand-computed fixtures use payoffs chosen so every Decimal terminates: b=3 → break-even 1/4,
b=4 → 1/5, b=1.5 → 1/2.5, b=1 → 1/2.
"""

from __future__ import annotations

from decimal import Decimal

from keel.research.deflate import inverse_normal_cdf, normal_cdf
from keel.research.significance import (
    FEE_REGIMES,
    render_family,
    se_null,
    significance,
    z_alpha,
)
from keel.research.throughput import design_effect, detectable_edge, n_eff

TAKER = Decimal("0.012")
ZERO = Decimal("0")


def _outcomes(
    wins: int,
    losses: int,
    win_pnl: Decimal = Decimal("10"),
    loss_pnl: Decimal = Decimal("-10"),
) -> list[tuple[str, Decimal, Decimal]]:
    """Synthetic closed-trade rows: equal-magnitude wins/losses with per-trade r-multiples.

    The r_multiple is carried for interface stability with `Trade` and is deliberately
    ignored by the math (payoff comes from pnl); the values here are arbitrary.
    """
    return [("win", win_pnl, Decimal("6"))] * wins + [("loss", loss_pnl, Decimal("-1"))] * losses


# -- payoff / break-even / win-rate arithmetic (hand-computed) -----------------------------------


def test_payoff_and_break_even_are_exact() -> None:
    # 3 wins of +30, 2 losses of -10: avg |win| 30 / avg |loss| 10 = b 3 -> break-even 1/4.
    stat = significance(
        "rsi_meanrev",
        "outside_allowance_taker",
        TAKER,
        _outcomes(3, 2, win_pnl=Decimal("30"), loss_pnl=Decimal("-10")),
    )
    assert stat.payoff_b == Decimal("3")
    assert stat.break_even == Decimal("0.25")
    assert stat.n_trades == 5
    assert stat.wins == 3


def test_win_rate_and_edge_are_exact() -> None:
    # 7 wins of +20, 3 losses of -5: b 4 -> break-even 1/5; win rate 7/10 -> edge exactly 1/2.
    stat = significance(
        "turtle_breakout",
        "outside_allowance_taker",
        TAKER,
        _outcomes(7, 3, win_pnl=Decimal("20"), loss_pnl=Decimal("-5")),
    )
    assert stat.win_rate == Decimal("0.7")
    assert stat.edge == Decimal("0.5")


def test_unequal_win_magnitudes_average_into_payoff() -> None:
    # wins of +40/+20 average to 30, not either one alone.
    rows = [
        ("win", Decimal("40"), Decimal("8")),
        ("win", Decimal("20"), Decimal("4")),
        ("loss", Decimal("-10"), Decimal("-1")),
    ]
    stat = significance("pullback_continuation", "outside_allowance_taker", TAKER, rows)
    assert stat.payoff_b == Decimal("3")
    assert stat.break_even == Decimal("0.25")


# -- the n_eff correction: raw n is never the sample size (#427) ---------------------------------


def test_n_effective_is_throughput_n_eff_never_raw_n() -> None:
    stat = significance("rsi_meanrev", "outside_allowance_taker", TAKER, _outcomes(60, 40))
    assert stat.n_effective == n_eff(Decimal(100))
    assert stat.n_effective != Decimal(100)
    assert stat.n_effective.quantize(Decimal("1")) == Decimal("39")
    assert stat.detectable_edge == detectable_edge(n_eff(Decimal(100)))


# -- standard error / z / p-value (hand-computed) ------------------------------------------------


def test_se_null_z_and_p_value_hand_computed() -> None:
    rows = _outcomes(3, 2, win_pnl=Decimal("30"), loss_pnl=Decimal("-10"))
    stat = significance("rsi_meanrev", "outside_allowance_taker", TAKER, rows)
    n_eff_5 = Decimal(5) / design_effect()
    se_expected = (Decimal("0.25") * (Decimal(1) - Decimal("0.25")) / n_eff_5).sqrt()
    assert se_null(Decimal("0.25"), n_eff_5) == se_expected
    assert stat.edge_z == stat.edge / se_expected
    # The p-value is the one place float is allowed (the module documents this): it must
    # agree with deflate.normal_cdf to well beyond reportable precision.
    assert abs(stat.p_value - (1 - normal_cdf(float(stat.edge_z)))) < 1e-6


def test_z_alpha_is_the_one_sided_95_percent_critical_value() -> None:
    # (z_0.95 + z_0.80)^2 / 4 = 1.5464 uses the SAME one-sided 95% alpha.
    assert z_alpha() == Decimal(str(inverse_normal_cdf(0.95)))
    assert z_alpha().quantize(Decimal("0.001")) == Decimal("1.645")


# -- verdicts: the null, the refusal, and the boundary -------------------------------------------


def test_null_refused_win_rate_equal_to_break_even() -> None:
    stat = significance("turtle_breakout", "inside_allowance_fee_free", ZERO, _outcomes(5, 5))
    assert stat.win_rate == stat.break_even == Decimal("0.5")
    assert stat.edge == Decimal("0")
    assert stat.edge_z == Decimal("0")
    assert stat.p_value == 0.5
    assert stat.verdict == "not_distinguishable"


def test_large_synthetic_edge_is_detected() -> None:
    # n=2000, 60% win against a 40% break-even (b=1.5): a fabricated edge this size must be
    # called distinguishable -- the tool is not allowed to be unable to say yes either.
    stat = significance(
        "rsi_meanrev",
        "outside_allowance_taker",
        TAKER,
        _outcomes(1200, 800, win_pnl=Decimal("30"), loss_pnl=Decimal("-20")),
    )
    assert stat.break_even == Decimal("0.4")
    assert stat.edge == Decimal("0.2")
    assert stat.p_value < 0.05
    assert stat.verdict == "distinguishable"


def test_published_detection_boundary_at_n_100_and_b_1() -> None:
    # The motivating honesty case: 100 pooled trades, edge exactly 20 points, break-even 1/2
    # (the maximal-variance geometry the note's constant 1.5464 assumes). This is exactly the
    # sample size at which the published table says a 20-point edge is the 80%-power limit,
    # so z lands on z_0.95 + z_0.80 = 2.487 (p ~ 0.006, comfortably under alpha but only
    # because 80% power >> 5% alpha by construction).
    stat = significance("turtle_breakout", "outside_allowance_taker", TAKER, _outcomes(70, 30))
    assert stat.edge == Decimal("0.2")
    z_power_boundary = z_alpha() + Decimal(str(inverse_normal_cdf(0.80)))
    assert abs(stat.edge_z - z_power_boundary) < Decimal("0.01")
    assert 0.005 < stat.p_value < 0.008
    assert stat.verdict == "distinguishable"
    # And the published figure read through throughput at exactly n_eff 39: 0.199. At this
    # run's n_eff (38.83) the detectable edge IS the observed edge, to half a point --
    # that is what "n=100 can only detect a 20-point edge" means.
    assert detectable_edge(Decimal("39")).quantize(Decimal("0.001")) == Decimal("0.199")
    assert abs(stat.detectable_edge - stat.edge) < Decimal("0.001")


def test_same_edge_below_the_pool_floor_is_refused() -> None:
    # The other half of the published statement: 25 pooled trades is n_eff ~ 9.7, and the
    # same 20-point edge does NOT clear alpha there. "n=100 can only detect a 20-point
    # edge" implies smaller pools detect even less.
    stat = significance("turtle_breakout", "outside_allowance_taker", TAKER, _outcomes(18, 7))
    assert stat.edge == Decimal("0.22")
    assert stat.p_value > 0.05
    assert stat.verdict == "not_distinguishable"


def test_b6_break_even_makes_the_boundary_stronger_not_weaker() -> None:
    # At the live target_rr=6 geometry (break-even 1/7), the null variance p(1-p) = 6/49 is
    # half the conservative 1/4, so the same 20-point edge is MORE significant than the
    # published boundary, not at it: 35 wins of 100 -> win rate 0.35 vs 1/7, ~20.7 points.
    stat = significance(
        "turtle_breakout",
        "outside_allowance_taker",
        TAKER,
        _outcomes(35, 65, win_pnl=Decimal("60"), loss_pnl=Decimal("-10")),
    )
    assert stat.break_even == Decimal(1) / Decimal(7)
    assert stat.edge > Decimal("0.2")
    assert stat.edge_z > Decimal("3")
    assert stat.p_value < 0.001
    assert stat.verdict == "distinguishable"


# -- degenerate samples --------------------------------------------------------------------------


def test_insufficient_n_for_empty_and_scratch_only() -> None:
    for rows in ([], [("scratch", Decimal("0"), Decimal("0"))] * 3):
        stat = significance("rsi_meanrev", "inside_allowance_fee_free", ZERO, rows)
        assert stat.n_trades == 0
        assert stat.verdict == "insufficient_n"


def test_scratch_and_open_excluded_from_n_and_averages() -> None:
    rows = [
        ("win", Decimal("20"), Decimal("4")),
        ("win", Decimal("20"), Decimal("4")),
        ("loss", Decimal("-10"), Decimal("-1")),
        ("loss", Decimal("-10"), Decimal("-1")),
        ("loss", Decimal("-10"), Decimal("-1")),
        ("scratch", Decimal("0"), Decimal("0")),
        ("open", Decimal("0"), Decimal("0")),
    ]
    stat = significance("pullback_continuation", "outside_allowance_taker", TAKER, rows)
    assert stat.n_trades == 5
    assert stat.wins == 2
    assert stat.win_rate == Decimal("0.4")
    assert stat.payoff_b == Decimal("2")


def test_no_losses_family_does_not_crash() -> None:
    # A family that never loses has break-even 0 by the issue's convention; the null has no
    # variance, so any positive edge is infinitely distinguishable. It must not divide by zero.
    rows = [("win", Decimal("25"), Decimal("5"))] * 4 + [("scratch", Decimal("0"), ZERO)]
    stat = significance("rsi_meanrev", "inside_allowance_fee_free", ZERO, rows)
    assert stat.n_trades == 4
    assert stat.break_even == Decimal("0")
    assert stat.edge == Decimal("1")
    assert stat.verdict == "distinguishable"


def test_no_wins_family_refuses() -> None:
    stat = significance("turtle_breakout", "outside_allowance_taker", TAKER, _outcomes(0, 8))
    assert stat.payoff_b == Decimal("0")
    assert stat.break_even == Decimal("1")
    assert stat.win_rate == Decimal("0")
    assert stat.edge == Decimal("-1")
    assert stat.p_value > 0.05
    assert stat.verdict == "not_distinguishable"


# -- reporting and determinism -------------------------------------------------------------------


def test_render_family_states_regime_n_n_eff_and_verdict() -> None:
    stat = significance("turtle_breakout", "outside_allowance_taker", TAKER, _outcomes(18, 7))
    text = "\n".join(render_family(stat)).lower()
    assert "outside_allowance_taker" in text
    assert "120 bp" in text
    assert "n=25" in text  # raw n always shown...
    assert "9.71" in text  # ...and never alone: beside its n_eff
    assert "not distinguishable" in text
    # the detectable edge at this n_eff, in points, beside the observed one
    assert "0.399" in text


def test_render_family_inside_allowance_uses_the_fee_free_wording() -> None:
    stat = significance("rsi_meanrev", "inside_allowance_fee_free", ZERO, _outcomes(50, 50))
    text = "\n".join(render_family(stat)).lower()
    assert "inside_allowance_fee_free" in text
    assert "fee-free" in text
    assert "not distinguishable" in text


def test_same_inputs_give_identical_stats() -> None:
    rows = _outcomes(70, 30)
    a = significance("turtle_breakout", "outside_allowance_taker", TAKER, rows)
    b = significance("turtle_breakout", "outside_allowance_taker", TAKER, rows)
    assert a == b  # frozen dataclass equality, Decimal fields and the float p included


def test_fee_regimes_carry_the_published_rates() -> None:
    assert FEE_REGIMES["outside_allowance_taker"] == Decimal("0.012")
    assert FEE_REGIMES["inside_allowance_fee_free"] == Decimal("0")
