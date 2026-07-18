[← Knowledge Base index](../README.md)

## Source 51 — "Building a Diversified Portfolio" (Swissquote primer, 19pp, 03-22 EN)

> A Swissquote beginner primer on **passive portfolio construction**: (1) the concept of ETFs
> (bond/stock/industry/commodity ETFs); (2) why ETFs suit beginners (variety, liquidity, low fees, tax);
> (3) assessing **risk tolerance** (conservative→aggressive investor profiles with stocks/bonds/cash mixes
> and their historical downside ranges); (4) **asset allocation** (% across stocks/bonds/cash by profile;
> diversification by asset-class/country/industry/size; the commodity→inflation→rates→bonds→stocks
> intermarket cycle; a worked conservative allocation heavy in bond ETFs); (5) **reassessing / rebalancing**
> weightings + the danger of over-diversification. Ends on the Swissquote CTA.
>
> **Saturation-honest: largely out of scope — reinforcement + exclusions, nothing to build.** Its core
> subjects fall outside our mandate — **ETFs are not our instrument** (we hold spot coins directly),
> **bonds/bond-ETFs = riba** (excluded), a **stocks/bonds/cash allocation** doesn't map to a
> **single-asset-class spot-crypto** book, and **passive fixed-weight rebalancing** is a different paradigm
> from our rule-driven trading. The one part that earns its keep is the **diversification-by-correlation**
> material, which *sharpens the rationale* for two rails we already have. Logged for that + to record the
> exclusions.

---

### 51.1 Diversification-by-correlation + over-diversification → sharpens the correlation-sizing & per-asset-cap rails (no new rail)
The book's diversification chapter is the transferable part, and it *reinforces* (doesn't add to) our rails:
- **"Diversify across things that move differently — some move with each other, some against"** → this is
  exactly the thesis behind our **correlation-adjusted-sizing rail** ([§4.1](./source-04.md)) and the
  **per-asset concentration cap** ([§10.3](./source-10.md)). No change; good grounding cite.
- **NEW nuance worth keeping — "redundancy doesn't diversify":** *"You can hold five ETFs that all track the
  S&P 500 and be no more diversified than holding one… too many funds in the same securities = less
  diversification, not more."* Ported to crypto this is a **sharpening of the correlation rail's rationale**:
  most alts are **highly correlated with BTC and with each other**, so a "diversified" basket of alts is
  largely **redundant exposure** — the correlation rail must treat a cluster of correlated coins as
  **near-single-exposure** when sizing, and it reinforces the **narrow BTC/ETH allowlist**
  ([§40](./source-40.md)/[§41](./source-41.md)) over chasing breadth. Reinforces, doesn't create, a rail.
- **Diversify by size (large vs small cap)** → faint echo of the liquidity/allowlist bias (large = safer,
  small = riskier), already applied ([§50.3](./source-50.md), [§22](./source-22.md)). Nothing new.

### 51.2 Risk-profile → exposure scaling; capital preservation → reinforces money-mgmt + the success bar
The conservative→aggressive **investor-profile** framework (more cash / less risk-asset = smaller downside;
the book shows conservative −8% vs aggressive −36% worst-case) maps loosely onto our **configurable risk
model** (risk_pct, per-order/daily caps, monthly allowance, DD breakers) and the **capital-preservation
success bar** (Sortino/drawdown over raw return). Two reinforcements, no new mechanism:
- The idea that **holding cash is a legitimate risk-lowering stance** (*"a beginner may want to exit a losing
  investment immediately to preserve limited capital"*) reframes keel's current **mostly-cash / under-deployed
  posture** as *preservation*, not pure defect — consistent with beating DCA on drawdown, not on raw return
  (milestone-6). Still worth raising deployment via the strategy work, but cash-heavy ≠ wrong.
- A **user-selectable risk profile** (scaling risk_pct / caps as a named "conservative/moderate/aggressive"
  preset) is a plausible **config convenience** later, but it's just presets over the existing rails — not a
  new capability. Note only.

### 51.3 ⛔ Excluded / N/A (instrument & riba)
- **Bonds & bond ETFs** (government, corporate, HY, IG, Treasury-bond ETFs, "German Bund", the whole 55%-bonds
  conservative allocation) → **riba (interest instruments) — excluded by mandate.** The book's "balance HY
  with IG," "compensate with longer-maturity bonds," and **"hedging by keeping an IG exposure"** are also
  **hedging + fixed-income = excluded** ([§4.9](./source-04.md), [§18](./source-18.md), [§28.1](./source-28.md)).
- **ETFs as an instrument** → **outside our mandate.** keel trades **spot crypto directly (you own the coin)**;
  an ETF is a fund share / basket wrapper, not direct spot ownership. A crypto **index ETF** would also fail
  the spot-ownership test and add a fund layer — not adopted. (Note: this is an *instrument* exclusion, not a
  shariah ruling on equity ETFs in general.)
- **Intermarket cycle** (commodity prices → inflation → interest rates → bond prices → stocks) → **macro
  prediction = our no-oracle principle** ([§6.4](./source-06.md)); additionally rate/bond-based (riba). A
  clean **negative exemplar** of the top-down forecasting the agent must never trade on. No action.
- **Passive fixed-weight rebalancing** ("rebalance back to target stock/bond/cash %") → a **different paradigm**
  from keel's **rule-driven entries/exits**. We don't hold a fixed allocation to rebalance to; the "trim an
  overweight position" impulse is already served by the **per-asset concentration cap** ([§10.3](./source-10.md)).
  Not adopted as a mechanism.
- **Lower-fees / tax-efficiency / mutual-fund comparison** → platform/fund-structure marketing; fee modeling
  is already handled in `backtest` ([§20.2](./source-20.md)). Nothing new.

### 51.4 Discarded (no agent value)
ETF definitions and the four ETF-type tables; ETF-growth chart; beginner-suitability pitch; the
liquidity/fees/tax sub-sections; region/industry home-bias maps; the Mr-Warren rebalancing example; Swissquote
"open an account" CTA, exchange list, and press-logo marketing.

---

### Net assessment (saturation-honest)
- **Largely out of scope** — a **passive ETF asset-allocation** primer. ETFs aren't our instrument, **bonds /
  bond-ETFs / HY-IG / the intermarket cycle are riba or no-oracle (excluded)**, a stocks/bonds/cash allocation
  doesn't map to single-asset spot crypto, and **fixed-weight rebalancing is a paradigm we don't use**.
- **Reinforcement that earns its keep:** the **diversification-by-correlation** + **"redundancy doesn't
  diversify"** idea *sharpens* our **correlation-adjusted-sizing rail** and **narrow BTC/ETH allowlist**
  (treat correlated alts as near-single exposure — [§51.1](#511-diversification-by-correlation--over-diversification--sharpens-the-correlation-sizing--per-asset-cap-rails-no-new-rail));
  and the risk-profile/preservation framing reinforces the money-mgmt caps + Sortino/drawdown success bar.
- **No new rules, rails, strategy, or allowlist change; no candidate rule** (no mechanical entry/exit anywhere).
- **Recommend not feeding further ETF / passive-allocation / asset-allocation primers** — structurally off our
  lane (single-asset, spot, long-only, rule-driven). Value remains in **crypto-appropriate *technical* strategy
  books** + the **Turtle-rule / per-class-floor build**. See [[halal-cb-autotrade-project]],
  [[halal-cb-transcript-workflow]].
