# Re-tuning `turtle_breakout` to the hourly clock buys 51% and still loses on everything — 0 of 144 parameter sets

**Date:** 2026-08-11
**Issue:** none. This is the experiment `docs/experiments/2026-08-11-hourly-backtest-turtle-breakout.md`
§9.4 recommended someone file, run on the same day instead of filed. That document identified its
own strongest objection in §7 — *daily-tuned parameters on an hourly clock is arguably a different
strategy* — and deliberately did not answer it. This document answers it. **It assumes the
companion has been read and does not restate it.**
**Change:** **none.** No code, no config, no parameter, no rule status, no version bump, no
demotion. In particular the fee defect this run prices *around* rather than through is still
unfixed on `main`, verified at `2bf5c6f` — see §9.
**Harness:** the shipped `keel/strategy/backtest.py::backtest`, driven over
`Repository.get_candles(product_id, Granularity.ONE_HOUR)` against the paper-forward candle cache
(`~/keel/keel.db`, opened read-only), at `fee_pct=Decimal("0.012")`. Identical code path to the
companion document and to every positive result this project has published. No new instrument was
built; a sweep driver that measured differently from the thing being defended would be worthless.
**Script:** `docs/experiments/2026-08-11-hourly-param-sweep-turtle-breakout.py`. It produced every
number below, and its docstring carries the grid **as declared before the run** (§2).
**Ledger:** one row, `hourly-turtle-param-sweep-2026-08-11`, session
`hourly-turtle-param-sweep-2026-08-11`, `kind: ablation`, `provenance: a_priori`,
`decision: diagnostic_only`, `n_trials: 864`. It changed nothing and must not count toward `N`.

**Verdict: the objection was correct, and correct does not mean exculpatory. Hourly-appropriate
parameters really are better — mean profit factor rises 0.419 → 0.634, +51%, and the winners are
the longest lookbacks exactly as the theory predicted. It is a 51% improvement on a number that
has to double. Zero of 144 parameter sets average PF above 1.0 across six assets; 8 of 864 cells
clear it and all eight are the same asset in the middle of a 30× liquidity surge, at sample sizes
half the promotion floor. The rule was not mistuned. It is negative, and tuning it hourly makes it
less negative, more slowly, at fewer trades.**

| question | answer |
|---|---|
| was §7's objection right that daily params were wrong for hourly? | **yes** — the baseline ranks **112 of 144**; 111 configs beat it |
| does re-tuning rescue the strategy? | **no** — best-of-144 mean PF **0.634**, and **0 of 144** average above 1.0 |
| do longer lookbacks win, as wall-clock reasoning predicts? | **yes** — mean PF rises monotonically-ish 0.407 → 0.538 from `entry_lookback` 40 → 336 |
| how many of the 864 cells beat break-even? | **8 (0.93%)** — against the **~43** that noise alone would hand you at p<0.05 |
| where are those 8? | **all ZEC**, `n` 49–67, in an asset whose recent liquidity is **~30× its own history** |
| does any cell clear PF 1.0 *and* `min_trades=100`? | **no — 0 of 864.** Best cell at n≥100 is ZEC **0.882** |
| does any single parameter axis carry the result? | **no** — every marginal sits in **0.452–0.538**; nothing dominates |
| is there a tuning frontier left to climb? | **no** — Spearman(mean PF, mean n) = **−0.77**. Edge and sample move apart |
| what is left after assets, granularity and parameters? | **the rule, or the execution cost** (§8) — and cost is the larger measured lever |
| is this validated? | **no** (§10) — one grid, one rule, 6 of 19 assets, one window, no walk-forward, no PBO |

---

## 1. What was actually being defended

The companion document's §7 is the reason this one exists, and it is worth being precise about
what it conceded, because the concession was not rhetorical. `TurtleBreakout` hard-codes
`self.granularity = Granularity.ONE_DAY`; `backtest` keys its per-bar window off that declared
attribute; so hourly candles arrive at a rule that believes they are days and cannot find out
otherwise. A `40` that the source comments annotate `# Donchian-high entry (days)` silently became
40 hours — a **1.7-day** channel where the walk-forward that produced the default had fitted a
40-day one. Turtle's "N", twenty days of volatility with fifty years of literature behind it,
became twenty hours and stopped being N.

