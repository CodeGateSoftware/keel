"""Ledger + hash-chain tests (spec §4)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from keel.research import ledger


def _append(path, trial_id: str, **over):
    kwargs = dict(
        trial_id=trial_id,
        session="test-session",
        rule="turtle_breakout",
        params={"entry": 40, "exit": 20},
        provenance="fitted",
        kind="sweep_node",
        decision="selected",
        per_trade_pnl=[Decimal("1.5"), Decimal("-2")],
        per_bar_pnl=[Decimal("0.1")],
        summary={"sr_trade": Decimal("0.3"), "expectancy": Decimal("610"), "trade_count": 31},
        timestamp=1_700_000_000,
    )
    kwargs.update(over)
    return ledger.append_trial(path, **kwargs)


def test_first_row_chains_to_zero_hash(tmp_path):
    path = tmp_path / "trials.jsonl"
    row = _append(path, "t1")
    assert row.prev_hash == ledger.ZERO_HASH
    assert len(row.row_hash) == 64


def test_second_row_chains_to_first(tmp_path):
    path = tmp_path / "trials.jsonl"
    first = _append(path, "t1")
    second = _append(path, "t2")
    assert second.prev_hash == first.row_hash


def test_roundtrip_preserves_decimal_exactly(tmp_path):
    path = tmp_path / "trials.jsonl"
    _append(path, "t1", per_trade_pnl=[Decimal("0.1"), Decimal("0.2")])
    (row,) = ledger.read_trials(path)
    assert row.per_trade_pnl == [Decimal("0.1"), Decimal("0.2")]
    assert row.summary["expectancy"] == Decimal("610")


def test_verify_chain_passes_on_untampered_file(tmp_path):
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        _append(path, f"t{i}")
    assert ledger.verify_chain(path) == []


def test_naive_edit_is_caught_at_the_edited_row(tmp_path):
    """Editing content without recomputing `row_hash`: the row no longer hashes to itself."""
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        _append(path, f"t{i}")
    lines = path.read_text().splitlines()
    lines[1] = lines[1].replace('"expectancy":"610"', '"expectancy":"99999"')
    path.write_text("\n".join(lines) + "\n")

    errors = ledger.verify_chain(path)
    assert len(errors) == 1
    assert "row 2" in errors[0]
    assert "row_hash" in errors[0]


def test_recomputed_hash_is_caught_at_the_NEXT_row(tmp_path):
    """The chain's real work.

    A tamperer who knows to recompute `row_hash` defeats the per-row check -- and is caught
    anyway, because row 3's stored `prev_hash` still points at row 2's ORIGINAL hash. Hiding
    the edit fully would mean rewriting every subsequent row, which is the cost the chain
    exists to impose.
    """
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        _append(path, f"t{i}")

    trials = ledger.read_trials(path)
    forged = replace(trials[1], summary={**trials[1].summary, "expectancy": Decimal("99999")})
    forged = replace(forged, row_hash=ledger.compute_row_hash(forged))

    lines = path.read_text().splitlines()
    payload = ledger._row_payload(forged)
    payload["row_hash"] = forged.row_hash
    lines[1] = ledger.canonical_json(payload)
    path.write_text("\n".join(lines) + "\n")

    errors = ledger.verify_chain(path)
    assert len(errors) == 1
    assert "row 3" in errors[0]
    assert "does not chain" in errors[0]


def test_deletion_is_detected(tmp_path):
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        _append(path, f"t{i}")
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n" + lines[2] + "\n")
    assert ledger.verify_chain(path) != []


def test_rejects_unknown_enum_values(tmp_path):
    path = tmp_path / "trials.jsonl"
    with pytest.raises(ValueError, match="provenance"):
        _append(path, "t1", provenance="vibes")
    with pytest.raises(ValueError, match="kind"):
        _append(path, "t1", kind="hunch")
    with pytest.raises(ValueError, match="decision"):
        _append(path, "t1", decision="maybe")


def test_series_missing_row_may_omit_series(tmp_path):
    path = tmp_path / "trials.jsonl"
    row = _append(path, "t1", per_trade_pnl=[], per_bar_pnl=[], series_missing=True)
    assert row.series_missing is True
    assert row.per_bar_pnl == []


def test_series_missing_false_requires_a_series(tmp_path):
    path = tmp_path / "trials.jsonl"
    with pytest.raises(ValueError, match="series_missing"):
        _append(path, "t1", per_trade_pnl=[], per_bar_pnl=[], series_missing=False)


def test_trial_counts_splits_m_from_decision_count(tmp_path):
    path = tmp_path / "trials.jsonl"
    _append(path, "t1", decision="selected")
    _append(path, "t2", decision="rejected")
    _append(path, "t3", decision="diagnostic_only")
    m, n_decisions = ledger.trial_counts(ledger.read_trials(path))
    assert m == 3
    assert n_decisions == 2


def test_summary_null_value_decodes_without_raising(tmp_path):
    """#445: a summary value written as JSON null must not brick the ledger's READ-BACK.

    `_decode_summary` Decimals every non-int summary value, and Decimal(None) raises -- so
    ONE row carrying a null (a not-computable degradation, as a single-fold walk-forward
    run wrote before the CLI began omitting Nones) made every later read_trials/
    verify_chain of the append-only chain raise forever. The reader now passes null through
    untouched: the row reads back with None, rows after it still read, and the chain still
    verifies (null re-encodes to null, so the recomputed hash is unchanged)."""
    path = tmp_path / "trials.jsonl"
    bad = _append(
        path,
        "wf-single-fold",
        provenance="a_priori",
        kind="walk_forward",
        decision="diagnostic_only",
        summary={"n_folds": 1, "degradation": None},
    )
    assert bad.row_hash  # the write succeeds; the incident was always on read-back
    _append(path, "t-after")
    rows = ledger.read_trials(path)  # must not raise
    assert len(rows) == 2
    assert rows[0].summary["degradation"] is None
    assert rows[0].summary["n_folds"] == 1  # ints (and Decimals) still decode as before
    assert rows[1].summary["expectancy"] == Decimal("610")
    assert ledger.verify_chain(path) == []
