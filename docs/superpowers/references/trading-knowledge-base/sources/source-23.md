[← Knowledge Base index](../README.md)

## Source 23 — "How to Trade Cryptocurrencies with Deriv" (Vince Stanzione, ebook, 56pp)

> **First ebook source** (prior 22 were transcripts). **Crypto-specific** — our exact market — and the
> author explicitly claims forex/stock/commodity techniques port to crypto ("I have been able to adapt
> trading tools to cryptos with very good results"), which is the working assumption of the whole KB.
>
> ⚠️ **Framing caveat — this is a CFD-broker promo book.** Its *entire premise* (trade crypto via
> leveraged CFDs on Deriv, never owning the coin, freely short, roll positions overnight) is **halal-
> excluded** (riba/leverage/shorting/swaps — §23.X below). We keep it only for the **mechanical,
> spot-adaptable trading systems** inside it. The high-value nugget is the **Donchian channel breakout
> system (§23.1)** — a *trend-following, buy-strength* method that is genuinely **new** to the KB and is
> the structural opposite of the "buy the oversold dip" rules that the sim just proved catch falling
> knives in downtrends (see [[halal-cb-autotrade-project]]: confluence-gate refuted, all buckets lose).

---

### 23.1 ⭐ Donchian channel breakout — the "20-day rule / 4-week rule" → `strategy/rules/` (NEW candidate rule)
Richard Donchian's channel: take the **highest high** and **lowest low** over N bars (conventionally
**20** → "20-day rule" / "4-week rule"). The band between them is the channel. The book gives it as a
complete, self-contained mechanical system (LTC/USD example):

- **Enter long when price hits the N-bar HIGH** of the channel (breakout / new-strength).
- **Exit (sell) when price hits the N-bar LOW** of the channel.
- (The book's short leg — enter short at N-bar low, cover at N-bar high — is **dropped**: long-only spot.
  For us the N-bar low is purely the **exit/trailing-stop level**, never a short entry.)

Why this matters for our current problem — it is a **trend-FOLLOWING breakout** that *buys strength /
new highs*, the exact inverse of our forex-ported pullback/RSI rules that *buy weakness / oversold dips*
and blow up by catching knives in downtrends. It directly tests the pivot hypothesis in the project memo
("source crypto-appropriate strategies … buy strength not falling knives"). The book's own stated edges
map cleanly onto what our harness rewards:
> 1. Winning trades are left to run. 2. Exact rule-based exit (no guessing). 3. Rule-based. 4. Risk always defined.
"Even with more losing trades than winning ones … you can still make money as long as the winners make
more points than the losers" — i.e. **low win-rate, high-R:R trend-following** (contrast our 55%-win
promotion floor, which is tuned for mean-reversion). ⚠️ **Promotion-gate note:** a Donchian trend
follower will likely FAIL the current `win_rate ≥ 0.55` floor by design — its edge is R:R, not hit-rate.
If we add this family, the gate needs a **per-rule-class floor** (trend-following: lower win% but higher
R:R & positive expectancy) rather than one global 55% bar. Flag for the promotion module.

**Parameterization to expose & sweep** (author says N and timeframe are free knobs — 20-day, 20-hour,
20-minute, even 10-minute; "run two systems, one long-term one short-term"):
`channel_lookback` (N bars, default 20), `entry_tf`, plus our standard risk model. **Crypto-fit:** high
— it is direction-agnostic, pure price, no forex-specific assumptions; the only adaptation is long-only
(drop the short leg) and calibrating N to crypto's timeframe. **Testability:** high — fully deterministic
(`detect()` = "close crosses prior-N-bar high"), no discretionary inputs. → **Candidate rule to validate
through the harness** (`keel simulate` / backtest→paper→promotion). Note it needs a **breakout**
`detect()`, which we don't have yet (existing rules are pullback/rsi/dca).

**20/20 variant:** overlay a **20-period SMA** inside the 20-period channel (SMA sits mid-channel); some
traders read the mid-line as an early warning of trend change. Optional confluence input, low weight.

### 23.2 N-bar channel low as a mechanical trailing stop → `execution/executor.py` (refines §19)
The channel's lower band doubles as a **structure-based trailing stop**: "place your stop at the 20-day
low … as price moves up, so does the 20-day low, and you lock in profit … trailing your stop." This is a
**simpler, fully-mechanical cousin of the §19.1 trailing algorithm** (which trails 1 ATR below each new
*structure* low on a lower TF). The Donchian-low trail is coarser but has two properties we want right now:
- It gives the trade **room to breathe** — directly addresses the sim finding that our **stops are too
  tight for crypto ATR** (losses averaging ~-2R = stops blown through). A wide N-bar-low trail is the
  book's whole answer to "close stop minimises loss vs. wide stop gives room to breathe — it's a trade-off."
- It is **monotonic toward profit** → fully compatible with the **no-stop-widening rail (§5.1)**.
Expose as an alternative `trail_mode = channel_low` with `channel_lookback`, tuned by the backtester.
Reconcile with §19.1 (offer both: fine ATR-structure trail vs. coarse channel trail).

### 23.3 RSI mean-reversion — concrete alt params to cut false signals → refines existing `rsi_meanrev`
Book's RSI guidance, useful because our current `rsi_meanrev` has **negative edge** and needs a param
sweep, not abandonment yet:
- Standard: **14 lookback, 70/30** bands (overbought/oversold).
- Author's own preference: **20 lookback** ("helps to offset false signals").
- **"Steady approach" 80/20:** 20 lookback + **80/20** bands → less sensitive, **fewer but cleaner
  signals**. Explicit trade-off stated: "too many signals, many false, vs. fewer, more accurate but
  delayed." → **Sweep candidates for the harness:** `rsi_lookback ∈ {14,20}`, `bands ∈ {70/30, 80/20}`.
  ⚠️ Long-only adaptation: **RSI>70/80 is NOT a short** — it's an **exit / don't-buy filter** for held
  or candidate longs (RSI<30/20 = the only *entry* side). Consistent with §3.3 / adaptation rules.

