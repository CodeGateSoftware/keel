"""Load and validate `config.yaml`; load CDP secrets from a git-ignored `.env`.

`allowlist` and `caps` are required and validated explicitly: missing or invalid values raise
`ConfigError` naming the offending key, and are never silently defaulted. Other blocks
(`auto_trade`, `promotion`, `money_mgmt`, `dca`) are pass-through typed dataclasses with
Phase-1-safe defaults — unused fields are fine per the plan.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from keel_core.products import is_spot_base_code
from keel_core.types import Granularity


class ConfigError(Exception):
    """Raised when `config.yaml` is missing or has an invalid value for a required key."""


def _to_decimal(value: Any, key: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConfigError(f"{key}: expected a number, got {value!r}") from exc


def _non_negative_decimal(value: Any, key: str) -> Decimal:
    amount = _to_decimal(value, key)
    # `Decimal.is_finite()` is False for inf/-inf/nan. Reject before the `< 0` comparison below:
    # a NaN comparison raises InvalidOperation (uncaught by any handler here), and `.inf` would
    # otherwise pass this check and become an unbounded cap wherever this field is used as one
    # (e.g. `subscription.unsubscribed_allowance_usd`) -- "unlimited" is expressed elsewhere in
    # this system as `None` (see `TierConfig.free_volume_usd`), never as `Infinity`.
    if not amount.is_finite():
        raise ConfigError(f"{key}: must be a finite number, got {value!r}")
    if amount < 0:
        raise ConfigError(f"{key}: must be a non-negative number, got {value!r}")
    return amount


def _non_negative_int(value: Any, key: str) -> int:
    """Parse a non-negative integer config value, rejecting bools and negatives.

    `isinstance(True, int)` is True in Python, so a bare int() would silently accept `yes`/`true`
    from YAML as 1 -- for a rail threshold that would turn a typo into a live setting.
    """
    if isinstance(value, bool):
        raise ConfigError(f"{key}: must be an integer, got a boolean")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key}: must be an integer, got {value!r}") from exc
    if parsed < 0:
        raise ConfigError(f"{key}: must be non-negative, got {parsed}")
    return parsed


# Issue #85: `max_per_order_usd`/`max_per_day_usd` were Phase-1 PLACEHOLDER guesses ($100/$300),
# never real Coinbase limits -- Coinbase One's only subscription constraint is monthly fee-free
# trading VOLUME (`SubscriptionConfig.assumed_free_volume_usd`), not a per-order or per-day $ cap.
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

# Rail 18 (settlement-currency): the settlement legs an order is allowed to spend/receive.
# Defaults to exactly what the Coinbase adapter declares it settles in
# (`keel_broker_coinbase.adapter._CAPABILITIES.quote_currencies`), so the shipped default and the
# venue's own statement agree without either being derived from the other. It is a FIELD rather
# than a constant in `guards.py` so an operator whose venue settles elsewhere has an escape
# hatch that is not a code edit -- see `Config.settlement_currencies`.
DEFAULT_SETTLEMENT_CURRENCIES = frozenset({"USD", "USDC"})

# The shape a settlement/quote currency code may take, applied after case-folding. Deliberately
# loose about WHICH codes exist (keel does not carry an ISO-4217 table, and venue codes like
# `USDC` are not in one anyway) and strict only about the shape a code can possibly have: rail 18
# compares these against `quote_currency_of`'s output, which is a single dash-delimited token, so
# anything with a space or punctuation in it is a typo that would sit in the set admitting
# nothing. 2 chars minimum because no real code is one letter; 10 maximum to catch a sentence
# pasted into the list.
_CURRENCY_CODE_RE = re.compile(r"[A-Z0-9]{2,10}")


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
    # Rail 16 (consecutive-loss breaker). SHIPS DISABLED: 0 means off.
    #
    # A live default would be actively harmful here. `turtle_breakout`'s own simulation shows a
    # max losing streak of 5, so the source's suggested threshold of 3 would have fired repeatedly
    # on a strategy that was working. The threshold must sit ABOVE the strategy's tested max streak
    # (`strategy/stats.py:max_losing_streak`) or the breaker fires on normal variance and stands the
    # system down during exactly the runs it was designed to survive. Set it from a sweep.
    max_consecutive_losses: int = 0
    streak_cooloff_days: int = 0


@dataclass(frozen=True)
class DcaConfig:
    budget_usd: Decimal = Decimal("0")
    cadence_days: int = 7


@dataclass(frozen=True)
class PaperConfig:
    """Paper-forward account model (spec: paper-mode fidelity).

    `starting_equity_usd` sets the one-time seed for `paper_cash_usdc` at paper-start:
    - `> 0`: a deliberate FUNDING OVERRIDE -- seed at exactly this amount (a funded
      paper-forward rehearsal), taking precedence over real broker mark-to-market equity, which
      is not read for the seed in this case.
    - `0` (the default): seed from real broker mark-to-market equity instead; if that read
      fails, paper drawdown tracking stays dormant that run (logged loudly) rather than seeding
      a bogus 0 denominator.
    `monthly_contribution_usd` models ongoing deposits during the paper-forward; 0 disables.
    """

    starting_equity_usd: Decimal = Decimal("0")
    monthly_contribution_usd: Decimal = Decimal("0")


_VALID_PACING_MODES = ("opportunistic", "even_daily")
#: `paper` simulates and places nothing; `confirm` is live. Whether you are ASKED is a
#: PROFILE choice (`keel autonomy on|off`), deliberately not a config mode -- config.yaml
#: ships as a release asset, and arming unattended trading should not be a YAML edit.
_VALID_AUTO_TRADE_MODES = ("paper", "confirm")


@dataclass(frozen=True)
class SubscriptionConfig:
    """Subscription-related settings. Three fields, three distinct roles — do not conflate them.

    `assumed_free_volume_usd` is the **simulator's** assumed fee-free monthly volume
    (`sim/account.py`'s `_monthly_allowance_cap`). It is a pinned, reproducible assumption for
    backtests. It is NOT the live cap: rail 14 derives that from the attested
    `broker_subscriptions` record, so that upgrading a tier changes one place. This field was
    called `monthly_allowance_usd` when it meant both, which is exactly the defect the
    subscription design spec §2 describes.

    `unsubscribed_allowance_usd` is what rail 14 permits on a venue whose subscription is
    unattested, suspect, lapsed, or overdue. The default `0` stops trading on that venue. A user
    content to pay fees may raise it deliberately — the point is that continuing to spend is an
    explicit choice rather than the consequence of a stale row.

    `pacing="opportunistic"` (default) enforces only the flat monthly cap. `pacing="even_daily"`
    additionally caps cumulative month-to-date spend to
    `allowance / business_days_in_month * business_days_elapsed`, so the allowance cannot be
    blown in one burst early in the month. It is the default pacing for new attestations, and
    the simulator reads it directly.
    """

    assumed_free_volume_usd: Decimal = Decimal("500")
    unsubscribed_allowance_usd: Decimal = Decimal("0")
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
class LoggingConfig:
    """Engine-activity logging settings.

    `verbose=False` (the default) means only errors/exceptions are ever logged --
    `logging_setup.configure_logging` sets the `"keel"` logger to `ERROR` level in that case, so
    major-operation/decision `logger.info(...)` calls throughout the codebase are suppressed
    while `logger.error`/`logger.exception` still always get through. `max_file_mb`/`file_count`
    size the rotating file handler: `file_count` is the TOTAL number of files kept (the active
    log plus its rotated backups), so `configure_logging` passes `backupCount=file_count - 1`.
    """

    verbose: bool = False
    file: str = "logs/keel.log"
    max_file_mb: int = 25
    file_count: int = 5


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
class ResearchConfig:
    """Thresholds for the G4 overfitting gate (spec §7, KB §78).

    ⛔ NEVER TUNE THESE TO OBTAIN A DESIRED VERDICT. Tuning an overfitting threshold until a
    strategy passes is precisely the Strathern misuse the gate exists to prevent (§78.7):
    "when a measure becomes a target, it ceases to be a good measure."

    `slope_floor` is calibrated from §78.8's worked cases -- real strategy -0.35, pure random
    walk -0.61, overfit real strategy -0.75 -- so -0.5 sits between the real-strategy case and
    the noise/overfit cases. It is NEGATIVE, so it must not be validated as non-negative.
    """

    pbo_max: Decimal = Decimal("0.05")
    slope_floor: Decimal = Decimal("-0.5")


@dataclass(frozen=True)
class ExecutionConfig:
    """Live-execution routing-time guardrails (Issue #350).

    `max_entry_spread_pct` is the threshold of the routing-time max-spread entry gate: a live
    BUY whose previewed book shows `(best_ask - best_bid) / mid` at or above it is REFUSED
    before the confirm gate and placement, so a thin book cannot be entered at a moment its
    spread alone makes the fill economics materially worse than the cost model assumes.
    SELLs are never gated (exits must execute), and paper mode never runs the gate (paper
    fills are synthetic and see no book -- which is why the paper-hourly profile accrues no
    evidence about it, and the gate ships before any live resumption rather than after).

    The default 0.005 (50bp) is anchored to #334's `strategy/backtest.SLIPPAGE_CAP_PCT`: the
    backtest never assumes more than 50bp of per-leg slippage on even the thinnest book, so a
    spread AT the cap has already consumed the model's entire worst-case cost, leaving the
    taker fee wholly outside it. Validated to (0, 0.10] at load: 0 would silently disarm the
    gate, and anything past 10% is not a threshold a thin-book guardrail could meaningfully
    have crossed by accident.
    """

    max_entry_spread_pct: Decimal = Decimal("0.005")


@dataclass(frozen=True)
class BrokerConfig:
    """Which broker adapter this deployment talks to (issue #370 Phase B2, venue selection).

    The section is OPTIONAL, and its absence means Coinbase -- not as a fallback the engine
    guesses at, but as the statement of how keel has always been built: `_build_broker`'s
    Coinbase construction predates the section and stays byte-identical when `name` is
    `"coinbase"`, so every shipped config and test that omits `broker:` behaves exactly as
    before. A config that NAMES a venue routes through the `keel.brokers` entry points
    (`keel_broker_api.registry.load_broker`), so installing an adapter is a package install,
    not a core change.

    `endpoint` ("paper" | "live") and `data_feed` ("iex" | "sip") are validated HERE even
    though only the Alpaca wiring consumes them today, because they are declared properties
    of the ADAPTER (FR-11's paper/live host posture, FR-5's data tier), not request-time
    details: a typo in either should fail at config load, not at the first network call --
    the same load-time posture the Alpaca adapter itself enforces on its constructor
    arguments. They are Alpaca's vocabulary; a config that sets them alongside a venue whose
    wiring has no such knob (Coinbase) is refused rather than silently ignored.
    """

    name: str = "coinbase"
    endpoint: str = "paper"
    data_feed: str = "iex"


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
    paper: PaperConfig = field(default_factory=PaperConfig)
    subscription: SubscriptionConfig = field(default_factory=SubscriptionConfig)
    tiers: tuple[TierConfig, ...] = field(default_factory=_default_tiers)
    fees: FeesConfig = field(default_factory=FeesConfig)
    quote_currency: str = "USD"
    # Where `keel assets propose`/the TUI's propose overlay look for an externally-produced
    # shortlist JSON (see `keel/commands/admission.py::latest_shortlist`). This is a FIELD, not a
    # constant baked into that module, for the same reason `settlement_currencies` is a field
    # rather than a `guards.py` literal: a user-specific absolute path hardcoded into library code
    # is untestable (every test would either write to it or monkeypatch it) and wrong for anyone
    # else's machine. `~` is expanded by the READER (`Path(config.proposals_dir).expanduser()`)
    # at USE time, never here at parse time -- expanding at parse would bake the parsing
    # machine's home directory into a `Config` that a test fixture or a different operator's
    # checkout might reasonably load, which is exactly the kind of environment-dependent value
    # `load_config` is supposed to keep out of a `Config` object. Keeping it as the literal string
    # `"~/keel/proposals"` is what makes the default portable across machines and safe to assert
    # on directly in a test (see `test_load_config_proposals_dir_defaults_to_keel_proposals`).
    proposals_dir: str = "~/keel/proposals"
    # Rail 18's allowed settlement legs -- the currencies an order may settle in, matched against
    # `quote_currency_of(product_id)`. NOT the same field as `quote_currency` above, which names
    # the ONE currency this deployment trades in (it screens candidates and excludes the
    # settlement balance from `keel assets holdings`); this is the SET a product's own quote leg
    # must belong to for the order to be admitted at all. A frozenset, so it is a safe dataclass
    # default without a factory and cannot be mutated out from under a rail.
    settlement_currencies: frozenset[str] = DEFAULT_SETTLEMENT_CURRENCIES
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    # Which broker adapter this deployment talks to. Defaulted (not required) so the
    # pre-existing configs that omit the section parse to the same Coinbase deployment they
    # always were -- see `BrokerConfig` for why the default is a statement, not a guess.
    broker: BrokerConfig = field(default_factory=BrokerConfig)


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


def _parse_auto_trade_mode(raw: dict[str, Any]) -> str:
    """`auto_trade.mode`, validated against `_VALID_AUTO_TRADE_MODES`.

    Previously any unknown value silently degraded to confirm. It now raises: a config saying
    `mode: bypass` was explicitly asking for autonomy, and autonomy has since become a profile
    choice. Reinterpreting that request quietly -- in either direction -- is worse than stopping.
    """
    mode = raw.get("mode", "paper")
    if mode not in _VALID_AUTO_TRADE_MODES:
        raise ConfigError(
            f"auto_trade.mode: invalid value {mode!r}; must be one of "
            f"{_VALID_AUTO_TRADE_MODES!r}. Autonomy is no longer a mode -- it is a profile "
            f"choice: use `mode: confirm` plus `keel autonomy on`."
        )
    return str(mode)


def _parse_allowlist(raw: dict[str, Any]) -> list[str]:
    """`allowlist:` -- the BASE legs every product id keel constructs is built from.

    Entries are shape-checked against `is_spot_base_code`, the base-leg half of the spot grammar
    rail 19 gates on, for the reason `_parse_settlement_currencies` shape-checks currency codes
    and the reason `quote_currency` is cross-checked against `settlement_currencies`: an entry
    this function admits and the rails then veto is a **silent, unfixable rejection**. An
    allowlist asset only ever reaches the venue through `_history_product`'s
    `f"{asset}-{quote_currency}"`, so `allowlist: [btc]` derives `btc-USD` -- which rail 19
    vetoes on every cycle and rail 1 never even reaches. The operator's first signal would be a
    veto on an asset they believed they had just enabled.

    ⚠️ **REJECTED, never uppercased** -- deliberately the opposite of `settlement_currencies`
    beside it, and the difference is what the value is FOR. A settlement code is *compared*
    against `quote_currency_of`'s already-uppercased output, so folding it makes the two sides
    agree by construction and changes nothing an operator can observe. An allowlist entry is
    *concatenated* into a venue identifier. Folding it would mean the asset keel goes on to
    trade is not the asset the file names, which is the same judgement
    `commands._products.validate_product_ids` makes about a typed `--products` id: a venue
    identifier is not free text, and guessing at one is how a typo becomes a position. The
    message carries the uppercase form, so the fix costs the operator one keystroke and stays
    theirs. (Blast radius nil: every shipped config -- live, paper-forward, sandbox, local --
    already lists uppercase tickers.)

    Reports the FIRST bad entry rather than all of them, matching the other `_parse_*` helpers
    here; the whole-list report belongs to `validate_product_ids`, where an operator is typing a
    list at a prompt rather than editing a file they can re-read.
    """
    allowlist = raw.get("allowlist")
    if not allowlist or not isinstance(allowlist, list):
        raise ConfigError("allowlist: missing or empty; must be a non-empty list of asset codes")
    for entry in allowlist:
        if not isinstance(entry, str) or not entry:
            raise ConfigError(f"allowlist: invalid entry {entry!r}; must be non-empty strings")
        if not is_spot_base_code(entry):
            # The hint fires only when case is the ONLY thing wrong, so it can never propose an
            # entry that is itself inadmissible -- `btc-usd` gets the refusal, not advice.
            hint = f" -- did you mean {entry.upper()}?" if is_spot_base_code(entry.upper()) else ""
            raise ConfigError(
                f"allowlist: invalid entry {entry!r}; an asset code is 1-16 UPPERCASE "
                f"alphanumeric characters (BTC, PAXG, 1INCH) -- it is the base leg of the "
                f"product id keel builds as '<asset>-{{quote_currency}}', so anything else "
                f"derives an id rail 19 would veto on every cycle{hint}"
            )
    return list(allowlist)


def _parse_settlement_currencies(raw: dict[str, Any]) -> frozenset[str]:
    """`settlement_currencies:`, uppercased -- rail 18's allowed set. Optional; an ABSENT key
    falls back to `DEFAULT_SETTLEMENT_CURRENCIES`.

    Uppercased at parse because `quote_currency_of` uppercases what it resolves, so the rail
    compares like with like by construction rather than by every call site remembering to fold
    case (the same normalisation `_history_product` applies when it CONSTRUCTS an id).

    A bare string is rejected rather than iterated: `settlement_currencies: USD` is a plausible
    typo, and taking it as a sequence would silently configure `{"U", "S", "D"}` -- a set that
    admits nothing and would veto every order with a message naming three letters.

    "Key absent" and "key present but null" are deliberately NOT the same thing: `raw.get` cannot
    tell them apart, so membership is tested instead. An operator who typed `settlement_currencies:`
    and left it bare was trying to change the rail's set; silently handing back the default would
    answer a deliberate edit with no feedback at all, and the currency they meant to add would
    still be vetoed on every order. Explicit null is rejected exactly like `[]`.

    Entries are shape-checked, not just non-empty: `quote_currency_of` can only ever return an
    uppercase alphanumeric token, so a code that is not one (`'US D'`, `'U'`, a sentence) is a
    member that admits nothing. Failing at load names the typo; admitting it would show up as a
    rail-18 veto on an order the operator believed they had just enabled.
    """
    if "settlement_currencies" not in raw:
        return DEFAULT_SETTLEMENT_CURRENCIES
    value = raw["settlement_currencies"]
    if value is None:
        raise ConfigError(
            "settlement_currencies: present but empty. Remove the key to accept the default "
            f"{sorted(DEFAULT_SETTLEMENT_CURRENCIES)}, or list the currency codes rail 18 should "
            "admit -- an empty set would veto every order, since no product's quote leg could "
            "be in it."
        )
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ConfigError(
            f"settlement_currencies: must be a non-empty list of currency codes, got {value!r}"
        )
    codes = set()
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError(
                f"settlement_currencies: invalid entry {entry!r}; must be non-empty strings"
            )
        code = entry.strip().upper()
        if not _CURRENCY_CODE_RE.fullmatch(code):
            raise ConfigError(
                f"settlement_currencies: invalid entry {entry!r}; a currency code is 2-10 "
                "alphanumeric characters (USD, USDC, EUR). Rail 18 matches this against the "
                "quote leg parsed out of a product id, which can never contain a space or "
                "punctuation, so this entry would admit nothing."
            )
        codes.add(code)
    if not codes:
        raise ConfigError(
            "settlement_currencies: empty; must be a non-empty list of currency codes. An empty "
            "set would veto every order, since no product's quote leg could be in it."
        )
    return frozenset(codes)


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


def _parse_logging(raw: dict[str, Any]) -> LoggingConfig:
    """Parse `logging:` -- optional, falls back to `LoggingConfig`'s defaults (verbose OFF,
    5 total 25MB rotated files) when absent."""
    logging_raw = raw.get("logging") or {}

    verbose = bool(logging_raw.get("verbose", False))

    max_file_mb = logging_raw.get("max_file_mb", 25)
    try:
        max_file_mb = int(max_file_mb)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"logging.max_file_mb: expected a positive integer, got {max_file_mb!r}"
        ) from exc
    if max_file_mb <= 0:
        raise ConfigError(
            f"logging.max_file_mb: must be a positive integer, got {max_file_mb!r}"
        )

    file_count = logging_raw.get("file_count", 5)
    try:
        file_count = int(file_count)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"logging.file_count: expected an integer >= 1, got {file_count!r}"
        ) from exc
    if file_count < 1:
        raise ConfigError(f"logging.file_count: must be an integer >= 1, got {file_count!r}")

    file = logging_raw.get("file", "logs/keel.log")
    if not isinstance(file, str) or not file:
        raise ConfigError(f"logging.file: must be a non-empty string, got {file!r}")

    return LoggingConfig(
        verbose=verbose, file=file, max_file_mb=max_file_mb, file_count=file_count
    )


def _parse_research(raw: dict[str, Any]) -> ResearchConfig:
    """Parse `research:` -- optional, falls back to `ResearchConfig`'s defaults.

    `slope_floor` is negative by design, so it deliberately does NOT go through
    `_non_negative_decimal`.
    """
    research_raw = raw.get("research") or {}
    defaults = ResearchConfig()

    pbo_max = _non_negative_decimal(
        research_raw.get("pbo_max", defaults.pbo_max), "research.pbo_max"
    )
    if pbo_max > 1:
        raise ConfigError(f"research.pbo_max: must be a probability in [0, 1], got {pbo_max!r}")

    raw_slope = research_raw.get("slope_floor", defaults.slope_floor)
    try:
        slope_floor = Decimal(str(raw_slope))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ConfigError(
            f"research.slope_floor: expected a number, got {raw_slope!r}"
        ) from exc

    return ResearchConfig(pbo_max=pbo_max, slope_floor=slope_floor)


#: The most permissive `execution.max_entry_spread_pct` a config may state. A guardrail
#: threshold above 10% cannot meaningfully be called a thin-book protection, so a value in
#: that territory is a typo (a misplaced decimal, a percentage typed where a fraction was
#: meant) and is refused at load rather than silently gutting the gate.
_MAX_ENTRY_SPREAD_PCT_CEILING = Decimal("0.10")


#: The two Alpaca trading environments an `endpoint:` may name. The vocabulary is exactly
#: two words because the ADAPTER derives the trading host from it (paper-api vs api) and
#: accepts no URL of any kind -- so no configuration can point a paper credential at the live
#: venue (FR-11). Anything else here is a typo that must fail at load.
_VALID_BROKER_ENDPOINTS = ("paper", "live")

#: The two Alpaca market-data tiers a `data_feed:` may name (FR-5): IEX is the free tier,
#: SIP the subscribed one. The choice is a DECLARED capability, never an assumption -- the
#: venue's server-side default is SIP, which silently fails for keys without the
#: subscription, so the adapter names its feed explicitly and the config names it first.
_VALID_BROKER_DATA_FEEDS = ("iex", "sip")


def _parse_broker(raw: dict[str, Any]) -> BrokerConfig:
    """Parse `broker:` (issue #370 B2) -- optional; an ABSENT section selects Coinbase
    unchanged, which is the byte-compatibility contract every pre-existing config and test
    relies on (see `BrokerConfig`).

    `name` is validated only for shape, not against a venue list: adapters are plugins
    discovered through the `keel.brokers` entry points, and keel-core cannot know which are
    installed. A name no adapter answers to fails at broker construction with the registry's
    own list of what IS installed -- the honest error, from the component that actually
    knows.

    `endpoint`/`data_feed` are Alpaca wiring keys, and setting them alongside any other
    venue is refused: the Coinbase construction has no such knob, so a config that sets one
    there would be silently ignoring an operator's deliberate edit -- the exact
    dead-knob-in-waiting failure `_parse_settlement_currencies` and friends exist to catch.
    """
    broker_raw = raw.get("broker") or {}
    if not isinstance(broker_raw, dict):
        raise ConfigError(
            f"broker: must be a mapping of {{name, endpoint, data_feed}}, got {broker_raw!r}"
        )

    name = broker_raw.get("name", "coinbase")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(
            f"broker.name: must be a non-empty venue name (resolved through the keel.brokers "
            f"entry points), got {name!r}"
        )
    name = name.strip()

    endpoint = broker_raw.get("endpoint", "paper")
    if endpoint not in _VALID_BROKER_ENDPOINTS:
        raise ConfigError(
            f"broker.endpoint: invalid value {endpoint!r}; must be one of "
            f"{_VALID_BROKER_ENDPOINTS!r} -- the trading host is derived from this choice and "
            f"is never a URL, so a paper credential cannot be pointed at the live venue"
        )

    data_feed = broker_raw.get("data_feed", "iex")
    if data_feed not in _VALID_BROKER_DATA_FEEDS:
        raise ConfigError(
            f"broker.data_feed: invalid value {data_feed!r}; must be one of "
            f"{_VALID_BROKER_DATA_FEEDS!r} -- the data tier is a declared capability, not a "
            f"server-side default to fall back on"
        )

    if name == "coinbase" and ("endpoint" in broker_raw or "data_feed" in broker_raw):
        raise ConfigError(
            f"broker.name: {name!r} has no endpoint/data_feed wiring -- those keys select the "
            "Alpaca trading host and market-data tier and are silently ignored by every other "
            "venue. Remove them, or set broker.name: alpaca."
        )

    return BrokerConfig(name=name, endpoint=endpoint, data_feed=data_feed)


def _parse_execution(raw: dict[str, Any]) -> ExecutionConfig:
    """Parse `execution:` -- optional, falls back to `ExecutionConfig`'s defaults.

    `max_entry_spread_pct` is checked for FINITENESS before the range comparison, mirroring
    `_non_negative_decimal`: `Decimal('NaN')` parses cleanly from a YAML `nan`, and the
    `<= 0` / `> ceiling` comparisons on it would RAISE InvalidOperation deep inside the
    executor's gate instead of failing here where the operator is editing the file.
    """
    execution_raw = raw.get("execution") or {}
    defaults = ExecutionConfig()

    pct = _to_decimal(
        execution_raw.get("max_entry_spread_pct", defaults.max_entry_spread_pct),
        "execution.max_entry_spread_pct",
    )
    if not pct.is_finite():
        raise ConfigError(
            f"execution.max_entry_spread_pct: must be a finite number in (0, 0.10], "
            f"got {pct!r}"
        )
    if pct <= 0 or pct > _MAX_ENTRY_SPREAD_PCT_CEILING:
        raise ConfigError(
            f"execution.max_entry_spread_pct: must be a number in (0, 0.10] -- a fraction of "
            f"price, not basis points (0.005 = 50bp); got {pct!r}. 0 would silently disarm "
            f"the routing-time entry-spread gate (#350), and a value above 0.10 is not a "
            f"thin-book threshold anyone means to set."
        )
    return ExecutionConfig(max_entry_spread_pct=pct)


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
    paper_raw = raw.get("paper") or {}
    subscription_raw = raw.get("subscription") or {}

    pacing = subscription_raw.get("pacing", "opportunistic")
    if pacing not in _VALID_PACING_MODES:
        raise ConfigError(
            f"subscription.pacing: invalid value {pacing!r}; must be one of "
            f"{_VALID_PACING_MODES!r}"
        )

    if "monthly_allowance_usd" in subscription_raw:
        raise ConfigError(
            "subscription.monthly_allowance_usd was renamed to "
            "subscription.assumed_free_volume_usd, which is now the SIMULATOR's assumed "
            "allowance only. The live rail-14 cap comes from the attested subscription record "
            "-- set it with `keel subscription attest --venue coinbase --tier <tier>`."
        )

    quote_currency = raw.get("quote_currency", "USD")
    if not isinstance(quote_currency, str) or not quote_currency:
        raise ConfigError(f"quote_currency: must be a non-empty string, got {quote_currency!r}")

    # Mirrors `quote_currency` immediately above: optional, but if present must be a non-empty
    # string. No shape/existence check beyond that -- unlike `allowlist`/`settlement_currencies`,
    # this is a filesystem path, not a venue identifier the rails compare against, so there is no
    # "would silently veto every order" failure mode to catch here. A missing directory is handled
    # by the reader (`latest_shortlist` returns `None` rather than raising) precisely because the
    # directory legitimately does not exist yet on a fresh install.
    proposals_dir = raw.get("proposals_dir", "~/keel/proposals")
    if not isinstance(proposals_dir, str) or not proposals_dir:
        raise ConfigError(
            f"proposals_dir: must be a non-empty string, got {proposals_dir!r}"
        )

    settlement_currencies = _parse_settlement_currencies(raw)

    # The two settings are independent knobs that describe the SAME leg, and a config that moves
    # one without the other is dead on arrival: `_history_product` builds every id keel can name
    # as `f"{asset}-{quote_currency}"`, and rail 18 vetoes any id whose quote leg is not in
    # `settlement_currencies`. So `quote_currency: EUR` against the default `{USD, USDC}` means
    # every order the deployment is capable of constructing is vetoed -- forever, on every cycle,
    # with a rail message about a currency the operator never typed. That is precisely the
    # "silent unfixable rejection" `_history_product`'s docstring was written to prevent, and it
    # is invisible until an order is attempted, so it is caught here instead.
    if quote_currency.upper() not in settlement_currencies:
        raise ConfigError(
            f"quote_currency: {quote_currency!r} is not in settlement_currencies "
            f"{sorted(settlement_currencies)}. Every product id keel builds is quoted in "
            f"quote_currency, and rail 18 vetoes any order whose settlement leg is outside "
            f"settlement_currencies -- as written, every order this deployment could place "
            f"would be rejected. Add {quote_currency.upper()!r} to settlement_currencies, or "
            f"set quote_currency to one of {sorted(settlement_currencies)}."
        )

    # Rail 16's two knobs are only meaningful together. The breaker arms
    # `halt_until = now_ts + streak_cooloff_days * 86400` and the rail tests `now_ts < halt_until`,
    # so a cooloff of 0 makes them equal: the breaker logs `streak.breaker_tripped` at WARNING,
    # writes the halt key, and vetoes nothing. Since `streak_cooloff_days` SHIPS at 0, an operator
    # who sets only `max_consecutive_losses` from a sweep gets a silently inert rail -- exactly the
    # reads-as-enforced-but-cannot-fire failure this codebase keeps having. Fail closed at load.
    _max_losses = _non_negative_int(
        money_mgmt_raw.get("max_consecutive_losses", 0), "money_mgmt.max_consecutive_losses"
    )
    _cooloff_days = _non_negative_int(
        money_mgmt_raw.get("streak_cooloff_days", 0), "money_mgmt.streak_cooloff_days"
    )
    if _max_losses > 0 and _cooloff_days <= 0:
        raise ConfigError(
            "money_mgmt.streak_cooloff_days: must be > 0 when max_consecutive_losses is "
            f"enabled (got max_consecutive_losses={_max_losses}, streak_cooloff_days="
            f"{_cooloff_days}). A cooloff of 0 arms the consecutive-loss breaker to expire at "
            "the very instant it trips, so it would log as tripped and veto nothing."
        )

    return Config(
        allowlist=allowlist,
        target_weights=target_weights,
        risk_pct=risk_pct,
        caps=caps,
        market_data=market_data,
        auto_trade=AutoTradeConfig(
            mode=_parse_auto_trade_mode(auto_trade_raw),
            enabled=bool(auto_trade_raw.get("enabled", False)),
            interval_sec=int(auto_trade_raw.get("interval_sec", 900)),
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
            max_consecutive_losses=_max_losses,
            streak_cooloff_days=_cooloff_days,
        ),
        dca=DcaConfig(
            budget_usd=_to_decimal(dca_raw.get("budget_usd", "0"), "dca.budget_usd"),
            cadence_days=int(dca_raw.get("cadence_days", 7)),
        ),
        paper=PaperConfig(
            starting_equity_usd=_non_negative_decimal(
                paper_raw.get("starting_equity_usd", "0"), "paper.starting_equity_usd"
            ),
            monthly_contribution_usd=_non_negative_decimal(
                paper_raw.get("monthly_contribution_usd", "0"), "paper.monthly_contribution_usd"
            ),
        ),
        subscription=SubscriptionConfig(
            assumed_free_volume_usd=_non_negative_decimal(
                subscription_raw.get("assumed_free_volume_usd", "500"),
                "subscription.assumed_free_volume_usd",
            ),
            unsubscribed_allowance_usd=_non_negative_decimal(
                subscription_raw.get("unsubscribed_allowance_usd", "0"),
                "subscription.unsubscribed_allowance_usd",
            ),
            pacing=pacing,
        ),
        tiers=_parse_tiers(raw),
        fees=_parse_fees(raw),
        quote_currency=quote_currency,
        proposals_dir=proposals_dir,
        settlement_currencies=settlement_currencies,
        logging=_parse_logging(raw),
        research=_parse_research(raw),
        execution=_parse_execution(raw),
        broker=_parse_broker(raw),
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


def load_alpaca_secrets(env_path: str | Path = ".env") -> dict:
    """Load Alpaca API credentials from the environment or a git-ignored `.env` file.

    Follows `load_secrets`' shape contract exactly -- `{"key_id": ..., "secret_key": ...}`
    when both are present, `{}` when neither is, so the caller (venue selection in
    `_build_broker`) owns the venue-specific "how to fix this" message rather than this
    loader guessing at one. Two deliberate divergences from `load_secrets`, both stated:

    * The REAL environment is read as well as the file (environment first), so a deployment
      can inject the paper keys without a `.env` at all. `load_secrets` predates multi-venue
      support and stays file-only for byte-compatibility; a new loader gets the honest
      both-sources semantics.
    * The names are the venue's own (`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`, the two
      headers Alpaca's API documents), namespaced by venue so one deployment's `.env` can
      hold credentials for several adapters without collisions.
    """
    values = dotenv_values(Path(env_path)) if Path(env_path).exists() else {}

    def _read(name: str) -> str | None:
        return os.environ.get(name) or values.get(name)

    key_id = _read("ALPACA_API_KEY_ID")
    secret_key = _read("ALPACA_API_SECRET_KEY")
    if not key_id and not secret_key:
        return {}
    return {"key_id": key_id, "secret_key": secret_key}


__all__ = [
    "ConfigError",
    "NON_BINDING_CAP_USD",
    "DEFAULT_SETTLEMENT_CURRENCIES",
    "Caps",
    "MarketDataConfig",
    "AutoTradeConfig",
    "PromotionConfig",
    "MoneyMgmtConfig",
    "DcaConfig",
    "PaperConfig",
    "SubscriptionConfig",
    "TierConfig",
    "LoggingConfig",
    "ResearchConfig",
    "ExecutionConfig",
    "FeesConfig",
    "BrokerConfig",
    "Config",
    "load_config",
    "load_secrets",
    "load_alpaca_secrets",
]
