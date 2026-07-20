"""`keel vault` -- init / status / rekey. Never prints a secret."""

from __future__ import annotations

from click.testing import CliRunner

from keel.cli import cli
from keel.security.secrets import load_vault


def _env(tmp_path, key="cdpkey", secret="cdpsecret"):
    p = tmp_path / ".env"
    p.write_text(f"CDP_API_KEY={key}\nCDP_API_SECRET={secret}\n")
    return str(p)


def test_init_imports_env_and_the_vault_unlocks(tmp_path):
    vault = str(tmp_path / "secrets.enc")
    result = CliRunner().invoke(
        cli,
        [
            "vault", "init", "--vault", vault,
            "--from-env", _env(tmp_path), "--passphrase", "pw1234567",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "imported" in result.output
    loaded = load_vault("pw1234567", path=vault)
    assert loaded["api_key"] == "cdpkey"


def test_init_never_prints_the_secret(tmp_path):
    vault = str(tmp_path / "secrets.enc")
    result = CliRunner().invoke(
        cli,
        [
            "vault", "init", "--vault", vault,
            "--from-env", _env(tmp_path, secret="TOPSECRET"), "--passphrase", "pw1234567",
        ],
    )
    assert "TOPSECRET" not in result.output


def test_init_refuses_to_clobber_without_force(tmp_path):
    vault = str(tmp_path / "secrets.enc")
    args = [
        "vault", "init", "--vault", vault,
        "--from-env", _env(tmp_path), "--passphrase", "pw1234567",
    ]
    assert CliRunner().invoke(cli, args).exit_code == 0
    second = CliRunner().invoke(cli, args)
    assert second.exit_code != 0
    assert "already exists" in second.output
    assert CliRunner().invoke(cli, [*args, "--force"]).exit_code == 0


def test_status_on_no_vault_points_at_env(tmp_path):
    result = CliRunner().invoke(cli, ["vault", "status", "--vault", str(tmp_path / "none.enc")])
    assert result.exit_code == 0
    assert "no vault" in result.output


def test_status_reports_unlock_and_present_fields(tmp_path):
    vault = str(tmp_path / "secrets.enc")
    CliRunner().invoke(
        cli,
        [
            "vault", "init", "--vault", vault,
            "--from-env", _env(tmp_path), "--passphrase", "pw1234567",
        ],
    )
    result = CliRunner().invoke(
        cli, ["vault", "status", "--vault", vault, "--passphrase", "pw1234567"]
    )
    assert "unlocks OK" in result.output
    assert "api_key" in result.output
    assert "cdpkey" not in result.output  # the value is never shown


def test_status_reports_a_wrong_passphrase_without_crashing(tmp_path):
    vault = str(tmp_path / "secrets.enc")
    CliRunner().invoke(
        cli,
        [
            "vault", "init", "--vault", vault,
            "--from-env", _env(tmp_path), "--passphrase", "pw1234567",
        ],
    )
    result = CliRunner().invoke(cli, ["vault", "status", "--vault", vault, "--passphrase", "WRONG"])
    assert result.exit_code != 0
    assert "unlock failed" in result.output


def test_rekey_changes_the_passphrase_and_preserves_secrets(tmp_path):
    vault = str(tmp_path / "secrets.enc")
    CliRunner().invoke(
        cli,
        [
            "vault", "init", "--vault", vault,
            "--from-env", _env(tmp_path), "--passphrase", "oldpw12345",
        ],
    )
    result = CliRunner().invoke(
        cli,
        ["vault", "rekey", "--vault", vault,
         "--old-passphrase", "oldpw12345", "--new-passphrase", "newpw12345"],
    )
    assert result.exit_code == 0, result.output
    assert load_vault("newpw12345", path=vault)["api_key"] == "cdpkey"

    import pytest

    from keel.security.secrets import VaultError

    with pytest.raises(VaultError):
        load_vault("oldpw12345", path=vault)
