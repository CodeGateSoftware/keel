"""Rail 17's input: the withdrawal-capability attestation (KB §65.4)."""

from __future__ import annotations

from click.testing import CliRunner

from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution.executor import WITHDRAWAL_ATTESTATION_TTL_SEC, _withdrawals_enabled

NOW = 1_784_505_600


def _repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    return Repository(conn)


def test_never_attested_reads_as_UNKNOWN(tmp_path):
    assert _withdrawals_enabled(_repo(tmp_path), NOW) is None


def test_a_fresh_attestation_is_honoured(tmp_path):
    repo = _repo(tmp_path)
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", NOW)
    assert _withdrawals_enabled(repo, NOW) is True

    repo.set_state("withdrawals_enabled", False)
    assert _withdrawals_enabled(repo, NOW) is False


def test_a_STALE_attestation_reads_as_UNKNOWN_not_as_suspended(tmp_path):
    """"Nobody has checked recently" is a different claim from "the broker says suspended".

    Both block entries, but the operator sees a different message and should.
    """
    repo = _repo(tmp_path)
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", NOW)
    assert _withdrawals_enabled(repo, NOW + WITHDRAWAL_ATTESTATION_TTL_SEC + 1) is None


def test_the_attestation_is_read_LIVE_so_a_revocation_takes_effect_immediately(tmp_path):
    """Never cached -- same posture rail 14 takes with the subscription record."""
    repo = _repo(tmp_path)
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", NOW)
    assert _withdrawals_enabled(repo, NOW) is True

    repo.set_state("withdrawals_enabled", False)
    assert _withdrawals_enabled(repo, NOW) is False


def test_a_repo_failure_reads_as_UNKNOWN_rather_than_propagating(tmp_path):
    class _BrokenRepo:
        def get_state(self, *a, **k):
            raise RuntimeError("db gone")

    assert _withdrawals_enabled(_BrokenRepo(), NOW) is None


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

    assert runner.invoke(
        cli, ["--db", str(db_path), "withdrawals", "attest", "--enabled"], input="yes\n"
    ).exit_code == 0
    assert "ENABLED" in runner.invoke(cli, ["--db", str(db_path), "withdrawals", "show"]).output

    suspended = runner.invoke(cli, ["--db", str(db_path), "withdrawals", "attest", "--suspended"])
    assert "ENTRIES are now halted" in suspended.output
    shown = runner.invoke(cli, ["--db", str(db_path), "withdrawals", "show"])
    assert "SUSPENDED" in shown.output
    assert "exits are unaffected" in shown.output
