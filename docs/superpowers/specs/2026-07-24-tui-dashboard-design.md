# `keel tui` — live read-only operator dashboard

**Status:** design → implement (this branch). **Date:** 2026-07-24.

## Motivation

The funded paper-forward (5-trend Turtle, $10k + $500/mo) is now live and will accrue trades
toward the n=100 evidence floor over *months*. Watching it means re-running `keel status` by hand.
`keel status` (PR #138) was deliberately built as the *substrate* for a TUI: `gather_status(...)
-> StatusReport` is a pure, broker-free report and `keel status --json` is its forward-compatible
shape. This spec adds the auto-refreshing full-screen dashboard that substrate was for.

The user named "TUI" explicitly as the next feature to build. It is unblocked *right now* — it does
not require the monorepo split or a `keel-client` protocol; a read-only, single-process dashboard
sits directly on `gather_status`.

## Non-goals

- **No actions.** The TUI is strictly read-only, exactly like `keel status` — it NEVER touches the
  broker or network and cannot confirm/kill/arm anything. Acting from a TUI (confirm a pending
  order, kill/resume) is a separate, larger feature that needs its own gating design.
- **No streaming / event push.** Poll-and-repaint on an interval, not `stream_events()`.
- **No new runtime dependency.** The project is deliberately stdlib-conservative (hand-rolled
  indicators over numpy, hand-rolled Decimal metrics, declined the quant stack). The TUI uses
  stdlib **`curses`** only. No `rich`/`textual`.
- **No multi-process health.** A TUI that can tell a hung `keel agent` from a healthy one needs the
  `app_health` table from the monorepo spec, which does not exist. Out of scope.

## Design

`keel/commands/tui.py`, mirroring the two-layer shape of `keel/commands/status.py`:

### 1. Pure screen model (the testable core)

```python
@dataclass(frozen=True)
class ScreenLine:
    text: str
    style: str   # one of: "heading" | "normal" | "ok" | "alert" | "warn" | "muted"

def build_screen(report: StatusReport, now_ts: int) -> list[ScreenLine]: ...
```

`build_screen` turns a `StatusReport` into styled rows. It reuses the *report* from
`gather_status` — it must not re-derive any status logic (Rail 11, freshness, etc.). Sections,
in order, each with semantic styling:

- **Title / mode** — `keel · <mode> mode` + a formatted `now_ts`. `heading`.
- **Kill switch** — `alert` (red) when engaged, `ok` (green) when clear.
- **Autonomy** — `alert` when ON (orders placed without asking), `muted` when off; the
  lapsed/lapses-at sub-line as `muted`; the profile-unreadable warning as `warn`.
- **Equity / drawdown / Rail 11** — HWM, total & weekly drawdown vs their ceilings, and the
  Rail 11 line styled by `report.rail11_status`: `HALTED`→`alert`, `unknown`→`warn`, `ok`→`ok`.
  In paper mode, the `paper_cash_usdc` line.
- **Open positions** — a header line then one row per position (id, product, qty, entry,
  age, rule); a position with `has_bracket=False` renders its bracket note as `warn`.
- **Rules** — the status counts line, then each live rule (`live` rules styled `alert`-ish/normal
  since a live rule means real money can move).
- **Data freshness** — one row per product; each styled by staleness via `_freshness_style`
  (see below): fresh→`ok`, stale→`warn`, no-data→`warn`.
- **Subscriptions** — one row per venue.
- **Footer** — `q quit · refreshing every Ns · read-only (no broker)`. `muted`.

`_freshness_style(granularity: str | None, age_sec: int | None) -> str`: a pure helper. No local
data or unknown granularity → `warn`. Otherwise compare `age_sec` to the granularity's own period
(a daily series older than ~2 days is stale): `age_sec > 2 * period_seconds(granularity)` → `warn`,
else `ok`. Keep the period lookup a small dict keyed by `Granularity.value`.

### 2. Rendering + loop (thin I/O)

- `render_plain(report, now_ts) -> list[str]` — the `ScreenLine.text` values only (styles
  dropped). Drives `--once` and any non-tty use; directly testable.
- `_paint(stdscr, lines: list[ScreenLine]) -> None` — paint styled lines into a curses window,
  mapping each style to a curses attribute (bold/colour), truncating to the window width and
  clipping to its height so a small terminal never raises. Tested against a *fake* stdscr that
  records `addstr` calls — no real terminal needed.
- `run_once(open_state, now_fn, echo) -> None` — `report = gather_status(*open_state())`; echo
  `render_plain`. `open_state: Callable[[], tuple[Repository, Config]]`, `now_fn: Callable[[],
  int]`, `echo: Callable[[str], None]` — all injectable, so `run_once` is testable with fakes and
  no CliRunner/terminal.
- `run_live(open_state, now_fn, interval) -> None` — `import curses` *lazily inside the function*
  (keeps the module importable where curses is absent, and keeps `build_screen` tests portable),
  then `curses.wrapper` a loop: poll `gather_status`, `_paint`, `getch` with a timeout of
  `interval` seconds; quit on `q`/`Q`/Ctrl-C. **Re-open the repo each poll** (via `open_state`) so
  the dashboard reflects writes committed by a separate `keel agent` process.

### 3. CLI

`keel tui`, registered in `keel/cli.py` via `cli.add_command(tui_cmd)`:

```
--interval FLOAT   seconds between refreshes (default 5.0; must be > 0)
--once             render a single frame to stdout and exit (no curses; for pipes/CI)
```

`open_state` closes over `_open_repo(ctx)` / `_load_cfg(ctx)` from `keel.commands._common`, called
fresh each poll. Default (interactive) path calls `run_live`; `--once` calls `run_once` with
`click.echo`. No disclaimer footer in the live loop (it owns the screen); `--once` may print the
disclaimer after the frame, matching `status`'s scripting-friendliness.

## Testing (TDD)

Unit tests in `tests/commands/test_tui.py`, driven by `StatusReport` fixtures (reuse the shapes in
`tests/commands/test_status.py`):

1. `build_screen` includes mode, kill-switch, HWM/drawdown, each open position, rule counts, each
   freshness row, subscriptions.
2. Style logic: kill-switch engaged → `alert`; Rail 11 `HALTED`→`alert`, `unknown`→`warn`,
   `ok`→`ok`; autonomy ON → `alert`; a bracket-less position → a `warn` line; stale freshness →
   `warn` via `_freshness_style` (parametrised over granularity/age).
3. `render_plain` returns the same text as `build_screen`'s lines (styles stripped).
4. `_paint` against a fake stdscr: does not raise on a tiny window (clips), maps styles to attrs,
   writes each visible line.
5. `run_once` with a fake `open_state`/`now_fn` echoes a full frame.
6. CLI: `CliRunner` invokes `keel tui --once --db <temp> --config <temp>` against a real temp DB and
   prints a frame; `--interval 0` is rejected.

Acceptance: `uv run pytest -q` green (test count up), `uv run ruff check` clean, and `keel tui
--once` prints a coherent dashboard against the real `keel.db`.

---

## v2 — human-readable time + interactive controls (2026-07-24)

Two follow-up asks: (1) show human-readable timestamps instead of raw unix seconds; (2) make the
dashboard *interactive* — a browsable help menu plus a few actions (toggle autonomy, fetch data,
refresh now). (2) intentionally relaxes v1's "strictly read-only" contract, so the safety design
below is the important part.

### Human-readable time

- New pure helper `_human_dt(ts: int) -> str` → local-time `YYYY-MM-DD HH:MM:SS` (via
  `time.localtime`/`strftime`). Applied to the title `now=`, each position's `opened_at=`, and the
  autonomy `lapses at`/`LAPSED at` timestamps. Freshness keeps `_human_age` (relative "4h ago").
- Testable deterministically by asserting it equals `time.strftime(fmt, time.localtime(ts))` for a
  fixed ts (machine-tz-independent).

### Interactive layer (live loop only; `--once` stays a static snapshot)

The safety contract changes from "cannot act" to **"can act, with the same asymmetric gating the
CLI already enforces"** (spec-wide §5 principle): a de-risking action is immediate; an action that
*adds* capability needs a typed-`yes` from a human. The hard rails are untouched — autonomy only
changes *who is asked*, never *what is allowed*.

Loop state: `mode` ∈ {`normal`, `help`}, `help_offset` (scroll), `message` (transient toast).

**Keybindings**
- normal: `q` quit · `h`/`?` help · `r` refresh-now · `a` toggle autonomy · `f` fetch-all-data.
- help: `↑`/`k`, `↓`/`j`, `PgUp`/`PgDn`, `Home`/`End` scroll · `q`/`Esc`/`h`/`?` close.

**Command bar** — footer becomes a keybinding hint line (pure `_footer_lines`), replacing v1's
static "q quit · read-only".

**Help overlay ("built smartly to browse")** — `build_help_screen() -> list[ScreenLine]` (pure)
lists every key, what it does, and the safety notes. The loop renders a scrolled window of it via
`_visible_slice(lines, offset, height) -> list[ScreenLine]` (pure, clamps offset) so long help
scrolls rather than truncates.

**Toast** — after any action the loop shows a one-line result (`✓ …` / `✗ …`) until the next action.

**Actions (injectable, so the logic is unit-tested without curses/network):**
- `toggle_autonomy(repo, now_ts, confirm_fn) -> str`: reads `repo.get_profile().is_autonomous(now)`.
  If ON → `set_autonomous(False, now)` immediately (de-risk, ungated) → `"autonomy → OFF"`. If OFF →
  arming: call `confirm_fn() -> bool`; on True `set_autonomous(True, now)` (no expiry, matching
  `keel autonomy on` default) → `"autonomy → ON"`, on False → `"autonomy unchanged (arming
  cancelled)"`. Fully testable with a fake repo + confirm stub.
