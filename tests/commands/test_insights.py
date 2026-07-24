"""Tests for `keel insights` -- the read-only summary + journal reporting surface.

Mirrors `tests/commands/test_status.py`'s split: pure builder functions get direct unit tests
(no CliRunner needed), renderers are tested as pure `list[str]` functions, and the click
commands themselves get one thin `CliRunner` pass each (human default + `--json`).

This module is entirely READ-ONLY over the DB -- no test here ever asserts on a write path,
because `keel/commands/insights.py` has none.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands.insights import (
    build_account_summary,
    build_gate_distance,
    build_insights_report,
    build_journal_report,
    build_rule_track_record,
    render_journal,
    render_summary,
)
from keel.commands.status import StatusReport, gather_status
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.paper import track_record
from keel.strategy.promotion import PromotionConfig, floor_for_class, promotion_class_of
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Granularity

NOW_TS = 1_800_000_000


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    return r


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "ETH"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        ),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _default_floor(config: Config) -> PromotionConfig:
    return PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )


def _seed_paper_trade(
    repo: Repository,
    rule_name: str,
    *,
    product_id: str = "BTC-USD",
    entry: str = "100",
    exit_price: str = "110",
    entry_ts: int = 1_000,
    exit_ts: int = 2_000,
    qty: str = "1",
    pnl: str = "10",
    r_multiple: str | None = "1.0",
    outcome: str = "win",
) -> tuple[int, int]:
    """Write an entry+exit paper order pair directly (matching `paper.py`'s own payload
    shape) so tests can control exact win/loss/r_multiple stats without simulating candles."""
    entry_id = repo.insert_order(
        dict(
            mode="paper",
            product_id=product_id,
            side="BUY",
            order_type="market",
            qty=Decimal(qty),
            limit_price=Decimal(entry),
            status="filled",
            fee=Decimal("0"),
            expected_fill=Decimal(entry),
            actual_fill=Decimal(entry),
            raw_response=json.dumps(
                {
                    "role": "entry",
                    "rule_name": rule_name,
                    "entry": entry,
                    "stop": "90",
                    "target": "120",
                    "qty": qty,
                    "ts": entry_ts,
                }
            ),
            confirmation="paper",
            rule_id=None,
            created_at=entry_ts,
            updated_at=entry_ts,
        )
    )
    exit_id = repo.insert_order(
        dict(
            mode="paper",
            product_id=product_id,
            side="SELL",
            order_type="market",
            qty=Decimal(qty),
            limit_price=Decimal(exit_price),
            status="filled",
            fee=Decimal("0"),
            expected_fill=Decimal(exit_price),
            actual_fill=Decimal(exit_price),
            raw_response=json.dumps(
                {
                    "role": "exit",
                    "rule_name": rule_name,
                    "entry_order_id": entry_id,
                    "entry": entry,
                    "exit": exit_price,
                    "qty": qty,
                    "pnl": pnl,
                    "r_multiple": r_multiple,
                    "mfe": "0",
                    "mae": "0",
                    "outcome": outcome,
                    "entry_ts": entry_ts,
                    "exit_ts": exit_ts,
                }
            ),
            confirmation="paper",
            rule_id=None,
            created_at=exit_ts,
            updated_at=exit_ts,
        )
    )
    return entry_id, exit_id


def _seed_trade_outcome(
    repo: Repository,
    *,
    rule_name: str | None = "dca",
    product_id: str = "BTC-USD",
    is_dca: bool = False,
    opened_at: int = 1_000,
    closed_at: int = 2_000,
    qty: str = "1",
    entry_fill: str = "100",
    exit_fill: str = "110",
    fees: str = "0.5",
    pnl_net: str = "9.5",
) -> int:
    return repo.insert_trade_outcome(
        dict(
            product_id=product_id,
            rule_name=rule_name,
            is_dca=is_dca,
            opened_at=opened_at,
            closed_at=closed_at,
            qty=Decimal(qty),
            entry_fill=Decimal(entry_fill),
            exit_fill=Decimal(exit_fill),
            fees=Decimal(fees),
            pnl_net=Decimal(pnl_net),
        )
    )


# -- build_gate_distance -----------------------------------------------------------------------


def test_gate_distance_trades_remaining_is_floor_minus_n_trades(repo: Repository) -> None:
    for i in range(5):
        _seed_paper_trade(repo, "dca", entry_ts=1000 + i, exit_ts=1100 + i)
    stats = track_record(repo, "dca")
    rule = Dca(product_id="BTC-USD")
    default_floor = _default_floor(_config())

    gate = build_gate_distance(rule, stats, default_floor)

    assert gate.n_trades == 5
    assert gate.min_trades == default_floor.min_trades
    assert gate.trades_remaining == default_floor.min_trades - 5


def test_gate_distance_blocking_reasons_include_n_trades_at_low_n(repo: Repository) -> None:
    _seed_paper_trade(repo, "dca")
    stats = track_record(repo, "dca")
    rule = Dca(product_id="BTC-USD")
    default_floor = _default_floor(_config())

    gate = build_gate_distance(rule, stats, default_floor)

    assert gate.passing is False
    assert any("n_trades" in reason for reason in gate.blocking_reasons)


def test_gate_distance_trend_follow_uses_relaxed_win_floor(repo: Repository) -> None:
    """`turtle_breakout` is `promotion_class = "trend_follow"`: win floor 0.30 (not the
    canonical 0.55), and `min_trades` stays the canonical 100 (NOT relaxed to 30)."""
    rule = TurtleBreakout(product_id="BTC-USD")
    stats = track_record(repo, "turtle_breakout")  # no trades seeded -- floor values only matter
    default_floor = _default_floor(_config())

    gate = build_gate_distance(rule, stats, default_floor)

    expected_floor = floor_for_class(promotion_class_of(rule), default=default_floor)
    assert gate.min_win_rate == 0.30
    assert gate.min_trades == 100
    assert expected_floor.min_win_rate == 0.30
    assert expected_floor.min_trades == 100


# -- build_rule_track_record -------------------------------------------------------------------


def test_rule_track_record_not_significant_below_30_trades(repo: Repository) -> None:
    for i in range(10):
        _seed_paper_trade(repo, "dca", entry_ts=1000 + i, exit_ts=1100 + i)
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="paper")
    row = next(r for r in repo.get_rules() if r["kind"] == "dca")
    stats = track_record(repo, "dca")
    default_floor = _default_floor(_config())

    record = build_rule_track_record(row, stats, default_floor)

    assert record.n_trades == 10
    assert record.significant is False


def test_rule_track_record_gate_none_for_non_paper_status(repo: Repository) -> None:
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")
    row = next(r for r in repo.get_rules() if r["kind"] == "dca")
    stats = track_record(repo, "dca")
    default_floor = _default_floor(_config())

    record = build_rule_track_record(row, stats, default_floor)

    assert record.status == "live"
    assert record.gate is None


def test_rule_track_record_gate_none_for_candidate_status(repo: Repository) -> None:
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="candidate")
    row = next(r for r in repo.get_rules() if r["kind"] == "dca")
    stats = track_record(repo, "dca")
    default_floor = _default_floor(_config())

    record = build_rule_track_record(row, stats, default_floor)

    assert record.gate is None


def test_rule_track_record_realized_rr_none_when_no_losses(repo: Repository) -> None:
    _seed_paper_trade(repo, "dca", pnl="10", outcome="win")
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="paper")
    row = next(r for r in repo.get_rules() if r["kind"] == "dca")
    stats = track_record(repo, "dca")
    assert stats.avg_loss == Decimal(0)
    default_floor = _default_floor(_config())

    record = build_rule_track_record(row, stats, default_floor)

    assert record.realized_rr is None


def test_rule_track_record_degrades_gracefully_for_unrecognized_kind(repo: Repository) -> None:
    """A stale `rules` row whose `kind` is no longer in `RULE_REGISTRY` must not crash the
    whole report -- `_build_rule` raises `ValueError`, which degrades to a default class and a
    `None` gate rather than propagating."""
    repo.insert_rule("some_retired_rule_kind", {"product_id": "BTC-USD"}, status="paper")
    row = next(r for r in repo.get_rules() if r["kind"] == "some_retired_rule_kind")
    stats = track_record(repo, "some_retired_rule_kind")
    default_floor = _default_floor(_config())

    record = build_rule_track_record(row, stats, default_floor)

    assert record.promotion_class == "default"
    assert record.gate is None


# -- build_account_summary ---------------------------------------------------------------------


def test_account_summary_projects_verbatim_from_status_report() -> None:
    from keel.commands.status import AutonomyStatus

    status_report = StatusReport(
        now_ts=NOW_TS,
        mode="paper",
        kill_switch_engaged=False,
        autonomy=AutonomyStatus(
            live=False, autonomous=False, autonomous_until=None, updated_ts=None,
            profile_readable=True,
        ),
        equity_state_mode="paper",
        high_water_mark=Decimal("1000"),
        drawdown_total_pct=Decimal("0.05"),
        drawdown_weekly_pct=Decimal("0.01"),
        max_total_dd_pct=Decimal("0.20"),
        max_weekly_dd_pct=Decimal("0.08"),
        rail11_status="ok",
        paper_cash_usdc=Decimal("955.25"),
        open_positions=[],
        rule_counts={},
        live_rules=[],
        data_freshness=[],
        subscriptions=[],
    )

    summary = build_account_summary(status_report)

    assert summary.mode == "paper"
    assert summary.rail11_status == "ok"
    assert summary.drawdown_total_pct == Decimal("0.05")
    assert summary.drawdown_weekly_pct == Decimal("0.01")
    assert summary.high_water_mark == Decimal("1000")
    assert summary.paper_cash_usdc == Decimal("955.25")
    assert summary.max_total_dd_pct == Decimal("0.20")
    assert summary.max_weekly_dd_pct == Decimal("0.08")


# -- build_insights_report ----------------------------------------------------------------------


def test_insights_report_empty_db_does_not_crash(repo: Repository) -> None:
    """The real current state of a fresh DB: no crash, zero closed trades, no/zero-trade rules."""
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_insights_report(repo, config, status_report, NOW_TS)

    assert report.closed_trade_count == 0
    assert report.rules == []


def test_insights_report_rule_filter(repo: Repository) -> None:
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="paper")
    repo.insert_rule("turtle_breakout", {"product_id": "ETH-USD"}, status="candidate")
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_insights_report(repo, config, status_report, NOW_TS, rule_filter="dca")

    assert len(report.rules) == 1
    assert report.rules[0].rule_name == "dca"


def test_insights_report_counts_closed_trades(repo: Repository) -> None:
    _seed_trade_outcome(repo, closed_at=1500)
    _seed_trade_outcome(repo, closed_at=1600)
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_insights_report(repo, config, status_report, NOW_TS)

    assert report.closed_trade_count == 2


# -- build_journal_report -----------------------------------------------------------------------


def test_journal_report_internal_order_is_oldest_first(repo: Repository) -> None:
    _seed_trade_outcome(repo, closed_at=3000, opened_at=2900, rule_name="dca")
    _seed_trade_outcome(repo, closed_at=1000, opened_at=900, rule_name="dca")
    _seed_trade_outcome(repo, closed_at=2000, opened_at=1900, rule_name="dca")
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS)

    assert [e.closed_at for e in report.entries] == [1000, 2000, 3000]


def test_journal_report_limit_caps_and_total_count_is_pre_limit(repo: Repository) -> None:
    for i in range(5):
        _seed_trade_outcome(repo, closed_at=1000 + i, opened_at=900 + i)
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS, limit=2)

    assert len(report.entries) == 2
    assert report.total_count == 5


def test_journal_report_dca_row_has_no_r_multiple(repo: Repository) -> None:
    _seed_trade_outcome(repo, is_dca=True, rule_name="dca", closed_at=1500)
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS)

    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.is_dca is True
    assert entry.r_multiple is None


def test_journal_report_include_open_appends_open_rows(repo: Repository) -> None:
    repo.open_position(
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        opened_at=NOW_TS - 100,
        qty=Decimal("0.01"),
        entry_fill=Decimal("65000"),
        entry_fee=Decimal("1.5"),
        bracket_order_id=None,
    )
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS, include_open=True)

    assert any(e.outcome == "open" for e in report.entries)
    open_entry = next(e for e in report.entries if e.outcome == "open")
    assert open_entry.r_multiple is None
    assert open_entry.product_id == "BTC-USD"


def test_journal_report_without_include_open_has_no_open_rows(repo: Repository) -> None:
    repo.open_position(
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        opened_at=NOW_TS - 100,
        qty=Decimal("0.01"),
        entry_fill=Decimal("65000"),
        entry_fee=Decimal("1.5"),
        bracket_order_id=None,
    )
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS, include_open=False)

    assert report.entries == []


def test_journal_report_rule_and_asset_filters(repo: Repository) -> None:
    _seed_trade_outcome(repo, rule_name="dca", product_id="BTC-USD", closed_at=1000)
    _seed_trade_outcome(repo, rule_name="turtle_breakout", product_id="ETH-USD", closed_at=1100)
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS, rule="dca")
    assert len(report.entries) == 1
    assert report.entries[0].rule_name == "dca"

    report2 = build_journal_report(repo, status_report, NOW_TS, asset="ETH-USD")
    assert len(report2.entries) == 1
    assert report2.entries[0].product_id == "ETH-USD"


def test_journal_report_since_until_window(repo: Repository) -> None:
    _seed_trade_outcome(repo, closed_at=1000)
    _seed_trade_outcome(repo, closed_at=2000)
    _seed_trade_outcome(repo, closed_at=3000)
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS, since_ts=1500, until_ts=2500)

    assert [e.closed_at for e in report.entries] == [2000]


def test_journal_report_enriches_r_multiple_from_paper_track_record(repo: Repository) -> None:
    """A `trade_outcomes` row that matches a paper trade's (entry_ts, exit_ts) gets that
    trade's real `r_multiple`/`outcome` rather than a pnl-sign guess."""
    _seed_paper_trade(
        repo, "dca", entry_ts=1000, exit_ts=2000, pnl="10", r_multiple="2.5", outcome="win"
    )
    _seed_trade_outcome(
        repo, rule_name="dca", opened_at=1000, closed_at=2000, pnl_net="9.5", is_dca=False
    )
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS)

    assert len(report.entries) == 1
    assert report.entries[0].r_multiple == Decimal("2.5")
    assert report.entries[0].outcome == "win"


