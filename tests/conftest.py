"""Shared pytest fixtures for keel tests."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from keel_core import telemetry
from keel_core.subscription import BrokerSubscription, SubscriptionStatus

if TYPE_CHECKING:
    from keel.data.repository import Repository

ATTESTATION_PERIOD_SEC = 365 * 24 * 3600


def attest_subscription(
    repo: Repository,
    *,
    now_ts: int,
    free_volume_usd: Decimal | None,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    pacing: str = "opportunistic",
    attest_due_ts: int | None = None,
    venue: str = "coinbase",
    tier_name: str = "Preferred",
) -> None:
    """Write an attested subscription record -- the single source of truth for its shape.

    Three test modules each built this record inline with near-identical bodies, so any change
    to `BrokerSubscription`'s fields had to land in three places or the copies drifted apart
    silently. Their `_attest` wrappers survive (each module's call convention differs) but now
    delegate here rather than re-constructing the record.

    `now_ts` is required rather than defaulted: each caller module defines its own `NOW_TS`,
    and a default here would silently bind the wrong clock into one of them.
    """
    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue=venue,
            tier_name=tier_name,
            free_volume_usd=free_volume_usd,
            pacing=pacing,
            subscription_usd_month=Decimal("29.99"),
            status=status,
            attested_at=now_ts,
            attest_due_ts=(
                attest_due_ts if attest_due_ts is not None else now_ts + ATTESTATION_PERIOD_SEC
            ),
        )
    )

VALID_CONFIG_YAML = """
allowlist:
  - BTC
  - ETH
  - PAXG

target_weights:
  BTC: 0.40
  ETH: 0.30
  PAXG: 0.30

risk_pct: 0.01

caps:
  max_per_order_usd: 100
  max_per_day_usd: 300
  max_exposure_usd: 5000
  max_per_asset_pct: 0.50

market_data:
  granularities:
    - ONE_DAY
    - ONE_HOUR
    - FIFTEEN_MINUTE
  history_days: 365

auto_trade:
  mode: paper
  enabled: false
  interval_sec: 900
  bypass_arm_ttl_sec: 3600

promotion:
  min_trades: 100
  min_expectancy: 0.0
  min_rr: 1.5
  min_win_rate: 0.55

money_mgmt:
  profit_trigger_pct: 0.10
  acceleration_pct: 0.05
  max_total_dd_pct: 0.20
  max_weekly_dd_pct: 0.08

dca:
  budget_usd: 50
  cadence_days: 7

quote_currency: USDC

subscription:
  assumed_free_volume_usd: 500
  unsubscribed_allowance_usd: 0
  pacing: opportunistic
"""


@pytest.fixture(autouse=True)
def _isolate_telemetry_context():
    """Clear the telemetry ContextVars between tests.

    `venue` and `cycle_id` are process-lifetime bindings by design -- the CLI binds venue once
    in `_load_cfg` and never unbinds, which is right for a real process but leaks across tests
    sharing one interpreter. Without this, whether `test_venue_absent_when_unbound` passes
    depends on whether a CLI test ran first, which is exactly the kind of order-dependence that
    makes a suite untrustworthy.
    """
    yield
    telemetry.bind_venue(None)
    telemetry.bind_cycle(None)


@pytest.fixture
def write_config(tmp_path: Path):
    """Factory fixture: write arbitrary YAML text to a temp config.yaml, return its path."""

    def _write(text: str) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(text)
        return path

    return _write


@pytest.fixture
def valid_config_path(write_config) -> Path:
    return write_config(VALID_CONFIG_YAML)


@pytest.fixture(autouse=True)
def _isolate_trials_ledger(tmp_path, monkeypatch):
    """Never let a test write to the REAL trials ledger.

    `docs/experiments/trials-ledger.jsonl` is a git-tracked, append-only, hash-chained audit
    record. A test run that appends to it corrupts a research artifact with fixture noise --
    which is exactly what happened once before this fixture existed: the `simulate` CLI tests
    silently wrote six junk rows into the committed ledger. Autouse, so no future test has to
    remember.
    """
    monkeypatch.setattr(
        "keel.research.ledger.DEFAULT_LEDGER_PATH", tmp_path / "trials-ledger.jsonl"
    )


@pytest.fixture(autouse=True)
def _restore_keel_logger():
    """Undo `configure_logging`'s global mutation of the "keel" logger after every test.

    `keel_core.logging_setup.configure_logging` sets `propagate = False` and (with the default
    `verbose: false`) level `ERROR` on `logging.getLogger("keel")`. Any test that invokes the
    CLI therefore leaks that state into every later test: `caplog` attaches its handler to the
    ROOT logger, so `propagate = False` silently starves it and `caplog.text` comes back empty.

    That is a latent ordering bug, not a theoretical one -- it stayed hidden only because the
    existing CLI tests live in `tests/test_cli.py`, which sorts AFTER `tests/execution/`.
    Adding CLI tests under `tests/data/` (which sorts before) broke two equity/reconcile
    assertions that had nothing to do with the change. Restore the logger instead of relying
    on collection order.
    """
    logger = logging.getLogger("keel")
    level, propagate, handlers = logger.level, logger.propagate, list(logger.handlers)
    try:
        yield
    finally:
        logger.setLevel(level)
        logger.propagate = propagate
        logger.handlers[:] = handlers
