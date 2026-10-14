"""`keel/web/staticfiles.py` -- the resolver in isolation, against a synthetic root.

The wire-level exercise of the same code lives in `tests/web/test_server.py`, against the one
placeholder asset the package actually ships. These tests are the ones that can try payloads a
real filesystem would not conveniently hold still for -- an escape through a directory that may
not even exist on the test machine -- without needing one to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from keel.web import staticfiles


def _P(rest: str) -> str:
    """A request path under the mount, composed from the constant rather than spelled.

    These paths were literal `/static/...` strings until #540 moved the mount to `/`, and every
    one of them had to be edited. Composed, the next move is none."""
    return staticfiles.STATIC_PREFIX + rest


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<html>hi</html>")
    (tmp_path / "style.css").write_text("body {}")
    (tmp_path / "app.js").write_text("console.log(1)")
    (tmp_path / "icon.svg").write_text("<svg></svg>")
    (tmp_path / "unknown.exe").write_bytes(b"MZ")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.html").write_text("<html>nested</html>")
    # A sibling of the static root -- the thing every traversal payload below is reaching for.
    (tmp_path.parent / "secret.txt").write_text("do not serve me")
    return tmp_path


def test_a_plain_file_resolves(root: Path) -> None:
    resolved = staticfiles.resolve_static_asset(root, _P("index.html"))
    assert resolved == root / "index.html"


def test_a_nested_file_resolves(root: Path) -> None:
    resolved = staticfiles.resolve_static_asset(root, _P("sub/nested.html"))
    assert resolved == root / "sub" / "nested.html"


def test_a_path_missing_the_prefix_does_not_resolve(root: Path) -> None:
    """The prefix check still refuses what does not carry it -- there is just far less it refuses.

    **This test used to pass `/index.html` and expect `None`**, which was true while the mount was
    `/static/`. At `/` that path IS under the prefix, and the assertion would have been asserting
    the opposite of the truth. The check itself is unchanged and still rejects a path that does
    not begin with the mount; what changed is that almost nothing is such a path any more, which
    is why `resolve_static_asset`'s containment check -- not this prefix -- is what actually keeps
    a request inside the static root. `test_a_traversal_never_escapes_the_root` is that one."""
    assert staticfiles.resolve_static_asset(root, "index.html") is None
    assert staticfiles.resolve_static_asset(root, "") is None


def test_a_missing_file_does_not_resolve(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, _P("nope.html")) is None


def test_a_directory_is_not_a_file(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, _P("sub")) is None
    assert staticfiles.resolve_static_asset(root, _P("sub/")) is None


def test_the_bare_prefix_does_not_resolve(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, _P("")) is None


# -- the entire point of the module ------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        _P("../secret.txt"),
        _P("../../secret.txt"),
        _P("sub/../../secret.txt"),
        _P("%2e%2e/secret.txt"),
        _P("%2e%2e%2f%2e%2e/secret.txt"),
        _P("/../secret.txt"),
        _P("./../secret.txt"),
        # The specific footgun the module docstring names: `Path("/root") / "/etc/passwd"`
        # discards the root entirely rather than raising. Both a raw absolute path and its
        # percent-encoded spelling must be caught by the same containment check.
        _P("/etc/passwd"),
        _P("%2fetc%2fpasswd"),
    ],
)
def test_directory_traversal_payloads_never_escape_the_root(root: Path, payload: str) -> None:
    assert staticfiles.resolve_static_asset(root, payload) is None


def test_a_null_byte_is_refused_not_raised(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, _P("index.html\x00.png")) is None


def test_a_doubly_encoded_traversal_is_inert_not_decoded_twice(root: Path) -> None:
    """`%252e%252e` decodes ONCE, here, to the literal string `%2e%2e` -- not to `..`. It must
    therefore fail to resolve to any real file, exactly like any other nonsense path, rather
    than be walked a second time into an escape."""
    assert staticfiles.resolve_static_asset(root, _P("%252e%252e/secret.txt")) is None


def test_a_symlink_out_of_the_root_is_refused(root: Path) -> None:
    """A file inside the root that POINTS outside it is not inside it. The listing the resolver
    answers from keeps only entries whose real location is under the root."""
    (root / "leak.txt").symlink_to(root.parent / "secret.txt")
    assert staticfiles.resolve_static_asset(root, _P("leak.txt")) is None


def test_a_symlinked_directory_out_of_the_root_is_refused(root: Path) -> None:
    outside = root.parent / "outside-dir"
    outside.mkdir()
    (outside / "inner.txt").write_text("do not serve me either")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    assert staticfiles.resolve_static_asset(root, _P("linked/inner.txt")) is None


def test_a_symlink_that_stays_inside_the_root_resolves_to_its_target(root: Path) -> None:
    (root / "alias.css").symlink_to(root / "style.css")
    assert staticfiles.resolve_static_asset(root, _P("alias.css")) == (root / "style.css").resolve()


def test_a_symlink_loop_in_the_root_names_nothing_and_breaks_nothing(root: Path) -> None:
    (root / "loop-a").symlink_to(root / "loop-b")
    (root / "loop-b").symlink_to(root / "loop-a")
    assert staticfiles.resolve_static_asset(root, _P("loop-a")) is None
    assert staticfiles.resolve_static_asset(root, _P("index.html")) == root / "index.html"


@pytest.mark.parametrize(
    "spelling",
    [
        _P("sub/../index.html"),
        _P("./index.html"),
        _P("sub//nested.html"),
        _P("sub/./nested.html"),
        _P("sub%2fnested.html/"),
    ],
)
def test_a_non_canonical_spelling_of_a_real_file_is_refused_not_normalised(
    root: Path, spelling: str
) -> None:
    """The request is looked up, by exact name, in the list of files the root holds -- it is never
    joined onto the root and walked. A browser canonicalises `.` and `..` before it sends a
    request, so the only client that sends these spellings is one probing the resolver, and it
    gets the same 404 as any other name the listing does not hold."""
    assert staticfiles.resolve_static_asset(root, spelling) is None


# -- content types ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("index.html", "text/html; charset=utf-8"),
        ("style.css", "text/css; charset=utf-8"),
        ("app.js", "text/javascript; charset=utf-8"),
        ("worker.mjs", "text/javascript; charset=utf-8"),
        ("manifest.webmanifest", "application/manifest+json"),
        ("data.json", "application/json; charset=utf-8"),
        ("icon.svg", "image/svg+xml"),
        ("icon.png", "image/png"),
        ("favicon.ico", "image/x-icon"),
        ("notes.txt", "text/plain; charset=utf-8"),
    ],
)
def test_known_extensions_map_to_the_right_content_type(name: str, expected: str) -> None:
    assert staticfiles.content_type_for(Path(name)) == expected


def test_an_unknown_extension_has_no_content_type() -> None:
    """No entry, not a guess. `mimetypes.guess_type` would happily return something for `.exe`;
    the caller must refuse instead of serving a file it cannot correctly label."""
    assert staticfiles.content_type_for(Path("payload.exe")) is None


def test_an_unknown_extension_does_not_resolve_even_though_the_file_exists(root: Path) -> None:
    """`resolve_static_asset` only proves the path is safe; `server.py` is expected to also
    check `content_type_for` before serving. This pins that the file existing is not, by
    itself, enough -- the type table is where an unrecognised kind gets refused."""
    resolved = staticfiles.resolve_static_asset(root, _P("unknown.exe"))
    assert resolved == root / "unknown.exe"  # resolves fine -- the type check is a SEPARATE gate
    assert staticfiles.content_type_for(resolved) is None


# -- every path the shell answers (#1006) ---------------------------------------------------------


def test_shell_request_paths_name_every_servable_file_and_client_route(tmp_path: Path) -> None:
    """The enumeration is built from the package side only, so a request path never reaches the
    filesystem. Exact membership for the mount, a shipped file, and a live and a retired client
    route; the count is files-with-a-known-type, plus the mount, plus every route name; a file
    whose extension has no Content-Type is absent."""
    paths = staticfiles.shell_request_paths(staticfiles.STATIC_ROOT)

    for expected in ("/", "/index.html", "/js/main.js", "/pulse", "/status"):
        assert expected in paths, expected
    files = [
        p
        for p in staticfiles.STATIC_ROOT.rglob("*")
        if p.is_file() and staticfiles.content_type_for(p) is not None
    ]
    routes = len(staticfiles.CLIENT_ROUTES) + len(staticfiles.RETIRED_CLIENT_ROUTES)
    assert len(files) >= 10
    assert len(paths) == len(files) + 1 + routes

    (tmp_path / "index.html").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.js").write_text("x")
    (tmp_path / "keel.bin").write_text("x")
    planted = staticfiles.shell_request_paths(tmp_path)
    assert _P("keel.bin") not in planted
    assert _P("sub/a.js") in planted
    assert len(planted) == 2 + 1 + routes
