"""The read-only pin for `keel mcp` (#477): the server CANNOT trade, proven, not promised.

`keel/web/server.py` answers "is this surface safe to leave running?" with a closed write
surface and a test that scans its source. This file is the same answer for the MCP server a
research assistant talks to over stdio -- and it is written BEFORE the server exists, so the
server is born inside the fence rather than moved into one later.

Six walls, each mechanical:

1. **The vocabulary.** Every tool NAME and DESCRIPTION is free of the write verbs below
   (word-boundary matched, so a description has to actually use the word, not merely share
   letters with it). The vocabulary is defined HERE, independently of `keel/capabilities.py`,
   because the registry only inventories GATED mutators -- `keel subscription attest` and
   `keel rules promote --force` mutate with no gate at all, so a vocabulary derived from
   CAPABILITIES would bless words those commands use. It is then cross-checked AGAINST the
   registry (every declared capability row must be caught by it), so the two lists cannot
   drift apart silently either.
2. **No capability call site is reachable.** No `keel/mcp/*.py` references any registry row's
   `module.function`, as a dotted string or as a bare name.
3. **The write-deny scan.** An AST walk over every `keel/mcp/*.py` rejecting any method call
   whose attribute name starts with a write-ish prefix (`set_`, `upsert_`, `record_`,
   `arm`, `attest`, `execute`, ...). The allowlist is EMPTY and pinned empty: a read-only
   server has no legitimate write call to allow.
4. **No gate call sites.** `_require_interactive_confirmation` appears nowhere in the package.
   A server must never HOLD the ceremony gate -- the gate is for terminals, and its
   fail-closed property is precisely what a pipe-connected process must not borrow.
5. **The docs pin.** `docs/mcp-server.md`'s tool table names exactly the tools the server
   exposes, bidirectionally -- a documented tool that does not exist, or an exposed tool the
   docs hide, both fail.
6. **The import surface.** `keel.mcp.server` imports without click and without `keel.cli`
   (checked in a fresh interpreter), and the package imports neither the executor nor the
   agent -- the trading paths start unavailable, not merely unused.

And one behavioural wall: a stream of only notifications (no `id`) produces zero bytes of
output -- stdin alone is never enough to make this server say anything.
"""

from __future__ import annotations

import ast
import inspect
import io
import re
import subprocess
import sys
from pathlib import Path

from keel.capabilities import CAPABILITIES
from keel.mcp.tools import TOOLS
from keel.mcp.server import serve

_ROOT = Path(__file__).resolve().parents[2]
_MCP_DIR = _ROOT / "keel" / "mcp"
_DOCS = _ROOT / "docs" / "mcp-server.md"

#: The write vocabulary. Independent of `keel/capabilities.py` on purpose (the registry covers
#: only GATED mutators; unattested mutators like `keel subscription attest` would define it).
#: Matched with word boundaries, so "orders" does not trip "order_create" and "attestation"
#: does not trip "attest" -- a description has to use the exact word.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "arm",
    "release",
    "resume",
    "spend",
    "attest",
    "promote",
    "update",
    "reset",
    "record",
    "withdraw",
    "autonomy",
    "kill",
    "trade",
    "execute",
    "order_create",
    "submit",
    "place",
)

_FORBIDDEN_WORD_RES = tuple(re.compile(rf"\b{re.escape(word)}\b") for word in FORBIDDEN_SUBSTRINGS)

#: Mutating actions the #453 registry does NOT cover (no gate, so no row). The vocabulary must
#: catch these too -- this is the whole reason it is not derived from CAPABILITIES.
_UNREGISTERED_MUTATORS = (
    "keel subscription attest --venue coinbase --tier premium",
    "keel rules promote --force 123",
)

#: Attribute-name prefixes that mark a method call as a write. An empty allowlist is the point.
WRITE_CALL_PREFIXES: tuple[str, ...] = (
    "upsert_",
    "record_",
    "append_",
    "set_",
    "mark_",
    "insert_",
    "add_",
    "delete_",
    "clear_",
    "remove_",
    "promote",
    "attest",
    "arm",
    "release",
    "spend",
    "execute",
    "place_order",
    "submit",
)
WRITE_CALL_ALLOWLIST: tuple[str, ...] = ()

#: The gate function whose call sites are the #453 inventory.
GATE_FUNCTION = "_require_interactive_confirmation"

#: Modules the read-only package may never import directly (transitively-shared read helpers
#: via `keel.commands.doctor` are fine; a DIRECT import of a trading path is not).
FORBIDDEN_IMPORTS: tuple[str, ...] = ("keel.agent", "keel.execution")


