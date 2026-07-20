[← Knowledge Base index](../README.md)

## Source 74 — Four empirical/theoretical papers on technical trading in CRYPTO markets

(A) Gerritsen, Bouri, Ramezanifar & Roubaud, **"The profitability of technical trading rules in the Bitcoin
market"** (20pp; daily BTC, Jul 2010 – Jan 2019; seven trend-following indicators; Ledoit–Wolf bootstrapped
Sharpe tests, 1,000 simulations)
(B) Grobys, Ahmed & Sapkota, **"Technical trading rules in the cryptocurrency market"** (*Finance Research
Letters* 32, 2020, 7pp; daily data, eleven largest cryptos, Jan 2016 – Dec 2018; Variable Moving Average
oscillator; SUR multivariate joint test)
(C) Resta, Pagnottoni & De Giuli, **"Technical Analysis on the Bitcoin Market: Trading Opportunities or
Investors' Pitfall?"** (*Risks* 8(2):44, 2020, 15pp; BTC at 5-min AND daily, Jan 2012 – Aug 2019)
(D) Zakamulin & Giner, **"Trend Following with Momentum Versus Moving Average: A Tale of Differences"**
(41pp, 2018; theoretical, autoregressive return process)

> **Why this source matters more than its page count suggests.** Almost every one of the 73 preceding
> sources is about **equities, futures or forex**. Our rules were ported to crypto **by analogy**, and the
> ported ones were refuted. This is the KB's **first body of crypto-specific empirical evidence on whether
> technical trading rules work at all** — and it lands squarely on the one rule we still believe in.

---

### §74.1 ⭐⭐ "Trading range breakout" IS our Donchian/Turtle rule — and it is the BEST-PERFORMING family on Bitcoin `strategy/rules/`

[A]'s Appendix A.2 defines its trading-range-breakout rule as, verbatim:

```
SUPPORT(B)_t    = MIN(B_{t-1}, B_{t-2}, …, B_{t-n})
RESISTANCE(B)_t = MAX(B_{t-1}, B_{t-2}, …, B_{t-n})
Buy  if  B_t > RESISTANCE(B)_t
Sell if  B_t < SUPPORT(B)_t
```

**That is `TurtleBreakout` exactly** — Donchian-high entry, Donchian-low exit, on daily closes. Different
name, identical mechanics.

Across the full 2010–2019 sample it is **the only rule family that beats buy-and-hold with statistical
significance in the long-only strategy** (their "Strategy 2: Long or out of Bitcoin", which is precisely our
constraint). Sharpe differences vs buy-and-hold, in basis points, with Ledoit–Wolf bootstrapped p-values:

| Rule | Δ vs B&H (bp) | p |
|---|---:|---:|
| SUP/RES **150** | **+3.77** | **0.06** |
| SUP/RES **200** | **+3.32** | **0.07** |
| SUP/RES 50 | +2.59 | 0.20 (ns) |

**This is the first crypto-specific, externally-published corroboration of the direction this project pivoted
to.** It does not validate our specific parameters, and it is in-sample over a period that ends before our
data window begins — but the *family* is no longer resting on analogy from equities.

### §74.2 ⚠️⭐ The best breakout lookback is 150–200 days, NOT 50 — our `entry_lookback=40` may be far too short `strategy/rules/`

In [A] the 50-day channel is **not significant** (p = 0.20) while 150 and 200 both are. This is now the
**third independent source pointing the same direction**:

