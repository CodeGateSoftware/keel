"""Pure, local arithmetic that proves a Robinhood Ed25519 credential pair malformed -- moved
here from `scripts/robinhood_smoke.py` (#233 PR4) so there is exactly ONE derivation of the
check, not two: the smoke script's long operator-facing `SystemExit` messages and the venue
readiness display's one-line summary (`keel.venue_readiness`, read via
`RobinhoodAdapter.credential_defect`) now both call `find_credential_defect` below instead of
each re-deriving it.

**The 2026-08-19 incident, and why a length check alone cannot catch it.** `ROBINHOOD_API_KEY`
held the base64-encoded Ed25519 PUBLIC key derived from `ROBINHOOD_PRIVATE_KEY`, instead of the
`rh-api-<uuid>` identifier Robinhood issues. Both values were well-formed 44-character base64 --
a public key and a seed are both 32 raw bytes, hence both 44 base64 characters -- so every
request signed correctly and every one 401'd, indistinguishable from a revoked key, a stale
clock, or a signing bug. The PREVIOUS guard compared the private key's length against 44 and
believed that caught "pasted the public key"; it could not have, because the public key passes
that same check. The only thing that actually distinguishes a seed's own public key from an
unrelated 32-byte value is DERIVING it and comparing -- `find_credential_defect` below is that
derivation, and it is the only check in this module that is new relative to the guard that
missed the incident.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum

#: The env var names `RobinhoodAdapter.DECLARED_CREDENTIAL_ENV` declares -- spelled once here so
#: the adapter module and this one cannot disagree about them. Matches
#: `keel/commands/credentials.py`'s `KNOWN` tuple (#437): the identifier Robinhood issues, and
#: the base64 Ed25519 seed generated locally.
API_KEY_ENV = "ROBINHOOD_API_KEY_CREDENTIAL"
PRIVATE_KEY_ENV = "ROBINHOOD_PRIVATE_KEY"

#: A base64-encoded 32-byte Ed25519 seed is 44 characters with padding.
#:
#: ⚠️ A raw Ed25519 PUBLIC key is ALSO 32 bytes, hence ALSO 44 base64 characters. This constant
#: earns its place against a PEM, a hex string, or a truncated paste -- it does NOT, and never
#: did, distinguish a seed from its own public key. See the module docstring.
SEED_B64_LEN = 44

#: A raw Ed25519 key, seed or public, before base64.
KEY_RAW_LEN = 32

#: The shape Robinhood issues for an API key, from the one credential observed to authenticate:
#: `rh-api-` followed by a UUID. Quoted in messages as guidance, deliberately NOT enforced -- it
#: is an observation about one credential, not a documented contract.
API_KEY_HINT = "rh-api-<uuid>"


class CredentialDefectKind(str, Enum):
    """Which of the locally-provable defects `find_credential_defect` found, in the order the
    checks run (each one only fires when the more specific check above it did not)."""

    MISSING = "missing"
    BAD_SEED_LENGTH = "bad_seed_length"
    KEY_IS_OWN_PUBLIC_KEY = "key_is_own_public_key"
    KEY_IS_A_KEY = "key_is_a_key"


@dataclass(frozen=True)
class CredentialDefect:
    """One locally-provable defect. `summary` is a one-line, SECRET-FREE description -- what
    `RobinhoodAdapter.credential_defect` returns verbatim for the readiness display. It never
    contains any part of a secret value, only the name of the variable and what is wrong with
    it."""

    kind: CredentialDefectKind
    summary: str
    #: Populated only for `MISSING`, so a caller that wants the specific variable name(s) (the
    #: smoke script's long message) does not have to re-parse `summary`.
    missing_names: tuple[str, ...] = ()


def raw_key_bytes(value: str) -> bytes | None:
    """The 32 raw bytes `value` encodes, or `None` if it is not base64 of an Ed25519-sized key.

    Total by construction: this runs against operator-pasted text, so every way base64 decoding
    can fail -- wrong alphabet, bad padding, plain prose -- has to mean "not a key" rather than a
    traceback. `validate=True` matters: without it `b64decode` silently DISCARDS characters
    outside the alphabet, so `rh-api-0f1e...` would decode to something rather than being
    rejected, and a real API key could be mistaken for a malformed one.
    """
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None
    return raw if len(raw) == KEY_RAW_LEN else None


def derive_public_key_b64(seed_b64: str) -> str | None:
    """The base64 PUBLIC key derived from a base64 Ed25519 seed, or `None` if it cannot be.

    `None` covers both "the seed is not a seed" and "pynacl is not installed", and both degrade
    to skipping one check rather than failing the run: this is a diagnostic, and a diagnostic
    that crashes on the malformed input it exists to describe is worse than one that stays quiet.
    """
    try:
        import nacl.signing

        raw = base64.b64decode(seed_b64, validate=True)
        return base64.b64encode(bytes(nacl.signing.SigningKey(raw).verify_key)).decode()
    except Exception:
        return None


def find_credential_defect(
    api_key: str | None,
    private_key: str | None,
    *,
    api_key_var: str = API_KEY_ENV,
    private_key_var: str = PRIVATE_KEY_ENV,
) -> CredentialDefect | None:
    """The three checks `scripts/robinhood_smoke.py::load_credentials` used to make inline, in
    the same order and for the same reason: each one only runs when the more specific check
    above it did not already fire, so a caller gets the most actionable thing true of the pair
    rather than the most generic.

    1. Either value absent.
    2. A private key that is not seed-shaped -- a PEM, a hex string, a truncated paste.
    3. An API key slot holding an Ed25519 key, with the case where it is THIS seed's own public
       key distinguished by name (the 2026-08-19 incident) from an unrelated 32-byte value.

    Returns `None` when nothing LOCALLY PROVABLE is wrong -- which is evidence of shape only,
    never evidence that the venue will accept the credential. `api_key`/`private_key` are
    stripped internally, so leading/trailing whitespace from a pasted value never causes a false
    "missing" or a false "bad length".
    """
    api_key = (api_key or "").strip()
    private_key = (private_key or "").strip()

    missing = tuple(
        name for name, val in ((api_key_var, api_key), (private_key_var, private_key)) if not val
    )
    if missing:
        return CredentialDefect(
            kind=CredentialDefectKind.MISSING,
            summary=f"missing {' and '.join(missing)}",
            missing_names=missing,
        )

    if len(private_key) != SEED_B64_LEN:
        return CredentialDefect(
            kind=CredentialDefectKind.BAD_SEED_LENGTH,
            summary=(
                f"{private_key_var} is {len(private_key)} characters, not the {SEED_B64_LEN} a "
                "base64-encoded 32-byte Ed25519 seed requires -- likely a PEM, a hex string, or "
                "a truncated paste"
            ),
        )

    if raw_key_bytes(api_key) is not None:
        if api_key == derive_public_key_b64(private_key):
            return CredentialDefect(
                kind=CredentialDefectKind.KEY_IS_OWN_PUBLIC_KEY,
                summary=(
                    f"{api_key_var} holds the base64 PUBLIC key derived from {private_key_var}, "
                    "not an API key identifier -- the operator pasted the wrong half of the "
                    "keypair (the 2026-08-19 incident)"
                ),
            )
        return CredentialDefect(
            kind=CredentialDefectKind.KEY_IS_A_KEY,
            summary=(
                f"{api_key_var} decodes as a 32-byte base64 value -- an Ed25519 KEY, not the "
                f"{API_KEY_HINT} identifier Robinhood issues"
            ),
        )

    return None


__all__ = [
    "API_KEY_ENV",
    "API_KEY_HINT",
    "KEY_RAW_LEN",
    "PRIVATE_KEY_ENV",
    "SEED_B64_LEN",
    "CredentialDefect",
    "CredentialDefectKind",
    "derive_public_key_b64",
    "find_credential_defect",
    "raw_key_bytes",
]
