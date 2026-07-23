"""The scheduled agent loop, confirm mode (P3 Task 8).

`run_once()` is a single cycle: poll fresh candles (`data.market_feed.poll_once`), reconstruct
the `live` rules (`repo.get_rules("live")` -> real `Rule` instances, `RULE_REGISTRY` below),
`strategy.engine.evaluate` them for ENTER `Signal`s, drive EXIT `Signal`s off currently-held
positions, and run every signal through `execution.executor.execute` (confirm|autonomous, from
`config.auto_trade.mode`) -- respecting the kill-switch (`repo.get_state("kill_switch")`)
throughout. `loop()` is the scheduled wrapper: call `run_once` every `interval_sec` until
`stop_flag()` returns `True`.

**This closes the Phase-2 gap** `strategy/engine.py`'s own docstring calls out: `evaluate()`
only ever emits ENTER signals (rules + read-only market data, no notion of what's currently
held). Something has to own position state and ask each rule's `exit_signal(held, candles_by_tf)`
whether to close it -- in Phase 2 that was deferred to "the paper trader's job"; in live trading
it's this module's job. Position ownership is tracked in `agent_state` under
`position_rule:<product_id>` (a dict carrying the owning rule's name plus the entry context a
`trade_outcomes` row needs: `opened_at`, `entry_fill`, `qty`, `entry_fee`) -- the
`orders` table has no column for it (live fills leave `rule_id` NULL, per `executor.py`'s own
docstring: paper mode encodes `rule_name` in `raw_response`, but live orders don't), so this
loop is the one place that source of truth lives, set the moment an ENTER places
(`ExecutionResult.placed`) and cleared the moment the matching EXIT places.

It is exit-rule OWNERSHIP state, not a position ledger: it is overwritten on every placed entry
rather than accumulated, so its `qty` is the last tranche's, not the position's. Anything needing
a true holding must use `_held_position` (the filled-orders audit log) instead -- see
`_mark_to_market_equity`, where valuing equity from this key manufactured a phantom drawdown.

**Rule reconstruction.** `rules.kind`/`rules.params` (P3 Task 1's `Repository.get_rules`) round-
trip through `json.dumps`/`json.loads`, so `params` on the way back out is JSON-plain (strings/
numbers/lists) even though the real rule constructors want `Decimal`s, a `Granularity` enum, and
tuples (`PullbackContinuation.granularity`/`buffer_ticks`/`ema_periods`,
`RsiMeanReversion.timeframe`/its several `Decimal` fields). `RULE_REGISTRY` + `_build_rule` are
the coercion boundary: a caller (this module, or a future CLI command that lists live rules)
only ever deals in real `Rule` instances, never in raw JSON dicts.

**Confirm mode places via a caller-supplied `confirm_fn`.** `run_once`/`loop` take an optional
`confirm_fn`, which `executor.execute` calls with the broker preview. With `confirm_fn=None` --
the default, and what any caller that does not supply one gets -- confirm mode **fails closed**:
the order is previewed and logged but never placed. The CLI supplies a real interactive prompt.

**Autonomy is a profile choice, not a config mode.** `_effective_mode` returns `"autonomous"`
only when `config.auto_trade.mode == "confirm"` **and**
`repo.get_profile().is_autonomous(now_ts)` is true.
The profile is read fresh every cycle and never cached, so `keel autonomy off` takes effect on
the next cycle rather than the next restart (a cycle in flight can still place; `keel kill` is
what stops trading immediately). The check lives inside `run_once`, not only at the
CLI, so an in-process caller cannot obtain autonomy the CLI would have refused; an absent or
unreadable profile row reads as not-autonomous. In every mode, `guards.check` runs FIRST and is
un-overridable -- autonomy changes who is asked, never what is allowed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from keel_core.products import quote_currency_of
from keel_core.telemetry import bind_cycle, log_event, new_cycle_id, unbind_cycle

from keel.config import Config
from keel.data import market_feed
from keel.data.repository import Repository
from keel.execution import equity as equity_mod
from keel.execution import executor, guards, reconcile, streak
from keel.execution.executor import ExecutionResult, _fetch_available_quote
from keel.execution.guards import FEED_STALENESS_CYCLES
from keel.strategy import engine
from keel.strategy.paper import PaperTrader
from keel.strategy.rules.base import Action, Rule, Setup, Signal
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.pullback_continuation import PullbackContinuation
from keel.strategy.rules.rsi_meanrev import RsiMeanReversion
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Granularity, Side

logger = logging.getLogger(__name__)

# -- rule reconstruction: DB row (kind, JSON-plain params) -> a real Rule instance -------------

RULE_REGISTRY: dict[str, type[Rule]] = {
    "pullback_continuation": PullbackContinuation,
    "rsi_meanrev": RsiMeanReversion,
    "dca": Dca,
    "turtle_breakout": TurtleBreakout,
}

# Constructor kwargs that must be coerced back from JSON-plain (str) to `Decimal`, per kind.
_DECIMAL_PARAMS: dict[str, tuple[str, ...]] = {
    "pullback_continuation": ("buffer_ticks",),
    "rsi_meanrev": (
        "atr_mult",
        "fixed_stop_pct",
        "fixed_rr",
        "level_tolerance",
        "support_proximity_pct",
    ),
    "dca": ("budget_usd", "dip_bonus_pct"),
    "turtle_breakout": ("atr_stop_mult", "target_rr"),
}

# The constructor kwarg holding a `Granularity`, stored as its `.value` string, per kind.
_GRANULARITY_PARAMS: dict[str, str] = {
    "pullback_continuation": "granularity",
    "rsi_meanrev": "timeframe",
}


def _build_rule(row: dict[str, Any]) -> Rule:
    """Reconstruct a real `Rule` instance from a `repo.get_rules()` row.

    Raises `ValueError` for a `kind` not in `RULE_REGISTRY` -- an unrecognized rule kind in the
    `live` set is a configuration error, not something to silently skip (consistent with this
    codebase's fail-loud posture elsewhere, e.g. `executor.execute`'s unknown-mode check).
    """
    kind = row["kind"]
    rule_cls = RULE_REGISTRY.get(kind)
    if rule_cls is None:
        raise ValueError(
            f"agent: no rule registered for kind {kind!r} -- known kinds: "
            f"{sorted(RULE_REGISTRY)!r}"
        )

    kwargs = dict(row.get("params") or {})

    for key in _DECIMAL_PARAMS.get(kind, ()):
        if key in kwargs and kwargs[key] is not None:
            kwargs[key] = Decimal(str(kwargs[key]))

    gran_key = _GRANULARITY_PARAMS.get(kind)
    if gran_key is not None and gran_key in kwargs:
        kwargs[gran_key] = Granularity(kwargs[gran_key])

    if kind == "pullback_continuation":
        if "ema_periods" in kwargs:
            kwargs["ema_periods"] = tuple(kwargs["ema_periods"])
        if "signal_patterns" in kwargs:
            kwargs["signal_patterns"] = tuple(kwargs["signal_patterns"])

    return rule_cls(**kwargs)


# -- freshness -----------------------------------------------------------------------------

_GRANULARITY_ORDER: dict[Granularity, int] = {g: i for i, g in enumerate(Granularity)}


def _finest_granularity(granularities: list[Granularity]) -> Granularity | None:
    """The most granular (shortest bar) configured timeframe -- the best single signal of
    "is fresh market data still arriving", matching `strategy.engine._trading_granularity`'s
    own "finest available" fallback. Checking staleness against *every* configured granularity
    would be wrong: a `ONE_DAY` series is expected to go up to ~24h between closed candles, far
    longer than any sane `interval_sec`-scaled staleness window, so it would spuriously flag a
    perfectly healthy feed as stale.
    """
    if not granularities:
        return None
    return min(granularities, key=lambda g: _GRANULARITY_ORDER.get(g, 0))


# -- held-position reconstruction (mirrors execution.executor._held_position) -----------------


def _held_position(repo: Repository, product_id: str) -> tuple[Decimal, Decimal]:
    """Net held qty + average cost basis for `product_id`, from filled live orders -- the same
    audit-log-as-source-of-truth approach `executor._held_position`/`guards.py` use.
    """
    buy_qty = Decimal("0")
    buy_cost = Decimal("0")
    sell_qty = Decimal("0")
    for order in repo.get_orders(mode="live", product_id=product_id, status="filled"):
        price = order.get("actual_fill") or order.get("limit_price") or order.get("expected_fill")
        qty = order["qty"] or Decimal("0")
        if order["side"] == Side.BUY.value:
            buy_qty += qty
            buy_cost += qty * (price or Decimal("0"))
        elif order["side"] == Side.SELL.value:
            sell_qty += qty

    net_qty = buy_qty - sell_qty
    avg_cost = (buy_cost / buy_qty) if buy_qty > 0 else Decimal("0")
    return (net_qty if net_qty > 0 else Decimal("0")), avg_cost


def _open_tranche(
    repo: Repository,
    product_id: str,
    rule_name: str,
    order: dict[str, Any] | None,
    result: ExecutionResult,
    now_ts: int,
) -> None:
    """Record the newly opened tranche in the `positions` ledger and point it at its bracket.

    The ledger is what a later exit attributes P&L against, so a tranche whose entry price or qty
    could not be read is NOT recorded: `record_closed_trade` already refuses to guess a missing
    entry price, and a ledger row carrying `None` would either crash the arithmetic or fabricate
    a number nobody observed. The order still stands and `_held_position` still sees it; only the
    attribution is withheld, and loudly.

    `result.bracket_order_id` is `None` for DCA (no stop, so no bracket) and for a bracket the
    rails vetoed. Neither is an error here -- the tranche is real either way, and reconciliation
    simply has no bracket to resolve back to it until one is placed.
    """
    entry_fill = None if order is None else order["actual_fill"]
    qty = None if order is None else order["qty"]
    if entry_fill is None or qty is None:
        log_event(
            logger,
            logging.WARNING,
            "agent.tranche_not_recorded",
            product=product_id,
            rule=rule_name,
            order_id=result.order_id,
            detail=(
                "the entry order reported no fill price or qty, so this tranche has no entry "
                "context -- its exit will be recorded with no P&L rather than a guessed one"
            ),
        )
        return

    position_id = repo.open_position(
        product_id=product_id,
        rule_name=rule_name,
        opened_at=now_ts,
        qty=qty,
        # The entry leg's fee, carried so `record_closed_trade` can net BOTH legs (the sim does).
        # Nothing else preserves it: the exit's order row knows only its own fee.
        entry_fee=order["fee"] or Decimal("0"),
        entry_fill=entry_fill,
    )
    if result.bracket_order_id is not None:
        repo.set_position_bracket(position_id, result.bracket_order_id)


def _position_state(repo: Repository, product_id: str) -> dict[str, Any] | None:
    """The tracked position for `product_id`, or `None` if nothing is held.

    `agent_state["position_rule:<product>"]` used to be a bare rule-name string and is now a dict
    carrying the entry context a `trade_outcomes` row needs. A database written by the previous
    version still holds the string form, so it is normalised here rather than migrated: the entry
    fields read back as `None`, which the outcome producer treats as "cannot attribute this trade"
    rather than guessing a price.
    """
    raw = repo.get_state(f"position_rule:{product_id}")
    if raw is None:
        return None
    if isinstance(raw, str):
        return {
            "rule_name": raw,
            "opened_at": None,
            "entry_fill": None,
            "qty": None,
            "entry_fee": None,
        }
    return raw


def _mark_to_market_equity(
    repo: Repository,
    broker: Any,
    products: list[str],
    price_by_product: dict[str, Decimal],
    quote_currency: str,
) -> Decimal | None:
    """Quote balance + mark-to-market value of every open position, or `None` if the quote
    balance could not be read.

    Unrealized P&L is included on purpose: a breaker that saw only realized P&L would read 0%
    while a position bled and would notice only after the loss was booked -- backwards for a
    circuit breaker, which has to fire WHILE you are losing.

    A held product with no fresh price this cycle is valued at its ENTRY fill rather than
    dropped: dropping it would understate equity and could trip rail 11 on a data gap rather
    than on a loss. That is why this iterates `products` and not `price_by_product` -- a product
    missing from the price map is exactly the case the fallback exists for.

    ⚠️ **Balances are summed at face value, with NO FX conversion.** That is correct for the
    supported case -- one settlement currency, whose products all share that quote leg -- and it
    is why `config.quote_currency` and the products' legs should agree. An account mixing, say,
    USD and EUR would have them added 1:1 and over-read equity, which (the HWM being monotonic)
    would arm rail 11 permanently. Reaching that state needs a product whose quote leg differs
    from the settlement currency, which the admission screen rejects; a live-seeded rule can
    bypass admission, so this is recorded as a known bound rather than claimed impossible.

    Settled cash is summed across EVERY currency in play: `quote_currency` plus the quote leg of
    each product being valued. Counting only the configured currency under-reads an account whose
    cash sits in what its products actually settle in (a `BTC-USD` deployment configured for
    USDC) -- and because the HWM is monotonic, that under-read arms rail 11 on a phantom
    drawdown permanently. Currencies with no account contribute nothing rather than failing.

    `None`, rather than a partial total, when NO quote balance could be read at all: equity is
    simply unknowable then, and a wrong one corrupts the high-water mark PERMANENTLY (an HWM
    never falls, so an under-read arms the breaker on a phantom drawdown from then on).
    """
    currencies: list[str] = []
    scanned = (*products, *repo.held_products())
    for candidate in (quote_currency, *(quote_currency_of(p) for p in scanned)):
        upper = (candidate or "").upper()
        if upper and upper not in currencies:
            currencies.append(upper)

    balances = [(c, _fetch_available_quote(broker, c)) for c in currencies]
    if all(balance is None for _, balance in balances):
        # Nothing readable at all -- not "zero cash", genuinely unknown.
        return None
    quote = sum((balance for _, balance in balances if balance is not None), Decimal("0"))

    # Quantities come from `_held_position` (the filled-orders audit log), NEVER from
    # `position_rule:<product>`. That key is exit-rule OWNERSHIP state: it is overwritten on
    # every placed entry rather than accumulated, so it holds the last tranche's qty, not the
    # position. Valuing equity from it undercounts every accumulated position -- and because the
    # high-water mark is monotonic, that undercount becomes a permanent phantom drawdown that
    # arms rail 11 on a flat account. See `test_equity_counts_the_net_held_qty_across_...`.
    #
    # The product set is the UNION of the live-rule products and every product with a non-zero
    # holding: `products` derives from the live rule set, so retiring a rule while its position
    # is still open would otherwise drop that holding out of equity in a single step.
    valued: set[str] = set()
    total = quote
    for product_id in (*products, *repo.held_products()):
        if product_id in valued:
            continue
        valued.add(product_id)

        qty, avg_cost = _held_position(repo, product_id)
        if qty <= 0:
            continue

        # A held product with no fresh price is valued at its cost basis rather than dropped:
        # dropping it understates equity and would trip rail 11 on a DATA GAP rather than on a
        # loss. `avg_cost` comes from the same audit log as `qty`, so the two always agree.
        mark = price_by_product.get(product_id)
        if mark is None:
            mark = avg_cost
        if mark <= 0:
            continue
        total += qty * mark
    return total


def _seed_paper_account_if_needed(
    repo: Repository,
    broker: Any,
    config: Config,
    products: list[str],
    price_by_product: dict[str, Decimal],
    now_ts: int,
    paper_trader: PaperTrader,
) -> None:
    """Enforce the equity-state mode stamp and seed the synthetic paper account once.

    On a paper->live or live->paper flip, clear the shared HWM/history/drawdown scalars
    (same keys `keel reset-hwm` clears) before this cycle's update_drawdown, so a synthetic
    HWM never poisons live equity (or vice versa). Seed `paper_cash_usdc` on first paper run
    from real broker mark-to-market equity, falling back to `config.paper.starting_equity_usd`.
    """
    if repo.get_state("equity_state_mode") != "paper":
        repo.set_state("equity_high_water_mark", None)
        repo.set_state("drawdown_total_pct", Decimal("0"))
        repo.set_state("drawdown_weekly_pct", Decimal("0"))
        repo.set_state("equity_history", [])
        repo.set_state("equity_state_mode", "paper")
    if paper_trader.get_cash() is None:
        seed = _mark_to_market_equity(
            repo, broker, products, price_by_product, config.quote_currency
        )
        if seed is None:
            fallback = config.paper.starting_equity_usd
            seed = fallback if fallback > 0 else None
        if seed is None:
            log_event(logger, logging.WARNING, "agent.paper_seed_unavailable")
            return
        paper_trader.seed_cash(seed, now_ts)


def _paper_resolve_bars(
    trader: PaperTrader,
    product_id: str,
    candles_by_tf: dict,
    granularities: list,
) -> None:
    """Advance the paper position on the newest bar so stops and targets can fire.

    Live trading resolves stops at the broker; paper has to be walked forward explicitly, or
    positions would only ever close on an explicit rule exit and the track record would be all
    winners-that-never-stopped-out.

    Uses the FINEST available timeframe -- a stop is touched intrabar, and a daily bar would
    miss touches a shorter bar sees.
    """
    for granularity in granularities:
        candles = candles_by_tf.get(granularity) or []
        if candles:
            trader.on_candle(product_id, candles[-1])
            return


def _paper_enter(
    trader: PaperTrader,
    signal: Signal,
    repo: Repository,
    config: Config,
    now_ts: int,
) -> executor.ExecutionResult:
    """Run the offline-computable rails, then record a paper fill if they pass.

    Paper runs the rails DELIBERATELY (see `guards.check`'s `offline` docstring): the promotion
    gate is scored on this track record, so a rehearsal that skipped them would promote a
    strategy on evidence of trades live trading would have vetoed.

    `broker=None` is passed to `_build_intent` on purpose: `_fetch_available_quote` returns
    `None` for a null broker without logging, and rail 13 -- the rail that would have consumed
    that balance -- is one of the two `offline=True` skips anyway, because paper has no live
    account to read a balance from.
    """
    def _result(placed, order_id=None, vetoed_by=None, reason=""):
        return ExecutionResult(
            placed=placed,
            order_id=order_id,
            vetoed_by=list(vetoed_by or []),
            preview=None,
            reason=reason,
        )

    intent = executor._build_intent(signal, None, repo, config, now_ts)
    if intent is None:
        return _result(False, reason="paper: nothing to size")

    verdict = guards.check(intent, repo, config, now_ts, offline=True)
    if not verdict.ok:
        return _result(False, vetoed_by=verdict.violations, reason="paper: vetoed by rails")

    order_id = trader.on_signal(signal)
    if order_id is None:
        return _result(False, reason="paper: no fill (position already open)")
    return _result(
        True,
        order_id=order_id,
        reason=f"paper: filled (skipped rails: {', '.join(verdict.skipped_rails)})",
    )


def _handle_exits(
    product_id: str,
    product_rules: list[Rule],
    candles_by_tf: dict[Granularity, list[Any]],
    repo: Repository,
    broker: Any,
    config: Config,
    mode: str,
    now_ts: int,
    confirm_fn: executor.ConfirmFn | None = None,
) -> list[ExecutionResult]:
    """Ask the owning rule whether to close `product_id`'s held position, and execute the EXIT
    if it fires. A no-op (`[]`) when there's nothing held, or no rule is on record as owning it
    (`position_rule:<product_id>` unset -- e.g. a position this loop never opened).
    """
    qty, avg_cost = _held_position(repo, product_id)
    if qty <= 0:
        return []

    position = _position_state(repo, product_id)
    owning_rule_name = None if position is None else position["rule_name"]
    if not owning_rule_name:
        return []

    owning_rule = next((r for r in product_rules if r.name == owning_rule_name), None)
    if owning_rule is None:
        return []

    stop = repo.get_state(f"open_stop:{product_id}", default=avg_cost)
    held_setup = Setup(
        product_id=product_id,
        direction="long",
        entry=avg_cost,
        stop=stop,
        target=avg_cost,
        context={},
        ts=now_ts,
    )
    if not owning_rule.exit_signal(held_setup, candles_by_tf):
        return []

    exit_signal = Signal(
        rule_name=owning_rule.name,
        product_id=product_id,
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="market",
        ts=now_ts,
    )
    result = executor.execute(
        exit_signal, broker, repo, config, mode, confirm_fn=confirm_fn, now_ts=now_ts
    )
    if result.placed:
        exit_order = repo.get_order(result.order_id) if result.order_id is not None else None
        if (
            exit_order is not None
            # An exit with no observed fill price cannot be valued. Unreachable today (exits are
            # always market orders, so `_initial_status` is `filled`), but a limit exit would
            # otherwise raise TypeError on `(None - entry_fill)` from inside `run_once` -- and
            # the producer already refuses to guess a missing ENTRY price for the same reason.
            and exit_order["actual_fill"] is not None
        ):
            _close_tranches(
                repo,
                config,
                product_id=product_id,
                exit_order=exit_order,
                # DERIVED, never asserted: §12.6 exempts DCA from the streak, and the owning
                # rule is what decides. Hardcoding False was safe only while `Dca.exit_signal`
                # returned False unconditionally -- an invariant held by an unrelated module
                # with nothing asserting it. `sim/portfolio_sim.py` passes this explicitly for
                # the same reason; the live path must match.
                is_dca=owning_rule.name == "dca",
                now_ts=now_ts,
            )
        # Clear the bracket's state alongside the position. These describe an exchange-side
        # bracket that no longer exists once the position is out; left behind they poison the
        # NEXT trade in this product -- rail 9 would veto a legitimate entry whose stop sits
        # below this closed trade's stop, and the held-setup above would be built from it.
        repo.set_state(f"position_rule:{product_id}", None)
        repo.set_state(f"open_stop:{product_id}", None)
        repo.set_state(f"open_target:{product_id}", None)
    return [result]


def _close_tranches(
    repo: Repository,
    config: Config,
    *,
    product_id: str,
    exit_order: dict[str, Any],
    is_dca: bool,
    now_ts: int,
) -> None:
    """Record ONE outcome per open tranche and close them all, oldest first.

    A rule exit sells the whole held position (`_build_intent` sizes from `_held_position`), so
    every open tranche of this product is closed by it. Booking a single aggregate outcome
    against one blob of entry context is precisely the mis-attribution the `positions` ledger
    exists to end: with two tranches, the older one's P&L would be computed against the newer
    one's entry price.

    The exit order carries ONE fee for the whole sale, so it is apportioned pro-rata by qty. The
    last tranche takes the rounding remainder rather than letting the parts fail to sum to the
    whole -- understating total cost would flatter `pnl_net`, whose SIGN is what rail 16 counts.

    A product with no ledger rows (opened before the v4 ledger, or a tranche whose entry context
    was unreadable) records nothing rather than guessing an entry price.
    """
    positions = repo.get_open_positions(product_id)
    if not positions:
        log_event(
            logger,
            logging.WARNING,
            "agent.exit_without_ledger_tranche",
            product=product_id,
            order_id=exit_order["id"],
        )
        return

    exit_fill = exit_order["actual_fill"]
    total_fee = exit_order["fee"] or Decimal("0")
    total_qty = sum((p["qty"] for p in positions), Decimal("0"))
    apportioned = Decimal("0")

    for index, position in enumerate(positions):
        is_last = index == len(positions) - 1
        if is_last or total_qty <= 0:
            fee_share = total_fee - apportioned
        else:
            fee_share = (total_fee * position["qty"] / total_qty).quantize(Decimal("0.00000001"))
            apportioned += fee_share

        streak.record_closed_trade(
            repo,
            config,
            product_id=product_id,
            position=position,
            exit_fill=exit_fill,
            exit_qty=position["qty"],
            fees=fee_share,
            is_dca=is_dca,
            now_ts=now_ts,
        )
        repo.close_position(position["id"], closed_at=now_ts)


# -- one cycle -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopResult:
    """The outcome of one `run_once` cycle."""

    ts: int
    skipped: bool
    skip_reason: str | None
    mode: str | None
    polled: int
    products: list[str] = field(default_factory=list)
    stale_products: list[str] = field(default_factory=list)
    enter_signals: list[Signal] = field(default_factory=list)
    enter_results: list[ExecutionResult] = field(default_factory=list)
    exit_results: list[ExecutionResult] = field(default_factory=list)


def _effective_mode(config: Config, repo: Repository, now_ts: int) -> str:
    """The executor mode for this cycle: `"autonomous"` or `"confirm"`.

    Two independent switches, deliberately not conflated into one enum:

    * `config.auto_trade.mode` says whether this is real money at all (`paper` never reaches an
      executor mode -- it routes to the paper path upstream).
    * `repo.get_profile().is_autonomous(now_ts)` says whether the user has opted out of being
      asked -- honouring any expiry the user set.

    `"autonomous"` is returned ONLY when the config is live (`confirm`) **and** the profile says
    so. Anything else -- an unknown mode, an absent profile row, a damaged database -- yields
    `"confirm"`, which with no `confirm_fn` places nothing at all. The failure direction is
    always toward asking a human.

    **The profile is read here, fresh, once per cycle and never cached**, so `keel autonomy off`
    takes effect on the NEXT order rather than the next restart. This mirrors rail 14's
    allowance, which is re-read live for exactly the same reason.

    This lives inside `run_once`'s own call path, not only at the CLI, so a caller driving
    `run_once` in-process cannot obtain autonomy the CLI would have refused.
    """
    if config.auto_trade.mode != "confirm":
        return "confirm"
    return "autonomous" if repo.get_profile().is_autonomous(now_ts) else "confirm"


def run_once(
    broker: Any,
    repo: Repository,
    config: Config,
    now_ts: int,
    confirm_fn: executor.ConfirmFn | None = None,
) -> LoopResult:
    """One agent cycle: poll -> (kill-switch / stale-data gates) -> evaluate -> exits -> entries.

    The kill-switch is checked *before* anything else (no poll, no evaluation, no orders) --
    `repo.get_state("kill_switch", default=True)` fails closed exactly like `guards.check`'s
    rail 12, so an agent that has never been explicitly `resume`d never trades. Per-product
    staleness (`market_feed.is_fresh`) is checked after polling and only skips *that* product;
    the cycle itself still runs (and `last_feed_ts` still updates) for the rest.
    """
    cycle_token = bind_cycle(new_cycle_id())
    try:
        log_event(logger, logging.INFO, "agent.cycle_start", now_ts=now_ts)

        if repo.get_state("kill_switch", default=True):
            log_event(logger, logging.INFO, "agent.cycle_skipped", reason="kill_switch")
            return LoopResult(
                ts=now_ts, skipped=True, skip_reason="kill_switch", mode=None, polled=0
            )

        # Which rules run depends on the MODE, because the lifecycle is
        # candidate -> paper -> live (`promotion._PROMOTE_NEXT`). Paper mode exists to prove
        # `paper`-status rules FORWARD before they earn real money; loading `live` rules there
        # would rehearse what is already trading and never advance a candidate, which is the
        # opposite of what the proving gate is for.
        rule_status = "paper" if config.auto_trade.mode == "paper" else "live"
        rules = [_build_rule(row) for row in repo.get_rules(rule_status)]
        products = sorted({p for p in (getattr(r, "product_id", None) for r in rules) if p})
        granularities = list(config.market_data.granularities)

        polled = market_feed.poll_once(broker, repo, products, granularities, now_ts=now_ts)
        log_event(
            logger,
            logging.INFO,
            "agent.feed_polled",
            candles_polled=polled,
            rule_count=len(rules),
            products=products,
        )
        # The feed-health heartbeat `guards.check`'s rail 12 reads -- nothing else in the system
        # sets this key, so the loop that actually calls `poll_once` each cycle is responsible
        # for recording that the check happened, independent of whether it found anything new.
        repo.set_state("last_feed_ts", now_ts)

        mode = _effective_mode(config, repo, now_ts)
        # `mode: paper` is not an executor mode (see `_effective_mode`); it routes to the
        # PAPER path instead, which never touches the broker. Constructed once per cycle and
        # rehydrated from the orders table, so a per-cycle agent resumes its open positions.
        paper_trader = PaperTrader(repo) if config.auto_trade.mode == "paper" else None
        # What this cycle actually DID, for the log line and `LoopResult`. `_effective_mode`
        # returns the executor string `"confirm"` for any non-live config -- paper included --
        # so reporting it verbatim told the user a confirm-mode (live) run happened when the
        # cycle only ever hit the paper path. `executor.execute`/`_handle_exits` still get the
        # real executor `mode`; only what we surface to the user changes.
        reported_mode = "paper" if paper_trader is not None else mode
        log_event(logger, logging.INFO, "agent.mode_resolved", mode=reported_mode)
        max_age_sec = config.auto_trade.interval_sec * FEED_STALENESS_CYCLES
        finest = _finest_granularity(granularities)

        enter_signals: list[Signal] = []
        enter_results: list[ExecutionResult] = []
        exit_results: list[ExecutionResult] = []
        stale_products: list[str] = []

        # Reconcile FIRST, before equity and before any entry. A bracket that filled since the
        # last cycle has already changed the position and the cash balance; reading equity or
        # sizing a new entry against stale `pending` state would mark sold inventory as still
        # held and compute rail 11's drawdown off a position that no longer exists.
        reconcile.reconcile_open_orders(broker, repo, config, now_ts)

        # Rail 11's inputs, refreshed BEFORE any entry this cycle. `poll_once` above has already
        # written the candles, so every product's latest price is readable here -- and it has to
        # be done now, not after the loop: `guards.check` reads these scalars from inside
        # `executor.execute`, which runs *within* the loop, so producing them afterwards would
        # let through exactly the entries the breaker exists to veto and fire a cycle late.
        latest_price_by_product: dict[str, Decimal] = {}
        if finest is not None:
            for product_id in products:
                product_candles = repo.get_candles(product_id, finest)
                if product_candles:
                    latest_price_by_product[product_id] = product_candles[-1].close

        # Paper now advances rail 11's scalars too: the synthetic account is seeded (once, from
        # real mark-to-market equity or the config fallback) and marked to market every cycle
        # exactly like a live account, via `_seed_paper_account_if_needed` + `PaperTrader.equity`.
        # `equity_state_mode` records which account last drove the shared HWM/drawdown keys, so a
        # paper<->live flip clears them first rather than letting one mode's scalars poison the
        # other's (see `_seed_paper_account_if_needed`'s docstring).
        if paper_trader is not None:
            _seed_paper_account_if_needed(
                repo, broker, config, products, latest_price_by_product, now_ts, paper_trader
            )
            equity_now = paper_trader.equity(latest_price_by_product)
        else:
            equity_now = _mark_to_market_equity(
                repo, broker, products, latest_price_by_product, config.quote_currency
            )
        if equity_now is None:
            # Leave the previous cycle's scalars in place -- see `_mark_to_market_equity`.
            log_event(
                logger,
                logging.INFO if paper_trader is not None else logging.WARNING,
                "agent.equity_unavailable",
                paper=paper_trader is not None,
            )
        else:
            # The symmetric live-side mode stamp/clear -- only right before a REAL update, so an
            # unreadable broker (equity_now is None, handled above) never gets to zero out the
            # previous cycle's scalars on the strength of a stamp alone.
            if paper_trader is None and repo.get_state("equity_state_mode") != "live":
                repo.set_state("equity_high_water_mark", None)
                repo.set_state("drawdown_total_pct", Decimal("0"))
                repo.set_state("drawdown_weekly_pct", Decimal("0"))
                repo.set_state("equity_history", [])
                repo.set_state("equity_state_mode", "live")
            equity_mod.update_drawdown(repo, equity=equity_now, now_ts=now_ts)

        for product_id in products:
            if finest is not None and not market_feed.is_fresh(
                repo, product_id, finest, now_ts, max_age_sec
            ):
                stale_products.append(product_id)
                log_event(
                    logger,
                    logging.INFO,
                    "agent.feed_stale",
                    product=product_id,
                    finest=finest,
                    max_age_sec=max_age_sec,
                )
                continue

            product_rules = [r for r in rules if getattr(r, "product_id", None) == product_id]
            candles_by_tf = {g: repo.get_candles(product_id, g) for g in granularities}

            if paper_trader is not None:
                _paper_resolve_bars(paper_trader, product_id, candles_by_tf, granularities)

            product_exit_results = _handle_exits(
                product_id, product_rules, candles_by_tf, repo, broker, config, mode, now_ts,
                confirm_fn=confirm_fn,
            )
            for exit_result in product_exit_results:
                log_event(
                    logger,
                    logging.INFO,
                    "agent.exit_evaluated",
                    product=product_id,
                    placed=exit_result.placed,
                    reason=exit_result.reason,
                )
            exit_results.extend(product_exit_results)

            product_signals = engine.evaluate(product_rules, candles_by_tf, repo=repo)
            log_event(
                logger,
                logging.INFO,
                "agent.signals_evaluated",
                product=product_id,
                rule_count=len(product_rules),
                signal_count=len(product_signals),
            )
            for signal in product_signals:
                enter_signals.append(signal)
                if paper_trader is not None:
                    result = _paper_enter(paper_trader, signal, repo, config, now_ts)
                else:
                    result = executor.execute(
                        signal, broker, repo, config, mode, confirm_fn=confirm_fn, now_ts=now_ts
                    )
                enter_results.append(result)
                log_event(
                    logger,
                    logging.INFO,
                    "agent.enter_evaluated",
                    product=product_id,
                    rule=signal.rule_name,
                    technique=signal.entry_technique,
                    cts_score=signal.cts_score,
                    placed=result.placed,
                    reason=result.reason,
                )
                if result.placed:
                    order = (
                        repo.get_order(result.order_id) if result.order_id is not None else None
                    )
                    # `position_rule` is now ONLY the exit-rule ownership marker its docstring
                    # always described. The entry context it used to carry (`entry_fill`, `qty`,
                    # `entry_fee`) moved to the `positions` ledger: this key is per-PRODUCT, so a
                    # second entry overwrote the first's and an older tranche's exit booked P&L
                    # against a price it never paid.
                    repo.set_state(
                        f"position_rule:{product_id}",
                        {"rule_name": signal.rule_name, "opened_at": now_ts},
                    )
                    _open_tranche(repo, product_id, signal.rule_name, order, result, now_ts)

        return LoopResult(
            ts=now_ts,
            skipped=False,
            skip_reason=None,
            mode=reported_mode,
            polled=polled,
            products=products,
            stale_products=stale_products,
            enter_signals=enter_signals,
            enter_results=enter_results,
            exit_results=exit_results,
        )
    finally:
        unbind_cycle(cycle_token)


# -- scheduled wrapper -----------------------------------------------------------------------


def loop(
    broker: Any,
    repo: Repository,
    config: Config,
    interval_sec: float,
    stop_flag: Callable[[], bool],
    confirm_fn: executor.ConfirmFn | None = None,
) -> list[LoopResult]:
    """Run `run_once` every `interval_sec` until `stop_flag()` returns `True`.

    `stop_flag` is checked once per cycle, *before* that cycle runs -- a `stop_flag` that's
    already `True` runs zero cycles. Tests drive this deterministically with a call-counting
    closure (e.g. "stop after N cycles") and `interval_sec=0` so the loop doesn't actually
    sleep.
    """
    results: list[LoopResult] = []
    while not stop_flag():
        results.append(
            run_once(broker, repo, config, now_ts=int(time.time()), confirm_fn=confirm_fn)
        )
        if interval_sec:
            time.sleep(interval_sec)
    return results
