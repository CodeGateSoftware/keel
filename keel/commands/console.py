"""The console shell around the dashboard -- menu navigation, the deployment profiles, and
the session banner (issue #388 C2; PRD O4, O9 and §5 C2).

The PRD's ask for this slice was structure, not features: the TUI grows a menu/sub-menu
navigation whose entries are the PRD §3 tree, with everything beyond Dashboard/Profile/Help
a named placeholder ("lands in C3/C4/C5") owned by a later slice. C3 (issue #389) since
landed the Compliance entry -- its sub-menu lives in `keel.commands.compliance_console`;
the remaining placeholders are owned by C4/C5. The dashboard stays the landing screen,
untouched -- the shell lands AROUND it (`run_live` gains menu modes; the pre-existing modes
and every pure builder are unchanged).

Three pure surfaces lived here at C2; C7 (issue #394) added the Venues browser and
the module's contextual-help rows. All directly unit-testable without curses (the
`build_*`/`discover_*`/`switch_*` functions take their inputs as values), mirroring
`keel/commands/tui.py`'s split between a pure screen model and the thin I/O loop:

* **The deployment convention (O4)** -- the four known config+db pairs, discovered from the
  tracked config files present in the deployment directory. A switch rebinds the WHOLE pair
  through the same `_load_cfg`/`_open_repo` loaders every CLI command uses (`ConsoleBinding`),
  and the LIVE pair is guarded by an explicit confirm step.
* **The menu model (PRD §3)** -- `CONSOLE_MENU`'s nine entries and the builders that render
  them, their placeholder notices, and the Profile menu.
* **The session banner (O9)** -- `build_banner_lines`, composed ONLY from the recorded
  session state (`keel.agent.latest_recorded_session`) and the venue adapter's own
  `session_bound` declaration: 24/7 for always-open venues, OPEN/CLOSED with the recorded
  next open/close for session-bound ones, and CLOCK UNAVAILABLE fail-loud when the record
  is absent or stale. No broker, no network, no TUI-side calendar -- the recording IS the
  source, exactly as `fetch --check`/`status` read it.
* **The Venues browser (O7, C7)** -- `build_venues_lines`, a THIN rendering of the
  `keel.commands.brokers` service payload (the same one `keel brokers list` prints)
  with the SELECTED adapter highlighted and the active deployment's binding named:
  capability display only, never key-presence inference.
* **Contextual help (O8, C7)** -- `CONTEXT_HELP`, this module's screens' rows for the
  `?` overlay; `keel.commands.help_console` is the registry and renderer.

No secrets anywhere in here: the banner and the profile menu render file NAMES and venue
declarations only, never config contents.
"""

from __future__ import annotations

import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from keel import agent
from keel.commands import brokers
from keel.commands._common import _load_cfg, _open_repo
from keel.commands.tui import ScreenLine, _blank, _market_session_style

if TYPE_CHECKING:
    from keel.config import Config
    from keel.data.repository import Repository


# -- the deployment convention (O4) ---------------------------------------------------------------


@dataclass(frozen=True)
class DeploymentProfile:
    """One deployment: a config file and its database, travelling as a PAIR (the wrappers'
    rule -- the whole reason `keel-live`/`keel-paperhourly`/`keel-equities` exist is that
    `--db` defaults to keel.db, and a config opened against the wrong ledger answers about
    the wrong account). `requires_confirmation` is LIVE's guard (O3): pointing the console
    at real money demands an explicit confirm step."""

    key: str
    label: str
    config_path: str
    db_path: str
    requires_confirmation: bool = False


