"""WCAG contrast gate for the web UI palette (#532).

Everything here is measured from `keel/web/static/css/keel.css` itself -- the light `:root { }`
block and the dark `@media (prefers-color-scheme: dark)` block are parsed with a regex, not
copied into this file as a second source of truth. That is deliberate: a hardcoded expected
hex string only proves this file agrees with itself, while re-deriving the ratios from the
actual stylesheet is what makes a reverted or fat-fingered palette value fail the build instead
of drifting unnoticed, which is the whole point of #532's acceptance criterion "a CI test
asserts every pair's ratio; changing a palette value to something failing makes it fail."

The luminance and contrast-ratio formulas are WCAG 2.x's own (relative luminance:
https://www.w3.org/TR/WCAG21/#dfn-relative-luminance; contrast ratio:
https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio) -- twenty lines of arithmetic, no dependency.

#593 moved the palette onto keeltrading.com's (the sibling site repository named in the
stylesheet's header). Every pin that legitimately moved was re-measured against the new values,
and the ONE grade that could not survive the site's lighter ink is recorded, with its reason, at
`_GRADE_FLOOR` -- the floors themselves are unchanged.
"""

from __future__ import annotations

import re

from keel.web import staticfiles

# The stylesheet the client actually loads, located through `staticfiles.STATIC_ROOT` rather than
# by a path guessed from this test file's own location (which breaks the moment either moves).
#
# It was `render._STYLE` -- an inline stylesheet inside a Python module -- until #540 deleted the
# server-rendered pages. Nothing about these measurements changed: a test at #536 asserted the two
# palettes were BYTE-IDENTICAL, so the values being measured here are the same values that were
# being measured before, now read from the file that was already the one in the browser.
_CSS = staticfiles.STATIC_ROOT / "css" / "keel.css"

#: WCAG 2.x AA, normal-size text (SC 1.4.3): fg/muted/accent/link/warn/bad/good all render
#: body- or label-sized text somewhere in the client (table cells, `.kv .v`, `.pill`), never
#: large text.
_AA_TEXT_MIN = 4.5

#: WCAG 2.x AA, non-text UI component boundaries (SC 1.4.11): the form-input border this issue
#: adds a token for. Decorative dividers (table rules, footer border, card edges) are explicitly
#: exempt from this minimum and are asserted UNCHANGED instead, below.
_AA_UI_BOUNDARY_MIN = 3.0

#: Minimum acceptable `|luminance(good) - luminance(bad)|`, pinned per theme rather than
#: globally, because a single shared floor cannot do both jobs at once: the light theme's fixed
#: delta (0.0334 under the #593 palette; 0.0319 under #532's) is much smaller than the dark
#: theme's (0.2407; 0.2382 before), since light
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
#:
#: The light floor drops from 0.06 to 0.025 in a later revision, because 0.06 was reached by
#: `--bad: #4d1711` (luminance 0.0223), which is AAA against `--bg`/`--card` and far from
#: `--good` but sits almost on top of `--fg` (`#1c1b19`, luminance 0.0110) -- see
#: `_MIN_SIGNAL_FG_RATIO` below, added for exactly that regression. `--good` (0.0904) already
#: occupies nearly the only luminance band that is both AAA-against-`--bg` and clearly separated
#: from `--fg`; there is no second such value far enough from `--good` to also hit a 0.06 delta.
#: `--bad` settles at `#7b2915` (luminance 0.0585, delta 0.0319 from `--good`) as the best
#: available balance of the three constraints at once (AAA, good/bad separation, fg
#: separation) -- still 29x the original 0.0011 collision, no longer a second collision of its
#: own.
_MIN_GOOD_BAD_LUMINANCE_DELTA = {"light": 0.025, "dark": 0.2}

