# Paper-mode fidelity: synthetic account, Rail 11 enforcement, and position sizing

**Date:** 2026-07-23
**Status:** Design approved; spec under review
**Scope:** single PR, ~300–500 LOC incl. tests, no `guards.py` changes, no schema changes
**Related:** `keel/agent.py`, `keel/strategy/paper.py`, `keel/execution/equity.py`, `keel/execution/guards.py`, `keel/sim/{account,portfolio_sim}.py`, `config.yaml`/`keel/config.py`

## 1. Context & problem

The paper-trading path exists to produce an honest out-of-sample (OOS) track record that the
promotion gate consumes — the mandatory "paper-proving" step before any rule trades live capital.
Two defects make today's paper path an unfaithful rehearsal:

1. **Rail 11 (the account-drawdown circuit breaker) is inert in paper.** Rail 11 halts new BUYs when
   `drawdown_total_pct >= max_total_dd_pct` (0.20) or `drawdown_weekly_pct >= max_weekly_dd_pct`
   (0.08) — thresholds in `config.yaml` (`money_mgmt`), enforced at `keel/execution/guards.py:384-397`.
   In paper mode the agent loop hard-sets `equity_now = None` whenever a paper trader is active
   (`keel/agent.py:699-715`), so `equity.update_drawdown()` is never called and Rail 11's scalars stay
   frozen at 0. Paper *does* run the rails — `_paper_enter` calls `guards.check(..., offline=True)`,
   and `offline=True` skips only rails 13 & 17 (`LIVE_STATE_RAILS`, `guards.py:139`), **not** Rail 11 —
   so the breaker is present but reads 0 forever. A catastrophic multi-asset drawdown could run the
   paper strategy into the ground with no halt, polluting the OOS track record.

2. **The paper path mis-sizes its fills.** `_paper_enter` builds a properly risk-*sized* intent (via
   the executor's sizing) and the rails veto against that sized notional — but `PaperTrader` then
   discards the size and fills a **fixed 1 unit** (`_QTY = Decimal(1)`, `keel/strategy/paper.py:38`,
   documented "position sizing out of scope"). One paper BTC trade is ~$100k notional; one ADA trade
   is under a dollar. There is no cash balance and no starting-equity concept anywhere in the paper
   track, so **drawdown-as-a-percentage of the paper account is undefined today.** The rails are
   judging an order the rehearsal does not actually take.

These are coupled: Rail 11 needs an equity denominator, which needs realistic sizing, which needs a
per-position quantity. They must be fixed together.

## 2. Goals & non-goals

**Goals**
- Rail 11 (total + weekly) enforced in paper mode against a coherent, faithful equity denominator.
- Paper fills sized realistically off the paper account's own mark-to-market equity.
- A paper drawdown halt behaves exactly like live (veto new buys; open positions ride to their stops).
- The paper→live transition cannot manufacture a phantom drawdown.
- `guards.py` and the DB schema are untouched (keeps this out of safety-review-heavy territory).

**Non-goals (explicit follow-ups)**
- Fixing the **live executor's** sizing, which currently sizes off `caps.max_exposure_usd` (a $5k
  placeholder proxy) rather than real equity. That is a safety-sensitive Phase-4 money-management
  change; this PR deliberately does not touch it. Consequence accepted: **paper ≠ live sizing** until
  that follow-up lands (paper sizes off real equity per §4.1; live still uses the $5k proxy).
- Unifying `SimAccount` and the paper account onto one shared account abstraction (a larger refactor;
  not forced by this work).

## 3. Design decisions (settled during brainstorming)

