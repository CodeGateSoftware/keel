"""`keel dca plan` -- the thin CLI over `keel.commands.dca_plan`.

Pins: off a TTY it prints and writes NOTHING (exit 0 approvable / 1 blocked, R12); at a TTY,
[Y] writes candidates, [N] writes nothing, [E] re-renders with edited weights before [Y].

R20: the command decides interactivity FIRST (`_common._is_interactive()`, attribute access),
THEN opens the database -- off a TTY with `_common._open_repo_ro(ctx)` (read-only, refuses a
missing file or a stale schema before any write could happen), at a TTY with `_common._open_repo`.
That ordering is what makes "off a TTY it writes nothing" hold even for a missing `--db` path
(a plain connect would create the file) or an old schema (`migrate()` would write).
"""

from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import _common
from keel.commands import dca as dca_cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from tests.commands.test_dca_plan import _screen
from tests.conftest import attest_subscription


def _repo(db: Path) -> Repository:
    conn = connect(str(db))
    migrate(conn)
    return Repository(conn)


#: #847: the default $500/0.1 budget/buffer's worst calendar month is $517.35 (5 x $103.47,
#: BTC/ETH/PAXG .4/.3/.3) or $517.40 (5 x $103.48, any renormalised 2-of-3 subset of the same
#: total) -- both exceed a $500 cap. 600 clears either, so this fixture's happy-path tests (about
#: CLI plumbing, not the cap defect itself) stay approvable; `test_dca_plan.py` pins the $500 cap
#: defect directly.
_ROOMY_CAP = Decimal("600")


