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

### Self-update: `keel update` and the console's update view

The four commands above are what `keel update` runs for you (issue #415) — same order, same
tools, one service (`keel/commands/update.py`) behind two front-ends: the `keel update` CLI
command and the Account menu's `update` entry in the TUI console (see "The TUI console" for the
ceremony). `keel update --check` mutates nothing: it prints current vs latest and the whole plan.

**What it does, in the manual procedure's own order.** It reads the latest release from the
public GitHub API (no auth, no tokens — an unauthenticated read is rate-limited to 60/hour per
IP, which a human-gated check never approaches; a rate-limit or network failure is an honest
error, not a guessed "up to date"). It downloads exactly the **four production wheels** —
`keel_core`, `keel_broker_api`, `keel_broker_coinbase`, `keel_trader`, by exact name, never
`Release/*.whl`, so the fake and Robinhood wheels can never ride along — into `Release/` in the
launch folder, verifying each file landed non-empty. **Backups first**: every `keel*.db` in the
launch folder is copied to `<db>.bak-before-<version>-<timestamp>` before anything is installed,
and the backups are **never deleted** — not on success, not on failure. It installs the four
wheels by path into the RUNNING
venv with `uv pip install --python <venv> <the four paths>` — uv is a **deployment dependency**
of self-update for exactly the reason the manual procedure uses it; an absent uv is an honest
error naming this section. It runs `keel migrate --db` for each database **with the new build**,
then **verifies** with the new build's `keel versions` — every keel distribution must report the
new version, the check that can actually fail. Only a verified success removes the **superseded
wheels** (the old version's four) from `Release/`; the new four stay for the next update.

**Never automatic — always typed.** The full run demands a typed `yes` at a terminal (the CLI's
own confirmation gate, inside the service: there is no ungated code path to the writes), and the
gate fails closed off a TTY, so no cron job or script can ever update a deployment. The wording
names the version pair, the launch folder, and that the running binary is replaced.

**A failed verify is loud, never papered over.** pip replaces the packages at install time, so
there is no cheap rollback; the updater says exactly that state, re-installs the **previous**
wheels best-effort when they are still in `Release/` (they are — cleanup only happens on
success), and names this section as the manual recovery. The backups are untouched either way.

**It refuses dev/source checkouts.** The plan refuses when the running build is not a release
install, when no keel distributions are installed (an `uv run keel` checkout), or when the
running `keel` package resolves from the launch folder itself — deploying wheels into a source
tree would shadow it, not update it. From a checkout, this section's four commands by hand
remain the procedure.

**The relaunch split.** On a verified success the **TUI relaunches itself** — it replaces its
own process with the new build's `keel` entry (`os.execv`, the terminal restored first, the
original TUI arguments carried over), because a console left running would keep the replaced
binary it started with. The **CLI prints the command instead** and does NOT relaunch anything:
`keel update` ends by telling you to run `keel tui` (or your deployment wrapper). A wrapper
invoked directly (`./keel-live tui`) relaunches through the venv's `keel` entry with the same
flags.

**The manual fallback is unchanged.** The four commands at the top of this section still work
and remain the documented procedure when uv is absent, the API is rate-limiting, or you simply
prefer the hand run: `gh release download`, `uv pip install --python .venv` the four wheel
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
  `execution.max_entry_spread_pct` — default **0.005 (50bp)**, anchored to the backtest's
  worst-case per-leg slippage assumption (#334's `SLIPPAGE_CAP_PCT`): if the spread ALONE
  consumes the model's entire cost estimate, the fill economics are materially worse than
  anything the rule was measured on, and the entry waits for the book to tighten.

The gate is BUY-only (exits must execute — the same principle that makes rail 17 halt entries,
not exits), **fails closed** (a live BUY whose preview carries no readable bid/ask is refused
with a distinct `book_unreadable` reason, never guessed past), and lives beside the eighteen
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
   the usual four plus this one.

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

US equities settle T+1: sale proceeds become spendable the next business day. On a **daily**
cadence this is immaterial for entries — the next entry attempt is at least a day after the
previous buy, by which time it has settled (a weekend makes it longer, never shorter).
**Exits are never T+1-blocked**: a SELL produces cash rather than spending it, and the engine
never needs to spend sale proceeds within a cycle. The documented cash-crunch case: on a cash
account you cannot spend *unsettled* proceeds, so an operator manually redeploying same-day
sale proceeds (outside the engine, which cycles daily) is the one way to meet the constraint
— and it is an operator act, not an engine one. The paper profile's synthetic cash does not
model settlement at all; that honesty is on record for any future live consideration.

### Account posture: cash only — never margin — and the PDT rule

The equities profile runs against a **cash account, never a margin account**. Margin
borrowing is a loan that charges interest — *riba* — so the posture is categorical, not a
preference; a cash account also sidesteps the pattern day trader (PDT) rule's $25,000
minimum-equity requirement, which applies to **margin** accounts only. The PDT rule: FINRA
flags a **margin** account as a pattern day trader when it executes four or more day trades
within five business days, and such an account then needs $25,000 of equity to keep day
trading. A cash account running keel's daily cadence is not that pattern — the rule does not
bind cash accounts, and in any case keel evaluates a session's bar once and holds overnight
by construction, so it does not day-trade in the first place; settled-cash funding is what
makes entries wait for settlement, and that interplay is the T+1 section above (not repeated
here).

Enforcement in code — the config refusing a margin posture where the venue reports one — is
issue **#372**'s scope; this runbook carries the operator-facing half now: **create and keep
the Alpaca account a cash account** (the paper account is one by default), and treat any
offer to "upgrade" to margin as a posture violation to decline, not a capability to use.

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

## The TUI console

`keel tui` (or any wrapper, e.g. `./keel-live tui`) opens the **operator console**: the dashboard is
still the landing screen, and `m` opens a menu tree over it — Profile, Trading, Rules, Compliance,
Data, Research, Account, Help — covering every operational read and write the CLI knows (the
setup-only writes are deliberately absent: `rules seed` is bootstrap, and schema migration rides
along every database open rather than being a menu action). The console
is **thin by construction**: each entry dispatches to the same `keel/commands/*` service layer the
CLI commands call, and an architectural test (`tests/commands/test_console_thinness.py`) pins that
the TUI layer contains no business logic — no sizing, screening, gating or reporting math, no
`Decimal` arithmetic beyond display, and no broker construction outside the service seams. If a
feature is missing, the fix lands in the service layer and both front-ends get it.

**Profile switching and the live guard.** The Profile menu lists every deployment as its config+db
**pair** — the same pairs the table above pins — and switching rebinds both halves everywhere, in
one action: every screen, banner and read answers about the new deployment on the next paint.
Selecting **LIVE** asks an explicit y/N at the terminal first; declining keeps the binding exactly
where it was, and no key path can rebind around that confirm (the one guarded entry point is
pinned by test). The switch rebinds the **console only** — a `keel agent` process keeps the pair
its own command line gave it, so pointing the console at live never changes what a running agent
trades. Binding a deployment directly through the CLI's `--config`/`--db` flags remains the
wrappers' documented path.

**The session banner.** Every screen's header names the active deployment (LIVE styled
unmistakably) and the market session state with the venue clock — OPEN/CLOSED with the recorded
next open/close, `24/7` for always-open venues, and **CLOCK UNAVAILABLE** rendered fail-loud when
the recorded clock is absent or stale, exactly as `fetch --check` treats it. The banner reads the
recorded session state; there is no TUI-side calendar.

