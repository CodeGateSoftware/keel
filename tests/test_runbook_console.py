"""Content pins for the operator runbook's console section (originally issue #392 C6).

**This file pinned the TUI console until #541, and the subject changed rather than the job.**
The old pins required the runbook to carry the curses dashboard's actual contracts -- the menu
tree, profile switching and the LIVE guard, the session banner, the typed-confirmation contract,
the ARMED surfaces, the Venues browser, the help system. That console is deleted, so pinning its
documentation would pin a description of nothing.

What has not changed is why the file exists: **the runbook is procedure, and a procedure that
drifts from the code is worse than none.** The console an operator opens today is a browser, so
these pins are about that section -- including the two facts most expensive to get wrong, which
are what the browser CANNOT do and where a headless operator goes instead.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "docs" / "operator-runbook.md"

SECTION_HEADING = "## The operator console, in a browser"


def _section() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(SECTION_HEADING)}\n(.*)", text, flags=re.S | re.M)
    assert match, f"the runbook has no '{SECTION_HEADING}' section"
    rest = match.group(1)
    next_section = re.search(r"^## ", rest, flags=re.M)
    body = rest[: next_section.start()] if next_section else rest
    # The runbook wraps at ~95 columns: squash whitespace so a phrase pinned across a line break
    # still reads as the phrase.
    return " ".join(body.split())


def test_the_runbook_has_a_console_section() -> None:
    """The section exists, by its name, where the TUI section used to be."""
    assert SECTION_HEADING in RUNBOOK.read_text(encoding="utf-8")


def test_the_section_says_what_the_console_is_and_that_it_is_thin() -> None:
    """Thinness is the property that lets one set of services answer both front-ends, and it is
    pinned by a test -- so the runbook says so rather than implying it."""
    section = _section().lower()
    assert "keel serve" in section
    assert "service layer" in section
    assert "test_console_thinness" in section


def test_the_section_names_every_view() -> None:
    """Seven views, named. An operator looking for one should find it here rather than by
    clicking around."""
    section = _section()
    for view in ("Status", "Setup", "Activity", "Insights", "Rules", "Venues", "Gates"):
        assert view in section, view


def test_the_section_documents_the_one_time_token() -> None:
    """The single most confusing thing about this server on first contact: the URL carries a
    token, it is new every run, and it is never written to disk."""
    section = _section().lower()
    assert "token" in section
    assert "loopback" in section
    assert "never written to disk" in section


def test_the_section_says_what_the_browser_cannot_do() -> None:
    """**The safety fact, and the reason this test is the sharpest one here.**

    Every capability-increasing action is a CLI command behind a typed confirmation at a
    terminal; the browser can perform none of them. An operator who believes otherwise will go
    looking in the wrong place for the button that arms autonomy, and -- worse -- an operator who
    believes the browser CAN do it may leave the page open thinking it is a control surface.
    """
    section = _section().lower()
    assert "capability-increasing" in section
    assert "keel capabilities" in section
    assert "typed confirmation" in section or "typed" in section
    # The claim must be about the SERVER, not about what the page happens to draw: "a client that
    # hides a button is not a gate" is the design spec's own sentence.
    assert "server implements no verb" in section


def test_the_section_answers_the_headless_case() -> None:
    """"But SSH" is the first objection to deleting a terminal UI, and the runbook must answer it
    where an operator will be standing when they ask."""
    section = _section()
    assert "ssh -L 8765:127.0.0.1:8765" in section
    assert "secure context" in section.lower()


def test_the_section_is_honest_about_what_was_deleted() -> None:
    """The TUI existed for years and operators will look for it. The section says plainly that it
    is gone, and why -- not silently omits it."""
    section = _section().lower()
    assert "keel tui" in section
    assert "curses" in section
    assert "#541" in section
