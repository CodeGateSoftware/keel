"""Tests for keel.strategy.promotion: the promotion/demotion lifecycle (P2 Task 9).

Builds `BacktestResult` fixtures directly (this module must not depend on `backtest.py`'s
simulation internals, `paper.py`, or `engine.py` — it only consumes the `BacktestResult`
shape, per the Phase 2 plan) and exercises the gates + DB-backed `transition()` against a
real in-memory `Repository`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.backtest import BacktestResult
from keel.strategy.promotion import PromotionConfig, can_promote, should_demote, transition


def _stats(
    n_trades: int = 150,
    win_rate: float = 0.6,
    avg_win: Decimal = Decimal("30"),
    avg_loss: Decimal = Decimal("-10"),
    expectancy: Decimal = Decimal("14"),
) -> BacktestResult:
    """A `BacktestResult` fixture with sane, independently-overridable fields.

    Defaults comfortably clear the default `PromotionConfig` floors (rr = 30/10 = 3.0 >=
    1.5, win_rate 0.6 >= 0.55, expectancy 14 > 0, n_trades 150 >= 100).
    """
    return BacktestResult(
        trades=[],
        n_trades=n_trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=Decimal("2"),
        max_drawdown=Decimal("50"),
        max_losing_streak=4,
        avg_mfe=Decimal("20"),
        avg_mae=Decimal("8"),
    )


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _insert_rule(repo: Repository, kind: str, status: str = "candidate") -> int:
    cursor = repo._conn.execute(
        "INSERT INTO rules (kind, params, status, created_at) VALUES (?, ?, ?, ?)",
        (kind, "{}", status, 1_700_000_000),
    )
    repo._conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _rule_status(repo: Repository, rule_id: int) -> str:
    row = repo._conn.execute("SELECT status FROM rules WHERE id = ?", (rule_id,)).fetchone()
    assert row is not None
    return row["status"]


# -- can_promote --------------------------------------------------------------


def test_can_promote_true_when_all_floors_met() -> None:
    cfg = PromotionConfig()
    ok, reasons = can_promote(_stats(), cfg)
    assert ok is True
    assert reasons == []


def test_can_promote_false_on_failing_win_rate_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = can_promote(_stats(win_rate=0.40), cfg)
    assert ok is False
    assert any("win_rate" in r for r in reasons)


def test_can_promote_false_on_failing_expectancy_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = can_promote(_stats(expectancy=Decimal("-1")), cfg)
    assert ok is False
    assert any("expectancy" in r for r in reasons)


def test_can_promote_false_on_failing_rr_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = can_promote(_stats(avg_win=Decimal("5"), avg_loss=Decimal("-10")), cfg)
    assert ok is False
    assert any("rr" in r for r in reasons)


def test_can_promote_false_on_insufficient_sample_with_reason() -> None:
    cfg = PromotionConfig()
    ok, reasons = can_promote(_stats(n_trades=10), cfg)
    assert ok is False
    assert any("n_trades" in r for r in reasons)


def test_can_promote_accumulates_all_failing_reasons() -> None:
    cfg = PromotionConfig()
    ok, reasons = can_promote(
        _stats(n_trades=5, win_rate=0.1, avg_win=Decimal("1"), avg_loss=Decimal("-10")), cfg
    )
    assert ok is False
    assert len(reasons) >= 3


def test_can_promote_respects_custom_config_floors() -> None:
    cfg = PromotionConfig(min_trades=5, min_rr=Decimal("1.0"), min_win_rate=0.5)
    stats = _stats(n_trades=6, win_rate=0.5, avg_win=Decimal("10"), avg_loss=Decimal("-9"))
    ok, reasons = can_promote(stats, cfg)
    assert ok is True
    assert reasons == []


# -- should_demote --------------------------------------------------------------


def test_should_demote_false_when_rolling_stats_above_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(), cfg) is False


def test_should_demote_true_when_rolling_win_rate_below_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(win_rate=0.30), cfg) is True


def test_should_demote_true_when_rolling_expectancy_below_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(expectancy=Decimal("-5")), cfg) is True


def test_should_demote_true_when_rolling_rr_below_floor() -> None:
    cfg = PromotionConfig()
    assert should_demote(_stats(avg_win=Decimal("5"), avg_loss=Decimal("-10")), cfg) is True


def test_should_demote_ignores_min_trades_floor() -> None:
    # A live rule's rolling window is deliberately smaller than the initial promotion
    # sample; should_demote must still fire on a small-but-healthy rolling sample being
    # False, and small-but-decayed sample being True, without requiring min_trades.
    cfg = PromotionConfig()
    assert should_demote(_stats(n_trades=10), cfg) is False
    assert should_demote(_stats(n_trades=10, win_rate=0.1), cfg) is True


# -- transition -----------------------------------------------------------------


def test_transition_promotes_candidate_to_paper_on_passing_stats(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "pullback_continuation", status="candidate")
    cfg = PromotionConfig()

    new_status = transition(repo, "pullback_continuation", _stats(), cfg)

    assert new_status == "paper"
    assert _rule_status(repo, rule_id) == "paper"


def test_transition_keeps_candidate_when_stats_fail(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "pullback_continuation", status="candidate")
    cfg = PromotionConfig()

    new_status = transition(repo, "pullback_continuation", _stats(win_rate=0.1), cfg)

    assert new_status == "candidate"
    assert _rule_status(repo, rule_id) == "candidate"


def test_transition_promotes_paper_to_live_on_passing_stats(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "rsi_meanrev", status="paper")
    cfg = PromotionConfig()

    new_status = transition(repo, "rsi_meanrev", _stats(), cfg)

    assert new_status == "live"
    assert _rule_status(repo, rule_id) == "live"


def test_transition_demotes_live_to_disabled_on_decayed_stats(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "dca", status="live")
    cfg = PromotionConfig()

    new_status = transition(repo, "dca", _stats(win_rate=0.2, expectancy=Decimal("-3")), cfg)

    assert new_status == "disabled"
    assert _rule_status(repo, rule_id) == "disabled"


def test_transition_keeps_live_when_rolling_stats_healthy(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "dca", status="live")
    cfg = PromotionConfig()

    new_status = transition(repo, "dca", _stats(), cfg)

    assert new_status == "live"
    assert _rule_status(repo, rule_id) == "live"


def test_transition_disabled_is_terminal(repo: Repository) -> None:
    rule_id = _insert_rule(repo, "dca", status="disabled")
    cfg = PromotionConfig()

    new_status = transition(repo, "dca", _stats(), cfg)

    assert new_status == "disabled"
    assert _rule_status(repo, rule_id) == "disabled"


def test_transition_unknown_rule_raises(repo: Repository) -> None:
    cfg = PromotionConfig()
    with pytest.raises(ValueError):
        transition(repo, "no-such-rule", _stats(), cfg)
