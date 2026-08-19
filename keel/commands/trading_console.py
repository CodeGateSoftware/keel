"""The Trading menu (issue #391 C5; PRD §3's Trading branch, O3's typed contracts).

Everything here is DISPATCH, never behavior (PRD O2, the discipline `compliance_console`
and `strategy_console` keep): the agent cycle is `agent.run_once` with the CLI's own
order-confirmation gate (`keel.commands.confirm._interactive_confirm` -- the SAME function
`keel agent` hands the executor, so an in-console cycle IS the agent pipeline and there is
no TUI-originated order path), the monitor poll is `keel.commands.monitor.monitor_cycle`,
and every state mutation goes through `keel.commands.trading`'s services with the CLI's OWN
typed gates rendered in-console (the curses suspend/restore dance) -- never piped, never
pre-filled, never bypassed. The gate wording and the confirmation lines are IMPORTED from
`trading.py` (their one home, the C1 style), so the two front-ends cannot drift.

The asymmetries are mirrored honestly (O3):

* `kill` ENGAGES a halt -- the CLI is one command with NO confirmation, and the console
  adds no ceremony of its own: selecting the entry dispatches `engage_kill_switch` and
  toasts the CLI's own line.
* `resume`/`resume-entries`/`reset-hwm`/`record-flow` RELEASE halts -- each keeps the
  CLI's typed `_require_interactive_confirmation` gate, verbatim, failing closed.
* `autonomy`'s ON direction is the CLI's own arm gate (extracted to
  `keel.commands.autonomy.autonomy_on_gate`); OFF only ever reduces capability and stays
  ungated, exactly as `keel autonomy off` does.

The session honesty on the cycle's confirm step is DISPLAY of B1 semantics, never new
logic: the plan reads the RECORDED session (`agent.latest_recorded_session`) and says,
for a session-bound venue whose record says CLOSED, that the cycle will skip with
`market_closed` (the clock-unavailable case gets its own line); running it anyway renders
the skip's logged reason verbatim through `render_loop_result`.

All the pure builders here are directly unit-testable without curses, mirroring the
`build_*`/`run_*` split of the other console modules.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from keel import agent
from keel.commands.monitor import MonitorCycle, monitor_cycle
from keel.commands.trading import (
    KILL_ENGAGED_LINE,
    RECORD_FLOW_DETAIL,
    RESET_HWM_ACTION,
    RESET_HWM_DETAIL,
    RESET_HWM_DONE_LINE,
    RESUME_ACTION,
    RESUME_DETAIL,
    RESUME_DISENGAGED_LINE,
    RESUME_ENTRIES_ACTION,
    RESUME_ENTRIES_CLEARED_LINE,
    RESUME_ENTRIES_DETAIL,
    clear_consecutive_loss_halt,
    disengage_kill_switch,
    engage_kill_switch,
    parse_flow_amount,
    record_flow,
    record_flow_action,
    render_blocked_entries,
    render_flow_recorded,
    render_loop_result,
    reset_high_water_mark,
)
from keel.commands.tui import CTRL_C_DISCLOSURE, ScreenLine, _blank, _message_style
from keel.types import Granularity

if TYPE_CHECKING:
    from keel.config import Config
    from keel.data.repository import Repository

#: One terminal prompt: injected so every form is unit-testable with a scripted fake, and
#: so the live loop can run the whole form through the curses suspend/restore dance.
PromptFn = Callable[[str], str]

#: The width every console line must fit (`_paint` clips at the window width; 80-column
#: terminals are this dashboard's stated target) -- the same budget the other console
#: modules keep, applied by wrapping rather than clipping.
_WIDTH = 78


def _wrap(text: str, *, indent: str = "  ", width: int = _WIDTH) -> list[str]:
    """Wrap `text` on spaces to the 80-column budget, continuation lines carrying `indent`.
    PURE -- the same rule every console module keeps, over `textwrap` so a fact can never
    lose its tail to `_paint`'s clip."""
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [
        indent
    ]


# -- the sub-menu model (PRD §3's Trading branch) --------------------------------------------------


