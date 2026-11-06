# Rail 17 reads Coinbase, skipped DCA buys retry, skips alert on macOS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live buys stop depending on a 7-day human attestation: rail 17 asks Coinbase per buy, a
skipped DCA buy is retried daily until its rule's next window, and every skipped live DCA buy
posts a macOS notification.

**Architecture:** Coinbase's `/v2/accounts` `allow_withdrawals` flag becomes rail 17's input,
read by the executor through an optional adapter method (absent method = unreadable = veto).
The stored `withdrawals_enabled` key keeps one meaning only: `False` is a manual suspension with
no expiry. DCA catch-up is a ledger-fed attribute on `Dca` that the agent sets before
`engine.evaluate`, so backtests and the account sim (which never set it) are unchanged. The
agent collects skipped DCA buys on the cycle result and posts them through a new macOS notifier
in `keel_core`.

**Tech Stack:** Python 3.14, click, sqlite, pytest, mypy, ruff; `coinbase-advanced-py`
`RESTClient.get`; `/usr/bin/osascript`.

**Spec:** `docs/superpowers/specs/2026-11-06-rail-17-venue-read-and-dca-catchup-design.md`

## Global Constraints

- TDD: every task writes its failing test first and runs it red before any implementation.
- Work in a worktree. Tests that touch `packages/` run with
  `PYTHONPATH=packages/keel-core:packages/keel-broker-api:packages/keel-broker-coinbase:packages/keel-broker-fake:packages/keel-broker-alpaca:packages/keel-broker-kraken:packages/keel-broker-robinhood`
  (the worktree otherwise resolves packages from the main checkout). Type-check with bare `mypy`.
- Rail 17 still applies only to live BUYs (`is_buy and not offline`); SELLs and paper are untouched.
- Fails closed: an unreadable venue, a missing quote wallet, or an adapter without the method
  vetoes the BUY.
- `keel withdrawals attest --enabled` keeps its typed-`yes` TTY gate. `--suspended` stays ungated.
- One fill per DCA rule per window, ever. The ledger (`orders`), not the candle, decides.
- A notification failure never fails the cycle. The notifier is a no-op off macOS
  (`sys.platform != "darwin"`), when `KEEL_DISABLE_MACOS_NOTIFY` is set, and under pytest.
- After touching `keel/web/static/js/*`: `node --check` each changed file and load the console
  in a real browser with the service worker unregistered.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Paginated `/v2/accounts`.** An account with more than one page of wallets must still find
   USDC/BTC; a wallet on page 2 is not "missing". Test in Task 1.
2. **Deploy-day catch-up.** On the first cycle after deploy, rules whose current window was
   vetoed before deploy (all seven, window opened on the 2026-11-05 bar) must buy once, and not
   a second time on the next day. Test in Task 4.
3. **Rule promoted mid-window.** A rule promoted to live after its current window opened must
   not buy immediately on catch-up. Test in Task 4.
4. **Same-day rerun after a crash.** The wrapper retries a failed cycle hourly the same day; a
   rerun after a fill in the same window must not buy again. Test in Task 4.
5. **Notification storm.** Seven skipped rules, every hourly retry of a failed day: at most one
   notification per rule per UTC day. Test in Task 5.

---

### Task 1: Coinbase adapter reads `allow_withdrawals`

**Files:**
- Modify: `packages/keel-broker-api/keel_broker_api/port.py` (add `WithdrawalReader` Protocol after `Broker`)
- Modify: `packages/keel-broker-coinbase/keel_broker_coinbase/transport.py` (declare `get` on `Transport`)
- Modify: `packages/keel-broker-coinbase/keel_broker_coinbase/adapter.py` (method on `CoinbaseAdapter`, beside `verify_cash_account`)
- Modify: `packages/keel-broker-fake/keel_broker_fake/adapter.py` (configurable answer, default all `True`)
- Test: `tests/broker_coinbase/test_adapter.py`

