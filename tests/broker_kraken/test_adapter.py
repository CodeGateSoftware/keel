"""The kraken stub, pinned to its two promises: port-complete, and honest about it (#313).

`keel_broker_kraken` exists so the venue can grow behind the `Broker` port without a
big-bang landing. The surface is agreed, nothing behind it is -- and the stub must never
drift toward pretending otherwise. These tests are what keep it honest in both directions:

* port-COMPLETE -- every `Broker` method exists, so the real implementation fills in a
  declared surface instead of inventing one method at a time;
* stub-HONEST -- every data/market method raises `NotImplementedError` with the one stub
  message, the capabilities claim nothing the code cannot honour, and the constructor
  accepts no credentials (there is no key handling and no network path to Kraken at all).
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from keel_broker_api.orders import MarketIOCByQuote
from keel_broker_kraken import KrakenAdapter
from keel_core.types import Granularity, Side

#: The single message every unimplemented method carries -- surfacing the issue number at
#: the first call, wherever that call comes from.
_STUB_MESSAGE = "kraken adapter is a stub — #313"

#: Every method of `keel_broker_api.port.Broker`, spelled here so a method added to the
#: port later fails this file until the stub grows it (or raises inside it) -- the surface
#: cannot drift silently in either direction.
_PORT_SURFACE = (
    "capabilities",
    "market_clock",
    "market_schedule",
    "get_candles",
    "get_balances",
    "preview_order",
    "place_order",
    "get_fee_summary",
    "get_order",
    "cancel_order",
)

_SPEC = MarketIOCByQuote(product_id="XBT-USD", side=Side.BUY, quote_size=Decimal("100"))


def _calls(adapter: KrakenAdapter) -> dict[str, object]:
    """One zero-argument thunk per port method (the two order methods included)."""
    return {
        "market_clock": adapter.market_clock,
        "market_schedule": adapter.market_schedule,
        "get_candles": lambda: adapter.get_candles("XBT-USD", Granularity.ONE_DAY, 0, 1),
        "get_balances": adapter.get_balances,
        "preview_order": lambda: adapter.preview_order(_SPEC),
        "place_order": lambda: adapter.place_order(_SPEC),
        "get_fee_summary": adapter.get_fee_summary,
        "get_order": lambda: adapter.get_order("O-ABC-123"),
        "cancel_order": lambda: adapter.cancel_order("O-ABC-123"),
    }


def test_the_port_surface_is_complete() -> None:
    """Every `Broker` method exists on the adapter -- the surface cannot drift from the port."""
    missing = [name for name in _PORT_SURFACE if not callable(getattr(KrakenAdapter, name, None))]
    assert not missing, f"kraken stub is missing port methods: {missing}"
    # And the stub's callable set is exactly the port's, so the two lists above are checked
    # against a real adapter, not a typo in `_calls`.
    assert set(_calls(KrakenAdapter())) == set(_PORT_SURFACE) - {"capabilities"}


def test_every_data_and_market_method_raises_not_implemented() -> None:
    """The stub-honest half: each call raises `NotImplementedError` carrying the one message
    that names the issue -- never an empty answer that reads as a venue's answer."""
    calls = _calls(KrakenAdapter())
    for name, call in calls.items():
        with pytest.raises(NotImplementedError, match=r"stub") as as_expected:
            call()
        assert _STUB_MESSAGE in str(as_expected.value), name


def test_capabilities_claim_nothing_the_stub_cannot_honour() -> None:
    """The declaration is the conservative floor: no orders, no preview, no fees, no quote
    currencies. The only claims are about the VENUE (crypto spot, 24/7), never the code."""
    cap = KrakenAdapter().capabilities()
    assert cap.venue == "kraken"
    assert cap.session_bound is False  # crypto venue: trades 24/7
    assert cap.asset_classes == frozenset({"spot"})  # a crypto spot venue
    assert cap.supported_orders == frozenset()  # the stub places nothing
    assert cap.can_preview is False  # neither native nor synthesized
    assert cap.supports_fee_summary is False
    assert cap.quote_currencies == frozenset()  # nothing verified, nothing claimed


def test_the_constructor_takes_no_credentials() -> None:
    """No key handling, structurally: the constructor's signature has no parameters, so there
    is no credential-shaped surface to fill in -- the stub holds nothing it could leak."""
    params = list(inspect.signature(KrakenAdapter).parameters)
    assert params == [], (
        "the kraken stub's constructor must take no arguments (#313: no key handling until "
        f"there is an implementation to hand them to); found: {params}"
    )
