"""The scheduled agent loop, confirm mode (P3 Task 8).

`run_once()` is a single cycle: poll fresh candles (`data.market_feed.poll_once`), reconstruct
the `live` rules (`repo.get_rules("live")` -> real `Rule` instances, `RULE_REGISTRY` below),
`strategy.engine.evaluate` them for ENTER `Signal`s, drive EXIT `Signal`s off currently-held
positions, and run every signal through `execution.executor.execute` (confirm|autonomous, from
`config.auto_trade.mode`) -- respecting the kill-switch (`repo.get_state("kill_switch")`)
throughout, and gated before any of that by the venue's market clock for session-bound venues
(FR-9: a closed equities market skips the cycle, it is not a stale feed). The clock answer is
read and recorded FIRST, before either gate can return, so even a halted cycle keeps the
recording fresh for the broker-free surfaces. `loop()` is the
scheduled wrapper: call `run_once` every `interval_sec` until `stop_flag()` returns `True`.

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
`RsiMeanReversion.timeframe`/its several `Decimal` fields). `RULE_REGISTRY` +
`build_rule_from_params` are **the** coercion boundary: a caller (this module, `rules backtest`,
`rules add`) only ever deals in real `Rule` instances, never in raw JSON dicts. `_build_rule` is
the thin DB-row adapter over it. There is deliberately only ONE such table
(`_DECIMAL_PARAMS`): a second copy living next to whichever command needed it would drift from
this one silently, and the symptom of the drift is a `Decimal`/`float` `TypeError` raised deep
inside a rule's arithmetic, at backtest or cycle time, far from the code that caused it.

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
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal

from keel_broker_api.results import MarketSchedule, SessionState
from keel_core.products import quote_currency_of
from keel_core.telemetry import (
    bind_cycle,
    log_event,
    log_exception,
    new_cycle_id,
    unbind_cycle,
)

from keel.config import Config
from keel.data import freshness, market_feed
from keel.data.repository import Repository
from keel.execution import equity as equity_mod
from keel.execution import executor, guards, reconcile, streak
from keel.execution.executor import ExecutionResult, _fetch_available_quote
from keel.execution.guards import FEED_STALENESS_CYCLES
from keel.strategy import engine
from keel.strategy.exit_policy import EXIT_POLICY_OFF, next_stop, policy_for, trailing_atr
from keel.strategy.paper import PaperTrader
from keel.strategy.rules.base import Action, Rule, Setup, Signal
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.pullback_continuation import PullbackContinuation
from keel.strategy.rules.rsi_meanrev import RsiMeanReversion
from keel.strategy.rules.turtle_breakout import TurtleBreakout
from keel.types import Granularity, Side

logger = logging.getLogger(__name__)

#: `keel agent` (single-cycle, non-`--loop`) exits with this status when `run_once` blocked at
#: least one entry on `freshness.entry_bar_ready` (Finding 1, HIGH: a re-evaluated daily bar is
#: a DUPLICATE REAL-MONEY ORDER, not a delayed one -- see `_entry_gate_granularity`/`run_once`
#: below). `keel-live-run.sh` only stamps the UTC day as done on a ZERO exit, so this nonzero
#: exit makes the runner decline to stamp, and one of the remaining 23 hourly LaunchAgent
#: triggers retries an hour later -- converting "duplicate order" into "<= 60 minutes of delay".
#: The `--loop` path never uses this (see `agent_cmd` in `keel/cli.py`): a long-running loop
#: just skips the blocked cycle and tries again next interval: exiting the process would kill a
#: healthy long-running loop over what is usually a transient publication lag.
DATA_NOT_READY_EXIT = 4

#: `keel agent` (single-cycle, non-`--loop`) exits with this status when the cycle skipped on
#: `market_clock_unavailable` -- the venue's clock could not be READ (a transient outage: no
#: network on wake, the clock endpoint erroring), which is a different fact from the venue
#: ANSWERING closed. It mirrors `DATA_NOT_READY_EXIT`'s contract with a day-stamping wrapper:
#: `paper-equities-run.sh` stamps the UTC day as done only on a ZERO exit, and stamping a
#: clock-unavailable skip would record the day as done while nothing was evaluated --
#: silently losing the trading day to an outage the next trigger would have retried. The
#: `market_closed` skip deliberately still exits 0: a closed venue is a fact about the
#: calendar (weekend, holiday), the skip is correct cadence bookkeeping, and nothing more can
#: happen that day. The `--loop` path never uses this either, for `DATA_NOT_READY_EXIT`'s
#: reason: a long-running loop just retries next interval.
MARKET_CLOCK_UNAVAILABLE_EXIT = 5

# -- rule reconstruction: DB row (kind, JSON-plain params) -> a real Rule instance -------------

RULE_REGISTRY: dict[str, type[Rule]] = {
    "pullback_continuation": PullbackContinuation,
    "rsi_meanrev": RsiMeanReversion,
    "dca": Dca,
    "turtle_breakout": TurtleBreakout,
}

# Constructor kwargs that must be coerced back from JSON-plain (str) to `Decimal`, per kind.
_DECIMAL_PARAMS: dict[str, tuple[str, ...]] = {
    "pullback_continuation": ("buffer_ticks", "trail_atr_mult", "be_roll_rr"),
    "rsi_meanrev": (
        "atr_mult",
        "fixed_stop_pct",
        "fixed_rr",
        "level_tolerance",
        "support_proximity_pct",
        "trail_atr_mult",
        "be_roll_rr",
    ),
    "dca": ("budget_usd", "dip_bonus_pct"),
    "turtle_breakout": ("atr_stop_mult", "target_rr"),
}

# The constructor kwarg holding a `Granularity`, stored as its `.value` string, per kind.
# `turtle_breakout`'s entry is what lets the hourly evidence profile (#337) store hourly rows:
# without it, a `granularity` in a turtle row's params would reach the constructor as the
# STRING "ONE_HOUR" and `isinstance(value, Granularity)` lookups (`_entry_gate_granularity`,
# `engine._trading_granularity`) would miss it, silently re-gating the rule on the coarsest
# configured granularity while it kept deciding on daily candles.
_GRANULARITY_PARAMS: dict[str, str] = {
    "pullback_continuation": "granularity",
    "rsi_meanrev": "timeframe",
    "turtle_breakout": "granularity",
}


def coerced_param_keys(kind: str) -> frozenset[str]:
    """The params of `kind` that arrive JSON-plain as STRINGS and are converted on the way in.

    Published so a caller validating operator-typed JSON (`keel rules add`) can tell the two
    cases apart without a second copy of these tables: `"2.5"` is correct for `atr_stop_mult`
    (a `Decimal`) and wrong for `oversold` (a `float`), and only these tables know which is
    which. Anything not listed here reaches its constructor exactly as JSON produced it.
    """
    keys = set(_DECIMAL_PARAMS.get(kind, ()))
    gran_key = _GRANULARITY_PARAMS.get(kind)
    if gran_key is not None:
        keys.add(gran_key)
    return frozenset(keys)


def build_rule_from_params(kind: str, params: dict[str, Any]) -> Rule:
    """Construct a `Rule` of `kind` from JSON-plain `params` (the `rules.params` shape).

    THE single JSON-plain -> constructor-kwarg coercion boundary, shared by every caller that
    has a `(kind, params)` pair and wants a real rule: `_build_rule` (a stored row, for the
    agent cycle and `rules backtest`) and `keel rules add` (an operator's `--params` JSON, for a
    row not yet written). Both directions of that boundary have the identical problem -- JSON
    has no `Decimal`, no enum and no tuple -- so both must use these tables. A command that
    coerced its own would drift from this one the first time a rule gained a `Decimal` field,
    and the drift would surface as a `Decimal`/`float` `TypeError` inside the rule's arithmetic,
    mid-backtest, long after the params were accepted.

    Raises `ValueError` for a `kind` not in `RULE_REGISTRY` -- an unrecognized rule kind in the
    `live` set is a configuration error, not something to silently skip (consistent with this
    codebase's fail-loud posture elsewhere, e.g. `executor.execute`'s unknown-mode check).
    Anything the rule's own constructor rejects (an unknown kwarg -> `TypeError`, an
    out-of-range value -> `ValueError`) propagates unchanged; `rules add` relies on exactly that
    to refuse a bad parameter set BEFORE it writes a row.
    """
    rule_cls = RULE_REGISTRY.get(kind)
    if rule_cls is None:
        raise ValueError(
            f"agent: no rule registered for kind {kind!r} -- known kinds: {sorted(RULE_REGISTRY)!r}"
        )

    kwargs = dict(params)

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


def _build_rule(row: dict[str, Any]) -> Rule:
    """Reconstruct a real `Rule` instance from a `repo.get_rules()` row.

    The DB-row adapter over `build_rule_from_params`: it adds the one thing a row has and a
    bare params dict does not, the `rules.id`. See that function for the coercion rules and the
    exceptions this can raise.
    """
    rule = build_rule_from_params(row["kind"], row.get("params") or {})
    # Thread the real `rules.id` onto the instance (additive `Rule.rule_id`, default `None`) so
    # it can flow onto emitted `Signal`s and, from there, into `orders.rule_id`/`signals.rule_id`
    # for audit -- `.get` rather than `row["id"]` so a hand-built row with no "id" (some tests)
    # still builds a rule, just with `rule_id` left at its `None` default.
    rule.rule_id = row.get("id")
    return rule


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


def _entry_gate_granularity(rule: Rule, granularities: list[Granularity]) -> Granularity | None:
    """Which granularity's freshness should gate `rule`'s ENTRIES (Finding 1, HIGH)?

    A rule that DECLARES its trading timeframe (`self.granularity` on `TurtleBreakout`/
    `PullbackContinuation`, `self.timeframe` on `RsiMeanReversion`) is gated on exactly that --
    the same attribute `strategy.engine._trading_granularity` reads.

    Absent a declaration, this falls back to the COARSEST configured granularity -- the ONE
    place this deliberately diverges from `_trading_granularity`'s own fallback, which picks the
    FINEST. That divergence is not an oversight: `Dca` (`strategy/rules/dca.py`) declares
    neither attribute, yet `Dca.detect` reads `candles_by_tf[Granularity.ONE_DAY]` directly and
    keys its cadence off `latest.ts // 86400 % cadence_days`, so a STALE daily bar re-fires the
    same cadence hit and buys twice. Falling back to the finest configured granularity (as
    `_trading_granularity` does, correctly, for its own job of picking CTS *scoring* data) would
    gate DCA on FIFTEEN_MINUTE and miss that hazard entirely -- the daily bar could be weeks
    stale while FIFTEEN_MINUTE stayed perfectly fresh. The coarsest granularity is the fallback
    that fails in the safe direction for any rule that does not tell us what it actually reads:
    it is the input most likely to be what a rule silent about its timeframe is keying off, per
    this codebase's rules so far.

    Returns `None` only when `granularities` itself is empty -- nothing to gate on at all.
    """
    for attr in ("granularity", "timeframe"):
        value = getattr(rule, attr, None)
        if isinstance(value, Granularity):
            return value
    if not granularities:
        return None
    return max(granularities, key=lambda g: _GRANULARITY_ORDER.get(g, 0))


@dataclass(frozen=True)
class BlockedEntry:
    """One rule's entry, withheld this cycle because its gating bar was not confirmed ready --
    see `freshness.entry_bar_ready` and the gate in `run_once` below. Recorded on `LoopResult`
    so a caller (the CLI, a log line, a test) can say exactly which bar was missing without
    re-deriving it.
    """

    product: str
    rule_name: str
    granularity: Granularity
    expected_ts: int
    stored_ts: int | None
    reason: str


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
    initial_stop: Decimal | None = None,
) -> None:
    """Record the newly opened tranche in the `positions` ledger and point it at its bracket.

    `initial_stop` is the stop this tranche was SIZED against (#520). It is recorded at OPEN and
    never rewritten, because that is the whole point: `open_stop:<product_id>` already tracks the
    current, ratcheting stop, and the break-even threshold needs the ORIGINAL per-unit risk.
    `None` is legitimate and means unknown -- DCA carries no stop by design -- and readers must
    disable the break-even arm for such a tranche rather than substitute the current stop.

    The ledger is what a later exit attributes P&L against, so a tranche whose entry price or qty
    could not be read is NOT recorded: `record_closed_trade` already refuses to guess a missing
    entry price, and a ledger row carrying `None` would either crash the arithmetic or fabricate
    a number nobody observed. The order still stands and `_held_position` still sees it; only the
    attribution is withheld, and loudly.

    `result.bracket_order_id` is `None` for DCA (no stop, so no bracket) and for a bracket the
    rails vetoed. Neither is an error here -- the tranche is real either way, and reconciliation
    simply has no bracket to resolve back to it until one is placed.
    """
    # `order is None` is folded into the one guard below rather than tested separately, so the
    # checker can narrow `order` for the `order["fee"]` read after it. Testing only the two
    # extracted values left `order` typed `dict | None` all the way down, even though a `None`
    # order forces `entry_fill`/`qty` to `None` and returns here.
    if order is None:
        entry_fill = qty = None
    else:
        entry_fill = order["actual_fill"]
        qty = order["qty"]
    if order is None or entry_fill is None or qty is None:
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
        initial_stop=initial_stop,
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
    HWM never poisons live equity (or vice versa).

    `paper_cash_usdc`'s seed amount on first paper run, by `config.paper.starting_equity_usd`:
    - `starting_equity_usd > 0`: seed at THAT amount -- a deliberately-funded paper-forward
      rehearsal. Real broker mark-to-market equity is NOT read for the seed in this case; the
      funded amount is the source of truth, so a funded paper-forward can seed with no broker
      equity read at all.
    - `starting_equity_usd == 0` (the default): seed from real broker mark-to-market equity
      LESS pending purification (#490 -- the seed is sizing equity, so accrued-but-unpurified
      reward income inside the balance read is subtracted via `equity.sizing_equity`); if
      that read fails, log `agent.paper_seed_unavailable` and leave the account unseeded this
      cycle (no bogus 0 denominator).
    """
    if repo.get_state("equity_state_mode") != "paper":
        repo.set_state("equity_high_water_mark", None)
        repo.set_state("drawdown_total_pct", Decimal("0"))
        repo.set_state("drawdown_weekly_pct", Decimal("0"))
        repo.set_state("equity_history", [])
        repo.set_state("equity_state_mode", "paper")
    if paper_trader.get_cash() is None:
        funding = config.paper.starting_equity_usd
        # Declared up front: inferring from the first branch pins `seed` to `Decimal`, and the
        # mark-to-market fallback below can legitimately yield `None` (unreadable broker). The
        # `is None` check in that branch still returns before `seed_cash`, so this only widens
        # the declaration to match what the two branches actually produce.
        seed: Decimal | None
        if funding > 0:
            seed = funding
        else:
            mtm = _mark_to_market_equity(
                repo, broker, products, price_by_product, config.quote_currency
            )
            if mtm is None:
                log_event(logger, logging.WARNING, "agent.paper_seed_unavailable")
                return
            # #490: a balance-derived seed is SIZING equity, so it must exclude
            # accrued-but-unpurified reward income. `_mark_to_market_equity` sums broker
            # balances at face value, and USDC Rewards accrue INSIDE the trading account
            # (runbook item 1 -- they cannot be switched off via the Advanced Trade API), so
            # the raw read includes them; left in, the tainted seed becomes `paper_cash_usdc`
            # -- exactly the equity `_paper_enter` sizes from via `equity_override` -- and
            # riba compounds into position size (discussion #472). The FUNDED branch above
            # is an operator-chosen constant, not a balance read, and is purified by no one.
            pending = equity_mod.pending_purification_usd(repo)
            seed = equity_mod.sizing_equity(mtm, pending)
            # Observable, not silent: this is the one equity figure the system derives from a
            # live balance read, so the subtraction's before/after belongs in the log beside
            # the unavailable-read warning above.
            log_event(
                logger,
                logging.INFO,
                "agent.paper_seed_purified",
                mark_to_market=str(mtm),
                pending_purification=str(pending),
                seed=str(seed),
            )
            if mtm > 0 and seed == Decimal("0"):
                log_event(
                    logger,
                    logging.WARNING,
                    "agent.paper_seed_clamped_to_zero",
                    mark_to_market=str(mtm),
                    pending_purification=str(pending),
                )
        paper_trader.seed_cash(seed, now_ts)


def _clear_live_mode_if_needed(repo: Repository) -> None:
    """Symmetric to `_seed_paper_account_if_needed`'s mode clear, for the live side.

    On a paper->live flip, clear the shared HWM/history/drawdown scalars before this cycle's
    update_drawdown, so a synthetic paper HWM never poisons live equity. Called unconditionally
    at the top of the live branch -- BEFORE the broker-equity read -- so an unreadable broker on
    the first live cycle can't skip the clear and let stale paper scalars survive an extra cycle.
    Gated on the prior mode being "paper" so a fresh/continuing live run leaves real scalars intact.
    """
    if repo.get_state("equity_state_mode") == "paper":
        repo.set_state("equity_high_water_mark", None)
        repo.set_state("drawdown_total_pct", Decimal("0"))
        repo.set_state("drawdown_weekly_pct", Decimal("0"))
        repo.set_state("equity_history", [])
    repo.set_state("equity_state_mode", "live")


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
    paper_equity: Decimal,
) -> executor.ExecutionResult:
    """Run the offline-computable rails, then record a paper fill sized off `paper_equity` if
    they pass.

    Paper runs the rails DELIBERATELY (see `guards.check`'s `offline` docstring): the promotion
    gate is scored on this track record, so a rehearsal that skipped them would promote a
    strategy on evidence of trades live trading would have vetoed.

    `broker=None` is passed to `_build_intent` on purpose: `_fetch_available_quote` returns
    `None` for a null broker without logging, and rail 13 -- the rail that would have consumed
    that balance -- is one of the two `offline=True` skips anyway, because paper has no live
    account to read a balance from.

    `paper_equity` sizes the intent (via `equity_override`) AND the fill: the synthetic account
    equity is what a real paper account would risk `config.risk_pct` of, not the `$5k
    max_exposure` proxy `_build_intent` falls back to absent an override -- that proxy only ever
    existed to gate the guard check, and sizing the fill off it would score the track record on
    trades no real paper balance could have produced.
    """

    def _result(placed, order_id=None, vetoed_by=None, reason=""):
        return ExecutionResult(
            placed=placed,
            order_id=order_id,
            vetoed_by=list(vetoed_by or []),
            preview=None,
            reason=reason,
        )

    intent = executor._build_intent(
        signal, None, repo, config, now_ts, equity_override=paper_equity
    )
    if intent is None:
        return _result(False, reason="paper: nothing to size")

    verdict = guards.check(intent, repo, config, now_ts, offline=True)
    if not verdict.ok:
        return _result(False, vetoed_by=verdict.violations, reason="paper: vetoed by rails")

    order_id = trader.on_signal(signal, qty=intent.qty)
    if order_id is None:
        return _result(
            False, reason="paper: no fill (position open or insufficient synthetic cash)"
        )
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
    # Same `Literal` pair as `_effective_mode`'s return and `executor.execute`'s parameter: this
    # only ever receives the former and only ever forwards to the latter, so a bare `str` here
    # was the one gap that let an unchecked value through the middle of that path.
    mode: Literal["confirm", "autonomous"],
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
        rule_id=owning_rule.rule_id,
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
        # Same reasoning for the unprotected-bracket retry record: the position it described is
        # out, so a surviving record would have the next cycle place a protective SELL against
        # inventory that is no longer held.
        repo.set_state(f"{executor.UNBRACKETED_PREFIX}{product_id}", None)
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


