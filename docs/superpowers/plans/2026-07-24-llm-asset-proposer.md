# LLM Asset Proposer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `keel assets propose --from <shortlist.json>`, a read-only candidate source that ingests an externally-produced LLM asset shortlist and routes each candidate through the existing `_screen_product` admission gate — admitting nothing.

**Architecture:** A pure, dependency-free module `keel/proposer.py` (parse → build report → render) plus a thin `assets propose` click command in `cli.py`. The admission gate is **injected** into the pure builder as a `screen_fn`; the CLI passes the real `_screen_product`, so `propose` uses the identical gate as `assets screen`/`assets holdings` (one gate by construction). See `docs/superpowers/specs/2026-07-24-llm-asset-proposer-design.md`.

**Tech Stack:** Python ≥3.14, stdlib only (`json`, `urllib.parse`, `dataclasses`, `Decimal`), `click`. No new dependencies.

## Global Constraints

- Python ≥3.14; **stdlib + `Decimal` only — NO new dependency** (no requests/pydantic/etc.).
- **Read-only:** the command writes nothing — no attestation, no allowlist change, no DB mutation. It adds no `Repository` write method.
- **One gate, unchanged:** route every candidate through the existing `_screen_product(repo, product, quote)` in `cli.py`. Do NOT modify `_screen_product`, `screen_asset`, `ScreenPolicy`, or the attestation requirement.
- **Provenance as code:** a candidate with zero source citations is rejected at schema validation, never screened.
- **`shariah_hypothesis` is an UNVERIFIED hint** — it is never passed to `screen_asset` and can never become an attestation.
- `--json` output: `click.echo(json.dumps(payload, indent=2, default=str))`, and the disclaimer is printed only on the non-JSON path (mirror `keel/commands/status.py`).
- Tests: `click.testing.CliRunner`, a real temp `--db` path (the CLI reopens the DB by path — do NOT use `:memory:` for CLI tests), the `valid_config_path` fixture (`tests/conftest.py`, allowlist `[BTC, ETH, PAXG]`, quote `USD`).
- Commit prefix: `feat(proposer): …`. End each commit body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Create `keel/proposer.py`** — pure logic: schema dataclasses, `parse_proposal`, `build_proposal_report` (takes an injected `screen_fn`), `render_proposal_report`, `report_to_jsonable`. No import of `keel.cli` (avoids a cycle); imports only `keel.commands._products._history_product`, `keel.compliance.screen`, `keel.data.repository` (for type hints).
- **Modify `keel/cli.py`** — add the `assets propose` command to the existing `assets_group`, wired to the real `_screen_product`.
- **Create `tests/test_proposer.py`** — pure unit tests for parse/build/render.
- **Modify `tests/compliance/test_assets_cli.py`** — CLI integration tests (same-gate equivalence, admits-nothing, `--json`, error handling).

---

### Task 1: Proposal schema parsing

