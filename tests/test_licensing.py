"""The repo's licence: one grant, stated where people and tools look for it.

The repository has been public from the start with no `LICENSE` file, which under copyright law
means all rights reserved: nobody may legally fork it, modify it, or contribute to it, and a
contributor who does has no rights to grant back. That blocks every remaining open-source phase
-- not as a matter of politeness but of law -- and it is invisible: the build is green, the repo
is readable, and every right is nonetheless reserved.

The decision is Apache-2.0, over AGPL-3.0 and MIT, for two reasons that matter for software that
moves money: the explicit patent grant, and a warranty disclaimer written for exactly this case.
The reasoning lives in `CONTRIBUTING.md` rather than only in a merged PR, so the choice is
challengeable in place instead of implicit.

These tests are a repo-hygiene check like `test_packaging.py`'s: they read the source tree, and
they exist because the failure mode is silent. A licence that drifts (a seventh distribution
added without the field, an appendix left filled with placeholders) produces a repo that looks
open and is not, which is worse than one that admits it is closed.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

#: The canonical Apache-2.0 text opens with this banner, fixed by the licence itself.
_APACHE_BANNER = (
    "                                 Apache License\n"
    "                           Version 2.0, January 2004\n"
    "                        http://www.apache.org/licenses/"
)

#: Everything above this line is the licence's own text and must never be edited; everything
#: below is the appendix the copyright holder fills in.
_END_OF_TERMS = "END OF TERMS AND CONDITIONS"


def _license_text() -> str:
    return (_ROOT / "LICENSE").read_text()


def _pyprojects() -> dict[str, dict]:
    """distribution name -> parsed `pyproject.toml`, for the root and every workspace member."""
    found = {}
    for path in [_ROOT / "pyproject.toml", *sorted((_ROOT / "packages").glob("*/pyproject.toml"))]:
        data = tomllib.loads(path.read_text())
        found[data["project"]["name"]] = data
    return found


def test_the_license_file_exists_and_is_canonical_apache_2_0():
    """`LICENSE` at the root must be the real Apache-2.0 text, not a paraphrase of it.

    Only the appendix is ours to fill. The banner and the END OF TERMS marker together bracket
    the canonical text, so a different licence pasted in (MIT is the usual accident) fails even
    though the file exists.
    """
    text = _license_text()
    assert text.startswith(_APACHE_BANNER), "LICENSE does not open with the Apache-2.0 banner"
    assert _END_OF_TERMS in text, "LICENSE has no 'END OF TERMS AND CONDITIONS' marker"


def test_the_appendix_names_the_copyright_holder_and_no_placeholders_survive():
    """The appendix must be filled in. An unfilled one is the classic licence mistake.

    `[yyyy] [name of copyright owner]` left in the file grants nothing to anyone: a year is not
    a date and a bracketed placeholder is not a person. GitHub still detects the licence, so
    everything looks configured while the legal line is blank.
    """
    appendix = _license_text().split(_END_OF_TERMS, 1)[1]
    assert re.search(r"Copyright\s+2026\s+CodeGate Software", appendix), (
        "the Apache-2.0 appendix must name the copyright holder and year (2026, CodeGate Software)"
    )
    # The appendix's how-to-apply instructions quote brackets legitimately; the failure to catch
    # is a copyright line that was never filled in at all.
    assert "Copyright [yyyy]" not in appendix, (
        "the appendix still carries the unfilled 'Copyright [yyyy] [name of copyright owner]' "
        "template line -- an unfilled appendix grants nothing"
    )


@pytest.mark.parametrize("name", sorted(_pyprojects()))
def test_every_distribution_declares_the_same_spdx_licence(name):
    """All six distributions must carry `license = "Apache-2.0"` (PEP 639 SPDX form).

    The string form is deliberate: the deprecated table form (`{ text = ... }`) lands in wheel
    metadata as a free-text `License:` field, while the SPDX expression becomes
    `License-Expression:`, which is what license-scanning tooling reads. A seventh distribution
    added without the field ships as all-rights-reserved metadata beside permissively-licensed
    siblings -- the same mixed-signal failure the version pins in `test_packaging.py` guard
    against, one directory over.
    """
    declared = _pyprojects()[name]["project"].get("license")
    assert declared == "Apache-2.0", (
        f"{name} declares {declared!r}; every distribution cut from this repo must state "
        "`license = \"Apache-2.0\"` (SPDX string, not the deprecated table form)"
    )


def test_the_licence_decision_is_recorded_where_it_can_be_challenged():
    """Why Apache-2.0 (and not AGPL-3.0 or MIT) must live in `CONTRIBUTING.md`, not in history.

    A licence chosen implicitly cannot be revisited explicitly: the reasoning that ruled out the
    alternatives exists only in a merged PR's discussion, which nobody reading the repo years
    later will find. #277's acceptance asks for exactly this -- decision and reasoning recorded,
    not left implicit.
    """
    contributing = _ROOT / "CONTRIBUTING.md"
    assert contributing.is_file(), "no CONTRIBUTING.md; the licence decision is implicit"
    text = contributing.read_text()
    assert "Apache-2.0" in text
    assert "AGPL" in text and "MIT" in text, (
        "the recorded decision must say why Apache-2.0 won over the alternatives (AGPL-3.0, MIT), "
        "or it is a conclusion without its reasoning"
    )
