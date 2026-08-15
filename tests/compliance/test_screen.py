"""Allowlist admission screening (KB §28.4, §65.5, §67.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.compliance.screen import (
    KNOWN_WRAPPERS,
    WAIVABLE_CRITERIA,
    AssetAttestation,
    InstrumentAttestation,
    MarketFacts,
    ScreenPolicy,
    missing_history_lines,
    screen_asset,
    split_failures,
)

_VENUE = "coinbase"


def _facts(
    asset="BTC", bars=2000, volume="50000000", quotable=True, product=None, venue=_VENUE
) -> MarketFacts:
    return MarketFacts(
        asset=asset,
        daily_bars=bars,
        median_daily_volume=Decimal(volume),
        quotable_in_settlement_currency=quotable,
        product_id=product if product is not None else f"{asset}-USD",
        venue=venue,
    )


def _instrument(venue=_VENUE, product="BTC-USD", wrapper="spot", source="venue product spec"):
    return InstrumentAttestation(
        venue=venue,
        product_id=product,
        wrapper=wrapper,
        source=source,
        attested_by="tester",
        attested_at=1_784_505_600,
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
    result = screen_asset(_facts(), _attestation(), instrument=_instrument())
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

    This test's INTENT is the short-circuit -- that `haram_sector`, `riba_yield` and `backing`
    produce no verdict at all when there is no attestation to judge. It used to assert that as
    "exactly one failure", which was a proxy that stopped being true once a second, independent
    missing-claim criterion existed. The intent is now asserted directly, by tag: no shariah tag
    appears. (Grepping the message TEXT for 'backing' would false-positive -- the
    missing-attestation message legitimately mentions backing while explaining why it cannot be
    judged, which is exactly why the original test counted instead.)

    Both missing-class failures are expected together, and that is deliberate rather than
    tolerated: an operator who runs `keel assets screen` once should be told BOTH claims they owe
    -- the underlying's and the listing's -- not discover the second only after recording the
    first and re-running.
    """
    result = screen_asset(_facts(), None, instrument=None)
    tags = [f.split(":")[0] for f in result.failures]
    assert "haram_sector" not in tags
    assert "riba_yield" not in tags
    assert "backing" not in tags
    assert sorted(tags) == ["attestation", "instrument_wrapper"]


def test_an_admitted_underlying_does_not_admit_an_unstated_wrapper():
    """Issue #202, the whole point: an honest attestation about BTC does not say what a BTC
    listing IS.

    `sector=payments, backing=native, pays_yield=False` is a true claim about the underlying, and
    it is equally true of spot BTC and of a BTC CFD -- swap financing, leverage and counterparty
    exposure are properties of the CONTRACT, not of the coin. So an asset attestation alone must
    not be able to admit anything, and with no instrument statement on file the screen fails
    closed exactly as it does for a missing asset attestation.
    """
    result = screen_asset(_facts(), _attestation())
    assert result.admitted is False
    assert any(f.startswith("instrument_wrapper:") for f in result.failures)


def test_a_cfd_on_an_admitted_underlying_is_refused():
    """Issue #202's ACCEPTANCE case, and the one that used to pass silently.

    A cTrader-style CFD spells itself `BTC-USD` -- identical to Coinbase's spot id, so the shape
    check passes it -- and its underlying's honest attestation is BTC's existing admitted one. It
    is the wrapper claim, and nothing else in this module, that refuses it.
    """
    facts = _facts(venue="ctrader")
    result = screen_asset(
        facts,
        _attestation(),
        instrument=_instrument(venue="ctrader", wrapper="cfd"),
    )
    assert result.admitted is False
    assert any(f.startswith("instrument_wrapper: 'cfd'") for f in result.failures)
    # The shape check is NOT what caught it -- the id is a well-formed spot id, which is the gap.
    assert not any(f.startswith("spot_instrument") for f in result.failures)


def test_an_attested_spot_listing_is_admitted():
    result = screen_asset(_facts(), _attestation(), instrument=_instrument(wrapper="spot"))
    assert result.admitted is True
    assert result.failures == []


@pytest.mark.parametrize("wrapper", sorted(KNOWN_WRAPPERS - {"spot"}))
def test_no_known_wrapper_other_than_spot_admits(wrapper):
    """Every name in the vocabulary except `spot` is a refusal. The vocabulary exists so that
    refusing is an explicit classification, not so that some of its entries are tolerated."""
    result = screen_asset(_facts(), _attestation(), instrument=_instrument(wrapper=wrapper))
    assert result.admitted is False
    assert any(f.startswith(f"instrument_wrapper: {wrapper!r}") for f in result.failures)


