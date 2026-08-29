"""`executor.scale_out` -- the partial profit-take, and the two things that had to exist first.

#502's remaining half. `scale_out` has been in the codebase, correct-looking and unreachable,
behind a tripwire test naming the three reasons wiring it would have been wrong: the partial SELL
ran beside a bracket committing the FULL position, the bracket was never resized, and no outcome
was recorded so rail 16 counted a scaled-out net winner as a loss. That tripwire is retired with
this module; these are the pins that take its place.

Two of them are the load-bearing ones and neither is about the happy path:

* **The crash ledger is written before the FIRST venue touch**, not after the sell. #519's
  pattern. The cancel removes the position's only protection and the re-place cannot be atomic
  with it, so a process that dies in the gap must leave levels behind for
  `reconcile_unbracketed_positions` -- otherwise the position is not merely unprotected but
  SILENT, indistinguishable from a DCA holding that carries no stop by design.
* **A scaled-out net winner is ONE outcome row and a WIN.** The half taken at target and the
  runner that later stops out at break-even sum into one `pnl_net`, per §2 of the trade-outcomes
  design. Booked as two rows the runner's fee-sized loss would increment rail 16's consecutive
  loss counter on every profitable scale-out, so the strategy that works best under scaling out
  would be the one that trips the breaker fastest.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import pytest
from keel_broker_api.orders import BracketGTC, OrderSpec
from keel_broker_api.results import PlaceResult

from keel.data.repository import Repository
from keel.execution import streak
from keel.execution.executor import UNBRACKETED_PREFIX, place_bracket, scale_out
from tests.execution.test_executor import (
    NOW_TS,
    FakeBroker,
    _config,
    _seed_open_position,
    repo,  # noqa: F401 -- the fixture, reused rather than re-declared
)

PRODUCT = "BTC-USD"
ENTRY = Decimal("50000")
STOP = Decimal("49000")
TARGET = Decimal("53000")
HELD = Decimal("0.2")


def _seed_bracketed_tranche(
    repository: Repository,
    broker: FakeBroker,
    *,
    qty: Decimal = HELD,
    entry: Decimal = ENTRY,
    entry_fee: Decimal = Decimal("1"),
) -> int:
    """A held, bracketed, ledger-tracked position -- the state every scale-out starts from.

    Deliberately built through `place_bracket` rather than by writing `open_stop`/`open_target`
    directly: those two keys have exactly one writer pair in production, and a fixture that
    forged them would let a scale-out pass against state the live path cannot produce.
    """
    _seed_open_position(repository, PRODUCT, qty, entry)
    bracket_id = place_bracket(
        broker,
        repository,
        _config(),
        product_id=PRODUCT,
        qty=qty,
        stop=STOP,
        target=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )
    assert bracket_id is not None
    position_id = repository.open_position(
        product_id=PRODUCT,
        rule_name="pullback_continuation",
        opened_at=NOW_TS - 1000,
        qty=qty,
        entry_fill=entry,
        entry_fee=entry_fee,
        initial_stop=STOP,
        bracket_order_id=bracket_id,
    )
    broker.events.clear()
    broker.place_calls.clear()
    broker.cancel_calls.clear()
    return position_id


class _LedgerWatchingBroker(FakeBroker):
    """Records the crash ledger's contents at the instant of EVERY venue call.

    The ordering pin cannot be written as "assert the record exists afterwards" -- it exists
    afterwards either way. What distinguishes writing it first from writing it last is what a
    process crashing mid-sequence would find, and the only way to observe that from a test is to
    look at the ledger from inside the venue call itself.
    """

    def __init__(self, repository: Repository, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._repo = repository
        self.ledger_at_touch: list[Any] = []

    def _observe(self) -> None:
        self.ledger_at_touch.append(self._repo.get_state(f"{UNBRACKETED_PREFIX}{PRODUCT}"))

    def preview_order(self, spec: OrderSpec) -> Any:
        self._observe()
        return super().preview_order(spec)

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        self._observe()
        return super().place_order(spec, idempotency_key=idempotency_key)

    def cancel_order(self, order_id: str) -> bool:
        self._observe()
        return super().cancel_order(order_id)


# -- the crash ledger ------------------------------------------------------------------------


def test_the_crash_ledger_is_written_before_the_first_venue_touch(repo):  # noqa: F811
    """#519's invariant, applied to the scale-out sequence.

    The FIRST thing `scale_out` does at the venue is cancel the bracket protecting the position.
    Every instant after that and before the remainder's bracket rests is a window in which the
    position has no stop, and a process that dies there must leave the sweep something to heal
    from. Writing the record after the sell -- the natural reading of "record what happened" --
    would leave the cancel window empty, and an unprotected position with nothing in the ledger
    saying so looks exactly like a DCA tranche and is never escalated.
    """
    broker = _LedgerWatchingBroker(repo)
    _seed_bracketed_tranche(repo, broker)
    broker.ledger_at_touch.clear()

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert result.placed is True
    assert broker.ledger_at_touch, "the venue was never touched -- the pin proved nothing"
    assert broker.ledger_at_touch[0] == {
        "stop": STOP,
        "target": TARGET,
        "qty": Decimal("0.1"),
    }, (
        "the crash ledger was not written before the first venue call: a crash between the "
        "cancel and the re-place would leave a live, unprotected position with nothing in the "
        f"ledger for the sweep to heal from (saw {broker.ledger_at_touch[0]!r})"
    )


def test_the_ledger_is_cleared_once_the_remainder_bracket_rests(repo):  # noqa: F811
    """Left standing, the sweep would re-place a bracket the position already holds on every
    later cycle -- rejected for insufficient base, and escalated as if the position were naked."""
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)

    scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert repo.get_state(f"{UNBRACKETED_PREFIX}{PRODUCT}") is None


def test_a_rejected_sell_after_the_cancel_is_critical_and_keeps_the_ledger(repo, caplog):  # noqa: F811
    """The bracket is gone and the sell did not happen, so the WHOLE position is naked.

    The record is retained deliberately: it is what the next cycle's sweep re-places from, and
    the sweep sizes from the TRANCHE -- untouched here, because nothing was booked -- so it
    heals the full position rather than the remainder this attempt was aiming at.
    """
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)
    broker._place_success = False

    with caplog.at_level(logging.CRITICAL):
        result = scale_out(
            broker,
            repo,
            _config(),
            product_id=PRODUCT,
            qty=Decimal("0.1"),
            exit_price=TARGET,
            rule_name="pullback_continuation",
            now_ts=NOW_TS,
        )

    assert result.placed is False
    assert repo.get_state(f"{UNBRACKETED_PREFIX}{PRODUCT}") is not None
    assert [r for r in caplog.records if r.getMessage() == "executor.position_unprotected"]
    assert repo.get_open_positions(PRODUCT)[0]["qty"] == HELD


# -- the resize ------------------------------------------------------------------------------


def test_the_bracket_is_cancelled_before_the_sell_and_re_placed_for_the_remainder(repo):  # noqa: F811
    """The whole point of the sequence, in one assertion about ORDER and one about SIZE.

    Cancel-before-place is not a preference: the resting native bracket commits the entire base
    position, so a partial SELL placed beside it is rejected on spot for insufficient base -- or
    fills, and leaves a bracket able to sell inventory no longer held.
    """
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.05"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert result.placed is True
    assert broker.events == ["cancel", "place", "place"], (
        "the resting bracket must be cancelled BEFORE the partial sell, and the remainder's "
        f"bracket placed after it -- saw {broker.events}"
    )
    sell_spec, bracket_spec = broker.place_calls[0]["spec"], broker.place_calls[1]["spec"]
    assert sell_spec.base_size == Decimal("0.05")
    assert isinstance(bracket_spec, BracketGTC)
    assert bracket_spec.base_size == Decimal("0.15"), (
        "the replacement bracket must commit only the REMAINDER; sized at the original quantity "
        "it can sell more base than the account holds"
    )
    assert bracket_spec.stop_trigger_price == STOP
    assert bracket_spec.take_profit_price == TARGET
    assert repo.get_state(f"open_stop:{PRODUCT}") == STOP
    assert repo.get_state(f"open_target:{PRODUCT}") == TARGET


def test_the_surviving_tranche_is_repointed_at_the_replacement_bracket(repo):  # noqa: F811
    """`get_position_for_bracket` is the ONE linkage direction reconciliation has. A tranche
    still naming the cancelled bracket orphans the replacement: its eventual fill resolves to no
    trade, and the `trade_outcomes` row closing the scaled-out position is dropped entirely."""
    broker = FakeBroker()
    position_id = _seed_bracketed_tranche(repo, broker)
    old_bracket_id = repo.get_open_positions(PRODUCT)[0]["bracket_order_id"]

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert result.bracket_order_id is not None
    assert result.bracket_order_id != old_bracket_id
    assert repo.get_position_for_bracket(result.bracket_order_id)["id"] == position_id


def test_a_bracket_that_cannot_be_cancelled_refuses_the_sell(repo):  # noqa: F811
    """Fails closed. An uncancellable bracket means we do not know what the exchange will do
    with that inventory, and adding a partial SELL to that uncertainty is strictly worse than
    waiting a cycle."""
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)
    broker.cancel_order = lambda order_id: False  # type: ignore[method-assign]

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert result.placed is False
    assert broker.place_calls == []
    assert repo.get_open_positions(PRODUCT)[0]["qty"] == HELD


# -- refusals, all before the venue is touched -----------------------------------------------


@pytest.mark.parametrize(
    "qty, why",
    [
        (Decimal("0"), "qty must be positive"),
        (Decimal("-0.1"), "qty must be positive"),
        (HELD, "is not a fraction"),
        (HELD * 2, "is not a fraction"),
    ],
)
def test_a_scale_out_that_is_not_a_fraction_of_the_position_is_refused(repo, qty, why):  # noqa: F811
    """A `qty` at or above the held size is a FULL exit, and a full exit belongs to `execute`'s
    EXIT path -- which also retires `position_rule:`, the levels and the crash ledger, none of
    which this function owns. Doing it here would leave the product with no owning rule and
    stale levels that rail 9 would then veto the next legitimate entry against."""
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=qty,
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert result.placed is False
    assert why in result.reason
    assert broker.events == []


def test_a_position_with_no_recorded_levels_is_refused(repo):  # noqa: F811
    """The subtle refusal. Without `open_stop`/`open_target` there is nothing to re-place, so
    cancelling would leave the remainder naked AND with no levels for the sweep to heal from --
    `_rebracket_or_escalate` refuses to invent them. Not scaling out is strictly better."""
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)
    repo.set_state(f"open_target:{PRODUCT}", None)

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert result.placed is False
    assert "no open_stop/open_target recorded" in result.reason
    assert broker.events == []


# -- the ledger and rail 16 ------------------------------------------------------------------


def test_the_sold_fraction_reduces_the_tranche_and_records_no_outcome_yet(repo):  # noqa: F811
    """The `positions.qty` UPDATE that did not exist before #502, and the row that must NOT be
    written yet.

    `qty` means WHAT IS STILL HELD -- `reconcile_unbracketed_positions` sizes its healing
    bracket from it. Leaving it at the original size after a partial sale would have the sweep
    commit more base than the account holds. And the trade is not over, so per §2 no outcome
    row exists yet: the half sold here is carried on the tranche until the last unit closes.
    """
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)

    scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    position = repo.get_open_positions(PRODUCT)[0]
    assert position["qty"] == Decimal("0.1")
    assert position["realized_qty"] == Decimal("0.1")
    assert position["realized_proceeds"] == TARGET * Decimal("0.1")
    assert position["realized_fees"] == Decimal("0.30")  # the previewed commission
    assert repo.get_trade_outcomes() == []


def test_a_scaled_out_net_winner_is_one_row_and_is_not_counted_as_a_loss(repo):  # noqa: F811
    """THE headline defect #502 names, end to end.

    Half comes off at the target for a real profit; the runner is later closed at break-even,
    which after fees is a small loss ON ITS OWN. Booked as two rows -- the tidy-looking
    alternative -- rail 16 would see that second row and increment its consecutive-loss counter,
    on a trade that made money. Every profitable scale-out would do it, so the breaker would
    fire on precisely the strategy the capability exists to enable.

    §2 of the trade-outcomes design settles this: one trade, one row, P&L summed across the
    partial exits, and the outcome unknown until the last unit closes.
    """
    broker = FakeBroker()
    _seed_bracketed_tranche(repo, broker)
    config = _config()

    scale_out(
        broker,
        repo,
        config,
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,  # +3000/unit on half the position
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )
    assert repo.get_trade_outcomes() == []
    assert repo.get_state("consecutive_losses", default=0) in (0, None)

    # The runner stops out at entry: break-even gross, a fee-sized loss on its own.
    runner = repo.insert_order(
        dict(
            mode="live",
            product_id=PRODUCT,
            side="SELL",
            order_type="market",
            qty=Decimal("0.1"),
            limit_price=ENTRY,
            status="filled",
            fee=Decimal("0.30"),
            expected_fill=ENTRY,
            actual_fill=ENTRY,
            raw_response=None,
            confirmation="autonomous",
            rule_id=None,
            created_at=NOW_TS + 10,
            updated_at=NOW_TS + 10,
        )
    )
    streak.book_exit(
        repo,
        config,
        product_id=PRODUCT,
        exit_order=repo.get_order(runner),
        sold_qty=None,
        is_dca=False,
        now_ts=NOW_TS + 10,
    )

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1, (
        "a scale-out sequence is ONE trade and must produce ONE outcome row -- a row per leg "
        "hands rail 16 the runner's fee-sized loss on every profitable scale-out"
    )
    outcome = outcomes[0]
    assert outcome["qty"] == Decimal("0.2")  # both legs
    assert outcome["fees"] == Decimal("0.60")  # both exit legs
    # (53000 - 50000) * 0.1 + (50000 - 50000) * 0.1 - 0.60 exit fees - 1 entry fee
    assert outcome["pnl_net"] == Decimal("298.40")
    assert outcome["pnl_net"] > 0
    assert repo.get_state("consecutive_losses", default=0) == 0, (
        "rail 16 counted a scaled-out net winner as a loss"
    )


def test_a_sale_spanning_two_tranches_closes_the_first_and_reduces_the_second(repo):  # noqa: F811
    """FIFO across the tranche boundary, and the fee apportioned by what each leg CONTRIBUTED.

    Apportioning by tranche SIZE instead would over-charge the tranche the sale only partly
    consumed -- and `pnl_net`'s sign is the only thing rail 16 reads.
    """
    config = _config()
    older = repo.open_position(
        product_id=PRODUCT,
        rule_name="pullback_continuation",
        opened_at=NOW_TS - 2000,
        qty=Decimal("0.1"),
        entry_fill=ENTRY,
        entry_fee=Decimal("0"),
    )
    newer = repo.open_position(
        product_id=PRODUCT,
        rule_name="pullback_continuation",
        opened_at=NOW_TS - 1000,
        qty=Decimal("0.1"),
        entry_fill=ENTRY,
        entry_fee=Decimal("0"),
    )
    exit_id = repo.insert_order(
        dict(
            mode="live",
            product_id=PRODUCT,
            side="SELL",
            order_type="market",
            qty=Decimal("0.15"),
            limit_price=TARGET,
            status="filled",
            fee=Decimal("1.00"),
            expected_fill=TARGET,
            actual_fill=TARGET,
            raw_response=None,
            confirmation="autonomous",
            rule_id=None,
            created_at=NOW_TS,
            updated_at=NOW_TS,
        )
    )

    streak.book_exit(
        repo,
        config,
        product_id=PRODUCT,
        exit_order=repo.get_order(exit_id),
        sold_qty=Decimal("0.15"),
        is_dca=False,
        now_ts=NOW_TS,
    )

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["qty"] == Decimal("0.1")  # the older tranche, closed whole
    assert outcomes[0]["fees"] == Decimal("0.66666667")  # 1.00 * 0.10/0.15

    open_positions = repo.get_open_positions(PRODUCT)
    assert [p["id"] for p in open_positions] == [newer]
    assert open_positions[0]["qty"] == Decimal("0.05")
    assert open_positions[0]["realized_qty"] == Decimal("0.05")
    assert open_positions[0]["realized_fees"] == Decimal("0.33333333")  # the remainder
    assert older not in [p["id"] for p in open_positions]


class _ShortFillingBroker(FakeBroker):
    """Fills SELLs at `fill_ratio` of what was ordered and reports it through `get_order`, the
    way the venue does and the way `_record_observed_fill_quantity` reads it."""

    def __init__(self, fill_ratio: Decimal, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fill_ratio = fill_ratio
        self._last_sell_size: Decimal = Decimal("0")

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        if isinstance(spec, BracketGTC) is False and getattr(spec, "base_size", None) is not None:
            self._last_sell_size = Decimal(spec.base_size)
        return super().place_order(spec, idempotency_key=idempotency_key)

    def get_order(self, order_id: str) -> Any:
        from keel_broker_api.results import OrderStatus

        return OrderStatus(
            order_id=order_id,
            status="FILLED",
            filled_size=self._last_sell_size * self.fill_ratio,
            average_filled_price=TARGET,
            total_fees=Decimal("0.20"),
        )


def test_a_scale_out_the_venue_filled_short_books_only_what_sold(repo):  # noqa: F811
    """The quantity booked is the VENUE's, never the one the rule asked for.

    A market IOC that fills short has its remainder cancelled, so the short fill is FINAL. Book
    the requested 0.1 when only 0.06 sold and the ledger claims base was released that the
    account still holds -- the tranche then under-reports what is held, and the next healing
    bracket protects less of the position than exists, quietly.
    """
    broker = _ShortFillingBroker(Decimal("0.6"))
    _seed_bracketed_tranche(repo, broker)

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id=PRODUCT,
        qty=Decimal("0.1"),
        exit_price=TARGET,
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert result.placed is True
    position = repo.get_open_positions(PRODUCT)[0]
    assert position["realized_qty"] == Decimal("0.06"), (
        "the tranche was reduced by the REQUESTED quantity rather than the one the venue sold"
    )
    assert position["qty"] == Decimal("0.14")
