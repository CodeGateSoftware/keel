"""The deployment identity card: "which deployment is this" without a pointer (#755).

`modeBadge` puts the mode WORD in the badge and the identifying `--db`/`--config` pair in a
`title` attribute. `title` renders as a hover tooltip, and there is no hover on a touch device --
so on the exact device Phase B (#648, #656) is built for, the half of the badge that distinguishes
two paper deployments, or paper from live when both read `confirm`, is unreachable.

**This adds no way to CHANGE anything, and the tests below are mostly about that.** Switching mode
or profile is a config-file edit plus a typed terminal ceremony; `render.js`'s own chip docstring
refuses the control and `test_neither_the_chip_nor_the_banner_builds_anything_clickable` pins the
refusal. A disclosure that reveals read-only facts is not that control, and the tests here hold it
to the same standard rather than exempting it.

A browser cannot run here, so these are source assertions -- see `test_client_assets`'s docstring
for the standing argument, and note the same comment-stripping discipline: a scan that a docstring
can satisfy is a scan that proves nothing.
"""

from __future__ import annotations

import re

from keel.web import staticfiles
from tests.web.test_client_assets import _INTERACTIVE_TOKENS, _function_body, _source

_RENDER = _source("render.js")
_MAIN = (staticfiles.STATIC_ROOT / "js" / "main.js").read_text()
_INDEX = (staticfiles.STATIC_ROOT / "index.html").read_text()
_CSS = (staticfiles.STATIC_ROOT / "css" / "keel.css").read_text()
_CSS_RULES = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)


def test_the_card_exists_and_is_exported() -> None:
    assert "export function deploymentCard(" in _RENDER


def test_the_shell_actually_calls_it() -> None:
    """An uncalled renderer still READS the keys it reads, so the config-parity scan passes over
    one that nothing mounts. #704's lesson, applied on the way in rather than after a review."""
    assert "deploymentCard" in _MAIN, "render.js exports it and the shell never mounts it"
    assert _MAIN.count("deploymentCard(") >= 1


def test_the_card_names_every_fact_that_identifies_a_deployment() -> None:
    """The mode word alone does not identify anything: two paper deployments both say `paper`,
    and a live sandbox and a paper-equities profile can both say `confirm`. The pair that
    distinguishes them is the point of the card."""
    body = _function_body(_RENDER, "deploymentCard")
    for key in ("config.db_path", "config.config_path", "config.mode", "config.profile"):
        assert key in body, f"the card does not name {key}"


def test_the_card_builds_nothing_that_could_change_anything() -> None:
    """Held to the SAME list as the chip and the banner, deliberately.

    A card that reveals facts is the whole request; a card that grew a "switch to live" affordance
    would be the growth funnel `sessionChip`'s docstring refuses, arriving through a door marked
    accessibility.
    """
    body = _function_body(_RENDER, "deploymentCard")
    for forbidden in _INTERACTIVE_TOKENS:
        assert forbidden not in body, f"deploymentCard builds something interactive: {forbidden}"


def test_the_disclosure_is_native_and_needs_no_handler() -> None:
    """`<details>`/`<summary>` is keyboard-operable, touch-operable and announced, with no
    listener to bind and no button to build -- which is what lets this pass the test above.

    `venueCard` already uses the same element for the same reason, so this is the established
    pattern here rather than a new one."""
    assert 'id="deployment"' in _INDEX
    assert "<details" in _INDEX, "the card is not a native disclosure"
    assert "<summary" in _INDEX


def test_the_badge_still_owns_the_mode_word_and_keeps_its_tooltip() -> None:
    """#755 asks for the touch path and says not to remove the pointer one: `title` works for
    keyboard and desktop and costs nothing to keep."""
    badge = _function_body(_RENDER, "modeBadge")
    assert "node.title" in badge, "the hover tooltip was removed rather than supplemented"
    assert "config.mode" in badge or "config && typeof config.mode" in badge


def test_the_card_survives_navigation_because_it_sits_outside_the_view() -> None:
    """`#view` is replaced wholesale on every route change. The same argument the chip and the
    banner already answer to."""
    view_at = _INDEX.index('id="view"')
    assert _INDEX.index('id="deployment"') < view_at, "the card is inside #view and gets repainted"


def test_the_card_says_that_switching_is_not_available_here() -> None:
    """The one sentence that turns an absence into a statement. Without it the card reads as an
    unfinished settings panel; with it, the refusal is legible where someone would look for the
    control."""
    body = _function_body(_RENDER, "deploymentCard")
    assert "SWITCHING_NOTE" in body, "the card places no such sentence"

    # And the constant it places actually says it. Asserting the reference alone would pass over
    # a `SWITCHING_NOTE` that had been emptied or reworded into an invitation -- the same gap
    # `test_a_click_is_acknowledged_without_claiming_arrival` closes for `markPending`.
    note = re.search(r"const SWITCHING_NOTE =(.+?);", _RENDER, flags=re.S)
    assert note, "SWITCHING_NOTE is not defined in render.js"
    words = note.group(1)
    assert "terminal" in words and "cannot change" in words, words
    for invitation in ("upgrade", "Go live", "switch to live", "Open Live"):
        assert invitation.lower() not in words.lower(), (
            "the refusal sentence has become an invitation: " + invitation
        )


def test_the_card_is_styled() -> None:
    assert "#deployment" in _CSS_RULES, "nothing styles the card"