def test_an_unknown_wrapper_must_be_classified_not_assumed():
    """Mirrors the unknown-backing branch: an unrecognised name is not a pass."""
    result = screen_asset(
        _facts(), _attestation(), instrument=_instrument(wrapper="probably spot")
    )
    assert result.admitted is False
    assert any("instrument_wrapper" in f and "not one of" in f for f in result.failures)


@pytest.mark.parametrize("wrapper", ["  SPOT ", "Spot", "spot\n"])
def test_the_wrapper_is_normalised_before_it_is_judged(wrapper):
    """Same `.strip().lower()` treatment `sector` and `backing` already get -- a stored 'SPOT'
    must not read as an unknown wrapper and reject an honestly-attested spot listing."""
    result = screen_asset(_facts(), _attestation(), instrument=_instrument(wrapper=wrapper))
    assert result.admitted is True


def test_a_statement_about_a_different_venue_is_no_evidence_about_this_one():
    """The identity check. A `BTC-USD` spot claim made about Coinbase says nothing about
    `BTC-USD` on a CFD broker -- treating it as evidence is precisely the confusion #202 is
    about, so a mismatch is handled as ABSENCE and fails closed."""
    result = screen_asset(
        _facts(venue="ctrader"),
        _attestation(),
        instrument=_instrument(venue="coinbase", wrapper="spot"),
    )
    assert result.admitted is False
    assert any("instrument_wrapper: UNATTESTED" in f for f in result.failures)
    assert any("'ctrader'" in f for f in result.failures)


def test_a_statement_about_a_different_product_is_no_evidence_about_this_one():
    """The same identity check on the other half of the key: Coinbase lists both `BTC-USD` and
    `BTC-PERP-USD`, so a spot claim about one must not travel to the other."""
    result = screen_asset(
        _facts(product="BTC-PERP-USD"),
        _attestation(),
        instrument=_instrument(product="BTC-USD", wrapper="spot"),
    )
    assert result.admitted is False
    assert any("instrument_wrapper: UNATTESTED" in f for f in result.failures)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_unsourced_instrument_attestation_is_refused(blank):
    """Mirrors the unsourced asset-attestation guard: 'spot, but nobody said where that came
    from' is an unsourced claim, and an unsourced claim is not evidence."""
    result = screen_asset(
        _facts(), _attestation(), instrument=_instrument(wrapper="spot", source=blank)
    )
    assert result.admitted is False
    assert any("instrument_wrapper: no source recorded" in f for f in result.failures)


def test_a_derivative_shaped_id_attested_as_spot_fails_BOTH_checks():
    """The two instrument criteria are complementary, and neither is allowed to hide behind the
    other. A venue whose id says `BTC-PERP-USD` while a human attests `spot` is two separate
    problems -- the shape and the claim disagree -- and the operator should see both."""
    result = screen_asset(
        _facts(product="BTC-PERP-USD"),
        _attestation(),
        instrument=_instrument(product="BTC-PERP-USD", wrapper="spot"),
    )
    assert result.admitted is False
    assert any(f.startswith("spot_instrument") for f in result.failures)


def test_instrument_wrapper_is_never_waivable():
    """Issue #202 is explicit that no criterion it adds may be waivable, and `WAIVABLE_CRITERIA`
    is pinned here rather than merely spot-checked: a documented exception permitting a
    derivative would waive the charter, not a threshold."""
    assert WAIVABLE_CRITERIA == frozenset({"history"})


def test_a_stray_waiver_for_instrument_wrapper_is_ignored_and_fails_closed():
    """Defence in depth for the pin above: even if a row reached `screen_exceptions` by hand,
    the up-front `WAIVABLE_CRITERIA` filter drops it before any branch can read it."""
    result = screen_asset(
        _facts(venue="ctrader"),
        _attestation(),
        waived={"instrument_wrapper": "someone tried to waive this"},
        instrument=_instrument(venue="ctrader", wrapper="cfd"),
    )
    assert result.admitted is False
    assert any(f.startswith("instrument_wrapper: 'cfd'") for f in result.failures)
    assert not any("WAIVED" in w for w in result.warnings)


def test_the_wrapper_verdict_is_still_assessable_at_zero_bars():
    """Like `settlement`, this criterion reads an attestation and never touches candles, so it
    stays a real verdict about the LISTING even with an empty cache -- it must not be suppressed
    as "about our data" the way `history`/`liquidity` legitimately are."""
    from keel.compliance.screen import DATA_DERIVED_FAILURES

    assert "instrument_wrapper" not in DATA_DERIVED_FAILURES
    facts = _facts(bars=0, volume="0")
    result = screen_asset(facts, _attestation(), instrument=None)
    about_the_asset, about_our_cache = split_failures(facts, result)
    assert any(f.startswith("instrument_wrapper") for f in about_the_asset)
    assert not any(f.startswith("instrument_wrapper") for f in about_our_cache)


