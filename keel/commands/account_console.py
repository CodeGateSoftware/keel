"""The Account menu (issue #392 C6; PRD §3's Account branch: pnl + versions).

The console tree's LAST placeholder turned real, and the one area that is READ-ONLY
top to bottom: `pnl` and `versions` are both views -- there is no write path here at
all, which the C6 ceremony-audit table states as this module's whole row. Everything
renders through the C1 services the CLI itself calls, never a re-implementation:

* **pnl** -- `keel.commands.pnl.build_pnl_report` + `render_pnl_report` (the exact
  `keel pnl` report, FIFO and all, unchanged) over the ACTIVE deployment's imported
  transactions. An empty transactions table renders its honest empty state naming the
  import path (`keel db import`, the Data menu's form) -- never a confident
  `total realized P&L: 0` over an account whose history was simply never loaded.
* **versions** -- the ONE shared renderer `keel.commands.versions.render_versions_lines`
  (the same lines `keel versions` prints, stderr half included), so the deploy check
  cannot drift between terminal and console. The environment scan (`build_info` +
  `check_install`, an importlib.metadata walk) runs ONCE per entry and the rows are
  HELD -- the Venues browser's contract, never per poll.

All the pure builders are directly unit-testable without curses, mirroring the
`build_*` split of the other console modules; the live loop owns only the I/O.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any

from keel.commands.pnl import build_pnl_report, render_pnl_report
from keel.commands.tui import ScreenLine, _blank
from keel.commands.versions import build_info, check_install, render_versions_lines

#: The width every console line must fit (`_paint` clips at the window width; 80-column
#: terminals are this dashboard's stated target) -- the same budget the other console
#: modules keep, applied by wrapping rather than clipping.
_WIDTH = 78


def _wrap(text: str, *, indent: str = "  ", width: int = _WIDTH) -> list[str]:
    """Wrap `text` on spaces to the 80-column budget, continuation lines carrying `indent`.
    PURE -- the same rule every console module keeps."""
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [
        indent
    ]


# -- the sub-menu model (PRD §3's Account branch) --------------------------------------------------


@dataclass(frozen=True)
class AccountEntry:
    """One entry of the Account sub-menu. `kind` is the closed dispatch vocabulary the
    other console modules keep; both entries here are `"view"` -- the Account branch has
    no form, no ARMED run and no immediate action, by its own inventory."""

    ordinal: int
    label: str
    description: str
    kind: str  # "view"
    target: str  # the view name


#: PRD §3's Account branch in tree order. The descriptions are O8's plain-English "what
#: will this do" in miniature, and they say honestly what each view reads.
ACCOUNT_MENU: tuple[AccountEntry, ...] = (
    AccountEntry(
        ordinal=1,
        label="pnl",
        description=(
            "realized + unrealized FIFO P&L from the imported transactions (read-only; "
            "the exact `keel pnl` report)"
        ),
        kind="view",
        target="pnl",
    ),
    AccountEntry(
        ordinal=2,
        label="versions",
        description=(
            "every keel distribution's version, not just keel-trader's -- the deploy "
            "check that can fail (read-only)"
        ),
        kind="view",
        target="versions",
    ),
)


def account_entry(ordinal: int) -> AccountEntry | None:
    """The entry selected by its displayed ordinal, or `None` -- the one-lookup rule
    every console menu keeps, so the rendered ordinals and the shortcut keys cannot
    drift."""
    for entry in ACCOUNT_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


#: This module's screens' contextual help (O8) -- the rows the `?` overlay renders,
#: keyed by the live loop's mode names. Plain `(subject, description)` pairs so the text
#: stays HERE with the module that owns the screens; `keel.commands.help_console` is the
#: registry and renderer.
CONTEXT_HELP: dict[str, tuple[tuple[str, str], ...]] = {
    "account": (
        (
            "the Account branch",
            "the console's read-only account area: the FIFO P&L report over imported "
            "transactions, and the whole-install version check -- no action here writes "
            "anything",
        ),
        (
            "pnl",
            "the EXACT `keel pnl` report (realized FIFO totals plus open positions), "
            "rendered from the same service over the ACTIVE deployment's imported "
            "transactions; nothing is recomputed locally",
        ),
        (
            "versions",
            "the same lines `keel versions` prints, from the one shared renderer -- a "
            "disagreement between keel distributions renders loud here just as it fails "
            "the CLI's exit code",
        ),
    ),
    "account-pnl": (
        (
            "the report",
            "`keel pnl`'s own output, verbatim, over this deployment's imported "
            "transactions; rebuilt each poll from the database like every offline view",
        ),
        (
            "no marks supplied",
            "the console renders the overall report without marks, exactly `keel pnl` "
            "with no --mark: unrealized P&L needs a price you supply, and none is "
            "inferred here",
        ),
        (
            "the empty state",
            "no imported transactions means exactly that: the report names the import "
            "path (`keel db import`, the Data menu's form) rather than printing a "
            "confident zero",
        ),
    ),
    "account-versions": (
        (
            "the deploy check",
            "every keel distribution installed in this environment, compared -- the same "
            "lines `keel versions` prints from the one shared renderer; the scan runs "
            "once per entry, and the rows are held across repaints",
        ),
        (
            "loud disagreement",
            "a partial install or a dev-only venue renders its error lines in the alert "
            "style -- the console's equivalent of the command's non-zero exit, because a "
            "view cannot exit",
        ),
    ),
}


def build_account_menu_lines(*, cursor: int = 0) -> list[ScreenLine]:
    """The Account sub-menu screen: every entry with its description wrapped to the
    80-column budget, exactly one cursor-marked row. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- account", "heading"),
        _blank(),
    ]
    cursor = max(0, min(cursor, len(ACCOUNT_MENU) - 1))
    for index, entry in enumerate(ACCOUNT_MENU):
        marker = ">" if index == cursor else " "
        head = f"{marker} {entry.ordinal:>2}  {entry.label}"
        lines.append(ScreenLine(head, "heading" if index == cursor else "normal"))
        for wrapped in _wrap(f"{entry.description}.", indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("up/k down/j move · Enter/Space select · 1-2 jump", "muted"))
    lines.append(ScreenLine("q/Esc/m to the console menu", "muted"))
    return lines


