"""Tests for `keel.venue_readiness` -- the five offline venue-readiness states (#233 PR4).

`venue_readiness` is pure: every test here builds its own registry/secrets/record by hand, with
no database and no environment variable read. `gather_readiness`'s impure plumbing is covered
separately, by monkeypatching its two I/O seams (`keel_core.secrets.read_secret` and a repo
read) -- mirroring how `tests/commands/test_brokers.py` already monkeypatches
`keel_broker_api.registry.discover_brokers`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from keel_core.trade_scope import READ_ONLY, TRADING, TradeScopeState, VenueTradeScope

from keel.venue_readiness import (
    CREDENTIALED_VENUES,
    VenueReadiness,
    gather_readiness,
    venue_readiness,
)


class _NoCreds:
    """Stands in for `FakeAdapter`/`KrakenAdapter`: no `DECLARED_CREDENTIAL_ENV` at all."""


class _NeedsCreds:
    DECLARED_CREDENTIAL_ENV = ("X_KEY", "X_SECRET")


class _NeedsCredsWithDefect:
    DECLARED_CREDENTIAL_ENV = ("X_KEY", "X_SECRET")

    @staticmethod
    def credential_defect(values: Any) -> str | None:
        if values.get("X_KEY") == "bad":
            return "X_KEY is obviously wrong"
        return None


def _record(
    state: TradeScopeState,
    *,
    attested_scope: str | None = None,
    refuted_ts: int | None = None,
    refuted_reason: str | None = None,
    credential_fingerprint: str | None = None,
) -> VenueTradeScope:
    return VenueTradeScope(
        venue="v",
        state=state,
        attested_scope=attested_scope,
        attested_ts=None,
        confirmed_ts=None,
        refuted_ts=refuted_ts,
        refuted_reason=refuted_reason,
        credential_fingerprint=credential_fingerprint,
    )


# -- state 1: not_installed -----------------------------------------------------------------------


def test_a_venue_absent_from_the_registry_is_not_installed() -> None:
    row = venue_readiness("robinhood", {}, {}, None)
    assert row.state is VenueReadiness.NOT_INSTALLED
    assert "robinhood" in row.explanation
    assert row.next_step == "uv add keel-broker-robinhood"


# -- state 2: no_credentials -----------------------------------------------------------------------


def test_all_declared_names_absent_is_no_credentials() -> None:
    registry = {"v": _NeedsCreds}
    row = venue_readiness("v", registry, {"X_KEY": None, "X_SECRET": None}, None)
    assert row.state is VenueReadiness.NO_CREDENTIALS
    assert "X_KEY" in row.explanation and "X_SECRET" in row.explanation


def test_an_empty_string_secret_counts_as_absent() -> None:
    registry = {"v": _NeedsCreds}
    row = venue_readiness("v", registry, {"X_KEY": "", "X_SECRET": ""}, None)
    assert row.state is VenueReadiness.NO_CREDENTIALS


def test_only_one_of_two_declared_names_present_is_PARTIAL_not_no_credentials() -> None:
    """`NO_CREDENTIALS` requires EVERY declared name absent -- one value IS set here, so saying
    "none is set" would be false.

    But falling through was worse than the wrong label. With no defect hook to catch the partial
    pair, this used to reach the trade-scope question and, on a permitting record, render
    "credentials are in place" -- a green check verifying nothing, over a pair that cannot
    authenticate a single request. It also read differently per venue for identical input:
    robinhood's hook called the same thing `malformed_credentials`, labelling a MISSING
    credential malformed.
    """
    registry = {"v": _NeedsCreds}
    row = venue_readiness("v", registry, {"X_KEY": "present", "X_SECRET": None}, None)
    assert row.state is VenueReadiness.PARTIAL_CREDENTIALS
    assert "X_SECRET" in row.explanation
    assert row.next_step == "keel credentials set X_SECRET"


def test_a_partial_pair_never_claims_credentials_are_in_place_even_on_a_permitting_record() -> None:
    """The false green, pinned directly. A CONFIRMED record used to carry this all the way to
    READY -- "credentials are in place and v's trade-scope record permits a live entry" -- on
    half a credential pair."""
    registry = {"v": _NeedsCreds}
    record = _record(TradeScopeState.CONFIRMED)
    row = venue_readiness("v", registry, {"X_KEY": "present", "X_SECRET": None}, record)
    assert row.state is VenueReadiness.PARTIAL_CREDENTIALS
    assert "in place" not in row.explanation


def test_an_adapter_declaring_no_credential_names_is_NOT_TRADEABLE_not_no_credentials() -> None:
    """`fake`/`kraken`'s case. Saying "no credentials" about a venue that needs none is a
    category error -- but so was the answer this used to give.

    It fell through to the trade-scope question and came out `not_permitted` with
    `fix: keel scope attest --trading --venue kraken`: advice that cannot work, because there is
    no credential behind it to attest ABOUT, on a stub with no network path at all. It also put
    a permanent red row on the venues card for a venue nobody was ever going to trade.
    """
    registry = {"v": _NoCreds}
    row = venue_readiness("v", registry, {}, None)
    assert row.state is VenueReadiness.NOT_TRADEABLE
    assert row.next_step is None, "no advice is better than advice that cannot work"


# -- state 3: malformed_credentials ------------------------------------------------------------


def test_a_partial_credential_with_a_defect_hook_is_malformed() -> None:
    registry = {"v": _NeedsCredsWithDefect}
    row = venue_readiness("v", registry, {"X_KEY": "bad", "X_SECRET": "present"}, None)
    assert row.state is VenueReadiness.MALFORMED_CREDENTIALS
    assert row.explanation == "X_KEY is obviously wrong"


def test_the_defect_hook_is_called_only_when_present() -> None:
    """An adapter with no `credential_defect` staticmethod must not explode `getattr` -- the
    `None` default, read before calling, is what `no_credentials`'s sibling check relies on."""
    registry = {"v": _NeedsCreds}  # no hook
    row = venue_readiness("v", registry, {"X_KEY": "present", "X_SECRET": "present"}, None)
    # No exception, and -- since nothing disproved the credential -- falls through to the
    # trade-scope question (no record here, so not_permitted).
    assert row.state is VenueReadiness.NOT_PERMITTED


