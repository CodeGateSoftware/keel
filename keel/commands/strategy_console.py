"""The Rules menu -- the console's strategy console (issue #390 C4; PRD O11 and §3's tree).

Everything here is DISPATCH, never behavior (PRD O2, the discipline `compliance_console`
keeps): the ledger reads the rules table and the recorded paper track (through
`keel.commands.rules`' extracted services, `strategy.promotion`'s own decision, and the
`insights` service's own gate distance), simulate is `commands.simulate.run_simulation`
with the active profile's config/db, and every form collects fields at the terminal and
calls the same service the CLI command calls. No sizing, gating or reporting math lives here.

The surfaces, all pure (or injected-I/O) and unit-testable without curses:

* **The sub-menu model** -- `STRATEGY_MENU`, PRD §3's Rules branch as the O11 strategy
  console: the tried-vs-used ledger, simulate + results, add, retry (backtest + promote,
  `--force` TYPED), enable/disable/demote, insights.
* **The tried-vs-used ledger (O11.2)** -- one view answering "which strategies are in use,
  which were tried, and WHY are the tried ones not used", split by COST. The ENTRY render is
  CHEAP and sources only recorded state: every rule row with its lifecycle status, kind,
  product and recorded stamps (`promoted_at`/`demoted_at`), plus the `insights` service's
  promotion-gate distance for `paper` rules (kind-wide, over the recorded paper orders --
  bounded, never a backtest). It invokes ZERO backtests, pinned by spy: the entry-time
  re-backtest this view shipped with measured ~7.5 minutes for ONE rsi_meanrev rule over 5y
  of hourly candles (the walk is intrinsically quadratic), ~2.4 hours for a 19-rule hourly
  deployment, uncancellable -- so it is gone. Nothing persists a last-backtest result
  (`rules backtest`/`promote` write no rows; the schema's `backtests` table has no writer),
  so the entry invents no storage either. The full per-rule backtest verdict is an EXPLICIT,
  per-rule, Enter-gated re-compute in the rule's detail view ("re-compute this rule's
  verdict"), warned before it runs (full-window backtest, minutes on long series), honestly
  blocking during the one rule (like simulate/fetch) and Esc-cancellable between rules; the
  result is HELD in the ledger's state and invalidated when the ledger is rebuilt from the
  rules table on the next entry. G4/PBO is honestly NOT RUN there (no `--pbo-session` is
  named), and `can_promote`'s own reason says so.
* **Simulate (O11.1)** -- an ARMED view that shows the TARGET REPORT PATH before anything
  runs (the confirm step) and pins that exact path INTO the run (`out_path`), so a run
  crossing UTC midnight cannot write a different filename than the one confirmed;
  `run_simulation` on Enter (the CLI's own defaults: 5y, $500/month, the allowlist's
  products, history fetched when the cache does not cover the window), and a results screen
  rendering the service's own verdict/report verbatim under a pinned verdict+path footer.
  The run blocks the loop exactly like `f` fetch does -- the CLI's own UX, mirrored
  honestly -- with the progress lines the CLI would have streamed collected and shown at
  the head of the results (and kept above the error line when the run fails).
* **The forms (O11.3/O11.4)** -- add (per-field help from `rules.describe_params`, the O8
  single source, offering only the params the kind PERSISTS; lands as `candidate` exactly
  as the CLI does, with the SERVICE's own validation messages rendered), retry (re-backtest
  always; promote only on an explicit y/N; `--force` behind
  `clis_typed_promote_force_gate` -- console-ADDED ceremony over the CLI's bare `--force`
  flag, built on the shared typed-confirmation gate and quoting the CLI's own force
  warning; exact phrase, never pre-filled, failing closed), and enable as the documented
  restore path.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from keel import agent
from keel.commands.rules import (
    ParamHelp,
    RulesRefused,
    RulesUsageError,
    add_rule_row,
    apply_rule_demote,
    apply_rule_disable,
    apply_rule_enable,
    attempt_promotion,
    backtest_resolved,
    describe_params,
    resolve_rule_backtest,
    run_rule_backtest,
)
from keel.commands.simulate import (
    SimulationOutcome,
    default_report_path,
    run_simulation,
)
from keel.commands.tui import ScreenLine, _blank, _message_style
from keel.types import Granularity

if TYPE_CHECKING:
    from keel.config import Config
    from keel.data.repository import Repository

#: One terminal prompt: injected so every form is unit-testable with a scripted fake, and so
#: the live loop can run the whole form through the curses suspend/restore dance.
PromptFn = Callable[[str], str]

#: The width every console line must fit (`_paint` clips at the window width; 80-column
#: terminals are this dashboard's stated target) -- the same budget `compliance_console`
#: keeps, applied by wrapping rather than clipping.
_WIDTH = 78


def _wrap(text: str, *, indent: str = "  ", width: int = _WIDTH) -> list[str]:
    """Wrap `text` on spaces to the 80-column budget, continuation lines carrying `indent`.
    PURE -- the same rule `compliance_console._wrap` keeps, over `textwrap` so a doc quote
    or a reason sentence can never lose its tail to `_paint`'s clip."""
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [
        indent
    ]


# -- the sub-menu model (PRD §3's Rules branch, as the O11 strategy console) ----------------------


@dataclass(frozen=True)
class StrategyEntry:
    """One entry of the Rules sub-menu. `kind` is the closed dispatch vocabulary: `"view"`
    renders a browsable report (the ledger), `"armed"` opens an ARMED compute view (simulate
    -- Enter is the confirm step), `"form"` runs a rules service at the terminal, and
    `"insights"` opens the existing insights overlay (returning here on close)."""

    ordinal: int
    label: str
    description: str
    kind: str  # "view" | "armed" | "form" | "insights"
    target: str


