"""Tests for `keel.commands.pulse` -- the service behind the console's Pulse home view.

The fixtures are the real shapes `tests/commands/test_activity.py` copies from a deployment's
log, run through `feed_from_lines` and `apply_scope(..., "today")` exactly as `api.py` does, and
each test that depends on a fixture REACHING the service asserts a non-empty precondition first:
an empty feed or an empty rule table would pass most of these by vacuum.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest
from keel_core.config import load_config

from keel.commands import pulse as pulse_mod
from keel.commands.activity import ActivityFeed, apply_scope, feed_from_lines
from keel.commands.pulse import (
    RESUME_COMMAND,
    NextDcaBuy,
    PulseReport,
    TodayCounts,
    build_pulse,
    gather_pulse,
)
from keel.commands.status import OpenPositionStatus, WithdrawalAttestationStatus
from keel.commands.trading import KILL_ENGAGED_LINE
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from tests.commands.test_activity import _line, _quiet_cycle_lines, _rail_veto_cycle_lines
from tests.commands.test_dca_plan import _insert_dca
from tests.web.test_payload import _status_report

_DAY = 86_400
# Day 100 at noon UTC: a Wednesday-ish mid-day, one day short of nothing in particular, and five
# days short of the next cadence-7 boundary (day 105).
NOW = 100 * _DAY + 12 * 3600
TODAY_START = 100 * _DAY


def _today_feed(*line_groups: list[str]) -> ActivityFeed:
    lines = [line for group in line_groups for line in group]
    return apply_scope(feed_from_lines(lines), "today", now_ts=NOW)


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _pulse(feed: ActivityFeed | None = None, **status_overrides: object) -> PulseReport:
    feed = feed if feed is not None else _today_feed()
    return build_pulse(_status_report(**status_overrides), feed, (), now_ts=NOW)


# -- today's counts -----------------------------------------------------------------------------


def test_today_counts_sum_the_scoped_cycles_and_nothing_outside_them() -> None:
    feed = _today_feed(
        _rail_veto_cycle_lines("aaaaaaaaaaaaaaaa", TODAY_START + 3600),
        _quiet_cycle_lines("bbbbbbbbbbbbbbbb", TODAY_START + 7200),
        # yesterday's veto: parsed, grouped, and then scoped OUT -- it must not be counted
        _rail_veto_cycle_lines("cccccccccccccccc", TODAY_START - 3600),
    )
    assert len(feed.cycles) == 2 and feed.cycles_out_of_scope == 1  # the fixture reached scope
    report = build_pulse(_status_report(), feed, (), now_ts=NOW)
    assert report.today == TodayCounts(
        cycles=2, signals=1, blocked=1, entered=0, exited=0, errors=0
    )


@pytest.mark.parametrize("status", ["missing", "unreadable", "unparseable", "oversized"])
def test_a_log_that_could_not_be_read_gives_no_counts_not_zeros(status: str) -> None:
    """**Review F3.** `None` is "not recorded", `0` is "recorded as zero"; summing an unread log
    into zeros is the always-passing fee rail's shape (#198)."""
    feed = apply_scope(ActivityFeed(status=status, source="/x/keel.log"), "today", now_ts=NOW)
    report = build_pulse(_status_report(), feed, (), now_ts=NOW)

    assert report.today == TodayCounts(
        cycles=None, signals=None, blocked=None, entered=None, exited=None, errors=None
    )
    assert report.today_basis == "unavailable"
    assert report.why_not_none == "Today's log could not be read, so reasons may be missing."


def test_a_window_that_stops_after_midnight_is_partial_not_the_days_total() -> None:
    lines = _rail_veto_cycle_lines("aaaaaaaaaaaaaaaa", TODAY_START + 3600)
    feed = apply_scope(feed_from_lines(lines, truncated=True), "today", now_ts=NOW)
    assert feed.status == "ok" and feed.cycles  # the fixture reached the service
    assert feed.scope_fully_covered is False  # and the window did not prove it reached midnight

    report = build_pulse(_status_report(), feed, (), now_ts=NOW)

    assert report.today_basis == "partial"
    assert report.today.cycles == 1  # still the counts it saw, labelled as a lower bound
    assert report.why_not_none == (
        "No reason in the part of today's log that was read, and the log may not reach "
        "back to midnight, so reasons may be missing."
    )


def test_a_fully_covered_day_is_complete_and_an_empty_file_is_a_real_zero() -> None:
    complete = build_pulse(
        _status_report(), _today_feed(_quiet_cycle_lines("b" * 16, NOW)), (), now_ts=NOW
    )
    assert complete.today_basis == "complete"
    assert complete.why_not_none == "Nothing stopped keel today."

    blank = apply_scope(ActivityFeed(status="empty", source="/x/keel.log"), "today", now_ts=NOW)
    report = build_pulse(_status_report(), blank, (), now_ts=NOW)
    assert report.today_basis == "complete"
    assert report.today.cycles == 0


