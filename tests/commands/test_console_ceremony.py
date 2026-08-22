"""The typed-confirmation + live-guard adversarial audit, as TESTS (issue #392 C6; PRD
§5 C6 / O3 -- "a dedicated adversarial review of the typed-confirmation contract and
live-profile guards", encoded so the review reruns forever).

THE TABLE. Every state-mutating console action has a row in `CEREMONY`, keyed by
`(registry, target)` -- the sub-menu it is invoked from and its dispatch target -- and
carrying one or more ceremony CELLS, each naming its class:

* **typed-phrase** -- the CLI's OWN `_require_interactive_confirmation` (or the typed
  asset-code / `yes` word) runs in-console through the curses suspend/restore dance:
  never piped, never pre-filled, never bypassed, failing closed.
* **confirm-step** -- an explicit y/N (`click.confirm`-shaped): the LIVE profile's
  guard and the retry flow's promote question.
* **armed-enter** -- the ARMED views: opening (and every poll) runs NOTHING; Enter is
  the confirm step, the run blocks, and its result is held.
* **ungated-by-design** -- the CLI's own contract has no gate; each row's note says WHY
  that is the safe direction (kill halts; autonomy off reduces capability; rules add
  lands as candidate; db import is read-only w.r.t. the exchange).

Each cell also names its PROOFS -- the existing test functions that pin it (one source
of truth: this suite asserts every named proof exists, and adds compact refusal proofs
only for the cells no existing test covered). The refusal invariant threaded through:
a declined gate, a wrong phrase or a cancel writes NOTHING -- the state the write would
touch is byte-identical after.

THE TEETH (`test_the_table_covers_every_mutating_action_the_registries_dispatch`): the
mutating keys are DERIVED from the console modules' dispatch registries at runtime --
everything that is not a module's declared read-only kind needs a row. A newly added
mutating action (a form, an armed run, an immediate action, a new kind nobody
classified) FAILS the suite until it is given a ceremony row; a stale row for a removed
action fails the same equality.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from keel.commands import (
    account_console,
    compliance_console,
    data_console,
    help_console,
    strategy_console,
    trading_console,
)
from keel.commands import (
    console as console_mod,
)
from keel.commands.tui import run_live
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW_TS = 1_800_000_000

#: The ceremony classes -- a closed vocabulary; a row outside it fails its own test.
CLASSES = ("typed-phrase", "confirm-step", "armed-enter", "ungated-by-design")


@dataclass(frozen=True)
class Cell:
    """One ceremony a mutating action carries: its class, the plain statement of what
    the ceremony IS (or why its absence is the safe direction), and the existing test
    functions that prove it."""

    ceremony: str
    note: str
    proofs: tuple[tuple[str, str], ...] = ()


T = "typed-phrase"
C = "confirm-step"
A = "armed-enter"
U = "ungated-by-design"

#: The audit table itself. Keys are `(registry, target)`; the non-menu write paths the
#: dashboard carries (its `a`/`f` keys) and the scout browser's `a` attest key ride
#: under their own area names, declared in `EXTRA_KEYS` below.
CEREMONY: dict[tuple[str, str], tuple[Cell, ...]] = {
    # -- the console shell (C2): the LIVE pair's guard ---------------------------------------------
    ("CONSOLE_MENU", "profile"): (
        Cell(
            C,
            "selecting the LIVE deployment asks an explicit y/N at the terminal (a view "
            "switch, not a typed gate: it changes what the console answers about, never "
            "what the engine does); a decline keeps the binding exactly where it was",
            (
                ("test_console", "test_live_requires_an_explicit_confirmation"),
                ("test_console", "test_switching_to_paper_never_asks_for_confirmation"),
                (
                    "test_console",
                    "test_switching_to_the_active_profile_is_a_no_op_not_a_confirmation",
                ),
                ("test_console", "test_a_wrong_pair_is_refused"),
                ("test_tui", "test_run_live_live_switch_declined_keeps_the_binding"),
            ),
        ),
        Cell(
            U,
            "paper switches carry no ceremony by design: every paper pair is the same "
            "class of deployment, and the wrapper's CLI flags remain the documented "
            "direct-binding path",
            (("test_console", "test_switching_rebinds_the_pair_in_one_action"),),
        ),
    ),
    # -- the Compliance menu (C3) ------------------------------------------------------------------
    ("COMPLIANCE_MENU", "attest"): (
        Cell(
            T,
            "the operator types the ASSET CODE back, naming the thing being recorded -- "
            "case-sensitive, nothing else accepts, and the question never leaks the "
            "phrase; a wrong phrase records nothing",
            (
                (
                    "test_compliance_console",
                    "test_attest_form_refuses_to_proceed_without_the_typed_asset_code",
                ),
                (
                    "test_compliance_console",
                    "test_the_attest_typed_gate_never_leaks_the_phrase_into_the_question",
                ),
                (
                    "test_compliance_console",
                    "test_attest_form_is_cancelled_by_an_empty_asset",
                ),
            ),
        ),
    ),
    ("COMPLIANCE_MENU", "attest-instrument"): (
        Cell(
            U,
            "the CLI's own gate is none (recording what contract a listing is adds an "
            "admission INPUT; the screen still fails closed on it); the form cancels "
            "cleanly and its vocabulary is the CLI's own Choice set",
            (
                (
                    "test_compliance_console",
                    "test_instrument_attest_form_uppercases_the_product_like_the_cli",
                ),
                (
                    "test_compliance_console",
                    "test_instrument_attest_validation_raises_form_input_error_and_the_form_renders_it",
                ),
            ),
        ),
    ),
    ("COMPLIANCE_MENU", "exempt"): (
        Cell(
            U,
            "the CLI's own gate is none; the form's own guards (the waivable-criterion "
            "vocabulary, a refused blank rationale) are the CLI's, and a cancel writes "
            "no waiver",
            (
                (
                    "test_compliance_console",
                    "test_exempt_form_records_a_documented_exception_and_refuses_a_blank_rationale",
                ),
            ),
        ),
    ),
    ("COMPLIANCE_MENU", "unexempt"): (
        Cell(
            U,
            "revoking a waiver is de-risking and is always allowed, exactly as the CLI "
            "allows it; the repository's own rowcount tells a revoke from a no-op",
            (
                ("test_compliance_console", "test_unexempt_form_reports_a_revoke_and_an_absence"),
            ),
        ),
    ),
    ("COMPLIANCE_MENU", "subscription-attest"): (
        Cell(
            U,
            "the CLI's own gate is none -- an attested tier only ever LOWERS rail 14's "
            "allowance to the tier's real cap; the form cancels on an empty tier and "
            "renders the service's own refusals verbatim",
            (
                (
                    "test_compliance_console",
                    "test_subscription_attest_form_dispatches_to_the_subscription_service",
                ),
                (
                    "test_compliance_console",
                    "test_subscription_attest_form_renders_a_service_error_calmly",
                ),
            ),
        ),
    ),
    ("COMPLIANCE_MENU", "subscription-set"): (
        Cell(
            U,
            "the CLI's own gate is none for a raw hand-set allowance (the form's menu "
            "row itself says 'prefer attest'); an empty amount cancels",
            (
                (
                    "test_compliance_console",
                    "test_subscription_set_form_dispatches_to_the_subscription_service",
                ),
            ),
        ),
    ),
    ("COMPLIANCE_MENU", "withdrawals-attest"): (
        Cell(
            T,
            "`--enabled` RELEASES a rail-17 entry halt and demands the CLI's own typed "
            "gate (a typed `yes`) -- the same posture as `keel autonomy on`; a declined "
            "gate writes not a single state row",
            (
                (
                    "test_compliance_console",
                    "test_withdrawals_enabled_requires_the_typed_gate_before_any_write",
                ),
                (
                    "test_compliance_console",
                    "test_withdrawals_form_rejects_an_unrecognized_answer_without_writing",
                ),
                (
                    "test_compliance_console",
                    "test_the_withdrawals_typed_gate_is_the_clis_own",
                ),
            ),
        ),
        Cell(
            U,
            "`--suspended` only ever REDUCES capability (it re-arms the halt) and is "
            "ungated, exactly as the CLI's own asymmetry keeps it",
            (("test_compliance_console", "test_withdrawals_suspended_is_ungated_like_the_cli"),),
        ),
    ),
    # the scout browser's offered (never auto-run) attest step: same typed gate as above
    ("scout-view", "a"): (
        Cell(
            T,
            "the scout-results browser OFFERS the attest step for a selected candidate; "
            "accepting it runs the SAME typed asset-code form the Compliance menu runs "
            "-- the proposer never decides, nothing attests without the human's phrase",
            (
                (
                    "test_compliance_console",
                    "test_scout_attest_step_uses_the_same_typed_attest_form",
                ),
                (
                    "test_tui",
                    "test_run_live_scout_attest_key_drives_the_typed_form_end_to_end",
                ),
            ),
        ),
    ),
    # -- the strategy console (C4) -----------------------------------------------------------------
    ("STRATEGY_MENU", "add"): (
        Cell(
            U,
            "`rules add` lands the row as CANDIDATE -- the gated step is promotion, not "
            "creation -- and the form's validations are the add service's own; an empty "
            "kind writes nothing",
            (("test_strategy_console", "test_the_add_form_cancels_on_an_empty_kind"),),
        ),
    ),
    ("STRATEGY_MENU", "retry"): (
        Cell(
            C,
            "after the re-backtest, the promote attempt asks an explicit y/N (the O3 "
            "promote confirmation); a declined or refused promote promotes nothing",
            (
                (
                    "test_strategy_console",
                    "test_the_retry_form_rebacktests_and_asks_before_promoting",
                ),
                (
                    "test_strategy_console",
                    "test_the_retry_promote_reports_the_machines_refusal_verbatim",
                ),
            ),
        ),
        Cell(
            T,
            "the `--force` bypass is offered only after a declined/refused promote and "
            "runs ONLY behind the console's TYPED gate "
            "(`clis_typed_promote_force_gate` -- the CLI's `--force` is a bare flag with "
            "no gate of its own; the console is deliberately STRICTER, quoting the CLI's "
            "own force warning over the shared typed-confirmation gate); a wrong phrase "
            "writes nothing and the status does not move",
            (
                (
                    "test_strategy_console",
                    "test_the_retry_force_requires_the_typed_phrase_and_refuses_a_wrong_one",
                ),
                (
                    "test_strategy_console",
                    "test_clis_typed_promote_force_gate_uses_the_clis_own_gate",
                ),
                ("test_strategy_console", "test_clis_typed_promote_force_gate_fails_closed"),
            ),
        ),
    ),
    ("STRATEGY_MENU", "simulate"): (
        Cell(
            A,
            "the simulate view opens ARMED showing the plan (the report path it will "
            "write); Enter is the confirm step, the run fetches/writes exactly as `keel "
            "simulate` does and blocks the loop, and its verdict is held",
            (
                (
                    "test_strategy_console",
                    "test_run_live_simulate_opens_armed_and_never_runs_the_service_until_enter",
                ),
                (
                    "test_strategy_console",
                    "test_run_live_simulate_enter_runs_the_service_once_and_holds_the_result",
                ),
            ),
        ),
    ),
    ("STRATEGY_MENU", "enable"): (
        Cell(
            U,
            "`rules enable` is the DOCUMENTED restore path and carries no CLI gate; it "
            "restores at candidate (never the prior status) and refuses a rule that is "
            "not disabled",
            (
                (
                    "test_strategy_console",
                    "test_the_enable_form_restores_a_disabled_rule_at_candidate",
                ),
                (
                    "test_strategy_console",
                    "test_the_enable_form_refuses_a_rule_that_is_not_disabled",
                ),
            ),
        ),
    ),
    ("STRATEGY_MENU", "disable"): (
        Cell(
            U,
            "disabling stops a rule from being evaluated -- the de-risking direction, "
            "with no CLI gate; an empty id changes nothing",
            (("test_console_ceremony",
              "test_a_cancelled_form_writes_nothing_the_state_it_would_touch"),),
        ),
    ),
    ("STRATEGY_MENU", "demote"): (
        Cell(
            U,
            "demotion moves a rule back down the lifecycle with the service's own "
            "recorded context, no CLI gate; an empty id changes nothing",
            (("test_console_ceremony",
              "test_a_cancelled_form_writes_nothing_the_state_it_would_touch"),),
        ),
    ),
    # -- the Trading menu (C5) ---------------------------------------------------------------------
    ("TRADING_MENU", "cycle"): (
        Cell(
            A,
            "the agent-cycle view opens ARMED naming the profile, the mode's semantics, "
            "the autonomy state and the session honesty; Enter runs ONE cycle through "
            "`agent.run_once` with the CLI's own order-confirmation gate (in confirm "
            "mode the y is asked MID-CYCLE, with curses suspended)",
            (
                (
                    "test_trading_console",
                    "test_run_live_the_cycle_entry_is_armed_until_enter",
                ),
                (
                    "test_trading_console",
                    "test_run_agent_cycle_dispatches_to_run_once_with_the_clis_own_confirm_gate",
                ),
                (
                    "test_trading_console",
                    "test_run_live_the_cycle_dispatch_suspends_curses_around_the_mid_cycle_confirm_gate",
                ),
            ),
        ),
    ),
    ("TRADING_MENU", "monitor"): (
        Cell(
            A,
            "the monitor view opens ARMED; Enter runs ONE poll (record the session, "
            "skip while closed, else fetch candles) -- read-only w.r.t. money",
            (
                (
                    "test_trading_console",
                    "test_run_live_the_monitor_poll_is_armed_until_enter",
                ),
            ),
        ),
    ),
    ("TRADING_MENU", "autonomy"): (
        Cell(
            T,
            "the ON direction asks the CLI's OWN arm gate (a typed word) with no expiry "
            "-- arming lets the agent place orders unattended, and a declined gate "
            "writes nothing",
            (
                (
                    "test_trading_console",
                    "test_autonomy_on_requires_the_clis_typed_gate_and_writes_nothing_on_a_refusal",
                ),
                (
                    "test_trading_console",
                    "test_the_autonomy_arm_gate_is_the_clis_own_extracted_gate",
                ),
            ),
        ),
        Cell(
            U,
            "the OFF direction only ever reduces capability and is ungated, exactly as "
            "`keel autonomy off` is",
            (("test_trading_console", "test_autonomy_off_is_ungated_and_immediate"),),
        ),
    ),
    ("TRADING_MENU", "record-flow"): (
        Cell(
            T,
            "declaring a deposit/withdrawal rebases rail 11's equity memory -- typed, "
            "with the RAW amount named in the action phrase; the gate runs BEFORE "
            "validation, so a refusal writes nothing",
            (
                (
                    "test_trading_console",
                    "test_record_flow_form_gates_before_validating_and_writes_nothing_on_a_refusal",
                ),
                (
                    "test_trading_console",
                    "test_the_record_flow_gate_carries_the_amount_in_its_action_phrase",
                ),
            ),
        ),
    ),
    ("TRADING_MENU", "reset-hwm"): (
        Cell(
            T,
            "clearing the drawdown reference is typed (it re-arms what rail 11 will "
            "allow); a declined gate leaves the mark untouched",
            (("test_trading_console", "test_reset_hwm_form_refusal_writes_nothing"),),
        ),
    ),
    ("TRADING_MENU", "resume-entries"): (
        Cell(
            T,
            "the ONLY early release of the consecutive-loss halt (rail 16) is typed; a "
            "declined gate leaves the halt armed and the loss counter as it was",
            (
                (
                    "test_trading_console",
                    "test_resume_entries_form_refusal_writes_nothing_and_success_clears_rail_16",
                ),
                (
                    "test_trading_console",
                    "test_run_live_resume_entries_refusal_writes_nothing_end_to_end",
                ),
            ),
        ),
    ),
    ("TRADING_MENU", "kill"): (
        Cell(
            U,
            "kill is one key with NO ceremony BY ITS OWN CLI CONTRACT: engaging the "
            "halt is the safe direction, dispatch is immediate, and the CLI's own line "
            "toasts -- the console adds no ceremony and removes none",
            (
                (
                    "test_trading_console",
                    "test_kill_dispatches_immediately_and_toasts_the_clis_own_line",
                ),
                (
                    "test_trading_console",
                    "test_run_live_kill_engages_from_the_menu_with_no_ceremony",
                ),
            ),
        ),
    ),
    ("TRADING_MENU", "resume"): (
        Cell(
            T,
            "RELEASING the kill switch is typed (`keel resume`'s own gate); a declined "
            "gate keeps the halt engaged -- proven end-to-end through the live loop",
            (
                (
                    "test_trading_console",
                    "test_resume_form_refusal_writes_nothing_and_the_success_line_is_the_clis",
                ),
                (
                    "test_trading_console",
                    "test_run_live_resume_refusal_writes_nothing_end_to_end",
                ),
                (
                    "test_trading_console",
                    "test_run_live_resume_with_the_typed_yes_releases_the_halt",
                ),
                ("test_trading_console", "test_the_resume_gate_is_the_clis_own"),
            ),
        ),
    ),
    # -- the Account menu (C6 read-only; #415 adds its ONE gated write) ---------------------------
    ("ACCOUNT_MENU", "update"): (
        Cell(
            T,
            "the update view opens ARMED with the whole plan (current vs latest, the "
            "production wheels, the Release/ dir, the .bak-before-* database "
            "backups, the RUNNING venv); Enter is NOT enough -- the run demands the "
            "CLI's OWN typed gate (`keel update`'s exact wording, which names the "
            "version, the launch folder and that the running binary is replaced), "
            "rendered at the terminal through the suspend/restore dance. The gate "
            "runs INSIDE the service's confirm seam, so there is no ungated path to "
            "the mutations; a wrong phrase, a no-TTY run or a decline writes "
            "not one file and never relaunches",
            (
                (
                    "test_account_console",
                    "test_run_live_update_entry_opens_the_armed_view_and_never_runs_the_service",
                ),
                (
                    "test_account_console",
                    "test_run_live_update_enter_runs_the_gate_at_the_terminal_and_a_refusal_writes_nothing",
                ),
                (
                    "test_account_console",
                    "test_run_update_at_terminal_gates_then_runs_then_relaunches",
                ),
            ),
        ),
    ),
    # -- the Data menu (C5) ------------------------------------------------------------------------
    ("DATA_MENU", "fetch"): (
        Cell(
            A,
            "the fetch view opens ARMED showing the plan (products x granularities x "
            "window for the ACTIVE profile); Enter runs the fetch service itself, "
            "blocking, with the CLI's streamed lines held",
            (
                (
                    "test_data_console",
                    "test_run_live_fetch_is_armed_until_enter_and_enter_runs_exactly_one",
                ),
            ),
        ),
    ),
    ("DATA_MENU", "fetch-check"): (
        Cell(
            A,
            "the dry-run opens ARMED like the fetch (same seam, same Enter); the check "
            "itself never touches the network and writes nothing",
            (
                (
                    "test_data_console",
                    "test_run_live_check_entry_runs_the_check_and_renders_the_verdict",
                ),
                (
                    "test_data_console",
                    "test_the_armed_check_screen_says_it_never_touches_the_network",
                ),
            ),
        ),
    ),
    ("DATA_MENU", "repair-gaps"): (
        Cell(
            A,
            "gap repair RE-REQUESTS history windows (it writes candles): ARMED with the "
            "plan first, Enter confirms, per-series outcomes render after",
            (
                (
                    "test_data_console",
                    "test_run_live_repair_gaps_confirms_then_renders_per_series_outcomes",
                ),
            ),
        ),
    ),
    ("DATA_MENU", "db-import"): (
        Cell(
            U,
            "`keel db import` is read-only w.r.t. the exchange and carries no CLI gate; "
            "the form validates the path with the CLI's OWN check (its message, "
            "verbatim) and an empty path imports nothing",
            (
                ("test_data_console", "test_db_import_cancels_on_an_empty_path"),
                (
                    "test_data_console",
                    "test_run_live_db_import_form_runs_at_the_terminal",
                ),
            ),
        ),
    ),
    # -- the dashboard's own write keys (pre-console, still the landing screen) --------------------
    ("normal", "a"): (
        Cell(
            T,
            "the dashboard's `a` toggles autonomy through `toggle_autonomy`, whose "
            "OFF->ON direction runs the CLI's OWN typed arm gate (`_confirm_arm_"
            "autonomy`, the same `_require_interactive_confirmation` wording); a "
            "decline writes nothing",
            (
                ("test_tui", "test_toggle_autonomy_off_to_on_declined"),
                ("test_tui", "test_toggle_autonomy_on_to_off_is_immediate_and_ungated"),
                ("test_tui", "test_confirm_arm_autonomy_true_on_typed_yes_and_restores_screen"),
            ),
        ),
    ),
    ("normal", "f"): (
        Cell(
            U,
            "`f` fetches candle history -- money-safe by construction (data only, never "
            "an order), the dashboard's own pre-console key, ungated like `keel fetch`; "
            "the console's own path to the SAME write is Data -> fetch, which IS gated "
            "(ARMED; Enter is the confirm step)",
            (
                (
                    "test_data_console",
                    "test_run_live_fetch_is_armed_until_enter_and_enter_runs_exactly_one",
                ),
            ),
        ),
    ),
}

#: The write paths that are NOT menu entries -- declared, so the coverage equality in
#: the teeth test cannot be satisfied by an accidental extra row for them either.
#: HOW TO KEEP THIS COMPLETE (the audit's own method): the non-menu writes are exactly
#: the KEY BRANCHES in `run_live` that dispatch work -- walk the loop's `ch ==`/`elif`
#: chain in every mode (the dashboard `normal` screen's keys, and per-mode keys such as
#: the scout browser's `a`) and list every branch whose body is not a pure view or
#: navigation action; re-walk the chain whenever a key branch is added, because a brand
#: new write key has no registry entry for the teeth test to derive and only a row here
#: classifies it.
EXTRA_KEYS: frozenset[tuple[str, str]] = frozenset(
    {
        ("scout-view", "a"),
        ("normal", "a"),
        ("normal", "f"),
    }
)


# -- the teeth: derive the mutating keys from the dispatch registries ------------------------------


def _mutating_keys() -> set[tuple[str, str]]:
    """Every (registry, target) the console dispatches that is not declared read-only.

    The derivation is deliberately CONSERVATIVE: an entry is mutating unless its kind is
    in its module's read-only set. A new kind value nobody classified is therefore
    mutating, and a new mutating entry without a ceremony row fails the equality below
    -- that failure is this suite's whole reason to exist."""
    keys: set[tuple[str, str]] = set()
    # the shell: switching the binding is the one mutating action (the LIVE guard)
    read_only_actions = {
        "dashboard", "help", "placeholder", "compliance", "strategy",
        "research", "trading", "data", "account",
    }
    for entry in console_mod.CONSOLE_MENU:
        if entry.action not in read_only_actions:
            keys.add(("CONSOLE_MENU", entry.action))
    # the sub-menus: kinds per module that provably only read
    read_only_kinds = {
        "COMPLIANCE_MENU": {"view", "scout"},
        "STRATEGY_MENU": {"view", "insights"},
        "TRADING_MENU": set(),  # armed, form AND action all mutate
        "DATA_MENU": {"view"},
        "ACCOUNT_MENU": {"view"},
        "HELP_MENU": {"view"},
    }
    for registry, menu in (
        ("COMPLIANCE_MENU", compliance_console.COMPLIANCE_MENU),
        ("STRATEGY_MENU", strategy_console.STRATEGY_MENU),
        ("TRADING_MENU", trading_console.TRADING_MENU),
        ("DATA_MENU", data_console.DATA_MENU),
        ("ACCOUNT_MENU", account_console.ACCOUNT_MENU),
        ("HELP_MENU", help_console.HELP_MENU),
    ):
        for entry in menu:
            if getattr(entry, "kind", "view") not in read_only_kinds[registry]:
                keys.add((registry, entry.target))
    return keys


