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
Coinbase account. Item 3 is not a one-time check: it recurs **weekly** for as long as the agent
runs.

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

> **The sizing-equity purification invariant (#490, discussion #472).** Rewards that slipped through
> anyway do not silently compound: any equity base that position sizing derives from a **live balance
> read** is reduced by pending purification — `mark_to_market − build_report(transactions).
> total_owed_usd` (`keel/execution/equity.py::sizing_equity`). Today that is the paper account's
> balance-derived seed (`paper.starting_equity_usd == 0`), which then sizes every paper fill; the live
> path sizes off `caps.max_exposure_usd` and DCA off `dca.budget_usd`, both operator constants immune
> by construction. Note the boundary: the **drawdown/HWM rail-11 equity is deliberately NOT purified** —
> it measures what the account actually holds, and a breaker must trip on real value, not on a
> post-obligation fiction. Discharging the owed amount (`keel purification` to see it) is still your
> act; keep the imported transaction ledger current so the subtraction sees what actually accrued.
> Note that no discharge is recorded — `total_owed_usd` is lifetime-cumulative (`keel purification`
> renders the same cumulative report), so an amount already given away keeps subtracting from future
> balance-derived seeds, which is the conservative direction; correcting or removing ledger rows is
> the remedy.

### 2. Zakat estimate — **report-only, not blocking**

A zakat estimate (~2.5% of holdings' market value per lunar year) is a **positive** obligation, unlike
item 1's prohibition, and it is informational: keel reports, you decide and discharge it. Tracked at
KB §33.1. No pre-live action; noted here so the account-level obligation set is complete in one place.

### 3. Withdrawal-capability attestation — **weekly refresh** (rail 17)

**Why.** Rail 17 (§65.4 *qabd*) halts all BUY entries unless the withdrawal-capability attestation is
fresh, and "fresh" means a **7-day TTL** (`WITHDRAWAL_ATTESTATION_TTL_SEC`,
`keel/execution/executor.py`). An expired attestation reads as UNKNOWN, and rail 17 fails closed on
unknown — live DCA buys are vetoed. That is not hypothetical: on **2026-08-14** it vetoed the only
live DCA signal because the attestation had lapsed, and as of 2026-08-17 every deployment's
attestation was weeks stale, so rail 17 was halting entries on the **live** deployment (it is a
`LIVE_STATE` rail, skipped in paper, where a stale attestation matters only to the status display).
Each deployment carries its own attestation (they do not share a database), so the live one must be
refreshed for the rail and the paper ones to quiet their status lines.

Rail 17 fails closed by itself — what it cannot do is refresh its own input, and that input is
deliberately human (see the warning below). This entry is the cadence obligation the rail cannot
enforce, which is why it lives here rather than in `guards.py`.

**How to verify.** `keel status` prints the rail-17 line with days-to-expiry (`attested, expires in
3d`, `EXPIRED 12d ago`, or `never attested`) — staleness is visible there *before* it vetoes, not
only in the veto log. `keel withdrawals show` reads the same state with the age to one decimal.

**Cadence.** Re-attest **weekly** — a calendar reminder is the intended mechanism. Confirm the
balances really are withdrawable on demand, then, per deployment:

```bash
keel withdrawals attest --enabled
keel --config config.live-sandbox.yaml --db keel-live.db withdrawals attest --enabled
```

> ⚠️ **The typed confirmation is deliberately human.** `--enabled` RELEASES a rail-17 entry halt and
> demands a typed `yes` at a terminal — so that a scheduled job can never release a §65.4 halt, the
> same posture as `keel autonomy on`. Do not script this command and do not pipe a `yes` into it:
> the weekly habit is the fix for staleness, and automating the release would undo the rail. If a
> calendar reminder ever feels like it should be a cron job, re-read this warning.

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
  Release/keel_broker_alpaca-$V-py3-none-any.whl \
  Release/keel_trader-$V-py3-none-any.whl
.venv/bin/keel versions
.venv/bin/keel status
```

Set `V` to the version being deployed; nothing else changes between releases. The five named
wheels are the production set (#425): the four base wheels plus `keel_broker_alpaca`, the
US-equities venue an equities deployment (`config.paper-equities.yaml`, `broker: name: alpaca`)
resolves through its `alpaca` entry point. A Coinbase-only deployment gets the adapter too — one
unused module, whose single dependency (`requests`) already rides every deployment transitively
via the Coinbase SDK — which is the price of a set stated by name rather than derived from each
deployment's config: an equities deployment must never be upgraded without its adapter, or
`keel versions` fails it with PARTIAL INSTALL.

**Every wheel is named, and that is the fix for a real bug.** Installing `keel_trader` alone
upgraded *only* `keel_trader`: its siblings were required without a version, so the `keel-core`
already on disk satisfied `keel-core` and stayed put. `~/keel` ran `keel-trader 0.5.7` against
`keel-core 0.5.5` for two releases that way. A wheel **path** is a direct requirement — that exact
file is installed whatever is already there — so naming all five is what actually moves them.
The wheels now also pin their siblings exactly (`Requires-Dist: keel-core==0.6.0`), which forces
the upgrade even for someone who installs `keel_trader` alone; the named paths are the same
guarantee stated where the operator can see it.

**Not `Release/*.whl`.** The release ships *every* workspace wheel, three of which a deployment
must not have: `keel_broker_fake`, a dev-only fake venue that registers a `fake` entry point under
`keel.brokers`; `keel_broker_robinhood`, an optional venue that pulls an Ed25519 stack
(`pynacl`, `cffi`) in for an adapter nothing constructs; and `keel_broker_kraken`, a
port-complete stub (#313) whose every data method raises. The five named wheels are production's
whole set. `--find-links Release` still points at that directory so the pinned
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

keel-broker-alpaca     0.6.0
keel-broker-api        0.6.0
keel-broker-coinbase   0.6.0
keel-core              0.6.0
keel-trader            0.6.0

ok: 5 keel distributions, all at 0.6.0.
```

A partial upgrade fails it, with the numbers: `error: PARTIAL INSTALL: 5 keel distributions at 2
different versions (0.5.5, 0.6.0)` — the exact failure an equities deployment hit on every
self-update before #425 moved `keel_broker_alpaca` with the rest. So does finding
`keel-broker-fake` installed — it was, at
`0.5.5`, in `~/keel`. Remove it: `uv pip uninstall --python .venv keel-broker-fake`. Nothing calls
`load_broker()` today so it is inert, but that is a property of this release, not of the package,
and no reason to leave a fake venue registered on the box that moves money.

A build reporting `(DIRTY)` or `[checkout]` corresponds to no commit and **must not be run against
live funds**. Step 4 is a read-only snapshot — no orders, no writes — confirming the new build
opens the database and reaches the venue.

If the deployment runs on a schedule (LaunchAgents, cron), a new build takes effect on the next
cycle with nothing to restart — each cycle is a fresh process. A **long-running** process is the
exception: a `keel serve` left running keeps the build it started with until you stop and restart it.

### Self-update: `keel update`

The four commands above — plus the per-database `keel migrate --db` step the updater also
runs (the four commands don't include it; it runs for every `keel*.db` with the new build,
between install and verify) — are what `keel update` runs for you (issue #415) — same order,
same tools, one service (`keel/commands/update.py`) behind two front-ends: the `keel update`
CLI command and the Account menu's `update` entry in the TUI console (see "The TUI console"
for the ceremony). `keel update --check` mutates nothing: it prints current vs latest and the
whole plan. All of this is for **venv deployments only** — a packaged (desktop) install never
self-updates; it updates by downloading the new installer
(docs/desktop-install.md, "How updates arrive"; decided in docs/decisions/0001-desktop-update-path.md),
and `keel update` on a bundle says so instead of offering one.

**What it does, in the manual procedure's own order.** It reads the latest release from the
public GitHub API (no auth, no tokens — an unauthenticated read is rate-limited to 60/hour per
IP, which a human-gated check never approaches; a rate-limit or network failure is an honest
error, not a guessed "up to date"). It downloads exactly the **five production wheels** —
`keel_core`, `keel_broker_api`, `keel_broker_coinbase`, `keel_broker_alpaca`, `keel_trader`, by
exact name, never `Release/*.whl`, so the fake, Robinhood and Kraken wheels can never ride
along — into `Release/` in the
launch folder, verifying each file landed non-empty (and bounding the read at 200 MiB, far above
the ~1 MiB wheels — a mis-pointed URL is refused, not streamed to disk; a failed download or
install removes the partial files so a torn wheel cannot poison a later rollback).
**Backups first**: every `keel*.db` in the
launch folder is copied to `<db>.bak-before-<version>-<timestamp>` before anything is installed
— through SQLite's own backup API, a consistent snapshot even with a writer mid-transaction,
where a plain file copy can be torn — and the backups are **never deleted** — not on success,
not on failure. It installs the five
wheels by path into the RUNNING
venv with `uv pip install --python <venv> --find-links Release <the five paths>` — the manual
command exactly, `--find-links Release` and all — uv is a **deployment dependency**
of self-update for exactly the reason the manual procedure uses it; an absent uv is an honest
error naming this section. It runs `keel migrate --db` for each database **with the new build**,
then **verifies** with the new build's `keel versions` — every keel distribution must report the
new version, the check that can actually fail. Only a verified success removes the **superseded
wheels** (the old version's five) from `Release/`; the new five stay for the next update.

**Never automatic from what keel ships — always typed.** The full run demands a typed `yes` at
a terminal (the CLI's own confirmation gate, called inside the service before any mutation;
both shipped front-ends — the CLI and the console's update view — hand it exactly that gate),
and that gate fails closed off a TTY, so a scheduled job, which has no terminal, cannot confirm.
The service underneath is a Python API, and an operator's own code could call it with its own
gate — just as the CLI itself can be driven with scripted input on a real TTY; the guarantee is
about what keel ships, not about what is physically expressible. The wording
names the version pair, the launch folder, and that the running binary is replaced.

**A failure is loud and phase-true, never papered over.** pip replaces the packages at install
time, so there is no cheap rollback. When the failure IS the install (uv absent, a timeout, a
corrupt wheel), the updater says the venv was **not updated — or is half-updated** (`keel
versions` shows exactly what is installed), removes the downloaded files, and attempts no
reinstall. When the failure is AFTER a finished install (migrate, verify), the updater says the
new wheels **are** installed, re-installs the **previous** wheels best-effort when they are
still in `Release/` (they are — cleanup only happens on success), and names this section as the
manual recovery — pointing at the `.bak-before-*` backups as the data recovery (the old build
opening the migrated databases is the migrations-are-additive assumption, not a guarantee). The
backups are untouched either way.

**It refuses everything that is not the deployment.** The plan refuses when the running build
is not a release install, when no keel distributions are installed (an `uv run keel` checkout),
and when the running `keel` package does not resolve from the launch folder's own `.venv`
site-packages — a source `keel/` directory under the launch folder (deploying wheels would
shadow the tree, not update it), a package resolving from outside the launch folder (a repo
run: the wheels would land in a venv that is not this deployment's), or an install whose origin
is not the wheels. From a checkout, this section's four commands by hand
remain the procedure.

**Nothing relaunches itself, and there used to be one thing that did.** On a verified success
`keel update` prints what to restart and stops there. Until #541 the TUI replaced its own process
with the new build's `keel` entry (`os.execv`, the terminal restored first), because a curses
front-end left running keeps the build it started with and there was no other way to pick up a new
one without the operator noticing. That code went with the dashboard, and its fallback had already
become wrong: with no arguments to carry it rebuilt `keel tui`, a command that no longer exists.

A long-running `keel serve` has the same property -- it keeps the build it started with -- and
needs no execv to fix it: stop it and start it again, and the browser tab reconnects to whatever is
listening.

**The manual fallback is unchanged.** The four commands at the top of this section still work
and remain the documented procedure when uv is absent, the API is rate-limiting, or you simply
prefer the hand run: `gh release download`, `uv pip install --python .venv` the five wheel
paths, `keel versions`, `keel status`.

## Paper vs. live

A deployment such as `~/keel` runs **two of them side by side**, and they share nothing: separate
configs, separate databases, separate allowlists, separate caps, separate schedules, separate
histories. **A figure from one says nothing about the other.** Checking a paper position size
against live account equity — or a live cap against paper cash — yields a confident wrong answer,
and has already produced one. Establish which account a number came from before reasoning about it.

| | paper | live | paper-hourly | paper-equities |
| --- | --- | --- | --- | --- |
| config | `config.paperforward.yaml` | `config.live-sandbox.yaml` | `config.paper-hourly.yaml` | `config.paper-equities.yaml` |
| database | `keel.db` (the `--db` default) | `keel-live.db` (must be passed) | `keel-paperhourly.db` (must be passed) | `keel-equities.db` (must be passed) |
| `auto_trade.mode` | `paper` | `confirm` | `paper` | `paper` |
| allowlist | BTC, ETH, PAXG, SOL, XLM, LTC, ADA, LINK (8) | BTC, ETH, PAXG, ADA, XLM (5) | paper's 8 + 11 Tier-2 = 19 (#351) | 5 US large caps, **unattested paper candidates** |
| `caps.max_exposure_usd` | 5000 | 200 | 5000 | 5000 |
| money spent | synthetic `paper_cash_usdc` | the real broker balance | synthetic `paper_cash_usdc` | synthetic `paper_cash_usdc` |
| sizing basis | the paper account's own equity | `caps.max_exposure_usd`, as a proxy | the hourly account's own equity | the equity account's own equity |
| rail 14 allowance | $500/month (Basic tier) | $200/month | $500/month | $500/month (simulator assumption; Alpaca has no tiers) |
| `equity_state_mode` | `paper` | `live` | `paper` | `paper` |
| launchd job | `com.keel.paperforward` | `com.keel.live` | `com.keel.paper-hourly` | `com.keel.paper-equities` |
| cadence | daily (day-stamp) | daily, UTC (UTC day-stamp) | **hourly**, UTC (UTC hour-stamp) | daily, in the US session (UTC day-stamp) |
| rules traded | daily turtle, `paper` | daily turtle + DCA, `live` | **hourly** turtle, `paper` | daily turtle on equities, `paper` |

### Installing and verifying a profile's launchd job

**Being tracked in this repo, or even sitting in `~/keel`, schedules nothing.** A plist has to
be COPIED to `~/Library/LaunchAgents/` and then `bootstrap`ped into launchd before it will ever
fire — those are two separate facts, and #640 is what it costs to conflate them.
`com.keel.paper-hourly.plist` has been tracked in this repo since 2026-08-03 and sat correctly
written in `~/keel`, but was never installed. Verified state on 2026-08-31: the hourly book's
last cycle was **2026-08-21 00:20:05Z** — ten days, and roughly 240 missed hourly cycles,
earlier — with **4 orders total (2 BUY / 2 SELL, all 2026-08-20), 19 rules in `paper`, and 0
rows in `trade_outcomes`**, while `~/Library/LaunchAgents/` held only `com.keel.live.plist` and
`com.keel.paperforward.plist`. No keel surface reported it: `keel status` and the pre-#640
`keel doctor` only ever look at the one database a given invocation is pointed at, never at a
sibling profile that has gone dark. `keel doctor`'s `profile.scheduled`/`profile.cycled`
findings close that hole -- see below.

Per profile, after copying `com.keel.<name>.plist` from the deployment directory to
`~/Library/LaunchAgents/`:

```bash
# install (or re-install after editing the plist)
cp com.keel.paper-hourly.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.keel.paper-hourly.plist

# verify it is actually loaded
launchctl list | grep com.keel

# uninstall (before deleting the plist, or before a `bootstrap` re-install)
launchctl bootout gui/$(id -u)/com.keel.paper-hourly
```

`launchctl list | grep com.keel` is the ground truth for "is launchd actually going to run
this" -- a job absent from that list runs on no schedule at all, regardless of what the plist
file says or where it sits on disk. Do this for all four: `com.keel.live`,
`com.keel.paperforward`, `com.keel.paper-hourly`, `com.keel.paper-equities`.

**`keel doctor` is the standing check that this never silently regresses again.** Since #640 it
gathers every profile a deployment declares (plist + runner script + config + sibling
database) and asks two questions no single `--config`/`--db` invocation could ever answer on
its own, because each only ever looks at the ONE database it was pointed at:

- `profile.scheduled` -- does launchd actually have a job for this profile right now (parses
  `launchctl list` itself)? FAILs when a profile is confirmed not loaded -- exactly the
  paper-hourly incident, reproduced as a finding instead of ten days of silence.
- `profile.cycled` -- is each profile's last cycle recent relative to ITS OWN cadence (a
  multiple of `interval_sec`, so an hourly profile and a daily profile are held to their own
  clocks, not one flat threshold)? FAILs on a profile that is loaded but has stalled.

Run `keel doctor` (or `keel doctor --json` for the machine-readable form, `products` array
included) after installing a new profile, and periodically thereafter -- it is what would have
caught the ten-day gap on day one instead of day ten.

**The new cycle shape, and why `doctor` does not gate.** As of #640/#642 all four wrappers run
`fetch`, then `doctor`, then the cycle (`agent`), then `doctor` again. `doctor`'s verdict is
surfaced by a macOS notification when it FAILs, but it is a REPORT, never a second gate: the
engine (`keel/agent.py`'s whole-cycle admission bit) already withholds every entry, on every
product, the instant any rule anywhere is blocked -- deliberately, to close a real-money
duplicate-order hazard (a blocked rule on one product must not leave a DIFFERENT product's
order placed while the day goes unstamped, which would re-enter the placed product on the next
trigger). A wrapper-level gate keyed to one product would be *finer-grained* than the engine
already is, would change nothing about what actually trades, and pushing the engine itself to
decide per-product would reopen the exact hazard that admission bit exists to close. So: **per
product in the report, book-wide in the gate** -- `doctor` names what is wrong; the engine
alone decides what does not trade.

This is also why fetching before every cycle matters more than it looks: on 2026-08-31 the
live book's BTC, ETH, PAXG, XLM and ADA feeds were each 18 hourly bars behind -- all five carry
live rules -- so the whole-cycle admission bit withheld EVERY entry, book-wide, every cycle,
and a single `keel fetch` cleared it. A product with NOTHING cached is skipped entirely and
withholds nothing (harmless, if unwatched); a product merely a few bars BEHIND is what
withholds the whole cycle. The wrappers now fetch before evaluating for exactly this reason,
and the live wrapper additionally gives exit 4 (`agent.DATA_NOT_READY_EXIT`, entries withheld
on data readiness) its own notification, distinct from an ordinary failure or a quiet market.

**Why there are three files per database.** Since keel serves a web UI, one process reads the
database while another writes it — a page refreshing while a fetch or an agent cycle runs. SQLite's
default journal cannot do that (a writer takes an exclusive lock), and it did not: a first fetch
watched from the setup page died at 45 seconds with `disk I/O error`. The databases are now in
**WAL** mode, so readers never block the writer and the writer never blocks readers.

That means `keel.db-wal` and `keel.db-shm` sit beside each `keel*.db`. They are part of the
database — do not delete them while keel is running, and prefer `keel update`'s backups (which use
SQLite's own online-backup API) over copying the `.db` file by hand. Conversion happens on the
next connection and needs nothing from you.

**Which one am I looking at.** On any dashboard (`keel status`, `keel insights`, `keel serve`) the
`equity_state_mode` line names the account the equity, high-water mark and drawdown figures
describe, and `paper_cash_usdc` is printed in paper mode only. On the command line it is the
`--config`/`--db` pair — and `--db` is the one that bites, because `keel.db` is its default, so a
live command that omits it silently reads the **paper** database and answers about the wrong
account. Live commands always carry both:

```bash
keel --config config.live-sandbox.yaml --db keel-live.db status
```

**The same view in a browser: `keel serve`.** This is the only interactive surface keel has; the
curses dashboard it replaced was deleted at #541, for reasons the console section below records.
`keel serve` renders the same reports over loopback HTTP:

```bash
keel --config config.live-sandbox.yaml --db keel-live.db serve
```

It prints a URL carrying a one-time token for that run and opens your browser (`--no-open` to
skip). The `--config`/`--db` pair still decides which account you are looking at, and still bites
the same way.

It is **read-only**, and structurally so: the server answers `GET` and `HEAD` and implements no
other verb, so there is no request it can answer that changes anything. Attesting, promoting a
rule, recording a flow and arming autonomy stay CLI commands behind the interactive-terminal gate.

Four things stand between that page and the rest of the machine: it binds `127.0.0.1`; it checks
the `Host:` header, so a hostname rebound to loopback is refused even though its packets arrive
on loopback; it requires the session token, which is minted per run and never written to disk; and
it serves no write verb. `--host` will bind anywhere you ask, and says plainly what that costs —
on a non-loopback address your positions, equity and full trade history are readable by anyone who
can reach the port, with a cleartext token as the only obstacle.

**Placing an order is gated differently.** Paper places freely against synthetic cash — nothing is
asked and nothing real moves, which is the point. Live runs `mode: confirm`: each order is
previewed and waits for a typed `y` at a terminal, so a headless live cycle **fails closed** and
places nothing — *unless autonomy is armed*, which is exactly what makes an unattended live cycle
place. Autonomy changes who is asked, never what is allowed; check the flag before assuming a
live cycle is supervised, rather than inferring it from `confirm`.

**Both fire hourly; both run once a day** (the third job, `com.keel.paper-hourly`, is the
exception that runs once per UTC *hour* — see "The hourly evidence profile" below — and the
fourth, `com.keel.paper-equities`, runs once per day *inside the US regular session*; see
"The equities paper profile"). Each
launchd job has a list of hourly triggers plus
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

## The hourly evidence profile (paper-hourly)

A third deployment, `config.paper-hourly.yaml` + `keel-paperhourly.db`, running the **same**
turtle rules on a different bar clock: one paper cycle per **UTC hour** (`com.keel.paper-hourly.plist`
fires hourly at :20; `paper-hourly-run.sh` stamps the UTC hour). Use `./keel-paperhourly <command>`
so the config and database always travel as a pair.

**Why it exists: evidence cadence, not profitability.** The daily-turtle rules fire 1.19–3.20
times per asset-year, so a promotion gate demanding n=100 per rule per product is 31–84 years
away — waiting is not a slower path, it is no path. The same rules evaluated on `ONE_HOUR` bars
fire ~50 times per asset-year (median n=268 over the 5-year cached window;
`docs/experiments/2026-08-11-hourly-backtest-turtle-breakout.md`), which makes the sample
collectable in months.

**The honest caveat, which changes nothing about the decision: the hourly configuration is
measured NET-NEGATIVE** — 0 of 90 / 0 of 82 cells at every fee this venue offers, restated
2026-08-13 under the production-faithful engine
(`docs/experiments/2026-08-13-restated-under-a-production-faithful-engine.md`). This profile
produces **admissible evidence** — rail vetoes, outcomes, pending lifespans, intent divergence:
the things a backtest cannot observe — not profit. Do not promote from it on a positive stretch:
n≈250 sequential trades inside one regime are not 250 independent draws. Daily-tuned parameters
on an hourly clock is also, legitimately, a different strategy (the experiment's own §7) — which
is exactly why the forward evidence this profile accrues is the only kind that can settle it.

**Bootstrap.** The database is created at deploy time by the operator and is empty until then
(24 hourly cycles against an unseeded database log `signals=0` and do nothing else — there are
no rules to evaluate):

```bash
keel migrate --db keel-paperhourly.db        # schema only; never seeds
# Seed the hourly rules. `rules seed` cannot do this (it writes each kind's constructor
# defaults, i.e. daily), so add each row with the one param that makes it hourly:
for p in BTC ETH PAXG SOL XLM LTC ADA LINK ZEC NEAR AVAX UNI FET ICP DOT CRV ALGO BCH DOGE; do
  keel --config config.paper-hourly.yaml --db keel-paperhourly.db rules add \
    --kind turtle_breakout --product "${p}-USD" --params '{"granularity": "ONE_HOUR"}'
done
# Advance each printed id candidate -> paper. --force is the documented bypass for a rule
# whose backtest can never clear the gate; for hourly turtle the backtest clears min_trades
# easily and fails on EDGE (the net-negative finding above), so force is deliberate here and
# the warning it prints is the caveat restated:
keel --config config.paper-hourly.yaml --db keel-paperhourly.db rules promote --force <id>
# Warm the candle cache before the first cycle — fetch honors the config's
# market_data.granularities (ONE_HOUR/ONE_DAY/FIFTEEN_MINUTE x 365d here):
keel --config config.paper-hourly.yaml --db keel-paperhourly.db fetch
```

**Arm it.** `keel migrate` creates schema and never seeds, so a fresh database has no
`kill_switch` row and `get_state("kill_switch", default=True)` fails closed — the profile logs
`skipped: kill_switch` on every cycle until this is run:

> ⚠️ **At a terminal, by a human, deliberately.** Same gate as `keel autonomy on` and the rail-17
> release: a scheduled job must never start a halted agent. Do not fold it into the block above,
> do not script it, do not pipe a `yes` into it. `keel resume` (with this profile's
> `--config`/`--db`) disengages the kill switch; `keel autonomy on` is a SEPARATE control,
> deciding who gets asked rather than whether the agent runs. Neither substitutes for the other —
> running only `autonomy on` leaves a halted agent authorised to trade unattended, which is worse
> than either state alone (#693).


**The 2026-08-17 expansion (#351): 8 → 19 assets.** The 11 additions above — ZEC, NEAR, AVAX,
UNI, FET, ICP, DOT, CRV, ALGO, BCH, DOGE — each passed a 15-minute data-health screen over 90
days (coverage ≥ 95.98%, zero zero-volume bars; results recorded in the issue), and each sits at
a flat 2% target weight: the sizing half of the spread guardrail whose live-path half is #350's
spread gate. The 8 incumbents keep their relative shape rescaled to 78% total (rules and params
untouched, so their evidence stays comparable across the expansion). Paperforward — the daily
profile — deliberately stays at 8 so its evidence remains a like-for-like 8-asset series.

### The spread guardrail: a sizing half and a live-path half

Thin books cost more to trade than the cost model assumes, and the corpus's thin tail is
exactly where the expansion above added exposure. The guardrail has two halves, each doing the
half it can:

- **Sizing (#358):** every Tier-2 addition sits at a flat 2% target weight, so a thin book can
  only ever be a 2% position.
- **Live path (#350):** a **routing-time maximum-spread gate** refuses a live BUY when the
  venue's own previewed book shows `(best_ask − best_bid) / mid` at or beyond
  `execution.max_entry_spread_pct` — default **0.005 (50bp)**, set by #334 to equal the
  backtest's slippage cap (`SLIPPAGE_CAP_PCT`). #523 moved that cap to the corpus tail
  (183.8bp) and deliberately left this gate at 50bp, so the two are now independent and the
  gate is the stricter of the pair: if the spread ALONE costs more per leg than the model
  assumes for a $5M/day book, the fill economics are materially worse than anything the rule
  was measured on, and the entry waits for the book to tighten.

The gate is BUY-only (exits must execute — the same principle that makes rail 17 halt entries,
not exits), **fails closed** (a live BUY whose preview carries no readable bid/ask is refused
with a distinct `book_unreadable` reason, never guessed past), and lives beside the twenty
rails rather than among them: `guards.check` is broker-less by design, and the book exists only
in the preview the executor just fetched.

**Paper accrues no evidence about it.** Paper fills are synthetic and see no book, so neither
paper profile ever exercises the gate — a reason it ships before any live resumption (the gate
must already be in force when live BUYs resume) rather than being validated on paper first.
A refusal is visible in the cycle log as `executor.entry_spread_refused` (with the measured
spread and the threshold) or `executor.entry_book_unreadable`.

**The rows differ from every other turtle row by one param.** `params.granularity: "ONE_HOUR"`
— `TurtleBreakout`'s declared trading timeframe, persisted the way `RsiMeanReversion.timeframe`
is and coerced back by `keel/agent.py`'s registry. A row with no `granularity` key (every row
written before the param existed) keeps meaning daily. `keel rules list` shows the param; it is
the one thing to check when a cycle logs `signals=0` and you need to know which clock a row trades.

**Cadence mechanics.** Hourly candles close at the top of each UTC hour; the :20 trigger gives
Coinbase twenty minutes to publish and `data.market_feed` to persist the bar (the same margin
`com.keel.live` uses for the same reason). The runner stamps the UTC **hour**
(`date -u '+%Y-%m-%dT%H'`) — the paperforward day-stamp is daily-grained and would collapse 23
of the 24 cycles into no-ops. The stamp is cadence bookkeeping, not the duplicate-entry barrier
it is on live: the paper path already refuses a second entry while a product is open
(`strategy/paper.py`). A failed cycle leaves the hour unstamped and the next trigger retries
against the then-newest bar. An hour lost to the machine being powered off is lost — the runner
cannot replay bars that closed while it was down; that is an hour of evidence, not an hour of
money, and it is why the profile's duty cycle matters more than its exact schedule.

## The equities paper profile (paper-equities)

A fourth deployment, `config.paper-equities.yaml` + `keel-equities.db`, running the **same**
daily turtle rules on a different asset class: US equities through Alpaca's **paper** API
(`broker: {name: alpaca, endpoint: paper, data_feed: iex}` — the config's `broker:` section is
the whole venue-selection surface; omitting it keeps Coinbase, byte-compatibly). One paper
cycle per day, fired *inside* the US regular session by `com.keel.paper-equities.plist`
(10:00–15:00 local/ET) and stamped on the UTC day by `paper-equities-run.sh`. Use
`./keel-equities <command>` so the config and database always travel as a pair.

**Why it exists: evidence on a session-bound venue, nothing more.** Every profile so far
exercises the engine on one venue and one asset class. This one accrues the same admissible
evidence — rail vetoes, outcomes, pending lifespans, intent divergence — where the venue has
a *clock*: weekends and holidays are read "market closed," never "feed stale" (#370 B1), and
the rails meet a second asset class for the first time. **The honest caveat, which changes
nothing: there is NO PROVEN EDGE on any asset class.** The crypto configurations are measured
net-negative on their own clocks, and these rules have never been measured on equities at all.
Do not promote from this profile on a positive stretch; a new asset class is a new
measurement, not a fresh start for unproven rules (Phase C's cost-fidelity work comes before
any strategy evaluation is believed).

**The allowlist is PAPER CANDIDATES, and asserts nothing religiously.** MSFT, AAPL, GOOGL,
NVDA and COST are liquid US large caps, chosen so a screen *could* be run on them — not
because any has been screened (leverage and the other screening ratios are the operator's
attestation to make, not a fact this file asserts). Trading them here is paper evidence
collection, full stop; see the attestation semantics below for what live consideration would
additionally demand.

### Bootstrap

Deployment to the operator's machine is **out of scope here** (it needs the operator's own
Alpaca paper credentials); the steps, once you have them:

1. **Alpaca paper account.** Create a paper trading key pair in the Alpaca dashboard's paper
   account, and put the values in `.env` (or the environment):

   ```bash
   ALPACA_API_KEY_ID=...
   ALPACA_API_SECRET_KEY=...
   ```

   Paper keys suffice — `endpoint: paper` selects `paper-api.alpaca.markets`, and the adapter
   derives the host from that word and accepts no URL, so these cannot be pointed at the live
   venue by any configuration.

2. **Install the adapter wheel.** The deployment must have `keel-broker-alpaca` installed —
   venue selection resolves `name: alpaca` through the `keel.brokers` entry points, and the
   error names what is installed when it is missing. The equities deployment's wheel list is
   the five named production wheels — since #425 the adapter ships with the standard set, so
   it is not an extra on top of it.

3. **Migrate + seed + warm:**

   ```bash
   keel migrate --db keel-equities.db        # schema only; never seeds
   for s in MSFT AAPL GOOGL NVDA COST; do
     keel --config config.paper-equities.yaml --db keel-equities.db rules add \
       --kind turtle_breakout --product "${s}-USD" --params '{"granularity": "ONE_DAY"}'
   done
   keel --config config.paper-equities.yaml --db keel-equities.db rules promote --force <id>
   keel --config config.paper-equities.yaml --db keel-equities.db fetch
   ```

**Arm it.** `keel migrate` creates schema and never seeds, so a fresh database has no
`kill_switch` row and `get_state("kill_switch", default=True)` fails closed — the profile logs
`skipped: kill_switch` on every cycle until this is run:

> ⚠️ **At a terminal, by a human, deliberately.** Same gate as `keel autonomy on` and the rail-17
> release: a scheduled job must never start a halted agent. Do not fold it into the block above,
> do not script it, do not pipe a `yes` into it. `keel resume` (with this profile's
> `--config`/`--db`) disengages the kill switch; `keel autonomy on` is a SEPARATE control,
> deciding who gets asked rather than whether the agent runs. Neither substitutes for the other —
> running only `autonomy on` leaves a halted agent authorised to trade unattended, which is worse
> than either state alone (#693).


   The `rules add` form (explicit per-symbol rows, granularity stated even though ONE_DAY is
   the constructor default) mirrors the hourly bootstrap so the clock each row trades is
   visible in the row itself. `--force` is the documented bypass for a rule whose backtest
   cannot clear the gate; for equity turtle the gate has not been evaluated on this asset
   class at all — the bypass is deliberate and the warning it prints is the caveat above
   restated. `fetch` warms ONE_DAY × 365d for the five symbols; run it on a weekend and it is
   quiet — B1's session awareness records the closed clock and `--check` does not alert on
   closed-explained staleness.

**Scheduling, in one paragraph.** The plist triggers at 10:00–15:00 local (ET), on the hour —
*inside* the 09:30–16:00 regular session, deliberately not shortly after the close: B1's
session gate skips the whole cycle whenever the venue clock answers closed, so an
after-close trigger would log `market_closed` and never evaluate a bar. The daily bar that
closes at 16:00 ET is evaluated at the *next* session's open — the conventional
daily-system semantics (signal on close, execute next open) — and the 10:00 anchor gives the
open thirty minutes to settle. The runner stamps the **UTC day** (Alpaca keys a session's
ONE_DAY bar to that UTC date, and the UTC rollover at 19:00/20:00 local is always after the
window), refuses to run outside its window — 10:00 inclusive to 16:00 exclusive, local (the
15:00 trigger runs; a closed-market skip exits 0 and must never be stamped as the day's
work) — and writes the stamp only after a successful cycle (a cycle that skipped because the
venue clock could not be *read* exits nonzero, so a transient clock outage is retried by the
next trigger rather than recorded as the day's work).

**Where that schedule is actually correct.** On an ET-anchored host — or one within ±4h of
ET, where the trigger hours still land inside the 09:30–16:00 ET session. The deployment
host's local zone is America/New_York, so the fixed local triggers keep their Eastern
meaning across both US DST transitions: what moves is the UTC instant, never the distance
from the open. Anywhere else, re-anchor the trigger hours so they land 10:00–15:00 **ET**
(on a host far enough ahead of ET, all six triggers can fire pre-open, and the runner's
local-hours guard will still endorse them — it reads the host's clock, not ET — so each day
would be stamped by a closed-market skip: permanently zero evidence). The guard is a
backstop against off-schedule boots, not a drift absorber for a mis-anchored schedule.

### Attestation semantics for equities

Equity screening criteria (business-activity screens, leverage ratios, purification) are
**operator-supplied classifications from attributed sources** — the engine computes market
facts and never classifies, exactly as on crypto. Attestations are keyed per
`(alpaca, SYMBOL)`: an equity instrument attests under its own venue namespace and is never
reused from a Coinbase row. The sources to watch are the ones the fiqh source review (#367)
already names for this territory: **AAOIFI**'s screening standards and **IFSB**'s
pronouncements (plus any scholar the operator trusts) — an attestation without a source is
not evidence. Two honest limits, stated rather than papered over:

- `keel assets attest-instrument --venue alpaca --product MSFT-USD --wrapper spot` records
  the *instrument* half (what contract the listing is) and works today.
- The *asset*-level screen (`keel assets screen`) is Coinbase-shaped by construction — its
  venue constant is deliberately hardcoded to `coinbase` (open item below) — so until the
  screen generalizes (#233 live-path work), equity classifications live in the operator's
  records, and this profile trades as **unattested paper candidates**. That is precisely why
  the config's allowlist carries its disclaimer and why nothing here is live.

**Dividend purification is fenced to Phase B3 — planned, not forgotten.** Purification
appears above only as a classification input (the ratio the operator attests). The walk the
fiqh source review implies — corporate actions (dividends, splits) recorded per event as
they occur (FR-10's recording duty), the purification amount computed against the attested
ratio under the operator's stated policy, and the disposition (how much, and where it went)
recorded — is the **B3 slice of this phase** (corporate actions + purification recording).
Until B3 lands, nothing here computes or records that walk, and a holder of dividend-paying
candidates carries the purification obligation in their own records.

### Rail 17 (withdrawal capability) for equities

"Can this asset leave this venue?" maps to **transfer-out capability** — for a US brokerage,
an ACATS transfer to another broker. It is attested like any venue:

```bash
keel --config config.paper-equities.yaml --db keel-equities.db withdrawals attest --enabled
```

Rail 17 is a live-state rail (skipped in paper), so this is recorded for the day live is ever
considered, and it lapses weekly like every deployment's attestation.

### T+1 settlement × daily cadence

US equities settle T+1: sale proceeds become spendable the next business day. Settlement is
**venue-side** — nothing keel can enforce about *when* the venue settles, so this section is
the documented half of a split whose enforced half already exists:

- **What keel enforces/surfaces**: the balances read reports the spendable figure honestly —
  `available` is the account's buying power clamped at cash, so unsettled T+1 proceeds show
  up as the gap between `available` and `total` rather than as spendable money (FR-6, the
  #562 balance work) — and **rail 13** (the spend rail) vetoes any BUY whose notional
  exceeds the reported available quote balance, failing closed when the balance is
  unreadable. The engine cannot spend what it cannot see.
- **What the operator must respect**: cadence choices that would re-spend unsettled
  proceeds. On the profile's **daily** cadence the constraint is met by construction — the
  next entry attempt is at least a day after the previous buy, by which time it has settled
  (a weekend makes it longer, never shorter), and **exits are never T+1-blocked**: a SELL
  produces cash rather than spending it. The one documented cash-crunch case is an operator
  manually redeploying same-day sale proceeds *outside* the engine — an operator act, not an
  engine one. The same `interval_sec` that makes this true also scales the feed-staleness
  window (B1), so any future tighter equities cadence must re-answer settlement and
  staleness together; a sub-daily cadence is not merely a staleness question.

The paper profile's synthetic cash does not model settlement at all; that honesty is on
record for any future live consideration.

### Account posture: cash only — never margin — and the PDT rule

The equities profile runs against a **cash-equivalent posture, never borrowed funds**. Margin
borrowing is a loan that charges interest — *riba* — so the posture is categorical, not a
preference; **no-borrowing is the posture's whole claim, and it sidesteps nothing on PDT.**
The PDT rule: FINRA flags a **margin** account as a pattern day trader when it executes four
or more day trades within five business days, and such an account then needs $25,000 of
equity to keep day trading — the rule binds margin accounts, and a true cash account would be
exempt, but **Alpaca offers no true cash accounts**: per Alpaca staff on the cash-account
option (forum.alpaca.markets/t/dan-wheres-the-cash-only-account-option/18353), "currently all
Alpaca accounts are margin accounts" — at `max_margin_multiplier=1` "the account remains a
margin account" and "pattern day trading rules apply". What actually keeps keel clear of PDT
is the **cadence**: keel evaluates a session's bar once and holds overnight by construction,
so it does not day-trade in the first place; settled-cash funding is what makes entries wait
for settlement, and that interplay is the T+1 settlement section above (not repeated here).

**Enforced in code since #372, not just documented.** Alpaca has no `account_type` field;
`/v2/account`'s `multiplier` is the venue's account margin classification, and it is as
cash as the venue gets: Alpaca opens every account as margin and offers no true cash
designation — multiplier **1** is the cash-equivalent posture (buying power equals cash,
shorts refused), **2** reg T margin, **4** PDT day-trading margin. At broker build — every
command that constructs one: the agent cycle, `fetch`, `monitor`, the order paths —
`AlpacaAdapter.verify_cash_account` reads that classification and **refuses** a margin
postured account (`CashAccountRequired`, naming riba, the honest PDT note — the posture
buys no exemption; the cadence is the PDT safety — and the fix), and refuses fail-closed
when the classification cannot be read: silence is not
consent to borrow. The engine never sees a broker on a margin account.

**The operator's half — set the multiplier, and expect to.** The venue's *default* for any
account with $2,000 or more equity is reg T margin (multiplier 2) — and a fresh Alpaca
paper account carries $100,000 of paper equity, so the default classification is MARGIN:
the first cycle against an untouched paper account refuses until the setting is changed.
Set the account's **max margin multiplier to 1** (the dashboard's trading-configuration
setting; at 1 the venue refuses orders beyond available cash and blocks shorts), keep it
there, and treat any offer to "upgrade" to margin as a posture violation to decline, not a
capability to use. **Confirm the classification took** before moving on: re-run
`keel assets holdings` — a cash classification builds the broker and lists balances, a
margin one still refuses — or read `GET /v2/account`'s `multiplier` directly, which must
answer `1`. If the refusal persists with the setting saved, that is a question for Alpaca
support, not something to work around: the setting you changed lives on the venue's
account-configurations object (`max_margin_multiplier`, `PATCH /v2/account/configurations`)
while the classification keel reads lives on the account object itself, and the venue
implies but does not document the linkage between the two.

The posture is one system with the rest of the engine's constraints — where each piece
lives:

| Constraint | Where it lives | Why there |
| --- | --- | --- |
| No margin borrowing (riba) | **Enforced in code**: `verify_cash_account` at broker build refuses any multiplier ≠ 1, fail-closed on unreadable | The venue reports the classification; refusing at build covers every path before one sees a broker |
| Cash-only declared | `BrokerCapabilities.cash_only` — every adapter declares it, `keel brokers list` shows "cash only" | The port's declaration seam: vocabulary for the day an adapter announces a borrowing path |
| Long-only, no shorting | **Enforced in code**, pre-existing: `Setup.direction` is `Literal["long"]` (a short entry cannot be constructed), exits only close held longs, rails 18/19 refuse non-spot shapes in every mode | Not new in #372 — cited so the posture reads as one system, not three |
| T+1 settlement churn | **Documented** (the section above): settlement is venue-side; keel surfaces the spendable figure and rail 13 spends only that | Nothing keel can enforce about when the venue settles |
| PDT $25k threshold | **Documented** (this section): keel's CADENCE — one evaluation per session bar, holds overnight by construction — is the PDT safety; the posture claims no exemption (at multiplier 1 the account remains a margin account under PDT rules) | The safety is the cadence, not the posture; there is no separate rule to enforce |
| Stock lending / cash sweep OFF | **Operator-verified** (the section below) | No order is placed; no rail can see account settings |

### Operator-verified opt-outs (Alpaca account level)

Two account settings the venue offers conflict with the posture the engine enforces, and
neither is visible to any rail (no order is placed). **Verify both are OFF in the Alpaca
dashboard** — under the account's settings, the stock-lending (fully-paid securities lending)
enrollment and the cash sweep / interest program enrollment:

- **Stock lending is OFF** — lending out held shares conflicts with *qabd* (possession; the
  engine's own possession rail assumes held means held) and the income is interest-like.
- **The high-yield cash sweep is OFF** — interest on idle USD is *riba*.

These are operator-verified obligations in the same class as the pre-live checklist's USDC
Rewards item: account settings no rail can see, re-checked after any account change.

### What is deliberately NOT here

- **`keel/assets` screening venue semantics** stay hardcoded to `coinbase` (`_VENUE` in
  `keel/cli.py`) — that hardcoding is deliberate pending #233's capability-declaration work
  on the live path; the paper profile does not need it, and the attestation section above
  records the consequence.
- **Deployment to the operator's machine** — needs the operator's Alpaca paper credentials;
  this section is the bootstrap, and the plist/runner/wrapper are authored for the
  America/New_York host like every sibling.
- **Cost fidelity and the DCA benchmark** — Phase C (PRD §6.3): Alpaca's real cost structure
  (regulatory fees on sells, spread, IEX-vs-SIP data fidelity) is measured and documented
  before any strategy evaluation on this asset class is believed.
- **Trademark posture** — unchanged and stated where it lives: the README's standing
  disclaimer covers Alpaca alongside every other venue, and nothing here duplicates it.

## The operator console, in a browser

`keel serve` opens keel's **operator console**: a local web page showing Status, Setup, Activity,
Insights, Rules, Venues and Gates, over the same `keel/commands/*` service layer the CLI commands
call. An architectural test (`tests/commands/test_console_thinness.py`) pins that thinness -- the
front-end renders and dispatches, and every behaviour comes from the services.

```bash
keel --config config.live-sandbox.yaml --db keel-live.db serve
```

It binds loopback and prints a URL carrying a one-time token for that run. The token is never
written to disk, so stopping the server invalidates it.

**`keel tui` was the console until #541, and it is gone.** It needed a terminal, and there were two
places it could not go: Windows, where CPython ships no `curses`, and a macOS app launched from
Finder, which has no controlling terminal at all -- both of them platforms a desktop release
targets. The menu tree it carried (Profile, Trading, Rules, Compliance, Data, Research, Account,
Help) went with it, along with roughly 24,000 lines of code and tests.

**On a headless host, forward the port rather than reaching for a terminal UI:**

```bash
ssh -L 8765:127.0.0.1:8765 your-host
```

The browser at the reading end gets the full interface, encrypted by SSH, and `http://127.0.0.1`
is still a secure context there -- which is what makes the installable app work. `keel status`
covers the rest from a plain shell.

**What the browser cannot do, and that is deliberate.** Every capability-increasing action --
arming autonomy, releasing the kill-switch, clearing a consecutive-loss halt, re-seeding the
drawdown high-water mark, declaring a deposit or withdrawal, attesting withdrawal capability, and
replacing the binary -- is a CLI command behind a typed confirmation at an interactive terminal.
`keel capabilities` lists all seven with the gate covering each. The server implements no verb
that would reach one, so this is a property of the server rather than of what the page draws.

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

## Alerts and notifications

Two outbound channels, one URL, zero control surface.

**The CRITICAL webhook (always on when configured).** keel escalates act-now conditions by
logging CRITICAL — `reconcile.position_unprotected` is the sharpest — and
`WebhookAlertHandler` (`keel_core/alerting.py`) POSTs each such record off the machine. The
URL lives in `KEEL_ALERT_WEBHOOK` (environment, or the git-ignored `.env` — it is closer to a
credential than to configuration, and `config.yaml` is committed). No URL configured means
no handler attached at all: an offline install makes zero network calls. This channel is
unchanged by the notification layer below and stays independent of it.

**Opt-in event notifications (default OFF).** The events an operator most needs are silent
precisely because they are not errors — rail 17's attestation nearing expiry fails CLOSED and
quietly vetoed a real setup for weeks before anyone opened the TUI. The notification layer
(#444) delivers those over the SAME generic webhook, per-event opt-in in Freqtrade's
`notification_settings` shape:

```yaml
# config.yaml — nothing is sent until an event is opted in here AND
# KEEL_ALERT_WEBHOOK is set. Both, always.
notifications:
  format: plain        # plain = generic JSON (default); slack = {"text": ...} chat payload
  events:
    attestation.expiring: true
    rail.armed: true
    setup.unplaced: true
    allowance.nearing_exhaustion: true
    feed.stale_open_position: true
```

The taxonomy (thresholds are the ones `keel doctor` computes — the notification layer reads
doctor's own findings, so the alert and the diagnostic can never disagree):

| Event key | Fires when | Why it is not a CRITICAL log |
|---|---|---|
| `attestation.expiring` | rail-17 withdrawal attestation has ≤2 of its 7 TTL days left (or has expired, or was never attested) | an expired attestation silently vetoes every entry — cycles keep running, nothing errors |
| `rail.armed` | rail 16's consecutive-loss halt arms, or rail 11's drawdown reaches 20% | a halt is a correct state, not a fault; the kill switch is deliberately absent (you engaged it at a terminal — you know) |
| `setup.unplaced` | a cycle detected an entry setup and could not place it | the veto is a WARNING; the rail-17 incident looked like a quiet week |
| `allowance.nearing_exhaustion` | month-to-date BUY spend reaches 80% of the in-force rail-14 allowance — or there is spend against an allowance of 0 (no subscription in force: lapsed or never attested) | rail 14 only speaks when it vetoes, which is too late to re-tier |
| `feed.stale_open_position` | a product's feed is stale while a position is open in it | the stale product is skipped at INFO; an open position's exits ride on that stopped data |

Payloads: `plain` is a flat JSON object (`event`, `severity`, `category`, `message`, plus the
numbers — `pct_used`, days remaining in the message); `slack` is Slack-compatible
`{"text": ...}` — accepted natively by Slack and Mattermost, and by Discord via a `/slack`
webhook endpoint. Delivery is one attempt per event per cycle, fire-and-forget at the end of
each agent cycle: each opted-in event is one inline POST with a 5-second timeout at the
cycle's tail, bounded by the events the cycle derived (one per taxonomy key, plus one per
armed rail and one per stale product held open) — so a dead endpoint costs a notification,
never a cycle, and a state that persists re-alerts on the next cycle anyway.

**Notify-only, by design.** There is no remote control surface — no command, query or
capability arrives through notifications, ever. Every capability-increasing action stays
TTY-gated (`keel capabilities` inventories them; #436), which is why this feature adds zero
gate call sites: outbound messages cannot release a rail, arm autonomy, or place an order.
The layer also writes nothing back — it reads the same repo keys `keel doctor` reads and
sends; that is the whole of it.
