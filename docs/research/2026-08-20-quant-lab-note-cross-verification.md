# Cross-verification of the Keel Quant Lab modelling note

**Contribution:** *Keel Quant Lab — Couche de modélisation stochastique pour Keel*
([PDF](Keel_Quant_Lab_note_pedagogique_260820_103629.pdf)), by **Dr. Issam Elhattab**, August 2026.

**This document:** what we checked, what held, what we found by running the note's own methods on
keel's real numbers, and how it sits against work keel had already published.

> **Version note.** Sections 1–7 below were written against **v0** (22 pp., 2026-08-20 05:39). The
> published PDF is now **v1** (27 pp., 2026-08-20 11:01), which adds a new §3 auditing the `keeks`
> implementation itself and renumbers everything after it. Section references below have been
> updated to v1 numbering; §8 covers what v1 adds. v0 is retained in git history at commit
> `0347bba` for internal reference and is not separately published.

Every figure below is reproducible from the repository and the cached candle data. Where a number
is an assumption rather than a measurement, it says so.

---

## 1. The mathematics

Re-derived rather than read. All of it holds.

| result | note | status |
|---|---|---|
| `f_N = ρ/s` | eq. 1 | ✓ loss at stop `N·s = ρ·W` |
| `κ = 2(φ+ψ)/s` | eq. 2 | ✓ |
| κ independent of `risk_pct` | Prop. 1 | ✓ and non-obvious — see §5 |
| `p_be = (1+κ)/(1+b)` | eq. 3 | ✓ |
| Kelly after costs | eq. 4 | ✓ re-derived from `g'(ρ)=0` |
| worked example (b=2, κ=0.10, p=45% → ρ*≈12%, notional 240%) | §4.4 | ✓ exact |
| κ / `p_be` table | §3.3 | ✓ every cell |
| `n ≈ 1.55/δ²` | eq. 6 | ✓ `(z₀.₉₅+z₀.₈₀)²/4 = 1.546` |
| sample-size table and durations | §5.2 | ✓ every cell |
| `g(λρ*) ≈ (2λ−λ²)g(ρ*)` → 75% growth at half Kelly | Annexe A | ✓ |

Claims about keel also check out: four rule families, `min_trades = 100`, PBO/CSCV present
(`keel/research/cscv.py`), eighteen rails, `risk_pct` a constant 1%. The central premise —
`docs/launch.md:60`, *no shipped rule family is net-positive at the taker fee actually paid,
0 of 90 and 0 of 82* — is stated accurately.

**One correction to our own first reading.** We initially reported the note's 268 / 940 / 49.4
figures as unsourced. They are sourced, in discussion #359: *49.4 signals per asset-year measured
over 5 years of cached Coinbase candles; median n=268 per rule-product; range 48.5 LTC to 57.0
BTC*. The error was ours.

---

## 2. Overlap with work keel had already published

Discussion **#368** (2026-08-18) analyses the same ten Keeks articles. Substantial overlap, which
matters only because the note does not cite it and a reader cannot tell deliberate divergence from
independent rediscovery:

| the note | #368 |
|---|---|
| §4.1 stake-vs-risk; tighter stop ⇒ bigger position | "One translation warning before comparing" |
| §2.3 OptimalF reservation | rejected, with the PBO/CSCV reason |
| §2.3 dynamic/streak sizing | rejected — "~250 sequential trades in one regime are not 250 independent draws" |
| §2.3 CPPI gap risk | covered, plus keel's spread gate |
| Annexe B, ten articles | "The series in ten lines" |
| headline: no edge ⇒ ρ=0 is correct | the framing of the whole post |

Three places they diverge and should be reconciled:

1. **#368 rejects a Merton γ knob outright** ("a guess with a dial on it"). The note keeps *Part de
   Merton* in its comparison table — defensible as a benchmark, but it should say so.
2. **Two diagnostics now compete for one slot.** #368 plans `kelly_f_star` at the promotion gate,
   report-only. The note proposes `π_edge`. Both, or one?
