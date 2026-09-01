"""keel doctor -- profile health (#640, #642).

#640's incident, reproduced as data: `com.keel.paper-hourly.plist` sat correctly written in
the repo and in `~/keel`, and was simply never `launchctl bootstrap`ped into
`~/Library/LaunchAgents`. Ten days of silence followed -- roughly 240 missed hourly cycles --
and nothing in `keel status` or the old `doctor` said so, because both only ever looked at the
ONE db this process happened to be pointed at, never at the sibling profiles a deployment is
supposed to be running.

`profile_findings` is the pure judgment over already-gathered facts; `collect_profiles` is the
filesystem-and-db collector that gathers them. Kept in separate test sections because that is
exactly the seam the source keeps: `profile_findings` never touches a filesystem or a clock
beyond the `now_ts` it is handed, so its tests build `ProfileHealth` rows by hand.

#642's per-product acceptance -- that a doctor finding can name WHICH product is gated without
a wrapper re-parsing `detail` prose -- is pinned here too, on `data_health_findings`' new
`products` field and its `render_json` round-trip.
"""

from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest

from keel.commands.doctor import (
    STALL_FAIL_INTERVALS,
    STALL_WARN_INTERVALS,
    Finding,
    ProfileHealth,
    SeriesHealth,
    _runner_cadence_sec,
    collect_profiles,
    data_health_findings,
    profile_findings,
    render_json,
    unreadable_profile_findings,
)
from keel.data.db import connect, migrate
from keel.data.freshness import Freshness
from keel.data.repository import Repository
from keel.types import Granularity
from tests.conftest import VALID_CONFIG_YAML

NOW = 1_788_000_000
HOUR = 3600
DAY = 86_400


def _profile(
    label: str = "com.keel.paper-hourly",
    *,
    runner: str = "paper-hourly-run.sh",
    db_file: str = "keel-paperhourly.db",
    scheduled: bool | None = True,
    last_cycle_ts: int | None = NOW - 100,
    cadence_sec: int = HOUR,
) -> ProfileHealth:
    return ProfileHealth(
        label=label,
        runner=runner,
        db_file=db_file,
        scheduled=scheduled,
        last_cycle_ts=last_cycle_ts,
        cadence_sec=cadence_sec,
    )


# -- profile_findings: profile.scheduled ----------------------------------------------------


def test_unscheduled_profile_fails_naming_label_and_exact_bootstrap_command() -> None:
    """#640's own incident: a plist that exists but was never loaded into launchd."""
    findings = profile_findings([_profile(label="com.keel.paper-hourly", scheduled=False)], NOW)
    (scheduled_finding,) = [f for f in findings if f.name == "profile.scheduled"]
    assert scheduled_finding.status == "fail"
    assert "com.keel.paper-hourly" in scheduled_finding.detail
    assert (
        "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.keel.paper-hourly.plist"
        in scheduled_finding.fix
    )


def test_unknown_scheduled_state_warns_not_ok() -> None:
    """launchd itself could not be asked -- 'cannot tell' must never read as 'fine'."""
    findings = profile_findings([_profile(scheduled=None)], NOW)
    (scheduled_finding,) = [f for f in findings if f.name == "profile.scheduled"]
    assert scheduled_finding.status == "warn"
    assert scheduled_finding.status != "ok"


def test_all_scheduled_is_ok() -> None:
    findings = profile_findings([_profile(scheduled=True), _profile(label="com.keel.live")], NOW)
    (scheduled_finding,) = [f for f in findings if f.name == "profile.scheduled"]
    assert scheduled_finding.status == "ok"


def test_one_unscheduled_among_many_still_fails_and_ignores_the_unknowns() -> None:
    # a confirmed-missing profile is worse news than an unreadable one; FAIL must win even
    # when another profile in the same run only reports "cannot tell".
    findings = profile_findings(
        [
            _profile(label="com.keel.live", scheduled=True),
            _profile(label="com.keel.paperforward", scheduled=None),
            _profile(label="com.keel.paper-hourly", scheduled=False),
        ],
        NOW,
    )
    (scheduled_finding,) = [f for f in findings if f.name == "profile.scheduled"]
    assert scheduled_finding.status == "fail"
    assert "com.keel.paper-hourly" in scheduled_finding.detail


# -- profile_findings: profile.cycled, the cadence-relative threshold ------------------------