#: Products whose young-table warmup notice has already been logged THIS PROCESS (#502). The
#: notice is per product, not per cycle -- a daily deployment a few bars short of the
#: `4 x atr_period + 1` threshold would otherwise repeat the same line every cycle until
#: warmed. Process scope is the deliberate bound: an `agent_state` key would outlive the
#: condition it describes and suppress the notice forever after a restart.
_WARMUP_LOGGED_PRODUCTS: set[str] = set()


def _manage_stops(
    broker: Any,
    repo: Repository,
    config: Config,
    rules: list[Rule],
    candles_by_tf_by_product: dict[str, dict[Granularity, list[Any]]],
    now_ts: int,
) -> None:
    """The per-cycle live stop-management step (#502 stage 2): ratchet each held tranche's
    resting bracket according to its OWNING rule's exit policy -- the rule row that owns the
    tranche's PRODUCT, never merely a row sharing its family name (the rules table holds one
    row per (kind, product), so a name-only key would govern a multi-product family's every
    tranche under whichever same-family row loaded last: an opted-in BTC row managing a
    knob-less ETH tranche's bracket, or the reverse; `_handle_exits` scopes ownership by
    product for the same reason).

    DEFAULT OFF, exactly like the sim wiring. The knobs are per-rule-family params
    (`trail_atr_mult` / `be_roll_rr` on `pullback_continuation` and `rsi_meanrev`), a rule
    whose params carry neither gets `EXIT_POLICY_OFF` and its position is not touched -- so
    every rule row that has not opted in trades exactly as before this step existed. The
    #442 experiment (docs/experiments/2026-08-22-trailing-vs-static-exits.md) measured
    trailing WORSE and the break-even roll no better than the static exit at the 120 bp fee,
    so the capability ships dark and the operator opts in per rule; `turtle_breakout` offers
    the knobs nowhere at all (its exit is the Donchian channel by choice).

    The decision is DELEGATED, not re-derived: `policy_for` + `next_stop` are the same
    functions the sim/backtest engines apply, so the live level and the simulated level come
    from one place. The bar is the latest COMPLETED one on the rule's trading timeframe
    (`market_feed.poll_once` stores closed candles only), so the no-lookahead contract holds:
    during the bar the venue's bracket rested at the old level, and the new level binds from
    the next bar. A punctual cycle sees exactly one new bar per run; a MISSED cycle reads only
    the newest bar, which is conservative in the one arm where it differs -- a break-even
    trigger on an older bar's high goes unobserved and leaves the stop lower, never looser.

    The ROLL itself is `executor.roll_stop_to`: ONE cancel-and-replace per tranche per cycle
    (#519's cancel-before-place protocol, the crash ledger, the ratchet and at/above-target
    refusals -- all in the executor, none re-invented here). Paper cycles never call this:
    paper entries place no exchange-side brackets, so there is nothing to roll.

    A failed roll is LOUD and ISOLATED, never a dead cycle. The roll's cancel half is a
    live-money action with ordinary failure modes (`CancelPending` / `CancelUnavailable` are
    what Coinbase's batch-cancel answers when a fill lands during the roll), so each
    tranche's roll is wrapped: a raise is logged at CRITICAL -- a possibly-half-completed
    action on live money must be loud -- and the step CONTINUES to the next tranche. Letting
    it propagate would abort `run_once` AFTER entries were placed: no `LoopResult`, no
    post-cycle notify, a nonzero CLI exit, and the live-run wrapper declining to stamp the
    UTC day -- so the next trigger re-runs the whole cycle into the duplicate-entry window.

    The kill-switch window inside a roll (see `executor._roll_stop`): a cancel is not
    rail-gated -- it REMOVES risk, so there is nothing for a guard to veto -- and a switch
    that engages mid-roll, after the cancel, fails only the REPLACEMENT closed (rail 12
    fails every order), leaving the position unbracketed until the switch clears. That is
    the correct failure direction, and it is not silent: the CRITICAL is loud, and
    `reconcile` heals from the crash ledger when cycles resume.
    """
    rules_by_owner = {(getattr(rule, "product_id", None), rule.name): rule for rule in rules}
    for tranche in repo.get_open_positions():
        rule = rules_by_owner.get((tranche["product_id"], tranche["rule_name"]))
        if rule is None:
            continue  # the owning row is not on this cycle's set (demoted/retired): no policy
        policy = policy_for(rule)
        if policy is EXIT_POLICY_OFF:
            continue  # the default: the rule never asked for stop management

        bracket_id = tranche["bracket_order_id"]
        if bracket_id is None:
            continue  # no bracket recorded: the unbracketed sweep owns this position (#195)
        old_order = repo.get_order(bracket_id)
        if old_order is None or old_order["status"] not in executor.RESTING_STATUSES:
            continue  # filled or dead: reconciliation owns that bracket, not this step
        # Per-PRODUCT ratchet state read inside the per-TRANCHE loop: this assumes the
        # ledger's one-tranche-per-asset shape (the same assumption that lets the defers
        # treat a product's slots as exclusive). Keying the ratchet per TRANCHE is a
        # prerequisite for pyramiding, and is deliberately not invented here.
        current_stop = repo.get_state(f"open_stop:{tranche['product_id']}")
        if current_stop is None:
            continue  # no recorded level to ratchet from -- nothing this step may act on

        # `initial_stop` is the tranche's ORIGINAL setup stop (#520) -- the denominator of the
        # break-even threshold. NULL means "nobody recorded it" (pre-ledger tranche, DCA), and
        # the ledger's own contract is that the BE arm switches OFF rather than substitutes the
        # current stop, which is a different (and on a ratcheted position, stale) policy.
        initial_stop = tranche["initial_stop"]
        if initial_stop is None:
            policy = replace(policy, be_roll_rr=None)

        candles_by_tf = candles_by_tf_by_product.get(tranche["product_id"])
        if not candles_by_tf:
            # Stale product this cycle: the freshness pre-pass skipped it, so THIS cycle
            # fetched no bars for it -- which is not "the table holds nothing" (it usually
            # holds older closed bars; staleness is a claim about the newest one). The
            # tranche waits a cycle, and says so at the same INFO volume as the
            # empty-timeframe skip below rather than skipping silently.
            log_event(
                logger,
                logging.INFO,
                "agent.stop_management_skipped",
                product=tranche["product_id"],
                rule=rule.name,
                reason="product skipped this cycle (stale feed) -- no bars fetched",
            )
            continue
        series = candles_by_tf.get(_management_timeframe(rule, candles_by_tf)) or []
        if not series:
            log_event(
                logger,
                logging.INFO,
                "agent.stop_management_skipped",
                product=tranche["product_id"],
                rule=rule.name,
                reason="no candles on the rule's trading timeframe",
            )
            continue

        # Young-table warmup (#442 fidelity). `trailing_atr` prices the trail off a
        # Wilder average that only matches the #442-measured sim behavior once the table
        # holds `4 x atr_period + 1` closed bars, and the live table does not start there:
        # `market_feed.poll_once` cold-starts an EMPTY table with one bar, and backfill has
        # no production caller. Short of the threshold the trail arm is OFF for the cycle.
        # The BE arm is NOT equally affected -- it reads only the latest bar's HIGH against
        # thresholds fixed at entry time, which is exactly what the sim does from bar one --
        # so it stays live. The notice fires once per product, not per cycle.
        warmup_bars = 4 * policy.atr_period + 1
        if policy.trail_atr_mult is not None and len(series) < warmup_bars:
            policy = replace(policy, trail_atr_mult=None)
            if tranche["product_id"] not in _WARMUP_LOGGED_PRODUCTS:
                _WARMUP_LOGGED_PRODUCTS.add(tranche["product_id"])
                log_event(
                    logger,
                    logging.INFO,
                    "agent.stop_management_waiting_for_warmup",
                    product=tranche["product_id"],
                    rule=rule.name,
                    bars=len(series),
                    needed=warmup_bars,
                )

        atr = trailing_atr(series, policy.atr_period)
        level = next_stop(
            policy,
            tranche["entry_fill"],
            # `initial_stop` is only read by the BE arm, which is OFF above when it is NULL;
            # the entry is the neutral stand-in for the unreachable denominator.
            initial_stop if initial_stop is not None else tranche["entry_fill"],
            current_stop,
            series[-1],
            atr,
        )
        if level <= current_stop:
            continue  # the ratchet: nothing proposed toward profit this cycle

        try:
            executor.roll_stop_to(
                broker,
                repo,
                config,
                product_id=tranche["product_id"],
                old_stop_order_id=bracket_id,
                new_stop=level,
                qty=tranche["qty"],
                rule_name=tranche["rule_name"],
                now_ts=now_ts,
            )
        except Exception:
            # Per-tranche isolation, deliberately BROAD: `CancelPending`/`CancelUnavailable`
            # from the fill-during-roll race is the expected raise, but a possibly-
            # half-completed action on live money must not be graded by exception type
            # here. The position may be unprotected RIGHT NOW (the cancel may have landed
            # before the raise), so this is CRITICAL with the traceback attached -- and the
            # cycle SURVIVES, per the docstring: a dead `run_once` after entries were
            # placed re-runs the cycle into the duplicate-entry window.
            log_exception(
                logger,
                "agent.stop_management_roll_failed",
                level=logging.CRITICAL,
                product=tranche["product_id"],
                rule=rule.name,
                old_stop_order_id=bracket_id,
                attempted_stop=level,
            )
            continue


