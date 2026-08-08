"""The OFFLINE report layer for the allowlist-admission workflow -- the substrate the TUI's three
new overlays (**screen**, **propose**, **discover**) render. See
`docs/superpowers/specs/2026-07-24-llm-asset-proposer-design.md` and `keel/proposer.py` for the
proposal-report path this module reuses rather than duplicates.

**Every verdict comes from the injected `screen_fn`** (`keel.cli._screen_product` in production),
never from a second, laxer path -- exactly the discipline `keel/proposer.py` already keeps, for
the same reason: `_screen_product`'s own docstring is explicit that every candidate source must
route through it, "so none of them can drift onto a laxer path". This module never imports
`keel.cli` (which would cycle back through here once `tui.py` wires these overlays in) and stays
importable, and unit-testable, with nothing but a fake `screen_fn`.

This module **admits nothing, attests nothing, writes nothing**. `build_screen_report` and
`build_propose_view` only ever read the DB (`Repository.get_candles`/`get_asset_attestation`/
`get_screen_exceptions`, all reached through `screen_fn`) -- see
`tests/commands/test_admission.py::test_build_screen_report_and_propose_view_write_nothing`.

**`discover` is the one part of this workflow that needs the network**, and it is handled
differently on purpose: `build_discover_report` takes already-fetched venue product dicts as a
plain argument instead of building a broker itself. That is what keeps THIS module fully
unit-testable (no fake broker, no monkeypatched `CoinbaseClient`) and, more importantly, is what
lets the TUI worker gate the one network-touching action in this whole workflow behind an
explicit keypress: the overlay can call `_build_broker(config).list_products()` itself, in its own
try/except, only when the operator asks for it, and hand the plain list here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from keel.commands._products import _default_sim_products
from keel.compliance.screen import (
    Candidate,
    DiscoveryPolicy,
    MarketFacts,
    ScreenResult,
    discover_candidates,
    missing_history_lines,
    split_failures,
)
from keel.config import Config
from keel.data.repository import Repository
from keel.proposer import (
    ProposalError,
    ProposalReport,
    build_proposal_report,
    parse_proposal,
    render_proposal_report,
)

ScreenFn = Callable[[Repository, str, str], tuple[MarketFacts, ScreenResult]]

#: Kept in sync with `Config.proposals_dir`'s own default BY HAND -- there is no single source of
#: truth to import from without creating a `keel.commands.admission` <-> `keel_core.config`
#: dependency neither module needs otherwise. `test_default_proposals_dir_matches_configs_default`
#: pins the two together so a drift breaks a test instead of silently disagreeing.
DEFAULT_PROPOSALS_DIR = "~/keel/proposals"

#: Mirrors `keel assets discover --min-volume-24h`'s own default (`keel/cli.py::assets_discover`).
#: A literal here, not an import from `keel.cli`, for the same reason `ScreenFn` is injected
#: rather than importing `_screen_product` directly: importing `keel.cli` from this module would
#: create the cycle `cli -> tui -> admission -> cli`. `test_build_discover_report_applies_
#: default_volume_floor_matching_assets_discover` reads the CLI option's own default and asserts
#: it equals this constant, so the two cannot silently drift apart.
#: Discovery's 24h-volume pre-filter, pinned EQUAL to `ScreenPolicy.min_median_daily_volume` so a
#: sweep can never be stricter than the gate it feeds. It bounds how many products get probed; it
#: is not a liquidity verdict (that is `--probe-liquidity`, which computes the gate's own median).
#:
#: Was 5,000,000 until 2026-08-08. At that floor the sweep returned 9 candidates and exactly one
#: unsettled survivor; at a lower floor, seven more cleared BOTH mechanical gates -- FET among them
#: at $2.94M/24h, i.e. invisible to the sweep while measuring 4.8x the admission floor. The floor,
#: not the market, was the binding constraint on the candidate pipeline.
#:
#: `tests/commands/test_admission.py` pins this to the CLI option, to `DiscoveryPolicy`'s default
#: and to the admission floor; all four move together or the suite fails.
DEFAULT_MIN_QUOTE_24H_VOLUME = Decimal("1000000")


# -- 2a. shortlist location (offline) ------------------------------------------------------------


#: Filename convention a candidate shortlist must follow to be picked up. Selection is by
#: CONVENTION rather than "newest `*.json`" because the proposals directory is shared: the scout
#: that produces shortlists writes sibling outputs beside them (`<date>-param-proposals.json`,
#: `<date>-research-briefs.md`). A bare `*.json` glob picked whichever sibling happened to be
#: written last, and the overlay then told the operator their own strategy output was a malformed
#: shortlist -- observed 2026-08-07, with `2026-08-07-param-proposals.json` shadowing the real
#: shortlist by one minute of mtime.
#:
#: Deliberately NOT "prefer a shortlist, else fall back to any `*.json`": on a run that produced
#: param proposals and no candidates, that fallback re-selects the sibling and reproduces the same
#: confusing error in a rarer, harder-to-diagnose case. Strict matching means an absent shortlist
#: reads as absent, which is the truth.
SHORTLIST_GLOB = "*shortlist.json"


def latest_shortlist(directory: Path) -> Path | None:
    """The newest `*shortlist.json` file under `directory` by mtime, or `None`.

    `None` covers four cases the dashboard must never be errored out by: `directory` does not
    exist, `directory` exists but is not a directory (a stray file at that path), it exists and is
    empty, or it holds only files that do not follow `SHORTLIST_GLOB`. **Never creates
    `directory`** -- a fresh install legitimately has no proposals directory yet, and a read-only
    report screen must not have the side effect of creating one. Any `OSError` encountered while
    probing the filesystem (a permissions problem, a race where the directory is removed mid-call)
    is treated the same way, for the same reason.

    Ties in mtime are broken by filename (the alphabetically-last name wins), so the result is
    deterministic across repeated calls on the same directory contents rather than depending on
    filesystem iteration order.
    """
    try:
        if not directory.is_dir():
            return None
        candidates = list(directory.glob(SHORTLIST_GLOB))
        if not candidates:
            return None
        return max(candidates, key=lambda p: (p.stat().st_mtime, p.name))
    except OSError:
        return None


# -- 2b. screen report (offline, DB reads only) --------------------------------------------------


@dataclass(frozen=True)
class ScreenedProduct:
    product: str
    asset: str
    facts: MarketFacts
    result: ScreenResult
    on_allowlist: bool
    attested: bool


@dataclass(frozen=True)
class ScreenReport:
    quote: str
    screened: list[ScreenedProduct]

    @property
    def admitted_count(self) -> int:
        return sum(1 for s in self.screened if s.result.admitted)


def build_screen_report(repo: Repository, config: Config, screen_fn: ScreenFn) -> ScreenReport:
    """Screen `_default_sim_products(config)` -- the configured allowlist, in the deployment's
    settlement currency, exactly the set `keel assets screen` screens by default -- through the
    injected `screen_fn`.

    Mirrors `keel/proposer.py::build_proposal_report`'s shape closely: `screen_fn` is injected for
    the identical reason (no `keel.cli` import, so no import cycle once `tui.py` wires this
    module in, and fully unit-testable with a fake). Read-only: this never calls
    `repo.upsert_asset_attestation` or any other write method.
    """
    quote = config.quote_currency
    allow = {asset.upper() for asset in config.allowlist}
    screened: list[ScreenedProduct] = []
    for product in _default_sim_products(config):
        asset = product.split("-")[0]
        facts, result = screen_fn(repo, product, quote)
        screened.append(
            ScreenedProduct(
                product=product,
                asset=asset,
                facts=facts,
                result=result,
                on_allowlist=asset in allow,
                attested=repo.get_asset_attestation(asset) is not None,
            )
        )
    return ScreenReport(quote=quote, screened=screened)


def render_screen_report(report: ScreenReport) -> list[str]:
    """Human-readable lines, one product at a time, shaped to read the same way an operator
    already reads `keel/proposer.py::render_proposal_report`'s output.

    Uses `screen.split_failures`/`screen.missing_history_lines` -- NOT a reimplementation of that
    split -- so the zero-cached-bars case says "no local history, run keel fetch" and never prints
    a `✗ history:` depth failure for a candidate this deployment has simply never fetched. See
    `tests/commands/test_admission.py`'s two headline tests, which pin exactly this distinction.
    """
    if not report.screened:
        return ["allowlist is empty -- nothing to screen."]

    lines: list[str] = []
    for sp in report.screened:
        allow = "on-allowlist" if sp.on_allowlist else "not-on-allowlist"
        attested = "attested" if sp.attested else "UNATTESTED"
        lines.append("")
        lines.append(
            f"{sp.result.summary:<7} {sp.asset:<8} bars={sp.facts.daily_bars} "
            f"median_daily_volume={sp.facts.median_daily_volume:.0f} {allow} {attested}"
        )
        failures, not_assessable = split_failures(sp.facts, sp.result)
        if sp.facts.daily_bars == 0:
            explanation = missing_history_lines(sp.product, not_assessable)
            lines.append(f"    ! {explanation[0]}")
            for extra_line in explanation[1:]:
                lines.append(f"      {extra_line}")
        for failure in failures:
            lines.append(f"    ✗ {failure}")
        for warning in sp.result.warnings:
            lines.append(f"    ! {warning}")

    lines.append("")
    lines.append(f"{report.admitted_count}/{len(report.screened)} admitted")
    return lines


# -- 2c. propose view (offline) -------------------------------------------------------------------

_PROPOSAL_SCHEMA_SUMMARY = (
    'expected schema: {"candidates": [{"asset": "<code>", "rationale": "<why>", '
    '"sources": ["<http(s) URL>", ...], "shariah_hypothesis": "<optional>"}]}'
)


@dataclass(frozen=True)
class ProposeView:
    source: Path | None
    status: str  # "ok" | "no-directory" | "no-shortlist" | "unreadable" | "malformed"
    detail: str | None  # the human reason when status != "ok"
    report: ProposalReport | None


def build_propose_view(
    repo: Repository,
    config: Config,
    screen_fn: ScreenFn,
    *,
    directory: Path | None = None,
    path: Path | None = None,
) -> ProposeView:
    """Locate and screen the shortlist the `propose` overlay should show. **Never raises.**

    Resolution order: an explicit `path` (names an exact shortlist file, skipping the
    newest-file search) beats `latest_shortlist(directory)`. `directory` defaults to
    `Path(config.proposals_dir).expanduser()`.

    Every failure mode is FAIL-SOFT: a missing directory, an empty one, an unreadable file
    (`OSError` -- a permissions problem, the file vanishing mid-read -- or `UnicodeDecodeError`,
    which is NOT an `OSError` and needs naming separately; see the read below), invalid JSON
    (`json.JSONDecodeError`), or a malformed top-level shortlist (`ProposalError` from
    `parse_proposal`, e.g. `{"candidates": "nope"}`) each produce a `ProposeView` carrying the
    matching `status` and a plain-English `detail` -- never an exception. This function backs a
    dashboard overlay: a stray file on disk, or a directory that has not been created yet on a
    fresh install, must never be able to kill the process reading it. A per-CANDIDATE problem
    (missing citation, bad URL) is a different, much softer case -- it does NOT reach this
    function's fail-soft branches at all, because `parse_proposal` already handles it by
    collecting the entry into `ParsedProposal.invalid` rather than raising; see
    `render_propose_view`, which renders those as `INVALID` lines from the underlying
    `ProposalReport`, same as `keel assets propose` does.

    On `"ok"`, the report is built with `parse_proposal` + `build_proposal_report` -- reused
    verbatim from `keel/proposer.py`, never reimplemented here.
    """
    if directory is None:
        directory = Path(config.proposals_dir).expanduser()

    source = path
    if source is None:
        try:
            directory_exists = directory.is_dir()
        except OSError:
            directory_exists = False
        if not directory_exists:
            return ProposeView(
                source=None,
                status="no-directory",
                detail=(
                    f"no proposals directory at {directory} -- drop a shortlist JSON there "
                    "(produced externally, e.g. an LLM + web-search scout, or via the discover "
                    f"overlay) and reopen this screen. {_PROPOSAL_SCHEMA_SUMMARY}"
                ),
                report=None,
            )
        source = latest_shortlist(directory)
        if source is None:
            return ProposeView(
                source=None,
                status="no-shortlist",
                detail=(
                    # Name the pattern, not just the directory: selection is by convention, so an
                    # operator whose file is called `candidates.json` is one rename away and has
                    # no way to discover that from "no shortlist yet".
                    f"{directory} has no {SHORTLIST_GLOB} file yet -- drop one there (the name "
                    f"must end in shortlist.json) and reopen this screen. "
                    f"{_PROPOSAL_SCHEMA_SUMMARY}"
                ),
                report=None,
            )

    try:
        raw_text = source.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        # `UnicodeDecodeError` subclasses **ValueError, not OSError** -- so `except OSError` alone
        # let a non-UTF-8 shortlist escape this function entirely, breaking the "Never raises"
        # contract above. It is not a hypothetical: a scout run on Windows writes UTF-16LE+BOM,
        # which is perfectly valid JSON and unreadable here. Uncaught, the TUI's propose overlay
        # repainted `'utf-8' codec can't decode byte 0xff...` every poll forever, naming neither
        # the file nor a next step, and `keel assets propose --from` exited on a raw traceback.
        # Both now get the same calm, file-naming `unreadable` report a permissions error gets.
        return ProposeView(
            source=source,
            status="unreadable",
            detail=f"could not read {source}: {exc}",
            report=None,
        )

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return ProposeView(
            source=source,
            status="malformed",
            detail=f"{source} is not valid JSON: {exc}. {_PROPOSAL_SCHEMA_SUMMARY}",
            report=None,
        )

    try:
        parsed = parse_proposal(raw)
    except ProposalError as exc:
        return ProposeView(
            source=source,
            status="malformed",
            detail=f"{source}: {exc}. {_PROPOSAL_SCHEMA_SUMMARY}",
            report=None,
        )

    report = build_proposal_report(
        parsed, repo, config.quote_currency, config.allowlist, screen_fn
    )
    return ProposeView(source=source, status="ok", detail=None, report=report)


_PROPOSE_STATUS_HEADERS = {
    "no-directory": "no proposals directory found",
    "no-shortlist": "no shortlist file found",
    "unreadable": "could not read the shortlist file",
    "malformed": "the shortlist file is malformed",
}


def render_propose_view(view: ProposeView) -> list[str]:
    """Human-readable lines. On `"ok"` this reuses `render_proposal_report(view.report)`
    VERBATIM -- a hard requirement, not a convenience: this module must never grow a second
    rendering path for the same report shape (see `keel/proposer.py`'s own docstring on why a
    duplicated split/render invites drift). It is prefixed with one line naming the shortlist file
    that was read, since the overlay otherwise never says which file produced what is on screen.

    For every non-`"ok"` status this renders a calm, actionable explanation -- naming the
    directory it looked in and what the operator should do about it, plus the expected schema --
    and never anything that reads like a crash or a traceback.
    """
    if view.status == "ok":
        assert view.report is not None  # invariant of "ok": build_propose_view guarantees this
        lines = [f"shortlist: {view.source}"]
        lines.extend(render_proposal_report(view.report))
        return lines

    header = _PROPOSE_STATUS_HEADERS.get(view.status, "propose view unavailable")
    lines = [header]
    if view.detail:
        lines.append(f"  {view.detail}")
    return lines


# -- 2d. discover view -----------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoverReport:
    quote: str
    venue_product_count: int
    candidates: list[Candidate]
    min_quote_24h_volume: Decimal


def build_discover_report(
    products: list[dict],
    config: Config,
    *,
    limit: int = 25,
    min_quote_24h_volume: Decimal | None = None,
) -> DiscoverReport:
    """PURE over `products` -- the caller's already-fetched venue metadata. Builds no broker and
    makes no network call; see the module docstring for why that split is what lets the TUI gate
    the one network-touching action in this whole workflow behind an explicit keypress.

    Uses `screen.discover_candidates` with a `DiscoveryPolicy` built from `config.quote_currency`
    and the volume floor, excluding `config.allowlist` -- mirroring `assets_discover` in
    `keel/cli.py`. `min_quote_24h_volume` defaults to `DEFAULT_MIN_QUOTE_24H_VOLUME`, matching
    `assets discover`'s own CLI default (`5000000`).
    """
    floor = (
        min_quote_24h_volume
        if min_quote_24h_volume is not None
        else DEFAULT_MIN_QUOTE_24H_VOLUME
    )
    policy = DiscoveryPolicy(quote_currency=config.quote_currency, min_quote_24h_volume=floor)
    candidates = discover_candidates(
        products, policy, exclude_assets=frozenset(a.upper() for a in config.allowlist)
    )
    return DiscoverReport(
        quote=policy.quote_currency,
        venue_product_count=len(products),
        candidates=candidates[:limit],
        min_quote_24h_volume=floor,
    )


def render_discover_report(report: DiscoverReport) -> list[str]:
    """Mirrors `assets_discover`'s table (rank, product, asset, 24h quote volume, name) and ends
    with the SAME loud warning `keel assets discover` prints -- these are PROPOSALS, not
    admissions. Deliberately omits `--probe-history`'s per-candidate marker column: that is an
    extra network request per candidate, out of scope for this offline module (the caller already
    made the one network call this workflow needs, to fetch `products`)."""
    lines = [
        f"{report.venue_product_count} venue products -> {len(report.candidates)} candidates "
        f"(quote={report.quote}, 24h volume >= {report.min_quote_24h_volume:,.0f}, excluding "
        "the current allowlist)",
        "",
        f"{'#':>3}  {'product':<14} {'asset':<8} {'24h quote volume':>18}  name",
    ]
    for index, candidate in enumerate(report.candidates, start=1):
        lines.append(
            f"{index:>3}  {candidate.product_id:<14} {candidate.asset:<8} "
            f"{candidate.quote_24h_volume:>18,.0f}  {candidate.base_name}"
        )
    lines.append("")
    lines.append(
        "⚠️  These are PROPOSALS, not admissions. Nothing above has been screened for sector "
        "or backing -- those cannot be derived from market data. Each one needs "
        "`keel assets attest` with a source before `keel assets screen` can admit it."
    )
    return lines
