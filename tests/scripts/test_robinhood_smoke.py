"""Tests for the read-only Robinhood probe script.

The script is the one thing in this repository designed to run against live credentials at a
venue with no sandbox, so its read-only guarantee and its credential pre-checks are the parts
worth pinning. Everything here runs offline with a stub transport.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import nacl.signing
import pytest

from scripts.robinhood_smoke import (
    PROBES,
    ReadOnlyViolation,
    _ReadOnly,
    annotations_in,
    compare_shapes,
    fixture_shape,
    load_credentials,
    report,
    run_probes,
    shape_of,
)

#: Raw bytes behind the test seed. Any 32 bytes is a valid Ed25519 seed.
_SEED_RAW = bytes(range(32))

#: A REAL base64 Ed25519 seed, not 44 arbitrary characters. It has to be real now: the guard
#: derives a public key from it, and `"A" * 44` is not valid base64 for 32 bytes at all -- it
#: decodes to 33, which is the sort of thing a constant named `_VALID_SEED_B64` should not be.
_VALID_SEED_B64 = base64.b64encode(bytes(_SEED_RAW)).decode()

#: The public key that seed derives to -- the exact value an operator pastes into Robinhood's
#: credential page, and the exact value that must never appear in `ROBINHOOD_API_KEY`.
_ITS_PUBLIC_KEY_B64 = base64.b64encode(
    bytes(nacl.signing.SigningKey(bytes(_SEED_RAW)).verify_key)
).decode()

#: Stands in for a real `rh-api-<uuid>`: the shape observed on the one credential known to
#: authenticate. Not base64, which is the property the guard keys off.
_VALID_API_KEY = "rh-api-1e2d3c4b-5a69-4788-9f01-23456789abcd"

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


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

    def get_orders(self, updated_at_start: str | None = None) -> Any:
        return self._request("GET", "/api/v2/crypto/trading/orders/")


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
    results = run_probes(guard, "BTC-USD")
    assert guard.calls, "probes issued no requests at all"
    assert {method for method, _ in guard.calls} == {"GET"}
    # Every probe must also have SUCCEEDED, not merely have issued a GET. `run_probes` records a
    # failure per probe rather than aborting, so a probe calling a transport method that does not
    # exist is swallowed into `{"ok": False}` -- which the read-only assertion above cannot see.
    # Adding the `orders` probe (#197) hit exactly that: it was silently inert here until the stub
    # grew a `get_orders`, and a probe nothing exercises is a probe that can rot unnoticed.
    assert {name for name, result in results.items() if not result["ok"]} == set()
    assert set(results) == {name for name, _ in PROBES}


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


def test_a_list_is_summarised_by_the_union_of_its_elements_not_by_the_first() -> None:
    """#230 D1: the key the second element carries has to appear in the summary."""
    shape = shape_of([{"symbol": "BILL-USD"}, {"symbol": "BTC-USD", "min_order_amount": "0.1"}])
    assert shape == [{"symbol": "str", "min_order_amount (1/2)": "str"}, "... 2 items"]


def test_a_partially_present_key_is_marked_with_its_count() -> None:
    """The live 63/26 split, rendered the way an operator reads it.

    The count is what makes partial presence *visible* rather than either silently dropped (which
    is what the old first-element summary did) or asserted as universal (which would be a
    different lie: 26 pairs really do lack this key).
    """
    pairs = [{"symbol": f"P{i}-USD"} for i in range(26)]
    pairs += [{"symbol": f"Q{i}-USD", "min_order_amount": "0.1"} for i in range(63)]

    element = shape_of(pairs)[0]

    assert element["min_order_amount (63/89)"] == "str"
    assert "min_order_amount" not in element, "the bare key would claim every pair carries it"


def test_an_89_element_list_still_prints_one_shape_and_a_count() -> None:
    """Summarising the whole list must not mean rendering the whole list."""
    shape = shape_of([{"symbol": f"P{i}-USD"} for i in range(89)])
    assert shape == [{"symbol": "str"}, "... 89 items"]


