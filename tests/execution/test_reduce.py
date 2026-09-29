"""`executor.reduce` in preview (#857, plan P7 Task 7.2; R17, S1, S3, Review Focus 4).

The rails decide, the venue quotes, and nothing is placed: `reduce` takes a sleeve `Reduction`
as far as `broker.preview_order` and records a `sell_proposals` row. It never cancels a resting
bracket, never writes an `orders` row, never places, and never raises for a venue failure --
a cycle must not die on a proposal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from keel_broker_api.orders import OrderSpec
from keel_broker_api.port import TradeScopeDenied
from keel_broker_api.results import Instrument, Preview
from keel_core.trade_scope import TradeScopeState

from keel.config import Caps
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import executor, guards, sleeve
from keel.execution.guards import LIVE_STATE_RAILS, OrderIntent, rail_name
from keel.strategy.reduction import Holding, Reduction, SellCosts
from keel.types import Side
from tests.conftest import attest_cash_posture, attest_trade_scope
from tests.execution.test_executor import (
    NOW_TS,
    FakeBroker,
    _attest,
    _config,
    _PreviewRefusingBroker,
)

D = Decimal
COSTS = SellCosts(D("0.012"), D("0.0005"), sleeve.FALLBACK_FEE_SOURCE)


@pytest.fixture
def repo() -> Repository:
    """`test_executor`'s live-ready book: every fail-closed rail (12, 14, 17, 20, 22) is satisfied,
    so a test that is not about one of them is not incidentally vetoed by it."""
    r = Repository(connect(":memory:"))
    migrate(r._conn)  # noqa: SLF001
    r.set_state("kill_switch", False)
    r.set_state("last_feed_ts", NOW_TS)
    r.set_state("withdrawals_enabled", True)
    r.set_state("withdrawals_attested_at", NOW_TS)
    _attest(r, free_volume_usd=D("10000000"))
    attest_trade_scope(r, now_ts=NOW_TS)
    attest_cash_posture(r, now_ts=NOW_TS)
    return r


class SpyBroker(FakeBroker):
    """`FakeBroker`, recording every method `reduce` touches by name.

    Placement and cancellation also RAISE, so a reach that some `except` swallowed still shows
    in `calls` -- the list, not the exception, is what the tests assert on."""

    def __init__(self, *, increment: Decimal | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.calls: list[str] = []
        self._increment = increment

    def get_balances(self):  # type: ignore[no-untyped-def]
        self.calls.append("get_balances")
        return super().get_balances()

    def get_instrument(self, product_id: str) -> Instrument | None:
        self.calls.append("get_instrument")
        if self._increment is None:
            return None
        return Instrument(product_id=product_id, base_increment=self._increment)

    def preview_order(self, spec: OrderSpec) -> Preview:
        self.calls.append("preview_order")
        return super().preview_order(spec)

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> Any:
        self.calls.append("place_order")
        raise AssertionError("S1: reduce placed an order")

    def cancel_order(self, order_id: str) -> bool:
        self.calls.append("cancel_order")
        raise AssertionError("S1: reduce cancelled an order")


def _held(repo: Repository, product_id: str = "BTC-USD", qty: str = "0.002") -> Holding:
    repo.open_position(
        product_id=product_id,
        rule_name="dca",
        opened_at=0,
        qty=D(qty),
        entry_fill=D("100000"),
        entry_fee=D("0.6"),
    )
    return sleeve.holding_of(repo, product_id)


def _red(product_id: str = "BTC-USD", qty: str = "0.001", price: str = "110000") -> Reduction:
    return Reduction(product_id, D(qty), "reverse_dca", {"cadence_day": 1}, D(price), NOW_TS)


def _run(
    repo: Repository,
    broker: Any,
    *,
    config: Any = None,
    holding: Holding | None = None,
    offline: bool = False,
    **red: str,
) -> executor.ReduceResult:
    reduction = _red(**red)
    return executor.reduce(
        reduction,
        broker=broker,
        repo=repo,
        config=config or _config(),
        holding=holding if holding is not None else _held(repo, reduction.product_id),
        costs=COSTS,
        rule_id=7,
        rule_status="live",
        now_ts=NOW_TS,
        offline=offline,
    )


def _proposal(repo: Repository, result: executor.ReduceResult) -> dict[str, Any]:
    assert result.proposal_id is not None
    row = repo.get_sell_proposal(result.proposal_id)
    assert row is not None
    return row


def _seed_resting_bracket(repo: Repository) -> int:
    """A resting protective SELL: `_clear_resting_bracket` WOULD cancel this one."""
    return repo.insert_order(
        dict(
            mode="live",
            product_id="BTC-USD",
            side="SELL",
            order_type="bracket",
            qty=D("0.002"),
            status="pending",
            raw_response='{"order_id": "b-1"}',
            confirmation="autonomous",
            created_at=1,
            updated_at=1,
        )
    )


# --- the happy path -------------------------------------------------------------------------


def test_preview_quotes_at_the_venue_records_and_places_nothing(repo: Repository) -> None:
    bracket = _seed_resting_bracket(repo)
    broker = SpyBroker()

    result = _run(repo, broker)

    assert result == executor.ReduceResult(
        product_id="BTC-USD",
        rule_kind="reverse_dca",
        proposal_id=result.proposal_id,
        decision="preview",
        vetoed_by=[],
        legs=1,
        reason="preview only: nothing placed",
        total_qty=D("0.001"),
    )
    assert broker.calls.count("preview_order") == 1
    assert "place_order" not in broker.calls and "cancel_order" not in broker.calls
    row = _proposal(repo, result)
    assert (row["fee_source"], row["expected_fee"]) == (sleeve.VENUE_FEE_SOURCE, D("0.30"))
    assert (row["decision"], row["qty"], row["rule_status"], row["rule_id"]) == (
        "preview",
        D("0.001"),
        "live",
        7,
    )
    assert row["rails"]["violations"] == [] and row["rails"]["preview_error"] is None
    # The resting bracket is untouched, and no order row was written.
    assert [(o["id"], o["status"]) for o in repo.get_orders()] == [(bracket, "pending")]


def test_the_previewed_spec_is_a_market_sell_of_the_leg(repo: Repository) -> None:
    broker = SpyBroker(increment=D("0.00000001"))
    _run(repo, broker)
    (call,) = broker.preview_calls
    spec = call["spec"]
    assert (spec.product_id, spec.side, spec.base_size) == ("BTC-USD", Side.SELL, D("0.001"))


# --- S1: zero placement, cancel or order-row calls, for every decision -------------------------


def _kill(repo: Repository) -> None:
    repo.set_state("kill_switch", True)


_OUTCOMES: dict[str, dict[str, Any]] = {
    "preview-venue-fee": dict(decision="preview"),
    "preview-that-raised": dict(decision="preview", broker="timeout"),
    "vetoed-by-a-rail": dict(decision="vetoed", setup=_kill),
    "vetoed-below-one-increment": dict(decision="vetoed", increment=D("0.01")),
    "vetoed-nothing-held": dict(decision="vetoed", empty=True),
    "offline-preview": dict(decision="preview", offline=True),
}


@pytest.mark.parametrize("outcome", list(_OUTCOMES), ids=list(_OUTCOMES))
def test_reduce_never_places_cancels_or_writes_an_order_for_any_decision(
    repo: Repository,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """S1 with spies on every writer a SELL path would reach: the broker's place and cancel, the
    repository's order writers, and the executor's own placement and cancel helpers. Each
    outcome also asserts its decision, so the spy is proven to have been in the path."""
    case = _OUTCOMES[outcome]
    _seed_resting_bracket(repo)
    reached: list[str] = []

    def _spy(name: str):  # type: ignore[no-untyped-def]
        def _record(*a: Any, **kw: Any) -> Any:
            reached.append(name)
            raise AssertionError(f"S1: reduce reached {name}")

        return _record

    for name in ("insert_order", "update_order"):
        monkeypatch.setattr(repo, name, _spy(f"repo.{name}"))
    for name in ("_run_order", "_clear_resting_bracket", "_cancel_at_exchange", "place_bracket"):
        monkeypatch.setattr(executor, name, _spy(f"executor.{name}"))
    if "setup" in case:
        case["setup"](repo)
    broker: Any = (
        _PreviewRefusingBroker(TimeoutError("read timed out"))
        if case.get("broker") == "timeout"
        else SpyBroker(increment=case.get("increment"))
    )
    holding = Holding("BTC-USD", ()) if case.get("empty") else None

    result = _run(repo, broker, holding=holding, offline=case.get("offline", False))

    assert result.decision == case["decision"]
    assert _proposal(repo, result)["decision"] == case["decision"]
    assert reached == []
    assert broker.place_calls == [] and broker.cancel_calls == []


# --- S3: which rails bite a sell --------------------------------------------------------------


def _foreign_allowlist(repo: Repository) -> dict[str, Any]:
    return dict(product_id="SOL-USD")


def _stale_feed(repo: Repository) -> dict[str, Any]:
    repo.set_state("last_feed_ts", NOW_TS - 10 * 86_400)
    return {}


def _kill_switch(repo: Repository) -> dict[str, Any]:
    repo.set_state("kill_switch", True)
    return {}


def _eur_settled(repo: Repository) -> dict[str, Any]:
    return dict(product_id="BTC-EUR")


def _perp_shaped(repo: Repository) -> dict[str, Any]:
    return dict(product_id="BTC-PERP-USD")


_BITING_RAILS = {
    "1-halal_allowlist": (_foreign_allowlist, "halal_allowlist"),
    "12-kill_switch": (_kill_switch, "kill_switch"),
    "12-stale_data": (_stale_feed, "stale_data"),
    "18-settlement_currency": (_eur_settled, "settlement_currency"),
    "19-spot_instrument": (_perp_shaped, "spot_instrument"),
}


@pytest.mark.parametrize("case", list(_BITING_RAILS), ids=list(_BITING_RAILS))
def test_a_rail_that_applies_to_a_sell_vetoes_the_reduction_before_the_venue_quotes(
    repo: Repository,
    case: str,
) -> None:
    """S3, the half that bites (spec §3.4). Each veto is recorded as a proposal whose
    `rails.violations` is the result's `vetoed_by`, and the venue is never asked to quote."""
    setup, rail = _BITING_RAILS[case]
    red = setup(repo)
    broker = SpyBroker()

    result = _run(repo, broker, **red)

    assert result.decision == "vetoed"
    assert rail in [rail_name(v) for v in result.vetoed_by]
    row = _proposal(repo, result)
    assert row["decision"] == "vetoed"
    assert row["rails"]["violations"] == result.vetoed_by
    assert "preview_order" not in broker.calls


