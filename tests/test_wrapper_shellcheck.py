"""shellcheck over the four tracked deployment wrappers (#640/#642).

`.github/workflows/install-smoke.yml` already shellchecks `scripts/install.sh` in CI, on the
Linux leg (`shellcheck` ships on Ubuntu runners, not on macOS ones). This file gives the four
launchd runners -- `keel-live-run.sh`, `paperforward-run.sh`, `paper-hourly-run.sh`,
`paper-equities-run.sh` -- the same coverage as a local, offline pytest check rather than only
a CI-side one, since `tests/test_schedule.py` and the sibling profile tests exercise these
scripts' BEHAVIOUR but never their shellcheck cleanliness. Skips cleanly rather than failing
when `shellcheck` is not installed (it is not a project dependency, and CI's Linux leg already
covers `scripts/install.sh` unconditionally), so this test only ever adds signal, never a
false-red on a machine that lacks the tool.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

WRAPPERS = [
    REPO_ROOT / "keel-live-run.sh",
    REPO_ROOT / "paperforward-run.sh",
    REPO_ROOT / "paper-hourly-run.sh",
    REPO_ROOT / "paper-equities-run.sh",
]

_SHELLCHECK = shutil.which("shellcheck")


@pytest.mark.skipif(_SHELLCHECK is None, reason="shellcheck is not installed on this machine")
@pytest.mark.parametrize("script", WRAPPERS, ids=lambda p: p.name)
def test_wrapper_is_shellcheck_clean(script: Path) -> None:
    assert script.exists(), f"{script} is missing"
    result = subprocess.run(
        [_SHELLCHECK, str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"shellcheck found issues in {script.name}:\n{result.stdout}{result.stderr}"
    )