def test_the_table_covers_every_mutating_action_the_registries_dispatch() -> None:
    """THE COVERAGE PROOF: the table's keys are EXACTLY the registries' mutating keys
    plus the dashboard's declared write keys -- no mutating action without a ceremony
    row (a new one fails here until it is classified), and no stale row for an action
    that no longer exists."""
    derived = _mutating_keys() | set(EXTRA_KEYS)
    table = set(CEREMONY)
    missing = derived - table
    assert not missing, f"unclassified mutating actions: {sorted(missing)}"
    stale = table - derived
    assert not stale, f"ceremony rows for actions that no longer exist: {sorted(stale)}"


def test_every_row_names_a_real_ceremony_class() -> None:
    for key, cells in sorted(CEREMONY.items()):
        assert cells, key
        for cell in cells:
            assert cell.ceremony in CLASSES, (key, cell.ceremony)
            assert cell.note.strip(), key


def test_every_named_proof_is_a_real_test_function() -> None:
    """One source of truth: the table only NAMES proofs -- this asserts each named
    (module, function) resolves to a real test in this suite's package, so a reference
    cannot rot into a string that pins nothing."""
    for key, cells in sorted(CEREMONY.items()):
        for cell in cells:
            assert cell.proofs, (key, "a cell with no proof at all")
            for module_name, function_name in cell.proofs:
                module = importlib.import_module(f"tests.commands.{module_name}")
                assert hasattr(module, function_name), (key, module_name, function_name)