def _mcp_sources() -> list[Path]:
    return sorted(_MCP_DIR.glob("*.py"))


def _forbidden_hits(text: str) -> list[str]:
    return [word for word, pattern in zip(FORBIDDEN_SUBSTRINGS, _FORBIDDEN_WORD_RES) if pattern.search(text)]


# -- wall 1: the vocabulary ----------------------------------------------------------------------


def test_every_tool_name_is_free_of_the_write_vocabulary() -> None:
    for tool in TOOLS:
        assert not _forbidden_hits(tool.name), (
            f"tool name {tool.name!r} contains write vocabulary {_forbidden_hits(tool.name)} -- a "
            "read-only server does not get to name a tool with a verb that changes keel's state"
        )


def test_every_tool_description_is_free_of_the_write_vocabulary() -> None:
    for tool in TOOLS:
        assert not _forbidden_hits(tool.description), (
            f"tool {tool.name!r} description contains write vocabulary "
            f"{_forbidden_hits(tool.description)} -- descriptions are what a model reads before "
            "choosing a tool, so they must not advertise a write"
        )


def test_the_vocabulary_catches_every_declared_capability_row() -> None:
    """Cross-check against the #453 registry: every gated action's own words must trip the
    vocabulary. A new capability whose naming dodges every word fails HERE, forcing the
    vocabulary to grow with the registry rather than behind it."""
    for cap in CAPABILITIES:
        row_text = " ".join((cap.module, cap.function, cap.surface, cap.invocation, cap.increases))
        assert _forbidden_hits(row_text), (
            f"capability {cap.module}.{cap.function} is not caught by the write vocabulary -- a "
            "server tool could now name that action without tripping wall 1. Extend "
            f"FORBIDDEN_SUBSTRINGS: {row_text!r}"
        )


def test_the_vocabulary_catches_the_unregistered_mutators() -> None:
    """`keel subscription attest` and `keel rules promote --force` have NO registry row (they
    are ungated), which is exactly why the vocabulary is defined here and not derived from
    CAPABILITIES."""
    for invocation in _UNREGISTERED_MUTATORS:
        assert _forbidden_hits(invocation), f"ungated mutator not caught by the vocabulary: {invocation}"


def test_there_are_exactly_eight_tools() -> None:
    assert len(TOOLS) == 8
    assert len({tool.name for tool in TOOLS}) == 8


# -- wall 2: no capability call site is reachable -------------------------------------------------


def test_every_tool_handler_is_defined_in_the_mcp_package() -> None:
    for tool in TOOLS:
        source = inspect.getsourcefile(tool.handler)
        assert source is not None and str(_MCP_DIR) in str(Path(source).resolve()), (
            f"tool {tool.name!r} handler lives outside keel/mcp/ ({source}) -- the read-only "
            "package is the only place a tool handler may be defined"
        )


def test_no_capability_row_is_referenced_from_the_server_package() -> None:
    """Neither the dotted `module.function` nor the bare function name of any registry row may
    appear in `keel/mcp/*.py` -- the gated actions are unreachable by name, not just uncalled."""
    sources = {path.name: path.read_text(encoding="utf-8") for path in _mcp_sources()}
    used_names: set[str] = set()
    for path in _mcp_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                used_names.add(node.attr)
    for cap in CAPABILITIES:
        dotted = f"{cap.module}.{cap.function}"
        for name, text in sources.items():
            assert dotted not in text, f"{name} references capability call site {dotted}"
        assert cap.function not in used_names, (
            f"keel/mcp references {cap.function} ({dotted}) by bare name -- a gated action must "
            "not be reachable from the read-only server"
        )


# -- wall 3: the AST write-deny scan --------------------------------------------------------------


