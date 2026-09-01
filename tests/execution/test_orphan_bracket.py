"""#668 -- a resting SELL whose position is gone is cancelled before the market can trigger it.

`_clear_resting_bracket` fails closed before an exit. `reconcile_unbracketed_positions` heals a
position that has no bracket. Both are POSITION-DRIVEN: they start from a tranche and ask what
protects it. A protective order whose position has left the account has no tranche to be walked
from, so neither can reach it, and before this sweep nothing looked.

The market then reverses through the stop and the venue is asked to sell an asset that is not
there. On a cash account that is a rejection; on a margin-enabled one (#666) it is a short --
*bay' ma la yamlik* produced entirely by a missing cancel, with nobody in the loop.

The trigger is the VENUE's own statement about the account, never keel's ledger, and that
asymmetry is the safety property these tests exist to hold: cancelling a protective order over a
position that really exists strips a live holding of its only stop, and keel's ledger can be
stale in exactly that direction.
"""

from __future__ import annotations

from decimal import Decimal

from keel_broker_api.results import Balance, CancelOutcome, Instrument

from keel.data.repository import Repository
from keel.execution.reconcile import ORPHAN_BRACKET_PREFIX, sweep_orphan_brackets
from keel.types import Side
from tests.execution.test_reconcile import (
    NOW,
    PRODUCT,
    repo,  # noqa: F401  -- the shared in-memory Repository fixture
)


class SweepBroker:
    """The three reads the sweep makes, and a cancel that records what it was asked to kill.

    `base` is the account's holding of the base leg as `Balance.total`; `available` is set
    independently so a test can model the venue's real answer for a product with a resting
    order against it -- `available` zero, the whole position on `hold`.
    """

    def __init__(
        self,
        *,
        base: Decimal | None = Decimal("0"),
        available: Decimal | None = None,
        increment: Decimal | None = Decimal("0.00000001"),
        cancel: object = CancelOutcome.CONFIRMED,
        raise_on_balances: bool = False,
    ) -> None:
        self._base = base
        self._available = base if available is None else available
        self._increment = increment
        self._cancel = cancel
        self._raise_on_balances = raise_on_balances
        self.cancelled: list[str] = []
        self.get_balances_calls = 0

    def get_balances(self) -> list[Balance]:
        self.get_balances_calls += 1
        if self._raise_on_balances:
            raise ConnectionError("simulated balance outage")
        balances = [Balance(currency="USD", available=Decimal("1000"), total=Decimal("1000"))]
        if self._base is not None:
            balances.append(
                Balance(currency="BTC", available=self._available, total=self._base)
            )
        return balances

    def get_instrument(self, product_id: str) -> Instrument | None:
        if self._increment is None:
            return None
        return Instrument(product_id=product_id, base_increment=self._increment)

    def cancel_order(self, order_id: str) -> object:
        self.cancelled.append(order_id)
        if isinstance(self._cancel, Exception):
            raise self._cancel
        return self._cancel


def _resting_sell(
    repo: Repository,  # noqa: F811
    *,
    native_id: str = "cb-orphan-1",
    status: str = "pending",
    product_id: str = PRODUCT,
    side: str = Side.SELL.value,
) -> int:
    """One resting protective order, as `place_bracket` leaves it."""
    return repo.insert_order(
        dict(
            mode="live",
            product_id=product_id,
            side=side,
            order_type="market",
            qty=Decimal("0.01"),
            limit_price=None,
            status=status,
            fee=None,
            expected_fill=Decimal("49000"),
            actual_fill=None,
            raw_response=f'{{"order_id": "{native_id}"}}',
            created_at=NOW - 1000,
            updated_at=NOW - 1000,
        )
    )


# -- what the sweep cancels -------------------------------------------------------------------


def test_a_resting_sell_over_an_empty_account_is_cancelled(repo):  # noqa: F811
    """The whole point: the venue holds nothing, so the protective order protects nothing."""
    order_id = _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0"))

    cancelled = sweep_orphan_brackets(broker, repo, NOW)

    assert cancelled == [order_id]
    assert broker.cancelled == ["cb-orphan-1"]
    assert repo.get_order(order_id)["status"] == "canceled"


