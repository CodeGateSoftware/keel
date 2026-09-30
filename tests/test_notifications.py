"""Tests for deriving notification events from the state doctor already computes (#444).

`keel.notifications.events_from_state` is a PURE function: it takes doctor's own finding
lists (`keel.commands.doctor.attestation_findings` / `rail_state_findings` outputs), the
allowance numbers doctor's `allowance_findings` receives, and the cycle facts the agent loop
already records (unplaced setups, stale products, held products) -- and derives the five
issue-named events. Reusing doctor's computations is the point: a notification layer that
re-implemented the TTL math would drift from the surface the operator diagnoses with, and
the drift would surface as an alert doctor says is fine.

`notify_after_cycle` is the wiring: it reads the same repo keys doctor's `gather_findings`
reads, derives the events, and hands them to `keel_core.notifications.send_event`. It never
raises and never trades -- notify-only, per #444's scope.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

import pytest
from keel_core.notifications import EVENTS_BY_KEY, WARN, NotificationSettings, send_event

from keel import notifications
from keel.commands.doctor import attestation_findings, rail_state_findings
from keel.config import AutoTradeConfig, Caps, Config, MarketDataConfig
from keel.execution.executor import ReduceResult
from keel.execution.sleeve_exit import ExitWatch
from keel.notifications import (
    _ATTESTATION_FINDINGS,
    ALLOWANCE_NEARING_USED_PCT,
    UnplacedSetup,
    events_from_state,
    notify_after_cycle,
)

DAY = 86_400
NOW = 1_800_000_000  # a fixed, realistic epoch: no fixture clock drift


# -- builders ---------------------------------------------------------------------------------


def _attestation(withdrawals_attested_at: int, now: int = NOW, *, enabled: bool | None = True):
    """doctor's own computation, fed a `None` subscription (rail 14 is not what these events
    are about) and the withdrawal-attestation freshness under test.

    `enabled=True` explicitly. `attestation_findings` used to default it to `True` and #794
    changed that to `None` -- "nobody has checked" -- because a defaulted `True` on a
    safety-reporting function reports `ok` for an account whose broker has frozen withdrawals.
    These tests are about the CLOCK, so they state the verdict rather than inherit it.
    """
    return attestation_findings(
        subscription=None,
        withdrawals_attested_at=withdrawals_attested_at,
        withdrawals_enabled=enabled,
        now_ts=now,
    )


def _rails(
    *,
    kill_switch: bool = False,
    streak_halt_until: int = 0,
    drawdown_total: Decimal = Decimal("0"),
    now: int = NOW,
):
    return rail_state_findings(
        kill_switch=kill_switch,
        streak_halt_until=streak_halt_until,
        drawdown_total=drawdown_total,
        now_ts=now,
    )


def _state(
    *,
    attestation=None,
    rails=None,
    month_to_date_spend: Decimal | None = None,
    allowance: Decimal | None = None,
    unplaced: tuple[UnplacedSetup, ...] = (),
    stale: tuple[str, ...] = (),
    held: tuple[str, ...] = (),
    sleeve: tuple[ReduceResult, ...] = (),
    sleeve_only: tuple[str, ...] = (),
    watch: tuple[ExitWatch, ...] = (),
):
    return events_from_state(
        attestation_findings=attestation if attestation is not None else _attestation(NOW),
        rail_findings=rails if rails is not None else _rails(),
        month_to_date_spend=month_to_date_spend,
        allowance=allowance,
        unplaced_setups=unplaced,
        stale_products=stale,
        held_products=held,
        sleeve_proposals=sleeve,
        sleeve_only_products=sleeve_only,
        exit_watch_transitions=watch,
    )


# -- rail 17: attestation nearing expiry ------------------------------------------------------


def test_an_attestation_due_within_two_days_fires_and_a_fresh_one_does_not():
    """The issue's sharpest silent failure: rail 17 has a 7-day TTL, fails closed, and the
    veto is a WARNING -- not a CRITICAL. doctor WARNs at <=2 days remaining; the notification
    fires on exactly that threshold, from exactly that computation."""
    due_soon = _state(attestation=_attestation(NOW - 5 * DAY))  # 2 of 7 days remain
    assert [e.key for e in due_soon] == ["attestation.expiring"]
    assert "2 day(s)" in due_soon[0].message  # doctor's own detail, numbers included
    assert due_soon[0].fields["status"] == "warn"

    fresh = _state(attestation=_attestation(NOW))  # 7 days remain
    assert fresh == []


def test_an_expired_attestation_fires_too():
    """Expired is the sharper case the WARN threshold exists to precede: rail 17 is already
    vetoing every entry at that point."""
    expired = _state(attestation=_attestation(NOW - 8 * DAY))

    assert [e.key for e in expired] == ["attestation.expiring"]


# -- rails arming -----------------------------------------------------------------------------


def test_armed_rails_fire_and_healthy_rails_do_not():
    """A rail ARMING (streak halt, drawdown breaker) is an operator-wants-to-know-today
    event that is not an error log. Derived from doctor's rail findings, so the thresholds
    are doctor's: `streak_halt_until > now`, `drawdown_total >= 20%`."""
    halted = _state(rails=_rails(streak_halt_until=NOW + DAY))
    assert [e.key for e in halted] == ["rail.armed"]
    assert "consecutive-loss halt" in halted[0].message

    broken = _state(rails=_rails(drawdown_total=Decimal("20")))
    assert [e.key for e in broken] == ["rail.armed"]

    healthy = _state(rails=_rails(streak_halt_until=NOW - 1, drawdown_total=Decimal("4")))
    assert healthy == []


