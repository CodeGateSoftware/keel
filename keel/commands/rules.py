"""`keel rules` -- the rule lifecycle (candidate -> paper -> live -> disabled).

Read-only against the exchange: every command here backtests against locally-cached candles or
mutates the `rules` table, with no network call and no broker. It needs the DB/config seams from
`keel.commands._common` and the shared product derivation from `keel.commands._products`, but
never the broker seam.

Two commands WRITE rows: `seed` (one per (kind, product) from each kind's constructor defaults)
and `add` (one from operator-supplied params). Both write at `candidate` by default and both
validate `--product(s)` against rails 18/19 before writing anything; only `seed` can be told to
write another status, and only for the supervised live-order test. `add` cannot, deliberately --
see its docstring.

Two layers, the house split since issue #390 C4 (the TUI-operator-console PRD, O2): the
validation/write logic lives in SERVICE functions (`add_rule_row`, `run_rule_backtest` --
itself `resolve_rule_backtest` + `backtest_resolved`, the backtest compute core the
strategy console's per-rule verdict also delegates to -- `attempt_promotion`,
`apply_rule_enable`/`disable`/`demote`, `describe_params`) that take a
repo, a config and values and echo their operator-facing lines through injected sinks; the
click commands above them parse options and dispatch. The strategy console
(`keel.commands.strategy_console`) is the second front-end over the same services -- one
implementation, never a TUI re-derivation.
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, get_args, get_origin, get_type_hints

import click
from keel_core.config import ConfigError
from keel_core.telemetry import log_event

from keel import agent
from keel.analysis import indicators
from keel.commands._common import _load_cfg, _open_repo, with_disclaimer
from keel.commands._products import parse_products_option
from keel.data.history import GRANULARITY_SECONDS
from keel.data.repository import Repository

# Rail 1's OWN key function, imported rather than re-derived: `rules add`'s allowlist note is a
# preview of what rail 1 will say about the product, and a note that disagreed with the rail it
# is quoting would be worse than no note at all.
from keel.execution.guards import _asset as _asset_of
from keel.research import bias as bias_mod
from keel.research import cscv as cscv_mod
from keel.research import ledger as trials_ledger
from keel.research import matrix as matrix_mod
from keel.strategy import backtest as backtest_mod
from keel.strategy import promotion as promotion_mod
from keel.types import Candle, Granularity

logger = logging.getLogger(__name__)

# `rules demote` steps a rule back one lifecycle stage; `disabled` is terminal (see
# `strategy.promotion`'s own `_PROMOTE_NEXT` docstring) and so is not a demote target.
_DEMOTE_PREV: dict[str, str] = {"live": "paper", "paper": "candidate"}


# -- the service layer (issue #390 C4 / PRD O2) ---------------------------------------------------
#
# The rule lifecycle's VALIDATION and WRITE logic, callable with a repo, a config and
# values -- the same seam the CLI commands dispatch through since this slice and the
# strategy console (`keel.commands.strategy_console`) dispatches to as its second
# front-end. Every function echoes its operator-facing lines through an injected `echo`
# (stdout) / `echo_err` (stderr) pair so the CLI passes `click.echo`/`click.echo(err=True)`
# and the console passes collectors -- one implementation, two renderings, byte-identical
# order. Two refusal shapes, matching the two the CLI always had:
#
# * `RulesUsageError` -- a usage error the CLI renders as `click.BadParameter` with the
#   same message and `param_hint` (exit 2, click's usage banner);
# * `RulesRefused` -- a refusal whose `Error: ...` lines have ALREADY been echoed to
#   `echo_err` when it raises; the CLI renders `ctx.exit(1)`, the console renders the
#   collected lines as its result.


class RulesUsageError(ValueError):
    """A `rules` usage error (a malformed product id, a params/product disagreement): the
    CLI turns this into `click.BadParameter(str(exc), param_hint=exc.param_hint)`, so the
    message and hint are exactly the ones the command always printed."""

    def __init__(self, message: str, param_hint: str | None = None) -> None:
        super().__init__(message)
        self.param_hint = param_hint


class RulesRefused(RuntimeError):
    """A command refusal: the `Error: ...` lines were already echoed through `echo_err`
    before this raised, and nothing was written. The front-end exits non-zero / renders
    the refusal -- it never re-prints the message itself."""


@dataclass(frozen=True)
class RulesOutcome:
    """What a rules-service call did, minus the printing: the stdout lines it echoed (in
    order), the rule row it wrote (if any), and the status that row now holds."""

    lines: tuple[str, ...] = ()
    rule_id: int | None = None
    new_status: str | None = None


def _noop(message: str) -> None:
    """The default echo: silent (a caller that wants the lines passes its own)."""
    del message


def _line_sink(echo: Callable[[str], None]) -> tuple[Callable[[str], None], list[str]]:
    """An echo wrapper that RECORDS every line it echoes -- how a service builds the
    `RulesOutcome.lines` it returns without printing anywhere its caller did not ask for."""
    recorded: list[str] = []

    def sink(line: str) -> None:
        recorded.append(line)
        echo(line)

    return sink, recorded


@click.group("rules")
def rules_group() -> None:
    """Rule lifecycle commands (candidate -> paper -> live -> disabled)."""


@rules_group.command("list")
@click.option("--status", default=None, help="Filter by status (candidate/paper/live/disabled).")
@click.pass_context
@with_disclaimer
def rules_list(ctx: click.Context, status: str | None) -> None:
    """List rules (read-only)."""
    repo = _open_repo(ctx)
    rows = repo.get_rules(status)
    if not rows:
        click.echo("no rules found.")
        return
    for row in rows:
        click.echo(f"[{row['id']}] {row['kind']} status={row['status']} params={row['params']}")


def _rule_row_or_refuse(
    repo: Repository, rule_id: int, echo_err: Callable[[str], None]
) -> dict[str, Any]:
    """The `rules` row for `rule_id`, or a refusal naming it (nothing written)."""
    rows = {row["id"]: row for row in repo.get_rules()}
    row = rows.get(rule_id)
    if row is None:
        echo_err(f"Error: no rule with id {rule_id}")
        raise RulesRefused(f"no rule with id {rule_id}")
    return row


def _resolve_granularity(rule: Any, granularity_opt: str | None) -> Granularity | None:
    if granularity_opt:
        return Granularity(granularity_opt)
    for attr in ("granularity", "timeframe"):
        value = getattr(rule, attr, None)
        if value is not None:
            return value
    return None


def _optional_cfg(ctx: click.Context) -> Any | None:
    """The loaded config, or `None` if there isn't a usable one.

    `rules backtest` is read-only and useful without a deployment config (a bare checkout, a
    scratch database). It should not start REQUIRING one just to learn a fee rate -- so a
    missing/invalid config degrades to the library default here rather than aborting. What it
    must never do is degrade *silently*: the caller pairs this with `_describe_fee`, which
    prints which of the two sources supplied the rate.
    """
    try:
        return _load_cfg(ctx)
    except ConfigError:
        return None


def _backtest_fee(config: Any | None) -> tuple[Decimal, str]:
    """The fee rate to price fills at, and a human-readable statement of where it came from.

    Returns `config.fees.taker_pct` when a config is loaded, else `backtest.TAKER_FEE_PCT`. The
    two agree by construction (pinned by a test), so the fallback cannot flatter a result; the
    *source* is returned anyway because "1.2000% (taker, from config `fees.taker_pct`)" and
    "1.2000% (taker, library default)" answer different questions when a deployment's config
    turns out not to be the one the operator thought they were running.

    Taker is correct here because `backtest` fills market-style at next-bar open (see
    `backtest.TAKER_FEE_PCT`). A caller wanting to model resting limit orders instead should
    pass `fees.maker_pct` explicitly and say so in its output.
    """
    if config is None:
        return backtest_mod.TAKER_FEE_PCT, "taker, library default"
    return config.fees.taker_pct, "taker, from config `fees.taker_pct`"


def _describe_fee(fee_pct: Decimal, source: str) -> str:
    """`fee_pct=1.2000% (taker, from config ...)` -- the provenance line that travels with every
    printed backtest number. See `backtest.TAKER_FEE_PCT` for why this is not optional."""
    return f"fee_pct={fee_pct * 100:.4f}% ({source})"


def _resolve_backtest_inputs(
    repo: Repository,
    rule: Any,
    granularity_opt: str | None,
    echo_err: Callable[[str], None],
) -> tuple[Granularity, list[Candle]]:
    """The product/granularity/candles resolution every stored-rule backtest path shares
    (`_backtest_rule` here, `resolve_rule_backtest` in the service section below): a rule
    with no product or no resolvable granularity is a refusal, not a crash. THE one copy
    of the input assembly -- extracted so the strategy console's per-rule verdict
    resolves through it too instead of re-deriving the loop."""
    product_id = getattr(rule, "product_id", None)
    if product_id is None:
        echo_err("Error: rule has no product_id to backtest against")
        raise RulesRefused("rule has no product_id")
    granularity = _resolve_granularity(rule, granularity_opt)
    if granularity is None:
        echo_err("Error: could not determine a granularity; pass --granularity")
        raise RulesRefused("no granularity")
    return granularity, repo.get_candles(product_id, granularity)


def _backtest_rule(
    repo: Repository,
    rule: Any,
    granularity_opt: str | None,
    fee_pct: Decimal,
    echo_err: Callable[[str], None],
) -> backtest_mod.BacktestResult:
    """Backtest `rule` against its own cached candles at `fee_pct` -- the one backtest path
    `rules backtest`/`rules promote` (and the strategy console's ledger/retry) all share.
    A rule with no product or no resolvable granularity is a refusal, not a crash."""
    _granularity, candles = _resolve_backtest_inputs(repo, rule, granularity_opt, echo_err)
    return backtest_mod.backtest(rule, candles, fee_pct=fee_pct)


def _load_pbo_for(
    session: str | None,
    blocks: int,
    echo_err: Callable[[str], None],
) -> cscv_mod.PBOResult | None:
    """The CSCV result for `session`, or `None` when no session was named -- the
    ctx-free service half of `_load_pbo` (see its docstring for the reasoning)."""
    if session is None:
        return None
    trials = trials_ledger.read_trials(trials_ledger.DEFAULT_LEDGER_PATH)
    build = matrix_mod.build_matrix(trials, session=session)
    if not build.columns:
        echo_err(
            f"Error: no usable trial columns for session {session!r} -- every trial is "
            "`series_missing` or the session has no trials. CSCV cannot run, so the rule "
            "cannot be promoted through the gate."
        )
        raise RulesRefused(f"no usable trial columns for session {session!r}")
    for warning in build.warnings:
        echo_err(f"warning: {warning}")
    return cscv_mod.pbo(build.columns, s=blocks)


def _load_pbo(ctx: click.Context, session: str | None, blocks: int) -> cscv_mod.PBOResult | None:
    """The CSCV result for `session`, or `None` when no session was named.

    Reuses the exact pipeline behind `keel trials pbo` -- ledger -> `build_matrix` ->
    `cscv.pbo` -- rather than a second implementation, so the number the gate applies is the
    same number an operator can reproduce by hand and audit.

    Returning `None` for "no session given" is safe here ONLY because `can_promote` treats
    `None` as NOT RUN and refuses to promote on it. A genuinely empty/unusable matrix is a hard
    error instead: the operator asked for the check, so silently downgrading to "not run" would
    hide a broken ledger behind the same message as not having asked.

    The CLI-shaped wrapper over `_load_pbo_for` (its `ctx.exit(1)` turning the service's
    `RulesRefused` into the exit the command always had); tests patch THIS name to pin the
    gate's verdict, so the service takes the loader as an injection rather than calling
    either half directly.
    """
    try:
        return _load_pbo_for(session, blocks, lambda message: click.echo(message, err=True))
    except RulesRefused:
        ctx.exit(1)
        raise  # unreachable: ctx.exit raises SystemExit


@rules_group.command("backtest")
@click.argument("rule_id", type=int)
@click.option(
    "--granularity", default=None, help="Override the candle granularity (default: the rule's own)."
)
@click.pass_context
@with_disclaimer
def rules_backtest(ctx: click.Context, rule_id: int, granularity: str | None) -> None:
    """Backtest a stored rule against its historical candles (read-only).

    The output states the fee rate the fills were priced at. That is not decoration: a profit
    factor is a statement about net edge, and at this strategy's cost-to-edge ratio the fee is
    the dominant term, not a rounding detail -- prior numbers printed without it turned out to
    be maker-priced against a taker fill model (#247).
    """
    try:
        run_rule_backtest(
            _open_repo(ctx),
            _optional_cfg(ctx),
            rule_id,
            granularity_opt=granularity,
            echo=click.echo,
            echo_err=lambda message: click.echo(message, err=True),
        )
    except RulesRefused:
        ctx.exit(1)


@dataclass(frozen=True)
class ResolvedBacktest:
    """What `resolve_rule_backtest` assembled: the row, the rebuilt rule, the fee rate its
    fills will be priced at (and where that rate came from), the rule's resolved
    granularity, and the repo's cached candles for it. The INPUT half of the `rules
    backtest` compute core, split from the run so a second front-end (the strategy
    console's per-rule verdict) can inspect what was resolved -- an empty candle list is
    its "no backtest on record" case, distinct from a backtest that ran and found nothing
    -- and then execute the same backtest the CLI runs."""

    row: dict[str, Any]
    rule: Any
    fee_pct: Decimal
    fee_source: str
    granularity: Granularity
    candles: list[Candle]


def resolve_rule_backtest(
    repo: Repository,
    config: Any | None,
    rule_id: int,
    *,
    granularity_opt: str | None = None,
    echo_err: Callable[[str], None] = _noop,
) -> ResolvedBacktest:
    """THE `rules backtest` input core: read the row, rebuild the rule, derive the fee,
    resolve the product/granularity and fetch its cached candles -- everything both
    front-ends need before a backtest runs, with NOTHING echoed and nothing written (the
    summary line is `run_rule_backtest`'s rendering; the console's verdict renders its
    own).

    Raises exactly what the pieces raise, so each front-end maps them its own way: the
    `RulesRefused` refusal shapes (unknown id, no product, no granularity), and
    `agent._build_rule`'s `ValueError` for a kind no longer in RULE_REGISTRY or params the
    constructor rejects."""
    row = _rule_row_or_refuse(repo, rule_id, echo_err)
    rule = agent._build_rule(row)
    fee_pct, fee_source = _backtest_fee(config)
    granularity, candles = _resolve_backtest_inputs(repo, rule, granularity_opt, echo_err)
    return ResolvedBacktest(
        row=row,
        rule=rule,
        fee_pct=fee_pct,
        fee_source=fee_source,
        granularity=granularity,
        candles=candles,
    )


def backtest_resolved(resolved: ResolvedBacktest) -> backtest_mod.BacktestResult:
    """THE backtest execution over `resolve_rule_backtest`'s output -- the service seam the
    strategy console's per-rule verdict runs the engine's backtest through, at the fee the
    resolution derived, so no front-end ever assembles the engine call itself. Whatever
    the backtest raises on a poisoned row propagates untouched: the caller renders the
    failure (the CLI as a crash, the console as its honest per-row error line)."""
    return backtest_mod.backtest(resolved.rule, resolved.candles, fee_pct=resolved.fee_pct)


def run_rule_backtest(
    repo: Repository,
    config: Any | None,
    rule_id: int,
    *,
    granularity_opt: str | None = None,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> tuple[RulesOutcome, backtest_mod.BacktestResult]:
    """THE `rules backtest` service: the row, the backtest, the fee-honest summary line --
    `resolve_rule_backtest` + `backtest_resolved` with the CLI's rendering over them.

    Returns the outcome (its single echoed line) beside the raw `BacktestResult` -- the
    strategy console's retry flow renders both, and a caller that only wants the CLI's
    line reads `outcome.lines`. Refusals (unknown id, no granularity) are the service's
    `RulesRefused` shapes, already echoed to `echo_err`."""
    resolved = resolve_rule_backtest(
        repo, config, rule_id, granularity_opt=granularity_opt, echo_err=echo_err
    )
    stats = backtest_resolved(resolved)
    sink, recorded = _line_sink(echo)
    sink(
        f"rule {rule_id} ({resolved.row['kind']}): n_trades={stats.n_trades} "
        f"win_rate={stats.win_rate:.2%} expectancy={stats.expectancy} "
        f"profit_factor={stats.profit_factor} max_drawdown={stats.max_drawdown} "
        f"{_describe_fee(resolved.fee_pct, resolved.fee_source)}"
    )
    return (
        RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status=resolved.row["status"]),
        stats,
    )


# -- lookahead / recursive-bias diagnostics (issue #440, C1a) ---------------------------------
#
# The seam `keel.research.bias` replays is the backtester's own (detect over a growing
# prefix), and the saved-rule path into it is the SAME one `rules backtest` resolves through:
# `resolve_rule_backtest` reads the row, rebuilds the rule and fetches its cached candles.
# The rule families' own bar-count params raise the harness's warmup floor past their
# indicator warmup region, and every coarser series cached beside the rule's timeframe (the
# coarsest included) activates the higher-timeframe poison axis -- the engine-veto leak
# check; when none is cached the report says the axis was not run, never implies coverage.

#: Constructor params that count BARS for the rule's indicators. The lookahead walk skips
#: the first max(DEFAULT_WARMUP, these) bars: below them a rule's detect returns warmup
#: noise (or None), and a divergence there indicts the harness, not the rule.
_PERIOD_HINT_KEYS: tuple[str, ...] = (
    "atr_period",
    "adx_period",
    "rsi_period",
    "entry_lookback",
    "exit_lookback",
    "lookback_days",
)

#: The recursive check reads each family's own ATR through this params key (turtle's stop is
#: `atr_period`/`atr_stop_mult`, rsi's is `atr_period`/`atr_mult`). Families without it
#: (dca, pullback_continuation in v1) get an honest "nothing configured to check" line
#: rather than a silently skipped flag.
_ATR_PERIOD_KEY = "atr_period"


def _lookahead_warmup(rule: Any) -> int:
    """max(bias.DEFAULT_WARMUP, the rule's own longest bar-count param) -- the walk starts
    past the rule's indicator warmup, read off its persisted params rather than a second,
    per-kind table that would drift the first time a rule gained a lookback knob."""
    params: dict[str, Any] = getattr(rule, "params", None) or {}
    hints = [
        value
        for key, value in params.items()
        if key in _PERIOD_HINT_KEYS and isinstance(value, int) and not isinstance(value, bool)
    ]
    ema_periods = params.get("ema_periods")
    if isinstance(ema_periods, (tuple, list)) and ema_periods:
        try:
            hints.append(max(int(period) for period in ema_periods))
        except (TypeError, ValueError):
            pass  # a non-numeric element is the add-flow's refusal, not this walk's problem
    return max([bias_mod.DEFAULT_WARMUP, *hints])


def _lookahead_views(
    repo: Repository, rule: Any, granularity: Granularity, candles: list[Candle]
) -> dict[Granularity, list[Candle]]:
    """The multi-timeframe dataset the lookahead harness walks for `rule`: its own resolved
    granularity's cached candles, plus EVERY coarser granularity the repo actually holds
    candles for -- queried in GRANULARITY_SECONDS order, so the coarsest CACHED series is
    always among them when one exists.

    Every, not just the next one up, because the engine-veto leak runs through the COARSEST
    *cached* higher TF (`engine._higher_tf_bias_ok` picks `max` over what it is handed), and
    deployments cache non-adjacent sets -- every shipped config ships [ONE_DAY, ONE_HOUR,
    FIFTEEN_MINUTE] with no SIX_HOUR. A one-step-coarser pick asked the repo for SIX_HOUR,
    got nothing back, and handed the poison axis an empty series: the axis silently no-oped
    and a rule blindly reading the last ONE_DAY bar -- the canonical engine-veto leak --
    was reported clean. Granularities with no cached candles are skipped (nothing to poison
    with); when NOT ONE coarser series is cached there is genuinely no higher-TF axis to
    run, and `bias.lookahead_analysis` carries that on the report's `notes` so the render
    says the axis was not run instead of implying coverage."""
    views: dict[Granularity, list[Candle]] = {granularity: candles}
    coarser = sorted(
        (g for g in Granularity if GRANULARITY_SECONDS[g] > GRANULARITY_SECONDS[granularity]),
        key=lambda g: GRANULARITY_SECONDS[g],
    )
    for gran in coarser:
        coarse_candles = repo.get_candles(rule.product_id, gran)
        if coarse_candles:
            views[gran] = coarse_candles
    return views


def _recursive_periods(rule: Any) -> list[int]:
    """The ATR periods the rule family configures, per its params -- today each ATR-stopped
    family (turtle, rsi) has exactly one, its own `atr_period`; a family without any gets an
    honest "nothing to check" line rather than a silently skipped flag."""
    params: dict[str, Any] = getattr(rule, "params", None) or {}
    period = params.get(_ATR_PERIOD_KEY)
    if isinstance(period, int) and not isinstance(period, bool):
        return [period]
    return []


def _turtle_atr_window(params: dict[str, Any], period: int) -> int | None:
    """The tail length `turtle_breakout`'s detect() feeds its own ATR -- `work = series
    [-needed:]` with `needed = max(entry_lookback + 1, exit_lookback + 1, adx_period * 4,
    atr_period * 4)` (its PERFORMANCE window) -- or `None` when the params are not turtle's.

    Read off the params rather than the class name so a kind that grows the same bar-count
    knobs is measured by its own declared window, and so the formula here can be checked
    against the rule source it mirrors by eye."""
    entry = params.get("entry_lookback")
    exit_lb = params.get("exit_lookback")
    adx = params.get("adx_period")
    if not (
        isinstance(entry, int)
        and not isinstance(entry, bool)
        and isinstance(exit_lb, int)
        and not isinstance(exit_lb, bool)
        and isinstance(adx, int)
        and not isinstance(adx, bool)
    ):
        return None
    return max(entry + 1, exit_lb + 1, adx * 4, period * 4)


def _atr_indicator(rule: Any, period: int) -> tuple[Callable[[list[Candle]], float], str, int]:
    """(indicator, name, min_warmup) for the rule family's OWN ATR at `period` -- the
    growing-prefix callable `recursive_analysis` samples, named and floored to match.

    turtle_breakout sizes its stop from ATR over a bounded tail (see `_turtle_atr_window`);
    every other `atr_period` family (rsi_meanrev) feeds full history -- the check mirrors
    whichever the family actually computes rather than one shape for all, so its verdict is
    about the indicator the rule trades. `min_warmup` floors the walk at the family's own
    real window: past it, drift is the indicator's, not warmup's."""
    params: dict[str, Any] = getattr(rule, "params", None) or {}
    window = _turtle_atr_window(params, period)
    if window is not None:
        tail = window

        def windowed(candles: list[Candle]) -> float:
            return indicators.atr(candles[-tail:], period)[-1]

        return windowed, f"atr[-{window}:]({period})[-1]", window

    def full_history(candles: list[Candle]) -> float:
        return indicators.atr(candles, period)[-1]

    return full_history, f"atr({period})[-1]", 4 * period


@rules_group.command("lookahead")
@click.argument("rule_id", type=int)
@click.option(
    "--granularity", default=None, help="Override the candle granularity (default: the rule's own)."
)
@click.option(
    "--sample-step",
    type=int,
    default=1,
    show_default=True,
    help="Anchor stride: the harness runs ~2 detect calls per sampled bar past warmup, so 1 "
    "checks every bar and costs correspondingly; raise it on long histories.",
)
@click.option(
    "--recursive",
    is_flag=True,
    default=False,
    help="Also run the recursive warmup-drift check on the rule's own ATR exactly as its "
    "family computes it (turtle's bounded tail window, rsi's full history); a family "
    "without one is reported honestly as having nothing to check.",
)
@click.pass_context
@with_disclaimer
def rules_lookahead(
    ctx: click.Context, rule_id: int, granularity: str | None, sample_step: int, recursive: bool
) -> None:
    """Diagnose a stored rule for lookahead bias (read-only; issue #440).

    Re-runs the rule's detect at every past bar against the data a live engine would have
    had there, and reports WHICH bars' decisions change once future bars are visible --
    PBO says nothing about this: a single-config rule can be catastrophically lookahead-
    biased and pass every overfitting check. Exits 1 on LOOKAHEAD DETECTED (a diagnostic
    that fails loud, like `keel doctor`); a recursive-drift verdict is printed but is not
    by itself an exit-1 condition.

    The same analysis gates `keel rules promote`: a rule that fails here is refused there.
    """
    try:
        _outcome, report = run_rule_lookahead(
            _open_repo(ctx),
            _optional_cfg(ctx),
            rule_id,
            granularity_opt=granularity,
            sample_step=sample_step,
            recursive=recursive,
            echo=click.echo,
            echo_err=lambda message: click.echo(message, err=True),
        )
    except RulesRefused:
        ctx.exit(1)
        raise  # unreachable: ctx.exit raises SystemExit
    if report is not None and report.verdict == "lookahead_detected":
        ctx.exit(1)


def run_rule_lookahead(
    repo: Repository,
    config: Any | None,
    rule_id: int,
    *,
    granularity_opt: str | None = None,
    sample_step: int = 1,
    recursive: bool = False,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> tuple[RulesOutcome, bias_mod.LookaheadReport]:
    """THE `rules lookahead` service: resolve the saved rule exactly as `rules backtest`
    does, run the truncation-diff (and, when asked, the rule's own ATR recursive check) over
    its cached candles, and echo `bias.render_lines` -- one implementation for the CLI and
    the strategy console. Returns the report beside the outcome so the caller can key its
    exit code off the verdict."""
    resolved = resolve_rule_backtest(
        repo, config, rule_id, granularity_opt=granularity_opt, echo_err=echo_err
    )
    sink, recorded = _line_sink(echo)

    report = bias_mod.lookahead_analysis(
        resolved.rule.detect,
        _lookahead_views(repo, resolved.rule, resolved.granularity, resolved.candles),
        rule_id=str(rule_id),
        sample_step=sample_step,
        warmup=_lookahead_warmup(resolved.rule),
    )
    for line in bias_mod.render_lines(report):
        sink(line)

    if recursive:
        periods = _recursive_periods(resolved.rule)
        if not periods:
            sink(
                f"recursive: no recursive-suspect indicator configured for "
                f"{resolved.row['kind']} (no {_ATR_PERIOD_KEY} param) -- nothing to check"
            )
        for period in periods:
            indicator_fn, indicator_name, min_warmup = _atr_indicator(resolved.rule, period)
            recursive_report = bias_mod.recursive_analysis(
                resolved.candles,
                indicator_fn=indicator_fn,
                # The family's own window (turtle's bounded tail; rsi's 4 x period floor
                # over full history) -- whatever `_atr_indicator` derived, so the prefixes
                # sampled are the ones the rule's own stop could have been sized from.
                min_warmup=min_warmup,
                rule_id=str(rule_id),
                indicator_name=indicator_name,
            )
            for line in bias_mod.render_lines(recursive_report):
                sink(line)

    return (
        RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status=resolved.row["status"]),
        report,
    )


@rules_group.command("promote")
@click.argument("rule_id", type=int)
@click.option(
    "--granularity", default=None, help="Override the candle granularity for the backtest."
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the backtest/promotion gate and advance the rule one lifecycle step directly "
    "(candidate->paper, or paper->live). For a deliberate, un-gated paper-forward start when "
    "a rule's backtest can never reach the min_trades floor -- analogous to `rules seed "
    "--status live`'s gate bypass.",
)
@click.option(
    "--pbo-session",
    default=None,
    help="Trials-ledger session label to run the G4 overfitting check (PBO/CSCV) against. "
    "REQUIRED for promotion: without it the check cannot run, and an un-run check is not a "
    "pass. Use `keel trials list` to find the session your parameter sweep recorded under.",
)
@click.option("--pbo-blocks", default=16, show_default=True, help="S: number of CSCV row blocks.")
@click.pass_context
@with_disclaimer
def rules_promote(
    ctx: click.Context,
    rule_id: int,
    granularity: str | None,
    force: bool,
    pbo_session: str | None,
    pbo_blocks: int,
) -> None:
    """Re-run a rule's backtest and advance its lifecycle status if it clears the gate.

    The gate is TWO things, and both must pass: the performance floors (G2 -- trades,
    expectancy, R:R, win rate) and the overfitting check (G4 -- PBO/CSCV against the trial
    matrix the rule's parameters were selected from, §78). Pass `--pbo-session` to supply the
    second. **Omitting it does not promote**: a rule that clears four floors on one in-sample
    parameter set is exactly what PBO exists to be suspicious of, so "nobody checked" is
    reported as a failing reason rather than quietly treated as fine (#247).

    The sample-size axis has a SECOND, pooled reading (#338): when the same parameters
    already run as `paper` rules on other products, their backtests are pooled with this
    rule's own (pooled n >= 100 AND a diversity floor of at least 5 products contributing
    >= 10 trades each). The pooled reading is printed BESIDE the per-rule one -- both
    readings, always -- and a promotion that clears on one product alone is judged
    exactly as before, untouched by the pool. `min_trades` itself is unchanged: 100
    stays 100; what changed is the unit of evaluation, with the operator's 2026-08-17
    agreement (see #338).

    With `--force`, SKIPS the backtest/gate entirely and advances the rule one lifecycle step
    directly. This exists for a low-frequency trend-follower (or any rule) whose backtest can
    NEVER produce `min_trades` (default 100) trades -- without a bypass such a rule could never
    reach `paper` status, yet the whole point of a paper-forward is to accrue the out-of-sample
    trades the backtest can't. Use deliberately and audit the (loud) warning this prints.
    """
    try:
        attempt_promotion(
            _open_repo(ctx),
            # Loaded ONLY on the gated path, exactly as the old body did -- there, AFTER
            # the row refusal: `--force` never needed a config, an unreadable one must
            # not newly refuse it, and NEITHER may mask the unknown-id refusal
            # (`--config missing.yaml rules promote 999` prints the id error, exit 1).
            # Passed LAZY (a zero-arg loader) so the service resolves it at that point.
            lambda: None if force else _load_cfg(ctx),
            rule_id,
            granularity_opt=granularity,
            force=force,
            pbo_session=pbo_session,
            pbo_blocks=pbo_blocks,
            # The CLI's own loader seam (tests patch `rules_cmd._load_pbo` to pin the G4
            # verdict); the console uses the service's default. Called at the exact point
            # the old command body called it, so stdout/stderr interleaving is unchanged.
            load_pbo=lambda session, blocks: _load_pbo(ctx, session, blocks),
            echo=click.echo,
            echo_err=lambda message: click.echo(message, err=True),
        )
    except RulesRefused:
        ctx.exit(1)


def attempt_promotion(
    repo: Repository,
    config: Any,
    rule_id: int,
    *,
    granularity_opt: str | None = None,
    force: bool = False,
    pbo_session: str | None = None,
    pbo_blocks: int = 16,
    load_pbo: Callable[[str | None, int], cscv_mod.PBOResult | None] | None = None,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> RulesOutcome:
    """THE `rules promote` service: re-backtest, both gate readings, the transition.

    The exact body the CLI command ran, echoed through the injected sinks in the exact
    order it printed them -- the CLI passes `click.echo`, the strategy console collects the
    same lines to render in-console. `load_pbo` defaults to the service's own ctx-free
    loader (`_load_pbo_for`); the CLI passes its `ctx`-carrying `_load_pbo` seam so the
    tests that pin the G4 verdict keep working unchanged.

    `config` may be a loaded `Config`, `None`, or a zero-arg CALLABLE producing either --
    resolved only on the gated path, after the row refusal, at the exact point the old
    command body loaded it: the CLI passes a lazy loader so an unreadable config cannot
    mask `Error: no rule with id N` (origin loaded config only after the row check).

    FORCE carries no gate HERE (the CLI's `--force` is a flag the operator already typed at
    a terminal); the strategy console's retry flow runs its own TYPED gate before calling
    with `force=True` -- the O3 contract is the front-end's to keep, never the service's to
    assume."""
    if load_pbo is None:
        load_pbo = lambda session, blocks: _load_pbo_for(session, blocks, echo_err)  # noqa: E731
    sink, recorded = _line_sink(echo)
    row = _rule_row_or_refuse(repo, rule_id, echo_err)

    if force:
        target = promotion_mod.next_status(row["status"])
        if target is None:
            sink(
                f"rule {rule_id} ({row['kind']}): already at {row['status']!r}; nothing to promote"
            )
            return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status=row["status"])
        repo.update_rule_status(rule_id, target)
        sink(
            f"⚠️  FORCE-PROMOTING rule {rule_id} ({row['kind']}): {row['status']} -> {target}, "
            "BYPASSING the backtest/promotion gate. This is for a deliberate, un-gated "
            "paper-forward start (e.g. a low-frequency trend-follower whose backtest can never "
            "reach the min_trades floor). Confirm this is intentional and monitor accordingly."
        )
        log_event(
            logger,
            logging.WARNING,
            "rules.promote_forced",
            rule_id=rule_id,
            kind=row["kind"],
            from_status=row["status"],
            to_status=target,
        )
        sink(f"rule {rule_id} ({row['kind']}): status -> {target}")
        return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status=target)

    # The lazy config resolves HERE -- after the row refusal, exactly where the old
    # command body loaded it (`config = _load_cfg(ctx)` sat below the force return).
    if callable(config):
        config = config()
    rule = agent._build_rule(row)

    # The lookahead gate (issue #440, C1a): a rule whose at-bar decision changes when future
    # bars are visible reads information it could not have had, and is not promotable -- wired
    # into the existing gauntlet the same way as its other checks: on the gated path only
    # (`--force` returned above, and remains the documented bypass for it too), fail-closed,
    # with the divergences and the command that reproduces them. Silent on the clean path,
    # so a passing promotion's output is unchanged.
    lookahead_granularity, lookahead_candles = _resolve_backtest_inputs(
        repo, rule, granularity_opt, echo_err
    )
    try:
        lookahead = bias_mod.lookahead_analysis(
            rule.detect,
            _lookahead_views(repo, rule, lookahead_granularity, lookahead_candles),
            rule_id=str(rule_id),
            # Whole history sampled to at most ~200 anchors (~400 detect calls): seconds, not
            # minutes, on the cached series a promotion re-backtests anyway.
            sample_step=max(1, -(-len(lookahead_candles) // 200)),
            warmup=_lookahead_warmup(rule),
        )
    except RulesRefused:
        raise
    except Exception as exc:
        # Fail-closed, gracefully: a detect that RAISES inside the harness (a poisoned view
        # the rule's arithmetic cannot survive, say) is not a clean verdict, and a traceback
        # escaping a promotion command is a posture the gate holds nowhere else. The operator
        # gets the named error and the same nothing-was-promised refusal as every other
        # failed check; `--force` remains the deliberate bypass.
        echo_err(
            f"Error: rule {rule_id} ({row['kind']}): lookahead analysis could not run: "
            f"{exc!r}. An un-run check is not a pass, so nothing was promoted."
        )
        raise RulesRefused(f"rule {rule_id}: lookahead analysis could not run") from exc
    if lookahead.verdict == "lookahead_detected":
        echo_err(
            f"Error: rule {rule_id} ({row['kind']}) fails the lookahead check -- its "
            "decision at past bars changes when future bars become visible, so the "
            "backtest it would be promoted on cannot be realized live. Nothing was promoted."
        )
        for line in bias_mod.render_lines(lookahead):
            echo_err(f"  {line}")
        echo_err(
            f"Fix the rule (see `keel rules lookahead {rule_id}` for the full report), or "
            "use --force deliberately to bypass this and every other gate."
        )
        raise RulesRefused(f"rule {rule_id} fails the lookahead check")
    # Coverage honesty: a clean verdict over the one time frame (no coarser series cached,
    # so the higher-TF poison axis never ran) must not read out of the promote gate as full
    # coverage -- the report's notes say what did not run; carry them here too. On the
    # STDOUT sink (like the recursive no-suspect line): the promotion PROCEEDS past this
    # point, and the strategy console renders a stderr line as a refusal.
    for note in lookahead.notes:
        sink(f"warning: rule {rule_id} ({row['kind']}): {note}")

    fee_pct, fee_source = _backtest_fee(config)
    stats = _backtest_rule(repo, rule, granularity_opt, fee_pct, echo_err)
    sink(f"rule {rule_id} ({row['kind']}): gate priced at {_describe_fee(fee_pct, fee_source)}")

    # #338: the sample-size axis also has a POOLED reading -- the same parameters'
    # evidence on other products. Siblings are `paper` rows with identical params
    # (minus the product), one per product; each is backtested against its own
    # product's candles at the same fee, and this rule's own reading joins the pool
    # exactly once. A rule with no siblings is judged exactly as before, with no pooled
    # lines in the output at all.
    candidate_product = (row["params"] or {}).get("product_id")
    pooled_samples: list[promotion_mod.ProductSample] | None = None
    sibling_rows = (
        promotion_mod.paper_sibling_rows(repo, row["kind"], row["params"])
        if candidate_product
        else []
    )
    if sibling_rows:
        samples = [promotion_mod.ProductSample(str(candidate_product), stats)]
        for sib in sibling_rows:
            sib_product = (sib["params"] or {}).get("product_id")
            sib_rule = agent._build_rule(sib)
            if _resolve_granularity(sib_rule, granularity_opt) is None:
                echo_err(
                    f"warning: pooled sibling rule {sib['id']} ({sib_product}) has no "
                    "granularity to backtest against; excluded from the pool"
                )
                continue
            sib_stats = _backtest_rule(repo, sib_rule, granularity_opt, fee_pct, echo_err)
            samples.append(promotion_mod.ProductSample(str(sib_product), sib_stats))
        # A pool of one (every sibling skipped) is no pool: judge the rule alone rather
        # than printing a "diversity 1 < 5" failure the operator cannot act on.
        if len(samples) > 1:
            pooled_samples = samples

    promo_cfg = promotion_mod.PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )
    pbo_result = load_pbo(pbo_session, pbo_blocks)
    gate = promotion_mod.pbo_gate_from_config(config.research)

    decision = promotion_mod.can_promote(stats, promo_cfg, pbo_result, gate, pooled_samples)

    # BOTH readings, whenever a pool existed -- the operator approving the promotion
    # is entitled to see which path carried it, and a pooled failure to see why.
    if decision.pooled is not None:
        reading = decision.pooled
        census = ", ".join(f"{product}={n}" for product, n in reading.per_product)
        sink(
            f"rule {rule_id} ({row['kind']}): sample readings -- per-rule "
            f"n_trades={stats.n_trades}, pooled n_trades={reading.n_pooled} across "
            f"{len(reading.per_product)} products"
        )
        sink(
            f"  pooled census (diversity floor {promotion_mod.MIN_POOLED_PRODUCTS} "
            f"products x >= {promotion_mod.MIN_TRADES_PER_PRODUCT_POOLED} trades): "
            f"{census} -- {reading.products_contributing} products contribute, "
            f"min contribution {reading.min_contribution}"
        )
        # Say in WORDS when the pooled path carried the promotion: a log auditor should
        # not have to infer it from n_trades being below the floor on the line above.
        if decision.promotable and stats.n_trades < promo_cfg.min_trades:
            sink(
                "  promotion carried by the POOLED reading "
                "(the rule's own sample is below min_trades)"
            )

    sink(f"rule {rule_id} ({row['kind']}): overfitting check = {decision.overfitting}")
    for reason in decision.reasons:
        sink(f"  - {reason}")

    new_status = promotion_mod.transition(
        repo, row["kind"], stats, promo_cfg, pbo_result, gate, pooled_samples, rule_id
    )
    sink(f"rule {rule_id} ({row['kind']}): status -> {new_status}")
    return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status=new_status)


