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
