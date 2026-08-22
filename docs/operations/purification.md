# Income purification (`keel purification`) — KB §65.9

**Purification is tracking income that must be given away.** Some credits arrive in the
trading account that are not sale proceeds and are not permitted to keep — interest, rewards,
staking payouts, promotional yield. USDC Rewards are the live example: they accrue **inside
the account**, on idle USDC balances, with no order placed, so no trading rail ever sees
them. Ayub (Ch 8.8.1 / 5.5.3): the prohibited income mixed into earnings must be doled away,
never recognised as profit. `keel purification` counts, after the fact, what actually
accrued — which is why it is machine-computable where the runbook's step 1 (disable rewards,
§56.3) is not: the credits land in the transaction ledger, so they can be counted there.

⛔ **REPORT-ONLY. The agent never disposes of funds.** It computes an amount owed and says
so, exactly as the zakat estimate does. Moving the money is the operator's act.

## Running it

```bash
keel purification
```

The report is computed from the **imported transaction ledger** (`keel db import`), so it is
only as current as your last import — run the import first or the figure understates what
accrued.

## How transactions are classified

Every ledger row's type string is classified one of three ways
(`keel/compliance/purification.py`):

| class | examples | meaning |
|---|---|---|
| `CLEAN` | buy, sell, convert, deposit, withdraw, send, receive, transfer | ordinary trading/custody activity — not counted |
| `NON_COMPLIANT` | anything containing reward, interest, staking, incentive, earn, airdrop, rebate, yield, inflation | income owed to charity — counted |
| `REVIEW` | anything unrecognised | surfaced for a human, counted nowhere |

The check is deliberate in both directions. Non-compliant markers win over clean ones (a
"reward buy" is a reward). And an unrecognised type is **never silently defaulted**: calling
it clean would let riba into P&L; calling it non-compliant would over-purify and misstate a
religious obligation as fact. Neither is honest about an unknown.

## What the report shows

- **Per asset**: units received (`qty_by_asset`) and USD owed (`owed_by_asset`) — what is
  actually held from a tainted source, not just its value.
- **`TOTAL OWED TO CHARITY`** (`total_owed_usd`), the figure to give away.
- Any `needs_review` rows, listed with their type strings.

The owed amount is **excluded from realised P&L and from the equity base position sizing
computes from** — otherwise riba would compound into position size (§65.9). Zakat, if you
estimate it, is on *purified* wealth, so purification runs first.

## What you do with `needs_review` rows

Decide what they are, then make the ledger tell the truth. The report names the exact type
strings it could not classify; look one up in the venue's export, and:

- if it is non-compliant income, the marker list in `keel/compliance/purification.py`
  (`NON_COMPLIANT_MARKERS`) should learn the string, so the next run counts it;
- if it is genuinely clean activity, it belongs in `CLEAN_MARKERS` for the same reason;
- either way the goal is that the row stops being unclassified — a `REVIEW` row is an open
  question you have not answered, not a verdict.

## Discharge is NOT recorded

`total_owed_usd` is **lifetime-cumulative**. There is no "paid" entry, and giving the money
away does not reduce the figure — an amount already discharged keeps appearing in the total,
which is the conservative direction (it keeps subtracting from balance-derived sizing seeds;
see the sizing-equity purification invariant in `docs/operator-runbook.md`, #490). Correcting
or removing ledger rows is the remedy when the figure is wrong. Treat the report as a running
obligation ledger whose balance you reconcile by hand, not a balance that clears itself.
