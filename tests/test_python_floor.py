"""The Python floor: one supported interpreter, declared identically everywhere.

The floor is **3.14**, and it is a POLICY floor rather than a measured one. That distinction is
the whole point of this docstring, because the previous floor was the other kind and the two
must not be confused when someone revisits this.

**What was measured (#283).** `requires-python` was `>=3.14.4` on day one -- "the interpreter I
develop on" rather than "the interpreter the code needs". So it was measured downward, running
the full suite at each step:

- **3.13, 3.12, 3.11**: 2,788 passed / 1 skipped, identical to 3.14. Nothing required 3.14.
- **3.10**: collection fails, `ImportError: cannot import name 'assert_never' from 'typing'`.

`assert_never` is 3.11+, and that was the one concrete feature binding the floor. #283 set it to
3.11 -- the lowest passing version -- reasoning that "a floor that bars contributors silently
costs them: they try, fail to build, and leave without opening an issue."

**Why it went back up.** The measurement still stands: nothing in this code needs 3.14, and 3.11
would still pass. What changed is the argument around it, on two counts.

- **Users stopped supplying the interpreter.** #283 was decided before the desktop bundle (D5)
  and `scripts/install.sh`, which bootstrap their own environment. An end user's system Python
  is no longer what keel runs on, so the floor now reaches contributors and packagers only --
  a far smaller group than "everyone who installs keel", and one that can install an interpreter.
- **3.14 stopped being new.** It was days old when #283 was decided. Distributions package it now.

And it buys two concrete things, both of which were blocked and are now unblocked:

- `ruff.toml` can target `py314`. It could not before: `ruff format` rewrites `except (A, B):`
  into PEP 758's unparenthesised form, which is a SyntaxError on 3.11-3.13 (see
  `tests/test_packaging.py::test_ruffs_target_version_does_not_exceed_the_python_floor`).
- The `numpy.*` mypy override is gone. numpy's bundled stubs use PEP 695 `type` statements,
  which a `python_version = "3.11"` run cannot parse; the workspace silenced the transitive
  crawl rather than raise the floor. At 3.14 mypy parses them and the override is unnecessary.

**What would justify lowering it again**: a contributor actually blocked, or a distribution
target that cannot supply 3.14. Not a feature -- there is no feature. Re-measure downward the
way #283 did, and re-check both bullets above, because both would have to be given back.

These tests pin every place the floor is stated, and each derives from `requires-python` rather
than repeating a literal, so raising or lowering it again is one edit plus this docstring. The
one place that deliberately does NOT track this metadata is `scripts/install.sh` (#557): the
installer enforces the floor of the RELEASE it installs -- read from that release's own
`requires-python`, because the dev tree's floor can be (and was) higher than the wheels a
release ships. `tests/test_install_script.py` pins that derivation instead.

There is no successor to the old `assert_never` test, and that absence is the point: a policy
floor has no binding feature to keep true. Asserting one would state a reason that is not the
reason.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

#: The floor. One value in every distribution, like the version pins.
_FLOOR = ">=3.14"


def _pyprojects() -> dict[str, dict]:
    """distribution name -> parsed `pyproject.toml`, for the root and every workspace member."""
    found = {}
    for path in [_ROOT / "pyproject.toml", *sorted((_ROOT / "packages").glob("*/pyproject.toml"))]:
        data = tomllib.loads(path.read_text())
        found[data["project"]["name"]] = data
    return found


def test_every_distribution_declares_the_same_floor():
    """`requires-python = ">=3.14"` everywhere -- a mixed floor is a mixed install waiting.

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
    (`tests/test_packaging.py`), one field over. Left lower, it flags code valid on the floor
    that the shipped packages actually use, and someone "fixes" the code instead of the config.

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


def test_ci_runs_the_suite_on_the_floor_so_it_stays_true():
    """The `test` job must run on the floor, or the floor is an untested claim.

    Measured floors rot: a dependency or a line of code starts needing something newer and
    the declared floor becomes a lie that only shows on a contributor's machine. The leg is what
    converts the floor from a declaration into an invariant. It lives in the `test` job's
    matrix on purpose -- a job of its own would report a status context nothing requires,
    which is exactly how #268's mypy gate came undone before it existed.
    """
    floor = _FLOOR[2:].strip()
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert f'"{floor}"' in ci, (
        f"ci.yml no longer runs on Python {floor} -- the declared floor has lost its CI leg; "
        "restore the matrix entry or re-measure the floor and update it everywhere"
    )


