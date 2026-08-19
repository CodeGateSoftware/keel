"""`keel subscription` -- the per-venue, user-attested allowance that rail 14 enforces.

Coinbase exposes no subscription endpoint, so a subscription is *asserted* by the user, not
fetched. This group writes that assertion into the repository; `execution.guards` rail 14 reads
it fresh on every order. It needs the DB/config seams (`_open_repo`, `_load_cfg`) from
`keel.commands._common` but never the broker, so it carries no live-network blast radius.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation

import click
from keel_core.subscription import BrokerSubscription, SubscriptionStatus
from keel_core.telemetry import current_venue

from keel.commands._common import _load_cfg, _open_repo, with_disclaimer
from keel.config import Config
from keel.data.repository import Repository
from keel.execution.guards import DEFAULT_VENUE

ATTESTATION_PERIOD_SEC = 365 * 24 * 3600


def _bound_venue_or_default(venue: str | None) -> str:
    """An explicit `--venue` wins; otherwise the venue THIS deployment trades.

    That is the same binding rail 14 gates every BUY on -- the one `_load_cfg` makes at
    process entry for telemetry (`bind_venue(config.broker.name)`) -- with coinbase when
    nothing is bound. A `--venue` default frozen at coinbase would make an alpaca operator
    type `--venue alpaca` on every invocation or silently write a record nothing reads.

    Must be called AFTER `_load_cfg(ctx)` has run, or there is nothing bound to read.
    """
    if venue is not None:
        return venue
    return current_venue() or DEFAULT_VENUE


@click.group("subscription")
def subscription_group() -> None:
    """View or attest a venue's subscription (the allowance execution.guards rail 14 enforces).

    Coinbase exposes no subscription endpoint, so a subscription is *asserted* by the user, not
    fetched. `attest` is that assertion. Rail 14 reads the resulting record fresh on every order,
    so an attestation takes effect on the very next one, with no restart. An omitted `--venue`
    means this deployment's bound venue (its `broker:` selection; coinbase when unbound) -- the
    same key rail 14 gates on, so the default writes the record that will actually be read.

    Until a venue is attested, rail 14 caps it at `subscription.unsubscribed_allowance_usd`
    (default 0) -- keel ships unable to place a live BUY, deliberately.
    """


def _resolve_pacing(
    repo: Repository, config: Config, venue: str, pacing: str | None
) -> str:
    """Explicit `--pacing` wins; otherwise keep the venue's existing choice, else config's.

    Re-attesting must not silently reset a pacing mode the user set earlier.
    """
    if pacing is not None:
        return pacing
    existing = repo.get_broker_subscription(venue)
    return existing.pacing if existing is not None else config.subscription.pacing


# -- the service layer (issue #389 C3: one implementation, two front-ends) ------------------------
#
# The three bodies below (attest, set, show) were CLI command bodies until C3 needed them
# from the console's Compliance menu: the TUI dispatches to the SAME functions the CLI
# commands call (PRD O2), so tier resolution, pacing carry-over, the record's shape and
# every echoed line live here ONCE. Errors raise `ValueError`; each front-end renders it
# its own way (the CLI keeps its byte-compatible stderr + exit-1 shape, the console
# returns the message as the form's result line).


def apply_subscription_attest(
    repo: Repository,
    config: Config,
    *,
    venue: str | None,
    tier_name: str,
    pacing: str | None,
    now_ts: int,
) -> str:
    """`subscription attest`'s write: resolve the venue and the tier, upsert the record,
    return the confirmation line. Raises `ValueError` for an unknown tier."""
    resolved_venue = _bound_venue_or_default(venue)

    tier = next((t for t in config.tiers if t.name == tier_name), None)
    if tier is None:
        valid = ", ".join(t.name for t in config.tiers)
        raise ValueError(f"unknown tier {tier_name!r}. Configured tiers: {valid}")

    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue=resolved_venue,
            tier_name=tier.name,
            free_volume_usd=tier.free_volume_usd,
            pacing=_resolve_pacing(repo, config, resolved_venue, pacing),
            subscription_usd_month=tier.subscription_usd_month,
            status=SubscriptionStatus.ACTIVE,
            attested_at=now_ts,
            attest_due_ts=now_ts + ATTESTATION_PERIOD_SEC,
        )
    )
    volume = "unlimited" if tier.free_volume_usd is None else str(tier.free_volume_usd)
    return (
        f"attested {resolved_venue}: tier={tier.name} free_volume_usd={volume} "
        f"status=active due in 365 days"
    )


def apply_subscription_set(
    repo: Repository,
    config: Config,
    *,
    venue: str | None,
    free_volume_raw: str,
    pacing: str | None,
    now_ts: int,
) -> str:
    """`subscription set`'s write: a raw allowance with no tier named. Raises `ValueError`
    for a non-numeric, non-finite or negative allowance (see the CLI body's own note for
    why `inf` in particular must never become a spend cap)."""
    resolved_venue = _bound_venue_or_default(venue)

    try:
        free_volume_usd = Decimal(free_volume_raw)
    except InvalidOperation:
        raise ValueError(
            f"--free-volume-usd must be a number, got {free_volume_raw!r}"
        ) from None
    # `Decimal("nan")`/`Decimal("inf")` parse without raising `InvalidOperation` above, so they
    # must be rejected here, before the `< 0` comparison below (a NaN comparison itself raises
    # InvalidOperation, uncaught). `inf` would otherwise become an unbounded live spend cap --
    # "unlimited" has no representation via this command; it is expressed elsewhere in this
    # system as `free_volume_usd is None` (a Premium tier via `subscription attest`), never `inf`.
    if not free_volume_usd.is_finite():
        raise ValueError(
            f"--free-volume-usd must be a finite number, got {free_volume_raw!r}"
        )
    if free_volume_usd < 0:
        raise ValueError("--free-volume-usd must be non-negative")

    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue=resolved_venue,
            tier_name="unknown",
            free_volume_usd=free_volume_usd,
            pacing=_resolve_pacing(repo, config, resolved_venue, pacing),
            # Placeholder: `set` names no tier, so there is no real subscription price to
            # record here. Must not be read as an actual (free) subscription cost.
            subscription_usd_month=Decimal("0"),
            # ACTIVE (full spend authority), even though `tier_name='unknown'` -- the same shape
            # the v2 migration backfill deliberately marks `suspect` instead. Not a contradiction:
            # the migration distrusts a *stale* hand-tuned number of unknown provenance, whereas
            # this is a *fresh, explicit* user assertion made right now, by name, via this
            # command. Provenance differs even though the resulting record looks identical.
            status=SubscriptionStatus.ACTIVE,
            attested_at=now_ts,
            attest_due_ts=now_ts + ATTESTATION_PERIOD_SEC,
        )
    )
    return (
        f"set {resolved_venue}: free_volume_usd={free_volume_usd} tier=unknown "
        f"(not an attestation -- prefer `subscription attest`)"
    )


def subscription_show_lines(repo: Repository, config: Config, now_ts: int) -> list[str]:
    """`subscription show`'s exact lines, as a function of the repo/config/now it reads --
    the console's Compliance menu renders the same report the CLI echoes."""
    records = repo.list_broker_subscriptions()

    if not records:
        # The advice names the BOUND venue -- the one rail 14 actually gates on for this
        # deployment -- so the operator's copy-paste writes the record that will be read.
        venue = _bound_venue_or_default(None)
        return [
            "no subscription attested for any venue -- rail 14 caps live BUYs at the "
            f"unsubscribed allowance {config.subscription.unsubscribed_allowance_usd}. "
            f"Run `keel subscription attest --venue {venue} --tier <tier>`."
        ]

    unsubscribed = config.subscription.unsubscribed_allowance_usd
    lines: list[str] = []
    for record in records:
        allowance = record.allowance_usd(now_ts, unsubscribed)
        cap = "unlimited" if allowance is None else str(allowance)
        volume = (
            "unlimited" if record.free_volume_usd is None else str(record.free_volume_usd)
        )
        lines.append(
            f"{record.venue}: tier={record.tier_name} free_volume_usd={volume} "
            f"pacing={record.pacing} stored_status={record.status.value} "
            f"effective_status={record.effective_status(now_ts).value} "
            f"effective_cap={cap} attested_at={record.attested_at} "
            f"attest_due_ts={record.attest_due_ts}"
        )
    return lines


