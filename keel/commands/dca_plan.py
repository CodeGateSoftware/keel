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

import shlex
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from keel_core.subscription import SubscriptionStatus

from keel.config import Config
from keel.data.repository import Repository

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
