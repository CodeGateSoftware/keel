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

All state is in memory. No network, no files, no credentials.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from decimal import Decimal

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import Balance, FeeSummary, PlaceResult, Preview
from keel_core.types import Candle, Granularity

#: This venue quotes only hourly and daily bars. Anything else it must refuse, not silently
#: approximate -- an adapter returning the wrong timeframe corrupts every downstream indicator.
SUPPORTED_GRANULARITIES = frozenset({Granularity.ONE_HOUR, Granularity.ONE_DAY})

#: Deliberately unlike Coinbase's page size, so any hardcoded 300 shows up as a bug.
MAX_CANDLES_PER_CALL = 50

_CAPABILITIES = BrokerCapabilities(
    venue="fake",
    supported_orders=frozenset({"market_ioc_base", "limit_gtc", "stop_limit_gtc"}),
    supports_native_preview=False,
    synthesizes_preview=False,
    supports_fee_summary=False,
    quote_currencies=frozenset({"USD"}),
    asset_classes=frozenset({"spot"}),
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

    def place_order(self, spec: OrderSpec) -> PlaceResult:
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


__all__ = ["MAX_CANDLES_PER_CALL", "SUPPORTED_GRANULARITIES", "FakeAdapter"]