def test_an_engaged_kill_switch_does_not_fire_the_rail_event():
    """The kill switch is a DELIBERATE state entered by an operator at a TTY (doctor renders
    it `halted`, "a correct state, not a fault"): the person who engaged it knows. The rails
    that arm THEMSELVES from trading outcomes are the ones worth a notification."""
    engaged = _state(rails=_rails(kill_switch=True))

    assert [e.key for e in engaged] == []


# -- month-to-date allowance nearing exhaustion -----------------------------------------------


def test_allowance_nearing_exhaustion_fires_at_the_threshold_with_the_pct():
    spend, cap = Decimal("850"), Decimal("1000")  # 85% used

    events = _state(month_to_date_spend=spend, allowance=cap)

    assert [e.key for e in events] == ["allowance.nearing_exhaustion"]
    assert "85" in events[0].message  # the pct used, in the human message
    # A STRING, not the raw Decimal: json.dumps (the plain format's serializer) cannot
    # encode a Decimal, and an unencodable field would swallow the whole delivery in
    # `send_event`'s broad except -- the blocker this suite's round-trip class pins.
    assert events[0].fields["pct_used"] == "85.00"


def test_a_lapsed_subscription_with_activity_fires_and_a_quiet_one_does_not():
    """Zero in-force allowance is the unsubscribed default; spend against it means a
    subscription lapsed (or was never attested) mid-month. That IS the allowance event's
    zero-runway case -- the old `allowance > 0` guard silently suppressed it, exactly the
    gap the corrected comment on `_ATTESTATION_FINDINGS` used to paper over."""
    lapsed = _state(month_to_date_spend=Decimal("25"), allowance=Decimal("0"))

    assert [e.key for e in lapsed] == ["allowance.nearing_exhaustion"]
    assert lapsed[0].fields["allowance"] == "0"
    assert lapsed[0].fields["pct_used"] == "100"
    assert "no subscription is in force" in lapsed[0].message

    quiet = _state(month_to_date_spend=Decimal("0"), allowance=Decimal("0"))
    assert quiet == []  # no spend, nothing to warn about


def test_a_comfortable_or_unlimited_allowance_does_not_fire():
    comfortable = _state(month_to_date_spend=Decimal("500"), allowance=Decimal("1000"))
    assert comfortable == []

    unlimited = _state(month_to_date_spend=Decimal("999999"), allowance=None)
    assert unlimited == []

    # and the threshold itself is the boundary, not a surprise
    exactly_at = _state(month_to_date_spend=ALLOWANCE_NEARING_USED_PCT, allowance=Decimal("100"))
    assert [e.key for e in exactly_at] == ["allowance.nearing_exhaustion"]


