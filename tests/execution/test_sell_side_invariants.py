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
from collections.abc import Iterator

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


def _dotted(node: ast.AST) -> str | None:
    """Flatten a pure `Name`/`Attribute` chain (`a.b.c`) to `"a.b.c"`, or `None` if `node` is not
    one -- a `Call`, a `Subscript`, anything with a side effect in the chain, is not a dotted
    name and cannot be `getattr`-string-matched or module-path-matched below."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _getattr_literal(node: ast.AST) -> str | None:
    """The literal string in `getattr(x, "name")` / `getattr(x, "name", default)`, or `None`.
    Bypass (c), issue #891: `getattr(broker, "place_order")(...)` never appears as a
    `broker.place_order(...)` `Call` node -- there is no `Attribute` node at all -- so it has to
    be matched on the string constant instead, regardless of what `x` is."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value
    return None


def _iter_outside_functions(node: ast.AST) -> Iterator[ast.AST]:
    """Yield every descendant of `node` that is not inside a `def`/`async def` -- module-level
    statements, class bodies, comprehensions, and a module-level lambda's body all count. The
    body of a nested function is skipped entirely: `_calls_in`/`_calls_via_attr` already walk it
    by name, and re-visiting it here would double-attribute its calls to `<module>` too.

    Bypass (e), issue #891: a call or attribute reach that is never inside any named function --
    module-level code, or a lambda assigned but never invoked from inside one -- is invisible to
    a scan keyed on `FunctionDef`/`AsyncFunctionDef` nodes. Everything this yields is scanned
    under the `<module>` pseudo-function name instead of silently vanishing.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        yield child
        yield from _iter_outside_functions(child)


def _call_target(node: ast.AST) -> str | None:
    """The name a `Call` node invokes -- bare (`f()`), attribute (`obj.f()`), or the literal
    string of a `getattr(obj, "f")(...)` -- or `None` if `node` is not a `Call` at all.

    `getattr` is checked FIRST and specifically on this node, not as a fallback: for
    `getattr(broker, "place_order")(spec)` the INNER `getattr(...)` call is itself a `Call` node
    ast.walk visits separately, and its own `.func` is `Name(id="getattr")` -- resolving that
    through the bare-name branch below would report the callable as `"getattr"` and never reach
    the literal string at all.
    """
    if not isinstance(node, ast.Call):
        return None
    literal = _getattr_literal(node)
    if literal is not None:
        return literal
    callee = node.func
    return (
        callee.id
        if isinstance(callee, ast.Name)
        else callee.attr
        if isinstance(callee, ast.Attribute)
        else None
    )


def _calls_in(tree: ast.AST, module: str, names: set[str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if _call_target(node) in names:
                found.add((module, func.name))
    return found


def _calls_outside_functions(tree: ast.AST, module: str, names: set[str]) -> set[tuple[str, str]]:
    """`_calls_in`'s counterpart for bypass (e): a call reachable from the module root without
    ever entering a `def`/`async def`. Returns a single `<module>` entry the same way `_calls_in`
    returns one entry per offending function -- there is exactly one pseudo-function to name."""
    for node in _iter_outside_functions(tree):
        if _call_target(node) in names:
            return {(module, "<module>")}
    return set()


def _functions_calling(names: set[str], roots: tuple[str, ...] = ("keel",)) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            module = _module_of(path)
            found |= _calls_in(tree, module, names)
            found |= _calls_outside_functions(tree, module, names)
    return found


_EXECUTOR_MODULE = "keel.execution.executor"

#: Every executor function that reaches `_run_order`, by any name a caller could import.
_ORDER_REACHING_NAMES = SECOND_LEVEL_NAMES | {"_run_order", "_roll_stop"}


def _attr_reach(node: ast.AST, base: str, names: set[str]) -> str | None:
    """Whether `node` is a LOAD of `<base>.<name>` for some `name` in `names` -- a call
    (`base.name(...)`), a bare reference (`f = base.name`), or anything else that reads the
    attribute. Bypass (b), issue #891: binding the attribute to a name and calling THAT never
    spells `base.name(...)` as a `Call` -- but the assignment itself is a LOAD of the attribute,
    which is what this checks instead of requiring a `Call` around it."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Load)
        and node.attr in names
        and _dotted(node.value) == base
    ):
        return node.attr
    return None


