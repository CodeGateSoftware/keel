"""The first-run checklist (#437, D4).

The pin that matters most here is `test_every_table_this_module_reads_exists`. I wrote
`"subscriptions"` and `"state"` in the first draft of `keel/commands/setup.py`; the real tables
are `broker_subscriptions` and `agent_state`. Nothing raised. The checklist simply reported an
attested subscription and an attested withdrawal capability as MISSING on a deployment where both
had been done -- which is the worst way this module can fail, because it sends an operator to
redo work they have already done, and a wrongly-missing item looks exactly like a genuinely
missing one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from keel.commands.setup import (
    READ_TABLES,
    STEPS,
    WITHDRAWALS_STATE_KEY,
    Stage,
    StepKind,
    inspect,
    render_lines,
)
from keel.data.db import connect, migrate
from tests.conftest import VALID_CONFIG_YAML


@pytest.fixture
def fresh(tmp_path: Path) -> tuple[Path, Path]:
    """A migrated database and a valid config -- the state `keel init` leaves behind."""
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    return config_path, db_path


# -- the pin that would have caught the bug ---------------------------------------------------


def test_every_table_this_module_reads_exists(fresh: tuple[Path, Path]) -> None:
    """Against a freshly migrated database, so a renamed or removed table fails HERE.

    Without this, a wrong table name is silent: `_table_exists` answers False, the step reports
    as not done, and the checklist confidently tells an operator to redo something finished."""
    _config_path, db_path = fresh
    conn = sqlite3.connect(str(db_path))
    try:
        present = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    missing = sorted(set(READ_TABLES) - present)
    assert not missing, (
        f"keel/commands/setup.py reads tables that do not exist: {missing}. Every step that "
        "depends on one is now silently reported as NOT DONE."
    )


def test_the_withdrawals_state_key_is_the_one_the_command_writes(fresh: tuple[Path, Path]) -> None:
    """Same failure mode one level down: a right table with a wrong key reads as unattested."""
    config_path, db_path = fresh
    conn = connect(str(db_path))
    try:
        from keel.data.repository import Repository

        Repository(conn).set_state(WITHDRAWALS_STATE_KEY, True)
        conn.commit()
    finally:
        conn.close()

    state = inspect(config_path, db_path)
    step = next(s for s in state.states if s.step.key == "withdrawals_attested")
    assert step.done is True, step.detail


# -- what a brand-new machine looks like ------------------------------------------------------


def test_nothing_at_all_is_new_and_every_step_is_outstanding(tmp_path: Path) -> None:
    state = inspect(tmp_path / "config.yaml", tmp_path / "keel.db")
    assert state.is_new
    assert all(item.blocking for item in state.states)
    assert state.next_step is not None
    assert state.next_step.step.key == "config"


def test_a_missing_database_makes_its_dependants_not_done_rather_than_unknown(
    tmp_path: Path,
) -> None:
    """`None` means "could not be determined", and it must not be used where the answer is
    knowable. With no database there are no rules, no candles and no attestations -- reporting
    that as undetermined tells a first-run user their deployment is in an unclear state when it
    is in a perfectly clear one."""
    state = inspect(tmp_path / "config.yaml", tmp_path / "keel.db")
    for item in state.states:
        assert item.done is False, (item.step.key, item.done, item.detail)


def test_a_half_built_deployment_is_not_new(fresh: tuple[Path, Path]) -> None:
    """`is_new` gates "offer to create a deployment here". A config someone spent an afternoon
    editing must never satisfy it."""
    config_path, db_path = fresh
    assert not inspect(config_path, db_path).is_new
    assert not inspect(config_path, config_path.parent / "absent.db").is_new
    assert not inspect(config_path.parent / "absent.yaml", db_path).is_new


# -- what it observes ---------------------------------------------------------------------------


def test_a_migrated_database_with_no_rules_reports_the_library_empty(
    fresh: tuple[Path, Path],
) -> None:
    config_path, db_path = fresh
    state = inspect(config_path, db_path)
    done = {item.step.key: item.done for item in state.states}
    assert done["config"] is True
    assert done["database"] is True
    assert done["rules"] is False
    assert done["rule_promoted"] is False
    assert done["market_data"] is False


def test_assets_are_measured_against_the_allowlist_not_against_a_count(
    fresh: tuple[Path, Path],
) -> None:
    """Six attestations mean nothing if the seventh allowlisted asset is unattested: that asset
    is the one the rails will veto, and a checklist reporting "6 attested" would look finished."""
    config_path, db_path = fresh
    conn = connect(str(db_path))
    try:
        from keel.data.repository import Repository

        repo = Repository(conn)
        for asset in ("BTC", "ETH"):
            repo.upsert_asset_attestation(
                asset=asset,
                sector="crypto",
                backing="none",
                pays_yield=False,
                source="a cited source",
                attested_by="tester",
                attested_at=1,
            )
        conn.commit()
    finally:
        conn.close()

    # VALID_CONFIG_YAML allowlists BTC, ETH and PAXG.
    state = inspect(config_path, db_path)
    item = next(s for s in state.states if s.step.key == "assets_attested")
    assert item.done is False
    assert "PAXG" in item.detail
    assert "BTC" not in item.detail


def test_an_unparseable_config_is_reported_not_raised(tmp_path: Path) -> None:
    """This runs to DESCRIBE a deployment, including a broken one. A traceback here would
    replace a checklist that says "your config will not parse" with a worse way of saying so."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("allowlist: [BTC\n  not: valid: yaml")
    state = inspect(config_path, tmp_path / "keel.db")
    item = next(s for s in state.states if s.step.key == "config")
    assert item.done is False
    assert "could not be parsed" in item.detail


def test_inspect_writes_nothing(tmp_path: Path) -> None:
    """It runs on every page load of an auto-refreshing browser view, against a database an
    agent may be mid-cycle on. It opens read-only and must not create a database by looking."""
    config_path = tmp_path / "config.yaml"
    db_path = tmp_path / "keel.db"
    inspect(config_path, db_path)
    assert not db_path.exists()
    assert not config_path.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == []


# -- the ceremony itself --------------------------------------------------------------------


def test_the_off_venue_steps_are_never_reported_as_done(fresh: tuple[Path, Path]) -> None:
    """keel cannot see whether USDC Rewards is off -- the venue's API does not expose enrolment
    status. The operator runbook is explicit that a green check verifying nothing is worse than
    an honest manual step, because it turns an open risk into a false assurance. So there is no
    database state that can make these tick."""
    config_path, db_path = fresh
    state = inspect(config_path, db_path)
    for item in state.states:
        if item.step.kind is StepKind.OFF_VENUE:
            assert item.done is False


def test_ready_for_live_is_never_true(fresh: tuple[Path, Path]) -> None:
    """Follows from the above, and is worth asserting on its own: the last word on going live
    belongs to the operator who checked the venue dashboard, not to a function that cannot see
    it."""
    config_path, db_path = fresh
    assert inspect(config_path, db_path).ready_for(Stage.LIVE) is False


def test_every_step_says_what_it_is_and_how_to_do_it() -> None:
    keys = [step.key for step in STEPS]
    assert len(keys) == len(set(keys))
    for step in STEPS:
        assert len(step.why) > 40, step.key
        assert step.how, step.key
        assert step.kind in set(StepKind)
        assert step.stage in set(Stage)


def test_the_paper_stage_contains_no_off_venue_step() -> None:
    """The paper stage is what a first-run wizard aims at, and it must be reachable without
    leaving the machine. An off-venue step in it would make "reaches a working paper deployment"
    depend on something keel can neither do nor check."""
    for step in STEPS:
        if step.stage is Stage.PAPER:
            assert step.kind is not StepKind.OFF_VENUE, step.key


def test_every_judgement_step_names_a_source_or_a_human_decision() -> None:
    """Attestations are human classifications with a cited source; an unsourced one is refused
    exactly like a missing one. A judgement step whose `why` did not say so would invite a
    wizard to default it."""
    for step in STEPS:
        if step.kind is StepKind.JUDGEMENT:
            assert any(
                word in step.why.lower()
                for word in ("human", "your choice", "yours", "judgement", "you")
            ), step.key


def test_the_rendered_checklist_names_every_step_and_the_next_action(
    tmp_path: Path,
) -> None:
    text = "\n".join(render_lines(inspect(tmp_path / "config.yaml", tmp_path / "keel.db")))
    for step in STEPS:
        assert step.title in text, step.key
    assert text.rstrip().splitlines()[-1].startswith("next: ")


def test_the_promotion_step_says_a_refusal_is_the_engine_working() -> None:
    """Measured on a real deployment: a freshly-seeded turtle_breakout promoted from the browser
    was refused with `n_trades 12 < 100`, `win_rate 0.5 < 0.55`, and the overfitting check never
    run. That is correct, and it is also the single most likely thing a first-run user will see
    on this step.

    Left unexplained it reads as a broken button on a checklist that has gone green everywhere
    else. The step's own text has to say that a refusal is the gate doing its job, that this item
    can stay outstanding for a long time, and where the deliberate bypass lives -- at a terminal,
    on the record."""
    step = next(s for s in STEPS if s.key == "rule_promoted")
    lowered = step.why.lower()
    assert "refuse" in lowered
    assert "not a fault" in lowered or "engine working" in lowered
    assert "--force" in step.why
    assert "terminal" in lowered


def test_ready_for_paper_does_not_claim_a_fresh_install_is_ready(fresh: tuple[Path, Path]) -> None:
    """ "Set up" and "has a rule worth running" are different states, and a checklist that
    conflated them would call a deployment ready on the strength of a rule the gate refused."""
    config_path, db_path = fresh
    assert inspect(config_path, db_path).ready_for(Stage.PAPER) is False
