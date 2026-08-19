"""The Compliance menu -- the console's third slice (issue #389 C3; PRD O6/O10 and §3's tree).

Everything here is DISPATCH, never behavior (PRD O2, the same discipline `console.py` keeps):
every view renders a C1 service's own report through its own renderer, every form collects
fields at the terminal and calls the SAME service/repository function the CLI command calls,
and the two browsers (scout results, Shariah in force) read through service reads added for
the purpose (`admission.list_shortlists`, `assets.gather_attestations_in_force`) -- never a
TUI-side re-implementation. No sizing, screening, gating or reporting math lives here.

Three surfaces, all pure (or injected-I/O) and unit-testable without curses:

* **The sub-menu model** -- `COMPLIANCE_MENU`, PRD §3's Compliance branch. `kind` is a closed
  vocabulary: `"view"` (a service report overlay; holdings/discover are network-gated and open
  ARMED), `"form"` (a record-write), `"scout"` (the proposals browser). `typed` marks the two
  entries the PRD marks "(typed)": `attest` and `withdrawals attest`.
* **The forms** -- `run_form` is the loop's single dispatch seam; the `run_*` functions beneath
  it collect fields through an injected `prompt_fn` and dispatch to the repository call the CLI
  makes (`upsert_asset_attestation`, `upsert_instrument_attestation`, `upsert_screen_exception`,
  `delete_screen_exception`, `set_state`) or to the extracted subscription services
  (`subscription.apply_subscription_attest`/`apply_subscription_set` -- one implementation, two
  front-ends since C3). Each returns the CLI's own confirmation line, so every write shows what
  it did.
* **The typed contract (O3)** -- `withdrawals attest --enabled` keeps the CLI's OWN typed gate,
  verbatim (`clis_typed_withdrawals_gate` wraps `_require_interactive_confirmation` with the
  CLI's exact action wording, fails closed); `attest` (typed per the PRD tree) ends with
  `typed_asset_confirmation`: the operator types the ASSET CODE back -- never pre-filled, never
  piped, and a wrong phrase means not a single row is written. The TUI adds no gate the CLI has
  (`attest-instrument`, exempt/unexempt, subscription writes match their CLI ungated shapes).

The scout-results handler (O6) is proposer-never-decider exactly like the skill that writes the
shortlists: the browser lists, renders and OFFERS the attest step; it never auto-attests, and
the attest step is the same typed form the Compliance menu's own entry runs.

The "Shariah in force" browser (O10) renders what the engine enforces for the ACTIVE profile
from records alone -- attestations in force, exemptions, rail 17's live state -- plus the
fiqh-derived constraints, each a VERBATIM quote from `docs/fiqh-basis.md` with a citation that
resolves to a real section heading of that document. Nothing on that screen is a TUI-authored
fiqh summary: the vocabulary section quotes the document too, and a term the document does not
state (gharar) is rendered as not-stated-there rather than defined -- the document's own
honesty rule, inherited. The two standing honesty lines are pinned to the document's wording
and to a FIXED footer of the view (`shariah_honesty_lines` + `pinned_frame`), painted outside
the scroll so they stay on screen at every scroll offset -- not merely present at the body's
end, one viewport below the fold.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from keel.commands.admission import (
    ProposeView,
    ScoutFile,
    list_shortlists,
    render_propose_view,
    render_screen_report,
)
from keel.commands.assets import (
    VENUE,
    AttestationsInForce,
    render_discover,
    render_holdings,
)
from keel.commands.subscription import (
    apply_subscription_attest,
    apply_subscription_set,
)
from keel.commands.tui import (
    ScreenLine,
    _admission_line_style,
    _blank,
    _message_style,
    _visible_slice,
)
from keel.commands.withdrawals import WITHDRAWALS_ATTEST_ACTION, WITHDRAWALS_ATTEST_DETAIL
from keel.compliance.screen import KNOWN_BACKINGS, KNOWN_WRAPPERS, WAIVABLE_CRITERIA
from keel.execution.executor import WITHDRAWAL_ATTESTATION_TTL_SEC

if TYPE_CHECKING:
    pass

#: One terminal prompt: injected so every form is unit-testable with a scripted fake, and so
#: the live loop can run the whole form through the curses suspend/restore dance.
PromptFn = Callable[[str], str]


class FormInputError(ValueError):
    """A form field that cannot be accepted (an unknown backing, a blank rationale). The
    `run_*` functions render this as the form's `Error: ...` result line -- the same shape
    the CLI's own option validation takes, never an exception past the form."""


# -- the sub-menu model (PRD §3's Compliance branch) ----------------------------------------------


@dataclass(frozen=True)
class ComplianceEntry:
    """One entry of the Compliance sub-menu. `kind` is the closed dispatch vocabulary:
    `"view"` renders a service report (the network-touching views open ARMED, gated behind
    Enter like the discover overlay), `"form"` runs a record-write at the terminal, and
    `"scout"` opens the proposals browser. `typed` marks the entries whose write carries a
    typed confirmation (O3 / the PRD tree's own "(typed)" annotations)."""

    ordinal: int
    label: str
    description: str
    kind: str  # "view" | "form" | "scout"
    target: str  # the view kind, the form name, or "" for the scout browser
    typed: bool = False


