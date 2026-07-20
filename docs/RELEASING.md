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

## Release notes come from PRs

The change list in each release is **auto-generated from the PRs merged since the previous tag**
(`.github/release.yml`). The unit is the **pull request** — a clear PR title is all that is needed
for a useful entry. Issues and issue↔commit linking are **not** required and are not enforced:
good PRs are the source.

Labels are **optional** and only affect grouping. Without them the notes are a flat "What's
Changed" list of PR titles, which is fine. With them, PRs are grouped into sections:

| label | section |
|---|---|
| `feature`, `enhancement` | Features |
| `bug`, `fix` | Fixes |
| `compliance`, `rails` | Compliance & rails |
| `research`, `experiment` | Research & validation |
| `docs`, `ci`, `tooling` | Docs, CI & tooling |
| `breaking` | ⚠️ Breaking changes |
| `norelease` | *excluded from notes* |

Apply a label when you want the grouping; skip it when you don't. Either way the PR appears.
