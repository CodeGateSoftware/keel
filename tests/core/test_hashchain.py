"""The one canonical form and the one chain walk (#721).

Two stores hash rows in this codebase -- the research trials ledger (JSONL, on disk, git-tracked)
and `audit_events` (SQLite, per deployment). They must agree about what canonical JSON means,
because two canonicalisations that disagree produce two hashes for one row, and the disagreement
does not surface as a bug: it surfaces, months later, as a chain that "cannot be verified".

So the form lives here, once, and both stores import it. These tests pin the properties the
hashes depend on rather than the hashes themselves -- with one deliberate exception, the tracked
ledger's own 93 rows (`tests/research/test_ledger.py`), which were hashed before this module
existed and must still verify byte-for-byte.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core import hashchain


def _link(label: str, prev: str, payload: dict[str, object]) -> hashchain.ChainLink:
    body = dict(payload)
    body["prev_hash"] = prev
    row_hash = hashchain.chain_hash(body)
    return hashchain.ChainLink(
        label=label, prev_hash=prev, row_hash=row_hash, recomputed_hash=row_hash
    )


def test_canonical_json_is_insertion_order_blind() -> None:
    """Key order in the caller's dict must not reach the hash.

    A payload assembled by a `dict` literal in one module and by `dict(row)` out of sqlite in
    another would otherwise hash differently while describing the same row.
    """
    assert hashchain.canonical_json({"b": 1, "a": 2}) == hashchain.canonical_json(
        {"a": 2, "b": 1}
    )


def test_canonical_json_has_no_incidental_whitespace() -> None:
    """`json.dumps` defaults put a space after every separator. Pinned, because a later reader
    "tidying" the call would silently invalidate every hash ever written."""
    assert hashchain.canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_decimal_crosses_as_its_exact_string_at_any_depth() -> None:
    """Money is TEXT everywhere in this codebase, and a hash over a float would be a hash over a
    lossy rendering of the number the row actually holds."""
    text = hashchain.canonical_json(
        {"qty": Decimal("0.10"), "legs": [Decimal("1.5")], "nested": {"fee": Decimal("2.000")}}
    )
    assert text == '{"legs":["1.5"],"nested":{"fee":"2.000"},"qty":"0.10"}'


def test_decimal_scale_is_part_of_the_hash() -> None:
    """`Decimal("1.0") == Decimal("1.00")` numerically, and they are DIFFERENT recorded values.
    A store that rewrote a row's scale changed the row, and the chain must say so."""
    assert hashchain.chain_hash({"qty": Decimal("1.0")}) != hashchain.chain_hash(
        {"qty": Decimal("1.00")}
    )


def test_chain_hash_changes_when_any_field_changes() -> None:
    base = {"a": 1, "b": "x"}
    assert hashchain.chain_hash(base) != hashchain.chain_hash({"a": 1, "b": "y"})
    assert hashchain.chain_hash(base) != hashchain.chain_hash({"a": 2, "b": "x"})


def test_an_absent_key_is_not_a_null_key() -> None:
    """The absent-vs-null distinction the ledger's docstring names. A writer that started
    emitting an explicit `None` for a field it used to omit has changed the row."""
    assert hashchain.chain_hash({"a": 1}) != hashchain.chain_hash({"a": 1, "b": None})


def test_an_intact_chain_reports_nothing() -> None:
    first = _link("one", hashchain.ZERO_HASH, {"n": 1})
    second = _link("two", first.row_hash, {"n": 2})
    assert hashchain.verify_links([first, second]) == []


def test_a_first_row_not_anchored_to_zero_is_a_break() -> None:
    """A chain that starts mid-air proves nothing about what came before it."""
    orphan = _link("one", "f" * 64, {"n": 1})
    errors = hashchain.verify_links([orphan])
    assert len(errors) == 1
    assert "row 1 (one)" in errors[0]
    assert "does not chain" in errors[0]


def test_edited_content_fails_at_that_row() -> None:
    first = _link("one", hashchain.ZERO_HASH, {"n": 1})
    tampered = hashchain.ChainLink(
        label=first.label,
        prev_hash=first.prev_hash,
        row_hash=first.row_hash,
        recomputed_hash=hashchain.chain_hash({"n": 99, "prev_hash": first.prev_hash}),
    )
    errors = hashchain.verify_links([tampered])
    assert len(errors) == 1
    assert "row 1 (one)" in errors[0]
    assert "row_hash" in errors[0]


def test_every_break_is_reported_not_only_the_first() -> None:
    """A chain check that stopped at the first break would let the second edit hide behind the
    first, which is the shape an auditor most needs to see."""
    first = _link("one", "a" * 64, {"n": 1})
    second = _link("two", "b" * 64, {"n": 2})
    errors = hashchain.verify_links([first, second])
    assert len(errors) == 2


def test_a_deleted_row_breaks_every_row_after_it() -> None:
    """The property that makes this evidence rather than a report."""
    first = _link("one", hashchain.ZERO_HASH, {"n": 1})
    second = _link("two", first.row_hash, {"n": 2})
    third = _link("three", second.row_hash, {"n": 3})
    errors = hashchain.verify_links([first, third])
    assert len(errors) == 1
    assert "row 2 (three)" in errors[0]


def test_an_empty_chain_reports_nothing_and_that_is_not_verified() -> None:
    """Nothing was checked. The caller holds the distinction between "no rows yet" and "no store
    at all" -- see `verify_links`' own docstring."""
    assert hashchain.verify_links([]) == []


def test_a_break_is_located_as_well_as_described() -> None:
    """`verify_links` prints; `find_breaks` locates. A caller that needs its own key for the
    first unverified row -- the timeline's chain-status column -- must not have to parse the
    English out of a message to find it."""
    first = _link("one", hashchain.ZERO_HASH, {"n": 1})
    second = _link("two", first.row_hash, {"n": 2})
    edited = hashchain.ChainLink(
        label=second.label,
        prev_hash=second.prev_hash,
        row_hash=second.row_hash,
        recomputed_hash=hashchain.chain_hash({"n": 99, "prev_hash": second.prev_hash}),
    )
    breaks = hashchain.find_breaks([first, edited])
    assert [(found.index, found.label, found.reason) for found in breaks] == [(2, "two", "content")]


def test_a_missing_row_is_a_link_break_not_a_content_break() -> None:
    """Two different accusations. A report that blurred them would send an auditor looking for an
    edited row when what happened was a deletion."""
    first = _link("one", hashchain.ZERO_HASH, {"n": 1})
    second = _link("two", first.row_hash, {"n": 2})
    third = _link("three", second.row_hash, {"n": 3})
    assert [found.reason for found in hashchain.find_breaks([first, third])] == ["link"]


def test_verify_links_is_exactly_find_breaks_formatted() -> None:
    """One walk, not two. A chain check that existed twice could disagree with itself, and the
    disagreement would be between a report and a badge over the same rows."""
    first = _link("one", "a" * 64, {"n": 1})
    second = _link("two", "b" * 64, {"n": 2})
    assert hashchain.verify_links([first, second]) == [
        found.message for found in hashchain.find_breaks([first, second])
    ]
