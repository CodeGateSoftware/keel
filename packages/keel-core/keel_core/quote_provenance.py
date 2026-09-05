"""WHERE an order preview's price came from (#715) -- one machine-readable token per case.

`keel.commands.confirm._read_preview` already classifies a preview into the cases that decide
which banner sentence a human reads at the confirm gate: it could not be read at all, it is
keel's own synthetic estimate, it is readable but carries no usable size, or it is a clean quote
the venue itself priced. This module gives those same cases a TOKEN -- the word
`keel.execution.executor` writes into `orders.quote_provenance` -- so the value recorded in the
database and the sentence rendered on screen share one vocabulary: `confirm.py`'s four banner
constants are looked up FROM these tokens rather than written a second time.

**What that does and does not buy, stated precisely because the first draft of this docstring
overclaimed it.** It guarantees the two never use different WORDS for the same case. It does not
make them one classifier: `confirm._read_preview` decides independently, and it must, because a
screen can show several alarms at once (a synthetic preview that is also unpriced renders both
banners) while this column holds exactly one value. So `provenance_of` applies a PRECEDENCE --
documented below -- to pick the single token that best describes a preview, and
`test_the_recorded_token_and_the_rendered_banner_correspond` pins the two against each other on
the rendered sentence, not on the constant.

**Dependency-free by construction, and that is load-bearing, not tidiness.** `provenance_of`
duck-types its argument instead of importing `keel_broker_api.results.Preview`: `keel-broker-api`
already depends on `keel-core` (its `pyproject.toml` lists `keel-core` under `dependencies`), so
importing `Preview` HERE would be a circular package dependency, not merely an unwanted one. Duck
typing on `synthetic`/`est_base_size`/`est_quote_size` costs nothing -- those are exactly the
fields `Preview` guarantees -- and keeps this module reachable from both `keel.execution.executor`
(which must not import `keel.commands.confirm`, since that pulls in `click`) and `confirm.py`
itself, with neither importing the other.

Four tokens, matching confirm.py's four banner constants one for one (`NATIVE_PREVIEW_MARKER` /
`SYNTHETIC_PREVIEW_MARKER` / `UNPRICED_PREVIEW_MARKER` / `UNREADABLE_PREVIEW_MARKER`) -- NOT a
fifth for `Preview.errors`. Errors are an orthogonal fact ("something the adapter or venue said
went wrong"), rendered as its own alarm block that can appear ALONGSIDE any of the four banners
above (a preview can be a genuine venue quote that also carries errors); provenance answers a
different question -- WHERE the price came from -- so it does not fork on that axis.
"""

from __future__ import annotations

from typing import Any

#: The preview could not be interpreted as a preview at all -- not `Preview`-shaped (missing the
#: `synthetic`/size fields), `None`, or the pre-port dict shape `confirm.py`'s `_read_preview`
#: also refuses to read. Corresponds to `UNREADABLE_PREVIEW_MARKER`.
UNREADABLE = "unreadable"

#: keel's adapter computed these numbers from a price lookup; the venue has not priced,
#: validated or reserved anything (`Preview.synthetic=True`). Corresponds to
#: `SYNTHETIC_PREVIEW_MARKER`.
SYNTHETIC_ESTIMATE = "synthetic_estimate"

#: Readable, non-synthetic, but either size leg is <= 0 -- NOT a zero-cost order, an order whose
#: cost could not be determined. Corresponds to `UNPRICED_PREVIEW_MARKER`.
UNPRICED = "unpriced"

#: A readable, non-synthetic preview with a determined size -- the venue priced this order
#: itself. Corresponds to `NATIVE_PREVIEW_MARKER`. Recorded even when `Preview.errors` is
#: non-empty; see the module docstring for why errors do not get their own token.
VENUE_QUOTED = "venue_quoted"


def provenance_of(preview: Any) -> str:
    """Classify `preview` into exactly one of the four tokens above.

    Mirrors `keel.commands.confirm._read_preview`'s own precedence -- unreadable first (so a
    shape with no `synthetic` flag can never be misread as a confident zero), then synthetic
    (which wins over unpriced: a fabricated estimate is worth recording as fabricated even when
    its size also could not be determined -- see `test_synthetic_wins_over_unpriced`), then
    unpriced, and only a preview with none of those is `VENUE_QUOTED`.
    """
    synthetic = getattr(preview, "synthetic", None)
    if not isinstance(synthetic, bool):
        return UNREADABLE
    if synthetic:
        return SYNTHETIC_ESTIMATE
    base_size = getattr(preview, "est_base_size", None)
    quote_size = getattr(preview, "est_quote_size", None)
    if base_size is None or quote_size is None:
        return UNREADABLE
    try:
        unpriced = quote_size <= 0 or base_size <= 0
    except (ArithmeticError, TypeError):
        # A size that cannot be COMPARED to zero cannot be interpreted, which is what
        # `UNREADABLE` means. `Decimal("NaN") <= 0` raises `InvalidOperation` -- and this
        # function is called from `executor._order_row`, which runs BEFORE `place_order`, so
        # before this returned instead of raising, one malformed field in a venue's preview
        # could abort a placement. `executor._safe_provenance` wraps this too; the belt is here
        # because a pure classifier that can raise is a hazard for its next caller as well.
        return UNREADABLE
    if unpriced:
        return UNPRICED
    return VENUE_QUOTED


__all__ = [
    "SYNTHETIC_ESTIMATE",
    "UNPRICED",
    "UNREADABLE",
    "VENUE_QUOTED",
    "provenance_of",
]
