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
from keel.commands.sleeve_report import (
    BandRow,
    BandsReport,
    DistributionRow,
    LotRow,
    bands_view,
    distribution_rows,
    lots_view,
    render_bands,
    render_distribution,
    render_lots,
)
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
    [line, stale, _collision] = render_distribution([row])
    assert line.startswith(
        "rule 1 (paper) BTC-USD cadence bar 1970-07-30, proposed 1970-07-31, mark bar 1970-07-20: "
    )
    # The fixture's newest bar is day 200, ten days before today: flagged, not silently used.
    assert row.mark_bar is not None and stale == sleeve_report.stale_mark_line(
        "BTC-USD", row.mark_bar
    )


def test_a_cadence_bar_several_days_out_is_named_ahead_of_time(repo) -> None:
    """Far from any cadence bar (today=185, yesterday's bar 184 off cadence), the next bar is
    the one a month's cadence lands on, 25 days out, and it is named plainly."""
    _seed(repo)
    [row] = distribution_rows(repo, _config(), now_ts=185 * DAY + 3_600)
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


# -- proposal replay (P12 Task 12.3, spec §3.7, R29) -------------------------------------------
#
# A FIDELITY check of the replay, not a verdict: synthetic candles and hand-computed figures. The
# research freeze (2026-09-27) holds -- no sweep, nothing appended to the trials ledger.

_FEE = D("0.012")
_SLIP = D("0.0005")


def _rising(days: int) -> list[Candle]:
    """Close 100 + d on day d: every 30-day cadence bar clears the floor and the drawdown gate."""
    return [_candle(d, str(100 + d)) for d in range(days)]


def _reverse(**params: Any) -> Any:
    from keel.strategy.rules.reverse_dca import ReverseDca

    return ReverseDca("BTC-USD", **{"target_usd": D("10"), "min_price_floor": D("1"), **params})


def test_the_replay_lists_every_reduction_and_is_deterministic() -> None:
    from keel.commands.sleeve_report import proposal_replay

    first = proposal_replay(_reverse(), _rising(91), fee_pct=_FEE, slippage_pct=_SLIP)
    assert [r.day for r in first.rows] == [30, 60, 90]
    assert first == proposal_replay(_reverse(), _rising(91), fee_pct=_FEE, slippage_pct=_SLIP)


def test_the_first_cadence_bar_is_vetoed_by_min_hold_as_the_pipeline_would() -> None:
    """Day 0 is a cadence bar too, but the synthetic lot was bought on day 0 and the proposal
    for bar 0 is made on day 1: one day held < 30 (`sleeve_refusal`, R12)."""
    from keel.commands.sleeve_report import proposal_replay
    from keel.execution.sleeve import MIN_HOLD

    replay = proposal_replay(_reverse(), _rising(91), fee_pct=_FEE, slippage_pct=_SLIP)
    assert [(v.day, v.reason) for v in replay.vetoed] == [(0, MIN_HOLD)]


def test_each_sale_is_hand_computed_against_the_fifo_lot() -> None:
    """Bar 30, close 130: gross from net is 10 / (1 - 0.012 - 0.0005), qty = gross / 130. The
    realised P&L is the leg's notional less slippage, less the fee on the notional, less the
    basis of the units consumed from the one lot bought at 100."""
    from keel.commands.sleeve_report import proposal_replay

    replay = proposal_replay(_reverse(), _rising(91), fee_pct=_FEE, slippage_pct=_SLIP)
    row = replay.rows[0]
    qty = D("10") / (D("1") - _FEE - _SLIP) / D("130")
    notional = qty * D("130")
    assert (row.qty, row.price) == (qty, D("130"))
    assert row.gross_usd == notional * (D("1") - _SLIP)
    assert row.fee_usd == notional * _FEE
    assert row.cost_basis == qty * D("100")
    assert row.realised_pnl == row.gross_usd - row.fee_usd - row.cost_basis
    assert row.legs is None  # no cap supplied: legs are not claimed


def test_the_terminal_value_is_with_and_without_the_rule() -> None:
    from keel.commands.sleeve_report import proposal_replay

    replay = proposal_replay(_reverse(), _rising(91), fee_pct=_FEE, slippage_pct=_SLIP)
    sold = sum((r.qty for r in replay.rows), D("0"))
    cash = sum((r.gross_usd - r.fee_usd for r in replay.rows), D("0"))
    assert replay.final_close == D("190")
    assert replay.units_left == D("1") - sold
    assert replay.cash_usd == cash
    assert replay.terminal_with == (D("1") - sold) * D("190") + cash
    assert replay.terminal_without == D("190")


def test_a_cap_slices_a_sale_into_legs_and_the_proposal_sells_the_first() -> None:
    """The live pipeline's rail-2 slicing (`sleeve.slice_qty`): a $10 target under a $4 cap
    needs 3 legs, and a proposal row carries its FIRST leg -- so does the replay."""
    from keel.commands.sleeve_report import proposal_replay
    from keel.execution import sleeve

    replay = proposal_replay(
        _reverse(), _rising(91), fee_pct=_FEE, slippage_pct=_SLIP, max_per_order_usd=D("4")
    )
    row = replay.rows[0]
    whole = D("10") / (D("1") - _FEE - _SLIP) / D("130")
    leg, legs = sleeve.slice_qty(whole, D("130"), max_per_order_usd=D("4"), base_increment=None)
    assert legs == 3
    assert (row.qty, row.legs) == (leg, 3)


