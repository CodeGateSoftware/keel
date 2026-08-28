# 0003 — The commands layer is the explanation engine, and stays — measured, not assumed

Date: 2026-08-27 · Issue: #525 (raised in the SE walkthrough, `docs/presentations/se.md` §3 and
§9.2 — "`commands/` is 23,188 lines — bigger than the engine. Healthy separation, or a symptom?") ·
Status: decided

## Context

`keel/commands/` was filed as larger than the engine it drives (23,188 vs 17,260 lines at filing).
#525 asked for a measurement-first survey before any refactor: split the layer by role, name any
decision-making code paths living there, quantify duplication across the front-ends, and record a
decision either way. This record is that survey and that decision. The method is reproducible from
the fenced snippets at the end; every number below is from `main` at 16725c1.

**The premise dissolved before the survey ran.** #541 (PR #554) deleted the curses TUI and the
console layer reachable only from inside it: `commands/` went 24,709 → 13,552 lines (+297/−23,448
with tests). The engine grew in the same window. Restating the issue's table with today's numbers:

| module        | at filing | today  |         | module      | at filing | today  |
|---------------|----------:|-------:|---------|-------------|----------:|-------:|
| commands/     |    23,188 | 13,552 |         | strategy/   |     4,033 |  4,566 |
|               |           |        |         | sim/        |     3,375 |  3,449 |
|               |           |        |         | execution/  |     3,102 |  3,841 |
|               |           |        |         | data/       |     2,930 |  3,055 |
|               |           |        |         | research/   |     1,658 |  3,725 |
|               |           |        |         | analysis/   |     1,308 |  1,308 |
|               |           |        |         | compliance/ |       854 |    854 |
|               |           |        |         | **engine**  | **17,260** | **20,798** |
| **ratio**     |     1.34× | 0.65×  |         |             |           |        |

The CLI layer is now two-thirds the size of the compute modules combined (0.60× if `keel/agent.py`,
the live loop, is counted as engine). "Larger than the engine it drives" is no longer true; what
remains is the question the issue actually wanted answered — is what is left in the right place?

### The role split (checklist item 1)