def test_rail_21_vetoes_when_the_venue_affirms_a_zero_holding(repo: Repository) -> None:
    broker = SpyBroker(balances={"USD": D("1"), "USDC": D("1"), "BTC": D("0")})
    result = _run(repo, broker)
    assert result.decision == "vetoed"
    assert [rail_name(v) for v in result.vetoed_by] == ["base_balance"]
    assert "preview_order" not in broker.calls


def test_rail_2_is_sliced_not_vetoed_and_the_legs_are_recorded(repo: Repository) -> None:
    """Rail 2 is a slicing obligation (R11): $165 at a $50 cap is 4 legs of at most $50. The
    same sale as ONE order is exactly what rail 2 vetoes -- the control proves the cap bites."""
    config = _config(
        caps=Caps(
            max_exposure_usd=D("1000000"),
            max_per_asset_pct=D("1"),
            max_per_order_usd=D("50"),
            max_per_day_usd=D("300000"),
        )
    )
    unsliced = OrderIntent(
        product_id="BTC-USD",
        side=Side.SELL,
        qty=D("0.0015"),
        entry=D("110000"),
        stop=None,
        notional=D("165"),
        is_dca=False,
        rule_kind="reverse_dca",
    )
    control = guards.check(unsliced, repo, config, NOW_TS)
    assert [rail_name(v) for v in control.violations] == ["per_order_cap"]

    result = _run(repo, SpyBroker(), config=config, qty="0.0015")

    assert (result.decision, result.legs) == ("preview", 4)
    row = _proposal(repo, result)
    assert row["legs"] == 4
    assert row["qty"] * D("110000") <= D("50")


