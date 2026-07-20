"""`keel init-config` / `init` / `rules seed --status` (prod-install scaffolding)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from keel.cli import _template_config_text, cli
from keel.config import load_config
from keel.data.db import connect, migrate
from keel.data.repository import Repository


def _repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    return Repository(conn)


# -- the packaged template -----------------------------------------------------


def test_the_shipped_template_is_a_VALID_config(tmp_path):
    """A default config that does not parse would break every fresh install."""
    p = tmp_path / "c.yaml"
    p.write_text(_template_config_text())
    load_config(str(p))  # raises ConfigError if invalid


def test_the_template_stays_in_sync_with_the_repo_config():
    """The wheel ships a COPY of config.yaml; this fails if they drift apart.

    Without it, edits to the repo config.yaml silently never reach the packaged template.
    """
    repo_config = Path(__file__).resolve().parent.parent / "config.yaml"
    assert _template_config_text() == repo_config.read_text(encoding="utf-8"), (
        "keel/templates/config.yaml has drifted from the repo config.yaml -- re-copy it"
    )


# -- init-config ---------------------------------------------------------------


def test_init_config_writes_a_config(tmp_path):
    out = tmp_path / "config.yaml"
    result = CliRunner().invoke(cli, ["init-config", "--config", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    load_config(str(out))


def test_init_config_refuses_to_clobber_without_force(tmp_path):
    out = tmp_path / "config.yaml"
    out.write_text("mine")
    result = CliRunner().invoke(cli, ["init-config", "--config", str(out)])
    assert result.exit_code != 0
    assert "already exists" in result.output
    assert out.read_text() == "mine"

    forced = CliRunner().invoke(cli, ["init-config", "--config", str(out), "--force"])
    assert forced.exit_code == 0
    assert out.read_text() != "mine"


# -- init (config + seed) ------------------------------------------------------


def test_init_writes_config_and_seeds_candidates(tmp_path):
    db = tmp_path / "t.db"
    cfg = tmp_path / "config.yaml"
    result = CliRunner().invoke(cli, ["--db", str(db), "init", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert cfg.exists()
    rules = Repository(connect(str(db))).get_rules()
    assert rules, "init should have seeded rules"
    assert all(r["status"] == "candidate" for r in rules), "init must seed candidates only"


# -- rules seed --status -------------------------------------------------------


def test_seed_defaults_to_candidate(tmp_path):
    repo = _repo(tmp_path)
    CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "rules", "seed",
              "--kinds", "dca", "--products", "BTC-USD"]
    )
    rules = repo.get_rules()
    assert rules and all(r["status"] == "candidate" for r in rules)


def test_seed_status_live_bypasses_the_gate_and_warns(tmp_path):
    repo = _repo(tmp_path)
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "rules", "seed",
              "--kinds", "dca", "--products", "BTC-USD", "--status", "live"]
    )
    assert result.exit_code == 0, result.output
    assert "LIVE status" in result.output
    assert "supervised live-order test only" in result.output
    live = repo.get_rules("live")
    assert len(live) == 1
    assert live[0]["kind"] == "dca"


def test_seed_rejects_an_unknown_status(tmp_path):
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "rules", "seed", "--status", "bogus"]
    )
    assert result.exit_code != 0
