[← Knowledge Base index](../README.md)

## Source 58 — "The Encyclopedia of Trading Strategies" (Jeffrey Owen Katz, Ph.D. & Donna L. McCormick, McGraw-Hill, 2000, 386pp)

**Type:** systematic **back-testing study**. This is *not* a technique catalog — it is a
**controlled experimental report**. Katz & McCormick take the standard menu of entry and exit
models, code each one explicitly in C++, and run every one of them over the *same* 36-market
futures portfolio, the *same* in-sample window (Aug 1985 – Dec 1994), the *same* out-of-sample
window (Jan 1995 – Feb 1999) and the *same* standardized exit — then report annualized
return, risk-to-reward ratio, % wins and statistical significance for each.

**Why this matters to us more than another technique book:** the KB is saturated on *what the
techniques are* (§23/§24/§27/§37/§38 are near-duplicates of each other; §54 is the definitive
reference). What the KB has almost none of is **independent evidence about which of them
actually survive testing.** This book is ~80% negative results, and negative results are
directly actionable for `keel`: they tell us what *not* to spend a build cycle on, and — in two
cases — that something we have **already shipped** did not survive the authors' out-of-sample test.

**Its central bias, stated plainly:** the authors test **futures** with **shorts** and
**leverage** as a matter of course. Every short result below is reinterpreted as an
exit / don't-buy filter, or discarded (see §58.21). Their "dollar volatility equalization"
across markets is our **equal-risk-by-ATR allocation** (§54.22) and is not re-extracted.

**Coverage note.** Chapters were covered at very uneven density, deliberately:

| Ch | Title | pp | Density | Why |
|----|-------|----|---------|-----|
| Part II intro | Entry orders, standardized exit, test platform | 71–82 | **dense** | the harness design is the most transferable thing in the book |
| 5 | **Breakout Models** | 83–108 | **dense** | our shipped rule *is* a breakout; 12 controlled tests on it |
| 6 | **Moving Average Models** | 109–132 | **dense** | trend-following vs counter-trend, head to head |
| 7 | **Oscillator-Based Entries** | 133–152 | **dense** | direct evidence on the RSI mean-reversion we already refuted |
| 13 | **The Standard Exit Strategy** | 293–308 | **dense** | isolates exits from entries — the stop-width defect |
| 14 | **Improvements on the Standard Exit** | 309–334 | **dense** | the single most useful chapter for defect (b) |
| Part III intro | Exit taxonomy, random-entry method | 281–292 | **dense** | the random-entry test bench |
| 4 | Statistics | 51–70 | medium | dedupes hard against §54.10 |
| 3 | Optimizers & Optimization | 29–50 | medium | curve-fitting; dedupes against §54.10 |
| 8 | Seasonality | 153–178 | skim | low-weight for us (§14.3); results summarized only |
| 9 | Lunar & Solar Rhythms | 179–202 | skim | no-oracle (§6.4); result recorded, not extracted |
| 10 | Cycle-Based Entries | 203–226 | skim | MESA/wavelet filter banks — not reproducible in `keel` |
| 11 | Neural Networks | 227–256 | skim | excluded with §54's black-box AI exclusion |
| 12 | Genetic Algorithms | 257–280 | skim | same; the GA optimizer is also excluded per §54.22 |
| 15 | Adding AI to Exits | 335–356 | skim | same |
| 1, 2 | Data; Simulators | 3–28 | skim | 1990s data vendors & TradeStation/C-Trader platform minutiae |
| — | Conclusion & Appendix | 357+ | read | the authors' own summary of what survived |

Sections use the KB convention **§58.x** = *source 58, section x*.

---

## §58.0 ⭐ The controlled-experiment harness: hold the exit fixed, vary ONE thing
**Module: `strategy/backtest.py`, `strategy/promotion.py`**

This is the book's methodological spine and it is a **concrete upgrade to `keel simulate` that
§54.10 does not give us.** Kaufman teaches *how to validate a finished system* (walk-forward,
OOS firewall, robustness plateau). Katz & McCormick teach *how to attribute a result to a
component*:

> *"One such trick is to have a set of standard entry and exit strategies that remain **fixed**
> as the particular entry, exit, or other element under study is varied. For example, when
> studying entry models, a standardized exit strategy will be repeatedly employed, without
> change, while a variety of entry models are tested and tweaked. Likewise, for the study of
> exits, a standardized entry technique will be employed."*

**The standardized exit** (used unchanged in every entry test in Part II):

```
money-management stop  = entry − mmstp × ATR(50)      mmstp = 1     (1 "volatility unit")
profit target (limit)  = entry + ptlim × ATR(50)      ptlim = 4
time exit (market)     = exit at close after maxhold bars,  maxhold = 10 days
whichever comes first; ALL exit orders are close-only
```

Three details are load-bearing for us:

1. **Stops and targets are expressed in volatility units (ATR multiples), never currency.**
   *"a $1,000 stop would be considered tight on today's S&P 500 (yet loose on wheat)… Volatility
   units are like standard deviations, providing a uniform scale of measurement."* Already our
   practice (§27.1/§54.3) — but note they use **ATR(50)**, a much longer lookback than our
   ATR(20), which makes the stop distance far more stable across regimes. **Testable:**
   sweep the ATR period used for the stop separately from the one used for sizing.
2. **All exit orders are restricted to the close** — explicitly to make the simulation
   *determinate*: *"Simulations become indeterminate and results untrustworthy when multiple
   intrabar orders are issued: The course of prices throughout the period represented by the bar,
   and hence the sequence of order executions, is unknown."* This is the same intrabar
   order-of-events problem as §20.2/§1.7, and it independently justifies **`stop_trigger=close`
   (§34.1)** on *methodological* grounds, not just anti-whipsaw grounds. Our sim can only be
   trusted at bar granularity if there is at most one intrabar order.
3. **The time exit (`maxhold`) is part of their BASELINE**, not an option. Every result in the
   book is produced by a system that force-exits after 10 bars. **This is a second, independent
   endorsement of §57.2's `max_hold`** — which the KB currently lists as an untested
   `exit_method` candidate with no equivalent in the codebase. Katz & McCormick treat a time
   stop as a mandatory component of a complete mechanical system.

**Testability:** directly. `keel simulate` should gain a **"component isolation" mode**: pin a
reference exit, sweep entries; then pin a reference entry, sweep exits. Today the harness
sweeps whole rules, which confounds entry quality with exit quality — the exact confound the
authors built their book to avoid.

**Reconciles with §54.10:** complementary, not duplicative. §54.10 = *is this system real?*
§58.0 = *which part of it is doing the work?*

---

## §58.1 ⭐⭐ Enter the breakout on a LIMIT, not at market — the book's single strongest finding
**Module: `execution/executor.py`, `strategy/rules/turtle_breakout`**

Every breakout model was tested three ways — entry at next open (market), entry on a **limit**,
entry on a **stop** — with everything else identical. From the chapter's own summary:

> *"Both in- and out-of-sample, and across all models, the **limit order provided the greatest
> edge**; the stop and market-at-open orders did poorly."*

And from *What Have We Learned?*:

> *"If possible, use a limit order to enter the market. The markets are noisy and usually give
> the patient trader an opportunity to enter at a better price; **this is the single most
> important thing one can do to improve a system's profitability**."*

**The mechanical rule** (their `limprice`, deliberately unsophisticated):

```
On the bar that closes beyond the channel/band, compute
    limprice = 0.5 × (High_breakout_bar + Low_breakout_bar)      # midpoint of the breakout bar
Post a BUY LIMIT at limprice, good for the next bar only.
If unfilled, cancel (no chase).
```

**Tested result** (Table 5-3, annualized risk-to-reward ratio / return-on-account):

| Model | Entry at open | Entry on **limit** | Entry on stop |
|---|---|---|---|
| Close-only channel breakout (IS) | ARRR −0.02 / ROA −1.1% | **ARRR 0.54 / ROA 32.6%** | — |
| HHLL breakout (IS) | 0.04 / 1.2% | **0.66 / 36.3%** | 0.22 / 8.7% |
| Volatility breakout (IS) | 0.51 / 27.4% | **0.98 / 48.3%** | 0.28 / 11.6% |
| HHLL breakout (OOS) | −0.41 / −15.9% | **−0.01 / −2.1%** | −0.44 / −15.5% |

The limit entry converted a **−1.1% loser into a +32.6% winner** on the close-only channel
breakout with transaction costs held constant. The authors stress this was achieved with *"a
somewhat arbitrary, almost certainly suboptimal"* limit price, and that *"a more sophisticated
limit entry strategy could undoubtedly provide some very substantial benefits."*

**Why it works, in their words:** *"the market pulled back after most profitable breakouts,
allowing entry at more favorable prices"*, and a limit *"side-steps the flurry of orders that
often hit the market when entry stops, placed at breakout thresholds, are triggered. Entries at
such times are likely to occur at unfavorable prices."* Note the second clause is a **stop-hunt
/ liquidity-sweep argument** — the same mechanism §35.3 describes from the SMC side.

**Crypto-fit: strong, and unusually so.**
- Crypto is *more* noisy and *more* prone to breakout-level stop clusters than 1990s futures, so
  the pullback-after-breakout the limit exploits should be at least as common.
- Coinbase Advanced Trade charges **maker fees below taker fees**, so a resting limit entry cuts
  the fee *and* the slippage. The book's transaction-cost effect (§58.5) is therefore *understated*
  for us.