def _management_timeframe(rule: Rule, candles_by_tf: dict[Granularity, list[Any]]) -> Granularity:
    """The series stop management reads: the rule's own trading timeframe when it declares one
    (`granularity` on `TurtleBreakout`/`PullbackContinuation`, `timeframe` on `RsiMeanReversion`
    -- the same attribute order `engine._trading_granularity` and `backtest._rule_trading_tf`
    use), else the finest series this cycle actually has. One deliberate divergence from the
    backtester, bounded: `backtest._rule_trading_tf` falls back to a fixed ONE_HOUR, this falls
    back to whatever the cycle polled finest -- but every knob-carrying family declares its
    timeframe, so a rule that is ever actually managed (`policy_for` non-OFF) never takes a
    fallback series at all, and the divergence is a bound, not a path anyone trades on."""
    for attr in ("granularity", "timeframe"):
        value = getattr(rule, attr, None)
        if isinstance(value, Granularity):
            return value
    return min(candles_by_tf, key=lambda g: _GRANULARITY_ORDER.get(g, 0))


# -- venue session (FR-9: a closed market is not a stale feed) --------------------------------

#: `agent_state` key PREFIXES the cycle writes each run when (and only when) the broker is a
#: session-bound port adapter. The full keys are venue-namespaced via `market_session_key`:
#: `market_session:{venue}` / `market_session_ts:{venue}` / `market_session_interval_sec:
#: {venue}` -- one pair per venue, so two deployments sharing a repo cannot clobber or
#: impersonate each other's clock, and each carries its OWN trust window (the interval that
#: deployment actually cycles at). The broker-free surfaces (`fetch --check`, `status`, the
#: TUI) read this recording instead of making a clock call of their own -- the same shape as
#: the `last_feed_ts` heartbeat: the one component holding a broker records, everyone else
#: reads. A 24/7 venue (or a pre-port broker) writes NOTHING, so every existing crypto
#: deployment keeps byte-identical state and output. A session-bound broker that declares no
#: `venue` in its capabilities records into the un-namespaced legacy keys -- every real
#: `BrokerCapabilities` carries `venue`, so that slot is the anonymous exception, not a
#: second global.
MARKET_SESSION_KEY = "market_session"
MARKET_SESSION_TS_KEY = "market_session_ts"
MARKET_SESSION_INTERVAL_KEY = "market_session_interval_sec"
#: The schedule half of the recording (issue #388 C2): the venue's next open/close, recorded
#: under the same venue-namespaced rule so the console session banner renders RECORDED data
#: and never makes a clock call of its own. `None` is a legal recorded value -- an
#: unreadable clock claims no schedule, and re-recording clears the previous cycle's times.
MARKET_SESSION_NEXT_OPEN_KEY = "market_session_next_open"
MARKET_SESSION_NEXT_CLOSE_KEY = "market_session_next_close"