#: THE FOUR KNOWN DEPLOYMENTS, by convention and not by a registry file: each pair is what
#: the deployment's own wrapper pins (`keel-paper`, `keel-live`, `keel-paperhourly`,
#: `keel-equities`) and what `docs/operator-runbook.md`'s deployment table states --
#
#:   paper-forward   config.paperforward.yaml    + keel.db             (the --db default)
#:   live            config.live-sandbox.yaml    + keel-live.db        (guarded)
#:   paper-hourly    config.paper-hourly.yaml    + keel-paperhourly.db
#:   paper-equities  config.paper-equities.yaml  + keel-equities.db
#
#: The tracked config files ARE the registry: `discover_profiles` lists the pairs whose
#: config exists in the deployment directory (the working directory, the same place the
#: CLI resolves its relative `--config`/`--db` paths), so a checkout without a live config
#: offers no live entry, and a new deployment becomes console-visible by shipping its
#: config + wrapper -- no second list to keep alive. The database half of a pair is not
#: existence-checked: `_open_repo` creates and migrates it on first open, exactly as the
#: CLI does for a fresh deployment.
KNOWN_PROFILES: tuple[DeploymentProfile, ...] = (
    DeploymentProfile(
        key="paper-forward",
        label="paper-forward",
        config_path="config.paperforward.yaml",
        db_path="keel.db",
    ),
    DeploymentProfile(
        key="live",
        label="LIVE",
        config_path="config.live-sandbox.yaml",
        db_path="keel-live.db",
        requires_confirmation=True,
    ),
    DeploymentProfile(
        key="paper-hourly",
        label="paper-hourly",
        config_path="config.paper-hourly.yaml",
        db_path="keel-paperhourly.db",
    ),
    DeploymentProfile(
        key="paper-equities",
        label="paper-equities",
        config_path="config.paper-equities.yaml",
        db_path="keel-equities.db",
    ),
)


def discover_profiles(base_dir: Any = None) -> list[DeploymentProfile]:
    """The known deployments whose config file exists in `base_dir` (the working directory
    when omitted), in `KNOWN_PROFILES`' stable order. PURE aside from the `stat` reads --
    no config is parsed here, only named."""
    root = Path.cwd() if base_dir is None else Path(base_dir)
    return [profile for profile in KNOWN_PROFILES if (root / profile.config_path).is_file()]


def active_profile(
    config_path: str, db_path: str, profiles: list[DeploymentProfile] | None = None
) -> DeploymentProfile | None:
    """The deployment the console is currently bound to, by EXACT pair -- both halves,
    because a deployment is the pair, not the config. `None` for anything else (a raw
    `keel tui` on the `config.yaml` default, or a config opened against another
    deployment's db): the banner then names the raw pair and says it recognizes nothing,
    rather than guessing a label an operator would trust."""
    known = KNOWN_PROFILES if profiles is None else tuple(profiles)
    for profile in known:
        if profile.config_path == config_path and profile.db_path == db_path:
            return profile
    return None