- §58.6 (Katz & McCormick): their optimum was **80–95**, not 20.
- Our own walk-forward already moved `entry_lookback` **20 → 40** ("every entry lookback longer than 20 beat
  20 out-of-sample").
- §74.2 here: **150–200 significant, 50 not.**

⇒ **Sweep `donchian_entry_n` far past 40** — the evidence has consistently said "longer" every time it has
been asked, and we have never tested above 40. Per §73.12 this is also the cleanest candidate for
re-derivation as an **`a_priori`** parameter (three external sources agreeing costs no trials budget),
rather than the *fitted* value it is today.

### §74.3 ⭐⭐ RSI is SIGNIFICANTLY NEGATIVE on Bitcoin — the third independent refutation `strategy/rules/`

[A], long-only strategy: RSI **−6.33 bp vs buy-and-hold, p < 0.01**. Not merely unprofitable — significantly
*worse* than doing nothing.

This is the **third independent corroboration** of a conclusion this project reached the hard way:

1. our own sim (the refuted dip-buyers, 16% win, PF 0.17);
2. §58.10a — Katz & McCormick found RSI mean-reversion **the worst model in their book**, worse than random;
3. §74.3 — significantly negative on Bitcoin specifically, with bootstrapped p < 0.01.

**Treat RSI mean-reversion as settled and closed. Build no further variants.** Three sources, three methods,
three markets, one answer.

### §74.4 ⚠️ Bollinger bands also significantly negative — a warning for the queued §54.15 band candidate `execution/executor.py`

[A]: Bollinger bands **−5.34 bp, p < 0.01** (long-only). The KB currently carries §54.15's ATR/stdev
volatility-band as a **candidate**. This is not the same construct, but it is the same family (a
volatility-scaled band around a mean), and on Bitcoin the canonical member of that family is significantly
loss-making. ⇒ **Demote the §54.15 band candidate below the breakout work**, and if it is ever tested, test
it against this result rather than against the equities literature that motivated it.

### §74.5 ⭐ The MACD family IS significant on Bitcoin — supports a second, breakout-uncorrelated rule class `strategy/rules/`

[A], long-only: MACD **+2.96 (p=0.02)**, MACD_SIGNAL **+2.80 (p=0.02)**, MACD_HIST **+3.57 (p=0.01)**; rate-of-
change **+3.67 (p=0.01)**. All significant, all positive.

This materially strengthens **§58.10c's `macd_divergence`** as the candidate second rule class — which matters
because under-deployment needs *more valid entries*, and §73.13's `MinBTL ∝ 1/(SR² × trades_per_year)` makes
trade frequency arithmetically valuable. A second rule class that is **genuinely uncorrelated** with the
breakout is the cleanest way to buy frequency without weakening the entry criterion (which §58.2's ablation
showed is a bad trade). ⚠️ But see §74.12 — "uncorrelated" must be *verified*, not assumed.

### §74.6 ⭐ The long-only constraint costs NOTHING here — third instance `CompliancePolicy`, `strategy/rules/`

[A] reports three strategies: **1** = long/out/**short**, **2** = long-or-out (ours), **3** = double-long/long/out
(leveraged). For trading range breakout, **Strategies 1 and 2 produce IDENTICAL numbers** (0.081/+2.59,
0.093/+3.77, 0.089/+3.32) — the short leg contributes **exactly nothing** to the breakout rule's edge.

Joins **§58.3** (long-only *improved* the tested breakout in both samples) and **§73**'s `Side ∈ {−1,+1}` mesh
dimension collapsing to `{+1}` and thereby **halving `N`**. Three independent instances now: **the halal
long-only constraint is not costing us performance on trend-following rules** — and in the §73 case it
actively helps by shrinking the trials budget.

### §74.7 ⭐⭐ The rule works in TRENDING markets and fails in QUIET ones — independent vindication of the ADX gate `analysis/regime.py`

[A]'s subsample analysis of trading range breakout (Δ vs B&H, long-only):

| Period | SUP/RES50 | SUP/RES150 | SUP/RES200 |
|---|---:|---:|---:|
| 2011–2012 | +2.41 (ns) | +3.20 (ns) | +2.58 (ns) |
| 2013–2014 | +5.45 (ns) | **+7.52 (p=0.05)** | **+6.30 (p=0.09)** |
| **2015–2016** | **−1.96** | **−4.31** | **−3.51** (all ns) |
| 2017–2018 | +2.76 (ns) | +5.57 (ns) | +5.54 (ns) |

The rule is **negative across every lookback in the quiet 2015–2016 stretch** and strongest in the trending
2013–2014 and 2017–2018 stretches. [A]'s own abstract: the breakout rule *"delivers outperformance in
strongly trending markets."*

**This is independent, crypto-specific support for keeping the ADX>25 trend gate** — exactly what our
2026-07-20 ablation concluded from our own data (gate-ON PF 1.60 vs gate-OFF 1.15, and gate-OFF turning ETH
negative). Two unrelated lines of evidence, same conclusion. ⇒ **§58.2's warning does not replicate on crypto;
the gate stays.**

### §74.8 [B] A long-only MA rule beats buy-and-hold by 8.76% p.a. across eleven cryptos `strategy/rules/`

[B] is **structurally the closest study to us in the entire KB**: daily data, **long-only by construction**
(*"In the cryptocurrency market, it is not possible to take a short position… so we only focus on the payoffs
from buy positions"*), across the eleven largest cryptocurrencies, 2016–2018.

Result: the **(1,20) Variable Moving Average** rule returns **45.63% p.a.** against buy-and-hold's **36.87%
p.a.** ⇒ **+8.76% p.a. excess**, jointly significant across ten coins (SUR joint test, χ² = 33.10, p < 0.01).
BTC individually: t = 3.25, significant at 1%.

⚠️ **But the long lookbacks fail here:** (1,150) and (1,200) are **not** jointly significant (12.94, 15.35),
and (1,200) *"generated profits only for Ethereum."*

### §74.9 ⚠️ [A] and [B] DISAGREE on lookback length — and the disagreement is informative, not noise

- **[A]:** breakout on **BTC**, 2010–2019 ⇒ **long** channels (150/200) significant, **50 not**.
- **[B]:** MA crossover on **eleven coins**, 2016–2018 ⇒ **short** (1,20) significant, **long (150/200) not**.

They are not directly comparable — different rule families (channel breakout vs MA crossover), different
assets, different windows. **Do not average them into a single "correct" lookback.** The honest reading:
**lookback is regime- and family-dependent**, which is itself an argument for §54.10's robustness-plateau
criterion (§73.1) over point-selecting a best value we cannot afford to fit (`N ≤ 3`, §73.3). Report the
plateau; do not chase the peak.

### §74.10 ⭐ Daily beats intraday, explicitly `strategy/engine.py`

[C], testing BTC at **both 5-minute and daily** granularity, 2012–2019: *"trading on daily data is more
profitable than going intraday… the Buy and Hold strategy outperforms the examined alternatives on an
intraday basis, while Simple Moving Averages yield the best performances when dealing with daily data."*

Direct, crypto-specific validation of the project's **daily-bar** commitment, and of the anti-scalping
posture on *trading* grounds (independent of §65.6's finding that the shariah justification for it was
overstated). Consistent with §61's timeframe-mismatch exclusions and §57's rejected intraday material.

### §74.11 ⚠️⚠️ EDGE DECAY: the crypto market is measurably getting MORE efficient over time `strategy/promotion.py`

Both [A] and [C] survey a consistent literature: Urquhart (2016) finds Bitcoin *"moving toward becoming an
efficient market"* on a sample-split test; Bariviera (2017) finds that **after 2014 the market became more
informationally efficient**; Sensoy (2019) finds both BTCUSD and BTCEUR **more informationally efficient since
the beginning of 2016**; Brauneis & Mestel (2018) find it **less inefficient as liquidity increases**. The
framing throughout is the **adaptive market hypothesis** — inefficiency is a decaying resource.

⚠️ **This is a direct edge-decay warning for us, and it is sharper than it first looks.** Every result in
§74.1–§74.8 comes from samples ending **2018–2019**. Our own validation window is **2021–2026**. So the
crypto-specific evidence supporting our direction was measured in a **more inefficient market than the one we
now trade**, and the trend in efficiency is monotonically against us.

Compounding this, **§73.7** proved that under AR(1) serial dependence — which **§62.2** established our series
has — in-sample optimization is **actively detrimental**, so a decay we observe is **ambiguous** between "the
edge died" and "the edge was a selection artifact all along," disambiguated only by `N`. ⇒ **Edge-decay
monitoring is not optional for this rule, and any decay must be read against a recorded trials count.**

### §74.12 ⚠️⭐ Momentum and Moving-Average rules are NOT independent families — this kills a diversification assumption `strategy/rules/`

[D] compares MOM and MA rules theoretically, modelling returns as an **autoregressive process** (the same
AR framing as §62.2): *"the similarity between the MOM and MA rules is rather high and **increases with
increasing trend strength**. However… the MA rules have a more robust forecast accuracy of the future
direction of price trends. As a result, under uncertain market dynamics the MA rules tend to gain an advantage
over the MOM rule."*

**Consequence for the under-deployment problem:** adding an MA rule alongside the breakout would buy trade
frequency but **NOT diversification** — the signals converge precisely when trends are strong, i.e. exactly
when our rule fires. Worse, correlated rules inflate `N` (§73.5) without adding independent evidence.

⇒ **A second rule class must be demonstrated uncorrelated, not assumed.** §58.10c's `macd_divergence`
(§74.5) is the better candidate *because* it is structurally different (oscillator divergence, not trend
persistence) — but that must be **measured** (signal-overlap / correlation of trade timing), not asserted.
Secondary: [D] gives a theoretical reason to prefer **MA over MOM** under uncertain dynamics if that choice
ever arises.

### §74.13 ⭐ Both empirical papers restrict parameters A PRIORI to control data-snooping — the practice §73.12 asks for `strategy/backtest.py`

[A]: *"to mitigate potential biases introduced by data snooping, we follow Brock et al. (1992), restraining
ourselves to the 'most popular ones': 1-50, 1-150, 5-150, 1-200 and 2-200."* [B] likewise fixes n ∈ {20, 50,
100, 150, 200} in advance.

**This is exactly §73.12's `a_priori` parameter provenance, practised by working researchers** — restrict the
grid from prior literature rather than searching it, precisely to keep `N` small. It is also the strongest
available external endorsement of §73.13's reframing that **the knowledge base is a trials-budget subsidy**.

Both papers additionally use real inferential machinery: [A] Ledoit–Wolf bootstrapped Sharpe differences
(1,000 simulations); [B] a SUR multivariate joint test handling cross-sectional correlation. **This is
markedly better evidence than the uncorrected raw-profitability studies the brief warned about** — though
neither applies a full data-snooping correction across the rule families tested (White's Reality Check /
Hansen's SPA), so per §73.2 their p-values are still single-trial statistics over a small chosen grid.

---

## Halal exclusions and screening

- ⛔ **[A]'s "Strategy 1" (long, out, and short) and "Strategy 3" (double-long)** — shorting and leverage
  respectively. **Only "Strategy 2: Long or out of Bitcoin" transfers**, and every number quoted above is
  taken from that column. Fortunately §74.6 shows the short leg adds nothing to the breakout rule anyway.
- ⛔ **[B]'s and [C]'s use of the Sharpe ratio** carries the usual risk-free-rate contact. Per **§73.4** this
  is removable by substitution (`rf = 0`) without touching the mathematics, and here it is used descriptively
  rather than as an optimization objective — so it is usable, with rf=0.
- ⛔ **[C]'s 5-minute intraday testing** — out of scope by timeframe and by the anti-scalping rail; kept only
  for its *comparative* conclusion (§74.10) that daily beats intraday.
- ⛔ [B]'s sample includes coins we would never admit (Dogecoin, Peercoin, BitShares, Namecoin, MaidSafeCoin,
  Nxt, Stellar) — the *finding* transfers, the *allowlist* does not. Any of these would still face the
  §41.1 / §71.6 screens on their own merits.
- N/A: [B] explicitly notes short positions are impractical in crypto anyway — a rare case where market
  structure and the halal constraint point the same way.

## Discarded (no agent value)

- [C]'s extensive literature survey of Bitcoin *price-formation* research (Google Trends correlation,
  macroeconomic news surprises, exchange price-discovery/interconnectedness, Markowitz extensions) — all
  either no-oracle (§6.4) or the declined MPT direction (§33/§54.22).
- [D]'s full derivations and lemmas — the usable content is the **conclusion** (§74.12); the proofs do not
  translate to a discrete daily-bar backtest.
- [A]'s OBV variants (all negative or non-significant in the long-only column) — no action; note this
  slightly tensions §54.23's volume-confirmation enthusiasm, though [A] tests OBV *as a standalone entry
  rule*, not as a breakout **filter**, which is how §54.23 proposes to use volume. Not a refutation.
- Bibliographies, figure reproductions, and [A]'s exchange-level price-discovery digressions.

## Net assessment

**The crypto-specific evidence SUPPORTS the trend-following/breakout direction and REFUTES the mean-reversion
direction — the two most consequential strategic bets this project has made.** It arrives with real
inferential machinery (bootstrapped Sharpe tests, SUR joint tests, a-priori grid restriction), which puts it
well above the uncorrected-profitability tier.

Three caveats keep it from being decisive: samples end **2018–2019** while we trade 2021–2026 and the market
is measurably maturing (§74.11); no paper applies a full multiple-comparison correction across rule families
(§74.13); and transaction-cost treatment is not stated in the sections read, which matters because [B]'s
(1,20) rule in particular would trade often enough for 0.6% round-trip fees to bite.

**The single most actionable item is §74.2:** three independent sources have now said our Donchian entry
lookback is too short, and we have never tested above 40.
