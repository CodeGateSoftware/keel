# Three rules, 24 assets, zero free parameters — the viable intersection is empty, and one of the three fails for the opposite reason we recorded

**Date:** 2026-08-12
**Issue:** #251
**Change:** documentation only. No code, no config, no rule status, no parameter, no version bump.
**Script:** `docs/experiments/2026-08-12-shipped-defaults-intersection.py` — produced every number
below, and is the file that was resumed four times (see §7).
**Ledger:** two rows, `shipped-defaults-intersection-2026-08-12`, session
`shipped-defaults-intersection-2026-08-12`.
**Deployment:** run against `keel 0.7.0`, i.e. **after** #247's taker-fee correction shipped. Every
figure here passes `fee_pct` and `slippage_pct` explicitly, so none of them depends on a library
default; §7 records the check that the pre-upgrade rows reproduce exactly.

**Verdict: across every signal rule the codebase ships, on every asset with hourly history, there
is no combination that clears the trade floor with an edge that survives the cheapest fee we can
reach. The three rules fail for three unrelated reasons, and pooling them loses the only
information that says where to look next.**

| question | answer |
|---|---|
| any asset-rule combo with `n≥100` ∧ gross>1 ∧ net@0.6%>1? | **one**, ZEC-`turtle` — then eliminated in §5 |
| the same at the 1.2% we actually pay? | **none**, 0 of 72 |
| is the 864-trial sweep winner overfit? | **no** — 0.6335 in-sample → **0.6346** out-of-sample |
| does `rsi_meanrev` lack gross edge, as #248 recorded? | **no** — it has the *best* of the three; it lacks observations |
| is `pullback_continuation` (never before backtested) different? | **yes, worse** — median gross **0.929** |
| do the three rules share a failure mode? | **no** — cost, signal, and sample size respectively |

---

## 1. What was declared, and when

Two arms, both fixed **before the run** — but in the brief that dispatched the implementing
agent, **not** in the script, which had no docstring until this write-up was prepared. Every other
harness in this directory carries its own pre-registration in the file; this one's lives in a session
transcript, which is weaker, and the script now says so at the top rather than presenting the
reconstruction as though it had always been there. Future runs put the declaration in the file.

**Arm A — `a_priori`, zero free parameters.** All three signal rules constructed with
`product_id=<asset>` and *nothing else*, so every other parameter takes its shipped constructor
default. Those defaults were written before this corpus existed and were never tuned on it, which
makes them the only genuinely unselected configuration available. 3 rules × 24 assets = 72
combinations.

**Arm B — out-of-sample transfer.** The 864-trial hourly sweep
(`docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev-sweep.py`) picked
`{entry_lookback: 336, exit_lookback: 80, atr_stop_mult: 2, target_rr: 6, adx_threshold: 25}` as
its mean-across-assets winner, scored on six assets. Arm B evaluates that exact config on the
**eighteen assets that were not in the sweep**. The evaluation selects nothing, so it consumes no
further multiple-testing budget; what it tests is whether the sweep's choice generalises.

**Costs.** Three fee levels — `0`, `0.006` (maker), `0.012` (taker) — with `slippage_pct` pinned
**explicitly** at `0.0005` in every call. That pin matters and was missing from earlier work: the
"zero fee" column is zero *fee*, not zero *cost*, because the shipped 5bp slippage default applies
regardless. Every gross figure in this document, and every break-even quoted in
`2026-08-12-fee-curve-and-rsi-meanrev.md`, already carries that slippage on both legs.

**Universe.** All 24 products with `ONE_HOUR` candles in `keel.db`; 20 of them carry ~44k bars back
to 2021. No asset was excluded for any reason, including the three that turn out to be nearly
untradeable by these rules.

90 combinations × 3 fees = **270 backtests**, 0 errors.

## 2. The result

| rule | `n≥100` | ∧ gross>1 | ∧ net@0.6%>1 | ∧ net@1.2%>1 |
|---|---:|---:|---:|---:|
| `turtle_breakout` | 21/24 | 7 | **1** (ZEC) | 0 |
| `pullback_continuation` | 4/24 | 1 (PAXG-USDT) | 0 | 0 |
| `rsi_meanrev` | 0/24 | — | — | 0 |
| **Arm A total** | **25/72** | **8** | **1** | **0** |

The columns are cumulative — each adds a condition to the one before it. `rsi_meanrev`'s later
columns are dashes rather than zeroes because the first condition already empties the set; it is
gross-positive on 14 of 24 assets, but never with enough trades to be admitted (§4).

## 3. Three rules, three unrelated failures

The single most useful thing in this dataset is that the rules do **not** fail the same way.

