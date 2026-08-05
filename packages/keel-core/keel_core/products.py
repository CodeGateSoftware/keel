"""Facts derivable from a venue product id.

The one rule this module exists to enforce: **the currency an order spends is a property of the
PRODUCT, not of global configuration.** `BTC-USD` settles in USD whatever `config.quote_currency`
says. Conflating the two let rail 13 check a balance the order never touches, which could pass an
order the account had no settled funds for -- the exact case that rail exists to prevent.

Two questions, two functions, deliberately not merged:

- `quote_currency_of` -- *what does this settle in?* A loose `rpartition` parse, because rails
  13/18 want an answer even for an id that is not a spot pair: it is what lets rail 18's message
  say `ADA-28AUG26-CDE` "settles in CDE", naming the venue suffix an operator can then look up.
- `parse_spot_product_id` -- *is this the shape of a spot pair at all?* A strict grammar, which
  rail 19 gates on. Tightening `quote_currency_of` into this one would have cost rail 18 its
  message and bought nothing, since the rails ask both questions anyway.
"""

from __future__ import annotations

import re

# A well-formed SPOT product id is exactly `BASE-QUOTE`: two non-empty uppercase-alphanumeric
# legs, one hyphen, nothing else. Empirical basis, from the 2026-08-05 Coinbase asset-class
# study (`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`), which enumerated
# every product the venue lists:
#
# - **SPOT (936): all match.** Every one has EXACTLY ONE hyphen (measured), and every observed
#   quote leg -- `USD`, `USDC`, `EUR`, `GBP`, `USDT`, `BTC`, `ETH`, `INR`, `SGD`, `CAD`, `AUD`
#   -- is 3-4 uppercase characters. Base legs are venue ticker symbols, which may lead with a
#   digit (`1INCH-USD`), hence `[A-Z0-9]` rather than `[A-Z]`.
# - **FUTURE (99): none match.** Every contract is `ROOT-DDMMMYY-CDE` -- two hyphens.
# - **EQUITY (1000, plus 813 `alias` ids): none match.** Each is an opaque 64-char hex hash
#   with no separator at all.
#
# The bounds are an envelope around what was measured, not a claim about it: 1-16 on the base is
# room for a long ticker; **2-10 on the quote is not free choice** -- it is `config`'s own
# `_CURRENCY_CODE_RE`, so a settlement currency that regex admits and this grammar rejects (or
# the reverse) cannot exist. A well-formed spot id no `settlement_currencies` set could ever
# name would be vetoed by rail 18 forever with nothing saying why.
#
# The base leg is split out rather than inlined because `config._parse_allowlist` checks against
# it: `allowlist` holds BASE legs, which `_history_product` concatenates into ids this grammar
# then judges. Two copies of the base grammar could disagree, and the config side losing that
# disagreement means an allowlist entry that loads cleanly and is vetoed on every cycle.
_SPOT_BASE_CODE_RE = re.compile(r"[A-Z0-9]{1,16}")
_SPOT_PRODUCT_ID_RE = re.compile(_SPOT_BASE_CODE_RE.pattern + r"-[A-Z0-9]{2,10}")


def quote_currency_of(product_id: str | None) -> str | None:
    """The settlement leg of `product_id` (`"BTC-USD"` -> `"USD"`), uppercased.

    Returns `None` for anything that does not resolve to a currency -- no separator, an empty
    base or quote leg, or a non-string. Callers must treat `None` as *unknown* and fail closed:
    rail 13 already vetoes a BUY on an unknown balance, and an unresolvable product id is
    precisely that.
    """
    if not isinstance(product_id, str):
        return None
    base, separator, quote = product_id.rpartition("-")
    if not separator or not base.strip() or not quote.strip():
        return None
    return quote.strip().upper()


def is_spot_base_code(code: object) -> bool:
    """Whether `code` is a well-formed BASE leg -- the half of the spot grammar before the hyphen.

    `"BTC"` -> `True`; `"btc"`, `"BTC-USD"`, `"BT C"`, `""` and non-strings -> `False`.

    The question `config._parse_allowlist` asks. An allowlist entry is not a product id: it is
    the base leg `_history_product` concatenates a settlement currency onto, so the whole-id
    grammar would reject every legitimate entry. Asking the base-leg half of the SAME grammar is
    what makes `asset in allowlist` imply `parse_spot_product_id(f"{asset}-{quote}")` for any
    quote the currency regex admits -- i.e. what stops config from admitting an asset rail 19
    will veto forever.

    **No normalisation, and no case-folding**, for `parse_spot_product_id`'s reason: this decides
    whether an id keel is about to CONSTRUCT will be well formed, and folding the input would
    mean the asset keel trades is not the asset the config file names.

    **Total by contract.** Never raises, on any input -- hence `object`.
    """
    return isinstance(code, str) and _SPOT_BASE_CODE_RE.fullmatch(code) is not None


def parse_spot_product_id(product_id: object) -> tuple[str, str] | None:
    """`(base, quote)` if `product_id` is a well-formed SPOT id, else `None`.

    `"BTC-USD"` -> `("BTC", "USD")`; `"ADA-28AUG26-CDE"` (futures), a 64-hex equity hash, and
    `"BTC-PERP-USD"` (the derivative-shaped id whose settlement leg is legitimate) all -> `None`.
    See `_SPOT_PRODUCT_ID_RE` above for the grammar and the census it rests on.

    This is a SHAPE check, not a settlement check, and not an existence check. It says nothing
    about whether the venue lists the product, whether the base is allowlisted (rail 1), or
    whether the quote is a settlement currency the operator configured (rail 18) -- `BTC-EUR`
    parses cleanly and rail 18 still vetoes it. The single question it answers is the one no
    other check asks: *is this the shape of a spot pair, or of something else?*

    **No normalisation, deliberately.** A lowercase id does not parse; it is not lowered first.
    `quote_currency_of` uppercases because it is answering "which currency is this" about an id
    that already exists, but silently accepting `btc-usd` here would mean an operator's typo
    became a traded product, and the CLI's job (`keel.commands._products.validate_product_ids`)
    is to say "did you mean BTC-USD?" instead.

    **Total by contract.** Never raises, on any input, including non-strings -- `product_id` is
    typed `object` to say so. Callers include the historical-order walk in
    `guards._open_exposure_by_asset`, which runs over whatever the audit log happens to hold; an
    exception there would turn one bad row into a crashed agent cycle, strictly worse than the
    hole rail 19 closes.
    """
    if not isinstance(product_id, str):
        return None
    match = _SPOT_PRODUCT_ID_RE.fullmatch(product_id)
    if match is None:
        return None
    base, _, quote = product_id.partition("-")
    return base, quote
