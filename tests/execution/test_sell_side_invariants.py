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
WEB_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "close_declared_position",  # P4
        "insert_sell_proposal",  # P6
        "update_sell_proposal",  # P6
    }
)


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


def _reaches_name(node: ast.AST, names: set[str]) -> str | None:
    """Whether `node` reaches one of `names` -- as a `_call_target` (a `Call`, which already
    covers `getattr`), or as a bare LOAD with no call at all. Bypass, issue #895:
    `f = executor._run_order; f()` and `f = broker.place_order; f(spec)` never spell the reach as
    a single `Call` node -- the assignment's right-hand side is a LOAD of the target (an
    `Attribute` or a bare `Name`), which is what this checks in addition to `_call_target`."""
    target = _call_target(node)
    if target is not None and target in names:
        return target
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr in names:
        return node.attr
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names:
        return node.id
    return None


def _calls_in(tree: ast.AST, module: str, names: set[str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if _reaches_name(node, names) is not None:
                found.add((module, func.name))
    return found


def _calls_outside_functions(tree: ast.AST, module: str, names: set[str]) -> set[tuple[str, str]]:
    """`_calls_in`'s counterpart for bypass (e): a call reachable from the module root without
    ever entering a `def`/`async def`. Returns a single `<module>` entry the same way `_calls_in`
    returns one entry per offending function -- there is exactly one pseudo-function to name."""
    for node in _iter_outside_functions(tree):
        if _reaches_name(node, names) is not None:
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

#: The two names with NO legitimate external caller at all -- not even through the pinned
#: `SECOND_LEVEL_CALLERS` set, which governs only the PUBLIC `SECOND_LEVEL_NAMES`. Reaching
#: either of these from outside `executor.py`, under any spelling, is always a bypass (#895).
_PRIVATE_ORDER_REACHING_NAMES = {"_run_order", "_roll_stop"}


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
    `executor_mod`) is not an offender.

    `executor`/the dotted `keel.execution.executor` path itself are scanned too, but only for
    `_PRIVATE_ORDER_REACHING_NAMES` (issue #895): those two names have no legitimate external
    caller under any spelling, unlike the public `SECOND_LEVEL_NAMES`, which the pinned
    `SECOND_LEVEL_CALLERS` set already governs when reached as plain `executor.<name>`."""
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
        # `executor` itself is deliberately NOT in `aliases` above: scanning it for the FULL
        # `_ORDER_REACHING_NAMES` would flag every already-legitimate `SECOND_LEVEL_CALLERS` site
        # (`agent.py`'s `executor.execute`, `reconcile.py`'s `executor.place_bracket`, ...) as a
        # bypass. But `_run_order`/`_roll_stop` have no legitimate caller under ANY name -- not
        # even a pinned second-level one -- so they ARE scanned here, through "executor" itself
        # and the fully dotted module path `_reaches_via_attr` already knows how to match
        # (issue #895): a plain `executor._roll_stop(...)`, `keel.execution.executor._run_order
        # (...)`, and a non-call load of either (`f = executor._run_order; f()`) all reach here.
        hit = _reaches_via_attr(node, "executor", _PRIVATE_ORDER_REACHING_NAMES)
        if hit is not None:
            offenders.append(f"{module}: {func_name} reaches executor.{hit}")

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


def _web_offenders(tree: ast.AST, module: str) -> list[str]:
    """Every sell-side operation `tree` names: a `WEB_FORBIDDEN_NAMES` name as an attribute or a
    bare name, or `executor.reduce` specifically (a bare `reduce` is `functools.reduce`)."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        name = (
            node.attr
            if isinstance(node, ast.Attribute)
            else node.id
            if isinstance(node, ast.Name)
            else None
        )
        if name in WEB_FORBIDDEN_NAMES:
            offenders.append(f"{module}: {name}")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "reduce"
            and isinstance(node.value, ast.Name)
            and node.value.id == "executor"
        ):
            offenders.append(f"{module}: executor.reduce")
    return offenders


