# `turtle_breakout` clears `min_trades=100` on hourly bars — and loses on all 19 of them

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

**Date:** 2026-08-11
**Issue:** none — nobody asked for this. It fell out of the 2026-08-11 scout run
(`~/keel/proposals/2026-08-11-shortlist.json`), which carried DOGE forward on a daily
`profit_factor` of **1.489 at n=12**, and out of the obvious next question: what is n=12 worth?
§9 proposes the two issues this *should* generate.
**Change:** **none.** No code, no config, no parameter, no rule status, no version bump. The
`config.live-sandbox.yaml` standing exception this document falsifies the premise of is
deliberately left standing and untouched — see §8 for why editing it here would have been the
wrong move.
**Harness:** the shipped `keel/strategy/backtest.py::backtest`, driven over
`Repository.get_candles(product_id, Granularity.ONE_HOUR)` against the paper-forward candle cache
(`~/keel/keel.db`, read-only). No new instrument. That is the point: the negative result is
produced by the same code path that produces every positive result this project has published, so
it cannot be dismissed as a harness difference.
**Script:** none, deliberately — a driver would have added a second thing to trust. The whole run
is four lines and they are in §2, reproducible against the cache as it stood today.
**Ledger:** one row, `hourly-turtle-granularity-2026-08-11`, session
`hourly-turtle-granularity-2026-08-11`, `decision: diagnostic_only` — this changed nothing, and
under spec §4.4 it must not count toward `N`.

**Verdict: the standing exception's premise is false and its conclusion is untouched. The
promotion gate is not unreachable — on hourly bars every asset clears `min_trades=100` in under
two years and the cached history already contains five. The rules fail it anyway, on edge: PF
0.270–1.042 at the fee the CLI actually charges, and 0/19 above 1.0 at the fee the config says
applies. The sandbox is not waiting for evidence. It has evidence, and the evidence is negative.**

| question | answer |
|---|---|
| is `min_trades=100` reachable for `turtle_breakout`? | **yes** — **195–274** hourly trades against **4–13** daily, over the *same calendar window* |
| how far away is the gate really? | **~1.9–2.1 years**, not the config's **31–84 years** — and 5.07 years are already cached |
| does the strategy pass once the sample exists? | **no** — PF **0.270–1.042**; 18 of 19 below 1.0 at the CLI's own default fee |
| does the one apparent winner survive a realistic fee? | **no** — ZEC **1.042 → 0.736**. **0 of 19** clear PF 1.0 at taker |
| is the CLI's fee the fee the sim's own config specifies? | **no** — `_run_backtest` prices **maker 0.6%** on a fill `config.paperforward.yaml` calls **taker 1.2%** (§5) |
| would adding assets fix it? | **no** — negative on **19 of 19**, spanning majors, mid-caps and a gold token |
| was DOGE's daily PF 1.489 real? | **no** — one trade is **63% of gross profit**; ex-outlier daily **0.55**, hourly **0.558** on n=261 |
| does this demote the five live rules? | **not on its own** (§8) — but the config's stated *reason* for keeping them cannot stand as written |
| is this validated? | **no** (§7) — daily-tuned params on an hourly clock is arguably a different strategy, and that objection is the strongest thing anyone can say against this document |

---

## 1. The claim under test

`turtle_breakout` is not one of keel's rules; it is substantially keel's strategy. Five of the six
rules in `keel-live.db` run it (BTC, ETH, PAXG, ADA, XLM); the sixth is a BTC `dca`. All five were
seeded straight to `status=live` with `promoted_at IS NULL`, through the bypass `keel rules seed`
warns about, and `config.live-sandbox.yaml:62-94` records that as a reviewed standing exception
rather than leaving it silent. Its argument has one load-bearing claim:

> WHY KEEP THEM. The promotion floor is min_trades=100 PER RULE. **This strategy cannot reach it.**
> […] BTC 13 trades = 2.59/yr -> 100 trades in ~39 years […] ADA 6 trades = 1.19/yr -> ~84 years
> **Waiting for the gate is not a slower path to the same place; it is no path.**

