[← Knowledge Base index](../README.md)

## Source 32 — "Developments in Risk Management in Islamic Finance: A Review" (Al Rahahleh, Bhatti & Misman, J. Risk Financial Manag. 2019, 23pp)

> Fifth compliance source (28–32), and — like [Source 31](./source-31.md) — an academic **literature
> review of risk management in Islamic BANKS** (credit / liquidity / market / operational / rate-of-return
> / equity-investment / displaced-commercial risk; empirical studies; VaR & stress-testing *by banks*).
> **Bank-level financing risk, not trading risk** → out of scope for our spot agent, whose risk model
> (14 hard rails, ATR sizing/stops, DD breakers, correlation sizing, Sources 1–27) is independent.
>
> **Saturation-honest: the compliance stream is exhausted.** One minor vocabulary formalization (§32.1);
> everything else restates §28–31 or is bank-empirical and discarded.

---

### 32.1 Negative vs positive Shariah screening — vocabulary for what CompliancePolicy already does
The paper frames shariah risk-identification as a **two-step screen**:
1. **Negative screening** — *"excludes Riba-, Gharar-, and Maysir-based transactions"* (done by the bank's
   Shariah board). → This is **exactly our `CompliancePolicy`** (§28): exclude leverage/derivatives/options
   (riba/gharar) + binary options (maisir) + the `haram_sector` allowlist screen (§28.4). Useful framing:
   **our code IS the "negative screen"** — the deterministic, automated equivalent of a Shariah board's
   exclusion pass, applied before any trade/allowlist admission.
2. **Positive screening** — *"emphasizes justice, ethics, and accountability."* → a softer ESG-like overlay
   (favoring ethical/just enterprises). **We don't do positive screening** — it's subjective and beyond our
   deliberately conservative, mechanical scope. Noted as **out of current scope** (a possible future
   CompliancePolicy overlay, aligns loosely with the §28.3 "favor real-utility assets" curation note).

### 32.2 Reinforced / out of scope (the substance of the paper)
- **Shariah non-compliance risk** as a named risk category → our CompliancePolicy *is* the mitigation of it
  (screen before executing); nothing to add beyond §28.
- **Bank risk taxonomy** (credit, liquidity, market, operational, rate-of-return, equity-investment,
  displaced-commercial risk) + empirical findings (IBs vs conventional banks; liquidity↔credit-risk
  relationships; VaR/stress-testing adoption by banks) → **bank-financing risk, orthogonal to trading**;
  discarded. Riba/gharar/maysir definitions, PLS/mudharabah/musharakah/takaful context → restate §28/§31.

---

### Net assessment (saturation-honest)
- **Out of scope + fully saturated.** Only takeaway: the **negative/positive-screening vocabulary**
  (§32.1) — confirms our `CompliancePolicy` is the automated "negative screen"; "positive screening"
  (ethics/ESG) noted as out of conservative scope. **No new rules, rails, strategy, or allowlist change.**
- **The Islamic-finance / compliance source stream (28→32) is exhausted.** Five sources have thoroughly
  grounded riba (al-fadl/al-nasee'ah)/gharar/maisir/ghorm, spot-settlement, haram-sector screening, AAOIFI,
  scholarly divergence, and now the negative/positive-screening framing. **Further compliance papers add
  nothing — recommend stopping this stream.**
- **Where value is now:** (1) **build the specified Turtle breakout rule** (§27.1/§25.1: Donchian 20/10 +
  ADX>25 + ATR sizing/~2×ATR stop + per-class R:R floor) and validate via `keel simulate`; (2)
  **new-author / new-technique strategy books** (not more Stanzione/Deriv or Islamic-finance titles). See
  [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
