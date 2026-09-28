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
from keel_core.config import load_config
from keel_core.subscription import SubscriptionStatus

from keel.commands import dca_plan as dca_mod
from keel.commands.dca_plan import (
    DcaPlanError,
    ExcludedAsset,
    PlanInputs,
    apply_command,
    monthly_buy_cap,
    parse_plan_inputs,
    parse_weight,
    select_universe,
)
from keel.compliance.screen import MarketFacts, ScreenResult
from keel.config import Config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
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


# -- the universe: admitted, weighted, allowlisted, no existing DCA rule -------------------------


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _screen(*rejected: str):
    """A fake `screen_fn` admitting every product except the named ASSETS. Injected exactly as
    `build_screen_report` takes it -- no candles or attestations needed to reach the plan."""

    def screen_fn(repo: Repository, product: str, quote: str) -> tuple[MarketFacts, ScreenResult]:
        asset = product.split("-")[0]
        facts = MarketFacts(
            asset=asset,
            daily_bars=2000,
            median_daily_volume=Decimal("5000000"),
            quotable_in_settlement_currency=True,
            product_id=product,
            venue="coinbase",
        )
        admitted = asset not in rejected
        failures = [] if admitted else ["history: 12 bars < 1460"]
        return facts, ScreenResult(asset=asset, admitted=admitted, failures=failures)

    return screen_fn


def _config(valid_config_path: Path, **overrides) -> Config:
    """conftest's VALID_CONFIG_YAML: allowlist BTC/ETH/PAXG, weights .40/.30/.30, taker 0.012 by
    default, max_per_order_usd 100, dca.budget_usd 50."""
    from dataclasses import replace

    return replace(load_config(str(valid_config_path)), **overrides)


def _insert_dca(
    repo: Repository, product: str, status: str, budget: str = "40", dip: str = "0"
) -> int:
    return repo.insert_rule(
        "dca",
        {
            "product_id": product,
            "cadence_days": 7,
            "budget_usd": budget,
            "dip_bonus_pct": dip,
            "lookback_days": 90,
        },
        status=status,
        now_ts=NOW_TS,
    )


def test_every_admitted_weighted_allowlisted_asset_is_allocated(valid_config_path: Path) -> None:
    universe = select_universe(_repo(), _config(valid_config_path), screen_fn=_screen())
    assert [(a.asset, a.product_id, a.weight) for a in universe.allocations] == [
        ("BTC", "BTC-USD", Decimal("0.4")),
        ("ETH", "ETH-USD", Decimal("0.3")),
        ("PAXG", "PAXG-USD", Decimal("0.3")),
    ]
    assert universe.excluded == ()
    assert sum(a.weight for a in universe.allocations) == Decimal("1")


def test_a_rejected_asset_is_excluded_with_the_screens_reason_and_weights_renormalise(
    valid_config_path: Path,
) -> None:
    universe = select_universe(_repo(), _config(valid_config_path), screen_fn=_screen("BTC"))
    assert [a.asset for a in universe.allocations] == ["ETH", "PAXG"]
    assert [a.weight for a in universe.allocations] == [Decimal("0.5"), Decimal("0.5")]
    assert universe.excluded == (
        ExcludedAsset("BTC", ("not_admitted",), "history: 12 bars < 1460"),
    )


def test_an_asset_with_no_weight_is_excluded_as_such(valid_config_path: Path) -> None:
    config = _config(
        valid_config_path, target_weights={"BTC": Decimal("0.5"), "ETH": Decimal("0.5")}
    )
    universe = select_universe(_repo(), config, screen_fn=_screen())
    assert [a.asset for a in universe.allocations] == ["BTC", "ETH"]
    assert universe.excluded == (ExcludedAsset("PAXG", ("no_weight",), ""),)


def test_an_existing_non_disabled_dca_rule_leaves_its_asset_untouched(
    valid_config_path: Path,
) -> None:
    """Rule 6 on the live account ($/week BTC) is the prime case: listed, unchanged, no new rule."""
    repo = _repo()
    rule_id = _insert_dca(repo, "BTC-USD", "live")
    _insert_dca(repo, "ETH-USD", "disabled")  # disabled does NOT count as existing

    universe = select_universe(repo, _config(valid_config_path), screen_fn=_screen())

    assert [a.asset for a in universe.allocations] == ["ETH", "PAXG"]
    assert universe.excluded == (ExcludedAsset("BTC", ("has_dca_rule",), f"rule {rule_id} (live)"),)
    assert [(e.rule_id, e.asset, e.status, e.budget_usd) for e in universe.existing] == [
        (rule_id, "BTC", "live", Decimal("40"))
    ]


