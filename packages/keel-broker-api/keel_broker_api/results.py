"""Domain types crossing the port in the broker-to-engine direction.

These replace the raw dicts today's `cb_client` returns: `get_accounts() -> list[dict]` probed at
`executor.py:168`, and `place_order`'s dict probed via `place_result.get("success")` at
`executor.py:345`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from keel_core.types import Side


class SessionState(str, Enum):
    """The venue's market clock, as `market_clock()` answers it (FR-9).

    Equities are not 24/7: a weekend or holiday is "market closed", never "feed stale", and
    the only honest source for that answer is the venue's own clock/calendar endpoints -- a
    locally maintained calendar drifts. Three members, not two, because "the clock could not
    be read" is a different fact from "the venue says closed": `CLOCK_UNAVAILABLE` is the
    fail-closed answer a session-bound adapter returns when the clock read fails, so a caller
    can log WHY it is treating the session as shut rather than guessing.

    Venues that are not session-bound never produce anything but `OPEN`: a 24/7 venue has no
    clock to read, and answering with a constant keeps crypto adapters network-free here.
    """

    OPEN = "open"
    CLOSED = "closed"
    CLOCK_UNAVAILABLE = "clock_unavailable"


class CancelOutcome(str, Enum):
    """What a venue actually said when asked to cancel one resting order (#412).

    This replaced a `bool`, and the reason is a real observation rather than a taste for enums.
    Robinhood's cancel endpoint answers `200` with the order still reading `open`: the request
    was accepted and the matching engine settles it about a second later. A boolean has one word
    for that and for "already filled, refused" -- `False` -- so a cancel that had in fact landed
    was reported as `exchange did not confirm cancellation ... it may still be live`. That is not
    a wording problem. On a deployment that cycles once a day, an exit that waits for a cancel
    it believes failed waits a DAY, and the log says the position is at risk when it is not.

    Four members, because the venues genuinely say four different things and the caller genuinely
    wants to log them differently:

    * `CONFIRMED` -- the venue states THIS order is terminal-cancelled. Alpaca's `204`,
      Robinhood's `state: canceled`, Coinbase's per-order `success: true`. Only this permits a
      caller to act as though the order can no longer consume inventory.
    * `ACCEPTED` -- the venue took the request and has not settled it yet. Robinhood's `open`
      after a `200`. **Not a failure**, and specifically not the same fact as a refusal.
    * `REFUSED` -- the venue declined: already filled, already terminal, or an id it never
      issued. Coinbase's `success: false`, Alpaca's `404`/`422`.
    * `UNKNOWN` -- nothing could be established. A 5xx, a timeout, a dropped connection, an
      answer with no row for this id. Distinguished from `REFUSED` because "the venue said no"
      and "the venue said nothing" are different facts, and only one of them is about the order.

    **Only `CONFIRMED` is safe to act on**, and `settled` says so in one place so no caller has
    to re-derive it. The other three all mean "this order may still be resting", which is the
    belief that keeps the engine watching it -- and the reconciliation poll at the top of every
    cycle (`keel.execution.reconcile.reconcile_open_orders`) is what establishes the terminal
    state for all of them.
    """

    CONFIRMED = "confirmed"
    ACCEPTED = "accepted"
    REFUSED = "refused"
    UNKNOWN = "unknown"

    @property
    def settled(self) -> bool:
        """Whether the venue has stated this order can no longer consume inventory.

        The one question a caller on the exit path is actually asking. It is deliberately NOT
        "did the cancel succeed": an `ACCEPTED` cancel will almost certainly succeed, and acting
        on that near-certainty is what places a second order against inventory the venue still
        has committed."""
        return self is CancelOutcome.CONFIRMED


def coerce_cancel_outcome(value: object) -> CancelOutcome:
    """Read an adapter's answer, tolerating the `bool` this used to be.

    An out-of-tree adapter written against the old contract still returns `True`/`False`, and the
    mapping is the conservative one: `True` meant "confirmed" and still does; `False` meant
    "not confirmed, fail closed" and becomes `REFUSED`, which fails closed identically. Anything
    else -- `None`, a string, a mistake -- is `UNKNOWN`, never a confirmation. There is no input
    to this function that turns an unconfirmed cancel into a confirmed one.
    """
    if isinstance(value, CancelOutcome):
        return value
    if value is True:
        return CancelOutcome.CONFIRMED
    if value is False:
        return CancelOutcome.REFUSED
    return CancelOutcome.UNKNOWN


@dataclass(frozen=True)
class MarketSchedule:
    """The venue's market clock WITH its schedule: `market_clock()`'s session state plus the
    NEXT OPEN and NEXT CLOSE timestamps, where the venue provides them (issue #388 C2, the
    console session banner's port read).

    Two halves, deliberately one value object rather than an extended `SessionState`:

    * `state` is the same three-member answer `market_clock()` gives, with the same
      fail-closed semantics -- `CLOCK_UNAVAILABLE` means "the clock could not be read",
      never a guess.
    * `next_open_ts`/`next_close_ts` are EXTRA facts, not guarantees: epoch seconds when the
      venue's own clock endpoint states them, `None` when it does not. A 24/7 venue answers
      the port's default (OPEN, both `None`) -- synthesizing timestamps for a market with no
      calendar would be inventing the locally-maintained calendar FR-9 forbids. A
      session-bound venue whose clock body omits or mangles them also answers `None` for the
      unusable field: the session state stands on its own, and a schedule nobody vouches
      for is dropped rather than guessed.
    """

    state: SessionState
    next_open_ts: int | None = None
    next_close_ts: int | None = None


@dataclass(frozen=True)
class Balance:
    """One currency's balance on the venue."""

    currency: str
    available: Decimal
    total: Decimal


@dataclass(frozen=True)
class Instrument:
    """One tradeable product's venue-imposed granularity.

    **Why this exists at all.** `executor._base_increment_for` (#516) needs the finest `base_size`
    a venue will accept, and reads it today by calling `broker.list_products()` and picking
    through raw dicts for `product_id` and `base_increment`. That is the pre-port
    `CoinbaseClient`'s shape, and it is one of the two gaps #524 names as the reason the live path
    cannot move onto the port: the port had no catalog read at all.

    **One product, not the catalogue, and that is the caller's own argument.** `list_products`
    returns every product the venue lists -- about 900 on Coinbase -- and
    `_base_increment_for` caches exactly ONE of them per miss, because `Repository.set_state`
    commits per call and caching all of them would mean ~900 fsyncs inside the order-placement
    path. A port method shaped like the caller's need lets an adapter ask the venue for one
    product where the venue supports that, and filter locally where it does not.

    **Only `base_increment`, for now.** Quote-side granularity and minimum sizes are the same
    class of fact and would sit here naturally, but nothing reads them yet, and a field no caller
    reads is a field no test meaningfully checks.
    """

    product_id: str
    #: The venue's finest acceptable `base_size` for this product. Always positive: an adapter
    #: that cannot obtain a usable value returns `None` from `get_instrument` rather than
    #: constructing an Instrument carrying zero, which a caller would quantize against and get
    #: a division error or a silent zero size.
    base_increment: Decimal

    def __post_init__(self) -> None:
        if self.base_increment <= 0:
            raise ValueError(
                f"base_increment must be positive, got {self.base_increment} for "
                f"{self.product_id}"
            )


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


__all__ = [
    "Balance",
    "Instrument",
    "CancelOutcome",
    "FeeSummary",
    "MarketSchedule",
    "OrderStatus",
    "PlaceResult",
    "Preview",
    "SessionState",
    "coerce_cancel_outcome",
]
