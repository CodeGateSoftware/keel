"""Tests for the trade-scope record's policy -- `may_place_live_entry`, the predicate rail 20
calls before a live ENTRY, and `credential_evidence` (#633), the fingerprint check it now runs
first.

Pure functions: no database, no config object, no rail. Every state in the module's docstring
table is a test here, and the full state x fingerprint matrix is covered in the section below.
"""

from __future__ import annotations

import dataclasses

import pytest
from keel_core.trade_scope import (
    READ_ONLY,
    TRADING,
    CredentialEvidence,
    TradeScopeState,
    VenueTradeScope,
)

NOW = 1_800_000_000

FP_A = "a" * 32
FP_B = "b" * 32


def _record(
    state: TradeScopeState = TradeScopeState.CONFIRMED,
    attested_scope: str | None = None,
    attested_ts: int | None = None,
    confirmed_ts: int | None = NOW,
    refuted_ts: int | None = None,
    refuted_reason: str | None = None,
    credential_fingerprint: str | None = None,
) -> VenueTradeScope:
    return VenueTradeScope(
        venue="coinbase",
        state=state,
        attested_scope=attested_scope,
        attested_ts=attested_ts,
        confirmed_ts=confirmed_ts,
        refuted_ts=refuted_ts,
        refuted_reason=refuted_reason,
        credential_fingerprint=credential_fingerprint,
    )


# -- pre-#633 behaviour, unchanged when the fingerprint is out of the picture --------------------
# Every call below passes `current_fingerprint=None` and every record here has
# `credential_fingerprint=None` -- both "unknown", so `credential_evidence` is UNFINGERPRINTED and
# the state machine below is reached exactly as it was before #633.


def test_confirmed_may_place_a_live_entry() -> None:
    """The venue itself proved it by accepting a placement."""
    record = _record(state=TradeScopeState.CONFIRMED)
    assert record.may_place_live_entry(None) is True


def test_attested_trading_may_place_a_live_entry() -> None:
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=TRADING)
    assert record.may_place_live_entry(None) is True


def test_attested_read_only_may_not_place_a_live_entry() -> None:
    """A read-only credential looks identical to a working one at the .env level (#233) --
    that is the whole reason this record exists."""
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=READ_ONLY)
    assert record.may_place_live_entry(None) is False


def test_refuted_may_not_place_a_live_entry() -> None:
    record = _record(state=TradeScopeState.REFUTED, refuted_ts=NOW)
    assert record.may_place_live_entry(None) is False


def test_unverified_may_not_place_a_live_entry() -> None:
    record = _record(state=TradeScopeState.UNVERIFIED, attested_scope=None)
    assert record.may_place_live_entry(None) is False


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
    assert record.may_place_live_entry(None) is True


def test_attested_read_only_is_refused_even_with_no_refutation_history() -> None:
    """Confirms the read-only arm is keyed on `attested_scope`, not merely "not refuted"."""
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=READ_ONLY, refuted_ts=None)
    assert record.may_place_live_entry(None) is False


@pytest.mark.parametrize("scope", [None, "", "TRADING", "read-only", "trading "])
def test_attested_with_anything_other_than_the_exact_trading_literal_is_refused(
    scope: str | None,
) -> None:
    record = _record(state=TradeScopeState.ATTESTED, attested_scope=scope)
    assert record.may_place_live_entry(None) is False


def test_the_record_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _record().state = TradeScopeState.REFUTED  # type: ignore[misc]


# -- credential_evidence: the four states, in isolation -------------------------------------------


def test_evidence_is_unfingerprinted_when_the_record_carries_no_fingerprint() -> None:
    record = _record(credential_fingerprint=None)
    assert record.credential_evidence(FP_A) is CredentialEvidence.UNFINGERPRINTED
    assert record.credential_evidence(None) is CredentialEvidence.UNFINGERPRINTED


def test_evidence_is_credential_unreadable_when_current_cannot_be_resolved() -> None:
    """The record HAS a fingerprint; the CURRENT credential could not be resolved. A fact about
    the observer, not the credential -- must not read as a mismatch."""
    record = _record(credential_fingerprint=FP_A)
    assert record.credential_evidence(None) is CredentialEvidence.CREDENTIAL_UNREADABLE


def test_evidence_matches_when_both_fingerprints_are_known_and_equal() -> None:
    record = _record(credential_fingerprint=FP_A)
    assert record.credential_evidence(FP_A) is CredentialEvidence.MATCHES


def test_evidence_is_different_credential_when_both_are_known_and_differ() -> None:
    record = _record(credential_fingerprint=FP_A)
    assert record.credential_evidence(FP_B) is CredentialEvidence.DIFFERENT_CREDENTIAL