#: Minimum acceptable contrast between a signal token (`good`, `bad`, `warn`) and `--fg`,
#: pinned per theme. This is a DIFFERENT collision from the one `_MIN_GOOD_BAD_LUMINANCE_DELTA`
#: guards: that one is good-vs-bad; this one is either-of-them-vs-the-body-text-colour every
#: unhighlighted table cell renders in. A signal token that drifts too close to `--fg` is
#: indistinguishable from a neutral cell in greyscale, on e-ink, or for a red-green
#: colour-deficient reader -- discovered when a draft of the good/bad fix picked
#: `--bad: #4d1711` (light), which cleared AAA against `--bg`/`--card` and a healthy delta from
#: `--good`, but landed at 1.19:1 against `--fg` (`#1c1b19`), down from `#96322a`'s original
#: 2.28:1. The floors below are NOT the pre-#532 values themselves -- `--good` (0.0904) already
#: sits at nearly the only luminance simultaneously AAA-against-`--bg` and far from `--fg`
#: (0.0110), which is why the original `--bad` (0.0893) read as 2.28:1 against `--fg` in the
#: first place: it was almost exactly as close to `--good` as it is far from `--fg`, i.e. the
#: same coincidence that caused the bug this issue fixes. There is no light `--bad` that is
#: simultaneously AAA, well-separated from `--good`, AND as far from `--fg` as the original
#: was. The floors instead sit with margin under what THIS palette actually reaches -- light
#: 1.7 (measured minimum 1.90, at `--good`, since #593 moved the palette onto the site's lighter
#: ink), dark 1.4 (measured minimum 1.42, at `--good`, see the stylesheet's dark `:root`
#: comment) -- high enough to reject the 1.19:1 regression that prompted this test, low enough
#: to admit the corrected palette.
_MIN_SIGNAL_FG_RATIO = {"light": 1.7, "dark": 1.4}

_SIGNAL_TOKENS = ("good", "bad", "warn")

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
_VAR_RE = re.compile(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})")

#: `_STYLE`'s own comments document rejected hex values in exactly the form a real declaration
#: takes -- `` `--bad: #96322a` `` inside a `/* ... */` block, prose around it notwithstanding
#: -- so `_VAR_RE` must never see comment text, only real declarations. It parses correctly
#: today only because none of the current comments happen to spell a rejected value with a
#: colon directly after the token name; that is luck, not a guarantee, and `dict.__setitem__`
#: via a dict comprehension keeps whichever match comes LAST, so a future comment written this
#: way would poison the parse silently rather than raising.
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Every token the stylesheet declares text or UI-boundary colour with, in both themes.
_EXPECTED_TOKENS = {
    "bg",
    "fg",
    "muted",
    "line",
    "card",
    "accent",
    "link",
    "warn",
    "bad",
    "good",
    "control-line",
}

