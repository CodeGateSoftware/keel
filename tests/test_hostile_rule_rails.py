"""What a foreign rule cannot do to the agent cycle, driven end to end rather than assumed.

`RULE_REGISTRY` is a plain dict. Nothing stops a caller -- a test, a future first-party rule
edited carelessly, an operator running keel as a library -- from putting an arbitrary `Rule`
subclass in it, and `agent._build_rule` will happily construct one and hand it to
`engine.evaluate`. #447 asked whether the layers that keep such a rule from trading something
keel does not trade hold **by test rather than by assumption**, and found that no single test
fed a foreign rule through the real cycle and checked. This file is that test.

Four layers are exercised here, at the level a cycle actually runs:

1. `Setup.__post_init__` -- a rule cannot construct a short intent at all.
2. `engine._long_shaped_ok` -- a rule cannot construct a short's PRICE GEOMETRY and label it
   long. The construction succeeds; the proposal is refused at the rule boundary.
3. `engine.evaluate`'s unconditional `side=Side.BUY` -- a rule does not choose the side, and
   cannot reach for it through the one field it does control end to end, `Setup.context`.
4. `guards.check` rails 18 and 19 -- a rule cannot name an instrument keel does not trade,
   whatever produced the intent.

**What this file does NOT prove.** It shows those four layers hold against a rule that
misbehaves in these six specific ways. It is not a proof that no rule can ever reach the broker,
and no in-process test could be: a rule is arbitrary Python, and the space of things arbitrary
Python can do is not enumerable by sampling six points in it. What makes the sample worth
something is the CONTROL test below -- the same harness, the same hostile-rule mechanism, a rule
that behaves, and a real BUY placed at the broker. Without it every "nothing was placed"
assertion here would pass just as well against a harness that places nothing under any
circumstances, which is the failure mode this file exists to avoid rather than reproduce.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from keel_core.telemetry import _FIELDS_ATTR

from keel.agent import run_once
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity, Side
from tests.conftest import attest_cash_posture, attest_subscription, attest_trade_scope
from tests.test_agent import (
    PRODUCT,
    FakeBroker,
    _candle,
    _live_config,
    _live_ready_repo,
    _seed_rule,
)
from tests.test_agent import repo as repo  # noqa: F401  -- reuse the module's rails-cleared repo


def _fresh_repo() -> Repository:
    """A second rails-cleared repo, for the one test that must drive two cycles side by side.

    Mirrors `tests/test_agent.py`'s `repo` fixture, which cannot be requested twice in a single
    test. Kept to exactly the same attestations so the two cycles differ in the rule alone.
    """
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    r.set_state("withdrawals_enabled", True)
    r.set_state("withdrawals_attested_at", 10**12)
    r.set_autonomous(True, now_ts=0)
    attest_subscription(r, now_ts=0, free_volume_usd=Decimal("10000000"))
    attest_trade_scope(r, now_ts=0)
    # Rail 22 (#691) fails closed without a cash-posture record, same as rail 20.
    attest_cash_posture(r, now_ts=0)
    return r


# -- the hostile rules ----------------------------------------------------------------------


class _ForeignRule(Rule):
    """A rule keel does not ship, firing an ENTER on every bar.

    Modelled on `tests/test_agent.py::_AlwaysEnterRule` deliberately: the point of these tests is
    what the ENGINE and the RAILS do with a rule's output, so the rule itself has to be the
    boring part. Everything hostile about the subclasses below is a single overridden value.
    """

    def __init__(
        self,
        product_id: str = PRODUCT,
        name: str = "foreign",
        context: dict | None = None,
    ) -> None:
        self.name = name
        self.product_id = product_id
        self.params: dict = {"product_id": product_id}
        self._context = context or {}

    def _prices(self, last: Candle) -> tuple[Decimal, Decimal, Decimal]:
        """`(entry, stop, target)` -- a well-formed long. Overridden to misbehave."""
        return last.close, last.close * Decimal("0.95"), last.close * Decimal("1.15")

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        candles = next((c for c in candles_by_tf.values() if c), [])
        if not candles:
            return None
        last = candles[-1]
        entry, stop, target = self._prices(last)
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context=dict(self._context),
            ts=last.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _InvertedRule(_ForeignRule):
    """A short's price geometry wearing `direction="long"`: stop ABOVE entry, target BELOW.

    This is the shape a rule that wanted to go short would actually emit, since the `direction`
    field itself is now refused at construction. It is the interesting hostile case precisely
    because every field is a well-typed `Decimal` and the object builds without complaint.
    """

    def _prices(self, last: Candle) -> tuple[Decimal, Decimal, Decimal]:
        return last.close, last.close * Decimal("1.05"), last.close * Decimal("0.85")


class _StopAboveEntryRule(_ForeignRule):
    """ONLY the stop leg is wrong: stop above entry, target honestly above it.

    Split from `_InvertedRule` because a fully inverted setup is refused by either leg of
    `_long_shaped_ok` on its own, so a rail that had silently lost half of itself would still
    pass a test written against the inverted case. Mutation-verified: with the target leg
    deleted the suite went green, which is what these two per-leg rules exist to stop.
    """

    def _prices(self, last: Candle) -> tuple[Decimal, Decimal, Decimal]:
        return last.close, last.close * Decimal("1.05"), last.close * Decimal("1.15")


class _UnderwaterTargetRule(_ForeignRule):
    """ONLY the target leg is wrong: an ordinary protective stop below entry, but a target
    BELOW the entry too -- an entry that books a loss the moment it reaches its own objective.

    The stop leg passes this, so it is the case that pins the target leg by itself.
    """

    def _prices(self, last: Candle) -> tuple[Decimal, Decimal, Decimal]:
        return last.close, last.close * Decimal("0.95"), last.close * Decimal("0.85")


class _ShortDirectionRule(_ForeignRule):
    """Constructs `Setup(direction="short")` outright. Cannot survive its own `detect()`."""

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        candles = next((c for c in candles_by_tf.values() if c), [])
        last = candles[-1] if candles else _candle(0, "100")
        return Setup(
            product_id=self.product_id,
            direction="short",  # type: ignore[arg-type]
            entry=last.close,
            stop=last.close * Decimal("1.05"),
            target=last.close * Decimal("0.85"),
            context={},
            ts=last.ts,
        )


# -- harness --------------------------------------------------------------------------------

#: A three-segment futures-shaped id. Rail 19 (`spot_instrument`) is what refuses this: it is
#: not `BASE-QUOTE`. Its BASE is `BTC`, which is inside `_config`'s allowlist on purpose -- the
#: product loop filters on the allowlist before any rail is reached, so an out-of-allowlist id
#: would be dropped early and prove nothing about rail 19.
FUTURES_PRODUCT = "BTC-30JUN25-CDE"

#: A perfectly well-formed spot pair settling in a currency the deployment has not configured.
#: Rail 19 PASSES this (the grammar is right); rail 18 (`settlement_currency`) is what refuses
#: it. The pair is what makes the two rails separable in this file rather than one assertion
#: standing in for both.
FOREIGN_SETTLEMENT_PRODUCT = "BTC-EUR"


def _drive(repo, monkeypatch, rule: Rule, *, product: str = PRODUCT) -> tuple[FakeBroker, object]:
    """One real live-mode cycle with `rule` as the only live rule, approved at the prompt.

    Confirm mode with a `confirm_fn` that says yes, not paper mode: paper never reaches the
    broker at all, so "nothing was placed" would be true there for a reason that has nothing to
    do with the rails. Here a well-behaved rule genuinely places, which is what gives the
    negative assertions their meaning.
    """
    _live_ready_repo(repo)
    repo.set_autonomous(False, now_ts=0)
    _seed_rule(repo, monkeypatch, rule, status="live")
    broker = FakeBroker(series={(product, Granularity.ONE_DAY): [_candle(0, "100")]})
    result = run_once(
        broker, repo, _live_config(), now_ts=90_000, confirm_fn=lambda preview: True
    )
    return broker, result


def _vetoes(result) -> list[str]:
    """Every rail violation the cycle's entry attempts collected -- `vetoed_by`, flattened."""
    return [v for r in result.enter_results for v in r.vetoed_by]


