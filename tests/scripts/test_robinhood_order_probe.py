"""Tests for the one script in this repository that deliberately places a live order (#412).

Everything here runs offline. That is not merely convenient: the fences below are the only thing
between a shape probe and a trade, so they are exercised against fakes precisely so they are
exercised at all -- a fence you can only test by placing an order is a fence nobody tests.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from scripts.robinhood_order_probe import (
    _MIN_DISCOUNT,
    OrderProbeRefused,
    _classify_placement_error,
    _OneOrderOnly,
    dumps_venue_json,
    plan_order,
    quantize_to,
    run,
)

_BID = Decimal("69000")


def _plan(**overrides: Any) -> dict[str, Decimal]:
    kwargs: dict[str, Any] = {
        "bid": _BID,
        "base_size": Decimal("0.0001"),
        "discount": Decimal("0.5"),
        "max_notional": Decimal("10"),
        "min_order_amount": Decimal("0.1"),
        "asset_increment": Decimal("0.00000001"),
        "max_order_size": Decimal("20"),
        "quote_increment": Decimal("0.01"),
    }
    kwargs.update(overrides)
    return plan_order(**kwargs)


# --- the fences ---------------------------------------------------------------------------------


def test_a_healthy_plan_prices_far_below_the_bid_and_under_the_cap() -> None:
    plan = _plan()
    assert plan["limit_price"] == Decimal("34500")
    assert plan["notional"] == Decimal("3.45")
    assert plan["discount"] >= _MIN_DISCOUNT


def test_a_discount_under_the_floor_is_refused() -> None:
    """An order that can cross the book is not a probe, it is a trade."""
    with pytest.raises(OrderProbeRefused, match="below the floor"):
        _plan(discount=Decimal("0.01"))


def test_a_notional_over_the_cap_is_refused() -> None:
    """Belt to the discount's braces: the amount at risk stays bounded by a number the operator
    typed, even if the quote were stale or crossed (#413)."""
    with pytest.raises(OrderProbeRefused, match="exceeds --max-notional"):
        _plan(base_size=Decimal("1"))


def test_a_notional_under_the_venue_minimum_is_refused() -> None:
    """`min_order_amount` is quote-denominated (#410). The venue would reject this and the run
    would learn nothing except that we can build a bad body."""
    with pytest.raises(OrderProbeRefused, match="below the venue's min_order_amount"):
        _plan(base_size=Decimal("0.00000001"))


def test_a_size_over_the_venue_maximum_is_refused() -> None:
    with pytest.raises(OrderProbeRefused, match="exceeds the venue's max_order_size"):
        _plan(base_size=Decimal("21"), max_notional=Decimal("100000000"))


def test_a_non_positive_bid_is_refused_rather_than_priced() -> None:
    """A zero bid would price the order at zero, which the venue might well accept."""
    with pytest.raises(OrderProbeRefused, match="non-positive bid"):
        _plan(bid=Decimal("0"))


def test_the_discount_is_rechecked_after_rounding() -> None:
    """Rounding happens against the venue's increments, so the discount that was checked before it
    is not the discount that will be sent. A coarse `quote_increment` is the case that separates
    them -- and the recheck must be against the price that actually goes on the wire."""
    plan = _plan(quote_increment=Decimal("1000"))
    assert plan["limit_price"] == Decimal("34000")
    assert plan["discount"] > _MIN_DISCOUNT


def test_rounding_is_always_downward() -> None:
    """Down, never nearest. Rounding a price UP moves a deliberately-unfillable buy toward the
    book; rounding a size UP spends more than was authorised. Both errors are in the one direction
    this script must not err."""
    assert quantize_to(Decimal("34659.99"), Decimal("0.01")) == Decimal("34659.99")
    assert quantize_to(Decimal("34659.999"), Decimal("0.01")) == Decimal("34659.99")
    assert quantize_to(Decimal("0.000199999"), Decimal("0.0001")) == Decimal("0.0001")


def test_rounding_normalizes_the_venues_padded_increments() -> None:
    """`asset_increment` arrives as `0.000000010000000000`, and inheriting that exponent would
    render `0.0001` onto the wire as `0.000100000000000000`."""
    assert str(quantize_to(Decimal("0.0001"), Decimal("0.000000010000000000"))) == "0.0001"


# --- the one-order guard ------------------------------------------------------------------------


class _StubTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.requests.append((method, path))
        return {"id": "o1"}


def test_a_second_order_creating_post_is_refused_at_the_request_layer() -> None:
    """A retry loop, a bug, or a copy-paste must not be able to turn this into two live orders."""
    guard = _OneOrderOnly(_StubTransport())

    guard._request("POST", "/api/v2/crypto/trading/orders/")
    with pytest.raises(OrderProbeRefused, match="SECOND order-creating request"):
        guard._request("POST", "/api/v2/crypto/trading/orders/")


def test_cancels_and_reads_are_not_capped() -> None:
    """A guard that also fenced the cancel could strand the very order it was protecting against."""
    guard = _OneOrderOnly(_StubTransport())

    guard._request("POST", "/api/v2/crypto/trading/orders/")
    for _ in range(3):
        guard._request("POST", "/api/v2/crypto/trading/orders/o1/cancel/")
        guard._request("GET", "/api/v2/crypto/trading/orders/")
    assert guard.orders_created == 1


def test_the_guard_is_installed_onto_the_transport_not_wrapped_around_it() -> None:
    """The trap `robinhood_smoke._ReadOnly` documents: a `__getattr__` wrapper is bypassed by the
    transport's own internal `self._request` calls, so the guarantee would cover the tests and
    not the probe."""
    transport = _StubTransport()
    guard = _OneOrderOnly(transport)

    assert transport._request == guard._request


# --- classifying a failed placement --------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _HTTPError(Exception):
    def __init__(self, response: _Response) -> None:
        super().__init__(f"{response.status_code} Error")
        self.response = response


def test_a_403_is_reported_as_a_scope_finding_and_certainly_no_order() -> None:
    """Observed twice against the real venue, 2026-08-11 and 2026-08-19."""
    result = _classify_placement_error(_HTTPError(_Response(403, "no permission")), "coid-1")

    assert result["outcome"] == "refused"
    assert "no TRADE scope" in result["note"]


def test_a_5xx_is_reported_as_UNKNOWN_because_the_order_may_exist() -> None:
    """The most important classification in this file. A 5xx is SILENCE, not refusal -- the venue
    may have created the order before the response was lost. Reporting silence as "nothing
    happened" is how a probe leaves a live order behind and tells the operator it did not."""
    result = _classify_placement_error(_HTTPError(_Response(503)), "coid-1")

    assert result["outcome"] == "UNKNOWN"
    assert "coid-1" in result["note"], "the id is the only handle on an order nobody saw"
    assert "do NOT assume nothing happened" in result["note"]


def test_a_connection_failure_with_no_response_is_also_UNKNOWN() -> None:
    """A dropped connection carries no status at all, and is the same silence as a 5xx."""
    result = _classify_placement_error(TimeoutError("connection timed out"), "coid-1")

    assert result["outcome"] == "UNKNOWN"
    assert "TimeoutError" in result["detail"]


# --- the sequence -------------------------------------------------------------------------------


class _SequenceTransport:
    """Answers the adapter's place/observe/cancel calls, recording the order of operations."""

    def __init__(self, *, observe_raises: bool = False) -> None:
        self.ops: list[str] = []
        self._observe_raises = observe_raises

    def create_order(self, body: dict[str, Any]) -> Any:
        self.ops.append("create")
        return {"id": "ord-1", "state": "open"}

    def get_order(self, order_id: str) -> Any:
        self.ops.append("get_order")
        if self._observe_raises and self.ops.count("get_order") == 1:
            raise RuntimeError("venue blipped while observing")
        return {"id": order_id, "state": "canceled"}

    def get_orders(self, updated_at_start: str | None = None) -> Any:
        self.ops.append("get_orders")
        return {"results": []}

    def cancel_order(self, order_id: str) -> Any:
        self.ops.append("cancel")
        return {"id": order_id}


def _plan_for_run() -> dict[str, Decimal]:
    return {"base_size": Decimal("0.0001"), "limit_price": Decimal("34500")}


def test_the_run_places_observes_and_cancels(tmp_path: Any) -> None:
    transport = _SequenceTransport()

    report = run(
        transport, symbol="BTC-USD", plan=_plan_for_run(), idempotency_key="t", out_dir=tmp_path
    )

    assert transport.ops[0] == "create"
    assert "cancel" in transport.ops
    assert report["placed"]["success"] is True
    assert (tmp_path / "rh_order_open_observed.json").exists()


def test_the_cancel_runs_even_when_observation_raises(tmp_path: Any) -> None:
    """The `finally` that matters. An exception between placement and cancellation must not leave
    a live resting order behind -- this process holds the only handle on it."""
    transport = _SequenceTransport(observe_raises=True)

    with pytest.raises(RuntimeError, match="venue blipped"):
        run(
            transport,
            symbol="BTC-USD",
            plan=_plan_for_run(),
            idempotency_key="t",
            out_dir=tmp_path,
        )

    assert "cancel" in transport.ops, "an unobserved order must still be cancelled"


# --- fixture fidelity ----------------------------------------------------------------------------


def test_recorded_decimals_stay_unquoted_and_strings_stay_quoted() -> None:
    """This venue quotes some money fields and not others, in the same object (#217 F6). A dump
    that quoted everything would erase the one distinction the fixtures exist to preserve."""
    text = dumps_venue_json({"unquoted": Decimal("0.00000001"), "quoted": "68329.20"})

    assert '"unquoted": 0.00000001' in text, "no scientific notation, no quotes"
    assert '"quoted": "68329.20"' in text, "trailing zero preserved, still a string"


def test_a_payload_carrying_the_sentinel_is_refused_rather_than_rewritten() -> None:
    """The textual unquoting is only safe while every sentinel in the output is one this module
    put there. A payload that contained one would otherwise be silently corrupted -- into a
    FIXTURE, which is the exact class of wrong claim #414 and #230 were."""
    with pytest.raises(ValueError, match="refusing to rewrite"):
        dumps_venue_json({"evil": "@@keel-decimal:1@@"})


def test_the_float_repr_trap_stays_closed() -> None:
    """`json` renders floats with `float.__repr__`, not `repr(o)`, so a float subclass carrying
    the original text is bypassed and `0.00000001` becomes `1e-08`. This is the regression test
    for that having actually happened here."""
    assert "e-" not in dumps_venue_json({"tiny": Decimal("0.00000001")})
