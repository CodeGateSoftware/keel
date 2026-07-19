"""Entry-point discovery for broker adapters.

Adapters are separate distributions registering under the `keel.brokers` group, so installing a
venue is `uv add keel-broker-<venue>` -- no core change, no rebuild.

Security note: entry points execute arbitrary code. When engines run server-side, only
first-party adapters may be installed. See the design spec's trust-boundary section.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

ENTRY_POINT_GROUP = "keel.brokers"


def discover_brokers() -> dict[str, Any]:
    """Map venue name to the adapter class registered under `keel.brokers`."""
    return {ep.name: ep.load() for ep in entry_points(group=ENTRY_POINT_GROUP)}


def load_broker(venue: str) -> Any:
    """Return the adapter class registered for `venue`, or raise `LookupError`."""
    found = discover_brokers()
    if venue not in found:
        available = ", ".join(sorted(found)) or "none installed"
        raise LookupError(f"no broker adapter registered for {venue!r} (available: {available})")
    return found[venue]


__all__ = ["ENTRY_POINT_GROUP", "discover_brokers", "load_broker"]