**Files:**
- Create: `keel/proposer.py`
- Test: `tests/test_proposer.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces: `Candidate(asset: str, rationale: str, sources: list[str], shariah_hypothesis: str | None)`, `InvalidEntry(raw: dict, reason: str)`, `ParsedProposal(candidates: list[Candidate], invalid: list[InvalidEntry])`, `ProposalError(ValueError)`, `parse_proposal(raw: dict) -> ParsedProposal`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proposer.py
import pytest
from keel.proposer import ParsedProposal, ProposalError, parse_proposal


def _entry(**over):
    e = {
        "asset": "sol",
        "rationale": "high liquidity and developer activity",
        "sources": ["https://coinmarketcap.com/currencies/solana/"],
    }
    e.update(over)
    return e


def test_valid_proposal_parses_and_normalizes_asset():
    parsed = parse_proposal({"candidates": [_entry()]})
    assert isinstance(parsed, ParsedProposal)
    assert len(parsed.candidates) == 1
    c = parsed.candidates[0]
    assert c.asset == "SOL"  # upper-cased
    assert c.sources == ["https://coinmarketcap.com/currencies/solana/"]
    assert c.shariah_hypothesis is None
    assert parsed.invalid == []


def test_optional_shariah_hypothesis_is_captured():
    parsed = parse_proposal({"candidates": [_entry(shariah_hypothesis="utility L1")]})
    assert parsed.candidates[0].shariah_hypothesis == "utility L1"


def test_missing_sources_makes_entry_invalid_not_screened():
    parsed = parse_proposal({"candidates": [_entry(sources=[])]})
    assert parsed.candidates == []
    assert len(parsed.invalid) == 1
    assert "sources" in parsed.invalid[0].reason


def test_non_url_source_is_invalid():
    parsed = parse_proposal({"candidates": [_entry(sources=["not-a-url"])]})
    assert parsed.candidates == []
    assert "URL" in parsed.invalid[0].reason


def test_empty_rationale_is_invalid():
    parsed = parse_proposal({"candidates": [_entry(rationale="  ")]})
    assert "rationale" in parsed.invalid[0].reason


def test_missing_asset_is_invalid():
    parsed = parse_proposal({"candidates": [_entry(asset="")]})
    assert "asset" in parsed.invalid[0].reason


def test_malformed_top_level_raises():
    with pytest.raises(ProposalError):
        parse_proposal({"not_candidates": []})
    with pytest.raises(ProposalError):
        parse_proposal([])  # not a dict


def test_mixed_valid_and_invalid_are_partitioned():
    parsed = parse_proposal({"candidates": [_entry(asset="BTC"), _entry(sources=[])]})
    assert [c.asset for c in parsed.candidates] == ["BTC"]
    assert len(parsed.invalid) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_proposer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel.proposer'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# keel/proposer.py
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
                shariah_hypothesis=(hypothesis.strip() if isinstance(hypothesis, str) and hypothesis.strip() else None),
            )
        )
    return ParsedProposal(candidates=candidates, invalid=invalid)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_proposer.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add keel/proposer.py tests/test_proposer.py
git commit -m "feat(proposer): proposal schema parsing with per-candidate validation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Screened-report builder (injected gate)

**Files:**
- Modify: `keel/proposer.py`
- Test: `tests/test_proposer.py`

**Interfaces:**
- Consumes: `ParsedProposal`/`Candidate`/`InvalidEntry` (Task 1); `keel.compliance.screen.MarketFacts`/`ScreenResult`; `keel.commands._products._history_product(asset, quote) -> "<asset>-<QUOTE>"`; `keel.data.repository.Repository.get_asset_attestation(asset) -> dict | None`.
- Produces: `ScreenFn` alias `Callable[[Repository, str, str], tuple[MarketFacts, ScreenResult]]`; `ScreenedCandidate(candidate, product, on_allowlist: bool, attested: bool, facts, result)`; `ProposalReport(screened: list[ScreenedCandidate], invalid: list[InvalidEntry])` with `.admitted_count: int`; `build_proposal_report(parsed, repo, quote, allowlist, screen_fn) -> ProposalReport`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proposer.py  (append)
from decimal import Decimal

from keel.compliance import screen as screen_mod
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.proposer import ProposalReport, build_proposal_report


def _repo():
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _fake_screen(admitted, bars=2000):
    calls = []

    def screen_fn(repo, product, quote):
        calls.append((product, quote))
        facts = screen_mod.MarketFacts(
            asset=product.split("-")[0],
            daily_bars=bars,
            median_daily_volume=Decimal("2000000"),
            quotable_in_settlement_currency=True,
        )
        result = screen_mod.ScreenResult(
            asset=product.split("-")[0],
            admitted=admitted,
            failures=[] if admitted else ["attestation: MISSING."],
        )
        return facts, result

    return screen_fn, calls


def test_build_routes_each_candidate_through_screen_fn():
    parsed = parse_proposal({"candidates": [_entry(asset="BTC")]})
    screen_fn, calls = _fake_screen(admitted=True)
    report = build_proposal_report(parsed, _repo(), "USD", ["BTC"], screen_fn)
    assert isinstance(report, ProposalReport)
    assert calls == [("BTC-USD", "USD")]
    sc = report.screened[0]
    assert sc.product == "BTC-USD"
    assert sc.on_allowlist is True
    assert sc.attested is False
    assert sc.result.admitted is True
    assert report.admitted_count == 1


def test_build_marks_off_allowlist():
    parsed = parse_proposal({"candidates": [_entry(asset="SOL")]})
    screen_fn, _ = _fake_screen(admitted=False)
    report = build_proposal_report(parsed, _repo(), "USD", ["BTC"], screen_fn)
    assert report.screened[0].on_allowlist is False


def test_shariah_hypothesis_is_never_passed_to_the_gate():
    # screen_fn only ever receives (repo, product, quote) -- the hypothesis cannot leak in.
    parsed = parse_proposal(
        {"candidates": [_entry(asset="SOL", shariah_hypothesis="totally halal, trust me")]}
    )
    captured = []

    def screen_fn(repo, product, quote):
        captured.append((repo, product, quote))
        return (
            screen_mod.MarketFacts("SOL", 0, Decimal(0), True),
            screen_mod.ScreenResult("SOL", admitted=False, failures=["attestation: MISSING."]),
        )

    report = build_proposal_report(parsed, _repo(), "USD", [], screen_fn)
    assert all(len(args) == 3 for args in captured)  # no 4th "hypothesis" arg exists
    assert report.screened[0].result.admitted is False  # hypothesis did not admit it


def test_invalid_entries_pass_through_to_report():
    parsed = parse_proposal({"candidates": [_entry(sources=[])]})
    screen_fn, calls = _fake_screen(admitted=True)
    report = build_proposal_report(parsed, _repo(), "USD", [], screen_fn)
    assert report.screened == []
    assert calls == []  # invalid entries are never screened
    assert len(report.invalid) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_proposer.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_proposal_report'`.

