"""The desktop artifacts: what the workflow must never do, and what the bundle must always be.

The build job cannot be run here -- it needs three OS runners -- so what is pinned is the set of
claims that would be expensive to discover were false: that publishing an unsigned artifact is
impossible by accident, and that the smoke step still checks each of the four ways a bundle breaks.
Three of those four are SILENT (#458): the binary starts cleanly and has no venues, or no version
identity, or no templates.
"""

from __future__ import annotations

import stat
import tomllib
from pathlib import Path

import pytest

from tests._workflow_yaml import strict_load

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_MACOS_SCRIPT = _ROOT / "packaging" / "macos_app.sh"
_INNO_SCRIPT = _ROOT / "packaging" / "keel.iss"
_SMOKE_WORKFLOW = _ROOT / ".github" / "workflows" / "installer-smoke.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    # The strict loader (duplicate keys REFUSED, like GitHub's parser) lives in
    # tests/_workflow_yaml.py since test_security_scans.py needed the same discipline
    # for code-quality.yml -- this file is where it was born (v0.11.0's re-dispatch).
    return strict_load(_WORKFLOW.read_text(encoding="utf-8"), source="release.yml")


@pytest.fixture(scope="module")
def desktop_job(workflow: dict) -> dict:
    return workflow["jobs"]["desktop"]


def _steps_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


@pytest.fixture(scope="module")
def release_job(workflow: dict) -> dict:
    return workflow["jobs"]["release"]


# -- the release must fail on a stale lockfile where the mistake is made ------------------------


def test_the_lockfile_is_checked_before_anything_can_mutate_it(
    workflow: dict, release_job: dict
) -> None:
    """#424: the 0.10.0 release, and the error that pointed everywhere but at the cause.

    A version bump touches eight pyproject.toml files; `uv.lock` is the ninth thing that
    must move with them. When it does not, `uv sync` silently re-locks the checkout, the
    release is stamped from a dirty tree, and the failure surfaces FIVE steps later as
    "artifact reports a dirty tree" -- after lint, types, tests and a full build have all
    run and passed, sending the reader to `keel/version.py` and the stamp step rather
    than to the lockfile. The guard belongs before the first step that can mutate the
    tree (`uv sync`), and its failure message must NAME the remedy.
    """
    steps = release_job["steps"]
    names = [str(step.get("name", "")) for step in steps]
    lock_step = next(
        (i for i, s in enumerate(steps) if "uv lock --check" in str(s.get("run", ""))),
        None,
    )
    assert lock_step is not None, (
        "the release must run `uv lock --check` before it can spend minutes discovering "
        "a stale lockfile as a dirty-tree error (#424)"
    )
    assert "set -euo pipefail" in str(steps[lock_step].get("run", "")), (
        "the lock check must fail the step outright, not fall through to a later one"
    )
    run = str(steps[lock_step].get("run", ""))
    assert "stale" in run and "uv lock" in run and "::error" in run, (
        "the lock check's failure message must name the cause (stale uv.lock) and the "
        "remedy (run `uv lock` locally and commit it with the version bump) -- the "
        "misdirecting error is the reason this guard exists"
    )
    for later in ("Sync dependencies", "Test"):
        at = names.index(later)
        assert lock_step < at, (
            f"the lock check must run before {later!r} -- `uv sync` is the step that "
            "silently re-locks a stale checkout, and everything after it builds on a "
            "tree the release did not intend to ship"
        )


# -- the release must not lie about what the wheel requires -------------------------------------


