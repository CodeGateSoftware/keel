"""What a venue can do, declared by its adapter and checked before the engine sizes an order."""

from __future__ import annotations

from dataclasses import dataclass

from keel_broker_api.orders import ORDER_KINDS

#: The instrument classes an adapter may declare -- the three the 2026-08-05 Coinbase
#: asset-class study enumerated at the venue (`SPOT`, `FUTURE`, `EQUITY`;
#: `docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`). A closed vocabulary for
#: the same reason `ORDER_KINDS` is one: a declaration checked against nothing is a comment with
#: a type annotation, and the near-misses (`SPOT`, `future`, `perp`) are exactly the values that
#: would sit in a set gating nothing.
ASSET_CLASSES: frozenset[str] = frozenset({"spot", "futures", "equity"})


@dataclass(frozen=True)
class BrokerCapabilities:
    """An adapter's self-declaration. The conformance suite verifies it does not lie.

    ⚠️ `asset_classes` is **not** what keeps keel spot-only today, and no engine code reads it.
    The spot gate on the live path is **rail 19 (`spot_instrument`)** in
    `keel/execution/guards.py`, which checks the product id's shape and needs no broker handle.
    That is deliberate, not an oversight: `guards.check` has no broker, the only broker the live
    path constructs is `keel/data/cb_client.py`'s `CoinbaseClient` -- which has no
    `capabilities()` at all -- and the paper path passes `broker=None`, so a gate built on this
    field would be dead code on every real path while reading as a defence. That exact pattern
    was built and deleted once already (R1's "what was deliberately NOT shipped").

    This field's job until then is to keep the declaration honest and checkable, so the
    broker-port migration that makes `capabilities()` reachable inherits a vocabulary rather
    than a free-form set. At that point the reconciliation belongs at LOAD time, not as a
    per-order raise -- a raise on the exit path can trap a position.
    """

    venue: str
    supported_orders: frozenset[str]
    supports_native_preview: bool
    synthesizes_preview: bool
    supports_fee_summary: bool
    quote_currencies: frozenset[str]
    asset_classes: frozenset[str]

    def __post_init__(self) -> None:
        unknown = self.supported_orders - ORDER_KINDS
        if unknown:
            raise ValueError(f"unknown order kinds: {sorted(unknown)}")
        unknown_classes = self.asset_classes - ASSET_CLASSES
        if unknown_classes:
            raise ValueError(f"unknown asset classes: {sorted(unknown_classes)}")

    @property
    def can_preview(self) -> bool:
        """Whether `confirm` mode is usable against this venue at all."""
        return self.supports_native_preview or self.synthesizes_preview


__all__ = ["ASSET_CLASSES", "BrokerCapabilities"]