- Risk: an unfilled limit misses the trades that gap away and never look back — precisely the
  **fat tail** a trend-follower lives on. The authors measured this and found it small:
  *"The limit order did not seriously reduce the number of trades or cause many profitable trades
  to be missed."*

⚠️ **Reconcile with §54.15 (Kaufman), which points the other way.** Kaufman warns *"don't
naively delay — you miss the fat tail"* and prescribes entering *"after a 0.50·ATR reverse or on
the next close"*. These are **not actually in conflict** — Kaufman's 0.50·ATR-reverse entry *is*
a limit entry with an ATR-scaled offset, whereas Katz & McCormick's is a limit at the breakout
bar's midpoint. The disagreement is over the **offset**, not over whether to be passive. The
harness should sweep them as one family:
`entry_order ∈ {market, limit@bar_midpoint, limit@entry−k·ATR}` with `k ∈ {0.25, 0.5, 1.0}`
and a **one-bar validity window** (their limit is good for one bar only — matching our existing
one-candle order validity, §2.2).

**Testability: high and cheap.** It is a change to order placement only; the signal logic is
untouched. It is also the rare change that is **safe under our rails** — a limit that never
fills simply means no trade. Measure: fill rate, missed-trade cost (what the skipped trades
would have returned), and net effect on expectancy.

---

## §58.2 ⚠️⭐ NEGATIVE RESULT — the ADX trend filter did NOT survive out-of-sample
**Module: `analysis/regime.py`, `strategy/engine.py` — challenges a SHIPPED component**

This one lands on something we already built. **§25.1 gave us the ADX(14) > 25 trend gate, and
it is in the live `turtle_breakout` rule.** Katz & McCormick tested exactly that idea — a
Wilder ADX filter married to a breakout entry — as Test 12:

```
trending = ADX(18)[today] > Highest(ADX, 6)[through yesterday]   # ADX makes a new 6-bar high
Enter the volatility breakout on a limit ONLY IF trending.
```

**Tested result:**

| | In-sample | Out-of-sample |
|---|---|---|
| Volatility breakout, limit entry, **no** filter (Test 8) | ROA 48.3%, ARRR 0.98 | ROA −16.9%, ARRR −0.58 |
| Volatility breakout, limit entry, **ADX filter** (Test 12) | **ROA 68.3%, ARRR 1.09** | **ROA −20.9%, ARRR −0.60** |

In-sample the ADX filter looked superb — *"All 100 parameter combinations except one produced
positive returns in-sample; 88 returned greater than 20%… the probability that such a high
return would result from chance was less than one in two-thousand."* Out-of-sample it was
**one of the worst performances in the entire book** (−20.9%), and *worse* than the unfiltered
model it was supposed to improve.

The authors' verdicts, twice stated:

> *"The ADX trend filter had a smaller benefit in-sample and provided **no benefit
> out-of-sample**… The ADX appears to have helped more in the past than in current times."*
>
> *"**Do not rely on indicators like the ADX for trendiness determination.**"*

And in the chapter body, the underlying reason: *"The problem is that trend indicators do not
function well, or tend to lag the market enough to make them less than ideal."*

**What to do with this — carefully.** This is a single study on 1990s futures, and our own sim
has ADX in the rule that *does* have a positive edge, so it is **not** grounds for ripping the
gate out. But it is strong, independent evidence that **the ADX gate is the most likely
overfit component in our shipped rule**, and it must be tested as such:

1. **Ablation test (high priority).** Run `turtle_breakout` with the ADX gate ON vs OFF over the
   same 5-year window, and — critically — with a **walk-forward split** (§54.10). If ADX-on
   only wins in-sample, we have reproduced the authors' finding on crypto and the gate should
   be demoted to a CTS confluence *factor* rather than a hard entry *gate*.
2. **This is also a plausible contributor to open defect (a), under-deployment.** ADX > 25 is a
   binary veto that stands the agent down. ~23 trades in 5 years is the signature of a hard gate
   that rarely opens. If the ablation shows ADX-off has equal or better expectancy with 2–3×
   the trade count, that is a direct fix for the under-deployment defect.
3. **Prefer the §54 alternative for trendiness.** Kaufman's **Efficiency Ratio** (§54.1),
   **ADXR/CSI market ranking** (§54.9) and the **Strategy Selection Indicator** (§54.17) measure
   trendiness *per market, over a long window, for asset selection* — which is a different and
   (per §58.3 below) far more productive use than ADX's *per-bar, timing* use. Katz &
   McCormick's own conclusion points the same way: **filter the asset, not the bar.**

**Reconciles with:** §25.1 (the source of our ADX gate — now contested), §54.9/§54.17 (the
better-supported trendiness tooling), §54.11 (Kaufman found all 5 trend methods profitable
across 17 markets — so this is a disagreement about the *filter*, not about trend-following).

---

## §58.3 ⭐ Restricting a breakout system to LONG POSITIONS ONLY improved it
**Module: `strategy/rules/`, `CompliancePolicy` — validation, not a new rule**

Test 10 took the best volatility-breakout model (Test 8) and changed exactly one thing: it
traded **only long positions**. Nothing else moved.

| | In-sample ARRR / ROA | Out-of-sample ARRR / ROA |
|---|---|---|
| Volatility breakout, limit entry, long **and** short (Test 8) | 0.98 / 48.3% | −0.58 / −16.9% |
| Volatility breakout, limit entry, **longs only** (Test 10) | **1.17 / 53.0%** | **−0.48 / −14.6%** |

Long-only was better on **both** samples, and produced *"1,263 trades, 48% profitable — a higher
percentage than in any earlier test."* The summary: *"Restricting trades to long positions
**greatly improved** the performance of the volatility breakout in-sample, and improved it to
some extent out-of-sample. **Breakout models do better on the long side than on the short one.**"*

This is a pattern that recurs in *every* breakout test in the chapter — Tests 1, 2, 3, 4, 6, 8,
10 all report longs outperforming shorts, in-sample and usually out-of-sample.

**Why this is worth logging:** the KB's long-only constraint has so far been justified purely as
a **compliance** constraint (§28.1–28.2) — something we accept a cost for. This is the first
tested evidence in the KB that on a **breakout/trend system specifically, long-only is not a
handicap at all.** The halal constraint and the empirically better configuration coincide.

⚠️ **Do not over-read it.** Their explanation is market-structural and does *not* port: *"perhaps
due to false breakouts on the short side occasioned by the constant decay in futures prices as
contracts near expiration"* and *"commodity prices are usually more driven by crises and
shortages than by excess supply."* Crypto has neither contract decay nor supply shortages. The
honest statement is: **long-only was not a cost in the one controlled test of it that exists in
the KB** — not that long-only is superior on crypto.

**Testability:** none needed; it is a constraint we cannot relax anyway. Logged as a
**confidence anchor**, alongside §54.11's "trend-following works."

---

## §58.4 ⭐⭐ Market selection beat every model tweak — the sharpest statement of the ETH question
**Module: `analysis/regime.py`, allowlist curation — open defect (c)**

The chapter's conclusion is blunt and is the single most important sentence in it for us:

> *"**No technique, except restricting the model to the currencies, improved results enough to
> overcome transaction costs in the out-of-sample period.**"*

Test 11 removed the long-only restriction and instead restricted the model to trading **only the
six currency markets** — no re-optimization at all, reusing Test 8's parameters:

| Test 11: volatility breakout, limit entry, **currencies only** | In-sample | Out-of-sample |
|---|---|---|
| ARRR | 0.61 | **0.34** |
| ROA | 36.3% | **17.7%** |
| Avg trade | $3,977 (268 trades, 48% wins) | **$2,106 (102 trades, 43% wins)** |

The authors: *"This is the **first** test where a breakout produced clearly profitable results in
**both samples** with realistic transaction costs included in the simulation!… The gain was so
great that the model actually profited out-of-sample, which cannot be said for any of the other
combinations tested!"* Note the in-sample number got *worse* (36.3% vs 48.3%) — the restriction
sacrificed in-sample return and bought out-of-sample survival. That is the exact signature of
removing overfit rather than adding it.

And the *What Have We Learned?* rule: *"**Choose 'trendy' markets to trade** when using such
trend-following models."*

**Three concrete transfers to defect (c) — the keep-or-drop-ETH decision:**

1. **Reframe the question.** Our framing has been "should the Turtle trade ETH?" The book's
   framing is stronger: **asset selection is the highest-leverage parameter in a trend system —
   higher than the entry model, the order type, or any filter.** It deserves the same
   walk-forward rigor we apply to a rule, not a one-off judgment call.
2. **There IS cross-sample persistence in market trendiness, and it is measurable but weak.**
   *"A correlation of 0.15 between net in-sample and net out-of-sample profits implies markets
   that traded well in the optimization period tended to trade well in the verification period."*
   **r = 0.15 is a real but very weak signal.** This is a valuable calibration: it says
   ranking assets by *past* trend-system profitability is better than nothing but is a poor
   predictor on its own — which argues for ranking on a **structural** trendiness measure
   (Efficiency Ratio §54.1, run-distribution classifier §54.21, SSI §54.17) rather than on
   backtested P&L. It also warns that a 2-asset allowlist gives us essentially zero statistical
   power to make this call from P&L alone.
