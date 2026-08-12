# At zero fee `turtle_breakout` makes money and `rsi_meanrev` does not — two failure modes that were one inference away from being pooled

**Date:** 2026-08-12
**Issue:** none. Two threads meet here. One is
`docs/experiments/2026-08-11-hourly-param-sweep-turtle-breakout.md` §10.3(b) — *the execution-cost
lever, the largest measured lever left* — which this document probes as far as the current fill
model honestly allows and no further (§8). The other is the first backtest of any rule other than
`turtle_breakout`: `rsi_meanrev` has been in `RULE_REGISTRY` the whole time and had never been run,
so "this fails on crypto spot at these fees" was a claim about one Donchian breakout.
**It assumes both companion documents have been read and does not restate them.** What it is
actually for is the inference *drawn from* them after they were written, which neither of them
makes: that execution cost is the binding constraint system-wide.
**Change:** **none.** No code, no config, no parameter, no rule status, no version bump, no
demotion. In particular the fill model is **not** touched, and §8 argues that changing the fee
*rate* without changing it would be a worse costing error than the one #247 fixed yesterday —
worse because it would be deliberate.
**Harness:** the shipped `keel/strategy/backtest.py::backtest`, driven over
`Repository.get_candles(product_id, Granularity.ONE_HOUR)` against the paper-forward candle cache
(`~/keel/keel.db`, opened `mode=ro`). `fee_pct` is passed **explicitly on every cell**, from
`Decimal("0")` to `Decimal("0.012")`, so that #247's change of the shipped default — landed on
`main` at `1dfe1fb`, between the companion documents and this one — cannot move a number here.
Identical code path to both companions and to every positive result this project has published.
**Script:** two, both committed as dated siblings:
`docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev-sweep.py` (the **abandoned** first grid)
and `docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev-diag.py` (the diagnostic grid). Each
carries its grid as declared before its run in its own docstring. **Paths were adjusted when they
were copied into the repo** — the run-time originals hard-coded the deployment's `~/keel` on
`sys.path`, its `~/keel/keel.db` and a session scratchpad output path; the committed copies take
`--db` / `--out` with repo-relative defaults and open the cache `mode=ro` per house convention.
The grids, the fee, the asset lists, the metrics, the resume logic and the per-trial bodies are
the runs'. The 61 fee-curve and zero-fee cells have no script and did not need one: the
configurations are named in §4 and the loop is one line.
**Ledger:** **two** rows, not one — `rsi-meanrev-grid-abandoned-2026-08-12` (`n_trials: 448`) and
`rsi-meanrev-diagnostic-and-fee-curve-2026-08-12` (`n_trials: 169`), both `kind: ablation`,
`provenance: a_priori`, `decision: diagnostic_only`. §10 argues the split rather than asserting
it. Neither counts toward `N`.

**Verdict: the inference is half right, and the wrong half is the half that would have set the
roadmap. Priced at zero, `turtle_breakout`'s best-swept configuration is profitable on all four
assets measured — BTC 1.090, ETH 1.458, SOL 1.533, ZEC 2.713 — and `rsi_meanrev` is not: BTC
0.775 on n=255, FET 0.907 on n=284, and a single ZEC cell at 1.094 that a fee of nine basis points
erases. Turtle has a gross edge that cost destroys. `rsi_meanrev` has no gross edge for
cost to destroy. They fail for different reasons, the reasons prescribe different fixes, and
pooling them predicts that maker fills rescue both — which cutting cost to zero, an intervention
strictly better than any maker rate on any venue, does not. The maker pivot is justified by
turtle's numbers alone, it buys SOL and ZEC and not BTC, and it buys nothing at all until the
simulator stops filling market-style at next-bar open.**

| question | answer |
|---|---|
| is execution cost the binding constraint system-wide? | **no — for turtle yes, for `rsi_meanrev` no.** One inference, two diseases |
| does `turtle_breakout` have a gross edge? | **yes** — PF **1.090 / 1.458 / 1.533 / 2.713** at `fee_pct=0` on BTC/ETH/SOL/ZEC |
| does `rsi_meanrev`? | **no** — **0.775** (BTC, n=255) and **0.907** (FET, n=284) at zero cost; ZEC **1.094**, dead at a **0.093%** fee |
| where is turtle's break-even `fee_pct`? | BTC **0.068%**, ETH **0.43%**, SOL **0.75%**, ZEC **1.74%** — a **26×** spread across four assets |
| does the 0.6% maker rate rescue anything? | **SOL (1.083) and ZEC (1.822) yes; ETH (0.875) nearly; BTC (0.564) not at any venue** |
| is switching the fee rate to maker sufficient? | **no** (§8) — the sim fills market-style at next-bar open. Rate without fill model repeats #247 on purpose |
| was the first `rsi_meanrev` grid's "unmeasurable" verdict right? | **no** — 48 of its own distinct cells already cleared n≥100, and they said what the second grid said |
| what actually gated the trade count? | **`oversold`, essentially alone** — ×3.93. `support_proximity_pct` ×1.19, `level_min_touches` ×1.19 |
| does firing 7× more often improve `rsi_meanrev`? | **no** — Spearman(n, PF) = **+0.03** over 108 cells; **0 of 108** above PF 1.0 |
| are the fee-curve configs edge estimates? | **no** (§9) — each is the argmax of 144 cells, at n = 123 / 121 / 92 / 50 |
| what did today cost? | **617 trials** (448 abandoned + 108 diagnostic + 61 fee cells), on top of the prior 864 |
| is this validated? | **no** (§12) — no walk-forward, no CSCV/PBO on these configs, 4–6 of 19 assets, one window |

---

## 1. The inference under test

Neither companion document claims that execution cost is the binding constraint on this system.
Both are careful not to. The second one puts the strongest version of the cost argument in its §8
and immediately fences it with three cautions, the first of which is *"maker pricing is not a free
re-label; it is a different fill model."*