#: PRD §3's Rules branch in tree order, worded as the O11 loop: ledger, simulate, add,
#: retry, and the lifecycle actions beneath them. The descriptions are O8's plain-English
#: "what will this do" in miniature, naming the dispatch honestly.
STRATEGY_MENU: tuple[StrategyEntry, ...] = (
    StrategyEntry(
        ordinal=1,
        label="tried-vs-used ledger",
        description=(
            "every rule with its lifecycle status and recorded context (read-only; no "
            "backtest runs here -- each rule's verdict is an explicit per-rule re-compute)"
        ),
        kind="view",
        target="ledger",
    ),
    StrategyEntry(
        ordinal=2,
        label="simulate + results",
        description=(
            "run the deterministic replay on this deployment (Enter confirms; it fetches "
            "and writes the report) and read the verdict"
        ),
        kind="armed",
        target="simulate",
    ),
    StrategyEntry(
        ordinal=3,
        label="add a strategy",
        description=(
            "the rules add flow: kind, product, params -- help at each field; lands as "
            "candidate exactly as the CLI does"
        ),
        kind="form",
        target="add",
    ),
    StrategyEntry(
        ordinal=4,
        label="retry a strategy",
        description=(
            "re-run the backtest, then re-attempt promote (confirm; --force is typed) "
            "through the same services the CLI calls"
        ),
        kind="form",
        target="retry",
    ),
    StrategyEntry(
        ordinal=5,
        label="enable (restore)",
        description="the documented restore path for a disabled rule -- back at candidate",
        kind="form",
        target="enable",
    ),
    StrategyEntry(
        ordinal=6,
        label="disable",
        description="take a rule out of the lifecycle (terminal; enable restores it)",
        kind="form",
        target="disable",
    ),
    StrategyEntry(
        ordinal=7,
        label="demote",
        description="step a rule back one stage (live->paper->candidate)",
        kind="form",
        target="demote",
    ),
    StrategyEntry(
        ordinal=8,
        label="insights",
        description="the promotion-gate distance and journal view (read-only)",
        kind="insights",
        target="",
    ),
)


def strategy_entry(ordinal: int) -> StrategyEntry | None:
    """The entry selected by its displayed ordinal, or `None` -- the one-lookup rule every
    console menu keeps, so the rendered ordinals and the shortcut keys cannot drift."""
    for entry in STRATEGY_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


#: This module's screens' contextual help (O8, issue #394 C7) -- the rows the `?`
#: overlay renders, keyed by the live loop's mode names. Plain `(subject, description)`
#: pairs so the text stays HERE with the module that owns the screens;
#: `keel.commands.help_console` is the registry and renderer.
CONTEXT_HELP: dict[str, tuple[tuple[str, str], ...]] = {
    "strategy": (
        (
            "the tried-vs-used ledger",
            "every rule row with its lifecycle status AND the machine's recorded reason "
            "it sits there -- the failing promotion-gate floor, the disabled context, "
            "the demotion; read-only, and zero backtests run to render it",
        ),
        (
            "simulate + results",
            "the deterministic replay on the ACTIVE deployment: Enter is the confirm "
            "step, the run fetches and writes exactly as `keel simulate` does, and the "
            "verdict (GO-LIVE / TRAIN-MORE), gates and the DCA benchmark render after",
        ),
        (
            "add a strategy",
            "the `rules add` flow at the terminal: kind, product, params -- each param "
            "field's help renders from the rule class itself (describe_params), and the "
            "row lands as candidate exactly as the CLI's add does",
        ),
        (
            "retry, and the lifecycle actions",
            "retry re-runs the backtest and re-attempts promote through the same "
            "services the CLI calls: promote's confirm is a y/N, and `--force` stays "
            "TYPED -- you type the phrase yourself at the terminal, and the prompt "
            "cannot be pre-filled. enable is the documented restore path for a disabled "
            "rule; disable and demote step rules back through the lifecycle",
        ),
        (
            "insights",
            "the per-rule promotion-gate distance and the trade journal, read-only",
        ),
    ),
    "strategy-ledger": (
        (
            "the rows",
            "one per rule: lifecycle status, the recorded reason it sits there, and "
            "the params it runs -- built cheaply from recorded state only",
        ),
        (
            "Enter",
            "opens the rule's detail (params with their per-field help, the paper gate's "
            "distance, and the explicit re-compute -- which is the ONE place the "
            "strategy console runs a backtest)",
        ),
    ),
    "strategy-rule": (
        (
            "the rule's params",
            "every parameter with its value and the help text the rule CLASS carries "
            "(describe_params by introspection) -- one source, the classes themselves",
        ),
        (
            "re-compute (ARMED)",
            "Enter runs the FULL-WINDOW backtest over the cached candles and judges it "
            "through the promotion gate -- real work that can take minutes; Esc or q "
            "returns without running anything",
        ),
    ),
    "strategy-simulate": (
        (
            "the ARMED view",
            "the plan (products, window, the deployment's db) renders first; Enter is "
            "the confirm step, and the run blocks the loop exactly like a fetch",
        ),
        (
            "the verdict",
            "GO-LIVE or TRAIN-MORE with the gates' own numbers, the DCA benchmark "
            "comparison and the tier matrix; the written report is browsable from the "
            "Research menu",
        ),
    ),
}


