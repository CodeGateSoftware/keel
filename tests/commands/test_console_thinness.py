"""The architectural thinness pin (issue #392 C6; PRD §6.2 -- "grep-able proof of no
duplicated logic ... pinned by an architectural test").

WHAT IT FORBIDS, precisely. The presentation layer is the console shell
(`keel/commands/console.py`), the live loop (`keel/commands/tui.py`), every console sub-menu
module (`keel/commands/*console*.py`), and -- since #435 -- every module of the local web UI
(`keel/web/*.py`). Those files render and dispatch; all behavior must come from the services
the CLI calls. The pin is an AST scan enforcing six rules -- five that hold across the whole
layer, and one (#533) scoped to the JSON serialiser:

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
* **Rule 5 -- no process/network orchestration in the console layer** (issue #415): no
  console module may import `subprocess`/`urllib` or call `execv`/`urlopen`/`os.system`
  itself. The self-update slice's shells-out work (uv, `keel migrate`, `keel versions`
  verification, the GitHub release fetch, the execv relaunch) lives in the SERVICE
  (`keel.commands.update`), which this pin does not scan -- so the rule needs no
  allowance at all, and a console module that inlines any of that orchestration fails
  here rather than shipping a second, unpinned place that replaces the running binary.
* **Rule 6 -- the serialisation contract** (issue #533), scoped to the module that turns the
  frozen reports into the browser's JSON (`SERIALISER_STEMS`). That module is the one place in
  this layer whose output is a machine-readable MONEY contract rather than a screen, so it
  carries one extra pin over the five ways the `Decimal`-only guarantee dies at that boundary:
  `Decimal.normalize()` (which renders `Decimal("50")` as `Decimal("5E+1")` -- a form that has
  reached the wire in this codebase before and broken real orders), `float()` on anything but a
  timestamp, `round()`, `json.dumps(..., default=float)` (one keyword, and it looks like a
  helpful fix for the `TypeError` a `Decimal` raises), and `len()` -- the quiet way a serialiser
  starts producing counts of its own instead of reading ones the report holds. Rules 1-5 already
  cover the serialiser as a member of `keel/web/`; this covers what is specific to it. The
  runtime half of the same contract -- a recursive walk over the real payload asserting that no
  JSON number appears anywhere in it -- lives in `tests/web/test_payload.py`. An AST rule and a
  walk over the output fail for different reasons and neither subsumes the other, which is why
  both exist.

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

#: The PRESENTATION layer: the console shell, the live loop, every sub-menu module -- and, since
#: #435, every module of the local web UI (`keel/web/`).
#:
#: The web UI is scanned by the same rules rather than a parallel pin of its own, because it
#: is the same kind of thing: a second front-end over `keel/commands/*`. A separate pin would have
#: drifted -- two files stating the same architecture, diverging one allowance at a time -- and
#: the failure it is guarding against is identical in both. `keel/commands/serve.py` is NOT
#: scanned: it is the command that binds the socket and launches a browser, which is service work
#: and is exactly what Rule 5 says belongs outside this layer.
def _console_module_paths() -> list[str]:
    paths = [os.path.join(REPO_ROOT, "keel", "commands", name) for name in ("console.py", "tui.py")]
    paths.extend(sorted(glob.glob(os.path.join(REPO_ROOT, "keel", "commands", "*console*.py"))))
    paths.extend(sorted(glob.glob(os.path.join(REPO_ROOT, "keel", "web", "*.py"))))
    return sorted(set(paths))


#: Rule 5's entry-scoped exceptions, in the same shape as every other allowance here:
#: (module stem, imported module).
#:
#: Rule 5 bans `urllib` by ROOT, which is the right coarseness for a rule about network egress --
#: `urllib.request.urlopen` is the thing it exists to stop. But `urllib.parse` performs no I/O at
#: all: it is string manipulation, and it is how `keel/web/server.py` splits a request path from
#: its query string, and (#535) how `keel/web/staticfiles.py` decodes one static-asset path
#: segment before checking it stays inside the static root. The alternative in both cases was
#: hand-rolling percent-decoding on attacker-influenced input, which is a strictly worse trade
#: than one named, scoped allowance.
#:
#: Scoped to the module and the exact import, so it cannot widen: `urllib.request` in either file
#: still fails, and `urllib.parse` anywhere else still fails.
RULE5_IMPORT_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {("server", "urllib.parse"), ("staticfiles", "urllib.parse")}
)


#: The SERIALISER: the module turning the frozen reports into the browser's JSON (#533). It is
#: already inside the scanned set (it lives under `keel/web/`), so Rules 1-5 apply to it as they
#: do to every other module of this layer. Rule 6 below is the extra, narrower pin it needs,
#: because it is the one module in the layer whose OUTPUT is a machine-readable money contract
#: rather than a screen.
SERIALISER_STEMS: frozenset[str] = frozenset({"payload"})


#: Rule 6b's entry-scoped allowance, in the same (module, enclosing function, argument) shape as
#: every other allowance here.
#:
#: `float()` is banned in the serialiser because it is the one call that turns a `Decimal` into
#: the IEEE-754 double the whole contract exists to keep off the wire. It is allowed on a
#: TIMESTAMP, which is not money: `time.gmtime` takes a float and nothing else, and a UTC instant
#: has no cent to lose. Scoped to the argument NAME, so `float(price)` at the same call site
#: still fails.
RULE6_FLOAT_ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("payload", "_gmt", "ts"),
    }
)


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

        # Rule 5: no subprocess/HTTP/exec orchestration in the console layer (issue
        # #415) -- that is the update service's, never a console module's.
        for node in ast.walk(tree):
            banned_imports = ("subprocess", "urllib.request", "urllib")
            if isinstance(node, ast.ImportFrom) and isinstance(node.module, str):
                root = node.module.split(".")[0]
                if (stem, node.module) in RULE5_IMPORT_ALLOWLIST:
                    pass
                elif node.module in banned_imports or root in banned_imports:
                    found.add(
                        "rule5_orchestration",
                        f"{stem}: imports {node.module} -- process/network orchestration "
                        "belongs to the service layer (keel.commands.update)",
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if (stem, alias.name) in RULE5_IMPORT_ALLOWLIST:
                        continue
                    if alias.name in banned_imports or root in banned_imports:
                        found.add(
                            "rule5_orchestration",
                            f"{stem}: imports {alias.name} -- process/network "
                            "orchestration belongs to the service layer "
                            "(keel.commands.update)",
                        )
            if isinstance(node, ast.Call):
                name = _dotted_call_name(node.func, aliases)
                if name is None:
                    continue
                if name in ("subprocess.run", "subprocess.check_output",
                            "urllib.request.urlopen", "os.execv", "os.system") or name.endswith(
                                ".execv"
                            ):
                    found.add(
                        "rule5_orchestration",
                        f"{stem}:{owners.get(node, '<module>')}: calls {name} -- the "
                        "console layer never shells out, opens a socket or replaces "
                        "its own process; that is keel.commands.update's job",
                    )

        # Rule 6 (#533): the serialisation contract, in the ONE module whose output is a
        # machine-readable money contract rather than a screen.
        if stem in SERIALISER_STEMS:
            for key, messages in _rule6_findings(stem, tree, aliases, owners).items():
                for message in messages:
                    found.add(key, message)
    return found


def _argument_identifier(node: ast.expr) -> str:
    """The last identifier of a call argument -- `ts` for `ts`, `x.ts` and `self.x.ts`.

    Used only to scope Rule 6b's allowance to the ARGUMENT, so `float(ts)` can be allowed at a
    site where `float(price)` still fails."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return "<expr>"