- [ ] **Step 3: Add the builder to `keel/proposer.py`**

Add these imports at the top of `keel/proposer.py` (below the existing imports):

```python
from typing import Callable

from keel.commands._products import _history_product
from keel.compliance import screen as screen_mod
from keel.data.repository import Repository

ScreenFn = Callable[[Repository, str, str], tuple["screen_mod.MarketFacts", "screen_mod.ScreenResult"]]
```

Append below the Task-1 code:

```python
@dataclass(frozen=True)
class ScreenedCandidate:
    candidate: Candidate
    product: str
    on_allowlist: bool
    attested: bool
    facts: "screen_mod.MarketFacts"
    result: "screen_mod.ScreenResult"


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_proposer.py -q`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add keel/proposer.py tests/test_proposer.py
git commit -m "feat(proposer): screened-report builder over an injected gate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Renderers (human + JSON)

**Files:**
- Modify: `keel/proposer.py`
- Test: `tests/test_proposer.py`

**Interfaces:**
- Consumes: `ProposalReport`/`ScreenedCandidate` (Task 2).
- Produces: `render_proposal_report(report) -> list[str]`; `report_to_jsonable(report) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proposer.py  (append)
import json

from keel.proposer import render_proposal_report, report_to_jsonable


def _report(admitted, bars, attested=False, hypothesis=None):
    parsed = parse_proposal(
        {"candidates": [_entry(asset="SOL", shariah_hypothesis=hypothesis)]}
    )

    def screen_fn(repo, product, quote):
        facts = screen_mod.MarketFacts("SOL", bars, Decimal("0"), True)
        failures = [] if admitted else (["history: too few bars"] if bars else ["liquidity: 0", "attestation: MISSING."])
        return facts, screen_mod.ScreenResult("SOL", admitted=admitted, failures=failures)

    repo = _repo()
    if attested:
        repo.attest_asset({"asset": "SOL", "sector": "payments", "backing": "native",
                           "pays_yield": False, "source": "https://x.invalid", "attested_by": "t",
                           "attested_at": 0})
    return build_proposal_report(parsed, repo, "USD", [], screen_fn)


def test_render_admit_shows_summary_and_sources():
    lines = render_proposal_report(_report(admitted=True, bars=2000))
    text = "\n".join(lines)
    assert "ADMIT" in text
    assert "SOL" in text
    assert "source: https://coinmarketcap.com/currencies/solana/" in text
    assert "1/1 admitted" in text


def test_render_unverified_hypothesis_is_labeled():
    lines = render_proposal_report(_report(admitted=False, bars=2000, hypothesis="halal L1"))
    text = "\n".join(lines)
    assert "UNVERIFIED" in text
    assert "halal L1" in text


def test_render_no_history_shows_missing_data_next_step():
    lines = render_proposal_report(_report(admitted=False, bars=0))
    text = "\n".join(lines)
    assert "no local history" in text
    assert "keel fetch --products SOL-USD" in text
    assert "MISSING-DATA verdict" in text
    # the liquidity failure is suppressed as not-assessable-without-history
    assert "not assessable without history" in text


def test_render_unattested_reject_shows_attest_next_step():
    lines = render_proposal_report(_report(admitted=False, bars=2000, attested=False))
    assert any("keel assets attest SOL" in line for line in lines)


def test_render_empty_report_is_friendly_not_blank():
    parsed = parse_proposal({"candidates": []})
    report = build_proposal_report(parsed, _repo(), "USD", [], lambda *a: None)
    lines = render_proposal_report(report)
    assert lines and "no candidates" in "\n".join(lines).lower()


def test_render_invalid_entries_are_listed():
    parsed = parse_proposal({"candidates": [_entry(sources=[])]})
    report = build_proposal_report(parsed, _repo(), "USD", [], lambda *a: None)
    text = "\n".join(render_proposal_report(report))
    assert "INVALID" in text
    assert "1 invalid" in text


def test_jsonable_is_json_serializable_and_has_keys():
    payload = report_to_jsonable(_report(admitted=True, bars=2000))
    dumped = json.dumps(payload, indent=2, default=str)  # must not raise
    back = json.loads(dumped)
    assert back["admitted_count"] == 1
    row = back["screened"][0]
    assert row["asset"] == "SOL"
    assert row["admitted"] is True
    assert row["sources"] == ["https://coinmarketcap.com/currencies/solana/"]
    assert "shariah_hypothesis" in row
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_proposer.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_proposal_report'`.

