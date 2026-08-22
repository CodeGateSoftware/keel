"""The Account menu (issue #392 C6; PRD §3's Account branch: pnl + versions; #415 adds
the update entry).

The console tree's LAST placeholder turned real (C6) as its READ-ONLY area -- and it
stayed that way until the self-update slice (#415) added the branch's ONE write path:
the `update` entry, an ARMED view whose run is TYPED (`keel update`'s own gate
wording, one gate, both front-ends -- its ceremony row). Everything renders through
the C1 services the CLI itself calls, never a re-implementation:

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
* **update** -- `keel.commands.update`, the self-update service: the entry-time check
  (`update_check`: one public-API read + the plan) runs ONCE and the plan is HELD,
  the versions view's contract; Enter is NOT enough -- the run happens at the
  TERMINAL through the suspend dance, behind the CLI's own typed gate, and a
  verified success RELAUNCHES the console (`os.execv` the new build's keel entry,
  terminal already restored). The subprocess/HTTP/execv orchestration is ALL the
  service's; this module renders and asks.

All the pure builders are directly unit-testable without curses, mirroring the
`build_*` split of the other console modules; the live loop owns only the I/O.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from keel.commands import update
from keel.commands.pnl import build_pnl_report, render_pnl_report
from keel.commands.tui import CTRL_C_DISCLOSURE, ScreenLine, _blank
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
    other console modules keep: `"view"` renders a read-only report, and the #415
    `update` entry is `"armed"` -- an ARMED view whose Enter dispatches the run at the
    TERMINAL, behind the CLI's own typed gate (the branch's one write path, and its
    ceremony row in the audit table)."""

    ordinal: int
    label: str
    description: str
    kind: str  # "view" | "armed"
    target: str  # the view name