def test_every_typed_row_says_the_prompt_is_not_pre_fillable() -> None:
    """O3's contract stated on every typed row: the phrase is typed by the human, never
    piped, pre-filled or bypassed (the help-text twin of this pin lives in
    `test_help_console`). The check is on the note's substance: each typed cell's note
    carries the failing-closed semantics."""
    for key, cells in sorted(CEREMONY.items()):
        for cell in cells:
            if cell.ceremony == T:
                lowered = cell.note.lower()
                assert any(
                    word in lowered
                    for word in ("typed", "phrase", "yes", "asset code", "gate")
                ), (key, cell.note)


# -- the compact refusal proofs: the cells no existing test covered --------------------------------
#
#: Every form row whose cancellation no existing test pins gets ONE generic proof in
#: `test_a_cancelled_form_writes_nothing_the_state_it_would_touch` below: run the form
#: with every prompt answered empty and assert the cancellation line AND a
#: byte-identical state.


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _canceling_prompt() -> Callable[[str], str]:
    def prompt(_text: str) -> str:
        return ""

    return prompt


def _rules_state(repo: Repository) -> Any:
    return repo.get_rules()


def _generic_refusal_rows(
    repo: Repository, config: Any
) -> dict[tuple[str, str], tuple[str, Callable[[Repository], Any]]]:
    """The form rows without an existing cancellation proof, each with the runner that
    cancels on all-empty answers and the state its write would touch."""
    prompt = _canceling_prompt()
    rows: dict[tuple[str, str], tuple[str, Callable[[Repository], Any]]] = {
        ("COMPLIANCE_MENU", "attest"): (
            lambda: compliance_console.run_form("attest", repo, config, prompt, NOW_TS),
            lambda r: r.get_asset_attestations(),
        ),
        ("COMPLIANCE_MENU", "attest-instrument"): (
            lambda: compliance_console.run_form(
                "attest-instrument", repo, config, prompt, NOW_TS
            ),
            lambda r: r.get_instrument_attestations(),
        ),
        ("COMPLIANCE_MENU", "exempt"): (
            lambda: compliance_console.run_form("exempt", repo, config, prompt, NOW_TS),
            lambda r: r.list_screen_exceptions(),
        ),
        ("COMPLIANCE_MENU", "unexempt"): (
            lambda: compliance_console.run_form("unexempt", repo, config, prompt, NOW_TS),
            lambda r: r.list_screen_exceptions(),
        ),
        ("COMPLIANCE_MENU", "subscription-attest"): (
            lambda: compliance_console.run_form(
                "subscription-attest", repo, config, prompt, NOW_TS
            ),
            lambda r: r.list_broker_subscriptions(),
        ),
        ("COMPLIANCE_MENU", "subscription-set"): (
            lambda: compliance_console.run_form(
                "subscription-set", repo, config, prompt, NOW_TS
            ),
            lambda r: r.list_broker_subscriptions(),
        ),
        ("COMPLIANCE_MENU", "withdrawals-attest"): (
            lambda: compliance_console.run_form(
                "withdrawals-attest", repo, config, prompt, NOW_TS
            ),
            lambda r: (r.get_state("withdrawals_enabled"), r.get_state("withdrawals_attested_at")),
        ),
        ("STRATEGY_MENU", "retry"): (
            lambda: strategy_console.run_retry_form(repo, config, prompt, NOW_TS),
            _rules_state,
        ),
        ("STRATEGY_MENU", "enable"): (
            lambda: strategy_console.run_enable_form(repo, config, prompt, NOW_TS),
            _rules_state,
        ),
        ("STRATEGY_MENU", "disable"): (
            lambda: strategy_console.run_disable_form(repo, config, prompt, NOW_TS),
            _rules_state,
        ),
        ("STRATEGY_MENU", "demote"): (
            lambda: strategy_console.run_demote_form(repo, config, prompt, NOW_TS),
            _rules_state,
        ),
        ("DATA_MENU", "db-import"): (
            lambda: data_console.run_db_import_form(repo, prompt),
            lambda r: r.get_transactions(),
        ),
    }
    return rows