# -- pnl: the service's own report, verbatim -------------------------------------------------------


def build_pnl_lines(transactions: list[dict[str, Any]]) -> list[ScreenLine]:
    """The pnl view: `render_pnl_report(build_pnl_report(...))` VERBATIM -- the exact
    `keel pnl` output, the overall report with no marks (the CLI with no --asset and no
    --mark) -- or, when the deployment holds no imported transactions, the honest empty
    state naming the import path instead of a confident zero. PURE over the transaction
    rows the caller read from the ACTIVE deployment."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- account / pnl", "heading"),
        _blank(),
    ]
    if not transactions:
        lines.append(ScreenLine("no imported transactions -- nothing to report yet.", "normal"))
        for wrapped in _wrap(
            "this report reads the Coinbase CSV exports `keel db import` loads (the "
            "Data menu's db import entry); until then there is no cost-basis history to "
            "compute from, and a zero printed here would be a claim, not a reading.",
            indent="",
        ):
            lines.append(ScreenLine(wrapped, "muted"))
        lines.append(_blank())
        lines.append(ScreenLine("q/Esc/m back to the Account menu", "muted"))
        return lines
    report = build_pnl_report(transactions, None, {})
    for line in render_pnl_report(report):
        for wrapped in _wrap(line, indent=""):
            lines.append(ScreenLine(wrapped, "normal"))
    lines.append(_blank())
    for wrapped in _wrap(
        "the overall report with no marks supplied, exactly `keel pnl` with no --mark -- "
        "unrealized P&L needs a price you supply, and none is inferred here.",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("q/Esc/m back to the Account menu", "muted"))
    return lines


# -- versions: one renderer, two front-ends --------------------------------------------------------


def versions_rows() -> list[tuple[str, bool]]:
    """Read the deploy check ONCE: the build identity and the installed-distribution
    report, rendered through the shared `render_versions_lines`. The importlib.metadata
    scan this triggers is the whole reason the live loop holds the rows from entry
    rather than rebuilding per poll (the Venues browser's contract)."""
    info = build_info()
    return render_versions_lines(info, check_install(source=info.source))


def build_versions_lines(rows: list[tuple[str, bool]]) -> list[ScreenLine]:
    """The versions view: the shared renderer's exact `(text, to_stderr)` pairs, the
    stderr half (the not-reproducible warning, the disagreement errors) styled loud --
    a view cannot exit non-zero, so loudness is the console's equivalent. PURE over the
    held rows."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- account / versions", "heading"),
        _blank(),
    ]
    for text, to_stderr in rows:
        if not text:
            lines.append(_blank())
            continue
        style = "alert" if to_stderr and text.startswith("error:") else (
            "warn" if to_stderr else "normal"
        )
        for wrapped in _wrap(text, indent=""):
            lines.append(ScreenLine(wrapped, style))
    lines.append(_blank())
    for wrapped in _wrap(
        "the same lines `keel versions` prints, from the one shared renderer -- a "
        "disagreement fails that command's exit code and renders loud here.",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("q/Esc/m back to the Account menu", "muted"))
    return lines
