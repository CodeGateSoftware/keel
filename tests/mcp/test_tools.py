"""The behaviour of the eight handlers in `keel/mcp/tools.py` (#477) -- the boundaries a model
on the other end of the pipe can be counted on to push against.

Three properties, each born of an adversarial review of the first cut:

- **Every list is bounded from BOTH ends.** `veto_log` accepted `limit=10**9` and would have
  marshalled a whole engine log into one response; the cap is now enforced in code AND
  declared in the schema a client reads before choosing arguments. `trials` chain errors are
  tail-bounded the same way -- a corrupted ledger is broken from the damage onward, and a
  tool response is not the place to restate every break.
- **Read-only holds at the ENGINE level.** `PRAGMA query_only = ON` on every connection the
  MCP surface opens, so a write slipped past the package's call discipline dies in SQLite
  itself, not in review.
- **Client-chosen paths stay confined.** The `trials` tool's `path` argument accepts a bare
  file name beside the default ledger and nothing else, the same confinement `reports`
  applies -- "/etc/passwd" arrives as a refusal, never as a read.
"""

from __future__ import annotations

import io
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.mcp.server import serve
from keel.mcp.tools import TOOLS, VETO_MAX_LIMIT, _open_readonly_repo, build_tools
from keel.research import ledger as trials_ledger
from keel.types import Candle, Granularity


def _handler(name: str) -> Any:
    return next(tool.handler for tool in TOOLS if tool.name == name)


def _call_tool(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One tools/call through the real loop, so refusals are seen exactly as a client sees
    them: an isError RESULT, not a dead connection."""
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    reader = io.StringIO(request + "\n")
    writer = io.StringIO()
    serve(reader, writer, "no-such.db", "no-such.yaml", "no-such.log")
    (response,) = [json.loads(line) for line in writer.getvalue().splitlines()]
    return response


# -- the veto_log cap -----------------------------------------------------------------------------


def test_veto_log_caps_an_absurd_limit_at_the_maximum(tmp_path: Path) -> None:
    """`limit=10**9` is not a client error to reject -- it is an argument to BOUND. The
    handler must return at most VETO_MAX_LIMIT events, and the most recent ones."""
    base = 1_700_000_000
    total = VETO_MAX_LIMIT + 137
    log = tmp_path / "keel.log"
    log.write_text(
        "".join(
            json.dumps(
                {"event": "executor.order_vetoed", "ts": base + i, "violations": [f"rail {i}"]}
            )
            + "\n"
            for i in range(total)
        ),
        encoding="utf-8",
    )
    handler = next(
        tool.handler for tool in build_tools(log_path=str(log)) if tool.name == "veto_log"
    )
    result = handler({"since_ts": 1, "limit": 10**9})
    assert result["count"] == len(result["events"]) == VETO_MAX_LIMIT == 500
    assert result["events"][0]["ts"] == base + total - VETO_MAX_LIMIT
    assert result["events"][-1]["ts"] == base + total - 1


def test_veto_log_schema_declares_the_maximum() -> None:
    """The cap a client cannot see is a cap a client cannot respect: the schema must state
    `maximum` beside the `minimum` it already stated."""
    schema = next(tool.input_schema for tool in TOOLS if tool.name == "veto_log")
    assert schema["properties"]["limit"]["maximum"] == VETO_MAX_LIMIT


# -- engine-level read-only -----------------------------------------------------------------------


def test_a_write_through_the_mcp_opened_repo_is_refused_by_sqlite_itself(tmp_path: Path) -> None:
    """`query_only = ON` means the connection the MCP surface hands to a Repository rejects
    writes in the engine: even a write that slipped past every source-level fence dies with
    `sqlite3.OperationalError`, while reads on the same connection keep working."""
    from keel.data.db import connect, migrate

    db = tmp_path / "keel.db"
    seed = connect(str(db))
    migrate(seed)
    seed.close()

    repo = _open_readonly_repo(str(db))
    assert repo.get_orders() == []  # reads through the same connection still answer

    candle = Candle(
        ts=1_700_000_000,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
    )
    try:
        repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [candle])
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        assert "readonly" in message or "query_only" in message, message
    else:
        raise AssertionError("a write through the MCP-opened repo was not refused")


# -- trials path confinement ----------------------------------------------------------------------


def test_trials_path_arguments_that_escape_the_ledger_directory_are_refused() -> None:
    """ "../../etc/passwd" and "/etc/passwd" are refusals, not reads: the argument is confined
    to a bare file name resolved inside the ledger's own directory, exactly as `reports`
    confines its `document`."""
    for hostile in ("../../etc/passwd", "/etc/passwd", "subdir/ledger.jsonl"):
        response = _call_tool(41, "trials", {"path": hostile})
        assert response["result"]["isError"] is True, hostile
        text = response["result"]["content"][0]["text"]
        assert "bare file name" in text, (hostile, text)


def test_trials_accepts_a_bare_file_name_beside_the_default_ledger() -> None:
    """The confinement is not a closure: a bare name still reads, resolved inside the
    ledger's directory, and a file that does not exist is a calm empty ledger."""
    response = _call_tool(42, "trials", {"path": "no-such-ledger.jsonl"})
    assert response["result"].get("isError") is None
    result = json.loads(response["result"]["content"][0]["text"])
    parent = Path(trials_ledger.DEFAULT_LEDGER_PATH).parent
    assert result["path"] == str(parent / "no-such-ledger.jsonl")
    assert result["rows"] == 0
    assert result["chain_errors"] == []


# -- chain-error tail bound ------------------------------------------------------------------------


def test_trials_chain_errors_are_tail_bounded(tmp_path: Path, monkeypatch: Any) -> None:
    """A ledger whose every row fails verification reports the FIRST 20 errors plus a count
    of the rest -- the finding survives, the megaphone does not."""
    total = 30
    ledger = tmp_path / "trials-ledger.jsonl"
    ledger.write_text(
        "".join(
            json.dumps(
                {
                    "trial_id": f"t-{index}",
                    "timestamp": 1_700_000_000 + index,
                    "session": "review",
                    "rule": "donchian_entry_n",
                    "params": {"n": 20},
                    "provenance": "fitted",
                    "kind": "sweep_node",
                    "decision": "selected",
                    "per_trade_pnl": ["1.5"],
                    "per_bar_pnl": [],
                    "series_missing": False,
                    "summary": {"trade_count": 2},
                    "prev_hash": "e" * 64,  # nothing chains to anything: every row errors
                    "row_hash": "f" * 64,
                }
            )
            + "\n"
            for index in range(total)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trials_ledger, "DEFAULT_LEDGER_PATH", ledger)

    result = _handler("trials")({})

    assert len(result["chain_errors"]) == 21
    assert all(error.startswith("row ") for error in result["chain_errors"][:20])
    assert result["chain_errors"][-1] == f"+{total - 20} more chain errors"
    assert result["rows"] == total