def test_journal_report_no_match_derives_outcome_from_pnl_sign(repo: Repository) -> None:
    _seed_trade_outcome(repo, rule_name="unmatched_rule", pnl_net="-5", closed_at=1500)
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)

    report = build_journal_report(repo, status_report, NOW_TS)

    assert len(report.entries) == 1
    assert report.entries[0].r_multiple is None
    assert report.entries[0].outcome == "loss"


# -- renderers ------------------------------------------------------------------------------


def test_render_summary_nonempty_and_has_small_sample_note(repo: Repository) -> None:
    for i in range(5):
        _seed_paper_trade(repo, "dca", entry_ts=1000 + i, exit_ts=1100 + i)
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="paper")
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)
    report = build_insights_report(repo, config, status_report, NOW_TS)

    lines = render_summary(report)

    assert len(lines) > 0
    assert any("n<30" in line for line in lines)


def test_render_summary_empty_db_has_friendly_line_not_blank(repo: Repository) -> None:
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)
    report = build_insights_report(repo, config, status_report, NOW_TS)

    lines = render_summary(report)

    assert len(lines) > 0
    assert any(line.strip() for line in lines)
    assert any("no" in line.lower() for line in lines)


def test_render_journal_empty_db_has_friendly_line(repo: Repository) -> None:
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)
    report = build_journal_report(repo, status_report, NOW_TS)

    lines = render_journal(report)

    assert len(lines) > 0
    assert any("no" in line.lower() for line in lines)


