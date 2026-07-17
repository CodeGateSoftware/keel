[← Knowledge Base index](../README.md)

## Source 33 — "A Review on Portfolio Optimization Models for Islamic Finance" (Lim, Goh & Sim, AIMS Mathematics, 2023, 28pp)

> Sixth compliance-adjacent source, but a **different angle**: an academic review of **portfolio
> optimization** (Markowitz mean-variance / MPT and its many modifications) with a Shariah overlay.
>
> **Key framing:** the project has **already DECLINED MPT / mean-variance / the quant-optimization stack**
> ("CAPM/MPT = riba"; NumPy/Pandas/statsmodels/ARIMA/CAPM/MPT all declined — spec §10; see
> [[halal-cb-autotrade-project]]). So the paper's core subject is a **road we've deliberately not taken** —
> we size positions with deterministic hand-rolled rules + rails, not optimization. **This paper both
> validates that decline AND surfaces one genuinely NEW, actionable halal item the compliance papers
> (28–32) missed: zakat (§33.1)** — a *positive* obligation, not an exclusion.

---

### 33.1 ⭐ Zakat — the 2.5% annual wealth-purification obligation → NEW: optional reporting feature
The compliance sources so far covered only **prohibitions** (what you can't do). Zakat is the first
**positive** obligation: an annual purification levy, one of the five pillars. For investments (the paper,
§2.5, per Bursa Malaysia practice):
- **Short-term / appreciation-seeking investor** (= our agent's profile — we trade for price appreciation):
  **2.5% of the portfolio's market value per lunar year (hawl).**
- Long-term dividend investors: 2.5% on dividends + a one-time payment (different basis).
- Also the general purification principle: any incidental **impermissible income** (e.g. stray exchange
  interest) should be **given to charity** to purify it (echoes the source-29 penalty→charity note).

→ **Actionable as an OPTIONAL halal-completeness feature** (not a trading rail): the agent already tracks
portfolio market value + FIFO P&L (`pnl.py`, DB) and journals — so it can **compute & report an estimated
zakat liability (~2.5% of market value per lunar year)** as a report line, the way it reports P&L. Keeps
the system "fully halal" end-to-end (compliant *earning* + the *purification* obligation). **Default: a
report/estimate only** — the agent never moves money to pay it (the user does); flag as a
`CompliancePolicy`/reporting enhancement, not a rail. (⚠️ zakat calculation has genuine scholarly nuance —
lunar-year timing, nisab threshold, valuation basis — so surface it as an *estimate + "consult a scholar"*,
consistent with the conservative-interpretation stance §29.2.)

### 33.2 Reinforces existing choices (no change)
- **Short selling forbidden** — *"the sale of items not owned is deemed gharar; short selling is forbidden
  in Islamic finance"* → another independent grounding for **long-only** (gharar of selling what you don't
  own), alongside the ownership principle (§28.2, §30.1). (The paper notes "Regulated Short Selling" is
  *argued* by some to be compliant — we don't touch it; conservative stance.)
- **Downside risk over variance** — the paper reviews **mean-semi-variance / downside-risk** models, noting
  plain standard deviation *"measures both positive and negative gains"* (you only care about downside) →
  **validates our success bar's use of Sortino / drawdown-preservation** over raw variance (memory:
  "beat DCA on drawdown/Sortino"). CVaR is reviewed as another downside measure (we use drawdown/Sortino;
  CVaR noted, not adopted — quant stack declined).
- **Cardinality / holding constraints** (cap the number of assets & max weight each) → maps to our
  **per-asset concentration cap (§10.3)** + allowlist sizing; *"a smaller asset universe could create a
  better setting for diversification"* → supports a **focused allowlist** over a sprawling one.
- **Correlation-based diversification** (the MPT premise) → reinforces the **correlation-adjusted sizing
  rail (§4.1)** — but we do it *deterministically*, not via mean-variance optimization.

### 33.3 Validates the project's decline of MPT / mean-variance (do NOT adopt)
The paper catalogs MPT's well-known drawbacks (estimation error in the covariance matrix, instability of
optimal weights, single-covariance dominance producing poorly-diversified "optimal" portfolios,
computational cost) and stresses that Shariah portfolio models must **exclude the risk-free asset** (its
rate = riba). → Two takeaways: (a) **confirms the CAPM/MPT-riba concern** (the risk-free rate is the riba
touchpoint); (b) the practical fragility of mean-variance optimization **supports our choice of simple,
robust, deterministic sizing + rails** over an optimizer. **No adoption** — this is a declined direction.

### 33.4 Out of scope / discarded
The mathematical review substance: Markowitz MV formulation; post-modern PT; higher-moment (skewness/
kurtosis) models; CVaR/robust/fuzzy/interval optimization; heuristic solvers (GA/PSO); computational-
efficiency discussion; the Islamic-finance principles primer (riba/gharar/maysir/haram — restates §28);
market-growth commentary; references. All either declined-direction (optimization) or covered by §28.

---

### Net assessment (saturation-honest)
- **NEW & actionable (small):** **zakat** (§33.1) — the first *positive* halal obligation vs the
  exclusion-only view; add an **optional zakat-estimate report** (~2.5% of portfolio market value/lunar
  year) so the system is halal end-to-end (earn compliantly + report the purification duty). Report-only;
  never auto-pays; surface with scholarly-nuance caveat.
- **REINFORCES:** long-only (short = gharar); Sortino/downside-risk success bar over variance; per-asset
  cap + focused allowlist; correlation-diversification (done deterministically).
- **VALIDATES a prior decision:** MPT/mean-variance stays **declined** (risk-free-rate = riba; optimizer
  fragility) — we keep deterministic sizing + rails.
- **Compliance stream still exhausted for *exclusions*** (28→32); this added one *positive*-obligation item
  (zakat) + optimization validation. **Recommendation unchanged: pause Islamic-finance papers**; value now
  is **building the Turtle breakout rule** (§27.1/§25.1) or **new-technique strategy books**. See
  [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
