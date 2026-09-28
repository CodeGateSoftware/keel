"""`keel dca plan` -- a multi-asset DCA schedule, proposed from this deployment's configuration and
written only on approval, only at `candidate`, through `keel rules add`'s own service.

**A pure service: no click here** (the same split `keel/commands/rules.py` documents, one step
further -- the web console imports this module, and click is a front-end's). Two front-ends:
`keel/commands/dca.py` (the CLI, which owns the `[Y]/[E]/[N]` loop) and `keel/web/api.py`
(`/api/dca-plan`, read-only). Both call `build_dca_plan`; only the CLI can reach
`apply_dca_plan`, and only from a terminal.

**Rail 14 is a monthly BUY cap, not a fee waiver (#836).** keel trades on Coinbase Advanced
Trade, where every order pays the venue's fee. `monthly_buy_cap` reads the attested record
exactly as `keel/execution/guards.py` rail 14 does; `tests/commands/test_dca_plan.py` drives
`guards.check` itself to prove the two agree.

**What this module never does:** promote, touch a `paper`/`live` row, or modify any existing rule.
It writes `candidate` rows through `keel.commands.rules.add_rule_row` and nothing else.
"""

from __future__ import annotations

import json
import shlex
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation
from typing import Literal

from keel_core.subscription import SubscriptionStatus

from keel import agent
from keel.commands._products import _history_product, parse_products_option
from keel.commands.admission import ScreenFn, build_screen_report
from keel.commands.rules import RulesOutcome, RulesRefused, RulesUsageError, add_rule_row
from keel.config import Config
from keel.data.repository import Repository

# Rail 1's own key function, as `keel/commands/rules.py` imports it: an existing rule's asset is
# read the way the rails read it, so "already has a DCA rule" cannot disagree with them.
from keel.execution.guards import _asset as _asset_of

#: Average days per month (365.25 / 12 = 30.4375): the per-buy formula's month.
MONTH_DAYS = Decimal("365.25") / Decimal("12")
DEFAULT_CADENCE_DAYS = 7


class DcaPlanError(ValueError):
    """An operator input the plan cannot be built from. The message is shown verbatim."""


@dataclass(frozen=True)
class PlanInputs:
    budget_usd: Decimal
    buffer_pct: Decimal
    cadence_days: int = DEFAULT_CADENCE_DAYS


def _decimal(raw: str, name: str) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise DcaPlanError(f"{name} {raw!r} is not a number") from exc
    if not value.is_finite():
        raise DcaPlanError(f"{name} {raw!r} is not a finite number")
    return value


def parse_plan_inputs(
    budget: str, buffer_pct: str, cadence_days: int = DEFAULT_CADENCE_DAYS
) -> PlanInputs:
    """The one parse of the plan's inputs, shared by the CLI and `/api/dca-plan` so the two
    refuse the same inputs with the same words."""
    budget_usd = _decimal(budget, "budget")
    if budget_usd <= 0:
        raise DcaPlanError(f"budget must be positive, got {budget!r}")
    buffer = _decimal(buffer_pct, "buffer-pct")
    if not (Decimal("0") <= buffer < Decimal("1")):
        raise DcaPlanError(
            f"buffer-pct is a fraction in [0, 1), got {buffer_pct!r} -- 0.1 means hold back 10%"
        )
    if cadence_days < 1:
        raise DcaPlanError(f"cadence-days must be at least 1, got {cadence_days}")
    return PlanInputs(budget_usd=budget_usd, buffer_pct=buffer, cadence_days=cadence_days)


def parse_weight(raw: str, asset: str) -> Decimal:
    """One `[E]`-edited weight: finite and >= 0 (0 excludes the asset for this session)."""
    value = _decimal(raw, f"weight for {asset}")
    if value < 0:
        raise DcaPlanError(f"weight for {asset} must be 0 or more, got {raw!r}")
    return value


@dataclass(frozen=True)
class BuyCap:
    """Rail 14's monthly BUY cap for one venue. `allowance_usd is None` means unlimited."""

    venue: str
    allowance_usd: Decimal | None
    #: "" when the record is in force; otherwise rail 14's own words for why it is not.
    degraded_reason: str
    pacing: str

    @property
    def in_force(self) -> bool:
        return self.degraded_reason == ""


