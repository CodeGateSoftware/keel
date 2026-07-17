"""keel command-line interface (P3 Task 9).

Wires the merged Phase 1-3 modules into a `click` CLI: `db import` (`data.csv_import.import_dir`),
`monitor` (`data.market_feed`), `agent` (`agent.run_once`/`agent.loop`), `rules
list|backtest|promote|demote|disable` (`data.repository` + `strategy.backtest`/`promotion`),
`pnl` (`analysis.pnl`), `kill`/`resume` (the `agent_state` kill-switch), and a Phase-4 `insights`
stub.

**Dangerous commands are gated.** Per the main spec §14 and `security.authz`, only
`{arm_bypass, raise_caps, disable_killswitch, unlock_vault}` require the passphrase gate. In this
CLI's surface that's `agent --bypass` (`arm_bypass`) and `resume` (`disable_killswitch`) --
`kill` (engaging the kill-switch) is a *safe* action and always allowed; every other command here
is read-only or a local rules-table/DB mutation with no live-trading blast radius, so per the plan
("read-only commands require no passphrase") they are not gated. There is no CLI surface for
`raise_caps`/`unlock_vault` in this task -- caps are config-file-only (no runtime override exists
in the merged `execution.guards`/`config` modules) and the vault (`security.secrets`) has no CLI
command in this task's scope.

**No interactive hangs in tests.** `--passphrase` may be passed explicitly; if omitted, it is only
read via an interactive `click.prompt` when stdin is a real TTY (`_resolve_passphrase`) --
non-interactive invocations (like `CliRunner`) get an empty passphrase, which fails the gate
closed rather than blocking on stdin.

**Disclaimer.** Every command prints the halal + not-financial/religious-advice disclaimer
footer (`with_disclaimer`, a decorator applied to every command's callback) -- always, even when
the command errors out or is refused by the authz gate.

**No live network in tests.** `_build_broker` is the one seam that would construct a real
`CoinbaseClient` (from `.env`/vault secrets via `coinbase.rest.RESTClient`); tests monkeypatch it
to inject a fake broker instead, exactly like `tests/test_agent.py`'s `FakeBroker`.
"""

from __future__ import annotations

import functools
import sys
import time
from dataclasses import replace
from decimal import Decimal
from typing import Any

import click

from keel import agent
from keel.analysis import pnl as pnl_analysis
from keel.config import Config, load_config
from keel.data import market_feed
from keel.data.csv_import import import_dir
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.security import authz
from keel.strategy import backtest as backtest_mod
from keel.strategy import promotion as promotion_mod
from keel.types import Granularity

DISCLAIMER = (
    "keel is a personal tool, not financial advice and not religious (Shariah) advice. "
    "Consult a qualified financial advisor and a knowledgeable scholar before trading. "
    "You are solely responsible for your own trading decisions."
)

DEFAULT_DB_PATH = "keel.db"
DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_AUTHZ_PATH = "authz.json"

# The dangerous-action names this CLI's gated commands map to (see module docstring).
_ARM_BYPASS = "arm_bypass"
_DISABLE_KILLSWITCH = "disable_killswitch"

# `rules demote` steps a rule back one lifecycle stage; `disabled` is terminal (see
# `strategy.promotion`'s own `_PROMOTE_NEXT` docstring) and so is not a demote target.
_DEMOTE_PREV: dict[str, str] = {"live": "paper", "paper": "candidate"}


# -- disclaimer -------------------------------------------------------------------------------


def _print_disclaimer() -> None:
    click.echo("")
    click.echo(DISCLAIMER)


def with_disclaimer(f: Any) -> Any:
    """Print the disclaimer footer after `f` runs, whether it succeeds, errors, or aborts."""

    @functools.wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return f(*args, **kwargs)
        finally:
            _print_disclaimer()

    return wrapper


# -- passphrase resolution (no interactive hangs under CliRunner) -----------------------------


def _resolve_passphrase(passphrase: str | None) -> str:
    """`passphrase` if given; else an interactive prompt on a real TTY; else `""` (fails closed).

    A non-interactive invocation (tests, scripts, CI) never blocks on stdin -- it simply gets an
    empty passphrase, which `authz.verify`/`authz.require` correctly reject.
    """
    if passphrase:
        return passphrase
    if sys.stdin is not None and sys.stdin.isatty():
        return click.prompt("Passphrase", hide_input=True)
    return ""


def _require_authz(ctx: click.Context, action: str, passphrase: str | None) -> None:
    """Enforce the authz gate for `action`, aborting the command (exit 1) on denial."""
    resolved = _resolve_passphrase(passphrase)
    try:
        authz.require(action, resolved, path=ctx.obj["authz_path"])
    except authz.AuthzError as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)


