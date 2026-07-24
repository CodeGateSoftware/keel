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