# -- the full matrix: record fingerprint x current fingerprint x state ---------------------------
#
# `may_place_live_entry` must withdraw permission ONLY on DIFFERENT_CREDENTIAL, and only from a
# state that would otherwise grant it (CONFIRMED, ATTESTED+TRADING). UNFINGERPRINTED and
# CREDENTIAL_UNREADABLE must never change the state machine's answer either way -- for every
# state, with or without a fingerprint on the record, an unresolved current fingerprint (None)
# must reproduce exactly what a bare, fingerprint-less call would have said.

_GRANTS = (TradeScopeState.CONFIRMED, TradeScopeState.ATTESTED)
_STATES_AND_BASE_PERMISSION = [
    (TradeScopeState.CONFIRMED, TRADING, True),  # attested_scope irrelevant when CONFIRMED
    (TradeScopeState.ATTESTED, TRADING, True),
    (TradeScopeState.ATTESTED, READ_ONLY, False),
    (TradeScopeState.REFUTED, None, False),
    (TradeScopeState.UNVERIFIED, None, False),
]


@pytest.mark.parametrize("state,attested_scope,base_permission", _STATES_AND_BASE_PERMISSION)
def test_no_recorded_fingerprint_never_withholds_permission_regardless_of_current(
    state: TradeScopeState, attested_scope: str | None, base_permission: bool
) -> None:
    """record fingerprint None x current in {None, FP_A, FP_B}: UNFINGERPRINTED every time, so
    the state machine's own answer is the only thing that matters -- this is the v14-backfill
    shape and the exact scenario #633's issue names."""
    for current in (None, FP_A, FP_B):
        record = _record(state=state, attested_scope=attested_scope, credential_fingerprint=None)
        assert record.may_place_live_entry(current) is base_permission, (
            f"state={state} current={current!r}: expected {base_permission}"
        )


@pytest.mark.parametrize("state,attested_scope,base_permission", _STATES_AND_BASE_PERMISSION)
def test_recorded_fingerprint_with_unresolved_current_never_withholds_permission(
    state: TradeScopeState, attested_scope: str | None, base_permission: bool
) -> None:
    """record fingerprint FP_A x current None: CREDENTIAL_UNREADABLE, so again the state
    machine's own answer stands -- an observer that cannot resolve the current credential must
    not be treated as proof the credential changed."""
    record = _record(state=state, attested_scope=attested_scope, credential_fingerprint=FP_A)
    assert record.may_place_live_entry(None) is base_permission


@pytest.mark.parametrize("state,attested_scope,base_permission", _STATES_AND_BASE_PERMISSION)
def test_recorded_fingerprint_matching_current_never_withholds_permission(
    state: TradeScopeState, attested_scope: str | None, base_permission: bool
) -> None:
    """record fingerprint FP_A x current FP_A: MATCHES, so the state machine's own answer stands
    -- the strongest case, and it must not accidentally flip anything."""
    record = _record(state=state, attested_scope=attested_scope, credential_fingerprint=FP_A)
    assert record.may_place_live_entry(FP_A) is base_permission


@pytest.mark.parametrize("state,attested_scope,base_permission", _STATES_AND_BASE_PERMISSION)
def test_recorded_fingerprint_differing_from_current_always_withholds_permission(
    state: TradeScopeState, attested_scope: str | None, base_permission: bool
) -> None:
    """record fingerprint FP_A x current FP_B: DIFFERENT_CREDENTIAL. Must be False for every
    state -- including CONFIRMED and ATTESTED+TRADING, which would otherwise grant permission.
    This is the assertion that actually exercises the #633 fix: for the two states where
    `base_permission` is True, a passing test here proves the mismatch WITHDRAWS permission that
    the state machine alone would have granted."""
    record = _record(state=state, attested_scope=attested_scope, credential_fingerprint=FP_A)
    assert record.may_place_live_entry(FP_B) is False


def test_mismatch_withdraws_permission_from_confirmed_specifically() -> None:
    """Named separately from the matrix above because this is the exact production shape a
    reviewer should be able to find without reading a parametrized table: a CONFIRMED record
    (the strongest possible state-machine answer) is still vetoed once its fingerprint disagrees
    with the current credential."""
    record = _record(state=TradeScopeState.CONFIRMED, credential_fingerprint=FP_A)
    assert record.may_place_live_entry(FP_A) is True
    assert record.may_place_live_entry(FP_B) is False


def test_mismatch_withdraws_permission_from_attested_trading_specifically() -> None:
    record = _record(
        state=TradeScopeState.ATTESTED, attested_scope=TRADING, credential_fingerprint=FP_A
    )
    assert record.may_place_live_entry(FP_A) is True
    assert record.may_place_live_entry(FP_B) is False