class ConsoleBinding:
    """The console's active deployment binding -- the config/db pair every screen reads.

    `open_state` IS the CLI path, not a parallel one: it writes the bound pair into the
    command's `ctx.obj` and calls the same `_load_cfg`/`_open_repo` every CLI command
    uses, so a switched console reads exactly what `keel --config X --db Y status` would.
    `rebind` swaps the pair in one assignment; the next `open_state()` (every poll, every
    screen) reflects it, which is the whole O4 acceptance -- profile switching visibly
    rebinds config/db everywhere in one action.

    Everything STATIC about the bound pair is resolved once per binding and cached here,
    not re-derived per poll: the venue's session-boundness (`session_bound`), whose
    resolution is an adapter-registry walk (`venue_session_bound` -> `load_broker` ->
    `discover_brokers()`, an `importlib.metadata` scan) the banner would otherwise repeat
    on every render of every screen, a few times a minute for as long as the console runs
    -- on top of the deliberate per-poll `open_state()` the banner's recorded-session read
    already needs. `rebind` invalidates the cache, because a different pair means a
    different config and possibly a different venue; a config file edited IN PLACE
    mid-session is not picked up until the operator re-selects the profile (or restarts),
    the same freshness a switched console already grants every other static read.
    """

    def __init__(
        self, ctx: click.Context, *, config_path: str, db_path: str
    ) -> None:
        self._ctx = ctx
        self._config_path = config_path
        self._db_path = db_path
        self._session_bound: bool | None = None

    @property
    def config_path(self) -> str:
        return self._config_path

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def pair(self) -> tuple[str, str]:
        """The bound (config_path, db_path) -- the value `active_profile` resolves."""
        return (self._config_path, self._db_path)

    def session_bound(self, config: Config) -> bool:
        """The bound deployment's venue session-boundness, resolved ONCE per binding (see
        the class docstring) through the same conservative `venue_session_bound` read --
        an unresolvable venue still answers `True`, so the banner fails loud rather than
        assuming a 24/7 it cannot know."""
        if self._session_bound is None:
            self._session_bound = venue_session_bound(config)
        return self._session_bound

    def rebind(self, profile: DeploymentProfile) -> None:
        """Swap the binding to `profile`'s WHOLE pair. No validation here -- `switch_
        profile` is the guarded entry point; this is the one-line mutation beneath it.
        Also drops the cached session-boundness: the new pair's venue may differ."""
        self._config_path = profile.config_path
        self._db_path = profile.db_path
        self._session_bound = None

    def open_state(self) -> tuple[Repository, Config]:
        """Open the bound deployment through the CLI's own loaders (`_common._open_repo`/
        `_load_cfg`), the exact seam `keel tui`'s `open_state` closures always used."""
        self._ctx.obj["config_path"] = self._config_path
        self._ctx.obj["db_path"] = self._db_path
        return _open_repo(self._ctx), _load_cfg(self._ctx)


def switch_profile(
    binding: ConsoleBinding,
    profile: DeploymentProfile,
    *,
    confirm_fn: Callable[[], bool],
    profiles: list[DeploymentProfile] | None = None,
) -> str:
    """Apply a profile switch to `binding`, returning the operator-facing toast text.

    The pair rule enforced, not assumed: `profile` must be one of the discovered known
    pairs -- a config from one deployment on another's database (the wrappers' footgun) is
    REFUSED with a `ValueError` rather than bound. Switching to the already-active pair is
    a calm no-op. The LIVE pair asks `confirm_fn()` first and stays untouched on a decline
    -- an explicit confirm step (a y/N at the terminal in the live loop), deliberately NOT
    O3's typed contract: typed confirmation is for destructive actions, and pointing the
    console at live data changes what you are LOOKING at, not what the engine does.
    """
    known = discover_profiles() if profiles is None else profiles
    if profile not in known:
        raise ValueError(
            f"refusing to bind {profile.config_path} + {profile.db_path}: not one of the "
            "known config+db pairs (a deployment is the PAIR, per the wrappers)"
        )
    if binding.pair == (profile.config_path, profile.db_path):
        return f"profile: {profile.label} already active"
    if profile.requires_confirmation and not confirm_fn():
        return "profile unchanged -- live confirmation not given"
    binding.rebind(profile)
    return f"profile -> {profile.label} ({profile.config_path} + {profile.db_path})"


# -- the menu model (PRD §3) -----------------------------------------------------------------------


@dataclass(frozen=True)
class MenuEntry:
    """One top-level entry of the PRD §3 tree. `lands_in` names the console slice that owns
    the entry's behavior: `None` means the entry works TODAY; anything else is a
    placeholder that renders a 'lands in Cx' notice, so no menu item is ever a dead click
    and no future slice has to restructure the tree.

    `action` is what selecting the entry does in the shell: `"dashboard"`/`"profile"`/
    `"help"` are this slice's three live destinations, `"compliance"` is C3's (issue #389)
    -- the Compliance sub-menu, `keel.commands.compliance_console` -- `"strategy"` and
    `"research"` are C4's (issue #390), `"trading"` and `"data"` are C5's (issue #391) --
    `keel.commands.trading_console` and `keel.commands.data_console` -- and
    `"placeholder"` everything else: a closed vocabulary the live loop dispatches on,
    rather than string-matching labels."""

    ordinal: int
    label: str
    description: str
    lands_in: str | None = None
    action: str = "placeholder"

    @property
    def available(self) -> bool:
        return self.lands_in is None


