"""A documented bootstrap that creates a database must also name the step that arms it (#694).

`keel migrate` creates schema and **never seeds** — correct, and documented. So a fresh database
has no `kill_switch` row, `get_state("kill_switch", default=True)` fails closed, and the profile
skips every cycle until someone runs `keel resume`.

Followed exactly on 2026-09-02, the equities bootstrap produced five promoted rules, 1249 cached
daily bars per symbol, and three consecutive `skipped: kill_switch` cycles. Nothing was broken;
the page was incomplete, and the failure looks like a broken profile rather than an unset flag.

The arming step is deliberately NOT part of the copy-pasteable block: the runbook's own warning
is that releasing a halt must stay a human gesture and must never be scriptable. So this checks
that the step is *named* in the section, not that it sits inside the code fence.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_RUNBOOK = _ROOT / "docs/operator-runbook.md"

#: Sections that document standing up a profile from nothing. Keyed by their heading so a
#: failure names the section an operator would have been reading.
_BOOTSTRAP_SECTIONS = (
    "## The hourly evidence profile (paper-hourly)",
    "## The equities paper profile (paper-equities)",
)


def _section(heading: str) -> str:
    text = _RUNBOOK.read_text(encoding="utf-8")
    start = text.index(heading)
    following = [
        m.start()
        for m in re.finditer(r"(?m)^## ", text)
        if m.start() > start
    ]
    return text[start : following[0] if following else len(text)]


def test_the_bootstrap_sections_exist() -> None:
    """A guard on the guard: a renamed heading would make every assertion below vacuous."""
    for heading in _BOOTSTRAP_SECTIONS:
        assert heading in _RUNBOOK.read_text(encoding="utf-8"), f"missing section: {heading}"


def test_every_bootstrap_that_migrates_a_database_names_the_arming_step() -> None:
    """`keel migrate` never seeds, so a bootstrapped profile is halted until `keel resume`.

    Checked per section rather than repository-wide: a mention of `resume` three thousand lines
    away is not something the operator following these steps will see.
    """
    missing = []
    for heading in _BOOTSTRAP_SECTIONS:
        body = _section(heading)
        if "keel migrate" not in body:
            continue  # this section does not create a database; nothing to arm
        # `keel resume`, not the WORD "resume". The hourly section passed the first version of
        # this test on the prose "must already be in force when live BUYs resume" -- a match
        # that has nothing to do with arming a profile, in a section that had the same gap.
        if "keel resume" not in body:
            missing.append(heading)

    assert not missing, (
        "these bootstrap sections create a database with `keel migrate` and never name the step "
        "that arms it, so following them exactly yields a profile that skips every cycle:\n  "
        + "\n  ".join(missing)
    )


def test_the_arming_step_is_not_inside_a_copy_pasteable_block() -> None:
    """The runbook's own constraint, and the reason this is a doc fix rather than a script fix.

    > The typed confirmation is deliberately human … so that a scheduled job can never release a
    > §65.4 halt. Do not script this command and do not pipe a `yes` into it.

    Putting `keel resume` inside the fenced block above it would make it look like one more line
    to paste — which is exactly what that warning forbids.
    """
    for heading in _BOOTSTRAP_SECTIONS:
        body = _section(heading)
        if "keel migrate" not in body or "resume" not in body:
            continue
        fenced = "".join(re.findall(r"```.*?```", body, re.S))
        assert "keel resume" not in fenced and "resume\n" not in fenced, (
            f"{heading}: the arming step sits inside a copy-pasteable block. The runbook "
            "requires it stay a deliberate human gesture, not one more line to paste."
        )
