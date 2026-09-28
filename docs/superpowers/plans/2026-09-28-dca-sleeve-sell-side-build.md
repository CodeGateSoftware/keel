# DCA Sleeve Sell-Side Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three prerequisites (#799, #811, #798), then build the sell side of the DCA sleeve in preview-only mode: the shared contracts, the proposal pipeline, the `reverse_dca` rule kind, the trim report with `profit_take`, and the exit monitor with `sleeve_exit`. The build ends with one gated placement path that is off by default.

**Architecture:** Every sleeve sale is a named rule kind (`promotion_class = "sleeve_sell"`). Each kind emits a `Reduction` through a new hook, `Rule.reduce_signal`. A new cycle step, `agent._handle_reductions`, runs after `_handle_exits` and before entries. It arbitrates between kinds, applies the sleeve caps, and hands at most one `Reduction` per product per UTC day to `executor.reduce`. In this plan `executor.reduce` runs the rails and the venue preview, records a `sell_proposals` row, and places nothing. The `positions` ledger is the only source of lots and average entry (`Holding`, via `sleeve.holding_of`). Placement is added in the last two PRs, and only behind both a separate TTY-armed sells window and a typed `yes` per sale.

**Tech Stack:** Python 3.14, click, `Decimal`, sqlite3 (no ORM), pytest with `CliRunner`, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-28-dca-sleeve-sell-side-design.md` (#857, merged as #859). It is cited below as "the spec", with `§n` for its sections. Its §11 is the PR plan this document re-orders, and its §12 defaults stand as decisions, because the operator merged it without answering them. The spec's header still says "DRAFT ... No code until the recommendations in §10 are accepted". The merge is that acceptance.

**Prerequisite issues (bodies and every comment read 2026-09-28; none of the three has a comment):** #799, #811, #798.

---

## Operator decision this plan implements (2026-09-28)

1. Resolve the prerequisites first: #811, #799 and #798.
2. Implement `reverse_dca`.
3. Deliver trims (`keel dca trim --preview`, and `profit_take` in preview) and the exit monitor (`sleeve_exit`) in **preview-only** mode, before any live placement is enabled.

**Out of scope:** steering, band rebalancing as a rule (`band_rebalance`), and rotation or tax-loss harvesting (`rotation`). The spec recommends not building them (§5, §8, §10). The read-only `--view bands` report is in scope (§5 recommends it). No `band_rebalance` or `rotation` rule class is written. Their names are reserved only in the arbitration order constant, so rail 10's vocabulary is decided once (§3.1).

## Global Constraints

- **TDD for every task.** The failing test comes first, and you watch it fail for the stated reason before you write code. A mutation run checks the tests. It does not replace writing them first.
- **Suite, per PR:** `uv run pytest -q`, `uv run ruff check keel tests`, `uv run ruff format --check keel tests`, and bare `uv run mypy`, never `mypy keel/`.
- **PRs that touch `packages/`** are P8 and P15 (`keel_core/notifications.py`). Run their tests with `PYTHONPATH="$PWD:$PWD/packages/keel-core:$PWD/packages/keel-broker-coinbase" uv run python -m pytest ...`, because the venv's editable installs point at the main checkout.
- **Worktrees only.** Each PR is built in its own `git worktree` off `origin/main`. Never touch the main checkout at `~/Development/work/CodeGate/keel`, and never touch `~/keel`, which is the live deployment and live money.
- **Rules live in neighbouring docstrings.** Before extending a module, read its module docstring and the sibling functions' docstrings. Each new module carries the rule it encodes in its own docstring.
- **Money is `Decimal`,** stored as TEXT (`_dec_to_text` / `_text_to_dec`). Timestamps are INTEGER epoch seconds. NULL means "not recorded", never zero.
- **Fees.**
  - A live figure is the venue's previewed commission (`Preview.est_fee`).
  - The fallback is `config.fees.taker_pct`, which is `Decimal("0.012")`, a fraction. Every fallback records its source as `"fallback:config.fees.taker_pct"`.
  - The rate `0.009` is never written as a literal anywhere (spec §2.2, Q5).
  - Every sell pays the fee. Rail 14 is a monthly BUY cap only (#836).
- **Slippage.** Per product, from `keel.commands.rules.backtest_slippage(repo, product_id)`. Import it lazily inside the function that uses it, as `doctor.gather_findings` does. It is the one liquidity-scaled definition.
- **Research freeze (2026-09-27) holds.** This plan runs no sweep and no new pre-registered trial:
  - `reverse_dca`'s backtest and sim tests are **fidelity tests of the harness**, pinned against hand computations on synthetic candles. They are not verdicts about returns (spec §6, "Evidence status").
  - The trim preview reports numbers and places nothing.
  - `keel rules backtest`'s proposal replay for a sleeve-sell rule is a description with no threshold (spec §3.7).
  - Nothing is appended to `docs/experiments/trials-ledger.jsonl`.
  - Spec §11 PR 7, the trial-4 pre-registration, is **not** in this plan.
- **Schema.** Two bumps: v22 in P6 (the `sell_proposals` table) and v23 in P17 (`profile.sells_until`). Each follows the existing pattern:
  - DDL in `_SCHEMA_STATEMENTS`;
  - a documented `_migrate_vNN_*` function that is idempotent via `PRAGMA table_info` or `IF NOT EXISTS`, with no backfill;
  - an entry in `_MIGRATIONS`;
  - a bump to `SCHEMA_VERSION`;
  - tests in `tests/data/test_migrations.py`. The literal `== 21` (or `== db.SCHEMA_VERSION == 21`) is pinned in **eight** tests there, not one: `test_fresh_database_is_stamped_at_the_current_version` (line 52), `test_v14_migration_bumps_the_stored_version` (627), `test_v15_migration_bumps_the_stored_version` (788), `test_an_existing_orders_table_gains_the_submit_book_by_ALTER` (890), `test_migration_to_v20_adds_the_columns_and_the_new_tables` (1050), `test_v19_database_gains_v20_columns_as_NULL_no_backfill` (1097), `test_v20_on_a_pre_v11_chain_does_not_duplicate_columns` (1168) and `test_a_v20_database_gains_rule_id_as_NULL_no_backfill` (1316). Every one of these relaxes to `stamped == db.SCHEMA_VERSION` (or `version == db.SCHEMA_VERSION`), because a database that migrates to head no longer lands on 21 once v22 exists. P6 Task 1 relaxes all eight in one step, and its own new v22 assertions are written against `db.SCHEMA_VERSION`, not the literal `22`, so P17's v23 bump does not repeat this exact break.
  - **Two more pins live outside `test_migrations.py`** (found by `grep -rn "SCHEMA_VERSION == 21" tests/` across the whole tree, which turns up exactly these two beyond the eight above — `tests/research/test_spread.py`'s `MONTHLY_BLOCK == 21` and `tests/mcp/test_tools.py`'s `len(...) == 21` are unrelated constants, not schema pins):
    - `tests/data/test_db.py:110`, inside `test_schema_version_is_21` (line 106): `assert SCHEMA_VERSION == 21`. Its own docstring reads "Deliberate tripwire: bump this literal consciously on every schema change" — it is meant to go red on every bump, not to be relaxed. P6 Task 1 renames it to `test_schema_version_is_22` and bumps the literal to `22`; P17 Task 1 renames it again to `test_schema_version_is_23` and bumps it to `23`.
    - `tests/data/test_trade_outcomes.py:42`, inside `test_schema_is_at_version_21`: `assert version == db.SCHEMA_VERSION == 21`. Unlike the tripwire, this one is an ordinary version-parity check with no docstring calling for a conscious bump, so P6 Task 1 relaxes it the same way as the eight above (`version == db.SCHEMA_VERSION`) and renames it to `test_schema_is_at_the_current_version` so the name doesn't go stale the next time the version moves.
- **Commit trailer.** Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Every PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **The spec's file names are wrong in one place.** `book_exit` lives in `keel/execution/streak.py`, not `keel/streak.py`.

## Safety invariants (every PR states them in its body and runs their tests)

- **S1: no new sell reaches the venue without `keel autonomy on --sells` at a TTY.**
  - The live `autonomy on` flag alone never releases one.
  - Mechanised by `tests/execution/test_sell_side_invariants.py`, created in P1. It pins, by AST scan, the exact set of functions that call `_run_order` or `broker.place_order` (`RUN_ORDER_CALLERS`/`PLACEMENT_CALLERS`), **and** the exact set of functions outside `executor.py` that call `executor.execute`/`executor.scale_out`/`executor.place_bracket`/`executor.roll_stop_to`/`executor.roll_to_break_even`/`executor.trail_stop_atr` (`SECOND_LEVEL_CALLERS`), so a new sell path one level removed from `_run_order` -- e.g. a CLI handler calling `executor.execute(EXIT)` or `executor.scale_out(...)` directly -- also turns this module red.
  - `executor.reduce` may not appear in either set before P18.
  - In P18 its one call must sit behind `sleeve.sells_released(...)`, which reads `Profile.is_autonomous_for_sells`.
- **S2: v1 execution is preview-only.**
  - Every `sleeve_sell` rule class declares `execution: Execution` with `Execution = Literal["preview"]`, and the registry-wide test in the invariants module asserts it.
  - No `execution: auto` value exists in this plan, so unattended selling is impossible.
  - The one placement path (P18) needs the sells window **and** a typed `yes` for each sale. It is off by default, because no row in `profile` sets `sells_until`.
- **S3: rails on sells, as the spec says (§3.4).** Every REDUCE intent goes through `guards.check` unmodified. Rails 1, 2, 9 (only with a `protective_stop`), 10, 12, 18, 19 and 21 can veto it. Rails 13, 17, 20 and 22 are buy-scoped in `guards.py` today, and a SELL passes them. P7 pins both halves. Rail 2 becomes a slicing obligation: `sleeve.slice_qty` keeps every leg at or under `max_per_order_usd`, and the proposal records how many legs (days) the sale needs.
- **S4: the browser never increases capability.** No `keel/web/` or `keel/mcp/` module may name `executor.reduce`, `insert_sell_proposal`, `update_sell_proposal`, `set_sells_window` or `close_declared_position`. The name scan is in the invariants module. The effect scan in `tests/web/test_server.py` also derives the forbidden operations from every gated CLI command automatically.

## Review Focus

These are the inputs the spec implies but does not name, most likely first. Each has a test in the task that owns the code.

1. **A 30-day `reverse_dca` cadence day that is also a 7-day `dca` buy day** (days where `day % 210 == 0`).
   - Expected: that month's distribution is skipped, not carried forward.
   - Expected: a `vetoed` proposal row with `rails.sleeve = "same_day_dca"` says why.
   - Test: P8 Task 2, `test_a_distribution_on_a_dca_buy_day_is_recorded_vetoed_not_carried`.
2. **The agent re-running on the same UTC day** (the hourly LaunchAgent trigger retries after a non-zero exit).
   - Expected: one proposal per product per UTC day, not one per run.
   - Test: P8 Task 2, `test_a_second_run_on_the_same_utc_day_writes_no_second_proposal`.
3. **A mixed-ownership product** (PAXG: turtle tranche 3 is the oldest row).
   - Expected: a `Reduction` consumes it first, FIFO.
   - Expected: the preview names that tranche.
   - Expected: `book_exit(is_dca=None)` books that leg `is_dca=0` and the DCA leg `is_dca=1`.
   - Tests: P5 Task 3 and P13 Task 1.
4. **A venue preview that raises during preview-only evaluation** (`TradeScopeDenied`, a network error, an empty field).
   - Expected: it neither aborts the cycle nor blocks the BTC DCA buy that runs after it.
   - Expected: the proposal is still recorded, with the fallback fee and `rails.preview_error`.
   - Test: P7 Task 2, `test_a_preview_that_raises_records_the_proposal_on_the_fallback_fee`.
5. **A live sleeve-sell rule in preview on a product whose only exit rule was demoted** (PAXG).
   - Expected: it must not make #811's `position.unmanaged` go quiet. A rule that can only propose does not manage an exit.
   - Test: P9 Task 3, `test_a_sleeve_sell_rule_does_not_count_as_managing_a_position`.

---

## Rulings

Each ruling is stated as `what — why — cost if wrong`.

**Folding the prerequisites into the spec's PRs**

- **R1.** #799 splits across two PRs.
  - P1 fixes proposal 2, "a filled entry must never be lost to a later failure".
  - P3 carries proposal 3's doctor check, as `ledger.drift`.
  - Proposal 1 (the adapter's `or "0"` guard) **already shipped** in #800, and proposal 4 (reconcile order id 4) was **already done by hand**: #811 shows `positions.id=3 PAXG-USD turtle_breakout`. So P3 closes #799.
  - Why: the issue body predates both. Re-doing proposal 1 is a no-op, and proposal 4 is operational work on `~/keel`, which this plan may not touch.
  - Cost if wrong: if the adapter guard regressed since #800, P1's test (`preview_order` raising on the bracket leg) still catches the stranding shape, whatever raises.
- **R2.** The spec's `sleeve.ledger_drift` and `sleeve.venue_drift` ship in the prerequisite P3, **renamed `ledger.drift` and `ledger.venue_drift`**. They sit beside the sibling `ledger.unbooked_exit`. They cover every open tranche, not only DCA.
  - Why:
    - They are the same check as #799 proposal 3 ("filled BUY with no position row" is exactly ledger qty < orders qty) and #798's "doctor check for the divergence".
    - A tranche-set check named `sleeve.*` would under-describe what it covers.
    - Landing them before any sell path is what spec §9 asks: "doctor WARN before any sale".
  - Cost if wrong: a rename in the doctor output, and one line in the web doctor view if it keys on the name.
- **R3.** `ledger.venue_drift` reads the venue holding from a new `agent_state` record, `venue_holding:<product>` = `{"total": str, "observed_at": int}`. `reconcile.record_venue_holdings` writes it once per live cycle, from one `broker.get_balances()` call.
  - Why:
    - `doctor.gather_findings` has no broker, by design (it is shared with `keel mcp` and pinned read-only).
    - `cycle_balances` (#719) stores only quote currencies. Adding base currencies there would feed `cash` readers numbers they sum.
    - The `balance_drift:` and `orphan_bracket:` records are the precedent: the cycle writes, doctor reads.
  - Cost if wrong: one extra `get_balances` call per live cycle. The record can be up to one cycle stale, and the finding prints its `observed_at`.
- **R4.** `keel positions close <id>` (#798 proposal 1) writes an `orders` row:
  - `mode='live'`, `side='SELL'`, `order_type='out_of_band'`, `status='filled'`;
  - `qty` = the tranche's held qty, `actual_fill` = the operator's `--price`, `fee` = `--fee` (default 0), `confirmation='operator_declared'`, `rule_id` = the tranche's.

  It then books that one tranche through `record_closed_trade`, with `is_dca` from the tranche's own `rule_name`, and closes it. It is typed-`yes` gated, and has its own capability row.
  - Why:
    - #798 requires the close to be "written in a way guard 6 actually reads (an order row, not only a `positions` mutation)".
    - Shrinking measured exposure is a capability increase for rails 4, 5 and 6.
    - `keel/commands/positions.py`'s docstring forbids a close action in *that* module, so the verb lives in a new module, `keel/commands/positions_close.py`.
  - Cost if wrong:
    - An operator who mistypes `--price` books a wrong P&L row. It is audit-chained, and correcting it is a second declared row plus a journal note.
    - A loss on a non-DCA tranche adds to rail 16's counter. That is honest, and the Q10 default applies.
- **R5.** P1's downgrade distinguishes two failure stages.
  - If the bracket leg fails **before** an `orders` row exists (preview or spec build), `place_bracket` writes the `unbracketed:` retry record and returns `None`.
  - If it fails **after** the row exists (`place_order` raised), the venue's state is unknown. `place_bracket` still returns `None`, so the tranche records. It writes **no** retry record, and logs CRITICAL `executor.bracket_state_unknown` naming the row.
  - Why:
    - A timeout after the venue accepted means a bracket may be resting. Re-placing would double-commit the base.
    - `_clear_resting_bracket` already fails closed on the pending row with no native id, so later exits wait for a human. That is keel's posture.
    - `TradeScopeDenied` is caught the same way, because `_run_order` already wrote the refutation before re-raising.
  - Cost if wrong: the rare place-stage failure needs a human to reconcile one pending row. This is the same as today, except the tranche is no longer lost.

**Contracts**

- **R6.** `reduce_signal` takes a third argument: `reduce_signal(self, holding, candles_by_tf, costs: SellCosts)`. The spec's §3.2 signature has only two.
  - Why:
    - §6 sizes gross from net as `target / (1 − fee − slippage)`, and §4 gates on fee drag. A rule is built from params only and cannot read `config` or the repo.
    - `SellCosts` carries the fallback rate and per-product slippage the caller resolved. The venue's previewed fee is then recorded by `executor.reduce`, and it is the fact the proposal stores.
  - Cost if wrong: one signature change across three rule classes.
- **R7.** The value types (`Lot`, `Holding`, `SellCosts`, `Reduction`) live in a new pure module, `keel/strategy/reduction.py`. `base.py` imports them for the hook's signature. The repo adapter `holding_of` lives in `keel/execution/sleeve.py`.
  - Why:
    - Rules and the sim must build a `Holding` without a repository.
    - `base.py` is the contract module every rule imports, and `keel.data` does not belong in it.
    - The sim builds `Holding.from_lots(...)` from its own lots, so live and sim size from one definition.
  - Cost if wrong: an import move.
- **R8.** `holding_of` does not filter by `rule_name` (spec §3.3). `Lot.entry_fee_share` prorates the entry fee by `qty / (qty + realized_qty)` (spec §3.3, Q4).
  - Why: a sleeve rule governs the product's holding, not its latest entrant.
  - Cost if wrong: a `Reduction` on PAXG reaches the turtle tranche. That is Q10's stated default, and it is booked honestly per leg (R9).
- **R9.** `book_exit(is_dca=None)` derives each leg's flag from `position["rule_name"] == "dca"`. `True` and `False` keep today's single-flag behaviour exactly, so `_handle_exits`, `_book_paper_exit` and `scale_out` are untouched (spec §3.2, #860).
  - Why: this is the one change spec §11 PR 2 marks required.
  - Cost if wrong: none to existing callers. They pass a bool and take the unchanged branch.
- **R10.** `promotion.SLEEVE_SELL = "sleeve_sell"` joins a new `promotion.RECOGNISED_CLASSES` frozenset, which the conformance test reads instead of `{DEFAULT_CLASS} | set(_CLASS_FLOORS)`. `floor_for_class` never receives it, because `attempt_promotion` routes a sleeve-sell rule to its own gate first (P12).
  - Why: `test_promotion_class_is_a_recognised_value` exists to stop a class silently falling back to the default floor. A sleeve-sell rule must not fall back to it either.
  - Cost if wrong: a conformance test edit.

**Pipeline**

- **R11.** Flooring to `base_increment` and capping at `max_per_order_usd` happen in the pipeline, in `sleeve.slice_qty`, not in each rule. The spec puts them in the rule for §4 and §6.
  - Why: a rule does not know the venue increment or the config cap. One shared slicer makes rail 2's slicing identical for all kinds and in the sim.
  - Cost if wrong: none. The rule's `qty` is an upper bound, and the slicer only lowers it.
- **R12.** `min_hold_days` (default 30) applies to the tranches the FIFO sale would **consume**, not to the newest tranche in the product.
  - Why:
    - Under the live weekly BTC DCA the newest tranche is always under 7 days old. The spec's wording, "after the newest tranche", would block every `reverse_dca` and `profit_take` proposal on BTC forever.
    - The spec's stated purpose is "a sale never realises a tranche bought this week", which is about the consumed tranches.
  - Cost if wrong: a proposal can appear while DCA is active. In this plan it only records.
- **R13.** `sleeve_exit` is exempt from `min_hold_days`. The same-day-DCA exclusion and the one-per-day cap still apply.
  - Why: a whole-sleeve sale consumes this week's tranche by definition. A structural exit that waits for a 30-day buy-free window never fires on a product under weekly DCA.
  - Cost if wrong: an exit proposal a day after a buy. That is preview only, and the round trip is spelled out in the proposal.
- **R14.** "One sleeve SELL per product per UTC day" is implemented as "one `sell_proposals` row per product per UTC day, whatever its decision, `superseded` rows excluded". A second run on the same day writes nothing and logs `sleeve.already_proposed_today`.
  - Why: the LaunchAgent retries hourly after a non-zero exit, and a proposal per run would spam the operator (Review Focus 2).
  - Cost if wrong: a vetoed proposal (for example on a stale feed) is not retried later that day. The spec already accepts "not carried forward" (§6d).
- **R15.** A `cooldown_days` param on a rule (`profit_take`, default 30) is enforced by the pipeline, by reading the rule's last non-superseded proposal. The rule itself stays pure.
  - Why: a rule has no state, and the proposals table is the state.
  - Cost if wrong: none beyond R14's.
- **R16.** In a live-mode cycle, `_handle_reductions` evaluates `sleeve_sell` rules at status `paper` **and** `live`. Both are preview-only here. A `paper` status row is hard-coded to preview whatever P18 adds, and its proposals record `rule_status='paper'`. Their products join the cycle's poll set.
  - Why:
    - A live-mode cycle evaluates only `live` rules. So the spec's §3.7 paper→live reading, "60 paper days with a reviewed proposal", could never be satisfied in the database where the rule is promoted.
    - For a rule that can only propose, "paper" can honestly mean "proposals against the real book, never placed".
  - Cost if wrong: one extra rule query per cycle. A paper sell rule can place nothing (tested in P8 and P18).
- **R17.** `executor.reduce` in preview runs, in order:
  - the same `_clamped_sell_qty`;
  - `guards.check`;
  - `_order_spec`;
  - `broker.preview_order`.

  It **never** runs `_clear_resting_bracket` (which cancels at the exchange), `insert_order` or `place_order`. A preview exception is caught and recorded, never raised.
  - Why: preview-only must not touch the venue's order book at all. A cycle must not die on a proposal.
  - Cost if wrong: the proposal carries a fallback fee instead of the venue's.
- **R18.** The paper-mode cycle runs `_handle_reductions` with `offline=True`: `guards.check(..., offline=True)`, no broker, and the fallback fee.
  - Why: this mirrors `_paper_enter`. Paper has no venue.
  - Cost if wrong: paper proposals carry the fallback fee.
- **R19.** Sleeve notifications travel on `LoopResult` (`reduce_results`, `exit_watch_transitions`), and `notifications.events_from_state` derives them. The notification layer still writes exactly one key.
  - Why: `tests/test_notifications.py` pins the write list to `[NOTIFIED_WINDOWS_KEY]`, and `setup.unplaced` already rides `LoopResult` the same way.
  - Cost if wrong: none.

**Rule kinds**

- **R20.** Sell-side kinds are registered in `agent.RULE_REGISTRY`, so `_build_rule` reconstructs them. They are excluded from `rules seed` and the first-run wizard through a new `agent.seedable_kinds()`.
  - Why: `seed_rules_into` builds every kind from `{"product_id": p}` alone. `reverse_dca` has required params (`target_usd`, `min_price_floor`), and a seeded seller with invented defaults is exactly what no one should get by accident.
  - Cost if wrong: an operator wanting a seeded sell rule types `rules add`, which they must do anyway.
- **R21.** The lookahead gate for a sleeve-sell rule adapts `reduce_signal` into `bias.lookahead_analysis`'s `Setup`-returning shape:
  - `entry=reduction.expected_price`, `stop=0`, `target=reduction.qty`;
  - a fixed synthetic `Holding` (one lot, 1 unit, entry at the first close);
  - `SellCosts(0.012, 0.0005, ...)`.
  - Why: the harness diffs `entry`, `stop` and `target` with a tolerance. Wrapping it keeps one harness. A deliberately peeking test rule proves the adapter is not vacuous (P12 Task 2).
  - Cost if wrong: a lookahead check weaker than the entry rules'. That is mitigated by the peeking-rule test.
- **R22.** Placement (P18) exists only as `keel dca {distribute,trim,exit} --confirm <proposal-id>`. It requires, in order:
  - an armed sells window (`keel autonomy on --sells`, P17);
  - a TTY;
  - a typed `yes` through one gate function, `keel.commands.dca.sleeve_sale_gate`, with one capability row.

  `execution: auto` is **not** built.
  - Why: the operator's invariant is stricter than spec §3.5. §3.5's `--confirm` path does not require the sells window, and the invariant says no new sell reaches the venue without it. So both are required.
  - Cost if wrong: an operator who wanted a one-off confirmed sale without arming the window must run `keel autonomy on --sells --for-hours 1` first. That is one command.
- **R23.** `keel dca proposals review <id>` writes `reviewed_ts` only at a TTY, after a `[y/N]` prompt, and is **not** a capability row.
  - Why: it releases no order. It is an input to a promotion that itself places nothing in this plan (live = preview). This matches `keel dca plan`'s posture: "the terminal is load-bearing, the heavier gate is not".
  - Cost if wrong: if a later build lets `live` place, this becomes a capability input and needs a row. P18's self-review re-checks it.
- **R24.** The CLI `keel dca trim --preview` ships once, in P13 and P14, with all three views. The spec's interim state, where only `--view lots` is accepted and everything else is a usage error, is dropped.
  - Why: the operator's ordering puts trims after `reverse_dca`. The lots view has no consumer before then.
  - Cost if wrong: the lots report arrives about 8 PRs later than spec §11's order. It is a report either way.
- **R25.** The CLI previews (`trim`, `distribute`, `exit`) price fees at the fallback rate, labelled as such, and call no broker. The per-cycle proposals carry the venue preview.
  - Why: `keel dca plan`'s precedent is that a read-only command opens `_open_repo_ro` off a TTY and never builds a broker.
  - Cost if wrong: the report overstates fee drag by about 0.3% of notional.
- **R26.** The exit monitor's default levels are module constants in `keel/execution/sleeve_exit.py`:
  - `dd_pct=35`, `lookback_days=200`, `sma_period=200`, `confirm_days=3`, `warn_pct=5`.

  A `sleeve_exit` rule's params override them per product. There is no new config key.
  - Why: the spec gives these values (§7). A config key is a second place for them to disagree.
  - Cost if wrong: a config key added later.
- **R27.** The monitor polls `ONE_DAY` candles for every product holding an unbracketed open tranche outside the cycle's rule products. This is a second `market_feed.poll_once` call, and it does **not** touch `last_feed_ts`. PAXG tranche 3 is therefore watched (Q7).
  - Why: the live cycle polls only live-rule products (`agent.feed_polled products=['BTC-USD']`, #811). The spec's §7 monitor assumes PAXG candles that are never fetched.
  - Cost if wrong: one extra candle request per cycle per watched product. If it fails, the level reads `insufficient_history`.
- **R28.** P17 and P18, the gated placement path, are the last group, and each can be merged on its own. The operator may stop after P16 with every preview surface complete.
  - Why: the operator's step 3 says preview "before any live placement is enabled". The task allows the gated path, provided it is off by default.
  - Cost if wrong: none. Nothing earlier depends on them.

- **R29.** `keel rules backtest`'s proposal replay prints its headline at `config.fees.taker_pct`. It prints a sensitivity row only when the operator passes `--fee-sensitivity-pct` (no default). A test asserts that `\b0\.009\b` appears nowhere in `keel/`.
  - Why: spec §3.7 asks for "1.2% and 0.9%" rows, and spec §2.2 and Q5 say "0.9% is never hardcoded". No constant for the measured live rate exists in `keel/` today. This reconciles the two without inventing one.
  - Cost if wrong: the operator types one flag to get the second row.
- **R30.** `keel positions close` creates a new `positions` click group. **No `keel positions` CLI command exists today.** The positions report is the web view only (`keel/web/api.py` over `commands/positions.gather_positions`). No `list` subcommand is added.
  - Why: #798 names `keel positions close`, and #799 says "`keel positions` cannot see it". Both assume a CLI that is not there.
  - Cost if wrong: an operator expecting `keel positions` to print the report finds only `close`. The doctor fix lines point at the console's Positions view.
- **R31.** In a cycle, `sleeve_sell` rules are excluded from `rules`, the list that feeds the entry pre-pass, `engine.evaluate` and `_handle_exits`. They are loaded separately (P8 Task 8.1).
  - Why: the pre-pass withholds **every** entry for the whole cycle when any rule's bar is not ready (`entries_withheld`). A sell rule on a product with a lagging feed would block the BTC DCA buy.
  - Cost if wrong: none. A sleeve rule neither enters nor exits.
- **R32.** The account sim (`portfolio_sim`) books a distribution against its one averaged DCA lot per asset. The per-rule accumulation row (`report.accumulation_table`) is the FIFO-faithful one, and it carries the pinned hand computation.
  - Why: `SimAccount.dca_positions` is one lot per asset by design (#85). Re-plumbing it to FIFO lots is out of proportion to a fidelity test.
  - Cost if wrong: the account sim's realised P&L on distributions is average-cost. Its docstring says so.
- **R33** (added in review round 1, #882). `guards._open_exposure_by_asset` (Task 4.0) contributes zero notional for any PRODUCT whose net filled quantity is `<= 0`, instead of its net notional, regardless of the prices the closing legs traded at.
  - Why: the function nets BUY/SELL **notional**, which is the right figure for a position still partly held. It is the wrong figure for one closed in full at a different price than its entry: PAXG tranche 3 closed at a real loss (4673.23 -> 4400) leaves a $3.61 notional residual that is not exposure, because zero units are held. Left unfixed, P4's own acceptance test cannot pass and #798's phantom exposure survives any loss-making close, declared or ordinary.
  - Cost if wrong: exposure rails 4/5/6 could under- or over-state a closed product's contribution to its asset bucket. The fix only changes behaviour when a product's net qty is `<= 0`; every still-open product nets by notional exactly as before, pinned by `test_a_partial_close_still_nets_by_notional`.
- **R34** (added in review round 1, #883; refined in rounds 2, 3 and 4 (#887); refined again, #890). `executor.reduce`'s confirm branch (Task 18.1) asks the typed-yes gate BEFORE it cancels the resting protective bracket, by wrapping the caller's `confirm_fn` rather than calling `_clear_resting_bracket` directly ahead of `_run_order`. A leg that does not close the position in full re-brackets the remainder afterward, via `place_bracket`, the same choreography `scale_out` (#502) uses -- including repointing the surviving tranche's `bracket_order_id` at the new bracket, the step round 2's fix omitted, and, unlike `scale_out`, sized from the ledger's own post-`book_exit` qty rather than the pre-sale `remainder`, since this path's booking can differ from what was ordered (a short fill) in a way `scale_out`'s never does. The crash-ledger `unbracketed:<product>` record that protects a mid-flight cancel/re-bracket is written INSIDE that same wrapped closure, after `confirm_fn` returns true and before the cancel it guards -- not earlier -- so a decline writes nothing there to leave stale; it is now written whenever `open_stop`/`open_target` exist, not only when the leg is partial, so a full close is covered too. A failure after the cancel is labelled by WHO refused: `declined` only when the operator said no before the exchange was touched; `failed` when the cancel itself failed or the venue rejected the SELL after a genuine yes. On the success path, what happens next is decided from `repo.get_open_positions`, not from the pre-sale `protecting_remainder`/`remainder` arithmetic: a true full close clears `position_rule:`/`open_stop:`/`open_target:`/`unbracketed:` together (round 4), a bracketed "full close" the venue fills short rewrites the retry record from the ledger and logs CRITICAL instead of silently clearing it (round 4), and an ordinary partial sale of a tranche that was never bracketed logs neither (#890 -- the short-fill arm is now keyed on `has_levels`, not on `not protecting_remainder` alone, so it no longer also catches every unbracketed DCA sale).
  - Why: the original draft cancelled the bracket unconditionally before `_run_order` (and therefore before the human's answer, which `_run_order` asks internally). A decline, or no TTY, left a stopped tranche naked at the exchange with no `unbracketed:` record -- the cancel had already happened and nothing said so. `execute()`'s own EXIT path has the same ordering today (pre-existing, out of scope here); `reduce()` is new code and need not repeat it. Round 1's fix reordered the cancel correctly but still wrote the crash-ledger record unconditionally, before the confirm gate ran -- round 2 found the three `test_883_*` tests could not pass as drafted: the record was never cleared on a decline, the fixture never seeded a filled BUY order for `_held_position` to find (so `protecting_remainder` was always false), and the placement counts omitted the SELL leg's own `_run_order` call. All three are fixed in Task 18.1: the record moves inside `_confirm_then_cancel`, `_bracketed` seeds a matching filled BUY order, and the two placement tests count 2 and 3 calls, not 1 and 2. Round 3 found two further gaps, both in Task 18.1: (a) the successful-partial-leg path never called `repo.set_position_bracket`, so a filled remainder bracket had no tranche pointing at it and `reconcile.exit_without_position_context` fired on every fill, WARNing `position.unprotected` every cycle after; (b) gating the crash-ledger write on `protecting_remainder` (partial only) meant a full close whose SELL the venue rejected after the cancel left the position naked with NO retry record at all, and the result was mislabelled "declined at the confirm gate" even though the operator said yes and the venue, not the operator, refused. Both are fixed: the record now writes whenever levels exist (and a fully successful full close clears it explicitly, since `place_bracket` -- its only other clearer -- is never called on that path), and the `reduce` result's `decision` distinguishes `declined` (operator said no, or the window/rule-status gate refused first) from `failed` (the cancel or the placement itself failed after a genuine yes). Round 4 (#887) found the success path's own clear was too narrow twice over: it cleared only `unbracketed:` on a full close, leaving `position_rule:`/`open_stop:`/`open_target:` stale for the next entry to trip over (rail 9's `no_stop_widening`, `guards.py:657-664`); and it decided "full close" from the pre-sale remainder rather than from what `book_exit` actually left in the `positions` ledger, so a full-close SELL the venue filled short (#446) cleared the retry record for a remainder that was, in fact, still naked and unrecorded. Both are fixed by re-checking `repo.get_open_positions` after `book_exit`, mirroring `agent._handle_exits`'s own tail (`agent.py:950-987`) instead of trusting the pre-sale prediction.
  - Cost if wrong: a declined or failed confirm now leaves the bracket resting rather than cancelled, which is the safe direction; a partial confirmed sale is followed by a second `place_bracket` call the venue must accept, the same call `scale_out` already makes routinely. Had round 2's three issues gone unfixed, the tests meant to prove R34 would themselves not pass, so the ruling would be undemonstrated rather than wrong. Had round 3's gaps gone unfixed: an orphaned tranche pointer would have kept warning `position.unprotected` every cycle after every partial confirmed sale, forever, with no crash involved at all; and a rejected full-close SELL would have left a live, undetected naked position, invisible to both the sweep (no record to read) and the operator (a label that reads as their own decision, not a fault). Had round 4's gaps gone unfixed: every successful confirmed full close of a bracketed position would have left `open_stop:` behind, so rail 9 would veto the next entry on that product whose stop sat below the closed trade's; and a full-close SELL the venue filled short would have had its retry record cleared by the narrow success-path clear, leaving the remainder naked and unrecorded -- the exact silence `agent._handle_exits` already guards against on its own EXIT path.
- **R35** (added in the #887 follow-up; extended, #890). `keel dca {distribute,trim,exit} --confirm <proposal-id>` (P18, Task 18.2) exits **0 only when a sale is actually placed** (`ReduceResult.decision == "placed"`, reached through `reduce`). Every other outcome exits **1** by raising `click.ClickException`, whether or not the call ever reaches `reduce`: a `declined` or `failed` `ReduceResult.decision` (the operator's no, no TTY, a closed sells window, a `paper` rule, the cancel of the resting bracket failing, or the venue rejecting the SELL after a genuine yes) via `f"{result.decision}: {result.reason}"`, and a refusal `_confirm_sale` raises BEFORE `reduce` is ever called -- the proposal no longer firing on the current book, a rule/verb kind mismatch, or a `paper` rule refused up front -- via its own message. No new exit code is minted anywhere on this path, and every non-zero outcome is told apart from another only by the message, never by the status.
  - Why: exit 1 through `click.ClickException` is how every typed-confirmation refusal and every venue failure in `keel/commands` already ends -- `_common._require_interactive_confirmation` ("aborted (confirmation not given).", the very gate `sleeve_sale_gate` wraps), `orders cancel`'s "aborted." and its stranded-bracket error, and `subscription`/`rules`/`versions`' `ctx.exit(1)`. Every code above 1 in `keel/` serves one NAMED machine consumer, never a human reading a message: `install.py`'s `ctx.exit(2 if plan.needs_confirmation else 0)` (`install.py:362`) is read by a scripted installer deciding whether to re-run with `--yes`; `cli.py`'s `sys.exit(141)` (`cli.py:1706`) is the SIGPIPE convention for a reader that hung up; `agent.DATA_NOT_READY_EXIT = 4` and `agent.MARKET_CLOCK_UNAVAILABLE_EXIT = 5` exist for the day-stamping launchd wrapper that must tell "retry in an hour" from "done". `--confirm` has none of these: no installer scripts it, no wrapper retries it, and its reader is a human at a TTY who already gets the decision word in the message. A distinct code for `failed`, or for a refusal before `reduce`, would be a contract nobody reads. The state that matters after a `failed` -- a cancelled bracket, a naked position -- is carried where it is acted on: the `unbracketed:` retry record the sweep heals from, the CRITICAL log, `doctor`'s `position.unprotected`, and the message itself, which names the step that failed. `keel dca plan`'s `[N]` exits 0 ("cancelled: nothing written."), but that is a menu choice among offered actions, not the typed-yes gate refusing; `--confirm` reuses the gate, so it follows the gate, whether the refusal comes from the gate itself or from `_confirm_sale`'s own pre-`reduce` checks.
  - Cost if wrong: a future caller that wants to branch on decline versus failure versus a pre-`reduce` refusal by status alone cannot, and must read the message. Adding a distinct code for any of them later is additive -- nothing that tests `!= 0` breaks. The opposite mistake (exit 0 on a decline, or on a proposal that no longer fires) would make a refusal read as success to anything checking the status, which is why it is not taken.

---

## File structure

| File | New? | PR | Responsibility |
|---|---|---|---|
| `keel/execution/executor.py` | modify | P1, P7, P18 | `place_bracket` downgrade (P1); `ReduceResult`, `reduce` (preview in P7, placement branch in P18) |
| `keel/commands/doctor.py` | modify | P2, P3, P10, P15 | `position_watch_findings`, `ledger_drift_findings`, `venue_drift_findings`, `sleeve_rule_findings`, `exit_watch_findings`; wiring in `gather_findings` |
| `keel/execution/reconcile.py` | modify | P3 | `VENUE_HOLDING_PREFIX`, `record_venue_holdings` |
| `keel/agent.py` | modify | P3, P8, P9, P15 | cycle calls; `_handle_reductions`; `seedable_kinds`; `_watch_sleeve_exits`; `LoopResult` fields |
| `keel/execution/sleeve.py` | **new** | P3 → P18 | ledger-side sleeve logic: `ledger_qty` (P3), `holding_of` (P5), costs/caps/slicing/`record_proposal` (P7), `sells_released` (P18) |
| `keel/commands/positions_close.py` | **new** | P4 | `keel positions close <id>`: service + gated command |
| `keel/capabilities.py` | modify | P4, P17, P18 | rows for `positions close`, `autonomy on --sells`, `sleeve_sale_gate` |
| `keel/strategy/reduction.py` | **new** | P5 | `Lot`, `Holding`, `SellCosts`, `Reduction` (pure values) |
| `keel/strategy/rules/base.py` | modify | P5 | `Action.REDUCE`, `Rule.reduce_signal` |
| `keel/strategy/promotion.py` | modify | P5, P12 | `SLEEVE_SELL`, `RECOGNISED_CLASSES`; `sleeve_sell_gate` |
| `keel/execution/streak.py` | modify | P5 | `book_exit(is_dca: bool \| None)` |
| `keel/data/db.py` | modify | P6, P17 | v22 `sell_proposals`; v23 `profile.sells_until` |
| `keel/data/repository.py` | modify | P6, P17 | proposal CRUD; `set_sells_window`; `get_profile` reads `sells_until` |
| `keel/data/audit.py` | modify | P6, P17 | `EVENT_STORES` entries |
| `keel/commands/dca.py` | modify | P6, P10, P12, P13, P14, P15, P18 | `proposals`, `distribute`, `trim`, `exit` subcommands; `sleeve_sale_gate` |
| `keel/commands/sleeve_report.py` | **new** | P6 → P15 | pure report builders and renderers: `render_proposal` (P6), `distribution_rows` (P10), `proposal_replay` (P12), `lots_view`/`bands_view` (P13), `gain_view` (P14), `render_exit_watch` (P15) |
| `keel/strategy/rules/reverse_dca.py` | **new** | P9 | `ReverseDca` |
| `keel/strategy/rules/profit_take.py` | **new** | P14 | `ProfitTake` |
| `keel/strategy/rules/sleeve_exit.py` | **new** | P16 | `SleeveExit` |
| `keel/execution/sleeve_exit.py` | **new** | P15 | pure exit monitor: `ExitWatch`, `classify` |
| `keel/commands/rules.py` | modify | P9, P12 | seed via `seedable_kinds`; sleeve-sell promotion route, replay, `--allow-concurrent-dca` |
| `keel/commands/setup.py` | modify | P9 | seed via `seedable_kinds` |
| `keel/sim/portfolio_sim.py`, `keel/sim/report.py` | modify | P11 | reverse path; `DcaSleeve` sell columns |
| `keel/notifications.py`, `packages/keel-core/keel_core/notifications.py` | modify | P8, P15 | `sleeve.proposal`, `sleeve.exit_watch` |
| `keel/commands/autonomy.py`, `packages/keel-core/keel_core/types.py` | modify | P17 | `--sells`; `Profile.sells_until`, `is_autonomous_for_sells` |
| `tests/execution/test_sell_side_invariants.py` | **new** | P1, extended by every PR | S1–S4 |

---

## PR sequence (merge order)

| # | Title | Closes | Size | Schema |
|---|---|---|---|---|
| P1 | `fix(execution): a bracket leg that throws after a filled entry downgrades to open-unbracketed (#799)` | refs #799 | S | none |
| P2 | `feat(doctor): position.unmanaged and position.unprotected -- a ledger-driven watch that a cleared retry key cannot silence (#811)` | #811 | M | none |
| P3 | `feat(doctor): ledger.drift and ledger.venue_drift, fed by a per-cycle venue-holdings record (#799, #798)` | #799 | M | none |
| P4 | `feat(positions): keel positions close <id> -- an operator-declared exit the rails can read (#798)` | #798 | M | none |
| P5 | `feat(strategy): sell-side contracts -- Holding, Reduction, Action.REDUCE, reduce_signal, book_exit(is_dca=None) (#857)` | — | M | none |
| P6 | `feat(data): sell_proposals, schema v22, and keel dca proposals list/show (#857)` | — | M | **v22** |
| P7 | `feat(execution): executor.reduce in preview -- rails, venue preview, sleeve caps, rail-2 slicing (#857)` | — | M | none |
| P8 | `feat(agent): _handle_reductions -- arbitration, one proposal per product per day, sleeve.proposal notifications (#857)` | — | M | none |
| P9 | `feat(rules): reverse_dca, preview-only (#857)` | — | M | none |
| P10 | `feat(dca): keel dca distribute --preview and the reverse_dca doctor findings (#857)` | — | S | none |
| P11 | `feat(sim): the reverse path -- distributions in the account sim and the accumulation row (#857)` | — | M | none |
| P12 | `feat(promotion): the sleeve_sell gate, proposal replay, proposals review, --allow-concurrent-dca (#857)` | — | M | none |
| P13 | `feat(dca): keel dca trim --preview --view lots and --view bands (#857)` | — | M | none |
| P14 | `feat(rules): profit_take in preview, and --view gain as trim's default (#857)` | — | M | none |
| P15 | `feat(execution): the sleeve exit monitor -- transition alerts, doctor, keel dca exit --preview (#857)` | — | M | none |
| P16 | `feat(rules): sleeve_exit, preview-only (#857)` | #857 | S | none |
| P17 | `feat(autonomy): keel autonomy on --sells -- a separate, TTY-armed sells window, schema v23 (#857)` | — | M | **v23** |
| P18 | `feat(dca): --confirm <proposal-id> places one sleeve sale behind the sells window and a typed yes (#857)` | — | M | none |

No PR is L. Spec §11's PR 1 was split across P3 and P13, its PR 2 across P5–P8, its PR 3 across P9–P11, and its PR 4 across P12, P17 and P18. The only PRs that depend on something other than their predecessor are P13, which needs P5 but not P7–P12, and P15, which needs P2 and P3 (spec §7's prerequisites) plus P8. They can be reviewed in parallel. They merge in the order above.

---

# Group A — Prerequisites

## P1 — `fix(execution): a bracket leg that throws after a filled entry downgrades to open-unbracketed (#799)` · S · no schema

**PR body states:** S1–S4. P1 creates the invariants module, adds no sell path, and changes no rail. Refs #799. It does not close it: proposal 3 lands in P3 (R1).

### Task 1.1: The sell-side invariants module (S1, S4)

**Files:**
- Create: `tests/execution/test_sell_side_invariants.py`

**Interfaces:**
- Produces:
  - `PLACEMENT_CALLERS: set[tuple[str, str]]` and `RUN_ORDER_CALLERS: set[tuple[str, str]]`, which later PRs edit only under a ruling;
  - `SECOND_LEVEL_CALLERS: set[tuple[str, str]]`, the pinned set of functions that call `executor.execute`/`executor.scale_out`/`executor.place_bracket`/`executor.roll_stop_to`/`executor.roll_to_break_even`/`executor.trail_stop_atr` (the names a caller outside `executor.py` actually spells to reach `_run_order` through `RUN_ORDER_CALLERS`'s four functions — `_roll_stop` itself is private and reached only through its three wrapper functions). A new CLI sell path that calls `executor.execute(EXIT)` or `executor.scale_out(...)` without going through the sells gate fails here, one level before it would need to fail at `RUN_ORDER_CALLERS`. Grown only under a ruling, same as `RUN_ORDER_CALLERS`;
  - `WEB_FORBIDDEN_NAMES: frozenset[str]`, which P4, P6, P7 and P17 grow;
  - the helpers `_functions_calling(names, roots)` and `_functions_calling_attr(base, names, roots)`, the second scoped to a specific attribute base (`executor.<name>`) so a same-named method on a different object — `conn.execute`, most of all — cannot collide.

- [ ] **Step 1: Write the tests.**

```python
"""The sell-side build's safety invariants, S1-S4 (docs/superpowers/plans/2026-09-28-dca-sleeve-
sell-side-build.md). Every PR in that plan runs this module.

S1 is enforced by an INVENTORY, the way `tests/test_capabilities.py` inventories gate call sites:
the set of functions that can reach the venue's order endpoint is pinned. A new caller fails here
until a ruling declares it. That is how "no new sell reaches the venue unless the sells window
is armed" stays a checked fact rather than a promise: `executor.reduce` cannot join
`RUN_ORDER_CALLERS` until P18, and P18's own test asserts the call sits behind
`sleeve.sells_released`.

`RUN_ORDER_CALLERS` alone only pins the functions that call `_run_order` directly, all four of
which live inside `executor.py` itself. A new sell path added anywhere ELSE in the codebase --
say a CLI command in `keel/commands/dca.py` that calls `executor.execute(EXIT)` or
`executor.scale_out(...)` straight from its handler -- never appears there and this module stays
green. `SECOND_LEVEL_CALLERS` closes that gap: it pins every function, anywhere under `keel`,
that calls one of `executor`'s order-reaching entry points by name (`executor.<name>`), so a new
second-level caller turns this module red the same way a new first-level one does.
"""

from __future__ import annotations

import ast
import glob
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Only `_run_order` may call `broker.place_order`.
PLACEMENT_CALLERS = {("keel.execution.executor", "_run_order")}

#: Every function that may hand an order to `_run_order`. Each is a pre-existing path:
#: the rule ENTER/EXIT (`execute`), the protective bracket, the #502 scale-out, and the
#: ratchet roll.
RUN_ORDER_CALLERS = {
    ("keel.execution.executor", "execute"),
    ("keel.execution.executor", "place_bracket"),
    ("keel.execution.executor", "scale_out"),
    ("keel.execution.executor", "_roll_stop"),
}

#: The names a caller OUTSIDE `executor.py` spells, as `executor.<name>`, to reach one of
#: `RUN_ORDER_CALLERS`'s four functions. `execute`, `place_bracket` and `scale_out` are the
#: names directly; `_roll_stop` is private and has no external caller today -- it is reached
#: only through its three wrapper functions (`roll_stop_to`, `roll_to_break_even`,
#: `trail_stop_atr`), so those three stand in for it here.
SECOND_LEVEL_NAMES = {
    "execute",
    "place_bracket",
    "scale_out",
    "roll_stop_to",
    "roll_to_break_even",
    "trail_stop_atr",
}

#: Every function, anywhere under `keel`, that calls `executor.<name>` for a name in
#: `SECOND_LEVEL_NAMES`. Today: the rule ENTER/EXIT dispatch (`run_once`, `_handle_exits`), the
#: stop-management loop (`_manage_stops`, via `roll_stop_to`), and the two reconcile sweeps that
#: re-place a missing bracket (via `place_bracket`). `scale_out`'s wrapper names have no external
#: caller yet -- the #502 CLI path is not built -- so none appears below for them.
SECOND_LEVEL_CALLERS = {
    ("keel.agent", "_handle_exits"),
    ("keel.agent", "_manage_stops"),
    ("keel.agent", "run_once"),
    ("keel.execution.reconcile", "reconcile_unbracketed_positions"),
    ("keel.execution.reconcile", "_rebracket_or_escalate"),
}

#: Operations the browser and the MCP server must never name (S4). Grown by the PR that
#: introduces each one.
WEB_FORBIDDEN_NAMES: frozenset[str] = frozenset()


def _module_of(path: str) -> str:
    rel = os.path.relpath(path, _ROOT)
    return rel[: -len(".py")].replace(os.sep, ".").removesuffix(".__init__")


def _calls_in(tree: ast.AST, module: str, names: set[str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = (
                callee.id
                if isinstance(callee, ast.Name)
                else callee.attr
                if isinstance(callee, ast.Attribute)
                else None
            )
            if name in names:
                found.add((module, func.name))
    return found


def _functions_calling(names: set[str], roots: tuple[str, ...] = ("keel",)) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                found |= _calls_in(ast.parse(fh.read()), _module_of(path), names)
    return found


def _calls_via_attr(tree: ast.AST, module: str, base: str, names: set[str]) -> set[tuple[str, str]]:
    """Like `_calls_in`, but only counts `<base>.<name>(...)` -- an attribute call whose object is
    literally the name `base`. This is what keeps `executor.execute` from being confused with
    `conn.execute`, `repo.execute` or any other `.execute(...)` in the tree: `_calls_in` matches on
    the bare method name alone and would treat all of them as the same caller.
    """
    found: set[tuple[str, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in names
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == base
            ):
                found.add((module, func.name))
    return found


def _functions_calling_attr(
    base: str, names: set[str], roots: tuple[str, ...] = ("keel",)
) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                found |= _calls_via_attr(ast.parse(fh.read()), _module_of(path), base, names)
    return found


def test_only_run_order_calls_place_order() -> None:
    assert _functions_calling({"place_order"}) == PLACEMENT_CALLERS


def test_the_run_order_callers_are_exactly_the_pinned_set() -> None:
    assert _functions_calling({"_run_order"}) == RUN_ORDER_CALLERS


def test_the_second_level_callers_are_exactly_the_pinned_set() -> None:
    assert _functions_calling_attr("executor", SECOND_LEVEL_NAMES) == SECOND_LEVEL_CALLERS


def test_the_scan_is_false_capable() -> None:
    tree = ast.parse("def sneaky():\n    executor._run_order(1)\n")
    assert _calls_in(tree, "m", {"_run_order"}) == {("m", "sneaky")}


def test_the_attr_scan_does_not_confuse_executor_execute_with_conn_execute() -> None:
    tree = ast.parse(
        "def sneaky():\n"
        "    conn.execute('SELECT 1')\n"
        "def honest():\n"
        "    executor.execute(1)\n"
    )
    assert _calls_via_attr(tree, "m", "executor", {"execute"}) == {("m", "honest")}


def test_the_browser_and_mcp_name_no_sell_side_operation() -> None:
    offenders: list[str] = []
    for root in ("keel/web", "keel/mcp"):
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                name = (
                    node.attr
                    if isinstance(node, ast.Attribute)
                    else node.id
                    if isinstance(node, ast.Name)
                    else None
                )
                if name in WEB_FORBIDDEN_NAMES:
                    offenders.append(f"{_module_of(path)}: {name}")
                # `executor.reduce` specifically -- a bare `reduce` is `functools.reduce`.
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "reduce"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "executor"
                ):
                    offenders.append(f"{_module_of(path)}: executor.reduce")
    assert offenders == []
```

- [ ] **Step 2: Run the tests.** Run: `uv run pytest tests/execution/test_sell_side_invariants.py -v`. Expected: 6 PASS.
  - These tests pin the present state; the red phase is `test_the_scan_is_false_capable` and `test_the_attr_scan_does_not_confuse_executor_execute_with_conn_execute`, which prove each scanner can fail.
  - If any set comparison fails because the scan found an extra caller, **stop**. Read that caller and record a ruling before widening the set.

- [ ] **Step 3: Commit.**

```bash
git add tests/execution/test_sell_side_invariants.py
git commit -m "test(execution): pin the venue-placement call sites -- S1/S4 of the sell-side build

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 1.2: A bracket leg that raises never costs a filled entry its tranche (R5)

**Files:**
- Modify: `keel/execution/executor.py`, `place_bracket` (currently lines 2268–2394)
- Test: `tests/execution/test_bracket_downgrade.py` (new), `tests/test_agent.py` (one test)

**Interfaces:**
- Produces: `place_bracket(...) -> int | None` never raises for a bracket-leg failure. `ExecutionResult.placed` stays `True` for the filled entry, with `bracket_order_id=None`.
- Produces: `executor.BRACKET_STATE_UNKNOWN_EVENT = "executor.bracket_state_unknown"`.

- [ ] **Step 1: Write the failing executor tests.**

```python
"""#799 proposal 2: a filled entry must never be lost to a later failure (plan R5)."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from keel_broker_api.orders import OrderSpec
from keel_broker_api.results import PlaceResult, Preview

from keel.execution import executor
from keel.types import Side
from tests.execution.test_executor import (  # noqa: F401 -- `repo` is a fixture
    NOW_TS,
    FakeBroker,
    _config,
    _enter_signal,
    repo,
)


class _SellPreviewRaises(FakeBroker):
    """The August incident: the ENTRY previews and fills, the protective SELL's preview throws
    `decimal.InvalidOperation` out of a degenerate venue field."""

    def preview_order(self, spec: OrderSpec) -> Preview:
        if spec.side is Side.SELL:
            self.preview_calls.append({"spec": spec})
            raise InvalidOperation("[<class 'decimal.ConversionSyntax'>]")
        return super().preview_order(spec)


class _SellPlaceRaises(FakeBroker):
    """The other stage: the bracket previews, then `place_order` raises -- the venue may or may
    not have accepted it."""

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        if spec.side is Side.SELL:
            raise TimeoutError("read timed out")
        return super().place_order(spec, idempotency_key=idempotency_key)


def test_a_bracket_preview_that_throws_after_a_filled_entry_downgrades(repo) -> None:  # noqa: F811
    broker = _SellPreviewRaises()

    result = executor.execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

    assert result.placed is True, "the entry FILLED -- reporting it unplaced loses the tranche"
    assert result.bracket_order_id is None
    assert len(broker.place_calls) == 1, "only the entry reached the venue"
    retry = repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")
    assert retry is not None and retry["stop"] == _enter_signal().setup.stop


def test_a_bracket_place_that_throws_downgrades_without_a_retry(repo, caplog) -> None:  # noqa: F811
    broker = _SellPlaceRaises()

    with caplog.at_level(logging.CRITICAL, logger="keel.execution.executor"):
        result = executor.execute(
            _enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS
        )

    assert result.placed is True
    assert result.bracket_order_id is None
    assert repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD") is None, (
        "the venue may hold a resting bracket -- a retry would double-commit the base"
    )
    pending_sells = [
        o for o in repo.get_orders(mode="live", status="pending") if o["side"] == "SELL"
    ]
    assert len(pending_sells) == 1, "the unknown-state row stays, so exits fail closed on it"
    assert any(executor.BRACKET_STATE_UNKNOWN_EVENT in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/execution/test_bracket_downgrade.py -v`. Expected: both FAIL. The `InvalidOperation` and the `TimeoutError` propagate out of `executor.execute`, and `BRACKET_STATE_UNKNOWN_EVENT` does not exist.

- [ ] **Step 3: Implement the downgrade in `place_bracket`.** Replace the bare `result = _run_order(...)` call with the block below, and extend the docstring with the two-stage rule.

```python
#: Logged when a bracket leg failed AFTER its `orders` row was written (plan R5): the venue may or
#: may not be holding it, so nothing re-places it and the row stays `pending` for a human.
BRACKET_STATE_UNKNOWN_EVENT = "executor.bracket_state_unknown"
```

```python
    # #799 proposal 2. The ENTRY has already filled when this runs, so NOTHING raised by the
    # bracket leg may escape: an exception here used to unwind through `execute` and `run_once`
    # past `_open_tranche`, and a funded position was left with no ledger row, no exit and no
    # stop for three weeks. The stage decides the recovery. Before the row exists (preview
    # threw) nothing reached the venue: record the retry exactly like a refusal. After it exists
    # (`place_order` threw) the venue's state is unknown: a retry could double-commit the base,
    # so leave the `pending` row -- `_clear_resting_bracket` fails closed on it -- and say so.
    # `TradeScopeDenied` is included on purpose: `_run_order` has already written the refutation.
    before = {o["id"] for o in repo.get_orders(mode="live", product_id=product_id)}
    try:
        result = _run_order(intent, broker, repo, config, "autonomous", None, now_ts, spec=spec)
    except Exception as exc:  # noqa: BLE001 -- see the comment above
        written = [
            o
            for o in repo.get_orders(mode="live", product_id=product_id)
            if o["id"] not in before and o["side"] == Side.SELL.value
        ]
        if not written:
            repo.set_state(
                f"{UNBRACKETED_PREFIX}{product_id}", {"stop": stop, "target": target, "qty": qty}
            )
            log_event(
                logger,
                logging.WARNING,
                "executor.bracket_not_placed",
                product=product_id,
                reason=f"the bracket leg raised before reaching the venue: {exc!r}",
                vetoed_by=[],
            )
        else:
            log_event(
                logger,
                logging.CRITICAL,
                BRACKET_STATE_UNKNOWN_EVENT,
                product=product_id,
                order_id=written[-1]["id"],
                reason=repr(exc),
                detail=(
                    "the entry filled and its bracket's placement raised after the order row "
                    "was written: the venue may be holding it. Nothing will re-place it; "
                    "exits on this product wait until a human reconciles the row"
                ),
            )
        return None
```

- [ ] **Step 4: Run the tests to see them pass.** Run: `uv run pytest tests/execution/test_bracket_downgrade.py tests/execution/test_price_precision.py tests/execution/test_executor.py -q`. Expected: PASS.

- [ ] **Step 5: Write the failing end-to-end test in `tests/test_agent.py`.** Place it beside the `_AlwaysEnterRule` tests. This is the #799 acceptance: the tranche is in the ledger with its stop.

```python
class _SellPreviewRaisesAgentBroker(FakeBroker):
    def preview_order(self, spec: OrderSpec) -> Preview:
        if spec.side is Side.SELL:
            raise InvalidOperation("[<class 'decimal.ConversionSyntax'>]")
        return super().preview_order(spec)


def test_799_a_throwing_bracket_preview_still_records_the_tranche(repo, monkeypatch):
    rule = _AlwaysEnterRule(PRODUCT)
    _seed_rule(repo, monkeypatch, rule, status="live")
    broker = _SellPreviewRaisesAgentBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]}
    )

    run_once(broker, repo, _config(), now_ts=90_000)

    [tranche] = repo.get_open_positions(PRODUCT)
    assert tranche["initial_stop"] == Decimal("95.00"), "#799: the stop was computed and discarded"
    assert tranche["bracket_order_id"] is None
```

- [ ] **Step 6: Run it.** Run: `uv run pytest tests/test_agent.py -k 799 -v`. Expected: PASS once Step 3 is in. Before Step 3 it fails with `InvalidOperation` out of `run_once`, and you can confirm that with `git stash` on the executor change.

- [ ] **Step 7: Run the full suite, then commit.**

```bash
uv run pytest -q && uv run ruff check keel tests && uv run ruff format --check keel tests && uv run mypy
git add keel/execution/executor.py tests/execution/test_bracket_downgrade.py tests/test_agent.py
git commit -m "fix(execution): a bracket leg that throws after a filled entry downgrades to open-unbracketed (#799)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**Acceptance (P1):**
- A preview-stage throw leaves `placed=True`, a retry record and a tranche with `initial_stop`.
- A place-stage throw leaves `placed=True`, no retry, the pending row and a CRITICAL event.
- The invariants module is green.

---

## P2 — `feat(doctor): position.unmanaged and position.unprotected (#811)` · M · no schema

**PR body states:** S1–S4 are unchanged; a doctor finding places nothing. Closes #811.

**The #811 acceptance line that is replaced:** "run against `~/keel/keel-live.db` ... reports PAXG-USD tranche 3". This plan may not touch `~/keel`. Task 2.1 reproduces that database's shape as a fixture (tranche 3's exact row). The operator runs `keel doctor` on the deployment after the release.

### Task 2.1: Two pure findings over plain rows

**Files:**
- Modify: `keel/commands/doctor.py`. Add `position_watch_findings` after `unbooked_exit_findings` (line 1428).
- Test: `tests/commands/test_doctor_position_watch.py` (new)

**Interfaces:**
- Produces: `position_watch_findings(open_positions: list[dict], all_rules: list[dict], resting: Callable[[dict], bool], retry_products: set[str], *, managed_status: str = "live") -> list[Finding]`.
- It returns exactly two findings, named `position.unmanaged` and `position.unprotected`. Each is `OK` or `WARN`, never `FAIL`, and has `products` populated when it warns (#642).
- `all_rules` is every rule row regardless of status, not only `live` ones. #811's first acceptance bullet requires `position.unmanaged`'s WARN to name "the rule's current status" (e.g. `paper`) for the product it warns about, and a rule that owns a demoted tranche is, by definition, no longer `live` -- so the function cannot both filter its input to `live` and report the status of a row that filtering just removed. The `live`-only membership test still decides WHO is managed; a second lookup over the same `all_rules` list finds each unmanaged product's most recent rule (by `id`, if more than one rule ever named the product) to name its status, or reports "no rule" when none ever did.
- `managed_status` is keyword-only, defaults to `"live"`, and is the status string that counts as "actively managed". A paper profile's engine promotes to `status="paper"`, never `"live"` (`agent.py`'s own `rule_status = "paper" if config.auto_trade.mode == "paper" else "live"`, line ~1700), so `get_rules("live")` -- and, before this fix, the hardcoded `"live"` comparison below -- is empty on every paper deployment, and every open paper tranche would WARN as unmanaged. `doctor.py`'s wiring (Step 4) passes `managed_status="paper"` on a paper profile.
- **`position.unprotected` is skipped entirely (reported `OK`, no `products`) when `managed_status == "paper"`.** `_open_tranche` (`keel/agent.py:329`) records `initial_stop=signal.setup.stop` for a paper entry exactly as it does for a live one (`agent.py:2112`, both the paper and live branches of `_handle_entries` converge on the same `_open_tranche` call below the `if result.placed:` guard) -- but `_paper_enter` (`agent.py:767`) never places a bracket, so `result.bracket_order_id` stays `None` on every paper fill, and `_has_resting_bracket` (`reconcile.py:471`) returns `False` whenever `position["bracket_order_id"] is None`, before it ever reads an order. `reconcile_unbracketed_positions`, the only writer of an `unbracketed:` retry record, is driven off `reconcile_open_orders`'s broker poll and is never run for a paper cycle (`agent.py`'s own comment: "LIVE cycles only: paper entries place no exchange-side brackets"), so `retry_products` is always empty on paper too. Every clause `position.unprotected` tests (`initial_stop` set, not resting, no retry record) is therefore true of every stopped PAPER tranche by construction, not by exception -- there is no exchange-side bracket for a paper fill to ever have, so the finding has nothing true to say on a paper profile and is skipped rather than made to WARN forever.

- [ ] **Step 1: Write the failing tests.**

```python
"""#811: is anything still watching a held tranche? Pure findings over plain rows."""

from __future__ import annotations

from decimal import Decimal

from keel.commands.doctor import OK, WARN, position_watch_findings

#: PAXG tranche 3 exactly as #811 printed it from the live database.
PAXG_TRANCHE_3 = {
    "id": 3,
    "product_id": "PAXG-USD",
    "rule_name": "turtle_breakout",
    "rule_id": None,
    "opened_at": 1_756_128_000,
    "qty": Decimal("0.01320427494019137563114227965"),
    "entry_fill": Decimal("4673.23"),
    "initial_stop": Decimal("4521.76390215979454"),
    "bracket_order_id": None,
    "status": "open",
}
BTC_DCA = {
    "id": 1,
    "product_id": "BTC-USD",
    "rule_name": "dca",
    "rule_id": None,
    "opened_at": 1_755_000_000,
    "qty": Decimal("0.0005"),
    "entry_fill": Decimal("100000"),
    "initial_stop": None,
    "bracket_order_id": None,
    "status": "open",
}
LIVE_BTC_DCA_RULE = {"id": 6, "kind": "dca", "status": "live", "params": {"product_id": "BTC-USD"}}
#: rule 3 exactly as #811 found it: demoted from `live` to `paper` on 2026-09-15, its tranche
#: still open. This is the row `position.unmanaged` must resolve PAXG-USD's status against.
PAXG_PAPER_TURTLE_RULE = {
    "id": 3, "kind": "turtle_breakout", "status": "paper", "params": {"product_id": "PAXG-USD"}
}


def _by_name(findings):
    return {f.name: f for f in findings}


def test_the_live_database_shape_reports_paxg_under_both_findings() -> None:
    found = _by_name(
        position_watch_findings(
            [BTC_DCA, PAXG_TRANCHE_3],
            [LIVE_BTC_DCA_RULE, PAXG_PAPER_TURTLE_RULE],
            lambda p: False,
            set(),
        )
    )
    assert found["position.unmanaged"].status == WARN
    assert found["position.unmanaged"].products == ("PAXG-USD",)
    assert "tranche 3" in found["position.unmanaged"].detail
    # #811's first acceptance bullet: the WARN names the owning rule's CURRENT status, not
    # just its name -- rule 3 was demoted `live` -> `paper`, and that demotion is the whole
    # reason PAXG-USD stopped being watched.
    assert "paper" in found["position.unmanaged"].detail
    assert found["position.unprotected"].status == WARN
    assert found["position.unprotected"].products == ("PAXG-USD",)
    assert "4521.76" in found["position.unprotected"].detail


def test_a_dca_tranche_produces_neither() -> None:
    found = _by_name(position_watch_findings([BTC_DCA], [LIVE_BTC_DCA_RULE], lambda p: False, set()))
    assert found["position.unmanaged"].status == OK
    assert found["position.unprotected"].status == OK


def test_a_resting_bracket_or_a_retry_record_clears_unprotected() -> None:
    with_bracket = position_watch_findings([PAXG_TRANCHE_3], [], lambda p: True, set())
    with_retry = position_watch_findings([PAXG_TRANCHE_3], [], lambda p: False, {"PAXG-USD"})
    assert _by_name(with_bracket)["position.unprotected"].status == OK
    assert _by_name(with_retry)["position.unprotected"].status == OK


def test_unmanaged_matches_on_product_not_on_rule_id() -> None:
    """`positions.rule_id` is NULL on everything before #803, so ownership cannot be the key."""
    live_turtle = {"id": 3, "kind": "turtle_breakout", "status": "live",
                   "params": {"product_id": "PAXG-USD"}}
    found = _by_name(position_watch_findings([PAXG_TRANCHE_3], [live_turtle], lambda p: True, set()))
    assert found["position.unmanaged"].status == OK


def test_a_demoted_dca_rule_surfaces_its_tranches_as_unmanaged() -> None:
    paper_dca = {"id": 9, "kind": "dca", "status": "paper", "params": {"product_id": "BTC-USD"}}
    found = _by_name(position_watch_findings([BTC_DCA], [paper_dca], lambda p: False, set()))
    assert found["position.unmanaged"].status == WARN
    assert "paper" in found["position.unmanaged"].detail


def test_a_product_with_no_rule_at_all_names_that_in_the_status() -> None:
    found = _by_name(position_watch_findings([PAXG_TRANCHE_3], [], lambda p: True, set()))
    assert found["position.unmanaged"].status == WARN
    assert "no rule" in found["position.unmanaged"].detail


def test_unprotected_is_skipped_on_a_paper_profile() -> None:
    """#881, 4th finding: a paper fill never has a bracket to rest, so `resting` is always False
    and there is never a retry record -- the exact same tranche this module WARNs on for a live
    profile is normal on a paper one, every cycle. `managed_status="paper"` is how the caller
    tells this function it is looking at a paper profile (`doctor.py`'s own wiring derives it
    from `config.auto_trade.mode`), so the same signal that already fixes `position.unmanaged`
    also has to silence `position.unprotected`."""
    found = _by_name(
        position_watch_findings(
            [PAXG_TRANCHE_3], [PAXG_PAPER_TURTLE_RULE], lambda p: False, set(),
            managed_status="paper",
        )
    )
    assert found["position.unprotected"].status == OK
    assert found["position.unprotected"].products == ()


def test_unprotected_still_warns_on_a_live_profile_with_the_same_shape() -> None:
    """Control for the test above: the paper skip must not silence the live-profile WARN this
    finding exists for in the first place (#811's PAXG tranche 3 fixture)."""
    found = _by_name(
        position_watch_findings([PAXG_TRANCHE_3], [PAXG_PAPER_TURTLE_RULE], lambda p: False, set())
    )
    assert found["position.unprotected"].status == WARN
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/commands/test_doctor_position_watch.py -v`. Expected: FAIL with `ImportError: cannot import name 'position_watch_findings'`.

- [ ] **Step 3: Implement.**

```python
def position_watch_findings(
    open_positions: list[dict[str, Any]],
    all_rules: list[dict[str, Any]],
    resting: Callable[[dict[str, Any]], bool],
    retry_products: set[str],
    *,
    managed_status: str = "live",
) -> list[Finding]:
    """Is anything still WATCHING each held tranche (#811)?

    `reconcile.position_unprotected` was the only channel that ever reported PAXG tranche 3,
    and it was driven off the `unbracketed:` retry record: clearing the record silenced the
    channel without closing the exposure. These two are stated over the `positions` ledger,
    which cannot be silenced that way -- the table that knows a tranche is held still says open.

    * `position.unmanaged` -- an open tranche whose PRODUCT has no `live` rule. Matched on
      product, not `rule_id`, because every tranche before #803 has NULL there. A DCA tranche
      whose rule was demoted is unmanaged too: unmanaged inventory is unmanaged with or without
      a stop. Only ENTRY/EXIT rule kinds count: a `sleeve_sell` rule proposes and cannot exit,
      so it manages nothing (plan Review Focus 5; P9 adds the exclusion and its test).
    * `position.unprotected` -- an open tranche with a recorded `initial_stop > 0`, no resting
      bracket (`reconcile._has_resting_bracket`, passed in as `resting`), and no retry record.
      The third clause makes it the complement of the reconcile sweep, not a duplicate. DCA
      (`initial_stop` absent) is excluded for the reason the sweep skips it silently. SKIPPED
      ENTIRELY (reported `OK`) when `managed_status == "paper"` (#881): a paper fill never gets
      a bracket (`_paper_enter` places none), so `resting` is always False, and paper never
      writes an `unbracketed:` retry record either (`reconcile_unbracketed_positions` only runs
      on live cycles) -- every clause here is unconditionally true for every stopped paper
      tranche, so without the skip this finding WARNs on every one of them, always.

    WARN, never FAIL: holding spot without a stop can be a human's choice (PAXG since
    2026-09-22). What was wrong is that nobody was told, and FAIL would halt cycles over a state
    the operator already accepted.

    `all_rules` is every rule row, of any status -- not just `live` ones. Membership in
    `managed` is decided by `status == managed_status` alone, but #811's first acceptance bullet
    requires the `position.unmanaged` WARN to name the owning rule's CURRENT status (e.g.
    `paper`), and a rule moved out of `managed_status` is exactly the row that produced the WARN
    in the first place. Filtering the input to that status before it arrives here would throw
    that row away before its status could be read. `status_by_product` resolves it from the full
    set instead, taking the highest `id` when more than one rule has ever named a product, and
    reporting `"no rule"` when none has.

    `managed_status` defaults to `"live"`, the status a live profile promotes to. A PAPER
    profile promotes to `status="paper"` instead (`agent.py`'s own
    `rule_status = "paper" if config.auto_trade.mode == "paper" else "live"`), and never writes
    a `"live"` row, so a paper deployment calling this with the default would find `managed`
    permanently empty and WARN `position.unmanaged` on every open tranche it holds -- correctly
    managed rules and all. The caller passes `managed_status="paper"` there. The same value is
    the paper-profile signal `position.unprotected` reads (#881): `managed_status == "paper"`
    means the caller is looking at a paper deployment, and that is reason enough on its own to
    skip a finding whose every input clause a paper tranche satisfies unconditionally.
    """
    managed = {
        str((row.get("params") or {}).get("product_id"))
        for row in all_rules
        if row.get("status") == managed_status
    }
    status_by_product: dict[str, str] = {}
    for row in sorted(all_rules, key=lambda r: r.get("id") or 0):
        status_by_product[str((row.get("params") or {}).get("product_id"))] = str(row.get("status"))

    unmanaged = [p for p in open_positions if str(p["product_id"]) not in managed]
    unprotected = [] if managed_status == "paper" else [
        p
        for p in open_positions
        if (p.get("initial_stop") or 0) > 0
        and not resting(p)
        and str(p["product_id"]) not in retry_products
    ]

    def _describe(rows: list[dict[str, Any]], *, levels: bool) -> str:
        parts = []
        for p in rows:
            status = status_by_product.get(str(p["product_id"]), "no rule")
            text = (
                f"{p['product_id']} tranche {p['id']} ({p['rule_name']}, {status}, "
                f"qty {p['qty']})"
            )
            if levels:
                text += f", stop {p['initial_stop']}, no resting bracket, no retry record"
            parts.append(text)
        return "; ".join(parts)

    def _products(rows: list[dict[str, Any]]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(p["product_id"]) for p in rows))

    out: list[Finding] = []
    if unmanaged:
        out.append(
            Finding(
                "position.unmanaged",
                WARN,
                f"{len(unmanaged)} open tranche(s) on a product with no live rule",
                _describe(unmanaged, levels=False)
                + " -- no live rule evaluates these products, so no exit can fire",
                "re-promote the owning rule, or close the tranche by hand "
                "(`keel positions close <id>` once #798 ships)",
                products=_products(unmanaged),
            )
        )
    else:
        out.append(Finding("position.unmanaged", OK, "every open tranche has a live rule",
                           "-", "-"))
    if unprotected:
        out.append(
            Finding(
                "position.unprotected",
                WARN,
                f"{len(unprotected)} tranche(s) hold a stop level and nothing resting",
                _describe(unprotected, levels=True)
                + " -- the next cycle will NOT retry: the retry record is gone",
                "doctor cannot re-place a bracket; place one at the venue or close the tranche",
                products=_products(unprotected),
            )
        )
    elif managed_status == "paper":
        out.append(Finding("position.unprotected", OK, "paper fills place no exchange bracket "
                           "by design; skipped (#881)", "-", "-"))
    else:
        out.append(Finding("position.unprotected", OK, "every stopped tranche is protected or "
                           "being retried", "-", "-"))
    return out
```

- [ ] **Step 4: Wire it into `gather_findings`,** directly after `unbooked_exit_findings`:

```python
    # #811. `all_rules` -- every status, not just "live" -- so `position.unmanaged` can name a
    # demoted rule's current status. `managed_status` mirrors `agent.py`'s own
    # `rule_status = "paper" if config.auto_trade.mode == "paper" else "live"`: a paper profile
    # promotes to `status="paper"`, never `"live"`, so passing the default here would WARN on
    # every open paper tranche (#881).
    findings += position_watch_findings(
        repo.get_open_positions(),
        repo.get_rules(),
        lambda position: reconcile_mod._has_resting_bracket(repo, position),
        {key[len(executor_mod.UNBRACKETED_PREFIX):]
         for key in repo.get_state_keys(executor_mod.UNBRACKETED_PREFIX)
         if repo.get_state(key) is not None},
        managed_status="paper" if config.auto_trade.mode == "paper" else "live",
    )
```

  Add a test to `tests/commands/test_doctor.py` beside the existing change-counter read-only test. The new test runs `gather_findings` on a repo seeded with PAXG tranche 3, and asserts the two names are present and that the change counter is unchanged.

  **#886: add `"position.unmanaged"` and `"position.unprotected"` to the exact-set assertion.** `tests/commands/test_doctor.py:603`'s `test_gather_findings_covers_every_check_over_a_seeded_db` asserts `{f.name for f in findings} == {...}` -- the COMPLETE set of finding names, not a subset. Wiring `position_watch_findings` into `gather_findings` above adds two names `gather_findings` now returns that this literal does not yet list, which turns the equality false and this existing test red. Add both names to the set literal. Verify the exact current set in the worktree before editing (`sed -n '/^def test_gather_findings_covers_every_check_over_a_seeded_db/,/^def /p' tests/commands/test_doctor.py`) rather than assuming the list above is still current -- later PRs in this plan (P3, P10, P15) each add to the same literal, and it must always be edited against what is actually there at the time, not this plan's snapshot of it.

  **Add a paper-profile test.** `tests/commands/test_doctor.py`'s own `gather_findings` tests already build their repo with `_seeded_repo(tmp_path / "keel.db")` and their config with `load_config(valid_config_path)` (both defined/imported at the top of that file). Seed a repo with an open tranche carrying `initial_stop=Decimal("90000")` and `bracket_order_id=None` (`repo.open_position(...)`, matching what a paper fill actually writes -- see #881 above) and a `status="paper"` rule for the same product (never `"live"`), take `config = load_config(valid_config_path)` and derive a paper config from it with `dataclasses.replace(config, auto_trade=dataclasses.replace(config.auto_trade, mode="paper"))` (`AutoTradeConfig`/`Config` are both frozen dataclasses, `packages/keel-core/keel_core/config.py`). Assert `gather_findings(repo, paper_config, [], now_ts)` reports BOTH `position.unmanaged` and `position.unprotected` as `OK`. Before this fix, `position.unmanaged` WARNs (`managed_status` hardcoded to `"live"`) and, separately, `position.unprotected` WARNs too (the tranche has a stop, no bracket, and no retry record, and nothing before #881's fix knew it was looking at paper) -- the test must fail red on both counts first, and stay green on both after.

- [ ] **Step 5: Run the tests, run `--json`, then commit.**
  - Run: `uv run pytest tests/commands/test_doctor_position_watch.py tests/commands/test_doctor.py -q`. Expected: PASS.
  - Add a `render_json` assertion that `products` carries `["PAXG-USD"]` for both findings (#642).

```bash
git add keel/commands/doctor.py tests/commands/test_doctor_position_watch.py tests/commands/test_doctor.py
git commit -m "feat(doctor): position.unmanaged and position.unprotected -- a ledger-driven watch (#811)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**Acceptance (P2):** #811's five acceptance bullets. The fifth is met on the fixture, and the operator confirms it on the deployment.

---

## P3 — `feat(doctor): ledger.drift and ledger.venue_drift, fed by a per-cycle venue-holdings record (#799, #798)` · M · no schema

**PR body states:** S1–S4. `record_venue_holdings` reads balances and writes one `agent_state` key per held product. It places and cancels nothing. Closes #799 (proposal 3; see R1 for proposals 1 and 4). Refs #798.

### Task 3.1: `sleeve.ledger_qty` and `ledger.drift`

**Files:**
- Create: `keel/execution/sleeve.py`
- Modify: `keel/commands/doctor.py`
- Test: `tests/execution/test_sleeve.py` (new), `tests/commands/test_doctor_ledger_drift.py` (new), `tests/commands/test_doctor.py` (append, one paper-profile wiring test)

**Interfaces:**
- Produces: `sleeve.ledger_qty(positions: Iterable[dict]) -> Decimal`, the sum of open tranche `qty`. It is the definition `Holding.qty` must equal (P5 pins it).
- Produces: `sleeve.orders_qty(repo: Repository, product_id: str, mode: str) -> Decimal`, net filled BUY - SELL qty from `orders` tagged `mode`. This is deliberately NOT a call to `executor._held_position`: that function hardcodes `mode="live"` BY DESIGN (see `agent._book_paper_exit`'s docstring -- "the live exit path is unreachable from paper BY CONSTRUCTION... routing paper exits through `executor.execute` would put a real broker behind them"), so a paper cycle's rules always see qty 0 there, on purpose. `ledger.drift` is a read-only doctor comparison, not a placement path, and it must compare the ledger against the SAME mode's orders on a paper profile, or every open paper tranche reads as a stranded fill.
- Produces: `doctor.ledger_drift_findings(ledger_by_product: dict[str, Decimal], orders_by_product: dict[str, Decimal], increments: dict[str, Decimal | None]) -> list[Finding]`, named `ledger.drift`. Unchanged in shape; the mode fix lives entirely in what its caller builds `orders_by_product` from (Step 3's wiring, below).

- [ ] **Step 1: Write the failing tests.**

```python
# tests/execution/test_sleeve.py
from decimal import Decimal

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import sleeve
from keel.types import Side


def test_ledger_qty_sums_what_is_still_held() -> None:
    rows = [{"qty": Decimal("0.0005")}, {"qty": Decimal("0.00049")}]
    assert sleeve.ledger_qty(rows) == Decimal("0.00099")


def test_ledger_qty_of_nothing_is_zero_not_none() -> None:
    assert sleeve.ledger_qty([]) == Decimal("0")


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _order(*, mode: str, side: str, qty: Decimal) -> dict:
    return dict(mode=mode, product_id="BTC-USD", side=side, order_type="market", qty=qty,
                limit_price=Decimal("100000"), status="filled", fee=Decimal("0"),
                expected_fill=Decimal("100000"), actual_fill=Decimal("100000"),
                created_at=0, updated_at=0)


def test_orders_qty_reads_the_requested_mode_only() -> None:
    """#881: on a paper profile every fill is `mode='paper'`. `orders_qty` must be told which
    mode to read, unlike `executor._held_position`, which hardcodes `mode='live'` on purpose."""
    repo = _repo()
    repo.insert_order(_order(mode="paper", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("5")))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="paper") == Decimal("0.001")
    assert sleeve.orders_qty(repo, "BTC-USD", mode="live") == Decimal("5")


def test_orders_qty_nets_sells_and_floors_at_zero() -> None:
    repo = _repo()
    repo.insert_order(_order(mode="paper", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="paper", side=Side.SELL.value, qty=Decimal("0.002")))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="paper") == Decimal("0")
```

```python
# tests/commands/test_doctor_ledger_drift.py
from decimal import Decimal

from keel.commands.doctor import OK, WARN, ledger_drift_findings


def test_the_799_shape_a_filled_buy_with_no_tranche_warns() -> None:
    """#799 proposal 3: order id 4 filled 0.0132 PAXG and no positions row was written."""
    [f] = ledger_drift_findings(
        {"PAXG-USD": Decimal("0")}, {"PAXG-USD": Decimal("0.0132")}, {"PAXG-USD": None}
    )
    assert f.name == "ledger.drift" and f.status == WARN
    assert f.products == ("PAXG-USD",)
    assert "orders say 0.0132" in f.detail and "ledger says 0" in f.detail


def test_drift_within_one_base_increment_is_not_drift() -> None:
    [f] = ledger_drift_findings(
        {"BTC-USD": Decimal("0.00099")}, {"BTC-USD": Decimal("0.000995")},
        {"BTC-USD": Decimal("0.00000001")},
    )
    assert f.status == WARN
    [g] = ledger_drift_findings(
        {"BTC-USD": Decimal("0.00099")}, {"BTC-USD": Decimal("0.000990005")},
        {"BTC-USD": Decimal("0.00001")},
    )
    assert g.status == OK


def test_an_unknown_increment_uses_exact_equality() -> None:
    [f] = ledger_drift_findings({"X-USD": Decimal("1")}, {"X-USD": Decimal("1")}, {"X-USD": None})
    assert f.status == OK
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/execution/test_sleeve.py tests/commands/test_doctor_ledger_drift.py -v`. Expected: `ModuleNotFoundError: keel.execution.sleeve` and `ImportError`.

- [ ] **Step 3: Implement `keel/execution/sleeve.py`.** Its docstring states the §3.3 rule: "the `positions` ledger is the truth for lots and average entry; `executor._held_position` (orders-derived) is what rails size from; when the two disagree by more than a base increment, that is #798's phantom exposure or #799's stranded fill, and it is a doctor finding, never a silent assumption".

```python
"""The DCA sleeve's ledger-side logic (docs/superpowers/specs/2026-09-28-dca-sleeve-sell-side-
design.md §3.3).

THE `positions` LEDGER IS THE TRUTH FOR WHAT IS HELD, LOT BY LOT. `executor._held_position` derives
a quantity and an average from `orders`, and the rails size from that. The two legitimately
disagree only by dust. Beyond one base increment they are telling different stories: #799's
filled entry with no tranche (the orders say more), or #798's out-of-band sale (both say more than
the venue). Each is a doctor finding (`ledger.drift`, `ledger.venue_drift`), never an assumption
a sale is sized from.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from keel.data.repository import Repository
from keel.types import Side


def ledger_qty(positions: Iterable[dict[str, Any]]) -> Decimal:
    """Sum of `qty` over the given OPEN tranches -- what is still held. `Holding.qty` (P5) is
    defined as exactly this, and a test pins the two equal."""
    return sum((Decimal(p["qty"]) for p in positions), Decimal("0"))


def orders_qty(repo: Repository, product_id: str, mode: str) -> Decimal:
    """Net filled BUY - SELL qty for `product_id`, from `orders` tagged `mode` (#881).

    Mirrors `executor._held_position`'s arithmetic, but is NOT `_held_position`: that function
    hardcodes `mode="live"` deliberately (`agent._book_paper_exit`'s docstring -- the live exit
    path is unreachable from paper BY CONSTRUCTION, not by accident). `ledger.drift` is a
    read-only doctor comparison, not a placement path, and it must compare the ledger against
    the SAME mode's orders: on a paper profile every fill is `mode="paper"`, and comparing it
    against `_held_position`'s always-empty `mode="live"` total would WARN on every open paper
    tranche as though it were #799's stranded fill.
    """
    buy_qty = Decimal("0")
    sell_qty = Decimal("0")
    for order in repo.get_orders(mode=mode, product_id=product_id, status="filled"):
        qty = order["qty"] or Decimal("0")
        if order["side"] == Side.BUY.value:
            buy_qty += qty
        elif order["side"] == Side.SELL.value:
            sell_qty += qty
    net = buy_qty - sell_qty
    return net if net > 0 else Decimal("0")
```

  In `doctor.py`:

```python
def ledger_drift_findings(
    ledger_by_product: dict[str, Decimal],
    orders_by_product: dict[str, Decimal],
    increments: dict[str, Decimal | None],
) -> list[Finding]:
    """The positions ledger against the orders log, per product (#799 proposal 3; plan R2).

    #799's stranded PAXG fill logged only a `preview_failed` -- "a harmless pre-trade hiccup".
    This is the check that would have named it: a filled BUY with no tranche makes the orders
    log hold more than the ledger. Tolerance is one base increment, because a venue that takes
    its fee in the base asset leaves exactly that kind of dust (#667). Unknown increment means
    exact equality -- no tolerance may be invented.
    """
    drifted: list[tuple[str, Decimal, Decimal]] = []
    for product in sorted(set(ledger_by_product) | set(orders_by_product)):
        ledger = ledger_by_product.get(product, Decimal("0"))
        orders = orders_by_product.get(product, Decimal("0"))
        tolerance = increments.get(product) or Decimal("0")
        if abs(ledger - orders) > tolerance:
            drifted.append((product, ledger, orders))
    if not drifted:
        return [Finding("ledger.drift", OK, "the positions ledger matches the orders log",
                        "-", "-")]
    detail = "; ".join(f"{p}: orders say {o}, ledger says {l}" for p, l, o in drifted)
    return [
        Finding(
            "ledger.drift",
            WARN,
            f"{len(drifted)} product(s) where the ledger and the orders log disagree",
            detail + " -- a filled entry with no tranche (#799) or an unbooked sale",
            "inspect the console's Positions view and `keel orders list`; record a missing "
            "tranche or "
            "declare an out-of-band close with `keel positions close <id>` (#798)",
            products=tuple(p for p, _, _ in drifted),
        )
    ]
```

  Wire it into `gather_findings`. Iterate the products in `repo.held_products()` ∪ the open-tranche products:
  - `ledger` is `sleeve.ledger_qty(repo.get_open_positions(p))`;
  - `order_mode = "paper" if config.auto_trade.mode == "paper" else "live"` (the same derivation as P2's `managed_status`), and `orders` is `sleeve.orders_qty(repo, p, order_mode)` -- **not** `executor_mod._held_position(repo, p)[0]`, which hardcodes `mode="live"` and would read as zero orders for every product on a paper profile (#881);
  - the increment comes from the cached `base_increment:<p>` state record (`record["increment"]`), and is `None` when the record is absent. **Never call the broker.**

  **Add a paper-profile test** to `tests/commands/test_doctor.py`, beside P2's: seed a repo with an open paper-mode tranche whose backing order is inserted with `mode="paper"` (not `"live"`), take a paper `config` the same way P2's test does (`dataclasses.replace(config, auto_trade=dataclasses.replace(config.auto_trade, mode="paper"))`), and assert `gather_findings` reports `ledger.drift` as `OK`. Before this fix, `orders_by_product` built from `_held_position` (always `mode="live"`) sees zero orders against a non-zero ledger and WARNs; the test must fail red for that reason first.

  **#886: add `"ledger.drift"` to the exact-set assertion.** Wiring `ledger_drift_findings` into `gather_findings` adds a name `test_gather_findings_covers_every_check_over_a_seeded_db` (`tests/commands/test_doctor.py:603`) does not yet list in its `{f.name for f in findings} == {...}` literal, turning that existing test red. Add `"ledger.drift"` to the set. Check the literal's actual current contents first -- P2's task above may already have added its own two names to the same set.

- [ ] **Step 4: Run the tests, then commit.** Run: `uv run pytest tests/execution/test_sleeve.py tests/commands/test_doctor_ledger_drift.py tests/commands/test_doctor.py -q`. Expected: PASS.

```bash
git add keel/execution/sleeve.py keel/commands/doctor.py tests/execution/test_sleeve.py tests/commands/test_doctor_ledger_drift.py tests/commands/test_doctor.py
git commit -m "feat(doctor): ledger.drift -- the positions ledger against the orders log (#799)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3.2: `reconcile.record_venue_holdings` and `ledger.venue_drift` (R3)

**Files:**
- Modify: `keel/execution/reconcile.py`, `keel/agent.py` (call it after `sweep_orphan_brackets`, live cycles only), `keel/commands/doctor.py`
- Test: `tests/execution/test_reconcile.py` (append), `tests/commands/test_doctor_ledger_drift.py` (append), `tests/test_agent.py` (one test), `tests/commands/test_doctor.py` (append, one paper-profile wiring test)

**Interfaces:**
- Produces: `reconcile.VENUE_HOLDING_PREFIX = "venue_holding:"`.
- Produces: `reconcile.record_venue_holdings(broker, repo, now_ts) -> dict[str, Decimal]`, which writes `{"total": str, "observed_at": int}` per product with an open tranche.
- Produces: `doctor.venue_drift_findings(ledger_by_product: dict[str, Decimal], venue: dict[str, dict]) -> list[Finding]`, named `ledger.venue_drift`.

- [ ] **Step 1: Write the failing tests.**

  This module has no `HeldBroker`, `NOW_TS` or `_BalancesRaise` today. `HeldBroker` lives in
  `tests/execution/test_sell_clamp.py` (itself a `FakeBroker` subclass imported from
  `tests/execution/test_executor.py`, the precedent for reusing a broker fake across test
  modules) -- import it from there rather than redefining it. This file's own clock constant is
  `NOW = 1_800_000_000` (not `NOW_TS`); the new tests use that. `_BalancesRaise` does not exist
  anywhere and is defined fresh, right beside `_Broker`.

```python
# appended to tests/execution/test_reconcile.py imports
from tests.execution.test_sell_clamp import HeldBroker


class _BalancesRaise:
    """`get_balances()` raises -- the venue is unreachable when `record_venue_holdings` polls."""

    def get_balances(self) -> list[Balance]:
        raise RuntimeError("network error")


# appended to tests/execution/test_reconcile.py
def test_record_venue_holdings_writes_the_venue_total_for_each_held_product(repo):
    repo.open_position(product_id="BTC-USD", rule_name="dca", opened_at=1, qty=Decimal("0.001"),
                       entry_fill=Decimal("100000"), entry_fee=Decimal("0.3"))
    broker = HeldBroker("BTC", available=Decimal("0.0007"), total=Decimal("0.0009"))

    recorded = reconcile.record_venue_holdings(broker, repo, NOW)

    assert recorded == {"BTC-USD": Decimal("0.0009")}, "TOTAL, never available (#667)"
    assert repo.get_state("venue_holding:BTC-USD") == {"total": "0.0009", "observed_at": NOW}
    assert broker.get_balances_calls == 1, "one balance read per cycle, not one per product"


def test_an_unreadable_balance_writes_nothing_rather_than_zero(repo):
    repo.open_position(product_id="BTC-USD", rule_name="dca", opened_at=1, qty=Decimal("0.001"),
                       entry_fill=Decimal("100000"), entry_fee=Decimal("0"))
    assert reconcile.record_venue_holdings(_BalancesRaise(), repo, NOW) == {}
    assert repo.get_state("venue_holding:BTC-USD") is None
```

```python
# appended to tests/commands/test_doctor_ledger_drift.py
from keel.commands.doctor import venue_drift_findings


def test_the_798_shape_the_ledger_holds_more_than_the_venue() -> None:
    """An out-of-band venue sale (#798): keel still counts the BUY, the venue does not."""
    [f] = venue_drift_findings(
        {"BTC-USD": Decimal("0.001")},
        {"BTC-USD": {"total": "0.0004", "observed_at": 1_700_000_000}},
    )
    assert f.name == "ledger.venue_drift" and f.status == WARN
    assert "ledger 0.001 > venue 0.0004" in f.detail
    assert "observed 2023-11-14" in f.detail


def test_the_venue_holding_more_than_the_ledger_is_not_this_finding() -> None:
    [f] = venue_drift_findings({"BTC-USD": Decimal("0.001")},
                               {"BTC-USD": {"total": "0.002", "observed_at": 1}})
    assert f.status == OK


def test_no_observation_reports_unknown_not_ok() -> None:
    [f] = venue_drift_findings({"BTC-USD": Decimal("0.001")}, {})
    assert f.status == WARN and "no venue observation" in f.detail
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/execution/test_reconcile.py -k venue tests/commands/test_doctor_ledger_drift.py -v`. Expected: `AttributeError` or `ImportError`.

- [ ] **Step 3: Implement.**
  - `record_venue_holdings` calls `broker.get_balances()` once, inside `try` (with `log_venue_failure` on exception, returning `{}`).
  - For each product in `{p["product_id"] for p in repo.get_open_positions()}`, it parses the base with `parse_spot_product_id` and matches `balance.currency.upper()`.
  - It writes the record only when a matching row exists. Its docstring states R3 and the `total`-not-`available` rule (#667).
  - Call it in `run_once` after `reconcile.sweep_orphan_brackets(...)`, guarded by `if paper_trader is None:` (the live cycle), and wrap it so an exception is logged and swallowed. A diagnostic write must never cost a cycle, which is the `notify_after_cycle` precedent.
  - `venue_drift_findings` warns when `ledger > Decimal(record["total"])`, or when there is no record for a held product. Wire it into `gather_findings` from `repo.get_state_keys(reconcile_mod.VENUE_HOLDING_PREFIX)`.
  - **On a paper profile** (`config.auto_trade.mode == "paper"`), `record_venue_holdings` never runs (the `if paper_trader is None:` guard two bullets up), so `venue_holding:` state is permanently empty BY DESIGN, not by omission -- there is no real venue holding to reconcile a paper tranche against. Without a guard, every open paper tranche would WARN `ledger.venue_drift` "no venue observation" on every cycle (#881). Pass an empty `ledger_by_product` (`{}`) to `venue_drift_findings` when `config.auto_trade.mode == "paper"`, so it returns its `OK` sentinel instead of iterating products it can never have an observation for.
  - Add a `tests/test_agent.py` test that a live `run_once` with an open tranche writes `venue_holding:<product>`, and that a paper `run_once` writes none.
  - **Add a paper-profile wiring test** to `tests/commands/test_doctor.py`, beside P2's and Task 3.1's: seed a repo with an open tranche and NO `venue_holding:` state record, take a paper `config` the same way (`auto_trade.mode="paper"`), and assert `gather_findings` reports `ledger.venue_drift` as `OK`. Before this fix, the empty `venue` dict plus a non-empty `ledger_by_product` WARNs "no venue observation"; the test must fail red for that reason first.
  - **#886: add `"ledger.venue_drift"` to the exact-set assertion.** Wiring `venue_drift_findings` into `gather_findings` adds a name `test_gather_findings_covers_every_check_over_a_seeded_db` (`tests/commands/test_doctor.py:603`) does not yet list, turning that existing test red. Add `"ledger.venue_drift"` to its `{f.name for f in findings} == {...}` set, alongside `"ledger.drift"` (Task 3.1, above) and P2's two names -- check the literal's actual current contents first.

- [ ] **Step 4: Run the tests, the suite, then commit.**

```bash
uv run pytest -q
git add keel/execution/reconcile.py keel/agent.py keel/commands/doctor.py tests/
git commit -m "feat(doctor): ledger.venue_drift from a per-cycle venue-holdings record (#798)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**Acceptance (P3):**
- The #799 fixture warns `ledger.drift`.
- A fabricated out-of-band sale warns `ledger.venue_drift` before any sale is attempted (spec §9 row 2).
- `gather_findings` stays read-only under the change-counter test.

---

## P4 — `feat(positions): keel positions close <id> -- an operator-declared exit the rails can read (#798)` · M · no schema

**PR body states:**
- S1: nothing reaches the venue. The test runs with `NoNetworkBroker`, and the command builds no broker at all.
- S4: `close_declared_position` joins `WEB_FORBIDDEN_NAMES`.
- It adds one capability row.
- Task 4.0 fixes `guards._open_exposure_by_asset` so a product closed in full releases all of its exposure, whatever price the close happened at -- without it, a declared close at a loss (or the guard's very next real caller, any ordinary loss-making exit) leaves a phantom notional residual and #798 is not actually closed.
- Closes #798. Together with P3 and Task 4.0, all three of #798's proposals are delivered.

### Task 4.0: `guards._open_exposure_by_asset` releases a fully closed product at ANY exit price

**Why this has to come before Task 4.1.** `_open_exposure_by_asset` nets BUY and SELL
**notional**: `exposure[asset] += amount` for a BUY, `-= amount` for a SELL, each `amount` at
the row's OWN price (`_order_notional`). That is the right figure for a position that is only
PARTLY closed -- the existing dust/partial-fill tests below pin exactly that. It is the WRONG
figure for a position closed IN FULL at a price other than its entry: PAXG tranche 3 bought
0.0132 at 4673.23 (notional \$61.69) and a declared close at 4400 (notional \$58.08, a REAL
loss, not fee dust) nets to \$3.61 of "exposure" the ledger no longer holds a single unit of.
Task 4.1's own acceptance test, `test_a_declared_close_releases_the_exposure_rails_4_5_6_read`,
closes PAXG at exactly that loss and asserts the exposure guard reads zero afterward -- it
cannot pass against today's `_open_exposure_by_asset`, and #798's phantom exposure survives.

**Files:**
- Modify: `keel/execution/guards.py`
- Test: `tests/execution/test_guards.py` (append)

**Interfaces:**
- `_open_exposure_by_asset` keeps its signature and its return type. The only change is that a
  PRODUCT whose net filled quantity (BUY qty − SELL qty, at each row's `filled_quantity` or
  `qty`) is `<= 0` contributes **zero** notional to its asset bucket, instead of its net
  notional. A product still net-long (qty `> 0`) contributes its net notional exactly as today.

- [ ] **Step 1: Write the failing tests.**

```python
# appended to tests/execution/test_guards.py

def test_a_fully_closed_position_reads_zero_exposure_even_at_a_loss(repo) -> None:
    """#798/#882: a close that exits the FULL held quantity releases all of it, whatever price
    the exit happened at. Net NOTIONAL (BUY $61.69 - SELL $58.08 = $3.61) is not zero when the
    exit priced below the entry -- but net QUANTITY is exactly zero, and a fully closed product
    carries no exposure left to measure. These are PAXG tranche 3's own numbers (#811)."""
    _seed_filled_order(
        repo, product_id="PAXG-USD", side=Side.BUY, qty=Decimal("0.0132"),
        price=Decimal("4673.23"), created_at=NOW_TS - 86_400,
    )
    _seed_filled_order(
        repo, product_id="PAXG-USD", side=Side.SELL, qty=Decimal("0.0132"),
        price=Decimal("4400"), created_at=NOW_TS,
    )
    assert "PAXG" not in guards._open_exposure_by_asset(repo)


def test_a_partial_close_still_nets_by_notional(repo) -> None:
    """Pinned so the fix above cannot regress the existing behaviour: a position only PARTLY
    closed (net qty still > 0) keeps netting by notional, dust and all."""
    _seed_filled_order(
        repo, product_id="PAXG-USD", side=Side.BUY, qty=Decimal("0.02"),
        price=Decimal("4673.23"), created_at=NOW_TS - 86_400,
    )
    _seed_filled_order(
        repo, product_id="PAXG-USD", side=Side.SELL, qty=Decimal("0.0132"),
        price=Decimal("4400"), created_at=NOW_TS,
    )
    exposure = guards._open_exposure_by_asset(repo)
    assert exposure["PAXG"] == Decimal("0.02") * Decimal("4673.23") - Decimal("0.0132") * Decimal("4400")
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/execution/test_guards.py -k "fully_closed_position or partial_close_still" -v`. Expected: the first FAILs (`"PAXG" in {"PAXG": Decimal("3.6066...")}`); the second already PASSes against today's code -- it exists to prove Step 3 does not change it.

- [ ] **Step 3: Implement.** Replace the function body (the docstring keeps every existing paragraph -- this appends one, per "rules live in neighbouring docstrings" -- and the malformed-row branch's WARNING/counted-vs-skipped decision is untouched):

```python
    """... (existing docstring, unchanged) ...

    **A fully (or over-) closed PRODUCT contributes zero, not its net notional (#798, #882).**
    Both sides above count at each row's OWN price, which is right for a position still partly held --
    the remaining notional really is what is still at risk. It is wrong once the position is
    FULLY closed: a close at any price other than the entry leaves a notional residual (a real
    gain or loss) that is not exposure, because zero units are held. Net QUANTITY, not notional,
    is what decides "closed": tracked per PRODUCT (never mixed across a futures/spot pair in the
    same asset bucket -- their units are not comparable), a product whose BUY qty minus SELL qty
    is `<= 0` contributes nothing to its asset bucket, whatever its net notional says.
    """
    exposure: dict[str, Decimal] = {}
    per_product: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for status in _OBSERVED_FILL_STATUSES:
        rows.extend(repo.get_orders(mode="live", status=status))
    for order in rows:
        product_id = order["product_id"]
        side = order["side"]
        if parse_spot_product_id(product_id) is None:
            # `action` is in the log line because "we saw a bad row" and "we let it release a
            # cap" are different events to the operator reading this at 3am.
            counted = side == Side.BUY.value
            log_event(
                logger,
                logging.WARNING,
                "guards.exposure_row_unparseable",
                product=str(product_id),
                order_id=order.get("id"),
                side=side,
                action="counted" if counted else "skipped",
            )
            if not counted:
                continue
        asset = _asset(product_id)
        amount = _order_notional(order)
        qty = order.get("filled_quantity") or order.get("qty") or Decimal("0")
        bucket = per_product.setdefault(
            product_id, {"asset": asset, "notional": Decimal("0"), "qty": Decimal("0")}
        )
        if side == Side.BUY.value:
            bucket["notional"] += amount
            bucket["qty"] += qty
        elif side == Side.SELL.value:
            bucket["notional"] -= amount
            bucket["qty"] -= qty
    for bucket in per_product.values():
        if bucket["qty"] > 0:
            asset = bucket["asset"]
            exposure[asset] = exposure.get(asset, Decimal("0")) + bucket["notional"]
    return {asset: amt for asset, amt in exposure.items() if amt > 0}
```

  This is mathematically identical to today's flat accumulation for every product that stays
  net-long (addition is associative; grouping by product first changes nothing when every
  group's own sign matches its contribution), so every existing exposure test -- partial fills,
  the malformed-row tests, rails 4/5/6 -- keeps passing unchanged. It differs ONLY where a
  product's net qty is `<= 0`, which is exactly the case this task fixes.

- [ ] **Step 4: Run the guard suite, then the full suite, then commit.**

```bash
uv run pytest tests/execution/test_guards.py -q
uv run pytest -q && uv run ruff check keel tests && uv run ruff format --check keel tests && uv run mypy
git add keel/execution/guards.py tests/execution/test_guards.py
git commit -m "fix(guards): a fully closed product releases all its exposure, not its net notional (#798)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**Acceptance (Task 4.0):** a product whose filled BUY and SELL quantities net to zero or less
contributes zero exposure regardless of the prices involved; a partly-closed product's exposure
is unchanged from today.

---

### Task 4.1: The service, `close_declared_position`

**Files:**
- Create: `keel/commands/positions_close.py`
- Test: `tests/commands/test_positions_close.py` (new)

**Interfaces:**
- Produces: `close_declared_position(repo, config, *, position_id: int, price: Decimal, fee: Decimal, now_ts: int) -> int`, which returns the new `orders.id`.
- Raises: `PositionCloseRefused(msg)` for an unknown or closed id, `price <= 0`, or `fee < 0`.

- [ ] **Step 1: Write the failing tests.**

```python
from decimal import Decimal

import pytest

from keel.commands.positions_close import PositionCloseRefused, close_declared_position
from keel.execution import guards
from tests.execution.test_executor import NOW_TS, _config, repo  # noqa: F401


def _tranche(repo, *, rule_name="turtle_breakout", qty="0.0132", fill="4673.23", fee="0.73"):
    repo.insert_order(dict(mode="live", product_id="PAXG-USD", side="BUY", order_type="market",
                           qty=Decimal(qty), status="filled", fee=Decimal(fee),
                           expected_fill=Decimal(fill), actual_fill=Decimal(fill),
                           confirmation="autonomous", created_at=1, updated_at=1))
    return repo.open_position(product_id="PAXG-USD", rule_name=rule_name, opened_at=1,
                              qty=Decimal(qty), entry_fill=Decimal(fill), entry_fee=Decimal(fee))


def test_a_declared_close_releases_the_exposure_rails_4_5_6_read(repo) -> None:  # noqa: F811
    pid = _tranche(repo)
    assert guards._open_exposure_by_asset(repo)["PAXG"] > 0

    order_id = close_declared_position(repo, _config(), position_id=pid,
                                       price=Decimal("4400"), fee=Decimal("0.52"), now_ts=NOW_TS)

    assert "PAXG" not in guards._open_exposure_by_asset(repo), "#798: guard 6 must read it"
    row = repo.get_order(order_id)
    assert (row["side"], row["order_type"], row["status"], row["confirmation"]) == (
        "SELL", "out_of_band", "filled", "operator_declared")
    assert repo.get_open_positions("PAXG-USD") == []


def test_the_outcome_is_booked_per_the_tranche_own_kind(repo) -> None:  # noqa: F811
    pid = _tranche(repo, rule_name="dca")
    close_declared_position(repo, _config(), position_id=pid, price=Decimal("4400"),
                            fee=Decimal("0"), now_ts=NOW_TS)
    [outcome] = repo.get_trade_outcomes()
    assert outcome["is_dca"] in (1, True)


def test_the_exit_ownership_state_retires_with_the_last_tranche(repo) -> None:  # noqa: F811
    pid = _tranche(repo)
    repo.set_state("position_rule:PAXG-USD", {"rule_name": "turtle_breakout", "opened_at": 1})
    repo.set_state("unbracketed:PAXG-USD", {"stop": "4521", "target": "5000", "qty": "0.0132"})
    close_declared_position(repo, _config(), position_id=pid, price=Decimal("4400"),
                            fee=Decimal("0"), now_ts=NOW_TS)
    assert repo.get_state("position_rule:PAXG-USD") is None
    assert repo.get_state("unbracketed:PAXG-USD") is None


@pytest.mark.parametrize("price,fee", [(Decimal("0"), Decimal("0")), (Decimal("1"), Decimal("-1"))])
def test_nonsense_is_refused_before_anything_is_written(repo, price, fee) -> None:  # noqa: F811
    pid = _tranche(repo)
    before = len(repo.get_orders())
    with pytest.raises(PositionCloseRefused):
        close_declared_position(repo, _config(), position_id=pid, price=price, fee=fee,
                                now_ts=NOW_TS)
    assert len(repo.get_orders()) == before


def test_an_already_closed_tranche_is_refused(repo) -> None:  # noqa: F811
    pid = _tranche(repo)
    repo.close_position(pid, closed_at=2)
    with pytest.raises(PositionCloseRefused, match="not open"):
        close_declared_position(repo, _config(), position_id=pid, price=Decimal("1"),
                                fee=Decimal("0"), now_ts=NOW_TS)
```

  Use `repo.get_trade_outcomes()` if it exists. Otherwise read the table directly with `repo._conn.execute("SELECT * FROM trade_outcomes")`. Check `keel/data/repository.py` for the reader name first, and use it.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/commands/test_positions_close.py -v`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Write the module docstring first. It states R4, why the verb is not in `positions.py` (that module's "NO CLOSE ACTION, EVER"), and that the order row, not the ledger mutation, is what rails 4, 5 and 6 read.

```python
class PositionCloseRefused(Exception):
    """The declared close cannot be recorded; the message is shown verbatim."""


def close_declared_position(repo, config, *, position_id, price, fee, now_ts) -> int:
    if price <= 0:
        raise PositionCloseRefused(f"--price must be positive, got {price}")
    if fee < 0:
        raise PositionCloseRefused(f"--fee must not be negative, got {fee}")
    position = next((p for p in repo.get_open_positions() if p["id"] == position_id), None)
    if position is None:
        raise PositionCloseRefused(f"tranche {position_id} is not open (see `keel positions`)")
    order_id = repo.insert_order(
        dict(
            mode="live",
            product_id=position["product_id"],
            side=Side.SELL.value,
            order_type="out_of_band",
            qty=position["qty"],
            status="filled",
            fee=fee,
            expected_fill=price,
            actual_fill=price,
            filled_quantity=position["qty"],
            confirmation="operator_declared",
            rule_id=position.get("rule_id"),
            created_at=now_ts,
            updated_at=now_ts,
        )
    )
    streak.record_closed_trade(
        repo, config, product_id=position["product_id"], position=position, exit_fill=price,
        exit_qty=position["qty"], fees=fee, is_dca=position["rule_name"] == "dca", now_ts=now_ts,
    )
    repo.close_position(position_id, closed_at=now_ts)
    if not repo.get_open_positions(position["product_id"]):
        for prefix in ("position_rule:", "open_stop:", "open_target:", executor.UNBRACKETED_PREFIX):
            repo.set_state(f"{prefix}{position['product_id']}", None)
    return order_id
```

- [ ] **Step 4: Run the tests, then commit.** Run: `uv run pytest tests/commands/test_positions_close.py -v`. Expected: PASS.

```bash
git add keel/commands/positions_close.py tests/commands/test_positions_close.py
git commit -m "feat(positions): close_declared_position -- an out-of-band exit written where guard 6 reads (#798)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4.2: The gated CLI verb and its capability row

**Files:**
- Modify: `keel/commands/positions_close.py` (a click group `positions_group` with one command, `close`), `keel/cli.py` (`cli.add_command(positions_group)` beside `dca_group`), `keel/capabilities.py`, `tests/execution/test_sell_side_invariants.py`
- Note: **there is no `keel positions` CLI command today.** The positions report is the web view, `keel/web/api.py` over `commands/positions.gather_positions`. This PR creates the `positions` group with `close` only. A `list` subcommand is not part of #798.
- Test: `tests/commands/test_positions_close.py`, `tests/test_capabilities.py`

**Interfaces:**
- Produces: `keel positions close <id> --price P [--fee F]`. It needs a typed `yes` through `_require_interactive_confirmation("record tranche <id> as closed out-of-band", ...)`, called from the function `positions_close_gate`.

- [ ] **Step 1: Write the failing tests.**

```python
import time
from pathlib import Path

from click.testing import CliRunner

from keel.cli import cli
from keel.commands import _common
from keel.data.db import connect, migrate
from keel.data.repository import Repository


def _file_repo(db: Path) -> Repository:
    conn = connect(str(db))
    migrate(conn)
    return Repository(conn)


def _seeded(tmp_path: Path) -> tuple[Path, int]:
    db = tmp_path / "t.db"
    return db, _tranche(_file_repo(db))


def _close(db: Path, config: Path, pid: int, input: str | None = None):
    return CliRunner().invoke(
        cli,
        ["--db", str(db), "--config", str(config), "positions", "close", str(pid),
         "--price", "4400", "--fee", "0.52"],
        input=input,
    )


def test_off_a_tty_the_close_is_refused_and_nothing_is_written(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    db, pid = _seeded(tmp_path)
    before = len(_file_repo(db).get_orders())

    result = _close(db, valid_config_path, pid)

    assert result.exit_code != 0
    assert "interactive terminal" in result.output
    assert len(_file_repo(db).get_orders()) == before
    assert [p["id"] for p in _file_repo(db).get_open_positions()] == [pid]


def test_at_a_tty_a_typed_yes_records_it(tmp_path, valid_config_path, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, pid = _seeded(tmp_path)

    result = _close(db, valid_config_path, pid, input="yes\n")

    assert result.exit_code == 0, result.output
    sells = [o for o in _file_repo(db).get_orders() if o["order_type"] == "out_of_band"]
    assert len(sells) == 1 and sells[0]["confirmation"] == "operator_declared"
    assert _file_repo(db).get_open_positions() == []


def test_at_a_tty_anything_but_yes_writes_nothing(tmp_path, valid_config_path, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, pid = _seeded(tmp_path)

    result = _close(db, valid_config_path, pid, input="y\n")

    assert result.exit_code != 0
    assert [p["id"] for p in _file_repo(db).get_open_positions()] == [pid]
```

  These tests reuse `_tranche` from Task 4.1's module. Then:
  - In `tests/test_capabilities.py`, update any hard-coded row-count wording (for example "Seven of the nine"). Do this in `keel/capabilities.py`'s docstring too.
  - Add `"close_declared_position"` to `WEB_FORBIDDEN_NAMES`.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/test_capabilities.py tests/commands/test_positions_close.py -v`. Expected:
  - `test_the_inventory_matches_every_gate_call_site_exactly` FAILS, because the new call site is undeclared;
  - the CLI tests FAIL because the command does not exist.

- [ ] **Step 3: Implement the command, and add the row.**

```python
    Capability(
        module="keel.commands.positions_close",
        function="positions_close_gate",
        surface="cli",
        invocation="keel positions close <id> --price P",
        increases=(
            "a tranche keel still counts is recorded as sold out-of-band, so rails 4/5/6 stop "
            "counting its notional -- measured exposure SHRINKS, which is headroom for new entries"
        ),
    ),
```

- [ ] **Step 4: Run the suite, then commit.** The suite must include `tests/web/test_server.py`, whose effect scan now forbids `close_declared_position` in `keel/web`.

```bash
uv run pytest -q
git add keel/ tests/
git commit -m "feat(positions): keel positions close <id>, typed-yes gated, declared in the inventory (#798)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**Acceptance (P4):**
- #798's three proposals are delivered: the close verb here, and the balance reconciliation and doctor check in P3.
- The close writes an order row that guard 6 reads.
- It is gated and inventoried.

---

# Group B — Shared contracts (land before any consumer)

## P5 — `feat(strategy): sell-side contracts -- Holding, Reduction, Action.REDUCE, reduce_signal, book_exit(is_dca=None) (#857)` · M · no schema

**PR body states:**
- S1: no new caller of `_run_order`. The invariants module is unchanged and green.
- S2: no rule kind exists yet.
- S3: unchanged.
- S4: unchanged.
- Nothing here runs on the live cycle. `reduce_signal` defaults to `None` on every existing rule.

### Task 5.1: The value types (R6, R7, R8)

**Files:**
- Create: `keel/strategy/reduction.py`
- Test: `tests/strategy/test_reduction.py` (new)

**Interfaces:**
- Produces:
  - `Lot(position_id: int, rule_name: str, opened_at: int, qty: Decimal, entry_fill: Decimal, entry_fee: Decimal, realized_qty: Decimal = 0)`, with `.entry_fee_share` and `.cost`;
  - `Holding(product_id: str, lots: tuple[Lot, ...], mark: Decimal | None = None)`, with `.qty`, `.cost_basis`, `.vwae -> Decimal | None`, `.unrealised -> Decimal | None`, `.fifo_legs(qty) -> tuple[tuple[Lot, Decimal], ...]`, `.fifo_cost(qty) -> Decimal`, and `Holding.from_rows(product_id, rows, mark=None)`. `from_rows` **keeps the rows' order**, because `get_open_positions` is FIFO by contract and `book_exit` consumes in that same order.
  - `SellCosts(fee_pct: Decimal, slippage_pct: Decimal, fee_source: str)`.
  - `Reduction(product_id: str, qty: Decimal, reason: str, trigger: dict[str, Any], expected_price: Decimal, ts: int)`.

- [ ] **Step 1: Write the failing tests.**

```python
from decimal import Decimal

import pytest

from keel.strategy.reduction import Holding, Lot, Reduction, SellCosts

D = Decimal


def _lot(pid, qty, fill, fee, realized="0", rule="dca", opened=0):
    return Lot(pid, rule, opened, D(qty), D(fill), D(fee), D(realized))


def test_vwae_includes_entry_fees_so_break_even_is_honest() -> None:
    """Spec Q4: 0.0005 BTC @ 100000 with a $0.45 fee costs $50.45, so break-even is 100900."""
    h = Holding("BTC-USD", (_lot(1, "0.0005", "100000", "0.45"),))
    assert h.cost_basis == D("50.45")
    assert h.vwae == D("100900")


def test_a_partly_scaled_out_tranche_prorates_its_entry_fee() -> None:
    """qty is what is STILL held; qty + realized_qty is the original size (#502)."""
    lot = _lot(1, "0.0003", "100000", "0.50", realized="0.0002")
    assert lot.entry_fee_share == D("0.30")
    assert lot.cost == D("30.30")


def test_fifo_legs_consume_the_oldest_lot_first_and_stop_inside_one() -> None:
    old, new = _lot(3, "0.0132", "4673.23", "0.73", rule="turtle_breakout"), _lot(9, "0.01", "4400", "0.4")
    legs = Holding("PAXG-USD", (old, new)).fifo_legs(D("0.015"))
    assert [(lot.position_id, q) for lot, q in legs] == [(3, D("0.0132")), (9, D("0.0018"))]


def test_fifo_legs_never_exceed_what_is_held() -> None:
    h = Holding("BTC-USD", (_lot(1, "0.001", "100000", "0"),))
    assert sum(q for _, q in h.fifo_legs(D("5"))) == D("0.001")


def test_fifo_cost_prorates_each_consumed_lot() -> None:
    h = Holding("BTC-USD", (_lot(1, "0.001", "100000", "1.00"), _lot(2, "0.001", "110000", "1.10")))
    assert h.fifo_cost(D("0.0015")) == D("100") + D("1.00") + D("55") + D("0.55")


def test_an_empty_holding_has_no_average_rather_than_a_zero_one() -> None:
    assert Holding("BTC-USD", ()).vwae is None
    assert Holding("BTC-USD", (), mark=D("1")).unrealised == D("0")
    assert Holding("BTC-USD", (_lot(1, "1", "1", "0"),)).unrealised is None


@pytest.mark.parametrize("qty,price", [(D("0"), D("1")), (D("-1"), D("1")), (D("1"), D("0"))])
def test_a_reduction_refuses_a_non_positive_size_or_price(qty, price) -> None:
    with pytest.raises(ValueError):
        Reduction("BTC-USD", qty, "reverse_dca", {}, price, 0)


@pytest.mark.parametrize("fee,slip", [(D("-0.01"), D("0")), (D("0.6"), D("0.5"))])
def test_sell_costs_refuse_rates_that_make_net_from_gross_undefined(fee, slip) -> None:
    with pytest.raises(ValueError):
        SellCosts(fee, slip, "fallback:config.fees.taker_pct")
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/strategy/test_reduction.py -v`. Expected: `ModuleNotFoundError: keel.strategy.reduction`.

- [ ] **Step 3: Implement.** The module docstring states:
  - spec §3.2 and §3.3;
  - R7 (why these live outside `base.py` and outside `keel.data`);
  - R8 (no `rule_name` filter);
  - that `weight` is deliberately **not** a `Holding` field, because one product cannot know the sleeve total (P13's bands view computes it over several holdings).

```python
@dataclass(frozen=True)
class Lot:
    position_id: int
    rule_name: str
    opened_at: int
    qty: Decimal
    entry_fill: Decimal
    entry_fee: Decimal
    realized_qty: Decimal = Decimal("0")

    @property
    def entry_fee_share(self) -> Decimal:
        original = self.qty + self.realized_qty
        return Decimal("0") if original <= 0 else self.entry_fee * self.qty / original

    @property
    def cost(self) -> Decimal:
        return self.qty * self.entry_fill + self.entry_fee_share


@dataclass(frozen=True)
class Holding:
    product_id: str
    lots: tuple[Lot, ...]
    mark: Decimal | None = None

    @property
    def qty(self) -> Decimal:
        return sum((lot.qty for lot in self.lots), Decimal("0"))

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.cost for lot in self.lots), Decimal("0"))

    @property
    def vwae(self) -> Decimal | None:
        qty = self.qty
        return None if qty <= 0 else self.cost_basis / qty

    @property
    def unrealised(self) -> Decimal | None:
        return None if self.mark is None else self.qty * self.mark - self.cost_basis

    def fifo_legs(self, qty: Decimal) -> tuple[tuple[Lot, Decimal], ...]:
        legs: list[tuple[Lot, Decimal]] = []
        remaining = qty
        for lot in self.lots:
            if remaining <= 0:
                break
            take = min(lot.qty, remaining)
            legs.append((lot, take))
            remaining -= take
        return tuple(legs)

    def fifo_cost(self, qty: Decimal) -> Decimal:
        return sum(
            (take * lot.entry_fill + lot.entry_fee_share * take / lot.qty
             for lot, take in self.fifo_legs(qty) if lot.qty > 0),
            Decimal("0"),
        )

    @classmethod
    def from_rows(
        cls, product_id: str, rows: Iterable[Mapping[str, Any]], mark: Decimal | None = None
    ) -> Holding:
        return cls(
            product_id,
            tuple(
                Lot(
                    position_id=int(r["id"]),
                    rule_name=str(r["rule_name"]),
                    opened_at=int(r["opened_at"]),
                    qty=Decimal(r["qty"]),
                    entry_fill=Decimal(r["entry_fill"]),
                    entry_fee=Decimal(r.get("entry_fee") or 0),
                    realized_qty=Decimal(r.get("realized_qty") or 0),
                )
                for r in rows
            ),
            mark,
        )


@dataclass(frozen=True)
class SellCosts:
    fee_pct: Decimal
    slippage_pct: Decimal
    fee_source: str

    def __post_init__(self) -> None:
        if self.fee_pct < 0 or self.slippage_pct < 0 or self.fee_pct + self.slippage_pct >= 1:
            raise ValueError(
                f"SellCosts: fee {self.fee_pct} + slippage {self.slippage_pct} must be in [0, 1)"
            )


@dataclass(frozen=True)
class Reduction:
    product_id: str
    qty: Decimal
    reason: str
    trigger: dict[str, Any]
    expected_price: Decimal
    ts: int

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"Reduction.qty must be positive, got {self.qty}")
        if self.expected_price <= 0:
            raise ValueError(f"Reduction.expected_price must be positive, got {self.expected_price}")
        if not self.reason:
            raise ValueError("Reduction.reason must name the rule kind (rail 10)")
```

- [ ] **Step 4: Run the tests, then commit.** Run: `uv run pytest tests/strategy/test_reduction.py -v`. Expected: PASS.

```bash
git add keel/strategy/reduction.py tests/strategy/test_reduction.py
git commit -m "feat(strategy): Lot, Holding, SellCosts, Reduction -- the sell side's value types (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5.2: `Action.REDUCE`, `Rule.reduce_signal`, and `SLEEVE_SELL` (R6, R10)

**Files:**
- Modify: `keel/strategy/rules/base.py`, `keel/strategy/promotion.py`, `tests/strategy/rule_conformance.py` (`test_promotion_class_is_a_recognised_value`)
- Test: `tests/strategy/test_base.py`, `tests/strategy/test_promotion.py`

**Interfaces:**
- Produces:
  - `Action.REDUCE = "REDUCE"`;
  - `Rule.reduce_signal(self, holding: Holding, candles_by_tf: dict[Granularity, list[Candle]], costs: SellCosts) -> Reduction | None`, which returns `None` by default;
  - `promotion.SLEEVE_SELL = "sleeve_sell"`;
  - `promotion.RECOGNISED_CLASSES: frozenset[str]`, equal to `{DEFAULT_CLASS, TREND_FOLLOW, SLEEVE_SELL}`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/strategy/test_base.py (append)
from keel.strategy.reduction import Holding, SellCosts


def test_reduce_is_an_action() -> None:
    assert Action.REDUCE.value == "REDUCE"


def test_every_shipped_rule_proposes_no_reduction_by_default() -> None:
    from keel.agent import RULE_REGISTRY, build_rule_from_params

    costs = SellCosts(Decimal("0.012"), Decimal("0.0005"), "fallback:config.fees.taker_pct")
    for kind in ("dca", "turtle_breakout", "pullback_continuation", "rsi_meanrev"):
        rule = build_rule_from_params(kind, {"product_id": "BTC-USD"})
        assert rule.reduce_signal(Holding("BTC-USD", ()), {}, costs) is None, kind
```

```python
# tests/strategy/test_promotion.py (append)
def test_sleeve_sell_is_recognised_and_is_never_a_backtest_floor() -> None:
    assert promotion.SLEEVE_SELL in promotion.RECOGNISED_CLASSES
    assert promotion.SLEEVE_SELL not in promotion._CLASS_FLOORS, (
        "a sleeve-sell rule has no R; it must never be judged by a trade floor (P12 routes it)"
    )
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/strategy/test_base.py tests/strategy/test_promotion.py -k "reduce or sleeve" -v`. Expected: `AttributeError`.

- [ ] **Step 3: Implement.**
  - Add `REDUCE = "REDUCE"` to `Action`. Its comment says a `REDUCE` is never built into a `Signal` or persisted in `signals`: the audit row is `sell_proposals`.
  - Add the method to `Rule`:

```python
    def reduce_signal(
        self,
        holding: Holding,
        candles_by_tf: dict[Granularity, list[Candle]],
        costs: SellCosts,
    ) -> Reduction | None:
        """Pure. A sleeve-sell rule's proposal to sell part of `holding`, or `None` (spec §3.2).

        Default `None`: an ENTRY rule proposes no reduction, so adding this hook changes nothing
        for the rules that exist. A sell-side kind overrides it; `agent._handle_reductions` is its
        only caller. `costs` is what the caller resolved (the fallback rate and per-product
        slippage, plan R6) -- a rule is built from params and cannot read config. The venue's
        previewed fee, when there is one, is recorded by `executor.reduce`, not decided here.
        """
        return None
```

  - In `promotion.py`, add `SLEEVE_SELL` and `RECOGNISED_CLASSES` beside `TREND_FOLLOW`, with a comment that points at P12's gate.
  - Change the conformance test to `known = set(promotion.RECOGNISED_CLASSES)`.
  - Before committing, grep for exhaustive `Action` matches: `grep -rn "Action\.\(ENTER\|EXIT\|NONE\)" keel`. Confirm no `match` or `dict` over the enum needs a new arm.

- [ ] **Step 4: Run the tests, then commit.** Run: `uv run pytest tests/strategy -q`. Expected: PASS.

```bash
git add keel/strategy/rules/base.py keel/strategy/promotion.py tests/strategy/
git commit -m "feat(strategy): Action.REDUCE, Rule.reduce_signal, and the sleeve_sell promotion class (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5.3: `book_exit(is_dca=None)` books each leg by its own tranche (R9, Review Focus 3)

**Files:**
- Modify: `keel/execution/streak.py`, `book_exit` (lines 179–283)
- Test: `tests/execution/test_streak.py` (append)

**Interfaces:**
- Produces: `book_exit(..., is_dca: bool | None, ...)`. `None` derives each leg's flag from `position["rule_name"] == "dca"`.

- [ ] **Step 1: Write the failing test.**

```python
def test_a_mixed_paxg_sale_books_each_leg_by_its_own_tranche(repo, config):
    """#860 / spec Q10: turtle tranche 3 is the oldest row, so FIFO reaches it first. With
    is_dca=None the turtle leg is a RULE outcome (counts toward rail 16) and the DCA leg is not."""
    repo.set_state("consecutive_losses", 0)
    repo.open_position(product_id="PAXG-USD", rule_name="turtle_breakout", opened_at=1,
                       qty=Decimal("0.0132"), entry_fill=Decimal("4673.23"), entry_fee=Decimal("0.73"))
    repo.open_position(product_id="PAXG-USD", rule_name="dca", opened_at=2,
                       qty=Decimal("0.01"), entry_fill=Decimal("4400"), entry_fee=Decimal("0.40"))
    exit_order = {"id": 99, "actual_fill": Decimal("4300"), "fee": Decimal("0.20")}

    streak.book_exit(repo, config, product_id="PAXG-USD", exit_order=exit_order,
                     sold_qty=None, is_dca=None, now_ts=10)

    outcomes = {o["rule_name"]: o for o in repo.get_trade_outcomes()}
    assert bool(outcomes["turtle_breakout"]["is_dca"]) is False
    assert bool(outcomes["dca"]["is_dca"]) is True
    assert repo.get_state("consecutive_losses") == 1, "only the turtle loss counts"


def test_a_bool_flag_still_books_every_leg_with_that_flag(repo, config):
    """R9: existing callers pass a bool and must see today's behaviour byte for byte."""
    repo.open_position(product_id="PAXG-USD", rule_name="turtle_breakout", opened_at=1,
                       qty=Decimal("1"), entry_fill=Decimal("10"), entry_fee=Decimal("0"))
    repo.open_position(product_id="PAXG-USD", rule_name="dca", opened_at=2,
                       qty=Decimal("1"), entry_fill=Decimal("10"), entry_fee=Decimal("0"))
    streak.book_exit(repo, config, product_id="PAXG-USD",
                     exit_order={"id": 1, "actual_fill": Decimal("9"), "fee": Decimal("0")},
                     sold_qty=None, is_dca=False, now_ts=10)
    assert all(not bool(o["is_dca"]) for o in repo.get_trade_outcomes())
```

  Use the trade-outcomes reader and the `repo` and `config` fixtures that `tests/execution/test_streak.py` already defines. Read the module's top first, and adapt the fixture names to it if they differ.

- [ ] **Step 2: Run the test to see it fail.** Run: `uv run pytest tests/execution/test_streak.py -k "mixed or bool_flag" -v`. Expected: the first test FAILS on `record_closed_trade(is_dca=None)`, because `None` is falsy and both legs book `is_dca=0`.

- [ ] **Step 3: Implement.**
  - Change the annotation to `is_dca: bool | None`.
  - In the leg loop, compute `leg_is_dca = (position.get("rule_name") == "dca") if is_dca is None else is_dca`, and pass `is_dca=leg_is_dca` to `record_closed_trade`.
  - Extend the docstring with a paragraph that cites spec §3.2 and #860, and says the existing single-flag callers are deliberately untouched.

- [ ] **Step 4: Run the tests, then commit.** Run: `uv run pytest tests/execution/test_streak.py tests/execution/test_scale_out.py tests/test_agent.py -q`. Expected: PASS.

```bash
git add keel/execution/streak.py tests/execution/test_streak.py
git commit -m "feat(streak): book_exit(is_dca=None) books each FIFO leg by its own tranche (#857, #860)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5.4: `sleeve.holding_of`, pinned to `ledger_qty`

**Files:**
- Modify: `keel/execution/sleeve.py`
- Test: `tests/execution/test_sleeve.py` (append)

**Interfaces:**
- Produces: `sleeve.holding_of(repo, product_id: str, mark: Decimal | None = None) -> Holding`.

- [ ] **Step 1: Write the failing test.**

```python
def test_holding_of_is_the_ledger_and_its_qty_is_doctors_ledger_qty(repo) -> None:
    repo.open_position(product_id="BTC-USD", rule_name="dca", opened_at=1, qty=Decimal("0.0005"),
                       entry_fill=Decimal("100000"), entry_fee=Decimal("0.45"))
    repo.open_position(product_id="BTC-USD", rule_name="turtle_breakout", opened_at=2,
                       qty=Decimal("0.0004"), entry_fill=Decimal("110000"), entry_fee=Decimal("0.5"))
    held = sleeve.holding_of(repo, "BTC-USD", mark=Decimal("120000"))
    assert held.qty == sleeve.ledger_qty(repo.get_open_positions("BTC-USD"))
    assert [lot.rule_name for lot in held.lots] == ["dca", "turtle_breakout"], "no rule filter, FIFO"
    assert held.mark == Decimal("120000")
```

- [ ] **Step 2: Run the test to see it fail.** Run: `uv run pytest tests/execution/test_sleeve.py -v`. Expected: `AttributeError: holding_of`.

- [ ] **Step 3: Implement.** Add `def holding_of(repo, product_id, mark=None) -> Holding: return Holding.from_rows(product_id, repo.get_open_positions(product_id), mark)`, with a docstring that cites spec §3.3 and R8.

- [ ] **Step 4: Run the tests, the suite, then commit.**

```bash
uv run pytest -q && uv run mypy
git add keel/execution/sleeve.py tests/execution/test_sleeve.py
git commit -m "feat(sleeve): holding_of -- the Holding over the positions ledger, no rule filter (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P6 — `feat(data): sell_proposals, schema v22, and keel dca proposals list/show (#857)` · M · **schema v22**

**PR body states:**
- S1–S3 are unchanged.
- S4: `insert_sell_proposal` and `update_sell_proposal` join `WEB_FORBIDDEN_NAMES`.
- **Schema v22.** It adds the `sell_proposals` table and its index. There is no backfill.

### Task 6.1: The v22 migration

**Files:**
- Modify: `keel/data/db.py`: add the DDL after `positions`, `_migrate_v22_sell_proposals`, `_MIGRATIONS[22]`, and `SCHEMA_VERSION = 22`. Add `sell_proposals` to the module docstring's table list.
- Test: `tests/data/test_migrations.py`. Append the v22 tests, and relax **all eight** `== 21` assertions (lines 52, 627, 788, 890, 1050, 1097, 1168, 1316 — see Global Constraints). All eight share the exact literal `== db.SCHEMA_VERSION == 21` (`assert version == db.SCHEMA_VERSION == 21` at line 52, `assert stamped == db.SCHEMA_VERSION == 21` at the other seven), so `sed -i '' 's/== db\.SCHEMA_VERSION == 21/== db.SCHEMA_VERSION/' tests/data/test_migrations.py` fixes all eight in one pass. Confirm with `grep -n '== 21' tests/data/test_migrations.py` that no hit remains.
- Also modify `tests/data/test_db.py` (bump the deliberate tripwire) and `tests/data/test_trade_outcomes.py` (relax the ordinary pin) — see Global Constraints for why these two are treated differently. Confirm afterward with `grep -rn "SCHEMA_VERSION == 21\|schema_version_is_21" tests/` that the only remaining `21` literal in a schema-version context is the one this task deliberately leaves for P17 to bump (there is none — P6 leaves the tripwire at `22`).

**Interfaces:**
- Produces: the `sell_proposals` columns `id, ts, product_id, rule_id, rule_kind, rule_status, qty, expected_price, vwae, cost_basis, expected_gross, expected_fee, fee_source, expected_net_pnl, legs, trigger, rails, decision, superseded_by, order_id, reviewed_ts`.

- [ ] **Step 1: Write the failing tests.**

```python
# -- v22: sell_proposals (#857) ----------------------------------------------------------------

_V22_COLUMNS = {
    "id", "ts", "product_id", "rule_id", "rule_kind", "rule_status", "qty", "expected_price",
    "vwae", "cost_basis", "expected_gross", "expected_fee", "fee_source", "expected_net_pnl",
    "legs", "trigger", "rails", "decision", "superseded_by", "order_id", "reviewed_ts",
}


def test_migration_to_v22_creates_sell_proposals() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sell_proposals)")}
    assert columns == _V22_COLUMNS
    assert db.SCHEMA_VERSION >= 22


def test_a_v21_database_gains_an_empty_sell_proposals_table() -> None:
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (21)")
    conn.commit()
    db.migrate(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM sell_proposals").fetchone()["n"] == 0
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == db.SCHEMA_VERSION


def test_v22_is_idempotent() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    db._migrate_v22_sell_proposals(conn)
    db.migrate(conn)
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/data/test_migrations.py -k v22 -v`. Expected: FAIL. The table is absent, and `SCHEMA_VERSION == 21`. Then run the full file, `uv run pytest tests/data/test_migrations.py -q`: the eight tests pinned to the literal `21` (lines 52, 627, 788, 890, 1050, 1097, 1168, 1316) fail too, once `SCHEMA_VERSION` becomes 22 below — that is expected, and Step 3 relaxes them in the same commit. Also run `uv run pytest tests/data/test_db.py -k schema_version -v` and `uv run pytest tests/data/test_trade_outcomes.py -k version -v`: both fail the same way (`SCHEMA_VERSION == 21` is now `22`) — Step 3 fixes both in the same commit.

- [ ] **Step 3: Implement.** First relax the eight pre-existing `== 21` pins so the whole file can go green on v22, not just the new tests:

```bash
sed -i '' 's/== db\.SCHEMA_VERSION == 21/== db.SCHEMA_VERSION/' tests/data/test_migrations.py
grep -n '== 21' tests/data/test_migrations.py  # expect no output
```

  Then handle the two pins outside `test_migrations.py`, each differently (see Global Constraints):

```bash
# tests/data/test_db.py: the deliberate tripwire -- bump the literal, don't relax it, and
# rename so the function name still matches the version it pins.
sed -i '' \
  -e 's/def test_schema_version_is_21/def test_schema_version_is_22/' \
  -e 's/assert SCHEMA_VERSION == 21/assert SCHEMA_VERSION == 22/' \
  tests/data/test_db.py

# tests/data/test_trade_outcomes.py: an ordinary pin -- relax it like the eight above, and
# rename it so it doesn't read as still asserting 21.
sed -i '' \
  -e 's/def test_schema_is_at_version_21/def test_schema_is_at_the_current_version/' \
  -e 's/assert version == db\.SCHEMA_VERSION == 21/assert version == db.SCHEMA_VERSION/' \
  tests/data/test_trade_outcomes.py

grep -rn "SCHEMA_VERSION == 21\|schema_version_is_21\|schema_is_at_version_21" tests/  # expect no output
```

  Then add the DDL:

```python
    # One row per `Reduction` a sleeve-sell rule proposed, EXECUTED OR NOT (spec §3.8). The
    # proposal is the product; the fill is optional -- in this build nothing is placed, so every
    # row is `preview`, `vetoed` or `superseded`. Money TEXT, timestamps INTEGER. `rule_id` has no
    # FK for `positions.rule_id`'s parity reason is NOT needed here -- this is a NEW table, so
    # fresh and migrated databases get the same DDL -- but it is still left unconstrained so a
    # deleted rule row cannot orphan the audit record of what it proposed.
    """
    CREATE TABLE IF NOT EXISTS sell_proposals (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ts               INTEGER NOT NULL,
        product_id       TEXT    NOT NULL,
        rule_id          INTEGER,
        rule_kind        TEXT    NOT NULL,
        rule_status      TEXT    NOT NULL,
        qty              TEXT    NOT NULL,
        expected_price   TEXT    NOT NULL,
        vwae             TEXT,
        cost_basis       TEXT,
        expected_gross   TEXT    NOT NULL,
        expected_fee     TEXT    NOT NULL,
        fee_source       TEXT    NOT NULL,
        expected_net_pnl TEXT,
        legs             INTEGER NOT NULL DEFAULT 1,
        trigger          TEXT    NOT NULL,
        rails            TEXT    NOT NULL,
        decision         TEXT    NOT NULL,
        superseded_by    TEXT,
        order_id         INTEGER,
        reviewed_ts      INTEGER,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sell_proposals_product_ts ON sell_proposals (product_id, ts)",
```

```python
def _migrate_v22_sell_proposals(conn: sqlite3.Connection) -> None:
    """v22 adds `sell_proposals` (#857). Table creation is handled by `_SCHEMA_STATEMENTS`;
    there is deliberately NO backfill.

    A row asserts that a sleeve-sell rule evaluated the book at a moment and proposed a sale,
    with the rails' answer and the fee it would have paid. No such evaluation happened before
    this version, so there is nothing true to seed; an empty table correctly says no rule has
    proposed anything yet.
    """
```

- [ ] **Step 4: Run the tests, the smoke test, then commit.** Run: `uv run pytest tests/data/test_migrations.py tests/data/test_db.py tests/data/test_trade_outcomes.py tests/test_migration_smoke.py tests/test_first_run_wizard.py tests/test_cli.py -k "migrat or schema" -q`. Expected: PASS.

```bash
git add keel/data/db.py tests/data/test_migrations.py tests/data/test_db.py tests/data/test_trade_outcomes.py
git commit -m "feat(db): v22 -- the sell_proposals table (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6.2: Repository methods and audit events

**Files:**
- Modify: `keel/data/repository.py`, `keel/data/audit.py` (`EVENT_STORES`), `tests/execution/test_sell_side_invariants.py`
- Test: `tests/data/test_sell_proposals.py` (new)

**Interfaces:**
- Produces:
  - `insert_sell_proposal(row: dict[str, Any]) -> int`, which writes the row and a `sell_proposal_recorded` audit event in one `write_transaction`;
  - `update_sell_proposal(proposal_id: int, **fields) -> None`, which writes a `sell_proposal_updated` event;
  - `get_sell_proposal(proposal_id) -> dict | None`;
  - `get_sell_proposals(*, product_id=None, rule_id=None, since_ts=None, limit=None) -> list[dict]`, newest first, returning `[]` when the table is absent (the `table_present` idiom, #751).

  Every reader decodes money to `Decimal` and `trigger` and `rails` to `dict`.

- [ ] **Step 1: Write the failing tests.**

```python
from decimal import Decimal

from keel.data.db import connect, migrate
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _row(**over):
    base = dict(ts=100, product_id="BTC-USD", rule_id=7, rule_kind="reverse_dca",
                rule_status="live", qty=Decimal("0.00092"), expected_price=Decimal("110000"),
                vwae=Decimal("100900"), cost_basis=Decimal("92.83"),
                expected_gross=Decimal("101.15"), expected_fee=Decimal("1.21"),
                fee_source="venue_preview", expected_net_pnl=Decimal("7.11"), legs=1,
                trigger={"cadence_day": 20_000}, rails={"violations": [], "skipped": []},
                decision="preview")
    base.update(over)
    return base


def test_a_proposal_round_trips_with_money_as_decimal_and_json_as_dict() -> None:
    repo = _repo()
    pid = repo.insert_sell_proposal(_row())
    got = repo.get_sell_proposal(pid)
    assert got["qty"] == Decimal("0.00092") and isinstance(got["expected_fee"], Decimal)
    assert got["trigger"] == {"cadence_day": 20_000}
    assert got["reviewed_ts"] is None and got["order_id"] is None


def test_every_write_is_chained_in_the_audit_log() -> None:
    repo = _repo()
    pid = repo.insert_sell_proposal(_row())
    repo.update_sell_proposal(pid, reviewed_ts=200)
    kinds = [e.event_type for e in repo.audit_chain().events if e.entity_id == str(pid)]
    assert kinds[-2:] == ["sell_proposal_recorded", "sell_proposal_updated"]


def test_newest_first_and_filterable() -> None:
    repo = _repo()
    a = repo.insert_sell_proposal(_row(ts=1))
    b = repo.insert_sell_proposal(_row(ts=2, product_id="PAXG-USD"))
    assert [p["id"] for p in repo.get_sell_proposals()] == [b, a]
    assert [p["id"] for p in repo.get_sell_proposals(product_id="BTC-USD")] == [a]
    assert [p["id"] for p in repo.get_sell_proposals(since_ts=2)] == [b]


def test_an_unmigrated_database_reads_as_no_proposals() -> None:
    conn = connect(":memory:")
    assert Repository(conn).get_sell_proposals() == []
```

  Before writing, check how `ChainState` exposes events in `keel/data/audit.py` (`chain_state`), and read the events through that API. If `audit_chain()` does not expose a list, use `keel.data.audit`'s event reader that `tests/data/test_audit_chain.py` uses.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/data/test_sell_proposals.py -v`. Expected: `AttributeError: insert_sell_proposal`.

- [ ] **Step 3: Implement.**
  - The writer follows `insert_order`'s shape: one `write_transaction`, then `append_event`. Money goes through `_dec_to_text`, and `trigger` and `rails` through `json.dumps(..., default=str, sort_keys=True)`.
  - Add `"sell_proposal_recorded": "sell_proposals"` and `"sell_proposal_updated": "sell_proposals"` to `EVENT_STORES`, with a comment citing spec §3.8. The spec names these `sleeve.proposal` and `sleeve.placed`; the vocabulary here follows the chain's existing `<store>_<verb>` form.
  - Add both writer names to `WEB_FORBIDDEN_NAMES`.

- [ ] **Step 4: Run the tests, then commit.** Run: `uv run pytest tests/data -q tests/execution/test_sell_side_invariants.py`. Expected: PASS.

```bash
git add keel/data/repository.py keel/data/audit.py tests/
git commit -m "feat(data): sell_proposals reads and writes, audit-chained (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6.3: `keel dca proposals list` and `keel dca proposals show <id>` (read-only)

**Files:**
- Modify: `keel/commands/dca.py` (a `proposals` subgroup, and an updated group docstring)
- Create: `keel/commands/sleeve_report.py` (`render_proposal(row) -> list[str]`)
- Test: `tests/commands/test_dca_proposals_cli.py` (new)

**Interfaces:**
- Produces: `sleeve_report.render_proposal(row: dict) -> list[str]`, which P8's notification message and P18's confirm banner reuse.

- [ ] **Step 1: Write the failing tests.**

```python
def test_list_prints_newest_first_and_writes_nothing(deployment, monkeypatch) -> None:
    db, config = deployment
    repo = _repo(db)
    repo.insert_sell_proposal(_row(ts=1))
    repo.insert_sell_proposal(_row(ts=2, decision="vetoed", rails={"violations": ["kill_switch: engaged"]}))
    watcher = sqlite3.connect(str(db))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]

    result = CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", "proposals", "list"])

    assert result.exit_code == 0, result.output
    lines = [l for l in result.output.splitlines() if l.startswith("#")]
    assert lines[0].startswith("#2 ") and "vetoed" in lines[0] and lines[1].startswith("#1 ")
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before


def test_show_prints_the_fee_source_and_the_rails(deployment) -> None:
    db, config = deployment
    pid = _repo(db).insert_sell_proposal(_row(fee_source="fallback:config.fees.taker_pct"))
    out = CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", "proposals",
                                   "show", str(pid)]).output
    assert "fallback:config.fees.taker_pct" in out and "legs: 1" in out
```

  Reuse `deployment` and `_repo` from `tests/commands/test_dca_cli.py`, and `_row` from `tests/data/test_sell_proposals.py`.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/commands/test_dca_proposals_cli.py -v`. Expected: `No such command 'proposals'`.

- [ ] **Step 3: Implement.**
  - Open the database with `_common._open_repo_ro(ctx)`, always. These commands never write, and R20's read-only opener refuses a stale schema with its own message.
  - `render_proposal` prints this format:
    `#<id> <YYYY-MM-DD> <product> <rule_kind> (rule <id>, <status>) sell <qty> @ <price>  gross $<g>  fee $<f> (<source>)  net $<n>  legs <k>  -> <decision>`
  - It appends `superseded by <kind>` and `reviewed` when they are set. Money is formatted with the existing `_money` helper in `doctor.py`, which you import.

- [ ] **Step 4: Run the tests, then commit.**

```bash
git add keel/commands/dca.py keel/commands/sleeve_report.py tests/commands/test_dca_proposals_cli.py
git commit -m "feat(dca): keel dca proposals list/show -- the proposals log, read-only (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Group C — The proposal pipeline (preview only)

## P7 — `feat(execution): executor.reduce in preview -- rails, venue preview, sleeve caps, rail-2 slicing (#857)` · M · no schema

**PR body states:**
- **S1:** `reduce` calls neither `_run_order`, `insert_order`, `place_order` nor `_clear_resting_bracket`. The pinned set test proves the first. This PR's tests prove the others with a spy broker and a seeded pending bracket.
- **S2:** there is no execution value but preview.
- **S3:** proven here (Task 7.2).
- **S4:** unchanged.

### Task 7.1: Sleeve costs, slicing, refusals and the one proposal writer (R11–R15)

**Files:**
- Modify: `keel/execution/sleeve.py`
- Test: `tests/execution/test_sleeve.py` (append)

**Interfaces:**
- Produces:
  - `sleeve.FALLBACK_FEE_SOURCE = "fallback:config.fees.taker_pct"`;
  - `sleeve.sell_costs(repo, config, product_id) -> SellCosts`;
  - `sleeve.slice_qty(qty, price, *, max_per_order_usd, base_increment) -> tuple[Decimal, int]`, which returns `(leg_qty, legs)`, where `(0, 0)` means no leg can be expressed;
  - `sleeve.proposed_today(repo, product_id, now_ts) -> bool`;
  - `sleeve.last_proposal_ts(repo, rule_id) -> int | None`;
  - `sleeve.sleeve_refusal(*, reduction, holding, rule_kind, rule_params, dca_fires_today, last_rule_proposal_ts, now_ts) -> str | None`, which returns one of `SAME_DAY_DCA`, `MIN_HOLD`, `COOLDOWN` or `None`;
  - `sleeve.record_proposal(repo, *, reduction, rule_id, rule_status, holding, costs, decision, rails, expected_fee, fee_source, legs, now_ts, superseded_by=None) -> int`;
  - the constants `DEFAULT_MIN_HOLD_DAYS = 30`, `MIN_HOLD_EXEMPT_KINDS = frozenset({"sleeve_exit"})`, and `ARBITRATION_ORDER = ("sleeve_exit", "reverse_dca", "profit_take", "band_rebalance", "rotation")`.

- [ ] **Step 1: Write the failing tests.**

```python
D = Decimal
DAY = 86_400


def test_slice_keeps_every_leg_under_the_per_order_cap_and_counts_the_days() -> None:
    """Rail 2 is a slicing obligation (spec §3.4, Q3): $450 of BTC at a $200 cap is 3 legs."""
    leg, legs = sleeve.slice_qty(D("0.0045"), D("100000"), max_per_order_usd=D("200"),
                                 base_increment=D("0.00000001"))
    assert leg == D("0.002") and legs == 3
    assert leg * D("100000") <= D("200")


def test_slice_floors_to_the_increment_and_reports_unexpressible_as_zero() -> None:
    assert sleeve.slice_qty(D("0.00123456789"), D("1"), max_per_order_usd=D("1000"),
                            base_increment=D("0.0001")) == (D("0.0012"), 1)
    assert sleeve.slice_qty(D("0.00001"), D("1"), max_per_order_usd=D("1000"),
                            base_increment=D("0.0001")) == (D("0"), 0)


def _h(*opened_days):
    return Holding("BTC-USD", tuple(
        Lot(i, "dca", d * DAY, D("0.001"), D("100000"), D("0")) for i, d in enumerate(opened_days)))


def test_min_hold_reads_the_consumed_tranches_not_the_newest(repo) -> None:
    """R12: under a weekly DCA the newest tranche is always young; FIFO sells the OLD one."""
    red = Reduction("BTC-USD", D("0.001"), "reverse_dca", {}, D("110000"), 100 * DAY)
    assert sleeve.sleeve_refusal(reduction=red, holding=_h(10, 97), rule_kind="reverse_dca",
                                 rule_params={}, dca_fires_today=False, last_rule_proposal_ts=None,
                                 now_ts=100 * DAY) is None
    assert sleeve.sleeve_refusal(reduction=red, holding=_h(80, 97), rule_kind="reverse_dca",
                                 rule_params={}, dca_fires_today=False, last_rule_proposal_ts=None,
                                 now_ts=100 * DAY) == sleeve.MIN_HOLD


def test_sleeve_exit_is_exempt_from_min_hold_but_not_from_the_dca_day() -> None:
    red = Reduction("BTC-USD", D("0.002"), "sleeve_exit", {}, D("60000"), 100 * DAY)
    kw = dict(reduction=red, holding=_h(97, 99), rule_kind="sleeve_exit", rule_params={},
              last_rule_proposal_ts=None, now_ts=100 * DAY)
    assert sleeve.sleeve_refusal(dca_fires_today=False, **kw) is None
    assert sleeve.sleeve_refusal(dca_fires_today=True, **kw) == sleeve.SAME_DAY_DCA


def test_cooldown_is_the_rules_own_param() -> None:
    red = Reduction("BTC-USD", D("0.001"), "profit_take", {}, D("130000"), 100 * DAY)
    assert sleeve.sleeve_refusal(reduction=red, holding=_h(10), rule_kind="profit_take",
                                 rule_params={"cooldown_days": 30}, dca_fires_today=False,
                                 last_rule_proposal_ts=80 * DAY, now_ts=100 * DAY) == sleeve.COOLDOWN


def test_sell_costs_use_the_configured_fallback_never_a_literal(repo) -> None:
    costs = sleeve.sell_costs(repo, _config(), "BTC-USD")
    assert costs.fee_pct == _config().fees.taker_pct
    assert costs.fee_source == sleeve.FALLBACK_FEE_SOURCE


def test_record_proposal_computes_gross_and_fifo_net(repo) -> None:
    red = Reduction("BTC-USD", D("0.001"), "reverse_dca", {"k": 1}, D("110000"), 5)
    held = Holding("BTC-USD", (Lot(1, "dca", 0, D("0.002"), D("100000"), D("0.60")),))
    costs = SellCosts(D("0.012"), D("0.0005"), sleeve.FALLBACK_FEE_SOURCE)
    pid = sleeve.record_proposal(repo, reduction=red, rule_id=7, rule_status="live", holding=held,
                                 costs=costs, decision="preview", rails={"violations": []},
                                 expected_fee=D("1.32"), fee_source="venue_preview", legs=1, now_ts=9)
    row = repo.get_sell_proposal(pid)
    assert row["expected_gross"] == D("0.001") * D("110000") * (1 - D("0.0005"))
    assert row["expected_net_pnl"] == row["expected_gross"] - D("1.32") - (D("100") + D("0.30"))
    assert sleeve.proposed_today(repo, "BTC-USD", 9)
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/execution/test_sleeve.py -v`. Expected: `AttributeError` on each new name.

- [ ] **Step 3: Implement.**

```python
FALLBACK_FEE_SOURCE = "fallback:config.fees.taker_pct"
VENUE_FEE_SOURCE = "venue_preview"
SAME_DAY_DCA = "same_day_dca"
MIN_HOLD = "min_hold_days"
COOLDOWN = "cooldown_days"
DEFAULT_MIN_HOLD_DAYS = 30
MIN_HOLD_EXEMPT_KINDS = frozenset({"sleeve_exit"})
#: Spec §3.6, fixed and not configurable. `band_rebalance`/`rotation` are NOT built (spec §5, §8);
#: they are named so rail 10's vocabulary and this order are decided once.
ARBITRATION_ORDER = ("sleeve_exit", "reverse_dca", "profit_take", "band_rebalance", "rotation")
_DAY = 86_400


def sell_costs(repo: Any, config: Any, product_id: str) -> SellCosts:
    from keel.commands.rules import backtest_slippage  # lazy: keeps click out of the cycle import

    slippage, _measured = backtest_slippage(repo, product_id)
    return SellCosts(config.fees.taker_pct, slippage, FALLBACK_FEE_SOURCE)


def slice_qty(
    qty: Decimal, price: Decimal, *, max_per_order_usd: Decimal, base_increment: Decimal | None
) -> tuple[Decimal, int]:
    cap_qty = max_per_order_usd / price
    leg = min(qty, cap_qty)
    if base_increment is not None and base_increment > 0:
        leg = (leg / base_increment).to_integral_value(rounding=ROUND_FLOOR) * base_increment
    if leg <= 0:
        return Decimal("0"), 0
    legs = int((qty / leg).to_integral_value(rounding=ROUND_CEILING))
    return leg, legs


def proposed_today(repo: Any, product_id: str, now_ts: int) -> bool:
    start = now_ts - now_ts % _DAY
    return any(
        p["decision"] != "superseded"
        for p in repo.get_sell_proposals(product_id=product_id, since_ts=start)
    )


def last_proposal_ts(repo: Any, rule_id: int | None) -> int | None:
    if rule_id is None:
        return None
    rows = [p for p in repo.get_sell_proposals(rule_id=rule_id) if p["decision"] != "superseded"]
    return int(rows[0]["ts"]) if rows else None


def sleeve_refusal(*, reduction, holding, rule_kind, rule_params, dca_fires_today,
                   last_rule_proposal_ts, now_ts) -> str | None:
    if dca_fires_today:
        return SAME_DAY_DCA
    if rule_kind not in MIN_HOLD_EXEMPT_KINDS:
        min_hold = int(rule_params.get("min_hold_days", DEFAULT_MIN_HOLD_DAYS))
        if any(now_ts - lot.opened_at < min_hold * _DAY
               for lot, _ in holding.fifo_legs(reduction.qty)):
            return MIN_HOLD
    cooldown = int(rule_params.get("cooldown_days", 0))
    if cooldown and last_rule_proposal_ts is not None and \
            now_ts - last_rule_proposal_ts < cooldown * _DAY:
        return COOLDOWN
    return None


def record_proposal(repo, *, reduction, rule_id, rule_status, holding, costs, decision, rails,
                    expected_fee, fee_source, legs, now_ts, superseded_by=None) -> int:
    gross = reduction.qty * reduction.expected_price * (Decimal("1") - costs.slippage_pct)
    basis = holding.fifo_cost(reduction.qty)
    return repo.insert_sell_proposal(
        dict(ts=now_ts, product_id=reduction.product_id, rule_id=rule_id,
             rule_kind=reduction.reason, rule_status=rule_status, qty=reduction.qty,
             expected_price=reduction.expected_price, vwae=holding.vwae,
             cost_basis=holding.cost_basis, expected_gross=gross, expected_fee=expected_fee,
             fee_source=fee_source, expected_net_pnl=gross - expected_fee - basis, legs=legs,
             trigger=reduction.trigger, rails=rails, decision=decision,
             superseded_by=superseded_by)
    )
```

  The module docstring gains one paragraph per rule: R11, R12, R13, R14, R15 and §3.6. Each paragraph names the ruling it encodes.

- [ ] **Step 4: Run the tests, then commit.**

```bash
uv run pytest tests/execution/test_sleeve.py -q
git add keel/execution/sleeve.py tests/execution/test_sleeve.py
git commit -m "feat(sleeve): costs, rail-2 slicing, the sleeve caps and the one proposal writer (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7.2: `executor.reduce`, preview only (R17, S1, S3, Review Focus 4)

**Files:**
- Modify: `keel/execution/executor.py`. Add `ReduceResult` and `reduce` after `scale_out`'s section, and extend the module docstring with a "**Sleeve reductions (preview)**" paragraph.
- Test: `tests/execution/test_reduce.py` (new)

**Interfaces:**
- Consumes: everything in Task 7.1.
- Produces:
  - `ReduceResult(product_id: str, rule_kind: str, proposal_id: int | None, decision: str, vetoed_by: list[str], legs: int, reason: str)`, frozen;
  - `executor.reduce(reduction, *, broker, repo, config, holding, costs, rule_id, rule_status, now_ts, offline=False) -> ReduceResult`.

- [ ] **Step 1: Write the failing tests.**

```python
"""executor.reduce in preview: the rails decide, the venue quotes, nothing is placed."""

from __future__ import annotations

from decimal import Decimal

from keel_broker_api.port import TradeScopeDenied

from keel.execution import executor, guards, sleeve
from keel.execution.guards import OrderIntent
from keel.strategy.reduction import Holding, Lot, Reduction, SellCosts
from keel.types import Side
from tests.execution.test_executor import (  # noqa: F401
    NOW_TS, FakeBroker, NoNetworkBroker, _PreviewRefusingBroker, _config, repo,
)
from keel.config import Caps

D = Decimal
COSTS = SellCosts(D("0.012"), D("0.0005"), sleeve.FALLBACK_FEE_SOURCE)


def _held(repo, qty="0.002"):
    repo.open_position(product_id="BTC-USD", rule_name="dca", opened_at=0, qty=D(qty),
                       entry_fill=D("100000"), entry_fee=D("0.6"))
    return sleeve.holding_of(repo, "BTC-USD")


def _red(qty="0.001", price="110000"):
    return Reduction("BTC-USD", D(qty), "reverse_dca", {"cadence_day": 1}, D(price), NOW_TS)


def _run(repo, broker, config=None, **kw):
    return executor.reduce(_red(**kw.pop("red", {})), broker=broker, repo=repo,
                           config=config or _config(), holding=_held(repo), costs=COSTS,
                           rule_id=7, rule_status="live", now_ts=NOW_TS, **kw)


def test_preview_quotes_at_the_venue_records_and_places_nothing(repo) -> None:  # noqa: F811
    # A resting SELL bracket on the product: a SELL path would cancel it. Preview must not.
    repo.insert_order(dict(mode="live", product_id="BTC-USD", side="SELL", order_type="market",
                           qty=D("0.002"), status="pending", raw_response='{"order_id": "b-1"}',
                           confirmation="autonomous", created_at=1, updated_at=1))
    broker = FakeBroker()

    result = _run(repo, broker)

    assert result.decision == "preview" and result.proposal_id is not None
    assert len(broker.preview_calls) == 1
    assert broker.place_calls == [] and broker.cancel_calls == [], "S1: nothing reaches the book"
    row = repo.get_sell_proposal(result.proposal_id)
    assert row["fee_source"] == sleeve.VENUE_FEE_SOURCE and row["expected_fee"] == D("0.30")
    assert [o["status"] for o in repo.get_orders() if o["side"] == "SELL"] == ["pending"]


def test_rail_12_vetoes_a_reduction_and_the_venue_is_never_asked(repo) -> None:  # noqa: F811
    repo.set_state("kill_switch", True)
    result = _run(repo, NoNetworkBroker())
    assert result.decision == "vetoed"
    assert any(v.startswith("kill_switch") for v in result.vetoed_by)
    assert repo.get_sell_proposal(result.proposal_id)["rails"]["violations"] == result.vetoed_by


def test_rail_21_vetoes_when_the_venue_affirms_a_zero_holding(repo) -> None:  # noqa: F811
    broker = FakeBroker(balances={"USD": D("1"), "USDC": D("1"), "BTC": D("0")})
    assert any(v.startswith("base_balance") for v in _run(repo, broker).vetoed_by)


def test_rail_2_is_sliced_not_vetoed_and_the_legs_are_recorded(repo) -> None:  # noqa: F811
    config = _config(caps=Caps(max_exposure_usd=D("1000000"), max_per_asset_pct=D("1"),
                               max_per_order_usd=D("50")))
    result = _run(repo, FakeBroker(), config=config, red={"qty": "0.0015"})
    assert result.decision == "preview" and result.legs == 4
    assert repo.get_sell_proposal(result.proposal_id)["qty"] * D("110000") <= D("50")


def test_buy_scoped_rails_13_17_20_22_do_not_veto_a_sell(repo, monkeypatch) -> None:  # noqa: F811
    """Rails 13 and 17 read `intent.available_quote` and `intent.withdrawals_enabled`, and both
    are `if is_buy` gated in `guards.py` -- neither is even EVALUATED for a SELL, let alone
    vetoing one. A `repo.set_state("withdrawals_enabled", False)` / no-quote-balance broker
    setup therefore never reaches either rail: it asserts nothing about them, only that
    `decision == "preview"` for unrelated reasons. What actually pins "these two are buy-only"
    is that `reduce`'s SELL intent carries `available_quote`/`withdrawals_enabled` as `None` --
    their dataclass defaults, since `reduce` never sets either -- which is exactly the value
    that WOULD veto if a rail wrongly applied to sells read it (both rails fail closed on
    `None`, per their own comments above)."""
    captured: dict[str, OrderIntent] = {}
    real_check = guards.check

    def _spy(intent, *a, **kw):
        captured["intent"] = intent
        return real_check(intent, *a, **kw)

    monkeypatch.setattr(guards, "check", _spy)
    repo._conn.execute("DELETE FROM venue_trade_scopes")   # rail 20
    repo._conn.execute("DELETE FROM venue_cash_postures")  # rail 22
    result = _run(repo, FakeBroker())
    assert result.decision == "preview", "spec §3.4: these rails are buy-only"
    assert captured["intent"].available_quote is None
    assert captured["intent"].withdrawals_enabled is None


def test_a_preview_that_raises_records_the_proposal_on_the_fallback_fee(repo) -> None:  # noqa: F811
    for exc in (TradeScopeDenied("403 read-only"), TimeoutError("read timed out")):
        repo._conn.execute("DELETE FROM sell_proposals")
        result = _run(repo, _PreviewRefusingBroker(exc))
        row = repo.get_sell_proposal(result.proposal_id)
        assert result.decision == "preview"
        assert row["fee_source"] == sleeve.FALLBACK_FEE_SOURCE
        assert row["expected_fee"] == D("0.001") * D("110000") * D("0.012")
        assert repr(exc) in row["rails"]["preview_error"]


def test_offline_touches_no_broker_and_runs_the_offline_rails(repo) -> None:  # noqa: F811
    result = _run(repo, NoNetworkBroker(), offline=True)
    assert result.decision == "preview"
    assert repo.get_sell_proposal(result.proposal_id)["rails"]["skipped"], "paper says what it skipped"
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/execution/test_reduce.py -v`. Expected: `AttributeError: module 'keel.execution.executor' has no attribute 'reduce'`.

- [ ] **Step 3: Implement.**

```python
@dataclass(frozen=True)
class ReduceResult:
    product_id: str
    rule_kind: str
    proposal_id: int | None
    decision: str
    vetoed_by: list[str]
    legs: int
    reason: str


def reduce(reduction, *, broker, repo, config, holding, costs, rule_id, rule_status, now_ts,
           offline: bool = False) -> ReduceResult:
    """A sleeve `Reduction`, taken as far as the venue's quote and no further (plan R17).

    PREVIEW ONLY IN THIS BUILD. The order of operations is `scale_out`'s without its second half:
    slice to rail 2, clamp to what the venue holds (#667), build the SELL intent, `guards.check`,
    express the spec, `preview_order` -- then RECORD, and stop. It never calls
    `_clear_resting_bracket` (that CANCELS at the exchange), `insert_order`, or `place_order`:
    a proposal must not touch the book. `tests/execution/test_sell_side_invariants.py` pins that
    this function is not a `_run_order` caller.

    Never raises for a venue failure: a preview that throws is recorded with the fallback fee and
    the exception text, because a cycle must not die on a proposal -- the DCA buy after it in the
    same cycle is the operator's plan.
    """
    increment = None if offline else _base_increment_for(broker, repo, reduction.product_id, now_ts)
    leg_qty, legs = sleeve.slice_qty(reduction.qty, reduction.expected_price,
                                     max_per_order_usd=config.caps.max_per_order_usd,
                                     base_increment=increment)
    fallback_fee = leg_qty * reduction.expected_price * costs.fee_pct
    if legs == 0:
        pid = sleeve.record_proposal(
            repo, reduction=reduction, rule_id=rule_id, rule_status=rule_status, holding=holding,
            costs=costs, decision="vetoed",
            rails={"violations": [], "skipped": [], "sleeve": "below_one_increment"},
            expected_fee=fallback_fee, fee_source=costs.fee_source, legs=0, now_ts=now_ts)
        return ReduceResult(reduction.product_id, reduction.reason, pid, "vetoed", [], 0,
                            "one leg cannot be expressed in the venue's increment")
    leg = replace(reduction, qty=leg_qty)
    if offline or broker is None:
        qty, held = leg_qty, None
    else:
        qty, held = _clamped_sell_qty(broker, repo, reduction.product_id, leg_qty, now_ts)
    intent = OrderIntent(
        product_id=reduction.product_id, side=Side.SELL, qty=qty, entry=reduction.expected_price,
        stop=None, notional=sizing.spend(qty, reduction.expected_price), is_dca=False,
        rule_kind=reduction.reason, rule_id=rule_id, base_increment=increment,
        available_base=held,
    )
    verdict = guards.check(intent, repo, config, now_ts, offline=offline)
    rails: dict[str, Any] = {"violations": list(verdict.violations),
                             "skipped": list(verdict.skipped_rails), "preview_error": None}
    if not verdict.ok:
        pid = sleeve.record_proposal(repo, reduction=leg, rule_id=rule_id, rule_status=rule_status,
                                     holding=holding, costs=costs, decision="vetoed", rails=rails,
                                     expected_fee=fallback_fee, fee_source=costs.fee_source,
                                     legs=legs, now_ts=now_ts)
        return ReduceResult(reduction.product_id, reduction.reason, pid, "vetoed",
                            list(verdict.violations), legs, "vetoed by guards")
    fee, source = fallback_fee, costs.fee_source
    if not offline and broker is not None:
        try:
            preview = broker.preview_order(_order_spec(intent))
            if preview.est_fee is not None and preview.est_fee > 0:
                fee, source = preview.est_fee, sleeve.VENUE_FEE_SOURCE
        except TradeScopeDenied as exc:
            _try_record_trade_scope_refuted(repo, str(exc), now_ts, intent, None)
            rails["preview_error"] = repr(exc)
        except Exception as exc:  # noqa: BLE001 -- see the docstring: never die on a proposal
            log_venue_failure(logger, "executor.reduce_preview_failed",
                              product=reduction.product_id)
            rails["preview_error"] = repr(exc)
    pid = sleeve.record_proposal(repo, reduction=leg, rule_id=rule_id, rule_status=rule_status,
                                 holding=holding, costs=costs, decision="preview", rails=rails,
                                 expected_fee=fee, fee_source=source, legs=legs, now_ts=now_ts)
    log_event(logger, logging.INFO, "executor.reduction_proposed", product=reduction.product_id,
              rule=reduction.reason, rule_id=rule_id, proposal_id=pid, legs=legs,
              fee_source=source)
    return ReduceResult(reduction.product_id, reduction.reason, pid, "preview", [], legs,
                        "preview only: nothing placed")
```

  Check `_order_spec`'s behaviour for a SELL with `base_increment=None` (#516: "send unquantized"). Also check `SizePrecisionUnavailable`: catch it beside the preview and record it in `preview_error`.

- [ ] **Step 4: Run the tests, the invariants and the suite, then commit.**

```bash
uv run pytest tests/execution/test_reduce.py tests/execution/test_sell_side_invariants.py -v && uv run pytest -q && uv run mypy
git add keel/execution/executor.py tests/execution/test_reduce.py
git commit -m "feat(execution): executor.reduce -- a sleeve reduction to the venue's quote and no further (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P8 — `feat(agent): _handle_reductions -- arbitration, one proposal per product per day, sleeve.proposal notifications (#857)` · M · no schema · touches `packages/keel-core`

**PR body states:**
- **S1:** the cycle's only new venue-facing call is `executor.reduce`, which is preview-only. A test runs a full live cycle under **autonomous** mode and asserts no SELL is placed.
- **S2:** paper-status rules are hard-coded to preview (R16).
- **S3:** inherited from P7.
- **S4:** unchanged.

### Task 8.1: Loading the sleeve rules, and keeping them out of the entry path

**Files:**
- Modify: `keel/agent.py` (in `run_once`, where the rules and `products` are derived, around lines 1700–1705)
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `agent._sleeve_rules(repo, config) -> list[tuple[Rule, str]]`, a list of `(rule, status)` pairs.
  - In a paper-mode cycle it loads status `("paper",)`; otherwise `("paper", "live")`.
  - It keeps only rules where `promotion_class_of(rule) == SLEEVE_SELL`.
- Produces: `rules` (the entry and exit path) now **excludes** `sleeve_sell` rules.
- Produces: `products` = the entry rules' products ∪ the sleeve rules' products.

- [ ] **Step 1: Write the failing tests.** Use a test-only sleeve rule class registered in `RULE_REGISTRY`, the same way `_AlwaysExitRule` is registered by the module's autouse fixture.

```python
class _AlwaysReduceRule(Rule):
    """Test double: proposes selling 10% of whatever is held, every cycle."""

    name = "fake_reduce"
    promotion_class = "sleeve_sell"
    accumulates = True

    def __init__(self, product_id: str, name: str = "fake_reduce") -> None:
        self.name, self.product_id = name, product_id
        self.params = {"product_id": product_id}

    def detect(self, candles_by_tf):
        return None

    def exit_signal(self, held, candles_by_tf):
        return False

    def describe(self):
        return {"name": self.name, "params": self.params}

    def reduce_signal(self, holding, candles_by_tf, costs):
        days = completed_days(candles_by_tf) or next(iter(candles_by_tf.values()), [])
        if holding.qty <= 0 or not days:
            return None
        return Reduction(self.product_id, holding.qty / 10, self.name, {}, days[-1].close, days[-1].ts)


def _seed_rules(repo, monkeypatch, *rules_and_status):
    """Seed rows and make `_build_rule` hand back the matching instance BY KIND -- `_seed_rule`
    above returns one instance for every row, which cannot express two rules."""
    by_kind = {}
    for rule, status in rules_and_status:
        repo.insert_rule(rule.name, {"product_id": rule.product_id}, status=status)
        by_kind[rule.name] = rule
    monkeypatch.setattr(agent, "_build_rule", lambda row: by_kind[row["kind"]])


def test_a_sleeve_rule_is_not_an_entry_rule_and_cannot_withhold_entries(repo, monkeypatch):
    """A sleeve rule in the entry pre-pass could block the whole cycle's entries on its own
    freshness gate; it must not be in that pass at all."""
    _seed_rules(
        repo, monkeypatch,
        (_AlwaysEnterRule(PRODUCT), "live"),
        (_AlwaysReduceRule("ETH-USD"), "live"),  # ETH has NO candles below
    )
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.blocked_entries == []
    assert [r.placed for r in result.enter_results] == [True]
    assert "ETH-USD" in result.products, "the sleeve rule's product is still polled"
```

- [ ] **Step 2: Run the test to see it fail.** Run: `uv run pytest tests/test_agent.py -k "sleeve_rule_is_not_an_entry" -v`. Expected: FAIL. The sleeve rule is in `rules`, and its ETH gate withholds entries.

- [ ] **Step 3: Implement.** In `run_once`:

```python
        rule_status = "paper" if config.auto_trade.mode == "paper" else "live"
        built = [_build_rule(row) for row in repo.get_rules(rule_status)]
        rules = [r for r in built if promotion.promotion_class_of(r) != promotion.SLEEVE_SELL]
        sleeve_rules = _sleeve_rules(repo, config)
        products = sorted(
            {p for p in (getattr(r, "product_id", None) for r in rules) if p}
            | {r.product_id for r, _status in sleeve_rules}
        )
```

- [ ] **Step 4: Run the tests, then commit.**

### Task 8.2: `_handle_reductions` (§3.2 and §3.6; R12–R16; Review Focus 1 and 2)

**Files:**
- Modify: `keel/agent.py`. Add `_handle_reductions` after `_handle_exits`, add `LoopResult.reduce_results: list[ReduceResult] = field(default_factory=list)`, and add the call in the main pass right after the `exit_results.extend(...)` line, **before** the `if not entries_allowed: continue`.
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `_handle_reductions(product_id, sleeve_rules: list[tuple[Rule, str]], product_rules: list[Rule], candles_by_tf, repo, broker, config, now_ts, *, offline: bool) -> list[ReduceResult]`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_under_autonomous_mode_a_reduction_is_proposed_and_never_placed(repo, monkeypatch):
    """S1 at the cycle level: the profile fixture is autonomous; a SELL still never goes out."""
    _seed_rules(repo, monkeypatch, (_AlwaysReduceRule(PRODUCT), "live"))
    _seed_open_position(repo, PRODUCT, Decimal("1"), Decimal("80"), ts=0, rule_name="dca")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000 + 40 * 86_400)

    assert [r.decision for r in result.reduce_results] == ["preview"]
    assert [c for c in broker.place_calls if c["side"] is Side.SELL] == []
    [row] = repo.get_sell_proposals()
    assert row["rule_status"] == "live" and row["decision"] == "preview"


def test_a_second_run_on_the_same_utc_day_writes_no_second_proposal(repo, monkeypatch):
    _seed_rules(repo, monkeypatch, (_AlwaysReduceRule(PRODUCT), "live"))
    _seed_open_position(repo, PRODUCT, Decimal("1"), Decimal("80"), ts=0, rule_name="dca")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    day = 90_000 + 40 * 86_400
    run_once(broker, repo, _config(), now_ts=day)
    run_once(broker, repo, _config(), now_ts=day + 3_600)
    assert len(repo.get_sell_proposals()) == 1


DAY = 86_400


def test_a_distribution_on_a_dca_buy_day_is_recorded_vetoed_not_carried(repo, monkeypatch):
    """Review Focus 1: day % 210 == 0 is both a 7-day DCA day and a 30-day distribution day."""
    from keel.strategy.rules.dca import Dca

    _seed_rules(repo, monkeypatch, (Dca(product_id=PRODUCT, cadence_days=7), "live"),
                (_AlwaysReduceRule(PRODUCT), "live"))
    _seed_open_position(repo, PRODUCT, Decimal("1"), Decimal("80"), ts=0, rule_name="dca")
    series = [_candle(d * DAY, "100") for d in range(150, 211)]
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=211 * DAY + 3_600)

    [row] = repo.get_sell_proposals()
    assert row["decision"] == "vetoed" and row["rails"]["sleeve"] == "same_day_dca"

    series.append(_candle(211 * DAY, "100"))  # day 211: DCA is off-cadence
    run_once(broker, repo, _config(), now_ts=212 * DAY + 3_600)

    rows = repo.get_sell_proposals()
    assert len(rows) == 2 and rows[0]["decision"] == "preview"
    held = sum((p["qty"] for p in repo.get_open_positions(PRODUCT)), Decimal("0"))
    assert rows[0]["qty"] == held / 10, "one ordinary proposal -- nothing carried forward"


def test_a_paper_status_sleeve_rule_proposes_in_a_live_cycle_and_says_so(repo, monkeypatch):
    _seed_rules(repo, monkeypatch, (_AlwaysReduceRule(PRODUCT), "paper"))
    _seed_open_position(repo, PRODUCT, Decimal("1"), Decimal("80"), ts=0, rule_name="dca")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000 + 40 * DAY)

    [row] = repo.get_sell_proposals()
    assert (row["rule_status"], row["decision"]) == ("paper", "preview")
    assert [c for c in broker.place_calls if c["side"] is Side.SELL] == []


class _ReverseDouble(_AlwaysReduceRule):
    def __init__(self, product_id: str) -> None:
        super().__init__(product_id, name="reverse_dca")


class _ProfitDouble(_AlwaysReduceRule):
    def __init__(self, product_id: str) -> None:
        super().__init__(product_id, name="profit_take")


def test_arbitration_supersedes_the_lower_kind_and_records_it(repo, monkeypatch):
    _seed_rules(repo, monkeypatch, (_ProfitDouble(PRODUCT), "live"),
                (_ReverseDouble(PRODUCT), "live"))
    _seed_open_position(repo, PRODUCT, Decimal("1"), Decimal("80"), ts=0, rule_name="dca")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    now = 90_000 + 40 * DAY

    run_once(broker, repo, _config(), now_ts=now)

    rows = {r["rule_kind"]: r for r in repo.get_sell_proposals()}
    assert rows["reverse_dca"]["decision"] == "preview"
    assert (rows["profit_take"]["decision"], rows["profit_take"]["superseded_by"]) == (
        "superseded", "reverse_dca")
    run_once(broker, repo, _config(), now_ts=now + 3_600)
    assert len(repo.get_sell_proposals()) == 2, "a superseded row does not reopen the day"
```

  Notes on the tests above:
  - The same-day test depends on `min_hold_days`: the seeded tranche opened at `ts=0`, so it is past 30 days by day 211.
  - The double sells 10% of the holding. Day 211's DCA buy grew the holding, so the day-212 proposal is compared against the ledger rather than a literal.
  - If `_seed_open_position` in `tests/test_agent.py` does not take `rule_name`, pass it through the keyword it does take. Read its signature (line 225) first.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/test_agent.py -k "reduction or proposal or sleeve or arbitration" -v`. Expected: FAIL. There is no `reduce_results` attribute, and no rows.

- [ ] **Step 3: Implement.**

```python
def _handle_reductions(product_id, sleeve_rules, product_rules, candles_by_tf, repo, broker,
                       config, now_ts, *, offline):
    """Ask every sleeve-sell rule on `product_id` for a `Reduction`; arbitrate; hand at most one
    to `executor.reduce` (spec §3.2, §3.6). It does not read `position_rule`: a sleeve rule is a
    policy over the holding, not its owner. In this build every result is a PROPOSAL (plan R17).
    """
    on_product = [(r, s) for r, s in sleeve_rules if r.product_id == product_id]
    positions = repo.get_open_positions(product_id)
    if not on_product or not positions:
        return []
    if sleeve.proposed_today(repo, product_id, now_ts):
        log_event(logger, logging.INFO, "sleeve.already_proposed_today", product=product_id)
        return []
    days = completed_days(candles_by_tf)
    holding = Holding.from_rows(product_id, positions, days[-1].close if days else None)
    costs = sleeve.sell_costs(repo, config, product_id)
    order = {kind: i for i, kind in enumerate(sleeve.ARBITRATION_ORDER)}
    fired = []
    for rule, status in sorted(on_product, key=lambda rs: order.get(rs[0].name, len(order))):
        try:
            reduction = rule.reduce_signal(holding, candles_by_tf, costs)
        except Exception:  # noqa: BLE001 -- one broken rule must not cost the cycle
            log_exception(logger, "agent.reduce_signal_failed", product=product_id, rule=rule.name)
            continue
        if reduction is not None:
            fired.append((rule, status, reduction))
    if not fired:
        return []
    winner, winner_status, winning = fired[0]
    results = []
    for rule, status, reduction in fired[1:]:
        pid = sleeve.record_proposal(repo, reduction=reduction, rule_id=rule.rule_id,
                                     rule_status=status, holding=holding, costs=costs,
                                     decision="superseded", rails={}, legs=0,
                                     expected_fee=reduction.qty * reduction.expected_price * costs.fee_pct,
                                     fee_source=costs.fee_source, superseded_by=winner.name,
                                     now_ts=now_ts)
        results.append(executor.ReduceResult(product_id, rule.name, pid, "superseded", [], 0,
                                             f"superseded by {winner.name}"))
    dca_fires_today = any(
        r.name == "dca" and r.detect(candles_by_tf) is not None for r in product_rules
    ) or _bought_today(repo, product_id, now_ts)
    refusal = sleeve.sleeve_refusal(
        reduction=winning, holding=holding, rule_kind=winner.name, rule_params=winner.params,
        dca_fires_today=dca_fires_today,
        last_rule_proposal_ts=sleeve.last_proposal_ts(repo, winner.rule_id), now_ts=now_ts)
    if refusal is not None:
        pid = sleeve.record_proposal(repo, reduction=winning, rule_id=winner.rule_id,
                                     rule_status=winner_status, holding=holding, costs=costs,
                                     decision="vetoed", rails={"violations": [], "sleeve": refusal},
                                     expected_fee=winning.qty * winning.expected_price * costs.fee_pct,
                                     fee_source=costs.fee_source, legs=0, now_ts=now_ts)
        return [*results, executor.ReduceResult(product_id, winner.name, pid, "vetoed", [], 0,
                                                refusal)]
    return [*results, executor.reduce(winning, broker=broker, repo=repo, config=config,
                                      holding=holding, costs=costs, rule_id=winner.rule_id,
                                      rule_status=winner_status, now_ts=now_ts, offline=offline)]
```

  `_bought_today(repo, product_id, now_ts)` is true when any `mode='live'` filled BUY on the product has `created_at` on or after the UTC day start. On a paper cycle, read `mode='paper'` rows. Wrap the call in `run_once` in `try/except Exception` with `log_exception("agent.reductions_failed")`, so a proposal failure never costs the entries after it (Review Focus 4). Pass `offline=paper_trader is not None`, and `broker=None` when offline.

- [ ] **Step 4: Run the tests, then commit.**

```bash
uv run pytest tests/test_agent.py -q
git add keel/agent.py tests/test_agent.py
git commit -m "feat(agent): _handle_reductions -- arbitration, the sleeve caps, preview-only proposals (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8.3: The `sleeve.proposal` notification (R19)

**Files:**
- Modify: `packages/keel-core/keel_core/notifications.py` (`EVENTS` += `EventSpec("sleeve.proposal", "execution", INFO)`), `keel/notifications.py` (an `events_from_state(..., sleeve_proposals: Sequence[ReduceResult] = ())` parameter, and its wiring in `notify_after_cycle` from `result.reduce_results`)
- Test: `tests/test_notifications.py`

- [ ] **Step 1: Write the failing tests.** Add `sleeve: tuple = ()` to the module's `_state(...)` helper, passed as `sleeve_proposals=sleeve`, then:

```python
from keel.execution.executor import ReduceResult


def test_a_new_sleeve_proposal_notifies_once_with_its_id():
    events = _state(sleeve=(ReduceResult("BTC-USD", "reverse_dca", 4, "preview", [], 1, ""),))
    [event] = [e for e in events if e.key == "sleeve.proposal"]
    assert event.fields["proposal_id"] == 4
    assert "keel dca proposals show 4" in event.message


def test_a_superseded_proposal_does_not_notify():
    events = _state(sleeve=(ReduceResult("BTC-USD", "profit_take", 5, "superseded", [], 0, ""),))
    assert [e for e in events if e.key == "sleeve.proposal"] == []
```

  The module's existing write-list test must stay green unchanged. The write list is still exactly `[NOTIFIED_WINDOWS_KEY]`.
- [ ] **Step 2: Run the tests to see them fail** with `PYTHONPATH="$PWD:$PWD/packages/keel-core" uv run python -m pytest tests/test_notifications.py -v`. Expected: `unknown notification key 'sleeve.proposal'`.
- [ ] **Step 3: Implement.** Only `preview` and `vetoed` decisions notify. The message is `f"{r.product_id} {r.rule_kind}: proposal #{r.proposal_id} {r.decision} -- keel dca proposals show {r.proposal_id}"`.
- [ ] **Step 4: Run the suite with the `PYTHONPATH` above, then commit.**

```bash
git add packages/keel-core/keel_core/notifications.py keel/notifications.py tests/test_notifications.py
git commit -m "feat(notifications): sleeve.proposal, derived from the cycle result (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Group D — `reverse_dca` (operator step 2)

## P9 — `feat(rules): reverse_dca, preview-only (#857)` · M · no schema

**PR body states:**
- **S2 is now non-vacuous:** the registry-wide preview-only test runs over the first real kind.
- **S1:** a live `reverse_dca` only proposes (P8).
- **S3:** unchanged.
- **S4:** unchanged.

The research freeze holds. This PR adds unit tests only; no backtest runs.

### Task 9.1: The rule (spec §6)

**Files:**
- Create: `keel/strategy/rules/reverse_dca.py`
- Test: `tests/strategy/test_reverse_dca.py` (new)

**Interfaces:**
- Produces: `ReverseDca(product_id, target_usd: Decimal, min_price_floor: Decimal, cadence_days: int = 30, max_drawdown_pct: Decimal = 25, lookback_days: int = 200, floor_qty: Decimal = 0, min_hold_days: int = 30, execution: Execution = "preview", name: str = "reverse_dca")`.
- Class attributes: `promotion_class = "sleeve_sell"`, `accumulates = True`, and `decimal_params = ("target_usd", "min_price_floor", "max_drawdown_pct", "floor_qty")`. `detect` always returns `None`, and `exit_signal` always returns `False`.
- `Execution = Literal["preview"]` is a **plain assignment**. Per `base.py`'s `TradeOutcome` note, the PEP 695 form would switch off `rules add`'s `Literal` validation.

- [ ] **Step 1: Write the failing tests.**

```python
from decimal import Decimal

import pytest

from keel.strategy.reduction import Holding, Lot, SellCosts
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.reverse_dca import ReverseDca
from keel.types import Granularity
from tests.strategy.test_dca import _candle

D = Decimal
COSTS = SellCosts(D("0.012"), D("0.0005"), "fallback:config.fees.taker_pct")


def _rule(**over):
    kw = dict(product_id="BTC-USD", target_usd=D("100"), min_price_floor=D("60000"))
    kw.update(over)
    return ReverseDca(**kw)


def _held(qty="0.01"):
    return Holding("BTC-USD", (Lot(1, "dca", 0, D(qty), D("50000"), D("0")),))


def _days(last_day: int, price: str = "100000", high: str | None = None, n: int = 5):
    return {Granularity.ONE_DAY: [_candle(d, price, high) for d in range(last_day - n + 1, last_day + 1)]}


def test_it_fires_on_the_same_epoch_aligned_days_as_dca() -> None:
    for day in (60, 61, 90):
        fires = _rule().reduce_signal(_held(), _days(day), COSTS) is not None
        assert fires == (Dca("BTC-USD", cadence_days=30).detect(_days(day)) is not None), day


def test_gross_is_sized_from_the_net_target() -> None:
    red = _rule().reduce_signal(_held(), _days(60), COSTS)
    gross = D("100") / (1 - D("0.012") - D("0.0005"))
    assert red is not None and red.qty == gross / D("100000")
    assert red.reason == "reverse_dca" and red.expected_price == D("100000")


@pytest.mark.parametrize("close,fires", [("60000", True), ("59999.99", False)])
def test_the_price_floor_at_its_boundary(close, fires) -> None:
    assert (_rule().reduce_signal(_held(), _days(60, close), COSTS) is not None) is fires


@pytest.mark.parametrize("close,fires", [("75000", True), ("74999.99", False)])
def test_the_drawdown_gate_at_its_boundary(close, fires) -> None:
    """25% below a 100000 high is 75000."""
    candles = _days(60, close)
    candles[Granularity.ONE_DAY][0] = _candle(56, close, high="100000")
    rule = _rule(min_price_floor=D("1"))
    assert (rule.reduce_signal(_held(), candles, COSTS) is not None) is fires


def test_never_more_than_holding_minus_floor_qty_and_none_at_the_floor() -> None:
    red = _rule(target_usd=D("100000")).reduce_signal(_held("0.01"), _days(60), COSTS)
    assert red is not None and red.qty == D("0.01")
    small = _rule(floor_qty=D("0.009"), target_usd=D("100000")).reduce_signal(
        _held("0.01"), _days(60), COSTS)
    assert small is not None and small.qty == D("0.001")
    assert _rule(floor_qty=D("0.01")).reduce_signal(_held("0.01"), _days(60), COSTS) is None


def test_no_carry_forward_after_a_gated_cadence_day() -> None:
    rule = _rule()
    assert rule.reduce_signal(_held(), _days(60, "50000"), COSTS) is None  # below the floor
    later = rule.reduce_signal(_held(), _days(90), COSTS)
    assert later is not None and later.qty * D("100000") < D("110"), "one target, not two"


def test_no_daily_candles_is_none_with_a_named_reason() -> None:
    rule = _rule()
    assert rule.reduce_signal(_held(), {}, COSTS) is None
    assert rule.last_rejection == {"gate": "no_daily_candles"}


def test_it_never_enters_and_never_exits_on_a_signal() -> None:
    assert _rule().detect(_days(60)) is None
    assert _rule().exit_signal(None, _days(60)) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [
    dict(target_usd=D("0")), dict(cadence_days=0), dict(min_price_floor=D("0")),
    dict(max_drawdown_pct=D("0")), dict(max_drawdown_pct=D("100.01")), dict(lookback_days=0),
    dict(floor_qty=D("-1")), dict(min_hold_days=-1), dict(execution="auto"),
])
def test_construction_refuses_nonsense(bad) -> None:
    with pytest.raises(ValueError):
        _rule(**bad)


def test_a_floor_above_the_current_close_is_allowed_it_is_a_choice() -> None:
    assert _rule(min_price_floor=D("1000000")).params["min_price_floor"] == D("1000000")
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/strategy/test_reverse_dca.py -v`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**
  - The module docstring carries spec §6's reasoning: "a spend plan, not an edge claim". It also states that a skipped distribution is **not** carried forward, and why. It names the pipeline, not the rule, as the owner of rail 2 slicing, the same-day DCA exclusion, `min_hold_days` and one-per-day (R11–R14).
  - `PARAM_DOCS` gives one entry per param. The texts come from spec §6's table.

```python
    def reduce_signal(self, holding, candles_by_tf, costs):
        days = completed_days(candles_by_tf)
        if not days:
            self.last_rejection = {"gate": "no_daily_candles"}
            return None
        latest = days[-1]
        p = self.params
        if (latest.ts // _SECONDS_PER_DAY) % p["cadence_days"] != 0:
            self.last_rejection = {"gate": "off_cadence"}
            return None
        close = latest.close
        if close < p["min_price_floor"]:
            self.last_rejection = {"gate": "price_floor", "close": close, "floor": p["min_price_floor"]}
            return None
        high = max(c.high for c in days[-p["lookback_days"]:])
        dd_level = high * (Decimal("1") - p["max_drawdown_pct"] / Decimal("100"))
        if close < dd_level:
            self.last_rejection = {"gate": "drawdown", "close": close, "level": dd_level}
            return None
        available = holding.qty - p["floor_qty"]
        if available <= 0:
            self.last_rejection = {"gate": "floor_qty", "held": holding.qty}
            return None
        gross = p["target_usd"] / (Decimal("1") - costs.fee_pct - costs.slippage_pct)
        qty = min(gross / close, available)
        self.last_rejection = None
        return Reduction(
            product_id=self.product_id, qty=qty, reason=self.name,
            trigger={"cadence_day": latest.ts // _SECONDS_PER_DAY, "close": str(close),
                     "high": str(high), "target_usd": str(p["target_usd"]),
                     "gross_usd": str(gross), "fee_pct": str(costs.fee_pct),
                     "slippage_pct": str(costs.slippage_pct), "fee_source": costs.fee_source},
            expected_price=close, ts=latest.ts,
        )
```

- [ ] **Step 4: Run the tests, then commit.**

```bash
uv run pytest tests/strategy/test_reverse_dca.py -v
git add keel/strategy/rules/reverse_dca.py tests/strategy/test_reverse_dca.py
git commit -m "feat(rules): reverse_dca -- a scheduled distribution, preview-only (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 9.2: Registry, `seedable_kinds`, conformance, and S2

**Files:**
- Modify: `keel/agent.py` (`RULE_REGISTRY["reverse_dca"] = ReverseDca`; a new `seedable_kinds() -> list[str]`), `keel/commands/rules.py` (the `rules seed` callers use `agent.seedable_kinds()`), `keel/commands/setup.py:1073` (same), `tests/strategy/rule_conformance.py` (new mixin `ReductionConformanceTests`), `tests/strategy/test_rule_conformance.py`, `tests/test_cli.py` (the `3 * len(RULE_REGISTRY)` assertions become `3 * len(agent.seedable_kinds())`), `tests/execution/test_sell_side_invariants.py`
- Test: those files, plus `tests/commands/test_rules_add.py`

**Interfaces:**
- Produces: `agent.seedable_kinds() -> list[str]`, which is every registry kind whose `promotion_class != SLEEVE_SELL`, in registry order.
- Produces: the `ReductionConformanceTests` mixin, with hooks `rule()`, `holding()` and `firing_candles()`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/strategy/rule_conformance.py -- new mixin, same file, same anti-tautology discipline
class ReductionConformanceTests:
    """The contract a sleeve-sell rule is held to. `detect()` never fires for these rules, so the
    entry mixin above would check nothing: this one's firing fixture is a (holding, candles) pair
    `reduce_signal` actually fires on, and `test_the_firing_fixture_actually_fires` pins it."""

    COSTS = SellCosts(Decimal("0.012"), Decimal("0.0005"), "fallback:config.fees.taker_pct")

    def rule(self) -> Rule: raise NotImplementedError
    def holding(self) -> Holding: raise NotImplementedError
    def firing_candles(self) -> dict[Granularity, list[Candle]]: raise NotImplementedError

    def _fired(self) -> Reduction:
        red = self.rule().reduce_signal(self.holding(), self.firing_candles(), self.COSTS)
        assert red is not None, "the firing fixture must fire, or every test below is vacuous"
        return red

    def test_the_firing_fixture_actually_fires(self) -> None:
        self._fired()

    def test_reduce_signal_is_deterministic_across_repeated_calls(self) -> None:
        assert len({self._fired() == self._fired() for _ in range(3)}) == 1

    def test_reduce_signal_mutates_neither_candles_nor_holding(self) -> None:
        candles, holding = self.firing_candles(), self.holding()
        before = (copy.deepcopy(candles), holding)
        self.rule().reduce_signal(holding, candles, self.COSTS)
        assert (candles, holding) == before

    def test_never_more_than_is_held(self) -> None:
        assert self._fired().qty <= self.holding().qty

    def test_it_never_enters_and_never_exits(self) -> None:
        assert self.rule().detect(self.firing_candles()) is None

    def test_describe_round_trips_through_build_rule_from_params(self) -> None:
        rule = self.rule()
        rebuilt = agent.build_rule_from_params(
            rule.name, json.loads(json.dumps(rule.describe()["params"], default=str)))
        assert rebuilt.reduce_signal(self.holding(), self.firing_candles(), self.COSTS) == self._fired()

    def test_it_is_a_sleeve_sell_rule(self) -> None:
        assert self.rule().promotion_class == promotion.SLEEVE_SELL
        assert self.rule().accumulates is True
```

```python
# tests/strategy/test_rule_conformance.py
class TestReverseDcaConformance(ReductionConformanceTests):
    def rule(self) -> Rule:
        return ReverseDca("BTC-USD", target_usd=Decimal("100"), min_price_floor=Decimal("1"))

    def holding(self) -> Holding:
        return Holding("BTC-USD", (Lot(1, "dca", 0, Decimal("0.01"), Decimal("50000"), Decimal("0")),))

    def firing_candles(self):
        return {Granularity.ONE_DAY: [_dca_candle(day=60, price="100000")]}
```

```python
# tests/execution/test_sell_side_invariants.py -- S2
def test_every_sleeve_sell_kind_is_preview_only() -> None:
    from typing import get_args, get_type_hints

    from keel.agent import RULE_REGISTRY
    from keel.strategy import promotion

    kinds = [c for c in RULE_REGISTRY.values() if c.promotion_class == promotion.SLEEVE_SELL]
    assert kinds, "vacuous until the first kind ships -- P9 ships it"
    for cls in kinds:
        assert get_args(get_type_hints(cls.__init__)["execution"]) == ("preview",), cls.__name__


def test_no_sleeve_sell_kind_is_seedable() -> None:
    from keel import agent

    assert "reverse_dca" in agent.RULE_REGISTRY
    assert "reverse_dca" not in agent.seedable_kinds()
```

```python
# tests/commands/test_rules_add.py -- append (uses the module's `_repo` and `_add`)
def test_rules_add_writes_a_reverse_dca_candidate(tmp_path, valid_config_path):
    result = _add(tmp_path, valid_config_path, "--kind", "reverse_dca", "--product", "BTC-USD",
                  "--params", '{"target_usd": "100", "min_price_floor": "60000"}')
    assert result.exit_code == 0, result.output
    [row] = _repo(tmp_path).get_rules()
    assert (row["kind"], row["status"]) == ("reverse_dca", "candidate")
    assert _build_rule(row).params["target_usd"] == Decimal("100")


def test_rules_add_refuses_execution_auto_and_writes_nothing(tmp_path, valid_config_path):
    result = _add(tmp_path, valid_config_path, "--kind", "reverse_dca", "--product", "BTC-USD",
                  "--params",
                  '{"target_usd": "100", "min_price_floor": "60000", "execution": "auto"}')
    assert result.exit_code == 1
    assert "execution" in result.output and "preview" in result.output
    assert _repo(tmp_path).get_rules() == []


def test_rules_add_refuses_a_reverse_dca_without_its_required_target(tmp_path, valid_config_path):
    result = _add(tmp_path, valid_config_path, "--kind", "reverse_dca", "--product", "BTC-USD",
                  "--params", '{"min_price_floor": "60000"}')
    assert result.exit_code == 1 and "target_usd" in result.output
    assert _repo(tmp_path).get_rules() == []
```

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/strategy/test_rule_conformance.py tests/execution/test_sell_side_invariants.py tests/commands/test_rules_add.py tests/test_cli.py -k "reverse or seed or sleeve" -v`. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Register the kind with a comment that cites spec §6 and R20.
  - Add `seedable_kinds()`.
  - Switch the two seed callers over.

- [ ] **Step 4: Run the full suite, then commit.** The full suite is needed because `rules seed`, `test_param_space` and `research/tuning` all iterate the registry.

```bash
uv run pytest -q && uv run mypy
git add keel/ tests/
git commit -m "feat(rules): register reverse_dca; sell-side kinds are never seeded; S2 is live (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 9.3: A sleeve-sell rule does not make a position "managed" (Review Focus 5)

**Files:**
- Modify: `keel/commands/doctor.py`, `gather_findings`'s `position_watch_findings` call
- Test: `tests/commands/test_doctor_position_watch.py`

- [ ] **Step 1: Write the failing test.**

```python
# added to tests/commands/test_doctor_position_watch.py's imports
import dataclasses

from keel.commands.doctor import gather_findings
from keel.config import load_config
from tests.commands.test_doctor import _seeded_repo


def test_a_sleeve_sell_rule_does_not_count_as_managing_a_position(tmp_path, valid_config_path) -> None:
    """A live reverse_dca on PAXG can only PROPOSE; #811's finding must still fire.

    #884: `valid_config_path` is `auto_trade.mode: paper` (`tests/conftest.py`). On that config,
    `gather_findings` wires `managed_status="paper"`, and the PAXG rule this test inserts is
    `status="live"` -- so it can NEVER count as managing PAXG-USD regardless of whether the
    sleeve-sell exclusion exists: `position.unmanaged` WARNs either way, and the test cannot
    tell Step 3's fix apart from no fix at all (it passes red-less, before the exclusion is
    written). A LIVE config, derived from the fixture rather than swapping fixtures, makes the
    inserted `status="live"` rule actually eligible to manage before the exclusion exists --
    that is the red this test needs.
    """
    repo = _seeded_repo(tmp_path / "keel.db")
    repo.open_position(product_id="PAXG-USD", rule_name="turtle_breakout", opened_at=1,
                       qty=Decimal("0.0132"), entry_fill=Decimal("4673.23"),
                       entry_fee=Decimal("0.73"), initial_stop=Decimal("4521.76"))
    repo.insert_rule("reverse_dca", {"product_id": "PAXG-USD", "target_usd": "10",
                                     "min_price_floor": "1"}, status="live")
    paper_config = load_config(valid_config_path)
    config = dataclasses.replace(
        paper_config, auto_trade=dataclasses.replace(paper_config.auto_trade, mode="live")
    )
    found = {f.name: f for f in gather_findings(repo, config, [], now_ts=10)}
    assert found["position.unmanaged"].status == WARN
```

  `tests/commands/test_doctor.py` has no `_migrated_repo` or `_config` -- its `gather_findings` tests build the repo with `_seeded_repo(tmp_path / "keel.db")` and the config with `load_config(valid_config_path)` (both already defined/imported at the top of that file; `valid_config_path` is the same fixture parameter its other tests take). Import `_seeded_repo` and `load_config` from there rather than the names this snippet originally assumed. `AutoTradeConfig`/`Config` are frozen dataclasses (`packages/keel-core/keel_core/config.py`), the same shape P2's and P3's paper-profile tests already derive from with `dataclasses.replace`.

- [ ] **Step 2: Run the test to see it fail.** Run: `uv run pytest tests/commands/test_doctor_position_watch.py -k sleeve -v`. Expected: FAIL -- `position.unmanaged` reports `OK`, because the reverse_dca rule's `status == "live"` still counts as managing PAXG-USD (no exclusion yet, and this test's LIVE config means that membership actually decides the outcome, unlike the pre-#884 version of this test).

- [ ] **Step 3: Implement.** Filter the rule rows themselves by CLASS, not by status, before the call -- **#885: do not swap `repo.get_rules()` for `repo.get_rules("live")` here.** `repo.get_rules("live")` reverts round 1's P2 fix (#880/#881) two ways at once: a demoted rule (PAXG's rule 3, `status="paper"`) is dropped from the list entirely, so `position.unmanaged`'s WARN falls back to `"no rule"` instead of naming its status (#811 acceptance bullet 1); and on a paper profile, where `managed_status="paper"` but every rule the engine ever promotes also carries `status="paper"`, an all-`"live"` query returns nothing at all, so `managed` is permanently empty and every open paper tranche WARNs (#881's original bug, again). Both `status_by_product` and `managed` inside `position_watch_findings` need the FULL row set, of every status; only the SLEEVE_SELL-class rows should be missing from what this function receives, because those are the only rows this task means to make ineligible:

```python
    non_sell_rules = [
        row for row in repo.get_rules()
        if promotion.promotion_class_of(agent.RULE_REGISTRY.get(row["kind"])) != promotion.SLEEVE_SELL
    ]
```

  `agent.RULE_REGISTRY.get(row["kind"])` returns `None` for a kind the registry does not recognise, and `promotion.promotion_class_of(None)` returns `DEFAULT_CLASS` (its own `getattr(rule, "promotion_class", DEFAULT_CLASS)` fallback) rather than raising -- so an unrecognised kind is kept, not silently dropped from `status_by_product`'s visibility the way `row["kind"] in agent.RULE_REGISTRY` would have dropped it. Pass `non_sell_rules` as the `all_rules` argument in place of the raw `repo.get_rules()` call already in `gather_findings` (P2 Task 2.1, Step 4); `managed_status=` stays exactly as P2 wired it, unchanged by this task.

  **The missing import.** `promotion` is `keel.strategy.promotion` (verified against the module's actual path; `promotion_class_of` and the module-level constants this task reads all live there, not in `keel.execution`). `gather_findings` does not import it yet -- add `from keel.strategy import promotion` to the function's own local-import block (`keel/commands/doctor.py`, inside `gather_findings`, beside the existing `from keel import agent` a few lines above the P2 wiring this task edits), matching where Task 9.2's `seedable_kinds` already imports the same module the same way (`from keel.strategy import promotion`, local to the function rather than top-of-file).

  Update `position_watch_findings`' docstring: the "P9 adds the exclusion" sentence becomes present tense.

- [ ] **Step 4: Run the tests, then commit.**

```bash
git add keel/commands/doctor.py tests/commands/test_doctor_position_watch.py
git commit -m "fix(doctor): a proposal-only sleeve rule does not manage a position (#811, #857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P10 — `feat(dca): keel dca distribute --preview and the reverse_dca doctor findings (#857)` · S · no schema

**PR body states:** S1–S4 are unchanged. Both surfaces are read-only, and the CLI builds no broker (R25).

### Task 10.1: `sleeve_report.distribution_rows` and `keel dca distribute --preview`

**Files:**
- Modify: `keel/commands/sleeve_report.py`, `keel/commands/dca.py`
- Test: `tests/commands/test_sleeve_report.py` (new), `tests/commands/test_dca_distribute_cli.py` (new)

**Interfaces:**
- Produces: `DistributionRow(rule_id: int, status: str, product_id: str, next_cadence_ts: int, gates: dict[str, bool], qty: Decimal | None, gross_usd: Decimal | None, fee_usd: Decimal | None, legs: int, dca_collision: bool)`.
- Produces: `distribution_rows(repo, config, now_ts) -> list[DistributionRow]`, and `render_distribution(rows) -> list[str]`.

- [ ] **Step 1: Write the failing tests.**

```python
DAY = 86_400


def _seed(repo, *, floor="60000", close="100000", dca=True):
    repo.insert_rule("reverse_dca", {"product_id": "BTC-USD", "target_usd": "100",
                                     "min_price_floor": floor}, status="paper")
    if dca:
        repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 7,
                                 "budget_usd": "50"}, status="live")
    repo.open_position(product_id="BTC-USD", rule_name="dca", opened_at=0, qty=D("0.01"),
                       entry_fill=D("50000"), entry_fee=D("0"))
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY,
                        [_candle(d, close) for d in range(195, 201)])


def test_the_next_cadence_day_and_whether_it_collides_with_the_weekly_buy(repo) -> None:
    _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.next_cadence_ts == 210 * DAY
    assert row.dca_collision is True, "210 is a multiple of both 30 and 7 -- Review Focus 1"
    assert row.gates == {"price_floor": True, "drawdown": True, "floor_qty": True}
    assert row.legs == 1 and row.qty is not None


def test_a_closed_gate_is_named_and_no_size_is_invented(repo) -> None:
    _seed(repo, floor="200000")
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.gates["price_floor"] is False and row.qty is None
```

  `Repository.upsert_candles(product, granularity, candles)` is the repository's candle writer (`keel/data/repository.py:501`). Check its exact signature before use. Add a CLI test in the `test_dca_cli.py` style:
  - off a TTY the command prints and writes nothing (the `PRAGMA data_version` watcher);
  - the output contains `fallback:config.fees.taker_pct` and `preview only: nothing is placed`.

- [ ] **Step 2: Run the tests to see them fail.** Expected: `ImportError`.

- [ ] **Step 3: Implement.**
  - `next_cadence_ts` is the smallest day `d ≥ today` with `d % cadence_days == 0`, where `today = now_ts // DAY`.
  - `dca_collision` is true if any non-disabled `dca` rule on the product has `d % its cadence_days == 0`.
  - Evaluate the gates and the size on the latest cached completed daily bar, by calling the rule's own `reduce_signal` with a candle list whose last bar is re-stamped to day `d`. That reuses the rule's arithmetic and invents none.
  - The fee is at the fallback rate. `legs` comes from `sleeve.slice_qty(..., base_increment=<cached increment or None>)`.

- [ ] **Step 4: Run the tests, then commit.**

### Task 10.2: `sleeve.buy_and_sell_same_asset` and `sleeve.price_floor_stale` (spec §6 failure modes a and b)

**Files:**
- Modify: `keel/commands/doctor.py` (`sleeve_rule_findings`, wired in `gather_findings`)
- Test: `tests/commands/test_doctor_sleeve_rules.py` (new)

- [ ] **Step 1: Write the failing tests.**

```python
def test_a_live_dca_beside_a_live_or_paper_reverse_dca_is_named() -> None:
    rules = [
        {"id": 6, "kind": "dca", "status": "live", "params": {"product_id": "BTC-USD"}},
        {"id": 20, "kind": "reverse_dca", "status": "paper",
         "params": {"product_id": "BTC-USD", "min_price_floor": "60000"}},
    ]
    found = {f.name: f for f in sleeve_rule_findings(rules, {"BTC-USD": Decimal("100000")})}
    assert found["sleeve.buy_and_sell_same_asset"].status == WARN
    assert "1.8%" not in found["sleeve.buy_and_sell_same_asset"].detail, "no hardcoded live rate"
    assert found["sleeve.price_floor_stale"].status == OK


def test_a_floor_below_half_the_close_protects_nothing() -> None:
    rules = [{"id": 20, "kind": "reverse_dca", "status": "live",
              "params": {"product_id": "BTC-USD", "min_price_floor": "49999"}}]
    found = {f.name: f for f in sleeve_rule_findings(rules, {"BTC-USD": Decimal("100000")})}
    assert found["sleeve.price_floor_stale"].status == WARN
    assert found["sleeve.price_floor_stale"].products == ("BTC-USD",)


def test_no_sleeve_rules_at_all_still_reports_both_findings_ok() -> None:
    """A deployment with no `dca`/`reverse_dca` rules yet -- the exact shape
    `test_gather_findings_covers_every_check_over_a_seeded_db` (`tests/commands/test_doctor.py:603`)
    runs over -- has nothing to WARN about for either check, but `gather_findings` still wires
    both names in. As P3's `ledger.drift`/`ledger.venue_drift` already do for their own
    nothing-to-report case, `sleeve_rule_findings` must return an explicit OK for both, not an
    empty list -- an empty list would make both names silently vanish from `gather_findings`'
    output on exactly the deployment the exact-set test seeds.
    """
    found = {f.name: f for f in sleeve_rule_findings([], {})}
    assert found["sleeve.buy_and_sell_same_asset"].status == OK
    assert found["sleeve.price_floor_stale"].status == OK
```

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - The round-trip wording is "two taker legs at the venue's fee", with no number.
  - The latest close comes from `repo.get_candles(product, ONE_DAY)[-1].close`, and is omitted when there is none. The finding is not judged without a close.
  - **`sleeve_rule_findings` always returns exactly two findings, never zero.** With no
    `dca`/`reverse_dca` rules at all (or none that collide, and none whose floor is stale),
    return `[Finding("sleeve.buy_and_sell_same_asset", OK, ...), Finding("sleeve.price_floor_stale", OK, ...)]`
    rather than `[]` -- the same shape P3's `ledger_drift_findings`/`venue_drift_findings` use
    for their own "nothing to report" case (`if not drifted: return [Finding("ledger.drift", OK, ...)]`).
    Without this, a repo with no sleeve rules yet (exactly what
    `test_gather_findings_covers_every_check_over_a_seeded_db`, `tests/commands/test_doctor.py:603`,
    seeds) would make `gather_findings` emit neither name at all, and the exact-set assertion
    below would fail on a MISSING name rather than an unexpected one.
  - **#886: add `"sleeve.buy_and_sell_same_asset"` and `"sleeve.price_floor_stale"` to the exact-set assertion.** Wiring `sleeve_rule_findings` into `gather_findings` adds two names `test_gather_findings_covers_every_check_over_a_seeded_db` (`tests/commands/test_doctor.py:603`) does not yet list, turning that existing test red. Add both to its `{f.name for f in findings} == {...}` set, beside the names P2 and P3 added -- check the literal's actual current contents first.
- [ ] **Step 4: Run the tests and the doctor read-only test, then commit.** Run: `uv run pytest tests/commands/test_doctor_sleeve_rules.py tests/commands/test_doctor.py -q`. Expected: PASS.

```bash
git add keel/commands/ tests/commands/
git commit -m "feat(dca): keel dca distribute --preview, and the two reverse_dca doctor findings (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P11 — `feat(sim): the reverse path -- distributions in the account sim and the accumulation row (#857)` · M · no schema

**PR body states:**
- S1–S4 are unchanged, because the sim has no broker.
- **The research freeze holds.** These tests are fidelity checks of the harness against a hand computation on synthetic, gapless candles (spec §6, "Evidence status"). No real-history run is made, and no ledger row is written.

### Task 11.1: `report.accumulation_table`, the FIFO-faithful per-rule row

**Files:**
- Modify: `keel/sim/portfolio_sim.py` (`DcaSleeve` gains `distributions: int = 0`, `units_sold`, `distributed_usd`, `realised_pnl` and `sell_fees`, each a `Decimal` defaulting to `0`; `marked(...)` takes them as keyword arguments), `keel/sim/report.py` (`accumulation_table` gains `max_per_order_usd: Decimal | None = None`, and processes accumulating rules **per asset**)
- Test: `tests/sim/test_dca_sleeve.py`

**Interfaces:**
- Produces:
  - each `dca` rule's row reports its own **remaining** lots after FIFO sales;
  - each `reverse_dca` rule's row has `buys=0`, `qty=0` and `cost_usd=0`, and carries the sell columns.
- Order of operations within a decided day mirrors the live cycle (reductions run before entries):
  - evaluate `dca_fires = any(dca.detect(view))`;
  - run the reverse rules through `sleeve.sleeve_refusal` and `sleeve.slice_qty`, then sell at the next bar's open × (1 − slip), minus the fee;
  - then buy.

- [ ] **Step 1: Write the failing test.** This is the pinned hand computation.

```python
def test_a_distribution_reproduces_the_hand_computation_on_gapless_candles() -> None:
    """61 flat days at 100. DCA every 7 days, $50, fee 1%, no slippage: buys decided on days
    0,7,...,56 (9 buys, 0.5 each, $50.50 each). reverse_dca every 30 days, $10 net: day 0 is a
    DCA day and holds nothing; day 30 sells gross 10/0.99 at day 31's open 100, i.e. 0.10101010
    units; its FIFO cost is 0.1010101 x 100 x 1.01; so realised = 10.00 - 10.20 = -0.20."""
    daily = [_candle(d, "100") for d in range(61)]
    dca = Dca("BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
    rev = ReverseDca("BTC-USD", target_usd=Decimal("10"), min_price_floor=Decimal("1"))

    rows = accumulation_table([dca, rev], {"BTC": {Granularity.ONE_DAY: daily}},
                              fee_pct=Decimal("0.01"), slippage_pct=Decimal("0"))

    out = rows["reverse_dca:BTC"]
    assert out.distributions == 1
    assert out.units_sold.quantize(Decimal("0.00000001")) == Decimal("0.10101010")
    assert out.distributed_usd.quantize(Decimal("0.01")) == Decimal("10.00")
    assert out.sell_fees.quantize(Decimal("0.01")) == Decimal("0.10")
    assert out.realised_pnl.quantize(Decimal("0.01")) == Decimal("-0.20")
    kept = rows["dca:BTC"]
    assert kept.buys == 9
    assert (kept.qty + out.units_sold) == Decimal("4.5")


def test_the_run_is_deterministic() -> None:
    args = ([Dca("BTC-USD", cadence_days=7), ReverseDca("BTC-USD", target_usd=Decimal("10"),
             min_price_floor=Decimal("1"))], {"BTC": {Granularity.ONE_DAY:
             [_candle(d, str(100 + d % 5)) for d in range(120)]}})
    kw = dict(fee_pct=Decimal("0.012"), slippage_pct=Decimal("0.0005"))
    assert accumulation_table(*args, **kw) == accumulation_table(*args, **kw)


def test_a_distribution_on_a_dca_day_is_skipped_in_the_sim_as_live() -> None:
    daily = [_candle(d, "100") for d in range(212)]
    rows = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), ReverseDca("BTC-USD", target_usd=Decimal("10"),
                                                    min_price_floor=Decimal("1"))],
        {"BTC": {Granularity.ONE_DAY: daily}}, fee_pct=Decimal("0.01"), slippage_pct=Decimal("0"))
    assert rows["reverse_dca:BTC"].distributions == 6, "days 30..180 sell; 210 is a DCA day"
```

  The key strings follow `report.rule_keys`. Before asserting keys, run `rule_keys([dca, rev])` and use what it returns. Days 30, 60, 90, 120, 150 and 180 sell. Day 210 is skipped, because it is a DCA day, and it is not carried to day 211: day 211 is off-cadence.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/sim/test_dca_sleeve.py -k "distribution or deterministic" -v`. Expected: FAIL, with no `distributions` attribute, and `reverse_dca:BTC` is a zero-buy row.

- [ ] **Step 3: Implement.**
  - Group the accumulating rules by asset.
  - Keep per-asset FIFO `Lot`s, whose `rule_name` is the owning DCA rule's key.
  - Build the `Holding` via `Holding(product, tuple(lots))`.
  - Apply the reductions with `sleeve.sleeve_refusal(..., last_rule_proposal_ts=None, now_ts=fill_bar.ts)`, then `sleeve.slice_qty(..., max_per_order_usd=max_per_order_usd or Decimal("Infinity"), base_increment=None)`.
  - Reduce the lots FIFO.
  - Take at most one sale per asset per day (R14), in `sleeve.ARBITRATION_ORDER`.
  - The function's docstring gains a paragraph stating the mirrored order and that the row is a harness-fidelity artefact, not a verdict.

- [ ] **Step 4: Run the tests, then commit.**

### Task 11.2: The account sim's reverse path and the rail-2 parity test

**Files:**
- Modify: `keel/sim/account.py` (`SimAccount.reduce_dca(asset, qty, price, ts, fee_pct) -> tuple[Decimal, Decimal]`, which returns `(net_proceeds, fee)`), `keel/sim/portfolio_sim.py` (a new `_process_reductions(...)` called beside `_process_dca_signals`, **before** it on the same bar; `SimResult.dca_sells: list[DcaSell]`)
- Test: `tests/sim/test_portfolio_sim.py`, `tests/execution/test_reduce.py` (the parity test)

- [ ] **Step 1: Write the failing tests.**

```python
def test_reduce_dca_shrinks_the_lot_and_credits_net_cash() -> None:
    account = _account_with_cash(Decimal("100"))  # the module's existing SimAccount builder
    account.open(OpenIntent(asset="BTC", qty=Decimal("1"), entry=Decimal("100"), stop=None,
                            notional=Decimal("100"), is_dca=True, rule_kind="dca"),
                 Decimal("100"), 0, dca=True)
    cash_after_buy = account.cash_usdc

    net, fee = account.reduce_dca("BTC", Decimal("0.25"), Decimal("100"), ts=1,
                                  fee_pct=Decimal("0.01"))

    assert (net, fee) == (Decimal("24.75"), Decimal("0.25"))
    assert account.dca_positions["BTC"].qty == Decimal("0.75")
    assert account.cash_usdc == cash_after_buy + Decimal("24.75")


def test_the_sim_and_guards_agree_on_an_oversized_distribution(repo) -> None:  # noqa: F811
    """Spec §6: a parity test. One slicer (`sleeve.slice_qty`) feeds both; the sliced leg passes
    rail 2 and one increment more is vetoed by it."""
    cap = Decimal("50")
    leg, _ = sleeve.slice_qty(Decimal("0.01"), Decimal("110000"), max_per_order_usd=cap,
                              base_increment=Decimal("0.00000001"))
    config = _config(caps=Caps(max_exposure_usd=Decimal("1e6"), max_per_asset_pct=Decimal("1"),
                               max_per_order_usd=cap))

    def _veto(qty):
        intent = OrderIntent(product_id="BTC-USD", side=Side.SELL, qty=qty, entry=Decimal("110000"),
                             stop=None, notional=qty * Decimal("110000"), is_dca=False,
                             rule_kind="reverse_dca")
        return guards.check(intent, repo, config, NOW_TS, offline=True).violations

    assert not any(v.startswith("per_order_cap") for v in _veto(leg))
    assert any(v.startswith("per_order_cap") for v in _veto(leg + Decimal("0.00000001")))
```

  Before writing the first test, read `SimAccount.__init__` and `open(..., dca=True)` in `keel/sim/account.py`, and `tests/sim/test_account.py`'s account builder. Name the builder `_account_with_cash` if the module has none, and give it the fields `SimAccount` requires. The lot is built through `account.open` itself, not by hand, so any fee `open` charges is already in `cash_after_buy`.

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - `reduce_dca` refuses `qty > lot.qty` with a `ValueError`. It adds `qty × price` to `monthly_volume` (#86 counts sells), and credits `qty × price × (1 − fee)`.
  - `_process_reductions` builds a single-lot `Holding` from the averaged sim lot. The account sim's DCA sleeve is one averaged lot per asset, so realised P&L there is average-cost. Say so in the docstring: the FIFO-faithful figure is the accumulation row's (Task 11.1).
- [ ] **Step 4: Run `uv run pytest tests/sim tests/execution -q`, then commit.**

```bash
git add keel/sim/ tests/sim/ tests/execution/test_reduce.py
git commit -m "feat(sim): the reverse path -- distributions in the account sim and the accumulation row (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P12 — `feat(promotion): the sleeve_sell gate, proposal replay, proposals review, --allow-concurrent-dca (#857)` · M · no schema

**PR body states:**
- S1: a sleeve-sell rule promoted to `live` still only proposes (S2).
- The new `--allow-concurrent-dca` flag weakens nothing, because a `live` sell rule places nothing in this build.
- `proposals review` is not a capability row (R23).
- S3 and S4 are unchanged.
- The research freeze holds. The replay is a description with no threshold.

### Task 12.1: `promotion.sleeve_sell_gate` (spec §3.7, Q2, Q6)

**Files:**
- Modify: `keel/strategy/promotion.py`
- Test: `tests/strategy/test_promotion.py`

**Interfaces:**
- Produces: `SLEEVE_SELL_MIN_PAPER_DAYS = 60`.
- Produces: `sleeve_sell_gate(*, status: str, promoted_at: int | None, reviewed_proposals: int, lookahead_clean: bool, concurrent_live_dca: bool, allow_concurrent_dca: bool, now_ts: int) -> tuple[bool, list[str]]`.

- [ ] **Step 1: Write the failing tests.**

```python
DAY = 86_400
_GATE = dict(lookahead_clean=True, concurrent_live_dca=False, allow_concurrent_dca=False)


def test_candidate_to_paper_needs_only_a_clean_lookahead() -> None:
    ok, _ = promotion.sleeve_sell_gate(status="candidate", promoted_at=None, reviewed_proposals=0,
                                       now_ts=0, **_GATE)
    assert ok
    ok, why = promotion.sleeve_sell_gate(status="candidate", promoted_at=None, reviewed_proposals=0,
                                         now_ts=0, **{**_GATE, "lookahead_clean": False})
    assert not ok and "lookahead" in why[0]


def test_paper_to_live_needs_sixty_days_and_one_reviewed_proposal() -> None:
    base = dict(status="paper", promoted_at=0, **_GATE)
    assert not promotion.sleeve_sell_gate(reviewed_proposals=1, now_ts=59 * DAY, **base)[0]
    assert not promotion.sleeve_sell_gate(reviewed_proposals=0, now_ts=60 * DAY, **base)[0]
    assert promotion.sleeve_sell_gate(reviewed_proposals=1, now_ts=60 * DAY, **base)[0]


def test_a_live_dca_on_the_product_refuses_unless_the_operator_types_the_flag() -> None:
    base = dict(status="paper", promoted_at=0, reviewed_proposals=1, now_ts=60 * DAY,
                lookahead_clean=True)
    ok, why = promotion.sleeve_sell_gate(concurrent_live_dca=True, allow_concurrent_dca=False, **base)
    assert not ok and "--allow-concurrent-dca" in why[0]
    assert promotion.sleeve_sell_gate(concurrent_live_dca=True, allow_concurrent_dca=True, **base)[0]
```

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.** This is a pure function. The docstring cites spec §3.7 ("a rule that never fired in paper is not promoted on silence"), Q2, Q6 and R16. It also notes that `live` means preview in this build (S2).
- [ ] **Step 4: Run the tests, then commit.**

### Task 12.2: Routing in `attempt_promotion`, and the lookahead adapter (R21)

**Files:**
- Modify: `keel/commands/rules.py`. Add `_reduction_as_detect(rule)`. Branch in `attempt_promotion` after `rule = agent._build_rule(row)`: when `promotion_class_of(rule) == SLEEVE_SELL`, run the lookahead through the adapter, count the reviewed proposals, detect a concurrent live `dca` (reverse_dca only), call `sleeve_sell_gate`, and transition or refuse. Add the `--allow-concurrent-dca` option.
- Test: `tests/commands/test_rules_services.py`

- [ ] **Step 1: Write the failing tests.**

```python
class _PeekingReduce(ReverseDca):
    """Decides about bar t-1 using bar t's close -- lookahead by construction, so the adapter
    must flag it (R21 is not vacuous). Its decision AT a bar changes once the next bar exists."""

    def reduce_signal(self, holding, candles_by_tf, costs):
        days = candles_by_tf.get(Granularity.ONE_DAY, [])
        if len(days) < 2 or days[-1].close <= days[-2].close:
            return None
        return Reduction(self.product_id, Decimal("0.001"), self.name, {}, days[-2].close,
                         days[-2].ts)


@pytest.fixture
def btc_book():
    """An in-memory book with 120 BTC-USD daily candles (price wanders so the peek has signal)."""
    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    daily = [_candle(d, str(100 + (d * 7) % 13)) for d in range(120)]
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, daily)
    return repo, daily


@pytest.fixture
def tmp_config(valid_config_path):
    return load_config(valid_config_path)


def test_the_lookahead_adapter_flags_a_peeking_sell_rule(btc_book) -> None:
    _repo, daily = btc_book
    rule = _PeekingReduce("BTC-USD", target_usd=Decimal("10"), min_price_floor=Decimal("1"))
    report = bias.lookahead_analysis(rules_cmd._reduction_as_detect(rule),
                                     {Granularity.ONE_DAY: daily}, warmup=5)
    assert report.verdict == "lookahead_detected"


def test_a_sleeve_rule_is_never_judged_by_a_trade_floor(btc_book, tmp_config) -> None:
    repo, _daily = btc_book
    rid = repo.insert_rule("reverse_dca", {"product_id": "BTC-USD", "target_usd": "10",
                                           "min_price_floor": "1"}, status="candidate")
    outcome = attempt_promotion(repo, tmp_config, rid)
    assert outcome.new_status == "paper"
    assert not any("min_trades" in line for line in outcome.lines)


def _paper_reverse(repo, *, days_in_paper: int) -> int:
    rid = repo.insert_rule("reverse_dca", {"product_id": "BTC-USD", "target_usd": "10",
                                           "min_price_floor": "1"}, status="paper")
    repo._conn.execute("UPDATE rules SET promoted_at = ? WHERE id = ?",
                       (int(time.time()) - days_in_paper * 86_400, rid))
    repo._conn.commit()
    return rid


def test_paper_to_live_refused_without_a_reviewed_proposal(btc_book, tmp_config):
    repo, _daily = btc_book
    rid = _paper_reverse(repo, days_in_paper=61)
    with pytest.raises(RulesRefused):
        attempt_promotion(repo, tmp_config, rid)
    assert {r["id"]: r["status"] for r in repo.get_rules()}[rid] == "paper"


def test_paper_to_live_refused_beside_a_live_dca_without_the_flag(btc_book, tmp_config):
    repo, _daily = btc_book
    rid = _paper_reverse(repo, days_in_paper=61)
    pid = repo.insert_sell_proposal(_row(rule_id=rid, rule_status="paper"))
    repo.update_sell_proposal(pid, reviewed_ts=int(time.time()))
    repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 7, "budget_usd": "40"},
                     status="live")

    with pytest.raises(RulesRefused):
        attempt_promotion(repo, tmp_config, rid)
    outcome = attempt_promotion(repo, tmp_config, rid, allow_concurrent_dca=True)
    assert outcome.new_status == "live"
```

  Notes:
  - `load_config` is the loader `_load_cfg` wraps. Import it from where `keel/commands/_common.py` imports it.
  - `_row` comes from `tests/data/test_sell_proposals.py`.
  - `attempt_promotion` gains an `allow_concurrent_dca: bool = False` keyword.

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement the adapter.**

```python
def _reduction_as_detect(rule: Rule) -> Callable[[dict[Granularity, list[Candle]]], Setup | None]:
    """`reduce_signal` in `Rule.detect`'s shape, so the ONE lookahead harness runs on it (plan
    R21): entry=expected_price, stop=0, target=qty, over a fixed synthetic one-lot holding."""
    costs = SellCosts(Decimal("0.012"), Decimal("0.0005"), sleeve.FALLBACK_FEE_SOURCE)

    def _detect(candles_by_tf):
        days = candles_by_tf.get(Granularity.ONE_DAY) or []
        if not days:
            return None
        holding = Holding(rule.product_id,
                          (Lot(0, "dca", 0, Decimal("1"), days[0].close, Decimal("0")),))
        red = rule.reduce_signal(holding, candles_by_tf, costs)
        if red is None:
            return None
        return Setup(product_id=rule.product_id, direction="long", entry=red.expected_price,
                     stop=Decimal("0"), target=red.qty, context={"reason": red.reason}, ts=red.ts)

    return _detect
```

- [ ] **Step 4: Run the tests, then commit.**

### Task 12.3: Proposal replay in `keel rules backtest` (spec §3.7), with no hardcoded 0.9% (R29)

**Files:**
- Modify: `keel/commands/rules.py`. In `run_rule_backtest`, a sleeve-sell branch calls `sleeve_report.proposal_replay(rule, daily, fee_pct, slippage)`. Add a `--fee-sensitivity-pct` option (no default).
- Modify: `keel/commands/sleeve_report.py` (`proposal_replay`)
- Test: `tests/commands/test_sleeve_report.py`

- [ ] **Step 1: Write the failing tests.**

```python
def test_the_replay_lists_every_reduction_and_is_deterministic() -> None:
    daily = [_candle(d, str(100 + d)) for d in range(0, 91)]
    rule = ReverseDca("BTC-USD", target_usd=Decimal("10"), min_price_floor=Decimal("1"))
    first = proposal_replay(rule, daily, fee_pct=Decimal("0.012"), slippage_pct=Decimal("0.0005"))
    assert [r.day for r in first.rows] == [30, 60, 90]
    assert first == proposal_replay(rule, daily, fee_pct=Decimal("0.012"),
                                    slippage_pct=Decimal("0.0005"))


def _backtest(tmp_path, valid_config_path, rid, *extra):
    return CliRunner().invoke(cli, ["--db", str(tmp_path / "t.db"), "--config",
                                    str(valid_config_path), "rules", "backtest", str(rid), *extra])


def test_the_sensitivity_row_exists_only_when_the_operator_supplies_a_rate(
    tmp_path, valid_config_path
) -> None:
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY,
                        [_candle(d, str(100 + d)) for d in range(91)])
    rid = repo.insert_rule("reverse_dca", {"product_id": "BTC-USD", "target_usd": "10",
                                           "min_price_floor": "1"}, status="candidate")

    plain = _backtest(tmp_path, valid_config_path, rid).output
    both = _backtest(tmp_path, valid_config_path, rid, "--fee-sensitivity-pct", "0.009").output

    assert plain.count("fee line:") == 1 and "fallback:config.fees.taker_pct" in plain
    assert both.count("fee line:") == 2 and "operator-supplied sensitivity" in both


def test_no_live_fee_rate_is_hardcoded_anywhere_in_keel() -> None:
    """Spec §2.2 / Q5: the tier can change, and the preview is the fact."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "keel"
    hits = [str(p) for p in root.rglob("*.py") if re.search(r"\b0\.009\b", p.read_text())]
    assert hits == []
```


- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - The replay seeds a synthetic one-lot holding (1 unit at the first close), walks the completed days, and applies `sleeve_refusal` and `slice_qty` as the live pipeline does.
  - It reports each reduction's realised P&L against the FIFO lots, and the terminal value with and without the rule.
  - It prints "a description, not a pass mark (spec §3.7)".
- [ ] **Step 4: Run the tests, then commit.**

### Task 12.4: `keel dca proposals review <id>` (R23)

**Files:**
- Modify: `keel/commands/dca.py`
- Test: `tests/commands/test_dca_proposals_cli.py`

- [ ] **Step 1: Write the failing tests.**

```python
def _review(deployment, pid, input=None):
    db, config = deployment
    return CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", "proposals",
                                    "review", str(pid)], input=input)


def test_off_a_tty_review_writes_nothing(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    pid = _repo(deployment[0]).insert_sell_proposal(_row())
    result = _review(deployment, pid)
    assert "not a terminal: nothing written" in result.output
    assert _repo(deployment[0]).get_sell_proposal(pid)["reviewed_ts"] is None


@pytest.mark.parametrize("answer,reviewed", [("y\n", True), ("n\n", False)])
def test_at_a_tty_the_answer_decides(deployment, monkeypatch, answer, reviewed) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    pid = _repo(deployment[0]).insert_sell_proposal(_row())
    _review(deployment, pid, input=answer)
    assert (_repo(deployment[0]).get_sell_proposal(pid)["reviewed_ts"] is not None) is reviewed


def test_a_superseded_proposal_cannot_be_reviewed(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    pid = _repo(deployment[0]).insert_sell_proposal(_row(decision="superseded"))
    result = _review(deployment, pid, input="y\n")
    assert result.exit_code != 0 and "superseded" in result.output
    assert _repo(deployment[0]).get_sell_proposal(pid)["reviewed_ts"] is None
```
- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement** with `_common._is_interactive()` decided first. The database opener follows R20 in `dca.py`'s docstring. Use `click.confirm`, not the typed-`yes` gate.
- [ ] **Step 4: Run the tests, the capability tests (unchanged: no new gate call site) and the suite, then commit.**

```bash
git add keel/ tests/
git commit -m "feat(promotion): the sleeve_sell gate, proposal replay, proposals review (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Group E — Trims, preview-only (operator step 3a)

## P13 — `feat(dca): keel dca trim --preview --view lots and --view bands (#857)` · M · no schema

**PR body states:** S1–S4 are unchanged. Both views are read-only reports over `Holding`, and the command builds no broker (R25). They are labelled "not tax advice" (spec §8.2). `band_rebalance` is **not** built. `--view bands` is spec §5's read-only drift report.

### Task 13.1: `lots_view` (spec §8.1, Review Focus 3)

**Files:**
- Modify: `keel/commands/sleeve_report.py`
- Test: `tests/commands/test_sleeve_report.py`

**Interfaces:**
- Produces: `LotRow(product_id, position_id, rule_name, opened_at, qty, entry_fill, entry_fee_share, cost, mark: Decimal | None, unrealised: Decimal | None, realised_if_sold: Decimal | None)`.
- Produces: `lots_view(repo, config, product_id: str | None = None) -> list[LotRow]` and `render_lots(rows) -> list[str]`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_the_mixed_paxg_ledger_lists_turtle_tranche_3_first_with_its_realisable_pnl(repo) -> None:
    repo.open_position(product_id="PAXG-USD", rule_name="turtle_breakout", opened_at=1,
                       qty=D("0.0132"), entry_fill=D("4673.23"), entry_fee=D("0.73"))
    repo.open_position(product_id="PAXG-USD", rule_name="dca", opened_at=2, qty=D("0.01"),
                       entry_fill=D("4400"), entry_fee=D("0.40"))
    repo.upsert_candles("PAXG-USD", Granularity.ONE_DAY, [_candle(10, "4300")])

    rows = lots_view(repo, _config())

    assert [(r.position_id, r.rule_name) for r in rows] == [(1, "turtle_breakout"), (2, "dca")]
    first = rows[0]
    assert first.unrealised == D("0.0132") * D("4300") - (D("0.0132") * D("4673.23") + D("0.73"))
    costs = sleeve.sell_costs(repo, _config(), "PAXG-USD")
    assert first.realised_if_sold == (
        D("0.0132") * D("4300") * (1 - costs.slippage_pct) - D("0.0132") * D("4300") * costs.fee_pct
        - first.cost
    )


def test_no_mark_means_no_pnl_rather_than_a_total_loss(repo) -> None:
    repo.open_position(product_id="BTC-USD", rule_name="dca", opened_at=1, qty=D("0.001"),
                       entry_fill=D("100000"), entry_fee=D("0"))
    [row] = lots_view(repo, _config())
    assert row.mark is None and row.unrealised is None and row.realised_if_sold is None


def test_the_rendered_report_says_what_it_is_not() -> None:
    assert any("not tax advice" in line for line in render_lots([]))
```

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - The mark is `repo.get_candles(p, ONE_DAY)[-1].close` when that exists. This is `positions.py`'s "what is absent stays absent" rule, cited in the docstring.
  - Costs come from `sleeve.sell_costs`.
  - `render_lots` prints a header line naming the fee source and "FIFO order -- a sale consumes these top to bottom".
- [ ] **Step 4: Run the tests, then commit.**

### Task 13.2: `bands_view` (spec §5, read-only)

**Files:**
- Modify: `keel/commands/sleeve_report.py`
- Test: `tests/commands/test_sleeve_report.py`

**Interfaces:**
- Produces: `BandRow(asset, target_weight, weight, band, status: Literal["over", "under", "within"], candidate_sell_usd: Decimal | None, fee_drag_usd: Decimal | None)`.
- Produces: `BandsReport(rows, incomplete: tuple[str, ...])` and `bands_view(repo, config) -> BandsReport`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_an_overweight_asset_shows_its_candidate_sell_and_both_legs_fees(repo) -> None:
    """band = max(0.15 x w, 0.015); BTC target .5 -> band .075; held at .6 -> over."""
    _hold(repo, "BTC-USD", qty="0.006", mark="100000")  # $600
    _hold(repo, "ETH-USD", qty="0.1", mark="4000")      # $400
    report = bands_view(repo, _config(target_weights={"BTC": D("0.5"), "ETH": D("0.5")}))
    btc = next(r for r in report.rows if r.asset == "BTC")
    assert (btc.weight, btc.band, btc.status) == (D("0.6"), D("0.075"), "over")
    assert btc.candidate_sell_usd == D("100")
    fee = _config().fees.taker_pct
    assert btc.fee_drag_usd is not None and btc.fee_drag_usd >= 2 * D("100") * fee


def test_a_missing_mark_makes_the_whole_report_incomplete_not_wrong(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.006", mark="100000")
    _hold(repo, "ETH-USD", qty="0.1", mark=None)
    report = bands_view(repo, _config(target_weights={"BTC": D("0.5"), "ETH": D("0.5")}))
    assert report.incomplete == ("ETH",) and report.rows == ()
```

  `_hold(repo, product, qty, mark)` is a module helper. It opens one `dca` tranche, and writes one daily candle at `mark` when `mark` is not `None`.

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.** The drag is computed from the configured rate and per-product slippage on both legs. The rendered report says, per spec §5, that:
  - "a redeploy leg would spend this month's rail-14 buy cap and meet rail 8";
  - "#831 found band trimming not better than static DCA after fees".
- [ ] **Step 4: Run the tests, then commit.**

### Task 13.3: `keel dca trim --preview --view {lots,bands}`

**Files:**
- Modify: `keel/commands/dca.py`
- Test: `tests/commands/test_dca_trim_cli.py` (new)

- [ ] **Step 1: Write the failing tests.**

```python
def _trim(deployment, *args):
    db, config = deployment
    return CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", "trim", *args])


def test_off_a_tty_the_lots_view_prints_and_writes_nothing(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    watcher = sqlite3.connect(str(deployment[0]))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]
    result = _trim(deployment, "--preview", "--view", "lots")
    assert result.exit_code == 0, result.output
    assert "not tax advice" in result.output
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before


def test_trim_without_preview_is_a_usage_error(deployment) -> None:
    result = _trim(deployment)
    assert result.exit_code == 2 and "--preview" in result.output


def test_the_gain_view_does_not_exist_until_p14(deployment) -> None:
    assert _trim(deployment, "--preview", "--view", "gain").exit_code == 2
```

  P14 deletes `test_the_gain_view_does_not_exist_until_p14` in the same commit that adds `gain`.
- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.** Here `--view` is `click.Choice(["lots", "bands"])`, `required=True`. P14 makes `gain` the default.
- [ ] **Step 4: Run the suite, then commit.**

```bash
git add keel/commands/ tests/commands/
git commit -m "feat(dca): keel dca trim --preview --view lots|bands -- read-only, not tax advice (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P14 — `feat(rules): profit_take in preview, and --view gain as trim's default (#857)` · M · no schema

**PR body states:**
- S2: `profit_take` declares `Execution = Literal["preview"]`, and the registry test covers it.
- No `profit_take` promotion is part of this plan (spec §4, "no `profit_take` rule is promoted"). The kind exists, so the report and the proposal log can show what it would do.
- S1, S3 and S4 are unchanged.

### Task 14.1: `ProfitTake` (spec §4)

**Files:**
- Create: `keel/strategy/rules/profit_take.py`
- Modify: `keel/agent.py` (registry), `tests/strategy/test_rule_conformance.py`
- Test: `tests/strategy/test_profit_take.py` (new)

**Interfaces:**
- Produces: `ProfitTake(product_id, gain_pct: Decimal = 25, trim_pct: Decimal = 15, min_net_usd: Decimal = 5, cooldown_days: int = 30, min_hold_days: int = 30, execution: Execution = "preview", name="profit_take")`. `trim_pct` must be in `[10, 20]`.

- [ ] **Step 1: Write the failing tests.**

```python
COSTS = SellCosts(D("0.012"), D("0.0005"), "fallback:config.fees.taker_pct")


def _held(fill="100000", fee="0.45", qty="0.01"):
    return Holding("BTC-USD", (Lot(1, "dca", 0, D(qty), D(fill), D(fee)),))


def _day(close):
    return {Granularity.ONE_DAY: [_candle(60, close)]}


def test_the_trigger_is_gain_over_vwae_including_entry_fees() -> None:
    held = _held()                       # vwae = (1000 + 0.45) / 0.01 = 100045
    trigger = held.vwae * D("1.25")
    rule = ProfitTake("BTC-USD", min_net_usd=D("0"))
    assert rule.reduce_signal(held, _day(str(trigger)), COSTS) is not None
    assert rule.reduce_signal(held, _day(str(trigger - D("0.01"))), COSTS) is None


def test_the_size_is_trim_pct_of_the_holding() -> None:
    red = ProfitTake("BTC-USD", trim_pct=D("15"), min_net_usd=D("0")).reduce_signal(
        _held(), _day("200000"), COSTS)
    assert red is not None and red.qty == D("0.0015")


def test_the_fee_gate_at_its_boundary() -> None:
    held, close = _held(), D("130000")
    qty = held.qty * D("0.15")
    net = qty * (close * (1 - COSTS.slippage_pct) - held.vwae) - qty * close * COSTS.fee_pct
    assert ProfitTake("BTC-USD", min_net_usd=net).reduce_signal(held, _day(str(close)), COSTS)
    assert ProfitTake("BTC-USD", min_net_usd=net + D("0.01")).reduce_signal(
        held, _day(str(close)), COSTS) is None


@pytest.mark.parametrize("bad", [dict(trim_pct=D("9.99")), dict(trim_pct=D("20.01")),
                                 dict(gain_pct=D("0")), dict(min_net_usd=D("-1")),
                                 dict(execution="auto")])
def test_construction_refuses_nonsense(bad) -> None:
    with pytest.raises(ValueError):
        ProfitTake("BTC-USD", **bad)


def test_an_empty_holding_proposes_nothing() -> None:
    assert ProfitTake("BTC-USD").reduce_signal(Holding("BTC-USD", ()), _day("1"), COSTS) is None
```

  Also add `TestProfitTakeConformance(ReductionConformanceTests)`, with `_held()` and `_day("200000")` as its fixture.

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - The module docstring carries spec §4's evidence status verbatim in substance: the drift half was tested by #831 and was not better, and the gain half is untested. It also says why no promotion is planned.
  - The drift trigger is **not** a param (spec §4).
  - `cooldown_days` and `min_hold_days` are read by the pipeline (R15, R12).
- [ ] **Step 4: Run the tests and the invariants, then commit.**

### Task 14.2: `--view gain`, the default

**Files:**
- Modify: `keel/commands/sleeve_report.py` (`GainRow`, `gain_view(repo, config, *, gain_pct=None, trim_pct=None, product_id=None)`), `keel/commands/dca.py`
- Test: `tests/commands/test_sleeve_report.py`, `tests/commands/test_dca_trim_cli.py`

**Interfaces:**
- Produces: `GainRow(product_id, qty, vwae, cost_basis, mark, unrealised, gain_pct_used, trim_pct_used, triggered: bool, fifo_first_tranche: int | None, qty_to_sell, fee_usd, net_usd, verdict: str, legs: int)`.
- The parameters come from `--gain-pct` and `--trim-pct` when given. Otherwise they come from a non-disabled `profit_take` rule on the product. Otherwise they are the spec defaults (25, 15).

- [ ] **Step 1: Write the failing tests.**

```python
def test_gain_view_uses_the_products_profit_take_rule_params_when_there_is_one(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.01", mark="200000")
    repo.insert_rule("profit_take", {"product_id": "BTC-USD", "gain_pct": "40", "trim_pct": "10"},
                     status="candidate")
    [row] = gain_view(repo, _config())
    assert (row.gain_pct_used, row.trim_pct_used) == (D("40"), D("10"))
    assert row.triggered and row.fifo_first_tranche is not None
    assert row.verdict in {"would trim", "below fee gate"}


def test_the_flags_override_the_rule(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.01", mark="200000")
    [row] = gain_view(repo, _config(), gain_pct=D("500"))
    assert row.triggered is False and row.verdict == "below trigger"
```

  CLI: bare `keel dca trim --preview` now prints the gain view, with exit 0.

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - The verdict comes from calling the rule's own `reduce_signal` (built from the chosen params) on the cached candles. It invents none of the arithmetic.
  - `legs` comes from `sleeve.slice_qty`.
  - `--view` becomes `click.Choice(["gain", "lots", "bands"])` with default `gain`.
- [ ] **Step 4: Run the suite, then commit.**

```bash
git add keel/ tests/
git commit -m "feat(rules): profit_take in preview, and keel dca trim --preview's gain view (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Group F — The exit monitor, preview-only (operator step 3b)

## P15 — `feat(execution): the sleeve exit monitor -- transition alerts, doctor, keel dca exit --preview (#857)` · M · no schema · touches `packages/keel-core`

**Prerequisites merged:** P1, P2 and P3 (spec §7: "after #811's two doctor findings and #799's 'record the position first' land").

**PR body states:**
- S1–S4 are unchanged. The monitor reads candles, writes `agent_state["sleeve_exit:<product>"]` and emits events. It places nothing.
- R27's extra poll is candles only.

### Task 15.1: The pure monitor (R26)

**Files:**
- Create: `keel/execution/sleeve_exit.py`
- Test: `tests/execution/test_sleeve_exit.py` (new)

**Interfaces:**
- Produces:
  - the constants `DEFAULT_DD_PCT = Decimal("35")`, `DEFAULT_LOOKBACK_DAYS = 200`, `DEFAULT_SMA_PERIOD = 200`, `DEFAULT_CONFIRM_DAYS = 3` and `DEFAULT_WARN_PCT = Decimal("5")`;
  - `Level = Literal["clear", "near", "breached", "insufficient_history"]`;
  - `ExitWatch(product_id, level: Level, close, dd_level, sma, breached_arms: tuple[str, ...], ts)`;
  - `classify(product_id, daily: list[Candle], *, previous: Level | None = None, dd_pct=..., lookback_days=..., sma_period=..., confirm_days=..., warn_pct=..., arms=("drawdown", "sma")) -> ExitWatch`;
  - `STATE_PREFIX = "sleeve_exit:"`.

- [ ] **Step 1: Write the failing tests.**

```python
def _flat(n, price="100", high=None):
    return [_candle(d, price, high) for d in range(n)]


def test_the_drawdown_arm_at_above_and_below_its_level() -> None:
    """high 100, dd 35% -> level 65."""
    base = _flat(10)
    at = classify("BTC-USD", [*base, _candle(10, "65")], arms=("drawdown",), warn_pct=D("0"))
    below = classify("BTC-USD", [*base, _candle(10, "64.99")], arms=("drawdown",), warn_pct=D("0"))
    assert at.level == "clear" and at.dd_level == D("65")
    assert below.level == "breached" and below.breached_arms == ("drawdown",)


def test_the_sma_arm_needs_confirm_days_consecutive_closes_below() -> None:
    series = [*_flat(200, "100"), _candle(200, "90"), _candle(201, "90")]
    assert classify("BTC-USD", series, arms=("sma",), confirm_days=3).level != "breached"
    series.append(_candle(202, "90"))
    assert classify("BTC-USD", series, arms=("sma",), confirm_days=3).level == "breached"


def test_too_little_history_for_the_sma_is_reported_not_guessed() -> None:
    assert classify("BTC-USD", _flat(150), arms=("sma",)).level == "insufficient_history"


def test_near_has_hysteresis_in_at_warn_pct_out_at_twice_it() -> None:
    """dd level 65; warn 5% -> in at <= 68.25, out above 71.5."""
    base = _flat(10)
    assert classify("BTC-USD", [*base, _candle(10, "68")], arms=("drawdown",)).level == "near"
    assert classify("BTC-USD", [*base, _candle(10, "70")], arms=("drawdown",),
                    previous="near").level == "near"
    assert classify("BTC-USD", [*base, _candle(10, "70")], arms=("drawdown",),
                    previous="clear").level == "clear"
    assert classify("BTC-USD", [*base, _candle(10, "72")], arms=("drawdown",),
                    previous="near").level == "clear"


def test_no_candles_is_insufficient_history() -> None:
    assert classify("PAXG-USD", []).level == "insufficient_history"
```

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - The drawdown arm's `high` is `max(c.high for c in daily[-lookback_days:])`.
  - The SMA arm computes the `sma_period` simple moving average of closes ending at each of the last `confirm_days` bars. It needs `len(daily) >= sma_period + confirm_days - 1`, or the arm is insufficient.
  - The level is `breached` if any requested arm breached. It is `insufficient_history` if every requested arm is insufficient.
  - Otherwise the nearest distance `(close − level) / level` over the available arms decides `near` or `clear`, with hysteresis: in at `warn_pct`, out at `2 × warn_pct`.
  - The module docstring states spec §7's two-layer design, R26, and failure modes (a)–(d).
- [ ] **Step 4: Run the tests, then commit.**

### Task 15.2: The per-cycle watch (R27, Q7, the #811 regression)

**Files:**
- Modify: `keel/agent.py` (`_watch_sleeve_exits`, called after `_manage_stops` in live cycles and after the main pass in paper cycles; `LoopResult.exit_watch_transitions: list[ExitWatch]`)
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `_watch_sleeve_exits(broker, repo, config, products, sleeve_rules, now_ts, *, live: bool) -> list[ExitWatch]`, which returns transitions only.

- [ ] **Step 1: Write the failing tests.**

```python
def test_paxg_tranche_3_is_watched_although_no_live_rule_polls_paxg(repo, monkeypatch):
    """Q7 + R27: the only live rule is BTC; PAXG's daily series is polled for the monitor."""
    _seed_rules(repo, monkeypatch, (_AlwaysEnterRule(PRODUCT), "live"))
    repo.open_position(product_id="PAXG-USD", rule_name="turtle_breakout", opened_at=0,
                       qty=Decimal("0.0132"), entry_fill=Decimal("4673.23"),
                       entry_fee=Decimal("0.73"), initial_stop=Decimal("4521.76"))
    broker = FakeBroker(series={
        (PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")],
        ("PAXG-USD", Granularity.ONE_DAY): [_candle(d * 86_400, "4700") for d in range(20)],
    })

    result = run_once(broker, repo, _config(), now_ts=21 * 86_400)

    assert repo.get_state("sleeve_exit:PAXG-USD")["level"] in ("clear", "insufficient_history")
    assert [t.product_id for t in result.exit_watch_transitions] == ["PAXG-USD"]
    assert repo.get_state("last_feed_ts") == 21 * 86_400, "the extra poll does not own rail 12"


def _paxg_book(repo, monkeypatch, days=20, price="4700"):
    _seed_rules(repo, monkeypatch, (_AlwaysEnterRule(PRODUCT), "live"))
    repo.open_position(product_id="PAXG-USD", rule_name="turtle_breakout", opened_at=0,
                       qty=Decimal("0.0132"), entry_fill=Decimal("4673.23"),
                       entry_fee=Decimal("0.73"), initial_stop=Decimal("4521.76"))
    series = [_candle(d * 86_400, price) for d in range(days)]
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")],
                                ("PAXG-USD", Granularity.ONE_DAY): series})
    return broker, series


def test_a_transition_fires_once_and_a_steady_level_fires_nothing(repo, monkeypatch):
    broker, series = _paxg_book(repo, monkeypatch)
    first = run_once(broker, repo, _config(), now_ts=21 * 86_400)
    second = run_once(broker, repo, _config(), now_ts=21 * 86_400 + 3_600)
    assert len(first.exit_watch_transitions) == 1 and second.exit_watch_transitions == []

    series.append(_candle(20 * 86_400, "2800"))  # 40% below the 4700 high
    third = run_once(broker, repo, _config(), now_ts=22 * 86_400)
    assert [t.level for t in third.exit_watch_transitions] == ["breached"]


def test_clearing_the_retry_record_changes_nothing(repo, monkeypatch):
    """#811 as a test: the monitor reads the positions ledger, never `unbracketed:`."""
    broker, _series = _paxg_book(repo, monkeypatch)
    repo.set_state("unbracketed:PAXG-USD", {"stop": "4521.76", "target": "5000", "qty": "0.0132"})
    run_once(broker, repo, _config(), now_ts=21 * 86_400)
    before = repo.get_state("sleeve_exit:PAXG-USD")

    repo.set_state("unbracketed:PAXG-USD", None)
    after_run = run_once(broker, repo, _config(), now_ts=21 * 86_400 + 3_600)

    assert repo.get_state("sleeve_exit:PAXG-USD")["level"] == before["level"]
    assert after_run.exit_watch_transitions == []
```

  In a live cycle `reconcile_unbracketed_positions` may try to re-place the PAXG bracket from the retry record on the first run. That is fine: the `FakeBroker` accepts it, and a resting bracket makes the tranche **bracketed**, which is still no transition on the second run. If that makes the first run's state absent (the tranche is no longer watched), give the `FakeBroker` a `place_order` that refuses SELLs for this test (`PlaceResult(success=False, ...)`). The point under test is the retry record, not the bracket.

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - Watched products are those with an open tranche where `not reconcile._has_resting_bracket(repo, p)`.
  - For a watched product outside `products`, in a live cycle, call `market_feed.poll_once(broker, repo, extra, [Granularity.ONE_DAY], now_ts=now_ts)` inside `try`. It must not set `last_feed_ts`.
  - Read `completed_days({ONE_DAY: repo.get_candles(p, ONE_DAY)})`.
  - Take the parameters from a `sleeve_exit` rule on the product when there is one (P16), otherwise the defaults.
  - Write `{"level", "ts", "close", "dd_level", "sma"}` with money as `str`.
  - Report a transition when `level != previous_level`, where a missing previous state counts as a transition.
  - Wrap the whole step in `try/except Exception`, with `log_exception`.
- [ ] **Step 4: Run the tests, then commit.**

### Task 15.3: The `sleeve.exit_watch` notification, the doctor finding, and `keel dca exit --preview`

**Files:**
- Modify: `packages/keel-core/keel_core/notifications.py` (`EventSpec("sleeve.exit_watch", "execution", WARN)`), `keel/notifications.py` (an `exit_watch_transitions` parameter), `keel/commands/doctor.py` (`exit_watch_findings(records: dict[str, dict]) -> list[Finding]`, named `sleeve.exit_watch`), `keel/commands/dca.py` (`exit --preview`), `keel/commands/sleeve_report.py` (`render_exit_watch`)
- Test: `tests/test_notifications.py`, `tests/commands/test_doctor_sleeve_rules.py`, `tests/commands/test_dca_exit_cli.py` (new)

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_notifications.py  (add `watch: tuple = ()` to `_state`, passed as exit_watch_transitions)
def _watch(level):
    return ExitWatch("PAXG-USD", level, D("4300"), D("3055"), None, (), 1)


@pytest.mark.parametrize("level", ["near", "breached", "clear"])
def test_every_exit_watch_transition_is_one_event(level):
    [event] = [e for e in _state(watch=(_watch(level),)) if e.key == "sleeve.exit_watch"]
    assert event.fields["level"] == level and "PAXG-USD" in event.message


# tests/commands/test_doctor_sleeve_rules.py
def test_exit_watch_findings_warn_on_near_and_breached_only() -> None:
    [warn] = exit_watch_findings({"PAXG-USD": {"level": "near", "close": "4300", "dd_level": "3055"}})
    assert warn.status == WARN and warn.products == ("PAXG-USD",)
    [ok] = exit_watch_findings({"BTC-USD": {"level": "insufficient_history"}})
    assert ok.status == OK and "not judged" in ok.detail
    [clear] = exit_watch_findings({"BTC-USD": {"level": "clear"}})
    assert clear.status == OK


def test_no_exit_watch_records_still_reports_the_finding_ok() -> None:
    """A deployment with no PAXG-style watched product yet -- the exact shape
    `test_gather_findings_covers_every_check_over_a_seeded_db` (`tests/commands/test_doctor.py:603`)
    runs over -- has no per-product record for `exit_watch_findings` to turn into a per-record
    Finding at all. As P3's `ledger.drift`/`ledger.venue_drift` do for their own empty case,
    return a single OK sentinel named `sleeve.exit_watch` rather than `[]` -- an empty list
    would make the name vanish from `gather_findings`' output on exactly the deployment the
    exact-set test seeds, and the per-record tests above (one Finding per dict entry) are
    unaffected: this is the `records == {}` case only, not a change to their shape.
    """
    [ok] = exit_watch_findings({})
    assert ok.name == "sleeve.exit_watch" and ok.status == OK


# tests/commands/test_dca_exit_cli.py
def test_exit_preview_prints_levels_and_writes_nothing(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    repo = _repo(deployment[0])
    repo.open_position(product_id="BTC-USD", rule_name="dca", opened_at=0, qty=D("0.001"),
                       entry_fill=D("100000"), entry_fee=D("0"))
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(d, "100000") for d in range(30)])
    watcher = sqlite3.connect(str(deployment[0]))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]
    db, config = deployment
    out = CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", "exit",
                                   "--preview"]).output
    assert "BTC-USD" in out and "preview only: an automatic sale is not built" in out
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
```

  The notification wording follows the event's level: `near` and `breached` are worded as warnings, and `clear` as a recovery. The write list stays `[NOTIFIED_WINDOWS_KEY]`.
- [ ] **Step 2: Run the tests to see them fail** with `PYTHONPATH="$PWD:$PWD/packages/keel-core" uv run python -m pytest ...`.
- [ ] **Step 3: Implement.**
  - **`exit_watch_findings` returns one Finding per `records` entry, but never an empty list.**
    With `records` non-empty this is unchanged -- each product's record becomes its own
    Finding, named `sleeve.exit_watch`, WARN for `near`/`breached` and OK otherwise (`clear` or
    `insufficient_history`), exactly as the per-record tests above already pin. With `records == {}`
    (nothing is being watched yet), return a single `[Finding("sleeve.exit_watch", OK, ...)]`
    sentinel instead of `[]` -- the same shape P3's `ledger_drift_findings`/`venue_drift_findings`
    use for their own nothing-to-report case. Without this, a repo with no watched product
    (exactly what `test_gather_findings_covers_every_check_over_a_seeded_db`,
    `tests/commands/test_doctor.py:603`, seeds) would make `gather_findings` never emit the
    name at all, and the exact-set assertion below would fail on a MISSING name.
  - **#886: add `"sleeve.exit_watch"` to the exact-set assertion.** Wiring `exit_watch_findings` into `gather_findings` adds a name `test_gather_findings_covers_every_check_over_a_seeded_db` (`tests/commands/test_doctor.py:603`) does not yet list, turning that existing test red. Add `"sleeve.exit_watch"` to its `{f.name for f in findings} == {...}` set, beside the names P2, P3 and P10 added -- check the literal's actual current contents first.
- [ ] **Step 4: Run the suite with the same `PYTHONPATH`, then commit.** Expected: PASS.

```bash
git add keel/ packages/keel-core/ tests/
git commit -m "feat(execution): the sleeve exit monitor -- levels on transition, doctor, keel dca exit --preview (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P16 — `feat(rules): sleeve_exit, preview-only (#857)` · S · no schema

**PR body states:**
- S2: `SleeveExit` declares `Execution = Literal["preview"]`.
- The whole-sleeve `Reduction` is sliced by rail 2, is exempt from `min_hold_days` (R13), and is **proposed, never placed** (spec §7, "Automatic selling stays off").
- Closes #857. After this PR every in-scope feature of the design exists in preview.

### Task 16.1: `SleeveExit`

**Files:**
- Create: `keel/strategy/rules/sleeve_exit.py`
- Modify: `keel/agent.py` (registry), `tests/strategy/test_rule_conformance.py`
- Test: `tests/strategy/test_sleeve_exit_rule.py` (new), `tests/test_agent.py`

**Interfaces:**
- Produces: `SleeveExit(product_id, dd_pct: Decimal = 35, lookback_days: int = 200, sma_period: int = 200, confirm_days: int = 3, arms: tuple[str, ...] = ("drawdown", "sma"), execution: Execution = "preview", name="sleeve_exit")`.
- It declares `tuple_params = ("arms",)`, and `arms` must be a non-empty subset of `{"drawdown", "sma"}`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_it_proposes_the_whole_holding_only_on_breached() -> None:
    held = Holding("BTC-USD", (Lot(1, "dca", 0, D("0.002"), D("100000"), D("0")),
                               Lot(2, "dca", 0, D("0.001"), D("90000"), D("0"))))
    crash = {Granularity.ONE_DAY: [*[_candle(d, "100000") for d in range(10)], _candle(10, "60000")]}
    near = {Granularity.ONE_DAY: [*[_candle(d, "100000") for d in range(10)], _candle(10, "67000")]}
    rule = SleeveExit("BTC-USD", arms=("drawdown",))
    red = rule.reduce_signal(held, crash, COSTS)
    assert red is not None and red.qty == D("0.003") and red.reason == "sleeve_exit"
    assert rule.reduce_signal(held, near, COSTS) is None, "near alerts; only breached proposes"


@pytest.mark.parametrize("arms", [(), ("trailing",), ("drawdown", "atr")])
def test_arms_are_a_closed_vocabulary(arms) -> None:
    with pytest.raises(ValueError):
        SleeveExit("BTC-USD", arms=arms)


# in tests/test_agent.py (the autonomous `repo` fixture)
def test_under_autonomous_mode_a_breach_is_proposed_and_nothing_is_sold(repo, monkeypatch):
    now = 30 * 86_400
    _seed_rules(repo, monkeypatch, (SleeveExit(PRODUCT, arms=("drawdown",)), "live"))
    _seed_open_position(repo, PRODUCT, Decimal("0.5"), Decimal("100"), ts=now - 2 * 86_400,
                        rule_name="dca")  # bought this week: R13 exempts sleeve_exit from min_hold
    series = [*(_candle(d * 86_400, "100") for d in range(29)), _candle(29 * 86_400, "60")]
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=now)

    [row] = repo.get_sell_proposals()
    assert (row["rule_kind"], row["decision"]) == ("sleeve_exit", "preview")
    assert row["qty"] * row["legs"] >= Decimal("0.5") and row["legs"] >= 1
    assert [c for c in broker.place_calls if c["side"] is Side.SELL] == []
```

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - `reduce_signal` calls `sleeve_exit.classify(self.product_id, completed_days(candles_by_tf), **self._monitor_params())`, and returns `Reduction(product_id, holding.qty, "sleeve_exit", trigger=<levels as str>, expected_price=close, ts)` only when the level is `breached` and `holding.qty > 0`.
  - The monitor (P15) reads the same params from this rule.
- [ ] **Step 4: Run the suite, then commit.**

```bash
git add keel/ tests/
git commit -m "feat(rules): sleeve_exit -- a structural-break proposal, never an automatic sale (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Group G — The gated placement path, off by default (R22, R28)

Merge P17 and P18 only when the operator decides to enable a confirmed sleeve sale. Every earlier PR stands without them.

## P17 — `feat(autonomy): keel autonomy on --sells -- a separate, TTY-armed sells window, schema v23 (#857)` · M · **schema v23** · touches `packages/keel-core`

**PR body states:**
- **S1:** this is the switch S1 names.
  - A plain `keel autonomy on` does not arm it (tested).
  - Arming it needs a TTY and a typed `yes`, through its own gate function and its own capability row.
  - `keel autonomy off` clears it.
  - The web effect scan and `WEB_FORBIDDEN_NAMES` forbid `set_sells_window` in `keel/web` and `keel/mcp`.
- **S2:** nothing reads the window yet. P18 is the only reader.
- **Schema v23** adds `profile.sells_autonomous` and `profile.sells_until`. There is no backfill.

### Task 17.1: The v23 migration and `Profile`

**Files:**
- Modify: `keel/data/db.py` (the `profile` DDL gains `sells_autonomous INTEGER NOT NULL DEFAULT 0` and `sells_until INTEGER`; add `_migrate_v23_profile_sells_window` with the `PRAGMA table_info` guard; set `SCHEMA_VERSION = 23`), `packages/keel-core/keel_core/types.py` (`Profile.sells_autonomous: bool = False`, `Profile.sells_until: int | None = None`, `is_autonomous_for_sells(now_ts) -> bool`), `keel/data/repository.py` (`get_profile` reads both; a new `set_sells_window(value, now_ts, expires_ts=None)` writes a `sells_window_set` audit event), `keel/data/audit.py` (`"sells_window_set": "profile"`)
- Test: `tests/data/test_migrations.py`, `tests/core/test_types.py` (or wherever `Profile.is_autonomous` is tested; run `grep -rn "is_autonomous(" tests` to find it), `tests/data/test_repository.py`, `tests/data/test_db.py` (the deliberate tripwire — see Global Constraints and P6 Task 1, which last bumped it to 22)

- [ ] **Step 1: Write the failing tests.**

```python
def test_a_v22_profile_gains_a_closed_sells_window_no_backfill() -> None:
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (22)")
    conn.execute("CREATE TABLE profile (id INTEGER PRIMARY KEY CHECK (id = 1), autonomous INTEGER "
                 "NOT NULL DEFAULT 0, autonomous_until INTEGER, updated_ts INTEGER NOT NULL)")
    conn.execute("INSERT INTO profile VALUES (1, 1, NULL, 5)")  # live autonomy is ON
    conn.commit()
    db.migrate(conn)
    profile = Repository(conn).get_profile()
    assert profile.is_autonomous(10) is True
    assert profile.is_autonomous_for_sells(10) is False, "S1: buy autonomy never implies sells"


def test_the_sells_window_lapses_like_autonomy() -> None:
    p = Profile(sells_autonomous=True, sells_until=100)
    assert p.is_autonomous_for_sells(99) and not p.is_autonomous_for_sells(100)
```

  Also bump the deliberate tripwire in `tests/data/test_db.py`, which P6 last left at 22:

```bash
sed -i '' \
  -e 's/def test_schema_version_is_22/def test_schema_version_is_23/' \
  -e 's/assert SCHEMA_VERSION == 22/assert SCHEMA_VERSION == 23/' \
  tests/data/test_db.py
```

- [ ] **Step 2: Run the tests to see them fail** with `PYTHONPATH="$PWD:$PWD/packages/keel-core" uv run python -m pytest ...`. The new v23 tests fail for `AttributeError`/`is_autonomous_for_sells` reasons; `tests/data/test_db.py::test_schema_version_is_23` fails because `SCHEMA_VERSION` is still `22`.
- [ ] **Step 3: Implement.** The migration docstring follows v7's reasoning: "no row means not armed ... seeding one would manufacture a consent record no human gave".
- [ ] **Step 4: Run the tests, including `tests/data/test_db.py`, then commit.**

### Task 17.2: `keel autonomy on --sells`, `off`, and `show`

**Files:**
- Modify: `keel/commands/autonomy.py` (`autonomy_sells_on_gate(config, for_hours, now_ts)`, which calls `_require_interactive_confirmation("turn SELL autonomy ON", ...)`; `--sells` on `on`; `off` clears both; `show` prints both), `keel/capabilities.py`, `tests/test_capabilities.py` (the count wording), `tests/execution/test_sell_side_invariants.py` (`"set_sells_window"`)
- Test: `tests/commands/test_autonomy_sells.py` (new)

- [ ] **Step 1: Write the failing tests.**

```python
def test_plain_autonomy_on_does_not_arm_sells(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    _run(deployment, "autonomy", "on", input="yes\n")
    assert _repo(deployment[0]).get_profile().is_autonomous_for_sells(int(time.time())) is False


def test_on_sells_needs_a_terminal_and_a_typed_yes(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    assert _run(deployment, "autonomy", "on", "--sells").exit_code != 0
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    assert _run(deployment, "autonomy", "on", "--sells", "--for-hours", "1", input="yes\n").exit_code == 0
    assert _repo(deployment[0]).get_profile().is_autonomous_for_sells(int(time.time()))


def test_autonomy_off_clears_the_sells_window_too(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    _run(deployment, "autonomy", "on", "--sells", input="yes\n")
    _run(deployment, "autonomy", "off")
    assert _repo(deployment[0]).get_profile().is_autonomous_for_sells(int(time.time())) is False
```

  `_run(deployment, *args, input=None)` is this module's `CliRunner` helper, built like `tests/commands/test_dca_cli.py`'s, with the full argument list instead of `dca plan`.

- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.** The capability row:

```python
    Capability(
        module="keel.commands.autonomy",
        function="autonomy_sells_on_gate",
        surface="cli",
        invocation="keel autonomy on --sells",
        increases=(
            "a confirmed sleeve sale may be placed at the venue, for the window named -- the ONLY "
            "switch that lets any sell-side rule's proposal become an order; buy autonomy never "
            "implies it"
        ),
    ),
```

- [ ] **Step 4: Run the suite, including `tests/web/test_server.py`, then commit.**

```bash
git add keel/ packages/keel-core/ tests/
git commit -m "feat(autonomy): keel autonomy on --sells -- a separate, TTY-armed sells window, schema v23 (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## P18 — `feat(dca): --confirm <proposal-id> places one sleeve sale behind the sells window and a typed yes (#857)` · M · no schema

**PR body states:**
- **S1** is the point of this PR:
  - `executor.reduce` joins `RUN_ORDER_CALLERS`, and an AST test asserts its one `_run_order` call is inside `if sleeve.sells_released(repo, now_ts):`.
  - Behavioural tests prove that global autonomy alone declines, and that a missing TTY or a missing typed `yes` declines.
  - A `paper`-status rule's proposal can never be confirmed (R16).
  - `_handle_reductions` never passes `execution=`, which is AST-tested, so the cycle never places.
- **S2:** there is still no `execution: auto`.
- **S3:** the placed order passes `guards.check` again at placement, through `_run_order`.
- **S4:** `sleeve_sale_gate` is CLI-only, and the web effect scan covers it.

### Task 18.1: `sleeve.sells_released` and the confirm branch of `executor.reduce`

**Files:**
- Modify: `keel/execution/sleeve.py` (`sells_released(repo, now_ts) -> bool`), `keel/execution/executor.py` (`reduce(..., execution: Literal["preview", "confirm"] = "preview", confirm_fn: ConfirmFn | None = None)`), `tests/execution/test_sell_side_invariants.py`
- Test: `tests/execution/test_reduce.py`

- [ ] **Step 1: Write the failing tests.**

```python
def test_confirm_without_the_sells_window_declines_even_under_global_autonomy(repo) -> None:  # noqa: F811
    repo.set_autonomous(True, now_ts=0)
    broker = FakeBroker()
    result = _run(repo, broker, execution="confirm", confirm_fn=lambda preview: True)
    assert result.decision == "declined" and "sells window" in result.reason
    assert broker.place_calls == []


def test_confirm_with_the_window_and_a_yes_places_one_leg_and_books_per_tranche(repo) -> None:  # noqa: F811
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    broker = FakeBroker()
    result = _run(repo, broker, execution="confirm", confirm_fn=lambda preview: True)
    assert result.decision == "placed" and len(broker.place_calls) == 1
    row = repo.get_sell_proposal(result.proposal_id)
    assert row["order_id"] is not None
    assert repo.get_order(row["order_id"])["confirmation"] == "confirm_sells"


def test_a_declined_typed_gate_places_nothing(repo) -> None:  # noqa: F811
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    broker = FakeBroker()
    assert _run(repo, broker, execution="confirm", confirm_fn=lambda p: False).decision == "declined"
    assert broker.place_calls == []


def _bracketed(repo, broker, config, *, qty="0.002", stop="95000", target="130000"):
    """Open a position AND place a REAL resting protective bracket for it, via `place_bracket`
    itself -- the fixture `_clear_resting_bracket` actually has to cancel, not a hand-rolled
    row.

    #883: `_held` alone only writes the `positions` ledger row `reduce`'s `holding` argument is
    built from -- it inserts no `orders` row. But `reduce`'s remainder math (`protecting_remainder`
    below, in Step 3) is computed from `_held_position` (`executor.py:897`), which reads FILLED
    LIVE orders (`repo.get_orders(mode="live", ..., status="filled")`), not the `positions`
    table. Without a matching filled BUY order, `_held_position` sees zero held, `remainder`
    comes out negative, `protecting_remainder` is `False`, and nothing re-brackets -- not because
    the code is wrong, but because the fixture never gave `_held_position` anything to find. So
    this fixture seeds BOTH: the `positions` row (`_held`, for `holding_of`) and the FILLED BUY
    order (for `_held_position`), at the same qty, the way a real filled entry would leave both
    behind.

    Returns the `Holding` `reduce` needs.
    """
    holding = _held(repo, qty)
    repo.insert_order(
        dict(
            mode="live",
            product_id="BTC-USD",
            side=Side.BUY.value,
            order_type="market",
            qty=D(qty),
            limit_price=D("100000"),
            status="filled",
            fee=D("0.6"),
            created_at=NOW_TS - 200,
            updated_at=NOW_TS - 200,
        )
    )
    order_id = executor.place_bracket(broker, repo, config, product_id="BTC-USD", qty=D(qty),
                                      stop=D(stop), target=D(target), rule_name="turtle_breakout",
                                      now_ts=NOW_TS - 100)
    assert order_id is not None, "fixture setup: the bracket must actually be resting"
    return holding


def _confirm_recording_cancel_state(broker, *, answer=True):
    """A `confirm_fn` that answers `answer` but first RECORDS whether `broker.cancel_calls` is
    already non-empty -- pinning the R34 ordering property (confirm asked BEFORE the cancel
    runs) directly, independent of any call-count or remainder side effect. Round 3: without
    this, a confirmed test (B or C below) cannot go red against a re-introduced
    unconditional-cancel-before-confirm bug, because for a confirm that always answers `True`
    the cancel happens either way and every call-count and `cancel_calls`-truthiness assertion
    is satisfied regardless of ordering.
    """
    seen: dict[str, list] = {"cancel_calls_at_confirm_time": None}

    def _confirm(preview):
        seen["cancel_calls_at_confirm_time"] = list(broker.cancel_calls)
        return answer

    return _confirm, seen


def test_883_a_declined_confirm_leaves_the_resting_bracket_untouched(repo) -> None:  # noqa: F811
    """#883: `_clear_resting_bracket` used to run BEFORE the typed-yes gate, so a "no" (or no
    TTY) left a stopped tranche naked with no `unbracketed:` record -- the cancel had already
    happened and nothing said so. The gate must be asked FIRST; the book is untouched on a no.

    Round 2: the retry record itself must not be written on a decline either. Step 3 writes it
    only inside `_confirm_then_cancel`, AFTER `confirm_fn` returns True and BEFORE the cancel --
    a decline returns out of that closure before that line runs, so nothing is ever written. (An
    earlier draft wrote it unconditionally before the confirm gate ran at all, which failed this
    exact assertion; see Step 3's docstring for why `reconcile._has_resting_bracket`, not the
    sweep's `if not intent: continue`, is what would have made even that stale record harmless.)
    """
    config = _config()
    broker = FakeBroker()
    _bracketed(repo, broker, config)
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    result = executor.reduce(_red(qty="0.001"), broker=broker, repo=repo, config=config,
                             holding=sleeve.holding_of(repo, "BTC-USD"), costs=COSTS, rule_id=7,
                             rule_status="live", now_ts=NOW_TS, execution="confirm",
                             confirm_fn=lambda preview: False)
    assert result.decision == "declined"
    assert broker.cancel_calls == [], "a typed no must never reach the exchange cancel"
    resting = [o for o in repo.get_orders(mode="live", product_id="BTC-USD", status="pending")
              if o["side"] == "SELL"]
    assert len(resting) == 1, "the bracket is still resting -- nothing was cancelled"
    assert repo.get_state("unbracketed:BTC-USD") is None, "no cancel happened, so nothing to heal"


def test_883_a_partial_confirmed_leg_re_brackets_the_remainder_like_scale_out(repo) -> None:  # noqa: F811
    """A "yes" that sells only part of a bracketed position must leave the remainder protected,
    exactly as `scale_out` re-brackets after a partial sell (#502) -- a sleeve sale is not
    exempt from that rule merely because a human, not a rule, triggered it."""
    config = _config()
    broker = FakeBroker()
    _bracketed(repo, broker, config, qty="0.002", stop="95000", target="130000")  # holds 0.002
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    confirm_fn, seen = _confirm_recording_cancel_state(broker, answer=True)
    result = executor.reduce(_red(qty="0.001"), broker=broker, repo=repo, config=config,
                             holding=sleeve.holding_of(repo, "BTC-USD"), costs=COSTS, rule_id=7,
                             rule_status="live", now_ts=NOW_TS, execution="confirm",
                             confirm_fn=confirm_fn)
    assert result.decision == "placed"
    assert seen["cancel_calls_at_confirm_time"] == [], (
        "the cancel must not have run yet when confirm_fn was asked -- a call-count or "
        "cancel_calls-truthiness assertion alone cannot tell this apart from a naive ordering, "
        "because confirm answers True here either way (#883 round 3)"
    )
    assert broker.cancel_calls, "the original bracket had to be cancelled to sell against it"
    # Three `place_order` calls total: the original bracket (fixture setup), the SELL leg
    # itself (`reduce`'s own `_run_order` call -- this is what makes `result.decision ==
    # "placed"` true in the first place), and a NEW bracket for the remainder (0.001) at the
    # SAME stop/target.
    assert len(broker.place_calls) == 3
    assert repo.get_state("open_stop:BTC-USD") == D("95000")
    assert repo.get_state("open_target:BTC-USD") == D("130000")
    assert repo.get_state("unbracketed:BTC-USD") is None, "place_bracket clears it on success"
    resting = [o for o in repo.get_orders(mode="live", product_id="BTC-USD", status="pending")
              if o["side"] == "SELL"]
    assert len(resting) == 1, "the old bracket is gone; exactly the new, smaller one rests"


def test_883_a_partial_confirmed_leg_repoints_the_surviving_tranche_at_the_new_bracket(repo) -> None:  # noqa: F811
    """#883 (round 3): `scale_out` repoints the tranche with
    `repo.set_position_bracket(position["id"], bracket_order_id)` after it re-brackets the
    remainder (`executor.py:2614`) -- Task 18.1's first draft re-bracketed but never repointed,
    so the tranche kept naming the CANCELLED bracket. When the new bracket eventually filled,
    `get_position_for_bracket` found nothing for it (`reconcile.exit_without_position_context`:
    no `trade_outcomes` row, the tranche never closes), and `position.unprotected` WARNed every
    cycle in the meantime even though a bracket really was resting."""
    config = _config()
    broker = FakeBroker()
    _bracketed(repo, broker, config, qty="0.002", stop="95000", target="130000")
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    result = executor.reduce(_red(qty="0.001"), broker=broker, repo=repo, config=config,
                             holding=sleeve.holding_of(repo, "BTC-USD"), costs=COSTS, rule_id=7,
                             rule_status="live", now_ts=NOW_TS, execution="confirm",
                             confirm_fn=lambda preview: True)
    assert result.decision == "placed"
    (position,) = repo.get_open_positions("BTC-USD")
    resting = [o for o in repo.get_orders(mode="live", product_id="BTC-USD", status="pending")
              if o["side"] == "SELL"]
    (new_bracket,) = resting
    assert position["bracket_order_id"] == new_bracket["id"], (
        "the tranche must point at the NEW bracket, not the one this leg cancelled"
    )


def test_883_a_full_close_does_not_attempt_to_re_bracket(repo) -> None:  # noqa: F811
    """The complement: a leg that closes the WHOLE bracketed position leaves nothing to
    re-protect, and must not try -- `place_bracket` for qty 0 has no meaning.

    #883 (round 3): a full close now writes the `unbracketed:` crash-ledger record too (Step 3
    gates the write on `has_levels`, not on `protecting_remainder`, so a rejected full-close SELL
    has a retry record to fall back on -- see the venue-rejection test below). A SUCCESSFUL full
    close must not be the one path that record silently outlives forever: `place_bracket`, its
    only other clearer, is never called when there is no remainder to re-bracket, so the success
    path must clear it explicitly instead.
    """
    config = _config()
    broker = FakeBroker()
    _bracketed(repo, broker, config, qty="0.002", stop="95000", target="130000")
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    result = executor.reduce(_red(qty="0.002"), broker=broker, repo=repo, config=config,
                             holding=sleeve.holding_of(repo, "BTC-USD"), costs=COSTS, rule_id=7,
                             rule_status="live", now_ts=NOW_TS, execution="confirm",
                             confirm_fn=lambda preview: True)
    assert result.decision == "placed"
    # Two `place_order` calls total: the original bracket (fixture setup) and the SELL leg
    # itself. No remainder, so no third call for a new bracket.
    assert len(broker.place_calls) == 2
    assert repo.get_state("unbracketed:BTC-USD") is None, (
        "a successful full close must not leave the pre-cancel crash-ledger record standing "
        "forever -- nothing will ever clear it otherwise, since place_bracket is never called"
    )
    resting = [o for o in repo.get_orders(mode="live", product_id="BTC-USD", status="pending")
              if o["side"] == "SELL"]
    assert resting == []


# Add `from keel_broker_api.results import PlaceResult` to this file's imports -- used below,
# not yet imported here (`test_executor.py` already has it, at its own line 26).
class _RejectsSecondPlacement(FakeBroker):
    """The venue accepts the FIRST `place_order` -- `_bracketed`'s own resting-bracket setup --
    and REJECTS every one after, WITHOUT raising. That is the shape of an ordinary placement
    refusal (insufficient funds, a size out of band) that `_run_order`'s own
    `place_result.success` already handles as `ExecutionResult(placed=False, ...)` -- distinct
    from `_RefusingBroker` above, which models a scope error that RAISES."""

    def place_order(self, spec, *, idempotency_key=None):  # noqa: ANN001, ANN202
        if not self.place_calls:
            return super().place_order(spec, idempotency_key=idempotency_key)
        self.place_calls.append({"spec": spec})
        self.events.append("place")
        return PlaceResult(success=False, broker_order_id=None, reason="no funds")


def test_883_a_venue_rejected_full_close_after_the_cancel_leaves_a_retry_record_and_is_not_mislabelled_a_decline(repo) -> None:  # noqa: F811
    """#883 (round 3): a full close has `protecting_remainder == False` (the remainder is 0), so
    the record used to be written only `if protecting_remainder:` -- nothing at all for a full
    close. A SELL the venue then rejects, after the cancel already ran, left the position naked
    with NO retry record for the sweep to read, and the result was mislabelled "declined at the
    confirm gate" even though the operator said yes and the exchange, not the operator, refused.
    """
    config = _config()
    broker = _RejectsSecondPlacement()
    _bracketed(repo, broker, config, qty="0.002", stop="95000", target="130000")
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    result = executor.reduce(_red(qty="0.002"), broker=broker, repo=repo, config=config,
                             holding=sleeve.holding_of(repo, "BTC-USD"), costs=COSTS, rule_id=7,
                             rule_status="live", now_ts=NOW_TS, execution="confirm",
                             confirm_fn=lambda preview: True)
    assert result.decision == "failed", "the operator said yes; the venue refused the order"
    assert "declined at the confirm gate" not in result.reason
    assert repo.get_state("unbracketed:BTC-USD") is not None, (
        "the bracket is gone and the SELL never filled -- the retry record must survive so the "
        "sweep re-brackets the position, sized off the ledger's still-full tranche"
    )
    resting = [o for o in repo.get_orders(mode="live", product_id="BTC-USD", status="pending")
              if o["side"] == "SELL"]
    assert resting == [], "the original bracket really is gone; nothing rests until the sweep heals it"


def test_887_a_full_close_clears_position_rule_and_open_levels_too(repo) -> None:  # noqa: F811
    """#887: the success path used to clear only `unbracketed:` on a genuine full close --
    from the PRE-SALE `_held_position` remainder, which said `remainder <= 0` and so never
    even looked at `position_rule:`/`open_stop:`/`open_target:`. Left set, a stale `open_stop`
    makes rail 9 veto the very next entry on this product with `no_stop_widening`
    (`guards.py:657-664`), and would make `has_levels` true on a later `reduce` of a product
    that no longer holds anything. Mirror `agent._handle_exits`'s own EXIT path exactly
    (`agent.py:981-987`): once `repo.get_open_positions` says nothing is left, all four keys
    clear together, not just the crash-ledger one.
    """
    config = _config()
    broker = FakeBroker()
    _bracketed(repo, broker, config, qty="0.002", stop="95000", target="130000")
    repo.set_state("position_rule:BTC-USD", {"rule_name": "turtle_breakout", "opened_at": 0})
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    result = executor.reduce(_red(qty="0.002"), broker=broker, repo=repo, config=config,
                             holding=sleeve.holding_of(repo, "BTC-USD"), costs=COSTS, rule_id=7,
                             rule_status="live", now_ts=NOW_TS, execution="confirm",
                             confirm_fn=lambda preview: True)
    assert result.decision == "placed"
    assert repo.get_open_positions("BTC-USD") == [], "fixture setup: this must be a true full close"
    assert repo.get_state("position_rule:BTC-USD") is None, (
        "a stale owning-rule record outlives a position that no longer exists"
    )
    assert repo.get_state("open_stop:BTC-USD") is None, (
        "a stale open_stop would veto the next entry on this product under rail 9's "
        "no_stop_widening"
    )
    assert repo.get_state("open_target:BTC-USD") is None
    assert repo.get_state("unbracketed:BTC-USD") is None


class _ShortFillingBroker(FakeBroker):
    """Fills every SELL at `fill_ratio` of what was ordered, reported through `get_order` the
    way the venue does -- the same shape as `tests/test_agent.py`'s `_ShortFillingBroker`
    (#446), rebuilt here because `_run_order` reads the fill through `broker.get_order`, which
    the shared `FakeBroker` above does not implement at all.
    """

    def __init__(self, fill_ratio, **kwargs):  # noqa: ANN001
        super().__init__(**kwargs)
        self.fill_ratio = fill_ratio
        self._last_sell_size = None

    def place_order(self, spec, *, idempotency_key=None):  # noqa: ANN001, ANN202
        if getattr(spec, "side", None) == Side.SELL:
            self._last_sell_size = Decimal(spec.base_size)
        return super().place_order(spec, idempotency_key=idempotency_key)

    def get_order(self, order_id):  # noqa: ANN001, ANN201
        ordered = self._last_sell_size or Decimal("0")
        return OrderStatus(order_id=order_id, status="FILLED",
                           filled_size=ordered * self.fill_ratio,
                           average_filled_price=Decimal("96000"), total_fees=Decimal("0.50"))


# Add `OrderStatus` to the `keel_broker_api.results` import above (alongside `PlaceResult`) --
# used by `_ShortFillingBroker` only, not yet imported in this file. Add a bare `import logging`
# too, for `caplog.at_level(logging.CRITICAL)` below -- this file has no other CRITICAL-log test.


def test_887_a_short_filled_full_close_rewrites_the_retry_record_from_the_ledger(repo, caplog) -> None:  # noqa: F811
    """#887: `book_exit` books only what the venue actually sold (#446) -- a SELL meant to be a
    full close that the venue fills SHORT leaves a tranche open. The pre-sale
    `protecting_remainder` math said this leg would close the position outright (`remainder`
    computed as `ledger_held - qty <= 0`), so no re-bracket ran, and the crash-ledger record
    `_confirm_then_cancel` wrote before the cancel still names that PRE-SALE number -- wrong
    the moment the fill comes in short. Mirror `agent._handle_exits`'s own short-fill tail
    (`agent.py:950-974`): the retry record is rewritten from what `repo.get_open_positions`
    actually still holds, `open_stop`/`open_target` are left alone (the product is still
    held), and a CRITICAL is logged.
    """
    config = _config()
    broker = _ShortFillingBroker(Decimal("0.6"))
    _bracketed(repo, broker, config, qty="0.002", stop="95000", target="130000")
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    with caplog.at_level(logging.CRITICAL):
        result = executor.reduce(_red(qty="0.002"), broker=broker, repo=repo, config=config,
                                 holding=sleeve.holding_of(repo, "BTC-USD"), costs=COSTS,
                                 rule_id=7, rule_status="live", now_ts=NOW_TS,
                                 execution="confirm", confirm_fn=lambda preview: True)
    assert result.decision == "placed", "the SELL itself was accepted -- only the fill was short"
    (position,) = repo.get_open_positions("BTC-USD")
    assert position["qty"] == D("0.0008"), "0.002 ordered, 60% filled -- 0.0008 is still held"
    assert repo.get_state("unbracketed:BTC-USD") == {
        "stop": D("95000"), "target": D("130000"), "qty": D("0.0008"),
    }, "the retry record must be rewritten from the LEDGER, not left describing a zero remainder"
    assert repo.get_state("open_stop:BTC-USD") == D("95000"), "still held -- levels are not cleared"
    assert repo.get_state("open_target:BTC-USD") == D("130000")
    assert [
        r for r in caplog.records
        if r.getMessage() == "executor.reduce_left_an_unprotected_remainder"
    ], "a naked remainder was left behind SILENTLY"


def test_887_a_partial_sale_of_an_unbracketed_dca_tranche_logs_no_critical(repo, caplog) -> None:  # noqa: F811
    """#890: the short-fill `else` arm above used to be reached by `not protecting_remainder`
    alone, with no `has_levels` check -- true for EVERY partial sale of a product with no
    recorded `open_stop`/`open_target`, not only a short-filled one. A DCA tranche is never
    bracketed, so a perfectly ordinary, fully-filled confirmed partial sale of one has
    `has_levels` and `protecting_remainder` both `False`, `still_held` non-empty (0.001 of
    0.002 remains), and the old gate logged a false CRITICAL
    `executor.reduce_left_an_unprotected_remainder` for a position that was never bracketed to
    begin with -- the `unbracketed:BTC-USD` write was already gated separately on `stop`/
    `target` being set, so it never fired; nothing was left LESS protected than it already was.
    Seeds the same way `_bracketed` seeds a bracketed position (`_held`
    for the `positions` row, a matching filled BUY order for `_held_position` -- see that
    fixture's own docstring), but never calls `place_bracket`, so no `open_stop`/`open_target`
    are ever recorded and no bracket rests to cancel.
    """
    config = _config()
    broker = FakeBroker()
    holding = _held(repo, "0.002")
    repo.insert_order(
        dict(
            mode="live",
            product_id="BTC-USD",
            side=Side.BUY.value,
            order_type="market",
            qty=D("0.002"),
            limit_price=D("100000"),
            status="filled",
            fee=D("0.6"),
            created_at=NOW_TS - 200,
            updated_at=NOW_TS - 200,
        )
    )
    repo.set_sells_window(True, now_ts=0, expires_ts=NOW_TS + 3600)
    with caplog.at_level(logging.CRITICAL):
        result = executor.reduce(_red(qty="0.001"), broker=broker, repo=repo, config=config,
                                 holding=holding, costs=COSTS, rule_id=7, rule_status="live",
                                 now_ts=NOW_TS, execution="confirm",
                                 confirm_fn=lambda preview: True)
    assert result.decision == "placed"
    assert repo.get_state("unbracketed:BTC-USD") is None, (
        "an unbracketed DCA tranche has nothing to heal -- no record should ever appear for it"
    )
    assert [
        r for r in caplog.records
        if r.getMessage() == "executor.reduce_left_an_unprotected_remainder"
    ] == [], "an ordinary partial sale of an unbracketed DCA tranche must not log a false CRITICAL"
```

```python
# tests/execution/test_sell_side_invariants.py
RUN_ORDER_CALLERS = {*RUN_ORDER_CALLERS_BEFORE_P18, ("keel.execution.executor", "reduce")}


def test_reduces_only_run_order_call_sits_behind_sells_released() -> None:
    tree = ast.parse(open(os.path.join(_ROOT, "keel/execution/executor.py")).read())
    reduce_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "reduce")
    guarded = [
        node for node in ast.walk(reduce_fn) if isinstance(node, ast.If)
        and "sells_released" in ast.unparse(node.test)
        and any(isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", "")) == "_run_order"
                for c in ast.walk(node))
    ]
    calls = [c for c in ast.walk(reduce_fn) if isinstance(c, ast.Call)
             and getattr(c.func, "id", getattr(c.func, "attr", "")) == "_run_order"]
    assert len(calls) == 1 and guarded


def test_the_cycle_never_asks_reduce_to_confirm() -> None:
    tree = ast.parse(open(os.path.join(_ROOT, "keel/agent.py")).read())
    for call in (c for c in ast.walk(tree) if isinstance(c, ast.Call)
                 and getattr(c.func, "attr", "") == "reduce"):
        assert all(k.arg != "execution" for k in call.keywords)
```

  Keep the pre-P18 set under a new name, `RUN_ORDER_CALLERS_BEFORE_P18`. The P1 test keeps using `RUN_ORDER_CALLERS`.

- [ ] **Step 2: Run the tests to see them fail.** All of Step 1's tests fail with `AttributeError`/`TypeError` first (`executor.reduce` has no `execution=`/`confirm_fn=` branch yet -- P7 built preview only). Once the confirm branch exists but BEFORE it is built the way Step 3 describes, confirm each of the four `test_883_*` failure modes individually, in this order, so the fix that follows is proven necessary rather than assumed:
  1. Against the shape #883 originally flagged -- `_clear_resting_bracket` called unconditionally before `_run_order`, ahead of the confirm gate, and with NO remainder re-bracket logic at all yet (the very first draft, before any of Task 18.1's fixes exist): `test_883_a_declined_confirm_leaves_the_resting_bracket_untouched` fails as expected, on `broker.cancel_calls == []` (non-empty -- the cancel already ran before `confirm_fn` was ever consulted). **The other two do NOT fail the way an earlier draft of this plan claimed.** Both `test_883_a_partial_confirmed_leg_re_brackets_the_remainder_like_scale_out` and `test_883_a_full_close_does_not_attempt_to_re_bracket` answer `confirm_fn` with `True` unconditionally, so under this shape the cancel happens either way -- ordering makes no observable difference to a call that was always going to happen. The full-close test in fact PASSES outright: it makes no remainder-re-bracket assertion, and its two-call count (bracket + SELL leg) holds with or without the ordering fix. The partial test instead fails on `len(broker.place_calls) == 3` (actual: 2) -- there is no remainder re-bracket logic in this shape at all yet, not because of the cancel's timing. Neither of these two is a `cancel_calls` failure, which is why `_confirm_recording_cancel_state` (Step 1) exists: it is the ONE assertion, in the partial test (`test_883_a_partial_confirmed_leg_re_brackets_the_remainder_like_scale_out`, "B"), that is actually sensitive to the ordering bug on its own terms, independent of what remainder logic happens to be built at the time -- `seen["cancel_calls_at_confirm_time"] == []` fails under this shape for B, where the incidental call-count/cancel_calls-truthiness assertions do not. The full-close test ("C", `test_883_a_full_close_does_not_attempt_to_re_bracket`) never calls `_confirm_recording_cancel_state` at all -- its `confirm_fn` is a bare `lambda preview: True` with no `seen` dict to assert on -- so it is not one of the tests this assertion covers.
  2. Fix the ordering (wrap `confirm_fn` as Step 3 describes) but keep the crash-ledger write BEFORE the wrapped closure, unconditionally, as an earlier draft did: `test_883_a_declined_confirm_leaves_the_resting_bracket_untouched` now fails on its last assertion alone (`repo.get_state("unbracketed:BTC-USD") is None`) -- the write already ran before `confirm_fn` was ever called, so a decline never has anything to clear it.
  3. With that also fixed (Step 3's actual code, write inside `_confirm_then_cancel` after the yes), the two placement tests still fail on their `place_calls` counts unless they account for the SELL leg's own `_run_order` placement, not just the bracket calls -- confirm this by counting `broker.place_calls` before asserting the fixed numbers (2 and 3, not 1 and 2).
  4. With the remainder re-bracket built but no tranche repoint, `test_883_a_partial_confirmed_leg_repoints_the_surviving_tranche_at_the_new_bracket` fails: `position["bracket_order_id"]` still names the CANCELLED bracket's id, not the new one. With the crash-ledger write still gated on `protecting_remainder`, `test_883_a_venue_rejected_full_close_after_the_cancel_leaves_a_retry_record_and_is_not_mislabelled_a_decline` fails on `repo.get_state("unbracketed:BTC-USD") is not None` (nothing was ever written for a full close) and, separately, on `result.decision == "failed"` (it reads `"declined"`, indistinguishable from an operator's own no). And, once the write is switched to `has_levels` but BEFORE the full-close success path is taught to clear it explicitly, `test_883_a_full_close_does_not_attempt_to_re_bracket`'s new last assertion (`repo.get_state("unbracketed:BTC-USD") is None`) itself goes red -- widening the write condition without also widening what clears it trades one gap (no record on a rejected full close) for another (a stale record after a successful one).
  5. (#887) With `test_883_a_full_close_does_not_attempt_to_re_bracket`'s own narrow clear (`if not protecting_remainder and has_levels: repo.set_state(..., None)`) in place but BEFORE it is replaced by Step 3's `still_held`-driven branch, `test_887_a_full_close_clears_position_rule_and_open_levels_too` fails on `repo.get_state("position_rule:BTC-USD") is None` -- the prior code never touched `position_rule:`/`open_stop:`/`open_target:` at all, only `unbracketed:`. And with that branch replaced but the short-fill `else` arm not yet added, `test_887_a_short_filled_full_close_rewrites_the_retry_record_from_the_ledger` fails on `repo.get_state("unbracketed:BTC-USD") == {...}` -- `protecting_remainder` was `False` (a full close was intended), so the prior code's `if protecting_remainder:` re-bracket never ran, and the `still_held`-is-empty branch does not apply either (`still_held` is non-empty after a short fill), so nothing rewrites the stale pre-sale record without the new `else` arm.
  6. (#890) With that short-fill arm added but still gated on `not protecting_remainder` alone -- the shape #887 itself landed, with no `has_levels` check -- `test_887_a_partial_sale_of_an_unbracketed_dca_tranche_logs_no_critical` fails: its DCA tranche has no `open_stop`/`open_target`, so `has_levels` and `protecting_remainder` are both `False`, `still_held` is non-empty (0.001 of 0.002 remains after an ordinary, fully-filled confirmed partial sale), and the un-split `else` arm reaches the CRITICAL log anyway, on `caplog.records == []` alone. The `unbracketed:BTC-USD` write in that same shape was already its own `if stop is not None and target is not None:` clause, which stays False for an unbracketed DCA tranche either way, so `repo.get_state("unbracketed:BTC-USD") is None` was never the failing assertion. Splitting that `else` into an `elif has_levels and remainder <= 0:` arm (the short-fill case) and a final `else` that writes nothing (this test's case) is what fixes it.

- [ ] **Step 3: Implement the confirm branch.** After a clean `guards.check`:
  - If `execution == "confirm"` and not `sleeve.sells_released(repo, now_ts)`, record `declined` and return.
  - Else refuse unless `rule_status == "live"` (R16).
  - **#883: the typed-yes gate must be asked BEFORE the bracket is cancelled, not after.** The
    original draft called `_clear_resting_bracket` here, unconditionally, before ever calling
    `_run_order` -- so a decline (or no TTY) left the bracket already cancelled, with no
    `unbracketed:` record, because nothing about a decline resembles the crash `place_bracket`'s
    own failure paths record for. Do not call `_clear_resting_bracket` directly. Instead, wrap
    the caller's `confirm_fn` so the cancel happens ONLY once the operator has said yes, inside
    the same gate `_run_order` already calls at the right moment (after its own fresh preview,
    before `insert_order`):

    ```python
    # NOT `held` -- that name is already this function's venue-observed available base from
    # `_clamped_sell_qty` above. `ledger_held` is the LEDGER's total (`_held_position`, the same
    # source `scale_out` uses), and `qty` here is the CLAMPED amount this leg will actually sell.
    ledger_held, _avg_cost = _held_position(repo, reduction.product_id)
    remainder = ledger_held - qty
    stop = repo.get_state(f"open_stop:{reduction.product_id}")
    target = repo.get_state(f"open_target:{reduction.product_id}")
    protecting_remainder = remainder > 0 and stop is not None and target is not None
    has_levels = stop is not None and target is not None

    cancel_failed = False
    operator_confirmed = False

    def _confirm_then_cancel(preview: Preview) -> bool:
        nonlocal cancel_failed, operator_confirmed
        if confirm_fn is None or not confirm_fn(preview):
            return False
        operator_confirmed = True
        # The crash ledger, same key and same reason as `scale_out`/`_roll_stop` (#519's
        # pattern) -- but written HERE, only once the operator has said yes, and BEFORE the
        # cancel that is about to run: if the process dies between this cancel and the
        # re-bracket below, the next cycle's sweep must still be able to heal the position. A
        # DECLINE returns out of this closure on the line above and never reaches this write --
        # #883 (round 2): an earlier draft wrote this record unconditionally, before the
        # confirm gate ran at all, so a decline left a stale record behind with nothing that
        # ever cleared it. It is written here, not earlier, precisely so a decline writes
        # nothing to clear.
        #
        # #883 (round 3): this used to be gated `if protecting_remainder:` -- true only for a
        # PARTIAL leg. A FULL close has `remainder == 0`, so `protecting_remainder` is always
        # False for one, and the old gate wrote NOTHING here for a full close -- a SELL the
        # venue then rejected, after this cancel had already run, left the position naked with
        # no retry record at all for the sweep to read. Gate on `has_levels` instead (there are
        # `open_stop`/`open_target` to heal from), not on how much would be left if the SELL
        # succeeds: `reconcile_unbracketed_positions` sizes the healing bracket from the
        # `positions` ledger's own `qty` (`scale_out`'s own docstring makes this point, and it
        # is why this record's `qty` field being sized for a SUCCESSFUL leg -- "the remainder"
        # -- is harmless even when the leg then fails; the field is read for PRESENCE and for
        # levels, never for sizing). The success path below re-checks what the LEDGER actually
        # still holds after `book_exit` (#887) -- when nothing is left it clears this record
        # explicitly, since `place_bracket` -- its only other clearer -- is never called when
        # there is no remainder to re-bracket; when something unexpected IS still held (the
        # venue short-filled what was meant to be a full close), it rewrites this record from
        # the ledger instead of leaving it describing the pre-sale number.
        #
        # A stale record IS still possible after this point -- if the cancel below fails, or a
        # crash lands between here and the re-bracket -- but that is harmless for the reason
        # `reconcile.reconcile_unbracketed_positions` actually skips it: `_has_resting_bracket`
        # (`reconcile.py:471`) is checked FIRST, before the retry record is even read
        # (`reconcile.py:~261`), and a bracket that is still resting -- which is exactly what a
        # failed cancel or an undone decline leaves -- makes that check true, so the sweep
        # `continue`s before it ever looks at `unbracketed:<product>`. (NOT the sweep's own
        # `if not intent: continue` a few lines below it, which only runs once
        # `_has_resting_bracket` has already said no bracket is resting.)
        if has_levels:
            repo.set_state(f"{UNBRACKETED_PREFIX}{reduction.product_id}",
                           {"stop": stop, "target": target, "qty": remainder})
        if not _clear_resting_bracket(broker, repo, reduction.product_id, now_ts):
            cancel_failed = True
            return False
        return True

    # The `_run_order` call stays TEXTUALLY inside `if sleeve.sells_released(...):`, matching
    # the S1 AST pin (`test_reduces_only_run_order_call_sits_behind_sells_released`) exactly as
    # the original draft did -- the early `not sleeve.sells_released(...)` return above already
    # makes this redundant at runtime, and that redundancy is the point: the mechanical scan
    # checks the SOURCE SHAPE, not the logical flow, so the call must visibly sit behind the
    # gate even though it is unreachable any other way.
    if sleeve.sells_released(repo, now_ts):
        # NOT `spec=spec` -- there is no `spec` in this function's scope (`reduce`'s preview
        # branch, Task 7.2, calls `broker.preview_order(_order_spec(intent))` directly and never
        # builds one it keeps around; `_run_order` is not a preview-branch caller). Passing an
        # undefined name is simply a `NameError`. `spec` defaults to `None` in `_run_order`'s own
        # signature, which then builds it internally with `_order_spec(intent)` -- exactly the
        # call `scale_out` relies on with its own unspec'd `_run_order(intent, broker, repo,
        # config, "autonomous", None, now_ts)` a few hundred lines up. Omit the keyword entirely.
        result = _run_order(intent, broker, repo, config, "confirm", _confirm_then_cancel,
                            now_ts)
    else:
        # Unreachable -- the early `not sleeve.sells_released(...)` return above already
        # guarantees this branch is never taken. Kept only so `_run_order`'s call sits behind
        # the gate for the AST pin, and so `result` (an `ExecutionResult`, like `_run_order`'s
        # own return) is never possibly-unbound under mypy.
        result = ExecutionResult(placed=False, order_id=None, vetoed_by=[], preview=None,
                                 reason="sells window closed between the two checks")
    if not result.placed:
        # #883 (round 3): three different shapes reach here, and only ONE of them is actually a
        # decline. (1) The operator said no, and `_confirm_then_cancel` returned False before
        # ever touching the exchange -- `operator_confirmed` stays False. (2) The operator said
        # yes, but the cancel itself failed (`cancel_failed`). (3) The operator said yes, the
        # cancel succeeded, and the EXCHANGE then rejected the SELL -- an ordinary broker
        # refusal, `PlaceResult(success=False, ...)` (insufficient funds, a size out of band),
        # the one failure `_run_order` can still return AFTER `confirm_fn` has run. NOT a guard
        # veto or the spread gate: both of those run inside `_run_order` BEFORE `confirm_fn` is
        # ever called (`guards.check` at the top of the function; `_entry_spread_gate` right
        # before the `mode == "confirm"` branch), and the spread gate is BUY-only besides (its
        # own docstring: "Refuse a live BUY..."), so neither can be the reason a SELL fails
        # AFTER a genuine yes. `operator_confirmed` is True and `cancel_failed`
        # is False. Case 3 used to fall through to the same "declined at the confirm gate" label
        # as case 1, which misattributes an exchange refusal to the operator's own answer. Cases
        # 2 and 3 share a `decision` of `"failed"` -- neither is a "no"; both are an attempt that
        # did not complete -- and are told apart only by `reason`'s text, which names WHICH step
        # failed.
        if cancel_failed:
            decision, reason = "failed", (
                f"could not cancel the resting exit bracket for {reduction.product_id}"
            )
        elif not operator_confirmed:
            decision, reason = "declined", "declined at the confirm gate"
        else:
            # `has_levels` is False for a DCA tranche that never had a bracket: nothing was
            # cancelled, and the reason must not claim otherwise. Held question (#887
            # follow-up): even when `has_levels` is True, `_clear_resting_bracket` returns
            # `True` whether it actually cancelled a resting order OR found none to cancel
            # (`executor.py:301-361` -- the loop over `RESTING_STATUSES` simply does not
            # iterate when nothing is resting, and the function still returns `True` at its
            # tail). `has_levels` can be stale True with nothing currently resting: a prior
            # re-bracket attempt on this same product can have cancelled the old bracket and
            # then had ITS OWN `place_bracket` call rejected -- that failure path
            # (`executor.py:2356-2374`) records `unbracketed:<product>` but never clears
            # `open_stop`/`open_target`, so a later `reduce` sees `has_levels` True with no
            # bracket actually resting. Recomputing an accurate "did this call's own cancel
            # touch a live order" flag would mean re-querying `RESTING_STATUSES` by side here,
            # duplicating `_clear_resting_bracket`'s own filter rather than cheaply reusing it
            # -- not worth it for a clause in a message. Word it so it stays true either way:
            # "any resting bracket", not "the resting bracket", so a reader does not take it as
            # proof one was actually there to cancel.
            after = (" after any resting bracket was cancelled" if has_levels else "")
            decision, reason = "failed", (
                f"the venue rejected the sell for {reduction.product_id}{after}: {result.reason}"
            )
        pid = sleeve.record_proposal(repo, reduction=leg, rule_id=rule_id,
                                     rule_status=rule_status, holding=holding, costs=costs,
                                     decision=decision, rails=rails, expected_fee=fallback_fee,
                                     fee_source=costs.fee_source, legs=legs, now_ts=now_ts)
        return ReduceResult(reduction.product_id, reduction.reason, pid, decision, [], legs,
                            reason)
    ```

  - On `placed` (`order_id = result.order_id`, bound here once so every read below -- the
    proposal update, the `confirmation` stamp, `book_exit`, and the CRITICAL log inside the
    LEDGER tail below -- can just say `order_id` rather than reaching back into `result`):
    - update the proposal: `decision="placed"`, `order_id`;
    - stamp `orders.confirmation = "confirm_sells"` with `repo.update_order(order_id, confirmation="confirm_sells")`, so a reader can tell which gate released it (spec §3.8);
    - when the order is `filled`, bind `exit_order = repo.get_order(order_id)` once and call `streak.book_exit(repo, config, product_id=..., exit_order=exit_order, sold_qty=streak.observed_sold_qty(exit_order) or intent.qty, is_dca=None, now_ts=now_ts)`.
    - **Re-bracket the remainder, exactly as `scale_out` does (#883), but sized from what the
      LEDGER actually still holds after `book_exit`, not from the pre-sale `remainder` above
      (held question, #887 follow-up).** `if protecting_remainder:` re-read the ledger now that
      `book_exit` has posted -- `still_held_qty = sum((p["qty"] for p in
      repo.get_open_positions(reduction.product_id)), Decimal("0"))` -- then call
      `bracket_order_id = place_bracket(broker, repo, config, product_id=reduction.product_id,
      qty=still_held_qty, stop=stop, target=target, rule_name=reduction.reason, now_ts=now_ts,
      rule_id=rule_id)`
      AFTER `book_exit`, for the same reason `scale_out` books before it re-places: the sweep
      that would otherwise heal from the crash-ledger record sizes a healing bracket off the
      `positions` ledger, which `book_exit` is what shrinks. **This deliberately differs from
      `scale_out`'s own re-bracket call, which sizes `qty=remainder` -- the PRE-SALE number
      (`executor.py:2589`).** That is not because `scale_out` is safe from a short fill:
      `_book_scale_out` books `exit_order.get("filled_quantity") or exit_order["qty"]`
      (`executor.py:2661`), and its own docstring says a scale-out the venue fills short must
      reduce the tranche by what actually sold. `scale_out`'s pre-sale `qty=remainder` bracket
      has that same gap on main today -- hold 0.002, scale out 0.001, the venue fills 0.0006,
      the ledger holds 0.0014, the bracket covers only 0.001, and `place_bracket` only clamps
      down, so 0.0004 goes naked and unrecorded. That gap is pre-existing, out of scope for
      this plan, and tracked in #893. `reduce`'s confirm branch sizes from the ledger instead
      because a short fill can leave MORE held than the pre-sale `remainder` predicted: it
      books through `streak.book_exit` with `sold_qty=streak.observed_sold_qty(exit_order) or
      intent.qty` (the bullet above), so bracketing at the stale, smaller pre-sale `remainder`
      would leave the unsold excess of a short-filled partial leg naked with no record --
      reading the ledger's own post-`book_exit`
      qty here closes that gap the same way the LEDGER tail below closes it for a short-filled
      FULL close. `place_bracket` clears `unbracketed:<product>` on success and re-writes it
      (unchanged) on failure or veto, logging CRITICAL either way (its own existing contract;
      nothing new needed here).
      **Repoint the surviving tranche at the new bracket (#883 round 3).** When
      `bracket_order_id is not None`, mirror `scale_out`'s own choreography exactly
      (`executor.py:2614`): `for position in repo.get_open_positions(reduction.product_id):
      repo.set_position_bracket(position["id"], bracket_order_id)`. Without this the tranche
      keeps naming the CANCELLED bracket -- `get_position_for_bracket` finds nothing when the
      new one eventually fills, `reconcile.exit_without_position_context` fires (no
      `trade_outcomes` row, the tranche never closes), and `position.unprotected` WARNs on it
      every cycle even though a bracket really is resting. When `not protecting_remainder` (a
      full close, or a DCA tranche that was never bracketed), skip the re-bracket AND the
      repoint entirely -- there is nothing to re-place, and `place_bracket` for a zero or
      unprotected remainder has no meaning. `test_883_a_partial_confirmed_leg_re_brackets_the_remainder_like_scale_out`
      still holds under this change: its leg is an ordinary full fill (`FakeBroker`, not the
      short-filling one), so `still_held_qty`, read after `book_exit`, equals the pre-sale
      `remainder` (0.001) exactly -- the test asserts `open_stop`/`open_target` and resting
      counts, nothing that would distinguish the two sources of the bracket's size.
    - **#887: what happens next must come from the LEDGER, not from the pre-sale
      `protecting_remainder`/`remainder` arithmetic above -- mirror `agent._handle_exits`'s own
      tail exactly (`agent.py:950-987`), the same bookkeeping its own EXIT path already does
      after its `book_exit`/`_close_tranches` call.** The prior draft's `if not
      protecting_remainder and has_levels: repo.set_state(..., None)` assumed the PRE-SALE
      remainder math (`ledger_held - qty <= 0`) is what actually happened at the venue. It is
      not: `qty` is what was ORDERED, and `book_exit` books what `streak.observed_sold_qty`
      says was actually SOLD (#446), which can be less. So after `book_exit` (and after the
      re-bracket attempt above, when `protecting_remainder` sent it down that path), re-read
      the ledger's own verdict: `still_held = repo.get_open_positions(reduction.product_id)`.
      - **If `still_held` is empty** -- the position really is fully out now, whether because a
        genuine full close's SELL filled completely or because a partial leg's re-bracket above
        happened to consume the last of it -- clear ALL FOUR keys together, not only
        `unbracketed:`: `repo.set_state(f"position_rule:{reduction.product_id}", None)`,
        `repo.set_state(f"open_stop:{reduction.product_id}", None)`,
        `repo.set_state(f"open_target:{reduction.product_id}", None)`, and
        `repo.set_state(f"{UNBRACKETED_PREFIX}{reduction.product_id}", None)`. Left set, a
        stale `open_stop` makes rail 9 veto the very next entry on this product with
        `no_stop_widening` (`guards.py:657-664`, `proposed_stop < prior_stop`), and would make
        `has_levels` true on a later `reduce` of a product that no longer holds anything.
        `agent.py:981-987` clears the same four keys for the same reason on its own EXIT path;
        this is that same choreography, not a new policy.
      - **Elif `protecting_remainder`** -- the re-bracket branch above already owns this case in
        full: `place_bracket` re-wrote or cleared `unbracketed:<product>` on its own existing
        contract, and the tranche was repointed at the new bracket. Nothing further runs here.
      - **Elif `has_levels and remainder <= 0`** -- `protecting_remainder` was `False` while
        `has_levels` is `True`; with `still_held` already known non-empty (the first arm above
        already took the empty case), the only way both of those hold at once is
        `remainder <= 0` -- this leg was meant to be a bracketed FULL close, and the venue
        instead filled it SHORT (#446). `book_exit` reduced the tranche rather than closing it,
        but the crash-ledger record `_confirm_then_cancel` wrote before the cancel still
        describes the PRE-SALE number (a zero remainder) -- stale, and the retry record for a
        naked remainder must never simply disappear. Rewrite it from what the ledger actually
        still holds, and log CRITICAL, exactly as `agent._handle_exits` does for its own
        short-filled exit (`agent.py:950-974`), simplified because this arm's own guard already
        proves levels exist -- no `if stop is not None and target is not None:` needed, and
        `stop`/`target` are the same values read at the top of this function (`open_stop:`/
        `open_target:` are untouched between there and here on this arm: `place_bracket` is
        never called when `protecting_remainder` is `False`, so nothing could have changed
        them):

        ```python
        naked_qty = sum((p["qty"] for p in still_held), Decimal("0"))
        repo.set_state(f"{UNBRACKETED_PREFIX}{reduction.product_id}",
                       {"stop": stop, "target": target, "qty": naked_qty})
        log_event(logger, logging.CRITICAL, "executor.reduce_left_an_unprotected_remainder",
                  product=reduction.product_id, order_id=order_id, remainder=str(naked_qty),
                  healable=True,
                  detail="the venue filled this sleeve sell SHORT: part of the position is "
                         "still held, its protective bracket was cancelled to place the sale, "
                         "and nothing is resting at the exchange for it right now. The "
                         "position state is retained so the next cycle can re-place from it")
        ```
      - **Else** -- `has_levels` is `False`. This is a DCA tranche, or any other holding that
        never had a bracket, still (partly or wholly) held after the sale -- whether because
        this leg was always an ordinary partial sale of it, filled in full as ordered, or
        because the venue happened to short-fill it too. Either way nothing here was ever
        protected, so the sale leaves nothing LESS protected than it already was: **no
        CRITICAL, no crash-ledger write.** (#890: the defect this arm exists to fix. The prior
        shape reached this branch on `not protecting_remainder` alone, with no `has_levels`
        check -- true for EVERY partial sale of an unbracketed product, not only a short-filled
        one, so an ordinary confirmed partial sale of a plain DCA tranche logged a false
        CRITICAL `executor.reduce_left_an_unprotected_remainder` for a position that was never
        bracketed to begin with -- the `unbracketed:` write itself was already gated
        separately on `stop`/`target` being set, so it never actually wrote a false record;
        only the CRITICAL log was spurious. Splitting the prior
        single `else` into the `has_levels` arm above and this one is the fix --
        `test_887_a_partial_sale_of_an_unbracketed_dca_tranche_logs_no_critical`, Task 18.1 Step
        1, pins it.)

        `position_rule:` is left untouched on either of these last two arms -- the product is
        still held, so whatever owns it keeps owning it; only the fully-out branch above ever
        retires it.
- [ ] **Step 4: Run the tests, the invariants and the suite, then commit.**

### Task 18.2: `keel dca {distribute,trim,exit} --confirm <proposal-id>`, one gate function and one capability row

**Files:**
- Modify: `keel/commands/dca.py` (`sleeve_sale_gate(proposal: dict, preview: Preview) -> bool`, which catches the `ClickException` that `_require_interactive_confirmation` raises on refusal and returns `False`; plus a shared `_confirm_sale(ctx, proposal_id, expected_kind)`), `keel/capabilities.py`
- Test: `tests/commands/test_dca_confirm_cli.py` (new)

- [ ] **Step 1: Write the failing tests.** `_build_broker` is patched to return `tests.execution.test_executor.FakeBroker()`, recorded as `broker`. `_seed_firing(deployment, status)` inserts a `reverse_dca` rule at `status` with `min_price_floor` 1 and `cadence_days` 1. It also opens a DCA tranche 60 days old, writes 30 daily candles, and inserts one `preview` proposal for that rule, returning its id.

```python
def _confirm(deployment, verb, pid, input=None):
    db, config = deployment
    return CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", verb,
                                    "--confirm", str(pid)], input=input)


def test_off_a_tty_confirm_places_nothing(deployment, monkeypatch, broker) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    pid = _seed_firing(deployment, "live")
    result = _confirm(deployment, "distribute", pid)
    assert result.exit_code == 1, result.output  # R35: no TTY refuses, same as any other refusal
    assert broker.place_calls == []


def test_without_the_sells_window_confirm_says_how_to_arm_it(deployment, monkeypatch, broker):
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    _repo(deployment[0]).set_autonomous(True, now_ts=0)  # global autonomy is NOT enough (S1)
    pid = _seed_firing(deployment, "live")
    result = _confirm(deployment, "distribute", pid, input="yes\n")
    assert result.exit_code == 1, result.output  # R35: a closed sells window refuses too
    assert "keel autonomy on --sells --for-hours 1" in result.output
    assert broker.place_calls == []


def test_window_and_typed_yes_place_one_leg_and_link_the_proposal(deployment, monkeypatch, broker):
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    repo = _repo(deployment[0])
    repo.set_sells_window(True, now_ts=0, expires_ts=int(time.time()) + 3_600)
    pid = _seed_firing(deployment, "live")
    result = _confirm(deployment, "distribute", pid, input="yes\n")
    assert result.exit_code == 0, result.output
    assert len(broker.place_calls) == 1
    assert _repo(deployment[0]).get_sell_proposals()[0]["trigger"]["confirms"] == pid


@pytest.mark.parametrize("status,verb,needle", [
    ("paper", "distribute", "paper"),
    ("live", "trim", "reverse_dca"),
])
def test_paper_rules_and_kind_mismatches_are_refused(deployment, monkeypatch, broker,
                                                     status, verb, needle):
    """R35/#890: a refusal `_confirm_sale` raises before `reduce` is ever called is still exit
    1, the same as a `declined`/`failed` `ReduceResult` -- `click.ClickException` all the way
    down, so `exit_code` alone never tells a caller which kind of refusal this was."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    _repo(deployment[0]).set_sells_window(True, now_ts=0, expires_ts=int(time.time()) + 3_600)
    pid = _seed_firing(deployment, status)
    result = _confirm(deployment, verb, pid, input="yes\n")
    assert result.exit_code == 1 and needle in result.output and broker.place_calls == []


def test_a_proposal_that_no_longer_fires_places_nothing(deployment, monkeypatch, broker):
    """R35/#890: this refusal never reaches `reduce` at all -- `_confirm_sale` catches it first
    -- but it still exits 1 via `click.ClickException`, exactly as a `ReduceResult.decision` of
    `declined` or `failed` would."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    repo = _repo(deployment[0])
    repo.set_sells_window(True, now_ts=0, expires_ts=int(time.time()) + 3_600)
    pid = _seed_firing(deployment, "live")
    repo._conn.execute("UPDATE rules SET params = json_set(params, '$.min_price_floor', '1e12')")
    repo._conn.commit()
    result = _confirm(deployment, "distribute", pid, input="yes\n")
    assert result.exit_code == 1
    assert f"proposal #{pid} no longer fires on the current book" in result.output
    assert broker.place_calls == []


def test_a_declined_typed_yes_exits_1_and_says_declined(deployment, monkeypatch, broker):
    """R35: a `declined` result exits 1, exactly as `_require_interactive_confirmation`'s own
    refusal does -- never 0, which would read as success to anything checking the status."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    _repo(deployment[0]).set_sells_window(True, now_ts=0, expires_ts=int(time.time()) + 3_600)
    pid = _seed_firing(deployment, "live")
    result = _confirm(deployment, "distribute", pid, input="no\n")
    assert result.exit_code == 1, result.output
    assert "declined: " in result.output and broker.place_calls == []


def test_a_venue_rejected_sale_exits_1_and_says_failed_not_declined(deployment, monkeypatch,
                                                                   broker):
    """R35: a `failed` result -- the operator said yes and the venue refused -- also exits 1,
    and the message names it `failed`, never `declined`: the status does not tell them apart,
    so the words must."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    broker._place_success = False  # noqa: SLF001 -- `FakeBroker`'s own refusal switch
    _repo(deployment[0]).set_sells_window(True, now_ts=0, expires_ts=int(time.time()) + 3_600)
    pid = _seed_firing(deployment, "live")
    result = _confirm(deployment, "distribute", pid, input="yes\n")
    assert result.exit_code == 1, result.output
    assert "failed: " in result.output and "declined" not in result.output
    assert len(broker.place_calls) == 1, "the SELL really was sent; the venue refused it"
```
- [ ] **Step 2: Run the tests to see them fail.**
- [ ] **Step 3: Implement.**
  - `_confirm_sale` re-evaluates the proposal against the current book and rails (spec §3.5): it loads the rule by `proposal.rule_id`, rebuilds the `Holding` and the candles, and calls `reduce_signal`.
  - It then calls `executor.reduce(..., execution="confirm", confirm_fn=lambda p: sleeve_sale_gate(proposal, p))` with a live broker from `_build_broker`.
  - It renders `render_proposal` and the venue preview before the typed gate.
  - **Exit codes (R35, extended #890).** `placed` returns normally (exit 0). Any other `ReduceResult.decision` -- `declined` or `failed` -- raises `click.ClickException(f"{result.decision}: {result.reason}")`, so both exit 1 and the message leads with the decision word. The same applies to every refusal `_confirm_sale` raises BEFORE it ever calls `reduce` -- the proposal no longer firing on the current book, a rule/verb kind mismatch, a `paper` rule refused up front -- each via its own `click.ClickException`, so `--confirm` exits 1 on every non-`placed` outcome regardless of whether `reduce` was reached. Do not mint a new exit code for `failed`, or for any pre-`reduce` refusal: the reason text already names the step that failed (Task 18.1's `reason` strings, or `_confirm_sale`'s own message), and the retry record and CRITICAL log carry the state.
  - The capability row:

```python
    Capability(
        module="keel.commands.dca",
        function="sleeve_sale_gate",
        surface="cli",
        invocation="keel dca distribute|trim|exit --confirm <proposal-id>",
        increases=(
            "ONE sleeve SELL leg is placed at the venue for a proposal that still fires -- only "
            "inside an armed `autonomy on --sells` window, and only for a `live` rule"
        ),
    ),
```

- [ ] **Step 4: Run the full suite, including `tests/web/test_server.py` and `tests/test_capabilities.py`, then commit.**

```bash
uv run pytest -q && uv run ruff check keel tests && uv run ruff format --check keel tests && uv run mypy
git add keel/ tests/
git commit -m "feat(dca): --confirm <proposal-id> places one sleeve sale behind the sells window and a typed yes (#857)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# What the spec and the issues say that the code contradicts today

Checked against `origin/main` at `a7fe3fa` (0.20.0), 2026-09-28.

| # | Source | Claim | Code today | Where this plan handles it |
|---|---|---|---|---|
| C1 | #799 body | "that adapter never got the guard ... the August incident would recur today" | Fixed by #800. `packages/keel-broker-coinbase/keel_broker_coinbase/adapter.py:341-343` has `or "0"` on all three fields. | R1 (proposal 1 is done) |
| C2 | #799 body | "positions: (no PAXG row)" | The row was written by hand afterwards. #811 prints `positions.id=3 PAXG-USD turtle_breakout`. | R1 (proposal 4 is done) |
| C3 | #799 body | the bracket preview's exception "aborted the flow" | Half-fixed. `place_bracket` now builds the spec inside `try` (`executor.py:2317-2345`), but `_run_order` still re-raises a preview or placement exception (`executor.py:1148-1152`, `1238-1246`). So the #799 stranding still reproduces whenever `preview_order` itself raises. | P1 |
| C4 | #798 body | `_open_exposure_by_asset` at `guards.py:339`; `close_position` at `repository.py:1397` | Now at `guards.py:362` and `repository.py:1406`. The behaviour is as described. | P3, P4 |
| C5 | #798 body | "Every BTC entry is vetoed" at $101.81 vs $100 | Since #841/#842 and #853/#869, DCA is exempt from rails 4 and 6. The veto described no longer applies to DCA buys. The phantom-exposure mechanism still applies to rule entries. | P3, P4 (the mechanism, not the example) |
| C6 | #798, #799, #811 | `keel positions` as a CLI command | No such command is registered in `keel/cli.py`. The positions report is web-only. | R30 |
| C7 | Spec §2.3 | DCA is "bound by rails ... 6 (until #853 lands)" | #853 has landed. `guards.py`'s docstring and `dca.py` list rail 6 as a DCA exemption. | None needed. The spec's rail list is stale. |
| C8 | Spec §3.2, the task | `keel/streak.py` | The file is `keel/execution/streak.py`. | Global Constraints |
| C9 | Spec §3.2 | `reduce_signal(self, holding, candles_by_tf)` | A rule cannot read config or fees, and §6's sizing needs them. | R6 |
| C10 | Spec §3.3 | `sleeve.venue_drift` compares to the venue's `Balance.total` | `doctor.gather_findings` has no broker, and no base-currency balance is persisted. `cycle_balances` holds quote currencies only. | R2, R3 |
| C11 | Spec §3.4 | `min_hold_days` "after the newest tranche" | Under the live weekly BTC DCA this blocks every non-exit proposal forever. | R12 |
| C12 | Spec §3.7 | paper→live after 60 paper days with a reviewed proposal | A live-mode cycle evaluates only `live` rules (`agent.py:1700-1701`), so a `paper` sell rule in the live database never proposes. | R16 |
| C13 | Spec §3.7 vs §2.2 and Q5 | the replay at "1.2% and 0.9%" vs "0.9% is never hardcoded" | There is no measured-rate constant in `keel/`. | R29 |
| C14 | Spec §7, Q7 | the monitor covers PAXG tranche 3 | PAXG is never polled: `agent.feed_polled products=['BTC-USD']`, because polling follows live-rule products (`agent.py:1702`). | R27 |
| C15 | Spec §3.8 | audit events `sleeve.proposal`, `sleeve.placed`, ... | `keel/data/audit.py`'s `EVENT_STORES` is a closed `<store>_<verb>` vocabulary keyed to a store. | P6 Task 6.2, P17 Task 17.1 |
| C16 | Spec §3.1 | "added with `keel rules add`" | True, but `rules seed` and the first-run wizard build **every** registry kind from `{"product_id": p}` (`rules.py:1206`, `setup.py:1073`). That raises for a kind with required params, and it would seed a seller. | R20 |
| C17 | Spec §3.5 | `--confirm <id>` places with a typed `yes` alone | The operator's invariant requires `autonomy on --sells` for **any** new sell reaching the venue. | R22 |
| C18 | The task's S3 line | "Rails 2, 12, 13 and 17–22 apply to sells as the spec says" | Rails 13, 17, 20 and 22 are BUY-only in `guards.py` (`if not offline and is_buy` at lines 701, 846, 985 and 1093), and spec §3.4 lists them as sell-exempt. Rails 18, 19 and 21 do apply to a SELL, and so do 1, 2, 9, 10 and 12. | S3 wording; P7 Task 7.2 pins both halves |
| C19 | Spec header | "DRAFT ... No code until §10 accepted" | The operator merged it (#859) without answering §12. | The §12 defaults stand |

---

# Open questions (each with the default this plan builds)

| # | Question | Default built |
|---|---|---|
| OQ1 | Build P17 and P18 (the gated placement path) now, or stop after P16? | Build them last. Each merges only when the operator says so (R28). Nothing earlier depends on them. |
| OQ2 | Should `reverse_dca` consume DCA tranches only, and skip a rule tranche such as PAXG 3? (This is spec Q10's open question for PR 3.) | No. FIFO across all tranches, booked per leg by `book_exit(is_dca=None)` (spec default). |
| OQ3 | `min_hold_days` reads the consumed tranches, not the newest one (R12). Is that the intent? | Yes, the consumed tranches. The spec's stated purpose is about the tranche the sale realises. |
| OQ4 | A vetoed proposal (for example on a stale feed) counts as the day's one proposal (R14). Should a later run that day retry? | No retry. Same-day spam is worse, and spec §6d already accepts "not carried forward". |
| OQ5 | Should `paper`-status sell rules propose in the live cycle (R16)? | Yes, preview-only and labelled `rule_status='paper'`. |
| OQ6 | Is `keel dca proposals review` a capability row (R23)? | No, a `[y/N]` at a TTY. Revisit if `execution: auto` is ever proposed. |
| OQ7 | Should `keel autonomy on --sells` require `--for-hours`? | No. It mirrors `autonomy on`: optional, with the same no-expiry warning printed. |
| OQ8 | Doctor names: `ledger.drift` and `ledger.venue_drift`, or the spec's `sleeve.*`? | `ledger.*` (R2). |
| OQ9 | Where do the exit monitor's default levels live? | Module constants in `keel/execution/sleeve_exit.py`, overridden per product by a `sleeve_exit` rule (R26). |
| OQ10 | Should a transition back to `clear` notify? | Yes, one `sleeve.exit_watch` event worded as a recovery. |
| OQ11 | Should `keel positions` get a `list` subcommand while the group exists? | No. It is out of #798's scope. |
| OQ12 | Trial 4 (profit-take over VWAE, judged on drawdown), spec §11 PR 7 | Not in this plan. The research freeze holds, and it is written only when the operator asks. |

---

# Self-review against the goal

**Spec coverage.** Every in-scope section of the spec maps to a task:
- §3.1 named kinds: P9, P14, P16, plus the `ARBITRATION_ORDER` names.
- §3.2 `REDUCE`, `Reduction`, the hook, `executor.reduce`, `_handle_reductions` and `book_exit(None)`: P5, P7, P8.
- §3.3 `Holding` and the two drift findings: P3, P5.
- §3.4 the rails per leg and the sleeve caps: P7, P8.
- §3.5 the gates: P17, P18. `execution: auto` is deliberately not built (R22).
- §3.6 arbitration: P7, P8.
- §3.7 promotion: P12.
- §3.8 the audit trail: P6, P8, P15, P17, P18.
- §4 `profit_take` and `trim --preview --view gain`: P14.
- §5 `--view bands` only: P13.
- §6 `reverse_dca`, its CLI, sim and doctor: P9–P12.
- §7 the monitor and `sleeve_exit`: P15, P16.
- §8.1 `--view lots`: P13. §8's `rotation` is not built.
- §9 the failure table:

| Row | Where it is covered |
|---|---|
| 1 | P7 |
| 2 | P3 |
| 3 | P7 |
| 4 | P8, P10 |
| 5 | P7 |
| 6 | P7 |
| 7 | P8 |
| 8 | S4 |
| 9 | P18 |
| 10 | P15 |
| 11 | P5 (for a `Reduction`). The `_handle_exits` half stays out of scope, as the spec says. |

The prerequisites are covered too: #799 in P1 and P3 (R1), #811 in P2, and #798 in P3 and P4.

**Safety invariants.**
- S1 is an AST inventory from P1 onward, plus behavioural tests at the executor (P7, P18) and the cycle (P8, P16).
- S2 is a registry-wide `Literal` test from P9 onward.
- S3 is P7 Task 7.2.
- S4 is the name scan plus the existing effect scan.

**Order.** Prerequisites (P1–P4), then contracts (P5–P6), then pipeline (P7–P8), then consumers (P9–P18), grouped by the operator's steps. Both schema changes carry a migration and a version bump, in P6 (v22) and P17 (v23).

**Placeholder scan.** Every test step carries code. Three helper names are to be read from the codebase before use: the trade-outcomes reader in `repository.py`, the `ChainState` event accessor in `audit.py`, and `tests/sim/test_account.py`'s account builder. Each task that needs one says where it lives and what to do if it differs. No step says "TBD", "handle edge cases" or "similar to Task N".

**Type consistency.** These names are used identically in every task that touches them:
- `ReduceResult(product_id, rule_kind, proposal_id, decision, vetoed_by, legs, reason)`;
- `sleeve.record_proposal(...)`'s keywords;
- `sleeve.slice_qty` returning `(leg_qty, legs)`;
- `sleeve.sleeve_refusal(...)`'s keywords;
- `Holding.from_rows` and `Holding.fifo_legs` / `fifo_cost`;
- `SellCosts(fee_pct, slippage_pct, fee_source)`;
- `sleeve_exit.classify(product_id, daily, ...)`;
- `Profile.is_autonomous_for_sells`, and `set_sells_window(value, now_ts, expires_ts)`.

**Review Focus.** Five lines, and each names the owning task and its test.

**Research freeze.** No PR runs a sweep, registers a trial or writes to the trials ledger. The sim and replay tests run on synthetic candles against hand computations.

---

# Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-28-dca-sleeve-sell-side-build.md`. The operator's hand-off already fixed the process: plan now, build later, one PR per section in the merge order above, each in its own worktree, each reviewed by fixing before it is opened.

**Recommended method: subagent-driven.** The tasks lean hard on each other's interfaces: `Reduction`, `ReduceResult`, `record_proposal` and the invariants module are consumed by twelve later PRs. The only path to the venue is the thing under test, so a mistake shipped here is a live-money sell. A fresh reviewer per task is worth its cost.
