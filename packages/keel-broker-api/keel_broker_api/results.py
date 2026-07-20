"""Domain types crossing the port in the broker-to-engine direction.

These replace the raw dicts today's `cb_client` returns: `get_accounts() -> list[dict]` probed at
`executor.py:168`, and `place_order`'s dict probed via `place_result.get("success")` at
`executor.py:345`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from keel_core.types import Side


@dataclass(frozen=True)
class Balance:
    """One currency's balance on the venue."""

    currency: str
    available: Decimal
    total: Decimal


@dataclass(frozen=True)
class Preview:
    """What the human approves at the confirm gate (`executor.py:311`).

    `synthetic=True` means these numbers are an estimate the adapter computed, not a quote the
    broker returned. Anything rendering a Preview must surface that distinction: approving an
    estimate must never look identical to approving a broker's own quote.
    """

    product_id: str
    side: Side
    est_base_size: Decimal
    est_quote_size: Decimal
    est_fee: Decimal
    synthetic: bool
    detail: Mapping[str, str] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaceResult:
    """Outcome of a placement attempt."""

    success: bool
    broker_order_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class OrderStatus:
    """Observed state of a previously placed order. Money fields default to `Decimal("0")`
    rather than `None` -- callers do arithmetic on them and must never special-case."""

    order_id: str
    status: str
    filled_size: Decimal
    average_filled_price: Decimal
    total_fees: Decimal


@dataclass(frozen=True)
class FeeSummary:
    """Fees and volume the venue reports for this account.

    Its consumer is subscription lapse detection: Coinbase exposes no subscription endpoint, so
    the engine cannot read a user's tier -- but a fee charged while the user claims a fee-free
    allowance contradicts the claim. See the subscription design spec.

    `volume_window` is explicit because Coinbase's window could not be determined from their
    docs. An adapter that does not know says "unknown", and reconciliation then uses only
    `fees_usd` -- which is window-independent for that test. This field exists so the engine can
    never silently compare a trailing-30-day figure against a calendar-month cap.
    """

    venue: str
    taker_rate: Decimal
    maker_rate: Decimal
    volume_usd: Decimal
    fees_usd: Decimal
    volume_window: str
    fetched_at: int

    def __post_init__(self) -> None:
        allowed = {"trailing_30d", "calendar_month", "unknown"}
        if self.volume_window not in allowed:
            raise ValueError(f"volume_window must be one of {sorted(allowed)}")


__all__ = ["Balance", "FeeSummary", "OrderStatus", "PlaceResult", "Preview"]