def build_strategy_menu_lines(*, cursor: int = 0, message: str | None = None) -> list[ScreenLine]:
    """The Rules sub-menu screen: every entry with its description wrapped to the 80-column
    budget, exactly one cursor-marked row, and the last action's confirmation lines as the
    toast. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- rules", "heading"),
        _blank(),
    ]
    cursor = max(0, min(cursor, len(STRATEGY_MENU) - 1))
    for index, entry in enumerate(STRATEGY_MENU):
        marker = ">" if index == cursor else " "
        head = f"{marker} {entry.ordinal:>2}  {entry.label}"
        style = "heading" if index == cursor else "normal"
        lines.append(ScreenLine(head, style))
        for wrapped in _wrap(f"{entry.description}.", indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("up/k down/j move · Enter/Space select · 1-8 jump", "muted"))
    lines.append(ScreenLine("q/Esc/m to the console menu", "muted"))
    if message is not None:
        lines.append(_blank())
        for part in message.splitlines():
            lines.append(ScreenLine(part, _message_style(part)))
    return lines


# -- the tried-vs-used ledger (O11.2) ----------------------------------------------------------


#: The lifecycle groups, in the ledger's in-use-first order -- `STATUS` itself is the
#: engine's own vocabulary; the gloss after each dash names what the runbook means by it.
_LEDGER_GROUPS: tuple[tuple[str, str], ...] = (
    ("live", "live -- IN USE (trading)"),
    ("paper", "paper -- proving (forward, no orders to the venue)"),
    ("candidate", "candidate -- TRIED, NOT USED"),
    ("disabled", "disabled -- OUT (terminal)"),
)


@dataclass(frozen=True)
class LedgerRule:
    """One rules-table row as the ledger sees it AT ENTRY: the RECORDED lifecycle facts
    only -- status, kind, product, the row's own stamps, its params -- plus (paper rows)
    the insights service's kind-wide gate distance over the recorded paper trades.
    Building one runs NO backtest; the backtest verdict is `RuleVerdict`, computed only by
    the explicit per-rule re-compute (`compute_rule_verdict`)."""

    rule_id: int
    kind: str
    status: str
    product_id: str | None
    promoted_at: int | None
    demoted_at: int | None
    paper_gate_lines: tuple[str, ...]
    params: dict[str, Any]


@dataclass(frozen=True)
class RuleVerdict:
    """One rule's EXPLICITLY-computed backtest verdict (the Enter-gated re-compute): the
    fee-honest backtest summary (`stats_line`) and the promotion gate's own reasons --
    exactly what `rules backtest` measures and `rules promote`'s gate answers -- or the
    honest absence ("no backtest on record") / honest failure (a backtest that raised).
    Held in the ledger's state per rule, never recomputed by a repaint."""

    stats_line: str | None
    reason_lines: tuple[str, ...]


def _paper_gate_lines(repo: Repository, config: Config, row: dict[str, Any]) -> list[str]:
    """The insights service's kind-wide paper-gate distance for a `paper` row -- its own
    reading of the recorded paper track record (by kind, exactly as `keel insights summary`
    reads it), with the kind-wide semantics disclosed ON the rendered line. Recorded
    orders only: no backtest is involved, so this is cheap enough for the entry render."""
    from keel.commands.insights import build_rule_track_record
    from keel.strategy import promotion as promotion_mod
    from keel.strategy.paper import track_record

    promo_cfg = promotion_mod.PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )
    record = build_rule_track_record(row, track_record(repo, row["kind"]), promo_cfg)
    lines: list[str] = []
    if record.gate is not None:
        verdict = "PASSING" if record.gate.passing else "blocked"
        lines.append(
            f"paper gate ({record.gate.promotion_class} floor): {verdict} -- "
            f"trades_remaining={record.gate.trades_remaining} "
            f"(n>={record.gate.min_trades}, win_rate>={record.gate.min_win_rate}, "
            f"rr>={record.gate.min_rr}, expectancy>{record.gate.min_expectancy})"
        )
        lines.extend(record.gate.blocking_reasons)
    else:
        # `render_summary`'s own wording for a row whose kind the registry no longer
        # knows -- the insights service's honest unavailable, not a TUI line.
        lines.append("paper gate: unavailable (rule kind not recognized -- stale row?)")
    lines.append(
        "distance is kind-wide (insights' own semantics): the floor reading pools every "
        "paper trade this KIND recorded, on any product -- not this row's trades alone"
    )
    return lines


def _ledger_rule(repo: Repository, config: Config, row: dict[str, Any]) -> LedgerRule:
    """The ledger's CHEAP read of ONE rule row: the recorded lifecycle facts, plus the
    paper-gate distance for `paper` rows. No backtest runs here -- the entry render must
    stay cheap on any deployment size (see the module docstring for what the entry-time
    re-backtest cost). One poisoned row degrades to its own error line, never a crash."""
    params = row["params"] or {}
    paper_gate: tuple[str, ...] = ()
    if row["status"] == "paper":
        try:
            paper_gate = tuple(_paper_gate_lines(repo, config, row))
        except Exception as exc:  # noqa: BLE001 -- one row's read must cost only that row
            paper_gate = (
                f"paper gate unreadable for this row: {exc!r} -- see `keel insights "
                "summary` for the kind's own reading",
            )
    return LedgerRule(
        rule_id=row["id"],
        kind=row["kind"],
        status=row["status"],
        product_id=params.get("product_id"),
        promoted_at=row.get("promoted_at"),
        demoted_at=row.get("demoted_at"),
        paper_gate_lines=paper_gate,
        params=params,
    )