def test_the_verify_step_comment_states_the_real_python_floor() -> None:
    """#438: the verify step's comment claimed the wheel carries `Requires-Python: >=3.14.4`
    while every pyproject.toml declares `>=3.11` (pinned by tests/test_python_floor.py).

    Harmless to the run -- the pinned interpreter is used either way -- but the comment was
    the only place a reader could learn what the wheel demands, and the next person to build
    packaging on top of it (the desktop job, the installer) would have built on a floor that
    does not exist. The floor stated in the workflow is now DERIVED from the manifest, so it
    cannot drift again."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    floor = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "requires-python"
    ]
    assert f"Requires-Python: {floor}" in text, (
        f"the verify step's comment must state the wheel's real floor ({floor!r}, from "
        "pyproject.toml) -- a stale floor is how the next packaging job gets built on a "
        "requirement that does not exist"
    )
    assert "3.14.4'" not in text.replace(".python-version", ""), (
        "the old false claim (`Requires-Python: >=3.14.4`) must be gone -- 3.14.4 is the "
        "interpreter .python-version pins for the BUILD, not a floor the wheel enforces"
    )


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
    # Subjects are per-OS FILES-ONLY globs: `out/*` would hand the action the unzipped
    # keel.app/ and dmg-stage/ directories macOS packaging leaves beside the .dmg, and a
    # shared `*.dmg *.zip` pattern would hand each leg one glob matching nothing.
    # Windows carries BOTH its deliverables (the zip and the setup.exe) in one
    # space-separated subject-path -- multi-subject attestation, supported since Dec 2024 --
    # because both are attached to the release, so both must be verifiable.
    subjects = {s["with"]["subject-path"] for s in attest}
    assert subjects == {"out/*.dmg", "out/*.zip out/*-setup.exe"}
    macos = next(s for s in attest if s["with"]["subject-path"] == "out/*.dmg")
    windows = next(s for s in attest if s["with"]["subject-path"] == "out/*.zip out/*-setup.exe")
    assert str(macos.get("if", "")).strip() == "runner.os == 'macOS'"
    assert str(windows.get("if", "")).strip() == "runner.os == 'Windows'"
    assert "SHA256SUMS-" in _steps_text(desktop_job)
    # The checksums and the upload are files-only for the same reason as the subjects:
    # a directory has no checksum and `gh release upload` cannot attach one.
    assert "find . -maxdepth 1 -type f" in _steps_text(desktop_job)
    assert "find out -maxdepth 1 -type f" in _steps_text(desktop_job)

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


# -- the Windows installer ---------------------------------------------------------------------


def test_the_inno_script_installs_per_user_without_an_admin_prompt() -> None:
    """#438's Windows deliverable: one setup.exe installing per-user to
    %LOCALAPPDATA%\\Programs\\keel, so the first thing a non-technical user is asked for is
    not an administrator password. `PrivilegesRequired=lowest` is the directive that keeps
    the UAC dialog away; `{localappdata}` is the per-user location Windows itself uses for
    per-user application installs."""
    text = _INNO_SCRIPT.read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in text
    assert "DefaultDirName={localappdata}\\Programs\\keel" in text


def test_the_installer_touches_the_program_and_never_the_deployment() -> None:
    """#438's two locations, kept separate by the installer: the PROGRAM (the frozen binary
    and its bundled runtime) is replaced wholesale; the DEPLOYMENT (config.yaml, keel*.db,
    .env, logs/) is never written, moved, or uninstalled. "config.yaml is never overwritten
    by an installer" and "no database is ever replaced, moved, or migrated by the installer"
    are the issue's hard rules -- an operator's allowlist and caps are hand-edited and
    irreplaceable, and keel has no down-migrations."""
    # Comment lines are excluded, as with the macOS script: the .iss EXPLAINS in prose
    # why there is no [UninstallDelete] section, and a test that forbade the words would
    # forbid documenting them.
    code = [
        line
        for line in _INNO_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    assert not any("[UninstallDelete]" in line for line in code), (
        "an [UninstallDelete] section is how an installer starts deleting things it was "
        "never pointed at -- the deployment must survive an uninstall"
    )
    for forbidden in ("config.yaml", "keel.db", ".env", "logs"):
        assert not any(forbidden in line for line in code), (
            f"the installer must never name the deployment's {forbidden} -- the program "
            "directory is all it owns"
        )
    # The only directory it creates is the program directory.
    assert 'DestDir: "{app}"' in "\n".join(code)


def test_the_workflow_builds_the_setup_exe_beside_the_zip(desktop_job: dict) -> None:
    """The zip is the no-install route and stays; the setup.exe is the installer #438
    specified. BOTH are attached to the release, so both must be built on the Windows leg
    and covered by the same files-only checksum/upload globs (which they are by
    construction -- those steps glob every file in out/)."""
    text = _steps_text(desktop_job)
    assert "keel.iss" in text, "the Windows leg must compile packaging/keel.iss"
    assert "ISCC" in text, "the Windows leg must invoke the Inno Setup compiler"
    assert "7z a -tzip" in text, "the zip must stay -- it is the no-install route"
    assert "-setup.exe" in "\n".join(
        str(s.get("with", {}).get("subject-path", "")) for s in desktop_job["steps"]
    ), "the setup.exe must be an attestation subject -- it is attached to the release"


def test_the_inno_script_is_smoke_compiled_before_any_release_needs_it() -> None:
    """The release workflow is manual-only and its desktop job runs AFTER the release is
    published, so without this the first real ISCC compile of keel.iss would be a release
    dispatch -- a script typo failing a release with the tag already pushed. The smoke
    workflow compiles the same script against a placeholder bundle on a Windows runner.

    It must trigger on PRs touching the script (that is where a compile break is
    introduced, and a workflow not yet on the default branch cannot be dispatched onto a
    branch at all), but ONLY those -- the `paths` filter is what keeps a Windows runner
    from being spent on every PR."""
    assert _SMOKE_WORKFLOW.is_file()
    smoke = strict_load(_SMOKE_WORKFLOW.read_text(encoding="utf-8"), source="installer-smoke.yml")
    triggers = smoke[True]  # PyYAML parses the bare `on` key as boolean True
    assert "workflow_dispatch" in triggers, "a human must always be able to ask for a compile"
    assert set(triggers["pull_request"]["paths"]) == {
        "packaging/keel.iss",
        ".github/workflows/installer-smoke.yml",
    }, (
        "the PR trigger must fire ONLY when the compile can have broken -- the .iss or "
        "the workflow itself -- or every PR pays for a Windows runner"
    )
    run = "\n".join(
        str(step.get("run", "")) for job in smoke["jobs"].values() for step in job["steps"]
    )
    assert "keel.iss" in run and "ISCC" in run, "the smoke must compile the real script"
    assert "placeholder" in run.lower(), (
        "the smoke must not need a freeze -- ISCC packages files, it does not run them, "
        "so a placeholder keel.exe exercises the whole script for one cheap compile"
    )


# -- signing: implemented, gated on the certificates, honest about skipping ---------------------
#
# #438's delta on the "ship unsigned" decision: the signing work is REAL -- codesign with
# the hardened runtime, notarytool --wait, stapler, signtool with an RFC 3161 timestamp --
# but each leg runs only when that leg's credentials exist on the `signing` environment.
# Missing credentials must SKIP WITH A NOTICE that names every secret and what it costs
# (#402's discipline: a missing prerequisite is announced, never a red release), and the
# checks below exist so the gate can never be detached from the step it guards.

_MACOS_SIGNING_SECRETS = (
    "MACOS_CERT_P12_BASE64",
    "MACOS_CERT_PASSWORD",
    "APP_STORE_CONNECT_KEY_ID",
    "APP_STORE_CONNECT_ISSUER_ID",
    "APP_STORE_CONNECT_KEY_CONTENT",
)
_WINDOWS_SIGNING_SECRETS = ("WINDOWS_CERT_PFX_BASE64", "WINDOWS_CERT_PASSWORD")


def _step(job: dict, name: str) -> dict:
    step = next((s for s in job["steps"] if str(s.get("name", "")) == name), None)
    assert step is not None, f"release.yml's desktop job must keep a step named {name!r}"
    return step


def test_the_signing_secrets_live_on_a_protected_environment(desktop_job: dict) -> None:
    """#438: the certificates must be ENVIRONMENT secrets, not repository ones -- a
    repository secret is handed to every same-repo PR build, while an environment secret
    stops at the environment's reviewers. Until the environment exists the reference is
    inert (GitHub treats a missing environment as unprotected) and the signing steps skip
    honestly, which is why this can be declared unconditionally."""
    assert desktop_job["environment"] == "signing", (
        "the desktop job must reference the `signing` environment -- that is the only "
        "thing that stands between a certificate secret and every PR build"
    )


def test_macos_signing_runs_only_when_every_apple_credential_exists(desktop_job: dict) -> None:
    """All FIVE or none: a signed-but-un-notarised app is the worst state on macOS (it
    still trips Gatekeeper, and now looks like it tried not to), so the gate is the whole
    Apple set -- the Developer ID Application .p12 to sign, and the App Store Connect API
    key trio notarytool needs. #402's lesson is measured per component, not by one token."""
    condition = str(_step(desktop_job, "Sign, notarise and staple (macOS)").get("if", ""))
    assert "runner.os == 'macOS'" in condition
    for secret in _MACOS_SIGNING_SECRETS:
        assert f"secrets.{secret} != ''" in condition, (
            f"the macOS sign step must require {secret} -- a partial credential set must "
            "skip, not half-sign"
        )


