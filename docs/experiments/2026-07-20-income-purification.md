# Income purification — the obligation, and what it found

**Date:** 2026-07-20
**KB basis:** §65.9 (purification), §56.3 (disable USDC rewards), §33.1 (zakat on purified wealth)
**Status:** built and run against the real imported history. **Report-only; no funds moved.**

## The obligation

Ayub, Ch 8.8.1 / 5.5.3: *"Islamic asset management companies have to purify their income by
deducting from the returns on the investments the earnings emanating from any unacceptable
source… **It is obligatory to dole away the prohibited income** that is mixed up with the
earnings."* Institutions run a **Charity Account** — non-compliant income is segregated and given
away, never recognised as profit.

This is the compliance queue's top item because it is the **only new machine-verifiable
obligation** the 2026-07-20 sweep found. It also has a claim independent of whether the strategy
ever validates: it is an obligation, not an optimisation.

## Why it is computable where §56.3 is not

§56.3's obligation — disable USDC rewards — is **preventive** and operator-attested. Advanced Trade
exposes no rewards endpoint, so we can attest a setting but cannot verify zero accrual.

§65.9 is the remedy for exactly that gap, and it *is* computable, because incoming reward credits
appear in the transaction ledger after the fact. **The purification report therefore doubles as a
detector for the §56.3 setting**: reward credits in the ledger are evidence that rewards were
enabled during that period. That connection is not in the KB — it falls out of building both.

## What it does

`keel purification` classifies every ledger credit:

| verdict | meaning |
|---|---|
| `clean` | sale proceeds, own deposit, asset transfer — ordinary trading or custody |
| `non_compliant` | interest, rewards, staking, incentives, rebates, promotional yield |
| `review` | **unrecognised — deliberately neither** |

**An unknown type is never given a silent default.** Calling it clean would let riba into P&L;
calling it non-compliant would state a religious obligation as fact on a guess. Neither is honest
about an unknown, so it is surfaced for a human.

⛔ **Report-only.** The agent computes an amount owed and says so. Moving it is the operator's act,
exactly as §33.1's zakat estimate is.

## Result on the real history

Run against the imported transaction history (188 trading transactions, 5 deposits):

- **19 non-compliant credits found** — 18 `Reward Income` plus 1 `Incentives Rewards Payout`,
  across BTC and USDC. The total owed is a few dollars.
- **0 credits needed review** — every type in the real ledger classified cleanly, which is a
  useful sanity check on the marker list rather than a foregone conclusion.

*Exact per-asset amounts are intentionally not committed here.* They are personal financial data,
this repo previously had to purge transaction records from its history, and the number is available
on demand from `keel purification`. The finding is the count and the mechanism, not the sum.

⚠️ **The USDC reward credits are the substantive part.** They are direct evidence that USDC rewards
were accruing during that period — precisely what §56.3 says to disable. The amount is trivial; the
signal is not, because it is the one thing §56.3 could not verify for itself.

## The interlock with P&L, and why it is now pinned

§65.9's first consequence: **P&L correctness is a compliance concern, not just an accounting one.**
Riba credits silently included in equity would inflate the equity base that fixed-fractional sizing
computes from — **riba compounding into position size.**

`analysis/pnl._classify` already ignores reward types, because they match neither its buy nor its
sell keywords. That is correct **but incidental** — broadening those keywords later would silently
start counting rewards as acquisitions. There is now a test asserting that adding a reward credit
does not change realised P&L, so that change fails loudly instead.

## Not done

- **Segregating the reward-acquired UNITS from the position.** The report states units received per
  asset, but `analysis/pnl.position()` does not yet net them out of holdings. Those coins are held
  and they came from a tainted source; how to treat the units (as opposed to their value at
  receipt) is a question §65.9 does not settle and a human should.
- **§33.1's zakat estimate.** Composes with this — zakat is on purified wealth, so purification
  runs first — but it is not built.
- **§65.4's withdrawal-capability guard** and **§65.5's asset-backed admission check** remain in
  the compliance queue. The latter is now partly served by `keel assets attest`'s `backing` field.
