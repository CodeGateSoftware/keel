"""The README's architecture block, against the tree it describes.

It drifted four ways at once before anyone noticed, and every one of them was checkable:

  - `keel-broker-alpaca` was absent, though it is part of the production install set
    (`docs/RELEASING.md`: a deployment installs five wheels, and that is one of them)
  - `keel-broker-kraken` was absent
  - "~3,000 tests" while the suite had passed 6,000
  - "~3,000 tests" while the suite had passed 6,000

The rail count is NOT checked here. `tests/execution/test_rail_count.py` owns it, derives it
from `guards.check`, and searches every English and numeric spelling repo-wide. This file's
first cut added a second check that took the HIGHEST rail number instead of the count -- and
they differ, because rail 15 was retired: numbered to 22, twenty-one of them. It "corrected" a
README that was right. Two sources of truth for one number is how the number goes wrong.

A reader checks an architecture diagram precisely when they do not yet know the codebase, so
it is read most carefully exactly when it can be believed least. These assertions are the two
halves of it that a machine can hold: which packages exist, and how many rails there are. The
prose beside each entry is not pinned -- describing a package is a judgement, counting them is
not.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_README = (REPO_ROOT / "README.md").read_text()


def _architecture_block() -> str:
    """The fenced block under `## Architecture`, verbatim."""
    start = _README.index("## Architecture")
    fence = _README.index("```", start)
    return _README[fence : _README.index("```", fence + 3)]


def test_every_shipped_package_is_in_the_diagram() -> None:
    """A package absent from the diagram is a venue a reader does not know exists. `alpaca` was
    the live example: shipped, in the release set, and unmentioned."""
    shipped = {
        p.name for p in (REPO_ROOT / "packages").iterdir() if (p / "pyproject.toml").is_file()
    }
    block = _architecture_block()
    missing = sorted(name for name in shipped if name not in block)
    assert not missing, f"packages/ ships these and the README does not name them: {missing}"


def test_the_diagram_names_no_package_that_does_not_exist() -> None:
    """The other direction, which a removal would break rather than an addition."""
    shipped = {p.name for p in (REPO_ROOT / "packages").iterdir() if p.is_dir()}
    named = set(re.findall(r"\bkeel-broker-[a-z]+\b|\bkeel-core\b", _architecture_block()))
    invented = sorted(named - shipped)
    assert not invented, f"the README names packages that are not in packages/: {invented}"


def test_no_hardcoded_test_count_that_cannot_stay_true() -> None:
    """ "~3,000 tests" was wrong by more than double. A precise count in prose is a promise the
    suite breaks every time it grows, so the block states no number at all -- the honest options
    were an untested number or no number, and no number cannot be wrong."""
    block = _architecture_block()
    assert not re.search(r"[\d,]+\s*tests", block), (
        "the architecture block hardcodes a test count; it will be wrong by the next PR"
    )