def test_ten_days_stale_fails_on_hourly_cadence() -> None:
    stale = _profile(cadence_sec=HOUR, last_cycle_ts=NOW - 10 * DAY)
    findings = profile_findings([stale], NOW)
    (cycled,) = [f for f in findings if f.name == "profile.cycled"]
    assert cycled.status == "fail"


def test_ten_days_stale_also_fails_on_daily_cadence() -> None:
    # the SAME absolute age that fails an hourly profile must also fail a daily one -- ten
    # days is broken no matter the cadence, which is the floor a cadence-relative threshold
    # must not undercut.
    stale = _profile(cadence_sec=DAY, last_cycle_ts=NOW - 10 * DAY)
    findings = profile_findings([stale], NOW)
    (cycled,) = [f for f in findings if f.name == "profile.cycled"]
    assert cycled.status == "fail"


def test_ninety_minutes_stale_fails_on_a_fifteen_minute_cadence_but_is_ok_on_daily() -> None:
    # the whole point of a cadence-relative threshold: the identical absolute staleness reads
    # as broken for a fast profile and unremarkable for a slow one. Uses `FIFTEEN_MIN` (900s,
    # the `auto_trade.interval_sec` fallback `VALID_CONFIG_YAML`/`collect_profiles` tests below
    # actually produce): 90 minutes is 6 missed cycles on that cadence -- past the 3-interval
    # FAIL line
    # -- and a rounding error on a once-a-day profile.
    FIFTEEN_MIN = 900
    ninety_min = 90 * 60
    fast = _profile(
        label="com.keel.paper-hourly", cadence_sec=FIFTEEN_MIN, last_cycle_ts=NOW - ninety_min
    )
    daily = _profile(label="com.keel.live", cadence_sec=DAY, last_cycle_ts=NOW - ninety_min)

    (fast_cycled,) = [f for f in profile_findings([fast], NOW) if f.name == "profile.cycled"]
    (daily_cycled,) = [f for f in profile_findings([daily], NOW) if f.name == "profile.cycled"]

    assert fast_cycled.status == "fail"
    assert daily_cycled.status == "ok"


def test_warn_band_between_two_and_three_intervals() -> None:
    age = int(STALL_WARN_INTERVALS * HOUR) + 60  # just past the warn threshold
    assert age <= STALL_FAIL_INTERVALS * HOUR  # still short of the fail threshold
    warn_profile = _profile(cadence_sec=HOUR, last_cycle_ts=NOW - age)
    (cycled,) = [f for f in profile_findings([warn_profile], NOW) if f.name == "profile.cycled"]
    assert cycled.status == "warn"


def test_never_cycled_fails_with_never_cycled_wording() -> None:
    findings = profile_findings([_profile(last_cycle_ts=None)], NOW)
    (cycled,) = [f for f in findings if f.name == "profile.cycled"]
    assert cycled.status == "fail"
    assert "never cycled" in cycled.detail


def test_healthy_profile_cycles_ok() -> None:
    findings = profile_findings([_profile(cadence_sec=HOUR, last_cycle_ts=NOW - 60)], NOW)
    (cycled,) = [f for f in findings if f.name == "profile.cycled"]
    assert cycled.status == "ok"


def test_empty_profile_list_is_ok_not_a_crash() -> None:
    findings = profile_findings([], NOW)
    assert len(findings) == 2
    assert all(f.status == "ok" for f in findings)
    assert {f.name for f in findings} == {"profile.scheduled", "profile.cycled"}
    # pins the dedicated empty-input path (not just "the general path happens to also be ok
    # for zero profiles"): both findings say plainly that nothing is configured.
    assert all(f.headline == "no profiles configured" for f in findings)


# -- collect_profiles: a real temporary deployment directory ---------------------------------


def _write_plist(directory: Path, label: str, runner_name: str) -> Path:
    """A real plist with the same shape as the four tracked in the repo root: `Label` and
    `ProgramArguments = ["/bin/bash", "<abs path>/<name>-run.sh"]` -- the operator's absolute
    path, which `collect_profiles` must re-resolve to the BASENAME inside `deployment_dir`."""
    path = directory / f"{label}.plist"
    with path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": label,
                "ProgramArguments": [
                    "/bin/bash",
                    f"/some/operator/specific/path/{runner_name}",
                ],
            },
            handle,
        )
    return path


