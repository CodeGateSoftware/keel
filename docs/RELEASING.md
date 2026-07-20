# Releasing keel

The release path is deliberately manual and human-gated — nothing that can move money ships on a
merge. See `.github/workflows/release.yml`.

## Build identity

Every build reports what it is:

```
$ keel --version
keel 0.1.0+c11baba726af [release]
```

`0.1.0+c11baba726af` is the **canonical build identity**: the semver version bound to the exact
commit hash (`+` is the semver / PEP 440 build-metadata separator). The version alone is ambiguous
— many commits share a version between bumps — so the hash is what actually pins "which code is
this". A build reporting `(DIRTY)` or `[checkout]` corresponds to no commit and **must not be run
against live funds**; `keel --version` warns loudly when so.

## Cutting a release

1. **Bump the version in a reviewed PR.** Edit `version` in `pyproject.toml`. The release workflow
   refuses to change the version itself — that decision belongs in a PR a human reviewed, so CI
   never writes to `main`. (The **first** release needs no bump: `pyproject.toml` already says
   `0.1.0`.)
2. **Merge it**, then **Actions → Release → Run workflow**, entering the same version.
3. The workflow: validates the input is semver and matches `pyproject.toml` and no such tag exists
   → runs tests + ruff → stamps the commit into `keel/_build_info.py` → `uv build --all-packages`
   → installs the wheel into a clean venv **by path** and asserts it self-identifies as a clean
   `[release]` → tags `v<version>` → composes release notes → publishes the GitHub Release with all
   wheels attached.

## Release notes come from PRs — so label every PR

The change list in each release is **auto-generated from the PRs merged since the previous tag**,
grouped by label (`.github/release.yml`). This only produces useful notes if PRs are labelled:

| label | section |
|---|---|
| `feature`, `enhancement` | Features |
| `bug`, `fix` | Fixes |
| `compliance`, `rails` | Compliance & rails |
| `research`, `experiment` | Research & validation |
| `docs`, `ci`, `tooling` | Docs, CI & tooling |
| `breaking` | ⚠️ Breaking changes |
| `norelease` | *excluded from notes* |

An unlabelled PR lands in "Other changes" — not wrong, just less useful. **Label PRs before merge.**

## Issues, and linking commits to them (adopt from the next cycle)

Until now this project worked PR-per-change with no issue tracker. Going forward, work should be
tracked as **issues**, and PRs/commits should **reference the issue they close**:

- Open an issue for each unit of planned work (`gh issue create`).
- In the PR body or a commit, write `Closes #N` (or `Refs #N` for partial progress). GitHub then
  links the commit and closes the issue on merge, and the reference shows up in the release notes.
- This gives three things the current flow lacks: a backlog that outlives a chat session, a
  durable "why" behind each change, and richer auto-generated release notes.

⚠️ This is a process commitment, not code. It only works if it is actually followed from the next
change onward — the mechanics (labels, `release.yml`, `Closes #N` linking) are in place; the
discipline is the part that has to be adopted.
