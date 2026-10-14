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

**The traversal defence is a lookup in a listing, not a join and not string matching.** A check
for the literal substring `".."` is a losing game against encoding (`%2e%2e`), redundant
separators, and symlinks, and it also has a sharper failure mode that is easy to miss entirely:
`Path`'s `/` operator DISCARDS the left side when the right side is absolute --
`Path("/srv/static") / "/etc/passwd"` is `Path("/etc/passwd")`, not an error and not a joined
path. A resolver that only rejected strings containing `".."` would serve `/etc/passwd` to a
request for `/static//etc/passwd` and never notice.

So the request path is never joined onto the root at all. `_servable_files` walks the root -- the
package's own directory, a fixed location -- and lists every regular file whose REAL location
(`resolve()`, symlinks followed) is still under the root (it does not descend into symlinked
directories at all); `resolve_static_asset` then answers by
exact name from that listing. A request path is only ever COMPARED with names the package
produced, never handed to a filesystem call, so there is no spelling of it -- `..`, an absolute
path, `%2e%2e`, a null byte, a symlinked file planted inside the root that points out of it --
that can name a file the listing does not already hold. Until the CodeQL alerts #3/#5/#7 this was
`(root / rel).resolve()` then `relative_to(root)`, which enforced the same containment but walked
a user-supplied path to get there; the listing is at least as strict (it also refuses
non-canonical spellings such as `sub/../index.html`, which no browser sends) and puts no request
byte near the filesystem. The walk is per call, over a directory of a few dozen files.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

#: Where the shipped assets live, inside the installed package. A wheel build ships this
#: directory because `pyproject.toml`'s `artifacts` glob (`keel/web/static/**`) says to; without
#: that glob the directory exists in a checkout and nowhere else, which is the exact bug #535
#: exists to close (see the comment beside that glob).
STATIC_ROOT = Path(__file__).resolve().parent / "static"

#: The URL prefix static requests are served under, and the prefix the client mounts at.
#:
#: **`/` since #540.** It was `/static/` from #535 to #539 only because the rendered-page table
#: (`ROUTES` in `server`, deleted at #540) still owned the root paths; deleting the rendered pages
#: freed them, and the client is the application now
#: rather than a second one parked beside it. The move was one edit per file rather than a hunt
#: because `tests/web/test_client_assets.py::test_the_mount_prefix_is_spelled_the_same_everywhere`
#: has pinned the three places that spell it -- here, `index.html`'s hrefs and `main.js`'s `BASE` --
#: since the prefix was introduced.
#:
#: **The consequence worth stating: this prefix no longer NARROWS anything.** While it was
#: `/static/`, `url_path.startswith(STATIC_PREFIX)` was itself a filter, and `resolve_static_asset`
#: could not be reached by a request for `/api/pulse`. At `/` every path passes that test, so the
#: containment in `resolve_static_asset` -- an exact-name lookup in `_servable_files` -- is the only
#: thing keeping a request inside the static root, and `server.do_GET` orders `/api/` ahead of
#: static resolution rather than relying on the prefix to separate them.
STATIC_PREFIX = "/"

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


def _servable_files(root: Path) -> dict[str, Path]:
    """Every regular file under `root`, by its request name (`relpath`, POSIX separators), mapped
    to its real location. Symlinked DIRECTORIES are not descended into (`Path.rglob` does not
    follow them by default), so nothing beneath one is listed, whether it points inside the root
    or outside it. A symlinked FILE is listed only if its resolved target is under `root`; that is
    what the `is_relative_to` filter is for. So the listing can never name a file the root does
    not hold. Built from the filesystem alone; no request path reaches
    it (see the module docstring)."""
    root_resolved = root.resolve()
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        real = path.resolve()  # non-strict: a symlink loop resolves to a non-file, not an error
        if not real.is_relative_to(root_resolved) or not real.is_file():
            continue
        files[path.relative_to(root).as_posix()] = real
    return files


def resolve_static_asset(root: Path, url_path: str) -> Path | None:
    """A file inside `root` for a request path under `STATIC_PREFIX`, or `None`.

    `None` covers three cases the caller must treat identically -- as a 404, never as a 500 or
    as "serve something close enough": the path does not start with the static prefix, no file
    of that exact name is in `_servable_files`'s listing (missing, a directory, a non-canonical
    spelling of a real file), or the only file of that name lives outside `root` once symlinks
    are followed. Decoded ONCE (`unquote`), matching `http.server`'s own behaviour and the
    browser's: a doubly-encoded `%252e%252e` decodes here to the literal, inert string `%2e%2e`,
    not to `..` -- and then, like a null byte or `..` itself, simply names nothing in the listing.
    The returned `Path` is the listing's own value, never one built from the request.
    """
    if not url_path.startswith(STATIC_PREFIX):
        return None
    rel = unquote(url_path[len(STATIC_PREFIX) :])
    return _servable_files(root).get(rel)


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
#: "that file is not there." With a closed list, a missing asset is still a 404 and only the names
#: in `CLIENT_ROUTES` and `RETIRED_CLIENT_ROUTES` ever reach the shell.
#:
#: `tests/web/test_client_assets.py::test_the_python_and_javascript_route_tables_agree` parses
#: `main.js`'s `ROUTES` and compares it to this tuple, so a view added on one side without the
#: other fails the build rather than 404ing in a browser nobody is running in CI.
CLIENT_ROUTES: tuple[str, ...] = (
    "pulse",
    "holdings",
    "rules",
    "audit",
)

#: The shell every client route is served from, relative to `STATIC_ROOT`.
CLIENT_ENTRY = "index.html"