#: PRD §3's Compliance branch, in tree order. The descriptions are O8's plain-English
#: "what will this do" in miniature, and they name the dispatch honestly: what each entry
#: reads or writes, and where the ceremony is.
COMPLIANCE_MENU: tuple[ComplianceEntry, ...] = (
    ComplianceEntry(
        ordinal=1,
        label="screen",
        description="the allowlist's admission verdicts (offline; the assets screen gate)",
        kind="view",
        target="screen",
    ),
    ComplianceEntry(
        ordinal=2,
        label="propose",
        description="the newest shortlist, screened (offline; the assets propose gate)",
        kind="view",
        target="propose",
    ),
    ComplianceEntry(
        ordinal=3,
        label="attest",
        description="record an asset classification -- typed: type the asset code to confirm",
        kind="form",
        target="attest",
        typed=True,
    ),
    ComplianceEntry(
        ordinal=4,
        label="attest-instrument",
        description="record what CONTRACT a venue listing is (spot admits; the rest refuse)",
        kind="form",
        target="attest-instrument",
    ),
    ComplianceEntry(
        ordinal=5,
        label="exempt",
        description="waive one DATA criterion for one asset, with a documented rationale",
        kind="form",
        target="exempt",
    ),
    ComplianceEntry(
        ordinal=6,
        label="unexempt",
        description="revoke a documented waiver (a de-risking action, always allowed)",
        kind="form",
        target="unexempt",
    ),
    ComplianceEntry(
        ordinal=7,
        label="holdings",
        description="what you hold, as allowlist CANDIDATES (one live balance read)",
        kind="view",
        target="holdings",
    ),
    ComplianceEntry(
        ordinal=8,
        label="discover",
        description="propose candidates from venue metadata (one live product read)",
        kind="view",
        target="discover",
    ),
    ComplianceEntry(
        ordinal=9,
        label="Scout results",
        description="browse the scout's shortlists and admit through the real flow",
        kind="scout",
        target="",
    ),
    ComplianceEntry(
        ordinal=10,
        label="Shariah in force",
        description="what the engine enforces now, from records (read-only)",
        kind="view",
        target="shariah",
    ),
    ComplianceEntry(
        ordinal=11,
        label="subscription show",
        description="every venue's subscription, with the cap actually in force",
        kind="view",
        target="subscription",
    ),
    ComplianceEntry(
        ordinal=12,
        label="subscription attest",
        description="assert a venue's tier (rail 14's allowance)",
        kind="form",
        target="subscription-attest",
    ),
    ComplianceEntry(
        ordinal=13,
        label="subscription set",
        description="hand-set a raw allowance, naming no tier (prefer attest)",
        kind="form",
        target="subscription-set",
    ),
    ComplianceEntry(
        ordinal=14,
        label="withdrawals attest",
        description="rail 17's qabd input -- typed 'yes' when enabling",
        kind="form",
        target="withdrawals-attest",
        typed=True,
    ),
    ComplianceEntry(
        ordinal=15,
        label="purification",
        description="non-compliant income owed to charity (report-only)",
        kind="view",
        target="purification",
    ),
)


def compliance_entry(ordinal: int) -> ComplianceEntry | None:
    """The entry selected by its displayed ordinal, or `None` -- the same one-lookup rule
    `console.menu_entry` keeps, so the rendered ordinals and the shortcut keys cannot drift."""
    for entry in COMPLIANCE_MENU:
        if entry.ordinal == ordinal:
            return entry
    return None


#: This module's screens' contextual help (O8, issue #394 C7) -- the rows the `?`
#: overlay renders, keyed by the live loop's mode names. Plain `(subject, description)`
#: pairs so the text stays HERE with the module that owns the screens;
#: `keel.commands.help_console` is the registry and renderer. The typed actions' rows
#: state the O3 contract explicitly: the prompt cannot be pre-filled.
CONTEXT_HELP: dict[str, tuple[tuple[str, str], ...]] = {
    "compliance": (
        (
            "the screen and propose views",
            "read-only admission verdicts -- what the allowlist's assets passed or "
            "failed, and the newest shortlist screened the same way; both offline",
        ),
        (
            "the attest / attest-instrument / exempt forms",
            "record-write forms run at the TERMINAL (curses suspended): attest records "
            "a human classification, attest-instrument names a listing's contract, "
            "exempt waives one DATA criterion with a documented rationale",
        ),
        (
            "attest and withdrawals attest are TYPED",
            "their confirmation asks you to TYPE the answer (the asset code, or 'yes') "
            "at the terminal -- the prompt cannot be pre-filled, piped or bypassed; "
            "backing out changes nothing",
        ),
        (
            "holdings and discover",
            "the two live reads: holdings screens what you actually hold as candidates, "
            "discover proposes from the venue's own product list. Both open ARMED -- "
            "nothing touches the network until Enter",
        ),
        (
            "Scout results / Shariah in force",
            "the scout browser drives the real propose -> screen -> attest flow; the "
            "shariah view renders what the engine enforces now, from records alone",
        ),
    ),
    "compliance-view": (
        (
            "what this view is",
            "a read-only rendering of a compliance service report -- verdicts, records "
            "or the venue's answer, depending on the entry you opened it from",
        ),
        (
            "ARMED views (holdings, discover)",
            "the network kinds open with nothing run: Enter makes the ONE live read, "
            "and the result is then held until the view closes -- a poll can never fire "
            "a venue call",
        ),
        (
            "q / Esc / m",
            "back to the Compliance menu",
        ),
    ),
    "scout-list": (
        (
            "the shortlists",
            "every proposal file the keel-asset-scout wrote to the configured "
            "proposals directory, newest first; an absent directory is a calm empty "
            "state, never an error",
        ),
        (
            "Enter",
            "screen the selected shortlist through the real admission services -- "
            "read-only; the human attest step is offered, never auto-run",
        ),
    ),
    "scout-view": (
        (
            "the screened shortlist",
            "each candidate with its admission verdict, rendered from the same screen "
            "service the CLI runs -- the proposer proposes, it never decides",
        ),
        (
            "a attest (TYPED)",
            "attesting a selected candidate opens the CLI's own typed gate at the "
            "terminal: you type the asset code yourself -- the prompt cannot be "
            "pre-filled -- and declining changes nothing",
        ),
    ),
}


#: The width every console line must fit: `_paint` clips at the window width and
#: 80-column terminals are this dashboard's stated target -- a clipped entry description
#: tail would be the "what will this do" half of the row (O8), so the menu WRAPS instead.
_MENU_WIDTH = 80


def _entry_rows(entry: ComplianceEntry, cursor: bool) -> list[ScreenLine]:
    """One menu entry within the 80-column budget: the ordinal+label row (carrying the
    `[typed]` marker), with the description on the same row when it fits and on its own
    indented rows when it does not -- the profile menu's guarded-note style, never a
    clipped tail. PURE."""
    marker = ">" if cursor else " "
    head = f"{marker} {entry.ordinal:>2}  {entry.label}"
    if entry.typed:
        head += "  [typed]"
    style = "heading" if cursor else "normal"
    if len(head) + 1 + len(entry.description) <= _MENU_WIDTH:
        return [ScreenLine(f"{head} {entry.description}", style)]
    return [
        ScreenLine(head, style),
        *(
            ScreenLine(wrapped, "muted")
            for wrapped in _wrap(entry.description, width=_MENU_WIDTH - 2, indent="      ")
        ),
    ]


