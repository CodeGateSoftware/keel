"""`sleeve_report.distribution_rows` / `render_distribution` -- what the next cadence day of each
`reverse_dca` rule would do, read-only (#857, plan P10 Task 10.1).

Every figure here is the rule's own arithmetic (`ReverseDca.reduce_signal`) run on the latest
cached daily close re-stamped to the next cadence day, sized by the pipeline's own slicer
(`sleeve.slice_qty`) and priced at the fallback fee (R25). The hand computations below are the
rule's documented formula -- `gross = target / (1 - fee - slippage)`, `qty = gross / close` --
not a second implementation of it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from keel_core.config import AutoTradeConfig, FeesConfig

from keel.commands import sleeve_report
from keel.commands.rules import backtest_slippage
from keel.commands.sleeve_report import DistributionRow, distribution_rows, render_distribution
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import sleeve
from keel.types import Candle, Granularity
from tests.execution.test_executor import _config as executor_config

D = Decimal
DAY = 86_400


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _config(mode: str = "live", **overrides: Any) -> Any:
    return executor_config(
        fees=FeesConfig(taker_pct=D("0.012")),
        auto_trade=AutoTradeConfig(mode=mode),
        **overrides,
    )


def _candle(day: int, close: str, high: str | None = None) -> Candle:
    c = D(close)
    return Candle(
        ts=day * DAY, open=c, high=D(high) if high else c, low=c, close=c, volume=D("1000")
    )


def _seed(
    repo: Repository,
    *,
    floor: str = "60000",
    close: str = "100000",
    dca: bool = True,
    status: str = "paper",
    dca_status: str = "live",
    extra: dict[str, Any] | None = None,
) -> int:
    rule_id = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "100", "min_price_floor": floor, **(extra or {})},
        status=status,
    )
    if dca:
        repo.insert_rule(
            "dca",
            {"product_id": "BTC-USD", "cadence_days": 7, "budget_usd": "50"},
            status=dca_status,
        )
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=0,
        qty=D("0.01"),
        entry_fill=D("50000"),
        entry_fee=D("0"),
    )
    repo.upsert_candles(
        "BTC-USD", Granularity.ONE_DAY, [_candle(d, close) for d in range(195, 201)]
    )
    return rule_id


def test_the_next_cadence_day_and_whether_it_collides_with_the_weekly_buy(repo) -> None:
    rule_id = _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.rule_id == rule_id and row.status == "paper" and row.product_id == "BTC-USD"
    assert row.next_cadence_ts == 210 * DAY
    assert row.dca_collision is True, "210 is a multiple of both 30 and 7 -- Review Focus 1"
    assert row.gates == {"price_floor": True, "drawdown": True, "floor_qty": True}
    assert row.legs == 1 and row.qty is not None


def test_the_size_is_the_rules_own_net_to_gross_arithmetic_at_the_fallback_fee(repo) -> None:
    _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    slippage, _measured = backtest_slippage(repo, "BTC-USD")
    close = D("100000")
    gross_target = D("100") / (D("1") - D("0.012") - slippage)
    assert row.qty == gross_target / close
    # The proposal's own definitions (`sleeve.record_proposal`, `executor.reduce`'s fallback fee).
    assert row.gross_usd == row.qty * close * (D("1") - slippage)
    assert row.fee_usd == row.qty * close * D("0.012")


def test_the_bar_a_cycle_today_judges_is_yesterdays_close(repo) -> None:
    """#921: at now=211*DAY+3_600 (today=211), the newest completed bar is 210 -- exactly what
    `tests/test_agent.py::test_a_distribution_on_a_dca_buy_day_is_recorded_vetoed_not_carried`
    pins the live cycle judging at this same instant. 210 is also a multiple of the 7-day dca's
    cadence, so the collision fires (Review Focus 1)."""
    _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=211 * DAY + 3_600)
    assert row.next_cadence_ts == 210 * DAY
    assert row.dca_collision is True


def test_todays_own_cadence_bar_is_next_and_is_proposed_tomorrow(repo) -> None:
    """#921: at now=210*DAY+3_600 (today=210), yesterday's bar (209) is off the 30-day cadence.
    Bar 210 is on cadence and closes tonight, so tomorrow's cycle judges it and records its
    proposal on day 211. It is the NEXT distribution. Skipping it to 240 would hide a sale that
    happens in a day. The line's two dates say "bar 210, proposed 211"."""
    _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=210 * DAY + 3_600)
    assert row.next_cadence_ts == 210 * DAY
    [line, _collision] = render_distribution([row])
    assert line.startswith("rule 1 (paper) BTC-USD cadence bar 1970-07-30, proposed 1970-07-31: ")


