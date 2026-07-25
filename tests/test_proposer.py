import json
from decimal import Decimal

import pytest

from keel.compliance import screen as screen_mod
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.proposer import (
    ParsedProposal,
    ProposalError,
    ProposalReport,
    build_proposal_report,
    parse_proposal,
    render_proposal_report,
    report_to_jsonable,
)


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


def _report(admitted, bars, attested=False, hypothesis=None):
    parsed = parse_proposal(
        {"candidates": [_entry(asset="SOL", shariah_hypothesis=hypothesis)]}
    )

    def screen_fn(repo, product, quote):
        facts = screen_mod.MarketFacts("SOL", bars, Decimal("0"), True)
        failures = (
            []
            if admitted
            else (
                ["history: too few bars"]
                if bars
                else ["liquidity: 0", "attestation: MISSING."]
            )
        )
        return facts, screen_mod.ScreenResult("SOL", admitted=admitted, failures=failures)

    repo = _repo()
    if attested:
        repo.upsert_asset_attestation(
            asset="SOL",
            sector="payments",
            backing="native",
            pays_yield=False,
            source="https://x.invalid",
            attested_by="t",
            attested_at=0,
        )
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
