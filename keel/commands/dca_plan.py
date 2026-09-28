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

**DCA's aggregate bounds are rail 14 and available cash, not `max_exposure_usd`/
`max_per_asset_pct` (#841/#853).** Rails 2 (per order), 3 (per day), 5 (correlated) and 13 (cash)
still bind a DCA buy, and this plan checks the two rails it CAN evaluate offline: rail 3 (a
BLOCKER: the busiest day every rule's cadence can coincide on, since `Dca.detect`'s scheduling
carries no per-rule phase) and rail 5 (a WARNING: a per-buy above the correlated-size cap, using
`guards.py`'s own `CORRELATED_SIZE_SCALE`/`UNCORRELATED_ASSETS`). Neither text may describe
`max_exposure_usd` or `max_per_asset_pct` as a limit on DCA.

**What this module never does:** promote, touch a `paper`/`live` row, or modify any existing rule.
It writes `candidate` rows through `keel.commands.rules.add_rule_row` and nothing else.
"""

from __future__ import annotations

import inspect
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
# read the way the rails read it, so "already has a DCA rule" cannot disagree with them. Rail 5's
# own constants (#853) so the correlated-size warning below computes the SAME cap the rail does,
# never a re-typed copy that could drift from it.
from keel.execution.guards import CORRELATED_SIZE_SCALE, UNCORRELATED_ASSETS
from keel.execution.guards import _asset as _asset_of
from keel.strategy.rules.dca import Dca

#: The default `Dca.__init__` itself uses for `budget_usd`, read from its signature -- not a
#: re-typed literal, and without touching the rule class. A stored row missing `budget_usd`
#: (`existing_dca_rules`) is built by `agent.build_rule_from_params` with exactly this default.
DCA_DEFAULT_BUDGET_USD = Decimal(str(inspect.signature(Dca).parameters["budget_usd"].default))

#: Average days per month (365.25 / 12 = 30.4375): the per-buy formula's month.
MONTH_DAYS = Decimal("365.25") / Decimal("12")
DEFAULT_CADENCE_DAYS = 7

_CENT = Decimal("0.01")

#: An operator-typed budget above this is not a realistic monthly USD figure. Quantizing a
#: `Decimal` to cents needs (integer digits + 2) <= the context's precision (28 by default);
#: past that, `.quantize()` raises `decimal.InvalidOperation` instead of rounding -- exactly
#: what `--budget 1e30` did (defect, review of #846): a raw traceback instead of a clean usage
#: error. This bound is refused well before that ceiling, at a figure no real monthly DCA budget
#: could reach.
MAX_BUDGET_USD = Decimal("1e12")
#: The finest input `_decimal` accepts: past this, a value is a typo or a probe, not money.
MAX_DECIMAL_PLACES = 12


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
    # The WRITTEN exponent, not `normalize()`d: the default context clamps a tiny value there.
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_DECIMAL_PLACES:
        # `format(x, "f")` would spell `1e-10000000` out as ten million digits, into the
        # command and the card (review of #850) -- refused here, before anything formats it.
        raise DcaPlanError(
            f"{name} {raw!r} has more than {MAX_DECIMAL_PLACES} decimal places -- refused before "
            "it could be spelled out"
        )
    return value


def parse_plan_inputs(
    budget: str, buffer_pct: str, cadence_days: int = DEFAULT_CADENCE_DAYS
) -> PlanInputs:
    """The one parse of the plan's inputs, shared by the CLI and `/api/dca-plan` so the two
    refuse the same inputs with the same words."""
    budget_usd = _decimal(budget, "budget")
    if budget_usd <= 0:
        raise DcaPlanError(f"budget must be positive, got {budget!r}")
    if budget_usd > MAX_BUDGET_USD:
        raise DcaPlanError(
            f"budget {budget!r} is not a realistic monthly USD amount (over "
            f"{format(MAX_BUDGET_USD, ',f')}) -- refused before it could break Decimal rounding"
        )
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
    #: True when the stored params carry no `budget_usd` and `budget_usd` above is
    #: `DCA_DEFAULT_BUDGET_USD`, the figure the rule would be built with -- inferred, not stored,
    #: so a live row like this gets its own named warning (review of #846).
    budget_inferred: bool = False


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
        # A row missing `budget_usd` entirely (legacy/malformed) is built by
        # `agent.build_rule_from_params` with `Dca.__init__`'s own default --
        # `DCA_DEFAULT_BUDGET_USD` -- not $0 (review of #846: $0 undercounted a live row's R7
        # commitment) and not `config.dca.budget_usd` (the executor's fallback, reached only when
        # a setup carries no `size_usd` at all -- never true of a `Dca` rule's own buy).
        raw_budget = params.get("budget_usd")
        budget_inferred = raw_budget is None
        budget_usd = DCA_DEFAULT_BUDGET_USD if budget_inferred else Decimal(str(raw_budget))
        found.append(
            ExistingDcaRule(
                rule_id=int(row["id"]),
                # `_asset_of` (rail 1's key function) does NOT uppercase its result -- confirmed
                # by reading `guards._asset` -- so the `.upper()` here is this module's own,
                # deliberate on top of it.
                asset=_asset_of(product).upper(),
                product_id=product,
                status=str(row["status"]),
                budget_usd=budget_usd,
                cadence_days=int(params.get("cadence_days", DEFAULT_CADENCE_DAYS)),
                dip_bonus_pct=Decimal(str(params.get("dip_bonus_pct", "0"))),
                budget_inferred=budget_inferred,
            )
        )
    return tuple(found)


def _weights_by_asset(raw: Mapping[str, Decimal], source: str) -> dict[str, Decimal]:
    """Uppercase every key, refusing -- never silently dropping (#848) -- when two keys collide
    only by case. `{"btc": .9, "BTC": .1}` uppercased by a plain dict comprehension lets
    whichever key iterates last overwrite the other, and the loser's weight vanishes with
    nothing said: exactly the silent-drop class R4 (`not_on_allowlist`) exists to prevent for
    every OTHER way a weight can disappear. Both callers below (`target_weights`, the `[E]`
    override) share this one guard so neither can drop a weight the other one catches."""
    by_upper: dict[str, list[str]] = {}
    for key in raw:
        by_upper.setdefault(key.upper(), []).append(key)
    collisions = {upper: keys for upper, keys in by_upper.items() if len(keys) > 1}
    if collisions:
        detail = "; ".join(
            f"{upper} ({', '.join(sorted(keys))})" for upper, keys in sorted(collisions.items())
        )
        raise DcaPlanError(
            f"{source} has keys that collide once uppercased -- {detail} -- fix the config; "
            "nothing was silently dropped"
        )
    parsed = {key: Decimal(str(w)) for key, w in raw.items()}
    # A `.nan` / `.inf` weight passes `load_config`; refused here, naming the key, rather than
    # as an `InvalidOperation` traceback from the `<= 0` comparison downstream.
    non_finite = sorted(key for key, w in parsed.items() if not w.is_finite())
    if non_finite:
        raise DcaPlanError(f"{source} has a non-finite weight for {', '.join(non_finite)}")
    return {key.upper(): w for key, w in parsed.items()}


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
    # #849: the allowlist names assets, so a repeat (`[BTC, ETH, btc]` passes `load_config`) is
    # the SAME asset -- one allocation, one buy, one rule. `dict.fromkeys` keeps first-seen order;
    # nothing is dropped, because a repeat carries no weight of its own.
    allowlist = list(dict.fromkeys(asset.upper() for asset in config.allowlist))
    weights = _weights_by_asset(config.target_weights, "target_weights")
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
    override = _weights_by_asset(weights_override or {}, "the edited weights")
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
    #: is withdrawn -- that statement is no longer true of the money path). This is still the
    #: AVERAGE-month figure shown to the operator; the warning that uses it is triggered by the
    #: worst-case figures below instead (R7 amended again, #847).
    existing_live_monthly_usd: Decimal
    #: R6 amended 2026-09-27, #847: rail 14 caps the UTC CALENDAR month
    #: (`guards._monthly_buy_spend_usd`), not the 30.4375-day average the per-buy SIZING still
    #: uses (unchanged). Every DCA rule in one plan shares one cadence and buys on the same days
    #: (`epoch_day % cadence_days == 0`, `Dca.detect`), so the worst calendar month for that
    #: cadence -- `_worst_month_buy_days` -- can hold more buys than the average-month `spend`
    #: figure implies (5 for a 7-day cadence, e.g. a 31-day month phased on the 1st). This field
    #: is that count.
    worst_month_buy_days: int
    #: The total spent, across every planned buy, on ONE cadence day -- exact: each addend
    #: (`buy.per_buy_usd`) is already rounded down to cents, and summing introduces no further
    #: rounding.
    worst_month_cycle_usd: Decimal
    #: `worst_month_cycle_usd x worst_month_buy_days` -- what the blocker check (R6) and the
    #: renderer both use. This field IS the figure that was checked against the cap; the
    #: renderer reuses it verbatim rather than recomputing, so the display can never show a
    #: total larger than what was actually checked (R5's rule, extended to this figure by #847).
    worst_month_spend_usd: Decimal
    #: R21 (#853): the busiest SINGLE DAY this plan's own buys can land on -- `Dca.detect`'s
    #: scheduling rule (`epoch_day % cadence_days == 0`) has no per-rule phase/offset, so every
    #: buy this plan schedules shares one cadence and therefore lands on the SAME days; the
    #: worst single day for this plan alone is simply the sum of every planned `per_buy_usd`.
    worst_day_cycle_usd: Decimal
    #: R21: the total of every EXISTING `live` DCA rule's own per-buy amount (`budget_usd`, not
    #: the average-month figure `existing_live_monthly_usd` shows). Because no rule's cadence
    #: carries a phase either, a day exists where `epoch_day` is a multiple of every cadence
    #: involved (e.g. their lcm) -- on it, this plan's rules AND every existing live rule buy at
    #: once, whatever their individual cadences are.
    existing_live_daily_usd: Decimal
    #: `worst_day_cycle_usd + existing_live_daily_usd` -- the figure the rail 3 (per-day cap)
    #: blocker (R21) compares against `caps.max_per_day_usd`. Rail 3 counts EVERY BUY placed
    #: that day, not just DCA, but this plan only knows about DCA rules -- so this is a DCA-only
    #: FLOOR on that day's total, and the blocker text says so rather than overclaiming.
    worst_day_spend_usd: Decimal
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def approvable(self) -> bool:
        return not self.blockers


def _cents_down(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_DOWN)


def _usd(value: Decimal) -> str:
    return f"${format(value.quantize(_CENT), ',f')}"


def _worst_month_buy_days(cadence_days: int) -> int:
    """The most buy days a `cadence_days`-cadence schedule (`epoch_day % cadence_days == 0`,
    `Dca.detect`'s own scheduling rule -- not this module's) can land inside any single UTC
    calendar month, over every month length (28-31 days) and every phase the cadence can fall
    into. Exact, computed from the scheduling rule itself (brute force over the small, bounded
    space of month lengths and phases), not the 30.4375-day average `MONTH_DAYS` uses for
    per-buy sizing -- rail 14 caps the CALENDAR month
    (`keel/execution/guards.py::_monthly_buy_spend_usd`), so this is the figure a blocker must
    compare against (#847). For a 7-day cadence this is 5 (a 31-day month phased on the 1st:
    days 1, 8, 15, 22, 29)."""
    worst = 0
    for length in (28, 29, 30, 31):
        for phase in range(min(cadence_days, length)):
            hits = sum(1 for day in range(length) if day % cadence_days == phase)
            worst = max(worst, hits)
    return worst


def per_day_cap_text(
    *,
    worst_day_spend_usd: Decimal,
    worst_day_cycle_usd: Decimal,
    existing_live_daily_usd: Decimal,
    live_rule_count: int,
    max_per_day_usd: Decimal,
) -> str:
    """Rail 3's blocker (R21, #853), as ONE sentence both front-ends show: the CLI prints
    `plan.blockers` under "Cannot approve" and `/api/dca-plan` sends them as the card's
    blockers, verbatim. Phrased as a DCA-only floor, because rail 3 counts every BUY placed that
    day and the plan knows only DCA rules."""
    return (
        "the busiest day every rule's cadence can coincide on -- no cadence carries a "
        "phase, so a day exists where every rule buys at once -- would spend "
        f"{_usd(worst_day_spend_usd)} in DCA buys alone ({_usd(worst_day_cycle_usd)} from this "
        f"plan's own buys plus {_usd(existing_live_daily_usd)} from {live_rule_count} existing "
        f"live DCA rule(s)); that exceeds caps.max_per_day_usd "
        f"{_usd(max_per_day_usd)} (rail 3), which counts every BUY placed that "
        "day, not just DCA"
    )


def correlated_size_text(buy: PlannedBuy, correlated_cap: Decimal) -> str:
    """Rail 5's warning (R22, #853) for one planned buy, as ONE sentence both front-ends show
    (`plan.warnings`, verbatim). A warning, not a blocker: the rail binds only while another
    correlated asset already has exposure open, which a plan cannot see."""
    return (
        f"{buy.asset}'s per-buy {_usd(buy.per_buy_usd)} exceeds the correlated-size cap "
        f"{_usd(correlated_cap)} (rail 5: max_per_order_usd x {CORRELATED_SIZE_SCALE}) -- "
        "this only binds while another correlated asset already has exposure open, so it "
        "may never veto a buy in practice, but a buy this large risks it"
    )


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

    - R6 amended 2026-09-27, #847: the cap check compares the WORST UTC CALENDAR month for this
      plan's cadence (`_worst_month_buy_days(cadence) x` the per-cycle total, `worst_month_*` on
      `DcaPlan`) against rail 14's cap -- not the 30.4375-day average `spend` alone, because rail
      14 caps the calendar month (`guards._monthly_buy_spend_usd`) and every rule in this plan
      buys on the same days. Per-buy SIZING is unchanged; only the blocker's comparison moved.
    - R7 (amended after #843, amended again by #847 for consistency): each existing `live` DCA
      row's monthly commitment is still shown at that rule's OWN `budget_usd`, average-month
      figure -- not `config.dca.budget_usd` -- but the WARNING is now triggered by the same
      worst-calendar-month arithmetic as the blocker: this plan's worst month plus each live
      row's own worst month (its own cadence), against the cap.
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

    # R6, amended #847: the worst UTC CALENDAR month this cadence can land in, not the
    # 30.4375-day average `spend` alone -- rail 14 caps the calendar month
    # (`guards._monthly_buy_spend_usd`), and every buy in this plan shares one cadence and lands
    # on the same days (`epoch_day % cadence_days == 0`). `worst_month_cycle_usd` sums buys that
    # are ALREADY rounded down to cents, so this introduces no further rounding -- and the
    # renderer reuses these exact fields rather than recomputing them (R5, extended).
    worst_days = _worst_month_buy_days(cadence)
    worst_month_cycle = sum((b.per_buy_usd for b in buys), Decimal("0"))
    worst_month_spend = worst_month_cycle * worst_days
    if cap.allowance_usd is not None and worst_month_spend > cap.allowance_usd:
        if cap.in_force:
            blockers.append(
                f"planned spend {_usd(spend)}/month; the worst calendar month for a "
                f"{cadence}-day cadence holds {worst_days} buy day(s), which at "
                f"{_usd(worst_month_cycle)} per cycle is {_usd(worst_month_spend)} -- that "
                f"exceeds rail 14's monthly buy cap {_usd(cap.allowance_usd)} on {cap.venue}"
            )
        else:
            blockers.append(
                f"rail 14's monthly buy cap on {cap.venue} is {_usd(cap.allowance_usd)} because "
                f"{cap.degraded_reason}; the worst calendar month for a {cadence}-day cadence "
                f"would spend {_usd(worst_month_spend)} ({worst_days} buy day(s) x "
                f"{_usd(worst_month_cycle)} per cycle). Run `keel subscription attest --venue "
                f"{cap.venue} --tier <tier>` to restore it."
            )

    live_rules = [rule for rule in universe.existing if rule.status == "live"]

    # R21 (#853): rail 3 (`guards.py`'s per-day cap) counts EVERY BUY placed that day, and
    # `Dca.detect`'s scheduling rule (`epoch_day % cadence_days == 0`) carries no per-rule phase
    # or offset -- so a day exists where `epoch_day` is a multiple of every cadence involved (at
    # worst, their lcm), and on it EVERY rule -- this plan's own buys and every existing LIVE DCA
    # rule -- lands at once, regardless of how their individual cadences differ. The worst single
    # day is therefore simply the sum of every planned per-buy plus every live rule's own per-buy
    # (`budget_usd`, its per-hit amount -- not the average-month figure `live_monthly` computes
    # below). This plan only knows about DCA rules, so the blocker is phrased as a DCA-only floor
    # on that day's total, never a claim about the whole day's spend.
    worst_day_cycle = sum((b.per_buy_usd for b in buys), Decimal("0"))
    existing_live_daily = sum((rule.budget_usd for rule in live_rules), Decimal("0"))
    worst_day_spend = worst_day_cycle + existing_live_daily
    if worst_day_spend > config.caps.max_per_day_usd:
        blockers.append(
            per_day_cap_text(
                worst_day_spend_usd=worst_day_spend,
                worst_day_cycle_usd=worst_day_cycle,
                existing_live_daily_usd=existing_live_daily,
                live_rule_count=len(live_rules),
                max_per_day_usd=config.caps.max_per_day_usd,
            )
        )

    live_monthly = sum(
        (_cents_down(rule.budget_usd * MONTH_DAYS / rule.cadence_days) for rule in live_rules),
        Decimal("0"),
    )
    # R7 amended #847, for consistency with R6: the TRIGGER compares worst calendar months too --
    # this plan's own worst month plus each live row's own worst month, at its own cadence and
    # its own budget_usd (exact: an integer count times an already-exact stored amount, no
    # rounding). The DISPLAYED `live_monthly` figure above stays the average-month one operators
    # already read this warning by.
    live_worst_monthly = sum(
        (rule.budget_usd * _worst_month_buy_days(rule.cadence_days) for rule in live_rules),
        Decimal("0"),
    )
    warnings: list[str] = [MIN_ORDER_UNKNOWN]
    allowance = cap.allowance_usd
    combined_worst = worst_month_spend + live_worst_monthly
    if allowance is not None and live_monthly > 0 and combined_worst > allowance:
        warnings.append(
            f"existing live DCA rules commit about {_usd(live_monthly)}/month, at each rule's "
            f"own amount; combined with this plan's worst calendar month total "
            f"{_usd(worst_month_spend)}, the worst-case combined total is "
            f"{_usd(combined_worst)}, which exceeds rail 14's monthly buy cap {_usd(allowance)}, "
            "which will veto buys once the month's total reaches it"
        )
    for rule in live_rules:
        if rule.budget_inferred:
            warnings.append(
                f"rule {rule.rule_id} ({rule.product_id}) has no stored budget_usd; its "
                f"commitment above is counted at the Dca rule's default "
                f"{_usd(DCA_DEFAULT_BUDGET_USD)} per buy, which is what the rule is built with"
            )
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

    # R22 (#853): rail 5 (correlation-adjusted sizing) halves the per-order cap for a correlated
    # asset WHILE another correlated asset already has exposure open (`guards.py`'s
    # `CORRELATED_SIZE_SCALE`/`UNCORRELATED_ASSETS` -- imported, never re-typed, so this cannot
    # drift from the rail). A plan has no live exposure to check, so a per-buy above that cap is
    # a WARNING, not a blocker: it only ever binds conditionally, and names the asset(s) it would
    # bind for.
    correlated_cap = config.caps.max_per_order_usd * CORRELATED_SIZE_SCALE
    for buy in buys:
        if buy.asset not in UNCORRELATED_ASSETS and buy.per_buy_usd > correlated_cap:
            warnings.append(correlated_size_text(buy, correlated_cap))

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
        worst_month_buy_days=worst_days,
        worst_month_cycle_usd=worst_month_cycle,
        worst_month_spend_usd=worst_month_spend,
        worst_day_cycle_usd=worst_day_cycle,
        existing_live_daily_usd=existing_live_daily,
        worst_day_spend_usd=worst_day_spend,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


#: Stated once per plan, verbatim (#836). The attested figure is a cap keel imposes on its own
#: buying; Advanced Trade charges its fee on every order regardless.
RAIL14_NOTE = (
    "Rail 14 is a monthly BUY cap keel imposes on its own buying -- not a fee waiver: every "
    "order pays the venue's fee."
)


def worst_month_text(plan: DcaPlan) -> str:
    """What rail 14's cap was checked against (R6, amended #847), as ONE sentence both
    front-ends show: the CLI prints it after "checked against the cap:", and `/api/dca-plan`
    sends it as the card's `cap_check` display. Built from the plan's own `worst_month_*` fields
    verbatim, so neither front-end can show a total other than the one the blocker compared."""
    return (
        f"worst calendar month for a {plan.inputs.cadence_days}-day cadence, "
        f"{plan.worst_month_buy_days} buy day(s) x {_usd(plan.worst_month_cycle_usd)} per cycle "
        f"= {_usd(plan.worst_month_spend_usd)}"
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
        # #847: shown right next to the cap, and reusing the plan's own fields verbatim -- never
        # a total recomputed (and possibly larger) than what the blocker check actually used.
        f"  checked against the cap: {worst_month_text(plan)}",
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