def test_a_same_day_dca_vetoes_the_sale_and_it_is_not_carried() -> None:
    """Review Focus 1: with a 30-day dca on the product every distribution bar is a buy bar,
    so every one is vetoed `same_day_dca`, and none is carried to the next day."""
    from keel.commands.sleeve_report import proposal_replay
    from keel.execution.sleeve import SAME_DAY_DCA
    from keel.strategy.rules.dca import Dca

    dca = Dca("BTC-USD", cadence_days=30)
    replay = proposal_replay(
        _reverse(min_hold_days=0), _rising(91), fee_pct=_FEE, slippage_pct=_SLIP, dca_rules=[dca]
    )
    assert replay.rows == ()
    assert [(v.day, v.reason) for v in replay.vetoed] == [
        (0, SAME_DAY_DCA),
        (30, SAME_DAY_DCA),
        (60, SAME_DAY_DCA),
        (90, SAME_DAY_DCA),
    ]


def test_the_replay_holding_shrinks_fifo_and_floor_qty_stops_it() -> None:
    """A target larger than what floor_qty leaves: the first sale takes the holding down to the
    floor, and every later bar proposes nothing (the rule's own floor_qty gate)."""
    from keel.commands.sleeve_report import proposal_replay

    rule = _reverse(target_usd=D("1000"), floor_qty=D("0.5"))
    replay = proposal_replay(rule, _rising(91), fee_pct=_FEE, slippage_pct=_SLIP)
    assert [r.day for r in replay.rows] == [30]
    assert replay.rows[0].qty == D("0.5")
    assert replay.units_left == D("0.5")


def test_no_daily_candles_replays_nothing() -> None:
    from keel.commands.sleeve_report import proposal_replay

    replay = proposal_replay(_reverse(), [], fee_pct=_FEE, slippage_pct=_SLIP)
    assert (replay.rows, replay.vetoed, replay.n_bars) == ((), (), 0)


def _file_repo(db) -> Repository:
    conn = connect(str(db))
    migrate(conn)
    return Repository(conn)


def _backtest(tmp_path, valid_config_path, rid, *extra):
    from click.testing import CliRunner

    from keel.cli import cli

    return CliRunner().invoke(
        cli,
        [
            "--db",
            str(tmp_path / "t.db"),
            "--config",
            str(valid_config_path),
            "rules",
            "backtest",
            str(rid),
            *extra,
        ],
    )


def _fee_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("fee line:")]


def test_the_sensitivity_row_exists_only_when_the_operator_supplies_a_rate(
    tmp_path, valid_config_path
) -> None:
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )

    plain = _backtest(tmp_path, valid_config_path, rid)
    both = _backtest(tmp_path, valid_config_path, rid, "--fee-sensitivity-pct", "0.009")

    assert plain.exit_code == 0, plain.output
    assert both.exit_code == 0, both.output
    assert _fee_lines(plain.output) == ["fee line: 1.2000% (fallback:config.fees.taker_pct)"]
    assert _fee_lines(both.output) == [
        "fee line: 1.2000% (fallback:config.fees.taker_pct)",
        "fee line: 0.9000% (operator-supplied sensitivity)",
    ]


def test_the_replay_says_it_is_a_description_not_evidence(tmp_path, valid_config_path) -> None:
    """The research freeze: the output states plainly what the replay is and is not."""
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    out = _backtest(tmp_path, valid_config_path, rid).output
    assert sleeve_report.REPLAY_DISCLAIMER in out.splitlines()
    sales = [line for line in out.splitlines() if " bar: sell " in line]
    vetoes = [line for line in out.splitlines() if " bar: vetoed " in line]
    assert (len(sales), len(vetoes)) == (3, 1)
    assert sum(1 for line in out.splitlines() if line.startswith("  terminal ")) == 1


def test_the_sensitivity_flag_is_refused_on_a_rule_with_no_replay(
    tmp_path, valid_config_path
) -> None:
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="candidate")
    result = _backtest(tmp_path, valid_config_path, rid, "--fee-sensitivity-pct", "0.01")
    assert result.exit_code == 2
    assert "No such option" not in result.output
    assert "only to a sleeve-sell rule's proposal replay" in result.output


@pytest.mark.parametrize("rate", ["-0.001", "1", "abc"])
def test_a_rate_outside_zero_to_one_is_a_usage_error(tmp_path, valid_config_path, rate) -> None:
    repo = _file_repo(tmp_path / "t.db")
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    result = _backtest(tmp_path, valid_config_path, rid, "--fee-sensitivity-pct", rate)
    assert result.exit_code == 2
    assert "No such option" not in result.output


def test_no_cached_daily_bars_is_a_named_refusal(tmp_path, valid_config_path) -> None:
    repo = _file_repo(tmp_path / "t.db")
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    result = _backtest(tmp_path, valid_config_path, rid)
    assert result.exit_code == 1
    assert "no cached ONE_DAY candles for BTC-USD" in result.output
    assert "granularity" not in result.output


