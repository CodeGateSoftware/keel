"""Lookahead and recursive-bias detection (issue #440, C1a).

PBO/CSCV (``keel.research.cscv``) answers "were these parameters over-selected across a
trial matrix?" -- it says nothing about a single-configuration strategy that reads
information it could not have had. A rule whose target is the future swing high backtests
beautifully and is worthless live. This module is the other question: **does this rule's
decision at bar N change when bars after N become visible?**

THE SEAM, AND WHY
-----------------
``Rule.detect(candles_by_tf) -> Setup | None`` is documented pure
(``keel/strategy/rules/base.py``), and the backtester fixes its per-bar semantics
(``keel/strategy/backtest.py``: ``candles_by_tf = {tf: candles[: i + 1]}``): the decision AT
bar N is detect over data ending at N -- the last bar of the prefix is the bar whose close
triggered the decision, and ``Setup.ts`` names it. That is what a live engine sees at N, and
it is the contract this harness replays. (``engine.evaluate`` does not walk the timeline
itself; it is driven once per bar by the agent/backtester with exactly such a growing
prefix, so reusing detect-on-prefix -- rather than evaluate -- is the faithful seam, without
dragging the CTS scorer and the regime gates into a bias diagnostic that must not depend on
them.)

Two comparisons are run per anchor, because a single detect call can only be asked about the
LAST bar it was handed:

**A. Full-series reference (the classic leak).** ``detect`` is run once on the full series.
Its setup claims an anchor bar (``Setup.ts``); the harness then re-runs detect on the prefix
ending at THAT bar -- the live view of the same decision -- and diffs presence and
entry/stop/target. A rule that fires at the argmax bar of the whole series, or prices the
setup off data after the anchor, diverges there. A rule that never fires on the full series
has nothing attributable to diff and is reported clean (with the bars it walked); a clean
last-bar rule is only ever compared at the final bar, where prefix and full coincide -- the
check is honest about being vacuous for that shape rather than manufacturing a verdict.

**B. Higher-timeframe poison (the engine-veto leak).** At EVERY sampled anchor, detect is
run twice: once on the live view (every timeframe truncated to bars CLOSED by the anchor's
close) and once on a poison view (the anchor's own and finer timeframes identical, every
COARSER timeframe at full length -- i.e. the future closes them). The engine's higher-TF
bias gate (``keel/strategy/engine.py`` ``_higher_tf_bias_ok``) reads the coarsest available
timeframe, and the natural leak is reading that coarse tail while its last bar is still
forming: a rule keyed on closed coarse bars (``ts + step <= anchor close``) sees the same
data in both views and is clean; a rule reading ``coarse[-1]`` blindly is handed different
values by the future and diverges, bar named.

Coarse-bar closedness is cut at the anchor bar's CLOSE instant (``anchor.ts +
anchor_step``), matching the backtester's decision time (bar i is complete when detect sees
``candles[: i + 1]``). Every coarse bar excluded from the live view closes strictly after
that instant, so the poison view's extra coarse bars are exactly the future -- a rule that
legitimately uses a coarse bar closed within the anchor's own span is not falsely flagged.
The anchor's own and finer timeframes include every bar with ``ts <= anchor.ts`` (all such
bars are closed by the anchor's close).

**Attribution rule (false-positive control):** two setups are compared only when both are
present and claim the SAME ``ts``. A rule that points at a different bar once more history
arrives is not thereby lookahead (an argmax anchor legitimately moves); only the values at a
bar both views agree on, and presence-versus-absence at the full run's claimed anchor, are
evidence.

**Coverage honesty:** when the dataset carries no granularity coarser than the anchor's,
Axis B cannot run at all and a clean verdict speaks for the one time frame only -- the
report then carries a ``notes`` line saying the axis was not run, and every renderer
prints it, so a clean single-TF verdict never reads as full coverage.

Cost: ~2 detect calls per sampled anchor plus 1 for the full run; ``sample_step`` bounds the
anchor count, ``warmup`` skips the indicator warmup region where every rule returns None or
noise.

Recursive bias (the lighter check, same issue): an indicator whose value keeps changing as
history grows -- long after its nominal warmup -- makes a backtest's past signals depend on
how much data the researcher happened to load. ``recursive_analysis`` measures the honest
summary of that: ``max |v(N) - v(N+step)|`` over growing prefixes from ``min_warmup``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from keel.data.history import GRANULARITY_SECONDS
from keel.strategy.rules.base import Setup
from keel.types import Candle, Granularity

__all__ = [
    "DEFAULT_WARMUP",
    "HIGHER_TF_NOT_RUN_NOTE",
    "LookaheadDivergence",
    "LookaheadReport",
    "RecursiveReport",
    "lookahead_analysis",
    "recursive_analysis",
    "render_lines",
]

#: Divergence fields, in report order. ``setup_present`` covers present-in-one-view-only.
_FIELDS: tuple[str, ...] = ("setup_present", "entry", "stop", "target")

#: Default anchor-stride 1: every bar past warmup is checked (the diagnostic default);
#: callers bounding cost pass a larger stride (the promotion gate samples to ~200 anchors).
_DEFAULT_SAMPLE_STEP = 1

#: Default bar-skip before the first anchor: past every registered rule's warmup region
#: (the longest lookback/period is ~55 bars), where detect returns None or warmup noise and
#: a divergence there would be an artifact of the harness, not of the rule. Callers with the
#: rule in hand (`keel rules lookahead`) raise it to the rule's own longest period.
DEFAULT_WARMUP = 50

#: Carried on the report (and rendered) when the dataset holds no granularity coarser than
#: the anchor's: Axis B -- the higher-TF poison view, the engine-veto leak check -- could not
#: run, so a clean verdict there is a verdict about the one time frame only. Saying so beats
#: implying coverage the run did not have.
HIGHER_TF_NOT_RUN_NOTE = "higher-TF axis not run: no coarser series cached"


@dataclass(frozen=True)
class LookaheadDivergence:
    """One bar where the rule's live-view decision differs from its future-extended one.

    ``field`` is ``"entry"``/``"stop"``/``"target"`` for a price that moved beyond tolerance,
    or ``"setup_present"`` when the signal exists in one view and not the other
    (``"absent"``/``"present"`` values). ``prefix_value`` is what the live engine at that
    bar would have seen; ``full_value`` is what the future-extended view says -- WHICH bar
    diverged and how, not just pass/fail."""

    bar_ts: int
    field: str
    prefix_value: str
    full_value: str


@dataclass(frozen=True)
class LookaheadReport:
    """The truncation-diff verdict for one rule over one multi-timeframe dataset."""

    rule_id: str
    #: Anchors actually walked (post-warmup, strided, final bar always included).
    n_bars_checked: int
    #: The first ``max_divergences`` divergences, in walk order.
    divergences: tuple[LookaheadDivergence, ...] = ()
    #: ALL divergences found (``len(divergences)`` is capped; this count is not).
    n_divergences: int = 0
    sample_step: int = _DEFAULT_SAMPLE_STEP
    anchor_granularity: str | None = None
    #: Coverage caveats -- e.g. the higher-TF poison axis not running because no coarser
    #: series was supplied. Rendered by ``render_lines``; empty on a fully-covered run.
    notes: tuple[str, ...] = ()

    @property
    def verdict(self) -> str:
        return "lookahead_detected" if self.n_divergences else "clean"


@dataclass(frozen=True)
class RecursiveReport:
    """The warmup-drift verdict for one indicator over one candle series."""

    rule_id: str
    indicator: str
    #: Growing prefixes sampled (``min_warmup`` to full length, step apart).
    n_prefixes: int
    #: The prefix length N with the worst ``|v(N) - v(N+step)|``; ``None`` when fewer than
    #: two prefixes were sampled (nothing to compare -- reported stable, honestly).
    worst_n: int | None = None
    max_drift: float = 0.0
    step: int = 25
    tolerance: float = 1e-8

    @property
    def verdict(self) -> str:
        return "recursive_drift" if self.max_drift > self.tolerance else "stable"


#: What ``render_lines`` accepts.
BiasReport = LookaheadReport | RecursiveReport


# -- lookahead (truncation diff) -------------------------------------------------------------------


def _gran_rank(gran: Granularity) -> int:
    """Coarseness rank by bar duration (``GRANULARITY_SECONDS`` is the one duration table,
    shared with the history backfill, so a granularity added there ranks here for free)."""
    return GRANULARITY_SECONDS[gran]


def _live_view(
    candles_by_tf: dict[Granularity, list[Candle]],
    anchor_ts: int,
    anchor_close_ts: int,
    *,
    coarse_full: bool = False,
) -> dict[Granularity, list[Candle]]:
    """The multi-timeframe view of the data at one anchor.

    The anchor's own and every FINER timeframe: bars with ``ts <= anchor_ts`` (all closed by
    the anchor's close). Every COARSER timeframe: bars closed by the anchor's close
    (``ts + step <= anchor_close_ts``) -- unless ``coarse_full`` (the poison view), which
    hands the rule the coarser series at FULL length so the future's closed versions of
    those bars are visible. Cut on ``ts``, never on list length: a coarser series aligned by
    length would hand the rule half-formed bars."""
    anchor_step_secs = anchor_close_ts - anchor_ts
    view: dict[Granularity, list[Candle]] = {}
    for gran, candles in candles_by_tf.items():
        if not candles:
            continue
        if _gran_rank(gran) <= anchor_step_secs:
            view[gran] = [c for c in candles if c.ts <= anchor_ts]
        elif coarse_full:
            view[gran] = list(candles)
        else:
            step = GRANULARITY_SECONDS[gran]
            view[gran] = [c for c in candles if c.ts + step <= anchor_close_ts]
    return view


def _diff_setups(
    prefix: Setup | None, full: Setup | None, *, tolerance: Decimal
) -> list[LookaheadDivergence]:
    """The divergences between the live-view setup and the future-extended one AT THE SAME
    anchor. Presence first, then prices beyond tolerance; setups claiming different bars
    (``ts``) are not comparable -- see the module's attribution rule."""
    if prefix is None and full is None:
        return []
    if (prefix is None) != (full is None):
        lone = full if full is not None else prefix
        assert lone is not None  # noqa: S101 - exactly one is None here, by the branch above
        return [
            LookaheadDivergence(
                bar_ts=lone.ts,
                field="setup_present",
                prefix_value="present" if prefix is not None else "absent",
                full_value="present" if full is not None else "absent",
            )
        ]
    assert prefix is not None and full is not None  # noqa: S101 - narrowed for the checker
    if prefix.ts != full.ts:
        return []
    divergences = []
    for name, prefix_value, full_value in (
        ("entry", prefix.entry, full.entry),
        ("stop", prefix.stop, full.stop),
        ("target", prefix.target, full.target),
    ):
        if abs(prefix_value - full_value) > tolerance:
            divergences.append(
                LookaheadDivergence(
                    bar_ts=prefix.ts,
                    field=name,
                    prefix_value=str(prefix_value),
                    full_value=str(full_value),
                )
            )
    return divergences