- The real `confirm_fn` in the loop is `_confirm_arm_autonomy(stdscr)`: **suspends curses**
  (`def_prog_mode` → `endwin`), runs a cooked-mode typed-`yes` prompt (the same bar as
  `_require_interactive_confirmation`), then **restores** (`reset_prog_mode` → `refresh`). Arming
  from the TUI is thus gated exactly like `keel autonomy on`.
- Fetch: the loop paints a "fetching…" frame, then runs a closure supplied by `tui_cmd` that lazy-
  imports the fetch primitives (`_build_broker`, `history_mod.ensure_history`, `_SIM_GRANULARITIES`,
  `_DAYS_PER_YEAR` — lazy to avoid the `cli`↔`tui` import cycle) over `_default_sim_products`.
  Money-safe (data only, no orders). Wrapped by `_guarded(label, fn) -> str` which returns `fn()`'s
  message or `"{label} failed: {exc}"` — `_guarded` is unit-tested with a raising fn.

**Testing additions:** `_human_dt` format; `build_help_screen` content + `_visible_slice`
clamping/scrolling; `_footer_lines` hints; `toggle_autonomy` both directions × confirm True/False
(fake repo); `_guarded` success + failure. The curses loop, `_confirm_arm_autonomy`, and the live
network fetch closure stay thin I/O (smoke only).

