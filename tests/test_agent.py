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

from decimal import Decimal
from typing import Any

import pytest

from keel import agent
from keel.agent import LoopResult, _build_rule, loop, run_once
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.rules.base import Rule, Setup
from keel.strategy.rules.pullback_continuation import PullbackContinuation
from keel.types import Candle, Granularity, Side
from tests.conftest import attest_subscription

PRODUCT = "BTC-USD"


# -- fakes --------------------------------------------------------------------------------


class FakeBroker:
    """Fake broker -- serves canned candles (`market_feed`) + order responses (`executor`).

    No network: `get_candles` reads from an injected in-memory series (like
    `test_market_feed.FakeClient`); `preview_order`/`place_order` return canned,
    `CoinbaseClient`-shaped responses (like `test_executor.FakeBroker`).
    """

    def __init__(
        self, series: dict[tuple[str, Granularity], list[Candle]] | None = None
    ) -> None:
        self._series = series or {}
        self.get_candles_calls: list[tuple[str, Granularity, int, int]] = []
        self.preview_calls: list[dict[str, Any]] = []
        self.place_calls: list[dict[str, Any]] = []
        self._order_seq = 0

    def get_accounts(self) -> list[dict[str, Any]]:
        """A comfortably large USDC balance -- rail 13 (USDC-funding) fails closed otherwise."""
        return [{"currency": "USDC", "available_balance": Decimal("1000000")}]

    def get_candles(
        self, product_id: str, granularity: Granularity, start: int, end: int
    ) -> list[Candle]:
        self.get_candles_calls.append((product_id, granularity, start, end))
        series = self._series.get((product_id, granularity), [])
        return [c for c in series if start <= c.ts <= end]

    def preview_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        self.preview_calls.append({"product_id": product_id, "side": side})
        return {
            "order_total": Decimal("50.00"),
            "commission_total": Decimal("0"),
            "errs": [],
            "warning": [],
        }

    def place_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        self._order_seq += 1
        order_id = f"broker-order-{self._order_seq}"
        self.place_calls.append({"product_id": product_id, "side": side})
        side_str = side.value if isinstance(side, Side) else side
        return {
            "success": True,
            "order_id": order_id,
            "product_id": product_id,
            "side": side_str,
            "client_order_id": f"client-{order_id}",
            "order_configuration": order_configuration,
            "error": None,
        }

    def cancel_order(self, order_id: str) -> None:
        pass


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


# -- fixtures / builders --------------------------------------------------------------------


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    # `_config()` below defaults `auto_trade.mode` to "bypass" -- Issue #60 (bypass-arm
    # hardening) means `run_once` now refuses that mode unarmed. Every pre-existing test in
    # this module was written against "bypass just works"; arming here (a huge ttl so no test's
    # `now_ts` can ever run past `armed_until`) keeps that behavior for tests that aren't
    # specifically exercising the arm/disarm/expiry gate itself (those call
    # `repo.disarm_bypass()` or a short ttl explicitly).
    r.arm_bypass(now_ts=0, ttl_sec=10**12)
    # rail 14 now derives its cap from the attested subscription record rather than a config
    # default; attest a very large allowance so pre-existing tests here (none of which exercise
    # rail 14) aren't incidentally tripped by it.
    attest_subscription(r, now_ts=0, free_volume_usd=Decimal("10000000"))
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
        auto_trade=AutoTradeConfig(mode="bypass", interval_sec=50_000),
        money_mgmt=MoneyMgmtConfig(),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _candle(ts: int, price: str = "100") -> Candle:
    p = Decimal(price)
    return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))


def _seed_open_position(
    repo: Repository, product_id: str, qty: Decimal, price: Decimal, ts: int
) -> None:
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
            confirmation="bypass",
            rule_id=None,
            created_at=ts,
            updated_at=ts,
        )
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
    assert repo.get_state(f"position_rule:{PRODUCT}") == "dca"
    # and records that the feed was checked this cycle (guards rail 12 reads this).
    assert repo.get_state("last_feed_ts") == 90_000


# -- run_once: bypass-arm hardening (Issue #60) --------------------------------------------------


def test_bypass_without_armed_token_places_nothing_and_reports_refusal(repo):
    """The core fix: `config.auto_trade.mode == "bypass"` with no armed token must not place
    any order, even though a real, merged `Dca` rule would otherwise fire -- it fails safe by
    falling back to confirm behavior (`confirm_fn=None` -> never placed) and surfaces why.
    """
    repo.disarm_bypass()
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)

    assert result.skipped is False
    assert len(result.enter_signals) == 1  # the rule still fires...
    assert result.enter_results[0].placed is False  # ...but nothing is placed.
    assert broker.place_calls == []
    assert result.mode == "confirm"  # fell back, fail-safe
    assert result.bypass_refused_reason is not None
    assert "armed" in result.bypass_refused_reason.lower()
    assert repo.get_orders() == []


def test_bypass_with_expired_token_places_nothing(repo):
    repo.arm_bypass(now_ts=1_000, ttl_sec=10)  # armed_until = 1_010
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)  # long past armed_until

    assert result.enter_results[0].placed is False
    assert broker.place_calls == []
    assert result.mode == "confirm"
    assert result.bypass_refused_reason is not None


def test_bypass_with_fresh_armed_token_places_normally(repo):
    """A freshly armed token (well within ttl) lets bypass through -- still subject to every
    guard, but with no confirm prompt required, exactly like bypass behaved before Issue #60."""
    repo.disarm_bypass()
    repo.arm_bypass(now_ts=1_000, ttl_sec=100_000)  # armed_until = 101_000
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    result = run_once(broker, repo, _config(), now_ts=90_000)  # inside the armed window

    assert result.mode == "bypass"
    assert result.bypass_refused_reason is None
    assert result.enter_results[0].placed is True
    assert len(broker.place_calls) == 1
    assert repo.get_orders(mode="live", product_id=PRODUCT)[0]["side"] == "BUY"


def test_confirm_mode_unaffected_by_arming_state(repo):
    """`mode="confirm"` never even looks at the arm token -- arm-check only gates bypass."""
    repo.disarm_bypass()
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})
    config = _config(auto_trade=AutoTradeConfig(mode="confirm", interval_sec=50_000))

    result = run_once(broker, repo, config, now_ts=90_000)

    assert result.mode == "confirm"
    assert result.bypass_refused_reason is None  # bypass was never requested
    assert result.enter_results[0].placed is False  # confirm_fn=None -> fails closed, as always
    assert broker.place_calls == []


# -- run_once: EXIT wiring on a held position ---------------------------------------------------


def test_held_position_whose_exit_fires_gets_an_exit_order(repo):
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
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


def test_build_rule_unknown_kind_raises():
    with pytest.raises(ValueError, match="dca"):
        _build_rule({"kind": "not_a_real_rule_dca", "params": {}})


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
