"""#821: the REAL `Dca` rule through the account sim, the edge table and the rendered report.

`test_portfolio_sim.py`'s DCA coverage used a stub that fires once, which is why none of this was
caught: the real rule fires on every hourly bar of a cadence day (24 buys, not one), read the
still-forming day, got N 0 in the edge table (it never saw daily candles), and its sleeve was
bought but never shown in the report.

Fixtures are synthetic and exact: a flat $100 market (so every $50 buy is exactly 0.5 units at
zero fee/slippage), with the very last close moved to $120 so a mark-to-market differs from cost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from keel.commands.simulate import build_account_metrics
from keel.config import Caps, Config, DcaConfig, MarketDataConfig, SubscriptionConfig
from keel.sim import portfolio_sim, report
from keel.sim.benchmark import BenchmarkResult
from keel.sim.portfolio_sim import SimTelemetry
from keel.sim.report import accumulation_table, rule_keys
from keel.strategy.rules.base import Rule, Setup
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.reverse_dca import ReverseDca
from keel.types import Candle, Granularity

_HOUR = 3_600
_DAY = 86_400
_DAYS = 60
_START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
_START_DAY = _START // _DAY
_BUDGET = Decimal("50")
_CADENCE = 7
_ZERO = Decimal("0")


def _bar(ts: int, close: str = "100") -> Candle:
    c = Decimal(close)
    return Candle(
        ts=ts, open=Decimal("100"), high=max(c, Decimal("100")), low=Decimal("100"), close=c,
        volume=Decimal("10"),
    )  # fmt: skip


def _market() -> dict[str, dict[Granularity, list[Candle]]]:
    hourly = [_bar(_START + i * _HOUR) for i in range(_DAYS * 24)]
    daily = [_bar(_START + d * _DAY) for d in range(_DAYS)]
    hourly[-1] = _bar(hourly[-1].ts, "120")
    daily[-1] = _bar(daily[-1].ts, "120")
    return {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: daily}}


def _cadence_days() -> list[int]:
    """Epoch days in the window that are cadence days AND have a following day in the window
    (the buy is decided once that day has closed, i.e. on the next day, and fills there)."""
    days = range(_START_DAY, _START_DAY + _DAYS - 1)
    return [d for d in days if d % _CADENCE == 0]


def _dca() -> Dca:
    return Dca("BTC-USD", cadence_days=_CADENCE, budget_usd=_BUDGET)


def _config(max_per_order_usd: Decimal = Decimal("1000000")) -> Config:
    return Config(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.02"),
        caps=Caps(
            max_per_order_usd=max_per_order_usd,
            max_per_day_usd=Decimal("1000000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        subscription=SubscriptionConfig(
            assumed_free_volume_usd=Decimal("1000000"), pacing="opportunistic"
        ),
        dca=DcaConfig(budget_usd=_BUDGET),
    )


def _run() -> portfolio_sim.SimResult:
    market = _market()
    hourly = market["BTC"][Granularity.ONE_HOUR]
    return portfolio_sim.run(
        [_dca()],
        market,
        _config(),
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
        fee_pct=_ZERO,
        slippage_pct=_ZERO,
    )


# ---------------------------------------------------------------------------
# (a) the account sim buys once per cadence day, not once per hourly bar
# ---------------------------------------------------------------------------


def test_the_sim_buys_the_budget_once_per_cadence_day_not_every_hour():
    cadence = _cadence_days()
    assert len(cadence) == 8  # the fixture really spans several cadence days

    result = _run()

    lot = result.dca_positions["BTC"]
    # Flat $100 at zero cost: each $50 buy is exactly 0.5 units. Before #821 this was ~24x.
    assert lot.qty == Decimal("0.5") * len(cadence)
    assert lot.qty * lot.entry_fill == _BUDGET * len(cadence)

    buys = result.dca_buys
    assert [b.decision_ts // _DAY for b in buys] == [d + 1 for d in cadence]
    assert [b.notional for b in buys] == [_BUDGET] * len(cadence)
    assert sum((b.notional for b in buys), _ZERO) == _BUDGET * len(cadence)
    assert {(b.asset, b.rule_kind) for b in buys} == {("BTC", "dca")}


# ---------------------------------------------------------------------------
# (d) the edge table: DCA is its own accumulation row, never a round trip
# ---------------------------------------------------------------------------


class _OneShot(Rule):
    """A risk-defined rule that wins once -- gives `__pooled__` something real to compare."""

    name = "one_shot"
    params: dict = {}

    def __init__(self, product_id: str, trigger_ts: int) -> None:
        self.product_id = product_id
        self.trigger_ts = trigger_ts

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        latest = candles_by_tf[Granularity.ONE_HOUR][-1]
        if latest.ts != self.trigger_ts:
            return None
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            context={},
            ts=latest.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


def test_edge_table_keeps_dca_out_of_the_round_trips_and_the_pool():
    market = _market()
    other = _OneShot("BTC-USD", market["BTC"][Granularity.ONE_HOUR][5].ts)

    with_dca = report.edge_table([other, _dca()], market, fee_pct=_ZERO, slippage_pct=_ZERO)
    without = report.edge_table([other], market, fee_pct=_ZERO, slippage_pct=_ZERO)

    assert list(with_dca) == ["one_shot:BTC", report.POOLED_KEY]
    assert repr(with_dca[report.POOLED_KEY]) == repr(without[report.POOLED_KEY])
    # G2 is neither fed fake round trips nor a zero-trade sample for the DCA rule.
    assert report.group_trades_by_class(with_dca, [_dca()]) == {}


def test_accumulation_table_reports_buys_cost_and_mark_for_a_real_dca():
    cadence = _cadence_days()

    rows = report.accumulation_table([_dca()], _market(), fee_pct=_ZERO, slippage_pct=_ZERO)

    assert list(rows) == ["dca:BTC"]
    row = rows["dca:BTC"]
    n = len(cadence)
    assert row.buys == n
    assert row.qty == Decimal("0.5") * n
    assert row.cost_usd == _BUDGET * n
    assert row.last_close == Decimal("120")
    assert row.value_usd == Decimal("0.5") * n * Decimal("120")
    assert row.unrealized_pnl == row.value_usd - row.cost_usd


def test_accumulation_table_charges_fee_and_slippage_in_the_cost_basis():
    fee, slip = Decimal("0.01"), Decimal("0.002")

    row = report.accumulation_table([_dca()], _market(), fee_pct=fee, slippage_pct=slip)["dca:BTC"]

    n = len(_cadence_days())
    per_buy = Decimal("0.5") * Decimal("100") * (1 + slip) * (1 + fee)
    assert row.cost_usd == per_buy * n


def test_accumulation_table_ignores_risk_defined_rules():
    market = _market()
    other = _OneShot("BTC-USD", market["BTC"][Granularity.ONE_HOUR][5].ts)
    assert report.accumulation_table([other], market, fee_pct=_ZERO, slippage_pct=_ZERO) == {}


# ---------------------------------------------------------------------------
# (e) the account metrics expose the DCA sleeve, marked at the last close
# ---------------------------------------------------------------------------


def test_account_metrics_expose_the_sleeve_marked_to_market():
    result = _run()
    market = _market()
    hourly = market["BTC"][Granularity.ONE_HOUR]

    metrics = build_account_metrics(result, hourly[0].ts, hourly[-1].ts)

    sleeve = metrics["dca_sleeve"]
    assert list(sleeve) == ["BTC"]
    row = sleeve["BTC"]
    n = len(_cadence_days())
    assert row.buys == n
    assert row.qty == result.dca_positions["BTC"].qty
    assert row.last_close == Decimal("120")
    assert row.cost_usd == _BUDGET * n
    assert row.value_usd == row.qty * Decimal("120")
    assert row.unrealized_pnl == row.value_usd - row.cost_usd
    assert row.unrealized_pnl == Decimal("10") * n  # 0.5 units x $20 up, per buy
    # The sleeve is not a closed trade and never inflates the round-trip count.
    assert metrics["trade_count"] == 0
    assert metrics["per_asset_pnl"] == {}


# ---------------------------------------------------------------------------
# (f) the rendered report has both sections, as tables
# ---------------------------------------------------------------------------


def _table_under(md: str, heading: str) -> list[list[str]]:
    """The rows (header first, separator dropped) of the first Markdown table after `heading`."""
    lines = md.splitlines()
    start = lines.index(heading)
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= {"-"} for c in cells):
                rows.append(cells)
        elif rows:
            break
    return rows


def _benchmark() -> BenchmarkResult:
    return BenchmarkResult(
        name="bench",
        equity_curve=[],
        contributions=[],
        ending_value=_ZERO,
        total_return_pct=_ZERO,
        max_drawdown_pct=_ZERO,
        sharpe=_ZERO,
        sortino=_ZERO,
        return_per_drawdown=_ZERO,
    )


def test_render_markdown_shows_the_sleeve_and_the_accumulation_row():
    result = _run()
    market = _market()
    hourly = market["BTC"][Granularity.ONE_HOUR]
    metrics = build_account_metrics(result, hourly[0].ts, hourly[-1].ts)
    edge = report.edge_table([_dca()], market, fee_pct=_ZERO, slippage_pct=_ZERO)
    accumulation = report.accumulation_table([_dca()], market, fee_pct=_ZERO, slippage_pct=_ZERO)
    verdict = report.Verdict("TRAIN MORE", ["x"], True, False, False)

    md = report.render_markdown(
        result,
        edge,
        metrics,
        _benchmark(),
        verdict,
        report.analyze_gaps(SimTelemetry(), {}, move_threshold_pct=Decimal("0.05")),
        accumulation=accumulation,
    )

    n = len(_cadence_days())
    header = ["Rule", "Buys", "Qty", "Cost basis", "Last close", "Value", "Unrealized P&L"]
    acc = _table_under(md, "## DCA accumulation (not round trips)")
    assert acc[0] == header
    a = accumulation["dca:BTC"]
    assert (a.buys, a.cost_usd, a.value_usd) == (n, _BUDGET * n, Decimal("60") * n)
    assert acc[1:] == [
        [
            "dca:BTC",
            str(n),
            str(a.qty),
            str(a.cost_usd),
            str(a.last_close),
            str(a.value_usd),
            str(a.unrealized_pnl),
        ]
    ]
    # The DCA rule has no row in the round-trip edge table.
    edge_rows = _table_under(md, "## Edge table")
    assert [r[0] for r in edge_rows[1:]] == [f"**{report.POOLED_KEY}**"]

    sleeve = _table_under(md, "### DCA sleeve (accumulation, marked to market)")
    assert sleeve[0] == ["Asset", *header[1:]]
    row = metrics["dca_sleeve"]["BTC"]
    assert sleeve[1:] == [
        [
            "BTC",
            str(n),
            str(row.qty),
            str(row.cost_usd),
            str(row.last_close),
            str(row.value_usd),
            str(row.unrealized_pnl),
        ]
    ]
    assert md.index("## Account results") < md.index(
        "### DCA sleeve (accumulation, marked to market)"
    )


def test_per_asset_pnl_is_labelled_as_realized_rule_pnl():
    metrics = {"per_asset_pnl": {"ETH": Decimal("5")}, "dca_sleeve": {}}

    lines = report._render_account_section(metrics)

    label = "Per-asset realized rule P&L (closed round trips; the DCA sleeve is below):"
    assert lines[lines.index(label) + 1 :] == ["- ETH: 5"]
    assert "### DCA sleeve (accumulation, marked to market)" not in lines


def test_backtest_still_gives_dca_no_round_trips():
    """`keel rules promote`'s docstring relies on this: a DCA rule produces no backtest trades,
    so `--force` stays its only way to paper. The accumulation row is not trades and nothing on
    the promotion path reads it; the round-trip backtest itself is unchanged by #821."""
    from keel.strategy.backtest import backtest

    daily = _market()["BTC"][Granularity.ONE_DAY]
    assert backtest(_dca(), daily, fee_pct=_ZERO, slippage_pct=_ZERO).n_trades == 0


