"""The EQUITIES paper profile's tracked assets + the venue-selection wiring it needs (#370 B2).

Two things are pinned here, and they ship together because one cannot run without the other:

1. The profile's deployment assets -- `config.paper-equities.yaml` + `com.keel.paper-equities
   .plist` + `paper-equities-run.sh` + `keel-equities` -- tracked in-repo exactly like the
   paperforward/live/paper-hourly ones. Nothing about them is executed by the suite's code
   paths, so like `tests/test_paper_hourly_profile.py` this file pins them against drift.
2. The MINIMAL engine wiring the profile needs: config-driven venue selection. Today
   `_build_broker` constructs a `CoinbaseClient` unconditionally; this profile is the first
   that must reach a different adapter (`keel-broker-alpaca`, paper host, IEX feed). The
   `broker:` config section is that surface, and its ABSENCE must leave the Coinbase
   construction path byte-identical -- pinned here by construction, not by assertion of
   intent, because every existing profile and test depends on that default.

The runner tests execute the REAL script verbatim through a harness that shims `date` (so
they do not depend on, or wait on, the wall clock) and stubs the deployment's `.venv/bin/keel`.
The shim renders "local" time in America/New_York ON PURPOSE: the script's window guard is
ET-anchored (the US regular session is 09:30 to 16:00 ET and the deployment host lives in
that zone), and fixing the shim's zone keeps these tests deterministic on any host rather
than only on an Eastern one.
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
from zoneinfo import ZoneInfo

import pytest

from keel.config import ConfigError, load_config
from keel.types import Granularity

from .conftest import VALID_CONFIG_YAML

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config.paper-equities.yaml"
PLIST = REPO_ROOT / "com.keel.paper-equities.plist"
RUN_SCRIPT = REPO_ROOT / "paper-equities-run.sh"
WRAPPER = REPO_ROOT / "keel-equities"
RUNBOOK = REPO_ROOT / "docs" / "operator-runbook.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

ET = ZoneInfo("America/New_York")

# The five paper candidates (see the config's header for the disclaimer that governs them):
# liquid US large caps chosen so a screen COULD be run on them, not ones that have.
CANDIDATES = ["MSFT", "AAPL", "GOOGL", "NVDA", "COST"]


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """A wall-clock instant in the deployment's zone, for the date shim to render."""
    return datetime(year, month, day, hour, minute, tzinfo=ET)


# -- the config: alpaca paper, ONE_DAY only, daily cadence --------------------------------------


def test_config_selects_the_alpaca_paper_venue():
    """The load-bearing difference from every sibling profile: `broker:` selects Alpaca's
    PAPER host with the IEX feed. `endpoint: paper` is the whole point -- there is no URL
    knob anywhere that could point a paper credential at the live venue (FR-11)."""
    config = load_config(str(CONFIG))

    assert config.broker.name == "alpaca"
    assert config.broker.endpoint == "paper"
    assert config.broker.data_feed == "iex"
    assert config.auto_trade.mode == "paper"


def test_config_is_five_flat_paper_candidates_summing_to_one():
    """Five liquid US large caps at a FLAT 20% each. Flatness states no view -- it is the
    sizing half of the same guardrail logic as the hourly profile's flat Tier-2 caps, applied
    to an asset class nothing has been measured on. If this fails because the set moved, move
    the CANDIDATES list with it or say why here."""
    config = load_config(str(CONFIG))

    assert config.allowlist == CANDIDATES
    assert set(config.target_weights) == set(CANDIDATES)
    assert all(w == Decimal("0.2") for w in config.target_weights.values())
    assert sum(config.target_weights.values()) == Decimal("1")
    assert config.risk_pct == Decimal("0.01")


