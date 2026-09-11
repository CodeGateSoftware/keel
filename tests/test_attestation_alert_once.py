"""One alert per attestation per window (#793).

`attestation.expiring` has fired since #444 and it fires on EVERY cycle in which doctor's
finding is WARN or FAIL. On the live deployment on 2026-09-11 that is both rails at once, every
cycle, for as long as they stay lapsed -- rail 17 expired on 09-08 and rail 22 has never been
attested at all. An alert that repeats until it is fixed is an alert nobody reads by the third
day, which is the failure mode this issue is named after.

So the event is suppressed once it has been reported for the window it is in. A **window** is
the attestation's state paired with its expiry, so:

* entering the final 48 hours reports once, not once per cycle;
* actually expiring reports again -- it is a different state and a different sentence ("entries
  are halted" is not "entries will be"), and an operator who missed the warning gets the halt;
* renewing and lapsing again reports again, because the new attestation has a new expiry;
* a never-attested rail reports once and then holds its tongue, which is the only shape that
  works for rail 22 -- it has no expiry to cross and would otherwise alert forever.

The ledger is a repo key, and writing it is a departure from `notify_after_cycle`'s documented
"never writes". `notifications.py` carries the reasoning; the tests here pin the behaviour.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from keel import attestations, notifications
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW = 1_800_000_000
DAY = 86_400
HOUR = 3_600
LEDGER = "notified_attestation_windows"


@pytest.fixture
def repo(tmp_path: Any) -> Repository:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


def _events(*names: str) -> list[Any]:
    from keel_core.notifications import notification_event

    return [
        notification_event("attestation.expiring", "rail: lapsed", finding=name, status="warn")
        for name in names
    ]


def _windows(repo: Repository, now_ts: int) -> dict[str, str]:
    return notifications.attestation_windows(attestations.survey(repo, now_ts), now_ts)


# -- the window id ---------------------------------------------------------------------------


def test_a_window_is_the_state_and_the_expiry_together(repo: Repository) -> None:
    """Either half alone is wrong. The state alone never changes while a rail stays expired, so
    a renewal that lapses again would be silent; the expiry alone does not move when EXPIRING
    becomes EXPIRED, so the halt itself would be."""
    repo.set_state("withdrawals_attested_at", str(NOW - 6 * DAY))
    repo.set_state("withdrawals_enabled", "true")

    expiring = _windows(repo, NOW)["attest.withdrawals"]
    expired = _windows(repo, NOW + 2 * DAY)["attest.withdrawals"]
    assert expiring != expired

    repo.set_state("withdrawals_attested_at", str(NOW + 2 * DAY))
    renewed = _windows(repo, NOW + 8 * DAY)["attest.withdrawals"]
    assert renewed not in (expiring, expired)


def test_the_window_holds_still_across_the_cycles_inside_it(repo: Repository) -> None:
    """The property the whole feature rests on: an hourly wrapper inside the final 48 hours must
    compute the SAME id every time, or suppression suppresses nothing."""
    repo.set_state("withdrawals_attested_at", str(NOW - 6 * DAY))
    repo.set_state("withdrawals_enabled", "true")
    ids = {_windows(repo, NOW + n * HOUR)["attest.withdrawals"] for n in range(24)}
    assert len(ids) == 1


def test_every_finding_that_can_fire_the_event_has_a_window(repo: Repository) -> None:
    """Rejects a third rail joining `_ATTESTATION_FINDINGS` without joining the model: its events
    would have no window, and the suppression below would have to choose between dropping them
    forever and repeating them forever."""
    assert set(_windows(repo, NOW)) == notifications._ATTESTATION_FINDINGS


# -- the suppression -------------------------------------------------------------------------


def test_the_first_report_of_a_window_goes_out() -> None:
    windows = {"attest.withdrawals": "expiring:100"}
    assert notifications.unreported(_events("attest.withdrawals"), windows=windows, already={})


def test_the_second_report_of_the_same_window_does_not() -> None:
    windows = {"attest.withdrawals": "expiring:100"}
    already = {"attest.withdrawals": "expiring:100"}
    assert (
        notifications.unreported(_events("attest.withdrawals"), windows=windows, already=already)
        == []
    )


def test_a_new_window_reports_again_over_a_stale_ledger_entry() -> None:
    windows = {"attest.withdrawals": "expired:100"}
    already = {"attest.withdrawals": "expiring:100"}
    assert (
        len(
            notifications.unreported(
                _events("attest.withdrawals"), windows=windows, already=already
            )
        )
        == 1
    )


def test_suppressing_one_rail_does_not_suppress_the_other() -> None:
    """The #732 bug one layer down: rail 17 reported and rail 22 silenced by it."""
    windows = {"attest.withdrawals": "expired:100", "attest.cash_posture": "missing:None"}
    already = {"attest.withdrawals": "expired:100"}
    sent = notifications.unreported(
        _events("attest.withdrawals", "attest.cash_posture"), windows=windows, already=already
    )
    assert [event.fields["finding"] for event in sent] == ["attest.cash_posture"]


@pytest.mark.parametrize("already", [{}, {"rail.streak_halt": "x"}])
def test_nothing_else_is_ever_suppressed(already: dict[str, str]) -> None:
    """`rail.armed`, `allowance.nearing_exhaustion` and the staleness events are per-cycle facts
    with no window, and a ledger miss must never swallow one.

    **The empty ledger is the case that matters**, and the first version of this test did not
    have it. Drop the `window is not None` guard and `already.get(name)` and `windows.get(name)`
    are both `None` for every non-attestation event -- `None == None` -- so EVERY armed rail,
    exhausted allowance and stale position goes silent, and the populated-ledger case above sails
    through it because `"x" != None`."""
    from keel_core.notifications import notification_event

    other = [notification_event("rail.armed", "streak halt", finding="rail.streak_halt")]
    assert notifications.unreported(other, windows={}, already=already) == other


