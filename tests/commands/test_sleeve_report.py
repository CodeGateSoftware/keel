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