3. ⚠️ **Counter-evidence against cherry-picking by past performance.** *"Curiously, the currency
   markets with the greatest returns in-sample are not necessarily those with the largest returns
   out-of-sample. This implies that **it is desirable to trade a complete basket of currencies,
   without selection based on historical performance**, when using a breakout system."*
   This is a direct argument **against dropping ETH** on the basis of its weak realized P&L: the
   correct unit of selection was the *category* (currencies = structurally trendy), not the
   individual best performers within it. Applied to us: **select the asset class on structure
   (is a spot crypto majors basket structurally trendy?), then trade the whole basket** rather
   than pruning to the historical winner. This also directly counteracts defect (a) — pruning to
   one asset halves the deployment opportunities.

**Net recommendation for defect (c):** do not drop ETH on P&L. Build the **structural trendiness
ranking** (§54.1 ER + §54.17 SSI + §54.21 classifier), apply it as an *admission* test to the
allowlist, and trade the whole admitted basket. Revisit only if ETH's *structural* ER falls
below the bar — not because its realized return was low.

---

## §58.5 Transaction costs decided every test — the magnitude is startling
**Module: `strategy/backtest.py`, `strategy/promotion.py`**

Test 1 and Test 2 are the identical close-only channel breakout model. The **only** difference
is that Test 2 charges 3 ticks of slippage and $15 per round turn.

| | Annual return, in-sample |
|---|---|
| Test 1 — **zero** transaction costs | **+76%** (all look-backs 5–100 profitable; best n=80; p < 0.001) |
| Test 2 — realistic costs | **negative**, in-sample *and* out-of-sample |

> *"While this breakout model was profitable without transaction costs, it traded miserably when
> realistic costs were assumed. Even the best in-sample solution had negative returns… Why should
> relatively small commission and slippage costs so devastate profits when, without such costs,
> the average trade makes thousands of dollars? Because, for many markets, trades involve multiple
> contracts, and slippage and commissions occur on a per-contract basis."*

**Reinforces §54.10 (realistic costs) and §20.2/§20.5 (spread & slippage modeling)** — but the
*magnitude* is new information. A 76%/yr edge was annihilated by costs alone. For us this
sharpens three existing positions:

- The **anti-scalping / min-move rail (§4.1)** is not merely a compliance preference (§28.3); it
  is what keeps us on the profitable side of this cliff.
- Any harness result produced without full **Coinbase maker/taker fees + realistic spot slippage**
  is uninformative. A promotion decision made on a gross-return backtest is worthless.
- It is the strongest argument for §58.1's **limit (maker) entry**: on Coinbase the limit order
  reduces the fee tier *and* the slippage simultaneously.

---

## §58.6 Breakout channel construction and look-back — HHLL vs close-only, and n ≈ 80–95
**Module: `strategy/rules/turtle_breakout`, `analysis/levels.py`**

Two channel definitions were tested head to head, both long-and-short, both with the standard exit:

```
Close-only channel:  buy if Close_today > Highest(CLOSE, n)[through yesterday]
HHLL channel:        buy if Close_today > Highest(HIGH,  n)[through yesterday]
```

Note **both require a CLOSE beyond the level** — the HHLL model breaks out when today's *close*
exceeds the prior n-day *high*. That is not our current rule: our Donchian entry triggers on the
20-day high being touched. This is a third independent vote for **close-confirmation**
(§34.1, §35.3, §58.0.2) and a cheap, well-specified variant to sweep:
`breakout_confirm ∈ {touch_high, close_above_high, close_above_highest_close}`.

**Tested results:**

| | In-sample | Out-of-sample |
|---|---|---|
| Close-only channel, limit entry | ARRR 0.54 / ROA **32.6%** | ARRR −0.14 / ROA −10.0% |
| HHLL channel, limit entry | ARRR 0.66 / ROA **36.3%** | ARRR −0.01 / ROA **−2.1%** |

HHLL was better on both samples here, and the authors' *What Have We Learned?* singles it out:

> *"**Focus on support and resistance, fundamental verities of technical analysis that are
> unlikely to be 'traded away.'** The highest-high/lowest-low breakout **held up better in the
> tests than other models**, even though it did not always produce the greatest returns. Stay
> away from popular volatility breakouts unless they implement some special twist that enables
> them to hold up, despite wide use."*

→ **This validates the channel construction we already use** (Donchian on highs/lows, §27.1) over
the ATR-band volatility breakout that §54.3 offers as an alternative candidate. Worth noting
because §54.3 lists the volatility breakout as a candidate rule; §58.6 is evidence to
**deprioritize it** relative to the Donchian we have. The authors' reasoning — *support and
resistance are structural and don't get arbitraged away, whereas a popular formula does* — is
also a durability argument that favors our existing rule.

⚠️ **Look-back: their optimum was 80–95 days, not 20.** Across Tests 1, 3, 4, 5 and 6 the
best-performing look-back was **80, 80, 85, 85 and 95 days** respectively, optimizing `n` from 5
to 100 in steps of 5. Test 1 also reports *"long trades were more profitable than short ones"*
and *"longer look-backs were associated with higher profits."*

**Do NOT port 80 directly** — their exit is radically shorter than ours (a 10-bar `maxhold` and a
1-ATR stop, §58.0), so their entry has to be highly selective to compensate; the combination is
not our combination. But it is real evidence that **our 20-day channel may be far too fast for a
trend system**, and that the entry look-back deserves a proper sweep rather than inheritance from
the Turtle literature. **Testable:** sweep `donchian_entry_n ∈ {20, 30, 40, 55, 80, 100}` jointly
with the exit look-back, and pick the **robustness plateau** (§54.11), not the peak.

⚠️ **Counter-consideration for defect (a):** a longer entry channel means *fewer* trades, which
makes under-deployment worse. If the sweep favors longer look-backs, deployment has to be
recovered elsewhere — from removing the ADX gate (§58.2), from trading the whole basket (§58.4),
or from a faster exit that recycles capital (§58.0.3, §57.2).

---

## §58.7 The volatility-breakout specification (reference, deprioritized)
**Module: `strategy/rules/` — reinforces §54.3**

Fully specified, for completeness, since §54.3 lists this as a candidate:

```
center     = EMA(Close, malen)
band_width = bw × ATR(atrlen)
upper      = center + band_width
buy (long-only) if Close_today > upper      # entry on a limit at the breakout bar's midpoint
```

Best in-sample parameters found by genetic optimization: `bw` 2.6–3.8, `malen` 5–22,
`atrlen` 18–41. The long-only variant's best was **bw = 2.6, malen = 15, atrlen = 18**
(53.0% annualized in-sample). **All** models decayed badly out-of-sample.

→ Reinforces §54.3's formula with tested parameter ranges, but §58.6's durability finding says
**prefer the Donchian we already have.** Keep this as a sweep variant only, not a build target.

---

## §58.8 The decay finding — and why crypto may be the exception
**Module: `analysis/insights.py` (edge decay), `strategy/promotion.py`**

The book's most sobering thread is that simple breakout systems **stopped working over the test
period itself**, independent of overfitting:

> *"Excessive optimization may not be the central issue: Despite optimization, this model
> generated poor in-sample returns and undesirably few trades. Like the others, this model may
> simply have worked better in the past."*
>
> *"Additional tests revealed that **no parameter set could make this model profitable in the
> out-of-sample period!** This finding rules out excessive optimization as the cause… Seemingly,
> in recent years, there has been a change in the markets."*
>
> *"The equity curve suggests a gradual increase in market efficiency over time, i.e., these
> systems worked better in the past… When simple computerized breakout systems became the rage in
> the late 1980s, they possibly caused the markets to become more efficient."*

Broken down, average equity across all breakout models showed **three regimes**: strong gains
Aug 1985 – Jun 1988, flat/choppy Jun 1988 – Jul 1994, declining Jul 1994 – Dec 1998.

**Two things follow for `keel`:**

1. **Edge decay is a first-class phenomenon, not a tail risk.** We already have edge-decay
   detection (§6.3, §20.7) and the §57.1 consecutive-loss breaker candidate. This is the
   empirical case for both: a *published, well-specified, statistically significant* trend system
   simply stopped working, and no re-optimization could recover it. **Our promotion harness must
   assume the Turtle's edge has a half-life** and re-validate on a rolling basis, not once.
2. ⚠️ **But the mechanism argues crypto is currently on the favorable side of it.** The authors
   attribute the decay to *market efficiency rising as the technique became widely automated*.
   §54.1 (Kaufman) makes the complementary structural claim: ***developing markets have LOW
   noise, mature markets HIGH noise, and noise rises as a market matures and gains
   participants***. Spot crypto is the least mature liquid market we have access to. **The
   coherent synthesis: the Turtle's edge on crypto is plausibly real *and* plausibly temporary,
   and its expiry is tied to crypto's maturation.** That is an argument for deploying it now,
   monitoring ER (§54.1) as a decay early-warning, and never assuming a validated edge is
   permanent.

---

## §58.9 ⚠️ NEGATIVE RESULT — 48 moving-average entry models, not one profitable — *Ch 6*
**Module: `strategy/rules/`, `analysis/indicators.py`**

Chapter 6 runs **48 controlled tests**: 4 moving-average types (simple, exponential,
front-weighted triangular, VIDYA-style adaptive) × 2 trend-following models (dual crossover,
slope turn) × 3 entry orders, then the same grid for 2 counter-trend models (contrarian
crossover, MA support/resistance). Standard exit and standard portfolio throughout.

> *"**None of the trend-following moving average models was profitable on a portfolio basis.**"*

Table 6-3, annualized return-on-account, portfolio, averaged over order type:

| Model | In-sample | Out-of-sample |
|---|---|---|
| Crossover models (all 4 MA types) | **−8.4%** | **−21.2%** |
| Slope models (all 4 MA types) | **−9%** | **−20%** |
| Counter-trend contrarian-crossover | **−9.7%** | **−21.4%** |
| Counter-trend MA support/resistance | **−3.0%** | **−12.0%** |

**Every single cell of Table 6-3 is negative.** Out of 48 model/order combinations, exactly one
made money in **both** samples: the **simple-MA support/resistance model entered on a stop**
(+$227/trade, ROA 4.2% in-sample; +$482/trade, ROA 14.8% out-of-sample) — and the authors
immediately caveat it: *"had relatively few trades: consequently, the results are less
statistically stable."*

**What this is worth to us — four things:**

1. ⚠️ **A caution against the KAMA/adaptive-MA candidate (§54.5).** Their VIDYA-style adaptive
   moving average — the same *family* as Kaufman's KAMA, both adapting speed to volatility/noise
   — was among the **worst** performers, and the authors flag it as their biggest surprise:
   *"Expect surprises. For the slope-based models, we thought the adaptive moving average, with
   its faster response, would provide the best performance; **in fact, it provided one of the
   worst**."* This is not proof KAMA fails (VIDYA adapts on volatility, KAMA on the Efficiency
   Ratio — a real difference, and §54.1 argues the ER is the better signal). But it **downgrades
   §54.5 from "promising candidate" to "test before believing"**, and it says the *adaptive*
   property is not self-justifying. Cheap to settle: our harness can run KAMA against a plain
   SMA/EMA crossover on the same data.
2. **Simple beat sophisticated.** *"In-sample, the simple moving average provided the best
   results in average dollars-per-trade. The worst results were for the adaptive moving
   average."* Reinforces the **KISS principle (§26)** with tested evidence.
3. ⭐ **The one genuinely constructive finding — combine a counter-trend entry with a
   trend-following context.** Their *What Have We Learned?*:
   > *"When designing an entry model, try to effectively combine a countertrend element with a
   > trend-following one. This may be done in any number of ways, e.g., **buy on a short-term
   > countertrend move while a longer-term trend is in progress**; look for a breakout when a
   > countertrend move is in progress; or apply a trend-following filter to a countertrend model."*
   >
   > *"The best models apparently are those that combine both countertrend and trend-following
   > elements. For example, attempting to **buy on a retracement with a limit, after a moving
   > average crossover or breakout**, provides better results than other combinations."*

   This is precisely **§54.16 (Raschke First-Cross)**, **§17.1 (pullback-in-uptrend)** and
   **§54.24's** "fast oscillator in a slower trend" — now with a third independent endorsement,
   and with the mechanism named: it is *also* why the limit entry of §58.1 works. The limit
   entry **is** the counter-trend element bolted onto our trend-following breakout. That
   reframing matters: §58.1 is not only a cost-saving trick, it is the book's own prescription
   for the best-performing model archetype.

   ⚠️ **But hold this against our own refutation.** Our pullback-continuation rule was **refuted
   on crypto** with negative expectancy. The distinction that survives both facts: the refuted
   rule bought a *pullback as the primary signal*; what Katz & McCormick endorse is a
   *breakout as the primary signal, filled on a shallow retracement*. Entry timing, not entry
   thesis. That is a **testably different rule**, and the harness should not treat the earlier
   refutation as settling it.
4. **Support/resistance again outperformed formula indicators**, echoing §58.6:
   > *"Even though traditional indicators, used in standard ways, usually fail (as do such
   > time-honored systems as volatility breakouts), classical concepts like **support/resistance
   > may not fail; they may actually be quite useful.**"*

   → Reinforces `analysis/levels.py` (§1.3, §4.8, §34.3) as a durable-concept module, and
   reinforces §58.6's preference for our Donchian (a support/resistance construct) over ATR
   bands.

---

## §58.10 ⭐⭐ Oscillators: RSI overbought/oversold was the WORST model in the book — *Ch 7*
**Module: `strategy/rules/rsi_mean_reversion` (already refuted), `analysis/indicators.py`**

Chapter 7 runs 21 tests across three oscillators (Stochastic, RSI, MACD) × three model types
(overbought/oversold, signal-line crossover, divergence) × three entry orders.

**Table 7-3 — annualized ROA (top line) and average $/trade (bottom line), portfolio:**

| Model | In-sample ROA / $trade | Out-of-sample ROA / $trade |
|---|---|---|
| Stochastic overbought/oversold | −10.1% / −$2,829 | −23.3% / −$2,761 |
| **RSI overbought/oversold** | **−10.0% / −$6,015** | **−20.2% / −$3,113** |
| Stochastic signal line | −10.3% / −$2,165 | −23.5% / −$1,874 |
| MACD signal line | −9.2% / −$1,498 | −20.5% / −$1,075 |
| Stochastic divergence | −10.0% / −$2,899 | −20.9% / −$2,873 |
| RSI divergence | −8.6% / −$1,705 | −20.2% / −$3,133 |
| ⭐ **MACD divergence** | **+22.0% / +$1,568** | **+5.5% / +$179** |

### §58.10a The RSI mean-reversion refutation is independently reproduced

> *"Tests 4 through 6: RSI Overbought/Oversold Models… The model performed **more poorly than
> the Stochastic** overbought/oversold one. The percentage of winning trades was **extremely
> low, ranging from 26% to 37%**… The average loss per trade reached **over $7,000**. **This
> model did not capture any market inefficiency.**"*

And in the summary: *"When considered across all order types and averaged, **overbought/oversold
models using the RSI were worst** (especially in terms of dollars-per-trade)."*

**This is the single most valuable confirmation in the book for us.** Our own sim refuted
RSI mean-reversion on crypto and we treated that as a crypto-specific finding. Katz & McCormick
show the same model was **the worst-performing entry in a 36-market futures portfolio across
14 years, in-sample and out-of-sample**. The refutation is not about crypto — **buying an RSI
oversold reading is a structurally bad entry**, and the KB should record it as settled rather
than as a one-market result.

Note the mechanism they give, which is exactly the "falling knife" our sim observed:
> *"The primary weakness of simple oscillator-based entries is that they perform poorly in
> sustained trends, often giving many false reversal signals. Some oscillators can easily become
> stuck at one of their extremes; it is not uncommon to see the Stochastic, for instance, pegged
> near 100 for a long period of time in the course of a significant market movement."*

→ **Action:** the `rsi_mean_reversion` rule class should be marked **refuted with external
corroboration** in the KB and in `promotion.py`'s rule registry, not merely "failed our
backtest". Do not spend another cycle on parameter variants of it.

### §58.10b ⚠️ RSI *divergence* also failed — and this contests §55.1

RSI divergence was also solidly negative (−8.6% / −20.2%), and the authors single it out:

> *"Overall, the results were poor… **Given that the RSI has been one of the indicators
> traditionally favored by traders using divergence, its poor showing in these tests is
> noteworthy.**"*

**This directly contests §55.1's divergence strength ladder**, which we adopted from an
*unvalidated* source (WizardTrader — "no backtests/samples", per the README) and which upgrades
`rsi_divergence` to an ordinal CTS grade. §58.10 is a *tested* result on the same construct and
it is negative. Given the KB's own note that §55 is unvalidated, **§58 should outrank it**: keep
the ordinal-grade *mechanism* (it is a better data structure than a boolean), but do not assume
RSI divergence carries a positive edge, and give it low CTS weight until our own harness says
otherwise.

### §58.10c ⭐ MACD divergence — the ONE oscillator model that worked, and it wants a limit entry

The exception is dramatic and specific:

> *"Finally, models that appear to work, producing **positive returns in both samples!**… The
> limit performed slightly worse in-sample, but much better out-of-sample… In-sample, the
> average profit per trade was **$1,250 with 47% winning trades (the highest so far)**; longs
> and shorts were profitable… Out-of-sample, the model made **$985 per trade; won 44% of the
> time**; was profitable in long and short positions."*
>
> *"The best results across samples were for the **MACD divergence model**. The limit produced
> the best combined results in both samples: a **12.5% return (annualized) and $1,250 per trade
> in-sample, and a 19.5% return (annualized) and $985 per trade out-of-sample. This model is
> dramatically different from all others.**"*

And the interaction finding: *"The divergence model, for example, worked well with the MACD, but
**terribly with the RSI**. Such results demonstrate that, when studying a model with an
indicator component that may be varied without altering the model's essence, it is important to
test all model-indicator combinations."*

⚠️ Honest caveats: out-of-sample statistical significance was weak (*p* ≈ 27.7% uncorrected),
trade count was low, and *"only shorts were profitable"* in one of the three order variants —
so part of the edge sits on the side we cannot trade. Treat it as a **promising candidate, not
a validated rule**.

**The mechanical divergence detector they used** (worth capturing — §55.1 says our detector must
compare non-adjacent pivots but does not fully specify it; this does, and it is testable as-is):

```
Over a look-back window `len3` (tested 15–25):
  find the lowest PRICE bar and the lowest OSCILLATOR bar
  BUY signal requires ALL of:
    1. the lowest price bar occurred at least 1 bar ago  (a definable valley exists)
    2. ...but within the past 6 bars                     (the valley is close to now)
    3. the lowest price bar occurs at least 4 bars AFTER the lowest oscillator bar
       (the oscillator's deepest valley must PRECEDE the price's deepest valley)
    4. the lowest oscillator bar is not the first bar of the look-back (a definable bottom)
    5. the oscillator has just turned upward
MACD parameters tested: fast 3–15, slow 10–40, signal fixed at 9.
```

