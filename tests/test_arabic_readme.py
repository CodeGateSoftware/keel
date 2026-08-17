"""The Arabic entry point: README.ar.md, and the switchers that bind it (#290).

A large share of keel's intended audience reads Arabic, and #290's point is that an Arabic
entry point is a signal the project means to serve that audience rather than merely permit
it. The issue is equally clear about the failure mode: the vocabulary of Islamic finance --
riba, qabd, 'ayn, dayn, gharar -- has precise, established Arabic renderings, and a machine
translation will get them subtly wrong, which in this domain is not a style problem but a
trust problem.

So this file pins the Arabic README to the same three facts the English first screen pins
(what keel is, the honest measured result, the not-a-fatwa-engine boundary), pins the
quickstart commands verbatim (commands are language-neutral -- an Arabic reader must be
able to run the identical path), pins the five terms in their established renderings, and
pins the scope statement: the full docs remain English; this is the entry point, not a
promise to translate everything. The boundary's English original is pinned INSIDE the
Arabic document too, so the translation and its source travel together and drift is
detectable from either side. Terminology was authored to the established renderings; the
document itself invites correction via Discussions, which is the honest form of "reviewed
by a native speaker" available to this repo today.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The Arabic entry point (#290): a sibling of README.md, discoverable from it.
_AR = "README.ar.md"

#: The boundary, in Arabic, pinned verbatim -- the same house pattern as
#: test_governance.py's English `_BOUNDARY`, so neither language can drift alone.
_BOUNDARY_AR = "كيل ليس محرّكَ فتاوى؛ إنه محرّكُ إنفاذٍ لحكمٍ شرعيٍّ أنت من يزوّده"

#: The no-scholarly-review stance (#289), stated in Arabic with the same bluntness.
_NO_REVIEW_AR = "لم تَجرِ أيُّ مراجعةٍ علميةٍ شرعيةٍ للأساس الفقهيّ لكيل"

#: The honest result, in Arabic: no shipped rule family is net-positive at the taker fee
#: actually paid. Pinned as a distinctive clause, not a word, so a paraphrase cannot
#: soften it into vagueness.
_HONEST_AR = "لا توجد عائلة قواعدٍ مُصدَّرة تحقّق ربحًا صافيًا"

#: The scope statement's load-bearing half: this page is the entry point, not a promise
#: to translate everything.
_SCOPE_AR = "ليست وعدًا بترجمة كل شيء"

#: The experiment record the honest result links -- the same file test_readme.py pins,
#: asserted here too so the Arabic page can never cite a record renamed away.
_EXPERIMENT_RECORD = "docs/experiments/2026-08-13-restated-under-a-production-faithful-engine.md"

#: The five terms #290 names as the ones a machine translation gets subtly wrong. Each
#: must appear in the Arabic README in its established rendering -- the glossary table is
#: where they are defined, so an Arabic reader meets them defined, not assumed.
_TERMS = ("الربا", "القبض", "العين", "الدَّين", "الغرر")


def _unwrapped(text: str) -> str:
    """Join markdown wrapping: drop blockquote markers, then collapse all whitespace."""
    return " ".join(re.sub(r"(?m)^\s*>\s?", "", text).split())


def _read(relative: str) -> str:
    """A repo file's text; empty until it exists, so a red run FAILS rather than errors."""
    path = _ROOT / relative
    return path.read_text() if path.is_file() else ""


def test_the_arabic_readme_exists_and_declares_rtl():
    """An Arabic reader gets a real page, rendered right-to-left, not a stub.

    GitHub renders the `<div dir="rtl">` wrapper (blank-line delimited so the markdown
    inside still renders); without it the Arabic text is bidi-reordered per line against a
    left-aligned frame, which reads as an afterthought -- the exact signal #290 says not
    to send.
    """
    text = _read(_AR)
    assert '<div dir="rtl">\n\n' in text and "\n\n</div>" in text, (
        f"{_AR} must wrap its content in <div dir=\"rtl\"> -- with blank lines after the "
        "opening tag and before the closing one, or GitHub's HTML-block rule stops "
        "rendering the markdown inside it -- so the page renders right-to-left"
    )


def test_both_readmes_carry_the_language_switcher():
    """The switcher is two-sided: from English to Arabic, and back.

    A link that exists in one direction only is a door that locks behind the reader; the
    Arabic page must be discoverable from README.md's first lines, and must lead back.
    """
    en = _unwrapped(_read("README.md").split("\n## ", 1)[0])
    ar = _unwrapped(_read(_AR).split("\n## ", 1)[0])
    assert "README.ar.md" in en, (
        "README.md's first screen must link README.ar.md -- the Arabic entry point is "
        "discoverable where every reader starts"
    )
    assert "README.md" in ar, (
        f"{_AR}'s first screen must link back to README.md -- the switcher is two-sided"
    )