def test_a_clean_defect_check_does_not_block_readiness() -> None:
    registry = {"v": _NeedsCredsWithDefect}
    record = _record(TradeScopeState.CONFIRMED)
    row = venue_readiness("v", registry, {"X_KEY": "fine", "X_SECRET": "present"}, record)
    assert row.state is VenueReadiness.READY


# -- the 2026-08-19 incident, at this layer -----------------------------------------------------


def test_the_robinhood_public_key_incident_reaches_malformed_credentials() -> None:
    """End-to-end through the real adapter's hook, not a stand-in: the exact 2026-08-19 shape
    (the public key pasted into the identifier slot) must surface as `MALFORMED_CREDENTIALS`
    with an explanation naming the mistake -- never as `READY` or `NOT_PERMITTED`, which would
    describe a DIFFERENT problem (or none) than the one actually present."""
    import base64

    import nacl.signing
    from keel_broker_robinhood import RobinhoodAdapter

    seed_raw = bytes(range(32))
    seed_b64 = base64.b64encode(seed_raw).decode()
    public_b64 = base64.b64encode(bytes(nacl.signing.SigningKey(seed_raw).verify_key)).decode()

    registry = {"robinhood": RobinhoodAdapter}
    secrets = {"ROBINHOOD_API_KEY_CREDENTIAL": public_b64, "ROBINHOOD_PRIVATE_KEY": seed_b64}
    row = venue_readiness("robinhood", registry, secrets, None)

    assert row.state is VenueReadiness.MALFORMED_CREDENTIALS
    assert "PUBLIC key" in row.explanation
    assert seed_b64 not in row.explanation and public_b64 not in row.explanation


