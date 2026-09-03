"""`doctor` must say when a liquidity number is a bound rather than a measurement -- #696.

The gate refuses honestly now, but only at screen time and only for a candidate someone is
actively screening. An operator running the equities profile day to day never sees it, while
`slippage_for_quote_volume` quietly prices MSFT off a $186M/day statistic on every cycle. This is
the standing report of that: which cached series carry a volume figure that is a LOWER BOUND.

WARN, never FAIL. Running on a single-venue feed is a legitimate configuration -- it is the free
tier, and the asymmetric bound admits the liquid names from it honestly. What is not legitimate is
not knowing.
"""

from __future__ import annotations

from keel.commands.doctor import FAIL, OK, WARN, feed_scope_findings


def test_no_series_at_all_is_ok() -> None:
    """A fresh deployment has nothing to report on, and that is not a warning."""
    (finding,) = feed_scope_findings({})
    assert finding.status == OK


def test_consolidated_series_only_is_ok() -> None:
    (finding,) = feed_scope_findings({("BTC-USD", "ONE_DAY"): ("coinbase",)})
    assert finding.status == OK
    assert "coinbase" in finding.detail or "consolidated" in finding.detail.lower()


def test_a_partial_feed_series_warns_and_names_it() -> None:
    (finding,) = feed_scope_findings(
        {
            ("MSFT-USD", "ONE_DAY"): ("alpaca:iex",),
            ("BTC-USD", "ONE_DAY"): ("coinbase",),
        }
    )
    assert finding.status == WARN
    assert "MSFT-USD" in finding.detail
    assert "alpaca:iex" in finding.detail
    assert "BTC-USD" not in finding.detail, "a consolidated series is not a finding"


def test_the_warning_says_the_number_is_a_bound_not_that_the_asset_is_thin() -> None:
    (finding,) = feed_scope_findings({("MSFT-USD", "ONE_DAY"): ("alpaca:iex",)})
    text = f"{finding.headline} {finding.detail}".lower()
    assert "lower bound" in text
    for asserted in ("illiquid", "too thin"):
        assert asserted not in text


def test_it_never_fails_only_warns() -> None:
    """A free data tier is a legitimate configuration. FAIL would make it a fault."""
    findings = feed_scope_findings({("MSFT-USD", "ONE_DAY"): ("alpaca:iex",)})
    assert all(f.status != FAIL for f in findings)


def test_an_unrecorded_series_is_reported_separately_from_a_partial_one() -> None:
    """Unrecorded is the absence of evidence, partial is a known limitation. Reporting them the
    same way would tell an operator to re-fetch a series that may already be consolidated."""
    (finding,) = feed_scope_findings({("ETH-USD", "ONE_DAY"): ()})
    assert finding.status == WARN
    assert "unrecorded" in finding.detail.lower()
    assert "lower bound" not in finding.detail.lower()


def test_a_mixed_series_is_reported_as_partial() -> None:
    (finding,) = feed_scope_findings(
        {("MSFT-USD", "ONE_DAY"): ("alpaca:iex", "alpaca:sip")}
    )
    assert finding.status == WARN
    assert "alpaca:iex" in finding.detail


def test_the_fix_line_says_the_re_fetch_must_be_under_a_different_feed() -> None:
    """Naming `keel fetch` is not enough -- re-fetching under the SAME feed changes nothing, and
    an operator who does it will get the identical warning and conclude the report is broken.
    The fix has to say *consolidated*.

    A surviving mutant put this here: replacing the explanatory half with "ask someone" left the
    command intact, so an assertion that merely looked for "keel" stayed green."""
    (finding,) = feed_scope_findings({("MSFT-USD", "ONE_DAY"): ("alpaca:iex",)})
    assert "keel fetch" in finding.fix
    assert "consolidated" in finding.fix.lower()


# --- the two states coexist, because that IS this deployment --------------------------------


def test_partial_and_unrecorded_are_both_reported_when_both_exist() -> None:
    """The normal state of this deployment: equities on a single-venue feed, plus legacy crypto
    series cached before provenance existed. An early return on the partial group dropped the
    unrecorded one entirely -- and every test here exercised one category alone, which is
    exactly how that survived.
    """
    (finding,) = feed_scope_findings(
        {
            ("MSFT-USD", "ONE_DAY"): ("alpaca:iex",),
            ("ETH-USD", "ONE_DAY"): (),
        }
    )
    assert finding.status == WARN
    assert "MSFT-USD" in finding.detail
    assert "ETH-USD" in finding.detail, "the unrecorded series was dropped"
    assert "lower bound" in finding.detail.lower()
    assert "unrecorded" in finding.detail.lower()


def test_the_fix_covers_both_remedies_when_both_states_exist() -> None:
    """They are different actions: re-fetch a partial series under a CONSOLIDATED feed; re-fetch
    an unrecorded one under any feed just to stamp it."""
    (finding,) = feed_scope_findings(
        {("MSFT-USD", "ONE_DAY"): ("alpaca:iex",), ("ETH-USD", "ONE_DAY"): ()}
    )
    assert "consolidated" in finding.fix.lower()
    assert "keel fetch" in finding.fix


def test_the_headline_counts_both_groups() -> None:
    findings = feed_scope_findings(
        {
            ("MSFT-USD", "ONE_DAY"): ("alpaca:iex",),
            ("AAPL-USD", "ONE_DAY"): ("alpaca:iex",),
            ("ETH-USD", "ONE_DAY"): (),
        }
    )
    (finding,) = findings
    assert "2" in finding.headline and "1" in finding.headline
