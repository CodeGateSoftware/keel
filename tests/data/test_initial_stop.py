"""`positions.initial_stop` — the stop a tranche was SIZED against (#520).

The break-even arm of `exit_policy.next_stop` needs the trade's ORIGINAL per-unit risk
(`entry + be_roll_rr * (entry - initial_stop)`). Live state carried `entry_fill` and the current,
ratcheting `open_stop:<product_id>` and nothing else, so the number the threshold is most
sensitive to was simply absent.

The distinction these tests defend: `None` means UNKNOWN, and a reader must disable the
break-even arm for that tranche rather than substitute the current stop. Substituting is not an
approximation — the current stop rises on every ratchet, so the threshold would creep toward
entry and the arm would fire earlier each time, drifting further from the measured policy the
longer a trade runs.
"""

from __future__ import annotations

from decimal import Decimal

from keel.data import db
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def _open(repo: Repository, **overrides):  # noqa: ANN003, ANN202
    kwargs = dict(
        product_id="BTC-USD",
        rule_name="pullback_continuation",
        opened_at=1_787_000_000,
        qty=Decimal("0.01"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("0.5"),
    )
    kwargs.update(overrides)
    return repo.open_position(**kwargs)


def test_a_recorded_initial_stop_round_trips_as_a_decimal() -> None:
    repo = _repo()
    _open(repo, initial_stop=Decimal("49000"))

    position = repo.get_open_positions("BTC-USD")[0]
    assert position["initial_stop"] == Decimal("49000")
    assert isinstance(position["initial_stop"], Decimal)


def test_an_unrecorded_initial_stop_reads_as_None_not_zero() -> None:
    """The load-bearing distinction. Zero would be a stop 100% below entry -- a real number, and
    a catastrophic one to compute a break-even threshold from. `None` says 'nobody recorded it'."""
    repo = _repo()
    _open(repo)  # no initial_stop -- DCA, or any caller that has none

    position = repo.get_open_positions("BTC-USD")[0]
    assert position["initial_stop"] is None


def test_dca_style_open_without_a_stop_is_accepted_not_rejected() -> None:
    """DCA has no stop BY DESIGN. Requiring one would refuse a legitimate tranche."""
    repo = _repo()
    position_id = _open(repo, rule_name="dca", initial_stop=None)

    assert position_id is not None
    assert repo.get_open_positions("BTC-USD")[0]["initial_stop"] is None


def test_the_column_exists_on_a_freshly_migrated_database() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(positions)")}
    assert "initial_stop" in columns


def test_the_migration_is_idempotent_and_does_not_backfill() -> None:
    """A database stamped before v12 got `positions` from v4's DDL, which has no such column, and
    `CREATE TABLE IF NOT EXISTS` never adds one -- hence the `PRAGMA table_info` guard.

    The honest value for a pre-v12 tranche is NULL. A backfill would fabricate the one input the
    policy is most sensitive to.
    """
    conn = db.connect(":memory:")
    db.migrate(conn)
    repo = Repository(conn)
    _open(repo, initial_stop=Decimal("49000"))

    db.migrate(conn)  # running it again must not raise, duplicate the column, or rewrite data

    columns = [row["name"] for row in conn.execute("PRAGMA table_info(positions)")]
    assert columns.count("initial_stop") == 1
    assert repo.get_open_positions("BTC-USD")[0]["initial_stop"] == Decimal("49000")


def test_initial_stop_is_not_rewritten_when_the_bracket_moves() -> None:
    """`open_stop:<product>` ratchets; this does not. That separation IS the fix -- if the ledger
    tracked the current stop there would be nothing to compute the original risk from."""
    repo = _repo()
    position_id = _open(repo, initial_stop=Decimal("49000"))

    repo.set_position_bracket(position_id, None)
    repo.set_state("open_stop:BTC-USD", Decimal("50500"))  # a ratchet moved the live stop

    assert repo.get_open_positions("BTC-USD")[0]["initial_stop"] == Decimal("49000")
