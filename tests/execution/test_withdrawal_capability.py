"""Rail 17's input: the venue's per-wallet `allow_withdrawals` flag (KB §65.4)."""

# `repo` is the executor tests' fixture, imported by name and redefined as a parameter.
# ruff: noqa: F811, F401

from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from keel.cli import cli
from keel.execution import guards
from keel.execution.executor import _build_intent, execute
from tests.execution.test_executor import (
    NOW_TS,
    FakeBroker,
    _config,
    _enter_signal,
    _exit_signal,
    _seed_open_position,
    _setup,
    repo,
)


def _buy(repo, broker: Any, signal=None):
    return execute(
        signal or _enter_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS
    )


def _assert_vetoed(result, broker, detail: str) -> None:
    assert result.placed is False
    assert result.vetoed_by[0].startswith("withdrawal_capability:")
    assert detail in result.vetoed_by[0]
    assert broker.place_calls == []


def test_manual_suspension_vetoes_without_reading_the_venue(repo):
    repo.set_state("withdrawals_enabled", False)
    broker = FakeBroker()
    result = _buy(repo, broker)
    _assert_vetoed(result, broker, "manual suspension (keel withdrawals attest --suspended)")
    assert "SUSPENDED" in result.vetoed_by[0]
    assert broker.withdrawals_calls == 0


def test_unreadable_suspension_state_fails_closed_without_asking_the_venue(repo):
    """Not knowing whether a suspension is stored is not evidence that none is."""

    class _BrokenRead:
        def __getattr__(self, name):
            return getattr(repo, name)

        def get_state(self, key, *a, **k):
            if key == "withdrawals_enabled":
                raise RuntimeError("db gone")
            return repo.get_state(key, *a, **k)

    broker = FakeBroker()
    result = _buy(_BrokenRead(), broker)
    _assert_vetoed(result, broker, "suspension state unreadable")
    assert "UNKNOWN" in result.vetoed_by[0]
    assert broker.withdrawals_calls == 0


def test_adapter_without_the_read_is_UNKNOWN(repo):
    class _NoReader(FakeBroker):
        withdrawals_allowed = None  # type: ignore[assignment]

    broker = _NoReader()
    result = _buy(repo, broker)
    _assert_vetoed(result, broker, "venue adapter cannot report withdrawals")
    assert "UNKNOWN" in result.vetoed_by[0]


def test_unreadable_venue_is_UNKNOWN_and_names_the_exception(repo):
    broker = FakeBroker(withdrawals_raises=RuntimeError("boom"))
    result = _buy(repo, broker)
    _assert_vetoed(result, broker, "venue unreadable: RuntimeError")
    assert "UNKNOWN" in result.vetoed_by[0]


def test_missing_quote_wallet_is_UNKNOWN(repo):
    broker = FakeBroker(withdrawals={"BTC": True})
    result = _buy(repo, broker)
    _assert_vetoed(result, broker, "no USD wallet reported")
    assert "UNKNOWN" in result.vetoed_by[0]


def test_quote_wallet_disabled_is_SUSPENDED(repo):
    broker = FakeBroker(withdrawals={"USD": False, "BTC": True})
    result = _buy(repo, broker)
    _assert_vetoed(result, broker, "USD withdrawals disabled at venue")
    assert "SUSPENDED" in result.vetoed_by[0]


def test_base_wallet_disabled_is_SUSPENDED(repo):
    broker = FakeBroker(withdrawals={"USD": True, "BTC": False})
    result = _buy(repo, broker)
    _assert_vetoed(result, broker, "BTC withdrawals disabled at venue")


def test_all_wallets_allowed_buys(repo):
    broker = FakeBroker(withdrawals={"USD": True, "BTC": True})
    assert _buy(repo, broker).placed is True
    assert broker.withdrawals_calls == 1


def test_stale_attestation_no_longer_vetoes(repo):
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", NOW_TS - 30 * 86400)
    assert _buy(repo, FakeBroker()).placed is True


def test_never_attested_account_buys_when_venue_allows(repo):
    for key in ("withdrawals_enabled", "withdrawals_attested_at"):
        repo.set_state(key, None)
    assert _buy(repo, FakeBroker()).placed is True


def test_base_wallet_missing_decides_on_quote(repo):
    broker = FakeBroker(withdrawals={"USD": True})  # no FET wallet
    signal = _enter_signal(_setup(product_id="FET-USD"))
    result = _buy(repo, broker, signal)
    assert not any(v.startswith("withdrawal_capability") for v in result.vetoed_by)


def test_sell_never_reads_withdrawals(repo):
    _seed_open_position(repo, "BTC-USD", __import__("decimal").Decimal("0.1"), 50000)
    broker = FakeBroker(withdrawals_raises=RuntimeError())
    result = execute(_exit_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)
    assert result.placed is True
    assert broker.withdrawals_calls == 0


def test_paper_never_reads_withdrawals(repo):
    """Paper builds its intent with NO broker; rail 17 is recorded as skipped, not vetoed."""
    intent = _build_intent(_enter_signal(), None, repo, _config(), NOW_TS)
    assert intent is not None
    verdict = guards.check(intent, repo, _config(), NOW_TS, offline=True)
    assert not any(v.startswith("withdrawal_capability") for v in verdict.violations)
    assert "withdrawal_capability" in verdict.skipped_rails


# -- CLI -----------------------------------------------------------------------


def test_cli_roundtrip_and_suspension_message(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    runner = CliRunner()
    # `--enabled` RELEASES rail 17's entry halt, so it now demands a typed `yes` at a terminal
    # (`--suspended` stays ungated -- it only ever reduces capability).

    # The TTY predicate lives in keel.commands._common; _require_interactive_confirmation
    # calls it there, so patch it at its definition (see that module's docstring).
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)

    unknown = runner.invoke(cli, ["--db", str(db_path), "withdrawals", "show"])
    assert "UNKNOWN (never attested)" in unknown.output

    assert (
        runner.invoke(
            cli, ["--db", str(db_path), "withdrawals", "attest", "--enabled"], input="yes\n"
        ).exit_code
        == 0
    )
    assert "ENABLED" in runner.invoke(cli, ["--db", str(db_path), "withdrawals", "show"]).output

    suspended = runner.invoke(cli, ["--db", str(db_path), "withdrawals", "attest", "--suspended"])
    assert "ENTRIES are now halted" in suspended.output
    shown = runner.invoke(cli, ["--db", str(db_path), "withdrawals", "show"])
    assert "SUSPENDED" in shown.output
    assert "exits are unaffected" in shown.output