def lookahead_analysis(
    detect_fn: Callable[[dict[Granularity, list[Candle]]], Setup | None],
    candles_by_tf: dict[Granularity, list[Candle]],
    *,
    rule_id: str = "",
    sample_step: int = _DEFAULT_SAMPLE_STEP,
    warmup: int = DEFAULT_WARMUP,
    tolerance: Decimal = Decimal("0.00000001"),
    max_divergences: int = 5,
) -> LookaheadReport:
    """Truncation-diff a detect callable for lookahead bias. See the module docstring for
    the seam, the two comparison axes, and the attribution rule.

    ``detect_fn`` is any callable with ``Rule.detect``'s shape -- a bound rule detect, or a
    wrapper around saved-rule params. ``candles_by_tf`` is the multi-timeframe dataset; the
    FINEST granularity present supplies the anchor bars (the trading timeframe; coarser
    series are the higher-TF bias data, as in the engine).
    """
    if sample_step < 1:
        raise ValueError(f"sample_step must be >= 1, got {sample_step}")
    if not candles_by_tf:
        return LookaheadReport(
            rule_id=rule_id,
            n_bars_checked=0,
            sample_step=sample_step,
            notes=(HIGHER_TF_NOT_RUN_NOTE,),
        )

    anchor_tf = min(candles_by_tf, key=_gran_rank)
    anchor_step = GRANULARITY_SECONDS[anchor_tf]
    anchor_candles = candles_by_tf[anchor_tf]
    has_coarser = any(_gran_rank(g) > anchor_step for g in candles_by_tf)

    full_setup = detect_fn(dict(candles_by_tf))

    last_index = len(anchor_candles) - 1
    # The final bar is ALWAYS an anchor when it lies past warmup: a last-bar-deciding rule is
    # only diffable against the full-series run there (prefix and full coincide), so a stride
    # that skipped it would silently drop the one anchor that shape of rule can fail at.
    indices = sorted(
        set(range(warmup, len(anchor_candles), sample_step))
        | ({last_index} if last_index >= warmup else set())
    )

    kept: list[LookaheadDivergence] = []
    seen: set[tuple[int, str, str, str]] = set()
    n_divergences = 0

    def _record(found: list[LookaheadDivergence]) -> None:
        nonlocal n_divergences
        for divergence in found:
            key = (
                divergence.bar_ts,
                divergence.field,
                divergence.prefix_value,
                divergence.full_value,
            )
            if key in seen:
                continue
            seen.add(key)
            n_divergences += 1
            if len(kept) < max_divergences:
                kept.append(divergence)

    for n in indices:
        anchor = anchor_candles[n]
        anchor_close_ts = anchor.ts + anchor_step
        live = _live_view(candles_by_tf, anchor.ts, anchor_close_ts)
        live_setup = detect_fn(live)

        # Axis B -- the higher-TF poison view, at every anchor where a coarser series exists.
        if has_coarser:
            poison = _live_view(candles_by_tf, anchor.ts, anchor_close_ts, coarse_full=True)
            _record(_diff_setups(live_setup, detect_fn(poison), tolerance=tolerance))

        # Axis A -- the full-series run's claimed anchor, diffed against the live view of it.
        if full_setup is not None and anchor.ts == full_setup.ts:
            _record(_diff_setups(live_setup, full_setup, tolerance=tolerance))

    return LookaheadReport(
        rule_id=rule_id,
        n_bars_checked=len(indices),
        divergences=tuple(kept),
        n_divergences=n_divergences,
        sample_step=sample_step,
        anchor_granularity=anchor_tf.value,
        notes=() if has_coarser else (HIGHER_TF_NOT_RUN_NOTE,),
    )