Conditions 1–4 are a precise, codeable formalization of "non-adjacent pivots" and of the
*ordering* requirement (oscillator bottoms first) that §55.1 gestures at. Condition 2 is a
**recency gate** we do not have. Condition 5 is a **turn confirmation** — do not signal on the
valley, signal on the turn off it.

→ **Testable candidate:** `macd_divergence` entry, limit order, as a *second* rule class beside
the Turtle. Its value to open defect (a) is that it is **structurally uncorrelated with a
breakout** — it fires when the Turtle cannot, which is exactly what an under-deployed,
mostly-cash agent needs. Long-only reading: take only the bullish-divergence (buy) branch;
the bearish branch becomes an **exit / don't-buy filter**.

### §58.10d Entry-order finding replicated a third time

Averaged across all 21 oscillator tests: *"When all models were averaged and broken down by order
type, the **limit order was best and the entry at open worst**."* — the same ordering found in
Ch 5 (breakouts) and Ch 6 (moving averages). **Three independent chapters, ~80 tests, same
conclusion.** This is the most heavily replicated result in the book and the basis for §58.1's
priority.

One refinement worth keeping: **a stop entry sometimes beat a limit for *counter-trend* models**,
and the authors explain why — *"This model likes stops, perhaps because they act as trend
filters: After countertrend activity is detected (triggering an entry signal), before entry can
occur, the market must demonstrate reversal by moving in the direction of the trade."* So the
rule is not "always limit"; it is **limit for trend-following entries** (which already contain
their own confirmation), **stop for counter-trend entries** (which need confirmation added).

---

## §58.11 ⭐⭐ The book's central result: most entries were no better than RANDOM — *Ch 13*
**Module: `strategy/promotion.py`, `strategy/backtest.py`**

Part III inverts the experiment. Instead of a fixed exit and varying entries, they fix a
**random entry** and vary the exit. The random entry model is exactly what it sounds like:

```
On each bar, draw u ~ Uniform(0,1) from a high-quality RNG (ran2, Numerical Recipes):
    u > 0.975 → long entry at next open
    u < 0.025 → short entry at next open
    otherwise → no signal
⇒ a signal roughly every 20 bars, direction random.
Run 10 independent seeds; use the mean and standard deviation as the benchmark distribution.
```

The random-entry + standard-exit portfolio produced **−$2,243 per trade, 36.91% wins**, with a
standard deviation across seeds of only **$304 and 0.70 percentage points** — i.e. a *tight,
well-characterized null distribution*.

Then the punchline:

> *"The results clearly demonstrate that **many of the entry strategies tested in earlier
> chapters using the SES were no better than random entries. Sometimes they were worse.**"*
>
> *"The performance figures in Tables 13-1 through 13-3 provide a **baseline** (in the form of
> means and standard deviations) that can serve as a **yardstick** when evaluating the entries
> studied in Part II. For this purpose the $TRD and WIN% figures are the best ones to use since
> they are not influenced by the number of trades taken by a system."*

Check it against §58.10: the oscillator models averaged **−$2,220/trade in-sample** against a
random-entry benchmark of **−$2,243**. They were, to within noise, **exactly random**. And the
Conclusion states outright that *"the RSI overbought/oversold model was the worst of them all.
In both samples, it provided staggering losses that were (statistically) significantly **worse
than those that would have been achieved with a random entry**."*

### ⭐ This is a concrete, high-value harness upgrade we do not have

`promotion.py` currently gates on **absolute** thresholds (expectancy, R:R ≥ 1.5–2, win-rate
floors, `min_trades: 100`). §54.10 adds walk-forward and OOS discipline. **Neither answers the
question "is this rule better than entering at random?"** — and Katz & McCormick show that is
the question most published entry models fail.

**Proposed: a random-entry control arm in `keel simulate`.**

```
For a candidate rule R with exit E:
  1. Run R+E → observe avg_return_per_trade, win_rate, expectancy.
  2. Generate N ≥ 20 random-entry sequences matched to R's TRADE FREQUENCY
     (long-only: draw a long signal with p = R's historical signals-per-bar).
  3. Run each random sequence through the SAME exit E, same costs, same period.
  4. Report R's percentile against that null distribution.
  5. Promotion requires R to beat the random-entry mean by ≥ 2 standard deviations
     on per-trade expectancy.
```

**Why this is worth building for `keel` specifically:**
- It is the **cleanest possible test of whether the entry signal carries information**, and it
  is *robust to the small-sample problem that plagues us*. Our Turtle has ~23 trades in 5 years,
  far under the `min_trades: 100` bar — meaning the current promotion gate cannot really be
  satisfied. A random-entry control with matched trade frequency gives a **valid significance
  test at low trade counts**, because the null is simulated rather than assumed.
- It **separates entry edge from exit edge** — the §58.0 attribution problem. If the Turtle
  beats random entries with the same exit, the Donchian signal is real. If it doesn't, our
  1.4% return is coming from the ATR stop and the trend of the underlying, not from the signal.
  That is a question we currently cannot answer and should be able to.
- Long-only adaptation is trivial and necessary: draw **only long** signals (a random-direction
  benchmark is meaningless when we can only go one way), and match trade frequency so the
  comparison isn't confounded by cost drag.

### ⚠️ And the corollary — the exit may be doing more work than the entry

> *"A good exit strategy is extremely important. **It can even pull profits from randomly
> entered trades!** Think of what it could do for trades entered on the basis of something
> better than the toss of the die."*

With the best exit found in Ch 14, *"the Swiss Franc, Light Crude, Heating Oil, COMEX Gold, and
Live Cattle had positive returns both in- and out-of-sample"* — **profitable systems built on
random entries** — and Feeder Cattle / Live Hogs returned 10.9% / 15.5% in-sample and 43.1% /
31.9% out-of-sample **on random entries**. The overall improvement:

> *"When compared with the standard exit strategy used in the tests of entry methods, which lost
> an average of $2,243 per trade… the best exit strategy thus far developed reduced the loss per
> trade to $1,236, representing a **reduction in loss per trade of over 44%**. The reduction is
> substantial enough that **many of the better (albeit losing) entry models would probably show
> overall profitability if they were combined with the best exit strategy.**"*

→ **Strategic implication for the project: our next build cycle should probably target EXITS,
not a new entry rule.** The KB has accumulated a long queue of candidate *entries*
(§54.3, §54.5, §54.11, §54.12, §54.15, §54.16, §54.22, §58.10c). This book argues that is the
lower-yield half of the problem. Combined with defect (b) — "stops historically too tight for
crypto ATR" — the highest-expected-value work is the exit sweep in §58.12–§58.14 below.

---

## §58.12 ⭐⭐ Optimal stop width — a measured INTERIOR optimum at ~1.5 ATR — *Ch 14*
**Module: `execution/executor.py`, `strategy/rules/turtle_breakout` — open defect (b)**

This is the most directly useful table in the book for us. The money-management stop (`mmstp`,
in ATR(50) units) was stepped 0.5 → 3.5 against the profit target (`ptlim`) stepped 0.5 → 5.0,
**on random entries** so that the result is attributable to the exit alone.

Selected cells from Table 14-1 (ARRR / WIN% / avg-$-per-trade), profit target held at 4.5:

| Stop width (ATR units) | 0.5 | 1.0 | **1.5** | 2.0 | 2.5 | 3.0 | 3.5 |
|---|---|---|---|---|---|---|---|
| ARRR | −2.54 | −1.66 | **−1.46** | −1.48 | −1.60 | −1.68 | −1.67 |
| WIN% | 19 | 32 | **39** | 43 | 44 | 46 | 45 |
| Avg $/trade | −1,824 | −1,590 | **−1,581** | −1,714 | −1,933 | −2,077 | −2,109 |

The authors' conclusion:

> *"There appears to be an **optimal placement for a fixed money management stop. Too wide a
> stop increases the percentage of wins. However, it also increases the overall loss. Too tight
> a stop keeps the individual losses small, but drastically cuts the percentage of winning
> trades**, again resulting in worse overall performance. An optimal value provides a moderate
> percentage of winning trades and the best performance. In this case, **the optimal distance to
> place the money management stop away from the entry price was 1.5 average true range units.**
> With some entry systems, the optimal placement might be much closer."*
>
> *"For most of the profit target limits, there was an optimal placement for the money
> management stop, **between a value of 1.0 and 2.0 average true range units** away from the
> entry price."*

**Four things to extract carefully:**

1. ⭐ **The optimum is INTERIOR, and both failure modes are real.** This is the most important
   structural point and it refines defect (b). The KB's framing has been one-directional —
   "stops are too tight for crypto, widen them" (§22.1, §34.1, §54.6, §36). Katz & McCormick
   measured the other side: **past ~2 ATR the win rate keeps climbing while every other metric
   deteriorates**, because the losses you do take grow faster than the trades you save. A
   widening sweep scored on win-rate will walk straight past the optimum. **The sweep must be
   scored on expectancy/ARRR, not win rate**, and must bracket the optimum from both sides.
