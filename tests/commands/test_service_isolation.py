"""Architecture pins for the service extraction (issue #387 C1): the shared service layer must be
reachable WITHOUT the CLI composition root.

PRD O2: "the TUI renders and dispatches. All behavior comes from the same services the CLI
calls." That had a structural precondition the UI code could not be trusted to keep by
convention: before the extraction, `keel/commands/tui.py` had to lazy-import
`from keel.cli import _screen_product` inside two functions (an import-cycle dodge), which
meant the "shared" gate physically lived in the front-end.

**The TUI is gone (#541) and these pins are not.** The front-end that replaced it -- `keel/web/`,
and `keel serve` over it -- has exactly the same relationship to the service layer, and the
failure mode is identical: a service that cannot be imported without the CLI is a service the
next front-end will have to reach through the CLI. Two pins keep that shape from coming back:

1. `test_services_import_without_the_cli` -- in a fresh interpreter, importing every audited
   service module must leave `keel.cli` out of `sys.modules`. A fresh interpreter is required
   because THIS process has `keel.cli` loaded; the check is about the import graph, not any one
   process's history.
2. `test_no_commands_module_imports_the_cli` -- a source-level scan (AST, module-level
   statements only, so a docstring mentioning `keel.cli` stays legal) asserting no
   `keel/commands/*` module imports the composition root at load time. Function-level lazy
   imports would evade `sys.modules`-before-first-call tricks but not this scan.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

COMMANDS_DIR = Path(__file__).resolve().parents[2] / "keel" / "commands"

#: Every `keel/commands/*` module the C1 audit made part of the shared service layer, plus the
#: pre-existing groups. A future service module added without being listed here still gets the
#: source-scan pin below; this list exists so the IMPORT pin fails loudly if a module is
#: renamed or goes missing rather than silently not being checked.
SERVICE_MODULES = [
    "keel.commands._common",
    "keel.commands._products",
    "keel.commands.activity",
    "keel.commands.admission",
    "keel.commands.assets",
    "keel.commands.autonomy",
    "keel.commands.brokers",
    "keel.commands.confirm",
    "keel.commands.db",
    "keel.commands.fetch",
    "keel.commands.insights",
    "keel.commands.monitor",
    "keel.commands.pnl",
    "keel.commands.purification",
    "keel.commands.research",
    "keel.commands.rules",
    "keel.commands.simulate",
    "keel.commands.status",
    "keel.commands.subscription",
    "keel.commands.trading",
    "keel.commands.trials",
    "keel.commands.versions",
    "keel.commands.withdrawals",
    # `keel.corpus` is not under `keel/commands/`, and is here because it is what #541 kept
    # when the console layer was deleted: the MCP server reads the research corpora through it,
    # and an MCP server that could only start after the CLI had loaded would be the exact
    # coupling this test exists to prevent.
    "keel.corpus",
]


def test_services_import_without_the_cli() -> None:
    """A fresh interpreter can load every service module AND the TUI without `keel.cli`.

    This is the load-time precondition for PRD O2's "the TUI dispatches to the services, never
    to the CLI": if any service (or the TUI itself) pulled in the composition root, the TUI
    would carry click's whole command surface -- and the old `from keel.cli import
    _screen_product` lazy imports would be back as an import cycle waiting to happen.
    """
    program = (
        "import sys\n"
        + "".join(f"import {name}\n" for name in SERVICE_MODULES)
        + "loaded = sorted(m for m in sys.modules if m == 'keel.cli' or m.startswith('keel.cli.'))"
        "\n"
        + "assert not loaded, f'keel.cli was imported by the service layer: {loaded}'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, (
        f"service layer failed to import standalone:\n{completed.stdout}\n{completed.stderr}"
    )


def test_no_commands_module_imports_the_cli() -> None:
    """No `keel/commands/*` module may import `keel.cli` at MODULE level -- the composition root
    imports THEM (`keel/cli.py` is where `cli.add_command(...)` happens), so any such import is
    a cycle dressed as a shortcut, and every one of them has historically been the seam where
    a front-end copy of a service started growing."""
    offenders: list[str] = []
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):  # the WHOLE tree: a function-level `from keel.cli import`
            # is the same cycle, one frame later -- C1 removed the TUI's last legitimate one
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "keel.cli" or name.startswith("keel.cli.") for name in names):
                offenders.append(f"{path.name}: imports {', '.join(names)}")
    assert not offenders, f"keel/commands modules importing the CLI composition root: {offenders}"


def test_the_scan_catches_a_function_level_lazy_import() -> None:
    """The regression this file exists for: a lazy `from keel.cli import ...` inside a function
    body. `ast.walk` (not `tree.body`) is what makes it uncatchable-proof -- this test feeds
    the scan's own logic the dodge and asserts it is reported."""
    dodging_source = (
        "def _dodge() -> None:\n"
        "    from keel.cli import _screen_product  # the old TUI seam\n"
        "\n"
        "def innocent() -> None:\n"
        "    from keel.commands.assets import screen_product\n"
        "    return screen_product\n"
    )
    tree = ast.parse(dodging_source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        if any(name == "keel.cli" or name.startswith("keel.cli.") for name in names):
            offenders.append(names[0])
    assert offenders == ["keel.cli"], "the function-level dodge must be caught"
