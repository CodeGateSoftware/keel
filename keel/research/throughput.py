"""Allowance-throughput planning (#478): route WITHIN fee-free allowances, and say
honestly how long the evidence takes.

Two questions this module answers, both with the same discipline the engine applies
everywhere else -- report the truth, refuse to flatter:

* **Throughput.** A venue's fee-free volume allowance (rail 14) caps monthly BUY
  notional, and the cross-verification showed that cap is the *profitability
  boundary*: inside it the reconstructed rules sit at break-even, outside it the
  taker fee decides. So the honest throughput of a venue is
  ``min(expected signals, allowance / mean trade notional)`` -- and at the measured
  numbers (a $4,212 mean hourly proposal against a $500/month Basic allowance) that
  is a fraction of a trade per month, which is exactly what the report must say
  rather than rounding up into a breach.

* **Evidence time.** Signals fire in herds -- about 8 assets the same day -- and
  those trades win or lose together (ICC 0.212), so pooled trades are not
  independent observations. Every time-to-detection figure this module produces is
  stated in *effective* observations via the design effect, never raw n (#427).

The allocator's non-negotiable, stated in the issue and enforced by construction:
it moves trades INTO an allowance, it never enlarges one. Products that do not fit
are deferred with a reason, not squeezed through the cap.

Sources for every constant are named where they are defined; the tests reproduce
the published values, and if they drift, every month-to-detection estimate printed
downstream is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: Size-weighted mean episode size k = 8.43 and within-episode ICC = 0.212,
#: measured over 5 years of cached Coinbase candles by the cross-verification of
#: the Quant Lab note (`docs/research/2026-08-20-quant-lab-note-cross-verification.md`
#: §4: 1,355 episodes, largest 24 assets firing the same day).
MEAN_EPISODE_SIZE = Decimal("8.43")
EPISODE_ICC = Decimal("0.212")

#: (z_{0.95} + z_{0.80})^2 / 4 = 1.5464 -- the sample-size constant of the note's
#: eq. 6 (alpha 5% two-sided, 80% power), so n_eff = 1.5464 / edge^2. Reproduces
#: the published table: a 20-point edge at n_eff 39, 5 points at 618-619.
SAMPLE_SIZE_CONSTANT = Decimal("1.5464")


def design_effect(k: Decimal = MEAN_EPISODE_SIZE, icc: Decimal = EPISODE_ICC) -> Decimal:
    """DEFF = 1 + (k - 1) * icc. The published value at the measured constants
    is 2.58: a pooled sample of n carries n / DEFF independent observations."""
    return Decimal(1) + (k - Decimal(1)) * icc


def n_eff(
    n_pooled: Decimal,
    k: Decimal = MEAN_EPISODE_SIZE,
    icc: Decimal = EPISODE_ICC,
) -> Decimal:
    """Effective independent observations in a pooled sample of ``n_pooled``
    herding trades. n = 100 -> about 39 (#427's finding about the 2026-09-30
    review floor)."""
    if n_pooled < 0:
        raise ValueError("n_pooled must be >= 0")
    return n_pooled / design_effect(k, icc)


def required_n_eff(edge: Decimal) -> Decimal:
    """Effective observations needed to detect ``edge`` (a win-rate edge as a
    fraction, 0.05 = 5 points) at 80% power, alpha 5%: 1.5464 / edge^2."""
    if edge <= 0 or edge >= 1:
        raise ValueError("edge must be in (0, 1)")
    return SAMPLE_SIZE_CONSTANT / (edge * edge)


def detectable_edge(effective_n: Decimal) -> Decimal:
    """The inverse of :func:`required_n_eff`: the smallest win-rate edge
    (as a fraction) that ``effective_n`` independent observations can detect
    at 80% power, alpha 5%. At n_eff 39 this is about 0.199 -- the published
    'a 100-trade pool can only detect a 20-point edge'."""
    if effective_n <= 0:
        raise ValueError("effective_n must be > 0")
    return (SAMPLE_SIZE_CONSTANT / effective_n).sqrt()


@dataclass(frozen=True)
class VenueThroughput:
    """One venue's honest monthly throughput at its fee-free allowance.

    ``monthly_allowance`` is None for an unlimited (Premium, in force) record --
    the same convention rail 14 uses, where an unlimited allowance has no cap to
    pace and the rail simply does not bind.
    """

    venue: str
    monthly_allowance: Decimal | None
    mean_trade_notional: Decimal
    expected_signals_per_month: Decimal

    def trades_per_month(self) -> Decimal:
        """min(expected signals, allowance / mean notional) -- the allowance
        binds first whenever one proposal exceeds it, and the honest answer is
        the fraction, never a rounded-up breach."""
        if self.monthly_allowance is None:
            return self.expected_signals_per_month
        if self.mean_trade_notional <= 0:
            raise ValueError("mean_trade_notional must be > 0")
        return min(
            self.expected_signals_per_month,
            self.monthly_allowance / self.mean_trade_notional,
        )


@dataclass(frozen=True)
class Product:
    """A tradable product the allocator may enable on one of its eligible
    venues (crypto -> coinbase, equities -> alpaca; eligibility is stated, never
    inferred)."""

    symbol: str
    venues: tuple[str, ...]
    mean_trade_notional: Decimal
    expected_signals_per_month: Decimal


@dataclass(frozen=True)
class VenuePlan:
    """The allocator's decision for one venue: which products run inside the
    allowance, which are deferred because they do not fit, and what that spends."""

    venue: str
    allowance: Decimal | None
    enabled: tuple[Product, ...]
    deferred: tuple[Product, ...]
    spend_per_month: Decimal
    trades_per_month: Decimal


def allocate(products: list[Product], allowances: dict[str, Decimal | None]) -> list[VenuePlan]:
    """Assign products to venues to maximize in-allowance trade count.

    Greedy per venue, most trades per allowance-dollar first (ties broken by
    symbol for determinism). By construction a plan's spend never exceeds its
    venue's allowance: a product that does not fit is deferred, and deferral is
    the honest outcome -- rail 14 is the boundary, never a budget to enlarge.

    A product eligible on no listed venue is an error the caller must fix in
    the eligibility table, not silently droppable inventory.
    """
    claimed: set[str] = set()
    plans: list[VenuePlan] = []
    for venue, allowance in allowances.items():
        eligible = [p for p in products if venue in p.venues and p.symbol not in claimed]
        eligible.sort(
            key=lambda p: (
                -(p.expected_signals_per_month / p.mean_trade_notional),
                p.symbol,
            )
        )
        enabled: list[Product] = []
        deferred: list[Product] = []
        spend = Decimal(0)
        for product in eligible:
            monthly_cost = product.mean_trade_notional * product.expected_signals_per_month
            if allowance is None or spend + monthly_cost <= allowance:
                enabled.append(product)
                spend += monthly_cost
            else:
                deferred.append(product)
        for product in enabled:
            claimed.add(product.symbol)
        trades = sum((p.expected_signals_per_month for p in enabled), Decimal(0))
        if allowance is not None:
            trades = (
                min(trades, allowance / next((p.mean_trade_notional for p in enabled), Decimal(1)))
                if enabled
                else Decimal(0)
            )
        plans.append(
            VenuePlan(
                venue=venue,
                allowance=allowance,
                enabled=tuple(enabled),
                deferred=tuple(deferred),
                spend_per_month=spend,
                trades_per_month=trades,
            )
        )
    listed = set(allowances)
    for product in products:
        if not (set(product.venues) & listed):
            raise ValueError(
                f"{product.symbol} is eligible on no listed venue "
                f"(eligibility: {list(product.venues)}, listed: {sorted(listed)})"
            )
    return plans


def months_to_target(target_effective_n: Decimal, pooled_trades_per_month: Decimal) -> Decimal:
    """Months for pooled trades to accumulate ``target_effective_n`` INDEPENDENT
    observations, applying the design effect -- never raw n."""
    per_month = n_eff(pooled_trades_per_month)
    if per_month <= 0:
        raise ValueError("pooled trades per month must be > 0")
    return target_effective_n / per_month


def render_report(venues: list[VenueThroughput], target_edge: Decimal) -> list[str]:
    """The honest throughput report: per-venue in-allowance trades per month,
    the n_eff correction applied, and the months to a detectable edge -- with
    the rail 14 guardrail stated outright, because a planner that could be read
    as license to trade through the cap would be worse than no planner."""
    lines: list[str] = [
        "Allowance throughput plan (#478)",
        "Rail 14 is the profitability boundary: these numbers route trades",
        "WITHIN fee-free allowances; an allowance is never enlarged or breached",
        f"to fit more. Design effect {design_effect()} (k={MEAN_EPISODE_SIZE},",
        f"ICC={EPISODE_ICC}): pooled trades are divided by it before any",
        "time-to-detection claim, per #427.",
        "",
    ]
    pooled = Decimal(0)
    for venue in venues:
        trades = venue.trades_per_month()
        pooled += trades
        cap = "unlimited" if venue.monthly_allowance is None else f"{venue.monthly_allowance}/month"
        lines.append(
            f"- {venue.venue}: {trades.quantize(Decimal('0.0001'))} trades/month "
            f"in-allowance (cap {cap}, mean proposal "
            f"{venue.mean_trade_notional}, signals {venue.expected_signals_per_month})"
        )
    target = required_n_eff(target_edge)
    months = months_to_target(target, pooled)
    lines += [
        "",
        f"Pooled: {pooled.quantize(Decimal('0.0001'))} trades/month -> "
        f"{n_eff(pooled).quantize(Decimal('0.01'))} effective observations/month.",
        f"A {target_edge.quantize(Decimal('0.001'))} edge needs {target.quantize(Decimal('1'))} "
        f"effective observations: {months.quantize(Decimal('1'))} months at this mix.",
    ]
    return lines