def _audit_config() -> Any:
    from decimal import Decimal

    from keel.config import (
        AutoTradeConfig,
        Caps,
        Config,
        DcaConfig,
        MarketDataConfig,
        MoneyMgmtConfig,
    )
    from keel.types import Granularity

    return Config(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[Granularity.ONE_HOUR], history_days=365),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )


def test_a_cancelled_form_writes_nothing_the_state_it_would_touch(repo: Repository) -> None:
    """The threaded refusal invariant, compactly: every form row above cancels on empty
    answers and leaves the state its write would touch byte-identical. The TYPED
    refusals (a wrong phrase, a declined gate) are proven by the named per-row tests;
    this is the cancel-path half for the rows no existing test pinned."""
    config = _audit_config()
    for key, (runner, state) in sorted(_generic_refusal_rows(repo, config).items()):
        before = state(repo)
        result = runner()
        lowered = result.lower()
        assert "cancelled" in lowered or "nothing" in lowered, (key, result)
        assert state(repo) == before, key


# -- the cross-cutting invariants ------------------------------------------------------------------


def test_the_live_binding_moves_only_through_the_guards_switch() -> None:
    """No key path can rebind the console: `ConsoleBinding.rebind` is called from
    exactly ONE place -- `console.switch_profile`, the function whose LIVE arm consults
    `confirm_fn` -- pinned structurally over the whole console layer (an AST scan: any
    other `.rebind(` call site fails). Direct binding via the CLI's own flags remains
    the wrapper's documented path, outside this code entirely."""
    import keel.commands as commands_pkg

    rebind_sites: list[str] = []
    for path in sorted(Path(commands_pkg.__path__[0]).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "rebind"
            ):
                # the one legitimate site: inside switch_profile itself
                if path.name == "console.py":
                    owner = next(
                        (
                            n.name
                            for n in ast.walk(tree)
                            if isinstance(n, ast.FunctionDef)
                            and n.lineno <= node.lineno <= n.end_lineno
                            and n.name == "switch_profile"
                        ),
                        None,
                    )
                    if owner == "switch_profile":
                        continue
                rebind_sites.append(f"{path.name}:{node.lineno}")
    assert not rebind_sites, rebind_sites
    # and the guarded function itself keeps the LIVE arm behind confirm_fn
    source = inspect.getsource(console_mod.switch_profile)
    assert "requires_confirmation and not confirm_fn()" in source


