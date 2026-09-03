"""The per-venue cash-posture record -- issue #691, Stage 2 of #666.

Stage 1 established the fact that shapes all of this: **Coinbase exposes no cash-versus-margin
field for spot.** A probe of the live account on 2026-09-02 found `margin_rate` present in the
response schema and `null` in the value, and portfolios `DEFAULT`/`CONSUMER` with no INTX. Every
margin, borrow, leverage and liquidation field in the SDK lives in the futures or perpetuals
types. So the venue check REFUTES and never issues: an INTX portfolio proves derivatives are
available, its absence proves nothing.

That residual is not an engineering gap, it is the same shape as rail 17's `qabd` -- silence is
not evidence of possession, and here silence is not evidence of a cash account. What closes it is
a human who knows their own account saying so on the record, with the venue able to contradict
them.

TWO DESIGN CHOICES DIFFER FROM `VenueTradeScope`, AND BOTH ARE DELIBERATE:

* **There is no `CONFIRMED` state**, and `test_there_is_no_confirmed_state` pins its absence.
  Trade scope earns `CONFIRMED` because the venue re-proves it on every accepted placement. No
  placement, read, or field can ever prove a spot account is cash-only, so a `CONFIRMED` value
  would be a state nothing could legally write -- and a state nothing can write is one a future
  reader will eventually write anyway.
* **There IS a TTL**, where trade scope has none. Trade scope needs no clock because the venue
  re-confirms it continuously; this record has NO observation channel at all, which is exactly
  `VenueSubscription`'s situation, and there a due date is the only thing between a lapsed claim
  and a live spend. Same argument, same remedy.
"""

from __future__ import annotations

from keel_core.cash_posture import (
    ATTESTATION_TTL_SEC,
    MARGIN_ENABLED,
    SPOT_CASH,
    CashPostureState,
    VenueCashPosture,
)
from keel_core.trade_scope import CredentialEvidence

NOW = 1_800_000_000
FP = "fp-current"


def _posture(
    state: CashPostureState = CashPostureState.ATTESTED,
    posture: str | None = SPOT_CASH,
    attested_ts: int | None = NOW - 86400,
    due_ts: int | None = NOW + 86400,
    refuted_ts: int | None = None,
    fingerprint: str | None = FP,
) -> VenueCashPosture:
    return VenueCashPosture(
        venue="coinbase",
        state=state,
        attested_posture=posture,
        attested_ts=attested_ts,
        attest_due_ts=due_ts,
        refuted_ts=refuted_ts,
        refuted_reason="INTX portfolio present" if refuted_ts else None,
        credential_fingerprint=fingerprint,
    )


# --- the state machine's shape ----------------------------------------------------------------


def test_only_the_two_states_something_can_actually_write_exist() -> None:
    """THE load-bearing pin, and it cuts BOTH ways.

    `CONFIRMED` is absent because no venue read can ever affirm a spot cash posture, so it would
    be a state nothing is entitled to write -- and an unreachable state is one a later reader
    eventually reaches for.

    `UNVERIFIED` is absent for the SAME reason, which the first version of this module missed:
    `attest` writes `ATTESTED`, `refute_posture` writes `REFUTED`, and "nobody has attested" is
    represented by NO ROW -- `get_venue_cash_posture` returns `None`. An `UNVERIFIED` member was
    therefore just as unreachable as `CONFIRMED`, sitting one line below the docstring arguing
    against exactly that. Absence-as-`None` and absence-as-a-row are the same fact, and having
    two spellings for it invites a caller to check one and miss the other."""
    assert {s.value for s in CashPostureState} == {"attested", "refuted"}
    assert not hasattr(CashPostureState, "CONFIRMED")
    assert not hasattr(CashPostureState, "UNVERIFIED")


def test_the_ttl_is_longer_than_rail_17s_window_and_shorter_than_forever() -> None:
    """A posture changes far less often than a withdrawal freeze -- an account does not silently
    acquire margin -- so rail 17's 7 days would be ceremony. But "never expires" would let a
    posture attested about a different account years ago authorise a live entry today."""
    assert ATTESTATION_TTL_SEC > 7 * 86400
    assert ATTESTATION_TTL_SEC <= 366 * 86400


# --- may_place_live_entry --------------------------------------------------------------------


# "Nobody has attested" is the ABSENCE of a record, not a state within one, so it is tested
# where absence is representable: `tests/execution/test_cash_posture_rail.py::
# test_a_missing_posture_vetoes_a_live_entry`.


def test_an_in_force_spot_cash_attestation_permits() -> None:
    assert _posture().may_place_live_entry(NOW, FP)


