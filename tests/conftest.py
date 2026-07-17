"""Shared pytest fixtures for keel tests."""

from __future__ import annotations

from pathlib import Path

import pytest

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
  monthly_allowance_usd: 500
  pacing: opportunistic
"""


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