def test_config_trades_the_daily_clock_on_one_day_bars_only():
    """A daily-clock equities profile: ONE_DAY is the ONLY granularity (hourly bars exist
    only within sessions and daily turtle rules do not read them) and the cadence is 86400s,
    which also scales the staleness window that B1's session awareness reads closed-explained
    through."""
    config = load_config(str(CONFIG))

    assert config.market_data.granularities == [Granularity.ONE_DAY]
    assert config.market_data.history_days == 365
    assert config.auto_trade.interval_sec == 86400


def test_config_states_the_candidate_disclaimer_and_the_no_edge_caveat():
    """Two honesty requirements, both in the header so they are read BEFORE the numbers:
    the allowlist is PAPER CANDIDATES whose classification is operator-attested per
    (alpaca, SYMBOL) -- the engine never classifies and the file asserts nothing religiously
    -- and the paper-hourly-style caveat that there is no proven edge on ANY asset class, so
    the profile exists for evidence, not profit."""
    text = CONFIG.read_text()
    assert "PAPER CANDIDATES" in text
    assert "OPERATOR-ATTESTED" in text
    assert "(alpaca, SYMBOL)" in text
    assert "asserts nothing religiously" in text
    assert "NO PROVEN EDGE" in text
    assert "ADMISSIBLE EVIDENCE" in text
    assert "not profit" in text
    assert "keel-equities.db" in text


def test_config_explains_why_one_day_only():
    """The granularity choice must carry its reason where the next editor meets it: hourly
    bars exist only within sessions and daily rules do not need them."""
    text = CONFIG.read_text()
    assert "ONE_DAY ONLY" in text
    assert "within sessions" in text


# -- the plist: one cycle per day, inside the US regular session --------------------------------


def _plist() -> dict:
    return plistlib.loads(PLIST.read_bytes())


def test_plist_is_well_formed_xml():
    """Same requirement as every sibling plist: parse with a STRICT parser. XML forbids a
    double hyphen inside a comment, this repo's prose puts one in every other sentence, and
    Apple's lenient parser accepts it -- so a malformed file would ship silently."""
    _plist()


def test_plist_fires_hourly_through_the_regular_session_window():
    """Six triggers, at 10:00-15:00 local (ET) on the hour -- every one INSIDE the US regular
    session (09:30 to 16:00 ET). Deliberately NOT shortly after the 16:00 close: B1's session
    gate skips the whole cycle whenever the venue clock says closed, so an after-close
    trigger would log market_closed and never evaluate a bar. The just-closed daily bar is
    evaluated at the NEXT session's open -- the standard daily-system semantics (signal on
    close, execute next open) -- and the 10:00 anchor gives the 09:30 open thirty minutes to
    settle. The extra triggers are catch-up breadth, not extra cycles: the runner is
    day-stamped."""
    data = _plist()
    triggers = [(entry["Hour"], entry["Minute"]) for entry in data["StartCalendarInterval"]]
    assert triggers == [(hour, 0) for hour in range(10, 16)]


def test_plist_still_runs_at_load():
    """A boot inside the window must run the day's cycle immediately rather than wait for
    the next trigger; the runner's day-stamp makes a repeated load harmless. A boot OUTSIDE
    the window is refused by the runner's own guard (an early-morning closed-market skip
    exits 0 and must not be allowed to stamp the day as done)."""
    assert _plist()["RunAtLoad"] is True


def test_plist_points_at_the_equities_runner_in_the_deployment_dir():
    data = _plist()
    assert data["Label"] == "com.keel.paper-equities"
    assert data["ProgramArguments"] == [
        "/bin/bash",
        "/Users/elmehdiaitbrahim/keel/paper-equities-run.sh",
    ]
    assert data["WorkingDirectory"] == "/Users/elmehdiaitbrahim/keel"


def test_plist_documents_its_schedule_reasoning():
    """The ET reasoning, the after-open-not-after-close decision, and the DST caveat must
    live in the plist's comment block where the next reader of the file will meet them."""
    text = PLIST.read_text()
    assert "09:30" in text
    assert "ET" in text
    assert "DST" in text
    assert "America/New_York" in text
    # The decision that contradicts the obvious copy-paste from a close-anchored schedule:
    assert "session gate" in text or "market_closed" in text