def compute_rule_verdict(
    repo: Repository, config: Config, entry: LedgerRule
) -> RuleVerdict:
    """THE explicit per-rule re-compute (Enter-gated in the console): the full-window
    backtest over the repo's cached candles -- DELEGATED to the `rules` service's compute
    core (`resolve_rule_backtest` + `backtest_resolved`, the same read/build/backtest
    `keel rules backtest` runs; this view once re-derived the granularity loop and the
    input assembly here, a drifting twin of the service's) -- judged by the promotion gate
    exactly as `rules promote` judges it (`can_promote`, no PBO session -- so the G4 axis
    renders as its own honest NOT RUN reason). This is REAL WORK -- minutes on long series
    -- which is precisely why nothing calls it from a render path. A backtest that raises
    (stale params, e.g. a quoted float a pre-guard row still carries) is an honest per-row
    error line, following `build_rule_track_record`'s graceful-degradation precedent."""
    from keel.commands.rules import _describe_fee
    from keel.strategy import promotion as promotion_mod

    try:
        resolved = resolve_rule_backtest(repo, config, entry.rule_id)
    except ValueError:
        return RuleVerdict(
            stats_line=None,
            reason_lines=(
                f"rule kind {entry.kind!r} is no longer in RULE_REGISTRY -- a stale row "
                "the engine cannot rebuild (see `keel rules list`)",
            ),
        )
    except RulesRefused as exc:
        return RuleVerdict(
            stats_line=None,
            reason_lines=(
                f"no backtest on record -- the backtest service refused this row: {exc} "
                f"(see `keel rules backtest {entry.rule_id}` for the CLI's own refusal)",
            ),
        )

    if not resolved.candles:
        return RuleVerdict(
            stats_line=None,
            reason_lines=(
                f"no backtest on record -- the repo holds no cached "
                f"{resolved.granularity.value} candles for "
                f"{entry.product_id or 'this product'} to backtest against",
            ),
        )

    promo_cfg = promotion_mod.PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )
    gate = promotion_mod.pbo_gate_from_config(config.research)
    try:
        stats = backtest_resolved(resolved)
    except Exception as exc:  # noqa: BLE001 -- the row's own error, never the view's
        return RuleVerdict(
            stats_line=None,
            reason_lines=(
                f"the backtest itself failed on this row's stored params: {exc!r} -- "
                f"see `keel rules backtest {entry.rule_id}` for the same failure "
                "(a stale param shape the add service now refuses)",
            ),
        )
    decision = promotion_mod.can_promote(stats, promo_cfg, None, gate)
    stats_line = (
        f"backtest: n_trades={stats.n_trades} win_rate={stats.win_rate:.2%} "
        f"expectancy={stats.expectancy} profit_factor={stats.profit_factor} "
        f"{_describe_fee(resolved.fee_pct, resolved.fee_source)}"
    )
    return RuleVerdict(stats_line=stats_line, reason_lines=tuple(decision.reasons))


def build_strategy_ledger(
    repo: Repository, config: Config, now_ts: int
) -> list[LedgerRule]:
    """Every rules-table row as a `LedgerRule`, grouped in-use-first (live, paper,
    candidate, disabled; by id within a group) -- the tried-vs-used ledger's data. CHEAP by
    contract: recorded rows plus the bounded paper-track read, ZERO backtests (pinned by
    spy in the tests -- the entry-time re-backtest this view shipped with cost minutes per
    rule on long series). The console builds this on entering the view and holds it,
    together with any re-computed verdicts; re-entering rebuilds both, which is the
    held-verdict invalidation: a rules-table write between visits can never leak a stale
    verdict onto a changed row."""
    del now_ts  # the ledger renders the RECORDS' stamps, never "now"
    rows = repo.get_rules()
    grouped: list[dict[str, Any]] = []
    for _status, _label in _LEDGER_GROUPS:
        grouped.extend(row for row in rows if row["status"] == _status)
    known = {status for status, _ in _LEDGER_GROUPS}
    grouped.extend(row for row in rows if row["status"] not in known)
    return [_ledger_rule(repo, config, row) for row in grouped]