@rules_group.command("demote")
@click.argument("rule_id", type=int)
@click.pass_context
@with_disclaimer
def rules_demote(ctx: click.Context, rule_id: int) -> None:
    """Manually step a rule's lifecycle status back one stage (live->paper->candidate)."""
    try:
        apply_rule_demote(
            _open_repo(ctx),
            rule_id,
            echo=click.echo,
            echo_err=lambda message: click.echo(message, err=True),
        )
    except RulesRefused:
        ctx.exit(1)


def apply_rule_demote(
    repo: Repository,
    rule_id: int,
    *,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> RulesOutcome:
    """THE `rules demote` service: one lifecycle step back, through the same
    `update_rule_status` write the CLI makes."""
    sink, recorded = _line_sink(echo)
    row = _rule_row_or_refuse(repo, rule_id, echo_err)
    prev = _DEMOTE_PREV.get(row["status"])
    if prev is None:
        sink(f"rule {rule_id} ({row['kind']}): already at {row['status']!r}; nothing to demote")
        return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status=row["status"])
    repo.update_rule_status(rule_id, prev)
    sink(f"rule {rule_id} ({row['kind']}): status -> {prev}")
    return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status=prev)


@rules_group.command("disable")
@click.argument("rule_id", type=int)
@click.pass_context
@with_disclaimer
def rules_disable(ctx: click.Context, rule_id: int) -> None:
    """Disable a rule (nothing promotes from here; `keel rules enable` can restore it, at
    `candidate` -- never at the status it held when disabled)."""
    try:
        apply_rule_disable(
            _open_repo(ctx),
            rule_id,
            echo=click.echo,
            echo_err=lambda message: click.echo(message, err=True),
        )
    except RulesRefused:
        ctx.exit(1)


