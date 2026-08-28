"""Each rule's declared parameter space (issue #528) -- the trials budget becomes derivable.

`n_trials` feeds `research.deflate.expected_max_sharpe`, and before #528 it was a number a
human remembered to record. These tests pin the OTHER end of that pipeline: every registered
rule family declares, on itself, the dimensions a sweep may legitimately explore -- name,
type, range, and the grid step the space is counted at -- so the size of the explorable space
is a property of the rule rather than of the operator's diligence.

The load-bearing pins, in order:

1. The declarations are EXACT (per family, field by field) and equal to the ranges
   `research.tuning` pinned for the #476 study -- one source of truth, not two that can
   disagree. `tests/research/test_tuning.py` pins the derivation side.
2. Every declared dimension names a constructor kwarg the rule REALLY accepts (`kwarg`) --
   a declaration that drifts from the signature under-counts trials, which is worse than no
   declaration at all (the issue's own acceptance criterion).
3. `describe()` renders the declaration JSON-plain, so a rule's self-description says what a
   parameter is ALLOWED to be, not just what it currently is.
4. `ParamSpec` refuses its own malformations loudly (inverted range, non-positive step, a
   type that does not match its bounds): a silently-broken declaration would corrupt every
   count derived from it.
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal

import pytest

from keel.agent import RULE_REGISTRY, build_rule_from_params
from keel.strategy.rules.base import ParamSpec, Rule
from keel.strategy.rules.dca import Dca
from keel.strategy.rules.turtle_breakout import TurtleBreakout


def _space(kind: str) -> tuple[ParamSpec, ...]:
    """The declaration read the way every consumer reads it: off a constructed rule."""
    return build_rule_from_params(kind, {"product_id": "BTC-USD"}).param_space()


# -- 1. the declarations are exact, and they are the ones tuning pinned ---------------------------


def test_turtle_declares_exactly_the_space_the_476_study_searched() -> None:
    """Field-for-field, equal to the ranges `tuning.SEARCH_SPACES` hand-maintained before
    #528 -- the walk-forward-validated Donchian channels, the KB ADX gate band, the Turtle
    2N stop, the nominal R:R. Steps are the older manual grids' resolutions -- the grid the
    space is COUNTED at; the #476 TPE study drew step-1 ints and continuous floats, so it
    did not move in them."""
    assert _space("turtle_breakout") == (
        ParamSpec("entry_lookback", "int", 20, 60, Decimal(5)),
        ParamSpec("exit_lookback", "int", 10, 30, Decimal(5)),
        ParamSpec("adx_threshold", "float", 20.0, 35.0, Decimal(5)),
        ParamSpec("atr_stop_mult", "decimal", 1.5, 3.0, Decimal("0.5")),
        ParamSpec("target_rr", "int", 3, 8, Decimal(1)),
    )


def test_rsi_declares_exactly_the_space_the_476_study_searched() -> None:
    assert _space("rsi_meanrev") == (
        ParamSpec("oversold", "float", 15.0, 30.0, Decimal(5)),
        ParamSpec("overbought", "float", 70.0, 85.0, Decimal(5)),
        ParamSpec("atr_mult", "decimal", 1.0, 2.5, Decimal("0.5")),
        ParamSpec("fixed_rr", "int", 1, 3, Decimal(1)),
        ParamSpec("rsi_period", "int", 10, 21, Decimal(1)),
    )


def test_pullback_declares_the_ema_fan_as_three_slots_of_one_kwarg() -> None:
    """The fan is searched per slot (the ranges are disjoint so the fan stays ordered), but
    the constructor takes ONE `ema_periods` tuple -- so each slot's `kwarg` names it."""
    assert _space("pullback_continuation") == (
        ParamSpec("ema_fast", "int", 5, 12, Decimal(1), param="ema_periods"),
        ParamSpec("ema_mid", "int", 15, 30, Decimal(1), param="ema_periods"),
        ParamSpec("ema_slow", "int", 40, 70, Decimal(1), param="ema_periods"),
        ParamSpec("buffer_ticks", "decimal", 0.01, 0.05, Decimal("0.01")),
    )


def test_the_declared_bounds_are_bit_equal_to_the_ranges_tuning_pinned() -> None:
    """The dedupe proof, stated without importing tuning: the bounds above ARE the (lo, hi)
    pairs the #476 harness searched, in the types its int-vs-float dispatch reads."""
    from keel.research import tuning

    for kind, space in tuning.SEARCH_SPACES.items():
        assert space == {spec.name: spec.bounds for spec in _space(kind)}, kind


def test_dca_declares_nothing_sweepable_and_the_abc_default_is_empty() -> None:
    """DCA is accumulation, not a risk-defined trade: no stop, no target, nothing a
    parameter study could legitimately sweep (#476 excluded it for exactly that). It
    inherits the ABC's empty declaration rather than overriding it, and a bare `Rule`
    subclass that declares nothing gets the same empty space -- the default is `()`, not
    an error, because an empty space is a truthful statement ("0 declared cells"), not a
    missing one."""
    assert _space("dca") == ()
    assert Dca("BTC-USD").param_space() == ()

    class _Bare(Rule):
        name = "bare"
        params: dict = {}

        def detect(self, candles_by_tf):  # type: ignore[no-untyped-def]
            return None

        def exit_signal(self, held, candles_by_tf):  # type: ignore[no-untyped-def]
            return False

        def describe(self) -> dict:
            return {"name": self.name, "params": self.params}

    assert _Bare().param_space() == ()