def market_session_key(venue: str) -> str:
    """The state key for `venue`'s recorded session: `market_session:{venue}`, or the legacy
    un-namespaced `market_session` for the anonymous slot (see the prefix docs above)."""
    return f"{MARKET_SESSION_KEY}:{venue}" if venue else MARKET_SESSION_KEY


def market_session_ts_key(venue: str) -> str:
    """`market_session_key`'s stamp twin, same namespacing rule."""
    return f"{MARKET_SESSION_TS_KEY}:{venue}" if venue else MARKET_SESSION_TS_KEY


def market_session_interval_key(venue: str) -> str:
    """The cycle interval the record was written at, same namespacing rule -- the trust
    window is derived from THIS value (see `recorded_market_closed`), not from whatever
    config the reading surface happens to be holding."""
    return f"{MARKET_SESSION_INTERVAL_KEY}:{venue}" if venue else MARKET_SESSION_INTERVAL_KEY


def market_session_next_open_key(venue: str) -> str:
    """The recorded next-open timestamp, same namespacing rule (issue #388 C2)."""
    return f"{MARKET_SESSION_NEXT_OPEN_KEY}:{venue}" if venue else MARKET_SESSION_NEXT_OPEN_KEY


def market_session_next_close_key(venue: str) -> str:
    """The recorded next-close timestamp, same namespacing rule (issue #388 C2)."""
    return f"{MARKET_SESSION_NEXT_CLOSE_KEY}:{venue}" if venue else MARKET_SESSION_NEXT_CLOSE_KEY


