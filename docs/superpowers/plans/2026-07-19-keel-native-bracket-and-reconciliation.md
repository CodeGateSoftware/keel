# Native Exit Bracket and Order Reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make keel's exit path trustworthy end to end — the exchange owns the stop-vs-target race, every fill is observed and recorded, and every position is addressable per tranche rather than per product.

**Architecture:** Coinbase's native `trigger_bracket_gtc` places one order carrying both exit prices, so the OCO relationship is the exchange's problem rather than ours. A per-cycle reconciliation pass reads order status back and turns observed fills into `trade_outcomes` rows, which is what rails 11 and 16 consume. The remaining work replaces the per-product `position_rule:<product>` key — an ownership marker being asked to serve as a position ledger — with a real per-tranche record.

**Tech Stack:** Python 3.12, `coinbase-advanced-py` (`RESTClient`), SQLite via `keel/data/repository.py`, pytest. Money is `Decimal` end to end.

## ⚠️ Read this before treating it as a normal plan

**Part A of this plan is already built.** It landed on `feat/native-bracket-and-reconciliation` (4 commits, 980 passing) *before* this document existed — it grew out of a review pass rather than a plan, which is precisely the problem this document exists to correct. Writing retroactive "write the failing test / watch it fail" steps for code that already passes would be theatre.

So Part A is written as **design decisions with their rationale and accepted trade-offs**, to be *reviewed against*, not executed. Part B is the genuinely unbuilt work, in normal executable task form.

**A reviewer's job on Part A** is to check the implementation against the stated intent and to challenge the trade-offs — not to re-derive them.

## Global Constraints