def monthly_buy_cap(repo: Repository, config: Config, *, venue: str, now_ts: int) -> BuyCap:
    """The allowance rail 14 enforces for `venue`, read the way `guards.check` reads it
    (`guards.py` rail 14): no record -> `unsubscribed_allowance_usd`; a record -> its
    `allowance_usd(now_ts, unsubscribed)`, which already degrades suspect/lapsed/overdue. The
    reason words are rail 14's, in its order (lapsed is reported ahead of overdue). Parity is
    pinned by driving `guards.check` at the cap and a cent over it."""
    unsubscribed = config.subscription.unsubscribed_allowance_usd
    record = repo.get_broker_subscription(venue)
    if record is None:
        return BuyCap(
            venue, unsubscribed, "no subscription has been attested", config.subscription.pacing
        )
    effective = record.effective_status(now_ts)
    if effective is SubscriptionStatus.ACTIVE:
        reason = ""
    elif record.status is SubscriptionStatus.LAPSED:
        reason = "its subscription is lapsed"
    elif record.attest_due_ts <= now_ts:
        reason = "its attestation is overdue"
    else:
        reason = f"its subscription is {effective.value}"
    return BuyCap(venue, record.allowance_usd(now_ts, unsubscribed), reason, record.pacing)


ExclusionReason = Literal["not_on_allowlist", "not_admitted", "no_weight", "has_dca_rule"]

#: The operator-facing words for each reason. One table, read by the terminal renderer and the
#: web payload alike.
REASON_TEXT: dict[str, str] = {
    "not_on_allowlist": "not on the allowlist",
    "not_admitted": "not admitted by the screen",
    "no_weight": "no positive target weight",
    "has_dca_rule": "already has a DCA rule -- existing, unchanged",
}

_NON_EXISTING = frozenset({"disabled"})


@dataclass(frozen=True)
class ExcludedAsset:
    asset: str
    reasons: tuple[ExclusionReason, ...]
    detail: str


@dataclass(frozen=True)
class ExistingDcaRule:
    rule_id: int
    asset: str
    product_id: str
    status: str
    budget_usd: Decimal
    cadence_days: int
    #: Coordinator amendment (2026-09-27), for Task 3's amended R7: the ceiling `dip_bonus_pct`
    #: rule params carry. Not itself modelled into the live commitment sum -- a row with
    #: `dip_bonus_pct > 0` gets a warning instead, since #843 sizes a live DCA buy from the rule's
    #: own `budget_usd` context and the dip bonus can push a single buy above that figure.
    dip_bonus_pct: Decimal


@dataclass(frozen=True)
class Allocation:
    asset: str
    product_id: str
    raw_weight: Decimal
    #: `raw_weight` renormalised over the allocated set; the allocations' weights sum to 1.
    weight: Decimal


@dataclass(frozen=True)
class Universe:
    allocations: tuple[Allocation, ...]
    excluded: tuple[ExcludedAsset, ...]
    existing: tuple[ExistingDcaRule, ...]
    #: `(asset, current raw weight)` for every asset `[E]` may edit (R14), allowlist order.
    editable: tuple[tuple[str, Decimal], ...]


def existing_dca_rules(repo: Repository) -> tuple[ExistingDcaRule, ...]:
    """Every non-disabled `dca` row, in id order. Read fresh by `apply_dca_plan` too."""
    found: list[ExistingDcaRule] = []
    for row in repo.get_rules():
        if row["kind"] != "dca" or row["status"] in _NON_EXISTING:
            continue
        params = row["params"] or {}
        product = str(params.get("product_id", ""))
        found.append(
            ExistingDcaRule(
                rule_id=int(row["id"]),
                # `_asset_of` (rail 1's key function) does NOT uppercase its result -- confirmed
                # by reading `guards._asset` -- so the `.upper()` here is this module's own,
                # deliberate on top of it.
                asset=_asset_of(product).upper(),
                product_id=product,
                status=str(row["status"]),
                budget_usd=Decimal(str(params.get("budget_usd", "0"))),
                cadence_days=int(params.get("cadence_days", DEFAULT_CADENCE_DAYS)),
                dip_bonus_pct=Decimal(str(params.get("dip_bonus_pct", "0"))),
            )
        )
    return tuple(found)


