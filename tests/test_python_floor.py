"""The Python floor: the lowest version the suite actually runs on, kept honest in CI.

`requires-python` was `>=3.14.4` from the project's first day, which said "the interpreter I
develop on" rather than "the interpreter the code needs" -- and Python 3.14 is new enough that
many contributors do not have it, several distributions do not package it, and some CI images
lag. A floor that bars contributors silently costs them: they try, fail to build, and leave
without opening an issue (#283).

So the floor was measured, not guessed. The full suite was run at each step down:

- **3.13, 3.12, 3.11**: 2,788 passed / 1 skipped -- identical to 3.14, nothing requires it.
- **3.10**: collection fails, `ImportError: cannot import name 'assert_never' from 'typing'` --
  the one concrete binding constraint found. `assert_never` is 3.11+.

The decision: the floor is **3.11**, the lowest passing version; development stays pinned to
3.14.4 via `.python-version`; CI runs the suite on 3.11 so the floor stays true instead of
rotting back into an untested claim. These tests pin all three halves of that decision -- the
declared floor in every distribution, the binding-feature evidence that justifies it, and the
CI leg that verifies it -- because each is a way the decision can silently come undone: a
seventh distribution added with a different floor, the `assert_never` imports disappearing
(which means the floor COULD drop and the recorded reason is stale), or the CI matrix losing
its floor leg.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The floor, as decided in #283. One value in every distribution, like the version pins.
_FLOOR = ">=3.11"


def _pyprojects() -> dict[str, dict]:
    """distribution name -> parsed `pyproject.toml`, for the root and every workspace member."""
    found = {}
    for path in [_ROOT / "pyproject.toml", *sorted((_ROOT / "packages").glob("*/pyproject.toml"))]:
        data = tomllib.loads(path.read_text())
        found[data["project"]["name"]] = data
    return found


def test_every_distribution_declares_the_same_floor():
    """`requires-python = ">=3.11"` everywhere -- a mixed floor is a mixed install waiting.

    The workspace is cut in one build; a member declaring a different floor than its siblings
    means an environment that satisfies one requirement and rejects another, which is the
    version-pin failure mode `test_packaging.py` guards, one field over.
    """
    floors = {name: data["project"]["requires-python"] for name, data in _pyprojects().items()}
    wrong = {name: floor for name, floor in floors.items() if floor != _FLOOR}
    assert not wrong, (
        f"these distributions declare a Python floor other than {_FLOOR!r}: {wrong}. The floor "
        "is one decision for the whole workspace (see tests/test_python_floor.py's docstring "
        "for the measured reasoning)"
    )


def test_the_binding_feature_that_sets_the_floor_still_exists():
    """The floor's REASON must stay true: `assert_never` (3.11+) must still be imported.

    3.10 fails collection on exactly this import. If it ever disappears from the codebase, the
    floor could drop further and this test's recorded justification is stale -- re-measure
    downward rather than leaving a floor pinned to a feature nobody uses.
    """
    importers: list[str] = []
    for base in (_ROOT / "keel", _ROOT / "packages", _ROOT / "tests"):
        for path in base.rglob("*.py"):
            if re.search(r"import\s+.*\bassert_never\b", path.read_text()):
                importers.append(str(path.relative_to(_ROOT)))
    assert importers, (
        "nothing imports `assert_never` any more -- the 3.11 floor's binding constraint is "
        "gone; re-run the suite on 3.10 and lower the floor if it now passes"
    )


def test_ci_runs_the_suite_on_the_floor_so_it_stays_true():
    """The `test` job must have a 3.11 leg, or the floor is an untested claim.

    Measured floors rot: a dependency or a line of code starts needing something newer and
    `>=3.11` becomes a lie that only manifests on a contributor's machine. The CI leg is what
    converts the floor from a declaration into an invariant. It lives in the `test` job's
    matrix on purpose -- a job of its own would report a status context nothing requires,
    which is exactly how #268's mypy gate came undone before it existed.
    """
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "3.11" in ci, (
        "ci.yml no longer runs on Python 3.11 -- the declared floor has lost its CI leg; "
        "restore the matrix entry or re-measure the floor and update it everywhere"
    )


def test_the_floor_is_stated_where_a_contributor_reads_it():
    """README and CONTRIBUTING must say 3.11, so the floor is found in docs, not the resolver.

    'A contributor hitting the floor gets a clear message rather than a confusing resolver
    error' (#283) starts with the floor being low enough to be rare and visible enough to be
    self-diagnosing when hit.
    """
    readme = (_ROOT / "README.md").read_text()
    contributing = (_ROOT / "CONTRIBUTING.md").read_text()
    assert "3.11" in readme, "the README's setup section must state the 3.11 floor"
    assert "3.11" in contributing, "CONTRIBUTING.md must state the 3.11 floor"
