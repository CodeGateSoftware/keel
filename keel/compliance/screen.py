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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from keel_core.products import parse_spot_product_id

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

#: Criteria a documented, human-recorded exception (`keel assets exempt`) may EVER waive. Only
#: DATA/market criteria belong here -- history depth, liquidity, that kind of thing -- because
#: those are facts about our own cache, not about the asset's shariah status. The shariah
#: criteria (a missing attestation, `haram_sector`, `riba_yield`, `dayn`/unknown backing) and
#: `settlement` can NEVER be waived: nothing in this module consults `waived` for them, and the
#: CLI's `--criterion` Choice is restricted to this set. Expanding it is a deliberate future
#: decision, not a default -- do not add to it to make a test pass.
WAIVABLE_CRITERIA = frozenset({"history"})

#: Failure classes that are DOWNSTREAM of having no cached history: with zero bars `liquidity`
#: reports on our data (median volume is 0 *because* there are no bars), not on the asset. `history`
#: belongs here for the identical reason and was the bug this set used to miss: at zero bars,
#: `history: 0 daily bars, need 1460` measures the depth of OUR CACHE, not the age of the asset --
#: a candidate never fetched is indistinguishable from one genuinely too young unless this tag is
#: suppressed exactly like `liquidity` is. `settlement` is deliberately NOT here -- it compares the
#: product's quote leg to the settlement currency and never touches candles, so it stays a real,
#: assessable verdict even with zero bars. This is the single source of truth for that tag set;
#: callers import it (or, better, call `split_failures`/`missing_history_lines` below rather than
#: reimplementing the split) so a tag rename here cannot silently disable the suppression
#: elsewhere.
DATA_DERIVED_FAILURES = frozenset({"history", "liquidity"})


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
    #: The venue id the other facts were gathered for. Carried rather than reduced to a bool so
    #: `screen_asset` can apply `parse_spot_product_id` -- rail 19's own grammar, one copy -- and
    #: so its verdict can NAME the id. `asset` is that id's base leg and cannot answer the shape
    #: question: `BTC-PERP-USD` and `BTC-USD` have the same `asset`.
    #:
    #: Deliberately has NO default. A default would have to be some id, and any id that parses
    #: is a fail-OPEN default for a criterion whose whole job is refusing one that does not --
    #: so a construction site that forgets it must fail loudly at the call, not quietly at the
    #: verdict.
    product_id: str


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
    waived: Mapping[str, str] | None = None,
) -> ScreenResult:
    """Deterministic admission decision. `attestation=None` fails closed.

    `waived` is `{criterion: rationale}` from a documented human exception (`keel assets
    exempt` / `repository.get_screen_exceptions`). It is consulted ONLY when a check would
    otherwise FAIL, and ONLY for criteria in `WAIVABLE_CRITERIA` -- a waiver for anything else
    (a stray `screen_exceptions` row for, say, `attestation`) is silently ignored and that
    criterion still fails closed. A waiver never affects any criterion other than its own, and a
    blank/whitespace rationale is treated as no waiver at all (fail closed -- see the `.strip()`
    check below, mirroring the unsourced-attestation guard further down).
    """
    policy = policy or ScreenPolicy()
    # Filtered ONCE, up front, rather than inline per-branch: this is the actual defense-in-depth
    # for a criterion that is not in WAIVABLE_CRITERIA. `history` is currently the SOLE consumer
    # of a waiver (screen_asset only ever reads `effective_waived["history"]`, so a shariah check
    # is already structurally unreachable from `waived`) -- but filtering here means that even if
    # a future edit wires a waiver lookup into another branch, it can never see an entry for a
    # criterion nobody was allowed to grant one for, because it was dropped before any branch ran.
    effective_waived = {c: r for c, r in (waived or {}).items() if c in WAIVABLE_CRITERIA}
    failures: list[str] = []
    warnings: list[str] = []

    # -- computed market facts -------------------------------------------------
    if facts.daily_bars < policy.min_daily_bars:
        history_rationale = effective_waived.get("history", "").strip()
        if history_rationale:
            # Self-retiring: this branch is only reached when the check WOULD fail, so a stale
            # waiver on an asset that has since accumulated enough history produces no output at
            # all -- see the `>=` branch below, which never looks at `effective_waived`.
            warnings.append(
                f"history: {facts.daily_bars} daily bars < {policy.min_daily_bars} required -- "
                f"WAIVED by documented exception: {history_rationale}"
            )
        else:
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
    # The SHAPE criterion, and rail 19's question asked one gate earlier (feasibility study R2).
    # Settlement used to be this screen's ONLY id-derived criterion, and settlement reads the
    # LAST segment: `quote_currency_of("BTC-PERP-USD")` is `"USD"`, so a derivative-shaped id
    # with a legitimate final segment passed it. `assets screen` -- the command that ANSWERS
    # "may keel trade this, and why not", and the one `--products` caller deliberately exempt
    # from option validation so that it can report rather than refuse -- therefore said ADMIT
    # about the one product shape rail 19 exists to veto. The exemption is only honest if the
    # answer is right.
    #
    # NO `ScreenPolicy` knob, unlike `require_settlement_quote` beside it, and for rail 19's
    # reason: settlement currencies are an operator preference with a real escape hatch
    # (`config.settlement_currencies`), whereas spot-only is this agent's charter. A knob whose
    # only safe value is its default is a liability.
    #
    # `parse_spot_product_id` is rail 19's own grammar, imported rather than restated, so the
    # screen and the rail cannot drift into disagreeing about what a spot id is -- an id this
    # gate admits and that rail then vetoes forever is the worst answer either could give.
    if parse_spot_product_id(facts.product_id) is None:
        failures.append(
            f"spot_instrument: {facts.product_id!r} is not a well-formed spot product id "
            "(BASE-QUOTE, uppercase, exactly one hyphen). keel is spot-only, so futures "
            "(BASE-DDMMMYY-CDE), equities (an opaque 64-char hash) and any other instrument "
            "shape are refused regardless of what they settle in -- rail 19 would veto every "
            "order for it"
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


def split_failures(facts: MarketFacts, result: ScreenResult) -> tuple[list[str], list[str]]:
    """Partition `result.failures` into `(about_the_asset, about_our_cache)`.

    This is the DECISION half of the zero-bar-history fix, kept in `screen.py` rather than in
    each caller, because this module already owns `DATA_DERIVED_FAILURES` -- the tag set that
    decides which failures are "downstream of an empty cache" in the first place. Duplicating the
    split next to each caller (as `assets holdings` and `assets propose` used to, independently)
    let the two copies drift: a tag rename here would silently stop suppressing one of them and
    keep suppressing the other. Putting the split next to the tag set makes that impossible --
    there is exactly one place either can change.

    With `facts.daily_bars > 0` every failure is returned as `about_the_asset` and
    `about_our_cache` is empty, UNSPLIT -- including a `history` shortfall.

    Be clear about what that does and does not mean. It does NOT mean a shallow cache proves the
    asset is young. `MarketFacts` carries a bar COUNT and no first-bar timestamp, so this function
    cannot distinguish "listed 18 months ago" from "we fetched an 18-month window": `keel fetch
    --years 2`, a fetch that aborted partway, or a venue that simply does not serve the full
    window all leave an OLD asset shallow (`keel/cli.py`'s `fetch` prints a note about exactly
    that -- "some series are still short... an asset younger than the requested window"). Every
    surface nonetheless renders `✗ history: 730 daily bars < 1460 required` as a verdict, because
    a partial cache is genuinely ambiguous and the gate must fail closed on ambiguity rather than
    admit on it.

    So the operator reading a non-zero `history` failure should CHECK THE FETCH WINDOW before
    concluding the asset is too young: `keel fetch --products <id> --years 5`, then re-screen. If
    the count does not move, it is the asset.

    The split is confined to EXACTLY zero bars because that is the only count where there is no
    ambiguity to resolve: with no candles at all, `history` and `liquidity` are reporting the
    emptiness of our cache and nothing whatsoever about the asset. Original ordering is preserved
    within each returned list, so a caller that renders them in order does not see failures
    reshuffled relative to how `screen_asset` produced them.
    """
    if facts.daily_bars > 0:
        return list(result.failures), []
    about_the_asset: list[str] = []
    about_our_cache: list[str] = []
    for failure in result.failures:
        if failure.split(":")[0] in DATA_DERIVED_FAILURES:
            about_our_cache.append(failure)
        else:
            about_the_asset.append(failure)
    return about_the_asset, about_our_cache


def missing_history_lines(product_id: str, not_assessable: Sequence[str]) -> list[str]:
    """The MISSING-DATA explanation shown INSTEAD OF `not_assessable`'s raw failures.

    Lives here, beside `split_failures`, for the same reason: the wording references the exact
    tag set this module owns, so the explanation and the tags it explains cannot drift apart.
    Formatting (indentation, bullets, `!`/`·` markers) is deliberately left to the caller instead
    of baked in here -- `keel/proposer.py` and `keel/cli.py`'s `assets holdings` indent by
    different amounts, and a third caller (the TUI) will have its own widget-native layout again.
    This function returns plain, UNINDENTED lines; callers prepend whatever presentation they need.

    The third line -- naming what is still unassessable -- is included only when `not_assessable`
    is non-empty, so a candidate that fails purely on shape/settlement/attestation (nothing
    downstream of the cache) does not get a trailing "not assessable until then:" with nothing
    after the colon. Tags are deduplicated and sorted so two failures sharing a tag collapse to
    one mention and the order is stable regardless of how `screen_asset` happened to emit them.
    """
    # The second line is the SEMANTIC one -- it says what a zero-bar report means -- and it is
    # deliberately agnostic about the asset. It used to read "it is not too young, we have simply
    # never fetched candles for it", which asserts something we cannot know: at exactly zero bars
    # a listing three days old and one three years old are the same input, since `MarketFacts`
    # carries no first-bar timestamp. Refusing to rule is the honest answer, and it is also the
    # useful one -- it points at the fetch, which is the only thing that can resolve it.
    # `test_missing_history_lines_claim_nothing_about_the_assets_age` pins this.
    lines = [
        f"no local history for {product_id} -- run `keel fetch --products {product_id}` first, "
        "then re-screen.",
        "This is a MISSING-DATA verdict, not a verdict about the asset: with no candles at all "
        "we cannot tell a genuinely young asset from one we have simply never fetched, so this "
        "says nothing about its age either way.",
    ]
    if not_assessable:
        tags = sorted({f.split(":")[0] for f in not_assessable})
        lines.append(f"not assessable until then: {', '.join(tags)}")
    return lines


# -- discovery (candidate PROPOSAL, not admission) -----------------------------


@dataclass(frozen=True)
class Candidate:
    """A venue product that cleared the cheap pre-filter. NOT an admitted asset."""

    product_id: str
    asset: str
    base_name: str
    quote_24h_volume: Decimal


@dataclass(frozen=True)
class DiscoveryPolicy:
    """The cheap pre-filter, run on venue metadata BEFORE fetching any history.

    Deliberately permissive: its only job is to cut ~900 products to a shortlist worth pulling
    five years of candles for. Everything that decides admission lives in `screen_asset`.
    """

    quote_currency: str = "USD"
    min_quote_24h_volume: Decimal = Decimal("1000000")


def median_daily_quote_volume(candles: Sequence[Any]) -> Decimal:
    """Median of `volume * close` over `candles` -- the liquidity statistic, defined ONCE.

    QUOTE volume, not base: `Candle.volume` is in base units, so a bar's contribution is scaled
    by its own close. That makes the number comparable across assets and comparable to the
    venue's reported quote volume.

    **Both callers must use this.** `cli._market_facts` feeds it to `screen_asset`'s liquidity
    criterion; `assets discover --probe-liquidity` uses it to pre-filter on the SAME statistic
    the gate will later apply. A second copy next to whichever caller needed it would drift, and
    the symptom of that drift is the one this function exists to prevent: a sweep that proposes
    an asset the screen then rejects on liquidity, or quietly drops one it would have admitted.

    Empty input is `Decimal(0)` -- no bars is no evidence of liquidity, and the criterion treats
    0 as failing, which is the fail-closed direction.
    """
    if not candles:
        return Decimal(0)
    volumes = sorted(candle.volume * candle.close for candle in candles)
    return volumes[len(volumes) // 2]


def discover_candidates(
    products: list[dict],
    policy: DiscoveryPolicy | None = None,
    exclude_assets: frozenset[str] | None = None,
) -> list[Candidate]:
    """Propose candidates from venue metadata. **Proposes only — admits nothing.**

    §5's asymmetry: a proposal may come from anywhere, but activity may only INCREASE through
    the deterministic gate. Nothing here checks sector or backing, and nothing here may be read
    as approval — every survivor still has to clear `screen_asset`, which fails closed without a
    human attestation.
    """
    policy = policy or DiscoveryPolicy()
    exclude = exclude_assets or frozenset()
    out: list[Candidate] = []

    for product in products:
        product_id = product.get("product_id") or ""
        if (product.get("quote_currency_id") or "").upper() != policy.quote_currency.upper():
            continue
        if product.get("status") != "online":
            continue
        if product.get("trading_disabled") or product.get("is_disabled"):
            continue
        if product.get("view_only"):
            continue

        asset = product_id.split("-")[0]
        if asset in exclude:
            continue

        raw_volume = product.get("quote_24h_volume")
        try:
            volume = Decimal(str(raw_volume))
        except (TypeError, ArithmeticError, ValueError):
            continue
        if volume < policy.min_quote_24h_volume:
            continue

        out.append(
            Candidate(
                product_id=product_id,
                asset=asset,
                base_name=product.get("base_name") or asset,
                quote_24h_volume=volume,
            )
        )

    return sorted(out, key=lambda c: c.quote_24h_volume, reverse=True)