def test_the_unattested_message_names_the_command_that_fixes_it():
    """The fail-closed default REJECTs every product until the operator attests it, so the
    failure has to carry the exact remedy -- that message is how they are told."""
    result = screen_asset(_facts(), _attestation(), instrument=None)
    assert any("keel assets attest-instrument" in f for f in result.failures)


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
    result = screen_asset(
        _facts(asset="PAXG"),
        _attestation(asset="PAXG", backing="ayn"),
        instrument=_instrument(product="PAXG-USD"),
    )
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


def test_an_instrument_that_is_not_a_spot_pair_is_rejected():
    """The criterion `assets screen` was missing (feasibility study R2).

    Settlement was the screen's ONLY id-derived criterion, and a derivative-shaped id whose last
    segment is a legitimate settlement currency passes it -- `quote_currency_of("BTC-PERP-USD")`
    is `"USD"`. So the command whose whole job is answering "may keel trade this" said ADMIT
    about the one product shape rail 19 was built to refuse.
    """
    result = screen_asset(_facts(product="BTC-PERP-USD"), _attestation())
    assert result.admitted is False
    assert any(f.startswith("spot_instrument") for f in result.failures)
    assert any("BTC-PERP-USD" in f for f in result.failures), "the verdict must name the id"


@pytest.mark.parametrize(
    "product",
    [
        "BTC-PERP-USD",  # the R2 residual: derivative-shaped, USD-settled
        "ADA-28AUG26-CDE",  # futures
        "ac568fb9e6c5a67da94f065a49fb7b0c59b7b258cfdf0a3b1560849071c3b05e",  # equity hash
        "btc-usd",  # lowercase: not the id the venue lists
        "BTCUSD",
        "",
    ],
)
def test_the_shape_criterion_uses_rail_19s_grammar_not_a_second_copy(product):
    """One grammar, so the screen and the rail cannot disagree about what a spot id is: an id
    the screen ADMITs but rail 19 vetoes is the worst possible answer to "may keel trade this"."""
    result = screen_asset(_facts(product=product), _attestation())
    assert any(f.startswith("spot_instrument") for f in result.failures), product


def test_the_spot_instrument_verdict_survives_a_missing_attestation():
    """It is a market fact, computed before the attestation early-return, so an unattested
    derivative reports BOTH reasons rather than only the shariah one."""
    result = screen_asset(_facts(product="BTC-PERP-USD"), None)
    assert any(f.startswith("spot_instrument") for f in result.failures)
    assert any(f.startswith("attestation") for f in result.failures)


def test_the_spot_instrument_criterion_can_never_be_waived():
    """`WAIVABLE_CRITERIA` is `{"history"}`; spot-only is this agent's charter, not a threshold.
    A stray exception row naming it must be dropped by the up-front filter like any other."""
    result = screen_asset(
        _facts(product="BTC-PERP-USD"),
        _attestation(),
        waived={"spot_instrument": "we really want it"},
    )
    assert result.admitted is False
    assert any(f.startswith("spot_instrument") for f in result.failures)


def test_the_spot_instrument_failure_is_assessable_with_zero_cached_bars():
    """Like `settlement`, it reads the product id and never touches candles -- so it must NOT be
    in `DATA_DERIVED_FAILURES`, or `assets holdings` would suppress a real verdict."""
    from keel.compliance.screen import DATA_DERIVED_FAILURES

    assert "spot_instrument" not in DATA_DERIVED_FAILURES
    result = screen_asset(_facts(bars=0, volume="0", product="BTC-PERP-USD"), _attestation())
    assert any(f.startswith("spot_instrument") for f in result.failures)


def test_every_failure_is_reported_not_just_the_first():
    """A screening report that stops at the first problem wastes a round trip."""
    result = screen_asset(
        _facts(bars=10, volume="1", quotable=False, product="BTC-PERP-EUR"),
        _attestation(sector="gambling", backing="dayn", yield_=True),
    )
    assert len(result.failures) >= 7


def test_policy_thresholds_are_configurable():
    lenient = ScreenPolicy(min_daily_bars=100, min_median_daily_volume=Decimal("1"))
    result = screen_asset(
        _facts(bars=200, volume="5"), _attestation(), lenient, instrument=_instrument()
    )
    assert result.admitted is True


# -- documented allowlist-screen exceptions (waivers) --------------------------
#
# Motivating case: PAXG passes shariah/liquidity screening but fails the 4-year history floor
# (441 bars < 1460). A human can record a DOCUMENTED exception that waives ONLY the `history`
# criterion -- surfaced loudly as a warning, never a silent exemption -- and only for criteria in
# `WAIVABLE_CRITERIA`, so the shariah core can never be bypassed this way.