@dataclass(frozen=True)
class TradingEntry:
    """One entry of the Trading sub-menu. `kind` is the closed dispatch vocabulary:
    `"armed"` opens an ARMED view (Enter is the confirm step -- the agent cycle and the
    monitor poll), `"form"` runs a service at the terminal through the suspend/restore
    dance (a typed gate, a prompt, or both), and `"action"` dispatches immediately (kill
    -- per its own CLI contract, no ceremony). `typed` marks the entries whose write
    carries a typed confirmation (the CLI's own `_HALT_COMMANDS`, plus autonomy's ON)."""

    ordinal: int
    label: str
    description: str
    kind: str  # "armed" | "form" | "action"
    target: str
    typed: bool = False


#: PRD §3's Trading branch in tree order. The descriptions are O8's plain-English "what
#: will this do" in miniature, naming the dispatch honestly (which service, which gate).
TRADING_MENU: tuple[TradingEntry, ...] = (
    TradingEntry(
        ordinal=1,
        label="agent cycle (single)",
        description=(
            "ONE agent cycle on the ACTIVE profile (Enter confirms first: it can place "
            "orders); blocks like simulate/fetch and holds the cycle's own result lines"
        ),
        kind="armed",
        target="cycle",
    ),
    TradingEntry(
        ordinal=2,
        label="monitor poll (single)",
        description=(
            "ONE poll: record the venue session, skip while a session-bound venue is "
            "closed, else fetch fresh candles for every allowlisted product (Enter "
            "confirms first)"
        ),
        kind="armed",
        target="monitor",
    ),
    TradingEntry(
        ordinal=3,
        label="autonomy",
        description=(
            "whether the agent places orders without asking first -- ON is typed (the "
            "CLI's own gate); OFF only ever reduces capability and is ungated"
        ),
        kind="form",
        target="autonomy",
        typed=True,
    ),
    TradingEntry(
        ordinal=4,
        label="record-flow",
        description=(
            "declare a deposit/withdrawal so rail 11 does not mistake it for P&L -- "
            "typed, with the CLI's own amount validation"
        ),
        kind="form",
        target="record-flow",
        typed=True,
    ),
    TradingEntry(
        ordinal=5,
        label="reset-hwm",
        description=(
            "clear rail 11's high-water mark so the next cycle re-seeds it -- typed"
        ),
        kind="form",
        target="reset-hwm",
        typed=True,
    ),
    TradingEntry(
        ordinal=6,
        label="resume-entries",
        description=(
            "clear an armed consecutive-loss halt (rail 16), re-permitting new entries "
            "-- the ONLY early release, typed"
        ),
        kind="form",
        target="resume-entries",
        typed=True,
    ),
    TradingEntry(
        ordinal=7,
        label="kill",
        description=(
            "engage the kill-switch, halting all trading immediately -- one key, no "
            "confirmation, per the CLI's own contract (halting is the safe direction)"
        ),
        kind="action",
        target="kill",
    ),
    TradingEntry(
        ordinal=8,
        label="resume",
        description=(
            "disengage the kill-switch; trading resumes on the next cycle -- typed"
        ),
        kind="form",
        target="resume",
        typed=True,
    ),
)


def trading_entry(ordinal: int) -> TradingEntry | None:
    """The entry selected by its displayed ordinal, or `None` -- the one-lookup rule
    every console menu keeps, so the rendered ordinals and the shortcut keys cannot
    drift."""
    for entry in TRADING_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