def test_no_live_fee_rate_is_hardcoded_anywhere_in_keel() -> None:
    """Spec §2.2 / Q5, plan R29: the tier can change, and the preview is the fact. The 0.9% row
    exists only when the operator types --fee-sensitivity-pct."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "keel"
    files = list(root.rglob("*.py"))
    assert len(files) > 100  # the scan reaches the package, not an empty glob
    hits = [str(p) for p in files if re.search(r"\b0\.009\b", p.read_text())]
    assert hits == []


def test_a_cooldown_param_is_enforced_from_the_last_replayed_sale() -> None:
    """R15 as the pipeline applies it (`sleeve_refusal` reads `cooldown_days` off the rule's
    params): with a 45-day cooldown a 30-day cadence sells on bar 30, is refused on bar 60 (30
    days after the bar-30 proposal), and sells again on bar 90 (60 days after it). A vetoed bar
    does not re-arm the cooldown (#915)."""
    from keel.commands.sleeve_report import proposal_replay
    from keel.execution.sleeve import COOLDOWN, MIN_HOLD

    rule = _reverse()
    rule.params["cooldown_days"] = 45
    replay = proposal_replay(rule, _rising(121), fee_pct=_FEE, slippage_pct=_SLIP)
    assert [r.day for r in replay.rows] == [30, 90]
    assert [(v.day, v.reason) for v in replay.vetoed] == [
        (0, MIN_HOLD),
        (60, COOLDOWN),
        (120, COOLDOWN),
    ]


def test_a_raising_dca_detect_counts_as_firing_not_a_crash() -> None:
    """Mirrors `agent._dca_fires_today`'s own rule: "a DCA whose detect RAISES counts as
    firing -- the refusal is the direction that costs nothing." A stub dca rule whose `detect`
    always raises must veto every bar it would otherwise judge, exactly as `Dca("BTC-USD",
    cadence_days=30)` does in `test_a_same_day_dca_vetoes_the_sale_and_it_is_not_carried`."""
    from keel.commands.sleeve_report import proposal_replay
    from keel.execution.sleeve import SAME_DAY_DCA

    class _RaisingDca:
        def detect(self, candles_by_tf: Any) -> Any:
            raise RuntimeError("boom")

    replay = proposal_replay(
        _reverse(min_hold_days=0),
        _rising(91),
        fee_pct=_FEE,
        slippage_pct=_SLIP,
        dca_rules=[_RaisingDca()],
    )
    assert replay.rows == ()
    assert [(v.day, v.reason) for v in replay.vetoed] == [
        (0, SAME_DAY_DCA),
        (30, SAME_DAY_DCA),
        (60, SAME_DAY_DCA),
        (90, SAME_DAY_DCA),
    ]


# -- issue #930: `run_proposal_replay`'s own logic (rules.py ~476) has no test ------------------


@pytest.fixture
def live_config_path(write_config: Any) -> Any:
    """The same valid config, `auto_trade.mode: live` -- for the half of R40's reading
    (`run_proposal_replay`'s `dca_status`) `valid_config_path` (paper) cannot exercise."""
    from tests.conftest import VALID_CONFIG_YAML

    text = VALID_CONFIG_YAML.replace("mode: paper", "mode: live")
    assert "mode: live" in text and "mode: paper" not in text
    return write_config(text)


def test_a_paper_dca_on_the_same_product_vetoes_every_distribution_bar(
    tmp_path, valid_config_path
) -> None:
    """R40's reading on a paper profile: the `paper` dca rules on the same product are replayed
    through their own `detect`, so every cadence-30 distribution bar collides with the
    cadence-30 dca and is vetoed `same_day_dca` (Review Focus 1)."""
    from keel.execution.sleeve import SAME_DAY_DCA

    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    dca_id = repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 30}, status="paper")
    result = _backtest(tmp_path, valid_config_path, rid)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    [head] = [line for line in lines if line.startswith("  same-day dca:")]
    assert head == (
        f"  same-day dca: 1 dca rule(s) at status paper on BTC-USD replayed (ids {dca_id})"
    )
    vetoed = [line for line in lines if f"bar: vetoed ({SAME_DAY_DCA})" in line]
    sold = [line for line in lines if " bar: sell " in line]
    # Bars 0, 30, 60, 90: day 0 is a cadence bar for BOTH rules too (0 % 30 == 0), so it
    # collides same as the later ones (`test_a_same_day_dca_vetoes_the_sale_and_it_is_not_carried`
    # pins the same four bars for the non-CLI replay).
    assert (len(vetoed), len(sold)) == (4, 0)


def test_a_live_dca_or_a_dca_on_another_product_is_not_replayed_on_a_paper_profile(
    tmp_path, valid_config_path
) -> None:
    """A paper profile's cycle runs `paper` dca rules (R40); a `live` dca on the same product,
    and a `paper` dca on a DIFFERENT product, are neither one the cycle would run beside this
    sale, so neither is replayed and neither vetoes anything."""
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 30}, status="live")
    repo.insert_rule("dca", {"product_id": "ETH-USD", "cadence_days": 30}, status="paper")
    result = _backtest(tmp_path, valid_config_path, rid)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    [head] = [line for line in lines if line.startswith("  same-day dca:")]
    assert head == "  same-day dca: no dca rule at status paper on BTC-USD"
    vetoed = [line for line in lines if "bar: vetoed (same_day_dca)" in line]
    sold = [line for line in lines if " bar: sell " in line]
    assert (len(vetoed), len(sold)) == (0, 3)


def test_a_live_dca_on_the_same_product_vetoes_under_a_live_profile(
    tmp_path, live_config_path
) -> None:
    """The other half of R40's reading: on a `live`-mode profile the cycle runs `live` dca
    rules, so a `live` (not `paper`) dca on the same product is the one replayed and vetoing."""
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    dca_id = repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 30}, status="live")
    result = _backtest(tmp_path, live_config_path, rid)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    [head] = [line for line in lines if line.startswith("  same-day dca:")]
    assert head == (
        f"  same-day dca: 1 dca rule(s) at status live on BTC-USD replayed (ids {dca_id})"
    )
    vetoed = [line for line in lines if "bar: vetoed (same_day_dca)" in line]
    sold = [line for line in lines if " bar: sell " in line]
    assert (len(vetoed), len(sold)) == (4, 0)


