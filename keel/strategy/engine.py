"""The per-bar evaluation engine (spec §9, Phase 2 Task 7).

Ties together the confluence scorer (`indicators_cts`), the rule library (`rules/*`), and
market-regime analysis (`analysis.regime`) into a single `evaluate()` pass: for every rule,
run `detect()`, assemble the CTS `context`, `score()` it, gate the result, pick the graded
`entry_technique` from the CTS tier, and emit a `Signal` -- optionally persisting it to the
`signals` table when a `Repository` is supplied. This module only ever emits *intents*
(spec's Global Constraints): no order placement, no rail enforcement (that's Phase 3
`execution/guards.py`).

**Gates applied to every non-exempt `Setup` (spec §9/§17.2, §1.2, §3.2):**
- **Choppy-regime gate:** the rule's trading-timeframe candles must show a tradeable
  `analysis.regime.Condition` (anything but CHOPPY, source-01 §1.2 "choppy => no trade").
- **Kill-zone (R:R floor) gate:** `Setup.rr` must clear `rr_floor` (default `1` -- the "≥1:1
  band" the kill zone widens/narrows around per spec §17.2; confluence narrows it further via
  the promotion gate's stricter 1.5-2 floor, not enforced here).
- **Higher-TF bias gate (§3.2):** when `candles_by_tf` carries a granularity coarser than the
  rule's trading timeframe, that higher-TF `Condition` must not be BEARISH (opposing a long
  setup). Absent higher-TF data, this gate is a no-op (nothing to bias-check against).

**DCA exemption:** rules that emit a no-stop, market-buy `Setup` (`context["order_class"] ==
"dca"` or `context["no_stop"]`, per `strategy/rules/dca.py`) are a *distinct order class* --
scheduled accumulation, not a risk-defined trade. `Setup.rr` degrades to `0` for these by
design (sentinel `stop=0`/`target=entry`), and the entire point of the rule is to keep buying
*through* drawdowns and choppy regimes (spec §12.1). All three gates above are therefore
skipped for this class; CTS scoring and `entry_technique` are still computed and attached to
the emitted `Signal` for consistency/audit, even though a market buy has no signal-candle
concept to grade.

**Exit signals are out of scope here.** `evaluate()`'s signature (rules + read-only market
data) has no notion of currently-held positions to check `Rule.exit_signal()` against --
that's the paper trader's job (Task 8), which owns open-position state.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from keel_core.telemetry import log_event

from keel.analysis import candles as candle_lib
from keel.analysis import indicators, levels, regime
from keel.strategy import indicators_cts
from keel.strategy.rules.base import Action, Rule, Setup, Signal
from keel.types import Candle, Granularity, Side

if TYPE_CHECKING:
    from keel.data.repository import Repository

logger = logging.getLogger(__name__)

# The "≥1:1 band" kill-zone floor (spec §17.2). Distinct from -- and looser than -- the
# promotion gate's stricter R:R >= 1.5-2 floor (spec §6.3), which judges a *rule's* backtested
# track record, not a single candidate `Setup`.
DEFAULT_RR_FLOOR = Decimal("1")

# Granularity coarseness ranking (ascending bar duration) -- lets the engine pick "the finest
# available timeframe" as a trading-TF fallback and "the coarsest available timeframe" as the
# higher-TF bias source, without hardcoding which Granularity values exist.
_GRANULARITY_RANK: dict[Granularity, int] = {g: i for i, g in enumerate(Granularity)}

_FIB_TOLERANCE = Decimal("0.005")


def evaluate(
    rules: list[Rule],
    candles_by_tf: dict[Granularity, list[Candle]],
    weights: dict[str, int] | None = None,
    repo: Repository | None = None,
    rr_floor: Decimal = DEFAULT_RR_FLOOR,
) -> list[Signal]:
    """Run every rule's `detect()`, score + gate the result, and emit passing `Signal`s.

    Rules that don't fire (`detect()` returns `None`) are silently skipped. Rules whose
    `Setup` fails a gate are also skipped -- no `Signal` is emitted or persisted for a
    rejected candidate. When `repo` is given, every *emitted* signal is written to the
    `signals` table (see `_persist_signal`).
    """
    signals: list[Signal] = []

    for rule in rules:
        setup = rule.detect(candles_by_tf)
        if setup is None:
            log_event(logger, logging.INFO, "engine.no_signal", rule=rule.name)
            continue

        trading_gran = _trading_granularity(rule, candles_by_tf)
        trading_candles = candles_by_tf.get(trading_gran, [])

        if not _is_market_buy_class(setup):
            if not _choppy_gate_ok(trading_candles):
                log_event(
                    logger,
                    logging.INFO,
                    "engine.setup_rejected",
                    rule=rule.name,
                    product=setup.product_id,
                    gate="choppy_regime",
                )
                continue
            if not _higher_tf_bias_ok(trading_gran, candles_by_tf):
                log_event(
                    logger,
                    logging.INFO,
                    "engine.setup_rejected",
                    rule=rule.name,
                    product=setup.product_id,
                    gate="higher_tf_bias",
                )
                continue
            if not _kill_zone_ok(setup, rr_floor):
                log_event(
                    logger,
                    logging.INFO,
                    "engine.setup_rejected",
                    rule=rule.name,
                    product=setup.product_id,
                    gate="kill_zone_rr",
                    rr_floor=rr_floor,
                )
                continue

        context = _assemble_cts_context(setup, trading_candles)
        cts_result = indicators_cts.score(context, weights)
        technique = indicators_cts.entry_technique(cts_result.total)

        signal = Signal(
            rule_name=rule.name,
            product_id=setup.product_id,
            action=Action.ENTER,
            side=Side.BUY,
            setup=setup,
            cts_score=cts_result.total,
            entry_technique=technique,
            ts=setup.ts,
        )
        signals.append(signal)
        log_event(
            logger,
            logging.INFO,
            "engine.setup_detected",
            rule=rule.name,
            product=setup.product_id,
            cts_score=cts_result.total,
            technique=technique,
            entry=setup.entry,
            stop=setup.stop,
            target=setup.target,
        )

        if repo is not None:
            _persist_signal(repo, signal, cts_result)

    return signals


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _is_market_buy_class(setup: Setup) -> bool:
    """True for DCA-style accumulation setups (no stop/target risk definition)."""
    context = setup.context
    return bool(context.get("no_stop")) or context.get("order_class") == "dca"


def _choppy_gate_ok(trading_candles: list[Candle]) -> bool:
    """Reject when the trading-TF regime is CHOPPY (source-01 §1.2)."""
    if len(trading_candles) < 2:
        return True
    condition = regime.detect_condition(trading_candles)
    return regime.is_tradeable(condition)


def _higher_tf_bias_ok(
    trading_gran: Granularity, candles_by_tf: dict[Granularity, list[Candle]]
) -> bool:
    """Reject a long setup when the coarsest available higher-TF condition is BEARISH.

    A no-op when `candles_by_tf` carries no granularity coarser than `trading_gran` -- there
    is nothing to bias-check against, so the trading-TF signal stands on its own.
    """
    trading_rank = _GRANULARITY_RANK.get(trading_gran, -1)
    higher = [g for g in candles_by_tf if _GRANULARITY_RANK.get(g, -1) > trading_rank]
    if not higher:
        return True

    bias_gran = max(higher, key=lambda g: _GRANULARITY_RANK[g])
    bias_candles = candles_by_tf[bias_gran]
    if len(bias_candles) < 2:
        return True

    return regime.detect_condition(bias_candles) != regime.Condition.BEARISH


def _kill_zone_ok(setup: Setup, rr_floor: Decimal) -> bool:
    """Reject a risk-defined setup whose R:R doesn't clear `rr_floor` (spec §17.2)."""
    try:
        return setup.rr >= rr_floor
    except (ZeroDivisionError, InvalidOperation):
        return False


