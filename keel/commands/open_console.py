"""`keel open` -- reach a console that is already running (#756).

`keel serve` prints its URL once, at startup, with the session token in it. That is enough when a
human is watching the terminal and useless when nothing is: under `launchd` the line goes to
`StandardOutPath`, and the way back into your own console becomes `grep`-ing a log for a token.

So a detached `serve` records how to reach itself (`keel/web/runtime.py`, which carries the
argument for why that is acceptable and how it is bounded), and this reads that record back.

**It mints nothing and it extends nothing.** The token it prints is the one the running server
already minted; stopping keel still revokes it, and this command has no way to bring it back.

**It refuses rather than guesses.** Three different absences look identical from here -- no server,
a server started from a terminal, and a server that was killed -- and each gets its own sentence,
because "not found" would send an operator hunting for a bug in the two cases where nothing is
wrong.
"""

from __future__ import annotations

import webbrowser
from typing import NoReturn

import click

from keel.commands.serve import DEFAULT_PORT
from keel.web import runtime


@click.command("open")
@click.option(
    "--port",
    default=DEFAULT_PORT,
    show_default=True,
    type=int,
    help="Which console. One `keel serve` process per port; see `keel serve --port`.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Print the address without launching a browser.",
)
def open_cmd(port: int, no_browser: bool) -> None:
    """Print (and open) the address of a running `keel serve`, token included.

    The deployment is the one the CURRENT DIRECTORY belongs to (or `KEEL_HOME`), the same way
    every other bare invocation resolves state -- so run this from the deployment, not from a
    checkout of the source.
    """
    record = runtime.live_record(port)
    if record is None:
        _refuse(port)

    url = runtime.url_for(record)
    click.echo(f"Opening the keel console at:\n\n    {url}\n")
    click.echo("This address carries the running server's session token. Stopping keel revokes it.")

    if no_browser:
        return
    try:
        # Best-effort, exactly as `keel serve` treats its own launch: the URL is already printed,
        # so a headless machine, a broken BROWSER variable or a sandbox with no launcher costs the
        # operator nothing they cannot recover by pasting.
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 -- any launcher failure is survivable here
        click.echo(f"(could not launch a browser: {exc})")


def _refuse(port: int) -> NoReturn:
    """Say which of the three absences this is, and what to do about each.

    A stale record is reported as a STOPPED server rather than as no server: the file is evidence
    that one ran, and telling an operator "nothing is serving" when a crashed process left its
    record behind hides the thing they most need to know.
    """
    # WHERE IT LOOKED, in every branch. The first real install of the console daemons succeeded
    # and read as a failure: `keel open` was run from a source checkout, which is itself a
    # deployment root (it has a `keel.db` and a `config.yaml`), so it searched that tree while
    # four healthy servers wrote to `~/keel/run/`. The message listed three explanations and the
    # true one was not among them -- it could not be, because it never said where it had looked.
    #
    # This command takes no `--db` and no `--config`, so nothing in its signature hints that the
    # answer depends on the working directory. The path is the hint.
    searched = runtime.run_dir()

    stale = runtime.read_record(port)
    if stale is not None:
        # WHICH of the two liveness checks failed. `live_record` requires a live pid AND something
        # answering on the port, and this branch used to assert the first had failed regardless --
        # measured saying "its process is gone" about the very process printing the sentence. A
        # server that is alive but not yet listening, or bound to a host other than the one
        # recorded, is a bind problem, and telling its operator it crashed sends them to the wrong
        # investigation. This codebase refuses that kind of confident wrong claim elsewhere
        # (`_session_banner` renders nothing rather than name a mode it cannot verify).
        pid = int(stale.get("pid", 0) or 0)
        if runtime.process_alive(pid):
            raise click.ClickException(
                f"a keel server was recorded on port {port} and its process ({pid}) is still "
                f"running, but nothing answers on {stale.get('host', '?')}:{port}.\n\n"
                "  That is a server that failed to bind, or one still starting. Its log says "
                f"which: `Address already in use` is the common one.\n\n"
                f"  The record is in {searched}."
            )
        raise click.ClickException(
            f"a keel server was recorded on port {port} but its process ({pid}) is gone -- it "
            "crashed or was killed. Its token died with it; start a new one with `keel serve` "
            f"(or `launchctl kickstart` the agent that runs it).\n\n"
            f"  The record is in {searched}."
        )
    raise click.ClickException(
        f"no recorded keel server on port {port}.\n\n"
        f"  Looked in {searched} -- the deployment resolved from $KEEL_HOME if that is set, "
        "and otherwise from the current directory. If your server is a different deployment, run "
        "this from ITS directory: `keel open` takes the same bare-invocation path as everything "
        "else, so a source checkout resolves to the checkout.\n\n"
        "  If one is running here, it was started from a terminal -- an interactive `keel serve` "
        "deliberately records nothing, and its URL is printed in that terminal. Only a detached "
        "server (launchd, or any run whose stdout is not a terminal) leaves a record, because "
        "that is the case where nobody can read the printed line.\n\n"
        f"  If none is running, start one: `keel serve --port {port}`."
    )
