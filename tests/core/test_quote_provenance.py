"""`keel_core.quote_provenance` -- WHERE an order preview's price came from (#715).

`keel.commands.confirm._read_preview` already classifies a preview into the same four cases
for its own banner rendering: unreadable, synthetic, unpriced, and a clean venue quote. This
module gives those four cases a MACHINE-READABLE token, so `orders.quote_provenance` (written
by `keel.execution.executor`) and the banner sentence a human reads at the confirm gate come
from the exact same classification and can never say two different things about one preview.

Pure and dependency-free on purpose -- see the module docstring for the import-boundary reason
(`keel.execution` must not import `keel.commands.confirm`, which pulls in `click`) AND for why
this module duck-types rather than importing `keel_broker_api.results.Preview`: `keel-broker-api`
already depends on `keel-core` (see its `pyproject.toml`), so the reverse import would be a
circular PACKAGE dependency, not just an awkward one.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core.quote_provenance import (
    SYNTHETIC_ESTIMATE,
    UNPRICED,
    UNREADABLE,
    VENUE_QUOTED,
    provenance_of,
)


class _FakePreview:
    """A duck-typed stand-in for `keel_broker_api.results.Preview` -- this module must classify
    on shape alone, never on the real class, or it could not stay dependency-free."""

    def __init__(self, *, synthetic: bool, est_base_size: Decimal, est_quote_size: Decimal):
        self.synthetic = synthetic
        self.est_base_size = est_base_size
        self.est_quote_size = est_quote_size


def _preview(*, synthetic=False, base=Decimal("0.001"), quote=Decimal("50")) -> _FakePreview:
    return _FakePreview(synthetic=synthetic, est_base_size=base, est_quote_size=quote)


def test_a_native_priced_preview_is_venue_quoted():
    assert provenance_of(_preview()) == VENUE_QUOTED


def test_a_synthetic_preview_is_synthetic_estimate_even_if_sized():
    """`synthetic` dominates: keel's own estimate is never recorded as a venue quote, however
    plausible the numbers look."""
    assert provenance_of(_preview(synthetic=True)) == SYNTHETIC_ESTIMATE


def test_a_zero_quote_size_is_unpriced():
    assert provenance_of(_preview(quote=Decimal("0"))) == UNPRICED


def test_a_zero_base_size_is_unpriced():
    assert provenance_of(_preview(base=Decimal("0"))) == UNPRICED


def test_synthetic_wins_over_unpriced():
    """A preview that is BOTH synthetic and unpriced is recorded as synthetic -- the token
    describes WHERE the price came from, and 'nowhere real' is worth recording even when the
    size could not be determined either."""
    assert (
        provenance_of(_preview(synthetic=True, base=Decimal("0"), quote=Decimal("0")))
        == SYNTHETIC_ESTIMATE
    )


def test_none_is_unreadable():
    assert provenance_of(None) == UNREADABLE


def test_a_shape_with_no_synthetic_flag_is_unreadable():
    """The pre-port dict shape (or anything else that isn't preview-shaped) is unreadable --
    exactly the case `keel.commands.confirm._read_preview` also refuses to interpret."""
    assert provenance_of({"est_quote_size": Decimal("50")}) == UNREADABLE


def test_the_four_tokens_are_distinct_strings():
    tokens = {UNREADABLE, SYNTHETIC_ESTIMATE, UNPRICED, VENUE_QUOTED}
    assert len(tokens) == 4
    assert all(isinstance(t, str) for t in tokens)