@subscription_group.command("attest")
@click.option(
    "--venue",
    default=None,
    help="Venue to attest (default: this config's bound venue -- its `broker:` selection; "
    "coinbase when unbound).",
)
@click.option("--tier", "tier_name", required=True, help="Tier name from config.yaml's `tiers`.")
@click.option(
    "--pacing",
    type=click.Choice(["opportunistic", "even_daily"]),
    default=None,
    help="Pacing mode (default: keep the venue's current value).",
)
@click.pass_context
@with_disclaimer
def subscription_attest(
    ctx: click.Context, venue: str | None, tier_name: str, pacing: str | None
) -> None:
    """Assert which subscription tier this venue is on -- clears `suspect` by asserting a named
    tier (`subscription set` also clears it, but names no tier)."""
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)

    try:
        line = apply_subscription_attest(
            repo, config, venue=venue, tier_name=tier_name, pacing=pacing,
            now_ts=int(time.time()),
        )
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)
    click.echo(line)


@subscription_group.command("set")
@click.option(
    "--venue",
    default=None,
    help="Venue to update (default: this config's bound venue -- its `broker:` selection; "
    "coinbase when unbound).",
)
@click.option(
    "--free-volume-usd",
    "free_volume_raw",
    required=True,
    help="Raw fee-free monthly volume in USD, e.g. 500.",
)
@click.option(
    "--pacing",
    type=click.Choice(["opportunistic", "even_daily"]),
    default=None,
    help="Pacing mode (default: keep the venue's current value).",
)
@click.pass_context
@with_disclaimer
def subscription_set(
    ctx: click.Context, venue: str | None, free_volume_raw: str, pacing: str | None
) -> None:
    """Set a raw allowance without naming a tier -- an escape hatch, not an attestation.

    Leaves `tier_name='unknown'`, which `show` surfaces: the record is visibly a hand-set number
    rather than a stated tier. Prefer `attest`.
    """
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)

    try:
        line = apply_subscription_set(
            repo, config, venue=venue, free_volume_raw=free_volume_raw, pacing=pacing,
            now_ts=int(time.time()),
        )
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)
    click.echo(line)


@subscription_group.command("show")
@click.pass_context
@with_disclaimer
def subscription_show(ctx: click.Context) -> None:
    """Show every venue's subscription, with the status and cap actually in force."""
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    for line in subscription_show_lines(repo, config, int(time.time())):
        click.echo(line)