def build_compliance_menu_lines(
    *, cursor: int = 0, message: str | None = None
) -> list[ScreenLine]:
    """The Compliance sub-menu screen: every PRD §3 entry with its description (wrapped to
    the 80-column budget, never clipped), the typed entries marked, exactly one
    cursor-marked row, and the last action's confirmation line (`message`) -- every write
    shows what it did, on the screen it was invoked from. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- compliance", "heading"),
        _blank(),
    ]
    cursor = max(0, min(cursor, len(COMPLIANCE_MENU) - 1))
    for index, entry in enumerate(COMPLIANCE_MENU):
        lines.extend(_entry_rows(entry, cursor=index == cursor))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j move · Enter/Space select · 1-9 jump", "muted",
        )
    )
    lines.append(ScreenLine("q/Esc/m to the Compliance menu", "muted"))
    if message is not None:
        lines.append(_blank())
        # The toast's STYLE follows the message's own semantics (a failure is an alert,
        # a cancellation a warning) -- `_message_style`, the rule every other console
        # toast keeps; a hardcoded "ok" made a green "failed" line.
        lines.append(ScreenLine(message, _message_style(message)))
    return lines


# -- the forms ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AttestFields:
    """The `keel assets attest` fields, exactly the CLI's own options."""

    asset: str
    sector: str
    backing: str
    pays_yield: bool
    source: str
    attested_by: str


@dataclass(frozen=True)
class InstrumentAttestFields:
    """The `keel assets attest-instrument` fields, exactly the CLI's own options."""

    venue: str
    product: str
    wrapper: str
    source: str
    attested_by: str


@dataclass(frozen=True)
class ExemptFields:
    """The `keel assets exempt` fields, exactly the CLI's own options."""

    asset: str
    criterion: str
    rationale: str
    granted_by: str


def _ask(prompt_fn: PromptFn, question: str) -> str:
    return prompt_fn(question).strip()


def typed_asset_confirmation(asset: str, prompt_fn: PromptFn) -> bool:
    """The attest form's typed gate (the PRD tree marks attest "(typed)"): the operator
    types the ASSET CODE back, naming the thing being recorded. Nothing is pre-filled and
    no other phrase accepts -- the question SHOWS the code, the answer must BE it, exactly:
    case-sensitive, with no whitespace tolerance beyond the trailing newline the prompt
    itself appends. The CLI's own typed gate (`_require_interactive_confirmation`) accepts
    exactly `yes` -- not `YES`, not ` yes ` -- and this gate is exactly as strict."""
    answer = prompt_fn(
        f'Type "{asset}" to record this attestation (anything else cancels)'
    ).rstrip("\n")
    return answer == asset


def collect_attest(prompt_fn: PromptFn, *, asset: str | None = None) -> AttestFields | None:
    """Collect the attest fields through `prompt_fn`. `asset` pre-seeds the asset code
    (the scout flow's chosen candidate) -- the judgment fields (sector, backing, source,
    attestor) are ALWAYS asked: the scout's shariah hypothesis is UNVERIFIED by design and
    can never pre-fill the human's classification. `None` = cancelled (empty first answer).
    Raises `FormInputError` for a field the CLI's own Choice validation would refuse."""
    if asset is None:
        asset = _ask(prompt_fn, "asset code (e.g. BTC) -- empty cancels")
        if not asset:
            return None
    asset = asset.upper()
    sector = _ask(prompt_fn, "sector -- the core business line / purpose of the token")
    if not sector:
        raise FormInputError("sector must be a non-empty description")
    backing = _ask(
        prompt_fn, f"backing -- one of: {', '.join(sorted(KNOWN_BACKINGS))} (ayn/dayn/native)"
    ).lower()
    if backing not in KNOWN_BACKINGS:
        raise FormInputError(
            f"backing must be one of: {', '.join(sorted(KNOWN_BACKINGS))}; got {backing!r}"
        )
    pays_yield = _ask(prompt_fn, "does BARE holding pay a yield, without staking/lending? (y/N)")
    pays_yield_bool = pays_yield.lower().startswith("y")
    source = _ask(prompt_fn, "source -- where this was established: URL, standard, ruling")
    if not source:
        raise FormInputError("an unsourced claim is not evidence -- source is required")
    attested_by = _ask(prompt_fn, "attested-by -- who established it")
    if not attested_by:
        raise FormInputError("attested-by is required (the audit trail names who said it)")
    return AttestFields(
        asset=asset,
        sector=sector,
        backing=backing,
        pays_yield=pays_yield_bool,
        source=source,
        attested_by=attested_by,
    )


def apply_attest(repo: Any, fields: AttestFields, now_ts: int) -> str:
    """THE write, the same repository call `keel assets attest` makes with the same
    argument names -- and the same confirmation line."""
    repo.upsert_asset_attestation(
        asset=fields.asset,
        sector=fields.sector,
        backing=fields.backing,
        pays_yield=fields.pays_yield,
        source=fields.source,
        attested_by=fields.attested_by,
        attested_at=now_ts,
    )
    return (
        f"attested {fields.asset}: sector={fields.sector} backing={fields.backing} "
        f"pays_yield={fields.pays_yield}"
    )


def run_attest_form(
    repo: Any, prompt_fn: PromptFn, now_ts: int, *, asset: str | None = None
) -> str:
    """Collect -> typed-confirm -> write. The typed gate is BETWEEN the fields and the
    repository: a wrong phrase returns the cancellation line and writes nothing."""
    try:
        fields = collect_attest(prompt_fn, asset=asset)
    except FormInputError as exc:
        return f"Error: {exc}"
    if fields is None:
        return "attest cancelled -- nothing recorded"
    if not typed_asset_confirmation(fields.asset, prompt_fn):
        return (
            f"attest cancelled -- the typed confirmation did not name {fields.asset}; "
            "nothing recorded"
        )
    return apply_attest(repo, fields, now_ts)


