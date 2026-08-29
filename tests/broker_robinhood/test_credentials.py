"""Tests for `keel_broker_robinhood.credentials` -- the ONE derivation of "is this Robinhood
Ed25519 credential pair locally provably wrong" (#233 PR4), moved out of
`scripts/robinhood_smoke.py::load_credentials` so the smoke script and the venue readiness
display (`RobinhoodAdapter.credential_defect`) cannot silently disagree.

`tests/scripts/test_robinhood_smoke.py` already pins the exact operator-facing wording the
script builds around this module's verdicts; these tests pin the DERIVATION itself, including
the one case the brief calls out by name: a length check alone cannot catch the 2026-08-19
incident, because a public key is exactly as long as a seed.
"""

from __future__ import annotations

import base64

import nacl.signing
import pytest
from keel_broker_robinhood.credentials import (
    API_KEY_ENV,
    KEY_RAW_LEN,
    PRIVATE_KEY_ENV,
    SEED_B64_LEN,
    CredentialDefectKind,
    derive_public_key_b64,
    find_credential_defect,
    raw_key_bytes,
)

_SEED_RAW = bytes(range(32))
_VALID_SEED_B64 = base64.b64encode(_SEED_RAW).decode()
_ITS_PUBLIC_KEY_B64 = base64.b64encode(
    bytes(nacl.signing.SigningKey(_SEED_RAW).verify_key)
).decode()
_VALID_API_KEY = "rh-api-1e2d3c4b-5a69-4788-9f01-23456789abcd"


def test_the_declared_env_names_match_credentials_py() -> None:
    """`keel/commands/credentials.py`'s `KNOWN` tuple (#437) is the source of truth for the
    names operators actually set; this module's constants must name the SAME two variables, or
    `RobinhoodAdapter.DECLARED_CREDENTIAL_ENV` and the readiness display would be checking
    presence of variables nobody is ever asked to set."""
    assert API_KEY_ENV == "ROBINHOOD_API_KEY_CREDENTIAL"
    assert PRIVATE_KEY_ENV == "ROBINHOOD_PRIVATE_KEY"


def test_a_wellformed_pair_has_no_defect() -> None:
    assert find_credential_defect(_VALID_API_KEY, _VALID_SEED_B64) is None


@pytest.mark.parametrize(
    ("api_key", "private_key", "expected_missing"),
    [
        (None, _VALID_SEED_B64, (API_KEY_ENV,)),
        (_VALID_API_KEY, None, (PRIVATE_KEY_ENV,)),
        ("", "", (API_KEY_ENV, PRIVATE_KEY_ENV)),
        ("   ", _VALID_SEED_B64, (API_KEY_ENV,)),  # whitespace-only strips to missing
    ],
)
def test_missing_values_are_named_individually(
    api_key: str | None, private_key: str | None, expected_missing: tuple[str, ...]
) -> None:
    defect = find_credential_defect(api_key, private_key)
    assert defect is not None
    assert defect.kind is CredentialDefectKind.MISSING
    assert defect.missing_names == expected_missing


def test_a_misshapen_private_key_is_caught_by_length() -> None:
    defect = find_credential_defect(_VALID_API_KEY, "tooshort")
    assert defect is not None
    assert defect.kind is CredentialDefectKind.BAD_SEED_LENGTH
    assert "8 characters" in defect.summary


# -- the 2026-08-19 incident: a length check alone cannot catch this ----------------------------


def test_the_public_key_pasted_as_the_api_key_is_caught_by_derivation() -> None:
    """The exact incident: the public key is 44 base64 characters, exactly like the seed, so it
    passes `BAD_SEED_LENGTH`'s check with room to spare. Only DERIVING the public key from the
    seed and comparing distinguishes it -- which is what this asserts actually fires."""
    defect = find_credential_defect(_ITS_PUBLIC_KEY_B64, _VALID_SEED_B64)
    assert defect is not None
    assert defect.kind is CredentialDefectKind.KEY_IS_OWN_PUBLIC_KEY
    assert "PUBLIC key" in defect.summary
    assert PRIVATE_KEY_ENV in defect.summary


def test_a_length_check_alone_does_not_catch_the_incident() -> None:
    """Mutation-target proof, stated as its own claim rather than folded into the test above: the
    PREVIOUS guard's mistake, reproduced and shown to pass a bare length check. If this ever
    starts failing, `find_credential_defect` has regressed to exactly what the 2026-08-19
    incident already proved insufficient."""
    assert len(_ITS_PUBLIC_KEY_B64) == SEED_B64_LEN
    assert len(_VALID_SEED_B64) == SEED_B64_LEN
    # A length-only guard would see two equally well-formed 44-character values and pass both.
    # `find_credential_defect` does not stop there -- it derives and compares, which is the only
    # thing that tells the two apart (asserted immediately above).


def test_an_unrelated_ed25519_key_in_the_api_key_slot_is_a_different_finding() -> None:
    """A 32-byte base64 value that is NOT this seed's own public key still cannot be an API key
    identifier -- but it is named differently, since the guard has nothing to match it against."""
    other = base64.b64encode(bytes(range(100, 132))).decode()
    defect = find_credential_defect(other, _VALID_SEED_B64)
    assert defect is not None
    assert defect.kind is CredentialDefectKind.KEY_IS_A_KEY
    assert "Ed25519 KEY" in defect.summary


def test_a_real_api_key_is_not_mistaken_for_base64() -> None:
    """`rh-api-<uuid>` contains `-`, outside the base64 alphabet -- without `validate=True` this
    would decode (discarding invalid characters) and could be misread as a pasted key."""
    assert find_credential_defect(_VALID_API_KEY, _VALID_SEED_B64) is None


def test_whitespace_around_a_valid_pair_is_stripped() -> None:
    assert find_credential_defect(f"  {_VALID_API_KEY}  ", f"\t{_VALID_SEED_B64}\n") is None


# -- the two pure primitives, directly ------------------------------------------------------------


def test_raw_key_bytes_rejects_non_base64() -> None:
    assert raw_key_bytes("not base64 at all!!") is None


def test_raw_key_bytes_rejects_the_wrong_decoded_length() -> None:
    short = base64.b64encode(b"short").decode()
    assert raw_key_bytes(short) is None


def test_raw_key_bytes_accepts_a_32_byte_value() -> None:
    assert raw_key_bytes(_VALID_SEED_B64) == _SEED_RAW
    assert len(raw_key_bytes(_VALID_SEED_B64) or b"") == KEY_RAW_LEN


def test_derive_public_key_b64_matches_nacl_directly() -> None:
    assert derive_public_key_b64(_VALID_SEED_B64) == _ITS_PUBLIC_KEY_B64


def test_derive_public_key_b64_is_none_for_a_non_seed() -> None:
    assert derive_public_key_b64("not a seed") is None


def test_no_summary_ever_carries_the_raw_secret_value() -> None:
    """`credential_defect`'s contract (#233 PR4): a DESCRIPTION, never a value. Every summary
    this module can produce is scanned for the actual secret bytes it was given."""
    for defect in (
        find_credential_defect(None, None),
        find_credential_defect(_VALID_API_KEY, "tooshort"),
        find_credential_defect(_ITS_PUBLIC_KEY_B64, _VALID_SEED_B64),
        find_credential_defect(
            base64.b64encode(bytes(range(100, 132))).decode(), _VALID_SEED_B64
        ),
    ):
        assert defect is not None
        assert _VALID_SEED_B64 not in defect.summary
        assert _ITS_PUBLIC_KEY_B64 not in defect.summary