| rule | gross edge | cost sensitivity | fires enough? | so the lever is |
|---|---|---|---|---|
| `turtle_breakout` | real, broad — 7/21 gross-positive at `n≥100` | moderate | yes, median n=241 | **cost** |
| `pullback_continuation` | ~none — mean 1.043, **median 0.929** | **extreme** | marginal, 4/24 | **signal** |
| `rsi_meanrev` | **best of the three** — median **1.1631**, 58% positive | moderate | **no**, 0/24 | **sample size** |

Any statement of the form "the strategy layer is dead" collapses these into one claim and destroys
the only guidance the data contains. Cheaper execution rescues exactly one of these three rows.

### 3.1 `pullback_continuation` — a signal failure, measured for the first time

This rule has been in `RULE_REGISTRY` since it was written and had **never been backtested**. It is
now measured: 24 assets, median `n` 60, only 4 clearing the floor.

```
product        n    PF@0   PF@0.6%  PF@1.2%   win@0
WLD-USD       12   2.810    1.650    0.881   75.0%
FET-USD       30   1.996    1.463    1.056   56.7%
UNI-USD       90   1.118    0.285    0.050   52.2%
PAXG-USDT    100   1.097    0.001    0.000   65.0%
ETH-USD      101   0.990    0.067    0.007   56.4%
ZEC-USD      128   0.875    0.290    0.102   53.9%
LTC-USD      123   0.695    0.037    0.005   53.7%
                 mean gross 1.0430   median gross 0.9292
```

**The fee collapse is qualitatively different from `turtle`'s.** PAXG-USDT goes 1.097 → **0.001**
at the maker rate. ETH 0.990 → 0.067. BTC 0.897 → 0.050. `turtle` at the same 0.6% retained
structure (ZEC 1.042, BTC 0.318); this goes to the floor.

The cause is legible in the win rates: **52–75% wins at gross PF ≈ 1.0** is small wins against
small losses, which is what `target_method="measured_1to1"` on an EMA touch produces. When per-trade
edge is a fraction of a percent, a 1.2% round trip does not reduce it, it erases it. The high win
rate is the *symptom* of maximal fee fragility here, not a strength — and it is the profile most
likely to be mistaken for a good result by anyone reading win rate first.

No fee schedule fixes a median gross PF of 0.929. This family needs re-engineering or retirement,
and that is a different sentence from the one `turtle` earns.

### 3.2 `turtle_breakout` — a cost failure

At shipped defaults, 21 of 24 assets clear `n≥100`, and 7 are gross-positive:

```
PAXG-USDT 1.248   ZEC 1.555   XRP 1.382   FET 1.255
CRV 1.083   ETH 1.044   AAVE 1.028
```

Every one of them dies on cost. The mechanism is worth stating precisely because it explains why
the collapse is so violent: **the toll is levied on the search, not on the edge.** A tail-sensitivity
probe (below) shows the profit concentrated in a handful of trades, so the account pays ~241
round-trip tolls in order to be present for the few that pay. Halving the toll does not halve the
number of times it is paid.

Concentration, at zero fee, with the best 1 and best 3 winning trades removed (losses all retained
— a deliberately harsh stress, not an unbiased estimator):

| asset | n | gross | top-3 share | ex-top1 | **ex-top3** |
|---|---:|---:|---:|---:|---:|
| ZEC-USD | 250 | 1.555 | 33.4% | 1.248 | **1.035** |
| XRP-USD | 144 | 1.382 | 29.7% | 1.221 | **0.972** |
| FET-USD | 259 | 1.255 | 29.2% | 1.126 | **0.889** |
| PAXG-USDT | 224 | 1.248 | 23.5% | 1.116 | **0.955** |
| CRV-USD | 246 | 1.083 | 45.5% | 0.888 | **0.590** |
| ETH-USD | 266 | 1.044 | 13.4% | 0.993 | **0.904** |
| AAVE-USD | 233 | 1.028 | 16.3% | 0.968 | **0.860** |

Deleting 3 trades out of ~250 — **1.2% of the sample** — takes six of seven below break-even.

**This is not by itself evidence of no edge.** A fat right tail *is* the strategy; a breakout system
with evenly distributed profits would be the surprising result. What it does establish is that
`min_trades=100` is measuring the wrong quantity for this family: n=250 reads as a large sample and
licenses confidence, while the number of independent events carrying the PnL is single-digit. The
gate counts trades; it cannot see that three of them are the result.

## 4. `rsi_meanrev` — this supersedes #248

`docs/experiments/2026-08-12-fee-curve-and-rsi-meanrev.md` recorded that `rsi_meanrev` "has no
gross edge at all", on the strength of BTC at 0.775 gross with n=255. Measured across the full
universe at shipped defaults, that generalisation does not hold:

```
n>=100: 0/24      median n 38      max n 81 (ETH)
gross PF: mean 1.1316   median 1.1631   gross-positive 14/24 (58%)
```

**`rsi_meanrev` has the best gross-edge distribution of the three rules** — median gross **1.1631**
against `turtle`'s **0.9892** and `pullback`'s **0.9292**, all over the same 24 assets. BTC sits near the
*bottom* of its distribution at 0.745, not at its centre.

Two things reconcile this with #248 without either being wrong on its own terms:

1. **#248's rsi figures came from *widened* parameters**, not defaults — the fee-curve rows at
   n=255–341 used `oversold` 25–35 and `support_proximity_pct` 0.02–0.05, chosen to make the rule
   fire. This document measures the shipped defaults, which are far more selective.
2. **#248 generalised from one asset.** That is the error, and it is mine; the correction is the
   distribution above, not a change in BTC's number, which reproduces exactly.

So the rule's failure mode is **sample-size suffocation**: 0 of 24 assets reach the promotion floor,
median n=38 against `min_trades=100`. It does not lose. It is not observable.

That reframes the open question and is the one live lead this study produces (§8).

## 5. Arm B — the sweep winner is not overfit, and that is worse

```
in-sample mean net PF @1.2%, on its 6 selection assets : 0.6335
out-of-sample mean, on 18 disjoint assets              : 0.6346    median 0.5138
out-of-sample mean GROSS                               : 1.5317    median 1.2311
n: median 84, only 1 of 18 reaches n>=100
```

A configuration chosen as best-of-144 on six assets reproduces its mean net profit factor **to three
decimal places** on eighteen assets it never saw. This is the opposite of overfitting. The sweep
found a real, stable, transferable property of the rule, and the property is that it loses after
costs.

That is a harder result than overfitting would have been. Overfitting is a methodology defect with a
methodology fix. A clean out-of-sample replication of 0.63 is a measurement.

**What does survive from the earlier reading** is narrower and still holds: the config buys its
higher gross PF with trade count. Median n falls from **241** at defaults to **84** here, and Arm B's
intersection is empty on all three criteria, including `n≥100 ∧ gross>1`. Its one asset above the
floor is LINK at gross 0.764. Longer lookbacks find *more* gross edge (67% of assets gross-positive,
mean gross 1.53) on *fewer* trades, and still cannot pay 1.2%.

Both arms, from opposite parameter regimes, say the same thing: the gross edge is real and broad;
the toll is what removes it.

## 6. The temporal probe, and why it is reported rather than gated

ZEC-`turtle` was the only combination in the study to clear all three cumulative criteria. A
year-by-year decomposition removes it:

| asset | n | gross | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | streak all/full |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZEC-USD | 250 | 1.555 | 1.85 | **0.66** | **0.30** | **0.96** | 2.10 | 1.51 | 3/3 |
| PAXG-USDT | 224 | 1.248 | 0.27 | 0.30 | 0.44 | 0.77 | 2.10 | 1.71 | 4/3 |
| XRP-USD | 144 | 1.382 | — | — | 0.35 | 2.37 | 1.10 | 1.39 | 1/1 |
| AAVE-USD | 233 | 1.028 | 1.01 | 1.41 | 0.94 | 1.44 | 0.66 | — | 1/1 |

**ZEC ran three consecutive losing years and compressed 92.7% of its lifetime net PnL into
2025–2026.** Its 1.555 is not a distributed structural edge; it is a recent regime sitting on top of
a three-year drawdown an operator would have had to fund. Across all 21 assets clearing the floor,
**none has a losing-year-free record**, and only XRP and AAVE avoid consecutive losing years — both
of which still net below 1.0 at every fee level (XRP 0.773 at maker, 0.490 at taker).

Three probes, each catching what the others miss:

| probe | eliminated |
|---|---|
| fee curve | 20 of 21 — only ZEC survived at maker |
| tail-sensitivity | 6 of the 7 gross-positive — only ZEC survived |
| **temporal** | **ZEC** — the one both others spared |

**This probe is reported, never gated**, for three reasons that are load-bearing:

1. **It is underpowered.** Yearly outcomes for an edgeless strategy are near coin-flips, and the
   number of length-4 sequences with no two consecutive losses is 8 of 16. A no-consecutive-losing-
   years rule passes a zero-edge strategy roughly **50%** of the time (40% at five buckets). It can
   flag the egregious cases — ZEC, PAXG-USDT — and nothing finer.
