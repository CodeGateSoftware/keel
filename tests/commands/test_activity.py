"""Tests for `keel.commands.activity` -- the pure substrate of `keel tui`'s `v` activity feed.

The whole module exists so that the grouping and summarising can be tested exhaustively while
the curses rendering stays a thin, separately-smoke-tested layer (`tests/commands/test_tui.py`).
So this file is deliberately heavy on two things:

* **Realistic fixtures.** Every event shape below is copied from a real deployment's
  `logs/keel.log` -- the PAXG rail veto of 2026-08-08 (an `engine.setup_detected` followed by two
  `guards.check_failed` and an `agent.enter_evaluated` with `placed=false`), the
  `choppy_regime` `engine.setup_rejected`, and the uncorrelated
  `cb_client.accounts_fetch_failed`/`executor.quote_fetch_failed` pairs that carry no `cycle_id`
  at all. A test built from an invented shape proves nothing about a parser whose entire job is
  to read someone else's file.
* **Degradation.** The log is written by another process, possibly by another version of this
  codebase, and may be rotated mid-read. A missing file, an empty one, a partial JSON line from a
  crash mid-write, a line that is valid JSON but not an object, an event with no `cycle_id`, an
  event with fields this code has never seen -- each has a test, because the requirement is not
  "usually works" but "never a traceback and never a blank overlay".
"""

from __future__ import annotations

import datetime
import json
import time
from typing import Any

import pytest

from keel.commands.activity import (
    _MAX_BYTES,
    _MAX_EVENTS_PER_CYCLE,
    _UNCORRELATED_GAP_SEC,
    ACTIVITY_HEADER,
    ACTIVITY_SCOPES,
    DEFAULT_ACTIVITY_SCOPE,
    ActivityCycle,
    ActivityEvent,
    ActivityFeed,
    _short_num,
    _stamp,
    apply_scope,
    build_activity_feed,
    cycle_style,
    describe_empty_scope,
    describe_status,
    event_style,
    feed_from_lines,
    footer_notes,
    group_cycles,
    next_activity_scope,
    normalise_scope,
    parse_events,
    read_log_window,
    render_cycle_row,
    render_event_detail,
    render_event_row,
    resolve_log_path,
    scope_headline,
    scope_start_ts,
    summarise_cycle,
)

# A weekday morning cycle, the cadence the real deployment actually runs at.
T0 = 1786194006.0  # 2026-08-08 09:00:06 UTC


# -- fixture builders (real shapes, from a real deployment's log) ---------------------------------


def _line(event: str, ts: float, cycle_id: str | None = "cyc0000000000001", **fields: Any) -> str:
    """One JSONL record in exactly `keel_core.telemetry.JsonFormatter`'s shape: the envelope keys
    it always writes, plus whatever the call site passed. `cycle_id=None` omits the key entirely,
    which is what an event emitted outside an engine cycle (or by a build predating the
    correlation id) actually looks like -- NOT a `null`."""
    payload: dict[str, Any] = {
        "ts": ts,
        "level": fields.pop("level", "INFO"),
        "logger": fields.pop("logger", "keel.agent"),
        "event": event,
        "venue": "coinbase",
    }
    if cycle_id is not None:
        payload["cycle_id"] = cycle_id
    payload.update(fields)
    return json.dumps(payload)


def _quiet_cycle_lines(cycle_id: str, start: float) -> list[str]:
    """The overwhelmingly commonest cycle in the real log: polled everything, evaluated every
    rule, found nothing. Five products, one rule each, zero signals."""
    products = ["ADA-USD", "BTC-USD", "ETH-USD", "PAXG-USD", "XLM-USD"]
    lines = [
        _line("agent.cycle_start", start, cycle_id, now_ts=int(start)),
        _line(
            "agent.feed_polled",
            start + 4,
            cycle_id,
            candles_polled=605,
            rule_count=5,
            products=products,
        ),
        _line("agent.mode_resolved", start + 4, cycle_id, mode="paper"),
        _line(
            "agent.paper_equity",
            start + 4,
            cycle_id,
            equity="11000",
            dd_total="0",
            dd_weekly="0",
        ),
    ]
    for product in products:
        lines.append(
            _line(
                "engine.no_signal",
                start + 5,
                cycle_id,
                rule="turtle_breakout",
                product=product,
                gate="donchian_high",
                close=0.19929,
                entry_level=0.21146,
                gap_pct=6.106678709418443,
            )
        )
        lines.append(
            _line(
                "agent.signals_evaluated",
                start + 5,
                cycle_id,
                product=product,
                rule_count=1,
                signal_count=0,
            )
        )
    return lines


def _rail_veto_cycle_lines(cycle_id: str = "b9871abe9898404e", start: float = T0) -> list[str]:
    """The 2026-08-08 PAXG cycle, verbatim in shape: a real setup, two guard violations on the
    single signal, and an entry that was not placed."""
    return [
        _line("agent.cycle_start", start, cycle_id, now_ts=int(start)),
        _line("agent.mode_resolved", start + 5, cycle_id, mode="paper"),
        _line(
            "engine.setup_detected",
            start + 5,
            cycle_id,
            rule="turtle_breakout",
            product="PAXG-USD",
            cts_score=5,
            technique="signal_candle",
            entry="4342.52",
            stop="4197.09381782563408",
            target="5215.07709304619552",
        ),
        _line(
            "agent.signals_evaluated",
            start + 5,
            cycle_id,
            product="PAXG-USD",
            rule_count=1,
            signal_count=1,
        ),
        _line(
            "guards.check_failed",
            start + 5,
            cycle_id,
            product="PAXG-USD",
            side="BUY",
            violation=(
                "per_asset_concentration_cap: PAXG exposure 3284.671252850915628790264696 "
                "exceeds 0.5 of max_exposure_usd (2500.0)"
            ),
        ),
        _line(
            "guards.check_failed",
            start + 5,
            cycle_id,
            product="PAXG-USD",
            side="BUY",
            violation=(
                "monthly_subscription_allowance: month-to-date BUY spend 0 + "
                "3284.671252850915628790264696 = 3284.671252850915628790264696 exceeds the "
                "allowance cap 500 -- remaining allowance 500"
            ),
        ),
        _line(
            "agent.enter_evaluated",
            start + 5,
            cycle_id,
            product="PAXG-USD",
            rule="turtle_breakout",
            technique="signal_candle",
            cts_score=5,
            placed=False,
            reason="paper: vetoed by rails",
        ),
    ]


def _fetch_failure_pair(start: float) -> list[str]:
    """The uncorrelated pair the real log holds 64 of -- no `cycle_id`, ERROR level, a full
    traceback in `exc`."""
    traceback = (
        'Traceback (most recent call last):\n  File "/Users/x/keel/keel/data/cb_client.py", '
        "line 120, in get_accounts\n    raise\nrequests.exceptions.HTTPError: 401 Unauthorized"
    )
    return [
        _line(
            "cb_client.accounts_fetch_failed",
            start,
            None,
            level="ERROR",
            logger="keel.data.cb_client",
            exc=traceback,
        ),
        _line(
            "executor.quote_fetch_failed",
            start,
            None,
            level="ERROR",
            logger="keel.execution.executor",
            quote_currency="USD",
            exc=traceback,
        ),
    ]


def _cycle_by_id(feed: ActivityFeed, cycle_id: str) -> ActivityCycle:
    return next(c for c in feed.cycles if c.cycle_id == cycle_id)


# -- parse_events: the happy path -----------------------------------------------------------------


def test_parse_events_reads_envelope_and_keeps_the_rest_as_fields() -> None:
    (event,), skipped = parse_events(
        [_line("engine.setup_rejected", T0, "abc123", rule="turtle_breakout", gate="choppy_regime")]
    )

    assert skipped == 0
    assert event.ts == T0
    assert event.level == "INFO"
    assert event.event == "engine.setup_rejected"
    assert event.cycle_id == "abc123"
    # `venue` is envelope and is excluded; the call site's own kwargs survive whole.
    assert event.fields == {"rule": "turtle_breakout", "gate": "choppy_regime"}


def test_parse_events_skips_blank_lines_without_counting_them_as_malformed() -> None:
    events, skipped = parse_events(["", "   ", _line("agent.cycle_start", T0), "\n"])

    assert len(events) == 1
    assert skipped == 0


def test_parse_events_accepts_a_string_timestamp() -> None:
    """`ts` is a float today, but a build that stringified it must still place the event."""
    (event,), skipped = parse_events(['{"ts": "1786194006.5", "event": "agent.cycle_start"}'])

    assert skipped == 0
    assert event.ts == pytest.approx(1786194006.5)