def _reaches_via_attr(node: ast.AST, base: str, names: set[str]) -> str | None:
    """`_attr_reach`, plus the two shapes it cannot express because they are not `<base>.<name>`
    at all: bypass (a), issue #891 -- `keel.execution.executor.<name>` used as the fully dotted
    module path after a plain `import keel.execution.executor` (no `as`, so `executor` itself is
    never bound as a name; only when `base` is literally `"executor"`, the one alias `_attr_reach`
    is always called with here) -- and bypass (c), a `getattr(x, "<name>")` whose target is a
    string constant, matched regardless of `x` or `base`."""
    hit = _attr_reach(node, base, names)
    if hit is not None:
        return hit
    if (
        base == "executor"
        and isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Load)
        and node.attr in names
        and _dotted(node.value) == _EXECUTOR_MODULE
    ):
        return node.attr
    tgt = _getattr_literal(node)
    return tgt if tgt in names else None


def _calls_via_attr(tree: ast.AST, module: str, base: str, names: set[str]) -> set[tuple[str, str]]:
    """Like `_calls_in`, but anchored to `<base>.<name>` specifically -- a call, a bare reference,
    a `getattr`, or the fully dotted module path (see `_reaches_via_attr`). Anchoring on `base`
    is what keeps `executor.execute` from being confused with `conn.execute`, `repo.execute` or
    any other `.execute` in the tree: `_calls_in` matches on the bare method name alone and would
    treat all of them as the same caller.
    """
    found: set[tuple[str, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if _reaches_via_attr(node, base, names) is not None:
                found.add((module, func.name))
    return found


def _attr_outside_functions(
    tree: ast.AST, module: str, base: str, names: set[str]
) -> set[tuple[str, str]]:
    """`_calls_via_attr`'s counterpart for bypass (e): a reach through `<base>.<name>` (or one of
    `_reaches_via_attr`'s other shapes) that sits outside every `def`/`async def`."""
    for node in _iter_outside_functions(tree):
        if _reaches_via_attr(node, base, names) is not None:
            return {(module, "<module>")}
    return set()


def _functions_calling_attr(
    base: str, names: set[str], roots: tuple[str, ...] = ("keel",)
) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            module = _module_of(path)
            found |= _calls_via_attr(tree, module, base, names)
            found |= _attr_outside_functions(tree, module, base, names)
    return found


def _executor_bypasses(tree: ast.AST, module: str) -> list[str]:
    """The shapes that reach an order path WITHOUT spelling `executor.<name>` as a plain
    `Name`-rooted attribute, which is all `_functions_calling_attr` can see once `base` is fixed
    to `"executor"` (the fully dotted `keel.execution.executor.<name>` path and a `getattr` are
    already covered there via `_reaches_via_attr`, so they are not re-scanned here): a star
    import, `from keel.execution.executor import <order-reaching name>`, and a reach through any
    OTHER name the module binds to the executor module -- as a call, a bare reference, or at
    module scope, outside every function. An alias used only for constants (`doctor.py`'s
    `executor_mod`) is not an offender."""
    offenders: list[str] = []
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _EXECUTOR_MODULE:
            for a in node.names:
                if a.name == "*":
                    offenders.append(f"{module}: star-imports from {_EXECUTOR_MODULE}")
                elif a.name in _ORDER_REACHING_NAMES:
                    offenders.append(f"{module}: imports {a.name} from {_EXECUTOR_MODULE}")
        elif isinstance(node, ast.ImportFrom) and node.module == "keel.execution":
            aliases |= {a.asname for a in node.names if a.name == "executor" and a.asname}
        elif isinstance(node, ast.Import):
            aliases |= {a.asname for a in node.names if a.name == _EXECUTOR_MODULE and a.asname}
    aliases.discard("executor")

    def _scan(node: ast.AST, func_name: str) -> None:
        for alias in aliases:
            hit = _attr_reach(node, alias, _ORDER_REACHING_NAMES)
            if hit is not None:
                offenders.append(f"{module}: {func_name} reaches {alias}.{hit}")

    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            _scan(node, func.name)

    for node in _iter_outside_functions(tree):
        _scan(node, "<module>")

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
        "m: sneaky reaches ex.execute",
        "m: sneaky reaches ex2.place_bracket",
    ]


def test_the_attr_scan_catches_a_dotted_import_without_an_alias() -> None:
    """Bypass (a), issue #891: `import keel.execution.executor` binds `keel`, not `executor`, so
    `keel.execution.executor.scale_out(...)` never spells `executor.<name>` as a `Name`-rooted
    attribute -- it is matched on the fully dotted module path instead (`_reaches_via_attr`)."""
    tree = ast.parse(
        "import keel.execution.executor\ndef sneaky():\n    keel.execution.executor.scale_out(1)\n"
    )
    assert _calls_via_attr(tree, "m", "executor", {"scale_out"}) == {("m", "sneaky")}


def test_the_attr_scan_catches_a_non_call_reference() -> None:
    """Bypass (b): binding `executor.scale_out` to a name and calling THAT never spells
    `executor.scale_out(...)` as a Call -- but the assignment itself is a LOAD of the attribute,
    which is what the scan keys on instead of requiring a Call around it."""
    tree = ast.parse("def sneaky():\n    f = executor.scale_out\n    f(1)\n")
    assert _calls_via_attr(tree, "m", "executor", {"scale_out"}) == {("m", "sneaky")}


def test_the_attr_scan_catches_getattr_with_an_order_reaching_name() -> None:
    """Bypass (c) against `_calls_via_attr` (the SECOND_LEVEL scan): `getattr(x, "scale_out")`
    never appears as an `executor.scale_out` Attribute node at all -- matched on the string
    constant instead, regardless of what `x` is."""
    tree = ast.parse("def sneaky():\n    getattr(anything, 'scale_out')(1)\n")
    assert _calls_via_attr(tree, "m", "executor", {"scale_out"}) == {("m", "sneaky")}


def test_the_call_scan_catches_getattr_with_an_order_reaching_name() -> None:
    """The same bypass (c) against `_calls_in` (behind `PLACEMENT_CALLERS`/`RUN_ORDER_CALLERS`):
    `getattr(broker, "place_order")(...)` never appears as a `broker.place_order(...)` Call
    node."""
    tree = ast.parse("def sneaky():\n    getattr(broker, 'place_order')(spec)\n")
    assert _calls_in(tree, "m", {"place_order"}) == {("m", "sneaky")}


def test_the_import_scan_catches_a_star_import() -> None:
    """Bypass (d): `from keel.execution.executor import *` brings every order-reaching name into
    the importer's namespace under no name the scan can pin by identifier -- flagged outright,
    not by the names it happens to bind."""
    tree = ast.parse("from keel.execution.executor import *\n")
    assert _executor_bypasses(tree, "m") == ["m: star-imports from keel.execution.executor"]


def test_the_call_scan_catches_a_module_level_lambda() -> None:
    """Bypass (e): a lambda bound at module scope is never inside a `def`, so `_calls_in` --
    which only walks the bodies of named functions -- never visits it and reports nothing at
    all for this module. `_calls_outside_functions` is the counterpart that does, naming the
    offending scope `<module>` instead of leaving it invisible."""
    tree = ast.parse("sneaky = lambda: broker.place_order(spec)\n")
    assert _calls_in(tree, "m", {"place_order"}) == set()
    assert _calls_outside_functions(tree, "m", {"place_order"}) == {("m", "<module>")}


def test_the_attr_scan_also_covers_the_module_level_shape() -> None:
    """The same containment gap against the attribute scan: an `executor.scale_out` reference
    sitting directly in module-level code, with no enclosing `def` at all for `_calls_via_attr`
    to walk."""
    tree = ast.parse("import keel.execution.executor as executor\nexecutor.scale_out(1)\n")
    assert _calls_via_attr(tree, "m", "executor", {"scale_out"}) == set()
    assert _attr_outside_functions(tree, "m", "executor", {"scale_out"}) == {("m", "<module>")}


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
