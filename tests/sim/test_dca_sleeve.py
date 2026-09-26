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

from keel.commands.simulate import build_account_metrics
from keel.config import Caps, Config, DcaConfig, MarketDataConfig, SubscriptionConfig
from keel.sim import portfolio_sim, report
from keel.sim.benchmark import BenchmarkResult
from keel.sim.portfolio_sim import SimTelemetry
from keel.strategy.rules.base import Rule, Setup
from keel.strategy.rules.dca import Dca
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


def _config() -> Config:
    return Config(
        allowlist=["BTC"],
        target_weights={},
        risk_pct=Decimal("0.02"),
        caps=Caps(
            max_per_order_usd=Decimal("1000000"),
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