def recorded_session_venues(repo: Repository) -> list[str]:
    """Every venue with a session record in this repo: the anonymous legacy slot first, then
    each `market_session:<venue>` key (discovered by prefix -- the reading surfaces hold no
    broker to ask for a venue id; see `Repository.get_state_keys`)."""
    prefix = f"{MARKET_SESSION_KEY}:"
    venues = [""]
    venues.extend(
        key[len(prefix) :] for key in repo.get_state_keys(prefix) if len(key) > len(prefix)
    )
    return venues


def _effective_interval_sec(config: Config, interval_sec: float | None) -> int:
    """The cycle interval a session record will be trusted for: the one the caller ACTUALLY
    cycles at (`loop`'s / `monitor --loop`'s effective interval, which `--interval` can
    override away from the config's), falling back to the config value when absent or
    degenerate -- a 0-interval loop is a test fiction, and recording it would hand the
    record a zero-length trust window."""
    if interval_sec is not None and interval_sec > 0:
        return int(interval_sec)
    return config.auto_trade.interval_sec


def _venue_session(broker: Any) -> tuple[str, SessionState, bool]:
    """`(venue, state, session_bound)` -- the state half of `_venue_schedule`, kept as its
    own seam because `record_market_session`'s RETURN and the cycle's session gate both
    speak `SessionState`, and will keep doing so."""
    venue, schedule, session_bound = _venue_schedule(broker)
    return venue, schedule.state, session_bound


def _venue_schedule(broker: Any) -> tuple[str, MarketSchedule, bool]:
    """The venue's identity, session-boundness and its clock+schedule answer, fail-closed.

    Returns `(venue, schedule, session_bound)`. `venue` comes from the same
    `capabilities()` read as `session_bound` (empty for a capabilities-less broker, which
    never records anyway). `session_bound=False` is also the answer for a broker that does
    not implement the broker port at all -- paper mode's `broker=None`, or a third-party
    object violating the port: a 24/7 posture with no clock to consult, which keeps every
    existing crypto behavior byte-identical. (Every broker the LIVE path constructs since
    #524 finished the migration is a registry-resolved adapter that answers `capabilities()`;
    the pre-port client this fallback used to carry is gone from the path, but the fallback
    itself stays: paper must never crash on its broker-less cycle.)

    The schedule read prefers the port's `market_schedule()` (issue #388 C2) and falls back
    to a DERIVED schedule -- `market_clock()`'s answer with null next open/close -- for a
    broker built against the pre-#388 port, so the extension breaks no existing adapter.

    **Fail-closed** (FR-9): a session-bound broker whose clock RAISES -- a third-party
    adapter violating the port's "answer `CLOCK_UNAVAILABLE`, never raise" -- is treated as
    `CLOCK_UNAVAILABLE` here too, and so is one whose read returns anything that is not the
    port's answer type (a `None`, a dict, a bool): the port's answer type is part of its
    contract, and `schedule.state.value` downstream must never be an `AttributeError` that
    kills the loop. The cycle must skip, not crash, and must never trade on an unknown
    session state. A `capabilities()` that itself raises reads as not-bound for the same
    reason the portless broker does: nothing about such a broker is known well enough to
    gate on, and its first network call will fail loudly through the ordinary paths.
    """
    caps_fn = getattr(broker, "capabilities", None)
    if caps_fn is None:
        return "", MarketSchedule(state=SessionState.OPEN), False
    try:
        caps = caps_fn()
        session_bound = bool(caps.session_bound)
        venue = str(getattr(caps, "venue", "") or "")
    except Exception:
        return "", MarketSchedule(state=SessionState.OPEN), False
    if not session_bound:
        return venue, MarketSchedule(state=SessionState.OPEN), False
    schedule_fn = getattr(broker, "market_schedule", None)
    if callable(schedule_fn):
        try:
            schedule = schedule_fn()
        except Exception:
            return venue, MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE), True
        if isinstance(schedule, MarketSchedule) and isinstance(schedule.state, SessionState):
            return venue, schedule, True
        return venue, MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE), True
    # A pre-#388 port adapter: derive the schedule from its clock answer, claiming no
    # next open/close (the port default's exact derivation, applied at the caller side).
    try:
        session = broker.market_clock()
    except Exception:
        return venue, MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE), True
    if not isinstance(session, SessionState):
        return venue, MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE), True
    return venue, MarketSchedule(state=session), True


def record_market_session(
    broker: Any,
    repo: Repository,
    config: Config,
    now_ts: int,
    *,
    interval_sec: float | None = None,
) -> tuple[SessionState, bool]:
    """Read the venue's session through `_venue_schedule` and record it -- state, stamp, the
    cycle interval to trust it for, and the schedule's next open/close (issue #388 C2) --
    under the venue's own namespaced keys.

    THE one shared recording step for every loop that holds a broker (the agent cycle here,
    `keel monitor --loop` in `keel/cli.py`), so the broker-free surfaces (`fetch --check`,
    `status`, the TUI, the console session banner) read one recording no matter which loop
    is cycling. A 24/7 venue records nothing. Returns `(state, session_bound)` so the
    caller can gate on the answer it just recorded.

    The schedule keys are ALWAYS written once a record exists, `None` included: a degraded
    clock must CLEAR the previous cycle's next open/close, not leave them rendering as
    current fact -- a stale `next_open` presented as truth is exactly the TUI-side calendar
    the port's schedule read exists to avoid.

    `interval_sec` is the interval THIS caller actually cycles at (`loop`'s effective
    interval, `--interval` override included); absent/degenerate values fall back to the
    config's (see `_effective_interval_sec`) -- the trust window must describe the
    deployment's real cadence, or a record written every 2h under a 15-minute config window
    false-positives the weekend between cycles.
    """
    venue, schedule, session_bound = _venue_schedule(broker)
    if session_bound:
        repo.set_state(market_session_key(venue), schedule.state.value)
        repo.set_state(market_session_ts_key(venue), now_ts)
        repo.set_state(
            market_session_interval_key(venue), _effective_interval_sec(config, interval_sec)
        )
        repo.set_state(market_session_next_open_key(venue), schedule.next_open_ts)
        repo.set_state(market_session_next_close_key(venue), schedule.next_close_ts)
    return schedule.state, session_bound


def recorded_market_closed(
    repo: Repository, config: Config, now_ts: int, venue: str | None = None
) -> bool:
    """Read the cycle's recorded venue clock and say whether staleness is defused (FR-9).

    Pure repo/config reads -- this is the seam that lets `fetch --check` stay offline while
    honoring the venue's own session answer: the agent cycle recorded it, this reads it.

    `venue` scopes the read. A NAMED venue answers only from its own key pair, so one
    venue's CLOSED never defuses another venue's staleness -- the shared-repo hazard this
    signature exists to close. `None` (the CLI surfaces' default) answers for this
    deployment's data as a whole: a deployment has one venue, and its broker-free surfaces
    hold no broker to ask which, so every recorded slot is consulted (see
    `recorded_session_venues`).

    **Pre-close staleness attenuation, stated plainly.** Staleness that BEGAN during
    trading -- a feed break in the hour before the close -- is silenced the moment a closed
    record exists, not just staleness that starts after it: `closed` defuses the alert
    without asking when the staleness started. That is a deliberate trade-off: the
    alternative (letting pre-close breaks keep alerting through the weekend) re-introduces
    exactly the weekend pages FR-9 exists to stop, and the silence is BOUNDED -- when the
    market reopens, the record either refreshes to `open` or expires out of the trust
    window below, and the still-pending staleness re-alerts. Closures postpone the alert;
    they never cancel it.

    Three honest limits, all deliberate:

    * Only a recorded `closed` defuses anything. `clock_unavailable` is fail-closed for
      TRADING but fail-LOUD for ALERTING -- suppressing a staleness alert on an unknown
      clock would hide exactly the outage the unreadable clock hints at.
    * The recording is trusted only for the feed-heartbeat window (`FEED_STALENESS_CYCLES`
      cycles, rail 12's own constant) of the RECORDED interval -- the cadence the writing
      deployment actually loops at, which `keel agent --loop --interval N` can set away
      from the config's. A recording older than that means the agent that wrote it has
      stopped cycling, and its last reading must not silence staleness alerts forever:
      alerts resume, which is also how the dead agent surfaces. A healthy deployment
      re-records every cycle, so on a real weekend the recording is always fresh. Records
      that predate the recorded interval fall back to `config.auto_trade.interval_sec`.
    * A stamp that is not an int never defuses -- no alert is silenced off a value nobody
      vouches for.
    """
    slots = [venue] if venue is not None else recorded_session_venues(repo)
    return any(_slot_market_closed(repo, config, now_ts, slot) for slot in slots)


