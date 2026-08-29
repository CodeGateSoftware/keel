"""Tests for the trade-scope record's policy -- `may_place_live_entry`, the predicate rail 20
calls before a live ENTRY.

Pure functions: no database, no config object, no rail. Every state in the module's docstring
table is a test here.
"""

from __future__ import annotations

import dataclasses

import pytest
from keel_core.trade_scope import READ_ONLY, TRADING, TradeScopeState, VenueTradeScope

NOW = 1_800_000_000


def _record(
    state: TradeScopeState = TradeScopeState.CONFIRMED,
    attested_scope: str | None = None,
    attested_ts: int | None = None,
    confirmed_ts: int | None = NOW,
    refuted_ts: int | None = None,
    refuted_reason: str | None = None,
) -> VenueTradeScope:
    return VenueTradeScope(
        venue="coinbase",
        state=state,
        attested_scope=attested_scope,
        attested_ts=attested_ts,
        confirmed_ts=confirmed_ts,
        refuted_ts=refuted_ts,
        refuted_reason=refuted_reason,
    )


def test_confirmed_may_place_a_live_entry() -> None:
    """The venue itself proved it by accepting a placement."""
    record = _record(state=TradeScopeState.CONFIRMED)
    assert record.may_place_live_entry() is True


def test_attested_trading_may_place_a_live_entry() -> None:
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=TRADING)
    assert record.may_place_live_entry() is True


def test_attested_read_only_may_not_place_a_live_entry() -> None:
    """A read-only credential looks identical to a working one at the .env level (#233) --
    that is the whole reason this record exists."""
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=READ_ONLY)
    assert record.may_place_live_entry() is False


def test_refuted_may_not_place_a_live_entry() -> None:
    record = _record(state=TradeScopeState.REFUTED, refuted_ts=NOW)
    assert record.may_place_live_entry() is False


def test_unverified_may_not_place_a_live_entry() -> None:
    record = _record(state=TradeScopeState.UNVERIFIED, attested_scope=None)
    assert record.may_place_live_entry() is False


def test_reattesting_trading_over_a_refuted_record_restores_permission() -> None:
    """Re-attestation after rotating a credential moves `state` back to `ATTESTED`, but the old
    `refuted_ts` is kept as history for `doctor` to surface -- it must not veto the new
    attestation, or a fixed credential could never trade again because of the credential it
    replaced."""
    record = _record(
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        refuted_ts=NOW - 86_400,
        refuted_reason="insufficient permissions",
    )
    assert record.may_place_live_entry() is True


def test_attested_read_only_is_refused_even_with_no_refutation_history() -> None:
    """Confirms the read-only arm is keyed on `attested_scope`, not merely "not refuted"."""
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=READ_ONLY, refuted_ts=None)
    assert record.may_place_live_entry() is False


@pytest.mark.parametrize("scope", [None, "", "TRADING", "read-only", "trading "])
def test_attested_with_anything_other_than_the_exact_trading_literal_is_refused(
    scope: str | None,
) -> None:
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=scope)
    assert record.may_place_live_entry() is False


def test_the_record_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _record().state = TradeScopeState.REFUTED  # type: ignore[misc]