def test_parse_events_uppercases_the_level_so_error_matching_is_case_insensitive() -> None:
    (event,), _ = parse_events(['{"ts": 1.0, "event": "x.y", "level": "error"}'])

    assert event.level == "ERROR"


# -- parse_events: degradation --------------------------------------------------------------------


def test_parse_events_survives_a_truncated_line_from_a_crash_mid_write() -> None:
    """A crash between `write()` and the newline leaves a partial JSON object on disk. It must be
    counted and stepped over -- the records around it are perfectly good."""
    good = _line("agent.cycle_start", T0)
    partial = '{"ts": 1786194011.2, "level": "INFO", "logger": "keel.agent", "eve'
    events, skipped = parse_events([good, partial, good])

    assert len(events) == 2
    assert skipped == 1


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "[]",
        '"just a string"',
        "12345",
        "true",
        "not json at all",
        "{",
    ],
)
def test_parse_events_counts_every_non_object_line_as_skipped(raw: str) -> None:
    events, skipped = parse_events([raw])

    assert events == []
    assert skipped == 1


def test_parse_events_borrows_the_previous_timestamp_when_ts_is_missing() -> None:
    """The log is append-ordered, so a neighbour's timestamp is very nearly right -- showing an
    event slightly imprecisely beats hiding it."""
    events, skipped = parse_events(
        [_line("agent.cycle_start", T0), '{"event": "agent.mode_resolved", "mode": "paper"}']
    )

    assert skipped == 0
    assert len(events) == 2
    assert events[1].ts == T0


def test_parse_events_skips_a_timestampless_line_with_no_predecessor_to_borrow_from() -> None:
    events, skipped = parse_events(['{"event": "agent.mode_resolved"}'])

    assert events == []
    assert skipped == 1


@pytest.mark.parametrize("bad_ts", [True, [1], {"a": 1}, None, "not-a-number"])
def test_parse_events_treats_a_wrongly_typed_ts_as_missing(bad_ts: Any) -> None:
    """`True` is the interesting one: `float(True)` is `1.0`, which would silently place a 2026
    event in 1970."""
    events, skipped = parse_events([json.dumps({"ts": bad_ts, "event": "x.y"})])

    assert events == []
    assert skipped == 1


def test_parse_events_names_an_event_that_has_no_name() -> None:
    (event,), skipped = parse_events(['{"ts": 1.0, "product": "BTC-USD"}'])

    assert skipped == 0
    assert event.event == "(unnamed event)"
    assert event.fields == {"product": "BTC-USD"}


def test_parse_events_ignores_a_non_string_cycle_id_rather_than_grouping_by_it() -> None:
    (event,), _ = parse_events(['{"ts": 1.0, "event": "x.y", "cycle_id": 17}'])

    assert event.cycle_id is None


# -- grouping --------------------------------------------------------------------------------------


def test_group_cycles_puts_every_event_of_one_cycle_in_one_group() -> None:
    events, _ = parse_events(_quiet_cycle_lines("cycle-a", T0))

    (cycle,) = group_cycles(events)

    assert cycle.cycle_id == "cycle-a"
    assert len(cycle.events) == len(events)


def test_group_cycles_regroups_interleaved_cycles_by_id_not_by_adjacency() -> None:
    """Two `keel` processes can share one log file, so a cycle's events are not guaranteed to be
    contiguous. Grouping by adjacency would split one cycle into several rows."""
    events, _ = parse_events(
        [
            _line("agent.cycle_start", T0, "cycle-a"),
            _line("agent.cycle_start", T0 + 1, "cycle-b"),
            _line("agent.mode_resolved", T0 + 2, "cycle-a", mode="paper"),
            _line("agent.mode_resolved", T0 + 3, "cycle-b", mode="live"),
        ]
    )

    cycles = group_cycles(events)

    assert [c.cycle_id for c in cycles] == ["cycle-a", "cycle-b"]
    assert all(len(c.events) == 2 for c in cycles)


def test_group_cycles_keeps_uncorrelated_events_instead_of_dropping_them() -> None:
    """The 64 fetch failures in the real log carry no `cycle_id`. They are the single most
    informative thing in it -- dropping them would hide why nothing traded."""
    events, _ = parse_events(_fetch_failure_pair(T0))

    (cycle,) = group_cycles(events)

    assert cycle.cycle_id is None
    assert cycle.is_uncorrelated
    assert cycle.errors == 2


def test_group_cycles_splits_uncorrelated_runs_on_a_time_gap() -> None:
    """Two failed polls sixteen minutes apart are two attempts, not one."""
    later = T0 + _UNCORRELATED_GAP_SEC + 1
    events, _ = parse_events(_fetch_failure_pair(T0) + _fetch_failure_pair(later))

    cycles = group_cycles(events)

    assert len(cycles) == 2
    assert all(c.is_uncorrelated for c in cycles)
    assert [c.started_ts for c in cycles] == [T0, later]


def test_group_cycles_keeps_one_uncorrelated_run_together_within_the_gap() -> None:
    events, _ = parse_events(
        _fetch_failure_pair(T0) + _fetch_failure_pair(T0 + _UNCORRELATED_GAP_SEC - 1)
    )

    cycles = group_cycles(events)

    assert len(cycles) == 1
    assert cycles[0].errors == 4


def test_group_cycles_starts_a_new_uncorrelated_group_after_a_correlated_one() -> None:
    """An uncorrelated event that follows a cycle must not be glued onto the previous
    uncorrelated group across it -- otherwise a row would span an unrelated cycle."""
    events, _ = parse_events(
        _fetch_failure_pair(T0)
        + [_line("agent.cycle_start", T0 + 1, "cycle-a")]
        + _fetch_failure_pair(T0 + 2)
    )

    cycles = group_cycles(events)

    assert [c.cycle_id for c in cycles] == [None, "cycle-a", None]


def test_summarise_cycle_on_no_events_does_not_raise() -> None:
    cycle = summarise_cycle("cycle-a", [])

    assert cycle.started_ts == 0.0
    assert cycle.is_quiet


# -- summarising: the counts ---------------------------------------------------------------------


def test_quiet_cycle_counts_are_all_zero_and_it_reads_as_quiet() -> None:
    feed = feed_from_lines(_quiet_cycle_lines("cycle-a", T0))
    (cycle,) = feed.cycles

    assert (cycle.signals, cycle.blocked, cycle.entered, cycle.exited, cycle.errors) == (
        0,
        0,
        0,
        0,
        0,
    )
    assert cycle.is_quiet
    assert cycle.mode == "paper"
    assert cycle.products == ("ADA-USD", "BTC-USD", "ETH-USD", "PAXG-USD", "XLM-USD")
    assert cycle.rules == ("turtle_breakout",)


def test_rail_veto_cycle_counts_one_signal_and_one_block_not_one_per_violation() -> None:
    """The real PAXG cycle trips TWO guards on a SINGLE signal. Counting `blocked=2` would imply
    two setups where there was one -- `blocked` counts entries that did not become an order."""
    feed = feed_from_lines(_rail_veto_cycle_lines())
    (cycle,) = feed.cycles

    assert cycle.signals == 1
    assert cycle.blocked == 1
    assert cycle.entered == 0
    assert cycle.errors == 0


def test_rail_veto_cycle_highlights_name_the_rails_and_the_reason() -> None:
    feed = feed_from_lines(_rail_veto_cycle_lines())
    (cycle,) = feed.cycles

    assert "rail veto: per_asset_concentration_cap" in cycle.highlights
    assert "rail veto: monthly_subscription_allowance" in cycle.highlights
    assert "not placed: paper: vetoed by rails" in cycle.highlights


def test_setup_rejected_cycle_is_highlighted_with_its_gate() -> None:
    """The 2026-08-11 `choppy_regime` rejection: zero signals, zero blocks, and yet emphatically
    not a quiet cycle -- the engine looked at a setup and declined it."""
    lines = _quiet_cycle_lines("cycle-a", T0) + [
        _line(
            "engine.setup_rejected",
            T0 + 5,
            "cycle-a",
            rule="turtle_breakout",
            product="PAXG-USD",
            gate="choppy_regime",
        )
    ]
    feed = feed_from_lines(lines)
    (cycle,) = feed.cycles

    assert cycle.highlights == ("gate rejected: choppy_regime (PAXG-USD)",)
    assert cycle_style(cycle) == "warn"