def _date(ts: int | None) -> str:
    """Local-time `YYYY-MM-DD` for a recorded stamp -- the recorded DATE, not a raw int.
    A missing stamp (a row that never left the status it was inserted at) renders `?`
    rather than guessing an epoch."""
    import time

    if ts is None:
        return "?"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def build_ledger_lines(
    ledger: list[LedgerRule],
    *,
    cursor: int = 0,
    verdicts: dict[int, RuleVerdict] | None = None,
) -> list[ScreenLine]:
    """The ledger screen: every lifecycle group with its rows, each row's RECORDED context
    and any HELD re-computed verdict beneath it, exactly one cursor-marked rule row. PURE --
    no verdict is computed here; `verdicts` is the held state the loop owns."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- rules / tried-vs-used ledger", "heading"),
    ]
    for wrapped in _wrap(
        "which strategies are in use, which were tried -- recorded state, freshly read; "
        "verdicts are per-rule re-computes (Enter), never entry-time backtests",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    if not ledger:
        lines.append(
            ScreenLine("no rules found -- `add a strategy` or `keel rules seed`.", "normal")
        )
        lines.append(_blank())
        lines.append(ScreenLine("Press q, Esc or m to return to the Rules menu.", "muted"))
        return lines
    cursor = max(0, min(cursor, len(ledger) - 1))
    by_status: dict[str, list[LedgerRule]] = {}
    for entry in ledger:
        by_status.setdefault(entry.status, []).append(entry)
    ordered = [
        (status, label) for status, label in _LEDGER_GROUPS if status in by_status
    ] + [(status, status) for status in by_status if status not in dict(_LEDGER_GROUPS)]
    index = 0
    for status, label in ordered:
        lines.append(ScreenLine(label.upper(), "heading"))
        for entry in by_status[status]:
            marker = ">" if index == cursor else " "
            product = entry.product_id or "?"
            lines.append(
                ScreenLine(
                    f"{marker} [{entry.rule_id}] {entry.kind} {product}",
                    "heading" if index == cursor else "normal",
                )
            )
            index += 1
            if status == "paper":
                for reason in entry.paper_gate_lines:
                    for wrapped in _wrap(f"- {reason}", indent="      "):
                        lines.append(ScreenLine(wrapped, "warn"))
            verdict = (verdicts or {}).get(entry.rule_id)
            if verdict is not None:
                for reason in verdict.reason_lines:
                    for wrapped in _wrap(f"- {reason}", indent="      "):
                        lines.append(ScreenLine(wrapped, "warn"))
                if verdict.stats_line is not None:
                    for wrapped in _wrap(verdict.stats_line, indent="      "):
                        lines.append(ScreenLine(wrapped, "muted"))
            else:
                for wrapped in _wrap(
                    "- no verdict computed in this view -- Enter opens the rule, where "
                    "Enter again re-computes it (a full-window backtest; minutes on "
                    "long series)",
                    indent="      ",
                ):
                    lines.append(ScreenLine(wrapped, "muted"))
            if status == "live" and entry.promoted_at is not None:
                lines.append(
                    ScreenLine(f"      promoted {_date(entry.promoted_at)} (recorded)", "muted")
                )
            if status == "paper" and entry.promoted_at is not None:
                # `update_rule_status` writes every non-disabled transition into
                # `promoted_at` (repository.py's own column choice), so a paper row's
                # stamp is its demotion stamp when it arrived live->paper -- rendered
                # with wording that names the column, never a false "was promoted".
                for wrapped in _wrap(
                    f"paper since {_date(entry.promoted_at)} -- the column is "
                    "promoted_at; the runbook's documented demotion path (live->paper) "
                    "writes here too",
                    indent="      ",
                ):
                    lines.append(ScreenLine(wrapped, "muted"))
            if status == "disabled":
                if entry.demoted_at is not None:
                    lines.append(
                        ScreenLine(
                            f"      disabled {_date(entry.demoted_at)} (recorded)", "muted"
                        )
                    )
                for wrapped in _wrap(
                    "restore path: `keel rules enable` -- returns the rule at candidate, "
                    "never at the status it held",
                    indent="      ",
                ):
                    lines.append(ScreenLine(wrapped, "muted"))
        lines.append(_blank())
    for wrapped in _wrap(
        "up/k down/j move · Enter the rule's detail (params; Enter there re-computes its "
        "verdict) · q/Esc/m back",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    return lines


def _default_display(help_: ParamHelp) -> str:
    """The param's default as the ADD FORM renders it -- `.value` for a granularity,
    `true/false` for a bool, else the raw default."""
    default = help_.default
    if isinstance(default, Granularity):
        return default.value
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, tuple):
        return json.dumps(list(default))
    return str(default)


#: Sentinel for "the row does not carry this param" (a row written before its kind
#: grew a field) -- rendered as the kind's own default, named as such.
_DEFAULT_MISS = object()


def build_ledger_detail_lines(
    entry: LedgerRule, *, verdict: RuleVerdict | None = None
) -> list[ScreenLine]:
    """One rule's detail: its lifecycle status, EVERY param rendered through
    `describe_params` (the O8 per-field help, single-sourced from the class), the paper
    gate's recorded distance again, and -- without a held `verdict` -- the ARMED
    re-compute: the warning renders BEFORE any Enter can start the work, exactly the
    simulate view's confirm step. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine(
            f"keel console -- rules / rule {entry.rule_id} ({entry.kind})", "heading"
        ),
        ScreenLine(
            f"status={entry.status} product={entry.product_id or '?'}", "normal"
        ),
        _blank(),
    ]
    try:
        params_help = describe_params(entry.kind)
    except ValueError:
        params_help = {}
    for name, help_ in params_help.items():
        value = entry.params.get(name, _DEFAULT_MISS)
        shown = (
            "(default)" if value is _DEFAULT_MISS else json.dumps(value, default=str)
        )
        head = f"  {name} = {shown}"
        if value is _DEFAULT_MISS:
            head += f"  [{_default_display(help_)}]"
        lines.append(ScreenLine(head, "normal"))
        for wrapped in _wrap(f"{help_.doc} ({help_.type_name})", indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))
    if not params_help:
        lines.append(
            ScreenLine(f"  (params: {json.dumps(entry.params, default=str)})", "normal")
        )
    lines.append(_blank())
    if entry.status == "paper":
        lines.append(
            ScreenLine("the recorded paper-gate distance (kind-wide):", "heading")
        )
        for reason in entry.paper_gate_lines:
            for wrapped in _wrap(f"- {reason}", indent="      "):
                lines.append(ScreenLine(wrapped, "warn"))
        lines.append(_blank())
    if verdict is not None:
        lines.append(
            ScreenLine("this session's re-computed verdict (the machine's own):", "heading")
        )
        for reason in verdict.reason_lines:
            for wrapped in _wrap(f"- {reason}", indent="      "):
                lines.append(ScreenLine(wrapped, "warn"))
        if verdict.stats_line is not None:
            for wrapped in _wrap(verdict.stats_line, indent="      "):
                lines.append(ScreenLine(wrapped, "muted"))
    else:
        lines.append(
            ScreenLine("re-compute this rule's verdict -- ARMED, nothing has run", "heading")
        )
        for wrapped in _wrap(
            "Enter runs the FULL-WINDOW backtest over the repo's cached candles and "
            "judges it through the promotion gate -- the same numbers `keel rules "
            f"backtest {entry.rule_id}` prints and `rules promote` refuses on.",
            indent="      ",
        ):
            lines.append(ScreenLine(wrapped, "normal"))
        for wrapped in _wrap(
            "WARNING: this is real work -- on long series it can take MINUTES (one "
            "rsi_meanrev rule over 5y of hourly candles measured ~7.5 minutes); the "
            "screen freezes while it runs, exactly like simulate/fetch, and the result "
            "is held here when it ends. Esc or q returns without running anything.",
            indent="      ",
        ):
            lines.append(ScreenLine(wrapped, "warn"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "Enter re-computes the verdict · q/Esc/m back to the ledger", "muted"
        )
    )
    return lines