# ---------------------------------------------------------------------------
# (g) the reverse path: `reverse_dca` distributions in the accumulation row (#857, P11)
#
# Fidelity checks of the harness against hand computations on synthetic, gapless candles
# (spec §6, "Evidence status"). No real history is read, and nothing here is a verdict.
# ---------------------------------------------------------------------------

_Q8 = Decimal("0.00000001")
_CENT = Decimal("0.01")


def _candle(day: int, close: str) -> Candle:
    """A daily bar for epoch day `day`, flat at `close` (so the next bar's open is its price)."""
    c = Decimal(close)
    return Candle(ts=day * _DAY, open=c, high=c, low=c, close=c, volume=Decimal("10"))


def _rev(**overrides: object) -> ReverseDca:
    kw: dict = dict(target_usd=Decimal("10"), min_price_floor=Decimal("1"))
    kw.update(overrides)
    return ReverseDca("BTC-USD", **kw)


def test_the_row_keys_are_rule_keys() -> None:
    dca, rev = Dca("BTC-USD", cadence_days=7), _rev()
    daily = [_candle(d, "100") for d in range(61)]

    rows = accumulation_table(
        [dca, rev], {"BTC": {Granularity.ONE_DAY: daily}}, fee_pct=_ZERO, slippage_pct=_ZERO
    )

    assert list(rows) == rule_keys([dca, rev]) == ["dca:BTC", "reverse_dca:BTC"]


