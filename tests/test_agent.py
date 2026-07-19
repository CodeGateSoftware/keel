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
from keel.strategy.rules.dca import Dca
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

    def cancel_order(self, order_id: str) -> bool:
        return True        # a CONFIRMED cancel -- see `_cancel_at_exchange`


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

    # the loop records which rule owns the freshly opened position, for future exit checks,
    # plus the entry context (opened_at/entry_fill/qty) a `trade_outcomes` row will need.
    position = agent._position_state(repo, PRODUCT)
    assert position is not None
    assert position["rule_name"] == "dca"
    assert position["opened_at"] == 90_000
    assert position["entry_fill"] == Decimal("100")
    assert position["qty"] == Decimal("0.5")
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

    class _BrokenAccountsBroker(FakeBroker):
        def get_accounts(self) -> list[dict[str, Any]]:
            raise RuntimeError("broker down")

    series = {(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    broker = _BrokenAccountsBroker(series=series)

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_state("equity_high_water_mark") is None
    assert repo.get_state("drawdown_total_pct") is None


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

        def get_accounts(self) -> list[dict[str, Any]]:
            return [{"currency": "USDC", "available_balance": self.balance}]

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
        {"rule_name": "dca", "opened_at": 1_000, "entry_fill": Decimal("100"),
         "qty": Decimal("0.5")},
    )
    broker = FakeBroker()

    equity = agent._mark_to_market_equity(
        repo, broker, [PRODUCT], {PRODUCT: Decimal("100")}, "USDC"
    )

    # 2.5 BTC held, not 0.5 -- cash is FakeBroker's 1_000_000
    assert equity == Decimal("1000000") + Decimal("2.5") * Decimal("100")


def test_equity_counts_a_held_position_whose_rule_is_no_longer_live(repo: Repository) -> None:
    """`products` comes from the LIVE rule set. Retire a rule while its position is still open
    and the holding would vanish from equity in one step -- a cliff-edge phantom drawdown."""
    _seed_open_position(repo, PRODUCT, Decimal("2"), Decimal("100"), ts=1_000)
    broker = FakeBroker()

    equity = agent._mark_to_market_equity(
        repo, broker, [], {PRODUCT: Decimal("100")}, "USDC"
    )

    assert equity == Decimal("1000000") + Decimal("2") * Decimal("100")


def test_equity_falls_back_to_avg_cost_when_a_held_product_has_no_price(
    repo: Repository,
) -> None:
    """Pre-flight fix (C): a held product missing from the price map is valued at its cost
    basis, never dropped -- dropping it understates equity and would trip rail 11 on a DATA GAP
    rather than on a loss. This is the assertion that fix was missing."""
    _seed_open_position(repo, PRODUCT, Decimal("2"), Decimal("100"), ts=1_000)
    broker = FakeBroker()

    equity = agent._mark_to_market_equity(repo, broker, [PRODUCT], {}, "USDC")

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
    _seed_open_position(repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000)
    repo.set_state(
        f"position_rule:{PRODUCT}",
        {
            "rule_name": "fake_exit",
            "opened_at": 1_000,
            "entry_fill": Decimal("50000"),
            "qty": Decimal("0.1"),
        },
    )
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

    agent.RULE_REGISTRY["dca"] = lambda **kw: _ExitingDca()
    try:
        repo.insert_rule("dca", {}, status="live")
        _seed_open_position(repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000)
        repo.set_state(
            f"position_rule:{PRODUCT}",
            {"rule_name": "dca", "opened_at": 1_000,
             "entry_fill": Decimal("50000"), "qty": Decimal("0.1")},
        )
        broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

        run_once(broker, repo, _config(), now_ts=90_000)
    finally:
        agent.RULE_REGISTRY["dca"] = Dca

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["is_dca"] is True          # recorded as DCA...
    assert repo.get_state("consecutive_losses", default=0) == 0   # ...and exempt from the streak


def test_the_enter_path_records_the_entry_fee_onto_the_position(repo: Repository) -> None:
    """Holds the WIRING of the entry-fee half of the "pnl_net was GROSS" fix.

    `record_closed_trade` does `position.get("entry_fee") or Decimal("0")`, so if the ENTER path
    stops writing it, `pnl_net` silently reverts to net-of-EXIT-fee-only -- restoring the exact
    Critical this branch fixed, where a fee-dominated loser is recorded as a WIN and RESETS the
    loss counter.

    Every other test that touches `entry_fee` hand-seeds it into a `position_rule` fixture, which
    makes them vacuous with respect to the producer. This one exercises the real ENTER path and
    asserts the value ARRIVES -- the difference between testing arithmetic and testing wiring.
    """
    repo.insert_rule("dca", {"product_id": PRODUCT}, status="live")
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000)

    position = repo.get_state(f"position_rule:{PRODUCT}")
    assert position is not None, "no entry was placed -- the fixture no longer exercises ENTER"
    assert "entry_fee" in position
    assert position["entry_fee"] is not None, (
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
        dict(mode="live", product_id=PRODUCT, side=Side.SELL.value, order_type="market",
             qty=Decimal("0.01"), limit_price=None, status="pending", fee=None,
             expected_fill=Decimal("49000"), actual_fill=None,
             raw_response='{"order_id": "cb-1"}', created_at=1_000, updated_at=1_000)
    )
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": "turtle_breakout", "opened_at": 1_000,
        "entry_fill": Decimal("50000"), "qty": Decimal("0.01"), "entry_fee": Decimal("3"),
    })

    class _ReconcilingBroker(FakeBroker):
        def get_order(self, order_id: str) -> dict[str, Any]:
            return {
                "order_id": order_id, "status": "FILLED", "filled_size": Decimal("0.01"),
                "average_filled_price": Decimal("48900"), "total_fees": Decimal("2.93"),
            }

    broker = _ReconcilingBroker(
        series={(PRODUCT, Granularity.ONE_DAY): [_candle(1_000 + i * 86_400) for i in range(30)]}
    )

    run_once(broker, repo, _config(), now_ts=1_000 + 29 * 86_400)

    assert repo.get_order(bracket_id)["status"] == "filled"
    assert len(repo.get_trade_outcomes()) == 1
    assert repo.get_state(f"position_rule:{PRODUCT}") is None


