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
from typing import Any

import click

from keel.commands._common import default_config_path, default_db_path
from keel.web.security import HostPolicy, new_session_token
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
@click.option(
    "--external-host",
    "external_hosts",
    multiple=True,
    metavar="HOSTNAME",
    help=(
        "A hostname a reverse proxy (e.g. a Cloudflare Tunnel) may present in Host:. "
        "Repeatable. One specific name each -- wildcards are refused. Default: none, and "
        "leaving it that way keeps this server loopback-only."
    ),
)
@click.pass_context
def serve_cmd(
    ctx: click.Context,
    host: str,
    port: int,
    open_browser: bool,
    external_hosts: tuple[str, ...],
) -> None:
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
    # Resolved ONCE, here, and carried on the config in both its forms. `build_info()` shells out
    # to git twice, and `/api/config` is polled by a service worker (#538) -- an endpoint that
    # forks a subprocess to answer "which build is this" would make the cheapest question on the
    # server the most expensive one.
    build = _build_info()
    # #648. Normalised HERE rather than in `HostPolicy`, so the policy compares two values that
    # are already in the same case and the comparison stays a plain set membership.
    cleaned = frozenset(name.strip().lower() for name in external_hosts if name.strip())
    # BUILT EAGERLY, and the reason is a bug this caught in its own first draft: `host_policy` is
    # a lazy property, so `HostPolicy.__post_init__`'s wildcard guard fired on the first REQUEST
    # rather than at startup -- a `--external-host '*.example.com'` server started cleanly, said
    # nothing, and would have raised somewhere inside a handler. Constructing one here turns that
    # into the command refusing to start, which is what the guard was written to mean.
    try:
        HostPolicy(bound_host=host, port=port, external_hosts=cleaned)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--external-host") from exc
    cfg = ServeConfig(
        host=host,
        port=port,
        external_hosts=cleaned,
        token=new_session_token(),
        db_path=obj.get("db_path") or default_db_path(),
        config_path=obj.get("config_path") or default_config_path(),
        build=_build_line(build),
        build_info=build,
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


def _build_info() -> Any:
    """The running build, resolved once, or `None`.

    Best-effort: a build identity is not worth failing a server start over, and `None` is a state
    the consumers already handle -- the footer renders empty and `/api/config` reports the version
    absent rather than inventing one. Split out of `_build_line` when `/api/config` (#534) needed
    the same object as STRUCTURE rather than as a sentence, so the two can never describe different
    builds: parsing `describe()`'s output back into fields would be a display string being read as
    data."""
    try:
        from keel.version import build_info

        return build_info()
    except Exception:  # pragma: no cover - metadata absent in odd environments
        return None


def _build_line(build: Any) -> str:
    """The build identity in the page footer, so a screenshot of the UI says which build produced
    it."""
    return "" if build is None else str(build.describe())
