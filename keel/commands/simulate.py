"""The simulation service behind `keel simulate` -- report assembly, top to bottom.

Issue #387 C1 (the TUI-operator-console PRD, O2): the deterministic-replay assembly (candle
loading, the #259 per-product slippage pass, the account-metrics reduction, the Coinbase One
tier/fee matrix, the promotion verdict, the trials-ledger row, the Markdown/HTML report) lived
inline in `keel/cli.py`'s command body, so a second front-end would have had to re-implement it.
It lives here now; the CLI wrapper parses options, lazily builds the broker at its
`_build_broker` seam, and echoes.

Two layers, mirroring `keel/commands/status.py`:

- the compute (`load_sim_candles`, `sim_coverage`, `slippage_assumptions`,
  `build_account_metrics`, `build_tier_results`, `default_report_path`) is pure over the
  `(repo, config)` it is handed;
- `run_simulation` is the whole pass, streaming its progress through an injected `echo`
  (defaulting to a no-op) and returning a `SimulationOutcome` -- verdict, reasons, the report
  path, the artifact path -- so a front-end can render its own progress and still get the
  structured result. `render_sim_coverage`/`render_slippage_assumptions` are the pure
  renderers of the exact lines the CLI prints.

`keel.cli` re-exports `SIM_SLIPPAGE_PCT` as `_SIM_SLIPPAGE_PCT`; the simulate tests pin that
the flat rate is structurally the engine's floor, and re-importing keeps them resolving to this
exact object.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel import agent
from keel.commands.fetch import DAYS_PER_YEAR
from keel.compliance import screen as screen_mod
from keel.config import Config
from keel.data import history as history_mod
from keel.data.repository import Repository
from keel.research import ledger as trials_ledger
from keel.sim import artifact as artifact_mod
from keel.sim import benchmark as benchmark_mod
from keel.sim import metrics as metrics_mod
from keel.sim import portfolio_sim
from keel.sim import report as report_mod
from keel.sim import tiers as tiers_mod
from keel.strategy import backtest as backtest_mod
from keel.strategy import promotion as promotion_mod
from keel.types import Candle, Granularity

# Fallback execution friction for the simulate pass, used only when a rate cannot be read from
# config. `simulate` sources the fee from `config.fees.taker_pct` instead (see `sim_fee_pct`),
# so the edge table, the account pass, the tier matrix and the benchmarks still price fills
# identically -- the property this constant used to provide, now at a rate the deployment
# controls.
#
# It was `Decimal("0.006")` until #247, deliberately matching `strategy/backtest.backtest`'s and
# `sim/portfolio_sim.run`'s own defaults. Those defaults were the MAKER rate under a taker fill
# model, so the consistency was real and the number was wrong -- every simulate report, edge
# table and benchmark comparison was priced at half the cost of trading
# (`docs/experiments/2026-08-11-hourly-backtest-turtle-breakout.md` §5).
SIM_FEE_PCT = backtest_mod.TAKER_FEE_PCT
# Aliased to the engine's floor (#259's cleanup), not repeated: the simulate report asserts
# its flat-priced dollar sections cost "the flat SLIPPAGE_FLOOR_PCT per leg", and that claim
# must be structurally true, not true by numeric coincidence that a retune would silently
# break. (`portfolio_sim` and `paper` still carry their own literals -- see #335.)
SIM_SLIPPAGE_PCT = backtest_mod.SLIPPAGE_FLOOR_PCT

#: The backtest ENGINE's supported timeframes -- an engine limit rather than a data choice
#: (Issue #349), and deliberately NOT `config.market_data.granularities` the way `fetch` uses.
SIM_GRANULARITIES = [Granularity.ONE_HOUR, Granularity.ONE_DAY]


def sim_fee_pct(config: Config) -> Decimal:
    """The rate `simulate` prices every fill at: the deployment's own `fees.taker_pct`.

    Taker because every simulated fill in this project is market-style (`backtest` fills at a
    touched level, `SimAccount` at next-bar open); a deployment on a different volume tier or
    venue moves the rate by editing config, not code.
    """
    return config.fees.taker_pct


def sim_asset(product_id: str) -> str:
    return product_id.split("-")[0]


def load_sim_candles(
    repo: Repository, products: list[str], start_ts: int, end_ts: int
) -> tuple[dict[str, dict[Granularity, list[Candle]]], dict[str, list[tuple[int, Decimal]]]]:
    """Load cached candles for `products` into `(candles_by_asset, prices_by_asset)`.

    `candles_by_asset`/`prices_by_asset` are keyed by bare asset code (`"BTC"`, not
    `"BTC-USD"`) to match `sim.portfolio_sim`/`sim.benchmark`'s convention (rules bind to an
    asset via `Rule.product_id`, `Config.target_weights` is asset-keyed).
    """
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]] = {}
    prices_by_asset: dict[str, list[tuple[int, Decimal]]] = {}
    for product in products:
        asset = sim_asset(product)
        per_tf = {
            gran: repo.get_candles(product, gran, start_ts, end_ts) for gran in SIM_GRANULARITIES
        }
        candles_by_asset[asset] = per_tf
        prices_by_asset[asset] = [(c.ts, c.close) for c in per_tf[Granularity.ONE_DAY]]
    return candles_by_asset, prices_by_asset


def sim_coverage(
    repo: Repository, products: list[str], start_ts: int
) -> dict[tuple[str, Granularity], history_mod.CoverageInfo]:
    """Per-asset cached-candle coverage (no network -- reads whatever's already in the DB)."""
    coverage: dict[tuple[str, Granularity], history_mod.CoverageInfo] = {}
    for product in products:
        asset = sim_asset(product)
        for gran in SIM_GRANULARITIES:
            coverage[(asset, gran)] = history_mod.coverage(repo, product, gran, start_ts)
    return coverage


def render_sim_coverage(
    db_path: str, coverage: dict[tuple[str, Granularity], history_mod.CoverageInfo]
) -> list[str]:
    """The exact coverage block `keel simulate` prints before computing anything."""
    lines = [f"data cached in: {db_path}"]
    for (asset, granularity), info in sorted(
        coverage.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
    ):
        lines.append(
            f"  coverage {asset} {granularity.value}: n_candles={info.n_candles} "
            f"first_ts={info.first_ts} last_ts={info.last_ts} gaps={info.gaps}"
        )
    return lines


def build_account_metrics(
    sim: portfolio_sim.SimResult, start_ts: int, end_ts: int
) -> dict[str, Any]:
    """Reduce `sim.equity_curve`/`sim.contributions`/`sim.trades` into the account-metrics
    dict `report.build_verdict`/`report.render_markdown` consume (see their docstrings for the
    keys each reads)."""
    equity_curve = sim.equity_curve
    contributed = sum((amount for _, amount in sim.contributions), Decimal("0"))
    ending_value = equity_curve[-1][1] if equity_curve else Decimal("0")
    total_return_pct = (
        (ending_value - contributed) / contributed if contributed > 0 else Decimal("0")
    )
    max_dd = metrics_mod.max_drawdown_pct(equity_curve)
    returns = metrics_mod.daily_returns(equity_curve)
    # Money-weighted IRR/CAGR treat contributions as outflows (negative) and the ending
    # portfolio value as the single inflow -- see `sim.metrics.irr`/`cagr_money_weighted`.
    cashflows = [(ts, -amount) for ts, amount in sim.contributions]

    closed_trades = [t for t in sim.trades if t.outcome != "open"]
    per_asset_pnl: dict[str, Decimal] = {}
    for trade in closed_trades:
        per_asset_pnl[trade.asset] = per_asset_pnl.get(trade.asset, Decimal("0")) + (
            trade.pnl or Decimal("0")
        )
    # `exit_ts` is optional on the trade type because an OPEN trade has none; `closed_trades`
    # has already excluded those, so every entry here carries one. Written as a filter rather
    # than left implicit so the average is taken over exactly the trades that can contribute a
    # duration -- matching the defensive `trade.pnl or Decimal("0")` two lines up, and keeping
    # a stray `None` from reaching `Decimal(None - entry_ts)`.
    hold_spans = [
        Decimal(t.exit_ts - t.entry_ts) / Decimal(3600)
        for t in closed_trades
        if t.exit_ts is not None
    ]
    avg_hold_hours = (
        sum(hold_spans, Decimal("0")) / len(hold_spans) if hold_spans else Decimal("0")
    )

    return {
        "contributed": contributed,
        "ending_value": ending_value,
        "net_pnl_usd": ending_value - contributed,
        "total_return_pct": total_return_pct,
        "irr": metrics_mod.irr(cashflows, ending_value),
        "cagr": metrics_mod.cagr_money_weighted(cashflows, ending_value, start_ts, end_ts),
        "max_drawdown_pct": max_dd,
        "return_per_drawdown": metrics_mod.return_per_drawdown(total_return_pct, max_dd),
        "sharpe": metrics_mod.sharpe(returns),
        "sortino": metrics_mod.sortino(returns),
        "trade_count": len(closed_trades),
        "avg_hold_hours": avg_hold_hours,
        "per_asset_pnl": per_asset_pnl,
    }


def build_tier_results(
    config: Config,
    rules: list[Any],
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]],
    sim_natural: portfolio_sim.SimResult,
    natural_metrics: dict[str, Any],
    start_ts: int,
    now_ts: int,
    monthly_contribution: Decimal,
    skip_within_cap: bool,
) -> list[tiers_mod.TierFeeResult]:
    """Assemble the Coinbase One tier/fee analysis matrix (Issue #86) -- one `OVER_CAP` row per
    `config.tiers` entry (always, from `sim_natural`, the already-computed natural/unthrottled
    run) plus one `WITHIN_CAP` row per tier: an unlimited tier (`free_volume_usd is None`, e.g.
    Premium) reuses `sim_natural` (nothing to throttle); a finite-free-volume tier gets its own
    separate throttled `portfolio_sim.run(..., monthly_volume_cap=tier.free_volume_usd)` pass
    UNLESS `skip_within_cap`, in which case that tier's within-cap row is simply omitted (only
    the cheap over-cap overlay, reusing `sim_natural`, is computed).
    """
    natural_n_months = len(sim_natural.contributions)
    natural_gross_pnl = natural_metrics.get("net_pnl_usd", Decimal("0"))

    results: list[tiers_mod.TierFeeResult] = []
    for tier in config.tiers:
        results.append(
            tiers_mod.compute_tier_fee_result(
                monthly_volume=sim_natural.monthly_volume,
                n_months=natural_n_months,
                tier=tier,
                mode=tiers_mod.OVER_CAP,
                taker_pct=config.fees.taker_pct,
                gross_pnl_usd=natural_gross_pnl,
            )
        )

        if tier.free_volume_usd is None:
            # Unlimited allowance -- nothing to throttle; within-cap == over-cap (Premium).
            results.append(
                tiers_mod.compute_tier_fee_result(
                    monthly_volume=sim_natural.monthly_volume,
                    n_months=natural_n_months,
                    tier=tier,
                    mode=tiers_mod.WITHIN_CAP,
                    taker_pct=config.fees.taker_pct,
                    gross_pnl_usd=natural_gross_pnl,
                )
            )
            continue

        if skip_within_cap:
            continue

        within_sim = portfolio_sim.run(
            rules,
            candles_by_asset,
            config,
            start_ts=start_ts,
            end_ts=now_ts,
            monthly_contribution=monthly_contribution,
            fee_pct=sim_fee_pct(config),
            slippage_pct=SIM_SLIPPAGE_PCT,
            monthly_volume_cap=tier.free_volume_usd,
        )
        within_metrics = build_account_metrics(within_sim, start_ts, now_ts)
        results.append(
            tiers_mod.compute_tier_fee_result(
                monthly_volume=within_sim.monthly_volume,
                n_months=len(within_sim.contributions),
                tier=tier,
                mode=tiers_mod.WITHIN_CAP,
                taker_pct=config.fees.taker_pct,
                gross_pnl_usd=within_metrics.get("net_pnl_usd", Decimal("0")),
            )
        )

    return results


