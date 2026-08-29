"""Behavioural tests for the six Wave B subcommands of `keel research` (issue #601):
`significance`, `pooled-review`, `throughput`, `tuning`, `factors`, `independence`.

`tests/commands/test_research_front_door.py` is a PINS file -- architectural invariants
(completeness, the Strathern-rail AST scan, the refusal pin) that hold whether or not any
subcommand ever succeeds. It deliberately never exercises a subcommand's SUCCESS path: its
own refusal fixture is built to make every one of them refuse. That left a real gap -- a
regression that made `factors` print an empty report, or made `significance` silently drop
one of its two fee regimes, would not fail a single test in this suite. This file closes
that gap: every test here seeds a fixture that makes a subcommand actually COMPUTE
something, invokes it through the real CLI (the same `CliRunner` + fixture-db machinery
`test_research_front_door.py` and `tests/research/test_trials_cli.py` already use), and
asserts on what it PRINTED -- not merely that it exited 0.

The last test in this file, `test_no_evidence_subcommand_names_a_winner`, is the one that
matters most: the Strathern rail (`cscv.py`/`deflate.py`/`walkforward.py` -- a score may
report, and may gate, but may NEVER be a sweep's ranking key) is pinned at the SOURCE level
by an AST scan in the front-door pins file, but nothing before this checked the RENDERED
output of a rail-bearing command actually run through the CLI. Mutation-verified: see the
commit message for the exact renderer edit, the failure it produced, and the revert.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.data.db import SCHEMA_VERSION, connect, migrate
from keel.data.repository import Repository
from keel.research import ledger as trials_ledger
from keel.research import tuning as tuning_mod
from keel.research import walkforward as wf_mod
from keel.types import Candle, Granularity

MISSING_CONFIG_NAME = "missing-config.yaml"  # never created: config degrades to the default


def _missing_config(tmp_path: Path) -> Path:
    return tmp_path / MISSING_CONFIG_NAME


def _invoke(runner: CliRunner, db: Path, tmp_path: Path, *args: str):
    """The house pattern (`test_research_front_door.py`'s refusal test, `test_trials_cli.py`'s
    `_invoke_mc`): a real `--db`, and a `--config` pointing at a path that does not exist so
    the fee degrades to the library default instead of loading whatever deployment config
    happens to surround the test run."""
    return runner.invoke(
        cli, ["--db", str(db), "--config", str(_missing_config(tmp_path)), *args]
    )


# -- shared candle fixtures -------------------------------------------------------------------


#: Jaccard for the 40-bar shared window in
#: `test_independence_does_not_stretch_a_trade_past_the_shared_history`. A measured
#: constant, not a derivation: it is pinned so a change in how trades are projected onto
#: the common index shows up as a failing number rather than as a quietly different report.
_EXPECTED_DEPTH_JACCARD = 0.3333333333333333


def _sawtooth_candles(n: int, *, start: int = 1_700_000_000) -> list[Candle]:
    """`n` daily bars in an asymmetric 19-bar sawtooth -- lifted from
    `tests/research/test_trials_cli.py::_mc_candles`: an 8-bar rally, a 9-bar crash, a 2-bar
    drift, then again, so a turtle rule both enters and gets stopped out AND its closed P&L
    comes out mixed-sign (wins and losses in one run)."""
    candles = []
    price = Decimal(100)
    for i in range(n):
        phase = i % 19
        if phase < 8:
            price += Decimal(4)
        elif phase < 17:
            price -= Decimal(9)
        else:
            price -= Decimal(1)
        open_ = price
        close = price + (Decimal("1.5") if i % 2 else Decimal("-1.5"))
        candles.append(
            Candle(
                ts=start + i * 86400,
                open=open_,
                high=max(open_, close) + Decimal(1),
                low=min(open_, close) - Decimal(1),
                close=close,
                volume=Decimal("10"),
            )
        )
    return candles


_TURTLE_A_PARAMS = {
    "product_id": "BTC-USD",
    "entry_lookback": 5,
    "exit_lookback": 3,
    "atr_period": 5,
    "atr_stop_mult": "2",
}
_TURTLE_B_PARAMS = {
    "product_id": "BTC-USD",
    "entry_lookback": 8,
    "exit_lookback": 4,
    "atr_period": 6,
    "atr_stop_mult": "2",
}


def _turtle_db(tmp_path: Path, *, name: str = "turtle.db", bars: int = 96) -> Path:
    """One turtle_breakout rule (`_TURTLE_A_PARAMS`) over `_sawtooth_candles(bars)`, which
    closes a mix of wins and losses (verified: 2 wins + 2 losses at `bars=96`)."""
    path = tmp_path / name
    conn = connect(str(path))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule("turtle_breakout", _TURTLE_A_PARAMS, status="candidate", now_ts=1_800_000_000)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _sawtooth_candles(bars))
    conn.close()
    return path


def _factor_candles(count: int, seed: int = 7) -> list[Candle]:
    """A pseudo-random but deterministic OHLCV walk -- lifted from
    `tests/research/test_cts_factors.py::_candles` (the module's own house fixture for a
    sample with varying factor presence)."""
    import random

    rng = random.Random(seed)
    price = 100.0
    out: list[Candle] = []
    for index in range(count):
        price = max(1.0, price * (1 + rng.uniform(-0.03, 0.03)))
        high = price * (1 + abs(rng.uniform(0, 0.02)))
        low = price * (1 - abs(rng.uniform(0, 0.02)))
        out.append(
            Candle(
                ts=1_600_000_000 + index * 86_400,
                open=Decimal(f"{rng.uniform(low, high):.2f}"),
                high=Decimal(f"{high:.2f}"),
                low=Decimal(f"{low:.2f}"),
                close=Decimal(f"{price:.2f}"),
                volume=Decimal("1000"),
            )
        )
    return out


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# == significance ===============================================================================


def test_significance_from_rule_renders_both_fee_regimes_with_n_eff(tmp_path):
    """`--from rule` backtests one stored rule and prices its closed trades at BOTH fee
    regimes -- significance.py's own rule is that they are never averaged. Raw n and n_eff
    must both be printed, side by side, never n_eff alone and never raw n alone (#427)."""
    db = _turtle_db(tmp_path)
    result = _invoke(
        CliRunner(), db, tmp_path,
        "research", "significance", "--from", "rule", "--rule", "1",
    )
    assert result.exit_code == 0, result.output
    assert "outside_allowance_taker" in result.output
    assert "inside_allowance_fee_free" in result.output
    # Raw n beside n_eff, never one without the other (#427's rule, stated in render_family).
    assert re.search(r"closed trades n=\d+ pooled -> [\d.]+ effective", result.output)
    assert "verdict:" in result.output


def test_significance_from_deployment_renders_both_fee_regimes(tmp_path):
    """`--from deployment` reads this db's own `trade_outcomes` ledger -- the same
    classifier `pooled-review` uses (`ledger_round_trips` + `RoundTrip.outcome()`)."""
    db = tmp_path / "deployment.db"
    conn = connect(str(db))
    migrate(conn)
    repo = Repository(conn)
    rows = [
        dict(
            product_id="BTC-USD", rule_name="turtle_breakout", is_dca=False,
            opened_at=1000, closed_at=2000, qty=Decimal("1"),
            entry_fill=Decimal("100"), exit_fill=Decimal("110"),
            fees=Decimal("1"), pnl_net=Decimal("8"),
        ),
        dict(
            product_id="ETH-USD", rule_name="turtle_breakout", is_dca=False,
            opened_at=3000, closed_at=4000, qty=Decimal("1"),
            entry_fill=Decimal("200"), exit_fill=Decimal("190"),
            fees=Decimal("1"), pnl_net=Decimal("-11"),
        ),
        dict(
            product_id="SOL-USD", rule_name="turtle_breakout", is_dca=False,
            opened_at=5000, closed_at=6000, qty=Decimal("2"),
            entry_fill=Decimal("50"), exit_fill=Decimal("55"),
            fees=Decimal("1"), pnl_net=Decimal("9"),
        ),
    ]
    for row in rows:
        repo.insert_trade_outcome(row)
    conn.close()

    result = _invoke(CliRunner(), db, tmp_path, "research", "significance", "--from", "deployment")
    assert result.exit_code == 0, result.output
    assert "deployment @ outside_allowance_taker" in result.output
    assert "deployment @ inside_allowance_fee_free" in result.output
    assert "closed trades n=3 pooled ->" in result.output


# == pooled-review ===============================================================================


def _pooled_review_dbs(tmp_path: Path) -> tuple[Path, Path]:
    """Two profile dbs. `db1` carries one round trip recorded BOTH as a matched
    orders pair (BUY 100 -> SELL 110) AND as the equivalent `trade_outcomes` row -- so
    `build_sample`'s dedup actually has a twin to collapse -- plus one ledger-only loss.
    `db2` carries three more ledger-only trips, so the pool is a genuine UNION across two
    files, not just one db's own rows. Every connection is checkpointed and closed before
    return so the on-disk `.db` file (not the WAL sidecars) is a clean, hashable snapshot.
    """
    db1 = tmp_path / "profile-db1.db"
    conn = connect(str(db1))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=1_800_000_000
    )
    repo.insert_order(
        dict(
            mode="paper", product_id="BTC-USD", side="BUY", order_type="market",
            qty=Decimal("1"), limit_price=None, status="filled", fee=Decimal("1"),
            expected_fill=Decimal("100"), actual_fill=Decimal("100"),
            filled_quantity=Decimal("1"), raw_response=None, confirmation="auto",
            rule_id=1, created_at=1000, updated_at=1000,
        )
    )
    repo.insert_order(
        dict(
            mode="paper", product_id="BTC-USD", side="SELL", order_type="market",
            qty=Decimal("1"), limit_price=None, status="filled", fee=Decimal("1"),
            expected_fill=Decimal("110"), actual_fill=Decimal("110"),
            filled_quantity=Decimal("1"), raw_response=None, confirmation="auto",
            rule_id=1, created_at=2000, updated_at=2000,
        )
    )
    repo.insert_trade_outcome(
        dict(
            product_id="BTC-USD", rule_name="turtle_breakout", is_dca=False,
            opened_at=1000, closed_at=2000, qty=Decimal("1"),
            entry_fill=Decimal("100"), exit_fill=Decimal("110"),
            fees=Decimal("1"), pnl_net=Decimal("8"),
        )
    )
    repo.insert_trade_outcome(
        dict(
            product_id="ETH-USD", rule_name="turtle_breakout", is_dca=False,
            opened_at=3000, closed_at=4000, qty=Decimal("1"),
            entry_fill=Decimal("200"), exit_fill=Decimal("190"),
            fees=Decimal("1"), pnl_net=Decimal("-11"),
        )
    )
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    db2 = tmp_path / "profile-db2.db"
    conn = connect(str(db2))
    migrate(conn)
    repo = Repository(conn)
    for opened, closed, entry, exit_, pnl in (
        (5000, 6000, "50", "55", "9"),
        (7000, 8000, "55", "60", "9"),
        (9000, 10000, "60", "58", "-5"),
    ):
        repo.insert_trade_outcome(
            dict(
                product_id="SOL-USD", rule_name="turtle_breakout", is_dca=False,
                opened_at=opened, closed_at=closed, qty=Decimal("2"),
                entry_fill=Decimal(entry), exit_fill=Decimal(exit_),
                fees=Decimal("1"), pnl_net=Decimal(pnl),
            )
        )
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return db1, db2


