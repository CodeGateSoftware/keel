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


# -- rules seed --products: rejected at the KEYBOARD, not at the rails ---------
#
# Feasibility study R2. Rails 18/19 stop an inadmissible product where the agent trades it; that
# is the right place for a safety rail and the wrong place for a typo. Seeding one wrote a row
# that looked seeded, that the agent then polled every cycle and vetoed forever.


def test_seed_refuses_a_futures_contract_and_names_it(tmp_path, valid_config_path):
    repo = _repo(tmp_path)
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "--config", str(valid_config_path),
              "rules", "seed", "--kinds", "dca",
              "--products", "XLM-28AUG26-CDE", "--status", "live"]
    )

    assert result.exit_code != 0
    assert "XLM-28AUG26-CDE" in result.output
    assert repo.get_rules() == [], "nothing may be seeded when the list is rejected"


def test_seed_refuses_a_derivative_shaped_id_that_settles_in_usd(tmp_path, valid_config_path):
    """The R2 residual at the keyboard: rail 18 alone would pass `BTC-PERP-USD`."""
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "--config", str(valid_config_path),
              "rules", "seed", "--kinds", "dca", "--products", "BTC-PERP-USD"]
    )
    assert result.exit_code != 0
    assert "BTC-PERP-USD" in result.output


def test_seed_refuses_an_unsettleable_but_well_formed_pair(tmp_path, valid_config_path):
    """`BTC-EUR` is a real spot pair; it fails on settlement membership, not on shape."""
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "--config", str(valid_config_path),
              "rules", "seed", "--kinds", "dca", "--products", "BTC-EUR"]
    )
    assert result.exit_code != 0
    assert "BTC-EUR" in result.output
    assert "settles in EUR" in result.output


def test_seed_refuses_a_lowercase_id_with_a_hint_rather_than_fixing_it(
    tmp_path, valid_config_path
):
    repo = _repo(tmp_path)
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "--config", str(valid_config_path),
              "rules", "seed", "--kinds", "dca", "--products", "btc-USD"]
    )

    assert result.exit_code != 0
    assert "did you mean BTC-USD" in result.output
    assert repo.get_rules() == [], "never silently uppercased into a real seeded rule"


def test_seed_with_no_products_still_seeds_the_allowlist(tmp_path, valid_config_path):
    """The default path must keep working: it now loads config unconditionally (it needs the
    settlement set to validate against), where before it loaded config only on this branch."""
    repo = _repo(tmp_path)
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "--config", str(valid_config_path),
              "rules", "seed", "--kinds", "dca"]
    )

    assert result.exit_code == 0, result.output
    seeded = {r["params"]["product_id"] for r in repo.get_rules()}
    assert seeded == {"BTC-USD", "ETH-USD", "PAXG-USD"}


def test_seed_rejects_an_unknown_status(tmp_path):
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "rules", "seed", "--status", "bogus"]
    )
    assert result.exit_code != 0


# -- the LIVE (production) template --------------------------------------------


def test_the_live_template_is_a_VALID_config(tmp_path):
    p = tmp_path / "live.yaml"
    p.write_text(_template_config_text(live=True))
    load_config(str(p))  # raises ConfigError if invalid


def test_the_live_template_is_CONFIRM_mode_never_armed(tmp_path):
    """The release asset must ask before every order. This is the tripwire against
    ever shipping a config that trades unattended straight off a download."""
    p = tmp_path / "live.yaml"
    p.write_text(_template_config_text(live=True))
    assert load_config(str(p)).auto_trade.mode == "confirm"


def test_the_dev_template_stays_paper():
    p = Path(__file__).resolve().parent.parent / "config.yaml"
    assert load_config(str(p)).auto_trade.mode == "paper"


def test_the_two_templates_have_the_same_top_level_keys():
    """Catches drift: a key added to the dev config must reach the live one too."""
    import yaml

    dev = yaml.safe_load(_template_config_text())
    live = yaml.safe_load(_template_config_text(live=True))
    assert set(dev) == set(live), "live/dev config templates have drifted apart"


def test_init_config_live_writes_the_confirm_mode_template(tmp_path):
    out = tmp_path / "config.yaml"
    result = CliRunner().invoke(cli, ["init-config", "--config", str(out), "--live"])
    assert result.exit_code == 0, result.output
    assert load_config(str(out)).auto_trade.mode == "confirm"


def test_init_config_without_live_still_writes_the_paper_template(tmp_path):
    out = tmp_path / "config.yaml"
    result = CliRunner().invoke(cli, ["init-config", "--config", str(out)])
    assert result.exit_code == 0, result.output
    assert load_config(str(out)).auto_trade.mode == "paper"
