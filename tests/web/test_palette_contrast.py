"""WCAG contrast gate for the web UI palette (#532).

Everything here is measured from `keel/web/render.py:_STYLE` itself -- the light `:root { }`
block and the dark `@media (prefers-color-scheme: dark)` block are parsed with a regex, not
copied into this file as a second source of truth. That is deliberate: a hardcoded expected
hex string only proves this file agrees with itself, while re-deriving the ratios from the
actual stylesheet is what makes a reverted or fat-fingered palette value fail the build instead
of drifting unnoticed, which is the whole point of #532's acceptance criterion "a CI test
asserts every pair's ratio; changing a palette value to something failing makes it fail."

The luminance and contrast-ratio formulas are WCAG 2.x's own (relative luminance:
https://www.w3.org/TR/WCAG21/#dfn-relative-luminance; contrast ratio:
https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio) -- twenty lines of arithmetic, no dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

from keel.web import render

# `render.RENDER_PY` doesn't exist -- reading `render.__file__` keeps this test tied to
# whatever module actually shipped the stylesheet, rather than a path guessed from this
# test file's own location (which breaks the moment either file moves).
_RENDER_PY = Path(render.__file__)

#: WCAG 2.x AA, normal-size text (SC 1.4.3): fg/muted/accent/warn/bad/good all render body-
#: or label-sized text somewhere in render.py (table cells, `.kv .v`, `.pill`), never large text.
_AA_TEXT_MIN = 4.5

#: WCAG 2.x AA, non-text UI component boundaries (SC 1.4.11): the form-input border this issue
#: adds a token for. Decorative dividers (table rules, footer border, card edges) are explicitly
#: exempt from this minimum and are asserted UNCHANGED instead, below.
_AA_UI_BOUNDARY_MIN = 3.0

#: Minimum acceptable `|luminance(good) - luminance(bad)|`, pinned per theme rather than
#: globally, because a single shared floor cannot do both jobs at once: the light theme's fixed
#: delta (measured 0.0681) is much smaller than the dark theme's (measured 0.2382), since light
#: mode keeps both colours near the dark end of the scale to hold AAA against a near-white
#: background, while dark mode has the whole upper half of the scale to spread them across.
#: A shared floor high enough to catch a regressed DARK pair would reject a compliant LIGHT
#: pair; a shared floor low enough to admit the light pair would not catch a reverted dark pair
#: (its original delta, 0.1234, would still pass a lenient shared floor). The values below sit
#: with headroom under each theme's actual measured delta and, checked against the ORIGINAL
#: palette this issue reports (light delta 0.0011, dark delta 0.1234), both floors reject it --
#: which is the property this pin exists to guarantee: reverting #532 fails this test. Note
#: what this floor does NOT guarantee: it only measures separation, not direction, which is why
#: `test_no_text_pair_grade_drops_below_its_pinned_floor` exists separately below -- an earlier
#: draft of this fix hit 0.0663/0.1984, clearing tighter floors than these, while moving `good`
#: and `bad` the wrong way and losing three AAA grades in the process.
_MIN_GOOD_BAD_LUMINANCE_DELTA = {"light": 0.06, "dark": 0.2}

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
_VAR_RE = re.compile(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})")

#: Every token render.py's stylesheet declares text or UI-boundary colour with, in both themes.
_EXPECTED_TOKENS = {
    "bg", "fg", "muted", "line", "card", "accent", "warn", "bad", "good", "control-line",
}

#: Tokens used to colour readable text somewhere in the page (table cells, `.kv .v`, pills,
#: nav labels, the `.field em` hint) -- excludes `bg`, `card` and `line`, which are surfaces and
#: a decorative divider, not foregrounds.
_TEXT_FOREGROUND_TOKENS = ("fg", "muted", "accent", "warn", "bad", "good")


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of a `#rrggbb` colour, in [0, 1]."""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def linearize(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    r_lin, g_lin, b_lin = linearize(r), linearize(g), linearize(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG contrast ratio between two colours: (L_lighter + 0.05) / (L_darker + 0.05)."""
    lum_a, lum_b = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _theme_palette(css: str, *, dark: bool) -> dict[str, str]:
    """The `--token: #hex;` declarations for one theme, keyed by token name (no `--`).

    Light mode is the first bare `:root { ... }` block -- matching `:root\\s*{` skips the
    adjacent `:root:not([data-theme="light"]) { color-scheme: light dark; }` rule, which has
    a `:not(...)` between `:root` and `{` and so never matches. Dark mode is the `:root:not(
    ...)  { ... }` block nested inside `@media (prefers-color-scheme: dark)`; searching for
    that selector specifically (rather than "the second `:root` block") is what keeps this
    parser from silently reading the wrong block if a rule is inserted between them later.
    """
    if dark:
        dark_css = css[css.index("@media") :]
        block = re.search(r':root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}', dark_css)
    else:
        block = re.search(r":root\s*\{([^}]*)\}", css)
    assert block is not None, f"could not find the {'dark' if dark else 'light'} :root block"
    return {name: hex_value for name, hex_value in _VAR_RE.findall(block.group(1))}


def _load_themes() -> tuple[dict[str, str], dict[str, str]]:
    css = render._STYLE
    return _theme_palette(css, dark=False), _theme_palette(css, dark=True)


def test_every_token_is_declared_in_both_themes() -> None:
    """Guards the parser and the palette together: a token dropped from either `:root` block
    -- by a typo, or by only half-applying a change -- fails here before it fails obscurely in
    a KeyError three tests down."""
    light, dark = _load_themes()
    assert _EXPECTED_TOKENS <= light.keys(), light.keys()
    assert _EXPECTED_TOKENS <= dark.keys(), dark.keys()


def test_text_foregrounds_meet_aa_against_bg_and_card_in_both_themes() -> None:
    """SC 1.4.3: every colour render.py uses for text (`.good`, `.warn`, `.bad`, `.muted`,
    nav labels, and `--accent` as button text on its own background) must reach 4.5:1 against
    both surfaces text can sit on -- the page (`--bg`) and a card (`--card`)."""
    for theme_name, palette in zip(("light", "dark"), _load_themes()):
        for token in _TEXT_FOREGROUND_TOKENS:
            for surface in ("bg", "card"):
                ratio = _contrast_ratio(palette[token], palette[surface])
                assert ratio >= _AA_TEXT_MIN, (
                    f"{theme_name} --{token} on --{surface} is {ratio:.2f}:1, "
                    f"below the {_AA_TEXT_MIN}:1 AA floor for normal text"
                )


def test_fg_on_bg_contrast_has_not_regressed() -> None:
    """`fg`/`bg` was untouched by #532 and is the strongest pair on the page (AAA in both
    themes already). Pinned to the exact numbers #532 measured and reported --
    16.50:1 light, 15.06:1 dark -- so any future edit to `--fg` or `--bg` for an unrelated
    reason still has to notice it moved this number."""
    light, dark = _load_themes()
    assert _contrast_ratio(light["fg"], light["bg"]) == _approx(16.50)
    assert _contrast_ratio(dark["fg"], dark["bg"]) == _approx(15.06)


def _approx(expected: float, tol: float = 0.01) -> object:
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, int | float) and abs(other - expected) <= tol

        def __repr__(self) -> str:
            return f"~{expected}"

    return _Approx()


def test_good_and_bad_differ_in_luminance_not_only_hue() -> None:
    """The core of #532: light mode's `#1f5f4f` (good) and `#96322a` (bad) had luminances
    0.0904 and 0.0893 -- a delta of 0.0011, a 1.01:1 ratio -- so profit and loss were
    distinguished by hue alone (WCAG 1.4.1). Reject any pair whose luminance separation falls
    back under the measured-and-margined floor in `_MIN_GOOD_BAD_LUMINANCE_DELTA`. This test
    only checks separation, not which colour moved to create it -- see
    `test_no_text_pair_grade_drops_below_its_pinned_floor` for the direction check that
    separation alone cannot express."""
    for theme_name, palette in zip(("light", "dark"), _load_themes()):
        delta = abs(_relative_luminance(palette["good"]) - _relative_luminance(palette["bad"]))
        floor = _MIN_GOOD_BAD_LUMINANCE_DELTA[theme_name]
        assert delta >= floor, (
            f"{theme_name} good/bad luminance delta is {delta:.4f}, below the {floor} floor "
            "-- profit and loss are distinguishable by hue alone again"
        )


def test_accent_is_not_good() -> None:
    """`--accent` and `--good` were byte-identical in both themes before #532
    (`#1f5f4f` light, `#6fbf9f` dark), so a hyperlink and a gain rendered as the same colour.
    They must stay two different declarations."""
    light, dark = _load_themes()
    assert light["accent"] != light["good"]
    assert dark["accent"] != dark["good"]


def test_control_border_meets_the_ui_boundary_minimum_in_both_themes() -> None:
    """SC 1.4.11: `.field input, .field select` sit on `--bg` (their own background is the
    page background, per render.py), so `--control-line` -- the border token this issue adds --
    must reach 3:1 against `--bg` on its own. Before #532 the only boundary was `--line` at
    1.27:1 light / 1.33:1 dark, which is why this is a distinct, higher-contrast token rather
    than a change to `--line` itself."""
    for theme_name, palette in zip(("light", "dark"), _load_themes()):
        ratio = _contrast_ratio(palette["control-line"], palette["bg"])
        assert ratio >= _AA_UI_BOUNDARY_MIN, (
            f"{theme_name} --control-line on --bg is {ratio:.2f}:1, "
            f"below the {_AA_UI_BOUNDARY_MIN}:1 non-text UI boundary floor"
        )


def test_decorative_dividers_stay_exempt_and_unchanged() -> None:
    """`--line` draws table rules, the footer border and card edges -- decorative dividers
    that SC 1.4.11 explicitly exempts, listing "purely decorative" boundaries alongside
    "essentially unaltered" browser-default controls. #532 must not raise `--line` globally to
    manufacture 3:1 for the one place (form inputs) that actually needed it; that is what the
    separate `--control-line` token above is for. Pinned to the exact hex values in place before
    this issue, so a well-intentioned "just raise --line too" edit fails here instead of
    quietly widening every rule and border on the page."""
    light, dark = _load_themes()
    assert light["line"] == "#e3dfd8"
    assert dark["line"] == "#2f2d25"
    # Still comfortably under the 3:1 boundary floor -- confirms the exemption is real, not
    # accidental compliance.
    assert _contrast_ratio(light["line"], light["bg"]) < _AA_UI_BOUNDARY_MIN
    assert _contrast_ratio(dark["line"], dark["bg"]) < _AA_UI_BOUNDARY_MIN


def test_field_input_border_uses_the_control_line_token_not_line() -> None:
    """Belt-and-suspenders on the CSS itself, not just the token's contrast value: this fails
    if `.field input, .field select` is ever pointed back at `var(--line)`, even if `--line`'s
    own hex value happened to reach 3:1 some day by coincidence."""
    css = render._STYLE
    field_rule = re.search(r"\.field input,\s*\.field select\s*\{[^}]*\}", css)
    assert field_rule is not None
    assert "var(--control-line)" in field_rule.group(0)
    assert "var(--line)" not in field_rule.group(0)


#: WCAG 2.x AAA, normal-size text (SC 1.4.6): 7:1. AA (`_AA_TEXT_MIN`, 4.5:1) is the WCAG floor
#: this whole page must clear; AAA is this palette's actual working standard in practice --
#: every text pair reached it before #532 except `--muted` and `--warn`, both pre-existing AA
#: design choices this issue never touched. The distinction matters because "still >= 4.5:1"
#: and "did not lose a grade it already had" are different properties: an early draft of this
#: fix's good/bad separation passed every ratio test above while quietly dropping `--good` and
#: `--accent` from AAA to AA in light mode and `--bad` further into AA in dark mode. Grades,
#: not just ratios, are what `_GRADE_FLOOR` below pins.
_AAA_TEXT_MIN = 7.0

_GRADE_RANK = {"FAIL": 0, "AA": 1, "AAA": 2}


def _grade(ratio: float) -> str:
    if ratio >= _AAA_TEXT_MIN:
        return "AAA"
    if ratio >= _AA_TEXT_MIN:
        return "AA"
    return "FAIL"


#: The WCAG grade every text-foreground/surface pair reaches as of this commit -- the floor a
#: future edit may raise but must not lower without a stated reason and an updated entry here,
#: the same standard CONTRIBUTING.md's documentation section asks of a comment that overturns a
#: prior decision. `muted` and `warn` are pinned at AA because that is what they were before
#: #532 and #532 does not touch them -- this table is not a claim that AA is good enough for
#: the palette in general, only a record of what each pair actually reaches today.
#:
#: The one AA entry #532 itself introduces is dark `--accent` on `--card` (was AAA at 7.83:1,
#: back when `--accent` was byte-identical to `--good`): splitting the token to fix the
#: hyperlink/gain collision cost this one grade, because the pairing that actually renders --
#: button text, `color: var(--card)` on `background: var(--accent)` -- needs `--accent` to read
#: as blue, and darkening it further into AAA-on-card territory pushes its hue toward `--bad`'s
#: red-brown register instead. Accepted deliberately; see the dark `:root` block's comment in
#: render.py for the fuller trade-off.
_GRADE_FLOOR: dict[str, dict[str, dict[str, str]]] = {
    "light": {
        "bg": {
            "fg": "AAA", "muted": "AA", "accent": "AAA", "warn": "AA", "bad": "AAA", "good": "AAA",
        },
        "card": {
            "fg": "AAA", "muted": "AA", "accent": "AAA", "warn": "AA", "bad": "AAA", "good": "AAA",
        },
    },
    "dark": {
        "bg": {
            "fg": "AAA", "muted": "AA", "accent": "AAA", "warn": "AAA", "bad": "AA", "good": "AAA",
        },
        "card": {
            "fg": "AAA", "muted": "AA", "accent": "AA", "warn": "AAA", "bad": "AA", "good": "AAA",
        },
    },
}


def test_no_text_pair_grade_drops_below_its_pinned_floor() -> None:
    """Regression guard for the mistake an earlier draft of #532 made: it fixed the good/bad
    luminance collision by DARKENING light `--good` and LIGHTENING dark `--bad` -- moving both
    colours TOWARD their own background instead of away from it. That passed every ratio test
    in this file, because all of the thresholds above are floors, not exact pins, and the draft
    cleared every one of them -- while quietly dropping three AAA grades to AA (light `--good`
    7.17:1 -> 4.89:1, light `--accent` 7.17:1 -> 6.59:1, dark `--bad` 6.24:1 -> 4.93:1) that a
    "does it still clear 4.5:1" check cannot see, because 4.89 and 7.17 both clear it.

    The rule the accepted fix follows instead: on any background, moving a colour toward the
    background loses contrast while moving it away gains contrast, so when two colours need
    separating, move whichever one has AAA headroom to spend -- separation and contrast both
    improve in the same move, instead of trading one for the other.

    This test pins the GRADE, not just the ratio, for every text pair in `_GRADE_FLOOR` and
    fails if a future edit -- including a well-intentioned separation fix like #532's own first
    draft -- lowers one without updating the floor and stating why, the same way the dark
    `--accent`/`--card` entry above already does for the one grade this issue's accepted fix
    deliberately spends.
    """
    light, dark = _load_themes()
    for theme_name, palette in (("light", light), ("dark", dark)):
        for surface in ("bg", "card"):
            for token, floor in _GRADE_FLOOR[theme_name][surface].items():
                ratio = _contrast_ratio(palette[token], palette[surface])
                actual = _grade(ratio)
                assert _GRADE_RANK[actual] >= _GRADE_RANK[floor], (
                    f"{theme_name} --{token} on --{surface} is {ratio:.2f}:1 ({actual}), "
                    f"below its pinned floor of {floor}"
                )
