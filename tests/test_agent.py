"""Tests for keel.agent -- the scheduled agent loop, confirm mode (P3 Task 8).

`run_once()` is the one-cycle composition this module drives: poll fresh candles
(`market_feed.poll_once`) -> stale-data check (`market_feed.is_fresh`) -> reconstruct `live`
rules (`repo.get_rules("live")`) -> `engine.evaluate` for ENTER signals -> drive EXIT signals
off held positions (the Phase-2 gap: the loop, not the engine, owns position state) ->
`executor.execute` for both, honoring the kill-switch. Every test injects a **fake broker**
(no network) -- `FakeBroker` below duck-types `CoinbaseClient.get_candles` (for `market_feed`)
+ `.preview_order`/`.place_order` (for `executor`), exactly like
`tests/data/test_market_feed.py::FakeClient` and `tests/execution/test_executor.py::FakeBroker`
it's modeled on -- against an in-memory `Repository` (`connect(":memory:")`).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from keel_broker_api.orders import OrderSpec
from keel_broker_api.results import (
    Balance,
    MarketSchedule,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)
from keel_core.telemetry import _FIELDS_ATTR

from keel import agent
from keel.agent import LoopResult, _build_rule, loop, run_once
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
    PaperConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import executor
from keel.strategy.rules.base import Action, Rule, Setup, Signal
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.pullback_continuation import PullbackContinuation
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Candle, Granularity, Side
from tests.conftest import attest_cash_posture, attest_subscription, attest_trade_scope

PRODUCT = "BTC-USD"


# -- fakes --------------------------------------------------------------------------------


class FakeBroker:
    """Fake broker -- serves canned candles (`market_feed`) + order responses (`executor`).

    No network: `get_candles` reads from an injected in-memory series (like
    `test_market_feed.FakeClient`); `preview_order`/`place_order` return canned,
    `CoinbaseClient`-shaped responses (like `test_executor.FakeBroker`).
    """

    def __init__(self, series: dict[tuple[str, Granularity], list[Candle]] | None = None) -> None:
        self._series = series or {}
        self.get_candles_calls: list[tuple[str, Granularity, int, int]] = []
        self.preview_calls: list[dict[str, Any]] = []
        self.place_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []
        # Ordered exchange interactions (place/cancel), so a test can assert SEQUENCE -- the
        # stop-management step (#502) must cancel a bracket before placing its replacement.
        self.events: list[str] = []
        self._order_seq = 0
        # The previewed fee, overridable by a subclass that means to test fee splitting.
        self.commission = Decimal("0")

    def get_balances(self) -> list[Balance]:
        """Comfortable balances -- rail 13 fails closed otherwise. Both legs are funded because
        rail 13 checks the PRODUCT's quote leg (BTC-USD spends USD), not config.quote_currency."""
        return [
            Balance(currency="USD", available=Decimal("1000000"), total=Decimal("1000000")),
            Balance(currency="USDC", available=Decimal("1000000"), total=Decimal("1000000")),
        ]

    def get_candles(
        self, product_id: str, granularity: Granularity, start: int, end: int
    ) -> list[Candle]:
        self.get_candles_calls.append((product_id, granularity, start, end))
        series = self._series.get((product_id, granularity), [])
        return [c for c in series if start <= c.ts <= end]

    def preview_order(self, spec: OrderSpec) -> Preview:
        self.preview_calls.append({"product_id": spec.product_id, "side": spec.side})
        return Preview(
            product_id=spec.product_id,
            side=spec.side,
            est_base_size=Decimal("0"),
            est_quote_size=Decimal("50.00"),
            est_fee=self.commission,
            synthetic=False,
            # Both book sides, as the real venue returns them: #350's spread gate fails closed on
            # a preview without them (tests that mean a degraded/bookless response build their
            # own Preview).
            detail={"best_bid": "99.95", "best_ask": "100", "order_total": "50.00"},
        )

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        self._order_seq += 1
        self.place_calls.append({"product_id": spec.product_id, "side": spec.side})
        self.events.append("place")
        return PlaceResult(success=True, broker_order_id=f"broker-order-{self._order_seq}")

    def cancel_order(self, order_id: str) -> bool:
        self.cancel_calls.append(order_id)
        self.events.append("cancel")
        return True  # a CONFIRMED cancel -- see `_cancel_at_exchange`


class _AlwaysExitRule(Rule):
    """Test-only rule (mirrors `test_engine.py::_FixedSetupRule`): never enters, always exits
    a held position -- isolates the loop's EXIT-signal wiring from any real rule's detect()
    gating logic.
    """

    def __init__(self, product_id: str, name: str = "fake_exit") -> None:
        self.name = name
        self.product_id = product_id
        self.params: dict = {"product_id": product_id}

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return True

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _NeverExitRule(_AlwaysExitRule):
    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False


@pytest.fixture(autouse=True)
def _register_fake_rules():
    """Register the test-only rule kinds into `agent.RULE_REGISTRY` for this test module,
    and restore the registry afterward so other tests aren't affected.
    """
    agent.RULE_REGISTRY["fake_exit"] = _AlwaysExitRule
    agent.RULE_REGISTRY["fake_no_exit"] = _NeverExitRule
    yield
    del agent.RULE_REGISTRY["fake_exit"]
    del agent.RULE_REGISTRY["fake_no_exit"]


@pytest.fixture(autouse=True)
def _reset_warmup_notice():
    """Start every test with a clean per-product warmup-notice set: the notice is once per
    product PER PROCESS (`agent._WARMUP_LOGGED_PRODUCTS`), so without a reset an earlier
    test's notice would suppress the one a later test asserts on."""
    agent._WARMUP_LOGGED_PRODUCTS.clear()
    yield
    agent._WARMUP_LOGGED_PRODUCTS.clear()


# -- fixtures / builders --------------------------------------------------------------------


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    # Rail 17 (§65.4) fails closed without a fresh withdrawal attestation. Seeded here for the
    # same reason the kill-switch is: these tests are not ABOUT rail 17, and a fail-closed rail
    # would otherwise veto every BUY in the module. A huge attested_at keeps it fresh regardless
    # of each test's `now_ts`.
    r.set_state("withdrawals_enabled", True)
    r.set_state("withdrawals_attested_at", 10**12)
    # Autonomy is a PROFILE choice now. Most tests in this module were written against "it just
    # places"; opting the profile in here preserves that, and the tests that are specifically
    # about the confirm/autonomous decision set the profile themselves.
    r.set_autonomous(True, now_ts=0)
    # rail 14 now derives its cap from the attested subscription record rather than a config
    # default; attest a very large allowance so pre-existing tests here (none of which exercise
    # rail 14) aren't incidentally tripped by it.
    attest_subscription(r, now_ts=0, free_volume_usd=Decimal("10000000"))
    # Rail 20 (#233) fails closed without a trade-scope record; confirm coinbase here for the
    # same reason -- these tests are not ABOUT rail 20.
    attest_trade_scope(r, now_ts=0)
    # Rail 22 (#691) fails closed without a cash-posture record, same as rail 20.
    attest_cash_posture(r, now_ts=0)
    return r


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "ETH", "PAXG"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[Granularity.ONE_DAY], history_days=365),
        auto_trade=AutoTradeConfig(mode="confirm", interval_sec=50_000),
        money_mgmt=MoneyMgmtConfig(),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _candle(ts: int, price: str = "100") -> Candle:
    p = Decimal(price)
    return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))


def _seed_open_position(
    repo: Repository,
    product_id: str,
    qty: Decimal,
    price: Decimal,
    ts: int,
    *,
    rule_name: str | None = None,
    entry_fee: Decimal = Decimal("0"),
    bracket_order_id: int | None = None,
) -> None:
    """Held inventory in the orders log, and -- when `rule_name` is given -- the matching
    `positions` tranche a real entry would have opened alongside it.

    Callers that only need inventory (equity/rail-11 tests) omit `rule_name`; callers that
    exercise an EXIT need the tranche, because the entry context P&L is attributed against lives
    there now rather than in `position_rule:<product>`.
    """
    repo.insert_order(
        dict(
            mode="live",
            product_id=product_id,
            side=Side.BUY.value,
            order_type="market",
            qty=qty,
            limit_price=price,
            status="filled",
            fee=Decimal("0"),
            expected_fill=price,
            actual_fill=price,
            raw_response=None,
            confirmation="autonomous",
            rule_id=None,
            created_at=ts,
            updated_at=ts,
        )
    )
    if rule_name is not None:
        repo.open_position(
            product_id=product_id,
            rule_name=rule_name,
            opened_at=ts,
            qty=qty,
            entry_fill=price,
            entry_fee=entry_fee,
            bracket_order_id=bracket_order_id,
        )


# -- run_once: kill-switch --------------------------------------------------------------------


def test_kill_switch_engaged_does_nothing(repo):
    repo.set_state("kill_switch", True)
    broker = FakeBroker()

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert isinstance(result, LoopResult)
    assert result.skipped is True
    assert result.skip_reason == "kill_switch"
    assert repo.get_orders() == []
    assert broker.place_calls == []
    assert broker.get_candles_calls == []


def test_kill_switch_unset_defaults_to_engaged_fails_closed(repo):
    """`agent_state.kill_switch` is never set -- `repo.get_state` defaults to `True` (fails
    closed, matching `guards.check`'s own rail 12 default)."""
    conn = connect(":memory:")
    migrate(conn)
    bare_repo = Repository(conn)
    broker = FakeBroker()

    result = run_once(broker, bare_repo, _config(), now_ts=90_000)

    assert result.skipped is True
    assert result.skip_reason == "kill_switch"


# -- run_once: session awareness (FR-9: a closed venue is not a stale feed) --------------------


def _skip_events(caplog) -> list[tuple[str, dict]]:
    """`(event, fields)` for every `agent.cycle_skipped` record, via `telemetry`'s own
    structured-fields attribute -- the same read `tests/execution/test_executor.py` uses."""
    return [
        (record.getMessage(), getattr(record, _FIELDS_ATTR, {}))
        for record in caplog.records
        if record.getMessage() == "agent.cycle_skipped"
    ]


class _SessionClockBroker(FakeBroker):
    """A `FakeBroker` that also answers the broker PORT's session surface.

    `capabilities()` duck-types the fields the engine reads (`session_bound`, and `venue`
    when the test wants the namespaced recording): the live path's `CoinbaseClient` has no
    `capabilities()` at all, and the engine must treat that broker as 24/7 rather than
    crash -- the tests below exercise both sides of that split. `venue=""` (the default)
    is the anonymous slot: a session-bound broker that declares no venue records into the
    legacy un-namespaced keys, exactly as every real `BrokerCapabilities.venue`-carrying
    adapter's negative space does not.
    """

    def __init__(self, clock_answer: Any, series=None, venue: str = "") -> None:
        super().__init__(series=series)
        self._clock_answer = clock_answer
        self._venue = venue
        self.clock_calls = 0

    def capabilities(self) -> Any:
        return SimpleNamespace(session_bound=True, venue=self._venue)

    def market_clock(self) -> Any:
        self.clock_calls += 1
        if isinstance(self._clock_answer, Exception):
            raise self._clock_answer
        return self._clock_answer


class _ScheduleClockBroker(_SessionClockBroker):
    """A `_SessionClockBroker` that also answers the port's SCHEDULE read (issue #388 C2).

    `market_schedule()` returns a canned `MarketSchedule` (or raises, when the test wants a
    third-party adapter that violates the port); the clock answer is derived from the
    schedule's own state unless the test overrides it, so the two reads agree by
    construction the way a conformant adapter's do.
    """

    def __init__(
        self,
        schedule: Any,
        series: Any = None,
        venue: str = "",
        clock_answer: Any = None,
    ) -> None:
        state = clock_answer if clock_answer is not None else getattr(schedule, "state", None)
        super().__init__(state, series=series, venue=venue)
        self._schedule_answer = schedule
        self.schedule_calls = 0

    def market_schedule(self) -> Any:
        self.schedule_calls += 1
        if isinstance(self._schedule_answer, Exception):
            raise self._schedule_answer
        return self._schedule_answer


def test_closed_market_skips_the_cycle_like_the_kill_switch(repo, caplog):
    """FR-9: a weekend or holiday on a session-bound venue reads "market closed", never
    "feed stale". The skip mirrors the kill-switch skip exactly -- no poll, no evaluation,
    no orders -- so a closed venue can neither log staleness-gated noise nor hammer the feed."""
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = _SessionClockBroker(
        SessionState.CLOSED, series={(PRODUCT, Granularity.ONE_DAY): [_candle(0)]}
    )

    with caplog.at_level(logging.INFO):
        result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is True
    assert result.skip_reason == "market_closed"
    assert result.mode is None
    assert result.polled == 0
    assert broker.get_candles_calls == []  # no feed polling while closed
    assert broker.place_calls == []  # and no evaluation ever ran
    assert _skip_events(caplog) == [("agent.cycle_skipped", {"reason": "market_closed"})]


def test_the_closed_session_is_recorded_for_the_staleness_surfaces(repo):
    """The cycle records the venue's session answer so the broker-free surfaces (`fetch
    --check`, `status`, the TUI) can show "market closed" without a clock call of their own."""
    broker = _SessionClockBroker(SessionState.CLOSED)

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_state("market_session") == "closed"
    assert repo.get_state("market_session_ts") == 90_000


def test_open_market_session_runs_the_cycle_exactly_as_today(repo):
    """An open session changes nothing: the clock is consulted once, then the cycle proceeds
    through its ordinary poll -> evaluate path, and the open answer is recorded too."""
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = _SessionClockBroker(
        SessionState.OPEN, series={(PRODUCT, Granularity.ONE_DAY): [_candle(0)]}
    )

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert result.skip_reason is None
    assert broker.clock_calls == 1
    assert broker.get_candles_calls  # polled as usual
    assert repo.get_state("market_session") == "open"


def test_a_24x7_broker_is_never_asked_its_clock_and_records_nothing(repo):
    """Crypto unchanged, byte for byte: a broker without the port's session surface (the
    live path's `CoinbaseClient` today) runs the cycle exactly as before, and no session
    state is written -- so every broker-free surface renders what it rendered yesterday."""

    class _NoPortBroker(FakeBroker):
        """Deliberately no `capabilities()`/`market_clock()`: the pre-port broker shape."""

    broker = _NoPortBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0)]})
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert broker.get_candles_calls  # the ordinary cycle ran
    assert repo.get_state("market_session") is None
    assert repo.get_state("market_session_ts") is None


def test_a_port_broker_that_declares_24x7_is_never_asked_its_clock_either(repo):
    """The other half of the 24/7 guarantee, at the engine seam: a broker that DOES answer
    the port but declares `session_bound=False` (the crypto adapters) has no clock consulted
    -- even one that would explode -- and records nothing."""

    class _AlwaysOpenBroker(FakeBroker):
        def capabilities(self) -> Any:
            return SimpleNamespace(session_bound=False)

        def market_clock(self) -> Any:
            raise AssertionError("a 24/7 venue must never be asked for a clock")

    broker = _AlwaysOpenBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0)]})
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert repo.get_state("market_session") is None


def test_unreadable_clock_fails_closed_with_a_distinct_reason(repo, caplog):
    """FR-9's fail-closed rule: a clock that cannot be read is TREATED as closed -- the
    cycle skips and never crashes -- but the reason is DISTINCT from a venue that says
    closed, because "we could not know" and "we know it is shut" are different facts for
    an operator."""
    broker = _SessionClockBroker(SessionState.CLOCK_UNAVAILABLE)

    with caplog.at_level(logging.WARNING):
        result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is True
    assert result.skip_reason == "market_clock_unavailable"
    assert broker.get_candles_calls == []
    assert repo.get_state("market_session") == "clock_unavailable"
    assert _skip_events(caplog) == [("agent.cycle_skipped", {"reason": "market_clock_unavailable"})]


def test_a_clock_that_raises_rather_than_answering_fails_closed(repo):
    """A third-party adapter that RAISES instead of answering `CLOCK_UNAVAILABLE` (the port
    says never do that) still fails closed here -- the engine never lets a clock read crash
    the cycle."""
    broker = _SessionClockBroker(RuntimeError("venue clock endpoint exploded"))

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is True
    assert result.skip_reason == "market_clock_unavailable"


def test_kill_switch_still_outranks_a_closed_market(repo):
    """The operator's halt still owns the SKIP REASON -- a closed market never masks an
    engaged kill-switch in the logs. The clock IS now consulted (once) before that check,
    so the session record stays fresh even while halted; see the recording-on-every-path
    tests below for why that ordering is load-bearing."""
    repo.set_state("kill_switch", True)
    broker = _SessionClockBroker(SessionState.CLOSED)

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is True
    assert result.skip_reason == "kill_switch"
    assert broker.clock_calls == 1
    assert repo.get_state("market_session") == "closed"


# -- run_once: the session record survives every skip path (review finding on #385) --------------


def test_a_halted_agent_still_records_and_refreshes_the_session(repo):
    """The kill-switch return used to sit BEFORE the session gate, so a halted agent never
    recorded or refreshed the session: weekends under a kill switch false-positived STALE,
    and a Friday-pre-close halt froze `market_session="open"` for the whole weekend. The
    session (and its ts) is now recorded first, on every path a cycle can return through."""
    repo.set_state("kill_switch", True)
    broker = _SessionClockBroker(SessionState.CLOSED)

    first = run_once(broker, repo, _config(), now_ts=90_000)
    second = run_once(broker, repo, _config(), now_ts=140_000)

    assert first.skip_reason == "kill_switch"  # the halt still owns the reason
    assert second.skip_reason == "kill_switch"
    assert repo.get_state("market_session") == "closed"
    assert repo.get_state("market_session_ts") == 140_000  # refreshed, not frozen at 90_000


def test_a_frozen_open_record_thaws_once_the_halted_agent_cycles_the_weekend(repo):
    """The frozen-"open" weekend, end to end: the halt happened Friday pre-close, the last
    record said open, and `status` rendered "market session: open (venue clock)" all
    weekend. Once that stale record is outside its trust window, the (still-halted) agent's
    next cycle re-records the venue's actual answer -- closed."""
    repo.set_state("kill_switch", True)
    repo.set_state("market_session", "open")
    # Friday's record, now older than the trust window (config 50_000 x FEED_STALENESS_CYCLES 3).
    repo.set_state("market_session_ts", 90_000)
    broker = _SessionClockBroker(SessionState.CLOSED)

    saturday = 90_000 + 200_000
    # Before the cycle, the expired open record defuses nothing -- the old code left it as
    # the deployment's only answer all weekend ("open (venue clock)", staleness alerting or
    # not, per whatever Friday froze).
    assert agent.recorded_market_closed(repo, _config(), saturday) is False

    result = run_once(broker, repo, _config(), now_ts=saturday)

    assert result.skip_reason == "kill_switch"
    # The cycle itself refreshed the venue's actual answer:
    assert repo.get_state("market_session") == "closed"
    assert repo.get_state("market_session_ts") == saturday


# -- recorded_market_closed: venue namespacing + the trust window -------------------------------


def test_the_record_is_namespaced_by_the_declared_venue(repo):
    """A session-bound broker that declares a venue records under `market_session:{venue}`,
    so two deployments sharing a repo cannot clobber (or impersonate) each other's clock."""
    broker = _SessionClockBroker(SessionState.CLOSED, venue="alpaca")

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_state("market_session:alpaca") == "closed"
    assert repo.get_state("market_session_ts:alpaca") == 90_000
    # The anonymous (legacy) slot stays untouched -- nothing impersonates it.
    assert repo.get_state("market_session") is None


# -- the recorded schedule (issue #388 C2: next open/close for the session banner) ---------------


def test_the_cycle_records_the_venues_next_open_and_next_close(repo):
    """O9's banner needs the schedule, and the recording rule is the one B1 set: the one
    component holding a broker records, every broker-free surface reads. The cycle now
    records `next_open`/`next_close` under the SAME venue-namespaced keys and the SAME
    trust window (state + ts + interval), so the banner renders recorded data and never
    makes a clock call of its own."""
    broker = _ScheduleClockBroker(
        MarketSchedule(
            state=SessionState.OPEN,
            next_open_ts=1_787_059_800,
            next_close_ts=1_786_996_800,
        ),
        venue="alpaca",
    )

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_state("market_session:alpaca") == "open"
    assert repo.get_state("market_session_ts:alpaca") == 90_000
    assert repo.get_state("market_session_next_open:alpaca") == 1_787_059_800
    assert repo.get_state("market_session_next_close:alpaca") == 1_786_996_800


def test_an_unreadable_clock_records_no_schedule_at_all(repo):
    """A CLOCK_UNAVAILABLE cycle still records (the degraded answer is itself a fact), but
    claims NO schedule: the two keys are written as nulls rather than left carrying the
    last readable cycle's timestamps -- a stale `next_open` rendered as fact is exactly the
    TUI-side calendar the PRD forbids."""
    broker = _ScheduleClockBroker(
        MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE), venue="alpaca"
    )
    # A previous cycle's good record, which the degraded cycle must clear:
    repo.set_state("market_session_next_open:alpaca", 1_787_059_800)
    repo.set_state("market_session_next_close:alpaca", 1_786_996_800)

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_state("market_session:alpaca") == "clock_unavailable"
    assert repo.get_state("market_session_next_open:alpaca") is None
    assert repo.get_state("market_session_next_close:alpaca") is None


def test_a_pre_schedule_port_broker_still_records_its_session_with_null_times(repo):
    """A third-party session-bound adapter built against the pre-#388 port (a
    `market_clock()` but no `market_schedule()`) keeps working: the state is derived from
    the clock answer and the schedule keys record as nulls -- the port extension breaks no
    existing adapter."""
    broker = _SessionClockBroker(SessionState.OPEN, venue="thirdparty")

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_state("market_session:thirdparty") == "open"
    assert repo.get_state("market_session_next_open:thirdparty") is None
    assert repo.get_state("market_session_next_close:thirdparty") is None


def test_a_schedule_that_raises_fails_closed_like_a_clock_that_raises(repo):
    """The port's fail-closed rule, carried to the new read: a schedule read that explodes
    is a CLOCK_UNAVAILABLE cycle -- the engine never lets it crash, and nothing from it is
    recorded as fact."""
    broker = _ScheduleClockBroker(
        RuntimeError("schedule endpoint exploded"),
        venue="alpaca",
        clock_answer=SessionState.OPEN,
    )

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is True
    assert result.skip_reason == "market_clock_unavailable"
    assert repo.get_state("market_session:alpaca") == "clock_unavailable"
    assert repo.get_state("market_session_next_open:alpaca") is None


def test_latest_recorded_session_round_trips_what_the_cycle_recorded(repo):
    """The broker-free read for the banner (and any other surface): one call over the
    recorded state returns the newest-stamped venue's session WITH its schedule and its
    freshness -- the same one-recording-everyone-reads shape `recorded_market_closed`
    already keeps."""
    broker = _ScheduleClockBroker(
        MarketSchedule(
            state=SessionState.CLOSED,
            next_open_ts=1_787_059_800,
            next_close_ts=1_786_996_800,
        ),
        venue="alpaca",
    )
    run_once(broker, repo, _config(), now_ts=90_000)

    record = agent.latest_recorded_session(repo, _config(), now_ts=95_000)

    assert record is not None
    assert record.venue == "alpaca"
    assert record.state == "closed"
    assert record.recorded_ts == 90_000
    assert record.next_open_ts == 1_787_059_800
    assert record.next_close_ts == 1_786_996_800
    assert record.fresh is True  # inside the recorded interval's trust window


def test_latest_recorded_session_answers_none_when_nothing_was_recorded(repo):
    """A 24/7 deployment (or one whose agent has never cycled) has no session record at
    all; the read says `None` rather than inventing one, and the banner decides what that
    means (24/7 vs CLOCK UNAVAILABLE) from the venue's own declaration."""
    assert agent.latest_recorded_session(repo, _config(), now_ts=90_000) is None


def test_latest_recorded_session_reports_stale_records_as_not_fresh(repo):
    """The record's honesty has a window (the recorded interval x FEED_STALENESS_CYCLES,
    exactly `recorded_market_closed`'s): beyond it the state no longer vouches for
    anything, `fresh` goes False -- the banner renders CLOCK UNAVAILABLE off that, never
    a months-old 'closed'."""
    now_ts = 90_000
    repo.set_state("market_session:alpaca", "closed")
    repo.set_state("market_session_ts:alpaca", now_ts - 500_000)  # far past 50_000 x 3
    repo.set_state("market_session_interval_sec:alpaca", 50_000)
    repo.set_state("market_session_next_open:alpaca", 1_787_059_800)

    record = agent.latest_recorded_session(repo, _config(), now_ts)

    assert record is not None
    assert record.fresh is False
    assert record.next_open_ts == 1_787_059_800  # the raw record is returned as-is


