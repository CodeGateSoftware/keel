"""The README, rewritten for the stranger the project now wants (#281).

The README was an operator runbook — deploy, upgrade, paper-vs-live — which is the right
document for the author's future self and the wrong one for a newcomer deciding whether to
spend an afternoon here. Phase 7 exists because that stranger is now the primary reader, and
a stranger's four questions are: what is this (led by the compliance engine, which is the
differentiated asset — plenty of people have a trading bot), is it any good (the honest
measured result, stated by us before they find it themselves), whose fiqh am I getting (the
not-a-fatwa-engine boundary), and can I try it in five minutes.

This file pins #281's acceptance so the rewrite cannot quietly regress to a runbook: the
honest result above the fold with a live link to the experiment record, a quickstart built
from commands that actually work, an architecture sketch naming the three load-bearing
places (rails, rules, adapter port), and an ending that routes questions and contributions.
The operator content is asserted to still EXIST — moved into `docs/operator-runbook.md`,
not deleted — and to be GONE from the README, because a README that is half runbook is a
runbook again with extra scrolling.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The current experiment record for the honest result — the file the README's claim must link.
_EXPERIMENT_RECORD = "docs/experiments/2026-08-13-restated-under-a-production-faithful-engine.md"


def _readme() -> str:
    return (_ROOT / "README.md").read_text()


def _readme_first_screen() -> str:
    """Everything before the first `##` section: what a reader sees without scrolling."""
    return _readme().split("\n## ", 1)[0]


def _unwrapped(text: str) -> str:
    """Join markdown wrapping: drop blockquote markers, then collapse all whitespace."""
    return " ".join(re.sub(r"(?m)^\s*>\s?", "", text).split())


def test_the_honest_result_is_stated_above_the_fold():
    """The measured verdict, in the first screen, linking the record that shows it.

    `docs/experiments/` records that no shipped rule family is net-positive at the taker fee
    actually paid — cost is the binding constraint. A visitor who finds that themselves feels
    misled; a visitor who is told upfront reads it as rigour. The claim is pinned TOGETHER
    with its link, and the link is pinned to a file that exists, so the README can never
    cite an experiment record that has been renamed away.
    """
    first = _unwrapped(_readme_first_screen()).lower()
    assert "net-positive" in first or "net positive" in first, (
        "the honest result (no shipped rule net-positive at the taker rate paid) must be "
        "stated in the README's first screen"
    )
    assert _EXPERIMENT_RECORD in _readme(), (
        f"the honest-result claim must link the experiment record ({_EXPERIMENT_RECORD})"
    )
    assert (_ROOT / _EXPERIMENT_RECORD).is_file(), (
        "the experiment record the README cites no longer exists — update the README's link"
    )


def test_the_quickstart_uses_commands_that_exist():
    """A five-minute path whose steps are real: init, fetch (with the honest key caveat), simulate.

    The commands are pinned verbatim, and so is the one prerequisite that is easy to hide:
    market data needs a (free, read-only) Coinbase CDP key — `keel fetch` without one dies
    in an AuthenticationError, and a quickstart that omits that is a quickstart that fails
    at step three.
    """
    text = _unwrapped(_readme())
    # `keel rules seed`, not `keel init`: a fresh CLONE already has the tracked `config.yaml`,
    # and `keel init` refuses to overwrite it -- the quickstart must work from a clone.
    for command in ("uv sync --all-extras --dev", "keel rules seed", "keel fetch", "keel simulate"):
        assert command in text, f"the quickstart must include the command {command!r}"
    assert "CDP" in text, (
        "the quickstart must say that market data needs a free read-only CDP key — "
        "verified: `keel fetch` without one raises AuthenticationError"
    )
    assert "paper" in text.lower(), "the quickstart must show the paper-mode path (no funds)"


def test_the_architecture_sketch_names_the_three_load_bearing_places():
    """Rails, rules, and the adapter port — a newcomer's map of where things live.

    Everything else in the tree is detail; these three are the surfaces a contributor's
    first PR is most likely to touch, and the sketch must name their files.
    """
    text = _readme()
    assert "keel/execution/guards.py" in text, "the sketch must say where the rails live"
    assert "keel/agent.py" in text, "the sketch must say where the rules live"
    assert "packages/keel-broker-" in text, (
        "the sketch must say where a broker adapter plugs in (the packages/keel-broker-* port)"
    )


def test_the_readme_ends_by_routing_questions_and_contributions():
    """Where to ask, and how to contribute — the last thing read should be the next step taken."""
    tail = _unwrapped(_readme().lower()[-2000:])
    assert "discussions" in tail, "the README must end by pointing questions at Discussions"
    assert "contributing" in tail, "the README must end pointing contributions at CONTRIBUTING.md"


def test_operator_content_moved_not_deleted():
    """The runbook sections live on in `docs/operator-runbook.md`, verbatim enough to find.

    Deployment, upgrades, and the paper-vs-live distinctions are operator knowledge the
project is not losing — #281 moves them, it does not delete them. Both halves are pinned:
the runbook GAINS the deployment/upgrade content, and the README LOSES it (below), because
leaving it in both is how the README grows back into a runbook.
    """
    runbook = (_ROOT / "docs" / "operator-runbook.md").read_text()
    assert "gh release download" in runbook, (
        "the wheel-deployment procedure must live in docs/operator-runbook.md now"
    )
    assert "keel versions" in runbook and "PARTIAL INSTALL" in runbook, (
        "the partial-install verification story must live in the operator runbook"
    )
    assert "paper" in runbook.lower() and "live" in runbook.lower(), (
        "the paper-vs-live distinctions must live in the operator runbook"
    )


def test_the_readme_is_no_longer_a_runbook():
    """Operator procedure is gone from the README — newcomer-first means actually first.

    The wheel-install commands and the deployment table are the runbook's densest content;
    their presence here is the regression signal that the README is drifting back."
    """
    text = _readme()
    assert "gh release download" not in text, (
        "deployment procedure belongs in docs/operator-runbook.md, not the README"
    )
    assert "com.keel.paperforward" not in text, (
        "the launchd/deployment table belongs in docs/operator-runbook.md, not the README"
    )
