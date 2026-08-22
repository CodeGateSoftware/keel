"""The code-quality scans, configured for real and without secrets that don't exist (#291).

The pre-launch gate (#291) names a gap that had been true since the repo opened: the CI
story was lint + types + tests, and nothing else -- `SONAR_TOKEN` and `SNYK_TOKEN` were
wishlist names for secrets nobody ever created, so no scan that needs them has ever run.
The fix is not to buy tokens: a public repository can run both classes of scan on what
GitHub gives it for free. `security.yml` is the tokenless BASELINE -- Dependabot watches
the manifests, a weekly `pip-audit` reads the exact locked set the app ships with, and
CodeQL (Python) reads the source -- and `code-quality.yml` stays the OPTIONAL enhanced
tier (SonarQube + Snyk) for if those tokens are ever created.

This file pins that split. The manifest lists are DERIVED FROM THE FILESYSTEM (a new
distribution under packages/ that Dependabot and the export do not know about fails here,
rather than passing because a hand-maintained list was never told), the audit is pinned to
`uv export --frozen` (the lock as committed, never a fresh resolve), and the secrets rule
is the honest one: the always-on scan workflows reference no secrets at all, and the
optional tier may reference `SONAR_TOKEN`/`SNYK_TOKEN` ONLY from jobs a preflight guards
-- so a missing prerequisite can never redden a scheduled run, only skip it with an
explanation.

And #402 taught where "configured" stops: the tokens were created on 2026-08-18, the
preflight flipped to ready, both scans ran for the first time -- and both FAILED, because
each needs ORG-level configuration no repository secret can carry (SonarQube Cloud's
mandatory `sonar.organization`; the Snyk organization on the snyk.io side). Every push to
`main` went red for reasons the repository could not fix, which is the exact permanently-
red-main outcome this tier was designed never to produce. The guard is therefore pinned
PER SCANNER and must measure the scan's real readiness, not merely token existence.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from tests._workflow_yaml import strict_load

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"

#: The optional enhanced tier: the one workflow allowed to reference the two tokens that
#: have never existed, and only behind its preflight guard.
_OPTIONAL_TIER = "code-quality.yml"

#: Secrets the baseline must NOT reference. They have never existed in this repository;
#: a baseline scan step that needs them is a scan that has never run -- the exact gap
#: #291 exists to close.
_FORBIDDEN_IN_BASELINE = ("SONAR_TOKEN", "SNYK_TOKEN")


def _read(relative: str) -> str:
    """A repo file's text; empty until it exists, so a red run FAILS rather than errors."""
    path = _ROOT / relative
    return path.read_text() if path.is_file() else ""


def _load_workflow(name: str) -> dict:
    """A workflow parsed the way GitHub parses it: duplicate keys refused, not merged.

    Every structural claim this file makes about code-quality.yml rides on PyYAML keeping
    the key GitHub would keep. `yaml.safe_load`'s last-key-wins would let a duplicate-key
    edit pass every pin here and then disable the workflow at dispatch time -- the exact
    v0.11.0 release.yml incident class, whose discipline (born in
    tests/test_desktop_packaging.py) tests/_workflow_yaml.py now shares.
    """
    return strict_load((_WORKFLOWS / name).read_text(encoding="utf-8"), source=name)


def _pyprojects() -> dict[str, dict]:
    """The workspace's manifests, keyed by directory -- the shape Dependabot must watch.

    Mirrors tests/test_packaging.py's discovery: the root pyproject.toml plus every
    packages/*/pyproject.toml. A distribution the dict does not contain is a distribution
    this file cannot speak about, which is why the derivation (not a list) matters.
    """
    manifests = {"/": tomllib.loads((_ROOT / "pyproject.toml").read_text())}
    for path in sorted((_ROOT / "packages").glob("*/pyproject.toml")):
        manifests[f"/{path.parent.relative_to(_ROOT).as_posix()}"] = tomllib.loads(path.read_text())
    return manifests


def test_dependabot_watches_every_python_manifest_and_the_actions():
    """Every manifest the filesystem declares -- nothing updates silently.

    The workspace is eight distributions today, and Dependabot's `pip` ecosystem works per
    manifest directory: an unlisted directory gets no update PRs, ever. The expected set
    is DERIVED from packages/*/pyproject.toml, so adding another distribution without
    telling Dependabot fails here instead of shipping a blind spot.
    """
    path = _ROOT / ".github" / "dependabot.yml"
    assert path.is_file(), (
        ".github/dependabot.yml must exist -- updates unwatched are updates unseen"
    )
    config = yaml.safe_load(path.read_text())
    assert config.get("version") == 2, "dependabot.yml must be the version-2 schema"
    entries = config.get("updates", [])
    pip_dirs = {e.get("directory") for e in entries if e.get("package-ecosystem") == "pip"}
    manifests = _pyprojects()
    for directory in manifests:
        assert directory in pip_dirs, (
            f"dependabot.yml must watch the manifest at {directory!r} -- a distribution "
            "Dependabot does not see is a distribution whose dependencies update silently"
        )
    assert pip_dirs == set(manifests), (
        f"dependabot.yml watches directories that no longer exist ({pip_dirs - set(manifests)}) "
        "-- a stale entry is configuration lying about what it does"
    )
    ecosystems = {e.get("package-ecosystem") for e in entries}
    assert "github-actions" in ecosystems, (
        "dependabot.yml must also watch github-actions -- the scan workflows' own actions "
        "age like any other dependency"
    )