2. ⚠️ **1.5 is NOT our number and must not be copied.** Three reasons: (a) their ATR is
   **ATR(50)** and ours is ATR(20) — a shorter, more reactive ATR yields systematically
   different multiples; (b) their entry is **random**, and they say explicitly *"with some entry
   systems, the optimal placement might be much closer"*; (c) crypto's return distribution is
   fatter-tailed than 1990s futures. What transfers is **the shape of the curve and the method**,
   not the constant. Our Turtle's 2N (2·ATR(20)) sits inside their 1.0–2.0 optimal band, which
   is mild reassurance that we are in the right neighborhood — not evidence we are optimal.
3. **Testable directly:** sweep `stop_atr_mult ∈ {1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0}` ×
   `atr_period ∈ {20, 50}`, score on expectancy, and look for the **robustness plateau**
   (§54.11) — expecting a genuine maximum rather than a monotone "wider is better" curve.
4. ⚠️ **Their stop is close-confirmed in the SES but intrabar in the MSES**, and the MSES was
   materially better: *"The MSES, which simply lifts the restriction of the exit to the close,
   performed much better."* This **cuts against §34.1's close-based stop confirmation** — a
   tension worth flagging honestly. The likely resolution is that their gain came from *faster
   escape from bad trades* (*"the ability of the MSES to more quickly escape from bad trades,
   cutting losses short. The more rapid and frequent escapes also explain the decline in the
   percentage of winning trades"* — wins fell 39% → 31.7%), which is a different trade-off from
   crypto whipsaw. **Both belong in the sweep**: `stop_trigger ∈ {close, intraday}` should be
   swept jointly with `stop_atr_mult`, not fixed by prior belief.

---

## §58.13 ⭐ The dynamic-stop ladder, ranked by test — and §55.3's 2-bar trail is confirmed to fail
**Module: `execution/executor.py`**

Four stop designs, all on random entries with the optimal profit target, so the ranking is a
clean comparison. Ranked best → worst by annualized risk-to-reward ratio:

| Rank | Stop design | ARRR | WIN% | Avg $/trade |
|---|---|---|---|---|
| 1 | **MEMA (modified-EMA ratchet) stop** — init 2.5 ATR, ATR offset 1.0, adapt coeff 0.30 | **−1.36** | 37% | **−1,407** |
| 2 | **Dynamic ATR trailing stop** — first-bar 2.0 ATR, later-bar 2.5 ATR from current close | −1.40 | 42% | ~−1,450 |
| 3 | **Optimal fixed stop** — 1.5 ATR from entry (§58.12) | −1.46 | 39% | −1,581 |
| 4 | ⛔ **2-bar highest-high/lowest-low trailing stop** | **−2.52** | **28%** | **−1,864** |

### §58.13a ⭐ The 2-bar channel trail failed — exactly as the KB predicted

The README's module map lists §55.3's **2-bar channel trail** as *"the TIGHT LOWER BOUND of the
trail sweep — **expected to lose on crypto** (it is the shakeout stop §34.1 exists to fix)."*
**Katz & McCormick tested it and it lost, for precisely the predicted reason:**

> *"This stop appears to have been **consistently too tight**, as evidenced by a decreased
> percentage of winning trades when compared with the baseline MSES model… the best solution has
> only **28% of the trades winning** in-sample and 29% out-of-sample. **Many potentially
> profitable trades (some of them trades that would have been profitable with the basic MSES,
> using an optimal fixed stop) were converted to small losses.** The tightness of this stop is
> also demonstrated by the total number of bars the average trade was held (4), compared with
> the usual 6 to 8 bars… **The 2-bar HHLL stop is obviously no great shakes, and one would be
> better served using a fixed, optimally placed stop.**"*

→ The KB's prediction is **confirmed by an independent controlled test**. This lets us
**deprioritize** the 2-bar trail from the sweep rather than spend cycles on it, and it
strengthens the §34.1 / §54.6 "stops too tight" thesis with a measured mechanism: a too-tight
trail does not merely lose a bit — it **halves the holding period and converts winners into
losers**, dropping the win rate by 11 points versus a well-placed fixed stop.

### §58.13b The MEMA stop — a NEW trailing-stop design with no KB equivalent

The winner is a design the KB does not have. §54.6/§54.8 give us the Kase Dev-Stop, an
ER-adaptive ATR stop and Parabolic SAR; §23.2 gives a channel-low trail; §26.2 a 20-SMA
close-below. The MEMA stop is structurally different — **an exponential moving average of price
that is only ever allowed to move toward the market, never away**:

```
# long position; atr = ATR(50)
on entry bar:
    stop = entry − mmstp × atr                      # mmstp ≈ 2.5
on each later bar:
    tmp = (High_t − stpa × atr) − stop              # stpa ≈ 1.0  (ATR offset)
    if tmp > 0:  stop = stop + stpb × tmp           # stpb ≈ 0.30 (adaptation rate ≈ 5-bar EMA)
    # if tmp <= 0 the stop is unchanged — it can never move down
```

> *"This method involves nothing more than a kind of offset exponential moving average (EMA),
> except that the moving average is initialized in a special way on entry to the trade and is
> only allowed to move in one direction; i.e., **the stop is never polled further away from the
> market, only closer**… `stpb` determines the effective length of an exponential moving average
> that can only move in one direction, in toward the prices."*

**Why it is interesting for crypto:** it is a **smoothly accelerating** trail. It starts wide
(2.5 ATR, respecting defect (b)) and tightens *gradually and proportionally to how far price has
run*, rather than snapping to a recent extreme the way a channel-low or N-bar trail does. That
is exactly the profile a fat-tailed, high-noise asset wants: wide enough early to survive the
shakeout, progressively protective as the fat tail develops. It is also **three parameters, all
continuous and well-behaved** — the authors note *"the model was well behaved with respect to
variations in the parameters"*, which is the robustness-plateau property §54.11 asks for. And it
never widens, so it composes with the **no-stop-widening rail** (§5.1) by construction.

→ **Testable candidate `trail_method=mema`** with `(mmstp, stpa, stpb)` swept around
`(2.5, 1.0, 0.30)`. Cheap to implement (three lines), long-only by construction, and it ranked
first in the only head-to-head test of trailing stops anywhere in the KB.

---

## §58.14 Profit targets: looser is better, and none may beat a tight one — *Ch 14*
**Module: `execution/executor.py`**

> *"As the profit target limit got tighter, the percentage of winning trades increased; this was
> expected… **However, the increased percentage of winning trades with tighter profit targets was
> not sufficient to overcome the effects of cutting short on trades that had the potential to
> yield greater profits. Looser profit targets performed better than tight ones.**"*
>
> *"Profits should not be cut short even though a higher percentage of winning trades might be
> gained… **This clearly shows the importance of letting profits run.**"*
>
> *"The results indicate that care has to be taken with profit targets: They tend to prematurely
> close trades that have large profit potential… **Sometimes it is better to have no profit
> target at all than to have an excessively tight one.**"*

The optimal fixed target was **4.5 ATR against a 1.5 ATR stop** — a **3:1 target-to-stop ratio**
at a **39% win rate**. That is squarely consistent with the KB's breakeven-winrate floor
(§23.1/§25.5/§35.2: `win_rate > 1/(1+R:R)`; at R:R 3, 25% suffices — 39% clears it comfortably)
and with our live `turtle_breakout` at 38.7% win / R:R ≈ 2.5. It is also a clean rebuttal of
§57.4's "70%+ win ratio" goal, from tested data.

→ **Reinforces §54.3's trend-follower caveat** ("for long-term trends a profit target *hurts* —
you forfeit the rare fat-tail move") with an independent measurement, and supports running the
Turtle with **no fixed profit target**, exiting on the channel/trail instead.

**One new mechanism worth a sweep slot — the "shrinking profit target"**, designed for exactly
the dead-position problem §57.2 raises:

```
limit = entry + ptlim × atr                                  # ptlim ≈ 5.5 (start FAR away)
each later bar:  limit = limit − ptga × (limit − Close_t)    # ptga ≈ 0.10 (creep toward price)
```

It starts too far away to be hit and creeps toward the market, so a **languishing** trade
eventually exits *"with a limit order on market noise, while not cutting profits short early in
the course of favorably disposed trades."* Result: ARRR improved −1.36 → −1.32 and average loss
$1,407 → $1,325 over the fixed target. It is a **profit-side analogue of the time stop** — and,
being a limit, it exits as a **maker with no slippage**. Modest but real, and it composes with
`max_hold` rather than competing with it.

---

## §58.15 The time exit: extending `maxhold` 10 → 30 days helped, mildly — *Ch 14*
**Module: `execution/executor.py` — the §57.2 `max_hold` candidate**

> *"Extension of the time limit improved results, but not dramatically. **Most trades were closed
> out well before the time limit expired; i.e., the average trade only lasted between 6 and 10
> bars.**"* (ARRR −1.32 → −1.22; average loss per trade $1,325 → $1,236.)

**Three readings for us:**
1. **Confirms `max_hold` belongs in the exit set** (§57.2's candidate, and part of this book's
   baseline per §58.0.3) — a standard component, not an exotic one. Second independent
   endorsement.
2. **But it is a backstop, not a driver.** The time limit rarely binds; loosening it produced a
   ~7% improvement in per-trade loss. Do not expect `max_hold` alone to fix defect (a).
3. ⚠️ **Their whole time-scale is ~10× faster than ours** — 6–10 bar average holds vs our ~24
   days. A `max_hold` calibrated from this book would be far too short. Sweep it against **our**
   observed hold distribution (the sim's 575-hour average), exactly as §57.1 warns for the
   streak breaker. A sensible bracket: `max_hold ∈ {30, 60, 90, 120 days, none}`.

---

## §58.16 Chapters covered by skim — headline results only
**Module: mostly none (recorded so no one re-mines them)**

Summarized from each chapter's *Conclusion* plus the book's closing chapter, which aggregates
all model families:

- **Ch 8 Seasonality (pp. 153–178).** The best-performing *conventional* family in the book.
  *"The seasonal models, on the whole, were **clearly better than chance**… two of them were
  profitable out-of-sample."* The **seasonal crossover with confirmation, entry on stop** was
  profitable in **both** samples ($846/trade, 7.4% in-sample; $1,677/trade, 9.5% out-of-sample).
  ⚠️ **Not adopted.** Their seasonality is a *calendar* effect on physical commodities (crop
  cycles, heating demand) with a real economic mechanism. Crypto has no crop cycle; §14.3
  already places BTC halving-cycle seasonality as a **low-weight** CTS factor and §6.4 forbids
  calendar prediction as an oracle. Logged as: *the seasonal result does not port — it was
  structural to physical commodities.*
- **Ch 9 Lunar & Solar Rhythms (pp. 179–202).** *"The basic lunar model had mixed findings. Most
  of the in-sample results were slightly positive when compared with chance… but not
  profitable."* Solar/sunspot models *"performed slightly better than chance in-sample, [but]
  were mixed and variable out-of-sample."* ⛔ **Excluded under no-oracle (§6.4)**, alongside the
  KB's standing Elliott/Gann/astrology exclusion. Recorded only because the result is
  *negative*, which retires the topic rather than leaving it open.
- **Ch 10 Cycle-Based Entries (pp. 203–226).** Butterworth / wavelet filter-bank cycle
  extraction — theoretically the most elegant models in the book, and among the worst:
  *"the cycle models, when using entry at open or on limit, actually performed **significantly
  worse in recent years than a random entry**."* ⛔ Not adopted — negative result, plus the
  implementation (maximum-entropy spectral analysis, filter banks) is not reproducible in
  `keel`'s Decimal/hand-rolled indicator stack.
- **Ch 11 Neural Networks (pp. 227–256) & Ch 12 Genetic Algorithms (pp. 257–280).** Ironically
  the *best* out-of-sample performers (*"the out-of-sample performance was, by far, the best for
  the long-side genetic models… 64.2% in-sample and 41.0% out-of-sample"*), and the authors flag
  that *"significant curve-fitting was only detected with the genetic and neural network
  models."* ⛔ **Excluded**, consistent with §54's exclusion of neural/genetic/fuzzy methods as
  **non-reproducible black boxes**, and with §6.4 / §35.1 (AI may explain, never decide). Note
  this is a **cost we are knowingly accepting**: the book's best OOS numbers sit inside the box
  we deliberately closed. The KB's reasoning stands — a model we cannot inspect, reproduce or
  audit cannot pass a promotion harness or a rail.
- **Ch 1–2 Data & Simulators (pp. 3–28).** 1990s data vendors (Pinnacle, DTN, Bonneville),
  contract back-adjustment, and C-Trader/TradeStation mechanics. Skimmed; **obsolete**. One
  durable nugget: their **data-checking utility** flags prices implausible relative to recent
  range — the same idea as our **data-spike guard (§24.3)**. No action.
- **Ch 3–4 Optimizers & Statistics (pp. 29–70).** Brute-force / genetic / annealing optimizers,
  and significance testing of trading results. Skimmed because **§54.10 covers this ground more
  usefully for us**. Two points survive as reinforcement: (a) they apply an explicit
  **multiple-comparison correction** when reporting the probability a result is chance (e.g.
  *"8.7% uncorrected; 99.9% corrected"*) — a discipline our sweep reports should adopt, since
  sweeping 7 stop widths × 6 look-backs is 42 comparisons; (b) the repeated finding that *"the
  optimization of one or two parameters… had minimal curve-fitting effect"* while
  many-parameter models curve-fit badly — reinforcing **few parameters + robustness plateau**
  (§54.10/§54.11).

---

## §58.17 ⚠️ NEGATIVE EXEMPLAR — the book's own final "625% annualized" portfolio
**Module: `strategy/promotion.py` — a selection-bias trap to guard against**

The book closes by assembling a portfolio: for each of the 36 markets they pick the
model-and-order combination that performed best **on in-sample statistical significance**, then
report that portfolio's results — *"544% annualized in-sample"* and *"625% annualized"*
out-of-sample, framed as *"A manifestation of the Holy Grail?"*

**Treat this as a cautionary exemplar, not a result.** The authors defend it (*"no out-of-sample
optimization took place"*), and the per-market model choice was indeed made on in-sample data
only — but the *portfolio construction step*, choosing which 36 model-market pairs to include,
is itself a selection made with knowledge of what worked, and the reported figure is the
performance of the survivors. It is the **same class of error §54.10's OOS/feedback firewall
exists to prevent**, dressed in a legitimate-looking procedure. Two tells: the same chapter notes
that for the genetically-evolved models *"in-sample markets that performed well… there were
generally no out-of-sample trades. The profitable out-of-sample behavior was achieved on almost a
totally different set of markets"* — i.e. the selection did not transfer — and the headline claim
is wildly out of scale with every individual result in the book's preceding 350 pages, nearly all
of which were negative or single-digit.

→ **Logged for `promotion.py`:** when we run per-asset rule selection (which §58.4 recommends!),
the *selection step itself must sit inside the walk-forward loop*, and the reported OOS number
must come from a window never used to choose the rule. Also a reminder to weight this source's
**methodology** highly and its **headline conclusions** soberly.

---

## §58.18 Reconciliation against prior sources

| Prior KB item | This source |
|---|---|
| §25.1 **ADX > 25 trend gate** (shipped) | ⚠️ **CONTESTED** — §58.2: the ADX filter helped in-sample, gave **no OOS benefit**, and was among the worst OOS results in the book; *"do not rely on indicators like the ADX for trendiness determination."* Ablation-test it. |
| §54.1/§54.9/§54.17 **ER / trendiness market ranking** | ⭐ **STRONGLY REINFORCED** — §58.4: market selection beat every model tweak; the only combination profitable in both samples was a *market restriction*, not a model change. §58.2 adds: filter the **asset**, not the **bar**. |
| §54.3 **volatility-breakout candidate** | ⚠️ **DEPRIORITIZED** — §58.6: HHLL/support-resistance breakouts *"held up better in the tests than other models"*; *"stay away from popular volatility breakouts."* Keeps our Donchian primary. |
| §54.5 **KAMA adaptive-trend candidate** | ⚠️ **DOWNGRADED** — §58.9: the adaptive MA was *"one of the worst"* performers and the authors' biggest surprise. Test before building. |
| §54.6/§54.8 **volatility-adaptive trailing stops** | ⭐ **EXTENDED** — §58.13 adds the **MEMA one-way-EMA stop**, ranked #1 in a controlled 4-way comparison; §58.12 supplies the missing **interior-optimum** finding (too loose is also bad). |
| §54.10 **testing rigor** | ⭐ **COMPLEMENTED, not duplicated** — §58.0 adds *component isolation* (fix the exit, vary the entry); §58.11 adds the *random-entry null benchmark*. §54.10 asks "is it real?"; §58 asks "which part, and is it better than chance?" |
| §54.15 **entry timing / "don't naively delay"** | ⚖️ **PARTIALLY CONTESTED, reconciled** — §58.1 finds the *limit* entry the single biggest improvement across ~80 tests, with *"the limit order did not seriously reduce the number of trades or cause many profitable trades to be missed."* Both are passive entries differing only in offset; sweep as one family. |
| §54.19 **pyramid on profits / never average down** | ⚖️ Untouched — the book does not test position scaling. No conflict. |
| §54.22 **equal-risk-by-ATR allocation** | ⧉ **DUPLICATE** — their "dollar volatility equalization" (size every market to the volatility of 2 S&P contracts) is the identical idea. Not re-extracted. |
| §55.1 **RSI-divergence strength ladder** (unvalidated source) | ⚠️ **CONTESTED** — §58.10b: RSI divergence tested poorly; *"its poor showing in these tests is noteworthy."* §58.10c supplies a fully-specified detector and says use **MACD**, not RSI. Keep the ordinal grade, switch the indicator, lower the CTS weight. |
| §55.2 **MACD-histogram slope confluence** | ⭐ **REINFORCED** — §58.10c: MACD divergence was the *only* profitable oscillator model, in both samples. |
| §55.3 **2-bar channel trail** ("expected to lose") | ⭐ **PREDICTION CONFIRMED** — §58.13a: tested, *"consistently too tight"*, 28% wins, holding period halved, worse than a fixed stop. Deprioritize from the sweep. |
| §57.2 **`max_hold` time exit** | ⭐ **REINFORCED and PROMOTED** — §58.0.3: a time exit is part of this book's *baseline*, not an option; §58.15 measures that extending it helps mildly. Second independent endorsement. |
| §57.2 **close-strength exit** | ⚖️ Untested here. Unchanged. |
| §57.4 **"70%+ win-ratio" negative exemplar** | ⭐ **REINFORCED with data** — §58.14: the optimal configuration ran at **39% wins**; tighter targets raised the win rate while worsening every other metric. |
| §34.1 **close-based stop confirmation** | ⚖️ **MIXED** — §58.0.2 supports it on *simulation-determinacy* grounds (one intrabar order, or the sim is untrustworthy); §58.12.4 finds the *intrabar* MSES outperformed the close-only SES. Sweep `stop_trigger`; don't assume. |
| §35.3 **liquidity-sweep / require a close** | ⭐ **REINFORCED** — §58.1: entry stops at breakout thresholds get filled into *"the flurry of orders"*; §58.6: both channel models require a **close** beyond the level. |
| §27.1 **Turtle 20-day channel** | ⚠️ **look-back CONTESTED** — §58.6: their optimum was **80–95 days**. Not portable (their exit is far faster), but a strong argument to sweep the look-back rather than inherit 20. |
| §26 **KISS** | ⭐ **REINFORCED** — §58.9: the *simple* MA beat all sophisticated variants; §58.16: few-parameter models showed *"minimal curve-fitting effect."* |
| §4.1 **anti-scalping / min-move rail** | ⭐ **REINFORCED** — §58.5: a +76%/yr edge was annihilated by transaction costs alone. |
| §5.1 **no-stop-widening rail** | ⭐ **REINFORCED** — §58.13b's MEMA stop is one-way by construction: *"the stop is never polled further away from the market, only closer."* |
| §28.1–28.2 **long-only as compliance cost** | ⭐ **REFRAMED** — §58.3: long-only *improved* the tested breakout system in both samples. The constraint and the best configuration coincided. |
| §6.4/§35.1 **no-oracle; AI never decides** | ⚖️ **COSTED** — §58.16: the book's best OOS results were the neural/genetic models we exclude. We keep the exclusion; this records what it costs. |
| §24.3 **data-spike guard** | ⧉ Duplicate (their data-checking utility). No action. |
| §14.3 **BTC seasonality, low weight** | ⚖️ Unchanged — §58.16: their seasonal edge was structural to physical commodities and does not port. |

---

## §58.19 ⛔ Halal exclusions and long-only reinterpretations

The entire book is **futures-based, dual-direction and leveraged by construction**. Excluded:

- ⛔ **Futures contracts as the instrument** — the whole 36-market portfolio (S&P, T-Bonds,
  Eurodollars, currencies, grains, livestock, softs) is non-spot derivatives: **gharar**, no
  ownership, deferred settlement (§27.4, §28.1). Nothing about the *instrument* is adopted; only
  the *methods*, re-applied to spot crypto.
- ⛔ **Short selling**, assumed on every page (*"the sells are the exact opposite"*). Per the
  standing rule, short results become **exit / don't-buy filters**:
  - the bearish branch of the MACD-divergence detector (§58.10c) → an **exit signal** for a held
    position, never a short entry;
  - Ch 7's finding that oscillators give *"many false reversal signals"* in sustained trends →
    a **don't-exit-on-an-oscillator-alone** caution;
  - **all short-side P&L in every table is discarded**, which materially changes some readings:
    §58.10c's MACD divergence had a variant where *"only shorts were profitable"* — worthless to
    us, and part of why that rule is logged as a candidate rather than a finding.
- ⛔ **Margin, contract multipliers and "number of contracts"** — `ncontracts = 5673.0 / dlrv`
  sizes positions by dollar-volatility on margin. Riba. We take only the *ratio* idea
  (equal-risk-by-ATR), which we already have from §54.22, funded with actual cash.
- ⛔ **Eurodollar and T-Bond / T-Note / T-Bill markets** in the test portfolio — interest-rate
  instruments; riba (§25.6). Their per-market results are read as noise, not signal, for us.
- ⛔ **Ch 9 Lunar & Solar Rhythms** — excluded under no-oracle (§6.4) *and* as astrology-adjacent,
  matching the KB's standing Elliott/Gann/astrology exclusion. The negative result is recorded so
  the topic can be retired rather than revisited.
- ⛔ **"Gunning" and the catastrophe-stop workaround** (p. 288) — their advice to keep the real
  stop *"in the system on the computer"* and phone the broker when it triggers, leaving only a
  far-away catastrophe stop with the broker. Not halal-excluded, but **rejected on rail
  grounds**: a stop that exists only in the agent's memory is a stop a crash or restart deletes.
  Our design deliberately does the opposite — the resting bracket is **persisted and reconciled**
  with the broker. (The underlying *concern* — a visible tight stop invites a sweep — is already
  handled by §34.1/§35.3 close-confirmation.)
- ⚠️ **"Contrarian trading" — exit into liquidity** (p. 289): *"exit long trades when most
  traders are buying."* Not excluded and mechanically sound (a limit exit into a buying frenzy
  gets a good fill), but **already covered** by §54.3's "take profits on the intraday spike" and
  §54.20's price-shock windfall-taking. No action.

---

## §58.20 Discarded (no agent value)

- **All C++ source listings** (~60 pages of `x19mod02.c`, `x20mod01.c`, TRDSIM class calls,
  `ts.buylimit()` / `ts.exitlongstop()`). The *rules* are extracted above; the code is bound to
  their C-Trader toolkit. Also all **TradeStation/EasyLanguage** commentary, including their bug
  report about TradeStation's Slow %K using an EMA instead of a 3-bar SMA.
- **Ch 1 data-vendor comparisons** (Pinnacle, DTN, Bonneville, Data Broadcasting Corp), contract
  **back-adjustment** methodology and continuous-contract construction — futures-specific and
  25 years obsolete.
- **Ch 3 optimizer implementations** (brute-force stepping, simulated annealing, differential
  evolution, the OptEvolve genetic optimizer) — §54.22 already excludes the GASP genetic
  optimizer on the same reasoning, and our sweeps are small enough for grid search.
- **The 36-market futures portfolio composition** and every market-by-market table
  (Tables 5-1/5-2, 6-4/6-5, 7-1/7-2, 13-4/13-6, 14-7) — pork bellies, feeder cattle, orange juice
  and lumber have no crypto analogue. Only the *portfolio-level* summary tables were used.
- **The companion CD-ROM / order form / `scientific-consultants.com`** (p. 364) — a 1999
  mail-order form.
- **The appendix bibliography** (pp. 365–368) — 1990s *Technical Analysis of Stocks & Commodities*
  articles, largely unobtainable and superseded by §54.
- **Neural-network architecture details** (18-14-4-1 nets, middle-layer neuron counts,
  training/shrinkage discussion) and **genetic rule-template encoding** — excluded per §58.16;
  not actionable under our no-black-box constraint.
- **Chapter 15 (Adding AI to Exits)** beyond its headline — it bolts the Ch 11 neural forecaster
  and Ch 12 evolved rules onto the MSES as signal exits. Same exclusion; and the authors
  themselves temper it (*"great improvement in exit performance should not be expected"*, since
  the rules fire on rare events).
- **The "Points of Light" per-market model assignments** — retained only as the §58.17 negative
  exemplar, not as recommendations.

---

## Net assessment (saturation-honest)

**This is the second-most valuable source in the KB after §54, and it is valuable for a reason no
other source is: it is the only one that tells us what FAILED.** The KB's problem is no longer a
shortage of candidate techniques — §54 alone left ~8 unbuilt entry candidates queued. The problem
is knowing which deserve a build cycle. This book prunes that queue with controlled experiments
rather than assertion.

**Genuinely new (no KB equivalent):**
- ⭐⭐ **The random-entry null benchmark** (§58.11) — a significance test that works at our low
  trade counts, and the only clean way to ask whether the Donchian signal beats chance.
- ⭐⭐ **The limit-entry finding** (§58.1) — replicated across three chapters and ~80 tests, and
  close to free money under Coinbase's maker/taker fee structure.
- ⭐ **The interior stop optimum** (§58.12) — the KB's stop discussion was one-directional
  ("wider"); this measures that too wide is also bad, and that the sweep must be scored on
  expectancy rather than win rate.
- ⭐ **The MEMA one-way-EMA trailing stop** (§58.13b) — a new stop design that won a controlled
  4-way comparison and satisfies the no-stop-widening rail by construction.
- ⭐ **Component-isolation testing** (§58.0) — fix the exit, vary the entry; then the reverse.
- **The shrinking profit target** (§58.14) and the **fully-specified divergence detector**
  (§58.10c).

**Contests things we already shipped or adopted** — the honest headline of this extraction: the
**ADX gate** (§58.2), the **20-day look-back** (§58.6) and **RSI-divergence grading** (§58.10b)
all take hits. None is refuted *for crypto*; each now has a specific, cheap test attached.

**Reinforces (with independent test evidence):** trend-following over counter-trend; long-only;
support/resistance-based breakouts over formula bands; KISS; realistic transaction costs; letting
profits run / no tight profit target; `max_hold`; market selection over model tuning; the
no-stop-widening rail; the breakeven-winrate floor over a 70% win-rate goal; and — pleasingly —
the KB's own *prediction* that a 2-bar trail would fail.

**Excluded:** the entire futures/short/margin frame; lunar-solar; cycle filter banks; neural and
genetic models (knowingly forgoing the book's best OOS numbers, per our no-black-box rule);
seasonality (a real result that does not port); and ~60pp of 1999 C++ and platform minutiae.

**Recommendation:** this book argues our next build cycle should be the **exit sweep**
(§58.12–§58.15) plus the **limit entry** (§58.1) and the **random-entry control** (§58.11) — all
three are changes to existing machinery rather than new rules, all three are directly testable
through `keel simulate`, and all three land on open defects (a) and (b). The ADX ablation
(§58.2) should ride along in the same sweep, since it is a one-flag change with a plausible
2–3× effect on trade count.

Our Turtle is trend-following ⇒ limit.

