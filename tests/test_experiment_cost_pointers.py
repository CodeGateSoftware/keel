"""An experiment record priced at the flat slippage floor must say where the correction is.

`2026-09-01-per-product-slippage-restatement.md` measured what that floor costs: the model
reaches it only at a $500M/day anchor and **no asset in keel's universe reaches it**, so every
figure produced under it overstates the profit factor — by a median of 0.090 across 120 cells.

That does not change any document's VERDICT. The correction is conservative-only: real cost is
higher, so a corrected profit factor can only fall, and every one of the 120 deltas was negative
or zero. Re-running thirteen documents to move numbers that were already null would be spending
compute to reach the same conclusion.

What it does change is what a reader may conclude by comparing ACROSS documents. A profit factor
of 0.9 in one and 1.06 in another are not on the same scale if both were priced at a rate neither
asset trades at, and a reader has no way to know that from the page. So each such record carries
one line saying so.

**Records are never rewritten** — `docs/experiments` is an append-only account of what was run,
and editing a measured number would falsify it. A pointer is an addition, not a revision.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_EXPERIMENTS = _ROOT / "docs/experiments"

#: The correction every flat-priced record must point at.
RESTATEMENT = "2026-09-01-per-product-slippage-restatement.md"

#: Records that report a profit factor AND state a slippage assumption are in scope. A document
#: that reports neither has nothing to qualify.
_REPORTS_PF = re.compile(r"profit factor|\bPF\b", re.I)
_STATES_SLIPPAGE = re.compile(r"slippage", re.I)


def flat_priced_records() -> list[Path]:
    """Every record whose figures rest on a slippage assumption, excluding the correction."""
    return [
        path
        for path in sorted(_EXPERIMENTS.glob("*.md"))
        if path.name != RESTATEMENT
        and _REPORTS_PF.search(text := path.read_text(encoding="utf-8"))
        and _STATES_SLIPPAGE.search(text)
    ]


def test_there_are_records_in_scope() -> None:
    """A guard on the guard: if the discovery ever matches nothing, every assertion below
    passes vacuously and this file stops meaning anything."""
    assert len(flat_priced_records()) >= 10


def test_every_flat_priced_record_points_at_the_correction() -> None:
    """The pin. A reader comparing profit factors across records has no way to know from the
    page that both were priced at a rate neither asset trades at."""
    missing = [
        path.name
        for path in flat_priced_records()
        if RESTATEMENT not in path.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"{len(missing)} experiment record(s) report a profit factor under a slippage "
        f"assumption without pointing at {RESTATEMENT}:\n  " + "\n  ".join(missing)
    )


def test_the_pointer_says_the_verdict_is_unchanged() -> None:
    """A bare link would read as "this result is wrong". It is not — the correction is
    conservative-only and every verdict here survives it. A pointer that let a reader conclude
    otherwise would be a worse error than the one it corrects.
    """
    for path in flat_priced_records():
        text = path.read_text(encoding="utf-8")
        window = text[max(0, text.find(RESTATEMENT) - 600) : text.find(RESTATEMENT) + 600]
        assert "verdict" in window.lower(), (
            f"{path.name}: the pointer does not say the verdict is unaffected, so it reads as "
            "a retraction of a result that still stands"
        )