def test_every_dependabot_entry_is_scheduled():
    """Watching is not enough; every entry must actually run on a schedule."""
    path = _ROOT / ".github" / "dependabot.yml"
    assert path.is_file(), (
        ".github/dependabot.yml must exist -- updates unwatched are updates unseen"
    )
    entries = yaml.safe_load(path.read_text()).get("updates", [])
    unscheduled = [e for e in entries if not e.get("schedule", {}).get("interval")]
    assert not unscheduled, (
        f"these dependabot entries have no schedule interval: {unscheduled} -- an entry "
        "that never runs is a configuration file lying about what it does"
    )


def test_the_security_workflow_audits_the_locked_dependency_set():
    """pip-audit reads the lock's export, frozen -- the set that ships, not a resolve.

    Auditing `uv export --frozen` output (not a bare environment audit, not a fresh
    resolve) is what makes the audit honest about what deployments run: the pinned
    versions in the committed `uv.lock` are the versions every deployment gets, so those
    are the versions that must be scanned. The repo's own distributions -- derived, not
    listed -- are excluded because they are the code being shipped, not dependencies of
    it; dev dependencies are deliberately included (the job gates nothing, and a CVE in
    the dev toolchain still runs on contributors' machines).
    """
    workflow = _read(".github/workflows/security.yml")
    assert workflow, (
        ".github/workflows/security.yml must exist -- the baseline scans are configured in code"
    )
    assert "uv export --frozen" in workflow and "--all-extras" in workflow, (
        "the audit must consume a FROZEN `uv export --all-extras` -- the full locked set as "
        "committed, extras included, never a fresh resolve in CI"
    )
    own = {data["project"]["name"] for data in _pyprojects().values()}
    for distribution in sorted(own):
        assert f"--no-emit-package {distribution}" in workflow, (
            f"the export must exclude the repo's own {distribution} -- it is the code under "
            "scan, not a third-party dependency of it"
        )
    assert "pip-audit" in workflow, (
        "the workflow must run pip-audit against the exported lock -- this is the "
        "dependency scanning #291 says has never actually run here"
    )


def test_the_security_workflow_runs_codeql_on_python():
    """Static analysis of the source, tokenless -- CodeQL over the Python suite."""
    workflow = _read(".github/workflows/security.yml")
    assert "github/codeql-action/init" in workflow, (
        "the workflow must initialise CodeQL -- this is the static analysis #291 says has "
        "never actually run here"
    )
    assert "languages: python" in workflow, (
        "CodeQL must analyse Python -- the only language this repo ships"
    )
    assert "github/codeql-action/analyze" in workflow, (
        "init without analyse is a workflow that reads the code and says nothing"
    )
    assert "security-events: write" in workflow, (
        "CodeQL must carry security-events: write to upload its results -- without the "
        "permission the scan runs and its findings go nowhere"
    )


def _code_lines(text: str) -> str:
    """The workflow with comments stripped -- a token NAMED in a comment explaining its
    absence is documentation; a token REFERENCED in code is a dependency on a secret.

    The `#` remarks in these files are YAML comments; stripping them lets the
    secret check scan what the runner executes. (No workflow here puts a meaningful `#`
    inside an executed string; cron expressions and the folded export scalar do not.)
    """
    return "\n".join(re.split(r"(^|\s)#", line)[0] for line in text.split("\n"))


