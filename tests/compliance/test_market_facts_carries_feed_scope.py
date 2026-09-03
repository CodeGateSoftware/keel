"""`market_facts` must read the provenance it has, or the gate is decorative -- #696.

`screen_asset` decides between a plain liquidity failure and `liquidity_unmeasured` from
`MarketFacts.volume_feed_is_consolidated`. Both fields default to `None` so a caller that forgets
them preserves the pre-#696 verdict rather than admitting or refusing anything new -- which makes
forgetting them SILENT. This is the pin that says the one real caller does not.

The same shape of miss already happened once on this issue: the provenance table landed with only
`keel fetch` writing to it, so the live path recorded nothing and the feature was nearly inert.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_core.types import Candle, Granularity

from keel.commands.assets import market_facts
from keel.data.db import connect, migrate
from keel.data.repository import Repository


@pytest.fixture()
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _candles(n: int = 5) -> list[Candle]:
    return [
        Candle(
            ts=i * 86400,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )
        for i in range(n)
    ]


def test_a_partial_feed_is_carried_into_the_facts(repo: Repository) -> None:
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex")
    facts = market_facts(repo, "MSFT-USD", "USD")
    assert facts.volume_feed == "alpaca:iex"
    assert facts.volume_feed_is_consolidated is False


def test_a_consolidated_feed_is_carried_into_the_facts(repo: Repository) -> None:
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _candles(), feed="coinbase")
    facts = market_facts(repo, "BTC-USD", "USD")
    assert facts.volume_feed == "coinbase"
    assert facts.volume_feed_is_consolidated is True


def test_an_unrecorded_series_carries_none(repo: Repository) -> None:
    """Not `False`. A legacy series' scope is unknown, and the gate treats unknown as
    "verdict unchanged" rather than as a known limitation."""
    repo.upsert_candles("ETH-USD", Granularity.ONE_DAY, _candles())
    facts = market_facts(repo, "ETH-USD", "USD")
    assert facts.volume_feed is None
    assert facts.volume_feed_is_consolidated is None


def test_a_mixed_series_names_both_feeds_and_reads_partial(repo: Repository) -> None:
    """A median is not decomposable by source, so a series carrying any narrow-feed bars is a
    lower bound overall -- and the operator needs to see WHICH feeds, to know what to re-fetch."""
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:iex")
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:sip")
    facts = market_facts(repo, "MSFT-USD", "USD")
    assert facts.volume_feed == "alpaca:iex, alpaca:sip"
    assert facts.volume_feed_is_consolidated is False


def test_the_scope_read_is_for_the_granularity_the_statistic_uses(repo: Repository) -> None:
    """`market_facts` medians ONE_DAY bars, so ONE_DAY provenance is what governs. An hourly
    series fetched under a different feed must not change this verdict."""
    repo.upsert_candles("MSFT-USD", Granularity.ONE_DAY, _candles(), feed="alpaca:sip")
    repo.upsert_candles("MSFT-USD", Granularity.ONE_HOUR, _candles(), feed="alpaca:iex")
    facts = market_facts(repo, "MSFT-USD", "USD")
    assert facts.volume_feed_is_consolidated is True