- Money is `Decimal` everywhere. No `float` in any price, quantity, fee, or P&L path.
- Fail closed. A missing or unreadable input must refuse to trade, never permit. The one documented exception is `_upgrade_to_observed_economics`, which refines an already-recorded number and keeps its estimate on failure.
- Every guard rail stays un-overridable: `guards.check` runs on every order including brackets.
- **Every producer's WIRING must be held by a test that fails when the producer is unhooked**, not only by unit tests that call it directly. This project has shipped four dormant rails; that gap is how each one happened.
- Verification for every task: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/mypy .` — and `tests/fixtures/baseline_backtest.json` must stay byte-identical (`git diff --stat` on it produces no output).
- Use `.venv/bin/python`, never bare `python` (the package is not installed globally).

---

## Part A — Landed, for review (commits `0d418ff`, `da7e0ee`, `a20c0d4`, `5c14395`)

### A1. A cancel that cannot reach the exchange raises (`0d418ff`, hardened in `5c14395`)

`executor._cancel_at_exchange` raises `CancelUnavailable` on three failure modes: the broker exposes no `cancel_order`, the order has no broker-side id to name, or the call itself raises. `5c14395` added the fourth and most important: `cancel_order` returns `bool`, and anything other than `True` is a refusal.

**Why:** Coinbase's `batch_cancel` answers per order, so a 200 response does not mean the order is gone — it comes back `{"success": false, "failure_reason": ...}` for an order that already filled. Both cancel sites previously did `getattr(broker, "cancel_order", None)` and skipped silently when absent; the real client had no such method at all, so in production the cancel was *always* skipped while the row was still marked `canceled`. Our records claimed a cancel that never happened.

**Invariant to preserve:** the cancel must succeed at the exchange *before* anything records it. Never mark local state on a failed cancel.

### A2. One native bracket replaces two client-paired legs (`da7e0ee`)

`place_bracket` places a single `trigger_bracket_gtc` order: `limit_price` = take-profit, `stop_trigger_price` = stop. `handle_oco_fill` and the `oco_sibling:` state are deleted.

**Why:** the old design paired two SELL legs client-side, so correctness depended on *us* observing a fill and cancelling the survivor. A missed fill left a live order able to sell an already-closed position. It also sized both legs at the full qty, committing a 1× position 2×.

**Verified against the SDK, not from memory:** `RESTClient.trigger_bracket_order_gtc` builds exactly this payload, and a SELL bracket requires `stop_trigger_price < last < limit_price` — so a swapped mapping fails loudly at the exchange rather than silently.

**Accepted trade-off (a genuine regression):** `_roll_stop` must now cancel *before* placing, because the resting bracket commits the whole position and a replacement would be rejected for insufficient funds. That opens a brief window with no protective stop, which the old place-then-cancel ordering did not have. `edit_order` cannot avoid it — it accepts only limit-GTC orders and edits only size/price, never `stop_trigger_price`. A rejected replacement logs CRITICAL `executor.position_unprotected`.

### A3. Per-cycle order reconciliation (`a20c0d4`)

`keel/execution/reconcile.py`, called at the **top** of `run_once` — before equity, before entries, because a fill that already happened has changed both the position and the cash balance.

**Why:** nothing re-read order status, so a stop-out — the dominant source of losses — closed a position the agent never noticed. The row stayed `pending`, `_held_position` kept counting sold inventory as held, and rail 16 counted only *voluntary* rule exits, systematically under-counting the losing side it exists to react to.

**Also upgrades two numbers from modelled to observed:** `actual_fill` was the expected price (now `average_filled_price`), and `fee` was the previewed commission (now `total_fees`).

**Deliberate semantics, each with a test:**
- A **partial** fill is left resting, not recorded — it has not closed the position.
- A **partial-then-CANCELLED** order records what actually sold. Dropping it would leave `_held_position` reporting the full position held.
- A **CANCELLED with no fill** closes the row without a trade outcome, and escalates CRITICAL `reconcile.position_unprotected` if the position is still held.
- A **broker error on one order** never abandons the rest.
- An **exit with no entry context** is skipped rather than guessed, matching `record_closed_trade`.
- A **FILLED order reporting price 0** is marked filled but records no P&L — feeding 0 to the producer would fabricate a full-notional phantom loss.

### A4. A voluntary exit clears the resting bracket first (`5c14395`)

`execute` calls `_clear_resting_bracket` before any SELL, and **refuses the exit** if the bracket cannot be cancelled.

**Why:** this was a functional break introduced by A2. `place_bracket` commits the entire base position; `_handle_exits` then issued a full-size market SELL for the same inventory, which on spot is rejected for insufficient funds — so `position_rule` was never cleared, no outcome recorded, and the agent retried the same doomed sell every cycle. A2 was validated against the path it changed (bracket fills) and not the path it left behind.

**Placed in `execute` rather than in `_handle_exits`** so every sell path gets it by construction rather than by each caller remembering.

### A5. Known-open items carried into Part B

| Item | Consequence today |
|---|---|
| `position_rule` is per-product, not per-tranche | Averaging up mis-attributes entry context. **Live constraint: one tranche per product.** |
| `place_bracket`'s return is discarded (`executor.py:154`) | No bracket id is persisted, so `roll_to_break_even`/`trail_stop_atr` are unreachable *by construction* |
| A dead bracket leaves a naked position | CRITICAL is logged; nothing re-brackets it |
| `get_order`/`cancel_order` live only on `cb_client` | That module is scheduled for deletion by broker-port Phase B; the `Broker` port has neither |
| `keel simulate` has no rail-11 producer | Third instance of the dormant-rail pattern |

---

## Part B — To build

### Task 1: Persist the bracket order id

Unblocks Tasks 2 and 3. Smallest possible change that makes the bracket addressable.

**Files:**
- Modify: `keel/execution/executor.py` (`execute`, ~line 154; `place_bracket`)
- Modify: `keel/execution/reconcile.py` (clear the key on a terminal bracket)
- Test: `tests/execution/test_executor.py`, `tests/execution/test_reconcile.py`

**Interfaces:**
- Produces: `agent_state["bracket_order:<product_id>"] -> int` (the local `orders.id` of the resting bracket), written by `place_bracket`, cleared whenever the bracket reaches a terminal state.

- [x] **Step 1: Write the failing test**

```python
def test_place_bracket_records_the_bracket_order_id(repo):
    """`roll_to_break_even`/`trail_stop_atr` take an `old_stop_order_id` and there is no
    production lookup that can produce one -- `execute` discards `place_bracket`'s return, so
    they are unreachable by construction rather than merely uncalled. Persisting the id is the
    prerequisite for wiring them, and for re-bracketing a position whose bracket died."""
    broker = FakeBroker()

    order_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    assert repo.get_state("bracket_order:BTC-USD") == order_id
```

- [x] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/execution/test_executor.py -k records_the_bracket_order_id -q`
Expected: FAIL — `assert None == 3`

- [x] **Step 3: Write the bracket id alongside the stop/target pair**

In `place_bracket`, immediately after the existing `repo.set_state(f"open_target:{product_id}", target)`:

```python
    # The resting bracket's local order id. `open_stop`/`open_target` describe the PRICES; this
    # names the ORDER, which is what a stop roll must cancel and what a re-bracket must replace.
    # Written by the same single writer as its price partners so the three cannot disagree.
    repo.set_state(f"bracket_order:{product_id}", result.order_id)
```

- [x] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/execution/test_executor.py -k records_the_bracket_order_id -q`
Expected: PASS

- [x] **Step 5: Write the failing test for clearing it**

```python
def test_a_terminal_bracket_clears_the_bracket_order_key(repo):
    """A stale `bracket_order` would have a later roll or re-bracket cancel an order that is
    already gone -- and `_cancel_at_exchange` now RAISES on an unconfirmable cancel, so a stale
    key turns into a refused exit rather than a silent no-op."""
    _seed_bracket(repo)
    repo.set_state("bracket_order:BTC-USD", 2)
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert repo.get_state("bracket_order:BTC-USD") is None
```

- [x] **Step 6: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/execution/test_reconcile.py -k clears_the_bracket_order_key -q`
Expected: FAIL — `assert 2 is None`