@pytest.fixture
def deployment(tmp_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "t.db"
    attest_subscription(_repo(db), now_ts=int(time.time()), free_volume_usd=_ROOMY_CAP)
    monkeypatch.setattr(dca_cli, "screen_product", _screen())
    return db, valid_config_path


def _run(deployment, *args: str, input: str | None = None):
    db, config = deployment
    return CliRunner().invoke(
        cli,
        ["--db", str(db), "--config", str(config), "dca", "plan", *args],
        input=input,
    )


def test_off_a_tty_it_prints_the_plan_and_writes_nothing(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    # `PRAGMA data_version` on ONE connection held across the run changes iff ANOTHER connection
    # committed -- `total_changes` would not do here, it is per-connection. The fixture already
    # migrated the file, so an up-to-date `migrate()` inside the command has nothing to commit.
    watcher = sqlite3.connect(str(deployment[0]))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]

    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1")

    assert result.exit_code == 0, result.output
    assert "== Schedule ==" in result.output and "$41.39" in result.output
    assert "nothing written" in result.output
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
    watcher.close()
    assert _repo(deployment[0]).get_rules() == []
    # For the report: the exact CLI output of an off-TTY, approvable run.
    print(result.output)


def test_off_a_tty_a_blocked_plan_exits_1(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    result = _run(deployment, "--budget", "5000", "--buffer-pct", "0")  # over the 500 cap
    assert result.exit_code == 1
    assert "== Cannot approve ==" in result.output
    assert _repo(deployment[0]).get_rules() == []


def test_off_a_tty_a_missing_db_path_is_refused_and_creates_nothing(
    tmp_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R20: a missing `--db` path off a TTY must not be created. `_open_repo` (read-write)
    would `connect()` it into existence and `migrate()` it; `_open_repo_ro` refuses first."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    missing = tmp_path / "nope.db"
    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(missing),
            "--config",
            str(valid_config_path),
            "dca",
            "plan",
            "--budget",
            "500",
            "--buffer-pct",
            "0.1",
        ],
    )
    assert result.exit_code != 0
    assert not missing.exists()


def test_off_a_tty_the_database_is_opened_read_only(deployment, monkeypatch) -> None:
    """R20: prove the off-TTY path never reaches `_common._open_repo` (the read-write opener) --
    patch it to raise, and show the off-TTY happy path still exits 0. The same patch then makes
    the TTY path fail, proving the patch reaches whichever branch actually calls it."""

    def _boom(ctx):
        raise AssertionError("rw open off a TTY")

    monkeypatch.setattr(_common, "_open_repo", _boom)
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1")
    assert result.exit_code == 0, result.output

    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    result_tty = _run(deployment, "--budget", "500", "--buffer-pct", "0.1", input="N\n")
    assert result_tty.exit_code != 0, result_tty.output


def test_off_a_tty_open_repo_ro_is_called_exactly_once(deployment, monkeypatch) -> None:
    """The counter variant of the same pin: the off-TTY path opens the DB read-only, once."""
    calls = {"n": 0}
    real = _common._open_repo_ro

    def counting(ctx):
        calls["n"] += 1
        return real(ctx)

    monkeypatch.setattr(_common, "_open_repo_ro", counting)
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1")
    assert result.exit_code == 0, result.output
    assert calls["n"] == 1


def test_the_screen_fn_is_called_once_per_allowlisted_product(
    tmp_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the `deployment` fixture's monkeypatch of `dca_cli.screen_product` actually reaches
    the reader: an empty fixture that never gets called would pass every other test in this file
    by vacuum. VALID_CONFIG_YAML's allowlist is BTC/ETH/PAXG -- exactly three products."""
    db = tmp_path / "t.db"
    attest_subscription(_repo(db), now_ts=int(time.time()), free_volume_usd=_ROOMY_CAP)
    calls: list[str] = []
    real = _screen()

    def counting(repo, product, quote):
        calls.append(product)
        return real(repo, product, quote)

    monkeypatch.setattr(dca_cli, "screen_product", counting)
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db),
            "--config",
            str(valid_config_path),
            "dca",
            "plan",
            "--budget",
            "500",
            "--buffer-pct",
            "0.1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 3
    assert set(calls) == {"BTC-USD", "ETH-USD", "PAXG-USD"}


def test_yes_at_a_tty_writes_candidates(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1", input="Y\n")
    assert result.exit_code == 0, result.output
    rows = _repo(deployment[0]).get_rules()
    assert [(r["kind"], r["status"]) for r in rows] == [("dca", "candidate")] * 3
    assert result.output.count("added rule ") == 3
    # Structure, not substrings: the three rows are exactly the worked example's products,
    # each paired with its own per-buy amount.
    assert {r["params"]["product_id"] for r in rows} == {"BTC-USD", "ETH-USD", "PAXG-USD"}
    expected_budget_by_product = {
        "BTC-USD": Decimal("41.39"),
        "ETH-USD": Decimal("31.04"),
        "PAXG-USD": Decimal("31.04"),
    }
    actual_budget_by_product = {
        r["params"]["product_id"]: Decimal(r["params"]["budget_usd"]) for r in rows
    }
    assert actual_budget_by_product == expected_budget_by_product


def test_no_at_a_tty_writes_nothing(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1", input="N\n")
    assert result.exit_code == 0
    assert _repo(deployment[0]).get_rules() == []
    assert "cancelled" in result.output.lower()


def test_edit_renormalises_and_reshows_before_approval(deployment, monkeypatch) -> None:
    """[E]: BTC 1, ETH 1, PAXG 0 -> BTC/ETH at 50% each, PAXG excluded, then [Y]."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1", input="E\n1\n1\n0\nY\n")
    assert result.exit_code == 0, result.output
    assert result.output.count("== Schedule ==") == 2  # shown, edited, re-shown
    assert "set to 0 in this session" in result.output
    rows = _repo(deployment[0]).get_rules()
    assert {r["params"]["product_id"] for r in rows} == {"BTC-USD", "ETH-USD"}
    assert {Decimal(r["params"]["budget_usd"]) for r in rows} == {Decimal("51.74")}


def test_edit_reprompts_on_a_bad_weight_and_keeps_the_valid_one(deployment, monkeypatch) -> None:
    """`keel/commands/dca.py:155` -- `_edit_weights`'s `except DcaPlanError` branch had no test.
    BTC gets an invalid weight ("-1") first: the loop must re-issue the SAME prompt (`_edit_weights`
    stays on BTC, it does not advance to ETH) and echo `parse_weight`'s reason, before accepting
    a valid one ("1") and moving on."""
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1", input="E\n-1\n1\n1\n0\nY\n")
    assert result.exit_code == 0, result.output
    assert "weight for BTC must be 0 or more, got '-1'" in result.output
    # The prompt itself (not the error line, which also contains "weight for BTC") was re-issued
    # for BTC, not skipped past to ETH:
    assert result.output.count("weight for BTC [") == 2
    assert result.output.count("weight for ETH [") == 1
    rows = _repo(deployment[0]).get_rules()
    assert {r["params"]["product_id"] for r in rows} == {"BTC-USD", "ETH-USD"}
    # The valid re-entered weight (1, not the rejected -1) is what was actually used: BTC/ETH
    # renormalise to 50/50, same per-buy figure `test_edit_renormalises_and_reshows_before_approval`
    # pins for the identical 1/1/0 split.
    assert {Decimal(r["params"]["budget_usd"]) for r in rows} == {Decimal("51.74")}


def test_a_blocked_plan_at_a_tty_does_not_offer_approve(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    result = _run(deployment, "--budget", "5000", "--buffer-pct", "0", input="Y\nN\n")
    assert "[Y] Approve" not in result.output
    assert _repo(deployment[0]).get_rules() == []
    assert result.exit_code == 0, result.output
    assert result.output.count("[E] Edit weights / [N] Cancel") == 1


def test_bad_inputs_are_click_usage_errors(deployment) -> None:
    result = _run(deployment, "--budget", "500", "--buffer-pct", "10")
    assert result.exit_code == 2
    assert "0.1 means" in result.output


def test_case_colliding_target_weights_are_a_clean_error_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#848: a config with `btc:` and `BTC:` both set used to reach `build_dca_plan` uncaught
    (`DcaPlanError` was never a `click.ClickException`), crashing with a raw traceback instead of
    the clean, verbatim-message error `DcaPlanError`'s own docstring promises."""
    from tests.conftest import VALID_CONFIG_YAML

    db = tmp_path / "t.db"
    attest_subscription(_repo(db), now_ts=int(time.time()), free_volume_usd=_ROOMY_CAP)
    monkeypatch.setattr(dca_cli, "screen_product", _screen())
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML.replace("BTC: 0.40", "btc: 0.40\n  BTC: 0.05"))

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db),
            "--config",
            str(config_path),
            "dca",
            "plan",
            "--budget",
            "500",
            "--buffer-pct",
            "0.1",
        ],
    )
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "collide" in result.output
    assert "btc" in result.output and "BTC" in result.output


def test_an_absurd_budget_is_a_click_usage_error_not_a_crash(deployment) -> None:
    """Defect (review of #846): `--budget 1e30` used to reach `Decimal.quantize` and raise
    `decimal.InvalidOperation` -- a bare traceback out of the CLI, not `click.BadParameter`."""
    result = _run(deployment, "--budget", "1e30", "--buffer-pct", "0.1")
    assert result.exit_code == 2, result.output
    assert "InvalidOperation" not in result.output
    assert "budget" in result.output.lower()


def test_buffer_pct_is_required(deployment) -> None:
    result = _run(deployment, "--budget", "500")
    assert result.exit_code == 2
    assert "--buffer-pct" in result.output


def test_the_disclaimer_is_printed(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: False)
    assert _common.DISCLAIMER in _run(deployment, "--budget", "500", "--buffer-pct", "0.1").output


def test_editing_every_weight_to_zero_leaves_edit_or_cancel(deployment, monkeypatch) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: True)
    result = _run(deployment, "--budget", "500", "--buffer-pct", "0.1", input="E\n0\n0\n0\nN\n")
    assert result.exit_code == 0, result.output
    assert "no asset is eligible" in result.output
    assert _repo(deployment[0]).get_rules() == []