def test_entered_and_exited_count_only_placed_results() -> None:
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        _line(
            "agent.enter_evaluated",
            T0 + 1,
            "cycle-a",
            product="BTC-USD",
            rule="turtle_breakout",
            placed=True,
            reason="placed",
        ),
        _line(
            "agent.exit_evaluated", T0 + 2, "cycle-a", product="ETH-USD", placed=True, reason="tp"
        ),
        _line(
            "agent.exit_evaluated",
            T0 + 3,
            "cycle-a",
            product="ADA-USD",
            placed=False,
            reason="no exit signal",
        ),
    ]
    feed = feed_from_lines(lines)
    (cycle,) = feed.cycles

    assert cycle.entered == 1
    assert cycle.exited == 1
    # A not-placed EXIT is routine (no exit signal) and is not a blocked ENTRY.
    assert cycle.blocked == 0
    assert "ENTERED BTC-USD" in cycle.highlights
    assert "EXITED ETH-USD" in cycle.highlights


def test_a_stringified_placed_false_does_not_read_as_an_entry() -> None:
    """`bool("false")` is `True`. Getting this wrong would have the feed report a fill that never
    happened, which is the single worst thing this module could say."""
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        _line(
            "agent.enter_evaluated",
            T0 + 1,
            "cycle-a",
            product="BTC-USD",
            placed="false",
            reason="paper: vetoed by rails",
        ),
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle.entered == 0
    assert cycle.blocked == 1


def test_entry_bar_not_ready_counts_as_blocked_even_though_no_entry_was_evaluated() -> None:
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        _line(
            "agent.entry_bar_not_ready",
            T0 + 1,
            "cycle-a",
            level="WARNING",
            product="BTC-USD",
            rule="turtle_breakout",
            granularity="ONE_DAY",
            bars_behind=1,
            reason="stored bar is behind the expected one",
        ),
        _line(
            "agent.entries_withheld",
            T0 + 1,
            "cycle-a",
            level="WARNING",
            blocked_count=1,
            products=["BTC-USD"],
            rules=["turtle_breakout"],
        ),
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle.blocked == 1
    assert "entries withheld (1 blocked)" in cycle.highlights