# -- DB / config / broker construction ---------------------------------------------------------


def _open_repo(ctx: click.Context) -> Repository:
    conn = connect(ctx.obj["db_path"])
    migrate(conn)
    return Repository(conn)


def _load_cfg(ctx: click.Context) -> Config:
    return load_config(ctx.obj["config_path"])


def _build_broker(config: Config) -> Any:  # pragma: no cover -- exercised only against fakes
    """Construct the real, network-talking `CoinbaseClient`. Tests monkeypatch this function."""
    from coinbase.rest import RESTClient

    from keel.config import load_secrets
    from keel.data.cb_client import CoinbaseClient

    secrets = load_secrets()
    transport = RESTClient(api_key=secrets.get("api_key"), api_secret=secrets.get("api_secret"))
    return CoinbaseClient(transport)


# -- root group ---------------------------------------------------------------------------------


@click.group()
@click.option(
    "--db", "db_path", default=DEFAULT_DB_PATH, show_default=True, help="SQLite DB path."
)
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    help="config.yaml path.",
)
@click.option(
    "--authz-path",
    default=DEFAULT_AUTHZ_PATH,
    show_default=True,
    help="Passphrase-gate state file (see `keel.security.authz`).",
)
@click.pass_context
def cli(ctx: click.Context, db_path: str, config_path: str, authz_path: str) -> None:
    """keel: an offline-first, halal, guard-railed Coinbase auto-trading agent."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    ctx.obj["config_path"] = config_path
    ctx.obj["authz_path"] = authz_path


# -- db import ------------------------------------------------------------------------------


@cli.group("db")
def db_group() -> None:
    """Local data import/maintenance commands."""


@db_group.command("import")
@click.argument("dir_path", type=click.Path(exists=True, file_okay=False))
@click.pass_context
@with_disclaimer
def db_import(ctx: click.Context, dir_path: str) -> None:
    """Import every `*.csv` Coinbase export in DIR_PATH (read-only w.r.t. the exchange)."""
    repo = _open_repo(ctx)
    result = import_dir(dir_path, repo)
    click.echo(f"imported={result.imported} skipped={result.skipped}")
    for warning in result.warnings:
        click.echo(f"  warning: {warning}")


# -- monitor ------------------------------------------------------------------------------


@cli.command()
@click.option("--loop", is_flag=True, default=False, help="Poll repeatedly instead of once.")
@click.option(
    "--interval",
    "interval_sec",
    type=float,
    default=None,
    help="Seconds between polls with --loop (default: config auto_trade.interval_sec).",
)
@click.option(
    "--max-cycles",
    type=int,
    default=None,
    help="Stop --loop after N cycles (default: run until interrupted).",
)
@click.pass_context
@with_disclaimer
def monitor(
    ctx: click.Context, loop: bool, interval_sec: float | None, max_cycles: int | None
) -> None:
    """Poll fresh candles for every allowlisted product (read-only)."""
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    broker = _build_broker(config)
    products = [f"{asset}-USD" for asset in config.allowlist]
    granularities = list(config.market_data.granularities)
    interval = interval_sec if interval_sec is not None else config.auto_trade.interval_sec

    cycles = 0
    while True:
        now_ts = int(time.time())
        written = market_feed.poll_once(broker, repo, products, granularities, now_ts=now_ts)
        click.echo(f"[{now_ts}] polled {written} new candle row(s) across {products}")
        cycles += 1
        if not loop or (max_cycles is not None and cycles >= max_cycles):
            break
        time.sleep(interval)


# -- agent ------------------------------------------------------------------------------


def _print_loop_result(result: agent.LoopResult) -> None:
    if result.skipped:
        click.echo(f"[{result.ts}] skipped: {result.skip_reason}")
        return
    entered = sum(1 for r in result.enter_results if r.placed)
    exited = sum(1 for r in result.exit_results if r.placed)
    click.echo(
        f"[{result.ts}] mode={result.mode} polled={result.polled} "
        f"products={result.products} stale={result.stale_products} "
        f"signals={len(result.enter_signals)} entered={entered} exited={exited}"
    )


@cli.command()
@click.option(
    "--loop", is_flag=True, default=False, help="Run the scheduled loop, not one cycle."
)
@click.option(
    "--confirm", "mode", flag_value="confirm", default=True, help="Confirm mode (default, safe)."
)
@click.option(
    "--bypass", "mode", flag_value="bypass", help="Bypass mode (dangerous; requires --passphrase)."
)
@click.option(
    "--interval",
    "interval_sec",
    type=float,
    default=None,
    help="Seconds between cycles with --loop (default: config auto_trade.interval_sec).",
)
@click.option(
    "--max-cycles",
    type=int,
    default=None,
    help="Stop --loop after N cycles (default: run until interrupted).",
)
@click.option("--passphrase", default=None, help="Required to arm --bypass.")
@click.pass_context
@with_disclaimer
def agent_cmd(
    ctx: click.Context,
    loop: bool,
    mode: str,
    interval_sec: float | None,
    max_cycles: int | None,
    passphrase: str | None,
) -> None:
    """Run the agent loop (confirm mode by default; every order is still hard-rail-guarded)."""
    if mode == "bypass":
        _require_authz(ctx, _ARM_BYPASS, passphrase)

    config = _load_cfg(ctx)
    config = replace(config, auto_trade=replace(config.auto_trade, mode=mode))
    repo = _open_repo(ctx)
    broker = _build_broker(config)

    if not loop:
        _print_loop_result(agent.run_once(broker, repo, config, now_ts=int(time.time())))
        return

    interval = interval_sec if interval_sec is not None else config.auto_trade.interval_sec

    def stop_flag(_count: list[int] = [0]) -> bool:  # noqa: B006 - intentional mutable counter
        if max_cycles is not None and _count[0] >= max_cycles:
            return True
        _count[0] += 1
        return False

    for result in agent.loop(broker, repo, config, interval, stop_flag):
        _print_loop_result(result)


# -- rules ------------------------------------------------------------------------------


@cli.group("rules")
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


def _require_rule_row(ctx: click.Context, repo: Repository, rule_id: int) -> dict[str, Any]:
    rows = {row["id"]: row for row in repo.get_rules()}
    row = rows.get(rule_id)
    if row is None:
        click.echo(f"Error: no rule with id {rule_id}", err=True)
        ctx.exit(1)
    assert row is not None  # narrows for type-checkers; ctx.exit() above raises SystemExit
    return row


def _resolve_granularity(rule: Any, granularity_opt: str | None) -> Granularity | None:
    if granularity_opt:
        return Granularity(granularity_opt)
    for attr in ("granularity", "timeframe"):
        value = getattr(rule, attr, None)
        if value is not None:
            return value
    return None


def _run_backtest(
    ctx: click.Context, repo: Repository, rule: Any, granularity_opt: str | None
) -> backtest_mod.BacktestResult:
    product_id = getattr(rule, "product_id", None)
    if product_id is None:
        click.echo("Error: rule has no product_id to backtest against", err=True)
        ctx.exit(1)
    granularity = _resolve_granularity(rule, granularity_opt)
    if granularity is None:
        click.echo("Error: could not determine a granularity; pass --granularity", err=True)
        ctx.exit(1)
    candles = repo.get_candles(product_id, granularity)
    return backtest_mod.backtest(rule, candles)


@rules_group.command("backtest")
@click.argument("rule_id", type=int)
@click.option(
    "--granularity", default=None, help="Override the candle granularity (default: the rule's own)."
)
@click.pass_context
@with_disclaimer
def rules_backtest(ctx: click.Context, rule_id: int, granularity: str | None) -> None:
    """Backtest a stored rule against its historical candles (read-only)."""
    repo = _open_repo(ctx)
    row = _require_rule_row(ctx, repo, rule_id)
    rule = agent._build_rule(row)
    stats = _run_backtest(ctx, repo, rule, granularity)
    click.echo(
        f"rule {rule_id} ({row['kind']}): n_trades={stats.n_trades} "
        f"win_rate={stats.win_rate:.2%} expectancy={stats.expectancy} "
        f"profit_factor={stats.profit_factor} max_drawdown={stats.max_drawdown}"
    )


@rules_group.command("promote")
@click.argument("rule_id", type=int)
@click.option(
    "--granularity", default=None, help="Override the candle granularity for the backtest."
)
@click.pass_context
@with_disclaimer
def rules_promote(ctx: click.Context, rule_id: int, granularity: str | None) -> None:
    """Re-run a rule's backtest and advance its lifecycle status if it clears the floor."""
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    row = _require_rule_row(ctx, repo, rule_id)
    rule = agent._build_rule(row)
    stats = _run_backtest(ctx, repo, rule, granularity)

    promo_cfg = promotion_mod.PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )
    new_status = promotion_mod.transition(repo, row["kind"], stats, promo_cfg)
    click.echo(f"rule {rule_id} ({row['kind']}): status -> {new_status}")