def test_a_cadence_bar_several_days_out_is_named_ahead_of_time(repo) -> None:
    """Contrast with the case above: a cadence-aligned bar in the FAR future (unclosed, just
    like bar 210 was at now=210*DAY) is named plainly. Only a bar landing on `today` itself is
    skipped -- the far-future case is unambiguous, since it is nobody's "today"."""
    _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.next_cadence_ts == 210 * DAY


def test_a_cadence_day_off_the_weekly_buy_does_not_collide(repo) -> None:
    _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=171 * DAY)
    assert row.next_cadence_ts == 180 * DAY
    assert 180 % 7 != 0
    assert row.dca_collision is False


def test_no_dca_rule_is_no_collision(repo) -> None:
    _seed(repo, dca=False)
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.dca_collision is False


@pytest.mark.parametrize("dca_status", ["candidate", "disabled", "paper"])
def test_only_a_dca_rule_the_live_cycle_would_run_collides(repo, dca_status: str) -> None:
    """The pipeline's own definition (`agent._dca_fires_today` over the cycle's loaded rules): a
    live-mode cycle runs `live` dca rules only, so a candidate, disabled or paper dca buys nothing
    that day and the pipeline would not veto the distribution."""
    _seed(repo, dca_status=dca_status)
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.dca_collision is False


def test_a_paper_profile_collides_with_its_paper_dca_and_ignores_live_sleeve_rules(repo) -> None:
    """A paper cycle loads `paper` rules only, entry and sleeve alike (`agent._sleeve_rules`)."""
    _seed(repo, status="paper", dca_status="paper")
    repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "100", "min_price_floor": "60000"},
        status="live",
    )
    rows = distribution_rows(repo, _config(mode="paper"), now_ts=201 * DAY)
    assert [(r.status, r.dca_collision) for r in rows] == [("paper", True)]


def test_candidate_and_disabled_sellers_are_not_previewed(repo) -> None:
    _seed(repo, status="candidate")
    repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "100", "min_price_floor": "60000"},
        status="disabled",
    )
    assert distribution_rows(repo, _config(), now_ts=201 * DAY) == []


def test_a_closed_gate_is_named_and_no_size_is_invented(repo) -> None:
    _seed(repo, floor="200000")
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.gates == {"price_floor": False}, "the rule stops at its first closed gate"
    assert (row.qty, row.gross_usd, row.fee_usd, row.legs) == (None, None, None, 0)


def test_a_drawdown_gate_is_reached_only_past_an_open_floor(repo) -> None:
    _seed(repo)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(190, "150000", high="200000")])
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.gates == {"price_floor": True, "drawdown": False}
    assert row.qty is None


def test_a_holding_at_its_floor_qty_sells_nothing(repo) -> None:
    _seed(repo, extra={"floor_qty": "0.01"})
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.gates == {"price_floor": True, "drawdown": True, "floor_qty": False}
    assert row.qty is None


def test_no_cached_daily_close_judges_no_gate(repo) -> None:
    repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "100", "min_price_floor": "60000"},
        status="paper",
    )
    [row] = distribution_rows(repo, _config(), now_ts=201 * DAY)
    assert row.gates == {} and row.qty is None and row.legs == 0


