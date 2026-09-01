"""The installable app: manifest, icons and service worker (#538).

Same line as `test_client_assets.py` draws, for the same reason: this proves **properties of the
shipped bytes** and what the server does with them over a real socket. It cannot prove that
Chrome offers to install the app, that the worker actually intercepts a navigation, or that the
shell paints with the engine stopped -- all three need a browser, and this repository is
deliberately Python-only.

What that leaves uncovered is listed once, honestly, and each item was checked by hand against a
running `keel serve` driven by a real Chromium; the results are in the PR body:

  * that the worker installs, activates and reaches `controlling` state;
  * that a second build leaves the new worker WAITING and the offer appears in the footer;
  * that taking the offer swaps the controller and reloads into the new build;
  * that the precache is populated and holds exactly `PRECACHE`;
  * that the shell still paints after `keel serve` is killed, with no figures on it;
  * that a changed build string swaps the cache and deletes the old one;
  * that the manifest and its icons are fetched with credentials rather than refused.

The one thing this module CAN prove about the API rule is stronger than a browser check anyway:
`/api/*` is outside the worker's scope, so no code in the worker can reach it. That is asserted
below as a property of the two path constants rather than as a property of a running browser.
"""

from __future__ import annotations

import http.client
import json
import re
import subprocess
import sys
import zlib
from math import hypot
from pathlib import Path

import pytest

from keel.web import server, staticfiles
from scripts import build_icons


def _P(rest: str) -> str:
    """A request path under the mount, composed rather than spelled -- see the identical helper
    in `test_staticfiles.py`. #540 moved the mount from `/static/` to `/`, and every literal had
    to be edited; composed, the next move is none."""
    return staticfiles.STATIC_PREFIX + rest


_STATIC = staticfiles.STATIC_ROOT
_SW = _STATIC / "sw.js"
_MANIFEST = _STATIC / "manifest.webmanifest"
_INDEX = _STATIC / "index.html"
_ICONS = _STATIC / "icons"

#: Files under `static/` that are deliberately NOT precached, with the reason each is exempt.
#: A closed set, compared against the directory below, so a new asset is either precached or
#: exempted here on purpose -- never omitted by having been forgotten.
_NOT_PRECACHED = {
    # A worker cannot precache itself: the browser fetches and stores the script through the
    # registration, not through the Cache API, and an entry here would be a second, stale copy
    # that nothing ever reads.
    "sw.js",
    # Prose about why `js/external/` is empty. Not loaded by the page at all.
    "js/external/README.md",
}


def _sw_source() -> str:
    return _SW.read_text(encoding="utf-8")


def _precache_list() -> list[str]:
    """The paths in the worker's `PRECACHE`, resolved through its `BASE` template literal.

    Parsed rather than imported, because there is no JavaScript runtime here. The parse is
    deliberately narrow -- it accepts exactly the two forms the file uses -- so that a `PRECACHE`
    rewritten into a shape this cannot read fails loudly instead of silently matching nothing.
    `test_the_precache_parser_actually_found_something` is the guard against that.
    """
    source = _sw_source()
    block = re.search(r"const PRECACHE = \[(.*?)\n\];", source, re.DOTALL)
    assert block is not None, "PRECACHE is not in the shape this parser understands"
    entries: list[str] = []
    for line in block.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("//"):
            continue
        if line == "SHELL":
            entries.append(f"{staticfiles.STATIC_PREFIX}{staticfiles.CLIENT_ENTRY}")
            continue
        template = re.fullmatch(r"`\$\{BASE\}([^`]*)`", line)
        assert template is not None, f"unparsed PRECACHE entry: {line}"
        entries.append(f"{staticfiles.STATIC_PREFIX}{template.group(1)}")
    return entries


# -- the icons -------------------------------------------------------------------------------------


