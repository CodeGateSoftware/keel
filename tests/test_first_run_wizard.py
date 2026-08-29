"""Acceptance pins for issue #437 -- "a first-run wizard that presents the ceremony rather than
skipping it."

The wizard (`keel.commands.setup`) shipped with its own unit tests for individual table/key names
(`tests/commands/test_setup.py`), but #437's four acceptance criteria never had a test that pins
the CRITERION rather than an implementation detail underneath it. That gap matters because the
criteria are the actual contract the issue promised, and the module beneath them is refactored
far more often than the promise is re-read. This file is that missing layer -- one test (or small
group) per criterion, written against the acceptance language itself.

**Pin 1, and the one that outranks the other three.** A wizard is new code reaching into an old
deployment's files. `~/keel` trades unattended, daily, on a live config and a database with
months of orders, attestations and a kill-switch state in it -- and nothing about teaching keel to
set up a FRESH deployment may change so much as one byte of an EXISTING one. `inspect` is
documented as read-only by contract; `create_config`/`create_database`/`seed_rule_library` are
documented as idempotent and non-destructive by contract. Both contracts are assumed everywhere
else in this module and neither had a test that actually builds a realistic deployment and proves
the assumption. The specific failure this prevents: a wizard that treats "already configured" as
"needs configuring," silently overwriting a live `config.yaml`'s caps or mode, dropping and
re-creating tables that hold a trading history, or re-seeding a rule library over rows a human has
since promoted.

**Pin 2, Acceptance 1.** A user with no prior state must be able to run the MECHANICAL steps alone
(`create_config`, `create_database`, `seed_rule_library` -- no network, no judgement) and land on a
working PAPER deployment: a config that parses, `mode: paper` (never `confirm`, and never by
construction since `create_config` could change which template it writes), a database at the
current schema, and a rule library that exists and trades nothing (`status == "candidate"`
everywhere). The failure this prevents is subtle: a fresh deployment that is technically "set up"
but one dropped guard away from opening `mode: confirm` on a user who never attested anything.

**Pin 3, Acceptance 2.** keel's whole Shariah-compliance premise is that a classification is a
human judgement with a cited source, never an inference from market data (`compliance/screen.py`'s
module docstring). A wizard that could record an attestation with a blank `source` -- or, worse,
record ANY attestation by defaulting a missing field -- would let an operator click through a form
that *looks* like they classified an asset when nobody did. This is pinned on BOTH sides of the
boundary: `keel.commands.setup.attest_asset` (the wizard's own gate) and
`keel.compliance.screen.screen_asset` (the engine's gate, which a CLI bypass or a future
second front-end could still reach). Each refusal is checked by its STATED reason string, not
merely by "not admitted" / "changed is False" -- a screen or an action that refuses every input
for the wrong reason would otherwise pass a pin that only checked the verdict.

**Pin 4.** The issue asks that migrations run idempotently "on every start, not only first run," so
an upgrade self-heals without a separate migrate step a user could forget. This is already true --
`keel/commands/_common.py::_open_repo` calls `migrate(conn)` unconditionally on every command that
opens the repository -- so there is nothing to BUILD here, only something to PIN before a future
edit adds an `if` that reintroduces the gap. The pin is two-layered: a behavioural check (migrating
an already-current database changes nothing) and a structural one (an AST scan of `_open_repo`
proving the `migrate` call sits outside every `if`), because the behavioural half alone cannot
distinguish "unconditional and a no-op" from "conditionally skipped and therefore also a no-op."
"""

from __future__ import annotations

import ast
import inspect as python_inspect
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from keel.commands import _common
from keel.commands.setup import (
    WITHDRAWALS_STATE_KEY,
    attest_asset,
    create_config,
    create_database,
    seed_rule_library,
)
from keel.commands.setup import inspect as setup_inspect
from keel.compliance import screen as screen_mod
from keel.config import load_config
from keel.data.db import SCHEMA_VERSION, connect, migrate
from keel.data.repository import Repository
from tests.conftest import VALID_CONFIG_YAML

# ---------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------