**D1 — Paper sizes off its account's mark-to-market equity, not the `$5k` proxy.** The sim
(`keel/sim/portfolio_sim.py:606-607`) sizes each trade off the account's actual mark-to-market
equity; the live executor sizes off `caps.max_exposure_usd`. Paper adopts the **sim mechanism**:
size off the synthetic account equity. Rationale: the paper-forward's job is to confirm the forward
edge (size-invariant metrics: PF, win rate, R-multiple) and rehearse live *operation*; sizing off a
coherent account equity gives Rail 11 an honest denominator and makes sizing and drawdown share one
base (so −20% ≈ ~20 max-risk losing trades, the breaker's intended biting point).

**D2 — The paper account is seeded from real equity, read once at start.** On the first paper cycle
with no paper-equity state (or after a mode flip, §4.4), read real account equity **once** via the
read-only broker key; fall back to a config `paper_starting_equity_usd` if the read fails. This is a
single startup read — not the per-cycle broker marking that was correctly ruled out — so the loop
stays broker-free thereafter. Rationale: the rehearsal answers "would Rail 11 have fired on *my*
capital," the truest operational test. Note: account **DD%** is only loosely comparable to the sim
(the sim is a DCA-from-$0 lifetime trajectory; a forward paper run is a lump-start trajectory) — the
rigorous cross-check is the **size-invariant edge metrics**, not the account DD%.

**Approach — Option C (reuse the live producer).** Paper computes one synthetic equity number per
cycle and feeds the **same** `equity.update_drawdown(...)` call live uses at `agent.py:715`. Rail 11
and `guards.py` are unchanged. This avoids a third divergent equity implementation alongside live
`_mark_to_market_equity` and `SimAccount`. `SimAccount` is precedent (its arithmetic is parity-tested
against `update_drawdown`) but **not** an import target: wiring it into paper would double-count the
fees/slippage `PaperTrader` already books into recorded fills and duplicate cap rails `guards.check`
already enforces in paper.

## 4. Architecture

A synthetic paper account, persisted in `agent_state`/the paper order ledger, is marked to market
each paper cycle; its equity drives the existing drawdown producer, which arms the existing Rail 11.

### 4.1 The synthetic paper account (`keel/strategy/paper.py`)
State (same *model* as `SimAccount`, re-implemented lightly, no fee double-count):
- `cash_usdc`: seeded per D2 — the seed value is the existing live `_mark_to_market_equity(...)`
  computation run **once** against the real broker at paper-start (quote balances + held positions
  marked to market), falling back to `paper_starting_equity_usd` when that read returns `None`.
  Thereafter `cash_usdc` is debited on entry fills and credited on exit fills — using the fills
  `PaperTrader` already books net of fee/slippage (`paper.py` entry/exit fill economics). No second
  fee application.
- Open positions carry `qty` (added to `_OpenPaperPosition`; the order payload already carries `qty`).
- Cumulative contributions, applied via a `deposit()`-style rebase that raises both `cash_usdc` and the
  Rail 11 HWM by the same amount (mirrors `SimAccount.deposit`, `account.py:203-233`), so an inflow is
  never read as recovery/drawdown.

New method `PaperTrader.equity(marks) -> Decimal`:
`cash_usdc + Σ qty·mark` over open positions, with the **cost-basis fallback** for a held product with
no fresh price (mirrors live `_mark_to_market_equity`, `agent.py:336-338` — dropping a stale-priced
holding would understate equity and trip Rail 11 on a data gap). Returns the coherent account equity.

**Sizing:** the paper enter path computes `qty = sizing.size(paper_equity, config.risk_pct,
setup.entry, setup.stop)` (as `portfolio_sim.py:607`), **not** the executor's `max_exposure_usd`-based
sizing. The intent's resulting notional feeds `guards.check` as today, so the exposure cap and other
rails judge the order the rehearsal actually takes.

### 4.2 Funding coherence (paper-path guard, not a rails change)
A BUY only fills when `cash_usdc >= notional`; otherwise it is not recorded (logged as a paper funding
skip). This keeps `cash_usdc` from going negative and the equity curve coherent, mirroring live rail
13's intent **without modifying `guards.py`** (rail 13 remains in the offline-skip list; this check
lives in the paper enter path). Rarely binds for a 1%-risk trend-follower but keeps the ledger honest.

### 4.3 Wiring Rail 11 (`keel/agent.py`)
- `_paper_enter` sizes via §4.1 and passes `intent.qty` to `PaperTrader.on_signal` (the qty is already
  in hand); `PaperTrader` records the fill at that qty (replacing `_QTY`).
- Replace the `equity_now = None if paper_trader is not None else _mark_to_market_equity(...)` branch
  (`agent.py:699-705`) with `equity_now = paper_trader.equity(marks)` in paper mode, using the
  per-product latest prices already assembled in the loop (`agent.py:687-692`). The existing
  `update_drawdown(...)` call at `agent.py:715` then runs unconditionally. Rail 11 (`guards.py:384-397`)
  reads the advanced scalars and vetoes buys at the configured thresholds. **`guards.py` unchanged.**

