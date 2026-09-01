"""#667 -- a SELL never asks the venue for more base than the account holds.

The ledger and the account drift apart in three ways keel cannot see from its own records: a
venue that takes its taker fee out of the received base leaves 0.9985 where the order said
1.0000; a partial fill leaves less still, and `orders.filled_quantity` is written only when the
venue's post-fill status was observable; an operator who moves coins out tells keel nothing at
all. Every one of them makes the ledger say MORE than the account holds.

Sizing a SELL from the ledger then asks for base that is not there. On a cash account that is a
rejected exit -- a position that wanted out, still in, unprotected. On a margin-enabled account
(#666) the venue fills the difference by opening a short: *bay' ma la yamlik*, arrived at by
arithmetic rather than by anyone's intent, and reachable without a single line of the strategy
layer proposing one.

Two mechanisms, deliberately split, and the split is what these tests pin:

* **the clamp** (`executor._clamp_to_held`) shrinks an order that is too big for a position that
  really exists -- down only, never up, and never to zero;
* **rail 21** (`guards.check`) refuses the one case the clamp will not touch, a venue that
  affirmatively reports holding nothing at all.

Neither cancels a protective order. That distinction is the reason
`_record_observed_fill_quantity`'s refusal to auto-resize still stands beside this: what it
declines to do is cancel a resting bracket on the strength of a settling snapshot, and nothing
here cancels anything.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.results import Balance

from keel.execution import executor, guards
from keel.execution.executor import BALANCE_DRIFT_PREFIX, place_bracket, scale_out
from keel.execution.guards import LIVE_STATE_RAILS, OrderIntent
from keel.strategy.rules.base import Action, Signal
from keel.types import Side
from tests.execution.test_executor import (
    NOW_TS,
    FakeBroker,
    _config,
    _seed_open_position,
    repo,  # noqa: F401  -- the shared in-memory Repository fixture
)


def _exit_signal(product_id: str = "BTC-USD") -> Signal:
    return Signal(
        rule_name="target_harvest",
        product_id=product_id,
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="market",
        ts=NOW_TS,
    )


class HeldBroker(FakeBroker):
    """A `FakeBroker` whose account carries a BASE holding as well as the quote legs.

    `available` and `total` are set INDEPENDENTLY, because the difference between them is the
    single most dangerous line in #667 and every test that cares about it needs to be able to
    make them disagree.
    """

    def __init__(self, base: str, available: Decimal, total: Decimal, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._base = base
        self._base_available = available
        self._base_total = total

    def get_balances(self) -> list[Balance]:
        balances = super().get_balances()
        balances.append(
            Balance(
                currency=self._base, available=self._base_available, total=self._base_total
            )
        )
        return balances


# -- the clamp -----------------------------------------------------------------------------


def test_a_sell_is_reduced_to_what_the_venue_says_is_held(repo):  # noqa: F811
    """The fee-dust case, end to end: the ledger says 1.0, the account holds 0.9985."""
    _seed_open_position(repo, "BTC-USD", Decimal("1.0"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("0.9985"), total=Decimal("0.9985"))

    result = executor.execute(
        _exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True
    assert broker.place_calls[-1]["spec"].base_size == Decimal("0.9985")


def test_a_sell_is_never_raised_to_a_larger_holding(repo):  # noqa: F811
    """Down-only. A venue holding MORE than the ledger expects does not enlarge the order.

    The asymmetry is the whole point: selling less than is held leaves dust, and selling more is
    the violation. A clamp that moved in both directions would sell base the position's own
    ledger never accounted for -- which is a different position, not a corrected one.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("5"), total=Decimal("5"))

    result = executor.execute(
        _exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True
    assert broker.place_calls[-1]["spec"].base_size == Decimal("0.1")


def test_an_unreadable_holding_sells_the_ledger_quantity(repo):  # noqa: F811
    """UNKNOWN never strands an exit -- the deliberate opposite of rail 13's fail-closed posture.

    A venue with no account row for the base is saying nothing about the holding, and refusing
    an exit on nothing said would replace "sometimes exits" with "never exits" the moment a
    balance endpoint went quiet.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    broker = FakeBroker()  # funds USD/USDC only -- no BTC row at all

    result = executor.execute(
        _exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True
    assert broker.place_calls[-1]["spec"].base_size == Decimal("0.1")


def test_a_broker_that_raises_on_get_balances_still_exits(repo):  # noqa: F811
    """A balance read must never become a new failure mode on the exit path."""
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))

    class Outage(FakeBroker):
        def get_balances(self) -> list[Balance]:
            raise ConnectionError("simulated balance outage")

    result = executor.execute(
        _exit_signal(), Outage(), repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True


def test_the_clamp_reads_total_and_not_available(repo):  # noqa: F811
    """THE load-bearing test of #667. `Balance.available` is the wrong number here.

    `available` excludes base committed to resting orders, and keel's own protective bracket
    commits the ENTIRE position -- so for every product keel is protecting, `available` reads
    zero while the account holds the lot. A clamp on that number would shrink every exit to
    nothing and rail 21 would then veto it, refusing every stop roll and every exit keel has
    ever placed. `total` (available + hold) is the ownership number, and ownership is the
    question *bay' ma la yamlik* asks.

    Modelled exactly as the venue reports it: `available=0`, `hold=0.1`, `total=0.1`.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("0"), total=Decimal("0.1"))

    result = executor.execute(
        _exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True, (
        "an exit was refused for a position the venue reports holding in full -- the clamp is "
        "reading `Balance.available`, which a resting bracket drives to zero"
    )
    assert broker.place_calls[-1]["spec"].base_size == Decimal("0.1")


def test_a_clamped_sell_records_the_drift_for_doctor(repo):  # noqa: F811
    """Silently absorbing the divergence would hide a real fact about the ACCOUNT.

    The log line alone is not enough: it requires an operator to know to grep for it. The drift
    is a property of the account, so it outlives the order that discovered it.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("1.0"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("0.9985"), total=Decimal("0.9985"))

    executor.execute(_exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    drift = repo.get_state(f"{BALANCE_DRIFT_PREFIX}BTC-USD")
    assert drift is not None, "a clamped SELL must leave a record `doctor` can surface"
    assert drift["held"] == "0.9985"
    assert Decimal(drift["drift"]) == Decimal("0.0015")


def test_an_unclamped_sell_records_nothing(repo):  # noqa: F811
    """No drift, no record. A key written on every ordinary exit would make the finding noise."""
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("0.1"), total=Decimal("0.1"))

    executor.execute(_exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert repo.get_state(f"{BALANCE_DRIFT_PREFIX}BTC-USD") is None


def test_the_bracket_placed_after_an_entry_is_sized_to_the_held_base(repo):  # noqa: F811
    """The oversized-bracket condition, closed where it is created.

    `place_bracket` receives the quantity the ENTRY was placed for. A venue that took its fee
    out of the received base holds less than that, so a bracket at the ordered size is a
    protective order able to sell more than is held -- exactly what
    `_record_observed_fill_quantity` names and declines to fix by cancelling. Sizing it down
    before it is ever placed cancels nothing.
    """
    broker = HeldBroker("BTC", available=Decimal("0.9985"), total=Decimal("0.9985"))

    place_bracket(
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

    assert broker.place_calls[-1]["spec"].base_size == Decimal("0.9985")


def test_a_scale_out_is_clamped_to_the_held_base(repo):  # noqa: F811
    """A partial exit is a SELL like any other, and drift binds it the same way.

    Under no drift the clamp cannot bind here at all -- `scale_out` already refuses a `qty` at
    or above the ledger's holding -- so this only fires when the books and the account really
    disagree.
    """
    _seed_open_position(repo, "BTC-USD", Decimal("1.0"), Decimal("50000"))
    repo.set_state("open_stop:BTC-USD", Decimal("45000"))
    repo.set_state("open_target:BTC-USD", Decimal("55000"))
    broker = HeldBroker("BTC", available=Decimal("0.3"), total=Decimal("0.3"))

    scale_out(
        broker, repo, _config(), "BTC-USD", Decimal("0.5"), Decimal("52000"), "target_1", NOW_TS
    )

    sells = [c["spec"] for c in broker.place_calls if getattr(c["spec"], "side", None) is Side.SELL]
    assert sells, "scale_out must have placed the partial SELL"
    assert sells[0].base_size == Decimal("0.3")


# -- rail 21 -------------------------------------------------------------------------------


def _sell_intent(**overrides: object) -> OrderIntent:
    fields: dict[str, object] = dict(
        product_id="BTC-USD",
        side=Side.SELL,
        qty=Decimal("0.1"),
        entry=Decimal("50000"),
        stop=None,
        notional=Decimal("5000"),
        is_dca=False,
        rule_kind="target_harvest",
    )
    fields.update(overrides)
    return OrderIntent(**fields)  # type: ignore[arg-type]


def _rail21(result: guards.GuardResult) -> list[str]:
    return [v for v in result.violations if v.startswith("base_balance")]


def test_rail_21_vetoes_a_sell_when_the_venue_reports_no_holding(repo):  # noqa: F811
    """The books say a position exists; the account says it does not. That is not an exit.

    `_build_intent` already returns `None` when the LEDGER says zero, so reaching rail 21 at all
    means the two disagree -- and refusing sells nothing and traps nothing, because there is
    nothing there to trap. On a margin-enabled account, sending it is the short.
    """
    result = guards.check(
        _sell_intent(available_base=Decimal("0")), repo, _config(), NOW_TS
    )

    assert _rail21(result), "a SELL against an affirmatively empty holding must be vetoed"
    assert "keel's ledger expects 0.1" in _rail21(result)[0]


def test_rail_21_does_not_veto_a_holding_merely_smaller_than_the_order(repo):  # noqa: F811
    """A partial holding is a real position. Vetoing it would strand what could still be sold.

    This is the boundary between the two mechanisms: below the order but above zero belongs to
    the clamp, and only zero-or-less belongs to the rail.
    """
    result = guards.check(
        _sell_intent(available_base=Decimal("0.05")), repo, _config(), NOW_TS
    )

    assert not _rail21(result)


def test_rail_21_does_not_veto_an_unknown_holding(repo):  # noqa: F811
    """Fails OPEN on unknown -- the deliberate inverse of rail 13, and it must stay that way.

    Rail 13 vetoes a BUY on an unknown quote balance because a refused BUY costs nothing. A
    refused SELL costs a position its exit. Do not make these two consistent.
    """
    result = guards.check(_sell_intent(available_base=None), repo, _config(), NOW_TS)

    assert not _rail21(result)


def test_rail_21_never_touches_a_buy(repo):  # noqa: F811
    """A BUY has rail 13. A base holding says nothing about whether an entry can be funded."""
    result = guards.check(
        _sell_intent(side=Side.BUY, available_base=Decimal("0")), repo, _config(), NOW_TS
    )

    assert not _rail21(result)


def test_rail_21_is_skipped_and_reported_in_paper(repo):  # noqa: F811
    """Paper has no account, so it cannot ask -- and must SAY it could not ask.

    A paper track record that silently skipped a live-state rail would score a strategy on
    evidence live trading would have refused. That is what the promotion gate exists to prevent.
    """
    result = guards.check(
        _sell_intent(available_base=Decimal("0")), repo, _config(), NOW_TS, offline=True
    )

    assert not _rail21(result)
    assert "base_balance" in result.skipped_rails
    assert "base_balance" in LIVE_STATE_RAILS


@pytest.mark.parametrize("held", [Decimal("0"), Decimal("-1")])
def test_rail_21_treats_a_negative_holding_as_empty(repo, held):  # noqa: F811
    """A venue reporting a negative base is already short. Nothing about that permits a SELL."""
    result = guards.check(_sell_intent(available_base=held), repo, _config(), NOW_TS)

    assert _rail21(result)


# -- rail 21, reached through the real cycle -------------------------------------------------
#
# Everything above this line builds an `OrderIntent` by hand, and that is not enough. A rail is
# only as real as the wiring that feeds it: drop `available_base=` from the intent the executor
# actually builds and every hand-built test above still passes, because none of them ever ran
# `execute`. These do.


def test_execute_vetoes_an_exit_the_venue_says_it_cannot_make(repo):  # noqa: F811
    """Ledger says 0.1 BTC, venue says 0. The exit does not go out, and rail 21 is why."""
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    broker = HeldBroker("BTC", available=Decimal("0"), total=Decimal("0"))

    result = executor.execute(
        _exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is False
    assert any(v.startswith("base_balance") for v in result.vetoed_by), (
        f"expected a base_balance veto, got {result.vetoed_by} -- the executor is not threading "
        "the venue's holding onto the intent, so rail 21 can never see it"
    )
    assert broker.place_calls == []


def test_place_bracket_is_vetoed_when_the_venue_holds_nothing(repo):  # noqa: F811
    """The same wiring, on the protective-order path.

    A bracket for a position the account no longer holds is the orphan #668 sweeps for. Refusing
    to place one is the cheaper half of the same problem.
    """
    broker = HeldBroker("BTC", available=Decimal("0"), total=Decimal("0"))

    result = place_bracket(
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

    assert result is None
    assert broker.place_calls == []


def test_scale_out_is_vetoed_when_the_venue_holds_nothing(repo):  # noqa: F811
    """And on the partial-exit path, which reaches `_run_order` by a third route again."""
    _seed_open_position(repo, "BTC-USD", Decimal("1.0"), Decimal("50000"))
    repo.set_state("open_stop:BTC-USD", Decimal("45000"))
    repo.set_state("open_target:BTC-USD", Decimal("55000"))
    broker = HeldBroker("BTC", available=Decimal("0"), total=Decimal("0"))

    result = scale_out(
        broker, repo, _config(), "BTC-USD", Decimal("0.5"), Decimal("52000"), "target_1", NOW_TS
    )

    assert result.placed is False
    assert any(v.startswith("base_balance") for v in result.vetoed_by), (
        f"expected a base_balance veto, got {result.vetoed_by}"
    )


def test_rolling_a_stop_is_vetoed_when_the_venue_holds_nothing(repo):  # noqa: F811
    """The fourth route into `_run_order`, and the one with the sharpest ordering hazard.

    `_roll_stop` CANCELS the resting bracket before it places the replacement, so a holding read
    on `Balance.available` would jump from ~0 to the full position across that single line and
    the roll's fate would depend on which side of the cancel it was read from. Reading `total`
    makes the cancel irrelevant to the number -- which is why this test can assert the veto
    without caring about the order of operations at all.
    """
    broker = HeldBroker("BTC", available=Decimal("0"), total=Decimal("0.01"))
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )
    assert stop_id is not None, "the bracket must place while the venue still reports the holding"

    # The position leaves the account out of band -- withdrawn, or an exit that already ran.
    broker._base_total = Decimal("0")

    rolled = executor.roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("50000"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert rolled is None, (
        "a stop was re-placed for a position the venue reports it no longer holds -- "
        "`_roll_stop` is not threading the holding onto its intent"
    )


def test_a_rolled_stop_is_re_placed_at_the_held_size(repo):  # noqa: F811
    """Not just vetoed on empty -- SIZED on partial. A roll re-places the whole position.

    `_roll_stop` re-places at the same quantity it cancelled, so it carries the ordered size
    forward across every roll of a position's life. Left unclamped it would keep reasserting an
    oversized protective order long after the drift that created it, at each new stop.
    """
    broker = HeldBroker("BTC", available=Decimal("0"), total=Decimal("1.0"))
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("1.0"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )
    assert stop_id is not None
    broker._base_total = Decimal("0.9985")

    rolled = executor.roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("50000"),
        qty=Decimal("1.0"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert rolled is not None
    assert broker.place_calls[-1]["spec"].base_size == Decimal("0.9985")


# -- the observation the clamp depends on ----------------------------------------------------


def test_a_filled_size_is_recorded_even_when_the_venue_reports_no_average_price(repo):  # noqa: F811
    """`filled_quantity` and `average_filled_price` are independent facts (#667).

    They were coupled: one `fill <= 0` guard returned before the quantity was recorded, so a
    venue that answered with a size and no average price lost the size too. The guard belongs to
    the PRICE -- it is there so a zero does not overwrite a usable estimate -- and the quantity
    is the half that measures how far the ledger has drifted.
    """
    from keel_broker_api.results import OrderStatus, PlaceResult

    order_id = repo.insert_order(
        dict(
            mode="live",
            product_id="BTC-USD",
            side=Side.BUY.value,
            order_type="market",
            qty=Decimal("1.0"),
            limit_price=Decimal("50000"),
            status="filled",
            fee=Decimal("0"),
            expected_fill=Decimal("50000"),
            actual_fill=Decimal("50000"),
            raw_response=None,
            confirmation="autonomous",
            rule_id=None,
            created_at=NOW_TS,
            updated_at=NOW_TS,
        )
    )

    class Priceless:
        """A venue that knows what executed but not at what average price."""

        def get_order(self, order_id: str) -> OrderStatus:
            return OrderStatus(
                order_id=order_id,
                status="FILLED",
                filled_size=Decimal("0.9985"),
                average_filled_price=Decimal("0"),
                total_fees=Decimal("0"),
            )

    executor._upgrade_to_observed_economics(
        Priceless(),
        repo,
        order_id,
        PlaceResult(success=True, broker_order_id="venue-1"),
        NOW_TS,
    )

    assert repo.get_order(order_id)["filled_quantity"] == Decimal("0.9985")
    # The price is still declined, which is the half of the old guard that was always right.
    assert repo.get_order(order_id)["actual_fill"] == Decimal("50000")
