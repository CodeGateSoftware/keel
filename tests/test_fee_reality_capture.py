"""The fee-reality terminal capture cannot drift away from the measurement (#646).

The README block already has this guard (`test_fee_reality_block.py`). The capture needs it more,
not less: a table that disagrees with the ledger is a wrong number a reader can check against the
source link beside it, while a picture that disagrees with the ledger is a wrong number wearing a
recording's authority -- and nobody diffs a screencast.

#646's binding constraint is "generation tooling committed ... so the GIF regenerates when the
measurement does". These assert the tooling exists, that what it produces is what is committed, and
that what it captured was a REAL run rather than a fixture.
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.dom.minidom
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "render_fee_reality_cast.py"
_CAST = _ROOT / "docs" / "assets" / "fee-reality.cast"
_SVG = _ROOT / "docs" / "assets" / "fee-reality.svg"
_README = _ROOT / "README.md"


def test_the_committed_assets_match_what_the_ledger_renders_today() -> None:
    """The whole guard. Run the generator in check mode: it re-runs the real renderer against the
    real ledger and compares. A ledger change that nobody re-captured fails here."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, text=True, cwd=_ROOT, check=False
    )
    assert result.returncode == 0, (
        f"the committed capture disagrees with the ledger:\n{result.stdout}{result.stderr}"
    )


def test_the_capture_is_deterministic_so_its_diff_means_something() -> None:
    """No wall clock anywhere in it. asciinema writes a `timestamp` into its header by default,
    which would make every regeneration a diff even when the measurement had not moved -- and an
    asset whose diff is always noise is one nobody reads."""
    header = json.loads(_CAST.read_text(encoding="utf-8").splitlines()[0])
    assert "timestamp" not in header
    assert header["version"] == 2


def test_the_cast_is_a_valid_asciinema_v2_recording() -> None:
    lines = _CAST.read_text(encoding="utf-8").splitlines()
    json.loads(lines[0])
    for line in lines[1:]:
        at, stream, _data = json.loads(line)
        assert stream == "o"
        assert isinstance(at, (int, float))


def test_the_svg_is_well_formed_and_carries_a_title() -> None:
    """It renders inline on GitHub or it does nothing at all, and an untitled image is an image a
    screen reader announces as nothing."""
    document = xml.dom.minidom.parse(str(_SVG))
    assert document.documentElement.tagName == "svg"
    assert document.getElementsByTagName("title")[0].firstChild.data


def test_the_capture_carries_the_date_basis_and_the_fee_basis() -> None:
    """#646: "Dated captions; per-venue fee basis named in the frame." A benchmark without a venue
    and a basis is a number with no way to check it."""
    svg = _SVG.read_text(encoding="utf-8")
    assert "taker 1.2%" in svg
    assert "Coinbase" in svg
    assert "trials ledger" in svg


def test_the_capture_names_no_competitor() -> None:
    """#646's own constraint: generic fee-drag math, never what another product costs you. The
    trademark posture allows nominative use on a compare page; an animated callout is not that."""
    text = _SVG.read_text(encoding="utf-8") + _CAST.read_text(encoding="utf-8")
    for name in ("jesse", "freqtrade", "backtrader", "tradingview", "quantconnect"):
        assert name not in text.lower(), f"the capture names {name}"


def test_the_figures_in_the_capture_come_from_the_ledger() -> None:
    """Not "a number appears" -- the SAME numbers the ledger-rendered README block carries. A
    capture built from a fixture would satisfy every check above and none of the point."""
    rendered = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "render_fee_reality.py")],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=True,
    ).stdout
    svg = _SVG.read_text(encoding="utf-8")

    figures = [line for line in rendered.splitlines() if line.startswith("| BTC")]
    assert figures, "the renderer emitted no asset row -- this test would prove nothing"
    for cell in figures[0].split("|"):
        value = cell.strip()
        if value:
            assert value in svg, f"{value!r} is in the ledger's table and not in the capture"


def test_the_readme_embeds_the_capture_and_says_what_is_real_about_it() -> None:
    """The pacing is composed and the output is not, and the caption says so. A capture presented
    as an unedited recording would be a small lie in service of a page about not telling them."""
    text = _README.read_text(encoding="utf-8")
    assert "docs/assets/fee-reality.svg" in text
    assert "render_fee_reality_cast.py" in text
    assert "The pacing is composed" in text
    assert "trials-ledger.jsonl" in text
