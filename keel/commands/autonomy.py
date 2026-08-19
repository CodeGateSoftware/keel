"""`keel autonomy` -- whether the agent places orders without asking first.

Autonomy is a profile choice stored in the repository and re-read by `agent.run_once` every
cycle. `on` RELEASES the confirm prompt, so it demands a typed `yes` at a terminal via the shared
gate; `off` only ever reduces capability and stays ungated. It changes *who is asked*, never
*what is allowed* -- the hard rails run in every mode.
"""

from __future__ import annotations

import time

import click

from keel.commands._common import (
    _load_cfg,
    _open_repo,
    _require_interactive_confirmation,
    with_disclaimer,
)
from keel.config import Config

#: Upper bound on `keel autonomy on --for-hours` (1 year). Guards against inf/nan/overflow
#: and against a "window" so long it is indistinguishable from no expiry at all.
_MAX_AUTONOMY_HOURS = 8760.0
#: One second. Below this the window rounds to zero and we would write an already-lapsed row
#: while printing "autonomy ON until ..." -- fails safe, but the message would be a lie.
_MIN_AUTONOMY_HOURS = 1.0 / 3600.0

#: The line `keel autonomy off` prints -- de-risking is done, say so the same way twice.
AUTONOMY_OFF_LINE = "autonomy off: every order will ask for confirmation."


def autonomy_expiry(for_hours: float | None, now_ts: int) -> int | None:
    """The expiry stamp `--for-hours` resolves to (`None` = never lapses), shared by the
    CLI command and any front-end that arms autonomy with the same semantics."""
    return None if for_hours is None else now_ts + int(for_hours * 3600)


def autonomy_on_gate(config: Config, for_hours: float | None, now_ts: int) -> None:
    """The typed gate `keel autonomy on` demands -- its ONE home, extracted from the CLI
    body (issue #391 C5) so the TUI's Trading menu runs the CLI's own ceremony: the same
    action wording, the same decisive facts (the window, the mode, the allowlist), the
    same typed `yes` from a human at a terminal. Byte-identical to what the CLI prints;
    the CLI command calls this."""
    expires_ts = autonomy_expiry(for_hours, now_ts)
    window = (
        "until you turn it off" if expires_ts is None else f"for {for_hours}h (until {expires_ts})"
    )
    _require_interactive_confirmation(
        "turn autonomy ON",
        f"Orders will be placed with NO further prompt, {window} "
        f"(mode={config.auto_trade.mode}, allowlist={config.allowlist}).",
    )


def render_autonomy_on(expires_ts: int | None) -> list[str]:
    """The lines `keel autonomy on` prints after arming -- the no-expiry warning included
    (`--for-hours`'s whole point), kept beside the gate so both front-ends show the same
    aftermath."""
    if expires_ts is None:
        return [
            "autonomy ON, with NO expiry -- it stays on until you run `keel autonomy off`.",
            "  Consider `--for-hours N` for a supervised session, so a forgotten `on` cannot "
            "grant unattended trading indefinitely.",
        ]
    return [f"autonomy ON until {expires_ts}. It lapses on its own after that."]


@click.group("autonomy")
def autonomy_group() -> None:
    """Whether the agent places orders without asking you first."""


@autonomy_group.command("show")
@click.pass_context
@with_disclaimer
def autonomy_show(ctx: click.Context) -> None:
    """Print the current autonomy setting."""
    repo = _open_repo(ctx)
    profile = repo.get_profile()
    # `_open_repo` runs `migrate()`, which recreates a merely MISSING table -- so this covers
    # damage migrate cannot heal (a corrupt page, a table of the wrong shape), not a fresh DB.
    if not repo.profile_readable():
        click.echo(
            "WARNING: the profile row could not be read (see the log). Reporting autonomy as "
            "OFF, which is the safe reading -- but the stored setting is UNKNOWN.",
            err=True,
        )
    now_ts = int(time.time())
    live = profile.is_autonomous(now_ts)
    state = (
        "ON -- orders are placed WITHOUT asking"
        if live
        else "off -- every order asks first"
    )
    click.echo(f"autonomy: {state}")
    if profile.autonomous and not live:
        click.echo(f"  (was ON but LAPSED at {profile.autonomous_until})")
    elif live and profile.autonomous_until is not None:
        left = profile.autonomous_until - now_ts
        click.echo(f"  lapses at {profile.autonomous_until} ({left}s left)")
    elif live:
        click.echo("  no expiry set -- stays on until `keel autonomy off`")
    if profile.updated_ts:
        click.echo(f"  last changed: {profile.updated_ts}")


@autonomy_group.command("on")
@click.option(
    "--for-hours",
    "for_hours",
    type=float,
    default=None,
    help="Let autonomy LAPSE automatically after this many hours (default: never lapses).",
)
@click.pass_context
@with_disclaimer
def autonomy_on(ctx: click.Context, for_hours: float | None) -> None:
    """Let the agent place orders without asking (dangerous: asks for confirmation).

    Every order is still subject to all hard rails -- autonomy changes who is asked, never what
    is allowed. It does NOT let the agent clear a safety halt: releasing the kill-switch or a
    drawdown breaker always needs a human, whatever this is set to.
    """
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    now_ts = int(time.time())
    if for_hours is not None and not (_MIN_AUTONOMY_HOURS <= for_hours <= _MAX_AUTONOMY_HOURS):
        # 0/negative would write an already-lapsed row while printing "autonomy ON until ...",
        # and inf/nan/1e18 would overflow int() after the operator had already typed `yes`.
        raise click.BadParameter(
            f"--for-hours must be at least {_MIN_AUTONOMY_HOURS} (one second) and at most "
            f"{_MAX_AUTONOMY_HOURS} ({int(_MAX_AUTONOMY_HOURS) // 24} days); got {for_hours!r}."
        )
    expires_ts = autonomy_expiry(for_hours, now_ts)
    autonomy_on_gate(config, for_hours, now_ts)
    repo.set_autonomous(True, now_ts, expires_ts=expires_ts)
    for line in render_autonomy_on(expires_ts):
        click.echo(line)


@autonomy_group.command("off")
@click.pass_context
@with_disclaimer
def autonomy_off(ctx: click.Context) -> None:
    """Require confirmation before every order again.

    Deliberately ungated and usable without a terminal: reducing risk must never be obstructed,
    so this works from a script, a cron job or a pipe. Arming is what needs a human.
    """
    _open_repo(ctx).set_autonomous(False, int(time.time()))
    click.echo(AUTONOMY_OFF_LINE)
