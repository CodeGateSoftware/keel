from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.results import FeeSummary


def _summary(window: str) -> FeeSummary:
    return FeeSummary(
        venue="coinbase",
        taker_rate=Decimal("0.012"),
        maker_rate=Decimal("0.006"),
        volume_usd=Decimal("1234.56"),
        fees_usd=Decimal("0"),
        volume_window=window,
        fetched_at=1_700_000_000,
    )


@pytest.mark.parametrize("window", ["trailing_30d", "calendar_month", "unknown"])
def test_accepts_every_legal_window(window: str) -> None:
    assert _summary(window).volume_window == window


def test_rejects_an_unknown_window() -> None:
    """An undeclarable window would let the engine compare mismatched periods silently."""
    with pytest.raises(ValueError, match="volume_window must be one of"):
        _summary("monthly")


def test_unknown_is_a_legal_declaration_not_an_error() -> None:
    """Coinbase's window is undocumented; "unknown" must be sayable, not a failure."""
    assert _summary("unknown").volume_window == "unknown"