def select_universe(
    repo: Repository,
    config: Config,
    *,
    screen_fn: ScreenFn,
    weights_override: Mapping[str, Decimal] | None = None,
) -> Universe:
    """allowlist ∩ admitted ∩ positive weight, minus assets with a non-disabled DCA rule.

    Admission comes from `build_screen_report` with the injected `screen_fn`
    (`keel.commands.assets.screen_product` in production) -- the one gate every candidate source
    routes through, never a laxer copy. READ-ONLY: this writes nothing.
    """
    quote = config.quote_currency
    allowlist = [asset.upper() for asset in config.allowlist]
    weights = {asset.upper(): Decimal(str(w)) for asset, w in config.target_weights.items()}
    admitted = {
        sp.asset.upper(): sp for sp in build_screen_report(repo, config, screen_fn).screened
    }
    existing = existing_dca_rules(repo)
    existing_by_asset = {rule.asset: rule for rule in existing}

    editable_assets = [
        asset
        for asset in allowlist
        if asset in admitted and admitted[asset].result.admitted and asset not in existing_by_asset
    ]
    override = {asset.upper(): w for asset, w in (weights_override or {}).items()}
    stray = sorted(set(override) - set(editable_assets))
    if stray:
        raise DcaPlanError(
            f"cannot edit the weight of {', '.join(stray)}: only admitted, allowlisted assets "
            "with no DCA rule are in this plan"
        )
    effective = {**weights, **override}

    excluded: list[ExcludedAsset] = []
    chosen: list[tuple[str, Decimal]] = []
    for asset in allowlist:
        reasons: list[ExclusionReason] = []
        details: list[str] = []
        screened = admitted.get(asset)
        if screened is None or not screened.result.admitted:
            reasons.append("not_admitted")
            details.append("; ".join(screened.result.failures) if screened else "not screened")
        weight = effective.get(asset, Decimal("0"))
        if weight <= 0:
            reasons.append("no_weight")
            if asset in override:
                details.append("set to 0 in this session")
        rule = existing_by_asset.get(asset)
        if rule is not None:
            reasons.append("has_dca_rule")
            details.append(f"rule {rule.rule_id} ({rule.status})")
        if reasons:
            excluded.append(
                ExcludedAsset(asset, tuple(reasons), "; ".join(d for d in details if d))
            )
        else:
            chosen.append((asset, weight))
    for asset in sorted(set(weights) - set(allowlist)):
        if weights[asset] > 0:
            excluded.append(ExcludedAsset(asset, ("not_on_allowlist",), ""))

    total = sum((w for _, w in chosen), Decimal("0"))
    allocations = tuple(
        Allocation(asset, _history_product(asset, quote), w, w / total) for asset, w in chosen
    )
    editable = tuple((asset, effective.get(asset, Decimal("0"))) for asset in editable_assets)
    return Universe(allocations, tuple(excluded), existing, editable)


def apply_command(
    inputs: PlanInputs | None, *, config_path: str | None = None, db_path: str | None = None
) -> str:
    """The exact `keel dca plan` invocation for `inputs` -- `format(x, "f")` so a `1e3` budget is
    spelled `1000`, never `1E+3`. `None` gives the template the web card shows before a budget
    is chosen. Global options (`--config`/`--db`) precede the group, as click requires."""
    head = ["keel"]
    if config_path is not None:
        head += ["--config", shlex.quote(config_path)]
    if db_path is not None:
        head += ["--db", shlex.quote(db_path)]
    if inputs is None:
        return " ".join([*head, "dca plan --budget <monthly USD> --buffer-pct <fraction>"])
    parts = [
        *head,
        "dca",
        "plan",
        "--budget",
        format(inputs.budget_usd, "f"),
        "--buffer-pct",
        format(inputs.buffer_pct, "f"),
    ]
    if inputs.cadence_days != DEFAULT_CADENCE_DAYS:
        parts += ["--cadence-days", str(inputs.cadence_days)]
    return " ".join(parts)


_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")

#: keel records no venue minimum order size: `keel_broker_api.results.Instrument` carries none
#: ("Minimum sizes are still absent for the original reason -- nothing reads them").
MIN_ORDER_UNKNOWN = (
    "keel does not record the venue's minimum order size (its instrument record carries none), "
    "so no per-buy amount was checked against it -- confirm each against the venue's product "
    "minimum before promoting."
)


@dataclass(frozen=True)
class PlannedBuy:
    asset: str
    product_id: str
    weight: Decimal
    #: `weight x 100`, computed here so no front-end multiplies (payload.py Rule 2).
    weight_pct: Decimal
    cadence_days: int
    per_buy_usd: Decimal
    monthly_usd: Decimal
    est_monthly_fee_usd: Decimal
    #: The venue minimum, when keel knows it. It never does today -- see MIN_ORDER_UNKNOWN.
    min_order_usd: Decimal | None


