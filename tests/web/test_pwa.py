"""The installable app: manifest, icons and service worker (#538).

Same line as `test_client_assets.py` draws, for the same reason: this proves **properties of the
shipped bytes** and what the server does with them over a real socket. It cannot prove that
Chrome offers to install the app, that the worker actually intercepts a navigation, or that the
shell paints with the engine stopped -- all three need a browser, and this repository is
deliberately Python-only.

What that leaves uncovered is listed once, honestly, and each item was checked by hand against a
running `keel serve` driven by a real Chromium; the results are in the PR body:

  * that the worker installs, activates and reaches `controlling` state;
  * that the precache is populated and holds exactly `PRECACHE`;
  * that the shell still paints after `keel serve` is killed, with no figures on it;
  * that a changed build string swaps the cache and deletes the old one;
  * that the manifest and its icons are fetched with credentials rather than refused.

The one thing this module CAN prove about the API rule is stronger than a browser check anyway:
`/api/*` is outside the worker's scope, so no code in the worker can reach it. That is asserted
below as a property of the two path constants rather than as a property of a running browser.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from keel.web import staticfiles
from scripts import build_icons

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


def _png_size(payload: bytes) -> tuple[int, int]:
    """Width and height from a PNG's IHDR, which is always the first chunk."""
    assert payload[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")


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


def test_the_api_prefix_is_outside_the_workers_scope() -> None:
    """The rule the whole issue is about, asserted as arithmetic on two constants.

    This is stronger than any browser check: it is not that the worker declines to cache `/api/*`,
    it is that the browser never consults the worker for those requests at all. Nothing that could
    be written inside `sw.js` changes this.
    """
    source = _sw_source()
    base = re.search(r'const BASE = "([^"]+)"', source)
    api = re.search(r'const API_PREFIX = "([^"]+)"', source)
    assert base is not None and api is not None
    assert base.group(1) == staticfiles.STATIC_PREFIX
    assert not api.group(1).startswith(base.group(1)), (
        f"{api.group(1)} is inside {base.group(1)} -- the scope no longer excludes the API"
    )


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


def test_the_cache_name_is_keyed_to_the_build() -> None:
    """A `CacheFirst` shell with a fixed cache name survives an engine upgrade and answers the new
    API with the old contract -- everything renders, only the fields are wrong."""
    source = _sw_source()
    assert 'searchParams.get("v")' in source, "the worker does not read a build from its own URL"
    assert re.search(r"const CACHE = `keel-shell-\$\{BUILD\}`", source), (
        "the cache name does not carry the build"
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
        ("/static/sw.js", "text/javascript; charset=utf-8"),
        ("/static/manifest.webmanifest", "application/manifest+json"),
        ("/static/icons/keel.svg", "image/svg+xml"),
        ("/static/icons/keel-192.png", "image/png"),
        ("/static/icons/keel-512.png", "image/png"),
        ("/static/icons/keel-maskable-512.png", "image/png"),
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
    for path in ("/static/sw.js", "/static/manifest.webmanifest", "/static/icons/keel-192.png"):
        status, _headers, _body = _request(running, path)  # no cookie
        assert status == 403, path


def test_the_served_manifest_is_valid_json(running) -> None:  # type: ignore[no-untyped-def]
    """Parsed off the wire, not off disk: a manifest that is correct in the repository and
    mangled by the server is a manifest the browser rejects."""
    _status, _headers, body = _request(
        running, "/static/manifest.webmanifest", cookie=_session(running)
    )
    manifest = json.loads(body)
    assert manifest["name"] == "keel"
    assert manifest["display"] == "standalone"


def test_the_worker_is_served_with_no_store(running) -> None:  # type: ignore[no-untyped-def]
    """The layer BELOW the worker's own versioning: a worker script held in the HTTP cache is a
    worker that keeps serving the previous build's shell after an upgrade."""
    _status, headers, _body = _request(running, "/static/sw.js", cookie=_session(running))
    assert headers["Cache-Control"] == "no-store, max-age=0"
