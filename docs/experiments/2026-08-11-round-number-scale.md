# `is_round_number` had no sense of scale — the fix, and what it costs live scoring

**Date:** 2026-08-11
**Issue:** #225 (surfaced as a side effect of #208 / PR #224)
**Change:** `keel/analysis/levels.py::is_round_number` — a **correctness fix to shipped scoring
code**, not a research finding. This write-up exists because the fix changes live CTS scores and
#225 requires a before/after rather than a drive-by patch.
**Harness:** `keel/research/cts_factors.py`, built for #208 and **reused unchanged**. No second
instrument was written; the whole point of having put the replay in the package was that the next
question could be asked with it.
**Script:** `docs/experiments/2026-08-11-round-number-scale.py` — produced every number below.
**Ledger:** one row, `round-number-scale-2026-08-11`, session `round-number-scale-2026-08-11`.

**Verdict: the factor is salvageable, the fix is safe to ship, and the reason it is safe is not
the reason anyone expected.**

| question #225 asked | answer |
|---|---|
| P(present) materially below 1.0 on 2dp assets? | **yes** — 1.0000 → **0.0358 / 0.0334 / 0.0463** on BTC/ETH/PAXG |
| broadly comparable across the allowlist? | **yes** — cross-asset spread **5.26× → 1.39×** |
| CTS distribution shift? | pooled mean **5.145 → 4.566**; BTC/ETH/PAXG each lose ≈**0.96** of a point, ADA/XLM ≈**0.16** |
| does it push qualifying setups below a gate? | **no — there is no CTS gate to fall through** (see §4) |
| bars whose entry technique changes | **1,103 of 6,827 (16.2%)**, every one of them one rung *down* |

---

## 1. The defect

```python
def is_round_number(price: Decimal, step: Decimal = Decimal("0.005")) -> bool:
    remainder = price % step
    distance = min(remainder, step - remainder)
    return distance <= step * Decimal("0.1")
```

`step` is an **absolute** half-cent. Coinbase quotes BTC-USD, ETH-USD and PAXG-USD to two
decimals, and `0.01 = 2 × 0.005`, so every quotable price is an exact multiple of `step`,
`remainder` is exactly zero, and the function returns `True` unconditionally. `distance <= step *
0.1` never got a chance to be false.