Note: `repo.attest_asset(...)` is used by the test helper — confirm the method name against `keel/data/repository.py` (search `def attest_asset` / `def upsert_asset_attestation`) and use the actual name; the attestation row keys are `asset/sector/backing/pays_yield/source/attested_by/attested_at`.

- [ ] **Step 3: Add the renderers to `keel/proposer.py`**

Add `from typing import Any` is already imported (Task 1). Append:

```python
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
        lines.append(f"{sc.result.summary:<7} {cand.asset:<8} bars={sc.facts.daily_bars} {allow} {attested}")
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
                f"    ! no local history -- run `keel fetch --products {sc.product}` first, then re-screen."
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
                "--sector <s> --backing <ayn|dayn|native> --source <url>`, then fetch data and backtest."
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_proposer.py -q`
Expected: PASS (all proposer unit tests).

- [ ] **Step 5: Commit**

```bash
git add keel/proposer.py tests/test_proposer.py
git commit -m "feat(proposer): human + JSON renderers with no-history + attest next-steps

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `keel assets propose` CLI command

**Files:**
- Modify: `keel/cli.py` (add the command to `assets_group`, near `assets_screen`/`assets_holdings`)
- Test: `tests/compliance/test_assets_cli.py`

**Interfaces:**
- Consumes: `keel.proposer.{parse_proposal, build_proposal_report, render_proposal_report, report_to_jsonable, ProposalError}` (Tasks 1-3); the existing `_screen_product` (same module, `cli.py`); `_load_cfg`/`_open_repo`/`DISCLAIMER` (already imported in `cli.py`); `json` and `pathlib.Path` (confirm both are imported at the top of `cli.py`; add `from pathlib import Path` / `import json` if missing).
- Produces: the `assets propose` command (no importable symbol other tasks depend on).

- [ ] **Step 1: Write the failing tests**

```python
# tests/compliance/test_assets_cli.py  (append; reuse existing helpers _repo_at, _seed_history, _attest)
import json


def _write_shortlist(tmp_path, candidates):
    path = tmp_path / "shortlist.json"
    path.write_text(json.dumps({"candidates": candidates}))
    return path


_SOL = {
    "asset": "SOL",
    "rationale": "high liquidity",
    "sources": ["https://coinmarketcap.com/currencies/solana/"],
}


def test_propose_rejects_an_unattested_candidate(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(tmp_path, [_SOL])
    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "propose", "--from", str(shortlist)],
    )
    assert result.exit_code == 0
    assert "REJECT" in result.output
    assert "0/1 admitted" in result.output


def test_propose_and_screen_agree_for_the_same_asset(tmp_path, valid_config_path):
    """One gate, shared by construction -- the proposer must not get a laxer path."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    shortlist = _write_shortlist(
        tmp_path, [{"asset": "BTC", "rationale": "reserve asset", "sources": ["https://bitcoin.org"]}]
    )
    proposed = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path),
              "assets", "propose", "--from", str(shortlist)],
    )
    screened = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path),
              "assets", "screen", "--products", "BTC-USD"],
    )
    assert "ADMIT" in proposed.output
    assert "ADMIT" in screened.output


def test_propose_writes_nothing(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    shortlist = _write_shortlist(tmp_path, [_SOL])
    CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path),
              "assets", "propose", "--from", str(shortlist)],
    )
    assert repo.get_asset_attestation("SOL") is None  # nothing attested/admitted


def test_propose_json_is_valid_and_has_no_trailing_prose(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(tmp_path, [_SOL])
    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path),
              "assets", "propose", "--from", str(shortlist), "--json"],
    )
    payload = json.loads(result.output)  # must parse cleanly
    assert payload["admitted_count"] == 0
    assert payload["screened"][0]["asset"] == "SOL"


def test_propose_missing_file_is_a_clean_error(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path),
              "assets", "propose", "--from", str(tmp_path / "nope.json")],
    )
    assert result.exit_code != 0


def test_propose_hypothesis_never_admits(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(
        tmp_path,
        [{"asset": "SOL", "rationale": "x", "sources": ["https://x.invalid"],
          "shariah_hypothesis": "definitely halal"}],
    )
    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path),
              "assets", "propose", "--from", str(shortlist)],
    )
    assert "REJECT" in result.output  # unattested + no history => rejected despite the hypothesis
    assert "UNVERIFIED" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/compliance/test_assets_cli.py -k propose -q`