# -- recursive drift -------------------------------------------------------------------------------


def recursive_analysis(
    candles: list[Candle],
    *,
    indicator_fn: Callable[[list[Candle]], float],
    min_warmup: int,
    rule_id: str = "",
    indicator_name: str = "indicator",
    step: int = 25,
    tolerance: float = 1e-8,
) -> RecursiveReport:
    """Measure whether ``indicator_fn`` converges once its warmup is behind it.

    For growing prefixes from ``min_warmup`` to the full series (``step`` bars apart), the
    worst consecutive-window change ``|v(N) - v(N+step)|`` is reported with the prefix
    length it occurred at. An indicator whose value depends on HOW MUCH history it was fed
    (recursive bias) keeps drifting long after warmup; a converging one (Wilder ATR on a
    stationary range, say) settles within tolerance. The lighter check of issue #440: a
    summary metric, not a forensic diff."""
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")
    lengths = list(range(min_warmup, len(candles) + 1, step))
    values = [indicator_fn(candles[:length]) for length in lengths]

    worst_n: int | None = None
    max_drift = 0.0
    for n, (a, b) in zip(lengths, zip(values, values[1:]), strict=False):
        drift = abs(a - b)
        if drift > max_drift:
            max_drift = drift
            worst_n = n
    return RecursiveReport(
        rule_id=rule_id,
        indicator=indicator_name,
        n_prefixes=len(lengths),
        worst_n=worst_n,
        max_drift=max_drift,
        step=step,
        tolerance=tolerance,
    )