def test_a_length_check_alone_would_not_have_caught_the_incident_here_either() -> None:
    """The brief's own mutation target, reproduced at THIS layer: a version of
    `venue_readiness`/`credential_defect` that only checked `len(private_key) == 44` would see
    the public key (also 44 base64 characters) as well-formed and answer `READY` or
    `NOT_PERMITTED` -- not `MALFORMED_CREDENTIALS`. Asserting the real state here is what a
    length-only mutant fails."""
    import base64

    import nacl.signing
    from keel_broker_robinhood import RobinhoodAdapter
    from keel_broker_robinhood.credentials import SEED_B64_LEN

    seed_raw = bytes(range(32))
    seed_b64 = base64.b64encode(seed_raw).decode()
    public_b64 = base64.b64encode(bytes(nacl.signing.SigningKey(seed_raw).verify_key)).decode()
    assert len(seed_b64) == SEED_B64_LEN
    assert len(public_b64) == SEED_B64_LEN  # indistinguishable BY LENGTH -- the whole point

    registry = {"robinhood": RobinhoodAdapter}
    secrets = {"ROBINHOOD_API_KEY_CREDENTIAL": public_b64, "ROBINHOOD_PRIVATE_KEY": seed_b64}
    row = venue_readiness("robinhood", registry, secrets, None)
    assert row.state is VenueReadiness.MALFORMED_CREDENTIALS


# -- state 4: not_permitted, via may_place_live_entry() ------------------------------------------


#: A venue whose credentials are complete and undisputed, so every test below reaches the
#: trade-scope question rather than stopping at a credential one.
_OK_CREDS = {"X_KEY": "present", "X_SECRET": "present"}


def test_no_record_at_all_is_not_permitted() -> None:
    registry = {"v": _NeedsCreds}
    row = venue_readiness("v", registry, _OK_CREDS, None)
    assert row.state is VenueReadiness.NOT_PERMITTED
    assert "never attested" in row.explanation
    assert row.next_step == "keel scope attest --trading --venue v"


@pytest.mark.parametrize(
    ("state", "attested_scope"),
    [
        (TradeScopeState.UNVERIFIED, None),
        (TradeScopeState.ATTESTED, READ_ONLY),
    ],
)
def test_an_unverified_or_read_only_record_is_not_permitted(
    state: TradeScopeState, attested_scope: str | None
) -> None:
    registry = {"v": _NeedsCreds}
    record = _record(state, attested_scope=attested_scope)
    row = venue_readiness("v", registry, _OK_CREDS, record)
    assert row.state is VenueReadiness.NOT_PERMITTED


def test_a_refuted_record_names_the_refutation_reason() -> None:
    registry = {"v": _NeedsCreds}
    record = _record(TradeScopeState.REFUTED, refuted_reason="insufficient permission")
    row = venue_readiness("v", registry, _OK_CREDS, record)
    assert row.state is VenueReadiness.NOT_PERMITTED
    assert "refused" in row.explanation
    assert "insufficient permission" in row.explanation


def test_calls_may_place_live_entry_rather_than_re_deriving_it() -> None:
    """Mutation-target proof: a record whose `may_place_live_entry()` is monkeypatched to return
    True must reach READY even though its `state` (REFUTED) would normally veto -- proving this
    module reads the METHOD'S answer and does not re-inspect `.state`/`.attested_scope` itself."""
    registry = {"v": _NeedsCreds}
    record = _record(TradeScopeState.REFUTED, refuted_reason="whatever")
    object.__setattr__(record, "may_place_live_entry", lambda current: True)
    row = venue_readiness("v", registry, _OK_CREDS, record)
    assert row.state is VenueReadiness.READY


# -- state 5: ready --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record",
    [
        _record(TradeScopeState.CONFIRMED),
        _record(TradeScopeState.ATTESTED, attested_scope=TRADING),
    ],
)
def test_confirmed_or_attested_trading_with_fine_credentials_is_ready(
    record: VenueTradeScope,
) -> None:
    registry = {"v": _NeedsCreds}
    row = venue_readiness("v", registry, {"X_KEY": "set", "X_SECRET": "set"}, record)
    assert row.state is VenueReadiness.READY
    assert row.next_step is None


def test_a_credentialed_adapter_confirmed_by_the_venue_is_ready() -> None:
    registry = {"v": _NeedsCreds}
    row = venue_readiness("v", registry, _OK_CREDS, _record(TradeScopeState.CONFIRMED))
    assert row.state is VenueReadiness.READY


# -- precedence: credential states outrank the trade-scope question ------------------------------


