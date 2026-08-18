# Money-management and fiqh source review — findings and recommendations

**Date:** 2026-08-18 · **Prepared for:** the operator · **Status:** findings and proposals only —
nothing here changes sizing code, rails, or attestations. Every actionable item is gated the way
this repo gates everything: measurement first, then a reviewed PR, then evidence before live.

**Method.** Eleven sources were read and mapped against keel's actual architecture
(`execution/sizing.py`, `execution/guards.py`, `strategy/promotion.py`, the experiments record):
ten posts from Will McGinnis's money-management series on the `keeks` betting library
(2025-04 → 2026-03), *Fortune's Formula* (Poundstone, 2005), and a 29-page Arabic research
paper prepared for the International Islamic Fiqh Academy (Jeddah) 2019 electronic-transactions
seminar: *أنواع المعاملات الرقمية المشفرة* ("Types of Encrypted Digital Transactions") by
Dr. Mu'taz Abu Jib and Prof. Ashraf Hashem (ARSI). Sources are listed at the end.

## Executive summary

1. **The most important finding is confirmatory, not new: applied to keel's measured edges, the
   Kelly-optimal bet is zero or a small negative number — which is exactly what keel already
   does.** No shipped rule family is net-positive at the venue's taker fee
   ([the honest result](../experiments/2026-08-13-restated-under-a-production-faithful-engine.md)),
   so every Kelly-variant formula in this literature, run on keel's own numbers, endorses keel's
   refusal to trade live. *Fortune's Formula* states the degenerate case plainly: on a fair coin,
   the Kelly bet is zero. The literature's "Kelly needs accurate probability estimates" is keel's
   promotion gate restated (edge floors + PBO + n≥100 pooled with a diversity floor).
2. **Keel's fixed-fractional risk sizing is the strongest member of the fixed-fraction family the
   series covers.** keel risks a fixed fraction of *equity* over the *stop distance*
   (`size = equity × risk_pct / |entry − stop|`), not a flat fraction of bankroll per bet; the
   series' own conclusion — optimal fixed fractions sit *below* Kelly, start at 1–2% — matches
   keel's `risk_pct = 0.01` with notional caps binding separately.
3. **Two adoptions are worth building when evidence exists** (R1 now, R2 later): a
   Kelly-*diagnostic* in promotion/simulate output (report-only), and a quarter-Kelly *ceiling*
   on the live path that stays dormant until a rule actually clears the promotion gate.
4. **One experiment is worth running on the hourly profile** (R3): drawdown-throttled sizing as a
   graduated complement to rail 11's binary halt — measured through the trials ledger before any
   live-path proposal, with the series' own "slower recovery" cost stated.
5. **Three ideas should be explicitly rejected** (R5): Optimal-f, streak-driven dynamic sizing,
   and a Merton/CRRA risk-aversion knob — each for reasons this repo's own machinery already
   encodes.
6. **The fiqh paper independently argues for keel's compliance architecture.** Its central
   conclusion — crypto-assets are a wide spectrum of materially different instruments, so no
   ruling should issue until the asset is *precisely defined*, per type, by qualified sources —
   is the fiqh-side statement of keel's per-product, attested, fails-closed screening. Its
   recommendation to route rulings through AAOIFI/IFSB standards names the attestation sources
   keel's `fiqh-basis.md` reading list should watch. It is a taxonomy-and-method paper, **not** a
   permissibility ruling, so it cannot stand alone as an attestation source.

## Part 1 — keel's existing stack, translated into the series' vocabulary

| Series concept | keel's implementation | Verdict |
|---|---|---|
| Fixed fraction (`bet = f × bankroll`) | `sizing.size`: risk `risk_pct` (1%) of equity over stop distance; DCA uses budget sizing | Stronger variant — risk-defined, not stake-defined |
| Fractional Kelly (¼–½) | Not implemented — no edge estimate is trusted enough to size on | Correct while no rule clears the gate (see R1/R2) |
| Drawdown-adjusted Kelly (`(1 − d/D) × bet`) | Rail 11: binary halt at total 20% / weekly 8% drawdown ceilings | Binary version; graduated version is R3 (experiment first) |
| CPPI (floor + multiplier) | Rail 11 ceiling ≈ floor at 80% of high-water mark; halt at the floor | Same protective goal, halt semantics; documented framing is R4 |
| Dynamic bankroll (streak modifiers) | Rail 16: consecutive-loss breaker (halts); no-martingale/no-stop-widening rails | Loss half exists as a halt; win-streak sizing rejected (R5) |
| Naive/flat baseline | `simulate` benchmarks against DCA (a fixed-budget naive strategy) | Methodological parity; optional extra arm in R6 |
| Kelly's estimation-error caveat | Promotion gate: edge floors, PBO/CSCV overfitting check, n≥100 pooled with ≥5-product × 10-trade diversity floor | The literature's caveat, made structural |
| Ruin protection | Fixed-fraction multiplicative sizing + per-order/per-day caps + exposure $5k + concentration 50% + no leverage | Beyond the series' model |

