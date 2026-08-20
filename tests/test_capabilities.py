"""The capability inventory, checked against the source in BOTH directions (#436).

`keel/capabilities.py` declares every action that increases what keel can do without asking
again, and which gate covers it. A declaration nobody checks is worse than no declaration: it
reads like an audit and ages into fiction. So this file is the check, and it fails two ways --

* a `_require_interactive_confirmation` call site that is NOT in the inventory fails here, so a
  new gated action cannot be added silently;
* an inventory row whose call site has disappeared fails here, so a gate that was quietly removed
  cannot leave a row behind claiming it is still covered.

The second direction is the one that matters. An action losing its gate is invisible in a diff
that only adds -- and the row left behind would go on telling an auditor it was covered.
"""

from __future__ import annotations

import ast
import glob
import importlib
import os
from typing import Any

import pytest

from keel.capabilities import CAPABILITIES, GATES, TTY, Capability, gate_named, render_lines

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The gate function every capability-increasing action must pass through.
GATE_FUNCTION = "_require_interactive_confirmation"


def _python_sources() -> list[str]:
    """Everything shipped: the CLI tree and every workspace package. A gate call in an adapter
    would be found here too -- the scan is not limited to where gates happen to live today."""
    paths = glob.glob(os.path.join(_ROOT, "keel", "**", "*.py"), recursive=True)
    paths += glob.glob(os.path.join(_ROOT, "packages", "*", "keel_*", "**", "*.py"), recursive=True)
    return sorted(paths)


def _dotted(path: str) -> str:
    rel = os.path.relpath(path, _ROOT)
    if rel.startswith("packages" + os.sep):
        rel = os.sep.join(rel.split(os.sep)[2:])
    return rel[: -len(".py")].replace(os.sep, ".").removesuffix(".__init__")


def _call_sites_in(source: str, module: str) -> set[tuple[str, str]]:
    """(module, enclosing function) for every call to the gate, excluding its own definition and
    the import that brings the name into scope."""
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
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else None
        )
        if name == GATE_FUNCTION:
            found.add((module, owners.get(node, "<module>")))
    return found


def _scan() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in _python_sources():
        module = _dotted(path)
        sites |= _call_sites_in(open(path, encoding="utf-8").read(), module)
    return sites


# -- the pin -------------------------------------------------------------------------------


def test_the_inventory_matches_every_gate_call_site_exactly() -> None:
    scanned = _scan()
    declared = {cap.key for cap in CAPABILITIES}

    undeclared = scanned - declared
    assert not undeclared, (
        "these call sites gate an action that the inventory does not declare -- add them to "
        f"keel/capabilities.py: {sorted(undeclared)}"
    )

    stale = declared - scanned
    assert not stale, (
        "these inventory rows claim a gate that no longer exists in the source. Either the gate "
        f"was removed (a safety regression) or the row is stale: {sorted(stale)}"
    )


def test_the_scan_is_proven_false_capable() -> None:
    """A scan that silently matched nothing would make the pin vacuously green in both
    directions."""
    snippet = (
        "def dangerous():\n"
        f"    {GATE_FUNCTION}('do the thing', 'detail')\n"
        "def harmless():\n"
        "    pass\n"
    )
    assert _call_sites_in(snippet, "fake.module") == {("fake.module", "dangerous")}


def test_the_scan_actually_reached_the_source_tree() -> None:
    paths = _python_sources()
    assert len(paths) > 100
    assert any(path.endswith(os.path.join("keel", "cli.py")) for path in paths)
    assert any(os.sep + "packages" + os.sep in path for path in paths)


def test_every_declared_row_resolves_to_a_real_function() -> None:
    """A row naming a function that no longer exists is a hole, not a pin."""
    for cap in CAPABILITIES:
        module: Any = importlib.import_module(cap.module)
        assert hasattr(module, cap.function), f"{cap.module}.{cap.function}"


def test_every_row_names_a_declared_gate() -> None:
    for cap in CAPABILITIES:
        assert gate_named(cap.gate) is not None


