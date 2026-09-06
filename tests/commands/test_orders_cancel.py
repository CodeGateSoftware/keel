"""`keel orders cancel` -- the cancel asymmetry (#707).

Cancelling an open ENTRY is refusing risk. The constitution says refusing risk is frictionless, so
it asks once and does it.

Cancelling an open EXIT or a protective bracket is REMOVING PROTECTION. That is the same class of
action as disabling a stop, and it takes the typed friction every other capability-increasing step
in this program takes. No broker makes this distinction; it falls straight out of keel's own rails.

The classification is the whole feature, so most of what follows is about getting it right in the
cases where a row does not announce which kind it is.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from keel.commands import orders as orders_mod
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_756_000_000


@pytest.fixture()
def repo(tmp_path) -> Repository:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


def _order(repo: Repository, **overrides: Any) -> int:
    row: dict[str, Any] = {
        "mode": "live",
        "product_id": "BTC-USD",
        "side": "buy",
        "qty": Decimal("1"),
        "status": "pending",
        "created_at": NOW - 100,
        "raw_response": '{"order_id": "venue-1"}',
    }
    row.update(overrides)
    return repo.insert_order(row)


# -- classification --------------------------------------------------------------------------------


def test_a_resting_buy_is_an_entry(repo: Repository) -> None:
    order_id = _order(repo, side="buy")
    assert orders_mod.classify_cancel(repo, order_id).kind == "entry"


def test_a_resting_sell_is_an_exit(repo: Repository) -> None:
    """The side label alone is enough to make it typed-friction. A SELL that is not protecting
    anything is still liquidating inventory the operator holds."""
    order_id = _order(repo, side="sell")
    assert orders_mod.classify_cancel(repo, order_id).kind == "exit"


def test_a_buy_that_is_some_positions_bracket_is_PROTECTIVE_not_an_entry(
    repo: Repository,
) -> None:
    """The guard that matters, and the reason the side label is not enough on its own.

    `positions.bracket_order_id` is the link, and a protective leg is the real hazard rather than
    the word "sell": a row wearing the entry side while a position points at it as its protection
    would be cancelled one-click under a side-only rule, stripping a stop from a live tranche.
    """
    order_id = _order(repo, side="buy")
    position_id = repo.open_position(
        product_id="BTC-USD",
        rule_name="breakout",
        qty=Decimal("1"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("5"),
        opened_at=NOW - 200,
        bracket_order_id=order_id,
    )
    assert position_id
    decision = orders_mod.classify_cancel(repo, order_id)
    assert decision.kind == "protective"
    assert decision.typed is True


def test_an_entry_asks_once_and_a_protective_leg_demands_the_phrase(repo: Repository) -> None:
    """The asymmetry, as the one field both front-ends read."""
    entry = _order(repo, side="buy")
    exit_order = _order(repo, side="sell")

    assert orders_mod.classify_cancel(repo, entry).typed is False
    assert orders_mod.classify_cancel(repo, exit_order).typed is True


# -- refusals --------------------------------------------------------------------------------------


def test_an_unknown_order_is_a_named_refusal(repo: Repository) -> None:
    decision = orders_mod.classify_cancel(repo, 999)
    assert decision.kind == "unknown"
    assert decision.cancellable is False
    assert "999" in decision.reason


def test_a_filled_order_is_refused_and_never_silently_ignored(repo: Repository) -> None:
    """A no-op that reports success is the worst answer here: the operator believes they have
    cancelled something that is still live, or already spent."""
    order_id = _order(repo, status="filled")
    decision = orders_mod.classify_cancel(repo, order_id)

    assert decision.cancellable is False
    assert "filled" in decision.reason


def test_an_already_canceled_order_is_refused_by_name(repo: Repository) -> None:
    """Idempotency without a lie. A second cancel does not reach the venue and does not claim to
    have done anything."""
    order_id = _order(repo, status="canceled")
    decision = orders_mod.classify_cancel(repo, order_id)

    assert decision.cancellable is False
    assert "canceled" in decision.reason


def test_a_partially_filled_order_is_still_cancellable(repo: Repository) -> None:
    """Its remainder is working at the exchange exactly like a pending order's whole size --
    `executor.RESTING_STATUSES` is the same list, and this reads it rather than restating it."""
    order_id = _order(repo, status="partially_filled")
    assert orders_mod.classify_cancel(repo, order_id).cancellable is True


def test_the_resting_statuses_come_from_the_executor(repo: Repository) -> None:
    """One list, not two. A second copy would drift the day the executor learned a third resting
    state, and this surface would then refuse to cancel something the engine considers live."""
    from keel.execution.executor import RESTING_STATUSES

    assert orders_mod.CANCELLABLE_STATUSES == RESTING_STATUSES


# -- the orphaned protective leg -------------------------------------------------------------------


def test_cancelling_a_zero_filled_entry_clears_its_orphaned_bracket(repo: Repository) -> None:
    """`executor.execute` places the bracket as soon as the entry is PLACED, not once it fills, so
    a resting entry can already have a protective leg. Cancel the entry and that leg is committing
    base inventory that was never acquired -- an orphan, and it must go with the entry."""
    entry = _order(repo, side="buy", status="pending")
    assert orders_mod.classify_cancel(repo, entry).clears_bracket is True


def test_cancelling_a_PARTIALLY_filled_entry_leaves_its_bracket_alone(repo: Repository) -> None:
    """The case the issue's rule does not cover, and the direction that matters.

    A partially filled entry means inventory the operator ACTUALLY HOLDS, and the bracket is what
    protects it. Clearing it "because we cancelled an entry" would strip a stop from a live
    tranche -- the exit-side hazard reappearing inside an entry-side action, which is exactly what
    the asymmetry exists to prevent. The remainder is cancelled; the protection stays.
    """
    entry = _order(repo, side="buy", status="partially_filled", filled_quantity=Decimal("0.4"))
    assert orders_mod.classify_cancel(repo, entry).clears_bracket is False


# -- the CLI ---------------------------------------------------------------------------------------


class _Broker:
    """A broker that confirms every cancel, and remembers which ones it was asked for."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def cancel_order(self, native_id: str) -> bool:
        self.cancelled.append(native_id)
        return True


