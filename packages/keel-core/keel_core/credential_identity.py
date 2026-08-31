"""A non-reversible fingerprint of WHICH credential is in place, for #633.

`venue_trade_scopes` (#233) records evidence about a credential -- the venue accepted a
placement, or refused one on permissions -- but until now the record was keyed by VENUE alone.
Nothing noticed when the credential underneath a venue changed: `keel credentials forget
CDP_API_KEY` followed by `keel credentials set CDP_API_KEY` (a different key entirely) left
`venue_trade_scopes` still saying `coinbase -> confirmed`, and rail 20 would permit a live entry
on the NEW key on the strength of evidence collected under the OLD one.

The obvious fix -- reset the record when `credentials forget`/`set` runs -- is a gate the common
path walks around. `keel credentials show` reports three sources (keychain, `.env`, environment)
and `forget`'s own output says keel does not edit `.env`. An operator who edits `.env` directly,
or exports a different value into the environment, changes the credential with no keel command
in the loop at all. So the evidence is bound to the credential directly: a fingerprint recorded
alongside the evidence, compared against the CURRENT credential at read time. It then does not
matter how the credential changed.

**What is fingerprinted, and why it must never be the signing secret.** Every venue's declared
credential pair has an IDENTIFIER (`CDP_API_KEY`'s value is the CDP key id; `ROBINHOOD_API_KEY_
CREDENTIAL`'s value is the `rh-api-<uuid>` identifier; `ALPACA_API_KEY_ID`'s value is the key id)
and a SIGNING SECRET (`CDP_API_SECRET`, `ROBINHOOD_PRIVATE_KEY`, `ALPACA_API_SECRET_KEY`). This
module fingerprints only the identifier. The secret is never read by this module and never
reaches the hash -- so even a total preimage break on sha256 recovers, at worst, a credential ID
and nothing that can sign a request. That is not an incidental property of the choice; it is the
reason the identifier is the right input independent of any leak argument: rotating the secret
under the SAME identifier is the SAME credential with the SAME scope and must not withdraw
permission, while a different identifier is a genuinely different credential. Fingerprinting the
secret would treat an ordinary rotation as a credential change and a stolen-then-replaced secret
under the same key id as no change at all -- backwards on both counts.

**Non-reversible, never logged, never rendered.** `ResolvedSecret.__repr__`
(`keel_core/secrets.py`) is the existing precedent for this discipline. Nothing in this module or
its callers should add the fingerprint to a log event, a `__repr__`, or an operator-facing render
-- PR2 of #633 is where a fingerprint is finally SHOWN, and only as a display that distinguishes
"different credential" from "never attested", never as the raw digest.

**`current_credential_fingerprint` never raises and returns `None` for anything it does not
know.** A venue absent from `CREDENTIAL_IDENTIFIER_ENV`, or an identifier that resolves to
nothing, yields `None` -- which callers must treat as "current credential unknown", never as
grounds to withdraw permission. That is the fail-safe direction: a fingerprinting failure must
never manufacture a mismatch, and `keel_core.trade_scope.VenueTradeScope.credential_evidence`
is where that failure mode is drawn out fully (`CREDENTIAL_UNREADABLE`, distinct from an actual
`DIFFERENT_CREDENTIAL`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Which environment-variable name carries each venue's credential IDENTIFIER (never the signing
#: secret) -- the first name in each adapter's `DECLARED_CREDENTIAL_ENV` pair. Hardcoded here
#: rather than imported from the adapters, following the precedent `keel/venue_readiness.py`'s
#: `CREDENTIALED_VENUES` already set: "matches `keel/commands/credentials.py`'s `KNOWN` tuple's
#: venues without importing that module". A venue NOT in this map yields `None` from
#: `current_credential_fingerprint` -- no fingerprint recorded, no fingerprint compared -- which
#: is the fail-safe direction: it can never WITHDRAW permission, only fail to add the extra check.
CREDENTIAL_IDENTIFIER_ENV: dict[str, str] = {
    "coinbase": "CDP_API_KEY",
    "alpaca": "ALPACA_API_KEY_ID",
    "robinhood": "ROBINHOOD_API_KEY_CREDENTIAL",
}

#: Domain-separates this hash from every other sha256 digest anywhere in the codebase, and
#: versions the derivation: changing this constant is the deliberate way to invalidate every
#: previously recorded fingerprint at once (a version bump), rather than accidentally colliding
#: with some unrelated hash of the same bytes computed somewhere else for a different purpose.
_FINGERPRINT_DOMAIN = b"keel/venue-trade-scope/credential-identifier/v1"

#: Truncated to 128 bits (32 hex chars). This REMOVES information rather than adding any -- the
#: fingerprint's only job is equality comparison between two digests derived from the same
#: formula, and 128 bits of collision resistance is astronomically more than this record's threat
#: model (an operator noticing a swapped key) will ever need. Truncating is also one more reason
#: this can never be inverted back to a credential: even a full preimage break on the untruncated
#: hash would leave 128 bits of the identifier's entropy folded away for good.
_FINGERPRINT_HEX_LEN = 32


def fingerprint_identifier(venue: str, env_name: str, value: str) -> str:
    """Pure. A domain-separated, non-reversible fingerprint of one credential identifier value.

    `venue` and `env_name` are folded into the hash alongside `value` so that the SAME identifier
    string used by two different venues (or recorded under two different env-var names) produces
    two different fingerprints -- a coincidental string collision must not read as "the same
    credential". NUL-separated and utf-8 encoded so no combination of shorter/longer field values
    can be confused for another (`"ab" + "c"` and `"a" + "bc"` hash differently only because of
    the separators).
    """
    digest = hashlib.sha256(
        _FINGERPRINT_DOMAIN
        + b"\x00"
        + venue.encode("utf-8")
        + b"\x00"
        + env_name.encode("utf-8")
        + b"\x00"
        + value.encode("utf-8")
    ).hexdigest()
    return digest[:_FINGERPRINT_HEX_LEN]


def current_credential_fingerprint(venue: str, *, env_path: str | Path | None = None) -> str | None:
    """The fingerprint of `venue`'s credential IDENTIFIER as it resolves RIGHT NOW, or `None`.

    `None` covers two cases the caller must treat identically -- "unknown", never a reason to
    withdraw permission:

    - `venue` is not in `CREDENTIAL_IDENTIFIER_ENV` (no adapter this module knows how to
      fingerprint).
    - The identifier's env name resolves to nothing at all (`read_secret(...).value is None`) --
      no credential configured, or a momentarily unreadable `.env`/keychain.

    Resolved through `keel_core.secrets.read_secret` -- the SAME function
    `keel_core.config.load_secrets` calls to build the CDP credentials the broker actually signs
    with, so a coinbase fingerprint is bound to the value that will really be used, not a
    separately-resolved copy that could disagree with it. `read_secret` itself never raises; this
    function adds no additional way to raise either, so a caller on a write path (confirming or
    refuting trade scope) can call it unconditionally without a try/except of its own.

    **Alpaca is a deliberate, harmless divergence.** `keel_core.config.load_alpaca_secrets` reads
    the real environment and `.env` but NOT the keychain -- unlike `read_secret`, which also
    checks the keychain. So it is theoretically possible for this function to fingerprint an
    Alpaca identifier the broker itself would never see (one stored ONLY in the keychain). This
    is harmless for #633's purpose: the fingerprint is WRITTEN (on confirm/refute) and READ (rail
    20) through this SAME function, so both sides of the comparison always agree with each other
    -- the fingerprint's only job is internal consistency between what was recorded and what is
    current, not re-validating that the broker itself can resolve the credential (that is
    `load_alpaca_secrets`'s job, unchanged by this module).
    """
    env_name = CREDENTIAL_IDENTIFIER_ENV.get(venue)
    if env_name is None:
        return None

    from keel_core.secrets import read_secret

    value = read_secret(env_name, env_path=env_path).value
    if not value:
        return None

    return fingerprint_identifier(venue, env_name, value)


__all__ = [
    "CREDENTIAL_IDENTIFIER_ENV",
    "current_credential_fingerprint",
    "fingerprint_identifier",
]