def test_the_baseline_scans_reference_no_secrets_at_all():
    """The always-on workflows run on GITHUB_TOKEN alone -- no wishlist tokens, none.

    The baseline is what makes the scans real: `security.yml` and `ci.yml` must reference
    no secret whatsoever, or they would be scans that run only for a hypothetical
    maintainer with hypothetical tokens -- the gap #291 exists to close, restated as YAML.
    """
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        if path.name == _OPTIONAL_TIER:
            continue
        executable = _code_lines(path.read_text())
        referenced = sorted(set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", executable)))
        assert not referenced or referenced == ["GITHUB_TOKEN"], (
            f"{path.name} references secrets {referenced} -- a workflow that always runs "
            "must run on GITHUB_TOKEN alone, or it fails for every contributor without "
            "the missing tokens"
        )


def test_the_strict_loader_rejects_a_duplicate_key_workflow_text() -> None:
    """Why every workflow parse here goes through `strict_load`, pinned on the loader itself.

    PyYAML's `safe_load` resolves duplicate mapping keys last-key-wins, so a workflow edit
    that leaves a stale `run:` beside the rewritten one parses fine, passes every pin in
    this file -- and is refused by GitHub's STRICT parser only at dispatch, disabling the
    workflow on main outright (v0.11.0's first re-dispatch of release.yml, exactly). This
    is the same discipline tests/test_desktop_packaging.py applies to release.yml; here it
    guards code-quality.yml, whose preflight structure the tests below pin key by key.
    """
    leaky = """
on: push
jobs:
  preflight:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v7
        uses: actions/checkout@v6
"""
    with pytest.raises(AssertionError, match="duplicate key 'uses'"):
        strict_load(leaky, source="duplicate-key workflow text")
    # And it is duplicate keys it refuses, not workflows: the same text with the stale
    # line removed loads, so the loader cannot hide behind refusing everything.
    cleaned = leaky.replace("        uses: actions/checkout@v6\n", "")
    assert strict_load(cleaned, source="clean")["jobs"]


def test_the_optional_tier_only_asks_for_its_tokens_behind_the_preflight_guard():
    """code-quality.yml may name SONAR_TOKEN/SNYK_TOKEN -- but only guarded.

    The optional tier's design constraint (its own header documents it): a job that needs
    a token must declare `needs: preflight` and run under the readiness guard FOR ITS OWN
    scanner, so a missing prerequisite SKIPS with an explanation instead of reddening
    every push to main. #402 is what happens when the guard is coarser than the failure:
    one shared `configured` flag was true the moment both tokens existed, so both scan
    jobs started running while only one of each pair's prerequisites was in place. This
    test pins the guard structurally, per job, so a future edit cannot detach a
    token-referencing job from its preflight -- nor re-merge the two scanners into one
    all-or-nothing flag.
    """
    workflow = _load_workflow(_OPTIONAL_TIER)
    jobs = workflow.get("jobs", {})
    assert "preflight" in jobs, (
        f"{_OPTIONAL_TIER} must keep its preflight job -- it is what lets the optional "
        "tier skip cleanly while its prerequisites are missing"
    )
    outputs = jobs["preflight"].get("outputs", {})
    for output in ("sonar_ready", "snyk_ready"):
        assert output in outputs, (
            f"preflight must declare a per-scanner `{output}` output -- a guard named in "
            "a job's `if` but never produced skips that job FOREVER, silently"
        )
    token_to_guard = {"SONAR_TOKEN": "sonar_ready", "SNYK_TOKEN": "snyk_ready"}
    for name, job in jobs.items():
        serialized = str(job)
        referenced = [s for s in _FORBIDDEN_IN_BASELINE if f"secrets.{s}" in serialized]
        if not referenced or name == "preflight":
            continue
        assert "preflight" in (job.get("needs") or []), (
            f"{_OPTIONAL_TIER}'s job {name!r} references {referenced} but does not declare "
            "`needs: preflight` -- a token-referencing job must be guarded"
        )
        for token in referenced:
            guard = f"needs.preflight.outputs.{token_to_guard[token]} == 'true'"
            assert guard in str(job.get("if", "")), (
                f"{_OPTIONAL_TIER}'s job {name!r} references {token} but is not gated on "
                f"`{guard}` -- without its own guard a missing prerequisite for that "
                "scanner reddens every push to main (#402)"
            )


def test_preflight_readiness_includes_the_org_level_config_not_just_the_tokens():
    """The #402 lesson: a token proves the secret exists, not that the scan can run.

    Both tokens were created on 2026-08-18; from that push onward every run of this
    workflow on `main` failed -- SonarQube Cloud with "You must define the following
    mandatory properties ... sonar.organization", Snyk with a server-side 422 -- because
    the remaining prerequisites live OUTSIDE the repository: the `sonar.organization`
    property (deliberately commented out in sonar-project.properties until the org owner
    supplies the real key) and the Snyk organization that the scan must be filed under
    (the `--org` the snyk monitor step has carried as a placeholder since the workflow
    was written). A preflight that checks only token presence cannot see either one, so
    this test pins that it checks both, and that each scan passes its org along when it
    finally runs.
    """
    workflow = _load_workflow(_OPTIONAL_TIER)
    preflight_text = str(workflow["jobs"]["preflight"])
    assert "sonar.organization" in preflight_text, (
        "preflight must check sonar-project.properties carries an active "
        "`sonar.organization` -- SonarQube Cloud rejects the scan without it (#402), and "
        "a token-only check stays green while the scan cannot run"
    )
    assert "SNYK_ORG" in preflight_text, (
        "preflight must check the SNYK_ORG repository variable -- the Snyk organization "
        "is org-level configuration the repository cannot derive, and the 422 it causes "
        "is invisible to a token-only check (#402)"
    )
    snyk_text = str(workflow["jobs"]["snyk"])
    assert "SNYK_ORG" in snyk_text and "--org" in snyk_text, (
        "the snyk job must pass the configured organization (`--org`) -- a placeholder "
        "comment is not configuration; the scan must be filed under the real org"
    )


def test_preflights_sonar_readiness_requires_a_value_not_just_an_uncommented_line() -> None:
    """A blank `sonar.organization=` is not readiness -- it is #402 one edit later.

    The readiness grep once accepted any uncommented `sonar.organization=` line, value or
    none. But SonarCloud's "You must define the following mandatory properties ...:
    sonar.organization" refusal fires for an EMPTY value exactly as for a missing one, so
    that grep would flip `sonar_ready` to true the moment someone uncommented the line
    without filling the key in -- and every push to `main` would go red again, the exact
    permanently-red outcome the preflight exists to prevent. The pattern is extracted from
    the workflow itself and pinned twice: as text (the non-blank tail must be there) and
    as behaviour (translated to Python's re, on the exact lines SonarCloud does not
    distinguish between).
    """
    workflow = _load_workflow(_OPTIONAL_TIER)
    script = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["preflight"]["steps"])
    grep_line = next(
        (line for line in script.splitlines() if "grep -Eq" in line and "sonar" in line),
        None,
    )
    assert grep_line is not None, (
        "preflight must keep grep-ing sonar-project.properties for `sonar.organization` -- "
        "an org-level check no repository secret can carry (#402)"
    )
    match = re.search(r"grep -Eq '([^']+)'", grep_line)
    assert match is not None, "the readiness check's pattern must stay a quoted `grep -Eq` argument"
    posix_ere = match.group(1)
    # The non-blank requirement itself: something other than whitespace must follow the `=`.
    assert "=[[:space:]]*[^[:space:]]" in posix_ere, (
        f"the readiness pattern {posix_ere!r} must require a non-whitespace VALUE after "
        "`sonar.organization=` -- an uncommented-but-empty key still trips SonarCloud's "
        "mandatory-property refusal, so accepting it as ready reddens main again (#402)"
    )
    # Behaviour, on the lines the refusal does not distinguish between.
    python_pattern = posix_ere.replace("[^[:space:]]", "[^ \\t]").replace("[[:space:]]", "[ \\t]")
    ready = re.compile(python_pattern).search
    assert ready("sonar.organization=x"), "a set key is ready"
    assert ready("sonar.organization = codegate"), "whitespace around the = is still a set key"
    assert not ready("sonar.organization="), "uncommented but EMPTY is not ready"
    assert not ready("sonar.organization=   "), "only whitespace after the = is not ready"
    assert not ready("#sonar.organization=x"), "a still-commented line is not ready"
    assert not ready(""), "nothing set at all is not ready"