def test_signals_falls_back_to_counting_setups_when_signals_evaluated_is_out_of_window() -> None:
    """A cycle whose `agent.signals_evaluated` records fell off the front of the bounded read
    must still report the setup it detected, not a misleading `signals=0`."""
    lines = [
        _line(
            "engine.setup_detected",
            T0,
            "cycle-a",
            rule="turtle_breakout",
            product="PAXG-USD",
            entry="4342.52",
        )
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle.signals == 1


def test_errors_are_counted_at_any_level_above_warning() -> None:
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        _line("cb_client.spot_fetch_failed", T0 + 1, "cycle-a", level="ERROR", exc="boom"),
        _line("agent.feed_stale", T0 + 2, "cycle-a", level="WARNING", product="XLM-USD"),
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle.errors == 1
    assert "stale feed: XLM-USD" in cycle.highlights


def test_a_products_field_that_is_not_a_list_is_ignored_not_exploded() -> None:
    """`agent.feed_polled.products` is a list today. A build that wrote a dict there must not
    take the overlay down, and must not scatter dict keys into the product breadth."""
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        _line("agent.feed_polled", T0 + 1, "cycle-a", products={"a": 1}, candles_polled=5),
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle.products == ()


def test_events_beyond_the_per_cycle_cap_are_dropped_but_the_counts_still_cover_them() -> None:
    over = _MAX_EVENTS_PER_CYCLE + 10
    lines = [
        _line(
            "agent.enter_evaluated",
            T0 + i,
            "cycle-a",
            product="BTC-USD",
            placed=False,
            reason="paper: vetoed by rails",
        )
        for i in range(over)
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle.blocked == over  # counted over ALL events...
    assert len(cycle.events) == _MAX_EVENTS_PER_CYCLE  # ...but only the newest are retained
    assert cycle.events_dropped == 10


# -- ordering + bounds ----------------------------------------------------------------------------


def test_feed_is_newest_first() -> None:
    lines = (
        _quiet_cycle_lines("oldest", T0)
        + _quiet_cycle_lines("middle", T0 + 86400)
        + _quiet_cycle_lines("newest", T0 + 172800)
    )

    feed = feed_from_lines(lines)

    assert [c.cycle_id for c in feed.cycles] == ["newest", "middle", "oldest"]


def test_cycles_sharing_a_timestamp_keep_a_stable_reverse_file_order() -> None:
    lines = [
        _line("agent.cycle_start", T0, "first"),
        _line("agent.cycle_start", T0, "second"),
    ]

    feed = feed_from_lines(lines)

    assert [c.cycle_id for c in feed.cycles] == ["second", "first"]


def test_max_cycles_keeps_the_newest_and_reports_what_it_dropped() -> None:
    lines: list[str] = []
    for i in range(10):
        lines.extend(_quiet_cycle_lines(f"cycle-{i:02d}", T0 + i * 86400))

    feed = feed_from_lines(lines, max_cycles=3)

    assert [c.cycle_id for c in feed.cycles] == ["cycle-09", "cycle-08", "cycle-07"]
    assert feed.cycles_dropped == 7
    assert feed.window_truncated is True
    assert any("BOUNDED" in note for note in footer_notes(feed))


def test_a_quiet_cycle_still_gets_a_row() -> None:
    """The load-bearing requirement: the run of quiet cycles IS the answer to "is it alive". A
    feed that omitted them would reproduce exactly the dead-looking dashboard it replaces."""
    lines: list[str] = []
    for i in range(12):
        lines.extend(_quiet_cycle_lines(f"cycle-{i:02d}", T0 + i * 86400))

    feed = feed_from_lines(lines)

    assert len(feed.cycles) == 12
    assert all(c.is_quiet for c in feed.cycles)


# -- feed_from_lines: whole-input degradation ------------------------------------------------------


def test_feed_from_no_lines_is_empty_not_an_exception() -> None:
    feed = feed_from_lines([])

    assert feed.status == "empty"
    assert feed.cycles == ()


def test_feed_from_only_unparseable_lines_says_unparseable_not_empty() -> None:
    """The two have completely different fixes -- "the file has no records yet" versus "this is
    not the log you think it is"."""
    feed = feed_from_lines(["not json", "<html>", "{"])

    assert feed.status == "unparseable"
    assert feed.lines_skipped == 3
    text = " ".join(describe_status(feed))
    assert "3 line(s) were read" in text
    assert "JSON object" in text


def test_feed_reports_skipped_lines_rather_than_swallowing_them() -> None:
    feed = feed_from_lines([*_quiet_cycle_lines("cycle-a", T0), "{partial"])

    assert feed.status == "ok"
    assert feed.lines_skipped == 1
    assert any("skipped as unparseable" in note for note in footer_notes(feed))


# -- read_log_window: the file-level degradation ---------------------------------------------------


def test_read_log_window_missing_file(tmp_path: Any) -> None:
    window = read_log_window(tmp_path / "nope.log")

    assert window.status == "missing"
    assert "nope.log" in (window.detail or "")


def test_read_log_window_empty_file(tmp_path: Any) -> None:
    path = tmp_path / "keel.log"
    path.write_text("")

    window = read_log_window(path)

    assert window.status == "empty"


def test_read_log_window_whitespace_only_file_is_empty_not_ok(tmp_path: Any) -> None:
    path = tmp_path / "keel.log"
    path.write_text("\n\n   \n")

    assert read_log_window(path).status == "empty"


def test_read_log_window_directory_is_unreadable_not_a_crash(tmp_path: Any) -> None:
    """`open()` on a directory raises `IsADirectoryError` (an `OSError`). A misconfigured
    `logging.file` pointing at a directory must read as one sentence, not a traceback."""
    window = read_log_window(tmp_path)

    assert window.status == "unreadable"
    assert window.detail


def test_read_log_window_unreadable_file_reports_the_reason(tmp_path: Any) -> None:
    path = tmp_path / "keel.log"
    path.write_text(_line("agent.cycle_start", T0))
    path.chmod(0o000)
    try:
        window = read_log_window(path)
    finally:
        path.chmod(0o600)

    # Running as root defeats the permission bit entirely; skip rather than assert a falsehood.
    if window.status == "ok":
        pytest.skip("filesystem permissions are not enforced for this user")
    assert window.status == "unreadable"
    assert "Error" in (window.detail or "")


def test_read_log_window_reads_the_whole_file_when_it_is_under_the_byte_cap(tmp_path: Any) -> None:
    lines = _quiet_cycle_lines("cycle-a", T0)
    path = tmp_path / "keel.log"
    path.write_text("\n".join(lines) + "\n")

    window = read_log_window(path)

    assert window.status == "ok"
    assert window.truncated is False
    assert len(window.lines) == len(lines)


def test_read_log_window_without_a_trailing_newline_keeps_the_last_record(tmp_path: Any) -> None:
    """The engine's handler is mid-write far more often than not, and the newest record is the
    one an operator most wants."""
    lines = _quiet_cycle_lines("cycle-a", T0)
    path = tmp_path / "keel.log"
    path.write_text("\n".join(lines))  # no trailing newline

    window = read_log_window(path)

    assert len(window.lines) == len(lines)


def test_read_log_window_byte_cap_drops_the_partial_first_line(tmp_path: Any) -> None:
    """Seeking to `size - max_bytes` lands mid-record. Feeding that half-object to the parser
    would count a phantom malformed line on every single repaint."""
    lines = [_line("agent.cycle_start", T0 + i, f"cycle-{i:03d}") for i in range(50)]
    blob = "\n".join(lines) + "\n"
    path = tmp_path / "keel.log"
    path.write_text(blob)

    # A cap that lands squarely inside a record, not on a boundary.
    window = read_log_window(path, max_bytes=len(lines[-1].encode()) * 3 + 7)

    assert window.truncated is True
    assert window.lines  # something survived
    events, skipped = parse_events(window.lines)
    assert skipped == 0  # and it is all whole records
    assert events


def test_read_log_window_byte_cap_smaller_than_one_record_yields_no_lines_not_a_crash(
    tmp_path: Any,
) -> None:
    path = tmp_path / "keel.log"
    path.write_text(_line("agent.cycle_start", T0) + "\n")

    window = read_log_window(path, max_bytes=8)

    # Nothing whole survived the boundary trim -- reported as `oversized`, never as a partial
    # record, and deliberately not as `empty` (the file is anything but).
    assert window.status == "oversized"
    assert window.lines == ()


def test_read_log_window_line_cap_keeps_the_newest_lines(tmp_path: Any) -> None:
    lines = [_line("agent.cycle_start", T0 + i, f"cycle-{i:03d}") for i in range(50)]
    path = tmp_path / "keel.log"
    path.write_text("\n".join(lines) + "\n")

    window = read_log_window(path, max_lines=5)

    assert len(window.lines) == 5
    assert window.truncated is True
    assert "cycle-049" in window.lines[-1]


def test_read_log_window_decodes_non_utf8_bytes_instead_of_raising(tmp_path: Any) -> None:
    """A truncated multi-byte character at a rotation boundary must cost one replacement
    character, not the whole overlay."""
    path = tmp_path / "keel.log"
    path.write_bytes(b"\xff\xfe garbage\n" + _line("agent.cycle_start", T0).encode() + b"\n")

    window = read_log_window(path)

    assert window.status == "ok"
    events, skipped = parse_events(window.lines)
    assert len(events) == 1
    assert skipped == 1


def test_read_log_window_after_rotation_reads_the_new_small_file(tmp_path: Any) -> None:
    """`RotatingFileHandler` renames the old file aside and opens a fresh one. Opening by PATH on
    every build (rather than holding a handle) is what makes that a non-event."""
    path = tmp_path / "keel.log"
    path.write_text("\n".join(_quiet_cycle_lines("old-cycle", T0)) + "\n")
    assert read_log_window(path).status == "ok"

    path.rename(tmp_path / "keel.log.1")
    path.write_text(_line("agent.cycle_start", T0 + 86400, "new-cycle") + "\n")

    window = read_log_window(path)
    feed = feed_from_lines(window.lines)

    assert [c.cycle_id for c in feed.cycles] == ["new-cycle"]


# -- build_activity_feed: config -> feed, and its totality -----------------------------------------


class _FakeLogging:
    def __init__(self, file: Any) -> None:
        self.file = file


class _FakeConfig:
    def __init__(self, file: Any) -> None:
        self.logging = _FakeLogging(file)


def test_resolve_log_path_uses_the_configured_logging_file(tmp_path: Any) -> None:
    path = tmp_path / "custom" / "engine.log"

    assert resolve_log_path(_FakeConfig(str(path))) == path.resolve()


@pytest.mark.parametrize("bad", [None, "", "   ", 17, object()])
def test_resolve_log_path_falls_back_to_the_default_for_an_unusable_setting(bad: Any) -> None:
    assert resolve_log_path(_FakeConfig(bad)).name == "keel.log"


def test_resolve_log_path_on_a_config_without_a_logging_section_does_not_raise() -> None:
    assert resolve_log_path(object()).name == "keel.log"


def test_build_activity_feed_end_to_end(tmp_path: Any) -> None:
    path = tmp_path / "keel.log"
    path.write_text(
        "\n".join(_quiet_cycle_lines("quiet-one", T0) + _rail_veto_cycle_lines("veto", T0 + 86400))
        + "\n"
    )

    # `scope="all"`: this test is about the read+parse+group pipeline, and the two fixture cycles
    # are deliberately a day apart. The day-scoping that `build_activity_feed` applies by default
    # has its own end-to-end test below.
    feed = build_activity_feed(_FakeConfig(str(path)), scope="all")

    assert feed.status == "ok"
    assert feed.source == str(path.resolve())
    assert [c.cycle_id for c in feed.cycles] == ["veto", "quiet-one"]
    assert _cycle_by_id(feed, "veto").blocked == 1


def test_build_activity_feed_on_a_missing_log_is_a_status_not_an_exception(tmp_path: Any) -> None:
    feed = build_activity_feed(_FakeConfig(str(tmp_path / "gone.log")))

    assert feed.status == "missing"
    assert feed.cycles == ()
    assert describe_status(feed)  # and it has something to say about it


def test_build_activity_feed_never_raises_on_a_hostile_config(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `resolve_log_path` falls back to the RELATIVE default, which resolves against the cwd --
    # chdir into an empty directory so this asserts the fallback, not whatever `logs/keel.log`
    # another test in the run happened to leave next to pytest's working directory.
    monkeypatch.chdir(tmp_path)

    class _Exploding:
        @property
        def logging(self) -> Any:  # pragma: no cover - the raise IS the behaviour under test
            raise RuntimeError("config went bad")

    feed = build_activity_feed(_Exploding())

    # The property error is swallowed and the default path used -- a status, never a traceback.
    assert feed.status == "missing"
    assert describe_status(feed)


# -- describe_status: never blank, always actionable -----------------------------------------------


@pytest.mark.parametrize("status", ["missing", "empty", "unparseable", "unreadable"])
def test_describe_status_always_says_something_useful(status: str) -> None:
    feed = ActivityFeed(status=status, source="/tmp/keel.log", detail="reason", lines_skipped=3)

    lines = describe_status(feed)

    assert lines
    assert any(line.strip() for line in lines)


def test_describe_status_for_a_missing_log_explains_the_working_directory_trap() -> None:
    """The commonest cause by far: `keel tui` run from anywhere but the deployment root resolves
    the relative default against the wrong cwd."""
    feed = ActivityFeed(status="missing", source="/somewhere/logs/keel.log")

    text = " ".join(describe_status(feed))

    assert "/somewhere/logs/keel.log" in text
    assert "WORKING DIRECTORY" in text
    assert "logging.file" in text


def test_describe_status_for_an_empty_log_mentions_the_verbose_toggle() -> None:
    """`logging.verbose: false` is the default and records only errors, so a healthy deployment
    genuinely writes nothing -- that is the fix, not a bug report."""
    text = " ".join(describe_status(ActivityFeed(status="empty", source="/tmp/keel.log")))

    assert "verbose" in text


# -- rendering ------------------------------------------------------------------------------------


def test_header_and_row_columns_line_up() -> None:
    """The header is built from the same field widths as the row, so this asserts the property
    that keeps them honest rather than a hardcoded column number."""
    (cycle,) = feed_from_lines(_quiet_cycle_lines("cycle-a", T0)).cycles
    row = render_cycle_row(cycle)

    # Each column starts at the same offset in the header as it does in the row.
    assert ACTIVITY_HEADER.index("mode") == row.index("paper")
    assert ACTIVITY_HEADER.index("sig") == row.index(f"{cycle.signals:>3}")
    assert ACTIVITY_HEADER.index("when") == row.index("2")  # the timestamp's leading digit


def test_cycle_row_renders_local_time_not_an_epoch_float() -> None:
    (cycle,) = feed_from_lines(_quiet_cycle_lines("cycle-a", T0)).cycles

    row = render_cycle_row(cycle)

    assert time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0)) in row
    assert "1786194006" not in row


def test_cycle_row_caret_and_marker_reflect_selection_and_expansion() -> None:
    (cycle,) = feed_from_lines(_quiet_cycle_lines("cycle-a", T0)).cycles

    assert render_cycle_row(cycle).startswith(" ▸")
    assert render_cycle_row(cycle, selected=True).startswith(">▸")
    assert render_cycle_row(cycle, selected=True, expanded=True).startswith(">▾")


def test_quiet_row_says_so_rather_than_rendering_a_bare_line_of_zeroes() -> None:
    (cycle,) = feed_from_lines(_quiet_cycle_lines("cycle-a", T0)).cycles

    assert "quiet -- looked, nothing to do" in render_cycle_row(cycle)


def test_uncorrelated_row_labels_itself() -> None:
    (cycle,) = feed_from_lines(_fetch_failure_pair(T0)).cycles

    assert "uncorrelated events (no cycle_id)" in render_cycle_row(cycle)


def test_row_summarises_highlights_beyond_the_display_cap() -> None:
    lines = [_line("agent.cycle_start", T0, "cycle-a")]
    for i in range(6):
        lines.append(
            _line(
                "guards.check_failed",
                T0 + i,
                "cycle-a",
                product="BTC-USD",
                side="BUY",
                violation=f"rail_{i}: too much",
            )
        )
    (cycle,) = feed_from_lines(lines).cycles

    assert "+3 more" in render_cycle_row(cycle)


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (_fetch_failure_pair(T0), "alert"),
        (_rail_veto_cycle_lines(), "warn"),
        (_quiet_cycle_lines("cycle-a", T0), "muted"),
    ],
)
def test_cycle_style_distinguishes_notable_cycles_from_quiet_ones(
    lines: list[str], expected: str
) -> None:
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle_style(cycle) == expected


