[← Knowledge Base index](../README.md)

## Source 39 — "Trading Cryptocurrencies 1" (Swissquote educational primer, 16pp)

> New publisher (Swissquote), but a **crypto-fundamentals EDUCATION primer**, not a strategy guide — the
> "1" implies a series with the fundamentals up front. Content: blockchain / hashing / nodes, hot-vs-cold
> **wallets**, **Bitcoin halving**, soft/hard **forks**, smart contracts, and descriptive per-coin profiles
> (BTC, ETH, LTC, …), ending in Swissquote account-opening marketing.
>
> **Saturation-honest: no trading content, nothing mechanical/actionable.** Two marginal reinforcements
> (§39.1–39.2); everything else is technology education or promo. Logged to record it was seen.

---

### 39.1 Bitcoin halving / limited supply → reinforces §14 (BTC cycle) + §22.4 (event calendar) — low-weight
The primer's one market-relevant claim: *"Bitcoin prices have never dropped in the period after a halving
… due to the automatic decrease in the supply of new bitcoins"* (halvings continue until the 21M cap). →
Maps to what we already have: **halving-cycle context (§14, deferred to v2 / low-weight)** and **halving as
a scheduled crypto event in the news/event calendar (§22.4)**. ⚠️ **The "prices never dropped after halving"
line is exactly the kind of cycle-prediction we treat as low-weight, backtested-only, NEVER a standalone
trigger (§14.4 / no-oracle §6.4)** — a 4-sample "it always went up" claim is not an edge. Reinforcement +
caution, nothing to build.

### 39.2 Hot/cold wallets & custody → §22.3 (mostly N/A — we're custodial)
Hot (online) vs cold (offline, Ledger/Trezor) wallet distinction; self-custody security. → Reinforces
**custody/key-security awareness (§22.3)** in principle, but **N/A to our design**: the agent is
**custodial on Coinbase** (no self-managed wallet), with API keys scoped **no-withdrawal** and git-ignored
(§22.3). Self-custody wallet management is out of our operating model. Marginal.

### 39.3 Out of scope / discarded (the bulk)
Blockchain / hashing / node / mining / soft-fork-vs-hard-fork / smart-contract technology explainers (pure
crypto education, no trading value); per-coin descriptive profiles (BTC/ETH/LTC/etc. "current trading" +
"price chart" blurbs — background, our allowlist is already BTC/ETH/PAXG); "limited supply / digital gold"
narrative (loosely reinforces the real-utility/store-of-value screening §28.3/§33, but not actionable);
Swissquote platform marketing, account-opening CTAs, "why trade with Swissquote".

---

### Net assessment (saturation-honest)
- **No trading/strategy content** — a crypto-fundamentals education primer + broker promo.
- **REINFORCES (marginally):** halving-cycle / limited-supply as **low-weight, backtested-only** context
  (§14/§22.4) — with the caution that "always up after halving" is a no-oracle non-edge (§6.4);
  hot/cold-wallet custody awareness (§22.3, but N/A — we're custodial).
- **No new rails, indicators, strategy, or allowlist change.** No action.
- If a **"Trading Cryptocurrencies 2/3"** with actual technical/strategy content shows up, that could be
  worth extracting — this Part-1 primer is not. See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