That is a real defect in the earlier measurement, and it admits a specific alternative hypothesis
that the earlier measurement could not exclude:

> The hourly arm is negative because the parameters are wrong for the clock, not because the rule
> is wrong. Scale the lookbacks to the bar size and the edge comes back.

This is not a weak objection. It is the objection with the best prior: a 1.7-day breakout channel
on a market that trends over weeks is *expected* to be a losing configuration, and finding that it
loses would be a finding about arithmetic, not about trend following. **If that hypothesis is
true, everything the companion document concluded about the rule narrows to a statement about one
badly-scaled parameter set.** So it gets a grid, and the grid gets to win if it can.

## 2. What was run, and what was fixed in advance

144 parameter sets — the full crossing of

```
entry_lookback   [40, 80, 120, 168, 240, 336]     # 40h .. 336h = two weeks
exit_lookback    [20, 40, 80]
atr_stop_mult    [2, 3]
target_rr        [6, 3]
adx_threshold    [25, 20]
```

on six assets — **ZEC, FET, SOL, DOGE, ETH, BTC** — for **864 trials**. All 864 completed; **zero
errors**, no config the rule refused, no cell dropped. `entry_lookback` reaches 336 because that is
two weeks of wall clock, which is roughly where a daily-40 breakout lives once you stop counting in
bars and start counting in time; the six assets were chosen to span the companion document's
measured PF range end to end (ZEC's 0.736 down to BTC's 0.148 at taker) so that no parameter set
could look good merely by being tried on the easy half of the field.

Four commitments were made before the run and are visible in the script's docstring rather than
asserted here after the fact.

**Fee is 1.2% throughout.** The simulator fills market-style at next-bar open; that is taker
behaviour; `config.paperforward.yaml` says so in its own comment. Sweeping at the shipped 0.6%
default would have inflated all 864 cells by roughly the margin that produced the companion
document's only apparent winner in the first place. §9 records what this costs in comparability.

**The headline statistic is mean profit factor across the six assets, not the best cell.** With 864
draws the maximum is close to worthless: it is the order statistic of a large sample and it will be
positive whether or not anything real is present. A parameter set that works on one asset has told
you about that asset. One that works on most has told you about the rule. Profit factor is also the
only cross-asset-comparable number the harness emits — `expectancy` and `max_drawdown` are in units
of a fixed 1-unit notional, so BTC's arrive four orders of magnitude larger than DOGE's and cannot
be averaged.

**Every trial counts and every trial is reported.** 864 goes in the ledger as `n_trials`. Nothing
was run and discarded; there is no second grid behind this one.

**The grid was pre-declared.** It is in the script docstring, unchanged since the run, which is why
the ledger row carries `provenance: a_priori` rather than `fitted`. This matters more than it
usually does: a negative result from a grid chosen *after* seeing the data would be uninterpretable
in the same way a positive one would be.

**Paths were adjusted when the script was copied into the repo** — the run-time original hard-coded
the deployment's `~/keel` on `sys.path`, its `~/keel/keel.db`, and a session scratchpad output path;
the committed copy takes `--db` / `--out` with repo-relative defaults and opens the cache `mode=ro`
per house convention. The grid, the fee, the asset list, the metric and the per-trial body are the
run's. The adjustment is recorded here because a script in `docs/experiments/` is a claim that the
numbers can be reproduced, and a reader who runs it against a different cache should know that is
the only degree of freedom they have been handed.

**Cross-check that the harness is the same one.** The grid contains the companion document's
baseline as cell `40/20/2/6/25`, and that cell reproduces the companion's 1.2% column exactly:
ZEC **0.7363**, FET **0.6231**, DOGE **0.3747**, BTC **0.1484** against its published 0.736 /
0.623 / 0.375 / 0.148. The two runs share no code beyond the package. That is the cheapest
available evidence that this sweep is measuring the same object the earlier document measured, and
it was checked before anything below was believed.

## 3. Result 1 — the objection is correct, and it is correct in the direction it predicted

Take the concession completely, because it is earned.

**The daily-tuned baseline is a bad hourly configuration.** Cell `40/20/2/6/25` ranks **112th of
144** by mean PF, at **0.4187**. One hundred and eleven parameter sets in a pre-declared grid beat
the one that was measured. Anyone who read the companion document as "turtle_breakout is negative
hourly" was reading a number produced near the bottom of its own parameter distribution.

