"""Tests for keel.config: load_config, load_secrets, ConfigError."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.config import Config, ConfigError, load_config, load_secrets

from .conftest import VALID_CONFIG_YAML


def test_load_config_valid_returns_config_with_decimal_weights_summing_to_one(valid_config_path):
    config = load_config(valid_config_path)

    assert isinstance(config, Config)
    assert config.allowlist == ["BTC", "ETH", "PAXG"]
    assert all(isinstance(w, Decimal) for w in config.target_weights.values())
    assert sum(config.target_weights.values()) == Decimal("1")
    assert isinstance(config.risk_pct, Decimal)
    assert config.risk_pct == Decimal("0.01")


def test_load_config_missing_allowlist_raises_configerror_mentioning_allowlist(write_config):
    text = VALID_CONFIG_YAML.replace(
        """allowlist:
  - BTC
  - ETH
  - PAXG""",
        "",
    )
    path = write_config(text)

    with pytest.raises(ConfigError, match="allowlist"):
        load_config(path)


def test_load_config_negative_cap_raises_configerror(write_config):
    text = VALID_CONFIG_YAML.replace(
        "max_per_order_usd: 100", "max_per_order_usd: -100"
    )
    path = write_config(text)

    with pytest.raises(ConfigError, match="max_per_order_usd"):
        load_config(path)


def test_load_config_caps_typed_and_correct(valid_config_path):
    config = load_config(valid_config_path)

    assert config.caps.max_per_order_usd == Decimal("100")
    assert config.caps.max_per_day_usd == Decimal("300")
    assert config.caps.max_exposure_usd == Decimal("5000")
    assert config.caps.max_per_asset_pct == Decimal("0.50")


def test_load_config_subscription_and_quote_currency_defaults(valid_config_path):
    config = load_config(valid_config_path)

    assert config.quote_currency == "USDC"
    assert config.subscription.assumed_free_volume_usd == Decimal("500")
    assert config.subscription.pacing == "opportunistic"


def test_load_config_subscription_and_quote_currency_absent_falls_back_to_defaults(write_config):
    text = VALID_CONFIG_YAML.replace(
        """quote_currency: USDC

subscription:
  assumed_free_volume_usd: 500
  unsubscribed_allowance_usd: 0
  pacing: opportunistic