def test_a_cycle_that_placed_an_order_reads_as_ok() -> None:
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        _line(
            "agent.signals_evaluated",
            T0 + 1,
            "cycle-a",
            product="BTC-USD",
            rule_count=1,
            signal_count=1,
        ),
        _line(
            "agent.enter_evaluated",
            T0 + 2,
            "cycle-a",
            product="BTC-USD",
            placed=True,
            reason="placed",
        ),
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle_style(cycle) == "ok"


def test_signals_without_a_fill_read_as_normal_neither_quiet_nor_alarming() -> None:
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        _line(
            "agent.signals_evaluated",
            T0 + 1,
            "cycle-a",
            product="BTC-USD",
            rule_count=1,
            signal_count=2,
        ),
    ]
    (cycle,) = feed_from_lines(lines).cycles

    assert cycle_style(cycle) == "normal"


# -- rendering: one event's detail -----------------------------------------------------------------


def _detail_for(lines: list[str], event_name: str) -> str:
    (cycle,) = feed_from_lines(lines).cycles
    ev = next(e for e in cycle.events if e.event == event_name)
    return render_event_detail(ev)


def test_setup_detected_detail_shows_entry_stop_and_target() -> None:
    detail = _detail_for(_rail_veto_cycle_lines(), "engine.setup_detected")

    assert "PAXG-USD" in detail
    assert "entry=4342.52" in detail
    assert "stop=4197.09" in detail  # trimmed from 4197.09381782563408
    assert "target=5215.07" in detail
    assert "cts=5" in detail


def test_guards_check_failed_detail_shows_the_whole_violation_string() -> None:
    """The collapsed row keeps only the rail's NAME. The expansion is where an operator gets the
    arithmetic that explains it, so it must not be trimmed away here too."""
    detail = _detail_for(_rail_veto_cycle_lines(), "guards.check_failed")

    assert "per_asset_concentration_cap" in detail
    assert "exceeds 0.5 of max_exposure_usd (2500.0)" in detail
    assert "VETOED" in detail


def test_enter_evaluated_detail_states_the_verdict_and_the_reason() -> None:
    detail = _detail_for(_rail_veto_cycle_lines(), "agent.enter_evaluated")

    assert "NOT PLACED" in detail
    assert "paper: vetoed by rails" in detail


def test_setup_rejected_detail_names_the_gate() -> None:
    lines = [
        _line(
            "engine.setup_rejected",
            T0,
            "cycle-a",
            rule="turtle_breakout",
            product="PAXG-USD",
            gate="choppy_regime",
        )
    ]

    assert "choppy_regime" in _detail_for(lines, "engine.setup_rejected")


def test_an_error_events_detail_is_the_exception_line_not_the_whole_traceback() -> None:
    """A traceback is why 330 records occupy 815 KB, and is the least useful part of it on a
    dashboard."""
    detail = _detail_for(_fetch_failure_pair(T0), "cb_client.accounts_fetch_failed")

    assert "401 Unauthorized" in detail
    assert "Traceback" not in detail
    assert "File \"" not in detail


def test_an_unknown_event_renders_its_own_fields_rather_than_nothing() -> None:
    """The event vocabulary grows faster than this renderer will. An event showing raw fields is
    useful; one showing nothing is a bug that hides itself."""
    lines = [_line("executor.stop_rolled", T0, "cycle-a", position_id=7, new_stop="123.45")]

    detail = _detail_for(lines, "executor.stop_rolled")

    assert "position_id=7" in detail
    assert "new_stop=123.45" in detail


def test_event_detail_is_capped_so_one_field_cannot_define_the_row_width() -> None:
    lines = [
        _line(
            "guards.check_failed",
            T0,
            "cycle-a",
            product="BTC-USD",
            side="BUY",
            violation="x" * 5000,
        )
    ]

    detail = _detail_for(lines, "guards.check_failed")

    assert len(detail) <= 220
    assert detail.endswith("...")


def test_event_detail_never_raises_on_an_event_with_no_fields_at_all() -> None:
    for name in (
        "agent.cycle_start",
        "agent.feed_polled",
        "agent.mode_resolved",
        "agent.paper_equity",
        "agent.signals_evaluated",
        "engine.no_signal",
        "engine.setup_detected",
        "engine.setup_rejected",
        "guards.check_failed",
        "agent.enter_evaluated",
        "agent.exit_evaluated",
        "agent.entry_bar_not_ready",
        "agent.entries_withheld",
        "agent.feed_stale",
        "agent.cycle_skipped",
        "equity.external_flow_recorded",
        "some.brand_new_event",
    ):
        ev = ActivityEvent(ts=T0, level="INFO", event=name, cycle_id="c", fields={})
        assert isinstance(render_event_detail(ev), str)
        assert isinstance(render_event_row(ev), str)
        assert isinstance(event_style(ev), str)


def test_event_row_shows_the_stable_event_id_so_it_can_be_grepped_in_the_raw_log() -> None:
    ev = ActivityEvent(ts=T0, level="INFO", event="guards.check_failed", cycle_id="c", fields={})

    assert "guards.check_failed" in render_event_row(ev)


def test_no_signal_is_muted_so_it_cannot_bury_a_rail_veto() -> None:
    quiet = ActivityEvent(ts=T0, level="INFO", event="engine.no_signal", cycle_id="c", fields={})
    veto = ActivityEvent(ts=T0, level="INFO", event="guards.check_failed", cycle_id="c", fields={})

    assert event_style(quiet) == "muted"
    assert event_style(veto) == "warn"


@pytest.mark.parametrize(
    ("raw", "places", "expected"),
    [
        ("4197.09381782563408", 2, "4197.09"),
        ("0.161297", 2, "0.161297"),  # sub-dollar assets keep their precision
        ("1979.0", 2, "1979"),
        ("605", 2, "605"),
        ("-0.00012345678", 2, "-0.000123"),
        ("not a number", 2, "not a number"),
    ],
)
def test_short_num(raw: str, places: int, expected: str) -> None:
    assert _short_num(raw, places) == expected


def test_footer_notes_always_name_the_source_so_the_operator_knows_what_was_read() -> None:
    feed = feed_from_lines(_quiet_cycle_lines("cycle-a", T0), source="/tmp/keel.log")

    assert any("/tmp/keel.log" in note for note in footer_notes(feed))


def test_footer_notes_state_the_bound_in_the_units_it_is_enforced_in() -> None:
    feed = feed_from_lines(_quiet_cycle_lines("cycle-a", T0), truncated=True)

    text = " ".join(footer_notes(feed))

    assert f"{_MAX_BYTES // 1024} KiB" in text


# -- the whole thing, on a realistic mixed log -----------------------------------------------------


def test_a_realistic_mixed_log_reads_as_the_story_it_actually_is() -> None:
    """Three weeks of quiet daily cycles, one rail veto, one gate rejection, and a run of
    uncorrelated fetch failures -- the exact shape of the deployment this feature was built for.
    The point of the assertion is that all four kinds survive together, in order, and that the
    notable ones are distinguishable from the quiet ones by style alone."""
    lines: list[str] = []
    for i in range(5):
        lines.extend(_quiet_cycle_lines(f"quiet-{i}", T0 + i * 86400))
    lines.extend(_fetch_failure_pair(T0 + 5 * 86400))
    lines.extend(_rail_veto_cycle_lines("veto", T0 + 6 * 86400))
    lines.extend(
        _quiet_cycle_lines("rejected", T0 + 7 * 86400)
        + [
            _line(
                "engine.setup_rejected",
                T0 + 7 * 86400 + 5,
                "rejected",
                rule="turtle_breakout",
                product="PAXG-USD",
                gate="choppy_regime",
            )
        ]
    )
    lines.append("{truncated mid-write")

    feed = feed_from_lines(lines, source="/tmp/keel.log")

    assert feed.status == "ok"
    assert feed.lines_skipped == 1
    assert [c.cycle_id for c in feed.cycles][:3] == ["rejected", "veto", None]
    styles = {c.cycle_id: cycle_style(c) for c in feed.cycles}
    assert styles["veto"] == "warn"
    assert styles["rejected"] == "warn"
    assert styles[None] == "alert"
    assert styles["quiet-0"] == "muted"


