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


def test_the_publish_option_is_named_for_what_it_does(workflow: dict) -> None:
    """These artifacts are not code-signed, by decision: Apple notarisation needs a paid
    Developer ID and there is no free substitute -- a self-signed certificate buys nothing,
    because Gatekeeper trusts Apple-issued IDs and nothing else.

    Given that, the protection is that nobody can publish unsigned binaries without reading the
    word "unsigned". `publish` would have been a value you could pick without thinking; this one
    is not."""
    desktop = workflow[True]["workflow_dispatch"]["inputs"]["desktop"]
    assert set(desktop["options"]) == {"build", "publish-unsigned", "skip"}
    assert "publish" not in desktop["options"]
    assert "unsigned" in desktop["description"].lower()


def test_the_default_attaches_nothing_to_a_release(workflow: dict, desktop_job: dict) -> None:
    """The safe value is the one you get by not thinking about it."""
    assert workflow[True]["workflow_dispatch"]["inputs"]["desktop"]["default"] == "build"
    attaching = [
        step
        for step in desktop_job["steps"]
        if "gh release upload" in str(step.get("run", ""))
        or "gh release create" in str(step.get("run", ""))
    ]
    assert attaching, "nothing attaches artifacts, so this test proves nothing"
    for step in attaching:
        assert str(step.get("if", "")).strip() == "inputs.desktop == 'publish-unsigned'"


def test_every_artifact_carries_provenance_and_checksums(desktop_job: dict) -> None:
    """What replaces OS-level trust, and it is free. An attestation ties each artifact to this
    repository, this workflow and this commit -- which is the question a code-signing certificate
    answers too, and the one an auditable project should care about most."""
    steps = desktop_job["steps"]
    attest = [s for s in steps if "attest-build-provenance" in str(s.get("uses", ""))]
    assert attest, "no build provenance attestation"
    assert attest[0]["with"]["subject-path"] == "out/*"
    assert "SHA256SUMS" in _steps_text(desktop_job)

    # The attestation needs OIDC, and the job must ask for it explicitly rather than relying on
    # a workflow-wide grant that would also widen the release job.
    assert desktop_job["permissions"]["id-token"] == "write"
    assert desktop_job["permissions"]["attestations"] == "write"


def test_provenance_is_produced_before_anything_is_attached(desktop_job: dict) -> None:
    """Attaching first would publish an artifact that is briefly unverifiable, and a failed
    attestation afterwards would leave it published anyway."""
    steps = desktop_job["steps"]
    attest_at = next(
        i for i, s in enumerate(steps) if "attest-build-provenance" in str(s.get("uses", ""))
    )
    attach_at = next(i for i, s in enumerate(steps) if "gh release upload" in str(s.get("run", "")))
    assert attest_at < attach_at


def test_the_release_notes_say_the_builds_are_unsigned_and_how_to_verify_them() -> None:
    """A download that trips Gatekeeper with no explanation is indistinguishable from a broken
    one, and a user who is told to click past a security warning with no way to check what they
    have is being taught a bad habit while holding exchange API keys."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "not code-signed" in text
    assert "Privacy & Security" in text
    assert "Open Anyway" in text
    assert "gh attestation verify" in text
    assert "SHA256SUMS" in text


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


# -- what the person downloading it is told ----------------------------------------------------

_INSTALL_DOC = _ROOT / "docs" / "desktop-install.md"


def test_the_install_note_covers_both_platforms_and_both_prices() -> None:
    """Windows was left as "a separate decision, still open" for one round. It is not: Azure
    Trusted Signing is ~$120/yr, MORE than Apple's, and since 2024 an EV certificate no longer
    grants an instant SmartScreen pass -- so it buys less for more. A page that named only the
    Apple cost would leave a reader assuming Windows was simply forgotten."""
    text = _INSTALL_DOC.read_text(encoding="utf-8")
    assert "$99" in text
    assert "120" in text
    assert "SmartScreen" in text
    assert "either" in text


def test_the_install_note_leads_with_the_path_that_has_no_warning() -> None:
    """Someone on this page is deciding whether to proceed. The first thing they should read is
    that there is a route with no warning at all -- not four paragraphs about certificates."""
    text = _INSTALL_DOC.read_text(encoding="utf-8")
    body = text[text.index("\n## ") :]
    first_heading = body.split("\n")[1]
    assert "not deal with this at all" in first_heading, first_heading
    assert body.index("Try it in five minutes") < body.index("The short version")


def test_the_install_note_gives_real_per_os_steps() -> None:
    """ "Open Anyway" alone is not instructions. Someone who has never done this needs to be told
    where the setting is, that Sequoia removed the right-click shortcut, and -- on Windows -- to
    Unblock the zip BEFORE extracting, which is what stops the prompt returning."""
    text = _INSTALL_DOC.read_text(encoding="utf-8")
    assert "Privacy & Security" in text
    assert "Sequoia" in text
    assert "Unblock" in text
    assert "Extract All" in text
    assert "Program Files" in text  # and why not to use it


def test_the_install_note_exists_and_states_the_actual_reason() -> None:
    """The reason is a budget, and saying so is better than "not signed at this time".

    A user who is told a build is unsigned with no reason assumes carelessness. A user who is
    told the certificate costs $99/yr and the project cannot commit to it has been given a fact
    they can weigh -- and it is the truth."""
    text = _INSTALL_DOC.read_text(encoding="utf-8")
    assert "$99" in text
    assert "cannot currently afford" in text.replace("either one", "")
    assert "no cheaper tier" in text or "no free option" in text
    # A self-signed certificate is the obvious "why not just..." and must be answered.
    assert "made ourselves" in text or "self-signed" in text


def test_the_install_note_tells_the_user_how_to_verify_before_bypassing() -> None:
    """We are asking someone to click past a security warning on a program they may give exchange
    API keys to. Asking that without offering a check would be the wrong trade."""
    text = _INSTALL_DOC.read_text(encoding="utf-8")
    assert "gh attestation verify" in text
    assert "SHA256SUMS" in text
    assert "do not open" in text.lower()


def test_the_install_note_says_what_the_warning_does_not_mean() -> None:
    """ "Damaged" is what macOS sometimes says, and it is not what happened. Leaving that
    uncorrected is how a working download gets deleted."""
    # Emphasis stripped: the doc bolds the "not", and a test that missed it because of two
    # asterisks would be checking markdown rather than meaning.
    text = _INSTALL_DOC.read_text(encoding="utf-8").lower().replace("*", "")
    assert "does not mean the download is damaged" in text
    assert "nothing was scanned" in text


def test_the_readme_documentation_map_links_the_install_note() -> None:
    text = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/desktop-install.md" in text


def test_the_dmg_carries_the_note_beside_the_app() -> None:
    """A page in the repository is no use to someone staring at "keel cannot be opened because
    the developer cannot be verified". The disk image is where they are actually looking."""
    text = _MACOS_SCRIPT.read_text(encoding="utf-8")
    assert "READ ME FIRST.txt" in text
    assert "$99" in text
    # The image is built from a staging directory, not from the .app alone -- otherwise the note
    # is written and then not shipped.
    assert '-srcfolder "$STAGE"' in text


def test_the_release_notes_point_at_the_full_explanation() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "docs/desktop-install.md" in text
    assert "cannot currently afford" in text