def slippage_assumptions(
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]],
    products: list[str],
    rule_products: list[str],
    fallback_pct: Decimal,
) -> tuple[list[backtest_mod.SlippageAssumption], Callable[[str], Decimal]]:
    """Per-product slippage for the edge pass (#259), from candles the run already loaded.

    Scales each product's rate from its OWN liquidity via `backtest.slippage_for_quote_volume`
    (the mapping's parameters and their rationale live there), with the statistic computed by
    the ONE definition (`screen_mod.median_daily_quote_volume`) over the product's cached
    ONE_DAY bars in the sim window -- no new data source, no network. A product with no daily
    bars falls back to the flat rate and is flagged as such, so the report says "fallback (no
    liquidity statistic)" rather than passing the flat 5bp off as a measured verdict.

    Returns the rows to print/report and the resolver `edge_table` prices fills with. The
    resolver is total: any product id it is not holding (a rule bound outside `--products`)
    answers `fallback_pct`, never `KeyError` -- absent data is the fallback case, not a crash.
    """
    by_product: dict[str, Decimal] = {}
    rows: list[backtest_mod.SlippageAssumption] = []
    for product in sorted(set(products) | set(rule_products)):
        asset = sim_asset(product)
        daily = candles_by_asset.get(asset, {}).get(Granularity.ONE_DAY, [])
        if daily:
            median = screen_mod.median_daily_quote_volume(daily)
            pct = backtest_mod.slippage_for_quote_volume(median)
            rows.append(backtest_mod.SlippageAssumption(product, median, pct))
        else:
            pct = fallback_pct
            rows.append(backtest_mod.SlippageAssumption(product, None, pct))
        by_product[product] = pct

    def _resolve(product_id: str) -> Decimal:
        return by_product.get(product_id, fallback_pct)

    return rows, _resolve


