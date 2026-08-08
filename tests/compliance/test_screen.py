"""Allowlist admission screening (KB §28.4, §65.5, §67.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.compliance.screen import (
    AssetAttestation,
    MarketFacts,
    ScreenPolicy,
    missing_history_lines,
    screen_asset,
    split_failures,
)


def _facts(asset="BTC", bars=2000, volume="50000000", quotable=True, product=None) -> MarketFacts:
    return MarketFacts(
        asset=asset,
        daily_bars=bars,
        median_daily_volume=Decimal(volume),
        quotable_in_settlement_currency=quotable,
        product_id=product if product is not None else f"{asset}-USD",
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
    assert screen_asset(_facts(bars=200, volume="5"), _attestation(), lenient).admitted is True


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
        _facts(bars=400), _attestation(), waived={"history": "PAXG: 441 bars, human-reviewed"}
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
    result = screen_asset(_facts(bars=2000), _attestation(), waived={"history": "stale reason"})
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
    assert [c.asset for c in found] == ["SOL"]


def test_discovery_drops_the_wrong_quote_currency():
    from keel.compliance.screen import discover_candidates

    assert discover_candidates([_product(quote="USDC")]) == []
    assert discover_candidates([_product(quote="BTC")]) == []


def test_discovery_drops_untradable_products():
    from keel.compliance.screen import discover_candidates

    for kwargs in (
        {"status": "offline"},
        {"trading_disabled": True},
        {"is_disabled": True},
        {"view_only": True},
    ):
        assert discover_candidates([_product(**kwargs)]) == [], kwargs


def test_discovery_drops_thin_products():
    from keel.compliance.screen import discover_candidates

    assert discover_candidates([_product(volume="100")]) == []


def test_discovery_survives_a_malformed_volume_rather_than_crashing():
    from keel.compliance.screen import discover_candidates

    assert discover_candidates([_product(volume=None)]) == []
    assert discover_candidates([_product(volume="n/a")]) == []


def test_discovery_excludes_assets_we_already_hold():
    from keel.compliance.screen import discover_candidates

    found = discover_candidates(
        [_product("BTC-USD"), _product("SOL-USD")], exclude_assets=frozenset({"BTC"})
    )
    assert [c.asset for c in found] == ["SOL"]


def test_discovery_ranks_by_liquidity():
    from keel.compliance.screen import discover_candidates

    found = discover_candidates(
        [_product("A-USD", volume="10000000"), _product("B-USD", volume="90000000")]
    )
    assert [c.asset for c in found] == ["B", "A"]


def test_discovery_proposes_but_never_admits():
    """A discovered candidate is still REJECTED by the screen until a human attests it."""
    from keel.compliance.screen import discover_candidates

    (candidate,) = discover_candidates([_product()])
    result = screen_asset(_facts(asset=candidate.asset), None)
    assert result.admitted is False


def test_discovery_matches_the_quote_currency_case_insensitively():
    """`quote_currency: usd` must not silently propose nothing while the screen accepts the same
    product -- the two comparisons have to agree."""
    from keel.compliance.screen import DiscoveryPolicy, discover_candidates

    lowercase_venue = _product(pid="SOL-USD", quote="usd")
    assert discover_candidates([lowercase_venue]), "lowercase venue quote id dropped everything"
    assert discover_candidates(
        [_product(pid="SOL-USD", quote="USD")], DiscoveryPolicy(quote_currency="usd")
    ), "lowercase configured quote currency dropped everything"


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
