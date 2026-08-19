"""The architectural thinness pin (issue #392 C6; PRD §6.2 -- "grep-able proof of no
duplicated logic ... pinned by an architectural test").

WHAT IT FORBIDS, precisely. The TUI layer is the console shell (`keel/commands/console.py`),
the live loop (`keel/commands/tui.py`), and every console sub-menu module
(`keel/commands/*console*.py`). Those files render and dispatch; all behavior must come
from the services the CLI calls. The pin is an AST scan enforcing four rules:

* **Rule 1 -- no compute-module imports.** Nothing may be imported from the compute
  trees -- `keel.strategy.*` (sizing/backtest/promotion math), `keel.execution.guards` /
  `keel.execution.sizing` / `keel.execution.executor` (the rails and the order path),
  `keel.compliance.screen` / `keel.compliance.purification` (screening and the §65.9
  report), `keel.analysis.*` (FIFO/P&L math) -- EXCEPT (a) CONSTANTS and TYPES, read
  from the source module's own AST (a class, or an ALL_CAPS module-level assignment):
  the console modules legitimately import gate WORDING constants and screen vocabulary
  (e.g. `KNOWN_BACKINGS`, `WITHDRAWAL_ATTESTATION_TTL_SEC`) and constructing a frozen
  parameter dataclass is keyword passthrough, not math; (b) submodules (their CALLS are
  Rule 2's business); (c) the audited `IMPORT_ALLOWLIST` below, whose every entry also
  carries a Rule-2 call allowance.
* **Rule 2 -- no compute-module calls.** No call whose callee resolves (through the
  import aliases, at any scope depth) into a compute tree, unless the exact
  (console module, enclosing function, resolved callee) is in `CALL_ALLOWLIST` -- each
  entry justified inline. This is the rule that makes "no sizing/screening/gating/
  reporting math in the TUI" mechanical: a new `size(...)`/`check(...)`/`backtest(...)`
  call in a console module FAILS here.
* **Rule 3 -- Decimal is display-only.** No arithmetic operator (+, -, *, /, //, %, **)
  may be applied to anything under a `Decimal(...)` construction -- money values may be
  read, formatted and passed to services, never computed with in the TUI layer.
* **Rule 4 -- no broker construction outside the seams.** `_build_broker`/`load_broker`
  may be called only (a) inside a `lambda` passed as a `build_broker`/`build_client`
  keyword to a service call -- the established `run_fn` seam where the agent
  cycle/monitor/fetch/simulate services take their venue handle -- or (b) at the audited
  read sites in `READ_SITES` below (the dashboard's own bounded balance/product reads
  and the offline capability display). Combined with Rule 2 (the executor allowlist
  admits only the two READ helpers), there is no TUI-originated order path: the sole
  order-capable dispatch is `agent.run_once`, which is not a compute-module call.

The allowlists are deliberately entry-scoped (module + enclosing function + callee), so
an allowance cannot leak to a new call site: the same callee at a different place, or a
different callee at the allowed place, both fail.
"""

from __future__ import annotations

import ast
import glob
import importlib
import os
from typing import Any

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The console layer: the shell, the live loop, and every sub-menu module.
def _console_module_paths() -> list[str]:
    paths = [os.path.join(REPO_ROOT, "keel", "commands", name) for name in ("console.py", "tui.py")]
    paths.extend(sorted(glob.glob(os.path.join(REPO_ROOT, "keel", "commands", "*console*.py"))))
    return sorted(set(paths))


#: The compute trees -- where sizing/backtest/gate/screening/reporting math lives. The
#: console layer reaches these ONLY through the `keel.commands.*` service layer (or the
#: audited allowances below).
COMPUTE_PREFIXES: tuple[str, ...] = (
    "keel.strategy",
    "keel.execution.guards",
    "keel.execution.sizing",
    "keel.execution.executor",
    "keel.compliance.screen",
    "keel.compliance.purification",
    "keel.analysis",
)


def _is_compute(dotted: str) -> bool:
    return any(
        dotted == prefix or dotted.startswith(prefix + ".") for prefix in COMPUTE_PREFIXES
    )