def test_a_distribution_reproduces_the_hand_computation_on_gapless_candles() -> None:
    """61 flat days at 100. DCA every 7 days, $50, fee 1%, no slippage: buys decided on days
    0,7,...,56 (9 buys, 0.5 each, $50.50 each). reverse_dca every 30 days, $10 net: day 0 is a
    DCA day and holds nothing; day 30 sells gross 10/0.99 at day 31's open 100, i.e. 0.10101010
    units; its FIFO cost is 0.1010101 x 100 x 1.01; so realised = 10.00 - 10.20 = -0.20."""
    daily = [_candle(d, "100") for d in range(61)]
    dca = Dca("BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
    rev = _rev()

    rows = accumulation_table(
        [dca, rev],
        {"BTC": {Granularity.ONE_DAY: daily}},
        fee_pct=Decimal("0.01"),
        slippage_pct=Decimal("0"),
    )

    out = rows["reverse_dca:BTC"]
    assert out.distributions == 1
    assert out.units_sold.quantize(_Q8) == Decimal("0.10101010")
    assert out.distributed_usd.quantize(_CENT) == Decimal("10.00")
    assert out.sell_fees.quantize(_CENT) == Decimal("0.10")
    assert out.realised_pnl.quantize(_CENT) == Decimal("-0.20")
    # The seller's row holds and bought nothing: its columns are the sale's.
    assert (out.buys, out.qty, out.cost_usd, out.value_usd) == (0, _ZERO, _ZERO, _ZERO)
    kept = rows["dca:BTC"]
    assert kept.buys == 9
    assert (kept.qty + out.units_sold) == Decimal("4.5")
    # The buyer's row carries no sale columns: the sale is the seller's.
    assert (kept.distributions, kept.units_sold, kept.realised_pnl) == (0, _ZERO, _ZERO)


def test_the_buyer_row_reports_its_remaining_lots_cost_after_the_sale() -> None:
    """The kept row's cost basis is what the REMAINING units cost, fee-inclusive: 9 x $50.50
    bought, less the 0.10101010 units sold at $101 each (100 x 1.01)."""
    daily = [_candle(d, "100") for d in range(61)]
    rows = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), _rev()],
        {"BTC": {Granularity.ONE_DAY: daily}},
        fee_pct=Decimal("0.01"),
        slippage_pct=_ZERO,
    )

    kept, out = rows["dca:BTC"], rows["reverse_dca:BTC"]
    assert kept.cost_usd.quantize(_Q8) == (
        Decimal("9") * Decimal("50.50") - out.units_sold * Decimal("101")
    ).quantize(_Q8)
    assert kept.value_usd == kept.qty * Decimal("100")


def test_the_sale_consumes_the_oldest_lot_first_not_the_average() -> None:
    """FIFO, not average cost (R32). The day-7 buy is sized at day 7's close (100, so 0.5 units)
    and fills at day 8's open, 200; later buys are 0.25 units at 200. Day 30 sells gross
    10/0.99 at 200 = 0.050505 units, all from the day-1 lot bought at 100: cost
    0.050505 x 100 x 1.01 = 5.10, so realised = 10.00 - 5.10 = 4.90. An average cost (300 over
    1.75 units, i.e. 171.43) would have said 1.26."""
    daily = [_candle(d, "100") for d in range(8)] + [_candle(d, "200") for d in range(8, 40)]
    rows = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), _rev()],
        {"BTC": {Granularity.ONE_DAY: daily}},
        fee_pct=Decimal("0.01"),
        slippage_pct=_ZERO,
    )

    out = rows["reverse_dca:BTC"]
    assert out.distributions == 1
    sold = Decimal("10") / Decimal("0.99") / Decimal("200")
    assert out.units_sold == sold
    assert out.realised_pnl.quantize(_CENT) == Decimal("4.90")
    assert out.realised_pnl == out.distributed_usd - sold * Decimal("100") * Decimal("1.01")