def _write_runner(directory: Path, name: str, *, config_file: str, db_file: str | None) -> Path:
    path = directory / name
    db_flag = f" --db {db_file}" if db_file else ""
    script = (
        '#!/bin/bash\ncd "$(dirname "$0")"\n'
        f"./.venv/bin/keel --config {config_file}{db_flag} agent\n"
    )
    path.write_text(script)
    return path


def _write_db(directory: Path, name: str, last_feed_ts: int) -> Path:
    path = directory / name
    conn = connect(str(path))
    migrate(conn)
    Repository(conn).set_state("last_feed_ts", last_feed_ts)
    conn.close()
    return path


@pytest.fixture
def deployment_dir(tmp_path: Path) -> Path:
    (tmp_path / "config.paper-hourly.yaml").write_text(VALID_CONFIG_YAML)
    _write_plist(tmp_path, "com.keel.paper-hourly", "paper-hourly-run.sh")
    _write_runner(
        tmp_path,
        "paper-hourly-run.sh",
        config_file="config.paper-hourly.yaml",
        db_file="keel-paperhourly.db",
    )
    _write_db(tmp_path, "keel-paperhourly.db", last_feed_ts=NOW - 300)
    return tmp_path


def test_collect_profiles_reads_label_runner_db_cadence_and_last_cycle(
    deployment_dir: Path,
) -> None:
    (profile,), failed = collect_profiles(deployment_dir, frozenset({"com.keel.paper-hourly"}), NOW)
    assert profile.label == "com.keel.paper-hourly"
    assert profile.runner == "paper-hourly-run.sh"
    assert profile.db_file == "keel-paperhourly.db"
    # this fixture's runner has no recognisable day/hour stamp, so cadence falls back to
    # VALID_CONFIG_YAML's auto_trade.interval_sec.
    assert profile.cadence_sec == 900
    assert profile.last_cycle_ts == NOW - 300
    assert profile.scheduled is True
    assert failed == []


def test_collect_profiles_reports_unscheduled_when_label_absent_from_loaded_set(
    deployment_dir: Path,
) -> None:
    (profile,), _failed = collect_profiles(deployment_dir, frozenset({"com.keel.live"}), NOW)
    assert profile.scheduled is False


def test_collect_profiles_reports_none_scheduled_when_launchd_unreadable(
    deployment_dir: Path,
) -> None:
    (profile,), _failed = collect_profiles(deployment_dir, None, NOW)
    assert profile.scheduled is None


def test_collect_profiles_runner_with_no_db_flag_defaults_to_keel_db(tmp_path: Path) -> None:
    (tmp_path / "config.paperforward.yaml").write_text(VALID_CONFIG_YAML)
    _write_plist(tmp_path, "com.keel.paperforward", "paperforward-run.sh")
    _write_runner(
        tmp_path, "paperforward-run.sh", config_file="config.paperforward.yaml", db_file=None
    )
    (profile,), _failed = collect_profiles(tmp_path, frozenset(), NOW)
    assert profile.db_file == "keel.db"
    # no keel.db was written for this profile -- an absent db must not raise, just report
    # "never cycled".
    assert profile.last_cycle_ts is None


def test_collect_profiles_corrupt_plist_alongside_good_one_skips_only_the_corrupt_one(
    deployment_dir: Path,
) -> None:
    corrupt = deployment_dir / "com.keel.paper-equities.plist"
    corrupt.write_bytes(b"this is not a plist file at all \x00\x01\x02")

    profiles, failed = collect_profiles(deployment_dir, frozenset(), NOW)

    assert len(profiles) == 1
    assert profiles[0].label == "com.keel.paper-hourly"


def test_collect_profiles_corrupt_plist_is_reported_not_silently_dropped(
    deployment_dir: Path,
) -> None:
    """Defect 3: the corrupt sibling from the test above must not just be absent -- it must be
    NAMED in the second return value, exactly like `keel/mcp/tools.py::_profiles()`'s existing
    `failed` list, so a doctor run can say out loud that it could not read this file."""
    corrupt = deployment_dir / "com.keel.paper-equities.plist"
    corrupt.write_bytes(b"this is not a plist file at all \x00\x01\x02")

    profiles, failed = collect_profiles(deployment_dir, frozenset(), NOW)

    assert len(profiles) == 1
    assert failed == [
        {
            "file": "com.keel.paper-equities.plist",
            "error": failed[0]["error"],  # the exact expat/plistlib message is not our contract
        }
    ]
    assert failed[0]["error"]  # but SOME reason must be present, never a blank string


