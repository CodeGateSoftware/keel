"""The allowlist-admission DECISION layer and the `keel assets` command services.

Issue #387 C1 (the TUI-operator-console PRD, O2): the TUI must dispatch to exactly what the CLI
calls -- one implementation, two front-ends. Until this module existed the single admission gate
(`screen_product`, THE decision every candidate source must route through) lived inside
`keel/cli.py`'s command body, so the only way to reach it was to import the CLI composition
root; `keel/commands/tui.py` had to lazy-import `keel.cli` inside functions to dodge the cycle,
and `keel/commands/admission.py` had to take the gate as an injected `screen_fn` for the same
reason. The gate, the market-facts assembly, the holdings listing and the discovery sweep now
live here -- importable, and unit-testable, with no `keel.cli` anywhere in the import graph.

Two layers, mirroring `keel/commands/status.py`'s split:

- `market_facts`/`screen_product`/`gather_holdings`/`run_discovery`/`screen_products` are the
  compute: pure aside from the `Repository` (and, for `run_discovery`'s optional probe columns,
  the caller's already-constructed client) they are handed. No click anywhere, so the TUI can
  call them directly.
- `render_holdings`/`render_discover`/`render_screened_asset` are the pure renderers, returning
  the exact lines the CLI echoes -- kept HERE rather than in the front-end so the two front-ends
  cannot drift apart on the same report. The CLI wrapper does nothing but parse options, build
  the broker at the `_build_broker` seam, and echo.

`keel/commands/admission.py` stays the TUI's OFFLINE report layer (screen/propose/discover
overlays); this module is where the verdicts those reports render actually come from.
`keel/cli.py` re-exports `screen_product` as `_screen_product` so the existing tests'
`cli_module._screen_product` pins keep resolving to this exact object.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel_core.products import quote_currency_of

from keel.commands._products import _history_product
from keel.compliance import screen as screen_mod
from keel.commands.fetch import DAYS_PER_YEAR
from keel.compliance.screen import (
    Candidate,
    DiscoveryExclusions,
    DiscoveryPolicy,
    MarketFacts,
    ScreenResult,
    discover_candidates,
)
from keel.config import Config
from keel.data.repository import Repository
from keel.types import Granularity

#: Days of recent daily candles `--probe-liquidity` samples per candidate. Deliberately a RECENT
#: window rather than the full history the screen medians over: the probe's job is to be one cheap
#: request that tracks the criterion, not to reproduce it exactly. Recent is also the conservative
#: direction for a *pre-filter* — it reflects the liquidity a new position would actually meet.
LIQUIDITY_PROBE_DAYS = 180


#: The venue every product screened here is listed on.
#:
#: ⚠️ A CONSTANT because it is currently a fact, not a configuration. The live path constructs
#: `keel/data/cb_client.py`'s `CoinbaseClient` directly, so there is exactly one venue these
#: product ids can mean, and an `InstrumentAttestation` is keyed on `(venue, product_id)` --
#: which means the screen needs a venue id to look one up, and inventing a per-call parameter
#: for a value with one possible answer would be a knob whose only safe setting is its default.
#:
#: The broker-port migration replaces this with the adapter's own `BrokerCapabilities.venue`
#: (`packages/keel-broker-api/keel_broker_api/capabilities.py`), at which point the wrapper
#: statement recorded for `BTC-USD` on Coinbase correctly stops applying to `BTC-USD` somewhere
#: else -- which is issue #202's entire point and the reason the key is a pair. Until an adapter
#: handle actually reaches this function, reading a venue id off one would be reading it off
#: nothing: the same dead-gate pattern `capabilities.py` warns about, where a lookup that cannot
#: fail reads as a defence.
VENUE = "coinbase"


#: Discovery's 24h-volume pre-filter, and the ONE home of its default (moved here from
#: `keel/commands/admission.py`, which used to mirror the CLI option's default BY HAND -- with
#: this module as the shared layer there is nothing left to mirror). It bounds how many products
#: get probed for history; it is not a liquidity verdict (that is `--probe-liquidity`, which
#: computes the gate's own median).
#:
#: Was 5,000,000 until 2026-08-08. At that floor the sweep returned 9 candidates and exactly one
#: unsettled survivor; at a lower floor, seven more cleared BOTH mechanical gates -- FET among them
#: at $2.94M/24h, i.e. invisible to the sweep while measuring 4.8x the admission floor. The floor,
#: not the market, was the binding constraint on the candidate pipeline.
#:
#: The fix that followed pinned this EQUAL to `ScreenPolicy.min_median_daily_volume`
#: (1,000,000), reasoning that a sweep pinned to the gate's own floor could never be stricter
#: than the gate it feeds. That reasoning got the INTENT right and the MECHANISM wrong: the two
#: floors measure DIFFERENT statistics -- this one is a single 24-hour venue snapshot, the
#: admission floor is the median of volume x close over ALL cached history -- so an equal number
#: does nothing to stop a quiet trading day from pushing the snapshot below a floor the asset's
#: own median clears many times over. Measured 2026-08-15: five assets (ATOM, AAVE, BCH, CRV,
#: ALGO) were silently dropped by the equal-floor sweep despite each measuring 3.1x-6.3x the
#: admission floor on the gate's own statistic; four of the five sat in an 852,133-979,000 24h
#: snapshot cluster on that single quiet day.
#:
#: The floor is now strictly BELOW the admission floor, by an order of magnitude, so a quiet-day
#: snapshot has real room before it can hide an asset the gate would admit. See
#: `keel.compliance.screen.DiscoveryPolicy.min_quote_24h_volume`, which carries the identical
#: reasoning next to the number it actually applies.
#:
#: `tests/commands/test_admission.py` pins this to the CLI option, to `DiscoveryPolicy`'s default,
#: and to being strictly less than the admission floor; all of those move together or the suite
#: fails.
DEFAULT_MIN_QUOTE_24H_VOLUME = Decimal("100000")


#: The ONE home of `assets discover --limit`'s default (same move as
#: `DEFAULT_MIN_QUOTE_24H_VOLUME` above, from `admission.py`'s former hand-kept mirror).
#:
#: Was 25, set back when `--min-volume-24h`'s floor was 1,000,000 and a sweep returned ~35
#: candidates -- 25 showed nearly all of them. Lowering that floor to 100,000 (see
#: `DEFAULT_MIN_QUOTE_24H_VOLUME` above) grew a typical sweep to ~130 candidates, all sorted by
#: descending 24h volume, so the five assets that floor change exists to surface (ATOM, AAVE,
#: BCH, CRV, ALGO -- see that constant's docstring) landed at ranks 33-59: below the ~35 still
#: above the OLD floor, and past a limit of 25. The floor fix was real but invisible at the
#: operator's own default view.
#:
#: 100 costs nothing extra on its own: with neither `--probe-history` nor `--probe-liquidity`,
#: `assets discover` makes exactly ONE venue request (`list_products`) regardless of `--limit` --
#: the candidate list is filtered and sorted locally. The two probe flags are the ones with a
#: per-row cost (one venue request EACH per candidate SHOWN, so two together), which is why that
#: trade-off is called out in `--limit`'s own `help=` text rather than left for an operator to
#: discover by combining the flags and watching the request count climb.
DEFAULT_DISCOVER_LIMIT = 100


# Never candidates: you cannot trade the currency you settle in, and fiat is funding rather than
# a position. Coinbase quotes many fiats, so the list is deliberately broad -- a missing one is
# only cosmetic (an extra row), never an admission.
FIAT_CURRENCIES = frozenset(
    {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "SGD", "BRL", "MXN", "TRY", "INR", "KRW"}
)

# Stablecoins are cash EQUIVALENTS -- funding you hold between positions, not positions. They are
# excluded for the same reason fiat is: proposing the money as something to buy with the money is
# noise. (They would be REJECTED anyway -- unattested, and a yield-bearing one fails §28.4 -- so
# this only removes a redundant row, never an admission.)
CASH_EQUIVALENTS = frozenset({"USDC", "USDT", "DAI", "PYUSD", "TUSD", "USDP", "GUSD"})


# -- the admission decision -----------------------------------------------------------------------


def market_facts(repo: Repository, product: str, quote: str) -> MarketFacts:
    """Everything the screen can compute for itself from data we already hold."""
    asset = product.split("-")[0]
    candles = repo.get_candles(product, Granularity.ONE_DAY)
    return MarketFacts(
        asset=asset,
        daily_bars=len(candles),
        # `screen_mod.median_daily_quote_volume` is the ONE definition of this statistic --
        # `assets discover --probe-liquidity` pre-filters on the same call, so the sweep and the
        # gate cannot disagree about what "liquid enough" means.
        median_daily_volume=screen_mod.median_daily_quote_volume(candles),
        # A REAL check: does this product settle in the currency this deployment trades in?
        # The former `or bool(candles)` fallback made it vacuous -- every screened product is
        # `-USD`, so it always fell through to "do we have bars", which the history criterion
        # already covers. One of four admission criteria was doing nothing.
        quotable_in_settlement_currency=quote_currency_of(product) == quote.upper(),
        # Carried, not reduced: `screen_asset` applies rail 19's grammar to it, so the screen's
        # shape verdict and the rail's cannot disagree, and the verdict can name the id.
        product_id=product,
        # The other half of the key the instrument statement is recorded under. See `VENUE`.
        venue=VENUE,
    )


def screen_product(
    repo: Repository, product: str, quote: str
) -> tuple[MarketFacts, ScreenResult]:
    """THE admission decision, for every candidate source.

    `assets screen`, `assets holdings --screen`, the proposer and any future front-end (the TUI)
    all route through here, so none of them can drift onto a laxer path -- which is what makes
    "the same vetting process" a property of the code rather than an intention. Returns the facts
    alongside the verdict so a caller can explain WHY without recomputing them.

    The instrument statement is looked up HERE, next to the asset attestation, rather than being
    threaded in by each caller -- that is what makes the wrapper criterion inherit the same
    single-decision-point property as everything else on this path. Three callers get the new
    check with no per-caller wiring, and none of them can be the one that forgot it.
    """
    asset = product.split("-")[0]
    facts = market_facts(repo, product, quote)
    raw = repo.get_asset_attestation(asset)
    attestation = (
        screen_mod.AssetAttestation(
            asset=raw["asset"],
            sector=raw["sector"],
            backing=raw["backing"],
            pays_yield=bool(raw["pays_yield"]),
            source=raw["source"],
            attested_by=raw["attested_by"],
            attested_at=raw["attested_at"],
        )
        if raw is not None
        else None
    )
    raw_instrument = repo.get_instrument_attestation(VENUE, product)
    instrument = (
        screen_mod.InstrumentAttestation(
            venue=raw_instrument["venue"],
            product_id=raw_instrument["product_id"],
            wrapper=raw_instrument["wrapper"],
            source=raw_instrument["source"],
            attested_by=raw_instrument["attested_by"],
            attested_at=raw_instrument["attested_at"],
        )
        if raw_instrument is not None
        else None
    )
    waived = repo.get_screen_exceptions(asset)
    return facts, screen_mod.screen_asset(
        facts, attestation, waived=waived, instrument=instrument
    )


@dataclass(frozen=True)
class ScreenedAsset:
    """One `assets screen` row: the product asked about, its facts, and the gate's verdict."""

    product: str
    asset: str
    facts: MarketFacts
    result: ScreenResult

    @property
    def admitted(self) -> bool:
        return self.result.admitted


