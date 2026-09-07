"""One name for one idea, across `keel serve` and `keel open`.

`serve` spelled it `--no-open` and `open` spelled it `--no-browser`. Two words for one concept on
adjacent commands, and the four console plists put both in front of an operator at once. Worse,
`keel open --no-open` is a contradiction in terms, and `--no-browser` is what every comparable
local daemon uses (`jupyter notebook`, `tensorboard`).

So `--no-browser` is the spelling on both, and `--open`/`--no-open` survives as a HIDDEN alias:
the plists in this repository already shipped with it, an operator may have scripted it, and a
flag that vanishes turns a working script into `no such option`.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands.open_console import open_cmd
from keel.commands.serve import serve_cmd

#: The commands that decide whether to launch a browser, and therefore the ones that must agree.
BROWSER_COMMANDS: tuple[click.Command, ...] = (serve_cmd, open_cmd)


def _flag_names(command: click.Command) -> set[str]:
    names: set[str] = set()
    for param in command.params:
        names.update(param.opts)
        names.update(param.secondary_opts)
    return names


def test_both_commands_spell_it_the_same_way() -> None:
    """The whole point, asserted over the SET rather than per command.

    A per-command check passes happily on two commands that disagree, which is exactly the state
    this replaces.
    """
    for command in BROWSER_COMMANDS:
        assert "--no-browser" in _flag_names(command), command.name


def test_the_deprecated_spelling_still_parses_on_serve() -> None:
    """The four `com.keel.serve.*.plist` files shipped with `--no-open` in them, and an operator
    may have copied one to `~/Library/LaunchAgents` already. Removing the flag outright would
    turn a running daemon into `Error: no such option` on its next restart -- which, under
    `KeepAlive`, is a crash loop rather than a message anybody sees."""
    assert "--no-open" in _flag_names(serve_cmd)


def test_the_deprecated_spelling_is_hidden_from_help() -> None:
    """Kept working, not kept advertised. Two spellings in `--help` is the confusion this change
    exists to remove."""
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--no-browser" in result.output
    assert "--no-open" not in result.output, "the deprecated spelling is still being advertised"


def test_open_does_not_grow_the_contradiction() -> None:
    """`keel open --no-open` is a sentence that argues with itself. It is not added for symmetry:
    nothing ever shipped it on this command, so there is no script to keep working."""
    assert "--no-open" not in _flag_names(open_cmd)


def test_serve_help_still_documents_what_the_flag_does() -> None:
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert "browser" in result.output.lower()


#: Every combination of the two spellings, and whether a browser should end up launched.
#:
#: The conflicting rows are the point. The alias used to win OUTRIGHT whenever it appeared, so
#: `--no-browser --open` opened a browser -- a HIDDEN, DEPRECATED flag quietly beating the one in
#: `--help`. Of the three possible rules (last wins, documented wins, refuse), that was the worst,
#: and the failure it produces is a GUI window at boot on a headless daemon, which is the exact
#: thing `--no-browser` is in the plists to prevent.
BROWSER_CASES: tuple[tuple[tuple[str, ...], bool], ...] = (
    ((), True),
    (("--browser",), True),
    (("--no-browser",), False),
    (("--open",), True),
    (("--no-open",), False),
    # Documented spelling wins when it was given EXPLICITLY, whichever order they appear in.
    (("--no-browser", "--open"), False),
    (("--open", "--no-browser"), False),
    (("--browser", "--no-open"), True),
    (("--no-open", "--browser"), True),
)


@pytest.mark.parametrize(("flags", "should_launch"), BROWSER_CASES)
def test_the_spellings_compose_the_way_the_help_says(
    flags: tuple[str, ...], should_launch: bool, tmp_path, monkeypatch
) -> None:
    """Behaviour, not just parsing.

    Every structural test above reads the commands' declared options, which proves the names exist
    and nothing about what they DO. A `--no-browser` that parsed and then opened a browser anyway
    passes all of them -- and on a launchd daemon at boot that is a window nobody asked for on a
    machine that may have no GUI session at all.

    The no-flags row is load bearing: without it, "no browser launched" would be satisfied by a
    build that never launches one, and every other row would be vacuous.
    """
    from keel.web import server as web_server

    class _Stub:
        def __init__(self) -> None:
            self.server_address = ("127.0.0.1", 8765)
            self.RequestHandlerClass = type("H", (), {"cfg": None})

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            return None

    launched: list[str] = []
    monkeypatch.setattr(web_server, "ensure_schema", lambda _path: None)
    monkeypatch.setattr(web_server, "build_server", lambda _cfg: _Stub())
    import keel.commands.serve as serve_mod

    monkeypatch.setattr(serve_mod.webbrowser, "open", lambda url: launched.append(url) or True)

    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "keel.db"), "serve", *flags, "--port", "8765"]
    )
    assert result.exit_code == 0, (flags, result.output)
    assert bool(launched) is should_launch, f"{flags} launched={bool(launched)}"