def test_collect_profiles_missing_db_file_is_never_cycled_not_a_skip(tmp_path: Path) -> None:
    (tmp_path / "config.live.yaml").write_text(VALID_CONFIG_YAML)
    _write_plist(tmp_path, "com.keel.live", "live-run.sh")
    _write_runner(tmp_path, "live-run.sh", config_file="config.live.yaml", db_file="keel.db")
    # deliberately no keel.db written

    (profile,), _failed = collect_profiles(tmp_path, frozenset({"com.keel.live"}), NOW)
    assert profile.last_cycle_ts is None
    assert profile.scheduled is True  # the profile itself is still fully resolved


def test_collect_profiles_empty_directory_returns_empty_list(tmp_path: Path) -> None:
    assert collect_profiles(tmp_path, frozenset(), NOW) == ([], [])


# -- collect_profiles: the runner's real invocation, not whatever text matches first ---------
#
# The real wrappers never pass `--config`/`--db` as literals -- they invoke
# `"$KEEL" --config "$CONFIG" --db "$DB" ...` with CONFIG/DB assigned earlier in the script.
# A naive `re.search` over the whole file also risks matching a comment before it ever reaches
# that invocation. These pin that collect_profiles resolves the shell variables from their own
# assignments and ignores comment text entirely, no matter what it says or where it sits.


def _write_variable_runner(
    directory: Path,
    name: str,
    *,
    config_file: str,
    db_file: str,
    config_var: str = "$CONFIG",
    db_var: str = "$DB",
    decoy_comment: str | None = None,
) -> Path:
    """A runner shaped like the real wrappers: `CONFIG=`/`DB=` assignments feeding a
    `"$KEEL" --config "$CONFIG" --db "$DB"` invocation, with an optional decoy comment line
    (naming DIFFERENT files) placed above the real assignments -- exactly where
    `keel-live-run.sh`'s own "check which one is live" note sits relative to its cycle."""
    path = directory / name
    lines = ["#!/bin/bash"]
    if decoy_comment is not None:
        lines.append(f"# {decoy_comment}")
    lines.append(f'CONFIG="{config_file}"')
    lines.append(f'DB="{db_file}"')
    lines.append(f'./.venv/bin/keel --config "{config_var}" --db "{db_var}" agent')
    path.write_text("\n".join(lines) + "\n")
    return path


def test_collect_profiles_resolves_shell_variables_to_their_assigned_files(
    deployment_dir: Path,
) -> None:
    """The wrappers' actual shape: `--config "$CONFIG" --db "$DB"` with CONFIG/DB assigned
    above. Without variable resolution, `load_config` is handed the literal string
    `"$CONFIG"`, raises, and the broad `except Exception: continue` silently drops the
    profile."""
    runner = deployment_dir / "paper-hourly-run.sh"
    runner.unlink()
    _write_variable_runner(
        deployment_dir,
        "paper-hourly-run.sh",
        config_file="config.paper-hourly.yaml",
        db_file="keel-paperhourly.db",
    )

    (profile,), _failed = collect_profiles(deployment_dir, frozenset(), NOW)

    assert profile.db_file == "keel-paperhourly.db"


def test_a_decoy_comment_naming_different_files_never_wins_over_the_real_invocation(
    deployment_dir: Path,
) -> None:
    """The actual defect this pins: `re.search` returns the FIRST match anywhere in the file,
    including inside a comment. `keel-live-run.sh` shipped a "check which one is live" comment
    that happened to name the right files in the right order, so the old regex "worked" by
    coincidence of comment wording -- reword it, reorder its flags, or delete it, and the
    profile would have silently vanished. Here the comment deliberately names the WRONG files
    (`config.WRONG.yaml` / `wrong.db`), which do not exist and are not written by this test;
    if the resolved profile ever reads them instead of the real `$CONFIG`/`$DB` assignments,
    `load_config` raises on a file that was never created, the broad `except` swallows it, and
    `collect_profiles` returns an EMPTY list rather than the one real profile -- a comment
    must never be a source of truth for what a script actually runs."""
    runner = deployment_dir / "paper-hourly-run.sh"
    runner.unlink()
    _write_variable_runner(
        deployment_dir,
        "paper-hourly-run.sh",
        config_file="config.paper-hourly.yaml",
        db_file="keel-paperhourly.db",
        decoy_comment="example: keel --config config.WRONG.yaml --db wrong.db",
    )

    (profile,), _failed = collect_profiles(deployment_dir, frozenset(), NOW)

    assert profile.db_file == "keel-paperhourly.db"


