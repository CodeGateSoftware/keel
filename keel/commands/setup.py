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
WITHDRAWALS_STATE_KEY = "withdrawals_enabled"

#: Every table name above, for the pin to iterate.
READ_TABLES: tuple[str, ...] = (
    RULES_TABLE,
    CANDLES_TABLE,
    ASSET_ATTESTATIONS_TABLE,
    SUBSCRIPTIONS_TABLE,
    STATE_TABLE,
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
        why="Seeded rules are candidates and trade nothing. Which rule to run is your choice.",
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
        that cannot see it."""
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
            "confirm_cycle",
        ):
            observations[key] = (False, "no database yet")

    if conn is not None:
        try:
            observations.update(_database_observations(conn, config, db_file))
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
        states.append(StepState(step=step, done=done, detail=detail))
    return DeploymentState(
        root=config_file.parent, config_path=config_file, db_path=db_file, states=tuple(states)
    )


def _database_observations(
    conn: sqlite3.Connection, config: Any, db_file: Path
) -> dict[str, tuple[bool | None, str]]:
    """Everything the database can answer, as (done, detail) per step key."""
    out: dict[str, tuple[bool | None, str]] = {}

    if not _table_exists(conn, RULES_TABLE):
        out["database"] = (False, f"{db_file} has no schema yet")
        return out
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
    # Never observable: keel cannot see whether a human watched one order go through, and
    # inferring it from a filled row would report a supervised cycle that nobody supervised.
    out["confirm_cycle"] = (False, "keel cannot observe this -- it is yours to do and to judge")
    return out


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


@click.command("setup")
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
    """
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


def _state_as_json(state: DeploymentState) -> dict[str, Any]:
    return {
        "root": str(state.root),
        "config_path": str(state.config_path),
        "db_path": str(state.db_path),
        "is_new": state.is_new,
        "ready_for_paper": state.ready_for(Stage.PAPER),
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
# 1. Only `MECHANICAL` and `OPERATOR_INPUT` steps may appear here, and the line between them and
#    the rest is the point. A MECHANICAL step has no input: the machine simply does it. An
#    OPERATOR_INPUT step is a FACT only the operator has -- an API key -- which a wizard can
#    record and could not possibly invent.
#
#    `JUDGEMENT` steps are DECISIONS: a Shariah classification, a promotion. A form that recorded
#    one would be making a compliance ruling on the operator's behalf, and no amount of "but they
#    clicked it" makes that the same thing as their having decided it. `OFF_VENUE` steps happen
#    somewhere keel cannot reach at all. Neither may ever be an action here, and the tests
#    enforce it against `STEPS` rather than against a list kept in this file.
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
# 4. Nothing here increases what keel can DO. Not one of the eleven capability-increasing actions
#    in `keel/capabilities.py` is reachable from this module, and a test asserts the two sets are
#    disjoint. Creating a config, a schema and a library of CANDIDATE rules leaves an engine that
#    still places nothing: candidates trade nothing until a human promotes them, and promotion is
#    a judgement step.


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
)

#: Mechanical steps that are deliberately NOT offered as one-click actions, and why. Recorded as
#: data rather than omitted silently, so the gap is visible to the next person rather than
#: looking like an oversight.
NOT_AUTOMATED_YET: dict[str, str] = {
    "market_data": (
        "The first fetch is a network call that can run for minutes across the allowlist. A "
        "request that blocks that long is not a button, it is a background job with progress "
        "and cancellation -- so it stays `keel fetch` until there is somewhere for such a job "
        "to live."
    ),
}


def action_for(key: str) -> Action | None:
    for action in ACTIONS:
        if action.key == key:
            return action
    return None
