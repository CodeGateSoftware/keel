"""Optuna parameter study, research-side only (#476): candidates for the gauntlet, never
auto-tuned lives.

`significance.py` "measures, never gates". This module goes one step further and it gates
exactly ONE thing: the PROPOSAL of a candidate. Nothing here auto-tunes a live or paper
profile, nothing writes a rule row, nothing changes the promotion gauntlet — a winner under
this harness is a hypothesis that would still have to clear the unchanged gauntlet (and the
overfitting gate below) before it could even be proposed. The binding constraint is the fee,
not the parameters: the sibling significance study (#475) found every family
indistinguishable from zero at the 120 bp taker fee, so optimization here is for
UNDERSTANDING, not rescue, and the harness's most important output is the refusal line —
"no candidate may be proposed" with the numbers — which it must always be able to say.

Shape of the honesty:

* **Train/held-out split before anything else.** The optimizer sees only the chronological
  train window (`split_chronologically`, 70% by default); the winner is re-priced once on
  the held-out tail, and a non-positive held-out expectancy refuses the proposal.
* **PBO/CSCV over the study's own trials.** The per-trial per-trade P&L columns are fed to
  `keel.research.cscv.pbo`; `passed` requires `pbo <= 0.5` — the study must not be its own
  best witness (Strathern rail respected throughout: CSCV returns probabilities, never the
  winning configuration, and `run_study`'s `best_params` is the OPTIMIZER's answer, reported
  under the gate, never the gate's input).
* **Deterministic under a fixed seed.** TPE with `TPESampler(seed=...)` over deterministic
  Decimal backtests reproduces bit-for-bit; the acceptance test pins it. A study that
  cannot reproduce itself is not evidence.
* **optuna is an optional research extra.** It rides the repo's dev dependency group, is
  imported lazily inside `run_study` ONLY, and `import keel.research.tuning` succeeds
  without it — shipped wheels stay clean, and so does every runtime path.

Since #528 this module is also where the trials budget becomes DERIVED rather than
remembered: `SEARCH_SPACES` reads the rules' own `param_space()` declarations (one source
of truth — the hand-maintained table is gone), `declared_cells` counts the grid a full
sweep would visit, and `explored_vs_declared` refuses a sweep whose explored range exceeds
the declaration. None of that searches anything; it prices the searching.

`Decimal` for every price-derived quantity (they are money); `float` appears only where
optuna's API demands it — inside the objective's return value — exactly as `deflate.py`
documents its probabilities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import ModuleType
from typing import Protocol

from keel.agent import RULE_REGISTRY, build_rule_from_params
from keel.research.cscv import pbo as cscv_pbo
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT, backtest
from keel.strategy.rules.base import ParamSpec
from keel.strategy.stats import BacktestResult
from keel.types import Candle

__all__ = [
    "MIN_TRADES_PER_COLUMN",
    "OVERFITTING_CEILING",
    "PBO_BLOCKS",
    "SEARCH_SPACES",
    "TRAIN_FRAC",
    "ExplorationCheck",
    "OverfittingGate",
    "StudyReport",
    "TrialSummary",
    "declared_cells",
    "evaluate_gate",
    "evaluate_params",
    "explored_vs_declared",
    "params_from_trial",
    "proposal_verdict",
    "run_study",
    "split_chronologically",
]

#: Fraction of the (ts-ordered) history the optimizer may see; the rest is held out and
#: the winner is re-priced on it exactly once. Chronological, never shuffled: the held-out
#: window is the FUTURE of the train window, which is the only direction a deployment
#: would ever live in.
TRAIN_FRAC = Decimal("0.7")

#: CSCV block count for the overfitting gate. The default `cscv.pbo` s=16 needs long
#: per-trial columns; parameter-study trials are whole backtests, so s=8 (28 combinations)
#: keeps the gate affordable while staying far off degeneracy.
PBO_BLOCKS = 8

#: A trial whose backtest closed fewer than this many trades carries no information about
#: overfitting and is EXCLUDED from the CSCV matrix rather than padding it with noise.
MIN_TRADES_PER_COLUMN = 10

#: `passed` requires PBO at or below one half: the IS-best configuration must be at least
#: as likely as not to land in the better OOS half. Above it, the study's winner is more
#: plausibly luck than edge, and the harness refuses to propose it.
OVERFITTING_CEILING = Decimal("0.5")


def _declared_space(kind: str) -> tuple[ParamSpec, ...]:
    """The rule's own declaration for `kind`, off a defaults-constructed instance.

    `build_rule_from_params` raises `ValueError` for an unknown kind, which is the same
    loud refusal `params_from_trial` owes -- a study over a kind with no rule is a study
    over nothing. A kind that declares nothing (dca) yields the empty tuple, and its
    family is simply absent from `SEARCH_SPACES`: an empty space is a truthful statement,
    not a missing one.
    """
    return build_rule_from_params(kind, {"product_id": "BTC-USD"}).param_space()


#: Pinned per-family search spaces, small enough to defend in review: a handful of knobs
#: a trader can name a reason for, not a grid-search carpet. Bounds are `(lo, hi)`; INTEGRAL
#: bounds are suggested as ints, fractional ones as floats (the dispatch
#: `params_from_trial` performs). `granularity`/`product_id` are deliberately absent: the
#: clock and the product are fixed by the caller, never searched.
#:
#: DERIVED, not hand-maintained (issue #528): every bound below is READ off the rule's own
#: `Rule.param_space()` declaration at import, so this dict cannot disagree with the rule --
#: a bound moves where the rule declares it and nowhere else. The literal this used to be
#: is gone on purpose; `tests/research/test_tuning.py` pins the equivalence.
SEARCH_SPACES: dict[str, dict[str, tuple]] = {
    kind: space
    for kind in RULE_REGISTRY
    if (space := {spec.name: spec.bounds for spec in _declared_space(kind)})
}


class Suggest(Protocol):
    """The slice of optuna's `Trial` the harness reads: two suggest methods.

    A Protocol rather than `optuna.trial.Trial` so the core stays importable (and testable)
    without optuna installed — any object with these two methods duck-types in, and the
    tests drive it with a table-backed fake.
    """

    def suggest_int(self, name: str, low: int, high: int) -> int: ...

    def suggest_float(self, name: str, low: float, high: float) -> float: ...


def _suggest_value(suggest: Suggest, name: str, bounds: tuple) -> int | float:
    """One suggestion, dispatching on the PINNED bounds' types, never the value's.

    Integral bounds -> `suggest_int` (discrete lookbacks and periods), fractional bounds ->
    `suggest_float`. The bounds in `SEARCH_SPACES` therefore declare their own type, and a
    reader of the space knows exactly what the sampler was allowed to draw.
    """
    low, high = bounds
    if isinstance(low, int) and isinstance(high, int):
        return suggest.suggest_int(name, low, high)
    return suggest.suggest_float(name, float(low), float(high))


def params_from_trial(family: str, suggest: Suggest) -> dict[str, object]:
    """Rule kwargs for one trial of `family`, honoring the pinned `SEARCH_SPACES`.

    `suggest` is any optuna-Trial-shaped object (see `Suggest`). Unknown families raise
    `ValueError` — a study over a family with no pinned space is a study over nothing. The
    pullback fan is returned strictly ordered (`ema_fast < ema_mid < ema_slow`) by
    DETERMINISTIC clamping of whatever was suggested, so the invariant survives a future
    edit of the disjoint ranges without ever re-rolling the sampler (which would break
    seed-reproducibility).
    """
    space = SEARCH_SPACES.get(family)
    if space is None:
        raise ValueError(
            f"unknown rule family {family!r} -- pinned search spaces: {sorted(SEARCH_SPACES)}"
        )

    if family == "pullback_continuation":
        fast = _suggest_value(suggest, "ema_fast", space["ema_fast"])
        mid = _suggest_value(suggest, "ema_mid", space["ema_mid"])
        slow = _suggest_value(suggest, "ema_slow", space["ema_slow"])
        assert isinstance(fast, int) and isinstance(mid, int) and isinstance(slow, int)
        mid = min(mid, slow - 1)
        fast = min(fast, mid - 1)
        buffer_ticks = _suggest_value(suggest, "buffer_ticks", space["buffer_ticks"])
        return {"ema_periods": (fast, mid, slow), "buffer_ticks": buffer_ticks}

    return {name: _suggest_value(suggest, name, bounds) for name, bounds in space.items()}


# -- the trials budget, derived from the declaration (issue #528) --------------------------------
#
# `n_trials` -- the input to `research.deflate.expected_max_sharpe` -- used to be a number
# a human remembered to record. These two helpers are the smallest honest step past that:
# the SIZE of the explorable space is read off the rule, and a sweep that explored beyond
# it is refused rather than silently exceeded. The ledger itself is untouched -- this FEEDS
# its rows, it does not replace them.


def declared_cells(rule_kind: str) -> int:
    """The number of cells a FULL sweep of `rule_kind`'s declared space would visit.

    The derivable budget: the product over the declared dimensions of each one's grid size
    at its declared step, so an experiment record can state "explored N trials of a
    declared space of M cells" without anyone remembering M by hand. A kind that declares
    nothing sweepable (dca) is the honest `0`, not an error; an unregistered kind raises
    `ValueError` (`build_rule_from_params`'s own refusal), because there is no declaration
    to read and a plausible `0` would be a lie.

    This counts the space, it does not license searching it: the #476 harness samples a
    few dozen points inside it precisely BECAUSE the whole grid is thousands of trials the
    multiple-comparison budget cannot afford.
    """
    specs = _declared_space(rule_kind)
    if not specs:
        return 0
    cells = 1
    for spec in specs:
        cells *= spec.cells
    return cells


@dataclass(frozen=True)
class ExplorationCheck:
    """What a sweep explored, measured against what its rule declared (#528).

    `declared_cells` is the size of the whole declared space; `explored_cells` is the part
    of it the explored box covers at the same steps -- the denominator and numerator of
    "explored N of a declared space of M cells". Both are properties of the inputs, so the
    same sweep always yields the same check.
    """

    rule_kind: str
    declared_cells: int
    explored_cells: int


def explored_vs_declared(
    explored: Mapping[str, tuple[float, float]], rule_kind: str
) -> ExplorationCheck:
    """Honesty check for a sweep's ranges against the rule's declaration (#528).

    `explored` is the box actually swept: `{dimension: (min, max)}` in the declaration's
    own dimension names (for `pullback_continuation`, the fan slots `ema_fast`/`ema_mid`/
    `ema_slow`, not the packed `ema_periods`). Returns the check when every explored range
    sits inside its declared one; REFUSES -- `ValueError` naming the parameter and BOTH
    ranges -- otherwise, because an explored range that silently exceeds the declared one
    spends trials budget the count above never included, which is exactly the asymmetry
    #528 exists to close. A dimension the rule never declared is the worse drift (no
    denominator exists for it at all) and is refused in the same breath. The candor the
    number owes: `explored_cells` is a grid-convention count -- the sampler draws off-grid
    (`suggest_int`/`suggest_float` carry no step) and an off-grid draw is invisible to a
    min/max box -- so it prices the box at the declared steps, it never enumerates what
    was visited. An empty box explores the explicit `0`, never the empty product's `1`.

    PURE: reads only its arguments and the rule's own declaration -- no clock, no RNG, no
    ledger -- so a driver can call it per cell and get identical answers for identical
    sweeps.
    """
    specs = {spec.name: spec for spec in _declared_space(rule_kind)}

    violations: list[str] = []
    for name, (low, high) in sorted(explored.items()):
        spec = specs.get(name)
        if spec is None:
            violations.append(
                f"{name} swept {low}..{high} but {rule_kind} declares no such dimension "
                "-- an undeclared knob has no trials budget to spend from"
            )
            continue
        declared_lo, declared_hi = spec.bounds
        if Decimal(str(low)) < Decimal(str(declared_lo)) or Decimal(str(high)) > Decimal(
            str(declared_hi)
        ):
            violations.append(
                f"{name} swept {low}..{high} exceeds the declared {declared_lo}..{declared_hi}"
            )
    if violations:
        raise ValueError("; ".join(violations))

    # An empty box is the explicit zero, not an error (the module's own convention for
    # honest empties -- `declared_cells` gives dca `0` the same way): nothing was swept,
    # and the empty product's `1` would claim a cell no trial ever visited.
    covered = 1 if explored else 0
    for name, (low, high) in explored.items():
        spec = specs[name]
        span = Decimal(str(high)) - Decimal(str(low))
        covered *= int(span / spec.step) + 1
    return ExplorationCheck(
        rule_kind=rule_kind,
        declared_cells=declared_cells(rule_kind),
        explored_cells=covered,
    )


def split_chronologically(
    candles: Sequence[Candle], train_frac: Decimal = TRAIN_FRAC
) -> tuple[list[Candle], list[Candle]]:
    """Split `candles` into (train, held-out) at `train_frac` of the length, in ts order.

    The input is stably sorted by `ts` first (a shuffled feed must not shuffle the split),
    the cut is floored so train never exceeds the fraction, and the two windows share no
    bar. Empty input is empty output, not an error — a study with no history legitimately
    has nothing to optimize and the backtests downstream will say so.
    """
    if not Decimal(0) < train_frac < Decimal(1):
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    ordered = sorted(candles, key=lambda candle: candle.ts)
    cut = int((Decimal(len(ordered)) * train_frac).to_integral_value(rounding="ROUND_FLOOR"))
    return ordered[:cut], ordered[cut:]


def evaluate_params(
    rule_kind: str,
    product_id: str,
    params: dict[str, object],
    candles: Sequence[Candle],
    fee_pct: Decimal,
    slippage_pct: Decimal,
) -> BacktestResult:
    """Backtest one parameter set: build the rule, run the shipped engine, price the costs.

    `params` carries BOTH the searched kwargs and any caller-fixed ones (`granularity`
    for a turtle on a non-daily clock) and goes through `build_rule_from_params` — the one
    JSON-plain-to-constructor coercion boundary — so Decimal fields (`atr_stop_mult`,
    `fixed_rr`, `buffer_ticks`, ...) coerce exactly as they do everywhere else in the
    codebase. The objective a study maximizes is `result.expectancy`: per-trade,
    fee-and-slippage-adjusted, in exact Decimal.
    """
    rule = build_rule_from_params(rule_kind, {"product_id": product_id, **params})
    return backtest(rule, list(candles), fee_pct=fee_pct, slippage_pct=slippage_pct)


@dataclass(frozen=True)
class OverfittingGate:
    """The proposal gate: held-out sign + CSCV's PBO over the study's own trials.

    `passed` is exactly `held_out_positive AND pbo <= OVERFITTING_CEILING` (with `pbo is
    None` — too few or too-short usable trial columns — counted as a failure: a
    certificate the study could not produce is not a certificate). `failures` names each
    failing condition with its number so the refusal line can quote them; it is empty iff
    `passed`.
    """

    train_expectancy: Decimal
    held_out_expectancy: Decimal
    held_out_positive: bool
    pbo: Decimal | None
    n_columns_used: int
    n_columns_skipped: int
    passed: bool
    failures: tuple[str, ...]


def evaluate_gate(
    train_result: BacktestResult,
    test_result: BacktestResult,
    trial_columns: Sequence[Sequence[Decimal]],
    s: int = PBO_BLOCKS,
) -> OverfittingGate:
    """Gate a study's winner BEFORE it could even be proposed as a candidate.

    `train_result`/`test_result` are the winner's backtests on the train and held-out
    windows (train rides along for the report's degradation numbers; only the held-out sign
    decides). `trial_columns` is the study's per-trial per-trade net P&L — one column per
    trial. Columns with fewer than `MIN_TRADES_PER_COLUMN` closed trades are skipped rather
    than padded; `cscv.pbo` itself truncates any residual raggedness to the shortest
    column, dropping OLDEST rows, exactly as its docstring specifies. Fewer than 2 usable
    columns — or a shortest usable column with fewer than `s` trades, which could not be
    cut into blocks at all — means CSCV has no matrix, and the gate refuses rather than
    invent a number.
    """
    usable = [column for column in trial_columns if len(column) >= MIN_TRADES_PER_COLUMN]
    skipped = len(trial_columns) - len(usable)
    held_out_positive = test_result.expectancy > 0
    # cscv cuts its s blocks from the SHORTEST usable column and raises if it cannot; a
    # study that thin has no certificate either, so the gate says "unavailable" (refuse)
    # rather than letting the ValueError escape. The `len(usable) >= 2` on the left
    # short-circuits the `min` on the right, which would raise on an empty list.
    pbo_value = (
        cscv_pbo(usable, s=s).pbo
        if len(usable) >= 2 and min(len(column) for column in usable) >= s
        else None
    )

    failures: list[str] = []
    if not held_out_positive:
        failures.append(f"held-out expectancy {test_result.expectancy} <= 0")
    if pbo_value is None:
        failures.append(
            f"pbo unavailable ({len(usable)} of {len(trial_columns)} trials have >= "
            f"{MIN_TRADES_PER_COLUMN} closed trades; CSCV needs >= 2 usable columns of "
            f">= {s} trades)"
        )
    elif pbo_value > OVERFITTING_CEILING:
        failures.append(f"pbo {pbo_value} > {OVERFITTING_CEILING}")

    return OverfittingGate(
        train_expectancy=train_result.expectancy,
        held_out_expectancy=test_result.expectancy,
        held_out_positive=held_out_positive,
        pbo=pbo_value,
        n_columns_used=len(usable),
        n_columns_skipped=skipped,
        passed=not failures,
        failures=tuple(failures),
    )


def proposal_verdict(family: str, product_id: str, gate: OverfittingGate) -> str:
    """The one line a study may print about promotion — and only after the gate.

    When `gate.passed` the line says PROPOSE (a hypothesis for the unchanged gauntlet,
    nothing more); otherwise it says "no candidate may be proposed" and names every failing
    condition with its number. The harness refuses to emit a proposing line in any other
    state; that refusal is the product, not an apology.
    """
    numbers = (
        f"train expectancy {gate.train_expectancy} -> held-out "
        f"{gate.held_out_expectancy}, pbo {gate.pbo}"
    )
    if gate.passed:
        return (
            f"PROPOSE as candidate for the gauntlet (nothing live, nothing auto-tuned): "
            f"{family} on {product_id} -- {numbers}. The proposal then faces the unchanged "
            f"promotion gauntlet like any other candidate; the binding constraint remains "
            f"the fee."
        )
    return (
        f"no candidate may be proposed for {family} on {product_id}: "
        + "; ".join(gate.failures)
        + f" ({numbers})"
    )


@dataclass(frozen=True)
class TrialSummary:
    """One completed trial as the report carries it: what was tried, what it earned."""

    number: int
    params: dict[str, object]
    train_expectancy: Decimal
    n_trades: int


@dataclass(frozen=True)
class StudyReport:
    """Everything a finished study knows, with the gate attached.

    `best_params` is the optimizer's answer ON THE TRAIN WINDOW (merged with the
    caller-fixed params), `best_train_expectancy` its in-sample score, `held_out_result`
    the single re-pricing of that answer on the future the optimizer never saw, and `gate`
    the verdict on proposing any of it.
    """

    family: str
    product_id: str
    seed: int
    fee_pct: Decimal
    slippage_pct: Decimal
    best_params: dict[str, object]
    best_train_expectancy: Decimal
    held_out_result: BacktestResult
    gate: OverfittingGate
    trials: tuple[TrialSummary, ...]


class _Trial(Suggest, Protocol):
    """`Suggest` plus the one bookkeeping method the objective records its row under."""

    def set_user_attr(self, key: str, value: object) -> None: ...


def _import_optuna() -> ModuleType:
    """Import optuna lazily, naming the extra when it is absent.

    Deliberately inside the runner only: `import keel.research.tuning` stays clean without
    it (pinned by test), so a deployment that never runs studies never pays for — or even
    notices — the dependency.
    """
    try:
        import optuna
    except ImportError as exc:  # pragma: no cover -- exercised via a sys.modules block in tests
        raise RuntimeError(
            "run_study needs optuna, which is deliberately NOT a runtime dependency of "
            "keel-trader: it rides the repo's dev dependency group (uv sync) so shipped "
            "wheels stay clean. Install optuna to run parameter studies."
        ) from exc
    return optuna


def run_study(
    rule_kind: str,
    product_id: str,
    candles: Sequence[Candle],
    *,
    n_trials: int,
    seed: int,
    fee_pct: Decimal = TAKER_FEE_PCT,
    slippage_pct: Decimal = SLIPPAGE_FLOOR_PCT,
    train_frac: Decimal = TRAIN_FRAC,
    fixed_params: dict[str, object] | None = None,
    pbo_blocks: int = PBO_BLOCKS,
) -> StudyReport:
    """One seeded TPE study over one family's pinned space, gated before any proposal.

    Deterministic under a fixed `seed`: `TPESampler(seed=...)` over deterministic Decimal
    backtests, `n_startup_trials` at its default, and no parallelism inside the study —
    two runs with the same `(seed, candles, n_trials)` reproduce every trial (the
    acceptance test). The objective is the TRAIN-window expectancy only; the held-out
    window is touched exactly once, by the winner, after `optimize` returns. optuna's
    chatty INFO logging is silenced to WARNING so a study's stdout stays readable.

    `fixed_params` pins what is deliberately NOT searched — most importantly
    `granularity` for a turtle on a non-daily clock — and is merged ahead of the searched
    kwargs so a searched name could never silently shadow it.
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")

    optuna = _import_optuna()
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    train, test = split_chronologically(candles, train_frac)
    pinned: dict[str, object] = dict(fixed_params or {})

    def objective(trial: _Trial) -> float:
        # Searched first, pinned LAST: `**` unpacking keeps the later key, so a searched
        # name colliding with a pin can never silently shadow it.
        params = {**params_from_trial(rule_kind, trial), **pinned}
        result = evaluate_params(rule_kind, product_id, params, train, fee_pct, slippage_pct)
        trial.set_user_attr("params", params)
        trial.set_user_attr("train_expectancy", str(result.expectancy))
        trial.set_user_attr("n_trades", result.n_trades)
        trial.set_user_attr(
            "per_trade_pnl", [str(t.pnl) for t in result.trades if t.outcome != "open"]
        )
        return float(result.expectancy)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials)

    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    summaries = tuple(
        TrialSummary(
            number=t.number,
            params=dict(t.user_attrs["params"]),
            train_expectancy=Decimal(str(t.user_attrs["train_expectancy"])),
            n_trades=int(t.user_attrs["n_trades"]),
        )
        for t in completed
    )

    best = study.best_trial
    best_params = dict(best.user_attrs["params"])
    # The winner, re-priced: train again (for the report's degradation numbers) and the
    # held-out future once. Identical inputs to the objective's own backtest, so the
    # re-run is bit-identical by determinism, not by trust.
    train_result = evaluate_params(rule_kind, product_id, best_params, train, fee_pct, slippage_pct)
    held_out_result = evaluate_params(
        rule_kind, product_id, best_params, test, fee_pct, slippage_pct
    )
    columns = [[Decimal(value) for value in t.user_attrs["per_trade_pnl"]] for t in completed]
    gate = evaluate_gate(train_result, held_out_result, columns, s=pbo_blocks)

    return StudyReport(
        family=rule_kind,
        product_id=product_id,
        seed=seed,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        best_params=best_params,
        best_train_expectancy=train_result.expectancy,
        held_out_result=held_out_result,
        gate=gate,
        trials=summaries,
    )
