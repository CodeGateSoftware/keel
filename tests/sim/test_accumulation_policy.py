"""Tests for `keel.sim.accumulation_policy` (#831): the accumulation-policy harness.

Every expected order and fee below is computed by hand in the test, from the spec's formulas
(`docs/superpowers/specs/2026-09-27-accumulation-policy-design.md` §4-§8), never read back from
the module. Price paths are tiny and synthetic. "Gapless" candles have each day's open equal to
the previous day's close, which is the one case where the spec's next-open fill and
`benchmark.dca_into_allowlist`'s same-day-close fill coincide.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from keel.sim import accumulation_policy as ap
from keel.sim.benchmark import dca_into_allowlist

D = Decimal
CENT = D("0.01")


def _panel(
    start: tuple[int, int, int],
    closes: dict[str, list[Decimal]],
    opens: dict[str, list[Decimal]] | None = None,
) -> ap.PricePanel:
    """A daily panel from `start`; opens default to gapless (open[k] == close[k-1])."""
    n = len(next(iter(closes.values())))
    base = datetime(*start, tzinfo=UTC)
    ts = [int((base + timedelta(days=k)).timestamp()) for k in range(n)]
    if opens is None:
        opens = {a: [c[0]] + c[:-1] for a, c in closes.items()}
    return ap.PricePanel(ts=ts, opens=opens, closes=closes)


def _zero_fees(mode: str = ap.ALLOWANCE) -> ap.FeeModel:
    return ap.FeeModel(mode=mode, taker_pct=D(0), allowance_usd=D(500), slippage={})


def _fees(mode: str = ap.ALLOWANCE) -> ap.FeeModel:
    return ap.FeeModel(mode=mode, taker_pct=D("0.012"), allowance_usd=D(500), slippage={})


def _wiggly(n: int, base: int, step: int, mod: int) -> list[Decimal]:
    return [D(base) + D((k * step) % mod) / D(4) for k in range(n)]


# -- (a) arm A is the trusted benchmark under zero fees ------------------------------------------


def test_arm_a_zero_fee_reproduces_dca_into_allowlist_to_the_cent():
    n = 100  # 2024-01-01 .. 2024-04-09: four deposit months, the last one still fillable
    closes = {
        "BTC": _wiggly(n, 40_000, 37, 23),
        "ETH": _wiggly(n, 2_000, 11, 17),
        "SOL": _wiggly(n, 100, 5, 13),
    }
    panel = _panel((2024, 1, 1), closes)
    weights = {"BTC": D("0.5"), "ETH": D("0.3"), "SOL": D("0.2")}

    ours = ap.run_account(panel, weights, ap.static_dca, _zero_fees(), deposit=D(500))
    bench = dca_into_allowlist(
        prices_by_asset={a: list(zip(panel.ts, c, strict=True)) for a, c in closes.items()},
        target_weights=weights,
        monthly_contribution=D(500),
        months=4,
        fee_pct=D(0),
        slippage_pct=D(0),
    )

    assert ours.deposits == bench.contributions
    assert len(ours.deposits) == 4
    assert [ts for ts, _ in ours.equity_curve] == [ts for ts, _ in bench.equity_curve]
    assert [v.quantize(CENT) for _, v in ours.equity_curve] == [
        v.quantize(CENT) for _, v in bench.equity_curve
    ]


def test_arm_a_fills_at_the_next_open_not_the_benchmark_close():
    # The one place the two conventions part: a gap between close[d] and open[d+1]. Arm A buys at
    # the next open (spec §4, as `report.accumulation_table`); the benchmark at the close.
    closes = {"BTC": [D(100)] * 5}
    opens = {"BTC": [D(100), D(125), D(100), D(100), D(100)]}
    panel = _panel((2024, 1, 1), closes, opens)

    ours = ap.run_account(panel, {"BTC": D(1)}, ap.static_dca, _zero_fees())

    assert ours.lots["BTC"].qty == D(500) / D(125)
    assert ours.equity_curve[-1][1] == D(400)


# -- (b) each arm's formula on hand-built states -------------------------------------------------


def test_arm_a_buys_weight_times_deposit():
    weights = {"X": D("0.5"), "Y": D("0.3"), "Z": D("0.2")}
    prices = {"X": D(1), "Y": D(1), "Z": D(1)}
    holdings = {"X": D(0), "Y": D(0), "Z": D(0)}

    orders = ap.static_dca(1, prices, holdings, D(500), D(500), D(0), weights)

    assert orders == [
        ap.Order("X", ap.BUY, D(250)),
        ap.Order("Y", ap.BUY, D(150)),
        ap.Order("Z", ap.BUY, D(100)),
    ]


def test_arm_b_buys_up_to_the_value_path_and_never_sells_a_surplus():
    # t=3, D=100, w=.5/.5 -> V = 150 each. X holds $100 (buy 50), Y holds $200 (surplus: 0).
    weights = {"X": D("0.5"), "Y": D("0.5")}
    prices = {"X": D(100), "Y": D(50)}
    holdings = {"X": D(1), "Y": D(4)}

    orders = ap.value_averaging(3, prices, holdings, D(500), D(100), D(0), weights)

    assert orders == [ap.Order("X", ap.BUY, D(50))]


def test_arm_b_caps_each_buy_at_three_times_its_dca_slice():
    # t=10, empty book: V = 500 each, capped at C_max * w * D = 3 * .5 * 100 = 150.
    weights = {"X": D("0.5"), "Y": D("0.5")}
    prices = {"X": D(1), "Y": D(1)}
    holdings = {"X": D(0), "Y": D(0)}

    orders = ap.value_averaging(10, prices, holdings, D(10_000), D(100), D(0), weights)

    assert orders == [ap.Order("X", ap.BUY, D(150)), ap.Order("Y", ap.BUY, D(150))]


def test_arm_c_splits_the_deposit_by_shortfall():
    # H = 200, D = 100 -> targets .4/.4/.2 of 300 = 120/120/60; shortfalls 20/20/60 (sum 100).
    weights = {"X": D("0.4"), "Y": D("0.4"), "Z": D("0.2")}
    prices = {"X": D(1), "Y": D(1), "Z": D(1)}
    holdings = {"X": D(100), "Y": D(100), "Z": D(0)}

    orders = ap.shortfall_steered(2, prices, holdings, D(100), D(100), D(0), weights)

    assert orders == [
        ap.Order("X", ap.BUY, D(20)),
        ap.Order("Y", ap.BUY, D(20)),
        ap.Order("Z", ap.BUY, D(60)),
    ]


def test_arm_c_sends_everything_to_the_only_underweight():
    # X 300, Y 100, D 200 -> targets 300/300; s_X = 0, s_Y = 200: all of D to Y, none to X.
    weights = {"X": D("0.5"), "Y": D("0.5")}
    prices = {"X": D(1), "Y": D(1)}
    holdings = {"X": D(300), "Y": D(100)}

    orders = ap.shortfall_steered(2, prices, holdings, D(200), D(200), D(0), weights)

    assert orders == [ap.Order("Y", ap.BUY, D(200))]


def test_arm_d_buys_like_a_then_trims_the_upper_breach_and_redeploys():
    # Post-buy: X 400+50 = 450, Y 100+50 = 150, H = 600. X share .75 > .5 + .075 -> trim to 300:
    # sell $150 = 15 units at $10. Y is the only underweight (shortfall 150): redeploy share 1.
    weights = {"X": D("0.5"), "Y": D("0.5")}
    prices = {"X": D(10), "Y": D(1)}
    holdings = {"X": D(40), "Y": D(100)}

    orders = ap.dca_with_trimming(2, prices, holdings, D(100), D(100), D(0), weights)

    assert orders == [
        ap.Order("X", ap.BUY, D(50)),
        ap.Order("Y", ap.BUY, D(50)),
        ap.Order("X", ap.SELL, D(15)),
        ap.Order("Y", ap.REDEPLOY, D(1)),
    ]


# -- (c) the allowance split ---------------------------------------------------------------------


def test_buys_within_the_allowance_are_free_and_the_excess_pays_taker():
    fees = _fees(ap.ALLOWANCE)
    assert fees.buy_fee(D(100), month_used=D(0)) == D(0)
    assert fees.buy_fee(D(100), month_used=D(400)) == D(0)  # lands exactly on A: still free
    assert fees.buy_fee(D(300), month_used=D(400)) == D(200) * D("0.012")
    assert fees.buy_fee(D(100), month_used=D(500)) == D(100) * D("0.012")
    assert fees.buy_fee(D(100), month_used=D(900)) == D(100) * D("0.012")


def test_sells_always_pay_taker_and_flat_mode_taxes_every_leg():
    allowance, flat = _fees(ap.ALLOWANCE), _fees(ap.FLAT)
    assert allowance.sell_fee(D(1000)) == D(12)
    assert flat.sell_fee(D(1000)) == D(12)
    assert flat.buy_fee(D(100), month_used=D(0)) == D("1.2")
    assert flat.buy_fee(D(300), month_used=D(400)) == D("3.6")


def _trim_path() -> ap.PricePanel:
    # Jan: X and Y flat at 100. From Feb 1: X at 300. Gapless, so Feb's fill opens at 300.
    n = 34  # 2024-01-01 .. 2024-02-03
    x = [D(100)] * 31 + [D(300)] * 3
    return _panel((2024, 1, 1), {"X": x, "Y": [D(100)] * n})


def test_arm_d_run_charges_the_sale_and_the_redeploy_beyond_the_allowance():
    weights = {"X": D("0.5"), "Y": D("0.5")}
    result = ap.run_account(_trim_path(), weights, ap.dca_with_trimming, _fees(ap.ALLOWANCE))

    # Feb decision: X 2.5 units * 300 = 750 (+250 buy = 1000), Y 250 (+250 = 500), H = 1500.
    # X share .667 > .575: sell down to 750 -> $250 = 250/300 units, filled at the 300 open.
    sold = D(250) / D(300)
    gross = sold * D(300)
    sell_fee = gross * D("0.012")
    proceeds = gross - sell_fee
    # Two $500 DCA buys used the allowance; the whole redeploy is beyond it.
    assert result.sell_fees == sell_fee
    assert result.buy_fees == proceeds * D("0.012")
    assert result.sell_notional == gross
    assert result.buy_notional == D(1000) + proceeds


def test_flat_mode_taxes_the_dca_buys_as_well():
    weights = {"X": D("0.5"), "Y": D("0.5")}
    result = ap.run_account(_trim_path(), weights, ap.dca_with_trimming, _fees(ap.FLAT))

    # Jan buys 250 each at fee .012: X qty = 247/100. Feb buy 250 at 300: X qty += 247/300.
    x_qty = D(247) / D(100)
    x_value = x_qty * D(300) + D(250)  # post-buy X at the decision close
    y_value = D(247) / D(100) * D(100) + D(250)
    total = x_value + y_value
    sell_value = x_value - D("0.5") * total
    sold = sell_value / D(300)
    gross = sold * D(300)
    proceeds = gross - gross * D("0.012")
    assert result.buy_fees == D(1000) * D("0.012") + proceeds * D("0.012")
    assert result.sell_fees == gross * D("0.012")


def test_arm_a_is_free_under_the_allowance_and_pays_on_every_buy_when_flat():
    closes = {"X": [D(100)] * 70}  # 2024-01-01 .. 2024-03-10: three deposits
    panel = _panel((2024, 1, 1), closes)
    allowance = ap.run_account(panel, {"X": D(1)}, ap.static_dca, _fees(ap.ALLOWANCE))
    flat = ap.run_account(panel, {"X": D(1)}, ap.static_dca, _fees(ap.FLAT))
    assert allowance.buy_fees == D(0)
    assert flat.buy_fees == D(1500) * D("0.012")
    assert allowance.sell_fees == flat.sell_fees == D(0)


def test_slippage_worsens_every_leg_per_asset():
    fees = ap.FeeModel(
        mode=ap.ALLOWANCE, taker_pct=D(0), allowance_usd=D(500), slippage={"X": D("0.01")}
    )
    closes = {"X": [D(100)] * 5, "Y": [D(100)] * 5}
    result = ap.run_account(
        _panel((2024, 1, 1), closes), {"X": D("0.5"), "Y": D("0.5")}, ap.static_dca, fees
    )
    assert result.lots["X"].qty == D(250) / D(101)
    assert result.lots["Y"].qty == D(250) / D(100)


# -- (d) cash never goes negative ----------------------------------------------------------------


def test_arm_b_scales_down_proportionally_when_cash_is_short():
    # Wants 150 + 150 = 300 but holds 200: each buy scaled by 2/3 -> 100 each.
    weights = {"X": D("0.5"), "Y": D("0.5")}
    prices = {"X": D(1), "Y": D(1)}
    holdings = {"X": D(0), "Y": D(0)}

    orders = ap.value_averaging(10, prices, holdings, D(200), D(100), D(0), weights)

    assert orders == [ap.Order("X", ap.BUY, D(100)), ap.Order("Y", ap.BUY, D(100))]


def test_the_account_refuses_an_allocator_that_overspends():
    def greedy(t, prices, holdings, cash, deposit, month_buy_notional, weights):
        return [ap.Order("X", ap.BUY, cash + D(1))]

    panel = _panel((2024, 1, 1), {"X": [D(100)] * 5})
    with pytest.raises(ValueError, match="cash"):
        ap.run_account(panel, {"X": D(1)}, greedy, _zero_fees())


@pytest.mark.parametrize("arm", sorted(ap.ARMS))
def test_no_arm_ever_holds_negative_cash(arm):
    rng = random.Random(831)
    n = 200
    closes: dict[str, list[Decimal]] = {}
    for asset in ("X", "Y", "Z"):
        price, series = D(100), []
        for _ in range(n):
            price = price * (D(1) + D(rng.randint(-80, 80)) / D(1000))
            series.append(price)
        closes[asset] = series
    weights = {"X": D("0.5"), "Y": D("0.3"), "Z": D("0.2")}

    result = ap.run_account(_panel((2024, 1, 1), closes), weights, ap.ARMS[arm], _fees())

    assert len(result.cash_curve) == n
    assert min(cash for _, cash in result.cash_curve) >= 0


# -- (e) average-cost accounting -----------------------------------------------------------------


def test_lot_sells_at_average_cost():
    lot = ap.Lot()
    lot.buy(D(1), D(100))
    lot.buy(D(1), D(200))
    assert (lot.qty, lot.cost, lot.avg_cost) == (D(2), D(300), D(150))

    realized = lot.sell(D("0.5"), proceeds=D(100))

    assert realized == D(25)
    assert (lot.qty, lot.cost, lot.avg_cost) == (D("1.5"), D(225), D(150))


def test_lot_refuses_to_sell_more_than_it_holds():
    lot = ap.Lot()
    lot.buy(D(1), D(100))
    with pytest.raises(ValueError):
        lot.sell(D(2), proceeds=D(1))


def test_arm_d_realizes_the_trim_against_average_cost():
    weights = {"X": D("0.5"), "Y": D("0.5")}
    result = ap.run_account(_trim_path(), weights, ap.dca_with_trimming, _zero_fees())

    # X: 2.5 units for $250, then 250/300 units for $250 -> avg cost = 500 / (2.5 + 250/300).
    held = D("2.5") + D(250) / D(300)
    avg = D(500) / held
    sold = D(250) / D(300)
    assert result.realized_pnl == sold * D(300) - sold * avg
    assert result.lots["X"].qty == held - sold


# -- (f) arm D trims only upper-band breaches ----------------------------------------------------


@pytest.mark.parametrize(
    ("weight", "expected"),
    [
        (D("0.30"), D("0.045")),
        (D("0.20"), D("0.030")),
        (D("0.06"), D("0.015")),
        (D("0.075"), D("0.015")),
        (D("0.375"), D("0.05625")),
    ],
)
def test_band_is_fifteen_percent_relative_with_a_one_and_a_half_point_floor(weight, expected):
    assert ap.band(weight) == expected


def _d_orders(values: dict[str, Decimal], weights: dict[str, Decimal]) -> list[ap.Order]:
    # Zero deposit so the band check sees exactly `values`; prices of 1 make value == units.
    prices = {a: D(1) for a in values}
    return ap.dca_with_trimming(2, prices, values, D(0), D(0), D(0), weights)


def test_floor_band_six_percent_weight_has_a_four_and_a_half_to_seven_and_a_half_corridor():
    weights = {"S": D("0.06"), "B": D("0.94")}
    assert _d_orders({"S": D("7.4"), "B": D("92.6")}, weights) == []
    assert _d_orders({"S": D("7.5"), "B": D("92.5")}, weights) == []  # on the edge: not above
    assert _d_orders({"S": D("7.6"), "B": D("92.4")}, weights) == [
        ap.Order("S", ap.SELL, D("1.6")),
        ap.Order("B", ap.REDEPLOY, D(1)),
    ]


def test_a_lower_band_breach_alone_triggers_no_trade():
    weights = {"S": D("0.06"), "B": D("0.94")}
    assert _d_orders({"S": D("4.4"), "B": D("95.6")}, weights) == []


def test_in_band_overweights_are_left_alone_and_proceeds_go_by_shortfall():
    # X .36 > .345 is trimmed by 6. Y .22 is overweight but inside .23: untouched, and gets
    # nothing. Z (.42 vs .50) is the only underweight.
    weights = {"X": D("0.3"), "Y": D("0.2"), "Z": D("0.5")}
    assert _d_orders({"X": D(36), "Y": D(22), "Z": D(42)}, weights) == [
        ap.Order("X", ap.SELL, D(6)),
        ap.Order("Z", ap.REDEPLOY, D(1)),
    ]


def test_trim_proceeds_split_across_underweights_by_shortfall():
    # X trimmed by 6; Y short 4, Z short 2 -> shares 4/6 and 2/6.
    weights = {"X": D("0.3"), "Y": D("0.2"), "Z": D("0.5")}
    assert _d_orders({"X": D(36), "Y": D(16), "Z": D(48)}, weights) == [
        ap.Order("X", ap.SELL, D(6)),
        ap.Order("Y", ap.REDEPLOY, D(4) / D(6)),
        ap.Order("Z", ap.REDEPLOY, D(2) / D(6)),
    ]


# -- (g) the time-weighted return neutralises deposits -------------------------------------------


@pytest.mark.parametrize("arm", sorted(ap.ARMS))
def test_flat_prices_give_zero_twr_despite_deposits(arm):
    panel = _panel((2024, 1, 1), {"X": [D(100)] * 70, "Y": [D(50)] * 70})
    result = ap.run_account(panel, {"X": D("0.5"), "Y": D("0.5")}, ap.ARMS[arm], _fees())

    assert result.equity_curve[-1][1] == D(1500)
    returns = ap.twr_returns(result)
    assert len(returns) == 69
    assert set(returns) == {D(0)}
    assert {v for _, v in ap.twr_index(result)} == {D(1)}


def test_twr_is_the_policy_return_not_the_money_weighted_one():
    # 5 units bought at 100; the price doubles mid-January; a Feb deposit adds cash, not return.
    closes = [D(100)] * 14 + [D(200)] * 19  # 2024-01-01 .. 2024-02-02
    result = ap.run_account(
        _panel((2024, 1, 1), {"X": closes}), {"X": D(1)}, ap.static_dca, _zero_fees()
    )

    index = ap.twr_index(result)
    assert index[0] == (result.equity_curve[0][0], D(1))
    assert index[13][1] == D(1)
    assert index[14][1] == D(2)
    assert index[-1][1] == D(2)
    assert result.equity_curve[-1][1] == D(1500)


def test_summarize_reports_the_account_on_a_hand_computed_flat_path():
    panel = _panel((2024, 1, 1), {"X": [D(100)] * 63})  # 2024-01-01 .. 2024-03-03
    result = ap.run_account(panel, {"X": D(1)}, ap.static_dca, _fees())

    s = ap.summarize(result)

    assert s["terminal_value"] == D(1500)
    assert s["deposited"] == D(1500)
    assert s["buy_fees"] == D(0)
    assert s["sell_fees"] == D(0)
    assert s["turnover"] == D(1)
    assert s["twr_total"] == D(0)
    assert s["sortino"] == D(0)
    assert s["max_drawdown"] == D(0)
    # Cash share: 1 on Jan 1 (all cash), 1/2 on Feb 1, 1/3 on Mar 1, 0 on every other day.
    assert s["avg_cash_share"] == (D(1) + D(500) / D(1000) + D(500) / D(1500)) / D(63)
    assert abs(s["irr"]) < D("0.000001")


# -- (h) the stationary block bootstrap ----------------------------------------------------------


def test_bootstrap_indices_are_deterministic_under_a_seed():
    a = ap.stationary_bootstrap_indices(500, 20, random.Random(7))
    b = ap.stationary_bootstrap_indices(500, 20, random.Random(7))
    c = ap.stationary_bootstrap_indices(500, 20, random.Random(8))
    assert a == b
    assert a != c
    assert len(a) == 500
    assert all(0 <= i < 500 for i in a)


@pytest.mark.parametrize("mean_block", [20, 60])
def test_bootstrap_mean_block_length_is_on_target(mean_block):
    n = 200_000
    idx = ap.stationary_bootstrap_indices(n, mean_block, random.Random(1))
    starts = 1 + sum(1 for prev, cur in zip(idx, idx[1:]) if cur != (prev + 1) % n)
    mean = n / starts
    assert abs(mean - mean_block) / mean_block < 0.05


def test_resample_panel_moves_joint_rows_with_the_same_index_for_every_asset():
    panel = _panel(
        (2024, 1, 1),
        closes={
            "X": [D(100), D(200), D(100), D(300)],
            "Y": [D(10), D(5), D(20), D(20)],
        },
        opens={
            "X": [D(100), D(110), D(190), D(105)],
            "Y": [D(10), D(11), D(6), D(19)],
        },
    )

    path = ap.resample_panel(panel, [2, 0, 1])

    assert path.ts == panel.ts
    # Day 1 takes row 2 (X x3 gap 1.05; Y x1 gap .95), day 2 row 0, day 3 row 1 -- both assets.
    assert path.closes["X"] == [D(100), D(300), D(600), D(300)]
    assert path.opens["X"] == [D(100), D(105), D(330), D(570)]
    assert path.closes["Y"] == [D(10), D(10), D(5), D(20)]
    assert path.opens["Y"] == [D(10), D("9.5"), D(11), D(6)]


def test_bootstrap_deltas_are_deterministic_and_zero_for_the_baseline():
    rng = random.Random(3)
    closes = {a: [D(100) + D(rng.randint(-20, 20)) for _ in range(70)] for a in ("X", "Y")}
    panel = _panel((2024, 1, 1), closes)
    weights = {"X": D("0.5"), "Y": D("0.5")}

    first = ap.bootstrap_deltas(panel, weights, _fees(), mean_block=5, n_paths=3, seed=11)
    again = ap.bootstrap_deltas(panel, weights, _fees(), mean_block=5, n_paths=3, seed=11)

    assert first == again
    assert sorted(first) == ["A", "B", "C", "D"]
    assert all(len(v) == 3 for v in first.values())
    assert first["A"] == [(D(0), D(0))] * 3


# -- (i) the decision rule -----------------------------------------------------------------------


def _deltas(n_positive: int, n: int = 20, mdd: Decimal = D("-0.01")) -> list[tuple[D, D]]:
    return [(D("0.1") if k < n_positive else D("-0.1"), mdd) for k in range(n)]


def test_passes_at_exactly_ninety_five_percent_with_a_drawdown_no_worse():
    assert ap.passes(_deltas(19)) is True
    assert ap.passes(_deltas(18)) is False
    assert ap.passes(_deltas(20, mdd=D(0))) is True
    assert ap.passes(_deltas(20, mdd=D("0.001"))) is False


def test_a_zero_sortino_difference_is_not_an_improvement():
    tied = [(D(0), D(0))] * 20
    assert ap.passes(tied) is False


def test_verdict_branches():
    good, bad = _deltas(20), _deltas(10)
    assert ap.verdict({20: good, 60: good}) == ap.BETTER
    assert ap.verdict({20: good, 60: bad}) == ap.BLOCK_DEPENDENT
    assert ap.verdict({20: bad, 60: good}) == ap.BLOCK_DEPENDENT
    assert ap.verdict({20: bad, 60: bad}) == ap.NOT_BETTER


def test_verdict_refuses_a_missing_block_length():
    with pytest.raises(ValueError):
        ap.verdict({20: _deltas(20)})
    with pytest.raises(ValueError):
        ap.passes([])