def test_the_no_config_replay_uses_the_library_default_fee_and_leaves_legs_unsliced(repo) -> None:
    """The no-config branch (`config is None`): the headline fee is `backtest.TAKER_FEE_PCT`
    labelled as the library default, `dca_status` defaults to `live`, and with no
    `max_per_order_usd` every sale row carries `legs unsliced` (no cap to slice against)."""
    from keel.commands.rules import run_proposal_replay

    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    out: list[str] = []
    err: list[str] = []
    run_proposal_replay(repo, None, rid, echo=out.append, echo_err=err.append)
    assert err == []
    assert "fee line: 1.2000% (library default: backtest.TAKER_FEE_PCT)" in out
    sales = [line for line in out if " bar: sell " in line]
    assert len(sales) == 3
    assert all(line.endswith("legs unsliced") for line in sales)


def test_an_hour_granularity_is_refused_and_a_day_is_accepted(tmp_path, valid_config_path) -> None:
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    hour = _backtest(tmp_path, valid_config_path, rid, "--granularity", "ONE_HOUR")
    assert hour.exit_code == 1
    assert "does not apply" in hour.output

    day = _backtest(tmp_path, valid_config_path, rid, "--granularity", "ONE_DAY")
    assert day.exit_code == 0, day.output


# -- review finding 4a: a fee rate that leaves no room for slippage -----------------------------


def test_a_fee_rate_that_leaves_no_room_for_slippage_is_refused_not_a_traceback(
    tmp_path, valid_config_path
) -> None:
    """`--fee-sensitivity-pct 0.9999` passes `_parse_fee_rate` (a fraction in [0, 1)), but
    `SellCosts` then raises on `fee + slippage >= 1`. The replay must refuse cleanly (exit 1, no
    traceback) rather than let that `ValueError` reach the terminal uncaught."""
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="candidate",
    )
    result = _backtest(tmp_path, valid_config_path, rid, "--fee-sensitivity-pct", "0.9999")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "slippage" in result.output


def test_an_unknown_id_with_the_sensitivity_flag_is_refused_as_unknown(
    tmp_path, valid_config_path
) -> None:
    """Round-3 review: the id is refused before the flag is judged, so the operator reads the
    real problem -- the rule does not exist -- not a flag complaint about a missing rule."""
    _file_repo(tmp_path / "t.db")
    result = _backtest(tmp_path, valid_config_path, 999, "--fee-sensitivity-pct", "0.01")
    assert result.exit_code == 1
    assert "Error: no rule with id 999" in result.output.splitlines()


def test_a_rule_that_never_fires_prints_one_nothing_line_per_fee_line(
    tmp_path, valid_config_path
) -> None:
    """#934: a floor above every close -- each fee line says the rule proposed nothing, once,
    and no sale or veto line appears."""
    repo = _file_repo(tmp_path / "t.db")
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _rising(91))
    rid = repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "99999"},
        status="candidate",
    )
    lines = _backtest(tmp_path, valid_config_path, rid, "--fee-sensitivity-pct", "0.01").output
    lines = lines.splitlines()
    fee_at = [i for i, line in enumerate(lines) if line.startswith("fee line: ")]
    assert len(fee_at) == 2
    assert [lines[i + 1] for i in fee_at] == ["  the rule proposed nothing over these bars"] * 2
    assert sum(1 for line in lines if "the rule proposed nothing" in line) == 2
    assert not any(" bar: " in line for line in lines)


# -- keel dca trim --preview --view lots (P13 Task 13.1, spec §8.1, Review Focus 3) -----------


def test_the_mixed_paxg_ledger_lists_turtle_tranche_3_first_with_its_realisable_pnl(repo) -> None:
    """Review Focus 3: PAXG's oldest open row is turtle tranche 3, so a FIFO sale consumes it
    first -- the lots view lists it first and names its rule."""
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="turtle_breakout",
        opened_at=1,
        qty=D("0.0132"),
        entry_fill=D("4673.23"),
        entry_fee=D("0.73"),
    )
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="dca",
        opened_at=2,
        qty=D("0.01"),
        entry_fill=D("4400"),
        entry_fee=D("0.40"),
    )
    repo.upsert_candles("PAXG-USD", Granularity.ONE_DAY, [_candle(10, "4300")])

    rows = lots_view(repo, _config())

    assert [(r.position_id, r.rule_name) for r in rows] == [(1, "turtle_breakout"), (2, "dca")]
    first = rows[0]
    assert first.cost == D("0.0132") * D("4673.23") + D("0.73")
    assert first.mark == D("4300")
    assert first.unrealised == D("0.0132") * D("4300") - (D("0.0132") * D("4673.23") + D("0.73"))
    costs = sleeve.sell_costs(repo, _config(), "PAXG-USD")
    assert (first.fee_pct, first.slippage_pct) == (costs.fee_pct, costs.slippage_pct)
    assert first.realised_if_sold == (
        D("0.0132") * D("4300") * (1 - costs.slippage_pct)
        - D("0.0132") * D("4300") * costs.fee_pct
        - first.cost
    )


def test_a_scaled_out_lot_carries_only_its_share_of_the_entry_fee(repo) -> None:
    """`Lot.entry_fee_share` (spec §3.3, Q4): a tranche half sold keeps half its entry fee."""
    pid = repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=1,
        qty=D("0.02"),
        entry_fill=D("50000"),
        entry_fee=D("2"),
    )
    repo._conn.execute(
        "UPDATE positions SET qty = ?, realized_qty = ? WHERE id = ?", ("0.01", "0.01", pid)
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(10, "60000")])
    [row] = lots_view(repo, _config())
    assert (row.qty, row.entry_fee_share, row.cost) == (D("0.01"), D("1"), D("501"))


def test_no_mark_means_no_pnl_rather_than_a_total_loss(repo) -> None:
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=1,
        qty=D("0.001"),
        entry_fill=D("100000"),
        entry_fee=D("0"),
    )
    [row] = lots_view(repo, _config())
    assert row.mark is None and row.unrealised is None and row.realised_if_sold is None
    assert row.cost == D("100")


