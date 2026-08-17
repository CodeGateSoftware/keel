"""The fiqh basis document: every encoded ruling, stated with its source (#288).

A Muslim developer deciding whether to trust keel with money is asking a question the code
cannot answer by being read: WHAT fiqh does this machine enforce, and where did each ruling
come from? The codebase carries those answers scattered across guard comments, a KB the size of
a bookshelf, and a handful of experiment records -- auditable in principle, auditable by nobody
who does not already know where to look. #288 asks for one document that states, ruling by
ruling, what Shariah reasoning is encoded, each with its in-repo source, plus what is attested
versus computed, what keel deliberately does not decide, the open questions, and how to
disagree. This file pins that document's acceptance.

The pins are deliberately TWO-SIDED (the house pattern from `test_contributing.py`): every
verbatim quote the document takes from `guards.py`, `screen.py`, or a KB source is asserted in
the document AND in the file it quotes, so the document cannot drift from the code it explains.
The §65.4 qabd condition is pinned THREE-sided -- document, guard, and source -- because it is
the single ruling most likely to be re-litigated. Nothing here asserts that any scholar has
reviewed the document; whether that happens is #289's question, not this one's.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The document under test (#288).
_DOC = "docs/fiqh-basis.md"

#: The one-sentence boundary, quoted exactly -- the same constant `test_governance.py` pins in
#: the README and CONTRIBUTING.md. Declared here (not imported) so each file reads standalone,
#: which is also why the doc must quote it verbatim: three copies, one sentence.
_BOUNDARY = "keel is not a fatwa engine. It is an enforcement engine for a ruling you supply."

#: §65.4's live condition on constructive possession (`qabd`), quoted exactly. Pinned in the
#: document AND in rail 17's comment AND in the KB source -- three-sided, because a sentence
#: this load-bearing must not drift in any of the three places it lives.
_QABD_CONDITION = "there is nothing to prevent the buyer from taking physical possession"

#: The screen's core epistemic split, from `keel/compliance/screen.py`'s docstring: what is
#: attested versus what is computed. Two-sided with the module it describes.
_ATTESTED_NEVER_INFERRED = "ATTESTED, never inferred"

#: The screen's fail-closed posture, same docstring. Two-sided.
_UNKNOWN_IS_A_REJECTION = "unknown is a rejection"

#: The riba screen's failure wording, from `screen_asset`'s `pays_yield` branch. Two-sided.
_RIBA_WORDING = (
    "the asset carries a guaranteed/expected return for holding it, which is riba-like"
)

#: The bare-holder semantics of `pays_yield`, established by fetching the staking docs. Pinned
#: two-sided with the experiment record that did the fetching, so the document's account of
#: what the field asserts cannot outlive the evidence for it.
_BARE_HOLDER = "Bare holding earns nothing, which is exactly what the field asserts."

#: §65.6's correction of the anti-scalping rail, quoted from the KB README's source-65 row.
#: Two-sided, so the document cannot soften the correction the KB is blunt about.
_PRUDENTIAL_CORRECTION = "keeps its trading justification and LOSES its shariah claim"

#: The exact waiver set the doc may claim, two-sided with `screen.py`'s definition: only
#: `history` is waivable today, and the doc must neither widen the hatch nor miss a change
#: to it. `keel assets exempt`'s `--criterion` Choice is built from this very set.
_HISTORY_ONLY_WAIVABLE = 'frozenset({"history"})'

#: Source-67's true author, in the source file's own title: the Handbook QUOTES OIC Fiqh
#: Academy Res. 53/4-6, it is not the resolution itself. Two-sided, so the doc cannot
#: promote a second-hand quotation into a primary resolution again.
_SOURCE_67_OWNER = "Handbook of Islamic Finance"

#: §71.1's finding, in the KB's own capital-letter formulation. The premise caveat's
#: load-bearing phrase: IIFA Res. 237 issued no ruling on whether crypto is tradable
#: property, which makes keel's premise an interpretive position under §29.2, not settled.
_ISSUED_NO_RULING = "ISSUED NO RULING"


def _doc() -> str:
    """The document's text; empty until it exists, so a red run FAILS rather than errors."""
    path = _ROOT / _DOC
    return path.read_text() if path.is_file() else ""


def _unwrapped(text: str) -> str:
    """Join markdown wrapping: drop blockquote markers, then collapse all whitespace."""
    return " ".join(re.sub(r"(?m)^\s*>\s?", "", text).split())