The inference was drawn anyway, and it was drawn for a good reason. By the end of 2026-08-11 the
record read: nineteen assets negative on a breakout, one hundred and forty-four parameter sets
negative on the same breakout, and a single arithmetic comparison — *changing the fee alone, with
mis-scaled daily parameters left in place, is worth more than the entire 144-cell parameter grid*
— showing cost to be the largest measured lever anyone had found. Add a mean-reversion rule that
also comes back negative and the shape of the conclusion writes itself:

> Two structurally opposite strategies both fail on the same venue at the same fee. The thing they
> share is the fee. Therefore the fee is the problem, and fixing execution is the system-wide fix.

That is a good inference from the evidence as it stood, and it is wrong. It is wrong in a way that
no amount of further parameter search would have exposed, because the two rules fail at the same
place on the same axis and their symptoms at 1.2% are indistinguishable: everything loses. The
test that separates them is not a better grid. It is removing the shared cause and seeing which
patient recovers.

**So: set `fee_pct` to zero.** Not to maker, not to some optimistic tier — to zero, which is
strictly better than any execution any venue will ever offer and which therefore bounds from above
everything an execution fix could possibly buy. If a rule is unprofitable at zero cost, no
execution work will save it and any roadmap that promises otherwise is promising something the
data has already refused.

## 2. What was run

Three things, in this order, against the same cached hourly candles as both companions.

**(a) The abandoned `rsi_meanrev` grid.** 576 cells declared, 448 rows written, stopped as
mis-centred. §5. Its script docstring is the pre-registration and is committed unedited, stale
numbers and all, because §6 uses it as an exhibit.

**(b) The `rsi_meanrev` diagnostic grid.** 108 cells whose declared primary metric is `n_trades`
and explicitly **not** profit factor, built to answer whether the rule can be made to fire enough
to be evaluated at all. §5, §6.

**(c) The fee curve and the zero-fee cells.** 61 backtests: `turtle_breakout` at its best-swept
configuration per asset on BTC / ETH / SOL / ZEC at `fee_pct` ∈ {0, 0.001, 0.002, 0.003, 0.004,
0.006, 0.012} — 28 cells — plus **18** bracketing cells to locate each break-even by measurement
rather than by interpolating between two points; and `rsi_meanrev` at one configuration on BTC /
ZEC / FET at {0, 0.006, 0.012} — 9 cells — plus **6** bracketing cells on ZEC. §3, §4.

Four things about how this was run are worth stating before any number is read.

**Every number was re-derived from the raw files, not from a running summary.** The two grids
wrote append-only JSONL; every statistic below is recomputed from those files, and where the
recomputation disagrees with what was believed during the run, the recomputed figure is the one
reported and the disagreement is itself reported (§5, §6). Several of the figures carried forward
from the run did not survive that, including the first grid's median trade count, its maximum
trade count, its trial count, and the sample sizes behind its apparent winners — and the item that
mattered most was not a number at all but a diagnosis (§6).

**The first grid's raw file was still growing when analysis started.** Its producing process was
still resident hours after the grid was abandoned — abandonment was a decision to stop reading the
output, not to stop the job. The first pass of the analysis therefore read a moving file and
produced numbers that did not reproduce. All figures below are taken from a frozen copy,
`rsi_results.jsonl` at 136,204 bytes, md5 `5588cfda7340501772ae10cb23a1a447`, 449 lines of which
448 parse and one is a torn final line from a kill mid-write. Anyone re-deriving these numbers
against a longer file should expect them to move, and the honest way to make that checkable is to
pin the snapshot rather than to describe it.

