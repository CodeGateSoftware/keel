"""`keel.commands._products.validate_product_ids` -- rejecting a bad id at the KEYBOARD.

Rails 18 and 19 stop an inadmissible product where the agent trades it, which is the right place
for a safety rail and the wrong place for a typo. `keel rules seed --products XLM-28AUG26-CDE
--status live` used to write a row the agent would then poll every cycle and veto forever, with
the reason buried in a log line. These tests pin the two questions the CLI asks instead -- the
same two the rails ask, deliberately -- and the fact that it never repairs an id on the
operator's behalf.
"""

from __future__ import annotations

import pytest

from keel.commands._products import validate_product_ids

_SETTLEMENT = frozenset({"USD", "USDC"})


def test_well_formed_settled_ids_are_returned_unchanged():
    ids = ["BTC-USD", "ETH-USD", "PAXG-USD", "ADA-USD", "XLM-USD", "BTC-USDC"]
    assert validate_product_ids(ids, _SETTLEMENT) == ids


@pytest.mark.parametrize(
    "bad",
    [
        "XLM-28AUG26-CDE",  # futures: two hyphens
        "BTC-PERP-USD",  # the R2 residual: derivative-shaped, USD-settled
        "ac568fb9e6c5a67da94f065a49fb7b0c59b7b258cfdf0a3b1560849071c3b05e",  # equity hash
        "BTCUSD",
        "BTC-",
        "-USD",
        "BTC--USD",
        "BTC/USD",
    ],
)
def test_an_id_that_is_not_a_spot_pair_is_rejected_on_SHAPE(bad):
    with pytest.raises(ValueError) as excinfo:
        validate_product_ids([bad], _SETTLEMENT)
    assert bad in str(excinfo.value)
    assert "not a spot product id" in str(excinfo.value)


def test_a_well_formed_pair_in_an_unconfigured_currency_is_rejected_on_SETTLEMENT():
    """`BTC-EUR` is a real Coinbase spot pair and passes the shape check. Rail 18 would veto it
    on every cycle; the operator should hear that now, and hear WHICH check refused it."""
    with pytest.raises(ValueError) as excinfo:
        validate_product_ids(["BTC-EUR"], _SETTLEMENT)
    message = str(excinfo.value)
    assert "BTC-EUR" in message
    assert "settles in EUR" in message
    assert "USD" in message and "USDC" in message


def test_the_settlement_set_is_the_operators_not_a_hardcode():
    """Widening `settlement_currencies` in config admits `-EUR` here too -- the CLI must ask the
    same question rail 18 asks, of the same set, or the two disagree."""
    assert validate_product_ids(["BTC-EUR"], frozenset({"EUR"})) == ["BTC-EUR"]
    with pytest.raises(ValueError):
        validate_product_ids(["BTC-USD"], frozenset({"EUR"}))


@pytest.mark.parametrize("lower", ["btc-USD", "btc-usd", "BTC-usd", "Btc-Usd"])
def test_a_lowercase_id_is_REJECTED_with_a_hint_never_silently_uppercased(lower):
    """Silently repairing it would mean the id the operator typed is not the id keel trades.

    A product id is a venue identifier, not free text; guessing at one is how a typo becomes a
    position. The hint costs one line and keeps the operator in charge of the fix.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_product_ids([lower], _SETTLEMENT)
    message = str(excinfo.value)
    assert lower in message
    assert f"did you mean {lower.upper()}" in message


def test_every_bad_id_is_reported_at_once_not_just_the_first():
    """An operator fixing a list one error per run learns the list slowly and gives up fast."""
    with pytest.raises(ValueError) as excinfo:
        validate_product_ids(["BTC-USD", "XLM-28AUG26-CDE", "BTC-EUR", "eth-usd"], _SETTLEMENT)
    message = str(excinfo.value)
    assert "XLM-28AUG26-CDE" in message
    assert "BTC-EUR" in message
    assert "eth-usd" in message


def test_an_empty_list_is_not_an_error_here():
    """Emptiness is a different complaint, owned by the caller that knows what empty means for
    it -- `_parse_products_option` falls back to the allowlist rather than validating nothing."""
    assert validate_product_ids([], _SETTLEMENT) == []


def test_it_never_raises_anything_but_ValueError():
    """`click.BadParameter` wrapping at the call sites depends on this: an unexpected exception
    type would surface as a traceback instead of a usage error."""
    for weird in ([None], [42], [""], ["   "], [b"BTC-USD"]):
        with pytest.raises(ValueError):
            validate_product_ids(weird, _SETTLEMENT)