Measured over the daily candle cache (6,827 bars, expanding window, the live path's own):
P(present) = **1.0000 / 1.0000 / 1.0000** on BTC / ETH / PAXG, **0.2170** on ADA, **0.1901** on
XLM. `round_number_proximity` is weight 1 of `DEFAULT_WEIGHTS`' 14, so three of the five live
allowlist assets carried an unconditional **+1 on every CTS score**.

A constant is worse than a redundant factor: a redundant factor at least varies. And because the
constant applied to three assets and not the other two, the CTS *total* was not comparable across
the allowlist — BTC's mean sat 1.44 points above XLM's, of which 0.82 was this artifact.

## 2. What "round" was made to mean, and why

**A round handle is a price with few significant figures.** That is what makes a number watchable
— 65,000, 3,400, 0.38 — and it is a property of the price *relative to its own magnitude*, which
is exactly the property an absolute constant cannot have. So:

```
spacing  = 10 ** (floor(log10(price)) - 1)     # the two-significant-figure grid
distance = distance from price to the nearest multiple of spacing
present  ⟺ distance <= spacing * tolerance     # tolerance default 0.02
```

Three choices in there, each of which was decided rather than defaulted into.

**Two significant figures, not three.** Two is what the words mean: 65,000 and 0.38 are handles,
65,100 and 0.381 are not, and both pairs stand in the same relation to their own price. It is
also the choice that survives measurement. A three-significant-figure grid pushes the spacing down
onto ADA's and XLM's quote increment, and tick quantization then drives the base rate rather than
the price does: measured, ADA reaches **0.2996** against BTC's **0.1861** at the same tolerance —
a 1.61× spread manufactured by nothing but quote precision. That is the same class of artifact
this fix exists to remove, so three figures was rejected.

**Tolerance is a fraction of the handle SPACING, not of price — and the denominator is the whole
argument.** Both denominators scale with the instrument, so both fix the reported bug. They differ
in what they hold constant:

- *Fraction of price* makes the presence rate depend on where in the decade the price sits. The
  spacing is 10% of price just above a power of ten and 1% of it just below, so the identical rule
  would fire ten times as often on BTC at 99,000 as at 10,500, and the factor would silently
  change meaning as an asset trended through a decade. BTC's daily history spans 15,760 to
  124,720 — two decade crossings — so this is not hypothetical.
- *Fraction of spacing* makes P(present) identically `2 × tolerance` for any price series smooth
  on the scale of the grid, independent of price, decade position and quote precision alike.

The second is precisely #225's acceptance criterion — *the factor must mean the same thing at
65,000 as at 0.38* — restated as an invariant instead of a hope. §3 shows it holds empirically.

**No venue handle, deliberately.** #225 floats deriving the grid from `quote_increment`. Rejected:
`assemble_cts_context` is a **pure** function of `(setup, candles)` and #224's offline replay —
the very instrument measuring this change — depends on that. Threading venue state into an
`analysis.*` primitive would make the scoring path unreplayable to fix a factor that does not need
it. It is also conceptually wrong: tick size is a venue's quoting rule, and a psychological handle
is a property of the number, identical on any venue that lists the asset.

**Tolerance = 0.02 is the one genuinely free parameter, and it is pinned, not fitted to P&L.**
The ladder, measured over the same closes:

| tolerance | BTC | ETH | PAXG | ADA | XLM | pooled | spread | `64975.78` |
|---:|---:|---:|---:|---:|---:|---:|---:|:--|
| 0.10 | 0.1953 | 0.1953 | 0.1961 | 0.2146 | 0.2097 | 0.2033 | 1.10× | `True` |
| 0.05 | 0.0925 | 0.0871 | 0.1089 | 0.1117 | 0.1057 | 0.0998 | 1.28× | `True` |
| **0.02** | **0.0368** | **0.0357** | **0.0414** | **0.0436** | **0.0425** | **0.0397** | **1.22×** | **`False`** |
| 0.01 | 0.0162 | 0.0195 | 0.0218 | 0.0267 | 0.0212 | 0.0210 | 1.64× | `False` |

⚠️ **Honest note: `0.10` is arguably the better number and it was not chosen.** It preserves the
original docstring's stated intent ("within 10% of the step size") with only the *step* corrected,
it gives the tightest cross-asset spread in the table (1.10×), and it lands the base rate at
0.195–0.215 — almost exactly where ADA and XLM already sat, making the fix minimally disruptive on
the two assets that were never broken. It was rejected on one ground: #225 names `Decimal(
"64975.78")` as a price that **must** score absent, and at `0.10` the band on BTC is ±100 and
64,975.78 (24.22 from the 65,000 handle) scores present. That case pins the tolerance below
0.0242. `0.02` is the round number under that ceiling.

Whether 24.22 dollars from 65,000 — 3.7 basis points, a fraction of a daily range — is really
"far from any round handle" is a judgement I did not feel entitled to overturn on a change to live
scoring. **If the intent was the looser band, `tolerance=0.10` is a one-character change and this
table is the evidence for it.** Flagged in §6.

## 3. Result — P(present) before and after, same bars

Unconditional sample, ONE_DAY, expanding window from the first cached bar (which reproduces the
live path exactly: `agent.run_once` → `repo.get_candles` with no bounds → `engine.evaluate`).

| asset | N | before | after | Δ |
|---|---:|---:|---:|---:|
| BTC-USD | 1,648 | 1.0000 | **0.0358** | −0.9642 |
| ETH-USD | 1,648 | 1.0000 | **0.0334** | −0.9666 |
| PAXG-USD | 259 | 1.0000 | **0.0463** | −0.9537 |
| ADA-USD | 1,636 | 0.2170 | **0.0465** | −0.1705 |
| XLM-USD | 1,636 | 0.1901 | **0.0440** | −0.1461 |
| **pooled** | **6,827** | **0.6183** | **0.0401** | **−0.5781** |

**Cross-asset spread (max/min): 5.26× → 1.39×.** That single number is #225's acceptance test.
The residual 1.39× is not noise in the definition — it is the sampling spread of a ~4% Bernoulli
rate over 259–1,648 bars, and PAXG (N=259, the widest) is the shortest series.

The repaired factor now sits inside the existing panel rather than dominating it:

| factor | wt | P(present) |
|---|---:|---:|
| in_pullback | 1 | 0.8389 |
| sr_touches | 2 | 0.7990 |
| fib_confluence | 1 | 0.3545 |
| ema_fan_aligned | 2 | 0.3009 |
| condition_aligned | 2 | 0.2751 |
| candlestick_pattern | 1 | 0.2026 |
| deceleration | 1 | 0.1743 |
| rsi_divergence | 2 | 0.0915 |
| **round_number_proximity** | **1** | **0.0401** ← repaired |
| rsi_extreme | 1 | 0.0230 |
| seasonality | 0 | 0.0000 |

It is now rarer than `candlestick_pattern` and commoner than `rsi_extreme` — a member of the
distribution, not an outlier at either end.

### The before-arm is verified, not asserted

Before and after are on **the same bars**, and the before-arm is reconstructed arithmetically
(only this factor moves, and it is a pure function of the entry price) rather than replayed. That
reconstruction is an argument, so arm E re-runs the **full replay** on all five assets with
`levels.is_round_number` monkeypatched back to its pre-#225 body and compares bar for bar:

```
asset           N   factor vec   CTS totals   other factors
BTC-USD      1648        MATCH        MATCH           MATCH
ETH-USD      1648        MATCH        MATCH           MATCH
PAXG-USD      259        MATCH        MATCH           MATCH
ADA-USD      1636        MATCH        MATCH           MATCH
XLM-USD      1636        MATCH        MATCH           MATCH
  reconstruction is EXACT
