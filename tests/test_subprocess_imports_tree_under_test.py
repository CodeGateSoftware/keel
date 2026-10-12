"""A `python -c` child must import keel from the tree under test (#978 review F2).

`conftest._run_in_tmp_path` moves every test's cwd to `tmp_path`, so a child no longer finds the
repo root through `''` on `sys.path`; it would import whatever the venv's editable install points
at -- the main checkout, in a worktree that borrows its venv -- and the four `-c` tests would
silently check a different tree's code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_a_python_dash_c_child_imports_keel_from_this_repo() -> None:
    out = subprocess.run(
        [sys.executable, "-c", "import keel; print(keel.__file__)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert Path(out).resolve().is_relative_to(_REPO_ROOT)
