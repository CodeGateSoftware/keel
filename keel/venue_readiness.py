"""Five venue-readiness states, derived STRICTLY OFFLINE (#233 PR4): one source of truth for
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

1. `NOT_INSTALLED` -- the adapter itself is not in `keel_broker_api.registry.discover_brokers()`.
2. `NO_CREDENTIALS` -- the adapter IS installed and declares credential env names
   (`DECLARED_CREDENTIAL_ENV`, read with `getattr`, same discipline as
   `brokers.py::_declared`), and every one of them is absent. An adapter that declares NO names
   (`fake`, `kraken` -- they take no credentials) can never land here: there is nothing to be
   missing, so it falls straight through to the trade-scope question below.
3. `MALFORMED_CREDENTIALS` -- credentials are present, and the adapter's OPTIONAL
   `credential_defect(values)` hook (read with `getattr(cls, "credential_defect", None)`, called
   only when present) found something LOCALLY provable wrong with the value. Only
   `RobinhoodAdapter` implements this hook today (the 2026-08-19 incident is exactly the case it
   catches); an adapter with no hook cannot be `MALFORMED_CREDENTIALS` even if it has a genuinely
   broken secret -- that residual is real and is not closed here, only at the one adapter #233
   has a proven failure mode for.
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
    NO_CREDENTIALS = "no_credentials"
    MALFORMED_CREDENTIALS = "malformed_credentials"
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
) -> VenueReadinessRow:
    """The pure derivation. `registry` is `keel_broker_api.registry.discover_brokers()`'s own
    shape (venue name -> adapter class); `secrets` is every credential env name any adapter might
    declare, already resolved to a value or `None` (presence only -- the caller owns HOW it was
    resolved); `record` is `repo.get_venue_trade_scope(venue)`'s own return, or `None` when no
    repo could be read. No network call, ever -- the heaviest thing this function does is decode
    base64 and derive an Ed25519 public key, both in-process, inside an adapter's own
    `credential_defect` hook.
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

    if declared:
        present = {name: secrets.get(name) for name in declared}
        if all(not value for value in present.values()):
            return VenueReadinessRow(
                venue=venue,
                state=VenueReadiness.NO_CREDENTIALS,
                explanation=f"none of {', '.join(declared)} is set",
                next_step="keel credentials set <name>  (see `keel credentials show`)",
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

    if record is not None and record.may_place_live_entry():
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
    return [
        venue_readiness(venue, registry, secrets, _read_only_trade_scope(db_path, venue))
        for venue in venues
    ]


def _read_only_trade_scope(db_path: str | None, venue: str) -> VenueTradeScope | None:
    """`repo.get_venue_trade_scope(venue)`, or `None` on anything that goes wrong -- never raises,
    never migrates, and never creates a file that did not already exist.

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
        return None
    conn = None
    try:
        from keel.data.repository import Repository

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return Repository(conn).get_venue_trade_scope(venue)
    except Exception:
        return None
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
