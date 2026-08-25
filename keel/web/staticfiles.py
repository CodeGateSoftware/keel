"""Static asset serving for `keel serve` (#535).

The client that consumes this -- plain ES modules, a stylesheet -- landed in #536 and lives in
`static/`. This module answers two questions about a request path, and they are different
questions: `resolve_static_asset` answers "which FILE does this name", and `resolve_client_route`
answers "is this one of the paths the browser routes itself", which names no file and is served
with the shell. Both sit on the same origin as the rendered routes (§"Static assets and the API
share ONE origin" in the design spec).

**Why a hand-written resolver and not `http.server.SimpleHTTPRequestHandler`.** The stdlib
handler roots itself at the process's current working directory, which is wherever `keel serve`
happened to be launched from -- not a fixed location inside the package. Wiring it up safely
would mean overriding `translate_path` anyway, at which point there is nothing left to reuse.

**The traversal defence is `resolve()` then `relative_to()`, not string matching.** A check for
the literal substring `".."` is a losing game against encoding (`%2e%2e`), redundant separators,
and symlinks, and it also has a sharper failure mode that is easy to miss entirely: `Path`'s `/`
operator DISCARDS the left side when the right side is absolute --
`Path("/srv/static") / "/etc/passwd"` is `Path("/etc/passwd")`, not an error and not a joined
path. A resolver that only rejected strings containing `".."` would serve `/etc/passwd` to a
request for `/static//etc/passwd` and never notice. Resolving the candidate against the
filesystem and then asking whether it is still `relative_to` the static root catches both
failure modes with one check, because it asks the only question that matters: where does this
path ACTUALLY point, not what does its spelling suggest.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

#: Where the shipped assets live, inside the installed package. A wheel build ships this
#: directory because `pyproject.toml`'s `artifacts` glob (`keel/web/static/**`) says to; without
#: that glob the directory exists in a checkout and nowhere else, which is the exact bug #535
#: exists to close (see the comment beside that glob).
STATIC_ROOT = Path(__file__).resolve().parent / "static"

#: The URL prefix static requests are served under, and (#536) the prefix the client mounts at.
#: Kept off `/` and the rest of `server.py`'s `ROUTES` table on purpose: the rendered pages still
#: own the root paths until #540 deletes them, so nothing here can collide with current routing.
#:
#: This string is spelled in three places -- here, `index.html`'s hrefs, and `main.js`'s `BASE` --
#: and `tests/web/test_client_assets.py::test_the_mount_prefix_is_spelled_the_same_everywhere`
#: pins that the three agree, so that moving the shell to `/` at #540 is one edit per file rather
#: than a hunt for the ones that were missed.
STATIC_PREFIX = "/static/"

#: Content-Type by extension, spelled out rather than left to `mimetypes.guess_type`. The stdlib
#: table is OS-dependent -- some platforms still answer a `.js` file with `text/plain` -- and a
#: wrong type paired with `X-Content-Type-Options: nosniff` (added below) is a script the
#: browser refuses to RUN rather than one it sniffs its way around the mistake for. So the type
#: has to be correct, not merely present, and an extension absent from this table is refused
#: (404) rather than guessed: a "correct" 404 is safer than a wrong Content-Type served with
#: confidence.
_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


def content_type_for(path: Path) -> str | None:
    """The Content-Type for a static file, by its extension. `None` for an extension this server
    does not know how to label -- the caller's job is to refuse, not to guess."""
    return _CONTENT_TYPES.get(path.suffix.lower())


