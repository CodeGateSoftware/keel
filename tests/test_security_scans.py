"""The code-quality scans, configured for real and without secrets that don't exist (#291).

The pre-launch gate (#291) names a gap that has been true since the repo opened: the CI
story is lint + types + tests, and nothing else -- `SONAR_TOKEN` and `SNYK_TOKEN` were
wishlist names for secrets nobody ever created, so keel has never had static analysis or
dependency scanning. For a public financial tool that is a bad first impression waiting to
happen, and the fix is not to go buy tokens: a public repository can run both classes of
scan on what GitHub gives it for free. Dependabot watches the manifests; a weekly
`pip-audit` over the exact locked set the app ships with catches vulnerable versions
pinned in `uv.lock`; CodeQL (Python) reads the source the same way the `test` context
reads the tests.

This file pins that the configuration exists and says what it must: every manifest
directory Dependabot needs (the workspace root plus each `packages/*` distribution --
a manifest it does not watch is a dependency that updates silently), the actions ecosystem
so the workflows themselves stay current, the audit actually reading the lock's export,
CodeQL on Python, and -- the honest part -- NO scan step depending on a secret: the scans
must keep running for a contributor who has none of the tokens the old wishlist named.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]

#: The six Python manifest directories of the workspace: the root distribution plus the
#: five packages/* distributions. Dependabot needs each named; a manifest not named here
#: is a distribution whose dependencies update without review.
_MANIFEST_DIRS = (
    "/",
    "/packages/keel-core",
    "/packages/keel-broker-api",
    "/packages/keel-broker-coinbase",
    "/packages/keel-broker-fake",
    "/packages/keel-broker-robinhood",
)

#: The distributions excluded when exporting the lock for audit -- excluded because they
#: are this repo's own code (not on PyPI, not auditable there), and pinned so the audit
#: cannot quietly start including or dropping one.
_OWN_DISTRIBUTIONS = (
    "keel-trader",
    "keel-core",
    "keel-broker-api",
    "keel-broker-coinbase",
    "keel-broker-fake",
    "keel-broker-robinhood",
)

#: Secrets the scans must NOT depend on. They have never existed in this repository; a
#: scan step that references them is a scan step that fails for everyone but whoever was
#: supposed to create the token -- which is the gap #291 exists to close, restated as YAML.
_FORBIDDEN_SECRETS = ("SONAR_TOKEN", "SNYK_TOKEN")


def _read(relative: str) -> str:
    """A repo file's text; empty until it exists, so a red run FAILS rather than errors."""
    path = _ROOT / relative
    return path.read_text() if path.is_file() else ""


def test_dependabot_watches_every_python_manifest_and_the_actions():
    """Six pip manifests and the workflows -- nothing updates silently.

    The workspace is six distributions, and Dependabot's `pip` ecosystem works per
    manifest directory: an unlisted directory gets no update PRs, ever. The
    `github-actions` entry keeps the workflow actions themselves (checkout, uv, CodeQL)
    from aging into the exact advisory-visibility problem this file exists for.
    """
    path = _ROOT / ".github" / "dependabot.yml"
    assert path.is_file(), (
        ".github/dependabot.yml must exist -- updates unwatched are updates unseen"
    )
    config = yaml.safe_load(path.read_text())
    assert config.get("version") == 2, "dependabot.yml must be the version-2 schema"
    entries = config.get("updates", [])
    pip_dirs = {e.get("directory") for e in entries if e.get("package-ecosystem") == "pip"}
    for directory in _MANIFEST_DIRS:
        assert directory in pip_dirs, (
            f"dependabot.yml must watch the manifest at {directory!r} -- a distribution "
            "Dependabot does not see is a distribution whose dependencies update silently"
        )
    ecosystems = {e.get("package-ecosystem") for e in entries}
    assert "github-actions" in ecosystems, (
        "dependabot.yml must also watch github-actions -- the scan workflows' own actions "
        "age like any other dependency"
    )


def test_every_dependabot_entry_is_scheduled():
    """Watching is not enough; every entry must actually run on a schedule."""
    path = _ROOT / ".github" / "dependabot.yml"
    if not path.is_file():
        assert False, ".github/dependabot.yml must exist -- updates unwatched are updates unseen"
    entries = yaml.safe_load(path.read_text()).get("updates", [])
    unscheduled = [e for e in entries if not e.get("schedule", {}).get("interval")]
    assert not unscheduled, (
        f"these dependabot entries have no schedule interval: {unscheduled} -- an entry "
        "that never runs is a configuration file lying about what it does"
    )


def test_the_security_workflow_audits_the_locked_dependency_set():
    """pip-audit reads the lock's export -- the set that ships, not what resolves today.

    Auditing `uv export` output (not a bare `pip-audit` of the environment, not a fresh
    resolve) is what makes the audit reproducible: the pinned versions in `uv.lock` are
    the versions every deployment gets, so those are the versions that must be scanned.
    The repo's own distributions are excluded because they are the code being shipped,
    not third-party dependencies of it.
    """
    workflow = _read(".github/workflows/security.yml")
    assert workflow, ".github/workflows/security.yml must exist -- the scans are configured in code"
    assert "uv export" in workflow and "--all-extras" in workflow, (
        "the audit must consume `uv export --all-extras` output -- the full locked set, "
        "extras included, because a vulnerable optional dependency is still a dependency"
    )
    for distribution in _OWN_DISTRIBUTIONS:
        assert f"--no-emit-package {distribution}" in workflow, (
            f"the export must exclude the repo's own {distribution} -- it is the code under "
            "scan, not a third-party dependency of it"
        )
    assert "pip-audit" in workflow, (
        "the workflow must run pip-audit against the exported lock -- this is the "
        "dependency scanning #291 says has never existed here"
    )


def test_the_security_workflow_runs_codeql_on_python():
    """Static analysis of the source, tokenless -- CodeQL over the Python suite."""
    workflow = _read(".github/workflows/security.yml")
    assert "github/codeql-action/init" in workflow, (
        "the workflow must initialise CodeQL -- this is the static analysis #291 says has "
        "never existed here"
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

    Comments in these files are YAML `#` remarks (or the same inside a shell heredoc);
    stripping them lets the forbidden-secret check scan what the runner executes.
    """
    return "\n".join(re.split(r"(^|\s)#", line)[0] for line in text.split("\n"))


def test_no_scan_step_depends_on_a_secret_that_does_not_exist():
    """The scans run on what a public repo has -- no SONAR_TOKEN, no SNYK_TOKEN, none.

    The wishlist names in #291 were never created; a scan step referencing them would be
    a scan that works only for a hypothetical maintainer with hypothetical tokens. The
    configuration is honest when its executable lines reference no secrets at all (the
    workflows' comments may name the absent tokens, because that is where the decision
    to run tokenless is documented).
    """
    for relative in (".github/workflows/security.yml", ".github/workflows/ci.yml"):
        workflow = _read(relative)
        assert workflow, f"{relative} must exist"
        executable = _code_lines(workflow)
        for secret in _FORBIDDEN_SECRETS:
            assert secret not in executable, (
                f"{relative} references {secret}, which does not exist in this repository -- "
                "the scans must run tokenless or they do not run"
            )
        dangling = sorted(set(re.findall(r"secrets\.([A-Z_]+)", executable)))
        assert not dangling, (
            f"{relative} references secrets {dangling} -- the scan workflows must run on "
            "GITHUB_TOKEN alone, or they fail for every contributor without them"
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
