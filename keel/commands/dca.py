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

**`keel dca proposals list|show` open read-only ALWAYS** (#857). They never write -- the proposals
log is written by the cycle (P7/P8), never by a reader -- so there is no TTY branch to take:
`_common._open_repo_ro` refuses a missing `--db` path or a stale schema at a terminal exactly as it
does off one, and a v21 database is the operator's `keel migrate`, not a side effect of reading.

**`keel dca proposals review <id>` is the one proposals verb that writes** (#857, P12, plan R23):
it stamps `reviewed_ts` -- the input spec §3.7's paper -> live gate counts -- after a `[y/N]` at a
terminal, and only there. It follows `dca plan`'s posture, not the typed-`yes` gate's: marking a
proposal reviewed releases no order and places nothing (a `live` sleeve-sell rule is preview-only
in this build), so it is not a capability row, and `_require_interactive_confirmation` would be
ceremony. Interactivity is decided first, exactly as above: off a TTY it opens read-only, prints
the proposal and `not a terminal: nothing written.`, and exits 0. A `superseded` proposal was never
its rule's decision and cannot be reviewed (exit 1); an already-reviewed one keeps its FIRST review
time -- the gate reads whether a review happened, and the audit chain already holds when.

**`keel dca trim --preview --view {lots,bands}` opens read-only ALWAYS, too** (#857, P13): the
per-tranche lots report (spec §8.1, "not tax advice") or the weights-against-targets drift
display (spec §5). Neither writes, proposes or builds a broker; every fee is the fallback rate
(R25). `--view bands` is a DISPLAY: band trimming was tested and not adopted (#831), no
`band_rebalance` rule exists, and the report says so every run. `--view gain` arrives in P14
(R24), where it becomes the default; until then it is a usage error.

**`keel dca distribute --preview` opens read-only ALWAYS, too** (#857, P10): what each
`reverse_dca` rule's next cadence day would do, on today's cached close. It writes nothing -- no
proposal row, no state -- and builds no broker, so its fee is the `config.fees.taker_pct` fallback,
labelled as such (plan R25, `dca plan`'s precedent). The per-cycle proposal is where the venue's
previewed fee is recorded.

Both openers are reached as `_common.<name>(ctx)` -- module attribute access, never a name bound
by `from ... import ...` -- for the same reason `_common`'s own docstring gives for
`_is_interactive`: it is the one patch point a test can rebind no matter which module the calling
command lives in.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import click

from keel.commands import _common, sleeve_report
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

#: The last line every sleeve preview (`distribute`, `trim`) prints: in this build no sleeve sale
#: is placed, by any path (S1, S2).
PREVIEW_FOOTER = "preview only: nothing is placed."

#: The `[Y]/[E]/[N]` prompt's per-choice label, keyed by letter. One table, so the loop's prompt
#: line and its retry message cannot disagree about what each letter means.
_CHOICE_LABELS = {"Y": "[Y] Approve", "E": "[E] Edit weights", "N": "[N] Cancel"}


@click.group("dca")
def dca_group() -> None:
    """Plan a multi-asset DCA schedule (writes `candidate` rules only, on approval), and read the
    sleeve's sell-proposals log."""


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


@dca_group.group("proposals")
def proposals_group() -> None:
    """The sleeve's sell-proposals log: what each sleeve-sell rule proposed, the rails' answer,
    and the fee it would have paid. `list` and `show` read; `review` marks one reviewed at a
    terminal."""


@proposals_group.command("list")
@click.option("--product", default=None, help="Only this product, e.g. BTC-USD.")
@click.pass_context
@with_disclaimer
def proposals_list_cmd(ctx: click.Context, product: str | None) -> None:
    """Every recorded sell proposal, newest first, one line each. Writes nothing."""
    repo = _common._open_repo_ro(ctx)
    rows = repo.get_sell_proposals(product_id=product)
    if not rows:
        click.echo("no sell proposals recorded" + (f" for {product}" if product else "") + ".")
        return
    for row in rows:
        click.echo(sleeve_report.render_proposal_line(row))


@proposals_group.command("show")
@click.argument("proposal_id", type=int)
@click.pass_context
@with_disclaimer
def proposals_show_cmd(ctx: click.Context, proposal_id: int) -> None:
    """One proposal in full: its trigger, the rails' answer, the fee and its source, and the
    legs rail 2's slicing needs. Writes nothing."""
    repo = _common._open_repo_ro(ctx)
    row = repo.get_sell_proposal(proposal_id)
    if row is None:
        raise click.ClickException(f"no sell proposal #{proposal_id}")
    for line in sleeve_report.render_proposal(row):
        click.echo(line)


@proposals_group.command("review")
@click.argument("proposal_id", type=int)
@click.pass_context
@with_disclaimer
def proposals_review_cmd(ctx: click.Context, proposal_id: int) -> None:
    """Mark one proposal reviewed, at a terminal, after a [y/N]: the evidence `keel rules
    promote` needs to take a sleeve-sell rule from paper to live. Releases no order and places
    nothing. Off a terminal: prints the proposal and writes nothing."""
    # R20: decide interactivity FIRST, once, before the database is touched at all.
    interactive = _common._is_interactive()
    repo = _common._open_repo(ctx) if interactive else _common._open_repo_ro(ctx)
    row = repo.get_sell_proposal(proposal_id)
    if row is None:
        raise click.ClickException(f"no sell proposal #{proposal_id}")
    for line in sleeve_report.render_proposal(row):
        click.echo(line)
    if row["decision"] == "superseded":
        raise click.ClickException(
            f"proposal #{proposal_id} was superseded by {row.get('superseded_by') or 'another'} "
            "kind: it was never its rule's decision, so it cannot be reviewed."
        )
    if row["reviewed_ts"] is not None:
        when = datetime.fromtimestamp(int(row["reviewed_ts"]), UTC).strftime("%Y-%m-%d")
        click.echo(f"already reviewed on {when}: nothing written.")
        return
    if not interactive:
        click.echo("")
        click.echo("not a terminal: nothing written.")
        return
    if not click.confirm(
        "Mark this proposal reviewed? It releases no order and places nothing", default=False
    ):
        click.echo("not reviewed: nothing written.")
        return
    repo.update_sell_proposal(proposal_id, reviewed_ts=int(time.time()))
    click.echo(f"proposal #{proposal_id} marked reviewed.")


@dca_group.command("distribute")
@click.option(
    "--preview",
    is_flag=True,
    default=False,
    help="Show what each reverse_dca rule's next cadence day would do. Required: it is the only "
    "mode in this build.",
)
@click.pass_context
@with_disclaimer
def distribute_cmd(ctx: click.Context, preview: bool) -> None:
    """What each `reverse_dca` rule's next cadence day would sell, on today's cached close: the
    gates, the size, the fee at the fallback rate, rail 2's legs, and whether a dca buy falls on
    the same day. Writes nothing and asks no venue."""
    if not preview:
        raise click.UsageError("pass --preview: it is the only mode in this build.")
    config = _load_cfg(ctx)
    repo = _common._open_repo_ro(ctx)
    rows = sleeve_report.distribution_rows(repo, config, now_ts=int(time.time()))
    for line in sleeve_report.render_distribution(rows):
        click.echo(line)
    click.echo(PREVIEW_FOOTER)


def _require_preview(_ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
    """`--preview` is refused as absent BEFORE `--view` is parsed (the option is eager), so the
    usage error names the missing mode first -- the only mode in this build."""
    if not value:
        raise click.UsageError("pass --preview: it is the only mode in this build.")
    return value


@dca_group.command("trim")
@click.option(
    "--preview",
    is_flag=True,
    default=False,
    is_eager=True,
    callback=_require_preview,
    help="Show the report. Required: it is the only mode in this build.",
)
@click.option(
    "--view",
    type=click.Choice(["lots", "bands"]),
    required=True,
    help="lots: every open tranche in FIFO order, with its unrealised and if-sold-now P&L (not "
    "tax advice). bands: sleeve weights against target_weights (a display; band trimming was "
    "tested and not adopted, #831).",
)
@click.pass_context
@with_disclaimer
def trim_cmd(ctx: click.Context, preview: bool, view: str) -> None:
    """A read-only report over the positions ledger: per-tranche P&L (`--view lots`) or weights
    against targets (`--view bands`). Fees at the fallback rate. Writes nothing, asks no venue,
    and proposes nothing."""
    config = _load_cfg(ctx)
    repo = _common._open_repo_ro(ctx)
    if view == "lots":
        lines = sleeve_report.render_lots(sleeve_report.lots_view(repo, config))
    else:
        try:
            report = sleeve_report.bands_view(repo, config)
        except DcaPlanError as exc:  # a config-level refusal (#848), shown verbatim
            raise click.ClickException(str(exc)) from exc
        lines = sleeve_report.render_bands(report)
    for line in lines:
        click.echo(line)
    click.echo(PREVIEW_FOOTER)
