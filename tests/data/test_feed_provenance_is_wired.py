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

from keel.data import history, market_feed, repair
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


# --- EVERY writer, not just the one the mechanism was built against ---------------------------
#
# The three tests above prove `ensure_history` carries the declaration. They say nothing about
# the other paths that write candles, and the first review of this branch found that they did
# not: `market_feed.poll_once` -- the path `agent.run_once` uses on EVERY cycle in every
# deployment -- and `repair.repair_series` both dropped it. The mechanism worked and was very
# nearly inert, because `keel fetch` is not how bars normally arrive.


class _PollClient(_FeedClient):
    """Enough of `Broker` for `poll_once`: it only reads candles here."""

    def get_candles(self, product_id, granularity, start, end):  # noqa: ANN001, ANN201
        return self._batches.pop(0) if self._batches else []


def test_the_live_poll_path_records_the_feed() -> None:
    """`agent.run_once` polls through here every cycle, so this is the path that decides
    whether provenance exists in a real database at all."""
    repo = _repo()
    client = _PollClient("alpaca:iex", [[_candle(0), _candle(86400)]])
    market_feed.poll_once(
        client, repo, ["MSFT-USD"], [Granularity.ONE_DAY], now_ts=86400 * 3
    )
    assert repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY) == ("alpaca:iex",)


def test_the_gap_repair_path_records_the_feed() -> None:
    """`fetch --repair-gaps` backfilled 29,676 bars into the hourly profile in one run. Bars
    that arrive in bulk are exactly the ones whose provenance must not be missing."""
    repo = _repo()
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, [_candle(0), _candle(86400 * 5)])
    client = _FeedClient("alpaca:iex", [[_candle(86400 * 2)], []])
    repair.repair_series(
        client,
        repo,
        "MSFT-USD",
        Granularity.ONE_DAY,
        now_ts=86400 * 6,
        sleep_fn=lambda _: None,
    )
    assert "alpaca:iex" in repo.get_series_feeds("MSFT-USD", Granularity.ONE_DAY)


def test_both_adapters_satisfy_the_declared_protocol() -> None:
    """`DeclaresVolumeFeed` documents the contract `volume_feed_of` reads. Unless something
    checks it, it drifts from the `getattr` that actually enforces it -- so this is what makes
    the Protocol load-bearing rather than decorative."""
    from keel_broker_alpaca.adapter import AlpacaAdapter
    from keel_broker_coinbase.adapter import CoinbaseAdapter

    from keel.data.feed_scope import DeclaresVolumeFeed

    assert isinstance(AlpacaAdapter(transport=object(), data_feed="iex"), DeclaresVolumeFeed)
    assert isinstance(CoinbaseAdapter(transport=object()), DeclaresVolumeFeed)
    assert volume_feed_of(_SilentClient([])) is None
