# Trading Knowledge Base — Sourced & Adapted for `halal-cb`

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
| — | "Trading Terminology Explained" (re-paste) | ⧉ duplicate of Source 4 | see [source-04](./sources/source-04.md) |

---

## Module map
Thematic view — which agent module each theme feeds, and where it's sourced.

| Agent module | Theme | Key sections |
|---|---|---|
| `analysis/candles.py` | Candlestick primitives (pin/doji/tweezer/marubozu/3-bar/rejection); V-top/double-bottom (full rules: RSI-extreme + shallow-pullback>0.382 + divergence) | §1.3, §2.1, §7.2, §8.2, §16.1, §21.1 |
| `analysis/levels.py` | S/R: horizontal/angular/round-number/gaps/magnet; ≥3 touches | §1.3, §4.8, §7.3, §9.2 |
| `analysis/regime.py` | Market condition + phase (run/pullback), choppy = no-trade | §1.2, §2.0 |
| `analysis/indicators.py` | EMA / RSI (+divergence) / MACD / ATR / Fib retrace+extension / deceleration | §1.4, §1.5, §3.1, §3.3, §4.4 |
| `analysis/insights.py` | AI behavioral/seasonality analysis; edge-decay detection; BTC cycle/seasonality context; seasonality as low-weight CTS factor; pivot-slice auto-pruning of weak pair/day/time/rule buckets | §6.1, §6.2, §6.3, §12.3, §14.3, §20.7, §21.2 |
| `analysis/pnl.py` | FIFO P&L, drawdown / time-in-drawdown, recovery table | §4.6, §10.5, §2.4 |
| `strategy/rules/` | Parameterized pullback-continuation family; RSI mean-reversion; DCA/dip-buy; deferred (harmonics incl. full Gartley, Fib-inversion, BTC macro-cycle) | §2.1, §3.3, §7.1, §9.3, §12.1, §15.2 |
| `strategy/engine.py` | Identify→Predict→Decide→Execute; **CTS scoring → rail-bounded execution**; 4 graded entry techniques; kill-zone entry gate; multi-TF bias; news filter | §2.0, §3.2, §3.6, §8.1, §9.1, §17.1, §17.2 |
| `strategy/backtest.py` | Backtest, intrabar order-of-events, spread/slippage modeling, MFE/MAE, no-overlap realism | §1.7, §2.3, §4.2, §20.2, §20.5 |
| `strategy/promotion.py` | Paper gate, promotion/demotion, expectancy + R:R≥1.5–2 & win≥55%, min-sample (100 trades/5yr), edge decay | §1.6, §4.5, §5.2, §6.3, §20.6 |
| `strategy/money_mgmt.py` | Smooth-ratio sizing ramp (profit-trigger + acceleration) bounded by total & weekly DD caps; fixed-fractional on current equity | §4.7, §20.3, §20.4 |
| `execution/executor.py` | Order lifecycle (close-validate, one-candle validity), OCO/bracket, partial exits, buffers, ATR stops, trailing-stop algorithm | §2.2, §3.5, §7.4, §8.2, §17.3, §19.1 |
| `execution/guards.py` | **The hard rails** (see below); crypto-volatility calibration; API-key security / no-withdrawal scope / custody risk | §4.1, §5.1, §8.1, §10.3, §10.4, §22.1, §22.3 |
| DB schema | transactions/candles/orders/rules/signals/backtests/pnl_daily/agent_state; journal fields | §1.7, §2.5, §4.6 |

### The hard rails (un-overridable, incl. bypass mode)
Base four (design): allowlist · per-order & daily $ caps · sell-only-on-rule · kill-switch + audit.
Added from transcripts: total-exposure cap (§4.1) · correlation-adjusted sizing (§4.1) ·
min-move/anti-scalping (§4.1) · no-averaging-into-losers (§5.1) · no-stop-widening (§5.1) ·
account-drawdown circuit breaker — total **and** weekly (§10.3, §20.4) · per-asset concentration cap (§10.3) ·
stale-data/feed-health guard (§10.4). CTS dynamic sizing moves **only within** these rails (§8.1, §10.6).

### Explicit exclusions (halal / spot)
Carry trades (**entire Source 18 excluded as riba**, §18), hedging, bonds, margin/leverage/lots, short-selling, scalping — see §4.9, §10.10, §18.

---

## Open judgment calls (for user review)
Decisions I made that are worth confirming/overriding:
1. **Long-only translations** of short setups → exit/don't-buy filters (§2.1, §7.1, §9.3).
2. **Deferrals to v2:** harmonics (Gartley/ABCD/Cipher), Fibonacci Inversion, Wall-Street-cheat-sheet
   macro-cycle (§3.4, §8.4, §9.3–9.4).
3. **Rule consolidation:** Sources 2 & 7 merged into one parameterized rule (§7.1).
4. **New rails beyond the original four** (see hard-rails list above).
5. **Riba exclusions:** carry/hedging/bonds/margin (§4.9, §10.10).
6. **CTS dynamic aggressiveness** bounded by rails (§8.1 ⚠️, §10.6).

## Status
11 trading sources + 1 out-of-scope. Content has **saturated** on structure; recent sources
mostly refine/rename/reinforce. Next step (pending user): fold into the finalized design spec.
