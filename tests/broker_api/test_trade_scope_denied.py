"""`TradeScopeDenied` -- the venue's half of #233's trade-scope record, as a port-level word.

The record has two writers: the operator attests (`keel scope attest`) and the VENUE falsifies.
This exception is the only channel the second writer has. Everything pinned here is about that
one job: it must be distinguishable from every other placement failure, because the executor
turns it -- and nothing else -- into a `REFUTED` row that vetoes live entries until a human
re-attests.
"""

from __future__ import annotations

import inspect

from keel_broker_api import port
from keel_broker_api.port import TradeScopeDenied, UnsupportedOrder


def test_it_is_a_sibling_of_unsupported_order_not_a_subclass_of_it() -> None:
    """A permission refusal is not an unsupported order kind, and the two must not be catchable
    as one another.

    `UnsupportedOrder` means "this adapter cannot express this spec" -- a fact about the ADAPTER,
    fixed by sending a different spec. `TradeScopeDenied` means "this credential may not trade" --
    a fact about the CREDENTIAL, fixed only by a human at a terminal. If either subclassed the
    other, `except UnsupportedOrder` sites (which the port's own docstring tells callers never to
    retry) would start swallowing refusals the record is supposed to learn from.
    """
    assert issubclass(TradeScopeDenied, Exception)
    assert not issubclass(TradeScopeDenied, UnsupportedOrder)
    assert not issubclass(UnsupportedOrder, TradeScopeDenied)
    assert TradeScopeDenied.__bases__ == (Exception,)


def test_the_venue_s_own_words_survive_as_the_message() -> None:
    """The refusal's message is written verbatim into `venue_trade_scopes.refuted_reason` and
    read back by `doctor` and `keel scope show`. The 2026-08-19 incident's whole cost was that
    the venue's answer was thrown away, so the one thing this exception must not do is paraphrase
    it."""
    exc = TradeScopeDenied("You do not have permission to perform this action.")
    assert str(exc) == "You do not have permission to perform this action."


def test_the_port_exports_it() -> None:
    """`__all__` is what an adapter author reads to learn the port's error vocabulary. A word the
    executor branches on that is not exported is a word nobody will map."""
    assert "TradeScopeDenied" in port.__all__


def test_it_is_not_a_protocol_method() -> None:
    """#233 rules out a `credential_scope()` port method explicitly: three of five adapters could
    only ever answer UNKNOWN (kraken is a stub with no network path at all), which is the dead
    gate `keel/capabilities.py` records having been built and deleted once already. This is an
    error-vocabulary addition and must stay one, so the `Broker` surface is pinned unchanged
    against a future edit that "helpfully" adds the method back."""
    methods = {
        name
        for name, _ in inspect.getmembers(port.Broker, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {
        "capabilities",
        "market_clock",
        "market_schedule",
        "get_candles",
        "get_balances",
        "get_instrument",
        "preview_order",
        "place_order",
        "get_fee_summary",
        "get_order",
        "cancel_order",
    }
