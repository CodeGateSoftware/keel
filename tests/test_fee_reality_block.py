"""The README's fee-reality benchmark is RENDERED from the ledger, never typed (#646).

The block is the project's opening claim -- "no shipped rule family is net-positive at the taker
fee actually paid" -- shown as numbers rather than asserted as prose. Its whole persuasive force
is that it comes from the hash-chained record of what was actually run, so a figure edited by
hand would not be a small inaccuracy: it would be the one claim this repository makes about
itself, made the way it says nobody should.

These tests are the mechanism. The README and `scripts/render_fee_reality.py` must agree
byte-for-byte, and the renderer must refuse to emit anything at all from a ledger row it does
not fully recognise.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

import render_fee_reality as rfr  # noqa: E402

_LEDGER_TEXT = (_ROOT / "docs/experiments/trials-ledger.jsonl").read_text(encoding="utf-8")
_README = (_ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_block_matches_what_the_ledger_renders() -> None:
    """The drift pin. Edit the README's numbers and this fails; re-run the renderer and it
    passes -- which is the only way the block is allowed to change."""
    block = rfr.render(_LEDGER_TEXT)
    assert block in _README, (
        "README.md's fee-reality block is not what scripts/render_fee_reality.py produces from "
        "the ledger. Regenerate it (`python scripts/render_fee_reality.py --write`) rather than "
        "editing the numbers -- a hand-edited benchmark is the failure this pin exists for."
    )


def test_the_block_leads_the_readme_before_the_feature_list() -> None:
    """#646's first acceptance line: the benchmark comes before what keel can do.

    A visitor who reads the capabilities first and the result second has been sold to and then
    corrected. The order is the honesty.
    """
    block_at = _README.find(rfr.BEGIN)
    assert block_at != -1, "the fee-reality block is not in README.md at all"
    for later in ("## ", "### What it does", "### Install"):
        found = _README.find(later)
        if found != -1:
            assert block_at < found, f"the benchmark sits after {later!r}"


def test_every_number_in_the_block_appears_in_the_ledger_row() -> None:
    """No figure may exist in the README that is not in the record.

    Stronger than "the renderer produced it", because a renderer with a literal baked in would
    also satisfy the drift pin -- both sides would simply carry the same invention.
    """
    curves, fees, _rule = rfr.parse(_LEDGER_TEXT)
    row = rfr._row(_LEDGER_TEXT)["params"]["fee_curve"]
    assert curves, "no asset curves parsed -- this test would prove nothing"
    for curve in curves:
        assert f"n={curve.trades}" in row
        for value in curve.by_fee.values():
            assert value in row, f"{curve.asset}: {value} is not in the ledger row"
        assert f"{curve.break_even_pct}%" in row


def test_a_profit_factor_never_carries_a_stray_sentence_period() -> None:
    """The bug the first draft shipped into its own output.

    `[\\d.]+` is greedy enough to swallow the sentence's full stop into the LAST asset's profit
    factor, and "1.303." is still readable, still wrong, and would have gone unnoticed in a
    table. Anchored on the last asset specifically, because that is the only position where the
    sentence can reach.
    """
    curves, _fees, _rule = rfr.parse(_LEDGER_TEXT)
    for curve in curves:
        for value in curve.by_fee.values():
            assert not value.endswith("."), f"{curve.asset}: {value!r} has a trailing period"
            float(value)


def test_the_selection_bias_warning_travels_with_the_numbers() -> None:
    """The ledger states it at full strength and the README must not quietly drop it.

    Every configuration in the table is the argmax of a 144-cell slice selected on the same data
    it is re-priced on. Quoting those as an asset's expected profit factor is exactly what the
    experiment record forbids, and a benchmark that omitted the caveat would be a stronger claim
    than the measurement supports -- in a block whose entire point is not doing that.
    """
    block = rfr.render(_LEDGER_TEXT)
    assert "never as edge estimates" in block
    assert "argmax" in block
    assert "zero *fee*, not zero cost" in block, (
        "slippage is held at 0.0005 in every cell, so the zero column is zero FEE and not zero "
        "cost -- the ledger's own `validation` field says so and the block must too"
    )


@pytest.mark.parametrize(
    ("mutation", "why"),
    [
        ("PF by fee_pct 0/0.001", "no per-asset curve at all"),
        ("BTC (n=123) 1.090/0.961", "asset curves with no fee-column list"),
        (
            # The break-even clause is SEPARATE prose in the real row, and it has to be here
            # too: without it the missing-break-even guard fires first and the count guard is
            # never reached -- which is exactly how the first version of this case passed
            # against a renderer with the count guard deleted.
            "PF by fee_pct 0/0.001/0.002: BTC (n=123) 1.090/0.961. "
            "Brackets: BTC 0.060%->1.0096 / 0.070%->0.9971 => 0.068%",
            "two values against three columns -- the case that reaches the count guard",
        ),
        (
            "PF by fee_pct 0/0.012: BTC (n=123) 1.090/0.333",
            "a curve with no measured break-even",
        ),
    ],
)
def test_the_renderer_refuses_a_ledger_row_it_cannot_fully_parse(mutation: str, why: str) -> None:
    """Renders the ledger's numbers or it does not ship. A partial parse is the quiet
    half-truth the block exists to refuse, so every recognition failure is fatal."""
    row = json.loads([line for line in _LEDGER_TEXT.splitlines() if rfr._SESSION in line][-1])
    row["params"]["fee_curve"] = mutation
    with pytest.raises(SystemExit):
        rfr.render(json.dumps(row))


def test_the_renderer_refuses_a_readme_without_sentinels() -> None:
    """It will not guess where the benchmark belongs."""
    with pytest.raises(SystemExit, match="sentinels"):
        rfr.replace_block("# keel\n\nnothing here\n", "block")


# -- the cost-distortion block (#646) ------------------------------------------------------------


def test_the_cost_distortion_block_matches_what_the_ledger_renders() -> None:
    """Same drift pin as the benchmark above, on the section that makes the claim.

    This block asserts that our own cost-model error exceeded our best strategy gain. That is a
    statement about two measured numbers, and it is only worth making while both come out of the
    record rather than out of someone's memory of them.
    """
    assert rfr.render_cost_distortion(_LEDGER_TEXT) in _README, (
        "README.md's cost-distortion block is not what the renderer produces from the ledger. "
        "Regenerate it rather than editing the numbers."
    )


def test_the_ratio_is_computed_not_typed() -> None:
    """`2.7x` is a DERIVED figure — the correction divided by the improvement — and both sides
    come from separate hash-chained ledger rows. A literal would survive either number changing
    underneath it, which is exactly the failure a headline ratio invites."""
    restated = rfr._summary(_LEDGER_TEXT, rfr._RESTATEMENT)
    exit_ab = rfr._summary(_LEDGER_TEXT, rfr._EXIT_AB)
    correction = abs(Decimal(restated["pf_median_delta"]))
    improvement = Decimal(exit_ab["delta_vs_control_median_zero_fee"])
    expected = (correction / improvement).quantize(Decimal("0.1"))

    assert f"**{expected}× larger**" in rfr.render_cost_distortion(_LEDGER_TEXT)
    assert expected > 1, "the claim only holds while the correction exceeds the improvement"


def test_the_block_names_no_other_framework() -> None:
    """#646 in terms: 'No competitor naming in the asset. Generic fee-drag math — never what
    another product costs you.'

    And beyond the trademark posture: we have not measured anyone else's assumptions. An
    unsourced claim about a named third party is the exact failure this section exists to avoid
    making about ourselves, and it would cost more credibility than it could ever buy.
    """
    block = rfr.render_cost_distortion(_LEDGER_TEXT).lower()
    for name in ("jesse", "freqtrade", "hummingbot", "backtrader", "quantconnect", "zipline"):
        assert name not in block, f"the block names {name!r}"
    assert "not a claim about any other framework" in block


def test_the_block_scopes_its_own_claim() -> None:
    """One venue, one universe, our own numbers. A finding stated wider than it was measured is
    the thing this repository refuses to publish, and a headline ratio is exactly where that
    temptation lands."""
    block = rfr.render_cost_distortion(_LEDGER_TEXT)
    assert "keel mis-pricing keel" in block
    assert "one venue" in block


def test_the_source_link_is_not_broken_by_wrapping() -> None:
    """`textwrap` will happily break inside a URL, and a broken markdown link renders as literal
    text — which the first render did. Every link in the block must survive on one line."""
    for line in rfr.render_cost_distortion(_LEDGER_TEXT).splitlines():
        if "](" in line:
            assert line.count("](") == line.count(")"), f"link broken across lines: {line!r}"
            assert ".md)" in line, f"link truncated: {line!r}"


def test_the_table_is_one_table() -> None:
    """A blank line between rows ENDS a markdown table. The first render put one after every
    paragraph uniformly and produced four one-row tables."""
    rows = [
        line
        for line in rfr.render_cost_distortion(_LEDGER_TEXT).splitlines()
        if line.startswith("|")
    ]
    assert len(rows) == 4
    block = rfr.render_cost_distortion(_LEDGER_TEXT)
    assert "\n\n|" not in block.split("| :--", 1)[1], "a blank line splits the table"
