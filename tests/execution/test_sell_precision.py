"""SELL `base_size` at the venue's precision (#516), and the asymmetry with BUY (#513).

The asymmetry is the point of this file. #513 refuses a BUY whose size cannot be expressed in the
venue's units; a SELL in the same position must NOT refuse. A refused BUY costs nothing, while a
refused SELL strands a position that wanted to exit -- so the unknown case sends unquantized and
logs, exactly as it did before this change. Several tests here exist to make "make it consistent
with BUY" fail loudly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.execution.executor import (
    _base_increment_for,
    _bracket_order_configuration,
    _order_configuration,
    _sell_base_size,
)
from keel.execution.guards import OrderIntent
from keel.types import Side

NOW = 1_787_400_000

#: XLM's real quantity from the rejected live order -- the shape an exit of it would carry.
MESSY_QTY = Decimal("114.0117873747800116713612474")


def _sell(qty: Decimal = MESSY_QTY, increment: Decimal | None = None) -> OrderIntent:
    return OrderIntent(
        product_id="XLM-USD",
        side=Side.SELL,
        qty=qty,
        entry=Decimal("0.201804"),
        stop=None,
        notional=Decimal("23.008"),
        is_dca=False,
        rule_kind="turtle_breakout",
        base_increment=increment,
    )


# -- the asymmetry ---------------------------------------------------------------------------


def test_unknown_increment_sends_unquantized_and_does_not_refuse() -> None:
    """THE load-bearing test. Do not "fix" this into consistency with the BUY path.

    A refused BUY costs nothing. A refused SELL strands a position. Sending full precision at
    least sometimes works -- a round quantity is accepted -- so refusing would replace
    "sometimes exits" with "never exits".
    """
    config = _order_configuration(_sell(increment=None))
    assert config == {"market_market_ioc": {"base_size": str(MESSY_QTY)}}


def test_a_buy_refuses_where_a_sell_sends() -> None:
    """The two sides, side by side, so the difference cannot be read as an oversight."""
    from keel.execution.executor import SizePrecisionUnavailable

    buy = OrderIntent(
        product_id="BTC-XYZ",  # settlement currency with no known increment
        side=Side.BUY,
        qty=Decimal("1"),
        entry=Decimal("100"),
        stop=None,
        notional=Decimal("23.008034739"),
        is_dca=False,
        rule_kind="turtle_breakout",
    )
    with pytest.raises(SizePrecisionUnavailable):
        _order_configuration(buy)

    sell = _sell(increment=None)
    assert _order_configuration(sell)["market_market_ioc"]["base_size"] == str(MESSY_QTY)


@pytest.mark.parametrize("increment", [None, Decimal("0"), Decimal("-1")])
def test_a_non_positive_increment_is_treated_as_unknown(increment: Decimal | None) -> None:
    assert _sell_base_size(_sell(increment=increment)) == MESSY_QTY


# -- quantization when the increment IS known -------------------------------------------------


def test_a_known_increment_floors_the_quantity() -> None:
    assert _sell_base_size(_sell(increment=Decimal("0.000001"))) == Decimal("114.011787")


def test_quantization_is_down_so_we_never_sell_more_than_held() -> None:
    """Selling more than held is rejected for insufficient funds; selling less leaves dust."""
    sent = _sell_base_size(_sell(increment=Decimal("0.01")))
    assert sent <= MESSY_QTY
    assert sent == Decimal("114.01")


def test_a_quantity_smaller_than_one_increment_is_sent_unchanged() -> None:
    """Dust the venue cannot express: send it and let the venue answer.

    Suppressing the order would silently retire a holding keel still believes it has, and an
    audited rejection beats a silent no-op.
    """
    tiny = Decimal("0.0000004")
    assert _sell_base_size(_sell(qty=tiny, increment=Decimal("0.001"))) == tiny


# -- brackets --------------------------------------------------------------------------------


def test_bracket_quantizes_its_base_size_when_the_increment_is_known() -> None:
    config = _bracket_order_configuration(
        MESSY_QTY, Decimal("0.25"), Decimal("0.18"), Decimal("0.000001")
    )
    assert config["trigger_bracket_gtc"]["base_size"] == "114.011787"


def test_bracket_sends_unquantized_when_the_increment_is_unknown() -> None:
    """A bracket the venue refuses leaves the position UNPROTECTED -- never make this stricter."""
    config = _bracket_order_configuration(MESSY_QTY, Decimal("0.25"), Decimal("0.18"), None)
    assert config["trigger_bracket_gtc"]["base_size"] == str(MESSY_QTY)


def test_bracket_prices_are_untouched() -> None:
    """Only the SIZE is quantized here. Prices have their own increment and are not in scope."""
    config = _bracket_order_configuration(
        Decimal("1"), Decimal("0.25"), Decimal("0.18"), Decimal("0.01")
    )
    assert config["trigger_bracket_gtc"]["limit_price"] == "0.25"
    assert config["trigger_bracket_gtc"]["stop_trigger_price"] == "0.18"


# -- the cached lookup -------------------------------------------------------------------------


class _Repo:
    """Minimal `get_state`/`set_state` stand-in -- the lookup touches nothing else."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = state or {}
        self.writes = 0

    def get_state(self, key, default=None):  # noqa: ANN001, ANN202
        return self.state.get(key, default)

    def set_state(self, key, value) -> None:  # noqa: ANN001
        self.state[key] = value
        self.writes += 1


