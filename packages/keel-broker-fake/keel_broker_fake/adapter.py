"""A second `Broker` implementation that deliberately disagrees with Coinbase.

Its purpose is design pressure, not utility. A port with one adapter is a guess: every
Coinbase-ism looks like a universal truth until something else has to satisfy the same
interface. This adapter is that something else, and each of its divergences exists to break one
specific assumption:

| Divergence                        | Assumption it breaks                                     |
|-----------------------------------|----------------------------------------------------------|
| No preview at all                 | That preview is always available                          |
| Base-size-only market orders      | That any venue can size a market order in quote currency  |
| Stops are a resting order + a     | That "a stop is an order type" is universal               |
| separate trigger record           |                                                           |
| Two granularities, 50-candle page | Coinbase's granularity set and pagination limits          |
| No fee summary                    | That every venue reports fees -- lapse detection must     |
|                                   | degrade to attestation alone                              |
| Unknown order id reports FAILED,  | That an unrecognised id raises -- a real venue's REST     |
| never raises                      | transport 404s instead, but the port must not make        |
|                                   | callers catch venue-shaped exceptions from `get_order`    |

All state is in memory. No network, no files, no credentials.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from decimal import Decimal

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder, default_market_schedule
from keel_broker_api.results import (
    Balance,
    CancelOutcome,
    FeeSummary,
    MarketSchedule,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)
from keel_core.types import Candle, Granularity

#: This venue quotes only hourly and daily bars. Anything else it must refuse, not silently
#: approximate -- an adapter returning the wrong timeframe corrupts every downstream indicator.
SUPPORTED_GRANULARITIES = frozenset({Granularity.ONE_HOUR, Granularity.ONE_DAY})

#: Deliberately unlike Coinbase's page size, so any hardcoded 300 shows up as a bug.
MAX_CANDLES_PER_CALL = 50

_CAPABILITIES = BrokerCapabilities(
    venue="fake",
    # `bracket_gtc` is undeclared deliberately, and here the answer is neither "the venue can"
    # nor "the venue cannot" -- there is no venue. This fake stands in for the venues that REFUSE
    # a bracket, which is three of the four real adapters; something has to exercise the refusing
    # half of the conformance contract, and a fake that supported every kind would exercise
    # nothing. Its `place_order` raises `UnsupportedOrder` for anything not listed here.
    supported_orders=frozenset({"market_ioc_base", "limit_gtc", "stop_limit_gtc"}),
    supports_native_preview=False,
    synthesizes_preview=False,
    supports_fee_summary=False,
    quote_currencies=frozenset({"USD"}),
    asset_classes=frozenset({"spot"}),
    session_bound=False,
)


@dataclass(frozen=True)
class _RestingOrder:
    """This venue's internal representation of a placed order."""

    order_id: str
    product_id: str
    kind: str


@dataclass(frozen=True)
class _Trigger:
    """A stop is not an order here -- it is a trigger that *creates* one when it fires."""

    trigger_id: str
    order_id: str
    stop_price: Decimal