#: This module's screens' contextual help (O8, issue #394 C7) -- the rows the `?`
#: overlay renders, keyed by the live loop's mode names. Plain `(subject, description)`
#: pairs so the text stays HERE with the module that owns the screens;
#: `keel.commands.help_console` is the registry and renderer. Every TYPED action's row
#: states the O3 contract explicitly: the prompt cannot be pre-filled.
CONTEXT_HELP: dict[str, tuple[tuple[str, str], ...]] = {
    "trading": (
        (
            "agent cycle / monitor poll",
            "ONE cycle or ONE poll on the ACTIVE deployment -- both open ARMED with the "
            "plan shown first, and Enter is the confirm step (a cycle can place orders; "
            "in confirm mode the CLI's own order gate runs at the terminal)",
        ),
        (
            "autonomy",
            "arming lets the agent place orders unattended -- the ON direction asks the "
            "CLI's own arm gate at the terminal; OFF only ever reduces capability",
        ),
        (
            "kill / resume (the kill switch)",
            "kill ENGAGES the halt immediately, one command with no ceremony -- that IS "
            "its CLI contract; resume RELEASES it and is TYPED: you type the release "
            "phrase yourself at the terminal, and the prompt cannot be pre-filled, "
            "piped or bypassed",
        ),
        (
            "resume-entries, reset-hwm, record-flow",
            # [review #406] the typed disclosure LEADS the row: all three ARE typed
            # gates in the CLI (`_require_interactive_confirmation`), so scoping it to
            # resume-entries' parenthetical read as though the other two could be
            # pre-filled.
            "the other halt-releasers and bookkeeping, all three TYPED at the terminal "
            "-- the prompt cannot be pre-filled, piped or bypassed: resume-entries "
            "clears the consecutive-loss halt, reset-hwm resets the drawdown reference, "
            "record-flow records a deposit or withdrawal against the equity base",
        ),
    ),
    "trading-cycle": (
        (
            "the ARMED view",
            "the plan names the profile, its paper/confirm semantics, the autonomy "
            "state and the session honesty line -- Enter runs the cycle through the "
            "agent pipeline itself (there is no TUI-originated order path), blocking "
            "like a fetch, and the result lines are held here",
        ),
    ),
    "trading-monitor": (
        (
            "the ARMED view",
            "ONE monitor poll over the ACTIVE profile's products: Enter runs the same "
            "monitor cycle the CLI runs, and its result lines (or a skip's logged "
            "reason, verbatim) are held here",
        ),
    ),
}


