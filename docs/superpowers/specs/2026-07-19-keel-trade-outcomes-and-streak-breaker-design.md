# keel — Closed-Trade Outcomes, the Consecutive-Loss Breaker, and Waking Rail 11

**Status:** design, approved in brainstorming 2026-07-19.
**KB grounding:** §57.1 (consecutive-loss breaker), §57.2, §23.1/§25.5/§35.2 (per-class floors),
§12.6 (DCA exemptions), §10.3/§20.4 (account-drawdown breaker).

## 1. Why this exists

KB §57.1 proposed a consecutive-loss circuit breaker as candidate **rail 16**. Building it surfaced a
larger problem.

**There is no closed-trade outcome anywhere in keel.** On exit, `agent.py:235` does
`repo.set_state(f"position_rule:{product_id}", None)` — it clears ownership and records no P&L. The
`orders` table is an order-lifecycle log (`qty`, `actual_fill`, `fee`, `status`), not a trade-outcome
log. `analysis/pnl.py:realized_pnl` aggregates *imported transactions* — a reporting path, not a live
per-trade signal.

**Consequence, and the real finding: rail 11 is dormant in production.** It reads
`drawdown_total_pct` / `drawdown_weekly_pct` from `agent_state` with `default=Decimal("0")`, and
**nothing writes those keys** — only tests do. The account-drawdown circuit breaker therefore *cannot
trip*. It reads as enforced in `guards.py` and in the design docs, and it is not.

That is the defect this work exists to fix. A streak breaker built on the same pattern would be a
second rail that looks like protection and provides none.

**Scope:** build the missing substrate (closed-trade outcomes + marked-to-market equity), then wire
**both** rails to it.

## 2. Definitions (approved)

**A trade** = the span from the opening BUY to the position being **fully closed** (quantity reaches
zero). `executor.scale_out` means positions can close in pieces; a half-off-at-target that later stops
out at breakeven is **one** trade, not two. Its P&L is the sum across all partial exits.

> A consequence to accept deliberately: a trade's outcome is unknown until the last unit closes, so a
> half-closed position contributes nothing to the streak yet. Correct, mildly counterintuitive.

**A loss** = realized P&L **net of fees** < 0. Gross would be wrong here specifically: rail 7 exists
because fees dominate small moves, so a trade that is +0.1% gross and −0.2% after fees is a loss and
must count as one. `orders.fee` is already recorded.

**DCA is exempt from the streak count.** Rails 8 and 11 already exempt it for the same reason: DCA is
*designed* to buy through drawdowns on a fixed budget (§12.6). Counting DCA fills as losses would trip
the breaker during exactly the accumulation it exists to perform. The streak counts **rule-driven
trades only**. DCA trades still produce outcome rows (they are real P&L for drawdown purposes) — they
are excluded from the *streak*, not from the *record*.

## 3. Architecture — producer / consumer split

The existing precomputed-scalar pattern is kept and finally honoured:

```
agent loop (has prices)                    guards.check (pure, stateless)
──────────────────────                     ─────────────────────────────
on trade close  → append trade_outcomes    rail 16 reads streak_halt_until ONLY
                → update streak counters
every cycle     → mark to market           rail 11 reads drawdown_total_pct / drawdown_weekly_pct
                → update equity HWM
                → write drawdown keys
```

**Guards stay pure and stateless.** No rail gains an I/O dependency, a price feed, or a clock beyond
`now_ts`. Every rail continues to read scalars and decide. This is why rail 11 was designed to read
precomputed values in the first place — the architecture anticipated a producer that was never built.

**The agent owns computation** because it is the only component with current prices (it polls candles
each cycle). Mark-to-market cannot live in `guards.check()`, whose signature is
`(intent, repo, config, now_ts)`.

## 4. Component: the trade-outcome record

New table, `SCHEMA_VERSION` 2 → 3, using the versioned-migration machinery added on 2026-07-19.

