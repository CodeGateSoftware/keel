"""`keel serve` -- open the read-only view in a browser (#435, D2).

This is the front-end half of the local web UI: it resolves where state lives, mints a session
token, binds loopback, and optionally opens a browser. Everything it serves comes from
`keel/web/`, which is pinned by `tests/commands/test_console_thinness.py` exactly as the console
layer is.

**Why the browser launch lives here and not in `keel/web/`.** `webbrowser.open` spawns a process.
Rule 5 of the thinness pin exists to keep process and network orchestration out of the rendering
layer -- the same reason the self-update slice's shell-outs live in `keel.commands.update` rather
than in the console that triggers them. Putting the launch in the command keeps `keel/web/` a
package that reads a database and returns strings, which is what makes it cheap to reason about.
"""

from __future__ import annotations

import webbrowser

import click

from keel.commands._common import default_config_path, default_db_path
from keel.web.security import new_session_token
from keel.web.server import ServeConfig, serve

#: Not 8080. Freqtrade's FreqUI and Jesse's dashboard both sit there, and an operator running one
#: of them alongside keel should not have to discover the clash through a bind error.
DEFAULT_PORT = 8765

DEFAULT_HOST = "127.0.0.1"


@click.command("serve")
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Address to bind.")
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int, help="Port to bind.")
@click.option(
    "--open/--no-open",
    "open_browser",
    default=True,
    show_default=True,
    help="Open the URL in your default browser.",
)
@click.pass_context
def serve_cmd(ctx: click.Context, host: str, port: int, open_browser: bool) -> None:
    """Serve keel's read-only view on localhost and open it in your browser.

    Read-only, by construction: the server implements GET and HEAD and nothing else, so there is
    no request this can answer that changes anything. Attesting, promoting a rule, recording a
    flow and turning autonomy on all remain CLI commands behind the interactive-terminal gate --
    the browser gets its own gate in a later change, and it will be a gate rather than a bypass.

    The printed URL carries a one-time token for this run. It is never written to disk, so
    stopping the server invalidates it, and no other program on this machine can read the page
    without it.

    Binding is loopback by default. `--host` accepts anything, and says loudly what that means:
    on a non-loopback address the page -- your positions, equity and full trade history -- is
    readable by anyone who can reach the port, with a cleartext token as the only obstacle.
    """
    obj = ctx.obj or {}
    cfg = ServeConfig(
        host=host,
        port=port,
        token=new_session_token(),
        db_path=obj.get("db_path") or default_db_path(),
        config_path=obj.get("config_path") or default_config_path(),
        build=_build_line(),
    )

    if open_browser:
        # Opened BEFORE `serve` blocks, and best-effort: a headless machine, a broken
        # BROWSER variable or a sandbox with no launcher must not stop the server starting --
        # the URL has already been printed, and typing it in is a complete fallback.
        try:
            webbrowser.open(cfg.url())
        except Exception:  # pragma: no cover - platform-specific launcher failures
            pass

    ctx.exit(serve(cfg, echo=click.echo))


def _build_line() -> str:
    """The build identity in the page footer, so a screenshot of the UI says which build produced
    it. Best-effort: a footer is not worth failing a server start over."""
    try:
        from keel.version import build_info

        return build_info().describe()
    except Exception:  # pragma: no cover - metadata absent in odd environments
        return ""