def collect_instrument_attest(prompt_fn: PromptFn) -> InstrumentAttestFields | None:
    """Collect the attest-instrument fields through `prompt_fn`. `None` = cancelled (empty
    product). Raises `FormInputError` for a field the CLI's own Choice validation would
    refuse -- the same convention `collect_attest` keeps: the COLLECT step raises, the
    `run_*` wrapper renders (one error shape, not two)."""
    venue = _ask(prompt_fn, f"venue (empty = {VENUE}, the default)")
    if not venue:
        venue = VENUE
    product = _ask(prompt_fn, "venue product id (e.g. BTC-USD) -- empty cancels")
    if not product:
        return None
    product = product.upper()  # matches the uppercase ids the screen looks up by
    wrapper = _ask(
        prompt_fn, f"wrapper -- one of: {', '.join(sorted(KNOWN_WRAPPERS))} (only spot admits)"
    ).lower()
    if wrapper not in KNOWN_WRAPPERS:
        raise FormInputError(
            f"wrapper must be one of: {', '.join(sorted(KNOWN_WRAPPERS))}; got {wrapper!r}"
        )
    source = _ask(prompt_fn, "source -- the venue's contract spec, its API docs, a filing")
    if not source:
        raise FormInputError("an unsourced claim is not evidence -- source is required")
    attested_by = _ask(prompt_fn, "attested-by -- who established it")
    if not attested_by:
        raise FormInputError("attested-by is required")
    return InstrumentAttestFields(
        venue=venue,
        product=product,
        wrapper=wrapper,
        source=source,
        attested_by=attested_by,
    )


def run_instrument_attest_form(repo: Any, prompt_fn: PromptFn, now_ts: int) -> str:
    """`keel assets attest-instrument` as a form. Not typed (the CLI's own gate is none);
    the wrapper vocabulary is enforced exactly as the CLI's Choice enforces it, as a
    `FormInputError` from the collect step rendered here -- `run_attest_form`'s own
    convention, never a second inline error-string shape."""
    try:
        fields = collect_instrument_attest(prompt_fn)
    except FormInputError as exc:
        return f"Error: {exc}"
    if fields is None:
        return "attest-instrument cancelled -- nothing recorded"
    repo.upsert_instrument_attestation(
        venue=fields.venue,
        product_id=fields.product,
        wrapper=fields.wrapper,
        source=fields.source,
        attested_by=fields.attested_by,
        attested_at=now_ts,
    )
    return f"attested {fields.product} on {fields.venue}: wrapper={fields.wrapper}"


def run_exempt_form(repo: Any, prompt_fn: PromptFn, now_ts: int) -> str:
    """`keel assets exempt` as a form: criterion restricted to `WAIVABLE_CRITERIA` (the
    CLI's own Choice restriction), a blank rationale refused (the CLI's own guard)."""
    asset = _ask(prompt_fn, "asset code (e.g. PAXG) -- empty cancels")
    if not asset:
        return "exempt cancelled -- nothing recorded"
    asset = asset.upper()
    criterion = _ask(
        prompt_fn, f"criterion to waive -- one of: {', '.join(sorted(WAIVABLE_CRITERIA))}"
    ).lower()
    if criterion not in WAIVABLE_CRITERIA:
        return (
            f"Error: criterion must be one of: {', '.join(sorted(WAIVABLE_CRITERIA))} -- a "
            "DATA/market criterion, never a shariah one"
        )
    rationale = prompt_fn(
        "rationale -- why this waiver is granted (documentation, not a formality)"
    )
    if not rationale.strip():
        return "Error: rationale must be a non-empty documented reason"
    granted_by = _ask(prompt_fn, "granted-by -- who granted it")
    if not granted_by:
        return "Error: granted-by is required"
    repo.upsert_screen_exception(
        asset=asset,
        criterion=criterion,
        rationale=rationale.strip(),
        granted_by=granted_by,
        granted_at=now_ts,
    )
    return f"recorded exception: {asset} waives '{criterion}' criterion (by {granted_by})"


def run_unexempt_form(repo: Any, prompt_fn: PromptFn) -> str:
    """`keel assets unexempt` as a form -- a de-risking action, always allowed, and the
    repository's own rowcount tells a real revoke from a no-op (never success either way)."""
    asset = _ask(prompt_fn, "asset code -- empty cancels")
    if not asset:
        return "unexempt cancelled -- nothing changed"
    asset = asset.upper()
    criterion = _ask(
        prompt_fn, f"criterion to revoke -- one of: {', '.join(sorted(WAIVABLE_CRITERIA))}"
    ).lower()
    if criterion not in WAIVABLE_CRITERIA:
        return f"Error: criterion must be one of: {', '.join(sorted(WAIVABLE_CRITERIA))}"
    removed = repo.delete_screen_exception(asset, criterion)
    if removed:
        return f"revoked exception: {asset} no longer waives '{criterion}' criterion"
    return f"no such exception: {asset} has no '{criterion}' waiver"


def run_subscription_attest_form(
    repo: Any, config: Any, prompt_fn: PromptFn, now_ts: int
) -> str:
    """Collect `subscription attest`'s fields and dispatch to the extracted service
    (`subscription.apply_subscription_attest`) -- tier resolution, venue binding and
    pacing carry-over live THERE, once, shared with the CLI."""
    venue = _ask(prompt_fn, "venue -- empty means this config's bound venue")
    tier_name = _ask(prompt_fn, "tier name from config.yaml's tiers -- empty cancels")
    if not tier_name:
        return "subscription attest cancelled -- nothing recorded"
    pacing = _ask(
        prompt_fn, "pacing (opportunistic/even_daily) -- empty keeps the venue's current value"
    ).lower()
    if pacing and pacing not in ("opportunistic", "even_daily"):
        return "Error: pacing must be opportunistic or even_daily"
    try:
        return apply_subscription_attest(
            repo,
            config,
            venue=venue or None,
            tier_name=tier_name,
            pacing=pacing or None,
            now_ts=now_ts,
        )
    except ValueError as exc:
        return f"Error: {exc}"


