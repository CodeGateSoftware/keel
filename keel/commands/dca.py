"""`keel dca` -- the CLI front-end over `keel.commands.dca_plan` (the service).

Thin on purpose: parse options, run the `[Y]/[E]/[N]` loop, echo the service's lines. Every
figure, refusal and write lives in the service, which `keel/web/api.py` calls too.

**The terminal is load-bearing, the heavier gate is not** -- `keel/commands/journal.py`'s
reasoning. Writing `candidate` rules is what `keel rules add` does ungated: a candidate cannot
trade until `keel rules promote` clears it, so `_require_interactive_confirmation`'s typed `yes`
would be ceremony. But a prompt needs a human, so off a TTY this prints and writes nothing.

**Interactivity is decided FIRST, and once, and the database is opened accordingly** (coordinator
ruling R20). `_common._is_interactive()` is read exactly once, before anything touches the
database, and that one value drives both which opener runs and the rest of the command's
behaviour. Off a TTY the opener is `_common._open_repo_ro` -- a read-only `mode=ro` connection
that REFUSES a missing `--db` path or a stale schema rather than creating or migrating the file --
because either of those, under a plain read-write connection, would itself be a write. At a TTY
the opener is `_common._open_repo`, the ordinary read-write one `[Y]` needs to insert rows.

Both openers are reached as `_common.<name>(ctx)` -- module attribute access, never a name bound
by `from ... import ...` -- for the same reason `_common`'s own docstring gives for
`_is_interactive`: it is the one patch point a test can rebind no matter which module the calling
command lives in.
"""

from __future__ import annotations

import time
from decimal import Decimal

import click

from keel.commands import _common
from keel.commands._common import _bound_venue_or_default, _load_cfg, with_disclaimer
from keel.commands.assets import screen_product
from keel.commands.dca_plan import (
    DEFAULT_CADENCE_DAYS,
    DcaPlanError,
    DcaPlanRefused,
    apply_dca_plan,
    build_dca_plan,
    parse_plan_inputs,
    parse_weight,
    render_dca_plan,
)

#: The `[Y]/[E]/[N]` prompt's per-choice label, keyed by letter. One table, so the loop's prompt
#: line and its retry message cannot disagree about what each letter means.
_CHOICE_LABELS = {"Y": "[Y] Approve", "E": "[E] Edit weights", "N": "[N] Cancel"}


@click.group("dca")
def dca_group() -> None:
    """Plan a multi-asset DCA schedule (writes `candidate` rules only, on approval)."""


@dca_group.command("plan")
@click.option("--budget", required=True, help="Monthly budget in USD, e.g. 500.")
@click.option(
    "--buffer-pct",
    required=True,
    help="Fraction of the budget held back, in [0, 1): 0.1 holds back 10%.",
)
@click.option(
    "--cadence-days",
    type=int,
    default=DEFAULT_CADENCE_DAYS,
    show_default=True,
    help="Days between buys for every asset in the plan.",
)
@click.pass_context
@with_disclaimer
def dca_plan_cmd(ctx: click.Context, budget: str, buffer_pct: str, cadence_days: int) -> None:
    """Propose a DCA schedule over the admitted, weighted allowlist.

    Spend is budget x (1 - buffer-pct), and must fit rail 14's monthly BUY cap (a cap keel
    imposes on its own buying -- not a fee waiver). At a terminal: [Y] writes one `candidate`
    `dca` rule per asset (never paper/live, never touching an existing rule), [E] edits weights,
    [N] cancels. Off a terminal: prints the plan and writes nothing.
    """
    try:
        inputs = parse_plan_inputs(budget, buffer_pct, cadence_days)
    except DcaPlanError as exc:
        raise click.BadParameter(str(exc)) from exc

    # R20: decide interactivity FIRST, once, before the database is touched at all.
    interactive = _common._is_interactive()
    config = _load_cfg(ctx)
    repo = _common._open_repo(ctx) if interactive else _common._open_repo_ro(ctx)
    venue = _bound_venue_or_default(None)  # after _load_cfg: rail 14's own venue binding
    weights: dict[str, Decimal] | None = None

    while True:
        try:
            plan = build_dca_plan(
                repo,
                config,
                inputs,
                venue=venue,
                now_ts=int(time.time()),
                screen_fn=screen_product,
                weights_override=weights,
            )
        except DcaPlanError as exc:
            # `DcaPlanError`'s own docstring: "the message is shown verbatim" -- a config-level
            # refusal (case-colliding `target_weights`, #848) or a stray `[E]` edit must reach
            # the operator as a clean error, not an uncaught traceback out of the CLI.
            raise click.ClickException(str(exc)) from exc
        for line in render_dca_plan(plan):
            click.echo(line)
        if not interactive:
            click.echo("")
            click.echo("not a terminal: nothing written. Run this at a terminal to approve.")
            if plan.blockers:
                ctx.exit(1)
            return
        choice = _prompt_choice(plan.approvable)
        if choice == "N":
            click.echo("cancelled: nothing written.")
            return
        if choice == "E":
            weights = _edit_weights(plan.editable)
            continue
        try:
            apply_dca_plan(
                repo,
                config,
                plan,
                now_ts=int(time.time()),
                echo=click.echo,
                echo_err=lambda m: click.echo(m, err=True),
            )
        except DcaPlanRefused as exc:
            raise click.ClickException(str(exc)) from exc
        return


def _prompt_choice(approvable: bool) -> str:
    """Show the offered letters ONCE, then read raw input in its own loop -- deliberately not
    `click.prompt(..., type=click.Choice(...))`, whose built-in retry re-displays the full prompt
    text on every invalid entry. A blocked plan's `[E] Edit weights / [N] Cancel` line must appear
    exactly once even if the operator types the `[Y]` that was not offered."""
    choices = (["Y"] if approvable else []) + ["E", "N"]
    click.echo(" / ".join(_CHOICE_LABELS[c] for c in choices))
    while True:
        raw = click.prompt("choice", prompt_suffix="> ", default="", show_default=False)
        answer = raw.strip().upper()
        if answer in choices:
            return answer
        click.echo(f"  please answer one of: {', '.join(choices)}")


def _edit_weights(editable: tuple[tuple[str, Decimal], ...]) -> dict[str, Decimal]:
    """Prompt each editable asset's weight (default: its current one); re-prompt on bad input."""
    edited: dict[str, Decimal] = {}
    for asset, current in editable:
        while True:
            raw = click.prompt(f"weight for {asset}", default=format(current, "f"))
            try:
                edited[asset] = parse_weight(raw, asset)
                break
            except DcaPlanError as exc:
                click.echo(f"  {exc}")
    return edited