def _trading_granularity(rule: Rule, candles_by_tf: dict[Granularity, list[Candle]]) -> Granularity:
    """The rule's trading timeframe: its own `granularity`/`timeframe` attribute if it
    declares one (`PullbackContinuation`, `RsiMeanReversion`), else the finest granularity
    present in `candles_by_tf` (a reasonable default -- the trading TF is normally the most
    granular series available; coarser series are the higher-TF bias data).
    """
    for attr in ("granularity", "timeframe"):
        value = getattr(rule, attr, None)
        if isinstance(value, Granularity):
            return value
    if not candles_by_tf:
        return Granularity.ONE_HOUR
    return min(candles_by_tf, key=lambda g: _GRANULARITY_RANK.get(g, 0))


# ---------------------------------------------------------------------------
# CTS context assembly
# ---------------------------------------------------------------------------


def _assemble_cts_context(setup: Setup, candles: list[Candle]) -> dict[str, Any]:
    """Build the 11-key `indicators_cts.score()` context from `analysis.*` + `setup`.

    Every flag is computed fresh from `analysis.regime`/`analysis.indicators`/
    `analysis.levels`/`analysis.candles` against the trading-TF `candles`, using `setup.entry`
    (and `setup.direction`, pinned to `"long"` in v1) to parameterize level/price-proximity
    checks -- not lifted verbatim from `setup.context`, whose keys are each rule's own
    explainability shape (e.g. `pattern` vs. `candlestick_pattern`) and aren't guaranteed to
    line up with the CTS context's documented key names (see `indicators_cts.py`'s docstring).
    """
    if len(candles) < 2:
        return {}

    last_idx = len(candles) - 1
    condition = regime.detect_condition(candles)
    phase = regime.detect_phase(candles)

    found_levels = levels.find_levels(candles)
    nearest = levels.nearest_level(setup.entry, found_levels)
    sr_touches = nearest.touches if nearest is not None else 0

    fan = indicators.ema_fan(candles)
    ema_fan_aligned = indicators.fan_aligned(fan, last_idx, "bullish")

    closes = [float(c.close) for c in candles]
    rsi_vals = indicators.rsi(closes)
    rsi_now = rsi_vals[-1] if rsi_vals else 50.0
    rsi_extreme = indicators.is_oversold(rsi_now) or indicators.is_overbought(rsi_now)
    divergence = indicators.rsi_divergence(candles, rsi_vals)

    return {
        "condition_aligned": condition == regime.Condition.BULLISH,
        "in_pullback": phase == regime.Phase.PULLBACK,
        "sr_touches": sr_touches,
        "round_number_proximity": levels.is_round_number(setup.entry),
        "deceleration": indicators.deceleration(candles),
        "ema_fan_aligned": ema_fan_aligned,
        "rsi_extreme": rsi_extreme,
        "rsi_divergence": divergence == "bullish",
        "candlestick_pattern": _detect_pattern(candles[-1]),
        "fib_confluence": _fib_confluence(candles, setup.entry),
        # Low-weight seasonality edge, off by default in v1 (spec §9) -- never computed.
        "seasonality": False,
    }