def render_slippage_assumptions(rows: list[backtest_mod.SlippageAssumption]) -> list[str]:
    """The exact slippage block `keel simulate` prints -- the terminal twin of the report's
    table: the numbers are assumptions, so they are stated loudly enough that an operator
    comparing two runs cannot miss that they were priced differently.
    """
    lines = [
        "assumed slippage per leg (scaled from median daily quote volume; "
        f"floor {backtest_mod.SLIPPAGE_FLOOR_PCT * 10000:.1f}bp, "
        f"cap {backtest_mod.SLIPPAGE_CAP_PCT * 10000:.1f}bp, anchor "
        f"${backtest_mod.SLIPPAGE_REFERENCE_QUOTE_VOLUME:,.0f}/day -- "
        "an assumption, not a measurement):"
    ]
    for row in rows:
        if row.fallback:
            lines.append(
                f"  {row.product_id}: {row.slippage_pct * 10000:.1f}bp "
                "(fallback: no liquidity statistic)"
            )
        else:
            note = " (capped)" if row.capped else ""
            lines.append(
                f"  {row.product_id}: median volume {row.median_daily_quote_volume:,.0f} "
                f"-> {row.slippage_pct * 10000:.1f}bp{note}"
            )
    return lines


def default_report_path(now_ts: int) -> Path:
    date_str = datetime.fromtimestamp(now_ts, tz=UTC).strftime("%Y-%m-%d")
    return Path("docs/superpowers/reports") / f"{date_str}-engine-validation.md"