- [x] **Step 7: Clear it wherever the position is released**

In `reconcile._record_fill`, alongside the existing clears:

```python
    repo.set_state(f"bracket_order:{product_id}", None)
```

And in `executor._clear_resting_bracket`, after a successful cancel:

```python
        repo.set_state(f"bracket_order:{product_id}", None)
```

- [x] **Step 8: Run the full suite and the gates**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/mypy .`
Expected: all pass; baseline fixture unchanged.

- [x] **Step 9: Mutation check**

Delete the `set_state(f"bracket_order:...")` line in `place_bracket` and re-run the suite.
Expected: **exactly** `test_place_bracket_records_the_bracket_order_id` fails. If nothing fails, the test is not holding the wiring — fix the test before proceeding. Restore afterwards.

- [x] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: persist the resting bracket's order id"
```

---

### Task 2: Re-bracket a position whose bracket died

**Files:**
- Modify: `keel/execution/reconcile.py`
- Test: `tests/execution/test_reconcile.py`

**Interfaces:**
- Consumes: `agent_state["bracket_order:<product>"]`, `open_stop:`, `open_target:` (Task 1); `executor.place_bracket`.

- [x] **Step 1: Write the failing test**

```python
def test_a_dead_bracket_on_a_held_position_is_replaced(repo):
    """Coinbase cancels resting orders for reasons outside our control -- product status
    changes, self-trade prevention, an operator tapping cancel in the mobile app. Logging
    CRITICAL and leaving the position naked is not a resting state a trading agent should sit in
    for an unbounded time. Re-place from the recorded stop/target."""
    _seed_bracket(repo)
    repo.set_state("open_target:BTC-USD", Decimal("53000"))
    broker = _RebracketingBroker({"cb-1": {
        "order_id": "cb-1", "status": "CANCELLED", "filled_size": Decimal("0"),
        "average_filled_price": Decimal("0"), "total_fees": Decimal("0"),
    }})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    assert broker.placed, "no replacement bracket was placed"
    leg = broker.placed[-1]["order_configuration"]["trigger_bracket_gtc"]
    assert leg["stop_trigger_price"] == "49000"
    assert leg["limit_price"] == "53000"
    assert repo.get_state("bracket_order:BTC-USD") is not None
```

Add the fake alongside `_Broker` in the same file:

```python
class _RebracketingBroker(_Broker):
    """`_Broker` plus the order-placement surface `place_bracket` needs."""

    def __init__(self, orders=None):
        super().__init__(orders)
        self.placed: list[dict] = []

    def get_accounts(self):
        return [{"currency": "USDC", "available_balance": Decimal("1000000")}]

    def preview_order(self, product_id, side, order_configuration):
        return {"order_total": Decimal("50"), "commission_total": Decimal("0"),
                "errs": [], "warning": []}

    def place_order(self, product_id, side, order_configuration):
        self.placed.append({"product_id": product_id, "side": side,
                            "order_configuration": order_configuration})
        return {"success": True, "order_id": f"cb-re-{len(self.placed)}"}
```

- [x] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/execution/test_reconcile.py -k dead_bracket_on_a_held_position_is_replaced -q`
Expected: FAIL — `AssertionError: no replacement bracket was placed`

- [x] **Step 3: Replace the bracket instead of only warning**

Replace the body of `_warn_if_position_left_unprotected` with a re-bracket attempt, keeping the CRITICAL as the *fallback* when replacement is impossible:

```python
def _rebracket_or_escalate(
    broker: Any, repo: Repository, config: Config, row: dict[str, Any], now_ts: int
) -> None:
    """Re-place the exit bracket for a still-held position whose bracket died, or escalate.

    Leaving a naked position and logging CRITICAL is right at the instant of detection but wrong
    as a resting state: nothing else revisits the order (it is no longer `pending`), so without
    this the position stays unprotected until a human notices.

    The recorded `open_stop`/`open_target` are reused deliberately rather than recomputed: they
    are the levels the ORIGINAL trade was risk-sized against, and inventing new ones here would
    silently re-risk the position on a level no rule produced.
    """
    if str(row["side"]).upper() != Side.SELL.value.upper():
        return
    product_id = row["product_id"]
    qty, _avg_cost = _held_position(repo, product_id)
    if qty <= 0:
        return

    stop = repo.get_state(f"open_stop:{product_id}")
    target = repo.get_state(f"open_target:{product_id}")
    position = repo.get_state(f"position_rule:{product_id}") or {}
    if stop is None or target is None:
        _escalate_unprotected(repo, row, qty, "no recorded stop/target to re-place from")
        return

    new_id = executor.place_bracket(
        broker, repo, config,
        product_id=product_id,
        qty=qty,
        stop=stop,
        target=target,
        rule_name=position.get("rule_name") or "rebracket",
        now_ts=now_ts,
    )
    if new_id is None:
        _escalate_unprotected(repo, row, qty, "replacement bracket was vetoed or rejected")
        return

    log_event(
        logger,
        logging.WARNING,
        "reconcile.bracket_replaced",
        product=product_id,
        dead_order_id=row["id"],
        new_order_id=new_id,
    )