```sql
CREATE TABLE IF NOT EXISTS trade_outcomes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    TEXT NOT NULL,
    rule_name     TEXT,             -- NULL for DCA / unattributed
    is_dca        INTEGER NOT NULL, -- streak exclusion flag, see §2
    opened_at     INTEGER NOT NULL,
    closed_at     INTEGER NOT NULL,
    qty           TEXT NOT NULL,
    entry_fill    TEXT NOT NULL,
    exit_fill     TEXT NOT NULL,
    fees          TEXT NOT NULL,
    pnl_net       TEXT NOT NULL     -- realized, net of fees; sign decides win/loss
);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_closed_at ON trade_outcomes(closed_at);
```

Money is exact `TEXT` (`str(Decimal)`), matching `candles`/`orders`/`broker_subscriptions`. Never
`float`.

**Migration adds the table only — no backfill.** Historical `orders` rows cannot be reliably paired
into round-trips (partial fills, scale-outs, positions opened before `position_rule` tracking existed).
A guessed backfill would seed both rails with fabricated history, and rail 16's threshold is derived
from streak statistics — poisoning it would be worse than starting empty. Both rails simply have no
history until trades close under the new code, which is the honest state.

**Repository surface:** `insert_trade_outcome(...)`, `get_trade_outcomes(since_ts=None)`.

**Open gap the implementation must close: where the ENTRY context comes from.** Writing an outcome row
needs `opened_at`, `entry_fill` and `qty`, but the only thing tracked across a position's life today is
`position_rule:<product_id>`, which holds the **rule name and nothing else** (`agent.py:404` sets it,
`:235` clears it). Two options, and the plan must pick one explicitly rather than discovering this
mid-implementation:

- **(i) Extend the tracked state** — store a small dict (`rule_name`, `opened_at`, `entry_fill`, `qty`)
  under `position_rule:<product_id>` instead of a bare string. Self-contained and cheap, but it changes
  the shape of an existing key, so every reader and its tests must move together.
- **(ii) Derive the entry from the `orders` log** at close time — pair the closing SELL back to its
  opening BUY(s) for that product. No state-shape change, and rails 3 and 8 already read `orders` this
  way, but it re-introduces exactly the round-trip pairing complexity (partial fills, scale-outs) that
  choosing the outcome-record approach was meant to avoid.

Recommendation: **(i)**. The pairing logic in (ii) is the thing this design set out to eliminate, and
doing it at close time would put it on the live-money path rather than in a producer that can be tested
in isolation.

## 5. Component: rail 16 — consecutive-loss breaker

Reads **one** `agent_state` scalar — `streak_halt_until` — and writes nothing. The counter
(`consecutive_losses`) is the producer's private state; the rail must not read it, or the "is the
threshold reached" decision would live in two places and could disagree. The producer decides; the rail
enforces.

```python
# 16. Consecutive-loss circuit breaker — a SEQUENCE breaker where rail 11 is a MAGNITUDE
#     breaker. Detects that the edge may have stopped working BEFORE the drawdown
#     accumulates. ENTRIES ONLY; DCA exempt (§12.6, §57.1).
if is_buy and not intent.is_dca:
    halt_until = repo.get_state("streak_halt_until", default=0)
    if now_ts < halt_until:
        violations.append(
            f"consecutive_loss_breaker: {config.money_mgmt.max_consecutive_losses} consecutive "
            f"losing trades tripped the breaker; new entries are halted until {halt_until} "
            f"({halt_until - now_ts}s remaining). Exits, stop-outs and DCA are unaffected. "
            f"Clear early with `keel resume-entries`."
        )
```

The message states the cause, the remaining duration, what is *not* blocked, and the override — per the
rail-14 lesson that a bare "0 exceeds cap 0" is arithmetically true and operationally useless.

**Entries only — non-negotiable.** The rail is gated on `is_buy`. A breaker that blocked exits would
trap capital in a losing position, inverting its own purpose. Stop-outs, exits and DCA stay live.

**Two mechanics, deliberately separate:**
- the **streak counter** resets to zero on any winning trade — normal operation, no halt;
- the **halt** is set when the counter reaches the threshold, and clears when the cool-off expires.

**Reset = time-based cool-off.** The two alternatives from §57.1 were rejected with cause:
- *"until the next winning trade"* **deadlocks** — halting entries drains open positions, so no new
  trades occur, so no win can clear the halt. Self-clearing in name only, permanent in practice.