def test_elements_that_type_the_same_key_differently_are_not_silently_collapsed() -> None:
    """⚠️ This venue quotes the same kind of value two ways in one object (#217 F6).

    Taking the first element's type would hide exactly the ambiguity #197 turns on. The merged
    token names both types and tallies them, and -- because it is not equal to either half -- it
    reports as a `TYPE DIFFERS` against a fixture that can only state one.
    """
    shape = shape_of([{"fee_charged": "0.01"}, {"fee_charged": Decimal("0.01")}])

    assert shape[0]["fee_charged"].startswith("Decimal|str")
    assert "1 str" in shape[0]["fee_charged"] and "1 Decimal" in shape[0]["fee_charged"]

    diffs = compare_shapes(shape, shape_of([{"fee_charged": "0.01"}]))
    assert len(diffs) == 1
    assert "TYPE DIFFERS" in diffs[0]


def test_nested_objects_inside_list_elements_are_merged_too() -> None:
    """A key two levels down is as invisible to a first-element sample as a top-level one."""
    element = shape_of(
        [
            {"tier": {"fee_ratio": "0.006"}},
            {"tier": {"fee_ratio": "0.006", "next_fee_tier_ratio": "0.004"}},
        ]
    )[0]
    assert element["tier"] == {"fee_ratio": "str", "next_fee_tier_ratio (1/2)": "str"}


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


def test_a_key_only_a_LATER_element_carries_is_still_reported() -> None:
    """⚠️ #230 D1, the defect this whole change exists for, as one executable claim.

    `shape_of` used to reduce a list to `[shape_of(value[0]), "... N items"]`, so every probe
    validated ONE element and reported a match for the whole collection. Live, `trading_pairs`
    returns 89 pairs in two distinct key-sets: 63 carry `min_order_amount` (BTC-USD and ETH-USD
    among them) and 26 do not -- and `results[0]` is BILL-USD, one of the 26. The probe therefore
    reported 5/5 and then 6/6 matched while blind to a field present on 71% of pairs, including
    every asset keel trades, and #218 deleted that field from the fixture on the strength of it.

    A field carried by SOME elements is an ordinary API shape, not an anomaly. The probe must
    model it, which starts with seeing it at all.
    """
    live = shape_of([{"symbol": "BTC-USD"}, {"symbol": "ETH-USD", "min_order_amount": "0.1"}])
    fixture = shape_of([{"symbol": "BTC-USD"}])

    diffs = compare_shapes(live, fixture)

    assert len(diffs) == 1, f"a key only the second element carries went unreported: {diffs}"
    assert "NEW AT VENUE" in diffs[0]
    assert "min_order_amount" in diffs[0]


def test_a_partially_present_key_matches_a_fixture_that_carries_it() -> None:
    """The other half of the #230 D1 decision, and the reason it is not simply "flag everything".

    A fixture is ONE representative object, so it cannot express "63 of 89". The convention chosen
    here is that the fixture carries the UNION of what a row can hold -- `rh_trading_pairs.json`'s
    row is BTC-USD, and BTC-USD is sent `min_order_amount` -- and the count reaches the operator as
    a note rather than as a difference. Treating partial presence as a mismatch instead would make
    every run of the `trading_pairs` probe fail against a venue behaving exactly as documented,
    which is the cry-wolf failure #217 F5 already taught this script to avoid.
    """
    live = shape_of([{"symbol": "BILL-USD"}, {"symbol": "BTC-USD", "min_order_amount": "0.1"}])
    fixture = shape_of([{"symbol": "BTC-USD", "min_order_amount": "0.1"}])

    assert compare_shapes(live, fixture) == []


def test_the_presence_count_is_reported_as_a_note_not_as_a_difference() -> None:
    """A clean match is only honest read next to "and 26 of the 89 rows lacked that key"."""
    live = shape_of({"results": [{"symbol": "BILL-USD"}, {"symbol": "BTC-USD", "min_order": "1"}]})

    notes = annotations_in(live)

    assert notes == ["  note: results[].min_order  present on 1/2 elements"]