One translation warning that matters when reading the series from keel's side: the posts size a
**stake** (fraction of bankroll wagered, binary win/loss); keel sizes a **risk** (fraction of
equity lost if the stop holds, with notional bounded separately by caps). A keel trade's notional
can be a large multiple of its risk when the stop is tight — the README already warns
"a tighter stop produces a LARGER position". Kelly comparisons against keel must therefore be
made on risk fractions, not notional.

## Part 2 — Findings per source

**Kelly criterion** (`f* = (p·b − q)/b`). Maximizes log-wealth growth; bet more with bigger edge,
nothing without one; full Kelly is volatile and brutally sensitive to estimation error. *keel
reading:* the formula's inputs (p, b) are exactly what keel refuses to estimate loosely — and at
keel's measured edges the numerator is ≤ 0.

**Keeks 0.3.0 / Merton share** (`f* = μ/(γσ²)`, γ = 1 Kelly-like, γ = 2 "empirically typical").
The CRRA framing is the honest generalization of "how much risk do you actually want". Its own
simulation: γ = 2 keeps 84% of returns with 61% less volatility. *keel reading:* γ is a parameter
with no evidence to tune it here; keel's fixed 1% risk is dynamically more conservative than any
γ at thin edges. Rejected as a knob (R5), retained as vocabulary.

**Fractional Kelly** (`f × f*`; G(f) ≈ r + f·K − f²K/2; half Kelly ≈ 75% of growth at half
variance). The standard practical concession to estimation error; recommended 25–50% for
individuals, 10–20% for professionals. *keel reading:* this is the **shape of R2** — a ceiling,
never a target, and only once an edge survives the gate.

**Drawdown-adjusted Kelly** (`(1 − d/D) × Kelly`). Linear de-risking toward zero at the maximum
acceptable drawdown D. Costs: suboptimal growth, slower recovery, parameter sensitivity.
*keel reading:* rail 11 already guarantees the D-bound by halting; the question R3 poses is
whether graduating *toward* the halt beats jumping to it — measurable on the hourly profile.

**Optimal-f (Vince).** Maximize `TWR = Π(1 + f·Rᵢ)` over the *actual trade history*, numerically.
The posts' own caveats: unreliable under ~30–50 trades, "often more aggressive than Kelly",
use a 50–70% safety factor. *keel reading:* backward-looking growth maximization on a small
sample is what the PBO/CSCV gate exists to catch. Rejected (R5).

**Fixed fraction** (`bet = f × bankroll`). Simple, no estimates, ruin-proof, slow recovery,
ignores edge strength. *keel reading:* keel's default posture, done more precisely. The series'
"start at 1–2%" matches `risk_pct = 0.01`.

**CPPI** (`bet = m × (bankroll − floor)`). Floor protection with multiplier exposure; gap risk can
breach the floor; cash drag. *keel reading:* rail 11's ceiling *is* a floor at 80% of high-water
mark with halt-at-floor semantics; keel's gap risks (thin books, weekend moves) are mitigated by
the #350 spread gate and zero leverage. Framing worth stating in the runbook (R4).

**Dynamic bankroll management** (`base × streak modifier`, bounded). Win-streak raises, loss-streak
cuts over a 3–5 lookback. The post's own cons: overreaction to variance, overconfidence on lucky
streaks, parameter sensitivity, backtesting difficulty. *keel reading:* loss-streak protection
already exists as rail 16's halt; win-streak sizing is the psychology the repo's own hourly
caveat warns about — ~250 sequential same-regime trades are not independent draws, so a streak is
not evidence of anything. Rejected (R5).

**Naive strategy / strategy comparison.** Flat betting as a baseline; the comparison post maps
risk tolerance → strategy and honestly shows its simulations are single-seed, cost-free, and
binary-outcome. *keel reading:* keel's DCA benchmark already plays the naive-baseline role, and
keel's own cost-faithful measurements dominate the series' simulation-based claims. The series'
methodological honesty (state the baseline, same random seed) is worth imitating in any R3
experiment design.