That is a strong claim and it is the entire justification. If the gate is unreachable then holding
the rules to it is a category error, the sandbox is the only instrument that can ever produce
evidence about them, and demotion destroys the experiment for nothing. Every subsequent sentence
in that comment block — what bounds the risk, what this is not, when to revisit — is downstream of
it.

The claim was measured on **daily bars**. It was never stated as a claim about daily bars. This
document is what happens when you re-run the identical measurement on a different bar clock.

## 2. What was run

Same rule, same constructor defaults (`entry_lookback=40`, `exit_lookback=20`, `adx_period=14`,
`adx_threshold=25`, `atr_period=20`, `atr_stop_mult=2`, `target_rr=6`, all optional filters off),
same cached candles, same shipped backtester. One thing changed: `ONE_DAY` → `ONE_HOUR`.

```python
repo = Repository(sqlite3.connect("keel.db"))
rule = TurtleBreakout(product_id="BTC-USD")                     # stock params, nothing overridden
candles = repo.get_candles("BTC-USD", Granularity.ONE_HOUR)     # 44,393 bars = 5.07 years
backtest(rule, candles)                                         # fee_pct defaults to 0.006
backtest(rule, candles, fee_pct=Decimal("0.012"))               # the rate config.paperforward.yaml states
```

Nineteen assets: the five live incumbents, plus the fourteen survivors of the scout process
(ZEC, FET, CRV, ALGO, SOL, AAVE, DOGE, AVAX, NEAR, LINK, LTC, ICP, DOT, UNI). Seventeen of the
nineteen carry 42,015–44,393 hourly bars, i.e. 4.79–5.07 years. NEAR has 34,437 (3.93 years) and
PAXG-USD has 11,025 (1.26 years) — PAXG is quoted natively in USD only since 2025-05-08 and is on
the allowlist via a documented `screen_exceptions` waiver. Those two are called out wherever they
matter rather than averaged into a claim.

**The daily and hourly arms cover the same calendar span.** BTC: 1,850 daily bars and 44,393
hourly bars are both 5.07 years. So nothing below is explained by one arm having more history.

⚠️ **Read every number in this document against §7 first.** `turtle_breakout`'s own source
comments say `entry_lookback: int = 40,  # Donchian-high entry (days)` and `atr_period: int = 20,
# 20 days = Turtle's "N"`. On hourly bars those become 40 hours and 20 hours. The rule hard-codes
`self.granularity = Granularity.ONE_DAY`, and `backtest` keys the series by *that* attribute
(`_rule_trading_tf`), so the rule is handed hourly bars **believing they are days** and never
learns otherwise. A fair reading of this entire document is therefore narrower than its title:
*daily-tuned turtle does not transfer to an hourly clock.* §7 argues that objection properly,
including what survives it and what does not. It is the strongest thing anyone can say against
this finding and it belongs at the front, not in a caveats list.

## 3. Result 1 — the floor is reachable, and it is not close

| asset | daily trades | daily/yr | hourly trades | hourly/yr | ratio | config's "years to 100" | actual, hourly |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | 13 | 2.57 | **274** | 54.1 | 21.1× | ~39 yr | **1.85 yr** |
| ETH | 13 | 2.57 | **266** | 52.5 | 20.5× | ~39 yr | **1.90 yr** |
| XLM | 8 | 1.59 | **248** | 49.3 | 31.0× | ~63 yr | **2.03 yr** |
| ADA | 6 | 1.19 | **238** | 47.3 | 39.7× | ~84 yr | **2.11 yr** |
| PAXG | 4 | 3.18 | **61** | 48.5 | 15.2× | ~31 yr | **2.06 yr** |

Across all 19 assets the hourly count runs **195–274** (NEAR's 195 is the low, on its shorter
3.93-year series). **Eighteen of nineteen clear `min_trades=100` outright.** The one that does not
is PAXG at n=61, and that is a history-length fact, not a trade-rate fact: at its own realized rate
of 0.0055 trades per hourly bar it crosses 100 after ~18,073 bars, i.e. **294 more days of native-
USD listing**. PAXG is the only asset on this list whose gate is genuinely in the future, and its
distance is ten months.

