"""The HOURLY paper profile's tracked deployment assets (issue #337).

`config.paper-hourly.yaml` + `com.keel.paper-hourly.plist` + `paper-hourly-run.sh` +
`keel-paperhourly` are the third deployment profile, tracked in-repo exactly like the
paperforward and live ones (since 2026-08-03; see docs/RELEASING.md). Nothing about them is
executed by the test suite's code paths, so like `tests/test_schedule.py` for the live
schedule, this file pins them so they cannot drift silently: the config must stay a
deliberate SUPERSET of paperforward's universe at the hourly cadence (11 Tier-2 additions
capped at 2% each since #351; the incumbents rescaled 1.00 -> 0.78, rules/params untouched),
the plist must keep 24 hourly triggers and `RunAtLoad`, and
the runner must keep its once-per-UTC-hour stamp (the paperforward day-stamp would collapse
23 of the 24 cycles into no-ops -- the exact regression a copy-paste of that script would
ship).

The runner tests execute the REAL script verbatim through a harness that shims `date` (so
they do not depend on, or wait on, the wall clock) and stubs the deployment's
`.venv/bin/keel`. No `sandbox-exec` is needed here, unlike `test_schedule.py`: this script
places nothing real, and is repointed away from `~/keel` before it runs. It DOES notify now
(#642 gave it a `notify()` seam identical in shape to `keel-live-run.sh`'s), so the harness
still redirects the literal `/usr/bin/osascript` the same way, purely so a test run can never
pop a real notification.
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from keel.config import load_config
from keel.types import Granularity

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config.paper-hourly.yaml"
PAPERFORWARD_CONFIG = REPO_ROOT / "config.paperforward.yaml"
PLIST = REPO_ROOT / "com.keel.paper-hourly.plist"
RUN_SCRIPT = REPO_ROOT / "paper-hourly-run.sh"
WRAPPER = REPO_ROOT / "keel-paperhourly"
RUNBOOK = REPO_ROOT / "docs" / "operator-runbook.md"


# -- the config: a deliberate SUPERSET of paperforward, hourly cadence -------------------------

# The 11 Tier-2 additions that passed the 2026-08-17 15-minute data-health screen in #351
# (coverage >= 95.98%, zero zero-volume bars over 90 days), each capped at 2% target weight.
TIER_TWO = ["ZEC", "NEAR", "AVAX", "UNI", "FET", "ICP", "DOT", "CRV", "ALGO", "BCH", "DOGE"]


def test_config_universe_is_a_deliberate_superset_of_paperforward():
    """Since #351 (2026-08-17) the hourly universe is a strict SUPERSET of paperforward's,
    not a mirror of it. The 11 additions above each passed a 15-minute data-health screen
    (coverage >= 95.98%, zero zero-volume bars over 90 days; results recorded in #351), and
    each is capped at 2% target weight -- the sizing half of the guardrail whose live-path
    half is #350's spread gate. The incumbents keep their rules/params untouched, so their
    evidence stays comparable across the expansion; only their target weights rescale,
    together, 1.00 -> 0.78 total. Paperforward (the daily profile) deliberately stays at 8
    so its evidence remains a like-for-like 8-asset series. If this fails because
    paperforward moved, move with it or say why here."""
    hourly = load_config(str(CONFIG))
    daily = load_config(str(PAPERFORWARD_CONFIG))

    assert set(daily.allowlist) < set(hourly.allowlist)
    assert set(TIER_TWO) == set(hourly.allowlist) - set(daily.allowlist)
    assert len(hourly.allowlist) == 19

    # Incumbents keep their relative shape, rescaled 1.00 -> 0.78; Tier-2 sits at the 2% cap.
    assert hourly.target_weights["BTC"] == Decimal("0.23")
    assert hourly.target_weights["ETH"] == Decimal("0.155")
    assert hourly.target_weights["PAXG"] == Decimal("0.155")
    for incumbent in ("SOL", "XLM", "LTC", "ADA", "LINK"):
        assert hourly.target_weights[incumbent] == Decimal("0.048")
    for addition in TIER_TWO:
        assert hourly.target_weights[addition] == Decimal("0.02")
    assert sum(hourly.target_weights.values()) == Decimal("1")

    assert hourly.caps == daily.caps
    assert hourly.quote_currency == daily.quote_currency
    assert hourly.fees == daily.fees
    assert hourly.paper == daily.paper


def test_config_trades_paper_on_the_hourly_cadence():
    """The three load-bearing differences from paperforward: paper mode, a ONE_HOUR cycle
    (`interval_sec: 3600`), and the same three candle series (the hourly rules trade ONE_HOUR;
    ONE_DAY stays the higher-TF bias input, FIFTEEN_MINUTE the entry-gate confirmation)."""
    config = load_config(str(CONFIG))

    assert config.auto_trade.mode == "paper"
    assert config.auto_trade.interval_sec == 3600
    assert config.market_data.granularities == [
        Granularity.ONE_DAY,
        Granularity.ONE_HOUR,
        Granularity.FIFTEEN_MINUTE,
    ]


def test_config_states_the_net_negative_caveat_in_its_header():
    """The issue's honesty requirement: anyone opening the config reads the caveat BEFORE the
    numbers, not in a runbook they may never open. A config that stopped saying it would look
    like a strategy worth copying."""
    text = CONFIG.read_text()
    assert "NET-NEGATIVE" in text
    assert "ADMISSIBLE EVIDENCE" in text
    assert "not profitability" in text
    assert "keel-paperhourly.db" in text


# -- the plist: one trigger per hour, its own stamp semantics ----------------------------------


def _plist() -> dict:
    return plistlib.loads(PLIST.read_bytes())


def test_plist_is_well_formed_xml():
    """Same requirement as com.keel.live.plist: parse with a STRICT parser. XML forbids a
    double hyphen inside a comment, this repo's prose puts one in every other sentence, and
    Apple's lenient parser accepts it -- so a malformed file would ship silently."""
    _plist()


def test_plist_fires_every_hour_at_twenty():
    """24 triggers, one per local hour, all at :20 -- so every UTC hour gets a trigger under
    any local offset, DST transitions included, with twenty minutes of margin for Coinbase to
    publish and `data.market_feed` to persist the hourly candle that closed at :00 (the same
    margin `com.keel.live.plist` uses for the same reason)."""
    data = _plist()
    triggers = [(entry["Hour"], entry["Minute"]) for entry in data["StartCalendarInterval"]]
    assert sorted(triggers) == [(hour, 20) for hour in range(24)]


def test_plist_still_runs_at_load():
    """A boot between triggers must run the current UTC hour's cycle immediately rather than
    wait up to an hour; the runner's hour-stamp makes a repeated load harmless."""
    assert _plist()["RunAtLoad"] is True


def test_plist_points_at_the_hourly_runner_in_the_deployment_dir():
    data = _plist()
    assert data["Label"] == "com.keel.paper-hourly"
    assert data["ProgramArguments"] == [
        "/bin/bash",
        "/Users/elmehdiaitbrahim/keel/paper-hourly-run.sh",
    ]
    assert data["WorkingDirectory"] == "/Users/elmehdiaitbrahim/keel"


def test_plist_documents_its_own_stamp_semantics():
    """The paperforward day-stamp is daily-grained; this job needs an HOUR-stamp. That fact,
    the net-negative purpose, and the :20 margin must all live in the plist's comment block
    where the next reader of the file will meet them."""
    text = PLIST.read_text()
    assert "hour-stamp" in text or "hour stamp" in text
    assert "NET-NEGATIVE" in text
    assert ":20" in text


# -- the runner: exactly once per UTC hour ------------------------------------------------------


def _install_date_shim(bin_dir: Path) -> None:
    """A `date` on PATH that reads its instant from `$KEEL_TEST_NOW` (epoch seconds) instead
    of the wall clock. Pure Python rather than a shell wrapper around `date -r`, so the
    harness is portable (the live schedule's shim leans on BSD `date -r`). Honours the
    invocation forms `paper-hourly-run.sh` actually uses: `date [-u] '+FORMAT'`.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "date"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "now = int(os.environ.get('KEEL_TEST_NOW', time.time()))\n"
        "args = sys.argv[1:]\n"
        "utc = '-u' in args\n"
        "# the leading '+' is date(1)'s format-string prefix, not part of the format\n"
        "fmt = next(a for a in args if a.startswith('+'))[1:]\n"
        "import datetime\n"
        "t = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)\n"
        "if not utc:\n"
        "    t = t.astimezone()\n"
        "print(t.strftime(fmt))\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


def _sandbox(
    tmp_path: Path,
    keel_exit_code: int,
    *,
    fetch_exit_code: int = 0,
    doctor_exit_code: int = 0,
    doctor_stdout: str = "",
) -> tuple[Path, Path, Path, dict[str, str], Path]:
    """Copy the REAL runner into `tmp_path`, repointed at the sandbox, with a stubbed `keel`.

    Two rewrites, both load-bearing: `DIR="..."` -> `tmp_path`, so the gate, the stamp and the
    invocations all run VERBATIM; and the literal `/usr/bin/osascript` -> a recorder (#642 gave
    this script a `notify()` seam it did not have before, shaped identically to
    `keel-live-run.sh`'s so this same rewrite works unmodified). No `sandbox-exec` is needed
    (this script places nothing real).

    The `$KEEL` stub dispatches on subcommand (`fetch`/`doctor`/`agent`), mirroring
    `tests/test_schedule.py`'s `_sandbox`: `keel_exit_code` keeps its original meaning -- the
    AGENT invocation's exit code -- so every pre-existing call site keeps working unmodified;
    `fetch_exit_code`/`doctor_exit_code`/`doctor_stdout` default to a clean, silent fetch and
    doctor, exactly as if this script still only ever ran `agent`.
    """
    source = RUN_SCRIPT.read_text()
    patched, count = re.subn(
        r'^DIR="[^"]*"$', f'DIR="{tmp_path}"', source, count=1, flags=re.MULTILINE
    )
    assert count == 1, "could not repoint DIR -- refusing to run a script aimed at the deployment"

    calls_log = tmp_path / "osascript-calls.log"
    recorder = tmp_path / "osascript-recorder.sh"
    recorder.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{calls_log}"\nexit 0\n')
    recorder.chmod(recorder.stat().st_mode | stat.S_IEXEC)
    osascript_count = patched.count("/usr/bin/osascript")
    assert osascript_count == 1, "expected exactly one literal reference to /usr/bin/osascript"
    patched = patched.replace("/usr/bin/osascript", str(recorder))

    assert "/Users/elmehdiaitbrahim/keel" not in patched

    script = tmp_path / "paper-hourly-run.sh"
    script.write_text(patched)

    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / ".venv" / "bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    invocations = stub_dir / "keel.invocations"
    doctor_stdout_file = stub_dir / "doctor.stdout"
    doctor_stdout_file.write_text(doctor_stdout)
    stub = stub_dir / "keel"
    stub.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{invocations}"\n'
        'case " $* " in\n'
        f'  *" fetch "*) exit {fetch_exit_code} ;;\n'
        f'  *" doctor "*) cat "{doctor_stdout_file}"; exit {doctor_exit_code} ;;\n'
        f"  *) exit {keel_exit_code} ;;\n"
        "esac\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    date_bin = tmp_path / "shim-bin"
    _install_date_shim(date_bin)

    env = dict(os.environ)
    env["PATH"] = f"{date_bin}:{env.get('PATH', '')}"
    return script, invocations, tmp_path / "logs" / ".paper-hourly-last-run", env, calls_log


def _run(script: Path, env: dict[str, str], now_utc: datetime) -> subprocess.CompletedProcess[str]:
    run_env = dict(env)
    run_env["KEEL_TEST_NOW"] = str(int(now_utc.timestamp()))
    return subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, env=run_env
    )


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text().splitlines())


def test_a_clean_cycle_stamps_the_utc_hour_and_the_same_hour_is_a_no_op(tmp_path):
    """The dedupe, end to end, in the real shell: a successful cycle stamps THIS_HOUR
    (`YYYY-MM-DDThh`, UTC); any later trigger in the same UTC hour (a repeated load,
    fall-back's repeated local hour) does nothing."""
    script, invocations, stamp, env, _calls = _sandbox(tmp_path, keel_exit_code=0)
    at_1420 = datetime(2026, 6, 15, 14, 20, tzinfo=UTC)

    first = _run(script, env, at_1420)
    assert first.returncode == 0
    # fetch, pre-cycle doctor, agent, post-cycle doctor (#640/#642's cycle shape).
    assert _count_lines(invocations) == 4
    assert stamp.read_text().strip() == "2026-06-15T14"

    second = _run(script, env, at_1420)
    assert second.returncode == 0
    assert "already ran" in second.stdout
    assert _count_lines(invocations) == 4, "an already-ran hour must not invoke keel at all"


def test_the_next_utc_hour_runs_its_own_cycle(tmp_path):
    """The regression a copy of paperforward-run.sh would ship: that script stamps the DATE,
    so 23 of this job's 24 daily triggers would be no-ops and the profile would silently
    collect daily evidence. The stamp must be hour-grained."""
    script, invocations, stamp, env, _calls = _sandbox(tmp_path, keel_exit_code=0)

    assert _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC)).returncode == 0
    assert _run(script, env, datetime(2026, 6, 15, 15, 20, tzinfo=UTC)).returncode == 0
    assert _run(script, env, datetime(2026, 6, 15, 16, 50, tzinfo=UTC)).returncode == 0

    assert _count_lines(invocations) == 12, "3 cycles x (fetch, doctor, agent, doctor)"
    assert stamp.read_text().strip() == "2026-06-15T16"


def test_the_cycle_invokes_fetch_then_doctor_then_agent_then_doctor_against_its_own_database(
    tmp_path,
):
    """Config and database must travel as a pair: `--db` defaults to keel.db (the DAILY paper
    account), so a runner that dropped the flag would drive hourly rows against the wrong
    ledger -- the exact footgun the `keel-live`/`keel-paperhourly` wrappers exist to remove.
    Also pins the #640/#642 cycle SHAPE and its ORDER: fetch, doctor, agent, doctor."""
    script, invocations, _, env, _calls = _sandbox(tmp_path, keel_exit_code=0)

    _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC))

    lines = invocations.read_text().splitlines()
    assert [line.split()[-1] for line in lines] == ["fetch", "doctor", "agent", "doctor"]
    for line in lines:
        assert line.startswith("--config config.paper-hourly.yaml --db keel-paperhourly.db ")