@dataclass(frozen=True)
class RecordedSession:
    """One venue's recorded session, as a display surface reads it (issue #388 C2): the
    state, its stamp, the schedule's next open/close, and the honesty flag the banner
    renders -- `fresh` (the record is still inside its trust window, `recorded_market_
    closed`'s own window).

    Raw, not interpreted: a stale record is returned as-is with `fresh=False`, and the
    CALLER decides what that means (the session banner renders CLOCK UNAVAILABLE off it;
    `status` keeps rendering the recorded state). No field here is ever derived from a
    clock call -- everything came from the recording.

    There is deliberately no `defused` here (closed AND fresh): defusal is FR-9's
    staleness-ALERT concern, owned by `recorded_market_closed` and surfaced through
    `MarketSessionStatus.defused` -- a display record that also carried it invited a
    banner rendering decision the banner has no business making.
    """

    venue: str
    state: str
    recorded_ts: int | None
    interval_sec: int | None
    next_open_ts: int | None
    next_close_ts: int | None
    fresh: bool


def latest_recorded_session(
    repo: Repository, config: Config, now_ts: int
) -> RecordedSession | None:
    """The most recently stamped venue's session record, or `None` when nothing is recorded
    -- the broker-free read the console session banner (O9) composes from. Pure
    repo/config reads, the same seam `recorded_market_closed` already is: the agent cycle
    recorded it, this reads it, and no reading surface ever makes a clock call.

    The newest-stamp tiebreak is the same one `keel/commands/status.py`'s own session read
    uses, so the two surfaces can never disagree about which venue is on screen. The
    freshness window is `recorded_market_closed`'s (the RECORDED interval x
    `FEED_STALENESS_CYCLES`), so a record the staleness rails would no longer trust is
    reported `fresh=False` here too -- one window, everywhere.
    """
    best: tuple[int, str] | None = None
    for venue in recorded_session_venues(repo):
        if repo.get_state(market_session_key(venue)) is None:
            continue
        recorded_ts = repo.get_state(market_session_ts_key(venue))
        stamped = (
            recorded_ts
            if isinstance(recorded_ts, int) and not isinstance(recorded_ts, bool)
            else -1
        )
        if best is None or stamped > best[0]:
            best = (stamped, venue)
    if best is None:
        return None
    venue = best[1]
    state = str(repo.get_state(market_session_key(venue)))
    recorded_ts = repo.get_state(market_session_ts_key(venue))
    interval = repo.get_state(market_session_interval_key(venue))
    interval_sec = (
        interval
        if isinstance(interval, int) and not isinstance(interval, bool) and interval > 0
        else None
    )
    fresh = (
        isinstance(recorded_ts, int)
        and not isinstance(recorded_ts, bool)
        and now_ts - recorded_ts
        <= (interval_sec or config.auto_trade.interval_sec) * FEED_STALENESS_CYCLES
    )
    return RecordedSession(
        venue=venue,
        state=state,
        recorded_ts=recorded_ts if isinstance(recorded_ts, int) else None,
        interval_sec=interval_sec,
        next_open_ts=repo.get_state(market_session_next_open_key(venue)),
        next_close_ts=repo.get_state(market_session_next_close_key(venue)),
        fresh=fresh,
    )


def _slot_market_closed(repo: Repository, config: Config, now_ts: int, venue: str) -> bool:
    """`recorded_market_closed` for one venue slot -- see that function for the policy."""
    if repo.get_state(market_session_key(venue)) != SessionState.CLOSED.value:
        return False
    recorded_ts = repo.get_state(market_session_ts_key(venue))
    if not isinstance(recorded_ts, int) or isinstance(recorded_ts, bool):
        return False  # unreadable stamp: do not defuse alerts off a value nobody vouches for
    recorded_interval = repo.get_state(market_session_interval_key(venue))
    interval_sec = (
        recorded_interval
        if isinstance(recorded_interval, int)
        and not isinstance(recorded_interval, bool)
        and recorded_interval > 0
        else config.auto_trade.interval_sec
    )
    return now_ts - recorded_ts <= interval_sec * FEED_STALENESS_CYCLES


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
    # Finding 1 (HIGH): rules whose entry this cycle withheld because their gating bar wasn't
    # confirmed ready (`freshness.entry_bar_ready`, applied in `run_once` below) -- see
    # `BlockedEntry`. Defaulted so every existing `LoopResult(...)` construction stays valid.
    blocked_entries: list[BlockedEntry] = field(default_factory=list)
    # Paper-forward observability (P4 Task 9): the synthetic account's equity + Rail 11's
    # drawdown scalars for THIS cycle. `None` in every non-paper cycle -- there is no synthetic
    # account to report on -- so all existing `LoopResult(...)` constructions stay valid.
    paper_equity: Decimal | None = None
    drawdown_total_pct: Decimal | None = None
    drawdown_weekly_pct: Decimal | None = None


