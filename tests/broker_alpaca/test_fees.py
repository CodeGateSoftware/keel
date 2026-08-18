"""Tests for `keel_broker_alpaca.fees` -- the sell-side regulatory pass-through model.

Alpaca charges no commission on US equities, but sells carry regulatory fees the venue
passes through at cost (PRD FR-7). The rates are constants with provenance in `fees.py`;
these tests pin both the arithmetic and the rates themselves, because a rate that silently
drifts is a cost model that quietly mis-prices every sell preview (PRD §8 "Regulatory
drift ... the cost model is versioned and re-measured, not assumed").
"""

from __future__ import annotations

from decimal import Decimal

from keel_broker_alpaca.fees import (
    COMMISSION_RATE,
    SEC_SECTION_31_PER_MILLION,
    TAF_MAX_PER_TRADE,
    TAF_PER_SHARE,
    estimate_regulatory_fees,
)
from keel_core.types import Side


def test_the_constants_carry_the_published_rates() -> None:
    """Pinned so a well-meaning "update" to one number without its provenance comment
    fails here rather than silently re-pricing every preview.

    Sources (read 2026-08-17): Alpaca's own regulatory-fee pages state the Section 31
    current rate ($22.90 per $1M of principal, sells only; $27.80 previously) and the TAF
    equity cap ($8.30); FINRA Schedule A to the By-Laws (SR-FINRA-2020-032, in force since
    2021-01-01, reaffirmed by SR-FINRA-2024-019) states the $0.000166 per-share TAF.
    """
    assert COMMISSION_RATE == Decimal("0")
    assert SEC_SECTION_31_PER_MILLION == Decimal("22.90")
    assert TAF_PER_SHARE == Decimal("0.000166")
    assert TAF_MAX_PER_TRADE == Decimal("8.30")


def test_a_small_sell_pays_sec_plus_taf_on_proceeds_and_shares() -> None:
    """100 shares sold at $50 -> $5,000 proceeds.

    SEC Section 31: 5000 * 22.90 / 1_000_000 = 0.1145
    FINRA TAF:        100 * 0.000166          = 0.0166
    """
    total, sec_fee, taf = estimate_regulatory_fees(
        side=Side.SELL, shares=Decimal("100"), proceeds=Decimal("5000")
    )
    assert sec_fee == Decimal("0.1145")
    assert taf == Decimal("0.0166")
    assert total == Decimal("0.1311")


def test_the_taf_caps_at_the_per_trade_maximum() -> None:
    """Alpaca's page states the equity TAF is capped ($8.30); 100,000 shares would
    nominally be 100000 * 0.000166 = 16.60, so the cap is what keeps the estimate honest."""
    total, sec_fee, taf = estimate_regulatory_fees(
        side=Side.SELL, shares=Decimal("100000"), proceeds=Decimal("1000000")
    )
    assert taf == TAF_MAX_PER_TRADE
    assert sec_fee == Decimal("22.90")  # 1M * 22.90 / 1M
    assert total == Decimal("31.20")


def test_a_buy_pays_nothing() -> None:
    """Commission is $0 and every pass-through fee this model carries is sell-side."""
    total, sec_fee, taf = estimate_regulatory_fees(
        side=Side.BUY, shares=Decimal("100"), proceeds=Decimal("5000")
    )
    assert total == sec_fee == taf == Decimal("0")


def test_a_fractional_sell_is_charged_on_proceeds_and_shares_alike() -> None:
    """Fractional exits still owe both legs: TAF is per share (fractional included) and
    Section 31 is per dollar of proceeds."""
    total, sec_fee, taf = estimate_regulatory_fees(
        side=Side.SELL, shares=Decimal("0.7577533"), proceeds=Decimal("99.99")
    )
    assert sec_fee == Decimal("99.99") * SEC_SECTION_31_PER_MILLION / Decimal(1_000_000)
    assert taf == Decimal("0.7577533") * TAF_PER_SHARE
    assert total == sec_fee + taf