Two things in that table deserve to be said plainly.

**First, the "~39 years" and "~84 years" figures were never properties of the strategy.** They are
properties of the daily clock. Notice the shape: the *daily* trade rate spans 1.19–3.18/yr, a 2.7×
spread that reads like a real difference between assets, and the config's per-asset horizons
(31/39/63/84 years) inherit that spread and look like per-asset facts. The *hourly* rate is
47.3–54.1/yr on every one of the five — a 1.14× spread. The trade rate is set by how often you
look, not by the asset. Once you look 24× as often, the five assets are indistinguishable.

**Second, and worse for the standing exception: there is no waiting at all.** "~2 years to the
gate" is the forward-looking number. The backward-looking one is that the cache already holds
5.07 years of hourly bars, so the sample the gate demands **existed before the argument that it
could never exist was written.** The exception's premise was not merely optimistic about the
future; it was falsifiable against data already on disk on 2026-08-08.

## 4. Result 2 — and the strategy fails on edge, not on sample size

This is the part that matters, and it is why §3 is not good news.

All 19 assets, `ONE_HOUR`, at the backtest's **default** `fee_pct = Decimal("0.006")` — the fee
`keel rules backtest` actually charges, for reasons §5 takes apart:

| asset | n | win% | PF |
|---|---:|---:|---:|
| ZEC | 250 | 23.2 | **1.042** |
| FET | 259 | 21.2 | 0.858 |
| CRV | 246 | 20.3 | 0.690 |
| ALGO | 244 | 22.1 | 0.649 |
| SOL | 267 | 26.6 | 0.585 |
| AAVE | 233 | 24.0 | 0.585 |
| XLM | 248 | 20.2 | 0.584 |
| DOGE | 261 | 17.2 | 0.558 |
| ADA | 238 | 18.1 | 0.534 |
| AVAX | 258 | 21.3 | 0.531 |
| NEAR | 195 | 22.6 | 0.491 |
| ETH | 266 | 22.2 | 0.479 |
| LINK | 240 | 20.4 | 0.420 |
| LTC | 230 | 14.8 | 0.394 |
| ICP | 200 | 17.5 | 0.390 |
| PAXG | 61 | 21.3 | 0.316 |
| BTC | 274 | 19.0 | **0.318** |
| DOT | 265 | 18.5 | 0.312 |
| UNI | 231 | 18.6 | 0.270 |

**Eighteen of nineteen below 1.0.** The distribution has no tail on the right: the field runs from
0.270 to 1.042 with nothing above. The five live incumbents sit at 0.584 (XLM) / 0.534 (ADA) /
0.479 (ETH) / 0.318 (BTC) / 0.316 (PAXG) — **ranks 7, 9, 12, 16 and 17 of 19.** None reaches the
top quartile of a field of losers, and BTC and PAXG are in the bottom four.

Win rates cluster at **15–27%**. That is not by itself an indictment: `turtle_breakout`'s own
docstring says as much (it "would fail the global 55%-win floor despite a positive expectancy",
KB §25.5), because a trend follower is supposed to lose small four times out of five and win large
once. The indictment is that at these win rates the rare large winners are not large enough to pay
for the frequent small losers *plus* the round trip. The structure is intact; the compensation is
not.

Note what changed and what did not between the daily and hourly arms. The daily numbers on the
allowlist looked *positive* — `config.live-sandbox.yaml:35-39` records BTC pf 1.61, ETH 1.21, XLM
11.44, ADA 5.52, PAXG 3.25. Those are the same rule on the same assets over the same window. XLM's
11.44 came from 8 trades and ADA's 5.52 from 6; at 248 and 238 trades the same rule prints 0.584
and 0.534. Nothing about the strategy changed between those two lines. What changed is that the
second one has enough trades to mean something, and it is the one that is bad.

## 5. Result 3 — the single apparent winner is a fee artifact, and the fee is a shipped defect