def build_trading_menu_lines(*, cursor: int = 0, message: str | None = None) -> list[ScreenLine]:
    """The Trading sub-menu screen: every entry with its description wrapped to the
    80-column budget, the typed entries marked, exactly one cursor-marked row, and the
    last action's confirmation lines as the toast. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- trading", "heading"),
        _blank(),
    ]
    cursor = max(0, min(cursor, len(TRADING_MENU) - 1))
    for index, entry in enumerate(TRADING_MENU):
        marker = ">" if index == cursor else " "
        head = f"{marker} {entry.ordinal:>2}  {entry.label}"
        if entry.typed:
            head += "  [typed]"
        style = "heading" if index == cursor else "normal"
        lines.append(ScreenLine(head, style))
        for wrapped in _wrap(f"{entry.description}.", indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("up/k down/j move · Enter/Space select · 1-8 jump", "muted"))
    lines.append(ScreenLine("q/Esc/m to the console menu", "muted"))
    if message is not None:
        lines.append(_blank())
        for part in message.splitlines():
            lines.append(ScreenLine(part, _message_style(part)))
    return lines


# -- the agent cycle: the ARMED confirm step, the run, the held result -----------------------------


@dataclass(frozen=True)
class CyclePlan:
    """What one agent cycle WILL do, shown BEFORE any of it runs (the confirm step): the
    deployment it runs against, the mode's own semantics (paper simulates; confirm is
    live money), the autonomy state (who gets asked), and the SESSION HONESTY line
    sourced from the recorded session state -- never a TUI-side calendar."""

    db_path: str
    profile_label: str | None
    mode: str
    autonomous: bool
    session_line: str | None


def session_honesty_line(
    session_bound: bool, recorded: agent.RecordedSession | None
) -> str | None:
    """What the RECORDED session says a cycle will do on a session-bound venue, as one
    line -- or `None` when there is nothing to disclose (a 24/7 venue, or a recorded OPEN
    session). PURE display of B1 semantics (`agent.run_once`'s own gate): CLOSED means
    the cycle will skip with `market_closed`; a clock that is absent, stale or unreadable
    means the cycle reads it fresh and skips with `market_clock_unavailable` if it cannot
    (fail-closed). No new session logic is born here -- the cycle itself may find a
    different answer when it runs, and its own logged skip reason is what renders then.
    """
    if not session_bound:
        return None
    if recorded is None or not recorded.fresh:
        return (
            "no fresh recorded clock -- the cycle reads the venue clock fresh and, if it "
            "cannot, skips with market_clock_unavailable (fail-closed)"
        )
    if recorded.state == "closed":
        return "the recorded venue session is CLOSED -- the cycle will skip with market_closed"
    if recorded.state != "open":
        return (
            f"the recorded clock state is {recorded.state!r} -- the cycle will skip with "
            "market_clock_unavailable (fail-closed)"
        )
    return None


def cycle_plan(
    repo: Repository,
    config: Config,
    db_path: str,
    now_ts: int,
    *,
    profile_label: str | None,
    session_bound: bool,
    recorded: agent.RecordedSession | None,
) -> CyclePlan:
    """The plan for a console cycle run: the deployment's db, its profile label, the
    mode and the profile's CURRENT autonomy state (read fresh, never cached -- the same
    freshness `agent._effective_mode` keeps), and the session honesty line over the
    recorded session."""
    return CyclePlan(
        db_path=db_path,
        profile_label=profile_label,
        mode=config.auto_trade.mode,
        autonomous=repo.get_profile().is_autonomous(now_ts),
        session_line=session_honesty_line(session_bound, recorded),
    )


def build_cycle_armed_lines(plan: CyclePlan) -> list[ScreenLine]:
    """The cycle view's ARMED state: NOTHING has run, and the screen says exactly what
    Enter will do -- the confirm step. The ACTIVE profile and its mode's semantics lead
    (on a live-mode deployment the REAL MONEY line carries the alert style, unmistakable),
    the autonomy state says who gets asked, and the session honesty line (when there is
    one) says what the recorded session already knows will happen. PURE."""
    label = plan.profile_label or "the active deployment"
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- trading / agent cycle", "heading"),
        _blank(),
        ScreenLine("ARMED -- nothing has run yet.", "normal"),
        _blank(),
        ScreenLine(f"Enter runs ONE agent cycle on THIS deployment ({plan.db_path}):", "normal"),
    ]
    for wrapped in _wrap(f"profile: {label} · mode={plan.mode}", indent="      "):
        lines.append(ScreenLine(wrapped, "normal"))
    if plan.mode == "paper":
        for wrapped in _wrap(
            "paper mode: fills are SIMULATED -- no order ever reaches the venue; the "
            "hard rails and the paper account run exactly as `keel agent` runs them.",
            indent="      ",
        ):
            lines.append(ScreenLine(wrapped, "ok"))
    else:
        # Anything that is not `paper` reaches the executor's confirm path: REAL MONEY,
        # in the alert style the banner keeps for the live pair.
        for wrapped in _wrap(
            "mode=confirm: REAL MONEY -- live orders on this profile. Each order asks "
            "at the terminal first (the CLI's own confirm gate), every hard rail runs.",
            indent="      ",
        ):
            lines.append(ScreenLine(wrapped, "alert"))
        if plan.autonomous:
            for wrapped in _wrap(
                "autonomy is ON: orders place with NO further prompt -- who is asked "
                "changes, never what is allowed.",
                indent="      ",
            ):
                lines.append(ScreenLine(wrapped, "alert"))
    if plan.session_line is not None:
        for wrapped in _wrap(f"session: {plan.session_line}", indent="      "):
            lines.append(ScreenLine(wrapped, "warn"))
    for wrapped in _wrap(
        "the cycle can take seconds to minutes (it polls the venue); the screen freezes "
        "while it runs, exactly like the CLI, and the cycle's own result lines are held "
        "here when it ends. Enter again re-runs.",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    for wrapped in _wrap(CTRL_C_DISCLOSURE, indent="      "):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("Press q/Esc/m to return to the Trading menu.", "muted"))
    return lines


def run_agent_cycle(
    repo: Repository,
    config: Config,
    *,
    now_ts: int,
    build_broker: Callable[[], Any],
    run_fn: Callable[..., agent.LoopResult] = agent.run_once,
    confirm_fn: Callable[..., bool] | None = None,
) -> agent.LoopResult:
    """THE cycle, dispatched: `agent.run_once` itself over the active profile's
    repo/config, handing the executor the CLI's own `_interactive_confirm` gate -- the
    SAME function `keel agent` passes (`keel.commands.confirm`, one gate, two
    front-ends), so the console has no order path of its own and O3's acceptance ("no
    TUI-originated order path that bypasses the agent pipeline") holds by construction.
    `run_fn`/`confirm_fn` are injectable so the loop's tests can spy without computing."""
    from keel.commands.confirm import _interactive_confirm

    return run_fn(
        build_broker(),
        repo,
        config,
        now_ts=now_ts,
        confirm_fn=confirm_fn if confirm_fn is not None else _interactive_confirm,
    )


