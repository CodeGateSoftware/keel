"""Buy-and-hold DCA benchmarks for the offline simulator (Sim Task 5).

Two reference strategies to compare rule-based backtests against: `dca_into_allowlist` (split a
fixed monthly dollar contribution across `Config.target_weights`) and `dca_into_btc` (100% of the
same contribution into BTC). Both never sell -- pure accumulation -- and share one engine
(`_run_dca`) and cost model, so the only difference between them is the weight split.

Cost model matches `sim.account`'s convention: the day's close is worsened by slippage,
`fill = close * (1 + slippage_pct)`, and the fee is taken off the top of the contributed dollar
amount before it's converted to quantity: `qty = (amount * (1 - fee_pct)) / fill`. The equity
curve is always marked to market at the day's *actual* close (never the slipped fill price), so
slippage and fees show up as a permanent haircut on the quantity bought, not as a mark-to-market
distortion.

One contribution is made on the first trading day of each of the first `months` distinct UTC
calendar months present in `prices_by_asset` (calendar-month grouping via
`execution.guards._utc_month_bounds`, matching every other monthly-cadence rail in keel). The
equity curve is sampled once per day for every day present in the data, including days after the
last contribution -- the position is marked to market for the rest of the series even once DCA'ing
has stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.execution.guards import _utc_month_bounds
from keel.sim import metrics


@dataclass
class BenchmarkResult:
    """The outcome of one buy-and-hold DCA run: its equity path, contributions, and metrics."""

    name: str
    equity_curve: list[tuple[int, Decimal]]
    contributions: list[tuple[int, Decimal]]
    ending_value: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe: Decimal
    sortino: Decimal
    return_per_drawdown: Decimal


def _run_dca(
    prices_by_asset: dict[str, list[tuple[int, Decimal]]],
    weights: dict[str, Decimal],
    monthly_contribution: Decimal,
    months: int,
    fee_pct: Decimal,
    slippage_pct: Decimal,
    name: str,
) -> BenchmarkResult:
    assets = [asset for asset, weight in weights.items() if weight > 0 and asset in prices_by_asset]

    price_maps: dict[str, dict[int, Decimal]] = {
        asset: dict(prices_by_asset[asset]) for asset in assets
    }
    all_ts = sorted({ts for asset in assets for ts, _ in prices_by_asset[asset]})

    qty_by_asset: dict[str, Decimal] = {asset: Decimal(0) for asset in assets}
    last_price: dict[str, Decimal] = {}
    contributions: list[tuple[int, Decimal]] = []
    equity_curve: list[tuple[int, Decimal]] = []

    current_month_key: int | None = None
    months_contributed = 0

    for ts in all_ts:
        for asset in assets:
            price = price_maps[asset].get(ts)
            if price is not None:
                last_price[asset] = price

        month_key, _ = _utc_month_bounds(ts)
        if month_key != current_month_key and months_contributed < months:
            current_month_key = month_key
            months_contributed += 1
            contributions.append((ts, monthly_contribution))
            for asset in assets:
                amount = monthly_contribution * weights[asset]
                price = last_price.get(asset)
                if amount <= 0 or price is None or price <= 0:
                    continue
                fill_price = price * (Decimal(1) + slippage_pct)
                qty_by_asset[asset] += (amount * (Decimal(1) - fee_pct)) / fill_price

        equity = sum(
            (qty_by_asset[asset] * last_price[asset] for asset in assets if asset in last_price),
            Decimal(0),
        )
        equity_curve.append((ts, equity))

    ending_value = equity_curve[-1][1] if equity_curve else Decimal(0)
    total_contributed = sum((amount for _, amount in contributions), Decimal(0))
    total_return_pct = (
        (ending_value - total_contributed) / total_contributed
        if total_contributed > 0
        else Decimal(0)
    )

    max_dd = metrics.max_drawdown_pct(equity_curve)
    returns = metrics.daily_returns(equity_curve)

    return BenchmarkResult(
        name=name,
        equity_curve=equity_curve,
        contributions=contributions,
        ending_value=ending_value,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_dd,
        sharpe=metrics.sharpe(returns),
        sortino=metrics.sortino(returns),
        return_per_drawdown=metrics.return_per_drawdown(total_return_pct, max_dd),
    )


def dca_into_allowlist(
    prices_by_asset: dict[str, list[tuple[int, Decimal]]],
    target_weights: dict[str, Decimal],
    monthly_contribution: Decimal,
    months: int,
    fee_pct: Decimal,
    slippage_pct: Decimal,
) -> BenchmarkResult:
    """DCA `monthly_contribution` into every asset in `target_weights`, split by weight.

    An asset named in `target_weights` with no matching entry in `prices_by_asset` simply gets no
    money invested for its slice (not an error) -- the rest of the split still executes.
    """
    return _run_dca(
        prices_by_asset,
        target_weights,
        monthly_contribution,
        months,
        fee_pct,
        slippage_pct,
        name="dca_into_allowlist",
    )


def dca_into_btc(
    prices_by_asset: dict[str, list[tuple[int, Decimal]]],
    monthly_contribution: Decimal,
    months: int,
    fee_pct: Decimal,
    slippage_pct: Decimal,
) -> BenchmarkResult:
    """DCA the full `monthly_contribution` into BTC every month, ignoring every other asset."""
    return _run_dca(
        prices_by_asset,
        {"BTC": Decimal(1)},
        monthly_contribution,
        months,
        fee_pct,
        slippage_pct,
        name="dca_into_btc",
    )