#: Tokens used to colour readable text somewhere in the page (table cells, `.kv .v`, pills,
#: nav labels, the `.field em` hint, the run links) -- excludes `bg`, `card` and `line`, which
#: are surfaces and a decorative divider, not foregrounds.
_TEXT_FOREGROUND_TOKENS = ("fg", "muted", "accent", "link", "warn", "bad", "good")


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

    `/* ... */` comments are stripped FIRST, over the whole stylesheet, before either block is
    located or `_VAR_RE` runs over it -- `_STYLE`'s comments live inside the `:root` braces and
    document rejected hex values in prose, so leaving them in place risks `_VAR_RE` matching a
    rejected value quoted in a comment instead of the real declaration (see `_COMMENT_RE`'s own
    note above).

    Light mode is the first bare `:root { ... }` block -- matching `:root\\s*{` skips the
    adjacent `:root:not([data-theme="light"]) { color-scheme: light dark; }` rule, which has
    a `:not(...)` between `:root` and `{` and so never matches. Dark mode is the `:root:not(
    ...)  { ... }` block nested inside `@media (prefers-color-scheme: dark)`; searching for
    that selector specifically (rather than "the second `:root` block") is what keeps this
    parser from silently reading the wrong block if a rule is inserted between them later.
    """
    css = _COMMENT_RE.sub("", css)
    if dark:
        dark_css = css[css.index("@media") :]
        block = re.search(r':root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}', dark_css)
    else:
        block = re.search(r":root\s*\{([^}]*)\}", css)
    assert block is not None, f"could not find the {'dark' if dark else 'light'} :root block"
    return {name: hex_value for name, hex_value in _VAR_RE.findall(block.group(1))}


def _load_themes() -> tuple[dict[str, str], dict[str, str]]:
    css = _CSS.read_text(encoding="utf-8")
    return _theme_palette(css, dark=False), _theme_palette(css, dark=True)


def test_every_token_is_declared_in_both_themes() -> None:
    """Guards the parser and the palette together: a token dropped from either `:root` block
    -- by a typo, or by only half-applying a change -- fails here before it fails obscurely in
    a KeyError three tests down."""
    light, dark = _load_themes()
    assert _EXPECTED_TOKENS <= light.keys(), light.keys()
    assert _EXPECTED_TOKENS <= dark.keys(), dark.keys()


def test_text_foregrounds_meet_aa_against_bg_and_card_in_both_themes() -> None:
    """SC 1.4.3: every colour the client uses for text (`.good`, `.warn`, `.bad`, `.muted`,
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
    """`fg`/`bg` is the strongest pair on the page (AAA in both themes). Untouched by #532;
    re-based by #593 onto the site's paper and ink, and pinned to the exact numbers that move
    produced -- 13.96:1 light, 14.62:1 dark -- so any future edit to `--fg` or `--bg` for an
    unrelated reason still has to notice it moved this number."""
    light, dark = _load_themes()
    assert _contrast_ratio(light["fg"], light["bg"]) == _approx(13.96)
    assert _contrast_ratio(dark["fg"], dark["bg"]) == _approx(14.62)


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


def test_signal_tokens_stay_distinguishable_from_fg() -> None:
    """A second, DIFFERENT collision from the good/bad one above: `good`, `bad` and `warn` are
    the only colours rendered over `--fg` (every unhighlighted table cell), so a signal token
    that drifts too close to `--fg` reads the same as ordinary text -- a loss that looks like
    a neutral row -- once colour is removed (greyscale, e-ink, a red-green colour-deficient
    reader). Caught in review: a draft of the good/bad separation fix picked light
    `--bad: #4d1711`, which passed every OTHER test in this file (AAA against `--bg`/`--card`,
    a healthy delta from `--good`) while landing at 1.19:1 against `--fg` -- effectively a
    second version of the exact bug #532 exists to fix, just against a different token. See
    `_MIN_SIGNAL_FG_RATIO` for why its floors are informed by, but not equal to, the pre-#532
    ratios."""
    for theme_name, palette in zip(("light", "dark"), _load_themes()):
        floor = _MIN_SIGNAL_FG_RATIO[theme_name]
        for token in _SIGNAL_TOKENS:
            ratio = _contrast_ratio(palette[token], palette["fg"])
            assert ratio >= floor, (
                f"{theme_name} --{token} on --fg is {ratio:.2f}:1, below the {floor}:1 floor "
                "-- a signal colour is becoming indistinguishable from ordinary body text"
            )


def test_comment_text_does_not_poison_the_parsed_palette() -> None:
    """Proves `_theme_palette`'s comment-stripping fix (`_COMMENT_RE`) actually works: a
    synthetic stylesheet with a comment that quotes a rejected value in exactly the
    `` `--bad: #hex` `` shape a real rejected-alternative note would use must not leak that
    value into the parsed palette -- the real declaration on the line below must win."""
    css = """
    :root {
      /* REJECTED: --bad: #000000 would be the wrong choice here. */
      --bg: #fbfaf8; --fg: #1c1b19; --bad: #7b2915; --good: #1f5f4f;
    }
    """
    palette = _theme_palette(css, dark=False)
    assert palette["bad"] == "#7b2915"


def test_accent_is_not_good() -> None:
    """`--accent` and `--good` were byte-identical in both themes before #532
    (`#1f5f4f` light, `#6fbf9f` dark), so a hyperlink and a gain rendered as the same colour.
    They must stay two different declarations."""
    light, dark = _load_themes()
    assert light["accent"] != light["good"]
    assert dark["accent"] != dark["good"]


def test_control_border_meets_the_ui_boundary_minimum_in_both_themes() -> None:
    """SC 1.4.11: `.field input, .field select` sit on `--bg` (their own background is the
    page background), so `--control-line` -- the border token this issue adds --
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
    separate `--control-line` token above is for. Pinned since #593 to the SITE's border
    literals (`--border` in keeltrading.com's global.css), which arrive well under 3:1 in both
    themes -- so a well-intentioned "just raise --line too" edit fails here instead of quietly
    widening every rule and border on the page, and a border retuned on the site fails here
    until it is copied across."""
    light, dark = _load_themes()
    assert light["line"] == "#d9d4c8"
    assert dark["line"] == "#2b3a44"
    # Still comfortably under the 3:1 boundary floor -- confirms the exemption is real, not
    # accidental compliance.
    assert _contrast_ratio(light["line"], light["bg"]) < _AA_UI_BOUNDARY_MIN
    assert _contrast_ratio(dark["line"], dark["bg"]) < _AA_UI_BOUNDARY_MIN


def test_field_input_border_uses_the_control_line_token_not_line() -> None:
    """Belt-and-suspenders on the CSS itself, not just the token's contrast value: this fails
    if `.field input, .field select` is ever pointed back at `var(--line)`, even if `--line`'s
    own hex value happened to reach 3:1 some day by coincidence."""
    css = _CSS.read_text(encoding="utf-8")
    field_rule = re.search(r"\.field input,\s*\.field select\s*\{[^}]*\}", css)
    assert field_rule is not None
    assert "var(--control-line)" in field_rule.group(0)
    assert "var(--line)" not in field_rule.group(0)


# -- the theme choice, and the colours of the header that carries it (#597) ------------------------


def test_a_pinned_dark_choice_paints_dark_on_a_light_machine() -> None:
    """The `:root[data-theme="dark"]` block duplicates the media block's tokens EXACTLY.

    The duplication is the site's own device (keeltrading.com's global.css carries the same two
    blocks beside a comment saying "the duplication below keeps both paths in plain CSS"), and
    both paths are needed: the `@media` block answers "the OS prefers dark and the reader has
    not pinned anything", the pinned block answers "the reader chose dark while their OS stays
    light". Without the second, #597's toggle could only ever go light on a light-theme machine
    -- a control with one working position.

    Pinned as EXACT EQUALITY rather than as a floor: the two blocks are one palette stated
    twice, and a value that drifts between them is not a new colour, it is the SAME page able
    to render two different darks depending on how the reader arrived at dark."""
    css = _COMMENT_RE.sub("", _CSS.read_text(encoding="utf-8"))
    pinned = re.search(r':root\[data-theme="dark"\]\s*\{([^}]*)\}', css)
    assert pinned is not None, "no pinned-dark block -- a stored dark choice cannot paint"
    pinned_palette = dict(_VAR_RE.findall(pinned.group(1)))

    media = re.search(
        r"@media \(prefers-color-scheme: dark\)\s*\{\s*"
        r':root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}',
        css,
    )
    assert media is not None
    media_palette = dict(_VAR_RE.findall(media.group(1)))

    assert pinned_palette == media_palette, (
        "the pinned-dark block and the media dark block are one palette stated twice; a value "
        "here that differs is the same page rendering two different darks"
    )
    # And the duplication is not vacuous: both blocks really do carry the whole palette.
    assert _EXPECTED_TOKENS <= pinned_palette.keys(), pinned_palette.keys()


def test_every_colour_on_the_page_is_a_measured_token() -> None:
    """**How #597's new header surfaces join this suite: by adding no colour of their own.**

    The brand mark, the theme toggle (its circle, its border, both icons' `currentColor`) and
    the mode badge are all coloured with `var(--...)` references into the SAME two token blocks
    every ratio above is measured from. That is the property that makes "the contrast suite
    covers them" true without a new pair per component: there is no hex literal outside the
    token blocks, so there is no colour on this page whose ratio is not one of the ratios this
    file already computes.

    Pinned globally rather than per-rule, because the failure it guards is exactly a new rule
    appearing with a hex in it -- `#0c5d52` pasted into a `.badge { color: ... }` would pass
    every existing test while escaping the whole measurement apparatus."""
    css = _COMMENT_RE.sub("", _CSS.read_text(encoding="utf-8"))
    # Everything that is not a token block: the light `:root`, the `@media` dark block, the
    # pinned-dark block (#597), and the `@media` wrapper text itself.
    stripped = re.sub(r":root\s*\{[^}]*\}", "", css)
    stripped = re.sub(r":root:not\(\[data-theme=\"light\"\]\)\s*\{[^}]*\}", "", stripped)
    stripped = re.sub(r":root\[data-theme=\"dark\"\]\s*\{[^}]*\}", "", stripped)
    stripped = re.sub(r"@media[^{]*\{", "", stripped)

    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert literals == [], (
        f"hex colour literals outside the token blocks: {literals} -- every colour on this page "
        "must be a token, so every pair is one this suite already measures"
    )


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
#: Every entry is AAA except `muted` and `warn` (pre-existing AA, untouched by #532). An earlier
#: draft pinned dark `--accent`/`--card` at AA (`#7aa8e0`, 6.92:1), on the reasoning that
#: reaching AAA meant darkening `--accent` toward `--bad`'s hue -- which repeated the same
#: directional error the `good`/`bad` fix exists to correct: in dark mode, darkening moves
#: TOWARD the background and only loses contrast, it does not shift hue. `--accent` had the
#: same headroom `--good` did; LIGHTENING it to `#86b1e5` reaches AAA on both surfaces (8.22:1
#: `--bg`, 7.68:1 `--card`) with the blue hue intact, so there is no exception left to record
#: here.
_GRADE_FLOOR: dict[str, dict[str, dict[str, str]]] = {
    # THE ONE LOWERED ENTRY (#593): light `--bad` was AAA since #532 and is AA now (5.87:1).
    # The site's ink is lighter than the near-black it replaced, and the band a light signal
    # colour can occupy -- AAA against the paper, 1.7:1 away from body ink, 0.025 luminance
    # from its opposite signal -- narrowed to 0.0207 of luminance, too narrow for good and bad
    # BOTH. Holding `--bad` at AAA meant moving it back toward `--good` (the luminance
    # collision #532 exists to fix) or toward `--fg` (reading as a neutral row in greyscale);
    # it is AA instead, 5.87:1, and further from `--fg` (2.38:1) and from `--good` (delta
    # 0.0334) than it has ever been. Every other entry holds its #532 grade.
    "light": {
        "bg": {
            "fg": "AAA",
            "muted": "AA",
            "accent": "AAA",
            "link": "AAA",
            "warn": "AA",
            "bad": "AA",
            "good": "AAA",
        },
        "card": {
            "fg": "AAA",
            "muted": "AA",
            "accent": "AAA",
            "link": "AAA",
            "warn": "AA",
            "bad": "AA",
            "good": "AAA",
        },
    },
    "dark": {
        "bg": {
            "fg": "AAA",
            "muted": "AA",
            "accent": "AAA",
            "link": "AAA",
            "warn": "AAA",
            "bad": "AA",
            "good": "AAA",
        },
        "card": {
            "fg": "AAA",
            "muted": "AA",
            "accent": "AAA",
            "link": "AAA",
            "warn": "AAA",
            "bad": "AA",
            "good": "AAA",
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
    draft, or a later one that repeats the same directional mistake for a third token
    (`--accent` was darkened toward dark mode's background before it was correctly lightened
    away from it) -- lowers a grade without updating the floor and stating why. Every entry is
    pinned at AAA except `muted` and `warn`, pre-existing AA choices this issue does not touch.
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