def _escalate_unprotected(
    repo: Repository, row: dict[str, Any], qty: Decimal, why: str
) -> None:
    log_event(
        logger,
        logging.CRITICAL,
        "reconcile.position_unprotected",
        product=row["product_id"],
        order_id=row["id"],
        held_qty=str(qty),
        reason=why,
        detail=(
            "the exit bracket is gone from the exchange and could not be replaced -- this "
            "position has NO protective stop. Re-place one or close it before trading on."
        ),
    )
```

Update the call site in the `_DEAD` branch from `_warn_if_position_left_unprotected(repo, row, status)` to `_rebracket_or_escalate(broker, repo, config, row, now_ts)`, and add `from keel.execution import executor` to the imports.

- [x] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/execution/test_reconcile.py -q`
Expected: PASS, including the pre-existing `test_a_dead_bracket_on_a_still_held_position_escalates_loudly` — which must now be updated to use a broker that *cannot* place (so the escalation path still fires). Change it to use plain `_Broker`, which has no `place_order`, and assert the CRITICAL.

- [x] **Step 5: Full gates + mutation check**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/mypy .`
Then stub `_rebracket_or_escalate`'s `place_bracket` call to return `None` and confirm the CRITICAL test fails.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: re-place a dead exit bracket instead of leaving the position naked"
```

---

### Task 3: A per-tranche position ledger

**The live blocker.** Until this lands, keel must run one tranche per product.

**Files:**
- Modify: `keel/data/db.py` (SCHEMA_VERSION 3 → 4, new `positions` table)
- Modify: `keel/data/repository.py` (typed access)
- Modify: `keel/agent.py` (write on ENTER, read on exit), `keel/execution/reconcile.py`
- Test: `tests/data/test_positions.py`, `tests/data/test_migrations.py`, `tests/test_agent.py`

**Interfaces:**
- Produces: `Repository.open_position(...) -> int`, `Repository.get_open_positions(product_id) -> list[dict]`, `Repository.close_position(position_id, ...)`.
- Replaces: `agent_state["position_rule:<product>"]` as the carrier of entry context. The key stays only as the exit-rule ownership marker its docstring always said it was.

**Why a table and not another `agent_state` key:** entry context is per-tranche and multi-valued (`opened_at`, `entry_fill`, `qty`, `entry_fee`, `rule_name`, `bracket_order_id`). A single JSON blob keyed by product is what produced every attribution bug on this branch — last-write-wins on a second entry, and a bracket filling after a newer entry attributes its P&L to the wrong tranche.

#### ⚠️ REVISED after review — five conflicts with Tasks 1–2 as landed

Assessed before execution against `b294f0e` and `789e36b`. The steps below are corrected in place; this block records *why*, since the original wording is the thing a reviewer would otherwise check against.

1. **Step 10 was not implementable.** It had `run_once` call `place_bracket` and thread its return into `set_position_bracket`. `run_once` never calls `place_bracket` — `executor.execute` does, internally, and discards the return (`executor.py:154`). That is Task 1's own stated premise, so the plan contradicted itself. **Fix:** `ExecutionResult` gains `bracket_order_id`, so `execute` surfaces what it already knows. ENTER becomes `execute` → `open_position` → `set_position_bracket`.

2. **Task 2's re-bracket orphaned the tranche — the dangerous one.** `_rebracket_or_escalate` re-places via `place_bracket`, but nothing re-pointed `positions.bracket_order_id`, so the tranche still named the dead order. When the replacement filled, `get_position_for_bracket` returned `None`, the "exit without position context" skip fired, and **no `trade_outcomes` row was written** — rail 16 blind to that loss. Exactly the failure class this branch exists to close. **Fix:** `_rebracket_or_escalate` resolves its tranche via `get_position_for_bracket(dead_row["id"])` and re-points it after a successful replace.

3. **Dual source of truth.** Task 1 made `agent_state["bracket_order:<product>"]` the pointer; this task calls `positions.bracket_order_id` "the ONE linkage direction". Both would be live, per-product vs per-tranche. Confirmed the agent_state key is **write-only — nothing in `keel/` reads it**. **Fix:** drop it. Per-tranche subsumes per-product. Task 1 keeps its value as the step that proved the id was obtainable and reachable; this task relocates it to the right home.

