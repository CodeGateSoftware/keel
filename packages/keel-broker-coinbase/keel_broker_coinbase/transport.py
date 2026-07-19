"""Structural transport interface and raw-response helper for the Coinbase adapter.

`Transport` and `_field` are copied verbatim from `keel/data/cb_client.py:45-80`. That
duplication is deliberate and scheduled for deletion: Phase B removes `cb_client.py` entirely.
See the Phase A plan's "Transitional duplication" section -- do not "fix" it by importing across
the two, which would create a transitional import graph on the live order path.

`get_transaction_summary` is new here, not copied: it is the fee/volume endpoint backing
`get_fee_summary`, and `cb_client.py` never needed it.
"""

from __future__ import annotations

from typing import Any, Protocol


class Transport(Protocol):
    """Structural interface the adapter depends on -- matches `coinbase.rest.RESTClient`."""

    def get_candles(
        self, product_id: str, start: str, end: str, granularity: str, **kwargs: Any
    ) -> Any: ...

    def get_product(self, product_id: str, **kwargs: Any) -> Any: ...

    def get_accounts(self, **kwargs: Any) -> Any: ...

    def preview_order(
        self, product_id: str, side: str, order_configuration: dict[str, Any], **kwargs: Any
    ) -> Any: ...

    def create_order(
        self,
        client_order_id: str,
        product_id: str,
        side: str,
        order_configuration: dict[str, Any],
        **kwargs: Any,
    ) -> Any: ...

    def get_transaction_summary(self, **kwargs: Any) -> Any: ...


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from a plain dict OR a `coinbase-advanced-py` `BaseResponse` object.

    Fixtures in tests are plain dicts (real-shaped JSON); the real transport returns
    `BaseResponse` subclasses that expose attributes instead of dict keys. Both support
    dict-style `[]` for present keys, but only dicts have `.get`, so branch on type.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


__all__ = ["Transport", "_field"]