class FakeAdapter:
    """An in-memory venue whose shape differs from Coinbase's in five deliberate ways."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.resting: list[_RestingOrder] = []
        self.triggers: list[_Trigger] = []

    def capabilities(self) -> BrokerCapabilities:
        return _CAPABILITIES

    def market_clock(self) -> SessionState:
        """This venue never closes: `SessionState.OPEN` as a constant, no state to consult.

        The 24/7 half of FR-9's split, held by a venue that has no clock endpoint at all --
        which is also the proof the answer costs nothing. There is deliberately no
        "sometimes closed" mode to test with: an adapter that could close would be
        session-bound and would say so, and this one exists to keep that declaration honest.
        """
        return SessionState.OPEN

    def market_schedule(self) -> MarketSchedule:
        """The port's DEFAULT schedule read, verbatim (issue #388 C2): the constant OPEN
        clock answer with NO next_open/next_close. This venue has no clock endpoint at all,
        so there is no calendar to carry -- claiming timestamps would be inventing one."""
        return default_market_schedule(self)

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Return synthetic ascending candles, capped at this venue's page size.

        Raises for an unsupported granularity rather than returning an empty list: a venue that
        cannot serve a timeframe must say so, or the caller reads "no data" as "no trades".
        """
        if granularity not in SUPPORTED_GRANULARITIES:
            supported = ", ".join(sorted(g.value for g in SUPPORTED_GRANULARITIES))
            raise ValueError(
                f"fake venue does not serve granularity {granularity.value!r} "
                f"(supported: {supported})"
            )

        step = 3600 if granularity is Granularity.ONE_HOUR else 86400
        timestamps = range(start_ts, max(start_ts, end_ts), step)
        return [
            Candle(
                ts=ts,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("1"),
            )
            for ts in list(timestamps)[:MAX_CANDLES_PER_CALL]
        ]

    def get_balances(self) -> list[Balance]:
        return [
            Balance(currency="USD", available=Decimal("1000"), total=Decimal("1000")),
            Balance(currency="BTC", available=Decimal("0.5"), total=Decimal("0.75")),
        ]

    def preview_order(self, spec: OrderSpec) -> Preview:
        """This venue has no preview endpoint and does not estimate one.

        `capabilities().can_preview` is false, so callers must gate on it rather than assume a
        preview is obtainable. Synthesising a fake quote here would be worse than refusing: it
        would put an invented number in front of a human at the confirm gate.
        """
        raise NotImplementedError("fake venue offers no order preview")

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        """`idempotency_key` is ACCEPTED and deliberately not acted on.

        This venue has no `client_order_id` to carry it into -- order ids are minted from a
        counter -- so there is nothing here to deduplicate against, and inventing a dedup table
        would make the fake behave better than the real venues it stands in for. Accepting the
        parameter is what matters: the fake must satisfy the same `Broker` signature every other
        adapter does, or it stops being a stand-in for one.
        """
        if spec.kind not in _CAPABILITIES.supported_orders:
            raise UnsupportedOrder(f"fake venue does not support order kind {spec.kind!r}")

        order_id = f"fake-{next(self._ids)}"
        self.resting.append(
            _RestingOrder(order_id=order_id, product_id=spec.product_id, kind=spec.kind)
        )
        if isinstance(spec, StopLimitGTC):
            # A stop is two objects here, not one order type -- but the port still gets exactly
            # one PlaceResult, which is what proves the port is not modelling venue internals.
            self.triggers.append(
                _Trigger(
                    trigger_id=f"trig-{next(self._ids)}",
                    order_id=order_id,
                    stop_price=spec.stop_price,
                )
            )
        return PlaceResult(success=True, broker_order_id=order_id)

    def get_fee_summary(self) -> FeeSummary:
        """This venue reports no fee or volume data.

        Subscription lapse detection must therefore fall back to attestation alone for it --
        which is precisely why `supports_fee_summary` exists as a capability rather than being
        assumed true.
        """
        raise NotImplementedError("fake venue reports no fee summary")

    def get_order(self, order_id: str) -> OrderStatus:
        """Report a resting order as `"OPEN"`; an unknown id as `"FAILED"` -- never raise.

        This venue fills nothing on its own (there is no matching engine here), so a resting
        order's money fields are always zero, never modelled. An unknown id could raise instead,
        but `OrderStatus`'s whole contract is that callers do arithmetic on its money fields
        without special-casing `None` -- special-casing "does this id exist" one layer up would
        just move the same problem, so this returns a normal `OrderStatus` either way.
        """
        for order in self.resting:
            if order.order_id == order_id:
                return OrderStatus(
                    order_id=order_id,
                    status="OPEN",
                    filled_size=Decimal("0"),
                    average_filled_price=Decimal("0"),
                    total_fees=Decimal("0"),
                )
        return OrderStatus(
            order_id=order_id,
            status="FAILED",
            filled_size=Decimal("0"),
            average_filled_price=Decimal("0"),
            total_fees=Decimal("0"),
        )

    def cancel_order(self, order_id: str) -> CancelOutcome:
        """Remove a resting order and its trigger (if any); confirm only what was actually here.

        This venue is synchronous and in-process, so it never produces `ACCEPTED`: removing the
        order IS the settlement. An id it is not holding is `REFUSED` -- the same answer a real
        venue gives for an order that already filled or was never issued.

        A stop is two objects at this venue (`_RestingOrder` + `_Trigger`), so cancelling the
        order without dropping its trigger would leave the trigger free to fire later and place
        a brand new order for a cancellation the caller believes already happened -- this
        venue's version of the naked-position bug `reconcile.py` exists to close.
        """
        before = len(self.resting)
        self.resting = [order for order in self.resting if order.order_id != order_id]
        self.triggers = [t for t in self.triggers if t.order_id != order_id]
        if len(self.resting) < before:
            return CancelOutcome.CONFIRMED
        return CancelOutcome.REFUSED


__all__ = ["MAX_CANDLES_PER_CALL", "SUPPORTED_GRANULARITIES", "FakeAdapter"]