def test_last_cycle_is_the_newest_today_or_the_one_before_the_scope_or_none() -> None:
    today = _today_feed(
        _quiet_cycle_lines("aaaaaaaaaaaaaaaa", TODAY_START + 3600),
        _quiet_cycle_lines("bbbbbbbbbbbbbbbb", TODAY_START + 7200),
    )
    assert _pulse(today).last_cycle_ts == TODAY_START + 7200

    yesterday_only = _today_feed(_quiet_cycle_lines("cccccccccccccccc", TODAY_START - 7200))
    assert yesterday_only.cycles == ()
    assert yesterday_only.last_cycle_before_scope is not None
    assert _pulse(yesterday_only).last_cycle_ts == TODAY_START - 7200

    assert _pulse(_today_feed()).last_cycle_ts is None


# -- why keel did not trade ---------------------------------------------------------------------


def test_why_not_lists_the_standing_blockers_before_the_cycle_reasons_in_order() -> None:
    feed = _today_feed(_rail_veto_cycle_lines("aaaaaaaaaaaaaaaa", TODAY_START + 3600))
    report = _pulse(feed, kill_switch_engaged=True, rail11_status="HALTED")
    assert report.why_not == (
        KILL_ENGAGED_LINE,
        "rail 11: HALTED",
        "rail veto: per_asset_concentration_cap",
        "rail veto: monthly_subscription_allowance",
        "not placed: paper: vetoed by rails",
    )


def test_why_not_dedupes_a_reason_two_cycles_repeat() -> None:
    feed = _today_feed(
        _rail_veto_cycle_lines("aaaaaaaaaaaaaaaa", TODAY_START + 3600),
        _rail_veto_cycle_lines("bbbbbbbbbbbbbbbb", TODAY_START + 7200),
    )
    assert len(feed.cycles) == 2
    assert _pulse(feed).why_not == (
        "rail veto: per_asset_concentration_cap",
        "rail veto: monthly_subscription_allowance",
        "not placed: paper: vetoed by rails",
    )


def test_why_not_takes_the_newest_cycle_first_and_keeps_only_the_named_prefixes() -> None:
    older = [
        _line("agent.cycle_start", TODAY_START + 100, "aaaaaaaaaaaaaaaa"),
        _line("agent.cycle_skipped", TODAY_START + 101, "aaaaaaaaaaaaaaaa", reason="market closed"),
        _line("agent.feed_stale", TODAY_START + 102, "aaaaaaaaaaaaaaaa", product="BTC-USD"),
    ]
    newer = [
        _line("agent.cycle_start", TODAY_START + 200, "bbbbbbbbbbbbbbbb"),
        _line("agent.entries_withheld", TODAY_START + 201, "bbbbbbbbbbbbbbbb", blocked_count=2),
        _line("equity.external_flow_recorded", TODAY_START + 202, "bbbbbbbbbbbbbbbb", amount="5"),
    ]
    report = _pulse(_today_feed(older, newer))
    assert report.why_not == (
        "entries withheld (2 blocked)",  # newest cycle first
        "cycle skipped: market closed",
        "stale feed: BTC-USD",
    )  # "external flow 5" is a highlight but not a reason


def test_why_not_omits_rail_17_in_paper_and_names_it_in_live() -> None:
    lapsed = WithdrawalAttestationStatus("expired", True, NOW - 9 * _DAY, None, 2 * _DAY)
    assert _pulse(mode="paper", withdrawal_attestation=lapsed).why_not == ()
    assert _pulse(mode="live", withdrawal_attestation=lapsed).why_not == (
        "rail 17: withdrawal attestation expired",
    )
    unattested = WithdrawalAttestationStatus("unattested", None, None, None, None)
    assert _pulse(mode="live", withdrawal_attestation=unattested).why_not == (
        "rail 17: withdrawal attestation unattested",
    )
    attested = WithdrawalAttestationStatus("attested", True, NOW - 3600, 6 * _DAY, None)
    assert _pulse(mode="live", withdrawal_attestation=attested).why_not == ()


def test_why_not_names_rail_17_wherever_status_says_entries_are_halted() -> None:
    """`status._rail17_line` evaluates rail 17 in every non-paper mode and says "entries halted"
    for suspended, expired and never-attested alike; the why-not list must not be narrower."""
    lapsed = WithdrawalAttestationStatus("expired", True, NOW - 9 * _DAY, None, 2 * _DAY)
    assert _pulse(mode="confirm", withdrawal_attestation=lapsed).why_not == (
        "rail 17: withdrawal attestation expired",
    )
    suspended = WithdrawalAttestationStatus("suspended", False, NOW - 3600, None, None)
    assert _pulse(mode="live", withdrawal_attestation=suspended).why_not == (
        "rail 17: withdrawal attestation suspended",
    )
    assert _pulse(mode="paper", withdrawal_attestation=suspended).why_not == ()