def test_every_mirror_points_at_a_row_in_the_inventory() -> None:
    """`mirrors` is what makes the duplication legible: the same action reached from a second
    front-end. A mirror pointing at nothing would make it noise instead."""
    keys = {cap.key for cap in CAPABILITIES}
    for cap in CAPABILITIES:
        if cap.mirrors is not None:
            assert cap.mirrors in keys, f"{cap.key} mirrors {cap.mirrors}, which is not declared"
            assert cap.mirrors != cap.key


def test_a_mirror_is_never_a_cli_row() -> None:
    """The CLI row is the original; console and TUI rows mirror it. A CLI row claiming to mirror
    something would mean the inventory had lost track of which is which."""
    for cap in CAPABILITIES:
        if cap.surface == "cli":
            assert cap.mirrors is None, cap.key


def test_the_surfaces_are_a_closed_vocabulary() -> None:
    assert {cap.surface for cap in CAPABILITIES} <= {"cli", "console", "tui"}


# -- what the gate itself must remain --------------------------------------------------------


def test_the_gate_has_exactly_one_implementation() -> None:
    """Every front-end imports the same function. A second definition would be a second
    ceremony, and the console modules' own docstrings promise there is not one."""
    definitions = []
    for path in _python_sources():
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == GATE_FUNCTION:
                definitions.append(_dotted(path))
    assert definitions == ["keel.commands._common"], definitions


def test_the_tty_predicate_has_no_environment_seam() -> None:
    """`_is_interactive`'s docstring says it deliberately has no env-var or flag override,
    because such a seam would be settable from cron and would dissolve every fail-closed built on
    it. This asserts the code still matches the promise: nothing in its body reads the
    environment, argv, or a config value -- it reads stdin and nothing else.

    Pinned rather than trusted because the seam would arrive as a convenience, in a change whose
    diff looks helpful, and it would be invisible in the one place it matters: a cron-driven live
    cycle that stops failing closed."""
    source = open(os.path.join(_ROOT, "keel", "commands", "_common.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    body = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_is_interactive"
    )
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(body)
        if isinstance(node, ast.Attribute | ast.Name)
    }
    for forbidden in ("environ", "getenv", "argv", "get", "config"):
        assert forbidden not in names, (
            f"_is_interactive reads `{forbidden}` -- the TTY gate has grown an override seam, "
            "which is settable from cron and defeats every fail-closed built on it"
        )
    assert "isatty" in names and "stdin" in names


def test_the_gate_is_the_only_thing_that_stops_anything() -> None:
    """The registry must stay a DECLARATION. If `keel/capabilities.py` were consulted at runtime
    it would become a second place to get the safety model wrong, and the two could disagree."""
    source = open(os.path.join(_ROOT, "keel", "capabilities.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported <= {"__future__", "dataclasses"}, (
        f"keel/capabilities.py imports {sorted(imported)} -- it is an inventory, not a gate, and "
        "importing keel machinery is how it would become load-bearing"
    )


# -- the rendering ---------------------------------------------------------------------------


def test_every_gate_and_every_action_appears_in_the_rendered_inventory() -> None:
    text = "\n".join(render_lines())
    for gate in GATES:
        assert gate.name in text
        assert gate.implementation in text
    for cap in CAPABILITIES:
        assert cap.invocation in text
        assert f"{cap.module}.{cap.function}" in text


def test_the_only_gate_today_is_the_tty_one() -> None:
    """#436's browser gate becomes a second `Gate` here, and this test is where that change
    announces itself -- so adding one cannot be a quiet edit."""
    assert GATES == (TTY,)
    assert {cap.gate for cap in CAPABILITIES} == {"tty"}


@pytest.mark.parametrize("cap", CAPABILITIES, ids=lambda cap: f"{cap.module}.{cap.function}")
def test_every_row_says_what_it_grants(cap: Capability) -> None:
    """`increases` is the field an auditor actually reads. An empty or copy-pasted one would make
    the inventory look complete while saying nothing."""
    assert len(cap.increases) > 30
    assert cap.invocation
    assert len({c.increases for c in CAPABILITIES}) == len(CAPABILITIES)