@pytest.fixture()
def deployment(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from tests.conftest import VALID_CONFIG_YAML

    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)

    broker = _Broker()
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    monkeypatch.setattr("keel.commands.orders._build_broker", lambda _cfg: broker, raising=False)
    monkeypatch.setattr("keel.commands._common._build_broker", lambda _cfg: broker)
    return db_path, config_path, broker


def _run(deployment, args: list[str], stdin: str = ""):
    from click.testing import CliRunner

    from keel.cli import cli

    db_path, config_path, _broker = deployment
    return CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), *args], input=stdin
    )


def _book(deployment) -> Repository:
    db_path, _config, _broker = deployment
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def test_keel_orders_still_lists_with_no_subcommand(deployment) -> None:
    """#707 turned this into a group. `keel orders --scope 7d` is what an operator's fingers and
    every runbook already know, and a group that stopped answering it would be a breaking change
    dressed as a feature."""
    result = _run(deployment, ["orders", "--scope", "7d"])
    assert result.exit_code == 0, result.output


def test_cancelling_an_entry_asks_once_and_reaches_the_venue(deployment) -> None:
    repo = _book(deployment)
    order_id = _order(repo, side="buy")
    repo._conn.close()  # noqa: SLF001

    result = _run(deployment, ["orders", "cancel", str(order_id)], stdin="y\n")

    assert result.exit_code == 0, result.output
    assert deployment[2].cancelled == ["venue-1"]
    assert _book(deployment).get_order(order_id)["status"] == "canceled"


def test_declining_the_entry_prompt_cancels_nothing(deployment) -> None:
    repo = _book(deployment)
    order_id = _order(repo, side="buy")
    repo._conn.close()  # noqa: SLF001

    result = _run(deployment, ["orders", "cancel", str(order_id)], stdin="n\n")

    assert result.exit_code != 0
    assert deployment[2].cancelled == []
    assert _book(deployment).get_order(order_id)["status"] == "pending"


def test_an_exit_needs_the_typed_phrase_and_a_y_will_not_do(deployment) -> None:
    """The friction is the feature. A `y` here is the muscle memory an entry prompt trains, and
    it must not reach a protective leg."""
    repo = _book(deployment)
    order_id = _order(repo, side="sell")
    repo._conn.close()  # noqa: SLF001

    result = _run(deployment, ["orders", "cancel", str(order_id)], stdin="y\n")

    assert result.exit_code != 0
    assert "phrase not typed" in result.output
    assert deployment[2].cancelled == []


def test_the_phrase_names_the_order_so_it_cannot_be_reused(deployment) -> None:
    """A phrase copied from one prompt must not answer a different one -- otherwise the friction
    is a ritual rather than a check on WHICH protection is being removed."""
    repo = _book(deployment)
    first = _order(repo, side="sell")
    second = _order(repo, side="sell")
    repo._conn.close()  # noqa: SLF001

    wrong = orders_mod.CANCEL_EXIT_PHRASE.format(order_id=first)
    result = _run(deployment, ["orders", "cancel", str(second)], stdin=wrong + "\n")

    assert result.exit_code != 0
    assert deployment[2].cancelled == []


def test_the_typed_phrase_cancels_a_protective_leg(deployment) -> None:
    repo = _book(deployment)
    order_id = _order(repo, side="sell")
    repo._conn.close()  # noqa: SLF001

    phrase = orders_mod.CANCEL_EXIT_PHRASE.format(order_id=order_id)
    result = _run(deployment, ["orders", "cancel", str(order_id)], stdin=phrase + "\n")

    assert result.exit_code == 0, result.output
    assert deployment[2].cancelled == ["venue-1"]


def test_cancelling_off_a_terminal_is_refused(deployment, monkeypatch) -> None:
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: False)
    repo = _book(deployment)
    order_id = _order(repo, side="buy")
    repo._conn.close()  # noqa: SLF001

    result = _run(deployment, ["orders", "cancel", str(order_id)])

    assert result.exit_code != 0
    assert "interactive terminal" in result.output
    assert deployment[2].cancelled == []


def test_a_filled_order_is_refused_before_the_broker_is_built(deployment) -> None:
    """No venue call for an order that cannot be cancelled. The refusal is a read."""
    repo = _book(deployment)
    order_id = _order(repo, status="filled")
    repo._conn.close()  # noqa: SLF001

    result = _run(deployment, ["orders", "cancel", str(order_id)], stdin="y\n")

    assert result.exit_code != 0
    assert "filled" in result.output
    assert deployment[2].cancelled == []
