"""The time-axised account-equity chart over `equity_points` -- issue #698.

This is NOT a replacement for `build_equity_curve`. That one plots cumulative net P&L over
CLOSED TRADES on a trade-order axis, and its docstring argues for that axis on its own terms: a
quiet week must not get the visual weight of fifty trades when the subject is a track record.
This builder plots a different quantity -- what the ACCOUNT was worth, every cycle, whether it
traded or not -- and for that quantity time is the only honest axis, because the gaps are the
information.

The properties pinned here are the ones that would otherwise let the chart tell a lie the data
does not support: a flip between two accounts drawn as one line, a gap drawn as a straight
segment, and a rail ceiling recomputed rather than read.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core.types import EquityReading

from keel.commands.insights import PLOT_HEIGHT, PLOT_WIDTH, build_equity_series

NOW = 1_800_000_000
DAY = 86_400


def _reading(
    ts: int,
    equity: str,
    *,
    mode: str = "live",
    hwm: str | None = None,
    cash: str | None = None,
    unrealized: str | None = None,
) -> EquityReading:
    return EquityReading(
        ts=ts,
        mode=mode,
        equity=Decimal(equity),
        cash=None if cash is None else Decimal(cash),
        unrealized=None if unrealized is None else Decimal(unrealized),
        hwm=Decimal(hwm if hwm is not None else equity),
    )


def test_no_readings_is_a_real_answer_not_an_empty_chart() -> None:
    """A deployment that has not run a cycle since v19 has no series. Distinct from a flat
    line, which is what an account that did not move looks like."""
    series = build_equity_series([])
    assert series.segments == []
    assert series.point_count == 0


def test_the_x_axis_is_time_so_a_quiet_stretch_leaves_a_gap() -> None:
    """The whole reason this chart exists next to the trade-order one: equity is a property of
    the calendar. Three cycles, the last of them a week after the second, must NOT be evenly
    spaced -- even spacing would draw a week of silence as one ordinary step."""
    series = build_equity_series(
        [
            _reading(NOW, "10000"),
            _reading(NOW + DAY, "10100"),
            _reading(NOW + 8 * DAY, "10200"),
        ]
    )
    xs = [point.x for point in series.segments[0].points]
    assert xs[0] == Decimal("0")
    assert xs[-1] == PLOT_WIDTH
    # 1 day into an 8-day span is one eighth across, not one half.
    assert xs[1] == (PLOT_WIDTH / 8).quantize(Decimal("0.01"))


def test_a_mode_flip_produces_two_segments_not_one_blended_curve() -> None:
    """The acceptance criterion this chart is judged on. $10k of paper and $250 of live are two
    unrelated accounts; joined, the flip draws a 97.5% collapse that never happened."""
    series = build_equity_series(
        [
            _reading(NOW, "10000", mode="paper"),
            _reading(NOW + DAY, "10100", mode="paper"),
            _reading(NOW + 2 * DAY, "250", mode="live"),
            _reading(NOW + 3 * DAY, "260", mode="live"),
        ]
    )
    assert [segment.mode for segment in series.segments] == ["paper", "live"]
    assert [len(segment.points) for segment in series.segments] == [2, 2]


def test_a_mode_that_resumes_after_a_flip_is_a_third_segment() -> None:
    """Segments follow the ORDER of the readings, not the set of modes: paper, live, paper is
    three runs. Grouping by mode alone would join the two paper stretches across the live one
    and draw a line through time the account did not spend in paper."""
    series = build_equity_series(
        [
            _reading(NOW, "10000", mode="paper"),
            _reading(NOW + DAY, "250", mode="live"),
            _reading(NOW + 2 * DAY, "10100", mode="paper"),
        ]
    )
    assert [segment.mode for segment in series.segments] == ["paper", "live", "paper"]


def test_the_high_water_mark_overlay_is_read_from_the_rows_not_recomputed() -> None:
    """`record_external_flow` REBASES the HWM on a declared deposit, so it is not the running
    maximum of the equity series. Recomputing it here would draw a ceiling rail 11 never used --
    and the point of the overlay is to show the ceiling that was actually in force."""
    readings = [
        _reading(NOW, "10000", hwm="10000"),
        _reading(NOW + DAY, "9000", hwm="10000"),
        # A $5k deposit: equity jumps and the operator declares the flow, so the HWM is rebased
        # UP rather than the deposit reading as a recovery. A running maximum would say 14000.
        _reading(NOW + 2 * DAY, "14000", hwm="15000"),
    ]
    series = build_equity_series(readings)
    assert [point.hwm for point in series.segments[0].points] == [
        Decimal("10000"),
        Decimal("10000"),
        Decimal("15000"),
    ]


def test_the_drawdown_ceiling_is_drawn_beneath_each_points_own_high_water_mark() -> None:
    """Rail 11 vetoes entries at `drawdown >= max_total_dd_pct` measured from the HWM, so the
    ceiling is a function of the HWM in force at that instant -- it MOVES with the rebase, and a
    single horizontal line would be wrong from the first deposit onwards."""
    series = build_equity_series(
        [_reading(NOW, "10000", hwm="10000"), _reading(NOW + DAY, "14000", hwm="15000")],
        max_total_dd_pct=Decimal("0.20"),
    )
    assert [point.dd_floor for point in series.segments[0].points] == [
        Decimal("8000"),
        Decimal("12000"),
    ]


def test_without_a_ceiling_there_is_no_floor_line_rather_than_a_zero_one() -> None:
    """A caller that did not supply the rail's setting has not told us the ceiling is zero."""
    series = build_equity_series([_reading(NOW, "10000")])
    assert series.segments[0].points[0].dd_floor is None


