"""`keel positions close <id>` -- an operator-declared, out-of-band exit (#798, plan R4, P4).

The service writes an `orders` row where rails 4/5/6 read, books the one tranche it names, and
places nothing. The CLI in front of it is gated by a typed `yes` at a terminal.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from keel import cli as keel_cli
from keel.commands import _common
from keel.commands.doctor import unbooked_exit_findings
from keel.commands.positions_close import (
    DECLARED_CONFIRMATION,
    DECLARED_ORDER_TYPE,
    PositionCloseRefused,
    close_declared_position,
    close_gate_wording,
)
from keel.config import AutoTradeConfig, Config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import executor, guards
from tests.conftest import VALID_CONFIG_YAML
from tests.execution.test_executor import NOW_TS
from tests.execution.test_executor import _config as _base_config


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _config(mode: str = "confirm") -> Config:
    """A LIVE profile by default: `auto_trade.mode` defaults to paper, and a declared close is a
    statement about a venue sale, which a paper profile has none of."""
    return _base_config(auto_trade=AutoTradeConfig(mode=mode, interval_sec=900))


def _tranche(
    repo: Repository,
    *,
    rule_name: str = "turtle_breakout",
    qty: str = "0.0132",
    fill: str = "4673.23",
    fee: str = "0.73",
    ordered: str | None = None,
    opened_at: int = 1,
) -> int:
    """PAXG tranche 3's own numbers (#811): the BUY order row and the ledger tranche it opened,
    owned by a real `rules` row (the FK is enforced). `ordered` lets the ORDER row say a
    different size from the tranche (#900's shape)."""
    rule_id = repo.insert_rule(rule_name, {"product_id": "PAXG-USD"}, status="live")
    repo.insert_order(
        dict(
            mode="live",
            product_id="PAXG-USD",
            side="BUY",
            order_type="market",
            qty=Decimal(ordered or qty),
            status="filled",
            fee=Decimal(fee),
            expected_fill=Decimal(fill),
            actual_fill=Decimal(fill),
            confirmation="autonomous",
            rule_id=rule_id,
            created_at=opened_at,
            updated_at=opened_at,
        )
    )
    return repo.open_position(
        product_id="PAXG-USD",
        rule_name=rule_name,
        opened_at=opened_at,
        qty=Decimal(qty),
        entry_fill=Decimal(fill),
        entry_fee=Decimal(fee),
        rule_id=rule_id,
    )


def _close(repo: Repository, pid: int, **kw: Any) -> int:
    args: dict[str, Any] = dict(price=Decimal("4400"), fee=Decimal("0.52"), now_ts=NOW_TS)
    args.update(kw)
    return close_declared_position(repo, _config(), position_id=pid, **args)


def test_a_declared_close_releases_the_exposure_rails_4_5_6_read(repo: Repository) -> None:
    pid = _tranche(repo)
    [tranche] = repo.get_open_positions()
    assert tranche["rule_id"] is not None
    assert guards._open_exposure_by_asset(repo)["PAXG"] > 0

    order_id = _close(repo, pid)

    assert "PAXG" not in guards._open_exposure_by_asset(repo), "#798: guard 6 must read it"
    row = repo.get_order(order_id)
    assert row is not None
    assert {
        k: row[k]
        for k in (
            "mode",
            "product_id",
            "side",
            "order_type",
            "status",
            "confirmation",
            "qty",
            "filled_quantity",
            "actual_fill",
            "fee",
            "rule_id",
            "created_at",
        )
    } == {
        "mode": "live",
        "product_id": "PAXG-USD",
        "side": "SELL",
        "order_type": "out_of_band",
        "status": "filled",
        "confirmation": "operator_declared",
        "qty": Decimal("0.0132"),
        "filled_quantity": Decimal("0.0132"),
        "actual_fill": Decimal("4400"),
        "fee": Decimal("0.52"),
        "rule_id": tranche["rule_id"],
        "created_at": NOW_TS,
    }
    assert repo.get_open_positions("PAXG-USD") == []


def test_the_sell_is_sized_from_the_tranche_not_from_the_order_log(repo: Repository) -> None:
    """#900: the ledger, not the orders log, is what a close books -- so when #900 corrects
    tranche quantities to the venue's filled size, a declared close follows the correction and
    `ledger.drift` keeps comparing like with like. The BUY row here says 0.0133 (ordered); the
    tranche says 0.0132 (held). The SELL is the tranche's."""
    pid = _tranche(repo, ordered="0.0133")

    order_id = _close(repo, pid)

    row = repo.get_order(order_id)
    assert row is not None
    assert (row["qty"], row["filled_quantity"]) == (Decimal("0.0132"), Decimal("0.0132"))


def test_the_outcome_is_booked_per_the_tranche_own_kind(repo: Repository) -> None:
    pid = _tranche(repo, rule_name="dca")
    _close(repo, pid, fee=Decimal("0"))

    [outcome] = repo.get_trade_outcomes()
    assert (outcome["rule_name"], outcome["is_dca"]) == ("dca", True)
    assert repo.get_state("consecutive_losses") is None, "DCA is exempt from the streak"


def test_a_non_dca_loss_is_booked_and_counts_toward_rail_16(repo: Repository) -> None:
    """R4's stated cost, pinned: an honest loss on a rule's tranche is a loss."""
    pid = _tranche(repo)
    _close(repo, pid)

    [outcome] = repo.get_trade_outcomes()
    expected = (
        Decimal("4400") * Decimal("0.0132")
        - Decimal("4673.23") * Decimal("0.0132")
        - Decimal("0.52")
        - Decimal("0.73")
    )
    assert {k: outcome[k] for k in ("is_dca", "qty", "exit_fill", "fees", "pnl_net")} == {
        "is_dca": False,
        "qty": Decimal("0.0132"),
        "exit_fill": Decimal("4400"),
        "fees": Decimal("0.52"),
        "pnl_net": expected,
    }
    assert repo.get_state("consecutive_losses") == 1


def test_a_scaled_out_tranche_sells_only_what_remains(repo: Repository) -> None:
    """`positions.qty` on an open tranche is what REMAINS after a scale-out (#502). The declared
    SELL must be that remainder -- the sold leg already has its own SELL row -- and the outcome
    folds the earlier leg in, as every other close does."""
    pid = _tranche(repo, qty="0.02")
    repo.reduce_position(
        pid,
        remaining_qty=Decimal("0.0068"),
        realized_qty=Decimal("0.0132"),
        realized_proceeds=Decimal("0.0132") * Decimal("5000"),
        realized_fees=Decimal("0.4"),
    )

    order_id = _close(repo, pid)

    row = repo.get_order(order_id)
    assert row is not None
    assert row["qty"] == Decimal("0.0068")
    [outcome] = repo.get_trade_outcomes()
    assert (outcome["qty"], outcome["fees"]) == (Decimal("0.02"), Decimal("0.92"))


def test_the_exit_ownership_state_retires_with_the_last_tranche(repo: Repository) -> None:
    pid = _tranche(repo)
    repo.set_state("position_rule:PAXG-USD", {"rule_name": "turtle_breakout", "opened_at": 1})
    repo.set_state("open_stop:PAXG-USD", "4521")
    repo.set_state("open_target:PAXG-USD", "5000")
    repo.set_state(
        f"{executor.UNBRACKETED_PREFIX}PAXG-USD", {"stop": "4521", "target": "5000", "qty": "1"}
    )

    _close(repo, pid, fee=Decimal("0"))

    for prefix in ("position_rule:", "open_stop:", "open_target:", executor.UNBRACKETED_PREFIX):
        assert repo.get_state(f"{prefix}PAXG-USD") is None, prefix


def test_closing_one_of_several_tranches_keeps_the_exit_state(repo: Repository) -> None:
    """The product still holds a tranche, so the keys that own and protect it stay -- including
    the retry record, whose `qty` a re-bracket never reads (it sizes from each tranche)."""
    older = _tranche(repo, qty="0.01", opened_at=1)
    newer = _tranche(repo, qty="0.0132", opened_at=2)
    state = {
        "position_rule:": {"rule_name": "turtle_breakout", "opened_at": 1},
        "open_stop:": "4521",
        "open_target:": "5000",
        executor.UNBRACKETED_PREFIX: {"stop": "4521", "target": "5000", "qty": "0.0232"},
    }
    for prefix, value in state.items():
        repo.set_state(f"{prefix}PAXG-USD", value)

    _close(repo, newer)

    assert [p["id"] for p in repo.get_open_positions("PAXG-USD")] == [older]
    assert {prefix: repo.get_state(f"{prefix}PAXG-USD") for prefix in state} == state


def test_closing_a_newer_tranche_leaves_doctor_quiet_about_the_older_one(
    repo: Repository,
) -> None:
    """End to end over the real rows: the declared SELL is dated after the older tranche opened,
    which is exactly what `ledger.unbooked_exit` reads as "sold but not booked"."""
    _tranche(repo, qty="0.01", opened_at=1)
    newer = _tranche(repo, qty="0.0132", opened_at=2)

    _close(repo, newer)

    assert len(repo.get_open_positions()) == 1
    assert len([o for o in repo.get_orders() if o["side"] == "SELL"]) == 1
    (finding,) = unbooked_exit_findings(repo.get_open_positions(), repo.get_orders())
    assert finding.status == "ok"


def _written(repo: Repository) -> tuple[int, int, list[int]]:
    return (
        len(repo.get_orders()),
        len(repo.get_trade_outcomes()),
        [p["id"] for p in repo.get_open_positions()],
    )


@pytest.mark.parametrize(
    "price,fee",
    [
        (Decimal("0"), Decimal("0")),
        (Decimal("-1"), Decimal("0")),
        (Decimal("1"), Decimal("-1")),
        (Decimal("NaN"), Decimal("0")),
        (Decimal("Infinity"), Decimal("0")),
        (Decimal("1"), Decimal("NaN")),
    ],
)
def test_nonsense_is_refused_before_anything_is_written(
    repo: Repository, price: Decimal, fee: Decimal
) -> None:
    pid = _tranche(repo)
    before = _written(repo)

    with pytest.raises(PositionCloseRefused):
        _close(repo, pid, price=price, fee=fee)

    assert _written(repo) == before


def test_an_already_closed_tranche_is_refused(repo: Repository) -> None:
    pid = _tranche(repo)
    repo.close_position(pid, closed_at=2)
    before = _written(repo)

    with pytest.raises(PositionCloseRefused, match="not open"):
        _close(repo, pid, price=Decimal("1"), fee=Decimal("0"))

    assert _written(repo) == before


def test_an_unknown_tranche_is_refused(repo: Repository) -> None:
    _tranche(repo)
    before = _written(repo)

    with pytest.raises(PositionCloseRefused, match="not open"):
        _close(repo, 999)

    assert _written(repo) == before


def test_a_paper_profile_is_refused(repo: Repository) -> None:
    """R4 writes `mode='live'`: the verb declares a sale made ON THE VENUE, and a paper profile
    has no venue to have sold on. Writing a live SELL into a paper database would put a row in
    front of rails that never saw its BUY."""
    pid = _tranche(repo)
    before = _written(repo)

    with pytest.raises(PositionCloseRefused, match="paper"):
        close_declared_position(
            repo,
            _config(mode="paper"),
            position_id=pid,
            price=Decimal("4400"),
            fee=Decimal("0"),
            now_ts=NOW_TS,
        )

    assert _written(repo) == before


# -- the gated CLI verb (Task 4.2) ---------------------------------------------------------------


def _file_repo(db: Path) -> Repository:
    conn = connect(str(db))
    migrate(conn)
    return Repository(conn)


@pytest.fixture
def live_config_path(write_config: Any) -> Path:
    """`VALID_CONFIG_YAML` is a paper profile, and the verb refuses paper (R4 writes `live`)."""
    text = VALID_CONFIG_YAML.replace("mode: paper", "mode: confirm")
    assert text != VALID_CONFIG_YAML, "the fixture no longer says `mode: paper` -- update this"
    return Path(write_config(text))


@pytest.fixture
def no_broker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """S1: the command builds no broker at all. Every construction seam raises, and is counted,
    so a test that never reached the command cannot pass this by vacuum."""
    built: list[str] = []

    def _refuse(*_a: Any, **_k: Any) -> Any:
        built.append("broker")
        raise AssertionError("keel positions close must not build a broker")

    monkeypatch.setattr(_common, "_build_broker", _refuse)
    monkeypatch.setattr(keel_cli, "_build_broker", _refuse)
    return built


def _seeded(tmp_path: Path) -> tuple[Path, int]:
    db = tmp_path / "t.db"
    return db, _tranche(_file_repo(db))


def _invoke(db: Path, config: Path, pid: int, *extra: str, input: str | None = None) -> Result:
    return CliRunner().invoke(
        keel_cli.cli,
        ["--db", str(db), "--config", str(config), "positions", "close", str(pid), *extra],
        input=input,
    )


def _close_cli(db: Path, config: Path, pid: int, input: str | None = None) -> Result:
    return _invoke(db, config, pid, "--price", "4400", "--fee", "0.52", input=input)


def _declared(db: Path) -> list[dict[str, Any]]:
    return [o for o in _file_repo(db).get_orders() if o["order_type"] == DECLARED_ORDER_TYPE]


def test_off_a_tty_the_close_is_refused_and_nothing_is_written(
    tmp_path: Path, live_config_path: Path, monkeypatch: pytest.MonkeyPatch, no_broker: list[str]
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    db, pid = _seeded(tmp_path)
    before = _written(_file_repo(db))

    result = _close_cli(db, live_config_path, pid)

    assert result.exit_code != 0
    action, _detail = close_gate_wording(_file_repo(db).get_open_positions()[0], Decimal("4400"))
    assert (
        f"Error: refusing to {action}: this needs confirmation from an interactive terminal."
        in result.output.splitlines()
    )
    assert _written(_file_repo(db)) == before
    assert no_broker == []


def test_at_a_tty_a_typed_yes_records_it(
    tmp_path: Path, live_config_path: Path, monkeypatch: pytest.MonkeyPatch, no_broker: list[str]
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, pid = _seeded(tmp_path)
    [tranche] = _file_repo(db).get_open_positions()

    result = _close_cli(db, live_config_path, pid, input="yes\n")

    assert result.exit_code == 0, result.output
    action, detail = close_gate_wording(tranche, Decimal("4400"))
    lines = result.output.splitlines()
    assert f"About to {action}." in lines
    assert f"  {detail}" in lines
    [sell] = _declared(db)
    assert (sell["confirmation"], sell["qty"], sell["actual_fill"], sell["fee"]) == (
        DECLARED_CONFIRMATION,
        Decimal("0.0132"),
        Decimal("4400"),
        Decimal("0.52"),
    )
    assert f"recorded order {sell['id']}: tranche {pid} closed out-of-band" in lines
    assert _file_repo(db).get_open_positions() == []
    assert no_broker == []


def test_the_fee_defaults_to_zero(
    tmp_path: Path, live_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, pid = _seeded(tmp_path)

    result = _invoke(db, live_config_path, pid, "--price", "4400", input="yes\n")

    assert result.exit_code == 0, result.output
    [sell] = _declared(db)
    assert sell["fee"] == Decimal("0")


def test_at_a_tty_anything_but_yes_writes_nothing(
    tmp_path: Path, live_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    db, pid = _seeded(tmp_path)
    before = _written(_file_repo(db))

    result = _close_cli(db, live_config_path, pid, input="y\n")

    assert result.exit_code != 0
    assert "Error: aborted (confirmation not given)." in result.output.splitlines()
    assert _written(_file_repo(db)) == before


@pytest.mark.parametrize(
    "args,refusal",
    [
        (("--price", "0"), "--price must be a positive number, got 0"),
        (("--price", "4400", "--fee", "-1"), "--fee must be zero or a positive number, got -1"),
    ],
)
def test_nonsense_is_refused_before_the_gate_asks(
    tmp_path: Path,
    live_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    refusal: str,
) -> None:
    """A refusal the service would make anyway is made BEFORE the typed gate, so the operator is
    never asked to confirm a row that cannot be written."""
    asked: list[bool] = []
    monkeypatch.setattr(_common, "_is_interactive", lambda: asked.append(True) or True)
    db, pid = _seeded(tmp_path)
    before = _written(_file_repo(db))

    result = _invoke(db, live_config_path, pid, *args, input="yes\n")

    assert result.exit_code != 0
    assert f"Error: {refusal}" in result.output.splitlines()
    assert asked == []
    assert _written(_file_repo(db)) == before


def test_an_unknown_tranche_is_refused_before_the_gate_asks(
    tmp_path: Path, live_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[bool] = []
    monkeypatch.setattr(_common, "_is_interactive", lambda: asked.append(True) or True)
    db, _pid = _seeded(tmp_path)

    result = _invoke(db, live_config_path, 999, "--price", "4400", input="yes\n")

    assert result.exit_code != 0
    assert asked == []
    assert _declared(db) == []