def build_cycle_result_lines(result: agent.LoopResult) -> list[ScreenLine]:
    """The held cycle result: `render_loop_result`'s exact lines (the shared twin the
    CLI prints, a skip's logged reason verbatim) plus the blocked-entry lines the CLI
    prints when a rule's gating bar was not confirmed ready. PURE -- the renderer is
    `trading.py`'s own; this screen never re-words a cycle."""
    lines = [
        ScreenLine("keel console -- trading / agent cycle result", "heading"),
    ]
    for line in render_loop_result(result):
        for wrapped in _wrap(line, indent=""):
            lines.append(ScreenLine(wrapped, "normal"))
    for blocked in render_blocked_entries(result):
        for wrapped in _wrap(blocked, indent=""):
            lines.append(ScreenLine(wrapped, "warn"))
    lines.append(_blank())
    for wrapped in _wrap(CTRL_C_DISCLOSURE, indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(
        ScreenLine("Enter re-runs the cycle · q/Esc/m back to the Trading menu", "muted")
    )
    return lines


# -- the monitor poll: one poll, ARMED -------------------------------------------------------------


@dataclass(frozen=True)
class MonitorPlan:
    """What one monitor poll WILL do, shown before any of it runs: the products and
    granularities the config resolves to (exactly what `keel monitor` polls), the
    interval the session record trusts, and the session honesty line."""

    db_path: str
    products: tuple[str, ...]
    granularities: tuple[Granularity, ...]
    interval_sec: float
    session_line: str | None


def monitor_plan(
    config: Config,
    db_path: str,
    *,
    session_bound: bool,
    recorded: agent.RecordedSession | None,
) -> MonitorPlan:
    """The plan for one console poll -- `keel monitor`'s own derivation (the allowlist's
    products in the settlement currency, the config's granularities, the config's
    interval), plus the same session honesty line the cycle plan carries (a poll skips
    while a session-bound venue is closed)."""
    from keel.commands._products import _default_sim_products

    return MonitorPlan(
        db_path=db_path,
        products=tuple(_default_sim_products(config)),
        granularities=tuple(config.market_data.granularities),
        interval_sec=float(config.auto_trade.interval_sec),
        session_line=session_honesty_line(session_bound, recorded),
    )


def build_monitor_armed_lines(plan: MonitorPlan) -> list[ScreenLine]:
    """The monitor view's ARMED state: NOTHING has run, and the screen says exactly what
    one poll does -- record the venue session, skip while a session-bound venue is
    closed, else fetch fresh candles for every product. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- trading / monitor poll", "heading"),
        _blank(),
        ScreenLine("ARMED -- nothing has run yet.", "normal"),
        _blank(),
        ScreenLine(f"Enter runs ONE poll against THIS deployment ({plan.db_path}):", "normal"),
    ]
    products = ", ".join(plan.products)
    for wrapped in _wrap(f"products {products}", indent="      "):
        lines.append(ScreenLine(wrapped, "normal"))
    granularities = ", ".join(g.value for g in plan.granularities)
    for wrapped in _wrap(f"granularities {granularities}", indent="      "):
        lines.append(ScreenLine(wrapped, "normal"))
    # The interval the session record trusts -- operationally relevant (it is what
    # `keel monitor`'s own loop would sleep between polls), so the plan renders it.
    for wrapped in _wrap(
        f"interval {plan.interval_sec:g}s -- the cadence the session record trusts",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "normal"))
    for wrapped in _wrap(
        "the poll records the venue session, then either skips (a shut venue mints no "
        "bars) or fetches fresh candles -- read-only w.r.t. money, exactly `keel "
        "monitor`'s own cycle.",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "normal"))
    if plan.session_line is not None:
        for wrapped in _wrap(f"session: {plan.session_line}", indent="      "):
            lines.append(ScreenLine(wrapped, "warn"))
    for wrapped in _wrap(
        "the screen freezes while it runs, exactly like the CLI, and the cycle's own "
        "line is held here when it ends. Enter again re-polls.",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    for wrapped in _wrap(CTRL_C_DISCLOSURE, indent="      "):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("Press q/Esc/m to return to the Trading menu.", "muted"))
    return lines


def run_monitor_poll(
    repo: Repository,
    config: Config,
    *,
    now_ts: int,
    build_broker: Callable[[], Any],
    cycle_fn: Callable[..., MonitorCycle] = monitor_cycle,
) -> MonitorCycle:
    """THE poll, dispatched: `monitor_cycle` itself (the unit `keel monitor`'s loop
    repeats) over the active profile's broker/repo/config, with the CLI's own product
    and granularity derivation. `cycle_fn` is injectable so the loop's tests can spy."""
    from keel.commands._products import _default_sim_products

    return cycle_fn(
        build_broker(),
        repo,
        config,
        _default_sim_products(config),
        list(config.market_data.granularities),
        now_ts,
        float(config.auto_trade.interval_sec),
    )


def build_monitor_result_lines(cycle: MonitorCycle) -> list[ScreenLine]:
    """The held poll result: the cycle's OWN line, verbatim -- the exact line the CLI
    prints for that cycle, skip line included. PURE."""
    lines = [
        ScreenLine("keel console -- trading / monitor poll result", "heading"),
    ]
    for wrapped in _wrap(cycle.line, indent=""):
        lines.append(ScreenLine(wrapped, "normal"))
    lines.append(_blank())
    for wrapped in _wrap(CTRL_C_DISCLOSURE, indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(
        ScreenLine("Enter re-polls · q/Esc/m back to the Trading menu", "muted")
    )
    return lines


# -- the typed gates: the CLI's own, verbatim, failing closed --------------------------------------


def _clis_typed_gate(action: str, detail: str) -> bool:
    """The shared shape of every Trading-menu typed gate (O3): the CLI's OWN
    `_require_interactive_confirmation` with the CLI's OWN action/detail wording --
    imported from `trading.py`/`autonomy.py`, their one home, so the console and the CLI
    can never drift into two ceremonies for one release. The console wraps it in the
    curses suspend/restore dance so the prompt renders in-console; the gate itself is
    untouched -- never pre-filled, never piped. Fails CLOSED: a wrong phrase, a Ctrl-C,
    any exception answers False and the halt stays."""
    from keel.commands._common import _require_interactive_confirmation

    try:
        _require_interactive_confirmation(action, detail)
        return True
    except Exception:
        return False


def clis_typed_resume_gate() -> bool:
    """`keel resume`'s typed gate, called verbatim."""
    return _clis_typed_gate(RESUME_ACTION, RESUME_DETAIL)


def clis_typed_resume_entries_gate() -> bool:
    """`keel resume-entries`'s typed gate, called verbatim."""
    return _clis_typed_gate(RESUME_ENTRIES_ACTION, RESUME_ENTRIES_DETAIL)


def clis_typed_reset_hwm_gate() -> bool:
    """`keel reset-hwm`'s typed gate, called verbatim."""
    return _clis_typed_gate(RESET_HWM_ACTION, RESET_HWM_DETAIL)


def clis_typed_record_flow_gate(amount: str) -> bool:
    """`keel record-flow`'s typed gate, called verbatim -- the action phrase names the
    RAW amount (sign included), because the exact rebase is what is being confirmed."""
    return _clis_typed_gate(record_flow_action(amount), RECORD_FLOW_DETAIL)


def clis_autonomy_on_gate(config: Config) -> bool:
    """`keel autonomy on`'s OWN arm gate (`autonomy.autonomy_on_gate`, extracted from the
    CLI body -- its one home), fail-closed. The console arms with no expiry, the CLI's
    own default, so the gate's window reads "until you turn it off" exactly as the CLI's
    does."""
    import time

    from keel.commands.autonomy import autonomy_on_gate

    try:
        autonomy_on_gate(config, None, int(time.time()))
        return True
    except Exception:
        return False


# -- kill: one key, no ceremony (the CLI's own contract) -------------------------------------------


def run_kill(repo: Repository) -> str:
    """`keel kill` as a console action: ENGAGE the halt immediately -- the CLI's own
    contract is one command with no confirmation (halting is the safe direction), and no
    ceremony is added or removed here. Returns the CLI's own line, verbatim."""
    engage_kill_switch(repo)
    return KILL_ENGAGED_LINE


# -- the forms -------------------------------------------------------------------------------------


def run_resume_form(repo: Repository, *, gate_fn: Callable[[], bool] | None = None) -> str:
    """`keel resume` as a form: the CLI's own typed gate, then `disengage_kill_switch`
    and the CLI's own line. A declined gate means not a single state row is written."""
    if gate_fn is None:
        gate_fn = clis_typed_resume_gate
    if not gate_fn():
        return "resume cancelled -- typed confirmation not given; the halt stays engaged"
    disengage_kill_switch(repo)
    return RESUME_DISENGAGED_LINE


def run_resume_entries_form(
    repo: Repository, *, gate_fn: Callable[[], bool] | None = None
) -> str:
    """`keel resume-entries` as a form: the CLI's own typed gate, then
    `clear_consecutive_loss_halt` and the CLI's own line. A declined gate writes
    nothing -- rail 16's halt and the loss counter both stay exactly as they were."""
    if gate_fn is None:
        gate_fn = clis_typed_resume_entries_gate
    if not gate_fn():
        return "resume-entries cancelled -- typed confirmation not given; the halt stays armed"
    clear_consecutive_loss_halt(repo)
    return RESUME_ENTRIES_CLEARED_LINE


def run_reset_hwm_form(repo: Repository, *, gate_fn: Callable[[], bool] | None = None) -> str:
    """`keel reset-hwm` as a form: the CLI's own typed gate, then `reset_high_water_mark`
    and the CLI's own line. A declined gate writes nothing."""
    if gate_fn is None:
        gate_fn = clis_typed_reset_hwm_gate
    if not gate_fn():
        return "reset-hwm cancelled -- typed confirmation not given; the mark is untouched"
    reset_high_water_mark(repo)
    return RESET_HWM_DONE_LINE


def run_record_flow_form(
    repo: Repository,
    prompt_fn: PromptFn,
    *,
    gate_fn: Callable[[str], bool] | None = None,
) -> str:
    """`keel record-flow` as a form, in the CLI's own ORDER: ask the amount, run the
    typed gate (naming the RAW amount the operator typed), THEN validate with the CLI's
    own messages, then declare the flow through `record_flow` and render the CLI's own
    lines. A declined gate or an invalid amount writes nothing."""
    if gate_fn is None:
        gate_fn = clis_typed_record_flow_gate
    raw = prompt_fn(
        "signed flow in quote currency (positive = deposit, negative = withdrawal) -- "
        "empty cancels"
    ).strip()
    if not raw:
        return "record-flow cancelled -- nothing recorded"
    if not gate_fn(raw):
        return "record-flow cancelled -- typed confirmation not given; nothing recorded"
    try:
        parsed = parse_flow_amount(raw)
    except ValueError as exc:
        return f"Error: {exc}"
    hwm = record_flow(repo, parsed)
    return "\n".join(render_flow_recorded(parsed, hwm))


def run_autonomy_form(
    repo: Repository,
    config: Config,
    prompt_fn: PromptFn,
    now_ts: int,
    *,
    arm_gate: Callable[[], bool] | None = None,
) -> str:
    """`keel autonomy` as a form, with the CLI's own asymmetry: `on` RELEASES the
    confirm prompt, so it demands the CLI's own typed gate (`clis_autonomy_on_gate`
    unless a test injects its own) and arms with NO expiry -- the CLI's own default;
    `off` only ever reduces capability and is ungated, exactly as `keel autonomy off`
    is. Both directions' result lines are the CLI's own."""
    if arm_gate is None:
        arm_gate = lambda: clis_autonomy_on_gate(config)  # noqa: E731 -- closes over config
    answer = prompt_fn(
        "turn autonomy on or off? (on/off) -- empty cancels"
    ).strip().lower()
    if answer not in ("on", "off"):
        return "autonomy cancelled -- nothing changed"
    if answer == "off":
        repo.set_autonomous(False, now_ts)
        from keel.commands.autonomy import AUTONOMY_OFF_LINE

        return AUTONOMY_OFF_LINE
    if not arm_gate():
        return "autonomy cancelled -- typed confirmation not given; autonomy stays off"
    # The CLI's own call shape (`autonomy_on` passes the expiry explicitly, `None` for
    # the never-lapses default -- mirrored, not re-derived).
    repo.set_autonomous(True, now_ts, expires_ts=None)
    from keel.commands.autonomy import render_autonomy_on

    return "\n".join(render_autonomy_on(None))