def test_no_console_action_originates_an_order_outside_the_agent_pipeline() -> None:
    """The order path is exactly one: `agent.run_once`, the same function `keel agent`
    runs, carrying the CLI's own confirm gate (`trading_console.run_agent_cycle`'s
    default `run_fn`). The thinness pin's executor allowance admits ONLY the two READ
    helpers -- asserted here by importing that suite's own allowlist, so the two pins
    cannot drift apart."""
    import keel.agent
    from tests.commands import test_console_thinness as thin

    executor_calls = {
        callee for _stem, _fn, callee in thin.CALL_ALLOWLIST
        if callee.startswith("keel.execution.executor.")
    }
    assert executor_calls == {
        "keel.execution.executor._withdrawals_enabled",  # the rail-17 state read
        "keel.execution.executor._fetch_available_quote",  # the display balance read
    }, executor_calls
    signature = inspect.signature(trading_console.run_agent_cycle)
    assert signature.parameters["run_fn"].default is keel.agent.run_once


def _seam_runner_groups() -> list[tuple[str, ...]]:
    """One entry per `_run_terminal_form(...)` call site in the console layer, in source
    order: every dotted runner name (`<module>.<runner>(...)`) reachable from the call's
    runner argument -- named inline in a lambda, or inside the local helper def a bare
    Name argument refers to (resolved within the call site's own enclosing function, so a
    same-named def elsewhere is not picked up).

    DERIVED, never hand-listed: a form-runner added at the seam is covered the day it
    lands (the hand-maintained tuple this replaces had already missed three of the
    trading forms), and a seam call site whose runner the extraction cannot read yields
    an EMPTY entry, which the canary test below fails loudly on."""
    from tests.commands import test_console_thinness as thin

    groups: list[tuple[str, ...]] = []
    for path in thin._console_module_paths():
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        all_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        sites = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_terminal_form"
        ]
        for call in sorted(sites, key=lambda c: c.lineno):
            names: set[str] = set()
            for arg in call.args[1:]:
                if isinstance(arg, ast.Lambda):
                    names |= _dotted_runner_calls(arg)
                elif isinstance(arg, ast.Name):
                    enclosing = max(
                        (d for d in all_defs if d.lineno < call.lineno <= d.end_lineno),
                        key=lambda d: d.lineno,
                        default=None,
                    )
                    if enclosing is None:
                        continue
                    for defn in all_defs:
                        if (
                            defn.name == arg.id
                            and enclosing.lineno <= defn.lineno
                            and defn.end_lineno <= enclosing.end_lineno
                        ):
                            names |= _dotted_runner_calls(defn)
            groups.append(tuple(sorted(names)))
    return groups


