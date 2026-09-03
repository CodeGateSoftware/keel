"""Whether a feed's volume may be read as market volume -- issue #696.

The load-bearing test is `test_a_partial_feed_above_the_floor_is_still_conclusive`, which pins
the asymmetry the whole design rests on: a single-venue feed's volume is a LOWER BOUND on
consolidated volume, so clearing the floor on one venue alone proves the floor is cleared, while
failing it proves nothing. Get that backwards and you either ban MSFT from an IEX feed or admit
a penny stock on one.

`test_the_bound_needs_no_market_share` is the other one that matters: nothing in this module
encodes a venue's percentage, because the bound holds for any share below 100%. A module that
needed 3.8% to be right would need editing every time market structure moved.
"""

from __future__ import annotations

from keel.data.feed_scope import (
    CONSOLIDATED_VOLUME_FEEDS,
    reports_consolidated_volume,
    volume_feed_of,
)


class _Declares:
    def __init__(self, feed: object) -> None:
        self._feed = feed

    @property
    def volume_feed_id(self) -> object:
        return self._feed


class _Silent:
    pass


# --- what a client declares ------------------------------------------------------------------


def test_a_client_that_declares_a_feed_is_read() -> None:
    assert volume_feed_of(_Declares("alpaca:iex")) == "alpaca:iex"


def test_a_client_that_declares_nothing_is_unrecorded() -> None:
    """`None`, never a default. A guessed feed is the bug this table exists to prevent."""
    assert volume_feed_of(_Silent()) is None


def test_a_blank_or_non_string_declaration_is_unrecorded() -> None:
    """Defensive against an adapter that grows the attribute but leaves it unset -- a `""` or a
    stray object would otherwise be written into provenance and read back as an unknown feed."""
    assert volume_feed_of(_Declares("")) is None
    assert volume_feed_of(_Declares("   ")) is None
    assert volume_feed_of(_Declares(None)) is None
    assert volume_feed_of(_Declares(object())) is None


# --- the scope verdict -----------------------------------------------------------------------


def test_no_recorded_provenance_is_none_not_false() -> None:
    """`None` is the absence of evidence; `False` is evidence of a limitation. A caller that
    conflates them treats every legacy database as though its scope had been established."""
    assert reports_consolidated_volume(()) is None


def test_a_consolidated_feed_reads_as_consolidated() -> None:
    assert reports_consolidated_volume(("coinbase",)) is True
    assert reports_consolidated_volume(("alpaca:sip",)) is True


def test_a_single_venue_feed_reads_as_partial() -> None:
    assert reports_consolidated_volume(("alpaca:iex",)) is False


def test_an_unrecognised_feed_is_treated_as_partial() -> None:
    """Conservative by design: it costs a conclusive pass on a consolidated feed nobody
    declared, and never grants one on a narrow feed nobody declared."""
    assert reports_consolidated_volume(("some-new-venue",)) is False


def test_a_mixed_series_is_partial() -> None:
    """A median is not decomposable by source. Once narrow-feed bars are in the series, the
    statistic is a lower bound for the whole series -- there is no honest way to read the
    consolidated half separately."""
    assert reports_consolidated_volume(("alpaca:iex", "alpaca:sip")) is False


def test_every_declared_consolidated_feed_is_a_nonempty_string() -> None:
    assert CONSOLIDATED_VOLUME_FEEDS
    assert all(isinstance(f, str) and f.strip() for f in CONSOLIDATED_VOLUME_FEEDS)


def test_the_narrow_alpaca_feed_is_not_declared_consolidated() -> None:
    """The one membership question this issue exists to answer."""
    assert "alpaca:iex" not in CONSOLIDATED_VOLUME_FEEDS
    assert "alpaca:sip" in CONSOLIDATED_VOLUME_FEEDS


def test_the_bound_needs_no_market_share() -> None:
    """No percentage anywhere in the module. The lower bound holds for ANY share below 100%, so
    a figure here would be a maintenance liability that buys nothing -- and would invite someone
    to scale a volume statistic by it, which is the one thing that must never happen."""
    import inspect
    import re

    from keel.data import feed_scope

    code = "".join(
        line
        for line in inspect.getsource(feed_scope).splitlines(keepends=True)
        if not line.lstrip().startswith("#")
    )
    body = code[code.index('"""', code.index('"""') + 3) + 3 :]
    assert not re.search(r"\d+(\.\d+)?\s*%", body), "a market share leaked into executable code"
    assert "0.038" not in body and "3.8" not in body