# -- rendering ------------------------------------------------------------------------------


def render_lines(report: BiasReport) -> list[str]:
    """Operator-facing lines for either report: who was checked, how many bars, the first
    divergences (bar ts, field, both values) or the worst drift, and the verdict."""
    if isinstance(report, LookaheadReport):
        lines = [
            f"lookahead analysis: rule {report.rule_id or '?'} -- anchor "
            f"{report.anchor_granularity or '?'}, {report.n_bars_checked} bar(s) checked "
            f"(sample_step={report.sample_step})",
            f"verdict: {report.verdict}",
        ]
        for note in report.notes:
            lines.append(f"  note: {note}")
        for divergence in report.divergences:
            lines.append(
                f"  bar ts={divergence.bar_ts} field={divergence.field}: "
                f"prefix={divergence.prefix_value} full={divergence.full_value}"
            )
        unshown = report.n_divergences - len(report.divergences)
        if unshown > 0:
            lines.append(f"  ... and {unshown} more divergence(s) not shown")
        return lines
    lines = [
        f"recursive analysis: rule {report.rule_id or '?'} -- indicator {report.indicator}, "
        f"{report.n_prefixes} growing prefix(es) (step={report.step})",
        f"verdict: {report.verdict}",
    ]
    if report.worst_n is None:
        lines.append("  fewer than two sampled prefixes; nothing to compare")
    else:
        lines.append(
            f"  worst drift |v(N)-v(N+{report.step})| = {report.max_drift:.3e} at N="
            f"{report.worst_n} (tolerance {report.tolerance:.1e})"
        )
    return lines