@dataclass(frozen=True)
class DcaPlan:
    inputs: PlanInputs
    spend_usd: Decimal
    buffer_usd: Decimal
    buys: tuple[PlannedBuy, ...]
    buy_count: int
    excluded: tuple[ExcludedAsset, ...]
    existing: tuple[ExistingDcaRule, ...]
    editable: tuple[tuple[str, Decimal], ...]
    cap: BuyCap
    planned_monthly_usd: Decimal
    est_monthly_fees_usd: Decimal
    taker_pct: Decimal
    taker_pct_display: Decimal
    #: Sum, over each non-disabled `live` DCA row, of `_cents_down(rule.budget_usd x MONTH_DAYS /
    #: rule.cadence_days)` -- that rule's OWN per-buy amount, because since #843 the live executor
    #: sizes each DCA buy from the rule's own `setup.context["size_usd"]`
    #: (`keel/execution/executor.py::_dca_budget`), not from `config.dca.budget_usd` (R7,
    #: amended after #843; R9, which claimed the executor sizes every buy from the config figure,
    #: is withdrawn -- that statement is no longer true of the money path).
    existing_live_monthly_usd: Decimal
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def approvable(self) -> bool:
        return not self.blockers


def _cents_down(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_DOWN)


def _usd(value: Decimal) -> str:
    return f"${format(value.quantize(_CENT), ',f')}"