# -- 2. a declaration that drifts from the signature under-counts trials --------------------------


@pytest.mark.parametrize("kind", sorted(RULE_REGISTRY))
def test_every_declared_dimension_feeds_a_real_constructor_kwarg(kind: str) -> None:
    """The issue's acceptance criterion, stated as a test: each declared dimension's
    `kwarg` must exist in the rule class's constructor signature. A declaration naming a
    kwarg that no longer exists would silently shrink the space (and the trials count
    derived from it) while still LOOKING like a declaration."""
    accepted = set(inspect.signature(RULE_REGISTRY[kind]).parameters) - {"self", "name"}
    for spec in _space(kind):
        assert spec.kwarg in accepted, f"{kind}.{spec.name} -> {spec.kwarg}: not a kwarg"


@pytest.mark.parametrize("kind", ["turtle_breakout", "rsi_meanrev", "pullback_continuation"])
def test_every_declared_dimension_is_persisted_by_describe(kind: str) -> None:
    """A declared dimension must also be a parameter the row PERSISTS: a space over a knob
    that `describe()["params"]` drops (pullback's non-persisted `granularity`) would count
    cells no stored rule could ever carry."""
    rule = build_rule_from_params(kind, {"product_id": "BTC-USD"})
    persisted = set(rule.describe()["params"])
    for spec in rule.param_space():
        assert spec.kwarg in persisted, f"{kind}.{spec.kwarg}: not persisted"


# -- 3. describe() renders the space JSON-plain ----------------------------------------------------


def test_describe_carries_the_declaration_json_plain() -> None:
    """`describe()` is the rule's self-description, so it now says what each parameter is
    ALLOWED to be, not just what it currently is. The rendering is JSON-plain (step as a
    string, matching the repo's TEXT-money convention) because `keel simulate` puts
    `rule.describe()` into a ledger row that must serialise."""
    rule = TurtleBreakout("BTC-USD")
    rendered = rule.describe()["param_space"]
    assert rendered == [spec.plain() for spec in rule.param_space()]
    assert rendered[0] == {
        "name": "entry_lookback",
        "type": "int",
        "lo": 20,
        "hi": 60,
        "step": "5",
    }
    assert json.dumps(rendered)  # ledger-row safe


def test_describe_of_a_rule_with_no_space_says_so_plainly() -> None:
    assert Dca("BTC-USD").describe()["param_space"] == []


# -- 4. the spec refuses its own malformations -----------------------------------------------------


def test_param_spec_refuses_an_inverted_range() -> None:
    with pytest.raises(ValueError, match="lo"):
        ParamSpec("entry_lookback", "int", 60, 20, Decimal(1))


def test_param_spec_refuses_a_non_positive_step() -> None:
    with pytest.raises(ValueError, match="step"):
        ParamSpec("entry_lookback", "int", 20, 60, Decimal(0))


def test_param_spec_refuses_a_type_that_does_not_match_its_bounds() -> None:
    """`type` is what the int-vs-float suggest dispatch and the cells arithmetic read; a
    "int" declared on fractional bounds (or an unknown type word) would mis-dispatch both."""
    with pytest.raises(ValueError, match="int"):
        ParamSpec("adx_threshold", "int", 20.0, 35.0, Decimal(1))
    with pytest.raises(ValueError, match="type"):
        ParamSpec("adx_threshold", "number", 20.0, 35.0, Decimal(1))


def test_param_spec_cells_counts_the_grid_and_floors_a_ragged_step() -> None:
    """`cells` is the countable size of the dimension at its declared step: inclusive of
    both ends, and FLOORING a step that does not evenly divide the span (the honest count
    of grid points inside the range, never one beyond it)."""
    assert ParamSpec("entry_lookback", "int", 20, 60, Decimal(5)).cells == 9  # 20..60 by 5
    assert ParamSpec("entry_lookback", "int", 20, 61, Decimal(5)).cells == 9  # 61 off-grid
    assert ParamSpec("atr_stop_mult", "decimal", 1.5, 3.0, Decimal("0.5")).cells == 4
    assert ParamSpec("buffer_ticks", "decimal", 0.01, 0.05, Decimal("0.01")).cells == 5
    assert ParamSpec("target_rr", "int", 3, 3, Decimal(1)).cells == 1  # a single legal point


def test_param_spec_bounds_preserve_the_int_vs_float_dispatch_types() -> None:
    """The derived `(lo, hi)` pair must stay an INT pair exactly when the dimension is
    integral: `tuning._suggest_value` dispatches on `isinstance(low, int)`, and a float
    20.0 would silently turn a discrete lookback into a continuous draw."""
    lookback = ParamSpec("entry_lookback", "int", 20, 60, Decimal(5))
    assert lookback.bounds == (20, 60)
    assert all(isinstance(bound, int) for bound in lookback.bounds)
    adx = ParamSpec("adx_threshold", "float", 20.0, 35.0, Decimal(5))
    assert adx.bounds == (20.0, 35.0)
    assert all(isinstance(bound, float) for bound in adx.bounds)