def apply_rule_disable(
    repo: Repository,
    rule_id: int,
    *,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> RulesOutcome:
    """THE `rules disable` service: `disabled` is terminal, and the write stamps
    `demoted_at` -- the recorded context the ledger renders for a disabled row."""
    sink, recorded = _line_sink(echo)
    row = _rule_row_or_refuse(repo, rule_id, echo_err)
    repo.update_rule_status(rule_id, "disabled")
    sink(f"rule {rule_id} ({row['kind']}): status -> disabled")
    return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status="disabled")


@rules_group.command("enable")
@click.argument("rule_id", type=int)
@click.pass_context
@with_disclaimer
def rules_enable(ctx: click.Context, rule_id: int) -> None:
    """Re-enable a disabled rule at `candidate` -- the bottom of the lifecycle ladder.

    This is the inverse of `rules disable`'s WRITE, not of its effect: `disable` records
    nothing about the status a rule held before it was disabled (it stamps only `demoted_at`),
    so there is nothing to restore, and a rule disabled from `live` comes back as `candidate`
    too. That is deliberate -- re-entry to the trading set is a promotion decision the gate
    must see again, not an undo.

    The path onward from `candidate` is `keel rules promote <id>` (gated); its `--force` is
    the documented bypass for a paper-forward whose backtest can never reach the min_trades
    floor -- a DCA rule produces no backtest trades at all, so force is the only way it can
    reach `paper` (see `promote`'s own docstring).
    """
    try:
        apply_rule_enable(
            _open_repo(ctx),
            rule_id,
            echo=click.echo,
            echo_err=lambda message: click.echo(message, err=True),
        )
    except RulesRefused:
        ctx.exit(1)


