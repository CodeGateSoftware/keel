"""What a deployment still needs before it can trade -- observed, never assumed (#437, D4).

First run today is roughly ten CLI invocations plus hand-edited YAML (`docs/go-live-runbook.md`),
and a non-technical user on a machine with no terminal cannot perform any of them. The obvious
response is a wizard that does the ten steps for you. That response is wrong for most of them,
and this module exists to say precisely which.

**Three kinds of step, and only one of them can be automated.**

* `MECHANICAL` -- writing a config, creating and migrating a database, seeding the rule library,
  fetching the first candles. A wizard may do these outright: there is no judgement in them and
  no way for a machine to get them wrong in a way a human would have got right.

* `JUDGEMENT` -- every Shariah attestation, and every promotion. `keel assets attest` requires a
  human-supplied classification WITH a `source`, and an unsourced attestation is refused exactly
  like a missing one (`compliance/screen.py`). A wizard may collect and record these. It must
  never default them, pre-tick them, or supply a plausible source -- an attestation the operator
  did not actually make is worse than no attestation, because the rails then stop asking.

* `OFF_VENUE` -- disabling Coinbase USDC Rewards and Alpaca's stock-lending/cash-sweep interest.
  That interest is riba (KB §56.3) and it accrues with **no order placed**, so no rail can see
  it. It is changed in the venue's own dashboard, and the venue's API does not expose enrolment
  status. keel can show the checklist and record that you say you did it; it cannot verify, and
  the operator runbook is explicit that a green check verifying nothing is worse than an honest
  manual step, because it turns an open risk into a false assurance. So these steps are never
  reported as `done` here -- only as acknowledged.

**Everything here is a READ.** `inspect` opens the database if there is one, reads config if
there is one, and answers what it found. It creates nothing, writes nothing and migrates nothing,
so it is safe to call on every page load and safe to call against a deployment an agent is
mid-cycle on. The step list is the ONE description of the ceremony, so the CLI, the browser view
and any later wizard cannot drift into three accounts of what a deployment needs.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import click

from keel.commands._common import default_config_path, default_db_path

#: The exact schema names this module reads. Named constants rather than string literals for a
#: reason that is not style: I wrote `"subscriptions"` and `"state"` here first, and both are
#: wrong -- the tables are `broker_subscriptions` and `agent_state`. Nothing failed. The checklist
#: simply reported an attested subscription and an attested withdrawal capability as MISSING,
#: which is the worst failure this module can have: it sends an operator to redo a step they have
#: already done, and it is invisible because a missing thing looks exactly like a thing that was
#: never there.
#:
#: `tests/commands/test_setup.py` now pins every one of these against a freshly migrated
#: database, so a rename fails a test instead of quietly turning a completed step incomplete.
RULES_TABLE = "rules"
CANDLES_TABLE = "candles"
ASSET_ATTESTATIONS_TABLE = "asset_attestations"
SUBSCRIPTIONS_TABLE = "broker_subscriptions"
STATE_TABLE = "agent_state"
#: Rail 20's record (#233, merged the morning this step was added). Verified against the v14
#: migration in `keel/data/db.py` -- an operator who completes `keel scope attest --trading`
#: is still vetoed on their first live entry by a checklist that never named the command, and
#: that is exactly the invisible failure this constants block exists to prevent: a wrong name
#: here would make a COMPLETED step report as missing, not raise.
VENUE_TRADE_SCOPES_TABLE = "venue_trade_scopes"
WITHDRAWALS_STATE_KEY = "withdrawals_enabled"
#: The venue-interest acknowledgement (#437 D4 part 2) -- who said they turned it off, and when.
#: Never read by any rail and never makes `venue_interest_off.done` `True`; see the `ACTIONS`
#: comment block for why recording the statement is still worth doing.
VENUE_INTEREST_ACK_BY_KEY = "venue_interest_off_acknowledged_by"
VENUE_INTEREST_ACK_AT_KEY = "venue_interest_off_acknowledged_at"

#: Every table name above, for the pin to iterate.
READ_TABLES: tuple[str, ...] = (
    RULES_TABLE,
    CANDLES_TABLE,
    ASSET_ATTESTATIONS_TABLE,
    SUBSCRIPTIONS_TABLE,
    STATE_TABLE,
    VENUE_TRADE_SCOPES_TABLE,
)


class StepKind(str, Enum):
    """Who can perform a step -- which is what decides whether a wizard may touch it.

    `OPERATOR_INPUT` was split out of `JUDGEMENT` when the credential step arrived (#437), and the
    distinction is worth the fourth member. A JUDGEMENT is a DECISION only a human may make -- a
    Shariah classification, a promotion -- and a wizard that made one would be making a compliance
    ruling on the operator's behalf. An OPERATOR_INPUT is a FACT only the operator possesses: an
    API key. A wizard may record one and cannot possibly invent one, so it is safe to offer as a
    form in a way a judgement is not.

    Collapsing the two would have forced one of two bad outcomes: either the browser could record
    an attestation (wrong), or it could never accept a credential (which leaves a desktop user
    with no way to configure keel at all, since they have no terminal to type one in)."""

    MECHANICAL = "mechanical"
    OPERATOR_INPUT = "operator_input"
    JUDGEMENT = "judgement"
    OFF_VENUE = "off_venue"


class Stage(str, Enum):
    """How far a step is needed for.

    `PAPER` is the whole of what a first-run wizard should aim at: a deployment that evaluates
    rules against real market data and places nothing. `LIVE` is everything the go-live runbook
    adds on top, and it is deliberately a separate stage rather than more items on one list --
    a user who has finished the paper stage has finished something, and should be told so."""

    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    kind: StepKind
    stage: Stage
    #: Why the step exists at all. For `OFF_VENUE` and `JUDGEMENT`, why it cannot be automated.
    why: str
    #: What the operator does. A CLI command, or an instruction at the venue.
    how: str


@dataclass(frozen=True)
class StepState:
    step: Step
    #: Observed, never assumed. `None` means "could not be determined" -- which is NOT `False`:
    #: an unreadable database is not an unseeded one, and reporting it as incomplete would send
    #: an operator to re-run a step that may already be done.
    done: bool | None
    #: What was actually observed, in the operator's words rather than a boolean.
    detail: str
    #: An `OFF_VENUE` acknowledgement's provenance -- who said so, and when. `None`/`""` means
    #: nobody has. Defaulted so `payload._step_payload`'s explicit field list needs no change:
    #: the acknowledgement reaches the browser through `detail`'s PROSE, not through a new
    #: payload key, which is what keeps this addition invisible to `keel/web/payload.py`.
    #:
    #: Deliberately NOT read by `StepState.blocking` or anywhere `done` is computed: recording an
    #: acknowledgement must never make `done` `True` for an `OFF_VENUE` step -- see the long
    #: comment above `ACTIONS` for why that invariant is the one this module defends hardest.
    #: Only `DeploymentState.live_blockers` reads these, and only for `OFF_VENUE` steps.
    acknowledged_at: int | None = None
    acknowledged_by: str = ""

    @property
    def blocking(self) -> bool:
        return self.done is not True


#: THE CEREMONY, in the order `docs/go-live-runbook.md` performs it. One description, three
#: front-ends.
STEPS: tuple[Step, ...] = (
    Step(
        key="config",
        title="A config file",
        kind=StepKind.MECHANICAL,
        stage=Stage.PAPER,
        why="Nothing runs without one: the allowlist, the caps and the trading mode all live here.",
        how="keel init-config  (writes the paper template, which places nothing)",
    ),
    Step(
        key="database",
        title="A database, at the current schema",
        kind=StepKind.MECHANICAL,
        stage=Stage.PAPER,
        why=(
            "Rules, orders, attestations and the equity ledger all live in it. Migrations are "
            "idempotent, so applying them costs nothing when there is nothing to apply."
        ),
        how="keel migrate",
    ),
    Step(
        key="rules",
        title="The rule library, seeded",
        kind=StepKind.MECHANICAL,
        stage=Stage.PAPER,
        why=(
            "The rules table starts empty and nothing else populates it. With zero rows the "
            "agent has no strategies to evaluate at all, however the config is set."
        ),
        how="keel rules seed  (seeds candidates only; promotion is a separate, deliberate step)",
    ),
    Step(
        key="credentials",
        title="A market-data credential",
        kind=StepKind.OPERATOR_INPUT,
        stage=Stage.PAPER,
        why=(
            "Candle history is fetched through an authenticated client, so `keel fetch` without a "
            "key fails outright -- even in paper mode, where no order can be placed. Only you "
            "have this key; keel can store it and cannot obtain or guess one."
        ),
        how=(
            "keel credentials set CDP_API_KEY   (a free, read-only Coinbase Developer Platform "
            "key is enough for market data)"
        ),
    ),
    Step(
        key="market_data",
        title="Market data",
        kind=StepKind.MECHANICAL,
        stage=Stage.PAPER,
        why=(
            "A rule with no candles produces no signal, so an empty database looks exactly like "
            "a quiet market."
        ),
        how="keel fetch",
    ),
    Step(
        key="assets_attested",
        title="Every allowlisted asset screened and attested",
        kind=StepKind.JUDGEMENT,
        stage=Stage.PAPER,
        why=(
            "A Shariah classification is a human judgement with a cited source. An unsourced "
            "attestation is refused exactly like a missing one, and an unattested asset is "
            "treated as unknown rather than as fine. Nothing may default this."
        ),
        how="keel assets attest --asset X --sector ... --backing ... --source ...",
    ),
    Step(
        key="rule_promoted",
        title="At least one rule promoted to paper",
        kind=StepKind.JUDGEMENT,
        stage=Stage.PAPER,
        why=(
            "Seeded rules are candidates and trade nothing. Which rule to run is your choice -- "
            "and on a fresh deployment the gate will very likely REFUSE it, naming too few "
            "trades, a win rate under the floor, and an overfitting check that was never run. "
            "That is the engine working, not a fault: a rule that has not earned paper status "
            "does not get it, and this step can stay outstanding for a long time. Bypassing the "
            "gate is `keel rules promote --force`, deliberately and on the record, at a terminal."
        ),
        how="keel rules promote <id>",
    ),
    Step(
        key="venue_interest_off",
        title="Interest and rewards disabled at the venue",
        kind=StepKind.OFF_VENUE,
        stage=Stage.LIVE,
        why=(
            "Coinbase pays USDC Rewards on idle balances and Alpaca pays stock-lending and "
            "cash-sweep interest. That interest is riba, and it accrues with NO order placed, so "
            "no rail can observe it. The venue's API does not expose enrolment status, so keel "
            "cannot verify this and will never show it as done -- a green check that verifies "
            "nothing turns an open risk into a false assurance."
        ),
        how=(
            "In the venue's own dashboard, turn off USDC Rewards (Coinbase: Assets -> USDC, or "
            "Settings -> Rewards/Earn) and any staking, earn or lending feature. Re-check after "
            "venue product changes -- enrolment has been on by default in some regions."
        ),
    ),
    Step(
        key="subscription_attested",
        title="The venue subscription attested",
        kind=StepKind.JUDGEMENT,
        stage=Stage.LIVE,
        why=(
            "Rail 14's monthly spend allowance is read from this record, and what you actually "
            "pay the venue is a fact only you have -- keel cannot read your billing. Without an "
            "attestation there is no allowance, and the rail vetoes."
        ),
        how="keel subscription attest --venue ... --tier ...",
    ),
    Step(
        key="withdrawals_attested",
        title="Withdrawal capability attested (rail 17)",
        kind=StepKind.JUDGEMENT,
        stage=Stage.LIVE,
        why=(
            "Holding a balance at a venue you cannot withdraw from is a compliance precondition, "
            "not a technical one, so the rail fails closed until a human says otherwise. The "
            "attestation carries a 7-day TTL and needs a terminal."
        ),
        how="keel withdrawals attest --enabled   (needs an interactive terminal)",
    ),
    Step(
        key="scope_attested",
        title="This venue's credential attested for live trading (rail 20)",
        kind=StepKind.JUDGEMENT,
        stage=Stage.LIVE,
        why=(
            "Rail 20 is a fact about the CREDENTIAL, not the asset, and it merged this morning "
            "(#233): a key that reads fine is not evidence it can trade -- a well-formed "
            "ROBINHOOD_API_KEY passed every read and the first live order still 403'd with 'You "
            "do not have permission to perform this action.' Only you can verify a credential "
            "actually places orders, so the rail fails closed on an unattested venue and keeps "
            "failing closed until a human says otherwise. Entries only -- it never blocks an "
            "exit, a stop roll, a cancel or a DCA exit."
        ),
        how="keel scope attest --trading   (needs an interactive terminal)",
    ),
    Step(
        key="confirm_cycle",
        title="One supervised cycle in confirm mode, verified at the venue",
        kind=StepKind.JUDGEMENT,
        stage=Stage.LIVE,
        why=(
            "The last step before real money runs unattended is a human watching one order go "
            "through and checking the fill against the venue's own screen. keel cannot do this "
            "for you and cannot tell whether you did."
        ),
        how="keel agent --once   with auto_trade.mode: confirm, then check the fill at the venue",
    ),
)


@dataclass(frozen=True)
class DeploymentState:
    """What is here, and what the deployment still needs."""

    root: Path
    config_path: Path
    db_path: Path
    states: tuple[StepState, ...]

    @property
    def is_new(self) -> bool:
        """True when there is no deployment here at all -- no config and no database.

        The signal a first run is being looked at, and deliberately narrow: a half-built
        deployment is NOT new, and offering to create one over the top of it is how a wizard
        overwrites a config someone spent an afternoon editing."""
        return not self.config_path.exists() and not self.db_path.exists()

    def stage_states(self, stage: Stage) -> tuple[StepState, ...]:
        return tuple(state for state in self.states if state.step.stage is stage)

    def ready_for(self, stage: Stage) -> bool:
        """Whether every step up to and including `stage` is observably done.

        `OFF_VENUE` steps can never be observed, so a deployment is never `ready_for(LIVE)` by
        this function's reckoning. That is the honest answer and it is deliberate: the last word
        on going live belongs to the operator who checked the venue dashboard, not to a function
        that cannot see it.

        `ready_for(PAPER)` is a real answer, but not one a fresh install reaches quickly:
        `rule_promoted` waits on a promotion gate that a newly-seeded rule will very likely fail,
        by design. "Set up" and "has a rule worth running" are different states, and this reports
        the second."""
        wanted = (Stage.PAPER,) if stage is Stage.PAPER else (Stage.PAPER, Stage.LIVE)
        return all(state.done is True for state in self.states if state.step.stage in wanted)

    @property
    def has_usable_database(self) -> bool:
        """Whether the dashboard pages can be built at all.

        `gather_status` reads tables; against a database with no schema it raises, and the
        browser view would answer a 500 to someone whose actual problem is that they have not
        set anything up yet. This is the check that turns that into a checklist."""
        for item in self.states:
            if item.step.key == "database":
                return item.done is True
        return False

    @property
    def next_step(self) -> StepState | None:
        """The first thing still to do, in runbook order. `None` when nothing is left."""
        for state in self.states:
            if state.blocking:
                return state
        return None

    @property
    def live_blockers(self) -> tuple[StepState, ...]:
        """Every outstanding step blocking live, in `STEPS` order -- paper steps first, because
        live is downstream of paper and #437's acceptance criterion says "in order".

        An `OFF_VENUE` step is satisfied by ACKNOWLEDGEMENT, never by `done`: `done` can never be
        `True` for one (see `venue_interest_off`'s doctrine, enforced above `ACTIONS`), so using
        `.blocking` here would make this property permanently non-empty the moment a deployment
        adds its first off-venue step -- exactly the trap `ready_for` avoids by returning `False`
        outright instead. Every other step is satisfied only when `done is True`, unchanged.
        """
        blockers = []
        for state in self.states:
            if state.step.kind is StepKind.OFF_VENUE:
                if state.acknowledged_at is None:
                    blockers.append(state)
            elif state.done is not True:
                blockers.append(state)
        return tuple(blockers)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _config_or_none(config_path: Path) -> Any:
    """The parsed config, or `None` when it is absent or unreadable.

    Deliberately swallowing: this runs to DESCRIBE a deployment, including a broken one, and an
    exception here would replace a checklist that says "your config will not parse" with a
    traceback that says the same thing less usefully."""
    if not config_path.exists():
        return None
    try:
        from keel.config import load_config

        return load_config(str(config_path))
    except Exception:
        return None


def inspect(config_path: str | Path, db_path: str | Path) -> DeploymentState:
    """Read the deployment and answer what it still needs. Writes nothing, migrates nothing."""
    config_file = Path(config_path)
    db_file = Path(db_path)
    config = _config_or_none(config_file)

    observations: dict[str, tuple[bool | None, str]] = {}

    # -- config --
    if not config_file.exists():
        observations["config"] = (False, f"no config at {config_file}")
    elif config is None:
        observations["config"] = (False, f"{config_file} exists but could not be parsed")
    else:
        mode = getattr(getattr(config, "auto_trade", None), "mode", "?")
        allowlist = list(getattr(config, "allowlist", []) or [])
        observations["config"] = (
            True,
            f"{config_file} -- mode {mode}, {len(allowlist)} allowlisted asset(s)",
        )

    observations["credentials"] = _credential_observation(config_file)

    # -- everything that needs the database --
    conn: sqlite3.Connection | None = None
    if db_file.exists():
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            # `Repository` reads rows by COLUMN NAME (`row["value"]`, `row["state"]`, ...), which
            # `_scope_observation`/`_venue_interest_observation` now call straight into for the
            # same reason `_withdrawals_observation` stayed on raw SQL: one place to decode a
            # `venue_trade_scopes` row (`_trade_scope_from_row`) rather than a second copy of it
            # here. Without this, every such call raises `TypeError: tuple indices must be
            # integers`, the default row shape this connection would otherwise have.
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            observations["database"] = (None, f"{db_file} could not be opened: {exc}")
    else:
        observations["database"] = (False, f"no database at {db_file}")
        # Everything below the database is knowably NOT done, not merely unknown: with no
        # database there are no rules, no candles and no attestations. Reporting these as
        # undetermined would tell a first-run user their deployment is in an unclear state when
        # it is in a perfectly clear one -- empty.
        for key in (
            "rules",
            "rule_promoted",
            "market_data",
            "assets_attested",
            "subscription_attested",
            "withdrawals_attested",
            "scope_attested",
            "confirm_cycle",
        ):
            observations[key] = (False, "no database yet")

    acks: dict[str, tuple[int | None, str]] = {}
    if conn is not None:
        try:
            db_observations, acks = _database_observations(conn, config, db_file)
            observations.update(db_observations)
        except sqlite3.Error as exc:
            # `None`, not False: an unreadable database is not an unseeded one, and telling an
            # operator to re-run steps that may already be done is its own kind of wrong.
            observations["database"] = (None, f"{db_file} could not be read: {exc}")
        finally:
            conn.close()

    states = []
    for step in STEPS:
        done, detail = observations.get(
            step.key,
            (None, "not determined -- the database could not be read")
            if step.kind is not StepKind.OFF_VENUE
            else (False, "keel cannot observe this; see below"),
        )
        acknowledged_at, acknowledged_by = acks.get(step.key, (None, ""))
        states.append(
            StepState(
                step=step,
                done=done,
                detail=detail,
                acknowledged_at=acknowledged_at,
                acknowledged_by=acknowledged_by,
            )
        )
    return DeploymentState(
        root=config_file.parent, config_path=config_file, db_path=db_file, states=tuple(states)
    )


def _database_observations(
    conn: sqlite3.Connection, config: Any, db_file: Path
) -> tuple[dict[str, tuple[bool | None, str]], dict[str, tuple[int | None, str]]]:
    """Everything the database can answer, as (done, detail) per step key -- plus a second,
    narrower map of (acknowledged_at, acknowledged_by) for whichever `OFF_VENUE` steps carry an
    acknowledgement. Two maps rather than one because the two have different TYPES and different
    OWNERS: the first is what `done` means, re-derived fresh on every call; the second is
    provenance nothing here computes, only reads back."""
    out: dict[str, tuple[bool | None, str]] = {}
    acks: dict[str, tuple[int | None, str]] = {}

    if not _table_exists(conn, RULES_TABLE):
        out["database"] = (False, f"{db_file} has no schema yet")
        return out, acks
    out["database"] = (True, f"{db_file}")

    rule_count = _count(conn, RULES_TABLE)
    out["rules"] = (
        rule_count > 0,
        f"{rule_count} rule(s) in the library" if rule_count else "the rule library is empty",
    )

    promoted = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {RULES_TABLE} WHERE status IN ('paper', 'live')"
        ).fetchone()[0]
    )
    out["rule_promoted"] = (
        promoted > 0,
        f"{promoted} rule(s) promoted" if promoted else "every rule is still a candidate",
    )

    if _table_exists(conn, CANDLES_TABLE):
        candles = _count(conn, CANDLES_TABLE)
        out["market_data"] = (
            candles > 0,
            f"{candles:,} candle(s) stored" if candles else "no market data yet",
        )
    else:
        out["market_data"] = (False, "no market data yet")

    out["assets_attested"] = _asset_observation(conn, config)

    if _table_exists(conn, SUBSCRIPTIONS_TABLE):
        subs = _count(conn, SUBSCRIPTIONS_TABLE)
        out["subscription_attested"] = (
            subs > 0,
            f"{subs} venue subscription(s) attested" if subs else "no subscription attested",
        )
    else:
        out["subscription_attested"] = (False, "no subscription attested")

    out["withdrawals_attested"] = _withdrawals_observation(conn)
    out["scope_attested"] = _scope_observation(conn, config)
    out["venue_interest_off"], acks["venue_interest_off"] = _venue_interest_observation(conn)
    # Never observable: keel cannot see whether a human watched one order go through, and
    # inferring it from a filled row would report a supervised cycle that nobody supervised.
    out["confirm_cycle"] = (False, "keel cannot observe this -- it is yours to do and to judge")
    return out, acks


#: The pair `keel fetch` needs. Both must be present: a key with no secret authenticates nothing.
MARKET_DATA_SECRETS: tuple[str, ...] = ("CDP_API_KEY", "CDP_API_SECRET")


def _credential_observation(config_file: Path) -> tuple[bool | None, str]:
    """Whether keel can see a market-data credential, and WHERE it is coming from.

    The source is reported because it is the difference between the two support questions this
    can produce -- "keel cannot see my key" and "keel is using a different key than the one I just
    typed". The VALUE is never read into the message, and `ResolvedSecret` will not print one even
    if a future edit tries."""
    from keel_core.secrets import SecretSource, read_secret

    env_path = config_file.parent / ".env"
    resolved = [read_secret(name, env_path=env_path) for name in MARKET_DATA_SECRETS]
    missing = [item.name for item in resolved if not item.found]
    if missing:
        return (False, f"missing: {', '.join(missing)}")
    sources = {item.source for item in resolved}
    if sources == {SecretSource.ENV_FILE}:
        return (True, "found in your .env file")
    if sources == {SecretSource.KEYCHAIN}:
        return (True, "stored in the OS keychain")
    if sources == {SecretSource.ENVIRONMENT}:
        return (True, "set in this process's environment")
    return (True, "found (" + ", ".join(sorted(s.value for s in sources)) + ")")


def _asset_observation(conn: sqlite3.Connection, config: Any) -> tuple[bool | None, str]:
    """Attested against the ALLOWLIST, not against the count.

    Six attestations mean nothing if the seventh allowlisted asset is unattested -- that asset
    is the one the rails will veto, and a checklist reporting "6 attested" would look finished.
    """
    if not _table_exists(conn, ASSET_ATTESTATIONS_TABLE):
        return (False, "no assets attested yet")
    attested = {
        str(row[0]).upper()
        for row in conn.execute(f"SELECT asset FROM {ASSET_ATTESTATIONS_TABLE}").fetchall()
    }
    if config is None:
        return (
            None,
            f"{len(attested)} asset(s) attested, but the allowlist could not be read",
        )
    allowlist = {str(asset).upper() for asset in (getattr(config, "allowlist", []) or [])}
    missing = sorted(allowlist - attested)
    if not allowlist:
        return (False, "the allowlist is empty, so there is nothing to trade")
    if missing:
        return (False, f"unattested: {', '.join(missing)}")
    return (True, f"all {len(allowlist)} allowlisted asset(s) attested")


def _withdrawals_observation(conn: sqlite3.Connection) -> tuple[bool | None, str]:
    """Rail 17's attestation, read from the same `state` row `keel withdrawals show` reads.

    Presence is reported, NOT freshness. The attestation carries a 7-day TTL and the status
    report computes it properly; duplicating that arithmetic here would be a second place to get
    it wrong, and this checklist's question is "has this ever been done", not "is it fresh now".
    """
    if not _table_exists(conn, STATE_TABLE):
        return (False, "no withdrawal attestation")
    row = conn.execute(
        f"SELECT value FROM {STATE_TABLE} WHERE key = ?", (WITHDRAWALS_STATE_KEY,)
    ).fetchone()
    if row is None:
        return (False, "no withdrawal attestation")
    return (True, "attested at some point -- keel status reports whether it is still fresh")


def _utc_date(ts: int) -> str:
    """An epoch second as a plain UTC date, for operator-facing provenance.

    Mirrors `keel.commands.scope._utc_date` exactly -- a raw epoch is unreadable at the moment it
    matters most, which for this module is an operator reading "who said they did this, and
    when" off a checklist. Not imported from `scope.py`: this module is a pure READ of the
    database and the config, and importing a CLI-facing command module into it for one date
    format would be the wrong direction of dependency for a two-line function.
    """
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def _resolved_venue(config: Any) -> str:
    """The venue rail 20 actually gates THIS deployment on: `config.broker.name`, the same field
    `keel.commands._common._load_cfg` binds via `bind_venue()` at process entry, defaulting to
    coinbase exactly as `BrokerConfig` does when a config omits `broker:` entirely.

    Deliberately NOT `_bound_venue_or_default` (`keel.commands._common`): that helper reads the
    CURRENTLY BOUND venue from `keel_core.telemetry`, and `inspect` has no click context to have
    run `_load_cfg` through -- nothing is bound here to read. Reading the field directly off the
    config this function ALREADY has is the read-only equivalent: the same source field, with
    the same default, resolved without a process-global side effect. Getting this wrong -- e.g.
    hard-coding coinbase -- would report an alpaca deployment's scope step against a coinbase
    record nothing writes, which is exactly the kind of wrongly-missing step this module's
    constants-block comment already warns about for table names.
    """
    from keel.execution.guards import DEFAULT_VENUE

    if config is None:
        return DEFAULT_VENUE
    name = getattr(getattr(config, "broker", None), "name", None)
    return name or DEFAULT_VENUE


def _scope_observation(conn: sqlite3.Connection, config: Any) -> tuple[bool | None, str]:
    """Rail 20's record (#233), read through the ONE method that owns the policy.

    Does not re-derive `may_place_live_entry`'s state machine: `scope.py`'s own module docstring
    is explicit that `VenueTradeScope.may_place_live_entry()` is the one place allowed to decide
    what `CONFIRMED`/`ATTESTED`/`REFUTED`/`UNVERIFIED` mean, and a second implementation here --
    even a read-only one -- would be a second place to get that meaning wrong.

    Distinguishes the three operator-facing cases rail 20's own guard message distinguishes: no
    record at all, REFUTED (naming `refuted_reason` when the venue gave one), and attested
    read-only. A `CONFIRMED` or `ATTESTED`-for-`TRADING` record is the only way this reports
    `True`.
    """
    from keel_core.trade_scope import TradeScopeState

    from keel.data.repository import Repository

    venue = _resolved_venue(config)
    if not _table_exists(conn, VENUE_TRADE_SCOPES_TABLE):
        return (False, f"{venue}: no trade scope attested -- rail 20 vetoes live entries")

    record = Repository(conn).get_venue_trade_scope(venue)
    if record is None:
        return (False, f"{venue}: no trade scope attested -- rail 20 vetoes live entries")
    # `current_fingerprint=None` (#633 PR1): this inspection does not yet resolve the real
    # current credential fingerprint, so it cannot distinguish "different credential" from
    # "never attested" -- deliberately unchanged in this PR, same reasoning as `venue_readiness`
    # and `scope_show_lines`. `None` never withdraws permission.
    if record.may_place_live_entry(None):
        return (True, f"{venue}: attested for TRADING (state={record.state.value})")
    if record.state is TradeScopeState.REFUTED:
        reason = f" ({record.refuted_reason})" if record.refuted_reason else ""
        return (
            False,
            f"{venue}: the venue REFUSED a live placement on this credential{reason} -- "
            "re-attest with `keel scope attest --trading` once the credential is fixed",
        )
    # ATTESTED-for-READ_ONLY, or UNVERIFIED -- the same pairing rail 20's own guard message uses.
    return (
        False,
        f"{venue}: attested READ_ONLY (or unverified) -- rail 20 vetoes live entries until it "
        "is attested for trading",
    )


def _venue_interest_observation(
    conn: sqlite3.Connection,
) -> tuple[tuple[bool | None, str], tuple[int | None, str]]:
    """Whether an acknowledgement has been recorded for `venue_interest_off` -- and, doctrinally,
    this NEVER makes `done` `True`. `keel` cannot observe USDC Rewards/lending enrolment at
    either venue (the module docstring's `OFF_VENUE` kind), so the first element of the return is
    always `False`; only the wording and the second element (provenance) change once someone has
    acknowledged.

    "On <date>, <name> stated they had done this at the venue" is a TRUE statement -- about a
    STATEMENT, not about the venue -- which is the whole argument the comment above `ACTIONS`
    makes for why this is worth recording at all despite never being allowed to tick the box.
    """
    if not _table_exists(conn, STATE_TABLE):
        return (False, "keel cannot observe this; see below"), (None, "")

    from keel.data.repository import Repository

    repo = Repository(conn)
    acknowledged_by = repo.get_state(VENUE_INTEREST_ACK_BY_KEY, default=None)
    acknowledged_at = repo.get_state(VENUE_INTEREST_ACK_AT_KEY, default=None)
    if not acknowledged_by or acknowledged_at is None:
        return (False, "keel cannot observe this; see below"), (None, "")

    at_int = int(acknowledged_at)
    detail = (
        f"{acknowledged_by} stated on {_utc_date(at_int)} that this was done at the venue -- "
        "keel did NOT and CANNOT verify it; this is why the step above is still not shown as "
        "done"
    )
    return (False, detail), (at_int, str(acknowledged_by))


def render_lines(state: DeploymentState) -> list[str]:
    """The checklist as text. ONE renderer, so the CLI and the browser cannot disagree about
    what a deployment needs."""
    lines: list[str] = []
    for stage, heading in ((Stage.PAPER, "to run in paper"), (Stage.LIVE, "to go live")):
        lines.append(f"{heading}:")
        for item in state.stage_states(stage):
            mark = "[x]" if item.done is True else "[ ]" if item.done is False else "[?]"
            lines.append(f"  {mark} {item.step.title}  ({item.step.kind.value})")
            lines.append(f"        {item.detail}")
            if item.blocking:
                lines.append(f"        do: {item.step.how}")
        lines.append("")
    nxt = state.next_step
    lines.append(f"next: {nxt.step.title} -- {nxt.step.how}" if nxt else "nothing outstanding.")
    return lines


# -- the command ---------------------------------------------------------------------------


@click.group("setup", invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
@click.pass_context
def setup_cmd(ctx: click.Context, as_json: bool) -> None:
    """What this deployment still needs, and which parts only you can do.

    Read-only: it opens the database read-only, parses the config if there is one, and writes
    nothing -- so it is safe to run against a deployment mid-cycle, and safe to run when there is
    no deployment here at all.

    Three kinds of step, and the distinction is the point. `mechanical` steps a machine may do
    for you. `judgement` steps it may collect and record but must never decide -- every Shariah
    attestation is a human classification with a cited source, and an unsourced one is refused
    exactly like a missing one. `off_venue` steps happen in the venue's own dashboard, and keel
    can neither perform nor verify them: disabling USDC Rewards and stock-lending interest is
    required because that interest is riba and accrues with no order placed, where no rail can
    see it. Those steps are therefore never shown as done -- a green check that verifies nothing
    would turn an open risk into a false assurance.

    **A group since #437 part 2, and `invoke_without_command=True` so the bare command is
    UNCHANGED.** `keel setup` with no subcommand still runs exactly this body, exactly as it did
    when this was a plain `@click.command` -- `tests/commands/test_setup.py` pins that the
    checklist text and the `--json` payload a bare invocation produces are identical to calling
    `inspect`/`render_lines`/`_state_as_json` directly, which is what a silent regression in the
    group conversion (losing `invoke_without_command`, or click's default "Missing command"
    usage error) would break first. `setup acknowledge-venue-interest-off` is the one subcommand
    -- see it below for why the CLI needs a write path the browser's `Action` already has.
    """
    if ctx.invoked_subcommand is not None:
        return
    obj = ctx.obj or {}
    state = inspect(
        obj.get("config_path") or default_config_path(),
        obj.get("db_path") or default_db_path(),
    )
    if as_json:
        click.echo(json.dumps(_state_as_json(state), indent=2))
        return
    for line in render_lines(state):
        click.echo(line)


@setup_cmd.command("acknowledge-venue-interest-off")
@click.option("--by", "acknowledged_by", required=True, help="Your name, for the record.")
@click.option(
    "--did-it/--not-yet",
    "did_it",
    required=True,
    help=(
        "Did you actually turn off USDC Rewards (Coinbase) and stock-lending/cash-sweep "
        "interest (Alpaca) at the venue? keel cannot verify this either way -- see "
        "`venue_interest_off` in `keel setup`."
    ),
)
@click.pass_context
def setup_acknowledge_venue_interest_off(
    ctx: click.Context, acknowledged_by: str, did_it: bool
) -> None:
    """Record that you say you turned off venue interest/rewards. Gives the CLI the same write
    the browser's `Action` has (#437 part 2), so a terminal-only deployment is not second-class.

    **Deliberately NOT behind the TTY gate** `withdrawals attest --enabled` and `scope attest
    --trading` use. That gate exists to stop a cron line releasing a rail veto with nobody at a
    terminal; this command releases no rail and no capability -- `keel/capabilities.py` has no
    entry for it, and adding `_require_interactive_confirmation` here with no capability to
    release would be ceremony buying nothing, the same objection the withdrawal/scope gates
    exist to avoid paying when it WOULD buy something.

    This can never make the step `done`. A blank `--by` or `--not-yet` records nothing at all --
    see `acknowledge_venue_interest_off`'s own docstring for why.
    """
    obj = ctx.obj or {}
    config_path = Path(obj.get("config_path") or default_config_path())
    db_path = Path(obj.get("db_path") or default_db_path())
    result = acknowledge_venue_interest_off(
        config_path,
        db_path,
        {"acknowledged_by": acknowledged_by, "did_it": "yes" if did_it else "no"},
    )
    click.echo(result.message)


def _state_as_json(state: DeploymentState) -> dict[str, Any]:
    return {
        "root": str(state.root),
        "config_path": str(state.config_path),
        "db_path": str(state.db_path),
        "is_new": state.is_new,
        "ready_for_paper": state.ready_for(Stage.PAPER),
        # Every outstanding step blocking live, in `STEPS` order -- see `DeploymentState.
        # live_blockers` for the `OFF_VENUE`-is-satisfied-by-acknowledgement rule this honours.
        "live_blockers": [item.step.key for item in state.live_blockers],
        "steps": [
            {
                "key": item.step.key,
                "title": item.step.title,
                "kind": item.step.kind.value,
                "stage": item.step.stage.value,
                "why": item.step.why,
                "how": item.step.how,
                "done": item.done,
                "detail": item.detail,
            }
            for item in state.states
        ],
    }


# -- the mechanical actions -------------------------------------------------------------------
#
# Everything below WRITES. Read the rules before adding to it:
#
# 1. THE INVARIANT IS THE CAPABILITY REGISTRY, not the step kind. Not one of the eight actions
#    in `keel/capabilities.py` may be reachable from `keel/web/`, and a test scans that package
#    to prove it. That is what stops the browser arming autonomy, releasing a halt, rebasing the
#    high-water mark, replacing the binary, or -- since #233 -- releasing rail 20's veto on a
#    venue's credential.
#
#    `StepKind` was tried as the rule ("MECHANICAL only", then "MECHANICAL or OPERATOR_INPUT")
#    and it was the wrong axis. Two JUDGEMENT steps -- attesting an asset, promoting a rule --
#    live in the PAPER stage, so forbidding them did not buy safety (the registry already
#    protects the dangerous ones); it only made a terminal-free paper deployment impossible,
#    which is the whole point of the milestone.
#
#    So the step-kind rule is now the SECONDARY policy it should always have been: **a wizard may
#    record what the operator supplies; it may never supply it.** An action over a JUDGEMENT step
#    must therefore declare inputs, every one of them required, with no defaults and nothing
#    pre-selected.
#
#    `OFF_VENUE` steps were held to be permanently unreachable for the same reason, on the theory
#    that "there is nothing there to record that would be true". That theory does not survive
#    contact with #437's own acceptance criterion -- "reaching live mode requires ... the
#    venue-side checklist explicitly acknowledged" has no meaning if no action anywhere can ever
#    record an acknowledgement -- and the module's OWN docstring already disagreed with it: "keel
#    can show the checklist and record that you say you did it" is right there at the top of this
#    file, three lines above "it cannot verify". The fix is not to add an exception, it is to
#    notice that "record that you say you did it" and "there is nothing there to record" were
#    never describing the same fact. "On <date>, <name> stated they had done this at the venue"
#    is a statement ABOUT A STATEMENT, and it is TRUE the moment the operator makes it -- keel
#    witnessed the claim even though it cannot witness the venue dashboard behind it. What keel
#    may never do is let that true statement stand in for a DIFFERENT, unverifiable one ("this is
#    actually off"), and `venue_interest_off.done` staying permanently non-`True` -- acknowledged
#    or not -- is what keeps those two statements from merging into one. That invariant is what
#    the runbook's "a green check verifying nothing is worse than an honest manual step" line is
#    actually defending: a property of the CHECK MARK, not of whether a record exists behind it.
#    So `venue_interest_off` now has an action, built to the same rule every JUDGEMENT action
#    follows -- required inputs, no defaults, nothing pre-selected, see rule 2 -- and it writes
#    provenance (who, when) through `detail`, never through `done`.
#
#    What remains CLI-only is decided by the registry, not by taste: `withdrawals attest
#    --enabled` and `scope attest --trading` are two of the eight and stay behind the TTY gate --
#    `scope_attested` is exactly the case this rule was written for: a JUDGEMENT step in the LIVE
#    stage, with a capability-registry entry, and therefore no `Action` despite being a step a
#    wizard could otherwise "collect and record". `confirm_cycle` is not an action because it
#    cannot be observed at all.
#
# 2. An action with `inputs` records ONLY what was submitted. It has no defaults, no fallbacks and
#    no "sensible guess" -- an action that could fill in a field the operator left blank is one
#    that could record something they never supplied.
#
# 3. Every action is IDEMPOTENT and NEVER destructive. A setup flow is something a nervous user
#    clicks twice, and a browser reload re-submits. "The config already exists" is a successful
#    outcome that changed nothing -- never an overwrite, and there is deliberately no `force`
#    parameter for any web caller to pass.
#
# 4. Nothing here increases what keel can DO. Not one of the eight capability-increasing actions
#    in `keel/capabilities.py` is reachable from this module, and a test asserts the two sets are
#    disjoint. Creating a config, a schema and a library of CANDIDATE rules leaves an engine that
#    still places nothing: candidates trade nothing until a human promotes them, and promotion is
#    a judgement step. Recording that an operator SAYS they flipped a venue dashboard switch is
#    the same shape of non-increase: it releases no rail, and `venue_interest_off.done` never
#    becomes `True` to make it look otherwise.


def template_config_text(live: bool = False) -> str:
    """A config.yaml template shipped inside the wheel (see pyproject `artifacts`).

    `live=False` returns the dev template (`mode: paper` -- places nothing). `live=True` returns
    the production template (`mode: confirm` -- previews every order and waits for approval),
    which is also the `config.yaml` attached to a GitHub Release.

    Lives here rather than in `keel/cli.py`, where it started: this service writes a config on a
    first run and a service may not import the CLI. `keel.cli` re-exports the name, so
    `init_config` and the tests that reach `keel.cli._template_config_text` are unaffected.
    """
    from importlib.resources import files

    name = "config.live.yaml" if live else "config.yaml"
    return (files("keel.templates") / name).read_text(encoding="utf-8")


@dataclass(frozen=True)
class ActionResult:
    step_key: str
    #: False when the step was already done. Not a failure -- the distinction the UI needs to say
    #: "created" rather than "already there", and the property that makes a double-click safe.
    changed: bool
    message: str


@dataclass(frozen=True)
class ActionInput:
    """One field an action needs from the operator.

    `secret=True` means the value must never be rendered back into a page, echoed into a log, or
    put in a URL. It is the difference between a `password` field and a `text` one, and between a
    form that can be submitted safely and one that leaks its own contents into browser history."""

    name: str
    label: str
    secret: bool = False
    #: A closed set of answers. Rendered as a select with NOTHING pre-selected -- see
    #: `keel/web/render.py`. Empty means free text.
    choices: tuple[str, ...] = ()
    #: Shown under the field. For a judgement, this is where the question actually gets asked.
    hint: str = ""


@dataclass(frozen=True)
class Action:
    key: str
    title: str
    #: What it will do, in the operator's words, shown before they choose it.
    detail: str
    run: Callable[[Path, Path, dict[str, str]], ActionResult]
    #: Empty for an action the machine performs unaided. Non-empty means it records what the
    #: operator supplied and nothing else -- see rule 2 above.
    inputs: tuple[ActionInput, ...] = ()

    @property
    def needs_input(self) -> bool:
        return bool(self.inputs)


def create_config(config_path: Path, _db_path: Path, _values: dict[str, str]) -> ActionResult:
    """Write the PAPER template if there is no config. Never overwrites.

    Paper deliberately, and never the live template from here: paper places nothing at all, which
    is the only state a first-run user can safely be dropped into. Hummingbot reached the same
    conclusion -- its paper mode needs no exchange keys, which makes it the only thing a new user
    *can* do first."""
    if config_path.exists():
        return ActionResult("config", False, f"{config_path} already exists -- left untouched")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(template_config_text(live=False), encoding="utf-8")
    return ActionResult("config", True, f"wrote {config_path} (paper -- places no orders)")


def create_database(_config_path: Path, db_path: Path, _values: dict[str, str]) -> ActionResult:
    """Create the database if absent and apply outstanding migrations.

    Migrations run every time, not only on first run, because that is what makes an upgrade
    self-heal: they are idempotent, so applying them costs nothing when there is nothing to
    apply."""
    from keel.data.db import connect, migrate

    existed = db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    if existed:
        return ActionResult("database", False, f"{db_path} is at the current schema")
    return ActionResult("database", True, f"created {db_path} at the current schema")


def seed_rule_library(config_path: Path, db_path: Path, _values: dict[str, str]) -> ActionResult:
    """Seed one CANDIDATE rule per (kind, allowlisted product).

    Candidates trade nothing. Promoting one is a separate, deliberate, human step -- which is why
    seeding is mechanical and promotion is not.

    Products come from the config's own allowlist through `parse_products_option`, the same
    validation the CLI applies: an id keel could not trade is refused here, naming it, with
    nothing written. Rails 18/19 would veto every order for such a rule anyway; the difference is
    that the operator hears it now rather than reading it out of a log."""
    from keel import agent
    from keel.commands._products import parse_products_option
    from keel.commands.rules import seed_rules_into
    from keel.data.db import connect
    from keel.data.repository import Repository

    config = _config_or_none(config_path)
    if config is None:
        return ActionResult("rules", False, f"no usable config at {config_path}")

    products, _warnings = parse_products_option(None, config)
    conn = connect(str(db_path))
    try:
        outcome = seed_rules_into(
            Repository(conn),
            list(agent.RULE_REGISTRY),
            products,
            status="candidate",
            force=False,
            now_ts=int(time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    if not outcome.seeded:
        return ActionResult(
            "rules", False, f"the library already covers all {len(outcome.skipped)} pair(s)"
        )
    return ActionResult(
        "rules",
        True,
        f"seeded {len(outcome.seeded)} candidate rule(s); candidates trade nothing until "
        "you promote one",
    )


def store_market_data_credential(
    config_path: Path, _db_path: Path, values: dict[str, str]
) -> ActionResult:
    """Record the CDP key and secret in the OS keychain.

    Records EXACTLY what was submitted. Both fields are required and neither has a default: an
    action that could fill in a field the operator left blank is one that could record something
    they never supplied.

    Nothing about the value reaches the result message, and `ResolvedSecret` refuses to print one
    even if a future edit tries. The confirmation is read back through the same resolver a real
    caller uses, so if a `.env` shadows what was just stored, the operator hears it now rather
    than at the first fetch that used the other key.
    """
    from keel_core.secrets import SecretSource, read_secret, store_secret

    missing = [name for name in MARKET_DATA_SECRETS if not values.get(name, "").strip()]
    if missing:
        return ActionResult(
            "credentials", False, f"nothing saved -- {', '.join(missing)} was blank"
        )

    try:
        for name in MARKET_DATA_SECRETS:
            store_secret(name, values[name].strip())
    except Exception as exc:
        # The message names the `.env` alternative; see `keel_core.secrets.store_secret`.
        return ActionResult("credentials", False, str(exc))

    env_path = config_path.parent / ".env"
    shadowed = [
        name
        for name in MARKET_DATA_SECRETS
        if read_secret(name, env_path=env_path).source is not SecretSource.KEYCHAIN
    ]
    if shadowed:
        return ActionResult(
            "credentials",
            True,
            "saved to the OS keychain, but keel will still read "
            f"{', '.join(shadowed)} from your .env or environment, which takes precedence",
        )
    return ActionResult("credentials", True, "saved to the OS keychain")


#: Years of history the first fetch ensures. The CLI's own `--years` default: a first run should
#: land in the same state a `keel fetch` would, not a thinner one that quietly changes what a
#: backtest is measured over.
FIRST_FETCH_YEARS = 5


def fetch_market_data(config_path: Path, db_path: Path, _values: dict[str, str]) -> ActionResult:
    """Start the first candle fetch IN THE BACKGROUND, and return immediately.

    This is the one action that cannot be synchronous. A first fetch runs for minutes across the
    allowlist; a request that blocks that long is not a button -- the browser gives up, the user
    reloads, and a second fetch starts on top of the first. `keel.commands.jobs` owns the single
    slot that makes the second one a refusal instead.

    `run_fetch` needs no adapting: its `echo` parameter is documented as the progress stream and
    emits the same lines the CLI prints, and its `build_client` is a lazy factory, so nothing
    constructs a broker until the fetch actually needs one.
    """
    from keel.commands import jobs

    if jobs.is_running():
        return ActionResult("market_data", False, "a job is already running")

    def _run(echo: Callable[[str], None]) -> None:
        import time as _time

        from keel.commands._common import _build_broker
        from keel.commands._products import parse_products_option
        from keel.commands.fetch import run_fetch
        from keel.config import load_config
        from keel.data import freshness as freshness_mod
        from keel.data.db import connect
        from keel.data.repository import Repository

        config = load_config(str(config_path))
        products, _warnings = parse_products_option(None, config)
        # Its own connection: this runs on a background thread, and a sqlite3 connection belongs
        # to the thread that made it.
        conn = connect(str(db_path))
        try:
            result = run_fetch(
                Repository(conn),
                config,
                lambda: _build_broker(config),
                db_path=str(db_path),
                products=products,
                years=FIRST_FETCH_YEARS,
                now_ts=int(_time.time()),
                tolerance_bars=freshness_mod.DEFAULT_TOLERANCE_BARS,
                echo=echo,
                echo_err=echo,
            )
        finally:
            conn.close()
        if result.error is not None:
            # RAISED, so the job records it as a failure. `run_fetch` returns the error rather
            # than raising because how a front-end fails is its business -- and this front-end
            # fails by showing a failed job, which is what the operator needs to see.
            raise RuntimeError(result.error)

    jobs.start("market_data", _run)
    return ActionResult(
        "market_data", True, "fetching in the background -- this page will show its progress"
    )


#: The classifications `keel assets attest` accepts. Read from the screen's own vocabulary so a
#: new backing kind cannot appear there and be missing here.
def _backing_choices() -> tuple[str, ...]:
    from keel.compliance import screen as screen_mod

    return tuple(sorted(screen_mod.KNOWN_BACKINGS))


def attest_asset(config_path: Path, db_path: Path, values: dict[str, str]) -> ActionResult:
    """Record one asset's Shariah classification -- EXACTLY as supplied, or not at all.

    Its CLI counterpart states the reason this is safe to expose: "an attestation cannot itself
    place an order or raise a cap, and the screen it feeds only ever ADMITS to a list that
    `guards.py` rail 1 still enforces per-trade". It is not one of the eleven, and the CLI imposes
    no ceremony on it beyond the mandatory `source`.

    Every field is required and NOTHING is defaulted. `pays_yield` in particular is a choice with
    no pre-selection rather than a checkbox: an unticked box would default to `no`, which is the
    PERMISSIVE answer (a yield-bearing asset fails KB §28.4), and a form whose default answer is
    the compliant one is a form that attests on the operator's behalf.
    """
    from keel.data.db import connect
    from keel.data.repository import Repository

    required = ("asset", "sector", "backing", "pays_yield", "source", "attested_by")
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        return ActionResult(
            "assets_attested", False, f"nothing recorded -- {', '.join(missing)} was blank"
        )

    backing = values["backing"].strip()
    if backing not in _backing_choices():
        return ActionResult("assets_attested", False, f"unknown backing {backing!r}")
    if values["pays_yield"].strip() not in ("yes", "no"):
        return ActionResult("assets_attested", False, "answer the yield question")

    asset = values["asset"].strip().upper()
    conn = connect(str(db_path))
    try:
        Repository(conn).upsert_asset_attestation(
            asset=asset,
            sector=values["sector"].strip(),
            backing=backing,
            pays_yield=values["pays_yield"].strip() == "yes",
            source=values["source"].strip(),
            attested_by=values["attested_by"].strip(),
            attested_at=int(time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return ActionResult("assets_attested", True, f"attested {asset}")


def promote_rule(config_path: Path, db_path: Path, values: dict[str, str]) -> ActionResult:
    """Re-run a rule's backtest and advance it IF it clears the gate -- in the background.

    **`force` is hard-wired False and is not a field.** `attempt_promotion`'s own docstring is
    explicit that force "carries no gate HERE ... the O3 contract is the front-end's to keep,
    never the service's to assume" -- so a front-end that offered it would be the thing removing
    the gate. The console's force path runs a typed terminal confirmation first; this one simply
    does not have a force path, which is the only version of that contract a browser can keep.

    A promotion re-runs a backtest, so it is a job for the same reason a fetch is.
    """
    from keel.commands import jobs

    raw = values.get("rule_id", "").strip()
    if not raw.isdigit():
        return ActionResult("rule_promoted", False, "nothing done -- give a numeric rule id")
    rule_id = int(raw)

    if jobs.is_running():
        return ActionResult("rule_promoted", False, "a job is already running")

    def _run(echo: Callable[[str], None]) -> None:
        from keel.commands.rules import attempt_promotion
        from keel.config import load_config
        from keel.data.db import connect
        from keel.data.repository import Repository

        conn = connect(str(db_path))
        try:
            attempt_promotion(
                Repository(conn),
                lambda: load_config(str(config_path)),
                rule_id,
                force=False,
                echo=echo,
                echo_err=echo,
            )
            conn.commit()
        finally:
            conn.close()

    jobs.start("rule_promoted", _run)
    return ActionResult("rule_promoted", True, "running the backtest and gate -- progress is above")


def acknowledge_venue_interest_off(
    _config_path: Path, db_path: Path, values: dict[str, str]
) -> ActionResult:
    """Record that the operator SAYS they turned off venue interest/rewards -- never that keel
    verified it, and never anything that makes `venue_interest_off.done` `True`. See the long
    comment above `ACTIONS` for the doctrine this defends: "on <date>, <name> stated they had
    done this at the venue" is a true statement about a STATEMENT, which is what makes it safe
    to record despite keel having no way to check the venue dashboard behind it.

    Shared verbatim between the browser `Action` and `keel setup acknowledge-venue-interest-off`
    -- one implementation, so the CLI and the web cannot silently disagree about what "blank"
    or "no" mean here.

    Required inputs, no defaults and nothing pre-selected (rule 2 above), and the two failure
    cases are both REFUSALS to write, not softened into a default:

    - A blank `acknowledged_by` records nothing. Defaulting it to e.g. `"operator"` would let an
      empty form field silently attribute an acknowledgement nobody actually made.
    - `did_it` answering anything other than exactly `"yes"` records nothing. This is a
      yes/no question with NO pre-selection for the same reason `pays_yield` has none in
      `attest_asset`: an unticked box must not default to the permissive answer, and here the
      permissive answer is "yes, I did it".
    """
    from keel.data.db import connect
    from keel.data.repository import Repository

    acknowledged_by = values.get("acknowledged_by", "").strip()
    did_it = values.get("did_it", "").strip().lower()
    if not acknowledged_by:
        return ActionResult(
            "venue_interest_off", False, "nothing recorded -- your name was blank"
        )
    if did_it != "yes":
        return ActionResult(
            "venue_interest_off",
            False,
            "nothing recorded -- you said you have not done this at the venue yet",
        )

    now_ts = int(time.time())
    conn = connect(str(db_path))
    try:
        repo = Repository(conn)
        repo.set_state(VENUE_INTEREST_ACK_BY_KEY, acknowledged_by)
        repo.set_state(VENUE_INTEREST_ACK_AT_KEY, now_ts)
        conn.commit()
    finally:
        conn.close()
    return ActionResult(
        "venue_interest_off",
        True,
        f"recorded: {acknowledged_by} said on {_utc_date(now_ts)} that this was done at the "
        "venue. keel did NOT and CANNOT verify it -- this step is still never shown as done.",
    )


#: THE closed set of steps a machine may perform on the operator's behalf.
ACTIONS: tuple[Action, ...] = (
    Action(
        key="config",
        title="Create a config file",
        detail=(
            "Writes the paper template. Paper places no orders at all, so nothing can be spent "
            "by anything that follows. An existing config is never overwritten."
        ),
        run=create_config,
    ),
    Action(
        key="database",
        title="Create the database",
        detail="Creates it if absent and applies outstanding migrations. Both are idempotent.",
        run=create_database,
    ),
    Action(
        key="rules",
        title="Seed the rule library",
        detail=(
            "One candidate rule per strategy and allowlisted product. Candidates trade nothing "
            "-- promoting one is your decision, and a separate step."
        ),
        run=seed_rule_library,
    ),
    Action(
        key="credentials",
        title="Save a market-data credential",
        detail=(
            "A free, read-only Coinbase Developer Platform key is enough for candle history. It "
            "is stored in your operating system's keychain, not in a file, and keel never "
            "displays it again."
        ),
        run=store_market_data_credential,
        inputs=(
            ActionInput("CDP_API_KEY", "CDP API key"),
            ActionInput("CDP_API_SECRET", "CDP API secret", secret=True),
        ),
    ),
    Action(
        key="assets_attested",
        title="Record this attestation",
        detail=(
            "Your classification of one asset, recorded exactly as you give it. keel does not "
            "check it and cannot: it is a statement about the world, and the source is what makes "
            "it one. Nothing here is filled in for you."
        ),
        run=attest_asset,
        inputs=(
            ActionInput("asset", "Asset code", hint="e.g. BTC"),
            ActionInput("sector", "Core business line or purpose", hint="what the token is for"),
            ActionInput(
                "backing",
                "Backing",
                choices=("ayn", "dayn", "native"),
                hint=(
                    "'ayn (an owned thing), dayn (a claim on an issuer), native (a base-layer coin)"
                ),
            ),
            ActionInput(
                "pays_yield",
                "Does holding it earn a return?",
                choices=("no", "yes"),
                hint="a yield-bearing asset is refused by the screen (KB §28.4)",
            ),
            ActionInput(
                "source", "Source", hint="URL, standard or ruling this was established from"
            ),
            ActionInput("attested_by", "Attested by", hint="who established it"),
        ),
    ),
    Action(
        key="rule_promoted",
        title="Promote this rule",
        detail=(
            "Re-runs the rule's backtest and advances it only if it clears the gate. There is no "
            "force option here: bypassing the gate needs a terminal."
        ),
        run=promote_rule,
        inputs=(ActionInput("rule_id", "Rule id", hint="from the Rules page"),),
    ),
    Action(
        key="market_data",
        title="Fetch market data",
        detail=(
            "Downloads candle history for every allowlisted product. This runs in the background "
            "and takes minutes on a first run; the page shows its progress."
        ),
        run=fetch_market_data,
    ),
    Action(
        key="venue_interest_off",
        title="Record that you turned this off at the venue",
        detail=(
            "Records only that you said so, with your name and today's date. keel has no way to "
            "see USDC Rewards or stock-lending enrolment and does not verify this -- it is never "
            "shown as done, here or anywhere else, acknowledged or not."
        ),
        run=acknowledge_venue_interest_off,
        inputs=(
            ActionInput("acknowledged_by", "Your name", hint="who is acknowledging this"),
            ActionInput(
                "did_it",
                "Did you turn off USDC Rewards / lending / cash-sweep interest at the venue?",
                choices=("yes", "no"),
                hint="answering anything but yes records nothing",
            ),
        ),
    ),
)

#: Mechanical steps deliberately NOT offered as one-click actions, and why. Recorded as data
#: rather than omitted silently, so a gap is visible to the next person rather than looking like
#: an oversight. Empty is a fine value: `market_data` lived here until `keel.commands.jobs` gave
#: a long-running step somewhere to live.
NOT_AUTOMATED_YET: dict[str, str] = {}


def action_for(key: str) -> Action | None:
    for action in ACTIONS:
        if action.key == key:
            return action
    return None