def _write_calls_in(source: str) -> list[str]:
    """Every `<expr>.<attr>(...)` whose attribute starts with a write prefix, minus the
    (empty) allowlist."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr.startswith(WRITE_CALL_PREFIXES) and attr not in WRITE_CALL_ALLOWLIST:
            found.append(attr)
    return found


def test_no_write_shaped_call_exists_in_the_server_package() -> None:
    offenders: list[str] = []
    for path in _mcp_sources():
        offenders += [f"{path.name}: .{attr}()" for attr in _write_calls_in(path.read_text(encoding="utf-8"))]
    assert not offenders, (
        "write-shaped calls in the read-only server: "
        f"{offenders}. A read-only tool has no legitimate state-changing call; if you believe "
        "you found one, justify it in the PR and add it to WRITE_CALL_ALLOWLIST -- the empty "
        "allowlist is the property, so the bar for entering it is a written argument"
    )


def test_the_write_call_allowlist_is_empty() -> None:
    assert WRITE_CALL_ALLOWLIST == ()


def test_the_write_scan_is_proven_false_capable() -> None:
    snippet = (
        "def readonly(rows):\n"
        "    return rows\n"
        "def dangerous(repo):\n"
        "    repo.set_state('kill_switch', True)\n"
        "    repo.upsert_candles('BTC', 'ONE_HOUR', [])\n"
        "    repo.arm_autonomy()\n"
        "    return readonly([])\n"
    )
    assert sorted(set(_write_calls_in(snippet))) == ["arm_autonomy", "set_state", "upsert_candles"]


# -- wall 4: no gate call sites -------------------------------------------------------------------


def _gate_call_sites_in(source: str) -> set[tuple[str, str]]:
    """(enclosing function,) for every call to the gate -- the same AST walk
    `tests/test_capabilities.py` uses, re-derived here rather than imported so this file fails
    on its own terms."""
    tree = ast.parse(source)
    owners: dict[ast.AST, str] = {}
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(func):
                owners.setdefault(node, func.name)
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (
            callee.id if isinstance(callee, ast.Name) else callee.attr if isinstance(callee, ast.Attribute) else None
        )
        if name == GATE_FUNCTION:
            found.add((owners.get(node, "<module>"),))
    return found


def test_the_server_package_never_holds_the_ceremony_gate() -> None:
    sites: set[tuple[str, ...]] = set()
    for path in _mcp_sources():
        sites |= _gate_call_sites_in(path.read_text(encoding="utf-8"))
    assert not sites, (
        f"keel/mcp calls {GATE_FUNCTION} at {sorted(sites)} -- a server must never hold the "
        "interactive-terminal gate; its fail-closed property is for terminals, and borrowing it "
        "here is how a pipe-connected process would come to look like a human"
    )


def test_the_gate_scan_is_proven_false_capable() -> None:
    snippet = (
        "def server_tool():\n"
        f"    {GATE_FUNCTION}('do the thing', 'detail')\n"
        "def harmless():\n"
        "    pass\n"
    )
    assert _gate_call_sites_in(snippet) == {("server_tool",)}


def test_the_server_package_does_not_import_the_trading_paths() -> None:
    offenders: list[str] = []
    for path in _mcp_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(name == banned or name.startswith(banned + ".") for banned in FORBIDDEN_IMPORTS):
                    offenders.append(f"{path.name}: imports {name}")
    assert not offenders, (
        f"keel/mcp imports the trading paths directly: {offenders}. Read seams are shared "
        "through `keel.commands.doctor` and friends; the executor and the agent start "
        "unreachable"
    )


# -- wall 5: the docs pin -------------------------------------------------------------------------


def test_the_docs_tool_table_matches_the_exposed_tools_exactly() -> None:
    text = _DOCS.read_text(encoding="utf-8")
    documented: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^\|\s*`([a-z_]+)`\s*\|", line)
        if match:
            documented.add(match.group(1))
    exposed = {tool.name for tool in TOOLS}
    assert documented == exposed, (
        f"docs/mcp-server.md table and the server disagree: only in docs={sorted(documented - exposed)}, "
        f"only in server={sorted(exposed - documented)}. An auditor must be able to read the "
        "docs as the whole surface"
    )


# -- wall 6: the import surface -------------------------------------------------------------------


def test_the_server_module_is_click_free_in_a_fresh_interpreter() -> None:
    program = (
        "import sys, keel.mcp.server; "
        "assert 'click' not in sys.modules, 'click leaked into the protocol module'; "
        "assert 'keel.cli' not in sys.modules, 'the composition root leaked into the protocol module'"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False, cwd=str(_ROOT)
    )
    assert completed.returncode == 0, (
        f"keel.mcp.server failed its click-free import check:\n{completed.stdout}\n{completed.stderr}"
    )


# -- the behavioural wall -------------------------------------------------------------------------


def test_stdin_alone_is_not_enough_notifications_produce_no_output() -> None:
    """Two notifications (no `id`) must produce zero bytes: a server that answered
    notifications would be a server that speaks when nobody asked, and the MCP client's
    framing would be the first thing to suffer."""
    reader = io.StringIO(
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId": 1}}\n'
    )
    writer = io.StringIO()
    serve(reader, writer, "no-such.db", "no-such.yaml", "no-such.log")
    assert writer.getvalue() == ""
