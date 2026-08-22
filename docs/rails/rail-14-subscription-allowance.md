# Rail 14 — subscription allowance (the fee-free monthly volume cap)

Rail 14 is one of keel's hard rails: checks in `keel/execution/guards.py` that run before every
order, in every mode, and cannot be switched off or widened from `config.yaml`. This one caps
the **month-to-date live BUY notional** — everything the agent has bought this UTC calendar
month, from the orders audit log, plus the order being placed — at the venue's **fee-free
monthly volume allowance**.

## What it bounds — and why that is a profitability boundary, not a budget

The cap is the subscription tier's `free_volume_usd`: the monthly volume the venue (Coinbase
Advanced Trade, at time of writing) lets the account trade **taker-fee-free**. That makes the
rail's economics, not just its accounting:

> Inside the allowance the measured strategies are indistinguishable from break-even; outside
> it the taker fee (120 bp) makes them decisively negative. **"It is not a budget limit; it is
> the profitability boundary."**
> — `docs/research/2026-08-20-quant-lab-note-cross-verification.md`, §5

On the hourly clock every round trip outside the allowance costs more than one full unit of
risk, so the rail is what keeps the agent from buying a known-losing trade. DCA is **not**
exempt — recurring buys are exactly the spend this rail exists to cap.

## Where the setting lives — the database, not config.yaml

The allowance is deliberately **not typed into config.yaml**. It is read from the venue's
**attested subscription record** in the database (`broker_subscriptions`, via
`repo.get_broker_subscription`), refreshed on every `keel subscription attest` — and read fresh
on every order, so a new attestation takes effect on the very next one, with no restart.

```bash
# Assert which tier the venue is on (the normal path; upgrades exactly one number)
keel subscription attest --venue <venue> --tier <tier>

# Escape hatch: a raw allowance without naming a tier (recorded as tier=unknown)
keel subscription set --venue <venue> --free-volume-usd 500
```

A record may also carry `pacing="even_daily"`, which additionally caps month-to-date spend to
`allowance / business_days_in_month × business_days_elapsed`, spreading the allowance evenly
over the month instead of letting the first week spend it all.

The rail **fails closed** like its siblings: an unattested venue, a `suspect` or `lapsed`
record, or one whose yearly re-attestation is overdue all fall back to
`config.subscription.unsubscribed_allowance_usd` — default **0**, i.e. no spending. keel ships
unable to place a live BUY, deliberately. A Premium record (`free_volume_usd` unlimited and in
force) has no cap and the rail does not apply.

## What happens when it is exceeded

The BUY is **vetoed** before it reaches the venue — the same fail-closed gate as every other
rail. The violation message says which case tripped:

- `subscription_unattested: … cannot spend because no subscription has been attested…` — there
  is no budget at all; the message names the bound venue and the `attest` command that restores
  it (pointing an alpaca deployment at a coinbase record would write a row nothing reads).
- `monthly_subscription_allowance: month-to-date BUY spend … exceeds the allowance cap …` — a
  real budget is exhausted; the message reports the remaining allowance and any `even_daily`
  paced cap.

SELLs are never blocked by this rail (they produce quote currency, they do not consume it). The
cap resets when the month rolls over.

## How to inspect it

- **`keel doctor`** — the `allowance.headroom` finding: what remains of this month's allowance,
  roughly how many typical orders that is, and a WARN ("allowance exhausted… the rail vetoes
  further BUYs until the month rolls over") when it is spent.
- **`keel status`** — the subscription row for each venue: tier, pacing, stored and effective
  status, and the effective cap actually in force.
- **`keel subscription show`** — every venue's record with attestation timestamps, in full.

The venue the rail gates on is the deployment's own bound venue (its `broker:` selection;
coinbase when unbound) — the same key `subscription attest` defaults to writing, so the
copy-paste advice in a veto message writes the record that will actually be read.