**The typed contracts: seven of the CLI's own, two the console adds.** Seven actions run the
CLI's own typed prompt in-console, word for word (curses suspends around it so the prompt
renders at the terminal): `resume`, `resume-entries`, `record-flow`, `reset-hwm`,
`withdrawals attest --enabled`, `autonomy on`, and `update` (the self-update run — the same
gate `keel update` demands; see "Self-update" under "Deploying a new version") — each the same
`_require_interactive_confirmation` gate the CLI command runs, demanding a typed `yes` and
failing closed off a TTY. Two more typed prompts are **ceremony the console adds on top of
an ungated CLI action** — deliberately *stricter* than the CLI, not identical to it: asset
`attest` makes you type the **asset code** back (the CLI's `keel assets attest` is not
gated — an attestation only ever admits to a list rail 1 still enforces per-trade), and the
retry flow's `rules promote --force` demands a typed `yes` quoting the CLI's own force
warning (the CLI's `--force` is a bare flag). Both are built on the same shared
typed-confirmation gate as the CLI's six. Every typed prompt **cannot be pre-filled, piped
or bypassed**; a wrong phrase or a decline writes nothing. `kill` is the deliberate
exception: **one key, no confirmation**, its own CLI contract — engaging the halt is the
safe direction — and the console adds no ceremony to it. The whole ceremony map (every
state-mutating console action → typed-phrase / confirm-step / ARMED+Enter /
ungated-by-design, each with its refusal proof) is pinned as a table-driven suite,
`tests/commands/test_console_ceremony.py`, so a newly added mutating action without a
classified ceremony row fails the tests.

**ARMED and blocking surfaces.** The runs that do real work — one agent cycle, one monitor poll,
fetch and its check/repair variants, one simulate — open **ARMED**: nothing runs until Enter,
which is the confirm step. While a run executes the screen freezes (it can take minutes, exactly
like the CLI) and the result is held on screen afterwards. **Ctrl-C exits the console
gracefully, discards held results, and the in-flight run does not complete** — as every frozen
screen states; the interrupt propagates out of the run itself (the loop's failure handlers
catch `Exception` only), which is what restores the terminal cleanly. The one entry that can
place orders, the agent cycle, goes through `agent.run_once` with the CLI's own
order-confirmation gate — there is no TUI-originated order path.

**Venues and help.** The Profile menu's **Venues** entry browses every installed adapter and its
declared capabilities — the same payload `keel brokers list` prints, one service, both
front-ends — with the selected adapter highlighted; no key presence is read or implied, and no
secret is ever shown. `?` on any screen opens that screen's own "what am I looking at" help, and
the Help menu holds the glossary (one source, `docs/glossary.md`, the fiqh terms anchored to
`docs/fiqh-basis.md`), every screen's rows consolidated, and the per-rule-parameter help rendered
from the rule classes themselves.

**Safety design notes.** Re-entering any sub-menu resets its cursor to the top — a remembered
row is a loaded one (leave Trading with the cursor on kill and a replayed Enter would engage the
halt with no ceremony). The Account menu's pnl (the FIFO report over imported transactions, with
an honest empty state until `keel db import` has loaded any) and versions (the deploy check)
entries are read-only views; the branch's **one** write path is the update entry (issue #415) —
an ARMED view whose run demands the CLI's own typed gate, and which on a verified success
relaunches the console on the new build ("Self-update" under "Deploying a new version" is its
procedure). The console runs no loop of its own and schedules
nothing: it is a front-end over the same services, and closing it never stops a deployment's own
scheduled cycles.

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
