"""The refusal must say how to GET the value it asks you to paste (#634 follow-up).

The evidence this exists for: on 2026-09-16 two people who know this codebase were locked out of
the console by a daemon restart, and neither recovered from the view. One went to `keel open
--help`; the other asked a colleague. The view named the cause well and the remedy not at all.

Worse than an omission, the refusal's prose says the token "is never written to disk". Since #756
that is true only of a server attached to a TERMINAL -- run detached, `keel/web/runtime.py`
records the address in a `0600` file precisely so `keel open` can hand it back. A reader who
believes the sentence concludes the token is unrecoverable and restarts the server, which mints a
NEW token: the single worst move available, and the one `refusedView`'s own docstring already
warns about ("sends them to restart the thing that is working").

`keel open` was named in `server.py`, in the launchd plist, and in `runtime.py`'s own module
docstring -- everywhere except the screen the locked-out operator is looking at.
"""

from __future__ import annotations

from tests.web.test_client_assets import _comments_only
from tests.web.test_view_sections import _JS, _decl

RENDER = (_JS / "render.js").read_text(encoding="utf-8")


def _refused() -> str:
    """`refusedView`'s body with comments stripped -- prose must not satisfy these assertions."""
    return _comments_only(_decl(RENDER, "refusedView"))


def test_the_refusal_names_the_command_that_recovers_the_address() -> None:
    """The remedy, on the screen that needs it.

    Rejects the mutation that started this: a view that tells an operator what to paste and never
    where to get it. Asserted on the CODE with comments stripped, so the prose above the function
    explaining why the command belongs here cannot stand in for the command.
    """
    body = _refused()
    assert "openCommandNode" in body, (
        "refusedView no longer builds the `keel open` command -- the view is back to asking for "
        "a token it never says how to obtain"
    )
    assert body.count("openCommandNode") == 1


def test_the_command_carries_this_console_s_own_port() -> None:
    """A deployment runs several consoles (four, on the machine this was found on), so a command
    without a port sends the operator to whichever one `keel open` defaults to -- a DIFFERENT
    console, answering happily, which reads as the command having failed.

    `window.location` is the source deliberately: the port is a fact about where this page is,
    not a claim from a payload that just refused us. Rejects the mutation that hardcodes a port
    or drops the argument.
    """
    body = _refused()
    assert "openCommandNode(window.location.port)" in body


def test_the_command_is_a_code_element_not_a_sentence() -> None:
    """Placed to be copied, not read past.

    The detail paragraph it sits beside is five sentences long; a command appended as a sixth is
    a command nobody sees. Rejects the mutation that inlines the text into the surrounding `<p>`.
    """
    command = _comments_only(_decl(RENDER, "openCommandNode"))
    assert 'el("code")' in command
    assert "keel open --port " in command
    assert "keel open" in command


def test_the_command_survives_a_console_on_a_default_port() -> None:
    """`location.port` is the EMPTY STRING on 80/443, so a naive build emits `keel open --port `
    with a dangling flag that the CLI rejects. The bare command is correct there -- `keel open`
    carries its own default -- and the branch is what keeps it that way."""
    command = _comments_only(_decl(RENDER, "openCommandNode"))
    assert "if (port)" in command
    assert "else" in command


def test_the_paste_field_points_at_the_command_not_at_a_past_printing() -> None:
    """ "Paste the address keel printed" is past tense about something that may be long gone --
    a daemon printed it to a log the plist says explicitly not to grep. The label must point at
    something the operator can make happen NOW."""
    body = _refused()
    assert "Paste the address from that command" in body
    assert "Paste the address keel printed" not in body, (
        "the label is back to naming a printing the operator may have no way to reach"
    )


def test_the_instructions_precede_the_field_they_serve() -> None:
    """Order is the whole affordance: the command PRODUCES the value the field consumes, so a
    reader who meets the field first has already been asked for something they do not have."""
    body = _refused()
    assert body.index("openCommandNode") < body.index('el("form")')
