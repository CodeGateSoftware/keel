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
gh release download v0.3.1 --repo CodeGateSoftware/keel --pattern '*.whl' --dir Release/
uv pip install --python .venv --find-links Release Release/keel_trader-0.3.1-py3-none-any.whl
.venv/bin/keel --version
.venv/bin/keel status
```

Substitute the version being deployed in both of the first two lines. `--find-links Release` is
what lets the single `keel_trader` wheel resolve its `keel-core` / `keel-broker-*` siblings from
that same directory — which is why step 1 downloads them all. Installing **by path** rather than
by bare name is deliberate: `keel` on PyPI is an unrelated project, so `pip install keel` fetches a
stranger's code (see `keel/version.py`).

Step 3 is the check that matters. It must report the version you just installed, bound to a
commit, from source `[release]`:

```
keel 0.3.1+deb8fa7e978d [release]
```

A build reporting `(DIRTY)` or `[checkout]` corresponds to no commit and **must not be run against
live funds**. Step 4 is a read-only snapshot — no orders, no writes — confirming the new build
opens the database and reaches the venue.

If the deployment runs on a schedule (LaunchAgents, cron), a new build takes effect on the next
cycle with nothing to restart — each cycle is a fresh process. A **long-running** process is the
exception: a `keel tui` left open keeps the build it started with until you quit and relaunch it.

## Before trading live

Read `docs/operator-runbook.md`. It lists the compliance obligations **no rail can enforce** — chiefly
that **interest/rewards on idle balances must be disabled** (Coinbase pays USDC rewards on idle balances, so riba can accrue with no order placed). Every guard in `keel/execution/guards.py`
inspects an order, so account-level obligations are invisible to all of them and are yours to verify.

Note keel ships **inert**: rail 14 refuses live BUYs until a subscription is attested with
`keel subscription attest --venue coinbase --tier <tier>`. That one is enforced in code and needs no
checklist.
