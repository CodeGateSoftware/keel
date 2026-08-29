# Go-live runbook — the first supervised live order

The purpose of this run is **not** profit. It is to prove the live execution path works, because
`place_order` has never been executed against the real Coinbase API. Everything up to now —
683 tests, a positive backtest, a passing verdict — says the *logic* is right. None of it says the
*plumbing* is. Treat the first order as an experiment whose result is "the order appeared on the
exchange, correctly, and we have a record of it".

**Budget the whole thing as money you are willing to lose outright.**

---

## Where am I up to?

```bash
keel setup            # what this deployment still needs, and which parts only you can do
```

Read-only — it opens the database read-only and writes nothing, so it is safe on a deployment
mid-cycle and safe when there is no deployment at all. It reports the same steps this runbook
lists, observed rather than assumed, in three kinds:

- **mechanical** — keel can do these for you.
- **judgement** — yours to decide. keel records them; it must never choose them. Every Shariah
  attestation is a human classification with a cited source, and an unsourced attestation is
  refused exactly like a missing one.
- **off-venue** — done in the venue's own dashboard, where keel can neither act nor look. These
  are **never** reported as done, because a green check that verifies nothing turns an open risk
  into a false assurance.

The same checklist is the Setup page in `keel serve`, and it is what a brand-new machine's
landing page shows instead of an empty dashboard.

## 0. Before you start

