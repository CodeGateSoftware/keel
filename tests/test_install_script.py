"""The terminal installer: what the script must always be, pinned over its text.

`scripts/install.sh` is the one file users are asked to pipe straight into bash (#479),
which makes every line of it a security surface. It cannot be executed here -- it needs
the network and a fresh machine -- so what is pinned is the same discipline
`tests/test_desktop_packaging.py` applies to the shell and workflow artifacts it guards:
the set of properties that would be expensive to discover were false. Each of these is a
way the script could be quietly made unauditable by a refactor: a dropped `pipefail`, a
helpful `sudo`, an install that names a package instead of a downloaded file (there is an
unrelated "keel" on PyPI, which is why the by-path rule exists), or a success banner that
survived the removal of the verification that earned it.
"""

from __future__ import annotations

import re
import stat
import tomllib
from pathlib import Path

import pytest

from keel.commands.update import PRODUCTION_WHEEL_PREFIXES

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "install.sh"


@pytest.fixture(scope="module")
def script() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(script: str) -> list[str]:
    """The commands only: non-blank, non-comment lines.

    A comment may EXPLAIN a forbidden thing (why there is no signing, why wheel checksums
    are absent); only code can DO one -- so the absence tests below run against code
    lines, the same split `tests/test_desktop_packaging.py` makes for the macOS packer.
    """
    return [
        line
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


# -- the floor the whole script stands on ----------------------------------------------------------


def test_fail_fast_is_on(script: str) -> None:
    """`set -euo pipefail`: any failing command or pipe stops the script, so a broken
    step can never be followed by a success message."""
    assert "set -euo pipefail" in script


def test_the_script_is_executable() -> None:
    assert _SCRIPT.exists()
    assert _SCRIPT.stat().st_mode & stat.S_IXUSR


def test_the_platform_is_checked_and_refuses_the_unsupported(script: str) -> None:
    """macOS and Linux are the supported pair (the CI smoke runs exactly those); any
    other `uname` must be refused loudly, with the Windows route named."""
    assert "uname" in script
    assert "Darwin" in script and "Linux" in script
    assert "desktop-install.md" in script  # where the refused platforms are sent


def test_every_curl_is_strict(code: list[str]) -> None:
    """`-fsSL`: fail on HTTP errors, no progress noise, follow the release redirect to
    the asset host. A curl that tolerates a 404 body would install whatever HTML came
    back. Only actual invocations count -- `command -v curl` is a PATH probe, not a
    download."""
    curls = [line for line in code if re.search(r"\bcurl\s+-", line)]
    assert curls, "the installer never curls anything -- this test proves nothing"
    for line in curls:
        assert "-fsSL" in line, f"a curl without -fsSL: {line}"


# -- no privilege, no secrets, no nested pipe-to-shell ---------------------------------------------


def test_no_privileged_commands(code: list[str]) -> None:
    """A per-user install under $HOME needs none; the moment `sudo` appears, the
    no-warning path becomes a system modification the user was not promised."""
    offenders = [line for line in code if "sudo" in line]
    assert not offenders, f"sudo is invoked: {offenders}"


def test_no_shell_piping_of_downloaded_code(script: str) -> None:
    """The user may pipe THIS script into bash; the script itself must never pipe
    anything it fetched into a shell, and must not `eval` -- those are the two ways a
    bootstrap script turns into a loader for arbitrary code."""
    assert not re.search(r"\|\s*(ba|z|da|k)?sh\b", script), "a pipe into a shell"
    offenders = [line for line in script.splitlines() if re.search(r"\beval\b", line)]
    assert not offenders, f"eval is used: {offenders}"


def test_no_credentials_or_auth(script: str) -> None:
    """The public releases API is read unauthenticated, and a bootstrap script must
    never grow a credential -- nothing to leak, nothing to phish for."""
    for secret in ("Authorization", "TOKEN", "PASSWORD", "SECRET"):
        assert secret not in script, f"{secret} appears in the installer"


# -- Python: the floor is checked, and stated when it fails ----------------------------------------


def test_the_python_floor_is_checked_and_stated(script: str) -> None:
    """The interpreter's own `version_info` must be compared against the floor every
    distribution declares, and the failure must NAME it -- a bare 'wrong python' sends the
    user nowhere.

    The floor is DERIVED from `requires-python` rather than written here, because this is the
    one place it is stated in shell rather than in metadata, and shell is not resolved by pip.
    Hardcoded, a raised floor leaves the installer building a venv keel then refuses to run in,
    and the mismatch surfaces as an import error after a successful-looking install.
    """
    root = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    requires = root["project"]["requires-python"]
    assert requires.startswith(">="), requires
    floor = requires[2:].strip()
    major, minor = (int(part) for part in floor.split("."))

    assert re.search(rf"sys\.version_info >= \({major}, ?{minor}\)", script), (
        f"the >= {floor} check via sys.version_info is gone or has drifted from "
        f"requires-python ({requires!r}) -- the installer would build a venv keel refuses "
        "to run in"
    )
    assert floor in script, "the failure message must state the floor"


# -- the wheels: exactly the five, never by name from an index -----------------------------------


def test_the_allowlist_is_keel_owns_production_prefixes(script: str) -> None:
    """The script's allowlist must EQUAL `PRODUCTION_WHEEL_PREFIXES` -- the same five
    distributions, in the same order, as the updater's selector. Selection is by exact
    `<prefix>-<version>-` name, so a drift here (a sixth wheel, a renamed one) is the
    difference between a deployment and a different machine."""
    match = re.search(r'^WHEEL_PREFIXES="([^"]+)"', script, re.MULTILINE)
    assert match, "the WHEEL_PREFIXES allowlist line is gone from scripts/install.sh"
    assert tuple(match.group(1).split()) == PRODUCTION_WHEEL_PREFIXES


def test_the_venue_wheels_a_deployment_must_not_have_are_absent(script: str) -> None:
    """Not merely unselected -- absent. The release carries a dev-only fake venue, an
    optional venue and a stub venue that a deployment must never ride; if their names
    appear anywhere in the script (even as a comment saying 'not this one'), the
    allowlist above is no longer the thing that keeps them out."""
    for banned in ("fake", "robinhood", "kraken"):
        assert banned not in script, f"{banned!r} appears in the installer"


def test_installs_by_exact_wheel_path_never_by_name(script: str, code: list[str]) -> None:
    """keel is installed BY PATH from the downloaded release wheels -- the hard repo
    rule, because `pip install keel` would fetch an UNRELATED PyPI project. Every
    install invocation must carry `--find-links` (so the keel wheels' pinned keel
    dependencies resolve from the download, not an index) and the wheel-path array; a
    bare package name must not appear anywhere."""
    assert not re.search(r"pip install keel\b", script), (
        "an install names a keel package rather than a downloaded wheel path"
    )
    joined = "\n".join(code)
    assert '-m pip install --no-input --find-links "$TMP_DIR" "${WHEEL_PATHS[@]}"' in joined, (
        "the pip path's exact wheel-path install form is gone"
    )
    assert 'uv pip install --python "$VENV_PY" --find-links "$TMP_DIR" "${WHEEL_PATHS[@]}"' in (
        joined
    ), "the uv path's exact wheel-path install form is gone"


def test_the_pipless_venv_gets_one_honest_bootstrap(script: str) -> None:
    """`python -m venv` can produce a venv without pip; the fallback must attempt
    `ensurepip` and then fail clearly -- never skip the install silently."""
    assert "ensurepip" in script
    assert "--no-input" in script


# -- the user's data: guarded ----------------------------------------------------------------------


def test_an_existing_config_is_never_overwritten(script: str, code: list[str]) -> None:
    """config.yaml is the user's the moment it lands: it may hold their edits. The copy
    from the release must sit behind an existence check that SAYS it kept the existing
    one -- a re-run that upgrades keel must not reset configuration."""
    assert '[ -e "${KEEL_DIR}/config.yaml" ]' in "\n".join(code)
    assert "not overwritten" in script
    assert 'cp "${TMP_DIR}/config.yaml" "${KEEL_DIR}/config.yaml"' in script


def test_an_existing_database_is_never_touched(script: str) -> None:
    """Upgrading code must not mean touching data; a `keel*.db` under the install
    directory is acknowledged and left alone."""
    assert "keel*.db" in script
    assert "not touched" in script


def test_destructive_cleanup_targets_only_the_temp_dir(code: list[str]) -> None:
    for line in code:
        if "rm -rf" in line:
            assert "TMP_DIR" in line, f"rm -rf outside the temp dir: {line}"


# -- verified before success ----------------------------------------------------------------------


def test_versions_runs_before_any_success_message(code: list[str]) -> None:
    """`keel versions` -- the one check that can actually fail, because it reports every
    keel distribution the venv resolves and whether they agree -- must run BEFORE the
    success banner. A success message that survives the removal of its verification is
    the desktop milestone's silent-failure lesson applied to the installer."""
    verify = [i for i, line in enumerate(code) if 'bin/keel" versions' in line]
    assert verify, "the installer no longer runs 'keel versions' from the venv"
    success = [i for i, line in enumerate(code) if "installed keel" in line]
    assert success, "no success line found -- this test proves nothing"
    assert min(verify) < min(success), (
        "the success banner is printed before 'keel versions' verified the install"
    )
