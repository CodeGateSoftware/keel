"""`profit_take` -- trim part of a held sleeve when its close stands far enough above the average
entry, preview-only (spec §4, #857).

**Evidence status, stated here because the code must not imply more (spec §4).** The request had
two triggers. The weight-drift half is #831's arm D, pre-registered and bootstrapped: NOT better
than static DCA after fees. The gain-over-entry half, which is the only trigger this rule has, is
UNTESTED: no pre-registered trial has measured it, and the nearest measurements (#442: trailing
exits did worse; #831 D's lead on one path, taken back by the bootstrap) do not favour it. KB §62
gives a theoretical motive under positive autocorrelation, which the KB itself treats as already
served by exits. So this kind claims no edge. It exists so `keel dca trim --preview` and the
proposals log can show what a trim WOULD do, with its fee arithmetic, before the operator sells
by hand.

**No `profit_take` rule is promoted in this build (spec §4, "Recommendation").** The kind is
registered so `rules add` can create one and the cycle can record its proposals; nothing makes it
place a sale. That waits on a pre-registered trial (spec §4's "accumulation policy trial 4"),
which this build does not run -- the research freeze of 2026-09-27 holds, and nothing here is a
parameter search: there is no `param_space()`.

**What the rule decides, on the COMPLETED daily bar (`completed_days`, as every sleeve kind):**

1. **Trigger**: `close >= vwae x (1 + gain_pct / 100)`. `vwae` is `Holding.vwae`, which includes
   each lot's prorated entry fee (spec §3.3, Q4), so "gain" is over what the units actually
   cost.
2. **Size**: `qty = holding.qty x trim_pct / 100`, with `trim_pct` in `[10, 20]` (the request's
   "trim 10-20% to cash"). `qty` is an upper bound: the pipeline floors it to the venue increment
   and slices it to rail 2 (`sleeve.slice_qty`, R11).
3. **Fee gate**: `net = qty x (close x (1 - slippage) - vwae) - qty x close x fee >= min_net_usd`
   -- the spec's formula, with the fee at the rate the caller resolved (`SellCosts`, R6: the
   `config.fees.taker_pct` fallback, labelled as such). A venue-previewed fee, when there is one,
   is recorded on the proposal by `executor.reduce`; it is not decided here. Because `vwae`
   carries the entry fees, the net is net of both legs' fees.

Below a gate the rule returns `None` and names it in `last_rejection` (`gain`, `fee_gate`,
`nothing_held`, `no_daily_candles`); the cycle logs it (`agent.reduction_declined`).

**The drift trigger is NOT a param (spec §4).** Drift is `band_rebalance`'s trigger (§5), which
is not built; keeping the two apart lets a trim be read as one thing.

**What the rule does NOT decide -- the pipeline owns it (plan R11-R15):** flooring and slicing
(`sleeve.slice_qty`); the same-day DCA exclusion; `min_hold_days` on the tranches the FIFO trim
would consume (R12, `sleeve.sleeve_refusal` reads it off `params`); `cooldown_days` from this
rule's last `preview`/`placed` proposal (R15, same function, same `params`); one proposal per
product per UTC day (R14); and arbitration, where `reverse_dca` outranks it (spec §3.6).

**Preview-only (S2).** `Execution = Literal["preview"]`, and the constructor refuses anything
else. Registered in `agent.RULE_REGISTRY`, excluded from `rules seed` and the wizard
(`agent.seedable_kinds`, R20/R38): a seeded seller is what no one should get by accident, even
one whose params all have defaults.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from keel.strategy.reduction import Holding, Reduction, SellCosts
from keel.strategy.rules.base import Rule, Setup, completed_days
from keel.types import Candle, Granularity

#: v1 execution is preview-only (S2). A PLAIN assignment, not the PEP 695 `type` statement, for
#: the reason `reverse_dca.Execution` gives: `get_type_hints()` must resolve it to a `Literal`
#: so `keel rules add` refuses `"auto"` before a row is written.
Execution = Literal["preview"]

_ONE = Decimal("1")
_HUNDRED = Decimal("100")
#: The request's "trim 10-20% to cash" (spec §4), inclusive at both ends.
TRIM_PCT_MIN = Decimal("10")
TRIM_PCT_MAX = Decimal("20")


class ProfitTake(Rule):
    """A trim of `trim_pct` of the product's holding when the completed daily close is
    `gain_pct` over the fee-inclusive average entry and the net clears `min_net_usd` (spec §4).

    It never enters (`detect` is `None`) and never exits (`exit_signal` is `False`): its one
    decision is `reduce_signal`, whose only caller in the cycle is `agent._handle_reductions`,
    and `keel dca trim --preview`'s gain view asks the same method (`sleeve_report.gain_view`).
    """

    PARAM_DOCS: dict[str, str] = {
        "gain_pct": (
            "trigger: the completed daily close must be this % above the average entry "
            "(entry fees included). Default 25."
        ),
        "trim_pct": "the part of the holding a trim sells, in % -- 10 to 20. Default 15.",
        "min_net_usd": (
            "the net profit a trim must clear after the fee and the product's slippage, in "
            "USD. Default 5."
        ),
        "cooldown_days": (
            "days after this rule's last proposal before another (read by the sell pipeline, "
            "not by the rule). Default 30."
        ),
        "min_hold_days": (
            "no trim that would consume a tranche younger than this (read by the sell "
            "pipeline, not by the rule). Default 30."
        ),
        "execution": "'preview' only in v1: every trim is a recorded proposal.",
    }

    promotion_class = "sleeve_sell"
    #: A sleeve policy, not a round trip: the edge report keeps it out of the pooled G2 sample.
    accumulates = True
    decimal_params = ("gain_pct", "trim_pct", "min_net_usd")

    def __init__(
        self,
        product_id: str,
        gain_pct: Decimal = Decimal("25"),
        trim_pct: Decimal = Decimal("15"),
        min_net_usd: Decimal = Decimal("5"),
        cooldown_days: int = 30,
        min_hold_days: int = 30,
        execution: Execution = "preview",
        name: str = "profit_take",
    ) -> None:
        if gain_pct <= 0:
            raise ValueError("gain_pct must be positive")
        if not TRIM_PCT_MIN <= trim_pct <= TRIM_PCT_MAX:
            raise ValueError(f"trim_pct must be in [{TRIM_PCT_MIN}, {TRIM_PCT_MAX}]")
        if min_net_usd < 0:
            raise ValueError("min_net_usd must not be negative")
        if cooldown_days < 0:
            raise ValueError("cooldown_days must not be negative")
        if min_hold_days < 0:
            raise ValueError("min_hold_days must not be negative")
        if execution != "preview":
            # S2: see `ReverseDca.__init__` -- the annotation binds `rules add` and mypy; this
            # binds every other constructor caller (`build_rule_from_params` on a stored row).
            raise ValueError(f"execution must be 'preview' in v1, got {execution!r}")

        self.name = name
        self.product_id = product_id
        self.params: dict = {
            "product_id": product_id,
            "gain_pct": gain_pct,
            "trim_pct": trim_pct,
            "min_net_usd": min_net_usd,
            "cooldown_days": cooldown_days,
            "min_hold_days": min_hold_days,
            "execution": execution,
        }

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Never an entry: a trim sells."""
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        """Never an exit: a trim is a PART sale and owns no position's exit."""
        return False

    def reduce_signal(
        self,
        holding: Holding,
        candles_by_tf: dict[Granularity, list[Candle]],
        costs: SellCosts,
    ) -> Reduction | None:
        """Pure. A `Reduction` of `trim_pct` of the holding when the trigger and the fee gate
        both pass; otherwise `None`, with `last_rejection` naming the gate (module docstring)."""
        days = completed_days(candles_by_tf)
        if not days:
            self.last_rejection = {"gate": "no_daily_candles"}
            return None
        vwae = holding.vwae
        if vwae is None:
            self.last_rejection = {"gate": "nothing_held"}
            return None
        latest = days[-1]
        close = latest.close
        p = self.params
        trigger_price = vwae * (_ONE + p["gain_pct"] / _HUNDRED)
        if close < trigger_price:
            self.last_rejection = {"gate": "gain", "close": close, "trigger_price": trigger_price}
            return None
        qty = holding.qty * p["trim_pct"] / _HUNDRED
        fee_usd = qty * close * costs.fee_pct
        net_usd = qty * (close * (_ONE - costs.slippage_pct) - vwae) - fee_usd
        if net_usd < p["min_net_usd"]:
            self.last_rejection = {
                "gate": "fee_gate",
                "qty": qty,
                "fee_usd": fee_usd,
                "net_usd": net_usd,
                "min_net_usd": p["min_net_usd"],
            }
            return None
        self.last_rejection = None
        return Reduction(
            product_id=self.product_id,
            qty=qty,
            reason=self.name,
            trigger={
                "close": str(close),
                "vwae": str(vwae),
                "gain_pct": str(p["gain_pct"]),
                "trigger_price": str(trigger_price),
                "trim_pct": str(p["trim_pct"]),
                "fee_usd": str(fee_usd),
                "net_usd": str(net_usd),
                "min_net_usd": str(p["min_net_usd"]),
                "fee_pct": str(costs.fee_pct),
                "slippage_pct": str(costs.slippage_pct),
                "fee_source": costs.fee_source,
            },
            expected_price=close,
            ts=latest.ts,
        )

    # No `param_space()` override: the gain half is untested and the research freeze holds, so
    # there is nothing a sweep may explore (spec §4's trial 4 would be pre-registered, not swept).

    def describe(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "param_space": [spec.plain() for spec in self.param_space()],
        }
