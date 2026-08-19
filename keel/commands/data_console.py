"""The Data menu (issue #391 C5; PRD §3's Data branch).

Everything here is DISPATCH, never behavior (PRD O2): `fetch`, `fetch --check` and
`repair gaps` all run `keel.commands.fetch.run_fetch` -- the SAME flow `keel fetch` runs,
with the CLI's own defaults (the allowlist's products, the config's granularities, 5y,
the default tolerance) and a LAZY broker factory, so `--check` and the all-current skip
still never construct one. The freshness overview is the same service's OFFLINE
read-only sweep (a `check=True` run never opens a network connection), rebuilt per poll;
`db import` is the CLI's own import service (`keel.data.csv_import.import_dir`) behind a
path form, with the CLI's own DIR_PATH validation surfaced verbatim.

The ARMED story is the simulate/discover pattern: opening a fetch screen shows the PLAN
(the products x granularities x window the ACTIVE profile's config resolves to, and the
db it warms) and makes NO call; Enter is the confirm step, the run blocks the loop
exactly like the CLI (the screen says so), and the progress lines the CLI would have
streamed are collected and HELD -- rendered as the results, above any error, so a failed
run never hides how far it got.

All the pure builders here are directly unit-testable without curses, mirroring the
`build_*`/`run_*` split of the other console modules.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from keel.commands.fetch import FetchResult, run_fetch
from keel.commands.tui import ScreenLine, _blank, _message_style
from keel.types import Granularity

if TYPE_CHECKING:
    from keel.config import Config
    from keel.data.repository import Repository

#: One terminal prompt: injected so the import form is unit-testable with a scripted
#: fake, and so the live loop can run it through the curses suspend/restore dance.
PromptFn = Callable[[str], str]

#: The width every console line must fit (`_paint` clips at the window width) -- the
#: same budget every console module keeps, applied by wrapping rather than clipping.
_WIDTH = 78


def _wrap(text: str, *, indent: str = "  ", width: int = _WIDTH) -> list[str]:
    """Wrap `text` on spaces to the 80-column budget, continuation lines carrying
    `indent`. PURE -- the same rule every console module keeps."""
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [
        indent
    ]


def _verbatim(line: str, *, width: int = _WIDTH) -> list[str]:
    """Render one of the service's OWN lines: VERBATIM (alignment spaces included -- a
    freshness row's columns are part of what the CLI prints) when it fits the budget,
    wrapped when it does not -- never clipped, never re-indented while it fits."""
    if len(line) <= width:
        return [line]
    return textwrap.wrap(line, width=width) or [""]


# -- the sub-menu model (PRD §3's Data branch) -----------------------------------------------------


@dataclass(frozen=True)
class DataEntry:
    """One entry of the Data sub-menu. `kind` is the closed dispatch vocabulary:
    `"armed"` opens an ARMED view (Enter is the confirm step -- fetch, fetch --check,
    repair gaps), `"view"` renders a read-only report (the freshness overview, rebuilt
    per poll), and `"form"` runs a service at the terminal (db import's path form)."""

    ordinal: int
    label: str
    description: str
    kind: str  # "armed" | "view" | "form"
    target: str


#: PRD §3's Data branch in tree order. The descriptions are O8's plain-English "what
#: will this do" in miniature, naming the dispatch honestly.
DATA_MENU: tuple[DataEntry, ...] = (
    DataEntry(
        ordinal=1,
        label="fetch",
        description=(
            "warm the candle cache for every allowlisted product (Enter confirms first: "
            "it shows the plan, then fetches -- money-safe, data only)"
        ),
        kind="armed",
        target="fetch",
    ),
    DataEntry(
        ordinal=2,
        label="fetch --check",
        description=(
            "the scheduler's dry-run: report freshness and exit -- NEVER touches the "
            "network; the exit verdict renders (Enter runs it)"
        ),
        kind="armed",
        target="fetch-check",
    ),
    DataEntry(
        ordinal=3,
        label="repair gaps",
        description=(
            "re-request interior holes window by window (Enter confirms first: the "
            "venue is re-asked for each gap; per-series outcomes render)"
        ),
        kind="armed",
        target="repair-gaps",
    ),
    DataEntry(
        ordinal=4,
        label="freshness overview",
        description=(
            "the current assessment of every series -- read-only, offline, from the "
            "same sweep `fetch --check` runs"
        ),
        kind="view",
        target="freshness",
    ),
    DataEntry(
        ordinal=5,
        label="db import",
        description=(
            "import Coinbase transaction-history CSV exports into this deployment's db "
            "(read-only w.r.t. the exchange)"
        ),
        kind="form",
        target="db-import",
    ),
)


def data_entry(ordinal: int) -> DataEntry | None:
    """The entry selected by its displayed ordinal, or `None` -- the one-lookup rule
    every console menu keeps, so the rendered ordinals and the shortcut keys cannot
    drift."""
    for entry in DATA_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


def build_data_menu_lines(*, cursor: int = 0, message: str | None = None) -> list[ScreenLine]:
    """The Data sub-menu screen: every entry with its description wrapped to the
    80-column budget, exactly one cursor-marked row, and the last action's confirmation
    lines as the toast. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- data", "heading"),
        _blank(),
    ]
    cursor = max(0, min(cursor, len(DATA_MENU) - 1))
    for index, entry in enumerate(DATA_MENU):
        marker = ">" if index == cursor else " "
        head = f"{marker} {entry.ordinal:>2}  {entry.label}"
        lines.append(ScreenLine(head, "heading" if index == cursor else "normal"))
        for wrapped in _wrap(f"{entry.description}.", indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("up/k down/j move · Enter/Space select · 1-5 jump", "muted"))
    lines.append(ScreenLine("q/Esc/m to the console menu", "muted"))
    if message is not None:
        lines.append(_blank())
        for part in message.splitlines():
            lines.append(ScreenLine(part, _message_style(part)))
    return lines


# -- fetch / fetch --check / repair gaps: the ARMED plan, the run, the held results ----------------


#: `keel fetch --years`'s own default -- the window the console's runs keep.
FETCH_YEARS = 5

#: The three run variants, by target: what each one says it does and how it dispatches.
_FETCH_TARGETS = ("fetch", "fetch-check", "repair-gaps")


@dataclass(frozen=True)
class FetchPlan:
    """What a fetch run WILL do, shown BEFORE any of it runs (the confirm step): the
    products x granularities x window the ACTIVE profile's config resolves to, the db it
    warms, and which variant (`check` never touches the network; `repair_gaps`
    re-requests interior windows)."""

    db_path: str
    products: tuple[str, ...]
    granularities: tuple[Granularity, ...]
    years: int
    check: bool = False
    repair_gaps: bool = False

    @property
    def target(self) -> str:
        if self.check:
            return "fetch-check"
        if self.repair_gaps:
            return "repair-gaps"
        return "fetch"


def fetch_plan(config: Config, db_path: str, target: str) -> FetchPlan:
    """The plan for a console fetch run -- `keel fetch`'s own defaults (the allowlist's
    products in the settlement currency via the same `_default_sim_products` derivation
    every fetch/simulate/monitor surface uses, the config's granularities, `--years 5`)
    and the variant's flags. PURE aside from reading the config it is handed."""
    from keel.commands._products import _default_sim_products

    if target not in _FETCH_TARGETS:
        raise ValueError(f"unknown fetch target: {target!r}")
    return FetchPlan(
        db_path=db_path,
        products=tuple(_default_sim_products(config)),
        granularities=tuple(config.market_data.granularities),
        years=FETCH_YEARS,
        check=target == "fetch-check",
        repair_gaps=target == "repair-gaps",
    )


def build_fetch_armed_lines(plan: FetchPlan) -> list[ScreenLine]:
    """A fetch view's ARMED state: NOTHING has run, and the screen says exactly what
    Enter will do -- the plan (products, granularities, window, db) first, then the
    variant's own story (`--check` never touches the network; repair re-requests
    windows). PURE."""
    lines: list[ScreenLine] = [
        ScreenLine(f"keel console -- data / {plan.target}", "heading"),
        _blank(),
        ScreenLine("ARMED -- nothing has run yet.", "normal"),
        _blank(),
        ScreenLine(
            f"Enter runs `keel fetch`'s flow on THIS deployment ({plan.db_path}):", "normal"
        ),
    ]
    products = ", ".join(plan.products)
    for wrapped in _wrap(f"products {products}", indent="      "):
        lines.append(ScreenLine(wrapped, "normal"))
    granularities = ", ".join(g.value for g in plan.granularities)
    for wrapped in _wrap(
        f"granularities {granularities} · {plan.years}y window (the CLI's own defaults)",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "normal"))
    if plan.check:
        for wrapped in _wrap(
            "this is the --check dry-run: it reports freshness and NEVER opens a network "
            "connection -- the plan above is judged, not fetched.",
            indent="      ",
        ):
            lines.append(ScreenLine(wrapped, "ok"))
    elif plan.repair_gaps:
        for wrapped in _wrap(
            "repair re-requests interior gap windows from the venue (money-safe: data "
            "only). A window the venue cannot supply is recorded absent at source; each "
            "series' outcome renders when the run ends.",
            indent="      ",
        ):
            lines.append(ScreenLine(wrapped, "normal"))
    else:
        for wrapped in _wrap(
            "the warm fetch pulls what is missing up to the window (money-safe: data "
            "only, no orders, no rails) and skips the network entirely when every "
            "series is already current.",
            indent="      ",
        ):
            lines.append(ScreenLine(wrapped, "normal"))
    for wrapped in _wrap(
        "the run can take seconds to minutes; the screen freezes while it runs, exactly "
        "like the CLI, and the exact lines it would have printed are held here when it "
        "ends. Enter again re-runs.",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("Press q or Esc to return to the Data menu.", "muted"))
    return lines


