"""Tests for `keel scope attest|show` -- rail 20's input (#233 PR1).

`scope attest --trading` RELEASES a rail-20 veto on live ENTRIES, so it must demand the same
typed-`yes`-at-a-terminal gate every other capability-increasing command uses, and must fail
closed off a TTY. `scope attest --read-only` only ever REDUCES capability and must stay ungated
so it works from cron. These tests pin that asymmetry directly, the way
`tests/commands/test_update.py` pins the update gate and `tests/test_cli.py` pins the four
halt-releasing commands -- by monkeypatching `keel.commands._common._is_interactive`, since that
predicate deliberately has no env-var seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from keel_core.trade_scope import READ_ONLY, TRADING, TradeScopeState, VenueTradeScope

from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from tests.conftest import VALID_CONFIG_YAML

ALPACA_BROKER_YAML = """
broker:
  name: alpaca
  endpoint: paper
  data_feed: iex
"""


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "keel.db"


def _run(db_path: Path, config_path: Path, *args: str, input: str | None = None):
    return CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), *args], input=input
    )


def _at_a_terminal(monkeypatch: pytest.MonkeyPatch, yes: bool = True) -> None:
    # Same patch point every other TTY-gated command test uses -- the predicate lives in
    # keel.commands._common and is called from there regardless of which module the gated
    # command itself lives in.
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: yes)


# -- the TTY asymmetry (the point of the command) ------------------------------------------------


def test_trading_requires_a_terminal_and_fails_closed_off_one(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SAFETY-CRITICAL: --trading must refuse off a TTY, with nothing written."""
    _at_a_terminal(monkeypatch, yes=False)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )
    assert result.exit_code != 0, result.output
    assert "terminal" in result.output.lower()
    assert _repo_at(db_path).get_venue_trade_scope("coinbase") is None


def test_trading_proceeds_on_a_typed_yes_at_a_terminal(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=True)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )
    assert result.exit_code == 0, result.output
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.state is TradeScopeState.ATTESTED
    assert record.attested_scope == TRADING


def test_trading_aborts_on_anything_other_than_a_typed_yes(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=True)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="y\n",
    )
    assert result.exit_code != 0, result.output
    assert "aborted" in result.output.lower()
    assert _repo_at(db_path).get_venue_trade_scope("coinbase") is None


def test_read_only_works_off_a_tty_with_no_gate(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--read-only only ever REDUCES capability, so it must stay usable from cron (no TTY)."""
    _at_a_terminal(monkeypatch, yes=False)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--read-only", "--venue", "coinbase",
    )
    assert result.exit_code == 0, result.output
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.state is TradeScopeState.ATTESTED
    assert record.attested_scope == READ_ONLY


def test_read_only_does_not_permit_a_live_entry(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=False)
    _run(db_path, valid_config_path, "scope", "attest", "--read-only", "--venue", "coinbase")
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.may_place_live_entry(None) is False


def test_trading_permits_a_live_entry(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=True)
    _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.may_place_live_entry(None) is True


def test_exactly_one_of_trading_or_read_only_is_required(
    db_path: Path, valid_config_path: Path
) -> None:
    result = _run(db_path, valid_config_path, "scope", "attest", "--venue", "coinbase")
    assert result.exit_code != 0


# -- re-attestation over a refuted record keeps the refusal history ------------------------------


def test_reattesting_trading_over_a_refuted_record_keeps_refuted_ts_and_reason(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design requirement: re-attesting (credential rotation) must NOT erase the fact that a
    prior credential on this venue was once refused by the venue itself."""
    repo = _repo_at(db_path)
    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue="coinbase",
            state=TradeScopeState.REFUTED,
            attested_scope=TRADING,
            attested_ts=1_700_000_000,
            confirmed_ts=1_650_000_000,
            refuted_ts=1_750_000_000,
            refuted_reason="403 You do not have permission to perform this action",
            credential_fingerprint=None,
        )
    )

    _at_a_terminal(monkeypatch, yes=True)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )
    assert result.exit_code == 0, result.output

    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.state is TradeScopeState.ATTESTED
    assert record.attested_scope == TRADING
    # history survives, unchanged
    assert record.refuted_ts == 1_750_000_000
    assert record.refuted_reason == "403 You do not have permission to perform this action"
    assert record.confirmed_ts == 1_650_000_000
    # but a live entry is permitted again -- the whole point of re-attesting
    assert record.may_place_live_entry(None) is True


def test_reattesting_read_only_over_a_refuted_record_also_keeps_the_history(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_at(db_path)
    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue="coinbase",
            state=TradeScopeState.REFUTED,
            attested_scope=TRADING,
            attested_ts=1_700_000_000,
            confirmed_ts=None,
            refuted_ts=1_750_000_000,
            refuted_reason="some refusal",
            credential_fingerprint=None,
        )
    )
    _at_a_terminal(monkeypatch, yes=False)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--read-only", "--venue", "coinbase",
    )
    assert result.exit_code == 0, result.output
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.refuted_ts == 1_750_000_000
    assert record.refuted_reason == "some refusal"


