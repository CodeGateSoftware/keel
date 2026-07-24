"""`keel rules` -- the rule lifecycle (candidate -> paper -> live -> disabled).

Read-only against the exchange: every command here backtests against locally-cached candles or
mutates the `rules` table, with no network call and no broker. It needs the DB/config seams from
`keel.commands._common` and the shared product derivation from `keel.commands._products`, but
never the broker seam.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

import click
from keel_core.telemetry import log_event

from keel import agent
from keel.commands._common import _load_cfg, _open_repo, with_disclaimer
from keel.commands._products import _default_sim_products
from keel.data.repository import Repository
from keel.strategy import backtest as backtest_mod
from keel.strategy import promotion as promotion_mod
from keel.types import Granularity

logger = logging.getLogger(__name__)

# `rules demote` steps a rule back one lifecycle stage; `disabled` is terminal (see
# `strategy.promotion`'s own `_PROMOTE_NEXT` docstring) and so is not a demote target.
_DEMOTE_PREV: dict[str, str] = {"live": "paper", "paper": "candidate"}


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
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the backtest/promotion gate and advance the rule one lifecycle step directly "
    "(candidate->paper, or paper->live). For a deliberate, un-gated paper-forward start when "
    "a rule's backtest can never reach the min_trades floor -- analogous to `rules seed "
    "--status live`'s gate bypass.",
)
@click.pass_context
@with_disclaimer
def rules_promote(ctx: click.Context, rule_id: int, granularity: str | None, force: bool) -> None:
    """Re-run a rule's backtest and advance its lifecycle status if it clears the floor.

    With `--force`, SKIPS the backtest/gate entirely and advances the rule one lifecycle step
    directly. This exists for a low-frequency trend-follower (or any rule) whose backtest can
    NEVER produce `min_trades` (default 100) trades -- without a bypass such a rule could never
    reach `paper` status, yet the whole point of a paper-forward is to accrue the out-of-sample
    trades the backtest can't. Use deliberately and audit the (loud) warning this prints.
    """
    repo = _open_repo(ctx)
    row = _require_rule_row(ctx, repo, rule_id)

    if force:
        target = promotion_mod.next_status(row["status"])
        if target is None:
            click.echo(
                f"rule {rule_id} ({row['kind']}): already at {row['status']!r}; "
                "nothing to promote"
            )
            return
        repo.update_rule_status(rule_id, target)
        click.echo(
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
        click.echo(f"rule {rule_id} ({row['kind']}): status -> {target}")
        return

    config = _load_cfg(ctx)
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

    Read-only w.r.t. the exchange: no network call, no confirmation gate -- it only ever
    writes local
    `rules` rows, exactly like `rules promote`/`demote`/`disable`.
    """
    repo = _open_repo(ctx)
    now_ts = int(time.time())

    if products:
        product_list = [p.strip() for p in products.split(",") if p.strip()]
    else:
        config = _load_cfg(ctx)
        product_list = _default_sim_products(config)

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

    existing_keys = {
        (row["kind"], (row["params"] or {}).get("product_id")) for row in repo.get_rules()
    }

    seeded: list[str] = []
    skipped: list[str] = []
    for kind in kind_list:
        rule_cls = agent.RULE_REGISTRY[kind]
        for product in product_list:
            label = f"{kind}:{product}"
            if not force and (kind, product) in existing_keys:
                skipped.append(label)
                continue
            rule = rule_cls(product_id=product)
            params = _json_plain(rule.describe()["params"])
            params["product_id"] = product
            repo.insert_rule(kind, params, status=status, now_ts=now_ts)
            seeded.append(label)

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