def run_console_fetch(
    repo: Repository,
    config: Config,
    plan: FetchPlan,
    *,
    now_ts: int,
    build_client: Callable[[], Any],
    run_fn: Callable[..., FetchResult] = run_fetch,
    progress: list[str] | None = None,
) -> FetchResult:
    """THE fetch run, dispatched: `run_fetch` itself over the active profile's repo/
    config with the CLI's own defaults and flags, its progress lines collected into
    `progress` (the CLI streamed them; the console shows them as the results). The
    broker factory stays LAZY -- `--check` and the all-current skip never construct one,
    exactly as the CLI's wrapper keeps them. `run_fn` is injectable so the loop's
    confirm-gate tests can spy the call without fetching anything."""
    sink = progress.append if progress is not None else (lambda _message: None)
    return run_fn(
        repo,
        config,
        build_client,
        db_path=plan.db_path,
        products=list(plan.products),
        years=plan.years,
        now_ts=now_ts,
        tolerance_bars=_default_tolerance_bars(),
        check=plan.check,
        refresh=False,
        repair_gaps=plan.repair_gaps,
        echo=sink,
        echo_err=sink,
    )


def _default_tolerance_bars() -> int:
    """`--tolerance-bars`'s own default, from the freshness module that owns it -- never
    a second constant here."""
    from keel.data import freshness as freshness_mod

    return int(freshness_mod.DEFAULT_TOLERANCE_BARS)


