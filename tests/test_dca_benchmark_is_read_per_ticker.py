"""A benchmark carried by one name is not a benchmark -- issue #371.

The equities DCA run pre-declared its own refutation condition: "a sleeve whose terminal value
is dominated by one ticker, making 'beat DCA' a statement about NVDA rather than about
equities". It fired. NVDA is 44.8% of the terminal sleeve and its +449% drags the pooled figure
to +145.10% while the median ticker returned +70.62% and the four-name sleeve without it
returned +69.03%.

So the number a future equity claim is measured against is the MEDIAN TICKER, not the pooled
sleeve, and the document has to say so where a reader will see it. Quoting +145% as "what DCA
did" would set a bar that one exceptional name built and then attribute clearing it -- or
failing to -- to a strategy.

These pins are on the prose because that is where the damage would be done. The numbers
themselves live in the `.jsonl` beside the record and are reproducible from the driver.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_RECORD = _ROOT / "docs/experiments/2026-09-03-equities-dca-benchmark.md"
_DATA = _ROOT / "docs/experiments/2026-09-03-equities-dca-benchmark.jsonl"


def _measured() -> list[dict]:
    rows = [json.loads(line) for line in _DATA.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["arm"] == "measured"]


def test_the_record_and_its_data_both_exist() -> None:
    assert _RECORD.is_file(), "the benchmark record is missing"
    assert _measured(), "the benchmark's measured arm produced no rows"


def test_one_ticker_really_does_dominate_the_sleeve() -> None:
    """The premise of every other pin here. If a re-run ever makes the sleeve balanced, these
    tests should fail loudly rather than keep enforcing a caveat that no longer applies."""
    rows = _measured()
    values = {r["product_id"]: Decimal(r["market_value"]) for r in rows}
    total = sum(values.values())
    top = max(values.values())
    assert top / total > Decimal("0.4"), f"no ticker dominates any more: {values}"


def test_the_headline_benchmark_is_the_median_ticker_not_the_pooled_sleeve() -> None:
    """The pooled figure may appear -- suppressing it would be its own dishonesty -- but the
    sentence naming what a strategy must beat has to name the median."""
    text = _RECORD.read_text(encoding="utf-8")
    headline = re.search(r"(?im)^.*benchmark to beat.*$", text)
    assert headline, "the record never says what the benchmark to beat is"
    assert "median" in headline.group(0).lower(), headline.group(0)


def test_the_concentration_caveat_is_stated_with_its_number() -> None:
    """In the OPENING, not just in the table. A reader who takes the headline and stops must
    still have been told that one name carries it -- the table row alone lets the prose be
    gutted while this pin stays green, which is exactly what a mutation run showed."""
    text = _RECORD.read_text(encoding="utf-8")
    opening = text[: text.index("## What was measured")]
    assert "44.8" in opening, "the concentration is not quantified before the first heading"
    assert re.search(r"(?i)without NVDA|excluding NVDA|ex-NVDA", text), (
        "the record does not report the sleeve with the dominant name removed"
    )


def test_every_cost_arm_is_named_with_its_result() -> None:
    """Three arms were declared before the run; a record that quietly drops one is a record
    that chose its comparison after seeing it."""
    text = _RECORD.read_text(encoding="utf-8")
    for arm in ("measured", "keel_today", "crypto_regime"):
        assert f"`{arm}`" in text, f"cost arm {arm} is not named in the record"


def test_the_record_refuses_to_read_as_a_recommendation() -> None:
    """DCA outperforming here is a property of a five-year window containing a historic
    mega-cap run, not evidence that accumulation works."""
    text = _RECORD.read_text(encoding="utf-8").lower()
    assert "not a recommendation" in text or "not advice" in text