def test_brace_form_variable_reference_also_resolves(deployment_dir: Path) -> None:
    """`${CONFIG}` is the same reference as `$CONFIG`, just braced -- shells accept both, so
    collect_profiles must too."""
    runner = deployment_dir / "paper-hourly-run.sh"
    runner.unlink()
    _write_variable_runner(
        deployment_dir,
        "paper-hourly-run.sh",
        config_file="config.paper-hourly.yaml",
        db_file="keel-paperhourly.db",
        config_var="${CONFIG}",
        db_var="${DB}",
    )

    (profile,), _failed = collect_profiles(deployment_dir, frozenset(), NOW)

    assert profile.db_file == "keel-paperhourly.db"


def test_variable_with_no_matching_assignment_skips_the_profile_rather_than_raising(
    deployment_dir: Path,
) -> None:
    """A runner referencing `$CONFIG` with no `CONFIG=` assignment anywhere in the script
    cannot be resolved to a real file. That must skip the profile exactly like today's
    "no --config in the file at all" case -- not raise, and not hand `load_config` the raw
    literal string `$CONFIG`. It must, however, still show up as a REPORTED failure (Defect 3)
    rather than vanish."""
    runner = deployment_dir / "paper-hourly-run.sh"
    runner.write_text('#!/bin/bash\n./.venv/bin/keel --config "$CONFIG" --db "$DB" agent\n')

    profiles, failed = collect_profiles(deployment_dir, frozenset(), NOW)
    assert profiles == []
    assert len(failed) == 1
    assert failed[0]["file"] == "com.keel.paper-hourly.plist"
    assert "$CONFIG" in failed[0]["error"]


def test_the_real_shipped_live_wrapper_resolves_without_its_comment_as_the_crutch(
    tmp_path: Path,
) -> None:
    """The regression pin that would have caught the actual incident: read the repository's
    OWN `keel-live-run.sh` verbatim (not a synthetic stand-in), and confirm collect_profiles
    resolves its config/db from the real `CONFIG=`/`DB=` assignments and cycle invocation --
    not from line 15's "check which one is live" comment. Comment-stripping inside
    collect_profiles means this passes regardless of whether that comment exists, is reworded,
    or has its flags reordered -- which is exactly the property the old first-match regex
    lacked."""
    repo_root = Path(__file__).resolve().parents[2]
    live_runner_src = repo_root / "keel-live-run.sh"
    assert live_runner_src.exists(), "this pin only means something against the real script"

    (tmp_path / "config.live-sandbox.yaml").write_text(VALID_CONFIG_YAML)
    _write_plist(tmp_path, "com.keel.live", "keel-live-run.sh")
    (tmp_path / "keel-live-run.sh").write_text(live_runner_src.read_text())

    (profile,), _failed = collect_profiles(tmp_path, frozenset(), NOW)

    assert profile.db_file == "keel-live.db"
    # the real wrapper stamps the UTC DATE (`date -u '+%Y-%m-%d'`), not the hour -- it cycles
    # once a day, and cadence_sec must say so, not the coincidental 900s of its
    # auto_trade.interval_sec.
    assert profile.cadence_sec == 86_400


# -- _runner_cadence_sec: the cadence comes from the runner's own stamp, not the config -------
#
# Defect 2: `collect_profiles` used to take cadence straight from `config.auto_trade.interval_sec`
# -- the auto-trade LOOP's sleep interval, not launchd's cadence. Verified against the real
# deployment: that field is 900 (15 minutes) on BOTH `com.keel.live` and `com.keel.paperforward`,
# whose runners in fact cycle once a DAY, so `profile.cycled`'s 3-interval FAIL threshold worked
# out to 45 minutes against a book that cycles once every 24 hours -- a permanent false alarm on
# 3 of the 4 tracked profiles. These pin that the cadence instead comes from the runner's own
# day/hour stamp format.


def test_an_hourly_stamp_format_yields_a_3600_second_cadence() -> None:
    code_text = 'THIS_HOUR="$(date -u \'+%Y-%m-%dT%H\')"\n'
    assert _runner_cadence_sec(code_text, fallback_sec=999) == 3600