def screen_products(
    repo: Repository, config: Config, products: list[str]
) -> list[ScreenedAsset]:
    """Screen an explicit product list through THE gate -- `keel assets screen`'s compute.

    The caller owns the `--products` semantics (the CLI deliberately passes them UNVALIDATED --
    see `assets_screen`'s body for why), so this function judges exactly what it is handed, in
    order, and reports; it writes nothing.
    """
    screened: list[ScreenedAsset] = []
    for product in products:
        facts, result = screen_product(repo, product, config.quote_currency)
        screened.append(
            ScreenedAsset(
                product=product,
                asset=product.split("-")[0],
                facts=facts,
                result=result,
            )
        )
    return screened


def render_screened_asset(screened: ScreenedAsset) -> list[str]:
    """One product's `assets screen` block -- the exact lines the CLI prints per product."""
    facts, result, product = screened.facts, screened.result, screened.product
    lines = [
        f"\n{result.summary:<7} {screened.asset:<8} bars={facts.daily_bars} "
        f"median_daily_volume={facts.median_daily_volume:.0f}"
    ]
    # Same zero-bars split `assets holdings --screen` and `assets propose` use, via the same
    # two helpers in `screen.py`. `assets screen` is the SIBLING of the TUI's `s` screen overlay
    # -- both screen `_default_sim_products(config)` through `screen_product` -- so leaving
    # only one of them able to explain an empty cache would hand an operator two different
    # stories about the same allowlist depending on which surface they looked at. That is the
    # drift `screen_product` exists to prevent, applied to the REPORTING rather than to the
    # verdict. At zero bars `history`/`liquidity` measure OUR CACHE, not the asset, so
    # printing them as verdicts would say "too young" about something we simply never
    # fetched. `settlement`/`spot_instrument` read the product id alone and never touch
    # candles, so they stay real verdicts here -- which is exactly why this command may still
    # be asked about an unvalidated `--products` id (see the CLI body's note).
    failures, not_assessable = screen_mod.split_failures(facts, result)
    if facts.daily_bars == 0:
        explanation = screen_mod.missing_history_lines(product, not_assessable)
        lines.append(f"    ! {explanation[0]}")
        for extra_line in explanation[1:]:
            lines.append(f"      {extra_line}")
    for failure in failures:
        lines.append(f"    ✗ {failure}")
    for warning in result.warnings:
        lines.append(f"    ! {warning}")
    return lines


