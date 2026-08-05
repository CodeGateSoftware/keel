"""`keel_core.products` -- deriving an order's settlement leg, and its SHAPE, from its id.

The currency an order spends is a property of the PRODUCT, never of global config. Getting this
wrong let rail 13 guard a balance the order never touches.

`parse_spot_product_id` answers the other half of the question -- not "what does it settle in"
but "is this the shape of a spot pair at all" -- which is what rail 19 gates on. The two are
deliberately separate functions: `quote_currency_of`'s loose `rpartition` parse is what lets rail
18 name `CDE` in its message, and tightening it would change that message for no gain.
"""

from __future__ import annotations

import pytest
from keel_core.config import _CURRENCY_CODE_RE
from keel_core.products import is_spot_base_code, parse_spot_product_id, quote_currency_of


def test_the_quote_leg_is_the_part_after_the_last_dash():
    assert quote_currency_of("BTC-USD") == "USD"
    assert quote_currency_of("BTC-USDC") == "USDC"
    assert quote_currency_of("PAXG-USD") == "USD"


def test_it_is_case_normalised():
    assert quote_currency_of("eth-usd") == "USD"


def test_a_malformed_product_id_returns_None_so_callers_fail_closed():
    """`None` is what rail 13 already treats as 'unknown' and vetoes on."""
    for bad in ("BTC", "", "   ", "BTC-", "-USD", None):
        assert quote_currency_of(bad) is None, f"{bad!r} should not resolve to a currency"


# -- parse_spot_product_id (rail 19's grammar) -------------------------------------------------

#: The three instrument classes the 2026-08-05 Coinbase asset-class study enumerated, as real
#: ids from that study, each paired with the parse the spot grammar must produce. Only the SPOT
#: rows may parse: a futures contract is `ROOT-DDMMMYY-CDE` (two hyphens) and an equity is a
#: 64-char hex hash (no hyphen), so both fail on shape alone -- no instrument model needed.
_REAL_PRODUCT_IDS = [
    # SPOT -- every id the live deployment can construct, plus the settlement legs rail 18
    # rejects by default (still well-formed SPOT; shape and settlement are separate questions).
    ("BTC-USD", ("BTC", "USD")),
    ("ETH-USD", ("ETH", "USD")),
    ("PAXG-USD", ("PAXG", "USD")),
    ("ADA-USD", ("ADA", "USD")),
    ("XLM-USD", ("XLM", "USD")),
    ("BTC-USDC", ("BTC", "USDC")),
    ("BTC-EUR", ("BTC", "EUR")),
    ("ETH-USDT", ("ETH", "USDT")),
    ("SOL-BTC", ("SOL", "BTC")),
    ("1INCH-USD", ("1INCH", "USD")),
    # FUTURE -- two hyphens. `quote_currency_of` reads the venue suffix `CDE` as a settlement
    # leg (which is how rail 18 catches it); the shape grammar rejects it outright.
    ("ADA-28AUG26-CDE", None),
    ("BIT-28AUG26-CDE", None),
    ("XLM-28AUG26-CDE", None),
    # The residual R2 closes: a derivative-shaped id whose FINAL segment is a legitimate
    # settlement currency, so rail 18 passes it and only the shape grammar stops it.
    ("BTC-PERP-USD", None),
    # EQUITY -- an opaque 64-char hex hash, no separator at all.
    ("ac568fb9e6c5a67da94f065a49fb7b0c59b7b258cfdf0a3b1560849071c3b05e", None),
    # ...and an equity `alias` id (the same share's other quote leg), equally opaque.
    ("a4a295140e2f9a6dbb2fc9b0f0a6f6e0a1b2c3d4e5f60718293a4b5c6d7e6162", None),
]


@pytest.mark.parametrize(("product_id", "expected"), _REAL_PRODUCT_IDS)
def test_the_spot_grammar_admits_spot_and_rejects_every_other_class(product_id, expected):
    assert parse_spot_product_id(product_id) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "-",
        "BTC-",
        "-USD",
        "BTC--USD",
        "BTC-U",  # quote leg below the 2-char floor `_CURRENCY_CODE_RE` sets
        "BTC-VERYLONGQUOTE",  # ...and above its 10-char ceiling
        "btc-usd",  # lowercase is a TYPO, not an id -- never silently uppercased
        "BTC-usd",
        "BTC/USD",
        "BTC_USD",
        " BTC-USD",
        "BTC-USD ",
        "BTC USD",
        "BTCUSD",
        "BTC-US D",
        "BTC-US.D",
    ],
)
def test_a_malformed_id_does_not_parse(bad):
    assert parse_spot_product_id(bad) is None