def test_the_browser_and_mcp_name_no_sell_side_operation() -> None:
    offenders: list[str] = []
    for root in ("keel/web", "keel/mcp"):
        for path in sorted(glob.glob(os.path.join(_ROOT, root, "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                offenders += _web_offenders(ast.parse(fh.read()), _module_of(path))
    assert offenders == []


def test_the_web_scan_catches_the_sell_proposal_writers() -> None:
    """P6: a proposal row is the input P18's confirm path places from, so writing one from the
    browser would be a capability increase (S4). Both writers, by attribute and by bare name."""
    tree = ast.parse(
        "def sneaky(repo):\n"
        "    repo.insert_sell_proposal({})\n"
        "    update_sell_proposal(1, decision='placed')\n"
        "def honest(repo):\n"
        "    repo.get_sell_proposals()\n"
    )
    assert _web_offenders(tree, "m") == [
        "m: insert_sell_proposal",
        "m: update_sell_proposal",
    ]


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


def test_the_bypass_scan_catches_a_private_name_call_via_the_bare_executor_name() -> None:
    """Issue #895: `from keel.execution import executor` binds the module under its own name,
    which `_executor_bypasses` discards from `aliases` -- scanning it for the full
    `_ORDER_REACHING_NAMES` would flag every already-legitimate `SECOND_LEVEL_CALLERS` site
    (`agent.py`'s `executor.execute`, and so on) as a bypass. But `_roll_stop` has no legitimate
    external caller under ANY name -- not even a pinned second-level one -- so a direct
    `executor._roll_stop(...)` must still be caught."""
    tree = ast.parse(
        "from keel.execution import executor\ndef sneaky():\n    executor._roll_stop(1)\n"
    )
    assert _executor_bypasses(tree, "m") == ["m: sneaky reaches executor._roll_stop"]


def test_the_bypass_scan_catches_a_private_name_call_via_the_dotted_module_path() -> None:
    """The same gap (issue #895), spelled as the fully dotted `keel.execution.executor.<name>`
    path after a plain `import keel.execution.executor` -- no `as`, so `executor` itself is never
    bound as a name."""
    tree = ast.parse(
        "import keel.execution.executor\ndef sneaky():\n    keel.execution.executor._run_order(1)\n"
    )
    assert _executor_bypasses(tree, "m") == ["m: sneaky reaches executor._run_order"]


def test_the_bypass_scan_catches_a_non_call_reference_to_a_private_name() -> None:
    """Issue #895: `f = executor._run_order; f()` never spells the reach as a single `Call`
    node -- the assignment itself is a LOAD of the attribute, which `_reaches_via_attr` catches
    regardless of whether a `Call` ever wraps it."""
    tree = ast.parse(
        "from keel.execution import executor\n"
        "def sneaky():\n"
        "    f = executor._run_order\n"
        "    f(1)\n"
    )
    assert _executor_bypasses(tree, "m") == ["m: sneaky reaches executor._run_order"]


def test_the_call_scan_catches_a_non_call_reference() -> None:
    """Issue #895: `f = broker.place_order; f(spec)` never spells `broker.place_order(...)` as a
    single `Call` node either -- the assignment's right-hand side is a bare LOAD of the
    attribute, which `_calls_in` must match without requiring a `Call` around it."""
    tree = ast.parse("def sneaky():\n    f = broker.place_order\n    f(1)\n")
    assert _calls_in(tree, "m", {"place_order"}) == {("m", "sneaky")}


def test_the_call_scan_catches_a_bare_name_passed_as_a_value() -> None:
    """A from-imported `_run_order` handed to a helper is a bare `Name` LOAD, not a call and not
    an attribute -- the one shape the attribute branch cannot see."""
    tree = ast.parse("def sneaky():\n    retry(_run_order, spec)\n")
    assert _calls_in(tree, "m", {"_run_order"}) == {("m", "sneaky")}


def test_the_module_level_scan_catches_a_non_call_reference() -> None:
    tree = ast.parse("f = broker.place_order\n")
    assert _calls_outside_functions(tree, "m", {"place_order"}) == {("m", "<module>")}


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


#: Everything a SELL path reaches once it stops previewing: the placement pipeline, the bracket
#: cancel `execute`/`scale_out` run first, the cancel under it, the order-row writers, and the
#: broker's own place and cancel. `executor.reduce` names NONE of them before P18 (R17, S1).
_REDUCE_FORBIDDEN_REACHES = {
    "_run_order",
    "place_order",
    "_clear_resting_bracket",
    "_cancel_at_exchange",
    "cancel_order",
    "insert_order",
    "update_order",
    "place_bracket",
    "execute",
    "scale_out",
}


def test_reduce_reaches_no_placement_cancel_or_order_writer() -> None:
    """P7 (R17): `reduce` exists, is scanned, and reaches none of `_REDUCE_FORBIDDEN_REACHES` by
    any spelling the scan knows. The pinned sets above stay unchanged: `reduce` joining one is a
    design change P18 owns, not something to absorb by editing a pin."""
    with open(os.path.join(_ROOT, "keel", "execution", "executor.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
    assert "reduce" in names, "the scan below would pass vacuously without the function"
    reduce_ = ("keel.execution.executor", "reduce")
    for name in sorted(_REDUCE_FORBIDDEN_REACHES):
        assert reduce_ not in _calls_in(tree, "keel.execution.executor", {name}), name
    assert reduce_ not in PLACEMENT_CALLERS | RUN_ORDER_CALLERS | SECOND_LEVEL_CALLERS


def test_the_reduce_scan_is_false_capable() -> None:
    """The same scan, over a `reduce` that cancels the bracket first, names it."""
    tree = ast.parse("def reduce(r):\n    _clear_resting_bracket(b, repo, 'BTC-USD', 0)\n")
    assert _calls_in(tree, "m", {"_clear_resting_bracket"}) == {("m", "reduce")}


#: P8: the ONE function in keel that hands a `Reduction` to `executor.reduce`. A second caller
#: is a design change, not something to absorb by editing this pin.
REDUCE_CALLERS = {("keel.agent", "_handle_reductions")}


def test_the_cycle_reaches_reduce_from_one_function_only() -> None:
    """P8 (S1): `agent._handle_reductions` is `executor.reduce`'s only caller in `keel/`."""
    assert _functions_calling_attr("executor", {"reduce"}) == REDUCE_CALLERS


def test_handle_reductions_reaches_no_placement_cancel_or_order_writer() -> None:
    """P8 (S1): the cycle's reduction step names nothing that places, cancels or writes an order
    -- directly or through `executor.<name>` -- so its one venue-facing call is the preview-only
    `executor.reduce`."""
    with open(os.path.join(_ROOT, "keel", "agent.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    [step] = [
        f
        for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef) and f.name == "_handle_reductions"
    ]
    only = ast.Module(body=[step], type_ignores=[])
    for name in sorted(_REDUCE_FORBIDDEN_REACHES | {"roll_stop_to", "_manage_stops"}):
        assert _calls_in(only, "keel.agent", {name}) == set(), name
    assert _calls_via_attr(only, "keel.agent", "executor", _ORDER_REACHING_NAMES) == set()


# -- S2: v1 execution is preview-only (P9 makes this non-vacuous) -------------------------------


def _sleeve_sell_kinds() -> dict[str, type]:
    from keel.agent import RULE_REGISTRY
    from keel.strategy import promotion

    return {
        kind: cls
        for kind, cls in RULE_REGISTRY.items()
        if promotion.promotion_class_of(cls) == promotion.SLEEVE_SELL
    }


def test_every_sleeve_sell_kind_is_preview_only() -> None:
    """S2: every registered `sleeve_sell` class declares `execution: Literal["preview"]` -- ONE
    choice, so `rules add` can offer nothing else -- and its constructor refuses `"auto"`."""
    import inspect
    from typing import Literal, get_args, get_origin, get_type_hints

    kinds = _sleeve_sell_kinds()
    assert "reverse_dca" in kinds, "vacuous until the first kind ships -- P9 ships it"
    assert "profit_take" in kinds, "P14 ships the second sleeve-sell kind"
    for kind, cls in kinds.items():
        hint = get_type_hints(cls.__init__)["execution"]
        assert get_origin(hint) is Literal, kind
        assert get_args(hint) == ("preview",), kind
        assert inspect.signature(cls).parameters["execution"].default == "preview", kind


def test_no_sleeve_sell_kind_is_seedable() -> None:
    """R20: registered (so `_build_rule` and `rules add` know it), never seeded."""
    from keel import agent

    kinds = _sleeve_sell_kinds()
    assert kinds, "vacuous without a sleeve-sell kind"
    seedable = agent.seedable_kinds()
    assert set(kinds).isdisjoint(seedable)
    # Nothing else is dropped: every non-sleeve kind is still seeded, in registry order.
    assert seedable == [k for k in agent.RULE_REGISTRY if k not in kinds]