def run_subscription_set_form(
    repo: Any, config: Any, prompt_fn: PromptFn, now_ts: int
) -> str:
    """Collect `subscription set`'s fields and dispatch to the extracted service."""
    venue = _ask(prompt_fn, "venue -- empty means this config's bound venue")
    free_volume_raw = _ask(
        prompt_fn, "raw fee-free monthly volume in USD (e.g. 500) -- empty cancels"
    )
    if not free_volume_raw:
        return "subscription set cancelled -- nothing recorded"
    pacing = _ask(
        prompt_fn, "pacing (opportunistic/even_daily) -- empty keeps the venue's current value"
    ).lower()
    if pacing and pacing not in ("opportunistic", "even_daily"):
        return "Error: pacing must be opportunistic or even_daily"
    try:
        return apply_subscription_set(
            repo,
            config,
            venue=venue or None,
            free_volume_raw=free_volume_raw,
            pacing=pacing or None,
            now_ts=now_ts,
        )
    except ValueError as exc:
        return f"Error: {exc}"


def clis_typed_withdrawals_gate() -> bool:
    """The CLI's OWN typed gate for `withdrawals attest --enabled`, called verbatim:
    `_require_interactive_confirmation` with the command's exact action/detail wording --
    imported from `withdrawals.py`, their ONE home, so the console and the CLI can never
    drift into two wordings for the same gate (pinned by test against that home).
    The console wraps it in the curses suspend/restore dance so the prompt renders
    in-console; the gate itself is untouched -- never pre-filled, never piped. Fails
    CLOSED: a wrong phrase, a Ctrl-C, any exception answers False and the halt stays."""
    from keel.commands._common import _require_interactive_confirmation

    try:
        _require_interactive_confirmation(
            WITHDRAWALS_ATTEST_ACTION, WITHDRAWALS_ATTEST_DETAIL
        )
        return True
    except Exception:
        return False


def run_withdrawals_form(
    repo: Any,
    prompt_fn: PromptFn,
    now_ts: int,
    *,
    confirm_enabled_fn: Callable[[], bool] | None = None,
) -> str:
    """`keel withdrawals attest` as a form, with the CLI's own asymmetry: `--suspended`
    only ever REDUCES capability and is ungated; `--enabled` RELEASES a rail-17 halt and
    demands the typed gate (`clis_typed_withdrawals_gate` unless a test injects its own).
    A declined gate means not a single state row is written."""
    if confirm_enabled_fn is None:
        confirm_enabled_fn = clis_typed_withdrawals_gate
    answer = _ask(
        prompt_fn,
        "attest withdrawals as enabled or suspended? (enabled/suspended) -- empty cancels",
    ).lower()
    if answer not in ("enabled", "suspended"):
        return "withdrawals attest cancelled -- nothing recorded"
    enabled = answer == "enabled"
    if enabled and not confirm_enabled_fn():
        return "withdrawals attest cancelled -- typed confirmation not given; nothing recorded"
    repo.set_state("withdrawals_enabled", bool(enabled))
    repo.set_state("withdrawals_attested_at", now_ts)
    ttl_days = WITHDRAWAL_ATTESTATION_TTL_SEC // 86400
    state = "ENABLED" if enabled else "SUSPENDED"
    line = f"withdrawals attested {state}; expires in {ttl_days} days"
    if not enabled:
        line += " -- new ENTRIES are now halted (rail 17). Exits are deliberately unaffected."
    return line


#: The loop's single form-dispatch seam: form name -> runner(repo, config, prompt_fn, now_ts).
#: Every member is directly unit-tested above/below; `run_form` adds no behavior of its own.
FORM_RUNNERS: dict[str, Callable[[Any, Any, PromptFn, int], str]] = {
    "attest": lambda repo, config, prompt_fn, now_ts: run_attest_form(repo, prompt_fn, now_ts),
    "attest-instrument": lambda repo, config, prompt_fn, now_ts: run_instrument_attest_form(
        repo, prompt_fn, now_ts
    ),
    "exempt": lambda repo, config, prompt_fn, now_ts: run_exempt_form(repo, prompt_fn, now_ts),
    "unexempt": lambda repo, config, prompt_fn, now_ts: run_unexempt_form(repo, prompt_fn),
    "subscription-attest": run_subscription_attest_form,
    "subscription-set": run_subscription_set_form,
    "withdrawals-attest": lambda repo, config, prompt_fn, now_ts: run_withdrawals_form(
        repo, prompt_fn, now_ts
    ),
}


def run_form(
    name: str,
    repo: Any,
    config: Any,
    prompt_fn: PromptFn,
    now_ts: int,
    *,
    confirm_enabled_fn: Callable[[], bool] | None = None,
) -> str:
    """Dispatch a Compliance form by name -- what the loop calls, and what the tests spy.
    An unknown name is a programming error (the menu's targets and this table are pinned
    together by test), so it raises rather than rendering a calm nothing."""
    if name == "withdrawals-attest" and confirm_enabled_fn is not None:
        return run_withdrawals_form(
            repo, prompt_fn, now_ts, confirm_enabled_fn=confirm_enabled_fn
        )
    runner = FORM_RUNNERS[name]
    return runner(repo, config, prompt_fn, now_ts)


# -- the scout-results browser (O6) ----------------------------------------------------------------


def scout_listing(config: Any) -> tuple[tuple[ScoutFile, ...], Path]:
    """The proposals the scout wrote, from CONFIG's `proposals_dir` (default
    `~/keel/proposals` -- the documented config key, never a TUI-side path guess), via
    the service read `admission.list_shortlists`. Returns the files newest-first and the
    directory it read (rendered on the empty state, so "no proposals" is checkable)."""
    directory = Path(config.proposals_dir).expanduser()
    return list_shortlists(directory), directory


