"""The order-preview confirmation gate (`mode="confirm"`'s human ask), shared by every front-end.

This module exists so the CLI and the TUI (PRD O2, the TUI-operator-console phase) hand the
executor ONE confirm function -- one implementation, two front-ends -- instead of each growing
its own rendering of what a preview means. `keel.agent.run_once`/`agent.loop` take a
`confirm_fn`; the CLI passes `interactive_confirm` (below) and asserts nothing else; a future
TUI trading surface passes the SAME function (its typed-prompt contract, PRD O3, is this gate's
behavior, not a re-implementation of it).

It is click-COUPLED by design: the ask is a terminal prompt (`click.confirm`/`click.prompt`),
and the fail-closed TTY check goes through `keel.commands._common._is_interactive` -- the single
patch point every other gate uses. Everything else here (reading the preview shape, deciding
degradedness, rendering the lines) is pure, so a front-end that renders previews itself can
still reuse `read_preview`/`preview_lines` and get byte-identical banners.

Moved verbatim from `keel/cli.py` (issue #387 C1); `keel.cli` re-exports the public markers and
`_interactive_confirm` so the existing tests' `cli_module.*` pins keep resolving to these exact
objects.
"""

from __future__ import annotations

from typing import Any

import click
from keel_broker_api.results import Preview

from keel.commands import _common

#: The banner a venue-priced preview carries. Exported (no underscore) because the tests assert
#: on the exact text a human sees -- the failure this gate defends against is a *misread screen*,
#: so the rendered string is the contract, not an implementation detail.
NATIVE_PREVIEW_MARKER = "BROKER QUOTE -- the venue priced this order itself."
SYNTHETIC_PREVIEW_MARKER = "SYNTHETIC ESTIMATE -- NOT A BROKER QUOTE."
UNPRICED_PREVIEW_MARKER = "UNPRICED -- this preview carries no usable size."
UNREADABLE_PREVIEW_MARKER = "PREVIEW UNREADABLE -- this gate cannot interpret what came back."
#: What a human must type to place a degraded (unpriced / error-carrying / unreadable) preview.
#: Compared case-insensitively after stripping: see `_ask_to_place`.
DEGRADED_PREVIEW_PHRASE = "place anyway"

_RULE_NATIVE = "  " + "=" * 72
_RULE_ALARM = "  " + "!" * 72


def _read_preview(
    preview: Preview | None,
) -> tuple[bool, dict[str, Any], tuple[str, ...], bool, bool]:
    """Normalize the port's `Preview` into `(synthetic, fields, errors, unpriced, ok)`.

    ONE shape since #524 finished the broker-port migration: every broker the live path can
    construct answers `preview_order` with a `Preview`, so the legacy Coinbase dict arm is
    deleted, not dormant. Anything else -- including a dict, the pre-port shape -- is
    unreadable (`ok=False`), which renders as its own alarm and demands the typed phrase
    rather than guessing at numbers this gate cannot vouch for.
    """
    if isinstance(preview, Preview):
        fields: dict[str, Any] = {
            "product_id": preview.product_id,
            "side": getattr(preview.side, "value", preview.side),
            "est_base_size": preview.est_base_size,
            "est_quote_size": preview.est_quote_size,
            "est_fee": preview.est_fee,
        }
        # NOT `fields.update(...)`: `detail` is free-form text an adapter chose, and a key
        # collision would let it silently overwrite a money field with something that merely
        # looks like one. A shadowed `est_fee` is a wrong number on a spending screen with
        # nothing to indicate it was substituted, so a colliding key is namespaced instead.
        for key, value in preview.detail.items():
            fields[key if key not in fields else f"detail.{key}"] = value
        # Either leg at zero means the adapter could not size this order. That is NOT a cheap
        # order; it is an unknown one, and the two must never render alike.
        unpriced = preview.est_quote_size <= 0 or preview.est_base_size <= 0
        return preview.synthetic, fields, tuple(preview.errors), unpriced, True

    return False, {}, (), True, False