Modules classified by dominant signal — what each imports from the compute trees, what shape its
functions have (`build_*`/`gather_*` assembly vs `render_*` formatting vs click bodies), and who
imports it back (the web API and MCP server import `keel.commands.*` as their service layer;
`keel/web/payload.py:108` states the contract: "…`keel.commands.*` is the SERVICE layer, which this
layer is allowed to read; the compute trees are not"):

| role | modules (lines) | lines | share |
|------|-----------------|------:|------:|
| **A. Shared report assembly** — `build_*`/`gather_*` cores consumed by CLI *and* web/MCP | activity 1,539 · setup 1,131 · insights 932 · doctor 849 · status 578 · brokers 307 · jobs 150 | 5,486 | 40.5% |
| **B. Services & decision orchestration** — gate sequencing, backtest/pricing passes, screening; CLI-only today | rules 1,918 · update 1,151 · trials 637 · assets 705 · simulate 564 | 4,975 | 36.7% |
| **C. Front-end glue** — arg parsing, dispatch, I/O, launchers | fetch 352 · subscription 292 · confirm 197 · trading 183 · autonomy 153 · credentials 150 · monitor 125 · serve 108 · withdrawals 92 · versions 83 · db 49 · capabilities 48 · mcp 48 | 1,880 | 13.9% |
| **D. Report assembly + rendering, CLI-only** | admission 564 · pnl 107 · purification 51 | 722 | 5.3% |
| **E. Layer plumbing** | _common 238 · _products 222 · \_\_init\_\_ 29 | 489 | 3.6% |

A line-level AST attribution of the whole layer (function bodies, by name class): 1,016 lines of
rendering functions, 3,734 of assembly/service functions, 1,292 of click command bodies, 3,320 of
module-private helpers — and 4,190 lines of module headers, docstrings and imports. That last
number is the tell: **31% of the layer is prose.** The docstrings are where the refusals explain
themselves, the gates cite their issues, and the display-vs-policy divergences are argued (e.g.
`status.py:164` spends eleven lines on why a display's `None` handling must *differ* from the
guard's). That is the explanatory surface the issue's "case that this is fine" describes, and it
does not exist anywhere else to move to.

### The decision paths (checklist item 2)

`_run_backtest`, named by #335, no longer exists: the #390 C4 / #387 C1 service extraction split it
into `resolve_rule_backtest` + `backtest_resolved` + `run_rule_backtest` (`keel/commands/rules.py`),
with the compute core in `keel.strategy.backtest`. The survey found ten further decision-bearing
paths. In every case that decides money or admission, the **policy** — the comparison that would
move funds — lives in a compute module; what lives in `commands/` is the orchestration that
sequences the gates and the refusals that explain them:

| path | location | what it orchestrates | where the policy lives |
|------|----------|----------------------|------------------------|
| `attempt_promotion` | `rules.py:749` (~205 lines) | THE promotion gauntlet: row refusal, `--force` bypass, lookahead veto (fail-closed), fee resolution, pooled-sibling loop, PBO load, both readings, transition | `strategy.promotion` (`can_promote`, `transition`, `next_status`), `research.bias`, `research.cscv` |
| `run_rule_backtest` / `resolve_rule_backtest` / `backtest_resolved` | `rules.py:379/338/370` | the #335 pricing path; also `trials.py:284–367`'s core | `strategy.backtest` |
| `run_rule_lookahead` (+ `_lookahead_views`, `_lookahead_warmup`, `_atr_indicator`) | `rules.py:604/460/524` | the lookahead gate harness, wired into the gauntlet by #440 C1a | `research.bias.lookahead_analysis` |
| `add_rule_row` / `seed_rules_into` | `rules.py:1634/1108` | rails 18/19 validation before any write | `commands._products`, `compliance.screen` grammar |
| `run_simulation` | `simulate.py:355` | the GO-LIVE/TRAIN-MORE pass; tier sweeps; the trials-ledger row | `sim.report.build_verdict`, `sim.portfolio_sim`, `strategy.promotion.floor_for_class` |
| `screen_product` | `assets.py:175` | THE admission routing point every candidate source must pass ("none of them can drift onto a laxer path") | `compliance.screen.screen_asset`; the live path enforces independently via `execution.guards` rail 1 |
| `admissibility_findings` | `doctor.py:363` | re-runs the executor's own sizing (`execution.sizing.size`/`spend`) across a 1.5–2× ATR band vs `caps.max_per_order_usd` | the sizing formula is the engine's, imported; only the diagnostic banding convention is local |
| `allowance_findings` / `veto_findings` / `doctor_exit_code` | `doctor.py:233/268/638` | diagnostic severity policy: rail-14 headroom, veto clustering, exit status | reads `guards._monthly_buy_spend_usd` and executor events — the rail's own math, never restated |
| `_rail11_status` | `status.py:164` | display-side rail-11 comparison | `execution.guards` rail 11; the `>=` is mirrored, the `None`→"unknown" divergence is documented as deliberate (a display must not fail-safe silently) |
| `promote_rule` | `setup.py:982` | the web's gated promotion entry (`force` hard-wired `False` — the O3 contract) | delegates to `attempt_promotion` |
| `plan_update` / `select_production_wheels` / `is_newer_version` | `update.py:424/387/234` | self-update decisions (operational, not trading) | local by nature — it installs the app, nothing else may |

The two deliberately-local comparisons (`_rail11_status`'s mirror and doctor's banding) are display
decisions, each with an inline argument for why it is not policy. No path was found where the
comparison that admits, promotes, sizes or refuses an order is computed in `commands/` rather than
delegated.

### The duplication audit (checklist item 3)

The issue's fear — four front-ends each rendering the same report — was real once: the TUI carried
its own renderings, and #554 deleted 11,103 lines of them. What remains, traced report by report
over today's three front-ends (CLI, web, MCP):

| report | shared core (one copy) | CLI projection | web/MCP projection | near-duplicate render paths |
|--------|------------------------|----------------|--------------------|------------------------------|
| status | `status.gather_status` (42 lines) | `render_human` (77) | `web/payload.py::status_payload` (42) — imports even `_human_age`/`_human_remaining` from `status.py` so the two can never disagree | **0** |
| doctor | `doctor.gather_findings` (97) | `doctor_lines` + `render_json` (15+15) | `mcp/tools.py::_doctor` (~25) — serialises the same `Finding` rows | **0** |
| purification | `compliance.purification.build_report` (engine) | `render_purification_report` (33) | `mcp/tools.py::_purification` (~12) — JSON of the same report | **0** |
| insights/journal/equity | `insights.build_*` (205 across three builders) | `render_summary`/`render_journal` (55+46) | `web/payload.py` calls the same builders | **0** |
| admission/promotion | `assets.screen_product`, `rules.attempt_promotion` | CLI-only today | web `/api/rules` reads rule *rows* (a table read), not the report | N/A — one front-end |
| backtest summary | `rules` services + `trials` | CLI-only today | — | N/A — one front-end |

Every load-bearing report is one shared core plus thin, format-specific projections (12–77 lines
each — text for terminals, JSON for pipes and browsers). There is nothing left to unify: the seam
the issue's option (b) would create already exists and is load-bearing in both directions — the web
imports the cores, and `attempt_promotion`'s second caller is the web's own setup action.

### Test parity (checklist item 3b)

`tests/commands/` is 11,031 lines and 527 test functions — more than any engine area's own suite
(execution 7,851; strategy 5,957; data 4,804; sim 4,314; research 4,180), plus `tests/web/` 6,046
and `tests/mcp/` 866 over the same cores. Two suites are architectural pins, not behaviour tests:
`test_service_parity.py` (682 lines) drives identical fixtures through the CLI command path and a
direct service call and asserts byte-equality of output and state; `test_console_thinness.py`
(764 lines) is an AST scan enforcing five rules over the presentation layer — no compute-module
imports or calls, `Decimal` arithmetic is display-only, no broker construction outside the seams.

## Options

**A — Move the decision orchestration to the engine.** Relocate `rules.py`'s service section (651
lines: `attempt_promotion` and siblings), `simulate.run_simulation`, and doctor's sizing band to
the compute trees, leaving `commands/` as pure rendering.

**B — Unify the remaining front-end rendering** behind a shared formatter layer.

**C — The shape stays**, recorded with numbers, with the one mechanical gap the survey found
closed by a pin rather than a move.

## Decision

**C. Nothing moves.** The layer is the explanation engine — 40.5% shared report assembly, 36.7%
gate orchestration whose policy is already engine-side, 13.9% glue, 5.3% CLI-only rendering — and
its size is dominated by prose and refusal wording that has no other home. Specifically:

1. **A is refused on the evidence.** Every money-deciding comparison already lives in a compute
   module (table above); what would move is sequencing and explanation, and moving it would put
   operator-facing echo sinks inside the engine — inverting the very separation that keeps the
   compute modules small (the stated benefit the issue's "case that this is fine" defends). The
   extraction A would undo was done *for* multiple front-ends (#390 C4), and its non-CLI callers
   still exist: `setup.promote_rule` (web) and `trials.py` both dispatch to `rules` services. The
   engine-invariants argument also cuts against A: the gate sequence is pinned today by
   byte-equality parity tests against the CLI a human actually runs; engine-side, those would
   become engine-style invariant tests of a sequence no engine path executes.
2. **B is moot.** Zero near-duplicate render paths remain among the load-bearing reports. The
   cores are shared, the projections are thin by design, and the thinness is *enforced* — for
   `keel/web/`. The one gap: the AST pin's glob covers `keel/web/*.py` only; `keel/mcp/*.py`
   (8 handlers, 664 lines, all thin today) is unscanned, so nothing mechanical prevents an MCP
   handler from growing compute later. That is filed as #588 — a pin extension, not a refactor.
3. **The ratio is now a feature to hold, not a question to re-ask.** If `commands/` again
   approaches engine size, the first suspect is a new front-end re-deriving reports — exactly what
   happened with the TUI — and the answer is the one #554 gave: delete the front-end, keep the
   cores.

**Standing rules this decision fixes:**

1. A compute module may grow operator-facing *explanations* only as data (reasons, verdicts,
   notes); the wording and ordering of what a human reads stays in `commands/`.
2. Any new front-end (desktop, MCP tool, remote API) consumes `keel.commands.*` services or the
   engine directly — it does not re-render another front-end's report from raw rows.
3. A decision-bearing path added to `commands/` must delegate its deciding comparison to a compute
   module and cite this record (or argue an exception in the PRD that supersedes it).

## Consequences

- **The question stops being re-asked.** "Is `commands/` too big?" now has a dated answer with a
  method: 13,552 lines, 0.65× the engine, no duplicated render paths, no engine-side decisions
  stranded in the CLI. Re-open only on a trigger below, by writing a superseding record.
- **The MCP thinness gap is the one actionable item**, filed as #588: extend
  `test_console_thinness.py`'s scan to `keel/mcp/*.py`. Today it would pass vacuously-green over
  eight thin handlers — which is the point of adding it now, before anything grows there.
- **`doctor.py`'s sizing band stays local** (`admissibility_findings`): it imports the executor's
  own `sizing.size`/`sizing.spend`, so a formula change cannot drift; only the 1.5–2× ATR banding
  convention is local, and it is a diagnostic convention, not policy.
- **Costs accepted:** the layer's size still slows grep-driven navigation versus the engine; the
  prose density (31%) is the price of refusals that explain themselves; and role classification is
  judged at module grain — a future module mixing roles should split before it blurs this table.
- **Triggers that would reopen this:** (a) `commands/` again exceeds the engine total; (b) a
  second near-duplicate render path appears for any load-bearing report; (c) an MCP handler or web
  module grows a deciding comparison of its own; (d) a fourth front-end is proposed.

### Method (reproducible)

Line counts: `wc -l keel/commands/*.py` and per-directory `find … | xargs wc -l`, on `main` at
16725c1. Role classification: an AST pass per module collecting `keel.*` imports (engine deps vs
web/MCP deps), click decorators, and `build_*`/`render_*` name shapes; cross-checked against who
imports each module back (`grep -n "from keel.commands" keel/web/*.py keel/mcp/*.py`). Decision
paths: function inventory of the ten largest modules, then reading every candidate that calls a
guard, an executor seam, or compares a money value. Duplication: for each load-bearing report,
locate the core (the function whose output both front-ends consume) and count render functions over
it. Line-level attribution: AST `end_lineno` spans bucketed by function-name class:

```python
# render / assembly / glue attribution, whole layer (function bodies only)
RENDER = r"^(render_|_human|_money|_fmt|_short_num|_clock|_stamp|_age|_describe_|_emit)"
ASSEMBLY = r"^(build_|gather_|screen_|summarise_|group_|apply_|resolve_|run_|attempt_|plan_|select_|parse_|inspect$|.*_findings)"
# click decorators on a def  ->  click-command body; spans sum vs wc -l  ->  headers/docstrings/imports
```
