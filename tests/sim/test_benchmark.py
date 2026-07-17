"""Tests for `keel.sim.benchmark`: DCA-into-allowlist / DCA-into-BTC buy-and-hold benchmarks
(Sim Task 5).

Every fixture is hand-computed flat/simple-fraction Decimal arithmetic (no numpy/pandas) so the
expected numbers are exactly reproducible on paper. Cost model matches `sim.account`'s convention:
`fill = close * (1 + slippage_pct)`, and the fee is taken off the contributed dollar amount before
conversion to quantity: `qty = (amount * (1 - fee_pct)) / fill`. Positions are DCA'd and never
sold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from keel.sim.benchmark import BenchmarkResult, dca_into_allowlist, dca_into_btc


def _ts(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, 12, 0, 0, tzinfo=UTC).timestamp())


def _daily_series(start: tuple[int, int, int], closes: list[Decimal]) -> list[tuple[int, Decimal]]:
    """Build a one-day-apart `(ts, close)` series starting at `start`, one entry per `closes`."""
    year, month, day = start
    base = _ts(year, month, day)
    return [(base + i * 86400, close) for i, close in enumerate(closes)]


# -- dca_into_allowlist -------------------------------------------------------------------------


def test_dca_into_allowlist_flat_price_holds_contributions_minus_fees():
    # 3 months of daily $100 BTC, monthly contribution $500, 1% fee, no slippage.
    closes = [Decimal("100")] * 90
    prices = {"BTC": _daily_series((2024, 1, 1), closes)}
    weights = {"BTC": Decimal("1")}

    result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights=weights,
        monthly_contribution=Decimal("500"),
        months=3,
        fee_pct=Decimal("0.01"),
        slippage_pct=Decimal("0"),
    )

    assert isinstance(result, BenchmarkResult)
    assert result.name == "dca_into_allowlist"
    assert len(result.contributions) == 3
    assert sum(amount for _, amount in result.contributions) == Decimal("1500")
    # qty bought each month = 500*0.99/100 = 4.95; 3 months -> 14.85 BTC * $100 = $1485.
    assert result.ending_value == Decimal("1485")
    assert result.total_return_pct == Decimal("1485") / Decimal("1500") - 1
    # Equity never declines from its running peak on a flat price -> zero drawdown.
    assert result.max_drawdown_pct == Decimal("0")


def test_dca_into_allowlist_40_30_30_split_known_qty():
    # One month only. Purchase happens on the first day (BTC=50000, ETH=2500, PAXG=2000);
    # equity is later marked at day-10 prices (BTC=60000, ETH=3000, PAXG=2100).
    prices = {
        "BTC": _daily_series((2024, 1, 1), [Decimal("50000")] * 11),
        "ETH": _daily_series((2024, 1, 1), [Decimal("2500")] * 11),
        "PAXG": _daily_series((2024, 1, 1), [Decimal("2000")] * 11),
    }
    prices["BTC"][-1] = (prices["BTC"][-1][0], Decimal("60000"))
    prices["ETH"][-1] = (prices["ETH"][-1][0], Decimal("3000"))
    prices["PAXG"][-1] = (prices["PAXG"][-1][0], Decimal("2100"))
    weights = {"BTC": Decimal("0.4"), "ETH": Decimal("0.3"), "PAXG": Decimal("0.3")}

    result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights=weights,
        monthly_contribution=Decimal("1000"),
        months=1,
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )

    # qty_BTC = 400/50000 = 0.008, qty_ETH = 300/2500 = 0.12, qty_PAXG = 300/2000 = 0.15
    # ending equity = 0.008*60000 + 0.12*3000 + 0.15*2100 = 480 + 360 + 315 = 1155
    assert result.ending_value == Decimal("1155")
    assert result.equity_curve[-1] == (prices["BTC"][-1][0], Decimal("1155"))
    assert result.total_return_pct == Decimal("155") / Decimal("1000")
    assert len(result.contributions) == 1
    assert result.contributions[0][1] == Decimal("1000")


def test_dca_into_allowlist_applies_slippage_and_fee_to_qty():
    # amount=$1000, close=$100, slippage=1% -> fill=$101; fee=2% -> qty = 1000*0.98/101
    prices = {"BTC": _daily_series((2024, 1, 1), [Decimal("100")])}
    weights = {"BTC": Decimal("1")}

    result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights=weights,
        monthly_contribution=Decimal("1000"),
        months=1,
        fee_pct=Decimal("0.02"),
        slippage_pct=Decimal("0.01"),
    )

    expected_qty = (Decimal("1000") * Decimal("0.98")) / Decimal("101")
    # Marked to market at the same day's *actual* close (not the slipped fill price).
    assert result.ending_value == expected_qty * Decimal("100")


def test_dca_into_allowlist_ignores_assets_missing_from_price_data():
    # target_weights names an asset ("SOL") with no price series -- its slice is simply not
    # invested, not an error.
    prices = {"BTC": _daily_series((2024, 1, 1), [Decimal("100")])}
    weights = {"BTC": Decimal("0.5"), "SOL": Decimal("0.5")}

    result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights=weights,
        monthly_contribution=Decimal("1000"),
        months=1,
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )

    # Only the $500 BTC slice is invested: qty = 500/100 = 5 -> $500 ending value.
    assert result.ending_value == Decimal("500")


def test_dca_into_allowlist_stops_contributing_after_months_but_keeps_marking_to_market():
    # 2 calendar months of data, but months=1 -> only the first month is bought; day 32
    # (second month) still marks the held position to market.
    closes = [Decimal("100")] * 31 + [Decimal("120")]
    prices = {"BTC": _daily_series((2024, 1, 1), closes)}
    weights = {"BTC": Decimal("1")}

    result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights=weights,
        monthly_contribution=Decimal("500"),
        months=1,
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )

    assert len(result.contributions) == 1
    # qty = 500/100 = 5; final mark at $120 -> $600, no second contribution.
    assert result.ending_value == Decimal("600")
    assert len(result.equity_curve) == 32


# -- dca_into_btc ---------------------------------------------------------------------------------


def test_dca_into_btc_ignores_other_assets_and_buys_100pct_btc():
    prices = {
        "BTC": _daily_series((2024, 1, 1), [Decimal("50000")] * 5),
        "ETH": _daily_series((2024, 1, 1), [Decimal("2500")] * 5),
    }

    result = dca_into_btc(
        prices_by_asset=prices,
        monthly_contribution=Decimal("500"),
        months=1,
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )

    assert result.name == "dca_into_btc"
    # qty = 500/50000 = 0.01 -> ending value flat at $500 (flat BTC price).
    assert result.ending_value == Decimal("500")


def test_dca_into_btc_matches_dca_into_allowlist_with_100pct_btc_weight():
    prices = {
        "BTC": _daily_series((2024, 1, 1), [Decimal("50000"), Decimal("55000")]),
        "ETH": _daily_series((2024, 1, 1), [Decimal("2500"), Decimal("2600")]),
    }

    btc_result = dca_into_btc(
        prices_by_asset=prices,
        monthly_contribution=Decimal("500"),
        months=1,
        fee_pct=Decimal("0.01"),
        slippage_pct=Decimal("0.005"),
    )
    allowlist_result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights={"BTC": Decimal("1")},
        monthly_contribution=Decimal("500"),
        months=1,
        fee_pct=Decimal("0.01"),
        slippage_pct=Decimal("0.005"),
    )

    assert btc_result.ending_value == allowlist_result.ending_value
    assert btc_result.equity_curve == allowlist_result.equity_curve


# -- metrics wiring ---------------------------------------------------------------------------


def test_metrics_populated_with_up_and_down_price_path():
    # Two months, price rises then falls, giving a real drawdown and non-flat returns.
    closes = (
        [Decimal("100") + Decimal(i) for i in range(30)]  # rises to 129
        + [Decimal("129") - Decimal(i) for i in range(1, 31)]  # falls to 99
    )
    prices = {"BTC": _daily_series((2024, 1, 1), closes)}
    weights = {"BTC": Decimal("1")}

    result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights=weights,
        monthly_contribution=Decimal("500"),
        months=2,
        fee_pct=Decimal("0.01"),
        slippage_pct=Decimal("0"),
    )

    assert isinstance(result.max_drawdown_pct, Decimal)
    assert isinstance(result.sharpe, Decimal)
    assert isinstance(result.sortino, Decimal)
    assert isinstance(result.return_per_drawdown, Decimal)
    assert result.max_drawdown_pct > Decimal("0")
    if result.max_drawdown_pct != Decimal("0"):
        assert result.return_per_drawdown == result.total_return_pct / result.max_drawdown_pct


def test_zero_months_makes_no_contributions_and_flat_zero_equity():
    prices = {"BTC": _daily_series((2024, 1, 1), [Decimal("100")] * 5)}
    weights = {"BTC": Decimal("1")}

    result = dca_into_allowlist(
        prices_by_asset=prices,
        target_weights=weights,
        monthly_contribution=Decimal("500"),
        months=0,
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )

    assert result.contributions == []
    assert result.ending_value == Decimal("0")
    assert result.total_return_pct == Decimal("0")
    assert result.max_drawdown_pct == Decimal("0")