@rules_group.command("demote")
@click.argument("rule_id", type=int)
@click.pass_context
@with_disclaimer
def rules_demote(ctx: click.Context, rule_id: int) -> None:
    """Manually step a rule's lifecycle status back one stage (live->paper->candidate)."""
    repo = _open_repo(ctx)
    row = _require_rule_row(ctx, repo, rule_id)
    prev = _DEMOTE_PREV.get(row["status"])
    if prev is None:
        click.echo(
            f"rule {rule_id} ({row['kind']}): already at {row['status']!r}; nothing to demote"
        )
        return
    repo.update_rule_status(rule_id, prev)
    click.echo(f"rule {rule_id} ({row['kind']}): status -> {prev}")


@rules_group.command("disable")
@click.argument("rule_id", type=int)
@click.pass_context
@with_disclaimer
def rules_disable(ctx: click.Context, rule_id: int) -> None:
    """Disable a rule (terminal status; it will never trade again)."""
    repo = _open_repo(ctx)
    row = _require_rule_row(ctx, repo, rule_id)
    repo.update_rule_status(rule_id, "disabled")
    click.echo(f"rule {rule_id} ({row['kind']}): status -> disabled")


# -- pnl ------------------------------------------------------------------------------


def _parse_marks(raw_marks: tuple[str, ...]) -> dict[str, Decimal]:
    marks: dict[str, Decimal] = {}
    for entry in raw_marks:
        asset, _, price = entry.partition("=")
        if not price:
            raise click.BadParameter(f"expected ASSET=PRICE, got {entry!r}")
        marks[asset] = Decimal(price)
    return marks


