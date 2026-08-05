"""`keel.commands._products` -- rejecting (or flagging) a bad `--products` id at the KEYBOARD.

Rails 18 and 19 stop an inadmissible product where the agent trades it, which is the right place
for a safety rail and the wrong place for a typo. `keel rules seed --products XLM-28AUG26-CDE
--status live` used to write a row the agent would then poll every cycle and veto forever, with
the reason buried in a log line. These tests pin the two questions the CLI asks instead -- the
same two the rails ask, deliberately -- and the fact that it never repairs an id on the
operator's behalf.
"""

from __future__ import annotations

import pytest

from keel.commands._products import (
    SETTLEMENT,
    SHAPE,
    check_product_ids,
    validate_product_ids,
)

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


# -- the two failure KINDS are distinguishable, so callers can weigh them differently ----------
#
# Feasibility study R2, corrected. Both questions are worth asking wherever an operator types an
# id, but they do not carry the same consequence, and a caller that can only string-match the
# message cannot act on the difference:
#
#   SHAPE      -- `BASE-QUOTE` or not. Always a typo. There is no config edit that rescues it,
#                 and no command for which it is a legitimate request.
#   SETTLEMENT -- a real spot pair whose quote leg this deployment does not settle in. Rail 18
#                 vetoes an ORDER for it; `keel fetch` places none, and needs the history before
#                 `assets screen` can say anything about the asset at all.


def test_the_two_failure_kinds_are_reported_separately():
    problems = check_product_ids(["BTC-USD", "XLM-28AUG26-CDE", "BTC-EUR"], _SETTLEMENT)
    assert [(p.product_id, p.kind) for p in problems] == [
        ("XLM-28AUG26-CDE", SHAPE),
        ("BTC-EUR", SETTLEMENT),
    ]


def test_a_clean_list_has_no_problems():
    assert check_product_ids(["BTC-USD", "BTC-USDC"], _SETTLEMENT) == []


def test_shape_is_asked_FIRST_so_a_malformed_id_is_never_reported_as_a_settlement_problem():
    """`quote_currency_of("XLM-28AUG26-CDE")` is `"CDE"`, a perfectly resolvable-looking leg.
    Reporting that as "settles in CDE, widen settlement_currencies" would invite a config edit
    that admits a futures contract -- so the shape question has to come first and stop there."""
    problems = check_product_ids(["XLM-28AUG26-CDE"], _SETTLEMENT)
    assert [p.kind for p in problems] == [SHAPE]


def test_validate_product_ids_still_raises_on_BOTH_kinds():
    """`rules seed` keeps both fatal: it writes a row the agent will poll every cycle, and a rule
    the rails veto forever is not a lesser problem than a typo -- it is a quieter one."""
    for bad in ("XLM-28AUG26-CDE", "BTC-EUR"):
        with pytest.raises(ValueError):
            validate_product_ids([bad], _SETTLEMENT)


def test_every_problem_carries_its_own_reason_string():
    """The message an operator reads is per-id, not per-run, so a caller that reports only some
    of the problems still reports each one in full."""
    problems = check_product_ids(["XLM-28AUG26-CDE", "BTC-EUR"], _SETTLEMENT)
    assert all(p.product_id in p.reason for p in problems)
    assert "not a spot product id" in problems[0].reason
    assert "settles in EUR" in problems[1].reason