def test_each_dca_rule_keeps_its_own_lots_and_the_oldest_rule_lot_is_sold_first() -> None:
    """Two DCA rules on one asset buy on the same day; the lots are one FIFO pool per asset,
    oldest first and, within a day, in rule order. The sale reaches the first rule's lot only."""
    first = Dca("BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
    second = Dca("BTC-USD", cadence_days=7, budget_usd=Decimal("5"))
    rules: list[Rule] = [first, second, _rev()]
    daily = [_candle(d, "100") for d in range(33)]

    rows = accumulation_table(
        rules, {"BTC": {Granularity.ONE_DAY: daily}}, fee_pct=_ZERO, slippage_pct=_ZERO
    )

    keys = rule_keys(rules)
    assert keys == ["dca#1:BTC", "dca#2:BTC", "reverse_dca:BTC"]
    # Five buys each (days 0, 7, 14, 21, 28): 2.5 and 0.25 units.
    sold = rows["reverse_dca:BTC"].units_sold
    assert sold == Decimal("0.1")
    assert rows["dca#1:BTC"].qty == Decimal("2.5") - sold
    assert rows["dca#2:BTC"].qty == Decimal("0.25")


def test_the_run_is_deterministic() -> None:
    rules: list[Rule] = [Dca("BTC-USD", cadence_days=7), _rev()]
    candles = {"BTC": {Granularity.ONE_DAY: [_candle(d, str(100 + d % 5)) for d in range(120)]}}
    kw = dict(fee_pct=Decimal("0.012"), slippage_pct=Decimal("0.0005"))

    first = accumulation_table(rules, candles, **kw)
    assert first["reverse_dca:BTC"].distributions == 3, "fixture: days 30, 60 and 90 sell"
    assert first == accumulation_table(rules, candles, **kw)


def test_a_distribution_on_a_dca_day_is_skipped_in_the_sim_as_live() -> None:
    daily = [_candle(d, "100") for d in range(212)]
    rows = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), _rev()],
        {"BTC": {Granularity.ONE_DAY: daily}},
        fee_pct=Decimal("0.01"),
        slippage_pct=Decimal("0"),
    )
    assert rows["reverse_dca:BTC"].distributions == 6, "days 30..180 sell; 210 is a DCA day"


def test_min_hold_days_refuses_a_sale_that_would_consume_a_young_lot() -> None:
    """The day-30 sale fills at day 31, exactly 30 days after the day-1 lot: at the default
    30 it sells, at 31 it is refused (R12), and the refusal is not carried to day 31."""
    daily = [_candle(d, "100") for d in range(61)]
    candles = {"BTC": {Granularity.ONE_DAY: daily}}

    def _distributions(min_hold_days: int) -> int:
        rows = accumulation_table(
            [Dca("BTC-USD", cadence_days=7), _rev(min_hold_days=min_hold_days)],
            candles,
            fee_pct=_ZERO,
            slippage_pct=_ZERO,
        )
        return rows["reverse_dca:BTC"].distributions

    assert _distributions(30) == 1
    assert _distributions(31) == 0


def test_rail_2_slices_the_distribution_to_one_leg_per_cadence_day() -> None:
    """With a $5 per-order cap the $10 distribution is one $5 leg (0.05 units at 100); the rest
    is not carried to the next day (`sleeve.slice_qty`, the one slicer live uses)."""
    daily = [_candle(d, "100") for d in range(61)]
    rows = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), _rev()],
        {"BTC": {Granularity.ONE_DAY: daily}},
        fee_pct=_ZERO,
        slippage_pct=_ZERO,
        max_per_order_usd=Decimal("5"),
    )

    out = rows["reverse_dca:BTC"]
    assert (out.distributions, out.units_sold, out.distributed_usd) == (
        1,
        Decimal("0.05"),
        Decimal("5"),
    )


def test_a_sale_pays_slippage_and_the_fee() -> None:
    """The sale fills at the next open x (1 - slippage) and pays the fee on that notional."""
    daily = [_candle(d, "100") for d in range(61)]
    fee, slip = Decimal("0.01"), Decimal("0.002")
    out = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), _rev()],
        {"BTC": {Granularity.ONE_DAY: daily}},
        fee_pct=fee,
        slippage_pct=slip,
    )["reverse_dca:BTC"]

    gross_notional = out.units_sold * Decimal("100") * (1 - slip)
    assert out.sell_fees == gross_notional * fee
    assert out.distributed_usd == gross_notional - out.sell_fees