def test_the_axis_bounds_contain_the_overlays_not_only_the_equity() -> None:
    """The ceiling is drawn on this canvas, so it has to fit on it. Bounds taken from the equity
    alone would push a floor line off the bottom of the box, where it reads as absent -- the one
    thing a rail's ceiling must never look like."""
    series = build_equity_series(
        [_reading(NOW, "10000", hwm="12000"), _reading(NOW + DAY, "10500", hwm="12000")],
        max_total_dd_pct=Decimal("0.20"),
    )
    assert series.low == Decimal("9600")  # the floor, below every equity reading
    assert series.high == Decimal("12000")  # the HWM, above every equity reading


def test_zero_is_not_forced_into_the_equity_axis() -> None:
    """`build_equity_curve` puts zero on its canvas unconditionally, because it plots net P&L
    and zero is the line between making and losing money. Account equity has no such line: an
    account is not "up" or "down" against nothing. Forcing zero in would squash every real move
    into the top sliver of a box that is mostly empty space below the account."""
    series = build_equity_series([_reading(NOW, "10000"), _reading(NOW + DAY, "10500")])
    assert series.low == Decimal("10000")
    assert series.high == Decimal("10500")


def test_a_flat_account_is_drawn_on_a_line_not_at_the_edge_of_the_box() -> None:
    """Zero span has no range to normalise against. Drawn mid-box: the account did not move,
    and pinning it to the top or the bottom would imply it sat at an extreme of something."""
    series = build_equity_series([_reading(NOW, "10000"), _reading(NOW + DAY, "10000")])
    ys = [point.y for point in series.segments[0].points]
    assert ys == [(PLOT_HEIGHT / 2).quantize(Decimal("0.01"))] * 2


def test_a_single_reading_sits_at_the_start_of_the_time_axis() -> None:
    """One cycle is a real state (the first after upgrading). A zero-width time span must not
    divide by zero, and the point belongs at the left edge: it is the beginning of the record,
    not the whole of it."""
    series = build_equity_series([_reading(NOW, "10000")])
    point = series.segments[0].points[0]
    assert point.x == Decimal("0")
    assert series.point_count == 1


def test_y_grows_downward_like_svgs_does() -> None:
    """The same convention `EquityPoint` documents: the browser is not allowed to do the one
    subtraction that would flip it."""
    series = build_equity_series([_reading(NOW, "10000"), _reading(NOW + DAY, "20000")])
    low_point, high_point = series.segments[0].points
    assert low_point.y > high_point.y