# -- the control ----------------------------------------------------------------------------


def test_a_well_behaved_foreign_rule_really_does_place_a_buy(repo, monkeypatch):
    """THE CONTROL, and every other test in this file depends on it.

    A foreign rule -- not in `RULE_REGISTRY` as shipped, constructed by this test module and
    injected -- proposing an ordinary long on an allowlisted spot pair places a real BUY at the
    broker through the real cycle. Without this, "no order was placed" below would be satisfied
    by a harness that is simply incapable of placing one: a mis-seeded candle series, an
    unattested rail, a config that vetoes everything. Every such mistake makes the hostile tests
    pass while proving nothing, and it is exactly the mistake a reader cannot see.
    """
    broker, _ = _drive(repo, monkeypatch, _ForeignRule())

    buys = [c for c in broker.place_calls if c["product_id"] == PRODUCT and c["side"] == Side.BUY]
    assert len(buys) == 1, broker.place_calls


# -- layer 1: a rule cannot construct a short -----------------------------------------------


def test_a_rule_cannot_construct_a_short_setup_at_all():
    """`Setup.direction` was `Literal["long"]` on a frozen dataclass with no `__post_init__` --
    a promise to mypy that cost a foreign rule nothing to break, since nothing type-checks a
    rule loaded out of a database row. Now it costs a `ValueError` at construction.
    """
    with pytest.raises(ValueError, match="direction must be 'long'"):
        Setup(
            product_id=PRODUCT,
            direction="short",  # type: ignore[arg-type]
            entry=Decimal("100"),
            stop=Decimal("105"),
            target=Decimal("85"),
            context={},
            ts=0,
        )