### 4.4 Mode-flip safety
Stamp `equity_state_mode` ("paper"/"live") in `agent_state`. At the top of each cycle, if the stamp
does not match the current mode, clear `equity_high_water_mark`, the equity history, and the
`drawdown_*` scalars before the first `update_drawdown` (the same clearing `keel reset-hwm` performs,
`cli.py:~1753`), then restamp. Prevents a synthetic paper HWM from meeting real equity on go-live and
manufacturing a permanent phantom drawdown (the failure `equity.record_external_flow`'s docstring
warns about). No runbook step; safe in both directions.

### 4.5 Legacy-order epoch cutoff
Set `paper_ledger_start_ts` in `agent_state` when this code first runs. Cash and equity-relevant
positions derive **only** from paper orders at/after that timestamp, so pre-existing fixed-1-unit
fills cannot poison the cash ledger. Older orders still feed `track_record` — its stats (win rate,
R-multiple, expectancy sign) are size-invariant, so the promotion gate is undisturbed.

### 4.6 Halt behavior & observability
- A paper Rail 11 breach **vetoes new buys**; open paper positions continue to resolve at their stops
  and targets via the existing `_paper_resolve_bars` — identical to live, where the breaker gates
  entries and existing positions ride to their stops. The pre-halt orders still yield the realized
  max drawdown for the verdict; the halt itself is promotion-relevant evidence.
- Surface paper equity and current total/weekly drawdown in the agent's INFO logging and in
  `keel status`, so a paper-forward is observable.

## 5. Configuration
- `paper_starting_equity_usd` (new): fallback seed when the one-time real-equity read fails (§4.1/D2).
- Monthly contribution during paper-forward: applied via §4.1's `deposit`-rebase **once per calendar
  month**, detected by a month rollover in the cycle timestamp (not a fixed 30-day wall-clock window),
  so the cadence is deterministic and matches how the sim funds. Amount is configurable; **default =
  the existing monthly allowance, and `0` disables contributions** (reasonable for a short paper-
  forward, since the account is already seeded from real equity per D2).

No new DB columns: the state fits existing `agent_state` keys and the order payload already carries
`qty`.

## 6. Testing
- **Live↔paper mark-to-market parity:** paper `equity()` and live `_mark_to_market_equity` agree for
  the same positions/prices (mirror the style of `tests/sim/test_account.py`'s guards-parity tests).
- **End-to-end DD halt:** a paper account drawn down to −20% produces `account_dd_breaker_total`
  vetoes on subsequent buys; weekly −8% likewise.
- **Mode-flip reset:** flipping `equity_state_mode` clears HWM/scalars before the next `update_drawdown`.
- **Legacy epoch cutoff:** orders before `paper_ledger_start_ts` are excluded from the cash ledger but
  still counted in `track_record`.
- **Funding check:** a BUY exceeding `cash_usdc` is skipped and logged, cash never goes negative.
- **Sizing:** paper fills at `sizing.size(paper_equity, risk_pct, entry, stop)` qty, not 1 unit.

## 7. Risks & containment
- **Paper vs live MTM divergence** → extract the pure `cash + Σ qty·mark` summation (with cost-basis
  fallback) into a shared helper in `keel/execution/equity.py` used by both `_mark_to_market_equity`
  and `PaperTrader.equity`; the parity test guards it.
- **Shared `agent_state` HWM across a mode flip** → §4.4 stamp + auto-clear.
- **Legacy 1-unit orders poisoning the ledger** → §4.5 epoch cutoff.
- **One-time seed read couples paper startup to the broker** → bounded to a single startup read that
  falls back to `paper_starting_equity_usd`; the loop is broker-free thereafter.

## 8. Effort & sequencing
Single PR, ~300–500 LOC incl. tests, no `guards.py` or schema changes, ~1–2 days. This fix is a
**prerequisite to a faithful paper-forward** of the 5-trend Turtle; the parallel `risk_pct` sim sweep
is independent and unaffected. Honest residual: paper marks at candle closes / once per cycle (same
granularity as live's own equity loop), so paper drawdown lags reality by up to one cycle and ignores
intra-cycle wicks — faithful to live's own equity cadence, not a defect.