def test_rails_9_and_10_are_satisfied_by_construction(
    repo: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rail 9 reads `stop`/`protective_stop`, and a reduction carries neither, so a recorded
    `open_stop` far above the price cannot veto it (rail 9 applies only to a resting protective
    variant, which P7 does not build). Rail 10 reads `rule_kind`, which is the reduction's
    `reason` -- never empty, `Reduction` refuses one."""
    captured: list[OrderIntent] = []
    real_check = guards.check

    def _capture(intent: OrderIntent, *a: Any, **kw: Any) -> guards.GuardResult:
        captured.append(intent)
        return real_check(intent, *a, **kw)

    monkeypatch.setattr(guards, "check", _capture)
    repo.set_state("open_stop:BTC-USD", D("200000"))

    result = _run(repo, SpyBroker())

    assert result.decision == "preview"
    (intent,) = captured
    assert (intent.stop, intent.protective_stop) == (None, None)
    assert (intent.side, intent.rule_kind, intent.rule_id) == (Side.SELL, "reverse_dca", 7)
    assert intent.is_dca is False


def test_buy_scoped_rails_do_not_veto_a_sell(
    repo: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3, the half that does NOT bite. Every buy-only rail -- 3, 4, 5, 6, 8, 11, 13, 14, 16, 17,
    20 and 22 -- is armed to veto, and a control BUY on the same book proves each one fires.
    The reduction then previews: `guards.py` gates every one of them on `is_buy`.

    Rails 13 and 17 read `available_quote`/`withdrawals_enabled`, and both fail CLOSED on
    `None`. `reduce`'s intent carries `None` for both, so if either rail applied to a sell it
    would veto here -- which is what makes the assertion on the captured intent load-bearing."""
    config = _config(
        caps=Caps(
            max_exposure_usd=D("1"),  # rails 4 and 6
            max_per_asset_pct=D("1"),
            max_per_order_usd=D("100000"),
            max_per_day_usd=D("1"),  # rail 3
        )
    )
    # Rail 8: an existing BTC position bought above the price the control BUY enters at.
    # Rail 5: ETH exposure is open, and the control BUY is over half the per-order cap.
    for product, qty, price in (("BTC-USD", "0.002", "120000"), ("ETH-USD", "1", "4000")):
        repo.insert_order(
            dict(
                mode="live",
                product_id=product,
                side="BUY",
                order_type="market",
                qty=D(qty),
                status="filled",
                expected_fill=D(price),
                actual_fill=D(price),
                created_at=1,
                updated_at=1,
            )
        )
    repo.set_state("drawdown_total_pct", D("99"))  # rail 11
    repo.set_state("streak_halt_until", NOW_TS + 86_400)  # rail 16
    repo._conn.execute("DELETE FROM broker_subscriptions")  # rail 14  # noqa: SLF001
    repo._conn.execute("DELETE FROM venue_trade_scopes")  # rail 20  # noqa: SLF001
    repo._conn.execute("DELETE FROM venue_cash_postures")  # rail 22  # noqa: SLF001

    control_buy = OrderIntent(
        product_id="BTC-USD",
        side=Side.BUY,
        qty=D("0.6"),
        entry=D("110000"),
        stop=None,
        notional=D("66000"),
        is_dca=False,
        rule_kind="turtle_breakout",
    )
    fired = {rail_name(v) for v in guards.check(control_buy, repo, config, NOW_TS).violations}
    assert fired == {
        "per_day_cap",  # 3
        "total_exposure_cap",  # 4
        "correlation_adjusted_sizing",  # 5
        "per_asset_concentration_cap",  # 6
        "no_averaging_into_losers",  # 8
        "account_dd_breaker_total",  # 11
        "usdc_funding",  # 13
        "subscription_unattested",  # 14
        "consecutive_loss_breaker",  # 16
        "withdrawal_capability",  # 17
        "trade_scope",  # 20
        "cash_posture",  # 22
    }

    captured: list[OrderIntent] = []
    real_check = guards.check

    def _capture(intent: OrderIntent, *a: Any, **kw: Any) -> guards.GuardResult:
        captured.append(intent)
        return real_check(intent, *a, **kw)

    monkeypatch.setattr(guards, "check", _capture)

    result = _run(repo, SpyBroker(), config=config)

    assert (result.decision, result.vetoed_by) == ("preview", []), "spec §3.4: buy-only rails"
    (intent,) = captured
    assert (intent.available_quote, intent.withdrawals_enabled) == (None, None)


# --- Review Focus 4: a preview that raises never kills the cycle --------------------------------


@pytest.mark.parametrize(
    "exc",
    [TradeScopeDenied("403 read-only"), TimeoutError("read timed out"), RuntimeError("")],
    ids=["trade-scope-denied", "timeout", "empty-message"],
)
def test_a_preview_that_raises_records_the_proposal_on_the_fallback_fee(
    repo: Repository,
    exc: Exception,
) -> None:
    result = _run(repo, _PreviewRefusingBroker(exc))

    row = _proposal(repo, result)
    assert result.decision == row["decision"] == "preview"
    assert row["fee_source"] == sleeve.FALLBACK_FEE_SOURCE
    assert row["expected_fee"] == D("0.001") * D("110000") * D("0.012")
    assert row["rails"]["preview_error"] == repr(exc)


def test_a_denied_preview_records_the_venues_refusal_as_every_preview_does(
    repo: Repository,
) -> None:
    """#233: a preview is a trade-scoped venue call, and `_run_order` records a denied one. So
    does `reduce` -- the venue has falsified the attestation, and rail 20 then vetoes the next
    BUY cleanly instead of that BUY's own preview raising out of the cycle."""
    _run(repo, _PreviewRefusingBroker(TradeScopeDenied("403 read-only")))
    scope = repo.get_venue_trade_scope("coinbase")
    assert scope is not None and scope.state is TradeScopeState.REFUTED


def test_a_preview_that_times_out_leaves_the_next_buy_unblocked(repo: Repository) -> None:
    """Review Focus 4: a failed proposal must not block the DCA buy after it in the cycle. A
    network failure says nothing about the credential, so no rail's input is touched."""
    before = repo.get_venue_trade_scope("coinbase")
    _run(repo, _PreviewRefusingBroker(TimeoutError("read timed out")))
    assert repo.get_venue_trade_scope("coinbase") == before
    dca_buy = OrderIntent(
        product_id="BTC-USD",
        side=Side.BUY,
        qty=D("0.0005"),
        entry=D("100000"),
        stop=None,
        notional=D("50"),
        is_dca=True,
        rule_kind="dca",
        available_quote=D("1000"),
        withdrawals_enabled=True,
    )
    assert guards.check(dca_buy, repo, _config(), NOW_TS).violations == []


@pytest.mark.parametrize(
    "preview,why",
    [
        (
            {"commission_total": "0", "best_bid": "1", "best_ask": "1"},
            "venue preview carried no commission",
        ),
        (
            {"commission_total": "0.30", "errs": ["INSUFFICIENT_FUND"], "best_bid": "1"},
            "venue preview reported errors: INSUFFICIENT_FUND",
        ),
    ],
    ids=["no-commission", "preview-errors"],
)
def test_a_preview_without_a_usable_fee_falls_back_and_says_why(
    repo: Repository,
    preview: dict[str, Any],
    why: str,
) -> None:
    """Review Focus 4's "empty field": the venue answered, but its fee is not a fact to record."""
    result = _run(repo, SpyBroker(preview=preview))
    row = _proposal(repo, result)
    assert result.decision == "preview"
    assert row["fee_source"] == sleeve.FALLBACK_FEE_SOURCE
    assert row["expected_fee"] == D("0.001") * D("110000") * D("0.012")
    assert (row["rails"]["preview_error"], row["rails"]["fee_fallback_reason"]) == (None, why)


def test_a_synthetic_preview_is_not_recorded_as_the_venues_fee(repo: Repository) -> None:
    """`Preview.synthetic` means the adapter estimated it; only a venue quote is `venue_preview`."""

    class _Synthetic(SpyBroker):
        def preview_order(self, spec: OrderSpec) -> Preview:
            self.calls.append("preview_order")
            return Preview(spec.product_id, Side.SELL, D("0.001"), D("110"), D("0.5"), True)

    row = _proposal(repo, _run(repo, _Synthetic()))
    assert row["fee_source"] == sleeve.FALLBACK_FEE_SOURCE
    assert row["rails"]["fee_fallback_reason"] == "preview is a synthetic estimate"


# --- offline (paper, R18) ------------------------------------------------------------------------


def test_offline_touches_no_broker_and_says_what_it_skipped(repo: Repository) -> None:
    broker = SpyBroker()
    result = _run(repo, broker, offline=True)
    row = _proposal(repo, result)
    assert result.decision == "preview"
    assert broker.calls == [] and broker.get_balances_calls == 0
    assert row["rails"]["skipped"] == list(LIVE_STATE_RAILS)
    assert row["fee_source"] == sleeve.FALLBACK_FEE_SOURCE


def test_a_live_proposal_skips_no_rail(repo: Repository) -> None:
    assert _proposal(repo, _run(repo, SpyBroker()))["rails"]["skipped"] == []


def test_no_broker_and_not_offline_says_the_venue_was_never_asked(repo: Repository) -> None:
    """`live = not offline and broker is not None`: `broker=None` with `offline=False` runs every
    rail (nothing is skipped, unlike the offline path) but has no broker to preview against, so
    it records the fallback fee. Without a `fee_fallback_reason` the row would read exactly like
    a venue preview that happened to fall back -- `preview_error` None, `skipped` empty -- when
    in fact the venue was never asked at all."""
    result = _run(repo, None)
    row = _proposal(repo, result)
    assert result.decision == "preview"
    assert row["fee_source"] == sleeve.FALLBACK_FEE_SOURCE
    assert row["rails"]["fee_fallback_reason"] == "no broker: the venue was not asked for a quote"
    assert row["rails"]["preview_error"] is None
    assert row["rails"]["skipped"] == []


# --- sizing: never more than is held (P7 carried item c) ------------------------------------------


def test_a_reduction_larger_than_the_holding_is_sized_to_the_holding(repo: Repository) -> None:
    result = _run(repo, SpyBroker(), qty="0.005")
    row = _proposal(repo, result)
    assert (row["qty"], result.legs) == (D("0.002"), 1)


def test_the_sale_is_clamped_to_what_the_venue_holds(repo: Repository) -> None:
    """#900: the ledger can overstate the venue by about the fee; the venue's holding wins, and
    the legs are counted over what can actually be sold."""
    broker = SpyBroker(balances={"USD": D("1"), "BTC": D("0.00198")})
    result = _run(repo, broker, qty="0.002")
    row = _proposal(repo, result)
    assert (row["qty"], result.legs, result.decision) == (D("0.00198"), 1, "preview")


def test_a_holding_resting_on_unsized_fills_is_disclosed_on_the_proposal(
    repo: Repository,
) -> None:
    """#900/#912: `positions` cannot name its entry order, so the proposal names the product's
    filled BUYs the venue never sized that could have booked the CURRENTLY OPEN lot -- `_held`
    (called by `_run` below) opens a 0.002 BTC lot at `opened_at=0`, so the fixture's unsized
    order matches it on both size and clock. The sized fill IS a candidate owner too, but its
    booked size (its `filled_quantity`, 0.00199) does not match the 0.002 lot, so it owns
    nothing here -- and a sized owner would not be listed anyway: only unsized owners are."""
    fill = dict(
        mode="live",
        product_id="BTC-USD",
        side="BUY",
        order_type="market",
        qty=D("0.002"),
        status="filled",
        created_at=0,
        updated_at=0,
    )
    unsized = repo.insert_order(fill)
    sized = repo.insert_order(fill | {"filled_quantity": D("0.00199")})

    row = _proposal(repo, _run(repo, SpyBroker()))

    assert row["rails"]["unverified_fill_orders"] == [unsized]
    assert sized not in row["rails"]["unverified_fill_orders"]


def test_a_holding_on_sized_fills_discloses_none(repo: Repository) -> None:
    assert _proposal(repo, _run(repo, SpyBroker()))["rails"]["unverified_fill_orders"] == []


def test_nothing_held_is_recorded_vetoed_and_the_venue_is_never_asked(
    repo: Repository,
) -> None:
    broker = SpyBroker()
    result = _run(repo, broker, holding=Holding("BTC-USD", ()))
    row = _proposal(repo, result)
    assert (result.decision, result.legs, row["rails"]["sleeve"]) == ("vetoed", 0, "nothing_held")
    assert broker.calls == []
    # A `Reduction` refuses qty <= 0, so `nothing_held` cannot record the (nonexistent) capped
    # sale; it records the reduction's own qty instead. Its net is NULL regardless -- there is no
    # lot to consume -- which is what this pins, not the qty column.
    assert row["expected_net_pnl"] is None


def test_a_leg_below_one_increment_is_recorded_vetoed_and_never_quoted(
    repo: Repository,
) -> None:
    broker = SpyBroker(increment=D("0.01"))
    result = _run(repo, broker)
    row = _proposal(repo, result)
    assert (result.decision, result.legs) == ("vetoed", 0)
    assert (row["legs"], row["rails"]["sleeve"]) == (0, "below_one_increment")
    assert "preview_order" not in broker.calls


def test_a_leg_below_one_increment_records_the_capped_sale_not_the_uncapped_ask(
    repo: Repository,
) -> None:
    """#911: a `Reduction` larger than the holding must not record qty/fee/net for units not
    held. Ledger holds 0.002 BTC; the reduction asks for 0.005; the increment (0.01) makes even
    the held 0.002 inexpressible. The row must show 0.002, priced on 0.002 -- not the uncapped
    0.005 the rule asked for."""
    broker = SpyBroker(increment=D("0.01"))
    result = _run(repo, broker, qty="0.005")
    row = _proposal(repo, result)
    assert (result.decision, result.legs) == ("vetoed", 0)
    assert row["rails"]["sleeve"] == "below_one_increment"
    assert row["qty"] == D("0.002")
    assert row["expected_fee"] == D("0.002") * D("110000") * D("0.012")


# -- the sale's total (P8, carried over from P7's held question) --------------------------------


def _sliced_config() -> Any:
    return _config(
        caps=Caps(
            max_exposure_usd=D("1000000"),
            max_per_asset_pct=D("1"),
            max_per_order_usd=D("50"),
            max_per_day_usd=D("300000"),
        )
    )


def test_a_sliced_sale_records_its_total_not_only_the_first_leg(repo: Repository) -> None:
    """Rail 2 slices $165 into 4 legs, and the row's `qty` is the FIRST leg. Without the total
    the proposal says "sell 0.00045, 4 legs" and nothing says the sale is 0.0015: the total is
    on the result and in the row's `rails.total_qty`."""
    result = _run(repo, SpyBroker(), config=_sliced_config(), qty="0.0015")

    row = _proposal(repo, result)
    assert result.legs == 4 and row["qty"] < D("0.0015"), "fixture: the sale must be sliced"
    assert result.total_qty == D("0.0015")
    assert D(row["rails"]["total_qty"]) == D("0.0015")


def test_the_recorded_total_is_the_capped_and_clamped_sale(repo: Repository) -> None:
    """The total is what would actually be sold -- capped at the ledger, clamped to the venue --
    never the rule's ask."""
    broker = SpyBroker(balances={"USD": D("1"), "BTC": D("0.00198")})

    result = _run(repo, broker, qty="0.005")

    assert result.total_qty == D("0.00198")
    assert D(_proposal(repo, result)["rails"]["total_qty"]) == D("0.00198")


def test_a_rails_veto_still_records_the_total(repo: Repository) -> None:
    _kill(repo)

    result = _run(repo, SpyBroker(), config=_sliced_config(), qty="0.0015")

    assert result.decision == "vetoed" and result.legs == 4
    assert result.total_qty == D("0.0015")
    assert D(_proposal(repo, result)["rails"]["total_qty"]) == D("0.0015")


# -- rail-2 parity: the sim and guards veto the same oversized distribution (spec §6, P11) ---------


def _flat_hourly_market(price: str, days: int) -> dict[str, dict[Any, list[Any]]]:
    from keel.types import Candle, Granularity

    p = D(price)

    def bar(ts: int) -> Candle:
        return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=D("10"))

    return {
        "BTC": {
            Granularity.ONE_HOUR: [bar(h * 3_600) for h in range(days * 24)],
            Granularity.ONE_DAY: [bar(d * 86_400) for d in range(days)],
        }
    }