def test_a_mixed_type_is_noted_as_well_as_reported() -> None:
    notes = annotations_in(shape_of({"results": [{"fee": "0.01"}, {"fee": Decimal("0.01")}]}))
    assert len(notes) == 1
    assert "results[].fee" in notes[0]
    assert "mixed types" in notes[0]


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


def test_a_misshapen_private_key_is_caught_before_a_request(tmp_path: Path) -> None:
    """A PEM, a hex string or a truncated paste must fail here, not as a 401 that reads like a
    signing bug."""
    env = _write_env(
        tmp_path, f"ROBINHOOD_API_KEY={_VALID_API_KEY}\nROBINHOOD_PRIVATE_KEY=tooshort\n"
    )
    with pytest.raises(SystemExit) as excinfo:
        load_credentials(env)
    message = str(excinfo.value)
    assert "8 characters" in message
    # ...and it no longer CLAIMS to have caught a public key, which it cannot do by length.
    assert "does NOT mean the public key" in message


def test_the_public_key_pasted_as_the_api_key_is_named_exactly(tmp_path: Path) -> None:
    """The real 2026-08-19 incident, and the whole reason this guard was rewritten.

    A seed and a public key are both 32 bytes and both 44 base64 characters, so no length check
    can separate them -- the old guard's comment claimed it did, and the operator got a bare 401
    instead. Deriving the public key from the seed is the only test that actually distinguishes
    them, and it turns an afternoon of "is it the key, the clock, or the signature?" into one
    line naming the file, the variable and the fix.
    """
    env = _write_env(
        tmp_path,
        f"ROBINHOOD_API_KEY={_ITS_PUBLIC_KEY_B64}\nROBINHOOD_PRIVATE_KEY={_VALID_SEED_B64}\n",
    )
    with pytest.raises(SystemExit) as excinfo:
        load_credentials(env)
    message = str(excinfo.value)
    assert "PUBLIC key of ROBINHOOD_PRIVATE_KEY" in message
    # The fix has to be actionable without a round trip to figure out what to do.
    assert "robinhood.com/account/crypto" in message
    assert "Add key" in message
    assert "ROBINHOOD_PRIVATE_KEY stays as it is" in message


def test_an_unrelated_ed25519_key_in_the_api_key_slot_is_still_refused(tmp_path: Path) -> None:
    """The same mistake made with a DIFFERENT credential's public key. It cannot be named as
    precisely -- the guard has nothing to match it against -- but a 32-byte base64 value is never
    an API key identifier whatever the identifier format turns out to be, so it must not pass."""
    other = base64.b64encode(bytes(range(100, 132))).decode()  # not _SEED_RAW, not its public key
    env = _write_env(
        tmp_path, f"ROBINHOOD_API_KEY={other}\nROBINHOOD_PRIVATE_KEY={_VALID_SEED_B64}\n"
    )
    with pytest.raises(SystemExit) as excinfo:
        load_credentials(env)
    assert "Ed25519 KEY" in str(excinfo.value)


def test_a_real_api_key_is_not_mistaken_for_base64(tmp_path: Path) -> None:
    """`rh-api-<uuid>` contains `-`, which is outside the base64 alphabet. Without
    `validate=True`, `b64decode` DISCARDS such characters instead of refusing, and a genuine API
    key could decode to 32 bytes by accident and be rejected as a pasted key. This is the test
    that pins the strict decode."""
    env = _write_env(
        tmp_path, f"ROBINHOOD_API_KEY={_VALID_API_KEY}\nROBINHOOD_PRIVATE_KEY={_VALID_SEED_B64}\n"
    )
    assert load_credentials(env) == (_VALID_API_KEY, _VALID_SEED_B64)