3. **#368 already spec'd a quarter-Kelly ceiling** (dormant) and **queued the (1−d/D) drawdown
   throttle** as a paper experiment. Neither appears in the note's Layer 3 table as keel's
   existing position.

---

## 3. What the note adds that keel did not have

#368 contains nothing quantitative on how costs move the break-even. The note supplies it, and at
keel's real fee that is the whole story.

`config.live-sandbox.yaml` sets `taker_pct: 0.012` — **120 bp**. The note's illustrative table uses
25–60 bp and says explicitly that measured costs must replace them. Doing so:

| stop `s` | κ | `p_be` at b=2 | `p_be` at b=6 |
|---|---|---|---|
| 2% | 1.25 | **75.0%** | 32.1% |
| 5% | 0.50 | **50.0%** | 21.4% |
| 10% | 0.25 | 41.7% | 17.9% |
| 15% | 0.17 | 38.9% | 16.7% |

The live rules run `target_rr: 6`, so the b=6 column is operative. A second consequence of the same
formulas: at b=6 and any `p` above ~28%, unconstrained Kelly demands **more than 100% notional**, so
the note's constraint (5) binds before the Full-vs-Half-Kelly question arises.

Also new: the power analysis, `π_edge`, hierarchical pooling and `n_eff`, the CUSUM erosion test,
and the portfolio layer.

---

## 4. Applying the note's methods: `n_eff` for the 2026-09-30 review

Discussion #359 schedules a pooled forward-trades review for 2026-09-30 at a floor of **n = 100**,
treating 100 pooled trades as 100 observations. The note's §6.2 warns that naive pooling assumes
independence. We measured the correction.

**Method.** Reconstructed the live rule on ONE_HOUR bars with its own parameters —
`entry_lookback: 40`, `adx_period: 14`, `adx_threshold: 25`, `atr_period: 20`, `atr_stop_mult: 2`,
`target_rr: 6` — resolving each entry to win/loss by first touch, across 25 products over 5 years of
cached candles.

**Validation.** The reconstruction reproduces keel's own published measurement, which is what makes
the rest usable:

| | reconstruction | #359 |
|---|---|---|
| signals per asset-year | 46.7 | **49.4** |
| median n per rule-product | 241 | **268** |

(Omitting the ADX gate gives 70.6/asset-year. The gate accounts for the difference.)

**Result.**

```
episodes (UTC days with >=1 signal):   1,355
size-weighted mean episode size k:      8.43
largest episode:                          24 assets firing the same day
ICC of outcomes within an episode:     0.212
design effect DEFF = 1 + (k-1)*rho:     2.58

n_eff at n = 100 pooled:                  39
detectable edge at 80% power:          20.0%    (12.4% if independent)
```

Signals fire in herds — about eight assets the same day — and those trades then win or lose
together. **A pooled sample of 100 carries roughly 39 independent observations.**

| to detect | n_eff | **pooled trades** |
|---|---|---|
| 12.4 pts | 101 | 259 |
| 7.5 pts | 275 | 708 |
| 5 pts | 618 | 1,593 |
| — | 100 | **258** — what "n=100" is assumed to deliver |

Caveats: DEFF is a **lower** bound (clusters are UTC days; these trades span many hours), the ICC is
from backtest outcomes, and `k` scales with the pool size.

---

## 5. The finding the note's framework produced that we did not expect

Rail 14 caps monthly BUY notional at the venue's **fee-free volume allowance**. Trades inside it pay
no taker fee. Applying `κ = 2(φ+ψ)/s` on each side of that boundary, at the measured median hourly
stop of 2.40% and `target_rr: 6`:

| regime | φ | κ | `p_be` | vs reconstructed 14.9% win rate |
|---|---|---|---|---|
| **inside allowance** | 0 | 0.042 | **14.88%** | **+0.02%** |
| inside, ψ = 2 bp | 0 | 0.017 | 14.52% | +0.38% |
| inside, ψ = 10 bp | 0 | 0.083 | 15.48% | −0.58% |
| **outside allowance** | 120 bp | 1.043 | 29.18% | **−14.28%** |

