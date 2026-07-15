"""Load and validate `config.yaml`; load CDP secrets from a git-ignored `.env`.

`allowlist` and `caps` are required and validated explicitly: missing or invalid values raise
`ConfigError` naming the offending key, and are never silently defaulted. Other blocks
(`auto_trade`, `promotion`, `money_mgmt`, `dca`) are pass-through typed dataclasses with
Phase-1-safe defaults — unused fields are fine per the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from halal_cb.types import Granularity


class ConfigError(Exception):
    """Raised when `config.yaml` is missing or has an invalid value for a required key."""


def _to_decimal(value: Any, key: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConfigError(f"{key}: expected a number, got {value!r}") from exc


def _non_negative_decimal(value: Any, key: str) -> Decimal:
    amount = _to_decimal(value, key)
    if amount < 0:
        raise ConfigError(f"{key}: must be a non-negative number, got {value!r}")
    return amount


@dataclass(frozen=True)
class Caps:
    max_per_order_usd: Decimal
    max_per_day_usd: Decimal
    max_exposure_usd: Decimal
    max_per_asset_pct: Decimal


@dataclass(frozen=True)
class MarketDataConfig:
    granularities: list[Granularity]
    history_days: int


@dataclass(frozen=True)
class AutoTradeConfig:
    mode: str = "paper"
    enabled: bool = False
    interval_sec: int = 900


@dataclass(frozen=True)
class PromotionConfig:
    min_trades: int = 30
    min_expectancy: Decimal = Decimal("0")
    min_rr: Decimal = Decimal("1.5")
    min_win_rate: Decimal = Decimal("0.4")


@dataclass(frozen=True)
class MoneyMgmtConfig:
    profit_trigger_pct: Decimal = Decimal("0.1")
    acceleration_pct: Decimal = Decimal("0.05")
    max_total_dd_pct: Decimal = Decimal("0.2")
    max_weekly_dd_pct: Decimal = Decimal("0.08")


@dataclass(frozen=True)
class DcaConfig:
    budget_usd: Decimal = Decimal("0")
    cadence_days: int = 7


@dataclass(frozen=True)
class Config:
    allowlist: list[str]
    target_weights: dict[str, Decimal]
    risk_pct: Decimal
    caps: Caps
    market_data: MarketDataConfig
    auto_trade: AutoTradeConfig = field(default_factory=AutoTradeConfig)
    promotion: PromotionConfig = field(default_factory=PromotionConfig)
    money_mgmt: MoneyMgmtConfig = field(default_factory=MoneyMgmtConfig)
    dca: DcaConfig = field(default_factory=DcaConfig)


_REQUIRED_CAP_KEYS = (
    "max_per_order_usd",
    "max_per_day_usd",
    "max_exposure_usd",
    "max_per_asset_pct",
)


def _parse_allowlist(raw: dict[str, Any]) -> list[str]:
    allowlist = raw.get("allowlist")
    if not allowlist or not isinstance(allowlist, list):
        raise ConfigError("allowlist: missing or empty; must be a non-empty list of asset codes")
    for entry in allowlist:
        if not isinstance(entry, str) or not entry:
            raise ConfigError(f"allowlist: invalid entry {entry!r}; must be non-empty strings")
    return list(allowlist)


def _parse_caps(raw: dict[str, Any]) -> Caps:
    caps_raw = raw.get("caps")
    if not caps_raw or not isinstance(caps_raw, dict):
        raise ConfigError("caps: missing; must define " + ", ".join(_REQUIRED_CAP_KEYS))
    for key in _REQUIRED_CAP_KEYS:
        if key not in caps_raw:
            raise ConfigError(f"caps.{key}: missing")
    return Caps(
        max_per_order_usd=_non_negative_decimal(
            caps_raw["max_per_order_usd"], "caps.max_per_order_usd"
        ),
        max_per_day_usd=_non_negative_decimal(caps_raw["max_per_day_usd"], "caps.max_per_day_usd"),
        max_exposure_usd=_non_negative_decimal(
            caps_raw["max_exposure_usd"], "caps.max_exposure_usd"
        ),
        max_per_asset_pct=_non_negative_decimal(
            caps_raw["max_per_asset_pct"], "caps.max_per_asset_pct"
        ),
    )


def _parse_target_weights(raw: dict[str, Any]) -> dict[str, Decimal]:
    weights_raw = raw.get("target_weights") or {}
    return {
        asset: _to_decimal(weight, f"target_weights.{asset}")
        for asset, weight in weights_raw.items()
    }


def _parse_market_data(raw: dict[str, Any]) -> MarketDataConfig:
    md = raw.get("market_data") or {}
    granularities_raw = md.get("granularities") or []
    try:
        granularities = [Granularity(g) for g in granularities_raw]
    except ValueError as exc:
        raise ConfigError(f"market_data.granularities: invalid value ({exc})") from exc
    history_days = md.get("history_days", 365)
    try:
        history_days = int(history_days)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"market_data.history_days: expected an integer, got {history_days!r}"
        ) from exc
    return MarketDataConfig(granularities=granularities, history_days=history_days)


def load_config(path: str | Path) -> Config:
    """Parse and validate `config.yaml` at `path`, returning a typed `Config`.

    Raises `ConfigError("<key>: <reason>")` for missing/invalid `allowlist` or `caps` — these
    are never silently defaulted. Other blocks fall back to Phase-1-safe defaults if absent.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config: file not found at {path}")

    with path.open() as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ConfigError("config: root must be a mapping")

    allowlist = _parse_allowlist(raw)
    caps = _parse_caps(raw)
    target_weights = _parse_target_weights(raw)
    risk_pct = _to_decimal(raw.get("risk_pct", "0.01"), "risk_pct")
    market_data = _parse_market_data(raw)

    auto_trade_raw = raw.get("auto_trade") or {}
    promotion_raw = raw.get("promotion") or {}
    money_mgmt_raw = raw.get("money_mgmt") or {}
    dca_raw = raw.get("dca") or {}

    return Config(
        allowlist=allowlist,
        target_weights=target_weights,
        risk_pct=risk_pct,
        caps=caps,
        market_data=market_data,
        auto_trade=AutoTradeConfig(
            mode=auto_trade_raw.get("mode", "paper"),
            enabled=bool(auto_trade_raw.get("enabled", False)),
            interval_sec=int(auto_trade_raw.get("interval_sec", 900)),
        ),
        promotion=PromotionConfig(
            min_trades=int(promotion_raw.get("min_trades", 30)),
            min_expectancy=_to_decimal(
                promotion_raw.get("min_expectancy", "0"), "promotion.min_expectancy"
            ),
            min_rr=_to_decimal(promotion_raw.get("min_rr", "1.5"), "promotion.min_rr"),
            min_win_rate=_to_decimal(
                promotion_raw.get("min_win_rate", "0.4"), "promotion.min_win_rate"
            ),
        ),
        money_mgmt=MoneyMgmtConfig(
            profit_trigger_pct=_to_decimal(
                money_mgmt_raw.get("profit_trigger_pct", "0.1"), "money_mgmt.profit_trigger_pct"
            ),
            acceleration_pct=_to_decimal(
                money_mgmt_raw.get("acceleration_pct", "0.05"), "money_mgmt.acceleration_pct"
            ),
            max_total_dd_pct=_to_decimal(
                money_mgmt_raw.get("max_total_dd_pct", "0.2"), "money_mgmt.max_total_dd_pct"
            ),
            max_weekly_dd_pct=_to_decimal(
                money_mgmt_raw.get("max_weekly_dd_pct", "0.08"), "money_mgmt.max_weekly_dd_pct"
            ),
        ),
        dca=DcaConfig(
            budget_usd=_to_decimal(dca_raw.get("budget_usd", "0"), "dca.budget_usd"),
            cadence_days=int(dca_raw.get("cadence_days", 7)),
        ),
    )


def load_secrets(env_path: str | Path = ".env") -> dict:
    """Load CDP API credentials from a git-ignored `.env` file.

    Returns `{"api_key": ..., "api_secret": ...}` when both are present, `{}` when the file is
    absent or empty so offline commands keep working without secrets configured.
    """
    env_path = Path(env_path)
    if not env_path.exists():
        return {}

    values = dotenv_values(env_path)
    api_key = values.get("CDP_API_KEY")
    api_secret = values.get("CDP_API_SECRET")
    if not api_key and not api_secret:
        return {}
    return {"api_key": api_key, "api_secret": api_secret}
