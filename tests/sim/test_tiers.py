"""Tests for `keel.sim.tiers`: Coinbase One tier/fee analysis (Issue #86).

Every fixture is a hand-built `monthly_volume` dict (no full `portfolio_sim.run()` needed --
`compute_tier_fee_result` is a pure function over primitives) so expected fee/net-P&L outcomes
are known exactly.
"""

from __future__ import annotations

from decimal import Decimal

from keel.config import TierConfig
from keel.sim.tiers import OVER_CAP, WITHIN_CAP, TierFeeResult, compute_tier_fee_result

TAKER_PCT = Decimal("0.012")

_BASIC = TierConfig(
    name="Basic", free_volume_usd=Decimal("500"), subscription_usd_month=Decimal("4.99")
)
_PREFERRED = TierConfig(
    name="Preferred", free_volume_usd=Decimal("10000"), subscription_usd_month=Decimal("29.99")
)
_PREMIUM = TierConfig(
    name="Premium", free_volume_usd=None, subscription_usd_month=Decimal("299.99")
)


def test_over_cap_volume_under_free_allowance_charges_zero_fee():
    monthly_volume = {0: Decimal("300")}  # under Basic's $500 free allowance

    result = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=1,
        tier=_BASIC,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("100"),
    )

    assert result.fees_usd == Decimal("0")
    assert result.paid_volume_usd == Decimal("0")
    assert result.free_volume_usd == Decimal("300")
    assert result.total_volume_usd == Decimal("300")


def test_over_cap_volume_over_free_allowance_charges_taker_fee_on_excess_only():
    monthly_volume = {0: Decimal("800")}  # $300 over Basic's $500 free allowance

    result = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=1,
        tier=_BASIC,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("100"),
    )

    assert result.paid_volume_usd == Decimal("300")
    assert result.free_volume_usd == Decimal("500")
    assert result.fees_usd == Decimal("300") * TAKER_PCT == Decimal("3.6")


def test_over_cap_sums_excess_across_multiple_months_independently():
    """Each UTC month's excess is computed against the free allowance separately -- a month
    under the cap contributes 0, it does not offset another month's excess."""
    monthly_volume = {0: Decimal("300"), 1: Decimal("800"), 2: Decimal("500")}

    result = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=3,
        tier=_BASIC,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("0"),
    )

    # month0: 0 excess, month1: 300 excess, month2: 0 excess -> 300 total
    assert result.paid_volume_usd == Decimal("300")
    assert result.fees_usd == Decimal("300") * TAKER_PCT
    assert result.total_volume_usd == Decimal("1600")


def test_within_cap_mode_is_always_fee_free_regardless_of_volume():
    """`mode=WITHIN_CAP` charges 0 fees by construction, even if the (hand-built, unrealistic)
    volume passed in would exceed the tier's free allowance -- callers are expected to pass a
    THROTTLED sim's volume for this mode, but the function itself doesn't re-derive that."""
    monthly_volume = {0: Decimal("999999")}

    result = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=1,
        tier=_BASIC,
        mode=WITHIN_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("100"),
    )

    assert result.fees_usd == Decimal("0")
    assert result.paid_volume_usd == Decimal("0")
    assert result.free_volume_usd == Decimal("999999")


def test_premium_unlimited_tier_always_zero_fee_in_both_modes():
    monthly_volume = {0: Decimal("50000")}  # far beyond Basic/Preferred's allowances

    over_cap = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=1,
        tier=_PREMIUM,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("500"),
    )
    within_cap = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=1,
        tier=_PREMIUM,
        mode=WITHIN_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("500"),
    )

    assert over_cap.fees_usd == Decimal("0")
    assert within_cap.fees_usd == Decimal("0")
    assert over_cap.net_pnl_usd == within_cap.net_pnl_usd  # "within == over" for Premium


def test_subscription_cost_multiplies_by_n_months_not_by_months_with_volume():
    """A month with zero trading volume still owes that month's subscription -- `n_months` is
    the caller-supplied span (e.g. `len(SimResult.contributions)`), not `len(monthly_volume)`."""
    monthly_volume = {0: Decimal("100")}  # only one month actually traded

    result = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=6,  # but the sim spanned 6 months
        tier=_BASIC,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("0"),
    )

    assert result.subscription_usd == Decimal("4.99") * 6


def test_net_pnl_subtracts_fees_and_subscription_from_gross_pnl():
    monthly_volume = {0: Decimal("800")}  # $300 excess -> $3.6 fee at 1.2%

    result = compute_tier_fee_result(
        monthly_volume=monthly_volume,
        n_months=1,
        tier=_BASIC,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("100"),
    )

    assert result.net_pnl_usd == Decimal("100") - Decimal("3.6") - Decimal("4.99")


def test_profits_cover_fees_true_when_net_pnl_non_negative():
    result = compute_tier_fee_result(
        monthly_volume={0: Decimal("100")},
        n_months=1,
        tier=_PREFERRED,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("1000"),
    )
    assert result.profits_cover_fees is True


def test_profits_cover_fees_false_when_net_pnl_negative():
    result = compute_tier_fee_result(
        monthly_volume={0: Decimal("50000")},  # huge excess over Preferred's $10k allowance
        n_months=1,
        tier=_PREFERRED,
        mode=OVER_CAP,
        taker_pct=TAKER_PCT,
        gross_pnl_usd=Decimal("10"),  # tiny gross P&L, dwarfed by the fee
    )
    assert result.profits_cover_fees is False
    assert result.net_pnl_usd < 0


def test_tier_fee_result_dataclass_shape():
    r = TierFeeResult(
        tier_name="Basic",
        mode=OVER_CAP,
        total_volume_usd=Decimal("1"),
        free_volume_usd=Decimal("1"),
        paid_volume_usd=Decimal("0"),
        fees_usd=Decimal("0"),
        subscription_usd=Decimal("4.99"),
        gross_pnl_usd=Decimal("10"),
        net_pnl_usd=Decimal("5.01"),
        profits_cover_fees=True,
    )
    assert r.tier_name == "Basic"
    assert r.mode == OVER_CAP
    assert r.profits_cover_fees is True