def test_latest_recorded_session_picks_the_newest_stamped_venue(repo):
    """A repo shared by two deployments answers with the MOST RECENTLY stamped venue's
    record -- the same tiebreak `status`'s own session read uses, so the two surfaces can
    never disagree about which venue is on screen."""
    repo.set_state("market_session:alpaca", "open")
    repo.set_state("market_session_ts:alpaca", 90_000)
    repo.set_state("market_session:nyse", "closed")
    repo.set_state("market_session_ts:nyse", 91_000)

    record = agent.latest_recorded_session(repo, _config(), now_ts=91_500)

    assert record is not None
    assert record.venue == "nyse"
    assert record.state == "closed"


def test_one_venues_closed_record_does_not_defuse_another_venue(repo):
    """The shared-DB hazard, pinned at the seam that answers it: an equities agent's CLOSED
    must silence only the equities venue's staleness -- a 24/7 deployment asking for its own
    venue reads right through that record."""
    now_ts = 90_000
    repo.set_state("market_session:alpaca", "closed")
    repo.set_state("market_session_ts:alpaca", now_ts)
    config = _config()

    assert agent.recorded_market_closed(repo, config, now_ts, venue="alpaca") is True
    assert agent.recorded_market_closed(repo, config, now_ts, venue="coinbase") is False


def test_two_venues_coexist_in_one_repo(repo):
    """Namespacing is per-venue state, not a single shared pair: one repo can hold a closed
    equities venue and an open one at the same time, and each venue's question gets its own
    venue's answer."""
    now_ts = 90_000
    repo.set_state("market_session:alpaca", "closed")
    repo.set_state("market_session_ts:alpaca", now_ts)
    repo.set_state("market_session:nyse", "open")
    repo.set_state("market_session_ts:nyse", now_ts)
    config = _config()

    assert repo.get_state("market_session:alpaca") == "closed"
    assert repo.get_state("market_session:nyse") == "open"
    assert agent.recorded_market_closed(repo, config, now_ts, venue="alpaca") is True
    assert agent.recorded_market_closed(repo, config, now_ts, venue="nyse") is False


def test_a_none_clock_answer_fails_closed_without_raising(repo):
    """A third-party adapter returning None (or any non-`SessionState`) from
    `market_clock()` used to raise `AttributeError` at `session.value` and kill the loop.
    It is `CLOCK_UNAVAILABLE`: the same fail-closed skip, the same distinct reason, and the
    degraded answer is still recorded."""
    broker = _SessionClockBroker(None)

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is True
    assert result.skip_reason == "market_clock_unavailable"
    assert repo.get_state("market_session") == "clock_unavailable"


def test_the_trust_window_uses_the_recorded_cycle_interval_not_the_config_one(repo):
    """`keel agent --loop --interval 7200` with a config interval of 900: the record is
    trusted for the interval the deployment ACTUALLY cycles at, so a record sitting between
    two long cycles (3h old -- long past the config-only 45-minute window) stays trusted
    instead of false-positiving the weekend."""
    config = _config(auto_trade=AutoTradeConfig(mode="confirm", interval_sec=900))
    now_ts = 90_000
    repo.set_state("market_session", "closed")
    repo.set_state("market_session_ts", now_ts - 10_800)  # 3h old
    repo.set_state("market_session_interval_sec", 7_200)

    assert agent.recorded_market_closed(repo, config, now_ts) is True


def test_the_trust_window_falls_back_to_config_when_no_interval_was_recorded(repo):
    """Records written before the interval was recorded alongside the session keep the
    config-derived window -- the pre-existing behaviour, unchanged."""
    config = _config(auto_trade=AutoTradeConfig(mode="confirm", interval_sec=900))
    now_ts = 90_000
    repo.set_state("market_session", "closed")

    repo.set_state("market_session_ts", now_ts - 2_700)  # exactly 900 x 3
    assert agent.recorded_market_closed(repo, config, now_ts) is True
    repo.set_state("market_session_ts", now_ts - 2_701)  # one second past it
    assert agent.recorded_market_closed(repo, config, now_ts) is False


def test_the_loop_threads_the_interval_it_actually_cycles_at_into_the_record(repo, monkeypatch):
    """`loop` knows the effective interval (`--interval` override included); `run_once`
    alone would only know the config's. The record carries the deployment's real cadence."""
    config = _config(auto_trade=AutoTradeConfig(mode="confirm", interval_sec=900))
    broker = _SessionClockBroker(SessionState.CLOSED)
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)  # no real 2h sleep
    cycles = [0]

    def stop_flag() -> bool:
        cycles[0] += 1
        return cycles[0] > 1

    loop(broker, repo, config, 7_200, stop_flag)

    assert repo.get_state("market_session_interval_sec") == 7_200


def test_staleness_that_began_before_the_close_is_attenuated_until_the_record_expires(repo):
    """The deliberate trade-off `recorded_market_closed`'s docstring states: staleness that
    begins DURING trading (a feed break before the close) is excused once the closed record
    exists -- a bounded silence -- and re-alerts after the record expires post-reopen, when
    the deployment that stopped re-recording can no longer vouch for the quiet."""
    config = _config()  # interval 50_000 -> trust window 150_000
    staleness_started = 90_000

    # Break-before-close: the venue was still open, so nothing defuses the alert.
    repo.set_state("market_session", "open")
    repo.set_state("market_session_ts", staleness_started)
    assert agent.recorded_market_closed(repo, config, staleness_started) is False

    # The venue closes and the (healthy) agent records it: the pending staleness goes quiet,
    # even though it predates the close.
    repo.set_state("market_session", "closed")
    repo.set_state("market_session_ts", staleness_started + 1_000)
    repo.set_state("market_session_interval_sec", 50_000)
    assert agent.recorded_market_closed(repo, config, staleness_started + 2_000) is True

    # Post-reopen, the record has expired out of its trust window: the staleness re-alerts.
    expired = staleness_started + 1_000 + 150_000 + 1
    assert agent.recorded_market_closed(repo, config, expired) is False


# -- run_once: the happy path (real merged Dca rule) -------------------------------------------


def test_run_once_polls_evaluates_and_executes_a_real_dca_rule(repo):
    """End-to-end with the real, merged `Dca` rule (spec's own `dca` cadence-boundary fixture,
    matching `tests/strategy/test_engine.py::_dca_daily_candle`): a single day-0 candle,
    `now_ts` inside day 1 so the latest *closed* daily candle is day 0 -- `poll_once` fetches
    it, `Dca.detect()` fires on the cadence boundary, and `executor.execute` places a real
    (fake-broker) market buy.
    """
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert result.polled == 1
    assert result.products == [PRODUCT]
    assert result.stale_products == []
    assert len(result.enter_signals) == 1
    assert result.enter_signals[0].rule_name == "dca"
    assert len(result.enter_results) == 1
    assert result.enter_results[0].placed is True
    assert result.exit_results == []

    orders = repo.get_orders(mode="live", product_id=PRODUCT)
    assert len(orders) == 1
    assert orders[0]["side"] == "BUY"
    # dca_size(config.dca.budget_usd=50, entry=100) = 0.5
    assert orders[0]["qty"] == Decimal("0.5")

    # the loop records which rule owns the freshly opened position, for future exit checks.
    position = agent._position_state(repo, PRODUCT)
    assert position is not None
    assert position["rule_name"] == "dca"
    assert position["opened_at"] == 90_000

    # ...and the entry context a `trade_outcomes` row will need now lands in the per-tranche
    # ledger rather than in that per-product blob, which was last-write-wins across entries.
    tranches = repo.get_open_positions(PRODUCT)
    assert len(tranches) == 1
    assert tranches[0]["rule_name"] == "dca"
    assert tranches[0]["opened_at"] == 90_000
    assert tranches[0]["entry_fill"] == Decimal("100")
    assert tranches[0]["qty"] == Decimal("0.5")
    # and records that the feed was checked this cycle (guards rail 12 reads this).
    assert repo.get_state("last_feed_ts") == 90_000


def test_run_once_writes_the_seeded_rules_db_id_onto_the_order(repo):
    """The metadata-only fix under test: a full cycle's placed order now carries the
    originating rule's real `rules.id`, threaded `_build_rule` -> `Rule.rule_id` ->
    `engine.evaluate`'s `Signal.rule_id` -> `executor._build_intent`'s `OrderIntent.rule_id` ->
    `executor._order_row`. Everything else about the placed order matches
    `test_run_once_polls_evaluates_and_executes_a_real_dca_rule` exactly -- proving this is
    ADDITIVE metadata, not a change to what gets placed.
    """
    rule_id = repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert len(result.enter_signals) == 1
    assert result.enter_signals[0].rule_id == rule_id
    assert result.enter_results[0].placed is True

    orders = repo.get_orders(mode="live", product_id=PRODUCT)
    assert len(orders) == 1
    order = orders[0]
    assert order["rule_id"] == rule_id
    # Everything else about the order is unchanged from the pre-fix shape/values.
    assert order["side"] == "BUY"
    assert order["qty"] == Decimal("0.5")
    assert order["status"] == "filled"
    assert len(broker.place_calls) == 1


# -- run_once: autonomy is a live-read profile choice --------------------------------------------


def test_autonomy_off_places_nothing_even_though_the_rule_fires(repo):
    """The default. The rule still fires and is logged, but with no `confirm_fn` the order is
    previewed and never placed -- confirm mode fails closed."""
    repo.set_autonomous(False, now_ts=0)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert len(result.enter_signals) == 1  # the rule still fires...
    assert result.enter_results[0].placed is False  # ...but nothing is placed.
    assert broker.place_calls == []
    assert result.mode == "confirm"
    assert repo.get_orders() == []


def test_autonomy_on_places_without_a_confirm_fn(repo):
    repo.set_autonomous(True, now_ts=0)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.mode == "autonomous"
    assert result.enter_results[0].placed is True
    assert len(broker.place_calls) == 1
    assert repo.get_orders(mode="live", product_id=PRODUCT)[0]["side"] == "BUY"


def test_an_absent_profile_row_is_treated_as_NOT_autonomous(repo):
    """Fails closed: a database that never recorded a choice must not imply consent."""
    repo._conn.execute("DELETE FROM profile")
    repo._conn.commit()
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.mode == "confirm"
    assert broker.place_calls == []


def test_the_profile_is_re_read_every_cycle_not_cached(repo):
    """`keel autonomy off` must take effect on the NEXT order, not the next restart."""
    repo.set_autonomous(True, now_ts=0)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    first = run_once(broker, repo, _config(), now_ts=90_000)
    assert first.mode == "autonomous"

    repo.set_autonomous(False, now_ts=1)
    second = run_once(broker, repo, _config(), now_ts=180_000)
    assert second.mode == "confirm", "the profile was cached; turning autonomy off did nothing"


def test_paper_mode_places_nothing_even_when_autonomous(repo):
    """The two switches are independent: autonomy never turns a simulation into real money."""
    repo.set_autonomous(True, now_ts=0)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    config = _config(auto_trade=AutoTradeConfig(mode="paper", interval_sec=50_000))

    run_once(broker, repo, config, now_ts=90_000)

    assert broker.place_calls == [], "paper mode must never reach the broker"


def test_paper_mode_is_reported_as_paper_not_confirm(repo):
    """The loop summary must name the real operating mode.

    Paper routes to the paper path and never touches the broker, but `_effective_mode` returns
    the executor string `"confirm"` for any non-live config -- so a paper cycle used to report
    `mode=confirm`, telling the user a confirm-mode (live) run happened when nothing did. The
    reported mode must say `"paper"`.
    """
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    config = _config(auto_trade=AutoTradeConfig(mode="paper", interval_sec=50_000))

    result = run_once(broker, repo, config, now_ts=90_000)

    assert result.mode == "paper"


# -- run_once: EXIT wiring on a held position ---------------------------------------------------


def test_held_position_whose_exit_fires_gets_an_exit_order(repo):
    rule_id = repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000)
    repo.set_state(f"position_rule:{PRODUCT}", "fake_exit")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert len(result.exit_results) == 1
    assert result.exit_results[0].placed is True
    assert result.enter_signals == []  # the fake rule never enters

    orders = repo.get_orders(mode="live", product_id=PRODUCT)
    sell_orders = [o for o in orders if o["side"] == "SELL"]
    assert len(sell_orders) == 1
    assert sell_orders[0]["qty"] == Decimal("0.1")
    # the exit Signal built in `_handle_exits` threads the owning rule's real DB id --
    # same fix as the ENTER path, exercised here on the LIVE EXIT path.
    assert sell_orders[0]["rule_id"] == rule_id

    # the position is no longer tracked as open once the exit is placed.
    assert not repo.get_state(f"position_rule:{PRODUCT}")


def test_held_position_whose_exit_does_not_fire_stays_open(repo):
    repo.insert_rule("fake_no_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000)
    repo.set_state(f"position_rule:{PRODUCT}", "fake_no_exit")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.exit_results == []
    assert repo.get_state(f"position_rule:{PRODUCT}") == "fake_no_exit"
    sell_orders = [
        o for o in repo.get_orders(mode="live", product_id=PRODUCT) if o["side"] == "SELL"
    ]
    assert sell_orders == []


def test_no_held_position_skips_exit_check_entirely(repo):
    """No open position for the product -> `_handle_exits` is a no-op (nothing to sell), even
    though the rule's `exit_signal` would otherwise fire."""
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.exit_results == []


# -- _position_state: entry context -------------------------------------------------------------


def test_opening_a_position_records_entry_context(repo, monkeypatch) -> None:
    """An outcome row later needs opened_at/entry_fill/qty, so the ENTER path must record them."""
    from keel.agent import _position_state

    _seed_open_position(repo, PRODUCT, Decimal("0.5"), Decimal("100"), ts=1_000)
    repo.set_state(
        f"position_rule:{PRODUCT}",
        {
            "rule_name": "turtle_breakout",
            "opened_at": 1_000,
            "entry_fill": Decimal("100"),
            "qty": Decimal("0.5"),
        },
    )

    state = _position_state(repo, PRODUCT)
    assert state is not None
    assert state["rule_name"] == "turtle_breakout"
    assert state["opened_at"] == 1_000
    assert state["entry_fill"] == Decimal("100")
    assert isinstance(state["entry_fill"], Decimal)
    assert state["qty"] == Decimal("0.5")


def test_position_state_tolerates_the_legacy_bare_string(repo) -> None:
    """Existing DBs hold a bare rule-name string; reading one must not crash mid-upgrade."""
    from keel.agent import _position_state

    repo.set_state(f"position_rule:{PRODUCT}", "turtle_breakout")

    state = _position_state(repo, PRODUCT)
    assert state is not None
    assert state["rule_name"] == "turtle_breakout"
    assert state["opened_at"] is None
    assert state["entry_fill"] is None


def test_position_state_is_none_when_unset(repo) -> None:
    """The negative: no tracked position must read as None, not as an empty dict."""
    from keel.agent import _position_state

    assert _position_state(repo, PRODUCT) is None


# -- run_once: stale feed ----------------------------------------------------------------------


def test_stale_feed_skips_trading_for_that_product(repo):
    """No candles ever recorded for the product (`market_feed.is_fresh` -> `False`, "no stored
    candle at all") -- the product is skipped entirely: no evaluation, no exits, no orders."""
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={})  # the feed has nothing to serve

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False  # the cycle itself ran -- only this product was stale
    assert result.stale_products == [PRODUCT]
    assert result.enter_signals == []
    assert result.enter_results == []
    assert repo.get_orders() == []


# -- run_once: no live rules --------------------------------------------------------------------


def test_no_live_rules_is_a_quiet_no_op_cycle(repo):
    broker = FakeBroker()

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert result.products == []
    assert result.polled == 0
    assert result.enter_signals == []
    assert result.exit_results == []
    # the feed-check heartbeat still updates even with nothing to poll.
    assert repo.get_state("last_feed_ts") == 90_000


# -- rule reconstruction (`repo.get_rules("live")` -> real rule instances) ---------------------


def test_build_rule_reconstructs_a_real_pullback_continuation_rule():
    row = {
        "kind": "pullback_continuation",
        "params": {
            "product_id": PRODUCT,
            "granularity": "ONE_HOUR",
            "ema_periods": [8, 20, 50],
            "entry_zone": "ema_touch",
            "signal_patterns": ["pin_bar"],
            "buffer_ticks": "0.02",
            "stop_method": "fixed",
            "target_method": "measured_1to1",
        },
    }

    rule = _build_rule(row)

    assert isinstance(rule, PullbackContinuation)
    assert rule.product_id == PRODUCT
    assert rule.granularity == Granularity.ONE_HOUR
    assert rule.params["buffer_ticks"] == Decimal("0.02")
    assert rule.params["ema_periods"] == (8, 20, 50)


def test_build_rule_populates_rule_id_from_the_row(repo):
    """The Phase-2 debt this branch fixes: `_build_rule` used to discard `row["id"]` entirely,
    which is the root cause of `orders.rule_id` always being written NULL."""
    rule_id = repo.insert_rule("dca", {"product_id": PRODUCT})
    row = repo.get_rules()[0]

    rule = _build_rule(row)

    assert rule.rule_id == rule_id


def test_build_rule_reconstructs_a_turtle_rule_with_its_declared_granularity():
    """The hourly paper profile (#337) stores turtle rows at `ONE_HOUR`; the coercion boundary
    must turn the stored `.value` string back into the enum, exactly as it does for
    `RsiMeanReversion.timeframe`. A row with NO `granularity` key keeps the constructor's
    `ONE_DAY` default, which is what every pre-existing turtle row keeps meaning (pinned in
    tests/strategy/test_turtle_breakout.py, `TestDeclaredGranularity`)."""
    row = {
        "kind": "turtle_breakout",
        "params": {
            "product_id": PRODUCT,
            "granularity": "ONE_HOUR",
            "entry_lookback": 40,
            "atr_stop_mult": "2",
            "target_rr": "6",
        },
    }

    rule = _build_rule(row)

    assert isinstance(rule, TurtleBreakout)
    assert rule.granularity is Granularity.ONE_HOUR
    assert rule.params["atr_stop_mult"] == Decimal("2")


def test_build_rule_leaves_rule_id_none_for_a_row_with_no_id():
    """A hand-built row (no "id" key -- e.g. a caller assembling params directly, not via
    `repo.get_rules()`) must not raise; `rule_id` just stays at its default `None`."""
    row = {
        "kind": "dca",
        "params": {"product_id": PRODUCT},
    }

    rule = _build_rule(row)

    assert rule.rule_id is None


def test_build_rule_unknown_kind_raises():
    with pytest.raises(ValueError, match="dca"):
        _build_rule({"kind": "not_a_real_rule_dca", "params": {}})


# -- the published coercion table (`coerced_param_keys`) --------------------------------------


def test_coerced_param_keys_names_every_param_that_arrives_as_a_string():
    """The whole contract of this function: a caller validating operator-typed JSON (`keel
    rules add`) asks it "may this param be quoted?" and must get a yes for EVERY param
    `build_rule_from_params` converts from a string -- the `Decimal` fields AND the
    `Granularity` field.

    The `Granularity` half is easy to mistake for dead weight, since `Granularity` subclasses
    `str` and a quoted `"ONE_DAY"` therefore survives `rules add`'s string check either way.
    What it actually carries is the ANSWER: `rules add` prints this set verbatim as "a quoted
    value is right only for [...]", and a hint that omitted `timeframe` would tell the operator
    the opposite of the truth about the one param whose whole job is to arrive quoted. See
    `test_the_quotable_hint_names_the_granularity_param` in tests/commands/test_rules_add.py.
    """
    assert agent.coerced_param_keys("rsi_meanrev") == frozenset(
        {
            "atr_mult",
            "fixed_stop_pct",
            "fixed_rr",
            "level_tolerance",
            "support_proximity_pct",
            "trail_atr_mult",
            "be_roll_rr",
            "timeframe",
        }
    )
    assert agent.coerced_param_keys("pullback_continuation") == frozenset(
        {"buffer_ticks", "trail_atr_mult", "be_roll_rr", "granularity"}
    )
    assert agent.coerced_param_keys("dca") == frozenset({"budget_usd", "dip_bonus_pct"})
    assert agent.coerced_param_keys("turtle_breakout") == frozenset(
        {"atr_stop_mult", "target_rr", "granularity"}
    )


def test_coerced_param_keys_covers_exactly_what_build_rule_from_params_converts():
    """Derived from the same declarations the coercion itself reads, so a rule that gains a
    `Decimal` or `Granularity` field cannot be converted on the way in without also being
    published here. A kind with no coerced params at all answers with an empty set, not a
    `KeyError`.

    Reads `Rule.decimal_params`/`granularity_param` off the registered CLASS since #447, where
    the two module-level dicts this used to read moved onto the rules themselves. The property
    is unchanged; the source of truth moved.
    """
    for kind, rule_cls in agent.RULE_REGISTRY.items():
        expected = set(rule_cls.decimal_params)
        if rule_cls.granularity_param is not None:
            expected.add(rule_cls.granularity_param)
        assert agent.coerced_param_keys(kind) == expected, kind
    assert agent.coerced_param_keys("not_a_real_kind") == frozenset()

    # `tuple_params` is deliberately absent from this answer: a tuple param arrives as a JSON
    # LIST, never a quoted string, and `coerced_param_keys` exists to tell `rules add` which
    # values an operator may legitimately quote. Folding tuples in would make `rules add`
    # accept `--params '{"ema_periods": "8"}'` as a well-typed tuple declaration.
    assert "ema_periods" not in agent.coerced_param_keys("pullback_continuation")


# -- loop() wrapper -----------------------------------------------------------------------------


def test_loop_runs_until_stop_flag_and_returns_each_cycle_result(repo):
    """`kill_switch` stays engaged so each `run_once` cycle is a cheap no-op -- this test is
    only exercising `loop()`'s stop-after-N-cycles wiring, not the full trading path."""
    repo.set_state("kill_switch", True)
    broker = FakeBroker()
    calls = {"n": 0}

    def stop_flag() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    results = loop(broker, repo, _config(), interval_sec=0, stop_flag=stop_flag)

    assert len(results) == 3
    assert all(r.skipped for r in results)


def test_loop_stops_immediately_when_stop_flag_is_already_true(repo):
    broker = FakeBroker()

    results = loop(broker, repo, _config(), interval_sec=0, stop_flag=lambda: True)

    assert results == []


# -- rail 11: the equity/drawdown producer must run as part of the CYCLE ----------------------


def test_run_once_writes_the_drawdown_scalars(repo: Repository) -> None:
    """Rail 11's inputs must be produced by the CYCLE, not only by a directly-called helper.

    Without this, `tests/execution/test_equity.py` passes in full while `run_once` never calls
    the producer -- exactly the state that left rail 11 unable to trip for the whole life of the
    project. The unit tests there cannot catch it; only this one can.
    """
    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = FakeBroker(series=series)

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_state("drawdown_total_pct") is not None
    assert repo.get_state("equity_high_water_mark") is not None


def test_run_once_skips_the_drawdown_update_when_the_quote_balance_is_unreadable(
    repo: Repository,
) -> None:
    """Equity is unknowable without the quote balance, and a wrong equity corrupts the
    high-water mark PERMANENTLY -- an HWM cannot be walked back, so an under-read arms the
    breaker on a phantom drawdown forever after. Skip and keep last cycle's scalars instead."""

    class _BrokenBalancesBroker(FakeBroker):
        def get_balances(self) -> list[Balance]:
            raise RuntimeError("broker down")

    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = _BrokenBalancesBroker(series=series)

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_state("equity_high_water_mark") is None
    assert repo.get_state("drawdown_total_pct") is None


def test_paper_to_live_flip_clears_stale_scalars_even_when_broker_unreadable(
    repo: Repository,
) -> None:
    """Pre-live-arming fix: a paper->live flip whose FIRST live cycle reads an unreadable broker
    must still clear the stale paper drawdown scalars, not let them survive a cycle. Regression
    for the asymmetric live-side mode clear (was gated inside the equity-readable branch)."""
    repo.set_state("equity_state_mode", "paper")
    repo.set_state("equity_high_water_mark", Decimal("999999"))
    repo.set_state("drawdown_total_pct", Decimal("0.9"))
    repo.set_state("drawdown_weekly_pct", Decimal("0.5"))

    class _BrokenBalancesBroker(FakeBroker):
        def get_balances(self) -> list[Balance]:
            raise RuntimeError("broker down")

    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = _BrokenBalancesBroker(series=series)

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_state("equity_state_mode") == "live"
    assert repo.get_state("equity_high_water_mark") is None
    assert repo.get_state("drawdown_total_pct") == Decimal("0")
    assert repo.get_state("drawdown_weekly_pct") == Decimal("0")