def _rel(relative: str) -> str:
    """Read a repo file, wrap-normalised; Python sources also join adjacent string literals.

    A failure message split across two literals (`"...which is "  "riba-like..."`) is ONE
    sentence to the reader but two quoted fragments after a naive whitespace collapse, so
    implicit concatenation is stitched back before matching.
    """
    text = re.sub(r"(?m)^\s*>\s?", "", (_ROOT / relative).read_text())
    return " ".join(re.sub(r'"\s+"', " ", text).split())


def test_the_document_exists():
    """#288's deliverable is a file, not a section of some other file.

    The basis must be findable by someone who has never read the code -- a path is a promise
    that it stays where it was put.
    """
    assert (_ROOT / _DOC).is_file(), (
        f"{_DOC} must exist: the fiqh basis has to be one findable document"
    )


def test_the_boundary_sentence_is_stated_verbatim():
    """The doc opens with the boundary, word for word, because everything after depends on it.

    A reader deciding whether to trust keel must first learn whose rulings these are -- and a
    paraphrased boundary is a different boundary.
    """
    assert _BOUNDARY in _unwrapped(_doc()), (
        f"{_DOC} must state the governance boundary verbatim: {_BOUNDARY!r}"
    )


def test_the_qabd_condition_is_pinned_three_sided():
    """§65.4's live condition appears in the doc, in rail 17's comment, and in the KB source.

    Rail 17 is the one place keel encodes fiqh as an executable check, and its entire logic is
    this sentence. If any of the three copies is rewritten, this fails rather than letting the
    doc explain a guard that no longer exists (or cite a source that no longer says it).
    """
    texts = {
        _DOC: _unwrapped(_doc()),
        "keel/execution/guards.py": _rel("keel/execution/guards.py"),
        "docs/superpowers/references/trading-knowledge-base/sources/source-65.md": _rel(
            "docs/superpowers/references/trading-knowledge-base/sources/source-65.md"
        ),
    }
    for relative, text in texts.items():
        assert _QABD_CONDITION in text, (
            f"{relative} must carry the §65.4 qabd condition verbatim ({_QABD_CONDITION!r}): "
            "the doc, the rail, and the source must say the same thing"
        )


def test_the_attested_never_inferred_split_is_pinned_two_sided():
    """The doc's core claim -- classifications are attested, never inferred -- is the screen's.

    This is the sentence that separates keel from a fatwa engine, and it is the screen's own
    docstring that says it. The doc must quote it, and the screen must still carry it.
    """
    assert _ATTESTED_NEVER_INFERRED in _unwrapped(_doc()), (
        f"{_DOC} must state that Shariah classifications are {_ATTESTED_NEVER_INFERRED!r}"
    )
    assert _ATTESTED_NEVER_INFERRED in _rel("keel/compliance/screen.py"), (
        "keel/compliance/screen.py must still carry the attested-never-inferred claim the doc "
        "quotes -- if the docstring is rewritten, the doc must be updated to match it"
    )


def test_the_fails_closed_posture_is_pinned_two_sided():
    """'Unknown is a rejection' is quoted from the screen, not invented for the doc.

    An unattested asset failing closed is the screen's single most consequential behaviour; a
    doc that describes it in softer words than the code would be claiming a kindness the code
    does not have.
    """
    assert _UNKNOWN_IS_A_REJECTION in _unwrapped(_doc()), (
        f"{_DOC} must state the fail-closed posture in the screen's own words: "
        f"{_UNKNOWN_IS_A_REJECTION!r}"
    )
    assert _UNKNOWN_IS_A_REJECTION in _rel("keel/compliance/screen.py"), (
        "keel/compliance/screen.py must still carry 'unknown is a rejection' -- the doc "
        "quotes it, so the two must move together"
    )


def test_the_riba_failure_wording_is_pinned_two_sided():
    """The doc quotes the exact message a riba-yield asset receives, and screen.py keeps it.

    Operators meet this ruling as a CLI failure line long before they meet it as doctrine; the
    doc's account and the operator's experience must be the same words.
    """
    assert _RIBA_WORDING in _unwrapped(_doc()), (
        f"{_DOC} must quote the riba failure wording verbatim ({_RIBA_WORDING!r})"
    )
    assert _RIBA_WORDING in _rel("keel/compliance/screen.py"), (
        "keel/compliance/screen.py must still carry the riba failure wording the doc quotes"
    )