def test_dust_below_the_base_increment_counts_as_nothing_held(repo):  # noqa: F811
    """A residue the venue cannot express as a size is not a position.

    A bracket over it is an orphan with a rounding error attached: the venue would refuse the
    order for being under its own minimum, so nothing is being protected either way.
    """
    order_id = _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0.000000005"), increment=Decimal("0.00000001"))

    assert sweep_orphan_brackets(broker, repo, NOW) == [order_id]


def test_exactly_one_increment_is_a_real_position(repo):  # noqa: F811
    """The boundary, and it belongs on the side of NOT cancelling.

    One increment is the smallest position the venue can express. A bracket over it is doing its
    job, and the comparison must be strictly-below rather than at-or-below.
    """
    _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0.00000001"), increment=Decimal("0.00000001"))

    assert sweep_orphan_brackets(broker, repo, NOW) == []
    assert broker.cancelled == []


def test_a_partially_filled_bracket_is_swept_too(repo):  # noqa: F811
    """`partially_filled` is RESTING (#446) -- its unfilled remainder still works at the venue.

    Sweeping only `pending` would leave exactly the leg that has already begun executing against
    a position that is gone.
    """
    order_id = _resting_sell(repo, status="partially_filled")
    broker = SweepBroker(base=Decimal("0"))

    assert sweep_orphan_brackets(broker, repo, NOW) == [order_id]


# -- what the sweep must NOT cancel -----------------------------------------------------------


def test_a_real_position_keeps_its_bracket(repo):  # noqa: F811
    """The dangerous mistake, and the one this suite exists to make impossible."""
    _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0.01"))

    assert sweep_orphan_brackets(broker, repo, NOW) == []
    assert broker.cancelled == []


def test_a_bracket_holding_its_own_base_keeps_it(repo):  # noqa: F811
    """`Balance.total`, not `Balance.available` -- the same trap as #667's clamp.

    A resting SELL HOLDS the base it commits, so `available` reads zero for precisely the
    products this sweep looks at. Reading it would cancel every protective order keel has ever
    placed, on its first cycle, and call the result a fix.

    Modelled as the venue reports it: `available=0`, `hold=0.01`, `total=0.01`.
    """
    _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0.01"), available=Decimal("0"))

    assert sweep_orphan_brackets(broker, repo, NOW) == [], (
        "a resting bracket's own hold was read as an empty account -- the sweep is reading "
        "`Balance.available`"
    )


def test_an_unreadable_balance_cancels_nothing(repo):  # noqa: F811
    """Unknown is the do-nothing answer here, not a licence to cancel.

    An unreachable venue means the sweep did not run, which is the honest state; the next cycle
    retries. Failing closed in the other direction would strip protection over a network blip.
    """
    _resting_sell(repo)
    broker = SweepBroker(raise_on_balances=True)

    assert sweep_orphan_brackets(broker, repo, NOW) == []
    assert broker.cancelled == []


def test_a_venue_with_no_account_row_cancels_nothing(repo):  # noqa: F811
    """A venue that omits empty accounts is saying nothing, not saying zero.

    Only an affirmative report of a zero holding may cancel. Treating a sparse response as an
    empty account would cancel brackets on every venue whose only fault is a terse balance list.
    """
    _resting_sell(repo)
    broker = SweepBroker(base=None)

    assert sweep_orphan_brackets(broker, repo, NOW) == []


def test_an_unknown_increment_still_cancels_on_an_affirmative_zero(repo):  # noqa: F811
    """No increment means no dust threshold -- and none may be invented.

    `_base_increment_for` returns None for a venue error as much as for an unknown product, so a
    guessed floor would cancel protective orders over a number nobody supplied. An affirmative
    zero needs no threshold to be unambiguous.
    """
    order_id = _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0"), increment=None)

    assert sweep_orphan_brackets(broker, repo, NOW) == [order_id]


def test_an_unknown_increment_does_not_cancel_over_dust(repo):  # noqa: F811
    """The other half of the same rule: without an increment, only zero counts."""
    _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0.000000005"), increment=None)

    assert sweep_orphan_brackets(broker, repo, NOW) == []


def test_a_resting_buy_is_never_swept(repo):  # noqa: F811
    """A resting BUY is not a protective order and holds no base to be orphaned from."""
    _resting_sell(repo, side=Side.BUY.value)
    broker = SweepBroker(base=Decimal("0"))

    assert sweep_orphan_brackets(broker, repo, NOW) == []