def test_the_sim_and_guards_agree_on_an_oversized_distribution(repo: Repository) -> None:
    """Spec §6: a parity test. The account sim sells a $100 distribution under a $50 per-order
    cap; the leg it FILLED -- sliced by the one slicer live uses (`sleeve.slice_qty`), from the
    one config both read -- passes rail 2 in `guards.check`, and one increment more is vetoed
    by it. So the sim never books a leg the live rails would refuse."""
    from keel.config import SubscriptionConfig
    from keel.sim import portfolio_sim
    from keel.strategy.rules.dca import Dca
    from keel.strategy.rules.reverse_dca import ReverseDca

    cap = D("50")
    config = _config(
        caps=Caps(max_exposure_usd=D("1e6"), max_per_asset_pct=D("1"), max_per_order_usd=cap),
        subscription=SubscriptionConfig(assumed_free_volume_usd=D("1e6"), pacing="opportunistic"),
    )
    market = _flat_hourly_market("110000", 61)
    hourly = market["BTC"][next(iter(market["BTC"]))]
    result = portfolio_sim.run(
        [
            Dca("BTC-USD", cadence_days=7, budget_usd=cap),
            ReverseDca("BTC-USD", target_usd=D("100"), min_price_floor=D("1")),
        ],
        market,
        config,
        start_ts=hourly[0].ts,
        end_ts=hourly[-1].ts,
        monthly_contribution=D("100000"),
        fee_pct=config.fees.taker_pct,
        slippage_pct=D("0"),
    )
    [sale] = result.dca_sells
    assert sale.qty < D("100") / sale.expected_price, "fixture: the distribution must be sliced"

    def _veto(qty: Decimal) -> list[str]:
        intent = OrderIntent(
            product_id="BTC-USD",
            side=Side.SELL,
            qty=qty,
            entry=sale.expected_price,
            stop=None,
            notional=qty * sale.expected_price,
            is_dca=False,
            rule_kind="reverse_dca",
        )
        return guards.check(intent, repo, config, NOW_TS, offline=True).violations

    assert not any(v.startswith("per_order_cap") for v in _veto(sale.qty))
    assert any(v.startswith("per_order_cap") for v in _veto(sale.qty + D("0.00000001")))
