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
    resolved = staticfiles.resolve_static_asset(root, "/static/index.html")
    assert resolved == root / "index.html"


def test_a_nested_file_resolves(root: Path) -> None:
    resolved = staticfiles.resolve_static_asset(root, "/static/sub/nested.html")
    assert resolved == root / "sub" / "nested.html"


def test_a_path_missing_the_prefix_does_not_resolve(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, "/index.html") is None


def test_a_missing_file_does_not_resolve(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, "/static/nope.html") is None


def test_a_directory_is_not_a_file(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, "/static/sub") is None
    assert staticfiles.resolve_static_asset(root, "/static/sub/") is None


def test_the_bare_prefix_does_not_resolve(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, "/static/") is None


# -- the entire point of the module ------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "/static/../secret.txt",
        "/static/../../secret.txt",
        "/static/sub/../../secret.txt",
        "/static/%2e%2e/secret.txt",
        "/static/%2e%2e%2f%2e%2e/secret.txt",
        "/static//../secret.txt",
        "/static/./../secret.txt",
        # The specific footgun the module docstring names: `Path("/root") / "/etc/passwd"`
        # discards the root entirely rather than raising. Both a raw absolute path and its
        # percent-encoded spelling must be caught by the same containment check.
        "/static//etc/passwd",
        "/static/%2fetc%2fpasswd",
    ],
)
def test_directory_traversal_payloads_never_escape_the_root(root: Path, payload: str) -> None:
    assert staticfiles.resolve_static_asset(root, payload) is None


def test_a_null_byte_is_refused_not_raised(root: Path) -> None:
    assert staticfiles.resolve_static_asset(root, "/static/index.html\x00.png") is None


def test_a_doubly_encoded_traversal_is_inert_not_decoded_twice(root: Path) -> None:
    """`%252e%252e` decodes ONCE, here, to the literal string `%2e%2e` -- not to `..`. It must
    therefore fail to resolve to any real file, exactly like any other nonsense path, rather
    than be walked a second time into an escape."""
    assert staticfiles.resolve_static_asset(root, "/static/%252e%252e/secret.txt") is None


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
    resolved = staticfiles.resolve_static_asset(root, "/static/unknown.exe")
    assert resolved == root / "unknown.exe"  # resolves fine -- the type check is a SEPARATE gate
    assert staticfiles.content_type_for(resolved) is None
