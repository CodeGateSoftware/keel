"""CLI wiring for `keel mcp` (#477): the command exists, and its stdout is protocol and
nothing else.

The second assertion is the one that matters. Stdout is the transport -- newline-delimited
JSON-RPC -- so the disclaimer footer every other command prints would land mid-stream and
corrupt the framing. `mcp_cmd` is deliberately NOT wrapped in `with_disclaimer`, and this file
is where that stays true: every byte on stdout must parse as one JSON response per line, with
the read-only statement travelling in serverInfo and the tool descriptions where the client
actually reads it.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from keel.cli import cli
from keel.commands._common import DISCLAIMER

EIGHT = (
    "doctor",
    "capabilities",
    "profiles",
    "orders",
    "veto_log",
    "purification",
    "trials",
    "reports",
)


def _request(request_id: int, method: str, params: dict | None = None) -> str:
    message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message)


def test_help_lists_the_command() -> None:
    result = CliRunner().invoke(cli, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "read-only" in result.output


def test_the_command_speaks_protocol_and_nothing_else_on_stdout(
    tmp_path, valid_config_path
) -> None:
    stdin = (
        "\n".join(
            [
                _request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                _request(2, "tools/list"),
                _request(3, "tools/call", {"name": "capabilities", "arguments": {}}),
            ]
        )
        + "\n"
    )
    result = CliRunner().invoke(
        cli,
        ["--db", str(tmp_path / "keel.db"), "--config", str(valid_config_path), "mcp"],
        input=stdin,
    )
    assert result.exit_code == 0, result.output

    # stdout is protocol and ONLY protocol: three requests answered (the notification never
    # is), every line one JSON object, and no disclaimer footer anywhere on the stream
    lines = result.stdout.splitlines()
    assert len(lines) == 3, f"expected 3 responses, got {lines!r}"
    responses = [json.loads(line) for line in lines]
    assert DISCLAIMER not in result.stdout

    assert responses[0]["result"]["serverInfo"]["name"] == "keel-read-only"
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == list(EIGHT)
    assert json.loads(responses[2]["result"]["content"][0]["text"])["rows"]
