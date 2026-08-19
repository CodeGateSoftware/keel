"""The trading-control state services behind `keel kill`/`resume`/`resume-entries`/
`keel record-flow`/`keel reset-hwm` -- and the agent cycle-line renderer.

Issue #387 C1 (the TUI-operator-console PRD, O2): each of those command bodies carried its
state mutation inline in `keel/cli.py`, so a second front-end (the TUI's Trading menu, C5)
would have had to re-implement the exact key sequences that release a halt or rebase the
high-water mark. The MUTATIONS live here now; the typed-confirmation prompts stay in the
front-end (PRD O3: the prompt is the terminal's business -- the TUI renders its own -- while
WHAT the confirmed action does to the DB is this module's, and is identical either way).

`render_loop_result` is the pure renderer of `agent.LoopResult` -- the exact lines `keel agent`
prints per cycle -- kept beside the state services so both front-ends show a cycle identically.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from keel import agent
from keel.data.repository import Repository
from keel.execution import equity as equity_mod

# -- the typed gates' wording, and the output lines (their ONE home) ------------------------------
#
# These strings used to live inline in `keel/cli.py`'s command bodies. The TUI's Trading
# menu (issue #391 C5) renders the SAME typed prompts and the SAME confirmation lines, and
# two front-ends printing one ceremony out of two copies is exactly the drift O3 forbids --
# so the wording moved here, the same way `withdrawals.py` owns its gate's wording. The CLI
# imports and prints these; the console imports and renders these; neither re-words them.

#: `keel resume`'s typed-confirmation phrase pair.
RESUME_ACTION = "disengage the kill-switch"
RESUME_DETAIL = "Trading resumes immediately: the agent may place orders on its next cycle."
#: `keel resume-entries`'s typed-confirmation phrase pair (rail 16's early release).
RESUME_ENTRIES_ACTION = "clear the consecutive-loss halt (rail 16)"
RESUME_ENTRIES_DETAIL = "New entries are re-permitted; the loss counter is reset with it."
#: `keel reset-hwm`'s typed-confirmation phrase pair.
RESET_HWM_ACTION = "reset rail 11's high-water mark"
RESET_HWM_DETAIL = "Any real, unrecovered drawdown stops being visible to the rail."
#: `keel record-flow`'s typed-confirmation detail; the ACTION carries the amount (see
#: `record_flow_action`) because the operator must confirm the exact number, sign included.
RECORD_FLOW_DETAIL = (
    "A wrong amount or sign here silently mis-states drawdown from now on "
    "(positive = deposit, negative = withdrawal)."
)


def record_flow_action(amount: str) -> str:
    """`keel record-flow`'s typed-confirmation action phrase, naming the RAW amount the
    operator typed (sign included) -- the thing being confirmed is that exact rebase."""
    return f"rebase rail 11's high-water mark by {amount}"


def parse_flow_amount(raw: str) -> Decimal:
    """The `--amount` validation `keel record-flow` performs, ONE implementation: raises
    `ValueError` with the CLI's exact message text (the CLI wraps it in a
    `click.BadParameter`; the console renders it verbatim). `Decimal("nan")`/`("inf")`
    parse without raising, so the finite check is load-bearing -- NaN written into the
    high-water mark poisons it permanently (every later `equity > hwm` is False)."""
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"--amount must be a number, got {raw!r}") from None
    if not parsed.is_finite():
        raise ValueError(f"--amount must be a finite number, got {raw!r}")
    return parsed


#: The line `keel kill` prints -- one key, no ceremony, by that command's own contract.
KILL_ENGAGED_LINE = "kill-switch ENGAGED: all trading halted."
#: The line `keel resume` prints once the typed `yes` released the halt.
RESUME_DISENGAGED_LINE = "kill-switch disengaged: trading resumed."
#: The line `keel resume-entries` prints once rail 16's halt is cleared.
RESUME_ENTRIES_CLEARED_LINE = "consecutive-loss breaker cleared: new entries permitted."
#: The line `keel reset-hwm` prints once the mark is cleared.
RESET_HWM_DONE_LINE = "equity high-water mark reset: it will re-seed from the next cycle's equity."


def render_flow_recorded(amount: Decimal, hwm: Decimal | None) -> list[str]:
    """The lines `keel record-flow` prints after the write -- the shared twin every
    front-end shows, so a declared flow reads identically wherever it renders."""
    if hwm is None:
        return [
            f"flow of {amount} recorded. No high-water mark yet -- the next cycle will seed it "
            "from observed equity, which already includes this flow."
        ]
    return [f"flow of {amount} recorded. High-water mark rebased to {hwm}."]


def render_blocked_entries(result: agent.LoopResult) -> list[str]:
    """The blocked-entry lines `keel agent` prints (to stderr) when a cycle withheld a
    rule's entry because its gating bar was not confirmed ready -- the same facts both
    front-ends must be able to state, from `LoopResult`'s own recorded reasons."""
    return [
        f"blocked: {blocked.rule_name} on {blocked.product} needs a confirmed "
        f"{blocked.granularity.value} bar at {blocked.expected_ts} "
        f"(have {blocked.stored_ts}, reason={blocked.reason})"
        for blocked in result.blocked_entries
    ]