#: The PRD §3 tree's top level, in tree order. The placeholder owners are the PRD §5
#: phasing's own assignments: Compliance landed with C3 (issue #389); Rules and Research
#: with C4 (issue #390) -- the strategy console (`keel.commands.strategy_console`) and
#: the evidence readers (`keel.commands.research_console`); Trading and Data with C5
#: (issue #391) -- `keel.commands.trading_console` and `keel.commands.data_console`.
#: Account is unassigned by the PRD's phasing and rides with the next slice (C6, the
#: safety & polish pass), and the description says so plainly rather than inventing
#: precision.
CONSOLE_MENU: tuple[MenuEntry, ...] = (
    MenuEntry(
        ordinal=1,
        label="Dashboard",
        description="the live view -- rails, session, positions, freshness, activity",
        action="dashboard",
    ),
    MenuEntry(
        ordinal=2,
        label="Profile",
        description="switch deployment (config+db pair); LIVE asks first",
        action="profile",
    ),
    MenuEntry(
        ordinal=3,
        label="Trading",
        description="agent cycle, monitor, autonomy, record-flow, reset-hwm, kill/resume",
        action="trading",
    ),
    MenuEntry(
        ordinal=4,
        label="Rules",
        description="the strategy console -- ledger, simulate, add, retry",
        action="strategy",
    ),
    MenuEntry(
        ordinal=5,
        label="Compliance",
        description="screen, propose, attest, exemptions, subscription, purification",
        action="compliance",
    ),
    MenuEntry(
        ordinal=6,
        label="Data",
        description="fetch, fetch --check, repair gaps, freshness, db import",
        action="data",
    ),
    MenuEntry(
        ordinal=7,
        label="Research",
        description="experiments, research docs, promotion reports, the trials ledger",
        action="research",
    ),
    MenuEntry(
        ordinal=8,
        label="Account",
        lands_in="C6",
        description="pnl, versions",
    ),
    MenuEntry(
        ordinal=9,
        label="Help",
        description="keys, the glossary, and the safety notes",
        action="help",
    ),
)


def menu_entry(ordinal: int) -> MenuEntry | None:
    """The entry selected by its displayed ordinal (1-9), or `None` -- the shortcut keys
    and the rendered ordinals resolve through this ONE lookup, so they cannot drift."""
    for entry in CONSOLE_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


def build_menu_lines(
    active: DeploymentProfile | None,
    *,
    cursor: int = 0,
    profiles: list[DeploymentProfile] | None = None,
) -> list[ScreenLine]:
    """The console menu screen: every PRD §3 entry on one list, placeholders carrying their
    'lands in Cx' notice inline, exactly one cursor-marked row. PURE."""
    known = KNOWN_PROFILES if profiles is None else tuple(profiles)
    lines: list[ScreenLine] = [ScreenLine("keel console -- menu", "heading")]
    if active is not None:
        lines.append(
            ScreenLine(
                f"active: {active.label} ({active.config_path} + {active.db_path})", "muted"
            )
        )
    else:
        lines.append(ScreenLine("active: no known deployment (see the header)", "muted"))
    lines.append(_blank())
    cursor = max(0, min(cursor, len(CONSOLE_MENU) - 1))
    for index, entry in enumerate(CONSOLE_MENU):
        marker = ">" if index == cursor else " "
        if entry.available:
            text = f"{marker} {entry.ordinal}  {entry.label:<12} {entry.description}"
            style = "heading" if index == cursor else "normal"
        else:
            text = f"{marker} {entry.ordinal}  {entry.label:<12} lands in {entry.lands_in}"
            style = "heading" if index == cursor else "muted"
        lines.append(ScreenLine(text, style))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j move · Enter/Space select · 1-9 jump · q/Esc/m back to the dashboard",
            "muted",
        )
    )
    # `known` is the discovered profile list; the count is surfaced so an operator who is
    # missing a deployment sees why (its config file is not in this directory).
    lines.append(
        ScreenLine(
            f"{len(known)} deployment profile(s) discovered -- the Profile entry switches them",
            "muted",
        )
    )
    return lines