def _dump_tables(db_path: Path) -> dict[str, list[tuple]]:
    """Every row of every real table, as plain tuples -- the byte-for-byte honesty check Pin 1
    and Pin 4 both need. `sqlite_sequence` and friends are excluded by the `sqlite_%` filter, not
    because they are uninteresting but because SQLite itself may rewrite their internal bookkeeping
    on a no-op `VACUUM`-adjacent operation in ways that have nothing to do with OUR data."""
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    finally:
        conn.close()


def _build_configured_deployment(tmp_path: Path) -> tuple[Path, Path]:
    """A deployment that looks like `~/keel` looks today: a live-mode config, a migrated
    database, a seeded (and therefore non-empty) rule library, one asset attestation, a
    withdrawals attestation row, and an autonomy/kill-switch row. Built from `VALID_CONFIG_YAML`
    (the same known-good fixture `tests/commands/test_setup.py` migrates against) with `mode`
    flipped to `confirm`, so this reads as a live deployment rather than a paper one -- the
    riskier of the two to get wrong."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML.replace("mode: paper", "mode: confirm"))

    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()

    seed_rule_library(config_path, db_path, {})

    now_ts = int(time.time())
    conn = connect(str(db_path))
    try:
        repo = Repository(conn)
        repo.upsert_asset_attestation(
            asset="BTC",
            sector="payments",
            backing="native",
            pays_yield=False,
            source="https://example.org/btc-classification",
            attested_by="operator",
            attested_at=now_ts,
        )
        repo.set_state(WITHDRAWALS_STATE_KEY, True)
        repo.set_state("kill_switch", False)
        repo.set_autonomous(True, now_ts)
    finally:
        conn.close()
    return config_path, db_path


# ---------------------------------------------------------------------------------------------
# Pin 1 -- THE HARD RULE: an existing configured deployment is left byte-identical.
# ---------------------------------------------------------------------------------------------


def test_a_configured_deployment_is_byte_identical_after_every_idempotent_action(
    tmp_path: Path,
) -> None:
    """Snapshot a realistic deployment, run every no-network mechanical action against it a
    SECOND time (exactly what a nervous user clicking "set up" twice, or a browser reload
    re-submitting a form, would do), and assert nothing moved: not the config's bytes, not
    `auto_trade.mode`, not one row in any table.

    Mutation-verified three ways (see the commit message): `create_config` made to overwrite
    unconditionally, `create_database` made to drop and recreate its tables, and
    `seed_rule_library` made to re-seed over an existing library with `force=True`. Each was
    caught and is restored in the working tree -- `keel/commands/setup.py` carries none of them.
    """
    config_path, db_path = _build_configured_deployment(tmp_path)

    config_bytes_before = config_path.read_bytes()
    dump_before = _dump_tables(db_path)
    mode_before = load_config(str(config_path)).auto_trade.mode
    assert mode_before == "confirm"  # sanity: this deployment really is live-mode, not paper

    # `inspect()` is documented read-only and is exercised here too -- it runs on every page
    # load in the browser front-end, including against a deployment mid-cycle.
    setup_inspect(config_path, db_path)

    config_result = create_config(config_path, db_path, {})
    db_result = create_database(config_path, db_path, {})
    rules_result = seed_rule_library(config_path, db_path, {})

    # `changed is False` is the module's own vocabulary for "already done, nothing written" --
    # asserted here as the cheap, direct signal before the expensive byte/row comparison below.
    assert config_result.changed is False, config_result.message
    assert db_result.changed is False, db_result.message
    assert rules_result.changed is False, rules_result.message

    config_bytes_after = config_path.read_bytes()
    dump_after = _dump_tables(db_path)
    mode_after = load_config(str(config_path)).auto_trade.mode

    assert config_bytes_after == config_bytes_before, (
        "a mechanical action rewrote an existing config.yaml -- an already-configured "
        "deployment must come out byte-identical"
    )
    assert mode_after == mode_before, (
        f"auto_trade.mode changed from {mode_before!r} to {mode_after!r} -- a live deployment "
        "must never have its trading mode silently rewritten by the wizard"
    )
    assert dump_after == dump_before, (
        "a mechanical action changed the contents of an already-migrated, already-seeded "
        "database -- nothing here may be destructive"
    )


# ---------------------------------------------------------------------------------------------
# Pin 2 -- Acceptance 1: a user with no prior state reaches a working paper deployment.
# ---------------------------------------------------------------------------------------------


def test_mechanical_steps_alone_reach_a_working_paper_deployment(tmp_path: Path) -> None:
    """From an empty directory -- no config, no database, nothing -- running exactly the three
    MECHANICAL actions in runbook order must produce: a config that parses, `mode == "paper"`
    (read from the config the action actually WROTE, never from the template constant directly,
    so a future template edit cannot silently invalidate this pin's own assumption), a database
    stamped at `SCHEMA_VERSION`, and a non-empty rule library whose rows are every one a
    `candidate` -- candidates trade nothing, which is the entire safety property a fresh,
    unattested deployment needs.

    Mutation-verified: `create_config` changed to write `template_config_text(live=True)` (the
    production template, `mode: confirm`) instead of the paper one. Caught and restored.
    """
    config_path = tmp_path / "config.yaml"
    db_path = tmp_path / "keel.db"
    assert not config_path.exists() and not db_path.exists()  # genuinely a fresh directory

    config_result = create_config(config_path, db_path, {})
    db_result = create_database(config_path, db_path, {})
    rules_result = seed_rule_library(config_path, db_path, {})

    assert config_result.changed is True
    assert db_result.changed is True
    assert rules_result.changed is True

    config = load_config(str(config_path))
    assert config.auto_trade.mode == "paper", (
        "a fresh deployment's own written config does not default to paper -- it would place "
        "no orders differently than documented, or worse, confirm/place them"
    )

    conn = connect(str(db_path))
    try:
        version_row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert version_row is not None, "create_database left no schema_version row at all"
        assert int(version_row["version"]) == SCHEMA_VERSION

        rules = Repository(conn).get_rules()
    finally:
        conn.close()

    assert rules, "seed_rule_library left an EMPTY rule library -- the agent has nothing to run"
    non_candidates = [r for r in rules if r["status"] != "candidate"]
    assert not non_candidates, (
        f"{len(non_candidates)} seeded rule(s) are not 'candidate': a fresh deployment must not "
        "arrive with anything already promoted to trade"
    )


# ---------------------------------------------------------------------------------------------
# Pin 3 -- Acceptance 2: no attestation is ever recorded without a human-entered `source`.
# ---------------------------------------------------------------------------------------------


def _fresh_config_and_db(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()
    return config_path, db_path


def _attestation_count(db_path: Path) -> int:
    conn = connect(str(db_path))
    try:
        return len(Repository(conn).get_asset_attestations())
    finally:
        conn.close()


def test_attest_asset_records_nothing_for_a_whitespace_only_source(tmp_path: Path) -> None:
    """A `source` of `"   "` `.strip()`s to empty, so it must be treated exactly like a missing
    field -- `attest_asset`'s own wizard-facing contract (module docstring: "an unsourced
    attestation is refused exactly like a missing one").

    Mutation-verified: `"source"` dropped from `attest_asset`'s `required` tuple in
    `keel/commands/setup.py`. Caught (the whitespace source was then written to the table) and
    restored.
    """
    config_path, db_path = _fresh_config_and_db(tmp_path)
    result = attest_asset(
        config_path,
        db_path,
        {
            "asset": "BTC",
            "sector": "payments",
            "backing": "native",
            "pays_yield": "no",
            "source": "   ",
            "attested_by": "operator",
        },
    )
    assert result.changed is False, result.message
    assert _attestation_count(db_path) == 0


def test_attest_asset_records_nothing_for_an_absent_source_key(tmp_path: Path) -> None:
    """The same refusal when `source` is not in `values` at all -- a front-end bug that forgets
    to submit the field must fail exactly like one that submits it blank, not raise and not
    silently write a `None`."""
    config_path, db_path = _fresh_config_and_db(tmp_path)
    result = attest_asset(
        config_path,
        db_path,
        {
            "asset": "BTC",
            "sector": "payments",
            "backing": "native",
            "pays_yield": "no",
            "attested_by": "operator",
            # "source" intentionally omitted.
        },
    )
    assert result.changed is False, result.message
    assert _attestation_count(db_path) == 0


def test_attest_asset_positive_control_a_complete_attestation_is_recorded(
    tmp_path: Path,
) -> None:
    """The other two tests would also pass against an `attest_asset` that records NOTHING,
    ever -- a rewrite that deleted the feature entirely. This is the control that rules that
    out: a real, fully-supplied attestation (a real `source` included) must actually be written,
    exactly once, with the asset it named."""
    config_path, db_path = _fresh_config_and_db(tmp_path)
    result = attest_asset(
        config_path,
        db_path,
        {
            "asset": "BTC",
            "sector": "payments",
            "backing": "native",
            "pays_yield": "no",
            "source": "https://example.org/btc-classification",
            "attested_by": "operator",
        },
    )
    assert result.changed is True, result.message

    conn = connect(str(db_path))
    try:
        rows = Repository(conn).get_asset_attestations()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["asset"] == "BTC"
    assert rows[0]["source"] == "https://example.org/btc-classification"


# -- the engine-side half: keel.compliance.screen.screen_asset --------------------------------


def _passing_market_facts() -> screen_mod.MarketFacts:
    """Facts built to clear every CRITERION OTHER than the attestation/source one, so a
    screen_asset call below has exactly one thing left to refuse on -- the source check -- and a
    test reading `result.failures` is reading a signal, not noise from unrelated criteria."""
    policy = screen_mod.ScreenPolicy()
    return screen_mod.MarketFacts(
        asset="BTC",
        daily_bars=policy.min_daily_bars + 10,
        median_daily_volume=policy.min_median_daily_volume + Decimal(1),
        quotable_in_settlement_currency=True,
        product_id="BTC-USD",
        venue="coinbase",
    )


def _passing_instrument() -> screen_mod.InstrumentAttestation:
    return screen_mod.InstrumentAttestation(
        venue="coinbase",
        product_id="BTC-USD",
        wrapper=screen_mod.WRAPPER_SPOT,
        source="https://coinbase.com/docs/products",
        attested_by="operator",
        attested_at=0,
    )


def test_screen_asset_refuses_a_missing_attestation_for_a_stated_reason() -> None:
    """`attestation=None` must be REFUSED, and refused with the specific "MISSING" reason --
    not merely `admitted is False`, which a screen that refused every input for any reason (or
    no reason) would also satisfy."""
    result = screen_mod.screen_asset(
        _passing_market_facts(), None, instrument=_passing_instrument()
    )
    assert result.admitted is False
    assert any("attestation: MISSING" in failure for failure in result.failures), result.failures


def test_screen_asset_refuses_a_whitespace_only_source_for_a_stated_reason() -> None:
    """The half the engine owns, mirroring the wizard's own rule: an attestation that exists but
    whose `source` is whitespace must be refused with the SAME "no source recorded" reason text
    as a never-populated one -- see `keel/compliance/screen.py:360-361`.

    Mutation-verified: the `if not attestation.source.strip(): failures.append(...)` check
    deleted from `screen_asset`. Caught (the whitespace-sourced attestation was then ADMITTED,
    with an empty failures list) and restored.
    """
    attestation = screen_mod.AssetAttestation(
        asset="BTC",
        sector="payments",
        backing=screen_mod.BACKING_NATIVE,
        pays_yield=False,
        source="   ",
        attested_by="operator",
        attested_at=0,
    )
    result = screen_mod.screen_asset(
        _passing_market_facts(), attestation, instrument=_passing_instrument()
    )
    assert result.admitted is False
    assert any(
        "attestation: no source recorded" in failure for failure in result.failures
    ), result.failures


def test_screen_asset_positive_control_a_complete_attestation_is_admitted() -> None:
    """The control for the previous two: with every criterion satisfied, including a real
    `source`, the screen must actually ADMIT -- ruling out a `screen_asset` that has quietly been
    made to refuse everything regardless of input, which would otherwise let both refusal pins
    above pass for the wrong reason."""
    attestation = screen_mod.AssetAttestation(
        asset="BTC",
        sector="payments",
        backing=screen_mod.BACKING_NATIVE,
        pays_yield=False,
        source="https://example.org/btc-classification",
        attested_by="operator",
        attested_at=0,
    )
    result = screen_mod.screen_asset(
        _passing_market_facts(), attestation, instrument=_passing_instrument()
    )
    assert result.admitted is True, result.failures
    assert result.failures == []


# ---------------------------------------------------------------------------------------------
# Pin 4 -- migrations run idempotently on every start, not only first run.
# ---------------------------------------------------------------------------------------------


def test_migrating_an_already_current_database_changes_nothing(tmp_path: Path) -> None:
    """A database already migrated to `SCHEMA_VERSION`, with real rows in it, must come out of a
    second `migrate()` call byte-for-byte (row-for-row) identical -- the behavioural half of the
    "self-healing, every start" property. (The structural half -- that the call site is actually
    unconditional rather than merely happening to be a no-op here -- is the next test.)
    """
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()

    now_ts = int(time.time())
    conn = connect(str(db_path))
    try:
        repo = Repository(conn)
        repo.set_state("probe", "a value that must survive a re-migration untouched")
        repo.upsert_asset_attestation(
            asset="BTC",
            sector="payments",
            backing="native",
            pays_yield=False,
            source="https://example.org/btc-classification",
            attested_by="operator",
            attested_at=now_ts,
        )
    finally:
        conn.close()

    dump_before = _dump_tables(db_path)

    conn = connect(str(db_path))
    migrate(conn)
    conn.close()

    dump_after = _dump_tables(db_path)
    assert dump_after == dump_before, (
        "re-running migrate() on an already-current database changed a row -- an upgrade that "
        "migrates on every start must not also re-touch data that needed no migrating"
    )

    conn = connect(str(db_path))
    try:
        version_row = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert int(version_row["version"]) == SCHEMA_VERSION


def test_open_repo_calls_migrate_unconditionally() -> None:
    """The structural half: `_common._open_repo` must call `migrate(conn)` with no `if` between
    the call and the function's top level. A behavioural test alone cannot tell "unconditional
    and therefore a no-op on a current database" apart from "wrapped in `if not db_existed:` and
    therefore ALSO a no-op on a current database" -- both pass the previous test identically, and
    only one of them self-heals a database that is current in schema_version but was, say,
    hand-edited or restored from an old backup with a stale row a later migration step would have
    fixed. An AST scan is the honest way to tell them apart: it reads the actual shape of the
    function rather than trusting that today's test data happens to exercise the gap.

    Mutation-verified: `_open_repo` rewritten to compute a (meaningless, always-true-shaped)
    `db_existed` flag and call `migrate(conn)` only `if not db_existed:`. Caught (this test
    failed on `finder.guarded`) and `keel/commands/_common.py` was restored to its original text
    -- `git diff` is clean of it in this commit.
    """
    source = python_inspect.getsource(_common._open_repo)
    tree = ast.parse(source)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef), "expected _open_repo to parse as one function def"

    class _MigrateCallFinder(ast.NodeVisitor):
        def __init__(self) -> None:
            self.if_depth = 0
            self.guarded = False
            self.unconditional = False

        def visit_If(self, node: ast.If) -> None:
            self.if_depth += 1
            self.generic_visit(node)
            self.if_depth -= 1

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "migrate":
                if self.if_depth > 0:
                    self.guarded = True
                else:
                    self.unconditional = True
            self.generic_visit(node)

    finder = _MigrateCallFinder()
    finder.visit(func)

    assert finder.unconditional or finder.guarded, (
        "_open_repo no longer calls migrate() anywhere -- this pin cannot tell a self-healing "
        "migration apart from a silently dropped one, because there is no call left to inspect"
    )
    assert not finder.guarded, (
        "_open_repo's migrate(conn) call now sits inside an `if` -- a repository opened outside "
        "that branch will never be migrated, and an upgrade stops self-healing"
    )