def test_a_daily_stamp_format_yields_an_86400_second_cadence() -> None:
    code_text = 'TODAY_RAW="$(date -u \'+%Y-%m-%d\')"\n'
    assert _runner_cadence_sec(code_text, fallback_sec=999) == 86_400


def test_the_anchoring_pin_a_date_stamp_beside_a_full_timestamp_log_line_still_reads_daily() -> (
    None
):
    """The whole trick, pinned directly: `keel-live-run.sh` stamps the UTC DATE
    (`date -u '+%Y-%m-%d'`) but ALSO logs with `date -u '+%Y-%m-%dT%H:%M:%SZ'` elsewhere in the
    same script. Without anchoring the closing quote immediately after `%H`, the hourly pattern
    would match that log-line timestamp too (it does contain `%Y-%m-%dT%H`) and wrongly give a
    once-a-day profile an hourly cadence -- exactly the bug this function exists to prevent."""
    code_text = (
        "TODAY_RAW=\"$(date -u '+%Y-%m-%d')\"\n"
        "HOUR_RAW=\"$(date -u '+%H')\"\n"
        "printf '%s\\n' \"$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf 'unknown')\"\n"
    )
    assert _runner_cadence_sec(code_text, fallback_sec=999) == 86_400


def test_an_unrecognised_stamp_falls_back_to_the_configs_interval_sec() -> None:
    code_text = 'echo "no date stamp of any recognised shape here"\n'
    assert _runner_cadence_sec(code_text, fallback_sec=1234) == 1234


# -- collect_profiles: end-to-end over the repository's four REAL tracked deployment files ----


def test_collect_profiles_resolves_all_four_real_profiles_at_their_true_cadence(
    tmp_path: Path,
) -> None:
    """The regression pin that would have caught all three defects at once: run
    `collect_profiles` over a directory holding the repository's own four plists, four runner
    scripts, and four configs -- untouched, exactly as committed -- and confirm every profile
    resolves at the cadence it ACTUALLY runs at. Before the three fixes in this change: Defect 1
    made `com.keel.paperforward.plist` unparseable, so it would be silently absent here; Defect 2
    made every non-paper-hourly profile's cadence read `config.auto_trade.interval_sec` (900 for
    live and paperforward) instead of their true once-a-day cadence."""
    repo_root = Path(__file__).resolve().parents[2]
    tracked_files = [
        "com.keel.live.plist",
        "com.keel.paper-equities.plist",
        "com.keel.paper-hourly.plist",
        "com.keel.paperforward.plist",
        "keel-live-run.sh",
        "paper-equities-run.sh",
        "paper-hourly-run.sh",
        "paperforward-run.sh",
        "config.live-sandbox.yaml",
        "config.paper-equities.yaml",
        "config.paper-hourly.yaml",
        "config.paperforward.yaml",
    ]
    for name in tracked_files:
        src = repo_root / name
        assert src.exists(), f"this pin only means something against the real {name}"
        (tmp_path / name).write_text(src.read_text())

    profiles, failed = collect_profiles(tmp_path, frozenset(), NOW)

    assert failed == []
    cadence_by_label = {p.label: p.cadence_sec for p in profiles}
    assert cadence_by_label == {
        "com.keel.live": 86_400,
        "com.keel.paperforward": 86_400,
        "com.keel.paper-hourly": 3600,
        "com.keel.paper-equities": 86_400,
    }


# -- profile.unreadable: Defect 3, an unresolvable profile must be reported, not dropped -------


def test_no_failures_reports_ok() -> None:
    (finding,) = unreadable_profile_findings([])
    assert finding.name == "profile.unreadable"
    assert finding.status == "ok"


def test_a_failure_is_named_by_file_and_reason() -> None:
    (finding,) = unreadable_profile_findings(
        [{"file": "com.keel.paperforward.plist", "error": "not well-formed (invalid token)"}]
    )
    assert finding.name == "profile.unreadable"
    assert finding.status == "warn"
    assert "com.keel.paperforward.plist" in finding.detail
    assert "not well-formed (invalid token)" in finding.detail


