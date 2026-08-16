"""Issue and PR templates: they gather what triage needs, and teach the standard on submit.

Templates do two jobs here (#285): they extract the information a report cannot be acted on
without (`keel --version`'s stamped build identity, paper-vs-live, WHICH rail or rule), and
they state the project's expectations at the exact moment someone is about to submit -- that
a security report goes to SECURITY.md not a public issue, that a classification question is
a discussion not a bug, that a PR carries tests-first evidence and names it when it touches
a rail or a default classification.

The failure modes are quiet ones, so they get tests:

- **A missing field** means triage round-trips for it, every time, forever.
- **A compliance question filed as a bug** gets triaged as one -- the issue that asked for
  this template says plainly that this route must be distinct, because a ruling question is
  neither broken behaviour nor a feature.
- **A security report landing in public issues** discloses to everyone simultaneously; the
  whole point of #279 was a private alternative, and config.yml is what routes people to it.
- **A PR template without the rail/classification checkbox** lets the one PR class that
  needs different review look like every other PR.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES = _ROOT / ".github" / "ISSUE_TEMPLATE"


def _form(name: str) -> dict:
    path = _TEMPLATES / name
    assert path.is_file(), f".github/ISSUE_TEMPLATE/{name} does not exist (#285)"
    parsed = yaml.safe_load(path.read_text())
    assert isinstance(parsed, dict) and "body" in parsed, (
        f"{name} is not a valid GitHub issue form (needs top-level keys incl. `body`)"
    )
    return parsed


def _field_ids(form: dict) -> set[str]:
    """Every `id:` across the form's attributes and markdown-free input blocks."""
    return {b["id"] for b in form["body"] if isinstance(b, dict) and b.get("id")}


def test_bug_report_collects_build_identity_mode_and_the_rail_or_rule():
    """The three facts every bug report here is useless without.

    `keel --version` stamps commit and build kind (release/checkout/dirty), because the
    same symptom on two builds is two bugs. Paper-vs-live, because they share nothing.
    The rail or rule involved, because that is the address of the behaviour.
    """
    ids = _field_ids(_form("bug_report.yml"))
    for needed in ("version", "mode", "component"):
        assert needed in ids, (
            f"bug_report.yml is missing the {needed!r} field -- triage will round-trip for it"
        )


def test_the_compliance_question_route_is_distinct_from_bugs_and_features():
    """A classification question must arrive as itself, not wearing a bug costume.

    "Should this asset be classified differently" is neither broken behaviour nor a missing
    feature; triaged as either, it gets the wrong reviewer and the wrong resolution. The
    template exists, is separate from bug_report/feature_request, and says where the
    substantive conversation belongs (a source, and the local-attestation route).
    """
    form = _form("compliance_question.yml")
    text = str(form).lower()
    assert "attestation" in text or "attest" in text, (
        "compliance_question.yml must route the asker toward attestation, not a code change"
    )
    assert "source" in text, (
        "compliance_question.yml must ask for a source -- a classification claim without one "
        "is exactly what CONTRIBUTING.md's governance section refuses"
    )


def test_config_routes_security_reports_to_security_md_not_public_issues():
    """The blank-issue chooser must point vulnerability reports at the private channel.

    SECURITY.md describes the channel; config.yml is the signpost someone actually meets
    first when the picker opens.
    """
    path = _TEMPLATES / "config.yml"
    assert path.is_file(), ".github/ISSUE_TEMPLATE/config.yml does not exist"
    parsed = yaml.safe_load(path.read_text())
    text = str(parsed)
    assert "SECURITY.md" in text, (
        "config.yml must route security reports to SECURITY.md, not to a public issue"
    )


def test_the_pr_template_carries_gates_tests_first_and_the_guarded_kinds():
    """Gates passed, tests-first evidence, and the rail/classification disclosure.

    The checkbox is the load-bearing part: a PR that touches a rail or a default
    classification needs source-and-discussion review (CONTRIBUTING.md's governance
    section), and nothing makes that visible at review time unless the author declared it
    at submit time.
    """
    path = _ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
    assert path.is_file(), ".github/PULL_REQUEST_TEMPLATE.md does not exist"
    text = path.read_text().lower()
    for gate in ("ruff", "mypy", "pytest"):
        assert gate in text, f"the PR template must list the {gate} gate"
    assert "test" in text and ("first" in text or "failing" in text), (
        "the PR template must ask for tests-first evidence (a failing test seen failing)"
    )
    assert "rail" in text and "classification" in text, (
        "the PR template must carry the checkbox for touching a rail or a default classification"
    )


def test_feature_request_template_exists_as_a_form():
    """A basic sanity check: the form exists and parses as one."""
    _form("feature_request.yml")
