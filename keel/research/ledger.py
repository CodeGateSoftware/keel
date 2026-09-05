"""Append-only, hash-chained trials ledger (spec §4).

Records *experiments*, never money: a row is "swept `donchian_entry_n` over {20,40,55},
selected 40, provenance `fitted`". The live trade record lives in `keel.db` and must never
come here -- see spec §2.1 for why git-tracking is safe only under that boundary.

Storage is JSONL so it is append-only by nature, diffable in review, and needs no migration
when the schema grows. `Decimal` is serialised as a string, matching the repo's TEXT-money
convention. Each row carries `prev_hash` (the previous row's `row_hash`) and its own
`row_hash`, so editing or deleting any row breaks verification for that row and every row
after it -- tamper-EVIDENT, not tamper-proof (spec §4.3).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

# The canonical form and the chain walk are SHARED with `keel/data/audit.py` (#721) rather
# than defined here. `canonical_json` and `ZERO_HASH` keep their names in this module's
# namespace because `tests/research/test_ledger.py` and every downstream reader reach for
# `ledger.canonical_json` -- the definition moved, the vocabulary did not.
from keel_core.hashchain import (
    ZERO_HASH,
    ChainLink,
    canonical_json,
    chain_hash,
    verify_links,
)

PROVENANCE = frozenset({"a_priori", "fitted"})
#: `monte_carlo` (#441) is a resampling DIAGNOSTIC row -- same vocabulary discipline as the
#: rest of the set: a row's kind says what kind of experiment produced it, so a bootstrap run
#: is not silently filed as an ablation or a sweep node it never was.
#: `walk_forward` (#445): one diagnostic row per fold of a rolling-origin validation of a
#: GIVEN parameter set -- never a selection among sets, so always `diagnostic_only`.
KINDS = frozenset(
    {
        "sweep_node",
        "ablation",
        "rule_retirement",
        "asset_prune",
        "threshold_nudge",
        "monte_carlo",
        "walk_forward",
    }
)
DECISIONS = frozenset({"selected", "rejected", "diagnostic_only"})

#: A CSCV column is a diagnostic, not a decision (spec §4.4) -- it does not count toward N.
DIAGNOSTIC_ONLY = "diagnostic_only"


DEFAULT_LEDGER_PATH = Path("docs/experiments/trials-ledger.jsonl")


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    timestamp: int
    session: str
    rule: str
    params: dict[str, Any]
    provenance: str
    kind: str
    decision: str
    per_trade_pnl: list[Decimal] = field(default_factory=list)
    per_bar_pnl: list[Decimal] = field(default_factory=list)
    series_missing: bool = False
    summary: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ZERO_HASH
    row_hash: str = ""


def _decode_series(raw: Any) -> list[Decimal]:
    return [Decimal(v) for v in (raw or [])]


def _decode_summary(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        # `trade_count` is a plain int; every other summary field is money/ratio -> Decimal.
        # None passes through untouched (#445): a null summary value (a not-computable
        # degradation, written before the walk-forward writer began OMITTING Nones) used to
        # raise Decimal(None) here, and one such row made every later read_trials/
        # verify_chain of the append-only chain raise forever. A reader must degrade this
        # row's value to None, never brick the ledger's read-back.
        if value is None or isinstance(value, int):
            out[key] = value
        else:
            out[key] = Decimal(value)
    return out


def _row_payload(record: TrialRecord) -> dict[str, Any]:
    """Everything that is hashed -- i.e. the row minus `row_hash` itself."""
    return {
        "trial_id": record.trial_id,
        "timestamp": record.timestamp,
        "session": record.session,
        "rule": record.rule,
        "params": record.params,
        "provenance": record.provenance,
        "kind": record.kind,
        "decision": record.decision,
        "per_trade_pnl": record.per_trade_pnl,
        "per_bar_pnl": record.per_bar_pnl,
        "series_missing": record.series_missing,
        "summary": record.summary,
        "prev_hash": record.prev_hash,
    }


def compute_row_hash(record: TrialRecord) -> str:
    """This row's hash, over `_row_payload` -- i.e. the row minus `row_hash` itself.

    The arithmetic moved to `keel_core.hashchain.chain_hash` in #721 so `audit_events` could
    share it. WHAT is hashed stayed here, because only this module knows which of a trial's
    fields are part of the record. The 93 rows in the git-tracked ledger were hashed before that
    move and still verify byte-for-byte -- pinned by
    `test_the_tracked_ledger_still_verifies_after_the_canonicaliser_moved`.
    """
    return chain_hash(_row_payload(record))


def _validate(record: TrialRecord) -> None:
    if record.provenance not in PROVENANCE:
        raise ValueError(f"provenance: {record.provenance!r} not in {sorted(PROVENANCE)}")
    if record.kind not in KINDS:
        raise ValueError(f"kind: {record.kind!r} not in {sorted(KINDS)}")
    if record.decision not in DECISIONS:
        raise ValueError(f"decision: {record.decision!r} not in {sorted(DECISIONS)}")
    if not record.series_missing and not (record.per_trade_pnl or record.per_bar_pnl):
        raise ValueError(
            "series_missing is False but no P&L series was supplied; a trial with no series "
            "must say so explicitly (spec §4.6) so the CSCV matrix can refuse it"
        )


def _last_hash(path: Path) -> str:
    if not path.exists():
        return ZERO_HASH
    last = ZERO_HASH
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = json.loads(line)["row_hash"]
    return last


def append_trial(
    path: Path | str,
    *,
    trial_id: str,
    session: str,
    rule: str,
    params: Mapping[str, Any] | None = None,
    provenance: str,
    kind: str,
    decision: str,
    per_trade_pnl: Sequence[Decimal] = (),
    per_bar_pnl: Sequence[Decimal] = (),
    series_missing: bool = False,
    summary: Mapping[str, Any] | None = None,
    timestamp: int | None = None,
) -> TrialRecord:
    """Validate, chain and append one trial. Returns the stored record."""
    path = Path(path)
    record = TrialRecord(
        trial_id=trial_id,
        timestamp=int(time.time()) if timestamp is None else timestamp,
        session=session,
        rule=rule,
        params=dict(params or {}),
        provenance=provenance,
        kind=kind,
        decision=decision,
        per_trade_pnl=list(per_trade_pnl),
        per_bar_pnl=list(per_bar_pnl),
        series_missing=series_missing,
        summary=dict(summary or {}),
        prev_hash=_last_hash(path),
    )
    _validate(record)
    record = replace(record, row_hash=compute_row_hash(record))

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _row_payload(record)
    payload["row_hash"] = record.row_hash
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(payload) + "\n")
    return record


def read_trials(path: Path | str) -> list[TrialRecord]:
    path = Path(path)
    if not path.exists():
        return []
    trials: list[TrialRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            trials.append(
                TrialRecord(
                    trial_id=raw["trial_id"],
                    timestamp=raw["timestamp"],
                    session=raw["session"],
                    rule=raw["rule"],
                    params=raw["params"],
                    provenance=raw["provenance"],
                    kind=raw["kind"],
                    decision=raw["decision"],
                    per_trade_pnl=_decode_series(raw.get("per_trade_pnl")),
                    per_bar_pnl=_decode_series(raw.get("per_bar_pnl")),
                    series_missing=raw.get("series_missing", False),
                    summary=_decode_summary(raw.get("summary")),
                    prev_hash=raw["prev_hash"],
                    row_hash=raw["row_hash"],
                )
            )
    return trials


def verify_chain(path: Path | str) -> list[str]:
    """Return a list of human-readable chain errors for the ledger at `path`; empty means intact.

    Reports rather than raises: a broken chain is a finding to surface in a report, and the
    caller usually wants every break rather than only the first.

    Reads the file itself, which is right for `keel trials verify` and wrong for a caller that has
    already read it -- see `verify_records`.
    """
    return verify_records(read_trials(path))


def verify_records(records: Iterable[TrialRecord]) -> list[str]:
    """The chain check over records already in memory. `verify_chain` is this plus a read.

    Split out for the callers that have the rows in hand. `keel/commands/research_record.py` reads
    the ledger for its rows and then needs a verdict about THOSE rows: going back through
    `verify_chain` parsed the same file a second time, so the badge on the page described a
    different read of the file from the rows beside it -- and a trial appended between the two
    would have the verdict cover a row the page never showed.

    **An empty sequence returns no errors, and that is not the same as "verified".** Nothing was
    checked. The distinction belongs to the caller, because only the caller knows whether empty
    means "no trials yet" or "no ledger at all" -- `keel/web/payload.py::_chain_payload` is where
    it is held for the web view, and it is the difference between an honest badge and a green
    light over a file nothing read.
    """
    return verify_links(
        ChainLink(
            label=record.trial_id,
            prev_hash=record.prev_hash,
            row_hash=record.row_hash,
            recomputed_hash=compute_row_hash(record),
        )
        for record in records
    )


def trial_counts(trials: Iterable[TrialRecord]) -> tuple[int, int]:
    """`(M, N_decisions)` -- spec §4.4. Diagnostics count toward M but not toward N."""
    materialised = list(trials)
    return len(materialised), sum(1 for t in materialised if t.decision != DIAGNOSTIC_ONLY)