def test_run_once_computes_a_real_equity_that_moves_rail_11(repo: Repository) -> None:
    """Pins the VALUE, not just the presence, of the scalars.

    `test_run_once_writes_the_drawdown_scalars` only asserts the keys are non-None, so a
    `_mark_to_market_equity` that returned a constant would satisfy it while leaving the breaker
    permanently at 0% in production. Drop the quote balance 30% between two cycles and require
    the drawdown to track it.
    """

    class _DecliningBroker(FakeBroker):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.balance = Decimal("10000")

        def get_balances(self) -> list[Balance]:
            return [Balance(currency="USD", available=self.balance, total=self.balance)]

    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = _DecliningBroker(series=series)
    now = 1_000 + 29 * 86_400

    run_once(broker, repo, _config(), now_ts=now)
    assert repo.get_state("equity_high_water_mark") == Decimal("10000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")

    broker.balance = Decimal("7000")
    run_once(broker, repo, _config(), now_ts=now + 86_400)

    assert repo.get_state("equity_high_water_mark") == Decimal("10000")  # never falls
    assert repo.get_state("drawdown_total_pct") == Decimal("0.3")


# -- the persisted equity series (#698) --------------------------------------------------------


def test_a_live_cycle_records_the_equity_point_with_its_cash_and_unrealized(
    repo: Repository,
) -> None:
    """The agent already computed all three every cycle and persisted none of them. The point
    has to carry the SPLIT, not just the total: a chart that can only draw one line cannot show
    whether an equity move was a position marking up or cash arriving."""
    _seed_open_position(repo, PRODUCT, Decimal("2"), Decimal("100"), ts=1_000)
    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = FakeBroker(series=series)
    now = 1_000 + 29 * 86_400

    run_once(broker, repo, _config(), now_ts=now)

    points = repo.get_equity_points()
    assert len(points) == 1
    point = points[0]
    assert point.mode == "live"
    assert point.ts == now
    # FakeBroker's cash, and 2 units bought at 100 now marked at the candle close of 100.
    assert point.cash == Decimal("1000000")
    assert point.unrealized == Decimal("0")
    assert point.equity == Decimal("1000000") + Decimal("2") * Decimal("100")


def test_a_paper_cycle_records_the_split_off_the_synthetic_account(repo, monkeypatch) -> None:
    """The paper branch's wiring, through the real loop rather than on `PaperTrader` alone.

    The two are different code: `tests/strategy/test_paper.py` proves `get_cash()` and
    `unrealized()` agree with `equity()` on one account state, and this proves the AGENT hands
    those two to `update_drawdown` for the same cycle it read the equity from -- a paper account
    that seeds itself DURING this cycle is exactly where the total and the split could come from
    two different states of the account.
    """
    from keel.strategy.paper import PaperTrader

    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="paper")
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("10000"), now_ts=0)
    repo.set_state("equity_state_mode", "paper")
    trader.on_signal(
        _paper_enter_signal(
            product_id=PRODUCT, entry=Decimal("100"), stop=Decimal("50"), target=Decimal("200"),
            ts=0,
        ),
        qty=Decimal("10"),
    )

    broker = _MarketDataOnlyBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100"), _candle(86_400, "120")]}
    )
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", 86_400)

    run_once(broker, repo, cfg, now_ts=86_400)

    point = repo.get_equity_points()[0]
    assert point.mode == "paper"
    assert point.cash == trader.get_cash()
    assert point.cash is not None and point.unrealized is not None
    # The reconciliation invariant, on the paper side and through the loop: the recorded parts
    # add back to the recorded total. The entry fill is whatever slippage and fees made it, so
    # the cost basis is read back rather than assumed.
    entry_fill = repo.get_orders(mode="paper")[0]["actual_fill"]
    assert point.cash + Decimal("10") * entry_fill + point.unrealized == point.equity


def test_the_live_split_reconciles_against_the_equity_it_was_read_with(repo: Repository) -> None:
    """`cash + cost basis + unrealized == equity`, on ONE read of the account.

    Called directly with an explicit price map, like its neighbours below: through `run_once`
    the map only covers products with a LIVE RULE, so a held-only position takes the cost-basis
    fallback and the marked-up case -- the one where the parts can disagree -- never arises.
    """
    _seed_open_position(repo, PRODUCT, Decimal("2"), Decimal("100"), ts=1_000)
    broker = FakeBroker()

    parts = agent._mark_to_market_parts(
        repo, broker, [PRODUCT], {PRODUCT: Decimal("150")}, "USD"
    )

    assert parts is not None
    cost_basis = Decimal("2") * Decimal("100")
    assert parts.unrealized == Decimal("2") * (Decimal("150") - Decimal("100"))
    assert parts.cash + cost_basis + parts.unrealized == parts.equity
    # And the total is exactly what the existing scalar path reports for the same read.
    assert parts.equity == agent._mark_to_market_equity(
        repo, broker, [PRODUCT], {PRODUCT: Decimal("150")}, "USD"
    )


def test_an_unreadable_equity_records_no_point(repo: Repository) -> None:
    """`_mark_to_market_equity` returns `None` when NO balance could be read -- equity is
    genuinely unknown, and the cycle already declines to touch rail 11's scalars for exactly
    that reason. The series must decline too: a gap in the chart is honest, a point carrying
    last cycle's number dressed as this cycle's reading is not."""

    class _MuteBroker(FakeBroker):
        def get_balances(self) -> list[Balance]:
            return []

    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}

    run_once(_MuteBroker(series=series), repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_equity_points() == []


# -- the persisted cycle_balances series (#719) --------------------------------------------------


def test_a_live_cycle_writes_one_cycle_balance_row_per_currency(repo: Repository) -> None:
    """The engine write site. `FakeBroker.get_balances()` funds both USD and USDC; configuring
    `quote_currency=USDC` while `PRODUCT` (BTC-USD) settles in USD puts both currencies into the
    same cycle's currency set (`agent._mark_to_market_parts`' own union), so a real live cycle
    through `run_once` genuinely has two currencies to observe. Seeding an OPEN POSITION in
    `PRODUCT` (rather than a live rule) puts it into the currency scan via `repo.held_products()`,
    the same path `test_the_live_split_reconciles_against_...` and its neighbours use."""
    _seed_open_position(repo, PRODUCT, Decimal("2"), Decimal("100"), ts=1_000)
    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = FakeBroker(series=series)
    now = 1_000 + 29 * 86_400

    run_once(broker, repo, _config(quote_currency="USDC"), now_ts=now)

    rows = {b.currency: b for b in repo.get_cycle_balances()}
    assert set(rows) == {"USD", "USDC"}
    assert rows["USD"].available == Decimal("1000000")
    assert rows["USD"].total == Decimal("1000000")
    assert rows["USDC"].available == Decimal("1000000")
    assert rows["USDC"].total == Decimal("1000000")


def test_the_cycle_balance_rows_carry_the_same_mode_stamp_as_the_equity_point(
    repo: Repository,
) -> None:
    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = FakeBroker(series=series)
    now = 1_000 + 29 * 86_400

    run_once(broker, repo, _config(), now_ts=now)

    point = repo.get_equity_points()[0]
    balances = repo.get_cycle_balances()
    assert balances, "the live cycle must have written at least one row"
    assert all(b.mode == point.mode == "live" for b in balances)
    assert all(b.ts == point.ts == now for b in balances)


def test_a_paper_cycle_writes_no_cycle_balances(repo, monkeypatch) -> None:
    """Paper has no venue balance to observe -- the paper branch never fetches one, so it must
    never write one either."""
    from keel.strategy.paper import PaperTrader

    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="paper")
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("10000"), now_ts=0)
    repo.set_state("equity_state_mode", "paper")
    trader.on_signal(
        _paper_enter_signal(
            product_id=PRODUCT, entry=Decimal("100"), stop=Decimal("50"), target=Decimal("200"),
            ts=0,
        ),
        qty=Decimal("10"),
    )

    broker = _MarketDataOnlyBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100"), _candle(86_400, "120")]}
    )
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", 86_400)

    run_once(broker, repo, cfg, now_ts=86_400)

    assert repo.get_equity_points(), "the paper cycle must have recorded an equity point"
    assert repo.get_cycle_balances() == []


def test_a_currency_with_no_observed_total_carries_it_as_none_in_the_equity_parts(
    repo: Repository,
) -> None:
    """`_mark_to_market_parts`' new `balances` field relays whatever the matched `Balance`
    itself reports for each leg, `None` included -- it does not invent a number for a leg the
    venue never populated."""

    class _NoTotalBroker:
        def get_balances(self) -> list[Balance]:
            return [Balance(currency="USDC", available=Decimal("500"), total=None)]  # type: ignore[arg-type]

    parts = agent._mark_to_market_parts(repo, _NoTotalBroker(), ["BTC-USD"], {}, "USDC")

    assert parts is not None
    by_currency = {c: (a, t) for c, a, t in parts.balances}
    assert by_currency["USDC"] == (Decimal("500"), None)


def test_the_equity_parts_carry_one_balance_entry_per_currency_in_play(repo: Repository) -> None:
    class TwoCurrencyBroker:
        def get_balances(self) -> list[Balance]:
            return [
                Balance(currency="USD", available=Decimal("1000"), total=Decimal("1000")),
                Balance(currency="USDC", available=Decimal("7"), total=Decimal("9")),
            ]

    parts = agent._mark_to_market_parts(repo, TwoCurrencyBroker(), ["BTC-USD"], {}, "USDC")

    assert parts is not None
    by_currency = {c: (a, t) for c, a, t in parts.balances}
    assert by_currency["USD"] == (Decimal("1000"), Decimal("1000"))
    assert by_currency["USDC"] == (Decimal("7"), Decimal("9"))


# -- rail 11: equity must be valued from the ORDERS LOG, not from position_rule ----------------


def test_equity_counts_the_net_held_qty_across_multiple_buys(repo: Repository) -> None:
    """`position_rule["qty"]` is OVERWRITTEN on every placed entry, never accumulated, so it
    holds the LAST tranche's qty -- not the position. Valuing equity from it undercounts every
    accumulated position (DCA is the acute case: it buys the same product every cycle).

    That matters far more than an ordinary rounding error because the high-water mark is
    MONOTONIC: the undercount manufactures a drawdown that only ever grows, and at
    `max_total_dd_pct` rail 11 vetoes every rule entry permanently on a flat account.
    """
    for i in range(5):
        _seed_open_position(repo, PRODUCT, Decimal("0.5"), Decimal("100"), ts=1_000 + i)
    # what the ENTER path actually writes -- the last leg only
    repo.set_state(
        f"position_rule:{PRODUCT}",
        {
            "rule_name": "dca",
            "opened_at": 1_000,
            "entry_fill": Decimal("100"),
            "qty": Decimal("0.5"),
        },
    )
    broker = FakeBroker()

    equity = agent._mark_to_market_equity(repo, broker, [PRODUCT], {PRODUCT: Decimal("100")}, "USD")

    # 2.5 BTC held, not 0.5 -- cash is FakeBroker's 1_000_000
    assert equity == Decimal("1000000") + Decimal("2.5") * Decimal("100")


def test_equity_counts_a_held_position_whose_rule_is_no_longer_live(repo: Repository) -> None:
    """`products` comes from the LIVE rule set. Retire a rule while its position is still open
    and the holding would vanish from equity in one step -- a cliff-edge phantom drawdown."""
    _seed_open_position(repo, PRODUCT, Decimal("2"), Decimal("100"), ts=1_000)
    broker = FakeBroker()

    equity = agent._mark_to_market_equity(repo, broker, [], {PRODUCT: Decimal("100")}, "USD")

    assert equity == Decimal("1000000") + Decimal("2") * Decimal("100")


def test_equity_falls_back_to_avg_cost_when_a_held_product_has_no_price(
    repo: Repository,
) -> None:
    """Pre-flight fix (C): a held product missing from the price map is valued at its cost
    basis, never dropped -- dropping it understates equity and would trip rail 11 on a DATA GAP
    rather than on a loss. This is the assertion that fix was missing."""
    _seed_open_position(repo, PRODUCT, Decimal("2"), Decimal("100"), ts=1_000)
    broker = FakeBroker()

    equity = agent._mark_to_market_equity(repo, broker, [PRODUCT], {}, "USD")

    assert equity == Decimal("1000000") + Decimal("2") * Decimal("100")


def test_exit_records_a_trade_outcome(repo: Repository) -> None:
    """The LIVE rail-16 producer must be invoked BY THE CYCLE, not only by a directly-called
    helper. Every test in tests/execution/test_streak.py calls `record_closed_trade` itself, so
    all of them would pass while `_handle_exits` never invoked it -- which is precisely how rail
    11 became dormant, and the guard this branch built for the equity and sim producers but not
    for the one that gates real money.

    The pre-existing exit test cannot serve as this guard: it seeds `position_rule` as a legacy
    BARE STRING, which `_position_state` normalises to `entry_fill=None`, which the producer
    deliberately SKIPS. The one integration test reaching this call site takes the branch where
    the producer does nothing.
    """
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(
        repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000, rule_name="fake_exit"
    )
    repo.set_state(f"position_rule:{PRODUCT}", {"rule_name": "fake_exit", "opened_at": 1_000})
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000)

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["rule_name"] == "fake_exit"


def test_a_dca_owned_exit_is_recorded_as_dca_and_never_moves_the_streak(
    repo: Repository,
) -> None:
    """§12.6 exempts DCA from the STREAK, and `_handle_exits` must derive that from the owning
    rule rather than asserting it. Today the exemption holds only because `Dca.exit_signal`
    returns False unconditionally -- a load-bearing invariant in an unrelated module with
    nothing asserting it. If DCA ever gains an exit condition, hardcoding `is_dca=False` would
    silently start counting DCA losses toward a live-money breaker.
    """

    class _ExitingDca(Rule):
        name = "dca"
        params: dict = {}
        product_id = PRODUCT

        def detect(self, candles_by_tf):
            return None

        def exit_signal(self, held, candles_by_tf):
            return True

        def describe(self):
            return {"name": self.name, "params": self.params}

    # The CLASS, not a `lambda **kw: _ExitingDca()` factory. `RULE_REGISTRY` is declared
    # `dict[str, type[Rule]]`, and since #447 `build_rule_from_params` reads the coercion
    # declarations (`Rule.decimal_params` and friends) off the registered class before it
    # constructs anything -- a bare function has no such attributes, so the type annotation
    # this test used to disregard is now load-bearing. `_ExitingDca` takes no constructor
    # arguments and the seeded row carries empty params, so it is a drop-in.
    agent.RULE_REGISTRY["dca"] = _ExitingDca
    try:
        repo.insert_rule("dca", {}, status="live")
        _seed_open_position(
            repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000, rule_name="dca"
        )
        repo.set_state(
            f"position_rule:{PRODUCT}",
            {"rule_name": "dca", "opened_at": 1_000},
        )
        broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

        run_once(broker, repo, _config(), now_ts=90_000)
    finally:
        agent.RULE_REGISTRY["dca"] = Dca

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["is_dca"] is True  # recorded as DCA...
    assert repo.get_state("consecutive_losses", default=0) == 0  # ...and exempt from the streak


def test_the_enter_path_records_the_entry_fee_onto_the_position(repo: Repository) -> None:
    """Holds the WIRING of the entry-fee half of the "pnl_net was GROSS" fix.

    `record_closed_trade` does `position.get("entry_fee") or Decimal("0")`, so if the ENTER path
    stops writing it, `pnl_net` silently reverts to net-of-EXIT-fee-only -- restoring the exact
    Critical this branch fixed, where a fee-dominated loser is recorded as a WIN and RESETS the
    loss counter.

    Every other test that touches `entry_fee` hand-seeds it into a fixture, which makes them
    vacuous with respect to the producer. This one exercises the real ENTER path and asserts the
    value ARRIVES -- the difference between testing arithmetic and testing wiring. It now reads
    the `positions` ledger, which is where the entry context moved; the guard is relocated, not
    weakened.
    """
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000)

    tranches = repo.get_open_positions(PRODUCT)
    assert tranches, "no entry was placed -- the fixture no longer exercises ENTER"
    assert tranches[0]["entry_fee"] is not None, (
        "the ENTER path stopped recording the entry fee; pnl_net will silently revert to "
        "net-of-exit-fee-only"
    )


def test_run_once_reconciles_a_filled_bracket(repo: Repository) -> None:
    """The reconciliation pass must run as part of the CYCLE, not only when called directly.

    Same guard as the equity and streak producers, for the same reason: every test in
    tests/execution/test_reconcile.py calls `reconcile_open_orders` itself and would pass in
    full while `run_once` never invoked it -- leaving stop-outs invisible to rails 11 and 16
    exactly as before.
    """
    _seed_open_position(repo, PRODUCT, Decimal("0.01"), Decimal("50000"), ts=1_000)
    bracket_id = repo.insert_order(
        dict(
            mode="live",
            product_id=PRODUCT,
            side=Side.SELL.value,
            order_type="market",
            qty=Decimal("0.01"),
            limit_price=None,
            status="pending",
            fee=None,
            expected_fill=Decimal("49000"),
            actual_fill=None,
            raw_response='{"order_id": "cb-1"}',
            created_at=1_000,
            updated_at=1_000,
        )
    )
    repo.set_state(
        f"position_rule:{PRODUCT}",
        {
            "rule_name": "turtle_breakout",
            "opened_at": 1_000,
        },
    )
    repo.open_position(
        product_id=PRODUCT,
        rule_name="turtle_breakout",
        opened_at=1_000,
        qty=Decimal("0.01"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("3"),
        bracket_order_id=bracket_id,
    )

    class _ReconcilingBroker(FakeBroker):
        def get_order(self, order_id: str) -> OrderStatus:
            return OrderStatus(
                order_id=order_id,
                status="FILLED",
                filled_size=Decimal("0.01"),
                average_filled_price=Decimal("48900"),
                total_fees=Decimal("2.93"),
            )

    broker = _ReconcilingBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    )

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_order(bracket_id)["status"] == "filled"
    assert len(repo.get_trade_outcomes()) == 1
    assert repo.get_state(f"position_rule:{PRODUCT}") is None


def test_run_once_brackets_a_tranche_whose_bracket_was_never_placed(repo: Repository) -> None:
    """The wiring guard for the unbracketed sweep, and the reason it matters more than most.

    Every test in tests/execution/test_reconcile.py calls `reconcile_unbracketed_positions`
    directly and would pass in full while `run_once` never invoked it -- leaving the exact hole
    the pass was written to close wide open (issue #195). A fix that is not on the cycle path
    protects nothing.
    """
    _seed_open_position(repo, PRODUCT, Decimal("0.01"), Decimal("50000"), ts=1_000)
    repo.set_state(
        f"position_rule:{PRODUCT}",
        {
            "rule_name": "turtle_breakout",
            "opened_at": 1_000,
        },
    )
    # A tranche naming NO bracket: the entry filled, the protective order was refused.
    position_id = repo.open_position(
        product_id=PRODUCT,
        rule_name="turtle_breakout",
        opened_at=1_000,
        qty=Decimal("0.01"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("3"),
        bracket_order_id=None,
    )
    repo.set_state(
        f"unbracketed:{PRODUCT}",
        {
            "stop": Decimal("49000"),
            "target": Decimal("53000"),
            "qty": Decimal("0.01"),
        },
    )
    broker = FakeBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    )

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    tranche = [p for p in repo.get_open_positions(PRODUCT) if p["id"] == position_id]
    assert tranche, "the tranche was closed rather than protected"
    assert tranche[0]["bracket_order_id"] is not None, (
        "run_once left a held tranche with no bracket -- the sweep is not on the cycle path"
    )
    assert repo.get_state(f"unbracketed:{PRODUCT}") is None


# -- live stop management (#502 stage 2): the per-cycle step, default off --------------------
#
# The state these tests seed is what a real entry leaves behind: held inventory in the orders
# audit log, ONE resting exchange-side bracket, the `open_stop:`/`open_target:` pair
# `place_bracket` records, and a `positions` tranche naming the bracket and carrying the
# ORIGINAL setup stop (`initial_stop`, #520) the break-even threshold is measured from.


def _seed_bracketed_tranche(
    repo: Repository,
    *,
    rule_name: str = "pullback_continuation",
    product: str = PRODUCT,
    bracket_ref: str = "cb-bracket-1",
    entry: Decimal = Decimal("50000"),
    initial_stop: Decimal | None = Decimal("49000"),
    qty: Decimal = Decimal("0.01"),
    target: Decimal = Decimal("55000"),
    ts: int = 1_000,
) -> int:
    """Held inventory + a resting bracket + the tranche row that owns it. Returns the bracket's
    order id. `initial_stop=None` seeds the pre-#520 TRANCHE shape the ledger documents as "BE
    arm disabled", not "zero" -- while the resting bracket's own stop (`open_stop:` state and
    the order row's `expected_fill`) stays a real number, because it always is: the bracket
    exists, so it rests at some level. `bracket_ref` is the broker-side order id the cancel
    path reads out of `raw_response` -- distinct per product so a multi-product test can tell
    whose cancel was whose."""
    stop = initial_stop if initial_stop is not None else Decimal("49000")
    _seed_open_position(repo, product, qty, entry, ts=ts)
    bracket_id = repo.insert_order(
        dict(
            mode="live",
            product_id=product,
            side=Side.SELL.value,
            order_type="market",
            qty=qty,
            limit_price=None,
            status="pending",
            fee=None,
            expected_fill=stop,
            actual_fill=None,
            raw_response=f'{{"order_id": "{bracket_ref}"}}',
            created_at=ts,
            updated_at=ts,
        )
    )
    repo.set_state(f"position_rule:{product}", {"rule_name": rule_name, "opened_at": ts})
    repo.open_position(
        product_id=product,
        rule_name=rule_name,
        opened_at=ts,
        qty=qty,
        entry_fill=entry,
        entry_fee=Decimal("0"),
        initial_stop=initial_stop,
        bracket_order_id=bracket_id,
    )
    repo.set_state(f"open_stop:{product}", stop)
    repo.set_state(f"open_target:{product}", target)
    return bracket_id


def _ranged_candle(ts: int, low: Decimal, high: Decimal, close: Decimal, open_: Decimal) -> Candle:
    return Candle(ts=ts, open=open_, high=high, low=low, close=close, volume=Decimal("1"))


#: Bar timestamps are exact multiples of ONE_DAY and `now` sits one full period past the last
#: bar, so the whole series is CLOSED and STORED by `market_feed.poll_once` and the product is
#: FRESH by `is_fresh`'s own arithmetic (age = one bar + 1,000s < 3 cycles at interval 50,000).
#: The pre-existing `1_000 + i * 86_400` pattern leaves bars OFF the day grid, the last stored
#: bar a full period older, and the product STALE -- which skips the step for a reason these
#: tests are not about.
def _bar_ts(i: int) -> int:
    return (i + 1) * 86_400


def _management_now_ts(n_bars: int = 30) -> int:
    return (n_bars + 1) * 86_400 + 1_000


def _rising_series(
    n: int = 30, base: Decimal = Decimal("50000"), step: Decimal = Decimal("100")
) -> list[Candle]:
    """A steady climb with a real high/low range, so Wilder ATR is non-zero and the trail arm
    has something to trail. Bar i: closes at `base + i*step`, +/-60 around it."""
    return [
        _ranged_candle(
            _bar_ts(i),
            low=base + i * step - Decimal("60"),
            high=base + i * step + Decimal("60"),
            close=base + i * step,
            open_=base + (i - 1) * step if i else base,
        )
        for i in range(n)
    ]


def _seed_history(repo: Repository, series: list[Candle], product: str = PRODUCT) -> None:
    """Pre-store the series in the candles table. `market_feed.poll_once` cold-starts an EMPTY
    table with only the newest closed bar (its catch-up start is `latest_closed`), so a test
    that means "a deployment with ATR history" must seed the table itself -- the cycle's poll
    then finds the tail current and stores nothing new."""
    repo.upsert_candles(product, Granularity.ONE_DAY, series)


def test_run_once_leaves_stops_alone_when_the_rule_carries_no_exit_knobs(repo: Repository) -> None:
    """THE DEFAULTS-OFF PIN. A rule whose params carry neither `trail_atr_mult` nor `be_roll_rr`
    gets `EXIT_POLICY_OFF` (see `strategy.exit_policy.policy_for`), and the management step
    must leave its position byte-for-byte as the pre-#502 cycle did: no roll attempt (no
    cancel at the broker), the resting bracket untouched, the recorded levels unchanged. This
    is every rule row in existence today unless an operator opts a rule in."""
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY"},
        status="live",
    )
    bracket_id = _seed_bracketed_tranche(repo)
    series = _rising_series()
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=_management_now_ts())

    assert "cancel" not in broker.events, "a knob-less rule had its bracket rolled"
    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("49000")
    assert repo.get_state(f"open_target:{PRODUCT}") == Decimal("55000")
    assert repo.get_order(bracket_id)["status"] == "pending"
    tranche = repo.get_open_positions(PRODUCT)[0]
    assert tranche["bracket_order_id"] == bracket_id