def test_an_operator_who_attests_margin_is_refused_not_ignored() -> None:
    """Attesting `margin_enabled` is an HONEST answer, and the right response is a refusal that
    records it -- not silently treating the record as absent, which would read as "nobody has
    attested" in every report."""
    assert not _posture(posture=MARGIN_ENABLED).may_place_live_entry(NOW, FP)


def test_an_unrecognised_posture_refuses() -> None:
    assert not _posture(posture="probably fine").may_place_live_entry(NOW, FP)
    assert not _posture(posture=None).may_place_live_entry(NOW, FP)


def test_an_expired_attestation_refuses() -> None:
    assert not _posture(due_ts=NOW - 1).may_place_live_entry(NOW, FP)


def test_the_due_moment_is_already_expired() -> None:
    """Due-at is when it expires, not one tick still-good -- `VenueSubscription`'s boundary,
    copied so the two records cannot disagree about what a due date means."""
    assert not _posture(due_ts=NOW).may_place_live_entry(NOW, FP)
    assert _posture(due_ts=NOW + 1).may_place_live_entry(NOW, FP)


def test_a_missing_due_date_refuses() -> None:
    """An attestation with no due date is a claim that never expires, which this record does not
    permit. Rather than inventing one at read time, it refuses -- the writer owes a due date."""
    assert not _posture(due_ts=None).may_place_live_entry(NOW, FP)


def test_a_refuted_record_refuses_even_inside_its_ttl() -> None:
    """The venue found an INTX portfolio. That is evidence, and it outranks the claim."""
    assert not _posture(
        state=CashPostureState.REFUTED, refuted_ts=NOW - 10
    ).may_place_live_entry(NOW, FP)


def test_re_attesting_over_a_refutation_recovers_and_keeps_the_history() -> None:
    """The recovery path, and the reason the predicate reads `state` rather than `refuted_ts`:
    an operator who closes the INTX portfolio and re-attests must be able to trade again, while
    `doctor` can still say "you re-attested a venue that refuted on <date>". Vetoing on
    `refuted_ts is not None` would make the one path that recovers never actually unblock --
    exactly the argument `VenueTradeScope.may_place_live_entry` sets out."""
    record = _posture(state=CashPostureState.ATTESTED, refuted_ts=NOW - 1000)
    assert record.may_place_live_entry(NOW, FP)
    assert record.refuted_ts == NOW - 1000


# --- #633: the credential the claim was made about --------------------------------------------


def test_a_different_credential_withdraws_permission_unconditionally() -> None:
    """A posture attested under one credential is not a claim about the account another
    credential reaches. Same rule as trade scope, and it overrides an in-force ATTESTED."""
    assert not _posture().may_place_live_entry(NOW, "fp-other")


def test_an_unfingerprinted_record_still_permits() -> None:
    """Fail-SAFE, matching trade scope: a record predating fingerprinting was never going to
    have one, and withholding permission would veto the next entry on a healthy deployment."""
    assert _posture(fingerprint=None).may_place_live_entry(NOW, FP)


def test_an_unreadable_current_credential_still_permits() -> None:
    """"I could not resolve this" is not "this changed" -- a locked keychain is a fact about the
    observer, not the credential."""
    assert _posture().may_place_live_entry(NOW, None)


def test_the_credential_evidence_vocabulary_is_shared_with_trade_scope() -> None:
    """One vocabulary, not two. A second copy of these four states would drift, and #624's whole
    lesson was that conflating two kinds of "unknown" fails differently."""
    assert _posture().credential_evidence(FP) is CredentialEvidence.MATCHES
    assert _posture().credential_evidence("fp-other") is CredentialEvidence.DIFFERENT_CREDENTIAL
    assert _posture(fingerprint=None).credential_evidence(FP) is CredentialEvidence.UNFINGERPRINTED
    assert _posture().credential_evidence(None) is CredentialEvidence.CREDENTIAL_UNREADABLE


# --- is_current, reported separately from permission ------------------------------------------


def test_is_current_answers_the_clock_question_alone() -> None:
    """Separate from `may_place_live_entry` so a report can say "expired" rather than only
    "refused" -- an operator needs to know whether to re-attest or to fix the account."""
    assert _posture().is_current(NOW)
    assert not _posture(due_ts=NOW - 1).is_current(NOW)
    assert not _posture(due_ts=None).is_current(NOW)
    # A refuted record can still be inside its window; the clock and the evidence are
    # different questions.
    assert _posture(state=CashPostureState.REFUTED, refuted_ts=NOW).is_current(NOW)