@pytest.mark.parametrize(
    "weird", [None, 42, 3.5, b"BTC-USD", ["BTC-USD"], {"BTC": "USD"}, object()]
)
def test_the_parser_is_TOTAL_and_never_raises(weird):
    """Rail 19 and `_asset` both run over historical audit rows, where one bad value must
    produce a veto, never an exception that crashes the agent cycle."""
    assert parse_spot_product_id(weird) is None


@pytest.mark.parametrize(("product_id", "expected"), _REAL_PRODUCT_IDS)
def test_any_parsed_quote_leg_is_a_valid_currency_code_by_configs_own_grammar(
    product_id, expected
):
    """The two grammars cannot be allowed to disagree about what a currency code is.

    Rail 18 compares `quote_currency_of`'s output against `config.settlement_currencies`, whose
    members are shape-checked at parse by `_CURRENCY_CODE_RE`. If this parser admitted a quote
    leg that regex rejects, there would be a well-formed spot id no configurable settlement set
    could ever name -- vetoed by rail 18 forever with nothing saying why.
    """
    parsed = parse_spot_product_id(product_id)
    if parsed is None:
        return
    assert _CURRENCY_CODE_RE.fullmatch(parsed[1]), parsed


def test_the_two_parsers_agree_on_the_quote_leg_of_a_well_formed_spot_id():
    """Where the shape grammar admits an id, `quote_currency_of` must read the same leg off it
    -- rails 18 and 19 would otherwise be talking about different halves of the same product."""
    for product_id, expected in _REAL_PRODUCT_IDS:
        if expected is None:
            continue
        assert quote_currency_of(product_id) == expected[1], product_id


# -- is_spot_base_code (the base-leg half, which `config.allowlist` is checked against) ---------


@pytest.mark.parametrize("code", ["BTC", "ETH", "PAXG", "ADA", "XLM", "SOL", "LTC", "LINK",
                                  "1INCH", "A", "ABCDEFGHIJKLMNOP"])
def test_a_real_ticker_is_a_valid_base_code(code):
    """Every asset the shipped configs list, plus the digit-leading and 16-char edges."""
    assert is_spot_base_code(code) is True


@pytest.mark.parametrize(
    "bad",
    [
        "btc",  # lowercase: a typo, not a ticker -- and never folded for us
        "Btc",
        "BTC-USD",  # a product id pasted where an asset belongs: derives `BTC-USD-USD`
        "BT C",
        "BTC/USD",
        "BTC.",
        "",
        " BTC",
        "BTC ",
        "ABCDEFGHIJKLMNOPQ",  # 17 chars: past the ceiling
    ],
)
def test_a_malformed_base_code_is_refused(bad):
    assert is_spot_base_code(bad) is False


@pytest.mark.parametrize("weird", [None, 42, 3.5, b"BTC", ["BTC"], {"BTC": 1}, object()])
def test_is_spot_base_code_is_TOTAL_and_never_raises(weird):
    assert is_spot_base_code(weird) is False


@pytest.mark.parametrize("code", ["BTC", "ETH", "PAXG", "1INCH", "A", "ABCDEFGHIJKLMNOP"])
@pytest.mark.parametrize("quote", ["USD", "USDC", "EUR", "USDT"])
def test_an_admitted_base_code_always_builds_an_id_the_spot_grammar_admits(code, quote):
    """THE property `config._parse_allowlist` rests on, and the reason the base grammar is shared
    rather than restated.

    `_history_product` is the only path from an allowlist entry to a venue id, and it is pure
    concatenation. So "config admitted this asset" must imply "rail 19 admits the id it derives",
    for every settlement currency `_CURRENCY_CODE_RE` allows -- otherwise config can hand the
    operator an asset the rails veto on every cycle, which is precisely the silent unfixable
    rejection the load-time checks exist to prevent.
    """
    assert _CURRENCY_CODE_RE.fullmatch(quote), "test premise: a configurable settlement code"
    assert parse_spot_product_id(f"{code}-{quote}") == (code, quote)