#: (console module file stem, imported name as resolved against its source module) --
#: function imports from the compute trees that survive the audit. Every entry's CALL
#: sites are pinned by `CALL_ALLOWLIST`; the import allowance exists only so the pinned
#: call sites can name their callee.
IMPORT_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # strategy_console: the recorded-paper-track read the insights service itself
        # performs (insights.py calls the same function the same way) -- dispatched,
        # never re-implemented, and call-pinned. (The verdict view's backtest half rides
        # `keel.commands.rules.resolve_rule_backtest`/`backtest_resolved` now, so the
        # engine's backtest needs no console-side import allowance.)
        ("strategy_console", "keel.strategy.paper.track_record"),
        # tui: the §65.9 report COMPUTE home the CLI's own `keel purification` body calls
        # (one implementation, two front-ends), and the executor's two READ helpers (the
        # rail-17 state read and the live-balance read rail 13 funds) -- reads, never the
        # order path.
        ("tui", "keel.compliance.purification.build_report"),
        ("tui", "keel.execution.executor._withdrawals_enabled"),
        ("tui", "keel.execution.executor._fetch_available_quote"),
    }
)

#: (console module file stem, enclosing function's name, fully resolved callee) -- every
#: call into a compute tree that survives the audit, each with its reason:
CALL_ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset(
    {
        # The ledger's paper-gate distance: constructs the gate's floor VALUE from the
        # config's own keyword fields (no threshold is stated here) and reads the
        # recorded paper track record exactly as `keel.commands.insights` does at its
        # own call -- the console mirrors the service's call because the service's
        # builder takes the record as an input.
        ("strategy_console", "_paper_gate_lines", "keel.strategy.promotion.PromotionConfig"),
        ("strategy_console", "_paper_gate_lines", "keel.strategy.paper.track_record"),
        # The detail view's Enter-gated verdict: its BACKTEST half is fully delegated to
        # the `keel.commands.rules` compute core (`resolve_rule_backtest` +
        # `backtest_resolved` -- the same read/build/backtest `keel rules backtest` runs;
        # the console-side granularity loop and input assembly are gone, so there is no
        # engine-backtest allowance here). What remains is the GATE half's dispatch: the
        # config's floor values and the engine's own `can_promote` judgment -- the same
        # dispatched-never-reimplemented pattern `_paper_gate_lines` above keeps.
        (
            "strategy_console",
            "compute_rule_verdict",
            "keel.strategy.promotion.PromotionConfig",
        ),
        (
            "strategy_console",
            "compute_rule_verdict",
            "keel.strategy.promotion.pbo_gate_from_config",
        ),
        ("strategy_console", "compute_rule_verdict", "keel.strategy.promotion.can_promote"),
        # The retry form names the typed --force gate's from->to pair through the
        # lifecycle vocabulary -- a pure status mapping, no gate math.
        ("strategy_console", "run_retry_form", "keel.strategy.promotion.next_status"),
        # The Shariah screen's purification report and rail-17 line: THE shared compute
        # home / state read the CLI's own command bodies call (one implementation, two
        # front-ends -- the C1 pattern; the console adds nothing to either).
        ("tui", "_do_compliance_payload", "keel.compliance.purification.build_report"),
        ("tui", "_do_compliance_payload", "keel.execution.executor._withdrawals_enabled"),
        # The dashboard's own live-balance line: the EXACT quote read rail 13 funds a
        # buy against, reused verbatim -- display-only, never an order.
        ("tui", "_balance_fn", "keel.execution.executor._fetch_available_quote"),
    }
)

#: (console module file stem, enclosing function, constructor name) -- the audited
#: broker-construction read sites (Rule 4b). Every other construction must ride the
#: `build_broker`/`build_client` lambda seam of a service call.
READ_SITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        # The offline capability display: loads the adapter CLASS and constructs it with
        # no transport to read `capabilities()` -- O7's capability display, not a handle.
        ("console", "venue_session_bound", "load_broker"),
        # The dashboard's pre-console bounded reads: the slow-cadence balance line, the
        # compliance menu's two Enter-gated venue reads, the discover overlay's one
        # product read, and the `f` fetch key's history warm -- all display/data reads,
        # none an order path. The first three construct with the CLI's own bounded
        # timeouts; `_do_fetch` deliberately constructs WITHOUT one, mirroring `keel
        # fetch`'s own unbounded client -- the documented frozen-screen behavior (the
        # screen freezes for as long as honest work takes, exactly like the CLI).
        ("tui", "_balance_fn", "_build_broker"),
        ("tui", "_do_compliance_network", "_build_broker"),
        ("tui", "_do_discover_report", "_build_broker"),
        ("tui", "_do_fetch", "_build_broker"),
    }
)