def _detect_pattern(candle: Candle) -> str | None:
    """First recognized signal-candle pattern on `candle`, highest-reliability first
    (`analysis.candles.pattern_confidence` ranks hammer/shooting_star > pin_bar > marubozu >
    doji), or `None` if nothing fires.
    """
    if candle_lib.is_hammer(candle):
        return "hammer"
    if candle_lib.is_shooting_star(candle):
        return "shooting_star"
    if candle_lib.is_pin_bar(candle) is not None:
        return "pin_bar"
    if candle_lib.is_marubozu(candle):
        return "marubozu"
    if candle_lib.is_doji(candle):
        return "doji"
    return None


def _fib_confluence(
    candles: list[Candle], entry: Decimal, tolerance: Decimal = _FIB_TOLERANCE
) -> bool:
    """True if `entry` sits within `tolerance` of a Fib retracement/extension level computed
    from the most recent swing high/low.
    """
    high_idx = levels.swing_highs(candles)
    low_idx = levels.swing_lows(candles)
    if not high_idx or not low_idx:
        return False

    swing_high = candles[high_idx[-1]].high
    swing_low = candles[low_idx[-1]].low
    if swing_high <= swing_low:
        return False

    fib_levels = {
        **indicators.fib_retracements(swing_high, swing_low),
        **indicators.fib_extensions(swing_high, swing_low),
    }
    return any(abs(entry - price) <= price * tolerance for price in fib_levels.values())


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist_signal(repo: Repository, signal: Signal, cts_result: indicators_cts.CTSResult) -> None:
    """Write an emitted `Signal` to the `signals` table via `Repository.insert_signal`
    (schema: `data/db.py`; P3 Task 1 added the typed method).

    `rule_id` is left `NULL` (no DB-backed `rules` row lookup is wired here -- Task 9's
    promotion lifecycle owns that linkage); `indicators` carries the full CTS breakdown +
    setup for audit/explainability; `fired=1` marks this as an actionable, gate-cleared
    signal -- rejected candidates are never persisted at all.
    """
    setup = signal.setup
    payload = {
        "rule_name": signal.rule_name,
        "action": signal.action.value,
        "entry_technique": signal.entry_technique,
        "setup": None
        if setup is None
        else {
            "direction": setup.direction,
            "entry": str(setup.entry),
            "stop": str(setup.stop),
            "target": str(setup.target),
            "context": setup.context,
        },
        "cts_factors": [
            {"name": f.name, "points": f.points, "present": f.present, "detail": f.detail}
            for f in cts_result.factors
        ],
    }
    repo.insert_signal(
        {
            "rule_id": None,
            "product_id": signal.product_id,
            "ts": signal.ts,
            "indicators": json.dumps(payload, default=str),
            "cts_score": str(signal.cts_score),
            "fired": 1,
        }
    )
