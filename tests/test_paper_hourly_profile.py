"""The HOURLY paper profile's tracked deployment assets (issue #337).

`config.paper-hourly.yaml` + `com.keel.paper-hourly.plist` + `paper-hourly-run.sh` +
`keel-paperhourly` are the third deployment profile, tracked in-repo exactly like the
paperforward and live ones (since 2026-08-03; see docs/RELEASING.md). Nothing about them is
executed by the test suite's code paths, so like `tests/test_schedule.py` for the live
schedule, this file pins them so they cannot drift silently: the config must stay the SAME
universe at the hourly cadence, the plist must keep 24 hourly triggers and `RunAtLoad`, and
the runner must keep its once-per-UTC-hour stamp (the paperforward day-stamp would collapse
23 of the 24 cycles into no-ops -- the exact regression a copy-paste of that script would
ship).

The runner tests execute the REAL script verbatim through a harness that shims `date` (so
they do not depend on, or wait on, the wall clock) and stubs the deployment's
`.venv/bin/keel`. No sandboxing is needed here, unlike `test_schedule.py`: this script places
nothing real, notifies nobody, and is repointed away from `~/keel` before it runs.
"""

from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
from datetime import UTC, datetime
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


# -- the config: same universe as paperforward, hourly cadence --------------------------------


def test_config_is_the_same_universe_as_paperforward():
    """Everything that defines WHAT is traded matches the daily paper profile; only the
    cadence differs. If this fails because paperforward moved, move with it or say why here:
    the hourly corpus measurement (n≈268, net-negative) was taken on this universe, and a
    silently different one would make the two profiles' evidence incomparable."""
    hourly = load_config(str(CONFIG))
    daily = load_config(str(PAPERFORWARD_CONFIG))

    assert sorted(hourly.allowlist) == sorted(daily.allowlist)
    assert hourly.target_weights == daily.target_weights
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


def _sandbox(tmp_path: Path, keel_exit_code: int) -> tuple[Path, Path, Path, dict[str, str]]:
    """Copy the REAL runner into `tmp_path`, repointed at the sandbox, with a stubbed `keel`.

    Only ONE rewrite, load-bearing for safety: `DIR="..."` -> `tmp_path`, so the gate, the
    stamp and the invocation all run VERBATIM. No notification redirection is needed (this
    script never notifies); no sandbox-exec either (it places nothing real -- but the DIR
    rewrite is still asserted so a test can never run the deployment's own copy).
    """
    source = RUN_SCRIPT.read_text()
    patched, count = re.subn(
        r'^DIR="[^"]*"$', f'DIR="{tmp_path}"', source, count=1, flags=re.MULTILINE
    )
    assert count == 1, "could not repoint DIR -- refusing to run a script aimed at the deployment"
    assert "/Users/elmehdiaitbrahim/keel" not in patched

    script = tmp_path / "paper-hourly-run.sh"
    script.write_text(patched)

    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / ".venv" / "bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    invocations = stub_dir / "keel.invocations"
    stub = stub_dir / "keel"
    stub.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{invocations}"\n'
        f"exit {keel_exit_code}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    date_bin = tmp_path / "shim-bin"
    _install_date_shim(date_bin)

    env = dict(os.environ)
    env["PATH"] = f"{date_bin}:{env.get('PATH', '')}"
    return script, invocations, tmp_path / "logs" / ".paper-hourly-last-run", env


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
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=0)
    at_1420 = datetime(2026, 6, 15, 14, 20, tzinfo=UTC)

    first = _run(script, env, at_1420)
    assert first.returncode == 0
    assert _count_lines(invocations) == 1
    assert stamp.read_text().strip() == "2026-06-15T14"

    second = _run(script, env, at_1420)
    assert second.returncode == 0
    assert "already ran" in second.stdout
    assert _count_lines(invocations) == 1


def test_the_next_utc_hour_runs_its_own_cycle(tmp_path):
    """The regression a copy of paperforward-run.sh would ship: that script stamps the DATE,
    so 23 of this job's 24 daily triggers would be no-ops and the profile would silently
    collect daily evidence. The stamp must be hour-grained."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=0)

    assert _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC)).returncode == 0
    assert _run(script, env, datetime(2026, 6, 15, 15, 20, tzinfo=UTC)).returncode == 0
    assert _run(script, env, datetime(2026, 6, 15, 16, 50, tzinfo=UTC)).returncode == 0

    assert _count_lines(invocations) == 3
    assert stamp.read_text().strip() == "2026-06-15T16"


def test_the_cycle_runs_the_hourly_config_against_its_own_database(tmp_path):
    """Config and database must travel as a pair: `--db` defaults to keel.db (the DAILY paper
    account), so a runner that dropped the flag would drive hourly rows against the wrong
    ledger -- the exact footgun the `keel-live`/`keel-paperhourly` wrappers exist to remove."""
    script, invocations, _, env = _sandbox(tmp_path, keel_exit_code=0)

    _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC))

    assert invocations.read_text().strip() == (
        "--config config.paper-hourly.yaml --db keel-paperhourly.db agent"
    )


def test_a_failed_cycle_writes_no_stamp_so_the_same_hour_retries(tmp_path):
    """Same failure direction as paperforward/live: a cycle that died (no network on wake,
    venue late publishing the bar) must not be recorded as done. A later trigger in the SAME
    hour retries; the stamp only appears once a cycle succeeds."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=4)

    failed = _run(script, env, datetime(2026, 6, 15, 14, 20, tzinfo=UTC))
    assert failed.returncode == 4, "the script must surface the cycle's exit code, not mask it"
    assert not stamp.exists(), "a failed cycle must leave the hour unstamped so it is retried"

    retried = _run(script, env, datetime(2026, 6, 15, 14, 40, tzinfo=UTC))
    assert retried.returncode == 4
    assert "already ran" not in retried.stdout
    assert _count_lines(invocations) == 2


def test_a_failed_cycle_is_retried_and_then_stamped_by_a_later_trigger(tmp_path):
    """The two-step of the failure path, in one sandbox: fail at 14:20 (no stamp), succeed at
    14:40 (stamps the same UTC hour), no-op at 14:59."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=4)
    at_1420 = datetime(2026, 6, 15, 14, 20, tzinfo=UTC)
    at_1440 = datetime(2026, 6, 15, 14, 40, tzinfo=UTC)

    assert _run(script, env, at_1420).returncode == 4
    assert not stamp.exists()

    # Flip the stub to success in place, then re-fire inside the same hour.
    stub = tmp_path / ".venv" / "bin" / "keel"
    stub.write_text(f'#!/bin/bash\nprintf "cycle\\n" >> "{invocations}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    ok = _run(script, env, at_1440)
    assert ok.returncode == 0
    assert stamp.read_text().strip() == "2026-06-15T14"
    assert _count_lines(invocations) == 2

    later = _run(script, env, datetime(2026, 6, 15, 14, 59, tzinfo=UTC))
    assert later.returncode == 0
    assert "already ran" in later.stdout
    assert _count_lines(invocations) == 2


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
