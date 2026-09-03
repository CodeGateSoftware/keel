"""`keel brokers list` must show the cash posture -- #691's last acceptance box.

A SEPARATE block, after readiness, and that is this module's own argument rather than a
preference: `keel/venue_readiness.py` states that readiness is "rendered as its own headed block,
AFTER the declarations block, with its own honesty line", because merging two different questions
"would re-blur exactly the distinction #233 exists to draw". Posture is a third question --
readiness asks whether this CREDENTIAL may trade, posture asks whether this ACCOUNT can borrow --
and folding it into either of the first two would repeat the mistake both were shaped to avoid.

`test_the_block_never_claims_a_posture_was_verified` is the one that matters. Every other surface
in keel can say "checked"; this one cannot, because no venue exposes the field. A block that
reads like a verification would invert the exact guarantee `docs/fiqh-basis.md` spends a section
establishing.
"""

from __future__ import annotations

from keel_core.cash_posture import (
    ATTESTATION_TTL_SEC,
    MARGIN_ENABLED,
    SPOT_CASH,
    CashPostureState,
    VenueCashPosture,
)

from keel.commands.brokers import render_cash_posture_lines

NOW = 1_800_000_000


def _record(
    venue: str = "coinbase",
    state: CashPostureState = CashPostureState.ATTESTED,
    posture: str | None = SPOT_CASH,
    due_ts: int | None = NOW + ATTESTATION_TTL_SEC,
    refuted_ts: int | None = None,
) -> VenueCashPosture:
    return VenueCashPosture(
        venue=venue,
        state=state,
        attested_posture=posture,
        attested_ts=NOW,
        attest_due_ts=due_ts,
        refuted_ts=refuted_ts,
        refuted_reason="INTX portfolio present" if refuted_ts else None,
        credential_fingerprint=None,
    )


def _text(records, now_ts: int = NOW) -> str:
    return "\n".join(render_cash_posture_lines(records, now_ts=now_ts))


def test_the_block_is_headed_so_it_cannot_be_read_as_part_of_readiness() -> None:
    text = _text([_record()])
    assert "cash posture" in text.lower()


def test_nothing_attested_says_so_and_names_the_command() -> None:
    text = _text([])
    assert "no venue" in text.lower() or "none" in text.lower()
    assert "keel posture attest" in text


def test_an_attested_venue_shows_its_posture_and_expiry() -> None:
    text = _text([_record()])
    assert "coinbase" in text
    assert "spot_cash" in text
    assert "expires" in text.lower()


def test_an_expired_attestation_is_marked_expired() -> None:
    text = _text([_record()], now_ts=NOW + ATTESTATION_TTL_SEC + 1)
    assert "EXPIRED" in text


def test_a_margin_venue_is_shown_as_refusing_entries() -> None:
    text = _text([_record(posture=MARGIN_ENABLED)])
    assert "margin" in text.lower()


def test_a_refuted_venue_names_the_venue_evidence() -> None:
    text = _text([_record(state=CashPostureState.REFUTED, refuted_ts=NOW)])
    assert "INTX portfolio present" in text


def test_the_block_never_claims_a_posture_was_verified() -> None:
    """THE pin. No venue exposes this field, so nothing here may read as a check that passed.
    `docs/fiqh-basis.md` spends a section establishing that the venue can only REFUTE, and a
    display saying "verified" would invert it for every reader who never opens the doc."""
    text = _text([_record()]).lower()
    for forbidden in ("verified", "confirmed", "checked", "proven"):
        assert forbidden not in text, f"the posture block reads as {forbidden!r}"


def test_the_block_says_the_claim_is_the_operators_own() -> None:
    """The positive half of the same pin: not merely avoiding "verified", but saying whose
    statement this is and that nothing can check it."""
    text = _text([_record()]).lower()
    assert "attested" in text
    assert "no venue" in text or "cannot" in text


def test_venues_are_listed_in_a_stable_order() -> None:
    """Sorted, so the same database renders the same text twice.

    THREE venues, in an order where reversing is not the same as sorting: with only
    `[coinbase, alpaca]` a mutation replacing `sorted` with `reversed` produced the identical
    output and the pin stayed green.
    """
    text = _text(
        [_record(venue="alpaca"), _record(venue="robinhood"), _record(venue="coinbase")]
    )
    assert text.index("alpaca") < text.index("coinbase") < text.index("robinhood")