def apply_rule_enable(
    repo: Repository,
    rule_id: int,
    *,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> RulesOutcome:
    """THE `rules enable` service -- the documented RESTORE path for a disabled rule: back
    at `candidate`, the lifecycle floor, never at the status it held when disabled."""
    sink, recorded = _line_sink(echo)
    row = _rule_row_or_refuse(repo, rule_id, echo_err)
    if row["status"] != "disabled":
        echo_err(
            f"Error: rule {rule_id} ({row['kind']}) is {row['status']!r}, not disabled -- "
            "`enable` only restores a disabled rule. To advance this one, use "
            "`keel rules promote`."
        )
        raise RulesRefused(f"rule {rule_id} is {row['status']!r}, not disabled")
    repo.update_rule_status(rule_id, "candidate")
    sink(f"rule {rule_id} ({row['kind']}): status -> candidate")
    sink(
        f"rule {rule_id} ({row['kind']}): re-enabled at CANDIDATE, the lifecycle floor -- "
        "a rule disabled from `live` lands here too (disable records no prior status to "
        f"restore). Advance with `keel rules promote {rule_id}`; `--force` is the documented "
        "bypass for a paper-forward whose backtest can never reach the min_trades floor "
        "(e.g. DCA)."
    )
    return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status="candidate")