**And the improvement arrives exactly where wall-clock reasoning says it should.** Marginal mean PF
by `entry_lookback`, averaged over all 24 configs sharing each value:

| `entry_lookback` | wall clock | mean n | mean PF | best config at this length |
|---:|---|---:|---:|---:|
| 40 | 1.7 days | 281 | 0.407 | 0.477 |
| 80 | 3.3 days | 216 | 0.452 | 0.534 |
| 120 | 5 days | 167 | 0.455 | 0.543 |
| 168 | 1 week | 132 | 0.500 | 0.584 |
| 240 | 10 days | 120 | 0.494 | 0.540 |
| 336 | **2 weeks** | 98 | **0.538** | **0.634** |

The ordering is essentially monotone in horizon, the single inversion (240 below 168) is within
noise at this spread, and the top is the longest channel in the grid. This is not a subtle pattern
that needed teasing out: **the closer the hourly configuration gets to the daily rule's wall-clock
horizon, the better it does.** The parameters were mis-scaled, the mis-scaling cost real
performance, and re-scaling recovers a real fraction of it — from 0.419 to 0.634, **+51%**.

The top of the grid, by mean PF across the six assets:

| entry | exit | atr | rr | adx | mean PF | assets > 1.0 | mean n |
|---:|---:|---:|---:|---:|---:|:---:|---:|
| 336 | 80 | 2 | 6 | 25 | **0.634** | 1/6 | 91 |
| 336 | 40 | 2 | 6 | 25 | 0.606 | 1/6 | 91 |
| 336 | 20 | 2 | 6 | 25 | 0.600 | 1/6 | 93 |
| 336 | 40 | 3 | 3 | 25 | 0.592 | 1/6 | 91 |
| 168 | 80 | 2 | 6 | 25 | 0.584 | 0/6 | 130 |

So the objection stands, in full, on its own terms. **§7 of the companion document was right.**

## 4. Result 2 — and every one of the 144 loses money

**Zero of 144 parameter sets average a profit factor above 1.0 across the six assets.** Not the
best one. Not one. The grid's champion is 0.634, which means that after the best tuning available
in a 144-cell pre-declared search, the strategy returns **63 cents of gross profit per dollar of
gross loss**.

The distribution is the argument, not the maximum. Across all 864 cells:

| statistic | value |
|---|---:|
| min | 0.095 |
| first quartile | 0.321 |
| median | **0.476** |
| third quartile | 0.621 |
| max | 1.303 |
| fraction ≤ 1.0 | **99.07%** |
| fraction < 0.7 | 84.8% |

The median cell loses half of every dollar it risks gross. The *third quartile* is 0.621 — 75% of
the search space sits below a number that would still be a catastrophic strategy. To reach
break-even from the median, a cell has to roughly double, and the entire measured range of the
grid, floor to ceiling, is 1.21 in PF units spread across six assets that differ from each other by
more than the parameters do.

**No single axis carries anything.** Marginal mean PF over all 864 cells:

| axis | values | mean PF |
|---|---|---|
| `adx_threshold` | 20 / 25 | 0.461 / 0.488 |
| `target_rr` | 3 / 6 | 0.452 / 0.497 |
| `atr_stop_mult` | 2 / 3 | 0.458 / 0.491 |
| `exit_lookback` | 20 / 40 / 80 | 0.456 / 0.474 / 0.494 |
| `entry_lookback` | 40 … 336 | 0.407 … 0.538 |

Every marginal outside `entry_lookback` moves by less than 0.05. That flatness is itself
diagnostic, and it is the second-strongest evidence in this document. When a strategy has an edge,
its parameters matter — there is a ridge, the axes interact, and the response surface has shape.
Here the surface is nearly level: the stop multiple, the target R:R and the trend filter each
change the answer by about 5%, in the direction you would guess, by an amount indistinguishable
from the cost of trading slightly more or less. **A flat response surface over a losing region is
what "there is nothing here to find" looks like when you sample it 864 times.**

The one axis that does move things, `entry_lookback`, moves them by trading less often — which is
§6.