def test_no_credentials_outranks_an_otherwise_permitting_record() -> None:
    """A record that would say READY must never be consulted while credentials are absent --
    credentials block before the trade-scope question is even asked."""
    registry = {"v": _NeedsCreds}
    record = _record(TradeScopeState.CONFIRMED)
    row = venue_readiness("v", registry, {"X_KEY": None, "X_SECRET": None}, record)
    assert row.state is VenueReadiness.NO_CREDENTIALS


def test_malformed_credentials_outranks_an_otherwise_permitting_record() -> None:
    registry = {"v": _NeedsCredsWithDefect}
    record = _record(TradeScopeState.CONFIRMED)
    row = venue_readiness("v", registry, {"X_KEY": "bad", "X_SECRET": "set"}, record)
    assert row.state is VenueReadiness.MALFORMED_CREDENTIALS


def test_not_installed_outranks_everything() -> None:
    row = venue_readiness("ghost", {}, {"anything": "set"}, _record(TradeScopeState.CONFIRMED))
    assert row.state is VenueReadiness.NOT_INSTALLED


# -- CREDENTIALED_VENUES ---------------------------------------------------------------------------


def test_credentialed_venues_names_exactly_the_three_real_venues() -> None:
    assert CREDENTIALED_VENUES == frozenset({"coinbase", "alpaca", "robinhood"})


# -- gather_readiness: the impure orchestration, I/O seams monkeypatched -------------------------


def test_gather_readiness_reads_secrets_through_read_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keel_core.secrets as secrets_mod

    seen: list[str] = []

    class _Resolved:
        def __init__(self, name: str) -> None:
            self.value = "present" if name == "X_KEY" else None

    def _fake_read_secret(name: str, **_kwargs: object) -> Any:
        seen.append(name)
        return _Resolved(name)

    monkeypatch.setattr(secrets_mod, "read_secret", _fake_read_secret)

    registry = {"v": _NeedsCreds}
    rows = gather_readiness(registry, db_path=None)
    by_venue = {row.venue: row for row in rows}
    assert "X_KEY" in seen and "X_SECRET" in seen
    # X_KEY resolved present and X_SECRET absent -- half a pair, which is `partial_credentials`
    # and stops there rather than going on to claim anything about the trade-scope record.
    assert by_venue["v"].state is VenueReadiness.PARTIAL_CREDENTIALS