def _json_plain(value: Any) -> Any:
    """Coerce `value` into the JSON-plain form `Repository.insert_rule` expects for `params`.

    `Rule.describe()`'s `params` dict holds real `Decimal`s and tuples (constructor kwargs, not
    storage types) -- `insert_rule` round-trips `params` through plain `json.dumps`/`json.loads`
    (see `agent._build_rule`'s own docstring), so a `Decimal` here would raise `TypeError` at
    insert time. This is the inverse of `agent._build_rule`'s `_DECIMAL_PARAMS`/tuple coercion:
    `Decimal` -> `str`, tuple -> list, recursively through nested dicts/lists.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class SeedOutcome:
    """What one seeding pass did. `seeded`/`skipped` are `kind:product` labels, in the order the
    pass produced them."""

    seeded: tuple[str, ...]
    skipped: tuple[str, ...]
    status: str


def seed_rules_into(
    repo: Any,
    kinds: list[str],
    products: list[str],
    *,
    status: str,
    force: bool,
    now_ts: int,
) -> SeedOutcome:
    """Insert one rule row per (kind, product) that has none. THE seeding pass -- extracted from
    `rules_seed` so the CLI and the first-run setup path (#437) share one implementation rather
    than two that drift.

    Idempotent by (kind, product_id): a pair that already has a row of ANY status is skipped
    unless `force`. That is what makes it safe to call from a setup flow a user may click twice.

    Validation belongs to the CALLER. `rules_seed` refuses an untradeable product id through
    `parse_products_option` before reaching here, naming it, with nothing written -- and any other
    caller must do the same. This function trusts its arguments, which is why it is not public
    API for arbitrary input.
    """
    existing_keys = {
        (row["kind"], (row["params"] or {}).get("product_id")) for row in repo.get_rules()
    }

    seeded: list[str] = []
    skipped: list[str] = []
    for kind in kinds:
        for product in products:
            label = f"{kind}:{product}"
            if not force and (kind, product) in existing_keys:
                skipped.append(label)
                continue
            # Via `build_rule_from_params` rather than `RULE_REGISTRY[kind](product_id=...)`:
            # that function is documented as THE `(kind, params)` -> `Rule` boundary, and this
            # was the one caller reaching around it. With `product_id` as the only param none
            # of its coercion tables apply, so it calls the very same constructor -- but a rule
            # kind that later needs coercion for a seeded default gets it here for free.
            rule = agent.build_rule_from_params(kind, {"product_id": product})
            params = _json_plain(rule.describe()["params"])
            params["product_id"] = product
            repo.insert_rule(kind, params, status=status, now_ts=now_ts)
            seeded.append(label)
    return SeedOutcome(seeded=tuple(seeded), skipped=tuple(skipped), status=status)


@rules_group.command("seed")
@click.option(
    "--products",
    default=None,
    help="Comma-separated product ids (default: the allowlist, in the configured "
    "settlement currency).",
)
@click.option(
    "--kinds",
    default=None,
    help="Comma-separated rule kinds (default: every kind in agent.RULE_REGISTRY).",
)
@click.option(
    "--status",
    type=click.Choice(["candidate", "paper", "live"]),
    default="candidate",
    show_default=True,
    help="Status to seed at. `live` bypasses the promotion gate -- for the supervised "
    "live-order test only (see the go-live runbook).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Insert a new candidate rule even if one already exists for a (kind, product) pair.",
)
@click.pass_context
@with_disclaimer
def rules_seed(
    ctx: click.Context,
    products: str | None,
    kinds: str | None,
    force: bool,
    status: str = "candidate",
) -> None:
    """Seed the `rules` table with one `candidate` rule per (kind, product) pair (Issue #81).

    The `rules` table starts out empty and nothing else populates it -- with zero rows,
    `agent.run_once`/`keel simulate` have no strategies to evaluate at all, no matter how
    `config.yaml` or the promotion floor are set. This seeds one row per (kind, product) using
    each rule kind's own constructor defaults (`RULE_REGISTRY[kind](product_id=...).describe()`),
    so the resulting rows are exactly what `agent._build_rule` already knows how to
    reconstruct -- they still start at `candidate` and must clear `rules promote` before they can
    trade `paper`/`live`.

    Idempotent by (kind, product_id): re-running this with no `--force` skips any pair that
    already has a rule row of any status, so it's safe to call repeatedly (e.g. from a setup
    script) without piling up duplicate candidates. `--force` inserts a fresh candidate anyway.

    `--products` is validated before anything is written (`parse_products_option`): an id keel
    could not trade -- a futures contract, an equity hash, a pair settling outside
    `settlement_currencies`, a lowercase typo -- is refused here, naming it, and NO row is
    seeded. Rails 18/19 would veto every order for such a rule anyway; the difference is that
    the operator hears it now rather than reading it out of a log after the row is in the table.

    Read-only w.r.t. the exchange: no network call, no confirmation gate -- it only ever
    writes local
    `rules` rows, exactly like `rules promote`/`demote`/`disable`.
    """
    repo = _open_repo(ctx)
    now_ts = int(time.time())

    # Config is loaded UNCONDITIONALLY now, where it used to be loaded only on the
    # allowlist-default branch: `parse_products_option` needs `settlement_currencies` to answer
    # rail 18's question about a typed id, and a `--products` seed that skipped that check is
    # exactly the case R2 exists to close -- `--products XLM-28AUG26-CDE --status live` wrote a
    # row that looked seeded and that the agent then polled and vetoed on every cycle forever.
    # The cost is that `rules seed --products ...` now needs a readable `config.yaml`, like every
    # other command that touches products; the gain is that the operator hears "no" at the
    # keyboard, with the reason, instead of in a log line nobody is reading.
    config = _load_cfg(ctx)
    try:
        # `settlement_is_fatal` stays at its default here, unlike `fetch`/`simulate`: this
        # command WRITES a row the agent then polls every cycle, so a rule the rails veto
        # forever is not a lesser problem than a typo, only a quieter one. Nothing is warned
        # about and admitted; hence no warnings to print.
        product_list, _ = parse_products_option(products, config)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--products") from exc

    if kinds:
        kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
    else:
        kind_list = list(agent.RULE_REGISTRY)

    unknown_kinds = [k for k in kind_list if k not in agent.RULE_REGISTRY]
    if unknown_kinds:
        click.echo(
            f"Error: unknown rule kind(s) {unknown_kinds!r}; known kinds: "
            f"{sorted(agent.RULE_REGISTRY)!r}",
            err=True,
        )
        ctx.exit(1)
        return

    outcome = seed_rules_into(
        repo, kind_list, product_list, status=status, force=force, now_ts=now_ts
    )
    seeded, skipped = outcome.seeded, outcome.skipped

    click.echo(f"seeded={len(seeded)} skipped={len(skipped)} status={status}")
    if status == "live":
        click.echo(
            "⚠️  seeded at LIVE status, bypassing the promotion gate. This is for the "
            "supervised live-order test only -- the agent will act on these (still confirm-"
            "gated and rail-guarded). Do not leave live-seeded rules in place afterwards."
        )
    for label in seeded:
        click.echo(f"  seeded: {label}")
    for label in skipped:
        click.echo(f"  skipped: {label}")


def _accepted_params(rule_cls: type) -> list[str]:
    """The kwargs `rule_cls`'s constructor accepts, for a refusal message to name.

    Read off the signature rather than listed anywhere, so it cannot go stale: an operator
    hand-copying a `params` object out of a scout proposal mistypes a key sooner or later, and
    `got an unexpected keyword argument 'cadance_days'` alone leaves them guessing at the
    spelling of the one they meant.
    """
    return [name for name in inspect.signature(rule_cls).parameters if name != "self"]


def _declared_choices(rule_cls: type) -> dict[str, tuple[Any, ...]]:
    """{param: the values it may take}, for every kwarg `rule_cls` annotates as a `Literal`.

    `stop_method: StopMethod` where `StopMethod = Literal["fixed", "atr"]` IS the rule's own
    published statement of what it accepts, so the choices are read off the rule and never
    re-listed here -- a kind that gains a `target_method` branch is covered with no change to
    this command. Both shapes are picked up: a scalar (`entry_zone`) and the ELEMENT type of a
    tuple param (`signal_patterns: tuple[SignalPattern, ...]`); a param is one or the other,
    never both, so one dict serves both callers.

    `typing.get_type_hints`, not `inspect.signature`: every rule module starts with
    `from __future__ import annotations`, which leaves `param.annotation` the bare STRING
    `'StopMethod'` -- unusable. `get_type_hints` resolves it against the defining module, and
    handles the dataclass-generated `__init__` (`RsiMeanReversion`) as well as the hand-written
    one. A rule whose annotations cannot be resolved at all yields no choices rather than an
    exception: an un-checkable param is the status quo, a crashing `rules add` is not.
    """
    hints = _init_hints(rule_cls)

    choices: dict[str, tuple[Any, ...]] = {}
    for name, hint in hints.items():
        if get_origin(hint) is tuple:
            element = next((arg for arg in get_args(hint) if arg is not Ellipsis), None)
            if element is not None and get_origin(element) is Literal:
                choices[name] = get_args(element)
        elif get_origin(hint) is Literal:
            choices[name] = get_args(hint)
    return choices


def _param_type_mismatches(kind: str, rule_cls: type, supplied: dict[str, Any]) -> list[str]:
    """Params whose JSON value cannot work with the rule, judged against the rule's own signature.

    Constructing the rule does NOT catch these, and the row they produce is the bad kind: it
    stores, it rebuilds, and it then fails deep inside the rule's arithmetic the first time a
    backtest calls `detect()` -- in a command nobody connects to the params they typed. Neither
    `RsiMeanReversion` nor `PullbackContinuation` validates ANYTHING in its constructor, so
    construction is not a filter for them; where `RsiMeanReversion` does check a value
    (`stop_method`, in `_compute_stop`) it does so at `detect()` time, which is precisely the
    too-late that this function exists to pull forward. Five shapes, each demonstrated to kill
    or corrupt a backtest:

    - `{"oversold": "10.0"}` -- quoted, and `oversold` is a `float`: dies at `str < int`.
      Quoting is the likeliest typo in hand-copied JSON precisely BECAUSE it is correct for the
      `Decimal` params, so which params may be quoted is answered by `agent.coerced_param_keys`
      -- the coercion tables themselves -- rather than guessed at or re-listed here.
    - `{"oversold": [1, 2]}` (or `{...}`) -- the SAME param and the same death as the quoted
      case, in the one JSON shape a scalar/quoted-string check does not look at: it stores,
      `rules backtest` rebuilds it, and `detect()` raises `TypeError: '<' not supported between
      instances of 'float' and 'list'`.
    - `{"lookback_days": 90.5}` (or `1e400`, which JSON parses to `inf`) -- `lookback_days`
      indexes a candle list, and `candles[-90.5:]` raises "slice indices must be integers".
      A whole number for a `float` default is the harmless direction and stays allowed.
    - `{"oversold": null}` -- no rule param has a `None` default, and the coercion boundary
      passes `None` through untouched, so it reaches the constructor intact.
    - `{"entry_zone": "banana"}` -- a value outside the `Literal` the rule declares for that
      param (`_declared_choices`). This one does not crash, which is worse: `PullbackContinuation`
      dispatches on `== "ema_touch"` and falls through to the `ema_band` branch, so the operator
      is handed a DIFFERENT rule's numbers under the name they typed (measured on real BTC-USD
      hourly candles: 7 trades with the same expectancy to the last digit as `ema_band`, where
      `ema_touch` gives 11). `rules promote` re-runs the backtest against that same stored row,
      so the row can advance toward `live` carrying a parameter nobody chose. Refusing it is the
      same judgement already made for a param the row cannot carry (`granularity`).

    Judged against the constructor's OWN default and its OWN annotations, so a rule that changes
    a field's type or gains a branch needs no change here. Nothing else is second-guessed: this
    is not a type checker, only the mismatches that survive construction and reliably ruin a
    backtest.
    """
    coerced = agent.coerced_param_keys(kind)
    choices = _declared_choices(rule_cls)
    problems: list[str] = []
    for name, param in inspect.signature(rule_cls).parameters.items():
        if name not in supplied or name in coerced:
            continue
        default, value = param.default, supplied[name]
        if default is inspect.Parameter.empty:
            continue
        # A tuple default means the param is a SEQUENCE; a JSON list is the right shape for it
        # and its choices (if it declares any) apply to the elements, so both are
        # `_sequence_problems`' business rather than the scalar checks below.
        is_sequence = isinstance(default, tuple)
        allowed = choices.get(name)
        if value is None and default is not None:
            problems.append(f"{name}=null is not a value {kind} can use (default {default!r})")
        elif not is_sequence and isinstance(value, (list, dict)):
            shape = "list" if isinstance(value, list) else "object"
            problems.append(
                f"{name}={value!r} is a JSON {shape}, but {kind} wants a single "
                f"{type(default).__name__} here (default {default!r})"
            )
        elif not is_sequence and allowed is not None and value not in allowed:
            problems.append(
                f"{name}={value!r} is not one of the values {kind} declares for it: "
                f"{list(allowed)!r} (default {default!r})"
            )
        elif isinstance(default, str) and not isinstance(value, str):
            problems.append(f"{name}={value!r} should be a quoted string (default {default!r})")
        elif isinstance(default, (bool, int, float)) and isinstance(value, str):
            problems.append(
                f"{name}={value!r} is quoted, but {kind} wants a number here (default {default!r})"
            )
        elif (
            isinstance(default, int)
            and not isinstance(default, bool)
            and not _is_whole_number(value)
        ):
            problems.append(
                f"{name}={value!r} must be a whole number -- {kind} counts bars with it "
                f"(default {default!r})"
            )
        elif is_sequence and default:
            problems.extend(_sequence_problems(name, default, value, allowed))
    return problems


def _is_whole_number(value: Any) -> bool:
    """A JSON int, and not a bool dressed as one (`True` is an `int` to `isinstance`)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _sequence_problems(
    name: str, default: tuple[Any, ...], value: Any, allowed: tuple[Any, ...] | None = None
) -> list[str]:
    """Why `value` cannot stand in for a tuple param such as `ema_periods`/`signal_patterns`.

    `build_rule_from_params` converts these with a blind `tuple(value)`, which is total and
    therefore silent about three shapes that then ruin a backtest:

    - `"abc"` -- `tuple("abc")` CHAR-SPLITS into `('a', 'b', 'c')`, a plausible-looking
      three-EMA fan made of letters;
    - `["8", "20", "50"]` -- quoted numbers survive as strings, and `ema()`'s
      `2.0 / (period + 1)` then raises `TypeError: can only concatenate str (not "int") to str`;
    - `["pin_bar", "hammmer"]` -- a misspelled pattern name. `_match_signal_pattern` compares
      the name against each pattern it knows and matches none, so the typo does not raise: the
      rule just never fires on it. `allowed` (the param's declared `Literal` element type, via
      `_declared_choices`) is what makes that visible, and it comes from the rule itself.

    Element type is taken from the constructor's own default tuple, so a param that changes
    from ints to something else needs no change here. An empty list is refused too: it builds a
    rule with no EMAs at all, which never signals and reads as a rule that simply does not work
    -- and a list of names the rule cannot match is refused for that same reason, since it is
    that same rule with extra steps.
    """
    element_type = type(default[0])
    check = _is_whole_number if element_type is int else lambda v: isinstance(v, element_type)
    if not isinstance(value, list):
        return [
            f"{name}={value!r} must be a JSON list of {element_type.__name__} "
            f"(default {list(default)!r})"
        ]
    if not value:
        return [f"{name}=[] is empty; {name} needs at least one {element_type.__name__}"]
    bad = [item for item in value if not check(item)]
    if bad:
        return [
            f"{name} has {bad!r} where {element_type.__name__} values belong "
            f"(default {list(default)!r})"
        ]
    if allowed is not None:
        unknown = [item for item in value if item not in allowed]
        if unknown:
            return [
                f"{name} has {unknown!r}, which the rule never matches -- it would simply "
                f"never fire on {'them' if len(unknown) > 1 else 'it'}. Declared values: "
                f"{list(allowed)!r}"
            ]
    return []