***Fortune's Formula* (Poundstone).** The history: Kelly (1956, Bell Labs), Shannon, Thorp's
blackjack and Princeton-Newport, and the cautionary counter-example — LTCM, which leverage plus
estimation overconfidence destroyed when a half-Kelly posture would not have been. Reviewers'
distillation matches the math: the formula "reduces the risk of ruin" **only for those with a
genuine edge; on a fair coin the Kelly bet is zero**. *keel reading:* keel's posture — no
leverage, sizing on actual cash, no live trading without a proven edge — is the book's lesson
operationalized. The "dark side" chapter (the formula serving insider edges) underlines why keel
claims no edge and measures instead.

**The Fiqh Academy paper (Abu Jib & Hashem 2019).** A taxonomy of encrypted digital instruments —
mined currencies (Bitcoin-generation), utility tokens (Filecoin's ICO worked example), security/
equity tokens (tZERO), protocol/platform tokens, asset-backed types — classified also by chain
type (public/private), issuing authority, and backing. Its principal finding: the term "digital
currencies" spans instruments so different in structure, characteristics, and substance that
**no ruling should issue until the asset under consideration is defined precisely and
comprehensively (تعريفاً دقيقاً جامعاً مانعاً)**, type by type; the new generations demand
deeper Shariah study than the first generation received. Its two recommendations: (1) a standing
committee of jurists, Shariah researchers, and fintech-literate economists to rule per type;
(2) Shariah *standards* for these assets via AAOIFI and the IFSB, analogous to existing standards
for tangible/intangible assets. *keel reading:* this is keel's compliance model argued from the
fiqh side — classification is per-instrument, supplied by qualified attribution, never inferred;
an absent classification is a rejection, not a default pass. See Part 4.

## Part 3 — Recommendations (ranked)

### R1 — Kelly diagnostic in promotion and simulate output *(adopt now; report-only)*

Compute, from a backtest's own fills, the empirical win rate `p` and payoff ratio
`b = avg_win / avg_loss`, derive `f* = (p·b − q)/b`, and **print** in `rules promote` /
`rules backtest` / `simulate` reports: `kelly_f_star = …; risk_pct 1% = …% of f*` (and `f* ≤ 0:
no edge to size on` when negative). Zero behavior change; it makes the sizing-versus-edge
relationship visible at exactly the moment a human decides whether to promote, and it restates
the honest result in sizing vocabulary. Small, testable, one PR.

### R2 — Quarter-Kelly ceiling on the live path *(spec now; implement only when a rule promotes)*

When a rule first clears the promotion gate, its live risk fraction should be capped at
`min(risk_pct, 0.25 × f*_forward)` — quarter-Kelly, the series' individual-investor
recommendation — computed on *forward* (paper/live) fills, not the backtest that earned the
promotion, and re-derived as evidence accrues. Fail-closed like rails 12/13/17: no computable
`f*` → the cap falls back to `risk_pct`. Until any rule promotes, this is dead code; writing the
spec now (this document) is enough, and implementation should wait for the first credible live
candidate so the knob ships with an edge to bind it.

### R3 — Drawdown-throttled sizing on the hourly profile *(experiment first, via the trials ledger)*

Test, in paper only: between a soft floor (e.g. 8% drawdown) and rail 11's existing 20% ceiling,
scale effective risk as `(1 − d/D) × risk_pct`, with rail 11's halt unchanged at the ceiling.
Record as a `threshold_nudge` trial in the experiments ledger; judge on drawdown distribution,
recovery time, and forgone winners over ≥ n=100 pooled signals. Stated costs, from the series
itself and from keel's own caveat: slower recovery, parameter sensitivity, and a throttle tuned
on same-regime sequences. No live-path proposal unless the measured trade-off is decisively
favorable.

### R4 — State the live account's floor semantics in CPPI terms *(documentation only)*

The operator runbook should say what rail 11 already implies: the 20% total-drawdown ceiling is a
hard floor at 80% of the high-water mark; below it, exposure is zero (CPPI with halt-at-floor
semantics, multiplier effectively 0 past the floor — deliberately not the m > 0 continuous form,
which re-risks into a drawdown). Include the gap-risk note and its mitigations (no leverage,
#350's spread gate, per-order caps). One paragraph, no code.

### R5 — Explicit rejections *(recorded so they are not re-proposed every time this literature is read)*

- **Optimal-f.** Backward-looking TWR maximization on ≤ 100-trade samples is overfitting bait;
  the PBO/CSCV gate already plays the "is this history real" role with sequence-aware methodology;
  the source itself concedes unreliability under 30–50 trades and prescribes a 50–70% safety
  factor — a fudge factor, not a control.
- **Streak-driven dynamic sizing.** Raising size on win streaks optimizes for the psychology of
  confidence, not for evidence; keel's own hourly caveat (sequential same-regime trades are not
  independent draws) makes a streak exactly the non-evidence it would size on. The protective
  half already exists as rail 16's consecutive-loss halt.
- **A Merton/CRRA γ knob.** A continuous risk-aversion parameter with no data to tune it adds a
  knob whose every setting is a guess; keel's fixed 1% risk with hard caps is already more
  conservative than any plausible γ at current edges. Revisit only if forward evidence ever
  makes γ identifiable — the same bar R2 sets.

### R6 — Optional: a fixed-fraction benchmark arm in `simulate` *(low priority)*

`simulate` already benchmarks against DCA. Adding a flat 2%-of-equity-per-signal arm (the
series' baseline discipline: same fills, same costs, same seed-equivalent determinism) would let
future threshold experiments state their baseline the way the comparison post does. Nice-to-have;
not scheduled.

## Part 4 — The fiqh source and keel's compliance architecture

The Abu Jib & Hashem paper's central methodological demand — *define the instrument precisely,
per type, before ruling; different types are materially different things* — is satisfied in keel
by construction: `keel assets attest` records a classification per `(venue, product_id)` with an
attributed human source, screening computes only market facts, and an absent attestation is a
rejection (KB §28.4/§65.5; `compliance/screen.py`). The paper's call for per-type rulings by
qualified bodies names the *kind* of source an attestation should cite: AAOIFI/IFSB standards as
they issue, academy seminar resolutions, or a qualified scholar's attributed position — never a
code-derived guess.

Recommended actions, all operator-level:

1. **Add the paper to `docs/fiqh-basis.md`'s reading list** as supporting the taxonomy-per-asset
   method (it is a method paper for the Fiqh Academy's seminar, not a permissibility ruling, so
   it *supports* attestations' framing rather than serving as one).
2. **Watch AAOIFI/IFSB for crypto-asset Shariah standards** — the paper's recommendation #2.
   When such a standard issues, it becomes the natural attributed source for allowlist
   attestations, and `fiqh-basis.md` should record its arrival.
3. **No screening changes.** The paper confirms the existing split (computed market facts vs
   attested classification); nothing in it argues for new rails or new inference.

## Part 5 — What this review deliberately does not change

No sizing formula, rail, gate, or attestation changes as a result of this document. The honest
result stands unmodified: at measured edges, every Kelly-variant in this literature says what
keel already practices — do not bet. The adoptions proposed (R1 now, R2 later, R3 as a paper
experiment, R4 as a paragraph) are instrumentation and documentation around that posture, not
departures from it.

## Sources

1. McGinnis, W. — *Kelly Criterion* (2025-04-01): https://mcginniscommawill.com/posts/2025-04-01-kelly-criterion/
2. McGinnis, W. — *keeks 0.3.0 release* (Merton share; 2025-10-15): https://mcginniscommawill.com/posts/2025-10-15-keeks-0_3_0-release/
3. McGinnis, W. — *Fractional Kelly* (2026-01-16): https://mcginniscommawill.com/posts/2026-01-16-fractional-kelly/
4. McGinnis, W. — *Drawdown-Adjusted Kelly* (2026-01-23): https://mcginniscommawill.com/posts/2026-01-23-drawdown-adjusted-kelly/
5. McGinnis, W. — *Optimal-f* (2026-01-30): https://mcginniscommawill.com/posts/2026-01-30-optimalf/
6. McGinnis, W. — *Fixed Fraction* (2026-02-06): https://mcginniscommawill.com/posts/2026-02-06-fixed-fraction/
7. McGinnis, W. — *CPPI Bankroll Management* (2026-02-13): https://mcginniscommawill.com/posts/2026-02-13-cppi/
8. McGinnis, W. — *Dynamic Bankroll Management* (2026-02-20): https://mcginniscommawill.com/posts/2026-02-20-dynamic-bankroll-management/
9. McGinnis, W. — *Naive Strategy* (2026-02-27): https://mcginniscommawill.com/posts/2026-02-27-naive-strategy/
10. McGinnis, W. — *Strategy Comparison* (2026-03-06): https://mcginniscommawill.com/posts/2026-03-06-strategy-comparison/
    (series code: `keeks`, https://github.com/wdm0006/keeks)
11. Poundstone, W. — *Fortune's Formula: The Untold Story of the Scientific Betting System That Beat
    the Casinos and Wall Street* (Hill & Wang, 2005).
12. أبو جيب، معتز & هاشم، أشرف — *أنواع المعاملات الرقمية المشفرة* ("Types of Encrypted Digital
    Transactions"), research paper for the International Islamic Fiqh Academy (Jeddah) Seminar on
    Electronic Transactions, 9–11 September 2019 (ARSI). Local copy:
    `~/Documents/eBooks/Tradings/QtlrYLgYY_260818_124605.pdf`.