## 5. Result 3 — eight winners in 864, all of them one asset, and that asset is in a regime

**Eight cells of 864 (0.93%) clear PF 1.0.** Every single one of them is ZEC.

| asset | best PF | n at best | best config | cells > 1.0 | mean PF over 144 |
|---|---:|---:|---|:---:|---:|
| BTC | 0.333 | 123 | e168 / x80 / atr3 / rr6 / adx25 | 0/144 | 0.200 |
| DOGE | 0.834 | 86 | e336 / x40 / atr2 / rr6 / adx20 | 0/144 | 0.539 |
| ETH | 0.556 | 121 | e240 / x80 / atr3 / rr3 / adx25 | 0/144 | 0.323 |
| FET | 0.926 | 89 | e336 / x80 / atr3 / rr3 / adx20 | 0/144 | 0.653 |
| SOL | 0.801 | 92 | e336 / x80 / atr3 / rr6 / adx20 | 0/144 | 0.455 |
| ZEC | **1.303** | **50** | e336 / x20 / atr2 / rr6 / adx25 | **8/144** | 0.676 |

Five of the six assets have **no** winning cell in a 144-cell search. Their best configurations —
each the maximum of 144 draws, each therefore biased upward — are 0.33, 0.56, 0.80, 0.83, 0.93.
The whole positive region of this experiment is one asset, and within that asset it is eight
adjacent cells at the long end of `entry_lookback`, all with `adx_threshold=25`.

**Start with the number that should have been positive and was not.** With 864 trials, a
conventional p<0.05 threshold would produce roughly **864 × 0.05 ≈ 43** apparently-good cells from
noise alone against a null of zero edge. We got **8**. Under-shooting the false-positive rate by
5× is not a null result — it is a positive claim about the distribution, and the claim is that the
underlying returns are shifted so far negative that random variation *rarely reaches* break-even.
The usual multiple-testing worry is that a large grid manufactures winners. This grid could not
manufacture the number of winners that pure chance would have been entitled to.

**Now ZEC.** The temptation is to read 8/144 as a surviving pocket of edge. Three facts argue it is
a regime.

**First, ZEC is the only one of the six in a liquidity surge, and the surge is enormous.** Using
the project's own liquidity statistic — `compliance/screen.py::median_daily_quote_volume`, median of
`volume × close` — over its own 180-day probe window against full cached history:

| asset | median daily quote volume, last 180d | full-history median | ratio |
|---|---:|---:|---:|
| **ZEC** | **$37,935,862** | **$1,229,309** | **30.9×** |
| BTC | $510,573,948 | $568,943,908 | 0.9× |
| ETH | $226,083,062 | $331,500,712 | 0.7× |
| SOL | $72,938,484 | $107,673,223 | 0.7× |
| DOGE | $14,579,103 | $29,840,712 | 0.5× |
| FET | $2,206,901 | $4,723,288 | 0.5× |

(The scout run's stored figure of ~$33.1M / 27× is the same fact on the 2026H1 window; the ratio is
27–31× depending on where you cut, and nothing in the argument turns on which. Every other asset in
the sweep is trading *below* its own historical median.)

**Second, the surge is a trend, which is the one thing this rule is built to capture.** ZEC's daily
closes by half-year: 27.79 → 20.93 (2024H1), 20.75 → 56.19 (2024H2), 58.09 → 38.18 (2025H1), then
**36.92 → 510.43 in 2025H2** — a 13.8× move in six months — and 525.17 → 398.93 across 2026H1. The
median daily quote volume over those same halves runs $455K, $935K, $550K, then **$16.3M**, then
**$33.3M**. A trend follower placed on a single asset during a single 14× directional move will
print a profit factor above 1. That is not evidence of edge; it is the definition of the strategy
working *once*, on one draw, in the conditions it was designed for. The question a promotion gate
exists to ask is whether it does so repeatably, and one asset in one regime is the exact shape of
evidence the gate is built to reject.

**Third, the winning cells are all below the sample floor.** The eight cells above PF 1.0 have
`n` ∈ {49, 49, 50, 51, 51, 52, 66, 67}. The promotion floor is `min_trades=100`
(`keel/strategy/promotion.py:56`). The best cell in the entire experiment — ZEC at 1.303 — rests on
**50 trades, exactly half the floor**, and it is the maximum of 864 draws. It is not merely
unpromotable; it is the single number in this document with the widest error bars, selected
precisely *because* it was the largest.