ZEC's 1.042 is the whole of the good news in §4. It does not survive contact with the correct fee,
and the reason it does not is a defect in code that ships.

```python
# keel/commands/rules.py:99-100
candles = repo.get_candles(product_id, granularity)
return backtest_mod.backtest(rule, candles)          # no fee_pct, no finer_candles
```

`backtest`'s signature is `fee_pct: Decimal = Decimal("0.006")`. So `keel rules backtest` and
`keel rules promote` both price fills at **0.6%**. Meanwhile `config.paperforward.yaml:111-117`
says, in its own comment, exactly what that number should be:

> Coinbase Advanced trading fees applied to volume beyond a tier's free allowance […]
> **`taker_pct` is the sim's default -- it fills market-style at next-bar open**; `maker_pct` is
> exposed for a caller that wants to model limit-order fills instead.
> ```yaml
> fees:
>   taker_pct: 0.012
>   maker_pct: 0.006
> ```

The config states the sim fills market-style at next-bar open and that taker is therefore the
default. The simulator does fill that way. The code takes **maker**. The two halves of the project
disagree in writing, and the config is the half that is right about the execution model.

This is not a rounding question. Fees are charged on both legs, so round-trip friction
(`2 × fee_pct + 2 × slippage_pct`, slippage `0.0005` unchanged in both arms) goes **1.30% → 2.50%
of notional, a 1.92× increase**. At `backtest`'s fixed 1-unit notional and ~250 round trips, the
choice of fee alone moves cumulative cost by **3.0× the position size**. Re-running with
`fee_pct=Decimal("0.012")`:

| asset | n | PF @ 0.6% | PF @ 1.2% | Δ |
|---|---:|---:|---:|---:|
| ZEC | 250 | **1.042** | **0.736** | −0.306 |
| FET | 259 | 0.858 | 0.623 | −0.235 |
| CRV | 246 | 0.690 | 0.476 | −0.214 |
| ALGO | 244 | 0.649 | 0.461 | −0.188 |
| DOGE | 261 | 0.558 | 0.375 | −0.183 |
| BTC | 274 | 0.318 | 0.148 | −0.170 |

**Zero of nineteen assets clear PF 1.0 at the realistic fee.** The only apparent winner in the
field loses 29% of its profit factor and lands at 0.736.

The size of those deltas is itself a finding, independent of which fee is correct. A
1.2-percentage-point change in round-trip cost should be a rounding error for a strategy with real
edge. Here it moves ZEC by 0.31 and BTC by 0.17 — meaning the gross move captured per trade is of
the same order as the transaction cost. **These are cost-dominated results.** For a strategy in
that regime the fee is not a modelling detail to be argued about later; it is the dominant term,
and getting it wrong by 2× inverts conclusions.

**Blast radius.** This is not confined to `rules backtest`. `keel/cli.py:1691` pins
`_SIM_FEE_PCT = Decimal("0.006")` with the comment *"Match `strategy/backtest.backtest`'s /
`sim/portfolio_sim.run`'s own defaults so the edge table, the account pass, and the benchmarks all
price fills identically"* — consistency achieved, at the wrong number. `portfolio_sim.run` defaults
to `0.006`; `strategy/paper.py::_DEFAULT_FEE_PCT` is `0.006`, so the **paper-forward's own realized
record** — the sandbox's evidence engine — is also priced at half. And `promotion.can_promote`
reads `expectancy`, `win_rate` and realized R:R off exactly these stats, so **the promotion gate
has always been evaluated at half the realistic cost too.** Every profit factor and every
expectancy this project has ever printed is optimistic by that margin.

**Not fixed here, on purpose.** Changing a fee default changes the gate for every rule at once and
changes the numbers in prior documents. That is a code change with its own before/after
obligation, and it does not belong in a research write-up that would then be arguing from its own
patch. §9 raises it as an issue.

## 6. Result 4 — DOGE, two independent methods, one number

The strongest evidence that §4 is measuring something real rather than manufacturing an artifact
is an accident, and it concerns the one asset the scout process currently recommends.

