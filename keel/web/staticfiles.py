"""Static asset serving for `keel serve` (#535).

The client that consumes this -- plain ES modules, a stylesheet, icons -- is #536 and does not
exist yet. What ships here is the serving capability itself, on the same origin as the existing
rendered routes (§"Static assets and the API share ONE origin" in the design spec), plus one
placeholder asset so the path is exercised end to end rather than merely reachable in theory.

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

#: The URL prefix static requests are served under. Kept off `/` and the rest of `server.py`'s
#: `ROUTES` table on purpose: the rendered pages still own the root paths today, and #536's
#: client does not exist yet, so nothing here can collide with current routing. When the client
#: lands, its entry point is served from under this prefix like everything else it ships.
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