def test_a_wellformed_credential_is_returned(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path, f"ROBINHOOD_API_KEY={_VALID_API_KEY}\nROBINHOOD_PRIVATE_KEY={_VALID_SEED_B64}\n"
    )
    assert load_credentials(env) == (_VALID_API_KEY, _VALID_SEED_B64)


# --- probes and fixtures agree ---------------------------------------------------------------


def test_every_probe_names_a_fixture_that_exists() -> None:
    """A renamed fixture must break here, not halfway through a live run."""
    for name, fixture_name in PROBES:
        assert (_FIXTURES / fixture_name).is_file(), f"{name} points at a missing {fixture_name}"


@pytest.mark.parametrize(("probe", "fixture_name"), PROBES)
def test_a_probe_response_matches_its_fixture_after_pagination(
    probe: str, fixture_name: str
) -> None:
    """The false positive #217 F5 reports, pinned so it cannot come back.

    Every one of the five probes goes through `RobinhoodTransport._paginate`, which resolves the
    cursor and hands back `{"results": [...]}` -- `next` and `previous` are consumed there and
    never reach a caller, by design. The fixtures are single RAW pages and still carry both. The
    script compared the two directly, so the very first live run reported `next` and `previous`
    `MISSING AT VENUE` on all five probes: five differences, on a clean run, against a venue that
    sends both fields. A report that cries wolf every time is worse than no report, because the
    real findings in that same run (F1 through F4) had to be read past it.

    This simulates the venue answering each fixture as its single page and asserts the comparison
    the script actually performs is clean. It fails on both halves of the bug: an unstripped
    envelope, and a fixture that has genuinely drifted from what the adapter reads.
    """
    payload = json.loads((_FIXTURES / fixture_name).read_text(), parse_float=Decimal)
    after_pagination = {"results": payload["results"]}

    diffs = compare_shapes(shape_of(after_pagination), fixture_shape(_FIXTURES / fixture_name))

    assert diffs == [], f"{probe} reports differences against its own fixture: {diffs}"


def test_a_fixture_number_is_decoded_the_way_the_live_transport_decodes_it() -> None:
    """An unquoted fixture number (#217 F2) must not be shape-reported as a `float`.

    `RobinhoodTransport._request` parses with `parse_float=Decimal`, so a live unquoted `64975.78`
    reaches `shape_of` as a `Decimal`. A fixture decoded with a plain `json.loads` reaches it as a
    `float`, and the script would then report `TYPE DIFFERS ... fixture='float' venue='Decimal'`
    on every unquoted money field of every probe -- the same cry-wolf failure as F5, in a
    different place.
    """
    shape = fixture_shape(_FIXTURES / "rh_estimated_price.json")
    assert shape["results"][0]["ask"] == "Decimal"


def test_a_quoted_fixture_value_is_still_reported_as_a_string() -> None:
    """The other half of #217 F6: this venue quotes SOME money and not others.

    `parse_float=Decimal` must not be mistaken for "everything becomes a `Decimal`". It converts
    JSON numbers only, so a quoted `"0.00000001"` stays a `str` -- which is what `trading_pairs`
    and `best_bid_ask` actually send. If this ever reported `Decimal`, the fixtures would have
    been normalized to a uniformity the venue does not have, and the probe would report
    `TYPE DIFFERS` against a live run.
    """
    assert fixture_shape(_FIXTURES / "rh_trading_pairs.json")["results"][0]["asset_increment"] == (
        "str"
    )
    assert fixture_shape(_FIXTURES / "rh_best_bid_ask.json")["results"][0]["bid"] == "str"


def test_the_pagination_envelope_is_stripped_only_from_a_paginated_shape() -> None:
    """`next`/`previous` are dropped because `_paginate` consumes them -- not because the keys are
    unwelcome. A payload with no `results` (an order object) is left exactly as it is, so a real
    key named `next` on some future endpoint would still be compared rather than silently eaten."""
    assert fixture_shape(_FIXTURES / "rh_order_open.json")["state"] == "str"
    assert "next" not in fixture_shape(_FIXTURES / "rh_accounts.json")


