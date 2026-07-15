[← Knowledge Base index](../README.md)

## Source 22 — "5 Things to Know Before Trading Crypto"

> **Directly on-topic** — crypto-specific, which is our exact market. Short but useful: it flags
> where crypto *differs* from the forex/stock examples the other 21 sources used, and validates our
> no-leverage + hard-risk posture. All in scope (long spot).

### 22.1 Crypto volatility regime — calibrate everything to it (new calibration note) → all modules
"Patterns are the same (human psychology), but crypto **moves 10–20% in an hour** vs ~1%/day forex."
→ Our volatility-dependent pieces must be **calibrated to crypto magnitude, not forex**:
- **ATR-based stops** (§17.3) and the **min-move/anti-scalping guard** (§4.1) use much larger absolute
  moves — good (bigger moves clear fees easily) but stops sit wider, so **position size shrinks**
  (`quantity = risk_amount / stop_distance`, §1.5). Volatility-adaptive sizing is *essential* here.
- **Drawdown expectations** (§4.6/§10.5) are larger per unit time → the account-DD breaker (§10.3/§20.4)
  thresholds must be set for crypto, and paper-gate/backtest samples must span crypto's wild ranges.
- Don't port forex "pip" thresholds literally — always use **% / ATR-relative** measures (adaptation rule).

### 22.2 Validation — no-leverage + hard risk (our design is doubly protected)
"**Leverage + volatility is a deadly cocktail**; even 2% per trade can wipe you out fast in crypto."
→ Our design **excludes leverage entirely (riba, §4.9)** — which *also* removes this exact danger — and
caps risk at **1%/trade** (§1.5) with exposure + correlation + DD rails. Strong validation of the
rails-first, no-leverage posture specifically for the crypto context.

### 22.3 NEW dimension — security / custody / operational risk → design safety model + `execution/guards.py`
Crypto adds risks forex doesn't: **exchange hacks, phishing, dodgy platforms, key theft.** For our
agent (custodial on Coinbase):
- **API-key security is a hard operational guard:** key in a git-ignored `.env`, **scoped to a
  dedicated trading portfolio**, **no withdrawal/transfer scope** (the agent can never move funds out)
  — already in the design safety model; this source reinforces *why*.
- **Exchange counterparty risk is an accepted, named risk** (Coinbase custody). Fund the trading
  portfolio with only a small, losable slice (§2.8/§10.7 speculation cap).
- Extends the §10.1 14-risk taxonomy with a crypto-specific **custody/security** row.

### 22.4 Crypto fundamentals/events as macro-context (extends §3.6 + §14) → low-weight
Crypto is driven by "**narratives, adoption cycles, regulation, Bitcoin halving, Ethereum upgrades**,"
not just charts. → Extend the **news/event-blackout filter (§3.6)** with **crypto-specific scheduled
events** (halving, major protocol upgrades, big regulatory dates) and the **halving cycle context
(§14)**. Same caution: **low-weight macro-context, backtested, never a standalone trigger** (§14.4).

### 22.5 Reinforced
Psychology magnified in crypto (FOMO/panic worse) → automated agent is immune by construction
(§4.10, §12.3), and the confirm-mode bias-detection (§6.1/§12.3) is *more* valuable here; patterns are
universal / driven by human behavior (§1.1, §8.5, §12.2); risk management non-negotiable (§10).

### 22.6 Discarded (no agent value)
Vantage-broker/hardware-wallet promos, "go watch my other videos" CTAs, Instagram-lifestyle framing.

---

### ⚠️ Note on the accompanying transcript (duplicate)
The transcript pasted alongside this one ("Why trading terminology matters…") is a **verbatim
duplicate of [Source 4](./source-04.md)** ("Trading Terminology Explained"). Not re-extracted — its
content already lives in source-04.md. Logged here only to record that it was seen and deduplicated.
