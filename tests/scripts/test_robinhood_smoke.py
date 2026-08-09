"""Tests for the read-only Robinhood probe script.

The script is the one thing in this repository designed to run against live credentials at a
venue with no sandbox, so its read-only guarantee and its credential pre-checks are the parts
worth pinning. Everything here runs offline with a stub transport.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.robinhood_smoke import (
    PROBES,
    ReadOnlyViolation,
    _ReadOnly,
    compare_shapes,
    load_credentials,
    run_probes,
    shape_of,
)

_VALID_SEED_B64 = "A" * 44  # a base64 32-byte Ed25519 seed is 44 characters


class _StubTransport:
    """Records requests and replays canned payloads. Never touches the network."""

    def __init__(self, payload: Any = None) -> None:
        self.payload = payload if payload is not None else {"results": [{"a": 1}]}
        self.seen: list[tuple[str, str]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.seen.append((method, path))
        return self.payload

    def get_accounts(self) -> Any:
        return self._request("GET", "/api/v2/crypto/trading/accounts/")

    def get_trading_pairs(self) -> Any:
        return self._request("GET", "/api/v2/crypto/trading/trading_pairs/")

    def get_best_bid_ask(self, symbol: str) -> Any:
        return self._request("GET", "/api/v2/crypto/marketdata/best_bid_ask/")

    def get_estimated_price(self, symbol: str, side: str, quantity: str) -> Any:
        return self._request("GET", "/api/v2/crypto/trading/estimated_price/")

    def get_holdings(self) -> Any:
        return self._request("GET", "/api/v2/crypto/trading/holdings/")


# --- the read-only guarantee ---------------------------------------------------------------


def test_non_get_requests_are_refused() -> None:
    """The whole point of the script: it must be structurally unable to place an order."""
    guard = _ReadOnly(_StubTransport())
    with pytest.raises(ReadOnlyViolation, match="read-only by construction"):
        guard._request("POST", "/api/v2/crypto/trading/orders/")


@pytest.mark.parametrize("method", ["post", "PUT", "delete", "PATCH"])
def test_every_mutating_verb_is_refused_case_insensitively(method: str) -> None:
    """A lowercase `post` must not slip past a naive `!= "POST"` comparison."""
    guard = _ReadOnly(_StubTransport())
    with pytest.raises(ReadOnlyViolation):
        guard._request(method, "/api/v2/crypto/trading/orders/")


def test_a_refused_request_is_not_recorded_as_issued() -> None:
    guard = _ReadOnly(_StubTransport())
    with pytest.raises(ReadOnlyViolation):
        guard._request("POST", "/orders/")
    assert guard.calls == []


def test_get_requests_pass_through_and_are_recorded() -> None:
    stub = _StubTransport()
    guard = _ReadOnly(stub)
    guard._request("GET", "/api/v2/crypto/trading/accounts/")
    assert guard.calls == [("GET", "/api/v2/crypto/trading/accounts/")]
    assert stub.seen == [("GET", "/api/v2/crypto/trading/accounts/")]


def test_running_every_probe_issues_only_gets() -> None:
    guard = _ReadOnly(_StubTransport())
    run_probes(guard, "BTC-USD")
    assert guard.calls, "probes issued no requests at all"
    assert {method for method, _ in guard.calls} == {"GET"}


# --- shapes --------------------------------------------------------------------------------


def test_shape_discards_every_leaf_value() -> None:
    """Balances must never reach the terminal -- only their types."""
    shape = shape_of({"buying_power": "1234.56", "count": 7, "live": True, "next": None})
    assert shape == {"buying_power": "str", "count": "int", "live": "bool", "next": "null"}
    assert "1234.56" not in json.dumps(shape)


def test_a_long_list_collapses_to_one_element_and_a_count() -> None:
    shape = shape_of([{"symbol": "BTC-USD"}, {"symbol": "ETH-USD"}, {"symbol": "SOL-USD"}])
    assert shape == [{"symbol": "str"}, "... 3 items"]


def test_an_empty_list_is_reported_rather_than_silently_matching() -> None:
    """An unfunded account returns `[]`; that must be visible, not a vacuous pass."""
    assert shape_of([]) == ["<empty>"]


# --- shape comparison ----------------------------------------------------------------------


def test_a_key_the_venue_does_not_send_is_reported() -> None:
    """The dangerous direction: a field the fixture invented and the adapter may already read."""
    diffs = compare_shapes({"a": "str"}, {"a": "str", "fee_tier_status": {"fee_ratio": "str"}})
    assert len(diffs) == 1
    assert "MISSING AT VENUE" in diffs[0]
    assert "fee_tier_status" in diffs[0]


def test_a_key_only_the_venue_sends_is_reported() -> None:
    diffs = compare_shapes({"a": "str", "fees_paid": "str"}, {"a": "str"})
    assert len(diffs) == 1
    assert "NEW AT VENUE" in diffs[0]
    assert "fees_paid" in diffs[0]


def test_a_type_change_is_reported_with_both_sides() -> None:
    diffs = compare_shapes({"quantity": "float"}, {"quantity": "str"})
    assert len(diffs) == 1
    assert "TYPE DIFFERS" in diffs[0]
    assert "quantity" in diffs[0]


def test_identical_shapes_produce_no_differences() -> None:
    shape = shape_of({"results": [{"account_number": "x", "buying_power": "1.00"}]})
    assert compare_shapes(shape, shape) == []


def test_differences_are_found_inside_list_elements() -> None:
    live = shape_of([{"symbol": "BTC-USD"}])
    fixture = shape_of([{"symbol": "BTC-USD", "min_order_amount": "1.00"}])
    diffs = compare_shapes(live, fixture)
    assert len(diffs) == 1
    assert "min_order_amount" in diffs[0]


# --- credentials ---------------------------------------------------------------------------


def _write_env(tmp_path: Path, body: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(body)
    return env


def test_a_missing_private_key_names_it_and_explains_why_it_is_needed(tmp_path: Path) -> None:
    """The exact case an operator hits after adding only the API key."""
    env = _write_env(tmp_path, "ROBINHOOD_API_KEY=abc\n")
    with pytest.raises(SystemExit) as excinfo:
        load_credentials(env)
    message = str(excinfo.value)
    assert "ROBINHOOD_PRIVATE_KEY" in message
    assert "signs EVERY request" in message


def test_a_missing_api_key_is_named(tmp_path: Path) -> None:
    env = _write_env(tmp_path, f"ROBINHOOD_PRIVATE_KEY={_VALID_SEED_B64}\n")
    with pytest.raises(SystemExit, match="ROBINHOOD_API_KEY"):
        load_credentials(env)


def test_a_public_key_pasted_as_the_private_key_is_caught_before_a_request(
    tmp_path: Path,
) -> None:
    """Wrong-length seeds must fail here, not as a 401 that reads like a signing bug."""
    env = _write_env(tmp_path, "ROBINHOOD_API_KEY=abc\nROBINHOOD_PRIVATE_KEY=tooshort\n")
    with pytest.raises(SystemExit) as excinfo:
        load_credentials(env)
    assert "PUBLIC key" in str(excinfo.value)


def test_a_wellformed_credential_is_returned(tmp_path: Path) -> None:
    env = _write_env(tmp_path, f"ROBINHOOD_API_KEY=abc\nROBINHOOD_PRIVATE_KEY={_VALID_SEED_B64}\n")
    assert load_credentials(env) == ("abc", _VALID_SEED_B64)


# --- probes and fixtures agree ---------------------------------------------------------------


def test_every_probe_names_a_fixture_that_exists() -> None:
    """A renamed fixture must break here, not halfway through a live run."""
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    for name, fixture_name in PROBES:
        assert (fixtures / fixture_name).is_file(), f"{name} points at a missing {fixture_name}"


def test_a_failing_probe_does_not_abort_the_others() -> None:
    class _Exploding(_StubTransport):
        def get_best_bid_ask(self, symbol: str) -> Any:
            raise RuntimeError("401 unauthorized")

    results = run_probes(_ReadOnly(_Exploding()), "BTC-USD")
    assert results["best_bid_ask"]["ok"] is False
    assert "401 unauthorized" in results["best_bid_ask"]["error"]
    assert results["accounts"]["ok"] is True
