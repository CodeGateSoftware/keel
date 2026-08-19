from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.results import FeeSummary


def _summary(window: str) -> FeeSummary:
    return FeeSummary(
        venue="coinbase",
        taker_rate=Decimal("0.012"),
        maker_rate=Decimal("0.006"),
        volume_usd=Decimal("1234.56"),
        fees_usd=Decimal("0"),
        volume_window=window,
        fetched_at=1_700_000_000,
    )


@pytest.mark.parametrize("window", ["trailing_30d", "calendar_month", "unknown"])
def test_accepts_every_legal_window(window: str) -> None:
    assert _summary(window).volume_window == window


def test_rejects_an_unknown_window() -> None:
    """An undeclarable window would let the engine compare mismatched periods silently."""
    with pytest.raises(ValueError, match="volume_window must be one of"):
        _summary("monthly")


def test_unknown_is_a_legal_declaration_not_an_error() -> None:
    """Coinbase's window is undocumented; "unknown" must be sayable, not a failure."""
    assert _summary("unknown").volume_window == "unknown"


# -- MarketSchedule (issue #388 C2: the banner's schedule read) ----------------------------------


def test_market_schedule_defaults_to_no_times() -> None:
    """The schedule value object: the session state is the answer; next open/close are EXTRA
    facts a venue provides or does not, so they default to `None` rather than to a guess a
    renderer could mistake for a real timestamp."""
    from keel_broker_api.results import MarketSchedule, SessionState

    schedule = MarketSchedule(state=SessionState.OPEN)
    assert schedule.state is SessionState.OPEN
    assert schedule.next_open_ts is None
    assert schedule.next_close_ts is None


def test_market_schedule_carries_the_timestamps_it_was_given() -> None:
    from keel_broker_api.results import MarketSchedule, SessionState

    schedule = MarketSchedule(
        state=SessionState.CLOSED, next_open_ts=1_787_059_800, next_close_ts=1_786_996_800
    )
    assert schedule.next_open_ts == 1_787_059_800
    assert schedule.next_close_ts == 1_786_996_800


def test_market_schedule_default_derives_from_the_clock_answer() -> None:
    """The port's DEFAULT `market_schedule()` -- `default_market_schedule(broker)` -- is derived
    from the broker's own `market_clock()` answer: the state crosses unchanged and NO
    next_open/next_close is claimed. That is the whole implementation the 24/7 adapters ship
    (their clock is the constant `OPEN`), and the honest fallback for any session-bound adapter
    that has not overridden the schedule read: it still answers the state, and claims no
    schedule it cannot vouch for."""
    from keel_broker_api.port import default_market_schedule
    from keel_broker_api.results import MarketSchedule, SessionState

    class _ClockOnlyBroker:
        def market_clock(self) -> SessionState:
            return SessionState.OPEN

    schedule = default_market_schedule(_ClockOnlyBroker())  # type: ignore[arg-type]
    assert schedule == MarketSchedule(state=SessionState.OPEN)
    assert schedule.next_open_ts is None and schedule.next_close_ts is None
