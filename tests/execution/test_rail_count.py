"""The rail count is DERIVED from `guards.check`, and every claim about it must agree.

It drifted three times before this file existed. Rails 19, 20 and 21 each arrived with a sweep
that updated the spelling the author happened to grep for -- "nineteen" when the stale text said
"eighteen", and never the digits -- so `guards.py` said twenty while `README.md` said 18 and
`executor.py` said eighteen, at the same commit.

A comment cannot enforce this and a checklist did not. The count is read out of the source of
truth (the numbered rails in `guards.check`) and every English and numeric spelling of it is
searched for across the repository, so a rail added tomorrow fails here rather than leaving a
document quietly wrong for three releases.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_GUARDS = _ROOT / "keel/execution/guards.py"

#: Number words this project would plausibly write a rail count as. Deliberately wider than the
#: current count in both directions: the failure mode is a STALE claim, so the search has to see
#: the numbers nobody expects to find.
_WORDS = {
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
    20: "twenty", 21: "twenty-one", 22: "twenty-two",
}

#: Where a count claim can hide. Experiment records, presentations, specs and research notes are
#: EXCLUDED: they are dated statements about what was true when they were written, and rewriting
#: one would be falsifying a record rather than fixing a document.
#:
#: `docs/research/2026-08-20-quant-lab-note-cross-verification.md` is the worked example. It
#: records what a third party's August-2026 note claimed about keel and what we verified --
#: "eighteen rails" and "four rule families" were both TRUE when it was checked, and both are
#: stale now. Updating it would silently rewrite a verification nobody re-ran.
_SEARCHED = ("README.md", "README.ar.md", "docs", "keel")
_EXCLUDED = (
    "docs/experiments",
    "docs/presentations",
    "docs/superpowers",
    "docs/research",
)


def rail_numbers() -> list[int]:
    """The numbered rails `guards.check` actually implements -- THE source of truth."""
    return [
        int(m.group(1))
        for m in re.finditer(r"^    # (\d+)\. ", _GUARDS.read_text(encoding="utf-8"), re.M)
    ]


def _files():
    for entry in _SEARCHED:
        path = _ROOT / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.suffix not in (".md", ".py") or not child.is_file():
                    continue
                rel = child.relative_to(_ROOT).as_posix()
                if any(rel.startswith(skip) for skip in _EXCLUDED):
                    continue
                yield child


def test_the_rails_are_numbered_contiguously_except_the_one_that_was_retired() -> None:
    """1-14 and 16-21. Rail 15 does not exist and its absence is deliberate -- every document
    that states the count says so, and a renumbering that quietly filled the gap would make
    every historical reference to a rail number wrong."""
    numbers = rail_numbers()
    assert numbers == sorted(numbers), f"rails are out of order: {numbers}"
    assert len(set(numbers)) == len(numbers), f"a rail number is used twice: {numbers}"
    assert 15 not in numbers, "rail 15 is retired; reusing the number would rewrite history"

    # EXACTLY 1..max minus the retired 15 -- not merely "sorted, unique and starting at 1".
    # A mutation renumbering rail 21 to 22 passed all three of those: the count was unchanged,
    # the order held, and 15 was still absent, while a silent gap opened at the top. A gap is
    # the shape this drifts in, so the assertion has to be about the SET.
    expected = [n for n in range(1, max(numbers) + 1) if n != 15]
    assert numbers == expected, (
        f"the rail numbers have a gap: {numbers}. Rail 15 is the ONE deliberate absence; any "
        "other missing number means a rail was renumbered or removed without renumbering the "
        "rest, and every document citing a rail by number is now ambiguous."
    )


def test_every_rail_count_claim_in_the_repository_is_current() -> None:
    """The pin. A stale count is not cosmetic: `README.md` is where a stranger learns what keel
    enforces, and a number two behind understates the machinery by exactly the rails most
    recently added -- the compliance ones."""
    expected = len(rail_numbers())
    current = _WORDS[expected]
    wrong = {n: w for n, w in _WORDS.items() if n != expected}
    stale: list[str] = []

    for path in _files():
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            for number, word in wrong.items():
                # `<word> rails` and `the <n> rails` -- the two shapes this repository writes.
                if re.search(rf"\b{word}\b[^.]{{0,40}}\brails?\b", lowered) or re.search(
                    rf"\bthe {number}\b[^.]{{0,20}}\brails?\b", lowered
                ):
                    rel = path.relative_to(_ROOT).as_posix()
                    stale.append(f"{rel}:{line_no}: {line.strip()[:88]}")

    assert not stale, (
        f"{len(stale)} rail-count claim(s) disagree with `guards.check`, which implements "
        f"{expected} rails ({current}):\n  " + "\n  ".join(stale)
    )
