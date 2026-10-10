# Console — four engine views (Pulse · Holdings · Rules · Audit & Setup) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Never ask the user anything; every open question in this initiative is settled in a "Rulings" list below.

**Goal:** Replace the console's thirteen screens with four engine views -- PULSE (home, with the HALT button), HOLDINGS, RULES, AUDIT & SETUP -- shipped as four sequential PRs, each leaving the console fully working and the suite green. The ledger's "Done means" (`.superpowers/orchestrate/2026-10-10-console-four-views/ledger.md`) is the acceptance list; the screen inventory with line numbers is `inventory.md` beside it. Line numbers below are at BASE_SHA `9b1db8755cf7ac6a65cb118c615dc8e630998143`.

**Architecture:** One server endpoint per view, composed in `keel/web/api.py` from the EXISTING `keel.commands.*` services (`gather_status`, `feed_from_lines`/`apply_scope`, `gather_positions`, `gather_orders`, `build_insights_report`, `gather_timeline`, `existing_dca_rules`) and serialised by `keel/web/payload.py` under its existing rules (every figure a `Field`, no `len()`, no arithmetic, judgements made in Python). Two small new service modules carry the two compositions that are genuinely new judgements -- `keel/commands/pulse.py` (today's counts, "why keel didn't trade", next DCA cadence day, unbracketed tranches) and `keel/commands/holdings.py` (tranche ↔ entry-order join, per-tranche cost). The client stays four files that matter: `main.js` (routes, mount, listeners), `render.js` (views, no arithmetic, no `.value`), `api.js` (the one `fetch`), `actions.js` (the write boundary; gains the gate token beside the CSRF token). The halt reuses `POST /api/gates/halt` exactly as `server._gated_action` already serves it; nothing in `keel/web/gates.py`, `security.py` or `server.py`'s write path changes.

**Tech Stack:** Python 3.14, `http.server`, sqlite3, `Decimal`; vanilla ES modules, no bundler, no JS test runner (JS is checked by `node --check` and by the Python source scans in `tests/web/`); pytest, ruff, mypy, uv.

**Spec:** the ledger and inventory named above, plus the design the user approved (quoted in the orchestrator brief). Do not relitigate it.

---

## Global constraints (every PR)

- **TDD.** Every task starts with a failing test, run to see it fail for the stated reason, then the code, then green. Client tests assert STRUCTURE (counts, pairings, parsed function bodies via `tests/web/test_client_assets.py::_function_body`/`_code_only`/`_comments_only`, `tests/web/test_view_sections.py::_decl`), never a bare substring that a declaration alone satisfies.
- **Suite, per PR, from the worktree root** (the exact CI invocations, `.github/workflows/ci.yml`):
  - `uv run ruff check keel tests packages`
  - `uv run ruff format --check .`
  - `uv run mypy` -- BARE. Never `mypy keel/` (`[tool.mypy] files` in pyproject.toml is the scope).
  - `uv run pytest -q`
  - `uv run keel --version` (the build-identity step)
  - If a PR touches `packages/` (none of these four does): `PYTHONPATH="$PWD:$PWD/packages/keel-core:$PWD/packages/keel-broker-api:$PWD/packages/keel-broker-coinbase" uv run python -m pytest -q`.
- **JS is never parsed by CI.** Every PR's verification runs `node --check` on EVERY touched file under `keel/web/static/js/` and on `keel/web/static/sw.js`, and loads EVERY route in a real browser (see "Browser check" below).
- **Browser check** (per PR, against a throwaway copy, never `~/keel`): `mkdir -p $SCRATCH/console && cp config.yaml keel.db $SCRATCH/console/` (the checkout's paper deployment), then `uv run keel --config $SCRATCH/console/config.yaml --db $SCRATCH/console/keel.db serve`, open the printed tokened URL. **The service worker cache name is `keel-shell-${BUILD}`** (`sw.js:54`), where `BUILD` is the `?v=` the page registers with (`keel.version.describe()`, `main.js:1618`). That string carries the commit only when the build knows it (`full_version`); the browser check on PR1 saw `keel-shell-0.21.0`, i.e. the VERSION alone, so a new commit at an unchanged version is NOT a new cache name and an installed console can keep serving the old shell (whose `main.js` asks for endpoints this PR removed) until the version is bumped at release. Do not rely on the cache name changing between PRs. The first load of every PR is therefore done after DevTools → Application → Service Workers → Unregister, then a hard reload, because the OLD worker serves the old shell until the new one is activated. Then visit every route in `staticfiles.CLIENT_ROUTES`, the `/` default, and a deep-link reload of each. Check the console tab for a thrown error (a view that throws leaves `aria-busy` down and the page dimmed -- `main.js:620`).
- **Worktrees only.** Each PR is built in its own `git worktree` off `origin/main`. Never touch the main checkout or `~/keel`.
- **Docstring rules travel with the code.** Before editing a module, read its module docstring and the sibling docstrings; preserve the rules they record (`keel/web/__init__.py`'s closed write surface; `gates.py` "stopping must never be slower than starting"; `security.py` gates token vs setup token; `payload.py` Rules 1-6 and the per-builder notes; `api.py` "formats nothing", "one clock read per request"). New modules carry their own rule in their own docstring, in the same register.
- **Route removal is lockstep** (one commit): `main.js` `ROUTES` (:105) · `staticfiles.CLIENT_ROUTES` (:139) · `index.html` nav `<li>` list (:122-162) · `api.API_ROUTES` (:1031) · `tests/web/test_api.py::API_ROUTES` (:55) · `tests/web/test_server.py::ROUTES` (:40, the secret sweep) · `tests/web/test_client_assets.py::_VIEW_ENDPOINTS` (:1284), `_ROW_ENDPOINTS` (:1783), `_ROW_ENDPOINTS_UNCOVERED` (:1810) · the dead `render.js` view function and its helpers · payload builders used ONLY by the web (grep `keel/` first; builders the CLI also uses stay) · their tests. `test_the_mount_prefix_is_spelled_the_same_everywhere` (:752) pins that the nav hrefs equal `CLIENT_ROUTES` IN ORDER, so the nav and the two tables are edited together.
- **CLI commands are untouched.** `gather_*` services keep their signatures. The two new service modules are additive. `keel/strategy/rules/dca.py` gains two pure module-level functions and `Dca.detect` calls one of them (same expression, one home).
- **No CHANGELOG.** The repo keeps none (`ls CHANGELOG*` is empty; `docs/RELEASING.md` is the release process). Each PR instead updates the docs that describe the screens: `docs/operator-runbook.md:837-841` ("The operator console, in a browser"), `README.md:247` (the `web/` tree line), `docs/architecture.md:21` (one line; only PR4 touches it), and `keel/web/static/js/docs.js::TERMS` (labels that link to the glossary -- `tests/web/test_doc_links.py:150` fails if a `TERMS` label is no longer a `kv("…")` label in `render.js`).
- **Commit trailer.** Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Every PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Global chrome is untouched in every PR**: `#engine` banner, `#mode-banner`, `#attest-banner`, session chip, deployment card, console switcher, theme toggle, footer build line (`render.js:3843-4128`, `index.html` header/footer). Their tests (`test_attestation_banner.py`, `test_deployment_card.py`, `test_profile_switcher.py`, `test_client_assets.py:2140-2260`) must stay green without edits.

## Facts the plan relies on (verified at BASE_SHA)

- The halt route: `server.py:854-859` -- `POST /api/gates/halt` needs admission (session cookie + Host), `X-Keel-Client: 1`, and `X-Keel-Gate == security.gates_token(cfg.token)`. No locality check, no phrase, no body read. `tests/web/test_server.py:1821` pins that it works on a `--external-host` deployment; `:1856` pins the token is still required. The halt is therefore the one write that is MEANT to be reachable wherever the console is served.
- The CSRF precedent: `api.read_setup` (:309-331) puts `csrf_token(cfg.token)` in `/api/setup`'s body as a bare string; `payload.setup_payload` (:3537) records the argument ("this token is not a credential on its own"); `tests/web/test_api.py:840` pins it appears on `/api/setup` and no other route; `actions.js:43-70` holds it (`let token`) and `api.js:296` sends it as `X-Keel-CSRF`; `tests/web/test_client_assets.py:563` pins "held in actions.js, sent by api.js, named nowhere else".
- `render.js` may never read `Field.value` nor compare (`test_render_never_judges_a_value_itself`, :324). Branching on the PRESENCE of a payload string is the accepted pattern (`cancelCell` :2313, `activityView` :2442). So "HALT button vs HALTED line" must arrive as structure: the payload sends `halt.resume_command` as `"keel resume"` when the kill switch is engaged and `""` otherwise.
- `payload.py` bans `len()` (Rule 6e); every count comes off a report. `api.py` formats nothing and reads the clock once (`tests/web/test_api.py:1533-1575`; the reader scan asserts `len(scanned) >= len(dispatched) >= 15` -- the route count falls below 15 in PR3, see its Task 1).
- `positions` rows carry NO entry `order_id` (`repository.py:1289-1339`); the agent opens the tranche with `opened_at=now_ts` (`agent.py:507-510`) from the same cycle clock the entry order was written with (`executor.py:1298` → `_order_row(..., now_ts, ...)`, `:2146 created_at=now_ts`); paper writes both with `signal.ts` (`paper.py:350-365`). So `(product_id, side == "buy", orders.created_at == positions.opened_at)` identifies the entry order deterministically; a tranche with no such order (hand-edited ledger, pre-#803 rows) is reported as "entry order not identified".
- The DCA cadence lives in ONE expression: `Dca.detect` (`keel/strategy/rules/dca.py:111`) `(latest.ts // 86400) % cadence_days != 0` over COMPLETED daily bars; `agent.py:375` and `dca_plan.py:458,473` cite it. `existing_dca_rules(repo)` (`dca_plan.py:233`) lists non-disabled `dca` rows with `cadence_days`, `budget_usd`, `status`.
- Today's cycles: `activity.feed_from_lines` + `apply_scope(feed, "today", now_ts=…)` (`api.py:333-358`); `ActivityCycle.blocked` already includes rail vetoes (#812) and `highlights` carries the sentences `"rail veto: <rail>"`, `"not placed: <reason>"`, `"gate rejected: …"`, `"entries withheld (N blocked)"`, `"cycle skipped: …"`, `"stale feed: …"` (`activity.py:635-754`). `ActivityFeed.last_cycle_before_scope` carries the last cycle when today is empty.
- Timeline: `TIMELINE_KINDS = ("trade","flow","attestation","system")` (`timeline.py:52`); discretionary journal rows are `kind="attestation"` (`:430`) so they survive a keel-only filter; system rows' summary (`:493`) omits `blocked`; `gather_timeline` collapses an unknown `kind` to `""` (`:536`). The CLI `keel timeline` and the CSV export both go through `gather_timeline`/`export_rows`.
- `keel/commands/plans.py` has no click command and `gather_plans` is called only by `api.read_plans`; `tests/commands/test_plans.py` (17 tests) tests it. `build_equity_series`/`build_equity_curve` are called only by `api.py`; `build_journal_report`/`gather_journal` are also CLI (`insights.py:1154`, `journal.py:361`).
- `docs.js::TERMS` labels that must keep existing as `kv(...)` labels: `autonomy`, `autonomy configured`, `autonomy lapses`, `kill switch`, `rail 11`, `withdrawal attestation (rail 17)`, `market session` (all from `statusState` → move to Pulse), `session` (from `venueCard` :3562 → removed in PR4 with the entry), `paper stage` (setup :1042, stays).

## Global rulings

1. **Gate token on `/api/pulse`, on every deployment including remote** — what: `read_pulse` adds `gate=security.gates_token(cfg.token)` to the pulse body as a bare string, scoped to that one endpoint (a parallel of `test_setup_carries_the_csrf_token_and_only_setup_does`) — why: it is the same posture as the CSRF token (`setup_payload`'s docstring: not a credential without the session cookie; `/api/*` is `no-store`; the worker never caches it), the halt route is deliberately reachable from a tunnel (`test_server.py:1821`), and the gate token's OTHER uses (resume routes) are refused on a remote deployment by `gated_action_permitted` and need a typed phrase locally, so exposing it widens nothing — cost if wrong: a session-holder on a remote deployment can halt keel from the console, which is the feature; they can already do it with the session cookie and one `curl`.
2. **Halt confirm is `window.confirm`** — what: one native confirm dialog in `main.js` with the exact text `Halt keel? Resuming requires \`keel resume\` at a terminal.`; no `<dialog>`, no slide/hold — why: `gates.py` "stopping must never be slower than starting"; a native confirm is keyboard-accessible, needs no CSS and cannot be left stale across a repaint — cost if wrong: a cosmetic mismatch with the cancel-help `<dialog>` styling.
3. **No browser resume, structurally** — what: the client never names `gates/resume`, `RESUME TRADING` or `CLEAR STREAK HALT`; the HALTED line names `keel resume` from the payload — why: the brief; the Tier-1 release ceremony remains CLI-only until #436's D3 lands — cost if wrong: none (adding it later is a route the server already serves).
4. **The Gates view goes in PR1** — what: `/gates`, `/api/gates`, `gatesView`, `gates_payload`, `_capability_payload` and their pins are deleted in PR1 — why: it advertises browser buttons that do not exist AND its one true sentence ("This view cannot perform any of them") becomes false the moment Pulse has HALT; `keel.capabilities` (the declaration) is untouched — cost if wrong: an operator loses a read-only table of CLI ceremonies that `keel --help` already lists.
5. **Subscriptions (rail 14) stay on Pulse, compact** — what: one small table (venue, tier, effective status, effective cap) under the freshness table — why: rail 14 caps BUYS, so a lapsed subscription is a "why keel didn't trade" fact that belongs on the glance page, not the audit page — cost if wrong: one table too many on Pulse, moved to Audit in a follow-up.
6. **Pulse is not sectioned** — what: a single scroll of cards and short tables; `sectioned()` serves Setup only (and Audit's setup section after PR4) — why: a glance page with tabs hides the thing you came to glance at — cost if wrong: a long page on a phone.
7. **Pulse carries open positions compactly** (product, rule, qty, entry, bracket) in PR1 — why: Status is removed in PR1 and the bracket warning (#641) must keep a home until Holdings exists; Holdings takes the detail in PR2 and the Pulse table stays as the bracket roll-call — cost if wrong: a few rows duplicated between Pulse and Holdings.
8. **Next DCA buy = next cadence DAY, not a predicted order time** — what: Pulse shows per LIVE `dca` rule the next UTC day `d` with `d % cadence_days == 0` (today if today is one), with the fixed note "placed by the first cycle after that day's daily bar closes" — why: the engine keys the buy off the COMPLETED bar's day (`Dca.detect`, `agent.py:375`); restating when the launchd cycle runs would be the drift the rule forbids and the server does not know the schedule (ledger ruling) — cost if wrong: an operator reads the cadence day as the order day and is one day early.
9. **Research stays four endpoints, retargeted to `/rules`** — what: `/api/research/{trials,gauntlet,slippage,matrix}` keep their readers, `needs_database=False`, empty sort surface and tests; only `html_route` and the view change — why: the Strathern rail is pinned per endpoint (`test_research_view.py:162-215, 588, 801, 882`) and the ledger is a FILE that answers on a machine with no deployment; folding them into `/api/rules` (which needs a database) would lose both — cost if wrong: the Rules view reads six endpoints concurrently (loopback; the server is threaded; `/research` already reads four).
10. **Track records are not sortable on Rules** — what: `/api/rules` keeps `rules` as its one sortable collection and carries `records` (the former `/api/insights` rules + gate distances) unsorted — why: one sortable collection per endpoint (`api.py:682`), and a track-record table sorted by expectancy is the leaderboard the research rail refuses one card down — cost if wrong: an operator cannot sort track records by win rate in the browser (`keel insights` can).
11. **Charts and the journal leave the console** — what: PR3 deletes `chart.js`, the equity-series chart, the closed-trade curve, the closed-trades table, the discretionary-journal section, `main.js`'s #602 zoom/pan/export block, the `.chart*` CSS, `/api/insights`, `/api/journal`, and the payload builders only they used (`insights_payload`, `_account_payload`, `journal_payload`, `_journal_entry_payload`, `equity_curve_payload`, `_equity_point_payload`, `equity_series_payload`, `_equity_series_point_payload`, `_discretionary_journal_payload`, `_notes_window_display`, `_rules_followed_*`) — why: the account's drawdown state the rails act on is already on Pulse as the exact scalars; the discretionary journal is operator data and survives as Timeline `attestation` rows on Audit; the closed trades survive as Timeline `trade` rows and in `keel insights`; the chart stack is the largest body of client code (526 + ~450 lines) carrying no engine decision — cost if wrong: re-adding a chart is a self-contained module recoverable from git history; the services (`build_equity_series`, `build_equity_curve`, `build_journal_report`, `gather_journal`) and their `tests/commands/` tests are NOT deleted, so nothing has to be rebuilt server-side.
12. **Holdings' entry-order join is a deterministic match, not a schema change** — what: `keel/commands/holdings.py` matches each open tranche to the `orders` row with the same `product_id`, `side == "buy"` and `created_at == opened_at`, reading placement, expected and actual fill from it; no match → placement `unknown` with the note "entry order not identified", slippage absent — why: both rows are written with one cycle clock (facts above); a migration adding `positions.order_id` would be an engine change in a console PR and would not backfill the rows the operator holds today — cost if wrong: a tranche with two buys at the same second on the same product (impossible under rail 4/`_held_position` today) would show the first order's figures.
13. **No cancel affordance on Holdings** — what: the recent-orders section carries no cancel cell; `cancelCell`, `openCancelHelp`, `_cancel_payload` and the `cancel` row key are deleted; `classify_cancel` stays in the service for `keel orders cancel` — why: the dialog was an affordance to act and the four-view console is read + halt; the `keel orders` CLI prints the same classification — cost if wrong: an operator opens a terminal to learn the cancel command they were going to run in a terminal anyway.
14. **Audit's primary endpoint is `/api/setup`** — what: `ROUTES` entry `{ name: "audit", endpoints: ["setup", "timeline", "venues"] }`; the timeline and venue readings are secondary — why: the primary reading decides `data === null` → `stoppedView`, and `stoppedView` links to the setup route, which IS the audit route; a timeline primary on a fresh machine would loop a stopped page back to itself — cost if wrong: the engine banner on Audit reflects `/api/setup`'s reading (which `respond` builds from the same `DeploymentState` as every other route).
15. **Timeline's keel-only default is a client-seeded server filter `kind=keel`** — what: `gather_timeline` learns one composite kind word `keel` = `{system, attestation}` (echoed back as `keel`, listed first in the chip bar); `main.js` seeds `paramsFor("timeline").kind = "keel"` at boot; the CSV export passes the same `?kind=` and resolves it the same way — why: the CSV must keep matching the screen (`api.py:617`), the CLI `keel timeline` default (`kind=""`, every kind) must not move, and a client-side filter over a capped page is the thing `timelineView`'s comment refuses — cost if wrong: one extra word in `TIMELINE_KINDS`' vocabulary.
16. **`/api/venues` is kept whole; Audit renders only `readiness`** — why: `_venue_payload` is still emitted, so it is not dead, and the declarations remain in `keel brokers list` — cost if wrong: ~60 lines of payload nobody renders, deletable later with `test_api.py:763/780` and `test_payload.py:1240-1300`.
17. **`keel/commands/plans.py` and `tests/commands/test_plans.py` are deleted in PR4** — why: no CLI command calls `gather_plans`; its only consumer is `/api/plans` — cost if wrong: the doc-traceability pin on `docs/evolution-plan.md`'s tier wording goes with it; the document itself is untouched.
18. **`test_api.py`'s reader-count floor drops from 15 to 10 in PR3** — why: it is a believability floor on the scan, not a policy; the route count is 17 after PR1, 16 after PR2, 14 after PR3, 12 after PR4 — cost if wrong: none; the scan is still cross-checked against `API_ROUTES`.

---

## PR1 — Pulse + Halt

**Branch:** `feat/console-pulse` · **PR title:** `console: Pulse home view with HALT; Status, Activity and Gates views retired (#13 PR1)`

**Scope in one line:** new `/api/pulse` + `pulseView` as `ROUTES[0]` and the `/` default, carrying kill switch / autonomy / lapses / rail 11 / drawdown / rail 17 / market session / freshness / subscriptions / today's cycles with highlights and detail / "why keel didn't trade" / last cycle time / next DCA cadence day per live rule / open positions with the bracket warning / the gate token; a HALT button → `POST /api/gates/halt`; `/status`, `/activity`, `/gates` and `/api/status`, `/api/activity`, `/api/gates` removed in lockstep.

**Files touched:**
- new: `keel/commands/pulse.py`, `tests/commands/test_pulse.py`, `tests/strategy/test_dca_cadence.py`, `tests/web/test_pulse_view.py`, `docs/superpowers/plans/2026-10-10-console-four-views.md` (a copy of this file)
- edit: `keel/strategy/rules/dca.py`, `keel/web/payload.py`, `keel/web/api.py`, `keel/web/staticfiles.py`, `keel/web/static/index.html`, `keel/web/static/manifest.webmanifest`, `keel/web/static/js/main.js`, `render.js`, `api.js`, `actions.js`, `keel/web/static/css/keel.css`, `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_client_assets.py`, `tests/web/test_view_sections.py`, `tests/web/test_payload.py`, `tests/web/test_pwa.py`, `docs/operator-runbook.md`, `README.md`
- Nav after PR1 (this order in all three tables): `pulse, setup, orders, positions, balances, timeline, insights, research, rules, venues, plans` + Docs.

### Task 1: One home for the DCA cadence expression

**Files:** `keel/strategy/rules/dca.py`, `tests/strategy/test_dca_cadence.py` (new)

- [ ] Step 1: Write the failing tests in `tests/strategy/test_dca_cadence.py`:
  - `test_is_cadence_day_is_the_expression_detect_keys_off`: for `cadence_days in (1, 7, 30)` and `ts` in a spread of day indices × 86400 (+ an intra-day offset), `is_cadence_day(ts, cadence)` is `True` exactly when `(ts // 86400) % cadence == 0`.
  - `test_detect_fires_exactly_when_is_cadence_day_says_so`: build `Dca("BTC-USD", cadence_days=7)`, feed `completed_days`-shaped daily candles ending on day `d` for `d in range(98, 113)` (use the candle helpers `tests/strategy/test_dca.py` already uses), assert `rule.detect(...) is not None` ⇔ `is_cadence_day(latest.ts, 7)`.
  - `test_next_cadence_day_ts_is_today_on_a_cadence_day_and_the_next_boundary_otherwise`: `next_cadence_day_ts(105 * 86400 + 3600, 7) == 105 * 86400`; `next_cadence_day_ts(100 * 86400, 7) == 105 * 86400`; `next_cadence_day_ts(ts, 1) == (ts // 86400) * 86400`.
  - `test_the_helpers_refuse_a_non_positive_cadence`: `ValueError` for `cadence_days <= 0` (mirrors `Dca.__init__`).
- [ ] Step 2: Run `uv run pytest tests/strategy/test_dca_cadence.py -q`; fails with `ImportError: cannot import name 'is_cadence_day'`.
- [ ] Step 3: In `dca.py` add, above the class, with a docstring naming `agent.py:375` and `dca_plan.py:458/473` as the readers that cite this expression:
  ```python
  def is_cadence_day(ts: int, cadence_days: int) -> bool:
      """Whether the daily bar stamped `ts` is a cadence boundary. THE expression `Dca.detect`
      keys the buy off (`(ts // 86400) % cadence_days == 0`), in one place so a console that
      says "next buy" and the engine that places it cannot drift."""
      if cadence_days <= 0:
          raise ValueError("cadence_days must be positive")
      return (ts // _SECONDS_PER_DAY) % cadence_days == 0

  def next_cadence_day_ts(now_ts: int, cadence_days: int) -> int:
      """00:00 UTC of the first cadence day on or after `now_ts`'s UTC day. A cadence DAY, not
      an order time: the buy is placed by the first cycle that sees that day's bar COMPLETED."""
      if cadence_days <= 0:
          raise ValueError("cadence_days must be positive")
      day = now_ts // _SECONDS_PER_DAY
      remainder = day % cadence_days
      return (day if remainder == 0 else day + cadence_days - remainder) * _SECONDS_PER_DAY
  ```
  and change `Dca.detect`'s check to `if not is_cadence_day(latest.ts, cadence_days): return None` (the comment `# off-cadence: no buy today` stays).
- [ ] Step 4: Run the new file and `uv run pytest tests/strategy -q tests/test_agent.py -q`; green.
- [ ] Step 5: Commit: `dca: name the cadence expression once (is_cadence_day, next_cadence_day_ts)`.

### Task 2: The Pulse service — `keel/commands/pulse.py`

**Files:** `keel/commands/pulse.py` (new), `tests/commands/test_pulse.py` (new)

The module docstring states its rule: *"A VIEW over `StatusReport`, `ActivityFeed` and `existing_dca_rules`; it reads nothing the CLI cannot and computes only what no existing report holds (today's sums, the why-not list, the next cadence day, the unbracketed roll-call). The halt is not here: `keel/web/gates.py` owns it."* It imports `is_cadence_day`/`next_cadence_day_ts` from `keel.strategy.rules.dca` (the commands layer may; `dca_plan.py:53` already imports `Dca`).

Shapes:
```python
@dataclass(frozen=True)
class TodayCounts:
    cycles: int; signals: int; blocked: int; entered: int; exited: int; errors: int

@dataclass(frozen=True)
class NextDcaBuy:
    rule_id: int; product_id: str; cadence_days: int; budget_usd: Decimal
    next_cadence_day_ts: int   # 00:00 UTC of the cadence day
    budget_inferred: bool      # ExistingDcaRule.budget_inferred, carried through

@dataclass(frozen=True)
class PulseReport:
    now_ts: int
    status: StatusReport
    feed: ActivityFeed                     # already scoped to "today"
    today: TodayCounts
    last_cycle_ts: float | None            # newest today, else last_cycle_before_scope, else None
    why_not: tuple[str, ...]               # ordered, de-duplicated
    next_dca: tuple[NextDcaBuy, ...]       # LIVE dca rules only, rule-id order
    unbracketed: tuple[OpenPositionStatus, ...]   # live-mode tranches with has_bracket False
    unbracketed_count: int                 # len taken HERE, never in payload.py
    resume_command: str                    # RESUME_COMMAND when status.kill_switch_engaged else ""

RESUME_COMMAND = "keel resume"
_WHY_PREFIXES = ("rail veto: ", "not placed: ", "gate rejected: ", "entries withheld", "cycle skipped: ", "stale feed: ")

def gather_pulse(repo: Repository, config: Config, feed: ActivityFeed, *, now_ts: int) -> PulseReport
```
`why_not` order: (1) `KILL_ENGAGED_LINE` (imported from `keel.commands.trading`, never restated) when the kill switch is engaged; (2) `f"rail 11: {status.rail11_status}"` when `rail11_status == "HALTED"`; (3) `f"rail 17: withdrawal attestation {state}"` when `status.mode == "live"` and `withdrawal_attestation.state in {"expired", "unattested"}`; (4) every `highlight` of today's cycles (newest cycle first) that starts with one of `_WHY_PREFIXES`, first occurrence kept. `today` sums over `feed.cycles`. `last_cycle_ts` = `max(c.started_ts for c in feed.cycles)` else `feed.last_cycle_before_scope.started_ts` else `None`. `next_dca` = `existing_dca_rules(repo)` filtered to `status == "live"`, each with `next_cadence_day_ts(now_ts, cadence_days)`. `unbracketed` = `[p for p in status.open_positions if not p.has_bracket]` when `status.mode != "paper"` else `()` (the judgement `_bracket_field` already makes: paper never has a bracket by construction).

- [ ] Step 1: Write `tests/commands/test_pulse.py` (build `StatusReport`s with the helper `tests/web/test_payload.py::_status_report` already has -- import it, or lift a copy into `tests/commands/conftest.py`; build feeds with `feed_from_lines` over literal JSON log lines like `tests/commands/test_activity.py` does, then `apply_scope(feed, "today", now_ts=NOW)`):
  - `test_today_counts_sum_the_scoped_cycles_and_nothing_outside_them`
  - `test_last_cycle_is_the_newest_today_or_the_one_before_the_scope_or_none` (three cases)
  - `test_why_not_lists_the_standing_blockers_before_the_cycle_reasons_in_order` (kill switch + rail 11 HALTED + a `guards.check_failed` line + an `enter_evaluated placed=false reason=…` line → exact tuple)
  - `test_why_not_dedupes_a_reason_two_cycles_repeat`
  - `test_why_not_omits_rail_17_in_paper_and_names_it_in_live`
  - `test_next_dca_lists_live_rules_only_with_their_next_cadence_day` (insert via `tests/commands/test_dca_plan.py::_insert_dca` with statuses live/candidate/disabled; `now_ts = 100 * 86400` → `105 * 86400` for cadence 7)
  - `test_unbracketed_is_the_live_tranches_without_a_bracket_and_empty_in_paper`
  - `test_resume_command_is_the_cli_s_own_and_only_when_engaged`
  - `test_the_service_reads_no_clock` (AST scan of the module for `time.time`/`datetime.now`, the shape `tests/web/test_api.py:1497` uses).
- [ ] Step 2: Run; fails with `ModuleNotFoundError: keel.commands.pulse`.
- [ ] Step 3: Implement the module as specified. Pure functions, no `click`.
- [ ] Step 4: Run `uv run pytest tests/commands/test_pulse.py -q` and `uv run pytest tests/commands/test_console_thinness.py -q` (the thinness pin scans `keel/web/*` only, but confirm it is still green); `uv run mypy`.
- [ ] Step 5: Commit: `commands: pulse service (today, why-not, next DCA cadence day, unbracketed)`.

### Task 3: `payload.pulse_payload`

**Files:** `keel/web/payload.py`, `tests/web/test_payload.py`

Shape (every `Field` built by the existing helpers; the status helpers `_autonomy_payload`, `_attestation_payload`, `_session_payload`, `_position_payload` (with its `_bracket_field`), `_rule_payload`, `_freshness_payload`, `_subscription_payload` are REUSED, not copied; `status_payload` itself is deleted in Task 4):
```python
def pulse_payload(report: PulseReport, *, gate: str = "") -> dict[str, Any]:
    status = report.status
    session_defused = bool(status.market_session.defused)
    return {
        "as_of": iso(report.now_ts), "generated_at": moment(report.now_ts), "mode": status.mode,
        "gate": str(gate),                               # bare string, like setup.csrf -- sent, never displayed
        "kill_switch": flag(status.kill_switch_engaged, on="ENGAGED", off="clear", on_state=BAD, off_state=GOOD),
        "halt": {"resume_command": report.resume_command},   # "" unless engaged; presence is the client's branch
        "autonomy": _autonomy_payload(status.autonomy),
        "drawdown": {... as status_payload ...},  "equity": {... as status_payload ...},
        "withdrawal_attestation": _attestation_payload(status.withdrawal_attestation),
        "market_session": _session_payload(status.market_session),
        "last_cycle_at": moment(report.last_cycle_ts),
        "today": {"cycles": count(...), "signals": count(...), "blocked": count(..., state=WARN if blocked else NEUTRAL),
                  "entered": count(...), "exited": count(...), "errors": count(..., state=BAD if errors else NEUTRAL)},
        "why_not": list(report.why_not),
        "next_dca": [{"rule_id": str(b.rule_id), "product_id": b.product_id, "cadence": _dca_cadence(b.cadence_days),
                      "budget": money(b.budget_usd), "budget_inferred": flag(b.budget_inferred, on="inferred — no budget_usd stored", off="stored", on_state=WARN, off_state=NEUTRAL),
                      "next_cadence_day": moment(b.next_cadence_day_ts)} for b in report.next_dca],
        "next_dca_note": NEXT_DCA_NOTE,                  # module constant: "placed by the first cycle after that day's daily bar closes"
        "open_positions": [_position_payload(p, status.mode) for p in status.open_positions],
        "unbracketed_count": count(report.unbracketed_count, state=WARN if report.unbracketed_count else NEUTRAL),
        "rule_counts": [...], "live_rules": [...], "data_freshness": [...], "subscriptions": [...],   # as status_payload
        "activity": activity_payload(report.feed),      # cycles with highlights/events, status_note, scope_fully_covered, last_cycle_before_scope
    }
```
- [ ] Step 1: Tests in `tests/web/test_payload.py` (reuse `_status_report`, `_activity_feed`; build a `PulseReport` directly):
  - `test_the_pulse_payload_carries_no_json_number` (walk with `_json_numbers`)
  - `test_the_halt_block_names_the_resume_command_only_when_engaged` (`halt.resume_command == "keel resume"` ⇔ `kill_switch.value == "true"`; the string comes from `pulse.RESUME_COMMAND`, assert equality against the constant, not a literal)
  - `test_the_gate_is_a_bare_string_and_never_a_field`
  - `test_why_not_crosses_as_a_list_of_sentences_unchanged`
  - `test_a_blocked_or_erroring_day_is_judged_here` (states on `today.blocked`/`today.errors`)
  - `test_next_dca_rows_carry_the_cadence_day_as_a_moment_and_the_note_once`
  - `test_the_pulse_payload_survives_an_empty_report` (extend `test_every_builder_survives_a_completely_empty_report` :1307 with `"pulse": payload.pulse_payload(...)`), and add `"pulse_payload"` to the parametrised `test_every_payload_is_json_serialisable_without_a_custom_encoder` (:1355) with its required `PulseReport` argument spelled out like `insights_payload`'s.
  - Retarget the existing status pins that must survive: `test_a_kill_switch_carries_its_own_judgement` (:1037), the bracket tests (:733-790), `test_absence_is_unknown_and_never_a_zero` (:1026) and `test_an_unknown_rail_state_is_unknown_and_not_quietly_good` (:1048) -- wherever they call `status_payload(...)`, call `pulse_payload(_pulse_report(status=...))` and read the same keys.
- [ ] Step 2: Run; fails with `AttributeError: pulse_payload`.
- [ ] Step 3: Implement `pulse_payload` + `NEXT_DCA_NOTE` beside `status_payload` (section header `# -- pulse (console PR1) --`). Keep `status_payload` for now (Task 4 deletes it with its route).
- [ ] Step 4: Run `uv run pytest tests/web/test_payload.py tests/commands/test_console_thinness.py -q`; green (Rule 6e: no `len()` -- `unbracketed_count` and `today.*` come off the report).
- [ ] Step 5: Commit: `web: pulse_payload`.

### Task 4: `/api/pulse` in the route table; `/api/status`, `/api/activity`, `/api/gates` out

**Files:** `keel/web/api.py`, `keel/web/payload.py`, `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_client_assets.py` (the `/api/status` parity test only), `tests/web/test_payload.py`

- [ ] Step 1: Tests first, in `tests/web/test_api.py`:
  - `API_ROUTES` (:55): replace `/api/status`, `/api/activity`, `/api/gates` with `/api/pulse` (first after `/api/config`). `test_this_module_pins_every_route_the_server_serves` now fails until the registry matches.
  - `test_pulse_carries_the_gate_token_and_only_pulse_does` (mirror of :840; loop over every other path in `API_ROUTES` and assert `security.gates_token(token) not in json.dumps(document)`); update :840's own loop (`/api/status` → `/api/pulse`, drop `/api/gates`).
  - `test_pulse_answers_data_null_when_nothing_is_set_up` (`empty_machine`, `engine.value == "stopped"`, `data is None`).
  - `test_pulse_reads_todays_cycles_from_the_configured_log` (write two JSON lines -- one today, one last week, shaped as in `test_read_activity_scopes_by_the_clock_it_was_handed` :1423 -- at `resolve_log_path(load_config(config_path))`; assert `data.today.cycles.value == "1"` and `data.activity.cycles` has one row).
  - `test_pulse_lists_the_next_cadence_day_for_live_dca_rules_only` (seed with `_insert_dca`).
  - `test_pulse_declares_no_sortable_collection` (`"/api/pulse" not in web_api.sortable_columns()`).
  - `test_a_stopped_engine_still_answers_the_endpoints_that_describe_the_binary` (:737): drop `/api/gates` from the tuple.
  - `test_the_api_routes_cover_every_html_route_that_reads` (:294): delete the `"/" if view == "/status"` special case; `/api/pulse` declares `html_route="/pulse"`.
  - The reader scan (:1533) still holds (17 routes ≥ 15).
- [ ] Step 2: `tests/web/test_server.py::ROUTES` (:40): `/api/status`, `/api/activity`, `/api/gates` → `/api/pulse`. `test_post_is_refused_everywhere_except_the_setup_actions` (:164): `"/gates"` → `"/pulse"` (still a 404 for POST -- `do_POST` only accepts `/api/gates/*` and `/api/setup/*`).
- [ ] Step 3: Run `uv run pytest tests/web/test_api.py tests/web/test_server.py -q`; the new tests fail with 404 on `/api/pulse`; the registry pin fails on the set difference.
- [ ] Step 4: Implement in `api.py`:
  ```python
  def read_pulse(cfg: ServeConfig, _query: Query, _state: Any, now_ts: int) -> dict[str, Any]:
      """The home view: what keel did today, what stopped it, what it will do next (console PR1).
      ONE status report, ONE log window, ONE repo open; `now_ts` is `respond`'s -- no second clock.
      `gate` is `security.gates_token(cfg.token)`, on this endpoint only, for the HALT button:
      see `payload.setup_payload` for why a derived write token may ride a GET body, and
      `server._gated_action` for why the halt needs nothing else."""
      from keel.commands.activity import apply_scope, feed_from_lines, read_log_window, resolve_log_path
      from keel.commands.pulse import gather_pulse
      config = load_config(cfg.config_path)
      path = resolve_log_path(config)
      window = read_log_window(path)
      feed = feed_from_lines(window.lines, source=str(path), truncated=window.truncated)
      if window.status != "ok" and feed.status == "empty":
          feed = feed_from_lines((), source=str(path))        # read_activity's own guard, kept verbatim
      feed = apply_scope(feed, "today", now_ts=now_ts)
      repo = open_repo(cfg.db_path)
      try:
          report = gather_pulse(repo, config, feed, now_ts=now_ts)  # gather_pulse calls gather_status(repo, config, now_ts=now_ts) itself
      finally:
          close_repo(repo)
      return payload.pulse_payload(report, gate=gates_token(cfg.token))
  ```
  (`from keel.web.security import csrf_token, gates_token, local_deployment` at the top.) Register `"/api/pulse": ApiRoute(html_route="/pulse", read=read_pulse)` directly after `/api/config`; delete the `/api/status`, `/api/activity`, `/api/gates` entries, `read_status`, `_status_report` (keep it if `read_insights` still uses it -- it does until PR3; so KEEP `_status_report`, delete only `read_status`), `read_activity`, `read_gates`. Update the `API_ROUTES` docstring ("Every one of the rendered routes…") to name `/pulse` as the landing route.
- [ ] Step 5: In `payload.py` delete `status_payload`, `activity_payload`'s callers stay (pulse uses it), delete `gates_payload` and `_capability_payload`. In `tests/web/test_payload.py` remove `"status"` from `test_every_builder_survives_a_completely_empty_report` and `"status_payload"` from the serialisable parametrisation (the pulse entries from Task 3 replace them). In `tests/web/test_client_assets.py` delete `test_the_status_payload_carries_every_field_the_status_view_places` (:1199), `_status_view_keys` (:1275) and `test_the_status_key_scan_found_the_keys_it_claims_to_check` (:1357) -- Task 5 replaces them with the generic `_VIEW_ENDPOINTS` entry for `pulseView`.
- [ ] Step 6: Run `uv run pytest tests/web -q`; expect only the client-side pins (route tables, `_VIEW_ENDPOINTS`, view-sections) red -- Task 5 fixes those. `uv run mypy` green.
- [ ] Step 7: Commit: `web: /api/pulse replaces /api/status, /api/activity and /api/gates`.

### Task 5: The client — `pulseView`, the HALT button, the route tables

**Files:** `keel/web/static/js/render.js`, `main.js`, `api.js`, `actions.js`, `keel/web/staticfiles.py`, `keel/web/static/index.html`, `manifest.webmanifest`, `css/keel.css`, `tests/web/test_pulse_view.py` (new), `tests/web/test_client_assets.py`, `tests/web/test_view_sections.py`, `tests/web/test_pwa.py`

- [ ] Step 1: Write `tests/web/test_pulse_view.py` (import the helpers from `test_client_assets`/`test_view_sections`; every assertion is structural):
  - `test_pulse_is_the_first_route_in_both_tables_and_the_default`: `_js_route_names()[0] == "pulse"`, `staticfiles.CLIENT_ROUTES[0] == "pulse"`, and `_decl(code, "DEFAULT_ROUTE")` is exactly `const DEFAULT_ROUTE = ROUTES[0];`.
  - `test_the_retired_views_are_gone_from_every_table`: none of `status`, `activity`, `gates` in `_js_route_names()`, `CLIENT_ROUTES`, the nav hrefs (`re.findall(r'<li><a href="([^"]+)">', index)`), nor `export function statusView|activityView|gatesView` in `render.js`; `main.js` has no `route.name === "activity"` / `"gates"` branch and `mount`'s fallthrough is `return pulseView(data)`.
  - `test_the_halt_control_is_one_form_with_one_button_or_one_resume_line`: body = `_function_body(render, "haltControl")`; count of `el("form"` == 1; count of `el("button"` == 1; the form sets `data-action` to `"halt"` exactly once (`body.count('"data-action", "halt"') == 1`); exactly one `el("code"` and it is placed from `halt.resume_command`, and the literal `keel resume` does NOT appear in `render.js` code (`_code_only`).
  - `test_the_halt_branch_is_presence_of_the_resume_command`: in the same body, the only `if (` is `if (plain(halt.resume_command))` (regex count == 1) -- no `.value`, no comparison (the generic scan :324 also covers this).
  - `test_pulse_view_offers_no_resume_and_names_no_phrase`: for every module in `_MODULES`, `_code_only(source)` contains none of `gates/resume`, `resume-entries`; no module's RAW source contains `RESUME TRADING` or `CLEAR STREAK HALT` (the phrases are `gates.TIER1_ACTIONS[*].phrase`; import them and assert by value, not by retyping).
  - `test_main_confirms_once_before_the_halt_and_never_elsewhere`: `_code_only(main)` has exactly one `confirm(` call; it sits inside `_decl(main, "HALT_CONFIRM")`'s consumer -- assert the submit listener body (slice from `contentNode.addEventListener("submit"` to the next `\n});`) contains `data-action` → `"halt"` branch, `window.confirm(HALT_CONFIRM)` and then `halt()` in that order (`index` comparisons), and that `halt(` is called exactly once in `main.js`; `HALT_CONFIRM` equals the sentence `Halt keel? Resuming requires \`keel resume\` at a terminal.` (read the declaration, compare the string).
  - `test_the_gate_token_lives_only_in_the_write_boundary`: `let gate` in `_code_only(actions)`; `actions.js` has no `headers`; `X-Keel-Gate` appears in `api.js` and in no other module's code; `api.js` has neither `let gate` nor `const gate`; the string `data.gate` appears in `actions.js` only (render.js's `gate` is the promotion gate -- scan for the dotted `data.gate`, never the word).
  - `test_api_js_posts_to_exactly_two_paths`: `re.findall(r'API_PREFIX \+ "([^"]+)"', code)` over `api.js` == `["setup/", "gates/halt"]` (order-insensitive set equality, length 2).
  - `test_the_pulse_view_places_every_section_once`: `_function_body(render, "pulseView")` calls each of `pulseState(`, `haltControl(`, `pulseToday(`, `pulseWhyNot(`, `pulseNextDca(`, `pulsePositions(`, `pulseFreshness(`, `pulseSubscriptions(`, `pulseCycles(` exactly once (count per name == 1).
  - `test_pulse_tables_pair_headers_and_cells`: covered by `test_every_table_emits_one_cell_per_declared_header` (:1526) -- assert the table scan now finds ≥ 4 tables inside the pulse region (slice from `export function pulseView(` to the next `\nexport function `).
  - `test_the_linked_labels_moved_to_pulse` is `tests/web/test_doc_links.py::test_every_linked_label_is_a_label_the_client_actually_puts_on_screen` -- no new test; it must stay green, which pins that `pulseState` emits `kv("kill switch"…)`, `kv("autonomy"…)`, `kv("autonomy configured"…)`, `kv("autonomy lapses"…)`, `kv("rail 11"…)`, `kv("withdrawal attestation (rail 17)"…)`, `kv("market session"…)`.
  - `test_the_manifest_starts_at_pulse`: `manifest["start_url"] == "/pulse"` (and update `tests/web/test_pwa.py:706` to the same; its docstring's `id` reasoning stands -- `id` stays `"/"`).
- [ ] Step 2: Update the existing pins:
  - `tests/web/test_client_assets.py`: `_VIEW_ENDPOINTS` (:1284): remove `activityView`, `gatesView`; add `("pulseView", "data", "/api/pulse")`. `_ROW_ENDPOINTS` (:1783): add `("pulseView", "data", "open_positions", "/api/pulse", "positions")` and `("pulseView", "data", "next_dca", "/api/pulse", "dca_live")`; add seed kind `"dca_live"` to `_seed_for` (:1862) that calls `_insert_dca(Repository(conn), "ETH-USD", "live")`. `_ROW_ENDPOINTS_UNCOVERED` (:1810): delete the four `statusView` entries, the `activityView` entries, the `gatesView` entry; add `("pulseView", "data", "live_rules")`, `("pulseView", "data", "data_freshness")`, `("pulseView", "data", "subscriptions")`, `("pulseView", "data", "why_not")` ("a list of sentences, not rows"), `("pulseView", "activity", "cycles")` ("needs a cycle log file beside the deployment; the service is pinned in tests/commands/test_pulse.py"), `("pulseView", "cycle", "events")`. `test_the_route_table_parser_actually_found_something` (:732): `assert "pulse" in names`. `test_the_view_key_scan_reads_every_ported_view` (:1342): replace the `activityView` spot check with `assert "halt.resume_command" in _view_keys("pulseView", "data")`.
  - `tests/web/test_view_sections.py`: delete `test_the_status_page_offers_the_five_sections…` (:164), `test_each_status_heading_is_written_once…` (:484), the `STATUS_SECTIONS` members of every `parametrize` (:197, :214, :522) and of `_HEADED_SECTIONS` (:519 → `{"paper", "live"}`), `_STATUS_KEYS` (:610) and its parametrize entry (:657), and the `"statusView"` member of the loop in `test_each_sectioned_route_remembers_its_own_section` (:677 → `("setupView",)`). Keep every Setup pin.
  - `tests/web/test_api.py`: nothing further (Task 4).
- [ ] Step 3: Run `uv run pytest tests/web -q`; the new file fails on missing `pulseView`/`haltControl`/`HALT_CONFIRM`; the route-table tests fail on the stale tables.
- [ ] Step 4: Implement the transport and the boundary:
  - `api.js`: extract the body of `runAction` (:296-339) into `async function post(url, extraHeaders, body)` returning a `Reading`; `runAction(key, values, csrf)` becomes `post(new URL(API_PREFIX + "setup/" + encodeURIComponent(key), origin), {"X-Keel-CSRF": csrf}, values)`; add
    ```js
    /** Engage the kill switch (console PR1). The one ungated write: `server._gated_action` wants the
     * session, `X-Keel-Client` and `X-Keel-Gate`, nothing else -- no phrase, no locality, because
     * "stopping must never be slower than starting" (keel/web/gates.py). Never rejects. */
    export async function runHalt(gate) {
      return post(new URL(API_PREFIX + "gates/halt", window.location.origin), { "X-Keel-Gate": gate }, {});
    }
    ```
  - `actions.js`: `let gate = "";` beside `let token`; `export function rememberGate(data) { gate = data && typeof data.gate === "string" ? data.gate : ""; }`; `export async function halt() { const reading = await runHalt(gate); const outcome = describe(reading); results.set("halt", outcome); return outcome; }`. Extend the module docstring's "WHAT IT HIDES" with the second token and the reason it is a second variable (security.py: "two surfaces whose tokens are derived apart should not be presented through one field").
  - `main.js`: import `rememberGate, halt`; `const HALT_CONFIRM = "Halt keel? Resuming requires `keel resume` at a terminal.";` (a double-quoted string containing backticks); in `mount`, `if (route.name === "pulse") { rememberGate(data); return pulseView(data); }` and make the final fallthrough `return pulseView(data);`; in the submit listener (:940), after `const key = …`: `if (key === "halt") { event.preventDefault(); if (!window.confirm(HALT_CONFIRM)) return; setBusy(form, true); showOutcome(key, "Halting…"); void halt().then((outcome) => { showOutcome(key, outcome); void paint(routeFor(window.location.pathname), true, false); }); return; }`. `ROUTES`: `{ name: "pulse", label: "Pulse", endpoints: ["pulse"] }` first; remove `status`, `activity`, `gates`; update the `ROUTES` doc comment and `routeFor`'s "resolving to Status" sentence. Remove the `activity`/`gates` branches and the `statusView`/`activityView`/`gatesView` imports.
  - `render.js`: `export function pulseView(data)` followed (same region, before the next `export function`) by `pulseState(data)` (the four `gridCard`s of `statusState` :709-760 -- move, keep the `kv` labels verbatim -- plus `kv("last cycle", data.last_cycle_at)`), `haltControl(data)`:
    ```js
    function haltControl(data) {
      const halt = data.halt;
      const card = el("div", "card halt");
      if (plain(halt.resume_command)) {
        const line = el("p", "halted");
        line.append(el("strong", undefined, "HALTED"), " — resume with ", el("code", undefined, plain(halt.resume_command)), " at a terminal.");
        card.append(line);
        return card;
      }
      const form = el("form", "action");
      form.setAttribute("data-action", "halt");
      const button = el("button", "halt", "HALT");
      button.setAttribute("type", "submit");
      form.append(button, el("span", "action-outcome"));
      card.append(form);
      return card;
    }
    ```
    `pulseToday(data)` (a `gridCard` of `today.*` + `kv("brackets missing", data.unbracketed_count)`), `pulseWhyNot(data)` (heading `h-why`; `stringList(data.why_not, "reasons")` or the empty sentence `"Nothing stopped keel today."` -- rendered only when `data.why_not` is empty, by `.length`, which is a list length not a value), `pulseNextDca(data)` (table `h-next-dca`: product, cadence, budget, budget basis, next cadence day; `note(plain(data.next_dca_note))`), `pulsePositions(data)` (the `statusPositions` table :763, id `h-positions`), `pulseFreshness` and `pulseSubscriptions` (moved from :848/:881), `pulseCycles(data)` (the `activityView` table :2456-2497 over `data.activity.cycles` WITHOUT sort/scope controls, then `emptyScope(data.activity)` when empty, then `cycleEvents` per cycle with events, then `feedNotes(data.activity)`). Delete `statusView`, `STATUS_SECTIONS`, `statusState`, `statusPositions`, `statusRules` (fold the counts into `pulseState` as `kv("rules", …)` per status; keep `live_rules` as a short table `h-live-rules`), `statusFreshness`, `statusSubscriptions`, `activityView`, `scopeSwitch`'s activity caller (keep `scopeSwitch` -- Orders uses it until PR2), `gatesView`, `invocationCell`. `sectioned`/`sectionSwitch` stay (Setup).
  - `staticfiles.CLIENT_ROUTES`, `index.html` nav (`<li><a href="/pulse">Pulse</a></li>` first; drop Status/Activity/Gates; fix the "#706 … Beside Gates" comment and the `<noscript>` link text `open the status page` → `open the Pulse page`), `manifest.webmanifest` `start_url: "/pulse"`.
  - `keel.css`: `.halt button.halt { background: var(--bad); border-color: var(--bad); }`, `.halted strong { color: var(--bad); }` (check the variable names at the top of `keel.css`; use `--bad` if it exists, else the state colour the `STATE_CLASS` table maps `bad` to), `.reasons li { margin: 0.2rem 0; }`.
- [ ] Step 5: `node --check keel/web/static/js/{main,render,api,actions}.js`; `uv run pytest tests/web -q`; `uv run pytest -q`; `uv run ruff check keel tests packages && uv run ruff format --check . && uv run mypy`.
- [ ] Step 6: Browser check (see Global constraints): unregister the worker, load `/` (lands on `/pulse`), every route, deep-link reload each; on the COPY, click HALT → native confirm → the card becomes the HALTED line, `#engine` banner stays green, the attestation banner unchanged; run `uv run keel --config $SCRATCH/console/config.yaml --db $SCRATCH/console/keel.db resume` at the terminal and confirm the button returns on the next poll. Confirm the DevTools console shows no error.
- [ ] Step 7: Commit: `console: Pulse home view with HALT; Status, Activity and Gates retired`.

### Task 6: Docs and the plan copy

**Files:** `docs/operator-runbook.md`, `README.md`, `docs/superpowers/plans/2026-10-10-console-four-views.md`

- [ ] Step 1: `docs/operator-runbook.md:839-841`: rewrite the sentence to name the console's views as they stand after this PR (Pulse, Setup, Orders, Positions, Balances, Timeline, Insights, Research, Rules, Venues, Plans -- "being slimmed to four engine views: Pulse, Holdings, Rules, Audit & Setup; see `docs/superpowers/plans/2026-10-10-console-four-views.md`") and add one paragraph "Halting from the browser": the HALT button engages the kill switch through the same `keel kill` operation, needs the session and the gate token, works from a tunnel, and resuming is `keel resume` at a terminal.
- [ ] Step 2: `README.md:247`: `web/  the local console \`keel serve\` renders — reads, setup steps and a halt`.
- [ ] Step 3: Copy this plan to `docs/superpowers/plans/2026-10-10-console-four-views.md` verbatim.
- [ ] Step 4: `uv run ruff format --check .` (docs are in the format scope -- it only checks `.py`, but the step runs over `.`); commit: `docs: console views after PR1; plan copy`.

### PR1 verification (run all, in this order, before opening the PR)

```
node --check keel/web/static/js/main.js && node --check keel/web/static/js/render.js && node --check keel/web/static/js/api.js && node --check keel/web/static/js/actions.js && node --check keel/web/static/sw.js
uv run ruff check keel tests packages
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run keel --version
```
plus the browser check in Task 5 Step 6. PR body: scope line, the rulings below, the browser-check evidence (routes loaded, halt engaged and resumed on the copy).

### PR1 rulings
- Global rulings 1-8 apply (gate token on pulse; `window.confirm`; no browser resume; Gates view removed now; subscriptions compact on Pulse; Pulse unsectioned; compact positions on Pulse; next cadence DAY).
- **`/api/pulse` declares no sortable collection** — why: a glance page; sorting is Holdings' job in PR2 — cost if wrong: one `sortable=` tuple later.
- **`_status_report` survives until PR3** — why: `read_insights`/`read_journal` call it; deleting it with `read_status` would be a PR3 change leaking into PR1 — cost if wrong: one unused helper for two PRs.
- **The halt reuses the setup form plumbing** (`data-action="halt"` form, `showOutcome`, `restoreOutcomes`, `setBusy`) — why: the outcome memory and the busy state already exist for one write surface and a second copy would be the drift `actions.js`'s docstring describes — cost if wrong: the word "action" on a form that is not a setup action; the server decides what `halt` is, not the attribute.

---

## PR2 — Holdings

**Branch:** `feat/console-holdings` · **PR title:** `console: Holdings view replaces Positions and Orders (#13 PR2)`

**Scope in one line:** new `/api/holdings` + `holdingsView` -- open tranches grouped by rule with per-tranche cost, entry-gate verdict, attestation chip, stop/bracket only when one exists (the Status "NO bracket" warning folded in), per-tranche slippage (expected vs actual fill of the entry order) and placement (AUTONOMOUS / approved); a compact "Recent orders by keel" section read from `/api/orders` (retargeted, cancel column and dialog removed); `/positions`, `/orders`, `/api/positions` removed in lockstep; no close or cancel control anywhere.

**Files touched:**
- new: `keel/commands/holdings.py`, `tests/commands/test_holdings.py`, `tests/web/test_holdings_view.py` (replaces `tests/web/test_positions_view.py`)
- edit: `keel/web/payload.py`, `keel/web/api.py`, `keel/web/staticfiles.py`, `keel/web/static/index.html`, `js/main.js`, `js/render.js`, `css/keel.css`, `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_client_assets.py`, `tests/web/test_orders_view.py`, `tests/web/test_payload.py`, `docs/operator-runbook.md`
- delete: `tests/web/test_positions_view.py` (its surviving pins move to `test_holdings_view.py`)
- Nav after PR2: `pulse, setup, holdings, balances, timeline, insights, research, rules, venues, plans` + Docs.

### Task 1: The Holdings service — `keel/commands/holdings.py`

**Files:** `keel/commands/holdings.py` (new), `tests/commands/test_holdings.py` (new)

Module docstring rule: *"A VIEW over `gather_positions` and the orders book. The only thing it adds is the join the ledger does not store: a tranche's entry order, found by the cycle clock both rows were written with (`agent._open_tranche` and `executor._order_row` share `now_ts`; `PaperTrader` writes both with `signal.ts`). Nothing here reaches a venue; `keel/web/__init__.py`'s posture is unchanged."*

```python
@dataclass(frozen=True)
class HoldingRow:
    position: PositionRow                 # the whole row; the payload reads what it needs
    has_bracket: bool                     # positions.bracket_order_id is not None
    cost: Decimal                         # qty * entry_fill + entry_fee -- computed HERE (commands layer may)
    entry_order: OrderRow | None          # orders._row_from_dict(match, rule_names, repo=None) -- no cancel classification
    entry_order_note: str                 # "" when matched, else ENTRY_ORDER_NOT_IDENTIFIED

@dataclass(frozen=True)
class HoldingsReport:
    now_ts: int
    mode: str                             # the deployment's `auto_trade.mode` -- `_bracket_field` needs it
    rows: tuple[HoldingRow, ...]
    @property open_count -> int; @property rules -> tuple[str, ...]  # distinct rule_name in first-seen (oldest-first) order, "" last as "unattributed"
    @property stop_row_ids -> tuple[int, ...]                         # tranches whose initial_stop is not None -- the payload lists them so the view can show the stop column only when one exists

ENTRY_ORDER_NOT_IDENTIFIED = "entry order not identified -- no buy order shares this tranche's product and open time"

def gather_holdings(repo: Repository, config: Config, *, now_ts: int) -> HoldingsReport
```
Implementation: `positions = gather_positions(repo, config, now_ts=now_ts)`; `raw_by_id = {int(r["id"]): r for r in repo.get_open_positions()}` (for `bracket_order_id`); `rule_names = {int(r["id"]): str(r["kind"]) for r in repo.get_rules(None)}` (what `gather_orders` does, once); for each `PositionRow`, `candidates = [o for o in repo.get_orders(product_id=row.product_id) if o["side"] == "buy" and o.get("created_at") == row.opened_at]`; take the first (oldest id) → `orders._row_from_dict(o, rule_names)` (NO `repo=`, so `cancel` is the inert default and `classify_cancel` is never run). `mode = str(config.auto_trade.mode)`.

- [ ] Step 1: Tests in `tests/commands/test_holdings.py` (seed through `Repository.insert_order`/`open_position` with a shared `now_ts`, as `tests/commands/test_positions.py` seeds):
  - `test_a_tranche_finds_its_entry_order_by_product_side_and_the_shared_clock`
  - `test_a_sell_at_the_same_second_is_never_the_entry_order`
  - `test_a_tranche_with_no_matching_order_says_so_rather_than_guessing` (note == constant; `entry_order is None`)
  - `test_cost_is_qty_times_fill_plus_the_entry_fee`
  - `test_rows_group_by_rule_in_first_seen_order_with_unattributed_last`
  - `test_has_bracket_follows_bracket_order_id`
  - `test_the_join_never_classifies_a_cancel` (monkeypatch `keel.commands.orders.classify_cancel` to raise; gather still succeeds)
  - `test_the_service_reads_no_clock` (AST scan as in PR1 Task 2)
- [ ] Step 2: Run; `ModuleNotFoundError`.
- [ ] Step 3: Implement.
- [ ] Step 4: `uv run pytest tests/commands/test_holdings.py -q`; `uv run mypy`.
- [ ] Step 5: Commit: `commands: holdings service (tranche ↔ entry order join, per-tranche cost)`.

### Task 2: `payload.holdings_payload`

**Files:** `keel/web/payload.py`, `tests/web/test_payload.py`

```python
def _holding_row_payload(row: HoldingRow, mode: str) -> dict[str, Any]:
    p, o = row.position, row.entry_order
    return {
        "id": str(p.id), "product_id": p.product_id, "rule_name": p.rule_name,
        "opened_at": moment(p.opened_at), "qty": quantity(p.qty),
        "entry_fill": money(p.entry_fill), "entry_fee": money(p.entry_fee), "cost": money(row.cost),
        "stop": money(p.initial_stop),                                  # ABSENT (payload.absent()) when None -- the view shows the column only when some row has one
        "bracket": _bracket_field(_as_open_position_status(row), mode),   # the #641 judgement, unchanged -- lift `_bracket_field` to take (has_bracket, initial_stop, mode) and have the old caller adapt
        "freshness": _readiness_field(p.ready, p.ready_reason),
        "attestation": _attestation_chip(p), "attest_due_at": moment(p.attest_due_ts),
        "placement": flag(o.confirmation_is_autonomous, on="AUTONOMOUS", off=o.confirmation or "unrecorded", on_state=WARN, off_state=NEUTRAL) if o else label("unknown", display=row.entry_order_note, state=UNKNOWN),
        "expected_fill": money(o.expected_fill) if o else absent(),
        "actual_fill": money(o.actual_fill) if o else absent(),
        "fill_divergence": (money(o.fill_divergence, signed=True) with the adverse state exactly as `_order_row_payload` builds it) if o else absent(),
        "entry_order_id": str(o.id) if o else "",
        "realized_qty": …, "realized_proceeds": …, "realized_fees": …,  # the disclosure survives
    }

def holdings_payload(report: HoldingsReport) -> dict[str, Any]:
    return {"as_of": …, "generated_at": …, "mode": report.mode, "open_count": count(report.open_count),
            "rules": [str(r) for r in report.rules],
            "stop_rows_present": [str(i) for i in report.stop_row_ids],   # the view draws the stop column only when this is non-empty
            "rows": [_holding_row_payload(r, report.mode) for r in report.rows]}
```
No `mark`, `market_value`, `unrealized`, `stop_distance*`, `mark_at` (the brief drops them; `PositionRow` still carries them for `keel positions` and balances).

- [ ] Step 1: Tests (`tests/web/test_payload.py`): `test_no_wire_value_in_the_holdings_payload_is_a_json_number`; `test_a_tranche_without_an_entry_order_crosses_as_unknown_placement_and_absent_slippage`; `test_the_slippage_state_is_the_services_verdict_not_the_sign` (parametrise buy adverse/favourable as `test_orders_view.py:187` does); `test_a_stop_is_absent_not_zero_when_none_was_recorded`; `test_the_bracket_judgement_is_unchanged_for_live_and_paper` (move the four bracket tests :733-790 here, driving `holdings_payload`; PR1's pulse copies stay too since Pulse still shows brackets); add `"holdings_payload"` to the empty-report and serialisable tests.
- [ ] Step 2: Run; `AttributeError`.
- [ ] Step 3: Implement; refactor `_bracket_field(has_bracket: bool, initial_stop: Decimal | None, mode: str)` with `_position_payload` adapting (`position.has_bracket, position.initial_stop`).
- [ ] Step 4: `uv run pytest tests/web/test_payload.py tests/commands/test_console_thinness.py -q`.
- [ ] Step 5: Commit: `web: holdings_payload`.

### Task 3: `/api/holdings` in; `/api/positions` out; `/api/orders` retargeted

**Files:** `keel/web/api.py`, `keel/web/payload.py`, `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_orders_view.py`

- [ ] Step 1: Tests: `API_ROUTES` (test_api :55) -- `/api/positions` → `/api/holdings`; `test_server.py::ROUTES` -- `/api/orders` stays, add `/api/holdings`; new `test_holdings_rows_carry_the_entry_order_figures_when_one_matches` (seed an order and a position sharing `created_at`/`opened_at`; see `_seed_positions`/`_seed_orders` :208-245 -- extend `_seed_positions` with an optional `opened_at` and `_seed_orders` with an optional `created_at` and `expected_fill`); `test_holdings_sorts_by_decimal_on_cost` (reuse the float-collision shape of :612); `test_every_declared_sort_column_is_a_column_the_rows_actually_have` (:552) covers the new `sortable`. `test_orders_view.py`: `test_serving_the_orders_page_imports_no_credential_code` (:926) -- add a second probe line `api.read_holdings(cfg, {}, None, 0)`; delete `test_the_orders_payload_carries_the_classification_and_the_command` (:997), `test_the_console_never_builds_a_control_that_cancels` (:971) and `test_the_console_hands_over_the_command_rather_than_composing_it` (:984) -- replaced in Task 4 by a structural "no cancel anywhere" pin; keep `test_there_is_no_cancel_route_at_all` and `test_the_web_package_cannot_reach_a_broker_or_a_credential`.
- [ ] Step 2: Run; 404 on `/api/holdings`.
- [ ] Step 3: `api.py`: `read_holdings` (docstring: READ ONLY; no `?limit=`/`?scope=`, `read_positions`'s own reasons, moved); route `"/api/holdings": ApiRoute(html_route="/holdings", read=read_holdings, collection="rows", sortable=("rule_name", "product_id", "opened_at", "qty", "entry_fill", "cost", "fill_divergence"))`; `/api/orders`'s `html_route` → `"/holdings"`; delete `/api/positions` and `read_positions`. `payload.py`: delete `positions_payload`, `_position_row_payload`, `_cancel_payload`, `_CANCEL_STATES`, and the `cancel` key from `_order_row_payload` (the `OrderRow.cancel` field stays on the service).
- [ ] Step 4: `uv run pytest tests/web/test_api.py tests/web/test_server.py tests/web/test_orders_view.py tests/web/test_payload.py -q`; `uv run mypy`.
- [ ] Step 5: Commit: `web: /api/holdings replaces /api/positions; /api/orders serves /holdings`.

### Task 4: The client — `holdingsView`

**Files:** `render.js`, `main.js`, `staticfiles.py`, `index.html`, `keel.css`, `tests/web/test_holdings_view.py` (new, from `test_positions_view.py`), `tests/web/test_client_assets.py`, `tests/web/test_orders_view.py`

- [ ] Step 1: Tests. Create `tests/web/test_holdings_view.py` by moving `test_positions_view.py`'s client pins and rewriting them for `holdingsView`:
  - `test_the_holdings_view_is_wired_into_the_client_router` (`holdings` in `CLIENT_ROUTES`, `route.name === "holdings"` branch, `export function holdingsView`; `positions`/`orders` in none of the tables; `positionsView`/`ordersView`/`cancelCell`/`openCancelHelp`/`orderDetail` no longer declared).
  - `test_the_holdings_view_has_no_close_or_cancel_action_anywhere`: over the region `export function holdingsView(` → next `\nexport function `, lowercase body contains none of `close`, `sell`, `exit`, `cancel`, `liquidate`, and `el("button"` count == 0, `addEventListener` count == 0 (the sort/scope controls are built by `table()`/`scopeSwitch`, outside the region).
  - `test_the_holdings_view_groups_by_the_reports_own_rule_list` (`data.rules` read; no `new Set(`/`Object.keys(` over rows in the region).
  - `test_tranche_rows_carry_gate_attestation_placement_and_slippage_columns`: parse the first `table(` call in `holdingsSection` via `_table_calls`; the header labels, as a list, contain exactly one each of `entry gate`, `attestation`, `placement`, `expected`, `actual`, `slippage` and the cell list pairs 1:1 (the generic :1526 pin), and NO `mark`, `value`, `unrealized`, `to stop` labels.
  - `test_the_stop_column_is_drawn_only_when_a_row_has_one`: the region reads `row.stop` inside a `.some(` or `.filter(` guard exactly once (presence test, no `.value`), and `"stop"` appears in a header exactly once.
  - `test_recent_orders_is_compact_and_has_no_cancel_column`: the orders table in `recentOrdersSection` has headers exactly `placement, rule, side, product, status, expected, actual, slippage, placed (UTC)` (no `fee`, no `cancel`).
  - `test_the_stylesheet_no_longer_carries_the_cancel_modal` (`.cancelhelp` absent from `keel.css`).
  - Move the surviving attestation/freshness client pins (`:221`, `:327`) over as-is against the new region.
- [ ] Step 2: `tests/web/test_client_assets.py`: `_VIEW_ENDPOINTS` -- replace `ordersView`/`positionsView` with `("holdingsView", "data", "/api/holdings")`, `("holdingsView", "orders", "/api/orders")`; `_ROW_ENDPOINTS` -- `("holdingsView", "data", "rows", "/api/holdings", "positions")`, `("holdingsView", "orders", "rows", "/api/orders", "orders")`; `test_the_route_table_parser…` unchanged. `tests/web/test_orders_view.py`: retarget `test_the_renderer_declares_the_placement_column_first` (:716), `test_the_orders_table_names_the_rule…` (:886), `test_the_orders_view_is_wired…` (:752), `test_the_scope_switch_names_itself_per_view` (:763), `test_the_status_tab_reaches_the_query_string…` (:892) at `recentOrdersSection`/`holdingsView`; delete `test_the_renderer_shows_the_book_pair_without_computing_one` (:288, the detail disclosure goes).
- [ ] Step 3: Run; red on missing `holdingsView`.
- [ ] Step 4: Implement `render.js`: `export function holdingsView(data, sort, onSort, orders, ordersSort, onOrdersSort, onScope, onStatus)` → h1 "Holdings", sub line (`generated_at`, `open_count` open tranche(s)), `holdingsSection(data, sort, onSort)` (one `table` per `data.rules` entry, id `h-hold-<rule>`, columns `opened (UTC) · product · qty · entry · fee · cost · [stop] · bracket · entry gate · attestation · placement · expected · actual · slippage`; `[stop]` is spliced into the header and cell lists only when `(data.stop_rows_present || []).length !== 0` -- a LIST of tranche ids that carry a stop, its emptiness being the branch, exactly how `why_not` and `kinds` are branched on; the client never reads `display` or `.value` to decide it), the realized-legs disclosure per tranche (moved from `positionsView` :2129-2145), then `recentOrdersSection(orders, ordersSort, onOrdersSort, onScope, onStatus)` (heading `h-orders`, `scopeSwitch`, `statusSwitch`, the compact table, `emptyOrders`). Delete `positionsView`, `ordersView`, `cancelCell`, `openCancelHelp`, `orderDetail`. `main.js`: `{ name: "holdings", label: "Holdings", endpoints: ["holdings", "orders"] }` replacing `orders` and `positions`; `mount` branch passes `readings[1]` and `sorter(route, "orders")` plus the scope/status callbacks (move them from the orders branch). `staticfiles`, `index.html` nav. `keel.css`: delete `.cancelhelp*`, `.invocation`, `.linklike` if no other caller (grep: `refusedView` uses `linklike`? check :564-660 -- keep it if so).
- [ ] Step 5: `node --check` on `render.js`, `main.js`; full suite; mypy; browser check (`/holdings` with the copy's open tranches; sort by `cost`; the stop column appears only when a tranche carries one; scope/status tabs on the orders section re-read the server).
- [ ] Step 6: Commit: `console: Holdings view replaces Positions and Orders`.

### Task 5: Docs

- [ ] `docs/operator-runbook.md` console paragraph: views after PR2; one sentence on Holdings ("per-tranche cost, entry-gate verdict, attestation, bracket, and the entry order's expected-vs-actual fill; no close, no cancel -- `keel orders cancel` and the exit path stay at the terminal"). Commit: `docs: console views after PR2`.

### PR2 verification
Same command block as PR1 (touched JS: `main.js`, `render.js`); browser check as in Task 4 Step 5.

### PR2 rulings
- Global rulings 12 (deterministic entry-order join) and 13 (no cancel affordance).
- **`/api/orders` survives as the Holdings secondary** rather than being folded into `/api/holdings` — why: it has its own `?scope=`/`?status=`/`?limit=` surface and 40 pins in `test_orders_view.py`; one sortable collection per endpoint — cost if wrong: two reads per Holdings paint (loopback).
- **The stop column is drawn from `stop_rows_present` (a list)** — why: `render.js` may not read `.value`/`display` to decide anything; list emptiness is the sanctioned branch — cost if wrong: one extra list on the wire.
- **`PositionRow` keeps `mark`/`market_value`/`unrealized`/`stop_distance`** — why: `keel positions` and `gather_balances` render them; only the console drops them — cost if wrong: none.

---

## PR3 — Rules

**Branch:** `feat/console-rules` · **PR title:** `console: Rules view absorbs Insights and Research; charts and journal retired (#13 PR3)`

**Scope in one line:** `rulesView` becomes the one place for rules -- the read-only ledger + params, rule track records with promotion-gate distances (from `build_insights_report`, now carried on `/api/rules` as `records`), the Research record (trials / gauntlet / slippage / matrix, four endpoints retargeted to `/rules`) and the DCA plan card; `/insights`, `/research`, `/api/insights`, `/api/journal`, `chart.js`, the equity charts, the closed-trades table and the discretionary-journal section are removed in lockstep.

**Files touched:**
- edit: `keel/web/payload.py`, `keel/web/api.py`, `keel/web/staticfiles.py`, `index.html`, `js/main.js`, `js/render.js`, `sw.js` (PRECACHE), `css/keel.css`, `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_client_assets.py`, `tests/web/test_research_view.py`, `tests/web/test_payload.py`, `tests/web/test_pwa.py` (only if a PRECACHE pin names `chart.js`), `docs/operator-runbook.md`
- delete: `keel/web/static/js/chart.js`
- Nav after PR3: `pulse, setup, holdings, balances, timeline, rules, venues, plans` + Docs.

### Task 1: `/api/rules` carries `records`; `/api/insights` and `/api/journal` go; research retargets

**Files:** `keel/web/api.py`, `keel/web/payload.py`, `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_payload.py`

- [ ] Step 1: Tests. `test_api.py::API_ROUTES`: remove `/api/insights`, `/api/journal` (14 routes remain). `test_the_reader_scan_sees_every_reader_there_is` (:1533): floor `>= 15` → `>= 10` (global ruling 18; keep the cross-check against `API_ROUTES`). New `test_rules_carries_the_track_records_and_their_gate_distances` (seed a promoted rule plus closed `trade_outcomes` the way `tests/commands/test_insights.py` seeds; assert `data.records[0].gate.trades_remaining` exists and no `records` sort is accepted: `?sort=expectancy` on `/api/rules` → 400 since `sortable` still names only ledger columns). `test_the_trade_limit_does_not_truncate_the_operators_journal` (:1271) is deleted with `/api/journal`. `test_server.py::ROUTES`: drop `/api/insights`, `/api/journal`; `:164` tuple: `"/insights"` → `"/rules"`. `test_payload.py`: delete the insights/journal/series/curve/notes tests (:787-1000 journal and curve pins, :1438-1560 series, :1860-1905 discretionary journal), KEEP and retarget the ones about track records and gates (`test_a_drawdown_fraction_is_not_rescaled…` :807, `test_a_win_rate_float_is_re_encoded…` :824, `test_a_loss_is_labelled_bad…` :839 if it drives a track record) at `rules_payload(rows, records=…)`; remove `"insights"`/`"journal"` from the empty-report dict and the serialisable parametrisation, add a `rules_payload` entry with a `records` argument.
- [ ] Step 2: Run; red.
- [ ] Step 3: `api.py`: `read_rules` becomes
  ```python
  def read_rules(cfg, _query, _state, now_ts):
      """The ledger, the params, and each rule's track record with its distance to the promotion
      gate (console PR3; was /api/insights). `records` is NOT the sortable collection: see the
      route entry. ONE status report, ONE repo open, `now_ts` is respond's."""
      from keel.commands.insights import build_insights_report
      repo = open_repo(cfg.db_path)
      try:
          rows = repo.get_rules(None)
          config = load_config(cfg.config_path)
          report = build_insights_report(repo, config, _status_report(cfg, now_ts), now_ts)
      finally:
          close_repo(repo)
      return payload.rules_payload(rows, records=report)
  ```
  (`_status_report` opens its own connection -- keep it; or pass `gather_status(repo, config, now_ts=now_ts)` inline and delete `_status_report`; prefer the latter and delete `_status_report`, `EQUITY_POINT_LIMIT`, `DEFAULT_JOURNAL_LIMIT`, `MAX_JOURNAL_LIMIT`, `_journal_limit`, `read_insights`, `read_journal`.) The four research routes: `html_route="/rules"`. `payload.rules_payload(rows, *, records: InsightsReport | None = None)` adds `"records": [_track_record_payload(r) for r in records.rules]` and `"closed_trade_count": count(records.closed_trade_count)` (empty list / `count(0)` when `None` -- the awaiting shape every caller can rely on); delete the builders in global ruling 11 and the `# -- journal --` section.
- [ ] Step 4: `uv run pytest tests/web/test_api.py tests/web/test_server.py tests/web/test_payload.py tests/commands/test_console_thinness.py -q`; `uv run mypy`.
- [ ] Step 5: Commit: `web: /api/rules carries track records and gate distances; /api/insights and /api/journal retired`.

### Task 2: The client — `rulesView` absorbs Research and Insights; `chart.js` and the #602 block go

**Files:** `render.js`, `main.js`, `chart.js` (delete), `sw.js`, `keel.css`, `staticfiles.py`, `index.html`, `tests/web/test_client_assets.py`, `tests/web/test_research_view.py`, `tests/web/test_pwa.py`

- [ ] Step 1: Tests.
  - `tests/web/test_client_assets.py`: `_MODULES` (:84) -- remove `chart.js`; `_DERIVATION_FREE` (:103) → `("render.js",)` (keep the tuple, keep the parametrise); delete the #602 chart tests (:380-535: `_insights_view_body`, `test_a_journal_row_with_a_point…`, `test_a_losing_trade_is_marked_by_shape…`, `test_the_chart_is_keyboard_operable…`), the series tests (:1380-1470), the journal-section tests (:2269-2295); `_VIEW_ENDPOINTS`: replace the two `insightsView` and three `researchView` entries with `("rulesView", "trials", "/api/research/trials")`, `("rulesView", "gauntlet", "/api/research/gauntlet")`, `("rulesView", "slippage", "/api/research/slippage")`, `("rulesView", "matrix", "/api/research/matrix")` (and keep `("rulesView", "data", "/api/rules")`, `("dcaPlanCard", "plan", "/api/dca-plan")`); `test_the_view_key_scan_reads_every_ported_view` -- replace `"curve" in _view_keys("insightsView", "journal")` with `"records" in _view_keys("rulesView", "data")`; `_ROW_ENDPOINTS`: `researchView` rows → `rulesView` with the same roots; drop the `insightsView` notes entry; `_ROW_ENDPOINTS_UNCOVERED`: `researchView` → `rulesView`, drop the `insightsView` entries, add `("rulesView", "data", "records")`: "needs closed trades to build a track record". New structural pins in `test_research_view.py` (rename nothing else): `test_the_research_sections_are_placed_once_each_in_the_rules_view` (`_function_body(render, "rulesView")` calls `trialsSection(`, `gauntletSection(`, `slippageSection(`, `matrixSection(`, `recordsSection(`, `dcaPlanCard(` exactly once each); retarget `_view_body()` (:290) to slice from `function trialsSection(`; `test_the_research_view_is_wired_into_the_client_router` → asserts `research` is in NONE of the tables and `rulesView` reads the four endpoints (`ROUTES` entry parse: the `rules` route's `endpoints` list equals `["rules", "dca-plan", "research/trials", "research/gauntlet", "research/slippage", "research/matrix"]`); `test_no_research_column_in_the_view_carries_a_sort_key` (:215) now scans the four section bodies; `test_the_records_table_offers_no_sort_control` (new: `recordsSection` body has no `onSort`/`sort:`).
  - `tests/web/test_pwa.py`: the PRECACHE ↔ shipped-files comparison (:546) is derived from the directory, so deleting `chart.js` and its PRECACHE line keeps it green; no edit unless a docstring example names `chart.js`.
- [ ] Step 2: Run; red on the route tables and the missing sections.
- [ ] Step 3: Implement. `render.js`: `rulesView(data, sort, onSort, plan, planError, trials, gauntlet, slippage, matrix)` -- h1 "Rules", sub "read-only · promotion happens in the CLI", ledger table (unchanged), params cards, `recordsSection(data)` (the `h-records` table from `insightsView` :2896-2946 WITHOUT the sort pair, its n=30 note, then `gateCard` per rule with a gate -- `samplePill`/`gateCard` move intact), `dcaPlanCard(plan, error)`, then the research record under a heading `h-research` with the sub-line `researchView` had: `trialsSection(trials)` (extracted from `researchView` :1561-1684, including the chain verdict + breaks), `gauntletSection`, `slippageSection`, `matrixSection` (unchanged). Delete `insightsView`, `notesSection`, `researchView`, the `chart.js` import. `main.js`: `ROUTES` `rules` entry with the six endpoints; remove `insights`/`research` entries and branches; `mount`'s rules branch hands `readings[2..5]` as secondary readings (`null` when a read failed, like the plan reading); delete the #602 block -- `activeCurve`, `chartViewBox`, `panning`, `CHART_*` constants, `chartSvgFrom`, `journalRowFrom`, `dataXAt`, `nearestPoint`, `applyViewBox`, `zoomBy`, `panTo`, `reapplyChartView` (and its call in `rebuildInto`), `highlightJournalRow`, the wheel/pointer/keydown/click/pointerover/focusin listeners that reference them (:1248-1400), `CHART_EXPORT_PROPERTIES`, `inlineComputedStyle`, `downloadChartBlob`, the PNG/SVG export functions and their `data-chart-action` listener; the `activeCurve = null` line in `mount`. `sw.js`: drop the `chart.js` PRECACHE line. `keel.css`: delete the `.chart` block (:1015-1115 region, every `.chart …` rule), `.chartactions`; rename `button.chartaction` → `.linkaction` and change `timelineView`'s `el("a", "chartaction", "Export CSV")` to `"linkaction"` (grep for every other `chartaction` user first). `staticfiles.CLIENT_ROUTES`, `index.html` nav.
- [ ] Step 4: `node --check` on `main.js`, `render.js`, `sw.js`; full suite; mypy; browser check (`/rules` shows ledger → params → track records with gate cards → DCA card → research sections; `/rules?budget=500&buffer=0.1` fills the card; no console error; the service worker precache has no `chart.js`).
- [ ] Step 5: Commit: `console: Rules view absorbs Insights and Research; charts and journal retired`.

### Task 3: Docs

- [ ] `docs/operator-runbook.md`: views after PR3; sentence: "Track records, promotion-gate distances and the research record live on Rules; the equity series and closed-trade journal are `keel insights` and `keel journal` at the terminal, and every closed trade and journal entry is still a Timeline row." Commit: `docs: console views after PR3`.

### PR3 verification
Command block as PR1 (touched JS: `main.js`, `render.js`, `sw.js`; `chart.js` deleted -- confirm `ls keel/web/static/js` no longer lists it); browser check as Task 2 Step 4.

### PR3 rulings
- Global rulings 9 (research stays four endpoints), 10 (records unsorted), 11 (charts and journal leave the console), 18 (reader floor).
- **`_status_report` is deleted here** — why: `read_rules` is its last caller and can take the status report from the connection it already holds — cost if wrong: none.
- **Research sections keep their own section functions** (`trialsSection` etc.) rather than being inlined into `rulesView` — why: the `test_research_view.py` structural pins slice by function name, and a 400-line view function is the shape `_decl` cannot bound — cost if wrong: four small top-level functions.

---

## PR4 — Audit & Setup

**Branch:** `feat/console-audit` · **PR title:** `console: Audit & Setup view; Timeline, Setup, Venues, Balances and Plans retired -- four views (#13 PR4)`

**Scope in one line:** one `auditView` at `/audit` with three sections -- the tamper-evident timeline (default kind filter `keel` = system + attestation, trade/flow selectable, system rows carry `blocked`, hash chain and CSV export unchanged), Setup (steps, the three stage tabs, the job panel, the CSRF write token -- the stopped view still lands here) and venue readiness; `/timeline`, `/setup`, `/venues`, `/balances`, `/plans` routes, `/api/balances`, `/api/plans`, `keel/commands/plans.py` and their tests removed in lockstep. Final nav: Pulse · Holdings · Rules · Audit & Setup · Docs.

**Files touched:**
- edit: `keel/commands/timeline.py`, `keel/web/api.py`, `keel/web/payload.py`, `keel/web/staticfiles.py`, `index.html`, `js/main.js`, `js/render.js`, `js/docs.js`, `css/keel.css`, `tests/commands/test_timeline.py`, `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_client_assets.py`, `tests/web/test_view_sections.py`, `tests/web/test_timeline_export.py`, `tests/web/test_payload.py`, `tests/web/test_doc_links.py` (only if it lists labels), `docs/operator-runbook.md`, `docs/architecture.md`, `README.md`
- delete: `keel/commands/plans.py`, `tests/commands/test_plans.py`, `tests/web/test_balances_view.py`. NOT `keel/commands/balances.py`: `keel balances` is a CLI command (`balances.py:232` is its service), so only the web side goes: `payload.balances_payload`, `_asset_balance_payload`, `_staleness_payload`, `_human_span`, `_SETTLED_UNRECORDED`, `api.read_balances`.
- Nav after PR4: `pulse, holdings, rules, audit` + Docs.

### Task 1: Timeline -- `blocked` on system rows and the `keel` composite kind

**Files:** `keel/commands/timeline.py`, `tests/commands/test_timeline.py`, `tests/web/test_timeline_export.py`

- [ ] Step 1: Tests (`tests/commands/test_timeline.py`): `test_a_system_row_names_the_blocked_count_between_signals_and_entered` (build an `ActivityCycle` with `blocked=2`; summary == `"cycle: 3 signal(s), 2 blocked, 1 entered, 0 exited, 0 error(s)"`); `test_kind_keel_is_system_and_attestation_only_and_echoes_back_as_keel` (seed one row of each kind as the existing tests do; `gather_timeline(..., kind="keel")` returns only `system`/`attestation` rows, `report.kind == "keel"`, `scoped_count` unchanged, `filtered_count` matches); `test_kinds_present_lists_keel_first_when_either_member_is_present`; `test_export_rows_honours_kind_keel_the_same_way`; `test_an_unknown_kind_still_collapses_to_every_kind`. `tests/web/test_timeline_export.py`: `test_the_export_filters_by_the_composite_kind` (`?kind=keel` → no `trade` rows in the CSV).
- [ ] Step 2: Run; red.
- [ ] Step 3: `timeline.py`: `KEEL_KIND = "keel"`; `KEEL_KINDS: frozenset[str] = frozenset({"system", "attestation"})` with a docstring: *"What keel itself wrote or a human swore to, as opposed to what a venue reported (`trade`) or cash did (`flow`); the console's default filter, because an audit page opens on keel's own record first (console PR4)."* In `gather_timeline`: `resolved_kind = requested if requested in TIMELINE_KINDS or requested == KEEL_KIND else ""`; `wanted = KEEL_KINDS if resolved_kind == KEEL_KIND else ({resolved_kind} if resolved_kind else None)`; `filtered = [r for r in scoped if wanted is None or r.kind in wanted]`; `kinds_present` = `(KEEL_KIND,) if present & KEEL_KINDS else ()` + the existing tuple. `_cycle_rows` summary: insert `f"{getattr(cycle, 'blocked', 0)} blocked, "` after the signals clause. `export_rows` passes `kind` through unchanged (it already does).
- [ ] Step 4: `uv run pytest tests/commands/test_timeline.py tests/web/test_timeline_export.py -q`.
- [ ] Step 5: Commit: `timeline: blocked on system rows; kind=keel composite filter`.

### Task 2: The route table -- `/api/balances`, `/api/plans` out; `html_route`s retargeted to `/audit`

**Files:** `keel/web/api.py`, `keel/web/payload.py`, `keel/commands/plans.py` (delete), `tests/web/test_api.py`, `tests/web/test_server.py`, `tests/web/test_payload.py`, `tests/commands/test_plans.py` (delete), `tests/web/test_balances_view.py` (delete)

- [ ] Step 1: Tests: `API_ROUTES` → `/api/config, /api/pulse, /api/setup, /api/holdings, /api/orders, /api/timeline, /api/research/{4}, /api/rules, /api/dca-plan, /api/venues` (12). `test_a_stopped_engine_still_answers…` (:737): `("/api/config", "/api/venues")` (plus `/api/setup`, already covered at :827). `test_setup_carries_the_csrf_token_and_only_setup_does` loop: `("/api/config", "/api/pulse", "/api/venues")`. `test_server.py::ROUTES`: drop nothing new beyond `/api/balances`/`/api/plans` if present (they are not; the tuple is the secret sweep and already lacks them); `:164` tuple → `("/", "/rules", "/pulse", "/audit")`. `test_payload.py`: delete the balances/plans pins (`test_a_shipped_tier_reads…` :1909, `test_a_planned_tier…` :1934, `_asset_balance_*`), keep the readiness ones (:1240-1300). `test_client_assets.py`: delete the Plans pins (:2337-2400) and `_PURCHASE_AFFORDANCES` if nothing else imports it (`tests/commands/test_plans.py` did; it is deleted too).
- [ ] Step 2: Run; red.
- [ ] Step 3: `api.py`: delete `read_balances`, `read_plans`, their routes; `/api/setup`, `/api/timeline`, `/api/venues` → `html_route="/audit"`. `payload.py`: delete `balances_payload` and its helpers, `plans_payload`, `_claim_payload`, `_tier_payload`, `_TIER_*`, `_trigger_payload`, `_tier_status`. `git rm keel/commands/plans.py tests/commands/test_plans.py tests/web/test_balances_view.py`. Grep `keel/` for `plans` imports (none expected beyond `api.py`).
- [ ] Step 4: `uv run pytest tests/web/test_api.py tests/web/test_server.py tests/web/test_payload.py tests/commands -q`; `uv run mypy`.
- [ ] Step 5: Commit: `web: /api/balances and /api/plans retired; setup, timeline and venues serve /audit`.

### Task 3: The client -- `auditView`

**Files:** `render.js`, `main.js`, `docs.js`, `staticfiles.py`, `index.html`, `keel.css`, `tests/web/test_client_assets.py`, `tests/web/test_view_sections.py`, `tests/web/test_pwa.py` (stopped-view pin :755-770 if it names `SETUP_ROUTE`), `tests/web/test_doc_links.py`

- [ ] Step 1: Tests.
  - `tests/web/test_view_sections.py`: every `setupView(data, sectionOf(route), onSectionFor(route))` mention → `setupSection(setup, sectionOf(route), onSectionFor(route))`; `test_the_job_panel_is_page_level_and_not_inside_any_section` (:577) keeps holding for `setupSection`; `_SETUP_KEYS` parity (:642) runs against `_view_keys("auditView", "setup")`.
  - `tests/web/test_client_assets.py`: `_VIEW_ENDPOINTS` → `("auditView", "setup", "/api/setup")`, `("auditView", "timeline", "/api/timeline")`, `("auditView", "venues", "/api/venues")` (remove `setupView`, `timelineView`, `balancesView`, `venuesView`); `_ROW_ENDPOINTS`: `("auditView", "timeline", "rows", "/api/timeline", "orders")`; `_ROW_ENDPOINTS_UNCOVERED`: the former `setupView`/`venuesView` entries re-keyed to `auditView` (`venues` is no longer mapped at all -- drop it; keep `readiness`). `test_the_route_table_parser_actually_found_something`: floor `>= 4`, `"pulse" in names`. New (in `tests/web/test_pulse_view.py` or a new `test_audit_view.py`): `test_the_nav_is_exactly_the_four_views_and_docs` (`_js_route_names() == ["pulse", "holdings", "rules", "audit"] == list(CLIENT_ROUTES)`; nav hrefs parsed from `index.html` equal `["/pulse", "/holdings", "/rules", "/audit"]`; exactly one `<li>` after them carries `id="docs-link"`); `test_the_stopped_view_lands_on_audit` (`_decl(main, "SETUP_ROUTE")` is `ROUTES.find((route) => route.name === "audit") ?? DEFAULT_ROUTE`); `test_audit_reads_setup_first_so_a_fresh_machine_sees_the_checklist` (the `audit` route's `endpoints[0] == "setup"`); `test_the_timeline_defaults_to_keel_only` (`_code_only(main)` contains `paramsFor("timeline").kind = "keel"` exactly once, in the boot block that seeds `dca-plan` :253); `test_audit_places_its_three_sections_once` (`_function_body(render, "auditView")` calls `timelineSection(`, `setupSection(`, `readinessSection(` once each, in that order by index); `test_the_write_token_is_remembered_on_the_audit_mount` (`mount`'s audit branch calls `remember(` once -- it was the setup branch's job); `test_the_readiness_table_is_the_only_venue_table` (`readinessSection` has exactly one `table(` call and `venueCard`/`h-declared`/`h-venues` are no longer declared); `test_the_terms_table_lost_the_venue_session_label` (`"session"` not in `docs.TERMS` -- `test_doc_links.py::test_every_linked_label…` enforces the other direction).
- [ ] Step 2: Run; red.
- [ ] Step 3: Implement. `render.js`: `export function auditView(setup, section, onSection, timeline, timelineSort, onTimelineSort, onKind, venues)` -- h1 "Audit & Setup"; `timelineSection(timeline, …)`: when `timeline` is `null` → `el("p", "empty", "The timeline could not be read -- keel isn't running here yet.")`, else the body of `timelineView` (:1384-1478) minus its h1 (chip bar from `data.kinds` -- the server now lists `keel` first; the summary card; the CSV link with the page's scope and kind; the chain verdict; the `h-timeline` table); `setupSection(setup, section, onSection)`: `setupView`'s body minus the h1 (heading `h-setup`, the summary card, `sectioned(data, SETUP_SECTIONS, …)`, the page-level `jobPanel`); `readinessSection(venues)`: when `null` → an empty line, else the `h-readiness` table from `venuesView` (:3511-3548) under heading "Venue readiness". Delete `setupView`, `timelineView`, `balancesView`, `venuesView`, `venueCard`, `plansView`, `claimList`, `citation`. `main.js`: `ROUTES = [pulse, holdings, rules, { name: "audit", label: "Audit & Setup", endpoints: ["setup", "timeline", "venues"] }]`; `SETUP_ROUTE = ROUTES.find((route) => route.name === "audit") ?? DEFAULT_ROUTE` (rename the comment: "where 'set keel up on this machine' points -- the Audit & Setup view"); `mount`'s audit branch: `remember(data); const timeline = readings[1]; const venues = readings[2]; return auditView(data, sectionOf(route), onSectionFor(route), timeline ? timeline.data : null, timeline ? timeline.sort : null, sorter(route, "timeline"), (kind) => { paramsFor("timeline").kind = kind; void paint(route, true, true); }, venues ? venues.data : null);`; the boot block that seeds `dca-plan` from the address also sets `paramsFor("timeline").kind = "keel";` (comment: the console's default is keel's own record; `all` is one chip away; the CLI default is untouched). Remove the `setup`/`timeline`/`balances`/`venues`/`plans` branches and imports. `docs.js::TERMS`: remove `session`. `staticfiles.CLIENT_ROUTES = ("pulse", "holdings", "rules", "audit")`; `index.html` nav: four `<li>` + Docs; update the nav comment ("the last entry is a link, not a view" -- now the fifth). `keel.css`: nothing required; delete rules whose only selectors were on removed markup (`grep` each class removed from `render.js` against `keel.css`).
- [ ] Step 4: `node --check` on `main.js`, `render.js`, `docs.js`; full suite; mypy; browser check: unregister the worker; `/` → `/pulse`; the four routes + deep-link reloads; `/audit` opens on keel-only rows with `blocked` in each cycle row, `trade`/`flow` chips re-read the server, Export CSV downloads the keel-only file; the Setup section shows the three stage tabs and the job panel; the readiness table; THEN stop the server, point it at an empty directory (`mkdir $SCRATCH/empty && uv run keel --config $SCRATCH/empty/config.yaml --db $SCRATCH/empty/keel.db serve`), load `/pulse` → "keel isn't running" → "Set keel up on this machine" → lands on `/audit` with the checklist and actions; run one idempotent action (e.g. the config-writing step) from the browser and confirm the outcome line survives the repaint.
- [ ] Step 5: Commit: `console: Audit & Setup view; Timeline, Setup, Venues, Balances and Plans retired`.

### Task 4: Docs

- [ ] `docs/operator-runbook.md` console section: the final four views, one paragraph each (Pulse: state + today + why-not + next DCA + HALT; Holdings; Rules; Audit & Setup: keel-only timeline default, CSV, setup, readiness); `README.md:247` unchanged from PR1; `docs/architecture.md:21`: "Local web console -- `keel serve`, loopback-only, zero-dependency static UI: four engine views (Pulse, Holdings, Rules, Audit & Setup)". Commit: `docs: the four console views`.

### PR4 verification
Command block as PR1 (touched JS: `main.js`, `render.js`, `docs.js`); browser check as Task 3 Step 4 including the empty-machine path.

### PR4 rulings
- Global rulings 14 (setup is Audit's primary), 15 (`kind=keel` composite, client-seeded), 16 (`/api/venues` kept whole), 17 (`plans.py` deleted).
- **`keel balances` (CLI) and `keel/commands/balances.py` stay; only the web payload goes** — why: CLI commands are out of scope; the service is theirs — cost if wrong: none.
- **The Setup stage tabs keep using `sectioned()` inside the Audit page** — why: the three runbook stages are pinned (`test_view_sections.py:180-195`) and tabs inside a section are the same control the page already has — cost if wrong: a nested tab bar on one page.
- **The `system` row text changes (`blocked` added) and therefore the CSV `summary` column text changes** — why: the brief asks for `blocked` on cycle rows and the CSV is built from the same rows; the CSV's COLUMNS are unchanged — cost if wrong: an auditor diffing two exports sees different summary wording for cycle rows written after this PR.

---

## Self-review against the goal (done while writing; re-run before each PR is opened)

- Nav exactly four views + Docs after PR4; `/` → `/pulse` from PR1 (`routeFor`, `DEFAULT_ROUTE = ROUTES[0]`, manifest `start_url`).
- Pulse: kill switch / autonomy / lapses / rail 11 / drawdown (PR1 Task 5 `pulseState`); today's signals/blocked/entered/errors (`pulseToday`); why-not list (`pulseWhyNot`, service Task 2); last cycle time (`last_cycle_at`); next DCA per live rule from the cadence expression (Task 1/2); freshness + market session; missing-bracket warnings (`bracket` column + `unbracketed_count`).
- HALT: `POST /api/gates/halt` with `X-Keel-Gate` from the pulse body; one `window.confirm`; HALTED line naming `keel resume` from the payload; no resume anywhere in the client (structural pins).
- Holdings: grouped by rule, cost, entry gate, attestation, stop only when present, slippage + placement from the matched entry order; no close/cancel (pins kept and strengthened); no mark/value/unrealized/fee-detail columns.
- Rules: ledger + params + records with gate distances + research + DCA card; no buttons (existing DCA-card pins).
- Audit & Setup: keel-only default, trade/flow by chip, `blocked` in system rows, hash chain + CSV unchanged in shape, Setup steps + job panel + CSRF token, readiness; stopped view lands here.
- Removals in lockstep per PR, with every pin named above; global chrome untouched (its tests are never edited); CLI untouched (services additive; `Dca.detect` semantics preserved by a property test).
- Every PR: `node --check` on each touched JS file, real-browser load of every route past the service worker, the five CI commands.
