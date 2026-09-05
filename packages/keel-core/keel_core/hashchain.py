"""One canonical form and one chain walk, for every hash-chained store in this codebase (#721).

Two stores chain their rows: the research trials ledger (`keel/research/ledger.py` -- JSONL, git
tracked, records experiments) and `audit_events` (`keel/data/audit.py` -- SQLite, per deployment,
records trading activity). They are deliberately separate stores over separate domains, and
blending them would be provenance laundering. What they must NOT have separately is a definition
of what a row's hash is over.

**Why this is a shared module and not a copied function.** A hash is only evidence if it can be
recomputed. Two canonicalisations that disagree -- one emitting `{"a": 1}` and the other
`{"a":1}`, one rendering `Decimal("1.0")` as `1.0` and the other as `"1.0"` -- produce two hashes
for one row. That disagreement is invisible at write time and surfaces months later as a chain
that "cannot be verified", at exactly the moment someone is trying to establish whether a record
was altered. The form is decided here, once.

**Tamper-EVIDENT, not tamper-proof.** Anyone who can write the store can rewrite the whole chain
from the edited row forward. What the chain buys is that a row cannot be changed QUIETLY: an
edit invalidates that row and every row after it, so a partial edit is visible and a full rewrite
requires touching every subsequent row.

This module holds no I/O, no `keel` imports and no schema. It is pure so both stores can depend
on it without depending on each other.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

#: What the FIRST row of a chain commits to. A chain anchored anywhere else starts mid-air: it
#: says nothing about whether rows were removed from in front of it, which is the one deletion a
#: chain would otherwise miss entirely.
ZERO_HASH = "0" * 64


def encode_value(value: Any) -> Any:
    """`Decimal` -> `str`, recursively, so JSON round-trips a money value exactly.

    Money is TEXT in every store here (`orders.qty`, `equity_points.equity`), and the reason is
    the same reason it is a string in the hash: a float rendering of `Decimal("0.10")` is a
    different number from the one the row holds, and a hash over a lossy rendering attests to a
    value that was never recorded.

    SCALE IS PRESERVED, and deliberately: `Decimal("1.0")` and `Decimal("1.00")` compare equal
    numerically and are different recorded values. A store that rewrote a row's scale changed
    that row, and the chain says so rather than shrugging.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {k: encode_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_value(v) for v in value]
    return value


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic serialisation: sorted keys, no incidental whitespace, Decimals as strings.

    Both the separators and the key ordering are pinned here rather than left to `json.dumps`
    defaults, because the hash is only reproducible if this is byte-stable. `sort_keys` in
    particular is not a tidiness preference: the same row is assembled from a `dict` literal in
    one module and from `dict(sqlite3.Row)` in another, and insertion order differs between them.

    An ABSENT key is not a null key. `{"a": 1}` and `{"a": 1, "b": None}` hash differently, which
    is correct -- a writer that started emitting an explicit null for a field it used to omit has
    changed what the row says.
    """
    return json.dumps(encode_value(dict(payload)), sort_keys=True, separators=(",", ":"))


def chain_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 over `canonical_json(payload)`.

    `payload` is everything the row commits to INCLUDING its `prev_hash` and excluding its own
    `row_hash`. Including `prev_hash` is what makes this a chain rather than a column of
    independent checksums: without it, a row could be moved, duplicated or reordered freely.
    """
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainLink:
    """One row, reduced to the four things a chain walk needs.

    Deliberately not a row: this module knows nothing about trials or audit events, and each
    store keeps its own record type, its own payload shape and its own reader. `recomputed_hash`
    is computed BY THE CALLER, because only the caller knows which of its fields are hashed --
    which is the fact that makes the check meaningful, and the fact this module must not guess.

    `label` is whatever identifies the row to a human reading a report -- a `trial_id`, a
    `seq_id`. It appears in the error text and nowhere else.
    """

    label: str
    prev_hash: str
    row_hash: str
    recomputed_hash: str


@dataclass(frozen=True)
class ChainBreak:
    """One place the chain fails, located as well as described.

    `index` is 1-based POSITION IN THE WALK, not any store's own identifier: the walk is the only
    thing that knows where a row sits in the chain, and a store whose rows were renumbered would
    otherwise report breaks at coordinates that no longer mean anything. Callers that need their
    own key look it up by this index -- see `keel/data/audit.py::chain_state`, which uses it to
    find the `seq_id` from which the chain stops being evidence.

    `reason` is one of `link` (a row was inserted, removed or reordered) or `content` (a row was
    edited in place). Two different accusations, and a report that blurred them would tell an
    auditor to look for the wrong thing.
    """

    index: int
    label: str
    reason: str
    message: str


def find_breaks(links: Iterable[ChainLink]) -> list[ChainBreak]:
    """Every break in the chain, located. Empty means intact.

    **Reports rather than raises.** A broken chain is a finding to surface in a report -- `keel
    doctor`, the timeline export, the research page's badge -- and the caller wants every break
    rather than only the first. A check that stopped at the first would let a second edit hide
    behind the first, which is the shape an auditor most needs to see.

    Two distinct failures, and the `elif` keeps them from doubling up: a row whose `prev_hash`
    does not match its predecessor's `row_hash` is a LINK failure, and a row whose content does
    not reproduce its own `row_hash` is a CONTENT failure. A row that fails the link check is
    reported once, on the link, because its content hash is then computed over a payload the walk
    already knows is in the wrong place.

    The walk continues from the row's STORED `row_hash` rather than the recomputed one, so a
    single edit produces one error rather than cascading into a false break at every later row.
    A DELETED row is what breaks every row after it, and that is the property these stores exist
    for.

    **An empty sequence returns no breaks, and that is not the same as "verified".** Nothing was
    checked. The distinction belongs to the caller, because only the caller knows whether empty
    means "no rows yet" or "no store at all" -- the difference between an honest badge and a
    green light over a file nothing read.
    """
    breaks: list[ChainBreak] = []
    expected_prev = ZERO_HASH
    for index, link in enumerate(links, start=1):
        if link.prev_hash != expected_prev:
            breaks.append(
                ChainBreak(
                    index=index,
                    label=link.label,
                    reason="link",
                    message=(
                        f"row {index} ({link.label}): prev_hash {link.prev_hash[:12]}... "
                        f"does not chain to {expected_prev[:12]}..."
                    ),
                )
            )
        elif link.recomputed_hash != link.row_hash:
            breaks.append(
                ChainBreak(
                    index=index,
                    label=link.label,
                    reason="content",
                    message=f"row {index} ({link.label}): content does not match row_hash",
                )
            )
        expected_prev = link.row_hash
    return breaks


def verify_links(links: Iterable[ChainLink]) -> list[str]:
    """`find_breaks`, as the human-readable lines a report prints. Empty means intact.

    A formatter over the one walk rather than a second walk: a chain check that existed twice is
    a chain check that can disagree with itself, and the disagreement would be between "the
    report says intact" and "the badge says broken" over the same rows.
    """
    return [found.message for found in find_breaks(links)]