4. **The migration test could never pass.** It asserted `PRAGMA user_version == 4`; this codebase stamps a `schema_version` **table** and never writes the pragma (always `0`). **Fix:** assert the table, matching `test_migrations.py:48`. The rest of Step 3 is sound — `_SCHEMA_STATEMENTS` genuinely runs before the version check (`db.py:306-309`), so the additive-DDL claim holds.

5. **The rule-exit path was left behind.** The task declared `position_rule` no longer carries `entry_fill`/`qty`/`entry_fee`, but `agent.py:344` still hands that blob to `record_closed_trade`, which **skips the outcome when `entry_fill` is missing** — so voluntary rule exits would silently stop producing outcomes, with `tests/test_agent.py:750` as the tripwire. Rewiring only `reconcile._record_fill` would have made this a fourth dormant-rail instance. **Fix:** `_handle_exits` closes every open tranche FIFO and records one outcome per tranche, apportioning the exit order's single fee pro-rata by qty. Its wiring guard moves to the `positions` table rather than being weakened.

Also corrected: `_rebracket_or_escalate` sized the replacement from `_held_position` (the whole **product**), which over-commits once a product holds more than one tranche. It now sizes from the owning tranche's `qty`.

Found by a second adversarial pass, and worse than the above because they would have produced a *green* task:

6. **Step 9's red step was fake — the test passes against unmodified code.** `_seed_bracket` seeds `position_rule` with `entry_fill: 50000`, and the test asserts `entry_fill == 50000` / `pnl_net == -15.94` — exactly what today's `_record_fill` already computes from that blob. `open_position` never touches `position_rule`, so the second tranche never enters the calculation and the "watch it fail" step could not fail. A test that cannot fail is not holding the attribution it names. **Fix:** seed the blob with the *newest* tranche's `52000` — which is what the last-write-wins bug actually leaves behind — so the old path yields `-35.94` and only correct per-tranche attribution yields `-15.94`. Also seed a second filled BUY so `_held_position` agrees with the two-tranche ledger.

7. **Step 9's closing assertion was vacuous.** `[r["id"] for r in get_open_positions(...)] != [first]` is already true with both tranches open (`[first, second] != [first]`), so it cannot detect a missing `close_position` — the very wiring Step 10.3 adds. **Fix:** assert `== [second]`.

8. **`_position_row_to_dict` is called three times and never defined,** and it cannot be a blanket money-decode: `qty`/`entry_fill`/`entry_fee` are TEXT needing `_text_to_dec`, while `id`/`bracket_order_id`/`opened_at` are INTEGER and must stay `int` (Step 5 asserts `== [11, 12]`).

9. **A partially-filled dead bracket would close a tranche that is still partly held.** `reconcile.py` routes `filled_size > 0` to `_record_fill`, which has no full-vs-partial distinction. **Fix:** record the outcome for what sold, but only `close_position` when the fill covers the tranche's qty; otherwise leave it open and log.

- [x] **Step 1: Write the failing migration test**

```python
def test_migration_to_v4_creates_the_positions_table():
    conn = connect(":memory:")
    migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
    assert cols >= {
        "id", "product_id", "rule_name", "opened_at", "closed_at",
        "qty", "entry_fill", "entry_fee", "bracket_order_id", "status",
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
```

- [x] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/data/test_migrations.py -k v4 -q`
Expected: FAIL — no such table `positions`

- [x] **Step 3: Add the table and bump the version**

In `keel/data/db.py`, add to `_SCHEMA_STATEMENTS` (which runs before the version check, so an existing v3 DB picks it up from the `IF NOT EXISTS` DDL — same additive pattern as `trade_outcomes` at v3):

```sql
CREATE TABLE IF NOT EXISTS positions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id        TEXT    NOT NULL,
    rule_name         TEXT    NOT NULL,
    opened_at         INTEGER NOT NULL,
    closed_at         INTEGER,
    qty               TEXT    NOT NULL,
    entry_fill        TEXT    NOT NULL,
    entry_fee         TEXT    NOT NULL,
    bracket_order_id  INTEGER,
    status            TEXT    NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_positions_open
    ON positions (product_id, status);