def test_an_existing_rules_dip_bonus_pct_is_carried(valid_config_path: Path) -> None:
    """Coordinator amendment: R7 (Task 3) needs each existing row's own `dip_bonus_pct`."""
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "live", dip="2")

    universe = select_universe(repo, _config(valid_config_path), screen_fn=_screen())

    assert universe.existing[0].dip_bonus_pct == Decimal("2")


def test_every_reason_that_applies_is_named(valid_config_path: Path) -> None:
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "candidate")
    config = _config(valid_config_path, target_weights={"ETH": Decimal("1")})
    universe = select_universe(repo, config, screen_fn=_screen("BTC"))
    btc = next(e for e in universe.excluded if e.asset == "BTC")
    assert btc.reasons == ("not_admitted", "no_weight", "has_dca_rule")


def test_a_weight_for_an_asset_off_the_allowlist_is_reported_not_dropped(
    valid_config_path: Path,
) -> None:
    """R4 / Review Focus 5: a weight never vanishes without a line saying why."""
    config = _config(
        valid_config_path,
        target_weights={
            "BTC": Decimal("0.5"),
            "ETH": Decimal("0.3"),
            "PAXG": Decimal("0.1"),
            "FET": Decimal("0.1"),
        },
    )
    universe = select_universe(_repo(), config, screen_fn=_screen())
    assert ExcludedAsset("FET", ("not_on_allowlist",), "") in universe.excluded


def test_weight_keys_are_matched_case_insensitively(valid_config_path: Path) -> None:
    """Review Focus 5: `btc: 0.4` in YAML is BTC's weight, not an off-allowlist asset."""
    config = _config(
        valid_config_path,
        target_weights={"btc": Decimal("0.4"), "Eth": Decimal("0.3"), "PAXG": Decimal("0.3")},
    )
    universe = select_universe(_repo(), config, screen_fn=_screen())
    assert [a.asset for a in universe.allocations] == ["BTC", "ETH", "PAXG"]
    assert universe.excluded == ()


def test_editable_assets_are_admitted_allowlisted_and_without_a_dca_rule(
    valid_config_path: Path,
) -> None:
    """R14: `[E]` cannot re-admit a rejected asset or stack a rule on an existing one."""
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "paper")
    universe = select_universe(repo, _config(valid_config_path), screen_fn=_screen("PAXG"))
    assert universe.editable == (("ETH", Decimal("0.3")),)


def test_a_weights_override_replaces_config_weights_and_zero_excludes(
    valid_config_path: Path,
) -> None:
    universe = select_universe(
        _repo(),
        _config(valid_config_path),
        screen_fn=_screen(),
        weights_override={"BTC": Decimal("1"), "ETH": Decimal("1"), "PAXG": Decimal("0")},
    )
    assert [(a.asset, a.weight) for a in universe.allocations] == [
        ("BTC", Decimal("0.5")),
        ("ETH", Decimal("0.5")),
    ]
    assert ExcludedAsset("PAXG", ("no_weight",), "set to 0 in this session") in universe.excluded
    # Edits persist into the next edit's defaults:
    assert dict(universe.editable)["PAXG"] == Decimal("0")


def test_a_weights_override_for_a_non_editable_asset_is_refused(valid_config_path: Path) -> None:
    with pytest.raises(DcaPlanError, match="BTC"):
        select_universe(
            _repo(),
            _config(valid_config_path),
            screen_fn=_screen("BTC"),
            weights_override={"BTC": Decimal("1")},
        )


def test_selecting_the_universe_writes_nothing(valid_config_path: Path) -> None:
    repo = _repo()
    before = repo._conn.total_changes  # type: ignore[attr-defined]
    select_universe(repo, _config(valid_config_path), screen_fn=_screen())
    assert repo._conn.total_changes == before  # type: ignore[attr-defined]