def build_fetch_result_lines(
    target: str,
    progress: tuple[str, ...],
    *,
    error: str | None,
    verdict: str | None,
) -> list[ScreenLine]:
    """The held fetch results: the progress lines the CLI would have streamed, VERBATIM
    (wrapped, never clipped -- a freshness row's tail is the detail), the run's failure
    below them when there was one (so the lines that say how far it got stay above), and
    the --check verdict as the service's own message. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine(f"keel console -- data / {target} results", "heading"),
        _blank(),
    ]
    if progress:
        for line in progress:
            if not line.strip():
                lines.append(_blank())
                continue
            for rendered in _verbatim(line):
                lines.append(ScreenLine(rendered, "muted"))
        lines.append(_blank())
    if error is not None:
        lines.append(ScreenLine(f"fetch failed: {error}", "alert"))
        lines.append(_blank())
    elif verdict is not None:
        # `--check`'s verdict, in the service's own words ("N series missing or stale")
        # -- loud, because a scheduler would exit non-zero on it.
        lines.append(ScreenLine(f"--check verdict: FAIL -- {verdict}", "alert"))
        lines.append(_blank())
    if error is not None:
        lines.append(ScreenLine("Press Enter to retry, or q/Esc to close.", "muted"))
    else:
        lines.append(
            ScreenLine("Enter re-runs · q/Esc/m back to the Data menu", "muted")
        )
    return lines


def check_verdict_footer(verdict: str | None) -> list[ScreenLine]:
    """The --check run's PINNED verdict footer: reserved off the window before the body
    is sliced (`compliance_console.pinned_frame`), so no scroll offset can hide what the
    scheduler's dry-run concluded. A PASSING run pins nothing -- its verdict already
    rides the service's own summary lines. PURE."""
    if verdict is None:
        return []
    return [ScreenLine(f"--check verdict: FAIL -- {verdict}", "alert")]