def test_a_short_constructing_rule_places_nothing_and_fails_loudly(repo, monkeypatch):
    """Driven through the real cycle, the rail above surfaces where the rule ran and the cycle
    places nothing.

    It RAISES rather than being skipped, and that is the intended behaviour rather than an
    accident of where the check sits: `engine.evaluate` puts no `try` around `rule.detect()`, so
    a rule that manufactures an intent keel has no path for takes the cycle down instead of
    trading. That is the right trade for a case no shipped rule can reach -- the type already
    forbids it -- and the reason the price-geometry rail one layer down is a refusal instead.
    """
    with pytest.raises(ValueError, match="direction must be 'long'"):
        _drive(repo, monkeypatch, _ShortDirectionRule())

    assert repo.get_orders() == []


# -- layer 2: a short's geometry wearing a long's label --------------------------------------


def test_an_inverted_setup_is_refused_at_the_rule_boundary(repo, monkeypatch, caplog):
    """The hostile case that actually type-checks: stop above entry, target below, every field a
    well-formed `Decimal` and `direction` honestly `"long"`.

    Refused at `engine._long_shaped_ok`, before scoring, so no `Signal` is emitted and nothing
    downstream -- sizing, the rails, the broker -- is ever asked. Pinned on all three of those
    consequences rather than only on "no order placed", because an order can fail to be placed
    for a dozen reasons and only the rejection EVENT says this rail is what stopped it.
    """
    with caplog.at_level(logging.WARNING):
        broker, result = _drive(repo, monkeypatch, _InvertedRule())

    assert broker.place_calls == []
    assert result.enter_results == []

    rejections = [
        getattr(record, _FIELDS_ATTR, {})
        for record in caplog.records
        if record.getMessage() == "engine.setup_rejected"
    ]
    assert any(fields.get("gate") == "not_long_shaped" for fields in rejections), rejections


@pytest.mark.parametrize(
    ("rule_cls", "leg"),
    [(_StopAboveEntryRule, "stop"), (_UnderwaterTargetRule, "target")],
    ids=["stop_above_entry", "target_below_entry"],
)
def test_each_leg_of_the_geometry_rail_is_load_bearing_on_its_own(
    repo, monkeypatch, caplog, rule_cls, leg
):
    """`_long_shaped_ok` is two comparisons, and the fully inverted setup above trips BOTH -- so
    a rail that had silently lost one of them would still pass that test.

    This was not a hypothetical. Deleting the `target >= entry` half left the whole file green;
    these two cases are what made the deletion fail. Each rule here breaks exactly one leg and
    keeps the other honest, so neither assertion can be carried by the other.
    """
    with caplog.at_level(logging.WARNING):
        broker, result = _drive(repo, monkeypatch, rule_cls())

    assert broker.place_calls == [], leg
    assert result.enter_results == [], leg
    rejections = [
        getattr(record, _FIELDS_ATTR, {})
        for record in caplog.records
        if record.getMessage() == "engine.setup_rejected"
    ]
    assert any(fields.get("gate") == "not_long_shaped" for fields in rejections), rejections


