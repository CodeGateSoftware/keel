"""`_build_broker` calls the coinbase posture check, not just the alpaca one (#666).

The adapter having a correct `verify_cash_account` proves nothing about anything calling it —
a compliance gate wired to nothing is a gate that exists only in a docstring. Three separate
sessions this week shipped a helper whose call site was unpinned and whose removal left every
other test green; this file is the call site's pin.
"""

from __future__ import annotations

from typing import Any

import pytest

from keel.commands import _common


class _Adapter:
    """A coinbase-shaped adapter that records whether the seam asked about its posture."""

    __module__ = "keel_broker_coinbase.adapter"

    def __init__(self, transport: Any, refuse: Exception | None = None) -> None:
        self.transport = transport
        self._refuse = refuse
        self.verified = False

    def verify_cash_account(self) -> None:
        self.verified = True
        if self._refuse is not None:
            raise self._refuse


def _build(monkeypatch, refuse: Exception | None = None):
    """Patch the two seams the coinbase branch reaches for: the entry-point registry (imported
    INSIDE `_build_broker`, so it must be patched on its own module) and the SDK client it
    constructs from `.env` secrets."""
    import coinbase.rest
    from keel_broker_api import registry

    holder: dict[str, _Adapter] = {}

    def factory(transport: Any, **_kwargs: Any) -> _Adapter:
        holder["adapter"] = _Adapter(transport, refuse)
        return holder["adapter"]

    factory.__module__ = "keel_broker_coinbase.adapter"
    monkeypatch.setattr(registry, "load_broker", lambda _venue: factory)
    monkeypatch.setattr(coinbase.rest, "RESTClient", lambda **_kwargs: object())
    monkeypatch.setattr("keel.config.load_secrets", lambda: {"api_key": "k", "api_secret": "s"})
    return holder


def test_the_seam_verifies_the_coinbase_posture(monkeypatch) -> None:
    """One `get_portfolios` read per build, beside Alpaca's one `/v2/account` read — the same
    place, for the same reason, so no command can build a broker that skipped it."""
    holder = _build(monkeypatch)

    _common._build_broker(_config())

    assert holder["adapter"].verified is True, (
        "_build_broker built a coinbase broker without asking about its account posture"
    )


def test_a_refusal_propagates_out_of_the_build(monkeypatch) -> None:
    """Refusing at BUILD, not at order time: guards are broker-less by design, and a per-order
    raise could fire on an exit path where a refusal traps a position."""
    from keel_broker_coinbase.adapter import CashAccountRequired

    _build(monkeypatch, refuse=CashAccountRequired("INTX portfolio"))

    with pytest.raises(CashAccountRequired, match="INTX"):
        _common._build_broker(_config())


def _config() -> Any:
    class _Broker:
        name = "coinbase"
        endpoint = None
        data_feed = None

    class _Config:
        broker = _Broker()

    return _Config()