**What this document cannot do is prove the regime hypothesis.** No regime split was run. The
honest statement is that 8/144 concentrated in the one asset with a 30× liquidity surge and a 14×
trend is what a regime artifact looks like, and that the alternative — ZEC has a durable
asset-specific edge that five other assets lack — makes the same prediction on this data. **The
experiment that separates them is a split: re-run ZEC's eight winning configurations on the
pre-surge window (through mid-2025) and the surge window separately.** If the edge lives entirely
in the surge, the pocket closes. If it survives the pre-surge years at usable `n`, ZEC deserves a
real look. That test is cheap, it is eight configurations rather than another grid, and it is the
follow-up this document recommends over any further parameter search (§9).

## 6. Result 4 — the bind: edge and sample size move in opposite directions

This is the structural result, and it is why "keep tuning" is not the answer to §4.

The only axis that improved anything was `entry_lookback`, and it improved things **by trading
less**. Re-read the §3 table with the `mean n` column in front:

| `entry_lookback` | mean n | mean PF | clears `min_trades=100`? |
|---:|---:|---:|:---:|
| 40 | 281 | 0.407 | yes |
| 80 | 216 | 0.452 | yes |
| 120 | 167 | 0.455 | yes |
| 168 | 132 | 0.500 | yes |
| 240 | 120 | 0.494 | yes |
| 336 | **98** | **0.538** | **no** |

Across the 144 configurations, **Spearman ρ(mean PF, mean n) = −0.77** (Pearson −0.76). That is not
a weak tendency; over this grid, how good a configuration looks and how much evidence supports it
are close to the same number with the sign flipped.

The consequences are exact:

- Best mean PF among configurations averaging **n ≥ 100**: **0.584** (`e168/x80/atr2/rr6/adx25`).
- Best mean PF among configurations averaging **n < 100**: **0.634** (`e336/x80/atr2/rr6/adx25`).
- **Cells clearing PF 1.0 *and* n ≥ 100: 0 of 864.** 707 of the 864 cells have n ≥ 100; the best
  profit factor among all of them is **0.882** (ZEC, `e80/x20/atr3/rr3/adx25`, n=197).

So the entire gain from the winning end of the grid — the whole of the 0.584 → 0.634 step, and all
eight break-even cells — is bought by dropping below the sample size at which the project agrees a
result means anything. **The configurations closest to working are exactly the configurations that
cannot be promoted, and it is the same fact that makes them both.**

That is worth stating as something other than an inconvenience. It is not a tuning problem awaiting
a cleverer search, and there is no reason to expect a finer grid between 240 and 336, or an
extension to 500, to escape it: pushing the channel out further will keep raising PF slowly and
keep cutting `n` fast, and the two lines were already crossing before the grid ended. **This is the
shape of the strategy on this clock.** A rule that only looks viable when you stop collecting
evidence about it is a rule about which you have decided not to collect evidence.

## 7. What is now eliminated

Three levers have been tested and none of them is where the problem is.

| lever | how it was tested | result |
|---|---|---|
| **asset selection** | 19 assets, hourly, baseline params (companion §4) | negative on 19/19 at taker |
| **granularity** | daily → hourly, same params, same window (companion §3–4) | sample size fixed, edge did not appear |
| **parameters** | **144 sets × 6 assets = 864 trials, this document** | **0/144 above break-even** |

Each was a plausible explanation of the earlier negative result before it was checked; none
survived. The remaining explanations are not a longer list, they are a shorter one:

1. **The rule has no edge on crypto at these horizons** — the 40/20 Donchian-plus-ADX structure
   simply does not extract more from this market than it gives back.
2. **The rule has an edge that execution cost consumes** — the gross signal is real and the round
   trip eats it.

These are distinguishable, and §8 argues the second is both the cheaper and the more informative
one to attack next.

## 8. Why execution cost is the next test, and why another sweep is not

