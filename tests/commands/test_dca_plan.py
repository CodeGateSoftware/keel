"""`keel.commands.dca_plan` -- the DCA plan SERVICE (no click).

Pins, in order: input parsing (percent-shaped and non-finite input refused), rail 14's cap read
exactly as `guards.check` reads it (parity driven through `guards.check` itself), the apply
command's no-exponent spelling, then (Tasks 2-5) the universe, the amounts, the rendering and
the all-or-nothing write.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest
from keel_core.subscription import SubscriptionStatus

from keel.commands import dca_plan as dca_mod
from keel.commands.dca_plan import (
    DcaPlanError,
    PlanInputs,
    apply_command,
    monthly_buy_cap,
    parse_plan_inputs,
    parse_weight,
)
from keel.execution import guards
from tests.conftest import attest_subscription
from tests.execution.test_guards import NOW_TS, _intent, _keys, _roomy_config, _unattested_repo


def test_the_service_module_imports_no_click() -> None:
    """R13: a pure service. The web layer imports this module; click is a front-end's."""
    tree = ast.parse(Path(dca_mod.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "click" not in imported
    assert "keel" in imported  # the scan saw real imports -- it is not vacuous


def test_parse_plan_inputs_accepts_a_budget_and_a_fraction() -> None:
    assert parse_plan_inputs("500", "0.1") == PlanInputs(Decimal("500"), Decimal("0.1"), 7)
    assert parse_plan_inputs(" 500 ", "0", cadence_days=14).cadence_days == 14


@pytest.mark.parametrize(
    ("budget", "buffer", "needle"),
    [
        ("0", "0.1", "budget must be positive"),
        ("-5", "0.1", "budget must be positive"),
        ("abc", "0.1", "is not a number"),
        ("NaN", "0.1", "not a finite number"),
        ("Infinity", "0.1", "not a finite number"),
        ("500", "10", "0.1 means"),  # percent-shaped: refused with the fraction hint (R11)
        ("500", "1", "0.1 means"),  # 1 would plan a spend of zero
        ("500", "-0.1", "0.1 means"),
    ],
)
def test_parse_plan_inputs_refuses_with_a_named_reason(
    budget: str, buffer: str, needle: str
) -> None:
    with pytest.raises(DcaPlanError) as excinfo:
        parse_plan_inputs(budget, buffer)
    assert needle in str(excinfo.value)


def test_parse_plan_inputs_refuses_a_non_positive_cadence() -> None:
    with pytest.raises(DcaPlanError, match="cadence"):
        parse_plan_inputs("500", "0.1", cadence_days=0)


def test_parse_weight() -> None:
    assert parse_weight("0.25", "BTC") == Decimal("0.25")
    assert parse_weight("0", "BTC") == Decimal("0")
    for bad in ("-1", "x", "NaN"):
        with pytest.raises(DcaPlanError, match="BTC"):
            parse_weight(bad, "BTC")


# -- rail 14's cap, read the way the rail reads it --------------------------------------------


@pytest.mark.parametrize(
    ("setup", "expected_cap", "reason_needle"),
    [
        ("none", Decimal("0"), "no subscription has been attested"),
        ("active", Decimal("500"), ""),
        ("suspect", Decimal("0"), "suspect"),
        ("lapsed", Decimal("0"), "lapsed"),
        ("overdue", Decimal("0"), "overdue"),
    ],
)
def test_the_cap_is_the_allowance_rail_14_enforces(
    setup: str, expected_cap: Decimal, reason_needle: str
) -> None:
    """Parity driven through `guards.check` ITSELF (R3): a buy of exactly the cap passes rail 14
    and a buy one cent over is vetoed by it. A copy that drifted from the rail fails here."""
    repo = _unattested_repo()
    if setup != "none":
        status = {
            "active": SubscriptionStatus.ACTIVE,
            "suspect": SubscriptionStatus.SUSPECT,
            "lapsed": SubscriptionStatus.LAPSED,
            "overdue": SubscriptionStatus.ACTIVE,
        }[setup]
        attest_subscription(
            repo,
            now_ts=NOW_TS,
            free_volume_usd=Decimal("500"),
            status=status,
            attest_due_ts=NOW_TS - 1 if setup == "overdue" else None,
        )
    config = _roomy_config()

    cap = monthly_buy_cap(repo, config, venue="coinbase", now_ts=NOW_TS)

    assert cap.allowance_usd == expected_cap
    assert cap.venue == "coinbase"
    assert reason_needle in cap.degraded_reason
    assert cap.in_force is (reason_needle == "")
    over = guards.check(_intent(notional=expected_cap + Decimal("0.01")), repo, config, NOW_TS)
    assert _keys(over) & {"monthly_subscription_allowance", "subscription_unattested"}
    if expected_cap > 0:
        at = guards.check(_intent(notional=expected_cap), repo, config, NOW_TS)
        assert not _keys(at) & {"monthly_subscription_allowance", "subscription_unattested"}


def test_an_unlimited_tier_has_no_cap() -> None:
    repo = _unattested_repo()
    attest_subscription(repo, now_ts=NOW_TS, free_volume_usd=None)
    cap = monthly_buy_cap(repo, _roomy_config(), venue="coinbase", now_ts=NOW_TS)
    assert cap.allowance_usd is None
    assert cap.in_force


def test_the_cap_is_read_for_the_venue_asked_about() -> None:
    """The DEPLOYMENT'S venue (rail 14's key), never a hardcoded coinbase."""
    repo = _unattested_repo()
    attest_subscription(repo, now_ts=NOW_TS, free_volume_usd=Decimal("900"), venue="alpaca")
    assert monthly_buy_cap(repo, _roomy_config(), venue="alpaca", now_ts=NOW_TS).allowance_usd == (
        Decimal("900")
    )
    assert monthly_buy_cap(
        repo, _roomy_config(), venue="coinbase", now_ts=NOW_TS
    ).allowance_usd == (Decimal("0"))


# -- the command string --------------------------------------------------------------------------


def test_apply_command_never_spells_an_exponent() -> None:
    """`Decimal("1e3")` is `1E+3`; a command carrying that would still parse, but a reader copying
    it sees a number they did not type. `format(..., "f")` is the only spelling used."""
    inputs = parse_plan_inputs("1e3", "0.10")
    assert apply_command(inputs) == "keel dca plan --budget 1000 --buffer-pct 0.10"


def test_apply_command_carries_cadence_only_when_not_the_default() -> None:
    assert "--cadence-days" not in apply_command(parse_plan_inputs("500", "0.1"))
    assert apply_command(parse_plan_inputs("500", "0.1", cadence_days=14)).endswith(
        "--cadence-days 14"
    )


def test_apply_command_names_the_deployment_before_the_subcommand() -> None:
    """R17: global options precede the group, and paths are shell-quoted."""
    command = apply_command(
        parse_plan_inputs("500", "0.1"), config_path="/a b/config.yaml", db_path="/x/keel.db"
    )
    assert command == (
        "keel --config '/a b/config.yaml' --db /x/keel.db dca plan --budget 500 --buffer-pct 0.1"
    )


def test_apply_command_without_inputs_is_the_template() -> None:
    assert apply_command(None) == "keel dca plan --budget <monthly USD> --buffer-pct <fraction>"