Treat +0.02% as coincidence — the win rate is a reconstruction and ψ is assumed. What survives the
whole plausible spread range is the shape: **indistinguishable from break-even inside the fee-free
allowance, decisively negative outside it.** The taker fee is the entire result.

This reframes rail 14. It is not a budget limit; it is the profitability boundary.

### The timeframe interaction

The hourly clock shortens the stop 5.8× (2.40% vs 13.86% median 2×ATR20), which by `f_N = ρ/s`
lengthens each position by the same factor, and by `κ = 2(φ+ψ)/s` multiplies the cost per unit of
risk by the same factor:

| bars | median stop | `f_N` at 1% | κ (outside) | `p_be` at b=6 |
|---|---|---|---|---|
| ONE_DAY | 13.86% | 0.07× | 0.18 R | 16.9% |
| **ONE_HOUR** | **2.40%** | **0.42×** | **1.04 R** | **29.2%** |

**On the hourly clock every round trip outside the allowance costs more than one full unit of
risk.** A 6R target nets 4.96R; a stop-out loses 2.04R. #359 presents hourly as a 23× acceleration
with "zero parameter changes" — the signal count is unchanged, the economics are not.

---

## 6. What this surfaced in the deployment

Both consequences were found by following the note's arithmetic, and both are now tracked:

