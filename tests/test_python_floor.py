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
ninth distribution added with a different floor, the `assert_never` imports disappearing
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


def test_mypy_checks_the_code_at_the_declared_floor():
    """`[tool.mypy] python_version` must equal `requires-python`, so the floor and the
    checker cannot drift.

    mypy's `python_version` is the interpreter it SIMULATES while checking. Left higher than
    the floor, it silently blesses syntax an interpreter the metadata invites would refuse at
    import -- the same failure shape as `ruff format`'s `target-version` mismatch
    (`tests/test_packaging.py`), one field over. Left lower, it flags 3.11-valid code the
    shipped packages actually use, and someone "fixes" the code instead of the config.

    Only the root `pyproject.toml` carries a `[tool.mypy]` section (every workspace member is
    checked by that one config), so one comparison -- the checker's version against the floor
    every distribution declares -- pins the whole workspace.
    """
    mypy = tomllib.loads((_ROOT / "pyproject.toml").read_text())["tool"]["mypy"]
    checker = mypy["python_version"]
    for name, data in _pyprojects().items():
        requires = data["project"]["requires-python"]
        assert requires.startswith(">="), (name, requires)
        floor = requires[2:].strip()
        assert checker == floor, (
            f"{name} declares requires-python {requires!r} but mypy checks at "
            f"python_version {checker!r} -- the floor and the checker have drifted; update "
            "[tool.mypy] python_version in pyproject.toml to match requires-python"
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


def test_mypy_checks_against_the_floor_it_promises():
    """`[tool.mypy].python_version` must equal the floor `requires-python` promises.

    The floor lives in two places that can drift apart: `requires-python = \">=3.11\"` in
    every distribution and `[tool.mypy] python_version = \"3.11\"` in the root. Nothing fails
    a build where they disagree -- mypy would happily check against 3.14 semantics while the
    distributions promise 3.11, or vice versa, and the first victim is a contributor on the
    floor version, who gets the checker's errors instead of the suite's. mypy must check
    what the floor promises, so the two are pinned to each other here, where a drift is a
    test failure instead of a silent mismatch (#316).
    """
    root = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    requires = root["project"]["requires-python"]
    match = re.search(r"\s*(\d+)\.(\d+)", requires)
    assert match, f"could not derive a Python minor version from requires-python {requires!r}"
    floor_minor = f"{match.group(1)}.{match.group(2)}"
    mypy_version = root["tool"]["mypy"]["python_version"]
    assert mypy_version == floor_minor, (
        f"mypy checks against python_version {mypy_version!r}, but the floor is {requires!r} "
        f"(minor {floor_minor!r}); mypy must check what the floor promises -- set "
        "[tool.mypy].python_version in the root pyproject.toml to match requires-python"
    )