def test_a_malformed_plist_is_reported_while_its_valid_sibling_still_resolves(
    deployment_dir: Path,
) -> None:
    """End to end: a corrupt plist alongside a good one must produce BOTH a resolved profile
    AND a named failure -- collect_profiles' two return values wired straight into
    profile_findings and unreadable_profile_findings, the way `doctor_cmd` actually wires them."""
    corrupt = deployment_dir / "com.keel.paper-equities.plist"
    corrupt.write_bytes(b"this is not a plist file at all \x00\x01\x02")

    profiles, failed = collect_profiles(
        deployment_dir, frozenset({"com.keel.paper-hourly"}), NOW
    )
    cycled_findings = profile_findings(profiles, NOW)
    unreadable_findings = unreadable_profile_findings(failed)

    assert any(f.label == "com.keel.paper-hourly" for f in profiles)
    (unreadable,) = unreadable_findings
    assert unreadable.status == "warn"
    assert "com.keel.paper-equities.plist" in unreadable.detail
    # the valid sibling's own findings are unaffected -- it is judged on its own facts, not
    # dragged down by the unrelated corrupt file.
    assert any(f.name == "profile.scheduled" and f.status == "ok" for f in cycled_findings)


# -- #642: structured product identity on Finding ---------------------------------------------


def _fresh(**overrides: object) -> Freshness:
    base = dict(
        product="BTC-USD",
        granularity=Granularity.ONE_HOUR,
        n_candles=8000,
        last_ts=NOW - 3600,
        bars_behind=0,
        gaps=0,
        missing=False,
        stale=False,
        market_closed=False,
    )
    base.update(overrides)
    return Freshness(**base)  # type: ignore[arg-type]


def _series(fresh: Freshness, unexplained_gaps: int = 0) -> SeriesHealth:
    return SeriesHealth(
        product=fresh.product,
        granularity=fresh.granularity.value,
        freshness=fresh,
        unexplained_gaps=unexplained_gaps,
    )


def test_missing_series_products_names_exactly_the_cold_products() -> None:
    fresh_btc = _series(_fresh(product="BTC-USD"))
    cold_sol = _series(
        _fresh(product="SOL-USD", n_candles=0, last_ts=None, missing=True, stale=True)
    )
    (missing, _stale, _gaps) = data_health_findings([fresh_btc, cold_sol])
    assert missing.products == ("SOL-USD",)
    assert "BTC-USD" not in missing.products


def test_stale_series_products_names_exactly_the_stale_products() -> None:
    fresh_btc = _series(_fresh(product="BTC-USD"))
    stale_eth = _series(_fresh(product="ETH-USD", bars_behind=5, stale=True))
    (_missing, stale_finding, _gaps) = data_health_findings([fresh_btc, stale_eth])
    assert stale_finding.products == ("ETH-USD",)


def test_gappy_series_products_names_exactly_the_gappy_products() -> None:
    fresh_btc = _series(_fresh(product="BTC-USD"))
    gappy_eth = _series(_fresh(product="ETH-USD", gaps=3), unexplained_gaps=3)
    (_missing, _stale, gaps_finding) = data_health_findings([fresh_btc, gappy_eth])
    assert gaps_finding.products == ("ETH-USD",)


def test_ok_findings_carry_no_products() -> None:
    (missing, stale, gaps) = data_health_findings([_series(_fresh())])
    assert missing.products == ()
    assert stale.products == ()
    assert gaps.products == ()


def test_products_are_sorted_and_deduplicated_across_granularities() -> None:
    cold_sol_1h = _series(_fresh(product="SOL-USD", granularity=Granularity.ONE_HOUR, missing=True))
    cold_sol_1d = _series(_fresh(product="SOL-USD", granularity=Granularity.ONE_DAY, missing=True))
    cold_avax = _series(_fresh(product="AVAX-USD", missing=True))
    (missing, _stale, _gaps) = data_health_findings([cold_sol_1h, cold_sol_1d, cold_avax])
    assert missing.products == ("AVAX-USD", "SOL-USD")


def test_render_json_round_trips_products() -> None:
    findings = [Finding("data.missing", "fail", "headline", "detail", "keel fetch", ("SOL-USD",))]
    parsed = json.loads(render_json(findings))
    assert parsed[0]["products"] == ["SOL-USD"]


def test_render_json_products_defaults_to_empty_list() -> None:
    findings = [Finding("rail.kill_switch", "ok", "clear", "-", "-")]
    parsed = json.loads(render_json(findings))
    assert parsed[0]["products"] == []
