[← Knowledge Base index](../README.md)

## Source 50 — "Introduction to Stock Investing: Corporate Analysis" (Swissquote primer, 9pp, 03-22 EN)

> A Swissquote **fundamental-analysis** primer: definitions + formulas for the standard equity
> financial ratios, grouped as **Key measures** (Revenue, Net income, EBITDA, EBIT, FCF, Dividend),
> **Market-value measures** (Market cap, EPS, P/E, Beta, Alpha-via-CAPM), **Profitability** (ROA, ROE),
> **Liquidity** (Current ratio, Quick ratio), and **Solvency** (leverage / Debt-to-Equity /
> Times-Interest-Earned). Worked examples on Amazon/Apple. No charts-based strategy, no entries/exits.
>
> **Saturation-honest: out of scope / N/A to spot crypto — reinforcement only, nothing to build.**
> Crypto tokens have **no income statement, EPS, dividends, or balance-sheet ratios**, so the entire
> equity-valuation machinery doesn't port (same finding as [§38.3](./source-38.md)). The one earning-its-keep
> item is the **Beta/Alpha/CAPM** section — a clean **negative exemplar** re-grounding our already-locked
> decision to **decline CAPM/MPT** (built on a risk-free rate = riba). Logged to record it was seen + that
> one reinforcement.

---

### 50.1 CAPM / Alpha / Beta → reinforces the DECLINED CAPM/MPT direction (riba via risk-free rate)
The market-value section defines **Alpha** ("a strategy's ability to beat the market / excess return") and
derives it straight from **CAPM**: `R = Rf + β·(Rm − Rf) + Alpha` ⇒ `Alpha = R − Rf − β·(Rm − Rf)`, and
**Beta** as volatility-vs-market (the "most common measure of risk"). This is a **negative exemplar** for us,
not an input to adopt — it reinforces three standing project decisions:
- **CAPM/MPT already DECLINED** (spec §10; confirmed by the Islamic portfolio-optimization review
  [§33](./source-33.md)). This primer makes the *reason* concrete: CAPM is **built on a risk-free rate `Rf`**
  — a guaranteed interest return, i.e. **riba**. An alpha/beta framework that references `Rf` is riba-anchored
  at its core → stays excluded. Good citation to attach to the spec's "CAPM declined" line.
- **Our performance bar is absolute risk-adjusted return, not CAPM-alpha.** We measure edge via **Sortino /
  drawdown-preservation + beat-DCA-benchmark** (milestone-6 harness), never "alpha vs a market portfolio."
  The book's own "beat the market" framing is exactly the yardstick we deliberately replaced.
- **We use direct volatility + correlation, not market-Beta.** Position risk is scaled by **ATR ("N")
  volatility sizing** ([§27.1](./source-27.md)) and the **correlation-adjusted-sizing rail**
  ([§4.1](./source-04.md)) computed from the price series — no market index, no `β`. Crypto also lacks a
  clean "market portfolio" to regress against. So Beta adds nothing we don't already get deterministically.

**Net: no rule/rail change — it hardens the rationale for keeping CAPM/MPT/alpha-beta out.**

### 50.2 Fundamental & valuation ratios → N/A to spot crypto (reinforces §38.3)
Revenue, Net income, EBITDA, EBIT, FCF, EPS, P/E, ROA, ROE, Current ratio, Quick ratio, Total-Debt ratio,
Debt/Equity, Times-Interest-Earned — **all require corporate financial statements that crypto tokens do not
have.** There is no earnings, no equity, no inventory, no interest-coverage for a spot coin. This is the same
conclusion already reached for the Stanzione stocks ebook ([§38.3](./source-38.md)): the **equity-valuation
machinery doesn't port to spot crypto.** Two sub-notes:
- **Dividend / Times-Interest-Earned** are additionally **riba-adjacent** — the nearest crypto analog
  (staking/lending yield, interest coverage) is caught by the **riba-yield / `haram_sector` screen**
  ([§28.4](./source-28.md), [§41.1](./source-41.md)); excluded regardless of the N/A point.
- **No mechanical, testable entry/exit** anywhere in the book → **zero candidate rules** for the harness
  (contrast the crypto-appropriate priority in [[halal-cb-transcript-workflow]]).

### 50.3 Market-cap size-as-risk → faint reinforcement of the liquidity/allowlist screen (nothing new)
The one fundamentals idea with a loose crypto echo: **market-cap tiers** (large/mid/small) with *"company
size is a basic element of your analysis… the smallest companies being the riskiest."* This faintly
reinforces our **liquidity + allowlist-curation** bias toward large, liquid assets (BTC/ETH) and against
thin small-caps ([§22](./source-22.md), [§40](./source-40.md)/[§41](./source-41.md) allowlist curation) — but
it's a well-worn principle we already apply. **Reinforcement only; no admission-criteria change.**

### 50.4 Discarded (no agent value)
All ratio definitions/formulas and Amazon/Apple worked tables (equity fundamentals, not our market);
large/mid/small-cap table; Swissquote "start trading / open an account" CTA, exchange list, press-logo
marketing, and cover/education boilerplate.

---

### Net assessment (saturation-honest)
- **Out of scope / N/A** — an equity **fundamental-analysis** primer; crypto has no financial statements, so
  the valuation machinery (Revenue/EPS/P/E/ROA/ROE/liquidity/solvency ratios) doesn't port. Same finding as
  [§38.3](./source-38.md); no new rules, rails, strategy, or allowlist change.
- **One reinforcement worth keeping:** the **CAPM/Alpha/Beta** section is a concrete **negative exemplar**
  that re-grounds our **declined CAPM/MPT** decision — CAPM depends on a **risk-free rate (`Rf`) = riba**, and
  we already use absolute Sortino/drawdown + ATR/correlation sizing instead ([§50.1](#501-capm--alpha--beta--reinforces-the-declined-capmmpt-direction-riba-via-risk-free-rate)).
- **Fundamental analysis is not our lane** (technical, spot, long-only). **Recommend not feeding further
  equity-fundamentals or valuation-ratio primers** — they're structurally N/A to crypto. Value remains in
  **crypto-appropriate *technical* strategy books** and the **Turtle-rule build / per-class promotion floor**.
  See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
