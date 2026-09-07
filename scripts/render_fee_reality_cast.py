"""Record the fee-reality benchmark as a terminal asset, from a REAL run (#646).

The README already carries the benchmark as a table, rendered from the hash-chained trials ledger
by `render_fee_reality.py`. #646 also asks for a ~10-second terminal capture, under one constraint
that decides this script's whole shape:

    "Generation tooling committed ... so the GIF regenerates when the measurement does."

A hand-recorded screencast cannot satisfy that. It is a binary blob whose numbers are frozen at
the moment somebody hit record, and the first time the ledger changes it becomes a picture of a
measurement that is no longer true -- a marketing asset wearing a measurement's clothes, which is
the exact thing this project's README argues against.

── WHAT IS REAL HERE, AND WHAT IS NOT ────────────────────────────────────────────────────────────

**The output is real.** This runs `scripts/render_fee_reality.py` as a SUBPROCESS and captures its
actual stdout. That command really reads `docs/experiments/trials-ledger.jsonl`, really parses the
recorded fee curve, and really prints those figures. Nothing here retypes a number, and there is
no fixture: if the ledger changes, re-running this changes the asset.

**The pacing is not real, and pretending otherwise would be the lie.** Nobody types at a uniform
55 ms per character, and no command returns in exactly the beat that reads well. The typing rhythm
and the pause before the output are composed, the way any screencast's are. What that buys is a
deterministic asset -- byte-identical across runs and machines, so the diff of a regenerated
capture is the diff of the MEASUREMENT and never of when it was recorded or how fast someone typed.

── TWO FORMATS, ONE SOURCE ───────────────────────────────────────────────────────────────────────

`docs/assets/fee-reality.cast` -- asciinema v2. The interchange format: plain JSON lines, so a
reviewer can read exactly what frames were emitted, and any asciinema tool can play it.

`docs/assets/fee-reality.svg` -- an animated SVG, which is what the README embeds. It renders
inline on GitHub, needs no player, no CDN and no JavaScript, and it is TEXT: a regenerated capture
shows up in review as a legible diff rather than an opaque binary. A GIF would be none of those
things.

Run it:

    python scripts/render_fee_reality_cast.py --write

`tests/test_fee_reality_capture.py` fails when the committed assets and this script's output
disagree, so the capture cannot drift away from the measurement without the suite saying so --
the same guard `render_fee_reality.py` already has on the README block.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _ROOT / "docs" / "assets"
_CAST = _ASSETS / "fee-reality.cast"
_SVG = _ASSETS / "fee-reality.svg"

#: The command the capture shows, and actually runs.
_COMMAND = "python scripts/render_fee_reality.py"

#: Terminal geometry. 92 columns because the widest line the renderer emits is the source link and
#: wrapping it would put a URL fragment on a line of its own, which reads as a broken frame.
_COLS = 92
_ROWS = 34

#: Seconds per typed character, and the beat before output. Composed, not measured -- see the
#: module docstring on why that is stated rather than hidden.
_KEYSTROKE = 0.055
_THINK = 0.45

#: How long the finished frame holds before the asset loops. Long enough to read the last
#: paragraph, which is the one that says these are comparisons and never edge estimates.
_HOLD = 6.0

#: The caption burned into the first frame. DATED, and it names the fee basis -- #646 requires
#: both, because a benchmark without a date and a venue is a number with no way to check it.
_CAPTION = "keel · fee-reality benchmark · Coinbase taker 1.2% · rendered from the trials ledger"


@dataclass(frozen=True)
class Frame:
    """One asciinema event: `[elapsed, "o", data]`."""

    at: float
    data: str


def real_output() -> str:
    """Run the renderer and return what it actually printed.

    A subprocess rather than an import, deliberately: the asset is supposed to show a COMMAND
    being run, and importing the module would capture something no operator can reproduce by
    typing what the frame shows.
    """
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "render_fee_reality.py")],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"render_fee_reality.py exited {result.returncode}; refusing to record a capture of a "
            f"run that failed:\n{result.stderr}"
        )
    return result.stdout


def _frames(output: str) -> list[Frame]:
    """The typed command, the beat, and the real output -- as timed events."""
    frames: list[Frame] = [Frame(0.0, f"\u001b[90m# {_CAPTION}\u001b[0m\r\n"), Frame(0.35, "$ ")]
    at = 0.5
    for char in _COMMAND:
        frames.append(Frame(at, char))
        at += _KEYSTROKE
    frames.append(Frame(at, "\r\n"))
    at += _THINK
    # The output goes out a line at a time so a player shows it filling rather than appearing --
    # and so the cast reads, line by line, as the thing the command printed.
    for line in output.splitlines():
        frames.append(Frame(at, line + "\r\n"))
        at += 0.06
    return frames


def build_cast(output: str) -> str:
    """asciinema v2: a header object, then one JSON array per event."""
    frames = _frames(output)
    header = {
        "version": 2,
        "width": _COLS,
        "height": _ROWS,
        # NO `timestamp` field. asciinema puts the wall clock there, which would make every
        # regeneration a diff even when the measurement had not moved -- and the whole point of
        # committing this asset is that its diff means something.
        "env": {"TERM": "xterm-256color"},
        "title": _CAPTION,
    }
    lines = [json.dumps(header, sort_keys=True)]
    for frame in frames:
        lines.append(json.dumps([round(frame.at, 3), "o", frame.data]))
    return "\n".join(lines) + "\n"


# -- the SVG ---------------------------------------------------------------------------------------
#
# Hand-built rather than shelled out to a renderer, and the reason is the constraint again: a
# capture generated by a tool that is not in this repository cannot be regenerated by someone who
# clones it. `agg` and `svg-term-cli` both want a toolchain (Rust, npm) that keel does not have and
# should not acquire to draw a picture. What the terminal actually does here is narrow -- monospace
# text, one dim colour for the caption, a cursor, and a wipe down the output -- so the SVG is
# narrow too.

#: The palette. `--bg`/`--fg` are keel.css's own dark values, so the asset does not look like it
#: came from somewhere else.
_BG = "#0f1720"
_FG = "#d7dee6"
_DIM = "#7c8b9a"
_PROMPT = "#4fb3a5"

_CHAR_W = 8.4
_LINE_H = 19.0
_PAD = 16.0

#: Characters that must be escaped before they reach the SVG's text nodes. The renderer's output is
#: ledger-derived and contains `&` and `<` in prose; leaving either raw produces a document that is
#: not XML and that GitHub declines to render, silently.
_XML = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def _escape(text: str) -> str:
    for char, entity in _XML.items():
        text = text.replace(char, entity)
    return text


def _strip_ansi(text: str) -> str:
    """The cast carries one dim-grey escape for the caption; the SVG colours that line directly."""
    out: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "\u001b":
            end = text.find("m", index)
            index = len(text) if end == -1 else end + 1
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


def build_svg(output: str) -> str:
    """An animated SVG of the same run, for the README to embed.

    One `<text>` per line, each revealed by a `<set>` at the moment the cast emits it -- so the two
    assets are the same recording in two formats rather than two recordings that could disagree.
    """
    frames = _frames(output)
    total = frames[-1].at + _HOLD

    lines: list[tuple[float, str, str]] = []  # (at, text, colour)
    typed = ""
    for frame in frames:
        data = _strip_ansi(frame.data)
        if frame.data.startswith("\u001b[90m"):
            lines.append((frame.at, data.rstrip("\r\n"), _DIM))
        elif data == "$ ":
            typed = "$ "
        elif data == "\r\n" and typed:
            lines.append((0.5, typed, _PROMPT))
            typed = ""
        elif typed:
            typed += data
        else:
            lines.append((frame.at, data.rstrip("\r\n"), _FG))

    height = _PAD * 2 + _LINE_H * (len(lines) + 1)
    width = _PAD * 2 + _CHAR_W * _COLS
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" font-family="ui-monospace,SFMono-Regular,'
        f'Menlo,monospace" font-size="13">',
        # A title element, so a screen reader gets the point of the asset rather than "image".
        f"<title>{_escape(_CAPTION)}</title>",
        f'<rect width="100%" height="100%" rx="6" fill="{_BG}"/>',
    ]
    for index, (at, text, colour) in enumerate(lines):
        y = _PAD + _LINE_H * (index + 1)
        parts.append(
            f'<text x="{_PAD:.0f}" y="{y:.0f}" fill="{colour}" opacity="0">'
            f"{_escape(text)}"
            f'<set attributeName="opacity" to="1" begin="{at:.2f}s" dur="{total - at:.2f}s"/>'
            f"</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Write the assets; otherwise check they are current."
    )
    args = parser.parse_args(argv)

    output = real_output()
    cast, svg = build_cast(output), build_svg(output)

    if args.write:
        _ASSETS.mkdir(parents=True, exist_ok=True)
        _CAST.write_text(cast, encoding="utf-8")
        _SVG.write_text(svg, encoding="utf-8")
        print(f"wrote {_CAST.relative_to(_ROOT)} and {_SVG.relative_to(_ROOT)}")
        return 0

    stale = [
        path.relative_to(_ROOT)
        for path, expected in ((_CAST, cast), (_SVG, svg))
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]
    if stale:
        print(f"stale: {', '.join(str(p) for p in stale)} -- run with --write", file=sys.stderr)
        return 1
    print("assets match the ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
