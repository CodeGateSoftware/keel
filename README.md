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

## Before trading live

Read `docs/operator-runbook.md`. It lists the compliance obligations **no rail can enforce** — chiefly
that **interest/rewards on idle balances must be disabled** (Coinbase pays USDC rewards on idle balances, so riba can accrue with no order placed). Every guard in `keel/execution/guards.py`
inspects an order, so account-level obligations are invisible to all of them and are yours to verify.

Note keel ships **inert**: rail 14 refuses live BUYs until a subscription is attested with
`keel subscription attest --venue coinbase --tier <tier>`. That one is enforced in code and needs no
checklist.
