# Trading Knowledge Base — Sourced & Adapted for `keel`

**Purpose:** Cumulative extraction from trading-course transcripts, translated into
deterministic, implementable rules/tools for our **halal spot-crypto, long-only,
no-leverage** agent. Each source is logged; concepts are mapped to agent modules.

This knowledge base is split across files. **This README is the index.** Each transcript
has its own file under [`sources/`](./sources/). The [module map](#module-map) below is the
thematic view — it points into the source files by section.

## How to read it — citation convention
Throughout, **`§N.x`** means *source N, section x* → the file
[`sources/source-NN.md`](./sources/), section `N.x`. Cross-references like "(§4.1)" are
therefore file-to-file links by convention (source-04.md §4.1). This keeps every inline
reference valid across the split without rewriting them.

## Adaptation rules that apply to ALL sources (non-negotiable)
- **Long-only spot.** No shorting. Bearish/short setups become *exit rules for held
  assets* + *"don't buy" filters*, never short entries.
- **No leverage / margin / lots.** Borrowing = riba; excluded by design. Position
  sizing uses actual cash only.
- **"Pips" → price ticks / percentages** for crypto.
- **Halal allowlist + hard caps + kill-switch** always bind (see design spec).
- Signals only count **at the right location** (confluence), never in isolation.
- **No prediction oracle.** The agent's intelligence is deterministic tested rules +
  backtest stats + analysis. An LLM may explain/summarize/flag; it never decides an entry (§6.4).

---

## Sources log

| # | Source | Status | File |
|---|---|---|---|
| 1 | "Trading for Beginners / Dummies" (full beginner course, ~2h20m) | extracted | [source-01](./sources/source-01.md) |
| 2 | "Trading for Beginners — Part 2" (strategy dev, journaling, fees, ~1h40m) | extracted | [source-02](./sources/source-02.md) |
| 3 | "TradingView Full Tutorial" (~1h17m, mostly UI) | extracted (thin) | [source-03](./sources/source-03.md) |
| 4 | "Trading Terminology Explained" (glossary; ~52m) | extracted | [source-04](./sources/source-04.md) |
| 5 | "Biggest Mistakes Traders Make" (~8m) | extracted | [source-05](./sources/source-05.md) |
| 6 | "How AI Is Changing Trading" (meta; ~8m) | extracted | [source-06](./sources/source-06.md) |
| 7 | "End-to-End Trend Trading Strategy" (NZDJPY; ~21m) | extracted | [source-07](./sources/source-07.md) |
| 8 | "Markets Are Not Random / Confluence Scoring" (GBPAUD; ~28m) | extracted | [source-08](./sources/source-08.md) |
| 9 | "High-Probability Fib Confluence / CTS" (GBPUSD; ~11m) | extracted | [source-09](./sources/source-09.md) |
| 10 | "14 Types of Risk / Risk Management" (~15m) | extracted | [source-10](./sources/source-10.md) |
| 11 | "The Best Way to Make Money" (business philosophy; ~23m) | out of scope | [source-11](./sources/source-11.md) |
| 12 | "Should I Sell Bitcoin Now? — DCA vs Timing" (BTC spot + behavioral finance; ~14m) | extracted (on-topic) | [source-12](./sources/source-12.md) |
| 13 | "The Guaranteed Financial Freedom Roadmap" (personal-finance/wealth; ~40m) | out of scope | [source-13](./sources/source-13.md) |
| 14 | "What's Happening to Bitcoin? — Cycle & Seasonality" (BTC macro-cycle; ~11m) | extracted (on-topic) | [source-14](./sources/source-14.md) |
| 15 | "Gartley Pattern (from the 1935 original)" (harmonic; ~16m) | extracted → deferred (v2) | [source-15](./sources/source-15.md) |
| 16 | "£4,500 GBP/JPY Trade" (live confluence short; ~12m) | extracted | [source-16](./sources/source-16.md) |
| 17 | "Three Trend-Continuation Setups" (Greystone method, 4 entry techniques; ~21m) | extracted | [source-17](./sources/source-17.md) |
| 18 | "Carry / Rollover" (~7m) | ⛔ excluded (riba) | [source-18](./sources/source-18.md) |
| 19 | "Trailing Stops" (trade management; ~15m) | extracted | [source-19](./sources/source-19.md) |
| 20 | "How Professionals Backtest" (backtest + money-mgmt + pivot optimization; ~80m) | extracted (rich) | [source-20](./sources/source-20.md) |
| 21 | "High- vs Low-Probability Setup" (AUD/CAD short, double-top + seasonality; ~22m) | extracted | [source-21](./sources/source-21.md) |
| 22 | "5 Things to Know Before Trading Crypto" (crypto-specific; ~4m) | extracted (on-topic) | [source-22](./sources/source-22.md) |
| 23 | "How to Trade Cryptocurrencies with Deriv" (Vince Stanzione **ebook**, 56pp; CFD-broker promo) | extracted (1 new system + refinements; premise excluded) | [source-23](./sources/source-23.md) |
| 24 | "10 Chart Patterns Every Pro Trader Should Know" (Vince Stanzione **ebook**, 24pp; pattern catalog) | extracted (thin; reinforces §23.1 breakout; subjective patterns → v2) | [source-24](./sources/source-24.md) |
| 25 | "The Complete Guide to Trading" (Corporate Finance Institute, 2018 **textbook**, 116pp) | extracted (NEW: ADX trend-strength filter + good-trade principle; Parts 1–2 out of scope) | [source-25](./sources/source-25.md) |
| 26 | "7 Traits of Successful Financial Traders" (Vince Stanzione **ebook**, 25pp; psychology) | extracted (thin; NEW pyramiding + 20-SMA exit; reinforces trend-following pivot) | [source-26](./sources/source-26.md) |
| 27 | "How to Trade Commodities" (Vince Stanzione **ebook**, 50pp) | extracted (mostly dup/reinforce; **HIGH: Turtle Trading = canonical breakout spec**) | [source-27](./sources/source-27.md) |
| 28 | "The Economics of Islamic Finance and Securitization" (Jobst, IMF WP 07/117, 2007, 37pp) | extracted (**compliance FOUNDATION** — grounds all exclusions + NEW haram-sector screen; securitization out of scope) | [source-28](./sources/source-28.md) |
| 29 | "Islamic Banking Processes and Products" (Oracle white paper, 2017, 14pp) | extracted (thin; reinforces §28 — AAOIFI ref + scholarly-divergence caveat; banking products out of scope) | [source-29](./sources/source-29.md) |
| 30 | "Understanding Riba in Islamic Finance" (Azzad Asset Mgmt white paper, 3pp) | extracted (thin; NEW riba al-fadl → spot-settlement grounding + stablecoin parity; AAOIFI re-confirmed) | [source-30](./sources/source-30.md) |
| 31 | "Risk Management in Mudharabah and Musharakah Financing" (Febianto, iECONS 2007, 30pp) | extracted (mostly out-of-scope/saturated; only nugget: ghorm vs gharar rationale) | [source-31](./sources/source-31.md) |
| 32 | "Developments in Risk Management in Islamic Finance: A Review" (Al Rahahleh et al., 2019, 23pp) | extracted (out-of-scope/saturated; only nugget: negative/positive screening vocabulary) | [source-32](./sources/source-32.md) |
| 33 | "A Review on Portfolio Optimization Models for Islamic Finance" (Lim et al., AIMS Math 2023, 28pp) | extracted (MPT declined-direction; **NEW: zakat 2.5% report**; reinforces long-only/Sortino/per-asset cap) | [source-33](./sources/source-33.md) |
| 34 | "The Trader's Bible" (T3 Live, 2025, 28pp; US equities/options day-trading) | extracted (**NEW: close-based stop** targets stop-defect; confluence-ordering + first-retest; options/gaps/earnings excluded/N-A) | [source-34](./sources/source-34.md) |
| 35 | "Quantified Edge: Using AI, ChatGPT & Python" (Metaverse Trading Academy, 2025, 65pp) | extracted (**LLM-feature reference** — validates §5 asymmetry + craft; NEW breakeven-winrate formula; SMC/order-flow deferred/N-A) | [source-35](./sources/source-35.md) |
| 36 | "7 Trading Themes for 2026" (Vince Stanzione **ebook**, 36pp; macro-outlook) | extracted (low value — time-bound predictions = no-oracle §6.4; reinforces wider-stops + narrow allowlist; negative LLM exemplar) | [source-36](./sources/source-36.md) |
| 37 | "How to Trade Forex" (Vince Stanzione **ebook**, 76pp) | extracted (near-total dup of §23/§27 — MA/Donchian/RSI + reused appendices; sessions marginal; options/multipliers/carry excluded) | [source-37](./sources/source-37.md) |
| 38 | "How to Trade Stocks" (Vince Stanzione **ebook**, 58pp) | extracted (near-total dup of §23/§27/§37; stock fundamentals/dividends N/A to crypto; reinforces no-oracle only) | [source-38](./sources/source-38.md) |
| 39 | "Trading Cryptocurrencies 1" (Swissquote primer, 16pp) | extracted (crypto-fundamentals education, no strategy; reinforces halving-cycle low-weight + custody only) | [source-39](./sources/source-39.md) |
| 40 | "Trading Cryptocurrencies 2: Chainlink, Tezos and more" (Swissquote primer, 13pp) | extracted (altcoin-profile catalog, no strategy; only bearing = allowlist-curation; Augur=haram_sector example) | [source-40](./sources/source-40.md) |
| 41 | "Trading Cryptocurrencies 3: Cardano, Algorand and more" (Swissquote primer, 17pp) | extracted (altcoin/DeFi-profile catalog, no strategy; concrete riba/haram_sector rejects: Aave/Compound/Maker/yearn) | [source-41](./sources/source-41.md) |
| 42 | "Introduction to Options" (Swissquote primer, 6pp) | ⛔ out of scope — options = excluded instrument (gharar/maisir/not-spot); reaffirms §27.4/§28.1, no action | [source-42](./sources/source-42.md) |
| 43 | "Buying a Call to Open" (Swissquote options series pt.2, 7pp) | ⛔ out of scope — options how-to (excluded instrument); logged compactly under §42, no action | [source-43](./sources/source-43.md) |
| 44 | "Buying a Call to Open: Profit and Loss" (Swissquote options series pt.3, 6pp) | ⛔ out of scope — options P&L (excluded instrument); logged compactly under §42, no action | [source-44](./sources/source-44.md) |
| 45 | "Selling a Naked Call to Open" (Swissquote options series pt.4) | ⛔ out of scope — options + short + premium-for-no-risk (triply excluded); logged compactly under §42, no action | [source-45](./sources/source-45.md) |
| 46 | "Selling a Covered Call to Open" (Swissquote options series pt.5) | ⛔ out of scope — options-income (written-call leg excluded; hold spot, no option overlay); logged compactly under §42, no action | [source-46](./sources/source-46.md) |
| 47 | "Buying Puts to Open" (Swissquote options series pt.6) | ⛔ out of scope — option + bearish/short-equivalent (doubly excluded); logged compactly under §42, no action | [source-47](./sources/source-47.md) |
| 48 | "Selling Puts to Open" (Swissquote options series pt.7) | ⛔ out of scope — options-income (cash-secured put, =§34.7); logged compactly under §42, no action | [source-48](./sources/source-48.md) |
| 49 | "Conclusion" (Swissquote options series pt.8 of 8) | ⛔ out of scope — closes options series; exotic strategies excluded, generic advice reinforces only; no action | [source-49](./sources/source-49.md) |
| 50 | "Introduction to Stock Investing: Corporate Analysis" (Swissquote primer, 9pp, 2022) | ⛔ out of scope — equity fundamental-analysis ratios, N/A to crypto (=§38.3); one reinforcement: CAPM/Alpha/Beta = negative exemplar re-grounding the declined CAPM/MPT (riba via Rf); no action | [source-50](./sources/source-50.md) |
| 51 | "Building a Diversified Portfolio" (Swissquote primer, 19pp, 2022) | ⛔ largely out of scope — passive ETF asset-allocation (ETFs not our instrument; bonds/bond-ETFs/intermarket-cycle = riba/no-oracle; fixed-weight rebalancing = wrong paradigm); reinforces correlation-sizing + narrow allowlist ("redundancy doesn't diversify" → correlated alts = near-single exposure); no action | [source-51](./sources/source-51.md) |
| 52 | "Introduction to ETF and Funds Investing" (Swissquote primer, 22pp, 2022) | ⧉ near-dup of §51 ETF material (not re-extracted) + ⛔ out of scope — ETNs (debt=riba)/ETCs/mutual funds are non-spot fund wrappers; bond/money-market funds=riba; value/growth=equity fundamentals N/A (§50); reinforces capital-gains-only (never yield/riba) + liquid-no-lockup; no action | [source-52](./sources/source-52.md) |
| 53 | "Basics of Warrants" (Swissquote Bank ebook, 38pp) | ⛔ excluded wholesale, not extracted — warrants = leveraged option-right derivatives (gharar/maisir + gearing, §27.4/§28.1); Greeks/exotic KO-warrants/mini-futures all excluded; generic order-types (limit/stop/trailing/OCO) already built in executor; Swissquote stream exhausted — stop feeding | — (not extracted) |
| 54 | **"Trading Systems and Methods"** (Perry J. Kaufman, 5th ed., Wiley, 2013, **1,232pp** — the canonical quant-trading textbook) | extracted (**part 1 — the crown-jewel source**; 5 chapters: noise/ER, adaptive/KAMA, volatility & stops, system-testing rigor, risk-control/market-ranking. Directly fixes the crypto stop/risk model + validation rigor + answers the ETH question; independently VALIDATES the trend-following pivot. Part-2 pass recommended for Ch 5/8/22/23-tail/24) | [source-54](./sources/source-54.md) |
| — | "Trading Terminology Explained" (re-paste) | ⧉ duplicate of Source 4 | see [source-04](./sources/source-04.md) |

---

## Module map
Thematic view — which agent module each theme feeds, and where it's sourced.

| Agent module | Theme | Key sections |
|---|---|---|
| `analysis/candles.py` | Candlestick primitives (pin/doji/tweezer/marubozu/3-bar/rejection); V-top/double-bottom (full rules: RSI-extreme + shallow-pullback>0.382 + divergence) | §1.3, §2.1, §7.2, §8.2, §16.1, §21.1 |
| `analysis/levels.py` | S/R: horizontal/angular/round-number/gaps/magnet; ≥3 touches (validates level exists); **first *retest* of a validated level = best entry, Nth bounce = exhausted §34.3** | §1.3, §4.8, §7.3, §9.2, §34.3 |
| `analysis/regime.py` | Market condition + phase (run/pullback), choppy = no-trade; **ADX trend/no-trade gate (ADX<25 = ranging → stand aside); 50/200 golden-cross long-horizon bias (§25.1–25.2)**; **NEW: rank markets by *trendiness* (Efficiency Ratio §54.1, ADXR/CSI §54.9, `% profitable tests`≥~70% §54.11) → per-asset trend-tradability gate (the data-driven ETH keep/drop/allocate answer)** | §1.2, §2.0, §25.1, §25.2, §54.1, §54.9, §54.11 |
| `analysis/indicators.py` | EMA / RSI (+divergence) / MACD (12/26/9) / ATR / Fib retrace+extension / deceleration; **NEW: ADX/DMI trend-strength (dir-blind; confirms real trend, rejects false breakouts) §25.1**; **NEW (Kaufman §54): Efficiency Ratio (fractal efficiency, noise≠volatility), annualized-vol & relative-vol, ADXR** | §1.4, §1.5, §3.1, §3.3, §4.4, §25.1, §25.3, §54.1, §54.2, §54.9 |
| `analysis/insights.py` | AI behavioral/seasonality analysis; edge-decay detection; BTC cycle/seasonality context; seasonality as low-weight CTS factor; pivot-slice auto-pruning of weak pair/day/time/rule buckets; **deferred LLM feature (OFF/API-gated): AI = thinking-tool not signal-generator; feed-don't-recall data; adversarial/refute prompting; output=backtestable doc; LLM outside live loop §35.1** | §6.1, §6.2, §6.3, §12.3, §14.3, §20.7, §21.2, §35.1 |
| `analysis/pnl.py` | FIFO P&L, drawdown / time-in-drawdown, recovery table | §4.6, §10.5, §2.4 |
| `strategy/rules/` | Parameterized pullback-continuation family; RSI mean-reversion; DCA/dip-buy; **NEW: Donchian channel breakout (trend-following/buy-strength, long-only) = canonical Turtle system — 20-day-high entry / 10-day-low ASYMMETRIC exit + ATR sizing/stops (§27.1); candidate to replace refuted dip-buying**; **NEW Kaufman candidates: KAMA adaptive-trend (ER-driven, for noisy crypto §54.5), volatility-breakout (buy on +k·ATR §54.3), linear-regression-slope trend (§54.11)**; deferred (harmonics incl. full Gartley, Fib-inversion, BTC macro-cycle) | §2.1, §3.3, §7.1, §9.3, §12.1, §15.2, **§23.1, §27.1, §54.3, §54.5, §54.11** |
| `strategy/engine.py` | Identify→Predict→Decide→Execute; **CTS scoring → rail-bounded execution** (CTS grade = A+/B/C conviction sizing §34.4); 4 graded entry techniques; kill-zone entry gate; multi-TF bias; news filter; **ADX + MACD-up as breakout-confirmation confluence factors (§25.1, §25.3)**; **top-down eval ORDER: structure→location(edges not middle)→pattern→candle-trigger-last §34.2** | §2.0, §3.2, §3.6, §8.1, §9.1, §17.1, §17.2, §25.1, §34.2, §34.4 |
| `strategy/backtest.py` | Backtest, intrabar order-of-events, spread/slippage modeling, MFE/MAE, no-overlap realism | §1.7, §2.3, §4.2, §20.2, §20.5 |
| `strategy/promotion.py` | Paper gate, promotion/demotion, expectancy + R:R≥1.5–2 & win≥55%, min-sample (100 trades/5yr), edge decay; **per-rule-class floor via breakeven-winrate formula `win_rate > 1/(1+R:R)` — R:R 3 ⇒ 25% suffices; replaces the flat 55% bar for trend-followers (§23.1, §25.5, §35.2)**; **Kaufman testing rigor §54.10: expectations-first, information ratio, walk-forward + OOS/feedback firewall, robustness-plateau (not the max), drawdown-probability check, `% profitable tests`≥~70% robustness bar §54.11** | §1.6, §4.5, §5.2, §6.3, §20.6, §23.1, §25.5, §35.2, §54.10, §54.11 |
| `strategy/money_mgmt.py` | Smooth-ratio sizing ramp (profit-trigger + acceleration) bounded by total & weekly DD caps; fixed-fractional on current equity; **ATR ("N") volatility-based sizing — smaller size when more volatile, crypto-essential (Turtle, §27.1)**; **pyramiding / scale-into-winners (position-level, never losers; rail-bounded, default off) §26.1**; **NEW Kaufman §54.7: volatility-parity / target-volatility sizing (`invest = ann-stdev / target-vol`, ~15%) via `cash/(ATR·√252)`; volatility stabilization = shrink size as vol rises (risk control WITHOUT stops)** | §4.7, §20.3, §20.4, §26.1, §27.1, §54.7 |
| `execution/executor.py` | Order lifecycle (close-validate, one-candle validity), OCO/bracket, partial exits, buffers, ATR stops, trailing-stop algorithm (+ channel-low trail §23.2, **20-SMA close-below & trail-to-breakeven §26.2**); **`stop_trigger=close\|intraday` — close-based stop confirmation cuts crypto whipsaw/shakeout, targets "stops too tight" defect §34.1**; target methods (1:1 / swing-high / Fib ext / **pattern-height §24.2**); **NEW Kaufman volatility-adaptive trailing stops §54.6/§54.8: Kase Dev-Stop (`ATR + f·STDEV`), ER-adaptive ATR stop (6·ATR initial, tightens as ER rises — fixes "stops too tight"), Parabolic SAR as long-only trail; stops trigger on the CLOSE (noise), profit-taking on the intraday spike** | §2.2, §3.5, §7.4, §8.2, §17.3, §19.1, §23.2, §24.2, §26.2, §34.1, §54.6, §54.8 |
| `execution/guards.py` | **The hard rails** (see below); crypto-volatility calibration; API-key security / no-withdrawal scope / custody risk; **data-spike guard (implausible-vs-ATR bad-tick §24.3)** | §4.1, §5.1, §8.1, §10.3, §10.4, §22.1, §22.3, §24.3 |
| `CompliancePolicy` (HalalPolicy) | Shariah grounding of the exclusion set: riba (al-nasee'ah + **al-fadl §30.1**)/gharar/maisir → no leverage/derivatives/options; ownership+profit-loss-sharing → spot long-only permissible; **spot/immediate settlement mandatory (deferred same-commodity/currency exchange = riba); same-asset/stablecoin swaps only at parity §30.1**; **`haram_sector` screen at allowlist admission — catch on-chain FUNCTION not just marketing: reject riba/lending/yield tokens (Aave/Compound/Maker/yearn §41.1) + maisir/prediction-market tokens (Augur §40.1)**; low-turnover as compliance value; **AAOIFI = authoritative screening-standards reference (2 sources); keep policy pluggable + document our conservative interpretation §29.1–29.2**; **NEW positive obligation: optional zakat-estimate report (~2.5% mkt value/lunar yr, report-only) §33.1** | §28.1–28.4, §29.1–29.2, §30.1, §30.3, §33.1, §40.1, §41.1 |
| DB schema | transactions/candles/orders/rules/signals/backtests/pnl_daily/agent_state; journal fields | §1.7, §2.5, §4.6 |

### The hard rails (un-overridable, incl. bypass mode)
Base four (design): allowlist · per-order & daily $ caps · sell-only-on-rule · kill-switch + audit.
Added from transcripts: total-exposure cap (§4.1) · correlation-adjusted sizing (§4.1) ·
min-move/anti-scalping (§4.1) · no-averaging-into-losers (§5.1) · no-stop-widening (§5.1) ·
account-drawdown circuit breaker — total **and** weekly (§10.3, §20.4) · per-asset concentration cap (§10.3) ·
stale-data/feed-health guard (§10.4). CTS dynamic sizing moves **only within** these rails (§8.1, §10.6).

### Explicit exclusions (halal / spot)
Carry trades (**entire Source 18 excluded as riba**, §18), hedging, bonds, margin/leverage/lots, short-selling, scalping — see §4.9, §10.10, §18.
Also **N/A (no crypto analog): TRIN / ARMS breadth index** (needs a stock-index advance-decline basket, §25.6); fixed-income/money-market asset classes (interest = riba, §25.6).
**Digital / binary / barrier options** (Rise-Fall, Touch/No-Touch, Ends-Between, Accumulators) → **excluded: not spot + maysir/gharar** (gambling/excessive-uncertainty), §27.4. Swap/rollover financing = riba (§27.4).

**Authoritative grounding (Source 28 — IMF paper on Islamic finance):** the exclusions above map 1:1 to the shariah prohibitions — **riba** (interest/guaranteed return → leverage/swap/carry/bonds), **gharar** (excessive uncertainty → *all* derivatives: CFDs/futures/forwards/options), **maisir** (gambling → binary/digital options + speculation). The **ownership + profit-loss-sharing** principle is *why* **spot long-only is permissible** while derivatives are not (§28.1–28.2). Two additions from §28: (a) **NEW `haram_sector` screen** at **allowlist admission** — exclude gambling/adult/prohibited-sector tokens + riba-yield/lending tokens (§28.4); (b) **low-turnover** is itself a compliance value (high churn drifts toward maisir) → reinforces the anti-scalping rail + trend-following hold bias (§28.3).

---

## Open judgment calls (for user review)
Decisions I made that are worth confirming/overriding:
1. **Long-only translations** of short setups → exit/don't-buy filters (§2.1, §7.1, §9.3).
2. **Deferrals to v2:** harmonics (Gartley/ABCD/Cipher), Fibonacci Inversion, Wall-Street-cheat-sheet
   macro-cycle (§3.4, §8.4, §9.3–9.4); **subjective chart-pattern geometry — H&S, cup & handle,
   rounding top/bottom, wedges (§24.5)** — same reason (discretionary trendline/curve fitting, overfit).
3. **Rule consolidation:** Sources 2 & 7 merged into one parameterized rule (§7.1).
4. **New rails beyond the original four** (see hard-rails list above).
5. **Riba exclusions:** carry/hedging/bonds/margin (§4.9, §10.10).
6. **CTS dynamic aggressiveness** bounded by rails (§8.1 ⚠️, §10.6).

## Status
Structure has **saturated** — most sources now refine/reinforce rather than reshape. **Source 23 is the
exception with a genuinely new lead:** the **Donchian channel breakout** (§23.1) — a *trend-following,
buy-strength* rule that is the structural opposite of the pullback/RSI dip-buying rules the sim just
proved catch falling knives (see [[halal-cb-autotrade-project]]). It plus a wide channel-low trailing
stop (§23.2) directly targets the two milestone-6 findings (negative dip-buy edge + too-tight stops).
**Next concrete step:** prototype the long-only Donchian breakout rule (+ `breakout` detector, per-class
promotion floor) and validate it through `keel simulate` on the cached 5yr data. Then fold into the spec.
Source 24 (chart-pattern catalog) is thin but **reinforces the breakout direction** (ascending-triangle /
base breakouts = same buy-strength thesis) and adds two small levers to fold in when building it: a
`pattern_height` target method (§24.2) and a data-spike feed guard (§24.3); its subjective patterns are
deferred to v2 (§24.5).
**Source 25 (CFI textbook) sharpened the breakout plan with the missing filter: the ADX trend-strength
indicator (§25.1)** — gate breakout entries on **ADX>25 + uptrend** to reject false breakouts / ranging
chop (the exact failure mode dip-buying hit). New indicator + regime gate + CTS factor. It also validated
the **expectancy-based per-class promotion floor** via the "good trade vs winning trade" principle (§25.5).
So the breakout-family rule to prototype now has: buy-strength entry (Donchian/ascending-triangle) +
**ADX/MACD-up confirmation** + channel-low trail (§23.2) + pattern-height/measured-move target options +
a low-win/high-R:R promotion floor. Then validate via `keel simulate`.
**Source 26 (Stanzione psychology ebook) is thin/reinforcing** but adds **pyramiding / scale-into-winners**
(§26.1 — the sanctioned opposite of the no-martingale rail; optional later `money_mgmt` enhancement,
default off) and a simple **20-SMA close-below exit + trail-to-breakeven** (§26.2); it also strongly
endorses the trend-following pivot (the author's own profits came from trend following) and cautions —
via KISS — to keep the ADX/MACD confluence set minimal and price-first. **Stanzione psychology content has
saturated** (23/24/26 mostly reinforce).
**Source 27 (Stanzione commodities ebook) is mostly duplication** (it re-embeds the Source-24 pattern
guide) **but the Turtle Trading section fully specifies the breakout rule to build:** long-only Turtle =
**20-day-high entry / 10-day-low ASYMMETRIC exit (new vs §23.1's 20/20) + ADX>25 confirmation + ATR
volatility sizing + ~2×ATR stop (attacks the "stops too tight for crypto" defect) + 1% risk + ATR/10-day
trailing exit + low-win/high-R:R promotion floor.** Turtle is a proven, published trend-follower — a
confidence anchor that the pivot is a real documented edge. Prototype this and validate via `keel simulate`.
**The Stanzione ebook stream has saturated** (23/24/26/27); 27 earned its keep on the Turtle spec alone.
**Source 28 (IMF Islamic-finance paper) is a new source TYPE — a compliance foundation, not a strategy.**
It gives authoritative shariah grounding (riba/gharar/maisir) for every exclusion rail, explains why spot
long-only is permissible while all derivatives/options are not, and adds a **`haram_sector` allowlist-
admission screen** (§28.4) + a low-turnover-as-compliance note (§28.3). Wire the sector screen into
CompliancePolicy; cite this paper in the design spec's compliance section. Its securitization/sukuk
machinery is out of scope. **Source 29 (Oracle Islamic-banking white paper) saturated the compliance
dimension** — only 3 takeaways: **AAOIFI** = the authoritative screening-standards reference; a
**scholarly-divergence caveat** (halal-screening isn't monolithic → keep CompliancePolicy pluggable +
document our conservative interpretation); and industry-practice confirmation that forwards/derivatives are
broadly prohibited. No new rails/strategy. **Source 30 (Azzad riba paper)** adds one nuance — **riba al-fadl**
(unequal same-commodity exchange) → an independent shariah grounding that **settlement must be spot/immediate**
(deferred same-commodity/currency exchange = riba, another reason forwards/futures are out) + a **stablecoin/
same-asset "parity only" note** (already satisfied by rail-13 USDC funding + no crypto-crypto pairs); re-confirms
AAOIFI. **Compliance sources 28/29/30 have covered the ground — recommend pausing further Islamic-finance papers**
(likely pure reinforcement). **Source 31 (mudharabah/musharakah risk paper) confirmed this** — out-of-scope
(bank PLS-financing risk, not trading risk); only nugget = **ghorm vs gharar / al-ghorm bil ghonm** ("no
liability, no gain" → profit is legitimized by bearing real owned-asset risk; sharpens §28.2 rationale, no
rule change). **Source 32 (Islamic-finance risk-management review) confirmed exhaustion again** — out-of-scope bank
risk-management literature review; only nugget = **negative vs positive Shariah screening** (our
CompliancePolicy = the automated "negative screen"; "positive"/ethics screening noted, out of conservative
scope). **Islamic-finance stream is exhausted (28→32); STOP feeding compliance papers — they now add nothing.**
**Source 33 (Islamic portfolio-optimization review)** is a **declined-direction** paper (project already
declined MPT/mean-variance = riba/quant-stack, spec §10) but surfaced one genuinely new *positive* item:
**zakat** (2.5%/lunar-yr wealth purification → optional report-only feature §33.1); it also reinforces
long-only (short = gharar), the Sortino/downside-risk success bar, and the per-asset cap, and **validates
keeping MPT declined**. Exclusion-side compliance remains exhausted; zakat is the one add.
**Source 34 (T3 Live "Trader's Bible", US equities/options)** — new author/market; much excluded (all
options = gharar/maisir) or N/A (gaps & earnings don't port to 24/7 crypto), but landed a real one:
**close-based stop confirmation** (§34.1 — trigger a stop on a candle *close* beyond the level, not an
intraday wick → cuts crypto whipsaw/shakeout, directly targets the "stops too tight" defect; `stop_trigger=
close|intraday`, fold into the Turtle build). Plus the top-down confluence *ordering* (§34.2), first-retest-
of-a-level entry timing (§34.3), and strong reinforcement of CTS-graded A+/B/C sizing + pyramiding + event-
blackout. Concentration-for-growth noted but NOT adopted (we keep caps for preservation).
**Source 35 (Quantified Edge / "AI Trader")** is mainly a **reference for the deferred LLM feature** — it
independently validates our §5/§6.4 asymmetry (**AI = thinking-tool, NOT signal-generator**) and adds craft
to fold into that spec: **feed-don't-recall data, adversarial/refute prompting, output-as-backtestable-doc,
LLM outside the live loop** (§35.1). New tool: **breakeven win-rate = 1/(1+R:R)** → makes the per-class
promotion floor a formula (`win_rate > 1/(1+R:R)`; R:R 3 ⇒ 25%) (§35.2). Reinforces the §34.1 close-based
stop via SMC's **liquidity-sweep-vs-BOS** (a wick beyond a level is a stop-hunt, not a breakout — require a
close, §35.3). SMC order-blocks/FVG deferred to v2; delta/footprint/order-flow N/A (no tick data). Possible
future: Market Profile POC/value-area (low priority).
**Source 36 (Stanzione "7 Trading Themes for 2026")** is a time-bound macro-**prediction** report → low
value / out of scope by our **no-oracle principle (§6.4)**; reinforces only wider-crypto-stops (§22.1/§34.1)
+ narrow BTC/ETH allowlist & real-utility/no-meme screening (§28.3/§33), and serves as a clean **negative
exemplar** of the price-forecasting the LLM feature must never do (§36.2→§35.1). CFDs/leverage/shorting/
options/ETFs all excluded. No action.
**Source 37 (Stanzione "How to Trade Forex")** is a **near-total duplicate** of the §23/§27 technical
content (MA-crossover / Donchian 20-day / RSI 70-30) with reused appendices — **no new content**; reinforces
the Donchian/Turtle family only. Sessions/time-of-day (§37.1) is marginal (already handled by pivot-slice
time-bucket pruning; doesn't port to 24/7 crypto). Digital options/multipliers/carry excluded.
**Stanzione/Deriv ebook stream is EXHAUSTED (23/24/26/27/36/37/38) — stop feeding Stanzione/Deriv titles;
prioritize new-technique books + the Turtle-rule build.** (Source 38 "How to Trade Stocks" = same
MA/Donchian/RSI dup; stock fundamentals/dividends N/A to crypto; reinforces no-oracle only.)
**Source 50 (Swissquote "Introduction to Stock Investing: Corporate Analysis")** is an **equity
fundamental-analysis** primer (Revenue/EBITDA/FCF/EPS/P/E/ROA/ROE/liquidity/solvency ratios) → **out of
scope / N/A to crypto** (tokens have no financial statements; same as §38.3). One reinforcement earns its
keep: its **CAPM-derived Alpha/Beta** section is a **negative exemplar** that re-grounds our **declined
CAPM/MPT** decision (spec §10, §33) — CAPM is anchored on a **risk-free rate (`Rf`) = riba**, and we already
use absolute **Sortino/drawdown + ATR/correlation sizing** instead of market-beta/alpha (§50.1). No new
rules/rails/allowlist change. **Fundamental analysis is not our lane** (we are technical/spot/long-only) —
**recommend not feeding further equity-fundamentals or valuation-ratio primers**; they're structurally N/A to
crypto. Value remains in **crypto-appropriate *technical* strategy books** + the Turtle-rule / per-class-floor build.
**Source 51 (Swissquote "Building a Diversified Portfolio")** is a **passive ETF asset-allocation** primer →
**largely out of scope**: ETFs aren't our instrument (we hold spot coins directly), **bonds/bond-ETFs +
the commodity→inflation→rates→bonds→stocks intermarket cycle are riba / no-oracle (excluded)**, a
stocks/bonds/cash allocation doesn't map to single-asset spot crypto, and **fixed-weight rebalancing is a
paradigm we don't use** (we're rule-driven; overweight is already capped by the per-asset rail). One
reinforcement earns its keep: the **"redundancy doesn't diversify"** point *sharpens* our
**correlation-adjusted-sizing rail + narrow BTC/ETH allowlist** — correlated alts are near-single exposure,
so breadth ≠ diversification (§51.1). No new rules/rails/allowlist change. **Recommend not feeding further
ETF / passive-allocation primers** — structurally off our lane (single-asset, spot, long-only, rule-driven).
**The Swissquote primer stream (39–51) has now saturated across every topic they publish** (crypto
fundamentals, options, equity fundamentals, portfolio/ETF allocation) — pause Swissquote titles; prioritize
crypto-appropriate *technical* strategy books + the Turtle build.
**Source 52 (Swissquote "Introduction to ETF and Funds Investing")** proves the saturation point — its ETF
half is a **verbatim duplicate of §51** (not re-extracted), and its new material (**ETNs = debt/riba, ETCs,
mutual funds**) is **out of scope by instrument/riba** (fund wrappers we don't trade; bond/money-market funds
= riba; value/growth = equity fundamentals N/A per §50). Only faint reinforcements: keel earns via
**capital-gains only, never distribution/yield** (riba screen), and prefers **liquid, no-lock-up spot**. No
new rules/rails/allowlist change. **Swissquote primer stream (39–52) exhausted across every topic they
publish — stop feeding Swissquote titles;** value is in crypto-appropriate *technical* strategy books + the Turtle build.
**Source 54 (Kaufman, "Trading Systems and Methods", 5th ed., 1,232pp) — THE CROWN-JEWEL SOURCE** and the
opposite of saturated: the canonical quant-trading textbook, wall-to-wall mechanical/backtestable methods.
Part-1 extraction (5 chapters) lands directly on the project's open problems and independently **validates
the trend-following pivot**: (1) the **Efficiency Ratio** (noise≠volatility, §54.1) + **market-ranking by
trendiness / ADXR / CSI** (§54.9) + the **`% profitable tests`≥~70% robustness bar** (§54.11) together give a
**data-driven answer to the ETH keep/drop/allocate question** (trade an asset only when its trendiness clears
the bar); (2) a **crypto stop/risk model** — ATR stops/targets, low-vol entry filter + high-vol exit/reset
(§54.3–4), and three volatility-adaptive trailing stops (**Kase Dev-Stop**, **ER-adaptive ATR stop**, **Parabolic
SAR** §54.6/§54.8) that attack the "stops-too-tight" defect, plus **volatility-parity sizing + volatility
stabilization** (§54.7); (3) new candidate rules — **KAMA adaptive-trend** (built on ER, for noisy crypto,
§54.5), **volatility-breakout** (§54.3), **linear-regression-slope** (§54.11); (4) a concrete **`keel simulate`
rigor upgrade** — expectations-first, information ratio, walk-forward with an **OOS/feedback firewall**,
robustness-plateau (not the max), realistic costs, drawdown-probability check (§54.10); and (5) the
headline validation — **"trend-following works; it's not the method, it's the market"** (all 5 trend methods
profitable across 17 markets), with **N-Day Breakout = highest profit factor / fewest trades / highest risk**,
proving the **Turtle's low-frequency, mostly-cash, high-per-trade profile is by design, not a bug** (§54.11).
**Recommend a part-2 pass** over Ch 5 (breakout+swing filter), Ch 8 (trend systems/bands), Ch 22 (extreme
events/price shocks), Ch 23-tail (ruin/optimal-f), Ch 24 (correlation + volatility stabilization). Excluded
per halal/no-oracle/scope: Ch 13 spreads/carry (riba), Ch 14 Elliott/Gann/astrology, Ch 6 ARIMA, Ch 20-tail
neural/genetic/fuzzy (non-reproducible). **More crypto-appropriate *technical* strategy books remain welcome —
this one is the anchor for the next build phase.**
