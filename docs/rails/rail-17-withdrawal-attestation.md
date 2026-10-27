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

## The TTL and the DCA cadence (#1010)

The 7-day TTL is shorter than a 14-day DCA cadence, so a biweekly rule's buy can fall due after
the attestation has lapsed. That is what happened live: the attestation was made on 2026-10-10
at 19:25Z. The weekly rules filled on 2026-10-16. The attestation expired on 2026-10-17 at
19:25Z. On 2026-10-23, acting on the 2026-10-22 bar, rail 17 vetoed all seven DCA signals. The
14-day rules 9 to 13 have never filled live. DCA buys are **not** exempt from this rail: a DCA
buy is an acquisition like any other.

Three surfaces now name each live DCA rule whose next buy is due after the attestation expires.
Each line gives the rule, the due date, the expiry and the date from which a fresh attestation
would cover that buy:

- `keel withdrawals attest --enabled` prints a `WARNING:` line per rule right after attesting.
- `keel status` prints the same line under the rail-17 line (live modes only; paper does not
  evaluate rail 17).
- `keel doctor` always reports the check `attest.dca_cadence`: OK when nothing is affected,
  WARN (never FAIL) when something is, because the rail is not vetoing yet.

The warning appears only while the attestation is fresh. An expired, suspended or absent
attestation is already reported as a halt by `attest.withdrawals` and the rail-17 status line,
so the cadence warning stays silent then. The web console banner does not show it yet.

"Next due" means a bar whose buy is not yet placed. On a placement day, after the 01:20 cycle has
bought yesterday's bar, that bar no longer counts: the warning looks at the following cadence
bar, so it neither misses a window that falls after expiry nor says rail 17 "will veto" a buy
already in (#1014). The check reads the rule's newest filled or pending live BUY.

A DCA window that a guard vetoes is no longer lost until the next cadence multiple. On a live
cycle, each later fresh daily bar of the same window retries the buy, with at most one buy per
window per rule: a filled or pending live BUY for a bar of the window closes it, so a same-day
rerun or a later bar does not buy again. A retry needs proof the window was due: a signal the
rule emitted, while live, for an earlier bar of the same window. A window that ends unfilled is
logged as `agent.dca_window_missed` on the next cadence day and is **not** back-filled. Paper
and simulation keep the plain cadence-only behaviour.

Because a retry day can coincide with another rule's cadence day, one day may now carry more
DCA spend than `keel dca plan`'s busiest-day figure. Rails 3 and 14 still bound it.
`keel pulse` (and the console's next-buy line) shows the next cadence day, not a pending retry.

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