def test_the_mark_is_the_latest_cached_daily_close(repo) -> None:
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=1,
        qty=D("0.001"),
        entry_fill=D("100000"),
        entry_fee=D("0"),
    )
    repo.upsert_candles(
        "BTC-USD", Granularity.ONE_DAY, [_candle(10, "90000"), _candle(11, "95000")]
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, [_candle(12, "1")])
    [row] = lots_view(repo, _config())
    assert row.mark == D("95000")


def test_products_group_in_order_and_a_product_filter_narrows(repo) -> None:
    for product, opened_at in (("PAXG-USD", 1), ("BTC-USD", 2), ("PAXG-USD", 3)):
        repo.open_position(
            product_id=product,
            rule_name="dca",
            opened_at=opened_at,
            qty=D("0.01"),
            entry_fill=D("100"),
            entry_fee=D("0"),
        )
    rows = lots_view(repo, _config())
    assert [(r.product_id, r.position_id) for r in rows] == [
        ("BTC-USD", 2),
        ("PAXG-USD", 1),
        ("PAXG-USD", 3),
    ]
    assert [r.position_id for r in lots_view(repo, _config(), product_id="PAXG-USD")] == [1, 3]


def _lot_row(**overrides: Any) -> LotRow:
    base: dict[str, Any] = dict(
        product_id="PAXG-USD",
        position_id=3,
        rule_name="turtle_breakout",
        opened_at=1_700_000_000,
        qty=D("0.0132"),
        entry_fill=D("4673.23"),
        entry_fee_share=D("0.73"),
        cost=D("62.418636"),
        fee_pct=D("0.012"),
        slippage_pct=D("0.01"),
        mark=D("4300"),
        unrealised=D("-5.658636"),
        realised_if_sold=D("-6.907236"),
        mark_bar=sleeve_report.MarkBar(19_675 * DAY, 19_675 * DAY, 0),  # 2023-11-14, fresh
    )
    base.update(overrides)
    return LotRow(**base)


def test_the_rendered_report_says_what_it_is_not() -> None:
    lines = render_lots([])
    assert lines.count(sleeve_report.NOT_TAX_ADVICE) == 1
    assert lines.count(sleeve_report.LOTS_FIFO_NOTE) == 1
    assert sleeve_report.NO_OPEN_LOTS in lines


def test_the_not_tax_advice_line_says_so() -> None:
    assert "not tax advice" in sleeve_report.NOT_TAX_ADVICE
    assert "top to bottom" in sleeve_report.LOTS_FIFO_NOTE


def test_a_rendered_lot_names_its_tranche_its_rule_and_the_fallback_fee() -> None:
    lines = render_lots([_lot_row(), _lot_row(position_id=4, rule_name="dca")])
    head, *rest = lines
    source = sleeve.FALLBACK_FEE_SOURCE
    assert (
        head == f"lots -- fees at the fallback rate ({source}); no venue asked; mark bar 2023-11-14"
    )
    lot_lines = [line for line in rest if line.startswith("  #")]
    assert [line.split()[0:2] for line in lot_lines] == [["#3", "turtle_breakout"], ["#4", "dca"]]
    assert lines.count("PAXG-USD") == 1, "one product heading over its lots"
    assert sleeve_report.NO_OPEN_LOTS not in lines


def test_a_rendered_lot_without_a_mark_prints_no_pnl() -> None:
    [line] = [
        text
        for text in render_lots([_lot_row(mark=None, unrealised=None, realised_if_sold=None)])
        if text.startswith("  #")
    ]
    assert "mark none" in line and "$0.00" not in line


# -- keel dca trim --preview --view bands (P13 Task 13.2, spec §5, read-only) -----------------


def _hold(repo: Repository, product: str, *, qty: str, mark: str | None) -> None:
    """One `dca` tranche of `product`, and one daily candle at `mark` when there is one."""
    repo.open_position(
        product_id=product,
        rule_name="dca",
        opened_at=1,
        qty=D(qty),
        entry_fill=D("1"),
        entry_fee=D("0"),
    )
    if mark is not None:
        repo.upsert_candles(product, Granularity.ONE_DAY, [_candle(10, mark)])


_HALVES = {"BTC": D("0.5"), "ETH": D("0.5")}


def test_an_overweight_asset_shows_its_candidate_sell_and_both_legs_fees(repo) -> None:
    """band = max(0.15 x w, 0.015); BTC target .5 -> band .075; held at .6 -> over."""
    _hold(repo, "BTC-USD", qty="0.006", mark="100000")  # $600
    _hold(repo, "ETH-USD", qty="0.1", mark="4000")  # $400
    report = bands_view(repo, _config(target_weights=_HALVES))
    btc = next(r for r in report.rows if r.asset == "BTC")
    assert (btc.target_weight, btc.weight, btc.band, btc.status) == (
        D("0.5"),
        D("0.6"),
        D("0.075"),
        "over",
    )
    assert btc.value_usd == D("600")
    assert btc.candidate_sell_usd == D("100")
    fee = _config().fees.taker_pct
    btc_slip, _ = backtest_slippage(repo, "BTC-USD")
    eth_slip, _ = backtest_slippage(repo, "ETH-USD")
    # The sell leg at BTC's slippage, the redeploy leg into the one underweight asset at ETH's.
    assert btc.fee_drag_usd == D("100") * (fee + btc_slip) + D("100") * (fee + eth_slip)
    assert btc.fee_drag_usd >= 2 * D("100") * fee


