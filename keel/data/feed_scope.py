"""Which data feed a series came from, and whether that feed sees the whole market -- #696.

keel's liquidity statistic is `median(volume * close)` over cached candles
(`compliance/screen.median_daily_quote_volume`), and every threshold keyed on it was calibrated
against VENUE-REPORTED volume. On a crypto exchange that is coherent: Coinbase's own volume is
the scale the floor was chosen against. On Alpaca's IEX feed it is not, because IEX reports one
US equity exchange's executions -- IEX publishes its overall share as roughly 3.8% for Q2 2026
-- while keel's model is anchored at $500M/day. The cost-fidelity run measured MSFT, one of the
most liquid securities in the world, cached at $186M/day and priced as a thin asset.

**THE ASYMMETRY IS THE WHOLE DESIGN.** A single-venue feed's volume is a LOWER BOUND on
consolidated volume, never an upper one. So the two directions are not equally informative:

* volume at or above the floor, on a partial feed -> **conclusive PASS.** If a name traded that
  much on one venue alone, it necessarily traded at least that much in total. No assumption is
  needed and none is made.
* volume below the floor, on a partial feed -> **not a verdict.** It licenses no claim either
  way, and must be refused as UNMEASURED rather than reported as thin.

That asymmetry is why this module can be honest without knowing any venue's market share. The
bound holds for any share below 100%, so nothing here encodes a percentage that would drift as
market structure changes -- the 3.8% above is context for a reader, never an input to a decision.

WHAT THIS DELIBERATELY DOES NOT DO. It does not fail closed on a partial feed. Nothing is
currently admitted on the equities profile, whose entire job is accruing paper evidence, and
refusing every IEX series would convert a data-vendor pricing tier into a hard prerequisite for
running the engine at all. The bound above is strong enough to admit the liquid names honestly
and refuse the rest without pretending to have measured them.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

#: Feeds that report CONSOLIDATED market volume -- i.e. where venue volume is market volume for
#: the purpose keel uses it. A crypto exchange is its own market in the sense that matters here:
#: the floor was calibrated on exactly this kind of number.
#:
#: Membership is a claim about market structure, so it is declared, never inferred from a feed's
#: name. An unrecognised feed id is treated as PARTIAL, which is the conservative reading: it
#: costs a conclusive pass on a genuinely consolidated feed nobody declared, and never grants one.
CONSOLIDATED_VOLUME_FEEDS: frozenset[str] = frozenset(
    {
        "coinbase",
        "kraken",
        # Alpaca's paid tier IS the consolidated tape (SIP), which is what makes the IEX
        # distinction meaningful rather than a blanket statement about the venue.
        "alpaca:sip",
    }
)


@runtime_checkable
class DeclaresVolumeFeed(Protocol):
    """A broker adapter that can name the feed its candles come from.

    Structural and optional on purpose: an adapter that does not declare one records no
    provenance, which reads back as "unrecorded" rather than as a guess. Forcing every adapter
    to implement it would mean editing five packages to add a value four of them would state
    identically -- and a required field invites a placeholder, which is the one value that must
    never enter this table.
    """

    @property
    def volume_feed_id(self) -> str | None: ...


def volume_feed_of(client: Any) -> str | None:
    """The feed id `client` declares, or `None` when it declares nothing.

    `None` means UNRECORDED. It must never be normalised to a default: the whole point of
    recording provenance at fetch time is that an unknown feed stays visibly unknown instead of
    inheriting whatever config is loaded when someone later reads the series.
    """
    feed = getattr(client, "volume_feed_id", None)
    if not isinstance(feed, str) or not feed.strip():
        return None
    return feed


def reports_consolidated_volume(feeds: tuple[str, ...]) -> bool | None:
    """Does this series' recorded provenance support reading its volume as market volume?

    * `True`  -- every recorded feed is consolidated.
    * `False` -- at least one recorded feed is partial, so the statistic is a LOWER BOUND. A
      mixed series counts as partial: the bars from the narrow feed are still in the median, and
      a median is not decomposable by source.
    * `None`  -- nothing recorded. Distinct from `False` on purpose: `False` is a known
      limitation a caller can reason about with the asymmetric bound, `None` is the absence of
      evidence, and a caller that cannot tell them apart will treat legacy databases as if their
      scope had been established.
    """
    if not feeds:
        return None
    return all(feed in CONSOLIDATED_VOLUME_FEEDS for feed in feeds)


__all__ = [
    "CONSOLIDATED_VOLUME_FEEDS",
    "DeclaresVolumeFeed",
    "reports_consolidated_volume",
    "volume_feed_of",
]
