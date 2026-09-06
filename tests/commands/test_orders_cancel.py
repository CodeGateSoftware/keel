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

    def __init__(self, refuse: tuple[str, ...] = ()) -> None:
        self.cancelled: list[str] = []
        self.refuse = refuse

    def cancel_order(self, native_id: str) -> bool:
        self.cancelled.append(native_id)
        # `False` is a REFUSED cancel on a successful call -- Coinbase answers per order, and
        # `_cancel_at_exchange` treats anything but CONFIRMED as "still live at the venue".
        return native_id not in self.refuse


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


# -- the report carries the classification, and the console reads it ------------------------------
#
# The web console never cancels anything: `keel serve` holds no venue credential and no broker
# handle, and #707's decision is that it never will. What it CAN do is classify -- that is a read --
# and hand the operator the exact terminal invocation. The classification therefore has to reach
# the report, and it has to be the SAME function the CLI gates on, or the console could describe an
# order one way while the terminal treats it another.


def test_the_report_classifies_every_row(repo: Repository) -> None:
    from keel.commands.orders import gather_orders

    entry = _order(repo, side="buy")
    exit_order = _order(repo, side="sell")
    bracket = _order(repo, side="buy")
    repo.open_position(
        product_id="BTC-USD",
        rule_name="breakout",
        qty=Decimal("1"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("5"),
        opened_at=NOW - 200,
        bracket_order_id=bracket,
    )

    report = gather_orders(repo, now_ts=NOW, scope="all")
    kinds = {row.id: row.cancel.kind for row in report.rows}

    assert kinds[entry] == "entry"
    assert kinds[exit_order] == "exit"
    assert kinds[bracket] == "protective"


def test_the_report_and_the_cli_gate_on_one_classification(repo: Repository) -> None:
    """One function, two front-ends. If each decided for itself, the console could tell an
    operator an order is a frictionless entry while the terminal demanded the phrase for it."""
    from keel.commands.orders import gather_orders

    for side in ("buy", "sell"):
        _order(repo, side=side)
    report = gather_orders(repo, now_ts=NOW, scope="all")

    for row in report.rows:
        assert row.cancel == orders_mod.classify_cancel(repo, row.id)


def test_classifying_a_page_of_orders_does_not_query_per_row(repo: Repository) -> None:
    """`get_position_for_bracket` per row is a query per row, and this page is capped at 2,000.

    The batch and the single lookup are the SAME rule -- `classify_cancel` takes the precomputed
    set when it has one and looks the row up when it does not -- so there is one classification,
    not a fast one and a careful one that can disagree.

    Counted through `sqlite3`'s own trace callback rather than by patching `execute`, which is
    read-only on a Connection.
    """
    from keel.commands.orders import gather_orders

    for _ in range(25):
        _order(repo, side="buy")

    seen: list[str] = []
    repo._conn.set_trace_callback(seen.append)  # noqa: SLF001
    try:
        gather_orders(repo, now_ts=NOW, scope="all")
    finally:
        repo._conn.set_trace_callback(None)  # noqa: SLF001

    lookups = [sql for sql in seen if "bracket_order_id" in sql]
    assert len(lookups) == 1, f"{len(lookups)} bracket queries for 25 rows"
    assert not [sql for sql in seen if sql.strip().upper().startswith(("INSERT", "UPDATE"))]


def test_the_invocation_is_composed_in_python_and_names_the_order(repo: Repository) -> None:
    """Rule 2: the client places this string and does not build it. A console that concatenated
    the command itself could drift from the command that exists."""
    order_id = _order(repo, side="buy")
    decision = orders_mod.classify_cancel(repo, order_id)
    assert decision.invocation == f"keel orders cancel {order_id}"


def test_an_order_that_cannot_be_cancelled_offers_no_invocation(repo: Repository) -> None:
    """Handing an operator a command that would be refused is worse than handing them nothing:
    they run it, it fails, and they learn the console does not know what it is looking at."""
    order_id = _order(repo, status="filled")
    decision = orders_mod.classify_cancel(repo, order_id)

    assert decision.cancellable is False
    assert decision.invocation == ""


def test_a_bracket_that_cannot_be_cleared_fails_the_command_loudly(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry is gone and a protective leg may still be working at the venue over inventory
    that was never acquired.

    Reporting the cancel that DID succeed and stopping there would leave the operator believing
    the position is flat while a sell sits at the exchange. It is the same rule
    `_clear_resting_bracket` states for the executor -- an uncancellable bracket means we do not
    know what the exchange will do with that inventory -- and the operator is the only one who can
    act on it.
    """
    from tests.conftest import VALID_CONFIG_YAML

    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    repo = Repository(conn)
    entry = _order(repo, side="buy", status="pending", raw_response='{"order_id": "venue-entry"}')
    _order(repo, side="sell", status="pending", raw_response='{"order_id": "venue-bracket"}')
    conn.close()

    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    broker = _Broker(refuse=("venue-bracket",))
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    monkeypatch.setattr("keel.commands._common._build_broker", lambda _cfg: broker)

    from click.testing import CliRunner

    from keel.cli import cli

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "orders", "cancel", str(entry)],
        input="y\n",
    )

    assert result.exit_code != 0, result.output
    assert "could NOT be cleared" in result.output
    # The entry cancel itself still happened and is still recorded -- the failure is about what
    # is left behind, not about pretending the first call did not occur.
    conn = connect(str(db_path))
    migrate(conn)
    assert Repository(conn).get_order(entry)["status"] == "canceled"


# -- what an entry cancel may and may not take with it ---------------------------------------------


def _deployment_with(tmp_path, monkeypatch, refuse: tuple[str, ...] = ()):
    from tests.conftest import VALID_CONFIG_YAML

    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    broker = _Broker(refuse=refuse)
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    monkeypatch.setattr("keel.commands._common._build_broker", lambda _cfg: broker)
    return db_path, config_path, broker, conn


def test_cancelling_an_entry_NEVER_touches_a_live_tranches_bracket(tmp_path, monkeypatch) -> None:
    """THE finding this test exists for, and it was a one-`y` path to a naked position.

    The first cut reused `executor._clear_resting_bracket`, whose contract is PRODUCT-WIDE: it
    cancels every resting SELL for the product. That is right where the executor calls it, because
    the caller is about to place a replacement SELL over the same inventory. It is catastrophic
    here -- the entry is going away and nothing replaces the protection, so cancelling an entry on
    a product that already held an open bracketed tranche stripped that tranche's stop behind a
    single `y`, on the one code path deliberately built to be frictionless.

    Cancelling that bracket DIRECTLY demands the typed phrase. Reaching it sideways through an
    entry must not be a shortcut past that.

    The aftermath was silent: the tranche kept pointing at a cancelled order, and
    `reconcile_unbracketed_positions` skips a tranche with no `unbracketed:` record by design, so
    nothing healed it and nothing said anything.
    """
    db_path, config_path, broker, conn = _deployment_with(tmp_path, monkeypatch)
    repo = Repository(conn)

    bracket = _order(
        repo, side="sell", status="pending", raw_response='{"order_id": "venue-bracket"}'
    )
    repo.open_position(
        product_id="BTC-USD",
        rule_name="breakout",
        qty=Decimal("1"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("5"),
        opened_at=NOW - 500,
        bracket_order_id=bracket,
    )
    entry = _order(repo, side="buy", status="pending", raw_response='{"order_id": "venue-entry"}')
    conn.close()

    from click.testing import CliRunner

    from keel.cli import cli

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "orders", "cancel", str(entry)],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert broker.cancelled == ["venue-entry"], "the live tranche's bracket was cancelled too"

    conn = connect(str(db_path))
    migrate(conn)
    book = Repository(conn)
    assert book.get_order(entry)["status"] == "canceled"
    assert book.get_order(bracket)["status"] == "pending", "a live tranche was left with no stop"


def test_cancelling_an_entry_does_clear_a_bracket_no_position_relies_on(
    tmp_path, monkeypatch
) -> None:
    """The orphan the rule is actually for: a resting SELL that no OPEN tranche points at commits
    base inventory nothing acquired, and it goes with the entry."""
    db_path, config_path, broker, conn = _deployment_with(tmp_path, monkeypatch)
    repo = Repository(conn)

    orphan = _order(
        repo, side="sell", status="pending", raw_response='{"order_id": "venue-orphan"}'
    )
    entry = _order(repo, side="buy", status="pending", raw_response='{"order_id": "venue-entry"}')
    conn.close()

    from click.testing import CliRunner

    from keel.cli import cli

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "orders", "cancel", str(entry)],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert broker.cancelled == ["venue-entry", "venue-orphan"]

    conn = connect(str(db_path))
    migrate(conn)
    assert Repository(conn).get_order(orphan)["status"] == "canceled"


def test_a_fill_landing_while_the_operator_answers_stops_the_cancel(
    tmp_path, monkeypatch
) -> None:
    """A typed phrase is 34 characters, and a resting order can fill while it is being typed.

    The first cut classified once, before the prompt, and everything downstream read that stale
    decision -- so an entry that had become `filled` still ran the orphan sweep, and
    `clears_bracket` was answering a question about an order that no longer existed in that
    state. The operator answered a question about a different order from the one in front of them
    now, so the honest response is to refuse rather than to proceed on the old answer.
    """
    db_path, config_path, broker, conn = _deployment_with(tmp_path, monkeypatch)
    repo = Repository(conn)
    entry = _order(repo, side="buy", status="pending", raw_response='{"order_id": "venue-entry"}')
    conn.close()

    def _fill_then_confirm(*_args: object, **_kwargs: object) -> bool:
        book = connect(str(db_path))
        migrate(book)
        Repository(book).update_order(entry, status="filled", updated_at=NOW)
        book.close()
        return True

    monkeypatch.setattr("click.confirm", _fill_then_confirm)

    from click.testing import CliRunner

    from keel.cli import cli

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), "orders", "cancel", str(entry)]
    )

    assert result.exit_code != 0
    assert "changed while you were answering" in result.output
    assert broker.cancelled == [], "the venue was asked to cancel an order that had filled"


def test_whatever_the_order_filled_is_booked_before_it_is_marked_canceled(
    tmp_path, monkeypatch
) -> None:
    """`execution.reconcile` states the rule: a CANCELLED order can still have SOLD something, and
    `canceled` is terminal -- `_polled_rows` only revisits resting statuses, so a fill dropped here
    is dropped for good. `CANCELLABLE_STATUSES` deliberately includes `partially_filled`, which is
    exactly the row that carries one."""
    db_path, config_path, broker, conn = _deployment_with(tmp_path, monkeypatch)
    repo = Repository(conn)
    entry = _order(
        repo,
        side="buy",
        status="partially_filled",
        filled_quantity=Decimal("0.4"),
        raw_response='{"order_id": "venue-entry"}',
    )
    conn.close()

    seen: list[str] = []

    def _spy(_broker: object, _repo: object, row: dict, _now: int) -> None:
        # The status at the moment the fill is read back: still resting, because the terminal
        # write has not happened yet. Booked after it, the row would be unreachable.
        seen.append(str(row["status"]))

    monkeypatch.setattr("keel.execution.reconcile._try_record_fill", _spy, raising=False)

    from click.testing import CliRunner

    from keel.cli import cli

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "orders", "cancel", str(entry)],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert seen == ["partially_filled"], "the fill was never read back before the terminal write"


def test_a_venue_that_refuses_the_cancel_is_a_message_not_a_traceback(
    tmp_path, monkeypatch
) -> None:
    """`CancelUnavailable` is a `RuntimeError`, and `cli.main` re-raises everything. This is the
    LIKELY outcome of the window above -- the order filled while the operator typed -- and "the
    exchange refused" is a sentence they can act on where a Python traceback is not.

    Local state is untouched either way: `_cancel_at_exchange` marks nothing on failure, which is
    its own first rule.
    """
    db_path, config_path, broker, conn = _deployment_with(
        tmp_path, monkeypatch, refuse=("venue-entry",)
    )
    repo = Repository(conn)
    entry = _order(repo, side="buy", status="pending", raw_response='{"order_id": "venue-entry"}')
    conn.close()

    from click.testing import CliRunner

    from keel.cli import cli

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "orders", "cancel", str(entry)],
        input="y\n",
    )

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert "the venue did not cancel" in result.output

    conn = connect(str(db_path))
    migrate(conn)
    assert Repository(conn).get_order(entry)["status"] == "pending"
