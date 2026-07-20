[← Knowledge Base index](../README.md)

# Source 58 — "The Encyclopedia of Trading Strategies" (Jeffrey Owen Katz, Ph.D. & Donna L. McCormick, McGraw-Hill, 2000, 386pp)

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
PLACEHOLDER_TAIL_ANCHOR
Our Turtle is trend-following ⇒ limit.