**Interfaces:**
- Produces:
  - `keel_broker_api.port.WithdrawalReader` — `Protocol` with
    `def withdrawals_allowed(self, currencies: Sequence[str]) -> dict[str, bool | None]: ...`
    Each requested currency maps to `True`/`False` from the venue, or `None` when the account
    has no wallet for it. Raises on any failure to read (network, auth, unparseable).
  - `CoinbaseAdapter.withdrawals_allowed(currencies)` implementing it.
  - `Transport.get(self, url_path: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any`.

- [ ] **Step 1: Write the failing tests**

```python
def test_withdrawals_allowed_reads_v2_accounts_flags():
    transport = FakeTransport(v2_pages=[{"data": [
        {"currency": {"code": "USDC"}, "allow_withdrawals": True},
        {"currency": {"code": "BTC"}, "allow_withdrawals": False},
    ], "pagination": {"next_starting_after": None}}])
    got = CoinbaseAdapter(transport).withdrawals_allowed(["USDC", "BTC", "FET"])
    assert got == {"USDC": True, "BTC": False, "FET": None}
    assert transport.get_calls[0][0] == "/v2/accounts"

def test_withdrawals_allowed_follows_pagination():
    # page 1 has BTC, page 2 has USDC; both must be found
    ...
    assert got == {"USDC": True, "BTC": True}
    assert len(transport.get_calls) == 2
    assert transport.get_calls[1][1]["starting_after"] == "<id of last row on page 1>"

def test_withdrawals_allowed_raises_when_unreadable():
    transport = FakeTransport(v2_raises=ConnectionError("reset"))
    with pytest.raises(ConnectionError):
        CoinbaseAdapter(transport).withdrawals_allowed(["USDC"])

def test_withdrawals_allowed_treats_non_bool_flag_as_unreadable():
    # {"allow_withdrawals": "yes"} or a missing key on a wallet that exists -> raises ValueError
    ...

def test_coinbase_adapter_satisfies_withdrawal_reader():
    assert isinstance(CoinbaseAdapter(FakeTransport()), WithdrawalReader)  # runtime_checkable
```

Extend the file's existing transport fake rather than adding a second one. Currency codes are
compared upper-cased; `currency` may be a dict (`{"code": ...}`) or a bare string — accept both.

- [ ] **Step 2: Run, expect FAIL** — `pytest tests/broker_coinbase/test_adapter.py -k withdrawals -v` → `AttributeError: ... withdrawals_allowed`.
- [ ] **Step 3: Implement.** `limit=100`, follow `pagination.next_starting_after` via `starting_after`, stop after 20 pages and raise if still paginating. Docstring records the 2026-11-06 probe (flag observed `true` only; never observed flipping) and that `can_transfer: false` is unaffected. Mark the Protocol `@runtime_checkable`. Fake adapter: `withdrawals_allowed` answers from a constructor dict, defaulting every currency to `True`.
- [ ] **Step 4: Run, expect PASS**, then `mypy` clean.
- [ ] **Step 5: Commit** — `feat(coinbase): read per-wallet allow_withdrawals for rail 17 (#1010)`.

---

### Task 2: Rail 17 decides from the venue; the TTL is gone

**Files:**
- Modify: `keel/execution/executor.py` (replace `_withdrawals_enabled`, delete `WITHDRAWAL_ATTESTATION_TTL_SEC`; `BASE_INCREMENT_TTL_SEC` keeps its own literal `7 * 24 * 60 * 60` and its comment stops citing rail 17)
- Modify: `keel/execution/guards.py` (rail 17 block, `OrderIntent`)
- Test: `tests/execution/test_withdrawal_attestation.py` → rename `tests/execution/test_withdrawal_capability.py`; `tests/execution/test_guards.py`
- Modify test fakes: `tests/execution/test_executor.py::FakeBroker`, `tests/test_agent.py::FakeBroker`, `tests/test_cli.py::FakeBroker` gain `withdrawals_allowed` answering `True` for everything (drop the now-meaningless `withdrawals_attested_at` seeding where it only existed to satisfy the TTL)