def build_placeholder_lines(entry: MenuEntry) -> list[ScreenLine]:
    """The screen a future slice's entry lands on: a notice naming the owning slice and
    saying what the shell is -- navigation only, nothing invokable from it. PURE."""
    lines = [
        ScreenLine(f"keel console -- {entry.label}", "heading"),
        _blank(),
        ScreenLine(f"{entry.label} lands in slice {entry.lands_in}.", "normal"),
        _blank(),
        ScreenLine(
            f"What it will hold: {entry.description}.", "normal"
        ),
        _blank(),
        ScreenLine(
            "The console shell is navigation only for now -- this entry renders, it does "
            "not act. Nothing here invokes a service, places an order, or changes state.",
            "muted",
        ),
        _blank(),
        ScreenLine("Press q or Esc to return to the menu.", "muted"),
    ]
    return lines


def build_profile_menu_lines(
    profiles: list[DeploymentProfile],
    *,
    cursor: int = 0,
    binding_pair: tuple[str, str] | None = None,
) -> list[ScreenLine]:
    """The Profile menu: every discovered deployment with its config+db pair visible (the
    O4 rule -- the active pair is VISIBLE before any action), the active one marked, the
    LIVE row styled as the guarded one -- plus the PRD §3 tree's Venues entry under the
    deployments (O7), reached with the cursor like any row. PURE."""
    active = (
        active_profile(binding_pair[0], binding_pair[1], profiles) if binding_pair else None
    )
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- profile", "heading"),
        ScreenLine(
            "each deployment is a config+db PAIR -- switching rebinds both, everywhere",
            "muted",
        ),
        _blank(),
    ]
    # The Venues entry rides BELOW the deployments and is part of the SAME cursor range:
    # index len(profiles) is the Venues row, and `profile_menu_venues_at` is the one
    # place that fact is stated, so the live loop's dispatch and this render cannot
    # disagree about which row opens the browser.
    venues_at = len(profiles)
    cursor = max(0, min(cursor, venues_at))
    for index, profile in enumerate(profiles):
        marker = ">" if index == cursor else " "
        suffix = (
            "  [active]"
            if active is not None and active.key == profile.key
            else ""
        )
        text = f"{marker} {profile.label} · {profile.config_path} + {profile.db_path}{suffix}"
        if profile.requires_confirmation:
            # The guard note WRAPS to its own row under the guarded pair rather than riding
            # the pair's line: the pair alone is already ~50 columns, and appending the note
            # ran the row ~27 past the 80-column budget `_paint` clips at -- so the tail
            # ("...asks for confirmation") was exactly the part that vanished. Same alert
            # style on both rows, so the guard reads as one marked entry.
            lines.append(ScreenLine(text, "alert"))
            lines.append(
                ScreenLine(
                    f"      (guarded: selecting {profile.label} asks for confirmation)",
                    "alert",
                )
            )
        else:
            lines.append(ScreenLine(text, "heading" if index == cursor else "normal"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            f"{'>' if cursor == venues_at else ' '} Venues -- every installed adapter and "
            "its capabilities",
            "heading" if cursor == venues_at else "normal",
        )
    )
    lines.append(
        ScreenLine("      (the O7 browser; the selected adapter is highlighted)", "muted")
    )
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j move · Enter/Space switch (Venues: open) · q/Esc/p/m to the menu",
            "muted",
        )
    )
    return lines


def profile_menu_venues_at(profiles: list[DeploymentProfile]) -> int:
    """The cursor index of the Profile menu's Venues row -- the ONE statement of where
    the browser entry sits, so the live loop's "is the cursor on Venues?" test and
    the rendered list cannot drift."""
    return len(profiles)


# -- the Venues browser (O7) ----------------------------------------------------------------------


def _wrap(text: str, *, indent: str = "  ", width: int = 78) -> list[str]:
    """Wrap to the 80-column budget `_paint` clips at -- the same rule every console
    module keeps. PURE."""
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [
        indent
    ]