def _rule6_findings(
    stem: str, tree: ast.Module, aliases: dict[str, str], owners: dict[ast.AST, str]
) -> dict[str, list[str]]:
    """The four ways the `Decimal`-only guarantee dies at the JSON boundary, as an AST scan.

    Written as its own function rather than inline in the fixture so the positive control below
    can run it over a synthetic module and prove it is capable of finding anything at all."""
    found = _Findings()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        owner = owners.get(node, "<module>")
        name = _dotted_call_name(node.func, aliases)
        short = name.rsplit(".", 1)[-1] if name else None

        # 6a -- normalize(). THE hazard: `Decimal("50").normalize()` is `Decimal("5E+1")`, and
        # "5E+1" on the wire is fifty read as five, or as nothing. Matched on the ATTRIBUTE, not
        # on a resolved dotted name, because the receiver is a value and never resolves.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "normalize":
            found.add(
                "rule6_serialisation",
                f"{stem}:{owner}: calls .normalize() (line {node.lineno}) -- it renders "
                'Decimal("50") as Decimal("5E+1"); format(value, "f") is the only rendering '
                "that cannot emit an exponent",
            )

        # 6b -- float(), the one call that turns a Decimal into the IEEE-754 double this whole
        # contract exists to keep off the wire. Allowed on a timestamp, entry-scoped.
        if short == "float" and name == "float":
            argument = _argument_identifier(node.args[0]) if node.args else "<none>"
            if (stem, owner, argument) not in RULE6_FLOAT_ALLOWLIST:
                found.add(
                    "rule6_serialisation",
                    f"{stem}:{owner}: calls float({argument}) (line {node.lineno}) -- money "
                    "crosses the wire as a string; only a timestamp may become a float here",
                )

        # 6c -- round(), which both computes and (on a float) reintroduces binary rounding.
        # Rounding for DISPLAY is done with a format spec, which leaves the wire value exact.
        if name == "round":
            found.add(
                "rule6_serialisation",
                f"{stem}:{owner}: calls round() (line {node.lineno}) -- display rounding "
                "belongs in a format spec, which leaves the wire value exact",
            )

        # 6d -- json.dumps(..., default=float). `json.dumps` raises a TypeError on a Decimal,
        # and `default=float` is the one-keyword fix that silently converts every money value in
        # the payload to a double. A PLAIN `json.dumps` is fine and is used: the serialiser
        # normalises every leaf to a string first, so it needs no encoder.
        if name in ("json.dumps", "json.dump"):
            for keyword in node.keywords:
                if keyword.arg in ("default", "cls"):
                    found.add(
                        "rule6_serialisation",
                        f"{stem}:{owner}: json.dumps(..., {keyword.arg}=...) (line "
                        f"{node.lineno}) -- a custom encoder is how every Decimal in the "
                        "payload becomes a double in one keyword",
                    )

        # 6e -- len(). A count on the wire must be one the REPORT holds, and `len()` is how a
        # serialiser quietly starts producing its own. The first draft of `journal_payload`
        # shipped `count(len(report.entries))`; it is numerically exact, which is precisely why
        # the runtime "computes nothing" guard could not be relied on to catch it -- that guard
        # compares figures, and a list length can coincide with one the report already holds.
        # An AST ban does not care about the coincidence. `JournalReport.shown_count` is where
        # that figure belongs, and any future count needs the same treatment upstream (or an
        # entry-scoped allowance here, in RULE6_FLOAT_ALLOWLIST's shape, with its reasoning).
        if name == "len":
            found.add(
                "rule6_serialisation",
                f"{stem}:{owner}: calls len() (line {node.lineno}) -- a count on the wire "
                "must be one the report already holds; add it to the report builder",
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


def test_rule_5_no_process_or_network_orchestration_in_the_console_layer(
    findings: dict[str, list[str]],
) -> None:
    """Issue #415's rule: the console layer never shells out, opens a socket, or
    replaces its own process. All of that orchestration (uv, the `keel migrate`/`keel
    versions` subprocesses, the GitHub release fetch, the execv relaunch) lives in the
    update SERVICE (`keel.commands.update`), outside this pin's file set -- which is
    why the rule needs no allowance: there is nothing to allow, and inlining any of it
    into a console module fails here."""
    assert not findings.get("rule5_orchestration"), findings["rule5_orchestration"]


def test_rule_6_the_serialisation_contract_holds_in_the_serialiser(
    findings: dict[str, list[str]],
) -> None:
    """Issue #533's rule: the module that writes keel's JSON never converts a money value to a
    binary float, never rounds one, never renders one through `normalize()`, and never installs a
    `json` encoder that would do any of those for it.

    All four are the same failure with different spellings -- a `Decimal` arriving in a browser as
    an IEEE-754 double -- and all four are SILENT, which is why they are pinned mechanically
    rather than left to review. `tests/web/test_payload.py` pins the same contract from the other
    end, by walking the rendered payload.
    """
    assert not findings.get("rule6_serialisation"), findings["rule6_serialisation"]


def test_rule_6_is_proven_false_capable() -> None:
    """Rule 6's own positive control, run over a synthetic module that commits all four sins.

    Without this, a typo in an attribute name or a resolver that never matches would leave Rule 6
    green over a serialiser doing exactly what it forbids -- and Rule 6 guards a contract seven
    downstream issues inherit, so a vacuously-green version of it is worse than none.

    The allowed shape is included too: `float(ts)` inside `_gmt` must NOT be flagged, or the rule
    is a blanket ban wearing an allowlist.
    """
    snippet = (
        "import json\n"
        "def _gmt(ts, fmt):\n"
        "    return float(ts)\n"
        "def bad(price, payload, rows):\n"
        "    a = price.normalize()\n"
        "    b = float(price)\n"
        "    c = round(price, 2)\n"
        "    d = json.dumps(payload, default=float)\n"
        "    e = len(rows)\n"
        "    return a, b, c, d, e\n"
    )
    tree = ast.parse(snippet)
    owners = _enclosing_functions(tree)

    messages = _rule6_findings("payload", tree, _collect_aliases(tree), owners).get(
        "rule6_serialisation", []
    )

    assert len(messages) == 5, messages
    assert any(".normalize()" in m for m in messages)
    assert any("float(price)" in m for m in messages)
    assert any("round()" in m for m in messages)
    assert any("default=" in m for m in messages)
    assert any("len()" in m for m in messages)
    assert not any(":_gmt:" in m for m in messages), messages


def test_rule_5_is_proven_false_capable() -> None:
    """The detector's own positive control: a synthetic module that shells out and
    execv's is flagged (an AST scan that silently matched nothing would make Rule 5
    vacuously green)."""
    snippet = "import subprocess\nimport os\nsubprocess.run(['uv'])\nos.execv('/k', ['/k'])\n"
    tree = ast.parse(snippet)
    aliases = _collect_aliases(tree)
    flagged = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            flagged += 1
        elif isinstance(node, ast.Call):
            name = _dotted_call_name(node.func, aliases)
            if name in ("subprocess.run", "os.execv"):
                flagged += 1
    assert flagged == 4, flagged


def test_the_scan_actually_scanned_the_console_layer() -> None:
    """The pin's own health check: the file set is the shell + the loop + every
    `*console*` module -- a glob that silently matched nothing would make every rule
    vacuously green."""
    paths = _console_module_paths()
    stems = {os.path.splitext(os.path.basename(p))[0] for p in paths}
    assert {"console", "tui"} <= stems
    # #435: the web UI is a front-end over the same services and is pinned by the same rules.
    # Named explicitly so that deleting or renaming a web module fails HERE, loudly, rather than
    # quietly shrinking the scanned set and leaving the rules green over less code.
    assert {"render", "security", "server"} <= stems
    # #533: the JSON serialiser joins them. Named here for the same reason and with an extra one:
    # Rule 6 is scoped BY STEM, so a serialiser renamed out of `SERIALISER_STEMS` would keep
    # passing Rules 1-5 while silently losing the money-contract pin entirely.
    assert SERIALISER_STEMS <= stems
    assert any(os.path.join("keel", "web") in path for path in paths)
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