#: Names that WERE client routes and are not any more, answered with the shell all the same.
#: `main.js`'s `routeFor` sends a name it does not know to the home view and rewrites the address
#: bar (its docstring: "a typo or a link written before a rename ... the landing page is more use
#: than an error"). That is only true if the server delivers the shell; without it the old path is
#: a plain-text 404 for a bookmark, an installed app whose cached manifest still says
#: `start_url: "/status"`, or a `/status?token=...` redirect, whenever no service worker answers.
#: **Closed, like `CLIENT_ROUTES`**, for the same reason: it is names, never a wildcard. Console
#: PR3 added Insights and Research (folded into Rules); PR4 added Setup, Timeline and Venues
#: (folded into Audit & Setup) and Balances and Plans. `main.js`'s `MOVED_ROUTES` sends every
#: name whose content moved -- positions and orders to Holdings, insights and research to Rules,
#: setup, timeline and venues to Audit & Setup -- where it now lives rather than home (#969).
#: Disjoint from `CLIENT_ROUTES`.
RETIRED_CLIENT_ROUTES: tuple[str, ...] = (
    "status",
    "activity",
    "gates",
    "orders",
    "positions",
    "insights",
    "research",
    "setup",
    "timeline",
    "balances",
    "venues",
    "plans",
)


def resolve_client_route(root: Path, url_path: str) -> Path | None:
    """The client shell for a path the BROWSER routes, or `None`.

    Called only after `resolve_static_asset` has returned `None`, so a real file always wins and
    this can never shadow one. The three paths that resolve here are `STATIC_PREFIX` itself,
    `STATIC_PREFIX` plus a name in `CLIENT_ROUTES` or `RETIRED_CLIENT_ROUTES`, and nothing else --
    an unknown name is `None` and becomes the same 404 any other unmapped path gets.

    **Why the server has to know about client routes at all.** `main.js` routes with the History
    API rather than a hash, so `pushState` puts `/static/rules` in the address bar; without
    this function, reloading that page or opening it from a bookmark asks the server for a file
    that does not exist. Hash routing would need no server cooperation, and was rejected for
    putting a `#` in every URL an operator copies -- see `main.js`'s module docstring. This is the
    fifteen lines that buys.

    **This is a read, and it widens nothing.** It maps the names in `CLIENT_ROUTES` and
    `RETIRED_CLIENT_ROUTES` onto one file that is already served at
    `STATIC_PREFIX + CLIENT_ENTRY`; the same session cookie and the same `Host` check gate it,
    because `server.do_GET` checks both before any static path is looked at. The one difference
    from an `/api/` read is the remote session lifetime: a path this function or
    `resolve_static_asset` answers is served past it to this session's cookie (#1006,
    `server.KeelHandler._is_a_sessioned_shell_read`, which asks `shell_request_paths`, the list of
    the paths these two functions answer), because the shell is files and the expired Pulse
    refusal it reads is what carries the HALT (#985).
    """
    if not url_path.startswith(STATIC_PREFIX):
        return None
    name = unquote(url_path[len(STATIC_PREFIX) :])
    if name and name not in CLIENT_ROUTES and name not in RETIRED_CLIENT_ROUTES:
        return None

    shell = (root / CLIENT_ENTRY).resolve()
    try:
        shell.relative_to(root.resolve())
    except ValueError:  # pragma: no cover - `CLIENT_ENTRY` is a fixed, relative literal
        return None
    if not shell.is_file():
        return None
    return shell


def shell_request_paths(root: Path) -> frozenset[str]:
    """Every request path the shell answers, spelled out (#1006).

    `STATIC_PREFIX + relpath` for each file under `root` that `content_type_for` can label (the
    files `resolve_static_asset` would serve), `STATIC_PREFIX` itself, and `STATIC_PREFIX` plus
    each name in `CLIENT_ROUTES` and `RETIRED_CLIENT_ROUTES` (the paths `resolve_client_route`
    answers with the shell).

    **Why enumerate rather than resolve.** `server.KeelHandler._is_a_sessioned_shell_read` asks
    "is this request path one the shell answers" and compares against this set, so the request
    path is only ever COMPARED, never handed to a filesystem call: what is built here comes from
    the package's own directory listing (`_servable_files`, the same one `resolve_static_asset`
    answers from). Its callers are that session check, reached only once the remote session
    lifetime has expired, and `shell_redirect_target`, reached only by a token exchange; the walk
    is per call, as `resolve_static_asset`'s is.
    """
    names = {
        STATIC_PREFIX + name
        for name, path in _servable_files(root).items()
        if content_type_for(path) is not None
    }
    names.add(STATIC_PREFIX)
    names.update(STATIC_PREFIX + name for name in (*CLIENT_ROUTES, *RETIRED_CLIENT_ROUTES))
    return frozenset(names)


def shell_redirect_target(root: Path, url_path: str) -> str:
    """The path the token exchange redirects to: the member of `shell_request_paths` that
    `url_path` names once decoded, else `STATIC_PREFIX` (CodeQL alert #2).

    **The answer is always one of the set's own strings, never the request's.** The redirect's
    `Location` used to be the request path as sent, which put request bytes into a response
    header; `http.server` splits the request line on whitespace, so a raw CR or LF never reached
    it, but a backslash did (`/\\host` is the protocol-relative `//host` to a browser), and a
    header built from what the client sent is one safe only for as long as every layer beneath
    it stays that way. Comparing against the closed set and returning the matched member means no
    byte of the request can be in the header at all. A `?token=` link to any path the console
    does not answer -- an `/api/` route, a typo -- lands on the home view, which is where the
    printed address sends everyone anyway.
    """
    wanted = unquote(url_path)
    for known in shell_request_paths(root):
        if known == wanted:
            return known
    return STATIC_PREFIX