class _Broker:
    def __init__(self, products=None, raises: bool = False) -> None:  # noqa: ANN001
        self._products = products or []
        self._raises = raises
        self.calls = 0

    def list_products(self):  # noqa: ANN202
        self.calls += 1
        if self._raises:
            raise RuntimeError("venue unreachable")
        return self._products


PRODUCTS = [
    {"product_id": "XLM-USD", "base_increment": "0.000001"},
    {"product_id": "BTC-USD", "base_increment": "0.00000001"},
    {"product_id": "BAD-USD", "base_increment": None},
]


def test_lookup_fetches_and_returns_the_requested_increment() -> None:
    repo, broker = _Repo(), _Broker(PRODUCTS)
    assert _base_increment_for(broker, repo, "XLM-USD", NOW) == Decimal("0.000001")


def test_a_second_call_for_the_same_product_is_served_from_cache() -> None:
    repo, broker = _Repo(), _Broker(PRODUCTS)
    _base_increment_for(broker, repo, "XLM-USD", NOW)
    assert broker.calls == 1

    assert _base_increment_for(broker, repo, "XLM-USD", NOW) == Decimal("0.000001")
    assert broker.calls == 1  # no second venue call


def test_a_miss_writes_exactly_one_row_not_one_per_product() -> None:
    """The review finding this test exists for.

    `set_state` commits per call, so caching all ~900 products would mean ~900 fsyncs inside the
    order-placement path -- the most latency-sensitive moment in the engine -- to save a handful
    of `list_products` calls per week. One row per miss is the right way round.
    """
    repo, broker = _Repo(), _Broker(PRODUCTS)
    _base_increment_for(broker, repo, "XLM-USD", NOW)

    assert repo.writes == 1
    assert list(repo.state) == ["base_increment:XLM-USD"]


def test_a_stale_entry_is_refetched() -> None:
    stale = {"base_increment:XLM-USD": {"increment": "0.1", "fetched_at": 0}}
    repo, broker = _Repo(stale), _Broker(PRODUCTS)
    assert _base_increment_for(broker, repo, "XLM-USD", NOW) == Decimal("0.000001")
    assert broker.calls == 1


def test_a_venue_error_returns_unknown_and_never_raises() -> None:
    """Unknown is a safe answer here; an exception in the exit path is not."""
    repo, broker = _Repo(), _Broker(raises=True)
    assert _base_increment_for(broker, repo, "XLM-USD", NOW) is None


def test_no_broker_is_unknown_not_an_error() -> None:
    """Paper mode passes no broker, exactly as `_fetch_available_quote` handles."""
    assert _base_increment_for(None, _Repo(), "XLM-USD", NOW) is None


def test_a_missing_or_malformed_increment_is_unknown() -> None:
    repo, broker = _Repo(), _Broker(PRODUCTS)
    assert _base_increment_for(broker, repo, "BAD-USD", NOW) is None
    assert _base_increment_for(broker, repo, "NOT-LISTED", NOW) is None
