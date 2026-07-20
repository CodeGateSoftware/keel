[← Knowledge Base index](../README.md)

## Source 80 — Four papers on rule-family comparison, FDR rule selection, crypto time-series momentum, and the MACD adjudication

(A) **Robert Hudson & Andrew Urquhart, "Technical trading and cryptocurrencies"** — *Annals of Operations
Research* **297** (2021), pp. 191–220, doi:10.1007/s10479-019-03357-1, open access (CC-BY), 30pp. Two Bitcoin
markets (CoinDesk from 18 Jul 2010; Bitstamp from 1 Dec 2012) plus Litecoin (28 Apr 2013), Ripple (4 Aug 2013)
and Ethereum (7 Aug 2015) — **all series end 31 Dec 2017**, with a 6-month pure out-of-sample extension into
H1 2018. **14,919 trading rules** across **five Brock-style classes**. Stationary bootstrap (Politis & Romano
1994, block length 10, B=1000) for individual p-values, then **four multiple-hypothesis corrections**:
Bonferroni, Holm (FWER) and Benjamini–Hochberg, Benjamini–Yekutieli (FDR). **⭐⭐ TOP PRIORITY — the only
study anywhere that runs channel breakout against four rival families on identical crypto data.**

(B) **Pierre Bajgrowicz & Olivier Scaillet, "Technical trading revisited: false discoveries, persistence
tests, and transaction costs"** — *Journal of Financial Economics* **106**(3), 2012, pp. 473–491, 59pp
(accepted manuscript, Archive ouverte UNIGE). DJIA daily, Jan 1897 – Jul 2011, the **7,846-rule universe of
Sullivan, Timmermann & White (1999)**. **The method paper of the batch:** first application of the False
Discovery Rate (Barras, Scaillet & Wermers 2010) to data snooping, plus the first genuine ex-ante persistence
test of trading rules. **Not crypto** — extracted for its METHOD and its NEGATIVE RESULT.

(C) **Yukun Liu & Aleh Tsyvinski, "Risks and Returns of Cryptocurrency"** — NBER Working Paper 24877, Aug
2018, 68pp (subsequently *Review of Financial Studies* **34**(6), 2021). Bitcoin 01 Jan 2011 – 31 May 2018,
Ripple from 04 Aug 2013, Ethereum from 07 Aug 2015; CoinDesk prices. Newey–West / bootstrapped predictive
regressions. Documents **time-series momentum at 1–4 week horizons** and **near-zero exposure to equity,
currency, commodity and macro factors**.

(D) ⚠️ **Anzhi Chen, Zigan Wang & Mengxin Yang, "Testing the Applicability of the Technical Trading Strategy
in the Cryptocurrency Market"** — *Journal of Finance and Economics* **11**(4), 2023, pp. 195–234, 40pp,
doi:10.12691/jfe-11-4-2. **⚠️ VENUE CAVEAT — LOW TIER: the publisher is Science and Education Publishing
(SciEP), which appears on widely-circulated lists of questionable open-access publishers.** The work itself
was inspected and is methodologically legitimate — it does what it says: BTC/USDT and ETH/USDT Binance daily
bars, 17 Aug 2017 – 31 Oct 2023, a **true IS/OOS split at 20 Dec 2021**, White's Reality Check + Hansen /
Romano–Wolf / Hsu-Hsu-Kuan stepwise SPA over a Politis–Romano stationary bootstrap (Q=0.9, B=1000, α=0.05),
83 strategy variants across EMAC, RSI, Bollinger and **MACD**, with slippage 0.001 and commission 0.0003.
**Extracted as NEGATIVE/SUPPORTING EVIDENCE ONLY. It is never load-bearing on its own** — but it is the only
open-access crypto MACD study that has BOTH a reality check AND a real out-of-sample period, which is exactly
the evidence §74.5 lacks.

> **Why this source exists.** §73.3 established that the Turtle fires ~**2.6 trades/yr/asset** and that
> ~**68 trades** are needed for its edge to clear z≥2 against §58.11's random-entry null — roughly **26
> years** at current frequency. §75.1 then showed that the proposed fix (§60.2 rank-and-fill) cannot produce
> signals at `|allowlist| = 3`, and that lifting `portfolio_sim.py:600` buys **correlated** trades, which
> help deployment but not **knowability**. The only remaining lever is a **second rule class that is
> genuinely uncorrelated with the channel breakout**. This source was assembled to answer: does the
> breakout survive a fair head-to-head? Is the MACD family a real candidate or a §74.5 artifact? And can
> rule-class independence be **cited**, or must it be **measured**?

---

### §80.1 ⭐⭐ [A] Five rule families, identical crypto data, same period — channel breakout is FIRST or SECOND on every market `strategy/rules/`

This is the comparison the KB has been missing. [A]'s Table 2 reports the **average annualized return across
every parameterization** of each class (not the best — the average, which is the honest number). All entries
are significant at the 1% level (\*\*\*):

| Rule class | CoinDesk | Bitstamp | Litecoin | Ripple | Ethereum | Rank |
|---|---:|---:|---:|---:|---:|:--:|
| **Channel breakout** | **10.11%** | **7.89%** | **9.21%** | **12.21%** | **15.99%** | **1st ×1, 2nd ×4** |
| Filter rule | 10.24% | 7.97% | 9.18% | 12.30% | 16.45% | 1st ×4, 2nd ×1 |
| Moving average | 7.72% | 5.82% | 6.74% | 8.89% | 11.13% | 3rd |
| Oscillator (RSI) | 7.34% | 5.07% | 5.85% | 7.94% | 9.40% | 4th |
| Support-resistance | 3.33% | 2.63% | 1.96% | 4.73% | 3.85% | **5th on all five** |

Risk-adjusted (Table 3, annualized Sharpe) tells the same story — channel breakout 0.0995 / 0.0769 / 0.0908 /
0.1211 / 0.1586, with only the filter rule ever ahead and only narrowly.

**Verdict on item 1: the channel-breakout family HOLDS UP.** It is never worse than second across five
cryptocurrencies, on five different sample windows, on both raw and risk-adjusted metrics — beaten only by
the filter rule, and only by 0.05–0.46 percentage points. **This is the first fair head-to-head the KB has,
and the breakout family finishes at or near the top of it.** ⇒ §74.1's crypto-specific corroboration is now
supported by a much larger and better-controlled study.

⚠️⚠️ **But read §80.5 before citing this in support of `TurtleBreakout`.** [A]'s "channel breakout" is **not**
plain Donchian — it carries a volatility-squeeze precondition our rule does not have, and the family that
actually matches our rule (their support-resistance class) finishes **last of five on all five markets.**

⚠️ **But the family that is MOST DIFFERENT from it is the oscillator class, and that class is 4th.** See
§80.14 — this is decision-relevant for the second-rule-class question, because "most different" and "most
profitable" point at different families here.

### §80.2 ⭐⭐ [A] ALL of the profit comes from BUY signals — the sell side contributes nothing or less `CompliancePolicy`, `strategy/rules/`

The single most important number in [A] for this project. Table 2 decomposes each class's return into the
average daily return following a **buy** signal and following a **sell** signal:

| Market | Channel breakout: Ave BUY Return | Ave SELL Return |
|---|---:|---:|
| CoinDesk | **+0.54%\*\*\*** | −0.01% |
| Bitstamp | **+0.42%\*\*\*** | −0.01% |
| Litecoin | **+0.47%\*\*\*** | +0.01% |
| Ripple | **+0.63%\*\*\*** | +0.01% |
| Ethereum | **+0.85%\*\*\*** | −0.02% |

[A]'s own summary: *"the average return from a buy signal is positive and statistically significant while the
average return from a sell signal is mostly negative indicating that the positive returns from technical
trading in cryptocurrencies comes from the buy signals rather than the sell signals."*

Table 4 quantifies how lopsided this is: **100.00%** of channel-breakout parameterizations produce positive
buy returns on CoinDesk, Bitstamp and Ripple (99.45%/99.77% on Litecoin/Ethereum), and **98.66% / 97.26% /
94.84% / 98.60% / 94.88%** are *significantly* positive. On the sell side, **the proportion of significantly
positive sell returns is 0.00%–0.70%** across all twenty market×class cells.

⇒ **The halal long-only constraint costs NOTHING here — this is now the FOURTH independent instance**, and
by far the most direct measurement. It joins **§74.6** ([A-of-74]'s long/short and long/out strategies
producing *identical* breakout numbers), **§58.3** (long-only *improved* the tested breakout in both samples)
and **§73**'s `Side ∈ {−1,+1}` collapsing to `{+1}` and halving `N`. Previous instances showed the short leg
adds nothing; **this one shows the short leg is where the losses live** — the sell signals are not merely
uninformative, their returns are on average negative in three of five markets while buy returns are strongly
positive. Discarding them is an improvement, not a sacrifice.