# -- a setup detected but not placeable -------------------------------------------------------


def test_a_detected_but_unplaced_setup_fires_and_clean_cycles_do_not():
    vetoed = _state(
        unplaced=(
            UnplacedSetup(product="BTC-USD", rule="dca", reasons=("account_dd_breaker_total",)),
        )
    )
    assert [e.key for e in vetoed] == ["setup.unplaced"]
    assert vetoed[0].fields["count"] == 1
    assert vetoed[0].fields["products"] == ["BTC-USD"]
    assert "BTC-USD" in vetoed[0].message

    clean = _state()
    assert clean == []


# -- feed staleness with an open position -----------------------------------------------------


def test_feed_staleness_fires_only_for_a_product_with_an_open_position():
    """The issue's exact wording: staleness on a product with an OPEN POSITION. A stale feed
    on an unheld product skips that product's entries -- notable, but the position case is
    the one where exits ride on data that has stopped arriving."""
    with_position = _state(stale=("BTC-USD", "ETH-USD"), held=("BTC-USD",))
    assert [e.key for e in with_position] == ["feed.stale_open_position"]
    assert with_position[0].fields["product"] == "BTC-USD"

    without_position = _state(stale=("BTC-USD", "ETH-USD"), held=())
    assert without_position == []


def test_a_rule_managed_product_keeps_the_exit_wording():
    """The original case, unchanged: a product an entry/exit rule watches rides its exits on the
    feed, so a stale feed is exactly that."""
    [event] = _state(stale=("BTC-USD",), held=("BTC-USD",), sleeve_only=("PAXG-USD",))
    assert event.key == "feed.stale_open_position"
    assert event.fields == {"product": "BTC-USD", "watched_by": "rules"}
    assert event.message == (
        "feed for BTC-USD is stale while a position is open -- the cycle skipped it, so its "
        "rule-driven exits are riding on stopped data"
    )


def test_a_product_only_a_sleeve_rule_watches_says_its_proposals_are_paused():
    """P8 polls a held product that only a sleeve-sell rule watches (PAXG under a reverse_dca).
    No rule exits it -- a sleeve rule proposes and never exits (plan Review Focus 5) -- so "rule-
    driven exits are riding on stopped data" is false there. What the stale feed stops is the
    sleeve rule's proposals: `run_once` skips the product before `_handle_reductions`. Same key,
    same WARN."""
    [event] = _state(stale=("PAXG-USD",), held=("PAXG-USD",), sleeve_only=("PAXG-USD",))
    assert event.key == "feed.stale_open_position"
    assert EVENTS_BY_KEY[event.key].severity == WARN
    assert event.fields == {"product": "PAXG-USD", "watched_by": "sleeve"}
    assert event.message == (
        "feed for PAXG-USD is stale while a position is open -- the cycle skipped it, so the "
        "sleeve-sell rule watching it proposes nothing until the feed resumes; no rule exits it"
    )


def test_a_sleeve_only_product_that_is_not_held_is_still_silent():
    assert _state(stale=("PAXG-USD",), held=(), sleeve_only=("PAXG-USD",)) == []


def test_a_fully_healthy_state_produces_no_events_at_all():
    assert _state() == []


# -- the post-cycle wiring --------------------------------------------------------------------


