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
    RAIL14_NOTE,
    DcaPlanError,
    ExcludedAsset,
    PlanInputs,
    apply_command,
    build_dca_plan,
    monthly_buy_cap,
    parse_plan_inputs,
    parse_weight,
    render_dca_plan,
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


# -- amounts, fees, the cap check, blockers and warnings (`build_dca_plan`) -----------------------


def replace_caps(valid_config_path: Path, **caps):
    from dataclasses import replace

    return replace(load_config(str(valid_config_path)).caps, **caps)


def _plan(
    valid_config_path: Path,
    repo: Repository | None = None,
    *,
    cap: str | None = "500",
    rejected: tuple[str, ...] = (),
    budget: str = "500",
    buffer: str = "0.1",
    weights_override=None,
    **config_overrides,
):
    repo = repo or _repo()
    if cap is not None:
        attest_subscription(repo, now_ts=NOW_TS, free_volume_usd=Decimal(cap))
    return build_dca_plan(
        repo,
        _config(valid_config_path, **config_overrides),
        parse_plan_inputs(budget, buffer),
        venue="coinbase",
        now_ts=NOW_TS,
        screen_fn=_screen(*rejected),
        weights_override=weights_override,
    )


def test_the_worked_example(valid_config_path: Path) -> None:
    plan = _plan(valid_config_path)
    assert plan.spend_usd == Decimal("450.00")
    assert plan.buffer_usd == Decimal("50.00")
    assert [
        (b.asset, b.cadence_days, b.per_buy_usd, b.monthly_usd, b.est_monthly_fee_usd)
        for b in plan.buys
    ] == [
        ("BTC", 7, Decimal("41.39"), Decimal("179.97"), Decimal("2.16")),
        ("ETH", 7, Decimal("31.04"), Decimal("134.96"), Decimal("1.62")),
        ("PAXG", 7, Decimal("31.04"), Decimal("134.96"), Decimal("1.62")),
    ]
    assert plan.buy_count == 3
    assert [b.weight_pct for b in plan.buys] == [Decimal("40.0"), Decimal("30.0"), Decimal("30.0")]
    assert plan.planned_monthly_usd == Decimal("449.89")
    assert plan.est_monthly_fees_usd == Decimal("5.40")
    assert plan.taker_pct == Decimal("0.012")
    assert plan.taker_pct_display == Decimal("1.200")
    assert plan.approvable, plan.blockers


def test_the_planned_total_never_exceeds_the_spend_that_was_checked(
    valid_config_path: Path,
) -> None:
    """R5: every rounding step errs toward spending less."""
    for budget in ("37", "101.01", "999.99", "12345"):
        plan = _plan(valid_config_path, budget=budget, buffer="0.05", cap=None)
        assert plan.planned_monthly_usd <= plan.spend_usd


def test_the_minimum_order_size_is_unknown_and_said_so(valid_config_path: Path) -> None:
    plan = _plan(valid_config_path)
    assert all(b.min_order_usd is None for b in plan.buys)
    assert any("minimum order size" in w for w in plan.warnings)


def test_spend_over_the_cap_is_a_blocker_naming_it_a_buy_cap(valid_config_path: Path) -> None:
    plan = _plan(valid_config_path, cap="400")  # spend 450 > 400
    assert not plan.approvable
    (blocker,) = [b for b in plan.blockers if "rail 14" in b]
    assert "450.00" in blocker and "400" in blocker and "buy cap" in blocker


def test_an_unattested_venue_blocks_with_the_attest_command(valid_config_path: Path) -> None:
    """Review Focus 2: not "450 exceeds 0" -- the reason and the fix."""
    plan = _plan(valid_config_path, cap=None)
    (blocker,) = [b for b in plan.blockers if "rail 14" in b]
    assert "no subscription has been attested" in blocker
    assert "keel subscription attest --venue coinbase" in blocker


def test_an_unlimited_tier_is_never_a_cap_blocker(valid_config_path: Path) -> None:
    repo = _repo()
    attest_subscription(repo, now_ts=NOW_TS, free_volume_usd=None)
    plan = _plan(
        valid_config_path,
        repo,
        cap=None,
        budget="100000",
        buffer="0",
        caps=replace_caps(valid_config_path, max_per_order_usd=Decimal("1000000")),
    )
    assert not [b for b in plan.blockers if "rail 14" in b]


