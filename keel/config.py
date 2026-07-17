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

from keel.types import Granularity


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


# Issue #85: `max_per_order_usd`/`max_per_day_usd` were Phase-1 PLACEHOLDER guesses ($100/$300),
# never real Coinbase limits -- Coinbase One's only subscription constraint is monthly fee-free
# trading VOLUME (`SubscriptionConfig.monthly_allowance_usd`), not a per-order or per-day $ cap.
# Risk-sized rule orders routinely land in the $400-24k range, so a $100/$300 default silently
# rejected 100% of them. These two fields are now OPTIONAL internal risk knobs -- a user MAY
# still tighten them in `config.yaml` -- but absent an explicit value they default to this
# effectively-unlimited sentinel so they never bind. The real, binding constraints are USDC
# funding (cash on hand), per-asset concentration (`max_per_asset_pct`), and total exposure
# (`max_exposure_usd`) -- `sim.portfolio_sim` CLAMPs a risk-sized order down to whichever of
# those is tightest instead of rejecting the signal outright (see `sim/account.py`'s
# `max_affordable_notional`). The cap-enforcement logic itself (`SimAccount.can_open`,
# `execution.guards.check`) is unchanged -- it still vetoes any order that exceeds whatever
# caps ARE configured; only the shipped defaults stop being a silent, fabricated blocker.
NON_BINDING_CAP_USD = Decimal("1000000000")  # $1B -- not a real limit, just "don't bind"


@dataclass(frozen=True)
class Caps:
    max_exposure_usd: Decimal
    max_per_asset_pct: Decimal
    max_per_order_usd: Decimal = NON_BINDING_CAP_USD
    max_per_day_usd: Decimal = NON_BINDING_CAP_USD


@dataclass(frozen=True)
class MarketDataConfig:
    granularities: list[Granularity]
    history_days: int


@dataclass(frozen=True)
class AutoTradeConfig:
    mode: str = "paper"
    enabled: bool = False
    interval_sec: int = 900
    # Issue #60: how long a `keel arm-bypass` token stays valid (`Repository.arm_bypass`'s
    # `ttl_sec`) before `agent.run_once` refuses `mode="bypass"` again and falls back to
    # `"confirm"`. 3600s (1h) is a sensible default -- long enough for one supervised session,
    # short enough that a forgotten arm doesn't grant unattended autonomy indefinitely.
    bypass_arm_ttl_sec: int = 3600


@dataclass(frozen=True)
class PromotionConfig:
    min_trades: int = 100
    min_expectancy: Decimal = Decimal("0")
    min_rr: Decimal = Decimal("1.5")
    min_win_rate: Decimal = Decimal("0.55")


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


_VALID_PACING_MODES = ("opportunistic", "even_daily")


@dataclass(frozen=True)
class SubscriptionConfig:
    """Rail 14 (monthly subscription-allowance) settings — see `execution/guards.py`.

    `pacing="opportunistic"` (default) only enforces the flat monthly cap. `pacing="even_daily"`
    additionally caps cumulative month-to-date spend to
    `monthly_allowance_usd / business_days_in_month * business_days_elapsed`, so the allowance
    can't be blown in one burst early in the month.
    """

    monthly_allowance_usd: Decimal = Decimal("500")
    pacing: str = "opportunistic"


@dataclass(frozen=True)
class TierConfig:
    """One Coinbase One subscription tier (Issue #86) -- `sim.tiers`/`keel simulate`'s tier/fee
    analysis matrix compares staying WITHIN a tier's fee-free monthly trading-volume allowance
    against trading freely and paying the taker fee on volume EXCEEDING it.

    `free_volume_usd is None` means an UNLIMITED fee-free allowance (Premium) -- there is no
    volume beyond which fees apply, so within-cap and over-cap analysis collapse to the same
    (always fee-free) result for that tier.
    """

    name: str
    free_volume_usd: Decimal | None
    subscription_usd_month: Decimal


@dataclass(frozen=True)
class FeesConfig:
    """Coinbase Advanced trading fees applied to volume beyond a tier's free allowance
    (Issue #86) -- the `<$1k-30d-volume` account tier's published rate. `taker_pct` is the sim's
    default fee-schedule rate (fills are modeled market-style, at next-bar open); `maker_pct` is
    exposed for a caller that wants to model limit-order fills instead."""

    taker_pct: Decimal = Decimal("0.012")
    maker_pct: Decimal = Decimal("0.006")


def _default_tiers() -> tuple[TierConfig, ...]:
    """Real Coinbase One tiers (Issue #86) -- see `TierConfig`'s docstring for `free_volume_usd
    is None` ("Premium" is unlimited)."""
    return (
        TierConfig(
            name="Basic", free_volume_usd=Decimal("500"), subscription_usd_month=Decimal("4.99")
        ),
        TierConfig(
            name="Preferred",
            free_volume_usd=Decimal("10000"),
            subscription_usd_month=Decimal("29.99"),
        ),
        TierConfig(
            name="Premium", free_volume_usd=None, subscription_usd_month=Decimal("299.99")
        ),
    )


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
    subscription: SubscriptionConfig = field(default_factory=SubscriptionConfig)
    tiers: tuple[TierConfig, ...] = field(default_factory=_default_tiers)
    fees: FeesConfig = field(default_factory=FeesConfig)
    quote_currency: str = "USDC"