def _nonfinite_params(params: dict[str, Any]) -> list[str]:
    """Params holding `Infinity`/`NaN` after coercion -- numbers that got away, each with why.

    `json.loads` accepts JSON's non-standard `Infinity`/`NaN` literals, and the guards a rule
    does have let them past: `Decimal('Infinity') <= 0` is `False`, so `Dca`'s "budget must be
    positive" check passes an infinite budget. Checked on the CONSTRUCTED params rather than on
    the input, so it catches such a value whichever way it arrived -- as a JSON float or as a
    quoted `"Infinity"` through the `Decimal` coercion.

    The reason is given PER PARAM because the two param types fail differently, and a single
    blanket reason is false for half of them:

    - a `float` param (`lookback_days`, `volume_mult`) is stored by `json.dumps` as a bare
      `Infinity` token, which is not valid JSON and which no strict reader (any consumer of
      this DB that is not Python) can parse back;
    - a `_DECIMAL_PARAMS` param (`budget_usd`) is stored as the STRING `"Infinity"`, which is
      perfectly valid JSON -- and that is worse, not better. It rebuilds silently into
      `Decimal('Infinity')`, which no `> 0` guard rejects and which then propagates: an
      infinite `budget_usd` yields `size_usd=Decimal('Infinity')` with nothing raising anywhere.

    Either way no backtest can report on the value, so either way it is refused; only the
    sentence explaining it differs.
    """
    bad: list[str] = []
    for name, value in params.items():
        if isinstance(value, float) and not math.isfinite(value):
            bad.append(
                f"{name}={value!r} -- a float param, so the row would store the bare token "
                f"`{json.dumps(value)}`, which is not valid JSON and which no strict reader "
                f"can parse back"
            )
        elif isinstance(value, Decimal) and not value.is_finite():
            bad.append(
                f'{name}={value!r} -- a Decimal param, so the row would store the string "'
                f'{value}", which IS valid JSON and therefore worse: it rebuilds silently into '
                f"Decimal('{value}'), which no positivity guard rejects and which propagates "
                f"into whatever the rule computes from it"
            )
    return bad


