"""Tests for `keel.commands.console` -- the console shell (issue #388 C2, PRD O4/O9).

Three surfaces, all pinned here:

* **The deployment convention (O4)** -- the four known config+db pairs, discovered from the
  tracked config files that exist in the deployment directory, exactly the pairs the
  `keel-paper`/`keel-live`/`keel-paperhourly`/`keel-equities` wrappers pin. A switch only
  ever binds a whole pair; the LIVE pair is guarded by an explicit confirm.
* **The menu shell (PRD §3)** -- the top-level tree's entries with future slices as
  placeholders that render a "lands in Cx" notice. Navigation only: no entry beyond
  Dashboard/Profile/Help does anything in this slice.
* **The session banner (O9)** -- profile + recorded market session + the venue market
  clock, composed from `keel.agent`'s recorded state alone: 24/7, open/closed with next
  open/close, or CLOCK UNAVAILABLE fail-loud when the record is absent or stale.

Mirrors `tests/commands/test_tui.py`'s fixture style (in-memory `Repository`, `_config`,
`NOW_TS`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import click
import pytest
from keel_broker_api.results import MarketSchedule, SessionState

from keel import agent
from keel.commands import console
from keel.commands.console import build_banner_lines
from keel.commands.tui import _human_dt
from keel.config import (
    AutoTradeConfig,
    BrokerConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW_TS = 1_800_000_000

#: The minimal config every profile fixture in these tests is built from -- `load_config`'s
#: own minimal shape (`tests/test_config.py`): allowlist + caps are required; everything
#: else defaults. Two entries so a second profile can be told apart by its allowlist.
_MINIMAL_CONFIG = "allowlist: [BTC]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"
_MINIMAL_CONFIG_ALT = "allowlist: [ETH]\ncaps: {max_exposure_usd: 100, max_per_asset_pct: 0.5}\n"


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    return r


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "ETH"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(
            granularities=[], history_days=365
        ),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _write_all_profiles(deployment_dir: Any) -> None:
    """Materialize every known deployment's config in `deployment_dir` (dbs are created by
    `_open_repo` exactly as the CLI would create them on first use)."""
    for profile in console.KNOWN_PROFILES:
        path = deployment_dir / profile.config_path
        path.write_text(
            _MINIMAL_CONFIG_ALT if profile.key == "paper-hourly" else _MINIMAL_CONFIG
        )


# -- the deployment convention (O4) ---------------------------------------------------------------


def test_the_known_profiles_are_exactly_the_wrapper_pairs() -> None:
    """The convention, stated as a pin: the four deployments the wrappers (`keel-paper`,
    `keel-live`, `keel-paperhourly`, `keel-equities`) and the runbook's deployment table
    define -- each config travelling with its OWN database, and LIVE the only guarded one."""
    assert [(p.key, p.config_path, p.db_path) for p in console.KNOWN_PROFILES] == [
        ("paper-forward", "config.paperforward.yaml", "keel.db"),
        ("live", "config.live-sandbox.yaml", "keel-live.db"),
        ("paper-hourly", "config.paper-hourly.yaml", "keel-paperhourly.db"),
        ("paper-equities", "config.paper-equities.yaml", "keel-equities.db"),
    ]
    assert [p.requires_confirmation for p in console.KNOWN_PROFILES] == [False, True, False, False]


def test_discovery_lists_only_deployments_whose_config_exists(tmp_path: Any) -> None:
    """The profile list is discovered from the tracked config files present in the
    deployment directory -- no registry file, no hard-coded "all four always": a checkout
    without `config.live-sandbox.yaml` must not offer a live entry that cannot load."""
    (tmp_path / "config.paperforward.yaml").write_text(_MINIMAL_CONFIG)
    (tmp_path / "config.paper-hourly.yaml").write_text(_MINIMAL_CONFIG)

    found = console.discover_profiles(base_dir=tmp_path)

    assert [p.key for p in found] == ["paper-forward", "paper-hourly"]


def test_discovery_from_the_working_directory_matches_an_explicit_dir(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`discover_profiles()` with no argument reads the working directory -- the same
    directory the CLI's relative `--config`/`--db` paths resolve against."""
    _write_all_profiles(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert [p.key for p in console.discover_profiles()] == [
        p.key for p in console.KNOWN_PROFILES
    ]


def test_active_profile_matches_the_exact_pair() -> None:
    """The active profile is resolved from the exact config+db PAIR the console was started
    with -- both halves, because the wrappers' whole reason to exist is that the pair is
    what names a deployment."""
    profiles = list(console.KNOWN_PROFILES)
    assert console.active_profile("config.live-sandbox.yaml", "keel-live.db", profiles).key == (
        "live"
    )
    assert console.active_profile("config.paperforward.yaml", "keel.db", profiles).key == (
        "paper-forward"
    )


def test_active_profile_refuses_a_mismatched_pair() -> None:
    """A config from one deployment opened against another deployment's db -- the exact
    footgun the wrappers exist to remove -- is NOT any known profile, and the header must
    not pretend it is."""
    assert console.active_profile("config.live-sandbox.yaml", "keel.db") is None
    assert console.active_profile("config.yaml", "keel.db") is None
    assert console.active_profile("config.paperforward.yaml", "keel-live.db") is None


# -- switching (O4): one action rebinds config+db everywhere --------------------------------------


def _binding(config_path: str = "config.paperforward.yaml", db_path: str = "keel.db"
             ) -> console.ConsoleBinding:
    ctx = click.Context(click.Command("tui"), obj={"config_path": config_path, "db_path": db_path})
    return console.ConsoleBinding(ctx, config_path=config_path, db_path=db_path)


def test_switching_rebinds_the_pair_in_one_action(tmp_path: Any) -> None:
    """The pinned acceptance: switching is ONE action that rebinds config AND db together
    (the wrappers' rule) -- never a config without its database."""
    _write_all_profiles(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    try:
        binding = _binding()
        profiles = console.discover_profiles(base_dir=tmp_path)
        target = next(p for p in profiles if p.key == "paper-hourly")

        toast = console.switch_profile(binding, target, confirm_fn=lambda: True)

        assert binding.config_path == "config.paper-hourly.yaml"
        assert binding.db_path == "keel-paperhourly.db"
        assert "paper-hourly" in toast
        assert "config.paper-hourly.yaml" in toast and "keel-paperhourly.db" in toast
    finally:
        monkeypatch.undo()


def test_switching_to_paper_never_asks_for_confirmation(tmp_path: Any) -> None:
    """Only LIVE is guarded: switching between paper deployments is as ungated as running
    the other wrapper would be, and a confirmation gate here would be ceremony."""
    (tmp_path / "config.paperforward.yaml").write_text(_MINIMAL_CONFIG)
    (tmp_path / "config.paper-hourly.yaml").write_text(_MINIMAL_CONFIG_ALT)
    binding = _binding()
    profiles = console.discover_profiles(base_dir=tmp_path)
    target = next(p for p in profiles if p.key == "paper-hourly")
    asked: list[bool] = []

    console.switch_profile(binding, target, confirm_fn=lambda: (asked.append(True) or True))

    assert asked == []  # paper switching is immediate


def test_live_requires_an_explicit_confirmation(tmp_path: Any) -> None:
    """O3's guard, at the profile seam: pointing the console at the LIVE deployment adds
    real-money answers, so it happens only after an explicit confirm step -- declined
    leaves the binding exactly where it was."""
    (tmp_path / "config.paperforward.yaml").write_text(_MINIMAL_CONFIG)
    (tmp_path / "config.live-sandbox.yaml").write_text(_MINIMAL_CONFIG_ALT)
    binding = _binding()
    profiles = console.discover_profiles(base_dir=tmp_path)
    live = next(p for p in profiles if p.key == "live")

    declined = console.switch_profile(binding, live, confirm_fn=lambda: False)
    assert binding.config_path == "config.paperforward.yaml"
    assert binding.db_path == "keel.db"
    assert "unchanged" in declined.lower()

    accepted = console.switch_profile(binding, live, confirm_fn=lambda: True)
    assert binding.config_path == "config.live-sandbox.yaml"
    assert binding.db_path == "keel-live.db"
    assert "live" in accepted.lower()


def test_switching_to_the_active_profile_is_a_no_op_not_a_confirmation(tmp_path: Any) -> None:
    (tmp_path / "config.paperforward.yaml").write_text(_MINIMAL_CONFIG)
    binding = _binding()
    profiles = console.discover_profiles(base_dir=tmp_path)
    active = next(p for p in profiles if p.key == "paper-forward")
    asked: list[bool] = []

    toast = console.switch_profile(binding, active, confirm_fn=lambda: (asked.append(True) or True))

    assert asked == []
    assert "already" in toast.lower()


def test_a_wrong_pair_is_refused(tmp_path: Any) -> None:
    """The pair rule enforced, not just assumed: a profile whose config+db combination is
    not one of the known pairs -- live's config on the paper database, the exact
    wrong-ledger footgun -- is refused outright, even though each half individually exists."""
    (tmp_path / "config.live-sandbox.yaml").write_text(_MINIMAL_CONFIG)
    binding = _binding()
    wrong_pair = console.DeploymentProfile(
        key="live", label="LIVE", config_path="config.live-sandbox.yaml", db_path="keel.db"
    )

    with pytest.raises(ValueError, match="refus"):
        console.switch_profile(binding, wrong_pair, confirm_fn=lambda: True)
    assert binding.db_path == "keel.db"  # untouched


def test_the_binding_opens_state_through_the_cli_loaders(tmp_path: Any) -> None:
    """The binding's `open_state` IS the CLI path: `_load_cfg`/`_open_repo` over the ctx
    pair -- so a switched console reads exactly what `keel --config X --db Y status` would,
    and rebinding changes what those loaders see."""
    _write_all_profiles(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    try:
        binding = _binding()
        repo_a, config_a = binding.open_state()
        assert config_a.allowlist == ["BTC"]

        binding.rebind(next(p for p in console.KNOWN_PROFILES if p.key == "paper-hourly"))
        repo_b, config_b = binding.open_state()

        assert config_b.allowlist == ["ETH"]  # the other config actually loaded
        # ...and the other database: a state row written through the first repo is not
        # visible through the second.
        repo_a.set_state("kill_switch", True)
        assert repo_b.get_state("kill_switch") is None
    finally:
        monkeypatch.undo()


# -- the menu shell (PRD §3) ---------------------------------------------------------------------


def test_the_menu_is_the_prd_tree_in_order() -> None:
    """The nine top-level entries of PRD §3's tree, in tree order -- the shell is
    navigation, and every future slice's entry is already in its place."""
    assert [entry.label for entry in console.CONSOLE_MENU] == [
        "Dashboard",
        "Profile",
        "Trading",
        "Rules",
        "Compliance",
        "Data",
        "Research",
        "Account",
        "Help",
    ]


def test_only_dashboard_profile_and_help_do_anything_in_this_slice() -> None:
    """C2 ships the SHELL: every other entry is a placeholder owned by a later slice, and
    must say so rather than dead-click."""
    available = [e.label for e in console.CONSOLE_MENU if e.lands_in is None]
    assert available == ["Dashboard", "Profile", "Help"]
    owners = {e.label: e.lands_in for e in console.CONSOLE_MENU if e.lands_in is not None}
    assert owners == {
        "Trading": "C5",
        "Rules": "C4",
        "Compliance": "C3",
        "Data": "C5",
        "Research": "C4",
        "Account": "C5",
    }


def test_the_menu_is_reachable_by_its_displayed_ordinals() -> None:
    """1-9 select the entries in displayed order -- the shortcut keys and the rendered
    ordinals are one function, so they cannot drift."""
    for entry in console.CONSOLE_MENU:
        assert console.menu_entry(entry.ordinal) is entry


def test_an_unknown_ordinal_has_no_entry() -> None:
    assert console.menu_entry(0) is None
    assert console.menu_entry(10) is None


def test_the_menu_screen_renders_every_entry_and_the_lands_in_notices(tmp_path: Any) -> None:
    """The menu screen: every PRD entry visible, placeholders carrying their 'lands in Cx'
    notice inline, and the cursor marking one row."""
    _write_all_profiles(tmp_path)
    lines = console.build_menu_lines(
        console.KNOWN_PROFILES[0], cursor=2, profiles=console.discover_profiles(base_dir=tmp_path)
    )
    texts = [line.text for line in lines]
    for entry in console.CONSOLE_MENU:
        assert any(entry.label in t for t in texts), entry.label
    joined = "\n".join(texts)
    assert "lands in C5" in joined
    assert "lands in C4" in joined
    assert "lands in C3" in joined
    # The cursor marks exactly one row (Trading, index 2 of the entries).
    marked = [t for t in texts if t.lstrip().startswith(">")]
    assert len(marked) == 1 and "Trading" in marked[0]


def test_the_placeholder_screen_names_its_slice_and_says_navigation_only() -> None:
    """Selecting a future entry lands on a notice, not a blank screen: which slice owns the
    behavior, and that the shell renders navigation only -- nothing is invokable from it."""
    trading = console.menu_entry(3)
    assert trading is not None and trading.label == "Trading"
    lines = console.build_placeholder_lines(trading)
    joined = "\n".join(line.text for line in lines)
    assert "C5" in joined
    assert "Trading" in joined
    assert "navigation" in joined.lower()


def test_the_profile_menu_lists_every_discovered_deployment_with_its_pair(tmp_path: Any) -> None:
    """The Profile menu shows each deployment's config+db pair -- the O4 rule that the
    active pair is VISIBLE before any action -- marks the active one, and marks LIVE as
    the guarded row."""
    _write_all_profiles(tmp_path)
    profiles = console.discover_profiles(base_dir=tmp_path)
    lines = console.build_profile_menu_lines(
        profiles,
        cursor=1,
        binding_pair=(profiles[0].config_path, profiles[0].db_path),
    )
    texts = [line.text for line in lines]
    for profile in profiles:
        assert any(profile.config_path in t and profile.db_path in t for t in texts), profile.key
    assert any("active" in t.lower() and "paper-forward" in t for t in texts)
    live_rows = [line for line in lines if "live-sandbox" in line.text]
    assert live_rows and all(line.style == "alert" for line in live_rows)
    assert any("confirm" in t.lower() for t in texts)  # the guard is stated on the row


# -- the session banner (O9) ----------------------------------------------------------------------


def _recorded(
    state: str,
    *,
    recorded_ts: int = NOW_TS - 60,
    next_open_ts: int | None = None,
    next_close_ts: int | None = None,
    fresh: bool = True,
    defused: bool = False,
) -> agent.RecordedSession:
    return agent.RecordedSession(
        venue="alpaca",
        state=state,
        recorded_ts=recorded_ts,
        interval_sec=900,
        next_open_ts=next_open_ts,
        next_close_ts=next_close_ts,
        fresh=fresh,
        defused=defused,
    )


def _banner(
    profile: console.DeploymentProfile | None = None,
    session_bound: bool = True,
    record: agent.RecordedSession | None = None,
) -> list[Any]:
    if profile is None:
        profile = console.KNOWN_PROFILES[0]
    return build_banner_lines(profile, session_bound, record, NOW_TS)


def test_banner_first_line_names_the_profile_and_its_config_db_pair() -> None:
    lines = _banner()
    assert lines[0].text == (
        f"console: paper-forward · {console.KNOWN_PROFILES[0].config_path} "
        f"+ {console.KNOWN_PROFILES[0].db_path}"
    )
    assert lines[0].style == "heading"


def test_banner_marks_the_live_profile_unmistakably() -> None:
    """The live deployment must be impossible to miss: the word LIVE and the strongest
    style the dashboard has -- the same weight a live autonomy line gets."""
    lines = _banner(profile=console.KNOWN_PROFILES[1])
    assert "LIVE" in lines[0].text
    assert lines[0].style == "alert"


def test_banner_names_an_unrecognized_deployment_honestly() -> None:
    """Started with a pair that is not one of the four (the raw `keel tui` default,
    `config.yaml` + `keel.db`), the header shows the pair and says it is no known
    deployment -- it never guesses a label."""
    lines = build_banner_lines(
        None, False, None, NOW_TS, binding_pair=("config.yaml", "keel.db")
    )
    assert "config.yaml" in lines[0].text and "keel.db" in lines[0].text
    assert "unrecognized" in lines[0].text
    assert lines[0].style == "warn"


def test_banner_renders_24_7_for_an_always_open_venue() -> None:
    """A 24/7 venue (every crypto deployment) says so explicitly -- muted, the expected
    quiet -- no matter what any record claims."""
    lines = _banner(session_bound=False)
    session = lines[1]
    assert "24/7" in session.text
    assert session.style == "muted"


def test_banner_renders_open_with_next_open_and_close() -> None:
    lines = _banner(
        record=_recorded(
            "open", next_open_ts=NOW_TS + 43_200, next_close_ts=NOW_TS + 7_200
        )
    )
    session = lines[1]
    assert "OPEN" in session.text
    assert _human_dt(NOW_TS + 7_200) in session.text  # next close, local time
    assert _human_dt(NOW_TS + 43_200) in session.text  # next open, local time
    assert session.style == "ok"


def test_banner_renders_closed_with_the_next_open() -> None:
    lines = _banner(record=_recorded("closed", next_open_ts=NOW_TS + 172_800, defused=True))
    session = lines[1]
    assert "CLOSED" in session.text
    assert _human_dt(NOW_TS + 172_800) in session.text
    assert session.style == "muted"  # an expected weekend, per B1's own convention


def test_banner_renders_clock_unavailable_when_nothing_is_recorded() -> None:
    """A session-bound venue with no record at all: CLOCK UNAVAILABLE, fail-loud -- the
    same posture `fetch --check` keeps, never a guessed-open."""
    lines = _banner(record=None)
    session = lines[1]
    assert "CLOCK UNAVAILABLE" in session.text
    assert session.style == "warn"


def test_banner_renders_clock_unavailable_when_the_record_is_stale() -> None:
    """A record outside its trust window no longer vouches for anything: stale renders as
    CLOCK UNAVAILABLE, not as the state it happened to freeze on."""
    lines = _banner(record=_recorded("open", fresh=False))
    session = lines[1]
    assert "CLOCK UNAVAILABLE" in session.text
    assert session.style == "warn"


def test_banner_renders_a_recorded_unreadable_clock_fail_loud() -> None:
    lines = _banner(record=_recorded("clock_unavailable"))
    session = lines[1]
    assert "CLOCK UNAVAILABLE" in session.text
    assert session.style == "warn"


def test_banner_without_schedule_times_renders_the_state_alone() -> None:
    """A session-bound venue whose adapter carries no schedule (the port default, or a
    pre-#388 third-party adapter): the state renders without invented times."""
    lines = _banner(record=_recorded("open"))
    session = lines[1]
    assert "OPEN" in session.text
    assert "next open" not in session.text.lower() and "next close" not in session.text.lower()


# -- the banner as one recorded-state read (the TUI's entry point) ---------------------------------


def test_console_banner_lines_composes_from_the_repo_and_config_alone(repo: Repository) -> None:
    """The TUI's one-call banner: recorded session + the venue's own session-boundness,
    nothing else -- no broker, no network, no TUI-side calendar."""
    binding = _binding()
    config = _config()  # no broker: section -> coinbase -> 24/7
    lines = console.console_banner_lines(binding, repo, config, NOW_TS)
    assert any("24/7" in line.text for line in lines)
    assert lines[0].text.startswith("console: paper-forward")

    # The same call over a session-bound venue with a recorded schedule renders it:
    alpaca = _config(broker=BrokerConfig(name="alpaca", endpoint="paper", data_feed="iex"))
    repo.set_state("market_session:alpaca", "open")
    repo.set_state("market_session_ts:alpaca", NOW_TS - 60)
    repo.set_state("market_session_interval_sec:alpaca", 900)
    repo.set_state("market_session_next_open:alpaca", NOW_TS + 43_200)
    repo.set_state("market_session_next_close:alpaca", NOW_TS + 7_200)
    lines = console.console_banner_lines(binding, repo, alpaca, NOW_TS)
    session = lines[1]
    assert "OPEN" in session.text
    assert _human_dt(NOW_TS + 7_200) in session.text


def test_venue_session_bound_reads_the_adapter_declaration() -> None:
    """24/7 vs session-bound comes from the ADAPTER's own capabilities declaration (the
    registry holds no broker handle and constructs no transport -- the read is offline),
    and a venue that cannot be resolved is treated as session-bound so the banner fails
    loud rather than assuming a 24/7 it cannot know."""
    assert console.venue_session_bound(_config()) is False  # coinbase default
    alpaca = _config(broker=BrokerConfig(name="alpaca", endpoint="paper", data_feed="iex"))
    assert console.venue_session_bound(alpaca) is True
    unknown = _config(broker=BrokerConfig(name="not-installed"))
    assert console.venue_session_bound(unknown) is True


# -- the port default the 24/7 recordings lean on (kept honest here too) ---------------------------


def test_a_24x7_market_schedule_is_open_with_no_times() -> None:
    """The banner's 24/7 rendering and the recording's "24/7 records nothing" both lean on
    the port's default schedule being OPEN-with-nulls; pinned once more at the console's
    layer so a future port change cannot silently alter the banner's semantics."""
    schedule = MarketSchedule(state=SessionState.OPEN)
    assert schedule.next_open_ts is None and schedule.next_close_ts is None