# -- the freshness overview: offline, the current assessment ---------------------------------------


def freshness_lines(
    repo: Repository,
    config: Config,
    db_path: str,
    now_ts: int,
    *,
    build_client: Callable[[], Any] | None = None,
    run_fn: Callable[..., FetchResult] = run_fetch,
) -> tuple[str, ...]:
    """The CURRENT assessment, offline: `run_fetch(check=True)`'s own sweep over the
    repo -- the exact lines `keel fetch --check` prints, collected rather than echoed.
    The check branch never constructs a broker (the factory is lazy inside the service),
    and the factory handed in here exists precisely so a test can prove that by raising.
    `run_fn` is injectable so the loop's offline proofs can spy without sweeping."""
    collected: list[str] = []
    run_fn(
        repo,
        config,
        build_client or (lambda: (_ for _ in ()).throw(AssertionError("unreachable"))),
        db_path=db_path,
        products=_default_products(config),
        years=FETCH_YEARS,
        now_ts=now_ts,
        tolerance_bars=_default_tolerance_bars(),
        check=True,
        echo=collected.append,
        echo_err=collected.append,
    )
    return tuple(collected)


def _default_products(config: Config) -> list[str]:
    from keel.commands._products import _default_sim_products

    return _default_sim_products(config)


def build_freshness_lines(rows: tuple[str, ...]) -> list[ScreenLine]:
    """The freshness overview screen: the sweep's own lines, VERBATIM (the state label,
    the counts, the detail -- wrapped, never clipped), under a title that names it. A
    fresh read is re-taken each poll by the loop; this builder renders whatever it is
    handed. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- data / freshness overview", "heading"),
        _blank(),
        ScreenLine("the current assessment, offline -- the same sweep `fetch --check` runs:",
                    "muted"),
        _blank(),
    ]
    if rows:
        for row in rows:
            for rendered in _verbatim(row):
                lines.append(ScreenLine(rendered, "normal"))
    else:
        lines.append(ScreenLine("(no series -- an empty allowlist?)", "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("q/Esc/m back to the Data menu", "muted"))
    return lines


# -- db import: the path form ----------------------------------------------------------------------


def run_db_import_form(repo: Repository, prompt_fn: PromptFn) -> str:
    """`keel db import` as a form: ask the directory, validate it with the CLI's OWN
    DIR_PATH check (`db.validated_import_dir` -- a bad path refuses with the CLI's exact
    message, verbatim), then run the CLI's own import service and render its own output
    lines. An empty path cancels; a refused path never reaches the import service."""
    import click

    from keel.commands.db import render_import_result, validated_import_dir
    from keel.data.csv_import import import_dir

    raw = prompt_fn(
        "directory holding the Coinbase *.csv exports (read-only w.r.t. the exchange) -- "
        "empty cancels"
    ).strip()
    if not raw:
        return "db import cancelled -- nothing imported"
    try:
        directory = validated_import_dir(raw)
    except click.BadParameter as exc:
        return f"Error: {exc.format_message()}"
    return "\n".join(render_import_result(import_dir(directory, repo)))