# -- layer 3: the rule does not choose the side ----------------------------------------------


def test_a_rule_cannot_reach_for_the_sell_side_through_its_context(repo, monkeypatch):
    """`Setup.context` is a free-form dict the rule fills in and the engine reads -- the one
    channel a rule controls that travels all the way to `assemble_cts_context` and the
    `signals` row. A rule that wanted to sell would try it here.

    `engine.evaluate` builds every `Signal` with `side=Side.BUY` as a literal, so the answer is
    that the channel does not connect to anything. Worth pinning anyway: `context` HAS grown
    engine-visible keys before (`order_class` selects the market-buy class, and with it an
    exemption from three gates), so "the engine ignores this dict" is not a standing property of
    the design -- it is true of the side specifically, and this is what keeps it true.
    """
    hostile_context = {"side": "SELL", "direction": "short", "order_class": None}
    hostile_broker, hostile_result = _drive(
        repo, monkeypatch, _ForeignRule(context=hostile_context)
    )
    benign_broker, benign_result = _drive(_fresh_repo(), monkeypatch, _ForeignRule())

    assert [s.side for s in hostile_result.enter_signals] == [Side.BUY]

    # The comparison, not a bare "no SELL was placed" -- which would be FALSE and would have
    # been a bug in this test rather than in the engine. An approved entry places two orders:
    # the BUY, and the protective OCO bracket, whose stop leg is a SELL. A rule cannot be said
    # to have failed to reach the sell side just because no SELL exists; what it must fail to do
    # is CHANGE anything. So the pin is that the hostile context produces the identical exchange
    # interaction to an empty one -- same orders, same sides, same sequence.
    assert [c["side"] for c in hostile_broker.place_calls] == [
        c["side"] for c in benign_broker.place_calls
    ]
    assert hostile_broker.events == benign_broker.events
    assert [s.side for s in hostile_result.enter_signals] == [
        s.side for s in benign_result.enter_signals
    ]
    assert benign_broker.place_calls, "if the benign cycle places nothing, this proves nothing"


# -- layer 4: the rails refuse the instrument, whatever proposed it --------------------------


def test_rail_19_refuses_a_non_spot_instrument_shape_whatever_rule_produced_it(
    repo, monkeypatch
):
    """A foreign rule naming a futures contract. Its BASE is allowlisted, so the product loop
    does not drop it early and the intent genuinely reaches `guards.check`.

    Rail 19 asks what SHAPE the id is, against the spot grammar, and this is three segments.
    The point of driving it from a rule rather than from a hand-built intent (which
    `tests/execution/test_guards.py` already covers thoroughly) is that the rail's guarantee is
    stated as "regardless of what produced the intent", and until now nothing had produced one
    from an unfamiliar rule to check that.
    """
    broker, result = _drive(
        repo, monkeypatch, _ForeignRule(product_id=FUTURES_PRODUCT), product=FUTURES_PRODUCT
    )

    assert broker.place_calls == []
    assert any("spot_instrument" in v for v in _vetoes(result)), _vetoes(result)


def test_rail_18_refuses_a_foreign_settlement_leg_whatever_rule_produced_it(repo, monkeypatch):
    """The companion, and the reason both are here rather than one: `BTC-EUR` is a perfectly
    well-formed spot pair, so rail 19 passes it and only rail 18 -- which asks what the id
    SETTLES IN -- refuses it. A single hostile product id would have let one rail's assertion
    stand in for both, and a reader could not tell which was load-bearing.
    """
    broker, result = _drive(
        repo,
        monkeypatch,
        _ForeignRule(product_id=FOREIGN_SETTLEMENT_PRODUCT),
        product=FOREIGN_SETTLEMENT_PRODUCT,
    )

    assert broker.place_calls == []
    vetoes = _vetoes(result)
    assert any("settlement_currency" in v for v in vetoes), vetoes
    assert not any("spot_instrument" in v for v in vetoes), (
        "BTC-EUR is well-formed spot grammar; if rail 19 also fired, this test is no longer "
        f"isolating rail 18: {vetoes}"
    )
