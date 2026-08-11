# keel

An offline-first, halal (long-only, no-leverage) auto-trading agent for Coinbase. See
`docs/superpowers/specs/2026-07-15-keel-autotrade-design.md` for the full design and
`docs/superpowers/plans/2026-07-15-keel-phase1-offline-foundation.md` for the Phase 1 build plan.

## How keel works

keel runs as a scheduled **agent loop** (`keel agent`). Each cycle, for every allowlisted
product, it:

1. **Polls** fresh candles from Coinbase (public market data).
2. Asks each **`live` rule** to `detect()` a setup on that product's candles.
3. Sends any resulting signal through the **rails** — un-overridable safety checks in
   `keel/execution/guards.py`.
4. **Previews** the order with the broker (the broker's own numbers, not an estimate).
5. Applies the **confirm / autonomy gate** (below).
6. Calls `place_order`, and **logs** before and after.

There is deliberately **no manual "place an order" command** — every order is the output of a
rule that cleared the rails. keel never takes a discretionary trade: its judgement is
deterministic, backtested rules plus the rails, never a prediction.

### Rules

Orders come from four rule kinds (`keel/agent.py::RULE_REGISTRY`). A rule must be walked
`candidate → paper → live` before it can trade (`keel rules list|promote|demote|disable`):

- **`dca`** — scheduled dip-buy accumulation: a fixed-budget market buy on a **calendar cadence**
  (e.g. weekly), no stop. The only rule that fires on a schedule you control — which is why it's
  the natural vehicle for a first live-order test (see `docs/go-live-runbook.md`).
- **`turtle_breakout`**, **`pullback_continuation`**, **`rsi_meanrev`** — risk-defined entries
  (with a stop and target) that fire only on a **real market setup** — a breakout, a pullback, an
  RSI extreme — so their timing isn't something you can summon on demand.

### The rails (un-overridable)

Every order — including in autonomous mode — must clear the guards in
`keel/execution/guards.py`: the **halal allowlist**, per-order and per-day spend caps, a
total-exposure cap and per-asset concentration cap, correlation-aware sizing, a minimum-move
floor, **no-martingale / no-stop-widening**, the **kill-switch** (fails closed — an unreadable
state halts trading), **total & weekly drawdown breakers**, a consecutive-loss / edge-decay
breaker, feed-staleness and quote-balance checks, and venue **subscription / withdrawal
attestations**. A rail veto names itself and the command that clears it. Nothing overrides a
rail — not even autonomy.

### Confirm vs. autonomy

- **confirm** (default): keel previews each order and asks `Place this order? [y/N]` at a
  terminal. Run headless, it declines — nothing is placed without a human `y`.
- **autonomy on**: keel places without asking. It changes **who is asked, never what is
  allowed** — every rail still runs first, and autonomy can never clear a safety halt. Prefer a
  time-boxed session: `keel autonomy on --for-hours N`. To stop trading immediately, use
  `keel kill`, not `keel autonomy off`.

### How much money moves

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
  at any one moment (rail 4, and rail 6's concentration cap is a percentage of it), *and* it is
  the **equity proxy that sizes orders** on the live path
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

### Halal by construction, and ships inert

Long-only spot only — no leverage, shorting, or derivatives; sizing uses actual cash, so no
riba. keel ships **inert**: nothing trades until you promote a rule to `live`, attest the venue
subscription (rail 14 refuses live BUYs otherwise), fund the account, and — in confirm mode —
type `y`. See `docs/go-live-runbook.md` for the first supervised order and
`docs/operator-runbook.md` for the account-level obligations no rail can enforce.

## Development

```bash
uv sync                 # install deps (Python 3.14)
uv run pytest           # run tests
uv run ruff check       # lint
```

Copy `.env.example` to `.env` and fill in your Coinbase Developer Platform (CDP) API key/secret
for any live (network) commands. `.env` is git-ignored and never committed. Offline commands
(config loading, analysis, backtests on imported CSVs) work without it.

Runtime settings (allowlist, target weights, risk caps, market data granularities, etc.) live in
`config.yaml` at the repo root — see `keel/config.py` for the schema and validation rules.

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
place. Autonomy changes who is asked, never what is allowed (see **Confirm vs. autonomy** above);
check the flag before assuming a live cycle is supervised, rather than inferring it from `confirm`.

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
`_build_intent` as `equity_override`; the live path has no equity reading there and falls back to
`caps.max_exposure_usd` as a proxy (`keel/execution/executor.py`). The same rule, the same setup and
the same day therefore produce different quantities on the two accounts, and neither is an estimate
of the other. The settings behind those numbers are covered under **How much money moves** above.

## Before trading live

Read `docs/operator-runbook.md`. It lists the compliance obligations **no rail can enforce** — chiefly
that **interest/rewards on idle balances must be disabled** (Coinbase pays USDC rewards on idle balances, so riba can accrue with no order placed). Every guard in `keel/execution/guards.py`
inspects an order, so account-level obligations are invisible to all of them and are yours to verify.

Note keel ships **inert**: rail 14 refuses live BUYs until a subscription is attested with
`keel subscription attest --venue coinbase --tier <tier>`. That one is enforced in code and needs no
checklist.
