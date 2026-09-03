"""Volume from a single-venue feed is a LOWER BOUND, and the two directions differ -- #696.

keel's liquidity floor was calibrated against venue-reported volume. On a crypto exchange that
is coherent: Coinbase's own volume is the scale the floor was chosen against. On Alpaca's IEX
feed it is not -- IEX reports one US equity exchange's own executions, and the cost-fidelity run
measured MSFT, one of the most liquid securities in the world, cached at $186M/day and priced as
a thin asset.

THE ASYMMETRY IS THE WHOLE GATE, and getting it backwards fails in both directions at once:

* at or above the floor on a partial feed -> **CONCLUSIVE PASS.** If a name traded that much on
  one venue alone it necessarily traded at least that much in total. A gate that refused here
  would ban MSFT and AAPL for thinness they do not have, and would turn a data-vendor pricing
  tier into a hard prerequisite for running the engine.
* below the floor on a partial feed -> **NOT A VERDICT.** It is consistent with a genuinely thin
  asset and with a liquid one that barely trades on this venue. Reporting it as "illiquid" would
  be asserting something unmeasured; the refusal has to say so.

`test_a_partial_feed_above_the_floor_is_admitted` and
`test_a_partial_feed_below_the_floor_refuses_without_calling_it_thin` are the two that carry it.
"""

from __future__ import annotations

from decimal import Decimal

from keel.compliance.screen import (
    DATA_DERIVED_FAILURES,
    WAIVABLE_CRITERIA,
    AssetAttestation,
    InstrumentAttestation,
    MarketFacts,
    ScreenPolicy,
    screen_asset,
)

_VENUE = "alpaca"
_FLOOR = Decimal("1000000")


def _facts(volume: str, scope: bool | None) -> MarketFacts:
    return MarketFacts(
        asset="MSFT",
        daily_bars=2000,
        median_daily_volume=Decimal(volume),
        quotable_in_settlement_currency=True,
        product_id="MSFT-USD",
        venue=_VENUE,
        volume_feed="alpaca:iex" if scope is False else ("alpaca:sip" if scope else None),
        volume_feed_is_consolidated=scope,
    )


def _clean_attestations() -> tuple[AssetAttestation, InstrumentAttestation]:
    return (
        AssetAttestation(
            asset="MSFT",
            sector="technology",
            backing="equity",
            pays_yield=False,
            source="operator",
            attested_by="tester",
            attested_at=1_784_505_600,
        ),
        InstrumentAttestation(
            venue=_VENUE,
            product_id="MSFT-USD",
            wrapper="spot",
            source="venue product spec",
            attested_by="tester",
            attested_at=1_784_505_600,
        ),
    )


def _screen(volume: str, scope: bool | None):
    asset, instrument = _clean_attestations()
    return screen_asset(
        _facts(volume, scope),
        asset,
        policy=ScreenPolicy(min_median_daily_volume=_FLOOR, min_daily_bars=1),
        instrument=instrument,
    )


def _tags(result) -> set[str]:
    return {failure.split(":", 1)[0] for failure in result.failures}


# --- the conclusive direction ----------------------------------------------------------------


def test_a_partial_feed_above_the_floor_is_admitted() -> None:
    """The bound is one-sided. Clearing the floor on ONE venue proves the floor is cleared."""
    result = _screen("5000000", scope=False)
    assert "liquidity" not in _tags(result)
    assert "liquidity_unmeasured" not in _tags(result)


def test_a_consolidated_feed_above_the_floor_is_admitted() -> None:
    assert "liquidity" not in _tags(_screen("5000000", scope=True))


def test_exactly_at_the_floor_is_admitted_on_a_partial_feed() -> None:
    """The boundary is `>= floor`, same as the consolidated path -- a partial feed must not
    acquire a stricter threshold by accident."""
    assert "liquidity" not in _tags(_screen(str(_FLOOR), scope=False))
    assert "liquidity_unmeasured" not in _tags(_screen(str(_FLOOR), scope=False))


# --- the inconclusive direction ---------------------------------------------------------------


def test_a_partial_feed_below_the_floor_refuses_without_calling_it_thin() -> None:
    """The refusal must not assert illiquidity it has not measured."""
    result = _screen("500000", scope=False)
    assert "liquidity_unmeasured" in _tags(result)
    assert "liquidity" not in _tags(result)
    line = next(f for f in result.failures if f.startswith("liquidity_unmeasured"))
    assert "alpaca:iex" in line
    assert "unmeasured" in line.lower()
    for asserted in ("illiquid", "too thin", "insufficient liquidity"):
        assert asserted not in line.lower(), f"the refusal asserts {asserted!r}"


def test_the_refusal_names_both_numbers_in_dollars() -> None:
    """An operator has to be able to see how far short of the floor this venue's slice fell --
    $500,000 against $1,000,000 is a different conversation from $50 against $1,000,000."""
    line = next(
        f for f in _screen("500000", scope=False).failures if f.startswith("liquidity_unmeasured")
    )
    assert "500000" in line.replace(",", "")
    assert "1000000" in line.replace(",", "")
    assert "$" in line


def test_the_refusal_says_what_would_resolve_it() -> None:
    line = next(
        f for f in _screen("500000", scope=False).failures if f.startswith("liquidity_unmeasured")
    )
    assert "consolidated" in line.lower()


# --- the consolidated and unrecorded paths ----------------------------------------------------


def test_a_consolidated_feed_below_the_floor_is_still_a_plain_liquidity_failure() -> None:
    """On a feed that sees the whole market, below the floor IS a measurement. Nothing about
    the crypto path changes."""
    result = _screen("500000", scope=True)
    assert "liquidity" in _tags(result)
    assert "liquidity_unmeasured" not in _tags(result)


def test_an_unrecorded_scope_keeps_the_existing_verdict() -> None:
    """Every series cached before #696 has unrecorded provenance. Treating unrecorded as partial
    would refuse the entire existing crypto universe on a technicality about metadata nobody
    wrote down -- so the verdict is unchanged, and the line says the scope is unrecorded."""
    result = _screen("500000", scope=None)
    assert "liquidity" in _tags(result)
    assert "liquidity_unmeasured" not in _tags(result)
    line = next(f for f in result.failures if f.startswith("liquidity"))
    assert "unrecorded" in line.lower()


def test_an_unrecorded_scope_above_the_floor_is_admitted() -> None:
    assert "liquidity" not in _tags(_screen("5000000", scope=None))


# --- how the new tag behaves in the rest of the module ----------------------------------------


def test_the_new_tag_is_data_derived() -> None:
    """It reports on OUR cache and OUR feed, never on the asset -- the same reason `liquidity`
    and `history` are suppressed when there are no bars at all."""
    assert "liquidity_unmeasured" in DATA_DERIVED_FAILURES


def test_the_new_tag_is_not_waivable_yet() -> None:
    """A human volume attestation is a reasonable future route and is deliberately NOT here.
    The module says expanding this set is a decision, not a default, and no attestation record
    exists for volume today -- a waiver with nothing to record would be a rubber stamp."""
    assert "liquidity_unmeasured" not in WAIVABLE_CRITERIA