DOGE on **daily** bars scores **PF 1.489 on n=12** — the best daily result in the 19-asset field,
reproduced from freshly fetched candles on 2026-08-11 (1,819 `ONE_DAY` bars), and the sole
performance basis for its shortlisting. Decompose it: **one winning trade is 63% of gross profit.**
Remove that trade and the remaining eleven give

```
PF_ex-outlier = 1.489 × (1 − 0.63) = 0.551
```

DOGE's **hourly** PF, over **261 trades**, at the same 0.6% fee, is **0.558**.

Two methods that share no data beyond the underlying price series — delete one observation from a
12-trade daily sample, versus measure 261 trades on an hourly clock — land **0.007 apart**. Neither
was tuned to the other; the hourly arm was run before the daily decomposition was done.

The natural reading is the correct one: DOGE's daily 1.489 is one lucky trade, ~0.55 is what the
turtle actually does on DOGE, and the shortlist's headline number is noise. The scout run's own
entry already says *"DO NOT ATTEST ON THE EDGE EVIDENCE"* on other grounds. This gives that
caution a quantitative basis it did not have: the edge evidence is not weak, it is **absent**, and
the figure that made DOGE look like the field's best candidate is the single best illustration in
this document of what n=12 buys you.

## 7. The strongest objection: these are daily params on an hourly clock

**This objection is substantially correct and it limits the finding.**

`turtle_breakout`'s parameters were chosen for daily bars, and the source says so in the
constructor signature itself:

```python
entry_lookback: int = 40,   # Donchian-high entry (days); walk-forward OOS default (was 20)
exit_lookback: int = 20,    # Donchian-low asymmetric exit (days); half the entry (was 10)
adx_period: int = 14,       # 14 days -- classic ADX
atr_period: int = 20,       # 20 days = Turtle's "N"
```

On hourly bars the 40-day Donchian channel becomes a **40-hour** channel, ADX confirms over 14
hours, and Turtle's "N" — a 20-day volatility unit with 50 years of literature behind it — becomes
20 hours and is no longer N. `_REPLAY_TAIL = 400` "completed daily bars (~16 months)" becomes 400
hours. The `entry_lookback` 20 → 40 walk-forward that produced these defaults was run on daily
bars and says nothing about an hourly clock.

The mechanism is worth naming because it is not a modelling choice, it is silent. `TurtleBreakout`
hard-codes `self.granularity = Granularity.ONE_DAY`. `_resolve_granularity` honours
`--granularity ONE_HOUR` and fetches hourly candles, but `backtest` then keys the per-bar window by
`_rule_trading_tf(rule)` — the rule's *declared* granularity — so the hourly series arrives under
the `ONE_DAY` key. **The rule cannot tell.** There is no error, no warning, and no field in the
output recording which clock was used.

So the honest statement of what §4 shows is:

> A 40-bar Donchian breakout with a 20-bar ADX/ATR stack, tuned on daily bars, is negative on 19
> crypto assets when run on hourly bars.

It is **not**:

> Trend following does not work on crypto.

**What survives the objection.** Three things, and they are the three the document is actually
for.

1. **§3 is untouched by it.** The trade-count result is arithmetic about the bar clock, not about
   parameter quality. Whether or not a 40-hour channel is a sensible strategy, running it produces
   195–274 trades where the daily version produces 4–13. "The floor is unreachable" is refuted by
   any configuration that reaches it, including a badly tuned one.
2. **§5 is untouched by it.** The fee default is wrong at every granularity and for every rule. It
   would be wrong if this experiment had never been run.
3. **§6 is untouched by it.** The DOGE decomposition is a pure daily-bar result: one trade is 63%
   of a 12-trade sample's gross profit. The hourly figure is corroboration, not the argument.