def test_windows_signing_runs_only_when_the_certificate_exists(desktop_job: dict) -> None:
    condition = str(_step(desktop_job, "Sign the installer (Windows)").get("if", ""))
    assert "runner.os == 'Windows'" in condition
    for secret in _WINDOWS_SIGNING_SECRETS:
        assert f"secrets.{secret} != ''" in condition


def test_each_skip_notice_names_every_missing_secret_and_the_price_of_fixing_it(
    desktop_job: dict,
) -> None:
    """The honest skip: each notice fires on the exact COMPLEMENT of its sign step's gate,
    and says what to buy, which secrets to create, and where the checklist is -- so a
    reader of a green run learns signing was SKIPPED, never believes it happened, and
    knows the purchase that would turn it on (#438's signing table, restated as text)."""
    cases = [
        ("Notice: macOS signing skipped", _MACOS_SIGNING_SECRETS, "$99"),
        ("Notice: Windows signing skipped", _WINDOWS_SIGNING_SECRETS, "SmartScreen"),
    ]
    for name, secrets, product in cases:
        notice = _step(desktop_job, name)
        condition = str(notice.get("if", ""))
        run = str(notice.get("run", ""))
        assert "::notice" in run, f"{name} must be a ::notice, not a log line"
        assert "docs/desktop-install.md" in run
        assert product in run.replace("\\", ""), (
            f"{name} must name the paid product that unlocks signing -- the reader is "
            "being asked to accept an unsigned binary, and the price is the context"
        )
        for secret in secrets:
            assert f"secrets.{secret} == ''" in condition, (
                f"{name} must fire when {secret} is missing -- every gap in the gate "
                "needs its explanation"
            )
            assert secret in run, (
                f"{name} must NAME {secret} -- a notice that says only 'not configured' "
                "has already been failed by code-quality.yml's preflight prose (#402)"
            )


