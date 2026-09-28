"""`keel.commands.dca_plan` -- the DCA plan SERVICE (no click).

Pins, in order: input parsing (percent-shaped and non-finite input refused), rail 14's cap read
exactly as `guards.check` reads it (parity driven through `guards.check` itself), the apply
command's no-exponent spelling, then (Tasks 2-5) the universe, the amounts, the rendering and
the all-or-nothing write.
"""

from __future__ import annotations

import ast
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
from keel_core.config import load_config
from keel_core.subscription import SubscriptionStatus

from keel.commands import dca_plan as dca_mod
from keel.commands.dca_plan import (
    RAIL14_NOTE,
    DcaPlanError,
    DcaPlanRefused,
    ExcludedAsset,
    PlanInputs,
    apply_command,
    apply_dca_plan,
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


@pytest.mark.parametrize(("budget", "buffer"), [("1e-10000000", "0.1"), ("500", "1e-10000000")])
def test_an_input_with_an_absurd_exponent_is_refused_before_it_is_spelled_out(
    budget: str, buffer: str
) -> None:
    """Review of #850: `format(x, "f")` spells `1e-10000000` out as ten million digits, into the
    command and the card's `summary.budget` -- a 25-character query answered with 10-20 MB of
    JSON. Refused at the one shared parse, so the CLI and the card refuse it alike."""
    with pytest.raises(DcaPlanError, match="decimal places"):
        parse_plan_inputs(budget, buffer)


def test_twelve_decimal_places_are_still_accepted() -> None:
    inputs = parse_plan_inputs("500.000000000001", "0.000000000001")
    assert format(inputs.buffer_pct, "f") == "0.000000000001"


def test_an_absurd_budget_is_refused_cleanly_not_a_decimal_crash() -> None:
    """Defect (review of #846): `--budget 1e30` used to reach `Decimal.quantize` (cents,
    28-digit default context) with more digits than the context allows, raising
    `decimal.InvalidOperation` -- a raw traceback, not a usage error. `parse_plan_inputs` must
    refuse it before it ever reaches a `quantize` call."""
    with pytest.raises(DcaPlanError, match="budget"):
        parse_plan_inputs("1e30", "0.1")
    # Not vacuous: unpatched, this is exactly the crash being refused against.
    with pytest.raises(InvalidOperation):
        Decimal("1e30").quantize(Decimal("0.01"))


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


def test_a_stored_rule_missing_budget_usd_is_counted_at_the_rules_default_and_named(
    valid_config_path: Path,
) -> None:
    """Defect (review of #846): a legacy row whose params lack `budget_usd` used to read as $0,
    undercounting a live rule's R7 commitment. `agent.build_rule_from_params` would construct
    that row with `Dca.__init__`'s own default, so the plan counts it at THAT default -- read
    from the constructor's signature, not a re-typed literal and not a change to the rule class
    -- and says so in one named warning, because the figure is inferred, not stored."""
    import inspect

    from keel.commands.dca_plan import existing_dca_rules
    from keel.strategy.rules.dca import Dca

    constructor_default = inspect.signature(Dca).parameters["budget_usd"].default
    repo = _repo()
    rule_id = repo.insert_rule(
        "dca",
        {"product_id": "BTC-USD", "cadence_days": 7, "dip_bonus_pct": "0", "lookback_days": 90},
        status="live",
        now_ts=NOW_TS,
    )
    _insert_dca(repo, "SOL-USD", "live", budget="40")  # stores budget_usd: no warning for it

    by_id = {rule.rule_id: rule for rule in existing_dca_rules(repo)}
    assert by_id[rule_id].budget_usd == constructor_default == Decimal("50")

    # Counted at $50 in R7's commitment, not $0: 50 x 30.4375/7 = 217.41 (down), plus SOL's
    # 40 x 30.4375/7 = 173.92 (down).
    plan = _plan(valid_config_path, repo)
    assert plan.existing_live_monthly_usd == Decimal("217.41") + Decimal("173.92")
    named = [w for w in plan.warnings if "no stored budget_usd" in w]
    assert len(named) == 1
    assert named[0].startswith(f"rule {rule_id} (BTC-USD) has no stored budget_usd")


@pytest.mark.parametrize(
    ("cadence", "expected"),
    [(1, 31), (2, 16), (7, 5), (10, 4), (14, 3), (28, 2), (30, 2), (31, 1), (45, 1)],
)
def test_worst_month_buy_days_is_the_most_cadence_days_any_calendar_month_holds(
    cadence: int, expected: int
) -> None:
    """R6 amended (#847): the most `epoch_day % cadence == 0` days any 28-31-day UTC calendar
    month can contain -- ceil(31 / cadence) for every cadence up to 31, and 1 beyond."""
    from keel.commands.dca_plan import _worst_month_buy_days

    assert _worst_month_buy_days(cadence) == expected


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


def test_a_repeated_allowlist_entry_is_one_asset_not_a_double_share(
    valid_config_path: Path,
) -> None:
    """#849: `allowlist: [BTC, ETH, btc, PAXG]` passes `load_config` as-is. The allowlist names
    assets, so a repeat (in any case) is the SAME asset: one allocation, one buy, one rule --
    never a doubled weight or two BTC-USD candidates. The plan must match the worked example
    exactly, as if BTC were listed once."""
    config = _config(valid_config_path, allowlist=("BTC", "ETH", "btc", "PAXG"))
    universe = select_universe(_repo(), config, screen_fn=_screen())
    assert [a.asset for a in universe.allocations] == ["BTC", "ETH", "PAXG"]
    assert sum(a.weight for a in universe.allocations) == Decimal("1")
    assert [asset for asset, _ in universe.editable] == ["BTC", "ETH", "PAXG"]

    plan = _plan(valid_config_path, allowlist=("BTC", "ETH", "btc", "PAXG"))
    assert [(b.asset, b.per_buy_usd) for b in plan.buys] == [
        ("BTC", Decimal("41.39")),
        ("ETH", Decimal("31.04")),
        ("PAXG", Decimal("31.04")),
    ]


def test_a_non_finite_target_weight_is_refused_cleanly(valid_config_path: Path) -> None:
    """A `.nan` / `.inf` weight in YAML passes `load_config`; it must be a `DcaPlanError` naming
    the key, not an `InvalidOperation` traceback from the `<= 0` comparison."""
    for bad in ("NaN", "Infinity"):
        config = _config(valid_config_path, target_weights={"BTC": Decimal(bad)})
        with pytest.raises(DcaPlanError, match="target_weights.*BTC"):
            select_universe(_repo(), config, screen_fn=_screen())


def test_case_colliding_target_weights_are_refused_not_silently_dropped(
    valid_config_path: Path,
) -> None:
    """#848: `{"btc": .9, "BTC": .1}` uppercase-collide. The old code built `{asset.upper(): w
    for asset, w in ...}` over the dict in iteration order, so BTC's weight silently became
    whichever key came last (0.1), and the 0.9 vanished with no line saying so -- the exact
    silent-drop class #198/R4 exists to prevent for every OTHER kind of dropped weight. This
    must refuse loudly instead, naming both colliding keys."""
    config = _config(
        valid_config_path,
        target_weights={"btc": Decimal("0.9"), "BTC": Decimal("0.1"), "ETH": Decimal("0.5")},
    )
    with pytest.raises(DcaPlanError) as excinfo:
        select_universe(_repo(), config, screen_fn=_screen())
    message = str(excinfo.value)
    assert "btc" in message
    assert "BTC" in message


def test_non_colliding_mixed_case_weights_still_work(valid_config_path: Path) -> None:
    """The collision guard must not refuse the ordinary case (one spelling per asset) that
    `test_weight_keys_are_matched_case_insensitively` already pins -- this is the negative case
    for the SAME guard, run through `build_dca_plan` rather than `select_universe` directly."""
    config = _config(
        valid_config_path,
        target_weights={"btc": Decimal("0.4"), "Eth": Decimal("0.3"), "PAXG": Decimal("0.3")},
    )
    universe = select_universe(_repo(), config, screen_fn=_screen())
    assert [a.asset for a in universe.allocations] == ["BTC", "ETH", "PAXG"]


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
    # #847: 600 clears the worst calendar month's 5 x $103.47 = $517.35 for the default
    # BTC/ETH/PAXG .4/.3/.3 weights (or any renormalised subset of them, which sums to the same
    # total) -- tests exercising THAT exact defect pass their own `cap` explicitly.
    cap: str | None = "600",
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
    """#847: at the cap this worked example was originally written against ($500), the plan is
    now a BLOCKER, not approvable -- see `test_the_worked_examples_worst_month_blocks_the_500_cap`
    below for why. The per-buy math this test exists to pin is unchanged."""
    plan = _plan(valid_config_path, cap="500")
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
    assert not plan.approvable


def test_the_worked_examples_worst_month_blocks_the_500_cap(valid_config_path: Path) -> None:
    """#847 (defect, review of #846): rail 14 caps the UTC CALENDAR month
    (`guards._monthly_buy_spend_usd`), not an average 30.4375-day month. Every DCA rule buys on
    the same days (`epoch_day % cadence_days == 0`, `Dca.detect`), so a 7-day cadence's worst
    calendar month holds 5 buy days (31-day month, phase aligned on the 1st: days 1/8/15/22/29).
    The worked example's per-cycle total is $41.39 + $31.04 + $31.04 = $103.47/week; 5 x $103.47
    = $517.35, which exceeds the $500 cap even though the average-month `spend` ($450) does not."""
    plan = _plan(valid_config_path, cap="500")
    assert plan.worst_month_buy_days == 5
    assert plan.worst_month_cycle_usd == Decimal("103.47")
    assert plan.worst_month_spend_usd == Decimal("517.35")
    assert not plan.approvable
    (blocker,) = [b for b in plan.blockers if "rail 14" in b]
    assert blocker == (
        "planned spend $450.00/month; the worst calendar month for a 7-day cadence holds 5 buy "
        "day(s), which at $103.47 per cycle is $517.35 -- that exceeds rail 14's monthly buy cap "
        "$500.00 on coinbase"
    )


def test_a_worst_month_that_fits_the_cap_is_still_approvable(valid_config_path: Path) -> None:
    """The other half of #847: the SAME worst-case figures, against a cap that actually clears
    them ($520 > $517.35), are not a blocker. Proves the fix compares the worst month correctly
    in both directions, not just as a stricter-always veto."""
    plan = _plan(valid_config_path, cap="520")
    assert plan.worst_month_buy_days == 5
    assert plan.worst_month_cycle_usd == Decimal("103.47")
    assert plan.worst_month_spend_usd == Decimal("517.35")
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
    weekly 40: 40 x 30.4375/7 = 173.9285... -> 173.92 (down), the average-month figure still
    shown. The WARNING TRIGGER itself is worst-case (#847, for consistency with the blocker):
    this plan's own worst month (ETH/PAXG only, since BTC already has a rule) is $517.40, and
    BTC's own worst month is 40 x 5 = $200.00; combined $717.40 exceeds the $600 cap used here
    (chosen so the plan itself, at $517.40, stays under it and this stays a WARNING, not a
    blocker -- rail 14 only ever blocks the order actually placed, not a forecast)."""
    repo = _repo()
    _insert_dca(repo, "BTC-USD", "live", budget="40")
    _insert_dca(repo, "SOL-USD", "candidate", budget="40")  # not live: no commitment
    plan = _plan(valid_config_path, repo)
    assert plan.existing_live_monthly_usd == Decimal("173.92")
    assert plan.approvable, plan.blockers
    (warning,) = [w for w in plan.warnings if "173.92" in w]
    assert "600" in warning


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


# -- rail 3 (#853): the busiest day every rule's cadence can coincide on is a BLOCKER ------------


def test_the_worked_examples_busiest_day_is_the_sum_of_its_own_buys(
    valid_config_path: Path,
) -> None:
    """The worked example's per-cycle total ($41.39 + $31.04 + $31.04 = $103.47) is also the
    busiest single day, since none of the plan's own buys carries a phase offset -- they all
    land on the same days."""
    plan = _plan(valid_config_path)
    assert plan.worst_day_cycle_usd == Decimal("103.47")
    assert plan.existing_live_daily_usd == Decimal("0")
    assert plan.worst_day_spend_usd == Decimal("103.47")
    assert plan.approvable, plan.blockers


def test_a_busy_day_over_the_per_day_cap_is_a_named_blocker(valid_config_path: Path) -> None:
    plan = _plan(
        valid_config_path, caps=replace_caps(valid_config_path, max_per_day_usd=Decimal("100"))
    )
    assert plan.blockers == (
        dca_mod.per_day_cap_text(
            worst_day_spend_usd=Decimal("103.47"),
            worst_day_cycle_usd=Decimal("103.47"),
            existing_live_daily_usd=Decimal("0"),
            live_rule_count=0,
            max_per_day_usd=Decimal("100"),
        ),
    )
    # One sentence, shown by the CLI exactly as the plan carries it.
    lines = dca_mod.render_dca_plan(plan)
    assert lines.count(f"  ✗ {plan.blockers[0]}") == 1


def test_a_busy_day_exactly_at_the_per_day_cap_is_approvable(valid_config_path: Path) -> None:
    """Boundary: exactly at the cap passes."""
    plan = _plan(
        valid_config_path,
        caps=replace_caps(valid_config_path, max_per_day_usd=Decimal("103.47")),
    )
    assert not [b for b in plan.blockers if "rail 3" in b]
    assert plan.approvable, plan.blockers


def test_a_busy_day_one_cent_over_the_per_day_cap_blocks(valid_config_path: Path) -> None:
    """Boundary: one cent over the cap blocks."""
    plan = _plan(
        valid_config_path,
        caps=replace_caps(valid_config_path, max_per_day_usd=Decimal("103.46")),
    )
    assert plan.blockers == (
        dca_mod.per_day_cap_text(
            worst_day_spend_usd=Decimal("103.47"),
            worst_day_cycle_usd=Decimal("103.47"),
            existing_live_daily_usd=Decimal("0"),
            live_rule_count=0,
            max_per_day_usd=Decimal("103.46"),
        ),
    )


def test_existing_live_dca_rules_push_the_busy_day_over_the_cap(valid_config_path: Path) -> None:
    """An existing live rule's OWN per-buy amount (its `budget_usd`) is counted toward the
    busiest-day figure, on top of this plan's own $103.47: 103.47 + 200 = 303.47 > 300."""
    repo = _repo()
    _insert_dca(repo, "SOL-USD", "live", budget="200")
    caps = replace_caps(valid_config_path, max_per_day_usd=Decimal("300"))
    plan = _plan(valid_config_path, repo, caps=caps)
    assert plan.existing_live_daily_usd == Decimal("200")
    assert plan.worst_day_spend_usd == Decimal("303.47")
    assert plan.blockers == (
        dca_mod.per_day_cap_text(
            worst_day_spend_usd=Decimal("303.47"),
            worst_day_cycle_usd=Decimal("103.47"),
            existing_live_daily_usd=Decimal("200"),
            live_rule_count=1,
            max_per_day_usd=Decimal("300"),
        ),
    )


def test_a_disabled_or_candidate_dca_rule_does_not_count_toward_the_busy_day(
    valid_config_path: Path,
) -> None:
    """Only LIVE rows spend real money on a cadence hit -- a candidate row is inert, matching
    R7's `live_rules` filter."""
    repo = _repo()
    _insert_dca(repo, "SOL-USD", "candidate", budget="200")
    caps = replace_caps(valid_config_path, max_per_day_usd=Decimal("300"))
    plan = _plan(valid_config_path, repo, caps=caps)
    assert plan.existing_live_daily_usd == Decimal("0")
    assert plan.worst_day_spend_usd == Decimal("103.47")


def test_rail3_blocker_does_not_claim_dca_is_the_whole_days_spend(valid_config_path: Path) -> None:
    """Rail 3 counts every BUY that day, not just DCA -- the blocker must say so, not imply DCA
    is the only spend rail 3 sees."""
    plan = _plan(
        valid_config_path, caps=replace_caps(valid_config_path, max_per_day_usd=Decimal("100"))
    )
    (blocker,) = [b for b in plan.blockers if "rail 3" in b]
    assert "DCA" in blocker and "not just DCA" in blocker


# -- rail 5 (#853): a per-buy above the correlated-size cap is a WARNING, naming the asset -------


def test_a_correlated_per_buy_over_the_correlated_size_cap_is_a_named_warning(
    valid_config_path: Path,
) -> None:
    from keel.execution.guards import CORRELATED_SIZE_SCALE

    plan = _plan(valid_config_path, budget="1000", buffer="0", cap="2000")
    correlated_cap = Decimal("100") * CORRELATED_SIZE_SCALE  # conftest's max_per_order_usd: 100
    btc = next(b for b in plan.buys if b.asset == "BTC")
    eth = next(b for b in plan.buys if b.asset == "ETH")
    paxg = next(b for b in plan.buys if b.asset == "PAXG")
    assert btc.per_buy_usd > correlated_cap
    assert eth.per_buy_usd > correlated_cap
    assert paxg.per_buy_usd > correlated_cap  # over the cap too, but PAXG is uncorrelated (gold)

    expected = [dca_mod.correlated_size_text(b, correlated_cap) for b in (btc, eth)]
    rail5 = [w for w in plan.warnings if w in expected]
    assert rail5 == expected  # exactly BTC then ETH, once each; never PAXG
    assert dca_mod.correlated_size_text(paxg, correlated_cap) not in plan.warnings
    assert plan.approvable, plan.blockers


def test_no_correlated_size_warning_when_every_per_buy_is_under_the_cap(
    valid_config_path: Path,
) -> None:
    plan = _plan(valid_config_path)  # the worked example: BTC $41.39 < correlated cap $50
    assert not [w for w in plan.warnings if "correlated-size cap" in w]


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


def test_the_worst_month_sentence_is_one_text_shared_by_the_cli_and_the_card(
    valid_config_path: Path,
) -> None:
    """#847's cap-check figure is shown by two front-ends: the CLI's line and the web card. ONE
    service function writes the sentence, so the card cannot word (or total) it differently from
    the line the terminal prints."""
    plan = _plan(valid_config_path)
    text = dca_mod.worst_month_text(plan)
    assert text == (
        "worst calendar month for a 7-day cadence, 5 buy day(s) x $103.47 per cycle = $517.35"
    )
    lines = render_dca_plan(plan)
    assert lines.count("  checked against the cap: " + text) == 1


def test_an_approvable_plan_has_no_cannot_approve_section(valid_config_path: Path) -> None:
    assert "== Cannot approve ==" not in render_dca_plan(_plan(valid_config_path))


def test_the_rendered_plan_states_rail_14_is_a_buy_cap_and_never_fee_free(
    valid_config_path: Path,
) -> None:
    lines = render_dca_plan(_plan(valid_config_path))
    assert sum(RAIL14_NOTE in line for line in lines) == 1
    # #847: the output says the WORST calendar month is what was checked against the cap, on
    # exactly one line, carrying the plan's own checked figures verbatim.
    (checked,) = [line for line in lines if "worst calendar month" in line]
    assert (
        checked == "  checked against the cap: worst calendar month for a 7-day cadence, "
        "5 buy day(s) x $103.47 per cycle = $517.35"
    )
    assert not any("fee-free" in line.lower() for line in lines)
    assert not any("max_exposure_usd" in line for line in lines)


# -- the all-or-nothing `candidate` write (Task 5) ------------------------------------------------


def _rows(repo: Repository) -> list[dict]:
    return [dict(row) for row in repo.get_rules()]


def test_approval_writes_one_candidate_dca_rule_per_buy_through_rules_add(
    valid_config_path: Path,
) -> None:
    repo = _repo()
    plan = _plan(valid_config_path, repo)
    outcomes = apply_dca_plan(repo, _config(valid_config_path), plan, now_ts=NOW_TS)

    rows = _rows(repo)
    assert len(outcomes) == len(rows) == plan.buy_count == 3
    for row, buy, outcome in zip(rows, plan.buys, outcomes, strict=True):
        assert (row["kind"], row["status"]) == ("dca", "candidate")
        assert row["params"]["product_id"] == buy.product_id
        assert row["params"]["cadence_days"] == 7
        assert Decimal(row["params"]["budget_usd"]) == buy.per_buy_usd
        assert outcome.rule_id == row["id"] and outcome.new_status == "candidate"
    # The row round-trips through the agent's own reconstruction (what `rules add` guarantees):
    from keel.agent import _build_rule

    rebuilt = _build_rule(rows[0])
    assert rebuilt.params["budget_usd"] == Decimal("41.39")


def test_approval_never_touches_an_existing_rule(valid_config_path: Path) -> None:
    repo = _repo()
    live_id = _insert_dca(repo, "BTC-USD", "live")
    other_id = repo.insert_rule(
        "turtle_breakout", {"product_id": "ETH-USD"}, status="paper", now_ts=NOW_TS
    )
    before = {row["id"]: row for row in _rows(repo)}

    apply_dca_plan(repo, _config(valid_config_path), _plan(valid_config_path, repo), now_ts=NOW_TS)

    after = {row["id"]: row for row in _rows(repo)}
    assert after[live_id] == before[live_id] and after[other_id] == before[other_id]
    new = [row for rid, row in after.items() if rid not in before]
    assert {row["params"]["product_id"] for row in new} == {"ETH-USD", "PAXG-USD"}
    assert {row["status"] for row in new} == {"candidate"}


def test_a_blocked_plan_writes_nothing(valid_config_path: Path) -> None:
    repo = _repo()
    plan = _plan(valid_config_path, repo, cap="400")
    with pytest.raises(DcaPlanRefused, match="rail 14"):
        apply_dca_plan(repo, _config(valid_config_path), plan, now_ts=NOW_TS)
    assert _rows(repo) == []


def test_a_dca_rule_written_after_the_preview_refuses_the_whole_approval(
    valid_config_path: Path,
) -> None:
    """Review Focus 3 / R2: the user's concurrent session adds ETH's DCA rule mid-prompt."""
    repo = _repo()
    plan = _plan(valid_config_path, repo)
    late = _insert_dca(repo, "ETH-USD", "candidate")

    with pytest.raises(DcaPlanRefused, match="ETH"):
        apply_dca_plan(repo, _config(valid_config_path), plan, now_ts=NOW_TS)
    assert [row["id"] for row in _rows(repo)] == [late]


def test_a_stale_dca_rule_refusal_names_the_late_rules_id(valid_config_path: Path) -> None:
    """R2: the refusal names the id of the rule that appeared since the preview, not only the
    asset -- an operator staring at two ETH rows needs to know which one is the intruder."""
    repo = _repo()
    plan = _plan(valid_config_path, repo)
    late = _insert_dca(repo, "ETH-USD", "candidate")

    with pytest.raises(DcaPlanRefused, match=f"rule {late}"):
        apply_dca_plan(repo, _config(valid_config_path), plan, now_ts=NOW_TS)
    assert [row["id"] for row in _rows(repo)] == [late]


def test_a_buy_the_rule_cannot_construct_is_refused_before_any_write(
    valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: pre-validation covers every buy before the first insert."""
    repo = _repo()
    plan = _plan(valid_config_path, repo)
    from keel import agent

    real = agent.build_rule_from_params
    paxg_calls = {"n": 0}

    def refuse_paxg(kind, params):
        if params["product_id"] == "PAXG-USD":
            paxg_calls["n"] += 1
            raise ValueError("budget_usd must be positive")
        return real(kind, params)

    monkeypatch.setattr(agent, "build_rule_from_params", refuse_paxg)
    with pytest.raises(DcaPlanRefused, match="PAXG"):
        apply_dca_plan(repo, _config(valid_config_path), plan, now_ts=NOW_TS)
    assert _rows(repo) == []
    # Non-vacuous: the patched function was actually reached for PAXG's pre-validation call.
    assert paxg_calls["n"] == 1


def test_a_locked_database_mid_write_names_the_rows_already_written(
    valid_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2's cost-if-wrong case: `add_rule_row` commits per row, so a row that fails AFTER
    pre-validation (a locked database, not something pre-validation catches) leaves earlier
    rows written. The refusal names their ids. The patch targets `dca_mod.add_rule_row` --
    the NAME bound into this module's namespace by `from keel.commands.rules import
    add_rule_row` -- not `keel.commands.rules.add_rule_row`, which `apply_dca_plan` never
    looks up again once that name is bound."""
    repo = _repo()
    plan = _plan(valid_config_path, repo)
    real = dca_mod.add_rule_row
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(dca_mod, "add_rule_row", flaky)

    with pytest.raises(DcaPlanRefused) as excinfo:
        apply_dca_plan(repo, _config(valid_config_path), plan, now_ts=NOW_TS)

    rows = _rows(repo)
    assert len(rows) == 1
    assert str(rows[0]["id"]) in str(excinfo.value)
    # Non-vacuous: the patch reached the two calls `apply_dca_plan`'s write loop made before
    # the refusal (one delegated to the real writer, one raised).
    assert calls["n"] == 2
