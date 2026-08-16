# keel — operator runbook

Procedures a **human** must perform, because the system cannot.

This file has a deliberately narrow scope: **compliance obligations that no rail can enforce.** Every
rail in `keel/execution/guards.py` inspects an `OrderIntent` — so anything that isn't an order is
invisible to all of them, by construction. Those obligations live here instead.

Things enforced *in code* do **not** belong in this file. Rail 14, for example, already refuses live BUYs
until a subscription is attested (`keel subscription attest`); it needs no runbook entry because it fails
closed on its own. If an item here ever becomes machine-verifiable, move it into the code and delete it
from this file.

Design home: `docs/superpowers/specs/2026-07-16-keel-broker-abstraction-design.md` §3.1
(`CompliancePolicy` account-level obligations).

---

## Pre-live checklist

Run through this **before arming the agent for live trading**, and re-check after any change to the
Coinbase account.

### 1. ⛔ Disable interest / rewards on idle balances — **required**

**Why.** Coinbase pays **USDC Rewards** on idle USDC balances. This applies whenever you hold idle
USDC — which is the case if you settle in USDC, and remains the case for any USDC you keep aside.
(Since 2026-07-22 rail 13 checks the quote leg of the product being traded rather than a single
configured currency, so a `-USD` deployment holds USD between trades; the rewards concern still
applies to any USDC balance you do hold.) Interest accruing on that balance is **riba**
(KB §56.3, grounded in §28.1 / §30.1) — and it accrues **with no order placed**, so no rail sees it.
This is not a trading decision the system can veto; it is an account setting only you can change.

**How to verify** (manual — see the limitation below):

1. Open the **Coinbase consumer app or web account** (not Advanced Trade).
2. Find **USDC Rewards** — typically under *Assets → USDC*, or *Settings → Rewards / Earn*.
3. Confirm it is **off / not enrolled**. Opt out if it is on.
4. Check any other yield, staking, earn or lending feature on the same account is likewise off.
5. Re-check after Coinbase product changes — enrolment has historically been enabled by default in some
   regions.

> ⚠️ **This cannot currently be automated, and should not be faked.** USDC Rewards is a consumer-account
> product; the **Advanced Trade API does not expose enrolment status** (no `reward`/`interest`/`earn`/
> `yield` endpoint exists in the SDK), and the broker port surfaces only capabilities, candles, balances,
> preview, place and fee-summary. A `usdc_rewards_disabled: true` flag in `config.yaml` would record
> *what you asserted*, not *what is true* — and a green check that verifies nothing is worse than an
> honest manual step, because it turns an open risk into a false assurance. If Coinbase ever exposes the
> state, promote this to a startup assertion and remove it from here.

### 2. Zakat estimate — **report-only, not blocking**

