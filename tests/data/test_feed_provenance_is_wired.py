"""Provenance is recorded where the bars are WRITTEN, not where they are read -- issue #696.

`feed_scope` can say what a feed means and `Repository` can store it; neither matters unless the
fetch path actually passes it. These are the wiring pins: an adapter declares its feed, and every
path that writes candles carries that declaration through to the row.

The failure this prevents is silent and total. If the fetch path drops the feed, every series
reads back as "unrecorded", the scope verdict is `None` everywhere, and the gate downstream
degrades to exactly the behaviour that existed before the issue was filed -- with a schema, a
module and a test suite all suggesting otherwise.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core.types import Candle, Granularity

from keel.data import history
from keel.data.db import connect, migrate
from keel.data.feed_scope import volume_feed_of
from keel.data.repository import Repository


def _candle(ts: int) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


class _FeedClient:
    """A venue client that declares its feed, as the Alpaca adapter does."""

    def __init__(self, feed: str, batches: list[list[Candle]]) -> None:
        self._feed = feed
        self._batches = batches

    @property
    def volume_feed_id(self) -> str:
        return self._feed

    def get_candles(self, product_id, granularity, start, end):  # noqa: ANN001, ANN201
        return self._batches.pop(0) if self._batches else []


class _SilentClient(_FeedClient):
    """A venue client that declares nothing -- most adapters, today."""

    def __init__(self, batches: list[list[Candle]]) -> None:
        super().__init__("", batches)

    @property
    def volume_feed_id(self) -> str | None:  # type: ignore[override]
        return None


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def test_the_alpaca_adapter_declares_its_configured_feed() -> None:
    from keel_broker_alpaca.adapter import AlpacaAdapter

    assert volume_feed_of(AlpacaAdapter(transport=object(), data_feed="iex")) == "alpaca:iex"
    assert volume_feed_of(AlpacaAdapter(transport=object(), data_feed="sip")) == "alpaca:sip"


def test_the_coinbase_adapter_declares_its_venue() -> None:
    """Coinbase's own volume IS the scale keel's floor was calibrated on, so it declares a
    consolidated feed -- and declaring it explicitly is what keeps that a stated claim rather
    than a default nobody wrote down."""
    from keel_broker_coinbase.adapter import CoinbaseAdapter

    assert volume_feed_of(CoinbaseAdapter(transport=object())) == "coinbase"


def test_ensure_history_records_the_feed_it_fetched_under() -> None:
    repo = _repo()
    client = _FeedClient("alpaca:iex", [[_candle(0), _candle(86400)]])
    history.ensure_history(
        client, repo, ["MSFT-USD"], [Granularity.ONE_DAY], years=1, now_ts=86400
    )
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ("alpaca:iex",)


def test_a_client_that_declares_nothing_records_nothing() -> None:
    """Unrecorded stays unrecorded. A fetch through an adapter with no declaration must not
    invent one, or every crypto series silently acquires a provenance nobody established."""
    repo = _repo()
    client = _SilentClient([[_candle(0), _candle(86400)]])
    history.ensure_history(
        client, repo, ["BTC-USD"], [Granularity.ONE_DAY], years=1, now_ts=86400
    )
    assert repo.get_candles("BTC-USD", Granularity.ONE_DAY)
    assert repo.get_series_feeds("BTC-USD", Granularity.ONE_DAY) == ()
