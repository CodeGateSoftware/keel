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

from pathlib import Path

from keel.commands import plans

_ROOT = Path(__file__).resolve().parents[2]


def _doc(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


# -- traceability ---------------------------------------------------------------------------------


def test_every_constitution_line_is_quoted_from_the_evolution_plan() -> None:
    """The eight numbered lines of §4, verbatim. Not paraphrased: a paraphrase is a new claim
    wearing the authority of an old one."""
    text = _doc("docs/evolution-plan.md")
    assert plans.CONSTITUTION, "the scan found no lines -- it would pass against any page"
    for line in plans.CONSTITUTION:
        assert line.text in text, f"constitution line not in the evolution plan: {line.text!r}"


def test_the_constitution_here_is_the_WHOLE_constitution() -> None:
    """Eight lines, and a page that quoted seven would be choosing which of the project's own
    commitments to show a reader -- on the page whose entire subject is what the project promises.
    Read out of the document rather than counted against a literal, so adding a ninth line to the
    constitution fails this until the page carries it."""
    text = _doc("docs/evolution-plan.md")
    section = text.split("## 4. The constitution")[1].split("\n## ")[0]
    numbered = [
        stripped
        for raw in section.splitlines()
        if (stripped := raw.strip()) and stripped[0].isdigit() and ". " in stripped
    ]
    assert len(numbered) == len(plans.CONSTITUTION), (
        f"the constitution has {len(numbered)} lines and this page carries "
        f"{len(plans.CONSTITUTION)}"
    )


def test_every_tier_is_quoted_from_the_phase_f_table() -> None:
    """Price and promise both. A page that restated `$14/mo` as "about fifteen dollars" would be
    a friendlier number than the one the project committed to."""
    text = _doc("docs/evolution-plan.md")
    assert plans.TIERS
    for tier in plans.TIERS:
        assert tier.name in text, f"tier name not in the evolution plan: {tier.name!r}"
        assert tier.price in text, f"tier price not in the evolution plan: {tier.price!r}"
        assert tier.promise in text, f"tier promise not in the evolution plan: {tier.promise!r}"


def test_every_trigger_is_quoted_from_the_decision_record() -> None:
    """ADR 0004 says the four triggers are the complete set and that a new trigger is a new
    decision record. The page may not grow a fifth."""
    text = _doc("docs/decisions/0004-monetisation-not-now.md")
    assert len(plans.TRIGGERS) == 4
    for trigger in plans.TRIGGERS:
        assert trigger.text in text, f"trigger not in ADR 0004: {trigger.text!r}"


def test_every_refusal_is_quoted_from_a_named_document() -> None:
    """The "never paywalled" list is the load-bearing half of this page, and it is the half a
    later contributor would be most tempted to soften."""
    assert plans.NEVER_PAYWALLED
    for refusal in plans.NEVER_PAYWALLED:
        assert refusal.text in _doc(refusal.source), (
            f"refusal not in {refusal.source}: {refusal.text!r}"
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
    report = plans.gather_plans(now_ts=0)
    assert report.anything_for_sale is False


def test_no_tier_carries_a_price_a_reader_could_act_on() -> None:
    """No link, no address, no instruction. The prices are here as a published intention, and a
    page that told a reader how to pay one would have shipped the tier."""
    for tier in plans.TIERS:
        blob = " ".join((tier.name, tier.price, tier.buys, tier.promise)).lower()
        for affordance in ("http", "@", "buy", "subscribe", "checkout", "sign up", "contact"):
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

    report = plans.gather_plans(now_ts=0)
    assert report.anything_for_sale is False

    shipped = replace(report, tiers=(replace(report.tiers[1], shipped=True), *report.tiers[2:]))
    assert shipped.anything_for_sale is True


def test_a_shipped_tier_changes_what_the_page_says(monkeypatch) -> None:
    """End to end through the payload, because the sentence is what a reader sees. The `on`
    wording is a WARNING rather than an announcement: a page still describing itself as a
    transparency artifact while a tier is live is out of date, and that is the honest reading."""
    from dataclasses import replace

    from keel.web import payload

    report = plans.gather_plans(now_ts=0)
    shipped = replace(report, tiers=(replace(report.tiers[1], shipped=True), *report.tiers[2:]))

    assert payload.plans_payload(report)["for_sale"]["value"] == "false"
    body = payload.plans_payload(shipped)["for_sale"]
    assert body["value"] == "true"
    assert body["state"] == "warn"