# -- holdings ----------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldingRow:
    """One holding, as a displayable allowlist candidate (see `gather_holdings`)."""

    asset: str
    balance: Decimal
    on_allowlist: bool
    attested: bool
    #: Populated only when the caller asked for `run_screen`: the gate's facts/verdict pair, the
    #: failure lines that survive the empty-cache split, and the verdict's warnings.
    facts: MarketFacts | None
    result: ScreenResult | None
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    zero_cache_explanation: tuple[str, ...]


@dataclass(frozen=True)
class HoldingsReport:
    quote: str
    floor: Decimal
    rows: tuple[HoldingRow, ...]


def broker_auth_hint(config: Config) -> str:
    """The credential hint naming the keys the CONFIG'S venue actually reads.

    Telling an alpaca operator to check CDP keys sends them hunting a credential this deployment
    never uses. Coinbase (the default, and any venue without dedicated credential wiring) keeps
    the historical CDP advice.
    """
    return (
        "ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY in .env (or the environment)"
        if config.broker.name == "alpaca"
        else "CDP_API_KEY/CDP_API_SECRET in .env"
    )


def gather_holdings(
    repo: Repository,
    config: Config,
    accounts: list[dict[str, Any]],
    floor: Decimal,
    *,
    run_screen: bool = False,
) -> HoldingsReport:
    """Turn broker account rows into allowlist CANDIDATES -- a SOURCE, not a gate.

    Holding an asset is not a reason to trade it: this admits nothing and mutates nothing. It
    answers "what do I already own that this system might trade?" by filtering out the
    settlement currency, fiat and cash equivalents (compared UPPERCASED -- a `usdc` balance is
    still the settlement currency and must not be presented as tradable on a casing accident)
    and everything at/below the dust floor, sorting by asset, and optionally screening each
    survivor through THE gate (unattested assets are REJECTED, because sector and backing cannot
    be derived from a balance any more than from a price).
    """
    quote = config.quote_currency
    excluded = FIAT_CURRENCIES | CASH_EQUIVALENTS | {quote.upper()}
    accounts = sorted(
        (
            a
            for a in accounts
            if (a.get("currency") or "").upper() not in excluded
            and a["available_balance"] > floor
        ),
        key=lambda a: (a.get("currency") or "").upper(),
    )

    allowlist = {asset.upper() for asset in config.allowlist}
    rows: list[HoldingRow] = []
    for account in accounts:
        # Uppercase here too, not just for the exclusion set: screening the raw code would look
        # up `btc` (UNATTESTED) while the allowlist check matched `BTC`, and would hand the
        # operator `keel fetch --products btc-USD`, a product id that never resolves.
        asset = (account.get("currency") or "").upper()
        attested = repo.get_asset_attestation(asset) is not None

        if not run_screen:
            rows.append(
                HoldingRow(
                    asset=asset,
                    balance=account["available_balance"],
                    on_allowlist=asset in allowlist,
                    attested=attested,
                    facts=None,
                    result=None,
                    failures=(),
                    warnings=(),
                    zero_cache_explanation=(),
                )
            )
            continue

        product = _history_product(asset, quote)
        facts, result = screen_product(repo, product, quote)
        # The likeliest misreading of this whole feature. With no cached bars, `history` and
        # `liquidity` cannot say anything about the ASSET -- `0 daily bars, need 1460` measures
        # the depth of OUR CACHE, and median volume is 0 *because* there are no bars -- so
        # printing either as a finding would assert exactly what this message exists to deny: a
        # candidate never fetched would read as indistinguishable from one genuinely too young.
        # They are shown as derived-from-an-empty-cache, not as verdicts.
        #
        # The split and the explanation both live in `keel.compliance.screen` (`split_failures` /
        # `missing_history_lines`), not here -- it owns `DATA_DERIVED_FAILURES`, the tag set that
        # decides the split, so the decision and the tags cannot silently drift apart the way two
        # independent per-caller copies could (and had, before `keel/proposer.py` and this
        # function were unified onto the same two functions).
        #
        # NOTE: `settlement` is deliberately NOT suppressed. It compares the product's quote leg
        # to the settlement currency and never reads candles, so it stays assessable at zero
        # bars. Do not add it to `DATA_DERIVED_FAILURES` -- no test would catch that here (a
        # derived product can never fail settlement), and it would hide a real verdict on any
        # externally supplied product.
        failures, not_assessable = screen_mod.split_failures(facts, result)
        explanation = (
            screen_mod.missing_history_lines(product, not_assessable)
            if facts.daily_bars == 0
            else ()
        )
        # Warnings carry compliance constraints that apply even to an ADMITted asset (§65.5's
        # bay' al-sarf regime for gold/silver backing, say). Dropping them would make the
        # holdings view quietly less informative than `assets screen` for the same asset.
        rows.append(
            HoldingRow(
                asset=asset,
                balance=account["available_balance"],
                on_allowlist=asset in allowlist,
                attested=attested,
                facts=facts,
                result=result,
                failures=tuple(failures),
                warnings=tuple(result.warnings),
                zero_cache_explanation=tuple(explanation),
            )
        )
    return HoldingsReport(quote=quote, floor=floor, rows=tuple(rows))