#: PRD §3's Account branch in tree order, plus the #415 update entry. The descriptions
#: are O8's plain-English "what will this do" in miniature, and they say honestly what
#: each view reads -- the update's names the typed gate and the replacement.
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
    AccountEntry(
        ordinal=3,
        label="update",
        description=(
            "check for a newer release and (after a TYPED confirmation) deploy it into "
            "this launch folder -- backups first, verify, then the console relaunches "
            "itself on the new build"
        ),
        kind="armed",
        target="update",
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
            "the console's account area: the FIFO P&L report and the whole-install "
            "version check are read-only views; the update entry is the branch's ONE "
            "write path, and it is typed-gated",
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
        (
            "update",
            "check for a newer release and deploy it into this launch folder -- the "
            "same service `keel update` runs, behind the same TYPED gate; a refused "
            "gate writes nothing and never relaunches",
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
    "account-update": (
        (
            "the ARMED check",
            "the release check ran ONCE on entry (one public read of the GitHub "
            "releases API, no auth) and the plan is held -- repaints re-check nothing; "
            "the view names current vs latest, the production wheels, the "
            "Release/ dir, the .bak-before-* backups and the RUNNING venv",
        ),
        (
            "Enter is not enough",
            "the run happens at the TERMINAL, behind `keel update`'s own TYPED gate "
            "(the wording names the version, the launch folder and that the running "
            "binary is replaced); a wrong phrase, a decline or no TTY writes nothing",
        ),
        (
            "the relaunch",
            "a VERIFIED success replaces this process with the new build's keel entry "
            "(os.execv, terminal restored first) -- a failure renders its honest state "
            "and the manual recovery instead, and the backups are never deleted",
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
    lines.append(ScreenLine("up/k down/j move · Enter/Space select · 1-3 jump", "muted"))
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


# -- update: the self-update view (issue #415) -----------------------------------------------------


def update_check(
    *,
    fetch: Callable[[str], bytes] | None = None,
    launch_dir: Any = None,
    venv_python: Any = None,
    package_file: Any = None,
) -> update.UpdatePlan:
    """The entry-time read: ONE public-API release check plus the plan -- the versions
    view's contract (read once, hold), never per poll. A network/API failure raises
    `update.UpdateError`, which the live loop holds as the view's honest error state
    (Enter retries); it is never rendered as a confident 'up to date'."""
    return update.plan_update(
        update.latest_release(fetch=fetch),
        launch_dir=launch_dir,
        venv_python=venv_python,
        package_file=package_file,
    )


def build_update_lines(plan: update.UpdatePlan) -> list[ScreenLine]:
    """The update view: the SHARED plan renderer's exact lines (the same report `keel
    update --check` prints -- one renderer, two front-ends), wrapped to the 80-column
    budget. An offered plan renders ARMED with the typed-gate disclosure; a refusal
    renders its reasons and offers nothing to run; up-to-date renders calm. PURE over
    the held plan."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- account / update", "heading"),
        _blank(),
    ]
    for line in update.render_plan_lines(plan):
        if not line.strip():
            lines.append(_blank())
            continue
        for wrapped in _wrap(line, indent=""):
            lines.append(ScreenLine(wrapped, "normal"))
    lines.append(_blank())
    if plan.offered:
        lines.append(ScreenLine("ARMED -- nothing has run yet.", "normal"))
        for wrapped in _wrap(
            "Enter opens the TYPED confirmation at the terminal (`keel update`'s own "
            "wording: it names the version, the launch folder and that the running "
            "binary is REPLACED); only the typed word `yes` proceeds, and the run "
            "blocks this screen exactly like the CLI, with its lines held here when "
            "it ends.",
            indent="",
        ):
            lines.append(ScreenLine(wrapped, "normal"))
        for wrapped in _wrap(
            "a verified success RELAUNCHES the console on the new build (the process "
            "is replaced); a failure renders its honest state and the manual recovery.",
            indent="",
        ):
            lines.append(ScreenLine(wrapped, "muted"))
    else:
        for wrapped in _wrap(
            "nothing will run -- Enter re-checks the release, q/Esc/m returns.",
            indent="",
        ):
            lines.append(ScreenLine(wrapped, "muted"))
    for wrapped in _wrap(CTRL_C_DISCLOSURE, indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("q/Esc/m back to the Account menu", "muted"))
    return lines


def build_update_error_lines(error: str) -> list[ScreenLine]:
    """The check's honest failure state: the error verbatim (wrapped), with Enter as
    the retry and no run offered -- a failed check is never a confident 'up to
    date'. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- account / update", "heading"),
        _blank(),
        ScreenLine("the release check failed:", "alert"),
    ]
    for wrapped in _wrap(error, indent=""):
        lines.append(ScreenLine(wrapped, "warn"))
    lines.append(_blank())
    for wrapped in _wrap(
        "Enter re-checks the release (one public read of the releases API); the "
        "manual procedure in docs/operator-runbook.md ('Deploying a new version') "
        "needs no API call at all.",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("q/Esc/m back to the Account menu", "muted"))
    return lines


def build_update_result_lines(
    result: update.UpdateResult,
    progress: list[str] | tuple[str, ...],
    *,
    relaunch_pending: bool = False,
) -> list[ScreenLine]:
    """The held run result: the streamed step lines VERBATIM above (wrapped, never
    clipped -- how far it got is the detail), then the shared summary/recovery
    renderer's lines, failure text loud. `relaunch_pending` -- a verified success
    whose execv failed, held for an Enter retry -- switches the footer from re-run to
    retry-the-relaunch. PURE over the held values."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- account / update results", "heading"),
        _blank(),
    ]
    if progress:
        for line in progress:
            if not line.strip():
                lines.append(_blank())
                continue
            for wrapped in _wrap(line, indent=""):
                lines.append(ScreenLine(wrapped, "muted"))
        lines.append(_blank())
    for line in update.render_result_lines(result):
        style = "alert" if not result.ok else "ok"
        for wrapped in _wrap(line, indent=""):
            lines.append(ScreenLine(wrapped, style))
    lines.append(_blank())
    for wrapped in _wrap(CTRL_C_DISCLOSURE, indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    footer = (
        "Enter retries the relaunch (re-installs nothing) · q/Esc/m back to the menu"
        if relaunch_pending
        else "Enter re-runs · q/Esc/m back to the Account menu"
    )
    lines.append(ScreenLine(footer, "muted"))
    return lines


def run_update_at_terminal(
    plan: update.UpdatePlan,
    *,
    progress: list[str],
    gate_fn: Callable[[update.UpdatePlan], bool] | None = None,
    relaunch_fn: Callable[[], object] | None = None,
    run_fn: Callable[..., update.UpdateResult] | None = None,
    on_relaunch_failure: Callable[[BaseException], object] | None = None,
) -> update.UpdateResult:
    """THE update run, at the terminal: the CLI's OWN typed gate rides the service's
    `confirm_gate` seam (both shipped front-ends gate the run; nothing keel ships calls
    the service ungated), the service's streamed lines collect into `progress`, and --
    only on a verified success -- the relaunch closure runs (execv, terminal already
    restored by the suspend dance). A relaunch that RAISES (execv refused) is rendered
    into `progress` with the manual `keel tui` start and reported through
    `on_relaunch_failure` -- the update itself is done and verified, so its result is
    returned held-ok, never lost to the exception. `gate_fn`/`run_fn` are injectable
    so the loop's tests can drive the contract without any of it."""
    gate = gate_fn if gate_fn is not None else update.typed_update_gate
    service = run_fn if run_fn is not None else update.run_update
    result = service(plan, echo=progress.append, confirm_gate=lambda: gate(plan))
    if result.ok:
        progress.append(
            f"updated to {plan.target_version} and verified -- relaunching the console "
            "on the new build."
        )
        if relaunch_fn is not None:
            try:
                relaunch_fn()
            except Exception as exc:  # execv refused: the update itself is DONE and verified
                progress.append(f"RELAUNCH FAILED: {exc}")
                progress.append(
                    "the new build IS installed and verified -- run `keel tui` by hand "
                    "(or your deployment wrapper). Enter retries the relaunch; it "
                    "re-installs nothing."
                )
                if on_relaunch_failure is not None:
                    on_relaunch_failure(exc)
                return result
            progress.append("relaunch did not replace the process -- run `keel tui` by hand.")
    return result
