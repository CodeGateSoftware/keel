"""Rail 14 is a monthly BUY cap, and nothing keel prints may say it buys fee-free (#836).

keel places its orders through Coinbase Advanced Trade. On 2026-09-27 that account's own
`get_transaction_summary` reported the Intro tier (0.9% taker, 0.5% maker) with
`has_promo_fee: false`, and every live fill had paid a fee. Coinbase One's "zero trading fees
up to $500/month" applies on the main Coinbase platform, not Advanced Trade. So the attested
`free_volume_usd` is a cap keel imposes on its own buying, not a fee waiver the venue grants,
and text calling it a "fee-free allowance" told the operator their orders were free when they
were not.

These tests pin the operator-facing wording and the append-only records. They deliberately do
NOT change what rail 14 enforces: the guard tests in `tests/execution/test_guards.py` pass
untouched, and that is the proof the numbers did not move.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

from keel.cli import cli
from keel.research.significance import render_family, significance
from keel.research.throughput import VenueThroughput, render_report
from keel.sim.report import _render_tier_section
from tests.research.test_significance import _outcomes

_ROOT = Path(__file__).resolve().parent.parent
_RAIL_DOC = _ROOT / "docs/rails/rail-14-subscription-allowance.md"
_FEE_NOTE = "#836"
#: A sentence asserting the venue waives fees inside the cap. Mentioning the words to say they
#: do NOT apply is allowed; claiming them is not.
_CLAIM = re.compile(
    r"lets the account trade taker-fee-free|trade taker-fee-free|fee-free monthly volume allowance",
    re.I,
)


def test_subscription_set_help_calls_it_a_buy_cap_not_a_fee_waiver() -> None:
    result = CliRunner().invoke(cli, ["subscription", "set", "--help"])
    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.split())
    assert "fee-free" not in help_text.lower()
    assert "buy cap" in help_text.lower()


def test_the_throughput_report_does_not_route_within_a_fee_free_allowance() -> None:
    venue = VenueThroughput(
        venue="coinbase",
        monthly_allowance=Decimal("500"),
        mean_trade_notional=Decimal("50"),
        expected_signals_per_month=Decimal("4"),
    )
    text = "\n".join(render_report([venue], target_edge=Decimal("0.124"))).lower()
    assert "fee-free" not in text
    assert "buy cap" in text
    assert "never enlarged" in text  # the rail 14 guardrail is still stated


def test_a_zero_fee_significance_row_is_labelled_hypothetical() -> None:
    stat = significance("rsi_meanrev", "inside_allowance_fee_free", Decimal("0"), _outcomes(50, 50))
    text = "\n".join(render_family(stat)).lower()
    assert "the fee-free allowance" not in text
    assert "zero fee" in text
    assert "hypothetical" in text
    assert _FEE_NOTE in text


def test_the_tier_section_says_its_within_cap_rows_are_hypothetical_on_advanced_trade() -> None:
    text = " ".join(_render_tier_section([]))
    assert "hypothetical" in text.lower()
    assert "Advanced Trade" in text
    assert _FEE_NOTE in text


def test_the_rail_14_doc_no_longer_claims_fees_are_waived() -> None:
    doc = _RAIL_DOC.read_text(encoding="utf-8")
    assert not _CLAIM.search(doc), "the rail 14 doc still claims the venue waives fees"
    assert "buy cap" in doc.lower()
    assert _FEE_NOTE in doc


def test_every_record_that_described_a_fee_free_regime_carries_the_fee_note() -> None:
    """Records are appended to, never rewritten. A record that priced or described a
    fee-free regime keeps its text and gains a dated note pointing at #836."""
    records = sorted((_ROOT / "docs/experiments").glob("*.md")) + sorted(
        (_ROOT / "docs/research").glob("*.md")
    )
    missing = [
        path.name
        for path in records
        if "fee-free" in (text := path.read_text(encoding="utf-8")).lower()
        and _FEE_NOTE not in text
    ]
    assert not missing, "records describing a fee-free regime without the #836 note:\n  " + (
        "\n  ".join(missing)
    )