def build_venues_lines(
    infos: list[brokers.BrokerInfo],
    *,
    selected_venue: str | None,
    profile: DeploymentProfile | None = None,
    binding_pair: tuple[str, str] | None = None,
    endpoint: str | None = None,
    data_feed: str | None = None,
) -> list[ScreenLine]:
    """The Venues browser (O7): every installed adapter with its declared
    capabilities, rendered from the SAME service payload `keel brokers list` prints
    (`brokers.list_installed_brokers` -- the equality is pinned by test), the
    SELECTED adapter (the active deployment's `broker: name:`) highlighted, and the
    active deployment's binding named at the top. PURE, and THIN: this renders the
    service's rows; every fact on it comes from the adapters' own declarations --
    capability display, never key-presence inference."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- profile / venues", "heading"),
    ]
    for wrapped in _wrap(
        "every installed adapter and its declared capabilities (keel brokers list "
        "renders the same payload)",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    # The active deployment's binding, VISIBLE before anything else (O4's rule, applied
    # to the venue half of the pair): which deployment is bound, which venue its config
    # selects, and -- where the venue declares the knobs -- which endpoint and data feed.
    if profile is not None:
        bound = f"{profile.label} ({profile.config_path} + {profile.db_path})"
    elif binding_pair is not None:
        bound = f"{binding_pair[0]} + {binding_pair[1]}"
    else:
        bound = "no known deployment"
    lines.append(ScreenLine(f"bound deployment: {bound}", "normal"))
    binding_bits = [f"venue {selected_venue or '?'}"]
    if endpoint is not None:
        binding_bits.append(f"endpoint {endpoint}")
    if data_feed is not None:
        binding_bits.append(f"data feed {data_feed}")
    for wrapped in _wrap("binding: " + " · ".join(binding_bits)):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    for info in infos:
        if info.error is not None:
            # A raising adapter renders its honest error block (the service's own shared
            # wording) -- never a fabricated capability row (#406 review).
            for row in brokers.adapter_error_block(info):
                lines.append(ScreenLine(row, "warn"))
            continue
        selected = selected_venue is not None and info.name == selected_venue
        version = info.package_version or "unknown version"
        head = f"{info.name} ({version}) · {info.venue}"
        if selected:
            head += "  [selected]"
        lines.append(ScreenLine(head, "heading" if selected else "normal"))
        # The capability facts and order kinds render through the SERVICE's own shared
        # wording (`brokers.capability_facts`) -- the one-phrase rule that pins the CLI
        # and this browser to identical information.
        for wrapped in _wrap(brokers.capability_facts(info)):
            lines.append(ScreenLine(wrapped, "muted"))
        for wrapped in _wrap(f"order kinds: {', '.join(info.supported_orders)}"):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    for wrapped in _wrap(brokers.NO_KEY_INFERENCE_LINE, indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j scroll · PgUp/PgDn/Home/End · q/Esc/v/m back to the profile menu",
            "muted",
        )
    )
    return lines


# -- contextual help (O8, issue #394 C7) ----------------------------------------------------------
#
#: This module's screens' contextual help -- the "what am I looking at" / "what will
#: this do" rows the `?` overlay renders -- keyed by the live loop's mode names. Plain
#: `(subject, description)` pairs so the text stays HERE, with the module that owns the
#: screen; `keel.commands.help_console` is the registry and renderer (it imports THIS
#: module lazily, so this dict imports nothing from it).
CONTEXT_HELP: dict[str, tuple[tuple[str, str], ...]] = {
    "menu": (
        (
            "the console menu",
            "the operator console's tree over the dashboard: every entry is an area "
            "(Trading, Rules, Compliance, ...) or a destination (Dashboard, Profile, Help)",
        ),
        (
            "the cursor and the ordinals",
            "up/k and down/j move the marker, Enter or Space selects, 1-9 jump straight "
            "to the numbered entry",
        ),
        (
            "q / Esc / m",
            "back to the dashboard -- m steps back one level out of any console screen",
        ),
    ),
    "profile": (
        (
            "the deployments",
            "every config+db PAIR the wrappers pin -- a deployment is the PAIR, and "
            "switching rebinds both halves everywhere, in one action",
        ),
        (
            "LIVE (guarded)",
            "selecting the live pair asks an explicit y/N at the terminal first; this "
            "guard is a VIEW switch, not a typed gate -- it changes what the console "
            "answers about, not what the engine does",
        ),
        (
            "Venues",
            "the installed-adapter browser (O7): every venue's declared capabilities, "
            "the selected one highlighted; Enter opens it",
        ),
    ),
    "venues": (
        (
            "the rows",
            "every installed adapter and its DECLARED capabilities -- the same one "
            "service payload `keel brokers list` prints; both front-ends render it",
        ),
        (
            "[selected]",
            "the active deployment's venue: the `broker:` name its config selects",
        ),
        (
            "bound deployment",
            "the active config+db pair, and -- where the venue declares the knobs -- "
            "which endpoint (paper/live) and data feed it is bound to",
        ),
        (
            "capability display only",
            "wired-for-deployment says a shipped config selects this venue; it does "
            "NOT say any key is present. No key presence is read or implied, and no "
            "secret is ever shown",
        ),
    ),
    "placeholder": (
        (
            "a future slice's entry",
            "the console tree is stable: this entry renders a 'lands in Cx' notice "
            "naming the slice that owns it -- navigation only, nothing is invokable",
        ),
    ),
}


# -- the session banner (O9) -----------------------------------------------------------------------


def _short_dt(ts: int) -> str:
    """Local-time `YYYY-MM-DD HH:MM` -- `_human_dt` minus the seconds, for the banner's
    line-two stamps. The banner is two rows on a screen `_paint` clips at the window width
    (80-column terminals are this dashboard's stated target), and line two can carry TWO
    schedule stamps beside their labels: at second precision that row ran ~11 columns past
    the budget and the SECOND timestamp -- the part an operator scrolled for -- was exactly
    what clipped. Seconds are not load-bearing for a next open/close; the date and minute
    are. PURE, and total on any int (same `time.localtime` contract as `_human_dt`)."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def build_banner_lines(
    profile: DeploymentProfile | None,
    session_bound: bool,
    recorded: agent.RecordedSession | None,
    now_ts: int,
    *,
    binding_pair: tuple[str, str] | None = None,
) -> list[ScreenLine]:
    """The two-line header every console screen carries: the active deployment, then the
    market session + clock. PURE -- a function of the binding, the venue's declared
    session-boundness and the RECORDED session, nothing else.

    Line one is the profile: a known paper deployment in the dashboard's heading style,
    LIVE in its alert style (unmistakable -- the same weight a live autonomy line gets),
    and an unrecognized pair named honestly (the raw paths, and a warn) rather than
    guessed at.

    Line two is the session, in B1's own severity vocabulary (`_market_session_style`:
    open ok, closed muted -- an expected weekend, never a warning -- unreadable warn):

    * a 24/7 venue says so explicitly, muted;
    * a session-bound venue renders its recorded state, with the recorded NEXT OPEN/CLOSE
      beside it when the recording carries them;
    * absent or stale (outside the recorded interval's trust window) renders CLOCK
      UNAVAILABLE, fail-loud -- exactly how `fetch --check` treats a record that no longer
      vouches for anything. No TUI-side calendar ever fills a gap.

    Every line fits the 80-column budget `_paint` clips at, worst case first: the two
    fail-loud CLOCK UNAVAILABLE variants lead with the headline (the reason and, for a
    stale record, the when-recorded stamp follow it), and the schedule stamps render
    through `_short_dt` (local time, minute precision) so an OPEN row can carry BOTH the
    recorded next close and next open without either clipping -- the same local-time
    contract `_human_dt` keeps for the dashboard's absolute points in time.
    """
    # Line one: the active deployment. File NAMES only -- a config path and a db path carry
    # no secrets; the config's CONTENTS are never rendered here.
    if profile is None:
        raw = f"{binding_pair[0]} + {binding_pair[1]}" if binding_pair else "the raw pair"
        first = ScreenLine(f"console: {raw} (unrecognized deployment)", "warn")
    elif profile.requires_confirmation:
        first = ScreenLine(
            f"console: {profile.label} (REAL MONEY) · {profile.config_path} + "
            f"{profile.db_path}",
            "alert",
        )
    else:
        first = ScreenLine(
            f"console: {profile.label} · {profile.config_path} + {profile.db_path}",
            "heading",
        )

    # Line two: the recorded session + clock.
    if not session_bound:
        second = ScreenLine("market: 24/7 (always open)", "muted")
        return [first, second]

    if recorded is None:
        second = ScreenLine(
            "market: CLOCK UNAVAILABLE -- no recorded clock yet (the agent cycle records it)",
            "warn",
        )
        return [first, second]
    if not recorded.fresh:
        recorded_at = (
            _short_dt(recorded.recorded_ts) if recorded.recorded_ts is not None else "unknown"
        )
        second = ScreenLine(
            f"market: CLOCK UNAVAILABLE -- the record is stale (recorded {recorded_at})",
            "warn",
        )
        return [first, second]

    times: list[str] = []
    if recorded.state == "open":
        if recorded.next_close_ts is not None:
            times.append(f"closes {_short_dt(recorded.next_close_ts)}")
        if recorded.next_open_ts is not None:
            times.append(f"opens {_short_dt(recorded.next_open_ts)}")
        text = "market: OPEN (venue clock)"
    elif recorded.state == "closed":
        if recorded.next_open_ts is not None:
            times.append(f"opens {_short_dt(recorded.next_open_ts)}")
        text = "market: CLOSED (venue clock) -- cycles skip"
    else:
        text = "market: CLOCK UNAVAILABLE (fail-closed) -- cycles skip until the clock answers"
    if times:
        text = f"{text} · {' · '.join(times)}"
    second = ScreenLine(text, _market_session_style(recorded.state))
    return [first, second]


