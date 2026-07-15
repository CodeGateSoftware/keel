[← Knowledge Base index](../README.md)

## Source 10 — "14 Types of Risk / Risk Management"

> The most rails-relevant source. Core thesis: **"making money is a byproduct of managing risk";
> the pro asks 'how much can I lose?' first.** This *is* our rails-first design philosophy.

### 10.1 The 14-risk taxonomy → coverage map against our rails (gap check)
| # | Risk | Covered by | Gap? |
|---|---|---|---|
| 1 | Company / single-asset | allowlist + per-asset concentration cap | add **per-asset cap** (10.3) |
| 2 | Sector | correlation rail (§4.1) — crypto = one sector | ✓ |
| 3 | Time (timing vs time-in-market) | DCA rule (design) — buy regularly | ✓ (validates DCA) |
| 4 | Asset | PAXG (gold) as all-weather; **bonds excluded = riba** | ✓ |
| 5 | Trade (position size) | 1% fixed-fractional + per-order cap | ✓ |
| 6 | Drawdown | max-DD tracking + recovery table (10.5) | add **account-DD circuit breaker** (10.3) |
| 7 | Correlation | correlation-adjusted sizing (§4.1) | ✓ |
| 8 | Capitalization | speculation cap; % returns not $ (10.7) | ✓ |
| 9 | Gambling | deterministic rules only; no-deviation (10.6) | ✓ |
| 10 | Volatility | ATR + cash buffer + <10% fluctuation (10.3) | add **account-DD breaker** |
| 11 | News | event-blackout filter (§3.6) | ✓ (event list 10.9) |
| 12 | Margin call | N/A — **no leverage/margin = riba-excluded** | ✓ (excluded) |
| 13 | Execution | automated executor + sanity bounds; caps | ✓ |
| 14 | Technology | error handling (design §10) | add **stale-data/feed-health guard** (10.4) |

Result: our 9 rails already cover most of the space. **Three additions** identified below.

### 10.2 (see 10.1) — core validation
Every remedy the source gives (predefined % risk, diversification, checklist, avoid news, never
get margin-called) is something our design already does. Strong end-to-end validation of rails-first.

### 10.3 NEW rails → `execution/guards.py`
- **Account-level drawdown circuit breaker.** Keep net-liquidity fluctuation **< ~10%**; if total
  account drawdown from peak breaches a configured threshold, **auto-halt new entries** (like an
  automatic kill-switch) until reviewed. Portfolio-level complement to per-trade risk (risks #6/#10).
- **Per-asset concentration cap.** Cap the **% of portfolio in any single allowlist asset** (risk #1),
  separate from the aggregate exposure cap (§4.1). Prevents one asset dominating even within the allowlist.
- Both are hard, config-driven, un-overridable — consistent with the user's rails decision.

### 10.4 NEW operational guard — stale-data / feed-health (risk #14) → poller + executor
Technology risk for an automated agent = acting on **stale or delayed market data**. Guard: before
evaluating/trading, verify the latest candle/price is **fresh** (within an expected age) and the API
is healthy; if the feed is stale or erroring, **skip the cycle and alert** — never trade on stale data.
Extends design §10 error handling into an explicit precondition.

### 10.5 Drawdown recovery table → encode as a risk-awareness output → `analysis/pnl.py`
The asymmetry, quantified (surface in reports so a drawdown's true cost is visible):
`5%→+5.26%`, `10%→+11.11%`, `15%→+17.64%`, `25%→+33%`, `50%→+100%`, `75%→+300%`, `90%→+900%`.
This is *why* the account-DD breaker (10.3) and 1% sizing matter — big losses need exponential recovery.

### 10.6 "Definition of gambling" → the NO-DEVIATION principle (strong, precise)
The source lists what counts as gambling: a non-signal trade that "looks good"; **not** tightening the
stop as the strategy says; risking **more** than the strategy permits; **risking LESS than the strategy
permits**; trading at prohibited times; anything not in the *documented, printed* strategy. → Principle
for the agent: **execute the documented rules EXACTLY — deviation in *either* direction (more OR less
risk) is an error.** Notably, arbitrary **under-sizing is also a violation**, which bounds the §8.1/§9.1
CTS "dynamic aggressiveness": size varies *only* per the documented CTS ladder, never ad hoc. A mechanical
agent enforces this structurally — this is its core advantage.

### 10.7 Portfolio allocation by risk tier + "% not $" → speculation cap (extends §2.8)
Allocate liquidity by speculation level (e.g. index-fund heavy, tiny % to higher-speculation crypto/FX),
and **judge performance in % ROI, not $ P&L** ("hide your P&L"). For us: the trading portfolio is a
small, funded, losable slice (design's dedicated-portfolio scoping); reporting leads with **% ROI**.

### 10.8 DCA validates the design's DCA rule (risk #3)
"Buy the market regularly like groceries, not perfume (Graham/Buffett); time-in-market > timing." →
Directly validates keeping the **DCA/dip-buy** rule in the library as the low-speculation backbone.

### 10.9 News-event list (reinforces §3.6) → event-blackout filter
Specific high-impact events to avoid trading around: **NFP, FOMC, ECB pressers, major interest-rate
decisions, elections**. Crypto-relevant subset: FOMC/CPI/rate decisions move crypto; add crypto-specific
events later. Concretizes the deferred §3.6 filter's event list.

### 10.10 Explicit exclusions (halal/spot)
**Bonds** (interest = riba), **margin/leverage & margin-call mechanics** (riba), forex lot/pip specifics —
excluded, consistent with prior sources.

### 10.11 Reinforced
Drawdown asymmetry (§5.1/§4.6), correlation rail (§4.1), 1% sizing (§1.5), checklist/own-your-rules
(§7.6), news filter (§3.6), cash buffer / speculation cap (§2.8), probability-not-prediction.

### 10.12 Discarded from Source 10 (no agent value)
Wealth-Atlas/"Always Free" book & tool promos, all-weather-portfolio investing tangent (out of scope
for the trading agent), generic hardware-shopping tips, CTAs.


