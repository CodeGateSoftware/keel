"""Alpaca's US-equities cost model: commission-free, with sell-side regulatory
pass-throughs (PRD FR-7).

Alpaca charges no commission on US equities, but SELLS carry regulatory fees the venue
passes through at cost. Every rate in this module is a constant with its provenance
inline, because a rate that silently drifts is a cost model that quietly mis-prices
every sell preview -- the PRD §8 "regulatory drift" risk, met with "the cost model is
versioned and re-measured, not assumed".

Provenance (all read 2026-08-17):

* **SEC Section 31 fee** -- charged on SELLS, per $1,000,000 of principal. The SEC's
  advisory 2026-2 rate ($20.60 per $1M) took effect 2026-04-04 and IS the rate in force;
  Alpaca's own regulatory-fees page (https://alpaca.markets/support/regulatory-fees)
  still publishes $22.90 ($27.80 previously) -- the venue's page is the stale side of
  the two. The encoded $22.90 deliberately tracks what the venue itself charges, which
  over-states the statutory rate by ~$0.02 per $10k -- the conservative direction for a
  sell preview's proceeds -- and that delta is the documented re-measurement trigger:
  when Alpaca's page moves to the advisory figure, the constant and this provenance
  move with it.
* **FINRA Trading Activity Fee (TAF)** -- charged on SELLS, per share, capped per trade.
  The cap ($8.30 for equities) is on Alpaca's page above; the per-share rate ($0.000166)
  is FINRA's, Schedule A to the FINRA By-Laws §4(b)(7) (SR-FINRA-2020-032, in force since
  2021-01-01; reaffirmed by SR-FINRA-2024-019).
* **CAT** (Consolidated Audit Trail, buys and sells) is a documented omission: the PRD
  names the two pass-throughs above, and CAT rounds to fractions of a cent per trade.

The one consumer of this module is `AlpacaAdapter.preview_order`: `est_fee` on a sell is
computed here, never invented by the caller, and `est_fee` on a buy is honestly zero.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core.types import Side

#: Alpaca's commission on US equities: zero (docs.alpaca.markets, "Regulatory Fees").
#: Zero is a claim this venue really makes -- it is not a placeholder for "unknown".
COMMISSION_RATE: Decimal = Decimal("0")

#: SEC Section 31 fee, per $1,000,000 of sale proceeds. See the module docstring for the
#: provenance and the periodic-adjustment caveat.
SEC_SECTION_31_PER_MILLION: Decimal = Decimal("22.90")

#: FINRA Trading Activity Fee per share sold.
TAF_PER_SHARE: Decimal = Decimal("0.000166")

#: FINRA's per-trade maximum for the equity TAF -- the cap Alpaca's fee page names.
TAF_MAX_PER_TRADE: Decimal = Decimal("8.30")

_MILLION: Decimal = Decimal(1_000_000)


def estimate_regulatory_fees(
    side: Side, shares: Decimal, proceeds: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Estimate the pass-through regulatory fees for one order.

    Returns `(total, sec_section_31, taf)`. A BUY pays nothing: every fee this model
    carries is sell-side, and commission is zero. A SELL pays Section 31 on proceeds and
    TAF on shares, with the TAF capped at its per-trade maximum.

    The inputs are the preview's own estimates, so the output is an estimate too -- which
    is exactly why it feeds `Preview.est_fee` with `synthetic=True` rather than anything
    that could read as a venue quote.
    """
    if side is Side.BUY:
        return Decimal("0"), Decimal("0"), Decimal("0")
    sec_fee = proceeds * SEC_SECTION_31_PER_MILLION / _MILLION
    taf = min(shares * TAF_PER_SHARE, TAF_MAX_PER_TRADE)
    return sec_fee + taf, sec_fee, taf


__all__ = [
    "COMMISSION_RATE",
    "SEC_SECTION_31_PER_MILLION",
    "TAF_MAX_PER_TRADE",
    "TAF_PER_SHARE",
    "estimate_regulatory_fees",
]