def test_a_per_buy_that_rounds_to_zero_is_a_named_blocker(valid_config_path: Path) -> None:
    """Review Focus 1: named, not a traceback from Dca.__init__ and not a dropped asset."""
    plan = _plan(
        valid_config_path,
        budget="1",
        buffer="0",
        weights_override={
            "BTC": Decimal("0.99"),
            "ETH": Decimal("0.005"),
            "PAXG": Decimal("0.005"),
        },
    )
    assert [b.asset for b in plan.buys] == ["BTC", "ETH", "PAXG"]  # not dropped
    assert any("ETH" in b and "$0.00" in b for b in plan.blockers)
    assert not plan.approvable


def test_a_per_buy_over_the_per_order_cap_is_a_blocker(valid_config_path: Path) -> None:
    """R8: conftest's max_per_order_usd is 100; BTC's per-buy at a 5000 budget is 459.95."""
    plan = _plan(valid_config_path, budget="5000", buffer="0", cap="100000")
    assert any("BTC" in b and "max_per_order_usd" in b for b in plan.blockers)


def test_an_empty_universe_is_a_blocker(valid_config_path: Path) -> None:
    plan = _plan(valid_config_path, rejected=("BTC", "ETH", "PAXG"))
    assert plan.buys == () and plan.buy_count == 0
    assert any("no asset is eligible" in b for b in plan.blockers)


def test_existing_live_dca_spend_is_warned_against_the_cap_at_each_rules_own_amount(
    valid_config_path: Path,
) -> None:
    """R7 amended (#843): a live row's commitment is that rule's OWN `budget_usd` -- what the
    live executor actually spends per buy since #843 -- not `config.dca.budget_usd`. BTC live
    weekly 40: 40 x 30.4375/7 = 173.9285... -> 173.92 (down). Planned spend (ETH/PAXG only,
    since BTC already has a rule) is 450; 450 + 173.92 = 623.92 > the 500 cap: a WARNING, not a
    blocker (rail 14 only ever blocks the order actually placed, not a forecast)."""
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "live", budget="40")
    _insert_dca(repo, "SOL-USD", "candidate", budget="40")  # not live: no commitment
    plan = _plan(valid_config_path, repo)
    assert plan.existing_live_monthly_usd == Decimal("173.92")
    assert plan.approvable, plan.blockers
    (warning,) = [w for w in plan.warnings if "173.92" in w]
    assert "500" in warning


def test_two_live_dca_rules_commitments_sum(valid_config_path: Path) -> None:
    """R7 amended: each live row's commitment is computed from its own budget_usd, and the
    rows sum. 20 x 30.4375/7 = 86.9642... -> 86.96 (down); 173.92 + 86.96 = 260.88."""
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "live", budget="40")
    _insert_dca(repo, "SOL-USD", "live", budget="20")
    plan = _plan(valid_config_path, repo)
    assert plan.existing_live_monthly_usd == Decimal("260.88")


def test_no_warning_claims_the_executor_sizes_dca_from_config(valid_config_path: Path) -> None:
    """R9 withdrawn (#843): the executor sizes each DCA buy from the rule's own size_usd, so a
    warning saying it uses config dca.budget_usd would be false about the money path."""
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "live")
    plan = _plan(valid_config_path, repo)
    assert len(plan.warnings) >= 2  # non-vacuous: the min-order note AND the live-commit warning
    assert not any("dca.budget_usd" in w for w in plan.warnings)
    assert not hasattr(plan, "executor_budget_usd")


def test_a_live_dip_bonus_rule_gets_a_named_warning(valid_config_path: Path) -> None:
    """R7 amended: a live row's dip bonus is not modelled into the commitment sum (its ceiling
    is unbounded in principle), so it gets its own warning instead, naming the rule and its
    product so the operator knows which row can spend more than the figure shown."""
    repo = _repo()
    rule_id = _insert_dca(repo, "BTC-USD", "live", dip="2")
    plan = _plan(valid_config_path, repo)
    (warning,) = [w for w in plan.warnings if f"rule {rule_id}" in w]
    assert "dip" in warning


def test_a_candidate_dip_bonus_rule_gets_no_such_warning(valid_config_path: Path) -> None:
    """Only LIVE rows spend real money on a dip bonus -- a candidate row is inert."""
    repo = _repo()
    rule_id = _insert_dca(repo, "BTC-USD", "candidate", dip="2")
    plan = _plan(valid_config_path, repo)
    assert not [w for w in plan.warnings if f"rule {rule_id}" in w]