A zakat estimate (~2.5% of holdings' market value per lunar year) is a **positive** obligation, unlike
item 1's prohibition, and it is informational: keel reports, you decide and discharge it. Tracked at
KB §33.1. No pre-live action; noted here so the account-level obligation set is complete in one place.

---

## Adding to this file

An item belongs here only if **all** of these hold:

- it is a compliance obligation (not an operational preference), **and**
- no rail can enforce it — there is no `OrderIntent` to inspect, **and**
- it is not machine-verifiable today.

If the third stops being true, implement the check and delete the entry. If the second stops being true,
it is a rail, and it belongs in `guards.py`.

---

# Part 2 — Operating a deployment

Moved here from the README when it was rewritten for newcomers (#281): everything below is
operator knowledge — deploying, upgrading, and keeping the paper and live accounts straight —
not something a first-time reader needs. It is unchanged in substance.

## Deploying a new version

Cutting a release is `docs/RELEASING.md`. Installing one into a deployment (e.g. `~/keel`) is four
commands, run **from the deployment directory** — every path below is relative to it:

```bash
V=0.6.0
gh release download "v$V" --repo CodeGateSoftware/keel --pattern '*.whl' --dir Release/
uv pip install --python .venv --find-links Release \
  Release/keel_core-$V-py3-none-any.whl \
  Release/keel_broker_api-$V-py3-none-any.whl \
  Release/keel_broker_coinbase-$V-py3-none-any.whl \
  Release/keel_trader-$V-py3-none-any.whl
.venv/bin/keel versions
.venv/bin/keel status
```

Set `V` to the version being deployed; nothing else changes between releases.

**Every wheel is named, and that is the fix for a real bug.** Installing `keel_trader` alone
upgraded *only* `keel_trader`: its siblings were required without a version, so the `keel-core`
already on disk satisfied `keel-core` and stayed put. `~/keel` ran `keel-trader 0.5.7` against
`keel-core 0.5.5` for two releases that way. A wheel **path** is a direct requirement — that exact
file is installed whatever is already there — so naming all four is what actually moves them.
The wheels now also pin their siblings exactly (`Requires-Dist: keel-core==0.6.0`), which forces
the upgrade even for someone who installs `keel_trader` alone; the four paths are the same
guarantee stated where the operator can see it.

**Not `Release/*.whl`.** The release ships *every* workspace wheel, two of which a deployment must
not have: `keel_broker_fake`, a dev-only fake venue that registers a `fake` entry point under
`keel.brokers`, and `keel_broker_robinhood`, an optional venue that pulls an Ed25519 stack
(`pynacl`, `cffi`) in for an adapter nothing constructs. The four named wheels are production's
whole dependency closure. `--find-links Release` still points at that directory so the pinned
siblings resolve locally rather than from PyPI, where they do not exist — which is why step 1
downloads them all. Installing **by path** rather than by bare name is deliberate and unchanged:
`keel` on PyPI is an unrelated project, so `pip install keel` fetches a stranger's code (see
`keel/version.py`).

Step 3 is the check that matters, and it is `keel versions` — **not** `keel --version`, which
could not fail. `--version` reports the `keel-trader` distribution's version and nothing else, so
it printed `0.6.0` while `keel-core` sat at `0.5.5`: a verification step blind to the failure mode,
which is worse than none, because it is trusted. `keel versions` prints the same build identity,
then every keel distribution in that venv, and **exits non-zero** when they disagree:

```
keel 0.6.0+deb8fa7e978d [release]

keel-broker-api       0.6.0
keel-broker-coinbase  0.6.0
keel-core             0.6.0
keel-trader           0.6.0

ok: 4 keel distributions, all at 0.6.0.
```

A partial upgrade fails it, with the numbers: `error: PARTIAL INSTALL: 4 keel distributions at 2
different versions (0.5.5, 0.6.0)`. So does finding `keel-broker-fake` installed — it was, at
`0.5.5`, in `~/keel`. Remove it: `uv pip uninstall --python .venv keel-broker-fake`. Nothing calls
`load_broker()` today so it is inert, but that is a property of this release, not of the package,
and no reason to leave a fake venue registered on the box that moves money.

A build reporting `(DIRTY)` or `[checkout]` corresponds to no commit and **must not be run against
live funds**. Step 4 is a read-only snapshot — no orders, no writes — confirming the new build
opens the database and reaches the venue.

If the deployment runs on a schedule (LaunchAgents, cron), a new build takes effect on the next
cycle with nothing to restart — each cycle is a fresh process. A **long-running** process is the
exception: a `keel tui` left open keeps the build it started with until you quit and relaunch it.

## Paper vs. live

A deployment such as `~/keel` runs **two of them side by side**, and they share nothing: separate
configs, separate databases, separate allowlists, separate caps, separate schedules, separate
histories. **A figure from one says nothing about the other.** Checking a paper position size
against live account equity — or a live cap against paper cash — yields a confident wrong answer,
and has already produced one. Establish which account a number came from before reasoning about it.

| | paper | live |
| --- | --- | --- |
| config | `config.paperforward.yaml` | `config.live-sandbox.yaml` |
| database | `keel.db` (the `--db` default) | `keel-live.db` (must be passed) |
| `auto_trade.mode` | `paper` | `confirm` |
| allowlist | BTC, ETH, PAXG, SOL, XLM, LTC, ADA, LINK (8) | BTC, ETH, PAXG, ADA, XLM (5) |
| `caps.max_exposure_usd` | 5000 | 200 |
| money spent | synthetic `paper_cash_usdc` | the real broker balance |
| sizing basis | the paper account's own equity | `caps.max_exposure_usd`, as a proxy |
| rail 14 allowance | $500/month (Basic tier) | $200/month |
| `equity_state_mode` | `paper` | `live` |
| launchd job | `com.keel.paperforward` | `com.keel.live` |

**Which one am I looking at.** On any dashboard (`keel status`, `keel insights`, `keel tui`) the
`equity_state_mode` line names the account the equity, high-water mark and drawdown figures
describe, and `paper_cash_usdc` is printed in paper mode only. On the command line it is the
`--config`/`--db` pair — and `--db` is the one that bites, because `keel.db` is its default, so a
live command that omits it silently reads the **paper** database and answers about the wrong
account. Live commands always carry both:

```bash
keel --config config.live-sandbox.yaml --db keel-live.db status
```

**Placing an order is gated differently.** Paper places freely against synthetic cash — nothing is
asked and nothing real moves, which is the point. Live runs `mode: confirm`: each order is
previewed and waits for a typed `y` at a terminal, so a headless live cycle **fails closed** and
places nothing — *unless autonomy is armed*, which is exactly what makes an unattended live cycle
place. Autonomy changes who is asked, never what is allowed; check the flag before assuming a
live cycle is supervised, rather than inferring it from `confirm`.

**Both fire hourly; both run once a day.** Each launchd job has a list of hourly triggers plus
`RunAtLoad`, and each runner is day-stamped: the first eligible trigger that finds no stamp for
today runs the cycle and writes the stamp, and every later trigger that day is a no-op. The
trigger count is **catch-up breadth, not cadence** — launchd re-runs a calendar interval missed
while asleep but *not* one that passed while the machine was off, so the extra triggers are what
stop a shutdown over the scheduled hour from losing the day outright. A cycle that **fails** leaves
no stamp, so the next hour retries it, which also covers waking with no network. The two jobs
differ only in anchor: paper fires 09:00–20:00 local and stamps the local date; live fires hourly
at :20 and gates and stamps on the **UTC** date, because a daily bar is not visible until the
00:00–01:00 UTC hourly candle has closed. On live the stamp is a correctness mechanism, not tidiness
— nothing on that path dedupes an entry, so two cycles in one UTC day means two entries off one
daily bar (`tests/test_schedule.py` pins it).

**Sizing is a different calculation on each.** Paper sizes off its own synthetic equity, passed to
`_build_intent` as `equity_override`; the live path has no equity reading here and falls back to
`caps.max_exposure_usd` as a proxy (`keel/execution/executor.py`). The same rule, the same setup and
the same day therefore produce different quantities on the two accounts, and neither is an estimate
of the other. The settings behind those numbers are covered next.

## How much money moves

Four settings decide position size and how much can be spent. Three live in `config.yaml`; the
fourth does not, which is most of why they drift apart.

- **`paper.starting_equity_usd`** — the synthetic paper account's seed. **It is a ONE-TIME seed,
  applied on the FIRST paper run only** (`keel/agent.py`, the `paper_trader.get_cash() is None`
  branch). Editing it afterwards does nothing at all: the seeding branch is skipped whenever
  `paper_cash_usdc` is already set, so an already-seeded account keeps its balance forever.
  Resizing a running paper account means clearing that persisted `paper_cash_usdc` — a key in the
  `agent_state` table, with **no command that clears it** (`keel reset-hwm` does not); a fresh
  database is the clean way. `0` (the default) means "seed from real mark-to-market equity
  instead"; any value above `0` overrides that and seeds at exactly that amount.
- **`paper.monthly_contribution_usd`** — a recurring top-up, applied once per UTC calendar month.
  It compounds, and the base is small: a contribution comparable to the seed doubles the account
  monthly, and every position size below grows with it.
- **`caps.max_exposure_usd`** — has **two jobs at once**. It is the ceiling on total notional held
  at any one moment (rail 4, and rail 6's concentration cap is a percentage of it), *and* it is the
  **equity proxy that sizes orders** on the live path
  (`keel/execution/executor.py::_build_intent`). So live `risk_pct` is a fraction of THIS number,
  not of real account equity — raising the cap raises the real dollars risked per trade. Set above
  actual equity it stops binding before available cash does, and the refusal comes later and less
  legibly from the funding check (rail 13). In paper mode the proxy is bypassed: sizing uses the
  paper account's own equity.
- **rail 14's monthly allowance** — the fee-free monthly BUY volume. It lives in the **database,
  not `config.yaml`**: the `broker_subscriptions` row written by `keel subscription attest --venue
  coinbase --tier <tier>`, or set directly with `keel subscription set --free-volume-usd N`.
  `config.yaml` only supplies the tier catalogue and the unattested fallback
  (`subscription.unsubscribed_allowance_usd`). Being in a different place from the caps is exactly
  why it drifts out of step with them.

**The interaction is the point.** Position sizing scales with equity (or, on the live path, with
the `max_exposure_usd` proxy); the rail-14 allowance is a fixed dollar figure that scales with
nothing. Let the two drift apart and *every* setup is vetoed — keel looks broken while every
component is doing exactly what it was configured to do.

The real case: at **$11,000** paper equity with `risk_pct: 0.01`, a PAXG setup with a 3.35%-wide
stop sized to **$3,284.67** — exactly 1% of equity ($110) at risk, the correct answer. Rail 14's
allowance was **$500/month**, so it was vetoed, as was every other setup. Not a bug in either
setting; the two were simply on different scales. Reseeding the paper account at $500 sizes the
same setup at **$149.30**, which fits.

Note the counter-intuitive mechanic behind those numbers: **a tighter stop produces a LARGER
position**, because `size = risk ÷ stop-distance` (`keel/execution/sizing.py::size`). That is how
a 1% risk becomes a **30% position** — `risk_pct` bounds what you lose if the stop holds, not what
you spend.
