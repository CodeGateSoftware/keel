"""The workspace's own dependency metadata: one version, pinned everywhere.

keel ships as six distributions cut from this repo in a single build, so "which version" is one
answer, not six. Left unpinned, a `keel-core` requirement is satisfied by whatever `keel-core` is
already installed, and installing the new `keel_trader` wheel upgrades nothing else -- which is
how `~/keel` came to run `keel-trader 0.5.7` against `keel-core 0.5.5` across two releases with
`keel --version` reporting the new number the whole time.

The pins are what make that impossible regardless of how someone installs. Their cost is that a
version bump has to move them too, and a forgotten pin is silent: the build still succeeds and the
wheel still installs, it just stops forcing the upgrade. These tests are what make it loud instead.
They read the `pyproject.toml` files from the source tree, so they are a repo-hygiene check, not a
runtime one -- `keel versions` is the runtime side, and it checks what is actually installed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _pyprojects() -> dict[str, dict]:
    """distribution name -> parsed `pyproject.toml`, for the root and every workspace member."""
    found = {}
    for path in [_ROOT / "pyproject.toml", *sorted((_ROOT / "packages").glob("*/pyproject.toml"))]:
        data = tomllib.loads(path.read_text())
        found[data["project"]["name"]] = data
    return found


def _requirement_name(spec: str) -> str:
    """`keel-core==0.6.0` -> `keel-core`;
    `coinbase-advanced-py>=1.8.4` -> `coinbase-advanced-py`."""
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";", " "):
        spec = spec.split(sep)[0]
    return spec.strip().lower().replace("_", "-")


def test_every_workspace_package_carries_the_same_version():
    """Six distributions, one release. A version that moved alone is a packaging bug."""
    versions = {name: data["project"]["version"] for name, data in _pyprojects().items()}
    assert len(set(versions.values())) == 1, f"workspace versions disagree: {versions}"


@pytest.mark.parametrize("name", sorted(_pyprojects()))
def test_workspace_siblings_are_pinned_to_the_exact_version(name):
    """Every intra-workspace dependency must be `==<this version>`, in every package.

    Not `>=`: a lower bound is satisfied by a newer sibling too, and the point is to forbid a
    MIXED install, not merely an old one.
    """
    projects = _pyprojects()
    version = projects[name]["project"]["version"]
    for spec in projects[name]["project"].get("dependencies", []):
        dep = _requirement_name(spec)
        if dep not in projects:
            continue  # a third-party dependency, with its own release cycle
        assert spec == f"{dep}=={version}", (
            f"{name} depends on {spec!r}; it must be '{dep}=={version}' so that installing "
            f"{name} cannot leave an older {dep} in place"
        )


def test_the_dev_only_fake_venue_is_not_a_runtime_dependency_of_anything():
    """It registers a `fake` venue entry point; a deployment must never install it.

    `keel versions` fails a release build that has it. This asserts nothing can drag it in.
    """
    for name, data in _pyprojects().items():
        deps = [_requirement_name(s) for s in data["project"].get("dependencies", [])]
        assert "keel-broker-fake" not in deps, f"{name} must not depend on keel-broker-fake"


#: The flag that identifies a strict override. `strict = true` cannot be used in a per-module
#: section -- mypy applies it GLOBALLY whatever `module` the section names -- so the strict
#: packages spell the bundle out flag by flag instead, and this is the one that best marks the
#: intent. Detecting the old `strict = true` spelling as well keeps this honest if a section is
#: ever added that way: the marker rule below should still cover it (the scoping bug is a
#: separate problem from the PEP 561 one).
_STRICT_MARKER = "disallow_untyped_defs"


def _mypy_overrides() -> list[dict]:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text())["tool"]["mypy"]["overrides"]


def _strict_modules() -> list[str]:
    """Import packages the root `[tool.mypy]` config checks in strict mode.

    Read from the config rather than listed here, so that tightening a package (giving it the
    strict flag block) automatically brings it under the marker rule below instead of requiring
    someone to remember this file.
    """
    modules: list[str] = []
    for override in _mypy_overrides():
        if not (override.get("strict") or override.get(_STRICT_MARKER)):
            continue
        entry = override["module"]
        for pattern in [entry] if isinstance(entry, str) else entry:
            modules.append(pattern.removesuffix(".*"))
    return sorted(modules)


@pytest.mark.parametrize("module", _strict_modules())
def test_strictly_typed_packages_ship_a_py_typed_marker(module):
    """A package mypy checks strictly must declare that fact to whoever installs it (PEP 561).

    Without the marker the annotations are invisible off the source tree: mypy in a CONSUMER's
    project reports `module is installed, but missing library stubs or py.typed marker` and falls
    back to `Any` for every symbol crossing the boundary. The wheel still builds and imports, so
    the loss is silent -- which is the same failure shape as the unpinned siblings above, and the
    reason this is a test rather than a note. All four broker distributions shipped that way
    through 0.7.0; `keel_broker_api` is the one that matters most, since the port's types are the
    contract every adapter and every consumer codes against.

    Scoped to strict modules on purpose: `keel.*` and `keel_core.*` are still `ignore_errors`, and
    a marker on unchecked code promises a guarantee nothing verifies. `keel_core` carries one
    anyway for historical reasons -- that is allowed, this rule is a floor, not an equality.
    """
    candidates = [*(_ROOT / "packages").glob(f"*/{module}/py.typed"), _ROOT / module / "py.typed"]
    assert any(p.is_file() for p in candidates), (
        f"{module} is type-checked strictly but ships no py.typed marker; "
        f"create an empty one beside its `__init__.py` so installers can see the annotations"
    )


def test_the_strict_module_list_is_not_empty():
    """`_strict_modules()` must actually find the strict packages.

    It discovers them by reading the mypy config, which means a change to how strictness is
    SPELLED there silently empties the parametrization above -- `pytest` then reports one
    skipped `[NOTSET]` case instead of four passing ones, and the py.typed rule stops being
    enforced without anything going red. That is exactly what happened when the broker section
    moved off `strict = true` onto the expanded flag list. A guard whose coverage can vanish
    quietly needs a guard of its own.
    """
    assert _strict_modules(), (
        "no strictly-typed modules found in [tool.mypy] overrides -- if the way strictness is "
        f"configured changed, update `_STRICT_MARKER` ({_STRICT_MARKER!r}) to match"
    )


def test_keel_is_not_exempt_from_type_checking():
    """`keel.*` must not reappear on an `ignore_errors` override.

    CI runs `mypy` (`ci.yml`, `release.yml`), which catches a type ERROR in `keel/`. It cannot
    catch the other direction: re-adding `keel.*` here silences the whole package, mypy goes
    green, and the ungating done in #266 is undone with nothing to show for it. That is the
    failure mode this file already guards for strictness, in the opposite direction -- an
    exemption that reads as a passing build.

    `tests.*` and `keel_core.*` are still legitimately exempt (see the comments beside each in
    pyproject.toml); this pins only the one that was deliberately brought under the checker.
    """
    exempt = []
    for override in _mypy_overrides():
        if not override.get("ignore_errors"):
            continue
        entry = override["module"]
        exempt.extend([entry] if isinstance(entry, str) else entry)

    assert "keel.*" not in exempt, (
        "`keel.*` is back on an `ignore_errors` override, which silently un-checks the entire "
        "package -- mypy will pass while checking nothing there. It was ungated deliberately "
        f"(#266); currently exempt: {sorted(exempt)!r}"
    )


def test_broker_strict_flags_match_mypy_strict():
    """The expanded flag list must stay equal to what `--strict` actually turns on.

    The broker packages spell `--strict` out flag by flag because `strict = true` is not a
    per-module setting (mypy applies it globally regardless of the `module` pattern, which is
    how `keel.*` came to be checked under full strict mode while appearing exempt). The cost of
    expanding it is that the list is now a COPY of mypy's, and a copy drifts: a future mypy that
    adds a flag to the bundle would tighten every other project and quietly leave these four
    behind.

    So the bundle is derived from the installed mypy rather than hard-coded here -- diffing a
    default `Options` against a `--strict` one -- and compared with the config. `implicit_reexport`
    is the one flag whose sense is inverted (`--strict` clears it; the config sets its `no_`
    form), and `warn_redundant_casts` is excluded because it is global-only and already mypy's
    default, so it never appears in the diff.
    """
    import contextlib
    import io

    from mypy.main import process_options

    def options(extra: list[str]):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _, opts = process_options([*extra, "x.py"], server_options=False)
        return opts

    default, strict = options([]), options(["--strict"])
    expected = set()
    for name in (n for n in dir(default) if not n.startswith("_")):
        value = getattr(strict, name, None)
        if isinstance(value, bool) and getattr(default, name, None) != value:
            # `--strict` CLEARS implicit_reexport; the config states that as `no_implicit_reexport`.
            expected.add(name if value else f"no_{name}")

    # Dropped explicitly, not by accident: `warn_redundant_casts` is part of the bundle but is
    # global-only, so it lives in `[tool.mypy]`. It happens not to appear in the diff above
    # because it is already mypy's default -- if that default ever flips it WOULD appear, and
    # this test would then demand it in a per-module section where mypy refuses to accept it,
    # leaving the config unsatisfiable. Excluding it by name keeps that impossible.
    expected.discard("warn_redundant_casts")

    broker_override = next(
        (
            o
            for o in _mypy_overrides()
            if o.get(_STRICT_MARKER) and "keel_broker_api.*" in o["module"]
        ),
        None,
    )
    # Asserted rather than left to `next()`: re-collapsing the block to `strict = true` is THE
    # regression this test exists to catch, and a bare `StopIteration` from an exhausted
    # generator is the least legible way pytest can report it.
    assert broker_override is not None, (
        f"no broker override sets {_STRICT_MARKER!r} -- if the block was collapsed back to "
        "`strict = true`, that re-enables strict mode globally for every module (see the "
        "comment above the block in pyproject.toml); expand it into its flags again"
    )
    configured = {k for k, v in broker_override.items() if k != "module" and v is True}

    assert configured == expected, (
        "the broker strict-flag list has drifted from mypy's own `--strict` bundle.\n"
        f"  missing from pyproject.toml: {sorted(expected - configured)}\n"
        f"  no longer implied by --strict: {sorted(configured - expected)}\n"
        "Update the [[tool.mypy.overrides]] block for the broker packages to match."
    )
