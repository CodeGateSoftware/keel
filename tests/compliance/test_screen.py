"""Allowlist admission screening (KB §28.4, §65.5, §67.2)."""

from __future__ import annotations

from decimal import Decimal

from keel.compliance.screen import (
    AssetAttestation,
    MarketFacts,
    ScreenPolicy,
    screen_asset,
)


def _facts(asset="BTC", bars=2000, volume="50000000", quotable=True) -> MarketFacts:
    return MarketFacts(
        asset=asset,
        daily_bars=bars,
        median_daily_volume=Decimal(volume),
        quotable_in_settlement_currency=quotable,
    )


def _attestation(asset="BTC", sector="payments", backing="native", yield_=False, source="s"):
    return AssetAttestation(
        asset=asset,
        sector=sector,
        backing=backing,
        pays_yield=yield_,
        source=source,
        attested_by="tester",
        attested_at=1_784_505_600,
    )


def test_a_clean_asset_is_admitted():
    result = screen_asset(_facts(), _attestation())
    assert result.admitted is True
    assert result.failures == []


# -- fail closed ---------------------------------------------------------------


def test_a_missing_attestation_is_a_REJECTION_not_a_default_pass():
    """The core policy: unknown is not 'probably fine'.

    Sector and backing cannot be derived from candles, so an unattested asset is unknown, and
    unknown fails closed -- mirroring an un-attested venue being `suspect` rather than assumed.
    """
    result = screen_asset(_facts(), None)
    assert result.admitted is False
    assert any("attestation: MISSING" in f for f in result.failures)


def test_a_missing_attestation_short_circuits_the_shariah_checks():
    """No sector/backing VERDICT is invented for an asset nobody has classified.

    Asserted as "exactly one failure, and it is the attestation one" rather than by grepping for
    'backing' -- the missing-attestation message legitimately mentions backing while explaining
    why it cannot be judged.
    """
    result = screen_asset(_facts(), None)
    assert len(result.failures) == 1
    assert result.failures[0].startswith("attestation: MISSING")


def test_an_unsourced_attestation_is_refused():
    result = screen_asset(_facts(), _attestation(source="   "))
    assert result.admitted is False
    assert any("no source recorded" in f for f in result.failures)


# -- §28.4 haram sector --------------------------------------------------------


def test_haram_sectors_are_rejected():
    for sector in ("gambling", "casino", "adult", "alcohol", "tobacco", "firearms", "riba_yield"):
        result = screen_asset(_facts(), _attestation(sector=sector))
        assert result.admitted is False, sector
        assert any("haram_sector" in f for f in result.failures), sector


def test_sector_matching_is_case_and_whitespace_insensitive():
    result = screen_asset(_facts(), _attestation(sector="  GamBling "))
    assert result.admitted is False
    assert any("haram_sector" in f for f in result.failures)


def test_a_yield_bearing_asset_is_rejected_as_riba_like():
    result = screen_asset(_facts(), _attestation(yield_=True))
    assert result.admitted is False
    assert any("riba_yield" in f for f in result.failures)


# -- §65.5 / §67.2 backing -----------------------------------------------------


def test_a_debt_claim_is_rejected():
    result = screen_asset(_facts(), _attestation(backing="dayn"))
    assert result.admitted is False
    assert any("dayn" in f for f in result.failures)


def test_an_unknown_backing_must_be_classified_not_assumed():
    result = screen_asset(_facts(), _attestation(backing="probably fine"))
    assert result.admitted is False
    assert any("not one of" in f for f in result.failures)


def test_asset_backed_is_admitted_but_warns_about_the_stricter_sarf_regime():
    """PAXG's case: admitted, but §65.5's no-deferment rule is surfaced, not buried."""
    result = screen_asset(_facts(asset="PAXG"), _attestation(asset="PAXG", backing="ayn"))
    assert result.admitted is True
    assert any("bay' al-sarf" in w for w in result.warnings)


# -- computed market facts -----------------------------------------------------


def test_insufficient_history_is_rejected():
    result = screen_asset(_facts(bars=400), _attestation())
    assert result.admitted is False
    assert any("history" in f for f in result.failures)


def test_thin_liquidity_is_rejected():
    result = screen_asset(_facts(volume="100"), _attestation())
    assert result.admitted is False
    assert any("liquidity" in f for f in result.failures)


def test_an_asset_not_quotable_in_the_settlement_currency_is_rejected():
    result = screen_asset(_facts(quotable=False), _attestation())
    assert result.admitted is False
    assert any("settlement" in f for f in result.failures)


def test_every_failure_is_reported_not_just_the_first():
    """A screening report that stops at the first problem wastes a round trip."""
    result = screen_asset(
        _facts(bars=10, volume="1", quotable=False),
        _attestation(sector="gambling", backing="dayn", yield_=True),
    )
    assert len(result.failures) >= 6


def test_policy_thresholds_are_configurable():
    lenient = ScreenPolicy(min_daily_bars=100, min_median_daily_volume=Decimal("1"))
    assert screen_asset(_facts(bars=200, volume="5"), _attestation(), lenient).admitted is True