def test_an_underweight_asset_has_no_candidate_sell(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.006", mark="100000")
    _hold(repo, "ETH-USD", qty="0.1", mark="4000")
    report = bands_view(repo, _config(target_weights=_HALVES))
    assert [(r.asset, r.status, r.candidate_sell_usd, r.fee_drag_usd) for r in report.rows] == [
        ("BTC", "over", D("100"), report.rows[0].fee_drag_usd),
        ("ETH", "under", None, None),
    ]
    assert report.rows[0].fee_drag_usd is not None


def test_a_weight_on_the_band_edge_is_within_not_over(repo) -> None:
    """The spec's trigger is strict: `weight > target + band` (§5). .575 and .425 sit on the two
    edges of a .5 target's .075 band."""
    _hold(repo, "BTC-USD", qty="0.00575", mark="100000")  # $575
    _hold(repo, "ETH-USD", qty="0.10625", mark="4000")  # $425
    report = bands_view(repo, _config(target_weights=_HALVES))
    assert [(r.weight, r.status, r.candidate_sell_usd) for r in report.rows] == [
        (D("0.575"), "within", None),
        (D("0.425"), "within", None),
    ]


def test_a_small_target_gets_the_absolute_band_floor(repo) -> None:
    """max(0.15 x .05, 0.015) = 0.015: the floor, not the relative band."""
    _hold(repo, "BTC-USD", qty="0.0095", mark="100000")
    _hold(repo, "ETH-USD", qty="0.0125", mark="4000")
    report = bands_view(repo, _config(target_weights={"BTC": D("0.95"), "ETH": D("0.05")}))
    eth = next(r for r in report.rows if r.asset == "ETH")
    assert eth.band == D("0.015")
    btc = next(r for r in report.rows if r.asset == "BTC")
    assert btc.band == D("0.95") * D("0.15")


def test_the_targets_are_normalised_over_the_positive_weights(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.005", mark="100000")
    _hold(repo, "ETH-USD", qty="0.125", mark="4000")
    report = bands_view(
        repo, _config(target_weights={"BTC": D("2"), "ETH": D("2"), "PAXG": D("0")})
    )
    assert [(r.asset, r.target_weight, r.weight) for r in report.rows] == [
        ("BTC", D("0.5"), D("0.5")),
        ("ETH", D("0.5"), D("0.5")),
    ]


def test_an_unheld_target_reads_zero_weight_and_needs_no_mark(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.01", mark="100000")
    report = bands_view(repo, _config(target_weights=_HALVES))
    assert report.incomplete == ()
    assert [(r.asset, r.value_usd, r.weight, r.status) for r in report.rows] == [
        ("BTC", D("1000"), D("1"), "over"),
        ("ETH", D("0"), D("0"), "under"),
    ]


def test_a_missing_mark_makes_the_whole_report_incomplete_not_wrong(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.006", mark="100000")
    _hold(repo, "ETH-USD", qty="0.1", mark=None)
    report = bands_view(repo, _config(target_weights=_HALVES))
    assert report.incomplete == ("ETH",) and report.rows == ()


def test_a_held_asset_outside_the_targets_is_named_and_not_weighed(repo) -> None:
    _hold(repo, "BTC-USD", qty="0.005", mark="100000")
    _hold(repo, "ETH-USD", qty="0.125", mark="4000")
    _hold(repo, "DOGE-USD", qty="1000", mark="1")
    report = bands_view(repo, _config(target_weights=_HALVES))
    assert report.untargeted == ("DOGE-USD",)
    assert [(r.asset, r.weight) for r in report.rows] == [("BTC", D("0.5")), ("ETH", D("0.5"))]


def test_nothing_held_in_the_targets_is_no_rows(repo) -> None:
    report = bands_view(repo, _config(target_weights=_HALVES))
    assert (report.rows, report.incomplete, report.untargeted) == ((), (), ())


def _band_row(**overrides: Any) -> BandRow:
    base: dict[str, Any] = dict(
        asset="BTC",
        target_weight=D("0.5"),
        weight=D("0.6"),
        band=D("0.075"),
        status="over",
        value_usd=D("600"),
        candidate_sell_usd=D("100"),
        fee_drag_usd=D("2.51"),
    )
    base.update(overrides)
    return BandRow(**base)


def test_the_bands_report_says_band_trimming_was_tested_and_not_adopted() -> None:
    report = BandsReport(rows=(_band_row(),), incomplete=(), untargeted=())
    lines = render_bands(report)
    for note in (
        sleeve_report.BANDS_NOT_A_RECOMMENDATION,
        sleeve_report.BANDS_NOT_ADOPTED,
        sleeve_report.BANDS_REDEPLOY_NOTE,
    ):
        assert lines.count(note) == 1, note
    assert lines.index(sleeve_report.BANDS_NOT_ADOPTED) > lines.index(
        next(line for line in lines if line.startswith("  BTC "))
    )


def test_the_bands_notes_carry_the_831_finding_and_the_rails() -> None:
    assert "tested and not adopted (#831)" in sleeve_report.BANDS_NOT_ADOPTED
    assert "rail-14" in sleeve_report.BANDS_REDEPLOY_NOTE
    assert "rail 8" in sleeve_report.BANDS_REDEPLOY_NOTE
    assert "not a recommendation" in sleeve_report.BANDS_NOT_A_RECOMMENDATION


def test_an_incomplete_report_prints_no_weights_and_names_the_missing_marks() -> None:
    lines = render_bands(BandsReport(rows=(), incomplete=("ETH", "SOL"), untargeted=()))
    assert sleeve_report.bands_incomplete_line(("ETH", "SOL")) in lines
    assert not any(line.startswith("  ") for line in lines), "no asset row is printed"
    assert lines.count(sleeve_report.BANDS_NOT_ADOPTED) == 1


def test_an_empty_report_says_so_and_still_carries_the_notes() -> None:
    lines = render_bands(BandsReport(rows=(), incomplete=(), untargeted=()))
    assert lines.count(sleeve_report.NO_BAND_ROWS) == 1
    assert lines.count(sleeve_report.BANDS_NOT_ADOPTED) == 1


def test_a_rendered_row_prints_the_candidate_only_when_over() -> None:
    lines = render_bands(
        BandsReport(
            rows=(
                _band_row(),
                _band_row(
                    asset="ETH",
                    weight=D("0.4"),
                    status="under",
                    value_usd=D("400"),
                    candidate_sell_usd=None,
                    fee_drag_usd=None,
                ),
            ),
            incomplete=(),
            untargeted=("DOGE-USD",),
        )
    )
    rows = [line for line in lines if line.startswith("  ")]
    assert [line.split()[0] for line in rows] == ["BTC", "ETH"]
    assert [("size to target" in line, "fee drag" in line) for line in rows] == [
        (True, True),
        (False, False),
    ]
    assert sleeve_report.bands_untargeted_line(("DOGE-USD",)) in lines


# -- review round 1 (#935): the rendered figures and R52's weighting, pinned exactly ----------


def test_a_rendered_lot_line_prints_each_figure_in_its_own_slot() -> None:
    [line] = [text for text in render_lots([_lot_row()]) if text.startswith("  #")]
    assert line == (
        "  #3 turtle_breakout opened 2023-11-14 qty 0.0132 @ 4673.23  fee share $0.73"
        "  cost $62.42  mark 4300  unrealised $-5.66  if sold now $-6.91"
        " (fee 1.2000%, slippage 1.0000%)"
    )


def test_a_rendered_lot_line_without_a_mark_stops_at_the_cost() -> None:
    [line] = [
        text
        for text in render_lots([_lot_row(mark=None, unrealised=None, realised_if_sold=None)])
        if text.startswith("  #")
    ]
    assert line == (
        "  #3 turtle_breakout opened 2023-11-14 qty 0.0132 @ 4673.23  fee share $0.73"
        "  cost $62.42  mark none (no cached daily close)"
    )


def test_a_rendered_band_line_prints_each_figure_in_its_own_slot() -> None:
    under = _band_row(
        asset="ETH",
        weight=D("0.4"),
        status="under",
        value_usd=D("400"),
        candidate_sell_usd=None,
        fee_drag_usd=None,
    )
    lines = render_bands(BandsReport(rows=(_band_row(), under), incomplete=(), untargeted=()))
    assert [line for line in lines if line.startswith("  ")] == [
        "  BTC target 50.00%  weight 60.00%  band ±7.50%  over  value $600.00"
        "  size to target $100.00  two-leg fee drag $2.51",
        "  ETH target 50.00%  weight 40.00%  band ±7.50%  under  value $400.00",
    ]


def test_the_redeploy_legs_slippage_is_weighted_by_each_underweights_shortfall(repo) -> None:
    """R52 with TWO underweights: ETH is .10 short, SOL .20, so the buy leg's slippage is
    (ETH x .1 + SOL x .2) / .3 -- not either one alone, not their max, not a plain mean."""
    _hold(repo, "BTC-USD", qty="0.008", mark="100000")  # $800 -> .80 of a .50 target
    _hold(repo, "ETH-USD", qty="0.0375", mark="4000")  # $150 -> .15 of .25
    _hold(repo, "SOL-USD", qty="0.001", mark="50000")  # $50 -> .05 of .25
    weights = {"BTC": D("0.5"), "ETH": D("0.25"), "SOL": D("0.25")}
    report = bands_view(repo, _config(target_weights=weights))
    slip = {p: backtest_slippage(repo, f"{p}-USD")[0] for p in ("BTC", "ETH", "SOL")}
    assert slip["ETH"] != slip["SOL"], "the fixture must tell the weightings apart"
    fee = _config().fees.taker_pct
    buy = (slip["ETH"] * D("0.1") + slip["SOL"] * D("0.2")) / D("0.3")
    btc = next(r for r in report.rows if r.asset == "BTC")
    assert btc.candidate_sell_usd == D("300")
    assert btc.fee_drag_usd == D("300") * (fee + slip["BTC"]) + D("300") * (fee + buy)


def test_a_target_asset_held_in_another_quote_is_named_not_dropped(repo) -> None:
    """R51: only the settlement-currency product is weighed; a BTC-USDC holding on a USD profile
    is listed as untargeted rather than silently missing from the report."""
    _hold(repo, "BTC-USD", qty="0.005", mark="100000")
    _hold(repo, "ETH-USD", qty="0.125", mark="4000")
    _hold(repo, "BTC-USDC", qty="0.001", mark="100000")
    report = bands_view(repo, _config(target_weights=_HALVES))
    assert report.untargeted == ("BTC-USDC",)
    assert [(r.asset, r.weight) for r in report.rows] == [("BTC", D("0.5")), ("ETH", D("0.5"))]


# -- carried from P13's held question (a): every view names the daily bar it marks at, and says
# -- when that bar is older than the one a cycle would judge a sleeve rule on ----------------------

#: One hour into UTC day 100: the newest COMPLETED daily bar is day 99's.
_NOW_100 = 100 * DAY + 3_600


def _dated(day: int) -> str:
    return sleeve_report._day(day * DAY)


@pytest.mark.parametrize(("last_day", "behind"), [(99, 0), (98, 1), (90, 9)])
def test_the_mark_bar_is_judged_by_the_cycles_own_daily_readiness_gate(
    last_day: int, behind: int
) -> None:
    """`MarkBar.stale` is exactly `freshness.entry_bar_ready`'s verdict on a daily-only series --
    the gate `agent._handle_reductions` skips a sleeve rule on (#917) -- not a new threshold."""
    from keel.data import freshness

    daily = [_candle(d, "100") for d in range(last_day - 2, last_day + 1)]
    bar = sleeve_report.mark_bar(daily, _NOW_100)
    readiness = freshness.entry_bar_ready(
        {Granularity.ONE_DAY: daily}, Granularity.ONE_DAY, _NOW_100
    )
    assert bar is not None
    assert (bar.ts, bar.expected_ts, bar.bars_behind) == (last_day * DAY, 99 * DAY, behind)
    assert bar.stale is (not readiness.ready) is (behind > 0)


def test_no_daily_bar_is_no_mark_bar() -> None:
    assert sleeve_report.mark_bar([], _NOW_100) is None


def test_the_mark_bar_head_names_one_date_a_range_or_none() -> None:
    fresh = sleeve_report.MarkBar(99 * DAY, 99 * DAY, 0)
    old = sleeve_report.MarkBar(90 * DAY, 99 * DAY, 9)
    assert sleeve_report.mark_bar_head([fresh, fresh, None]) == f"mark bar {_dated(99)}"
    assert sleeve_report.mark_bar_head([fresh, old]) == (f"mark bars {_dated(90)}..{_dated(99)}")
    assert sleeve_report.mark_bar_head([None]) == "mark bar none"


def test_a_stale_line_names_the_product_its_bar_and_the_bar_the_cycle_expects() -> None:
    old = sleeve_report.MarkBar(90 * DAY, 99 * DAY, 9)
    assert sleeve_report.stale_mark_line("PAXG-USD", old) == (
        f"STALE mark: PAXG-USD's latest cached daily bar is {_dated(90)}, 9 bar(s) behind "
        f"{_dated(99)}, the newest completed one -- a cycle would not judge a sleeve rule on it "
        "(freshness.entry_bar_ready), so every PAXG-USD figure here is priced at an old close"
    )


def _two_products(repo: Repository) -> None:
    """BTC fresh (its newest bar is day 99's), PAXG stale (day 90)."""
    for product, opened_at in (("BTC-USD", 1), ("PAXG-USD", 2)):
        repo.open_position(
            product_id=product,
            rule_name="dca",
            opened_at=opened_at,
            qty=D("0.01"),
            entry_fill=D("100"),
            entry_fee=D("0"),
        )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(98, "110"), _candle(99, "120")])
    repo.upsert_candles("PAXG-USD", Granularity.ONE_DAY, [_candle(90, "130")])


def test_the_lots_view_carries_each_products_mark_bar(repo) -> None:
    _two_products(repo)
    rows = lots_view(repo, _config(), now_ts=_NOW_100)
    assert [(r.product_id, r.mark_bar) for r in rows] == [
        ("BTC-USD", sleeve_report.MarkBar(99 * DAY, 99 * DAY, 0)),
        ("PAXG-USD", sleeve_report.MarkBar(90 * DAY, 99 * DAY, 9)),
    ]


def test_the_lots_head_names_the_mark_bar_and_a_stale_product_is_flagged_once(repo) -> None:
    _two_products(repo)
    rows = lots_view(repo, _config(), now_ts=_NOW_100)
    lines = render_lots(rows)
    assert lines[0] == (
        f"lots -- fees at the fallback rate ({sleeve.FALLBACK_FEE_SOURCE}); no venue asked; "
        f"mark bars {_dated(90)}..{_dated(99)}"
    )
    stale = [line for line in lines if line.startswith("STALE mark: ")]
    assert stale == [sleeve_report.stale_mark_line("PAXG-USD", rows[1].mark_bar)]
    assert lines[1] == stale[0], "the flag sits directly under the head it qualifies"


def test_a_fresh_lots_report_has_one_date_and_no_flag(repo) -> None:
    _two_products(repo)
    lines = render_lots(lots_view(repo, _config(), product_id="BTC-USD", now_ts=_NOW_100))
    assert lines[0].endswith(f"; mark bar {_dated(99)}")
    assert not any(line.startswith("STALE mark: ") for line in lines)


def test_the_bands_view_carries_the_mark_bar_of_every_weighed_holding(repo) -> None:
    _two_products(repo)
    report = bands_view(
        repo, _config(target_weights={"BTC": D("0.5"), "PAXG": D("0.5")}), now_ts=_NOW_100
    )
    assert report.marks == (
        ("BTC-USD", sleeve_report.MarkBar(99 * DAY, 99 * DAY, 0)),
        ("PAXG-USD", sleeve_report.MarkBar(90 * DAY, 99 * DAY, 9)),
    )
    lines = render_bands(report)
    assert lines[0].endswith(f"; mark bars {_dated(90)}..{_dated(99)}")
    assert [line for line in lines if line.startswith("STALE mark: ")] == [
        sleeve_report.stale_mark_line("PAXG-USD", report.marks[1][1])
    ]
    assert lines[1].startswith("STALE mark: PAXG-USD")


def test_a_distribution_row_names_its_mark_bar_and_flags_a_stale_one(repo) -> None:
    repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1"},
        status="live",
    )
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=1,
        qty=D("0.01"),
        entry_fill=D("100"),
        entry_fee=D("0"),
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(90, "100000")])
    [row] = distribution_rows(repo, _config(), now_ts=_NOW_100)
    assert row.mark_bar == sleeve_report.MarkBar(90 * DAY, 99 * DAY, 9)
    first, *rest = render_distribution([row])
    assert f"proposed {_dated(121)}, mark bar {_dated(90)}: " in first
    assert first.startswith("rule ")
    assert rest[0] == sleeve_report.stale_mark_line("BTC-USD", row.mark_bar)