**`fee_pct` is explicit everywhere.** `1dfe1fb` (#247) changed `backtest`'s default from the maker
rate to `TAKER_FEE_PCT`, along with `_SIM_FEE_PCT`, `portfolio_sim.run` and
`paper._DEFAULT_FEE_PCT`. That landed after both companion documents ran and before this one did —
which means the work here straddles a commit that changed the very quantity it measures. Both
grids pass `fee_pct=Decimal("0.012")` explicitly and the fee curve passes a different explicit
value in every cell, so **no number in this document depends on any default** and it does not
matter which side of `1dfe1fb` any given cell was executed on. That is the only way a fee-curve
document could be trusted across such a commit, and the §2 cross-check confirms it empirically:
cells run before and after `1dfe1fb` agree to six digits.

**Both grids were pre-declared and both are `a_priori`.** Each script's docstring is the grid that
ran. The second was declared *after* seeing the first grid's data — that is what makes it a
diagnostic rather than a continuation, and it is the single strongest argument for two ledger rows
rather than one (§10).

**Cross-check that this is the same harness.** The fee curve's `fee_pct = 0.012` column is a cell
that already exists in two earlier runs, and it reproduces both exactly. Against the prior
document's 864-cell sweep: BTC **0.3331**, ETH **0.5560**, SOL **0.8011**, ZEC **1.3033** — its
published 0.333 / 0.556 / 0.801 / 1.303. Against the diagnostic grid: `rsi_meanrev` BTC
**0.068042**, FET **0.294594**, ZEC **0.367288** — identical to all six printed digits. The runs
share no code beyond the package and were executed on different days, one of them across the #247
commit. That is the cheapest available evidence that the zero-fee column is being produced by the
same object that produced every number in the companion documents, and it was checked before
anything below was believed.

## 3. Result 1 — the decisive test: one rule has a gross edge and the other does not

Set the round trip to nothing and ask what is left.

| rule / asset | config | n | PF @ 0.0% | PF @ 0.6% | PF @ 1.2% |
|---|---|---:|---:|---:|---:|
| turtle BTC | e168/x80/atr3/rr6/adx25 | 123 | **1.090** | 0.564 | 0.333 |
| turtle ETH | e240/x80/atr3/rr3/adx25 | 121 | **1.458** | 0.875 | 0.556 |
| turtle SOL | e336/x80/atr3/rr6/adx20 | 92 | **1.533** | 1.083 | 0.801 |
| turtle ZEC | e336/x20/atr2/rr6/adx25 | 50 | **2.713** | 1.822 | 1.303 |
| `rsi_meanrev` BTC | os30/touch2/prox0.005 | 255 | **0.775** | 0.245 | 0.068 |
| `rsi_meanrev` FET | os30/touch2/prox0.005 | 284 | **0.907** | 0.514 | 0.295 |
| `rsi_meanrev` ZEC | os30/touch2/prox0.005 | 341 | **1.094** | 0.629 | 0.367 |

**Four of four turtle configurations are profitable before costs. Two of three `rsi_meanrev`
configurations are not, and the third is profitable by nine percent.** That is the whole finding
and everything else in this document is either its evidence or its consequences.

Read the BTC rows against each other, because they are the cleanest pair available: same asset,
same five years, same bars, same harness, same fee schedule, opposite signal logic. Turtle enters
on a 168-hour Donchian breakout confirmed by ADX and takes 123 trades; `rsi_meanrev` enters on an
oversold *bounce* — a 14-period RSI that dipped below 30 on the prior bar and is now recovering —
occurring within half a percent of a twice-touched support level, and takes 255. At zero cost
turtle returns **1.090** and `rsi_meanrev` returns **0.775**. One of them is
extracting more from BTC's price series than it gives back and the other is giving back a quarter
of everything it risks *before anyone has been paid a cent*. There is no execution improvement,
no venue, no rebate and no fee tier that turns 0.775 into a positive number, because 0.775 is what
the strategy does in the absence of all of them.

**The ZEC cell at 1.094 deserves to be stated at its strongest and then read carefully.** It is a
real number: 341 trades, more than three times the promotion floor, and above break-even. It is
also (i) nine percent of headroom against a round trip of 2.50% at taker and 1.30% at maker on
this venue — its break-even *fee* is measured at **0.093%**, one sixth of the cheapest rate this
account is offered; (ii) the *only* one of three assets above 1.0, where turtle
is four of four; and (iii) the same ZEC that the companion document spent its §5 arguing is in a
regime — 180-day median daily quote volume ~31× its own full-history median, closes running
36.92 → 510.43 across 2025H2. A mean-reverter posting its one positive result on the one asset in
a 14× directional move is not obviously measuring mean reversion. **The competing reading — ZEC
has an asset-specific edge two other assets lack — makes the same prediction on this data, and no
regime split was run here either.** What can be said without a split is that 1.094 at literally
zero cost is not a finding that survives contact with any venue, and that the claim under test in
this section was about whether cost is the binding constraint, not about whether ZEC is special.

The asymmetry is what matters and it is not subtle:

- **Turtle's problem is arithmetic.** The signal is real and the round trip is bigger. That is a
  solvable class of problem — you make the round trip smaller, or you trade less often for more,
  or you conclude the venue cannot support the strategy. All three are engineering questions with
  measurable answers.
- **`rsi_meanrev`'s problem is the signal.** There is nothing for cheaper execution to preserve.
  The class of fix is different: a different entry, a different rule, or the conclusion that mean
  reversion at this horizon on this venue is not a thing that pays.

Confusing the two costs you the entire diagnostic value of having run a second rule family at all.

**One asymmetry in the comparison runs in `rsi_meanrev`'s favour and should be said out loud,
because it removes the objection that dominated both companion documents.** `TurtleBreakout`
hard-codes `self.granularity = Granularity.ONE_DAY` (`turtle_breakout.py:140`), so on hourly bars
it is handed a series it believes is daily and its `entry_lookback=40` silently becomes 40 hours —
the §7 objection that the second companion spent 864 trials answering. `RsiMeanReversion` declares
`timeframe: Granularity = Granularity.ONE_HOUR`, and `_rule_trading_tf` reads `granularity` then
`timeframe`, so it receives hourly candles under the key it was written for. **Its parameters are
native to this clock; turtle's were not.** The rule that has a gross edge here is the one running
on the wrong clock with parameters someone had to re-tune to get 51% back, and the rule that does
not is the one that was already on its own clock. Whatever `rsi_meanrev`'s problem is, it is not
the problem that consumed the previous two documents.

## 4. Result 2 — the fee curve, and where break-even actually is

`turtle_breakout`, best-swept configuration per asset (the argmax of that asset's 144 cells in the
prior document's 864-trial grid — see §9 before believing any of it as an edge estimate), hourly,
`slippage_pct` left at its `0.0005` default throughout:

| asset | n | 0.00% | 0.10% | 0.20% | 0.30% | 0.40% | 0.60% | 1.20% | break-even `fee_pct` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 123 | 1.090 | 0.961 | 0.854 | 0.764 | 0.688 | 0.564 | 0.333 | **0.068%** |
| ETH | 121 | 1.458 | 1.330 | 1.217 | 1.117 | 1.028 | 0.875 | 0.556 | **0.433%** |
| SOL | 92 | 1.533 | 1.441 | 1.356 | 1.279 | 1.208 | 1.083 | 0.801 | **0.751%** |
| ZEC | 50 | 2.713 | 2.525 | 2.355 | 2.202 | 2.063 | 1.822 | 1.303 | **1.741%** |

The last column is the `fee_pct` at which that asset crosses PF 1.0, and it is **measured, not
interpolated**: eighteen additional cells were run to bracket each crossing to within 5 basis
points or better, so the interpolation left inside the bracket is visibly tiny.

| asset | last cell above 1.0 | first cell below 1.0 | quoted break-even |
|---|---|---|---:|
| BTC | 0.060% → 1.0096 | 0.070% → 0.9971 | **0.068%** |
| ETH | 0.430% → 1.0025 | 0.440% → 0.9944 | **0.433%** |
| SOL | 0.750% → 1.0005 | 0.800% → 0.9751 | **0.751%** |
| ZEC | 1.700% → 1.0191 | 1.750% → 0.9957 | **1.741%** |
| `rsi_meanrev` ZEC | 0.090% → 1.0025 | 0.095% → 0.9977 | **0.093%** |

Measuring rather than interpolating was not pedantry, and the first attempt got it wrong. SOL's
only measured neighbours were 0.60% (1.083) and 1.20% (0.801); the chord between them crosses 1.0
at 0.78%, and the first bracket was placed at 0.80/0.85/0.90% on the expectation that the true
crossing would sit *above* the chord. **All three of those cells came back below 1.0.** Profit
factor is convex in the fee — BTC's successive 0.1% steps cost 0.129, 0.107, 0.090, 0.077, a
shrinking slope — so the curve lies *below* its chords and crosses *earlier*, not later. The true
crossing is 0.751%. Every estimate in the neighbourhood was therefore biased in the direction that
flatters the maker argument, on the asset that argument most depends on. A second round of cells
was run; both rounds are counted in §10.

Note the units, because they are easy to conflate. `fee_pct` is charged **per leg**, so total
round-trip friction is `2 × fee_pct + 2 × slippage_pct`: 2.50% of notional at taker, 1.30% at
maker, and **0.10% at `fee_pct = 0`** — the zero-fee column is zero *fee*, not zero cost.

Three things to take from that table.

**First, the spread is the finding, not the level.** Break-even runs from 0.068% to 1.741% — a
factor of **26** across four assets running the same rule on the same clock over the same window.
The parameter grid in the companion document moved mean PF across six assets by 0.215 in total;
the *asset* moves the tolerable fee by a factor of twenty-six. Nothing in that document's
five parameter axes had an effect of remotely this size. Whatever governs it — ZEC's regime, SOL's
trend persistence, BTC's efficiency — it is a bigger lever than tuning, and it is a lever nobody
has tried to characterise.

**Second, the ordering is the inverse of tradability.** The asset with the most headroom is the
one with the 31× liquidity surge and 50 trades; the asset with the least is the deepest, most
liquid, most efficiently priced instrument in the field. That is exactly the shape you expect if
what turtle is capturing is inefficiency: the more efficiently an asset is priced, the less there
is above the round trip. It is also exactly the shape you expect if the positive cells are regime
artifacts, since the assets in a regime are the ones in a move. **These two explanations are not
distinguished by anything in this document**, and both predict the ordering seen.

**Third, and worst for the maker pivot: the assets it rescues are the assets you cannot promote.**
At `fee_pct = 0.006` the two assets above break-even are SOL (1.083) and ZEC (1.822), at **n = 92
and n = 50**. The two below are ETH (0.875) and BTC (0.564), at **n = 121 and n = 123**. The
promotion floor is `min_trades=100` (`keel/strategy/promotion.py:66` at `1dfe1fb`). So both of
the configurations that maker execution would make profitable are below the floor, and both of the
configurations with enough trades to promote stay unprofitable. This is the companion's §6 bind — edge
and sample size moving in opposite directions, Spearman −0.77 over its 144 configurations — showing
up again on a completely different axis. It was not an artifact of the parameter grid. It survives
into the fee dimension, which is the first evidence that it is structural rather than a property of
one search.

## 5. Result 3 — the abandoned grid, and why abandoning it was correct

The first `rsi_meanrev` sweep declared 576 cells: 96 parameter sets across six assets, `oversold` ∈
{15, 20, 25, 30}, `overbought` ∈ {70, 80}, `atr_mult` ∈ {1.2, 2.5}, `fixed_rr` ∈ {1.5, 2, 3},
`require_divergence` ∈ {False, True}, `support_proximity_pct` pinned at the shipped 0.005. It was
stopped part-way and its results were never used for an edge claim.

Re-derived from the frozen JSONL:

| | |
|---|---:|
| cells declared | 576 |
| rows written | **448** |
| distinct cells among them | **432** |
| duplicate rows from the resume path | 16 (all returning identical results) |
| torn final line | 1 |
| declared cells never run | **144** — the entire `oversold=30` block |
| median `n_trades` | **17** (16.5 over distinct cells) |
| cells that never fired at all | **36** |
| rows with n < 10 | 168 of 448 |
| maximum `n_trades` | **224** |
| distinct cells with PF > 1.0 | **19**, at n ∈ {2 (×9), 3 (×8), **16** (×2)} |

`oversold` is the first key in the grid dict and therefore varies slowest under
`itertools.product`, so the blocks that ran are `oversold` 15, 20 and 25 — complete, 144 cells each
— and the block that never ran is `oversold=30`, **the setting most likely to fire of the four
declared.** A grid that spends three quarters of its budget before reaching its most promising
corner is not merely mis-centred; it is mis-*ordered*, and the ordering was an accident of dict
literal order rather than a decision.

**The abandonment was the right call and is worth arguing rather than apologising for.** By the
time it was stopped the run had already recorded, in the output available when the diagnostic grid
was declared, that `oversold=15` gives median n=2 against `oversold=20`'s 25, and that
`require_divergence=True` gives median n=1 against `False`'s 20 — figures preserved verbatim in the
diagnostic script's docstring, and over the full 448 rows they settle at 2.5 / 16.5 and 6 / 41.
Half the remaining 144 cells carry `require_divergence=True`. Continuing as
declared would have spent 144 further trials, 72 of them on a filter the run had already measured
firing once or twice in five years, to produce cells that could not be evaluated. **Trials are the
scarce resource in this project** — the multiple-testing budget is the reason the ledger exists —
and spending 144 of them to reconfirm a dead end recorded in your own output is the most expensive
kind of thoroughness. Stopping was correct.

**What was not correct was the conclusion drawn at the same moment.** The reading taken from this
grid was *"`rsi_meanrev` is unmeasurable"* — median 6 trades, 36 cells that never fire, nothing
above n=80. That reading is an artifact of summarising 448 cells by their median when three
quarters of them were spent on settings the same output had already shown to be dead. Look instead
at the subset that was already measurable:

| | abandoned grid, its own n≥100 cells | purpose-built diagnostic grid |
|---|---:|---:|
| distinct cells | **48** | 108 |
| best PF | **0.4718** (DOGE, n=142) | **0.4556** (ZEC, n=144) |
| median PF | **0.2243** | **0.2092** |
| cells above PF 1.0 | **0** | **0** |

**The abandoned grid already contained the answer, at 48 cells, and the answer it contained is the
one the second grid went on to produce — best PF within 0.016, median PF within 0.015, and zero
above break-even in both.** 54 of its 448 rows cleared `min_trades=100`. Its true maximum was
n=224, not the 80 that was believed. The first grid was mis-centred, and the conclusion drawn from
it was mis-*read*, and of the two errors the second is the more expensive: mis-centring costs you
trials, mis-reading costs you a finding you had already paid for.

## 6. Result 4 — the diagnosis was wrong, and then the correction was wrong too

This section records two mistakes because a document that hides its wrong turns teaches nothing
about how the right answer was reached, and because the second mistake was made while fixing the
first, which is the more instructive of the two.

**The prediction.** `support_proximity_pct` — how near price must be to a support level for the
entry conjunction to pass — was named as the binding gate on trade count, on the reasoning that it
is the rule's distinguishing feature over a bare RSI trigger and that its shipped default of 0.005
is tight. The recommendation was to widen it, and the diagnostic grid was built to do so, taking
it from 0.005 to 0.05 — a **tenfold** widening.

**It was not the binding gate.** Holding asset, `oversold` and `level_min_touches` fixed, there are
36 matched triples in the diagnostic grid. Across them, widening the proximity gate tenfold moves
trade count by a median factor of **1.186**, with the whole distribution inside 1.071–1.426:

| asset | `oversold` | touches | n @ 0.005 | n @ 0.02 | n @ 0.05 | ratio |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 30 | 2 | 255 | 275 | **290** | ×1.137 |
| ETH | 35 | 2 | 828 | 869 | **887** | ×1.071 |
| DOGE | 35 | 2 | 768 | 831 | **858** | ×1.117 |
| FET | 25 | 3 | 94 | 117 | **134** | ×1.426 |

A 10× loosening of a gate that was supposed to be the constraint buys **19%** more trades. The
headline pair — BTC's 255 → 290 — is 14%. That is not a binding constraint; that is a gate that
was already almost always open, and widening it further mostly re-admits price action that was
within half a percent of a level anyway.

**The correction, and its own error.** The reading taken from that result was that the binding
gates were `oversold` and `level_min_touches`, the latter being the axis the first grid had pinned
at its default and therefore never tested. Half of that is right. Over 54 matched pairs, relaxing
`level_min_touches` from 3 to 2 moves trade count by a median factor of **1.185** — which is,
to three decimal places, indistinguishable from the 1.186 of the proximity gate it was supposed to
displace. **The axis the second grid was built to add turned out not to matter either.**

The one gate that binds is `oversold`, and it binds alone:

| axis | change | matched pairs | median effect on n |
|---|---|---:|---:|
| `support_proximity_pct` | 0.005 → 0.05 (10×) | 36 | **×1.186** |
| `level_min_touches` | 3 → 2 | 54 | **×1.185** |
| `oversold` | 25 → 30 | 36 | **×2.183** |
| `oversold` | 25 → 35 | 36 | **×3.933** |

And across the whole diagnostic grid, marginal median `n_trades` by `oversold` is **168.5 → 383.5 →
696.5** for 25 / 30 / 35, against 270.5 → 351.0 for the proximity gate's full tenfold sweep. The
entire jump from the first grid's median of 17 to the second's median of **322.5** is bought by
`oversold` and `require_divergence` — **both of which were axes in the first grid.** The one axis
the second grid added contributed a factor of 1.19.

The conjecture that explains this, offered as a conjecture: on five years of hourly bars a support
level with two or three touches is nearly everywhere, so the support conjunction is close to a
tautology at this resolution, while the RSI threshold is a genuine tail condition on an oscillator
and 25 versus 35 is a real difference in how far into that tail you insist on going. That is
consistent with the numbers and it was not tested.

**What this establishes, and what it does not.** It establishes that *"`rsi_meanrev` is
unmeasurable"* was a property of grid centring, not of the rule — the rule fires 80 to 887 times
per asset over five years under settings that were available in the first grid. It does **not**
rescue the rule, and §3 is why: making it fire more does not make it profitable. Across the 108
diagnostic cells, Spearman(`n_trades`, PF) = **+0.034**, i.e. nothing. The tightest corner of the
grid has median n=127 and median PF 0.2496; the loosest has median n=778.5 and median PF 0.2145.
**Loosening every gate multiplies the sample by six and leaves the profit factor exactly where it
was**, which is the signature of a per-trade loss rate that is a structural property of the signal
rather than a sampling artifact. That is a much stronger statement than the first grid could have
made, and it is the reason the diagnostic grid was worth its 108 trials even though it added an
axis that did not matter.

The diagnostic grid in full: **104 of 108 cells clear n≥100**; **zero of 108 have PF > 1.0**;
median n **322.5**, max n **887**, median PF **0.2092**, best PF **0.4556**, win rates 14.9%–34.0%.
Per asset:

| asset | median n | best PF | PF range across its 18 cells |
|---|---:|---:|---|
| BTC | 134.0 | 0.0953 | 0.0680 – 0.0953 |
| ETH | 513.0 | 0.1444 | 0.1092 – 0.1444 |
| SOL | 427.5 | 0.2102 | 0.1715 – 0.2102 |
| DOGE | 423.0 | 0.3149 | 0.2053 – 0.3149 |
| FET | 304.0 | 0.4171 | 0.2486 – 0.4171 |
| ZEC | 331.0 | 0.4556 | 0.2722 – 0.4556 |

Note how narrow those per-asset ranges are. Eighteen configurations spanning a tenfold proximity
sweep, a touch-count change and a ten-point RSI shift move BTC's profit factor between 0.068 and
0.095 and never leave the neighbourhood. This is the companion document's flat-response-surface
argument in a much more extreme form: there is no ridge here because there is no hill.

## 7. Two failure modes, and what pooling them would have cost

Put the two rules side by side at the three fees that matter.

| | turtle (best of 144/asset) | `rsi_meanrev` |
|---|---|---|
| PF at 0.0% | **1.090 – 2.713**, 4 of 4 above 1.0 | **0.775 – 1.094**, 1 of 3 above 1.0 |
| PF at 0.6% | 0.564 – 1.822, 2 of 4 above 1.0 | 0.245 – 0.629, **0 of 3** |
| PF at 1.2% | 0.333 – 1.303, 1 of 4 above 1.0 | 0.068 – 0.367, **0 of 3** |
| break-even `fee_pct` | **0.068% – 1.741%** | **≤ 0.093%**, and negative at 0% on 2 of 3 |
| what cost is doing | destroying a real gross edge | finishing something already dead |

At 1.2% the two rows look the same: both mostly below 1.0, both worse than they were, both
apparently victims of the same tax. That similarity is the trap. **The symptom converges and the
disease does not**, and the fee curve is the instrument that separates them because it measures
the same rule at seven prices instead of one rule at one price.

Here is the concrete architectural decision that pooling would have produced. The pooled claim —
*execution cost is the binding constraint* — makes a prediction: reduce cost and both rules improve
toward break-even together. The natural project that follows is a maker-execution program, sold as
a platform-level fix, justified by the fact that it helps "the strategies". It would then have been
validated on turtle, where it does help, and rolled out to `rsi_meanrev`, where the strictly
stronger intervention of setting cost to **zero** leaves BTC at 0.775. The work would have been
real work, correctly executed, on a foundation that the zero-fee cell falsifies in one line. The
cost of the wrong inference is not a wrong number in a document; it is a quarter's engineering
aimed at the wrong term of the equation for half the rules it claims to serve.

The error also runs in the other direction, and that half is worth naming because it is the half
that makes the project look worse than it is. "Two structurally opposite strategies both fail on
this venue" reads as evidence about the venue, and it would license a much larger conclusion —
that crypto spot at retail fees is not tradable by rules of this family. The zero-fee test refuses
that too. One of the two rules has a gross edge on four of four assets, and on two of them that
edge clears a round trip the venue **already publishes a rate for**. Pooling would have thrown away
a positive result as well as manufacturing a false one.

The general form, which is the part worth carrying to the next experiment: **two negative results
are evidence of a common cause only if they are negative in the same way.** Establishing that
costs a controlled sweep of the suspected common cause, which is what the fee curve is. Before
this document, "both rules lose at 1.2%" was one observation reported twice.

## 8. What maker execution buys, and the prerequisite that makes the question askable

**The maker pivot is justified. Its justification is narrow and it comes entirely from turtle.**

At `fee_pct = 0.006` — round-trip friction `2 × 0.006 + 2 × 0.0005 = 1.30%` against taker's 2.50%,
the 48% cost reduction the companion document costed — the four-asset field reads SOL **1.083**,
ZEC **1.822**, ETH **0.875**, BTC **0.564**. So, precisely:

**What maker execution would buy.** SOL and ZEC cross break-even — comfortably, at 0.751% and
1.741% against a 0.600% rate. ETH lands at 0.875 and needs **0.433%**, a 28% cost reduction below
maker: near enough that a volume tier, a rebate, or the free-allowance mechanics of rail 14 could
plausibly close it, and far enough that nothing currently in the deployment does. That is two
assets converted and one in reach, out of four.

**What it would not buy.** BTC needs a **0.068%** per-leg fee against the 0.60% maker rate of the
`<$1k-30d-volume` band this account sits in — a factor of nine, not a rounding gap. Coinbase's
schedule does reach very low maker rates at the top, but those tiers are priced off 30-day volume
that a deployment capped at `max_exposure_usd 200` cannot generate by orders of magnitude. BTC is
therefore not a fee-tier problem, and it is the asset with the largest live allocation. And maker
execution would buy `rsi_meanrev` nothing at all — not less than expected, *nothing*, because the
rule is under water at zero.

**And none of it can be claimed until the fill model changes.** This is the part that must not be
skipped, and #247 is the reason it is now easy to state precisely. Yesterday's fix on `main` at
`1dfe1fb` corrected a costing error whose whole content was that the **rate** did not match the
**fill model**: `backtest` fills market-style at next-bar open, which is a marketable order
crossing the spread, which is taker behaviour, and it was priced at the maker rate in four places.
The commit message says so; `config.yaml`'s `fees:` comment had said so the entire time.

Setting `fee_pct = 0.006` on the same simulator re-creates that error exactly. The rate would be
maker; the fill would still be a market-style execution at next bar open, taken every time the
signal fires, with no possibility of not being filled. **The difference is that #247's version was
an oversight and this one would be a decision** — made with the corrected code in the tree and the
commit that corrected it in the log. That is a worse defect than the one just fixed, and no
research document should launder it by reporting a maker-priced number without the qualification.

What an actual maker model requires, so that the prerequisite is a specification and not a
misgiving:

1. **A resting order fills only when price trades *through* the limit**, not when it merely touches
   it. A touch is a queue position, not a fill. This is the load-bearing change and it is not a
   one-line edit to the fill rule; it changes which bars generate trades.
2. **The entry population changes, adversely.** A breakout entered as a resting limit does not fill
   on the bar that breaks out — it fills when price comes back to the level, which selects
   disproportionately for breakouts that failed. The 1.083 and 1.822 above are **taker fills priced
   at a maker rate**, which is an upper bound on maker execution and not an estimate of it. Some of
   the gain comes straight back and nobody has measured how much.
3. **Unfilled signals need a policy.** A resting order that never fills is a trade that never
   happens, so `n_trades` falls — and §4's third finding is that the two assets maker execution
   rescues are already below the promotion floor at n=92 and n=50. A fill model that reduces n
   further makes them less promotable, not more.
4. **Intrabar resolution needs re-deriving.** The module's conservative fallback — entry-vs-stop
   ambiguity invalidates the trade, stop-vs-target resolves to the stop — was designed for
   market-style fills and its bias direction under limit fills is not obvious.

Until those exist, the correct maker claim is the conditional one: *if* passive fills could be
obtained at 0.6% without adverse selection, SOL and ZEC would clear break-even at n=92 and n=50.
Both halves of that sentence are load-bearing.

## 9. Selection bias, stated at full strength

**Every configuration in §3 and §4 is the best cell of its asset's 144-cell slice of an 864-trial
sweep, selected on the same data it is now being re-priced on.** That is the maximum of 144 draws.
It is upward-biased by construction, the bias is not small when the underlying distribution is as
flat as the companion document measured it, and re-pricing a selected cell at seven fees does not
launder the selection — it produces seven biased numbers instead of one.

The sample sizes make it worse rather than better. n = **123** (BTC), **121** (ETH), **92** (SOL),
**50** (ZEC). Two of four clear `min_trades=100`; two do not; and as §4 established, the two that
clear are the two that stay unprofitable at maker. ZEC's 50 trades are half the floor and its
2.713 is the largest profit factor in this document — the maximum of 144 draws on the smallest
sample, which is exactly the combination that should be trusted least.

So, stated as a rule rather than a caveat: **these are not edge estimates and must never be quoted
as any asset's expected profit factor.** What they legitimately support is a *comparison* — the
shape of the fee curve, the ordering of break-even across assets, and above all the contrast in §3
between a rule that is positive at zero cost and one that is not. That contrast is robust to the
selection bias in a way the levels are not, because the bias inflates turtle's cells and turtle is
the arm that wins; correcting for it would move turtle down toward `rsi_meanrev`, and the
`rsi_meanrev` cells were **not** selected for performance at all — the diagnostic grid's declared
objective was `n_trades`, and its own docstring says so before the run.

That asymmetry is worth stating plainly, because it is the reason §3 survives §9: **the selection
bias runs *against* the finding that the two rules differ.** Turtle's numbers are the maxima of a
144-cell search per asset. `rsi_meanrev`'s are not maxima of anything — the zero-fee arm uses one
mid-grid configuration on all three assets (`oversold=30`, the middle of the three swept values;
`support_proximity_pct=0.005`, the shipped default; `level_min_touches=2`) and **it is not the
best cell on any of them.** The best diagnostic cell is `os35/prox0.05/touch2` on BTC,
`os25/prox0.02/touch2` on FET and `os25/prox0.05/touch3` on ZEC. Debiasing would push turtle down
toward `rsi_meanrev` and leave it where it is, which shrinks the gap but cannot invert it: the
best of the 108 diagnostic cells at the taker rate is **0.4556**, and no debiasing moves 0.775 to
the other side of 1.0.

**Consequently, any maker re-test must be a fresh pre-declared hypothesis.** Re-running the sweep
winners at a maker rate and reporting the result is the single most efficient way to convert an
in-sample maximum into a headline. The configurations for a maker test must be chosen without
reference to the 864-cell ranking — fixed in advance from theory, or drawn from a held-out window,
or declared as a small set with its own pre-registration — and the trial count must go in the
ledger before the run. Otherwise the bias in §9 is not merely unaddressed; it is compounded and
then published.

## 10. Trial budget, and why this is two ledger rows

**617 trials today.** 448 on the abandoned grid, 108 on the diagnostic grid, 61 on the fee curve
and zero-fee cells. Added to the prior document's 864, this line of work has now spent **1,481
trials** and produced no positive decision. Both of today's rows are `decision: diagnostic_only`, so
under spec §4.4 neither counts toward `N` — but §4.4 is about decisions, and the trials were spent
regardless. Both facts belong on the record, and the second is the one that constrains what any
future positive result from this rule family is allowed to claim.

Of the 448, only 432 are distinct cells; 16 are re-runs produced by the resume path after the job
was killed mid-write. **They are still counted.** A duplicate trial buys no information but it
consumed a draw, and a budget that only counts informative trials is not a budget. The 144 declared
cells that never ran are *not* counted, because they never ran.

**Two rows, not one.** The argument, since the alternative is defensible and was considered:

**For one row:** the two grids are one investigation with one conclusion, both companions used one
row per document, and splitting a single day's work into two rows inflates `M` for free.

**For two rows, which is what was done:**

1. **`provenance` is a per-row claim about a pre-registration, and there is no single
   pre-registration that covers both.** Each grid has its own docstring, declared before its own
   run. A merged row asserting `a_priori` would be asserting it about a design that never existed
   as a single declaration.
2. **The second grid was declared after seeing the first grid's data.** It is `a_priori` with
   respect to its own hypothesis and posterior to the first grid's results — which is precisely
   what makes it a diagnostic rather than a continuation. Two rows in sequence make that
   relationship legible in the ledger; one row erases it, and erases it in the direction that
   flatters the work.
3. **The ledger's job is to stop trials being re-spent, and a merged row hides the dead grid.**
   Someone asking "has `rsi_meanrev` been swept?" must be able to find a row that says *576 cells
   declared, 448 spent, abandoned as mis-centred, do not re-run this grid* — with its own
   `n_trials`. Folded into a row whose headline is the diagnostic result, that warning becomes a
   sentence in a `params` blob, and the most likely consequence is that the mis-centred grid gets
   run again by someone who did not read to the end.
4. **It rhymes with the document's own finding.** This is a write-up about the cost of pooling two
   things that failed for different reasons. Pooling two grids that answered different questions —
   one an edge search, one a feasibility probe with a declared non-PF objective — into one ledger
   row would make the same category error in the record that §7 argues against in the analysis.

The 61 fee-curve cells are recorded inside the second row (`fee_curve_and_zero_fee_cells: 61`,
with the four `turtle_breakout` configurations named in `params`) rather than given a third row.
They select nothing: they re-price already-ledgered configurations at additional fee rates, and a
row implies a search that did not happen. A purist would give them their own row keyed on
`turtle_breakout`, since the second row's `rule` field reads `rsi_meanrev` and 46 of the 61 cells
are turtle. That objection is fair, the count is reported either way, and nothing in the budget is
hidden by the choice.

**Mechanics.** Both rows were appended with the shipped
`keel/research/ledger.py::append_trial`, which computes `prev_hash` and `row_hash` itself; the
SHA-256 chain was not hand-written. `_decode_summary` coerces every non-`int` summary value to
`Decimal`, so `summary` carries only integers and decimal-parsable strings and all prose lives in
`params`. Both rows carry `series_missing: true` — `backtest` returns aggregate statistics and no
per-trade series, so neither trial can enter a CSCV matrix, and the ledger refuses to let them
pretend otherwise. Chain verification via both `ledger.verify_chain()` and `uv run keel trials
verify` is reported in the PR.

## 11. What this changes, and what it does not

**1. Nothing is demoted, retuned, or reconfigured.** No code, no config, no version. The five live
`turtle_breakout` rules trade the daily clock at ~2.6 trades a year each and nothing here measures
that configuration. `config.live-sandbox.yaml` is untouched for the third document running, for the
reason the first one gave: a reviewed exception in a live config is the reviewer's edit to make.

**2. Neither companion document is corrected.** This is important and easy to get wrong. Neither of
them claims that execution cost is the system-wide binding constraint; the second explicitly
fences the cost argument with the fill-model caution this document's §8 expands. What is corrected
is an inference drawn from reading them together, and the correction is that the inference needs
the zero-fee arm to be checkable at all. Their findings stand as written.

**3. `rsi_meanrev` should not be tuned.** Not in a finer grid, not on more assets, not at a
different horizon on the strength of anything here. It is negative at zero cost on two of three
assets, its response surface across 108 cells is flat to within 0.03 PF per asset, and firing it
six times more often does not move it. The remaining open question about it is not a parameter
question — it is whether a mean-reversion entry of a different construction behaves differently,
and that is a new rule, with a new pre-registration, not a continuation of this one.

**4. The maker-fill model is the recommended next piece of work, and it is engineering, not
research.** §8 names the four things it requires. It should be built and tested against a
pre-declared configuration set (§9), and the honest null is that adverse selection eats enough of
the 1.30%-versus-2.50% gap to leave SOL and ZEC short — with the additional problem that both are
already below the promotion floor.

**5. The asset dimension is unexplored and is the largest measured lever in this document.**
Break-even varies 26× across four assets against parameter effects of a few percent. Nobody has
characterised what drives it. That is a cheaper and more informative question than any further
parameter search, and unlike the fee question it can be asked with the harness that already exists.

**6. Nothing here is a validated conclusion and it must not be cited as one.** Ledgered
`diagnostic_only`, twice, for the reasons in §12.

## 12. Limits

- **No walk-forward and no out-of-sample split.** Every cell was scored on the same 5.07 years the
  configurations were selected on. The fee curve compounds this: it re-prices in-sample winners.
- **No CSCV/PBO and no deflated Sharpe on these configurations.** #247 wired the dormant PBO gate
  into promotion, and none of these trials can feed it: both ledger rows are `series_missing`,
  because `backtest` emits aggregates and no per-trade P&L series. The one method the project has
  for detecting exactly the selection problem §9 describes cannot be run on the numbers in §4.
- **One rule family with a gross edge and one without.** Two rules is not a survey of rule space.
  "Trend following has gross edge here and mean reversion does not" is two observations, not a
  taxonomy, and the mean-reversion arm is a single implementation of a single entry.
- **4 of 19 assets on the fee curve, 6 of 19 in the grids, 3 of 19 in the zero-fee `rsi_meanrev`
  arm.** The fee curve's four were chosen because they were the assets whose sweep winners were
  already known — which is to say, chosen by the selection process §9 warns about.
- **The same cached candles as everything else this project has published.** One venue, one
  2021–2026 crypto cycle. Four assets over one broadly correlated window are not four independent
  experiments, and both of the assets that clear break-even at maker are assets in a directional
  move over part of that window.
- **Trades within a cell are not independent draws.** `backtest` holds one position at a time, so
  a cell's 50 or 887 trades are sequential and overlapping in regime.
- **The ZEC readings — turtle's 2.713 and `rsi_meanrev`'s 1.094 — are both from the asset the
  companion document argued is in a regime, and no regime split was run here either.** Both remain
  arguable in both directions on this data.
- **`slippage_pct` was held at 0.0005 in every cell,** including the zero-fee ones. "Zero fee" is
  therefore zero *fee*, not zero cost: round-trip friction at `fee_pct=0` is still 0.10% of
  notional. Turtle's BTC break-even of 0.068% is a *fee* threshold measured on top of that
  slippage, and the true zero-cost profit factors are marginally higher than the 1.090–2.713
  reported. This makes §3's turtle arm slightly conservative and `rsi_meanrev`'s 0.775 slightly
  flattering.
- **No `finer_candles` in any cell,** so intrabar ambiguity falls back to the module's conservative
  resolution, identically everywhere. It is a level effect on all cells and cannot explain
  differences between them.
- **The first grid's raw file was moving during analysis** (§2). Everything reported from it is
  taken from a pinned snapshot whose md5 is recorded; a reader whose copy differs is not reading
  the same 448 rows.