| | check | why |
|---|---|---|
| ☐ | `keel --version` reports `[release]`, no `DIRTY`, no `[checkout]` | a build that matches no commit is not reproducible; do not run it against funds |
| ☐ | `keel versions` exits **0** | `--version` speaks for the `keel-trader` distribution alone. This one checks every keel distribution in the venv and fails on a partial upgrade — new engine, old libraries — which `--version` cannot see |
| ☐ | you have a **trade-enabled** CDP key (the read-only one cannot place orders) | |
| ☐ | `CDP_API_KEY` / `CDP_API_SECRET` are set — in `.env` (git-ignored) or via `keel credentials set NAME` | resolved in this order: **environment → `.env` → OS keychain** (`keel_core.secrets`). A `.env` beside your deployment outranks the keychain deliberately — every deployment that predates `keel credentials` keeps behaving byte-identically — but that means a `.env` value silently SHADOWS anything you store with `keel credentials set` afterwards. `keel credentials show` names which of the three is actually in use; trust that over your memory of which one you last touched |
| ☐ | you are at a real terminal | confirmation and every halt-releasing command fail closed off a TTY |
| ☐ | settled funds are in the **quote leg of the product you trade** (`BTC-USD` → **USD**) | rail 13 checks the balance of the currency the ORDER spends, derived from the product — not `config.quote_currency` — and never draws from a bank/ACH |

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
keel scope show                        # rail 20: can this venue's credential place a live ENTRY?
```

Attest anything missing (`keel assets attest`, `keel subscription attest`,
`keel withdrawals attest`, `keel scope attest --trading`) before continuing. If a rail vetoes
later, its message names the command that fixes it.

Rail 20 exists because a credential that *reads* fine is not evidence it can *trade*: a
well-formed `ROBINHOOD_API_KEY` passed every read this deployment ever made, and the first live
order still 403'd with "You do not have permission to perform this action." That is a fact about
the credential, not about the asset — attesting `BTC` in Section 2's other checks says nothing
about whether the venue behind it will let this credential place a live order — so `keel scope
attest --trading` is its own, separate step, per venue. Like rail 17's withdrawal gate, it vetoes
ENTRIES ONLY: an existing position is already yours, so a rail that also blocked exits, stop
rolls or DCA exits over a fact about the credential rather than the position would strand a
holding nobody could get out of. And it fails closed — a venue nobody has attested keeps every
live entry vetoed until `keel scope attest` runs, deliberately, rather than assuming an untested
credential is fine.

## 3. Promote exactly one rule

Seeded rules are `candidate` and trade nothing. Promote **one**, on **one** product:

```bash
keel rules list
keel rules promote <id>       # candidate -> paper -> live
```

Promoting to `live` is what the promotion floor exists to gate. If you are deliberately
short-circuiting it for this test, know that you are — and promote a single rule, not the library.

A **DCA** rule cannot clear the gate at all, structurally: it has no stop and no target, so
`backtest()` opens a position that never closes, every trade stays `open`, and the aggregates see
`n_trades=0` against `min_trades: 100`. It also needs `--granularity ONE_DAY`, because `Dca` never
sets `self.granularity` the way `TurtleBreakout` does. So a DCA go-live is
`keel rules promote <id> --force`, twice — the bypass that flag documents, not a judgement call.

Once promoted, record the change: `python scripts/rule_manifest.py export --db <db>` and commit
`deploy/live-rules.json`. A fresh deployment re-seeds from constructor defaults and will otherwise
bring back a differently-sized rule (see `docs/RELEASING.md`).

### Standing exception: the sandbox's five live-seeded rules

This section covers the *act* of short-circuiting the gate. The supervised-live sandbox has been
running in that state since **2026-07-24**, and that ongoing state is recorded here so it is a
decision on the record rather than an oversight nobody re-examined.

`keel-live.db` rules 1–5 (`turtle_breakout` on BTC/ETH/PAXG/ADA/XLM) carry `status = live` with
**`promoted_at IS NULL`** — seeded directly, never promoted. `rules seed` printed *"Do not leave
live-seeded rules in place afterwards."* They were left in place. **Reviewed 2026-08-08; kept
deliberately.**

**Why they are kept.** `min_trades` is 100 *per rule*, and this strategy cannot reach it.
Backtesting each rule over ~5.02 years of daily bars on 2026-08-08:

| rule | trades | rate | years to 100 trades |
|---|---:|---:|---:|
| BTC | 13 | 2.59/yr | ~39 |
| ETH | 13 | 2.59/yr | ~39 |
| XLM | 8 | 1.59/yr | ~63 |
| ADA | 6 | 1.19/yr | ~84 |
| PAXG | 4 | 3.20/yr | ~31 |

Waiting for the gate is not a slower route to the same destination — it is no route. The sandbox
exists to accumulate the live evidence the promotion gate demands and cannot itself generate.
Demoting these rules would end that experiment without putting anything in its place.

**What bounds the risk instead.** Not the promotion gate, which never ran. The caps
(`max_exposure_usd` 200 total at once, `max_per_order_usd` 100), the nineteen un-overridable
`guards.py` rails, rail 1's allowlist, and rail 14's monthly allowance. **The bypass is of the
evidence gate, not the safety rails** — separate mechanisms, and only the first was skipped.

**What it is not.** Not a precedent for admitting assets, not a reason to raise caps, and not a
claim that these five rules are validated. They are not: no walk-forward or PBO run has ever
covered ADA or XLM, and none of the five clears any promotion axis. See
`docs/experiments/2026-08-07-unvalidated-skip-set-reassessment.md`.

**Revisit if** caps rise above this sandbox's few-dollars-of-damage scale; the rule set changes; a
rule begins trading materially more often than the table above; or this sandbox starts being
treated as evidence for anything beyond itself.

## 4. Run one cycle, in confirm mode, and watch

```bash
keel agent
```

Autonomy is **off** by default, so this is what you should see:

```
Rails PASSED. Coinbase order preview:
  ========================================================================
  BROKER QUOTE -- the venue priced this order itself.
  ========================================================================
    order_total: 5.00
    ...
