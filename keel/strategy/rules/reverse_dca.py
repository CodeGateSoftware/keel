"""`reverse_dca` -- a scheduled cash distribution out of a held sleeve (spec §6, #857).

**A spend plan, not an edge claim.** This is the mirror of `dca`: a fixed-cadence, fixed-size
SALE, decided by the operator's need for cash and bounded by caps. It claims nothing about
returns and needs no backtest of alpha -- #831's null bears on it no more than on whether to
deposit $500 a month. Its tests are hand computations on synthetic candles (spec §6, "Evidence
status"), and the research freeze (2026-09-27) is untouched by it.

**What the rule decides, on COMPLETED daily bars (`completed_days`, as `Dca` reads them):**

1. **Cadence**, epoch-aligned exactly like `Dca`'s: `day % cadence_days == 0`, so a 30-day
   distribution and a 30-day buy fall on the same days, and a 30-day distribution and a 7-day
   buy share every 210th day (Review Focus 1, refused by the pipeline -- see below).
2. **Price floor**: no sale when the close is below `min_price_floor`. A floor above today's
   close is allowed at construction -- it is the operator's choice to pause distributions, not
   an error.
3. **Drawdown**: no sale when the close is more than `max_drawdown_pct` below the highest high of
   the last `lookback_days` completed bars. Together with the floor, this is what keeps a
   distribution from panic-selling at a bottom.
4. **`floor_qty`**: units never sold. A holding at or under it proposes nothing.
5. **Size**: `target_usd` is the NET cash wanted; gross is `target / (1 - fee - slippage)`, at
   the rates the caller resolved (`SellCosts`, plan R6), and `qty = min(gross / close,
   holding.qty - floor_qty)`. `qty` is an upper bound: the pipeline only ever lowers it.

**A skipped distribution is NOT carried forward.** The next cadence day distributes one
`target_usd`, never two: a catch-up sale after a drawdown or floor gate lifts is exactly the
sale those gates exist to avoid. The rule has no state, so there is nothing to carry.

**What the rule does NOT decide -- the pipeline owns it (plan R11-R14):** flooring to the venue's
`base_increment` and slicing to rail 2's `max_per_order_usd` (`sleeve.slice_qty`); the same-day
DCA exclusion and `min_hold_days` on the tranches the FIFO sale would consume
(`sleeve.sleeve_refusal`, which reads `min_hold_days` off this rule's `params`); and one proposal
per product per UTC day (`sleeve.proposed_today`). A rule cannot know the increment, the config
cap, or what the ledger bought today; one shared pipeline makes those identical for every
sleeve-sell kind and for the sim.

**Preview-only (S2).** `Execution = Literal["preview"]`: no `execution: auto` value exists, so
`keel rules add` refuses one and the constructor refuses one. Every `Reduction` this rule emits
is recorded as a `sell_proposals` row by `executor.reduce` and placed nowhere. The kind is
registered (`agent.RULE_REGISTRY`) so `rules add` can create it, and is excluded from `rules
seed` and the wizard (`agent.seedable_kinds`, plan R20): its `target_usd` and `min_price_floor`
are required, and a seeded seller with invented defaults is what no one should get by accident.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from keel.strategy.reduction import Holding, Reduction, SellCosts
from keel.strategy.rules.base import Rule, Setup, completed_days
from keel.types import Candle, Granularity

_SECONDS_PER_DAY = 86_400

#: v1 execution is preview-only (S2). A PLAIN assignment, not the PEP 695 `type` statement: see
#: `base.TradeOutcome` -- `get_type_hints()` must resolve this to a `Literal` for `keel rules add`
#: (`commands.rules._declared_choices`) to refuse `"auto"` before a row is written.
Execution = Literal["preview"]

_ONE = Decimal("1")
_HUNDRED = Decimal("100")


class ReverseDca(Rule):
    """Cadence-based cash distribution from the product's held sleeve (spec §6).

    It never enters (`detect` is `None`) and never exits (`exit_signal` is `False`): its one
    decision is `reduce_signal`, whose only caller is `agent._handle_reductions`.
    """

    PARAM_DOCS: dict[str, str] = {
        "target_usd": (
            "NET cash wanted per distribution; the sale is sized gross, as "
            "target / (1 - fee - slippage). Required."
        ),
        "min_price_floor": (
            "no sale when the completed daily close is below this price. Required. A floor "
            "above today's close is allowed: it pauses distributions."
        ),
        "cadence_days": "days between distributions, epoch-aligned like dca's (e.g. 30).",
        "max_drawdown_pct": (
            "no sale when the close is more than this % below the lookback_days high."
        ),
        "lookback_days": "window, in completed daily candles, for that high.",
        "floor_qty": (
            "units never sold; a distribution never takes the holding below it, and a holding "
            "at or under it proposes nothing."
        ),
        "min_hold_days": (
            "no sale that would consume a tranche younger than this (read by the sell "
            "pipeline, not by the rule)."
        ),
        "execution": "'preview' only in v1: every distribution is a recorded proposal.",
    }

    promotion_class = "sleeve_sell"
    #: A sleeve policy, not a round trip: the edge report keeps it out of the pooled G2 sample.
    accumulates = True
    decimal_params = ("target_usd", "min_price_floor", "max_drawdown_pct", "floor_qty")

    def __init__(
        self,
        product_id: str,
        target_usd: Decimal,
        min_price_floor: Decimal,
        cadence_days: int = 30,
        max_drawdown_pct: Decimal = Decimal("25"),
        lookback_days: int = 200,
        floor_qty: Decimal = Decimal("0"),
        min_hold_days: int = 30,
        execution: Execution = "preview",
        name: str = "reverse_dca",
    ) -> None:
        if target_usd <= 0:
            raise ValueError("target_usd must be positive")
        if min_price_floor <= 0:
            raise ValueError("min_price_floor must be positive")
        if cadence_days <= 0:
            raise ValueError("cadence_days must be positive")
        if not 0 < max_drawdown_pct <= _HUNDRED:
            raise ValueError("max_drawdown_pct must be in (0, 100]")
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        if floor_qty < 0:
            raise ValueError("floor_qty must not be negative")
        if min_hold_days < 0:
            raise ValueError("min_hold_days must not be negative")
        if execution != "preview":
            # S2: the annotation is a promise to mypy and to `rules add`; this makes it a fact
            # for every other constructor caller (`build_rule_from_params` on a stored row).
            raise ValueError(f"execution must be 'preview' in v1, got {execution!r}")

        self.name = name
        self.product_id = product_id
        self.params: dict = {
            "product_id": product_id,
            "target_usd": target_usd,
            "min_price_floor": min_price_floor,
            "cadence_days": cadence_days,
            "max_drawdown_pct": max_drawdown_pct,
            "lookback_days": lookback_days,
            "floor_qty": floor_qty,
            "min_hold_days": min_hold_days,
            "execution": execution,
        }

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Never an entry: a distribution sells."""
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        """Never an exit: a sleeve policy proposes a PART sale and owns no position's exit."""
        return False

    def reduce_signal(
        self,
        holding: Holding,
        candles_by_tf: dict[Granularity, list[Candle]],
        costs: SellCosts,
    ) -> Reduction | None:
        """Pure. On a cadence day whose close clears the floor and the drawdown gate, a
        `Reduction` sized from the net target; otherwise `None`, with `last_rejection` naming the
        gate (see the module docstring for each)."""
        days = completed_days(candles_by_tf)
        if not days:
            self.last_rejection = {"gate": "no_daily_candles"}
            return None
        latest = days[-1]
        p = self.params
        cadence_day = latest.ts // _SECONDS_PER_DAY
        if cadence_day % p["cadence_days"] != 0:
            self.last_rejection = {"gate": "off_cadence"}
            return None
        close = latest.close
        if close < p["min_price_floor"]:
            self.last_rejection = {
                "gate": "price_floor",
                "close": close,
                "floor": p["min_price_floor"],
            }
            return None
        high = max(c.high for c in days[-p["lookback_days"] :])
        dd_level = high * (_ONE - p["max_drawdown_pct"] / _HUNDRED)
        if close < dd_level:
            self.last_rejection = {"gate": "drawdown", "close": close, "level": dd_level}
            return None
        available = holding.qty - p["floor_qty"]
        if available <= 0:
            self.last_rejection = {"gate": "floor_qty", "held": holding.qty}
            return None
        gross = p["target_usd"] / (_ONE - costs.fee_pct - costs.slippage_pct)
        qty = min(gross / close, available)
        self.last_rejection = None
        return Reduction(
            product_id=self.product_id,
            qty=qty,
            reason=self.name,
            trigger={
                "cadence_day": cadence_day,
                "close": str(close),
                "high": str(high),
                "target_usd": str(p["target_usd"]),
                "gross_usd": str(gross),
                "fee_pct": str(costs.fee_pct),
                "slippage_pct": str(costs.slippage_pct),
                "fee_source": costs.fee_source,
            },
            expected_price=close,
            ts=latest.ts,
        )

    # No `param_space()` override: a spend plan has nothing a sweep may legitimately explore,
    # and the research freeze holds (the empty declaration is `Dca`'s, for the same reason).

    def describe(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "param_space": [spec.plain() for spec in self.param_space()],
        }