**Interfaces:**
- Consumes: `WithdrawalReader.withdrawals_allowed` (Task 1).
- Produces:
  - `executor.manual_withdrawal_suspension(repo) -> bool` — `True` iff stored `withdrawals_enabled` is exactly `False`. No timestamp is read.
  - `executor._withdrawal_capability(broker, repo, product_id: str) -> tuple[bool | None, str]` — `(verdict, detail)`. Order of decision: manual suspension → `(False, "manual suspension (keel withdrawals attest --suspended)")`; broker without `withdrawals_allowed` → `(None, "venue adapter cannot report withdrawals")`; raised read → `(None, "venue unreadable: <ExcType>")`; quote wallet `None` → `(None, "no <QUOTE> wallet reported")`; any requested wallet `False` → `(False, "<CUR> withdrawals disabled at venue")`; else `(True, "")`. Requested currencies are the product's quote leg (`quote_currency_of`) and base leg; base `None` is allowed.
  - `OrderIntent.withdrawals_detail: str = ""`.

- [ ] **Step 1: Write the failing tests** in `test_withdrawal_capability.py`, one per branch above, through `executor.execute` on a live BUY with `FakeBroker`, asserting `result.placed is False`, `vetoed_by[0].startswith("withdrawal_capability:")`, the detail substring, and `broker.place_calls == []`. Plus:

```python
def test_stale_attestation_no_longer_vetoes():
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", NOW_TS - 30 * 86400)
    assert execute(...).placed is True

def test_never_attested_account_buys_when_venue_allows():
    # no withdrawals_* keys at all
    assert execute(...).placed is True

def test_base_wallet_missing_decides_on_quote():
    broker = FakeBroker(withdrawals={"USD": True})  # no FET wallet
    assert execute(signal_for("FET-USD"), ...).placed is True

def test_sell_never_reads_withdrawals():
    broker = FakeBroker(withdrawals_raises=RuntimeError())
    assert execute(exit_signal, ...).placed is True
    assert broker.withdrawals_calls == 0

def test_paper_never_reads_withdrawals(): ...  # offline path, rail recorded as skipped
```

In `test_guards.py`: the rail-17 messages for `None` and `False` include `intent.withdrawals_detail`, and neither says "DCA-exempt" or "attest".

- [ ] **Step 2: Run, expect FAIL** (stale attestation still vetoes; missing method not handled).
- [ ] **Step 3: Implement.** The read happens in the ENTER branch where `_withdrawals_enabled` is called today, live only (`broker is not None`). Log `executor.withdrawal_capability` with product, verdict, detail. New rail-17 messages:
  - `None`: `withdrawal_capability: UNKNOWN ({detail}). Under §65.4 possession requires that nothing prevents withdrawal on demand; an unverified state is not evidence that it holds. Buy skipped; it is retried next cycle within its window.`
  - `False`: `withdrawal_capability: SUSPENDED ({detail}). Under §65.4 an asset that cannot be withdrawn may not have been validly possessed, so new entries are halted. Existing holdings and exits are deliberately unaffected.`
- [ ] **Step 4: Run** `pytest tests/execution tests/test_agent.py tests/test_cli.py tests/test_hostile_rule_rails.py -q` → PASS; `mypy` clean.
- [ ] **Step 5: Commit** — `feat(rail-17): decide withdrawal capability from the venue, drop the 7-day TTL (#1010)`.

---

### Task 3: Remove the expiry surfaces; `withdrawals` CLI becomes a manual switch

**Files:**
- Modify: `keel/commands/withdrawals.py`
- Modify: `keel/attestations.py` (drop the rail-17 `Attestation` from `survey_definitions`, delete `withdrawal_expiry` and `_withdrawal_dates`)
- Modify: `keel/commands/doctor.py` (rail-17 freshness check → a finding that FAILs only when a manual suspension is set; it never reads `withdrawals_attested_at`)
- Modify: `keel/commands/status.py` (`WithdrawalAttestationStatus` → shows `manual suspension: on/off`; no days-to-expiry; no venue call)
- Modify: `keel/notifications.py` (no rail-17 expiry event)
- Modify: `keel/commands/setup.py` (wizard step explains the venue read and the manual switch; no weekly cadence)
- Modify: `keel/web/payload.py`, `keel/web/api.py`, `keel/web/events.py`, `keel/web/static/js/*.js` and `index.html` wherever they render rail-17 expiry
- Test: `tests/test_cli.py`, `tests/test_attestation_expiry.py`, `tests/test_attestation_alert_once.py`, `tests/test_notifications.py`, `tests/commands/test_doctor.py`, `tests/commands/test_status.py`, `tests/web/test_attestation_banner.py`, `tests/web/test_server.py`, `tests/test_first_run_wizard.py`, `tests/commands/test_console_thinness.py`