def test_the_bare_holder_semantics_are_pinned_to_the_experiment_record():
    """'Bare holding earns nothing' is evidence, not vibes -- the fetching record is cited.

    `pays_yield` asserts what holding WITHOUT staking earns; that semantics was established by
    fetching the staking docs (2026-08-07). The doc must quote the finding and the record must
    still show it.
    """
    assert _BARE_HOLDER in _unwrapped(_doc()), (
        f"{_DOC} must state the bare-holder semantics in the record's own words: "
        f"{_BARE_HOLDER!r}"
    )
    record = "docs/experiments/2026-08-07-unvalidated-skip-set-reassessment.md"
    assert _BARE_HOLDER in _rel(record), (
        f"{record} must still carry the finding the doc quotes -- the semantics stand on it"
    )


def test_rail_17s_seven_day_ttl_is_pinned_to_the_executor():
    """The doc names the TTL by its constant, two-sided with the executor that defines it.

    'Fresh attestation' is a rubbery phrase; `WITHDRAWAL_ATTESTATION_TTL_SEC` is 7 days, and a
    reader auditing the rail needs the number and the symbol that owns it.
    """
    assert "WITHDRAWAL_ATTESTATION_TTL_SEC" in _doc(), (
        f"{_DOC} must name rail 17's TTL as WITHDRAWAL_ATTESTATION_TTL_SEC (7 days), so the "
        "number is traceable to the constant that enforces it"
    )
    assert "WITHDRAWAL_ATTESTATION_TTL_SEC" in _rel("keel/execution/executor.py"), (
        "keel/execution/executor.py must still define WITHDRAWAL_ATTESTATION_TTL_SEC -- "
        "the doc cites it, so a rename must fail here rather than orphan the citation"
    )


def test_the_prudential_rails_are_separated_from_the_fiqh_rails_with_the_65_6_correction():
    """Safety rails must not borrow religious authority -- and §65.6 must be quoted saying so.

    Only rail 17 (and the screen behind rail 1) encode fiqh; the rest are prudential. §65.6
    stripped the anti-scalping rail of a shariah claim it never had, and the KB's wording is
    the citable form of that correction. Two-sided with the KB README's source-65 row.
    """
    doc = _unwrapped(_doc())
    assert "PRUDENTIAL" in doc, (
        f"{_DOC} must mark the non-fiqh rails as prudential -- a safety rail wearing fiqh "
        "clothing is exactly the confusion the document exists to prevent"
    )
    assert _PRUDENTIAL_CORRECTION in doc, (
        f"{_DOC} must state the §65.6 anti-scalping correction in the KB's words: "
        f"{_PRUDENTIAL_CORRECTION!r}"
    )
    kb = "docs/superpowers/references/trading-knowledge-base/README.md"
    assert _PRUDENTIAL_CORRECTION in _rel(kb), (
        f"{kb} must still carry the §65.6 correction the doc quotes"
    )


def test_the_waivable_criteria_claim_is_pinned_two_sided():
    """The doc says only `history` may be waived, and `screen.py` must still mean it.

    `keel assets exempt` is the one escape hatch in the curation screen. The doc's account of
    what it can waive must match the set the code enforces, in both directions: a doc that
    widens the hatch claims an authority the code does not have, and a code change the doc
    misses describes a hatch that no longer exists.
    """
    assert _HISTORY_ONLY_WAIVABLE in _unwrapped(_doc()), (
        f"{_DOC} must state the waiver set exactly ({_HISTORY_ONLY_WAIVABLE!r}): only "
        "`history` is waivable today -- never a Shariah criterion, settlement, or liquidity"
    )
    assert _HISTORY_ONLY_WAIVABLE in _rel("keel/compliance/screen.py"), (
        "keel/compliance/screen.py must still define WAIVABLE_CRITERIA as "
        f"{_HISTORY_ONLY_WAIVABLE!r} -- if the set grows, the doc must be updated to match"
    )


def test_the_atom_dilution_open_question_is_stated_not_hidden():
    """The hardest open question stays in the doc, phrased on the screen's own axis.

    ATOM's uncapped, dynamic inflation pays only bonded delegators, so a bare holder is
    structurally diluted -- yet `pays_yield=NO` remains correct on the axis the field asserts.
    The doc must hold both halves at once and say plainly that this repo has no settled answer.
    """
    known = _doc().split("## Known open questions", 1)[1]
    known_open_questions = known.split("## How to disagree", 1)[0]
    assert "ATOM" in known_open_questions, (
        f"{_DOC} must state the ATOM dilution question among the open questions"
    )
    assert "structurally diluted" in _unwrapped(known_open_questions), (
        f"{_DOC} must say a bare ATOM holder is structurally diluted -- the mechanism, not "
        "just the worry"
    )
    assert "pays_yield" in known_open_questions, (
        f"{_DOC} must name the pays_yield axis alongside the dilution question, so the screen "
        "field and the open question cannot be conflated"
    )
    assert "no settled answer" in _unwrapped(known_open_questions).lower(), (
        f"{_DOC} must say plainly that the ATOM question has no settled answer in this repo"
    )