# -- next DCA buy -------------------------------------------------------------------------------


def test_next_dca_lists_live_rules_only_with_their_next_cadence_day(
    valid_config_path: Path,
) -> None:
    repo = _repo()
    repo.set_state("kill_switch", False)
    live_id = _insert_dca(repo, "BTC-USD", "live", budget="40")
    _insert_dca(repo, "ETH-USD", "candidate")
    _insert_dca(repo, "PAXG-USD", "disabled")
    config = load_config(str(valid_config_path))
    report = gather_pulse(repo, config, _today_feed(), now_ts=NOW)
    assert report.next_dca == (
        NextDcaBuy(
            rule_id=live_id,
            product_id="BTC-USD",
            cadence_days=7,
            budget_usd=Decimal("40"),
            next_cadence_day_ts=105 * _DAY,
            budget_inferred=False,
        ),
    )


def test_next_dca_is_today_when_today_is_a_cadence_day() -> None:
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "live")
    from keel.commands.dca_plan import existing_dca_rules

    rules = existing_dca_rules(repo)
    assert len(rules) == 1  # the row reached the reader
    report = build_pulse(_status_report(), _today_feed(), rules, now_ts=105 * _DAY + 5)
    assert report.next_dca[0].next_cadence_day_ts == 105 * _DAY


# -- unbracketed roll-call ----------------------------------------------------------------------


def _tranche(id_: int, *, has_bracket: bool) -> OpenPositionStatus:
    return OpenPositionStatus(
        id=id_,
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        qty=Decimal("0.01"),
        entry_price=Decimal("50000"),
        opened_at=NOW - 3600,
        has_bracket=has_bracket,
    )


def test_unbracketed_is_the_live_tranches_without_a_bracket_and_empty_in_paper() -> None:
    tranches = [_tranche(1, has_bracket=True), _tranche(2, has_bracket=False)]
    live = _pulse(mode="live", open_positions=tranches)
    assert [p.id for p in live.unbracketed] == [2]
    assert live.unbracketed_count == 1
    paper = _pulse(mode="paper", open_positions=tranches)
    assert paper.unbracketed == () and paper.unbracketed_count == 0
    # sanity: the same tranches really are unbracketed, so the paper empty is the mode's doing
    assert paper.status.open_positions[1].has_bracket is False


# -- resume command -----------------------------------------------------------------------------


def test_resume_command_is_the_cli_s_own_and_only_when_engaged() -> None:
    from keel.cli import cli

    assert "resume" in cli.commands  # `keel resume` is a real command, not a string we invented
    assert RESUME_COMMAND == "keel resume"
    assert _pulse(kill_switch_engaged=True).resume_command == RESUME_COMMAND
    assert _pulse(kill_switch_engaged=False).resume_command == ""


# -- the module's own rules ---------------------------------------------------------------------


def test_the_service_reads_no_clock() -> None:
    tree = ast.parse(Path(pulse_mod.__file__).read_text(encoding="utf-8"))
    calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert calls  # the scan saw real calls
    assert not {"time.time", "datetime.datetime.now", "datetime.now"} & set(calls)


def test_the_service_imports_no_click() -> None:
    tree = ast.parse(Path(pulse_mod.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in (n.names if isinstance(n, ast.Import) else [None])
    }
    assert "keel" in imported and "click" not in imported


@pytest.mark.parametrize("status", ["missing", "unreadable", "unparseable", "oversized"])
def test_no_cycles_line_is_empty_when_the_log_could_not_be_read(status: str) -> None:
    """**Review R2-F1.** The status card already explains an unread log; "keel has not run yet
    today." over it would be an engine claim the log never made."""
    feed = apply_scope(ActivityFeed(status=status, source="/x/keel.log"), "today", now_ts=NOW)
    assert build_pulse(_status_report(), feed, (), now_ts=NOW).no_cycles_line == ""


def test_no_cycles_line_is_worded_per_basis_and_empty_when_a_cycle_ran_today() -> None:
    blank = apply_scope(ActivityFeed(status="empty", source="/x/keel.log"), "today", now_ts=NOW)
    assert build_pulse(_status_report(), blank, (), now_ts=NOW).no_cycles_line == (
        "keel has not run yet today."
    )

    # a bounded read that held no cycle at all: it cannot say the day was quiet
    cut = ActivityFeed(status="ok", source="/x/keel.log", window_truncated=True)
    partial = apply_scope(cut, "today", now_ts=NOW)
    assert partial.status == "ok" and not partial.cycles  # the fixture reached the service
    assert partial.scope_fully_covered is False
    assert build_pulse(_status_report(), partial, (), now_ts=NOW).no_cycles_line == (
        "No cycle in the part of today's log that was read."
    )

    ran = _today_feed(_quiet_cycle_lines("b" * 16, TODAY_START + 3600))
    assert ran.cycles
    assert build_pulse(_status_report(), ran, (), now_ts=NOW).no_cycles_line == ""
