"""#799 proposal 2: a filled entry must never be lost to a later failure (plan R5).

The ENTRY has already filled when `place_bracket` runs, so nothing its bracket leg raises may
escape `executor.execute`. The stage decides the recovery: a throw BEFORE the bracket's `orders`
row exists (the preview) writes the `unbracketed:` retry record; a throw AFTER it exists
(`place_order`) leaves the `pending` row for a human and writes no retry, because the venue may be
holding the bracket.
"""

from __future__ import annotations

import logging
from decimal import InvalidOperation

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
