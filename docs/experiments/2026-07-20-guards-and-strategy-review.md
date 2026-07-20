# Guards & Strategy Review against KB sources 58–74 — 2026-07-20

Every finding below was checked against the **code**, not against the KB's description of the code.
Nothing here has been changed yet; this is the review, with a ranked change list at the end.

---

## A. Defects found

### A1. 🔴 `TREND_FOLLOW` relaxed `min_trades` 100 → 30, and the evidence says that is backwards

`strategy/promotion.py:62-66`:

```python
TREND_FOLLOW: PromotionConfig(
    min_trades=30,          # ← relaxed from the default 100
    min_win_rate=0.30,      # ← correctly relaxed (§25.5 breakeven-winrate)
    ...
)
```

The per-class floor was introduced to fix a **win-rate** miscalibration: a trend-follower legitimately wins
under half its trades, so the flat 55% bar was wrong for it (§25.5). **That reasoning is sound and the
`min_win_rate=0.30` relaxation is correct.**

But `min_trades` was relaxed **at the same time**, and two independent lines of evidence now say the
sample-size axis needed no relaxation at all — it needed *tightening*:

| Source | Trades required |
|---|---:|
| 2026-07-20 random-entry experiment (empirical) | **~68** |
| §73.3 MinBTL at N=26 (theoretical) | **~68** |
| §73.3 MinBTL at our real N≈336 | **~143** |
| **Current `TREND_FOLLOW` floor** | **30** |

**The two axes are independent.** Win-rate and sample size were relaxed together as if they were one
concession; only the first was justified. At `min_trades=30` the gate can pass a rule on roughly **half**
the sample its own edge would need to be distinguishable from random entries.

This is not hypothetical: the Turtle currently produces **30 pooled trades** — it clears this floor *exactly*,
and would fail a correctly-calibrated one.

### A2. 🔴 The allowlist is juristically homogeneous, but the assets are not (§71.4a)

`config.yaml`:
```yaml
allowlist: [BTC, ETH, PAXG]
```
`guards.py:231`: `if asset not in config.allowlist`.

A flat list of strings, no per-instrument metadata. But SC Malaysia's `ribawi`-backing classifier (§71.4a)
puts these in **two different juristic regimes**:

| Asset | Backing | Classification | Regime |
|---|---|---|---|
| BTC, ETH | none | `urudh` (trade goods) | ordinary `bay' mutlaq`, **expressly exempt from `sarf`** |
| **PAXG** | gold | **currency** | **`bay' al-sarf`** — stricter |
| **USDC** (quote) | fiat | **currency** | **`bay' al-sarf`** — stricter |

