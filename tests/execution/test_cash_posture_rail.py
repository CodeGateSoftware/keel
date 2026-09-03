"""Rail 22: the cash posture no venue will affirm -- issue #691, Stage 2 of #666.

Coinbase exposes no cash-versus-margin field for spot, so the venue check refutes and never
issues (Stage 1). This rail asks the only source that CAN answer -- the operator's record -- and
fails closed when there is none, exactly as rails 12/13/17/20 do.

TWO PROPERTIES CARRY THE RAIL, and both are about what it must NOT do:

* `test_every_non_entry_is_untouched_by_a_missing_posture` -- exits, stop rolls, cancels and DCA
  exits pass with no record at all. A rail that blocked an exit over a fact about the ACCOUNT
  would strand a position that wanted out, which is rails 11/16/17/20's own rule and the reason
  this one is `is_buy`-gated.
* `test_paper_mode_skips_it_and_says_so` -- paper has no live account to attest, so the rail is
  skipped AND reported as skipped. Silently omitting it would make a paper track record claim a
  guarantee it never ran.

The veto messages are pinned individually because they are the whole operator interface here:
there is no venue read to consult, so a message that fails to say what to run leaves a person
with a halted engine and no next step.
"""

from __future__ import annotations

import pytest
from keel_core.cash_posture import MARGIN_ENABLED, CashPostureState

from keel.execution.guards import LIVE_STATE_RAILS, check
from tests.conftest import attest_cash_posture
from tests.execution.test_guards import (
    NOW_TS,
    _config,
    _intent,
    repo,  # noqa: F401  -- the seeded guards fixture
)


def _violations(repository, *, is_buy: bool = True, offline: bool = False) -> list[str]:
    result = check(
        _intent(side="BUY" if is_buy else "SELL"),
        repository,
        _config(),
        now_ts=NOW_TS,
        offline=offline,
    )
    return list(result.violations)


def _cash(violations: list[str]) -> list[str]:
    return [v for v in violations if v.startswith("cash_posture")]


def test_a_missing_posture_vetoes_a_live_entry(fresh_repo_without_posture) -> None:
    """Fails closed on absent. No human has stated the account's posture, and no venue read can
    supply one, so unknown is not evidence of a cash account."""
    lines = _cash(_violations(fresh_repo_without_posture))
    assert lines, "a missing cash posture did not veto a live entry"
    assert "coinbase" in lines[0]
    assert "keel posture attest" in lines[0]


def test_an_in_force_spot_cash_attestation_admits(fresh_repo_without_posture) -> None:
    attest_cash_posture(fresh_repo_without_posture, now_ts=NOW_TS)
    assert not _cash(_violations(fresh_repo_without_posture))


def test_an_expired_attestation_vetoes_and_says_it_expired(fresh_repo_without_posture) -> None:
    """"Expired" and "never attested" call for the same command but tell an operator different
    things about their own diligence, so the messages differ."""
    attest_cash_posture(
        fresh_repo_without_posture, now_ts=NOW_TS, attest_due_ts=NOW_TS - 1
    )
    (line,) = _cash(_violations(fresh_repo_without_posture))
    assert "expired" in line.lower()
    assert "keel posture attest" in line


def test_a_margin_attestation_vetoes_and_names_what_was_attested(
    fresh_repo_without_posture,
) -> None:
    """An operator who attests margin gave an honest answer. The veto has to reflect that rather
    than reading as "you forgot to attest" -- the remedy is a change to the ACCOUNT, not a
    re-run of the command."""
    attest_cash_posture(
        fresh_repo_without_posture, now_ts=NOW_TS, attested_posture=MARGIN_ENABLED
    )
    (line,) = _cash(_violations(fresh_repo_without_posture))
    assert "margin" in line.lower()


def test_a_refuted_posture_vetoes_and_names_the_venue_evidence(
    fresh_repo_without_posture,
) -> None:
    attest_cash_posture(
        fresh_repo_without_posture,
        now_ts=NOW_TS,
        state=CashPostureState.REFUTED,
        refuted_ts=NOW_TS,
        refuted_reason="INTX portfolio present",
    )
    (line,) = _cash(_violations(fresh_repo_without_posture))
    assert "INTX portfolio present" in line


def test_a_different_credential_vetoes_with_its_own_message(
    fresh_repo_without_posture, monkeypatch
) -> None:
    """#633. Something WAS attested here, but not for the credential in place now -- and saying
    "never attested" about that would repeat #624."""
    attest_cash_posture(
        fresh_repo_without_posture, now_ts=NOW_TS, credential_fingerprint="fp-old"
    )
    monkeypatch.setattr(
        "keel.execution.guards.current_credential_fingerprint", lambda _venue: "fp-new"
    )
    (line,) = _cash(_violations(fresh_repo_without_posture))
    assert "DIFFERENT" in line
    assert "never" not in line.lower()


# --- what the rail must NOT do ----------------------------------------------------------------


@pytest.mark.parametrize("side", ["SELL"])
def test_every_non_entry_is_untouched_by_a_missing_posture(
    fresh_repo_without_posture, side
) -> None:
    """THE property that keeps this rail safe. A position that wants out must always be able to
    leave, whatever is or is not recorded about the account."""
    assert not _cash(_violations(fresh_repo_without_posture, is_buy=False))


def test_paper_mode_skips_it_and_says_so(fresh_repo_without_posture) -> None:
    """Skipped because paper has no live account -- and RECORDED as skipped, so a paper track
    record is honest about the rail it never ran."""
    result = check(
        _intent(side="BUY"), fresh_repo_without_posture, _config(), now_ts=NOW_TS, offline=True
    )
    assert not [v for v in result.violations if v.startswith("cash_posture")]
    assert "cash_posture" in result.skipped_rails


def test_the_rail_is_declared_a_live_state_rail() -> None:
    """`LIVE_STATE_RAILS` is what makes the paper skip reportable rather than invisible."""
    assert "cash_posture" in LIVE_STATE_RAILS


@pytest.fixture()
def fresh_repo_without_posture(request):
    """The standard guards `repo` fixture, with the cash-posture record REMOVED.

    That fixture seeds one so the other twenty-one rails' tests are not incidentally vetoed by
    rail 22 -- which means a test ABOUT rail 22 has to take it back out, or it would be asserting
    against the fixture's own attestation instead of the state it means to.
    """
    # `getfixturevalue` rather than a parameter named `repo`: the module-level re-export above
    # is what registers the fixture, and a parameter of the same name would shadow it.
    repository = request.getfixturevalue("repo")
    repository._conn.execute("DELETE FROM venue_cash_postures")  # noqa: SLF001
    repository._conn.commit()  # noqa: SLF001
    return repository
