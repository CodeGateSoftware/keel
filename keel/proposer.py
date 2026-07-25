"""LLM asset proposer -- ingest an externally-produced shortlist and route each candidate
through the EXISTING admission gate. Admits nothing. See
docs/superpowers/specs/2026-07-24-llm-asset-proposer-design.md.

Pure and dependency-free: no LLM, no network, no DB writes. The gate is injected as `screen_fn`
so this module never imports `keel.cli` (which would cycle) and stays unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from keel.commands._products import _history_product
from keel.compliance import screen as screen_mod
from keel.data.repository import Repository

ScreenFn = Callable[
    [Repository, str, str], tuple[screen_mod.MarketFacts, screen_mod.ScreenResult]
]


class ProposalError(ValueError):
    """Malformed proposal at the top level (not a per-candidate issue)."""


@dataclass(frozen=True)
class Candidate:
    asset: str
    rationale: str
    sources: list[str]
    shariah_hypothesis: str | None = None


@dataclass(frozen=True)
class InvalidEntry:
    raw: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class ParsedProposal:
    candidates: list[Candidate]
    invalid: list[InvalidEntry]


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value)
    except (ValueError, TypeError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _candidate_error(entry: Any) -> str | None:
    """Return None if the entry is a valid candidate, else a human reason string."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    asset = entry.get("asset")
    if not isinstance(asset, str) or not asset.strip():
        return "missing or empty 'asset'"
    rationale = entry.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return "missing or empty 'rationale'"
    sources = entry.get("sources")
    if not isinstance(sources, list) or not sources:
        return "missing or empty 'sources' (>= 1 citation required)"
    if not all(_is_http_url(s) for s in sources):
        return "every source must be a non-empty http(s) URL"
    hypothesis = entry.get("shariah_hypothesis")
    if hypothesis is not None and not isinstance(hypothesis, str):
        return "'shariah_hypothesis' must be a string when present"
    return None