Place this order? [y/N]:
```

**Read the banner first, then the numbers.** The `=` rule above means Coinbase priced this order
itself — those are the venue's own figures. Check the product, the side, and the total. If
anything surprises you, answer `N`; declining places nothing and costs nothing.

The rails have already passed at this point. The prompt is an **additional** human gate, never a
replacement for them.

**A `!` block instead of the `=` rule means stop and read.** The gate shouts in three cases, and
none of them should appear against Coinbase today:

- `SYNTHETIC ESTIMATE -- NOT A BROKER QUOTE` — the figures are keel's own estimate from a price
  lookup. The venue has priced, validated and reserved nothing, and is bound by none of them.
  Only a venue with no preview endpoint produces this; Coinbase has one.
- `UNPRICED -- this preview carries no usable size` — a zero here is **not** a cheap order, it is
  an order whose cost could not be determined.
- `PREVIEW ERRORS (n)` — the venue or the adapter reported a problem with this specific order.

The last two replace `[y/N]` with a typed `place anyway`. That is friction, not a wall: it stays
possible on purpose so a broken pricing endpoint can never trap you out of *closing* a position.
If you are opening one, the right answer to a shouting gate is almost always to decline.

**If you instead see `signals=0` and no preview**, no rule produced a setup this cycle — the
cycle itself ran fine. With a DCA test vehicle this is almost always the cadence gotcha (see
*What can still go wrong*), not a failure.

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

**Five** commands re-permit trading after a halt. Each demands a typed `yes` from a terminal and
**cannot be run from a script or cron job** — a breaker that can reset itself is not a breaker.

| command | releases |
|---|---|
| `keel resume` | the kill-switch |
| `keel resume-entries` | a consecutive-loss halt (rail 16) |
| `keel record-flow --amount ±N` | rebases rail 11's high-water mark |
| `keel reset-hwm` | rail 11's high-water mark, clearing a stuck drawdown halt |
| `keel withdrawals attest --enabled` | rail 17's entry halt |

The de-risking direction of each is always allowed and needs no terminal: `keel kill`,
`keel withdrawals attest --suspended`, `keel autonomy off`.

## 7. Only afterwards: autonomy

Do **not** turn this on for the first run.

```bash
keel autonomy show
keel autonomy on     # asks for a typed "yes"; requires a terminal
keel autonomy off    # always allowed, works anywhere, needs no terminal
```

Autonomy stops keel asking before each order. It changes **who is asked, never what is allowed** —
every hard rail still runs first, and it does **not** let the agent clear a safety halt.

The setting lives in your profile in the database and is re-read **at the start of every cycle**.
So `keel autonomy off` takes effect on the **next cycle**, not the next restart — but note that
with `interval_sec: 900` a cycle already in flight can still place orders for up to 15 minutes.
**If you need trading to stop immediately, use `keel kill`, not `autonomy off`.**

**Prefer a time-bounded session:**

```bash
keel autonomy on --for-hours 4    # lapses on its own; nothing to remember
```

Without `--for-hours` autonomy has **no expiry** and stays on until you turn it off — a forgotten
`autonomy on` will still be trading unattended weeks later. `keel autonomy show` tells you which
you have and how long is left.

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
- **`signals=0` and no preview — the rule simply didn't fire this cycle.** Nothing broke; no
  `live` rule produced a setup. If your test vehicle is the **DCA rule**, note it is gated to a
  **calendar cadence**: it emits a buy only when the most recent daily candle's day-number since
  the Unix epoch is a multiple of `cadence_days` — for the weekly default (`cadence_days: 7`) that
  is **Thursdays, UTC**. On any other day you get `signals=0` and never reach the confirm prompt.
  Either run on a cadence day, or — for an on-demand test — set the DCA rule's `cadence_days` to
  `1` so it fires every cycle, and revert it afterward. (There is no CLI to edit rule params; it
  is a one-row update to the `rules` table's `params` JSON, which places no order.) The
  risk-defined rules (`turtle_breakout`, `pullback_continuation`, `rsi_meanrev`) instead fire only
  on a genuine market setup, whose timing you cannot choose — so **DCA on a cadence day is the
  controllable path** for a first live order.
- **Equity moved because you deposited or withdrew.** Tell the tool (`keel record-flow --amount
  ±N`), or rail 11 will read the movement as drawdown and veto entries on an account that lost
  nothing.
