# Paper trading — it never existed, and it still cannot start

**Date:** 2026-07-20
**Status:** wired and tested. **Cannot run for the Turtle today** — see the bootstrap finding.

## The correction

I told the user several times today that paper trading was "ready to start — rules seeded,
`mode: paper` in config, `track_record` wired." **That was wrong on inspection:**

- **`PaperTrader` had no production caller.** `grep` found it referenced only by its own tests.
- **`config.auto_trade.mode: paper` silently degraded to `"confirm"`** in
  `agent._confirm_or_bypass`, which with no `confirm_fn` places nothing and records nothing.

So setting `mode: paper` produced a loop that polled, evaluated, and did nothing — **while looking
like it was paper trading.** The pieces existed and were individually tested; nothing connected
them. Memory recorded Phase 2 as "rule→engine→paper(DB)→track_record→promotion gate composes",
which is true — it *composes*, verified in a scratchpad. I read "composes" as "wired."

## What was built

- `guards.check(..., offline=True)` skips **only** `LIVE_STATE_RAILS` (rail 13 USDC-funding, rail
  17 withdrawal capability) — the two whose inputs describe the real account — and **returns them
  in `GuardResult.skipped_rails`**. Never silently omitted: a paper track record must be honest
  about which checks it could not make, because the promotion gate is scored on it.
- `agent.run_once` routes ENTER signals through `_paper_enter` when `mode == "paper"`: build
  intent → offline rails → `PaperTrader.on_signal`. No broker order, no account read.
- `_paper_resolve_bars` walks the newest bar through `PaperTrader.on_candle` so stops and targets
  fire. Without it, positions would only ever close on an explicit rule exit and the track record
  would be all winners-that-never-stopped-out.
- **`PaperTrader` now rehydrates open positions from the orders table.** Open state used to live
  only in the object, so a per-cycle agent would forget every position, never exit them, and
  re-enter on the next signal. Pairing is exact — every exit payload carries its
  `entry_order_id`.
- **Paper mode loads `paper`-status rules, not `live`.** The lifecycle is candidate → paper →
  live; loading `live` rules would rehearse what is already trading and never advance a candidate.

Paper's real contract, pinned by a test: it **may read market data** (polling needs the venue, the
same read-only access `keel fetch` uses) but **must never place an order or read account state**.
An earlier version of that test asserted "never touches the broker" and was simply wrong.

## ⚠️ The bootstrap finding — paper still cannot start

Wiring was not the only thing missing.

**Our three Turtle rules are `candidate`.** To reach `paper` status they must clear
`can_promote`, whose floor is **`min_trades = 100`**. The Turtle's backtest has **31 trades**.

⇒ **The rule cannot enter paper trading, because entering paper requires backtest evidence the
rule does not have.** A rule that trades ~6×/year cannot accumulate 100 backtest trades on 5 years
of a 3-asset allowlist — which is the same wall as everything else measured today.

**This is the gate working, not a bug.** The proving gate deliberately refuses to spend months of
forward testing on a rule with no demonstrated backtest edge. And the floor is not arbitrary:
MinBTL independently puts the evidence requirement at ~125 trades.

⛔ **Do not "fix" this by lowering `min_trades`.** That floor was already lowered to 30 once and
restored to 100 on evidence; memory records the reversal explicitly. The honest routes are more
assets or more history — and this is now the *third* independent finding today pointing at
allowlist expansion.

## Also found

**`run_once` has no as-of capability.** It reads the whole candle table, so replaying the live
loop over history re-evaluates the same final bar every cycle. My first replay ran 900 cycles and
produced 0 orders for exactly that reason — a flaw in the harness, not the code. It does not
affect production, where time moves forward, but it means the live loop cannot be backtested
without a per-cut database. The simulator (`keel simulate`) is the supported path for that.

**Paper no longer calls the broker for equity.** It previously did, failed, and logged an ERROR
every cycle — so paper looked broker-free only by accident. Rail 11's scalars now simply do not
advance in paper, stated explicitly.

**`_fetch_available_quote(None, ...)` no longer logs an exception.** Paper passes no broker; that
is expected, and an ERROR per paper entry would have filled the operator's log with noise about a
handled condition.

## What would actually start the clock

1. Expand the allowlist so the Turtle's backtest can reach ~100–125 trades, **or**
2. Consciously promote a rule to `paper` status by hand, accepting that it did so without clearing
   the backtest floor — a decision, and one that should be recorded in the trials ledger as such.

Option 2 is defensible for a *rehearsal* whose purpose is to test the machinery rather than the
edge. It should not be confused with the rule having earned forward evidence.