# -- hostile numeric input: the renderers run inside the live loop's repaint path -----------------


@pytest.mark.parametrize("hostile_ts", [1e20, -1e20, "nan", "inf", "-inf", 1e300])
def test_a_timestamp_outside_the_renderable_range_is_treated_as_missing(hostile_ts: Any) -> None:
    """`time.localtime`/`time.strftime` RAISE on these rather than degrade, and the renderer runs
    on the dashboard's repaint path -- one such record would take the whole TUI down. It is
    treated exactly like a missing `ts`: the neighbour's time is borrowed."""
    lines = [
        _line("agent.cycle_start", T0, "cycle-a"),
        json.dumps({"ts": hostile_ts, "event": "agent.mode_resolved", "cycle_id": "cycle-a"}),
    ]

    feed = feed_from_lines(lines)
    (cycle,) = feed.cycles

    assert len(cycle.events) == 2
    assert cycle.events[1].ts == T0
    assert render_cycle_row(cycle)  # and it renders, which is the point
    assert all(render_event_row(e) for e in cycle.events)


def test_a_hostile_timestamp_with_no_predecessor_is_skipped_not_rendered() -> None:
    feed = feed_from_lines([json.dumps({"ts": 1e20, "event": "agent.cycle_start"})])

    assert feed.cycles == ()
    assert feed.lines_skipped == 1


def test_rendering_a_hand_built_event_with_an_impossible_ts_does_not_raise() -> None:
    """The backstop for a caller that never went through `parse_events` -- a `??:??:??` on screen
    is a far better outcome than an exception on the repaint path."""
    ev = ActivityEvent(ts=1e20, level="INFO", event="agent.cycle_start", cycle_id="c", fields={})
    cycle = summarise_cycle("c", [ev])

    assert "?" in render_event_row(ev)
    assert "?" in render_cycle_row(cycle)


def test_one_garbled_count_does_not_discard_every_other_cycle_in_the_window() -> None:
    """`float("inf")` PARSES; it is `int()` that raises `OverflowError`, which is not a
    `ValueError`. Letting that escape would abort the whole grouping pass, so a single bad line
    would empty a feed holding weeks of perfectly good cycles."""
    lines = [
        *_quiet_cycle_lines("good-before", T0),
        _line(
            "agent.signals_evaluated",
            T0 + 100,
            "garbled",
            product="BTC-USD",
            rule_count=1,
            signal_count="inf",
        ),
        *_quiet_cycle_lines("good-after", T0 + 86400),
    ]

    feed = feed_from_lines(lines)

    assert feed.status == "ok"
    assert {c.cycle_id for c in feed.cycles} == {"good-before", "garbled", "good-after"}
    assert _cycle_by_id(feed, "garbled").signals == 0


def test_a_read_window_holding_no_whole_record_is_not_reported_as_empty(tmp_path: Any) -> None:
    """One ERROR record carrying a traceback can be kilobytes on its own. Reusing the `empty`
    status here would print "engine log is empty (N bytes)", which is self-contradictory and
    sends an operator after the wrong problem."""
    path = tmp_path / "keel.log"
    path.write_text(_line("cb_client.accounts_fetch_failed", T0, None, exc="x" * 4000) + "\n")

    # A window that lands inside the single huge record: everything before the first newline is
    # a partial record and is trimmed, and the only newline in range is the file's trailing one.
    window = read_log_window(path, max_bytes=64)

    assert window.status == "oversized"
    assert "no complete record" in (window.detail or "")
    assert window.size_bytes > 64

    feed = ActivityFeed(status=window.status, source=str(path), detail=window.detail)
    text = " ".join(describe_status(feed))
    assert "empty" not in text
    assert "No complete record" in text


# ==================================================================================================
# Day scoping -- `apply_scope`, the default `today` view, and the empty state that must never be
# blank.
#
# Every test below pins its own "now" and derives every fixture timestamp from it through
# `_local_midnight`, so the whole section is deterministic in any timezone and on any day it
# happens to run -- which is the entire reason `scope_start_ts`/`apply_scope`/`build_activity_feed`
# take `now_ts` as a parameter instead of reading a clock internally.
# ==================================================================================================


def _local_midnight(now_ts: float, days_ago: int = 0) -> float:
    """Local midnight `days_ago` calendar days before the local day containing `now_ts`.

    Calendar arithmetic, not `- days * 86400`: that is what makes these fixtures land on the
    intended civil day across a DST transition, which is exactly the property the production
    boundary claims and these tests would otherwise be unable to hold it to."""
    day = datetime.datetime.fromtimestamp(now_ts).date() - datetime.timedelta(days=days_ago)
    return datetime.datetime.combine(day, datetime.time.min).timestamp()


#: The instant the scope tests measure from: 14:00 LOCAL on the local day containing a fixed UTC
#: epoch. Anchoring through `_local_midnight` rather than to the raw epoch keeps "today" the same
#: civil day whether the suite runs in UTC, New York or Tokyo.
_SCOPE_NOW = _local_midnight(1_786_212_000.0) + 14 * 3600

#: The same day, but 07:00 -- BEFORE the deployment's 09:00 daily cycle. This is the case the
#: whole empty-state design exists for: "today" is legitimately empty every single morning.
_SCOPE_NOW_EARLY = _local_midnight(_SCOPE_NOW) + 7 * 3600


def _cycle_at(ts: float, cycle_id: str) -> list[str]:
    """One quiet cycle starting at `ts`."""
    return _quiet_cycle_lines(cycle_id, ts)


def _scoped(lines: list[str], scope: str, now_ts: float, **kwargs: Any) -> ActivityFeed:
    return apply_scope(
        feed_from_lines(lines, source="/tmp/keel.log", **kwargs), scope, now_ts=now_ts
    )


# -- the boundary itself ---------------------------------------------------------------------------


def test_scope_start_ts_for_today_is_local_midnight_of_the_current_calendar_day() -> None:
    start = scope_start_ts("today", _SCOPE_NOW)

    assert start == _local_midnight(_SCOPE_NOW)
    # ...and it renders as 00:00:00 in the SAME local clock the rows are stamped in, which is the
    # property that makes "today" mean what the operator reading those stamps thinks it means.
    assert start is not None
    assert time.strftime("%H:%M:%S", time.localtime(start)) == "00:00:00"


def test_scope_start_ts_for_today_is_not_a_rolling_24_hours() -> None:
    """The distinction the requirement turns on. At 14:00 the boundary is 14 hours back, not 24 --
    a rolling window would put yesterday's 09:00 cycle on screen every morning and drop it every
    afternoon, so the same day's feed would change shape depending on when it was opened."""
    start = scope_start_ts("today", _SCOPE_NOW)

    assert start is not None
    assert _SCOPE_NOW - start == pytest.approx(14 * 3600, abs=3600)
    assert start != _SCOPE_NOW - 86400


def test_scope_start_ts_for_7d_covers_today_plus_the_six_days_before_it() -> None:
    start = scope_start_ts("7d", _SCOPE_NOW)

    assert start == _local_midnight(_SCOPE_NOW, days_ago=6)


def test_scope_start_ts_for_all_has_no_lower_bound() -> None:
    assert scope_start_ts("all", _SCOPE_NOW) is None


@pytest.mark.parametrize("bad_now", [float("inf"), float("nan"), 1e30, -1e30])
def test_scope_start_ts_on_an_unusable_clock_degrades_to_unbounded_not_to_a_crash(
    bad_now: float,
) -> None:
    """`None` means "show everything". A clock this module cannot read must widen the view, never
    empty it -- an empty screen is the one outcome the whole module is built to avoid."""
    assert scope_start_ts("today", bad_now) is None


def test_next_activity_scope_cycles_today_then_7d_then_all_then_back() -> None:
    assert next_activity_scope("today") == "7d"
    assert next_activity_scope("7d") == "all"
    assert next_activity_scope("all") == "today"


def test_next_activity_scope_of_something_unrecognised_lands_on_the_default() -> None:
    assert next_activity_scope("last tuesday") == DEFAULT_ACTIVITY_SCOPE