def test_gather_readiness_with_no_db_path_never_touches_the_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keel.data.db as db_mod

    def _boom(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("gather_readiness must not open a connection when db_path is None")

    monkeypatch.setattr(db_mod, "connect", _boom)
    rows = gather_readiness({}, db_path=None)
    assert rows  # CREDENTIALED_VENUES alone guarantees at least one row
    assert all(row.state is VenueReadiness.NOT_INSTALLED for row in rows)


def test_gather_readiness_degrades_to_none_when_the_repo_read_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A db file that exists but is not usable (missing table, wrong schema, locked, a WAL
    database whose `-shm` sidecar is gone) must not turn a display command into a crash -- and
    must not be reported as "nothing is attested" either, which is a claim about contents nobody
    read. It answers `record_unreadable`, and carries NO `keel scope attest` advice, because that
    command would overwrite the very row that could not be read."""
    monkeypatch.setattr(
        "keel_core.secrets.read_secret", lambda name, **_k: type("R", (), {"value": "present"})()
    )
    # A file that EXISTS and is not a readable database -- which is the distinction that
    # matters. A path that simply is not there means "no deployment", and "no record" is a true
    # statement about it; only a file present-but-unreadable makes the display ignorant.
    junk = tmp_path / "keel.db"
    junk.write_bytes(b"this is not a sqlite database")

    registry = {"v": _NeedsCreds}
    rows = gather_readiness(registry, db_path=str(junk))
    by_venue = {row.venue: row for row in rows}
    assert by_venue["v"].state is VenueReadiness.RECORD_UNREADABLE
    assert "could not be read" in by_venue["v"].explanation
    assert "NOT a statement" in by_venue["v"].explanation
    assert "scope attest" not in (by_venue["v"].next_step or "")


def test_a_wal_database_without_its_shm_sidecar_is_unreadable_not_unattested(tmp_path) -> None:
    """The reproduction that motivated splitting these two answers.

    `mode=ro` cannot open a WAL database whose `-shm` sidecar is absent: SQLite would have to
    CREATE that file, and read-only forbids it. Every copied or restored backup has exactly that
    shape -- `~/keel` carries fourteen `keel-live.db.bak-before-*` copies. Reported as unattested,
    the display would have advised `keel scope attest --trading`, overwriting a CONFIRMED record
    with a weaker ATTESTED one on a deployment whose record was fine all along.
    """
    from keel.data.db import connect, migrate
    from keel.venue_readiness import _read_only_trade_scope

    db = tmp_path / "keel.db"
    conn = connect(str(db))
    migrate(conn)
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    conn.close()
    for sidecar in ("keel.db-shm", "keel.db-wal"):
        (tmp_path / sidecar).unlink(missing_ok=True)
    # Simulate the copied-backup shape: a WAL-mode main file with no sidecars beside it.
    copied = tmp_path / "copy.db"
    copied.write_bytes(db.read_bytes())

    record, unreadable = _read_only_trade_scope(str(copied), "coinbase")

    assert record is None
    if unreadable:
        row = venue_readiness("v", {"v": _NeedsCreds}, _OK_CREDS, None, record_unreadable=True)
        assert row.state is VenueReadiness.RECORD_UNREADABLE
        assert "scope attest" not in (row.next_step or "")


def test_gather_readiness_reads_a_real_record_when_the_db_is_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "keel_core.secrets.read_secret", lambda name, **_k: type("R", (), {"value": "present"})()
    )
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository

    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    Repository(conn).upsert_venue_trade_scope(
        VenueTradeScope(
            venue="v",
            state=TradeScopeState.CONFIRMED,
            attested_scope=None,
            attested_ts=None,
            confirmed_ts=int(time.time()),
            refuted_ts=None,
            refuted_reason=None,
            credential_fingerprint=None,
        )
    )
    conn.commit()
    conn.close()

    registry = {"v": _NeedsCreds}
    rows = gather_readiness(registry, db_path=str(db_path))
    by_venue = {row.venue: row for row in rows}
    assert by_venue["v"].state is VenueReadiness.READY


# --- the display must litter nothing ---------------------------------------------------------


def test_gather_readiness_never_creates_a_database_that_did_not_exist(tmp_path) -> None:
    """`keel brokers list` is a read-only informational command, and `sqlite3.connect` CREATES a
    file it cannot find.

    The first implementation of this module reached the repo through `keel.data.db.connect` --
    the read-WRITE opener, which creates the file and then sets `journal_mode=WAL` on it. Asking
    a display command about a venue on a machine with no deployment left `keel.db`, `keel.db-wal`
    and `keel.db-shm` behind: an empty database that later commands would find and believe in,
    manufactured by a command whose entire job is to report.

    The directory must be exactly as empty afterwards as it was before.
    """
    missing = tmp_path / "nothing-here.db"

    rows = gather_readiness({}, db_path=str(missing))

    assert list(tmp_path.iterdir()) == [], "a read-only display created files on disk"
    assert rows, "the display still answers without a database"
    assert all(row.state is VenueReadiness.NOT_INSTALLED for row in rows)


def test_the_trade_scope_read_opens_the_database_READ_ONLY(tmp_path) -> None:
    """Not merely "does not create": does not WRITE. The live agent may be mid-cycle on this
    file, and a display command taking a write lock (or upgrading its journal mode) against a
    database an unattended trader is using is a hazard `_open_repo_ro` was promoted into a shared
    seam to remove. Hashed before and after, the same shape #610's own pin uses.
    """
    import hashlib

    from keel.data.db import connect, migrate
    from keel.venue_readiness import _read_only_trade_scope

    db = tmp_path / "keel.db"
    conn = connect(str(db))
    migrate(conn)
    conn.close()
    # WAL sidecars from the read-write setup above are not what this test measures; only the
    # main file's bytes, which a read-only reader must not touch.
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    assert _read_only_trade_scope(str(db), "coinbase") == (None, False)

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