At `fee_pct = 0.012` with `slippage_pct = 0.0005` unchanged, round-trip friction is
`2 × 0.012 + 2 × 0.0005 = 2.50%` of notional. At maker pricing it is `2 × 0.006 + 2 × 0.0005 =
1.30%` — **a 48% reduction in the dominant cost term**, on a strategy whose win rates in this sweep
run 13.6%–37.1% (mean 24.1%). At a ~20% win rate the average winner must cover four average losers
*plus* eight legs of fees before the strategy breaks even; halving the fee is not a tweak to that
arithmetic, it is the largest term in it.

The size of that lever can be read off numbers already published. The companion document's §4 gives
these same six assets at the same baseline parameters priced at maker: ZEC 1.042, FET 0.858, SOL
0.585, DOGE 0.558, ETH 0.479, BTC 0.318 — **mean 0.640**. This sweep's best of 144 tuned
configurations at taker is **0.634**.

> **Changing the fee alone, with the mis-scaled daily parameters left in place, is worth more than
> the entire 144-cell parameter grid.**

That single comparison is the argument for what to test next. The grid moved the mean by +0.215;
the execution model moves it by +0.221 without touching a parameter, and the two effects act on
different terms so there is at least a chance they stack. Nobody has run the cell that matters —
**the tuned long-lookback configuration priced as a maker fill** — and it is one configuration, not
a grid, so it costs almost nothing against the multiple-testing budget.

Three cautions, so this does not get quoted as a plan that is expected to work.

**Maker pricing is not a free re-label; it is a different fill model.** A breakout entry executed as
a resting limit order does not fill on the bar that breaks out — it fills when price comes back to
it, which selects disproportionately for breakouts that failed. That adverse selection is not in
the 0.640 figure, which is a *taker* fill priced at a *maker* rate and is therefore an upper bound
on what maker execution could buy. Testing this properly means modelling limit fills, not editing a
constant, and the honest expectation is that some of the 0.221 is given straight back.

**0.640 is still below 1.0.** Even the upper bound does not reach break-even. Execution cost is
therefore very likely **necessary and not sufficient**, which means the interesting version of the
test is fee model and long-lookback parameters together, with the null being that the combination
still lands short.

**Another parameter sweep is the one thing not worth doing.** §4 showed a flat response surface,
§6 showed the only productive axis trades PF against `n` at ρ = −0.77, and this document already
spent 864 trials establishing both. A finer grid inside a region that has been sampled 864 times
buys nothing except a larger multiple-testing burden and a better chance of finding the 43rd noise
cell.

## 9. What this cost, and what it is priced against

**864 trials.** That is the largest single draw on this project's multiple-testing budget to date,
and it bought a negative result. It is recorded in the ledger as `n_trials: 864` rather than as one
tidy row implying one experiment, specifically so that nobody re-runs this grid without knowing it
has already been run and paid for. The ledger row is `decision: diagnostic_only`, so under spec
§4.4 it does not count toward `N` — but §4.4 is about *decisions*, and the trials were still spent.
Both facts belong on the record.

`provenance: a_priori` is load-bearing here. The grid in the script's docstring is the grid that
ran; no cells were added after seeing results and none were dropped. `kind: ablation` follows the
convention the recent diagnostic write-ups established (`between-family-*`, `cts-factor-*`,
`hourly-turtle-granularity-*`) rather than `sweep_node`, which this ledger uses one-row-per-node
with `provenance: fitted` and would misdescribe a single aggregate row over 864 pre-declared cells
from which nothing was selected.

**These numbers are stricter than the project's own gate, and that is a defect, not a virtue.** The
sweep priced every one of the 864 cells at taker 1.2%. Meanwhile, verified on `main` at `2bf5c6f`:

```
keel/strategy/backtest.py:171     fee_pct: Decimal = Decimal("0.006")
keel/sim/portfolio_sim.py:225     fee_pct: Decimal = Decimal("0.006")
keel/strategy/paper.py:46         _DEFAULT_FEE_PCT = Decimal("0.006")
keel/cli.py:1691                  _SIM_FEE_PCT = Decimal("0.006")
```