2. **It is fragile to bucket boundaries.** 2021 and 2026 are partial buckets (data starts mid-July
   2021, ends July/August 2026). Excluding them **changes the verdict for 4 of 21 assets**. FET
   flips from fail to pass: its full-year sequence is `1.08, 1.30, 1.65, 0.68`, one losing year, so
   the "alpha decay" reading of FET rests entirely on a 7-month partial bucket with n=34. A metric
   that moves when you change where the year starts cannot gate a promotion.
3. **Stationarity is not edge.** The decisive counter-example is in this dataset:
   **ZEC-`pullback_continuation` is the only asset-rule combination in the whole study with no
   losing complete year** — 1.06, 1.17, 1.16, 1.13 across 2022–2025 — and its gross PF is **0.875**,
   netting 0.290 at maker. It is perfectly stationary at losing slightly, every year, reliably. A
   stationarity gate would have waved it through while rejecting ZEC-`turtle`, which at least made
   money gross.

The principled instrument for this already exists and is now live: `keel/research/cscv.py`,
`deflate.py` and `matrix.py` shipped long ago, and #247 wired `g4_pbo_gate` into `can_promote`,
where `pbo=None` returns `NOT_RUN` and blocks. **It is deployed and nothing feeds it.** Building a
weaker annual-bucket heuristic beside an unfed rigorous one is the wrong order of work.

### 6.1 A cross-rule observation about the corpus itself

PAXG-USDT shows the **same** temporal signature under both `turtle` (streak 4: 2021–2024 sub-1.0,
then 2.10, 1.71) and `pullback` (streak 3: 0.19, 0.57, 0.54, then 3.09, 1.44, 2.82). Two
structurally unrelated rules — a breakout and a pullback-continuation — both lose on gold for three
or four years and both turn positive in 2024.

That is not a rule property. It is a property of gold's 2024+ regime, and it means any aggregate
metric over this corpus is partly measuring that macro shift whichever rule is applied. The same
caution applies to ZEC. It is an argument for walk-forward evaluation over whole-corpus aggregates,
independent of everything else here.

## 7. Method notes, including the errors

**The simulator's open-position handling is correct and was not changed.** `summarize()` excludes
unclosed trades from every aggregate — `closed = [t for t in trades if t.outcome != "open"]` — and
says so in its docstring. LINK's `n=240` is 240 *closed* trades. Marking the open position to market
at the final candle was considered and **rejected**: it injects an unrealized price into a
realized-PnL metric and would systematically flatter trend-following, which by construction tends to
be holding a winner when a series truncates. The current design is the conservative one.

**Version boundary.** The first 27 combinations ran on `keel 0.6.1`, the rest on `0.7.0` after
#247's fee fix was deployed mid-run. Because #247 is a costing change with `n_trades` and
`win_rate` unchanged, and because this script passes `fee_pct` and `slippage_pct` explicitly, the
results should be version-independent — and were checked rather than assumed. BTC-USD reproduces to
six decimal places on all three fee levels across the boundary. The banked rows were kept.

**Four failed runs preceded the successful one, and the cause was the harness, not the machine.**
The script was piped into `tail`, which buffers until EOF; when the invoking shell was reaped the
parent blocked on a closed pipe at 0.07s of CPU with no workers, and an empty output file was twice
misread as "still running". Fixed by redirecting to a file. Separately, `pkill -f intersection.py`
reaps parents but not `multiprocessing` children, whose cmdline differs — 40 orphaned workers were
left burning CPU across the session and had to be reaped by PPID. Both are recorded because both
produced confident status reports that were false.

**Job ordering was changed mid-run, and it cannot affect the result.** `rsi_meanrev` at defaults is
by far the slowest cell: `backtest()` calls `rule.detect()` only while flat, so the rule that almost
never fires pays full support-level detection on nearly all 44k bars. Left in declaration order it
starved the two arms that answer the question. Arm B and `pullback` were promoted ahead of it. Every
declared job still ran, and a backtest is independent of dispatch order.

## 8. What this leaves open

The one live lead is `rsi_meanrev`, and it is a sharp, cheap question:

**Does its gross edge survive being made to fire more often?**

Median gross PF 1.1631 at defaults, on median n=38. The 108-cell diagnostic in #248 already widened
`oversold` to 25–35 and `support_proximity_pct` to 0.02–0.05 and found no net-viable cell at
taker — but it never asked whether widening **preserved the gross edge** or simply bought trades by
accepting worse setups. Those are different findings with different consequences, and the data to
separate them was never computed.

If selectivity is what creates the edge, the rule is unpromotable by construction and should be said
so. If the edge survives to n≥100, it is the only route by which any rule the codebase ships reaches
its own promotion floor honestly.

Nothing else here justifies engineering investment. In particular, a limit-order queue simulator has
no target: its measured prize was one asset (ZEC) with ~8bp of headroom below the maker rate, and §6
removes that asset.
