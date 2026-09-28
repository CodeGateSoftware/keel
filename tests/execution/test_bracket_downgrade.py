"""#799 proposal 2: a filled entry must never be lost to a later failure (plan R5).

The ENTRY has already filled when `place_bracket` runs, so nothing its bracket leg raises may
escape `executor.execute`. The stage decides the recovery: a throw BEFORE the bracket's `orders`
row exists (the preview) writes the `unbracketed:` retry record; a throw AFTER it exists
(`place_order`) leaves the `pending` row for a human and writes no retry, because the venue may be
holding the bracket.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

import pytest
from keel_broker_api.orders import OrderSpec
from keel_broker_api.port import TradeScopeDenied
from keel_broker_api.results import PlaceResult, Preview
from keel_core.telemetry import _FIELDS_ATTR

from keel.execution import executor
from keel.types import Side
from tests.execution.test_executor import (  # noqa: F401 -- `repo` is a fixture
    NOW_TS,
    FakeBroker,
    _config,
    _enter_signal,
    repo,
)


class _SellPreviewRaises(FakeBroker):
    """The August incident: the ENTRY previews and fills, the protective SELL's preview throws
    `decimal.InvalidOperation` out of a degenerate venue field."""

    def __init__(self) -> None:
        super().__init__()
        self.sell_previews = 0

    def preview_order(self, spec: OrderSpec) -> Preview:
        if spec.side is Side.SELL:
            self.sell_previews += 1
            raise InvalidOperation("[<class 'decimal.ConversionSyntax'>]")
        return super().preview_order(spec)


class _SellPlaceRaises(FakeBroker):
    """The other stage: the bracket previews, then `place_order` raises -- the venue may or may
    not have accepted it."""

    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc
        self.sell_places = 0

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        if spec.side is Side.SELL:
            self.sell_places += 1
            raise self._exc
        return super().place_order(spec, idempotency_key=idempotency_key)


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == event]


def test_a_bracket_preview_that_throws_after_a_filled_entry_downgrades(
    repo,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = _SellPreviewRaises()
    signal = _enter_signal()

    with caplog.at_level(logging.WARNING, logger="keel.execution.executor"):
        result = executor.execute(signal, broker, repo, _config(), "autonomous", now_ts=NOW_TS)

    assert broker.sell_previews == 1, "the bracket leg must actually have reached the preview"
    assert result.placed is True, "the entry FILLED -- reporting it unplaced loses the tranche"
    assert result.bracket_order_id is None
    assert [c["spec"].side for c in broker.place_calls] == [Side.BUY], "only the entry was placed"
    orders = repo.get_orders(mode="live", product_id="BTC-USD")
    assert [(o["side"], o["status"]) for o in orders] == [("BUY", "filled")], (
        "a preview-stage failure writes no bracket row"
    )
    retry = repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")
    assert retry is not None
    assert (retry["stop"], retry["target"]) == (signal.setup.stop, signal.setup.target)
    [warning] = _events(caplog, "executor.bracket_not_placed")
    assert warning.levelno == logging.WARNING
    assert getattr(warning, _FIELDS_ATTR)["product"] == "BTC-USD"
    assert _events(caplog, executor.BRACKET_STATE_UNKNOWN_EVENT) == []


@pytest.mark.parametrize(
    "exc",
    [TimeoutError("read timed out"), TradeScopeDenied("trade scope denied")],
    ids=["timeout", "trade_scope_denied"],
)
def test_a_bracket_place_that_throws_downgrades_without_a_retry(
    repo,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    exc: Exception,
) -> None:
    broker = _SellPlaceRaises(exc)

    with caplog.at_level(logging.WARNING, logger="keel.execution.executor"):
        result = executor.execute(
            _enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS
        )

    assert broker.sell_places == 1, "the bracket leg must actually have reached place_order"
    assert result.placed is True
    assert result.bracket_order_id is None
    assert repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD") is None, (
        "the venue may hold a resting bracket -- a retry would double-commit the base"
    )
    [pending_sell] = [
        o for o in repo.get_orders(mode="live", status="pending") if o["side"] == "SELL"
    ]
    [critical] = _events(caplog, executor.BRACKET_STATE_UNKNOWN_EVENT)
    assert critical.levelno == logging.CRITICAL
    fields = getattr(critical, _FIELDS_ATTR)
    assert (fields["product"], fields["order_id"]) == ("BTC-USD", pending_sell["id"])
    assert _events(caplog, "executor.bracket_not_placed") == []
    assert repo.get_state("open_stop:BTC-USD") is None, "no stop is asserted for an unknown row"


def test_a_bracket_that_places_still_returns_its_order_id(repo) -> None:  # noqa: F811
    """The control: the downgrade must not swallow the success path."""
    broker = FakeBroker()

    result = executor.execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

    [bracket] = [o for o in repo.get_orders(mode="live") if o["side"] == "SELL"]
    assert result.bracket_order_id == bracket["id"]
    assert repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD") is None


# -- the spec build: an `ArithmeticError` is a bracket that cannot be BUILT (#799 follow-up) ----


def _cache_increment(repo, increment: str) -> None:  # noqa: F811
    """A FRESH cached `base_increment:` record, so the bracket leg reads it without asking the
    venue. `quote_increment` is present so `_price_increment_for` does not refetch over it."""
    repo.set_state(
        f"{executor.BASE_INCREMENT_PREFIX}BTC-USD",
        {"increment": increment, "quote_increment": "0.01", "fetched_at": NOW_TS},
    )


def test_a_non_finite_cached_increment_still_brackets_a_filled_entry(repo) -> None:  # noqa: F811
    """`Infinity` is a degenerate venue field, and degenerate means UNKNOWN: the bracket goes
    out unquantized, as it does for any unknown increment, rather than raising out of
    `place_bracket` after the entry filled."""
    _cache_increment(repo, "Infinity")
    broker = FakeBroker()

    result = executor.execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

    assert result.placed is True, "the entry FILLED -- reporting it unplaced loses the tranche"
    assert [c["spec"].side for c in broker.place_calls] == [Side.BUY, Side.SELL]
    [bracket] = [o for o in repo.get_orders(mode="live") if o["side"] == "SELL"]
    assert result.bracket_order_id == bracket["id"]


def test_an_arithmetic_error_building_the_bracket_downgrades_a_filled_entry(
    repo,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A finite, positive increment can still break the quantize: one far finer than the
    context's 28-digit precision raises `InvalidOperation` (`DivisionImpossible`) from
    `quantize_down`. That is an `ArithmeticError`, not a `ValueError`, and it is the same event
    as a bracket the venue refuses: the entry keeps its tranche, and the retry record holds the
    levels. Narrowing the spec-build `except` back to `ValueError` fails this test."""
    _cache_increment(repo, "1E-40")
    broker = FakeBroker()
    signal = _enter_signal()

    with caplog.at_level(logging.WARNING, logger="keel.execution.executor"):
        result = executor.execute(signal, broker, repo, _config(), "autonomous", now_ts=NOW_TS)

    assert result.placed is True, "the entry FILLED -- reporting it unplaced loses the tranche"
    assert result.bracket_order_id is None
    assert [c["spec"].side for c in broker.place_calls] == [Side.BUY], "no bracket was sent"
    retry = repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")
    assert retry is not None
    assert (retry["stop"], retry["target"]) == (signal.setup.stop, signal.setup.target)
    [warning] = _events(caplog, "executor.bracket_not_placed")
    assert getattr(warning, _FIELDS_ATTR)["product"] == "BTC-USD"


def test_an_arithmetic_error_building_a_roll_replacement_is_escalated_not_raised(
    repo,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`_roll_stop` has the same spec-build `try`, and there the old bracket is ALREADY
    cancelled: an exception escaping it skips the CRITICAL that says the position is naked."""
    broker = FakeBroker()
    old_id = executor.place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("54000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )
    assert old_id is not None
    _cache_increment(repo, "1E-40")

    with caplog.at_level(logging.WARNING, logger="keel.execution.executor"):
        new_id = executor.roll_stop_to(
            broker,
            repo,
            _config(),
            product_id="BTC-USD",
            old_stop_order_id=old_id,
            new_stop=Decimal("50000"),
            qty=Decimal("0.01"),
            rule_name="pullback_continuation",
            now_ts=NOW_TS + 100,
        )

    assert new_id is None
    assert broker.events == ["place", "cancel"], "cancelled, and no replacement was sent"
    [critical] = _events(caplog, "executor.position_unprotected")
    assert critical.levelno == logging.CRITICAL
    assert getattr(critical, _FIELDS_ATTR)["cancelled_order_id"] == old_id