def test_signing_happens_between_packaging_and_the_checksums(desktop_job: dict) -> None:
    """The sums must cover the SIGNED bytes: checksumming first and signing after would
    publish hashes that prove nothing about what the user downloads. Signing must also
    come after packaging, because the steps sign out/keel.app and out/*-setup.exe."""
    names = [str(s.get("name", "")) for s in desktop_job["steps"]]
    package_at = names.index("Package (macOS)")
    installer_at = names.index("Build the installer (Windows)")
    sign_at = names.index("Sign, notarise and staple (macOS)")
    win_sign_at = names.index("Sign the installer (Windows)")
    checksums_at = names.index("Checksums")
    assert package_at < sign_at and installer_at < win_sign_at < checksums_at
    assert sign_at < checksums_at


def test_the_macos_leg_uses_the_hardened_runtime_and_waits_for_notarisation(
    desktop_job: dict,
) -> None:
    """`--options runtime` is REQUIRED for notarisation (an app signed without the
    hardened runtime is rejected server-side, after the upload); `--wait` is what makes a
    Rejected submission FAIL the step instead of returning an id; stapling is what lets a
    machine that never queries Apple verify the ticket. And the certificate must live in
    an EPHEMERAL keychain that is deleted after -- imported into the login keychain it
    would outlive the step until the job ends."""
    run = str(_step(desktop_job, "Sign, notarise and staple (macOS)").get("run", ""))
    assert "--options runtime" in run
    assert "notarytool" in run and "--wait" in run
    assert "stapler staple" in run
    assert "security create-keychain" in run
    assert "security delete-keychain" in run


def test_the_windows_leg_timestamps_its_signature_and_verifies_it(
    desktop_job: dict,
) -> None:
    """An untimestamped signature dies with the certificate -- /tr (RFC 3161) is what
    makes it outlive the cert's expiry -- and an unverified signature is a hope, so the
    step must signtool-verify /pa against the machine's default policy afterwards."""
    run = str(_step(desktop_job, "Sign the installer (Windows)").get("run", ""))
    assert "signtool" in run
    assert "/tr http" in run and "/td SHA256" in run and "/fd SHA256" in run
    assert "verify" in run


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


def test_the_install_note_carries_the_operator_activation_checklist() -> None:
    """#438 made activation a PURCHASE, not a code change -- and the skip notices in the
    workflow point at this page. So the page must hold the complete shopping list: every
    secret name the gates check, the product that sells it, the price from #438's signing
    table, the `signing` environment by name, and the one manual step (release-notes
    wording) whose forgetting errs safe. A checklist missing a name would send the
    operator to GitHub with an incomplete list and a second dispatch they did not expect."""
    text = _INSTALL_DOC.read_text(encoding="utf-8")
    for secret in (*_MACOS_SIGNING_SECRETS, *_WINDOWS_SIGNING_SECRETS):
        assert secret in text, f"the activation checklist must name {secret}"
    assert "Environments" in text and "`signing`" in text, (
        "the checklist must say WHERE the secrets go -- an environment secret in the wrong "
        "place is a repository secret, visible to every same-repo PR build"
    )
    # The prices from #438's signing table, restated where the decision is made.
    assert "$99" in text and "$9.99" in text and "SmartScreen" in text
    assert "EV" in text, (
        "the checklist must warn EV is not worth extra -- no instant SmartScreen pass since 2024"
    )
    # The honest asymmetry: the notes wording cannot auto-detect signing, and the failure
    # mode of forgetting it must be stated (and must be the safe direction).
    assert "safe direction" in text
    assert "notarised" in text or "un-notarised" in text, (
        "the checklist must explain WHY the macOS gate is all five secrets or none"
    )