```

This also independently confirms the claim the reconstruction rests on: **no other CTS factor
moved.** Had anything else read the round-number flag transitively, the `other factors` column
would have broken.

## 4. Result — CTS distribution, and the threshold impact

| asset | arm | mean | median | sd | min | max | Δmean |
|---|---|---:|---:|---:|---:|---:|---:|
| BTC-USD | before | 5.771 | 6.0 | 2.008 | 1 | 11 | |
| | after | **4.806** | **5.0** | 2.016 | 0 | 10 | **−0.964** |
| ETH-USD | before | 5.620 | 6.0 | 1.906 | 1 | 11 | |
| | after | **4.653** | **5.0** | 1.916 | 0 | 10 | **−0.967** |
| PAXG-USD | before | 6.367 | 6.0 | 1.647 | 3 | 10 | |
| | after | **5.413** | **5.0** | 1.643 | 2 | 9 | **−0.954** |
| ADA-USD | before | 4.652 | 4.0 | 1.861 | 0 | 10 | |
| | after | **4.481** | 4.0 | 1.818 | 0 | 10 | **−0.171** |
| XLM-USD | before | 4.335 | 4.0 | 1.945 | 0 | 10 | |
| | after | **4.189** | 4.0 | 1.921 | 0 | 10 | **−0.146** |
| **pooled** | before | 5.145 | 5.0 | 2.028 | 0 | 11 | |
| | after | **4.566** | **4.0** | 1.930 | 0 | 10 | **−0.578** |

Exactly as predicted: the three 2dp assets lose ≈0.96 (the constant, minus the ~4% of bars where
the factor legitimately fires), the two others lose ≈0.16. **The cross-asset mean gap narrows from
1.44 points (BTC 5.771 vs XLM 4.335) to 0.62 (4.806 vs 4.189)** — 57% of the gap between the
highest- and lowest-scoring allowlist assets was this bug, not the market.

### Every threshold a CTS total is compared against

Grepping `cts` across `keel/`, `packages/` and `scripts/` finds **exactly one**:

```python
# keel/strategy/indicators_cts.py:154
def entry_technique(total: int, low: int = 5, high: int = 8) -> Literal[...]
```

called from **one** site, `keel/strategy/engine.py:144`, with no override. There is no config key
(`min_cts`, `cts_min`, `min_score` — none exist), and **the promotion gate does not read CTS at
all**: `promotion.can_promote` runs off backtested `n_trades` / `expectancy` / realized R:R /
`win_rate`, and the PBO gate off `pbo` and `degradation_slope`.

| asset | arm | confirm_3bar | signal_candle | aggressive | moved |
|---|---|---:|---:|---:|---:|
| BTC-USD | before | 460 | 847 | 341 | |
| | after | 751 | 733 | 164 | **468 (28.4%)** |
| ETH-USD | before | 489 | 891 | 268 | |
| | after | 762 | 751 | 135 | **406 (24.6%)** |
| PAXG-USD | before | 37 | 157 | 65 | |
| | after | 80 | 145 | 34 | **74 (28.6%)** |
| ADA-USD | before | 826 | 688 | 122 | |
| | after | 889 | 650 | 97 | **88 (5.4%)** |
| XLM-USD | before | 894 | 645 | 97 | |
| | after | 934 | 622 | 80 | **67 (4.1%)** |
| **pooled** | before | 2,706 | 3,228 | 893 | |
| | after | **3,416** | **2,901** | **510** | **1,103 (16.2%)** |

16.2% of bars change technique; every move is one rung down, because removing a point cannot raise
a total. `aggressive` falls **43%** pooled and roughly **halves** on the three 2dp assets.

**⭐ And none of it changes a single order.** `entry_technique`'s three return values —
`"confirm_3bar"`, `"signal_candle"`, `"aggressive"` — appear **nowhere** in `keel/`, `packages/`
or `scripts/` outside `indicators_cts.py`'s own definition and docstring. Nothing branches on the
technique; nothing sizes, stops, or picks an order type from it. In `agent.py` it reaches exactly
one place — a field on the `agent.enter_evaluated` log line (`agent.py:1166`). `cts_score` has the
same shape: written to the `signals` table, logged, carried on sim records, read by no gate.

So the specific danger #225 raised — *"may push scores below promotion/entry thresholds that were
tuned with the constant in place"* — **cannot occur**. There is no CTS floor. No setup that
previously qualified is rejected, no position changes size, no stop moves. What changes is the
*label* recorded in the audit trail and in `signals.cts_score`, on 16.2% of bars.

That is the finding that makes this fix safe to ship. It is also, in its own right, a defect worth
a follow-up (§6): `indicators_cts.py`'s module table promises `confirm_3bar` means "smaller size,
wider stop" and `aggressive` means "larger size toward the cap, tighter stop", and **none of that
is wired to anything.** The graded entry ladder in spec §9/§17.1 is documented, computed, scored,
persisted — and inert.

## 5. What changed in the tests

The repo's own engine fixture is the smallest end-to-end demonstration of the bug. It enters at
**128.02**, which is 2.02 away from the nearest handle (130, on a 10-wide grid at that magnitude)
— it is not near a magnet level and never was. It scored present only because 128.02 is an exact
multiple of half a cent, as every 2dp price is. Correcting it drops that fixture's CTS from 5 to 4
and its technique from `signal_candle` to `confirm_3bar` — a real crossing of `entry_technique`'s
`low=5` edge, caused entirely by removing a point that was never earned.
`test_default_weights_on_same_fixture_yields_confirm_3bar_tier` now asserts the corrected values,
with a companion test showing the point returns when the entry is nudged onto the 130 handle.

## 6. What this changes, and what it explicitly does not

**1. `is_round_number` is fixed.** One caller in the package (`engine.assemble_cts_context`,
`engine.py:288`), which uses the default. The parameter was **renamed `step` → `tolerance`**
rather than kept: its meaning inverted from an absolute price step to a relative fraction of the
handle grid, and a caller passing `step=Decimal("0.005")` under the old name would silently get
new behaviour. Renaming makes any such caller fail loudly. There are none outside the tests.

**2. No threshold was retuned, and none should be on this evidence alone.** `low=5` / `high=8` are
untouched. The pooled median moved 5 → 4, so `low=5` now sits above the median and a plurality of
bars land in `confirm_3bar` — but retuning band edges is a separate decision with its own evidence
requirement, and it is moot until §6.3 is resolved.

**3. Recommend a follow-up issue: the graded entry ladder is inert.** `entry_technique` is
computed on every signal, persisted, and read by nothing. Either wire it to sizing/stop/order-type
as spec §9/§17.1 describes, or delete the claim from the docstring — but the current state, where
the audit trail records a posture that execution does not implement, is the worst of both. **This
is also the precondition for §6.2**: recalibrating `low`/`high` is meaningless while nothing
consumes their output.

**4. Recommend recording `tolerance=0.10` as an open question** (§2). It is the better number on
every axis except the one correctness case #225 pins, and that case is arguable. Cheap to revisit;
the ladder above is the evidence.

**5. Nothing here says this factor predicts anything.** Under §73.5 a well-defined factor is
necessary and never sufficient. This fix makes `round_number_proximity` *mean something
consistent across assets*; whether what it means is worth a point of CTS is a question nobody has
asked, and 4% presence on a weight-1 factor of 14 means it now moves the total very little either
way.

## Caveats

- In-sample, one granularity, one window, no out-of-sample split. This is a correctness fix
  measured for blast radius, not a strategy result.
- **Serial dependence.** Bars are autocorrelated, so 6,827 observations are worth fewer than 6,827
  independent ones. No conclusion here rests on a p-value — nothing is tested for significance —
  so this does not move the finding, but the base rates are less precise than N suggests.
- **PAXG-USD contributes 259 of 6,827 daily bars** (listed 2025-05-08) and is the widest cell in
  every table, including the 1.39× spread that the acceptance criterion is read off.
- The synthetic setup prices entry at the bar's **close** (`cts_factors._synthetic_setup`,
  unchanged from #224). `round_number_proximity` is measured against that price, so its base rate
  would shift under a different entry convention. The close is the neutral choice — it is what a
  market order fills at — but it is a choice, and it is the one this factor is most sensitive to.
- N is 6,827 here against 6,822 in #224: the candle cache grew by five daily bars between the two
  runs. Same assets, same window rule, five more bars.
- `tolerance=0.02` yields `2 × tolerance` presence **only for a price series smooth on the scale
  of the grid**. That holds for all five allowlist assets (§3 confirms it: 0.033–0.047 against a
  predicted 0.04). It would not hold for an asset pinned near a handle, or one whose tick is a
  meaningful fraction of its 2-significant-figure spacing — i.e. an asset quoted to fewer than
  ~3 significant figures. None on the allowlist is close.
- All five assets are crypto over one broadly-correlated window.