def test_render_journal_dca_row_labeled_and_no_r_equals_zero(repo: Repository) -> None:
    _seed_trade_outcome(repo, is_dca=True, rule_name="dca", closed_at=1500)
    config = _config()
    status_report = gather_status(repo, config, now_ts=NOW_TS)
    report = build_journal_report(repo, status_report, NOW_TS)

    lines = render_journal(report)

    joined = "\n".join(lines)
    assert "DCA" in joined
    assert "R=0" not in joined


# -- the `keel insights` commands ----------------------------------------------------------------


def _repo_at(db_path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def test_insights_summary_command_default_prints_disclaimer(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "insights", "summary"]
    )

    assert result.exit_code == 0, result.output
    assert "not financial advice" in result.output


def test_insights_summary_command_json_is_valid_no_prose(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path), "insights", "summary", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "not financial advice" not in result.output
    assert "account" in payload
    assert "rules" in payload
    assert "closed_trade_count" in payload
    assert payload["account"]["mode"] == "paper"


def test_insights_journal_command_default_prints_disclaimer(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "insights", "journal"]
    )

    assert result.exit_code == 0, result.output
    assert "not financial advice" in result.output


def test_insights_journal_command_json_is_valid_no_prose(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path), "insights", "journal", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "not financial advice" not in result.output
    assert "entries" in payload
    assert "total_count" in payload
    assert "filters" in payload
