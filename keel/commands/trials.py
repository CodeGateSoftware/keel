"""`keel trials` -- the append-only experiments ledger (spec §4).

Records *experiments*, never money. This group is deliberately self-contained: it touches
neither the broker, the repository, nor `config.yaml`, operating only on a ledger file path.
That is why it was the first group extracted out of the monolithic `keel/cli.py` -- it shares
none of the network/DB seams the other commands do, so moving it here cannot change any
monkeypatch target the CLI tests rely on.

TWO exceptions, both measurement over backtests rather than ledger-file operations:
`trials monte-carlo` (#441) resamples one observed run, and `trials walk-forward` (#445)
validates one given rule across rolling-origin folds. Each reads the DB (and, when one
loads, `config.yaml`) to rebuild a stored rule and its candles, writes only to the ledger
-- never to the db -- and resolves the rule through the exact `rules backtest` seam
(`keel.commands.rules.resolve_rule_backtest`) so the observed run, and every fold, is the
run an operator can reproduce by hand.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import click

from keel.commands import rules as rules_mod
from keel.commands._common import _open_repo
from keel.data.history import GRANULARITY_SECONDS
from keel.data.repository import Repository
from keel.research import cscv as cscv_mod
from keel.research import deflate as deflate_mod
from keel.research import ledger as trials_ledger
from keel.research import matrix as matrix_mod
from keel.research import montecarlo as mc_mod
from keel.research import walkforward as wf_mod
from keel.strategy import backtest as backtest_mod


@click.group("trials")
def trials_group() -> None:
    """Append-only trials ledger (spec §4). Records experiments, never money."""


_LEDGER_OPTION = click.option(
    "--ledger",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Ledger path (default: docs/experiments/trials-ledger.jsonl).",
)


def _ledger_path(ledger: Path | None) -> Path:
    return ledger if ledger is not None else trials_ledger.DEFAULT_LEDGER_PATH


@trials_group.command("record")
@_LEDGER_OPTION
@click.option("--trial-id", required=True)
@click.option("--session", required=True, help="Free-text experiment/session label.")
@click.option("--rule", required=True)
@click.option("--params", default="{}", help="JSON object of the full parameter dict.")
@click.option("--provenance", required=True, type=click.Choice(sorted(trials_ledger.PROVENANCE)))
@click.option("--kind", required=True, type=click.Choice(sorted(trials_ledger.KINDS)))
@click.option("--decision", required=True, type=click.Choice(sorted(trials_ledger.DECISIONS)))
@click.option("--series-missing", is_flag=True, default=False)
@click.option("--per-bar-pnl", default=None, help="JSON array of per-bar P&L.")
@click.option("--per-trade-pnl", default=None, help="JSON array of per-trade P&L.")
def trials_record(
    ledger: Path | None,
    trial_id: str,
    session: str,
    rule: str,
    params: str,
    provenance: str,
    kind: str,
    decision: str,
    series_missing: bool,
    per_bar_pnl: str | None,
    per_trade_pnl: str | None,
) -> None:
    """Record one trial -- the path scratchpad experiments use (spec §4.5)."""

    def _series(raw: str | None) -> list[Decimal]:
        return [Decimal(str(v)) for v in json.loads(raw)] if raw else []

    try:
        record = trials_ledger.append_trial(
            _ledger_path(ledger),
            trial_id=trial_id,
            session=session,
            rule=rule,
            params=json.loads(params),
            provenance=provenance,
            kind=kind,
            decision=decision,
            per_trade_pnl=_series(per_trade_pnl),
            per_bar_pnl=_series(per_bar_pnl),
            series_missing=series_missing,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"recorded {record.trial_id} ({record.decision}) hash={record.row_hash[:12]}")


@trials_group.command("list")
@_LEDGER_OPTION
def trials_list(ledger: Path | None) -> None:
    """List recorded trials and the two N accountings (spec §4.4)."""
    trials = trials_ledger.read_trials(_ledger_path(ledger))
    for index, record in enumerate(trials, start=1):
        flag = " [series_missing]" if record.series_missing else ""
        click.echo(
            f"{index:>4}  {record.trial_id:<34} {record.rule:<18} "
            f"{record.provenance:<9} {record.kind:<16} {record.decision}{flag}"
        )
    m, n_decisions = trials_ledger.trial_counts(trials)
    click.echo(f"\nM={m}  N_decisions={n_decisions}")


@trials_group.command("verify")
@_LEDGER_OPTION
def trials_verify(ledger: Path | None) -> None:
    """Verify the hash chain. Exits non-zero if broken."""
    errors = trials_ledger.verify_chain(_ledger_path(ledger))
    if not errors:
        click.echo("chain intact")
        return
    for error in errors:
        click.echo(error, err=True)
    raise click.ClickException(f"{len(errors)} chain error(s)")


@trials_group.command("deflate")
@_LEDGER_OPTION
@click.option("--sharpe", required=True, type=float, help="Observed ANNUALISED Sharpe.")
@click.option(
    "--trades-per-year", default=6.0, show_default=True, type=float,
    help="Realised trade frequency, used to express MinBTL in trades.",
)
@click.option(
    "--rho", default=None, type=float,
    help="Assumed correlation between trials (§78.2). Omit to report an assumption BAND.",
)
@click.option("--skew", default=0.0, show_default=True, type=float)
@click.option("--kurtosis", default=3.0, show_default=True, type=float, help="Non-excess.")
@click.option(
    "--trial-sharpe-variance", default=None, type=float,
    help="V[{SR_n}] across trials. Omit if the ledger cannot supply it -- DSR is then skipped "
    "rather than computed from a guess.",
)
def trials_deflate(
    ledger: Path | None,
    sharpe: float,
    trades_per_year: float,
    rho: float | None,
    skew: float,
    kurtosis: float,
    trial_sharpe_variance: float | None,
) -> None:
    """Expected-max Sharpe, MinBTL and (where computable) DSR, from the ledger's trial counts.

    ⛔ REPORTING ONLY (§78.7's Strathern rail). Every input is itemised, and anything the ledger
    cannot supply is reported as MISSING rather than filled in with a plausible default.
    """
    trials = trials_ledger.read_trials(_ledger_path(ledger))
    m_total, n_decisions = trials_ledger.trial_counts(trials)
    if n_decisions < 2:
        raise click.ClickException(f"only {n_decisions} decision trials -- need >= 2")

    click.echo("inputs")
    click.echo(f"  M (all ledger rows)      : {m_total}")
    click.echo(f"  N decisions (excl. diag) : {n_decisions}")
    click.echo(f"  observed annualised SR   : {sharpe}")
    click.echo(f"  trades/year              : {trades_per_year}")
    click.echo(f"  skew / kurtosis          : {skew} / {kurtosis}")

    bands = [rho] if rho is not None else [0.0, 0.5, 0.9]
    click.echo("\nMinBTL by assumed trial correlation (§78.2 N̂ = ρ̂ + (1−ρ̂)·M)")
    click.echo(f"  {'rho':>5} {'N_hat':>8} {'E[max]':>8} {'MinBTL yr':>10} {'MinBTL trades':>14}")
    for assumed in bands:
        n_hat = deflate_mod.implied_independent_trials(assumed, n_decisions)
        effective = max(2, int(round(n_hat)))
        emax = deflate_mod.expected_max_sharpe(effective)
        years = deflate_mod.min_backtest_length_years(effective, sharpe)
        trades = deflate_mod.min_trades(effective, sharpe, trades_per_year)
        click.echo(
            f"  {assumed:>5.2f} {n_hat:>8.1f} {emax:>8.3f} {years:>10.1f} {trades:>14.0f}"
        )

    if trial_sharpe_variance is None:
        click.echo(
            "\nDSR: NOT COMPUTED. V[{SR_n}] requires a per-trial Sharpe on every ledger row, "
            "and the backfilled rows are series_missing (§78.4). Supply "
            "--trial-sharpe-variance to compute it under an explicit assumption."
        )
        return

    n_hat = deflate_mod.implied_independent_trials(rho if rho is not None else 0.0, n_decisions)
    effective = max(2, int(round(n_hat)))
    sr0 = deflate_mod.sharpe_rejection_threshold(effective, trial_sharpe_variance)
    observations = int(round(trades_per_year * deflate_mod.min_backtest_length_years(
        effective, sharpe
    ))) if sharpe > 0 else 0
    dsr = deflate_mod.deflated_sharpe(sharpe, sr0, max(2, observations), skew, kurtosis)
    click.echo(f"\nSR_0 (rejection bar)      : {sr0:.4f}")
    click.echo(f"DSR                       : {dsr:.4f}")


@trials_group.command("pbo")
@_LEDGER_OPTION
@click.option("--session", default=None, help="Only use columns from this session label.")
@click.option("--blocks", default=16, show_default=True, help="S: number of row blocks.")
def trials_pbo(ledger: Path | None, session: str | None, blocks: int) -> None:
    """Probability of Backtest Overfitting over a declared candidate grid (§78.6).

    Reports probabilities. It deliberately does NOT report which configuration won: PBO
    evaluates the quality of a selection process and must never become the objective that
    selection relies on (§78.7's Strathern warning).
    """
    trials = trials_ledger.read_trials(_ledger_path(ledger))
    build = matrix_mod.build_matrix(trials, session=session)
    if not build.columns:
        raise click.ClickException("no usable columns (all trials are series_missing?)")
    for warning in build.warnings:
        click.echo(f"warning: {warning}", err=True)
    if build.refused:
        click.echo(f"refused {len(build.refused)} series_missing trial(s)", err=True)

    result = cscv_mod.pbo(build.columns, s=blocks)

    click.echo(f"columns (N)          : {result.n_columns}")
    click.echo(f"blocks (S)           : {result.n_blocks}")
    click.echo(f"combinations         : {result.n_combinations}")
    click.echo(f"rows used / dropped  : {result.rows_used} / {result.rows_dropped}")
    click.echo(f"PBO                  : {result.pbo:.4f}")
    click.echo(f"degradation slope    : {result.degradation_slope:.4f}")
    click.echo(f"Prob[OOS < 0]        : {result.prob_loss:.4f}")
    click.echo(f"stochastic dominance : 1st={result.dominance_1st} 2nd={result.dominance_2nd}")
    click.echo(
        "\nRead PBO alongside the degradation slope, never alone (§78.7 limitation 4): a high "
        "PBO with a flat, positive OOS scatter is the GOOD outcome -- a broad plateau of "
        "near-identical configurations produces high PBO by construction."
    )


# -- monte-carlo resampling (#441) ----------------------------------------------------------------
#
# The first `trials` subcommand that touches the db (see the module docstring). Everything it
# does is measurement: resolve the stored rule exactly as `rules backtest` does, run the
# observed backtest once, resample (trade reshuffle or moving-block candle bootstrap), then
# print the report and append ONE diagnostic_only ledger row. It never writes to the db, never
# changes a rule's status, and never feeds any gate -- the percentile is evidence about path
# luck, and "measurement, not gating" is the whole point of the module it fronts.

#: The equity baseline resampled finals are read against: cumulative P&L from zero, matching
#: the backtest engine's fixed 1-unit notional (there is no account balance to compound).
_MC_START = Decimal(0)


def _closed_pnls(result: backtest_mod.BacktestResult) -> list[Decimal]:
    """The observed run's realised per-trade P&L (fee and slippage already inside it).

    Open trades carry no pnl and no outcome yet -- excluding them is not a choice, it is the
    only well-defined reading. A closed trade without a pnl would be a data error and is
    named rather than coerced (same invariant `stats.summarize` states).
    """
    pnls: list[Decimal] = []
    for trade in result.trades:
        if trade.outcome == "open":
            continue
        if trade.pnl is None:
            raise click.ClickException(
                f"a {trade.outcome!r} trade reached the resample without a pnl -- only an "
                "open trade may omit realised P&L"
            )
        pnls.append(trade.pnl)
    return pnls


def _resolve_with_granularity_fallback(
    repo: Repository, config: Any | None, rule_id: int, granularity_opt: str | None
) -> rules_mod.ResolvedBacktest:
    """`resolve_rule_backtest` with the #441 granularity default chain: an explicit
    `--granularity` wins, then the rule's own, and a rule that declares neither falls back to
    ONE_HOUR (the engine's own trading-timeframe default) instead of refusing -- resampling a
    series the rule itself reads hourly is the null that matches how it trades."""
    try:
        return rules_mod.resolve_rule_backtest(
            repo, config, rule_id, granularity_opt=granularity_opt
        )
    except rules_mod.RulesRefused as exc:
        if granularity_opt is None and "no granularity" in str(exc):
            return rules_mod.resolve_rule_backtest(
                repo, config, rule_id, granularity_opt="ONE_HOUR"
            )
        raise


@trials_group.command("monte-carlo")
@_LEDGER_OPTION
@click.option("--rule", required=True, type=int, help="Stored rule id to resample.")
@click.option(
    "--mode",
    type=click.Choice(["trades", "candles"]),
    default="trades",
    show_default=True,
    help="trades: same trades reshuffled (ordering luck). candles: moving-block bootstrap of "
    "the rule's own candles, re-backtested per path (the null that keeps local structure).",
)
@click.option(
    "--paths",
    type=click.IntRange(1, 2000),
    default=200,
    show_default=True,
    help="Number of resampled paths (capped: each candles-mode path is a full backtest).",
)
@click.option(
    "--seed",
    required=True,
    type=int,
    help="Seed for the resampling RNG -- required, because determinism is the point.",
)
@click.option(
    "--block-len",
    type=click.IntRange(min=1),
    default=24,
    show_default=True,
    help="Moving-block length in bars (candles mode only).",
)
@click.option(
    "--granularity",
    default=None,
    help="Candle granularity (default: the rule's own, else ONE_HOUR).",
)
@click.option(
    "--session",
    default="monte-carlo",
    show_default=True,
    help="Ledger session label for the diagnostic row.",
)
@click.pass_context
def trials_monte_carlo(
    ctx: click.Context,
    ledger: Path | None,
    rule: int,
    mode: str,
    paths: int,
    seed: int,
    block_len: int,
    granularity: str | None,
    session: str,
) -> None:
    """Is this rule's equity curve an outlier under resampling? (#441)

    Measurement, not gating: the percentile is evidence about path luck and nothing else.
    """
    repo = _open_repo(ctx)
    config = rules_mod._optional_cfg(ctx)
    try:
        resolved = _resolve_with_granularity_fallback(repo, config, rule, granularity)
    except rules_mod.RulesRefused as exc:
        raise click.ClickException(str(exc)) from exc

    # The observed run: the exact `rules backtest` execution seam, at the fee the resolution
    # derived, so the number below is the number an operator can reproduce by hand.
    observed = rules_mod.backtest_resolved(resolved)
    pnls = _closed_pnls(observed)

    if mode == "trades":
        if not pnls:
            raise click.ClickException(
                "no closed trades in the observed backtest -- nothing to reshuffle"
            )
        resampled = mc_mod.reshuffle(pnls, paths, seed)
    else:
        if not resolved.candles:
            raise click.ClickException(
                f"no candles cached for {resolved.rule.product_id} "
                f"{resolved.granularity.value} -- fetch first"
            )
        candle_paths = mc_mod.moving_block_bootstrap(
            resolved.candles,
            block_len=block_len,
            n_paths=paths,
            seed=seed,
            step_sec=GRANULARITY_SECONDS[resolved.granularity],
        )
        resampled = []
        for candle_path in candle_paths:
            # Same fee as the observed run, so a percentile difference is path luck, not a
            # pricing difference between the observed and resampled worlds.
            replay = backtest_mod.backtest(resolved.rule, candle_path, fee_pct=resolved.fee_pct)
            resampled.append(_closed_pnls(replay))

    # Both statistics read the SAME construction on both sides: each path's additive curve
    # from `_MC_START`, its final point and its `max_drawdown`. The observed drawdown is
    # computed here from the same helpers over the same closed-trade P&L the resamples read
    # (numerically equal to `observed.max_drawdown` -- `stats.summarize` runs the same
    # running-peak loop over the same closed trades from the same zero base) rather than
    # lifted from the BacktestResult, so apples-to-apples is by construction, not by claim.
    observed_curve = mc_mod.equity_curve(pnls, _MC_START)
    curves = [mc_mod.equity_curve(path, _MC_START) for path in resampled]
    finals = [curve[-1] for curve in curves]
    drawdowns = [mc_mod.max_drawdown(curve) for curve in curves]

    observed_final = observed_curve[-1]
    observed_drawdown = mc_mod.max_drawdown(observed_curve)
    report = mc_mod.MonteCarloReport(
        mode=mode,
        n_paths=paths,
        seed=seed,
        start=_MC_START,
        n_trades=len(pnls),
        observed_final=observed_final,
        distribution_min=min(finals),
        distribution_median=mc_mod.median(finals),
        distribution_max=max(finals),
        percentile=mc_mod.percentile_of(observed_final, finals),
        observed_drawdown=observed_drawdown,
        drawdown_min=min(drawdowns),
        drawdown_median=mc_mod.median(drawdowns),
        drawdown_max=max(drawdowns),
        drawdown_percentile=mc_mod.percentile_of(observed_drawdown, drawdowns),
        block_len=block_len if mode == "candles" else None,
    )
    for line in report.render_lines():
        click.echo(line)
    fee_line = rules_mod._describe_fee(resolved.fee_pct, resolved.fee_source)
    click.echo(f"  backtest priced at {fee_line}")

    # The id carries every knob two runs can differ by -- two resamples that differ only in
    # --paths (or --block-len) are different experiments and must never collide on one row.
    trial_id = f"mc-{rule}-{mode}-seed{seed}-p{paths}"
    if mode == "candles":
        trial_id += f"-b{block_len}"
    try:
        record = trials_ledger.append_trial(
            _ledger_path(ledger),
            trial_id=trial_id,
            session=session,
            rule=resolved.row["kind"],
            params={
                "mode": mode,
                "paths": paths,
                "seed": seed,
                "block_len": block_len if mode == "candles" else None,
                "granularity": resolved.granularity.value,
                "fee_pct": str(resolved.fee_pct),
            },
            provenance="a_priori",
            kind="monte_carlo",
            decision="diagnostic_only",
            per_trade_pnl=pnls,
            summary={
                "observed_final": observed_final,
                "distribution_min": report.distribution_min,
                "distribution_median": report.distribution_median,
                "distribution_max": report.distribution_max,
                "percentile": report.percentile,
                "observed_drawdown": observed_drawdown,
                "drawdown_min": report.drawdown_min,
                "drawdown_median": report.drawdown_median,
                "drawdown_max": report.drawdown_max,
                "drawdown_percentile": report.drawdown_percentile,
                "n_trades": len(pnls),
                "n_paths": paths,
            },
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"recorded {record.trial_id} (diagnostic_only) hash={record.row_hash[:12]}")


# -- walk-forward validation (#445) ----------------------------------------------------------------
#
# The second `trials` subcommand that touches the db (see the module docstring), and unlike
# the resampling one it is DETERMINISTIC end to end: there is no --seed option because
# nothing samples. Everything it does is measurement of a GIVEN rule: resolve the stored
# rule exactly as `rules backtest` does (through the same granularity default chain as
# `trials monte-carlo`), split its candles into rolling-origin folds, run the engine's
# backtest per fold, print the stability report, append ONE diagnostic_only ledger row
# PER FOLD. It never writes to the db, never changes a rule's status, and never feeds any
# gate -- and it must never name a fold, window or parameter set to favour, which is the
# whole design of `keel.research.walkforward` (the Strathern rail, spec §6).


@trials_group.command("walk-forward")
@_LEDGER_OPTION
@click.option("--rule", required=True, type=int, help="Stored rule id to validate.")
@click.option(
    "--train-bars",
    required=True,
    type=click.IntRange(min=1),
    help="Train window length in bars.",
)
@click.option(
    "--test-bars",
    required=True,
    type=click.IntRange(min=1),
    help="Test window length in bars.",
)
@click.option(
    "--step-bars",
    default=None,
    type=click.IntRange(min=1),
    help="Advance of both windows per fold (default: --test-bars, non-overlapping tests).",
)
@click.option(
    "--granularity",
    default=None,
    help="Candle granularity (default: the rule's own, else ONE_HOUR).",
)
@click.option(
    "--session",
    default="walk-forward",
    show_default=True,
    help="Ledger session label for the per-fold rows.",
)
@click.pass_context
def trials_walk_forward(
    ctx: click.Context,
    ledger: Path | None,
    rule: int,
    train_bars: int,
    test_bars: int,
    step_bars: int | None,
    granularity: str | None,
    session: str,
) -> None:
    """Validate ONE stored rule across rolling-origin folds (#445).

    Stability, not selection: this reports per-fold out-of-sample metrics and a
    degradation trend for the GIVEN rule. There is no seed option (nothing samples)
    and no fold, window or parameter set is ever named as preferable -- a
    walk-forward that reported a winning window would reintroduce the ranking the
    Strathern rail forbids.
    """
    repo = _open_repo(ctx)
    config = rules_mod._optional_cfg(ctx)
    try:
        resolved = _resolve_with_granularity_fallback(repo, config, rule, granularity)
    except rules_mod.RulesRefused as exc:
        raise click.ClickException(str(exc)) from exc

    if not resolved.candles:
        raise click.ClickException(
            f"no candles cached for {resolved.rule.product_id} "
            f"{resolved.granularity.value} -- fetch first"
        )

    try:
        folds_bounds = wf_mod.folds(
            len(resolved.candles),
            train_bars=train_bars,
            test_bars=test_bars,
            step_bars=step_bars,
        )
        report = wf_mod.walk_forward(
            resolved.rule,
            resolved.candles,
            folds_bounds=folds_bounds,
            fee_pct=resolved.fee_pct,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    for line in wf_mod.render_lines(report):
        click.echo(line)
    click.echo()
    click.echo(
        f"  folds priced at {rules_mod._describe_fee(resolved.fee_pct, resolved.fee_source)}"
    )

    # ONE ledger row PER FOLD (issue #445), each carrying its own bounds in params and its
    # own TEST per-trade P&L, so the run is auditable fold by fold -- and no summary row:
    # a summary row would be one more place a reader might look for a scoreboard.
    effective_step = step_bars if step_bars is not None else test_bars
    for fold in report.fold_metrics:
        try:
            record = trials_ledger.append_trial(
                _ledger_path(ledger),
                trial_id=(
                    f"wf-{rule}-tr{train_bars}-te{test_bars}-st{effective_step}-f{fold.fold_index}"
                ),
                session=session,
                rule=resolved.row["kind"],
                params={
                    "train_bars": train_bars,
                    "test_bars": test_bars,
                    "step_bars": effective_step,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                    "granularity": resolved.granularity.value,
                    "fee_pct": str(resolved.fee_pct),
                    "rule_params": resolved.row.get("params") or {},
                },
                provenance="a_priori",
                kind="walk_forward",
                decision="diagnostic_only",
                # A fold whose test window closed zero trades has an empty series by
                # construction; the schema's word for "no P&L series on this row" is
                # series_missing, so the fold stays ledgered (visible in M) instead of
                # being silently dropped.
                per_trade_pnl=list(fold.test_trade_pnl),
                series_missing=not fold.test_trade_pnl,
                # Never a JSON null in a summary (#445): the ledger reader Decimals every
                # non-int summary value, and Decimal(None) raises -- ONE null row (a
                # single-fold run's not-computable degradation) would make every later
                # read of this append-only ledger crash forever. Not-computable values
                # (degradation with fewer than two measuring folds, a median with no
                # measuring fold) are OMITTED here, never nulled; the reader additionally
                # passes any null through untouched so a hypothetical bad row degrades
                # gracefully instead of bricking the chain.
                summary={
                    key: value
                    for key, value in {
                        "n_folds": report.n_folds,
                        "n_folds_test_positive": report.n_folds_test_positive,
                        "median_test_expectancy": report.median_test_expectancy,
                        "degradation": report.degradation,
                        "train_n_trades": fold.train_n_trades,
                        "train_expectancy": fold.train_expectancy,
                        "test_n_trades": fold.test_n_trades,
                        "test_expectancy": fold.test_expectancy,
                        "test_win_rate": fold.test_win_rate,
                        "test_max_drawdown": fold.test_max_drawdown,
                    }.items()
                    if value is not None
                },
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"recorded {record.trial_id} (diagnostic_only) hash={record.row_hash[:12]}")