def build_dca_plan(
    repo: Repository,
    config: Config,
    inputs: PlanInputs,
    *,
    venue: str,
    now_ts: int,
    screen_fn: ScreenFn,
    weights_override: Mapping[str, Decimal] | None = None,
) -> DcaPlan:
    """The whole proposal, every figure computed here and nowhere downstream. READ-ONLY.

    Per buy: `monthly share x cadence_days / (365.25/12)`, rounded DOWN to cents; the monthly
    total is recomputed from that rounded buy and rounded down again; the fee estimate at the
    CONFIGURED `fees.taker_pct` is rounded UP (R5). Blockers (R6, R8) make the plan
    unapprovable; warnings never do:

    - R7 (amended after #843): each existing `live` DCA row's monthly commitment, at that rule's
      OWN `budget_usd` -- not `config.dca.budget_usd` -- summed against this plan's spend and
      rail 14's cap.
    - R7's dip-bonus corollary: a `live` row with `dip_bonus_pct > 0` can spend more than the
      commitment above on any given buy, so it gets its own warning naming the rule.
    - The minimum-order gap: keel does not know the venue's minimum order size
      (`MIN_ORDER_UNKNOWN`).
    - Rail 14's `even_daily` pacing, when the attested record uses it.
    """
    universe = select_universe(repo, config, screen_fn=screen_fn, weights_override=weights_override)
    cap = monthly_buy_cap(repo, config, venue=venue, now_ts=now_ts)
    cadence = inputs.cadence_days
    spend = _cents_down(inputs.budget_usd * (Decimal("1") - inputs.buffer_pct))
    taker = config.fees.taker_pct

    buys: list[PlannedBuy] = []
    blockers: list[str] = []
    for allocation in universe.allocations:
        per_buy = _cents_down(spend * allocation.weight * cadence / MONTH_DAYS)
        monthly = _cents_down(per_buy * MONTH_DAYS / cadence)
        buys.append(
            PlannedBuy(
                asset=allocation.asset,
                product_id=allocation.product_id,
                weight=allocation.weight,
                weight_pct=(allocation.weight * _HUNDRED).quantize(Decimal("0.1")),
                cadence_days=cadence,
                per_buy_usd=per_buy,
                monthly_usd=monthly,
                est_monthly_fee_usd=(monthly * taker).quantize(_CENT, rounding=ROUND_UP),
                min_order_usd=None,
            )
        )
        if per_buy <= 0:
            blockers.append(
                f"{allocation.asset}'s per-buy rounds to $0.00 -- raise the budget or its weight"
            )
        elif per_buy > config.caps.max_per_order_usd:
            blockers.append(
                f"{allocation.asset}'s per-buy {_usd(per_buy)} exceeds caps.max_per_order_usd "
                f"{_usd(config.caps.max_per_order_usd)}; that rail would veto every buy"
            )
    if not buys:
        blockers.append("no asset is eligible -- see the excluded list for each reason")
    if cap.allowance_usd is not None and spend > cap.allowance_usd:
        if cap.in_force:
            blockers.append(
                f"planned spend {_usd(spend)} exceeds rail 14's monthly buy cap "
                f"{_usd(cap.allowance_usd)} on {cap.venue}"
            )
        else:
            blockers.append(
                f"rail 14's monthly buy cap on {cap.venue} is {_usd(cap.allowance_usd)} because "
                f"{cap.degraded_reason}; planned spend is {_usd(spend)}. Run `keel subscription "
                f"attest --venue {cap.venue} --tier <tier>` to restore it."
            )

    live_rules = [rule for rule in universe.existing if rule.status == "live"]
    live_monthly = sum(
        (_cents_down(rule.budget_usd * MONTH_DAYS / rule.cadence_days) for rule in live_rules),
        Decimal("0"),
    )
    warnings: list[str] = [MIN_ORDER_UNKNOWN]
    allowance = cap.allowance_usd
    if allowance is not None and live_monthly > 0 and spend + live_monthly > allowance:
        warnings.append(
            f"existing live DCA rules commit about {_usd(live_monthly)}/month, at each rule's "
            f"own amount; with this plan's {_usd(spend)} that exceeds rail 14's monthly buy cap "
            f"{_usd(allowance)}, which will veto buys once the month's total reaches it"
        )
    for rule in live_rules:
        if rule.dip_bonus_pct > 0:
            warnings.append(
                f"rule {rule.rule_id} ({rule.product_id}) has dip_bonus_pct "
                f"{rule.dip_bonus_pct}: each 1-point drawdown from its recent high adds "
                f"{rule.dip_bonus_pct}% of its budget to a buy, so its buys can exceed both its "
                "listed per-buy amount and the commitment shown above"
            )
    if cap.pacing == "even_daily":
        warnings.append(
            "rail 14 pacing is even_daily: the cap is also paced per business day, so an early-"
            "month buy can be vetoed below the full-month figure shown"
        )

    return DcaPlan(
        inputs=inputs,
        spend_usd=spend,
        buffer_usd=_cents_down(inputs.budget_usd) - spend,
        buys=tuple(buys),
        buy_count=len(buys),
        excluded=universe.excluded,
        existing=universe.existing,
        editable=universe.editable,
        cap=cap,
        planned_monthly_usd=sum((b.monthly_usd for b in buys), Decimal("0")),
        est_monthly_fees_usd=sum((b.est_monthly_fee_usd for b in buys), Decimal("0")),
        taker_pct=taker,
        taker_pct_display=taker * _HUNDRED,
        existing_live_monthly_usd=live_monthly,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


#: Stated once per plan, verbatim (#836). The attested figure is a cap keel imposes on its own
#: buying; Advanced Trade charges its fee on every order regardless.
RAIL14_NOTE = (
    "Rail 14 is a monthly BUY cap keel imposes on its own buying -- not a fee waiver: every "
    "order pays the venue's fee."
)


def render_dca_plan(plan: DcaPlan) -> list[str]:
    """The CLI's exact lines. Sections are `== Title ==` headers so the CLI and tests read them
    the same way."""
    inputs = plan.inputs
    cap = "unlimited" if plan.cap.allowance_usd is None else _usd(plan.cap.allowance_usd)
    lines = [
        "== DCA plan ==",
        f"  budget {_usd(inputs.budget_usd)}/month, buffer {format(inputs.buffer_pct, 'f')} "
        f"({_usd(plan.buffer_usd)} held back) -> spend {_usd(plan.spend_usd)}/month",
        f"  rail 14 monthly buy cap on {plan.cap.venue}: {cap}"
        + ("" if plan.cap.in_force else f" (because {plan.cap.degraded_reason})"),
        f"  {RAIL14_NOTE}",
        "",
        "== Schedule ==",
        f"  {'asset':<8} {'weight':>7} {'cadence':<14} {'per buy':>10} {'monthly':>10} "
        f"{'est. fee':>9}",
    ]
    for buy in plan.buys:
        lines.append(
            f"  {buy.asset:<8} {format(buy.weight_pct, 'f') + '%':>7} "
            f"{'every ' + str(buy.cadence_days) + ' days':<14} {_usd(buy.per_buy_usd):>10} "
            f"{_usd(buy.monthly_usd):>10} {_usd(buy.est_monthly_fee_usd):>9}"
        )
    lines.append(
        f"  total {_usd(plan.planned_monthly_usd)}/month, est. fees "
        f"{_usd(plan.est_monthly_fees_usd)}/month at the configured fees.taker_pct "
        f"{_trim_pct(plan.taker_pct_display)}%"
    )
    if plan.excluded:
        lines += ["", "== Excluded =="]
        for item in plan.excluded:
            reasons = ", ".join(REASON_TEXT[r] for r in item.reasons)
            detail = f" ({item.detail})" if item.detail else ""
            lines.append(f"  {item.asset} -- {reasons}{detail}")
    if plan.existing:
        lines += ["", "== Existing DCA rules (unchanged) =="]
        for rule in plan.existing:
            row = (
                f"  [{rule.rule_id}] {rule.product_id} {rule.status} "
                f"{_usd(rule.budget_usd)} every {rule.cadence_days} days"
            )
            if rule.dip_bonus_pct > 0:
                row += f" (dip_bonus_pct {rule.dip_bonus_pct})"
            lines.append(row)
    if plan.blockers:
        lines += ["", "== Cannot approve =="] + [f"  ✗ {b}" for b in plan.blockers]
    lines += ["", "== Notes =="] + [f"  ! {w}" for w in plan.warnings]
    return lines


def _trim_pct(value: Decimal) -> str:
    """`1.200` -> `1.2`; `format(..., "f")` keeps it exponent-free, and `.rstrip` only trims."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# -- the all-or-nothing `candidate` write ---------------------------------------------------------


class DcaPlanRefused(RuntimeError):
    """Approval refused. Raised before the first insert, so nothing was written -- except the
    one case R2 names (a row failing AFTER pre-validation), whose message lists what was."""


def _noop(message: str) -> None:
    del message


def _rule_params(buy: PlannedBuy) -> dict[str, object]:
    return {"cadence_days": buy.cadence_days, "budget_usd": format(buy.per_buy_usd, "f")}


def apply_dca_plan(
    repo: Repository,
    config: Config,
    plan: DcaPlan,
    *,
    now_ts: int,
    echo: Callable[[str], None] = _noop,
    echo_err: Callable[[str], None] = _noop,
) -> tuple[RulesOutcome, ...]:
    """Write one `candidate` `dca` rule per planned buy, through `keel rules add`'s own service.

    All-or-nothing (R2): refuse a blocked plan; re-read the rules table and refuse if any planned
    asset gained a non-disabled DCA rule since the preview; construct every rule (rails 18/19 +
    `build_rule_from_params`) before the FIRST insert. Never promotes; never writes paper/live;
    never touches an existing row -- `add_rule_row` writes `candidate` and nothing else.

    `add_rule_row` commits per row, so it is not itself transactional across a whole plan: a row
    that fails AFTER pre-validation has passed (a locked database -- `sqlite3.Error` -- rather
    than anything `RulesRefused`/`RulesUsageError` names) still leaves the earlier rows written.
    That refusal names their ids (R2's stated cost if wrong).
    """
    if plan.blockers:
        raise DcaPlanRefused("the plan cannot be approved: " + "; ".join(plan.blockers))
    fresh = {rule.asset: rule for rule in existing_dca_rules(repo)}
    stale = [buy.asset for buy in plan.buys if buy.asset in fresh]
    if stale:
        raise DcaPlanRefused(
            f"{', '.join(stale)} gained a DCA rule since this plan was shown "
            f"({', '.join(f'rule {fresh[a].rule_id}' for a in stale)}); nothing was written -- "
            "re-run `keel dca plan` to see the current state"
        )
    for buy in plan.buys:
        try:
            parse_products_option(buy.product_id, config)
            agent.build_rule_from_params("dca", {**_rule_params(buy), "product_id": buy.product_id})
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise DcaPlanRefused(f"{buy.asset}: {exc}; nothing was written") from exc

    outcomes: list[RulesOutcome] = []
    for buy in plan.buys:
        try:
            outcomes.append(
                add_rule_row(
                    repo,
                    config,
                    kind="dca",
                    product=buy.product_id,
                    params_json=json.dumps(_rule_params(buy)),
                    now_ts=now_ts,
                    echo=echo,
                    echo_err=echo_err,
                )
            )
        except (RulesRefused, RulesUsageError, sqlite3.Error) as exc:
            written = ", ".join(str(o.rule_id) for o in outcomes) or "none"
            raise DcaPlanRefused(f"{buy.asset}: {exc}; rules already written: {written}") from exc
    return tuple(outcomes)