---

## v3 — live "available to buy" balance (2026-07-24)

Show how much of the settlement currency (`config.quote_currency`, e.g. USDC) is available in the
real account to fund buys, refreshed periodically so deposits/sells/buys are reflected. This is a
LIVE broker read (like `f` fetch, it crosses the no-network line — money-safe: `get_accounts` only).

- Reuse `keel.execution.executor._fetch_available_quote(broker, config.quote_currency)` — the exact
  live balance rail 13 funds a buy against — so the TUI and the rail never disagree.
- `AvailableBalance(amount: Decimal | None, quote: str, updated_ts: int | None, error: str | None)`
  (frozen). `_available_lines(available) -> list[ScreenLine]` (pure): None → []; amount set → an
  "ok" line `available to buy: {amount:,.2f} {quote}  (live account, {HH:MM:SS})`; unreadable →
  a "warn" line `available to buy: unavailable — {error}`. Rendered by `build_screen` via a new
  keyword-only `available=None` param placed in the money section (so `--once`/existing tests,
  which pass no `available`, are unchanged and stay network-free).
- `_refresh_balance(open_state, now_fn, balance_fn) -> AvailableBalance` (injectable/testable):
  `balance_fn(config) -> Decimal | None`; returns an `AvailableBalance`, fail-soft (any exception →
  an error balance, never crashes the loop). In `run_live` the real `balance_fn` is
  `lambda cfg: _fetch_available_quote(_build_broker(cfg), cfg.quote_currency)` (lazy-imported).
- `run_live` refreshes the balance on a SLOW cadence (`_BALANCE_REFRESH_SEC = 30`, not every 5s
  repaint) and on `r`/after `f`, caches the last value, and passes it to `build_screen`. `--once`
  stays DB-only (no network); the live balance is a live-loop feature.
- Tests: `_available_lines` (three cases + style), `build_screen` with/without `available`,
  `_refresh_balance` (value / None / raising `balance_fn`). Broker wiring stays thin I/O.