def test_even_daily_pacing_is_stated(valid_config_path: Path) -> None:
    repo = _repo()
    attest_subscription(repo, now_ts=NOW_TS, free_volume_usd=Decimal("500"), pacing="even_daily")
    plan = _plan(valid_config_path, repo, cap=None)
    assert any("even_daily" in w for w in plan.warnings)


# -- the terminal renderer, with rail 14's wording pinned (Task 4) -------------------------------


def _section(lines: list[str], title: str) -> list[str]:
    """The lines under a `== title ==` header, up to the next header."""
    start = lines.index(f"== {title} ==")
    rest = lines[start + 1 :]
    end = next((i for i, line in enumerate(rest) if line.startswith("== ")), len(rest))
    return [line for line in rest[:end] if line.strip()]


def test_the_schedule_has_one_row_per_buy_carrying_its_figures(valid_config_path: Path) -> None:
    plan = _plan(valid_config_path)
    rows = _section(render_dca_plan(plan), "Schedule")
    body = [row for row in rows if not row.lstrip().startswith(("asset", "total"))]
    assert len(body) == plan.buy_count == 3
    for row, buy in zip(body, plan.buys, strict=True):
        cells = row.split()
        assert cells[0] == buy.asset
        assert "every 7 days" in row
        assert f"${buy.per_buy_usd}" in row and f"${buy.monthly_usd}" in row
    (total,) = [row for row in rows if row.lstrip().startswith("total")]
    assert "$449.89" in total and "$5.40" in total


def test_fees_are_labelled_as_the_configured_rate(valid_config_path: Path) -> None:
    text = "\n".join(render_dca_plan(_plan(valid_config_path)))
    assert "configured fees.taker_pct 1.2%" in text


def test_every_excluded_asset_appears_once_with_its_reasons(valid_config_path: Path) -> None:
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "live")
    plan = _plan(valid_config_path, repo, rejected=("PAXG",))
    rows = _section(render_dca_plan(plan), "Excluded")
    assert len(rows) == len(plan.excluded) == 2
    assert {row.split()[0] for row in rows} == {"BTC", "PAXG"}
    btc = next(row for row in rows if row.split()[0] == "BTC")
    assert "existing, unchanged" in btc


def test_existing_rules_are_listed_unchanged(valid_config_path: Path) -> None:
    repo = _repo()
    rule_id = _insert_dca(repo, "BTC-USD", "live")
    rows = _section(
        render_dca_plan(_plan(valid_config_path, repo)), "Existing DCA rules (unchanged)"
    )
    assert len(rows) == 1 and rows[0].split()[0] == f"[{rule_id}]"


def test_an_existing_rules_dip_bonus_is_shown_on_its_row(valid_config_path: Path) -> None:
    """Coordinator addition: a live existing rule's row carries its dip bonus when it has one,
    and the Notes section still has exactly one line per warning (the dip-bonus warning among
    them). `[id]` stays the row's first whitespace cell."""
    repo = _repo()
    rule_id = _insert_dca(repo, "BTC-USD", "live", dip="2")
    plan = _plan(valid_config_path, repo)
    assert len(plan.warnings) >= 2  # min-order note AND the dip-bonus warning, non-vacuous
    lines = render_dca_plan(plan)
    existing_rows = _section(lines, "Existing DCA rules (unchanged)")
    assert len(existing_rows) == len(plan.existing) == 1
    (row,) = existing_rows
    assert row.split()[0] == f"[{rule_id}]"
    assert "dip_bonus_pct 2" in row
    assert len(_section(lines, "Notes")) == len(plan.warnings)


def test_blockers_and_warnings_each_get_one_line(valid_config_path: Path) -> None:
    plan = _plan(valid_config_path, cap="400")
    lines = render_dca_plan(plan)
    assert len(_section(lines, "Cannot approve")) == len(plan.blockers)
    assert len(_section(lines, "Notes")) == len(plan.warnings)


def test_an_approvable_plan_has_no_cannot_approve_section(valid_config_path: Path) -> None:
    assert "== Cannot approve ==" not in render_dca_plan(_plan(valid_config_path))


def test_the_rendered_plan_states_rail_14_is_a_buy_cap_and_never_fee_free(
    valid_config_path: Path,
) -> None:
    lines = render_dca_plan(_plan(valid_config_path))
    assert sum(RAIL14_NOTE in line for line in lines) == 1
    assert not any("fee-free" in line.lower() for line in lines)
    assert not any("max_exposure_usd" in line for line in lines)
