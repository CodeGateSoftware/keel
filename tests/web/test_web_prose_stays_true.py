"""Counts and names in `keel/web/` prose, checked against the code they describe (#967).

Each of these was a sentence that had been true once and was still being read: a refresh constant
whose pages were deleted at #540, a "seven" that became eight when a capability row was added,
and a "four checks" that missed `Sec-Fetch-Site`. A phrase ban would catch only the exact wording
that went stale. These tests derive the number or the name from the code instead, so the next
edit that changes the code fails here until the prose is changed with it.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

from keel.capabilities import BROWSER, CAPABILITIES
from keel.web import gates, server

WEB = Path(server.__file__).resolve().parent
KEEL = WEB.parent

_NUMBER_WORDS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_PRIVATE_CONSTANT = re.compile(r"_[A-Z][A-Z0-9_]*")


def _module_constants(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [name for name in names if _PRIVATE_CONSTANT.fullmatch(name)]


def test_every_private_constant_in_keel_web_has_a_reader_in_its_module() -> None:
    """A private constant nothing reads is a comment pretending to be configuration.
    `server._REFRESH_SEC` described pages that reload themselves; #540 removed the pages and the
    constant stayed, still answering "how often does the console refresh" with a number that
    drives nothing (the client polls on `main.js`'s own `POLL_MS`)."""
    dead: dict[str, list[str]] = {}
    for path in sorted(WEB.glob("*.py")):
        tree = ast.parse(path.read_text())
        loaded = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        unread = [name for name in _module_constants(tree) if name not in loaded]
        if unread:
            dead[path.name] = unread

    assert dead == {}


def test_a_cited_private_server_constant_exists() -> None:
    """Prose elsewhere in keel that names `server._SOMETHING` is naming a number a reader will
    go and look up. `events.HEARTBEAT_SEC` and `api._SCREEN_CACHE_TTL_SECONDS` both justified
    themselves against `server._REFRESH_SEC` after it stopped meaning anything."""
    cited = {
        (path.relative_to(KEEL.parent).as_posix(), match.group(1))
        for path in sorted(KEEL.rglob("*.py"))
        for match in re.finditer(r"`server\.(_[A-Z][A-Z0-9_]*)`", path.read_text())
    }
    missing = sorted((where, name) for where, name in cited if not hasattr(server, name))

    assert missing == []


def _stated_count(text: str, pattern: str) -> int:
    match = re.search(pattern, " ".join(text.split()))
    assert match, f"the sentence matching {pattern!r} is gone; re-point this test at its successor"
    return _NUMBER_WORDS[match.group(1).lower()]


def test_the_gates_module_counts_the_actions_it_refuses_from_the_inventory() -> None:
    """`gates`' docstring says how many capability increases the browser gate refuses outright.
    That is every action in `keel.capabilities` that no `web` row mirrors -- eight of the ten
    since the inventory grew, while the docstring still said seven."""
    browser_mirrored = {cap.mirrors for cap in CAPABILITIES if cap.gate == BROWSER.name}
    terminal_only = [
        cap
        for cap in CAPABILITIES
        if cap.gate != BROWSER.name and (cap.module, cap.function) not in browser_mirrored
    ]

    stated = _stated_count(gates.__doc__ or "", r"The other (\w+) are refused")

    assert len(terminal_only) == stated


def _refusing_guards(method: object, *, stop_at: str | None = None) -> list[ast.If]:
    """The top-level `if not ...: return` guards of a handler method, in order, up to (not
    including) the statement whose source mentions `stop_at`."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))  # type: ignore[arg-type]
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    guards: list[ast.If] = []
    for statement in function.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue  # the docstring, which names `_gated_action` itself
        if stop_at is not None and stop_at in ast.unparse(statement):
            return guards
        if isinstance(statement, ast.If) and isinstance(statement.test, ast.UnaryOp):
            if isinstance(statement.test.op, ast.Not):
                guards.append(statement)
    assert stop_at is None, f"{stop_at!r} was never reached"
    return guards


def test_the_phrase_check_names_its_place_among_every_check_before_it() -> None:
    """`run_gated_action` is the LAST check a release passes. Its docstring says how many come
    first and which; both are read here from the two handler methods that run them, so a check
    added to `do_POST` (as `Sec-Fetch-Site` was) fails this until the docstring counts it."""
    handler = server.KeelHandler
    before_dispatch = _refusing_guards(handler.do_POST, stop_at="_gated_action")
    in_gated_action = _refusing_guards(handler._gated_action)
    checks = len(before_dispatch) + len(in_gated_action) + 1  # + the phrase itself

    doc = inspect.getdoc(gates.run_gated_action) or ""
    assert _stated_count(doc, r"LAST of (\w+) checks") == checks

    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for guard in [*before_dispatch, *in_gated_action]
        for node in ast.walk(guard.test)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute | ast.Name)
    } - {"startswith", "get", "tokens_match"}
    unnamed = sorted(name for name in called if name not in doc)
    assert unnamed == [], f"run_gated_action's docstring does not name {unnamed}"