def _trading_pair(symbol: str, *, minimum: bool) -> dict[str, Any]:
    """One row shaped like the venue's, with or without the key 26 of the 89 pairs omit."""
    row: dict[str, Any] = {
        "symbol": symbol,
        "asset_code": symbol.split("-")[0],
        "quote_code": "USD",
        "asset_increment": "0.00000001",
        "quote_increment": "0.01",
        "max_order_size": "20.0000000000000000",
        "status": "tradable",
        "is_api_tradable": True,
    }
    if minimum:
        row["min_order_amount"] = "0.1"
    return row


def _live_shaped_results(fixture_name: str) -> Any:
    """A fixture replayed as the post-pagination payload a probe actually hands to `shape_of`."""
    payload = json.loads((_FIXTURES / fixture_name).read_text(), parse_float=Decimal)
    return {"results": payload["results"]}


def test_the_real_63_of_89_split_matches_the_fixture_and_is_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """⚠️ #230 end to end, against the split measured live: 63 pairs with, 26 without.

    This is the run that has to come out right, and both halves of "right" are asserted:

    * **exit 0 and no differences.** The restored `min_order_amount` in `rh_trading_pairs.json`
      (BTC-USD's real `0.1`) is what earns that -- delete it again and this probe reports
      `NEW AT VENUE`, which is how #218 would have been caught.
    * **the 63/89 printed.** A bare "shape matches" over a collection whose rows differ is the
      overstatement that started all of this. The count is the difference between "the probe
      checked this" and "the probe checked `results[0]`".

    `results[0]` is deliberately BILL-USD, the pair the live venue returns first and one of the 26
    that lack the key -- the exact ordering that made the old summary wrong.
    """
    pairs = [_trading_pair("BILL-USD", minimum=False)]
    pairs += [_trading_pair(f"P{i}-USD", minimum=False) for i in range(25)]
    pairs += [_trading_pair(sym, minimum=True) for sym in ("BTC-USD", "ETH-USD")]
    pairs += [_trading_pair(f"Q{i}-USD", minimum=True) for i in range(61)]
    assert len(pairs) == 89
    assert sum("min_order_amount" in pair for pair in pairs) == 63

    results = {
        name: {"ok": True, "shape": shape_of(_live_shaped_results(fixture_name))}
        for name, fixture_name in PROBES
    }
    results["trading_pairs"] = {"ok": True, "shape": shape_of({"results": pairs})}

    exit_code = report(results, as_json=False)

    out = capsys.readouterr().out
    assert exit_code == 0, f"a venue behaving exactly as measured must not fail the probe:\n{out}"
    assert "shape matches rh_trading_pairs.json" in out
    assert "note: results[].min_order_amount  present on 63/89 elements" in out
    assert "all 6 probes matched their fixtures." in out
    # One summary row, not 89: the report has to stay readable at the venue's real page count.
    assert out.count("min_order_amount") == 1


def test_the_fixture_carries_the_field_218_removed() -> None:
    """The regression itself, pinned where the probe would meet it (#230 D2).

    Without this, the live 63/89 rows would report `NEW AT VENUE results[].min_order_amount` on
    every run -- a real difference against a fixture that dropped a field the venue sends.
    """
    pair = json.loads((_FIXTURES / "rh_trading_pairs.json").read_text())["results"][0]
    assert pair["symbol"] == "BTC-USD", "the row has to be a pair that CARRIES the minimum"
    assert pair["min_order_amount"] == "0.1"


def test_a_failing_probe_does_not_abort_the_others() -> None:
    class _Exploding(_StubTransport):
        def get_best_bid_ask(self, symbol: str) -> Any:
            raise RuntimeError("401 unauthorized")

    results = run_probes(_ReadOnly(_Exploding()), "BTC-USD")
    assert results["best_bid_ask"]["ok"] is False
    assert "401 unauthorized" in results["best_bid_ask"]["error"]
    assert results["accounts"]["ok"] is True