def test_a_rule_exit_clears_the_stop_and_target_state(repo: Repository) -> None:
    """`open_stop`/`open_target` describe a bracket that no longer exists once the position is
    exited. Left behind they poison the NEXT trade in that product: rail 9 (no stop widening)
    vetoes a legitimate entry whose stop sits below the previous, closed trade's stop, and
    `_handle_exits` builds its held setup from a dead trade's stop.

    They were cleared on the reconcile path only -- the rule-exit path cleared `position_rule`
    and left them.
    """
    repo.insert_rule("fake_exit", {"product_id": PRODUCT}, status="live")
    _seed_open_position(repo, PRODUCT, Decimal("0.1"), Decimal("50000"), ts=1_000)
    repo.set_state(f"position_rule:{PRODUCT}", {
        "rule_name": "fake_exit", "opened_at": 1_000,
        "entry_fill": Decimal("50000"), "qty": Decimal("0.1"), "entry_fee": Decimal("3"),
    })
    repo.set_state(f"open_stop:{PRODUCT}", Decimal("49000"))
    repo.set_state(f"open_target:{PRODUCT}", Decimal("53000"))
    broker = FakeBroker(series={(PRODUCT, Granularity.ONE_DAY): [_candle(0, "100")]})

    run_once(broker, repo, _config(), now_ts=90_000)

    assert repo.get_state(f"position_rule:{PRODUCT}") is None
    assert repo.get_state(f"open_stop:{PRODUCT}") is None
    assert repo.get_state(f"open_target:{PRODUCT}") is None
