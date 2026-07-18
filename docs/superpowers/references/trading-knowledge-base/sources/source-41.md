[← Knowledge Base index](../README.md)

## Source 41 — "Trading Cryptocurrencies 3: Cardano, Algorand and more" (Swissquote primer, 17pp)

> Part 3 of the Swissquote series ([39](./source-39.md), [40](./source-40.md)). **Same category: an
> altcoin/token profile catalog** — Cardano, Algorand, Aave, Compound, Maker, Cosmos, Uniswap,
> yearn.finance, Filecoin — describing each project, then Swissquote marketing.
>
> **Saturation-honest: no trading/strategy content.** But this batch is **DeFi-heavy**, which makes it a
> concrete **worked example for the CompliancePolicy `haram_sector`/riba screen (§28.4)** — several of these
> tokens are interest/lending/yield protocols that would be **rejected outright** (§41.1). That's the only
> bearing. Logged to record it was seen.

---

### 41.1 DeFi lending/yield tokens → concrete `haram_sector`/riba reject examples (reinforces §28.4)
Part 2 gave a **maisir** reject example (Augur, §40.1); Part 3 gives a cluster of **riba** ones. Their core
function *is* interest/leverage/yield, so the CompliancePolicy admission gate excludes them:
- **Aave** — lending/borrowing protocol; deposits earn interest, borrowing = leverage → **riba, reject.**
- **Compound** — algorithmic money-market lending (earn/borrow interest) → **riba, reject.**
- **Maker (MKR/DAI)** — collateralized-debt stablecoin system (stability fee = interest) → **riba-based, reject.**
- **yearn.finance** — *"automates **yield-farming** strategies"* → **riba (yield), reject.**
- (Uniswap = DEX/AMM; Cardano/Algorand/Cosmos/Filecoin = L1/infra tokens — no inherent riba, but still
  outside our liquid-majors allowlist BTC/ETH/PAXG, and would need the full admission gate: liquidity +
  5yr-data + backtestable + halal-sector.)

→ **Reinforces the `haram_sector`/riba-yield screen (§28.4) with a named token set.** The screen isn't just
about a token's *marketing sector* — it must catch tokens whose **on-chain function is lending/interest/
yield** (DeFi money-markets, yield aggregators, interest-bearing/rebasing tokens). No new rule — a
sharpened example of what the existing screen must reject. **Nothing to build now; note the examples in the
CompliancePolicy design.**

### 41.2 Discarded (no agent value)
All per-project technology explainers (Cardano PoS/Ouroboros, Algorand consensus, Cosmos IBC, Uniswap AMM,
Filecoin storage, DeFi protocol mechanics); Ledger/Trezor custody mentions (§22.3, N/A — we're custodial);
Swissquote platform marketing & account CTAs.

---

### Net assessment (saturation-honest)
- **No trading/strategy/mechanical content** — altcoin/DeFi profile catalog (Part 3 of §39/§40).
- **Only bearing:** a concrete **riba/`haram_sector` reject set** — Aave/Compound/Maker/yearn (lending/
  yield = riba) — reinforcing §28.4 with named examples (complements Augur/maisir from §40.1); other L1
  tokens sit outside the majors allowlist and would face the full admission gate.
- **No new rails, indicators, strategy, or allowlist change. No action.**
- **The Swissquote primer series (39–41) is crypto-project education, not strategy** — recommend skipping
  further parts unless one contains actual technical/trading methods. See [[halal-cb-autotrade-project]],
  [[halal-cb-transcript-workflow]].