def test_a_terminal_order_is_never_swept(repo):  # noqa: F811
    """Only orders still working at the venue can be cancelled; the rest are history."""
    _resting_sell(repo, status="filled")
    broker = SweepBroker(base=Decimal("0"))

    assert sweep_orphan_brackets(broker, repo, NOW) == []
    assert broker.cancelled == []


# -- failure behaviour --------------------------------------------------------------------------


def test_a_refused_cancel_leaves_the_row_alone_and_continues(repo):  # noqa: F811
    """A venue that refuses a cancel may have already filled the order. Local state must not lie.

    And the sweep keeps going: one refusal must not abandon the remaining orphans -- the same
    per-item isolation every other venue call in this module has.
    """
    first = _resting_sell(repo, native_id="cb-refused")
    second = _resting_sell(repo, native_id="cb-ok")

    class PartlyRefusing(SweepBroker):
        def cancel_order(self, order_id: str) -> object:
            self.cancelled.append(order_id)
            return CancelOutcome.REFUSED if order_id == "cb-refused" else CancelOutcome.CONFIRMED

    broker = PartlyRefusing(base=Decimal("0"))
    cancelled = sweep_orphan_brackets(broker, repo, NOW)

    assert cancelled == [second]
    assert repo.get_order(first)["status"] == "pending", (
        "a refused cancel was recorded locally as canceled while it may still be live"
    )
    assert broker.cancelled == ["cb-refused", "cb-ok"]


def test_an_accepted_but_unsettled_cancel_is_not_recorded_as_done(repo):  # noqa: F811
    """The venue took it and settles asynchronously. `reconcile_open_orders` reads the terminal
    state next cycle; claiming it here would retire an order that is still working."""
    order_id = _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0"), cancel=CancelOutcome.ACCEPTED)

    assert sweep_orphan_brackets(broker, repo, NOW) == []
    assert repo.get_order(order_id)["status"] == "pending"


def test_a_raising_cancel_does_not_abort_the_sweep(repo):  # noqa: F811
    """Never raises into the cycle. This runs at the top of every run and owns no failure."""
    _resting_sell(repo)
    broker = SweepBroker(base=Decimal("0"), cancel=RuntimeError("venue exploded"))

    assert sweep_orphan_brackets(broker, repo, NOW) == []


def test_the_balance_is_read_once_per_product_not_once_per_order(repo):  # noqa: F811
    """Two legs on one product ask the venue one question. This runs on every cycle."""
    _resting_sell(repo, native_id="cb-a")
    _resting_sell(repo, native_id="cb-b")
    broker = SweepBroker(base=Decimal("0"))

    assert len(sweep_orphan_brackets(broker, repo, NOW)) == 2
    assert broker.get_balances_calls == 1


def test_an_empty_book_touches_no_venue_at_all(repo):  # noqa: F811
    """No resting SELLs, no balance read. The common case must cost nothing."""
    broker = SweepBroker(base=Decimal("0"))

    assert sweep_orphan_brackets(broker, repo, NOW) == []
    assert broker.get_balances_calls == 0


# -- what it records ----------------------------------------------------------------------------


def test_a_cancelled_orphan_is_recorded_for_doctor(repo):  # noqa: F811
    """The cancel resolves the ORDER; it does not resolve why keel was protecting a ghost."""
    order_id = _resting_sell(repo)

    sweep_orphan_brackets(SweepBroker(base=Decimal("0")), repo, NOW)

    record = repo.get_state(f"{ORPHAN_BRACKET_PREFIX}{PRODUCT}")
    assert record is not None
    assert record["order_id"] == order_id
    assert record["held"] == "0"


def test_the_tranche_is_left_open_on_purpose(repo):  # noqa: F811
    """Closing it would book a realized outcome at a price nobody observed.

    The sweep's warrant covers the ORDER. What the position was worth when it left the account
    is a question only an operator can answer, and inventing an answer is worse than the open
    row -- `doctor`'s `ledger.unbooked_exit` and `bracket.orphan` both surface it.
    """
    _resting_sell(repo)
    position_id = repo.open_position(
        product_id=PRODUCT,
        rule_name="turtle_breakout",
        opened_at=NOW - 1000,
        qty=Decimal("0.01"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("3"),
    )

    sweep_orphan_brackets(SweepBroker(base=Decimal("0")), repo, NOW)

    assert [p["id"] for p in repo.get_open_positions()] == [position_id]
