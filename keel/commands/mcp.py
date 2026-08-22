"""`keel mcp` -- serve the read-only surface to an MCP client over stdio (#477).

The front-end half of the MCP server: it resolves where state lives and hands the three paths
to `keel.mcp.server.main`, which owns the protocol loop. Everything the server exposes comes
from `keel/mcp/`, pinned read-only by `tests/mcp/test_readonly.py` the same way the browser
view's write surface is pinned.

**Why this command prints nothing but protocol.** Stdout is the transport -- newline-delimited
JSON-RPC -- so a banner, a disclaimer footer or a stray print would land mid-stream and corrupt
the framing. That is why this command is NOT wrapped in `with_disclaimer` like nearly every
other: the read-only statement travels where the client actually reads it, in `serverInfo` and
the tool descriptions. Anything an operator needs on start-up belongs on stderr, and today
there is nothing worth saying.
"""

from __future__ import annotations

import click

from keel.commands._common import default_config_path, default_db_path


@click.command("mcp")
@click.option(
    "--log",
    "log_path",
    default="logs/keel.log",
    show_default=True,
    help="Engine JSONL log the veto tools read.",
)
@click.pass_context
def mcp_cmd(ctx: click.Context, log_path: str) -> None:
    """Serve keel's read-only surface to an MCP client over stdio.

    One JSON-RPC message per line on stdin, one response per line on stdout, until EOF. The
    eight tools (doctor, capabilities, profiles, orders, veto_log, purification, trials,
    reports) read state, logs and reports; none of them can place, halt or release anything,
    and tests/mcp/test_readonly.py scans the server package to keep it that way. Point an MCP
    client at this command (stdio transport) and ask it what it sees.
    """
    from keel.mcp.server import main as mcp_main

    obj = ctx.obj or {}
    mcp_main(
        obj.get("db_path") or default_db_path(),
        obj.get("config_path") or default_config_path(),
        log_path,
    )
