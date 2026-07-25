"""LLM asset proposer -- ingest an externally-produced shortlist and route each candidate
through the EXISTING admission gate. Admits nothing. See
docs/superpowers/specs/2026-07-24-llm-asset-proposer-design.md.

Pure and dependency-free: no LLM, no network, no DB writes. The gate is injected as `screen_fn`
so this module never imports `keel.cli` (which would cycle) and stays unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


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