class _Repo:
    """The repo keys `notify_after_cycle` reads -- doctor's `gather_findings` reads -- with
    just enough behaviour to answer them. Keeping it hand-rolled (not the real in-memory
    Repository) pins that the notification layer READS ONLY: no order writes, no state
    writes, nothing a read-only surface could do."""

    def __init__(
        self,
        *,
        withdrawals_attested_at: int,
        held: tuple[str, ...] = (),
        withdrawals_enabled: bool | None = True,
    ) -> None:
        self._withdrawals_attested_at = withdrawals_attested_at
        self._withdrawals_enabled = withdrawals_enabled
        self._held = held
        self.state_writes: list[tuple[str, object]] = []
        #: #732's read. `None` is the unattested posture, which `cash_posture_findings` reports
        #: as its own finding -- so the default here is a book where nobody has attested rather
        #: than one where the question is not asked.
        self.cash_posture: object | None = None

    def get_state(self, key: str, default: object = None) -> object:
        if key == "withdrawals_attested_at":
            return self._withdrawals_attested_at
        # Rail 17's second key (#793). The double is a deployment that HAS attested, so the
        # answer is the attestation's verdict; without it every fixture here reads as an account
        # nobody ever attested for, and these tests are about the expiry clock.
        if key == "withdrawals_enabled":
            return self._withdrawals_enabled
        if key == "kill_switch":
            return False
        if key == "streak_halt_until":
            return 0
        if key == "drawdown_total_pct":
            return Decimal("0")
        return default

    def get_broker_subscription(self, venue: str):  # None: rail 14 is out of scope here
        return None

    def get_venue_cash_posture(self, venue: str):
        return self.cash_posture

    def held_products(self) -> list[str]:
        return list(self._held)

    def set_state(self, key: str, value: object) -> None:
        self.state_writes.append((key, value))

    def get_orders(self, **_: object) -> list[dict]:  # `_monthly_buy_spend_usd` scans this
        return []


class _LoopResult:
    """The tail-of-cycle facts the wiring derives from -- duck-typed to `LoopResult`'s
    notification-relevant fields."""

    def __init__(
        self,
        *,
        enter_signals=(),
        enter_results=(),
        stale_products=(),
        reduce_results=(),
        sleeve_only_products=(),
        exit_watch_transitions=(),
    ) -> None:
        self.enter_signals = list(enter_signals)
        self.enter_results = list(enter_results)
        self.stale_products = list(stale_products)
        self.reduce_results = list(reduce_results)
        self.sleeve_only_products = list(sleeve_only_products)
        self.exit_watch_transitions = list(exit_watch_transitions)


def _recording_transport(calls: list[tuple[str, str]]):
    def _transport(url: str, body: bytes) -> None:
        calls.append((url, body.decode("utf-8")))

    return _transport


def test_notify_after_cycle_reads_doctor_seams_and_sends_only_opted_in_events():
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW - 5 * DAY)  # rail 17: 2 days remain
    # A HEALTHY posture, so this test stays about rail 17 alone. #732 wired
    # `cash_posture_findings` into the same path, and the default double carries no posture at
    # all -- which `doctor` reports as "cash posture never attested", a FAIL, because rail 22
    # vetoes on it. That is a real second event, not a fixture artefact, and it belongs to the
    # tests below rather than to this one.
    repo.cash_posture = _healthy_posture()
    settings = NotificationSettings(events=frozenset({"attestation.expiring"}))
    config = _config_with(settings)

    sent = notify_after_cycle(
        repo,
        config,
        _LoopResult(),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 1
    assert len(calls) == 1
    assert "attestation.expiring" in calls[0][1]
    # Notify-only, with ONE exception since #793: the ledger of which attestation windows have
    # already been alerted on. Pinned as an exact list rather than relaxed to "no writes I mind",
    # because the property being defended is that nothing this layer writes can change what keel
    # TRADES -- and that only holds while the set of keys is this short and this boring.
    assert [key for key, _ in repo.state_writes] == [notifications.NOTIFIED_WINDOWS_KEY]


def test_notify_after_cycle_without_a_url_makes_zero_network_calls(monkeypatch):
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW - 5 * DAY)
    config = _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))

    sent = notify_after_cycle(
        repo, config, _LoopResult(), NOW, url=None, transport=_recording_transport(calls)
    )

    assert sent == 0
    assert calls == []