# -- #633: attest stamps the CURRENT fingerprint, never carries the old one forward --------------


def test_attest_writes_the_current_credential_fingerprint(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=True)
    monkeypatch.setattr(
        "keel.commands.scope.current_credential_fingerprint", lambda venue: "f" * 32
    )
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )
    assert result.exit_code == 0, result.output
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.credential_fingerprint == "f" * 32


def test_attest_does_not_carry_an_old_fingerprint_forward(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator is attesting about the credential IN PLACE NOW -- a re-attestation must
    overwrite a stale fingerprint from an earlier write, unlike `refuted_ts`/`confirmed_ts`,
    which are carried forward as history."""
    repo = _repo_at(db_path)
    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue="coinbase",
            state=TradeScopeState.CONFIRMED,
            attested_scope=None,
            attested_ts=None,
            confirmed_ts=1_700_000_000,
            refuted_ts=None,
            refuted_reason=None,
            credential_fingerprint="stale" + "0" * 27,
        )
    )
    _at_a_terminal(monkeypatch, yes=True)
    monkeypatch.setattr(
        "keel.commands.scope.current_credential_fingerprint", lambda venue: "fresh" + "0" * 27
    )
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )
    assert result.exit_code == 0, result.output
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    assert record.credential_fingerprint == "fresh" + "0" * 27


def test_attest_writes_none_when_no_current_credential_resolves(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=False)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--read-only", "--venue", "coinbase",
    )
    assert result.exit_code == 0, result.output
    record = _repo_at(db_path).get_venue_trade_scope("coinbase")
    assert record is not None
    # No credentials are configured in this test's environment, so the real
    # `current_credential_fingerprint` resolves to None -- written as-is, not defaulted away.
    assert record.credential_fingerprint is None


# -- the venue key ---------------------------------------------------------------------------


def test_attest_writes_the_explicit_venue_not_a_hardcoded_default(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=True)
    result = _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "alpaca",
        input="yes\n",
    )
    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_venue_trade_scope("alpaca") is not None
    assert repo.get_venue_trade_scope("coinbase") is None


def test_attest_without_venue_defaults_to_the_configs_bound_venue(
    tmp_path: Path, write_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=False)
    db_path = tmp_path / "keel.db"
    config_path = write_config(VALID_CONFIG_YAML + ALPACA_BROKER_YAML)

    result = _run(db_path, config_path, "scope", "attest", "--read-only")

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_venue_trade_scope("alpaca") is not None
    assert repo.get_venue_trade_scope("coinbase") is None


def test_attest_without_venue_defaults_to_coinbase_when_nothing_is_bound(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=False)
    result = _run(db_path, valid_config_path, "scope", "attest", "--read-only")
    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_venue_trade_scope("coinbase") is not None
    assert repo.get_venue_trade_scope("alpaca") is None


# -- show ------------------------------------------------------------------------------------


def test_show_reports_no_records_and_names_the_bound_venue(
    tmp_path: Path, write_config
) -> None:
    db_path = tmp_path / "keel.db"
    config_path = write_config(VALID_CONFIG_YAML + ALPACA_BROKER_YAML)

    result = _run(db_path, config_path, "scope", "show")

    assert result.exit_code == 0, result.output
    assert "--venue alpaca" in result.output
    assert "--venue coinbase" not in result.output
    assert "fails closed" in result.output.lower() or "deliberately" in result.output.lower()


def test_show_reports_trading_and_permitted(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_a_terminal(monkeypatch, yes=True)
    _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )
    result = _run(db_path, valid_config_path, "scope", "show")
    assert result.exit_code == 0, result.output
    assert "coinbase" in result.output
    assert "True" in result.output


def test_show_surfaces_a_past_refusal_even_after_reattestation(
    db_path: Path, valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_at(db_path)
    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue="coinbase",
            state=TradeScopeState.REFUTED,
            attested_scope=TRADING,
            attested_ts=1_700_000_000,
            confirmed_ts=None,
            refuted_ts=1_750_000_000,
            refuted_reason="403 denied",
            credential_fingerprint=None,
        )
    )
    _at_a_terminal(monkeypatch, yes=True)
    _run(
        db_path, valid_config_path, "scope", "attest", "--trading", "--venue", "coinbase",
        input="yes\n",
    )

    result = _run(db_path, valid_config_path, "scope", "show")
    assert result.exit_code == 0, result.output
    assert "refut" in result.output.lower()
    # Rendered as a UTC date, not a raw epoch: the design's wording is "refuted a
    # credential on <date>", and an operator judging whether a refusal is old news or
    # this morning cannot read 1750000000.
    assert "2025-06-15" in result.output
    assert "1750000000" not in result.output
