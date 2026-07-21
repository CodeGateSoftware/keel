"""Compose release notes that INLINE each merged PR's body, instead of linking to it.

The release workflow previously called GitHub's `releases/generate-notes` API, which returns a
flat/categorised list of `* <title> by @author in #N` -- titles and links only, so a reader had
to click through every PR to learn what actually shipped. This module renders, per PR, a
`### <title> (#N)` heading followed by the PR's **cleaned** description.

Grouping is driven by `.github/release.yml` (the single source of truth), so labels keep working
exactly as documented in `docs/RELEASING.md`: the first category whose labels intersect the PR's
labels wins, an unlabelled PR lands in the `"*"` catch-all, and `norelease` PRs are dropped.

This is release tooling -- it is deliberately NOT part of the shipped `keel` package.

Usage (from the release workflow):

    gh api ... | python scripts/release_notes.py > notes-body.md

reading a JSON array of `{number, title, body, labels}` on stdin.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

#: PRs carrying this label never appear in the notes (mirrors `.github/release.yml`).
EXCLUDE_LABEL = "norelease"

#: Rendered in place of an empty description, so an entry is never a silent blank.
NO_DESCRIPTION = "_(no description)_"

# Everything from the Claude Code footer onward is tooling noise, not release content.
_FOOTER_MARKER = "🤖 Generated with"
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_COAUTHOR_RE = re.compile(r"^[ \t]*Co-[Aa]uthored-[Bb]y:.*$", re.MULTILINE)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class PullRequest:
    """One merged PR, as fetched from the GitHub API."""

    number: int
    title: str
    body: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Category:
    """A notes section. `labels == ("*",)` marks the catch-all, which must come last."""

    title: str
    labels: tuple[str, ...]


def clean_pr_body(body: str | None) -> str:
    """Strip tooling noise from a PR description.

    Removes the Claude Code footer (and everything after it), HTML comments, and
    `Co-Authored-By:` trailers, then collapses runs of blank lines. Returns `""` for an
    absent or whitespace-only body -- callers substitute `NO_DESCRIPTION`.
    """
    if not body:
        return ""

    text = body.replace("\r\n", "\n")

    marker = text.find(_FOOTER_MARKER)
    if marker != -1:
        text = text[:marker]

    text = _HTML_COMMENT_RE.sub("", text)
    text = _COAUTHOR_RE.sub("", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def load_categories(path: str | Path = ".github/release.yml") -> list[Category]:
    """Read the notes categories from `.github/release.yml`, preserving their order.

    Order matters twice: the first matching category wins, and the `"*"` catch-all is last so a
    labelled PR never falls into it by accident.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    categories = (raw.get("changelog") or {}).get("categories") or []
    return [
        Category(title=str(entry["title"]), labels=tuple(entry.get("labels") or ()))
        for entry in categories
    ]


def categorize(
    prs: list[PullRequest], categories: list[Category]
) -> list[tuple[Category, list[PullRequest]]]:
    """Bucket PRs into categories, dropping `norelease` and empty sections.

    Returns `(category, prs)` pairs in the category order given, omitting any category that
    ended up empty.
    """
    buckets: dict[str, list[PullRequest]] = {c.title: [] for c in categories}

    for pr in prs:
        labels = set(pr.labels)
        if EXCLUDE_LABEL in labels:
            continue
        for category in categories:
            if "*" in category.labels or labels & set(category.labels):
                buckets[category.title].append(pr)
                break

    return [(c, buckets[c.title]) for c in categories if buckets[c.title]]


def compose_release_notes(prs: list[PullRequest], categories: list[Category]) -> str:
    """Render the grouped, body-inlined change list as markdown."""
    sections: list[str] = []

    for category, bucket in categorize(prs, categories):
        lines = [f"## {category.title}", ""]
        for pr in bucket:
            body = clean_pr_body(pr.body) or NO_DESCRIPTION
            lines += [f"### {pr.title} (#{pr.number})", "", body, ""]
        sections.append("\n".join(lines).rstrip())

    return "\n\n".join(sections)


def _pr_from_json(entry: dict) -> PullRequest:
    return PullRequest(
        number=int(entry["number"]),
        title=str(entry.get("title") or ""),
        body=str(entry.get("body") or ""),
        labels=tuple(entry.get("labels") or ()),
    )


def main() -> None:
    """Read a JSON array of PRs on stdin; print the composed notes on stdout."""
    payload = json.load(sys.stdin) or []
    prs = [_pr_from_json(entry) for entry in payload]
    categories = load_categories(Path(__file__).resolve().parent.parent / ".github" / "release.yml")
    sys.stdout.write(compose_release_notes(prs, categories))


if __name__ == "__main__":
    main()