def test_the_floor_is_stated_where_a_contributor_reads_it():
    """The docs must state the floor, so it is found by reading rather than by a resolver error.

    'A contributor hitting the floor gets a clear message rather than a confusing resolver
    error' (#283) starts with the floor being low enough to be rare and visible enough to be
    self-diagnosing when hit.
    """
    floor = _FLOOR[2:].strip()

    # The English docs must use the `3.14+` form, not a bare `3.14`. A bare match is what let
    # this test pass while both files still said "any Python 3.11+ (the repo develops on 3.14)"
    # -- the development pin satisfied the assertion and the stated floor stayed wrong.
    for name in ("README.md", "CONTRIBUTING.md"):
        text = (_ROOT / name).read_text()
        assert f"{floor}+" in text, (
            f"{name} must state the floor as {floor}+ -- a bare {floor!r} also matches a "
            "sentence naming the development pin beside a stale floor, which is how this "
            "assertion once passed against docs that were wrong"
        )

    # The Arabic mirror carries "and above" in words rather than a `+`, which is better Arabic
    # than transliterating the English token; it is checked for the version alone.
    arabic = (_ROOT / "README.ar.md").read_text()
    assert floor in arabic, f"README.ar.md must state the {floor} floor"

    # Deliberately NOT scanning for the superseded floors. CONTRIBUTING.md cites 3.11 on purpose
    # -- the measured history behind #283 and why it was reversed -- and this project requires
    # rejected decisions to stay recorded. A scan that forbade the old number would punish
    # exactly the documentation discipline the contribution guide asks for.


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