def test_an_event_carrying_no_finding_at_all_is_never_suppressed() -> None:
    """Staleness and allowance events carry no `finding` field. `windows.get("")` must not be a
    hit -- and it cannot be, because `attestation_windows` is keyed by real finding names."""
    from keel_core.notifications import notification_event

    bare = [notification_event("feed.stale_open_position", "BTC-USD is stale")]
    assert notifications.unreported(bare, windows={"": "x"}, already={"": "x"}) == bare


# -- the ledger ------------------------------------------------------------------------------


def test_a_delivered_report_is_recorded_and_an_undelivered_one_is_not(repo: Repository) -> None:
    """A webhook that was down must not cost the only alert for this window."""
    notifications.record_reported(repo, {"attest.withdrawals": "expiring:100"})
    assert json.loads(str(repo.get_state(LEDGER))) == {"attest.withdrawals": "expiring:100"}

    notifications.record_reported(repo, {})
    assert json.loads(str(repo.get_state(LEDGER))) == {"attest.withdrawals": "expiring:100"}


def test_an_unreadable_ledger_reports_rather_than_staying_silent(repo: Repository) -> None:
    """Fail OPEN. A corrupt key is a reason to send a duplicate alert, never a reason to
    withhold the one that says live is halted."""
    repo.set_state(LEDGER, "{not json")
    assert notifications.reported_windows(repo) == {}


# -- end to end ------------------------------------------------------------------------------


def _config() -> Any:
    from keel_core.notifications import NotificationSettings

    from tests.test_notifications import _config_with

    return _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))


def _rail17(calls: list[Any]) -> int:
    """Deliveries mentioning rail 17.

    Counted per RAIL rather than per cycle because a fresh deployment has attested no cash
    posture either, so rail 22 alerts alongside on the first cycle -- which is right, and is the
    live deployment's own state on 2026-09-11, but it is not what these tests are about.
    """
    return len([call for call in calls if "rail 17" in json.dumps(call)])


def _cycle(repo: Repository, calls: list[Any], now_ts: int) -> int:
    from tests.test_notifications import _LoopResult

    def _transport(url: str, body: bytes) -> None:
        calls.append(json.loads(body.decode("utf-8")))

    return notifications.notify_after_cycle(
        repo,
        _config(),
        _LoopResult(),
        now_ts,
        url="https://alerts.example/hook",
        transport=_transport,
    )


def test_a_lapsed_rail_alerts_on_the_first_cycle_and_not_on_the_next(repo: Repository) -> None:
    """The whole point. The wrapper fires hourly; without this the operator gets the same two
    sentences 24 times a day for as long as the rail stays lapsed."""
    repo.set_state("withdrawals_attested_at", str(NOW - 6 * DAY))
    repo.set_state("withdrawals_enabled", True)
    calls: list[Any] = []

    for hour in range(3):
        _cycle(repo, calls, NOW + hour * HOUR)

    assert _rail17(calls) == 1


def test_the_halt_itself_alerts_even_though_the_warning_already_did(repo: Repository) -> None:
    """ "Entries are halted" is not "entries will be", and an operator who missed the warning has
    exactly one more chance to hear about it."""
    repo.set_state("withdrawals_attested_at", str(NOW - 6 * DAY))
    repo.set_state("withdrawals_enabled", True)
    calls: list[Any] = []

    _cycle(repo, calls, NOW)
    assert _rail17(calls) == 1
    _cycle(repo, calls, NOW + 2 * DAY)
    assert _rail17(calls) == 2
    # And rail 22, whose own window never moved, stays quiet through all of it -- INCLUDING the
    # cycle after rail 17's second alert, which is the one that matters. Rejects a
    # `record_reported` that REPLACES the ledger instead of merging into it: rail 17's second
    # alert evicts rail 22's entry, and the eviction is invisible until the next cycle reads a
    # ledger that has forgotten rail 22. Measured: without this third cycle the mutant survives.
    _cycle(repo, calls, NOW + 2 * DAY + HOUR)
    assert len([call for call in calls if "rail 22" in json.dumps(call)]) == 1


def test_renewing_and_lapsing_again_alerts_again(repo: Repository) -> None:
    """Rejects a ledger keyed on the finding name alone: one alert, ever, per deployment."""
    repo.set_state("withdrawals_attested_at", str(NOW - 6 * DAY))
    repo.set_state("withdrawals_enabled", True)
    calls: list[Any] = []
    _cycle(repo, calls, NOW)
    assert _rail17(calls) == 1

    repo.set_state("withdrawals_attested_at", str(NOW + DAY))
    _cycle(repo, calls, NOW + DAY)
    assert _rail17(calls) == 1, "renewed: nothing to say"
    _cycle(repo, calls, NOW + 6 * DAY)
    assert _rail17(calls) == 2, "lapsing again is news again"


def test_an_undelivered_alert_is_retried_on_the_next_cycle(repo: Repository) -> None:
    """A webhook that was down must not cost the only alert for this window."""
    repo.set_state("withdrawals_attested_at", str(NOW - 6 * DAY))
    repo.set_state("withdrawals_enabled", True)

    def _dead(url: str, body: bytes) -> None:
        raise OSError("connection refused")

    from tests.test_notifications import _LoopResult

    assert (
        notifications.notify_after_cycle(
            repo, _config(), _LoopResult(), NOW, url="https://x/y", transport=_dead
        )
        == 0
    )
    calls: list[Any] = []
    _cycle(repo, calls, NOW + HOUR)
    assert _rail17(calls) == 1