**Interfaces:**
- Consumes: `executor.manual_withdrawal_suspension` (Task 2), `WithdrawalReader` (Task 1).
- Produces: CLI output contracts below.

- [ ] **Step 1: Write the failing tests.**

```python
def test_attest_enabled_says_it_clears_suspension_and_never_expires(cli_tty_yes):
    out = run("withdrawals attest --enabled")
    assert "manual suspension cleared" in out and "expires" not in out

def test_attest_suspended_has_no_expiry():
    out = run("withdrawals attest --suspended")
    assert "expires" not in out
    assert manual_withdrawal_suspension(repo) is True

def test_show_prints_venue_answer_per_wallet(fake_broker_withdrawals={"USD": True, "BTC": False}):
    out = run("withdrawals show")
    assert "manual suspension: off" in out
    assert re.search(r"BTC\s+withdrawals disabled", out) and re.search(r"USD\s+allowed", out)

def test_show_reports_unreadable_venue_without_failing(): ...  # exit 0, says "venue unreadable"

def test_survey_has_no_withdrawal_attestation():
    assert "withdrawals" not in {a.key for a in attestations.survey_definitions()}

def test_doctor_rail17_passes_without_any_attestation(): ...
def test_doctor_rail17_fails_on_manual_suspension(): ...
def test_notifications_never_emit_withdrawal_expiry_even_when_attested_long_ago(): ...
```

`withdrawals show` lists the quote currency plus the base of every live rule's product; it needs
the broker, so it builds one the way `keel status --live` does and degrades to "venue
unreadable" on failure. Existing tests that assert expiry wording are deleted, not loosened;
list each deleted test in the commit body.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Web console: remove the rail-17 expiry banner; keep a rail-17 line showing manual suspension on/off.
- [ ] **Step 4: Verify.** Full `pytest -q` PASS; `mypy`; `ruff check`; `node --check` on each changed JS file; open `keel serve` in a real browser with the service worker unregistered and confirm the console renders and the banner is gone.
- [ ] **Step 5: Commit** — `feat(rail-17): withdrawals is a manual switch; remove the expiry machinery (#1010)`.

---

### Task 4: DCA catch-up until the next window

**Files:**
- Modify: `keel/strategy/rules/dca.py`
- Modify: `keel/agent.py` (set catch-up state on each loaded `Dca` before `engine.evaluate` and before `_dca_fires_today`)
- Modify: `keel/data/repository.py` only if a rule-scoped order query is needed (`get_orders` filters by mode/product/status today; filtering `rule_id` in Python is acceptable)
- Test: `tests/strategy/test_dca_cadence.py`, `tests/test_agent.py`

**Interfaces:**
- Produces (in `dca.py`):
  - `def window_start_day(day: int, cadence_days: int) -> int` — `day - day % cadence_days`.
  - `@dataclass(frozen=True) class DcaCatchUp: since_day: int; filled_window_day: int | None; in_flight: bool`
    `since_day` = `promoted_at // 86400` (fallback `created_at`); `filled_window_day` = the
    `window_start_day` of the most recent filled BUY for this rule in this mode, computed from
    the order's `created_at // 86400 - 1` (the cycle on day d+1 buys bar d); `in_flight` = any BUY
    for this rule in `RESTING_STATUSES`.
  - `Dca.catch_up: DcaCatchUp | None = None` (class attribute default; backtest/sim never set it).
  - `Setup.context` gains `"catch_up": bool` and `"window_start_day": int` on every DCA setup.
- Produces (in `agent.py`): `def _dca_catch_up_state(repo, rule: Dca, rule_row: dict, *, mode: str) -> DcaCatchUp`.