Expected: FAIL — no such command `propose` (click usage error, non-zero exit).

- [ ] **Step 3: Add the command to `keel/cli.py`**

Confirm `import json` and `from pathlib import Path` exist near the top of `cli.py` (add whichever is missing). Then add this command directly after `assets_screen` (so it sits inside `assets_group`):

```python
@assets_group.command("propose")
@click.option(
    "--from", "from_file", required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON shortlist file produced OUTSIDE keel (an LLM + web-search scout).",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
@click.pass_context
def assets_propose(ctx: click.Context, from_file: str, as_json: bool) -> None:
    """Screen an externally-produced LLM asset shortlist. ADMITS NOTHING.

    The shortlist is produced outside keel (you, or your Claude + the firecrawl skills). Each
    candidate is routed through the SAME admission gate as `assets screen`; unattested or
    history-less candidates fail closed. This command never attests, never edits the allowlist,
    never writes to the DB -- it only reports verdicts and next steps.
    """
    from keel.proposer import (
        ProposalError,
        build_proposal_report,
        parse_proposal,
        render_proposal_report,
        report_to_jsonable,
    )

    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    try:
        raw = json.loads(Path(from_file).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"could not read/parse {from_file}: {exc}") from exc
    try:
        parsed = parse_proposal(raw)
    except ProposalError as exc:
        raise click.ClickException(str(exc)) from exc

    report = build_proposal_report(
        parsed, repo, config.quote_currency, config.allowlist, _screen_product
    )
    if as_json:
        click.echo(json.dumps(report_to_jsonable(report), indent=2, default=str))
        return
    for line in render_proposal_report(report):
        click.echo(line)
    click.echo("")
    click.echo(DISCLAIMER)
```

- [ ] **Step 4: Run the propose tests, then the whole suite + lint**

Run: `uv run pytest tests/compliance/test_assets_cli.py -k propose -q`
Expected: PASS (6 tests).

Run: `uv run pytest -q`
Expected: PASS (full suite green; new count = prior + all proposer/CLI tests).

Run: `uv run ruff check`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add keel/cli.py tests/compliance/test_assets_cli.py
git commit -m "feat(proposer): keel assets propose -- screen an LLM shortlist, admit nothing

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (author checklist — completed)

- **Spec coverage:** §3.1 command → Task 4; §3.2 schema + citation/hypothesis rules → Task 1; §3.3 shared gate (injected `_screen_product`, equivalence test) → Tasks 2 & 4; §3.4 module layout (`keel/proposer.py` pure + thin CLI) → Tasks 1-4; §3.5 no enable-flag → nothing to build (inert command); Testing section → tests across Tasks 1-4 (schema, shared-gate equivalence, admits-nothing, no-history, hypothesis-never-admits, `--json`). All covered.
- **Placeholder scan:** none — every step has real code and exact commands.
- **Type consistency:** `Candidate`/`InvalidEntry`/`ParsedProposal`/`ScreenedCandidate`/`ProposalReport`, `parse_proposal`/`build_proposal_report`/`render_proposal_report`/`report_to_jsonable`, and the `screen_fn` signature `(repo, product, quote) -> (MarketFacts, ScreenResult)` are consistent across tasks and match the real `_screen_product`/`ScreenResult`/`MarketFacts`.
- **One open verification for the implementer:** the attestation-insert method name used in the Task-3 test helper (`repo.attest_asset(...)`) must be confirmed against `keel/data/repository.py` and corrected to the actual name if different (keys: `asset/sector/backing/pays_yield/source/attested_by/attested_at`). Alternatively, attest via the CLI `_attest(...)` helper as the CLI tests do, to avoid depending on the internal name.