def test_every_kb_citation_resolves_to_a_source_file_in_the_repo():
    """Every §N.x the doc cites must be a real in-repo file -- citations are checkable
    or they are worthless.

    The KB's convention is that §N.x means sources/source-NN.md; a citation of a source that
    does not exist (there is no source-53 or source-77) is a dead reference wearing the costume
    of scholarship. The load-bearing trio -- §65 (Ayub), §67 (the Handbook quoting the OIC),
    §71 (AAOIFI/IIFA) -- must all appear, because rail 17's tri-sourcing stands on them.
    """
    text = _doc()
    cited = {int(match) for match in re.findall(r"§(\d+)", text)}
    assert cited, f"{_DOC} must cite the KB with §N.x section references"
    for source in (65, 67, 71):
        assert source in cited, (
            f"{_DOC} must cite §{source}: the qabd ruling and the screen stand on it"
        )
    sources_dir = _ROOT / "docs/superpowers/references/trading-knowledge-base/sources"
    for source in sorted(cited):
        assert (sources_dir / f"source-{source:02d}.md").is_file(), (
            f"{_DOC} cites §{source} but sources/source-{source:02d}.md does not exist -- "
            "every citation must resolve to a real in-repo source file"
        )


def test_the_foundational_premise_non_ruling_is_pinned_two_sided():
    """The premise caveat names IIFA's withheld ruling, and source-71.md must still carry it.

    Everything the doc encodes presupposes crypto is tradable property; the honest caveat --
    that IIFA Res. 237 issued no ruling on exactly that question, leaving keel's premise a
    well-supported interpretive position under §29.2 -- must sit among the open questions and
    stay anchored to the KB section that established it.
    """
    known = _doc().split("## Known open questions", 1)[1]
    known_open_questions = known.split("## How to disagree", 1)[0]
    assert _ISSUED_NO_RULING in _unwrapped(known_open_questions), (
        f"{_DOC} must state the §71.1 non-ruling ({_ISSUED_NO_RULING!r}) among the open "
        "questions: keel's premise is an interpretive position under §29.2, not a settled "
        "ruling"
    )
    source = "docs/superpowers/references/trading-knowledge-base/sources/source-71.md"
    assert _ISSUED_NO_RULING in _rel(source), (
        f"{source} must still carry the non-ruling the doc cites -- the caveat stands on it"
    )


def test_source_67_is_attributed_to_its_true_author():
    """§67 is the Handbook quoting OIC Fiqh Academy Res. 53/4-6, not the resolution itself.

    The doc cites §67.1 for the `qabd` tri-sourcing; that passage is a 2022 handbook's
    second-hand reproduction of the resolution. Attributing the quote to the Academy directly
    would dress a quotation as a primary ruling -- the doc and the source file must agree on
    whose document it is.
    """
    source = "docs/superpowers/references/trading-knowledge-base/sources/source-67.md"
    assert _SOURCE_67_OWNER in _unwrapped(_doc()), (
        f"{_DOC} must attribute source-67 to the {_SOURCE_67_OWNER!r} -- it quotes the OIC "
        "resolution, it is not the resolution"
    )
    assert _SOURCE_67_OWNER in _rel(source), (
        f"{source} must still name itself the {_SOURCE_67_OWNER!r} the doc cites"
    )


def test_the_disagreement_section_names_the_local_attestation_route():
    """How to disagree must route through `keel assets attest`, not through a fiqh court.

    The document tells a reader how to dissent; the only route the architecture offers is
    recording your own attestation locally. A disagreement section without that command is an
    invitation to litigate upstream, which the project has forsworn.
    """
    text = _doc()
    assert "## What keel deliberately does not decide" in text, (
        f"{_DOC} must have a section naming what keel deliberately does not decide"
    )
    assert "## How to disagree" in text, (
        f"{_DOC} must have a 'How to disagree' section"
    )
    disagreement = text.split("## How to disagree", 1)[1]
    assert "keel assets attest" in disagreement, (
        f"{_DOC}'s disagreement section must name the local route (`keel assets attest`) "
        "-- it is the only dissent path the architecture provides"
    )


def test_the_readme_links_the_document():
    """The README's documentation map points at the basis, so a stranger can find it.

    A document about findability that cannot be found from the front door would be a joke in
    exactly the register this project tries to avoid.
    """
    assert _DOC in (_ROOT / "README.md").read_text(), (
        f"README.md must link {_DOC} from its documentation map"
    )