def _latest_release_floor() -> str | None:
    """The floor of the wheels most recently published as a GitHub Release, read from
    THAT TAG's own `pyproject.toml` -- not `main`'s. This is the floor README.md,
    docs/desktop-install.md, packaging/macos_app.sh and scripts/install.sh's
    `FALLBACK_FLOOR` all actually describe (#557): the shipped artifact, which can
    legitimately trail `main` when `requires-python` has moved but no release carrying
    the move has shipped yet.

    Reads local tags only -- no network call, matching how the rest of this test suite
    stays hermetic. `ci.yml`'s checkout fetches tags (`fetch-depth: 0`, the same reason
    `release.yml`'s checkout already does) so this resolves in CI; a shallow local clone
    without tags returns `None`, and callers must skip rather than fail, since an absent
    tag says nothing about whether the docs are correct.
    """
    tags = subprocess.run(
        ["git", "tag", "--list", "v*.*.*", "--sort=-creatordate"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    names = [line for line in tags.stdout.splitlines() if line.strip()]
    if tags.returncode != 0 or not names:
        return None
    latest = names[0]

    show = subprocess.run(
        ["git", "show", f"{latest}:pyproject.toml"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if show.returncode != 0:
        return None
    try:
        data = tomllib.loads(show.stdout)
        requires = data["project"]["requires-python"]
    except (tomllib.TOMLDecodeError, KeyError):
        return None
    match = re.match(r">=\s*(\d+)\.(\d+)", requires)
    return f"{match.group(1)}.{match.group(2)}" if match else None


def test_the_wheels_floor_stated_in_release_facing_docs_matches_requires_python():
    """The floor a READER sees before running the wheels one-liner must be the floor pip
    will actually enforce (#595) -- checked against the RIGHT reference, so the pin
    cannot hand back the bug it exists to prevent.

    Two different floors, two different jobs, and this test must not blur them:

    - `pyproject.toml`'s `requires-python` on `main` -- the DEV TREE's floor. The ONLY
      doc that must equal it is `.github/workflows/release.yml`'s Desktop template,
      because that template generates the text of the NEXT release, which is built from
      whatever `main` says at tag time. Equality there is not a coincidence; it is what
      "generated from this commit" means.
    - The floor of the most recently PUBLISHED release (`_latest_release_floor`,
      above) -- what README.md, docs/desktop-install.md, packaging/macos_app.sh and
      `FALLBACK_FLOOR` all describe: Pythons the WHEELS SITTING ON THE RELEASES PAGE
      right now actually require. `scripts/install.sh` says why in its own comment
      (#557): "never the development tree -- enforcing the dev tree's floor here is
      exactly bug #557." An earlier version of this test compared all four to `main`'s
      floor instead. That is true today only because the release carrying #546's raised
      floor has already shipped (v0.12.2, verified) -- and false the next time `main`
      moves the floor ahead of a release, at which point that version would have failed
      with a message reading as an instruction to bump `FALLBACK_FLOOR` to match `main`,
      i.e. to reintroduce #557 by way of the test meant to prevent #595. Comparing
      against the actual latest release closes that gap: right after `main` moves, the
      docs correctly still show the OLD release's floor and this test stays green, the
      same way `scripts/install.sh` itself would still enforce the old floor.

    #546 raised `requires-python` to 3.14 and that release shipped (v0.12.0), but
    `docs/desktop-install.md` and the v0.12.0 release notes' Desktop section still said
    "3.11": every hand-maintained copy agreed with every other one, and all of them were
    wrong, because nothing checked them against a release's actual metadata. Anyone on
    3.11-3.13 who read "you qualify" and ran the installer got refused by pip
    mid-install.

    Both floors are parsed fresh here -- from `main`'s `pyproject.toml` and from the
    latest tag's -- never hardcoded and never read from this module's `_FLOOR` constant,
    so a mistake in `_FLOOR` itself cannot mask a real drift.
    """
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    requires = pyproject["project"]["requires-python"]
    parsed = re.match(r">=\s*(\d+)\.(\d+)", requires)
    assert parsed, f"could not parse a floor out of requires-python {requires!r}"
    dev_floor = f"{parsed.group(1)}.{parsed.group(2)}"

    release_floor = _latest_release_floor()
    if release_floor is None:
        pytest.skip(
            "no release tags reachable locally -- cannot determine the latest shipped "
            "floor without them (a shallow clone without fetch-tags); ci.yml fetches "
            "tags, so this runs for real in CI"
        )

    wheels_pattern = re.compile(r"wheels declare `?>=\s*(\d+\.\d+)")
    for relpath in ("README.md", "docs/desktop-install.md", "packaging/macos_app.sh"):
        text = " ".join((_ROOT / relpath).read_text(encoding="utf-8").split())
        found = wheels_pattern.search(text)
        assert found, f"{relpath} no longer states the wheels floor beside 'wheels declare'"
        assert found.group(1) == release_floor, (
            f"{relpath} states the wheels floor as {found.group(1)!r}, but the latest "
            f"release actually ships {release_floor!r} -- this is the exact drift #595 "
            "was filed over. (If main's requires-python has moved ahead of the latest "
            "release, that is fine and expected -- do NOT bump this doc to match main; "
            "it should keep stating the latest RELEASE's floor until a release ships "
            "that raises it.)"
        )

    install_sh = (_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    fallback = re.search(r'FALLBACK_FLOOR="(\d+\.\d+)"', install_sh)
    assert fallback, "scripts/install.sh's FALLBACK_FLOOR constant is gone"
    assert fallback.group(1) == release_floor, (
        f"scripts/install.sh's FALLBACK_FLOOR is {fallback.group(1)!r}, but the latest "
        f"release actually ships {release_floor!r}. (If main's requires-python has moved "
        "ahead of the latest release, do NOT bump FALLBACK_FLOOR to match main -- that is "
        "bug #557. FALLBACK_FLOOR tracks the oldest still-installable release, never main.)"
    )

    release_yml = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert f"Python {dev_floor}+." in release_yml, (
        f"the Desktop section of the NEXT release's notes (.github/workflows/release.yml) "
        f"does not state the floor as {dev_floor!r} -- it will publish a stale floor the "
        "moment the next release runs, exactly as the v0.12.0 notes did (#595). This one "
        "DOES compare against main's requires-python, on purpose: the template generates "
        "the next release from whatever main says at tag time."
    )
