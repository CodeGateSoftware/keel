# Go-live runbook — the first supervised live order

The purpose of this run is **not** profit. It is to prove the live execution path works, because
`place_order` has never been executed against the real Coinbase API. Everything up to now —
683 tests, a positive backtest, a passing verdict — says the *logic* is right. None of it says the
*plumbing* is. Treat the first order as an experiment whose result is "the order appeared on the
exchange, correctly, and we have a record of it".

**Budget the whole thing as money you are willing to lose outright.**

---

## 0. Before you start

| | check | why |
|---|---|---|
| ☐ | `keel --version` reports `[release]`, no `DIRTY`, no `[checkout]` | a build that matches no commit is not reproducible; do not run it against funds |
| ☐ | you have a **trade-enabled** CDP key (the read-only one cannot place orders) | |
| ☐ | `.env` holds `CDP_API_KEY` / `CDP_API_SECRET`, and `.env` is git-ignored | credentials live only here — there is no vault |
| ☐ | you are at a real terminal | confirmation and every halt-releasing command fail closed off a TTY |
| ☐ | funds are in **USDC**, not USD | rail 13 vetoes a BUY that is not funded from `quote_currency`, and never draws from a bank/ACH |

## 1. Bring the deployment up

```bash
keel migrate                 # existing database: apply outstanding schema migrations
# or, on a brand-new deployment:
keel init                    # writes config.yaml and seeds the rule library as `candidate`
keel init-config --live      # optional: the production config (mode: confirm)
```

Then check the config you are actually about to run:

```bash
keel config show 2>/dev/null || grep -A3 '^auto_trade:' config.yaml
```

- `auto_trade.mode: confirm` — live, and asks before each order.
- `caps.max_exposure_usd` — set this to something you can lose. For a first run, small.
- `allowlist` — only assets you have screened and attested.

## 2. Attest what the rails need

Several rails **fail closed** until a human has attested something. This is deliberate: an
unattested venue or asset is treated as unknown, not as fine.

```bash
keel assets list                       # what is attested, and what is not
keel subscription show                 # rail 14's spend allowance comes from this record
keel withdrawals show                  # rail 17: withdrawal capability is a compliance precondition
```

Attest anything missing (`keel assets attest`, `keel subscription attest`,
`keel withdrawals attest`) before continuing. If a rail vetoes later, its message names the
command that fixes it.

## 3. Promote exactly one rule

Seeded rules are `candidate` and trade nothing. Promote **one**, on **one** product:

```bash
keel rules list
keel rules promote <id>       # candidate -> paper -> live
```

Promoting to `live` is what the promotion floor exists to gate. If you are deliberately
short-circuiting it for this test, know that you are — and promote a single rule, not the library.

## 4. Run one cycle, in confirm mode, and watch

```bash
keel agent
```

Autonomy is **off** by default, so this is what you should see:

```
Rails PASSED. Coinbase order preview:
    order_total: 5.00
    ...
Place this order? [y/N]:
```

**Read the preview before answering.** It is the broker's own numbers, not keel's estimate. Check
the product, the side, and the total. If anything surprises you, answer `N` — declining places
nothing and costs nothing.

The rails have already passed at this point. The prompt is an **additional** human gate, never a
replacement for them.

## 5. Verify against reality

Do not trust the tool's own success message alone. Check the exchange:

```bash
keel pnl
keel rules list
```

- The order appears in the Coinbase UI/app with matching product, side and size.
- `orders` has a row with the real broker order id.
- The fill price is sane against the market at that moment.

If the order did **not** appear but keel thinks it placed one, stop and investigate before
running anything else. That is the exact failure this run exists to catch.

## 6. Halting

```bash
keel kill        # engage the kill-switch: halts all trading immediately
keel resume      # release it -- asks for a typed "yes" at a terminal
```

The kill-switch is checked first on every cycle and **defaults to engaged**, so a damaged or
unreadable state halts trading rather than permitting it.

Four commands re-permit trading after a halt — `resume`, `resume-entries`, `record-flow`,
`reset-hwm`. Each demands a typed `yes` from a terminal and **cannot be run from a script or cron
job**. That is deliberate: a breaker that can reset itself is not a breaker.

## 7. Only afterwards: autonomy

Do **not** turn this on for the first run.

```bash
keel autonomy show
keel autonomy on     # asks for a typed "yes"; requires a terminal
keel autonomy off    # always allowed, works anywhere, needs no terminal
```

Autonomy stops keel asking before each order. It changes **who is asked, never what is allowed** —
every hard rail still runs first, and it does **not** let the agent clear a safety halt. The
setting lives in your profile in the database and is re-read on every order, so `keel autonomy off`
takes effect on the **next order**, not the next restart.

Turn it on only once you have watched several supervised cycles behave correctly.

---

## What can still go wrong

- **`place_order` has never run against the real API.** This runbook is that test. Expect the
  unexpected on the first attempt, and keep the size trivial.
- **A rail vetoes and you disagree.** Read the veto message; it names the rail and the command
  that clears it. Do not work around a rail — the rails are un-overridable by design, including
  in autonomous mode.
- **Confirm mode places nothing when run headless.** That is not a bug: with no TTY the
  confirmation declines. Use a terminal, or turn autonomy on deliberately.
- **Equity moved because you deposited or withdrew.** Tell the tool (`keel record-flow --amount
  ±N`), or rail 11 will read the movement as drawdown and veto entries on an account that lost
  nothing.