```

Bump `SCHEMA_VERSION` to `4` and add `_migrate_v4_positions` as a deliberate no-op that only advances the stamp, mirroring `_migrate_v3_trade_outcomes`.

- [x] **Step 4: Run it and watch it pass, then update the pre-existing version assertions**

Run: `.venv/bin/python -m pytest tests/data/test_migrations.py -q`
Three pre-existing assertions expect version 3. Update them to 4 — read the whole file first and confirm each is legitimate fallout of the bump, not a weakened assertion. (Task 2 of the previous plan hit exactly this and the reviewer verified it; do the same.)

- [x] **Step 5: Write the failing repository test**

```python
def test_two_tranches_in_one_product_are_separately_addressable(repo):
    """The whole point. `position_rule:<product>` was last-write-wins, so a second entry
    overwrote the first's entry price and qty -- and a bracket from the FIRST tranche filling
    later computed its P&L against the SECOND tranche's entry. That inflated loss fed rail 16's
    counter and the trade_outcomes ledger."""
    a = repo.open_position(product_id="BTC-USD", rule_name="turtle_breakout", opened_at=1_000,
                           qty=Decimal("0.01"), entry_fill=Decimal("50000"),
                           entry_fee=Decimal("3"), bracket_order_id=11)
    b = repo.open_position(product_id="BTC-USD", rule_name="turtle_breakout", opened_at=2_000,
                           qty=Decimal("0.01"), entry_fill=Decimal("52000"),
                           entry_fee=Decimal("3.1"), bracket_order_id=12)

    assert a != b
    rows = repo.get_open_positions("BTC-USD")
    assert [r["entry_fill"] for r in rows] == [Decimal("50000"), Decimal("52000")]
    assert [r["bracket_order_id"] for r in rows] == [11, 12]
```

- [x] **Step 6: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/data/test_positions.py -q`
Expected: FAIL — `AttributeError: 'Repository' object has no attribute 'open_position'`

- [x] **Step 7: Implement typed access**

Follow `insert_trade_outcome`/`get_trade_outcomes` in `keel/data/repository.py` for money encoding — use the file's existing `_dec_to_text`/`_text_to_dec` helpers (the trade-outcomes pair used a bare `Decimal(...)`, which the review flagged as inconsistent; do not repeat it).

```python
def open_position(
    self, *, product_id: str, rule_name: str, opened_at: int, qty: Decimal,
    entry_fill: Decimal, entry_fee: Decimal, bracket_order_id: int | None = None,
) -> int:
    """Record a newly opened tranche and return its id."""
    cur = self._conn.execute(
        """
        INSERT INTO positions
            (product_id, rule_name, opened_at, qty, entry_fill, entry_fee,
             bracket_order_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (product_id, rule_name, opened_at, _dec_to_text(qty), _dec_to_text(entry_fill),
         _dec_to_text(entry_fee), bracket_order_id),
    )
    self._conn.commit()
    return int(cur.lastrowid)


def get_open_positions(self, product_id: str | None = None) -> list[dict[str, Any]]:
    """Open tranches, oldest first. FIFO is the attribution order a later exit uses."""
    sql = "SELECT * FROM positions WHERE status = 'open'"
    params: list[Any] = []
    if product_id is not None:
        sql += " AND product_id = ?"
        params.append(product_id)
    sql += " ORDER BY opened_at, id"
    return [self._position_row_to_dict(r) for r in self._conn.execute(sql, params)]


def get_position_for_bracket(self, bracket_order_id: int) -> dict[str, Any] | None:
    """The open tranche whose bracket is `bracket_order_id`, or `None`.

    This is the ONE linkage direction: a position points at its bracket, never the reverse.
    Reconciliation starts from a filled order row and needs the tranche that owns it, so this is
    the lookup it uses instead of reading `position_rule:<product>`.
    """
    row = self._conn.execute(
        "SELECT * FROM positions WHERE bracket_order_id = ? AND status = 'open'",
        (bracket_order_id,),
    ).fetchone()
    return None if row is None else self._position_row_to_dict(row)


def set_position_bracket(self, position_id: int, bracket_order_id: int) -> None:
    """Attach a (re-placed) bracket to an open tranche."""
    self._conn.execute(
        "UPDATE positions SET bracket_order_id = ? WHERE id = ?",
        (bracket_order_id, position_id),
    )
    self._conn.commit()


def close_position(self, position_id: int, *, closed_at: int) -> None:
    self._conn.execute(
        "UPDATE positions SET status = 'closed', closed_at = ? WHERE id = ?",
        (closed_at, position_id),
    )
    self._conn.commit()
```

- [x] **Step 8: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/data/test_positions.py -q`
Expected: PASS

- [x] **Step 9: Write the failing attribution test**

```python
def test_an_older_tranches_bracket_filling_attributes_to_THAT_tranche(repo):
    """The bug this task exists to kill. Tranche 1 at 50000, tranche 2 at 52000, then tranche
    1's bracket fills at 49000. P&L must be computed against 50000 -- against 52000 it books a
    loss that never happened and feeds it to a live-money breaker."""
    first = repo.open_position(product_id=PRODUCT, rule_name="turtle_breakout", opened_at=1_000,
                               qty=Decimal("0.01"), entry_fill=Decimal("50000"),
                               entry_fee=Decimal("3"), bracket_order_id=None)
    repo.open_position(product_id=PRODUCT, rule_name="turtle_breakout", opened_at=2_000,
                       qty=Decimal("0.01"), entry_fill=Decimal("52000"),
                       entry_fee=Decimal("3.1"), bracket_order_id=None)
    bracket_id = _seed_bracket(repo, native_id="cb-1")   # the resting SELL order row
    repo.set_position_bracket(first, bracket_id)          # ...owned by the FIRST tranche
    broker = _Broker({"cb-1": {
        "order_id": "cb-1", "status": "FILLED", "filled_size": Decimal("0.01"),
        "average_filled_price": Decimal("49000"), "total_fees": Decimal("2.94")}})

    reconcile.reconcile_open_orders(broker, repo, _config(), now_ts=NOW)

    outcome = repo.get_trade_outcomes()[0]
    assert outcome["entry_fill"] == Decimal("50000")
    # (49000 - 50000) * 0.01 - 2.94 exit - 3 entry
    assert outcome["pnl_net"] == Decimal("-15.94")
    assert [r["id"] for r in repo.get_open_positions(PRODUCT)] != [first]