`Dca.detect` decision with bar day `d`, `w = window_start_day(d, N)`:
1. `catch_up is None` → today's behaviour exactly (fire iff `d == w`).
2. `catch_up.in_flight` → no setup.
3. `catch_up.filled_window_day == w` → no setup (already bought this window).
4. `d == w` → setup, `catch_up=False`.
5. `w + 1 < catch_up.since_day` → no setup (the window's buying cycle, day `w + 1`, ran before the rule went live).
6. else → setup, `catch_up=True`, sized from bar `d`.

- [ ] **Step 1: Write the failing tests** (`test_dca_cadence.py`):

```python
def test_no_catch_up_state_is_unchanged_behaviour():  # backtests
    assert Dca("BTC-USD", cadence_days=7).detect(bars_ending(day=7*k + 3)) is None

def test_catch_up_fires_on_a_missed_window_day():
    rule = Dca("BTC-USD", cadence_days=14); rule.catch_up = DcaCatchUp(since_day=0, filled_window_day=None, in_flight=False)
    s = rule.detect(bars_ending(day=14*k + 5))
    assert s is not None and s.context["catch_up"] is True and s.context["window_start_day"] == 14*k

def test_catch_up_stops_once_the_window_filled():
    rule.catch_up = DcaCatchUp(0, filled_window_day=14*k, in_flight=False)
    assert rule.detect(bars_ending(day=14*k + 5)) is None

def test_window_open_day_after_fill_in_same_window_does_not_rebuy():  # Review Focus 4
    rule.catch_up = DcaCatchUp(0, filled_window_day=14*k, in_flight=False)
    assert rule.detect(bars_ending(day=14*k)) is None

def test_in_flight_order_blocks_catch_up(): ...
def test_rule_promoted_mid_window_waits_for_next_window():  # Review Focus 3
    rule.catch_up = DcaCatchUp(since_day=14*k + 4, filled_window_day=None, in_flight=False)
    assert rule.detect(bars_ending(day=14*k + 6)) is None
    assert rule.detect(bars_ending(day=14*(k+1))) is not None
def test_next_window_open_day_is_a_normal_buy(): ...
```

`tests/test_agent.py`:

```python
def test_vetoed_dca_window_buys_on_next_cycle_then_not_again():  # Review Focus 2
    # cycle 1 (window-open): broker.withdrawals_raises -> vetoed, no order
    # cycle 2 (next day): venue readable -> exactly one filled BUY for the rule
    # cycle 3 (day after): no new order
    assert [o["rule_id"] for o in repo.get_orders(mode="live", status="filled")] == [rule_id]

def test_catch_up_day_counts_as_dca_day_for_sleeve_exclusion(): ...  # _dca_fires_today True
```

Run red, implement, run green (`pytest tests/strategy tests/test_agent.py tests/sim -q`; the sim
tests prove backtests are unchanged), `mypy`.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the decision above and the agent wiring (both live and paper agent paths; the ledger is filtered by the cycle's own `mode`).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(dca): retry a skipped buy daily until the rule's next window (#1010)`.

---

### Task 5: macOS notification for every skipped live DCA buy

**Files:**
- Create: `packages/keel-core/keel_core/macos_notify.py`
- Modify: `keel/agent.py` (collect `skipped_buys` on the cycle result; post after `notify_after_cycle`)
- Test: `tests/core/test_macos_notify.py`, `tests/test_agent.py`

**Interfaces:**
- Produces:
  - `keel_core.macos_notify.post(title: str, message: str, *, runner: Callable[[list[str]], Any] | None = None) -> bool` — runs `["/usr/bin/osascript", "-e", script]` with a 10 s timeout; `False` (never raises) on non-darwin, `KEEL_DISABLE_MACOS_NOTIFY` set, `PYTEST_CURRENT_TEST` set without an injected `runner`, or any error. Escapes `"` and `\` in both strings.
  - `@dataclass(frozen=True) class SkippedBuy: rule_id: int; product: str; reason: str` and `CycleResult.skipped_buys: list[SkippedBuy]`.
  - `agent._post_skipped_buys(repo, skipped: list[SkippedBuy], now_ts: int, *, post=macos_notify.post) -> int` — posts at most once per `rule_id` per UTC day, remembered in `agent_state` key `dca_skip_notified:<rule_id>` = the UTC date string; returns the count posted.

A skipped buy is a DCA setup this cycle (window-open or catch-up) on a non-offline cycle whose
`ExecutionResult.placed` is `False`. `reason` = `"; ".join(vetoed_by)` or `result.reason`. A DCA rule that
was due but produced no signal because entries were withheld (`entries_allowed` false / stale
feed / not ready) is also a skip with that reason. Notification text:
title `keel-live: DCA buy skipped`, message `<PRODUCT> (rule <id>): <reason, first 180 chars>. Retrying daily until <next window date>.`

- [ ] **Step 1: Write the failing tests.**

```python
def test_post_is_noop_off_darwin(monkeypatch): monkeypatch.setattr(sys, "platform", "linux"); assert post("t", "m", runner=boom) is False
def test_post_escapes_quotes(): calls=[]; post('a"b', 'c\\d', runner=calls.append); assert '\\"' in calls[0][2]
def test_post_swallows_runner_errors(): assert post("t", "m", runner=raises) is False

def test_skipped_live_dca_buy_posts_once_per_rule_per_day():  # Review Focus 5
    posted = []
    _post_skipped_buys(repo, [SkippedBuy(9, "PAXG-USD", "withdrawal_capability: UNKNOWN (...)")] * 2, NOW, post=lambda t, m: posted.append(m) or True)
    _post_skipped_buys(repo, [SkippedBuy(9, "PAXG-USD", "...")], NOW + 3600, post=...)
    assert len(posted) == 1
    _post_skipped_buys(repo, [SkippedBuy(9, "PAXG-USD", "...")], NOW + 86400, post=...)
    assert len(posted) == 2

def test_cycle_with_vetoed_dca_records_skipped_buy(): ...  # run_once, live, venue unreadable
def test_paper_cycle_records_no_skipped_buys(): ...
def test_notifier_failure_does_not_fail_cycle(): ...
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` PASS; `mypy`; `ruff check`.
- [ ] **Step 5: Commit** — `feat(alerts): macOS notification for every skipped live DCA buy (#1010)`.

---

### Task 6: Docs, planner note, release notes

**Files:**
- Rewrite: `docs/rails/rail-17-withdrawal-attestation.md` → `docs/rails/rail-17-withdrawal-capability.md` (fix every inbound link: `grep -rn rail-17-withdrawal-attestation`)
- Modify: `docs/operator-runbook.md` (delete the weekly-attest item; add "a macOS notification says a DCA buy was skipped: read the reason; it retries daily"), `docs/go-live-runbook.md`, `docs/glossary.md`, `docs/fiqh-basis.md` (mechanism only; the §65.4 basis text stays)
- Modify: `keel/commands/dca_plan.py` output: one line stating catch-up can add up to one buy per rule to a calendar month and rail 14 still bounds it
- Modify: `CHANGELOG.md` (Unreleased: Changed/Removed/Added entries)
- Test: `tests/commands/test_dca_plan.py` (the new line), `tests/test_fiqh_basis.py` and any doc tests that pin rail-17 wording

- [ ] **Step 1: Failing test** for the `dca-plan` line; update doc-pinning tests to the new wording.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Write the docs and the line.**
- [ ] **Step 4: Run** full `pytest -q`, `mypy`, `ruff check`, `ruff format --check`.
- [ ] **Step 5: Commit** — `docs(rail-17): venue-read withdrawal capability, DCA catch-up, skip alerts (#1010)`; open the PR with `Closes #1010`.

## Deploy note (not a task)

After release, deploy to `~/keel` as usual and confirm `keel versions` prints `[release]`. The
first live cycle after deploy will catch up every rule whose current window is unfilled. If it
lands before the 2026-11-12 bar (weekly) / 2026-11-19 bar (biweekly), that is all seven rules (≈ $40 + $25 + $25 + 4×$15 = $150), subject to
rails 13 and 14.
