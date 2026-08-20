"""The desktop artifacts: what the workflow must never do, and what the bundle must always be.

The build job cannot be run here -- it needs three OS runners -- so what is pinned is the set of
claims that would be expensive to discover were false: that publishing an unsigned artifact is
impossible by accident, and that the smoke step still checks each of the four ways a bundle breaks.
Three of those four are SILENT (#458): the binary starts cleanly and has no venues, or no version
identity, or no templates.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_MACOS_SCRIPT = _ROOT / "packaging" / "macos_app.sh"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def desktop_job(workflow: dict) -> dict:
    return workflow["jobs"]["desktop"]


def _steps_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


# -- the thing that must not happen ------------------------------------------------------------


def test_publishing_an_unsigned_artifact_fails_rather_than_succeeding_quietly(
    desktop_job: dict,
) -> None:
    """An unsigned binary that then asks for exchange API keys is the shape of malware
    distribution -- the desktop PRD opens on a live example found while surveying this space.

    So `publish` must FAIL while signing is unconfigured. A step that silently did nothing when a
    secret was missing is exactly how an unsigned artifact ends up attached to a release looking
    like a signed one."""
    guards = [
        step
        for step in desktop_job["steps"]
        if str(step.get("if", "")).strip() == "inputs.desktop == 'publish'"
    ]
    assert guards, "nothing guards the publish path"
    assert any("exit 1" in str(step.get("run", "")) for step in guards)


def test_the_default_is_build_not_publish(workflow: dict) -> None:
    """Publishing is opt-in. The safe value is the one you get by not thinking about it."""
    desktop = workflow[True]["workflow_dispatch"]["inputs"]["desktop"]
    assert desktop["default"] == "build"
    assert set(desktop["options"]) == {"build", "publish", "skip"}


def test_no_step_attaches_a_desktop_artifact_to_the_github_release(desktop_job: dict) -> None:
    """The only route to a release asset is `gh release upload`/`create`. While signing is
    unconfigured there must be no such call in this job at all -- the guard above is the
    intended stop, and this is the one that catches a second route added around it."""
    text = _steps_text(desktop_job)
    assert "gh release upload" not in text
    assert "gh release create" not in text


def test_the_desktop_job_runs_after_the_release_and_builds_that_tag(desktop_job: dict) -> None:
    """It must package the commit that was actually released, not whatever `main` drifted to."""
    assert desktop_job["needs"] == "release"
    checkout = next(
        s for s in desktop_job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["ref"] == "v${{ inputs.version }}"


# -- the four silent failures, still checked ---------------------------------------------------


def test_the_smoke_step_asserts_the_bundle_identifies_itself(desktop_job: dict) -> None:
    """Without the stamp a bundle reports `[checkout]`; without collected metadata
    `keel versions` answers "no keel distributions installed" -- the deploy check that exists to
    catch a partial upgrade, reporting success by having nothing to compare."""
    text = _steps_text(desktop_job)
    assert "--version" in text
    assert "versions" in text
    assert "DIRTY" in text


def test_the_smoke_step_asserts_the_bundle_has_venues_and_not_the_fake_one(
    desktop_job: dict,
) -> None:
    """`0 adapter(s)` was the real result of the first build that looked fine. And the dev-only
    fake venue must never reach a shipped artifact: it would put a FAKE VENUE in the venue list
    of a signed install a real person downloaded, which is worse than shipping nothing because it
    looks like a supported option."""
    text = _steps_text(desktop_job)
    assert "brokers list" in text
    assert "adapter" in text
    assert "fake" in text.lower()


def test_the_smoke_step_asserts_a_first_run_can_write_a_config(desktop_job: dict) -> None:
    """The templates are package DATA. Without them `init-config` cannot write a config, so a
    first run cannot start at all."""
    assert "init-config" in _steps_text(desktop_job)


def test_the_bundle_is_stamped_before_it_is_frozen(desktop_job: dict) -> None:
    """Order matters: the stamp is a source file the freeze embeds. Stamping afterwards would
    produce an unstamped bundle and a stamped working tree."""
    names = [str(step.get("name", "")) for step in desktop_job["steps"]]
    assert names.index("Stamp build info") < names.index("Freeze")
    assert names.index("Freeze") < names.index("Smoke the bundle")


# -- the macOS bundle --------------------------------------------------------------------------


def test_the_macos_script_is_executable() -> None:
    assert _MACOS_SCRIPT.exists()
    assert _MACOS_SCRIPT.stat().st_mode & stat.S_IXUSR


def test_the_app_launches_serve_and_not_the_cli() -> None:
    """An app launched from Finder has NO controlling terminal. A bundle whose entry point were
    the CLI would open, find no tty, refuse every gated action and exit with nothing on screen.
    The packaging research rates this the single biggest technical risk in the milestone."""
    text = _MACOS_SCRIPT.read_text(encoding="utf-8")
    assert '"$BIN" serve' in text
    assert "CFBundleExecutable" in text


def test_the_launcher_does_not_invent_a_working_directory() -> None:
    """Finder launches with cwd `/`, and every keel path used to resolve against cwd -- the
    blocker D1 (#434) removed. `keel_core.paths` now resolves state to the OS app-data directory,
    and a `cd` in the launcher would override that with a guess."""
    text = _MACOS_SCRIPT.read_text(encoding="utf-8")
    assert "cd /" in text
    assert "cd $HOME" not in text and 'cd "$HOME"' not in text


def test_the_script_runs_no_signing_command() -> None:
    """It produces an UNSIGNED bundle on purpose -- signing needs a Developer ID certificate that
    exists only in the release workflow's protected environment. A script that appeared to sign
    would be worse than one that plainly does not.

    Comment lines are excluded rather than the whole file searched: the script EXPLAINS in prose
    which commands signing will need, and a test that forbade the words would forbid documenting
    them."""
    code = [
        line
        for line in _MACOS_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for command in ("codesign", "notarytool", "stapler", "productsign"):
        offenders = [line for line in code if command in line]
        assert not offenders, f"{command} is invoked: {offenders}"