def render_holdings(report: HoldingsReport) -> list[str]:
    """The exact `keel assets holdings` lines, as a pure function of the report."""
    if not report.rows:
        return [
            f"no holdings above {report.floor} (excluding {report.quote} and fiat)."
        ]
    lines = [
        f"{len(report.rows)} holding(s) above {report.floor}, excluding "
        f"{report.quote} and fiat:\n"
    ]
    for row in report.rows:
        on_allowlist = "on-allowlist" if row.on_allowlist else "not-on-allowlist"
        attested = "attested" if row.attested else "UNATTESTED"
        lines.append(
            f"  {row.asset:<8} balance={row.balance:<18} {on_allowlist:<16} {attested}"
        )
        if row.result is None or row.facts is None:
            continue
        lines.append(f"      {row.result.summary}  ({row.facts.daily_bars} daily bars cached)")
        if row.zero_cache_explanation:
            lines.append(f"      ! {row.zero_cache_explanation[0]}")
            for extra_line in row.zero_cache_explanation[1:]:
                lines.append(f"        {extra_line}")
        for failure in row.failures:
            lines.append(f"      ✗ {failure}")
        for warning in row.warnings:
            lines.append(f"      ! {warning}")
    lines.append(
        "\n⚠️  Holdings are CANDIDATES, not admissions. Nothing here has been admitted to "
        "trading:\nthat needs `keel assets attest` with a source, a passing screen, and a "
        "deliberate edit to\n`allowlist` in config.yaml."
    )
    return lines