def test_run_once_trails_a_ratcheting_stop_through_the_broker(repo: Repository) -> None:
    """OPTED IN (`trail_atr_mult`), the cycle manages the held position's bracket with the SAME
    policy the sim/backtest engines apply: `next_stop` on the latest COMPLETED bar (poll stores
    closed candles only), then ONE roll -- #519's cancel-before-place protocol -- so the venue's
    bracket ratchets with the climb. The expected level is computed here with the policy's own
    functions: the live step's contract is fidelity to `strategy.exit_policy`, not a re-derived
    trail of its own.

    57 bars is the young-table warmup threshold (`4 x 14 + 1`, the default ATR period) -- the
    threshold side of the boundary `test_the_trail_arm_waits_for_a_warm_candle_table` pins
    from below."""
    from keel.strategy.exit_policy import next_stop, policy_for, trailing_atr

    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "trail_atr_mult": "1.5"},
        status="live",
    )
    # A 57-bar climb trails to ~55.4k, past the default 55k target -- the at/above-target
    # refusal (a stop that has caught the target is a coin flip) would veto the roll these
    # tests exist to observe, so the fixture's target sits above the trail's reach.
    bracket_id = _seed_bracketed_tranche(repo, target=Decimal("60000"))
    series = _rising_series(57)
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=_management_now_ts(57))

    policy = policy_for(_build_rule(repo.get_rules("live")[0]))
    expected = next_stop(
        policy,
        Decimal("50000"),
        Decimal("49000"),
        Decimal("49000"),
        series[-1],
        trailing_atr(series, policy.atr_period),
    )
    assert expected > Decimal("49000"), "the fixture must climb far enough to trail"
    assert repo.get_state(f"open_stop:{PRODUCT}") == expected
    # The #519 protocol through the cycle: the roll cancels the old bracket, then places.
    assert broker.events[-2:] == ["cancel", "place"], broker.events
    assert repo.get_order(bracket_id)["status"] == "canceled"
    replacement_id = repo.get_open_positions(PRODUCT)[0]["bracket_order_id"]
    assert replacement_id is not None and replacement_id != bracket_id
    assert repo.get_order(replacement_id)["status"] == "pending"
    assert repo.get_state(f"open_target:{PRODUCT}") == Decimal("60000")


def test_the_live_trail_never_widens_the_stop(repo: Repository) -> None:
    """Rail 9's invariant on the live path: a falling cycle proposes a trail BELOW the recorded
    stop, and the step must not roll at all -- the existing bracket stays resting, the recorded
    stop unmoved. `next_stop` is ratchet-only by construction; this pins that the LIVE step
    inherits it (it only rolls when the level strictly improves). 57 bars so the trail arm is
    PAST warmup and genuinely proposing -- a no-roll here is the ratchet refusing, not the
    warmup gate muting the arm."""
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "trail_atr_mult": "1.5"},
        status="live",
    )
    bracket_id = _seed_bracketed_tranche(repo, initial_stop=Decimal("52000"))
    falling = [
        _ranged_candle(
            _bar_ts(i),
            low=Decimal("53000") - Decimal("120") * i - Decimal("60"),
            high=Decimal("53000") - Decimal("120") * i + Decimal("60"),
            close=Decimal("53000") - Decimal("120") * i,
            open_=Decimal("53000") - Decimal("120") * (i - 1) if i else Decimal("53000"),
        )
        for i in range(57)
    ]
    _seed_history(repo, falling)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): falling})

    run_once(broker, repo, _config(), now_ts=_management_now_ts(57))

    assert "cancel" not in broker.events
    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("52000")
    assert repo.get_order(bracket_id)["status"] == "pending"


def test_run_once_rolls_to_break_even_once_the_trade_reaches_be_roll_rr(repo: Repository) -> None:
    """The other arm, opted in alone: a bar whose HIGH clears `entry + be_roll_rr x` the
    ORIGINAL per-unit risk (the tranche's `initial_stop`, #520 -- never the already-raised
    current stop) rolls the stop to the entry. 30 bars also pins that the BE arm is NOT
    young-table-gated (see `_manage_stops`'s warmup note): it reads only the latest bar's
    high, exactly what the sim does from bar one."""
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "be_roll_rr": "1"},
        status="live",
    )
    bracket_id = _seed_bracketed_tranche(repo)
    series = [
        _ranged_candle(
            _bar_ts(i),
            low=Decimal("49950"),
            high=Decimal("50050"),
            close=Decimal("50000"),
            open_=Decimal("50000"),
        )
        for i in range(29)
    ] + [
        # high 51200 clears entry + 1R (50000 + 1 * (50000 - 49000) = 51000)
        _ranged_candle(
            _bar_ts(29),
            low=Decimal("50300"),
            high=Decimal("51200"),
            close=Decimal("50800"),
            open_=Decimal("50000"),
        )
    ]
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=_management_now_ts())

    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("50000")
    assert repo.get_order(bracket_id)["status"] == "canceled"
    assert broker.events[-2:] == ["cancel", "place"], broker.events


def test_a_tranche_without_initial_stop_disables_the_break_even_arm(repo: Repository) -> None:
    """The ledger's own contract (`Repository.open_position` / #520's migration): `initial_stop
    IS NULL` means "nobody recorded it" -- pre-ledger tranches and DCA -- and the BE arm must
    switch OFF rather than substitute the current stop, which is a different policy (and, on a
    ratcheted position, a guaranteed profit-stealing roll to a stale level)."""
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "be_roll_rr": "1"},
        status="live",
    )
    bracket_id = _seed_bracketed_tranche(repo, initial_stop=None)
    series = [
        _ranged_candle(
            _bar_ts(i),
            low=Decimal("49950"),
            high=Decimal("50050"),
            close=Decimal("50000"),
            open_=Decimal("50000"),
        )
        for i in range(29)
    ] + [
        _ranged_candle(
            _bar_ts(29),
            low=Decimal("50300"),
            high=Decimal("51200"),
            close=Decimal("50800"),
            open_=Decimal("50000"),
        )
    ]
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=_management_now_ts())

    assert "cancel" not in broker.events
    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("49000")
    assert repo.get_order(bracket_id)["status"] == "pending"


def test_turtle_positions_are_never_managed(repo: Repository) -> None:
    """`policy_for` reads turtle as OFF by DESIGN (#442 hypothesis 3): its real exit is the
    asymmetric Donchian channel, and a trail would cut the rare long winners a low-win-rate
    trend-follower exists to let run. The family carries neither knob and cannot express
    them."""
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT}, status="live")
    bracket_id = _seed_bracketed_tranche(repo, rule_name="turtle_breakout")
    flat = [
        _ranged_candle(
            _bar_ts(i),
            low=Decimal("49950"),
            high=Decimal("50050"),
            close=Decimal("50000"),
            open_=Decimal("50000"),
        )
        for i in range(30)
    ]
    _seed_history(repo, flat)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): flat})

    run_once(broker, repo, _config(), now_ts=_management_now_ts())

    assert "cancel" not in broker.events
    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("49000")
    assert repo.get_order(bracket_id)["status"] == "pending"


def test_paper_cycles_do_not_manage_exchange_brackets(repo: Repository) -> None:
    """Paper mode never places exchange-side brackets (its entries resolve on the synthetic
    account), so there is nothing for a live step to roll -- and touching the broker's real
    order book from the paper path would be a category error. The step is live-mode only."""
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "trail_atr_mult": "1.5"},
        status="paper",
    )
    bracket_id = _seed_bracketed_tranche(repo)
    # The SAME data the trail test rolls on -- so "no roll" here proves the PAPER gate blocked
    # the step, not an unlucky series.
    series = _rising_series()
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(
        broker,
        repo,
        _config(auto_trade=AutoTradeConfig(mode="paper", interval_sec=50_000)),
        now_ts=_management_now_ts(),
    )

    assert "cancel" not in broker.events
    assert broker.place_calls == []
    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("49000")
    assert repo.get_order(bracket_id)["status"] == "pending"


def test_a_family_row_only_manages_the_tranches_of_its_own_product(repo: Repository) -> None:
    """PRODUCT-SCOPED ownership (#502 review): the rules table holds ONE row per
    (kind, product) and `Rule.name` is the FAMILY name, so resolving a tranche's policy by
    name alone governed a multi-product family's every tranche under whichever same-family
    row loaded last -- here the ETH row (id 1, knob-less) loses a name-only dict to the BTC
    row (id 2, opted in), and BTC's trail policy would have rolled ETH's bracket on a climb
    ETH never asked for. The step keys rows by `(product_id, name)` -- the same product
    scoping `_handle_exits` uses -- so each product's tranche answers to its OWN row."""
    eth = "ETH-USD"
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": eth, "granularity": "ONE_DAY"},
        status="live",
    )
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "trail_atr_mult": "1.5"},
        status="live",
    )
    btc_bracket = _seed_bracketed_tranche(
        repo, bracket_ref="btc-bracket-1", target=Decimal("60000")
    )
    # target=60000, NOT the 55000 default: the 57-bar climb trails to ~55.4k, and under a
    # name-only key the leaked roll would be vetoed by the at/above-target refusal before any
    # cancel -- leaving the test green against the very bug it exists to pin (the at/above-target
    # guard fires before the cancel, so the fixture must stay clear of it on BOTH products).
    eth_bracket = _seed_bracketed_tranche(
        repo, product=eth, bracket_ref="eth-bracket-1", ts=2_000, target=Decimal("60000")
    )
    # The SAME climb on both products: under a name-only key this series rolls ETH too.
    series = _rising_series(57)
    _seed_history(repo, series)
    _seed_history(repo, series, product=eth)
    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): series,
            (eth, Granularity.ONE_DAY): series,
        }
    )

    run_once(broker, repo, _config(), now_ts=_management_now_ts(57))

    assert "eth-bracket-1" not in broker.cancel_calls, (
        "the knob-less product's tranche was managed under the opted-in row's policy"
    )
    assert repo.get_order(eth_bracket)["status"] == "pending"
    assert repo.get_state(f"open_stop:{eth}") == Decimal("49000")
    assert "btc-bracket-1" in broker.cancel_calls, "the opted-in product's tranche was not managed"
    assert repo.get_order(btc_bracket)["status"] == "canceled"
    assert repo.get_state(f"open_stop:{PRODUCT}") > Decimal("49000")


def test_a_tranche_whose_owning_row_is_absent_this_cycle_is_skipped(repo: Repository) -> None:
    """The dict-miss path, pinned: the tranche names a family whose row is not on this
    cycle's (product, name) set -- demoted, retired, or simply never existed -- and the step
    skips it rather than falling back to any OTHER row's policy, not even the same
    product's different family's row (the opted-in `pullback_continuation` here must not
    adopt a tranche opened by `rsi_meanrev`)."""
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "trail_atr_mult": "1.5"},
        status="live",
    )
    bracket_id = _seed_bracketed_tranche(repo, rule_name="rsi_meanrev")
    series = _rising_series(57)
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=_management_now_ts(57))

    assert "cancel" not in broker.events, "a tranche was managed under a row it does not own"
    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("49000")
    assert repo.get_order(bracket_id)["status"] == "pending"


def test_management_skips_and_logs_when_the_rules_timeframe_has_no_candles(
    repo: Repository, caplog
) -> None:
    """The empty-timeframe path, pinned: the rule declares ONE_HOUR, this deployment polls
    ONE_DAY only, so the management series is empty and the tranche waits a cycle WITH an
    INFO skip -- never rolling on a bar that does not exist."""
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": PRODUCT, "granularity": "ONE_HOUR", "trail_atr_mult": "1.5"},
        status="live",
    )
    bracket_id = _seed_bracketed_tranche(repo)
    series = _rising_series(57)
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    with caplog.at_level(logging.INFO):
        run_once(broker, repo, _config(), now_ts=_management_now_ts(57))

    skips = [
        (record.getMessage(), getattr(record, _FIELDS_ATTR, {}))
        for record in caplog.records
        if record.getMessage() == "agent.stop_management_skipped"
    ]
    assert skips == [
        (
            "agent.stop_management_skipped",
            {
                "product": PRODUCT,
                "rule": "pullback_continuation",
                "reason": "no candles on the rule's trading timeframe",
            },
        )
    ]
    assert "cancel" not in broker.events
    assert repo.get_order(bracket_id)["status"] == "pending"


def test_the_trail_arm_waits_for_a_warm_candle_table(repo: Repository, caplog) -> None:
    """Young-table warmup (#502 review): the trail level only matches the #442-measured sim
    behavior once the table holds `4 x atr_period + 1` closed bars, and the live table does
    not start there (`poll_once` cold-starts at ONE bar; backfill has no production caller).
    56 bars -- one short of 4 x 14 + 1 -- carries the trail knob but must NOT roll, and must
    say so at INFO once per product, not once per cycle (the second cycle stays quiet). The
    threshold side of the boundary is
    `test_run_once_trails_a_ratcheting_stop_through_the_broker` (57 bars, rolls)."""
    warmup_product = "WARM-USD"  # unique: the once-per-product notice set is module state
    repo.insert_rule(
        "pullback_continuation",
        {"product_id": warmup_product, "granularity": "ONE_DAY", "trail_atr_mult": "1.5"},
        status="live",
    )
    bracket_id = _seed_bracketed_tranche(repo, product=warmup_product)
    series = _rising_series(56)
    _seed_history(repo, series, product=warmup_product)
    broker = FakeBroker(series={(warmup_product, Granularity.ONE_DAY): series})

    with caplog.at_level(logging.INFO):
        run_once(broker, repo, _config(), now_ts=_management_now_ts(56))
        run_once(broker, repo, _config(), now_ts=_management_now_ts(56) + 500)

    notices = [
        record
        for record in caplog.records
        if record.getMessage() == "agent.stop_management_waiting_for_warmup"
    ]
    assert len(notices) == 1, "the warmup notice is per product, not per cycle"
    assert getattr(notices[0], _FIELDS_ATTR, {}) == {
        "product": warmup_product,
        "rule": "pullback_continuation",
        "bars": 56,
        "needed": 57,
    }
    assert "cancel" not in broker.events, "a young table rolled a trail anyway"
    assert repo.get_state(f"open_stop:{warmup_product}") == Decimal("49000")
    assert repo.get_order(bracket_id)["status"] == "pending"


def test_a_failed_roll_is_loud_and_isolated_never_a_dead_cycle(repo: Repository, caplog) -> None:
    """PER-TRANCHE ISOLATION (#502 review): a cancel that raises mid-roll -- `CancelPending`/
    `CancelUnavailable` are ordinary Coinbase batch-cancel outcomes when a fill lands during
    the roll -- must not abort `run_once`. A dead cycle AFTER entries were placed costs the
    `LoopResult`, the post-cycle notify, and the live-run wrapper's UTC day-stamp, so the
    next trigger re-runs the whole cycle into the duplicate-entry window. The failure is
    CRITICAL (the roll may be half-completed on live money), the crash-ledger record stands
    for the sweep to heal from, and the LATER tranche is still managed."""
    eth = "ETH-USD"
    for product in (PRODUCT, eth):
        repo.insert_rule(
            "pullback_continuation",
            {"product_id": product, "granularity": "ONE_DAY", "trail_atr_mult": "1.5"},
            status="live",
        )
    btc_bracket = _seed_bracketed_tranche(
        repo, bracket_ref="btc-bracket-1", target=Decimal("60000")
    )
    eth_bracket = _seed_bracketed_tranche(
        repo, product=eth, bracket_ref="eth-bracket-2", ts=2_000, target=Decimal("60000")
    )
    series = _rising_series(57)
    _seed_history(repo, series)
    _seed_history(repo, series, product=eth)

    class _CancelDeniedBroker(FakeBroker):
        def cancel_order(self, order_id: str) -> bool:
            if order_id == "btc-bracket-1":
                raise RuntimeError("batch cancel unavailable (fill in flight)")
            return super().cancel_order(order_id)

    broker = _CancelDeniedBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): series,
            (eth, Granularity.ONE_DAY): series,
        }
    )

    with caplog.at_level(logging.CRITICAL):
        result = run_once(broker, repo, _config(), now_ts=_management_now_ts(57))

    assert result.skipped is False, "a failed roll killed the whole cycle"
    failures = [
        record
        for record in caplog.records
        if record.getMessage() == "agent.stop_management_roll_failed"
    ]
    assert len(failures) == 1
    assert getattr(failures[0], _FIELDS_ATTR, {})["product"] == PRODUCT
    assert getattr(failures[0], _FIELDS_ATTR, {})["old_stop_order_id"] == btc_bracket
    assert failures[0].exc_info, "a CRITICAL half-completed live-money action carries the traceback"
    # The raise lands inside the executor's cancel, BEFORE the local `canceled` mark, so
    # the old bracket still rests and the crash ledger stands for the next cycle's sweep.
    assert repo.get_order(btc_bracket)["status"] == "pending"
    assert repo.get_state(f"unbracketed:{PRODUCT}") is not None
    # The LATER tranche was still managed -- isolation, not abandonment.
    assert "eth-bracket-2" in broker.cancel_calls
    assert repo.get_order(eth_bracket)["status"] == "canceled"
    assert repo.get_state(f"open_stop:{eth}") > Decimal("49000")


def test_both_knobs_resolve_to_one_roll_per_cycle(repo: Repository) -> None:
    """ONE roll, not one per arm (#502 review): a rule carrying BOTH knobs can win on both
    in the same cycle, and the step must walk #519's cancel-before-place window exactly
    once -- `next_stop` takes the max over the arms and `roll_stop_to` places exactly one
    replacement, never `trail_stop_atr` and `roll_to_break_even` back to back."""
    from keel.strategy.exit_policy import next_stop, policy_for, trailing_atr

    repo.insert_rule(
        "pullback_continuation",
        {
            "product_id": PRODUCT,
            "granularity": "ONE_DAY",
            "trail_atr_mult": "1.5",
            "be_roll_rr": "1",
        },
        status="live",
    )
    # A 57-bar climb trails to ~55.4k, past the default 55k target -- the at/above-target
    # refusal (a stop that has caught the target is a coin flip) would veto the roll these
    # tests exist to observe, so the fixture's target sits above the trail's reach.
    bracket_id = _seed_bracketed_tranche(repo, target=Decimal("60000"))
    series = _rising_series(57)
    _seed_history(repo, series)
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): series})

    run_once(broker, repo, _config(), now_ts=_management_now_ts(57))

    policy = policy_for(_build_rule(repo.get_rules("live")[0]))
    expected = next_stop(
        policy,
        Decimal("50000"),
        Decimal("49000"),
        Decimal("49000"),
        series[-1],
        trailing_atr(series, policy.atr_period),
    )
    # Both arms genuinely contended: the climb clears the BE threshold (the last bar's high
    # is ~55,660 >> 51,000) and the trail proposes higher still.
    assert expected > Decimal("50000")
    assert broker.events == ["cancel", "place"], broker.events
    assert repo.get_state(f"open_stop:{PRODUCT}") == expected
    assert repo.get_order(bracket_id)["status"] == "canceled"
    assert repo.get_open_positions(PRODUCT)[0]["bracket_order_id"] is not None


def test_a_rule_exit_records_one_outcome_per_tranche(repo: Repository) -> None:
    """The other half of the per-tranche ledger, and the half the plan originally left behind.

    A rule exit sells the WHOLE held position, so it closes every open tranche. Booking one
    aggregate outcome against a single blob of entry context is the same mis-attribution the
    reconcile path had: here tranche 1 (50000) is a WINNER and tranche 2 (52000) a LOSER at the
    51000 average exit, and collapsing them to one outcome against the average reports a flat
    trade -- hiding a loss rail 16 is supposed to count.
    """
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(
        repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000, rule_name="fake_exit"
    )
    _seed_open_position(
        repo, PRODUCT, Decimal("0.1"), Decimal("52000"), ts=2_000, rule_name="fake_exit"
    )
    repo.set_state(f"position_rule:{PRODUCT}", {"rule_name": "fake_exit", "opened_at": 1_000})
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000)

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 2, "the tranches were collapsed into one aggregate outcome"
    assert [o["entry_fill"] for o in outcomes] == [Decimal("50000"), Decimal("52000")]
    # exit is the 51000 average cost basis: +100 on the first tranche, -100 on the second.
    assert [o["pnl_net"] for o in outcomes] == [Decimal("100.0"), Decimal("-100.0")]
    # the LOSER must reach the streak; an aggregate flat outcome would have counted nothing.
    assert repo.get_state("consecutive_losses", default=0) == 1
    assert repo.get_open_positions(PRODUCT) == []


def test_a_rule_exit_apportions_the_exit_fee_across_tranches(repo: Repository) -> None:
    """One exit order carries ONE fee for the whole sale. Charging it to every tranche would
    multiply the cost by the tranche count; charging it to none would make `pnl_net` gross on
    the exit leg -- and its SIGN is what rail 16 counts. The shares must sum to the whole."""
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(
        repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000, rule_name="fake_exit"
    )
    _seed_open_position(
        repo, PRODUCT, Decimal("0.3"), Decimal("50000"), ts=2_000, rule_name="fake_exit"
    )

    class _FeeBroker(FakeBroker):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.commission = Decimal("4.00")

    repo.set_state(f"position_rule:{PRODUCT}", {"rule_name": "fake_exit", "opened_at": 1_000})
    broker = _FeeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000)

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 2
    # 0.1 and 0.3 of a 0.4 position -> a 1:3 split of the 4.00 fee.
    assert [o["fees"] for o in outcomes] == [Decimal("1.00000000"), Decimal("3.00000000")]
    assert sum(o["fees"] for o in outcomes) == Decimal("4.00")


def test_a_rule_exit_clears_the_stop_and_target_state(repo: Repository) -> None:
    """`open_stop`/`open_target` describe a bracket that no longer exists once the position is
    exited. Left behind they poison the NEXT trade in that product: rail 9 (no stop widening)
    vetoes a legitimate entry whose stop sits below the previous, closed trade's stop, and
    `_handle_exits` builds its held setup from a dead trade's stop.

    They were cleared on the reconcile path only -- the rule-exit path cleared `position_rule`
    and left them.
    """
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(
        repo,
        PRODUCT,
        Decimal("0.1"),
        Decimal("50000"),
        ts=1_000,
        rule_name="fake_exit",
        entry_fee=Decimal("3"),
    )
    repo.set_state(f"position_rule:{PRODUCT}", {"rule_name": "fake_exit", "opened_at": 1_000})
    repo.set_state(f"open_stop:{PRODUCT}", Decimal("49000"))
    repo.set_state(f"open_target:{PRODUCT}", Decimal("53000"))
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_state(f"position_rule:{PRODUCT}") is None
    assert repo.get_state(f"open_stop:{PRODUCT}") is None
    assert repo.get_state(f"open_target:{PRODUCT}") is None


# -- paper mode (KB: the proving gate's evidence source) ------------------------


class _MarketDataOnlyBroker(FakeBroker):
    """Paper's real contract: it may read MARKET DATA, but must never place an order or read
    ACCOUNT state.

    Polling fresh candles legitimately needs the venue -- the same read-only market-data access
    `keel fetch` uses. What paper must not do is place orders or touch balances/positions, so
    those explode.
    """

    def place_order(self, *a, **k):
        raise AssertionError("paper mode placed an order")

    def preview_order(self, *a, **k):
        raise AssertionError("paper mode previewed an order")

    def get_balances(self, *a, **k):
        raise AssertionError("paper mode read account state")


class _AlwaysEnterRule(Rule):
    """Fires an ENTER on every bar, so one cycle is enough to exercise the paper fill path."""

    def __init__(self, product_id: str, name: str = "fake_enter", stop_mult: str = "0.95") -> None:
        self.name = name
        self.product_id = product_id
        self.stop_mult = Decimal(stop_mult)
        self.params: dict = {"product_id": product_id}

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        candles = next((c for c in candles_by_tf.values() if c), [])
        if not candles:
            return None
        last = candles[-1]
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=last.close,
            stop=last.close * self.stop_mult,
            target=last.close * Decimal("1.15"),
            context={},
            ts=last.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


def _paper_config(**over):
    # Roomy caps: these tests are about the PAPER ROUTING, not about rails 2/6, which have their
    # own coverage in test_guards. The allowlist rail is left binding on purpose -- one test
    # below relies on it to prove paper still enforces the offline rails.
    over.setdefault(
        "caps",
        Caps(
            max_per_order_usd=Decimal("10000000"),
            max_per_day_usd=Decimal("10000000"),
            max_exposure_usd=Decimal("10000000"),
            max_per_asset_pct=Decimal("1"),
        ),
    )
    cfg = _config(**over)
    return replace(cfg, auto_trade=replace(cfg.auto_trade, mode="paper"))


