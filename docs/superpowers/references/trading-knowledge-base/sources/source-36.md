[← Knowledge Base index](../README.md)

## Source 36 — "7 Trading Themes for 2026: A Special Report for Deriv Traders" (Vince Stanzione, ebook, 36pp)

> Fifth Stanzione / Deriv ebook (23/24/26/27/36). **Different genre: a time-bound macro-PREDICTIONS /
> market-outlook report** — 7 forecasts for 2026 (US rates→zero, gold/silver up, oil bearish, Mag-7 tops
> out, global-stock bargains, crypto "trader's market", USD rebounds).
>
> **This is fundamentally a "prediction oracle" piece — the exact thing our design rejects (§6.4).** Our
> agent never encodes or acts on directional forecasts ("BTC will fall 70%", "ETH → $500"). So the report
> is **out of scope by principle**, on top of being dated and mostly non-crypto. **Low value.** The only
> keepers are reinforcement (§36.1) plus a meta-use: it's a clean *exemplar of what the LLM feature must
> NOT do* (§36.2). Everything else discarded.

---

### 36.1 Reinforcement (the only usable content — all from Theme 6, crypto)
- **Wider stops for crypto whipsaws:** *"Keep position sizes small, and use **wider stops to survive
  whipsaws** … volatility will remain very high."* → reinforces our **crypto-ATR-scaled / close-based
  stops** direction (§22.1, §27.1, §34.1) that targets the "stops too tight for crypto" defect. Nothing
  new — but independent confirmation that wide, volatility-appropriate stops are the crypto norm.
- **Stick to BTC/ETH majors; avoid alts/meme coins:** *"Avoid almost all altcoins and meme coins — many
  already down 90%+ … as the market matures, investors will demand **real utility, credible teams, genuine
  use cases** … expect widespread delistings."* → reinforces our **narrow, liquid-majors-only allowlist**
  (BTC/ETH/PAXG) and the **real-utility / `haram_sector` screening** at allowlist admission (§28.3, §28.4,
  §33) — favor genuine-utility assets, exclude pure-speculation/meme tokens. Independent validation of the
  conservative allowlist posture.
- **Crypto volatility/drawdown context:** *"60–80% crashes are completely normal in Bitcoin's history";
  ETH "positive in just 54% of months (2017–2025)."* → reinforces crypto-volatility calibration (§22.1) and
  the **drawdown-preservation success bar** (milestone-6). Marginal.

### 36.2 ⭐ Meta-use — a textbook example of the forecasting our system refuses (§6.4 / §35.1)
The report *is* directional prediction: "gold up 20–25%", "BTC 70–80% drawdown to ~$24k", "ETH loses a
zero to $500", "USD roars back." → It's a useful **negative exemplar** for the deferred LLM feature: this
is precisely the **"signal generator / price predictor" mode the LLM must never operate in** (§35.1) and
the **prediction-oracle the deterministic engine never encodes** (§6.4). If/when the LLM feature ingests
such a thematic report, its role is to **extract candidate *products/narratives* → hand to the
deterministic screen** (halal + liquidity + 5yr-data + backtestable), **never to adopt the price call.**
Cite as the canonical "don't do this" alongside §35.1.

### 36.3 ⛔ Excluded / out of scope (the bulk)
- **The prediction premise itself** — all 7 themes are dated 2026 directional forecasts → **no-oracle
  (§6.4)**; not encoded.
- **CFDs / leverage / shorting** — *"go both long and short", "stop-and-reverse: short → long → short",
  "Deriv CFDs", "use leverage"* → **excluded** (riba/gharar/shorting). Our crypto exposure is **long spot
  only.**
- **Options** — *"long-dated options on ETFs (IBIT, ETHA), puts/calls"* → **excluded** (not spot,
  maisir/gharar, §27–28).
- **ETFs & equities** (GDX/GLD/SLV/IBIT/ETHA/XPR, MSTR, Mag-7) → out of scope (not spot crypto).
- **Non-crypto themes** (Themes 1–5, 7: rates, gold/silver, oil, US/global stocks, USD) → not our market.
- **BTC/gold ratio** framing → minor analytical note; we trade vs USD, not actionable.

### 36.4 Discarded (no agent value)
Deriv/CFD marketing & disclaimers; author bio; all macro-forecast narrative; specific ETF holdings tables;
"Trump bump" commentary; time-stamped price targets.

---

### Net assessment (saturation-honest)
- **Low value / out of scope by principle** — a time-bound macro-**prediction** report, the antithesis of
  our deterministic no-oracle design (§6.4). No new mechanical/backtestable content.
- **REINFORCES (only):** wider crypto stops (§22.1/§34.1), narrow BTC/ETH-majors allowlist + real-utility/
  no-meme screening (§28.3/§28.4/§33), crypto volatility/drawdown norms (§22.1).
- **Meta-use:** a clean **negative exemplar** of the price-prediction mode the LLM feature must avoid and
  the engine must never encode (§36.2 → §6.4/§35.1).
- **EXCLUDED:** the forecasts themselves; CFDs/leverage/shorting; options; ETFs/equities; non-crypto themes.
- **No action.** The **Stanzione/Deriv ebook stream remains saturated** (now spanning strategy 23/24/26/27
  + this outlook piece 36) — recommend continuing to prioritize **new-technique strategy books** and the
  **Turtle-rule build**. See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