def test_normalise_scope_falls_back_to_today_rather_than_filtering_to_nothing() -> None:
    assert normalise_scope("7d") == "7d"
    assert normalise_scope("") == DEFAULT_ACTIVITY_SCOPE
    assert normalise_scope("yesterday") == DEFAULT_ACTIVITY_SCOPE


def test_the_default_scope_is_today() -> None:
    """Stated as a test because it is the requirement, not an implementation detail."""
    assert DEFAULT_ACTIVITY_SCOPE == "today"
    assert ACTIVITY_SCOPES[0] == "today"


# -- what `today` actually shows -------------------------------------------------------------------


def test_today_shows_several_cycles_from_today_and_hides_every_earlier_day() -> None:
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=3) + 9 * 3600, "old-3"),
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "old-1"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today-a"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 11 * 3600, "today-b"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 13 * 3600, "today-c"),
    ]

    feed = _scoped(lines, "today", _SCOPE_NOW)

    assert [c.cycle_id for c in feed.cycles] == ["today-c", "today-b", "today-a"]
    assert feed.cycles_out_of_scope == 2
    assert feed.scope == "today"
    assert feed.scope_start_ts == _local_midnight(_SCOPE_NOW)
    # The newest thing OUTSIDE the scope is kept -- and it is the newest, not the oldest.
    assert feed.last_cycle_before_scope is not None
    assert feed.last_cycle_before_scope.cycle_id == "old-1"


def test_today_shows_exactly_one_cycle_when_the_day_holds_exactly_one() -> None:
    """The real deployment's normal afternoon: one cycle a day, at 09:00. One row is the correct,
    complete answer -- and the header must say "1 cycle", not look like a truncated log."""
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "yesterday"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"),
    ]

    feed = _scoped(lines, "today", _SCOPE_NOW)

    assert [c.cycle_id for c in feed.cycles] == ["today"]
    assert feed.cycles_out_of_scope == 1
    assert "1 cycle" in scope_headline(feed)
    assert "1 cycles" not in scope_headline(feed)


def test_a_quiet_cycle_that_ran_today_is_still_a_row_not_an_empty_state() -> None:
    """A quiet cycle is the positive observation "it looked and there was nothing to do". Scoping
    to today must not turn that into a blank day."""
    feed = _scoped(_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"), "today", _SCOPE_NOW)

    (cycle,) = feed.cycles
    assert cycle.is_quiet
    assert "quiet -- looked, nothing to do" in render_cycle_row(cycle)


def test_today_holds_nothing_before_the_daily_run_but_the_last_run_is_remembered() -> None:
    """07:00 on a deployment that runs at 09:00 -- the case that would otherwise render blank."""
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW_EARLY, days_ago=2) + 9 * 3600, "old"),
        *_cycle_at(_local_midnight(_SCOPE_NOW_EARLY, days_ago=1) + 9 * 3600, "yesterday"),
    ]

    feed = _scoped(lines, "today", _SCOPE_NOW_EARLY)

    assert feed.status == "ok"  # the LOG is fine; it is the DAY that is empty
    assert feed.cycles == ()
    assert feed.cycles_out_of_scope == 2
    assert feed.last_cycle_before_scope is not None
    assert feed.last_cycle_before_scope.cycle_id == "yesterday"


@pytest.mark.parametrize(
    ("scope", "expected_ids"),
    [
        ("today", ["today-b", "today-a"]),
        ("7d", ["today-b", "today-a", "d1", "d3", "d6"]),
        ("all", ["today-b", "today-a", "d1", "d3", "d6", "d9", "d40"]),
    ],
)
def test_every_scope_returns_the_cycles_it_promises(scope: str, expected_ids: list[str]) -> None:
    """One fixture, three scopes, exact counts -- `7d` must include the sixth day back and exclude
    the ninth, and `all` must reach the 40-day-old one."""
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=40) + 9 * 3600, "d40"),
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=9) + 9 * 3600, "d9"),
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=6) + 9 * 3600, "d6"),
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=3) + 9 * 3600, "d3"),
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "d1"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today-a"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 13 * 3600, "today-b"),
    ]

    feed = _scoped(lines, scope, _SCOPE_NOW)

    assert [c.cycle_id for c in feed.cycles] == expected_ids
    assert feed.cycles_out_of_scope == 7 - len(expected_ids)


def test_a_cycle_at_exactly_local_midnight_belongs_to_the_day_that_begins_then() -> None:
    """The boundary is inclusive on the lower side, so midnight belongs to the day it starts --
    the calendar's own convention, and the only one under which two adjacent days can neither
    both claim a cycle nor both disown it."""
    midnight = _local_midnight(_SCOPE_NOW)
    lines = [
        *_cycle_at(midnight - 1, "one-second-before"),
        *_cycle_at(midnight, "exactly-midnight"),
    ]

    today = _scoped(lines, "today", _SCOPE_NOW)
    assert [c.cycle_id for c in today.cycles] == ["exactly-midnight"]

    # ...and the excluded one is not lost, it is the previous day's newest.
    assert today.last_cycle_before_scope is not None
    assert today.last_cycle_before_scope.cycle_id == "one-second-before"

    # Seen from the following day, the same midnight cycle is out of scope again -- the boundary
    # moves with the day rather than the cycle being permanently "today's".
    tomorrow = _scoped(lines, "today", _SCOPE_NOW + 86400)
    assert tomorrow.cycles == ()
    assert tomorrow.last_cycle_before_scope is not None
    assert tomorrow.last_cycle_before_scope.cycle_id == "exactly-midnight"


def test_apply_scope_leaves_a_feed_alone_under_all() -> None:
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=30) + 9 * 3600, "ancient"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"),
    ]
    unscoped = feed_from_lines(lines, source="/tmp/keel.log")

    feed = apply_scope(unscoped, "all", now_ts=_SCOPE_NOW)

    assert feed.cycles == unscoped.cycles
    assert feed.cycles_out_of_scope == 0
    assert feed.last_cycle_before_scope is None
    assert feed.scope_start_ts is None