def _seed_rule(repo, monkeypatch, rule, status="paper"):
    """Paper mode loads `paper`-status rules -- the proving gate's middle stage, not `live`."""
    repo.insert_rule(rule.name, {"product_id": rule.product_id}, status=status)
    monkeypatch.setattr(agent, "_build_rule", lambda row: rule)


def test_paper_mode_records_a_fill_and_never_places_or_reads_account_state(repo, monkeypatch):
    from keel.strategy.paper import track_record

    """The end-to-end that was missing entirely: mode=paper now actually trades on paper.

    Before this, `mode: paper` silently degraded to confirm-with-no-callback -- the loop polled,
    evaluated, and recorded NOTHING, while looking like it was paper trading.
    """
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT))

    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    # Task 6: entries now size off the synthetic account equity, so the account needs a seed --
    # the real-balance read still gets attempted and swallowed exactly as before (see the
    # caught `AssertionError` in the log), this only supplies the config fallback behind it.
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("100000")))
    result = run_once(broker, repo, cfg, now_ts=90_000)

    assert result.skipped is False
    orders = repo.get_orders(mode="paper")
    assert len(orders) == 1
    assert orders[0]["side"] == Side.BUY.value
    assert track_record(repo, "fake_enter").n_trades >= 0


def test_paper_mode_full_cycle_writes_the_seeded_rules_db_id_onto_the_order(repo, monkeypatch):
    """Same fix as the live path, exercised end-to-end on the PAPER path: this test does NOT
    monkeypatch `_build_rule` (unlike `_seed_rule`'s helper above), so the real rule-id threading
    runs -- `repo.get_rules()` -> `_build_rule` -> `Rule.rule_id` -> `Signal.rule_id` ->
    `PaperTrader._enter`'s inserted order.
    """
    monkeypatch.setitem(agent.RULE_REGISTRY, "fake_enter", _AlwaysEnterRule)
    rule_id = repo.insert_rule("fake_enter", {"product_id": PRODUCT}, status="paper")

    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("100000")))

    result = run_once(broker, repo, cfg, now_ts=90_000)

    assert result.skipped is False
    orders = repo.get_orders(mode="paper")
    assert len(orders) == 1
    assert orders[0]["rule_id"] == rule_id
    # the rule_name blob in raw_response is untouched by this fix -- still present.
    import json

    payload = json.loads(orders[0]["raw_response"])
    assert payload["rule_name"] == "fake_enter"


def test_paper_mode_still_enforces_the_offline_rails(repo, monkeypatch):
    """Paper runs the rails deliberately -- the promotion gate is scored on this record."""
    # A 0.01% entry-to-stop move trips rail 7 (min-move / anti-scalping) -- an OFFLINE-computable
    # rail, so paper must still enforce it. (An out-of-allowlist product would not prove this:
    # the product loop filters on the allowlist before any rail is reached.)
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT, stop_mult="0.9999"))

    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    # Sizing needs a seeded synthetic account (Task 6) to reach the rails at all -- see the
    # matching note on `test_paper_mode_records_a_fill_and_never_places_or_reads_account_state`.
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("100000")))
    result = run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_orders(mode="paper") == []
    assert any("vetoed by rails" in (r.reason or "") for r in result.enter_results)
    vetoes = [v for r in result.enter_results for v in r.vetoed_by]
    assert any("min_move_anti_scalping" in v for v in vetoes), vetoes


def test_paper_mode_obeys_the_kill_switch(repo, monkeypatch):
    repo.set_state("kill_switch", True)
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT))

    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    result = run_once(broker, repo, _paper_config(), now_ts=90_000)

    assert result.skipped is True
    assert repo.get_orders(mode="paper") == []


def test_paper_mode_loads_PAPER_status_rules_not_live_ones(repo, monkeypatch):
    """The proving gate is candidate -> paper -> live.

    Loading `live` rules in paper mode would rehearse what is already trading and never advance
    a candidate -- the opposite of what paper trading is for.
    """
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="live")
    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _paper_config(), now_ts=90_000)

    assert repo.get_orders(mode="paper") == [], "a LIVE rule must not trade in paper mode"


# -- #639: a filled paper exit must book its outcome -----------------------------------------
#
# The pin the deployment needed two weeks ago. Six paper round trips completed in `~/keel/keel.db`
# between 08-21 and 08-28, every exit `status='filled'` with an `actual_fill`, and
# `trade_outcomes` held ZERO rows while all eight `positions` stayed `status='open'`. The
# promotion gate's pooled sample-size axis (#338) counts `trade_outcomes`, so pooled n was
# permanently 0 and no rule could ever be promoted -- silently.
#
# The cause is a whole missing edge, not a regression: `_paper_resolve_bars` -> `on_candle` ->
# `PaperTrader._close` writes the exit ORDER and forgets the position it belongs to, and
# `_handle_exits` (which is what calls `_close_tranches`) cannot cover for it because
# `_held_position` reads `mode="live"` orders ONLY -- so in a paper cycle it returns qty 0 and
# returns `[]` before ever looking at a rule. The agent OPENS the paper tranche
# (`_open_tranche`, in the shared entry loop) and, until now, nothing ever closed it.


class _EnterOnceRule(_AlwaysEnterRule):
    """Enters on the FIRST bar only, so the round-trip tests can assert `exactly one` outcome
    row without `_AlwaysEnterRule` re-entering on the very cycle the exit books."""

    def __init__(self, product_id: str, name: str = "fake_enter", stop_mult: str = "0.95",
                 target_mult: str = "1.15") -> None:
        super().__init__(product_id, name=name, stop_mult=stop_mult)
        self.target_mult = Decimal(target_mult)

    def detect(self, candles_by_tf):
        candles = next((c for c in candles_by_tf.values() if c), [])
        if len(candles) != 1:
            return None
        last = candles[-1]
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=last.close,
            stop=last.close * self.stop_mult,
            target=last.close * self.target_mult,
            context={},
            ts=last.ts,
        )


def _bar(ts: int, o: str, h: str, low: str, c: str) -> Candle:
    return Candle(
        ts=ts, open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(c),
        volume=Decimal("1"),
    )


_DAY = 86_400


def _paper_round_trip(repo, monkeypatch, rule, second_bar: Candle, *, now_ts: int = 90_000):
    """Two paper cycles: bar 1 enters, `second_bar` resolves the stop/target. Returns the
    cycle timestamp the exit booked at."""
    _seed_rule(repo, monkeypatch, rule)
    first = _bar(0, "100", "100", "100", "100")
    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [first]})
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("100000")))

    run_once(broker, repo, cfg, now_ts=now_ts)
    assert len(repo.get_open_positions(PRODUCT)) == 1, "the entry cycle must open a tranche"

    broker._series = {(PRODUCT, Granularity.ONE_DAY): [first, second_bar]}
    exit_ts = now_ts + _DAY
    run_once(broker, repo, cfg, now_ts=exit_ts)
    return exit_ts


def test_a_filled_paper_exit_books_exactly_one_outcome_and_closes_its_tranche(repo, monkeypatch):
    """#639's invariant, end to end: a filled exit carrying an `actual_fill` produces EXACTLY
    ONE `trade_outcomes` row and closes the position it belongs to.

    This is the check that would have caught the six unbooked round trips. It asserts the whole
    row, not just its existence: the pooled promotion axis counts rows, but rail 16 and the
    track record read the numbers on them, and a row written against the wrong price is a worse
    failure than no row at all.
    """
    exit_ts = _paper_round_trip(
        repo, monkeypatch, _EnterOnceRule(PRODUCT), _bar(_DAY, "101", "130", "101", "130")
    )

    orders = repo.get_orders(mode="paper")
    sells = [o for o in orders if o["side"] == Side.SELL.value]
    assert len(sells) == 1 and sells[0]["status"] == "filled"
    assert sells[0]["actual_fill"] is not None, "the premise: the exit reported a fill price"
    entry = next(o for o in orders if o["side"] == Side.BUY.value)

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1, (
        "a filled paper exit booked no outcome row -- the promotion gate's pooled sample size "
        "(#338) counts these, so it stays at zero forever and no rule can ever be promoted"
    )
    (outcome,) = outcomes
    assert outcome["product_id"] == PRODUCT
    assert outcome["rule_name"] == "fake_enter"
    assert outcome["is_dca"] is False
    assert outcome["qty"] == entry["qty"]
    assert outcome["entry_fill"] == entry["actual_fill"]
    assert outcome["exit_fill"] == sells[0]["actual_fill"]
    assert outcome["fees"] == sells[0]["fee"]
    # NET of BOTH legs' fees, like the sim -- the entry fee is carried on the tranche.
    assert outcome["pnl_net"] == (
        sells[0]["actual_fill"] * entry["qty"]
        - entry["actual_fill"] * entry["qty"]
        - sells[0]["fee"]
        - entry["fee"]
    )
    # The CYCLE's clock, not the bar's: `opened_at` is the cycle timestamp `_open_tranche`
    # recorded, and a `closed_at` taken from the candle would make a paper trade's recorded
    # holding period the difference between two different clocks.
    assert outcome["closed_at"] == exit_ts

    assert repo.get_open_positions(PRODUCT) == [], (
        "the tranche is still open after its exit filled -- exactly the state #639 found in the "
        "live paper book, where all eight positions sat open against six completed round trips"
    )
    assert repo.get_state(f"position_rule:{PRODUCT}") is None, (
        "the exit-ownership marker outlived the position it names"
    )


def test_a_losing_paper_exit_advances_the_consecutive_loss_streak(repo, monkeypatch):
    """Rail 16 is fed by the SAME producer. Booking the outcome row without wiring the streak
    would leave the breaker blind to every paper loss, which is the half of `record_closed_trade`
    a caller can most easily forget."""
    _paper_round_trip(
        repo, monkeypatch, _EnterOnceRule(PRODUCT), _bar(_DAY, "99", "99", "80", "80")
    )

    (outcome,) = repo.get_trade_outcomes()
    assert outcome["pnl_net"] < 0
    assert repo.get_state("consecutive_losses") == 1


def test_a_losing_paper_dca_exit_is_recorded_but_exempt_from_the_streak(repo, monkeypatch):
    """§12.6: DCA is exempt from the STREAK, not from the RECORD. DCA buys through drawdowns on
    a fixed budget by design, so its losses must not trip rail 16 -- but its P&L is real and the
    pooled promotion axis still counts it. `is_dca` is DERIVED from the tranche's owning rule,
    exactly as `_handle_exits` derives it from the owning rule on the live path; hardcoding
    either value breaks one of these two tests."""
    _paper_round_trip(
        repo, monkeypatch, _EnterOnceRule(PRODUCT, name="dca"), _bar(_DAY, "99", "99", "80", "80")
    )

    (outcome,) = repo.get_trade_outcomes()
    assert outcome["is_dca"] is True
    assert outcome["pnl_net"] < 0, "the premise: this round trip lost money"
    assert repo.get_state("consecutive_losses", default=0) == 0


# -- paper equity: seed, mode-flip clear, per-cycle drawdown (P4 Task 5) --------------------


class _NullBalanceBroker(FakeBroker):
    """Serves candles fine, but has no readable account at all -- `_mark_to_market_equity`
    (and therefore the paper seed's real-equity attempt) must return `None` here, forcing the
    config fallback rather than a phantom balance."""

    def get_balances(self) -> list[Balance]:
        return []


def test_paper_cycle_advances_drawdown_scalar(repo):
    """A paper run_once with an already-seeded account and a losing mark (cash below the
    existing HWM) writes a non-zero `drawdown_total_pct` -- Rail 11's scalars advancing in
    paper, which is the whole point of this task."""
    repo.set_state("equity_state_mode", "paper")
    repo.set_state("equity_high_water_mark", Decimal("10000"))
    repo.set_state("paper_cash_usdc", Decimal("7000"))
    repo.set_state("paper_ledger_start_ts", 0)
    broker = FakeBroker()

    run_once(broker, repo, _paper_config(), now_ts=90_000)

    assert repo.get_state("equity_state_mode") == "paper"
    assert repo.get_state("equity_high_water_mark") == Decimal("10000"), "HWM must not fall"
    assert repo.get_state("drawdown_total_pct") == Decimal("0.3")


def test_mode_flip_clears_hwm(repo):
    """A prior LIVE cycle's HWM/drawdown must not poison the first paper cycle after a flip --
    it is cleared and re-seeded from the paper account's own (real mark-to-market) equity."""
    repo.set_state("equity_state_mode", "live")
    repo.set_state("equity_high_water_mark", Decimal("999999"))
    repo.set_state("drawdown_total_pct", Decimal("0.9"))
    broker = FakeBroker()

    run_once(broker, repo, _paper_config(), now_ts=90_000)

    assert repo.get_state("equity_state_mode") == "paper"
    assert repo.get_state("equity_high_water_mark") != Decimal("999999")
    assert repo.get_state("drawdown_total_pct") != Decimal("0.9")


def test_loop_result_carries_paper_equity_and_drawdown(repo):
    """Task 9: a paper cycle surfaces its synthetic equity + drawdown scalars on the returned
    `LoopResult` -- the observability for a paper-forward (`_print_loop_result` + `log_event`),
    not just a side effect buried in repo state."""
    repo.set_state("equity_state_mode", "paper")
    repo.set_state("equity_high_water_mark", Decimal("10000"))
    repo.set_state("paper_cash_usdc", Decimal("7000"))
    repo.set_state("paper_ledger_start_ts", 0)
    broker = FakeBroker()

    result = run_once(broker, repo, _paper_config(), now_ts=90_000)

    assert result.paper_equity == Decimal("7000")
    assert result.drawdown_total_pct == Decimal("0.3")
    assert result.drawdown_weekly_pct is not None


def test_seed_falls_back_to_config_when_broker_read_none(repo):
    """First paper run, broker has no readable balance at all: seed from
    `config.paper.starting_equity_usd` instead of leaving the account dormant."""
    broker = _NullBalanceBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_state("paper_cash_usdc") == Decimal("10000")


def test_seed_prefers_configured_funding_over_real_equity_when_broker_IS_readable(repo):
    """A funded paper-forward: `config.paper.starting_equity_usd > 0` is a DELIBERATE funding
    override and must win even when the broker's real mark-to-market equity is readable (and
    very different) -- the whole point is to rehearse at a specific funded amount, not at
    whatever the real account happens to hold. `FakeBroker`'s real balance ($1,000,000) would
    produce a wildly different seed if the real-equity read were still used."""
    broker = FakeBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_state("paper_cash_usdc") == Decimal("10000")


def test_seed_uses_real_equity_when_starting_equity_usd_is_zero(repo, monkeypatch):
    """`starting_equity_usd == 0` (the default) keeps the existing behavior: seed from real
    broker mark-to-market equity, not the (disabled) funding override."""
    broker = FakeBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("0")))
    monkeypatch.setattr(agent, "_mark_to_market_equity", lambda *a, **k: Decimal("42000"))

    run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_state("paper_cash_usdc") == Decimal("42000")


def _reward_income_tx(coinbase_id: str, total: str) -> dict[str, Any]:
    """A reward-income row in the imported transaction ledger -- the shape `keel data import`
    writes from a Coinbase export. USDC Rewards accrue INSIDE the trading account (runbook §1:
    they cannot be switched off via the Advanced Trade API), so they reach the balance the
    paper seed reads, and this row is what makes that accrual visible to purification."""
    return {
        "coinbase_id": coinbase_id,
        "source": "coinbase",
        "type": "Reward Income",
        "asset": "USDC",
        "ts": 1_700_000_000,
        "qty": Decimal("1"),
        "price": Decimal("1"),
        "subtotal": Decimal(total),
        "total": Decimal(total),
        "fees": Decimal("0"),
    }


def test_paper_seed_excludes_accrued_but_unpurified_reward_income(repo, monkeypatch):
    """#490: the balance-derived paper seed is sizing equity, so it must subtract pending
    purification. `_mark_to_market_equity` sums broker balances at face value, and reward
    income accrues inside the account -- left in, the tainted seed becomes `paper_cash_usdc`,
    which is exactly the equity `_paper_enter` sizes from (`equity_override`), i.e. riba
    compounding into position size (discussion #472). The seed must equal
    `mark_to_market_equity - pending_purification_usd`."""
    repo.upsert_transaction(_reward_income_tx("rx1", "2000"))
    broker = FakeBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("0")))
    monkeypatch.setattr(agent, "_mark_to_market_equity", lambda *a, **k: Decimal("42000"))

    run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_state("paper_cash_usdc") == Decimal("40000")


def test_purified_paper_seed_logs_the_subtraction(repo, monkeypatch, caplog):
    """Observability for #490: the purification subtraction must not be silent. The seed event
    carries the mark-to-market read, the pending purification subtracted from it, and the final
    seed -- the before/after an operator needs to see WHY a balance-derived seed is smaller than
    the balance the broker reports."""
    repo.upsert_transaction(_reward_income_tx("rx1", "2000"))
    broker = FakeBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("0")))
    monkeypatch.setattr(agent, "_mark_to_market_equity", lambda *a, **k: Decimal("42000"))

    with caplog.at_level(logging.INFO):
        run_once(broker, repo, cfg, now_ts=90_000)

    events = [
        (r.getMessage(), getattr(r, _FIELDS_ATTR, {}))
        for r in caplog.records
        if r.getMessage() == "agent.paper_seed_purified"
    ]
    assert events == [
        (
            "agent.paper_seed_purified",
            {"mark_to_market": "42000", "pending_purification": "2000", "seed": "40000"},
        )
    ]


def test_a_seed_fully_consumed_by_pending_purification_warns(repo, monkeypatch, caplog):
    """The clamp is the loudest case: pending purification at or above the mark-to-market read
    floors `sizing_equity` at zero, and a `paper_cash_usdc` of 0 must arrive with a WARNING
    that explains it, not read as a broken account."""
    repo.upsert_transaction(_reward_income_tx("rx1", "2000"))
    broker = FakeBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("0")))
    monkeypatch.setattr(agent, "_mark_to_market_equity", lambda *a, **k: Decimal("1500"))

    with caplog.at_level(logging.WARNING):
        run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_state("paper_cash_usdc") == Decimal("0")
    clamps = [
        (r.getMessage(), getattr(r, _FIELDS_ATTR, {}))
        for r in caplog.records
        if r.getMessage() == "agent.paper_seed_clamped_to_zero"
    ]
    assert clamps == [
        (
            "agent.paper_seed_clamped_to_zero",
            {"mark_to_market": "1500", "pending_purification": "2000"},
        )
    ]


def test_funded_paper_seed_is_the_configured_amount_not_purified(repo):
    """The other seed branch is a DELIBERATE operator-chosen funding amount, not a balance
    read -- it contains no reward accruals by construction, so purification does not apply to
    it. Pinning the boundary so the #490 exclusion stays scoped to balance-derived equity
    (the same reason the live path's config-constant stand-in is immune)."""
    repo.upsert_transaction(_reward_income_tx("rx1", "2000"))
    broker = FakeBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_state("paper_cash_usdc") == Decimal("10000")


# -- paper fills sized off paper equity (P4 Task 6) -----------------------------


def _paper_enter_signal(
    product_id: str = PRODUCT,
    entry: Decimal = Decimal("100"),
    stop: Decimal = Decimal("90"),
    target: Decimal = Decimal("130"),
    ts: int = 1_000,
) -> Signal:
    return Signal(
        rule_name="fake_enter",
        product_id=product_id,
        action=Action.ENTER,
        side=Side.BUY,
        setup=Setup(
            product_id=product_id,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context={},
            ts=ts,
        ),
        cts_score=7,
        entry_technique="signal_candle",
        ts=ts,
    )


def test_paper_enter_sizes_off_paper_equity(repo):
    """`_paper_enter` must size the fill off the SYNTHETIC ACCOUNT EQUITY it is handed, not the
    `$5k max_exposure` proxy `_build_intent` falls back to and not the old fixed 1-unit fill."""
    from keel.execution import sizing
    from keel.strategy.paper import PaperTrader

    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("30000"), now_ts=1_000)
    repo.set_state("last_feed_ts", 90_000)
    config = _paper_config()
    sig = _paper_enter_signal(entry=Decimal("100"), stop=Decimal("90"), target=Decimal("130"))

    result = agent._paper_enter(
        trader, sig, repo, config, now_ts=90_000, paper_equity=Decimal("30000")
    )

    assert result.placed
    orders = repo.get_orders(mode="paper")
    assert len(orders) == 1
    expected_qty = sizing.size(Decimal("30000"), config.risk_pct, Decimal("100"), Decimal("90"))
    assert expected_qty == Decimal("30")
    assert orders[0]["qty"] == expected_qty
    assert orders[0]["qty"] != Decimal("1"), "must not fill the old fixed 1-unit qty"


def test_paper_mode_never_runs_the_entry_spread_gate(repo):
    """#350's max-spread gate is live-path ONLY: paper fills are synthetic and see no book, so
    `_paper_enter` never previews an order and the gate (fail-closed on an unreadable book for
    a live BUY) must not refuse a paper entry. The same "no readable book" condition that
    REFUSES a live BUY therefore fills here -- which is exactly why the paper-hourly profile
    accrues NO evidence about the gate, and why the gate ships before any live resumption
    rather than being validated on paper first.
    """
    from keel.strategy.paper import PaperTrader

    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("30000"), now_ts=1_000)
    repo.set_state("last_feed_ts", 90_000)
    config = _paper_config()  # the gate is armed at its default 50bp threshold
    sig = _paper_enter_signal(entry=Decimal("100"), stop=Decimal("90"), target=Decimal("130"))

    result = agent._paper_enter(
        trader, sig, repo, config, now_ts=90_000, paper_equity=Decimal("30000")
    )

    assert result.placed
    assert result.vetoed_by == []
    assert repo.get_orders(mode="paper") and repo.get_orders(mode="live") == []


def test_run_once_sizes_a_paper_entry_off_the_seeded_synthetic_equity(repo, monkeypatch):
    """Loop-level: the `equity_now` Task 5 computes for the paper branch is what sizes the fill,
    not a re-derived value and not the fixed 1-unit qty `_AlwaysEnterRule` used to produce."""
    from keel.execution import sizing

    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT))
    repo.set_state("paper_cash_usdc", Decimal("30000"))
    repo.set_state("paper_ledger_start_ts", 0)
    repo.set_state("equity_state_mode", "paper")
    # `_AlwaysEnterRule` sets entry = candle close, stop = 0.95 * close -> a 5% stop distance.
    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _paper_config(), now_ts=90_000)

    orders = repo.get_orders(mode="paper")
    assert len(orders) == 1
    expected_qty = sizing.size(
        Decimal("30000"), _paper_config().risk_pct, Decimal("100"), Decimal("95")
    )
    assert orders[0]["qty"] == expected_qty
    assert orders[0]["qty"] != Decimal("1")
    assert result.enter_results[0].placed


def test_run_once_skips_paper_entries_when_the_synthetic_account_is_unseeded(repo, monkeypatch):
    """Sizing off an UNKNOWN equity is worse than not trading: an unseeded/unreadable paper
    account must skip entries this cycle rather than fall back to a garbage size."""
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT))
    # No cash seeded, and the fallback is disabled (0) -- `equity_now` stays `None` all cycle.
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("0")))
    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, cfg, now_ts=90_000)

    assert repo.get_orders(mode="paper") == []
    assert result.enter_results, "the signal still fires; it must just not be filled"
    assert not any(r.placed for r in result.enter_results)


# -- monthly contribution: calendar-month rollover (P4 Task 7) ------------------


def test_paper_monthly_contribution_applied_once_per_month(repo):
    """A configured `monthly_contribution_usd` deposits once per UTC calendar month -- applied
    the cycle the month is first seen, not re-applied on a later cycle in the SAME month, and
    applied again once the calendar rolls into the next month."""
    # JAN15/JAN20 share a UTC month_start; FEB03 is the next calendar month (see
    # `guards._utc_month_bounds`).
    JAN15 = 1_705_320_000
    JAN20 = 1_705_752_000
    FEB03 = 1_706_961_600

    # `_NullBalanceBroker` (no readable account) forces the paper seed onto the config
    # fallback, so `paper_cash_usdc` starts deterministic and non-`None`.
    broker = _NullBalanceBroker()
    cfg = _paper_config(
        paper=PaperConfig(
            starting_equity_usd=Decimal("10000"),
            monthly_contribution_usd=Decimal("500"),
        )
    )

    # Cycle 1 (JAN15): first-ever cycle seeds the account AND applies month 1's contribution.
    run_once(broker, repo, cfg, now_ts=JAN15)
    cash_after_first = repo.get_state("paper_cash_usdc")
    assert cash_after_first == Decimal("10000") + Decimal("500")
    assert repo.get_state("paper_last_contribution_month") == 1_704_067_200

    # Cycle 2 (JAN20): same calendar month -- no second contribution.
    run_once(broker, repo, cfg, now_ts=JAN20)
    assert repo.get_state("paper_cash_usdc") == cash_after_first

    # Cycle 3 (FEB03): calendar rolled over -- contribution applies again.
    before = repo.get_state("paper_cash_usdc")
    run_once(broker, repo, cfg, now_ts=FEB03)
    assert repo.get_state("paper_cash_usdc") >= before + Decimal("500") - Decimal("1")
    assert repo.get_state("paper_last_contribution_month") == 1_706_745_600