def test_insufficient_history_with_no_waiver_still_rejects():
    result = screen_asset(_facts(bars=400), _attestation())
    assert result.admitted is False
    assert any("history" in f for f in result.failures)


def test_a_documented_history_waiver_admits_and_warns_loudly():
    result = screen_asset(
        _facts(bars=400),
        _attestation(),
        waived={"history": "PAXG: 441 bars, human-reviewed"},
        instrument=_instrument(),
    )
    assert result.admitted is True
    assert not any("history" in f for f in result.failures)
    assert any(
        "WAIVED" in w and "PAXG: 441 bars, human-reviewed" in w for w in result.warnings
    )


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_rationale_waiver_does_not_admit_undocumented_is_not_documented(blank):
    """The whole thesis is 'documented, never silent'. A blank rationale is not documentation,
    so it must fail closed exactly like an unsourced attestation does."""
    result = screen_asset(_facts(bars=400), _attestation(), waived={"history": blank})
    assert result.admitted is False
    assert any("history" in f for f in result.failures)
    assert not any("WAIVED" in w for w in result.warnings)


def test_a_waiver_is_self_retiring_once_history_clears_the_floor():
    """No leftover warning once the underlying condition it was granted for no longer holds."""
    result = screen_asset(
        _facts(bars=2000), _attestation(), waived={"history": "stale reason"},
        instrument=_instrument(),
    )
    assert result.admitted is True
    assert not any("WAIVED" in w for w in result.warnings)
    assert not any("history" in f for f in result.failures)


def test_a_waiver_for_a_non_waivable_criterion_is_ignored_and_fails_closed():
    """SAFETY, non-vacuous: `bars` is BELOW the floor, so the history branch is actually
    entered -- if the `WAIVABLE_CRITERIA` filter were broken, an attestation-keyed waiver could
    only matter here if it somehow leaked into the history check too, which this also rules out.
    A stray `screen_exceptions` row for a shariah criterion must never bypass it, and must not
    incidentally waive history either (no "history" key was ever granted)."""
    result = screen_asset(
        _facts(bars=400), None, waived={"attestation": "someone tried to waive this"}
    )
    assert result.admitted is False
    assert any("attestation: MISSING" in f for f in result.failures)
    assert any("history" in f for f in result.failures)  # NOT waived -- no "history" key granted
    assert not any("WAIVED" in w for w in result.warnings)


def test_a_non_waivable_key_does_not_rescue_the_asset_it_was_stray_recorded_on():
    """Same shape as above, but on an asset that is otherwise CLEAN except for low history: an
    `attestation`-keyed waiver (never granted for `history`) must still leave history REJECTED."""
    result = screen_asset(_facts(bars=400), _attestation(), waived={"attestation": "x"})
    assert result.admitted is False
    assert any("history" in f for f in result.failures)
    assert not any("WAIVED" in w for w in result.warnings)


def test_a_stray_non_waivable_key_alongside_a_real_waiver_is_dropped_not_honored():
    """The up-front filter drops non-`WAIVABLE_CRITERIA` keys one at a time -- a `settlement`
    entry riding along with a legitimate `history` waiver must have zero effect."""
    result = screen_asset(
        _facts(bars=400),
        _attestation(),
        waived={"history": "documented reason", "settlement": "someone tried to waive this too"},
        instrument=_instrument(),
    )
    assert result.admitted is True
    assert any("WAIVED" in w and "documented reason" in w for w in result.warnings)
    assert not any("settlement" in w for w in result.warnings)
    assert result.failures == []


def test_a_history_waiver_does_not_rescue_a_different_real_failure():
    """The waiver is scoped to history alone -- it must not paper over an unrelated rejection."""
    result = screen_asset(
        _facts(bars=400), None, waived={"history": "reason"}
    )
    assert result.admitted is False
    assert any("attestation: MISSING" in f for f in result.failures)


def test_a_history_waiver_does_not_rescue_a_dayn_backing_failure():
    result = screen_asset(
        _facts(bars=400),
        _attestation(backing="dayn"),
        waived={"history": "reason"},
    )
    assert result.admitted is False
    assert any("dayn" in f for f in result.failures)
    assert not any("history" in f for f in result.failures)


# -- discovery (proposal stage) ------------------------------------------------


def _product(pid="SOL-USD", quote="USD", volume="50000000", **over):
    base = {
        "product_id": pid,
        "base_name": pid.split("-")[0],
        "quote_currency_id": quote,
        "status": "online",
        "trading_disabled": False,
        "is_disabled": False,
        "view_only": False,
        "quote_24h_volume": volume,
    }
    base.update(over)
    return base