""",
        "",
    )
    path = write_config(text)

    config = load_config(path)

    assert config.quote_currency == "USDC"
    assert config.subscription.assumed_free_volume_usd == Decimal("500")
    assert config.subscription.pacing == "opportunistic"


def test_load_config_subscription_pacing_even_daily(write_config):
    text = VALID_CONFIG_YAML.replace("pacing: opportunistic", "pacing: even_daily")
    path = write_config(text)

    config = load_config(path)

    assert config.subscription.pacing == "even_daily"


def test_load_config_subscription_invalid_pacing_raises_configerror(write_config):
    text = VALID_CONFIG_YAML.replace("pacing: opportunistic", "pacing: yolo")
    path = write_config(text)

    with pytest.raises(ConfigError, match="subscription.pacing"):
        load_config(path)


def test_assumed_free_volume_usd_parses(write_config) -> None:
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace(
            "assumed_free_volume_usd: 500", "assumed_free_volume_usd: 1234.5"
        )
    )
    config = load_config(path)
    assert config.subscription.assumed_free_volume_usd == Decimal("1234.5")


def test_unsubscribed_allowance_defaults_to_zero(valid_config_path) -> None:
    """Fail-closed: an unattested venue may spend nothing unless the user says otherwise."""
    assert load_config(valid_config_path).subscription.unsubscribed_allowance_usd == Decimal("0")


def test_unsubscribed_allowance_parses(write_config) -> None:
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace(
            "pacing: opportunistic", "pacing: opportunistic\n  unsubscribed_allowance_usd: 25"
        )
    )
    assert load_config(path).subscription.unsubscribed_allowance_usd == Decimal("25")


def test_the_old_monthly_allowance_key_is_rejected(write_config) -> None:
    """Silently ignoring a hand-set spend number is the exact defect this work fixes."""
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace("assumed_free_volume_usd: 500", "monthly_allowance_usd: 500")
    )
    with pytest.raises(ConfigError, match="assumed_free_volume_usd"):
        load_config(path)


def test_the_rejection_message_points_at_attest(write_config) -> None:
    from tests.conftest import VALID_CONFIG_YAML

    path = write_config(
        VALID_CONFIG_YAML.replace("assumed_free_volume_usd: 500", "monthly_allowance_usd: 500")
    )
    with pytest.raises(ConfigError, match="subscription attest"):
        load_config(path)


def test_load_config_quote_currency_empty_raises_configerror(write_config):
    text = VALID_CONFIG_YAML.replace("quote_currency: USDC", "quote_currency: ''")
    path = write_config(text)

    with pytest.raises(ConfigError, match="quote_currency"):
        load_config(path)


def test_load_config_promotion_defaults_are_canonical_proving_floors(write_config):
    """Phase-1 placeholders (30 trades / 40% win) were replaced with the canonical
    proving-gate floors from spec §6.2/§11 (Issue #66): 100 trades / 55% win rate.
    """
    text = VALID_CONFIG_YAML.replace(
        """promotion:
  min_trades: 100
  min_expectancy: 0.0
  min_rr: 1.5
  min_win_rate: 0.55
""",
        "",
    )
    path = write_config(text)

    config = load_config(path)

    assert config.promotion.min_trades == 100
    assert config.promotion.min_win_rate == Decimal("0.55")
    assert config.promotion.min_rr == Decimal("1.5")
    assert config.promotion.min_expectancy == Decimal("0")


def test_load_config_market_data_granularities_and_history_days(valid_config_path):
    from keel.types import Granularity

    config = load_config(valid_config_path)

    assert config.market_data.granularities == [
        Granularity.ONE_DAY,
        Granularity.ONE_HOUR,
        Granularity.FIFTEEN_MINUTE,
    ]
    assert config.market_data.history_days == 365


# -- tiers / fees (Issue #86, Coinbase One tier/fee analysis) ----------------------------------


def test_load_config_tiers_and_fees_default_to_real_coinbase_one_values(valid_config_path):
    """`VALID_CONFIG_YAML` has no `tiers:`/`fees:` block -- absent, they fall back to the real
    Coinbase One tiers/fee schedule, not Phase-1 placeholders."""
    config = load_config(valid_config_path)

    assert [t.name for t in config.tiers] == ["Basic", "Preferred", "Premium"]
    basic, preferred, premium = config.tiers
    assert basic.free_volume_usd == Decimal("500")
    assert basic.subscription_usd_month == Decimal("4.99")
    assert preferred.free_volume_usd == Decimal("10000")
    assert preferred.subscription_usd_month == Decimal("29.99")
    assert premium.free_volume_usd is None  # unlimited
    assert premium.subscription_usd_month == Decimal("299.99")

    assert config.fees.taker_pct == Decimal("0.012")
    assert config.fees.maker_pct == Decimal("0.006")


def test_load_config_tiers_and_fees_overridable(write_config):
    text = (
        VALID_CONFIG_YAML
        + """
tiers:
  - name: Solo
    free_volume_usd: 100
    subscription_usd_month: 1.00
  - name: Unlimited
    free_volume_usd: null
    subscription_usd_month: 50.00

fees:
  taker_pct: 0.02
  maker_pct: 0.01
"""
    )
    path = write_config(text)

    config = load_config(path)

    assert [t.name for t in config.tiers] == ["Solo", "Unlimited"]
    assert config.tiers[0].free_volume_usd == Decimal("100")
    assert config.tiers[1].free_volume_usd is None
    assert config.fees.taker_pct == Decimal("0.02")
    assert config.fees.maker_pct == Decimal("0.01")


def test_load_config_tier_missing_name_raises_configerror(write_config):
    text = (
        VALID_CONFIG_YAML
        + """
tiers:
  - free_volume_usd: 100
    subscription_usd_month: 1.00
"""
    )
    path = write_config(text)

    with pytest.raises(ConfigError, match="tiers"):
        load_config(path)


# -- auto_trade.bypass_arm_ttl_sec (Issue #60, bypass-arm hardening) ---------------------------


def test_load_config_bypass_arm_ttl_sec_default_is_one_hour(valid_config_path):
    config = load_config(valid_config_path)

    assert config.auto_trade.bypass_arm_ttl_sec == 3600


def test_load_config_bypass_arm_ttl_sec_overridable(write_config):
    text = VALID_CONFIG_YAML.replace("bypass_arm_ttl_sec: 3600", "bypass_arm_ttl_sec: 120")
    path = write_config(text)

    config = load_config(path)

    assert config.auto_trade.bypass_arm_ttl_sec == 120


def test_load_config_bypass_arm_ttl_sec_absent_falls_back_to_default(write_config):
    text = VALID_CONFIG_YAML.replace("  bypass_arm_ttl_sec: 3600\n", "")
    path = write_config(text)

    config = load_config(path)

    assert config.auto_trade.bypass_arm_ttl_sec == 3600


# -- logging (engine-activity logging feature) --------------------------------------------------


def test_load_config_logging_defaults(valid_config_path):
    config = load_config(valid_config_path)

    assert config.logging.verbose is False
    assert config.logging.file == "logs/keel.log"
    assert config.logging.max_file_mb == 25
    assert config.logging.file_count == 5


def test_load_config_logging_overridable(write_config):
    text = (
        VALID_CONFIG_YAML
        + """
logging:
  verbose: true
  file: logs/custom.log
  max_file_mb: 10
  file_count: 3
"""
    )
    path = write_config(text)

    config = load_config(path)

    assert config.logging.verbose is True
    assert config.logging.file == "logs/custom.log"
    assert config.logging.max_file_mb == 10
    assert config.logging.file_count == 3


def test_load_config_logging_max_file_mb_zero_raises_configerror(write_config):
    text = VALID_CONFIG_YAML + "\nlogging:\n  max_file_mb: 0\n"
    path = write_config(text)

    with pytest.raises(ConfigError, match="logging.max_file_mb"):
        load_config(path)


def test_load_config_logging_max_file_mb_negative_raises_configerror(write_config):
    text = VALID_CONFIG_YAML + "\nlogging:\n  max_file_mb: -5\n"
    path = write_config(text)

    with pytest.raises(ConfigError, match="logging.max_file_mb"):
        load_config(path)


def test_load_config_logging_file_count_zero_raises_configerror(write_config):
    text = VALID_CONFIG_YAML + "\nlogging:\n  file_count: 0\n"
    path = write_config(text)

    with pytest.raises(ConfigError, match="logging.file_count"):
        load_config(path)


def test_load_config_logging_empty_file_raises_configerror(write_config):
    text = VALID_CONFIG_YAML + "\nlogging:\n  file: ''\n"
    path = write_config(text)

    with pytest.raises(ConfigError, match="logging.file"):
        load_config(path)


def test_load_secrets_missing_env_returns_empty_dict(tmp_path):
    missing_path = tmp_path / "does-not-exist.env"

    assert load_secrets(str(missing_path)) == {}


def test_load_secrets_reads_cdp_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("CDP_API_KEY=my-key\nCDP_API_SECRET=my-secret\n")

    secrets = load_secrets(str(env_path))

    assert secrets == {"api_key": "my-key", "api_secret": "my-secret"}


def test_types_importable_with_correct_field_types():
    from decimal import Decimal as Dec

    from keel.types import Candle, Granularity, Side

    assert Granularity.ONE_MINUTE.value == "ONE_MINUTE"
    assert Granularity.ONE_DAY.value == "ONE_DAY"
    assert Side.BUY.value == "BUY"
    assert Side.SELL.value == "SELL"

    candle = Candle(
        ts=1_700_000_000,
        open=Dec("100.1"),
        high=Dec("101.2"),
        low=Dec("99.9"),
        close=Dec("100.5"),
        volume=Dec("12.3"),
    )
    assert candle.ts == 1_700_000_000
    assert isinstance(candle.open, Dec)
    assert isinstance(candle.close, Dec)

    with pytest.raises(Exception):
        # frozen dataclass: mutation must fail
        candle.close = Dec("1")  # type: ignore[misc]
