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
        line for line in script.splitlines() if line.strip() and not line.lstrip().startswith("#")
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


# -- Python: the floor is the RELEASE's, derived at install time (#557) ----------------------------


def test_the_floor_comes_from_the_release_being_installed(script: str) -> None:
    """The installer must enforce the `requires-python` of the RELEASE it installs, not the
    development tree's floor (#557): main required 3.14 while the wheels being downloaded
    declared >= 3.11, so the script refused -- at its own step 2 -- the very Pythons the
    wheels support, and every Linux leg of install-smoke went red. The floor is fetched
    from the resolved tag's own pyproject.toml, parsed out of its `requires-python`, and
    the interpreter check compares against THAT derived floor, never a hardcoded minor.
    """
    assert "https://raw.githubusercontent.com/${REPO}/${TAG}/pyproject.toml" in script, (
        "the installer no longer fetches the installed release's pyproject.toml -- the "
        "floor would silently become a hardcoded one again"
    )
    assert "requires-python" in script, "the requires-python parse is gone from the installer"
    assert "sys.version_info >= (${FLOOR_MAJOR}, ${FLOOR_MINOR})" in script, (
        "the version check no longer compares against the derived floor -- a literal minor "
        "here is how #557 happened"
    )
    # The parse is pinned whole, because the assertions above cannot tell a correct
    # program from a loosened one: a specifier class like `[<>=]*` passes them all while
    # reading a `<3.14`-style specifier as a floor of 3.14 -- #557 smuggled back in
    # through the regex instead of a hardcoded minor. The program must accept ONLY a
    # `>=X.Y` requires-python.
    sed_program = (
        r"""s/^[[:space:]]*requires-python[[:space:]]*=[[:space:]]*">=[[:space:]]*"""
        r"""\([0-9][0-9]*\.[0-9][0-9]*\)\(\.[0-9][0-9]*\)*"[[:space:]]*$/\1/p"""
    )
    assert sed_program in script, (
        "the requires-python parse is no longer the exact >=-only sed program -- a "
        "loosened specifier class would accept '<3.14'-style specifiers and re-introduce "
        "#557 through the regex"
    )


def test_the_floor_fetch_precedes_the_python_search(script: str) -> None:
    """The floor depends on the release tag, so the tag must be resolved and its floor
    read BEFORE any Python candidate is tried -- a refactor that swaps the order back
    cannot know the floor it is checking against."""
    assert script.index("${TAG}/pyproject.toml") < script.index("for candidate in"), (
        "the release's floor is fetched after the Python search already ran -- the "
        "chicken-and-egg #557 fixed is back"
    )


def test_a_failed_floor_fetch_falls_back_to_a_commented_constant(script: str) -> None:
    """If the pyproject fetch or the parse fails, the script must fall back to a
    conservative constant whose comment NAMES what it stands for and where it must be
    updated -- a bare constant would rot the first time a release raises its floor."""
    match = re.search(r'^FALLBACK_FLOOR="(\d+\.\d+)"', script, re.MULTILINE)
    assert match, "the FALLBACK_FLOOR constant is gone from scripts/install.sh"
    above = script[: match.start()].splitlines()[-4:]
    assert any(line.lstrip().startswith("#") and "shipped" in line.lower() for line in above), (
        "the fallback constant's comment no longer names the shipped-wheel floor it stands "
        "for and where it must be updated"
    )


def test_the_enforced_floor_and_its_source_are_printed(script: str, code: list[str]) -> None:
    """Auditable: the run must say WHICH floor it enforces and WHERE it came from, so an
    operator (or a red CI leg) can see the release the number was read from. The fallback
    branch must be named by the text that reaches the operator -- the uppercase FALLBACK
    literal inside the FLOOR_SOURCE construction -- not merely by a lowercase 'fallback'
    in a comment, which tells the operator nothing."""
    assert ">= ${FLOOR}" in script, "the enforced floor is not stated"
    assert "${FLOOR_SOURCE}" in script, "the floor's source is not stated beside it"
    assert any('FLOOR_SOURCE="the FALLBACK constant' in line for line in code), (
        "the fallback is never NAMED in what is printed -- the FLOOR_SOURCE construction "
        "must carry the literal FALLBACK, not just a comment mentioning one"
    )


def test_the_candidate_names_are_built_from_the_floor(script: str) -> None:
    """`python3 python3.14` was a fixed pair tied to the old fixed floor; the candidate
    names must be BUILT from the derived floor (a 3.11 floor tries 3.14 down to 3.11, so a
    pyenv or deadsnakes Python without a `python3` shim is still found) -- a moved floor
    must move the search with it."""
    assert "for candidate in python3 python3.14" not in script, (
        "the pre-#557 fixed candidate pair is back"
    )
    assert 'CANDIDATES+=("python${FLOOR_MAJOR}.${minor}")' in script, (
        "candidate names are no longer constructed from the floor's minor versions"
    )