def test_discovery_keeps_liquid_online_products_in_the_settlement_currency():
    from keel.compliance.screen import discover_candidates

    found = discover_candidates([_product()])
    assert [c.asset for c in found.candidates] == ["SOL"]


def test_discovery_drops_the_wrong_quote_currency():
    from keel.compliance.screen import discover_candidates

    assert discover_candidates([_product(quote="USDC")]).candidates == ()
    assert discover_candidates([_product(quote="BTC")]).candidates == ()


def test_discovery_drops_untradable_products():
    from keel.compliance.screen import discover_candidates

    for kwargs in (
        {"status": "offline"},
        {"trading_disabled": True},
        {"is_disabled": True},
        {"view_only": True},
    ):
        assert discover_candidates([_product(**kwargs)]).candidates == (), kwargs


def test_discovery_drops_thin_products():
    from keel.compliance.screen import discover_candidates

    assert discover_candidates([_product(volume="100")]).candidates == ()


def test_discovery_survives_a_malformed_volume_rather_than_crashing():
    from keel.compliance.screen import discover_candidates

    assert discover_candidates([_product(volume=None)]).candidates == ()
    assert discover_candidates([_product(volume="n/a")]).candidates == ()


def test_discovery_treats_nan_volume_as_unreadable_rather_than_crashing():
    """`Decimal("NaN")` parses cleanly -- the `try/except` around the parse does not catch it --
    and then `volume < policy.min_quote_24h_volume` raises `decimal.InvalidOperation`, which used
    to crash the whole sweep on a single bad venue row. A NaN is exactly as uninformative about
    liquidity as an unparseable string, so it must land in the same `unreadable_volume` bucket,
    not blow up the command. Covers a string `"NaN"`, a `float("nan")` (the `str()` call ahead of
    `Decimal(...)` still produces the string `"nan"`), and the signaling `"sNaN"` form."""
    from keel.compliance.screen import discover_candidates

    for nan_volume in ("NaN", "nan", float("nan"), "sNaN"):
        result = discover_candidates([_product(volume=nan_volume)])
        assert result.candidates == (), nan_volume
        assert result.excluded.unreadable_volume == 1, nan_volume
        assert result.excluded.below_volume_floor == 0, nan_volume


def test_discovery_treats_infinite_volume_as_unreadable_not_a_credible_candidate():
    """`Decimal("Infinity")` also parses cleanly and compares fine against the floor, so left
    unguarded it would silently become a candidate. A venue reporting infinite 24h volume is not
    credible data -- it is a malformed feed -- so it is counted `unreadable_volume`, the same
    fail-closed bucket as any other value this module cannot trust, rather than treated as
    "definitely liquid"."""
    from keel.compliance.screen import discover_candidates

    result = discover_candidates([_product(volume="Infinity")])
    assert result.candidates == ()
    assert result.excluded.unreadable_volume == 1
    assert result.excluded.below_volume_floor == 0


def test_discovery_excludes_assets_we_already_hold():
    from keel.compliance.screen import discover_candidates

    found = discover_candidates(
        [_product("BTC-USD"), _product("SOL-USD")], exclude_assets=frozenset({"BTC"})
    )
    assert [c.asset for c in found.candidates] == ["SOL"]


def test_discovery_ranks_by_liquidity():
    from keel.compliance.screen import discover_candidates

    found = discover_candidates(
        [_product("A-USD", volume="10000000"), _product("B-USD", volume="90000000")]
    )
    assert [c.asset for c in found.candidates] == ["B", "A"]


def test_discovery_proposes_but_never_admits():
    """A discovered candidate is still REJECTED by the screen until a human attests it."""
    from keel.compliance.screen import discover_candidates

    (candidate,) = discover_candidates([_product()]).candidates
    result = screen_asset(_facts(asset=candidate.asset), None)
    assert result.admitted is False


def test_discovery_matches_the_quote_currency_case_insensitively():
    """`quote_currency: usd` must not silently propose nothing while the screen accepts the same
    product -- the two comparisons have to agree."""
    from keel.compliance.screen import DiscoveryPolicy, discover_candidates

    lowercase_venue = _product(pid="SOL-USD", quote="usd")
    assert discover_candidates(
        [lowercase_venue]
    ).candidates, "lowercase venue quote id dropped everything"
    assert discover_candidates(
        [_product(pid="SOL-USD", quote="USD")], DiscoveryPolicy(quote_currency="usd")
    ).candidates, "lowercase configured quote currency dropped everything"


