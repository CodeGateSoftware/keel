"""Tests for `keel subscription attest|set|show`.

Attestation is the only thing that establishes a live spend cap, so these tests pin what each
command writes, not merely that it exits zero. Everything runs through `CliRunner` -- no live
network, no live broker, matching `tests/test_cli.py`.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
from click.testing import CliRunner
from keel_core.subscription import SubscriptionStatus

from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from tests.conftest import VALID_CONFIG_YAML

ONE_YEAR = 31_536_000

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


def _run(db_path: Path, config_path: Path, *args: str):
    return CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), *args]
    )


def test_attest_writes_the_tiers_values(db_path: Path, valid_config_path: Path) -> None:
    result = _run(
        db_path, valid_config_path,
        "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred",
    )
    assert result.exit_code == 0, result.output

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.tier_name == "Preferred"
    assert record.free_volume_usd == Decimal("10000")
    assert record.subscription_usd_month == Decimal("29.99")
    assert record.status is SubscriptionStatus.ACTIVE


def test_attest_sets_a_one_year_due_date(db_path: Path, valid_config_path: Path) -> None:
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Basic")
    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.attest_due_ts == record.attested_at + ONE_YEAR


def test_attest_stores_unlimited_as_null_for_premium(
    db_path: Path, valid_config_path: Path
) -> None:
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Premium")
    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.free_volume_usd is None


def test_attest_clears_a_suspect_status(db_path: Path, valid_config_path: Path) -> None:
    """Only an explicit attestation clears suspect -- detection must not be self-clearing."""
    _run(db_path, valid_config_path,
         "subscription", "set", "--venue", "coinbase", "--free-volume-usd", "500")

    repo = _repo_at(db_path)
    stored = repo.get_broker_subscription("coinbase")
    assert stored is not None
    repo.upsert_broker_subscription(
        dataclasses.replace(stored, status=SubscriptionStatus.SUSPECT)
    )

    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.status is SubscriptionStatus.ACTIVE


def test_attest_rejects_an_unknown_tier_and_lists_the_valid_ones(
    db_path: Path, valid_config_path: Path
) -> None:
    result = _run(db_path, valid_config_path,
                  "subscription", "attest", "--venue", "coinbase", "--tier", "Gold")
    assert result.exit_code != 0
    assert "Basic" in result.output
    assert "Preferred" in result.output
    assert "Premium" in result.output
    assert _repo_at(db_path).get_broker_subscription("coinbase") is None


def test_attest_keeps_an_existing_pacing_choice(
    db_path: Path, valid_config_path: Path
) -> None:
    """Re-attesting must not silently reset a pacing the user set earlier."""
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Basic",
         "--pacing", "even_daily")
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.pacing == "even_daily"


def test_set_leaves_the_tier_unknown(db_path: Path, valid_config_path: Path) -> None:
    """The escape hatch must be visibly not an attestation."""
    _run(db_path, valid_config_path,
         "subscription", "set", "--venue", "coinbase", "--free-volume-usd", "750")

    record = _repo_at(db_path).get_broker_subscription("coinbase")
    assert record is not None
    assert record.tier_name == "unknown"
    assert record.free_volume_usd == Decimal("750")
    assert record.status is SubscriptionStatus.ACTIVE


def test_set_rejects_a_negative_allowance(db_path: Path, valid_config_path: Path) -> None:
    result = _run(db_path, valid_config_path,
                  "subscription", "set", "--venue", "coinbase", "--free-volume-usd", "-1")
    assert result.exit_code != 0
    assert _repo_at(db_path).get_broker_subscription("coinbase") is None


def test_set_rejects_a_non_numeric_allowance(db_path: Path, valid_config_path: Path) -> None:
    result = _run(db_path, valid_config_path,
                  "subscription", "set", "--venue", "coinbase",
                  "--free-volume-usd", "not-a-number")
    assert result.exit_code != 0
    assert _repo_at(db_path).get_broker_subscription("coinbase") is None


def test_set_rejects_an_infinite_allowance(db_path: Path, valid_config_path: Path) -> None:
    """`inf` would become an unbounded live spend cap -- unlimited is expressed elsewhere in
    this system as `free_volume_usd is None` (a Premium tier via `subscription attest`), never
    as `Infinity` via this raw-number escape hatch."""
    result = _run(db_path, valid_config_path,
                  "subscription", "set", "--venue", "coinbase",
                  "--free-volume-usd", "inf")
    assert result.exit_code != 0
    assert _repo_at(db_path).get_broker_subscription("coinbase") is None


def test_set_rejects_a_nan_allowance(db_path: Path, valid_config_path: Path) -> None:
    """`Decimal("nan")` parses without raising `InvalidOperation`, so without an explicit
    finiteness check the subsequent `< 0` comparison would itself raise an uncaught
    `InvalidOperation` -- a stack trace instead of the intended clean error message."""
    result = _run(db_path, valid_config_path,
                  "subscription", "set", "--venue", "coinbase",
                  "--free-volume-usd", "nan")
    assert result.exit_code != 0
    # Exit code and non-persistence alone do NOT discriminate this fix: before the finiteness
    # check existed, the uncaught `InvalidOperation` also exited non-zero and also wrote nothing
    # (it fired before the upsert). Only the absence of the exception and the presence of the
    # intended message tell the two apart.
    assert not isinstance(result.exception, InvalidOperation), result.exception
    assert "must be a finite number" in result.output
    assert _repo_at(db_path).get_broker_subscription("coinbase") is None


def test_show_reports_nothing_attested_on_a_fresh_database(
    db_path: Path, valid_config_path: Path
) -> None:
    result = _run(db_path, valid_config_path, "subscription", "show")
    assert result.exit_code == 0
    assert "no subscription" in result.output.lower()


def test_show_surfaces_effective_status_and_cap(
    db_path: Path, valid_config_path: Path
) -> None:
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    result = _run(db_path, valid_config_path, "subscription", "show")
    assert result.exit_code == 0, result.output
    assert "coinbase" in result.output
    assert "Preferred" in result.output
    assert "10000" in result.output
    assert "effective_status=active" in result.output


def test_show_reports_an_overdue_record_as_suspect(
    db_path: Path, valid_config_path: Path
) -> None:
    """Effective status is what a user needs and is not a stored column."""
    _run(db_path, valid_config_path,
         "subscription", "attest", "--venue", "coinbase", "--tier", "Preferred")

    repo = _repo_at(db_path)
    stored = repo.get_broker_subscription("coinbase")
    assert stored is not None
    repo.upsert_broker_subscription(dataclasses.replace(stored, attest_due_ts=1))

    result = _run(db_path, valid_config_path, "subscription", "show")
    assert "effective_status=suspect" in result.output


# -- the venue default follows the config's bound venue (#386 review) ---------------------------
#
# Rail 14 gates every BUY on the DEPLOYMENT'S venue record (the binding `_load_cfg` makes via
# `bind_venue(config.broker.name)`). A `--venue` default frozen at coinbase would make an
# alpaca operator type `--venue alpaca` on every invocation or silently write a coinbase row
# that nothing reads.


def test_attest_without_venue_defaults_to_the_configs_bound_venue(
    tmp_path: Path, write_config
) -> None:
    """On an alpaca config, `subscription attest` with no `--venue` must write the
    alpaca-keyed record -- the one rail 14 actually consults on that deployment."""
    db_path = tmp_path / "keel.db"
    config_path = write_config(VALID_CONFIG_YAML + ALPACA_BROKER_YAML)

    result = _run(db_path, config_path, "subscription", "attest", "--tier", "Basic")

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_broker_subscription("alpaca") is not None
    assert repo.get_broker_subscription("coinbase") is None, "nothing bound writes coinbase"


def test_set_without_venue_defaults_to_the_configs_bound_venue(
    tmp_path: Path, write_config
) -> None:
    db_path = tmp_path / "keel.db"
    config_path = write_config(VALID_CONFIG_YAML + ALPACA_BROKER_YAML)

    result = _run(db_path, config_path, "subscription", "set", "--free-volume-usd", "500")

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_broker_subscription("alpaca") is not None
    assert repo.get_broker_subscription("coinbase") is None


def test_attest_without_venue_defaults_to_coinbase_when_nothing_is_bound(
    db_path: Path, valid_config_path: Path
) -> None:
    """The compatibility pin: a config without a `broker:` section binds coinbase, so the
    historical default is what an omitted `--venue` still means there."""
    result = _run(db_path, valid_config_path, "subscription", "attest", "--tier", "Basic")

    assert result.exit_code == 0, result.output
    repo = _repo_at(db_path)
    assert repo.get_broker_subscription("coinbase") is not None
    assert repo.get_broker_subscription("alpaca") is None


def test_show_names_the_bound_venue_in_its_empty_advice(tmp_path: Path, write_config) -> None:
    """The fresh-database advice must point at the venue THIS deployment trades, or the
    operator's copy-paste attests a record nothing reads."""
    db_path = tmp_path / "keel.db"
    config_path = write_config(VALID_CONFIG_YAML + ALPACA_BROKER_YAML)

    result = _run(db_path, config_path, "subscription", "show")

    assert result.exit_code == 0, result.output
    assert "--venue alpaca" in result.output
    assert "--venue coinbase" not in result.output