```

- [x] **Step 10: Run it, watch it fail, then thread the position id through**

Run: `.venv/bin/python -m pytest tests/execution/test_reconcile.py -k older_tranches_bracket -q`
Expected: FAIL — the outcome is attributed to the newest `position_rule` blob.

Wire it, keeping ONE linkage direction — a position points at its bracket, never the reverse:

1. `agent.run_once`'s ENTER path calls `repo.open_position(...)`, then `place_bracket(...)`, then `repo.set_position_bracket(position_id, bracket_order_id)`.
2. `reconcile._record_fill` resolves the owning tranche with `repo.get_position_for_bracket(row["id"])` instead of reading `position_rule:<product>`, and falls back to the existing skip-with-warning when it returns `None` (an exit whose tranche we cannot identify is the same "do not invent a P&L" case).
3. On a full close, `repo.close_position(position["id"], closed_at=now_ts)`.

`position_rule:<product>` survives only as the exit-rule ownership marker its docstring always described — it no longer carries `entry_fill`/`qty`/`entry_fee`.

- [x] **Step 11: Full gates + the wiring mutation check**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/mypy .`
Then stub the `repo.open_position(...)` call in `run_once` and confirm an agent-level test fails — not only a repository unit test. If only unit tests fail, the wiring is unheld and this task has reproduced the branch's signature defect.

- [x] **Step 12: Commit**

```bash
git add -A
git commit -m "feat: per-tranche position ledger replaces position_rule for entry context"
```

---

### Task 4: Put `get_order`/`cancel_order` on the broker port

Resolves layering debt: both landed only on `keel/data/cb_client.py`, which broker-port Phase B deletes (`docs/superpowers/plans/2026-07-19-keel-broker-port-phase-a.md:27`). `packages/keel-broker-api/keel_broker_api/port.py` has neither, so Phase B's scope silently grew by two endpoints.

**Files:**
- Modify: `packages/keel-broker-api/keel_broker_api/port.py` (add to the `Broker` protocol)
- Modify: `packages/keel-broker-coinbase/keel_broker_coinbase/{transport.py,adapter.py}`
- Test: the package's existing conformance suite

- [x] **Step 1: Write the failing conformance test**

Add to the conformance suite, following its existing shape:

```python
def test_get_order_returns_observed_economics(broker):
    """`execution.reconcile` duck-types this today against `cb_client`, a module Phase B
    deletes. It must exist on the PORT or reconciliation breaks the moment Phase B lands."""
    order = broker.get_order("cb-1")
    assert order.status in {"FILLED", "OPEN", "CANCELLED", "EXPIRED", "FAILED", "PENDING"}
    assert isinstance(order.average_filled_price, Decimal)
    assert isinstance(order.total_fees, Decimal)


def test_cancel_order_reports_per_order_confirmation(broker):
    """Coinbase's batch_cancel answers per order, so a 200 is not a confirmation. The port must
    surface the per-order boolean, not the HTTP result."""
    assert broker.cancel_order("cb-1") is True
    assert broker.cancel_order("already-filled") is False
```

- [x] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest packages/keel-broker-api -q`
Expected: FAIL — `Broker` has no attribute `get_order`

- [x] **Step 3: Add `OrderStatus` to the port's result types and both methods to the protocol**

Mirror the existing `Balance`/`Preview`/`PlaceResult` dataclasses in `packages/keel-broker-api/keel_broker_api/results.py`:

```python
@dataclass(frozen=True)
class OrderStatus:
    """Observed state of a previously placed order. Money fields default to `Decimal("0")`
    rather than `None` -- callers do arithmetic on them and must never special-case."""

    order_id: str
    status: str
    filled_size: Decimal
    average_filled_price: Decimal
    total_fees: Decimal
```

And on the `Broker` protocol:

```python
    def get_order(self, order_id: str) -> OrderStatus: ...

    def cancel_order(self, order_id: str) -> bool: ...