- *"until an operator resumes"* **cannot be backtested** — `keel simulate` cannot model a human, so a
  sweep could never evaluate it, and §57.1 requires harness validation before this is trusted. It is
  also near-redundant with the existing kill-switch.

A cool-off is the only option that is both self-clearing and sweepable.

**Operator override:** an operator may clear a halt early (same relationship `resume` has to the
kill-switch). An override, not *the* reset mechanism.

**Ships disabled by default.** `max_consecutive_losses = 0` means the rail is off.

> `turtle_breakout`'s own sim shows a **max losing streak of 5**. The source's suggested threshold of 3
> would have fired repeatedly on a strategy that was working. The threshold must sit **above** the
> strategy's tested max streak or it fires on normal variance and stands the system down during exactly
> the runs it was designed to survive. `stats.max_losing_streak` (`strategy/stats.py:41`) is currently
> a statistic with no consumer; this rail is its consumer, and it is how the threshold gets sized.

Config, `MoneyMgmtConfig`:

```python
max_consecutive_losses: int = 0      # 0 = disabled
streak_cooloff_days: int = 0         # cool-off after the halt trips
```

## 6. Component: waking rail 11

Rail 11's code is unchanged. The producer supplies its inputs.

- **Equity** = quote balance + **mark-to-market** value of open positions.
- **Total drawdown** = from the **high-water mark**, not initial capital — matching `stats.py` and the
  sim. Drawdown-from-deposit would read 0% forever on an account in profit.
- **Weekly drawdown** = **rolling 7 days**, not calendar week. A calendar reset clears the breaker every
  Monday regardless of conditions.
- **Unrealized is included.** A drawdown breaker seeing only realized P&L sits at 0% while a position
  bleeds and notices only after the loss is booked — backwards for a circuit breaker, which must fire
  *while* you are losing. This is what forces mark-to-market, and therefore the agent-side producer.

The HWM persists in `agent_state` (`equity_high_water_mark`).

## 7. Testing

**The rails.** Unit tests against `guards.check` in the established style. Each new rail test must be
proven to **fail against the unbuilt rail** — this design exists because two mechanisms shipped that
could not fire, so "the test passes" is not evidence here. Specifically: a test asserting a BUY is
vetoed must be shown to pass only because the rail vetoed it, not because another rail did
(`_keys(result)` exact-match, per the file's convention).

**The producer.** Round-trip tests over `trade_outcomes`: scale-out closes as one trade; a
fees-flip-the-sign trade counts as a loss; DCA produces a row but does not move the streak; a win
resets the counter; the halt expires on schedule.

**Rail 11 regression.** A test that fails if the drawdown keys are unwritten — i.e. one that would have
caught the current dormancy. This is the highest-value test in the change.

**Simulator parity.** `sim/account.py` mirrors the rails for backtests and is held in parity by
`tests/sim/test_account.py`. Rail 16 must be added there too, or `keel simulate` will report entries
the live engine would refuse — the divergence class §57.3 names. The sweep needs it anyway to evaluate
thresholds.

**Baseline.** `tests/fixtures/baseline_backtest.json` must stay byte-identical: backtesting does not run
the rails, so any movement is a bug.

## 8. Out of scope

- Backfilling historical outcomes (§4).
- Retuning `turtle_breakout` or acting on the sim's losing buckets — the breaker is a generic
  mitigation, not a fix for those.
- Any change to rail 11's thresholds; only its inputs are supplied.
- Promoting the breaker to enabled-by-default. That requires a sweep and is a separate decision.

## 9. Done criteria

- `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` pass; baseline fixture byte-unchanged.
- A closing trade appends exactly one `trade_outcomes` row with `pnl_net` net of fees.
- A scale-out sequence produces **one** row, not one per partial exit.
- `drawdown_total_pct` / `drawdown_weekly_pct` are written every cycle; a test fails if they are not.
- Rail 16 vetoes BUY entries while halted and never vetoes a SELL, an exit, or a DCA buy.
- With `max_consecutive_losses = 0` (default) the rail is inert and no existing test changes behaviour.
- `sim/account.py` parity holds with rail 16 present.
