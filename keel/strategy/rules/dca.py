"""Rule 3 -- DCA / dip-buy backbone (spec §8 rule 3, §10.8/§12.1, §12.6).

Scheduled accumulation of a single allowlist asset: a fixed-cadence, fixed-budget market buy,
scaled up when price is down from its recent high ("continuing-through-drawdown beat
perfectly-timed DCA", §12.1). This is a **distinct order class** from the risk-defined rules
(pullback-continuation, RSI mean-reversion): it is a market buy with no stop-loss or take-profit
(accumulation, not a risk-defined trade) and it never exits on a signal.

**Phase-3 note (spec §12.6, §14 rail 10) -- document only, not enforced here:** DCA is exempt
from the rule-trading account-drawdown circuit breaker -- it keeps buying through drawdowns
within its own small capped budget -- but it remains bounded by the halal allowlist, the
per-asset concentration cap, and the kill-switch. Those bounds are enforced by
`execution/guards.py` in Phase 3; this rule (like every Phase 2 rule) only emits *intents*.
"""

from __future__ import annotations

from decimal import Decimal

from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

_SECONDS_PER_DAY = 86_400


class Dca(Rule):
    """Cadence-based dip-buy accumulation rule.

    One `Dca` instance is bound to a single `product_id` -- the candles passed to `detect` are
    assumed to already be for that product, matching how the Task 7 evaluation engine will
    evaluate rules per-product.

    Params: documented per-parameter in `PARAM_DOCS` below (issue #390 C4 / PRD O8) -- the
    single source `keel.commands.rules.describe_params` renders by introspection, so the
    class docstring and the help system cannot drift apart.
    """

    PARAM_DOCS: dict[str, str] = {
        "cadence_days": "days between buys (e.g. 7 = weekly).",
        "budget_usd": "base $ spent on a non-dip cadence hit.",
        "dip_bonus_pct": (
            "linear scale-up -- extra % of budget_usd added per 1 percentage point the "
            "price is below its recent high. E.g. 2 at a 10% drawdown adds a 20% bonus. "
            "0 (default) disables dip-scaling entirely."
        ),
        "lookback_days": (
            "window (in daily candles) used to find the 'recent high' that dip% is "
            "measured against."
        ),
    }

    def __init__(
        self,
        product_id: str,
        cadence_days: int = 7,
        budget_usd: Decimal = Decimal("50"),
        dip_bonus_pct: Decimal = Decimal("0"),
        lookback_days: int = 90,
        name: str = "dca",
    ) -> None:
        if cadence_days <= 0:
            raise ValueError("cadence_days must be positive")
        if budget_usd <= 0:
            raise ValueError("budget_usd must be positive")
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")

        self.name = name
        self.product_id = product_id
        self.params: dict = {
            "product_id": product_id,
            "cadence_days": cadence_days,
            "budget_usd": budget_usd,
            "dip_bonus_pct": dip_bonus_pct,
            "lookback_days": lookback_days,
        }

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Pure. On a cadence boundary, a market-buy long `Setup` sized to `budget_usd` (scaled
        up on dips); off-cadence (or with no daily candles), `None`.
        """
        candles = candles_by_tf.get(Granularity.ONE_DAY)
        if not candles:
            return None

        latest = candles[-1]
        cadence_days = self.params["cadence_days"]
        if (latest.ts // _SECONDS_PER_DAY) % cadence_days != 0:
            return None  # off-cadence: no buy today

        budget_usd: Decimal = self.params["budget_usd"]
        dip_bonus_pct: Decimal = self.params["dip_bonus_pct"]
        lookback_days: int = self.params["lookback_days"]

        window = candles[-lookback_days:]
        recent_high = max(c.high for c in window)
        entry = latest.close

        drawdown_pct = Decimal("0")
        if recent_high > 0 and entry < recent_high:
            drawdown_pct = (recent_high - entry) / recent_high * Decimal("100")

        size_usd = budget_usd * (Decimal("1") + dip_bonus_pct * drawdown_pct / Decimal("100"))

        context = {
            "order_class": "dca",
            "entry_type": "market",
            "cadence_days": cadence_days,
            "budget_usd": budget_usd,
            "dip_bonus_pct": dip_bonus_pct,
            "drawdown_pct": drawdown_pct,
            "recent_high": recent_high,
            "size_usd": size_usd,
            "no_stop": True,
        }

        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=entry,
            # Sentinels: DCA is accumulation, not a risk-defined trade -- there is no stop-loss
            # or take-profit. stop=0 / target=entry keep `Setup.rr` from raising (rr degrades to
            # 0, meaning "not risk-graded") instead of requiring stop/target to be Optional and
            # breaking every other rule's contract.
            stop=Decimal("0"),
            target=entry,
            context=context,
            ts=latest.ts,
        )

    def exit_signal(
        self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]
    ) -> bool:
        """DCA never exits on a signal -- it's accumulation, not a risk-defined trade."""
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}