def test_paper_monthly_contribution_disabled_by_default(repo):
    """`monthly_contribution_usd` defaults to 0 -- no deposit, no state key written."""
    broker = _NullBalanceBroker()
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    run_once(broker, repo, cfg, now_ts=1_705_320_000)

    assert repo.get_state("paper_cash_usdc") == Decimal("10000")
    assert repo.get_state("paper_last_contribution_month") is None


# -- Rail 11 end-to-end in paper: a REAL drawdown driven through run_once (P4 Task 8) -----------


def test_paper_full_loop_drawdown_halt_vetoes_subsequent_buys(repo, monkeypatch):
    """The headline acceptance test: drive a genuine drawdown through `run_once` -- not inject
    the scalar directly -- and prove Rail 11 vetoes the very next paper entry attempt.

    Cycle 1 opens a paper position and marks it at its entry price, seeding the high-water mark.
    Cycle 2 feeds a catastrophic mark-down for the SAME product; `paper_trader.equity(...)`
    craters and `update_drawdown` writes `drawdown_total_pct` far past the 20% ceiling -- both
    via the REAL `run_once` loop, not injected. A fresh paper entry attempt against that
    loop-produced state is then run through `agent._paper_enter` -- the exact function `run_once`
    itself calls for every paper ENTER signal -- and must come back vetoed by
    `account_dd_breaker_total`, proving the scalar Tasks 5/6 wired up is the one Rail 11 actually
    reads, end-to-end.

    (Deliberately does NOT rely on `engine.evaluate` re-firing `_AlwaysEnterRule` in cycle 2 to
    produce that attempt: with only two candles on record, `engine`'s own, unrelated
    choppy-regime gate has too few swing pivots to ever call the window tradeable, and rejecting
    on THAT gate would prove nothing about rail 11. `_paper_enter` is the real production
    function `run_once` calls once a signal clears the engine, so driving it directly here still
    exercises the genuine wiring under test.)
    """
    from keel.strategy.paper import PaperTrader

    # A rule registered for PRODUCT is still needed so `run_once` includes it in `products` and
    # therefore in `latest_price_by_product` -- without that, the loop would never learn day 1's
    # crashed price and would mark the position at cost basis instead.
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="paper")
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    # Seed the synthetic account and open a large paper position directly, BEFORE any cycle runs
    # -- the brief's licensed shortcut: prove the SCALAR advances and Rail 11 acts on it through
    # the real loop, without needing realistic sizing to get a position open in the first place.
    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("10000"), now_ts=0)
    repo.set_state("equity_state_mode", "paper")
    entry_signal = _paper_enter_signal(
        product_id=PRODUCT, entry=Decimal("100"), stop=Decimal("50"), target=Decimal("200"), ts=0
    )
    trader.on_signal(entry_signal, qty=Decimal("90"))  # ~90% of the seeded $10k cash

    broker = _MarketDataOnlyBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): [
                _candle(0, "100"),  # day 0: the price the position was opened at
                # day 1: a catastrophic mark-down. Realistic sizing/market moves would need a
                # much bigger position to cross 20%; a large adverse move is the brief's other
                # licensed shortcut for proving the scalar without modeling a realistic market.
                _candle(86_400, "1"),
            ]
        }
    )

    # Cycle 1 (now_ts inside day 1 -> day 0's candle is the latest CLOSED): equity marks the
    # position at its entry price, seeding the high-water mark at a real, non-zero equity.
    run_once(broker, repo, cfg, now_ts=90_000)
    hwm_before = repo.get_state("equity_high_water_mark")
    assert hwm_before is not None and hwm_before > Decimal("9000")
    assert repo.get_state("drawdown_total_pct") == Decimal("0")

    # Cycle 2 (now_ts inside day 2 -> day 1's candle is now the latest closed): the
    # catastrophic mark-down. Since the #442 gap fix, that candle -- entirely below the
    # position's stop (50) -- CLOSES the position at its open ($1/unit), so equity crashes
    # through the realized loss rather than an open-position mark; either way the scalar
    # the loop reads is the same one rail 11 acts on.
    now_ts_2 = 86_400 + 90_000
    run_once(broker, repo, cfg, now_ts=now_ts_2)

    dd_total = repo.get_state("drawdown_total_pct")
    assert dd_total is not None and dd_total > Decimal("0.20"), (
        f"expected the REAL loop to drive drawdown_total_pct past the 20% ceiling, got "
        f"{dd_total} (hwm was {hwm_before})"
    )

    # A subsequent paper ENTER attempt, run through the real `_paper_enter` against the state the
    # loop just produced, must come back vetoed by rail 11 -- not filled.
    post_crash_trader = PaperTrader(repo)
    post_crash_equity = post_crash_trader.equity({PRODUCT: Decimal("1")})
    next_signal = _paper_enter_signal(
        product_id=PRODUCT,
        entry=Decimal("1"),
        stop=Decimal("0.5"),
        target=Decimal("2"),
        ts=now_ts_2 + 1,
    )

    # The entry plus the gap-through exit are the only paper orders on record so far.
    orders_before_attempt = len(repo.get_orders(mode="paper", product_id=PRODUCT))
    assert orders_before_attempt == 2  # the entry, and the exit the gap through the stop wrote

    entry_result = agent._paper_enter(
        post_crash_trader, next_signal, repo, cfg, now_ts=now_ts_2, paper_equity=post_crash_equity
    )

    assert entry_result.placed is False
    assert any("account_dd_breaker_total" in v for v in entry_result.vetoed_by), (
        entry_result.vetoed_by
    )
    assert len(repo.get_orders(mode="paper", product_id=PRODUCT)) == orders_before_attempt, (
        "no new paper order should have been filled once the breaker tripped"
    )


def test_run_once_vetoes_a_paper_entry_through_the_real_loop_when_drawdown_breaker_is_tripped(
    repo, monkeypatch
):
    """Loop-level companion to the acceptance test above: that test drives the drawdown scalar
    through a real `run_once` cycle, but asserts the veto via a DIRECT `agent._paper_enter(...)`
    call -- so the within-cycle ordering (drawdown refreshed BEFORE the entry loop, by the SAME
    `run_once` invocation that then evaluates the entry) is never executed end-to-end.

    This drives a genuine ENTER signal (`_AlwaysEnterRule`, same as the other loop-level paper
    tests) through `run_once` itself, against a cycle where rail 11's scalar is already tripped,
    and asserts the resulting `LoopResult.enter_results` shows the veto -- proving the breaker
    fires through the REAL loop entry path, not just a direct `_paper_enter` call.
    """
    from keel.strategy.paper import PaperTrader

    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="paper")
    cfg = _paper_config(paper=PaperConfig(starting_equity_usd=Decimal("10000")))

    # Already-seeded paper account (no open position), in "paper" equity-state mode.
    trader = PaperTrader(repo)
    trader.seed_cash(Decimal("10000"), now_ts=0)
    repo.set_state("equity_state_mode", "paper")

    NOW = 90_000
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", NOW)
    # Pre-set a high-water mark far above the seeded cash: with no open position, this
    # cycle's equity is just the $10k cash, so `update_drawdown` (called by `run_once`
    # itself, before the entry loop) recomputes `drawdown_total_pct` to 0.5 -- well past
    # the 0.20 ceiling -- from THIS state, through the real loop, not injected directly.
    repo.set_state("equity_high_water_mark", Decimal("20000"))

    broker = _MarketDataOnlyBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, cfg, now_ts=NOW)

    dd_total = repo.get_state("drawdown_total_pct")
    assert dd_total is not None and dd_total > Decimal("0.20"), (
        f"expected the real loop to compute drawdown past the ceiling, got {dd_total}"
    )
    assert result.enter_signals, "the rule still fires a signal; it must just be vetoed"
    assert any(not r.placed for r in result.enter_results)
    assert any(
        "account_dd_breaker_total" in v for r in result.enter_results for v in r.vetoed_by
    ), [r.vetoed_by for r in result.enter_results]
    assert repo.get_orders(mode="paper") == [], (
        "no paper BUY order should be written once the breaker tripped, through the real loop"
    )


# -- interactive confirm: run_once threads confirm_fn to placement --------------


def _live_config(**over):
    """Confirm mode, roomy caps, all live-BUY rails satisfied so an approved order places."""
    over.setdefault(
        "caps",
        Caps(
            max_per_order_usd=Decimal("1000000"),
            max_per_day_usd=Decimal("1000000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
    )
    cfg = _config(**over)
    return replace(cfg, auto_trade=replace(cfg.auto_trade, mode="confirm"))


def _live_ready_repo(repo):
    """Clear every live-BUY rail that isn't the confirmation itself."""
    repo.set_state("kill_switch", False)
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", 10**12)
    attest_subscription(repo, now_ts=0, free_volume_usd=Decimal("10000000"))
    return repo


def test_confirm_APPROVED_places_the_order(repo, monkeypatch):
    """The change: an approved confirm-mode order is actually placed (was: never)."""
    repo.set_autonomous(False, now_ts=0)  # these tests are ABOUT the confirm gate
    _live_ready_repo(repo)
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _live_config(), now_ts=90_000, confirm_fn=lambda preview: True)

    # An approved entry places the BUY (and its protective OCO bracket) -- was: nothing at all.
    buys = [c for c in broker.place_calls if c["product_id"] == PRODUCT and c["side"] == Side.BUY]
    assert len(buys) == 1


def test_confirm_DECLINED_places_nothing(repo, monkeypatch):
    repo.set_autonomous(False, now_ts=0)  # these tests are ABOUT the confirm gate
    _live_ready_repo(repo)
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _live_config(), now_ts=90_000, confirm_fn=lambda preview: False)

    assert broker.place_calls == []


def test_confirm_fn_defaulting_to_None_still_fails_closed(repo, monkeypatch):
    """No confirm_fn (the old default) must still place nothing -- backward compatible."""
    repo.set_autonomous(False, now_ts=0)  # these tests are ABOUT the confirm gate
    _live_ready_repo(repo)
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _live_config(), now_ts=90_000)  # no confirm_fn

    assert broker.place_calls == []


def test_confirm_fn_sees_the_broker_preview(repo, monkeypatch):
    repo.set_autonomous(False, now_ts=0)  # these tests are ABOUT the confirm gate
    _live_ready_repo(repo)
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule(PRODUCT), status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    seen: list[Preview] = []

    def _capture(preview):  # noqa: ANN001, ANN202
        seen.append(preview)
        return True

    run_once(broker, repo, _live_config(), now_ts=90_000, confirm_fn=_capture)
    # The operator is shown the port's `Preview` since #524, not a venue dict. `order_total` is
    # still there -- it moved into `detail`, which is where the port carries the venue's own
    # figures as strings.
    assert len(seen) == 1
    assert isinstance(seen[0], Preview)
    assert "order_total" in seen[0].detail


def test_a_rail_veto_means_the_confirm_prompt_is_never_reached(repo, monkeypatch):
    """The rails run FIRST. A vetoed order never asks the human -- confirmation is not a
    substitute for the hard limits."""
    repo.set_autonomous(False, now_ts=0)  # these tests are ABOUT the confirm gate
    _live_ready_repo(repo)
    _seed_rule(repo, monkeypatch, _AlwaysEnterRule("DOGE-USD"), status="live")  # off allowlist
    broker = FakeBroker(series={("DOGE-USD", Granularity.ONE_DAY): [_candle(0, "100")]})
    asked = {"n": 0}

    def _count(preview):
        asked["n"] += 1
        return True

    run_once(broker, repo, _live_config(), now_ts=90_000, confirm_fn=_count)
    assert asked["n"] == 0
    assert broker.place_calls == []


# -- the CLI wires the interactive prompt --------------------------------------


def _gate_preview(**overrides):
    """The port's `Preview` -- the one shape the confirm gate reads since #524."""
    from decimal import Decimal

    from keel_broker_api.results import Preview
    from keel_core.types import Side

    fields: dict = {
        "product_id": "BTC-USD",
        "side": Side.BUY,
        "est_base_size": Decimal("0.0001"),
        "est_quote_size": Decimal("5.00"),
        "est_fee": Decimal("0.03"),
        "synthetic": False,
    }
    fields.update(overrides)
    return Preview(**fields)


def test_interactive_confirm_places_on_yes_declines_on_no(monkeypatch, capsys):
    """`_interactive_confirm` renders the preview and returns the human's yes/no."""
    import keel.cli as cli_module

    # The TTY predicate lives in keel.commands._common; _interactive_confirm calls it there.
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)

    monkeypatch.setattr(cli_module.click, "confirm", lambda *a, **k: True)
    assert cli_module._interactive_confirm(_gate_preview())
    out = capsys.readouterr().out
    assert "Rails PASSED. Order preview:" in out
    assert "est_quote_size: 5.00" in out

    monkeypatch.setattr(cli_module.click, "confirm", lambda *a, **k: False)
    assert cli_module._interactive_confirm(_gate_preview()) is False


def test_interactive_confirm_fails_closed_without_a_tty(monkeypatch):
    import keel.cli as cli_module

    # The TTY predicate lives in keel.commands._common; _interactive_confirm calls it there.
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: False)
    assert cli_module._interactive_confirm(_gate_preview()) is False


def test_agent_command_passes_interactive_confirm_in_CONFIRM_mode(repo, monkeypatch):
    """The wiring: `keel agent` (confirm) hands run_once the interactive confirm_fn; bypass
    hands it None."""
    repo.set_autonomous(False, now_ts=0)  # these tests are ABOUT the confirm gate
    from click.testing import CliRunner

    import keel.cli as cli_module
    from keel.cli import cli

    captured = {}

    def _fake_run_once(broker, repo_arg, config, now_ts, confirm_fn=None):
        captured["confirm_fn"] = confirm_fn
        captured["mode"] = config.auto_trade.mode
        return agent.LoopResult(
            ts=now_ts, skipped=False, skip_reason=None, mode=config.auto_trade.mode, polled=0
        )

    monkeypatch.setattr(cli_module, "_build_broker", lambda config, **_kw: object())
    monkeypatch.setattr(cli_module, "_open_repo", lambda ctx: repo)
    monkeypatch.setattr(cli_module, "_load_cfg", lambda ctx: _live_config())
    monkeypatch.setattr(cli_module.agent, "run_once", _fake_run_once)

    result = CliRunner().invoke(cli, ["agent"])
    assert result.exit_code == 0, result.output
    assert captured["mode"] == "confirm"
    assert captured["confirm_fn"] is cli_module._interactive_confirm


def test_an_EXPIRED_autonomy_falls_back_to_confirm_and_places_nothing(repo):
    """End-to-end guard on the enforcement chain run_once -> _effective_mode -> is_autonomous.
    Without this, dropping the now_ts argument would silently un-bound autonomy again."""
    repo.set_autonomous(True, now_ts=1_000, expires_ts=50_000)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)  # well past the expiry

    assert result.mode == "confirm", "expired autonomy must fall back to asking a human"
    assert broker.place_calls == []


def test_autonomy_still_applies_strictly_before_its_expiry(repo):
    repo.set_autonomous(True, now_ts=1_000, expires_ts=90_001)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.mode == "autonomous"
    assert len(broker.place_calls) == 1


def test_equity_counts_settled_cash_in_EVERY_quote_leg_being_traded(repo):
    """Rail 11's HWM is monotonic, so an under-read arms a phantom drawdown PERMANENTLY.

    Counting only `config.quote_currency` under-reads an account whose settled cash sits in the
    currency its products actually settle in -- and this is reachable now that rail 13 permits
    such an order. Equity must see the cash that funds the trading.
    """

    class TwoCurrencyBroker:
        def get_balances(self):
            return [
                Balance(currency="USD", available=Decimal("1000"), total=Decimal("1000")),
                Balance(currency="USDC", available=Decimal("7"), total=Decimal("7")),
            ]

    equity = agent._mark_to_market_equity(repo, TwoCurrencyBroker(), ["BTC-USD"], {}, "USDC")
    assert equity is not None
    assert equity >= Decimal("1000"), (
        f"equity {equity} ignored the USD cash that funds BTC-USD orders"
    )


def test_equity_is_a_total_when_only_SOME_currencies_are_readable(repo):
    """`None` means 'equity is unknowable', reserved for when NOTHING is readable. A currency the
    account simply has no wallet for must contribute nothing, not void the whole reading -- that
    would return None on a perfectly ordinary account and stall rail 11's equity tracking."""

    class OnlyUsd:
        def get_balances(self):
            return [Balance(currency="USD", available=Decimal("1000"), total=Decimal("1000"))]

    equity = agent._mark_to_market_equity(repo, OnlyUsd(), ["BTC-USD"], {}, "USDC")
    assert equity == Decimal("1000"), f"expected a total, got {equity!r}"


def test_equity_is_None_only_when_NOTHING_is_readable(repo):
    class NoAccounts:
        def get_balances(self):
            return []

    assert agent._mark_to_market_equity(repo, NoAccounts(), ["BTC-USD"], {}, "USDC") is None


def test_equity_finds_cash_for_a_HELD_product_whose_rule_was_retired(repo, monkeypatch):
    """The valuation loop already covers `held_products()`; the currency scan must too. A
    retired rule leaves its position marked to market while the cash funding it goes unseen --
    an under-read, and the HWM never falls."""

    class EurOnly:
        def get_balances(self):
            return [Balance(currency="EUR", available=Decimal("500"), total=Decimal("500"))]

    monkeypatch.setattr(repo, "held_products", lambda: ["BTC-EUR"])
    equity = agent._mark_to_market_equity(repo, EurOnly(), [], {}, "USD")
    assert equity == Decimal("500"), f"cash for a held product's quote leg was missed: {equity!r}"


# -- CHARACTERIZATION: same-UTC-day re-entry is NOT deduped on the live path -------------------
#
# These two tests are deliberately NOT regression tests for a bug fix -- they PIN a hazard that
# exists today in the live path, on purpose, so it cannot regress into "nobody noticed it was
# there" without a test going red.
#
# The only thing that stops `keel-live-run.sh` from driving `agent.run_once` twice for the same
# signal on the same day is the script's own day-stamp file (`$DIR/logs/.keel-live-last-run`,
# written only after a clean exit -- see the file's own header, "EXACTLY ONCE PER CALENDAR DAY").
# That stamp lives entirely OUTSIDE `run_once`/`executor.execute`/`guards.check`: nothing in this
# module, or in anything it calls, remembers "we already entered this product today" and refuses
# a second attempt. Concretely (verified by reading, not assumed):
#   * `Repository.get_open_positions` is read by the EXIT/reconcile/status paths, never by the
#     ENTER path -- an ENTER never asks "is one already open?".
#   * `strategy.engine.evaluate` writes to the `signals` table (`_persist_signal`) but nothing
#     ever reads it back to suppress a repeat; it is an audit log, not a dedupe key.
#   * `executor._build_intent` mints a fresh `uuid4()` `client_order_id` every call, so Coinbase's
#     own idempotency-key protection (which would catch an exact retry) never engages either --
#     each cycle looks like a brand-new, unrelated order to the exchange.
#   * `execution/guards.py`'s eighteen-and-counting rails are all DOLLAR caps
#     (`max_per_order_usd`/`max_per_day_usd`/`max_exposure_usd`/`max_per_asset_pct`) or account
#     state (drawdown, streak, balance) -- there is no "N entries per product per day" rail.
#
# So: delete or weaken the runner's day-stamp (or run two DIFFERENT trigger paths against the
# same DB the same day -- e.g. a wake-from-sleep replay racing a manual `keel agent` invocation),
# and the live path happily takes the same signal twice. These two tests drive `agent.run_once`
# directly, twice, with two `now_ts` values inside one UTC calendar day -- exactly what the
# runner's stamp exists to collapse into one -- and assert the DUPLICATE actually happens.
#
# If someone later adds a real live-side entry dedupe (an actual per-day-per-product guard,
# not just the shell script's stamp), these two tests SHOULD start failing. That failure is the
# signal to REWRITE them to assert a single entry, not to delete them: until that rail exists,
# this is the only thing in the test suite that would notice its absence.


def test_two_cycles_in_one_utc_day_place_two_dca_orders(repo):
    """CHARACTERIZATION, not a spec: pins the DCA half of the live day-stamp hazard described
    in the section header above. See that comment for the full mechanism.

    Template: `test_run_once_polls_evaluates_and_executes_a_real_dca_rule` (single day-0 daily
    candle, `Dca`'s cadence-boundary fixture -- `latest.ts // 86_400 % cadence_days == 0` with
    `cadence_days=7`, and `0 % 7 == 0`). `market_feed._latest_closed_ts(now_ts, 86_400)` resolves
    to that same day-0 candle for ANY `now_ts` inside calendar day 1 (`[86_400, 172_800)`), so
    both cycles below evaluate the identical `Setup` off the identical candle -- the rule does
    not "know" it already fired.

    `EARLY`/`LATE` model the runner's own two-trigger-in-one-day shape (an hourly-launchd
    detector re-armed after a missed day-stamp -- see `keel-live-run.sh`'s header): `EARLY` is
    the first cycle of day 1, `LATE` is a much later cycle the SAME day. `LATE` is kept inside
    rail 12's staleness window (`config.auto_trade.interval_sec(50_000) * FEED_STALENESS_CYCLES
    (3) = 150_000` seconds past the day-0 candle) so the second cycle still finds the feed fresh
    and reaches the rule at all -- a stale-feed skip would prove nothing about this hazard.
    """
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    config = _config()

    EARLY = 90_000  # 1h into day 1 -- the day's first cycle.
    LATE = 140_000  # ~14h40m later, same UTC day (still < the 150_000s staleness ceiling).

    first = run_once(broker, repo, config, now_ts=EARLY)
    second = run_once(broker, repo, config, now_ts=LATE)

    # The hazard, stated as an assertion: BOTH cycles place. A real dedupe would make the second
    # `placed is False` (or the signal never fire at all); today it does not.
    assert first.enter_results[0].placed is True
    assert second.enter_results[0].placed is True

    orders = repo.get_orders(mode="live", product_id=PRODUCT)
    assert len(orders) == 2, (
        "expected the SAME dca signal to place twice in one UTC day -- if this is 1, a dedupe "
        "now exists and this test must be REWRITTEN (not deleted) to assert that instead"
    )
    assert all(o["side"] == "BUY" for o in orders)