### §80.3 ⭐ [A] Breakeven transaction costs are 30–144bp for channel breakout — well above our real ~50–60bp `strategy/backtest.py`

[A] Table 6 reports, per class, the average number of new trades, the **breakeven transaction cost in basis
points** (the level at which the rule's profit is exactly zero), and the **percentage of rules whose breakeven
TC exceeds 50bp** (their stated benchmark for real Bitcoin costs, from Lintilhac & Tourin 2017):

| Market | Channel breakout: no. trades | Breakeven TC | % of rules > 50bp |
|---|---:|---:|---:|
| CoinDesk | 274.25 | **61.00 bp** | 30.18% |
| Bitstamp | 217.57 | **54.12 bp** | 27.38% |
| Litecoin | 214.77 | 30.42 bp | 17.97% |
| Ripple | 219.22 | 33.06 bp | 18.37% |
| Ethereum | 93.53 | **144.03 bp** | 47.35% |

⚠️ **The brief's expectation that breakeven costs are "well above real crypto costs" is TRUE for Bitcoin and
Ethereum and MARGINAL for Litecoin and Ripple.** The honest reading: on our two actual trend assets the
*average* channel-breakout rule clears a 50bp round-trip with room to spare (61bp, 54bp, 144bp), but on the
alts the average rule does **not** (30bp, 33bp). And note the tail — only 18–47% of parameterizations clear
50bp, so **the average clearing the bar does not mean an arbitrary parameterization will.**

⚠️ Contrast with **support-resistance: 7.88–16.16 bp breakeven, and 0.00%–3.49% of rules clear 50bp.** That
family is wiped out by realistic costs on every market. See §80.5 — this matters more than it looks.

### §80.4 ⚠️⚠️ [A] THE BEST IN-SAMPLE RULE ON BOTH BITCOIN MARKETS WAS A CHANNEL BREAKOUT — AND IT WENT NEGATIVE OUT OF SAMPLE ON BOTH `strategy/promotion.py`

This is the finding in this source that most deserves to hurt. [A] Table 9 takes the single best in-sample
rule per market (data to 31 Dec 2017) and runs it, untouched, through the first six months of 2018:

| Market | Best in-sample rule | OOS Ann. Return | OOS Ann. Sharpe | OOS Ann. Sortino |
|---|---|---:|---:|---:|
| **CoinDesk (BTC)** | **CB2: 25/0.05/0.025/5** | **−0.0010** | **−0.0502** | **−0.3470** |
| **Bitstamp (BTC)** | **CB2: 25/0.05/0.025/5** | **−0.0091** | **−0.0641** | **−0.0553** |
| Litecoin | CB2: 25/0.05/0.025/5 | +0.0775 | +1.3553 | +2.1900 |
| Ripple | MA1: 2/0.001/1 | +0.0546 | +0.7380 | +1.2162 |
| Ethereum | MA4: 2/25/0/1/5 | +0.0631 | +1.1900 | +1.8500 |

[A]'s own abstract concedes it: *"there is no predictability for Bitcoin in the out-of-sample period,
although predictability remains in other cryptocurrencies."* Their explanation is a liquidity/attention
story: *"Bitcoin was the first cryptocurrency created and was the most liquid and therefore attracts more
attention from investors… means that profitable trading strategies may become more difficult to find as the
market becomes more efficient."*

⚠️ **Three things this does to the KB:**

1. **It is a direct, crypto-specific, breakout-specific instance of §74.11's edge-decay warning** — and it is
   sharper than §74.11 because it is not a survey of efficiency tests, it is *our own rule family* failing on
   *Bitcoin specifically* in an honest OOS window. §74.11 said the market is measurably maturing; §80.4 shows
   what that maturation did to the winning rule.
2. **It is a textbook §73.1 result.** The best-of-14,919 in-sample rule failing OOS is precisely the
   `E[max_N]` phenomenon: at N = 14,919 the expected best-of-sweep Sharpe under a *zero-edge* null is very
   large, so the in-sample winner is mostly selection. That the *same* CB2 parameterization won on three
   markets is mildly reassuring, but it still lost money on the two that matter to us.
3. **It does NOT refute §80.1.** §80.1 is the *average across all parameterizations* of the class, which is
   not a best-of-N statistic and therefore not subject to `E[max_N]`. §80.4 is the *best single rule*. The
   correct joint reading is: **the family has real, broad, cost-surviving edge in-sample; picking the
   in-sample-best member of it is exactly the mistake §73 warns against.** ⇒ Directly reinforces §54.10's
   **robustness-plateau over the peak** and §73.13(b)'s "report plateau width, treat a narrow peak as
   disqualifying."

⇒ **Action:** when the `donchian_entry_n` sweep demanded by §74.2 is run, **it must be reported as a plateau
and logged against the §73.6 trials ledger, and its OOS arm must be treated as the primary result, not the
confirmation.** §80.4 is what happens otherwise.

### §80.5 ⚠️⭐⭐ [A]'s "channel breakout" is NOT plain Donchian — it carries a VOLATILITY-SQUEEZE PRECONDITION, and the plain-Donchian family is the WORST of the five `strategy/rules/`, `analysis/indicators.py`

**This is the most consequential mechanical finding in this source, and it partially corrects §74.1.**

[A]'s Appendix defines the two families precisely. The **support-resistance** rules are plain Donchian:

```
SR1: resistance = highest close of the previous j periods
     support    = lowest  close of the previous j periods
     if price rises at least x% above resistance and holds for d periods → go long
     j ∈ {2,5,10,15,20,25,50,100,200}   x ∈ {0,0.01,0.05,0.1,0.5,1,5}   d ∈ {1,2,3,4,5}
```

At `x = 0, d = 1` **SR1 is exactly `TurtleBreakout`'s entry** — a Donchian-extremum crossing, no filter.

The **channel breakout** rules add a precondition:

```
A c% trading channel EXISTS at time t iff
     high over the previous j periods  ≤  (1 + c) × low over the previous j periods
i.e. support and resistance have converged to within c% of each other.

CB1: IF a c% channel exists AND price moves at least x% above the channel's upper bound
     and remains so for d periods → go long
     j ∈ {2,5,10,25,50,75,100,150,200}   c ∈ {0.05,0.01,0.5,0.1,0.25,0.5}
     x ∈ {0,0.01,0.05,0.1,0.5,1,5}       d ∈ {2,3,4,5}   (k ∈ {3,5} for CB2)
```

**The `c%` condition is a volatility-contraction / squeeze filter.** The rule only arms when the recent range
has compressed; it then fires on the expansion out of that compression.

Now put that beside §80.1 and §80.3:

| | Ann. return (CoinDesk / Bitstamp) | Breakeven TC | % rules > 50bp |
|---|---|---|---|
| **Channel breakout** (squeeze-gated) | 10.11% / 7.89% | 61.0 / 54.1 bp | 30.2% / 27.4% |
| **Support-resistance** (plain Donchian) | **3.33% / 2.63%** | **11.4 / 11.9 bp** | **0.00% / 0.95%** |

**The squeeze precondition is associated with roughly 3× the return and 5× the breakeven cost tolerance, on
identical data, over the same period, with the same lookback grid.** Plain Donchian is the worst of five
families and is annihilated by realistic transaction costs on every one of the five markets.

⚠️⚠️ **What this does and does not mean.**

- It does **NOT** overturn §74.1. §74.1's source [A-of-74] (Gerritsen et al.) tested plain Donchian on BTC
  2010–2019 and found it the best family there, long-only, with bootstrapped significance. Two competent
  studies disagree on plain Donchian's standing. Different windows, different comparison sets, different
  benchmarks (buy-and-hold vs cash).
- It **DOES** mean the KB can no longer treat "channel breakout performed well in Hudson & Urquhart" as
  support for `TurtleBreakout` as currently specified. **Our rule is their support-resistance class, not
  their channel-breakout class.** Any future citation must be to SR, and SR is their worst result.
- It **DOES** supply the strongest external motivation the KB has for **§61.3's NR7 / volatility-contraction
  candidate**, which is currently logged only as an unvalidated "arm the Donchian watch after a squeeze"
  timing idea from a discretionary source. §80.5 is 14,919-rule, five-market, bootstrap-corrected,
  cost-audited evidence that **squeeze-gating a breakout is worth roughly 3× on crypto.** That promotes
  §61.3 from a speculative timing filter to the **highest-value single modification available to the
  existing rule** — and crucially it is a *modification of the validated rule*, not a new rule class, so it
  does not need to clear the independence bar of §80.16.

⇒ **Action: add a `channel_squeeze` precondition to the Turtle sweep** — `max(high, j) ≤ (1+c) · min(low, j)`
evaluated on the entry lookback, `c` swept over [A]'s a-priori grid `{0.01, 0.05, 0.1, 0.25, 0.5}`. Per
§73.12 the grid is **`a_priori`** (taken from published literature, not fitted here), so it costs little
against the §73.6 trials budget. Pure arithmetic on the existing OHLC candle store — **stdlib/Decimal
portable, no NumPy needed.**

⚠️ Caveat to log honestly: the squeeze filter will make the rule fire **less** often, which is the wrong
direction for the under-deployment defect (§75.1). It buys **quality**, not **frequency**. It is not the
second rule class, and it is not a knowability fix.

### §80.6 ⭐ [A] 20–50% of rules survive a full multiple-hypothesis correction — the correction that §74.13 said was missing `strategy/backtest.py`

**§74.13 flagged that neither of its empirical papers applied a full data-snooping correction across rule
families, so their p-values were "single-trial statistics over a small chosen grid."** [A] closes that gap.
Table 8, percentage of the 14,919 rules still significant at the 5% level after each procedure:

| Market | Bonferroni | Holm | **BH (FDR)** | BY (FDR) | None (bootstrap only) |
|---|---:|---:|---:|---:|---:|
| CoinDesk | 33.61% | 33.61% | **50.41%** | 33.61% | 57.65% |
| Bitstamp | 27.11% | 27.13% | **46.28%** | 28.56% | 58.64% |
| Litecoin | 23.26% | 23.26% | **31.84%** | 23.26% | 53.23% |
| Ripple | 20.35% | 20.35% | **27.96%** | 20.35% | 27.96% |
| Ethereum | 23.37% | 23.37% | **32.68%** | 23.37% | 32.68% |

Two things to record:

1. **A large fraction survives.** Even under Bonferroni — the most conservative correction available, applied
   across ~15,000 hypotheses — a fifth to a third of rules remain significant. **This is materially stronger
   evidence for crypto technical trading than anything previously in the KB**, and it is the first result
   here that is not vulnerable to §73.2's "could I have manufactured this by sweeping?" objection.
2. **BH (FDR) selects 1.2–1.5× as many rules as the FWER methods** — the exact power gain that [B] was
   written to argue for (§80.7). Note BY collapses back to Bonferroni levels; its `C_M = Σ 1/i ≈ log(M)+0.5`
   penalty is severe at M = 14,919.

⚠️ **But read §80.6 against §80.4.** Predictive power surviving correction **in-sample** did not prevent the
in-sample best rule from losing money **out-of-sample on both Bitcoin markets** six months later. Statistical
significance across a rule population and economic value to an investor picking one rule are different
claims — which is precisely [B]'s thesis.

### §80.7 ⭐⭐ [B] The False Discovery Rate procedure — select a surviving SET of rules, not the single best. FULLY STDLIB-PORTABLE `strategy/backtest.py`, `strategy/promotion.py`

**Why this matters to us.** Every data-snooping tool the KB currently carries (§73.2's MinBTL, White's
Reality Check as referenced in §74.13, §58.11's random-entry control) answers a question about **one** rule.
[B]'s framing of the limitation, verbatim: *"The BRC only indicates whether the rule that performs best in the
sample indeed beats the benchmark… It provides no information on the other strategies. In practice, investors
prefer not to base their investment decision on a single strategy."* And on why FWER methods fail: *"they do
not select further strategies once they find a rule whose performance is due to luck, even if there remain an
important number of true outperforming rules in the population."*

**The full procedure, with portability stated per step.**

**Step 1 — individual p-values via the stationary bootstrap** (Politis & Romano 1994; [B] Appendix A;
identical to the machinery [A] and [D] both use). Given the original return series `{f_t, t = L..T}`, generate
bootstrap series `{f^b_t}`:

```
θ(L) ~ Uniform{L..T};  f^b_L = f_{θ(L)}
for t = L+1..T:
    draw U ~ Uniform[0,1]
    if U <  q:  θ(t) ~ Uniform{L..T}          # start a new block
    if U >= q:  θ(t) = θ(t-1) + 1             # continue the block
                if θ(t) > T: θ(t) = L         # wrap
    f^b_t = f_{θ(t)}
```
`q = 0.1` ⇒ **average block length 10** ([B] follows STW; [A] and [D] both use the same value). `B = 1000`.
The p-value is obtained by comparing the original performance `φ` to the quantiles of `{φ^b − φ}`.
**Portability: `random.random()`, `random.randint()`, list indexing. Pure stdlib.**

**Step 2 — estimate `π₀`, the proportion of TRUE-NULL rules** (Storey 2002; [B] Appendix C, eq. 3). The
insight: under the null, two-sided p-values are Uniform[0,1]; alternatives cluster near zero. So the flat
right-hand tail of the p-value histogram estimates the null mass:

```
π̂₀(λ) = #{p_k > λ ; k = 1..l} / ( l · (1 − λ) )
```
[B] sets **λ = 0.6** by visual inspection of the histogram, noting *"π̂₀ is not sensitive to the choice of λ
when the number of rules is high"* and that Storey's automated λ-selection *"produces almost identical
estimates."* **Portability: one comparison and one division over a list of floats. Pure stdlib; exact under
`Decimal`.**

**Step 3 — estimate FDR⁺ at threshold γ** ([B] Appendix B, eq. 1). False discoveries under a two-sided test
split evenly between the positive and negative tails, hence the ½:

```
                    ½ · π̂₀ · l · γ
FDR⁺(γ)  =  ────────────────────────────────────
             #{ p_k ≤ γ  AND  φ_k > 0 ;  k = 1..l }
```
Numerator = expected false positives among rules called significantly *positive*; denominator = the count
actually called. **Portability: counting and arithmetic on a sorted list. Pure stdlib.**

**Step 4 — build the portfolio to a target FDR⁺** ([B] Appendix E). *"The algorithm starts with the rule
having the smallest p-value (and a positive performance). Then, the rule corresponding to the next p-value is
added and the FDR⁺ recomputed. This process is repeated until we reach the desired FDR⁺ target."*

```
sort surviving-positive rules by p-value ascending
S = []
for rule in sorted_rules:
    S.append(rule)
    if FDR_plus(p_value_of_last_added) > target:   # target = 0.10 in [B]
        S.pop(); break
return S
```
**[B]'s target: FDR⁺ = 10%, i.e. 90% of the selected set possesses genuine predictive ability.** They report
results *"qualitatively stable for values ranging from 5% to 20%."* **Portability: `sorted()` and a loop.
Pure stdlib.**

**Step 5 — pool the selected rules with EQUAL weight.** [B] §2.3: *"After pooling the signals of the selected
rules with equal weight, we invest a proportion of the wealth corresponding to the neutral signals in the risk
free rate, and go long or short the market with the remaining money."* Their worked example: 60 rules → 40
buy, 10 neutral, 10 sell ⇒ net 30 buy + 20 neutral ⇒ 60% invested. They note this *"is equivalent to
averaging the forecasts of the selected rules with equal weights and no prior"* and that weighting better
rules more heavily *"has an effect very similar to reducing the FDR target level to keep fewer rules."*

⛔ **HALAL ADAPTATION — mandatory rewrite of Step 5.** [B] pools to a signed net exposure that can go short,
and parks the neutral remainder at the risk-free rate. **Both are excluded.** Our version:

```
long_votes    = #{rules in S signalling BUY}
non_long      = #{rules in S signalling NEUTRAL or SELL}     # sell → NOT-A-SHORT, just "don't buy"
deployment    = long_votes / (long_votes + non_long)          # ∈ [0, 1], never negative
```
The remainder stays in **idle cash, earning nothing** — per §56.3, interest/rewards on idle balances must be
DISABLED at the account level, so there is no `r_f` leg to place. This is not a loss of fidelity: §80.2 shows
the sell signals carry no positive information anyway, so demoting them to "don't buy" discards nothing.
The resulting `deployment ∈ [0,1]` then feeds the existing fixed-fractional sizing, bounded by the hard rails.

⭐ **Composition with the trials ledger.** [B]'s `l` (the number of rules tested) is exactly §73.6's
`trials_attempted`. The FDR machinery is therefore not a parallel accounting system — **it consumes the
ledger §73 already requires us to keep.** And unlike §73.2's MinBTL, which currently fails for every rule we
have and must ship reporting-only, FDR yields an *actionable set* rather than a pass/fail gate.

⚠️ **The binding practical caveat: `l` must be LARGE.** [B] relies on `l → ∞` asymptotics (Storey 2003;
Farcomeni 2007) and their `l` is 7,846; [A]'s is 14,919. **`π̂₀(λ)` is a tail-density estimate and is
meaningless at `l = 5` or `l = 20`.** Our §73.3 trials budget is `N ≤ 3`. **These are not the same `N`** —
§73's `N` counts *selection decisions that consume statistical credibility*, while [B]'s `l` counts
*hypotheses simultaneously evaluated in one pass*. FDR is legitimate for the second and useless at our value
of the first. ⇒ **FDR is applicable to a broad single-pass sweep (e.g. the §74.2 `donchian_entry_n` × §80.5
`c` grid, hundreds of cells), NOT to the handful of head-to-head rule-class decisions the project actually
faces.** Record that boundary; do not import FDR as a general promotion gate.

### §80.8 ⭐⭐ [B] PERSISTENCE: an investor could NEVER have selected the future best rules ex ante — even WITH the more powerful FDR method `strategy/promotion.py`

[B]'s central negative result, and the strongest single argument in this source about **how the project
should promote rules**.

**The test design** (§5): *"Every month, we construct a portfolio of rules using price data of the previous
month. We then measure the out-of-sample performance of the selected rules over the following month… to
rebalance the portfolio, we use only information that would have been readily available to an investor."*
They emphasise that this is genuinely OOS in a way prior work was not: *"STW qualify as out-of-sample the
results for the period after the sample of the original BLL study. However, and despite the term, STW always
measure the performance in-sample."*

**The results:**

- *"the out-of-sample performance is negative in most cases throughout the recent periods. Even equipped with
  the more powerful FDR method, investors could not have reasonably anticipated which rules would generate
  positive returns, and this even in the unrealistic case of zero transaction costs. Hence, **there is no hot
  hands phenomenon.**"*
- **Portfolio turnover is near-total:** *"on average, less than five percent of the rules remain in the
  portfolio after the first rebalancing. After two rebalancings the portfolio consists of almost exclusively
  new rules."* The set of "good" rules is not stable month to month.
- **Transaction costs finish it:** *"even during the early periods, one-way transaction costs of less than 5
  to 35 basis points suffice to offset any out-of-sample performance."*
- **Robustness:** they tested whether *other* information could have helped — NBER business-cycle state,
  market environment — and found *"even knowing the state of the business cycle ex ante would not help an
  investor selecting the future outperforming rules."*
- **Their verdict on the entire prior literature:** *"The BLL results should be viewed as a statistical
  anomaly, discovered ex post by extensive data snooping. In any case, they should not be viewed as an
  episode of market inefficiency, as the hypothetical predictability could not have been exploited."*

⚠️⚠️ **What this obliges the project to change.**

1. **A rule that tests well is not thereby a rule we can select.** [B] separates two claims the KB has been
   treating as one: *"does this rule have predictive power?"* (§80.6: often yes) and *"could I have known to
   pick it in advance?"* (§80.8: no). **Our promotion gate currently only tests the first.** This is the same
   distinction §73.11 drew between §58.11's null-over-the-data and §73.2's null-over-the-search — §80.8 adds
   a **third**: a null over the *selection procedure itself, run forward in time*.
2. **The concrete implementable version for us is a rolling ex-ante re-selection audit.** Periodically
   re-run the selection using only data available at that date, then measure the selected configuration's
   performance over the *following* window. If our chosen `donchian_entry_n` / `c` / `adx_threshold` would
   **not** have been selected at earlier dates, or if the selection churns like [B]'s 95%-per-month turnover,
   the parameters are noise-fitted regardless of what the full-sample backtest says. **This is cheap** — it
   reuses the existing walk-forward harness and adds only the record of *which config would have been picked
   when*.
3. ⭐ **It supplies the affirmative argument for the KB's own `a_priori` doctrine (§73.12).** If ex-ante
   selection from price data is impossible, then a parameter's provenance from **prior published literature**
   is not merely a trials-budget economy — it is the *only* selection channel [B] did not refute. Everything
   [B] tested was selection from *own past performance*, and all of it failed. **§73.12's `a_priori` vs
   `fitted` distinction is now empirically load-bearing, not just bookkeeping.**
4. ⚠️ **Scope limit, stated by the authors, that must be recorded honestly:** *"our results say little about
   the existence of profitable trading strategies in other markets, using different frequencies or more
   sophisticated rules."* This is DJIA, 1897–2011, blue-chip index, the STW rule universe. Crypto 2021–2026
   is not that market. §80.8 is a strong caution about *method*; it is not a demonstration that our rule
   fails.

### §80.9 ⚠️ [B] Transaction costs must be ENDOGENOUS to rule selection, not applied afterwards `strategy/backtest.py`

A methodological point that the KB does not currently implement and that [B] makes crisply:

*"Individual break-even transaction costs are informative. However it is difficult to use break-even costs in
a rules selection process because they are computed ex post, once the trading rules have already been
selected… **Trading rules that survive the inclusion of transaction costs are often not among those that
perform best before costs. Transaction costs must be treated as endogenous and not exogenous to the selection
process.**"*

Their demonstration (§4, Table 3): in sample period 3 (1939–1962), *"if we omit transaction costs, the best
rule in the sample uses a window of only two days of data. When transaction costs are taken into account, the
best rule needs 250 days, or 12 months of data."* **A 125× shift in the optimal lookback, caused purely by
where costs are applied.**

⇒ **Direct action on our sweep:** every candidate configuration must be scored **net of the modelled
Coinbase fee + slippage inside the objective**, never ranked gross and cost-checked afterwards. If our sweep
currently ranks on gross P&L and then filters, §80.9 says it is systematically selecting the wrong lookback —
and biasing it **short**, which is the exact direction §74.2 says we are already wrong in. **These two
findings compound: a gross-ranked sweep would help explain why our fitted `entry_lookback` keeps landing
short of every external source's recommendation.**

⚠️ Note [A] and [B] disagree in tone about whether costs kill crypto technical trading — [A] finds breakeven
TCs comfortably above 50bp for BTC/ETH channel breakout (§80.3), [B] finds 5–35bp sufficient to erase DJIA
performance. Different markets, different eras, different rule densities. **[A] is the relevant one for us;
[B]'s contribution here is the procedural point, not the number.**

### §80.10 ⭐⭐ [C] Weekly time-series momentum in Bitcoin — significant at 1–3 weeks (NOT 4), the actual signal definition `strategy/rules/`

The candidate second rule class, mechanically specified.

**The signal is the SIGN AND SIZE OF THE ASSET'S OWN PAST RETURN.** No indicator, no crossing, no
extremum — a continuous predictive regression of next-period return on this-period return. [C] Table 14:

| Bitcoin, WEEKLY | R_{t+1} | R_{t+2} | R_{t+3} | R_{t+4} |
|---|---:|---:|---:|---:|
| coefficient on R_t | **0.19\*\*\*** | **0.22\*\*\*** | **0.21\*\*\*** | 0.09\* |
| t-stat (Newey–West) | (3.73) | (4.52) | (4.26) | (1.72) |
| t-stat (bootstrapped) | [2.17] | [2.73] | [2.47] | [1.40] |
| R² | 0.03 | 0.05 | 0.05 | 0.01 |

[C]'s own gloss: *"A one-standard-deviation increase in this week's return leads to increases in weekly
returns of 3.16 percent, 3.66 percent, 3.49 percent, and 1.50 percent at the 1-week, 2-week, 3-week, and
4-week ahead returns."* (Weekly SD is 16.64%, so 16.64 × 0.19 = 3.16.)

**Horizon boundary: 1–3 weeks is solid on both t-stats; week 4 is significant only on the Newey–West t and
NOT on the bootstrapped t [1.40].** Record the usable horizon as **1–3 weeks**, not the brief's "1–4."

The **quintile** form is the tradeable one ([C] Table 15, sorting weekly returns into quintiles by formation
return and measuring the following weeks):

| Quintile | Formation return | R_{t+1} | Sharpe | R_{t+3} | Sharpe |
|---|---:|---:|---:|---:|---:|
| 5 (highest) | +27.44% | **11.22%\*\*\*** | **0.45** | **10.07%\*\*\*** | **0.43** |
| 4 | +7.59% | 3.75%\* | 0.25 | 3.04%\* | 0.21 |
| 3 | +1.84% | 1.15% | 0.10 | 3.62%\*\* | 0.26 |
| 2 | −2.56% | 0.27% | 0.02 | 2.40% | 0.15 |
| 1 (lowest) | −14.95% | 2.60%\* | 0.19 | −0.50% | −0.04 |

**Daily** momentum is much weaker and choppier (Table 14 Panel A: R_{t+1} 0.06\*\*\* but bootstrapped t only
[1.22]; R_{t+5} and R_{t+6} significant, R_{t+2} and R_{t+7} negative). ⇒ **Use the WEEKLY horizon. The daily
signal is not reliable enough to build on, and its sign flips across horizons.**

⚠️⚠️ **ETHEREUM IS THE PROBLEM.** [C] Table 18 Panel B, Ethereum daily: R_{t+1} 0.08\*\* but bootstrapped
t only [1.67] (not significant), and **R_{t+5} is −0.08\*\*, significantly NEGATIVE.** [C] state plainly:
*"for Ethereum, the momentum effect is less significant than for Bitcoin and Ripple."* Ripple is fine at the
1–5 day horizon; **Ethereum, one of our three allowlisted assets, shows weak-to-reversing momentum.** They
report no weekly-horizon table for Ethereum, so the weekly result cannot be assumed to transfer. ⇒ **Any
build must treat ETH as an open question, not a covered case.** This is a real limitation given §58.4's
"trade the whole admitted basket" doctrine.

### §80.11 ⭐⭐ [C] The LONG-ONLY half of that momentum signal is the half that works — the short leg would have LOST money `CompliancePolicy`, `strategy/rules/`

⛔ **What is excluded by mandate first.** [C] Table 15's **"Difference" row** (quintile 5 minus quintile 1:
8.62 at R_{t+1}, 11.28 at R_{t+2}, 10.57 at R_{t+3}, 5.18 at R_{t+4}) is a **long-short spread portfolio**
and is **EXCLUDED**. The related cross-sectional momentum literature [C] cites (Stoffels 2017, a
15-cryptocurrency cross-sectional long-short strategy) is likewise **EXCLUDED** and was not extracted.

**What survives is stronger than what was dropped.** Look at what the quintile tables actually show:

- **Quintile 5 alone is where the entire effect lives.** 11.22% (Sharpe 0.45) at R_{t+1}, 10.07% (Sharpe
  0.43) at R_{t+3}. That is a **pure long-only signal on a single asset's own past return** — buy Bitcoin
  after a strongly positive week, hold 1–3 weeks.
- **The bottom quintile's return is POSITIVE, not negative** (+2.60% at R_{t+1}). A short leg on quintile 1
  would have been shorting into a **positive 2.60%/week** — a loss. **The long-short "Difference" that the
  mandate forbids us is worth *less* than the long-only leg on its own** (8.62 vs 11.22), because the short
  side is a drag. ⇒ **FIFTH independent instance that the long-only constraint costs nothing**, joining
  §80.2, §74.6, §58.3 and §73's `Side` collapse. Here the constraint is not merely free — **obeying it is
  strictly better than the paper's headline spread.**

⭐ **[C] Table 17 is the honest test, and the long-only leg survives it.** Table 17 ("No Lookahead") uses
**only the first two years of data to fix the quintile cutoffs**, then applies them going forward — removing
the full-sample lookahead that contaminates Tables 15/16:

| Quintile | Formation | R_{t+1} | Sharpe | R_{t+3} | Sharpe |
|---|---:|---:|---:|---:|---:|
| **5** | +24.14% | **7.88%\*\*** | **0.37** | **7.53%\*\*\*** | **0.41** |
| 1 | −14.97% | 3.35%\* | 0.25 | 1.37% | 0.12 |

The top-quintile effect **shrinks but holds** (11.22 → 7.88, Sharpe 0.45 → 0.37), and the bottom quintile is
**still positive**. [C]: *"we use the first two years of data to determine the quintile cutoffs and study the
out-of-sample momentum performance, and we find strong momentum effect as well."* Table 16 (restricted to
2013 onward, i.e. dropping the wildest early data) also holds: quintile 5 = 7.18%\*\* Sharpe 0.34.