def _params_delta(existing: dict[str, Any], added: dict[str, Any]) -> str:
    """How an already-stored rule's params differ from the row just added, as a short `k=v` list.

    Only the DIFFERENCE, because two rows for one (kind, product) are the comparison the command
    exists to enable: they agree on eleven params and differ on the one under test. Printing both
    in full (as `rules list` does) would make the operator diff them by eye at precisely the
    moment they have to pick the right id for `rules backtest`. Both sides are the JSON-plain
    stored form, so `'2'` vs `2` is a real difference and is shown as one.

    A key present on ONE side only is not a parameter difference and is not reported as one. A
    row written before its kind grew a param simply has no such key, and a plain key-union diff
    drowns the real answer in schema history -- measured against a real pre-existing row, four
    of the five reported "differences" were params that did not exist when the row was written,
    and they buried the `entry_lookback` the operator was choosing between. Those keys are still
    named, because the two rows genuinely cannot be compared on them, but as a counted note
    AFTER the differences rather than mixed in among them.
    """
    shared = sorted(set(existing) & set(added))
    parts = [f"{key}={existing[key]!r}" for key in shared if existing[key] != added[key]]

    only_added = sorted(set(added) - set(existing))
    only_existing = sorted(set(existing) - set(added))
    if not parts and not only_added and not only_existing:
        return "(identical params)"

    delta = ", ".join(parts) if parts else "(no differing param)"
    if only_added:
        delta += (
            f" [+{len(only_added)} param(s) that row does not carry, so not comparable: "
            f"{', '.join(only_added)}]"
        )
    if only_existing:
        delta += (
            f" [{len(only_existing)} param(s) only that row carries: {', '.join(only_existing)}]"
        )
    return delta


@rules_group.command("add")
@click.option("--kind", required=True, help="Rule kind (one of agent.RULE_REGISTRY).")
@click.option("--product", required=True, help="The single product id the rule trades.")
@click.option(
    "--params",
    "params_json",
    default=None,
    help="Constructor params as a JSON object, e.g. '{\"entry_lookback\": 55}'. Omit the flag "
    "(or pass '{}') to take the kind's own defaults -- a baseline row to compare a proposal "
    "against. An EMPTY string is refused rather than read as the defaults.",
)
@click.pass_context
@with_disclaimer
def rules_add(ctx: click.Context, kind: str, product: str, params_json: str | None) -> None:
    """Insert ONE `candidate` rule with operator-supplied params and print its id.

    This is the path from a parameter proposal to evidence. `rules seed` builds rows only from
    each kind's constructor defaults, so before this command a proposed parameter set had no way
    into `rules backtest`/`simulate` short of hand-written Python against
    `Repository.insert_rule` -- and a proposal that cannot be measured is a proposal that gets
    adopted on argument instead.

    ⚠️ **The status is always `candidate`, and there is deliberately no flag to change it.**
    `candidate` is the lifecycle floor: the row must still clear `rules backtest` and
    `rules promote` before it can reach `paper`, let alone `live`. That single property is what
    makes it safe to let an operator (or a scout's JSON) put arbitrary rule params into the
    table of a system trading real money -- nothing added here can place an order until the
    promotion gate has seen its numbers. `rules seed --status live` exists for the supervised
    live-order test; a command whose whole input is un-vetted parameters must not have its
    equivalent.

    **Validated by CONSTRUCTION, before anything is written.** The params are parsed and then
    actually used to construct `RULE_REGISTRY[kind](product_id=..., **params)` via the shared
    `agent.build_rule_from_params` coercion boundary (the one that turns JSON-plain values back
    into the `Decimal`s/enums/tuples the constructors want). If the rule refuses them -- an
    unknown kwarg, or a value its `__init__` rejects, e.g. `Dca` on `cadence_days <= 0` -- this
    refuses too, names the problem, and writes NOTHING. What lands in the row is
    `.describe()`'s params, not the raw JSON, so the stored row is exactly what
    `agent._build_rule` can reconstruct: a row that stores but cannot rebuild is worse than a
    refusal, because it fails later, inside a backtest or an agent cycle.

    Construction is NOT sufficient on its own. Two of the four rule kinds (`RsiMeanReversion`,
    `PullbackContinuation`) have no `__init__` validation at all, and where a rule does check a
    value it may do so far too late to help here -- `RsiMeanReversion` raises
    `unknown stop_method` from `_compute_stop`, i.e. at `detect()` time, on a row that has
    already been written. So four further classes of bad params are refused here, each because
    it writes a row that stores, rebuilds, and *then* fails or lies:

    - a value of the wrong JSON type for the field (`_param_type_mismatches`): a quoted number,
      a fraction where the rule counts bars, a `null`, a JSON list/object where a single value
      belongs, a char-splittable string or a list of quoted numbers for `ema_periods`. These
      die inside `detect()`'s arithmetic, mid-backtest;
    - a value outside the choices the param's own `Literal` declares (`_declared_choices`, same
      check): `entry_zone="banana"` does not crash -- `PullbackContinuation` dispatches by
      equality and falls through to the `ema_band` branch -- so the operator reads another
      rule's numbers under the name they typed, and `rules promote` re-runs that same stored row
      toward `paper`/`live`. Likewise a `signal_patterns` name the rule can never match;
    - `Infinity`/`NaN` (`_nonfinite_params`), which `json.loads` accepts, which `Dca`'s
      "budget must be positive" guard passes, and which no backtest can report on;
    - a kwarg the rule constructs with but does NOT persist in `describe()["params"]`
      (`PullbackContinuation(granularity=...)` -- accepted, dropped, and rebuilt at the default,
      so the backtest would silently measure a rule on a different candle series).

    Every one of those is read off the rule itself -- its signature, its defaults, its
    annotations, its `describe()` -- so a kind that changes a field's type, gains a
    `target_method` branch or starts persisting a param needs no change here.

    What is NOT checked here is whether a value makes economic sense (a negative
    `atr_stop_mult`, say). That is the rule's own judgement to make, in its constructor, where
    every caller gets it -- `Dca` already does. Re-stating it in a CLI command would be exactly
    the second, drifting copy this command is otherwise careful not to create, and a `candidate`
    rule with a nonsense parameter answers for itself in the backtest it must pass.

    `--product` is validated exactly as `rules seed --products` is (`parse_products_option`,
    rails 18/19): a futures contract, an equity hash, a pair settling outside
    `settlement_currencies`, or a lowercase typo is refused here, named, with no row written.

    **Duplicates are allowed** -- comparing two parameter sets for the same (kind, product) is
    the entire point, so this takes none of `seed`'s idempotency skip. Any existing rules for
    that pair are REPORTED with their ids and statuses instead, so an operator who now has
    several is not surprised by them at backtest time.

    **A product outside `config.allowlist` is allowed and said out loud, not refused.**
    Backtesting an asset before deciding whether to admit it is the intended workflow; rail 1
    still stands between a non-allowlisted asset and any order, and a `candidate` rule never
    trades regardless.

    Read-only w.r.t. the exchange: no network call, no broker, no confirmation gate -- it only
    writes one local `rules` row, exactly like `rules seed`.
    """
    try:
        add_rule_row(
            _open_repo(ctx),
            _load_cfg(ctx),
            kind=kind,
            product=product,
            params_json=params_json,
            now_ts=int(time.time()),
            echo=click.echo,
            echo_err=lambda message: click.echo(message, err=True),
        )
    except RulesUsageError as exc:
        raise click.BadParameter(str(exc), param_hint=exc.param_hint) from exc
    except RulesRefused:
        ctx.exit(1)