def test_a_quiet_days_snapshot_no_longer_hides_an_asset_the_gate_would_admit():
    """The 2026-08-15 regression this default change fixes.

    `--min-volume-24h` used to default to `Decimal("1000000")`, pinned EQUAL to the admission
    floor `ScreenPolicy.min_median_daily_volume`. That equality does not make the pre-filter
    non-stricter than the gate it feeds, because the two sides measure DIFFERENT statistics:
    discovery's `quote_24h_volume` is a single 24-hour venue snapshot, while the gate's
    `median_daily_volume` is the median of volume x close over ALL cached history. A quiet
    trading day can push the snapshot below a floor the asset clears comfortably on a typical
    day, and an equal number does nothing to prevent that.

    Measured on 2026-08-15, five assets were silently dropped by discovery at the old
    1,000,000 floor despite the gate's own statistic sitting far above the admission floor:
    ATOM 3,077,474 (3.08x), AAVE 6,315,463 (6.32x), BCH 5,464,940 (5.46x), CRV 3,329,753
    (3.33x) and ALGO 3,780,207 (3.78x). Four of the five -- ATOM, AAVE, BCH, CRV -- had 24h
    snapshots clustered between 852,133 and 979,000 on that single quiet day, comfortably below
    the old floor while their median-over-history liquidity was multiples of the admission
    requirement. ALGO was NOT part of that cluster: it was a separate, lower outlier at
    `437,712` (`430,520` measured again an hour later) -- the lowest 24h snapshot of the five,
    and the one that most tightly constrains how low this floor may safely sit.

    This test pins ALGO's actual measured 24h figure, `437712`, not the cluster's -- the LOWEST
    measured value in the incident, not the highest. That is deliberate: a regression test that
    instead pinned the top of the cluster (`852133`) would keep passing under a floor as high as
    500,000, which would silently re-hide ALGO while looking green. `437712` is well under the
    OLD floor of 1,000,000 but above the new default of 100,000. Under the default
    `DiscoveryPolicy()` it must survive the pre-filter, so the sweep can no longer hide an asset
    the gate would admit. (Verified separately: a floor of 500,000 fails this test once it is
    pinned to `437712`, and would NOT have failed it pinned to `852133`.)
    """
    from keel.compliance.screen import discover_candidates

    found = discover_candidates([_product(pid="ALGO-USD", volume="437712")])

    assert [c.asset for c in found.candidates] == ["ALGO"]


def test_discovery_counts_every_exclusion_by_reason():
    """`discover_candidates` used to drop excluded products with a bare `continue`, so nothing
    recorded WHY a product was excluded or how many were. That made a quiet-day floor problem
    (see `test_a_quiet_days_snapshot_no_longer_hides_an_asset_the_gate_would_admit`) invisible
    in the sweep's own output -- an operator watching `900 -> 40` had no way to tell whether the
    missing 860 were junk (wrong quote currency, offline, disabled) or real candidates sitting
    just under the floor.

    Exactly ONE product is fed per reason, first-match-wins in the declaration order of
    `DiscoveryExclusions`, plus one clean survivor -- so every count below is 1, the total is the
    number of reasons, and a product excluded for one reason is pinned NOT to be double-counted
    against a later check it would also fail. The `trading_disabled` reason covers two venue
    flags (`trading_disabled` and `is_disabled`) and is therefore fed only its first variant
    here; that the second lands in the same bucket rather than a reason of its own is pinned
    separately by `test_discovery_counts_both_trading_disabled_flag_variants_together`, which
    keeps this test's one-product-per-reason arithmetic honest.
    """
    from keel.compliance.screen import discover_candidates

    products = [
        _product("WRONGQ-USD", quote="EUR"),
        _product("OFFLINE-USD", status="offline"),
        _product("HALTED-USD", trading_disabled=True),
        _product("VIEWONLY-USD", view_only=True),
        _product("BTC-USD"),  # excluded via `exclude_assets` below: already on the allowlist
        _product("BADVOL-USD", volume="not-a-number"),
        _product("THIN-USD", volume="1"),
        _product("SOL-USD"),  # the one survivor
    ]

    result = discover_candidates(products, exclude_assets=frozenset({"BTC"}))

    assert result.excluded.wrong_quote_currency == 1
    assert result.excluded.not_online == 1
    assert result.excluded.trading_disabled == 1
    assert result.excluded.view_only == 1
    assert result.excluded.already_on_allowlist == 1
    assert result.excluded.unreadable_volume == 1
    assert result.excluded.below_volume_floor == 1
    assert result.excluded.total == 7
    assert [c.asset for c in result.candidates] == ["SOL"]