def test_a_large_target_slices_to_rail_2(repo) -> None:
    """R11: the pipeline's slicer, not the rule, splits a sale over the per-order cap."""
    from keel_core.config import Caps

    _seed(repo, extra={"target_usd": "450"})
    config = _config(
        caps=Caps(
            max_per_order_usd=D("200"),
            max_per_day_usd=D("300000"),
            max_exposure_usd=D("1000000"),
            max_per_asset_pct=D("1"),
        )
    )
    [row] = distribution_rows(repo, config, now_ts=201 * DAY)
    assert row.qty is not None
    assert (
        row.legs
        == sleeve.slice_qty(row.qty, D("100000"), max_per_order_usd=D("200"), base_increment=None)[
            1
        ]
    )
    assert row.legs == 3


# -- render_distribution -----------------------------------------------------------------------


def _row(**over: Any) -> DistributionRow:
    base: dict[str, Any] = dict(
        rule_id=20,
        status="paper",
        product_id="BTC-USD",
        next_cadence_ts=1_790_640_000,  # 2026-09-29T00:00:00Z
        gates={"price_floor": True, "drawdown": True, "floor_qty": True},
        qty=D("0.00102"),
        gross_usd=D("101.99"),
        fee_usd=D("1.2240"),
        legs=1,
        dca_collision=False,
    )
    base.update(over)
    return DistributionRow(**base)


def test_an_open_row_renders_its_size_fee_source_and_gates() -> None:
    assert render_distribution([_row()]) == [
        "rule 20 (paper) BTC-USD cadence bar 2026-09-29, proposed 2026-09-30: "
        "sell 0.00102 over 1 leg"
        "  gross $101.99  fee $1.22 (fallback:config.fees.taker_pct)"
        "  gates price_floor=open drawdown=open floor_qty=open",
    ]


def test_a_colliding_row_says_the_pipeline_will_veto_it() -> None:
    lines = render_distribution([_row(dca_collision=True, legs=3)])
    assert lines == [
        "rule 20 (paper) BTC-USD cadence bar 2026-09-29, proposed 2026-09-30: "
        "sell 0.00102 over 3 legs"
        "  gross $101.99  fee $1.22 (fallback:config.fees.taker_pct)"
        "  gates price_floor=open drawdown=open floor_qty=open",
        "  a dca buy falls on the same day: the pipeline records it vetoed (same_day_dca), "
        "and it is not carried forward",
    ]


def test_a_closed_row_names_the_gate_and_prints_no_size() -> None:
    row = _row(gates={"price_floor": False}, qty=None, gross_usd=None, fee_usd=None, legs=0)
    assert render_distribution([row]) == [
        "rule 20 (paper) BTC-USD cadence bar 2026-09-29, proposed 2026-09-30: "
        "no sale  gates price_floor=closed",
    ]


def test_a_row_with_no_close_says_so() -> None:
    row = _row(gates={}, qty=None, gross_usd=None, fee_usd=None, legs=0)
    assert render_distribution([row]) == [
        "rule 20 (paper) BTC-USD cadence bar 2026-09-29, proposed 2026-09-30: "
        "no sale  no cached daily close",
    ]


def test_no_rows_is_one_line() -> None:
    assert render_distribution([]) == [sleeve_report.NO_DISTRIBUTION_RULES]


def test_a_closed_row_on_a_dca_day_says_nothing_of_a_veto() -> None:
    """A closed gate means the rule proposes nothing, so there is no row for the pipeline to
    veto -- the same-day-DCA line would describe a proposal that never exists."""
    row = _row(
        gates={"price_floor": False},
        qty=None,
        gross_usd=None,
        fee_usd=None,
        legs=0,
        dca_collision=True,
    )
    assert len(render_distribution([row])) == 1
