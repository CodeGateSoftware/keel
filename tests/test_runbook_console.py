"""Content pins for the operator runbook's TUI-console section (issue #392 C6; PRD §5
C6 -- "docs (runbook TUI section)"): the section must exist where an operator looks for
it, and must carry the console's actual contracts -- the menu tree over the C1
services, the profile switching + LIVE guard, the session banner, the typed-confirmation
contract, the ARMED/blocking surfaces, the Venues browser, the help system, and the
safety design notes. The same style as the other runbook pins
(`tests/test_paper_equities_profile.py` et al.): the runbook is procedure, and a
procedure that drifts from the code is worse than none."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "docs" / "operator-runbook.md"


def _section() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    match = re.search(r"^## The TUI console\n(.*)", text, flags=re.S | re.M)
    assert match, "the runbook has no '## The TUI console' section"
    # up to the next top-level (##) heading: the section's own text
    rest = match.group(1)
    next_section = re.search(r"^## ", rest, flags=re.M)
    body = rest[: next_section.start()] if next_section else rest
    # The runbook wraps at ~95 columns: squash whitespace so a phrase pinned across a
    # line break still reads as the phrase.
    return " ".join(body.split())


def test_the_runbook_has_a_tui_console_section() -> None:
    """The section exists, by its name, in Part 2 (after the deployment profiles,
    before the money settings)."""
    assert "## The TUI console" in RUNBOOK.read_text(encoding="utf-8")


def test_the_section_says_what_the_console_is_and_that_it_is_thin() -> None:
    section = _section()
    lowered = section.lower()
    assert "keel tui" in lowered
    assert "menu" in lowered
    # thin by construction: the same services the CLI calls, pinned by a test
    assert "same services" in lowered
    assert "thin" in lowered
    assert "no business logic" in lowered or "renders and dispatches" in lowered


def test_the_section_documents_profile_switching_and_the_live_guard() -> None:
    section = _section()
    lowered = section.lower()
    assert "profile" in lowered
    assert "config+db" in section or "config + db" in lowered or "pair" in lowered
    assert "live" in lowered
    assert "confirm" in lowered
    # the running agent keeps its own pair: the switch rebinds the CONSOLE only
    assert "running agent" in lowered or "its own pair" in lowered
    # direct binding via the CLI flags remains the wrapper's documented path
    assert "--config" in section


def test_the_section_documents_the_session_banner() -> None:
    section = _section().lower()
    assert "banner" in section
    assert "session" in section
    assert "clock unavailable" in section


def test_the_section_documents_the_typed_contracts() -> None:
    section = _section().lower()
    assert "typed" in section
    # never pre-filled
    assert "pre-filled" in section
    # the SIX CLI-own typed prompts run in-console; the two the console adds (attest's
    # asset code, promote --force's typed yes) are stated as STRICTER than the CLI --
    # never as identical to it, because the CLI's own gates for those two do not exist
    assert "the cli's own typed prompt" in section
    assert "stricter" in section
    for action in ("resume", "attest", "kill"):
        assert action in section
    # kill's one-key contract stated as its own
    assert "one key" in section or "one-key" in section


def test_the_section_documents_the_armed_views_and_ctrl_c() -> None:
    section = _section().lower()
    assert "armed" in section
    assert "enter" in section
    assert "ctrl-c" in section or "ctrl+c" in section or "control-c" in section
    # the code's own disclosure wording: Ctrl-C exits gracefully, discards held results,
    # and the in-flight run does NOT complete (the handlers catch Exception only, so the
    # interrupt propagates out of the run) -- pinned so "it does not abort a run in
    # flight" cannot come back
    assert "does not complete" in section
    assert "gracefully" in section


def test_the_section_documents_the_venues_browser_and_brokers_list() -> None:
    section = _section().lower()
    assert "venues" in section
    assert "keel brokers list" in section


def test_the_section_documents_the_help_and_glossary_system() -> None:
    section = _section().lower()
    assert "glossary" in section
    assert "?" in _section()


def test_the_section_documents_the_safety_design_notes() -> None:
    section = _section().lower()
    # cursor resets: a remembered row is a loaded one
    assert "cursor" in section
    assert "reset" in section
    # the audit table's existence: the ceremony classes are pinned by tests
    assert "audit" in section or "ceremony" in section


def test_the_section_documents_the_account_menu() -> None:
    section = _section().lower()
    assert "account" in section
    assert "pnl" in section
    assert "versions" in section


def test_the_section_is_honest_about_scope() -> None:
    """The runbook's voice: state the limits, don't sell. The console runs no loop of
    its own and the typed gates cannot be automated -- the same warnings the command
    surface carries."""
    section = _section().lower()
    assert "does not" in section or "doesn't" in section or "never" in section
    assert "loop" in section  # the console is not a scheduler