def test_a_failed_cycle_writes_no_stamp_so_the_same_hour_retries(tmp_path):
    """Same failure direction as paperforward/live: a cycle that died (no network on wake,
    venue late publishing the bar) must not be recorded as done. A later trigger in the SAME
    hour retries; the stamp only appears once a cycle succeeds."""
    script, invocations, stamp, env, _calls = _sandbox(tmp_path, keel_exit_code=4)

    failed = _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC))
    assert failed.returncode == 4, "the script must surface the cycle's exit code, not mask it"
    assert not stamp.exists(), "a failed cycle must leave the hour unstamped so it is retried"
    # the post-cycle doctor still ran even though the cycle itself failed.
    assert _count_lines(invocations) == 4

    retried = _run(script, env, datetime(2026, 6, 15, 14, 40, tzinfo=UTC))
    assert retried.returncode == 4
    assert "already ran" not in retried.stdout
    assert _count_lines(invocations) == 8


def test_a_failed_cycle_is_retried_and_then_stamped_by_a_later_trigger(tmp_path):
    """The two-step of the failure path, in one sandbox: fail at 14:20 (no stamp), succeed at
    14:40 (stamps the same UTC hour), no-op at 14:59."""
    script, invocations, stamp, env, _calls = _sandbox(tmp_path, keel_exit_code=4)
    at_1420 = datetime(2026, 6, 15, 14, 20, tzinfo=UTC)
    at_1440 = datetime(2026, 6, 15, 14, 40, tzinfo=UTC)

    assert _run(script, env, at_1420).returncode == 4
    assert not stamp.exists()

    # Flip the stub to success in place, then re-fire inside the same hour. This simple
    # replacement stub does not dispatch by subcommand -- it does not need to, since every one
    # of the cycle's four invocations (fetch, doctor, agent, doctor) only needs to succeed here.
    stub = tmp_path / ".venv" / "bin" / "keel"
    stub.write_text(f'#!/bin/bash\nprintf "cycle\\n" >> "{invocations}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    ok = _run(script, env, at_1440)
    assert ok.returncode == 0
    assert stamp.read_text().strip() == "2026-06-15T14"
    assert _count_lines(invocations) == 8

    later = _run(script, env, datetime(2026, 6, 15, 14, 59, tzinfo=UTC))
    assert later.returncode == 0
    assert "already ran" in later.stdout
    assert _count_lines(invocations) == 8


# -- #640/#642: fetch/doctor never gate, and this script now has a notify() seam ----------------


def test_runner_script_has_a_notify_seam_shaped_like_keel_live_runs(tmp_path):
    """#642: every paper wrapper gets the SAME single-seam `notify()` (identical shape, same
    `2>/dev/null || true` guard) as `keel-live-run.sh`'s, precisely so the same test-harness
    rewrite (`_sandbox`'s `/usr/bin/osascript` -> recorder swap) works on it unmodified."""
    text = RUN_SCRIPT.read_text()
    assert 'OSASCRIPT="/usr/bin/osascript"' in text
    assert "notify() {" in text
    assert '2>/dev/null || true' in text


def test_a_failing_fetch_notifies_and_the_cycle_still_runs(tmp_path):
    """#642: a fetch failure is reported, not swallowed, and never gates -- the cycle still
    runs on whatever cache it already has."""
    script, invocations, stamp, env, calls = _sandbox(tmp_path, keel_exit_code=0, fetch_exit_code=5)
    result = _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC))

    assert result.returncode == 0, "a fetch failure must never abort the script"
    assert stamp.read_text().strip() == "2026-06-15T14", "the cycle must still run and stamp"
    assert _count_lines(invocations) == 4
    assert "fetch failed" in calls.read_text()