@cli.command()
@click.option("--asset", default=None, help="Scope the report to one asset.")
@click.option(
    "--mark",
    "raw_marks",
    multiple=True,
    help="ASSET=PRICE mark for unrealized P&L (repeatable, e.g. --mark BTC=65000).",
)
@click.pass_context
@with_disclaimer
def pnl(ctx: click.Context, asset: str | None, raw_marks: tuple[str, ...]) -> None:
    """Realized + unrealized FIFO P&L report from imported transactions (read-only)."""
    repo = _open_repo(ctx)
    transactions = repo.get_transactions(asset)
    marks = _parse_marks(raw_marks)

    if asset is not None:
        realized = pnl_analysis.realized_pnl(transactions, asset)
        pos = pnl_analysis.position(transactions, asset)
        click.echo(f"{asset}: realized={realized} open_qty={pos.qty} avg_cost={pos.avg_cost}")
        if asset in marks:
            unrealized = (marks[asset] - pos.avg_cost) * pos.qty
            click.echo(f"{asset}: mark={marks[asset]} unrealized={unrealized}")
        return

    total_realized = pnl_analysis.realized_pnl(transactions)
    click.echo(f"total realized P&L: {total_realized}")
    unrealized_by_asset = pnl_analysis.unrealized_pnl(transactions, marks) if marks else {}
    for a in sorted({tx["asset"] for tx in transactions}):
        pos = pnl_analysis.position(transactions, a)
        if not pos.qty:
            continue
        line = f"  {a}: open_qty={pos.qty} avg_cost={pos.avg_cost}"
        if a in unrealized_by_asset:
            line += f" unrealized={unrealized_by_asset[a]}"
        click.echo(line)


# -- kill / resume ------------------------------------------------------------------------------


@cli.command()
@click.pass_context
@with_disclaimer
def kill(ctx: click.Context) -> None:
    """Engage the kill-switch, halting all trading immediately. Always allowed (safe action)."""
    repo = _open_repo(ctx)
    repo.set_state("kill_switch", True)
    click.echo("kill-switch ENGAGED: all trading halted.")


@cli.command()
@click.option("--passphrase", default=None, help="Required to disengage the kill-switch.")
@click.pass_context
@with_disclaimer
def resume(ctx: click.Context, passphrase: str | None) -> None:
    """Disengage the kill-switch (dangerous: requires the authz passphrase)."""
    _require_authz(ctx, _DISABLE_KILLSWITCH, passphrase)
    repo = _open_repo(ctx)
    repo.set_state("kill_switch", False)
    click.echo("kill-switch disengaged: trading resumed.")


# -- insights (Phase 4 stub) ---------------------------------------------------------------------


@cli.command()
@click.pass_context
@with_disclaimer
def insights(ctx: click.Context) -> None:
    """Portfolio insights (Phase 4 -- not yet implemented)."""
    click.echo("insights: not yet implemented (see Phase 4 of the roadmap).")


if __name__ == "__main__":  # pragma: no cover
    cli()
