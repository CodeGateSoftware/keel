"""Allowance-throughput planning (#478) — n_eff, in-allowance pacing, venue allocation.

The load-bearing tests reproduce PUBLISHED values: the cross-verification of the Quant Lab
note (`docs/research/2026-08-20-quant-lab-note-cross-verification.md` §4-§5) fixed DEFF at
2.58 from k = 8.43 and ICC = 0.212, n_eff(100) ≈ 39, a 20-point detectable edge at 80% power
at n_eff 39, and 618 effective observations for a 5-point edge. If this module drifts from
those, every month-to-detection estimate it prints is wrong.

The guardrail test is the issue's own non-negotiable: an allocation may move trades INTO a
venue's fee-free allowance; it may never enlarge or breach the allowance to fit more.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.research.throughput import (
    EPISODE_ICC,
    MEAN_EPISODE_SIZE,
    Product,
    VenueThroughput,
    allocate,
    design_effect,
    detectable_edge,
    months_to_target,
    n_eff,
    render_report,
    required_n_eff,
)


def test_design_effect_matches_the_measured_value() -> None:
    # 1 + (8.43 - 1) * 0.212 = 2.57516 -> the published "DEFF 2.58"
    assert design_effect() == Decimal("2.57516")


def test_hundred_pooled_trades_carry_about_39_effective_observations() -> None:
    assert n_eff(Decimal(100)).quantize(Decimal("1")) == Decimal("39")


def test_detectable_edge_at_39_effective_observations_is_20_points() -> None:
    # (z_0.95 + z_0.80)^2 / 4 = 1.5464; sqrt(1.5464 / 39) = 0.19927...
    assert detectable_edge(Decimal(39)).quantize(Decimal("0.001")) == Decimal("0.199")


def test_five_point_edge_needs_618_effective_observations() -> None:
    # The cross-verification's table: 5 pts -> 618 (n_eff)
    assert required_n_eff(Decimal("0.05")).quantize(Decimal("1")) == Decimal("619")


def test_unlimited_allowance_yields_the_full_signal_count() -> None:
    venue = VenueThroughput(
        venue="coinbase",
        monthly_allowance=None,
        mean_trade_notional=Decimal("4212"),
        expected_signals_per_month=Decimal("20"),
    )
    assert venue.trades_per_month() == Decimal("20")


def test_allowance_binds_before_the_signal_count() -> None:
    # The measured reality: one hourly proposal ($4,212) is 8.4x a $500/month
    # allowance -- the honest answer is a fraction of a trade, not zero trades
    # and not a rounded-up breach.
    venue = VenueThroughput(
        venue="coinbase",
        monthly_allowance=Decimal("500"),
        mean_trade_notional=Decimal("4212"),
        expected_signals_per_month=Decimal("20"),
    )
    assert venue.trades_per_month().quantize(Decimal("0.0001")) == Decimal("0.1187")


def test_allocator_respects_eligibility_and_fills_each_allowance() -> None:
    products = [
        Product(
            symbol="BTC-USD",
            venues=("coinbase",),
            mean_trade_notional=Decimal("100"),
            expected_signals_per_month=Decimal("2"),
        ),
        Product(
            symbol="AAPL",
            venues=("alpaca",),
            mean_trade_notional=Decimal("50"),
            expected_signals_per_month=Decimal("1"),
        ),
    ]
    plans = allocate(
        products,
        {"coinbase": Decimal("1000"), "alpaca": Decimal("60")},
    )
    by_venue = {plan.venue: plan for plan in plans}
    assert [p.symbol for p in by_venue["coinbase"].enabled] == ["BTC-USD"]
    assert [p.symbol for p in by_venue["alpaca"].enabled] == ["AAPL"]
    assert by_venue["coinbase"].deferred == ()
    assert by_venue["alpaca"].spend_per_month == Decimal("50")  # notional 50 x 1 signal


def test_allocator_prefers_more_trades_per_allowance_dollar() -> None:
    # 2 trades / $100 beats 3 trades / $400: with a $250 allowance the planner
    # enables the efficient product and DEFERS the other -- it never breaches.
    products = [
        Product(
            symbol="INEFFICIENT",
            venues=("coinbase",),
            mean_trade_notional=Decimal("400"),
            expected_signals_per_month=Decimal("3"),
        ),
        Product(
            symbol="EFFICIENT",
            venues=("coinbase",),
            mean_trade_notional=Decimal("100"),
            expected_signals_per_month=Decimal("2"),
        ),
    ]
    plans = allocate(products, {"coinbase": Decimal("250")})
    plan = plans[0]
    assert [p.symbol for p in plan.enabled] == ["EFFICIENT"]
    assert [p.symbol for p in plan.deferred] == ["INEFFICIENT"]
    assert plan.spend_per_month <= Decimal("250")


def test_allocation_never_enlarges_or_breaches_an_allowance() -> None:
    # The issue's non-negotiable, as a property over the allocation output.
    products = [
        Product(
            symbol=f"P{i}",
            venues=("coinbase",),
            mean_trade_notional=Decimal("100") + Decimal(i) * Decimal("37"),
            expected_signals_per_month=Decimal("1"),
        )
        for i in range(8)
    ]
    allowance = Decimal("500")
    (plan,) = allocate(products, {"coinbase": allowance})
    assert plan.spend_per_month <= allowance
    for product in plan.deferred:
        assert plan.spend_per_month + product.mean_trade_notional > allowance


def test_unlimited_venue_enables_every_eligible_product() -> None:
    products = [
        Product(
            symbol="BTC-USD",
            venues=("coinbase",),
            mean_trade_notional=Decimal("100"),
            expected_signals_per_month=Decimal("2"),
        )
    ]
    (plan,) = allocate(products, {"coinbase": None})
    assert [p.symbol for p in plan.enabled] == ["BTC-USD"]
    assert plan.spend_per_month == Decimal("200")  # notional 100 x 2 signals


def test_months_to_target_applies_the_design_effect() -> None:
    # 10 pooled trades/month, DEFF 2.5744 -> 3.884 n_eff/month; 101 effective
    # observations (a 12.4-point edge) takes 26.01 months.
    months = months_to_target(Decimal("101"), Decimal("10"))
    assert months.quantize(Decimal("0.01")) == Decimal("26.01")


def test_report_states_the_rail_14_guardrail_and_the_numbers() -> None:
    venue = VenueThroughput(
        venue="coinbase",
        monthly_allowance=Decimal("500"),
        mean_trade_notional=Decimal("4212"),
        expected_signals_per_month=Decimal("20"),
    )
    lines = render_report([venue], target_edge=Decimal("0.124"))
    text = "\n".join(lines).lower()
    assert "rail 14" in text
    assert "never enlarged" in text
    assert "0.1187" in text


def test_constants_carry_their_sources() -> None:
    assert MEAN_EPISODE_SIZE == Decimal("8.43")
    assert EPISODE_ICC == Decimal("0.212")


def test_allocator_rejects_a_product_with_no_eligible_venue() -> None:
    orphan = Product(
        symbol="ORPHAN",
        venues=(),
        mean_trade_notional=Decimal("100"),
        expected_signals_per_month=Decimal("1"),
    )
    with pytest.raises(ValueError, match="ORPHAN"):
        allocate([orphan], {"coinbase": Decimal("1000")})
