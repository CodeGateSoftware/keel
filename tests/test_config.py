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
    assert config.subscription.monthly_allowance_usd == Decimal("500")
    assert config.subscription.pacing == "opportunistic"


def test_load_config_subscription_and_quote_currency_absent_falls_back_to_defaults(write_config):
    text = VALID_CONFIG_YAML.replace(
        """quote_currency: USDC

subscription:
  monthly_allowance_usd: 500
  pacing: opportunistic
""",
        "",
    )
    path = write_config(text)

    config = load_config(path)

    assert config.quote_currency == "USDC"
    assert config.subscription.monthly_allowance_usd == Decimal("500")
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


def test_load_config_quote_currency_empty_raises_configerror(write_config):
    text = VALID_CONFIG_YAML.replace("quote_currency: USDC", "quote_currency: ''")
    path = write_config(text)

    with pytest.raises(ConfigError, match="quote_currency"):
        load_config(path)


def test_load_config_market_data_granularities_and_history_days(valid_config_path):
    from keel.types import Granularity

    config = load_config(valid_config_path)

    assert config.market_data.granularities == [
        Granularity.ONE_DAY,
        Granularity.ONE_HOUR,
        Granularity.FIFTEEN_MINUTE,
    ]
    assert config.market_data.history_days == 365


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