def test_notify_after_cycle_never_raises_into_the_trading_path():
    """The wiring runs at the tail of every agent cycle; a repo read that explodes must cost
    a notification, never a cycle."""

    class _ExplodingRepo(_Repo):
        def get_state(self, key: str, default: object = None) -> object:
            raise RuntimeError("database is closed")

    calls: list[tuple[str, str]] = []
    config = _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))

    sent = notify_after_cycle(
        _ExplodingRepo(withdrawals_attested_at=NOW),
        config,
        _LoopResult(),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 0
    assert calls == []


def test_notify_after_cycle_lets_base_exceptions_through_to_the_operator():
    """The broad `except Exception` swallows delivery failures, NOT Ctrl-C: an operator
    stopping keel at the cycle tail must see the interrupt reach their terminal, not have it
    quietly converted into 'zero delivered'. Pinned so a future `except BaseException`
    "hardening" cannot eat it."""

    def _interrupt(url: str, body: bytes) -> None:
        raise KeyboardInterrupt

    repo = _Repo(withdrawals_attested_at=NOW - 5 * DAY)
    config = _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))

    with pytest.raises(KeyboardInterrupt):
        notify_after_cycle(
            repo,
            config,
            _LoopResult(),
            NOW,
            url="https://alerts.example/hook",
            transport=_interrupt,
        )


# -- every real event payload must survive delivery ---------------------------------------------


class TestEveryTaxonomyEventSurvivesDelivery:
    """The regression class for the blocker an adversarial review caught: the allowance event
    carried `pct_used` as a raw Decimal, `send_event`'s PLAIN format `json.dumps`'d it with no
    `default=`, and the TypeError was swallowed by the broad except -- delivered `False`, the
    operator got nothing -- while the SLACK format kept working because its fields are
    f-string'd into one text line. Format asymmetry like that is invisible to any test that
    hand-builds a payload, so this class round-trips the REAL field payloads of ALL FIVE
    taxonomy events -- built by `events_from_state` from crafted state, never hand-rolled --
    through `send_event` in BOTH formats, asserting delivered `True` and exactly one POST
    each. This is the test whose absence hid the bug."""

    ALL_KEYS = {
        "attestation.expiring",
        "rail.armed",
        "setup.unplaced",
        "allowance.nearing_exhaustion",
        "feed.stale_open_position",
        "sleeve.proposal",
        "sleeve.exit_watch",
    }

    @staticmethod
    def _all_five_events():
        return _state(
            attestation=_attestation(NOW - 5 * DAY),  # rail 17: 2 of 7 days remain -> WARN
            rails=_rails(streak_halt_until=NOW + DAY),  # rail 16's halt is armed
            month_to_date_spend=Decimal("850"),
            allowance=Decimal("1000"),  # 85% used -- the event that used to carry a Decimal
            unplaced=(
                UnplacedSetup(product="BTC-USD", rule="dca", reasons=("account_dd_breaker_total",)),
            ),
            stale=("ETH-USD",),
            held=("ETH-USD",),  # stale feed under an open position
            # #857: a sliced sleeve proposal, whose total is a Decimal -- the same trap.
            sleeve=(
                ReduceResult(
                    "BTC-USD", "reverse_dca", 4, "preview", [], 4, "", total_qty=Decimal("0.0015")
                ),
            ),
            # #857 P15: a breach carries Decimal levels -- the same trap again.
            watch=(_watch("breached", arms=("drawdown",)),),
        )

    @pytest.mark.parametrize("fmt", ["plain", "slack"])
    def test_every_real_event_payload_delivers_in_this_format(self, fmt):
        events = self._all_five_events()
        assert {e.key for e in events} == self.ALL_KEYS  # the crafted state is all five, really

        for event in events:
            calls: list[tuple[str, str]] = []
            sent = send_event(
                "https://alerts.example/hook",
                event,
                NotificationSettings(events=frozenset({event.key}), format=fmt),
                transport=_recording_transport(calls),
            )

            assert sent is True, (event.key, fmt)
            assert len(calls) == 1, (event.key, fmt)  # one POST per event, exactly
            payload = json.loads(calls[0][1])  # parses: the delivered body IS JSON
            if fmt == "plain":
                assert payload["event"] == event.key
            else:
                assert event.key in payload["text"]


def _config_with(settings: NotificationSettings) -> Config:
    return Config(
        allowlist=["BTC"],
        target_weights={"BTC": Decimal("1")},
        risk_pct=Decimal("0.01"),
        caps=Caps(max_exposure_usd=Decimal("5000"), max_per_asset_pct=Decimal("1")),
        market_data=MarketDataConfig(granularities=[], history_days=30),
        auto_trade=AutoTradeConfig(),
        notifications=settings,
    )