### 23.4 Money management — 5% fixed-fractional, proportional both ways → reinforces §4.7 / §20.3
"Max stake on a single trade **never more than 5% of account total**; if balance drops, trade size drops
proportionately; if it rises, it rises." Classic **fixed-fractional on current equity**, symmetric — this
is exactly our `money_mgmt` smooth-ratio model (§20.3) and anti-martingale posture. (Author uses 5% as a
CFD-punter ceiling; our spot risk cap stays tighter at **1%/trade** per §1.5/§22.2 — 5% noted, not adopted.)
"**No single trade should ever blow the account**" = our per-order cap + DD breaker rationale (§4.1/§10.3).

### 23.5 Drawdown asymmetry math → reinforces the preservation success-bar (§10.5 / milestone-6 verdict)
Concrete recovery table (validates why our success bar is **drawdown/Sortino preservation, not raw
return**): −10% needs +11% to recover; −25% needs +33%; **−50% needs +100%**; −80% needs +500%. "As
losses get larger, the return needed to recover rises much faster." → Direct numeric justification for
the **account-DD circuit breaker (§10.3/§20.4)** and for scoring the sim on **capital preservation**.

### 23.6 Market states & round-number S/R → reinforces `analysis/regime.py` + `analysis/levels.py`
- **Three states only:** trend-higher (HH/HL), trend-lower (LH/LL), sideways-range — maps 1:1 to our
  regime classifier (§1.2/§2.0). Trend-lower for us = **exit/stand-aside**, never short.
- **Range trading:** support = "floor" (buyers > sellers), resistance = "ceiling" (sellers > buyers);
  buy the bottom of the range. (Author sells the top → for us that's **take-profit/exit at range top**,
  not a short.) Reinforces §1.3/§4.8/§9.2.
- **Round numbers as S/R:** psychological levels (5,000 / 10,000 / 20,000 for BTC) act as support/
  resistance — reinforces the **round-number level detector (§4.8)**.
- **Market psychology:** rallies stall from *lack of new buyers*, not sellers ("fuel for the fire") —
  colour for regime/levels, not a mechanical trigger.

### 23.7 Multi-timeframe + candlesticks → reinforces (already covered)
Multi-TF analysis (1m/1h/1d for the same pair) reinforces §3.2 multi-TF bias. Candlestick OHLC anatomy
(body/wick, up/down) reinforces §1.3/§30-primitives — nothing new, standard Homma/Dow account.

### 23.8 ⛔ Halal / spot exclusions — flag explicitly (most of the book's *premise*)
The book is built on instruments/tactics our design **excludes by construction**. Recording so it's clear
what was deliberately dropped:
- **CFDs + leverage / margin** (5% margin to hold 10 coins, "capital goes further"): **riba/leverage —
  excluded** (§4.9). We trade **spot, cash-only, no margin**.
- **Short selling** (heavily promoted — "profit from falling markets", sell-first-buy-back): **excluded**
  (long-only). All short setups → **exit / don't-buy filters** per the non-negotiable adaptation lens.
- **Swap / overnight rollover charges** (23:59 GMT rollover fee on held CFDs): this is **financing
  interest = riba — excluded** (mirrors the whole of Source 18 carry/rollover). N/A to spot ownership.
- **Crypto "pairs" trades** (short BTC + long ETH; ETH/BTC, LTC/BTC crosses to "back a scenario"): a form
  of **hedging / relative-value — excluded** (§4.9). We hold single long spot positions vs. USD only.
- **Negative-balance protection / margin call mechanics:** N/A — only meaningful with leverage.

### 23.9 Discarded (no agent value)
Deriv/MT5 platform promos and feature lists; "visit Deriv Academy" CTAs; company/regulatory boilerplate
and licences; author bio & social handles; "Grain of Rice" compounding fable; affiliate-programme pitch
(Appendix E); FAQs (account opening / KYC / deposits-withdrawals); glossary (all terms already in
Source 4); news-source list (Cointelegraph/Coinmarketcap/Dailyhodl/Bloomberg — already noted as
low-weight fundamental context, §22.4; and already the exact sites named in the project's LLM
product-selection roadmap item 3); Stock-to-Flow (S2F) mention (a **prediction oracle** → excluded by the
no-oracle rule, §6.4); "trading bots" glossary blurb; synthetic-indices cross-sell.

---

### Net assessment (saturation-honest)
- **GENUINELY NEW:** the **Donchian channel breakout system (§23.1)** — a mechanical *trend-following /
  buy-strength* rule the KB lacked, and the most promising crypto-native lead for replacing the refuted
  dip-buying rules. Needs a new `breakout` detector + a per-rule-class promotion floor (low-win/high-R:R).
- **USEFULLY REFINES:** channel-low trailing stop as a *wide, crypto-appropriate* stop model (§23.2,
  addresses the "stops too tight" defect); concrete RSI sweep params 20-lookback / 80-20 (§23.3).
- **REINFORCES:** fixed-fractional sizing (§23.4), drawdown-asymmetry / preservation bar (§23.5), three
  states + round-number S/R (§23.6), multi-TF + candles (§23.7).
- **EXCLUDED:** the book's entire CFD/leverage/short/swap/pairs premise (§23.8).
- **Next action:** prototype the Donchian breakout rule (long-only, channel-low trail) and run it through
  `keel simulate` on the cached 5yr data — this is a concrete deliverable for roadmap direction #1
  (fix negative-edge strategies) sourced from roadmap direction #2 (the book pipeline).