def parse_proposal(raw: Any) -> ParsedProposal:
    """Validate a decoded JSON proposal into valid Candidates + InvalidEntries.

    Raises ProposalError for a malformed top-level structure. A per-candidate problem (missing
    citation, bad URL, empty field) does NOT raise -- the entry is collected into `invalid` and
    excluded from screening, never silently dropped.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("candidates"), list):
        raise ProposalError("proposal must be an object with a 'candidates' list")

    candidates: list[Candidate] = []
    invalid: list[InvalidEntry] = []
    for entry in raw["candidates"]:
        reason = _candidate_error(entry)
        if reason is not None:
            raw_entry = entry if isinstance(entry, dict) else {"value": entry}
            invalid.append(InvalidEntry(raw=raw_entry, reason=reason))
            continue
        hypothesis = entry.get("shariah_hypothesis")
        candidates.append(
            Candidate(
                asset=entry["asset"].strip().upper(),
                rationale=entry["rationale"].strip(),
                sources=[s.strip() for s in entry["sources"]],
                shariah_hypothesis=(
                    hypothesis.strip()
                    if isinstance(hypothesis, str) and hypothesis.strip()
                    else None
                ),
            )
        )
    return ParsedProposal(candidates=candidates, invalid=invalid)


@dataclass(frozen=True)
class ScreenedCandidate:
    candidate: Candidate
    product: str
    on_allowlist: bool
    attested: bool
    facts: screen_mod.MarketFacts
    result: screen_mod.ScreenResult


@dataclass(frozen=True)
class ProposalReport:
    screened: list[ScreenedCandidate]
    invalid: list[InvalidEntry]

    @property
    def admitted_count(self) -> int:
        return sum(1 for s in self.screened if s.result.admitted)


def build_proposal_report(
    parsed: ParsedProposal,
    repo: Repository,
    quote: str,
    allowlist: list[str],
    screen_fn: ScreenFn,
) -> ProposalReport:
    """Route each valid candidate through the injected admission gate. Writes nothing.

    `screen_fn` receives only (repo, product, quote) -- the LLM's rationale and shariah_hypothesis
    are never passed to the gate, so they cannot influence admission (asymmetry, by construction).
    """
    allow = {a.upper() for a in allowlist}
    screened: list[ScreenedCandidate] = []
    for cand in parsed.candidates:
        product = _history_product(cand.asset, quote)
        facts, result = screen_fn(repo, product, quote)
        screened.append(
            ScreenedCandidate(
                candidate=cand,
                product=product,
                on_allowlist=cand.asset in allow,
                attested=repo.get_asset_attestation(cand.asset) is not None,
                facts=facts,
                result=result,
            )
        )
    return ProposalReport(screened=screened, invalid=parsed.invalid)


_DATA_DERIVED_FAILURES = frozenset({"liquidity"})  # keep in sync with cli.py `assets holdings`


def render_proposal_report(report: ProposalReport) -> list[str]:
    """Human-readable lines. Admits nothing -- this only reports gate verdicts + next steps."""
    lines: list[str] = []
    if not report.screened and not report.invalid:
        return ["no candidates in proposal."]

    for sc in report.screened:
        cand = sc.candidate
        allow = "on-allowlist" if sc.on_allowlist else "not-on-allowlist"
        attested = "attested" if sc.attested else "UNATTESTED"
        lines.append("")
        lines.append(
            f"{sc.result.summary:<7} {cand.asset:<8} bars={sc.facts.daily_bars} "
            f"{allow} {attested}"
        )
        lines.append(f"    rationale: {cand.rationale}")
        for src in cand.sources:
            lines.append(f"    source: {src}")
        if cand.shariah_hypothesis:
            lines.append(
                f"    UNVERIFIED hypothesis (never used for admission): {cand.shariah_hypothesis}"
            )
        failures = list(sc.result.failures)
        if sc.facts.daily_bars == 0:
            derived = [f for f in failures if f.split(":")[0] in _DATA_DERIVED_FAILURES]
            failures = [f for f in failures if f not in derived]
            lines.append(
                f"    ! no local history -- run `keel fetch --products {sc.product}` first, "
                "then re-screen."
            )
            lines.append("      This is a MISSING-DATA verdict, not a verdict about the asset.")
            for failure in derived:
                lines.append(f"    · ({failure.split(':')[0]}: not assessable without history)")
        for failure in failures:
            lines.append(f"    ✗ {failure}")
        for warning in sc.result.warnings:
            lines.append(f"    ! {warning}")
        if not sc.result.admitted and not sc.attested:
            lines.append(
                f"    next: human-classify with `keel assets attest {cand.asset} "
                "--sector <s> --backing <ayn|dayn|native> --source <url>`, then fetch data "
                "and backtest."
            )

    for entry in report.invalid:
        lines.append("")
        lines.append(f"INVALID  {entry.reason}: {entry.raw}")

    invalid_word = "entry" if len(report.invalid) == 1 else "entries"
    lines.append("")
    lines.append(
        f"{report.admitted_count}/{len(report.screened)} admitted "
        f"({len(report.invalid)} invalid {invalid_word})"
    )
    return lines


def report_to_jsonable(report: ProposalReport) -> dict[str, Any]:
    return {
        "screened": [
            {
                "asset": sc.candidate.asset,
                "product": sc.product,
                "rationale": sc.candidate.rationale,
                "sources": sc.candidate.sources,
                "shariah_hypothesis": sc.candidate.shariah_hypothesis,
                "on_allowlist": sc.on_allowlist,
                "attested": sc.attested,
                "admitted": sc.result.admitted,
                "summary": sc.result.summary,
                "daily_bars": sc.facts.daily_bars,
                "failures": sc.result.failures,
                "warnings": sc.result.warnings,
            }
            for sc in report.screened
        ],
        "invalid": [{"reason": e.reason, "raw": e.raw} for e in report.invalid],
        "admitted_count": report.admitted_count,
    }