# Only the real, binding caps are required; `max_per_order_usd`/`max_per_day_usd` are optional
# internal risk knobs (see `NON_BINDING_CAP_USD` above) and fall back to a non-binding default
# when absent from `caps:` -- they're never silently REQUIRED, but if present they're still
# validated/enforced like any other cap.
_REQUIRED_CAP_KEYS = (
    "max_exposure_usd",
    "max_per_asset_pct",
)
_OPTIONAL_CAP_KEYS = (
    "max_per_order_usd",
    "max_per_day_usd",
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
        raise ConfigError(
            "caps: missing; must define " + ", ".join(_REQUIRED_CAP_KEYS + _OPTIONAL_CAP_KEYS)
        )
    for key in _REQUIRED_CAP_KEYS:
        if key not in caps_raw:
            raise ConfigError(f"caps.{key}: missing")
    return Caps(
        max_exposure_usd=_non_negative_decimal(
            caps_raw["max_exposure_usd"], "caps.max_exposure_usd"
        ),
        max_per_asset_pct=_non_negative_decimal(
            caps_raw["max_per_asset_pct"], "caps.max_per_asset_pct"
        ),
        max_per_order_usd=_non_negative_decimal(
            caps_raw.get("max_per_order_usd", NON_BINDING_CAP_USD), "caps.max_per_order_usd"
        ),
        max_per_day_usd=_non_negative_decimal(
            caps_raw.get("max_per_day_usd", NON_BINDING_CAP_USD), "caps.max_per_day_usd"
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


def _parse_tiers(raw: dict[str, Any]) -> tuple[TierConfig, ...]:
    """Parse `tiers:` (Issue #86) -- optional, like every other pass-through block; absent or
    empty falls back to `_default_tiers()`'s real Coinbase One defaults."""
    tiers_raw = raw.get("tiers")
    if not tiers_raw:
        return _default_tiers()
    if not isinstance(tiers_raw, list):
        raise ConfigError(
            "tiers: must be a list of {name, free_volume_usd, subscription_usd_month}"
        )

    tiers: list[TierConfig] = []
    for i, entry in enumerate(tiers_raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"tiers[{i}]: must be a mapping")
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise ConfigError(f"tiers[{i}].name: missing or invalid")
        free_volume_raw = entry.get("free_volume_usd")
        free_volume_usd = (
            None
            if free_volume_raw is None
            else _non_negative_decimal(free_volume_raw, f"tiers[{i}].free_volume_usd")
        )
        subscription_usd_month = _non_negative_decimal(
            entry.get("subscription_usd_month", "0"), f"tiers[{i}].subscription_usd_month"
        )
        tiers.append(
            TierConfig(
                name=name,
                free_volume_usd=free_volume_usd,
                subscription_usd_month=subscription_usd_month,
            )
        )
    return tuple(tiers)


def _parse_fees(raw: dict[str, Any]) -> FeesConfig:
    """Parse `fees:` (Issue #86) -- optional, falls back to the real Coinbase Advanced
    `<$1k-30d-volume` rate (`FeesConfig`'s defaults) when absent."""
    fees_raw = raw.get("fees") or {}
    return FeesConfig(
        taker_pct=_non_negative_decimal(fees_raw.get("taker_pct", "0.012"), "fees.taker_pct"),
        maker_pct=_non_negative_decimal(fees_raw.get("maker_pct", "0.006"), "fees.maker_pct"),
    )


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
    subscription_raw = raw.get("subscription") or {}

    pacing = subscription_raw.get("pacing", "opportunistic")
    if pacing not in _VALID_PACING_MODES:
        raise ConfigError(
            f"subscription.pacing: invalid value {pacing!r}; must be one of "
            f"{_VALID_PACING_MODES!r}"
        )

    quote_currency = raw.get("quote_currency", "USDC")
    if not isinstance(quote_currency, str) or not quote_currency:
        raise ConfigError(f"quote_currency: must be a non-empty string, got {quote_currency!r}")

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
            bypass_arm_ttl_sec=int(auto_trade_raw.get("bypass_arm_ttl_sec", 3600)),
        ),
        promotion=PromotionConfig(
            min_trades=int(promotion_raw.get("min_trades", 100)),
            min_expectancy=_to_decimal(
                promotion_raw.get("min_expectancy", "0"), "promotion.min_expectancy"
            ),
            min_rr=_to_decimal(promotion_raw.get("min_rr", "1.5"), "promotion.min_rr"),
            min_win_rate=_to_decimal(
                promotion_raw.get("min_win_rate", "0.55"), "promotion.min_win_rate"
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
        subscription=SubscriptionConfig(
            monthly_allowance_usd=_non_negative_decimal(
                subscription_raw.get("monthly_allowance_usd", "500"),
                "subscription.monthly_allowance_usd",
            ),
            pacing=pacing,
        ),
        tiers=_parse_tiers(raw),
        fees=_parse_fees(raw),
        quote_currency=quote_currency,
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