def _preview_lines(preview: Preview | None) -> tuple[list[str], bool]:
    """Render `preview` for a human; return `(lines, degraded)`.

    `degraded` is true when the preview is unpriced, carries errors, or could not be read at all
    -- the three cases where the numbers on screen do not mean what they appear to mean.

    Provenance is rendered ABOVE the numbers, not below, because a footnote under a tidy
    key/value block is read after the decision has already been made.
    """
    synthetic, fields, errors, unpriced, readable = _read_preview(preview)

    lines: list[str] = []
    if not readable:
        lines += [
            _RULE_ALARM,
            f"  !! {UNREADABLE_PREVIEW_MARKER}",
            "  !! Nothing below has been checked. Do not read it as a quote.",
            _RULE_ALARM,
            f"    {preview!r}",
        ]
        return lines, True

    if synthetic:
        lines += [
            _RULE_ALARM,
            f"  !! {SYNTHETIC_PREVIEW_MARKER}",
            "  !! keel's adapter computed these figures from a price lookup. The",
            "  !! venue has NOT priced, validated or reserved anything, and is bound",
            "  !! by none of the numbers below. The fill can differ.",
            _RULE_ALARM,
        ]
    else:
        lines += [_RULE_NATIVE, f"  {NATIVE_PREVIEW_MARKER}", _RULE_NATIVE]

    lines += [f"    {key}: {value}" for key, value in fields.items()]

    # One block below the numbers, not one per problem: two rules butted together read as a
    # rendering glitch, and a glitch is the last thing a money screen should look like.
    alarms: list[str] = []
    if unpriced:
        alarms += [
            f"  !! {UNPRICED_PREVIEW_MARKER}",
            "  !! This is NOT a zero-cost order -- it is an order whose cost could",
            "  !! not be determined. Approving it sends an order to the venue with",
            "  !! no idea what it will spend.",
        ]
    if errors:
        alarms += [f"  !! PREVIEW ERRORS ({len(errors)}) -- reported against this order:"]
        alarms += [f"  !!   - {error}" for error in errors]
    if alarms:
        lines += [_RULE_ALARM, *alarms, _RULE_ALARM]

    return lines, bool(unpriced or errors)


def _ask_to_place(degraded: bool) -> bool:
    """The question itself. A clean preview takes an ordinary y/n; a degraded one does not.

    **Why extra friction, and why not a block.** A `y` at a `[y/N]` prompt is muscle memory after
    the tenth order of the day, and the whole point of the banners above is that this particular
    screen is not like the last ten. Demanding a typed phrase forces the operator to have read
    *something*. It is deliberately NOT a refusal: the exit path is the one that must never be
    walled off, and an unpriced preview is exactly what a human would see when a venue's pricing
    endpoint is down and they are trying to close a position. Refusing outright would trap a
    position behind a broken preview endpoint -- a worse money outcome than a warned-and-approved
    order. So: harder to do, never impossible.

    An abort (Ctrl-C, EOF) at either prompt is a decline, not a traceback out of the gate.
    """
    try:
        if not degraded:
            return bool(click.confirm("Place this order?", default=False))
        click.echo(
            "This preview is NOT a reliable quote. To place it anyway you must type the "
            f'phrase "{DEGRADED_PREVIEW_PHRASE}" -- anything else declines.'
        )
        typed = click.prompt(
            f'Type "{DEGRADED_PREVIEW_PHRASE}" to place, or press Enter to decline',
            default="",
            show_default=False,
        )
    except click.Abort, EOFError:
        click.echo("aborted -- declining.", err=True)
        return False
    return str(typed).strip().lower() == DEGRADED_PREVIEW_PHRASE


def _interactive_confirm(preview: Preview | None) -> bool:
    """Human-in-the-loop order confirmation for `mode="confirm"`.

    Called by the executor ONLY after the intent has already passed every hard rail -- this is
    an additional human gate, never a replacement for the rails.

    **What this renders, and why it shouts.** `Preview`'s own docstring sets the requirement:
    "approving an estimate must never look identical to approving a broker's own quote." A
    venue that has a preview endpoint returns numbers it is willing to stand behind; a venue
    without one gets an estimate keel computed from a price lookup that validated nothing and
    reserved nothing. Those two screens used to be byte-identical, because this function took
    a raw dict and had nowhere to put `Preview.synthetic`. They are now visually unmistakable, and
    the distinction sits ABOVE the numbers rather than under them.

    Three further states get their own alarm block: `Preview.errors` (the adapter or the venue
    said something went wrong), an unpriced preview (a zero size renders as a harmless "$0.00
    order" unless something says otherwise), and a preview shape this gate cannot read at all.
    Each of those also upgrades the question from `[y/N]` to a typed phrase -- see `_ask_to_place`
    for why that is friction and not a refusal.

    Fails closed: a non-TTY invocation (a script, a cron job, a headless run) declines rather
    than blocking on stdin, so `mode="confirm"` never trades unattended.
    """
    lines, degraded = _preview_lines(preview)
    # Venue-neutral on purpose: a `Preview` can come from any venue, and an unreadable preview
    # is not any venue's either -- naming one would assert a provenance this gate does not have.
    click.echo("\nRails PASSED. Order preview:")
    for line in lines:
        click.echo(line)
    if not _common._is_interactive():
        click.echo("no TTY -- declining (confirm mode fails closed).", err=True)
        return False
    return _ask_to_place(degraded)