def test_the_boundary_is_stated_in_arabic_with_its_english_original():
    """The not-a-fatwa-engine boundary, translated precisely, source quoted alongside.

    The Arabic sentence is pinned verbatim, and the English original is pinned INSIDE the
    Arabic document: a translation whose source is not present cannot be checked against
    it, and this boundary is the one sentence the project cannot afford to have drift
    between its two languages.
    """
    text = _unwrapped(_read(_AR))
    assert _BOUNDARY_AR in text, (
        f"{_AR} must state the boundary in Arabic, pinned verbatim: {_BOUNDARY_AR!r}"
    )
    assert (
        "keel is not a fatwa engine. It is an enforcement engine for a ruling you supply."
        in text
    ), (
        f"{_AR} must quote the boundary's English original alongside its translation, so "
        "the two languages' statements are checkable against each other"
    )


def test_the_no_scholarly_review_stance_is_stated_in_arabic():
    """#289's stance crosses the language barrier with its bluntness intact.

    A translation that softens 'no scholarly review has occurred' into something more
    comfortable would be the overstated claim #289 warns about, in different clothes.
    """
    assert _NO_REVIEW_AR in _unwrapped(_read(_AR)), (
        f"{_AR} must state the no-scholarly-review stance plainly: {_NO_REVIEW_AR!r}"
    )
    assert "docs/fiqh-basis.md" in _read(_AR), (
        f"{_AR} must link docs/fiqh-basis.md -- the Arabic reader is owed the same "
        "auditable basis the English reader gets"
    )


def test_the_honest_result_is_stated_in_arabic_and_links_the_record():
    """The measured result crosses over too, with its evidence.

    The honest result is the project's credibility play ('stated by us first'); an Arabic
    entry point that omitted it, or stated it without the record, would read as marketing
    to exactly the audience most likely to check.
    """
    text = _unwrapped(_read(_AR))
    assert _HONEST_AR in text, (
        f"{_AR} must state the honest result in Arabic, pinned verbatim: {_HONEST_AR!r}"
    )
    assert _EXPERIMENT_RECORD in text, (
        f"{_AR} must link the experiment record ({_EXPERIMENT_RECORD}) behind the claim"
    )
    assert (_ROOT / _EXPERIMENT_RECORD).is_file(), (
        "the experiment record the Arabic page cites no longer exists -- update the link"
    )


def test_the_quickstart_commands_are_verbatim():
    """Commands are language-neutral: the Arabic reader runs the identical path.

    The quickstart is pinned to the same commands test_readme.py pins, because a step
    'translated' into a variant that behaves differently is a step that fails for the
    reader who trusted the Arabic page. The CDP key caveat must survive translation too.
    """
    text = _unwrapped(_read(_AR))
    for command in (
        "uv sync --all-extras --dev",
        "keel rules seed",
        "keel fetch",
        "keel simulate --years 1 --skip-within-cap",
    ):
        assert command in text, f"{_AR}'s quickstart must include the command {command!r}"
    assert "CDP" in text, (
        f"{_AR} must keep the CDP key caveat -- `keel fetch` fails without a key, and the "
        "Arabic reader must learn that before step four, not at it"
    )


def test_the_five_terms_appear_in_their_established_renderings():
    """riba, qabd, 'ayn, dayn, gharar -- defined in the document, not assumed.

    #290 names these five as the vocabulary a machine translation gets subtly wrong; the
    Arabic README carries a glossary that gives each its established rendering with the
    English term beside it, so the entry point teaches the vocabulary it uses.
    """
    text = _read(_AR)
    for term in _TERMS:
        assert term in text, (
            f"{_AR} must use and define the established rendering of {term!r} -- the "
            "glossary is where an Arabic reader meets these terms defined"
        )
    assert "| --- |" in text, (
        f"{_AR}'s glossary must be a real GFM table (header row plus '| --- |' separator), "
        "not a drawing that stops rendering as a table"
    )


def test_the_scope_is_stated_entry_point_not_translation_promise():
    """The Arabic page says what it is: a door, not a promise.

    The full docs remain English, and saying so in the page itself is the difference
    between an entry point and an implied commitment the project cannot keep.
    """
    text = _unwrapped(_read(_AR))
    assert _SCOPE_AR in text, (
        f"{_AR} must state its scope: the entry point, {_SCOPE_AR!r}"
    )
    assert "بالإنجليزية" in text, (
        f"{_AR} must say plainly that the full documentation remains in English"
    )


def test_disclaimers_are_stated_in_arabic():
    """Not financial advice, not religious advice -- in the reader's language too."""
    text = _unwrapped(_read(_AR))
    assert "ليست نصيحةً مالية" in text, (
        f"{_AR} must carry the not-financial-advice disclaimer in Arabic"
    )
    assert "وليست فتوى" in text or "ولا نصيحةً شرعية" in text, (
        f"{_AR} must carry the not-Shariah-advice disclaimer in Arabic"
    )
    assert "Apache-2.0" in text and "(LICENSE)" in text, (
        f"{_AR} must state the licence with its link, as the English README does"
    )
