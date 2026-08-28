"""Generate keel's app icons (#538) from one geometry, with no dependencies.

The PWA manifest needs raster icons, and a repository that commits PNGs without saying where
they came from has committed four files nobody can review or reproduce. So the mark is defined
ONCE below as plain geometry, and both the SVG and every PNG are emitted from it:
`scripts/build_icons.py --check` re-renders and compares bytes, which
`tests/web/test_pwa.py` runs on every build. A hand-edited PNG fails; a changed shape has to
be changed here, where the change is readable in a diff.

Since #593 the geometry is keeltrading.com's ship-mark, transcribed from the site's
`public/favicon.svg` into the design-space numbers below, so the tab icon here and the tab icon
on the site are the same drawing. The sibling site repository is not readable by CI; the two
COLOURS the mark wears are pinned on the css side by
`tests/web/test_client_assets.py::test_the_palette_wears_keeltrading_coms_identity`.

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
from math import ceil, cos, floor, hypot, pi, sin
from pathlib import Path

#: Where the generated icons land, inside the served static tree.
ICON_DIR = Path(__file__).resolve().parent.parent / "keel" / "web" / "static" / "icons"

#: The two brand colours, copied from keeltrading.com (#593): the site's `--accent` teal
#: (`global.css`), with the mark stroked in the paper colour the site's own favicon strokes it
#: in (`--bg`, `#f8f7f3` -- not pure white). The installed app's tile therefore matches both the
#: page it opens and the site that sent the operator to it. CI cannot read the sibling site
#: repository; the css-side pin on the same copy is
#: `tests/web/test_client_assets.py::test_the_palette_wears_keeltrading_coms_identity`.
BACKGROUND = (0x0C, 0x5D, 0x52, 0xFF)
FOREGROUND = (0xF8, 0xF7, 0xF3, 0xFF)


def _stroke(
    start: tuple[float, float], end: tuple[float, float], width: float
) -> tuple[tuple[float, float], ...]:
    """A straight stroke of `width` as a four-point polygon, with butt caps.

    The mark below is a handful of strokes, so it is written as strokes rather than as
    hand-computed corner polygons: geometry whose corners are spelled out one by one is geometry
    nobody can adjust later without re-deriving the perpendiculars by hand, and every adjustment
    to a mark is a nudge.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = hypot(dx, dy)
    # A zero-length stroke has no perpendicular. It is a caller error rather than a shape, and
    # returning an empty polygon here would silently drop a limb of the mark instead.
    if length == 0:
        raise ValueError("a stroke needs two distinct points")
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    return (
        (start[0] + nx, start[1] + ny),
        (end[0] + nx, end[1] + ny),
        (end[0] - nx, end[1] - ny),
        (start[0] - nx, start[1] - ny),
    )


def _octagon(cx: float, cy: float, r: float) -> tuple[tuple[float, float], ...]:
    """A regular octagon of circumradius `r`, standing on a flat top.

    The site's mark strokes round caps and joins, and a circle is not a polygon. This is the
    closest eight-sided stand-in, sized so its EDGES are tangent to the true cap circle
    (`r = radius / cos(pi/8)`): coverage is never less than the cap it replaces, and the corners
    poke past it by four per cent of the stroke width -- subpixel at every size rendered here.
    """
    return tuple(
        (cx + r * cos(pi / 8 + k * pi / 4), cy + r * sin(pi / 8 + k * pi / 4)) for k in range(8)
    )


#: ── the mark, in keeltrading.com's own 24-unit design space (#593) ─────────────────────────────
#:
#: Transcribed from the site's `public/favicon.svg` so the two files can be read side by side:
#: a mast rising from a hull in section, two stays angling down from the masthead to the deck,
#: and the hull's flare drawn as two mirrored cubic beziers. Every number below is either a
#: literal coordinate from that file or a point ON one of its curves.
#:
#: **Until #593 the mark was a `k` monogram**, chosen at #538 by rendering three nautical
#: silhouettes and watching each read as something else at icon size. #593 replaces it with the
#: site's ship, which settles that experiment the other way: the name is unambiguous at every
#: size AND it is the mark the product's public face already wears. The letter's one real
#: advantage -- surviving a 16px favicon -- is the site's problem too, and its answer was a
#: 1.7-unit stroke and about nine coordinates, which is what the numbers below keep.
_DESIGN = 24.0
_STROKE_WIDTH = 1.7
_CORNER_RADIUS = 5.0
_MASTHEAD = (12.0, 4.0)
_MASTFOOT = (12.0, 14.0)
_STAY_PORTS = ((8.8, 8.0), (15.2, 8.0))
#: The hull: the site's `M12 14 c-4 0 -6.4 1.8 -7.6 4.8 h15.2 C18.4 15.8 16 14 12 14 Z`,
#: with both cubic beziers sampled at t = 0, .125, ..., 1 (this rasteriser draws polygons; the
#: chords sit a tenth of a per cent of the tile from the true curve -- half a pixel at 512).
#: Four samples per side were tried first and read as facets; eight do not.
_HULL = (
    (4.4, 18.8),
    (4.907, 17.732),
    (5.531, 16.784),
    (6.277, 15.963),
    (7.15, 15.275),
    (8.154, 14.728),
    (9.294, 14.328),
    (10.574, 14.083),
    (12.0, 14.0),
    (13.426, 14.083),
    (14.706, 14.328),
    (15.846, 14.728),
    (16.85, 15.275),
    (17.723, 15.963),
    (18.469, 16.784),
    (19.093, 17.732),
    (19.6, 18.8),
)


