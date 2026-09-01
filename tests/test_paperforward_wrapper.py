"""`paperforward-run.sh`'s cycle shape and notification seam (#640/#642).

Unlike `keel-live-run.sh` and the other two paper profiles, no test file previously executed
this script -- it is the oldest of the four wrappers and had drifted furthest from having its
own coverage. This file is deliberately LIGHT: `tests/test_schedule.py` already carries the
full argued case for the `fetch -> doctor -> cycle -> doctor` shape and for why doctor is a
report and never a gate (see that file's docstrings, and `keel-live-run.sh`'s own block
comments, which this script's comments mirror verbatim in spirit). What is pinned here is
just: the shape actually ships on THIS script too, in the right order; a fetch/doctor failure
still lets the cycle run; and the failure-leaves-no-stamp contract this script always had is
unchanged.

The harness shims `date` (LOCAL time -- this script is local-anchored, unlike the UTC-anchored
live/hourly/equities runners) and stubs `.venv/bin/keel`, dispatching on subcommand exactly
like `tests/test_schedule.py`'s and the sibling profile tests' harnesses do.
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_SCRIPT = REPO_ROOT / "paperforward-run.sh"
PLIST = REPO_ROOT / "com.keel.paperforward.plist"


def _install_date_shim(bin_dir: Path) -> None:
    """A `date` on PATH that reads its instant from `$KEEL_TEST_NOW` (epoch seconds), rendering
    LOCAL time -- this script's gate is local-anchored (`SCHED_HOUR=9`, plain `date`, no `-u`).
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "date"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "from datetime import datetime as dt\n"
        "now = int(os.environ.get('KEEL_TEST_NOW', time.time()))\n"
        "args = sys.argv[1:]\n"
        "# the leading '+' is date(1)'s format-string prefix, not part of the format\n"
        "fmt = next(a for a in args if a.startswith('+'))[1:]\n"
        "t = dt.fromtimestamp(now)\n"
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

    Two rewrites, both load-bearing: `DIR="..."` -> `tmp_path`, and the literal
    `/usr/bin/osascript` -> a recorder, exactly like the sibling profile harnesses. No
    `sandbox-exec` is needed (this script places nothing real).
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

    script = tmp_path / "paperforward-run.sh"
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
    return script, invocations, tmp_path / "logs" / ".paperforward-last-run", env, calls_log


def _run(script: Path, env: dict[str, str], now: datetime) -> subprocess.CompletedProcess[str]:
    run_env = dict(env)
    run_env["KEEL_TEST_NOW"] = str(int(now.timestamp()))
    return subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, env=run_env)


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text().splitlines())


# -- the plist: the pin the other three profiles had and this one did not -------------------


def test_plist_is_well_formed_xml() -> None:
    """Same requirement as every sibling plist (`tests/test_schedule.py`,
    `tests/test_paper_hourly_profile.py`, `tests/test_paper_equities_profile.py`): parse with a
    STRICT XML parser, not merely with Apple's lenient one.

    `com.keel.paperforward.plist` was the only one of the four tracked plists WITHOUT this pin,
    and it was the only one that was actually broken: its "hour retries it" comment carried a
    literal `--` inside an XML comment, which XML forbids. `plutil -lint` reports OK (Apple's
    CFPropertyList parser tolerates it), which is how it shipped and ran under launchd for as
    long as it did, but Python's `plistlib` (expat) rejects it outright:
    `xml.parsers.expat.ExpatError: not well-formed (invalid token): line 17, column 27`. Because
    `collect_profiles` parses every plist with `plistlib`, the malformed file made the daily
    paper profile silently unparseable -- it never even reached the broad
    `except Exception: continue`'s "one bad profile" accounting, because there was no test
    watching this file at all. Three plists tested, one untested, and the untested one was the
    one that was wrong.
    """
    plistlib.loads(PLIST.read_bytes())


def test_runner_script_has_a_notify_seam_shaped_like_keel_live_runs() -> None:
    """#642: same single-seam `notify()`, same shape, same `2>/dev/null || true` guard as
    `keel-live-run.sh`'s -- so the same test-harness rewrite works here unmodified."""
    text = RUN_SCRIPT.read_text()
    assert 'OSASCRIPT="/usr/bin/osascript"' in text
    assert "notify() {" in text
    assert "2>/dev/null || true" in text


def test_a_clean_cycle_invokes_fetch_then_doctor_then_agent_then_doctor_in_order(
    tmp_path: Path,
) -> None:
    script, invocations, stamp, env, _calls = _sandbox(tmp_path, keel_exit_code=0)
    now = datetime(2026, 6, 15, 9, 30)

    result = _run(script, env, now)
    assert result.returncode == 0

    lines = invocations.read_text().splitlines()
    assert [line.split()[-1] for line in lines] == ["fetch", "doctor", "agent", "doctor"]
    assert stamp.read_text().strip() == "2026-06-15"


def test_a_failing_fetch_notifies_and_the_cycle_still_runs(tmp_path: Path) -> None:
    script, invocations, stamp, env, calls = _sandbox(tmp_path, keel_exit_code=0, fetch_exit_code=3)
    result = _run(script, env, datetime(2026, 6, 15, 9, 30))

    assert result.returncode == 0, "a fetch failure must never abort the script"
    assert stamp.read_text().strip() == "2026-06-15", "the cycle must still run and stamp"
    assert _count_lines(invocations) == 4
    assert "fetch failed" in calls.read_text()


def test_a_doctor_fail_notifies_and_the_cycle_still_runs_with_no_gate(tmp_path: Path) -> None:
    """#642's per-product-gating instruction is refused here too, for the identical reason it
    is refused in `keel-live-run.sh`: `keel/agent.py`'s whole-cycle admission bit already
    withholds every entry, on every product, book-wide, the instant any rule is blocked. A
    doctor FAIL ahead of this cycle is a REPORT, and must not stop the cycle from running or
    stamping."""
    script, invocations, stamp, env, calls = _sandbox(
        tmp_path,
        keel_exit_code=0,
        doctor_exit_code=1,
        doctor_stdout=(
            "[FAIL] attest.withdrawals: withdrawal capability never attested\n"
            "       rail 17 halts every BUY entry until it is\n"
        ),
    )
    result = _run(script, env, datetime(2026, 6, 15, 9, 30))

    assert result.returncode == 0, "a doctor FAIL must never abort the script"
    assert stamp.read_text().strip() == "2026-06-15", "the cycle must still run and stamp"
    assert _count_lines(invocations) == 4
    calls_text = calls.read_text()
    assert "doctor reported FAIL" in calls_text
    assert "attest.withdrawals" in calls_text
    assert "report only" in calls_text


def test_a_failed_cycle_writes_no_stamp_and_the_post_cycle_doctor_still_runs(
    tmp_path: Path,
) -> None:
    """The pre-existing contract, unchanged: a failed cycle must leave the day unstamped so
    the next trigger retries. New: the post-cycle doctor still runs regardless."""
    script, invocations, stamp, env, _calls = _sandbox(tmp_path, keel_exit_code=7)
    result = _run(script, env, datetime(2026, 6, 15, 9, 30))

    assert result.returncode == 7, "the script must surface the cycle's exit code, not mask it"
    assert not stamp.exists(), "a failed cycle must leave the day unstamped so it is retried"
    subcommands = [line.split()[-1] for line in invocations.read_text().splitlines()]
    assert subcommands == ["fetch", "doctor", "agent", "doctor"], (
        "the post-cycle doctor must run even though the cycle itself failed"
    )
