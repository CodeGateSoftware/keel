"""The Plans page, inverted (#706).

Alpaca's Plans & Features page is a tier matrix with "Current plan" and "Upgrade" badges --
radical clarity about pricing, and honest *as marketing*. keel's constitution runs the other way:
paper is free and unlimited forever, and the free engine is never a demo.

So this page takes the clarity and refuses the gating. It is a TRANSPARENCY ARTIFACT: what runs on
this device and why it costs nothing, what a tier would change if one ever existed, and the named
trigger that must fire before any of it does.

── THE ACCEPTANCE CRITERION IS TRACEABILITY, SO IT IS MECHANICAL ────────────────────────────────

"Every claim on the page traces to the evolution plan or the constitution -- no aspirational
copy." A page of marketing sentences that *sound* like the documents would satisfy a human
reviewer and rot in a month.

So every claim carries the file it came from, and these tests read that file and assert the
sentence is IN it. A claim nobody can trace fails the build; a document edited out from under the
page fails the build too, which is the direction that actually matters -- the page cannot go on
asserting something the project has stopped believing.
"""

from __future__ import annotations

import re
from pathlib import Path

from keel.commands import plans

_ROOT = Path(__file__).resolve().parents[2]


def _doc(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def _readable(document: str) -> str:
    """The document with its markdown EMPHASIS removed, and nothing else changed.

    The page renders quoted sentences through `plain()`, which sets `textContent` -- the client has
    no markdown pass and bans `innerHTML` -- so a quote carrying `**bold**` shows literal asterisks
    on the page whose whole subject is that it can be checked. Worse, quoting up to a closing `**`
    ends the sentence where the emphasis ends rather than where the sentence does, which reads as a
    truncation bug: the affiliate rule did exactly that.

    So the CLAIMS are written as prose and the DOCUMENT is normalised to meet them. Only `**` and
    backticks go; every word stays, so a quote still has to be the document's own words in the
    document's own order.
    """
    return document.replace("**", "")


def _statements(document: str) -> list[str]:
    """Every place the document STARTS saying something, as the text from there on.

    A bare `in` verifies PRESENCE, not faithfulness, and the difference is not academic:
    `"reopens affiliate links"` is a contiguous substring of ADR 0004, where the sentence reads
    "None of these four reopens affiliate links". Quoted alone under a heading saying "What no tier
    will ever gate", it would invert the record's meaning, carry a checkable citation, and pass a
    substring test.

    So this enumerates statement starts -- a list item, a table cell, a paragraph, and each
    sentence within one -- and `_quotes` asks whether a claim begins at one. Continuation lines are
    joined and whitespace collapsed first, because these documents wrap at 100 columns and a quoted
    sentence should not have to reproduce where the wrapping fell.
    """
    starts: list[str] = []
    for block in _readable(document).split("\n\n"):
        for row in block.splitlines():
            if row.strip().startswith("|"):
                # A table row: every cell is its own statement. `| 3 | Operators repeatedly...`
                starts.extend(cell.strip() for cell in row.strip().strip("|").split("|"))
        prose = " ".join(
            row.strip() for row in block.splitlines() if not row.strip().startswith("|")
        )
        prose = " ".join(prose.split())
        if not prose:
            continue
        # The block itself, the text after each list marker, and each sentence within it.
        starts.append(prose)
        starts.extend(_LIST_MARKER.split(prose)[1:])
        starts.extend(part.strip() for part in _SENTENCE_END.split(prose)[1:])
    return [start for start in starts if start]


#: A markdown list marker anywhere in a joined block -- `- `, `* `, `1. `. Used to SPLIT, so what
#: follows each marker becomes a statement start of its own.
_LIST_MARKER = re.compile(r"(?:^|\s)(?:[-*]|\d+\.)\s+")

#: Sentence-ending punctuation followed by a space. Same use: what follows is a new statement.
_SENTENCE_END = re.compile(r"(?<=[.?!:])\s+")


def _quotes(claim: str, document: str) -> bool:
    """Whether `claim` is quoted from the START of something the document says.

    Not that it appears somewhere in it. That does not make a quote honest -- nothing can -- but it
    removes the trick of beginning a sentence in the middle to drop the word that reverses it.
    """
    wanted = " ".join(claim.split())
    return any(start.startswith(wanted) for start in _statements(document))


# -- traceability ---------------------------------------------------------------------------------


def test_every_constitution_line_is_quoted_from_the_evolution_plan() -> None:
    """The eight numbered lines of §4, verbatim. Not paraphrased: a paraphrase is a new claim
    wearing the authority of an old one."""
    text = _readable(_doc("docs/evolution-plan.md"))
    assert plans.CONSTITUTION, "the scan found no lines -- it would pass against any page"
    for line in plans.CONSTITUTION:
        assert _quotes(line.text, text), (
            f"constitution line not quoted from the start of a statement: {line.text!r}"
        )


def test_the_constitution_here_is_the_WHOLE_constitution() -> None:
    """Eight lines, and a page that quoted seven would be choosing which of the project's own
    commitments to show a reader -- on the page whose entire subject is what the project promises.
    Read out of the document rather than counted against a literal, so adding a ninth line to the
    constitution fails this until the page carries it."""
    text = _doc("docs/evolution-plan.md")
    heading = re.search(r"^#+ [\d.]* ?The constitution.*$", text, re.MULTILINE)
    assert heading is not None, (
        "the evolution plan has no 'The constitution' heading -- this test cannot find the "
        "section it is counting, which is a failure to report and not one to crash on"
    )
    section = text[heading.end() :].split("\n## ")[0]
    # `^\d+\. ` at the START of a line, anchored. The first cut filtered on "first character is a
    # digit", which counted a nested sub-list item and a prose line beginning with a year as
    # constitution lines -- three innocent reformats of the document turned into build failures
    # that said the constitution had grown.
    numbered = re.findall(r"^\d+\. ", section, re.MULTILINE)
    assert numbered, "found no numbered lines in the constitution section"
    assert len(numbered) == len(plans.CONSTITUTION), (
        f"the constitution has {len(numbered)} lines and this page carries "
        f"{len(plans.CONSTITUTION)}"
    )


def test_every_tier_is_quoted_from_the_phase_f_table() -> None:
    """Price and promise both. A page that restated `$14/mo` as "about fifteen dollars" would be
    a friendlier number than the one the project committed to."""
    text = _readable(_doc("docs/evolution-plan.md"))
    assert plans.TIERS
    for tier in plans.TIERS:
        # ALL FOUR CELLS. `buys` is the longest string on the page and a whole
        # table column, and the first cut of this test checked name, price and
        # promise only -- so a contributor could add "early access to new rule
        # families" to a tier, render it with a citation beside it, and pass
        # every gate.
        for cell in (tier.name, tier.price, tier.buys, tier.promise):
            assert _quotes(cell, text), (
                f"tier cell not quoted from the start of a statement: {cell!r}"
            )


def test_every_trigger_is_quoted_from_the_decision_record() -> None:
    """ADR 0004 says the four triggers are the complete set and that a new trigger is a new
    decision record. The page may not grow a fifth."""
    text = _readable(_doc("docs/decisions/0004-monetisation-not-now.md"))
    assert len(plans.TRIGGERS) == 4
    for trigger in plans.TRIGGERS:
        assert _quotes(trigger.text, text), (
            f"trigger not quoted from the start of its cell: {trigger.text!r}"
        )


def test_every_refusal_is_quoted_from_a_named_document() -> None:
    """The "never paywalled" list is the load-bearing half of this page, and it is the half a
    later contributor would be most tempted to soften."""
    assert plans.NEVER_PAYWALLED
    for refusal in plans.NEVER_PAYWALLED:
        assert _quotes(refusal.text, _readable(_doc(refusal.source))), (
            f"refusal not quoted from the start of a statement in {refusal.source}: "
            f"{refusal.text!r}"
        )


def test_every_claim_names_a_file_that_exists() -> None:
    """A citation to a document that is not there is worse than none: it looks checkable."""
    sources = {claim.source for claim in plans.every_claim()}
    assert sources
    for source in sources:
        assert (_ROOT / source).is_file(), f"cited document is missing: {source}"


# -- the refusals ---------------------------------------------------------------------------------


def test_nothing_on_this_page_is_for_sale_today() -> None:
    """ADR 0004's answer is "not now", and no trigger has fired. A tier rendered as available
    would be this page announcing a product that does not exist."""
    assert all(not tier.shipped for tier in plans.TIERS)
    report = plans.gather_plans()
    assert report.anything_for_sale is False


def test_no_tier_carries_a_price_a_reader_could_act_on() -> None:
    """No link, no address, no instruction. The prices are here as a published intention, and a
    page that told a reader how to pay one would have shipped the tier."""
    from tests.web.test_client_assets import _PURCHASE_AFFORDANCES

    # The SAME list the payload test uses. Two lists drift, and the first cut had exactly that:
    # six generic e-commerce words here and six different ones there, neither a superset of the
    # other, and a `bitcoin:` URI -- the rail ADR 0004 actually names -- invisible to both.
    #
    # This is the second belt. The first is `test_every_tier_is_quoted_from_the_phase_f_table`
    # above: every cell of every row has to appear verbatim in the evolution plan, so an injected
    # call to action fails traceability without anyone having had to think of its wording.
    for tier in plans.TIERS:
        blob = " ".join((tier.name, tier.price, tier.buys, tier.promise)).lower()
        for affordance in _PURCHASE_AFFORDANCES:
            assert affordance not in blob, f"{tier.name} carries an affordance: {affordance}"


def test_the_page_says_what_would_have_to_happen_first() -> None:
    """The difference between a transparency artifact and a coming-soon page. A reader learns the
    condition, and the condition is about users rather than revenue."""
    assert plans.TRIGGER_FRAMING_SOURCE
    text = _doc(plans.TRIGGER_FRAMING_SOURCE)
    assert plans.TRIGGER_FRAMING in text


def test_the_free_tier_is_the_one_that_exists() -> None:
    """Every other row is conditional; this one is what the reader is running. The page must not
    present the four as four choices."""
    (free,) = [tier for tier in plans.TIERS if tier.available_now]
    assert "Free" in free.name
    assert free.price.startswith("$0")


def test_the_for_sale_sentence_is_read_from_the_table_not_asserted_beside_it() -> None:
    """The day a tier ships, the page must stop saying nothing is for sale -- and it must do so
    because the TABLE changed, not because someone remembered to edit a sentence too.

    A constant here would let the two disagree, and the direction they would disagree in is the
    one that matters: a shipped product under a headline saying there is nothing to buy.
    """
    from dataclasses import replace

    report = plans.gather_plans()
    assert report.anything_for_sale is False

    shipped = replace(report, tiers=(replace(report.tiers[1], shipped=True), *report.tiers[2:]))
    assert shipped.anything_for_sale is True


def test_a_shipped_tier_changes_what_the_page_says(monkeypatch) -> None:
    """End to end through the payload, because the sentence is what a reader sees. The `on`
    wording is a WARNING rather than an announcement: a page still describing itself as a
    transparency artifact while a tier is live is out of date, and that is the honest reading."""
    from dataclasses import replace

    from keel.web import payload

    report = plans.gather_plans()
    shipped = replace(report, tiers=(replace(report.tiers[1], shipped=True), *report.tiers[2:]))

    assert payload.plans_payload(report)["for_sale"]["value"] == "false"
    body = payload.plans_payload(shipped)["for_sale"]
    assert body["value"] == "true"
    assert body["state"] == "warn"


def test_a_quote_that_starts_mid_sentence_is_refused() -> None:
    """The mechanism, demonstrated on the exact case that motivated it.

    ADR 0004 says "None of these four reopens affiliate links (rule 2, above)". The tail of that
    sentence, quoted alone under "What no tier will ever gate", says the opposite of the record --
    and it is a contiguous substring, so a plain `in` accepts it.
    """
    record = _doc(plans.DECISION_RECORD)
    assert "reopens affiliate links" in record, "the premise: it IS a substring"
    assert not _quotes("reopens affiliate links", record)
    assert _quotes("None of these four reopens affiliate links", record)


def test_a_quote_may_span_the_documents_line_wrapping() -> None:
    """These documents wrap at 100 columns, and a quoted sentence should not have to reproduce
    where the wrapping fell -- only what the sentence says."""
    record = _doc(plans.DECISION_RECORD)
    wrapped = (
        "Affiliate or referral links to any trading venue, broker, or exchange are never added "
        "to keel, its documentation, or its site"
    )
    assert wrapped not in record, "the premise: the raw document breaks this line"
    assert _quotes(wrapped, _readable(record)) or _quotes(wrapped, record)


def test_every_string_the_page_shows_is_traced_by_something() -> None:
    """The coverage question, asked of the payload rather than of the tables.

    `every_claim` is what the traceability tests iterate, and the first cut of it omitted
    `Tier.buys` -- a whole table column, and the longest string on the page. This compares what
    the wire actually carries against what is traced, so the next omission fails here rather than
    waiting for someone to notice a column nobody checks.

    The sentences this codebase WRITES -- status notes, the nothing-for-sale line -- are named
    exemptions: they describe the page's own state rather than quoting a document, and there is
    nothing for them to trace to.
    """
    from keel.web import payload

    traced = {" ".join(claim.text.split()) for claim in plans.every_claim()}
    body = payload.plans_payload(plans.gather_plans())

    shown: set[str] = set()
    for claim in [*body["constitution"], *body["never_paywalled"], body["trigger_framing"]]:
        shown.add(" ".join(str(claim["text"]).split()))
    for tier in body["tiers"]:
        for key in ("name", "price", "buys", "promise"):
            shown.add(" ".join(str(tier[key]).split()))
    for trigger in body["triggers"]:
        shown.add(" ".join(str(trigger["text"]).split()))

    assert shown <= traced, f"shown on the page and traced by nothing: {sorted(shown - traced)}"


def _table_rows(document: str, header: str) -> list[list[str]]:
    """The cells of every row of the markdown table whose header row contains `header`."""
    rows: list[list[str]] = []
    seen_header = False
    for line in _readable(document).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            seen_header = False
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if header in cells:
            seen_header = True
            continue
        if seen_header and not set("".join(cells)) <= set("-: "):
            rows.append(cells)
    return rows


def test_each_trigger_is_the_WHOLE_firing_condition_not_a_prefix_of_it() -> None:
    """Anchoring a quote to the start of a statement stops it beginning mid-sentence. It does not
    stop it ENDING early, and for a trigger that is the dangerous half.

    ADR 0004's trigger 3 reads "...ask for something that costs real money to run on their behalf
    — hosted infra, ... — not a feature request, a request to pay for upkeep". Cut at "on their
    behalf", it fires on any feature request, which is strictly more of the world than the record
    says. The page would then be publishing an easier promise than the one that was made.

    So the firing condition is compared WHOLE, against the record's own "What fires it" cell.
    """
    rows = _table_rows(_doc(plans.DECISION_RECORD), "What fires it")
    assert len(rows) == 4, f"ADR 0004's trigger table has {len(rows)} rows"

    recorded = {int(cells[0]): " ".join(cells[1].split()) for cells in rows}
    for trigger in plans.TRIGGERS:
        assert " ".join(trigger.text.split()) == recorded[trigger.number], (
            f"trigger {trigger.number} is not the record's whole firing condition"
        )


def test_the_constitution_counter_ignores_a_nested_sub_list() -> None:
    """The parser's own regression. "First character is a digit" counted a nested sub-point and a
    prose line beginning with a year as constitution lines, so three innocent reformats of the
    document became build failures claiming the constitution had grown."""
    import re as _re

    # Three numbered lines, plus the two shapes the old predicate miscounted: a nested sub-point
    # that happens to be numbered, and a prose line beginning with a date. Both satisfy "first
    # character is a digit" and one of them satisfies ". " as well.
    section = (
        "1. One.\n"
        "2. Two.\n"
        "   1. a numbered sub-point\n"
        "2026-08-31 note. Every Jesse mechanic adopted passed a line here.\n"
        "3. Three.\n"
    )
    assert len(_re.findall(r"^\d+\. ", section, _re.MULTILINE)) == 3

    loose = [
        line
        for raw in section.splitlines()
        if (line := raw.strip()) and line[0].isdigit() and ". " in line
    ]
    assert len(loose) == 5, "the premise: the old predicate counted five"