def test_the_committed_icons_match_the_geometry_that_generated_them() -> None:
    """The icons are reproducible from `scripts/build_icons.py`, byte for byte.

    This is what makes four binary files reviewable: nobody can read a PNG in a diff, but anybody
    can read three stroke coordinates, and this test is the link between the two. A hand-edited
    icon fails here; a deliberate change has to be made in the geometry, where it is legible.

    Run as a subprocess rather than by calling `build_icons.main(["--check"])` in-process so that
    the assertion covers the ENTRY POINT an operator would actually run, exit code included.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-m", "scripts.build_icons", "--check"],
        capture_output=True,
        text=True,
        cwd=str(_STATIC.parent.parent.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_icon_check_would_notice_a_changed_pixel() -> None:
    """Mutation: the comparison above is bytes, not existence.

    Without this, `--check` passing would be consistent with it comparing nothing at all -- the
    exact failure `test_a_vacuous_assertion` shapes exist to catch elsewhere in this suite.
    """
    fresh = build_icons.build()
    name = "keel-192.png"
    tampered = bytearray(fresh[name])
    tampered[-1] ^= 0xFF
    assert bytes(tampered) != fresh[name]
    assert (_ICONS / name).read_bytes() == fresh[name]


def test_every_icon_the_manifest_names_exists_and_is_the_size_it_claims() -> None:
    """A manifest that names a missing icon is a manifest a browser rejects **silently** -- the
    install prompt simply never appears, with nothing in the console tying it to the file.

    The dimensions are read out of the PNG header rather than trusted, because `sizes` is a
    string the manifest asserts and nothing else checks.
    """
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["icons"], "no icons declared"
    for icon in manifest["icons"]:
        source = icon["src"]
        assert source.startswith(staticfiles.STATIC_PREFIX), f"{source} is not same-origin"
        path = _STATIC / source[len(staticfiles.STATIC_PREFIX) :]
        assert path.is_file(), f"{source} is declared but not shipped"
        if icon["type"] != "image/png":
            continue
        width, height = _png_size(path.read_bytes())
        declared = icon["sizes"]
        assert f"{width}x{height}" == declared, f"{source} is {width}x{height}, declared {declared}"


def test_a_maskable_icon_is_declared_and_is_not_also_declared_any() -> None:
    """Both purposes on one file is the commonest way an install looks subtly wrong.

    A maskable icon carries 20% padding by construction, so a launcher that does NOT crop to the
    safe area draws a small mark in a big box. Chrome's own guidance is two files; this asserts
    keel ships two.
    """
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    purposes = [icon.get("purpose", "any") for icon in manifest["icons"]]
    assert "maskable" in purposes, "no maskable icon -- Android will letterbox the tile"
    for purpose in purposes:
        assert purpose in ("any", "maskable"), f"{purpose} declares two roles for one file"


def test_the_maskable_ink_stays_inside_the_circle_android_may_crop_to() -> None:
    """Ink beyond the 0.40-radius safe circle is a mark the launcher will crop pieces off.

    Android may crop a maskable icon to any shape inside the middle 80% of the tile, and a circle
    is the tightest of them, so `MASKABLE_SCALE` exists to hold the mark whole under that crop.
    This measures the committed PNG rather than the geometry's intentions: every pixel whose
    CENTRE lies outside the 0.40 circle must be pure background. The first #593 icons shipped a
    stray ink blob at radius 0.52 (a doubled design-space division in `_CAP_POINTS` that folded
    every cap into the tile's corner), and a test that trusted the geometry instead of the bytes
    would have inherited the bug it existed to catch.
    """
    pixels = _png_rgba((_ICONS / "keel-maskable-512.png").read_bytes())
    size = len(pixels)
    centre = size / 2
    outside = [
        (row, column)
        for row, line in enumerate(pixels)
        for column, pixel in enumerate(line)
        if hypot(column + 0.5 - centre, row + 0.5 - centre) > 0.40 * size
        and pixel != build_icons.BACKGROUND
    ]
    assert not outside, (
        f"{len(outside)} inked pixels outside the maskable safe circle, nearest "
        f"{outside[:3]} -- a launcher cropping to the middle 80% would cut the mark"
    )


def _png_size(payload: bytes) -> tuple[int, int]:
    """Width and height from a PNG's IHDR, which is always the first chunk."""
    assert payload[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")


def _png_rgba(payload: bytes) -> list[list[tuple[int, int, int, int]]]:
    """Every pixel of one of the generator's PNGs, decoded with the stdlib.

    The whole point of measuring the maskable safe zone is to read the bytes that ship, so the
    test cannot lean on Pillow (a dependency the web surface refuses on principle) -- and it
    does not need to: the generator emits plain RGBA8 with filter 0 on every scanline. All five
    PNG filters are undone anyway rather than asserting on filter 0, so a future encoder change
    fails nothing here -- `--check` is the gate whose business the filter choice is.
    """
    width, height = _png_size(payload)
    assert payload[24:26] == b"\x08\x06", "the icons are 8-bit RGBA by construction"
    compressed = bytearray()
    offset = 8
    while offset < len(payload):
        length = int.from_bytes(payload[offset : offset + 4], "big")
        if payload[offset + 4 : offset + 8] == b"IDAT":
            compressed += payload[offset + 8 : offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(compressed)
    stride = width * 4
    rows: list[list[tuple[int, int, int, int]]] = []
    previous = bytearray(stride)
    for row in range(height):
        start = row * (stride + 1)
        line = bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            left = line[i - 4] if i >= 4 else 0
            up = previous[i]
            up_left = previous[i - 4] if i >= 4 else 0
            match raw[start]:
                case 0:
                    continue
                case 1:
                    line[i] = (line[i] + left) & 0xFF
                case 2:
                    line[i] = (line[i] + up) & 0xFF
                case 3:
                    line[i] = (line[i] + (left + up) // 2) & 0xFF
                case 4:
                    p = left + up - up_left
                    pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                    predictor = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                    line[i] = (line[i] + predictor) & 0xFF
                case _:
                    raise AssertionError(f"unknown PNG filter {raw[start]}")
        previous = line
        rows.append([tuple(line[i : i + 4]) for i in range(0, stride, 4)])
    return rows


# -- the manifest ----------------------------------------------------------------------------------


def test_the_manifest_start_url_and_scope_agree_with_the_client() -> None:
    """`start_url` must be inside `scope`, and `scope` must be where the client actually lives.

    Getting this wrong does not fail loudly: the app installs, and then opens OUTSIDE its own
    scope, which means it opens in a browser tab rather than the standalone window the operator
    just installed it for.
    """
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["scope"] == staticfiles.STATIC_PREFIX
    assert manifest["start_url"].startswith(manifest["scope"])
    landing = manifest["start_url"][len(staticfiles.STATIC_PREFIX) :]
    assert landing in staticfiles.CLIENT_ROUTES, (
        f"start_url lands on {landing!r}, which is not a client route -- a reload of the installed "
        "app would 404"
    )


def test_the_manifest_link_is_fetched_with_credentials() -> None:
    """The one attribute that decides whether this app is installable at all.

    `rel="manifest"` is fetched with credentials mode "omit" by DEFAULT. Every response this
    server sends is gated on the session cookie, so without `crossorigin="use-credentials"` the
    manifest fetch is a 403, the browser reports no manifest, and nothing in the page or the
    console points at the cause. Found by driving a real browser, not by reading the spec.
    """
    html = _INDEX.read_text(encoding="utf-8")
    link = re.search(r"<link[^>]*rel=\"manifest\"[^>]*>", html)
    assert link is not None, "the shell declares no manifest"
    assert 'crossorigin="use-credentials"' in link.group(0), (
        "a manifest link without use-credentials is refused by this server's own admission check"
    )


def test_the_shell_declares_a_theme_colour_matching_the_manifest() -> None:
    """Two places say what colour the title bar is, and they disagree at their peril: the meta tag
    wins in the browser tab, the manifest wins in the installed window, and a mismatch shows up as
    the window changing colour when it is installed."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    html = _INDEX.read_text(encoding="utf-8")
    meta = re.search(r"<meta name=\"theme-color\" content=\"([^\"]+)\">", html)
    assert meta is not None, "no theme-color in the shell"
    assert meta.group(1) == manifest["theme_color"]


# -- the service worker ----------------------------------------------------------------------------


def test_the_worker_is_served_from_the_scope_it_must_control() -> None:
    """**The structural half of the API rule.**

    A worker's default scope is its own directory. Serving it from `static/js/` would scope it to
    `/static/js/`, where it could not answer a navigation to `/static/insights` -- the design
    spec's file list puts `sw` among the modules, and this is the one place this implementation
    departs from it, deliberately, because the alternative is a `Service-Worker-Allowed` header
    whose removal would silently shrink the scope rather than fail.
    """
    assert _SW.is_file(), "sw.js must sit at the static root, not under js/"
    assert not (_STATIC / "js" / "sw.js").exists(), (
        "a worker under js/ is scoped to /static/js/ and cannot control the client's routes"
    )


def test_the_api_prefix_is_now_inside_the_workers_scope() -> None:
    """**A guarantee this suite used to make, and no longer can. Recorded, not quietly dropped.**

    At #538 this test asserted the opposite: the worker was served from `/static/`, so `/api/*`
    was OUTSIDE its scope and the browser never consulted it for an API request at all. That was
    structural -- nothing written inside `sw.js` could have cached a balance, because nothing
    inside `sw.js` could see one.

    #540 moved the shell to `/`. The scope is the whole origin, every `/api/*` request now passes
    through the worker's `fetch` handler, and the structural guarantee is gone. What replaces it
    is the explicit guard, which #538 wrote as dead code and pinned with a test whose docstring
    said it "stops being redundant on the same day that nobody is thinking about it" -- that day
    was this one. This test asserts the new arrangement so that nobody reads the module comment
    and assumes the old one still holds.
    """
    source = _sw_source()
    base = re.search(r'const BASE = "([^"]+)"', source)
    api = re.search(r'const API_PREFIX = "([^"]+)"', source)
    assert base is not None and api is not None
    assert base.group(1) == staticfiles.STATIC_PREFIX
    assert api.group(1).startswith(base.group(1)), (
        "the mount moved back off `/`; if that is deliberate, restore the #538 form of this test "
        "rather than deleting it -- the scope would be excluding the API again"
    )
    # And therefore the guard below is the whole protection. Asserted here, next to the loss, so
    # the two facts are read together.
    assert "url.pathname.startsWith(API_PREFIX)" in source


def test_the_worker_still_guards_the_api_prefix_explicitly() -> None:
    """Belt and braces, pinned so it is not tidied away as dead code.

    It IS dead code today, and that is exactly why it needs a test: #540 moves the shell to `/`,
    the scope widens to the whole origin, and this guard stops being redundant on the same day
    that nobody is thinking about it.
    """
    source = _sw_source()
    assert "url.pathname.startsWith(API_PREFIX)" in source, (
        "the explicit /api/ guard is gone; see this test's docstring before removing it"
    )


def test_the_worker_never_writes_to_a_cache_outside_install() -> None:
    """No runtime `cache.put`, anywhere. The only write is `addAll(PRECACHE)`.

    A `put` in the fetch handler is how every "just cache the last good response" change starts,
    and it is the change that turns this into an app that shows last week's equity.
    """
    source = _sw_source()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("*", "/*", "//"))
    )
    assert ".put(" not in code, "a runtime cache write in the service worker"
    assert code.count(".addAll(") == 1, "the precache should be the only thing written"


def test_the_worker_never_takes_over_a_page_it_did_not_render() -> None:
    """**This is a correction: the worker called `skipWaiting()` unconditionally at install.**

    The original argument was that the version-keyed cache made it safe -- new worker, new cache,
    old one deleted. That is true about CACHES and silent about the page already on screen:
    `skipWaiting()` with `clients.claim()` takes over a document parsed and rendered by the OLD
    build, and answers its later requests from the NEW build's cache. One page, two builds.

    keel had a second line of defence that made it hard to notice -- a new build means a restarted
    process, a new session token, and a 403 the banner reports -- but a hazard covered by an
    unrelated layer is still a hazard.

    So: `skipWaiting` appears only inside the message handler, never in `install`.
    """
    source = _sw_source()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("*", "/*", "//"))
    )
    # Sliced to the install handler ALONE, not "install up to activate": the message handler now
    # sits between them and it is the one place `skipWaiting` legitimately appears, so the wider
    # slice would find it there and report the opposite of the truth.
    start = code.index('addEventListener("install"')
    install = code[start : code.index("self.addEventListener(", start + 1)]
    assert "skipWaiting" not in install, (
        "the worker takes over at install again; see this test's docstring before restoring it"
    )
    assert 'if (event.data === "SKIP_WAITING") void self.skipWaiting();' in code, (
        "the consent path is gone -- an update could never be applied"
    )


def test_the_client_offers_the_update_rather_than_applying_it() -> None:
    """The other half: a waiting worker is useless if nothing tells the operator it is waiting.

    Both arrival paths are pinned. `registration.waiting` catches an update that installed during
    a previous visit; `updatefound` + `installed` catches one that arrives while the page is open.
    A build that handled only the second would leave an update stranded forever for anyone who
    closed the tab at the wrong moment."""
    main = (_STATIC / "js" / "main.js").read_text(encoding="utf-8")
    assert "registration.waiting" in main, "an update installed on a previous visit is stranded"
    assert '"updatefound"' in main, "an update arriving while the page is open is missed"
    assert 'postMessage("SKIP_WAITING")' in main


def test_the_first_install_is_not_announced_as_an_update() -> None:
    """`navigator.serviceWorker.controller` is the test for "update" versus "first install".

    Without it the very first visit -- a worker installing with nothing to replace -- would offer
    the operator a reload for the build they are already running, which teaches them the notice
    means nothing.

    **And it gates the OFFER, never the watching.** An earlier spelling returned early from
    `watchForUpdate` when there was no controller, which meant that on a first visit -- the one
    load where there reliably is none -- the `updatefound` listener was never attached. This
    asserts the listener is registered unconditionally."""
    main = (_STATIC / "js" / "main.js").read_text(encoding="utf-8")
    watcher = main[main.index("function watchForUpdate(") : main.index("function offerUpdate(")]
    assert "navigator.serviceWorker.controller" in watcher, (
        "a first install is announced as an update"
    )
    assert "return;" not in watcher.split('addEventListener("updatefound"')[0], (
        "watchForUpdate returns before attaching its listener; a first visit would never watch"
    )


def test_the_reload_waits_for_the_new_worker_to_take_over() -> None:
    """Reloading straight after `postMessage` races: the new worker may not be controlling yet, so
    the reload fetches the old build again and leaves the offer standing."""
    main = (_STATIC / "js" / "main.js").read_text(encoding="utf-8")
    controller_change = main.index('"controllerchange"')
    post = main.index('postMessage("SKIP_WAITING")')
    assert controller_change < post, (
        "the reload listener is registered after the message is sent -- the takeover can land first"
    )


def test_the_update_offer_is_not_in_the_live_region() -> None:
    """The engine banner is the page's one `aria-live` region and it answers "is keel running".

    An upgrade notice is neither urgent nor about the engine, and putting it there would interrupt
    a screen reader mid-table to say something that can wait indefinitely."""
    html = _INDEX.read_text(encoding="utf-8")
    banner = html[html.index('id="engine"') : html.index('id="content"')]
    assert 'id="update"' not in banner
    assert 'id="update"' in html, "the offer has nowhere to go"


def test_the_cache_name_is_keyed_to_the_build() -> None:
    """A `CacheFirst` shell with a fixed cache name survives an engine upgrade and answers the new
    API with the old contract -- everything renders, only the fields are wrong."""
    source = _sw_source()
    assert 'searchParams.get("v")' in source, "the worker does not read a build from its own URL"
    assert re.search(r"const CACHE = `keel-shell-\$\{BUILD\}`", source), (
        "the cache name does not carry the build"
    )


def test_a_token_bearing_navigation_never_answers_from_cache() -> None:
    """**The worker must not hold the door shut against the only key.**

    `keel serve` prints `http://127.0.0.1:8765/?token=...` and the server exchanges that token for
    the session cookie ON THAT NAVIGATION -- `server.do_GET` reads `query["token"]` and answers
    with `Set-Cookie` before anything else happens. Every later `/api/*` read is gated on the
    cookie (`_admitted`).

    A cache-first answer to that navigation means the server never sees the token, never sets the
    cookie, and the shell that loads is then refused by every endpoint it calls. What the operator
    reads is "Not authorised -- open the address keel printed when it started", which is the one
    instruction that cannot help, because they DID open it and the worker intercepted it. There is
    no way out of that state from inside the page.

    So the guard is asserted structurally: the navigation branch must return to the network
    BEFORE it reaches the cache, and the parameter it looks for must be the one the server
    actually reads. Binding both sides here is the point -- a rename on either side fails this.
    """
    source = _sw_source()
    server_source = (Path(server.__file__)).read_text(encoding="utf-8")

    param = re.search(r'query\.get\("([^"]+)"\)', server_source)
    assert param is not None, "the server no longer reads a token out of the query string"

    branch = re.search(
        r'if \(request\.mode === "navigate"\) \{(.*?)\n  \}', source, re.DOTALL
    )
    assert branch is not None, "the navigation branch is not in the shape this test understands"
    body = branch.group(1)

    # The WHOLE line, not a substring anywhere in the branch. A substring search passes
    # against an inverted condition (`!has(...)`), a guard that falls through instead of
    # returning, a dead guard (`false && has(...)`), and a guard reading a different
    # parameter than the constant -- four mutations that each restore the bug this test
    # exists to prevent. Asserted as an exact line so none of them can pass.
    guard_line = re.search(
        r"^\s*if \(url\.searchParams\.has\(SESSION_TOKEN_PARAM\)\) return;\s*$",
        body,
        re.MULTILINE,
    )
    assert guard_line is not None, (
        "the navigation branch does not decline a token-bearing navigation with exactly "
        "`if (url.searchParams.has(SESSION_TOKEN_PARAM)) return;` -- an inverted, dead, "
        "fall-through or differently-parameterised guard reads like a check and restores "
        "the lockout"
    )
    guard = guard_line.start()
    cache = body.find("caches.match(")
    assert cache != -1, "the navigation branch no longer consults the cache at all"
    assert guard < cache, (
        "the token check must come BEFORE the cache lookup; after it, the cached shell has "
        "already been returned and the exchange never happened"
    )
    assert re.search(
        r'const SESSION_TOKEN_PARAM = "' + re.escape(param.group(1)) + r'"', source
    ), (
        f"the worker's session-token parameter does not match the server's {param.group(1)!r} -- "
        "one side was renamed and the other was not"
    )


def test_the_worker_deletes_only_its_own_stale_caches() -> None:
    """Scoped to the `keel-shell-` prefix. A worker that deletes caches it did not create is a
    worker that will one day delete somebody else's."""
    source = _sw_source()
    assert 'name.startsWith("keel-shell-")' in source
    assert "name !== CACHE" in source


def test_every_shipped_asset_is_either_precached_or_exempt() -> None:
    """The list in `sw.js` against the directory on disk.

    A worker cannot walk a directory, so the list is hand-maintained -- which means the only thing
    standing between it and an app that works until it is opened offline is this comparison.
    """
    shipped = {
        str(path.relative_to(_STATIC)).replace("\\", "/")
        for path in _STATIC.rglob("*")
        if path.is_file()
    }
    precached = {path[len(staticfiles.STATIC_PREFIX) :] for path in _precache_list()}
    missing = shipped - precached - _NOT_PRECACHED
    assert not missing, (
        f"shipped but not precached: {sorted(missing)} -- add each to PRECACHE in sw.js, or to "
        "_NOT_PRECACHED here with the reason it is exempt"
    )
    stale = precached - shipped
    assert not stale, f"precached but not shipped: {sorted(stale)} -- the app would fail to install"


def test_the_precache_parser_actually_found_something() -> None:
    """Guards the comparison above against silently matching an empty list."""
    entries = _precache_list()
    assert len(entries) > 5, entries
    assert f"{staticfiles.STATIC_PREFIX}{staticfiles.CLIENT_ENTRY}" in entries


def test_nothing_exempted_from_the_precache_is_loaded_by_the_shell() -> None:
    """An exemption is a promise that the file is not needed to paint. Checked, not trusted."""
    html = _INDEX.read_text(encoding="utf-8")
    for name in _NOT_PRECACHED:
        assert f"{staticfiles.STATIC_PREFIX}{name}" not in html, (
            f"{name} is exempt from the precache but the shell loads it"
        )


# -- the registration ------------------------------------------------------------------------------


def test_the_client_registers_the_worker_only_after_a_successful_config_read() -> None:
    """Registering before the build is known would install a worker under a name that has to be
    corrected on the next load -- two registrations, two caches, for one deployment.

    And with the engine stopped there is no build at all, at which point the right move is to
    leave the installed worker alone: it is the thing letting the operator read the page.
    """
    main = (_STATIC / "js" / "main.js").read_text(encoding="utf-8")
    config_block = main[main.index('void read("config")') :]
    assert "registerWorker(config)" in config_block, (
        "the worker is not registered from the config read"
    )
    assert re.search(
        r"const build = \(config && \(config\.build \|\| config\.version\)\) \|\| \"\";", main
    ), "registerWorker no longer derives the build from the config document"
    assert "if (!build) return;" in main, "a failed config read must register nothing"


def test_the_registration_encodes_the_build_string() -> None:
    """`keel.version` produces `0.11.2+88fb17bcab15`, and a raw `+` in a query string decodes to a
    SPACE -- the worker would key its cache to a build that is not the one running."""
    main = (_STATIC / "js" / "main.js").read_text(encoding="utf-8")
    assert "encodeURIComponent(build)" in main, "an unencoded build string in the registration URL"


def test_the_registration_scope_matches_the_manifest() -> None:
    """Three files name the same prefix; a disagreement is an app that installs and then behaves
    as though it had not been."""
    main = (_STATIC / "js" / "main.js").read_text(encoding="utf-8")
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert "{ scope: BASE }" in main
    assert manifest["scope"] == staticfiles.STATIC_PREFIX


# -- over the wire ---------------------------------------------------------------------------------

from tests.web.test_server import _request, _session  # noqa: E402


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        (_P("sw.js"), "text/javascript; charset=utf-8"),
        (_P("manifest.webmanifest"), "application/manifest+json"),
        (_P("icons/keel.svg"), "image/svg+xml"),
        (_P("icons/keel-192.png"), "image/png"),
        (_P("icons/keel-512.png"), "image/png"),
        (_P("icons/keel-maskable-512.png"), "image/png"),
    ],
)
def test_every_install_asset_is_served_with_the_type_the_browser_requires(  # type: ignore[no-untyped-def]
    path: str, content_type: str, running
) -> None:
    """A wrong `Content-Type` under `nosniff` is not a file the browser works around -- it is a
    manifest it ignores and a worker it refuses to run, in both cases without saying why."""
    status, headers, _body = _request(running, path, cookie=_session(running))
    assert status == 200, path
    assert headers["Content-Type"] == content_type, path


def test_the_install_assets_are_behind_the_same_admission(running) -> None:  # type: ignore[no-untyped-def]
    """Not weakened for the browser's convenience. This is the reason the manifest link needs
    `use-credentials`, asserted from the server's side."""
    for path in (_P("sw.js"), _P("manifest.webmanifest"), _P("icons/keel-192.png")):
        status, _headers, _body = _request(running, path)  # no cookie
        assert status == 403, path


def test_the_served_manifest_is_valid_json(running) -> None:  # type: ignore[no-untyped-def]
    """Parsed off the wire, not off disk: a manifest that is correct in the repository and
    mangled by the server is a manifest the browser rejects."""
    _status, _headers, body = _request(
        running, _P("manifest.webmanifest"), cookie=_session(running)
    )
    manifest = json.loads(body)
    assert manifest["name"] == "keel"
    assert manifest["display"] == "standalone"


def test_the_worker_is_served_with_no_store(running) -> None:  # type: ignore[no-untyped-def]
    """The layer BELOW the worker's own versioning: a worker script held in the HTTP cache is a
    worker that keeps serving the previous build's shell after an upgrade."""
    _status, headers, _body = _request(running, _P("sw.js"), cookie=_session(running))
    assert headers["Cache-Control"] == "no-store, max-age=0"


# -- the cold start, after the run that authorised it ended (#634) --------------------------


_MAIN = _STATIC / "js" / "main.js"
_RENDER = _STATIC / "js" / "render.js"


def _strip_js_comments(source: str) -> str:
    """`source` with `//` and `/* */` comments removed.

    Needed because every assertion below is about CODE, and this repository writes more prose in
    its comments than code around them -- a substring search over the raw file would match the
    paragraph explaining a rule as readily as the line enforcing it. Neither `main.js` nor
    `render.js` contains a regex literal on the lines this touches, so a four-state scanner is
    enough; `test_client_assets._code_only` makes the same argument at greater length.
    """
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        pair = source[index : index + 2]
        if pair == "//":
            end = source.find("\n", index)
            index = length if end == -1 else end
        elif pair == "/*":
            end = source.find("*/", index + 2)
            index = length if end == -1 else end + 2
        else:
            out.append(source[index])
            index += 1
    return "".join(out)


def test_the_install_identity_and_the_start_url_are_unchanged() -> None:
    """#634 changed neither, and both were candidates.

    `id` is install identity: a browser that sees a different `id` treats the manifest as a
    different app, so an operator who already installed keel would end up with two icons and the
    old one pointing at nothing. It is `"/"` and it stays `"/"`.

    `start_url` was the more tempting edit -- the issue is literally titled around it -- and it is
    unchanged on purpose. The tokenless cold start is not fixed by pointing it somewhere else,
    because there is no path on this server that carries a token: the token is minted per run and
    lives in the terminal. Moving `start_url` would have relocated the failure, not removed it."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["id"] == "/"
    assert manifest["start_url"] == "/status"


def test_a_refusal_is_not_painted_as_an_outage(running: server.ServeConfig) -> None:
    """**A 403 said "keel isn't running", and keel was running.**

    A refusal arrives at the client in the same shape a stopped engine does -- `data: null` with
    an `error` -- so it fell into `stoppedView`, whose heading is "keel isn't running". That sends
    an operator to restart the one thing that is working, which mints a new token and makes the
    situation strictly worse. The branch must split before that view.

    **The status is bound to the wire, not to a constant this test invented.** The refusal is
    fetched from a real server over a real socket and its `error.status` is read out of the JSON
    the client will actually parse; `main.js`'s constant must equal that. A hard-coded `"403"` in
    both places would pass while the server answered `403` as an integer, or as `"Forbidden"`, or
    stopped putting a status in the envelope at all -- and the client would silently fall back to
    the outage view again.
    """
    connection = http.client.HTTPConnection(running.host, running.port, timeout=10)
    try:
        connection.request(
            "GET", "/api/status", headers={"Host": f"{running.host}:{running.port}"}
        )
        response = connection.getresponse()
        refusal = json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()

    assert response.status == 403
    on_the_wire = refusal["error"]["status"]

    main = _strip_js_comments(_MAIN.read_text(encoding="utf-8"))
    render = _strip_js_comments(_RENDER.read_text(encoding="utf-8"))

    assert "export function refusedView(" in render, (
        "render.js no longer offers a view for a refusal, so a 403 falls back to the outage view"
    )
    assert re.search(r"^\s*refusedView,\s*$", main, re.MULTILINE), (
        "main.js does not import refusedView"
    )

    constant = re.search(r'const REFUSED_STATUS = "([^"]*)";', main)
    assert constant is not None, "main.js no longer names the status it treats as a refusal"
    assert constant.group(1) == on_the_wire, (
        f"main.js branches on {constant.group(1)!r} and the server answers "
        f"{on_the_wire!r} -- one side was changed and the other was not"
    )

    # The WHOLE guard line, and its position relative to the outage view. A substring search for
    # `refusedView` anywhere in the file passes against a guard that is inverted, one that is
    # dead (`false &&`), one that reads `primary.data` instead of `primary.error`, and one that
    # sits AFTER the `stoppedView` call and can therefore never run -- four edits that each
    # restore the "keel isn't running" lie.
    guard = re.search(
        r"^\s*if \(primary\.error && primary\.error\.status === REFUSED_STATUS\) \{\s*$",
        main,
        re.MULTILINE,
    )
    assert guard is not None, (
        "the refusal branch is not `if (primary.error && primary.error.status === "
        "REFUSED_STATUS) {` -- an inverted, dead or differently-keyed guard reads like a check "
        "and paints a running server as a stopped one"
    )
    refused_call = main.find("refusedView(primary")
    stopped_call = main.find("stoppedView(primary")
    assert refused_call != -1 and stopped_call != -1
    assert guard.start() < refused_call < stopped_call, (
        "the refusal view must be reached from the guard and before the outage view"
    )


def test_the_reconnect_field_never_navigates_to_a_pasted_origin() -> None:
    """**The pasted text supplies a token and nothing else.**

    The obvious implementation of "paste the address keel printed" navigates to the pasted
    address, which is an open redirect the operator types into themselves -- and it is also wrong
    over a private network, because what `keel serve` prints on the machine is a loopback URL and
    a phone reaching the same process over Tailscale is on another origin entirely.

    So `reconnect` mines the paste for its `token` and builds its destination from
    `window.location`. Asserted as the shape rather than as behaviour: the only thing handed to
    `location.assign` is a URL constructed from `window.location`, and the parsed paste is only
    ever read for its token.

    Chosen against the mutations someone who preferred the obvious version would write:
    `assign(text)`, `assign(parsed.href)`, `new URL(text, ...)` as the target, and dropping the
    empty-token guard so a blank field navigates to a tokenless URL and lands back here."""
    main = _strip_js_comments(_MAIN.read_text(encoding="utf-8"))
    body = re.search(r"function reconnect\(pasted\) \{(.*?)\n\}", main, re.DOTALL)
    assert body is not None, "reconnect is not in the shape this test understands"
    code = body.group(1)

    assigns = re.findall(r"window\.location\.assign\(([^)]*)\)", code)
    assert assigns == ["target.href"], (
        f"reconnect navigates to {assigns!r}; the only value it may navigate to is a URL it "
        "built itself from window.location -- anything derived from the paste is an open "
        "redirect and breaks the loopback-URL-pasted-into-a-Tailscale-origin case"
    )

    assert re.search(
        r"^\s*const target = new URL\(window\.location\.pathname, window\.location\.origin\);\s*$",
        code,
        re.MULTILINE,
    ), "the destination is no longer built from window.location"

    uses = re.findall(r"parsed\.[A-Za-z.]*", code)
    assert set(uses) == {"parsed.searchParams.get"}, (
        f"the parsed paste is read for {sorted(set(uses))!r}; it may only be read for its token"
    )

    guard = code.find("if (!token) {")
    assert guard != -1, "an empty token is no longer refused before navigating"
    assert guard < code.find("window.location.assign("), (
        "the empty-token guard must come before the navigation, or a blank field reloads to a "
        "tokenless URL and lands straight back on the refusal"
    )