**The long-only implementable form:**
```
each week-end:  form = weekly return over the trailing 7 days
                if form >= cutoff_q5:   eligible to hold for the next 1–3 weeks
                else:                   do not buy   (NEVER short)
cutoff_q5 = 80th percentile of trailing weekly returns, fixed from a
            leading window and held constant (Table 17 method, not full-sample)
```
**Portability: a rolling percentile of a list of weekly returns. `sorted()` + index. Pure stdlib/Decimal.**

⚠️ **Costs are not modelled.** [C] is an asset-pricing paper; the quintile returns are gross. A weekly-formed,
1–3-week-held signal turns over far faster than the Turtle's ~24-day hold, so §80.9's endogenous-cost rule
binds hard here. Quintile 5's 7.88%/week gross has ample room over a ~50–60bp round trip, but that must be
verified in our own harness, not assumed.

### §80.12 ⭐ [C] Crypto has NO exposure to equity, currency, commodity or macro factors — the second-rule-class case rests on this `analysis/regime.py`

[C]'s systematic negative result, and the reason its momentum finding is credible as a *distinct* risk source
rather than a repackaged equity factor:

- **Equity factors:** CAPM betas are *"sizable but the alphas remain large and statistically significant."*
  Bitcoin's alpha is **22–24% per month** and survives FF3/FF4/FF5/FF6. Exposures to SMB, HML, MOM, RMW, CMA
  are *"low and not statistically significant"* for Bitcoin (Ethereum shows a significant *negative* HML
  loading, −10.25 to −16.05, i.e. it comoves with growth rather than value). R² ranges 0.01–0.39.