def engage_kill_switch(repo: Repository) -> None:
    """Engage the kill-switch, halting all trading immediately. A safe action, always allowed."""
    repo.set_state("kill_switch", True)


def disengage_kill_switch(repo: Repository) -> None:
    """Disengage the kill-switch. DANGEROUS: the front-end must have taken the typed `yes`."""
    repo.set_state("kill_switch", False)


def clear_consecutive_loss_halt(repo: Repository) -> None:
    """Clear an armed consecutive-loss halt (rail 16), re-permitting new entries.

    This is the ONLY way to release the halt early: rail 16 reads `streak_halt_until` and never
    the threshold, so setting `money_mgmt.max_consecutive_losses: 0` disables future trips but
    does NOT clear one already armed. The rail's own violation message names the CLI command.

    The loss counter is reset alongside the halt -- leaving it at or above the threshold would
    re-arm the breaker on the very next loss, which is not what an operator clearing a halt
    means. Exits, sells and DCA are never affected by rail 16 and are unaffected here.
    """
    repo.set_state("streak_halt_until", 0)
    repo.set_state("consecutive_losses", 0)


def record_flow(repo: Repository, amount: Decimal) -> Decimal | None:
    """Declare an external deposit/withdrawal so rail 11 does not mistake it for P&L; return the
    rebased high-water mark (or `None` when there is not one yet).

    Equity is `cash + positions`, so money moving in or out shifts it -- but neither is a
    trading result. Because the high-water mark never falls, an unrecorded deposit ratchets it
    up and a later withdrawal of the same money then reads as a drawdown that never recovers:
    rail 11 vetoes every entry on an account that lost nothing. Declaring the flow shifts the
    HWM (and the rolling weekly peak) by the same amount, so the drawdown keeps measuring
    trading performance. The caller owns validating the amount (finite, signed) and taking the
    typed confirmation.
    """
    equity_mod.record_external_flow(repo, amount=amount)
    # Returned EXACTLY as stored -- the caller's "rebased to X" line has always printed
    # whatever the repo holds, and narrowing it here would change that message on a value
    # this function did not write.
    hwm: Decimal | None = repo.get_state("equity_high_water_mark")
    return hwm


def reset_high_water_mark(repo: Repository) -> None:
    """Reset rail 11's equity high-water mark, clearing a stuck drawdown halt.

    The HWM is MONOTONIC by design -- it never falls -- so any equity reading that was wrong or
    is no longer comparable is permanent. The common cause is not a loss at all: depositing
    ratchets the HWM up, and a later withdrawal then reads as a drawdown that never recovers.
    Without this, the only remedy is editing sqlite by hand.

    Clearing the key (rather than writing a number) lets the next cycle re-seed it from observed
    equity, which is the same path a fresh install takes. `drawdown_total_pct` is zeroed so the
    rail is not left vetoing on a stale scalar in the window before that next cycle runs.
    """
    repo.set_state("equity_high_water_mark", None)
    repo.set_state("drawdown_total_pct", Decimal("0"))
    repo.set_state("drawdown_weekly_pct", Decimal("0"))
    repo.set_state("equity_history", [])


def render_loop_result(result: agent.LoopResult) -> list[str]:
    """The exact lines `keel agent` prints for one cycle -- the shared twin every front-end
    shows, so a cycle reads identically wherever it is rendered."""
    if result.skipped:
        return [f"[{result.ts}] skipped: {result.skip_reason}"]
    entered = sum(1 for r in result.enter_results if r.placed)
    exited = sum(1 for r in result.exit_results if r.placed)
    lines = [
        f"[{result.ts}] mode={result.mode} polled={result.polled} "
        f"products={result.products} stale={result.stale_products} "
        f"signals={len(result.enter_signals)} blocked={len(result.blocked_entries)} "
        f"entered={entered} exited={exited}"
    ]
    if result.paper_equity is not None:
        lines.append(
            f"paper equity ${result.paper_equity} | drawdown "
            f"{result.drawdown_total_pct} total / {result.drawdown_weekly_pct} weekly"
        )
    return lines