```

- [x] **Step 4: Implement on the Coinbase adapter**

Add `get_order` and `cancel_orders` to `Transport` in `packages/keel-broker-coinbase/keel_broker_coinbase/transport.py`, then implement both adapter methods against them — port the normalization from `keel/data/cb_client.py:254-298` verbatim, including the empty-`results`-is-failure rule.

- [x] **Step 5: Run the conformance suite and the full suite**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/mypy .`

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: get_order/cancel_order on the broker port and Coinbase adapter"
```

---

### Task 5: A rail-11 producer for the simulator

Third instance of the dormant-rail pattern. `keel simulate` cannot trip rail 11, so its threshold cannot be swept — exactly the gap `49d9657` closed for rail 16.

**Files:**
- Modify: `keel/sim/account.py` (drawdown state on `SimAccount`), `keel/sim/portfolio_sim.py` (call it per bar)
- Test: `tests/sim/test_account.py`, `tests/sim/test_portfolio_sim.py`

- [x] **Step 1: Write the failing acceptance test**

The behavioural one. A parity test will not catch this — that lesson is `49d9657`.

```python
def test_sweeping_max_total_dd_pct_changes_the_backtest():
    """Rail 11 must be sweepable in the sim for the same reason rail 16 had to be: a threshold
    you cannot vary is a threshold you cannot choose. If these are equal, the producer is not
    wired and the sweep is a no-op."""
    loose = _drawdown_backtest(max_total_dd_pct=Decimal("0.90"))
    tight = _drawdown_backtest(max_total_dd_pct=Decimal("0.05"))

    assert len(loose.trades) > len(tight.trades), (
        "max_total_dd_pct had no effect -- the sim-side equity/drawdown producer is not wired"
    )
```

- [x] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/sim/test_portfolio_sim.py -k sweeping_max_total_dd -q`
Expected: FAIL with equal trade counts — the same signature `28 > 28` failure rail 16 showed.

- [x] **Step 3: Add drawdown state to `SimAccount`**

Mirror `record_trade_outcome`'s shape and place it next to the streak state it sits beside. Track `equity_high_water_mark` in memory (no `Repository`, by design — see the module docstring), update it from `mark_to_market` each bar, and expose `drawdown_total_pct` for `can_open` to read. Contributions are already tracked (`self.contributed`), so unlike live there is no external-flow ambiguity — deposits are known exactly, and the HWM must be rebased by them.

- [x] **Step 4: Enforce it in `can_open` and call it per bar**

Add the rail-11 check to `SimAccount.can_open` alongside rail 16, and call the equity update from `portfolio_sim`'s per-bar loop *before* signals are evaluated, mirroring `run_once`'s reconcile → equity → entries ordering.

- [x] **Step 5: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/sim/ -q`
Expected: PASS. Report the sweep response curve (as `49d9657` did: `0 → 28 | 1 → 3 | 2 → 4 | 3 → 5 | 5 → 6`) in the commit message — a monotonic curve is the evidence the rail is genuinely tunable.

- [x] **Step 6: Mutation check**

Stub the per-bar equity update in `portfolio_sim` and confirm **exactly** the acceptance test fails.

- [x] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: sim-side equity/drawdown producer so rail 11 is sweepable"
```

---

## Done criteria

- `.venv/bin/python -m pytest -q`, `ruff check .`, `mypy .` all pass; `tests/fixtures/baseline_backtest.json` byte-identical.
- Averaging into a product attributes each tranche's P&L to its own entry price — verified by a test, not by convention.
- No position can sit unprotected across cycles: a dead bracket is replaced, or CRITICAL is logged with the reason it could not be.
- `roll_to_break_even`/`trail_stop_atr` are reachable (a bracket id is persisted) — wiring them is still out of scope, but they are no longer unreachable *by construction*.
- `get_order`/`cancel_order` exist on the `Broker` port, so broker-port Phase B can delete `cb_client.py` without breaking reconciliation.
- Sweeping `max_total_dd_pct` changes a backtest.
- Every producer added here has a wiring test that fails when the producer is unhooked.

## Explicitly out of scope

- **Wiring `roll_to_break_even`/`trail_stop_atr` to a rule.** Task 1 makes them reachable; deciding *when* to roll a stop is a strategy question needing its own spec. Note the pre-flight from the review: `_roll_stop` should pre-flight `guards.check` on the replacement *before* cancelling, since a veto unrelated to the roll (kill-switch, daily cap, stale feed) would otherwise leave the position naked.
- **`scale_out`.** Unreachable, and wrong in the single-bracket world: a partial SELL collides with a bracket committing the full position, and it records no outcome. `test_scale_out_has_no_production_caller` is the tripwire; fix those two things before wiring it.
- **A production consumer for `get_trade_outcomes`.** The table is deliberately write-only for now — an audit log and the substrate for a future sweep.
