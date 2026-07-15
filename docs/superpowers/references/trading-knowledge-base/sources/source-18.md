[← Knowledge Base index](../README.md)

## Source 18 — "Carry / Rollover" — ⛔ FULLY EXCLUDED (riba)

> **This entire transcript is out of bounds for us.** It teaches **carry / rollover trading** —
> earning the overnight interest-rate differential between two currencies (positive carry). That is
> **interest = riba**, hard-excluded by the governing halal constraint (§4.9, §10.10, design spec §2).
> Nothing here is adoptable. Logged so the exclusion is explicit and traceable.

### 18.1 What it teaches (and why every bit is excluded)
- **Rollover/carry = overnight interest** paid/earned on a held FX position (applied ~5pm NY). The
  source frames "positive carry" as a compounding edge and a whole strategy (weekly-chart holds,
  ≤3% risk, stop beyond long-term structure, hold for months/years to collect interest).
- **For us:** earning/paying interest is **riba** — forbidden regardless of profitability. The design
  already excludes carry (§2.6, §4.9). **We hold spot; there is no overnight financing.**
- **Crypto note:** the spot-crypto analogue would be **perpetual-futures funding rates** — also
  interest-like and **not something we trade** (no perps, no leverage). N/A and excluded.

### 18.2 The ONE transferable (non-carry) lesson — already captured
"A strategy profitable on paper but not in the live account" → because real holding costs aren't
modeled. For us the cost isn't carry (excluded) but **fees + spread + slippage** — which our
backtester must model and the executor must log (§4.2). This merely re-confirms §4.2; no new work.

### 18.3 Meta-value
Cleanest demonstration yet of the **halal filter working**: an entire, well-produced trading strategy
that we correctly reject wholesale. Good provenance for *why* carry is excluded if ever questioned.

### 18.4 Discarded (i.e. all of it, for agent purposes)
Positive/negative carry mechanics, interest-differential math, leverage-amplified carry returns,
weekly-hold carry rules, the AUD/CAD 2-year carry case study, tier-one CTAs.
