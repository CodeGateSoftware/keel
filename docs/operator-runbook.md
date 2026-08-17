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
live DCA signal because the attestation had lapsed, and both deployments' attestations were weeks
stale as of 2026-08-17, so rail 17 was halting entries everywhere. Each deployment carries its own
attestation (they do not share a database), so both must be refreshed.

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

## Paper vs. live

A deployment such as `~/keel` runs **two of them side by side**, and they share nothing: separate
configs, separate databases, separate allowlists, separate caps, separate schedules, separate
histories. **A figure from one says nothing about the other.** Checking a paper position size
against live account equity — or a live cap against paper cash — yields a confident wrong answer,
and has already produced one. Establish which account a number came from before reasoning about it.

| | paper | live | paper-hourly |
| --- | --- | --- | --- |
| config | `config.paperforward.yaml` | `config.live-sandbox.yaml` | `config.paper-hourly.yaml` |
| database | `keel.db` (the `--db` default) | `keel-live.db` (must be passed) | `keel-paperhourly.db` (must be passed) |
| `auto_trade.mode` | `paper` | `confirm` | `paper` |
| allowlist | BTC, ETH, PAXG, SOL, XLM, LTC, ADA, LINK (8) | BTC, ETH, PAXG, ADA, XLM (5) | same 8 as paper |
| `caps.max_exposure_usd` | 5000 | 200 | 5000 |
| money spent | synthetic `paper_cash_usdc` | the real broker balance | synthetic `paper_cash_usdc` |
| sizing basis | the paper account's own equity | `caps.max_exposure_usd`, as a proxy | the hourly account's own equity |
| rail 14 allowance | $500/month (Basic tier) | $200/month | $500/month |
| `equity_state_mode` | `paper` | `live` | `paper` |
| launchd job | `com.keel.paperforward` | `com.keel.live` | `com.keel.paper-hourly` |
| cadence | daily (day-stamp) | daily, UTC (UTC day-stamp) | **hourly**, UTC (UTC hour-stamp) |
| rules traded | daily turtle, `paper` | daily turtle + DCA, `live` | **hourly** turtle, `paper` |

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
exception that runs once per UTC *hour* — see "The hourly evidence profile" below). Each
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
for p in BTC ETH PAXG SOL XLM LTC ADA LINK; do
  keel --config config.paper-hourly.yaml --db keel-paperhourly.db rules add \
    --kind turtle_breakout --product "${p}-USD" --params '{"granularity": "ONE_HOUR"}'
done
# Advance each printed id candidate -> paper. --force is the documented bypass for a rule
# whose backtest can never clear the gate; for hourly turtle the backtest clears min_trades
# easily and fails on EDGE (the net-negative finding above), so force is deliberate here and
# the warning it prints is the caveat restated:
keel --config config.paper-hourly.yaml --db keel-paperhourly.db rules promote --force <id>
# Warm the candle cache (ONE_HOUR/ONE_DAY/FIFTEEN_MINUTE x 365d) before the first cycle:
keel --config config.paper-hourly.yaml --db keel-paperhourly.db fetch
```

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