def test_two_cycles_in_one_utc_day_reenter_the_same_turtle_breakout(repo, monkeypatch):
    """CHARACTERIZATION, not a spec: pins the risk-defined-rule half of the same hazard (see the
    section header above for the full mechanism) -- this time with a rule that opens a bracketed
    position, not a no-stop DCA buy, so it also exercises `executor.place_bracket` twice.

    Candle series: `tests/strategy/test_turtle_breakout.py`'s own `_trending_base` (a smooth,
    strictly monotonic rise) is enough to fire `TurtleBreakout.detect()` directly, as that
    module's tests do -- but `strategy.engine.evaluate` ALSO gates on
    `analysis.regime.detect_condition`, which classifies structure from swing PIVOTS (local
    highs/lows with a neighbor on each side). A strictly monotonic series has no interior pivot
    at all, so `detect_condition` reads it as CHOPPY and `engine.evaluate` discards the signal
    before `executor.execute` is ever reached -- verified directly against this module's own
    `_trending_base(20)` + breakout bar. So this reuses the OTHER verified-BULLISH fixture next
    door, `tests/strategy/test_engine.py::_uptrend_candles` (a real up-down-up zigzag with
    higher highs and higher lows), plus one final bar that closes above the prior 5-day Donchian
    high with a strongly trending ADX -- confirmed by direct script run to clear every gate:
    `TurtleBreakout.detect()`'s own donchian/ADX gates, `engine.evaluate`'s choppy-regime gate,
    and the kill-zone `rr>=1` floor (`rr` comes out at the rule's default `target_rr=6`).
    `entry_lookback=5, exit_lookback=3, adx_period=5, atr_period=5` is `test_turtle_breakout.py`'s
    own `_SMALL_PARAMS`, reused so `min_needed` (`max(lookbacks) + 2 == 7`) stays well under this
    11-candle series.

    Per the notes this test was written against: rail 8 (`no_averaging_into_losers`) does not
    veto the second entry because `execute()` records `actual_fill = intent.entry`
    (`executor.py`), so the average cost basis equals each entry's own setup price -- an
    identical setup on the second cycle is not "averaging into a loser". Rail 9
    (`no_stop_widening`) does not veto the second bracket because the setup (and therefore the
    stop `place_bracket` writes to `open_stop:<product>`) is byte-for-byte identical between the
    two cycles -- there is nothing to widen against. Both were verified by running this exact
    scenario, not assumed.

    `place_bracket` is a module-level function in `keel.execution.executor`, called by name from
    `execute()` -- wrapping it (rather than replacing it) here counts calls while still letting
    the real bracket placement run, so the SELL leg this asserts on on the repo side is genuine,
    not simulated.
    """
    _DAY = 86_400
    # `test_engine.py::_uptrend_candles`'s base prices -- verified `regime.detect_condition ==
    # BULLISH` there. This module's own `_candle` helper builds a FLAT bar
    # (open=high=low=close), which would erase the very swing structure `regime` needs, so the
    # 0.5 high/low spread is rebuilt here instead of reusing `_candle`.
    base_prices = [100, 105, 102, 108, 104, 112, 109, 118, 114, 124]
    candles = [
        Candle(
            ts=i * _DAY,
            open=Decimal(str(v)),
            high=Decimal(str(v)) + Decimal("0.5"),
            low=Decimal(str(v)) - Decimal("0.5"),
            close=Decimal(str(v)),
            volume=Decimal("1"),
        )
        for i, v in enumerate(base_prices)
    ]
    breakout_price = Decimal("140")  # clears the prior 5-day Donchian high with room to spare.
    breakout_ts = len(base_prices) * _DAY
    candles.append(
        Candle(
            ts=breakout_ts,
            open=breakout_price - Decimal("0.5"),
            high=breakout_price + Decimal("0.5"),
            low=breakout_price - Decimal("0.5"),
            close=breakout_price,
            volume=Decimal("1"),
        )
    )
    # `market_feed.poll_once` only ever fetches from `latest_closed` forward on a bare DB (its
    # first-ever poll fetches a single day, not the lookback window `backfill` would have primed
    # in production) -- seeding the repo directly is the honest way to give the rule its full
    # history without also testing `poll_once`'s own resumability, which is `test_market_feed.py`'s
    # job, not this test's.
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, candles)
    repo.insert_rule(
        "turtle_breakout",
        {
            "product_id": PRODUCT,
            "entry_lookback": 5,
            "exit_lookback": 3,
            "adx_period": 5,
            "atr_period": 5,
        },
        status="live",
    )
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): candles})
    config = _config()

    # `market_feed._latest_closed_ts(now_ts, 86_400)` resolves to `breakout_ts` for any `now_ts`
    # in calendar day 11 (`[11*86_400, 12*86_400)`) -- both cycles below land in that one day, so
    # both evaluate the SAME breakout bar. `LATE` is kept inside rail 12's staleness window
    # (`interval_sec(50_000) * FEED_STALENESS_CYCLES(3) = 150_000`s past `breakout_ts`) for the
    # same reason as the DCA test above.
    EARLY = breakout_ts + 90_000
    LATE = breakout_ts + 136_000

    real_place_bracket = executor.place_bracket
    bracket_calls = {"n": 0}

    def _spy_place_bracket(*args, **kwargs):
        bracket_calls["n"] += 1
        return real_place_bracket(*args, **kwargs)

    monkeypatch.setattr(executor, "place_bracket", _spy_place_bracket)

    first = run_once(broker, repo, config, now_ts=EARLY)
    second = run_once(broker, repo, config, now_ts=LATE)

    # The hazard, stated as an assertion: the SAME breakout is entered twice, same day.
    assert len(first.enter_results) == 1
    assert len(second.enter_results) == 1
    assert first.enter_results[0].placed is True
    assert second.enter_results[0].placed is True

    entry_orders = [
        o for o in repo.get_orders(mode="live", product_id=PRODUCT) if o["side"] == "BUY"
    ]
    assert len(entry_orders) == 2, (
        "expected the SAME turtle_breakout signal to enter twice in one UTC day -- if this is "
        "1, a live-side entry dedupe now exists and this test must be REWRITTEN (not deleted) "
        "to assert that instead"
    )

    assert bracket_calls["n"] == 2, (
        "each of the two live entries should have opened its own exchange-side exit bracket "
        "(rail 8/9 notes above explain why neither is vetoed) -- if this is 1, something started "
        "deduping the bracket even though the entry itself still duplicated, which is a new and "
        "different hazard worth its own test, not a fix for this one"
    )
    # Counting the CALLS is not enough on its own: `place_bracket` returns `None` when a rail
    # vetoes, and a vetoed second bracket would leave a real position riding with no exchange-side
    # stop while this test still went green. Asserting the returned ids proves the docstring's
    # claim about rail 9 -- that an identical stop is not "widening" -- rather than assuming it.
    assert first.enter_results[0].bracket_order_id is not None
    assert second.enter_results[0].bracket_order_id is not None, (
        "the second entry's bracket was vetoed -- the duplicate position is riding without an "
        "exchange-side stop, which is strictly worse than the duplicate entry this test pins"
    )


# -- entry bar readiness gate (Finding 1, HIGH: duplicate real-money orders) -------------------
#
# The live LaunchAgent fires at :20 past every UTC hour; the runner gates on UTC hour >= 1, so
# the first eligible trigger of the day is 01:20 UTC. `turtle_breakout.py::_completed_days`
# withholds the just-closed ONE_DAY bar until the 00:00-01:00 UTC ONE_HOUR bar has closed at
# 01:00 UTC -- normally a 20-minute margin. If the ONE_HOUR series is even one bar late (a
# routine publication lag), or the ONE_DAY bar itself hasn't been fetched yet, `_completed_days`
# withholds one MORE daily bar than it should and the rule re-evaluates a bar a PRIOR cycle
# already traded. Nothing else on the live path dedupes an ENTRY (see this module's own
# docstring above), so that re-evaluation is a DUPLICATE REAL-MONEY ORDER, not a delayed one.
# `keel.data.freshness.entry_bar_ready` closes the gap; the tests below pin it at the
# `agent.run_once` level, where the gate is actually wired.

_DAY = 86_400
_HOUR = 3_600

# `turtle_breakout.py`'s own `_SMALL_PARAMS` (see `tests/strategy/test_turtle_breakout.py`),
# reused so `min_needed` (`max(lookbacks) + 2 == 7`) stays well under the 11-candle series below.
_TURTLE_PARAMS = {"entry_lookback": 5, "exit_lookback": 3, "adx_period": 5, "atr_period": 5}


def _turtle_daily_series() -> list[Candle]:
    """11 daily candles (epoch days 0-10): the zigzag base from
    `tests/strategy/test_engine.py::_uptrend_candles` (days 0-8, verified `regime.detect_condition
    == BULLISH` there -- a strictly monotonic series has no interior pivot and reads as CHOPPY,
    see `test_two_cycles_in_one_utc_day_reenter_the_same_turtle_breakout`'s docstring above)
    followed by TWO INDEPENDENT Donchian breakouts, day 9 and day 10 -- each verified directly
    against `TurtleBreakout.detect()` (with `_TURTLE_PARAMS`) to clear every gate on its own
    (Donchian high, ADX>threshold, kill-zone rr>=1) when it is the newest bar.

    Two consecutive breakouts model the hazard this section pins: day 9's breakout is one a
    correctly-anchored PRIOR cycle already traded (it was "yesterday's" freshly-closed bar
    then); day 10's is the genuine fresh one due THIS cycle. `_completed_days`'s over-eager
    two-bar drop hands day 9 back to `detect()` a second time -- there is nothing wrong with day
    9 itself, only with re-serving it.
    """

    def _c(ts: int, price: str) -> Candle:
        p = Decimal(price)
        return Candle(
            ts=ts,
            open=p - Decimal("0.5"),
            high=p + Decimal("0.5"),
            low=p - Decimal("0.5"),
            close=p,
            volume=Decimal("1"),
        )

    base_prices = ["100", "105", "102", "108", "104", "112", "109", "118", "114"]
    candles = [_c(i * _DAY, v) for i, v in enumerate(base_prices)]
    candles.append(_c(9 * _DAY, "140"))  # day 9: the "already traded" breakout
    candles.append(_c(10 * _DAY, "160"))  # day 10: the genuine fresh breakout
    return candles


def test_turtle_entry_is_blocked_when_the_hourly_series_has_not_crossed_the_day_close(repo):
    """THE REGRESSION, hourly-lag shape. At 01:20 UTC on "day 11", day 10 is the newest COMPLETE
    daily bar (`expected_last_ts`); the 00:00-01:00 UTC hourly bar (ts == day 11's 00:00) is
    what `_completed_days` needs closed to release it. Here the hourly series is stamped one
    bar SHORT of that -- a routine one-hour venue publication lag.

    PRE-FIX: `_completed_days` sees the late hourly series and drops day 10 (correctly) AND day
    9 (incorrectly), so `TurtleBreakout.detect()` fires on day 9's ALREADY-TRADED breakout --
    demonstrated below by `enter_signals` being non-empty, which is this test's load-bearing
    assertion; everything after it is the fixed behaviour.
    POST-FIX: the entry-readiness gate blocks the rule before `engine.evaluate` ever sees it,
    because the confirming hourly bar has not arrived -- independent of whatever
    `_completed_days` itself would compute.
    """
    candles = _turtle_daily_series()
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, candles)
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT, **_TURTLE_PARAMS}, status="live")

    today_open = 11 * _DAY  # "day 11" 00:00 UTC -- the confirming hourly bar's ts if current
    late_hourly = [_candle(today_open - _HOUR)]  # one bar SHORT of the boundary
    repo.upsert_candles(PRODUCT, Granularity.ONE_HOUR, late_hourly)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): candles,
            (PRODUCT, Granularity.ONE_HOUR): late_hourly,
        }
    )
    config = _config(
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        )
    )
    now_ts = today_open + _HOUR + 20 * 60  # 01:20 UTC on day 11 -- the first live trigger

    result = run_once(broker, repo, config, now_ts=now_ts)

    # THE BUG, stated as an assertion: pre-fix, this fires on day 9's already-traded breakout.
    assert result.enter_signals == [], (
        f"turtle_breakout fired on a bar this cycle's freshness could not confirm: "
        f"{result.enter_signals!r}"
    )
    assert all(not r.placed for r in result.enter_results)
    assert repo.get_orders(mode="live", product_id=PRODUCT) == []

    assert len(result.blocked_entries) == 1
    blocked = result.blocked_entries[0]
    assert blocked.product == PRODUCT
    assert blocked.rule_name == "turtle_breakout"
    assert blocked.granularity == Granularity.ONE_DAY


def test_turtle_entry_is_blocked_when_the_daily_bar_itself_has_not_been_fetched_yet(repo):
    """THE REGRESSION, daily-lag shape. Here the ONE_HOUR confirming series IS current, but the
    ONE_DAY series itself lags -- day 10's bar hasn't been fetched into the cache yet, so the
    newest stored daily bar (day 9) is one bar behind `expected_last_ts`. `_completed_days`
    drops nothing extra in this shape (there's nothing to drop -- day 10 was never there), but
    the series' own newest bar is still the already-traded day 9 breakout.

    Distinct failure mode from the hourly-lag test above (`bars_behind > 0` on the gated series
    itself, vs. an unconfirmed FINER series) -- both must block, via different `BarReadiness`
    reasons.
    """
    candles = _turtle_daily_series()
    stored_daily = candles[:-1]  # day 10 not fetched yet -- only through day 9
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, stored_daily)
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT, **_TURTLE_PARAMS}, status="live")

    today_open = 11 * _DAY
    current_hourly = [_candle(today_open)]  # the hourly series IS current this time
    repo.upsert_candles(PRODUCT, Granularity.ONE_HOUR, current_hourly)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): stored_daily,
            (PRODUCT, Granularity.ONE_HOUR): current_hourly,
        }
    )
    config = _config(
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        )
    )
    now_ts = today_open + _HOUR + 20 * 60

    result = run_once(broker, repo, config, now_ts=now_ts)

    # THE BUG: pre-fix, `_completed_days` doesn't drop anything here (the hourly series is
    # current), so `detect()` sees day 9 as the newest bar and fires on it -- already traded.
    assert result.enter_signals == [], (
        f"turtle_breakout fired on day 9's bar while day 10's had not been fetched yet: "
        f"{result.enter_signals!r}"
    )
    assert repo.get_orders(mode="live", product_id=PRODUCT) == []
    assert len(result.blocked_entries) == 1
    assert result.blocked_entries[0].granularity == Granularity.ONE_DAY


def test_turtle_entry_is_evaluated_and_placed_when_both_series_are_current(repo):
    """THE COUNTERWEIGHT to the two regression tests above: this gate must not become a blanket
    "never trade". When both series genuinely are current -- the normal case, true for ~23 of
    the 24 daily triggers -- the entry must fire and place exactly as it did before this gate
    existed. Same day-11 01:20 UTC instant, differing only in that the hourly series HAS crossed
    day 11's 00:00 close.
    """
    candles = _turtle_daily_series()
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, candles)
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT, **_TURTLE_PARAMS}, status="live")

    today_open = 11 * _DAY
    current_hourly = [_candle(today_open)]
    repo.upsert_candles(PRODUCT, Granularity.ONE_HOUR, current_hourly)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): candles,
            (PRODUCT, Granularity.ONE_HOUR): current_hourly,
        }
    )
    config = _config(
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        )
    )
    now_ts = today_open + _HOUR + 20 * 60

    result = run_once(broker, repo, config, now_ts=now_ts)

    assert len(result.enter_signals) == 1
    assert result.enter_signals[0].setup is not None
    assert result.enter_signals[0].setup.entry == Decimal("160")  # day 10's genuine breakout
    assert len(result.enter_results) == 1
    assert result.enter_results[0].placed is True
    assert result.blocked_entries == []
    # `get_orders` also carries the bracket's SELL leg (`place_bracket`'s protective stop) --
    # same convention `test_two_cycles_in_one_utc_day_reenter_the_same_turtle_breakout` above
    # uses -- so the BUY count, not the raw row count, is what proves exactly one entry placed.
    buy_orders = [o for o in repo.get_orders(mode="live", product_id=PRODUCT) if o["side"] == "BUY"]
    assert len(buy_orders) == 1


def test_exit_still_runs_while_a_different_rules_entry_is_blocked(repo):
    """The exit path must never be held hostage by the entry gate: an open position's rule-driven
    channel exit runs IN-PROCESS (unlike the protective stop, which rests at the broker), so it
    has to fire on a stale-feed cycle exactly as it would on a fresh one. Staying in a losing
    position an extra day because a DIFFERENT rule's confirming series lagged is strictly worse
    than a delayed entry. Seeds a held position owned by `fake_exit` (always exits) alongside a
    `turtle_breakout` rule blocked by the same late-hourly scenario as the first regression test
    above, both for the SAME product, and asserts both effects land in the one cycle.
    """
    _seed_open_position(repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000)
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    repo.set_state(f"position_rule:{PRODUCT}", "fake_exit")

    candles = _turtle_daily_series()
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, candles)
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT, **_TURTLE_PARAMS}, status="live")

    today_open = 11 * _DAY
    late_hourly = [_candle(today_open - _HOUR)]
    repo.upsert_candles(PRODUCT, Granularity.ONE_HOUR, late_hourly)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): candles,
            (PRODUCT, Granularity.ONE_HOUR): late_hourly,
        }
    )
    config = _config(
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        )
    )
    now_ts = today_open + _HOUR + 20 * 60

    result = run_once(broker, repo, config, now_ts=now_ts)

    assert len(result.exit_results) == 1
    assert result.exit_results[0].placed is True
    # THE BUG: pre-fix, turtle_breakout also fires here (it is blind to the same lag the exit
    # correctly ignores) -- this is the load-bearing assertion for the "before" transcript.
    assert result.enter_signals == [], (
        f"turtle_breakout should have been blocked but fired anyway: {result.enter_signals!r}"
    )
    blocked_names = {b.rule_name for b in result.blocked_entries}
    assert "turtle_breakout" in blocked_names
    # `fake_exit` declares neither `granularity` nor `timeframe`, so it is ALSO gated on the
    # coarsest configured granularity (ONE_DAY) and shows up here too -- harmlessly, since its
    # `detect()` always returns `None` and it was never going to enter regardless. This test
    # only asserts on turtle_breakout, which IS the hazard being pinned.


def test_entry_gate_granularity_falls_back_to_the_coarsest_configured_granularity_for_dca():
    """`Dca` declares neither `granularity` nor `timeframe`, yet `Dca.detect` reads
    `candles_by_tf[Granularity.ONE_DAY]` directly and keys its cadence off
    `latest.ts // 86400 % cadence_days` -- so a stale DAILY bar re-fires the same cadence hit
    and buys twice. `strategy.engine._trading_granularity`'s FINEST fallback (built for CTS
    *scoring*) is wrong here: gating DCA on FIFTEEN_MINUTE would miss the hazard entirely -- the
    daily bar could be weeks stale while FIFTEEN_MINUTE stayed perfectly fresh. The COARSEST
    configured granularity is the fallback that fails in the safe direction for any rule that
    does not declare what it reads.
    """
    rule = Dca(product_id=PRODUCT)
    granularities = [Granularity.ONE_DAY, Granularity.ONE_HOUR, Granularity.FIFTEEN_MINUTE]

    assert agent._entry_gate_granularity(rule, granularities) == Granularity.ONE_DAY


def test_run_once_blocks_a_dca_entry_on_a_stale_daily_bar(repo):
    """The same gate, applied to a rule that declares no `granularity`/`timeframe` attribute at
    all -- `Dca` reads `ONE_DAY` directly (see the `_entry_gate_granularity` test above), so a
    stale daily bar must block it exactly like a declared-granularity rule, using the coarsest
    fallback. `cadence_days=1` makes every stored bar a cadence hit regardless of its ts, so a
    fire here can only be explained by the gate being absent, not by an off-cadence miss.
    """
    repo.insert_rule(
        "dca",
        {"product_id": PRODUCT, "cadence_days": 1},
        status="live",
    )
    stale_daily = [_candle(0, "100")]  # epoch day 0 -- wildly behind
    # Kept fresh so `market_feed.is_fresh`'s STALE-FEED skip (gated on the FINEST configured
    # granularity) doesn't pre-empt this test before it ever reaches the entry gate.
    fresh_hourly = [_candle(5 * _DAY + 60, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, stale_daily)
    repo.upsert_candles(PRODUCT, Granularity.ONE_HOUR, fresh_hourly)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): stale_daily,
            (PRODUCT, Granularity.ONE_HOUR): fresh_hourly,
        }
    )
    config = _config(
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        )
    )
    now_ts = 5 * _DAY + 2 * _HOUR

    result = run_once(broker, repo, config, now_ts=now_ts)

    # THE BUG: pre-fix, DCA's cadence math fires off the stale day-0 bar regardless of how far
    # behind it is -- nothing today checks the daily bar's own freshness before `detect()` runs.
    assert result.enter_signals == [], f"dca fired on a stale daily bar: {result.enter_signals!r}"
    assert repo.get_orders(mode="live", product_id=PRODUCT) == []
    assert len(result.blocked_entries) == 1
    assert result.blocked_entries[0].rule_name == "dca"
    assert result.blocked_entries[0].granularity == Granularity.ONE_DAY


# -- entry admission is WHOLE-CYCLE, not per-rule (adversarial review of PR #174) ---------------
#
# The gate pinned above closed the "which BAR" half of Finding 1 but was wired at the wrong
# GRANULARITY: `ready_rules` was filtered PER PRODUCT, inside the very loop that also calls
# `executor.execute` for that product's signals. `blocked_entries` was populated correctly, but
# nothing consulted it before an order went out for an EARLIER, unrelated product in the same
# `products` loop -- and the cycle could still finish with `blocked_entries` non-empty, which is
# what the CLI's exit code is keyed off. `keel-live-run.sh` reads that exit code alone to decide
# whether to stamp the UTC day; a nonzero exit after orders already placed makes it decline to
# stamp and retry the WHOLE cycle next hour, re-entering whatever already placed. The tests below
# pin the fix: entry admission is now decided ONCE, for the WHOLE cycle, before any
# `executor.execute`/`_paper_enter` runs -- see the pre-pass comment in `agent.run_once`.

_XLM = "XLM-USD"  # sorts AFTER "BTC-USD" (`products = sorted(...)`) -- see the regression test.


def _blocked_cycle_config(**overrides: Any) -> Config:
    """`interval_sec=100_000` -> `max_age_sec = 300_000`s (`FEED_STALENESS_CYCLES == 3`). Wide
    enough that a daily series exactly ONE bar behind its own `expected_last_ts` -- which costs
    close to TWO calendar days of `now_ts - stored_ts`, since both "one bar behind" and "now
    sitting right at the top of its own day" each contribute a step -- still reads as FRESH to
    `market_feed.is_fresh`. That is deliberate: every test below wants the product gated by the
    stricter `freshness.entry_bar_ready` (blocked), never pre-empted by the looser staleness
    skip (which would prove nothing about this section's hazard). Shared so the arithmetic is
    worked out once.
    """
    return _config(auto_trade=AutoTradeConfig(mode="confirm", interval_sec=100_000), **overrides)


def test_a_ready_products_order_placed_before_a_blocked_products_own_check_is_the_regression(
    repo,
):
    """THE REGRESSION. Two products, one cycle: `BTC-USD` sorts before `XLM-USD`
    (`products = sorted(...)`), so the OLD per-product loop reached BTC-USD FIRST, found its DCA
    rule ready, and placed a REAL order via `executor.execute` -- ONLY THEN did the loop reach
    XLM-USD and discover its own DCA rule was blocked. `blocked_entries` ended up non-empty, but
    nothing before this fix ever consulted it before BTC-USD's order went out.

    PRE-FIX: this must fail by showing BTC-USD's order placed anyway, alongside a non-empty
    `blocked_entries` -- captured verbatim in the PR transcript before the fix landed.
    POST-FIX: a blocked rule ANYWHERE in the cycle withholds EVERY entry that cycle, so
    BTC-USD's otherwise-ready order never places either, and `enter_signals` stays empty --
    nothing was ever handed to `engine.evaluate` at all.
    """
    config = _blocked_cycle_config()
    now_ts = 11 * _DAY  # start of day 11 -- `expected_last_ts(ONE_DAY)` resolves to day 10.

    # BTC-USD: READY. Daily bar stored exactly at day 10 (== expected); `cadence_days=1` makes
    # every stored bar an unconditional cadence hit (`day % 1 == 0` always).
    repo.insert_rule("dca", {"product_id": PRODUCT, "cadence_days": 1}, status="live")
    ready_daily = [_candle(10 * _DAY, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, ready_daily)

    # XLM-USD: BLOCKED. Daily bar stored at day 9 -- one bar SHORT of day 10 -- so
    # `entry_bar_ready` reports `bars_behind == 1`, reason "behind".
    repo.insert_rule("dca", {"product_id": _XLM, "cadence_days": 1}, status="live")
    blocked_daily = [_candle(9 * _DAY, "100")]
    repo.upsert_candles(_XLM, Granularity.ONE_DAY, blocked_daily)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): ready_daily,
            (_XLM, Granularity.ONE_DAY): blocked_daily,
        }
    )

    result = run_once(broker, repo, config, now_ts=now_ts)

    assert len(result.blocked_entries) == 1
    assert result.blocked_entries[0].product == _XLM

    assert result.enter_signals == [], (
        f"BTC-USD's DCA entry was evaluated even though XLM-USD's was blocked this cycle: "
        f"{result.enter_signals!r}"
    )
    assert all(not r.placed for r in result.enter_results)
    assert repo.get_orders(mode="live", product_id=PRODUCT) == [], (
        "BTC-USD's order placed before XLM-USD's block was ever accounted for -- this IS the bug"
    )