def _healthy_posture():
    """An attested, in-date cash posture -- rail 22 quiet."""
    from keel_core.cash_posture import CashPostureState, VenueCashPosture

    return VenueCashPosture(
        venue="coinbase",
        state=CashPostureState.ATTESTED,
        attested_posture="SPOT_CASH",
        attested_ts=NOW - DAY,
        attest_due_ts=NOW + 200 * DAY,
        refuted_ts=None,
        refuted_reason=None,
        credential_fingerprint="fp-1",
    )


# -- the registry and the call site must agree (#732) ----------------------------------------------


def _lapsed_posture_repo(*, withdrawals_attested_at: int) -> _Repo:
    """A book where BOTH registered attestation findings are unhealthy at once."""
    from keel_core.cash_posture import CashPostureState, VenueCashPosture

    repo = _Repo(withdrawals_attested_at=withdrawals_attested_at)
    repo.cash_posture = VenueCashPosture(
        venue="coinbase",
        state=CashPostureState.ATTESTED,
        attested_posture="SPOT_CASH",
        attested_ts=NOW - 200 * DAY,
        attest_due_ts=NOW - DAY,  # lapsed
        refuted_ts=None,
        refuted_reason=None,
        credential_fingerprint="fp-1",
    )
    return repo


def test_every_registered_attestation_finding_is_actually_deliverable():
    """THE CLASS, not the instance, and driven through the REAL `notify_after_cycle`.

    `_ATTESTATION_FINDINGS` is an opt-in registry; the call site is a hand-written list of doctor
    gatherers. Two lists that must agree with nothing making them agree -- and they did not:
    `attest.cash_posture` was registered and never produced, so an operator who wired a webhook
    for it would never have been told.

    That matters more than a missing warning. It fires when the account is attested
    MARGIN-ENABLED, when the posture attestation has expired, or when it was attested with no due
    date at all -- three states in which rail 22 has stopped letting the agent enter positions,
    where the symptom otherwise is SILENCE.

    A test asserting the two lists match by name would be a third list. This makes every
    registered finding unhealthy at once and asserts each one ARRIVES, so a registration with no
    gatherer fails here rather than in production quiet.
    """
    calls: list[tuple[str, str]] = []
    repo = _lapsed_posture_repo(withdrawals_attested_at=NOW - 5 * DAY)
    config = _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))

    notify_after_cycle(
        repo,
        config,
        _LoopResult(),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    delivered = " ".join(body for _url, body in calls)
    for name in _ATTESTATION_FINDINGS:
        assert name in delivered, f"{name} is registered and nothing delivers it"


def test_both_attestation_rails_are_notified_when_both_are_unhealthy():
    """The `break` said "one event per cycle: the finding list carries one rail-17 verdict" --
    true when the registry held rail 17 alone. With rail 22 in it, a break makes a cash-posture
    problem invisible whenever a withdrawals problem also exists: the same silence, one layer
    down."""
    calls: list[tuple[str, str]] = []
    repo = _lapsed_posture_repo(withdrawals_attested_at=NOW - 5 * DAY)
    config = _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))

    sent = notify_after_cycle(
        repo,
        config,
        _LoopResult(),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 2, f"expected one event per unhealthy rail, got {sent}"
    delivered = " ".join(body for _url, body in calls)
    assert "rail 17" in delivered
    assert "rail 22" in delivered


def test_a_healthy_cash_posture_notifies_nothing():
    """The other direction: an alert that fired on a healthy posture is the alert nobody reads."""
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW - DAY)  # rail 17 comfortably in date
    repo.cash_posture = _healthy_posture()
    config = _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))

    sent = notify_after_cycle(
        repo,
        config,
        _LoopResult(),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 0
    assert calls == []


def test_a_deployment_that_never_attested_a_posture_is_told(monkeypatch):
    """`cash_posture_findings(None)` is a FAIL -- "cash posture never attested" -- and rail 22
    vetoes live entries on it. This is the standing case #732 is really about: nothing lapsed,
    nothing broke, the agent simply cannot enter and had no way to say so."""
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW - DAY)  # rail 17 fine; no posture at all
    config = _config_with(NotificationSettings(events=frozenset({"attestation.expiring"})))

    sent = notify_after_cycle(
        repo,
        config,
        _LoopResult(),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 1
    assert "rail 22" in calls[0][1]


# -- sleeve.proposal (#857, plan P8 Task 8.3, R19) ----------------------------------------------


def _proposal(decision: str, *, proposal_id: int = 4, legs: int = 1, total=None) -> ReduceResult:
    return ReduceResult("BTC-USD", "reverse_dca", proposal_id, decision, [], legs, "", total)


def test_a_new_sleeve_proposal_notifies_once_with_its_id():
    events = _state(sleeve=(_proposal("preview"),))

    [event] = [e for e in events if e.key == "sleeve.proposal"]
    assert event.fields["proposal_id"] == 4
    assert (event.fields["product"], event.fields["rule_kind"], event.fields["decision"]) == (
        "BTC-USD",
        "reverse_dca",
        "preview",
    )
    assert event.message.endswith("keel dca proposals show 4")


def test_a_vetoed_proposal_notifies_too():
    """A vetoed proposal is a decision the operator should see (a skipped distribution says why
    on the row); only arbitration's losers are silent."""
    [event] = _state(sleeve=(_proposal("vetoed", proposal_id=9),))
    assert (event.key, event.fields["proposal_id"], event.fields["decision"]) == (
        "sleeve.proposal",
        9,
        "vetoed",
    )


def test_a_superseded_proposal_does_not_notify():
    events = _state(sleeve=(_proposal("superseded", proposal_id=5, legs=0),))
    assert [e for e in events if e.key == "sleeve.proposal"] == []


def test_one_event_per_notifying_proposal_in_the_cycle():
    events = _state(
        sleeve=(
            _proposal("superseded", proposal_id=5, legs=0),
            _proposal("preview", proposal_id=6),
            _proposal("vetoed", proposal_id=7),
        )
    )
    assert [e.fields["proposal_id"] for e in events if e.key == "sleeve.proposal"] == [6, 7]


def test_a_repeated_refusal_is_recorded_but_does_not_notify_again():
    """P9's carried item: a cooldown vetoing daily wrote 29 alerts in a row. A result that
    repeats its product's previous refusal (`repeats_previous`, set by the cycle from the
    proposals table) is silent; the first veto and every transition still notify."""
    repeated = dataclasses.replace(_proposal("vetoed", proposal_id=8), repeats_previous=True)
    events = _state(sleeve=(_proposal("vetoed", proposal_id=7), repeated))
    assert [e.fields["proposal_id"] for e in events if e.key == "sleeve.proposal"] == [7]


def test_a_sliced_proposal_names_the_whole_sale_and_its_legs():
    """P7's held question: the row's `qty` is the first leg. The notification carries the
    TOTAL and the leg count, as text, so the operator reads the size of the sale."""
    [event] = _state(sleeve=(_proposal("preview", legs=4, total=Decimal("0.0015")),))

    assert (event.fields["total_qty"], event.fields["legs"]) == ("0.0015", 4)
    assert "sell 0.0015 over 4 legs" in event.message


def test_the_wiring_derives_sleeve_proposals_from_the_cycle_result():
    """`notify_after_cycle` reads `result.reduce_results` (R19) and writes nothing new: the
    write list is still exactly the #793 ledger key, and here not even that."""
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW)
    repo.cash_posture = _healthy_posture()
    config = _config_with(NotificationSettings(events=frozenset({"sleeve.proposal"})))

    sent = notify_after_cycle(
        repo,
        config,
        _LoopResult(reduce_results=[_proposal("preview", proposal_id=11)]),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 1
    [(_url, body)] = calls
    payload = json.loads(body)
    assert (payload["event"], payload["proposal_id"]) == ("sleeve.proposal", 11)
    assert repo.state_writes == []


def test_the_wiring_reads_which_stale_products_only_a_sleeve_rule_watches():
    """`notify_after_cycle` hands `result.sleeve_only_products` to the pure derivation, so a
    stale, held product that only a sleeve rule watches is worded as paused proposals."""
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW, held=("PAXG-USD", "BTC-USD"))
    repo.cash_posture = _healthy_posture()
    config = _config_with(NotificationSettings(events=frozenset({"feed.stale_open_position"})))

    sent = notify_after_cycle(
        repo,
        config,
        _LoopResult(stale_products=["BTC-USD", "PAXG-USD"], sleeve_only_products=["PAXG-USD"]),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 2
    payloads = sorted((json.loads(body) for _url, body in calls), key=lambda p: p["product"])
    assert [(p["event"], p["product"], p["watched_by"]) for p in payloads] == [
        ("feed.stale_open_position", "BTC-USD", "rules"),
        ("feed.stale_open_position", "PAXG-USD", "sleeve"),
    ]


# -- sleeve.exit_watch (#857, plan P15 Task 15.3; spec §7, plan OQ10) ---------------------------


def _watch(level, *, previous=None, arms=(), sma=None) -> ExitWatch:
    return ExitWatch("PAXG-USD", level, Decimal("4300"), Decimal("3055"), sma, arms, 1, previous)


@pytest.mark.parametrize("level", ["near", "breached", "clear", "insufficient_history"])
def test_every_exit_watch_transition_is_one_event(level):
    [event] = [e for e in _state(watch=(_watch(level),)) if e.key == "sleeve.exit_watch"]
    assert (event.fields["product"], event.fields["level"]) == ("PAXG-USD", level)
    assert event.fields["previous"] is None
    assert (event.fields["close"], event.fields["dd_level"]) == ("4300", "3055")


def test_one_event_per_transition_in_the_cycle():
    events = _state(watch=(_watch("near"), dataclasses.replace(_watch("clear"), product_id="X")))
    assert [(e.fields["product"], e.fields["level"]) for e in events] == [
        ("PAXG-USD", "near"),
        ("X", "clear"),
    ]


def test_no_transition_no_event():
    assert [e for e in _state() if e.key == "sleeve.exit_watch"] == []


def test_a_breach_names_its_arms_and_says_nothing_is_sold():
    [event] = _state(watch=(_watch("breached", previous="near", arms=("drawdown", "sma")),))
    assert event.fields["breached_arms"] == ["drawdown", "sma"]
    assert event.fields["previous"] == "near"
    assert event.message.startswith("PAXG-USD breached its sleeve exit level (drawdown, sma)")
    assert event.message.endswith(notifications.EXIT_WATCH_TAIL)


def test_a_return_to_clear_is_worded_as_a_recovery_not_a_first_look():
    """Plan OQ10: a transition back to `clear` notifies, worded as a recovery. A product seen
    for the first time at `clear` is not a recovery -- nothing was wrong before."""
    [recovered] = _state(watch=(_watch("clear", previous="breached"),))
    [eased] = _state(watch=(_watch("clear", previous="near"),))
    [first] = _state(watch=(_watch("clear"),))
    [judged] = _state(watch=(_watch("clear", previous="insufficient_history"),))
    assert recovered.message.startswith("PAXG-USD recovered: clear of its sleeve exit levels")
    assert eased.message.startswith("PAXG-USD recovered: clear of its sleeve exit levels")
    assert first.message.startswith("PAXG-USD is now watched: clear of its sleeve exit levels")
    assert judged.message.startswith("PAXG-USD is now watched: clear of its sleeve exit levels")


def test_the_wiring_derives_exit_watch_events_from_the_cycle_result():
    """`notify_after_cycle` reads `result.exit_watch_transitions` and writes nothing new: the
    write list is still exactly the #793 ledger key, and here not even that."""
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW)
    repo.cash_posture = _healthy_posture()
    config = _config_with(NotificationSettings(events=frozenset({"sleeve.exit_watch"})))

    sent = notify_after_cycle(
        repo,
        config,
        _LoopResult(exit_watch_transitions=[_watch("near")]),
        NOW,
        url="https://alerts.example/hook",
        transport=_recording_transport(calls),
    )

    assert sent == 1
    [(_url, body)] = calls
    payload = json.loads(body)
    assert (payload["event"], payload["product"], payload["level"]) == (
        "sleeve.exit_watch",
        "PAXG-USD",
        "near",
    )
    assert repo.state_writes == []
