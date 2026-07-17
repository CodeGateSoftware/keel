"""Coinbase One subscription-tier / fee analysis (Issue #86).

Given a completed sim run's per-month trading VOLUME (`portfolio_sim.SimResult.monthly_volume`,
Issue #85's buys+sells convention) and a `keel.config.TierConfig`, this module answers: for a
given Coinbase One tier, does staying WITHIN the tier's fee-free monthly volume allowance (a
throttled run, 0 trading fees, but the subscription is still due) or trading freely and paying
the taker fee on volume EXCEEDING it ("over cap") net out ahead, once the tier's own monthly
subscription cost is subtracted too?

**Interpretive note (fee layering).** The underlying `portfolio_sim.run()` pass already bakes a
generic per-fill execution-friction fee (`fee_pct`, e.g. the CLI's `_SIM_FEE_PCT`) and slippage
into every trade's P&L -- a market-microstructure cost present regardless of which Coinbase One
tier is chosen. This module's `fees_usd` is a SEPARATE overlay: the ADDITIONAL Coinbase One
monthly-volume-based fee (`fees.taker_pct`, charged only on the portion of a month's trading
volume beyond the tier's `free_volume_usd`), which is what actually varies by tier/mode.
`gross_pnl_usd` is therefore the relevant sim run's own net P&L (friction/slippage-inclusive
already) -- "gross" relative to THIS module's tier-fee-and-subscription overlay, not literally
frictionless. This mirrors `keel.sim.report`'s own "Interpretive notes" convention for spec
ambiguities the plan text leaves implicit.

**Two sim runs feed one tier's two rows.** A finite-free-volume tier's `OVER_CAP` row uses the
NATURAL (unthrottled) sim run's volume/P&L; its `WITHIN_CAP` row uses a SEPARATE, throttled run
(`portfolio_sim.run(..., monthly_volume_cap=tier.free_volume_usd)`) -- the throttle changes which
trades open at all, so the two rows' `gross_pnl_usd` legitimately differ. An unlimited tier
(`tier.free_volume_usd is None`, i.e. Premium) has nothing to throttle -- both rows reuse the
natural run and are always fee-free (`WITHIN_CAP == OVER_CAP` for that tier).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.config import TierConfig

__all__ = ["OVER_CAP", "WITHIN_CAP", "TierFeeResult", "compute_tier_fee_result"]

WITHIN_CAP = "within_cap"
OVER_CAP = "over_cap"


@dataclass(frozen=True)
class TierFeeResult:
    """One row of the tier/fee analysis matrix -- one (tier, mode) pair."""

    tier_name: str
    mode: str  # WITHIN_CAP | OVER_CAP
    total_volume_usd: Decimal
    free_volume_usd: Decimal  # this row's volume that fell within the tier's fee-free allowance
    paid_volume_usd: Decimal  # this row's volume that was charged a fee (0 for within-cap/Premium)
    fees_usd: Decimal
    subscription_usd: Decimal  # tier subscription cost over the whole simulated span
    gross_pnl_usd: Decimal
    net_pnl_usd: Decimal
    profits_cover_fees: bool  # net_pnl_usd >= 0 -- does this tier/mode net out ahead?


def compute_tier_fee_result(
    monthly_volume: dict[int, Decimal],
    n_months: int,
    tier: TierConfig,
    mode: str,
    taker_pct: Decimal,
    gross_pnl_usd: Decimal,
) -> TierFeeResult:
    """Compute one (tier, mode) row from `monthly_volume` (a sim run's per-UTC-month trading
    volume, keyed by each month's start ts -- `portfolio_sim.SimResult.monthly_volume`).

    `mode == WITHIN_CAP`, or any tier with `tier.free_volume_usd is None` (unlimited, e.g.
    Premium), is always fee-free by construction -- for a finite-free-volume tier, `monthly_volume`
    should itself come from a THROTTLED run
    (`portfolio_sim.run(..., monthly_volume_cap=tier.free_volume_usd)`); for an unlimited tier,
    the natural (unthrottled) run is used directly since there is nothing to throttle.

    `mode == OVER_CAP` (for a finite-free-volume tier) charges `taker_pct` on each month's volume
    beyond `tier.free_volume_usd`, summed across months -- a month that stayed under the
    allowance contributes 0 to `paid_volume_usd`/`fees_usd` for that month.

    `n_months` is the number of UTC calendar months the sim run spanned (e.g.
    `len(SimResult.contributions)`, not `len(monthly_volume)` -- a month with zero trading volume
    still owes that month's subscription). Subscription cost is
    `tier.subscription_usd_month * n_months`.
    """
    total_volume = sum(monthly_volume.values(), Decimal("0"))
    subscription_total = tier.subscription_usd_month * n_months

    if tier.free_volume_usd is None or mode == WITHIN_CAP:
        free_volume = total_volume
        paid_volume = Decimal("0")
        fees = Decimal("0")
    else:
        free_volume = Decimal("0")
        paid_volume = Decimal("0")
        for month_volume in monthly_volume.values():
            free_volume += min(month_volume, tier.free_volume_usd)
            paid_volume += max(Decimal("0"), month_volume - tier.free_volume_usd)
        fees = paid_volume * taker_pct

    net_pnl = gross_pnl_usd - fees - subscription_total
    return TierFeeResult(
        tier_name=tier.name,
        mode=mode,
        total_volume_usd=total_volume,
        free_volume_usd=free_volume,
        paid_volume_usd=paid_volume,
        fees_usd=fees,
        subscription_usd=subscription_total,
        gross_pnl_usd=gross_pnl_usd,
        net_pnl_usd=net_pnl,
        profits_cover_fees=net_pnl >= Decimal("0"),
    )
