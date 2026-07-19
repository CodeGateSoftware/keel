from __future__ import annotations

import pytest
from keel_broker_api.registry import discover_brokers, load_broker


def test_discover_brokers_returns_a_mapping() -> None:
    """Real adapters are registered by later tasks; the call itself must work with none."""
    assert isinstance(discover_brokers(), dict)


def test_load_broker_rejects_unknown_venue() -> None:
    with pytest.raises(LookupError, match="no broker adapter registered for 'nonesuch'"):
        load_broker("nonesuch")