# -- discover ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoverRow:
    """One discovery table row: the candidate plus its (optional) probe columns."""

    candidate: Candidate
    #: `"yes "`/`"NO  "`/`"?   "` when `probe_history` was requested, else `None`.
    history_marker: str | None
    #: The probe's median statistic, or `None` when the probe could not answer (rendered `?`).
    median_daily_volume: Decimal | None
    #: `"LOW"`/`"ok "` for a answered liquidity probe; meaningless when the median is `None`.
    liquidity_verdict: str


@dataclass(frozen=True)
class DiscoverSweep:
    quote: str
    venue_product_count: int
    candidates: tuple[Candidate, ...]
    #: The FULL survivor list, before the table's `limit` cut -- see `render_discover`'s
    #: never-truncate-silently note.
    survivor_count: int
    min_quote_24h_volume: Decimal
    excluded: DiscoveryExclusions
    rows: tuple[DiscoverRow, ...]
    probe_history: bool
    probe_liquidity: bool
    min_median_daily_volume: Decimal


def run_discovery(
    client: Any,
    products: list[dict[str, Any]],
    config: Config,
    *,
    quote: str | None = None,
    min_volume_24h: Decimal | None = None,
    limit: int | None = None,
    probe_history: bool = False,
    probe_liquidity: bool = False,
    now_ts: int | None = None,
) -> DiscoverSweep:
    """Run the discovery sweep over ALREADY-FETCHED venue metadata. Admits nothing.

    A cheap pre-filter whose only job is to cut ~900 products to a shortlist worth pulling five
    years of candles for. Sector and backing are NOT considered here and cannot be -- every
    candidate below is still REJECTED by `assets screen` until a human attests it.

    Takes `products` as a plain argument rather than building a broker itself, exactly like
    `keel.commands.admission.build_discover_report`: that is what lets a front-end gate the one
    network call (`client.list_products()`) behind an explicit ask. The optional probe columns
    DO make one `client.get_candles` request per shown candidate (two per row if both probes are
    on) -- passed the same client the caller built, never one of its own.

    `min_volume_24h`/`limit` default to `DEFAULT_MIN_QUOTE_24H_VOLUME`/`DEFAULT_DISCOVER_LIMIT`,
    matching `assets discover`'s own CLI defaults; `now_ts` defaults to "now" and exists so the
    probe windows are deterministic under test.
    """
    if now_ts is None:
        now_ts = int(time.time())
    volume_floor = (
        min_volume_24h if min_volume_24h is not None else DEFAULT_MIN_QUOTE_24H_VOLUME
    )
    shown = limit if limit is not None else DEFAULT_DISCOVER_LIMIT
    policy = DiscoveryPolicy(
        quote_currency=quote or config.quote_currency,
        min_quote_24h_volume=volume_floor,
    )
    result = discover_candidates(
        products, policy, exclude_assets=frozenset(config.allowlist)
    )

    screen_policy = screen_mod.ScreenPolicy()
    four_years_ago = now_ts - 4 * DAYS_PER_YEAR * 86400
    liquidity_window_start = now_ts - LIQUIDITY_PROBE_DAYS * 86400
    rows: list[DiscoverRow] = []
    for candidate in result.candidates[:shown]:
        history_marker: str | None = None
        if probe_history:
            try:
                probed = client.get_candles(
                    candidate.product_id,
                    Granularity.ONE_DAY,
                    four_years_ago,
                    four_years_ago + 30 * 86400,
                )
                history_marker = "yes " if probed else "NO  "
            except Exception:  # noqa: BLE001 -- a probe failure is unknown, not a verdict
                history_marker = "?   "
        median: Decimal | None = None
        verdict = ""
        if probe_liquidity:
            try:
                sampled = client.get_candles(
                    candidate.product_id, Granularity.ONE_DAY, liquidity_window_start, now_ts
                )
                median = screen_mod.median_daily_quote_volume(sampled)
                # Same comparison `screen_asset` will make, against the same floor -- that is the
                # entire point. A candidate marked LOW here is one the gate would reject.
                verdict = "LOW" if median < screen_policy.min_median_daily_volume else "ok "
            except Exception:  # noqa: BLE001 -- same rule as the history probe: unknown, not a no
                median = None
                verdict = ""
        rows.append(
            DiscoverRow(
                candidate=candidate,
                history_marker=history_marker,
                median_daily_volume=median,
                liquidity_verdict=verdict,
            )
        )

    return DiscoverSweep(
        quote=policy.quote_currency,
        venue_product_count=len(products),
        candidates=tuple(result.candidates[:shown]),
        survivor_count=len(result.candidates),
        min_quote_24h_volume=volume_floor,
        excluded=result.excluded,
        rows=tuple(rows),
        probe_history=probe_history,
        probe_liquidity=probe_liquidity,
        min_median_daily_volume=screen_policy.min_median_daily_volume,
    )


