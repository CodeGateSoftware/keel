# Rail 17 TTL vs DCA Cadence Implementation Plan (#1010)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop rail 17's 7-day withdrawal-attestation TTL from silently starving the 14-day DCA rules. Three surfaces warn when a live DCA rule's next buy falls after the attestation expires. A DCA window vetoed by a guard is retried on later fresh daily bars of the same window, with at most one buy per window per rule. The rail-17 veto text stops implying that DCA is exempt.

**Architecture:** One pure helper in `keel/attestations.py` (`dca_windows_after_expiry`) plus a never-raising repo wrapper (`live_dca_windows_after_expiry`) that `keel withdrawals attest`, `keel status` and `keel doctor` all call. For the retry, `Dca.detect` stays pure. It reads an optional, per-cycle `DcaWindowLedger` value object. Only the live agent sets this object, computing it from two new `Repository` reads (the rule's newest counted live BUY and its recent signal timestamps). When the ledger is `None` (backtest, account sim, paper, every existing test), behaviour is byte-for-byte today's.

**Tech Stack:** Python 3.14, click, sqlite3 via `keel.data.repository.Repository`, pytest, ruff, mypy, uv.

**Spec:** GitHub issue #1010 (`gh issue view 1010`; it has no comments as of 2026-10-27), plus the run ledger's definition of done D1–D4 restated under Global Constraints.

## Global Constraints

- D1: `keel withdrawals attest`, `keel status` and `keel doctor` warn when any LIVE DCA rule's next due window falls after the withdrawal attestation's expiry, naming the rule, the due date and the expiry.
- D2: A DCA window vetoed by a guard is retried on the next fresh daily bar inside the same cadence window. At most one buy per cadence window per rule. A stale bar must not double-buy.
- D3: The rail-17 UNKNOWN veto text in `keel/execution/guards.py` no longer says "exits and DCA-exempt paths are unaffected". DCA is NOT exempt from rail 17.
- D4: Failing test first for every task. The final tree passes all proof commands. Live config and DB are untouched.
- Do NOT change `executor.WITHDRAWAL_ATTESTATION_TTL_SEC` (7 days). The issue rejects lengthening it.
- Do NOT change any guard's semantics. Only the D3 text changes in `guards.py`.
- Do NOT edit any `config*.yaml`, any `*.plist`, any `*.db`, or anything under `~/keel`.
- `Dca.detect` stays pure: no repo, no clock, no I/O. Its inputs are `self` and `candles_by_tf`.
- The D1 arithmetic lives in ONE place (`keel/attestations.py`). The three commands call it and never restate it.
- No JS changes. The web payload is out of scope (see Ruling R6).
- Epoch-day arithmetic uses `_SECONDS_PER_DAY = 86_400` (already in `keel/strategy/rules/dca.py`). The cadence stays anchored to epoch days: a bar of day `d` is a cadence bar iff `d % cadence_days == 0` (`is_cadence_day`).
- A live order placed on UTC day `P` acted on the completed daily bar of day `P - 1`, because `freshness.entry_bar_ready` admits only yesterday's bar (see its docstring). Every window computation below relies on this.
- Proof commands (from `.github/workflows/ci.yml`), run from the worktree root: `uv sync --all-extras --dev`, `uv run ruff check keel tests packages`, `uv run ruff format --check .`, `uv run mypy` (bare, never `mypy keel/`), `uv run pytest -q`.
- This repo writes its rules in docstrings. Every new function gets a docstring that says WHY, citing #1010, the way its neighbours do. Read the neighbouring docstrings before editing a module.

## Rulings made by the planner (binding on the implementer)

- **R1: Window and fill attribution.** For a bar on epoch day `d`, the cadence window is `[ws, ws + c)`, where `ws = d - d % c`. A window is *satisfied* when the rule has a counted live BUY whose **bar day** (`created_at // 86400 - 1`) is `>= ws`. The orchestrator's literal wording, "`created_at` inside the window", is wrong in two ways because of the one-day placement lag. A buy for the window's last bar is placed on day `ws + c`, so the literal rule would count it in the NEXT window and suppress that window's regular buy. With `cadence_days == 1`, every buy would land in the next window and the rule would never buy again. The shift fixes both.
- **R2: "Counted" BUY = `status IN ('filled', 'pending')`.** A DCA market order is written `pending` at insert and `filled` on success (`executor._initial_status`). A `pending` row is one whose outcome nobody has observed yet. Treating it as unfilled is exactly the double-buy hazard. `rejected` and `canceled` do not count, so the window is retried.
- **R3: A retry needs proof the window was due.** A retry fires only if the rule emitted a signal for an EARLIER bar of the same window (a `signals` row with `rule_id` = this rule and bar day in `[ws, d)`). `engine.evaluate` persists every emitted signal before execution, so a guard-vetoed or broker-rejected cadence buy leaves one. Without that proof, a rule promoted mid-window would buy on promotion day. Consequence: a cadence day on which entries were withheld for the whole cycle (feed not ready, kill switch before evaluation) leaves no signal and is not retried. That matches D2's "vetoed by a guard".
- **R4: "Reported, not back-filled".** When the ledger shows the previous window was signalled but never satisfied, the next window's cadence-day setup carries `context["previous_window_missed_ts"]`. The agent then logs a WARNING event `agent.dca_window_missed`. Nothing buys for the missed window.
- **R5: Due date for D1.** `next_dca_bar_ts(now_ts, c) = next_cadence_day_ts(now_ts - 86400, c)`: the next cadence bar not yet acted on, counting yesterday's bar. `due_ts = bar_ts + 86400` is 00:00 UTC of the day the buy is placed. A rule is reported when `expires_at < due_ts + 86400`, meaning the attestation does not last through the whole placement day. The live cycle runs at 01:20 UTC, not 00:00, so this is the conservative test. `reattest_from_ts = due_ts + 86400 - TTL`: an attestation made on or after it covers that placement day.
- **R6: The web console does not get this for free.** `attestations.survey()` is left unchanged. It feeds `notifications` alert-once windows and `web/api.py` banners, and adding entries there would change both. A follow-up issue can surface it on the web. This PR does not plan JS.
- **R7: Paper cycles never get a ledger.** The ledger is set only when `paper_trader is None` (live cycles). Paper and sim stay on today's cadence-only behaviour.
- **R8: Doctor always emits `attest.dca_cadence`.** It is OK when nothing is affected and WARN (not FAIL) when something is. This follows doctor's every-check-by-name convention (`test_gather_findings_covers_every_check_over_a_seeded_db`). WARN, because the rail is not vetoing yet.
- **R9: No CHANGELOG.** The repo has no `CHANGELOG.md`. Release notes come from PR bodies (`docs/RELEASING.md` §"Release notes come from PRs"), so the docs task updates `docs/rails/rail-17-withdrawal-attestation.md` instead.

## Review Focus

- A second cycle on the same UTC day after a successful retry must not buy again. Task 4's `test_dca_retry_does_not_double_buy_on_a_same_day_rerun` pins this.
- A rule promoted to live mid-window must not buy before its next cadence bar. Task 3's `test_an_unsignalled_window_never_retries` and Task 4's `test_a_rule_promoted_mid_window_waits_for_its_cadence_bar` pin this.
- A cadence-1 (daily) rule must still buy every day once the ledger is on. Task 3's `test_cadence_one_buys_every_day_with_the_ledger_on` pins this.
- An attestation of `--suspended`, or none at all, must not produce cadence warnings, because doctor and status already report those as halts. Task 1's `test_live_wrapper_is_silent_without_an_enabled_attestation` pins this.
- A retry day can coincide with another rule's cadence day, so one day can now carry more DCA spend than `keel dca plan`'s busiest-day figure. Rails 3 and 14 still bound it. No code change; the docs task (Task 9) states it.

---

### Task 1: Shared D1 helper: which live DCA windows fall after rail 17's expiry

**Files:**
- Modify: `keel/strategy/rules/dca.py` (add `next_dca_bar_ts` beside `next_cadence_day_ts`)
- Modify: `keel/attestations.py` (add `DcaWindowAfterExpiry`, `dca_windows_after_expiry`, `live_dca_windows_after_expiry`; extend the module docstring with a short "#1010: TTL vs cadence" paragraph)
- Test: `tests/test_dca_windows_after_expiry.py` (new)

**Interfaces:**
- Produces:
  - `keel.strategy.rules.dca.next_dca_bar_ts(now_ts: int, cadence_days: int) -> int`: `next_cadence_day_ts(now_ts - _SECONDS_PER_DAY, cadence_days)`. Raises `ValueError` on non-positive cadence (inherited).
  - `keel.attestations.DcaWindowAfterExpiry`: frozen dataclass with fields `rule_id: int`, `product_id: str`, `cadence_days: int`, `bar_ts: int`, `due_ts: int`, `expires_at: int`, `reattest_from_ts: int`, and a method `line() -> str`.
  - `keel.attestations.dca_windows_after_expiry(rules: Iterable[tuple[int, str, int]], *, attested_at: int, now_ts: int) -> tuple[DcaWindowAfterExpiry, ...]`. Pure. `rules` are `(rule_id, product_id, cadence_days)`. `expires_at` comes from `withdrawal_expiry(attested_at=...)`, so the TTL is read at call time. Applies Ruling R5. Output is in input order.
  - `keel.attestations.live_dca_windows_after_expiry(repo: Any, now_ts: int) -> tuple[DcaWindowAfterExpiry, ...]`. Never raises (returns `()` on any exception, like `survey`). Returns `()` unless `withdrawals_attested_at` is truthy AND `withdrawals_enabled` is truthy. Rules come from `repo.get_rules("live")` filtered to `kind == "dca"`. Each row is built with a lazy-imported `keel.agent.build_rule_from_params(row["kind"], row["params"] or {})`, the single coercion boundary, so the default cadence is never restated. A row that fails to build is skipped. Tuples are `(int(row["id"]), rule.product_id, int(rule.params["cadence_days"]))`.
- `line()` exact format, with dates in UTC (`datetime.fromtimestamp(ts, UTC)`):
  `rule {rule_id} {product_id} (every {cadence_days}d): next DCA buy due {due:%Y-%m-%d} (bar {bar:%Y-%m-%d}), after the withdrawal attestation expires {expires:%Y-%m-%d %H:%M}Z; rail 17 will veto it -- re-attest on or after {reattest:%Y-%m-%d} to cover it`

- [ ] **Step 1: Write the failing tests** in `tests/test_dca_windows_after_expiry.py`. Use the live evidence: attested 2026-10-10T19:25Z, expired 2026-10-17T19:25Z, weekly rule 6, biweekly rule 9. Constants: `ATTESTED = 1_791_660_300`, `DAY = 86_400`. Use a `repo` fixture like `tests/test_attestation_expiry.py` (`connect(str(tmp_path / "keel.db"))`, `migrate`, `Repository`).

```python
def test_next_dca_bar_is_the_next_cadence_bar_not_yet_acted_on() -> None:
    assert next_dca_bar_ts(ATTESTED, 14) == 20_748 * DAY   # 2026-10-22 bar
    assert next_dca_bar_ts(ATTESTED, 7) == 20_741 * DAY    # 2026-10-15 bar
    # on the placement day itself, yesterday's cadence bar is still the due one
    assert next_dca_bar_ts(20_749 * DAY + 4_800, 14) == 20_748 * DAY

def test_the_live_lapse_is_reproduced_biweekly_flagged_weekly_not() -> None:
    found = attestations.dca_windows_after_expiry(
        [(6, "BTC-USD", 7), (9, "PAXG-USD", 14)], attested_at=ATTESTED, now_ts=ATTESTED
    )
    assert [w.rule_id for w in found] == [9]
    (w,) = found
    assert (w.bar_ts, w.due_ts, w.expires_at, w.reattest_from_ts) == (
        20_748 * DAY, 20_749 * DAY, 1_792_265_100, 20_743 * DAY
    )
    assert w.line() == (
        "rule 9 PAXG-USD (every 14d): next DCA buy due 2026-10-23 (bar 2026-10-22), after the "
        "withdrawal attestation expires 2026-10-17 19:25Z; rail 17 will veto it -- re-attest on "
        "or after 2026-10-17 to cover it"
    )

def test_expiry_is_read_from_the_executor_at_call_time(monkeypatch) -> None:
    monkeypatch.setattr(executor, "WITHDRAWAL_ATTESTATION_TTL_SEC", 14 * DAY)
    assert attestations.dca_windows_after_expiry(
        [(9, "PAXG-USD", 14)], attested_at=ATTESTED, now_ts=ATTESTED) == ()

def test_an_attestation_lasting_exactly_through_the_placement_day_is_not_reported() -> None:
    # attested_at chosen so expires_at == due_ts + DAY for the 2026-10-22 bar
    at = 20_750 * DAY - 7 * DAY
    assert attestations.dca_windows_after_expiry([(9, "PAXG-USD", 14)], attested_at=at, now_ts=at) == ()
    assert len(attestations.dca_windows_after_expiry([(9, "PAXG-USD", 14)], attested_at=at - 1, now_ts=at)) == 1

def test_live_wrapper_reads_only_live_dca_rows(repo) -> None:
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", ATTESTED)
    live14 = repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    repo.insert_rule("dca", {"product_id": "ADA-USD", "cadence_days": 14}, status="paper")
    repo.insert_rule("dca", {"product_id": "XLM-USD", "cadence_days": 14}, status="disabled")
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")  # default cadence 7: covered
    found = attestations.live_dca_windows_after_expiry(repo, ATTESTED)
    assert [w.rule_id for w in found] == [live14]

@pytest.mark.parametrize("enabled, attested_at", [(False, ATTESTED), (None, ATTESTED), (True, 0)])
def test_live_wrapper_is_silent_without_an_enabled_attestation(repo, enabled, attested_at) -> None:
    repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    if enabled is not None:
        repo.set_state("withdrawals_enabled", enabled)
    repo.set_state("withdrawals_attested_at", attested_at)
    assert attestations.live_dca_windows_after_expiry(repo, ATTESTED) == ()

def test_live_wrapper_never_raises() -> None:
    class _Broken:
        def get_state(self, *a, **k): raise RuntimeError("locked")
    assert attestations.live_dca_windows_after_expiry(_Broken(), ATTESTED) == ()
```

- [ ] **Step 2: Run the tests to verify they fail**
Run: `uv run pytest tests/test_dca_windows_after_expiry.py -q`
Expected: FAIL with `ImportError` (`next_dca_bar_ts`) / `AttributeError` (`dca_windows_after_expiry`).

- [ ] **Step 3: Implement** `next_dca_bar_ts` in `keel/strategy/rules/dca.py`, and the dataclass and two functions in `keel/attestations.py`, with the signatures above. The docstrings must say why "bar + 1 day" is the due day (the freshness gate) and why the pure function takes `attested_at` rather than `expires_at` (the TTL is read at call time, which is the module's own rule). Lazy-import `keel.agent` inside `live_dca_windows_after_expiry`, as `withdrawal_expiry` does with `executor`, to avoid an import cycle.

- [ ] **Step 4: Run the tests to verify they pass**
Run: `uv run pytest tests/test_dca_windows_after_expiry.py tests/test_attestation_expiry.py tests/strategy/test_dca_cadence.py -q`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add keel/strategy/rules/dca.py keel/attestations.py tests/test_dca_windows_after_expiry.py
git commit -m "feat(#1010): compute which live DCA windows fall after rail 17's expiry"
```

### Task 2: Repository reads for the DCA window ledger

**Files:**
- Modify: `keel/data/repository.py` (two methods; put `last_live_buy_created_at` after `get_orders`, and `signal_ts_since` after `insert_signal`)
- Test: `tests/data/test_repository.py`

**Interfaces:**
- Produces:
  - `Repository.last_live_buy_created_at(self, rule_id: int) -> int | None`: `SELECT MAX(created_at) FROM orders WHERE mode = 'live' AND side = 'BUY' AND rule_id = ? AND status IN ('filled', 'pending')`. Applies Ruling R2. The docstring cites R2's double-buy reasoning.
  - `Repository.signal_ts_since(self, rule_id: int, since_ts: int) -> list[int]`: `SELECT ts FROM signals WHERE rule_id = ? AND ts >= ? ORDER BY ts`. Inclusive.

- [ ] **Step 1: Write the failing tests** (use the module's existing `repo` fixture; insert a real rule first, because `orders.rule_id` has a foreign key to `rules.id`)

```python
def test_last_live_buy_created_at_counts_filled_and_pending_buys_of_that_rule_only(repo):
    rid = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    other = repo.insert_rule("dca", {"product_id": "ETH-USD"}, status="live")
    assert repo.last_live_buy_created_at(rid) is None
    # helper `_order(rule_id, created_at, status="filled", side="BUY", mode="live")` builds the
    # same dict `tests/execution/test_guards.py::_seed_filled_order` does, with these overrides
    repo.insert_order(_order(rid, 100))
    repo.insert_order(_order(rid, 200, status="pending"))
    repo.insert_order(_order(rid, 900, status="rejected"))
    repo.insert_order(_order(rid, 901, status="canceled"))
    repo.insert_order(_order(rid, 902, side="SELL"))
    repo.insert_order(_order(rid, 903, mode="paper"))
    repo.insert_order(_order(other, 904))
    assert repo.last_live_buy_created_at(rid) == 200

def test_signal_ts_since_is_inclusive_ordered_and_per_rule(repo):
    rid = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    other = repo.insert_rule("dca", {"product_id": "ETH-USD"}, status="live")
    for rule_id, ts in ((rid, 300), (rid, 100), (rid, 200), (other, 250)):
        repo.insert_signal({"rule_id": rule_id, "product_id": "BTC-USD", "ts": ts, "fired": 1})
    assert repo.signal_ts_since(rid, 200) == [200, 300]
    assert repo.signal_ts_since(rid, 301) == []
```

- [ ] **Step 2: Run the tests to verify they fail**
Run: `uv run pytest tests/data/test_repository.py -q -k "last_live_buy or signal_ts_since"`
Expected: FAIL with `AttributeError: 'Repository' object has no attribute ...`

- [ ] **Step 3: Implement both methods** with the SQL above, parameterised and with no f-strings. `int(...)` the MAX when it is not `None`.

- [ ] **Step 4: Run the tests to verify they pass**
Run: `uv run pytest tests/data/test_repository.py -q`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add keel/data/repository.py tests/data/test_repository.py
git commit -m "feat(#1010): repository reads for a DCA rule's counted buys and recent signals"
```

### Task 3: `Dca.detect` retries a signalled, unsatisfied window (pure)

**Files:**
- Modify: `keel/strategy/rules/dca.py` (add `DcaWindowLedger`; add a class attribute `window_ledger: DcaWindowLedger | None = None` on `Dca`; extend `detect`; add a module-docstring paragraph on #1010)
- Test: `tests/strategy/test_dca_window_retry.py` (new; reuse the `_candle(day)` shape from `tests/strategy/test_dca_cadence.py`)

**Interfaces:**
- Produces:
  - `keel.strategy.rules.dca.DcaWindowLedger`: frozen dataclass with `last_counted_bar_day: int | None` (the bar day of the rule's newest counted live BUY, i.e. `created_at // 86400 - 1`) and `signalled_bar_days: frozenset[int]` (bar days the rule emitted a signal for, covering at least the current and previous window).
  - `Dca.window_ledger: DcaWindowLedger | None = None`. This is a class-level default, and is not a constructor parameter, so `test_rule_contract.py`'s constructor introspection and every `describe()`/`params` round-trip stay unchanged.
- `detect` algorithm. With `d = latest.ts // 86400`, `c = cadence_days`, `ws = d - d % c` and `L = self.window_ledger`:
  1. `L is None`: exactly today's code path, adding no context keys.
  2. `L.last_counted_bar_day is not None and L.last_counted_bar_day >= ws`: set `self.last_rejection = {"gate": "dca_window_satisfied", "window_start_ts": ws * 86400}` and return `None`.
  3. `d == ws`: fire as today. Add `context["window_start_ts"] = ws * 86400` and `context["dca_retry"] = False`. If `any(ws - c <= s < ws for s in L.signalled_bar_days)` and (`L.last_counted_bar_day is None or L.last_counted_bar_day < ws - c`), also add `context["previous_window_missed_ts"] = (ws - c) * 86400` (Ruling R4).
  4. `any(ws <= s < d for s in L.signalled_bar_days)`: fire with the same sizing as a cadence hit. Add `context["window_start_ts"] = ws * 86400` and `context["dca_retry"] = True`.
  5. Otherwise return `None`.
  Every fired path sets `self.last_rejection = None`, as `cusum_event.py:246` does. Sizing (dip bonus, lookback) is unchanged and computed on the current bar.

- [ ] **Step 1: Write the failing tests**

```python
def _detect(rule, bar_day):
    return rule.detect({Granularity.ONE_DAY: [_candle(d) for d in range(85, bar_day + 1)]})

def test_without_a_ledger_nothing_changes_off_cadence() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    assert rule.window_ledger is None
    assert _detect(rule, 99) is None
    setup = _detect(rule, 98)
    assert setup is not None and "dca_retry" not in setup.context

def test_a_signalled_unsatisfied_window_retries_on_every_later_bar_of_that_window() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=None, signalled_bar_days=frozenset({98}))
    for d in range(99, 105):
        setup = _detect(rule, d)
        assert setup is not None, d
        assert setup.context["dca_retry"] is True
        assert setup.context["window_start_ts"] == 98 * 86_400
    nxt = _detect(rule, 105)
    assert nxt is not None and nxt.context["dca_retry"] is False

def test_a_satisfied_window_never_fires_again_even_on_its_own_cadence_bar() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=98, signalled_bar_days=frozenset({98}))
    for d in range(98, 105):
        assert _detect(rule, d) is None, d
    assert rule.last_rejection == {"gate": "dca_window_satisfied", "window_start_ts": 98 * 86_400}

def test_an_unsignalled_window_never_retries() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=None, signalled_bar_days=frozenset())
    assert _detect(rule, 100) is None
    assert _detect(rule, 98) is not None

def test_the_bars_own_signal_does_not_justify_a_retry_on_that_bar() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=None, signalled_bar_days=frozenset({100}))
    assert _detect(rule, 100) is None

def test_a_previous_windows_signal_does_not_retry_into_this_one() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=None, signalled_bar_days=frozenset({91}))
    assert _detect(rule, 99) is None

def test_cadence_one_buys_every_day_with_the_ledger_on() -> None:
    rule = Dca("BTC-USD", cadence_days=1)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=98, signalled_bar_days=frozenset({98}))
    assert _detect(rule, 99) is not None

def test_a_missed_previous_window_is_reported_on_the_next_cadence_bar_not_back_filled() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=None, signalled_bar_days=frozenset({98}))
    setup = _detect(rule, 105)
    assert setup is not None and setup.context["previous_window_missed_ts"] == 98 * 86_400
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=99, signalled_bar_days=frozenset({98}))
    assert "previous_window_missed_ts" not in _detect(rule, 105).context
```

- [ ] **Step 2: Run the tests to verify they fail**
Run: `uv run pytest tests/strategy/test_dca_window_retry.py -q`
Expected: FAIL with `ImportError: cannot import name 'DcaWindowLedger'`

- [ ] **Step 3: Implement** `DcaWindowLedger`, `Dca.window_ledger` and the `detect` branches above. Update the `detect` docstring: it is still pure, and the ledger is an input the caller supplies, set per cycle by `agent.run_once` and never by the sim. Update the `Dca` class docstring to name the ledger.

- [ ] **Step 4: Run the tests to verify they pass, and that the default path is unchanged**
Run: `uv run pytest tests/strategy/ tests/sim/ -q`
Expected: PASS (the existing `test_dca.py`, `test_dca_cadence.py`, `test_engine.py`, `test_rule_contract.py`, `test_rule_conformance.py` and `tests/sim/*` are untouched and still green)

- [ ] **Step 5: Commit**
```bash
git add keel/strategy/rules/dca.py tests/strategy/test_dca_window_retry.py
git commit -m "feat(#1010): Dca retries a vetoed window from a caller-supplied ledger, one buy per window"
```

### Task 4: The live agent arms the ledger and reports a missed window

**Files:**
- Modify: `keel/agent.py`. Add `_dca_window_ledger`. Call it in `run_once`'s main pass. Add the `agent.dca_window_missed` log. Extend the `_entry_gate_granularity` docstring's DCA paragraph (~line 372) with one sentence: the stale-bar double-buy is now also stopped by the window ledger, while the freshness gate is still what keeps a stale bar out.
- Test: `tests/test_agent.py` (append a `# -- #1010: DCA window retry` section; reuse `repo`, `FakeBroker`, `_config`, `_candle`, `run_once`, `PRODUCT`)

**Interfaces:**
- Consumes: `Repository.last_live_buy_created_at`, `Repository.signal_ts_since` (Task 2); `DcaWindowLedger`, `Dca.window_ledger` (Task 3).
- Produces: `keel.agent._dca_window_ledger(repo: Repository, rule: Dca, now_ts: int) -> DcaWindowLedger`:
  - `last = repo.last_live_buy_created_at(rule.rule_id)`, giving `last_counted_bar_day = None if last is None else last // 86400 - 1`.
  - `c = int(rule.params["cadence_days"])` and `since = (now_ts // 86400 - 2 * c - 1) * 86400`, giving `signalled_bar_days = frozenset(ts // 86400 for ts in repo.signal_ts_since(rule.rule_id, since))`.
- Wiring in `run_once`'s MAIN PASS, after the `if not entries_allowed: continue` and before `engine.evaluate(product_rules, ...)`: when `paper_trader is None`, set `rule.window_ledger = _dca_window_ledger(repo, rule, now_ts)` for each `rule` in `product_rules` with `isinstance(rule, Dca) and rule.rule_id is not None` (Ruling R7). Rules are rebuilt every cycle, so the attribute never outlives the cycle.
- After `engine.evaluate` returns, for each signal whose `setup.context` has `previous_window_missed_ts`, emit `log_event(logger, logging.WARNING, "agent.dca_window_missed", product=product_id, rule=signal.rule_name, rule_id=signal.rule_id, window_start_ts=<that value>)`.
- The freshness gate (`entry_bar_ready` pre-pass, `entries_allowed`) is untouched. A retry can only fire on a bar that gate admitted (Constraint 3).

- [ ] **Step 1: Write the failing tests.** The module already defines `DAY = 86_400` (~line 5342). Reuse it, and append the new section after that definition. Use cadence 7 and cadence bar day 98. Each cycle gets its own broker serving days `98..bar_day`, built as `_series(last_day) = {(PRODUCT, Granularity.ONE_DAY): [_candle(d * 86_400) for d in range(98, last_day + 1)]}`. A cycle on day `X` runs at `now_ts = X * 86_400 + 3_600`. Rail 17 is made stale with `repo.set_state("withdrawals_attested_at", 1)` and fresh with `repo.set_state("withdrawals_attested_at", 10**12)`.

```python
def test_dca_vetoed_window_is_retried_on_the_next_fresh_bar(repo):
    rid = repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    repo.set_state("withdrawals_attested_at", 1)                       # rail 17 vetoes
    r1 = run_once(FakeBroker(series=_series(98)), repo, _config(), now_ts=99 * DAY + 3_600)
    assert len(r1.enter_signals) == 1 and r1.enter_results[0].placed is False
    assert repo.get_orders(mode="live", product_id=PRODUCT) == []
    repo.set_state("withdrawals_attested_at", 10**12)                  # operator re-attests
    r2 = run_once(FakeBroker(series=_series(99)), repo, _config(), now_ts=100 * DAY + 3_600)
    assert r2.enter_results[0].placed is True
    assert r2.enter_signals[0].setup.context["dca_retry"] is True
    orders = repo.get_orders(mode="live", product_id=PRODUCT)
    assert len(orders) == 1 and orders[0]["rule_id"] == rid

def test_dca_retry_does_not_double_buy_on_a_same_day_rerun_or_later_in_the_window(repo):
    # same setup as above through r2, then:
    run_once(FakeBroker(series=_series(99)), repo, _config(), now_ts=100 * DAY + 7_200)   # same bar
    run_once(FakeBroker(series=_series(100)), repo, _config(), now_ts=101 * DAY + 3_600)  # later bar
    assert len(repo.get_orders(mode="live", product_id=PRODUCT)) == 1
    r5 = run_once(FakeBroker(series=_series(105)), repo, _config(), now_ts=106 * DAY + 3_600)
    assert r5.enter_results[0].placed is True                          # next window buys normally
    assert len(repo.get_orders(mode="live", product_id=PRODUCT)) == 2

def test_an_unfilled_window_is_reported_not_back_filled(repo, caplog):
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    repo.set_state("withdrawals_attested_at", 1)
    run_once(FakeBroker(series=_series(98)), repo, _config(), now_ts=99 * DAY + 3_600)
    repo.set_state("withdrawals_attested_at", 10**12)
    with caplog.at_level(logging.WARNING):
        r = run_once(FakeBroker(series=_series(105)), repo, _config(), now_ts=106 * DAY + 3_600)
    assert [s.setup.context.get("dca_retry") for s in r.enter_signals] == [False]
    assert len(repo.get_orders(mode="live", product_id=PRODUCT)) == 1
    missed = [e for e in _events(caplog) if e[0] == "agent.dca_window_missed"]
    assert len(missed) == 1 and missed[0][1]["window_start_ts"] == 98 * DAY
    # `_events(caplog)`: new helper beside `_skip_events` (~line 315), same body without the
    # `agent.cycle_skipped` filter -- `[(r.getMessage(), getattr(r, _FIELDS_ATTR, {})) for r in caplog.records]`

def test_a_rule_promoted_mid_window_waits_for_its_cadence_bar(repo):
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    r = run_once(FakeBroker(series=_series(100)), repo, _config(), now_ts=101 * DAY + 3_600)
    assert r.enter_signals == [] and repo.get_orders(mode="live", product_id=PRODUCT) == []
```

- [ ] **Step 2: Run the tests to verify they fail**
Run: `uv run pytest tests/test_agent.py -q -k "dca_vetoed_window or double_buy_on_a_same_day or reported_not_back_filled or promoted_mid_window"`
Expected: the first three FAIL (no retry: `r2.enter_signals` is empty; no `agent.dca_window_missed` event). `test_a_rule_promoted_mid_window_waits_for_its_cadence_bar` already passes today and is a guard against regression. Note that in its Step 2 output.

- [ ] **Step 3: Implement** `_dca_window_ledger` and the wiring above.

- [ ] **Step 4: Run the tests to verify they pass**
Run: `uv run pytest tests/test_agent.py tests/test_hostile_rule_rails.py tests/strategy/test_paper.py -q`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add keel/agent.py tests/test_agent.py
git commit -m "feat(#1010): live cycles retry a vetoed DCA window and report a missed one"
```

### Task 5: `keel withdrawals attest` warns about uncovered DCA windows

**Files:**
- Modify: `keel/commands/withdrawals.py` (`withdrawals_attest`)
- Test: `tests/execution/test_withdrawal_attestation.py`

**Interfaces:**
- Consumes: `attestations.live_dca_windows_after_expiry(repo, now_ts)` (Task 1).
- Behaviour: only on `--enabled`, after the existing `withdrawals attested ENABLED; expires in 7 days` echo, call `click.echo(f"WARNING: {w.line()}")` for each window returned for `now_ts`. `--suspended` prints nothing new.

- [ ] **Step 1: Write the failing test**

```python
def test_attest_enabled_names_each_live_dca_rule_whose_next_buy_falls_after_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    monkeypatch.setattr("keel.commands.withdrawals.time.time", lambda: 1_791_660_300)
    db = tmp_path / "t.db"
    conn = connect(str(db)); migrate(conn); repo = Repository(conn)   # `keel.data.db.connect`/`migrate`
    rid = repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="live")
    out = CliRunner().invoke(cli, ["--db", str(db), "withdrawals", "attest", "--enabled"], input="yes\n")
    assert out.exit_code == 0, out.output
    warnings = [l for l in out.output.splitlines() if l.startswith("WARNING: ")]
    assert len(warnings) == 1
    assert warnings[0].startswith(f"WARNING: rule {rid} PAXG-USD (every 14d): next DCA buy due 2026-10-23")
    assert "expires 2026-10-17 19:25Z" in warnings[0]
```

- [ ] **Step 2: Run it to verify it fails**
Run: `uv run pytest tests/execution/test_withdrawal_attestation.py -q -k after_expiry`
Expected: FAIL (`len(warnings) == 0`)

- [ ] **Step 3: Implement** the echo loop. Add one sentence to the command docstring citing #1010.

- [ ] **Step 4: Run to verify it passes**
Run: `uv run pytest tests/execution/test_withdrawal_attestation.py tests/test_cli.py -q -k withdrawals`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add keel/commands/withdrawals.py tests/execution/test_withdrawal_attestation.py
git commit -m "feat(#1010): withdrawals attest warns when a live DCA buy falls after expiry"
```

### Task 6: `keel status` shows the same warning

**Files:**
- Modify: `keel/commands/status.py` (`StatusReport`, `gather_status`, `render_human`)
- Test: `tests/commands/test_status.py`

**Interfaces:**
- Consumes: `attestations.live_dca_windows_after_expiry`, `DcaWindowAfterExpiry` (Task 1).
- Produces: `StatusReport.dca_windows_after_expiry: tuple[DcaWindowAfterExpiry, ...] = ()`, declared as the LAST field with a default, so every existing `StatusReport(...)` construction (TUI tests, `pulse`) stays valid. This is the pattern `market_session` documents. `gather_status` fills it with `attestations.live_dca_windows_after_expiry(repo, now_ts)`. `render_human` appends `f"  WARNING: {w.line()}"` for each window, immediately after the `_rail17_line(...)` line. `--json` carries it through `asdict` unchanged.

- [ ] **Step 1: Write the failing tests** (module `repo` fixture and `_config()`; seed `withdrawals_enabled=True`, `withdrawals_attested_at=1_791_660_300`, one live 14-day DCA rule and one live 7-day DCA rule; `now_ts=1_791_660_300`)

```python
def test_status_lists_each_live_dca_rule_due_after_the_attestation_expires(repo):
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", 1_791_660_300)
    rid14 = repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 14}, status="live")
    repo.insert_rule("dca", {"product_id": "ETH-USD", "cadence_days": 7}, status="live")
    report = gather_status(repo, _config(), now_ts=1_791_660_300)
    assert [w.rule_id for w in report.dca_windows_after_expiry] == [rid14]
    lines = render_human(report)
    i = next(n for n, l in enumerate(lines) if l.startswith("rail 17 (withdrawal capability)"))
    assert lines[i + 1] == f"  WARNING: {report.dca_windows_after_expiry[0].line()}"
    assert sum(l.startswith("  WARNING: rule ") for l in lines) == 1

def test_status_json_carries_the_windows(repo):
    # same four seeding lines as above
    report = gather_status(repo, _config(), now_ts=1_791_660_300)
    assert _report_to_jsonable(report)["dca_windows_after_expiry"][0]["rule_id"] == rid14
```
`"rail 17 (withdrawal capability)"` is `_rail17_line`'s own `prefix`. The module's `NOW_TS` is not used here, because these tests pin the live-evidence clock.

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/commands/test_status.py -q -k dca_windows`
Expected: FAIL with `AttributeError: 'StatusReport' object has no attribute 'dca_windows_after_expiry'`

- [ ] **Step 3: Implement** the field, the gather call and the render lines.

- [ ] **Step 4: Run to verify they pass**
Run: `uv run pytest tests/commands/test_status.py tests/commands/ -q -k "status or pulse or tui"`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add keel/commands/status.py tests/commands/test_status.py
git commit -m "feat(#1010): keel status warns when a live DCA buy falls after rail 17's expiry"
```

### Task 7: `keel doctor` adds `attest.dca_cadence`

**Files:**
- Modify: `keel/commands/doctor.py` (new `dca_cadence_findings`; one call in `gather_findings` right after `attestation_findings`)
- Test: `tests/commands/test_doctor.py` (new tests; add `"attest.dca_cadence"` to the name set in `test_gather_findings_covers_every_check_over_a_seeded_db`)

**Interfaces:**
- Consumes: `attestations.live_dca_windows_after_expiry`, `DcaWindowAfterExpiry` (Task 1).
- Produces: `doctor.dca_cadence_findings(windows: Sequence[attestations.DcaWindowAfterExpiry]) -> list[Finding]`. It always returns exactly one finding (Ruling R8):
  - empty: `Finding("attest.dca_cadence", OK, "no live DCA buy falls after the withdrawal attestation expires", "-", "-")`
  - non-empty: `Finding("attest.dca_cadence", WARN, f"{len(windows)} live DCA buy(s) fall after the withdrawal attestation expires", "; ".join(w.line() for w in windows), "keel withdrawals attest --enabled")`

- [ ] **Step 1: Write the failing tests**

```python
def test_dca_cadence_finding_is_ok_when_nothing_is_uncovered():
    (f,) = dca_cadence_findings(())
    assert (f.name, f.status) == ("attest.dca_cadence", OK)

def test_dca_cadence_finding_warns_and_names_every_uncovered_rule():
    windows = attestations.dca_windows_after_expiry(
        [(9, "PAXG-USD", 14), (10, "ADA-USD", 14), (6, "BTC-USD", 7)],
        attested_at=1_791_660_300, now_ts=1_791_660_300)
    (f,) = dca_cadence_findings(windows)
    assert f.status == WARN and f.headline.startswith("2 live DCA buy(s)")
    assert f.detail.count("rule ") == 2 and "rule 9 PAXG-USD" in f.detail and "rule 10 ADA-USD" in f.detail
    assert f.fix == "keel withdrawals attest --enabled"

def test_gather_findings_wires_the_dca_cadence_check(tmp_path, valid_config_path):
    repo = _seeded_repo(tmp_path / "keel.db")
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", NOW - 6 * 86_400)
    repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 14}, status="live")
    findings = gather_findings(repo, load_config(valid_config_path), [], NOW)
    (f,) = [x for x in findings if x.name == "attest.dca_cadence"]
    assert f.status == WARN
```
The planner checked the arithmetic for `NOW = 1_784_500_000` (epoch day 20653). The next 14-day bar is day 20664 and the buy is placed on day 20665. Expiry is `NOW + 86_400` = 1_784_586_400, which is earlier than `20666 * 86_400`, so the finding is WARN.

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/commands/test_doctor.py -q -k "dca_cadence or covers_every_check"`
Expected: FAIL with `ImportError: cannot import name 'dca_cadence_findings'`, and the name-set assertion fails

- [ ] **Step 3: Implement** `dca_cadence_findings`. Wire `findings += dca_cadence_findings(attestations.live_dca_windows_after_expiry(repo, now_ts))` immediately after the `attestation_findings(...)` call in `gather_findings`.

- [ ] **Step 4: Run to verify they pass**
Run: `uv run pytest tests/commands/test_doctor.py tests/commands/test_doctor_cash_posture.py tests/test_attestation_expiry.py -q`
Expected: PASS (`test_gather_findings_performs_no_writes_whatsoever` still green: the helper only reads)

- [ ] **Step 5: Commit**
```bash
git add keel/commands/doctor.py tests/commands/test_doctor.py
git commit -m "feat(#1010): doctor reports live DCA buys due after rail 17's expiry"
```

### Task 8: Rail 17's veto text stops implying DCA is exempt (D3)

**Files:**
- Modify: `keel/execution/guards.py` (rail 17 UNKNOWN message, ~line 916-920; the rail-17 comment block ~905-912 gains one line: "DCA is NOT exempt: a DCA buy is an acquisition like any other (#1010).")
- Test: `tests/execution/test_guards.py` (rail-17 section)

**Interfaces:** None. Text only. The violation key `withdrawal_capability` and every branch condition stay byte-identical.

New UNKNOWN text, exact:
`"withdrawal_capability: UNKNOWN (no fresh attestation, or the broker did not report). Under §65.4 possession requires that nothing prevents withdrawal on demand; an unverified state is not evidence that it holds. Attest with `keel withdrawals attest --enabled` (exits are unaffected; DCA buys are NOT exempt)."`

- [ ] **Step 1: Write the failing test**

```python
def test_rail17_unknown_text_does_not_imply_dca_is_exempt(repo: Repository) -> None:
    for is_dca in (False, True):
        result = check(_intent(withdrawals_enabled=None, is_dca=is_dca), repo, _config(), NOW_TS)
        (text,) = [v for v in result.violations if v.startswith("withdrawal_capability:")]
        assert "DCA-exempt" not in text
        assert "DCA buys are NOT exempt" in text
```

- [ ] **Step 2: Run to verify it fails**
Run: `uv run pytest tests/execution/test_guards.py -q -k does_not_imply_dca`
Expected: FAIL (`"DCA-exempt" in text`)

- [ ] **Step 3: Replace the string** with the exact text above. Search for anything that matched the old wording: `grep -rn "DCA-exempt paths" keel tests docs`. It must return nothing afterwards.

- [ ] **Step 4: Run to verify it passes**
Run: `uv run pytest tests/execution/ tests/test_notifications.py tests/commands/ -q -k "rail17 or withdrawal or activity or pulse"`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add keel/execution/guards.py tests/execution/test_guards.py
git commit -m "fix(#1010): rail 17's veto text no longer implies DCA is exempt"
```

### Task 9: Rail-17 doc, then the full proof run

**Files:**
- Modify: `docs/rails/rail-17-withdrawal-attestation.md` (new section "## The TTL and the DCA cadence (#1010)")

The new section must state:
1. The 7-day TTL vs the 14-day cadence, with the live 2026-10-10 → 2026-10-23 sequence from the issue.
2. That `keel withdrawals attest --enabled`, `keel status` and `keel doctor` (`attest.dca_cadence`) now name each live DCA rule whose next buy is due after expiry, with the date to re-attest from.
3. That a window vetoed by a guard is retried on each later fresh daily bar of the same window, at most one buy per window per rule. A window that ends unfilled is logged as `agent.dca_window_missed` and is not back-filled.
4. That a retry day can coincide with another rule's cadence day, so one day may now carry more DCA spend than `keel dca plan`'s busiest-day figure, still bounded by rails 3 and 14.
5. That the web console banner does not yet show this (Ruling R6).

Also edit the "What the rail permits" bullet so that no sentence there implies DCA is exempt. It already says "DCA buys included"; keep it.

- [ ] **Step 1: Write the doc section.** There is no failing test: the doc is prose, and D3's test already pins the code text.
- [ ] **Step 2: Full proof run**
```bash
uv sync --all-extras --dev
uv run ruff check keel tests packages
uv run ruff format --check .
uv run mypy
uv run pytest -q
```
Expected: every command exits 0. If `ruff format --check` flags a file you touched, run `uv run ruff format <file>` and re-run the check. Do not reformat files you did not touch.
- [ ] **Step 3: Verify nothing live was touched**
Run: `git diff --stat origin/main -- '*.yaml' '*.plist' '*.db' keel/execution/executor.py`
Expected: empty output. `WITHDRAWAL_ATTESTATION_TTL_SEC` is unchanged, and no config or DB file appears.
- [ ] **Step 4: Commit**
```bash
git add docs/rails/rail-17-withdrawal-attestation.md
git commit -m "docs(#1010): rail 17 TTL vs DCA cadence, the retry and the warnings"
```

---

## Self-review against the definition of done

- **D1** (attest/status/doctor warn, naming rule, due date, expiry): Task 1 has the single computation and the exact `line()` text with rule id, product, due date and expiry. Tasks 5, 6 and 7 are the three surfaces, and each one calls `live_dca_windows_after_expiry` without restating any arithmetic. The live 10-10/10-17/10-23 lapse is reproduced in Task 1's headline test.
- **D2** (vetoed window retried on the next fresh bar, one buy per window, no stale double-buy): Task 2 adds the reads, Task 3 the pure decision, and Task 4 the live wiring plus the same-day rerun, later-bar, next-window, reported-not-back-filled and promoted-mid-window tests. The freshness gate is untouched, so a retry only ever runs on an admitted bar.
- **D3** (veto text): Task 8, with an exact-string test over both `is_dca` values.
- **D4** (TDD, green proof, live untouched): every task opens with a failing test and the command that shows it failing. Task 4 names the one regression guard that passes before the change. Task 9 runs the five CI commands and checks with a diff that no config, plist, DB or TTL changed.
- **Constraints 1–7:** `Dca.detect` stays pure, and a `None` ledger is today's path (Task 3, Step 4 runs `tests/strategy` and `tests/sim`). The window definition is per Ruling R1 (the literal one would break cadence 1 and steal the next window's buy). The freshness gate is kept (Task 4). The D1 arithmetic is in one place (Task 1). The TTL, guard semantics and configs are unchanged (Task 9, Step 3). It is one PR, in the ordered task sequence. Every task has a failing-test step.
- **Type consistency:** `DcaWindowLedger.last_counted_bar_day` / `signalled_bar_days`, `Repository.last_live_buy_created_at` / `signal_ts_since`, `attestations.DcaWindowAfterExpiry` / `dca_windows_after_expiry` / `live_dca_windows_after_expiry`, `dca.next_dca_bar_ts`, `doctor.dca_cadence_findings`, and `StatusReport.dca_windows_after_expiry` are spelled the same in every task.
