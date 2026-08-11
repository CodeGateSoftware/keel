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