def test_pooled_review_pools_across_profiles_dedups_and_states_the_power_sentence(tmp_path):
    db1, db2 = _pooled_review_dbs(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "research", "pooled-review",
            "--db", str(db1), "--db", str(db2),
            "--run-date", "2026-08-28",
        ],
    )
    assert result.exit_code == 0, result.output

    # Pooling: 2 trips from db1 (one deduped) + 3 from db2 -> 5 pooled, not 6 -- the dedup
    # actually removed the orders-derived twin of the ledger row it matched.
    assert "**pooled** | — | **5**" in result.output
    assert "(1 deduped this" in result.output

    # #427's power sentence, exact wording from `pooled_review.power_sentence`.
    assert "can only see an edge of" in result.output
    assert "points or larger" in result.output

    # No pass/fail verdict on the edge anywhere in this report (its own closing section).
    assert "This is not a pass/fail gate" in result.output


def test_pooled_review_out_and_jsonl_write_the_files_asked_for(tmp_path):
    db1, db2 = _pooled_review_dbs(tmp_path)
    out_path = tmp_path / "report.md"
    jsonl_path = tmp_path / "report.jsonl"
    result = CliRunner().invoke(
        cli,
        [
            "research", "pooled-review",
            "--db", str(db1), "--db", str(db2),
            "--run-date", "2026-08-28",
            "--out", str(out_path),
            "--jsonl", str(jsonl_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_path.is_file()
    assert "can only see an edge of" in out_path.read_text()

    assert jsonl_path.is_file()
    row = json.loads(jsonl_path.read_text())
    assert row["pooled_n"] == 5
    assert row["counted_n"] == 5
    assert "can only see an edge of" in row["power_sentence"]


def test_pooled_review_never_writes_to_the_profile_dbs(tmp_path):
    """This command points at live deployment databases (`DEFAULT_POOLED_REVIEW_DBS`); every
    read goes through `_connect_ro` (`mode=ro`). Assert the guarantee mechanically: hash the
    two profile `.db` files before and after a full run (report + `--out` + `--jsonl`), and
    require them byte-identical. WAL sidecars (`-wal`/`-shm`) are excluded from the hash --
    a reader opening a WAL-mode db legitimately creates/touches those, but a `mode=ro`
    connection is structurally unable to write a new page into the main file itself, which
    is the property that actually matters here."""
    db1, db2 = _pooled_review_dbs(tmp_path)
    before = (_file_hash(db1), _file_hash(db2))

    result = CliRunner().invoke(
        cli,
        [
            "research", "pooled-review",
            "--db", str(db1), "--db", str(db2),
            "--run-date", "2026-08-28",
            "--out", str(tmp_path / "report.md"),
            "--jsonl", str(tmp_path / "report.jsonl"),
        ],
    )
    assert result.exit_code == 0, result.output

    after = (_file_hash(db1), _file_hash(db2))
    assert before == after, "keel research pooled-review wrote to a profile db it must only read"


# == #610: significance/factors/independence must be as read-only as pooled-review =============


def _stamp_schema_version(db: Path, version: int) -> None:
    """Rewrite the stored schema_version directly, bypassing `migrate()` -- the one way to
    build a database that CLAIMS an older version than its tables actually match, without
    hand-maintaining a second, older copy of the schema. The tables are today's; only the
    claimed version is stale, which is exactly the field the new read-only seam reads."""
    conn = connect(str(db))
    conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()
    conn.close()


def test_significance_factors_independence_never_write_to_the_database(tmp_path):
    """#610: these are read-only questions about a deployment, the same way `pooled-review`
    is -- none may open it read-write or migrate it as a side effect of being asked. Same
    mechanical pin as `test_pooled_review_never_writes_to_the_profile_dbs`: hash the file
    before and after a real CLI invocation and require it byte-identical."""
    sig_db = tmp_path / "sig-ro.db"
    conn = connect(str(sig_db))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_trade_outcome(
        dict(
            product_id="BTC-USD", rule_name="turtle_breakout", is_dca=False,
            opened_at=1000, closed_at=2000, qty=Decimal("1"),
            entry_fill=Decimal("100"), exit_fill=Decimal("110"),
            fees=Decimal("1"), pnl_net=Decimal("8"),
        )
    )
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    before_sig = _file_hash(sig_db)
    result = _invoke(
        CliRunner(), sig_db, tmp_path, "research", "significance", "--from", "deployment"
    )
    assert result.exit_code == 0, result.output
    assert _file_hash(sig_db) == before_sig, "significance --from deployment wrote to the db"

    fac_db = tmp_path / "factors-ro.db"
    conn = connect(str(fac_db))
    migrate(conn)
    repo = Repository(conn)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _factor_candles(400, seed=7))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    before_fac = _file_hash(fac_db)
    result = _invoke(
        CliRunner(), fac_db, tmp_path,
        "research", "factors", "--product", "BTC-USD", "--granularity", "ONE_DAY",
    )
    assert result.exit_code == 0, result.output
    assert _file_hash(fac_db) == before_fac, "factors wrote to the db"

    indep_db = tmp_path / "independence-ro.db"
    conn = connect(str(indep_db))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule("turtle_breakout", _TURTLE_A_PARAMS, status="candidate", now_ts=1_800_000_000)
    repo.insert_rule("turtle_breakout", _TURTLE_B_PARAMS, status="candidate", now_ts=1_800_000_000)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _sawtooth_candles(96))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    before_indep = _file_hash(indep_db)
    result = _invoke(
        CliRunner(), indep_db, tmp_path,
        "research", "independence", "--rule-a", "1", "--rule-b", "2",
    )
    assert result.exit_code == 0, result.output
    assert _file_hash(indep_db) == before_indep, "independence wrote to the db"


def test_research_readonly_commands_refuse_a_database_older_than_this_binary(tmp_path):
    """#610, decision 1: a database whose stored schema predates what this binary expects is
    an OPERATOR error (run a command that writes, or upgrade), not a question a read-only
    command can answer honestly by silently reading a schema it was never tested against.
    Built by migrating to the current version and then rewriting the stamp down by one --
    the tables match today's shape; only the claimed version is stale, which is exactly the
    field under test."""
    db = tmp_path / "stale.db"
    conn = connect(str(db))
    migrate(conn)
    conn.close()
    _stamp_schema_version(db, SCHEMA_VERSION - 1)

    for args in (
        ["research", "significance", "--from", "deployment"],
        ["research", "factors", "--product", "BTC-USD", "--granularity", "ONE_DAY"],
        ["research", "independence", "--rule-a", "1", "--rule-b", "2"],
    ):
        result = _invoke(CliRunner(), db, tmp_path, *args)
        assert result.exit_code != 0, f"{args} should refuse a stale-schema database"
        assert "schema" in result.output.lower(), result.output
        assert str(SCHEMA_VERSION) in result.output, result.output


def test_research_readonly_commands_refuse_a_missing_database(tmp_path):
    """Mirrors the MCP read-only surface's own rule (`keel/mcp/tools.py::_open_readonly_repo`):
    a read-only view must never let `sqlite3.connect` CREATE the file it cannot find, which
    would make a typo in `--db` the first write this surface has ever made.

    The message is pinned, not just the exit code and the absent file: `mode=ro` against a
    missing path already refuses on its own (sqlite3 raises `OperationalError: unable to
    open database file` even with no explicit check), so a test that stopped at "non-zero
    exit, file absent" would pass unchanged if the seam's own existence check -- which turns
    that into a command a caller can actually act on -- were deleted.
    """
    missing = tmp_path / "does-not-exist.db"
    result = _invoke(
        CliRunner(), missing, tmp_path, "research", "significance", "--from", "deployment"
    )
    assert result.exit_code != 0
    assert not missing.exists(), "a read-only command must never create the database file"
    assert "no database at" in result.output, result.output
    assert "read-only" in result.output, result.output


# == throughput ==================================================================================


def test_throughput_allocates_within_allowance_and_never_exceeds_it(tmp_path):
    venues = [
        {
            "venue": "coinbase",
            "monthly_allowance": "1000",
            "mean_trade_notional": "100",
            "expected_signals_per_month": "5",
        }
    ]
    products = [
        {
            "symbol": "BTC-USD",
            "venues": ["coinbase"],
            "mean_trade_notional": "100",
            "expected_signals_per_month": "3",
        },
        {
            "symbol": "ETH-USD",
            "venues": ["coinbase"],
            "mean_trade_notional": "100",
            "expected_signals_per_month": "20",  # deliberately too big to fit alongside BTC
        },
    ]
    allowances = {"coinbase": "1000"}

    result = CliRunner().invoke(
        cli,
        [
            "research", "throughput",
            "--venues-json", json.dumps(venues),
            "--products-json", json.dumps(products),
            "--allowances-json", json.dumps(allowances),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "allocation:" in result.output

    match = re.search(
        r"coinbase \(cap (?P<cap>[\d.]+)/month\): enabled=(?P<enabled>\[.*?\]) "
        r"deferred=(?P<deferred>\[.*?\]) spend=(?P<spend>[\d.]+)",
        result.output,
    )
    assert match is not None, result.output
    assert "BTC-USD" in match.group("enabled")
    assert "ETH-USD" in match.group("deferred")  # too big to fit -- deferred, never squeezed in
    # The allocator's non-negotiable (throughput.py's own docstring): a plan's spend never
    # exceeds its venue's allowance.
    assert Decimal(match.group("spend")) <= Decimal(match.group("cap"))


# == tuning =======================================================================================


def test_tuning_reports_declared_search_spaces_with_cell_counts(tmp_path):
    result = CliRunner().invoke(cli, ["research", "tuning", "--rule-kind", "turtle_breakout"])
    assert result.exit_code == 0, result.output
    declared = tuning_mod.declared_cells("turtle_breakout")
    assert f"turtle_breakout: declared search space ({declared} cells)" in result.output
    for name, bounds in tuning_mod.SEARCH_SPACES["turtle_breakout"].items():
        assert f"{name}: {bounds}" in result.output


def test_tuning_explored_within_declared_bounds_reports_as_explored_not_refused(tmp_path):
    explored = {"entry_lookback": [25, 40]}
    result = CliRunner().invoke(
        cli,
        [
            "research", "tuning",
            "--rule-kind", "turtle_breakout",
            "--explored-json", json.dumps(explored),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "refused" not in result.output.lower()
    check = tuning_mod.explored_vs_declared(
        {name: (float(bounds[0]), float(bounds[1])) for name, bounds in explored.items()},
        "turtle_breakout",
    )
    assert (
        f"explored {check.explored_cells} of {check.declared_cells} declared cells"
        in result.output
    )


# == factors ======================================================================================


def test_factors_renders_pairwise_cluster_and_variance_sections(tmp_path):
    db = tmp_path / "factors.db"
    conn = connect(str(db))
    migrate(conn)
    repo = Repository(conn)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _factor_candles(400, seed=7))
    conn.close()

    result = _invoke(
        CliRunner(), db, tmp_path,
        "research", "factors", "--product", "BTC-USD", "--granularity", "ONE_DAY",
    )
    assert result.exit_code == 0, result.output
    assert "CTS factor collinearity -- BTC-USD" in result.output
    assert "varying factor(s) of 11" in result.output
    assert "pairwise (Holm-Bonferroni adjusted" in result.output
    assert "pre-declared clusters" in result.output
    assert "CTS total variance:" in result.output


# == independence ================================================================================


def test_independence_renders_overlap_and_correlation_figures(tmp_path):
    db = tmp_path / "independence.db"
    conn = connect(str(db))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule("turtle_breakout", _TURTLE_A_PARAMS, status="candidate", now_ts=1_800_000_000)
    repo.insert_rule("turtle_breakout", _TURTLE_B_PARAMS, status="candidate", now_ts=1_800_000_000)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _sawtooth_candles(96))
    conn.close()

    result = _invoke(
        CliRunner(), db, tmp_path,
        "research", "independence", "--rule-a", "1", "--rule-b", "2",
    )
    assert result.exit_code == 0, result.output
    assert "independence -- rule 1 vs rule 2 over 96 common bars (§80.16)" in result.output
    assert "jaccard overlap" in result.output
    assert "position correlation" in result.output
    assert "pnl correlation" in result.output
    assert "median entry distance" in result.output
    assert re.search(r"entry distances \(n=\d+\):", result.output)


def test_independence_does_not_stretch_a_trade_past_the_shared_history(tmp_path):
    """Characterises `keel research independence` when the two rules' cached series differ,
    so the common bar index has INTERIOR GAPS rather than being total.

    Every other fixture here puts both rules on the same product, which makes the
    intersection total and leaves `_vectors`' off-index branch unexercised. This one puts
    rule 1 on BTC-USD (192 bars) and rule 2 on ETH-USD (every other one of those bars), so
    the intersection is 96 gapped bars and trades routinely exit on a timestamp that is in
    one rule's cache and not in the shared index.

    **What this does NOT do, stated because the surrounding commit changes that branch.**
    `_vectors` used to map an off-index exit to `n - 1` and now walks back to the previous
    shared bar; the difference is real on the merits (`n - 1` asserts occupancy on bars the
    shared history never observed, which can only inflate Jaccard, never deflate it) but
    **this fixture does not distinguish the two** -- restoring the old defaulting leaves the
    number below unchanged at 0.333..., which was verified rather than assumed. So this is a
    characterisation pin, not a proof of the fix: it holds the gapped path executing and its
    output stable, and it would catch a future change that moves the number. The correctness
    argument for the projection lives in `_vectors`' own docstring, not here.
    """
    db = tmp_path / "indep-depth.db"
    conn = connect(str(db))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout", {**_TURTLE_A_PARAMS, "product_id": "BTC-USD"},
        status="candidate", now_ts=1_800_000_000,
    )
    repo.insert_rule(
        "turtle_breakout", {**_TURTLE_A_PARAMS, "product_id": "ETH-USD"},
        status="candidate", now_ts=1_800_000_000,
    )
    btc = _sawtooth_candles(192)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, btc)
    # ETH keeps only every OTHER bar, so the intersection has INTERIOR gaps rather than a
    # truncated tail. That distinction is the whole point: for a trade whose exit lies beyond
    # the shared window, `n - 1` is the right answer (it really was held throughout). The bug
    # is a trade whose exit falls in a HOLE -- present in one cache, absent from the
    # intersection -- which the old code stretched to the end of the window instead of back to
    # the previous shared bar.
    repo.upsert_candles("ETH-USD", Granularity.ONE_DAY, btc[::2])
    conn.close()

    result = _invoke(
        CliRunner(), db, tmp_path,
        "research", "independence", "--rule-a", "1", "--rule-b", "2",
    )
    assert result.exit_code == 0, result.output
    assert "over 96 common bars" in result.output, result.output

    match = re.search(r"jaccard overlap[^0-9]*([0-9.]+)", result.output)
    assert match, result.output
    jaccard = float(match.group(1))
    assert 0.0 <= jaccard <= 1.0, jaccard
    assert jaccard == pytest.approx(_EXPECTED_DEPTH_JACCARD, abs=1e-6), (
        f"jaccard over a gapped 96-bar shared window came out {jaccard}, expected "
        f"{_EXPECTED_DEPTH_JACCARD} -- a change here means trades are being projected onto "
        "the common index differently; check _vectors before updating this number"
    )


# == the rail, at the rendered surface ==========================================================
#
# The Strathern rail (cscv.py/deflate.py/walkforward.py) is pinned at the SOURCE level in
# `test_research_front_door.py::test_research_module_never_sorts_ranks_or_maxes` (an AST scan
# of keel/commands/research.py) and at walkforward.py's own source in
# `tests/research/test_walkforward.py::test_refusal_to_rank_enforced_by_source_scan`. Neither
# ever runs a command and reads its stdout. This test does: it drives every `keel research`
# subcommand that fronts a rail-bearing module through a fixture that makes it actually
# SUCCEED (a refusal has nothing to rank in the first place, so it would not catch a
# regression), and asserts the same vocabulary ban
# `test_refusal_to_rank_enforced_by_source_scan` uses, over the rendered output this time.


def _pbo_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "pbo-trials.jsonl"
    for column_index in range(6):
        drift = Decimal(column_index) / Decimal(10)
        series = [drift + (Decimal("10") if i % 2 else Decimal("-10")) for i in range(32)]
        trials_ledger.append_trial(
            path,
            trial_id=f"grid-{column_index}",
            session="grid",
            rule="turtle_breakout",
            params={"entry": 20 + column_index * 5},
            provenance="fitted",
            kind="sweep_node",
            decision="diagnostic_only",
            per_bar_pnl=series,
            timestamp=1_700_000_000,
        )
    return path


def _deflate_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "deflate-trials.jsonl"
    for i in range(5):
        trials_ledger.append_trial(
            path,
            trial_id=f"t{i}",
            session="s",
            rule="turtle_breakout",
            params={},
            provenance="fitted",
            kind="sweep_node",
            decision="selected",
            series_missing=True,
            timestamp=1_700_000_000 + i,
        )
    return path


def test_no_evidence_subcommand_names_a_winner(tmp_path):
    """Run the evidence subcommands against fixtures that make each SUCCEED -- a refusal has
    nothing to rank, so it would not exercise a renderer's word choice -- and assert none of
    their rendered stdout names a winner.

    Covers the three rail-bearing aliases (`pbo`, `deflate`, `walk-forward`) AND the four new
    subcommands that render a report of their own (`significance`, `throughput`, `tuning`,
    `factors`). The first version of this test drove only the three aliases while its name
    claimed "no evidence subcommand", which was an overclaim; the six new subcommands are the
    newest renderers on this surface and so the likeliest place a ranking phrase gets written.

    The word list starts from `tests/research/test_walkforward.py::
    test_refusal_to_rank_enforced_by_source_scan` (`best`, `winner`, `optimal`) and is widened
    here, because that list is a source-scan vocabulary and this is an OUTPUT scan: a renderer
    can name a winner without ever using the word "best". `highest`/`lowest`/`top-ranked`/
    `strongest`/`ranked #` are the phrasings a report actually reaches for.

    Still not a proof. A renderer could name a winner in words none of these match, and this
    only sees the fixtures it happens to run. It is a tripwire on the obvious phrasings, and
    it is stated as one.

    Mutation-verified: see the commit message for the exact renderer edit (a `best: ...`
    line added to `walkforward.render_lines`), the failure it produced here, and the revert.
    """
    runner = CliRunner()
    outputs: dict[str, str] = {}

    pbo_ledger = _pbo_ledger(tmp_path)
    pbo_result = runner.invoke(
        cli, ["research", "pbo", "--ledger", str(pbo_ledger), "--session", "grid", "--blocks", "4"]
    )
    assert pbo_result.exit_code == 0, pbo_result.output
    assert "PBO" in pbo_result.output
    outputs["pbo"] = pbo_result.output

    deflate_ledger = _deflate_ledger(tmp_path)
    deflate_result = runner.invoke(
        cli,
        [
            "research", "deflate",
            "--ledger", str(deflate_ledger), "--sharpe", "0.4",
            "--rho", "0.5", "--trial-sharpe-variance", "0.05",
        ],
    )
    assert deflate_result.exit_code == 0, deflate_result.output
    assert "DSR" in deflate_result.output
    outputs["deflate"] = deflate_result.output

    wf_db = _turtle_db(tmp_path, name="wf.db")
    wf_ledger = tmp_path / "wf-trials.jsonl"
    wf_result = _invoke(
        runner, wf_db, tmp_path,
        "research", "walk-forward",
        "--rule", "1", "--train-bars", "40", "--test-bars", "20",
        "--ledger", str(wf_ledger),
    )
    assert wf_result.exit_code == 0, wf_result.output
    assert "walk-forward:" in wf_result.output
    outputs["walk-forward"] = wf_result.output

    sig_db = _turtle_db(tmp_path, name="winner-sig.db")
    sig_result = _invoke(
        runner, sig_db, tmp_path, "research", "significance", "--from", "rule", "--rule", "1"
    )
    assert sig_result.exit_code == 0, sig_result.output
    outputs["significance"] = sig_result.output

    thr_result = runner.invoke(
        cli,
        [
            "research", "throughput",
            "--venues-json",
            json.dumps([{
                "venue": "coinbase", "monthly_allowance": "5000",
                "mean_trade_notional": "100", "expected_signals_per_month": "10",
            }]),
        ],
    )
    assert thr_result.exit_code == 0, thr_result.output
    outputs["throughput"] = thr_result.output

    tun_result = runner.invoke(cli, ["research", "tuning"])
    assert tun_result.exit_code == 0, tun_result.output
    outputs["tuning"] = tun_result.output

    fac_db = tmp_path / "winner-fac.db"
    fac_conn = connect(str(fac_db))
    migrate(fac_conn)
    Repository(fac_conn).upsert_candles(
        "BTC-USD", Granularity.ONE_DAY, _factor_candles(400, seed=7)
    )
    fac_conn.close()
    fac_result = _invoke(
        runner, fac_db, tmp_path,
        "research", "factors", "--product", "BTC-USD", "--granularity", "ONE_DAY",
    )
    assert fac_result.exit_code == 0, fac_result.output
    outputs["factors"] = fac_result.output

    banned = (
        "best", "winner", "optimal", "top-ranked", "top ranked",
        "highest", "lowest", "strongest", "ranked #",
    )
    for name, output in outputs.items():
        lowered = output.lower()
        for word in banned:
            assert word not in lowered, (
                f"keel research {name} printed ranking word {word!r} -- a score may report "
                "and may gate, but naming a leader is the Strathern rail's one prohibition"
            )


def test_backtest_failure_during_a_fold_is_not_a_refusal(tmp_path, monkeypatch):
    """A `ValueError` escaping the backtest engine mid-fold must NOT become an exit-0
    refusal.

    `keel research walk-forward` catches `ValueError` to turn "no train/test window fits
    this candle series" into a printed refusal at exit 0 (#601). That catch is deliberately
    wrapped around `wf_mod.folds` ALONE, and this test is why. `wf_mod.walk_forward` runs
    `backtest` twice per fold and reaches `deflate`; a `ValueError` raised in there is a
    BUG -- a Decimal conversion, a malformed candle, `walkforward._closed_pnl`'s
    poisoned-row guard -- and a wider catch would print it as `refused: ...` and exit 0,
    making a genuine engine defect indistinguishable from an honest "the cached history
    cannot answer this" AND invisible to anything downstream checking the exit code. That
    is the one shape where #601's new contract could make a failure quieter than it was
    before, so it is pinned rather than trusted.

    The fixture reaches `walk_forward` for real: 96 bars with train 40 / test 20 is a
    window `folds` accepts, so the narrow catch is passed cleanly and the failure happens
    where a fold runs.

    Mutation-verified: see the commit message for the restored wide catch, the failure this
    test then produced, and the revert.
    """
    runner = CliRunner()
    db = _turtle_db(tmp_path, name="wf-bug.db")

    def _boom(*args, **kwargs):
        raise ValueError("engine bug: malformed candle at bar 7")

    monkeypatch.setattr(wf_mod.backtest_mod, "backtest", _boom)

    result = _invoke(
        runner, db, tmp_path,
        "research", "walk-forward",
        "--rule", "1", "--train-bars", "40", "--test-bars", "20",
        "--ledger", str(tmp_path / "wf-bug-trials.jsonl"),
    )

    assert result.exit_code != 0, (
        "a ValueError from the backtest engine exited 0 -- an engine bug is being reported "
        f"as success: {result.output!r}"
    )
    assert "refused:" not in result.output, (
        "a ValueError from the backtest engine was printed as an evidence refusal; only "
        f"`folds` window failures may take that path: {result.output!r}"
    )
    assert isinstance(result.exception, ValueError), result.exception
    assert "engine bug" in str(result.exception)


def test_operator_mistakes_in_throughput_are_not_refusals(tmp_path):
    """`keel research throughput` must report an OPERATOR mistake as an error, not as a
    refusal at exit 0 -- the same boundary
    `test_backtest_failure_during_a_fold_is_not_a_refusal` pins for walk-forward.

    `throughput.py` raises `ValueError` in five places and only ONE of them is
    evidence-shaped (`InsufficientThroughput`: nothing is flowing, so no time-to-detection
    can be stated). The other four report a caller mistake. Two are reachable from this
    command and are pinned here:

    * a non-positive `mean_trade_notional` in `--venues-json`, which
      `VenueThroughput.trades_per_month` raises on -- a typo in an option value;
    * a product eligible on no listed venue, which `allocate` raises on and whose own
      docstring calls "an error the caller must fix in the eligibility table, not silently
      droppable inventory" -- so printing it as `refused:` would be this command overruling
      the callee's stated claim about itself.

    Both were exit-0 `refused:` lines until #601 review caught them. The third assertion
    keeps the honest refusal honest: an empty `--venues-json` still refuses at exit 0, so
    this pin cannot be satisfied by turning every failure into an error.

    Mutation-verified: see the commit message for the restored wide `except ValueError`,
    the failures it produced here, and the revert.
    """
    runner = CliRunner()

    typo = runner.invoke(
        cli,
        [
            "research", "throughput",
            "--venues-json",
            json.dumps([{
                "venue": "coinbase",
                "monthly_allowance": "500",
                "mean_trade_notional": "0",
                "expected_signals_per_month": "1",
            }]),
        ],
    )
    assert typo.exit_code != 0, (
        "a zero mean_trade_notional exited 0 -- an operator typo in --venues-json is being "
        f"reported as an evidence refusal: {typo.output!r}"
    )
    assert "refused:" not in typo.output, typo.output

    ineligible = runner.invoke(
        cli,
        [
            "research", "throughput",
            "--venues-json",
            json.dumps([{
                "venue": "coinbase",
                "monthly_allowance": "5000",
                "mean_trade_notional": "100",
                "expected_signals_per_month": "10",
            }]),
            "--products-json",
            json.dumps([{
                "symbol": "AAPL",
                "venues": ["alpaca"],
                "mean_trade_notional": "100",
                "expected_signals_per_month": "5",
            }]),
            "--allowances-json", json.dumps({"coinbase": "5000"}),
        ],
    )
    assert ineligible.exit_code != 0, (
        "a product eligible on no listed venue exited 0 -- `allocate` calls that an error "
        f"the caller must fix, and this command relabelled it a refusal: {ineligible.output!r}"
    )
    assert "refused:" not in ineligible.output, ineligible.output

    # ...and the one genuine refusal still refuses, so this pin cannot be satisfied by
    # making everything an error.
    nothing_flowing = runner.invoke(cli, ["research", "throughput", "--venues-json", "[]"])
    assert nothing_flowing.exit_code == 0, nothing_flowing.output
    assert "refused:" in nothing_flowing.output, nothing_flowing.output
