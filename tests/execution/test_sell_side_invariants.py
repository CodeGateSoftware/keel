"""The sell-side build's safety invariants, S1-S4 (docs/superpowers/plans/2026-09-28-dca-sleeve-
sell-side-build.md). Every PR in that plan runs this module.

S1 is enforced by an INVENTORY, the way `tests/test_capabilities.py` inventories gate call sites:
the set of functions that can reach the venue's order endpoint is pinned. A new caller fails here
until a ruling declares it. That is how "no new sell reaches the venue unless the sells window
is armed" stays a checked fact rather than a promise: `executor.reduce` cannot join
`RUN_ORDER_CALLERS` until P18, and P18's own test asserts the call sits behind
`sleeve.sells_released`.

`RUN_ORDER_CALLERS` alone only pins the functions that call `_run_order` directly, all four of
which live inside `executor.py` itself. A new sell path added anywhere ELSE in the codebase --
say a CLI command in `keel/commands/dca.py` that calls `executor.execute(EXIT)` or
`executor.scale_out(...)` straight from its handler -- never appears there and this module stays
green. `SECOND_LEVEL_CALLERS` closes that gap: it pins every function, anywhere under `keel`,
that calls one of `executor`'s order-reaching entry points by name (`executor.<name>`), so a new
second-level caller turns this module red the same way a new first-level one does.
"""

from __future__ import annotations

import ast
import glob
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Only `_run_order` may call `broker.place_order`.
PLACEMENT_CALLERS = {("keel.execution.executor", "_run_order")}

#: Every function that may hand an order to `_run_order`. Each is a pre-existing path:
#: the rule ENTER/EXIT (`execute`), the protective bracket, the #502 scale-out, and the
#: ratchet roll.
RUN_ORDER_CALLERS = {
    ("keel.execution.executor", "execute"),
    ("keel.execution.executor", "place_bracket"),
    ("keel.execution.executor", "scale_out"),
    ("keel.execution.executor", "_roll_stop"),
}

#: The names a caller OUTSIDE `executor.py` spells, as `executor.<name>`, to reach one of
#: `RUN_ORDER_CALLERS`'s four functions. `execute`, `place_bracket` and `scale_out` are the
#: names directly; `_roll_stop` is private and has no external caller today -- it is reached
#: only through its three wrapper functions (`roll_stop_to`, `roll_to_break_even`,
#: `trail_stop_atr`), so those three stand in for it here.
SECOND_LEVEL_NAMES = {
    "execute",
    "place_bracket",
    "scale_out",
    "roll_stop_to",
    "roll_to_break_even",
    "trail_stop_atr",
}

#: Every function, anywhere under `keel`, that calls `executor.<name>` for a name in
#: `SECOND_LEVEL_NAMES`. Today: the rule ENTER/EXIT dispatch (`run_once`, `_handle_exits`), the
#: stop-management loop (`_manage_stops`, via `roll_stop_to`), and the two reconcile sweeps that
#: re-place a missing bracket (via `place_bracket`). `scale_out`'s wrapper names have no external
#: caller yet -- the #502 CLI path is not built -- so none appears below for them.
SECOND_LEVEL_CALLERS = {
    ("keel.agent", "_handle_exits"),
    ("keel.agent", "_manage_stops"),
    ("keel.agent", "run_once"),
    ("keel.execution.reconcile", "reconcile_unbracketed_positions"),
    ("keel.execution.reconcile", "_rebracket_or_escalate"),
}

#: Operations the browser and the MCP server must never name (S4). Grown by the PR that
#: introduces each one.
WEB_FORBIDDEN_NAMES: frozenset[str] = frozenset()


def _module_of(path: str) -> str:
    rel = os.path.relpath(path, _ROOT)
    return rel[: -len(".py")].replace(os.sep, ".").removesuffix(".__init__")


