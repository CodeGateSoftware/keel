"""The stdio transport behind `keel mcp` (#477): a minimal, read-only subset of MCP over stdlib.

**Why hand-rolled and not the `mcp` SDK.** The precedent is `keel/web/server.py`: the repo is
stdlib-only, plain dataclasses, zero asyncio, and the transport this server speaks is ONE
shape -- newline-delimited JSON-RPC 2.0 on stdin/stdout with three methods. An SDK would pull
pydantic and an async runtime into a wheel whose proposition is auditability, to buy framing
this loop does in a screenful. The moment this server grows prompts or resources or sampling,
the SDK is the right answer and this comment should go.

**Why the loop must never die.** The client on the other end is a research assistant, not an
operator: a tool that raises, a malformed line, an unknown method -- each is one bad request,
and none of them is a reason to drop the connection the other tools were about to use. Tool
failures become `isError` tool RESULTS (machine-readable, exactly where a model looks for
them); protocol failures become JSON-RPC error responses; the loop reads the next line either
way.

**Why stdout is protocol and nothing else.** A banner, a disclaimer, a stray print -- any of
them would land mid-stream and corrupt the framing. The read-only statement travels in
`serverInfo` and the tool descriptions, where the client actually reads it; anything an
operator needs goes to stderr.
"""

from __future__ import annotations

import json
import sys
from typing import IO, Any

from keel.mcp.tools import build_tools, json_safe

#: What the server calls itself. The name says the whole proposition: read-only.
SERVER_NAME = "keel-read-only"

#: The protocol revision this subset implements. Echoed back when the client names a different
#: one -- a client that asked for a newer revision is told "yours", and the three methods here
#: are stable across every revision MCP has shipped.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: The complete method surface, in one place so tests can pin it.
HANDLED_METHODS = ("initialize", "tools/list", "tools/call")

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


#: The handshake's version, resolved ONCE per process. `build_info()` shells out to git, and
#: `initialize` is answered for every connection -- paying that subprocess cost per handshake
#: was the inconsistency (the doctor tool pays it on every call and is right to). Cached on
#: first use so it is paid exactly once; `None` means "not resolved yet".
_HANDSHAKE_VERSION: str | None = None


def _server_version() -> str:
    """The running build's full version (`0.1.0+<commit>`), resolved once per process.

    `build_info()` shells out to git, which is why the answer is cached in `_HANDSHAKE_VERSION`
    rather than recomputed per `initialize`. Any failure falls back to the distribution version
    alone -- the handshake wants a string, never an exception."""
    global _HANDSHAKE_VERSION
    if _HANDSHAKE_VERSION is None:
        try:
            from keel.version import build_info

            _HANDSHAKE_VERSION = build_info().full_version
        except Exception:  # a handshake must never die over a version string
            from keel.version import _package_version

            _HANDSHAKE_VERSION = _package_version()
    return _HANDSHAKE_VERSION


def _write_message(writer: IO[str], payload: dict[str, Any]) -> None:
    writer.write(json.dumps(payload) + "\n")
    writer.flush()


def _result(writer: IO[str], request_id: Any, result: dict[str, Any]) -> None:
    _write_message(writer, {"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(writer: IO[str], request_id: Any, code: int, message: str) -> None:
    _write_message(
        writer, {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


def _initialize_result(message: dict[str, Any]) -> dict[str, Any]:
    params = message.get("params")
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    version = requested if isinstance(requested, str) and requested else DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
    }


def _tools_list_result(tools: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "tools": [
            {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
            for tool in tools
        ]
    }


def _tool_call_result(tools_by_name: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    params = message.get("params")
    if not isinstance(params, dict):
        raise TypeError("tools/call params must be an object")
    name = params.get("name")
    tool = tools_by_name.get(str(name)) if name is not None else None
    if tool is None:
        return {
            "content": [{"type": "text", "text": f"unknown tool: {name!r}"}],
            "isError": True,
        }
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        result = tool.handler(arguments)
    except Exception as exc:  # one bad tool call is one bad tool call -- the loop survives it
        return {
            "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
            "isError": True,
        }
    return {"content": [{"type": "text", "text": json.dumps(json_safe(result))}]}


def serve(reader: IO[str], writer: IO[str], db_path: str, config_path: str, log_path: str) -> None:
    """Read one JSON message per line until EOF, answering requests and never notifications.

    Sync and stdlib by design (there is no asyncio anywhere in keel, and a stdio server has
    exactly one client). Every failure path answers and continues: parse errors, unknown
    methods, malformed params and tool exceptions all leave the loop alive for the next line.
    """
    tools = build_tools(db_path, config_path, log_path)
    tools_by_name = {tool.name: tool for tool in tools}
    for line in reader:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            _error(writer, None, _PARSE_ERROR, "Parse error")
            continue
        if not isinstance(message, dict):
            _error(writer, None, _INVALID_REQUEST, "Invalid Request")
            continue
        if "id" not in message:
            # A notification: no id, no response, ever -- stdin alone is never enough to make
            # this server say anything.
            continue
        method = message.get("method")
        request_id = message["id"]
        if method == "initialize":
            _result(writer, request_id, _initialize_result(message))
        elif method == "tools/list":
            _result(writer, request_id, _tools_list_result(tools))
        elif method == "tools/call":
            try:
                _result(writer, request_id, _tool_call_result(tools_by_name, message))
            except Exception as exc:  # malformed params at the protocol layer, not tool layer
                _error(writer, request_id, _INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        elif isinstance(method, str):
            _error(writer, request_id, _METHOD_NOT_FOUND, f"method not found: {method}")
        else:
            _error(writer, request_id, _INVALID_REQUEST, "Invalid Request")


def main(db_path: str, config_path: str, log_path: str) -> None:
    """Wire the loop to this process's stdio. Everything else a CLI might print goes to
    stderr; stdout is protocol and nothing else."""
    serve(sys.stdin, sys.stdout, db_path, config_path, log_path)
