"""`BrokerCapabilities` -- an adapter's self-declaration, checked for sense at construction.

`supported_orders` has been checked against `ORDER_KINDS` since the port landed; `asset_classes`
was a free-form set nobody validated. A declaration nothing checks is a comment with a type
annotation, and it is the field a future broker-port migration will gate spot-only on -- so the
vocabulary is pinned now, while the field is still cheap to change (feasibility study R2).
"""

from __future__ import annotations

import pytest
from keel_broker_api.capabilities import ASSET_CLASSES, BrokerCapabilities


def _caps(**overrides: object) -> BrokerCapabilities:
    base: dict[str, object] = dict(
        venue="test",
        supported_orders=frozenset({"market_ioc_quote"}),
        supports_native_preview=True,
        synthesizes_preview=False,
        supports_fee_summary=True,
        quote_currencies=frozenset({"USD"}),
        asset_classes=frozenset({"spot"}),
    )
    base.update(overrides)
    return BrokerCapabilities(**base)  # type: ignore[arg-type]


def test_the_vocabulary_is_the_three_classes_the_venue_study_enumerated() -> None:
    """`SPOT`, `FUTURE` and `EQUITY` are what Coinbase lists. A set that drifts from the
    vocabulary adapters declare against is how a typo becomes a silently permissive gate."""
    assert ASSET_CLASSES == frozenset({"spot", "futures", "equity"})


def test_a_declaration_of_spot_is_accepted() -> None:
    assert _caps().asset_classes == frozenset({"spot"})


@pytest.mark.parametrize("bogus", ["margin_spot", "SPOT", "perp", "", "future"])
def test_an_unknown_asset_class_is_refused_at_construction(bogus: str) -> None:
    """Mirrors the `supported_orders` check: an adapter cannot invent a class the engine has no
    vocabulary for. `SPOT` and `future` are the near-misses that would otherwise pass silently."""
    with pytest.raises(ValueError) as excinfo:
        _caps(asset_classes=frozenset({bogus}))
    assert bogus in str(excinfo.value)


def test_the_unknown_order_kind_check_still_fires() -> None:
    """The new check must not shadow the one that was already there."""
    with pytest.raises(ValueError) as excinfo:
        _caps(supported_orders=frozenset({"iceberg"}))
    assert "iceberg" in str(excinfo.value)
