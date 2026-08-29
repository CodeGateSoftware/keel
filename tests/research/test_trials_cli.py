"""CLI surface for the trials ledger (spec §4.5) -- the scratchpad recording path, plus the
`trials monte-carlo` resampling front-end (#441)."""

from __future__ import annotations

from decimal import Decimal

from click.testing import CliRunner

from keel.agent import build_rule_from_params
from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.research import ledger as trials_ledger
from keel.research.montecarlo import equity_curve, max_drawdown
from keel.strategy.backtest import backtest
from keel.types import Candle, Granularity


def _record(runner, path, trial_id, decision="selected"):
    return runner.invoke(
        cli,
        [
            "trials", "record",
            "--ledger", str(path),
            "--trial-id", trial_id,
            "--session", "s1",
            "--rule", "turtle_breakout",
            "--params", '{"entry": 40}',
            "--provenance", "fitted",
            "--kind", "sweep_node",
            "--decision", decision,
            "--series-missing",
        ],
    )


def test_record_then_list_and_verify(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"

    assert _record(runner, path, "t1").exit_code == 0
    assert _record(runner, path, "t2", decision="diagnostic_only").exit_code == 0

    listed = runner.invoke(cli, ["trials", "list", "--ledger", str(path)])
    assert listed.exit_code == 0
    assert "t1" in listed.output
    assert "M=2" in listed.output
    assert "N_decisions=1" in listed.output

    verified = runner.invoke(cli, ["trials", "verify", "--ledger", str(path)])
    assert verified.exit_code == 0
    assert "intact" in verified.output.lower()


def test_verify_exits_nonzero_on_tamper(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    _record(runner, path, "t1")
    _record(runner, path, "t2")

    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace('"s1"', '"s2"')
    path.write_text("\n".join(lines) + "\n")

    verified = runner.invoke(cli, ["trials", "verify", "--ledger", str(path)])
    assert verified.exit_code != 0
    assert "row 1" in verified.output


def test_record_rejects_bad_enum(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    result = runner.invoke(
        cli,
        [
            "trials", "record", "--ledger", str(path), "--trial-id", "t1",
            "--session", "s", "--rule", "r", "--params", "{}",
            "--provenance", "vibes", "--kind", "sweep_node",
            "--decision", "selected", "--series-missing",
        ],
    )
    assert result.exit_code != 0


def test_pbo_command_reports_but_never_names_a_winner(tmp_path):
    """The command prints probabilities. It must not print a winning configuration."""
    path = tmp_path / "trials.jsonl"
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

    runner = CliRunner()
    result = runner.invoke(
        cli, ["trials", "pbo", "--ledger", str(path), "--session", "grid", "--blocks", "4"]
    )
    assert result.exit_code == 0
    assert "PBO" in result.output
    assert "degradation slope" in result.output
    assert "Prob[OOS < 0]" in result.output
    assert "dominance" in result.output.lower()

    # ⛔ Strathern rail (spec §6): the output must not let a caller recover which
    # configuration won. Assert the substantive property -- no candidate parameter value and
    # no winner-naming vocabulary reaches the output -- rather than banning loose words, since
    # the legitimate N>>10 warning also contains "recommended".
    for entry in (20, 25, 30, 35, 40, 45):
        assert f"entry={entry}" not in result.output
        assert f"'entry': {entry}" not in result.output
    for word in ("best", "winner", "optimal", "chosen"):
        assert word not in result.output.lower()


def test_pbo_refuses_when_every_trial_is_series_missing(tmp_path):
    """Backfilled rows count toward M but cannot be columns (spec §4.6)."""
    path = tmp_path / "trials.jsonl"
    runner = CliRunner()
    _record(runner, path, "backfilled")

    result = runner.invoke(cli, ["trials", "pbo", "--ledger", str(path), "--blocks", "4"])
    # #601: an evidence-shaped refusal is a result, not an error -- exit 0, on stdout.
    assert result.exit_code == 0, result.output
    assert "refused" in result.output
    assert "no usable columns" in result.output


def test_deflate_reports_a_band_and_refuses_to_invent_DSR(tmp_path):
    """Anything the ledger cannot supply must read as MISSING, not as a plausible default."""
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    for i in range(5):
        _record(runner, path, f"t{i}")

    result = runner.invoke(
        cli, ["trials", "deflate", "--ledger", str(path), "--sharpe", "0.4"]
    )
    assert result.exit_code == 0, result.output
    # A band, not a single number, because rho is not measured here.
    assert "0.00" in result.output and "0.90" in result.output
    assert "MinBTL" in result.output
    assert "DSR: NOT COMPUTED" in result.output


def test_deflate_computes_dsr_when_the_variance_is_supplied_explicitly(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    for i in range(5):
        _record(runner, path, f"t{i}")

    result = runner.invoke(
        cli,
        ["trials", "deflate", "--ledger", str(path), "--sharpe", "0.4",
         "--rho", "0.5", "--trial-sharpe-variance", "0.05"],
    )
    assert result.exit_code == 0, result.output
    assert "SR_0" in result.output
    assert "DSR" in result.output
    assert "NOT COMPUTED" not in result.output


def test_deflate_refuses_on_too_few_decision_trials(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    _record(runner, path, "only-one")
    result = runner.invoke(
        cli, ["trials", "deflate", "--ledger", str(path), "--sharpe", "0.4"]
    )
    # #601: an evidence-shaped refusal is a result, not an error -- exit 0, on stdout.
    assert result.exit_code == 0, result.output
    assert "refused" in result.output
    assert "need >= 2" in result.output


# -- trials monte-carlo (#441): is the equity curve an outlier? -----------------------------------


def _mc_candles(n: int, *, start: int = 1_700_000_000) -> list[Candle]:
    """`n` daily bars in an asymmetric 19-bar sawtooth -- an 8-bar rally, a 9-bar crash, a
    2-bar drift, then again -- so a turtle rule both enters and gets stopped out AND its
    closed P&L comes out MIXED-SIGN: wins and losses in one multiset is what makes
    reshuffling move max drawdown (a big loss early digs a different hole than the same
    loss late), which is the statistic trades mode exists to measure."""
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


def _mc_db(tmp_path, *, bars: int = 96):
    """A temp db holding one turtle rule (small lookbacks, short ATR) and its daily candles --
    96 bars = 5 full sawtooth cycles, whose backtest closes 2 wins and 2 losses."""
    conn = connect(str(tmp_path / "mc.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout",
        {
            "product_id": "BTC-USD",
            "entry_lookback": 5,
            "exit_lookback": 3,
            "atr_period": 5,
            "atr_stop_mult": "2",
        },
        status="candidate",
        now_ts=1_800_000_000,
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _mc_candles(bars))
    conn.close()
    return tmp_path / "mc.db"


def _invoke_mc(runner, db, ledger, *extra):
    """One `trials monte-carlo` invocation against a real (if tiny) db; `--config` points at a
    path that does not exist so the fee degrades to the library default instead of loading
    whatever deployment config happens to surround the test run."""
    return runner.invoke(
        cli,
        [
            "--db",
            str(db),
            "--config",
            str(db.parent / "missing.yaml"),
            "trials",
            "monte-carlo",
            "--rule",
            "1",
            "--ledger",
            str(ledger),
            *extra,
        ],
    )


def test_monte_carlo_help_pins_the_seed_and_the_two_modes():
    result = CliRunner().invoke(cli, ["trials", "monte-carlo", "--help"])
    assert result.exit_code == 0
    for needle in (
        "--seed",
        "--mode",
        "--paths",
        "--block-len",
        "--granularity",
        "trades",
        "candles",
    ):
        assert needle in result.output


def test_monte_carlo_trades_mode_appends_exactly_one_diagnostic_row(tmp_path):
    db = _mc_db(tmp_path)
    ledger = tmp_path / "trials.jsonl"
    result = _invoke_mc(CliRunner(), db, ledger, "--mode", "trades", "--paths", "20", "--seed", "7")
    assert result.exit_code == 0, result.output
    assert "percentile" in result.output
    assert "path luck" in result.output
    assert "fee_pct" in result.output  # every printed number travels with its fee
    assert "recorded" in result.output

    rows = trials_ledger.read_trials(ledger)
    assert len(rows) == 1  # ONE row, never one per path
    row = rows[0]
    assert row.decision == "diagnostic_only"  # measurement, never a gate input
    assert row.provenance == "a_priori"
    assert row.kind == "monte_carlo"
    assert row.per_trade_pnl  # the observed per-trade P&L rode along
    assert row.summary["n_paths"] == 20
    assert trials_ledger.verify_chain(ledger) == []


def test_monte_carlo_trades_mode_reports_a_non_degenerate_drawdown_distribution(tmp_path):
    """The shape statistic, not the permutation invariant: the fixture's closed P&L is
    mixed-sign, so reordering the SAME trades moves max drawdown (a big loss early digs a
    different hole than late) and the drawdown percentile is a real measurement -- while
    final equity stays pinned at one value, stated as the by-construction invariant it is."""
    db = _mc_db(tmp_path)
    ledger = tmp_path / "trials.jsonl"
    result = _invoke_mc(CliRunner(), db, ledger, "--mode", "trades", "--paths", "20", "--seed", "7")
    assert result.exit_code == 0, result.output
    assert "observed max drawdown" in result.output
    assert "resampled max drawdown" in result.output
    assert "observed drawdown percentile" in result.output
    assert "by construction" in result.output  # the final-equity invariant stays named

    summary = trials_ledger.read_trials(ledger)[0].summary
    # Final equity: the invariant -- one value, percentile exactly 1/2.
    assert summary["distribution_min"] == summary["distribution_max"]
    assert summary["percentile"] == Decimal("0.5")
    # Max drawdown: the measurement -- the reshuffles genuinely spread...
    assert summary["drawdown_min"] < summary["drawdown_max"]
    assert summary["drawdown_min"] <= summary["observed_drawdown"] <= summary["drawdown_max"]
    # ...so the drawdown percentile is NOT the forced 1/2 of a permutation invariant.
    assert summary["drawdown_percentile"] != Decimal("0.5")


def test_monte_carlo_trial_id_carries_paths_and_block_len(tmp_path):
    """Two runs differing only in --paths (or --block-len) are different experiments; the
    id must say so, or the second append would collide with the first row."""
    db = _mc_db(tmp_path)
    ledger = tmp_path / "trials.jsonl"
    result = _invoke_mc(CliRunner(), db, ledger, "--mode", "trades", "--paths", "20", "--seed", "7")
    assert result.exit_code == 0, result.output
    assert trials_ledger.read_trials(ledger)[0].trial_id == "mc-1-trades-seed7-p20"

    result = _invoke_mc(
        CliRunner(),
        db,
        ledger,
        "--mode",
        "candles",
        "--paths",
        "5",
        "--seed",
        "11",
        "--block-len",
        "10",
    )
    assert result.exit_code == 0, result.output
    assert trials_ledger.read_trials(ledger)[1].trial_id == "mc-1-candles-seed11-p5-b10"


def test_monte_carlo_candles_mode_rebacktests_bootstrapped_paths(tmp_path):
    db = _mc_db(tmp_path)
    ledger = tmp_path / "trials.jsonl"
    result = _invoke_mc(
        CliRunner(),
        db,
        ledger,
        "--mode",
        "candles",
        "--paths",
        "5",
        "--seed",
        "11",
        "--block-len",
        "10",
    )
    assert result.exit_code == 0, result.output
    assert "candles mode" in result.output
    assert "block_len=10" in result.output
    # Drawdown is informative in candles mode too: each path is a full re-backtest, so its
    # curve has a shape worth reading, not just an endpoint.
    assert "observed max drawdown" in result.output
    summary = trials_ledger.read_trials(ledger)[0].summary
    for key in (
        "observed_drawdown",
        "drawdown_min",
        "drawdown_median",
        "drawdown_max",
        "drawdown_percentile",
    ):
        assert key in summary
    rows = trials_ledger.read_trials(ledger)
    assert len(rows) == 1 and rows[0].kind == "monte_carlo"
    assert trials_ledger.verify_chain(ledger) == []


def test_monte_carlo_is_deterministic_under_a_fixed_seed(tmp_path):
    db = _mc_db(tmp_path)
    out_a = _invoke_mc(CliRunner(), db, tmp_path / "a.jsonl", "--seed", "7", "--paths", "12")
    out_b = _invoke_mc(CliRunner(), db, tmp_path / "b.jsonl", "--seed", "7", "--paths", "12")
    assert out_a.exit_code == 0 and out_b.exit_code == 0
    # The rendered report reproduces line-for-line; only the ledger row's timestamp (and so
    # its hash) legitimately differs between the two runs.
    report_a = [line for line in out_a.output.splitlines() if "recorded" not in line]
    report_b = [line for line in out_b.output.splitlines() if "recorded" not in line]
    assert report_a == report_b


def _dca_db(tmp_path, *, candles: bool):
    """A temp db holding one DCA rule; `candles=False` caches nothing at the granularity
    the fallback chain resolves to (dca declares none -> ONE_HOUR, while the cache below is
    ONE_DAY), `candles=True` caches the bars the rule will actually read."""
    conn = connect(str(tmp_path / "dca.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 7}, now_ts=1_800_000_000)
    if candles:
        repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _mc_candles(30))
    conn.close()
    return tmp_path / "dca.db"


def test_monte_carlo_refuses_when_no_closed_trades_and_writes_no_row(tmp_path):
    db = _dca_db(tmp_path, candles=True)
    ledger = tmp_path / "trials.jsonl"
    result = _invoke_mc(CliRunner(), db, ledger, "--seed", "3")
    # The mechanism: dca declares no granularity, the fallback chain resolves ONE_HOUR, and
    # nothing is cached there -- so the observed backtest runs over ZERO candles and closes
    # nothing. A refusal, not a degenerate row -- and per #601 a refusal is a printed
    # result, exit 0, not a ClickException.
    assert result.exit_code == 0, result.output
    assert "refused" in result.output
    assert "no closed trades" in result.output
    assert not ledger.exists()


def _open_only_candles(*, flat: int = 50, rally: int = 8, start: int = 1_700_000_000):
    """Bars for the genuinely-all-open case: a long flat run (no breakout, ADX stays low),
    then a sustained rally that clears both the breakout and the ADX filter -- the rule's
    FIRST entry fills on the rally and the series ends before any exit bar exists."""
    candles = []
    for i in range(flat + rally):
        o = Decimal(100) if i < flat else Decimal(100 + 8 * (i - flat + 1))
        close = o + (Decimal("0.5") if i % 2 else Decimal("-0.5"))
        candles.append(
            Candle(
                ts=start + i * 86400,
                open=o,
                high=max(o, close) + Decimal(1),
                low=min(o, close) - Decimal(1),
                close=close,
                volume=Decimal("10"),
            )
        )
    return candles


def test_monte_carlo_refuses_when_every_trade_is_genuinely_open(tmp_path):
    """The honest all-open case, verified against the backtester before it was written here:
    with the same turtle params as `_mc_db` over `_open_only_candles()` the observed backtest
    produces exactly ONE trade and its outcome is "open" -- real candles, a real entry, and
    zero realised P&L to resample, so the command refuses rather than emitting a degenerate
    row."""
    conn = connect(str(tmp_path / "open-only.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout",
        {
            "product_id": "BTC-USD",
            "entry_lookback": 5,
            "exit_lookback": 3,
            "atr_period": 5,
            "atr_stop_mult": "2",
        },
        status="candidate",
        now_ts=1_800_000_000,
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _open_only_candles())
    conn.close()

    ledger = tmp_path / "trials.jsonl"
    result = _invoke_mc(CliRunner(), tmp_path / "open-only.db", ledger, "--seed", "3")
    # #601: an evidence-shaped refusal is a result, not an error -- exit 0, on stdout.
    assert result.exit_code == 0, result.output
    assert "refused" in result.output
    assert "no closed trades" in result.output
    assert not ledger.exists()


def test_monte_carlo_candles_mode_refuses_cleanly_when_no_candles_are_cached(tmp_path):
    """A rule resolving to an empty candle list is a refusal naming what to fetch -- parity
    with trades mode's refusal, never a raw ValueError traceback out of the bootstrap."""
    db = _dca_db(tmp_path, candles=True)  # ONE_DAY cached; the fallback resolves ONE_HOUR
    ledger = tmp_path / "trials.jsonl"
    result = _invoke_mc(CliRunner(), db, ledger, "--seed", "3", "--mode", "candles")
    assert result.exit_code != 0
    assert "no candles cached for BTC-USD ONE_HOUR" in result.output
    assert "fetch first" in result.output
    assert not ledger.exists()


def test_reported_observed_drawdown_equals_the_engine_statistic():
    """The equality the CLI comment relies on: montecarlo.max_drawdown over equity_curve of
    the closed trades equals BacktestResult.max_drawdown (keel/strategy/stats.py's loop) for
    the same run -- pinned so the two drawdown loops cannot drift apart silently."""
    rule = build_rule_from_params(
        "turtle_breakout",
        {
            "product_id": "BTC-USD",
            "entry_lookback": 5,
            "exit_lookback": 3,
            "atr_period": 5,
            "atr_stop_mult": "2",
        },
    )
    res = backtest(rule, _mc_candles(96))
    pnls = [t.pnl for t in res.trades if t.pnl is not None and t.outcome != "open"]
    assert max_drawdown(equity_curve(pnls, Decimal("0"))) == res.max_drawdown


def test_monte_carlo_caps_paths_at_2000(tmp_path):
    db = _mc_db(tmp_path)
    result = _invoke_mc(CliRunner(), db, tmp_path / "t.jsonl", "--paths", "2001", "--seed", "1")
    assert result.exit_code != 0
