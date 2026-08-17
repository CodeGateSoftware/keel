"""Tests for `keel status` -- the read-only, no-broker operator dashboard.

Two layers, matching `keel/commands/status.py`'s split:

- `gather_status` is a PURE function (`Repository` + `Config` + `now_ts` -> `StatusReport`
  dataclass), driven directly here for every logic branch (Rail 11 halted/ok/unknown,
  kill-switch rendering, positions, rule counts, freshness). No click, no CliRunner needed for
  these.
- The `keel status` command itself (human-readable + `--json`) gets one thin `CliRunner` pass,
  since the rendering/wiring is what's left untested by the pure-function tests.

Fixtures mirror `tests/test_agent.py::repo` (in-memory `Repository`, `set_state` seeding) and
`tests/conftest.py::valid_config_path` (a real `config.yaml` on disk for the CLI test).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands.status import gather_status, render_human
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
from keel.execution.executor import WITHDRAWAL_ATTESTATION_TTL_SEC
from keel.types import Candle, Granularity

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


# -- mode / kill-switch / autonomy -----------------------------------------------------------


def test_paper_mode_surfaces_equity_and_ok_drawdown(repo: Repository) -> None:
    repo.set_state("equity_state_mode", "paper")
    repo.set_state("paper_cash_usdc", Decimal("955.25"))
    repo.set_state("drawdown_total_pct", Decimal("0.05"))
    repo.set_state("drawdown_weekly_pct", Decimal("0.01"))

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert report.mode == "paper"
    assert report.equity_state_mode == "paper"
    assert report.paper_cash_usdc == Decimal("955.25")
    assert report.drawdown_total_pct == Decimal("0.05")
    assert report.drawdown_weekly_pct == Decimal("0.01")
    assert report.rail11_status == "ok"


def test_confirm_mode_reports_no_paper_cash(repo: Repository) -> None:
    report = gather_status(repo, _config(auto_trade=AutoTradeConfig(mode="confirm")), now_ts=NOW_TS)
    assert report.mode == "confirm"
    assert report.paper_cash_usdc is None


def test_rail11_halted_on_total_drawdown_breach(repo: Repository) -> None:
    repo.set_state("drawdown_total_pct", Decimal("0.20"))  # == ceiling: >= trips it
    repo.set_state("drawdown_weekly_pct", Decimal("0.00"))

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert report.rail11_status == "HALTED"


def test_rail11_halted_on_weekly_drawdown_breach(repo: Repository) -> None:
    repo.set_state("drawdown_total_pct", Decimal("0.00"))
    repo.set_state("drawdown_weekly_pct", Decimal("0.09"))  # > 0.08 ceiling

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert report.rail11_status == "HALTED"


def test_rail11_unknown_when_state_never_written(repo: Repository) -> None:
    """A fresh DB has never written `drawdown_total_pct`/`drawdown_weekly_pct` -- unknown must
    not be misread as safe ("ok") nor as an alarm ("HALTED")."""
    report = gather_status(repo, _config(), now_ts=NOW_TS)
    assert report.drawdown_total_pct is None
    assert report.drawdown_weekly_pct is None
    assert report.rail11_status == "unknown"


def test_kill_switch_defaults_engaged(repo: Repository) -> None:
    """`get_state("kill_switch", default=True)` fails closed -- an UNSET key must read engaged,
    not clear. This repo fixture explicitly clears it (`set_state("kill_switch", False)`), so
    exercise the true default via a second, untouched connection."""
    conn = connect(":memory:")
    migrate(conn)
    fresh = Repository(conn)

    report = gather_status(fresh, _config(), now_ts=NOW_TS)

    assert report.kill_switch_engaged is True


def test_kill_switch_clear_when_resumed(repo: Repository) -> None:
    repo.set_state("kill_switch", False)
    report = gather_status(repo, _config(), now_ts=NOW_TS)
    assert report.kill_switch_engaged is False


def test_autonomy_reflects_profile(repo: Repository) -> None:
    repo.set_autonomous(True, now_ts=NOW_TS - 10)
    report = gather_status(repo, _config(), now_ts=NOW_TS)
    assert report.autonomy.live is True
    assert report.autonomy.autonomous is True


def test_autonomy_off_by_default(repo: Repository) -> None:
    report = gather_status(repo, _config(), now_ts=NOW_TS)
    assert report.autonomy.live is False


# -- open positions ---------------------------------------------------------------------------


def test_no_open_positions_is_empty_list(repo: Repository) -> None:
    report = gather_status(repo, _config(), now_ts=NOW_TS)
    assert report.open_positions == []


def _insert_bracket_order(repo: Repository, product_id: str, ts: int) -> int:
    """`positions.bracket_order_id` is a real FK into `orders`; seed one to attach."""
    return repo.insert_order(
        dict(
            mode="live",
            product_id=product_id,
            side="SELL",
            order_type="limit",
            qty=Decimal("0.01"),
            limit_price=Decimal("70000"),
            status="pending",
            fee=Decimal("0"),
            expected_fill=None,
            actual_fill=None,
            raw_response=None,
            confirmation="autonomous",
            rule_id=None,
            created_at=ts,
            updated_at=ts,
        )
    )


def test_open_position_appears_in_report(repo: Repository) -> None:
    bracket_id = _insert_bracket_order(repo, "BTC-USD", NOW_TS - 3600)
    repo.open_position(
        product_id="BTC-USD",
        rule_name="pullback_continuation",
        opened_at=NOW_TS - 3600,
        qty=Decimal("0.01"),
        entry_fill=Decimal("65000"),
        entry_fee=Decimal("1.5"),
        bracket_order_id=bracket_id,
    )

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert len(report.open_positions) == 1
    pos = report.open_positions[0]
    assert pos.product_id == "BTC-USD"
    assert pos.qty == Decimal("0.01")
    assert pos.entry_price == Decimal("65000")
    assert pos.opened_at == NOW_TS - 3600
    assert pos.has_bracket is True


def test_open_position_without_bracket_reports_false(repo: Repository) -> None:
    repo.open_position(
        product_id="ETH-USD",
        rule_name="dca",
        opened_at=NOW_TS,
        qty=Decimal("1"),
        entry_fill=Decimal("3000"),
        entry_fee=Decimal("2"),
        bracket_order_id=None,
    )

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert report.open_positions[0].has_bracket is False


# -- rules ---------------------------------------------------------------------------------


def test_rule_counts_grouped_by_status(repo: Repository) -> None:
    repo.insert_rule("pullback_continuation", {"product_id": "BTC-USD"}, status="candidate")
    repo.insert_rule("dca", {"product_id": "ETH-USD"}, status="candidate")
    repo.insert_rule("turtle_breakout", {"product_id": "BTC-USD"}, status="live")
    repo.insert_rule("mean_reversion", {"product_id": "SOL-USD"}, status="disabled")

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert report.rule_counts == {"candidate": 2, "live": 1, "disabled": 1}


def test_live_rules_are_listed_with_kind_and_product(repo: Repository) -> None:
    repo.insert_rule("turtle_breakout", {"product_id": "BTC-USD", "lookback": 20}, status="live")
    repo.insert_rule("dca", {"product_id": "ETH-USD"}, status="candidate")

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert len(report.live_rules) == 1
    rule = report.live_rules[0]
    assert rule.kind == "turtle_breakout"
    assert rule.product_id == "BTC-USD"
    assert rule.params["lookback"] == 20


def test_no_rules_gives_empty_counts_and_list(repo: Repository) -> None:
    report = gather_status(repo, _config(), now_ts=NOW_TS)
    assert report.rule_counts == {}
    assert report.live_rules == []


# -- data freshness ---------------------------------------------------------------------------


def _candle(ts: int, price: str = "100") -> Candle:
    p = Decimal(price)
    return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))


def test_data_freshness_uses_finest_granularity_and_computes_age(repo: Repository) -> None:
    # Two candles on the finest configured granularity (ONE_HOUR); a coarser ONE_DAY series
    # (seeded with a far staler ts) proves freshness does NOT read that one instead.
    repo.upsert_candles(
        "BTC-USD",
        Granularity.ONE_HOUR,
        [_candle(NOW_TS - 7200), _candle(NOW_TS - 3600)],
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(NOW_TS - 999_999)])

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    btc = next(f for f in report.data_freshness if f.product_id == "BTC-USD")
    assert btc.last_ts == NOW_TS - 3600
    assert btc.age_sec == 3600
    eth = next(f for f in report.data_freshness if f.product_id == "ETH-USD")
    assert eth.last_ts is None
    assert eth.age_sec is None


# -- subscriptions (rail 14, optional section) ------------------------------------------------


def test_subscriptions_empty_when_none_attested(repo: Repository) -> None:
    report = gather_status(repo, _config(), now_ts=NOW_TS)
    assert report.subscriptions == []


def test_subscriptions_surfaced_when_attested(repo: Repository) -> None:
    from keel_core.subscription import BrokerSubscription, SubscriptionStatus

    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue="coinbase",
            tier_name="Preferred",
            free_volume_usd=Decimal("10000"),
            pacing="opportunistic",
            subscription_usd_month=Decimal("29.99"),
            status=SubscriptionStatus.ACTIVE,
            attested_at=NOW_TS - 1000,
            attest_due_ts=NOW_TS + 1_000_000,
        )
    )

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    assert len(report.subscriptions) == 1
    row = report.subscriptions[0]
    assert row.venue == "coinbase"
    assert row.effective_status == "active"


# -- rail 17 (withdrawal attestation, §65.4) -------------------------------------------------


def _attest(repo: Repository, enabled: bool, age_sec: int) -> None:
    repo.set_state("withdrawals_enabled", enabled)
    repo.set_state("withdrawals_attested_at", NOW_TS - age_sec)


def test_rail17_attested_fresh_shows_time_remaining(repo: Repository) -> None:
    """A fresh attestation is visible BEFORE it vetoes: 4 days of the 7 spent -> 3 days left."""
    _attest(repo, enabled=True, age_sec=4 * 86400)

    report = gather_status(repo, _config(), now_ts=NOW_TS)

    w = report.withdrawal_attestation
    assert w.state == "attested"
    assert w.expires_in_sec == 3 * 86400
    assert "rail 17 (withdrawal capability): attested, expires in 3d" in render_human(report)


def test_rail17_expired_names_the_halt_and_the_fix(repo: Repository) -> None:
    """19 days since attestation against the 7-day TTL -> expired 12 days ago, with the command
    that releases it -- the 2026-08-14 event, visible in status instead of in a veto log.

    The halt wording is the LIVE-mode rendering: rail 17 is a LIVE_STATE rail, so the line is
    asserted under a non-paper mode, and the paper variant (which cannot claim a halt the rail
    will not run) is pinned by its own test below."""
    _attest(repo, enabled=True, age_sec=19 * 86400)
    live = _config(auto_trade=AutoTradeConfig(mode="confirm"))

    report = gather_status(repo, live, now_ts=NOW_TS)

    w = report.withdrawal_attestation
    assert w.state == "expired"
    assert w.expired_for_sec == 12 * 86400
    assert (
        "rail 17 (withdrawal capability): EXPIRED 12d ago -- entries halted; "
        "re-attest with keel withdrawals attest" in render_human(report)
    )


def test_rail17_expired_in_paper_names_the_state_not_a_halt(repo: Repository) -> None:
    """Paper mode never evaluates rail 17 (`LIVE_STATE_RAILS`), so claiming "entries halted"
    there would be a permanently-red alert for a halt that cannot occur -- alert fatigue,
    the exact failure #340 exists to fix. The paper rendering names the state and says the
    rail is not evaluated, keeping only the re-attest nudge."""
    _attest(repo, enabled=True, age_sec=19 * 86400)

    report = gather_status(repo, _config(), now_ts=NOW_TS)  # _config() defaults to paper

    assert (
        "rail 17 (withdrawal capability): EXPIRED 12d ago "
        "(rail 17 not evaluated in paper); "
        "re-attest with keel withdrawals attest" in render_human(report)
    )


def test_rail17_never_attested_is_said_as_such(repo: Repository) -> None:
    """A fresh DB has no attestation at all -- rail 17 fails closed on that too, so status
    must not render the silence as health."""
    report = gather_status(
        repo, _config(auto_trade=AutoTradeConfig(mode="confirm")), now_ts=NOW_TS
    )

    w = report.withdrawal_attestation
    assert w.state == "unattested"
    assert w.attested_at is None
    assert (
        "rail 17 (withdrawal capability): never attested -- entries halted; "
        "re-attest with keel withdrawals attest" in render_human(report)
    )


def test_rail17_suspended_attestation_still_names_the_halt(repo: Repository) -> None:
    """A FRESH `--suspended` attestation is a deliberate rail-17 halt, not staleness -- the
    line must say which, because the release is the same command with `--enabled`."""
    _attest(repo, enabled=False, age_sec=3600)

    report = gather_status(
        repo, _config(auto_trade=AutoTradeConfig(mode="confirm")), now_ts=NOW_TS
    )

    w = report.withdrawal_attestation
    assert w.state == "suspended"
    assert (
        "rail 17 (withdrawal capability): SUSPENDED -- entries halted; "
        "re-attest with keel withdrawals attest --enabled" in render_human(report)
    )


def test_rail17_freshness_uses_the_executors_own_ttl(repo: Repository) -> None:
    """Pin the boundary to `WITHDRAWAL_ATTESTATION_TTL_SEC` itself, at ±1s: status must age the
    attestation with the SAME constant the executor vetoes on, never a restated 7 days -- a
    drift between the two would report "attested" on the very cycle rail 17 vetoes."""
    _attest(repo, enabled=True, age_sec=WITHDRAWAL_ATTESTATION_TTL_SEC - 1)
    fresh = gather_status(repo, _config(), now_ts=NOW_TS).withdrawal_attestation
    assert fresh.state == "attested"
    assert fresh.expires_in_sec == 1

    _attest(repo, enabled=True, age_sec=WITHDRAWAL_ATTESTATION_TTL_SEC + 1)
    stale = gather_status(repo, _config(), now_ts=NOW_TS).withdrawal_attestation
    assert stale.state == "expired"
    assert stale.expired_for_sec == 1


def test_rail17_line_sits_beside_rail11(repo: Repository) -> None:
    """The two halt-rails render adjacently, so one glance answers "can the agent enter today?"
    for both the drawdown breaker and the possession rail."""
    _attest(repo, enabled=True, age_sec=3600)
    lines = render_human(gather_status(repo, _config(), now_ts=NOW_TS))

    rail11_at = next(i for i, line in enumerate(lines) if line.startswith("rail11"))
    rail17_at = next(i for i, line in enumerate(lines) if line.startswith("rail 17"))
    assert rail17_at == rail11_at + 1


# -- the `keel status` command --------------------------------------------------------------


def _repo_at(db_path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def test_status_command_runs_read_only_and_prints_key_facts(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    _repo_at(db_path).set_state("kill_switch", False)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "status"]
    )

    assert result.exit_code == 0, result.output
    assert "mode: paper" in result.output
    assert "kill_switch: clear" in result.output.lower() or "clear" in result.output.lower()
    assert "no open positions" in result.output.lower()
    # A fresh DB has never been attested -- rail 17 is halting entries, and status says so
    # rather than rendering the silence as health.
    assert "rail 17 (withdrawal capability): never attested" in result.output


def test_status_command_json_flag_emits_parseable_json(tmp_path, valid_config_path) -> None:
    db_path = tmp_path / "keel.db"
    seeded = _repo_at(db_path)
    seeded.set_state("kill_switch", False)
    seeded.set_state("drawdown_total_pct", Decimal("0.01"))

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "status", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "paper"
    assert payload["kill_switch_engaged"] is False
    assert payload["rail11_status"] in {"ok", "HALTED", "unknown"}
    assert payload["withdrawal_attestation"]["state"] == "unattested"
    assert "open_positions" in payload
    assert "rule_counts" in payload
    assert "data_freshness" in payload