def test_preflight_still_fails_loudly_only_when_a_human_dispatched_it():
    """The asymmetry that makes skipping honest: a human dispatch is refused loudly,
    an automatic trigger skips with an explanation. Reversed, the workflow either
    reddens every push to main while prerequisites are missing (#402, observed) or
    silently ignores a direct request."""
    workflow = _load_workflow(_OPTIONAL_TIER)
    steps = workflow["jobs"]["preflight"]["steps"]
    run = "\n".join(str(step.get("run", "")) for step in steps)
    assert "workflow_dispatch" in run, (
        "preflight must branch on the event type -- the skip-vs-fail decision is the "
        "point of the preflight job"
    )
    assert "::error" in run and "exit 1" in run, (
        "a dispatched run that cannot scan must FAIL with an annotation -- silently "
        "doing nothing in response to a direct request is the worse outcome"
    )


def test_the_security_workflow_runs_on_a_schedule_not_only_on_push():
    """A vulnerability published Tuesday is in the lock Wednesday whether or not anyone pushes.

    Dependabot proposes updates when versions move, but the audit must also fire on the
    calendar: new CVEs are published against versions already pinned. A workflow that
    only runs on push scans exactly as often as the repo is noisy.
    """
    workflow = _read(".github/workflows/security.yml")
    assert "schedule:" in workflow and "cron:" in workflow, (
        ".github/workflows/security.yml must carry a schedule trigger -- CVEs are "
        "published against already-pinned versions, not only against new commits"
    )