def test_lots_keep_distinct_ids_once_a_sale_fully_consumes_one() -> None:
    """#923: `Lot(position_id=len(lots), ...)` reuses an id once a sale drops a fully consumed
    lot from `lots`, so a later buy's id collides with a still-held lot's -- `consumed[...]`
    (keyed by `position_id`) then takes units meant for both. On a flat, zero-fee market every
    buy and every sale trade at the same price, so units bought must equal units still held plus
    units sold exactly, and nothing is created or destroyed (realised P&L must be exactly zero).
    400 days is long enough that several lots are fully consumed and dropped."""
    daily = [_candle(d, "100") for d in range(400)]
    dca = Dca("BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
    rev = _rev(target_usd=Decimal("50"))

    rows = accumulation_table(
        [dca, rev], {"BTC": {Granularity.ONE_DAY: daily}}, fee_pct=_ZERO, slippage_pct=_ZERO
    )

    kept, out = rows["dca:BTC"], rows["reverse_dca:BTC"]
    assert out.distributions > 1, "fixture: several sales, so a lot is fully consumed"
    bought = kept.buys * Decimal("0.5")
    assert kept.qty + out.units_sold == bought
    assert out.realised_pnl == _ZERO


# ---------------------------------------------------------------------------
# (h) the reverse path through the ACCOUNT sim (#857, P11): average cost, one averaged lot
# ---------------------------------------------------------------------------


def _hourly_market(closes: dict[int, str], days: int) -> dict[str, dict[Granularity, list[Candle]]]:
    """`days` epoch-aligned days from day 0, flat within a day at `closes[d]` (the latest key at
    or before `d`), so a buy decided on day `d`'s first hour fills at day `d`'s price."""

    def price(day: int) -> Decimal:
        return Decimal(closes[max(k for k in closes if k <= day)])

    def bar(ts: int, p: Decimal) -> Candle:
        return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("10"))

    hourly = [bar(h * _HOUR, price(h * _HOUR // _DAY)) for h in range(days * 24)]
    daily = [bar(d * _DAY, price(d)) for d in range(days)]
    return {"BTC": {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: daily}}


def _run_account(
    rules: list[Rule],
    market: dict[str, dict[Granularity, list[Candle]]],
    *,
    fee: Decimal = Decimal("0.01"),
    slippage: Decimal = _ZERO,
    config: Config | None = None,
    monthly_volume_cap: Decimal | None = None,
) -> portfolio_sim.SimResult:
    hourly = market["BTC"][Granularity.ONE_HOUR]
    return portfolio_sim.run(
        rules,
        market,
        config or _config(),
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=Decimal("100000"),
        fee_pct=fee,
        slippage_pct=slippage,
        monthly_volume_cap=monthly_volume_cap,
    )


def test_the_account_sim_reproduces_the_hand_computation() -> None:
    """The edge pass's hand computation, through the account sim: on a flat market the averaged
    lot's cost IS every lot's, so average cost and FIFO agree. Day 30's decision is taken on day
    31's first hour and fills at its second, at 100."""
    result = _run_account([Dca("BTC-USD", cadence_days=7), _rev()], _hourly_market({0: "100"}, 61))

    [sale] = result.dca_sells
    assert (sale.asset, sale.rule_kind) == ("BTC", "reverse_dca")
    assert (sale.decision_ts, sale.fill_ts) == (31 * _DAY, 31 * _DAY + _HOUR)
    assert sale.qty.quantize(_Q8) == Decimal("0.10101010")
    assert sale.expected_price == sale.fill_price == Decimal("100")
    assert sale.net_usd.quantize(_CENT) == Decimal("10.00")
    assert sale.fee_usd.quantize(_CENT) == Decimal("0.10")
    assert sale.realised_pnl.quantize(_CENT) == Decimal("-0.20")

    row = portfolio_sim.dca_sleeve(result)["BTC"]
    assert (row.buys, row.distributions) == (9, 1)
    assert row.qty + sale.qty == Decimal("4.5")
    assert (row.units_sold, row.distributed_usd, row.sell_fees, row.realised_pnl) == (
        sale.qty,
        sale.net_usd,
        sale.fee_usd,
        sale.realised_pnl,
    )
    assert row.cost_usd == sum((b.cost_usd for b in result.dca_buys), _ZERO) - sale.cost_basis


def test_the_account_sim_books_the_sale_at_average_cost_not_fifo() -> None:
    """R32, pinned: the same market as the FIFO test above. Lots: 0.5 @ 100, then 0.5 @ 200
    (sized at day 7's close, filled on day 8) and 3 x 0.25 @ 200 -- $300 over 1.75 units. The
    account sim's realised P&L is net less the sold units at that AVERAGE (x 1.01): 1.26, where
    the FIFO row says 4.90."""
    market = _hourly_market({0: "100", 8: "200"}, 40)
    rules: list[Rule] = [Dca("BTC-USD", cadence_days=7), _rev()]

    [sale] = _run_account(rules, market).dca_sells

    average = Decimal("300") / Decimal("1.75")
    assert sale.qty == Decimal("10") / Decimal("0.99") / Decimal("200")
    assert sale.realised_pnl == sale.net_usd - sale.qty * average * Decimal("1.01")
    assert sale.realised_pnl.quantize(_CENT) == Decimal("1.26")
    fifo = accumulation_table(
        rules, {"BTC": {Granularity.ONE_DAY: market["BTC"][Granularity.ONE_DAY]}},
        fee_pct=Decimal("0.01"), slippage_pct=_ZERO,
    )["reverse_dca:BTC"]  # fmt: skip
    assert fifo.realised_pnl.quantize(_CENT) == Decimal("4.90")


def test_the_account_sim_refuses_a_sale_that_reaches_a_lot_younger_than_min_hold_days() -> None:
    """#924: the account sim's refusal check used to build its `Holding` from ONE lot, opened at
    the averaged lot's FIRST buy (day 1) -- so `min_hold_days` only ever saw that oldest buy.
    Five weekly $50 buys (days 1, 8, 15, 22, 29) hold 2.5 units by day 29; an $80 net target on
    day 30 needs ~0.808 units, which FIFO reaches into the day-8 lot (23 days old at the day-31
    fill, under the default 30-day `min_hold_days`) -- live's `sleeve.sleeve_refusal` walks every
    FIFO lot the sale would consume and refuses it. The account sim must refuse it too, and agree
    with the edge pass, which was already FIFO-correct."""
    market = _hourly_market({0: "100"}, 40)
    rules: list[Rule] = [Dca("BTC-USD", cadence_days=7), _rev(target_usd=Decimal("80"))]

    result = _run_account(rules, market)

    fifo = accumulation_table(
        rules, {"BTC": {Granularity.ONE_DAY: market["BTC"][Granularity.ONE_DAY]}},
        fee_pct=Decimal("0.01"), slippage_pct=_ZERO,
    )["reverse_dca:BTC"]  # fmt: skip
    assert fifo.distributions == 0, "fixture: the edge pass, FIFO, refuses the whole sale"
    assert result.dca_sells == []


def test_the_account_sim_skips_a_distribution_on_a_dca_day_as_live() -> None:
    result = _run_account([Dca("BTC-USD", cadence_days=7), _rev()], _hourly_market({0: "100"}, 212))

    assert [s.decision_ts // _DAY for s in result.dca_sells] == [31, 61, 91, 121, 151, 181]


def test_the_account_sim_slices_to_the_configured_per_order_cap() -> None:
    """Rail 2, from the sim's own config: a $50 cap (which the $50 DCA buys still clear) sells
    one $50 leg of the ~$80 distribution -- 0.5 units at 100 -- and carries nothing over.

    `min_hold_days=0`: `sleeve.sleeve_refusal`'s `min_hold_days` walk is checked against the
    PRE-slice reduction (0.8 units, #924's per-buy FIFO holding), which reaches into the day-8
    lot (23 days old at the day-31 fill) before rail 2 ever slices it down to the $50 leg this
    test is about; zeroing `min_hold_days` isolates rail 2's own slicing from that separate gate
    (covered on its own by `test_min_hold_days_refuses_a_sale_that_would_consume_a_young_lot`)."""
    config = _config(max_per_order_usd=Decimal("50"))
    result = _run_account(
        [Dca("BTC-USD", cadence_days=7), _rev(target_usd=Decimal("80"), min_hold_days=0)],
        _hourly_market({0: "100"}, 61),
        fee=_ZERO,
        config=config,
    )

    [sale] = result.dca_sells
    assert sale.qty == Decimal("0.5")
    assert sale.qty * sale.expected_price == config.caps.max_per_order_usd


def test_the_account_sim_skips_a_distribution_that_would_breach_the_volume_cap() -> None:
    """Issue #86's throttled run: a sale is volume, so like a DCA buy it is SKIPPED when it
    would push the month past `monthly_volume_cap`. January buys $100 (two buys at 100; the
    third would breach $100); on 1 February, at 300, the $10-net sale is ~$10.10 against a fresh
    month -- but a $100-net target is ~$101, over the cap, and is skipped."""
    market = _hourly_market({0: "100", 30: "300"}, 40)

    def _sells(target: str, cap: Decimal | None) -> int:
        rules: list[Rule] = [Dca("BTC-USD", cadence_days=7), _rev(target_usd=Decimal(target))]
        return len(_run_account(rules, market, monthly_volume_cap=cap).dca_sells)

    assert _sells("100", None) == 1, "fixture: uncapped, the day-30 distribution sells"
    assert _sells("10", Decimal("100")) == 1, "fixture: a sale under the cap still sells"
    assert _sells("100", Decimal("100")) == 0


# ---------------------------------------------------------------------------
# (i) the rendered tables carry the sell columns once anything was distributed
# ---------------------------------------------------------------------------

_SELL_HEADER = ["Distributions", "Units sold", "Distributed (net)", "Sell fees", "Realised P&L"]


def test_render_markdown_shows_the_distribution_columns_on_both_tables() -> None:
    market = _hourly_market({0: "100"}, 61)
    rules: list[Rule] = [Dca("BTC-USD", cadence_days=7), _rev()]
    result = _run_account(rules, market)
    hourly = market["BTC"][Granularity.ONE_HOUR]
    metrics = build_account_metrics(result, hourly[0].ts, hourly[-1].ts)
    daily = {"BTC": {Granularity.ONE_DAY: market["BTC"][Granularity.ONE_DAY]}}
    accumulation = accumulation_table(rules, daily, fee_pct=Decimal("0.01"), slippage_pct=_ZERO)
    edge = report.edge_table(rules, market, fee_pct=_ZERO, slippage_pct=_ZERO)

    md = report.render_markdown(
        result,
        edge,
        metrics,
        _benchmark(),
        report.Verdict("TRAIN MORE", ["x"], True, False, False),
        report.analyze_gaps(SimTelemetry(), {}, move_threshold_pct=Decimal("0.05")),
        accumulation=accumulation,
    )

    base = ["Buys", "Qty", "Cost basis", "Last close", "Value", "Unrealized P&L"]
    acc = _table_under(md, "## DCA accumulation (not round trips)")
    assert acc[0] == ["Rule", *base, *_SELL_HEADER]
    rev = accumulation["reverse_dca:BTC"]
    assert acc[1:] == [
        [key, str(r.buys), str(r.qty), str(r.cost_usd), str(r.last_close), str(r.value_usd),
         str(r.unrealized_pnl), str(r.distributions), str(r.units_sold), str(r.distributed_usd),
         str(r.sell_fees), str(r.realised_pnl)]
        for key, r in accumulation.items()
    ]  # fmt: skip
    assert [row[0] for row in acc[1:]] == ["dca:BTC", "reverse_dca:BTC"]
    assert rev.distributions == 1, "fixture: the edge pass distributed"

    sleeve = _table_under(md, "### DCA sleeve (accumulation, marked to market)")
    assert sleeve[0] == ["Asset", *base, *_SELL_HEADER]
    row = metrics["dca_sleeve"]["BTC"]
    assert row.distributions == 1, "fixture: the account sim distributed"
    assert sleeve[1][7:] == [
        str(row.distributions),
        str(row.units_sold),
        str(row.distributed_usd),
        str(row.sell_fees),
        str(row.realised_pnl),
    ]


def test_a_sleeve_with_no_distribution_renders_exactly_as_before() -> None:
    """Nothing sold, nothing new: the seven #821 columns, byte for byte."""
    rows = {
        "dca:BTC": portfolio_sim.DcaSleeve.marked(1, Decimal("1"), Decimal("100"), Decimal("120"))
    }

    lines = report._render_holdings_table("Rule", rows)

    assert lines == [
        "| Rule | Buys | Qty | Cost basis | Last close | Value | Unrealized P&L |",
        "|---|---|---|---|---|---|---|",
        "| dca:BTC | 1 | 1 | 100 | 120 | 120 | 20 |",
    ]


def test_the_accumulation_paragraph_on_distributions_appears_only_when_one_was_made() -> None:
    held = portfolio_sim.DcaSleeve.marked(1, Decimal("1"), Decimal("100"), Decimal("120"))
    sold = portfolio_sim.DcaSleeve.marked(
        0, _ZERO, _ZERO, Decimal("120"), distributions=1, units_sold=Decimal("0.1")
    )

    quiet = report._render_accumulation_section({"dca:BTC": held})
    loud = report._render_accumulation_section({"dca:BTC": held, "reverse_dca:BTC": sold})

    quiet_table = report._render_holdings_table("Rule", {"dca:BTC": held})
    assert quiet == [*quiet[:4], *quiet_table] and len(quiet) == 4 + len(quiet_table)
    loud_table = report._render_holdings_table("Rule", {"dca:BTC": held, "reverse_dca:BTC": sold})
    assert loud[0] == quiet[0] and loud[1] == quiet[1] and loud[3] == "" and loud[5] == ""
    assert loud[6:] == loud_table and loud[4] not in quiet


def test_the_accumulation_intro_stops_claiming_never_sell_once_something_did() -> None:
    """#925: the intro sentence claimed every accumulating row never sells, which is false once
    a `reverse_dca` row (also `accumulates`) has distributed. Byte-identical when nothing was."""
    held = portfolio_sim.DcaSleeve.marked(1, Decimal("1"), Decimal("100"), Decimal("120"))
    sold = portfolio_sim.DcaSleeve.marked(
        0, _ZERO, _ZERO, Decimal("120"), distributions=1, units_sold=Decimal("0.1")
    )

    quiet = report._render_accumulation_section({"dca:BTC": held})
    loud = report._render_accumulation_section({"dca:BTC": held, "reverse_dca:BTC": sold})

    assert quiet[2] == (
        "Accumulating rules buy on a cadence and never sell, so they have no win rate, "
        "expectancy or R-multiples. Each row is its buys over the daily series (decided on "
        "completed days, once per day, filled at the next open), their cost including fees, and "
        f"the holding marked at the last close. Not in `{report.POOLED_KEY}`, not in G2."
    )
    assert loud[2] == (
        "Accumulating rules buy on a cadence; a sleeve-sell rule among them also sells (below), "
        "so they have no win rate, expectancy or R-multiples. Each row is its buys over the "
        "daily series (decided on completed days, once per day, filled at the next open), their "
        f"cost including fees, and the holding marked at the last close. Not in "
        f"`{report.POOLED_KEY}`, not in G2."
    )
    assert "never sell" not in loud[2]


def test_the_sleeve_caption_stops_claiming_never_sold_once_something_did() -> None:
    """#925: `_render_account_section`'s DCA-sleeve caption claimed the sleeve never sold,
    unconditionally. Byte-identical when nothing was distributed."""
    held = {"BTC": portfolio_sim.DcaSleeve.marked(1, Decimal("1"), Decimal("100"), Decimal("120"))}
    sold = {
        "BTC": portfolio_sim.DcaSleeve.marked(
            0, _ZERO, _ZERO, Decimal("120"), distributions=1, units_sold=Decimal("0.1")
        )
    }

    quiet = report._render_account_section({"dca_sleeve": held})
    loud = report._render_account_section({"dca_sleeve": sold})

    caption_idx = quiet.index("### DCA sleeve (accumulation, marked to market)") + 2
    assert quiet[caption_idx] == (
        "Bought on the DCA cadence and never sold: unrealized, marked at each asset's "
        "last close. Cost basis includes entry fees. Included in the ending value above, "
        "not in the trade count or the realized P&L."
    )
    loud_idx = loud.index("### DCA sleeve (accumulation, marked to market)") + 2
    assert loud[loud_idx] == (
        "Bought on the DCA cadence; a sleeve-sell rule also sold from it (the distribution "
        "columns below), so what remains is unrealized, marked at each asset's last close. "
        "Cost basis includes entry fees. Included in the ending value above, not in the trade "
        "count or the realized P&L."
    )
    assert "never sold" not in loud[loud_idx]


def test_simulate_slices_the_accumulation_row_at_the_configured_per_order_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`keel simulate` hands the edge pass the config's rail-2 cap, the one the account sim and
    `guards.check` read, so the two sim passes slice a distribution alike."""
    from keel.commands import simulate as simulate_service
    from keel.commands._products import _default_sim_products
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository
    from tests.commands.test_service_parity import NOW_TS, _seed_sim_candles

    seen: list[dict] = []
    real = report.accumulation_table

    def spy(*args: object, **kwargs: object) -> dict:
        seen.append(kwargs)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report, "accumulation_table", spy)
    repo = Repository(connect(":memory:"))
    migrate(repo._conn)  # noqa: SLF001
    _seed_sim_candles(repo, NOW_TS)
    config = _config(max_per_order_usd=Decimal("777"))

    simulate_service.run_simulation(
        repo,
        config,
        None,
        db_path=":memory:",
        products=_default_sim_products(config),
        years=1,
        monthly_contribution=Decimal("500"),
        now_ts=NOW_TS,
        out_path=tmp_path / "report.md",
        no_trial_record=True,
        skip_within_cap=True,
    )

    assert [call["max_per_order_usd"] for call in seen] == [Decimal("777")]


# ---------------------------------------------------------------------------
# (j) the shared decision: one sale per product per day, arbitration, fail-closed cadence
# ---------------------------------------------------------------------------


def test_a_duplicated_daily_bar_still_sells_once_that_day() -> None:
    """R14 in the edge pass: the cadence bar of day 30 twice means day 30 is decided twice, and
    only the first decision sells. (`min_hold_days=0`: the first decision fills on the
    duplicate, still day 30, 29 days after the first lot.)"""
    daily = [_candle(d, "100") for d in range(61)]
    daily.insert(30, daily[30])
    rows = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), _rev(min_hold_days=0)],
        {"BTC": {Granularity.ONE_DAY: daily}},
        fee_pct=_ZERO,
        slippage_pct=_ZERO,
    )

    assert rows["reverse_dca:BTC"].distributions == 1


class _ExitFirst(ReverseDca):
    """A second sleeve-sell kind, named for the one spec §3.6 puts before `reverse_dca`."""

    def __init__(self) -> None:
        super().__init__("BTC-USD", target_usd=Decimal("20"), min_price_floor=Decimal("1"))
        self.name = "sleeve_exit"


def test_arbitration_follows_the_fixed_order_not_the_rule_order() -> None:
    """Listed second, the `sleeve_exit`-named rule still wins the day (spec §3.6), and the
    `reverse_dca` beside it sells nothing."""
    rev, first = _rev(), _ExitFirst()
    assert portfolio_sim.sleeve_sellers([Dca("BTC-USD"), rev, first]) == [first, rev]

    rows = accumulation_table(
        [Dca("BTC-USD", cadence_days=7), rev, first],
        {"BTC": {Granularity.ONE_DAY: [_candle(d, "100") for d in range(61)]}},
        fee_pct=_ZERO,
        slippage_pct=_ZERO,
    )

    assert (rows["sleeve_exit:BTC"].units_sold, rows["reverse_dca:BTC"].distributions) == (
        Decimal("0.2"),
        0,
    )


class _BrokenDca(Dca):
    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        raise RuntimeError("boom")


def test_a_dca_whose_detect_raises_counts_as_on_cadence() -> None:
    """Fail toward refusing the sale, as `agent._dca_fires_today` does."""
    view = {Granularity.ONE_DAY: [_candle(1, "100")]}
    assert portfolio_sim.dca_on_cadence([Dca("BTC-USD", cadence_days=7)], view) is False
    assert portfolio_sim.dca_on_cadence([_BrokenDca("BTC-USD")], view) is True


def test_the_account_sim_sale_pays_slippage_and_the_fee() -> None:
    slip = Decimal("0.002")
    result = _run_account(
        [Dca("BTC-USD", cadence_days=7), _rev()], _hourly_market({0: "100"}, 61), slippage=slip
    )

    [sale] = result.dca_sells
    assert sale.fill_price == Decimal("100") * (1 - slip)
    assert sale.gross_usd == sale.qty * sale.fill_price
    assert sale.fee_usd == sale.gross_usd * Decimal("0.01")
    assert sale.net_usd == sale.gross_usd - sale.fee_usd


def test_a_sleeve_sold_down_to_nothing_keeps_its_row() -> None:
    """A $1,000 target on a $250 holding sells all 2.5 units (the rule caps at the holding); the
    lot is gone, but the asset's row stays, at qty 0, carrying the distribution.

    `min_hold_days=0`: selling every unit necessarily reaches the day-29 lot, 2 days old at the
    day-31 fill (#924's per-buy FIFO holding) -- zeroing `min_hold_days` isolates the "sold to
    nothing" behaviour this test is about from that separate gate."""
    result = _run_account(
        [Dca("BTC-USD", cadence_days=7), _rev(target_usd=Decimal("1000"), min_hold_days=0)],
        _hourly_market({0: "100"}, 33),
        fee=_ZERO,
    )

    assert "BTC" not in result.dca_positions
    row = portfolio_sim.dca_sleeve(result)["BTC"]
    assert (row.qty, row.value_usd, row.distributions, row.units_sold) == (
        _ZERO,
        _ZERO,
        1,
        Decimal("2.5"),
    )
    assert row.cost_usd == _ZERO


# ---------------------------------------------------------------------------
# (k) the cooldown's own bookkeeping (`last_sale[...] = ...`, R15) -- both sim passes
# ---------------------------------------------------------------------------


class _Cooldown(ReverseDca):
    """A `reverse_dca` whose `params` carries a `cooldown_days` (R15): the constructor exposes
    no such keyword (the sell pipeline reads it straight off `params`, never off the rule), so a
    stub sets it there directly."""

    def __init__(self, cooldown_days: int, **kw: object) -> None:
        super().__init__("BTC-USD", **kw)
        self.params = {**self.params, "cooldown_days": cooldown_days}


def _cooldown_rev(cooldown_days: int = 20) -> _Cooldown:
    """Cadence 10, short enough to fire twice inside a 20-day cooldown; `min_hold_days=0`
    isolates the cooldown gate from R12's own."""
    return _Cooldown(
        cooldown_days,
        target_usd=Decimal("10"),
        min_price_floor=Decimal("1"),
        cadence_days=10,
        min_hold_days=0,
    )


def test_cooldown_refuses_the_repeat_sale_and_allows_it_once_elapsed_in_the_edge_pass() -> None:
    """#857's cooldown (R15), and the missing coverage of `_accumulate_asset`'s own bookkeeping:
    cadence day 0 is also a DCA day (refused SAME_DAY_DCA, not cooldown, and never recorded);
    cadence day 10 (decided/filled day 11) sells and records `last_sale`; cadence day 20
    (decided/filled day 21) is only 10 days after day 11's sale, under the 20-day cooldown, and
    is refused; cadence day 30 (decided/filled day 31) is exactly 20 days after day 11's sale,
    the cooldown elapsed, and sells again. Two distributions, not three -- proving the test would
    fail (three) if `last_sale[seller] = fill_bar.ts` were removed, since `last_rule_proposal_ts`
    would then always read `None` and day 20 would sell too."""
    daily = [_candle(d, "100") for d in range(40)]
    dca = Dca("BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
    rev = _cooldown_rev()

    rows = accumulation_table(
        [dca, rev], {"BTC": {Granularity.ONE_DAY: daily}}, fee_pct=_ZERO, slippage_pct=_ZERO
    )

    assert rows["reverse_dca:BTC"].distributions == 2


def test_cooldown_refuses_the_repeat_sale_and_allows_it_once_elapsed_in_the_account_sim() -> None:
    """The same fixture through the account sim (`_process_reductions`'s own `last_sale`).
    Proves the test would fail (three fills, at days 11, 21 and 31) if
    `last_sale[id(sale.rule)] = fill_bar.ts` were removed there."""
    market = _hourly_market({0: "100"}, 40)
    result = _run_account(
        [Dca("BTC-USD", cadence_days=7, budget_usd=Decimal("50")), _cooldown_rev()],
        market,
        fee=_ZERO,
    )

    assert [s.decision_ts // _DAY for s in result.dca_sells] == [11, 31]
