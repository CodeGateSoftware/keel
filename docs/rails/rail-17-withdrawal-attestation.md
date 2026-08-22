# Rail 17 — withdrawal capability (the `qabd` rail)

Rail 17 is one of keel's hard rails: checks in `keel/execution/guards.py` that run before every
order, in every mode, and cannot be switched off or widened from `config.yaml`. This one blocks
new **BUY** entries unless a *fresh attestation* says the account's balances can be withdrawn on
demand right now.

## What the rail permits

A BUY (a new entry, DCA buys included) passes rail 17 only when the operator has **recently
attested** that the account is in a withdrawable state. Everything else is vetoed:

- **No attestation at all, or one older than 7 days** — the rail reads the state as `UNKNOWN`
  and vetoes the BUY. An unverified state is not evidence that possession holds.
- **Attested `--suspended`** — withdrawals are known to be restricted, so new entries are
  halted.
- **SELLs are never blocked.** Existing holdings are already owned; forcing a sale to "fix" a
  withdrawal freeze would be strictly worse than holding through it. Exits, stop-outs and
  protective orders are deliberately unaffected.

In paper trading the rail is *skipped* (it describes the real account, which a paper rehearsal
cannot see) — but it is recorded as skipped, never silently omitted, so a paper track record is
honest about its own gaps.

## Why it exists

The fiqh basis is KB §65.4, Ayub's constructive-possession test (*qabd*): possession of a bought
asset holds only while *"there is nothing to prevent the buyer from taking physical possession
whenever he desires."* An asset sitting in an account that cannot be withdrawn from may not have
been validly possessed at all — so **acquiring more of it** is the thing to stop. The rail turns
that into a mechanical rule: funds enter the account's holdings only under a deliberate,
recently-renewed human act, never on an old assertion.

Because the attestation is about the account's *current* state, and a withdrawal freeze can
appear at any time, the TTL is deliberately short: **7 days**
(`WITHDRAWAL_ATTESTATION_TTL_SEC` in `keel/execution/executor.py`). A stale attestation is
treated as no better than none.

## What happens when it expires

Nothing dramatic and nothing automatic — the rail simply starts vetoing. An expired attestation
resolves to `UNKNOWN` (distinct from an attested suspension: "nobody has checked recently" is a
different claim than "the broker says withdrawals are restricted"), and the rail **fails closed**
on unknown. On 2026-08-14 this was not hypothetical: a lapsed attestation vetoed the only live
DCA signal of the day. `keel status` shows the freshness line with days-to-expiry, so staleness
is visible *before* it vetoes.

## Renewing it

Confirm in the venue's own app that the BTC/ETH/PAXG/USDC balances really are withdrawable on
demand, then, per deployment (each has its own database and its own attestation):

```bash
keel withdrawals attest --enabled
keel --config config.live-sandbox.yaml --db keel-live.db withdrawals attest --enabled
```

`--enabled` **releases an entry halt**, so it demands a typed `yes` at a terminal — a cron line
must not be able to clear a rail-17 halt and let the next cycle place live orders with no human
in the loop. `--suspended` only ever reduces capability and is ungated. To inspect the current
state: `keel withdrawals show`, or the rail-17 line in `keel status`.

The intended cadence is **weekly** — see item 3 of `docs/operator-runbook.md`. The rail fails
closed by itself; what it cannot do is refresh its own input, and that input is deliberately
human.