def resolve_static_asset(root: Path, url_path: str) -> Path | None:
    """A file inside `root` for a request path under `STATIC_PREFIX`, or `None`.

    `None` covers three cases the caller must treat identically -- as a 404, never as a 500 or
    as "serve something close enough": the path does not start with the static prefix, the
    computed target does not exist or is not a plain file, or the target would land outside
    `root` once symlinks and `..` segments are resolved. Decoded ONCE (`unquote`), matching
    `http.server`'s own behaviour and the browser's: a doubly-encoded `%252e%252e` decodes here
    to the literal, inert string `%2e%2e`, not to `..`.
    """
    if not url_path.startswith(STATIC_PREFIX):
        return None
    rel = unquote(url_path[len(STATIC_PREFIX) :])
    if not rel or "\x00" in rel:
        # A null byte truncates a C string in some filesystem APIs beneath Python's own; refused
        # here rather than let `resolve()` raise it as an uncaught `ValueError` a layer up.
        return None

    root_resolved = root.resolve()
    try:
        # See the module docstring: `root / rel` alone is not a safe join when `rel` can be
        # absolute. `resolve()` then `relative_to()` is what actually enforces containment,
        # regardless of how `rel` got here.
        candidate = (root / rel).resolve()
        candidate.relative_to(root_resolved)
    except ValueError:
        return None

    if not candidate.is_file():
        return None
    return candidate


# -- the client's own routes (#536) ---------------------------------------------------------------
#
# Everything above this line answers "which FILE does this path name". Everything below answers a
# different question: "is this path one the CLIENT routes in the browser", which has no file of
# its own and must be answered with the shell.


#: The view names `keel/web/static/js/main.js` routes with the History API, in that module's own
#: order. `/glossary` is deliberately absent from both -- `api.py`'s route table records why: it
#: "becomes an outbound keeltrading.com link in #539 and `render_glossary` is deleted in #540, so
#: an `/api/glossary` would be a surface built in order to be removed."
#:
#: **A closed list, never a wildcard, and the difference is not stylistic.** The obvious
#: implementation of single-page-app deep linking is "serve `index.html` for anything under the
#: prefix that is not a file", and it has a failure mode that is genuinely hard to debug: a
#: mistyped or missing `.js` asset stops being a 404 and becomes a 200 carrying HTML with a
#: `text/html` Content-Type. The browser then refuses to execute it -- correctly, under `nosniff`
#: -- and reports a MIME-type error naming the module, which is several steps removed from
#: "that file is not there." With a closed list, a missing asset is still a 404 and only these
#: seven names ever reach the shell.
#:
#: `tests/web/test_client_assets.py::test_the_python_and_javascript_route_tables_agree` parses
#: `main.js`'s `ROUTES` and compares it to this tuple, so a view added on one side without the
#: other fails the build rather than 404ing in a browser nobody is running in CI.
CLIENT_ROUTES: tuple[str, ...] = (
    "status",
    "setup",
    "activity",
    "insights",
    "rules",
    "venues",
    "gates",
)

#: The shell every client route is served from, relative to `STATIC_ROOT`.
CLIENT_ENTRY = "index.html"


def resolve_client_route(root: Path, url_path: str) -> Path | None:
    """The client shell for a path the BROWSER routes, or `None`.

    Called only after `resolve_static_asset` has returned `None`, so a real file always wins and
    this can never shadow one. The three paths that resolve here are `STATIC_PREFIX` itself,
    `STATIC_PREFIX` plus a name in `CLIENT_ROUTES`, and nothing else -- an unknown name is `None`
    and becomes the same 404 any other unmapped path gets.

    **Why the server has to know about client routes at all.** `main.js` routes with the History
    API rather than a hash, so `pushState` puts `/static/insights` in the address bar; without
    this function, reloading that page or opening it from a bookmark asks the server for a file
    that does not exist. Hash routing would need no server cooperation, and was rejected for
    putting a `#` in every URL an operator copies -- see `main.js`'s module docstring. This is the
    fifteen lines that buys.

    **This is a read, and it widens nothing.** It maps seven names onto one file that is already
    served at `STATIC_PREFIX + CLIENT_ENTRY`; the same session cookie and the same `Host` check
    gate it, because `server.do_GET` checks both before any static path is looked at.
    """
    if not url_path.startswith(STATIC_PREFIX):
        return None
    name = unquote(url_path[len(STATIC_PREFIX) :])
    if name and name not in CLIENT_ROUTES:
        return None

    shell = (root / CLIENT_ENTRY).resolve()
    try:
        shell.relative_to(root.resolve())
    except ValueError:  # pragma: no cover - `CLIENT_ENTRY` is a fixed, relative literal
        return None
    if not shell.is_file():
        return None
    return shell