def render_discover(sweep: DiscoverSweep) -> list[str]:
    """The exact `keel assets discover` lines, as a pure function of the sweep."""
    lines = [
        f"{sweep.venue_product_count} venue products -> {sweep.survivor_count} candidates "
        f"(quote={sweep.quote}, 24h volume >= {sweep.min_quote_24h_volume:,.0f}, "
        "excluding the current allowlist)",
        sweep.excluded.summary_line(),
    ]
    # Never truncate silently: `survivor_count` above is the FULL survivor list (only the table
    # loop is cut to `limit`), so if there are more survivors than the table allows, say so
    # explicitly -- how many exist, how many are about to be shown, and that --limit is the
    # knob. A silent cap here would be exactly the defect class this command's own fix
    # (the --min-volume-24h floor) exists to eliminate, just moved one step later in the pipeline.
    if sweep.survivor_count > len(sweep.rows):
        lines.append(
            f"showing {len(sweep.rows)} of {sweep.survivor_count} candidates -- raise --limit "
            "to see the rest."
        )
    lines.append("")
    header = f"{'#':>3}  {'product':<14} {'asset':<8} {'24h quote volume':>18}"
    if sweep.probe_history:
        header += "  4yr?"
    if sweep.probe_liquidity:
        header += f"  {'median daily (probe)':>21}"
    lines.append(header + "  name")

    for index, row in enumerate(sweep.rows, start=1):
        candidate = row.candidate
        line = (
            f"{index:>3}  {candidate.product_id:<14} {candidate.asset:<8} "
            f"{candidate.quote_24h_volume:>18,.0f}"
        )
        if row.history_marker is not None:
            line += f"  {row.history_marker}"
        if sweep.probe_liquidity:
            if row.median_daily_volume is None:
                line += f"  {'?':>17} ?  "
            else:
                line += f"  {row.median_daily_volume:>17,.0f} {row.liquidity_verdict}"
        lines.append(line + f"  {candidate.base_name}")

    lines.append(
        "\n⚠️  These are PROPOSALS, not admissions. Nothing above has been screened for sector "
        "or backing -- those cannot be derived from market data. Each one needs "
        "`keel assets attest` with a source before `keel assets screen` can admit it."
    )
    if sweep.probe_liquidity:
        lines.append(
            f"\n'median daily (probe)' is the SAME statistic the screen applies (median of "
            f"volume x close), floor {sweep.min_median_daily_volume:,.0f} -- but sampled "
            f"over the last {LIQUIDITY_PROBE_DAYS} days, where the screen medians over all "
            "cached history. Treat LOW as 'the gate will reject this' and ok as 'worth pulling "
            "candles for', never as the verdict itself. Run `keel assets screen` for that."
        )
    return lines
