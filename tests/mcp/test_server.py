"""The protocol behaviour of `keel/mcp/server.py` (#477): one JSON message per line, and a
loop that outlives everything a client can get wrong.

The contract under test is narrow on purpose -- this is a read-only subset, not an MCP
implementation. What must hold: the handshake answers with the server's identity, tools/list
names exactly the registry, tools/call answers as a tool RESULT (isError) rather than a dead
connection, protocol-level faults answer as JSON-RPC errors (-32700/-32601/-32603), and
notifications -- which carry no id -- are answered with silence, because stdin alone is never
enough to make this server say anything.

Readers and writers are `io.StringIO`, so every test is the loop's own framing logic and
nothing else.
"""

from __future__ import annotations

import io
import json
from typing import Any

from keel.capabilities import CAPABILITIES
from keel.mcp.server import HANDLED_METHODS, SERVER_NAME, serve
from keel.mcp.tools import TOOLS

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


def _roundtrip(lines: list[str]) -> list[dict[str, Any]]:
    reader = io.StringIO("".join(line + "\n" for line in lines))
    writer = io.StringIO()
    serve(reader, writer, "no-such.db", "no-such.yaml", "no-such.log")
    return [json.loads(out) for out in writer.getvalue().splitlines()]


def _request(request_id: Any, method: str, params: dict[str, Any] | None = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message)


def _text_of(response: dict[str, Any]) -> str:
    return response["result"]["content"][0]["text"]


# -- the handshake -------------------------------------------------------------------------------


def test_initialize_echoes_the_clients_protocol_version() -> None:
    (response,) = _roundtrip(
        [_request(1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})]
    )
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == "2025-03-26"
    assert response["result"]["capabilities"] == {"tools": {}}


def test_initialize_names_the_read_only_server() -> None:
    (response,) = _roundtrip([_request(7, "initialize")])
    assert response["result"]["serverInfo"]["name"] == SERVER_NAME == "keel-read-only"
    assert response["result"]["serverInfo"]["version"]


def test_initialize_defaults_the_protocol_version_when_the_client_names_none() -> None:
    (response,) = _roundtrip([_request(1, "initialize", {"protocolVersion": None})])
    assert response["result"]["protocolVersion"] == "2025-06-18"


# -- tools/list ----------------------------------------------------------------------------------


def test_tools_list_names_exactly_the_eight_tools_with_schemas() -> None:
    (response,) = _roundtrip([_request(2, "tools/list")])
    listed = response["result"]["tools"]
    assert tuple(tool["name"] for tool in listed) == EIGHT
    assert {tool["name"] for tool in listed} == {tool.name for tool in TOOLS}
    for tool in listed:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"].strip()


def test_the_method_surface_is_pinned() -> None:
    assert HANDLED_METHODS == ("initialize", "tools/list", "tools/call")


# -- tools/call ----------------------------------------------------------------------------------


def test_tools_call_dispatches_the_capabilities_tool_without_a_database() -> None:
    (response,) = _roundtrip([_request(3, "tools/call", {"name": "capabilities", "arguments": {}})])
    result = response["result"]
    assert result.get("isError") is None or result["isError"] is False
    rows = json.loads(_text_of(response))["rows"]
    assert len(rows) == len(CAPABILITIES)
    assert {row["gate"] for row in rows} == {"tty"}


def test_an_unknown_tool_is_an_error_result_not_a_dead_loop() -> None:
    responses = _roundtrip(
        [
            _request(4, "tools/call", {"name": "autonomy_on", "arguments": {}}),
            _request(5, "tools/list"),
        ]
    )
    assert responses[0]["result"]["isError"] is True
    assert "unknown tool" in _text_of(responses[0])
    # the loop survived: the next request answered normally
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == list(EIGHT)


def test_a_raising_tool_is_an_error_result_and_the_loop_survives_it() -> None:
    # doctor against a database that does not exist: the handler raises, the loop lives
    responses = _roundtrip(
        [
            _request(6, "tools/call", {"name": "doctor", "arguments": {}}),
            _request(8, "tools/call", {"name": "capabilities", "arguments": {}}),
        ]
    )
    assert responses[0]["result"]["isError"] is True
    assert "FileNotFoundError" in _text_of(responses[0])
    assert json.loads(_text_of(responses[1]))["rows"]


# -- protocol faults -----------------------------------------------------------------------------


def test_a_malformed_line_answers_parse_error_and_the_loop_continues() -> None:
    responses = _roundtrip(["{this is not json", _request(9, "tools/list")])
    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == list(EIGHT)


def test_an_unknown_method_is_method_not_found() -> None:
    (response,) = _roundtrip([_request(10, "resources/list")])
    assert response["error"]["code"] == -32601
    assert "resources/list" in response["error"]["message"]


def test_non_object_json_is_invalid_request() -> None:
    (response,) = _roundtrip(["[1, 2, 3]"])
    assert response["error"]["code"] == -32600


def test_blank_lines_are_ignored() -> None:
    responses = _roundtrip(["", "   ", _request(11, "tools/list")])
    assert len(responses) == 1
    assert responses[0]["id"] == 11


def test_every_response_is_a_single_json_line() -> None:
    reader = io.StringIO(_request(1, "initialize") + "\n" + _request(2, "tools/list") + "\n")
    writer = io.StringIO()
    serve(reader, writer, "no-such.db", "no-such.yaml", "no-such.log")
    raw = writer.getvalue()
    assert raw.count("\n") == 2
    assert raw.endswith("\n")


# -- notifications -------------------------------------------------------------------------------


def test_notifications_produce_no_output() -> None:
    responses = _roundtrip(
        [
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}),
        ]
    )
    assert responses == []