_BROKER_CONSTRUCTORS = ("_build_broker", "load_broker")
_ARITH_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
)


def _parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def _dotted_call_name(func: ast.expr, aliases: dict[str, str]) -> str | None:
    """Resolve a Call's func to a full dotted name through the import aliases in scope,
    or `None` when it does not resolve to a module-qualified name."""
    parts: list[str] = []
    node: ast.expr = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))


def _collect_aliases(tree: ast.Module) -> dict[str, str]:
    """name -> dotted module( .name) for every keel.*/keel_broker_* import at any scope
    depth: `from keel.strategy import promotion as p` -> p = keel.strategy.promotion."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and isinstance(node.module, str):
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    aliases[alias.asname] = alias.name
    return aliases


def _source_exports_constants_or_types(module_dot: str, name: str) -> bool:
    """Whether `name` is a CONSTANT or TYPE at the compute module's own top level --
    the vocabulary/wording imports the console legitimately makes. Parsed from the
    source, so a renamed constant cannot silently keep an allowance alive."""
    try:
        module = importlib.import_module(module_dot)
    except Exception:
        return False
    tree = _parse(getattr(module, "__file__", ""))
    exported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            exported.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    exported.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                exported.add(node.target.id)
    return name in exported


def _enclosing_functions(tree: ast.Module) -> dict[ast.AST, str]:
    """node -> nearest enclosing function's name (module level maps to '<module>')."""
    owners: dict[ast.AST, str] = {}

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            owners[child] = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            visit(child, owner)

    visit(tree, "<module>")
    return owners


class _Findings(dict[str, list[str]]):
    def add(self, key: str, message: str) -> None:
        self.setdefault(key, []).append(message)


@pytest.fixture(scope="module")
def findings() -> dict[str, list[str]]:
    """One scan over the whole console layer -- the shared fixture every rule-test reads."""
    found = _Findings()
    for path in _console_module_paths():
        stem = os.path.splitext(os.path.basename(path))[0]
        tree = _parse(path)
        aliases = _collect_aliases(tree)
        owners = _enclosing_functions(tree)

        # Rule 1: no imports from the compute trees except constants/types/submodules
        # and the audited function imports.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and isinstance(node.module, str):
                if not _is_compute(node.module):
                    continue
                for alias in node.names:
                    resolved = f"{node.module}.{alias.name}"
                    bound = alias.asname or alias.name
                    try:
                        importlib.import_module(f"{node.module}.{alias.name}")
                    except Exception:
                        is_submodule = False
                    else:
                        is_submodule = True
                    if is_submodule:
                        continue  # a module object; its CALLS are Rule 2's business
                    if _source_exports_constants_or_types(node.module, alias.name):
                        continue
                    if (stem, resolved) in IMPORT_ALLOWLIST:
                        continue
                    found.add(
                        "rule1_imports",
                        f"{stem}: imports compute function {resolved} (bound as {bound!r})",
                    )

        # Rule 2: no calls resolving into the compute trees outside the audited sites.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted_call_name(node.func, aliases)
            if name is not None and _is_compute(name):
                if (stem, owners.get(node, "<module>"), name) not in CALL_ALLOWLIST:
                    found.add(
                        "rule2_calls",
                        f"{stem}:{owners.get(node, '<module>')}: calls {name}",
                    )

        # Rule 3: no arithmetic over a Decimal(...) construction.
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, _ARITH_OPS):
                for side in (node.left, node.right):
                    if any(
                        isinstance(n, ast.Call)
                        and _dotted_call_name(n.func, aliases) in ("Decimal", "decimal.Decimal")
                        for n in ast.walk(side)
                    ):
                        found.add(
                            "rule3_decimal_math",
                            f"{stem}:{owners.get(node, '<module>')}: Decimal arithmetic "
                            f"(line {node.lineno})",
                        )
                        break

        # Rule 4: broker construction only at the seam lambdas or the audited read sites.
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        def _inside_service_lambda(node: ast.AST) -> bool:
            # Walk up: is this call inside a Lambda that is itself the build_broker/
            # build_client KEYWORD argument of a service call (the run_fn seam)?
            current: ast.AST | None = node
            while current is not None:
                parent = parents.get(current)
                if isinstance(parent, ast.Lambda):
                    grand = parents.get(parent)
                    if (
                        isinstance(grand, ast.keyword)
                        and grand.arg in ("build_broker", "build_client")
                    ):
                        return True
                current = parent
            return False

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted_call_name(node.func, aliases)
            short = name.rsplit(".", 1)[-1] if name else None
            if short not in _BROKER_CONSTRUCTORS:
                continue
            if _inside_service_lambda(node):
                continue
            if (stem, owners.get(node, "<module>"), short) in READ_SITES:
                continue
            found.add(
                "rule4_broker_construction",
                f"{stem}:{owners.get(node, '<module>')}: constructs a broker ({short}, "
                f"line {node.lineno}) outside the build_broker/build_client service seam",
            )
    return found


