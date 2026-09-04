"""CSV formula-injection defence for the activity export -- issue #703.

**This is the security control on the export, not a formatting nicety.** The file is meant to be
opened in Excel or Google Sheets by an auditor, a tax preparer, or the operator -- and both
applications EXECUTE a cell whose text begins with a formula trigger. Several columns in this
export carry text keel did not write: a transaction's `notes` (imported from a venue CSV), an
attestation's `source` and `attested_by` (typed by a human), a `product_id` and `rule_name`
(config). A cell reading `=SUM(...)` or `@…` in any of those runs when the file is opened.

The defence is OWASP's: prefix the cell with a single quote so the spreadsheet takes it as inert
text. It is applied to every text column rather than to a list of "risky" ones, because a
maintained list of which columns are attacker-influenced is exactly the thing that rots.
"""

from __future__ import annotations

import csv
import io

import pytest

from keel.commands.timeline import csv_safe


@pytest.mark.parametrize(
    "dangerous",
    [
        "=SUM(A1:A10)",
        "@malicious",
        "+1+1",
        "-1+1",
        "=cmd|' /C calc'!A0",
        "\tleading tab",
        "\rleading carriage return",
    ],
)
def test_a_formula_trigger_is_neutralised(dangerous: str) -> None:
    """Every trigger character OWASP names. `=` and `@` are the obvious ones; `+` and `-` are
    formulas too, and a leading tab or carriage return can re-open the parse in some
    spreadsheets."""
    escaped = csv_safe(dangerous)

    assert escaped.startswith("'"), f"{dangerous!r} was not neutralised"
    assert escaped[1:] == dangerous, "the original text must survive intact after the quote"


@pytest.mark.parametrize(
    "ordinary",
    ["BTC-USD", "turtle_breakout", "a note", "", "0.01", "reward: staking", "  spaced"],
)
def test_ordinary_text_is_left_exactly_as_it_is(ordinary: str) -> None:
    """The defence must not corrupt the record it protects. An audit export whose every cell
    gained a stray quote would be unusable as evidence of anything."""
    assert csv_safe(ordinary) == ordinary


@pytest.mark.parametrize(
    "dangerous",
    [" =SUM(A1:A10)", "   @malicious", "  -1+1"],
)
def test_leading_whitespace_does_not_smuggle_a_formula_past(dangerous: str) -> None:
    """A strict first-character test is defeated by one space, and `" =cmd|..."` is a legal
    `coinbase_id` out of an imported venue CSV that lands in a cell by itself. Google Sheets and
    LibreOffice trim leading whitespace before deciding whether a cell is a formula.

    The ORIGINAL text is what gets quoted: an audit record must not have its cells silently
    reformatted, only made inert.
    """
    escaped = csv_safe(dangerous)

    assert escaped.startswith("'"), f"{dangerous!r} slipped past the trigger check"
    assert escaped[1:] == dangerous, "the original text, unaltered, after the quote"


def test_a_negative_number_is_still_neutralised_and_still_readable() -> None:
    """`-12.30` is a formula trigger AND a real figure this export carries. Quoting it is the
    correct trade: a spreadsheet shows `-12.30` as text rather than evaluating it, and the value
    is still there to read and to re-import. Losing the minus sign would be worse than losing
    numeric typing."""
    assert csv_safe("-12.30") == "'-12.30"


def test_the_escape_survives_a_real_csv_round_trip() -> None:
    """The end-to-end property the acceptance criterion asks for: written by `csv.writer`, read
    back by `csv.reader`, the cell still carries its quote and its original text."""
    payload = '=HYPERLINK("http://evil","click")'
    buffer = io.StringIO()
    csv.writer(buffer).writerow([csv_safe(payload), csv_safe("BTC-USD")])

    row = next(csv.reader(io.StringIO(buffer.getvalue())))

    assert row[0] == "'" + payload
    assert row[1] == "BTC-USD"


def test_a_quote_and_a_comma_still_round_trip() -> None:
    """`csv.writer` owns quoting and escaping; `csv_safe` must not double-handle it. A note
    containing a comma and a double quote has to come back byte-identical."""
    payload = 'note with, a comma and a "quote"'
    buffer = io.StringIO()
    csv.writer(buffer).writerow([csv_safe(payload)])

    assert next(csv.reader(io.StringIO(buffer.getvalue())))[0] == payload


def test_none_becomes_an_empty_cell_not_the_word_none() -> None:
    """An absent value is an empty cell. `"None"` in an audit column is a value that looks like
    data and is not."""
    assert csv_safe(None) == ""