- **The factor zoo:** of **155 documented anomaly factors**, *"only four out of the 155 factors are
  significant, but those four factors do not form any discernible patterns."* At α=0.05, ~7.75 significant
  hits are expected by chance from 155 tests — **4 is BELOW the chance rate.** This is a clean null.
- **Currencies:** AUD, CAD, EUR, SGD, GBP — *"the exposures of all cryptocurrencies to these currencies are
  not statistically significant."* Also null against the Lustig–Roussanov–Verdelhan DOLLAR and CARRY factors.
  R² 0.00–0.06.
- **Commodities:** gold, platinum, silver — *"with the exception of the exposure of Ethereum to gold, the
  exposures of all other cryptocurrencies to these commodities are not statistically significant."*
- **Macro:** non-durable consumption, durable consumption, industrial production, personal income — all
  insignificant for Bitcoin and Ripple; Ethereum loads on durable consumption growth.

⭐ **The one exposure that DID show up is directly relevant to our allowlist: Ethereum ↔ gold, +5.45\*
(t = 1.77, bootstrapped [1.83]), R² 0.10.** Our allowlist is BTC/ETH/**PAXG**, and PAXG *is* gold. This is
weak (10% level) and single-specification, so it is **not** grounds to change the correlation rail — but it
is a concrete hypothesis worth checking against the §54.22 **rolling 60-day correlation** the rail already
computes. Log it as a check, not a finding.

### §80.13 ⚠️ [C]'s statistical treatment is NOT multiple-testing corrected — a real limitation under §73 `strategy/backtest.py`

Stated plainly so the KB does not over-weight §80.10/§80.11. [C] reports Newey–West t-statistics in
parentheses and bootstrapped t-statistics in brackets — **per-hypothesis inference only.** There is **no
Reality Check, no SPA, no FWER control, no FDR** anywhere in the paper. This is normal for an asset-pricing
paper and abnormal for a trading-rule paper, and it is the standard the rest of this source is held to:

- [A] applies a stationary bootstrap **plus four MHT corrections** across 14,919 rules (§80.6).
- [B] applies a stationary bootstrap **plus FDR** across 7,846 rules, **plus** a forward-looking persistence
  test (§80.8).
- [D] applies a stationary bootstrap **plus Reality Check plus stepwise SPA**, repeated 500 times (§80.14).
- **[C] applies neither.**

Per **§73.2**, [C]'s p-values are single-trial statistics. **Mitigating factors, in [C]'s favour:** the
hypothesis space is genuinely small (a handful of horizons, not a parameter mesh); the effect appears at
multiple adjacent horizons rather than at one isolated cell, which is the plateau signature §54.10 asks for
and the opposite of §73.8's lone-spike pathology; and **Table 17's no-lookahead cutoff construction is a real
if partial OOS control**. **Net: treat the weekly momentum result as a well-supported hypothesis worth
testing in-house, NOT as a validated rule.** It has not earned the standing §80.1 has.

### §80.14 ⚠️⚠️⭐⭐ THE ADJUDICATION: §74.5's significant MACD vs [D]'s failed MACD `strategy/rules/`

**The conflict.** §74.5 records, from Gerritsen et al.'s long-only column on Bitcoin 2010–2019:
MACD **+2.96 bp vs B&H (p=0.02)**, MACD_SIGNAL **+2.80 (p=0.02)**, MACD_HIST **+3.57 (p=0.01)** — all
significant, all positive. On that basis the KB promoted §58.10c's `macd_divergence` to leading
second-rule-class candidate. [D] reportedly finds the opposite.

**[D]'s actual MACD numbers**, read directly from Tables 6 and 11:

| | BTC/USDT (Table 6) | ETH/USDT (Table 11) |
|---|---|---|
| Best MACD | macd(20, 25, 6, 30, 10) | macd(20, 35, 6, 30, 10) |
| **In-sample ann. return** | 34.103% | **33.508%** |
| **In-sample Sharpe** | 0.574 | **0.462** |
| Max drawdown | 76.51% | — |
| **p (nominal)** | **0.116** | — |
| **p (Reality Check)** | **0.281** | — |
| **p (stepwise SPA)** | **0.295** | — |
| OOS ann. return | 4.898% | — |
| OOS Sharpe | 0.128 | — |
| p (OOS, all three) | 0.171 / 0.912 / 0.713 | — |
| **Profitable MACD strategies, IS (500 tests)** | **0.000 (0.0%)** | — |
| **Profitable MACD strategies, OOS (500 tests)** | **0.000 (0.0%)** | — |

**⚠️ The single most important detail, which the brief's framing understates: [D]'s MACD failed at the
NOMINAL level (p = 0.116). It was never significant to begin with.** The brief describes it as "profitable
pre-Dec-2021, FAILED out-of-sample once snooping-adjusted." That is the correct description of [D]'s **EMAC**
result (IS 116.017%, Sharpe 1.341, RC p = 0.026 ⇒ genuinely significant in-sample, then OOS 3.593% with
p = 0.767 ⇒ killed). It is **not** what happened to MACD. MACD never cleared any bar — not nominal, not
Reality Check, not stepwise, not in-sample, not out-of-sample, on neither asset. **[D]'s MACD result is
weaker than "died out-of-sample"; it is "was never alive."**

**Why the two studies disagree — the four axes:**

| | §74.5 (Gerritsen et al.) | [D] (Chen et al.) |
|---|---|---|
| **Period** | Jul 2010 – Jan 2019 | Aug 2017 – Oct 2023 (IS to Dec 2021) |
| **Assets** | BTC only | BTC/USDT **and** ETH/USDT |
| **Benchmark** | vs **buy-and-hold**, in basis points | vs **zero / cash**, annualized return & Sharpe |
| **Correction** | Ledoit–Wolf bootstrapped Sharpe difference, 1,000 sims, **NO cross-rule MHT** (§74.13) | Stationary bootstrap + **White's Reality Check + stepwise SPA**, ×500 repetitions |
| **OOS** | **None** — full sample in-sample | **Yes** — genuine 22-month forward window |
| **Costs** | not stated in the sections read | slippage 0.001 + commission 0.0003, modelled |
| **Direction** | long-only column extracted | **long-only by construction** — *"Fractional trading and short selling are not permitted"* |
| **Venue** | peer-reviewed finance journal | ⚠️ **SciEP, low-tier** |

**⭐ MY VERDICT: the conflict is REAL, it RESOLVES AGAINST §74.5, and the MACD family should be DEMOTED from
leading second-rule-class candidate to unproven — but not closed.**

The reasoning, and where I hold it loosely:

1. **The periods barely overlap, and the non-overlapping part is ours.** §74.5's sample is 2010–2019; [D]'s
   in-sample begins Aug 2017 and its out-of-sample is Dec 2021 – Oct 2023. **Our validation window is
   2021–2026.** [D]'s OOS window is the only evidence in this KB that sits inside the era we actually trade.
   §74.11 already established that crypto is measurably becoming more efficient; §80.4 showed the same rot
   killing the best breakout rule on Bitcoin between 2017 and 2018. **An indicator that worked 2010–2019 and
   does not work 2017–2023 is the exact signature §74.11 predicts.** The disagreement is not a contradiction
   — it is a **time series**.
2. **[D] is methodologically stronger on precisely the axis that matters here.** §74.13 recorded that §74's
   papers *"do not apply a full data-snooping correction across the rule families tested (White's Reality
   Check / Hansen's SPA), so per §73.2 their p-values are still single-trial statistics."* [D] applies
   exactly the correction §74.13 said was missing, **and adds a real OOS split.** When a weaker-method study
   and a stronger-method study disagree, and the stronger one is also more recent and closer to our trading
   era, the stronger one governs.
3. **The venue caveat cuts less than it appears to.** [D] is being used to **reject** a hypothesis, not to
   establish one. The asymmetry matters: a low-tier venue is a reason to distrust a *positive* claim (weak
   review lets false positives through), and a much weaker reason to distrust a *null* (nobody publishes in a
   questionable venue to report that a strategy did not work). **[D] is negative evidence, which is the
   direction its venue weakness least contaminates.** Its numbers are internally consistent, its method is
   correctly described, and its result agrees with [A]'s §80.4 and [B]'s §80.8 on the general shape.
   Nonetheless: **it is never cited alone. Every §80.14 conclusion must be stated as jointly resting on [D]
   AND on §80.4/§80.8/§74.11, all of which point the same way.**
4. **⚠️ Where the verdict is genuinely weak, stated honestly.** §74.5 and [D] **do not test the same rule.**
   §74.5 tested MACD as a standalone entry (crossover of MACD/SIGNAL/HIST above zero). [D] tested
   `macd(fast, slow, signal, sma=30, direction=10)` — a parameterized crossover with a holding-period
   modifier. **Neither tested §58.10c's `macd_divergence`**, which is what the KB actually proposes to build:
   *bullish divergence between price lows and MACD-histogram lows*, with five specified conditions including
   a recency gate and turn confirmation, refined by §55.1's ordinal strength ladder and §55.2's slope /
   centre-line discriminators. **Divergence is a different construct from crossover** — it fires on a
   *disagreement* between price and oscillator, not on the oscillator's level or sign. So strictly:
   **the KB has evidence for MACD-crossover (§74.5, positive, older, weaker method) and against MACD-crossover
   ([D], negative, newer, stronger method), and NO evidence either way on MACD-divergence.**

**What the KB should do, concretely:**

- ⬇️ **Demote `macd_divergence` from "leading candidate" to "unproven candidate."** Remove the §74.5
  entitlement. The `strategy/rules/` module-map claim that "§74.5 the MACD family IS significant on Bitcoin
  ⇒ strengthens §58.10c" must be qualified with §80.14.
- ⛔ **Do NOT build MACD-crossover as a second rule class.** On this the two studies plus §80.4's decay
  narrative are sufficient. That variant is closed.
- ❓ **`macd_divergence` remains open but now has to earn its place on its own**, with no external
  corroboration behind it. Under §73.6 it must be budgeted as a *fitted* hypothesis costing real trials, not
  imported as `a_priori` on §74.5's authority.
- ⬆️ **Promote [C]'s weekly time-series momentum (§80.10/§80.11) ahead of it** as the leading second-rule-class
  candidate. It is peer-reviewed at the top tier (RFS), its long-only leg is the strong leg (§80.11), it
  survives a no-lookahead cutoff construction, and — decisively for the actual problem — see §80.16.

**⚠️ What would SETTLE this, since the honest answer includes "not fully resolved":**
1. **A MACD study on 2021–2026 crypto with a snooping correction.** [D]'s OOS ends Oct 2023; our window runs
   to 2026. Nothing in this KB covers 2024–2026.
2. **A test of MACD *divergence* specifically** — every study to date tests crossovers. Until one exists,
   §58.10c is extrapolation from a related-but-different construct in both directions.
3. **Cheapest and fully within our control: run §58.11's random-entry control on `macd_divergence` in our own
   harness before building it as a rule.** §73.3 showed the Turtle needs ~68 trades to clear z≥2; if
   `macd_divergence` fires often enough to reach that count in our window, it settles the question on *our*
   data in *our* era — and if it does not fire that often, it fails the knowability test that motivated the
   whole search, and the answer is the same either way. **This is the decisive experiment and it does not
   require another paper.**

### §80.15 [D] EMAC, RSI and Bollinger also fail out-of-sample — and [D] is LONG-ONLY BY CONSTRUCTION `strategy/rules/`

[D]'s other three families, recorded for completeness and because they corroborate existing KB positions:

| Strategy | Best IS | IS ann. ret | IS Sharpe | RC p | OOS ann. ret | OOS RC p | Verdict |
|---|---|---:|---:|---:|---:|---:|---|
| **EMAC** (BTC) | emac(18, 47) | 116.017% | 1.341 | **0.026** ✓ | **3.593%** | 0.767 ✗ | significant IS, **dead OOS** |
| EMAC (ETH) | emac(10, 35) | 143.769% | 1.189 | — | **−9.41%** | ✗ | **negative OOS** |
| **RSI** (BTC) | rsi(14, 86, 32) | 66.816% | 0.774 | 0.155 ✗ | 19.367% | 0.972 ✗ | never significant |
| **Bollinger** (BTC) | bbands(21, 3) | 88.059% | 1.519 | 0.055 ✗ | 12.826% | 0.823 ✗ | never significant |
| Bollinger (ETH) | bbands(21, 2) | 7.998% | 0.12 | ✗ | 14.978% | ✗ | *"unsatisfactory… in both"* |

Three cross-references land:

- ⭐ **RSI: a FOURTH independent refutation.** §74.3 declared RSI mean-reversion *"settled and closed"* on
  three sources (our own sim; §58.10a; §74.3). [D] adds a fourth on 2017–2023 crypto with a reality check:
  never significant, in-sample or out. **The closure holds; nothing to revisit.**
- ⭐ **Bollinger: corroborates §74.4.** §74.4 recorded Bollinger significantly negative on Bitcoin and used it
  to demote §54.15's ATR/stdev volatility-band candidate. [D] finds Bollinger never significant on either
  BTC or ETH, and on ETH *"unsatisfactory in both the in-sample and out-of-sample periods."* **The §54.15
  demotion stands.**
- ⚠️ **EMAC is the cautionary one, and it is the pattern §80.4 and §80.8 both describe:** genuinely
  significant in-sample after a Reality Check (p = 0.026), then 3.593% (BTC) and −9.41% (ETH) out-of-sample.
  [D]: *"If financiers adopt the optimal technique based on the in-sample period, they may generate profits
  in the out-of-sample period, but these profits are likely attributable to chance."* **This is the third
  independent instance in this source alone of "passed the snooping correction in-sample, died forward."**

⭐ **Recording [D]'s directional constraint, which is unusually valuable:** §7 states *"Fractional trading and
short selling are not permitted."* **[D] is long-only by construction**, like §74.8's [B-of-74] and like us.
Every number above is therefore a long-only number requiring no adaptation. Given how rarely the literature
tests our actual constraint, this is worth noting even though the result is negative — **a negative result
measured under our exact constraint is more informative than a positive one that has to be adapted.**

### §80.16 ⚠️⚠️⭐⭐ THE KNOWN HOLE — rule-class independence CANNOT be cited. It MUST be measured in-house. `strategy/rules/`, `sim/metrics.py`

**Direct answer to the question this batch was assembled to settle.**

**What was checked.** Grepped the KB for `false discovery`, `storey`, `bajgrowicz`, `tsyvinski`,
`time-series momentum` / `time series momentum`, `stationary bootstrap`, `benjamini`, `breakeven transaction`,
`Newey`, `Calmar` — **all returned NONE** (`urquhart` returned only source-74). Then read all four papers in
this source in full for any measurement of **signal overlap, trade-timing correlation, or return correlation
BETWEEN rule families**. Findings:

- **[A]** is the closest proxy that exists — five families, identical data, identical periods, side by side
  (§80.1). **But it reports each family's returns SEPARATELY and never once correlates them.** No correlation
  matrix, no signal-overlap statistic, no joint-portfolio construction. It measures whether each family
  works; it never asks whether two of them work *at the same times*.
- **[B]** comes closest to touching the question and then explicitly steps around it. Its FDR method *requires*
  reasoning about dependence, and §2.2 describes the structure: *"the trading rules behave dependently in
  small groups, with each group being essentially independent of the others. For example, a 2-day moving
  average rule with a 0.01 band is highly correlated to a 2-day moving average rule with a 0.015 band.
  However, the performance of a 200-day moving average rule is going to be very different, let alone a filter
  or a support and resistance rule. Such form of dependence is called **block dependence**."* **That is a
  qualitative assertion made to justify a weak-dependence assumption — not a measurement.** [B] never
  reports a between-family correlation. It only needs the dependence to be *local enough* for asymptotics;
  it does not need, and does not produce, its magnitude.
- **[C]** measures crypto against **external** factor sets (equities, currencies, commodities, macro,
  155 anomalies — §80.12) and finds near-zero exposure. It never measures its momentum signal against any
  technical rule.
- **[D]** tests four families independently and never relates them.

⇒ **CONCLUSION: rule-class independence is NOT citable. Not from these four papers, not from anything already
in the KB. It must be MEASURED IN-HOUSE. This is a BUILD task, not a SOURCING task.**

⚠️ **The KB already anticipated this and the anticipation is now confirmed.** §74.12 warned, on theoretical
grounds from Zakamulin & Giner, that MOM and MA rules are *not* independent families and that their
*"similarity… increases with increasing trend strength"* — i.e. they converge exactly when our rule fires —
and concluded *"a second rule class must be demonstrated uncorrelated, not assumed."* **§80.16 upgrades that
from a caution to a finding: four papers spanning 30,000+ tested rules across two asset classes and 114 years
of data contain no measurement of between-family signal correlation on any market, let alone crypto.**
Nobody has done it. There is no paper to find.

**⭐ The measurement to build — cheap, and it reuses what already exists.** The project already has, per
§58.11's implementation, the machinery to generate trade sequences and compare them against a null. The
independence measurement is strictly simpler:

```
For rule classes A and B over the same asset and window:
  1. Generate each rule's daily POSITION vector  s_t ∈ {0, 1}   (long-only ⇒ binary, not {−1,0,1})
  2. Overlap        = #{t : s^A_t = 1 AND s^B_t = 1} / #{t : s^A_t = 1 OR s^B_t = 1}   (Jaccard)
  3. Signal corr    = Pearson correlation of the two position vectors
  4. Trade-timing   = for each A-entry, distance in bars to nearest B-entry; report the distribution
  5. Return corr    = correlation of the two rules' daily P&L series
```
**Portability: counting, means, and one covariance over two equal-length lists. Pure stdlib arithmetic;
exact under `Decimal`. No NumPy, no Pandas, no SciPy.**

⚠️ **§73.5 makes this non-optional, not merely nice to have.** Correlated rules inflate `N` without adding
independent evidence — two rules that fire together are one rule that has been counted twice, and it enters
the trials budget twice while contributing one observation's worth of information. Given §73.3's finding that
`N ≤ 3` on our 5-year window, **adding a second rule class that turns out to be correlated is strictly worse
than adding nothing**: it consumes budget, inflates apparent trade count, and leaves knowability exactly
where it was. The §80.16 measurement is what stands between the project and that outcome.

⭐ **The economic prior favours [C]'s momentum over `macd_divergence`, but the prior is not the measurement.**
The Turtle entry is a **40-day Donchian extremum-crossing EVENT** — a discrete, rare, threshold-triggered
condition on the tail of the price distribution. [C]'s signal is a **continuous weekly return-sign / quintile
rank** — a dense, always-defined condition on the centre of the distribution, at a ~5–7× shorter horizon.
Those are structurally different objects, which is the strongest a-priori case in this batch. **But §74.12's
whole point is that structural difference did not prevent MOM and MA from converging when trends are
strong**, and a 40-day breakout and a top-quintile weekly return will *both* fire in a strong uptrend.
**Measure it. The prior is a reason to test this candidate first, not a reason to skip the test.**

### §80.17 Net synthesis — what these four papers jointly say about the second-rule-class decision

| Question | Answer | Basis |
|---|---|---|
| Does channel breakout survive a fair five-family head-to-head on crypto? | **Yes — 1st or 2nd on all five markets, raw and risk-adjusted** | §80.1 |
| Does *our* rule (plain Donchian) survive it? | ⚠️ **No — it is their support-resistance class, the WORST of five, and dies to realistic costs** | §80.5 |
| What is the highest-value fix to the existing rule? | **Add the `c%` volatility-squeeze precondition** (~3× return, ~5× cost tolerance) — promotes §61.3 | §80.5 |
| Does the long-only constraint cost anything? | **No — all profit is in the buy signals; the sell side is a drag. 4th and 5th independent instances** | §80.2, §80.11 |
| Can we pick the best rule from a sweep? | ⚠️⚠️ **No. [A]'s best BTC rule went NEGATIVE OOS on both markets; [B] shows ex-ante selection is impossible; [D] shows EMAC dying forward** | §80.4, §80.8, §80.15 |
| Is MACD a valid second rule class? | ⚠️ **Crossover: NO, closed. Divergence: UNPROVEN, demoted from leading candidate** | §80.14 |
| Is weekly time-series momentum a valid second rule class? | **Promising and now the leading candidate — but a hypothesis, not a validated rule (no MHT correction)** | §80.10, §80.11, §80.13 |
| Is it uncorrelated with the Turtle? | ⚠️⚠️ **UNKNOWN AND UNCITABLE — must be measured in-house. Build task.** | §80.16 |
| How should we select a SET of rules rather than one? | **[B]'s FDR — fully stdlib-portable — but only over a broad single-pass sweep, never over our handful of rule-class decisions** | §80.7 |

---

## Halal exclusions and screening

- ⛔ **[C]'s long-short quintile spread ("Difference" row, [C] Table 15: 8.62 / 11.28 / 10.57 / 5.18) is
  EXCLUDED BY MANDATE.** Only the **top-quintile long-only leg** transfers (§80.11). Fortunately the excluded
  leg is the *worse* one — quintile 1's return is **positive** (+2.60%/week), so the short side is a loss and
  the long-only leg (11.22%) beats the full spread (8.62%).
- ⛔ **[C]'s cross-sectional momentum literature** (Stoffels 2017's 15-cryptocurrency long-short strategy;
  Jegadeesh & Titman; Asness–Moskowitz–Pedersen) — cross-sectional long-short by construction. **Not
  extracted, not adapted.** [C]'s *time-series* momentum (single asset, own past return) is a different
  object and is the only momentum result taken from this paper.
- ⛔ **[A]'s short leg.** Their position variable is `S ∈ {1, 0, −1}` and every rule in the appendix is
  specified with a "go short" branch. **Only the buy-signal column transfers**, and §80.2 shows that column
  is where all the profit is. Every [A] number quoted above is either a buy-signal statistic or an
  all-parameterization average whose short contribution is measured at ≈0 or negative.
- ⛔ **[B]'s Step-5 pooling to a signed net exposure** — *"go long or short the market with the remaining
  money"* — is **rewritten in §80.7** to a non-negative `deployment ∈ [0,1]`. Sell votes become "don't buy,"
  never shorts.
- ⛔ **[B]'s risk-free-rate leg.** [B] parks neutral-signal wealth at *"the daily Federal funds rate"* and its
  test statistic is an excess-return Sharpe. Per **§73.4** this is removable by substitution (`rf = 0`)
  without touching the mathematics. **Additionally: [B]'s §5 short-selling-cost analysis (Table 6, lending
  fees of 5–20bp/yr sufficient to erase performance; "the 10%-FDR⁺ portfolio results in short positions in
  more than 20% of the days") is doubly excluded** — shorting *and* the borrow fee, which is riba. Its
  finding is nonetheless mildly confirmatory of the long-only posture: the paper's own results are worse once
  the short leg is honestly priced.
- ⛔ **[A]'s and [D]'s use of the Sharpe ratio** with a live `r_f` — same treatment: `rf = 0` by substitution
  (§73.4), and here Sharpe is descriptive/comparative rather than an optimization objective. [A]'s **Sortino
  and Calmar** ratios need no adaptation and are the preferred readings per §54.10/§54.22.
- ⛔ **[D]'s ETH/USDT and BTC/USDT pairs** are Binance **USDT-quoted** spot. USDT is not on our allowlist and
  would face the §71.4a `ribawi`-backing classifier (fiat-backed ⇒ currency ⇒ `bay' al-sarf` regime) and
  §71.3's *thaman* split on same-type swaps. **The price series is usable as evidence about BTC/ETH; the
  quote asset is not an endorsement of USDT.**
- ⛔ **[A]'s Ripple and Litecoin results** — the *findings* about rule families transfer; the *assets* do not.
  Neither is on the allowlist and both would face the §41.1 / §71.6 screens on their own merits. Note the
  §80.4 asymmetry cuts the wrong way for us: the OOS survivors were the alts, and the OOS failures were the
  two Bitcoin markets.
- N/A: **[D] is long-only by construction** (*"Fractional trading and short selling are not permitted"*) —
  a rare case, like §74.8's, where the paper's own design already matches our constraint and no adaptation
  is required.

## Discarded (no agent value)

- **[C] §5 and the entire industry-exposure half of the paper** — the index of exposures of 354 US SIC 3-digit
  industries and 137 Chinese CIC industries to cryptocurrency returns, the 30 Fama-French industry
  regressions, Consumer Goods / Healthcare / Fabricated Products / Metal Mining loadings. This is an
  equity-selection tool for people trading stocks *against* crypto; we trade neither equities nor pairs, and
  it is the declined MPT/cross-sectional direction (§33/§54.22).
- **[C]'s investor-attention factors** — Google Trends deviations, Twitter post counts, the "Bitcoin hack"
  negative-attention ratio. These are genuinely predictive in [C] (a 1-SD Google-search increase yields +2.3%
  2-week-ahead Bitcoin returns), **but they are an external data feed we do not have, cannot backfill
  point-in-time without survivorship contamination, and would make the agent dependent on a Google product's
  availability and revision policy.** More fundamentally they sit close to §6.4's no-prediction-oracle rail:
  a sentiment feed is a forecast input, not a price-derived deterministic rule. Logged as known-and-declined
  rather than unseen. Same disposition for the price-to-"dividend" ratio (built from Bitcoin wallet counts —
  and [C] finds it has *no* predictive power anyway) and the mining-cost supply proxies.
- **[C]'s realized-volatility predictor** — predicts Ripple at 4-, 5- and 7-day horizons but **not Bitcoin or
  Ethereum**. Null on both assets we trade.
- **[B] Appendices F and G** — the asymptotic standard deviations of `π̂₀`, `π̂⁺_A`, `π̂⁻_A` under dependence
  (Farcomeni 2007's empirical-process convergence, the covariance kernel `K(λ₁,λ₂)`, spatial-mixing
  conditions), and the Monte Carlo design. **The point estimators in §80.7 are what we need; the confidence
  intervals around them require the full covariance machinery and are not portable to stdlib.** The Monte
  Carlo's *conclusion* is extracted in §80.7 (FDR is more powerful than RW/BRC, and behaves well under
  cross-sectional dependence); its 7,846-strategy simulation design is not reproducible at our scale.
- **[B] Appendix H** — the survey of historical DJIA transaction costs and equity-loan lending fees from the
  1890s onward. Historically interesting, entirely inapplicable to Coinbase Advanced Trade fee tiers.
- **[A]'s filter-rule family** — it edges out channel breakout on three of five markets (§80.1) and has the
  highest breakeven TCs on Ethereum (147.56bp). **Not extracted as a candidate because it is
  mechanically a percentage-move-from-extremum rule, i.e. the same construct as our refuted dip-buy /
  pullback family read in the opposite direction**, and because §80.1 shows it never beats channel breakout
  by enough to justify opening a new family when §80.5 offers a cheaper improvement to the one we have.
  Recorded here so a future pass does not mistake it for unexamined.
- **[A]'s and [B]'s literature surveys** (Brock/Sullivan/White/Hsu/Neely/Marshall lineage; the foreign-exchange
  and commodity technical-trading literature) — [A]'s survey is partially redundant with §74.11's
  efficiency-decay survey and adds no new crypto-specific result.
- **[D] §§2.1–2.2** — several pages of textbook exposition on quantitative investment and the three forms of
  the efficient market hypothesis. Padding; no content.
- **[D]'s ETH/USDT tables 7–10** for EMAC/RSI/Bollinger, beyond the headline verdicts recorded in §80.15 —
  the pattern (insignificant in-sample, insignificant or negative out-of-sample) is uniform and the
  per-parameter detail adds nothing.
- **Bibliographies, cumulative-log-return figure reproductions** (both papers reproduce their equity curves
  as low-resolution figures with no extractable numbers), **and [A]'s CoinDesk-vs-Bitstamp index-construction
  digression** (they find CoinDesk marginally more profitable and attribute it to CoinDesk being a
  cross-exchange average that *"may offer more inefficiencies than the actual price of Bitcoin"* — a caution
  about index data, not a tradeable finding).

## Net assessment

**This source resolves three of the four questions it was assembled to answer, and definitively closes the
fourth in the negative.**

**What it gives the project.** The first fair head-to-head of rule families on crypto (§80.1), and our family
wins it. The most direct measurement in the KB that the long-only constraint is free — indeed profitable —
because **all** of technical trading's crypto edge is in the buy signals (§80.2). A concrete,
literature-grounded, `a_priori`, stdlib-portable modification to the existing rule worth roughly 3× on
[A]'s data (§80.5, the volatility-squeeze precondition). A complete, portable procedure for selecting a
*surviving set* of rules rather than a single best (§80.7). And a new leading second-rule-class candidate
with a peer-reviewed top-tier pedigree whose long-only leg is the strong leg (§80.10–§80.11).

**What it takes away.** §74.1's identification of published "channel breakout" results with our Donchian rule
was **too generous** — [A]'s channel breakout carries a squeeze precondition our rule lacks, and the family
that *does* match our rule is the worst of the five (§80.5). §74.5's MACD entitlement is **withdrawn**: the
crossover variant is closed, and `macd_divergence` now has to earn its place with no external support
(§80.14). And the batch's most sobering result: **[A]'s best in-sample rule on both Bitcoin markets was a
channel breakout that went negative out-of-sample six months later** (§80.4), which is the same phenomenon
[B] documents systematically (§80.8) and [D] reproduces on EMAC (§80.15). **Three independent demonstrations,
in one source, of a rule passing a snooping correction in-sample and dying forward.** §73's warnings are no
longer a theoretical position in this KB; they are an observed outcome in three separate datasets.

**The honest limits.** [A]'s data ends 31 Dec 2017 with a 6-month OOS extension — **it is subject to exactly
the §74.11 staleness that §74.11 itself warned about**, and §80.4 is that staleness caught in the act. [B] is
not crypto and its authors say so. [C] has no multiple-testing correction at all (§80.13). [D] is
**low-tier-venue and is never load-bearing alone** — every conclusion drawn from it in §80.14 and §80.15 is
also supported by [A]'s §80.4, [B]'s §80.8, or §74.11.

**The single most actionable item is §80.16, and it is a BUILD, not a READ.** Rule-class independence — the
one fact on which the entire second-rule-class decision turns — **is not measured anywhere in this
literature.** Four papers, 30,000+ tested rules, two asset classes, 114 years of data, and not one
between-family correlation. §74.12 warned that independence must be measured rather than assumed; §80.16
confirms there is no paper that could have supplied it. **The measurement is a few dozen lines of stdlib
arithmetic over two position vectors the harness already produces.** Until it is run, promoting any second
rule class risks §73.5's worst case: spending trials budget on a rule that fires when the Turtle fires, and
buying trade count without buying knowability — the precise failure §75.1 identified in the previous
candidate.
