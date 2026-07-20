"""Allowlist admission screening — the curation gate (KB §28.4, §65.5, §67.2).

⚠️ **This is a CURATION gate, not a per-trade rail.** §28.4 is explicit: a token's sector and
backing are *"a listing criterion, checked once when curating the allowlist, not per-trade"*.
`guards.py` rail 1 keeps enforcing the allowlist mechanically on every intent; this decides what
is allowed to ENTER that list. Nothing here runs on the hot path.

**The screen is split by what is knowable, and that split is the whole design.**

- **Market facts are COMPUTED** from data we already hold: history depth, liquidity, whether the
  asset is quotable in our settlement currency. No judgement, no attestation, recomputed freely.
- **Shariah classifications are ATTESTED, never inferred.** Whether a token's core purpose is a
  haram sector (§28.4), whether it is asset-backed `'ayn` or a claim `dayn` (§65.5/§67.2), and
  whether it pays a riba-like yield are questions of fact-plus-scholarship about the world. This
  module cannot derive them from candles and does not pretend to. They must be recorded
  explicitly, with a source, by a human.

**Absent attestation FAILS CLOSED.** An unattested asset is not "probably fine" — it is unknown,
and unknown is a rejection. This mirrors `broker_subscriptions`, where an un-attested venue is
`suspect` and blocks live BUYs rather than defaulting to a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

#: §28.4's haram business lines, plus the crypto-specific readings it names.
HARAM_SECTORS = frozenset(
    {
        "gambling",
        "casino",
        "adult",
        "alcohol",
        "pork",
        "tobacco",
        "firearms",
        "riba_yield",  # interest-bearing "yield"/lending tokens
    }
)

#: §65.5/§67.2. `'ayn` = a tangible/owned thing; `dayn` = a debt claim on an issuer. A pure claim
#: is a different contract with different rules, so it must be named rather than assumed.
BACKING_AYN = "ayn"
BACKING_DAYN = "dayn"
BACKING_NATIVE = "native"  # a base-layer coin, neither a claim nor a warehouse receipt
KNOWN_BACKINGS = frozenset({BACKING_AYN, BACKING_DAYN, BACKING_NATIVE})


@dataclass(frozen=True)
class AssetAttestation:
    """Human-recorded facts a candle series cannot answer. See the module docstring."""

    asset: str
    sector: str  # free text; matched against HARAM_SECTORS
    backing: str  # one of KNOWN_BACKINGS
    pays_yield: bool  # riba-like guaranteed/expected return attached to holding it
    source: str  # where this was established -- a URL, a standard, a scholar's ruling
    attested_by: str
    attested_at: int


@dataclass(frozen=True)
class MarketFacts:
    """Everything the screen can compute for itself."""

    asset: str
    daily_bars: int
    median_daily_volume: Decimal
    quotable_in_settlement_currency: bool


@dataclass(frozen=True)
class ScreenPolicy:
    """Admission thresholds. Deliberately explicit rather than magic numbers in the logic."""

    #: §28.4 names "5yr-data" as an existing admission criterion. 4 years of daily bars is the
    #: floor here, not 5: PAXG-USD has ~439 and would be rejected either way, and demanding a
    #: full 5 years would reject an asset that is 4.9 years old for no principled reason.
    min_daily_bars: int = 4 * 365
    #: Liquidity: enough depth that our order size is not the market. A deliberately blunt
    #: floor -- the real constraint is our own tiny size, so this only excludes the genuinely thin.
    min_median_daily_volume: Decimal = Decimal("1000000")
    require_settlement_quote: bool = True


@dataclass(frozen=True)
class ScreenResult:
    asset: str
    admitted: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return "ADMIT" if self.admitted else "REJECT"


def screen_asset(
    facts: MarketFacts,
    attestation: AssetAttestation | None,
    policy: ScreenPolicy | None = None,
) -> ScreenResult:
    """Deterministic admission decision. `attestation=None` fails closed."""
    policy = policy or ScreenPolicy()
    failures: list[str] = []
    warnings: list[str] = []

    # -- computed market facts -------------------------------------------------
    if facts.daily_bars < policy.min_daily_bars:
        failures.append(
            f"history: {facts.daily_bars} daily bars < {policy.min_daily_bars} required "
            "(a rule cannot be validated on a series shorter than its evidence needs)"
        )
    if facts.median_daily_volume < policy.min_median_daily_volume:
        failures.append(
            f"liquidity: median daily volume {facts.median_daily_volume} < "
            f"{policy.min_median_daily_volume} required"
        )
    if policy.require_settlement_quote and not facts.quotable_in_settlement_currency:
        failures.append(
            "settlement: not quotable in the configured settlement currency -- a cross would "
            "add a second exchange leg, and §65.7 requires each leg be priced and settled"
        )

    # -- attested shariah classification ---------------------------------------
    if attestation is None:
        failures.append(
            "attestation: MISSING. Sector and backing cannot be derived from price data, so an "
            "unattested asset is unknown, and unknown is a rejection (fail closed). Record one "
            "with `keel assets attest`."
        )
        return ScreenResult(asset=facts.asset, admitted=False, failures=failures, warnings=warnings)

    sector = attestation.sector.strip().lower()
    if sector in HARAM_SECTORS:
        failures.append(f"haram_sector: {sector!r} is an excluded business line (§28.4)")
    if attestation.pays_yield:
        failures.append(
            "riba_yield: the asset carries a guaranteed/expected return for holding it, which is "
            "riba-like (§28.4); holding it is not a bare spot position"
        )

    backing = attestation.backing.strip().lower()
    if backing not in KNOWN_BACKINGS:
        failures.append(
            f"backing: {backing!r} is not one of {sorted(KNOWN_BACKINGS)} -- classify it "
            "explicitly rather than leaving it open (§65.5/§67.2)"
        )
    elif backing == BACKING_DAYN:
        failures.append(
            "backing: 'dayn' -- a debt claim on an issuer, not an owned thing. Trading a pure "
            "claim is a different contract under different rules (§65.5/§67.2), so it is not "
            "admitted by this policy"
        )
    elif backing == BACKING_AYN:
        warnings.append(
            "backing 'ayn': asset-backed. If the backing is gold or silver, §65.5's stricter "
            "bay' al-sarf regime applies -- no deferment, 72h settlement bound"
        )

    if not attestation.source.strip():
        failures.append("attestation: no source recorded -- an unsourced claim is not evidence")

    return ScreenResult(
        asset=facts.asset,
        admitted=not failures,
        failures=failures,
        warnings=warnings,
    )