def test_the_no_python_failure_names_remedies(script: str) -> None:
    """'install a newer Python' sends a Linux user nowhere (#557): the failure must name
    the three real roads -- the deadsnakes PPA on Ubuntu, pyenv, and uv's own interpreter
    installer."""
    die_at = script.find('[ -n "$PY" ] || die')
    assert die_at != -1, "the no-Python failure path is gone"
    window = script[die_at : die_at + 900]
    for remedy in ("deadsnakes", "pyenv install", "uv python install"):
        assert remedy in window, (
            f"the no-Python failure message does not name the {remedy!r} remedy"
        )


def test_no_development_tree_floor_remains_in_code(code: list[str]) -> None:
    """The dev tree's floor (3.14 when #557 was filed) must not survive anywhere the
    script ENFORCES or states it -- comments may cite the history, only code can apply
    the wrong floor (the same split the absence tests above make)."""
    offenders = [line for line in code if "3.14" in line]
    assert not offenders, (
        f"the dev-tree 3.14 floor is hardcoded in installer code: {offenders} -- the floor "
        "must be derived from the release being installed (#557)"
    )


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


# -- the config the release ships: confirm-mode production, never "paper" (#556) ------------------


def test_the_config_is_called_what_it_is_in_both_places(script: str) -> None:
    """The release's config.yaml is `keel/templates/config.live.yaml` shipped as
    `config.yaml` -- the PRODUCTION config in `auto_trade.mode: confirm`: keel previews
    every order and waits for explicit approval, and with credentials present it CAN
    place live orders. Calling it "the paper profile" (#556) printed a cannot-place-a-
    live-order reassurance directly above the line asking for a Coinbase CDP key -- wrong
    in the direction that costs money. BOTH places that describe the config (the step 6
    copy line and the closing next-steps block) must say what it is.
    """
    assert script.count("auto_trade.mode: confirm") >= 2, (
        "the step 6 copy line and the next-steps block must both name auto_trade.mode: confirm"
    )
    assert "paper profile" not in script, (
        'the installed config is still described as "the paper profile" (#556)'
    )


def test_the_next_steps_point_at_the_real_paper_template(script: str) -> None:
    """`mode: paper` IS a real thing in keel -- the dev template `keel init-config` writes
    WITHOUT `--live` (`keel/cli.py`: "the dev template in `mode: paper`, which places
    nothing at all") -- so the honest fix is not to stop mentioning paper but to say how
    to GET it: the next-steps block must name `init-config --force` as the swap-in."""
    assert "init-config --force" in script, (
        "the next-steps block does not say how to obtain the paper template"
    )
    assert "auto_trade.mode: paper" in script or "mode: paper" in script


# -- the published one-liner, qualified where it is advertised (#557) -----------------------------


def test_the_published_one_liner_is_qualified_for_linux(script: str) -> None:
    """README.md publishes the one-liner and docs/desktop-install.md mirrors it, both
    without qualification; on Linux the script stops at its Python step unless a
    supported interpreter is already on PATH (#557). Both places must say so and state
    the CURRENT SHIPPED floor (3.11) -- not the development tree's floor, which is what
    broke the Linux leg in the first place. The DMG's first-mount note (written by
    packaging/macos_app.sh, above the pip-install-the-wheels escape hatch) tells the same
    truth. Checked against whitespace-normalized text: the qualifier must survive the
    repo's line wrapping, not sit on one lucky line. The README's stated floor is then
    checked against the script's FALLBACK_FLOOR constant, so the two numbers that are
    maintained by hand cannot drift apart silently."""
    readme = " ".join((_ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "On Linux" in readme and "wheels declare" in readme and "3.11" in readme, (
        "README.md does not qualify the installer one-liner for Linux with the shipped floor"
    )
    desktop = " ".join((_ROOT / "docs" / "desktop-install.md").read_text(encoding="utf-8").split())
    assert "wheels declare" in desktop and "3.11" in desktop, (
        "docs/desktop-install.md does not state the shipped floor beside the one-liner"
    )
    assert "Python 3.14 or later" not in desktop, (
        "docs/desktop-install.md still publishes the DEV tree's floor as the installer's "
        "requirement -- exactly the confusion #557 is about"
    )
    dmg = " ".join((_ROOT / "packaging" / "macos_app.sh").read_text(encoding="utf-8").split())
    assert "wheels declare" in dmg and "3.11" in dmg, (
        "packaging/macos_app.sh's DMG note does not state the shipped floor beside the "
        "pip-install-the-wheels alternative"
    )
    assert "Python 3.14 or later" not in dmg, (
        "the DMG's first-mount note still publishes the DEV tree's floor as the wheels' "
        "requirement -- the same confusion #557 is about, one mount screen later"
    )
    fallback = re.search(r'^FALLBACK_FLOOR="(\d+\.\d+)"', script, re.MULTILINE)
    assert fallback, "the FALLBACK_FLOOR constant is gone from scripts/install.sh"
    stated = re.search(r"wheels declare `?>=\s*(\d+\.\d+)", readme)
    assert stated, "README.md no longer states the shipped floor beside the one-liner"
    assert fallback.group(1) == stated.group(1), (
        f"README.md states the shipped floor as {stated.group(1)!r} while the installer's "
        f"FALLBACK_FLOOR is {fallback.group(1)!r} -- two manual constants that must be one"
    )


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
