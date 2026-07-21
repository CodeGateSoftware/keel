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

_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})(\s+)(.*)$")

#: GitHub rejects a release body longer than this. The real first release composed 168k
#: characters of PR bodies, which `gh release create` would have refused outright.
GITHUB_RELEASE_BODY_LIMIT = 125_000

#: Room left for the workflow's fixed preamble (install/configure instructions).
_PREAMBLE_RESERVE = 6_000

#: Never trim an entry below this -- a stub that says nothing is worse than a link.
_MIN_BODY_CHARS = 300


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


def demote_headings(body: str, min_level: int = 4) -> str:
    """Shift every heading in a PR body down so it nests UNDER that PR's entry.

    Entries render as `## <category>` / `### <title>`, so a body containing its own `##`
    headings would render them as SIBLINGS of the category headings and flatten the whole
    document outline. Relative depth is preserved (the shallowest heading becomes
    `min_level`), headings never go past h6, and `#` inside a fenced code block is left
    alone -- a shell comment is not a heading.
    """
    lines = body.split("\n")

    levels: list[int] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            levels.append(len(match.group(1)))

    if not levels:
        return body
    shift = min_level - min(levels)
    if shift <= 0:
        return body

    out: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            level = min(6, len(match.group(1)) + shift)
            out.append("#" * level + match.group(2) + match.group(3))
        else:
            out.append(line)
    return "\n".join(out)


def truncate_body(body: str, limit: int | None, number: int) -> str:
    """Trim `body` to roughly `limit` characters, pointing at the PR for the rest.

    Cuts at a paragraph (then line) boundary where one is available, and re-closes a code
    fence left open by the cut -- an orphaned fence would swallow everything after it on the
    release page.
    """
    if limit is None or len(body) <= limit:
        return body

    cut = body.rfind("\n\n", 0, limit)
    if cut < limit // 2:
        cut = body.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit

    kept = body[:cut].rstrip()
    if kept.count("```") % 2:
        kept += "\n```"
    return f"{kept}\n\n*…truncated — full description in #{number}.*"


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


def compose_release_notes(
    prs: list[PullRequest],
    categories: list[Category],
    *,
    body_limit: int | None = None,
    total_limit: int | None = None,
) -> str:
    """Render the grouped, body-inlined change list as markdown.

    `total_limit` caps the whole document (GitHub refuses an over-long release body). The
    budget is shared evenly across entries, so **every PR stays listed** and only the bodies
    give ground -- dropping entries would silently hide shipped work.
    """
    grouped = categorize(prs, categories)
    entries = sum(len(bucket) for _, bucket in grouped)

    if total_limit is not None and entries:
        # Headings, blank lines and the truncation marker all cost characters too.
        overhead = sum(len(category.title) + 8 for category, _ in grouped) + sum(
            len(pr.title) + 70 for _, bucket in grouped for pr in bucket
        )
        budget = max(_MIN_BODY_CHARS, (total_limit - overhead) // entries)
        body_limit = budget if body_limit is None else min(body_limit, budget)

    sections: list[str] = []
    for category, bucket in grouped:
        lines = [f"## {category.title}", ""]
        for pr in bucket:
            body = clean_pr_body(pr.body)
            body = truncate_body(demote_headings(body), body_limit, pr.number) if body else (
                NO_DESCRIPTION
            )
            lines += [f"### {pr.title} (#{pr.number})", "", body, ""]
        sections.append("\n".join(lines).rstrip())

    out = "\n\n".join(sections)

    # Belt and braces: a pathological set of titles could still overrun the budget.
    if total_limit is not None and len(out) > total_limit:
        out = out[: max(0, total_limit - 60)].rstrip() + "\n\n*…release notes truncated.*"
    return out


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
    sys.stdout.write(
        compose_release_notes(
            prs, categories, total_limit=GITHUB_RELEASE_BODY_LIMIT - _PREAMBLE_RESERVE
        )
    )


if __name__ == "__main__":
    main()
