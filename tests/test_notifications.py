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

from decimal import Decimal

from keel_core.notifications import NotificationSettings

from keel.config import AutoTradeConfig, Caps, Config, MarketDataConfig
from keel.commands.doctor import attestation_findings, rail_state_findings
from keel.notifications import (
    ALLOWANCE_NEARING_USED_PCT,
    UnplacedSetup,
    events_from_state,
    notify_after_cycle,
)

DAY = 86_400
NOW = 1_800_000_000  # a fixed, realistic epoch: no fixture clock drift


# -- builders ---------------------------------------------------------------------------------


def _attestation(withdrawals_attested_at: int, now: int = NOW):
    """doctor's own computation, fed a `None` subscription (rail 14 is not what these events
    are about) and the withdrawal-attestation freshness under test."""
    return attestation_findings(
        subscription=None, withdrawals_attested_at=withdrawals_attested_at, now_ts=now
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
):
    return events_from_state(
        attestation_findings=attestation if attestation is not None else _attestation(NOW),
        rail_findings=rails if rails is not None else _rails(),
        month_to_date_spend=month_to_date_spend,
        allowance=allowance,
        unplaced_setups=unplaced,
        stale_products=stale,
        held_products=held,
    )


# -- rail 17: attestation nearing expiry ------------------------------------------------------


def test_an_attestation_due_within_two_days_fires_and_a_fresh_one_does_not():
    """The issue's sharpest silent failure: rail 17 has a 7-day TTL, fails closed, and the
    veto is a WARNING -- not a CRITICAL. doctor WARNs at <=2 days remaining; the notification
    fires on exactly that threshold, from exactly that computation."""
    due_soon = _state(attestation=_attestation(NOW - 5 * DAY))  # 2 of 7 days remain
    assert [e.key for e in due_soon] == ["attestation.expiring"]
    assert "2 day(s)" in due_soon[0].message
    assert due_soon[0].fields["days_remaining"] == 2

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
    assert events[0].fields["pct_used"] == Decimal("85")


def test_a_comfortable_or_unlimited_allowance_does_not_fire():
    comfortable = _state(month_to_date_spend=Decimal("500"), allowance=Decimal("1000"))
    assert comfortable == []

    unlimited = _state(month_to_date_spend=Decimal("999999"), allowance=None)
    assert unlimited == []

    # and the threshold itself is the boundary, not a surprise
    exactly_at = _state(
        month_to_date_spend=ALLOWANCE_NEARING_USED_PCT, allowance=Decimal("100")
    )
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


def test_a_fully_healthy_state_produces_no_events_at_all():
    assert _state() == []


# -- the post-cycle wiring --------------------------------------------------------------------


class _Repo:
    """The repo keys `notify_after_cycle` reads -- doctor's `gather_findings` reads -- with
    just enough behaviour to answer them. Keeping it hand-rolled (not the real in-memory
    Repository) pins that the notification layer READS ONLY: no order writes, no state
    writes, nothing a read-only surface could do."""

    def __init__(self, *, withdrawals_attested_at: int, held: tuple[str, ...] = ()) -> None:
        self._withdrawals_attested_at = withdrawals_attested_at
        self._held = held
        self.state_writes: list[tuple[str, object]] = []

    def get_state(self, key: str, default: object = None) -> object:
        if key == "withdrawals_attested_at":
            return self._withdrawals_attested_at
        if key == "kill_switch":
            return False
        if key == "streak_halt_until":
            return 0
        if key == "drawdown_total_pct":
            return Decimal("0")
        return default

    def get_broker_subscription(self, venue: str):  # None: rail 14 is out of scope here
        return None

    def held_products(self) -> list[str]:
        return list(self._held)

    def set_state(self, key: str, value: object) -> None:
        self.state_writes.append((key, value))

    def get_orders(self, **_: object) -> list[dict]:  # `_monthly_buy_spend_usd` scans this
        return []


class _LoopResult:
    """The tail-of-cycle facts the wiring derives from -- duck-typed to `LoopResult`'s
    notification-relevant fields."""

    def __init__(self, *, enter_signals=(), enter_results=(), stale_products=()) -> None:
        self.enter_signals = list(enter_signals)
        self.enter_results = list(enter_results)
        self.stale_products = list(stale_products)


def _recording_transport(calls: list[tuple[str, str]]):
    def _transport(url: str, body: bytes) -> None:
        calls.append((url, body.decode("utf-8")))

    return _transport


def test_notify_after_cycle_reads_doctor_seams_and_sends_only_opted_in_events():
    calls: list[tuple[str, str]] = []
    repo = _Repo(withdrawals_attested_at=NOW - 5 * DAY)  # rail 17: 2 days remain
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
    assert repo.state_writes == []  # notify-only: the layer writes NOTHING back


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