def venue_session_bound(config: Config) -> bool:
    """Whether the active profile's venue is session-bound, read from the ADAPTER's own
    capabilities declaration -- broker-free and offline: the registry loads the adapter
    CLASS and constructs it without a transport (every first-party adapter's
    `capabilities()` is a constant), so this is a capability DISPLAY, not a broker handle.
    Until the C7 `brokers` service lands, this one-boolean read is the whole surface the
    banner needs from that future service.

    The resolution walks the adapter registry (`load_broker` -> `discover_brokers()`, an
    `importlib.metadata` scan), so callers re-rendering per poll go through
    `ConsoleBinding.session_bound` -- the once-per-binding cache -- rather than calling
    this directly on every banner build.

    A venue that cannot be resolved answers `True` (session-bound) -- the conservative
    direction: an unknown venue renders CLOCK UNAVAILABLE until it records, never an
    assumed 24/7 that would quietly hide a closed market."""
    try:
        from keel_broker_api.registry import load_broker

        adapter_cls = load_broker(config.broker.name)
        return bool(adapter_cls().capabilities().session_bound)
    except Exception:
        return True


def console_banner_lines(
    binding: ConsoleBinding, repo: Repository, config: Config, now_ts: int
) -> list[ScreenLine]:
    """The banner as one read: the binding's deployment + the recorded session + the
    venue's session-boundness (cached per binding -- see `ConsoleBinding`), over the
    repo/config the caller already holds. This is the TUI loop's single entry point; the
    composition (and every rendering decision) lives in `build_banner_lines`."""
    profile = active_profile(binding.config_path, binding.db_path)
    recorded = agent.latest_recorded_session(repo, config, now_ts)
    return build_banner_lines(
        profile,
        binding.session_bound(config),
        recorded,
        now_ts,
        binding_pair=binding.pair,
    )