def test_a_doctor_fail_notifies_and_the_cycle_still_runs_with_no_gate(tmp_path):
    """#642's per-product-gating instruction is refused here too, for the identical reason it
    is refused in `keel-live-run.sh` (see that script's block comment, and
    `tests/test_schedule.py::test_a_doctor_fail_notifies_and_the_cycle_still_runs_with_no_gate_of_its_own`):
    `keel/agent.py`'s whole-cycle admission bit already withholds every entry, on every
    product, book-wide, the instant any rule is blocked. A doctor FAIL ahead of this cycle is
    a REPORT, and must not stop the cycle from running or stamping."""
    script, invocations, stamp, env, calls = _sandbox(
        tmp_path,
        keel_exit_code=0,
        doctor_exit_code=1,
        doctor_stdout=(
            "[FAIL] data.stale: 3 of 19 series are stale\n"
            "       BTC-USD ONE_HOUR 5 bars behind\n"
        ),
    )
    result = _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC))

    assert result.returncode == 0, "a doctor FAIL must never abort the script"
    assert stamp.read_text().strip() == "2026-06-15T14", "the cycle must still run and stamp"
    assert _count_lines(invocations) == 4
    calls_text = calls.read_text()
    assert "doctor reported FAIL" in calls_text
    assert "data.stale" in calls_text
    assert "report only" in calls_text