**One policy cannot correctly govern both.** Today it does. Note the practical exposure is currently
limited — we already settle spot and immediately, which satisfies the strict branch — so this is a
**correctness-of-reasoning** gap rather than a live violation. But it becomes a live gap the moment a
same-type swap appears (USDC↔USDT), where `sarf`'s parity branch binds and `bay' mutlaq` does not (§71.3).

### A3. 🟠 No withdrawal-capability guard (§65.4, §71.5)

`grep -rn "withdraw" keel/` returns only the CLI's `record-flow` (declaring deposits so rail 11 doesn't misread
them as P&L) — unrelated.

`qabd` is now triangulated **four times** (§65.4 · §66.2 · §67.1 · §71.5), and all four converge on
**possession = the ability to dispose, not physical custody**. AAOIFI SS No.18 §3/5's second condition is
literally *"ability of the possessor to undertake transactions in them."*

So **losing withdrawability breaks the possession our entire spot-settlement claim rests on** — and nothing
detects it.

There is already a clean pattern to copy: rail 13 injects a broker-derived fact via
`OrderIntent.available_quote` and **fails closed on `None`** (`guards.py:120-124`). A
`withdrawals_enabled: bool | None = None` field is the same shape.

### A4. 🟠 No trials ledger — so no backtest we produce is interpretable (§73.6)

There is no `trials_attempted` anywhere. §73's central claim is that **without `N` there is no threshold**:
a reported Sharpe is edge *plus* a selection bias of size `E[max_N]`, and `E[max_N]` is computable only if
you know `N`. A backtest without a trials count is not weak evidence — it is **no** evidence.

`TurtleBreakout` exposes 6 tunable parameters ⇒ `N = 2^6 = 64` at two values each ⇒ `E[max_64] ≈ 2.35`
**from a rule with no edge at all** (§73.5).

---

## B. Things that are already right (checked, not assumed)

- ✅ **The anti-scalping rail does NOT claim shariah justification.** `guards.py:294` says *"conservative
  spread+fees clearance floor"* — a trading rationale — and no spec claims otherwise. §65.6 showed the
  shariah framing is overstated; **that error existed only in the KB README, which has been corrected.**
  The rail needs no change. Independently, §74.10 now supplies *trading* evidence for it (daily beats
  intraday on BTC).
- ✅ **`sim/metrics.py` already uses `rf = 0` for both Sharpe and Sortino**, explicitly *"(halal policy:
  riba-free)"*. §73.4's gate statistic is therefore most of the way built — what is missing is only the
  **per-trade** formulation (vs the current annualized-from-periods one), which is what dissolves §54.22's
  intermittent-returns objection.
- ✅ **Rails 12/13/14 fail closed** — unset kill-switch, unknown balance, unattested subscription all veto.
  This is the correct posture and the right precedent for A3.
- ✅ **The ADX>25 gate survives ablation and now has independent crypto-specific support** (§74.7). Keep it.
- ✅ **`_mark_to_market_equity` returns `None` rather than a partial total** when the quote balance is
  unreadable — correct, since a wrong equity permanently poisons the monotonic HWM.

---

## C. Strategy findings (no defect, but evidence has moved)

- **`entry_lookback=40` is probably too short.** Three independent sources now say longer: §58.6 (optimum
  80–95), our own 20→40 walk-forward ("every lookback longer than 20 beat 20 OOS"), and §74.2 (150–200
  significant, 50 not). **We have never tested above 40.**
- **RSI mean-reversion is settled-closed** — three independent refutations (§58.10a, §74.3 at p<0.01, our
  own sim). No further variants.
- **§54.15's ATR/stdev band candidate should be demoted** — §74.4 finds Bollinger bands significantly
  *negative* on BTC (−5.34bp, p<0.01). Same family.
- **A second rule class must be *measured* uncorrelated, not assumed** (§74.12): MOM and MA similarity
  *increases with trend strength*, so they converge exactly when our rule fires. `macd_divergence` (§58.10c,
  supported by §74.5's significant MACD results) is the better candidate *because* it is structurally
  different — but verify signal-overlap before counting it as diversification.
- ⚠️ **Edge decay is measured, not hypothetical** (§74.11): four studies find crypto becoming more
  informationally efficient over time, and all the crypto-specific evidence above comes from samples ending
  **2018–2019** while we trade **2021–2026**. The evidence supporting our direction was measured in a *more
  inefficient market than ours*.

---

## D. Ranked change list

Cheap and evidence-backed first; nothing here is speculative.

| # | Change | Why | Size |
|---|---|---|---|
| **1** | **Raise `TREND_FOLLOW.min_trades` 30 → 100** (i.e. stop relaxing it), keep `min_win_rate=0.30` | A1 — two independent lines say ~68–143; 30 is roughly half. The win-rate relaxation was justified, the sample-size one never was | 1 line + test |
| **2** | **Sweep `donchian_entry_n` far past 40** (e.g. 40/60/80/120/160/200), report the **plateau**, not the peak | C — three sources say longer, never tested. Per §73.12 re-derive as `a_priori` so it costs no trials budget | sweep only |
| **3** | **`trials_attempted` + persisted append-only trials ledger** | A4 — prerequisite for any MinBTL work; without `N` nothing is interpretable | small |
| **4** | **MinBTL as a REPORTED metric** (not yet a gate) | §73.2. Ship reporting-only: at current values it fails for every rule we have, and a gate that blocks everything on day one gets disabled rather than heeded | ~15 lines |
| **5** | **Per-instrument `backing` attribute on the allowlist** (`none` / `gold` / `fiat`) → routes to the `sarf` vs `bay' mutlaq` regime | A2 — the allowlist is not juristically homogeneous | config + policy |
| **6** | **`withdrawals_enabled` on `OrderIntent`, failing closed, blocking ENTRIES only** | A3 — mirrors rail 13 exactly; exits must never be blocked or capital is trapped | small |
| **7** | **Demote the §54.15 band candidate; do not build `macd_divergence` as "diversification" until correlation is measured** | C | planning only |

**Not recommended:** per-config p-values in the sim report (§73.8 — an 8,800-node mesh on a *pure random walk*
produced PSR-Stat 2.83, ">99% confident" and wrong); relaxing the ADX gate for trade frequency (measured and
rejected 2026-07-20, and §73.13 shows `SR_trade` enters MinBTL *squared* while frequency enters linearly).

## E. The through-line

Three of the four defects (A1, A3, A4) share a shape: **a check exists, and its calibration or its input was
never revisited when the evidence changed.** A1 relaxed two axes when only one was justified. A4 never
existed because nobody asked what `N` was. This is the same class as the dormant-rail pattern the branch
merged this morning was written to close — a rail that reads as enforced but cannot fire — one level up, at
the *thresholds* rather than the producers.
