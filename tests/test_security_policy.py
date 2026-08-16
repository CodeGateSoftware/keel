"""SECURITY.md: a private channel must exist before strangers read the source.

keel holds exchange API credentials and places live orders. The day the repo is discoverable,
someone will eventually find a way to make it misbehave, and the only reporting routes today
are a public issue -- which discloses to everyone simultaneously -- or silence. A
vulnerability with no private channel is one that either burns the reporter's good will or
burns every operator at once; both end with the flaw living unpatched in the code that moves
money.

This pins #279's acceptance into the repo itself, for the same reason the licence and
governance tests exist: the failure mode is a file that LOOKS configured and is not. A
SECURITY.md that names no working channel, promises a response a solo maintainer cannot
deliver, or stays silent on what counts as a vulnerability here is worse than none, because
it is trusted.

The one part of #279 pytest cannot pin is the GitHub-side setting -- enabling private
vulnerability reporting in the repo's settings is a mutation outside the tree, and it is
verified against the live repo (`gh api .../security_and_analysis`) at issue-close time, not
in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _policy() -> str:
    path = _ROOT / "SECURITY.md"
    assert path.is_file(), (
        "SECURITY.md does not exist at the repo root -- there is no stated route for reporting "
        "a vulnerability privately (#279)"
    )
    return path.read_text()


def test_the_policy_names_a_private_channel():
    """A reporter's first question is 'where?', and the answer must already be written down.

    The channel is GitHub's private vulnerability reporting (the *Report a vulnerability*
    button under the Security tab), which had to be ENABLED in the repo's settings for the
    sentence to be true -- the file alone gives no channel.
    """
    text = _policy().lower()
    assert "report a vulnerability" in text or "private vulnerability reporting" in text, (
        "SECURITY.md must point at a real private channel (GitHub private vulnerability "
        "reporting / the 'Report a vulnerability' button), not just an email-shaped promise"
    )


def test_the_policy_states_a_response_expectation_a_solo_maintainer_can_meet():
    """An acknowledgement window, stated in days, that matches one maintainer's reality.

    'We will respond promptly' is not a commitment; a number is. The test accepts any stated
    day-count precisely because the honest number is the maintainer's to size -- what it pins
    is that a number was committed to at all.
    """
    text = _policy().lower()
    assert re.search(r"within \d+ (business )?days", text), (
        "SECURITY.md must state a concrete acknowledgement window ('within N days') -- "
        "honestly sized for a solo maintainer, but concrete"
    )


def test_a_bypassable_rail_is_named_as_a_security_issue():
    """A rail that can be bypassed is a security issue here, not merely a bug.

    The rails ARE the product: an allowlist that can be talked around or a kill-switch that
    can be raced past is the worst class of defect this codebase can carry, and a reporter
    who thinks 'it is just a logic quirk, not security' will file it as a regular public bug.
    """
    text = _policy().lower()
    assert "rail" in text and "bypass" in text, (
        "SECURITY.md must say explicitly that a rail that can be bypassed is a security issue"
    )


def test_out_of_scope_reports_are_named():
    """What will NOT be treated as a vulnerability, so the channel stays usable.

    Strategy performance, market losses, and a user's own key handling are the three floods
    a trading-repo security channel receives; each belongs in a public issue or nowhere at
    all, and a policy that does not say so trains reporters to ping the private channel with
    them anyway.
    """
    text = _policy().lower()
    assert "strategy" in text and "market" in text, (
        "SECURITY.md must name strategy performance and market losses as out of scope"
    )
    assert "key" in text, "SECURITY.md must name a user's own key handling as out of scope"
