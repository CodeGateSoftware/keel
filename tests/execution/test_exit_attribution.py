"""A protective SELL must name the rule that owns it (#803).

The live symptom: `keel-live.db` order id 5, a bracket for the `turtle_breakout` PAXG tranche,
rendered `unattributed` in the console because `orders.rule_id` was NULL.

The cause was structural rather than a lookup that failed. `OrderIntent` carries `rule_kind` (a
KIND -- "dca", "turtle_breakout") and `rule_id` (the `rules.id` row) as separate fields. Entries
thread the id from `signal.rule_id`; both EXIT paths build their intent from a POSITION, and
`positions` stored only the kind -- so there was no id to thread and every protective SELL was
written anonymous.

Per-rule accounting reading `orders.rule_id` therefore saw entries attributed and exits
anonymous, which for a trend rule drops precisely the leg the outcome lands on.
"""

from __future__ import annotations

from decimal import Decimal

from keel.execution.executor import place_bracket, roll_to_break_even
from tests.execution.test_executor import (
    NOW_TS,
    _config,
    _seed_open_position,
    repo,  # noqa: F401  -- the shared in-memory Repository fixture
)
from tests.execution.test_sell_clamp import HeldBroker


def test_open_position_records_the_rule_id(repo):  # noqa: F811
    position_id = repo.open_position(
        product_id="PAXG-USD",
        rule_name="turtle_breakout",
        opened_at=NOW_TS,
        qty=Decimal("0.0132"),
        entry_fill=Decimal("4673.23"),
        entry_fee=Decimal("0.73"),
        rule_id=3,
    )

    position = next(p for p in repo.get_open_positions() if p["id"] == position_id)
    assert position["rule_id"] == 3
    assert position["rule_name"] == "turtle_breakout", "the kind must survive alongside the id"


def test_a_position_without_a_recorded_rule_id_reads_none(repo):  # noqa: F811
    """Unknown is a legitimate value -- pre-v21 tranches have no id and must not be guessed one."""
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=NOW_TS,
        qty=Decimal("0.001"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("0.5"),
    )

    assert repo.get_open_positions()[0]["rule_id"] is None


def test_the_bracket_order_carries_the_owning_rule_id(repo):  # noqa: F811
    """The defect, end to end: the protective SELL lands in `orders` ATTRIBUTED.

    Dropping `rule_id=rule_id` from `place_bracket`'s `OrderIntent` puts the row back to NULL and
    fails this test -- which is exactly the state live order id 5 is in.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("1.0"), Decimal("50000"))
    # A real `rules` row: `orders.rule_id` is a FOREIGN KEY, so attributing an order to an id
    # that does not exist is refused by the schema -- which is itself the reason this attribution
    # is worth carrying rather than reconstructing.
    rule_id = repo.insert_rule("turtle_breakout", {"product_id": "BTC-USD"}, status="live")
    broker = HeldBroker("BTC", available=Decimal("1.0"), total=Decimal("1.0"))

    bracket_id = place_bracket(
        broker,
        repo,
        _config(),
        "BTC-USD",
        Decimal("1.0"),
        Decimal("45000"),
        Decimal("55000"),
        "turtle_breakout",
        NOW_TS,
        rule_id=rule_id,
    )

    assert bracket_id is not None, "the bracket was not placed -- this test proves nothing"
    assert repo.get_order(bracket_id)["rule_id"] == rule_id


def test_an_unattributed_position_still_places_its_bracket(repo):  # noqa: F811
    """A missing id must never cost a position its stop.

    The whole point of `rule_id` being optional: a pre-v21 tranche has none, and refusing to
    protect it because the bookkeeping is incomplete would trade a reporting gap for an
    unprotected position.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("1.0"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("1.0"), total=Decimal("1.0"))

    bracket_id = place_bracket(
        broker,
        repo,
        _config(),
        "BTC-USD",
        Decimal("1.0"),
        Decimal("45000"),
        Decimal("55000"),
        "turtle_breakout",
        NOW_TS,
    )

    assert bracket_id is not None
    assert repo.get_order(bracket_id)["rule_id"] is None


def test_a_rolled_bracket_keeps_the_owning_rule_id(repo):  # noqa: F811
    """The ratchet is the THIRD exit path, and it builds its own intent.

    `place_bracket` and `scale_out` take `rule_id` from their caller; `_roll_stop` does not, and
    threading it through its three public wrappers would have been three more signatures to keep
    in step. It resolves the owner itself instead, from the bracket the tranche currently names.

    Without that, every ratcheted stop -- the protective order that fires most often on a running
    trend trade -- lands unattributed while its entry reads the rule's name.
    """
    rule_id = repo.insert_rule("turtle_breakout", {"product_id": "BTC-USD"}, status="live")
    position_id = repo.open_position(
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        opened_at=NOW_TS,
        qty=Decimal("0.01"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("0.5"),
        rule_id=rule_id,
    )
    broker = HeldBroker("BTC", available=Decimal("0.01"), total=Decimal("0.01"))
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="turtle_breakout",
        now_ts=NOW_TS,
        rule_id=rule_id,
    )
    repo.set_position_bracket(position_id, stop_id)

    rolled_id = roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("50000"),
        qty=Decimal("0.01"),
        rule_name="turtle_breakout",
        now_ts=NOW_TS + 100,
    )

    assert rolled_id is not None, "the roll did not happen -- this test proves nothing"
    assert repo.get_order(rolled_id)["rule_id"] == rule_id


def test_rolling_an_unattributed_position_still_replaces_the_stop(repo):  # noqa: F811
    """A tranche predating v21 names no rule. The roll must still happen: refusing to re-protect
    a position because its attribution is unknown would cancel a stop and not replace it."""
    _seed_open_position(repo, "BTC-USD", Decimal("0.01"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("0.01"), total=Decimal("0.01"))
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="turtle_breakout",
        now_ts=NOW_TS,
    )

    rolled_id = roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("50000"),
        qty=Decimal("0.01"),
        rule_name="turtle_breakout",
        now_ts=NOW_TS + 100,
    )

    assert rolled_id is not None
    assert repo.get_order(rolled_id)["rule_id"] is None