# -- the wrapper and the runbook ----------------------------------------------------------------


def test_wrapper_pins_the_config_and_database_together():
    """`keel-paperhourly` exists for the same reason as `keel-live`: `--db` defaults to
    keel.db, and the hourly rows must never touch the daily paper ledger."""
    text = WRAPPER.read_text()
    assert "--config config.paper-hourly.yaml" in text
    assert "--db keel-paperhourly.db" in text


def test_runbook_documents_the_profile_its_database_and_the_caveat():
    """The operator-facing contract: where paper-vs-live is documented, the third profile
    must appear with its bootstrap and the net-negative caveat -- the issue's acceptance
    demands the caveat be impossible to miss."""
    text = RUNBOOK.read_text()
    assert "The hourly evidence profile" in text
    assert "config.paper-hourly.yaml" in text
    assert "keel-paperhourly.db" in text
    assert "NET-NEGATIVE" in text
    assert "keel migrate --db keel-paperhourly.db" in text
    assert '"granularity": "ONE_HOUR"' in text


def test_runbook_documents_launchctl_install_and_verification():
    """#640 exists because the install step was never written down: `com.keel.paper-hourly.plist`
    was tracked in-repo and sat in `~/keel` for weeks, unloaded, and nothing noticed for ten
    days. This pin is worth having on its own -- it is the one paragraph that would have
    prevented the incident."""
    text = RUNBOOK.read_text()
    assert "launchctl bootstrap gui/$(id -u)" in text
    assert "launchctl list | grep com.keel" in text
    assert "launchctl bootout gui/$(id -u)" in text
    assert "schedules nothing" in text


def test_runbook_documents_profile_scheduled_and_profile_cycled_findings():
    """The new `keel doctor` findings that make a stalled or unscheduled profile visible are
    the structural fix for #640; the runbook must name them, not just the incident."""
    text = RUNBOOK.read_text()
    assert "profile.scheduled" in text
    assert "profile.cycled" in text


def test_runbook_documents_the_cycle_shape_and_why_doctor_does_not_gate():
    """#642's refused per-product-gating instruction, in the operator-facing doc: per product
    in the report, book-wide in the gate."""
    text = RUNBOOK.read_text()
    assert "fetch" in text
    normalized = " ".join(text.split())
    assert "per product in the report, book-wide in the gate" in normalized
