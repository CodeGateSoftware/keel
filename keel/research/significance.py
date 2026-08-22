"""Rule-family significance (#475): is a family's edge distinguishable from zero at the fee
actually paid?

This module measures. It does not score, gate, or change anything. It is report-only evidence
for the promotion gate, and its most important output is the refusal: the honest result to
date is that no shipped family is net positive at the 120 bp taker fee and the fee-free
allowance reconstruction sits at break-even, so a significance tool here must be able to say
"not distinguishable from zero" and mean it. A tool that cannot say no is a flattery tool.

The question is a one-proportion test with the break-even as the null, priced at the fee
actually paid:

* **Break-even from payoff.** A family whose average win is `b` times its average loss breaks
  even at win rate `1/(1+b)` (the note's eq. 3 with `kappa` folded into the prices the
  backtest already charges). The edge is `win_rate - break_even`, in win-rate points.
* **Two fee regimes, never an average of them.** Outside the venue's fee-free volume
  allowance every round trip pays the taker rate on both legs; inside it pays none. The same
  reconstructed trades are evaluated at BOTH rates because the cross-verification
  (`docs/research/2026-08-20-quant-lab-note-cross-verification.md` §5) showed the fee IS the
  result: decisively negative outside, indistinguishable from break-even inside. `fee_pct`
  is a parameter, not a constant, so a deployment threads its own `config.fees.taker_pct`.
* **n_eff, never raw n (#427).** Signals fire in herds (~8 assets the same day, ICC 0.212),
  so pooled trades are divided by `throughput.design_effect()` before any standard error is
  formed. A pooled 100 is ~39 independent observations, and the standard error is computed
  on those 39.
* **One-sided, alpha 5%.** Consistent with the note's sample-size constant
  `(z_0.95 + z_0.80)^2/4 = 1.5464`: a POSITIVE edge is the only thing that could justify
  promotion, so the test asks only in that direction and refuses everything else, including
  honestly negative samples.

`Decimal` for every rate, price and edge -- they derive from money -- and `float` ONLY
inside `p_value`, exactly as `deflate.py` documents its probabilities: the normal CDF is a
rational approximation with no exact form, and none of this is money.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from keel.research.deflate import inverse_normal_cdf, normal_cdf
from keel.research.throughput import design_effect, detectable_edge, n_eff

#: The two fee regimes the reconstruction is priced at. Labels, not truth: the driver threads
#: the deployment's own `config.fees.taker_pct` when it has one, and these constants stand in
#: for callers that genuinely have no config (library use, tests). `outside_allowance_taker`
#: matches `backtest.TAKER_FEE_PCT` (120 bp, both legs); `inside_allowance_fee_free` is the
#: rail-14 allowance regime where the venue charges nothing on monthly BUY notional up to the
#: cap -- the profitability boundary, not a budget to spend.
FEE_REGIMES: dict[str, Decimal] = {
    "outside_allowance_taker": Decimal("0.012"),
    "inside_allowance_fee_free": Decimal("0"),
}

#: One trade outcome as `(outcome, pnl, r_multiple)` -- the shape of `Trade` rows a
#: `backtest` result carries, minus everything the math does not read. `pnl`/`r_multiple`
#: are `Decimal | None` because an OPEN trade has neither yet; they are read only for
#: "win"/"loss" rows, which always have them. "scratch" and "open" rows count toward
#: nothing: a scratch is not evidence of skill either way, and an open trade has no outcome.
OutcomeRow = tuple[str, Decimal | None, Decimal | None]


@dataclass(frozen=True)
class FamilySignificance:
    """One family's edge, measured against its own break-even at one fee regime.

    Every Decimal field is derived from the trades' money; `p_value` is the only float
    (see the module docstring). `n_effective` is `throughput.n_eff(n_trades)` -- raw `n`
    appears beside it in the report but is never used as a sample size.
    """

    family: str
    fee_regime: str
    fee_pct: Decimal
    n_trades: int
    wins: int
    win_rate: Decimal
    payoff_b: Decimal
    break_even: Decimal
    edge: Decimal
    n_effective: Decimal
    edge_z: Decimal
    p_value: float
    edge_ci_low: Decimal
    detectable_edge: Decimal
    verdict: str


def z_alpha() -> Decimal:
    """The one-sided 95% critical value, `z_0.95 ~ 1.645`, as a Decimal.

    One-sided alpha 5% is the convention the note's sample-size constant already assumes
    (`(z_0.95 + z_0.80)^2/4 = 1.5464`: one-sided 95% confidence, 80% power). This module's
    test and that planning formula therefore answer compatible questions.
    """
    return Decimal(str(inverse_normal_cdf(0.95)))


def se_null(break_even: Decimal, n_effective: Decimal) -> Decimal:
    """Standard error of a win rate under the null, `sqrt(p(1-p)/n_eff)`.

    `p` is the BREAK-EVEN, not the observed win rate: the question is how far the observed
    rate scatters when the family is exactly worthless, so the variance is the null's.
    `n_effective` is effective observations, never raw n (#427).
    """
    if n_effective <= 0:
        raise ValueError("n_effective must be > 0")
    return (break_even * (Decimal(1) - break_even) / n_effective).sqrt()


def significance(
    family: str,
    fee_regime: str,
    fee_pct: Decimal,
    outcomes: Sequence[OutcomeRow],
) -> FamilySignificance:
    """Measure one family's edge over its break-even at `fee_pct`.

    `outcomes` is the reconstructed trade list as `(outcome, pnl, r_multiple)` rows; the
    fee has already been charged inside `pnl` by whoever produced the trades (the driver
    runs `backtest` twice, once per regime) -- this function prices nothing, it only reads.

    `edge_ci_low` reuses the null standard error -- evaluated at the sample-estimated
    break-even rather than at the observed win rate -- a score-style choice, immaterial at
    these n_eff.

    Degenerate samples are answered, never smoothed over: no closed trades is
    "insufficient_n"; a family with no losses has break-even 0 and an edge the null cannot
    explain (reported as z = +infinity rather than a crash); a family with no wins has
    break-even 1 and z = -infinity. Both extremes are artefacts of tiny samples, and the
    verdict vocabulary says what happened rather than hiding it.
    """
    # Wins/losses carry pnl by construction (the backtest sets it on every close); scratch
    # and open rows fall through to nothing. A win/loss row without a pnl is a data error
    # and is named, not coerced.
    win_pnls: list[Decimal] = []
    loss_pnls: list[Decimal] = []
    for outcome, pnl, _r in outcomes:
        if outcome not in ("win", "loss"):
            continue
        if pnl is None:
            raise ValueError(
                f"a {outcome!r} row reached the payoff math without a pnl -- only "
                "'open' trades have none, and those are excluded here"
            )
        (win_pnls if outcome == "win" else loss_pnls).append(pnl)
    n_trades = len(win_pnls) + len(loss_pnls)

    if n_trades == 0:
        return FamilySignificance(
            family=family,
            fee_regime=fee_regime,
            fee_pct=fee_pct,
            n_trades=0,
            wins=0,
            win_rate=Decimal(0),
            payoff_b=Decimal(0),
            break_even=Decimal(0),
            edge=Decimal(0),
            n_effective=Decimal(0),
            edge_z=Decimal(0),
            p_value=1.0,
            edge_ci_low=Decimal(0),
            detectable_edge=Decimal(0),
            verdict="insufficient_n",
        )

    win_rate = Decimal(len(win_pnls)) / Decimal(n_trades)

    # Payoff b: average |win| over average |loss|, from the SAME trades. b needs both
    # sides; each one-sided sample gets the honest degenerate answer instead of a crash.
    if not win_pnls:
        payoff_b = Decimal("0")  # never won: break-even 1, the least flattering null
    elif not loss_pnls:
        # Never lost: |loss| is 0 and no finite b exists. break-even 0 follows exactly.
        payoff_b = Decimal("Infinity")
    else:
        avg_win = sum((abs(pnl) for pnl in win_pnls), Decimal(0)) / Decimal(len(win_pnls))
        avg_loss = sum((abs(pnl) for pnl in loss_pnls), Decimal(0)) / Decimal(len(loss_pnls))
        payoff_b = avg_win / avg_loss if avg_loss != 0 else Decimal("Infinity")
    break_even = Decimal(1) / (Decimal(1) + payoff_b)

    edge = win_rate - break_even
    n_effective = n_eff(Decimal(n_trades))
    se = se_null(break_even, n_effective)

    if se == 0:
        # Degenerate null (break-even 0 or 1): zero variance, so any non-zero edge is
        # infinitely many standard errors away. Say that instead of dividing by zero.
        edge_z = (
            Decimal("Infinity") if edge > 0 else Decimal("-Infinity") if edge < 0 else Decimal(0)
        )
    else:
        edge_z = edge / se
    p_value = 1.0 - normal_cdf(float(edge_z))

    verdict: str
    if p_value <= 0.05:
        verdict = "distinguishable"
    else:
        verdict = "not_distinguishable"

    return FamilySignificance(
        family=family,
        fee_regime=fee_regime,
        fee_pct=fee_pct,
        n_trades=n_trades,
        wins=len(win_pnls),
        win_rate=win_rate,
        payoff_b=payoff_b,
        break_even=break_even,
        edge=edge,
        n_effective=n_effective,
        edge_z=edge_z,
        p_value=p_value,
        edge_ci_low=edge - z_alpha() * se,
        detectable_edge=detectable_edge(n_effective),
        verdict=verdict,
    )


def render_family(stat: FamilySignificance) -> list[str]:
    """Report lines for one `FamilySignificance`.

    Always shows the fee regime, raw n BESIDE n_eff (raw n never alone, #427), the payoff
    and break-even it implies, the observed win rate and edge with its one-sided 95% lower
    bound, the smallest edge this much evidence could detect, and the verdict in plain
    words -- including the honest shape for "no".
    """
    fee_bp = (stat.fee_pct * Decimal(10000)).quantize(Decimal("1"))
    if stat.fee_pct > 0:
        fee_phrase = f"the {fee_bp} bp taker fee"
    else:
        fee_phrase = "the fee-free allowance"

    if stat.verdict == "insufficient_n":
        verdict_line = (
            f"verdict: insufficient_n -- no closed trades at {fee_phrase}; "
            "nothing to test and nothing promoted"
        )
    elif stat.verdict == "distinguishable":
        verdict_line = f"verdict: distinguishable from zero at {fee_phrase}"
    else:
        verdict_line = f"verdict: not distinguishable from zero at {fee_phrase}"

    return [
        f"{stat.family} @ {stat.fee_regime} (fee {fee_bp} bp per leg):",
        f"  closed trades n={stat.n_trades} pooled -> {stat.n_effective.quantize(Decimal('0.01'))} "
        f"effective (design effect {design_effect().quantize(Decimal('0.001'))}, #427)",
        f"  payoff b={_fmt(stat.payoff_b)} -> break-even win rate {_fmt(stat.break_even)}; "
        f"observed {_fmt(stat.win_rate)} -> edge {_fmt(stat.edge)} points",
        f"  edge z={_fmt(stat.edge_z)}, one-sided p={stat.p_value:.4f}; "
        f"95% one-sided lower bound on the edge: {_fmt(stat.edge_ci_low)}",
        f"  smallest edge detectable at this n_eff: {_fmt(stat.detectable_edge)} "
        "(80% power, alpha 5%)",
        f"  {verdict_line}",
    ]


def _fmt(value: Decimal) -> str:
    """Plain number or the two infinities a degenerate payoff can produce."""
    if value.is_infinite():
        return "inf" if value > 0 else "-inf"
    return str(value.quantize(Decimal("0.0001")))