def _dotted_runner_calls(node: ast.AST) -> set[str]:
    """Every `x.y(...)` call inside `node` (a lambda body or a helper def handed to the
    seam) -- the runner dispatches, as dotted names. Bare-name calls (`open_state()`,
    `now_fn()`, ...) are plumbing, not runners, and are excluded by shape."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and isinstance(sub.func.value, ast.Name)
        ):
            names.add(f"{sub.func.value.id}.{sub.func.attr}")
    return names


def test_the_seam_runner_derivation_covers_every_call_site() -> None:
    """The canary for the derived runners: the console layer's `_run_terminal_form(` call
    sites -- counted independently, by text -- are exactly one list entry each, and no
    entry is empty. Without this, an extraction that silently matched nothing (an AST
    change, a rename of the seam) would hand the suspend test an empty runner set and it
    would pass vacuously; with it, every seam call site contributes the runners it
    dispatches, or the suite says so."""
    import re

    from tests.commands import test_console_thinness as thin

    text_sites = 0
    for path in thin._console_module_paths():
        text = Path(path).read_text(encoding="utf-8")
        text_sites += len(re.findall(r"_run_terminal_form\(", text))
        text_sites -= len(re.findall(r"def _run_terminal_form\(", text))
    groups = _seam_runner_groups()
    assert text_sites == len(groups), (text_sites, groups)
    assert all(groups), groups


def test_every_blocking_run_that_can_prompt_suspends_curses() -> None:
    """Every terminal-prompting run in the live loop goes through the ONE shared
    suspend/restore seam (`_run_terminal_form` -- `def_prog_mode` -> `endwin` -> the
    run -> `reset_prog_mode` -> refresh), pinned structurally: no form-runner or cycle
    dispatch inside `run_live` may execute outside a `_run_terminal_form(...)` call
    span, or outside a helper def that the seam is handed. The runners scanned for are
    DERIVED from the seam's own call sites (`_seam_runner_groups`), so a form-runner
    added at the seam is covered without anyone remembering to extend a hand-listed
    tuple. The end-to-end dance proofs are the named per-row tests (the cycle's
    mid-flight gate, the db-import form, resume's typed yes)."""
    source = inspect.getsource(run_live)
    tree = ast.parse(source)
    seam_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_terminal_form"
        ):
            seam_calls.append(node)
    assert seam_calls, "the scan found no _run_terminal_form calls -- it has rotted"

    # The spans where a runner may legitimately appear: the seam call itself (a lambda
    # argument), plus the local helper DEFS the seam is handed (resolved within the
    # seam call's own enclosing function, so a same-named def elsewhere is not covered).
    all_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    covered: list[tuple[int, int]] = [(c.lineno, c.end_lineno) for c in seam_calls]
    for call in seam_calls:
        enclosing = max(
            (d for d in all_defs if d.lineno < call.lineno <= d.end_lineno),
            key=lambda d: d.lineno,
            default=None,
        )
        if enclosing is None:
            continue
        for arg in call.args[1:]:
            if isinstance(arg, ast.Name):
                for defn in all_defs:
                    if defn.name == arg.id and enclosing.lineno <= defn.lineno:
                        covered.append((defn.lineno, defn.end_lineno))

    runners = sorted({name for group in _seam_runner_groups() for name in group})
    assert runners, "the derivation found no runners -- it has rotted"
    lines = source.splitlines()
    for index, line in enumerate(lines, start=1):
        for runner in runners:
            # only actual CALLS (a docstring may name the seam without invoking it)
            if runner + "(" in line and not any(
                start <= index <= end for start, end in covered
            ):
                pytest.fail(
                    f"{runner} dispatched outside the suspend/restore seam "
                    f"(line {index}): {line.strip()}"
                )
    # the seam itself keeps the dance -- a prompt must never render under curses
    seam_source = inspect.getsource(run_live.__globals__["_run_terminal_form"])
    for call in ("def_prog_mode", "endwin", "reset_prog_mode"):
        assert call in seam_source, call


def test_the_account_menu_contributes_exactly_one_mutating_action_the_typed_update() -> None:
    """Through C6 the Account branch was read-only top to bottom (its own pin said
    so). #415 adds its ONE write path: the update entry, kind "armed", whose run is
    TYPED (the row above). pnl and versions stay views, and no OTHER mutating action
    may appear in the branch without a ceremony row -- the teeth test surfaces it."""
    assert [(e.target, e.kind) for e in account_console.ACCOUNT_MENU] == [
        ("pnl", "view"),
        ("versions", "view"),
        ("update", "armed"),
    ]
    assert {key for key in _mutating_keys() if key[0] == "ACCOUNT_MENU"} == {
        ("ACCOUNT_MENU", "update")
    }
