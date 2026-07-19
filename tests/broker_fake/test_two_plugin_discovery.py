"""The plugin mechanism with one plugin is as much a guess as a port with one adapter."""

from __future__ import annotations

import pytest
from keel_broker_api.registry import discover_brokers, load_broker


def test_both_adapters_are_discovered() -> None:
    assert {"coinbase", "fake"} <= set(discover_brokers())


def test_load_broker_returns_the_right_class_per_venue() -> None:
    assert load_broker("coinbase").__name__ == "CoinbaseAdapter"
    assert load_broker("fake").__name__ == "FakeAdapter"


def test_adapters_declare_different_capabilities() -> None:
    """If these matched, the fake would not be exerting any design pressure."""
    coinbase = load_broker("coinbase")().capabilities()
    fake = load_broker("fake")().capabilities()
    assert coinbase.supported_orders != fake.supported_orders
    assert coinbase.supports_native_preview and not fake.can_preview


def test_unknown_venue_still_raises() -> None:
    with pytest.raises(LookupError):
        load_broker("nonesuch")