def test_discovery_survivors_plus_excluded_always_account_for_every_product():
    """The invariant `len(candidates) + excluded.total == len(products)` that
    `render_discover_report` (`keel/commands/admission.py`) leans on to derive its displayed
    survivor count via subtraction (`venue_product_count - excluded.total`) instead of carrying
    a redundant field. Nothing previously asserted this identity directly -- only that individual
    counts landed in the right buckets -- so a future change that drops a product on the floor
    (double-counts it, or skips it) without incrementing any `excluded` field or appending to
    `candidates` would go unnoticed here and would render nonsense downstream.

    Uses the same one-product-per-reason mix as
    `test_discovery_counts_every_exclusion_by_reason`, plus a SECOND survivor, so the identity is
    checked with more than one candidate on each side of the equation."""
    from keel.compliance.screen import discover_candidates

    products = [
        _product("WRONGQ-USD", quote="EUR"),
        _product("OFFLINE-USD", status="offline"),
        _product("HALTED-USD", trading_disabled=True),
        _product("VIEWONLY-USD", view_only=True),
        _product("BTC-USD"),  # already on the allowlist
        _product("BADVOL-USD", volume="not-a-number"),
        _product("THIN-USD", volume="1"),
        _product("SOL-USD"),  # survivor 1
        _product("ETH-USD"),  # survivor 2
    ]

    result = discover_candidates(products, exclude_assets=frozenset({"BTC"}))

    assert len(result.candidates) + result.excluded.total == len(products)


def test_discovery_counts_both_trading_disabled_flag_variants_together():
    """`trading_disabled` covers BOTH the `trading_disabled` and `is_disabled` product flags --
    feeding one of each must total 2 under the single `trading_disabled` reason, not split
    across a reason that does not exist."""
    from keel.compliance.screen import discover_candidates

    products = [
        _product("HALTED-USD", trading_disabled=True),
        _product("DISABLED-USD", is_disabled=True),
    ]

    result = discover_candidates(products)

    assert result.excluded.trading_disabled == 2
    assert result.excluded.total == 2
    assert result.candidates == ()


def test_discovery_exclusions_summary_line_names_every_reason_and_the_total():
    from keel.compliance.screen import DiscoveryExclusions

    exclusions = DiscoveryExclusions(
        wrong_quote_currency=1,
        not_online=1,
        trading_disabled=1,
        view_only=1,
        already_on_allowlist=1,
        unreadable_volume=1,
        below_volume_floor=1,
    )

    line = exclusions.summary_line()

    assert line == (
        "excluded 7: wrong quote currency 1, not online 1, trading disabled 1, view only 1, "
        "already on allowlist 1, unreadable 24h volume 1, below 24h volume floor 1"
    )


# -- split_failures / missing_history_lines -------------------------------------------------
#
# The single source of truth for "is a zero-bar `history` failure a lie about the asset, or a
# fact about our cache" now lives here, not duplicated per-caller (`assets holdings`, `assets
# propose`, and the TUI to come). These tests pin the split and the wording directly, so a
# regression shows up here rather than as a re-appeared `✗ history: 0 daily bars` line three
# call sites away.


def test_split_failures_leaves_a_nonzero_history_asset_entirely_unsplit():
    """With ANY cached bars, every failure -- including a genuine `history` shortfall for a
    young asset -- is a real verdict about the asset. Nothing is downstream of the cache."""
    facts = _facts(bars=400)
    result = screen_asset(facts, _attestation())
    about_asset, about_cache = split_failures(facts, result)
    assert about_asset == result.failures
    assert about_cache == []
    assert any(f.startswith("history") for f in about_asset)


def test_split_failures_splits_only_at_exactly_zero_bars():
    """At zero bars, `history` and `liquidity` are downstream of having no cache -- they measure
    OUR data, not the asset -- so both move to `about_cache`. `settlement` and `attestation`
    keep being real verdicts about the asset regardless of bar count."""
    facts = _facts(bars=0, volume="0", quotable=False)
    result = screen_asset(facts, None)  # unattested too, so `attestation` also fails
    about_asset, about_cache = split_failures(facts, result)

    cache_tags = {f.split(":")[0] for f in about_cache}
    asset_tags = {f.split(":")[0] for f in about_asset}
    assert cache_tags == {"history", "liquidity"}
    assert "settlement" in asset_tags
    assert "attestation" in asset_tags


def test_split_failures_preserves_original_ordering_in_both_lists():
    """Callers render these lists in order; a silent reorder would scramble the report even
    though the same failures are all still present somewhere in it."""
    facts = _facts(bars=0, volume="0", quotable=False)
    result = screen_asset(facts, None)
    about_asset, about_cache = split_failures(facts, result)

    def _positions(subset: list[str]) -> list[int]:
        return [result.failures.index(f) for f in subset]

    assert _positions(about_asset) == sorted(_positions(about_asset))
    assert _positions(about_cache) == sorted(_positions(about_cache))