def test_apply_scope_does_not_read_the_clock_when_now_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The injection is the point: no test in this file may depend on the day it runs on."""

    def _boom() -> float:  # pragma: no cover - being called IS the failure
        raise AssertionError("apply_scope read the wall clock instead of using now_ts")

    monkeypatch.setattr(time, "time", _boom)

    feed = _scoped(_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "t"), "today", _SCOPE_NOW)

    assert len(feed.cycles) == 1


def test_apply_scope_on_an_unrecognised_scope_shows_today_rather_than_nothing() -> None:
    feed = _scoped(_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "t"), "since tuesday",
                   _SCOPE_NOW)

    assert feed.scope == "today"
    assert len(feed.cycles) == 1


# -- the empty state: the line that answers "is keel alive" ----------------------------------------


def test_empty_today_names_the_last_run_and_when_the_next_one_is_due() -> None:
    """The most important wording in this change. Before 09:00 the panel is legitimately empty,
    and a blank panel is indistinguishable from a dead agent -- so it must say, in plain words,
    that keel has not run YET, when it last ran, and when the next run is expected."""
    lines = _cycle_at(_local_midnight(_SCOPE_NOW_EARLY, days_ago=1) + 9 * 3600, "yesterday")

    feed = _scoped(lines, "today", _SCOPE_NOW_EARLY)
    said = describe_empty_scope(feed)
    text = " ".join(said)

    assert said  # never blank
    assert said[0] == "keel has not run yet today."
    assert "Last cycle:" in text
    assert "yesterday" in text
    # The last run's actual stamp, not a vague "recently".
    assert feed.last_cycle_before_scope is not None
    assert _stamp(feed.last_cycle_before_scope.started_ts) in text
    # ...and the forward-looking half: 09:00 is still two hours away at 07:00.
    assert "Next cycle due today around 09:00 local" in text
    assert "in 2h 00m" in text
    assert "Press t to widen the scope" in text


def test_empty_today_after_the_usual_time_says_the_run_is_overdue() -> None:
    """Different news, so it reads differently: at 14:00 with nothing since yesterday 09:00, the
    schedule has been missed and the operator should be told to go and look."""
    lines = _cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "yesterday")

    text = " ".join(describe_empty_scope(_scoped(lines, "today", _SCOPE_NOW)))

    assert "keel has not run yet today." in text
    assert "Its usual start time today (09:00 local) passed 5h 00m ago." in text
    assert "check that the agent's schedule is still running" in text


def test_empty_today_with_no_history_at_all_still_explains_itself() -> None:
    """A brand-new deployment: no cycle today, and none before it either. There is no last run to
    name, so it says so rather than leaving the operator to infer it from silence."""
    feed = ActivityFeed(
        status="ok",
        source="/tmp/keel.log",
        scope="today",
        scope_start_ts=_local_midnight(_SCOPE_NOW),
        now_ts=_SCOPE_NOW,
    )

    said = describe_empty_scope(feed)
    text = " ".join(said)

    assert said
    assert "keel has not run today" in text
    assert "no earlier" in text
    assert "logging.verbose" in text
    assert "Press t to widen the scope" in text


def test_an_entirely_empty_log_is_still_an_empty_log_under_the_today_default() -> None:
    """Scoping must not swallow the FILE-level statuses: an empty log still reports `empty`, with
    the same `logging.verbose` advice it always had, not "keel has not run yet today"."""
    feed = apply_scope(feed_from_lines([]), "today", now_ts=_SCOPE_NOW)

    assert feed.status == "empty"
    assert feed.cycles == ()
    assert "verbose" in " ".join(describe_status(feed))


@pytest.mark.parametrize("scope", ["today", "7d", "all"])
def test_the_empty_state_is_never_blank_for_any_scope(scope: str) -> None:
    """The invariant the whole design rests on -- there is no combination of scope and history
    that renders zero lines."""
    feed = apply_scope(feed_from_lines([], source="/tmp/keel.log"), scope, now_ts=_SCOPE_NOW)

    said = describe_empty_scope(feed)

    assert said
    assert any(line.strip() for line in said)


def test_describe_empty_scope_under_all_keeps_the_original_no_cycles_wording() -> None:
    """`all` is not a day filter, so the answer there is about the LOG holding nothing groupable
    -- the pre-scoping wording, unchanged."""
    feed = apply_scope(
        ActivityFeed(status="ok", source="/tmp/keel.log"), "all", now_ts=_SCOPE_NOW
    )

    assert "No cycles in the window" in describe_empty_scope(feed)[0]


def test_empty_7d_does_not_claim_to_know_when_the_next_run_is_due() -> None:
    """The next-run estimate is inferred from a DAILY cadence and only makes sense for today."""
    lines = _cycle_at(_local_midnight(_SCOPE_NOW, days_ago=30) + 9 * 3600, "ancient")

    text = " ".join(describe_empty_scope(_scoped(lines, "7d", _SCOPE_NOW)))

    assert "keel has not run in the last 7 days." in text
    assert "Last cycle:" in text
    assert "due today" not in text


# -- coverage: the bounded window versus the day boundary ------------------------------------------


def test_coverage_is_proven_when_the_window_reaches_past_the_boundary() -> None:
    """A window that contains a cycle from BEFORE midnight has demonstrably seen the whole day,
    truncated or not."""
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "yesterday"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"),
    ]

    feed = _scoped(lines, "today", _SCOPE_NOW, truncated=True)

    assert feed.scope_fully_covered
    assert not any("COVERAGE UNPROVEN" in note for note in footer_notes(feed))


def test_coverage_is_proven_when_the_whole_file_was_read() -> None:
    lines = _cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today")

    feed = _scoped(lines, "today", _SCOPE_NOW, truncated=False)

    assert feed.scope_fully_covered


def test_coverage_is_unproven_when_the_bounded_window_begins_inside_today() -> None:
    """The interaction the read bounds create: a truncated window whose OLDEST record is already
    today cannot show that nothing happened earlier today -- only that it did not see it."""
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 11 * 3600, "today-a"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 13 * 3600, "today-b"),
    ]

    feed = _scoped(lines, "today", _SCOPE_NOW, truncated=True)

    assert not feed.scope_fully_covered
    notes = footer_notes(feed)
    assert any("COVERAGE UNPROVEN" in note for note in notes)
    assert any("NOT evidence that today was quiet" in note for note in notes)


def test_an_empty_today_in_an_unproven_window_says_so_instead_of_implying_a_quiet_day() -> None:
    """The empty state cannot say "nothing happened today" when the read never reached midnight.
    Constructed directly, because a window that is both truncated and empty of today's cycles is
    exactly the combination a fixture cannot produce by writing a small file."""
    feed = ActivityFeed(
        status="ok",
        source="/tmp/keel.log",
        scope="today",
        scope_start_ts=_local_midnight(_SCOPE_NOW),
        now_ts=_SCOPE_NOW,
        window_truncated=True,
        scope_fully_covered=False,
    )

    text = " ".join(describe_empty_scope(feed))

    assert "CAVEAT" in text
    assert "cannot show that nothing happened" in text


def test_all_scope_never_reports_coverage_doubt() -> None:
    """There is no boundary to fall short of, so the caveat would be meaningless noise."""
    feed = _scoped(
        _cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"), "all", _SCOPE_NOW,
        truncated=True,
    )

    assert not any("COVERAGE UNPROVEN" in note for note in footer_notes(feed))


def test_footer_reports_how_much_the_scope_hid() -> None:
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=2) + 9 * 3600, "d2"),
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "d1"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"),
    ]

    notes = " ".join(footer_notes(_scoped(lines, "today", _SCOPE_NOW)))

    assert "2 older cycle(s) in the window are hidden" in notes
    assert "press t to widen" in notes


# -- the header line -------------------------------------------------------------------------------


def test_scope_headline_names_the_day_the_count_the_hidden_and_the_key() -> None:
    lines = [
        *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "d1"),
        *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"),
    ]

    headline = scope_headline(_scoped(lines, "today", _SCOPE_NOW))

    assert "scope: today (" in headline
    assert time.strftime("%Y-%m-%d", time.localtime(_SCOPE_NOW)) in headline
    assert "1 cycle" in headline
    assert "1 older hidden" in headline
    assert "press t to widen" in headline
    # It has to survive an 80-column terminal to be worth writing.
    assert len(headline) <= 80


@pytest.mark.parametrize(
    ("scope", "fragment"),
    [("today", "today ("), ("7d", "last 7 days (from "), ("all", "all history in the window")],
)
def test_scope_headline_names_every_scope(scope: str, fragment: str) -> None:
    feed = _scoped(_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "t"), scope, _SCOPE_NOW)

    assert fragment in scope_headline(feed)


# -- build_activity_feed: the default really is today ----------------------------------------------


def test_build_activity_feed_defaults_to_today(tmp_path: Any) -> None:
    """The requirement, end to end from a real file: only today's cycle comes back, and the one
    from yesterday is hidden rather than dropped."""
    path = tmp_path / "keel.log"
    path.write_text(
        "\n".join(
            [
                *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "yesterday"),
                *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"),
            ]
        )
        + "\n"
    )

    feed = build_activity_feed(_FakeConfig(str(path)), now_ts=_SCOPE_NOW)

    assert feed.scope == "today"
    assert [c.cycle_id for c in feed.cycles] == ["today"]
    assert feed.cycles_out_of_scope == 1
    assert feed.last_cycle_before_scope is not None
    assert feed.last_cycle_before_scope.cycle_id == "yesterday"


def test_build_activity_feed_widens_on_request(tmp_path: Any) -> None:
    path = tmp_path / "keel.log"
    path.write_text(
        "\n".join(
            [
                *_cycle_at(_local_midnight(_SCOPE_NOW, days_ago=1) + 9 * 3600, "yesterday"),
                *_cycle_at(_local_midnight(_SCOPE_NOW) + 9 * 3600, "today"),
            ]
        )
        + "\n"
    )

    feed = build_activity_feed(_FakeConfig(str(path)), scope="all", now_ts=_SCOPE_NOW)

    assert [c.cycle_id for c in feed.cycles] == ["today", "yesterday"]
    assert feed.cycles_out_of_scope == 0


def test_build_activity_feed_stamps_the_scope_even_on_a_missing_log(tmp_path: Any) -> None:
    """So the overlay's header reads identically whether or not the file was there."""
    feed = build_activity_feed(_FakeConfig(str(tmp_path / "gone.log")), now_ts=_SCOPE_NOW)

    assert feed.status == "missing"
    assert feed.scope == "today"
    assert describe_status(feed)  # the file-level explanation is untouched by scoping


def test_today_has_no_upper_bound_so_a_clock_skewed_row_is_shown_not_hidden() -> None:
    """A record stamped later today than "now" can only come from a writer whose clock is ahead.
    This module shows it -- with its odd timestamp visible -- rather than shrinking the panel and
    giving the operator nothing to go on. Pinned as a test because it is a decision, not an
    accident: "today" is the calendar day, and `now` is only ever its lower-bound anchor."""
    lines = _cycle_at(_local_midnight(_SCOPE_NOW) + 20 * 3600, "later-today")  # 20:00 vs now 14:00

    feed = _scoped(lines, "today", _SCOPE_NOW)

    assert [c.cycle_id for c in feed.cycles] == ["later-today"]
    assert feed.cycles_out_of_scope == 0
