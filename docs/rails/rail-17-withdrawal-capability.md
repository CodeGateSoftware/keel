# Rail 17 — withdrawal capability (the `qabd` rail)

Rail 17 is one of keel's hard rails: checks in `keel/execution/guards.py` that run before every
order, in every mode, and cannot be switched off or widened from `config.yaml`. This one blocks
new **BUY** entries unless the venue itself says the account's balances can be withdrawn right
now.

## What the rail reads

On every live BUY the executor asks Coinbase (`GET /v2/accounts`, the `allow_withdrawals` flag)
about two wallets: the order's **quote wallet** (the cash being spent) and the **wallet of the coin
being bought**. The existing API key is view + trade with `can_transfer: false`, so this is a
read the key can already make; nothing new is granted.

- **Both wallets allow withdrawals** — the BUY passes.
- **Either wallet has withdrawals off** — the BUY is vetoed, and the veto names the wallet.
- **The coin's wallet does not exist yet** (first purchase of that coin) — the decision rests on
  the quote wallet alone.
- **The venue cannot be read, the quote wallet is missing, the broker adapter has no such method,
  or the manual-suspension store cannot be read** — the BUY is vetoed. An unverified state is not
  evidence that possession holds, so the rail fails closed.
- **SELLs are never blocked.** Existing holdings are already owned; forcing a sale to "fix" a
  withdrawal freeze would be strictly worse than holding through it. Exits, stop-outs and
  protective orders are unaffected.

**Only the Coinbase adapter can answer.** `withdrawals_allowed` is implemented by the Coinbase
broker adapter alone; the Alpaca, Kraken and Robinhood adapters do not have it. On a live
deployment against any of those venues the rail cannot read the venue, so **every live BUY is
refused** (`withdrawal_capability: UNKNOWN (venue adapter cannot report withdrawals)`). That is the fail-closed rule above, not a fault to
attest around; SELLs on those venues are unaffected.

In paper trading the rail is *skipped* (it describes the real account, which a paper rehearsal
cannot see) — recorded as skipped, never silently omitted. SELLs and paper never read the venue.

## What has and has not been observed

`allow_withdrawals` has only ever been seen `true`. It has **not** been observed flipping to
`false` during a real withdrawal freeze, so it is not known that Coinbase sets it in that
situation. The rail is correct if it does and silent if it does not. That is why the manual
brake below exists, and why this page does not claim the venue read is a complete detector.

## The manual brake

`keel withdrawals attest --suspended` halts all new entries whatever the venue says. It has **no
expiry**: it stays until someone clears it. `keel withdrawals attest --enabled` clears it, and
still demands a typed `yes` at a terminal — a scheduled job must not be able to release a
rail-17 halt and let the next cycle place live orders with no human in the loop. `--suspended`
only ever reduces capability and is ungated.

Per deployment (each has its own database):

```bash
keel --config config.live-sandbox.yaml --db keel-live.db withdrawals attest --suspended
keel --config config.live-sandbox.yaml --db keel-live.db withdrawals attest --enabled
```

`keel withdrawals show` prints the manual suspension state and, for each wallet, the venue's
current answer. If the venue cannot be reached it says so and the suspension line is still shown.

## What was removed

Rail 17 used to rest on an operator attestation with a 7-day TTL. A lapsed attestation read as
unknown and vetoed BUYs; on 2026-08-14 that vetoed the only live DCA signal of the day. The TTL
(`WITHDRAWAL_ATTESTATION_TTL_SEC`), the weekly renewal chore and every expiry warning are gone.
Rail 22 (cash posture) and rail 14 (subscription) keep their attestations, unchanged.

## Why it exists

The fiqh basis is KB §65.4, Ayub's constructive-possession test (*qabd*): possession of a bought
asset holds only while *"there is nothing to prevent the buyer from taking physical possession
whenever he desires."* An asset sitting in an account that cannot be withdrawn from may not have
been validly possessed at all — so **acquiring more of it** is the thing to stop. See
`docs/fiqh-basis.md`.

## A skipped buy is retried

DCA buys are **not** exempt from this rail: a DCA buy is an acquisition like any other. A rail-17
veto skips that day's DCA buy. DCA catch-up retries a buy that was due and did not fill
on each later daily cycle until it fills or the rule's next window opens, so a short freeze does
not cost the window. A manual suspension is different: it is retried the same way, but nothing
clears it except `keel withdrawals attest --enabled`. A skipped live DCA buy also posts a macOS notification ("keel-live: DCA buy
skipped") with the reason; see `docs/operator-runbook.md`.