`promotion.can_promote` reads `expectancy`, `win_rate` and realized R:R off statistics produced at
those defaults. So the gate this document keeps measuring configurations against is itself
evaluated at half the realistic cost — the defect the companion document raised in its §9.2 and
deliberately did not fix. The practical consequence for this write-up is a comparability hazard:
**every profit factor here is lower than a number produced by `keel rules backtest` for the same
configuration**, and anyone diffing the two without reading this paragraph will conclude the sweep
is broken. **That defect should be fixed before anyone re-litigates this result**, because until it
is, the sweep and the gate are denominated in different currencies. It is not fixed here for the
same reason it was not fixed there: changing a fee default moves the gate for every rule at once
and restates the numbers in prior documents, which is a code change with its own before/after
obligation and does not belong inside a research write-up that would then be arguing from its own
patch.

## 10. What this changes, and what it does not

**1. Nothing is demoted, retuned, or reconfigured by this document.** The five live
`turtle_breakout` rules trade the **daily** clock at ~2.6 trades per year each. This sweep did not
measure that configuration, and the companion document's §8 reasoning — the caps bound the damage,
the sandbox is the only source of live evidence, acting against an untested configuration would
repeat the error in the other direction — is unchanged by anything here. What this does remove is
the last available reading under which the hourly evidence could be dismissed as an artifact of
mis-scaled parameters.

**2. The §7 objection is answered and should stop being cited as an open question.** It was correct
about the mechanism, it accounted for a real 51% of the performance gap, and it does not reach
break-even. The companion document's finding narrows from "turtle_breakout is negative hourly" to
the more defensible and more damaging *"turtle_breakout is negative hourly across its parameter
space, and the best available tuning gets 63% of the way to losing nothing"*.

**3. Recommended next test, in preference order.** (a) The ZEC regime split of §5 — eight
configurations, pre-surge versus surge window, which settles whether the only positive cells in 864
are an edge or a regime; this is the highest information per trial available. (b) The maker-fill
model of §8, tested with tuned parameters, which is the largest measured lever left and is worth
knowing about regardless of this rule. (c) **Not** another parameter sweep (§8).

**4. Recommended issue, unchanged and now more urgent: fix the fee default** (§9). Two independent
documents now report numbers that cannot be compared to the CLI's output without a paragraph of
explanation.

**5. Nothing here is a validated conclusion and it must not be cited as one.** It is a screening
result, ledgered `diagnostic_only`, and §11 is the list of reasons.

## 11. Limits

- **No walk-forward and no out-of-sample split.** Every one of the 864 cells was fitted and scored
  on the same window. The 144-cell ranking in §3 is an in-sample ranking; the only reason it can
  carry weight is that its conclusion is negative, and in-sample optimism biases *against* that
  conclusion.
- **No CSCV/PBO and no deflated Sharpe.** This is a 144-configuration grid — precisely the object
  those methods exist to evaluate — and neither was run. The §5 argument from "8 observed versus
  ~43 expected under the null" is a back-of-envelope substitute for a PBO computation, not a
  replacement for one. The ledger row carries no P&L series (`series_missing: true`), so this trial
  cannot enter a CSCV matrix at all.
- **One rule, one grid.** 144 sets is a large search over five axes and a small one over the space
  of trend-following rules. Nothing here tests position sizing, pyramiding, portfolio-level
  allocation, or a different entry structure.
- **6 of 19 assets.** Chosen before the run to span the observed PF range, which is the right
  criterion for detecting a parameter set that works broadly, but six is six. A parameter set that
  works only on the thirteen assets not swept would have been missed.
- **The same cached candles as everything else.** One venue, one 2021–2026 crypto cycle, the same
  `~/keel/keel.db` behind every result this project has published. Six assets over one broadly
  correlated window are not six independent experiments, and a trend follower's fate is largely a
  fact about the regime — which is the §5 argument turned against the whole document, and it
  applies.
- **Trades within a cell are not independent draws either.** `backtest` holds one position at a
  time, so a cell's 91 or 281 trades are sequential and overlapping in regime.
- **The regime hypothesis for ZEC is argued, not measured.** §5 names the split that would settle
  it; until that is run, "probably regime" is a reading of the evidence and the competing reading
  survives on this data.
- **Fee comparability.** Every number here is taker-priced against a project that gates at maker
  (§9). Stricter, but not comparable without the caveat.
- **No `finer_candles` in any cell**, so intrabar ambiguity falls back to the module's conservative
  resolution, identically across all 864 cells. It cannot explain differences *within* the grid;
  it is a level effect on all of them.
