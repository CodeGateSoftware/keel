"""Venue-readiness states, derived STRICTLY OFFLINE (#233 PR4): one source of truth for
`keel brokers list`'s readiness block and `/api/venues`' `readiness` rows, so the CLI and the web
cannot silently disagree about what a venue's credential can actually do today.

`keel/commands/brokers.py`'s own capability display answers "what does the ADAPTER declare it
can do" -- never an inference about the operator's keys. This module answers a different
question: "can a live ENTRY actually be placed on THIS deployment, right now". Both are true
statements about the same adapter and neither should be confused with the other (see
`keel/commands/brokers.py::NO_KEY_INFERENCE_LINE` for why that confusion is the thing #233
exists to prevent).

The motivating case (2026-08-19): `ROBINHOOD_API_KEY` held the base64 Ed25519 PUBLIC key derived
from `ROBINHOOD_PRIVATE_KEY` instead of the `rh-api-<uuid>` identifier Robinhood issues. The
credential READ fine -- both env vars were present, both were well-formed 44-character base64 --
and every live order still 403'd. "Present" and "well-formed" are the wrong predicate; this
module is where keel asks the questions that actually matter, in order:

Each is only asked when the more specific question above it did not already answer, so an
operator gets the most actionable thing true of the venue rather than the most generic:

1. `NOT_INSTALLED` -- the adapter itself is not in `keel_broker_api.registry.discover_brokers()`.
1b. `NOT_TRADEABLE` -- installed, declares no credential env names, and is not one of
   `CREDENTIALED_VENUES`: `fake` (the deterministic dev venue) and `kraken` (a stub with no
   network path at all). These used to fall through to the trade-scope question and come out
   `NOT_PERMITTED` advising `keel scope attest --trading --venue kraken` -- advice that cannot
   work, because there is no credential behind it to attest ABOUT -- and put a permanent red row
   on the venues card for a venue nobody was going to trade.
2. `NO_CREDENTIALS` -- the adapter IS installed and declares credential env names
   (`DECLARED_CREDENTIAL_ENV`, read with `getattr`, same discipline as
   `brokers.py::_declared`), and every one of them is absent. An adapter that declares NO names
   (`fake`, `kraken` -- they take no credentials) can never land here: there is nothing to be
   missing, so it falls straight through to the trade-scope question below.
2b. `PARTIAL_CREDENTIALS` -- SOME declared names are set and some are not. Half a pair cannot
   authenticate a single request, and before this state existed it walked past every check to
   render "credentials are in place" -- the green check verifying nothing that
   `keel/commands/setup.py`'s OFF_VENUE doctrine names. It also read differently per venue for
   identical input, since robinhood's defect hook called the same thing `MALFORMED_CREDENTIALS`.
3. `MALFORMED_CREDENTIALS` -- credentials are present, and the adapter's OPTIONAL
   `credential_defect(values)` hook (read with `getattr(cls, "credential_defect", None)`, called
   only when present) found something LOCALLY provable wrong with the value. Only
   `RobinhoodAdapter` implements this hook today (the 2026-08-19 incident is exactly the case it
   catches); an adapter with no hook cannot be `MALFORMED_CREDENTIALS` even if it has a genuinely
   broken secret -- that residual is real and is not closed here, only at the one adapter #233
   has a proven failure mode for.
3b. `RECORD_UNREADABLE` -- credentials are fine, a database IS there, and this process could
   not read it. Distinct from "no record" on purpose: collapsing the two let the display ASSERT
   "has never attested" about a row nobody saw, and advise `keel scope attest --trading`, which
   would overwrite a `CONFIRMED` record with a weaker `ATTESTED` one.
4. `NOT_PERMITTED` -- credentials are fine (or not needed), but the venue's trade-scope record
   says rail 20 (`keel_core.trade_scope.VenueTradeScope.may_place_live_entry`,
   `keel/execution/guards.py`) will veto a live entry: no record, read-only, refuted, or
   unverified. This function calls `may_place_live_entry()` rather than re-deriving the state
   machine -- exactly the discipline rail 20 and `doctor.trade_scope_findings` already keep, so
   there is exactly one place to get the trade-scope policy wrong.
5. `READY` -- credentials are fine and the record permits a live entry.

`venue_readiness` is PURE and unit-testable with NO database and NO environment: every input
(the registry mapping, resolved secret values, the `VenueTradeScope | None`) is a plain argument.
`gather_readiness` below is the impure counterpart -- it resolves those inputs for real (the
installed registry, `keel_core.secrets.read_secret`, a best-effort read-only repo open) -- kept
in the SAME module as the pure function it calls, the same shape `keel/commands/doctor.py` keeps
its pure `Finding`-producing functions next to the impure `gather_findings` that wires them to a
real repo and config.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from keel_core.trade_scope import READ_ONLY, TradeScopeState, VenueTradeScope


class VenueReadiness(str, Enum):
    """The five states, in the order `venue_readiness` checks them."""

    NOT_INSTALLED = "not_installed"
    NOT_TRADEABLE = "not_tradeable"
    NO_CREDENTIALS = "no_credentials"
    PARTIAL_CREDENTIALS = "partial_credentials"
    MALFORMED_CREDENTIALS = "malformed_credentials"
    RECORD_UNREADABLE = "record_unreadable"
    NOT_PERMITTED = "not_permitted"
    READY = "ready"


#: The venues keel has a credential/trade-scope story for, independent of whether the adapter
#: happens to be installed in THIS environment -- the only way `NOT_INSTALLED` can ever appear.
#: Matches `keel/commands/credentials.py`'s `KNOWN` tuple's venues (coinbase, alpaca, robinhood)
#: without importing that module (#437 owns it; this module only needs the venue NAMES, not its
#: CLI). `keel brokers list` and `/api/venues` both check the union of this set and whatever
#: `discover_brokers()` actually returns, so a fourth-party adapter someone installs still gets a
#: row and a venue nobody has installed yet still gets `NOT_INSTALLED` rather than silently
#: vanishing from the display.
CREDENTIALED_VENUES: frozenset[str] = frozenset({"coinbase", "alpaca", "robinhood"})


@dataclass(frozen=True)
class VenueReadinessRow:
    """One venue's readiness verdict: the state, a short operator-facing explanation of WHY, and
    the concrete next step (a command to run) where one exists -- `None` only for `READY`, which
    has nothing left to do. Modelled on `doctor.py`'s `Finding`, but not a `Finding`: this module
    has no `doctor`-style OK/WARN/FAIL status and no log-derived `detail`, and forcing this shape
    into that one would mean inventing fields `Finding` carries that readiness has no use for."""

    venue: str
    state: VenueReadiness
    explanation: str
    next_step: str | None


def _attest_fix(venue: str) -> str:
    return f"keel scope attest --trading --venue {venue}"


def venue_readiness(
    venue: str,
    registry: Mapping[str, Any],
    secrets: Mapping[str, str | None],
    record: VenueTradeScope | None,
    *,
    record_unreadable: bool = False,
    current_fingerprint: str | None = None,
) -> VenueReadinessRow:
    """The pure derivation. `registry` is `keel_broker_api.registry.discover_brokers()`'s own
    shape (venue name -> adapter class); `secrets` is every credential env name any adapter might
    declare, already resolved to a value or `None` (presence only -- the caller owns HOW it was
    resolved); `record` is `repo.get_venue_trade_scope(venue)`'s own return, or `None` when no
    repo could be read. No network call, ever -- the heaviest thing this function does is decode
    base64 and derive an Ed25519 public key, both in-process, inside an adapter's own
    `credential_defect` hook.

    `current_fingerprint` (#633) is threaded straight into `record.may_place_live_entry` and
    defaults to `None` -- "current credential unknown", which never withdraws permission. PR1 of
    #633 keeps this display's behaviour UNCHANGED: `gather_readiness` below does not yet resolve
    a real fingerprint, and this function does not yet distinguish "different credential" from
    "never attested" in its `NOT_PERMITTED` explanation. Wiring that distinction in before the
    display can make it correctly would repeat #624's collapse (every failure asserting "never
    attested"); PR2 is where a real fingerprint and the distinguishing text both arrive together.

    `record_unreadable` distinguishes "there is no record" from "there IS a database and this
    process could not read it", which `record=None` alone cannot. Collapsing the two let the
    display ASSERT "has never attested or confirmed a live trade scope" about a deployment whose
    record it never saw, and then advise `keel scope attest --trading` -- advice that would
    overwrite a `CONFIRMED` row with a weaker `ATTESTED` one. Not hypothetical: a WAL database
    whose `-shm` sidecar is absent cannot be opened `mode=ro` at all (SQLite must create that
    file, and `mode=ro` forbids it), which is the shape of every copied or restored backup.
    """
    adapter_cls = registry.get(venue)
    if adapter_cls is None:
        return VenueReadinessRow(
            venue=venue,
            state=VenueReadiness.NOT_INSTALLED,
            explanation=f"no adapter for {venue!r} is installed in this environment",
            next_step=f"uv add keel-broker-{venue}",
        )

    declared = tuple(sorted(getattr(adapter_cls, "DECLARED_CREDENTIAL_ENV", ())))

    if not declared and venue not in CREDENTIALED_VENUES:
        # A venue that presents NO credentials is not one this deployment can trade -- `fake` is
        # the deterministic dev venue and `kraken` is a stub with no network path at all. They
        # were previously falling through to the trade-scope question and coming out
        # `not_permitted` with `fix: keel scope attest --trading --venue kraken`, which is advice
        # that cannot work: there is no credential behind it to attest ABOUT. Answering with no
        # fix at all is the honest shape, and it keeps a permanent red row off the venues card
        # for a venue nobody was ever going to trade.
        return VenueReadinessRow(
            venue=venue,
            state=VenueReadiness.NOT_TRADEABLE,
            explanation=(
                f"{venue} presents no credentials and is not a venue this deployment trades "
                "(a dev or stub adapter)"
            ),
            next_step=None,
        )

    if declared:
        present = {name: secrets.get(name) for name in declared}
        missing = tuple(name for name in declared if not present.get(name))
        if len(missing) == len(declared):
            return VenueReadinessRow(
                venue=venue,
                state=VenueReadiness.NO_CREDENTIALS,
                explanation=f"none of {', '.join(declared)} is set",
                next_step="keel credentials set <name>  (see `keel credentials show`)",
            )
        if missing:
            # HALF a credential pair is not "credentials are in place". Before this branch,
            # `CDP_API_KEY` set with `CDP_API_SECRET` missing walked past every check and
            # rendered "credentials are in place and coinbase's trade-scope record permits a
            # live entry" -- a green check verifying nothing, which is the exact failure
            # `keel/commands/setup.py`'s OFF_VENUE doctrine names. It also read differently per
            # venue for identical input: robinhood's defect hook called the same state
            # `malformed_credentials`, labelling a MISSING credential malformed.
            return VenueReadinessRow(
                venue=venue,
                state=VenueReadiness.PARTIAL_CREDENTIALS,
                explanation=(
                    f"{', '.join(missing)} is not set, but the rest of {venue}'s credentials are "
                    "-- every request will be built from an incomplete pair"
                ),
                next_step=f"keel credentials set {missing[0]}",
            )

        # Only Robinhood declares this hook today (#233 PR4): an adapter with none cannot be
        # found MALFORMED_CREDENTIALS even on a genuinely broken value -- see the module
        # docstring's point 3. A PARTIALLY present pair (one of `declared` set, the other not)
        # that reaches an adapter with NO hook also falls through here uncaught, for the same
        # reason: nothing declared a way to prove it wrong.
        defect_check = getattr(adapter_cls, "credential_defect", None)
        if defect_check is not None:
            defect = defect_check(present)
            if defect:
                return VenueReadinessRow(
                    venue=venue,
                    state=VenueReadiness.MALFORMED_CREDENTIALS,
                    explanation=defect,
                    next_step=(
                        "fix the value (`keel credentials show` names the source), then "
                        "`keel credentials set <name>` to replace it"
                    ),
                )
    # else: this adapter declares NO credential env names -- it needs none (`fake`, `kraken`),
    # so it can never be NO_CREDENTIALS or MALFORMED_CREDENTIALS. Fall through to trade scope.

    if record_unreadable:
        # Deliberately BEFORE both the READY and the NOT_PERMITTED branches, and carrying no
        # `keel scope attest` advice. Every other branch below states something about the
        # record's CONTENTS; this one is the admission that the contents are unknown, and the
        # attest command is precisely the wrong suggestion -- it would overwrite whatever the
        # unread row actually says, including a `CONFIRMED` one, with a weaker claim.
        return VenueReadinessRow(
            venue=venue,
            state=VenueReadiness.RECORD_UNREADABLE,
            explanation=(
                f"this deployment's database could not be read, so {venue}'s trade-scope record "
                "is unknown -- this is NOT a statement that nothing is attested"
            ),
            next_step="keel doctor  (check the database path and that it is readable)",
        )

    if record is not None and record.may_place_live_entry(current_fingerprint):
        return VenueReadinessRow(
            venue=venue,
            state=VenueReadiness.READY,
            explanation=(
                f"credentials are in place and {venue}'s trade-scope record permits a live entry"
            ),
            next_step=None,
        )

    if record is None:
        explanation = (
            f"{venue} has never attested or confirmed a live trade scope for this credential"
        )
    elif record.state is TradeScopeState.REFUTED:
        reason = f" ({record.refuted_reason})" if record.refuted_reason else ""
        explanation = f"{venue} refused a live placement on this credential{reason}"
    elif record.state is TradeScopeState.ATTESTED and record.attested_scope == READ_ONLY:
        explanation = f"{venue}'s credential is attested read-only"
    else:
        # UNVERIFIED, or any other combination `may_place_live_entry()` fails closed on.
        explanation = f"{venue} has a trade-scope row rail 20 will still veto a live entry on"

    return VenueReadinessRow(
        venue=venue,
        state=VenueReadiness.NOT_PERMITTED,
        explanation=explanation,
        next_step=_attest_fix(venue),
    )


def gather_readiness(
    registry: Mapping[str, Any], *, db_path: str | None
) -> list[VenueReadinessRow]:
    """The impure counterpart: resolves every input `venue_readiness` needs for real, then calls
    it once per venue -- the ONE place both `keel brokers list` and `/api/venues` gather inputs,
    so they cannot diverge on HOW a record or a secret is resolved (mirrors
    `doctor.gather_findings` sitting next to its pure `Finding`-producing functions).

    `db_path` is READ-ONLY and best-effort: `None` means "no usable deployment database" (the
    caller's call -- `keel brokers list` when nothing at that path exists yet, `/api/venues` when
    `DeploymentState.has_usable_database` is false) and skips opening anything at all. When a
    path IS given, this opens it WITHOUT migrating (never creates or alters schema) and degrades
    every venue's record to `None` on any failure -- a missing table, a locked file, a schema
    older than the trade-scope migration -- which is exactly rail 20's own "unknown" and never a
    reason to fail the display.
    """
    from keel_core.secrets import read_secret

    names: set[str] = set()
    for adapter_cls in registry.values():
        names.update(getattr(adapter_cls, "DECLARED_CREDENTIAL_ENV", ()))
    secrets = {name: read_secret(name).value for name in names}

    venues = sorted(set(registry) | CREDENTIALED_VENUES)
    rows: list[VenueReadinessRow] = []
    for venue in venues:
        record, unreadable = _read_only_trade_scope(db_path, venue)
        rows.append(
            venue_readiness(venue, registry, secrets, record, record_unreadable=unreadable)
        )
    return rows


def _read_only_trade_scope(db_path: str | None, venue: str) -> tuple[VenueTradeScope | None, bool]:
    """`(record, unreadable)` -- never raises, never migrates, never creates a file.

    The BOOLEAN is the point of the tuple. Returning a bare `None` for both "there is no record"
    and "there is a database this process could not read" let the display assert the former
    about the latter, and then advise `keel scope attest --trading` -- which would overwrite a
    `CONFIRMED` row with a weaker `ATTESTED` one, on a deployment whose record was fine all
    along and merely unread.

    ⚠️ **Not `keel.data.db.connect`, and that is the whole point of this function.** `connect`
    is the read-WRITE opener: `sqlite3.connect` happily CREATES a file it cannot find, and
    `connect` then sets `journal_mode=WAL` on it, so asking a display command about a venue on a
    machine with no deployment would leave `keel.db`, `keel.db-wal` and `keel.db-shm` behind --
    an empty database that later commands would then find and believe in. `keel brokers list` is
    a read-only informational command and must litter nothing.

    So this uses the `mode=ro` URI shape #610 promoted to a seam for exactly this hazard
    (`keel/commands/_common.py::_open_repo_ro`), with the same explicit existence check in front
    of it, because `mode=ro`'s own refusal is an `OperationalError` and this path wants an
    ANSWER, not an exception.

    Where it deliberately differs from that seam: `_open_repo_ro` REFUSES a database stamped
    below this binary's `SCHEMA_VERSION`, because a research command must not answer a question
    from a schema it was never tested against. A readiness DISPLAY must not refuse -- a
    deployment whose database predates #233's trade-scope migration has no record to read, which
    is exactly rail 20's own "unknown", and `NOT_PERMITTED` is the honest thing to show rather
    than a crashed command.
    """
    if db_path is None or not Path(db_path).exists():
        # NOT "unreadable". There is no database, so "no record" is a true statement about this
        # deployment rather than an admission of ignorance -- the caller passes `None` exactly
        # when it has already established that no file is there.
        return None, False
    conn = None
    try:
        from keel.data.repository import Repository

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return Repository(conn).get_venue_trade_scope(venue), False
    except Exception:
        # A file IS there and this process could not read it: a schema older than #233's
        # migration, a permissions problem, or -- the case that motivated separating these two
        # answers -- a WAL database whose `-shm` sidecar is absent, which `mode=ro` cannot open
        # because SQLite would have to CREATE that file. Every copied or restored backup has
        # that shape. Reported as unknown, never as "nothing is attested".
        return None, True
    finally:
        if conn is not None:
            conn.close()


__all__ = [
    "CREDENTIALED_VENUES",
    "VenueReadiness",
    "VenueReadinessRow",
    "gather_readiness",
    "venue_readiness",
]
