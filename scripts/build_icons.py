"""Generate keel's app icons (#538) from one geometry, with no dependencies.

The PWA manifest needs raster icons, and a repository that commits PNGs without saying where
they came from has committed four files nobody can review or reproduce. So the mark is defined
ONCE below as plain geometry, and both the SVG and every PNG are emitted from it:
`scripts/build_icons.py --check` re-renders and compares bytes, which
`tests/web/test_icons.py` runs on every build. A hand-edited PNG fails; a changed shape has to
be changed here, where the change is readable in a diff.

**Why hand-rolled rasterisation rather than Pillow.** keel's web surface ships zero JavaScript
dependencies on purpose (the design spec's §2), and the same argument applies with more force to
a build-time dependency that exists to draw three strokes: the whole rasteriser is a
point-in-polygon test and a `zlib.compress`, both stdlib, and the output is deterministic across
platforms in a way "whatever Pillow does with anti-aliasing this release" is not. Byte-identical
output is what makes `--check` a test rather than a suggestion.

Release tooling: deliberately NOT shipped in the wheel.
"""

from __future__ import annotations

import argparse
import struct
import zlib
from math import hypot
from pathlib import Path

#: Where the generated icons land, inside the served static tree.
ICON_DIR = Path(__file__).resolve().parent.parent / "keel" / "web" / "static" / "icons"

#: The two brand colours, taken from `keel/web/static/css/keel.css`'s light palette so the
#: installed app's tile matches the page it opens. `--accent` (#1a5578) rather than `--fg`: the
#: mark has to survive being shrunk to a 16px favicon and sitting on an unknown desktop
#: background, and a near-black square is indistinguishable from every other near-black square.
BACKGROUND = (0x1A, 0x55, 0x78, 0xFF)
FOREGROUND = (0xFB, 0xFA, 0xF8, 0xFF)