def add_rule_row(
    repo: Repository,
    config: Any,
    *,
    kind: str,
    product: str,
    params_json: str | None,
    now_ts: int,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> RulesOutcome:
    """THE `rules add` service: every validation the CLI command performs, the single
    `insert_rule`, and the confirmation lines -- the exact body the command ran, echoed
    through the injected sinks in the exact order it printed them, so the CLI is
    byte-compatible and the strategy console's add form renders the SAME messages.

    The usage-error shapes raise `RulesUsageError` (message + param_hint: the CLI renders
    `click.BadParameter`, the form renders `Error: ...`); the refusal shapes echo their
    `Error: ...` lines to `echo_err` and raise `RulesRefused`. Nothing is ever written
    before every check has passed."""
    # Everything below this line VALIDATES; the single `insert_rule` is the last statement that
    # can run. A refusal after a partial write would leave a row the operator was told did not
    # exist -- and rows are what the agent polls.
    try:
        # `settlement_is_fatal` stays at the default, as in `rules seed` and for the same
        # reason: this command WRITES a row the agent then polls every cycle, so a product the
        # rails veto forever is not a lesser problem than a typo, only a quieter one.
        product_list, _ = parse_products_option(product, config)
    except ValueError as exc:
        raise RulesUsageError(str(exc), param_hint="--product") from exc
    if len(product_list) != 1:
        raise RulesUsageError(
            f"expects exactly ONE product id, got {len(product_list)} ({product!r}) -- one "
            f"invocation inserts one rule, and the `rules backtest <id>` it prints names one id",
            param_hint="--product",
        )
    product_id = product_list[0]

    rule_cls = agent.RULE_REGISTRY.get(kind)
    if rule_cls is None:
        echo_err(f"Error: unknown rule kind {kind!r}; known kinds: {sorted(agent.RULE_REGISTRY)!r}")
        raise RulesRefused(f"unknown rule kind {kind!r}")

    # "flag absent" and "flag given but empty" are different intentions, and only `is None`
    # tells them apart. An empty string is what a shell hands over when the proposal plumbing
    # misfires -- `--params "$(jq -c .params proposal.json)"` yields `""` when the key is
    # missing or jq errors -- and reading that as "the kind's own defaults" is the worst
    # available outcome: an id is printed, the operator backtests it, and the numbers they read
    # belong to the stock rule rather than to the proposal they believe they measured.
    if params_json is not None and not params_json.strip():
        raise RulesUsageError(
            "was given as an EMPTY string, which is not the same as omitting the flag. This is "
            'almost always a shell accident (`--params "$(jq -c .params proposal.json)"` '
            "yields an empty string when the key is missing or jq fails), and taking it as "
            '"use the defaults" would print a rule id for a row that is NOT the parameter set '
            "you meant to measure. Pass '{}' to ask for the kind's defaults deliberately, or "
            "omit --params entirely.",
            param_hint="--params",
        )

    try:
        supplied: Any = json.loads(params_json) if params_json is not None else {}
    except json.JSONDecodeError as exc:
        raise RulesUsageError(f"not valid JSON ({exc})", param_hint="--params") from exc
    if not isinstance(supplied, dict):
        raise RulesUsageError(
            f"must be a JSON object of constructor kwargs, got {type(supplied).__name__}",
            param_hint="--params",
        )

    # One source of truth for the product. Silently letting `--params` win would print one id
    # and store another, and the stored one is the one that trades.
    if "product_id" in supplied and supplied["product_id"] != product_id:
        raise RulesUsageError(
            f"names product_id {supplied['product_id']!r}, which disagrees with --product "
            f"{product_id!r}; pass the product once, via --product",
            param_hint="--params",
        )

    mismatches = _param_type_mismatches(kind, rule_cls, supplied)
    if mismatches:
        quotable = sorted(agent.coerced_param_keys(kind))
        echo_err(
            f"Error: {kind} cannot use these params:\n"
            + "\n".join(f"       - {problem}" for problem in mismatches)
            + f"\n       (a quoted value is right only for {quotable!r} -- keel converts "
            f"those on the way in)"
        )
        raise RulesRefused(f"{kind} cannot use these params")

    try:
        rule = agent.build_rule_from_params(kind, {**supplied, "product_id": product_id})
    except TypeError as exc:
        # An unknown/misspelled kwarg: the constructor names it, we name the alternatives.
        echo_err(
            f"Error: {kind} does not accept these params: {exc}\n"
            f"       accepted params: {_accepted_params(rule_cls)!r}"
        )
        raise RulesRefused(f"{kind} does not accept these params") from exc
    except (ValueError, ArithmeticError) as exc:
        # The rule's OWN validation (e.g. `Dca`: "cadence_days must be positive"), or a value
        # that is not the type the coercion boundary expects (`Decimal("x")` -> InvalidOperation,
        # an `ArithmeticError`). Either way the params cannot make a rule, so no row.
        echo_err(f"Error: {kind} rejected these params: {exc}")
        raise RulesRefused(f"{kind} rejected these params") from exc

    nonfinite = _nonfinite_params(rule.describe()["params"])
    if nonfinite:
        echo_err(
            f"Error: {kind} was given a value that is not a finite number, which no backtest "
            f"can report on:\n" + "\n".join(f"       - {problem}" for problem in nonfinite)
        )
        raise RulesRefused(f"{kind} was given a non-finite value")

    # `.describe()`'s params, JSON-plain -- the same thing `rules seed` stores, so the row is
    # exactly what `agent._build_rule` reconstructs. `product_id` is re-applied because not
    # every kind carries it in `describe()` (e.g. `TurtleBreakout` does not).
    params = _json_plain(rule.describe()["params"])
    params["product_id"] = product_id

    # A constructor kwarg that `describe()` does not carry would be accepted, then LOST: the row
    # rebuilds without it and the backtest measures a different rule than the one asked for.
    # `PullbackContinuation(granularity=ONE_DAY)` is exactly this -- it constructs, `params` has
    # no `granularity` key, and `_build_rule` rebuilds it at the ONE_HOUR default, on a different
    # candle series. Silently trading a rule the operator did not ask for is worse than a
    # refusal, so this is a refusal. Derived from the rule's own `describe()`, so a kind that
    # starts persisting a field needs no change here.
    dropped = sorted(set(supplied) - set(params))
    if dropped:
        echo_err(
            f"Error: {kind} accepts {dropped!r} but does not persist "
            f"{'them' if len(dropped) > 1 else 'it'} in the rule row, so the value would be "
            f"silently lost when the rule is rebuilt for a backtest or a cycle. Refusing rather "
            f"than storing a rule that is not the one you asked for."
        )
        raise RulesRefused(f"{kind} does not persist {dropped!r}")

    existing = [
        row
        for row in repo.get_rules()
        if row["kind"] == kind and (row["params"] or {}).get("product_id") == product_id
    ]

    rule_id = repo.insert_rule(kind, params, status="candidate", now_ts=now_ts)

    sink, recorded = _line_sink(echo)
    sink(f"added rule {rule_id}: {kind} {product_id} status=candidate")
    sink(f"  params: {json.dumps(params, sort_keys=True)}")
    if _asset_of(product_id) not in config.allowlist:
        sink(
            f"  note: {_asset_of(product_id)} is not in this deployment's allowlist "
            f"{sorted(config.allowlist)!r}. The rule is added and can be backtested; rail 1 "
            f"would veto any ORDER for it until the asset is admitted."
        )
    if existing:
        sink(
            f"  note: {len(existing)} other rule(s) already exist for {kind}/{product_id} -- "
            f"this is allowed (comparing parameter sets is the point), but backtest the right "
            f"one:"
        )
        for row in existing:
            sink(f"    [{row['id']}] status={row['status']} {_params_delta(row['params'], params)}")
    sink(f"next: keel rules backtest {rule_id}")
    return RulesOutcome(lines=tuple(recorded), rule_id=rule_id, new_status="candidate")


# -- parameter help, single-sourced from the classes (issue #390 C4 / PRD O8) ---------------------


@dataclass(frozen=True)
class ParamHelp:
    """One rule parameter's help, DERIVED from the class that defines it: the
    per-parameter docstring the class carries (`PARAM_DOCS`, added AT THE CLASS per O8 --
    never a second, drifting table), the constructor's own default, the type its annotation
    declares, the choices its own `Literal` states, and whether a QUOTED value is the right
    shape (the coercion boundary's own answer, `agent.coerced_param_keys`)."""

    name: str
    doc: str
    default: Any
    type_name: str
    choices: tuple[str, ...] | None
    quotable: bool


def _init_hints(rule_cls: type) -> dict[str, Any]:
    """The resolved type hints of `rule_cls.__init__`, or `{}` when they cannot be resolved
    -- the same resolution (and for the same reasons) `_declared_choices` documents."""
    try:
        # `type: ignore[misc]`: mypy rejects reading `__init__` off a value because a subclass
        # could carry an incompatible one -- which is precisely what is being introspected here.
        return get_type_hints(rule_cls.__init__)  # type: ignore[misc]
    except (NameError, TypeError):  # an unresolvable forward ref, or a slot wrapper __init__
        return {}


def _param_type(hint: Any, default: Any) -> tuple[str, tuple[str, ...] | None]:
    """(type_name, declared choices) for one param, from its annotation when it resolves and
    from its own default otherwise. PURE."""
    if get_origin(hint) is Literal:
        return "choice", tuple(str(value) for value in get_args(hint))
    if get_origin(hint) is tuple:
        element = next((arg for arg in get_args(hint) if arg is not Ellipsis), None)
        if element is not None and get_origin(element) is Literal:
            return "list", tuple(str(value) for value in get_args(element))
        return "list", None
    if hint is Granularity or isinstance(default, Granularity):
        return "granularity", None
    # An OPTIONAL param (`Decimal | None`, the #442 exit-policy knobs): the help names
    # the type a supplied value must be, not the NoneType of its off-default -- an
    # operator reading "NoneType" would reasonably conclude the param cannot be set.
    hint_args = get_args(hint)
    if type(None) in hint_args:
        member = next((arg for arg in hint_args if arg is not type(None)), None)
        if member is not None:
            return getattr(member, "__name__", type(default).__name__), None
    return type(default).__name__, None


def describe_params(kind: str) -> dict[str, ParamHelp]:
    """{param: help} for every operator-facing parameter of rule `kind`, by introspection.

    THE O8 parameter-level source: the doc comes from the class's own `PARAM_DOCS`, the
    default and the type from its constructor's signature/annotations, the choices from the
    `Literal` it declares (`_declared_choices`' own read), and `quotable` from
    `agent.coerced_param_keys` -- the coercion tables themselves -- so the help can never
    disagree with what `rules add` accepts. `product_id` (supplied by `--product`, one
    source of truth) and `name` (the kind's identity, not a knob) are excluded, and so is
    any constructor kwarg the kind does not PERSIST: the same `describe()["params"]` the
    add service's dropped-param refusal reads is the one source here, so the help can never
    offer a param the add flow would refuse as silently lost (pullback_continuation's
    `granularity` -- accepted by the constructor, never stored in the row). A kind's
    `PARAM_DOCS` entry for such a param stays where it is, documenting the class; the FORM
    simply does not offer it.

    Raises `ValueError` for a kind not in `RULE_REGISTRY`, naming the known kinds -- the
    same refusal `add_rule_row` makes."""
    rule_cls = agent.RULE_REGISTRY.get(kind)
    if rule_cls is None:
        raise ValueError(
            f"unknown rule kind {kind!r}; known kinds: {sorted(agent.RULE_REGISTRY)!r}"
        )
    # ONE source for "does the row persist this param?": the constructed rule's own
    # `describe()["params"]` -- exactly the dict `add_rule_row` stores and
    # `agent._build_rule` rebuilds from. A kind whose defaults cannot even construct
    # offers everything it accepts (the honest fallback; no registered kind hits it).
    try:
        persisted = set(
            agent.build_rule_from_params(kind, {"product_id": "BTC-USD"}).describe()["params"]
        )
    except (TypeError, ValueError, ArithmeticError):
        persisted = None
    docs = getattr(rule_cls, "PARAM_DOCS", {})
    hints = _init_hints(rule_cls)
    quotable = agent.coerced_param_keys(kind)
    params: dict[str, ParamHelp] = {}
    for name, param in inspect.signature(rule_cls).parameters.items():
        if name in ("self", "product_id", "name"):
            continue
        if persisted is not None and name not in persisted:
            continue
        type_name, choices = _param_type(hints.get(name), param.default)
        params[name] = ParamHelp(
            name=name,
            doc=docs.get(name, ""),
            default=param.default,
            type_name=type_name,
            choices=choices,
            quotable=name in quotable,
        )
    return params