def test_rule_1_no_compute_module_imports(findings: dict[str, list[str]]) -> None:
    """Constants, types and submodules aside, the console layer imports nothing from the
    compute trees: gate wording and screen vocabulary come from their one homes, and
    every FUNCTION those modules export is reached through `keel.commands.*` services
    (or the pinned, justified allowances)."""
    assert not findings.get("rule1_imports"), findings["rule1_imports"]


def test_rule_2_no_compute_module_calls(findings: dict[str, list[str]]) -> None:
    """The PRD §6.2 pin itself: no sizing, screening, gating or reporting math is called
    from the TUI layer outside the audited dispatch sites -- a new `size(...)`,
    `check(...)` or `can_promote(...)` call in a console module fails here."""
    assert not findings.get("rule2_calls"), findings["rule2_calls"]


def test_rule_3_decimal_is_display_only(findings: dict[str, list[str]]) -> None:
    """Money values are read, formatted and handed to services in the console layer --
    never computed with: every arithmetic operator over a Decimal construction fails."""
    assert not findings.get("rule3_decimal_math"), findings["rule3_decimal_math"]


def test_rule_4_no_broker_construction_outside_the_seams(
    findings: dict[str, list[str]],
) -> None:
    """A venue handle appears in the console layer only inside the `build_broker`/
    `build_client` lambda a service call receives (the `run_fn` seam) or at the audited
    bounded read sites -- there is no other construction, so no TUI-originated order
    path (the sole order-capable dispatch is `agent.run_once`, not a compute call)."""
    assert not findings.get("rule4_broker_construction"), findings["rule4_broker_construction"]


def test_the_scan_actually_scanned_the_console_layer() -> None:
    """The pin's own health check: the file set is the shell + the loop + every
    `*console*` module -- a glob that silently matched nothing would make every rule
    vacuously green."""
    paths = _console_module_paths()
    stems = {os.path.splitext(os.path.basename(p))[0] for p in paths}
    assert {"console", "tui"} <= stems
    assert {
        "compliance_console",
        "strategy_console",
        "research_console",
        "trading_console",
        "data_console",
        "help_console",
        "account_console",
    } <= stems


def test_every_allowance_names_a_real_callee() -> None:
    """An allowance for a callee that no longer exists is a hole, not a pin: every
    allowlisted compute callee must still resolve to a real attribute of its module."""
    for _stem, _fn, dotted in sorted(CALL_ALLOWLIST):
        module_dot, _, attr = dotted.rpartition(".")
        module: Any = importlib.import_module(module_dot)
        assert hasattr(module, attr), dotted
    for _stem, dotted in sorted(IMPORT_ALLOWLIST):
        module_dot, _, attr = dotted.rpartition(".")
        module = importlib.import_module(module_dot)
        assert hasattr(module, attr), dotted


def test_the_allowances_are_scoped_not_global() -> None:
    """The teeth of the allowance design: entries are (module, enclosing function,
    callee) triples, so the same callee at a NEW site, or a new callee at an allowed
    site, both fail Rule 2 -- an allowance cannot leak across the layer."""
    for stem, fn, dotted in CALL_ALLOWLIST:
        assert isinstance(stem, str) and isinstance(fn, str) and "." in dotted
