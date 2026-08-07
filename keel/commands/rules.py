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
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import time
from decimal import Decimal
from typing import Any

import click
from keel_core.telemetry import log_event

from keel import agent
from keel.commands._common import _load_cfg, _open_repo, with_disclaimer
from keel.commands._products import parse_products_option
from keel.data.repository import Repository

# Rail 1's OWN key function, imported rather than re-derived: `rules add`'s allowlist note is a
# preview of what rail 1 will say about the product, and a note that disagreed with the rail it
# is quoting would be worse than no note at all.
from keel.execution.guards import _asset as _asset_of
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


def _accepted_params(rule_cls: type) -> list[str]:
    """The kwargs `rule_cls`'s constructor accepts, for a refusal message to name.

    Read off the signature rather than listed anywhere, so it cannot go stale: an operator
    hand-copying a `params` object out of a scout proposal mistypes a key sooner or later, and
    `got an unexpected keyword argument 'cadance_days'` alone leaves them guessing at the
    spelling of the one they meant.
    """
    return [name for name in inspect.signature(rule_cls).parameters if name != "self"]


def _param_type_mismatches(kind: str, rule_cls: type, supplied: dict[str, Any]) -> list[str]:
    """Params whose JSON type cannot work with the rule, judged against the constructor default.

    Constructing the rule does NOT catch these, and the row they produce is the bad kind: it
    stores, it rebuilds, and it then raises `TypeError` deep inside the rule's arithmetic the
    first time a backtest calls `detect()` -- in a command nobody connects to the params they
    typed. Two of keel's four rule kinds (`RsiMeanReversion`, `PullbackContinuation`) are plain
    classes with no validation of their own, so construction is not a filter for them at all.
    Three shapes, each demonstrated to kill a backtest:

    - `{"oversold": "10.0"}` -- quoted, and `oversold` is a `float`: dies at `str < int`.
      Quoting is the likeliest typo in hand-copied JSON precisely BECAUSE it is correct for the
      `Decimal` params, so which params may be quoted is answered by `agent.coerced_param_keys`
      -- the coercion tables themselves -- rather than guessed at or re-listed here.
    - `{"lookback_days": 90.5}` (or `1e400`, which JSON parses to `inf`) -- `lookback_days`
      indexes a candle list, and `candles[-90.5:]` raises "slice indices must be integers".
      A whole number for a `float` default is the harmless direction and stays allowed.
    - `{"oversold": null}` -- no rule param has a `None` default, and the coercion boundary
      passes `None` through untouched, so it reaches the constructor intact.

    Judged against the type of the constructor's OWN default, so a rule that changes a field's
    type needs no change here. Nothing else is second-guessed: this is not a type checker, only
    the mismatches that survive construction and reliably kill a backtest.
    """
    coerced = agent.coerced_param_keys(kind)
    problems: list[str] = []
    for name, param in inspect.signature(rule_cls).parameters.items():
        if name not in supplied or name in coerced:
            continue
        default, value = param.default, supplied[name]
        if default is inspect.Parameter.empty:
            continue
        if value is None and default is not None:
            problems.append(f"{name}=null is not a value {kind} can use (default {default!r})")
        elif isinstance(default, str) and not isinstance(value, str):
            problems.append(f"{name}={value!r} should be a quoted string (default {default!r})")
        elif isinstance(default, (bool, int, float)) and isinstance(value, str):
            problems.append(
                f"{name}={value!r} is quoted, but {kind} wants a number here "
                f"(default {default!r})"
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
        elif isinstance(default, tuple) and default:
            problems.extend(_sequence_problems(name, default, value))
    return problems


def _is_whole_number(value: Any) -> bool:
    """A JSON int, and not a bool dressed as one (`True` is an `int` to `isinstance`)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _sequence_problems(name: str, default: tuple[Any, ...], value: Any) -> list[str]:
    """Why `value` cannot stand in for a tuple param such as `ema_periods`/`signal_patterns`.

    `build_rule_from_params` converts these with a blind `tuple(value)`, which is total and
    therefore silent about two shapes that then kill a backtest:

    - `"abc"` -- `tuple("abc")` CHAR-SPLITS into `('a', 'b', 'c')`, a plausible-looking
      three-EMA fan made of letters;
    - `["8", "20", "50"]` -- quoted numbers survive as strings, and `ema()`'s
      `2.0 / (period + 1)` then raises `TypeError: can only concatenate str (not "int") to str`.

    Element type is taken from the constructor's own default tuple, so a param that changes
    from ints to something else needs no change here. An empty list is refused too: it builds a
    rule with no EMAs at all, which never signals and reads as a rule that simply does not work.
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
    return []


def _nonfinite_params(params: dict[str, Any]) -> list[str]:
    """Param names holding `Infinity`/`NaN` after coercion -- numbers that got away.

    `json.loads` accepts JSON's non-standard `Infinity`/`NaN` literals, and the guards a rule
    does have let them past: `Decimal('Infinity') <= 0` is `False`, so `Dca`'s "budget must be
    positive" check passes an infinite budget. Checked on the CONSTRUCTED params rather than on
    the input, so it catches such a value whichever way it arrived -- as a JSON float or as a
    quoted `"Infinity"` through the `Decimal` coercion.

    Beyond the arithmetic, a non-finite value makes the stored row indefensible as data:
    `json.dumps` writes a bare `Infinity`, which is not valid JSON and which no strict reader
    (any consumer of this DB that is not Python) can parse back.
    """
    bad: list[str] = []
    for name, value in params.items():
        if isinstance(value, float) and not math.isfinite(value):
            bad.append(f"{name}={value!r}")
        elif isinstance(value, Decimal) and not value.is_finite():
            bad.append(f"{name}={value!r}")
    return bad


def _params_delta(existing: dict[str, Any], added: dict[str, Any]) -> str:
    """How an already-stored rule's params differ from the row just added, as a short `k=v` list.

    Only the DIFFERENCE, because two rows for one (kind, product) are the comparison the command
    exists to enable: they agree on eleven params and differ on the one under test. Printing both
    in full (as `rules list` does) would make the operator diff them by eye at precisely the
    moment they have to pick the right id for `rules backtest`. Both sides are the JSON-plain
    stored form, so `'2'` vs `2` is a real difference and is shown as one.
    """
    keys = sorted(set(existing) | set(added))
    diffs = [
        f"{key}={existing.get(key, '<absent>')!r}"
        for key in keys
        if existing.get(key, "<absent>") != added.get(key, "<absent>")
    ]
    return ", ".join(diffs) if diffs else "(identical params)"


@rules_group.command("add")
@click.option("--kind", required=True, help="Rule kind (one of agent.RULE_REGISTRY).")
@click.option("--product", required=True, help="The single product id the rule trades.")
@click.option(
    "--params",
    "params_json",
    default=None,
    help="Constructor params as a JSON object, e.g. '{\"entry_lookback\": 55}'. Omit to take "
    "the kind's own defaults (a baseline row to compare a proposal against).",
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
    unknown kwarg, or a value its own validation rejects, e.g. `Dca` on `cadence_days <= 0` --
    this refuses too, names the problem, and writes NOTHING. What lands in the row is
    `.describe()`'s params, not the raw JSON, so the stored row is exactly what
    `agent._build_rule` can reconstruct: a row that stores but cannot rebuild is worse than a
    refusal, because it fails later, inside a backtest or an agent cycle. Construction alone
    is NOT sufficient on its own -- two of the four rule kinds validate nothing in their
    constructors -- so three further classes of bad params are refused here, each because it
    writes a row that stores, rebuilds, and *then* fails:

    - a value of the wrong JSON type for the field (`_param_type_mismatches`): a quoted number,
      a fraction where the rule counts bars, a `null`, a char-splittable string or a list of
      quoted numbers for `ema_periods`. These die inside `detect()`'s arithmetic, mid-backtest;
    - `Infinity`/`NaN` (`_nonfinite_params`), which `json.loads` accepts, which `Dca`'s
      "budget must be positive" guard passes, and which no backtest can report on;
    - a kwarg the rule constructs with but does NOT persist in `describe()["params"]`
      (`PullbackContinuation(granularity=...)` -- accepted, dropped, and rebuilt at the default,
      so the backtest would silently measure a rule on a different candle series).

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
    repo = _open_repo(ctx)

    # Everything below this line VALIDATES; the single `insert_rule` is the last statement that
    # can run. A refusal after a partial write would leave a row the operator was told did not
    # exist -- and rows are what the agent polls.
    config = _load_cfg(ctx)
    try:
        # `settlement_is_fatal` stays at its default, as in `rules seed` and for the same
        # reason: this command WRITES a row the agent then polls every cycle, so a product the
        # rails veto forever is not a lesser problem than a typo, only a quieter one.
        product_list, _ = parse_products_option(product, config)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--product") from exc
    if len(product_list) != 1:
        raise click.BadParameter(
            f"expects exactly ONE product id, got {len(product_list)} ({product!r}) -- one "
            f"invocation inserts one rule, and the `rules backtest <id>` it prints names one id",
            param_hint="--product",
        )
    product_id = product_list[0]

    rule_cls = agent.RULE_REGISTRY.get(kind)
    if rule_cls is None:
        click.echo(
            f"Error: unknown rule kind {kind!r}; known kinds: {sorted(agent.RULE_REGISTRY)!r}",
            err=True,
        )
        ctx.exit(1)
        return

    try:
        supplied: Any = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"not valid JSON ({exc})", param_hint="--params") from exc
    if not isinstance(supplied, dict):
        raise click.BadParameter(
            f"must be a JSON object of constructor kwargs, got {type(supplied).__name__}",
            param_hint="--params",
        )

    # One source of truth for the product. Silently letting `--params` win would print one id
    # and store another, and the stored one is the one that trades.
    if "product_id" in supplied and supplied["product_id"] != product_id:
        raise click.BadParameter(
            f"names product_id {supplied['product_id']!r}, which disagrees with --product "
            f"{product_id!r}; pass the product once, via --product",
            param_hint="--params",
        )

    mismatches = _param_type_mismatches(kind, rule_cls, supplied)
    if mismatches:
        quotable = sorted(agent.coerced_param_keys(kind))
        click.echo(
            f"Error: {kind} cannot use these params:\n"
            + "\n".join(f"       - {problem}" for problem in mismatches)
            + f"\n       (a quoted value is right only for {quotable!r} -- keel converts "
            f"those on the way in)",
            err=True,
        )
        ctx.exit(1)
        return

    try:
        rule = agent.build_rule_from_params(kind, {**supplied, "product_id": product_id})
    except TypeError as exc:
        # An unknown/misspelled kwarg: the constructor names it, we name the alternatives.
        click.echo(
            f"Error: {kind} does not accept these params: {exc}\n"
            f"       accepted params: {_accepted_params(rule_cls)!r}",
            err=True,
        )
        ctx.exit(1)
        return
    except (ValueError, ArithmeticError) as exc:
        # The rule's OWN validation (e.g. `Dca`: "cadence_days must be positive"), or a value
        # that is not the type the coercion boundary expects (`Decimal("x")` -> InvalidOperation,
        # an `ArithmeticError`). Either way the params cannot make a rule, so no row.
        click.echo(f"Error: {kind} rejected these params: {exc}", err=True)
        ctx.exit(1)
        return

    nonfinite = _nonfinite_params(rule.describe()["params"])
    if nonfinite:
        click.echo(
            f"Error: {kind} was given a value that is not a finite number: "
            f"{', '.join(nonfinite)}. No backtest can report on it, and the stored row would "
            f"not be valid JSON.",
            err=True,
        )
        ctx.exit(1)
        return

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
        click.echo(
            f"Error: {kind} accepts {dropped!r} but does not persist "
            f"{'them' if len(dropped) > 1 else 'it'} in the rule row, so the value would be "
            f"silently lost when the rule is rebuilt for a backtest or a cycle. Refusing rather "
            f"than storing a rule that is not the one you asked for.",
            err=True,
        )
        ctx.exit(1)
        return

    existing = [
        row
        for row in repo.get_rules()
        if row["kind"] == kind and (row["params"] or {}).get("product_id") == product_id
    ]

    rule_id = repo.insert_rule(kind, params, status="candidate", now_ts=int(time.time()))

    click.echo(f"added rule {rule_id}: {kind} {product_id} status=candidate")
    click.echo(f"  params: {json.dumps(params, sort_keys=True)}")
    if _asset_of(product_id) not in config.allowlist:
        click.echo(
            f"  note: {_asset_of(product_id)} is not in this deployment's allowlist "
            f"{sorted(config.allowlist)!r}. The rule is added and can be backtested; rail 1 "
            f"would veto any ORDER for it until the asset is admitted."
        )
    if existing:
        click.echo(
            f"  note: {len(existing)} other rule(s) already exist for {kind}/{product_id} -- "
            f"this is allowed (comparing parameter sets is the point), but backtest the right "
            f"one:"
        )
        for row in existing:
            click.echo(
                f"    [{row['id']}] status={row['status']} {_params_delta(row['params'], params)}"
            )
    click.echo(f"next: keel rules backtest {rule_id}")
