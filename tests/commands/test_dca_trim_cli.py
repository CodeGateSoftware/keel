"""`keel dca trim --preview [--view {gain,lots,bands}]` -- the read-only trim report (#857, plan
P13 Task 13.3 and P14 Task 14.2; spec §4, §5 and §8.1).

Read-only ALWAYS, like `keel dca distribute --preview`: it opens the database through
`_common._open_repo_ro` whatever the terminal, writes nothing, and builds no broker -- every fee
is the fallback rate, labelled as such (R25). `--view gain` (P14) is the default, so bare
`--preview` prints it (R24); a missing `--preview` is a usage error.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import _common, sleeve_report
from keel.commands import dca as dca_cli
from keel.config import load_config
from keel.data import db as keel_db
from keel.types import Granularity
from tests.commands.test_dca_cli import _repo, deployment  # noqa: F401 - the fixture
from tests.commands.test_sleeve_report import _candle

_PREVIEW_REQUIRED = "Error: pass --preview: it is the only mode in this build."


def _trim(deployment_pair, *args: str):
    db, config = deployment_pair
    return CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", "trim", *args])


def _seed(db: Path) -> None:
    """PAXG's mixed ledger (turtle tranche first, Review Focus 3) and a BTC dca tranche, each
    with a cached daily close -- the fixture config targets BTC .4, ETH .3, PAXG .3."""
    repo = _repo(db)
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="turtle_breakout",
        opened_at=86_400,
        qty=Decimal("0.0132"),
        entry_fill=Decimal("4673.23"),
        entry_fee=Decimal("0.73"),
    )
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="dca",
        opened_at=2 * 86_400,
        qty=Decimal("0.01"),
        entry_fill=Decimal("4400"),
        entry_fee=Decimal("0.40"),
    )
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=3 * 86_400,
        qty=Decimal("0.0015"),
        entry_fill=Decimal("100000"),
        entry_fee=Decimal("1.80"),
    )
    repo.upsert_candles("PAXG-USD", Granularity.ONE_DAY, [_candle(10, "4300")])
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(10, "110000")])


def _no_broker(*_a, **_k):
    raise AssertionError("trim --preview built a broker (R25: the CLI previews build none)")


def _expected(db: Path, config_path: Path, view: str) -> list[str]:
    repo, config = _repo(db), load_config(config_path)
    if view == "lots":
        return sleeve_report.render_lots(sleeve_report.lots_view(repo, config))
    if view == "gain":
        return sleeve_report.render_gain(sleeve_report.gain_view(repo, config))
    return sleeve_report.render_bands(sleeve_report.bands_view(repo, config))


@pytest.mark.parametrize("view", ["gain", "lots", "bands"])
@pytest.mark.parametrize("interactive", [False, True])
def test_each_view_prints_its_report_and_writes_nothing(
    deployment,  # noqa: F811
    monkeypatch,
    interactive,
    view,
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    monkeypatch.setattr(_common, "_build_broker", _no_broker)
    db, config_path = deployment
    _seed(db)
    expected = _expected(db, config_path, view)
    # `PRAGMA data_version` on ONE connection held across the run changes iff ANOTHER connection
    # committed. A second, independent count: every table's rows.
    watcher = sqlite3.connect(str(db))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]
    tables = [
        name for (name,) in watcher.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    counts = {t: watcher.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}

    result = _trim(deployment, "--preview", "--view", view)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    start = lines.index(expected[0])
    assert lines[start : start + len(expected)] == expected
    assert lines[start + len(expected)] == dca_cli.PREVIEW_FOOTER
    assert lines.count(dca_cli.PREVIEW_FOOTER) == 1
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
    assert {
        t: watcher.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables
    } == counts
    watcher.close()
    print(result.output)  # for the PR body: the exact CLI output


def test_the_lots_view_lists_the_turtle_tranche_first_and_says_not_tax_advice(
    deployment,  # noqa: F811
) -> None:
    db, _config_path = deployment
    _seed(db)
    result = _trim(deployment, "--preview", "--view", "lots")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    lots = [line.split()[:2] for line in lines if line.startswith("  #")]
    assert lots == [["#3", "dca"], ["#1", "turtle_breakout"], ["#2", "dca"]]
    assert lines.count(sleeve_report.NOT_TAX_ADVICE) == 1


def test_the_bands_view_says_band_trimming_was_not_adopted(deployment) -> None:  # noqa: F811
    db, _config_path = deployment
    _seed(db)
    result = _trim(deployment, "--preview", "--view", "bands")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines.count(sleeve_report.BANDS_NOT_ADOPTED) == 1
    assert lines.count(sleeve_report.BANDS_NOT_A_RECOMMENDATION) == 1
    assert [line.split()[0] for line in lines if line.startswith("  ")] == ["BTC", "ETH", "PAXG"]


def test_the_footer_says_nothing_is_placed() -> None:
    assert dca_cli.PREVIEW_FOOTER == "preview only: nothing is placed."


@pytest.mark.parametrize("args", [(), ("--view", "lots"), ("--view", "bands"), ("--view", "gain")])
def test_trim_without_preview_is_a_usage_error(deployment, args) -> None:  # noqa: F811
    """The missing mode is named first, whatever `--view` says: `--preview` is eager."""
    result = _trim(deployment, *args)
    assert result.exit_code == 2
    assert _PREVIEW_REQUIRED in result.output.splitlines()


def test_bare_preview_prints_the_gain_view(deployment, monkeypatch) -> None:  # noqa: F811
    """R24/spec §4: `gain` is `--view`'s default, so bare `--preview` prints the gain report --
    exactly the `--view gain` lines -- and exits 0."""
    monkeypatch.setattr(_common, "_build_broker", _no_broker)
    db, config_path = deployment
    _seed(db)
    bare = _trim(deployment, "--preview")
    explicit = _trim(deployment, "--preview", "--view", "gain")
    assert bare.exit_code == 0, bare.output
    expected = _expected(db, config_path, "gain")
    lines = bare.output.splitlines()
    start = lines.index(expected[0])
    assert lines[start : start + len(expected)] == expected
    assert bare.output == explicit.output


def test_the_gain_view_lists_each_held_product_with_its_verdict(deployment) -> None:  # noqa: F811
    db, _config_path = deployment
    _seed(db)
    result = _trim(deployment, "--preview")
    assert result.exit_code == 0, result.output
    rows = [line.split()[0] for line in result.output.splitlines() if line.startswith("  ")]
    assert rows == ["BTC-USD", "PAXG-USD"]
    assert result.output.splitlines().count(sleeve_report.GAIN_NOT_EVIDENCE) == 1


def test_the_gain_flags_reach_the_view(deployment) -> None:  # noqa: F811
    """`--gain-pct` and `--trim-pct` override the spec defaults, and `--product` narrows: the
    printed line is `gain_view`'s at those arguments, not the defaults'."""
    db, config_path = deployment
    _seed(db)
    result = _trim(
        deployment,
        "--preview",
        "--gain-pct",
        "500",
        "--trim-pct",
        "20",
        "--product",
        "BTC-USD",
    )
    assert result.exit_code == 0, result.output
    repo, config = _repo(db), load_config(config_path)
    [row] = sleeve_report.gain_view(
        repo, config, gain_pct=Decimal("500"), trim_pct=Decimal("20"), product_id="BTC-USD"
    )
    assert (row.gain_pct_used, row.trim_pct_used, row.verdict) == (
        Decimal("500"),
        Decimal("20"),
        "below trigger",
    )
    [line] = [text for text in result.output.splitlines() if text.startswith("  ")]
    assert line == [text for text in sleeve_report.render_gain([row]) if text.startswith("  ")][0]


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (("--trim-pct", "25"), "--trim-pct"),
        (("--trim-pct", "9"), "--trim-pct"),
        (("--gain-pct", "0"), "--gain-pct"),
        (("--gain-pct", "abc"), "--gain-pct"),
        # Review round 1 (#936): not a traceback -- `Decimal("NaN") <= 0` raises rather than
        # answering, and Infinity would pass the rule's range check.
        (("--gain-pct", "NaN"), "--gain-pct"),
        (("--trim-pct", "Infinity"), "--trim-pct"),
        (("--gain-pct", "-Infinity"), "--gain-pct"),
    ],
)
def test_a_flag_the_rule_would_refuse_is_a_usage_error(deployment, args, named) -> None:  # noqa: F811
    result = _trim(deployment, "--preview", *args)
    assert result.exit_code == 2
    assert any(
        line.startswith(f"Error: Invalid value for '{named}'")
        for line in result.output.splitlines()
    )


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (("--view", "lots", "--gain-pct", "30"), "--gain-pct"),
        (("--view", "bands", "--trim-pct", "12"), "--trim-pct"),
        (("--view", "bands", "--product", "BTC-USD"), "--product"),
    ],
)
def test_a_flag_that_does_not_apply_to_the_view_is_a_usage_error(
    deployment,  # noqa: F811
    args,
    named,
) -> None:
    """A flag silently ignored would read as applied: `--gain-pct`/`--trim-pct` belong to the gain
    view, and the bands view weighs the whole sleeve, so it takes no `--product`."""
    result = _trim(deployment, "--preview", *args)
    assert result.exit_code == 2
    assert any(line.startswith("Error: ") and named in line for line in result.output.splitlines())


def test_the_product_flag_narrows_the_lots_view(deployment) -> None:  # noqa: F811
    db, _config_path = deployment
    _seed(db)
    result = _trim(deployment, "--preview", "--view", "lots", "--product", "PAXG-USD")
    assert result.exit_code == 0, result.output
    lots = [line.split()[0] for line in result.output.splitlines() if line.startswith("  #")]
    assert lots == ["#1", "#2"]


@pytest.mark.parametrize("interactive", [False, True])
def test_a_missing_database_is_refused_not_created(
    tmp_path: Path, valid_config_path, monkeypatch, interactive
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    db = tmp_path / "absent.db"
    result = _trim((db, valid_config_path), "--preview", "--view", "lots")
    assert result.exit_code == 1
    assert "read-only command" in result.output
    assert not db.exists()


def test_a_stale_schema_is_refused_not_migrated(tmp_path: Path, valid_config_path) -> None:
    db = tmp_path / "v21.db"
    conn = keel_db.connect(db)
    keel_db.migrate(conn)
    conn.execute("DROP TABLE sell_proposals")
    conn.execute("UPDATE schema_version SET version = 21")
    conn.commit()
    conn.close()

    result = _trim((db, valid_config_path), "--preview", "--view", "bands")

    assert result.exit_code == 1
    assert "schema version 21" in result.output


def test_colliding_target_weights_are_a_clean_error_not_a_traceback(
    tmp_path: Path,
    write_config,
    deployment,  # noqa: F811
) -> None:
    """`load_config` passes `{btc: .5, BTC: .5}` (#848); the bands view refuses it by name, as
    `keel dca plan` does, rather than silently keeping one of the two."""
    db, _config_path = deployment
    config_path = write_config(
        Path(deployment[1]).read_text().replace("  BTC: 0.40\n", "  BTC: 0.40\n  btc: 0.10\n")
    )
    result = _trim((db, config_path), "--preview", "--view", "bands")
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert any(
        line.startswith("Error: target_weights has keys that collide")
        for line in result.output.splitlines()
    )


def test_a_profit_take_row_that_does_not_build_is_named_not_a_traceback(
    deployment,  # noqa: F811
) -> None:
    """P14's held item: a `profit_take` row with bad stored params crashed the whole gain view.
    It is skipped and named on one line; every held product is still reported."""
    db, _config_path = deployment
    _seed(db)
    bad = _repo(db).insert_rule(
        "profit_take", {"product_id": "BTC-USD", "trim_pct": "90"}, status="paper"
    )

    result = _trim(deployment, "--preview")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    skipped = [line for line in lines if line.startswith("SKIPPED rule ")]
    assert len(skipped) == 1 and skipped[0].startswith(f"SKIPPED rule {bad} (profit_take, paper)")
    assert sorted(line.split()[0] for line in lines if line.startswith("  ")) == [
        "BTC-USD",
        "PAXG-USD",
    ]