def _effective_mode(
    config: Config, repo: Repository, now_ts: int
) -> Literal["confirm", "autonomous"]:
    """The executor mode for this cycle: `"autonomous"` or `"confirm"`.

    The return type is the same `Literal` pair `executor.execute` accepts, not a bare `str`:
    those two values are all this can return (see the fail-toward-`"confirm"` note below), and
    the wider annotation meant the two `execute(...)` call sites in this module were passing an
    unchecked `str` into a `Literal` parameter. `config.auto_trade.mode` stays a `str` -- it has
    a third value, `"paper"`, which never reaches an executor mode at all.

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
    interval_sec: float | None = None,
) -> LoopResult:
    """One agent cycle: (kill-switch / market-session gates) -> poll -> evaluate -> exits
    -> entries.

    The venue session is read and RECORDED first, before any gate can return (FR-9) -- a
    session-bound venue's clock answer under its own namespaced keys, with the interval
    this deployment actually cycles at (`interval_sec`, which `loop` threads in; the config
    value when absent). Recording on every path is load-bearing: the kill-switch skip and
    the session skip both return BEFORE anything else, and a recording that stopped at
    either return would leave a halted-but-healthy deployment unable to track the venue
    clock -- a weekend under a kill switch would false-positive STALE, and a Friday-pre-close
    halt would freeze `market_session="open"` (which `status` would render as "open (venue
    clock)") all weekend. The clock read is not a poll, an evaluation or an order; the halt
    still owns the skip reason.

    The kill-switch is checked next (no poll, no evaluation, no orders) --
    `repo.get_state("kill_switch", default=True)` fails closed exactly like `guards.check`'s
    rail 12, so an agent that has never been explicitly `resume`d never trades. The venue
    session gate (FR-9) sits directly after it and skips the same way: a session-bound venue
    whose clock says closed (or cannot be read -- fail-closed) is a closed market, not a
    stale feed, so the cycle does nothing rather than poll a shut venue and log
    staleness-gated noise. The session answer is recorded first either way, so the
    broker-free surfaces (`fetch --check`, `status`) can render it without a clock call.
    Per-product staleness (`market_feed.is_fresh`) is checked after polling and only skips
    *that* product; the cycle itself still runs (and `last_feed_ts` still updates) for the rest.
    """
    cycle_token = bind_cycle(new_cycle_id())
    try:
        log_event(logger, logging.INFO, "agent.cycle_start", now_ts=now_ts)

        # FR-9's session read + recording, BEFORE any gate can return -- see the docstring.
        # `market_feed.poll_once` is never reached for a closed venue, so this clock call is
        # the one network touch a skipped cycle makes, and only for session-bound venues.
        session, session_bound = record_market_session(
            broker, repo, config, now_ts, interval_sec=interval_sec
        )

        if repo.get_state("kill_switch", default=True):
            log_event(logger, logging.INFO, "agent.cycle_skipped", reason="kill_switch")
            return LoopResult(
                ts=now_ts, skipped=True, skip_reason="kill_switch", mode=None, polled=0
            )

        # FR-9's session gate, mirroring the kill-switch skip exactly (no poll, no
        # evaluation, no orders). Two distinct reasons, because "the venue says shut" and
        # "the clock could not be read" are different operator facts: a weekend is expected
        # and logged at INFO, an unreadable clock is a degraded read worth a WARNING.
        if session_bound and session is not SessionState.OPEN:
            reason = (
                "market_closed" if session is SessionState.CLOSED else "market_clock_unavailable"
            )
            log_event(
                logger,
                logging.INFO if session is SessionState.CLOSED else logging.WARNING,
                "agent.cycle_skipped",
                reason=reason,
            )
            return LoopResult(ts=now_ts, skipped=True, skip_reason=reason, mode=None, polled=0)

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
        # Fees come from the deployment's own `fees.taker_pct` (paper fills are market-style,
        # same as the backtester's), not from `paper.py`'s library default -- a paper-forward
        # exists to estimate what live trading would have cost, so it has to price fills at the
        # rate THIS account would actually pay.
        paper_trader = (
            PaperTrader(repo, fee_pct=config.fees.taker_pct)
            if config.auto_trade.mode == "paper"
            else None
        )
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
        blocked_entries: list[BlockedEntry] = []
        # Built once per product in the pre-pass below and reused by the main pass, so a
        # product's series is read from the DB exactly once per cycle rather than twice.
        candles_by_tf_by_product: dict[str, dict[Granularity, list[Any]]] = {}

        # Reconcile FIRST, before equity and before any entry. A bracket that filled since the
        # last cycle has already changed the position and the cash balance; reading equity or
        # sizing a new entry against stale `pending` state would mark sold inventory as still
        # held and compute rail 11's drawdown off a position that no longer exists.
        reconcile.reconcile_open_orders(broker, repo, config, now_ts)

        # Then protect anything still holding without a bracket. AFTER the pass above, which may
        # itself have healed a dead bracket or closed a position out -- running it first would
        # re-place brackets for tranches that were about to be resolved anyway. This is the only
        # thing that ever revisits a bracket that was never placed at all: it owns no `pending`
        # row, so the pass above cannot see it (issue #195).
        reconcile.reconcile_unbracketed_positions(broker, repo, config, now_ts)

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
            # Recurring deposit (P4 Task 7), applied once per UTC calendar month -- AFTER the
            # seed (a first-ever cycle both seeds and contributes) and BEFORE this cycle's
            # equity/`update_drawdown`, so the deposit lands in the equity this cycle computes
            # rather than reading as next cycle's unexplained jump. `record_external_flow`
            # rebases the HWM + weekly history so the deposit is never read as a recovery.
            contribution = config.paper.monthly_contribution_usd
            if contribution > 0 and paper_trader.get_cash() is not None:
                month_start, _ = guards._utc_month_bounds(now_ts)
                if repo.get_state("paper_last_contribution_month") != month_start:
                    paper_trader.deposit(contribution)
                    equity_mod.record_external_flow(repo, amount=contribution)
                    repo.set_state("paper_last_contribution_month", month_start)
            equity_now = paper_trader.equity(latest_price_by_product)
        else:
            # Mirrors `_seed_paper_account_if_needed`'s clear: unconditional, at the top of the
            # branch, BEFORE the broker-equity read, so an unreadable broker on the first live
            # cycle after a paper->live flip can't skip the clear (see
            # `_clear_live_mode_if_needed`'s docstring).
            _clear_live_mode_if_needed(repo)
            equity_now = _mark_to_market_equity(
                repo, broker, products, latest_price_by_product, config.quote_currency
            )
        # Task 9: paper-forward observability -- the synthetic equity + drawdown scalars this
        # cycle advanced, surfaced on `LoopResult` (rendered by
        # `keel.commands.trading.render_loop_result` + this log line) instead
        # of only living in repo state. `None` unless this is a paper cycle that read equity.
        result_paper_equity: Decimal | None = None
        result_drawdown_total_pct: Decimal | None = None
        result_drawdown_weekly_pct: Decimal | None = None
        if equity_now is None:
            # Leave the previous cycle's scalars in place -- see `_mark_to_market_equity`.
            log_event(
                logger,
                logging.INFO if paper_trader is not None else logging.WARNING,
                "agent.equity_unavailable",
                paper=paper_trader is not None,
            )
        else:
            # The live-side mode clear now happens up-front in the live branch above (see
            # `_clear_live_mode_if_needed`), symmetrically with the paper-side clear in
            # `_seed_paper_account_if_needed`. Nothing left to stamp/clear here.
            equity_mod.update_drawdown(repo, equity=equity_now, now_ts=now_ts)

            if paper_trader is not None:
                result_paper_equity = equity_now
                result_drawdown_total_pct = repo.get_state("drawdown_total_pct")
                result_drawdown_weekly_pct = repo.get_state("drawdown_weekly_pct")
                log_event(
                    logger,
                    logging.INFO,
                    "agent.paper_equity",
                    equity=str(equity_now),
                    dd_total=str(result_drawdown_total_pct),
                    dd_weekly=str(result_drawdown_weekly_pct),
                )

        # `_paper_enter` sizes the fill off THIS cycle's synthetic equity -- reusing `equity_now`
        # computed above rather than re-deriving it, so the entry and the drawdown scalars it just
        # advanced always agree on what the account was worth this cycle. `None` when unseeded or
        # unreadable (handled just above): sizing a fill off an unknown equity would be worse than
        # not trading, so paper entries are skipped this cycle instead, below.
        paper_equity = equity_now if paper_trader is not None else None

        # == PRE-PASS: decide entry admission for the WHOLE CYCLE, before any order goes out ====
        #
        # Finding 1 (HIGH) was closed at the wrong GRANULARITY: `entry_bar_ready` was correct,
        # but it used to gate `ready_rules` PER PRODUCT, inside the very loop that also called
        # `executor.execute` for that product's signals. A cycle could place a real order for
        # product A and only THEN discover, a few iterations later, that product B's confirming
        # bar hadn't arrived -- `blocked_entries` ended up correctly non-empty, but nothing had
        # consulted it before A's order was already live.
        #
        # WHY this has to be whole-cycle, not per-product: `keel-live-run.sh` reads this cycle's
        # exit status as a SINGLE bit and decides off that alone whether to stamp the UTC day --
        # it has no way to ask "which orders, if any, did that cycle place". A's order placed
        # plus a nonzero exit (because B was blocked) is the worst of both worlds: an order is
        # live AND the day is left unstamped, so the next hourly trigger retries the WHOLE cycle
        # against the SAME daily bar and re-enters everything that already traded, A included.
        # Entry admission therefore has to be ATOMIC with the exit status that gates the
        # day-stamp -- decided once, for every product, before `executor.execute`/`_paper_enter`
        # runs for any of them, not resolved product-by-product as each reaches the front of the
        # loop.
        #
        # THE COST, stated plainly: one lagging product now delays EVERY entry this cycle,
        # including ones whose own data was perfectly fresh. That trade is deliberate -- a
        # withheld entry is recovered by the very next hourly trigger, at most ~60 minutes
        # later; a duplicate live entry is not recoverable at all. Paying a bounded, known delay
        # to avoid an unbounded, unrecoverable duplication is the trade this whole module exists
        # to make.
        #
        # This is also what makes the runner's retry IDEMPOTENT FOR ENTRIES, which is the
        # property that gives "<= 60 minutes of delay" its meaning: a blocked cycle places NO
        # entries and (via `keel-live-run.sh`'s exit-4 contract) leaves the day unstamped, so the
        # retry an hour later starts from "no entry happened yet", not "an entry happened and
        # needs to happen again". Without that idempotence, "delay" is just a duplicate entry
        # that hasn't landed yet.
        #
        # A blocked cycle is NOT inert, though: EXITS are deliberately exempt from all of this --
        # see the main pass below -- and still run for every non-stale product regardless of
        # `entries_allowed`, so THIS SAME cycle can place a real SELL even while every entry was
        # withheld. That exemption is correct on its own terms (the protective stop rests at the
        # broker, but the rule-driven channel exit runs IN-PROCESS, and trapping a losing
        # position an extra cycle is strictly worse than a delayed entry) -- the point here is
        # only that "entries withheld" must never be misread as "this cycle placed nothing".
        for product_id in products:
            if finest is not None and not market_feed.is_fresh(
                repo, product_id, finest, now_ts, max_age_sec
            ):
                # Stale-product skip stays FIRST and still `continue`s past the product
                # entirely, exactly as before this pre-pass existed -- load-bearing ordering,
                # independently validated: a dead venue or a delisted product reads as stale
                # here and is skipped WITHOUT ever reaching the readiness gate below, so it can
                # never block the whole cycle's entries just by being permanently absent from
                # the feed. Only a product that IS being polled, and is merely a bar or two
                # behind, can withhold the cycle.
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
            candles_by_tf_by_product[product_id] = candles_by_tf

            for rule in product_rules:
                gate_gran = _entry_gate_granularity(rule, granularities)
                if gate_gran is None:
                    continue
                readiness = freshness.entry_bar_ready(candles_by_tf, gate_gran, now_ts)
                if readiness.ready:
                    continue
                log_event(
                    logger,
                    logging.WARNING,
                    "agent.entry_bar_not_ready",
                    product=product_id,
                    rule=rule.name,
                    granularity=gate_gran.value,
                    expected_ts=readiness.expected_ts,
                    stored_ts=readiness.stored_ts,
                    bars_behind=readiness.bars_behind,
                    reason=readiness.reason,
                    blocked_by=None if readiness.blocked_by is None else readiness.blocked_by.value,
                    blocked_by_ts=readiness.blocked_by_ts,
                )
                blocked_entries.append(
                    BlockedEntry(
                        product=product_id,
                        rule_name=rule.name,
                        granularity=gate_gran,
                        expected_ts=readiness.expected_ts,
                        stored_ts=readiness.stored_ts,
                        reason=readiness.reason or "unknown",
                    )
                )

        # The whole-cycle admission bit: ANY blocked rule, on ANY product, withholds EVERY entry
        # this cycle -- see the pre-pass comment above for why this cannot be decided per-rule
        # or per-product. `blocked_entries` is still recorded per-rule (for the operator log
        # line and `LoopResult`), but what it GATES is binary.
        entries_allowed = not blocked_entries
        if not entries_allowed:
            log_event(
                logger,
                logging.WARNING,
                "agent.entries_withheld",
                blocked_count=len(blocked_entries),
                products=sorted({b.product for b in blocked_entries}),
                rules=sorted({b.rule_name for b in blocked_entries}),
            )

        # == MAIN PASS: exits ALWAYS run; entries run ONLY when `entries_allowed` ================
        for product_id in products:
            if product_id in stale_products:
                continue  # already recorded + logged in the pre-pass above -- not logged twice.

            candles_by_tf = candles_by_tf_by_product[product_id]
            product_rules = [r for r in rules if getattr(r, "product_id", None) == product_id]

            if paper_trader is not None:
                _paper_resolve_bars(paper_trader, product_id, candles_by_tf, granularities)

            # EXITS are exempt from `entries_allowed` and always run, for every non-stale
            # product, regardless of which (if any) product is withholding this cycle's
            # entries. An open position's rule-driven channel exit runs IN-PROCESS (the
            # protective stop rests at the broker, but the channel exit does not), so it must
            # never be held hostage by a DIFFERENT product's lagging feed -- staying in a losing
            # position an extra cycle is strictly worse than a delayed entry, and nothing about
            # the duplicate-order hazard this gate exists for applies to a SELL that closes an
            # existing position.
            product_exit_results = _handle_exits(
                product_id,
                product_rules,
                candles_by_tf,
                repo,
                broker,
                config,
                mode,
                now_ts,
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

            if not entries_allowed:
                # Whole-cycle withholding (see the pre-pass comment above): evaluate NOTHING for
                # entries on this product, even though its own rules may individually have been
                # ready -- `engine.evaluate` is not even called, so no signal is scored,
                # persisted, or logged, and `enter_signals` stays empty for the whole cycle.
                continue

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
                    if paper_equity is None:
                        log_event(
                            logger,
                            logging.INFO,
                            "agent.paper_enter_skipped_no_equity",
                            product=product_id,
                            rule=signal.rule_name,
                        )
                        result = ExecutionResult(
                            placed=False,
                            order_id=None,
                            vetoed_by=[],
                            preview=None,
                            reason="paper: skipped (synthetic account equity unavailable)",
                        )
                    else:
                        result = _paper_enter(
                            paper_trader, signal, repo, config, now_ts, paper_equity
                        )
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
                    order = repo.get_order(result.order_id) if result.order_id is not None else None
                    # `position_rule` is now ONLY the exit-rule ownership marker its docstring
                    # always described. The entry context it used to carry (`entry_fill`, `qty`,
                    # `entry_fee`) moved to the `positions` ledger: this key is per-PRODUCT, so a
                    # second entry overwrote the first's and an older tranche's exit booked P&L
                    # against a price it never paid.
                    repo.set_state(
                        f"position_rule:{product_id}",
                        {"rule_name": signal.rule_name, "opened_at": now_ts},
                    )
                    _open_tranche(
                        repo,
                        product_id,
                        signal.rule_name,
                        order,
                        result,
                        now_ts,
                        # #520: the stop the position was SIZED against, captured at open
                        # because nothing else preserves it -- `open_stop:<product>` starts
                        # here and then ratchets away from it. `None` for DCA, which has no
                        # stop by design.
                        initial_stop=signal.setup.stop if signal.setup is not None else None,
                    )

        # == STOP MANAGEMENT: ratchet held positions' brackets per the owning rule's policy ==
        #
        # AFTER the trading decisions (a rule exit this cycle already closed its tranches, so
        # they are no longer `open` and cannot be managed; a fresh entry's bracket rests at its
        # own setup stop and may trail from this same completed bar, mirroring the sim's
        # bar-end sequencing) and BEFORE cycle end. LIVE cycles only: paper entries place no
        # exchange-side brackets, so there is nothing to roll and the paper path must not touch
        # the real order book. Default-off per rule -- see `_manage_stops`.
        if paper_trader is None:
            _manage_stops(broker, repo, config, rules, candles_by_tf_by_product, now_ts)

        cycle_result = LoopResult(
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
            blocked_entries=blocked_entries,
            paper_equity=result_paper_equity,
            drawdown_total_pct=result_drawdown_total_pct,
            drawdown_weekly_pct=result_drawdown_weekly_pct,
        )

        # Post-cycle notifications (#444): derive the opted-in events from the same seams
        # doctor reads and fire-and-forget them. AFTER every trading work, default-off at the
        # config, and `notify_after_cycle` swallows everything -- a notification failure must
        # never cost a cycle. The import is lazy for the same reason `gather_findings`'s is:
        # `keel.notifications` bridges to `keel.commands.doctor`, and keeping it out of this
        # module's import graph keeps the loop importable without the diagnostic layer.
        from keel.notifications import notify_after_cycle

        notify_after_cycle(repo, config, cycle_result, now_ts)

        return cycle_result
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

    `interval_sec` is also threaded into each cycle's session record (see
    `run_once`/`record_market_session`): THIS loop's cadence is the one the record's trust
    window must describe, `--interval` overrides included -- a record trusted for the
    config's 15 minutes would expire between 2-hour cycles and false-positive the weekend.
    """
    results: list[LoopResult] = []
    while not stop_flag():
        results.append(
            run_once(
                broker,
                repo,
                config,
                now_ts=int(time.time()),
                confirm_fn=confirm_fn,
                interval_sec=interval_sec,
            )
        )
        if interval_sec:
            time.sleep(interval_sec)
    return results