# -- simulate (O11.1): the ARMED view, the run, the results ----------------------------------------


@dataclass(frozen=True)
class SimulatePlan:
    """What the simulate pass WILL do, shown BEFORE any of it runs (the confirm step):
    the deployment's db, the CLI's own defaults, the products the allowlist resolves to,
    and the report path `run_simulation` will write."""

    db_path: str
    years: int
    monthly_contribution: Decimal
    products: tuple[str, ...]
    report_path: Path


def simulate_plan(
    config: Config,
    db_path: str,
    *,
    now_ts: int,
    years: int = 5,
    contribution: Decimal = Decimal("500"),
) -> SimulatePlan:
    """The plan for a console simulate run -- `keel simulate`'s own defaults (5y, $500/month,
    the allowlist's products in the settlement currency) and the report path the service
    will write (`default_report_path`, the same directory the Research readers list)."""
    from keel.commands._products import _default_sim_products

    return SimulatePlan(
        db_path=db_path,
        years=years,
        monthly_contribution=contribution,
        products=tuple(_default_sim_products(config)),
        report_path=default_report_path(now_ts),
    )


def build_simulate_armed_lines(plan: SimulatePlan) -> list[ScreenLine]:
    """The simulate view's ARMED state: NOTHING has run, and the screen says exactly what
    Enter will do -- the target report path FIRST (O11.1's confirm step), the products, and
    the fetch/write the pass makes. PURE."""
    lines = [
        ScreenLine("keel console -- rules / simulate", "heading"),
        _blank(),
        ScreenLine("ARMED -- nothing has run yet.", "normal"),
        _blank(),
        ScreenLine(
            f"Enter runs the simulate pass over THIS deployment ({plan.db_path}):",
            "normal",
        ),
    ]
    for wrapped in _wrap(
        f"products {', '.join(plan.products)} · {plan.years}y window · "
        f"{plan.monthly_contribution}/month contribution",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "normal"))
    for wrapped in _wrap(
        "it FETCHES candle history for those products when the cache does not already "
        "cover the window (the same reads `keel fetch` makes) -- read-only w.r.t. money: "
        "no orders, no rails.",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "normal"))
    lines.append(ScreenLine("the report will be WRITTEN at:", "normal"))
    for wrapped in _wrap(str(plan.report_path), indent="      "):
        lines.append(ScreenLine(wrapped, "normal"))
    for wrapped in _wrap(
        "the run can take seconds to minutes; the screen freezes while it runs (exactly "
        "like the CLI) and holds the result here when it ends. Enter again re-runs.",
        indent="      ",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("Press q or Esc to return to the Rules menu.", "muted"))
    return lines


def run_simulate(
    repo: Repository,
    config: Config,
    plan: SimulatePlan,
    *,
    now_ts: int,
    build_client: Callable[[], Any] | None,
    run_fn: Callable[..., SimulationOutcome] = run_simulation,
    progress: list[str] | None = None,
) -> SimulationOutcome:
    """THE simulate run, dispatched: `run_simulation` itself over the active profile's
    repo/config with the CLI's own defaults, its progress lines collected into `progress`
    (the CLI streamed them; the console shows them at the head of the results). `build_client`
    is the CLI's own seam (`None` = `--no-fetch`); `run_fn` is injectable so the loop's
    confirm-gate tests can spy the call without computing anything."""
    sink = progress.append if progress is not None else (lambda _message: None)
    return run_fn(
        repo,
        config,
        build_client,
        db_path=plan.db_path,
        products=list(plan.products),
        years=plan.years,
        monthly_contribution=plan.monthly_contribution,
        now_ts=now_ts,
        # The path the ARMED screen pre-showed is the path the run writes: pinned here so
        # a run crossing UTC midnight cannot write a different filename than the one the
        # operator confirmed (`default_report_path(now_ts)` inside the service would
        # otherwise re-derive the date from the run-time `now_ts`).
        out_path=plan.report_path,
        echo=sink,
    )


def _verdict_style(status: str) -> str:
    """The verdict's own severity: GO-LIVE is the pass, TRAIN-MORE is the deliberate
    not-yet (a warn, not an alert -- the report's own vocabulary), anything else is
    fail-loud."""
    if status == "GO-LIVE":
        return "ok"
    if status == "TRAIN-MORE":
        return "warn"
    return "alert"


