"""The order-preview confirmation gate (`mode="confirm"`'s human ask), shared by every front-end.

This module exists so the CLI and the TUI (PRD O2, the TUI-operator-console phase) hand the
executor ONE confirm function -- one implementation, two front-ends -- instead of each growing
its own rendering of what a preview means. `keel.agent.run_once`/`agent.loop` take a
`confirm_fn`; the CLI passes `interactive_confirm` (below) and asserts nothing else; a future
TUI trading surface passes the SAME function (its typed-prompt contract, PRD O3, is this gate's
behavior, not a re-implementation of it).

It is click-COUPLED by design: the ask is a terminal prompt (`click.confirm`/`click.prompt`),
and the fail-closed TTY check goes through `keel.commands._common._is_interactive` -- the single
patch point every other gate uses. Everything else here (reading either preview shape, deciding
degradedness, rendering the lines) is pure, so a front-end that renders previews itself can
still reuse `read_preview`/`preview_lines` and get byte-identical banners.

Moved verbatim from `keel/cli.py` (issue #387 C1); `keel.cli` re-exports the public markers and
`_interactive_confirm` so the existing tests' `cli_module.*` pins keep resolving to these exact
objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
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


def _preview_decimal(value: Any) -> Decimal | None:
    """Best-effort `Decimal` for a money field that may be a `Decimal`, a string or junk."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _read_preview(preview: Any) -> tuple[bool, dict[str, Any], tuple[str, ...], bool, bool]:
    """Normalize either accepted preview shape into `(synthetic, fields, errors, unpriced, ok)`.

    **Why two shapes.** The port's `Preview` is where this is going; the raw dict is where the
    live path still is. `executor.py` calls `broker.preview_order(product_id, side,
    order_configuration)` and `keel/commands/_common.py` builds a `CoinbaseClient`, so every
    preview a human sees TODAY is `cb_client.preview_order`'s dict. Phase B has not landed.
    Accepting only `Preview` would break the one venue that actually trades; accepting only
    `dict` is the bug this exists to fix. So the gate accepts both and this function is the seam
    -- deliberately transitional, and deletable the day `preview_order` returns `Preview`.

    **Why a dict is read as native.** The dict shape *is* the Coinbase native-preview response
    (`supports_native_preview=True`), so treating it as a broker quote is accurate rather than
    optimistic. But "accurate today" is not "safe forever": if some future adapter returns a dict
    while synthesizing, silently labelling it a broker quote is exactly the failure this issue is
    about. So a dict that carries a truthy `synthetic` key is believed over the default. Any new
    adapter should return `Preview` and not rely on that.

    `ok=False` means neither shape was recognized -- which is itself a warning, not a blank.
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

    if isinstance(preview, Mapping) and preview:
        errors = preview.get("errs") or preview.get("errors") or ()
        sized = next(
            (
                _preview_decimal(preview[key])
                for key in ("order_total", "quote_size", "est_quote_size")
                if key in preview
            ),
            None,
        )
        # `== 0`, NOT `<= 0`, and the difference is deliberate. A zero size is unambiguous: the
        # order has no size and its cost is unknown. A NEGATIVE one is not -- it could just as
        # easily be Coinbase reporting a SELL's `order_total` as signed proceeds, and that
        # convention has not been verified against a real sell preview. Guessing wrong in the
        # `<= 0` direction would demand a typed phrase on EVERY live sell, which trains the
        # operator to type it by reflex and destroys the signal on the previews that need it.
        # Sign-agnostic here until a live probe settles it; the `Preview` branch above owns its
        # own types and can afford to be stricter.
        return (
            bool(preview.get("synthetic", False)),
            dict(preview),
            tuple(str(error) for error in errors),
            sized is None or sized == 0,
            True,
        )

    return False, {}, (), True, False


def _preview_lines(preview: Any) -> tuple[list[str], bool]:
    """Render `preview` for a human; return `(lines, degraded)`.

    `degraded` is true when the preview is unpriced, carries errors, or could not be read at all
    -- the three cases where the numbers on screen do not mean what they appear to mean.

    Provenance is rendered ABOVE the numbers, not below them, because a footnote under a tidy
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
    except (click.Abort, EOFError):
        click.echo("aborted -- declining.", err=True)
        return False
    return str(typed).strip().lower() == DEGRADED_PREVIEW_PHRASE


def _interactive_confirm(preview: Preview | Mapping[str, Any] | None) -> bool:
    """Human-in-the-loop order confirmation for `mode="confirm"`.

    Called by the executor ONLY after the intent has already passed every hard rail -- this is
    an additional human gate, never a replacement for the rails.

    **What this renders, and why it shouts.** `Preview`'s own docstring sets the requirement:
    "approving an estimate must never look identical to approving a broker's own quote." A
    venue that has a preview endpoint returns numbers it is willing to stand behind; a venue
    without one gets an estimate keel computed from a price lookup that validated nothing and
    reserved nothing. Those two screens used to be byte-identical, because this function took a
    raw dict and had nowhere to put `Preview.synthetic`. They are now visually unmistakable, and
    the distinction sits ABOVE the numbers rather than under them.

    Three further states get their own alarm block: `Preview.errors` (the adapter or the venue
    said something went wrong), an unpriced preview (a zero size renders as a harmless "$0.00
    order" unless something says otherwise), and a preview shape this gate cannot read at all.
    Each of those also upgrades the question from `[y/N]` to a typed phrase -- see `_ask_to_place`
    for why that is friction and not a refusal.

    Accepts both the port's `Preview` and the legacy Coinbase dict; `_read_preview` documents why
    both, and which one the live path actually sends today.

    Fails closed: a non-TTY invocation (a script, a cron job, a headless run) declines rather
    than blocking on stdin, so `mode="confirm"` never trades unattended.
    """
    lines, degraded = _preview_lines(preview)
    # The legacy header names Coinbase because the dict shape IS the Coinbase client's response.
    # A `Preview` can come from any venue, so it gets the venue-neutral header.
    # An unreadable preview is not "Coinbase's" either -- naming a venue over a shape this gate
    # could not parse asserts a provenance it does not have.
    legacy_coinbase_dict = (
        isinstance(preview, Mapping) and bool(preview) and not preview.get("synthetic", False)
    )
    header = "Coinbase order preview" if legacy_coinbase_dict else "Order preview"
    click.echo(f"\nRails PASSED. {header}:")
    for line in lines:
        click.echo(line)
    if not _common._is_interactive():
        click.echo("no TTY -- declining (confirm mode fails closed).", err=True)
        return False
    return _ask_to_place(degraded)
