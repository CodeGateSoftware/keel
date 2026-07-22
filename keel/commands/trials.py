"""`keel trials` -- the append-only experiments ledger (spec §4).

Records *experiments*, never money. This group is deliberately self-contained: it touches
neither the broker, the repository, nor `config.yaml`, operating only on a ledger file path.
That is why it was the first group extracted out of the monolithic `keel/cli.py` -- it shares
none of the network/DB seams the other commands do, so moving it here cannot change any
monkeypatch target the CLI tests rely on.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import click

from keel.research import cscv as cscv_mod
from keel.research import deflate as deflate_mod
from keel.research import ledger as trials_ledger
from keel.research import matrix as matrix_mod


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
