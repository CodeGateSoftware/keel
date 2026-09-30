"""`sleeve_exit` -- propose selling the whole held sleeve when the exit monitor says `breached`,
preview-only (spec §7, #857, plan P16).

**Spec §7's second layer.** The first is the monitor, `keel.execution.sleeve_exit`: pure
arithmetic over completed daily bars that says, per held product, `clear`, `near`, `breached` or
`insufficient_history` against a trailing drawdown and a confirmed close under the SMA. The cycle
records that level and alerts on each change (`agent._watch_sleeve_exits`, `sleeve.exit_watch`).
This rule adds one thing: on `breached` it hands the sell pipeline a `Reduction` for the WHOLE
holding, which `executor.reduce` records as a `sell_proposals` row and places nowhere.

**One definition of a breach.** `reduce_signal` asks the monitor's own `classify`, at this rule's
params (`monitor_kwargs`), and the cycle's watch and `keel dca exit --preview` read the SAME
params off the same rule (`monitor_rule`/`monitor_params` below). A proposal, an alert and the
preview can therefore never disagree about whether a product breached. The rule reads no state,
so it classifies with no `previous` level: that only moves the `near` band's hysteresis, and a
breach does not depend on it.

**Evidence status, stated here because the code must not imply more (spec §7).** #442 measured
trailing exits worse on the rule families; #830 found a 200-day SMA filter on the daily turtle
indistinguishable from its baseline; #831's every arm lived through a 78-80% drawdown. None of
them tests a 35% structural stop on an accumulation sleeve, and this build runs no such test: the
defaults are spec §7's values (R26, `keel.execution.sleeve_exit.DEFAULT_*`), untuned, and there is
no `param_space()` -- the research freeze of 2026-09-27 holds. The monitor is an operator alert,
which needs no edge; the proposal is the same alert with its round trip spelled out.

**Spec §7 failure mode (a), and why nothing here sells.** A DCA sleeve keeps buying through the
drawdown a stop would sell into, so an automatic `sleeve_exit` would sell the sleeve and the
weekly `dca` would rebuy it two fees later. So `Execution = Literal["preview"]` (S2), the
constructor refuses anything else, and the proposal is the end of the line in this build.

**What the pipeline decides, not the rule (plan R11-R14):** flooring and rail-2 slicing
(`sleeve.slice_qty` -- a whole-sleeve sale is the one most likely to need several legs), the
same-day-DCA exclusion, one proposal per product per UTC day, and arbitration, where
`sleeve_exit` outranks every other kind (`sleeve.ARBITRATION_ORDER`). **R13: it is exempt from
`min_hold_days`** (`sleeve.MIN_HOLD_EXEMPT_KINDS`): a whole-sleeve exit consumes this week's DCA
tranche by definition, and one that waited for a 30-day buy-free window would never fire under a
weekly DCA. So the rule has no `min_hold_days` param, and no `cooldown_days` either.

**Validation (P15's held item a).** The constructor refuses, by name, what `classify` would
otherwise choke on mid-cycle: `arms` must be a list of known arm names -- a bare string is
refused as one rather than split into letters -- the windows must be positive ints, and `dd_pct`
and `warn_pct` finite `Decimal`s in range. `rules add` and the cycle both build through
`agent.build_rule_from_params`, so a bad row is refused before it is written, and a stored one
that no longer builds is skipped and named (R68).

**Registered, never seeded** (`agent.RULE_REGISTRY`, `agent.seedable_kinds`; R20, R38, R61): a
seeded seller is what no one should get by accident, even one whose params all default.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any, Literal

from keel.execution import sleeve_exit as monitor
from keel.strategy.reduction import Holding, Reduction, SellCosts
from keel.strategy.rules.base import Rule, Setup, completed_days
from keel.types import Candle, Granularity

#: v1 execution is preview-only (S2). A PLAIN assignment, not the PEP 695 `type` statement, for
#: the reason `reverse_dca.Execution` gives: `get_type_hints()` must resolve it to a `Literal`
#: so `keel rules add` refuses `"auto"` before a row is written.
Execution = Literal["preview"]

#: The registered kind (`agent.RULE_REGISTRY`), and the name a rule of it carries.
KIND = "sleeve_exit"

_HUNDRED = Decimal("100")

_STATUS_RANK = {"live": 0, "paper": 1}


def _positive_int(name: str, value: object) -> int:
    # `bool` is an `int`; `True` days is not a window anyone meant.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int, got {value!r}")
    return value


def _percent(name: str, value: object, *, low: Decimal, low_open: bool) -> Decimal:
    """A finite `Decimal` percent in `(low, 100)` or `[low, 100)`. Finiteness FIRST, as
    `Reduction.__post_init__` orders it: `Decimal("NaN") < 0` raises rather than answering, and
    `Infinity` would pass a lower bound outright."""
    if not isinstance(value, Decimal):
        raise ValueError(f"{name} must be a Decimal percent, got {value!r}")
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite number, got {value}")
    too_low = value <= low if low_open else value < low
    if too_low or value >= _HUNDRED:
        interval = f"({low}, 100)" if low_open else f"[{low}, 100)"
        raise ValueError(f"{name} must be in {interval}, got {value}")
    return value


def _arms(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"arms must be a list of arm names, not the bare string {value!r}")
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"arms must be a list of arm names, got {value!r}")
    arms = tuple(value)
    if not arms or len(set(arms)) != len(arms) or not set(arms) <= set(monitor.ARMS):
        raise ValueError(
            f"arms must be a non-empty list of distinct names from {list(monitor.ARMS)}, "
            f"got {list(arms)!r}"
        )
    return arms


class SleeveExit(Rule):
    """A proposal to sell the whole holding when the monitor classifies the product `breached`
    at this rule's levels (spec §7).

    It never enters (`detect` is `None`) and never exits (`exit_signal` is `False`): its one
    decision is `reduce_signal`, whose only caller in the cycle is `agent._handle_reductions`.
    """

    PARAM_DOCS: dict[str, str] = {
        "dd_pct": (
            "the trailing drawdown, in % under the lookback_days high, whose breach proposes "
            "the whole sleeve. Default 35."
        ),
        "lookback_days": "completed daily bars the trailing high is taken over. Default 200.",
        "sma_period": "the simple moving average of daily closes the structural break is judged "
        "against. Default 200.",
        "confirm_days": "consecutive closes under that SMA that confirm a break. Default 3.",
        "warn_pct": (
            "within this % of a level the monitor alerts 'near' (and leaves it beyond twice "
            "this); 0 turns 'near' off. It proposes nothing. Default 5."
        ),
        "arms": "which levels to judge: a list of 'drawdown' and/or 'sma'. Default both.",
        "execution": "'preview' only in v1: every exit is a recorded proposal, never a sale.",
    }

    promotion_class = "sleeve_sell"
    #: A sleeve policy, not a round trip: the edge report keeps it out of the pooled G2 sample.
    accumulates = True
    decimal_params = ("dd_pct", "warn_pct")
    tuple_params = ("arms",)

    def __init__(
        self,
        product_id: str,
        dd_pct: Decimal = monitor.DEFAULT_DD_PCT,
        lookback_days: int = monitor.DEFAULT_LOOKBACK_DAYS,
        sma_period: int = monitor.DEFAULT_SMA_PERIOD,
        confirm_days: int = monitor.DEFAULT_CONFIRM_DAYS,
        warn_pct: Decimal = monitor.DEFAULT_WARN_PCT,
        arms: Sequence[str] = monitor.ARMS,
        execution: Execution = "preview",
        name: str = KIND,
    ) -> None:
        checked_arms = _arms(arms)
        checked_dd = _percent("dd_pct", dd_pct, low=Decimal("0"), low_open=True)
        checked_warn = _percent("warn_pct", warn_pct, low=Decimal("0"), low_open=False)
        windows = {
            key: _positive_int(key, value)
            for key, value in (
                ("lookback_days", lookback_days),
                ("sma_period", sma_period),
                ("confirm_days", confirm_days),
            )
        }
        if execution != "preview":
            # S2: see `ReverseDca.__init__` -- the annotation binds `rules add` and mypy; this
            # binds every other constructor caller (`build_rule_from_params` on a stored row).
            raise ValueError(f"execution must be 'preview' in v1, got {execution!r}")

        self.name = name
        self.product_id = product_id
        self.params: dict = {
            "product_id": product_id,
            "dd_pct": checked_dd,
            **windows,
            "warn_pct": checked_warn,
            "arms": checked_arms,
            "execution": execution,
        }

    def monitor_kwargs(self) -> dict[str, Any]:
        """`classify`'s level keywords at this rule's params -- what the rule decides on, and
        what the cycle's watch and `keel dca exit --preview` judge the product at."""
        p = self.params
        return {
            "dd_pct": p["dd_pct"],
            "lookback_days": p["lookback_days"],
            "sma_period": p["sma_period"],
            "confirm_days": p["confirm_days"],
            "warn_pct": p["warn_pct"],
            "arms": p["arms"],
        }

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Never an entry: an exit sells."""
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        """Never an exit of a POSITION: it proposes a sleeve sale and owns no tranche's exit
        (plan Review Focus 5 -- a rule that can only propose does not manage a position)."""
        return False

    def reduce_signal(
        self,
        holding: Holding,
        candles_by_tf: dict[Granularity, list[Candle]],
        costs: SellCosts,
    ) -> Reduction | None:
        """Pure. A `Reduction` of the whole holding when `classify` says `breached`; otherwise
        `None`, with `last_rejection` naming why (`no_daily_candles`, `nothing_held`, or
        `level` with the level it read). `costs` prices nothing here: the size is the holding,
        and the fee is recorded on the proposal by `executor.reduce`."""
        del costs
        days = completed_days(candles_by_tf)
        if not days:
            self.last_rejection = {"gate": "no_daily_candles"}
            return None
        if holding.qty <= 0:
            self.last_rejection = {"gate": "nothing_held"}
            return None
        watch = monitor.classify(self.product_id, days, **self.monitor_kwargs())
        if watch.level != "breached" or watch.close is None or watch.ts is None:
            self.last_rejection = {"gate": "level", "level": watch.level}
            return None
        p = self.params
        self.last_rejection = None
        return Reduction(
            product_id=self.product_id,
            qty=holding.qty,
            reason=self.name,
            trigger={
                "level": watch.level,
                "breached_arms": list(watch.breached_arms),
                "close": str(watch.close),
                "dd_level": None if watch.dd_level is None else str(watch.dd_level),
                "sma": None if watch.sma is None else str(watch.sma),
                "dd_pct": str(p["dd_pct"]),
                "lookback_days": p["lookback_days"],
                "sma_period": p["sma_period"],
                "confirm_days": p["confirm_days"],
                "arms": list(p["arms"]),
            },
            expected_price=watch.close,
            ts=watch.ts,
        )

    # No `param_space()` override: the levels are spec §7's alert levels, untested as a policy,
    # and the research freeze holds -- there is nothing a sweep may explore.

    def describe(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "param_space": [spec.plain() for spec in self.param_space()],
        }


def monitor_rule(
    sleeve_rules: Iterable[tuple[Rule, str]], product_id: str
) -> tuple[SleeveExit, str] | None:
    """The `(rule, status)` of the `SleeveExit` whose params set `product_id`'s levels, or
    `None` -- R26's defaults. `sleeve_rules` is the cycle's `(rule, status)` pairs
    (`agent._sleeve_rules`); a `live` rule wins over a `paper` one, then the lowest rule id.

    Selected by the REGISTERED CLASS, not by a rule's name: before P16 registered the kind, R69
    read any loaded rule named `sleeve_exit` duck-typed and coerced its raw params. A rule of
    this class has already been validated by its constructor, so its params are used as built.
    """
    candidates = [
        (rule, status)
        for rule, status in sleeve_rules
        if isinstance(rule, SleeveExit) and rule.product_id == product_id
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda pair: (_STATUS_RANK.get(pair[1], 2), pair[0].rule_id or 0),
    )


def monitor_params(sleeve_rules: Iterable[tuple[Rule, str]], product_id: str) -> dict[str, Any]:
    """The `classify` keyword arguments `monitor_rule`'s rule sets for `product_id`
    (`SleeveExit.monitor_kwargs`), or `{}` -- R26's defaults -- with none."""
    chosen = monitor_rule(sleeve_rules, product_id)
    return {} if chosen is None else chosen[0].monitor_kwargs()