def build_simulate_result_lines(
    outcome: SimulationOutcome, progress: tuple[str, ...] = ()
) -> list[ScreenLine]:
    """The simulate RESULTS: the service's own verdict headline and failing gates, the
    progress the CLI would have streamed, and then the report it wrote -- VERBATIM, the
    net-negative honesty included (the report states its own caveats; this screen renders
    them, it does not summarize them). Long report lines WRAP to the 80-column budget
    rather than clipping. PURE."""
    lines = [
        ScreenLine("keel console -- rules / simulate results", "heading"),
        ScreenLine(f"verdict: {outcome.verdict_status}", _verdict_style(outcome.verdict_status)),
    ]
    # The paths wrap rather than clip: the tail of a report path is exactly the part that
    # identifies the file (and this screen's own footer carries them pinned, unwrapped,
    # on their own rows).
    for wrapped in _wrap(f"report: {outcome.report_path}", indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    if outcome.verdict_reasons:
        lines.append(ScreenLine("failing gates:", "normal"))
        for reason in outcome.verdict_reasons:
            for wrapped in _wrap(f"- {reason}", indent="      "):
                lines.append(ScreenLine(wrapped, "warn"))
    if outcome.artifact_path is not None:
        for wrapped in _wrap(f"artifact: {outcome.artifact_path}", indent=""):
            lines.append(ScreenLine(wrapped, "muted"))
    if progress:
        lines.append(_blank())
        lines.append(ScreenLine("run progress (what the CLI streamed):", "muted"))
        for line in progress:
            for wrapped in _wrap(line, indent="      "):
                lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    lines.append(ScreenLine("the report written by the run (verbatim):", "heading"))
    for line in outcome.report_markdown.splitlines():
        if not line.strip():
            lines.append(_blank())
            continue
        for wrapped in textwrap.wrap(line, width=_WIDTH) or [""]:
            lines.append(ScreenLine(wrapped, "normal"))
    lines.append(_blank())
    for wrapped in _wrap(
        "Enter re-runs the pass · q/Esc/m back to the Rules menu (the report stays on "
        "disk -- reachable from Research / promotion reports)",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    return lines


def simulate_verdict_footer(outcome: SimulationOutcome) -> list[ScreenLine]:
    """The simulate results' PINNED footer: the verdict and the report path, reserved off
    the window before the body is sliced (`compliance_console.pinned_frame`) so no scroll
    offset can hide what the run concluded or where it was written. PURE -- the verdict
    and the path each get their own row (the path wraps if it must), so neither
    load-bearing fact can lose its tail to the 80-column clip."""
    footer = [
        ScreenLine(
            f"verdict: {outcome.verdict_status}", _verdict_style(outcome.verdict_status)
        )
    ]
    footer.extend(
        ScreenLine(wrapped, "muted")
        for wrapped in _wrap(f"report: {outcome.report_path}", indent="")
    )
    return footer


# -- the forms (O11.3 add / O11.4 retry + restore) ---------------------------------------------


def _service_result(out: list[str], err: list[str]) -> str:
    """A form's result from the service's collected lines: its stdout lines on success,
    its already-`Error:`-prefixed stderr lines on a refusal -- the service's own words,
    never a re-wording."""
    if err:
        return "\n".join(err)
    return "\n".join(out)


def _ask(prompt_fn: PromptFn, question: str) -> str:
    return prompt_fn(question).strip()


def run_add_form(
    repo: Repository, config: Config, prompt_fn: PromptFn, now_ts: int
) -> str:
    """The `rules add` flow in-console: kind (from `RULE_REGISTRY`), product, then ONE
    PROMPT PER PARAM with its O8 help rendered from `describe_params` -- doc, type and
    default; an empty answer keeps the kind's default exactly as omitting the key from
    `--params` does. Dispatches to `add_rule_row` (the same service the CLI command
    calls), so the row lands as `candidate` with the same validations and the same
    messages."""
    kinds = sorted(agent.RULE_REGISTRY)
    kind = _ask(prompt_fn, f"rule kind -- one of: {', '.join(kinds)} (empty cancels)")
    if not kind:
        return "add cancelled -- nothing written"
    try:
        params_help = describe_params(kind)
    except ValueError as exc:
        return f"Error: {exc}"
    product = _ask(prompt_fn, "product id (e.g. BTC-USD) -- empty cancels")
    if not product:
        return "add cancelled -- nothing written"

    supplied: dict[str, Any] = {}
    for name, help_ in params_help.items():
        choices = f", one of {list(help_.choices)}" if help_.choices else ""
        quoted = " -- a QUOTED value is correct here" if help_.quotable else ""
        answer = prompt_fn(
            f"{name} ({help_.type_name}, default {_default_display(help_)}{choices})"
            f"{quoted} -- {help_.doc}\n"
            f"  [empty keeps the default]"
        ).strip()
        if not answer:
            continue
        try:
            supplied[name] = json.loads(answer)
        except json.JSONDecodeError as exc:
            return (
                f"Error: {name}: not valid JSON ({exc}) -- values are typed as the CLI's "
                "--params JSON types them (numbers unquoted, Decimal params quoted)"
            )

    params_json = json.dumps(supplied) if supplied else None
    out: list[str] = []
    err: list[str] = []
    try:
        outcome = add_rule_row(
            repo,
            config,
            kind=kind,
            product=product,
            params_json=params_json,
            now_ts=now_ts,
            echo=out.append,
            echo_err=err.append,
        )
    except RulesUsageError as exc:
        return f"Error: {exc}"
    except RulesRefused:
        return _service_result(out, err)
    del outcome
    return _service_result(out, err)


def run_retry_form(
    repo: Repository,
    config: Config,
    prompt_fn: PromptFn,
    now_ts: int,
    *,
    typed_force_fn: Callable[[int, str, str, str], bool] | None = None,
) -> str:
    """The retry flow (O11.4): re-run the backtest (always -- its line is the result's
    first content), then re-attempt the promotion through `attempt_promotion` -- only on
    an explicit y/N (the O3 promote confirmation), with the PBO session named or honestly
    absent (the gate's own NOT RUN reason renders when it is). `--force` is offered after
    a declined-or-refused promote and runs ONLY behind `typed_force_fn` -- the console's
    typed gate (`clis_typed_promote_force_gate`, the default: console-added ceremony over
    the CLI's bare `--force` flag, quoting its warning over the shared
    typed-confirmation gate), never pre-filled, failing closed: a wrong phrase writes
    nothing."""
    if typed_force_fn is None:
        typed_force_fn = clis_typed_promote_force_gate
    del now_ts  # the services stamp their own times
    rule_id_raw = _ask(prompt_fn, "rule id to retry (see the ledger) -- empty cancels")
    if not rule_id_raw:
        return "retry cancelled -- nothing done"
    try:
        rule_id = int(rule_id_raw)
    except ValueError:
        return f"Error: rule id must be a number, got {rule_id_raw!r}"

    out: list[str] = []
    err: list[str] = []
    try:
        _outcome, _stats = run_rule_backtest(
            repo, config, rule_id, echo=out.append, echo_err=err.append
        )
    except RulesRefused:
        return _service_result(out, err)

    rows = {row["id"]: row for row in repo.get_rules()}
    row = rows.get(rule_id)

    answer = _ask(
        prompt_fn, "attempt promotion through the gate now? (y/N)"
    ).lower()
    promoted = False
    if answer.startswith("y"):
        session = _ask(
            prompt_fn,
            "pbo-session label for the G4 check (empty = NOT RUN -- the gate will "
            "refuse; `keel trials list` shows the labels)",
        )
        try:
            outcome = attempt_promotion(
                repo,
                config,
                rule_id,
                pbo_session=session or None,
                echo=out.append,
                echo_err=err.append,
            )
        except RulesRefused:
            return _service_result(out, err)
        promoted = row is not None and outcome.new_status != row["status"]

    if not promoted:
        force = _ask(
            prompt_fn,
            "force-promote past the gate? the documented bypass (--force) -- it needs "
            "the TYPED phrase (y/N)",
        ).lower()
        if force.startswith("y"):
            target = None
            if row is not None:
                from keel.strategy import promotion as promotion_mod

                target = promotion_mod.next_status(row["status"])
            if target is None or row is None:
                out.append(
                    f"rule {rule_id}: nothing to promote (already at a terminal status)"
                )
            elif not typed_force_fn(rule_id, row["kind"], row["status"], target):
                out.append(
                    "retry cancelled -- the typed confirmation was not given; "
                    "nothing promoted"
                )
            else:
                try:
                    attempt_promotion(
                        repo,
                        config,
                        rule_id,
                        force=True,
                        echo=out.append,
                        echo_err=err.append,
                    )
                except RulesRefused:
                    return _service_result(out, err)

    return _service_result(out, err)


def clis_typed_promote_force_gate(
    rule_id: int, kind: str, from_status: str, target: str
) -> bool:
    """The typed gate for the console's `--force` (O3): the CLI's OWN
    `_require_interactive_confirmation`, with action wording that quotes the CLI's force
    warning verbatim in substance -- pinned by test so the two front-ends can never drift
    into two ceremonies for one bypass. Fails CLOSED: a wrong phrase, a Ctrl-C, any
    exception answers False and the status does not move."""
    from keel.commands._common import _require_interactive_confirmation

    try:
        _require_interactive_confirmation(
            f"force-promote rule {rule_id} ({kind}): {from_status} -> {target}, "
            "BYPASSING the backtest/promotion gate",
            "This is the CLI's `rules promote --force` bypass, for a deliberate, un-gated "
            "paper-forward start (e.g. a low-frequency trend-follower whose backtest can "
            "never reach the min_trades floor). It writes a WARNING-level audit record -- "
            "confirm this is intentional and monitor accordingly.",
        )
        return True
    except Exception:
        return False


def _lifecycle_form(
    repo: Repository,
    prompt_fn: PromptFn,
    verb: str,
    service: Callable[..., Any],
) -> str:
    """The shared shape of the one-rule-id lifecycle forms (enable/disable/demote): ask
    the id, dispatch to the extracted service with collecting sinks, render the service's
    own lines -- its refusal lines are already `Error:`-prefixed."""
    raw = _ask(prompt_fn, "rule id (see the ledger) -- empty cancels")
    if not raw:
        return f"{verb} cancelled -- nothing changed"
    try:
        rule_id = int(raw)
    except ValueError:
        return f"Error: rule id must be a number, got {raw!r}"
    out: list[str] = []
    err: list[str] = []
    try:
        service(repo, rule_id, echo=out.append, echo_err=err.append)
    except RulesRefused:
        pass
    return _service_result(out, err)


def run_enable_form(
    repo: Repository, config: Config, prompt_fn: PromptFn, now_ts: int
) -> str:
    """`rules enable` as a form -- the DOCUMENTED restore path for a disabled rule, back
    at `candidate` (never at the status it held: `disable` records no prior status)."""
    del config, now_ts
    return _lifecycle_form(repo, prompt_fn, "enable", apply_rule_enable)


def run_disable_form(
    repo: Repository, config: Config, prompt_fn: PromptFn, now_ts: int
) -> str:
    del config, now_ts
    return _lifecycle_form(repo, prompt_fn, "disable", apply_rule_disable)


def run_demote_form(
    repo: Repository, config: Config, prompt_fn: PromptFn, now_ts: int
) -> str:
    del config, now_ts
    return _lifecycle_form(repo, prompt_fn, "demote", apply_rule_demote)