def _unit(points: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
    """Design-space points moved into the 0..1 unit square both renderers draw in."""
    return tuple((x / _DESIGN, y / _DESIGN) for x, y in points)


#: The mark as unit-space polylines, in drawing order: mast, two stays, hull.
STROKES: tuple[tuple[tuple[float, float], ...], ...] = (
    _unit((_MASTHEAD, _MASTFOOT)),
    *(_unit((_MASTHEAD, port)) for port in _STAY_PORTS),
    _unit(_HULL),
)

#: Every vertex of every stroke, in design space. The site's mark strokes with round caps AND
#: round joins, so each of these gets a cap octagon: the octagons at the polyline's ends are the
#: round caps, and the ones at its interior vertices are the round joins -- without them, the
#: butt caps of consecutive hull chords leave a wedge on the outside of each turn (the chords
#: turn by up to eighteen degrees, and the wedge is three pixels at 512 -- found by sampling the
#: render against the true round-join coverage at 2048px, not by looking). The mast's foot needs
#: no special case: its octagon is the same as every other vertex's, and the mast's bottom end
#: sits inside the hull's stroke besides, the same trick the `k` used at its arm junction.
_CAP_POINTS = tuple(
    point for stroke in STROKES for point in _unit(stroke)
)

_W = _STROKE_WIDTH / _DESIGN

#: Every polygon the rasteriser draws: one quad per stroke segment, plus a round-cap octagon at
#: each vertex above.
SHAPES: tuple[tuple[tuple[float, float], ...], ...] = tuple(
    _stroke(a, b, _W) for stroke in STROKES for a, b in zip(stroke, stroke[1:])
) + tuple(_octagon(x, y, (_W / 2) / cos(pi / 8)) for x, y in _CAP_POINTS)

#: The safe-area inset a maskable icon is judged against. Android may crop a maskable icon to
#: any shape inside the middle 80% -- a circle, a squircle, a rounded square -- so the mark is
#: scaled to sit inside that circle rather than merely inside the square. Getting this wrong is
#: invisible on the developer's own launcher and clips the icon on somebody else's.
MASKABLE_SCALE = 0.72

#: Samples per axis inside each pixel. 3 means nine coverage tests per pixel, which is enough to
#: keep the hull's curve from stepping visibly at 192px and cheap enough that all four icons
#: render in well under a second.
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

    # Coverage is accumulated per polygon, over only the samples inside that polygon's bounding
    # box, rather than per sample against every polygon. The ship is sixteen-odd small shapes
    # where the `k` was three, and testing all 2.4M samples of a 512px render against every one
    # of them turned the render into tens of seconds of pure Python. The DECISION per sample is
    # unchanged -- same sample grid, same even-odd test, a sample inside ANY shape counts once
    # however many shapes overlap it (the caps sit on top of the strokes), and a point outside a
    # polygon's bounding box cannot be inside the polygon -- so the bytes are exactly what the
    # direct loop produced at #538, in a fraction of the visits.
    step = 1.0 / (size * _SUPERSAMPLE)
    samples = size * _SUPERSAMPLE
    covered = bytearray(samples * samples)
    for polygon in shapes:
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        k_first = max(0, ceil(min(xs) / step - 0.5))
        k_last = min(samples - 1, floor(max(xs) / step - 0.5))
        j_first = max(0, ceil(min(ys) / step - 0.5))
        j_last = min(samples - 1, floor(max(ys) / step - 0.5))
        for j in range(j_first, j_last + 1):
            y = (j + 0.5) * step
            base = j * samples
            for k in range(k_first, k_last + 1):
                if _inside(polygon, (k + 0.5) * step, y):
                    covered[base + k] = 1

    total = _SUPERSAMPLE * _SUPERSAMPLE
    rows: list[bytes] = []
    for row in range(size):
        pixels = bytearray()
        for column in range(size):
            hits = sum(
                covered[(row * _SUPERSAMPLE + sub_y) * samples + column * _SUPERSAMPLE + sub_x]
                for sub_y in range(_SUPERSAMPLE)
                for sub_x in range(_SUPERSAMPLE)
            )
            pixels.extend(_blend(hits / total))
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
    """The same mark as SVG: keeltrading.com's favicon, redrawn from the same numbers.

    Emitted from `STROKES`/`_STROKE_WIDTH` rather than hand-written beside them, so the vector
    and the rasters cannot drift: a change to the hull that forgot the SVG would otherwise ship
    an icon that disagrees with itself depending on which size the launcher picked.

    The vector keeps two things the rasters deliberately drop, both because a favicon's context
    differs from a launcher tile's:

      * the site's ROUNDED tile corners (`rx`), which the rasters cannot carry -- they are
        opaque by design (see `_blend`), and the maskable one must bleed to the launcher's crop
        edge; a transparent corner is an arbitrary backdrop waiting to happen;
      * true round caps and joins from the stroke renderer, where the polygon rasteriser
        approximates the same caps with `_octagon`.

    `viewBox="0 0 1 1"` lets the unit coordinates go in verbatim. No `<script>`, no external
    reference: `server._STATIC_CSP` covers `image/svg+xml` precisely because SVG is active
    content, and an icon is the last place that should be exercised.
    """
    background = "#{:02x}{:02x}{:02x}".format(*BACKGROUND[:3])
    foreground = "#{:02x}{:02x}{:02x}".format(*FOREGROUND[:3])
    width = f"{_W:.6g}"
    rx = f"{_CORNER_RADIUS / _DESIGN:.6g}"
    paths = "".join(
        '<path d="M {}"/>'.format(" L ".join(f"{x:g} {y:g}" for x, y in stroke))
        for stroke in STROKES
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" role="img" '
        'aria-label="keel">'
        f'<rect width="1" height="1" rx="{rx}" fill="{background}"/>'
        f'<g fill="none" stroke="{foreground}" stroke-width="{width}" '
        f'stroke-linecap="round" stroke-linejoin="round">{paths}</g>'
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