**What does not survive.** Any claim that `turtle_breakout` as deployed — on daily bars, with
these parameters, on these five assets — has been shown to be unprofitable. It has not been. What
has been shown is that its daily record rests on 4–13 trades per asset, that the same rule with
the same parameters on a measurable sample is decisively negative, and that nobody has produced
evidence which distinguishes "the daily version has edge that the hourly version lacks" from "the
daily sample is too small to show the absence of edge". **That distinction is testable and has not
been tested** (§9.4).

## 8. What this does to the standing exception

The exception's structure is: *premise* (the gate is unreachable) → *conclusion* (keep the rules
live, since demotion ends the only experiment that can produce evidence).

**The premise is false as written.** Not weakened, not qualified — false. The gate is reachable in
~2 years forward and is already clearable against 5 years of cached history. "Waiting for the gate
is not a slower path to the same place; it is no path" describes the daily clock and nothing more
general, and it was written as a claim about the strategy.

**The conclusion may still hold, and this document does not overturn it.** Three reasons, stated
so the next reader does not have to reconstruct them:

- **The bounds are unchanged.** What limits damage is `max_exposure_usd 200` total,
  `max_per_order_usd 100`, the 18 `guards.py` rails, rail 1's allowlist and rail 14's subscription
  allowance — never the promotion gate, which never ran. §4 does not touch any of them. The
  sandbox's exposure to being wrong is still a few dollars.
- **The live rules trade the daily clock.** They take ~2.6 trades a year each. Nothing here
  measures the thing they actually do at a sample size that could condemn it (§7).
- **Demoting on a screening result would be the same error in the other direction.** The reason
  this evidence is worth acting on is that it has 195–274 trades behind it. Acting on it against a
  configuration it did not test would discard that advantage.

**What must change is the comment.** As written, `config.live-sandbox.yaml:70-79` tells a future
reader that no evidence about these rules can exist, so none should be sought. That is now known to
be untrue, and leaving it would let a false statement do load-bearing work in a live config — the
precise failure that block was rewritten on 2026-08-07 to fix, when an earlier version credited a
walk-forward that had never covered ADA or XLM. The honest version of the argument is available and
is *stronger*, because it stops resting on an unreachability claim that can be checked:

> The rules stay because the caps bound the damage to a few dollars and because the sandbox is the
> only source of *live* evidence — not because evidence is unobtainable. Hourly backtests over the
> same five years clear `min_trades=100` on 18 of 19 assets and are negative on all of them; that is a
> screening result on a clock these rules do not trade, and it is a standing reason to treat this
> sandbox as an experiment with an expected negative result rather than a strategy in production.

Its own **REVISIT IF** clause already fires on this: *"this sandbox is treated as evidence for
anything beyond itself."* The reachability of the gate is exactly the kind of fact that clause
exists to catch. **This document does not edit that file.** A live config with a documented,
reviewed exception should be changed by the person who owns the review, with the evidence in hand,
not as a side effect of a research PR — §9 raises it as an issue instead.

## 9. What this changes, and what it does not

**1. Nothing is demoted, retuned, or reconfigured by this document.** See §8.

**2. Recommend an issue: `_run_backtest` prices fills at the wrong fee.** Three acceptable fixes,
in preference order: (a) `_run_backtest` reads `fees.taker_pct` from the loaded config and passes
it; (b) `backtest`'s default becomes the taker rate, matching the fill model it already implements;
(c) at minimum, **the printed output states which fee it used** — `keel rules backtest`'s output
line today reports `n_trades`, `win_rate`, `expectancy`, `profit_factor` and `max_drawdown` and
gives the reader no way to know any of them are maker-priced. (c) is not optional even if (a) or
(b) ships, because prior numbers in `docs/experiments/` and in `config.live-sandbox.yaml`'s own
table were printed without it. The same issue should cover `_SIM_FEE_PCT`, `portfolio_sim.run`, and
`strategy/paper.py::_DEFAULT_FEE_PCT`, which are all `0.006` by deliberate agreement with the
wrong default (§5). **Deliberately not fixed here.**