def _stroke(
    start: tuple[float, float], end: tuple[float, float], width: float
) -> tuple[tuple[float, float], ...]:
    """A straight stroke of `width` as a four-point polygon, with butt caps.

    The mark below is three strokes, so it is written as three strokes rather than as twelve
    hand-computed corners: a letterform whose geometry is spelled out corner by corner is one
    nobody can adjust later without re-deriving the perpendiculars by hand, and every adjustment
    to a monogram is a nudge.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = hypot(dx, dy)
    # A zero-length stroke has no perpendicular. It is a caller error rather than a shape, and
    # returning an empty polygon here would silently drop a limb of the letter instead.
    if length == 0:
        raise ValueError("a stroke needs two distinct points")
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    return (
        (start[0] + nx, start[1] + ny),
        (end[0] + nx, end[1] + ny),
        (end[0] - nx, end[1] - ny),
        (start[0] - nx, start[1] - ny),
    )


#: The mark: a lowercase `k`, in a 0..1 unit square with y increasing downward.
#:
#: **A monogram rather than a picture of a keel, and that was decided by looking.** Three
#: nautical marks were drawn and rendered first -- a hull in section over a keel, a hull in
#: profile with a fin, and a bulb keel -- and each one read as something else at icon size: a
#: funnel, a letter T, and an exclamation mark on a saucer. A launcher tile is 32-48 CSS pixels
#: on a background nobody chose, and at that size a silhouette gets one reading, which is not
#: necessarily the one it was drawn with. The name is unambiguous at every size, and this
#: application's whole argument is that a surface should say plainly what it is.
#:
#: Every coordinate is a fraction, so the SAME numbers render at 16px and at 512px. Sizes are
#: not special-cased and there is no hinting: a mark that needs different geometry at small
#: sizes is a mark with too much in it, and the fix is fewer shapes rather than more code.
_STEM = _stroke((0.305, 0.115), (0.305, 0.885), 0.150)
#: The arm and the leg meet at ONE point, and that point is INSIDE the stem rather than on its
#: right edge: butt caps that meet on the edge leave a small wedge of background at the
#: junction -- visible at 512px, and at 32px it reads as a broken letter rather than as a nick.
#: Ending both strokes inside the stem lets the stem cover the joint.
_JUNCTION = (0.335, 0.600)
_ARM = _stroke((0.755, 0.300), _JUNCTION, 0.140)
_LEG = _stroke(_JUNCTION, (0.775, 0.885), 0.140)

SHAPES: tuple[tuple[tuple[float, float], ...], ...] = (_STEM, _ARM, _LEG)

#: The safe-area inset a maskable icon is judged against. Android may crop a maskable icon to
#: any shape inside the middle 80% -- a circle, a squircle, a rounded square -- so the mark is
#: scaled to sit inside that circle rather than merely inside the square. Getting this wrong is
#: invisible on the developer's own launcher and clips the icon on somebody else's.
MASKABLE_SCALE = 0.72

#: Samples per axis inside each pixel. 3 means nine coverage tests per pixel, which is enough to
#: keep the letter's diagonals from stepping visibly at 192px and cheap enough that all four
#: icons render in well under a second.
_SUPERSAMPLE = 3

#: What gets written, and what `--check` compares against. `any` and `maskable` are separate
#: files rather than one file declared as both: a maskable icon has 20% padding by construction,
#: so declaring it `any` too puts a small mark in a big box everywhere the safe area is not
#: cropped -- the commonest way an install looks slightly wrong for no visible reason.
TARGETS: tuple[tuple[str, int, bool], ...] = (
    ("keel-192.png", 192, False),
    ("keel-512.png", 512, False),
    ("keel-maskable-512.png", 512, True),
)

SVG_NAME = "keel.svg"


def _inside(polygon: tuple[tuple[float, float], ...], x: float, y: float) -> bool:
    """Even-odd point-in-polygon. Ray-casts to the right and counts crossings.

    The `!=` on the two comparisons is what makes a vertex on the ray count once rather than
    twice or zero times -- the classic crossing-number test, kept verbatim rather than
    "simplified", because every simplification of it drops a boundary case.
    """
    inside = False
    count = len(polygon)
    for index in range(count):
        x0, y0 = polygon[index]
        x1, y1 = polygon[(index - 1) % count]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def _blend(coverage: float) -> tuple[int, int, int, int]:
    """Foreground over background at `coverage`, rounded half-up.

    Composited here rather than left as a transparent foreground over a transparent background:
    the icon is opaque by design (a manifest icon with alpha gets an arbitrary backdrop from
    whatever is behind it), so every pixel is a straight mix of two known colours.
    """
    return tuple(  # type: ignore[return-value]
        int(back + (fore - back) * coverage + 0.5)
        for back, fore in zip(BACKGROUND, FOREGROUND, strict=True)
    )


def render(size: int, *, maskable: bool) -> bytes:
    """One icon as PNG bytes."""
    scale = MASKABLE_SCALE if maskable else 1.0
    offset = (1.0 - scale) / 2.0
    shapes = tuple(
        tuple((x * scale + offset, y * scale + offset) for x, y in shape) for shape in SHAPES
    )

    rows: list[bytes] = []
    step = 1.0 / (size * _SUPERSAMPLE)
    for row in range(size):
        pixels = bytearray()
        for column in range(size):
            hits = 0
            for sub_y in range(_SUPERSAMPLE):
                y = (row * _SUPERSAMPLE + sub_y + 0.5) * step
                for sub_x in range(_SUPERSAMPLE):
                    x = (column * _SUPERSAMPLE + sub_x + 0.5) * step
                    if any(_inside(shape, x, y) for shape in shapes):
                        hits += 1
            pixels.extend(_blend(hits / (_SUPERSAMPLE * _SUPERSAMPLE)))
        rows.append(bytes(pixels))
    return _png(size, rows)


def _png(size: int, rows: list[bytes]) -> bytes:
    """RGBA8 PNG, filter 0 on every scanline.

    No filtering (`0` = None) rather than the adaptive heuristic a full encoder uses: these are
    flat-colour images where filtering buys a few hundred bytes, and a fixed filter is one fewer
    thing for `--check` to have to reproduce identically.
    """
    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(raw, 9)),
            _chunk(b"IEND", b""),
        )
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return b"".join(
        (
            struct.pack(">I", len(payload)),
            kind,
            payload,
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF),
        )
    )


def render_svg() -> bytes:
    """The same mark as SVG, for the manifest's `any` entry and anywhere a vector is better.

    Emitted from `SHAPES` rather than hand-written beside it, so the vector and the rasters
    cannot drift: a change to the hull that forgot the SVG would otherwise ship an icon that
    disagrees with itself depending on which size the launcher picked.

    `viewBox="0 0 1 1"` lets the unit coordinates go in verbatim. No `<script>`, no external
    reference: `server._STATIC_CSP` covers `image/svg+xml` precisely because SVG is active
    content, and an icon is the last place that should be exercised.
    """
    background = "#{:02x}{:02x}{:02x}".format(*BACKGROUND[:3])
    foreground = "#{:02x}{:02x}{:02x}".format(*FOREGROUND[:3])
    paths = "".join(
        f'<polygon points="{points}"/>'
        for points in (" ".join(f"{x:g},{y:g}" for x, y in shape) for shape in SHAPES)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" role="img" '
        'aria-label="keel">'
        f'<rect width="1" height="1" fill="{background}"/>'
        f'<g fill="{foreground}">{paths}</g>'
        "</svg>\n"
    ).encode()


def build() -> dict[str, bytes]:
    """Every generated file, by name."""
    output = {name: render(size, maskable=maskable) for name, size, maskable in TARGETS}
    output[SVG_NAME] = render_svg()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the committed icons against a fresh render; write nothing.",
    )
    args = parser.parse_args(argv)

    generated = build()
    if not args.check:
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        for name, payload in generated.items():
            (ICON_DIR / name).write_bytes(payload)
            print(f"wrote {ICON_DIR / name} ({len(payload)} bytes)")
        return 0

    stale = [
        name
        for name, payload in generated.items()
        if not (ICON_DIR / name).is_file() or (ICON_DIR / name).read_bytes() != payload
    ]
    if stale:
        print("icons differ from the geometry in this file: " + ", ".join(sorted(stale)))
        print("re-run `python -m scripts.build_icons` and commit the result.")
        return 1
    print(f"{len(generated)} icons match the committed bytes.")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