@dataclass(frozen=True)
class SimulationOutcome:
    """What `run_simulation` produced, minus the printing."""

    verdict_status: str
    verdict_reasons: tuple[str, ...]
    #: The Markdown report actually written.
    report_path: Path
    report_markdown: str
    #: The HTML artifact, when `artifact=True`; else `None`.
    artifact_path: Path | None


def run_simulation(
    repo: Repository,
    config: Config,
    build_client: Callable[[], Any] | None,
    *,
    db_path: str,
    products: list[str],
    years: int,
    monthly_contribution: Decimal,
    now_ts: int,
    out_path: Path | None = None,
    artifact: bool = False,
    refresh: bool = False,
    trial_decision: str = "diagnostic_only",
    trial_provenance: str = "a_priori",
    no_trial_record: bool = False,
    skip_within_cap: bool = False,
    echo: Callable[[str], None] = lambda _message: None,
) -> SimulationOutcome:
    """THE simulate pass: replay the real rule set over cached (or freshly pulled) candles,
    compare it to a DCA benchmark, write the GO-LIVE/TRAIN-MORE report.

    READ-ONLY with respect to money: no orders, no rails, no confirmation gate.

    `build_client` is a lazy factory rather than a client argument because construction order
    is behavior: under `--no-fetch` (`build_client=None`) no broker is ever constructed and the
    pass runs over whatever is already cached in the DB. Otherwise the client is built right
    where the old command body built it -- after option parsing, before the coverage read.

    Also computes a Coinbase One subscription-tier/fee analysis (Issue #86): for each
    configured tier (`config.tiers`), whether staying WITHIN its fee-free monthly
    trading-volume allowance (a separate, throttled sim run per finite-free-volume tier) or
    trading freely and paying the taker fee on volume EXCEEDING it ("over cap") nets out
    ahead. This means up to 3 total sim passes (natural + one throttled run per
    finite-free-volume tier) unless `skip_within_cap`.
    """
    months = years * 12
    start_ts = now_ts - years * DAYS_PER_YEAR * 86400

    if build_client is not None:
        client = build_client()
        history_mod.ensure_history(
            client,
            repo,
            products,
            SIM_GRANULARITIES,
            years,
            now_ts,
            sleep_fn=time.sleep,
            refresh=refresh,
        )

    coverage = sim_coverage(repo, products, start_ts)
    for line in render_sim_coverage(db_path, coverage):
        echo(line)

    candles_by_asset, prices_by_asset = load_sim_candles(repo, products, start_ts, now_ts)
    rules = [agent._build_rule(row) for row in repo.get_rules()]

    # One rate for the whole pass -- edge table, account sim, and both benchmarks -- so the
    # report's comparisons stay like-for-like. Sourced from config, and reported in the header
    # below rather than left for the reader to assume.
    fee_pct = sim_fee_pct(config)

    # #259: the EDGE pass prices each product's fills from its own liquidity (the flat
    # `SIM_SLIPPAGE_PCT` remains the fallback for products without a statistic). The account
    # pass and benchmarks below stay flat -- `SimAccount`'s cost model is unchanged by #259 --
    # and the report says so beside the numbers (see `report._render_slippage_rows`).
    slippage_rows, slippage_by_product = slippage_assumptions(
        candles_by_asset, products, [rule.product_id for rule in rules], SIM_SLIPPAGE_PCT
    )
    for line in render_slippage_assumptions(slippage_rows):
        echo(line)

    edge = report_mod.edge_table(
        rules,
        candles_by_asset,
        fee_pct=fee_pct,
        slippage_pct=SIM_SLIPPAGE_PCT,
        slippage_by_product=slippage_by_product,
    )

    sim = portfolio_sim.run(
        rules,
        candles_by_asset,
        config,
        start_ts=start_ts,
        end_ts=now_ts,
        monthly_contribution=monthly_contribution,
        fee_pct=fee_pct,
        slippage_pct=SIM_SLIPPAGE_PCT,
    )
    sim.coverage = coverage

    account_metrics = build_account_metrics(sim, start_ts, now_ts)

    benchmark = benchmark_mod.dca_into_allowlist(
        prices_by_asset,
        config.target_weights,
        monthly_contribution,
        months,
        fee_pct,
        SIM_SLIPPAGE_PCT,
    )
    # Secondary benchmark (spec: DCA-into-BTC); not fed into the verdict gate, but computed so
    # a future report revision can surface it without another sim pass.
    benchmark_mod.dca_into_btc(
        prices_by_asset, monthly_contribution, months, fee_pct, SIM_SLIPPAGE_PCT
    )

    promo_cfg = promotion_mod.PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )
    # G2 is checked per rule class (KB §25.5): each class's pooled edge sample is judged
    # against its own floor, so a low-win/high-R:R trend-follower isn't rejected by the global
    # 55%-win floor `promo_cfg` carries. Classes without a fixed floor fall back to `promo_cfg`.
    pooled_by_class = report_mod.group_trades_by_class(edge, rules)
    floors = {
        cls: promotion_mod.floor_for_class(cls, promo_cfg) for cls in pooled_by_class
    }
    verdict = report_mod.build_verdict(
        edge[report_mod.POOLED_KEY],
        account_metrics,
        benchmark,
        sim.coverage,
        promo_cfg,
        pooled_by_class=pooled_by_class,
        floors=floors,
    )
    gaps = report_mod.analyze_gaps(
        sim.telemetry, sim.coverage, move_threshold_pct=portfolio_sim.MOVE_THRESHOLD_PCT
    )

    tier_results = build_tier_results(
        config,
        rules,
        candles_by_asset,
        sim,
        account_metrics,
        start_ts,
        now_ts,
        monthly_contribution,
        skip_within_cap,
    )

    if not no_trial_record:
        # One ledger row per simulate run: the run IS one configuration of the whole rule set,
        # and its account equity curve is that configuration's per-bar P&L column. Deposits are
        # stripped by `bar_pnl` -- new capital is not profit (spec §4.5).
        series = metrics_mod.bar_pnl(sim.equity_curve, sim.contributions)
        trials_ledger.append_trial(
            trials_ledger.DEFAULT_LEDGER_PATH,
            trial_id=f"simulate-{now_ts}",
            session="keel simulate",
            rule=",".join(sorted({rule.name for rule in rules})) or "none",
            params={
                "products": products,
                "years": years,
                "monthly_contribution": str(monthly_contribution),
                "rules": [rule.describe() for rule in rules],
            },
            provenance=trial_provenance,
            kind="sweep_node",
            decision=trial_decision,
            per_bar_pnl=series,
            series_missing=not series,
            summary={"trade_count": len(sim.trades)},
        )

    md = report_mod.render_markdown(
        sim,
        edge,
        account_metrics,
        benchmark,
        verdict,
        gaps,
        in_sample=True,
        tier_results=tier_results,
        fee_pct=fee_pct,
        slippage_rows=slippage_rows,
    )

    out = out_path if out_path is not None else default_report_path(now_ts)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)

    echo(f"verdict: {verdict.status}")
    echo(f"report written to {out}")
    if verdict.reasons:
        echo("failing gates: " + "; ".join(verdict.reasons))

    artifact_path: Path | None = None
    if artifact:
        html = artifact_mod.render_html(
            sim, benchmark, verdict, gaps, account_metrics, in_sample=True
        )
        artifact_path = out.with_suffix(".html")
        artifact_path.write_text(html)
        echo(f"artifact written to {artifact_path}")

    return SimulationOutcome(
        verdict_status=verdict.status,
        verdict_reasons=tuple(verdict.reasons),
        report_path=out,
        report_markdown=md,
        artifact_path=artifact_path,
    )