def test_split_failures_keeps_settlement_assessable_at_zero_bars():
    """`settlement` reads the product id, never a candle, so it must stay a real verdict even
    when there is no cached history at all -- the one criterion this split must NOT catch."""
    facts = _facts(bars=0, volume="0", quotable=False)
    result = screen_asset(facts, _attestation())
    about_asset, about_cache = split_failures(facts, result)
    assert any(f.startswith("settlement") for f in about_asset)
    assert not any(f.startswith("settlement") for f in about_cache)


def test_missing_history_lines_omits_the_third_line_when_nothing_is_suppressed():
    """A candidate that fails on shape/settlement/attestation alone (no derived failures at all)
    gets the two-line MISSING-DATA explanation and nothing more -- a trailing "not assessable
    until then:" with nothing after the colon would be worse than no line at all."""
    lines = missing_history_lines("SOL-USD", [])
    assert len(lines) == 2
    assert "no local history" in lines[0]
    assert "keel fetch --products SOL-USD" in lines[0]
    assert "MISSING-DATA verdict" in lines[1]
    assert not any("not assessable" in line for line in lines)


def test_missing_history_lines_claim_nothing_about_the_assets_age():
    """The SEMANTIC sentence -- the one that says what a zero-bar report actually means -- was
    pinned by nothing. Every test around it asserted proxies at the call sites (`"no local
    history" in text`, `"✗ history" not in text`), so the sentence could be reworded into
    anything and the suite stayed green.

    It used to read "it is not too young, we have simply never fetched candles for it", which
    asserts a fact about the ASSET this function cannot possibly know: at exactly zero bars a
    brand-new listing and a never-fetched veteran are the same input, and `MarketFacts` carries
    no first-bar timestamp to tell them apart. Refusing to rule is the honest position, and it is
    the one the explanation must state."""
    lines = missing_history_lines("SOL-USD", ["history: 0 daily bars < 1460 required"])
    semantic = lines[1]

    assert "MISSING-DATA verdict, not a verdict about the asset" in semantic
    assert "cannot tell" in semantic
    assert "not too young" not in semantic


def test_missing_history_lines_never_restate_a_suppressed_failure_verbatim():
    """These lines are shown INSTEAD OF `not_assessable`, so leaking one back in verbatim would
    reprint the very depth verdict the suppression exists to withhold -- and the call-site proxy
    `"✗ history" not in text` would not catch it, because the leak carries no `✗`. Only the TAGS
    survive, on the third line."""
    suppressed = [
        "history: 0 daily bars < 1460 required",
        "liquidity: median daily volume 0 < 1000000 required",
    ]
    joined = "\n".join(missing_history_lines("SOL-USD", suppressed))

    for failure in suppressed:
        assert failure not in joined
    assert "1460" not in joined
    assert "0 daily bars" not in joined


def test_missing_history_lines_dedupes_and_sorts_tags():
    """Two failures sharing a tag collapse to one mention, and the tags print in a stable,
    predictable order rather than whatever order `screen_asset` happened to emit them in."""
    lines = missing_history_lines(
        "SOL-USD",
        [
            "liquidity: median daily volume 0 < 1000000 required",
            "history: 0 daily bars < 1460 required",
            "liquidity: a second liquidity-tagged failure, hypothetically",
        ],
    )
    assert lines[2] == "not assessable until then: history, liquidity"


# -- median_daily_quote_volume: the ONE definition of the liquidity statistic ------------------


def _vol_candle(volume: str, close: str):
    from keel.types import Candle

    return Candle(
        ts=0,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def test_median_daily_quote_volume_is_the_median_of_volume_times_close():
    """Quote volume, not base: a bar's contribution is `volume * close`.

    `discover`'s pre-filter and `screen`'s criterion must compute the SAME statistic or the
    sweep silently proposes assets the gate then rejects. This is that one definition.
    """
    from keel.compliance.screen import median_daily_quote_volume

    candles = [
        _vol_candle(volume="10", close="1"),  # 10
        _vol_candle(volume="10", close="100"),  # 1000
        _vol_candle(volume="10", close="10"),  # 100  <- median
    ]

    assert median_daily_quote_volume(candles) == Decimal("100")


def test_median_daily_quote_volume_of_no_candles_is_zero():
    """No bars is not high liquidity -- it is no evidence, and the screen treats 0 as failing."""
    from keel.compliance.screen import median_daily_quote_volume

    assert median_daily_quote_volume([]) == Decimal(0)