- **Neither paper profile has ever recorded a trade.** `keel-paperhourly.db` and `keel.db` have zero
  rows in `trade_outcomes`, `positions` and `orders`. paper-hourly detected 15 setups in 60 cycles
  and entered none: 15 of 15 vetoed `subscription_unattested`, and the remainder would have failed
  the exposure caps anyway, because at `risk_pct` 1% on hourly stops a typical proposal is ~$4,212
  against a $2,500 per-asset cap — **0 of 25 products admissible**. (#426)
- **The cause is a synthetic-equity mismatch.** The profiles size against a synthetic $10k account
  while inheriting the *real* account's allowance. At the attested Basic tier that is $500/month, so
  one hourly proposal is 8.4× the entire month. Fixed by seeding from real broker equity (PR #430).
- **Throughput, not signal frequency, is the binding constraint.** At $500/month, 258 pooled trades
  is ~8 years away even after the fix. (#427)

---

## 7. Disposition

| item | disposition |
|---|---|
| J1 — κ / `p_be` table at real costs and stops | **adopt first.** Produced above in part; complete it per rule family. |
| J2 — power curves and time-to-decision | **adopt.** Already applied to the September review (§4). |
| `π_edge` | **promising, needs reconciliation** with #368's `kelly_f_star` before either is built. |
| Hierarchical pooling / `n_eff` | **adopt.** §4 is the first application; DEFF 2.58 is the number to carry. |
| CUSUM erosion detector | **defer.** Nothing to detect erosion in until a rule accrues trades. |
| Multi-asset portfolio layer | **defer.** Same reason. |
| OptimalF, streak sizing, Merton γ | **already rejected** in #368, with reasons. Keep as benchmarks only. |
| `ρ_final = min{...}` and "la modélisation propose, Keel dispose" | **endorsed.** Matches the existing guard architecture exactly. |

### Scope gap to close

The framework is built on `R`-multiples, which require a stop. `dca` has none — the live position
shows `NO bracket` — and it is the only family that has ever traded. The note should state that it
covers `turtle_breakout`, `pullback_continuation` and `rsi_meanrev`, and that DCA needs separate
treatment.

---

---

## 8. What v1 adds: an audit of the `keeks` implementation

v1 adds a new §3 that goes past reading the Keeks *articles* and audits the *code*, pinned to
`wdm0006/keeks` at commit `1a5d04a` (2026-08-18) — with the note that `version.py` there says 0.5.0
while PyPI still serves 0.3.0, so the public README lags the implementation. Pinning the commit is
the right instinct and matches how keel records its own experiment provenance.

Five findings. We have not re-run his numbers against the library, so these are reported as his
results, not independently confirmed — with one exception noted below.

| | finding |
|---|---|
| **K1** | The audited Kelly-with-costs implementation reduces **exactly** to the note's `ρ*` when `L=1, B=b, c=κ`. Verification table shows agreement to 0 or ~1e-16. |
| **K2** | The general break-even is `p_be = (L+c)/(B+L)`. A fixed 0.5 probability floor is the true break-even **only** at `B=L=1, c=0`. |
| **K3** | `KellyCriterion`'s default 0.5 gate zeroes positions that are economically positive. For `B=2` the true break-even is 1/3, so the band `1/3 ≤ p < 0.5` is admissible but suppressed — and `FractionalKellyCriterion` inherits it, since it delegates to an internal `KellyCriterion` with defaults and does not expose `min_probability`. |
| **K4** | `transaction_cost` (proportional, strategy side) and `transaction_costs` (absolute, simulator side) differ by one character and carry different units. Documented, but the published figures make the gap tangible: at `cost_input = 0.01`, realised fee is 0.00046% of stake for Kelly. |
| **K5** | `BankRoll.max_draw_down` is **not** peak-to-trough drawdown. It is the maximum percentage losable in a *single settlement*, raising `RuinError`. |

**K1 is a third independent confirmation of the same formula.** He derived it from the library, we
re-derived it from `g'(ρ)=0` (§1), and the note derived it analytically. Three routes, one result.

**K3 is the substantive finding**, and he evidences it from the project's own published artefact:
in `strategy_benchmark.csv` the "Kelly" and "Naive" rows are identical to every decimal — median
terminal capital 12,233.6, median drawdown 0.8091 — so a nine-strategy benchmark is effectively
comparing eight. He also states the fair reading himself: at `B=L=1, c=0` the gate coincides with
break-even, so the published benchmark is not wrong; the degeneracy shows for asymmetric payoffs
such as `b=2`, which that benchmark does not cover. That scrupulousness is worth naming.

**K5 matters directly to any Keel–Keeks integration.** keel's drawdown rails (rail 11, 20% total /
8% weekly) are peak-to-trough. Mapping them onto `max_draw_down` would encode a different object
entirely — a per-settlement loss ceiling. His published evidence: under an 8% ceiling, Full Kelly
halts in 100% of paths while Half Kelly and 2% fixed fraction never do. Anyone wiring the two
together must convert explicitly or use the parameter only for what it is.

### Disposition of the four proposed upstream PRs

He proposes contributing back (issues are restricted on that repo; PRs are the documented path):

| | | our view |
|---|---|---|
| PR-1 | probability gate and Naive/Kelly degeneracy | **strongest candidate.** Behaviour change locked by existing tests, so it needs a deprecation path — he says so. |
| PR-2 | cost-parameter naming | ergonomic, low risk |
| PR-3 | finite-sample inference: Beta–binomial posterior, credible interval, `π_edge` | this is the note's own contribution, upstreamed |
| PR-4 | Bayesian position sizing | depends on PR-3 |

These are contributions to `keeks`, not to keel, and keel takes no position on whether that project
wants them. Worth recording that the sizing layer proposed for keel would rest on PR-3 landing, or
on keel implementing the inference itself.

### What v1 does not yet address

The two asks from §6 and §7 stand: it still does not cite #368, and the `R`-multiple framework
still does not say that `dca` — the only family that has ever traded, and one with no stop — is out
of scope.


## References

- Contribution: [Keel Quant Lab note (PDF)](Keel_Quant_Lab_note_pedagogique_260820_103629.pdf), Dr. Issam Elhattab
- #368 — Keel meets Kelly: what ten money-management writeups taught an engine that refuses to bet
- #359 — ~940 signals/year: from 2 trades per asset-year to a collectable evidence pipeline
- #304 / `docs/launch.md` — the honest measured result
- #426, #427, #430 — work arising from this review