def build_scout_list_lines(
    files: tuple[ScoutFile, ...], directory: Path, *, cursor: int = 0
) -> list[ScreenLine]:
    """The Scout results browser's file list: every shortlist newest-first with its date,
    exactly one cursor row, and an empty state that names the directory it read AND the
    filename convention -- an operator whose file is called `candidates.json` is one
    rename away and must be able to discover that from this screen. The directory-bearing
    lines WRAP to the 80-column budget (`_paint` clips at the window width): a path's tail
    is exactly the part that identifies it. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- compliance / Scout results", "heading"),
    ]
    for wrapped in _wrap(f"shortlists in {directory} (config proposals_dir), newest first",
                         indent=""):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(_blank())
    if not files:
        for wrapped in _wrap(f"no proposals -- {directory} holds no *shortlist.json file yet.",
                             indent=""):
            lines.append(ScreenLine(wrapped, "normal"))
        for wrapped in _wrap(
            "the keel-asset-scout writes there (operator-local); a shortlist's name must "
            'end in "shortlist.json" and hold {"candidates": [...]}.',
            indent="",
        ):
            lines.append(ScreenLine(wrapped, "muted"))
        lines.append(_blank())
        lines.append(
            ScreenLine("Press q, Esc or m to return to the Compliance menu.", "muted")
        )
        return lines
    cursor = max(0, min(cursor, len(files) - 1))
    for index, scout_file in enumerate(files):
        marker = ">" if index == cursor else " "
        day = time.strftime("%Y-%m-%d %H:%M", time.localtime(scout_file.mtime_ts))
        text = (
            f"{marker} {scout_file.path.name}  · written {day} · {scout_file.size_bytes} bytes"
        )
        lines.append(ScreenLine(text, "heading" if index == cursor else "normal"))
    lines.append(_blank())
    for wrapped in _wrap(
        "Enter opens a shortlist · q/Esc/m back -- the flow proposes and screens; it "
        "never auto-attests",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    return lines


def build_scout_file_lines(
    view: ProposeView, *, cursor: int = 0
) -> tuple[list[ScreenLine], int, int]:
    """The selected shortlist, rendered through the propose services' own renderer
    (`render_propose_view` -- parse + `build_proposal_report` through THE gate), with a
    cursor over the CANDIDATE rows (the `ADMIT`/`REJECT` verdict lines map 1:1 to
    `view.report.screened`) so `a` can offer the typed attest step for the chosen asset.

    Returns (lines, the cursor row's line index, the candidate count) -- the count is what
    lets the loop clamp the cursor and refuse `a` when there is nothing selectable. PURE."""
    lines: list[ScreenLine] = [
        ScreenLine("keel console -- compliance / Scout results", "heading"),
    ]
    verdict_rows: list[int] = []
    for text in render_propose_view(view):
        if not text:
            lines.append(_blank())
            continue
        stripped = text.lstrip()
        if view.status == "ok" and stripped.startswith(("ADMIT", "REJECT")):
            verdict_rows.append(len(lines))
        lines.append(ScreenLine(text, _admission_line_style(text)))
    cursor_line = len(lines)  # default: no row marked (no candidates / non-ok view)
    if verdict_rows:
        cursor = max(0, min(cursor, len(verdict_rows) - 1))
        target = verdict_rows[cursor]
        marked = lines[target]
        lines[target] = ScreenLine(f"> {marked.text}", "heading")
        cursor_line = target
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j move · a attest the selected candidate (TYPED -- never auto-run) · "
            "q/Esc back to the list",
            "muted",
        )
    )
    candidates = (
        len(view.report.screened) if view.status == "ok" and view.report is not None else 0
    )
    return lines, cursor_line, candidates


# -- the "Shariah in force" browser (O10) ----------------------------------------------------------


@dataclass(frozen=True)
class FiqhConstraint:
    """One fiqh-derived constraint the rails encode, SOURCED from `docs/fiqh-basis.md`'s
    own structure: `quote` is a verbatim passage of that document (whitespace-normalized
    when rendered/wrapped), and `citation` is one of its EXACT section headings -- so the
    browser can never drift into a TUI-authored fiqh summary (pinned by test against the
    document itself)."""

    key: str
    quote: str
    citation: str


#: The fiqh-derived constraints O10 names, each keyed to the fiqh basis's own section.
#: The no-leverage/no-interest posture rides the screen's attested axes; spot-only is the
#: charter (rails 18/19); qabd is rail 17; purification is §65.9 -- exactly the document's
#: own mapping, quoted from it.
FIQH_CONSTRAINTS: tuple[FiqhConstraint, ...] = (
    FiqhConstraint(
        key="attested-vs-computed",
        quote=(
            "market facts are computed, Shariah classifications are **ATTESTED, never "
            "inferred**"
        ),
        citation="## What is attested versus what is computed",
    ),
    FiqhConstraint(
        key="rail-1-allowlist",
        quote=(
            "Per-trade and un-overridable: every intent, DCA included, must be for an "
            "allowlisted asset."
        ),
        citation="### Rail 1 — allowlist enforcement (`keel/execution/guards.py`)",
    ),
    FiqhConstraint(
        key="rail-17-qabd",
        quote=(
            "An asset we cannot withdraw is an asset we may not validly POSSESS — so "
            "acquiring more of it is the thing to stop."
        ),
        citation="### Rail 17 — withdrawal capability, `qabd` §65.4",
    ),
    FiqhConstraint(
        key="rails-18-19-spot-charter",
        quote="Spot-only is this agent's CHARTER, not an operator preference",
        citation="### Rails 18/19 — settlement currency and spot-instrument shape",
    ),
    FiqhConstraint(
        key="purification",
        quote=(
            "interest/reward credits are segregated from realised P&L and the equity base, "
            "reported as owed to charity, never recognised as profit"
        ),
        citation="### Purification (§65.9) and idle-balance rewards (§56.3)",
    ),
)


@dataclass(frozen=True)
class VocabTerm:
    """One vocabulary term of the shariah screen, anchored to `docs/fiqh-basis.md`:
    `stated=True` means `definition` is a verbatim passage of the document; a term the
    document does NOT state renders `stated=False` and says so, rather than being defined
    here -- the document's own "not stated" honesty rule, inherited (never a second,
    drifting glossary; the full single-source glossary is C7's)."""

    term: str
    definition: str
    citation: str
    stated: bool = True


VOCABULARY: tuple[VocabTerm, ...] = (
    VocabTerm(
        term="attestation",
        definition="A human records them, with a source and a name, via `keel assets attest`.",
        citation="## What is attested versus what is computed",
    ),
    VocabTerm(
        term="qabd",
        definition="possession is the ability to dispose, not physical custody",
        citation="### Rail 17 — withdrawal capability, `qabd` §65.4",
    ),
    VocabTerm(
        term="riba",
        definition=(
            "Coinbase pays USDC rewards on idle balances, that interest is riba, and it "
            "accrues with no order placed"
        ),
        citation="### Purification (§65.9) and idle-balance rewards (§56.3)",
    ),
    VocabTerm(
        term="maisir",
        definition=(
            "what makes speculation *maisir* is non-ownership, non-delivery, "
            "difference-settlement"
        ),
        citation="### Rails 18/19 — settlement currency and spot-instrument shape",
    ),
    VocabTerm(
        term="exemption",
        definition=(
            "a documented exception (`keel assets exempt`) may waive only ONE criterion "
            "today: `history`"
        ),
        citation="### The curation screen (`keel/compliance/screen.py`)",
    ),
    VocabTerm(
        term="purification",
        definition=(
            "interest/reward credits are segregated from realised P&L and the equity base, "
            "reported as owed to charity, never recognised as profit"
        ),
        citation="### Purification (§65.9) and idle-balance rewards (§56.3)",
    ),
    VocabTerm(
        term="gharar",
        definition=(
            "not stated in docs/fiqh-basis.md -- the knowledge-base sources it indexes "
            "(docs/superpowers/references/trading-knowledge-base/) are the place to read it"
        ),
        citation="## How to read the citations",
        stated=False,
    ),
)

#: The standing honesty lines, sourced from the fiqh basis's own wording (the boundary
#: sentence and the review-status section's opening clause -- both pinned two-sided by
#: test against the document, the house pattern from `tests/test_fiqh_basis.py`).
NOT_A_FATWA_ENGINE_LINE = (
    "keel is not a fatwa engine. It is an enforcement engine for a ruling you supply."
)
NO_SCHOLARLY_REVIEW_LINE = "No scholarly review of keel's fiqh basis has occurred."


def _wrap(text: str, width: int = 78, indent: str = "  ") -> list[str]:
    """Wrap `text` on spaces to `width`, every continuation line carrying `indent` -- the
    shariah screen's quotes, definitions and honesty lines are sentences of the fiqh
    basis, not screen furniture, and `_paint` clips at the window width (80-column
    terminals are this dashboard's stated target): a clipped citation tail would be the
    part an operator scrolled for. PURE."""
    words = text.split()
    if not words:
        return [indent]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(indent) + len(candidate) > width:
            lines.append(f"{indent}{current}")
            current = word
        else:
            current = candidate
    lines.append(f"{indent}{current}")
    return lines


def _date(ts: Any) -> str:
    """Local-time `YYYY-MM-DD` for an attestation/exception stamp -- the recorded DATE,
    not a raw int (and not the time: a ruling's provenance is its day)."""
    return time.strftime("%Y-%m-%d", time.localtime(int(ts)))


def build_shariah_lines(
    inventory: AttestationsInForce,
    *,
    withdrawals_enabled: bool | None,
    now_ts: int,
) -> list[ScreenLine]:
    """The "Shariah in force" browser's CONTENT (the view overlay adds title/footer):
    what the engine enforces for the ACTIVE profile, rendered from records alone.

    * the attestations in force over the active allowlist -- asset rows (source, ruling,
      recorded date) and the instrument statements behind each product, via the service
      read `gather_attestations_in_force`; unattested allowlisted assets are NAMED as the
      fail-closed gap they are;
    * the documented exemptions in effect;
    * rail 17's live state (`withdrawals_enabled`: the same read the rail makes, with
      `None` rendered UNKNOWN/stale -- fail-closed is a fact worth showing);
    * the fiqh-derived constraints, each a verbatim `docs/fiqh-basis.md` quote with its
      section citation, and the vocabulary anchored the same way;
    * the two standing honesty lines -- as a PINNED FOOTER (`shariah_honesty_lines`,
      painted outside the scroll by `pinned_frame`), always visible at every scroll
      offset rather than riding the body's tail a viewport below the fold.

    READ-ONLY: pure over its inputs; nothing here re-derives, and no state changes."""
    del now_ts  # the dates rendered are the RECORDS' own stamps, not "now"
    # The header carries the ACTIVE allowlist however long it is (paper-hourly runs 19
    # assets -- one unwrapped line would clip past the 80-column budget), so it wraps
    # through the same `_wrap` the quotes and honesty lines use.
    lines: list[ScreenLine] = []
    for wrapped in _wrap(
        f"active allowlist: {', '.join(inventory.allowlist)} · quote {inventory.quote}",
        indent="",
    ):
        lines.append(ScreenLine(wrapped, "muted"))
    lines.append(
        ScreenLine("read-only -- what the engine enforces now, rendered from records", "muted")
    )
    lines.append(_blank())
    lines.append(ScreenLine("attestations in force", "heading"))
    if not inventory.asset_rows and not inventory.instrument_rows:
        lines.append(ScreenLine("  none recorded for the active allowlist", "normal"))
    for row in inventory.asset_rows:
        lines.append(
            ScreenLine(
                f"  {row['asset']:<8} sector={row['sector']} backing={row['backing']} "
                f"pays_yield={bool(row['pays_yield'])}",
                "normal",
            )
        )
        lines.append(
            ScreenLine(
                f"      source: {row['source']} (by {row['attested_by']}, "
                f"{_date(row['attested_at'])})",
                "muted",
            )
        )
    for row in inventory.instrument_rows:
        lines.append(
            ScreenLine(
                f"  {row['product_id']} on {row['venue']}: wrapper={row['wrapper']}",
                "normal",
            )
        )
        lines.append(
            ScreenLine(
                f"      source: {row['source']} (by {row['attested_by']}, "
                f"{_date(row['attested_at'])})",
                "muted",
            )
        )
    for asset in inventory.unattested:
        lines.append(
            ScreenLine(
                f"  {asset:<8} no attestation -- the screen fails closed "
                "(unknown is a rejection)",
                "warn",
            )
        )

    lines.append(_blank())
    lines.append(ScreenLine("documented exemptions in effect", "heading"))
    if not inventory.exceptions:
        lines.append(ScreenLine("  none", "muted"))
    for row in inventory.exceptions:
        lines.append(
            ScreenLine(
                f"  {row['asset']:<8} waives '{row['criterion']}' (by {row['granted_by']}, "
                f"{_date(row['granted_at'])})",
                "normal",
            )
        )
        for wrapped in _wrap(row["rationale"], indent="      "):
            lines.append(ScreenLine(wrapped, "muted"))

    lines.append(_blank())
    if withdrawals_enabled is None:
        rail17 = ScreenLine(
            "rail 17 (qabd) right now: UNKNOWN -- no fresh attestation; rail 17 blocks "
            "new entries",
            "warn",
        )
    elif withdrawals_enabled:
        rail17 = ScreenLine("rail 17 (qabd) right now: ENABLED (attested)", "ok")
    else:
        rail17 = ScreenLine(
            "rail 17 (qabd) right now: SUSPENDED -- rail 17 blocks new entries", "warn"
        )
    lines.append(rail17)

    lines.append(_blank())
    lines.append(
        ScreenLine(
            "fiqh-derived constraints the rails encode (quoted from docs/fiqh-basis.md)",
            "heading",
        )
    )
    for constraint in FIQH_CONSTRAINTS:
        for wrapped in _wrap(f"{constraint.key}: {constraint.quote}"):
            lines.append(ScreenLine(wrapped, "normal"))
        lines.append(ScreenLine(f"      -- {constraint.citation}", "muted"))

    lines.append(_blank())
    lines.append(ScreenLine("vocabulary (anchored to docs/fiqh-basis.md)", "heading"))
    for term in VOCABULARY:
        for wrapped in _wrap(f"{term.term}: {term.definition}"):
            lines.append(ScreenLine(wrapped, "normal"))

    # The two standing honesty lines are NOT part of this body: they render as a pinned
    # footer (`shariah_honesty_lines` + `pinned_frame`) so no scroll offset can hide them.
    return lines


def shariah_honesty_lines() -> list[ScreenLine]:
    """The shariah view's FIXED footer: the two standing honesty lines, wrapped to the
    80-column budget, in the alert style -- painted OUTSIDE the scroll (see
    `pinned_frame`), so they are on screen at EVERY scroll offset. O10's "always visible"
    made structural: as the body's tail they were one viewport below the fold on any real
    allowlist, and an operator who never scrolled never saw them. PURE."""
    lines: list[ScreenLine] = []
    for wrapped in _wrap(NOT_A_FATWA_ENGINE_LINE):
        lines.append(ScreenLine(wrapped, "alert"))
    for wrapped in _wrap(f"{NO_SCHOLARLY_REVIEW_LINE} (docs/fiqh-basis.md, review status)"):
        lines.append(ScreenLine(wrapped, "alert"))
    return lines


def pinned_frame(
    body: list[ScreenLine],
    footer: list[ScreenLine],
    *,
    offset: int,
    height: int,
) -> list[ScreenLine]:
    """A scrolled `body` under a FIXED `footer`: the footer's rows are reserved off the
    window FIRST, then the body is sliced through `_visible_slice` into what remains --
    so whatever `offset` the body is scrolled to, the frame that gets painted ENDS with
    the footer's lines. PURE, and total: a footer taller than `height` still renders
    (clipped by `_paint`, never raising here)."""
    window = max(height - len(footer), 0)
    return [*_visible_slice(body, offset, window), *footer]


# -- the view overlay ------------------------------------------------------------------------------


_VIEW_TITLES: dict[str, str] = {
    "screen": "screen",
    "propose": "propose",
    "holdings": "holdings",
    "discover": "discover",
    "shariah": "Shariah in force",
    "subscription": "subscription",
    "purification": "purification",
}

#: The two views whose payload is one live venue read -- they open ARMED, gated behind an
#: explicit Enter exactly like the dashboard's discover overlay, and hold their result.
_NETWORK_VIEWS = ("holdings", "discover")


def build_compliance_view_lines(
    kind: str, payload: Any, *, error: str | None = None
) -> list[ScreenLine]:
    """One Compliance report overlay, PURE over its payload: the service's own renderer's
    lines, styled by the admission overlays' own style function, under a title that names
    the entry, with the ARMED story for the network-gated views (`payload is None`) and a
    fail-soft alert for a read that failed. The caller (the loop) gathers the payload --
    this function never touches a repo, a broker or the network."""
    lines: list[ScreenLine] = [
        ScreenLine(f"keel console -- compliance / {_VIEW_TITLES.get(kind, kind)}", "heading"),
        _blank(),
    ]
    if error is not None:
        # Honest about what happens next: nothing retries on its own here -- the network
        # views HOLD this error until Enter re-runs the read (and the offline views
        # rebuild on the next poll, which Enter also forces) -- so the line names the
        # retry key instead of claiming a "retrying..." that never happens.
        lines.append(
            ScreenLine(f"{kind} read failed: {error} -- press Enter to retry", "alert")
        )
    elif payload is None:
        # The network-gated views' ARMED state: opening them made NO call. What Enter
        # does is named, in the same words the discover overlay uses for the same story.
        if kind == "holdings":
            call = "get_accounts -- the same balance read the dashboard's live-balance line makes"
        else:
            call = "list_products -- the same product metadata read `keel assets discover` makes"
        lines.append(ScreenLine("ARMED -- no network call has been made yet.", "normal"))
        lines.append(_blank())
        lines.append(
            ScreenLine(f"Pressing Enter makes ONE live call to the venue ({call}).", "normal")
        )
        lines.append(
            ScreenLine(
                "It never fires from opening this screen, and the result is held until "
                "Enter is pressed again or the screen closes.",
                "muted",
            )
        )
        if kind == "holdings":
            lines.append(
                ScreenLine(
                    "Holdings are CANDIDATES, not admissions -- nothing here is admitted.",
                    "muted",
                )
            )
    elif kind == "screen":
        for text in render_screen_report(payload):
            lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    elif kind == "propose":
        for text in render_propose_view(payload):
            lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    elif kind == "holdings":
        for text in render_holdings(payload):
            lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    elif kind == "discover":
        for text in render_discover(payload):
            lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    elif isinstance(payload, list) and payload and isinstance(payload[0], ScreenLine):
        # Pre-styled content (the shariah browser's own builder).
        lines.extend(payload)
    else:
        # The report-shape payloads (purification, subscription) arrive as the service's
        # own already-rendered `list[str]` lines.
        for text in payload:
            lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    lines.append(_blank())
    lines.append(ScreenLine("Press q or Esc to return to the Compliance menu.", "muted"))
    return lines