def _calls_in(tree: ast.AST, module: str, names: set[str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = (
                callee.id
                if isinstance(callee, ast.Name)
                else callee.attr
                if isinstance(callee, ast.Attribute)
                else None
            )
            if name in names:
                found.add((module, func.name))
    return found


def _functions_calling(names: set[str], roots: tuple[str, ...] = ("keel",)) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                found |= _calls_in(ast.parse(fh.read()), _module_of(path), names)
    return found


def _calls_via_attr(tree: ast.AST, module: str, base: str, names: set[str]) -> set[tuple[str, str]]:
    """Like `_calls_in`, but only counts `<base>.<name>(...)` -- an attribute call whose object is
    literally the name `base`. This is what keeps `executor.execute` from being confused with
    `conn.execute`, `repo.execute` or any other `.execute(...)` in the tree: `_calls_in` matches on
    the bare method name alone and would treat all of them as the same caller.
    """
    found: set[tuple[str, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in names
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == base
            ):
                found.add((module, func.name))
    return found


def _functions_calling_attr(
    base: str, names: set[str], roots: tuple[str, ...] = ("keel",)
) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                found |= _calls_via_attr(ast.parse(fh.read()), _module_of(path), base, names)
    return found


_EXECUTOR_MODULE = "keel.execution.executor"

#: Every executor function that reaches `_run_order`, by any name a caller could import.
_ORDER_REACHING_NAMES = SECOND_LEVEL_NAMES | {"_run_order", "_roll_stop"}


def _executor_bypasses(tree: ast.AST, module: str) -> list[str]:
    """The two shapes that reach an order path WITHOUT spelling `executor.<name>`, which is all
    `_functions_calling_attr` can see: `from keel.execution.executor import <order-reaching
    name>`, and a call through any OTHER name the module binds to the executor module. An alias
    used only for constants (`doctor.py`'s `executor_mod`) is not an offender."""
    offenders: list[str] = []
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _EXECUTOR_MODULE:
            offenders += [
                f"{module}: imports {a.name} from {_EXECUTOR_MODULE}"
                for a in node.names
                if a.name in _ORDER_REACHING_NAMES
            ]
        elif isinstance(node, ast.ImportFrom) and node.module == "keel.execution":
            aliases |= {a.asname for a in node.names if a.name == "executor" and a.asname}
        elif isinstance(node, ast.Import):
            aliases |= {a.asname for a in node.names if a.name == _EXECUTOR_MODULE and a.asname}
    aliases.discard("executor")
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases
                and node.func.attr in _ORDER_REACHING_NAMES
            ):
                offenders.append(
                    f"{module}: {func.name} calls {node.func.value.id}.{node.func.attr}"
                )
    return offenders


def test_only_run_order_calls_place_order() -> None:
    assert _functions_calling({"place_order"}) == PLACEMENT_CALLERS


def test_the_run_order_callers_are_exactly_the_pinned_set() -> None:
    assert _functions_calling({"_run_order"}) == RUN_ORDER_CALLERS


def test_the_second_level_callers_are_exactly_the_pinned_set() -> None:
    assert _functions_calling_attr("executor", SECOND_LEVEL_NAMES) == SECOND_LEVEL_CALLERS


def test_the_scan_is_false_capable() -> None:
    tree = ast.parse("def sneaky():\n    executor._run_order(1)\n")
    assert _calls_in(tree, "m", {"_run_order"}) == {("m", "sneaky")}


def test_the_attr_scan_does_not_confuse_executor_execute_with_conn_execute() -> None:
    tree = ast.parse(
        "def sneaky():\n    conn.execute('SELECT 1')\ndef honest():\n    executor.execute(1)\n"
    )
    assert _calls_via_attr(tree, "m", "executor", {"execute"}) == {("m", "honest")}


def test_the_browser_and_mcp_name_no_sell_side_operation() -> None:
    offenders: list[str] = []
    for root in ("keel/web", "keel/mcp"):
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                name = (
                    node.attr
                    if isinstance(node, ast.Attribute)
                    else node.id
                    if isinstance(node, ast.Name)
                    else None
                )
                if name in WEB_FORBIDDEN_NAMES:
                    offenders.append(f"{_module_of(path)}: {name}")
                # `executor.reduce` specifically -- a bare `reduce` is `functools.reduce`.
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "reduce"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "executor"
                ):
                    offenders.append(f"{_module_of(path)}: executor.reduce")
    assert offenders == []


def test_the_import_scan_catches_a_direct_import_and_an_alias() -> None:
    tree = ast.parse(
        "from keel.execution.executor import scale_out\n"
        "from keel.execution import executor as ex\n"
        "import keel.execution.executor as ex2\n"
        "def sneaky():\n"
        "    scale_out(1)\n"
        "    ex.execute(1)\n"
        "    ex2.place_bracket(1)\n"
        "def harmless():\n"
        "    ex.BALANCE_DRIFT_PREFIX\n"
    )
    assert _executor_bypasses(tree, "m") == [
        "m: imports scale_out from keel.execution.executor",
        "m: sneaky calls ex.execute",
        "m: sneaky calls ex2.place_bracket",
    ]


def test_no_module_reaches_the_executor_order_paths_under_another_name() -> None:
    """`SECOND_LEVEL_CALLERS` is scanned as `executor.<name>`. Importing an order-reaching
    function by name, or binding the module under an alias (`doctor.py` has `executor_mod`),
    would call the same function where that scan cannot see it -- so both shapes are pinned
    shut here, and the only way to reach them from outside `executor.py` stays the scanned one.
    """
    offenders: list[str] = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "keel", "**", "*.py"), recursive=True)):
        module = _module_of(path)
        if module == "keel.execution.executor":
            continue
        with open(path, encoding="utf-8") as fh:
            offenders += _executor_bypasses(ast.parse(fh.read()), module)
    assert offenders == []