**3. Asset selection is not the lever, and the scout process should be told so.** This is the
finding with the largest effect on where effort goes. The scout ranks candidates on daily
`profit_factor` at n=4–17 and shortlists the top of that distribution; §6 shows what that
distribution is made of. At a measurable sample the rule is negative on **19 of 19** assets —
majors, mid-caps, an L1 spread, a DeFi spread, a privacy coin, a meme coin and a gold token.
There is no asset in this field whose addition would make the strategy positive, and no reason to
expect the twentieth to differ. **Adding assets cannot fix a strategy that is negative across
every asset it has been measured on.** The §73.3 argument for expansion — more assets buy
statistical power for the same zero trials — is untouched and still correct; what is refuted is
expansion as a *performance* fix. Effort currently spent finding the next DOGE is better spent on
whether the rule has an edge to scale.

**4. Recommend an issue: run the test this document could not.** The §7 objection is resolvable,
and cheaply. Re-run the hourly arm with parameters *scaled to the hourly clock* (a 40-day channel
is ~960 hourly bars, not 40) on the same 19 assets. Two possible outcomes and both are worth
having: if scaled-hourly turtle is also negative, §7's escape hatch closes and the finding
generalises to the strategy; if it is positive, the finding narrows to "these parameters do not
transfer" and keel learns that its clock, not its rule, is the binding constraint. Until then §7
stands and this remains a screening result.

**5. Nothing here is a validated conclusion, and it must not be cited as one.** No walk-forward, no
CSCV/PBO, no deflated Sharpe, no out-of-sample split, one parameter set, one rule, one granularity
change. It is `diagnostic_only` in the ledger for that reason and does not count toward `N`. What
it is: 19 independent negative screens at n≈250 each, which is a great deal more than the 4–13 per
asset that everything else in this project's decision record rests on.

## Caveats

- **§7 is the caveat.** Daily-tuned parameters on an hourly clock is arguably a different strategy.
  It is stated in §2 and argued in §7 rather than buried here, because a reader who reaches this
  list without having met it has been misled by the layout.
- **In-sample, one window, no out-of-sample split.** The same 5.07 years, on assets the scout
  selected partly by looking at that period.
- **19 crypto assets over one broadly correlated window.** These are not 19 independent
  experiments. A single regime — the 2021–2026 crypto cycle — is common to all of them, and a
  trend follower's fate is largely a fact about the regime. 19 of 19 negative is 19 correlated
  observations, not 19 independent ones.
- **PAXG is a partial cell.** 11,025 hourly bars against 42,015–44,393 for the rest, n=61 rather
  than ~250, and it is the one asset that does not clear the floor. Its PF of 0.316 has the widest
  error bars in the table and should not be read as comparable precision.
- **NEAR has 34,437 bars (3.93 years)** against ~5 for the rest — a smaller version of the same.
- **No `finer_candles` in either arm.** `_run_backtest` does not pass them and neither did this
  run, so intrabar ambiguity falls back to the module's conservative resolution: entry-vs-stop
  ambiguity invalidates the trade, stop-vs-target resolves to the stop. Daily bars span far more
  range than hourly ones, so that fallback fires more often, and both of its branches flatter the
  daily arm (invalidated trades are disproportionately losers). **The direction of that bias
  favours the daily numbers this document is arguing against, which strengthens the finding — but
  it was not measured, and it is argued here, not shown.** `sim/report.py::edge_table` does pass
  hourly bars as `finer_candles` for daily rules; `rules backtest` does not, which is a second,
  smaller inconsistency in the same family as §5.
- **Trade counts are not independent observations either.** `backtest` holds one position at a
  time, so 274 hourly trades are sequential and overlapping in regime. n≈250 is a real improvement
  over n=13; it is not 250 independent draws.
- **The 1.2% taker rate is the published `<$1k-30d-volume` tier**, which is what this account is.
  It ignores rail 14's monthly free-volume allowance, under which some fills cost nothing. The
  0.6% arm is not a model of that allowance — it is maker pricing on a taker fill, and no tier
  makes it correct.
- **The ledger row carries no P&L series** (`series_missing: true`), so this trial cannot enter a
  CSCV matrix. It is a screen, and the ledger refuses to let it pretend otherwise.