def test_plist_dst_caveat_is_honest_about_non_et_hosts():
    """The caveat must not overstate safety: on a host far enough ahead of ET every trigger
    can fire pre-open, PASS the runner's local-hours guard, and stamp a closed-market skip
    as the day's work -- permanently zero evidence. The plist must say the schedule is only
    correct on an ET-anchored host, that other hosts re-anchor the trigger hours to land
    10:00-15:00 ET, and that the local-hours guard is a backstop, not a drift absorber."""
    text = PLIST.read_text()
    assert "ET-anchored" in text
    assert "re-anchor" in text
    assert "backstop" in text


# -- the runner: exactly once per UTC day, only inside the session window ------------------------


def _install_date_shim(bin_dir: Path) -> None:
    """A `date` on PATH that reads its instant from `$KEEL_TEST_NOW` (epoch seconds) instead
    of the wall clock, rendering LOCAL time in America/New_York (the deployment host's zone
    -- see the module docstring for why the shim's zone is fixed rather than the host's).
    Pure Python rather than a shell wrapper around `date -r`, so the harness is portable.
    Honours the invocation forms `paper-equities-run.sh` actually uses: `date [-u] '+FORMAT'`.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "date"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "from datetime import datetime as dt\n"
        "from zoneinfo import ZoneInfo\n"
        "now = int(os.environ.get('KEEL_TEST_NOW', time.time()))\n"
        "args = sys.argv[1:]\n"
        "utc = '-u' in args\n"
        "# the leading '+' is date(1)'s format-string prefix, not part of the format\n"
        "fmt = next(a for a in args if a.startswith('+'))[1:]\n"
        "t = dt.fromtimestamp(now, tz=__import__('datetime').timezone.utc)\n"
        "if not utc:\n"
        "    t = t.astimezone(ZoneInfo('America/New_York'))\n"
        "print(t.strftime(fmt))\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


def _sandbox(tmp_path: Path, keel_exit_code: int) -> tuple[Path, Path, Path, dict[str, str]]:
    """Copy the REAL runner into `tmp_path`, repointed at the sandbox, with a stubbed `keel`.

    Only ONE rewrite, load-bearing for safety: `DIR="..."` -> `tmp_path`, so the window
    guard, the stamp and the invocation all run VERBATIM. No notification redirection and no
    sandbox-exec are needed (this script places nothing real and notifies nobody -- but the
    DIR rewrite is still asserted so a test can never run the deployment's own copy).
    """
    source = RUN_SCRIPT.read_text()
    patched, count = re.subn(
        r'^DIR="[^"]*"$', f'DIR="{tmp_path}"', source, count=1, flags=re.MULTILINE
    )
    assert count == 1, "could not repoint DIR -- refusing to run a script aimed at the deployment"
    assert "/Users/elmehdiaitbrahim/keel" not in patched

    script = tmp_path / "paper-equities-run.sh"
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
    return script, invocations, tmp_path / "logs" / ".paper-equities-last-run", env


def _run(script: Path, env: dict[str, str], now: datetime) -> subprocess.CompletedProcess[str]:
    run_env = dict(env)
    run_env["KEEL_TEST_NOW"] = str(int(now.astimezone(UTC).timestamp()))
    return subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, env=run_env
    )


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text().splitlines())


def test_a_clean_cycle_stamps_the_utc_day_and_the_same_day_is_a_no_op(tmp_path):
    """The dedupe, end to end, in the real shell: a successful cycle at 10:30 ET stamps
    THIS UTC day; a later trigger the same UTC day does nothing."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=0)

    first = _run(script, env, _et(2026, 6, 15, 10, 30))
    assert first.returncode == 0
    assert _count_lines(invocations) == 1
    assert stamp.read_text().strip() == "2026-06-15"

    second = _run(script, env, _et(2026, 6, 15, 12, 45))
    assert second.returncode == 0
    assert "already ran" in second.stdout
    assert _count_lines(invocations) == 1


def test_the_next_utc_day_runs_its_own_cycle(tmp_path):
    """The stamp must be DAY-grained in a way that rolls over: the next day's first
    in-window trigger is a new cycle, not a no-op against yesterday's stamp."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=0)

    assert _run(script, env, _et(2026, 6, 15, 10, 30)).returncode == 0
    assert _run(script, env, _et(2026, 6, 16, 10, 30)).returncode == 0

    assert _count_lines(invocations) == 2
    assert stamp.read_text().strip() == "2026-06-16"


def test_the_cycle_runs_the_equities_config_against_its_own_database(tmp_path):
    """Config and database must travel as a pair: `--db` defaults to keel.db (the daily
    CRYPTO paper account), so a runner that dropped the flag would drive equity rows against
    the wrong ledger -- the exact footgun the `keel-equities` wrapper exists to remove."""
    script, invocations, _, env = _sandbox(tmp_path, keel_exit_code=0)

    _run(script, env, _et(2026, 6, 15, 10, 30))

    assert invocations.read_text().strip() == (
        "--config config.paper-equities.yaml --db keel-equities.db agent"
    )


def test_a_failed_cycle_writes_no_stamp_so_the_same_day_retries(tmp_path):
    """Same failure direction as every sibling runner: a cycle that died must not be
    recorded as done. A later trigger the SAME UTC day retries; the stamp only appears once
    a cycle succeeds."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=4)

    failed = _run(script, env, _et(2026, 6, 15, 10, 30))
    assert failed.returncode == 4, "the script must surface the cycle's exit code, not mask it"
    assert not stamp.exists(), "a failed cycle must leave the day unstamped so it is retried"

    retried = _run(script, env, _et(2026, 6, 15, 11, 30))
    assert retried.returncode == 4
    assert "already ran" not in retried.stdout
    assert _count_lines(invocations) == 2


def test_a_failed_cycle_is_retried_and_then_stamped_by_a_later_trigger(tmp_path):
    """The two-step of the failure path, in one sandbox: fail at 10:30 (no stamp), succeed
    at 11:30 (stamps the same UTC day), no-op at 12:10."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=4)

    assert _run(script, env, _et(2026, 6, 15, 10, 30)).returncode == 4
    assert not stamp.exists()

    # Flip the stub to success in place, then re-fire inside the same day.
    stub = tmp_path / ".venv" / "bin" / "keel"
    stub.write_text(f'#!/bin/bash\nprintf "cycle\\n" >> "{invocations}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    ok = _run(script, env, _et(2026, 6, 15, 11, 30))
    assert ok.returncode == 0
    assert stamp.read_text().strip() == "2026-06-15"
    assert _count_lines(invocations) == 2

    later = _run(script, env, _et(2026, 6, 15, 12, 10))
    assert later.returncode == 0
    assert "already ran" in later.stdout
    assert _count_lines(invocations) == 2


def test_a_boot_before_the_window_neither_runs_nor_stamps(tmp_path):
    """THE equities-specific regression: B1's session gate makes a pre-open cycle SKIP with
    market_closed and exit 0 -- a runner without a window guard would stamp that skip as the
    day's work and suppress the real evaluation at 10:00. A boot before the window must
    leave the day unstamped and run nothing."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=0)

    early = _run(script, env, _et(2026, 6, 15, 8, 10))
    assert early.returncode == 0
    assert _count_lines(invocations) == 0
    assert not stamp.exists()

    # The day is still available to its scheduled run.
    assert _run(script, env, _et(2026, 6, 15, 10, 30)).returncode == 0
    assert _count_lines(invocations) == 1
    assert stamp.read_text().strip() == "2026-06-15"


def test_a_boot_after_the_close_neither_runs_nor_stamps(tmp_path):
    """The post-session mirror of the pre-open guard: after 16:00 ET the venue clock says
    closed, so a cycle would skip and stamp-fail the day. Outside the window, exit quietly."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=0)

    late = _run(script, env, _et(2026, 6, 15, 16, 30))
    assert late.returncode == 0
    assert _count_lines(invocations) == 0
    assert not stamp.exists()


def test_the_runner_script_states_its_stamp_ordering_in_comments():
    """The acceptance demand is readable in the file itself, not only in tests: `set -e`,
    the stamp written only AFTER a successful cycle, and the window guard's reason."""
    text = RUN_SCRIPT.read_text()
    assert "set -euo pipefail" in text
    assert "only AFTER a successful cycle" in text
    # The stamp write must come after the keel invocation in the file: a reorder would
    # stamp failures as done.
    invocation_at = text.index("./.venv/bin/keel")
    stamp_write_at = text.rindex('> "$STAMP"')
    assert invocation_at < stamp_write_at


# -- the runner: the two skip kinds are stamped differently (#386 review) ------------------------
#
# B1's session gate skips a cycle two ways, and only one of them is the day's work:
# market_closed (weekend, holiday) exits 0 and stamping it is CORRECT cadence bookkeeping --
# nothing more can happen that day; market_clock_unavailable (a transient clock outage) is a
# degraded read that must NOT stamp, or the day is silently lost while the log says "ran".


def _clock_exit() -> int:
    """The contract the runner depends on: the agent's distinct nonzero exit for a
    market_clock_unavailable skip (`agent.MARKET_CLOCK_UNAVAILABLE_EXIT`)."""
    from keel.agent import MARKET_CLOCK_UNAVAILABLE_EXIT

    return MARKET_CLOCK_UNAVAILABLE_EXIT


def test_a_clock_unavailable_cycle_writes_no_stamp_and_is_retried(tmp_path):
    """A transient clock outage at the 10:00 trigger must not be recorded as the day's work:
    the cycle exits MARKET_CLOCK_UNAVAILABLE_EXIT (nonzero), `set -e` stops the script short
    of the stamp, and the next trigger retries -- the same recovery shape as any failed
    cycle."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=_clock_exit())

    failed = _run(script, env, _et(2026, 6, 15, 10, 30))
    assert failed.returncode == _clock_exit(), "the script must surface the cycle's exit code"
    assert not stamp.exists(), "a clock-unavailable skip must leave the day unstamped"

    # Flip the stub to a healthy cycle, then re-fire inside the same day: the retry stamps.
    stub = tmp_path / ".venv" / "bin" / "keel"
    stub.write_text(f'#!/bin/bash\nprintf "cycle\\n" >> "{invocations}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    ok = _run(script, env, _et(2026, 6, 15, 11, 30))
    assert ok.returncode == 0
    assert stamp.read_text().strip() == "2026-06-15"
    assert _count_lines(invocations) == 2


def test_a_market_closed_skip_still_stamps_the_utc_day(tmp_path):
    """The other skip kind keeps its historical treatment: a closed venue (here, a Saturday)
    skips with market_closed and exits 0, and stamping THAT is correct -- nothing more can
    happen that day, so the day is recorded as done rather than retried forever."""
    script, invocations, stamp, env = _sandbox(tmp_path, keel_exit_code=0)

    # 2026-06-20 is a Saturday: the (stubbed) cycle would have skipped market_closed.
    ok = _run(script, env, _et(2026, 6, 20, 11, 0))

    assert ok.returncode == 0
    assert stamp.read_text().strip() == "2026-06-20"


# -- the wrapper --------------------------------------------------------------------------------


def test_wrapper_pins_the_config_and_database_together():
    """`keel-equities` exists for the same reason as `keel-live`/`keel-paperhourly`: `--db`
    defaults to keel.db, and the equity rows must never touch a crypto ledger."""
    text = WRAPPER.read_text()
    assert "--config config.paper-equities.yaml" in text
    assert "--db keel-equities.db" in text


# -- the runbook --------------------------------------------------------------------------------


def test_runbook_documents_the_profile_its_database_and_the_caveat():
    """The operator-facing contract: the fourth profile appears with its bootstrap, its
    venue, its own database, and the no-edge caveat -- impossible to miss, per the issue."""
    text = RUNBOOK.read_text()
    assert "The equities paper profile" in text
    assert "config.paper-equities.yaml" in text
    assert "keel-equities.db" in text
    assert "keel migrate --db keel-equities.db" in text
    assert '"granularity": "ONE_DAY"' in text
    assert "NO PROVEN EDGE" in text


def test_runbook_documents_attestation_rail17_t_plus_1_and_the_opt_outs():
    """The equities-specific compliance semantics the issue demands: operator-supplied
    attestation per (alpaca, SYMBOL) from AAOIFI/IFSB-class sources (the engine never
    classifies), rail 17's ACATS transfer-out reading, the T+1 x daily-cadence interaction,
    and the two operator-verified opt-outs (stock lending OFF for qabd, high-yield sweep OFF
    for riba) with where to verify each."""
    text = RUNBOOK.read_text()
    assert "(alpaca, SYMBOL)" in text
    assert "AAOIFI" in text
    assert "IFSB" in text
    assert "never classifies" in text or "engine never infers" in text
    assert "ACATS" in text
    assert "T+1" in text
    assert "Stock lending" in text
    assert "high-yield" in text.lower()
    assert "qabd" in text
    assert "riba" in text
    assert "Alpaca dashboard" in text


def test_runbook_notes_what_is_deliberately_not_here():
    """The scope fence: `keel/assets` screening venue semantics stay hardcoded to coinbase
    (deliberate, #233 live-path work), deployment itself is out of scope (it needs the
    operator's Alpaca paper credentials), and the trademark posture stays in the README
    rather than duplicated here."""
    text = RUNBOOK.read_text()
    assert "#233" in text
    assert "out of scope" in text or "deliberately NOT here" in text
    assert "README" in text


def test_runbook_states_the_cash_no_margin_pdt_posture():
    """PRD 5/6.4's account posture, operator-facing half: cash accounts ONLY (margin
    borrowing is riba; a cash account also sidesteps the PDT rule's $25k margin-account
    threshold), the PDT rule explained (what it is; why a cash account on keel's daily
    cadence is not that pattern), the T+1 interplay CROSS-REFERENCED rather than duplicated,
    and the fence that enforcement-in-code is #372's scope."""
    text = RUNBOOK.read_text()
    assert "cash account" in text.lower()
    assert "margin" in text.lower()
    assert "pattern day trader" in text.lower() or "PDT" in text
    assert "25,000" in text or "$25k" in text
    # The scope fence: config refusing a margin posture is #372's work, not undocumented.
    assert "#372" in text
    # And the posture cross-references the T+1 section instead of restating it.
    assert "T+1 settlement" in text


def test_runbook_fences_dividend_purification_as_phase_b3():
    """Purification must not read as forgotten: the recorded-event + operator-policy walk
    (corporate actions recorded per FR-10, purification math against the attestation's
    ratio, disposition recorded) is fenced as the B3 slice of this phase."""
    text = RUNBOOK.read_text()
    assert "purification" in text.lower()
    assert "corporate actions" in text.lower()
    assert "B3" in text
    assert "FR-10" in text


def test_runbook_profile_table_gains_the_fourth_column():
    """The paper-vs-live comparison table must carry the equities column, or an operator
    cross-checking profiles reads a three-profile world."""
    text = RUNBOOK.read_text()
    assert "paper-equities" in text
    assert "com.keel.paper-equities" in text
    assert "config.paper-equities.yaml" in text


def test_env_example_carries_the_alpaca_paper_key_names():
    """`.env.example` is where a new operator looks first; the new venue's key names and
    the paper-keys-suffice note must be there."""
    text = ENV_EXAMPLE.read_text()
    assert "ALPACA_API_KEY_ID=" in text
    assert "ALPACA_API_SECRET_KEY=" in text
    assert "paper" in text


# -- venue selection: the engine wiring the profile needs ---------------------------------------


def _write_config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(VALID_CONFIG_YAML + extra)
    return path


ALPACA_BROKER_YAML = """
broker:
  name: alpaca
  endpoint: paper
  data_feed: iex
"""


def test_absent_broker_section_defaults_to_coinbase(tmp_path):
    """The compatibility pin: with no `broker:` section the parsed config selects coinbase,
    which is the name `_build_broker`'s legacy branch answers to. Every shipped profile and
    every existing test loads a config without the section."""
    config = load_config(str(_write_config(tmp_path)))

    assert config.broker.name == "coinbase"
    assert config.broker.endpoint == "paper"
    assert config.broker.data_feed == "iex"


def test_broker_section_round_trips_alpaca_paper_iex(tmp_path):
    config = load_config(str(_write_config(tmp_path, ALPACA_BROKER_YAML)))

    assert config.broker.name == "alpaca"
    assert config.broker.endpoint == "paper"
    assert config.broker.data_feed == "iex"


def test_broker_endpoint_is_validated_at_load(tmp_path):
    """FR-11's load-time posture: an endpoint outside paper/live is a typo that must fail
    at config load, not at first request -- and live/paper is the whole vocabulary because
    the trading host is derived from it, never configured as a URL."""
    with pytest.raises(ConfigError, match="broker.endpoint"):
        load_config(
            str(_write_config(tmp_path, "\nbroker:\n  name: alpaca\n  endpoint: prod\n"))
        )


def test_broker_data_feed_is_validated_at_load(tmp_path):
    """The data tier is a DECLARED capability (FR-5): iex or sip, nothing else, refused at
    load rather than silently falling back to the venue's server-side default."""
    with pytest.raises(ConfigError, match="broker.data_feed"):
        load_config(
            str(_write_config(tmp_path, "\nbroker:\n  name: alpaca\n  data_feed: cows\n"))
        )


def test_coinbase_rejects_the_alpaca_only_knobs(tmp_path):
    """`endpoint`/`data_feed` are alpaca wiring keys; on coinbase they would be silently
    ignored -- the exact silent-dead-knob failure this config module refuses elsewhere."""
    with pytest.raises(ConfigError, match="broker.name"):
        load_config(str(_write_config(tmp_path, "\nbroker:\n  endpoint: live\n")))


def test_build_broker_default_is_byte_compatible_coinbase(
    tmp_path, monkeypatch
):
    """THE default pin, by construction: no `broker:` section -> `_build_broker` takes the
    unchanged Coinbase path -- `load_secrets()` from `.env`, a `RESTClient` built from those
    CDP values, wrapped in `CoinbaseClient`. The kwargs and the wrapping are asserted, so a
    refactor that changed any of it for the default config fails here."""
    import coinbase.rest

    from keel.commands._common import _build_broker
    from keel.data import cb_client

    (tmp_path / ".env").write_text("CDP_API_KEY=cb-key\nCDP_API_SECRET=cb-secret\n")
    monkeypatch.chdir(tmp_path)

    calls: dict[str, object] = {}

    class _FakeRESTClient:
        def __init__(self, **kwargs: object) -> None:
            calls["rest_kwargs"] = kwargs

    def _fake_coinbase_client(transport: object) -> object:
        calls["transport"] = transport
        return object()

    monkeypatch.setattr(coinbase.rest, "RESTClient", _FakeRESTClient)
    monkeypatch.setattr(cb_client, "CoinbaseClient", _fake_coinbase_client)

    config = load_config(str(_write_config(tmp_path)))
    broker = _build_broker(config)

    assert broker is not None
    assert calls["rest_kwargs"] == {
        "api_key": "cb-key",
        "api_secret": "cb-secret",
        "timeout": None,
    }
    assert isinstance(calls["transport"], _FakeRESTClient)


def test_build_broker_selects_alpaca_paper_iex(tmp_path, monkeypatch):
    """The new path: `broker: {name: alpaca, endpoint: paper, data_feed: iex}` resolves
    through the `keel.brokers` entry points and constructs the adapter against the PAPER
    trading host with the IEX feed. No network happens at construction -- the assertion is
    on the built object's own declared properties."""
    from keel_broker_alpaca import AlpacaAdapter
    from keel_broker_alpaca.transport import PAPER_TRADING_HOST

    from keel.commands._common import _build_broker

    (tmp_path / ".env").write_text(
        "ALPACA_API_KEY_ID=paper-key-id\nALPACA_API_SECRET_KEY=paper-secret\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

    config = load_config(str(_write_config(tmp_path, ALPACA_BROKER_YAML)))
    broker = _build_broker(config)

    assert isinstance(broker, AlpacaAdapter)
    assert broker.endpoint == "paper"
    assert broker._transport.trading_host == PAPER_TRADING_HOST
    assert broker._transport.data_feed == "iex"


def test_build_broker_alpaca_missing_secrets_names_the_venue_and_env_vars(tmp_path, monkeypatch):
    """The missing-keys error must name the venue and BOTH env var names, so the operator's
    next action is a copy-paste rather than a grep. The real secrets loader also honours the
    environment (not only `.env`), so both sources are cleared here."""
    from keel.commands._common import _build_broker

    monkeypatch.chdir(tmp_path)  # no .env in here
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

    config = load_config(str(_write_config(tmp_path, ALPACA_BROKER_YAML)))

    with pytest.raises(RuntimeError) as excinfo:
        _build_broker(config)

    message = str(excinfo.value)
    assert "alpaca" in message
    assert "ALPACA_API_KEY_ID" in message
    assert "ALPACA_API_SECRET_KEY" in message


def test_build_broker_refuses_a_venue_without_cli_wiring(tmp_path, monkeypatch):
    """A name that RESOLVES to an adapter but has no credential wiring in the CLI (fake,
    robinhood -- installed in dev) is refused with the two names that do have wiring, rather
    than constructing an adapter that can never reach its venue."""
    from keel.commands._common import _build_broker

    monkeypatch.chdir(tmp_path)

    config = load_config(str(_write_config(tmp_path, "\nbroker:\n  name: fake\n")))

    with pytest.raises(RuntimeError, match="coinbase.*alpaca"):
        _build_broker(config)


def test_build_broker_unknown_name_surfaces_the_entry_point_list(tmp_path, monkeypatch):
    """A name with no entry point at all fails through the registry's own error, which
    lists what IS installed -- discovery stays the authority on what exists."""
    from keel.commands._common import _build_broker

    monkeypatch.chdir(tmp_path)

    config = load_config(str(_write_config(tmp_path, "\nbroker:\n  name: nonsuch\n")))

    with pytest.raises(LookupError, match="no broker adapter registered"):
        _build_broker(config)


def test_load_alpaca_secrets_reads_environment_then_env_file(tmp_path, monkeypatch):
    """`load_secrets`' shape contract, followed for the new venue: both keys present ->
    a populated dict; absent everywhere -> `{}`; the ENVIRONMENT wins over the file so a
    deployment can inject credentials without one."""
    from keel.config import load_alpaca_secrets

    (tmp_path / ".env").write_text(
        "ALPACA_API_KEY_ID=file-key-id\nALPACA_API_SECRET_KEY=file-secret\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

    assert load_alpaca_secrets() == {"key_id": "file-key-id", "secret_key": "file-secret"}

    monkeypatch.setenv("ALPACA_API_KEY_ID", "env-key-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "env-secret")
    assert load_alpaca_secrets() == {"key_id": "env-key-id", "secret_key": "env-secret"}

    # Absent everywhere: no env vars, no .env in the cwd.
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert load_alpaca_secrets() == {}