def test_retry_after_the_late_series_catches_up_places_exactly_one_order(repo):
    """THE END-TO-END STATEMENT OF THE FIX -- the property that actually matters is not merely
    "a blocked cycle places nothing" but that a RETRY of the same cycle, once the late series has
    caught up, is IDEMPOTENT: it must not somehow end up placing what the first, blocked attempt
    already would have, on top of what it places now. Two `run_once` calls at the SAME `now_ts`
    -- the runner's own retry shape, an hourly trigger re-running the identical cycle against the
    identical bar after a missed day-stamp (see `keel-live-run.sh`'s header) -- first with
    XLM-USD's daily bar one bar behind (blocked, zero orders anywhere), second with it caught up
    (nothing blocked, BTC-USD's ready DCA finally places, exactly once).

    XLM-USD's own DCA uses `cadence_days=3` (`10 % 3 != 0`) so that even once its data is fresh
    and it stops being blocked, it still does not itself fire -- isolating this test to the ONE
    order the fix is actually responsible for, rather than also depending on a second rule
    firing correctly.
    """
    config = _blocked_cycle_config()
    now_ts = 11 * _DAY

    repo.insert_rule("dca", {"product_id": PRODUCT, "cadence_days": 1}, status="live")
    ready_daily = [_candle(10 * _DAY, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, ready_daily)

    repo.insert_rule("dca", {"product_id": _XLM, "cadence_days": 3}, status="live")
    blocked_daily = [_candle(9 * _DAY, "100")]
    repo.upsert_candles(_XLM, Granularity.ONE_DAY, blocked_daily)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): ready_daily,
            (_XLM, Granularity.ONE_DAY): blocked_daily,
        }
    )

    first = run_once(broker, repo, config, now_ts=now_ts)
    assert first.blocked_entries != []
    assert first.enter_signals == []
    assert repo.get_orders(mode="live") == []

    # The late series catches up -- both in the repo (what `run_once` reads) and the fake broker
    # (what a subsequent `market_feed.poll_once` would re-serve, so the retry is realistic).
    caught_up_daily = [_candle(9 * _DAY, "100"), _candle(10 * _DAY, "100")]
    repo.upsert_candles(_XLM, Granularity.ONE_DAY, caught_up_daily)
    broker._series[(_XLM, Granularity.ONE_DAY)] = caught_up_daily

    second = run_once(broker, repo, config, now_ts=now_ts)
    assert second.blocked_entries == []

    orders = repo.get_orders(mode="live")
    assert len(orders) == 1, (
        f"expected exactly one order across BOTH cycles -- the blocked first cycle must not "
        f"have left BTC-USD's entry half-placed for the second cycle to duplicate: {orders!r}"
    )
    assert orders[0]["product_id"] == PRODUCT
    assert orders[0]["side"] == "BUY"


def test_same_product_two_rules_one_ready_one_blocked_neither_places(repo):
    """Same product, two rules that gate on DIFFERENT granularities: `dca` falls back to the
    coarsest configured granularity (`ONE_DAY`), `pullback_continuation` is given an explicit
    `granularity=ONE_HOUR`. Constructed so the ONE_HOUR series is current with respect to ITS
    OWN expectation (ready, on its own) while the ONE_DAY series' CONFIRMING hourly bar has not
    yet crossed into the new day (blocked) -- i.e. right after midnight, before the first hourly
    bar of the new day has closed.

    Neither rule needs to actually detect a tradeable setup: readiness is a property of the
    CANDLE TIMESTAMPS alone (`freshness.entry_bar_ready` never calls `rule.detect()`), and post-
    fix `engine.evaluate()` is never even invoked while any rule anywhere is blocked -- so this
    only has to pin that ONE blocked rule on a product is enough to withhold that SAME product's
    OTHER, individually-ready rule too.
    """
    config = _blocked_cycle_config(
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        )
    )
    midnight = 11 * _DAY
    now_ts = midnight + 300  # 00:05 UTC on day 11 -- just past midnight.

    # ONE_DAY series: yesterday's (day 10) bar, exactly `expected_last_ts(ONE_DAY)` -- current on
    # its own terms.
    daily = [_candle(10 * _DAY, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, daily)
    repo.insert_rule("dca", {"product_id": PRODUCT, "cadence_days": 1}, status="live")

    # ONE_HOUR series: yesterday 23:00's bar -- current w.r.t. ONE_HOUR's OWN expectation (the
    # 00:00-01:00 bar is still forming), but it has NOT crossed today's 00:00 boundary, so it
    # cannot confirm the daily bar above.
    hourly = [_candle(midnight - _HOUR, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_HOUR, hourly)
    repo.insert_rule(
        "pullback_continuation", {"product_id": PRODUCT, "granularity": "ONE_HOUR"}, status="live"
    )

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): daily,
            (PRODUCT, Granularity.ONE_HOUR): hourly,
        }
    )

    result = run_once(broker, repo, config, now_ts=now_ts)

    blocked_names = {b.rule_name for b in result.blocked_entries}
    assert blocked_names == {"dca"}, (
        f"expected only the ONE_DAY-gated dca rule blocked, not pullback_continuation "
        f"(ONE_HOUR-gated, current on its own terms): {result.blocked_entries!r}"
    )
    assert result.enter_signals == [], (
        "pullback_continuation must not have been evaluated either -- one blocked rule on this "
        f"product withholds the WHOLE product's entries, not just the rule that was itself "
        f"blocked: {result.enter_signals!r}"
    )
    assert repo.get_orders(mode="live", product_id=PRODUCT) == []


def test_exit_still_runs_when_a_different_products_blocked_entry_withholds_the_whole_cycle(repo):
    """Complements `test_exit_still_runs_while_a_different_rules_entry_is_blocked` above (same
    product, two rules) with the shape that matters post-fix: a DIFFERENT PRODUCT's block now
    withholds the whole cycle's entries, not just its own. BTC-USD holds a position owned by
    `fake_exit` (always exits); XLM-USD's `dca` entry is blocked. Exits are exempt from
    `entries_allowed` entirely (see the pre-pass comment in `run_once`) -- protective stops rest
    at the broker, but the channel exit here runs IN-PROCESS, and holding a losing position an
    extra cycle is strictly worse than a delayed entry -- so BTC-USD's exit must still fire while
    `enter_signals` stays empty for the whole cycle.
    """
    config = _blocked_cycle_config()
    now_ts = 11 * _DAY

    _seed_open_position(
        repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000, rule_name="fake_exit"
    )
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    repo.set_state(f"position_rule:{PRODUCT}", "fake_exit")
    fresh_daily = [_candle(10 * _DAY, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, fresh_daily)

    repo.insert_rule("dca", {"product_id": _XLM, "cadence_days": 1}, status="live")
    blocked_daily = [_candle(9 * _DAY, "100")]
    repo.upsert_candles(_XLM, Granularity.ONE_DAY, blocked_daily)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): fresh_daily,
            (_XLM, Granularity.ONE_DAY): blocked_daily,
        }
    )

    result = run_once(broker, repo, config, now_ts=now_ts)

    assert len(result.exit_results) == 1
    assert result.exit_results[0].placed is True
    assert result.enter_signals == [], (
        f"entries should have been withheld for the whole cycle: {result.enter_signals!r}"
    )
    blocked_products = {b.product for b in result.blocked_entries}
    assert blocked_products == {_XLM}


def test_a_stale_products_missing_feed_does_not_withhold_a_different_products_entry(repo):
    """Pins the ORDERING the pre-pass in `run_once` must preserve: the stale-feed check
    (`market_feed.is_fresh`) runs BEFORE the entry-readiness gate and `continue`s past a stale
    product entirely -- it never reaches `freshness.entry_bar_ready` and so never contributes to
    `blocked_entries`. A dead venue or a delisted product must not be able to silently halt every
    OTHER product's trading merely by having no feed at all; only a product that IS being polled,
    and is merely a bar or two behind, can withhold the whole cycle's entries (see the pre-pass
    comment in `run_once`).
    """
    config = _blocked_cycle_config()
    now_ts = 11 * _DAY

    # XLM-USD: no candles ever recorded -- `market_feed.is_fresh` -> False, "no stored candle at
    # all" -- skipped as STALE, exactly like `test_stale_feed_skips_trading_for_that_product`.
    repo.insert_rule("dca", {"product_id": _XLM, "cadence_days": 1}, status="live")

    # BTC-USD: healthy and ready -- an ordinary cadence-hit DCA entry.
    repo.insert_rule("dca", {"product_id": PRODUCT, "cadence_days": 1}, status="live")
    ready_daily = [_candle(10 * _DAY, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, ready_daily)

    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): ready_daily})

    result = run_once(broker, repo, config, now_ts=now_ts)

    assert result.stale_products == [_XLM]
    assert result.blocked_entries == [], (
        f"a stale product must not surface as a BLOCKED entry -- it never reached the readiness "
        f"gate at all: {result.blocked_entries!r}"
    )
    assert len(result.enter_signals) == 1
    assert result.enter_signals[0].product_id == PRODUCT
    orders = repo.get_orders(mode="live", product_id=PRODUCT)
    assert len(orders) == 1
    assert orders[0]["side"] == "BUY"


def test_retry_after_a_blocked_cycle_does_not_duplicate_an_already_placed_exit(repo):
    """THE PROPERTY THAT MAKES "the runner declines to stamp and retries" SAFE FOR EXITS. #174
    turned "a nonzero exit runs after exits have already run this UTC day" from a rare,
    exception-only path into a ROUTINE one: every blocked cycle now takes it, because a blocked
    cycle still runs `_handle_exits` for every non-stale product (see the pre-pass comment in
    `run_once`) and then exits 4, which makes `keel-live-run.sh` decline to stamp the day, and
    one of the remaining hourly triggers re-runs the SAME cycle an hour later. If that retry
    re-placed an already-placed exit, it would SELL a position that was already sold --
    dumping it a second time into whatever the market happens to be doing an hour on.

    Seeds a position on BTC-USD owned by `fake_exit` (`exit_signal` always fires) alongside a
    blocked XLM-USD `dca` entry, so the FIRST cycle both places the exit AND reports
    `blocked_entries` non-empty -- the exact shape #174 makes routine. It then re-runs `run_once`
    at the SAME `now_ts` against the SAME repo/broker state, exactly as the retried hourly
    trigger would, and asserts exactly ONE SELL order exists across both cycles.

    CORRECTING THE STATED PREMISE, by mutation, not by inspection alone: this test was
    commissioned to pin `_handle_exits` clearing `agent_state["position_rule:<product>"]` on
    placement (~line 631) as THE mechanism that stops the duplicate. That is not what a mutation
    test can honestly show. Un-clearing `position_rule:<product>` ALONE does NOT turn this test
    red -- `_handle_exits`'s own `if qty <= 0: return []` (`_held_position`, the filled-orders
    audit log) already reads the position as closed on the retry: an exit is always a market
    order, so its own SELL is recorded `status="filled"` the instant it places, and net qty
    (buys minus sells) is what BOTH `agent._handle_exits` AND, independently,
    `execution.executor._build_intent`'s own separate `_held_position` check before either one
    ever consults `position_rule` at all. The system is triple-redundant: that qty check in
    `agent.py`, the same qty check (separately implemented) in `executor.py`, and the
    `position_rule` clear each independently block the duplicate -- breaking any ONE OR TWO of
    the three still leaves this test green, and only breaking all three at once produces a
    second SELL. `position_rule` clearing is real and matters, but for a DIFFERENT reason (see
    its own comment at the clear site: stale bracket state poisoning the NEXT position opened on
    this product, not this retry) -- not for the property this test pins. The primary mechanism
    for THIS property is the audit-log qty netting in `_held_position`: a placed exit is itself
    a filled SELL, so the very next read of "what do we hold" already reflects it.
    """
    config = _blocked_cycle_config()
    now_ts = 11 * _DAY

    # BTC-USD holds a position owned by `fake_exit` (always exits).
    _seed_open_position(
        repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000, rule_name="fake_exit"
    )
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    repo.set_state(f"position_rule:{PRODUCT}", "fake_exit")
    fresh_daily = [_candle(10 * _DAY, "100")]
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, fresh_daily)

    # XLM-USD's dca entry is blocked -- withholds the WHOLE cycle's entries, exactly the
    # scenario a real blocked cycle exits #174 makes ROUTINE.
    repo.insert_rule("dca", {"product_id": _XLM, "cadence_days": 1}, status="live")
    blocked_daily = [_candle(9 * _DAY, "100")]
    repo.upsert_candles(_XLM, Granularity.ONE_DAY, blocked_daily)

    broker = FakeBroker(
        series={
            (PRODUCT, Granularity.ONE_DAY): fresh_daily,
            (_XLM, Granularity.ONE_DAY): blocked_daily,
        }
    )

    first = run_once(broker, repo, config, now_ts=now_ts)
    assert first.blocked_entries != []
    assert len(first.exit_results) == 1
    assert first.exit_results[0].placed is True

    # The retry: SAME `now_ts`, SAME repo/broker state -- exactly what the next hourly trigger
    # runs, because the blocked cycle's exit-4 left the UTC day unstamped (`keel-live-run.sh`).
    second = run_once(broker, repo, config, now_ts=now_ts)
    # XLM-USD's data hasn't caught up between cycles (nothing in this test advances it), so the
    # retry is blocked again too -- confirms this really is the SAME cycle re-running against
    # the SAME unconfirmed bar, not a coincidentally-unblocked second attempt.
    assert second.blocked_entries != []

    orders = repo.get_orders(mode="live", product_id=PRODUCT)
    sells = [o for o in orders if o["side"] == "SELL"]
    assert len(sells) == 1, (
        f"the retry duplicated the exit -- expected exactly one SELL across both cycles, "
        f"got {len(sells)}: {sells!r}"
    )


# -- post-cycle notifications (#444) ------------------------------------------------------------
#
# Notify-only, default-off: `run_once` derives events from the same seams doctor reads and
# hands them to `keel_core.notifications` for delivery, AFTER the cycle's trading work is
# done. These two tests pin the two contract halves -- disabled means ZERO transport calls,
# and a warn-state cycle with the event opted in delivers exactly that event.

# A realistic epoch (2026-09-13 08:00 UTC), so the rail-17 fixture below is genuinely the
# WARN branch: attested 5 days before NOW, i.e. 2 of the 7 TTL days remain. A negative
# `withdrawals_attested_at` would take doctor's never-attested FAIL branch instead -- a
# different (sharper) finding than the comments and assertions here describe.
_NOTIFY_NOW = 1_800_000_000
_NOTIFY_ATTESTED_AT = _NOTIFY_NOW - 5 * 86_400
# The latest CLOSED daily candle as of NOW (day 20_833 opens at 1_799_971_200), on a DCA
# cadence boundary (20_832 % 7 == 0) and inside rail 12's staleness window (< 150_000s old).
_NOTIFY_CANDLE_TS = 1_799_884_800


class _NotifySink:
    """Records what the notification transport would have POSTed (stands in for
    `keel_core.notifications.post_json`, no network)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, body: bytes) -> None:
        self.calls.append((url, body.decode("utf-8")))


def _notify_config(events: frozenset[str]) -> Config:
    from keel_core.notifications import NotificationSettings

    return _config(notifications=NotificationSettings(events=events))


def _notify_re_attest_subscription(repo) -> None:
    """The fixture's subscription (attested at ts 0, due 31.5M) is long EXPIRED by the
    realistic epoch these tests run at -- rail 14 would veto the BUY and the test would stop
    being about rail 17's notification. Re-attest a valid one for the same reason the fixture
    attests at all: rail 14 is not what these tests are about."""
    attest_subscription(repo, now_ts=_NOTIFY_NOW - 60 * 86_400, free_volume_usd=Decimal("10000000"))


def test_a_cycle_with_notifications_disabled_makes_zero_transport_calls(repo, monkeypatch):
    """The default-off guarantee, at the loop level: the SAME warn state that would notify an
    opted-in deployment produces no delivery attempt at all -- no URL resolution shortcut, no
    half-open transport, nothing."""
    sink = _NotifySink()
    monkeypatch.setattr("keel_core.notifications.post_json", sink)
    monkeypatch.setenv("KEEL_ALERT_WEBHOOK", "https://alerts.example/hook")
    # rail 17 at 5 of its 7 days (2 remain): the WARN state an opted-in deployment would be
    # told about -- doctor's `attest.withdrawals` WARN, not the never-attested FAIL.
    repo.set_state("withdrawals_attested_at", _NOTIFY_ATTESTED_AT)
    # The module fixture attests the cash posture at ts 0, which is already expired by
    # `_NOTIFY_NOW`. Rail 22 is not what this test is about, so re-attest on its clock (#691).
    attest_cash_posture(repo, now_ts=_NOTIFY_NOW)
    _notify_re_attest_subscription(repo)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(_NOTIFY_CANDLE_TS, "100")]}
    )

    run_once(broker, repo, _notify_config(frozenset()), now_ts=_NOTIFY_NOW)

    assert sink.calls == []


def test_a_warn_state_cycle_emits_exactly_the_opted_in_events(repo, monkeypatch):
    """Opt in ONE event, craft exactly that warn state, and the cycle delivers it once --
    with the event key in the payload -- while the healthy facts of the same cycle (the
    placed DCA entry, the fresh feed, the ample allowance) notify nothing."""
    import json

    sink = _NotifySink()
    monkeypatch.setattr("keel_core.notifications.post_json", sink)
    monkeypatch.setenv("KEEL_ALERT_WEBHOOK", "https://alerts.example/hook")
    # attested 5 days before NOW: 2 of the 7 TTL days remain -> doctor WARNs (not the
    # never-attested FAIL a negative epoch would take).
    repo.set_state("withdrawals_attested_at", _NOTIFY_ATTESTED_AT)
    # The module fixture attests the cash posture at ts 0, which is already expired by
    # `_NOTIFY_NOW`. Rail 22 is not what this test is about, so re-attest on its clock (#691).
    attest_cash_posture(repo, now_ts=_NOTIFY_NOW)
    _notify_re_attest_subscription(repo)
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(_NOTIFY_CANDLE_TS, "100")]}
    )

    result = run_once(
        broker, repo, _notify_config(frozenset({"attestation.expiring"})), now_ts=_NOTIFY_NOW
    )

    assert result.enter_results[0].placed is True  # the cycle itself traded normally
    assert len(sink.calls) == 1
    assert sink.calls[0][0] == "https://alerts.example/hook"
    assert json.loads(sink.calls[0][1])["event"] == "attestation.expiring"


# -- #446's exit-side sibling: a market exit the venue filled SHORT ---------------------------


class _ShortFillingBroker(FakeBroker):
    """Fills every SELL at `fill_ratio` of what was ordered, and reports it the way the venue
    does -- through `get_order`, which is what `_record_observed_fill_quantity` reads."""

    def __init__(self, fill_ratio: Decimal, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fill_ratio = fill_ratio
        self._last_sell_size: Decimal | None = None

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        size = getattr(spec, "base_size", None)
        if size is not None and getattr(spec, "side", None) == Side.SELL:
            self._last_sell_size = Decimal(size)
        return super().place_order(spec, idempotency_key=idempotency_key)

    def get_order(self, order_id: str) -> OrderStatus:
        ordered = self._last_sell_size or Decimal("0")
        return OrderStatus(
            order_id=order_id,
            status="FILLED",
            filled_size=ordered * self.fill_ratio,
            average_filled_price=Decimal("51000"),
            total_fees=Decimal("0.50"),
        )


def test_a_short_filled_exit_books_only_what_sold_and_keeps_the_remainder(
    repo: Repository, caplog
) -> None:
    """#446's exit-side sibling, which #502 was asked to settle in the same pass.

    `_close_tranches` booked `exit_qty=position["qty"]` and then closed the tranche, for every
    open tranche of the product, on the strength of the order having been PLACED. Nothing in
    that path ever consulted how much the venue actually sold. A market exit filled short
    therefore wrote a `trade_outcomes` row for quantity that was never sold, marked a tranche
    closed while its base was still held, and -- worse than either -- cleared `position_rule:`,
    `open_stop`, `open_target` and the crash ledger, so the surviving remainder had no owning
    rule to manage it, no levels to re-place from, and nothing saying it was unprotected. Its
    bracket had already been cancelled to place the exit. That is a live, naked, SILENT
    position: exactly the state #519 built the crash ledger to make impossible.

    Here the venue sells 60% of a 0.2 position. The correct outcome is: nothing booked (the
    trade is not over), the tranche reduced to what is still held, every piece of position state
    RETAINED, the crash ledger written so the next cycle's sweep re-places, and a CRITICAL.
    """
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(
        repo, PRODUCT, Decimal("0.2"), Decimal("50000"), ts=1_000, rule_name="fake_exit"
    )
    repo.set_state(f"position_rule:{PRODUCT}", {"rule_name": "fake_exit", "opened_at": 1_000})
    repo.set_state(f"open_stop:{PRODUCT}", Decimal("49000"))
    repo.set_state(f"open_target:{PRODUCT}", Decimal("53000"))
    broker = _ShortFillingBroker(
        Decimal("0.6"), series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]}
    )

    with caplog.at_level(logging.CRITICAL):
        run_once(broker, repo, _config(), now_ts=90_000)

    positions = repo.get_open_positions(PRODUCT)
    assert len(positions) == 1, "the tranche was closed on the strength of a SHORT fill"
    assert positions[0]["qty"] == Decimal("0.08"), (
        "the tranche must be reduced to what is still held: the sweep sizes its healing bracket "
        "from this number, and an over-sized bracket is refused for insufficient base"
    )
    assert positions[0]["realized_qty"] == Decimal("0.12")
    assert repo.get_trade_outcomes() == [], (
        "an outcome row was written for a trade that is not over -- and for quantity the venue "
        "never sold"
    )

    assert repo.get_state(f"position_rule:{PRODUCT}") is not None, (
        "the remainder was left with no owning rule, so no later cycle would ever manage it"
    )
    assert repo.get_state(f"open_stop:{PRODUCT}") == Decimal("49000")
    assert repo.get_state(f"open_target:{PRODUCT}") == Decimal("53000")
    assert repo.get_state(f"{executor.UNBRACKETED_PREFIX}{PRODUCT}") == {
        "stop": Decimal("49000"),
        "target": Decimal("53000"),
        "qty": Decimal("0.08"),
    }, "the naked remainder is not in the crash ledger, so the sweep will never heal it"
    assert [
        r for r in caplog.records if r.getMessage() == "agent.exit_left_an_unprotected_remainder"
    ], "an unprotected remainder was left behind SILENTLY"


def test_a_fully_filled_exit_still_closes_everything_and_clears_the_state(
    repo: Repository,
) -> None:
    """The other side of the pin above, and the reason `observed_sold_qty` returns `None` for a
    full fill rather than the number it observed.

    A full fill closes EVERY open tranche, which is not the same as consuming the order's
    quantity FIFO: `_build_intent` sizes an exit from the filled-orders history while the
    tranches come from the `positions` ledger, and the two legitimately disagree for inventory
    that predates the ledger. Consuming FIFO would strand those tranches open forever, so the
    only behaviour #446's fix changes is the short-fill one.
    """
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(
        repo, PRODUCT, Decimal("0.2"), Decimal("50000"), ts=1_000, rule_name="fake_exit"
    )
    repo.set_state(f"position_rule:{PRODUCT}", {"rule_name": "fake_exit", "opened_at": 1_000})
    repo.set_state(f"open_stop:{PRODUCT}", Decimal("49000"))
    broker = _ShortFillingBroker(
        Decimal("1"), series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]}
    )

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_open_positions(PRODUCT) == []
    assert len(repo.get_trade_outcomes()) == 1
    assert repo.get_state(f"position_rule:{PRODUCT}") is None
    assert repo.get_state(f"open_stop:{PRODUCT}") is None


# -- the orphan-bracket sweep runs, and runs LAST (#668) ----------------------------------------


def test_run_once_sweeps_orphan_brackets_after_the_rebracket_pass(repo, monkeypatch):
    """The sweep is wired into the cycle, and its POSITION in the cycle is the point.

    A function nothing calls reports nothing, and no test above this one would notice: every
    sweep test drives `sweep_orphan_brackets` directly. This asserts the wiring.

    The order matters as much as the presence. `reconcile_unbracketed_positions` heals a tranche
    that has no bracket, and for a tranche whose position left the account out of band it will
    PLACE one -- against nothing. Sweeping afterwards makes the cancel the cycle's final word,
    so nothing re-creates within the same cycle what the sweep just removed. Running it first
    would leave a fresh orphan resting for a full day, and this deployment cycles once per UTC
    day.
    """
    from keel.execution import reconcile

    calls: list[str] = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append(name)
            return []

        return _fn

    monkeypatch.setattr(reconcile, "reconcile_open_orders", record("open_orders"))
    monkeypatch.setattr(reconcile, "reconcile_unbracketed_positions", record("unbracketed"))
    monkeypatch.setattr(reconcile, "sweep_orphan_brackets", record("orphan_sweep"))
    repo.set_state("kill_switch", False)

    run_once(FakeBroker(), repo, _config(), now_ts=90_000)

    assert calls == ["open_orders", "unbracketed", "orphan_sweep"], (
        f"expected the three reconciliation passes in order, got {calls}"
    )
