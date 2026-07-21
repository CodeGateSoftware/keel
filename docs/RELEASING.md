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
   `[release]` → verifies the live config asset is `mode: confirm` → tags `v<version>` → composes
   release notes → publishes the GitHub Release with all wheels **and `config.yaml`** attached.

## Release assets

| asset | what it is |
|---|---|
| `keel_trader-<version>-py3-none-any.whl` | the CLI. Install **by path**, never by bare name. |
| `keel_core-*`, `keel_broker_*` wheels | workspace members `keel` depends on; download them all. |
| `config.yaml` | the **production** config: real allowlist/caps in `auto_trade.mode: confirm`. |

`config.yaml` is `keel/templates/config.live.yaml`, committed and reviewed like any other code.
It ships in **confirm** mode — keel previews every order and waits for your approval — so a fresh
download is ready for live use but can never trade unattended. The release **fails loudly** if
that file is ever anything other than `mode: confirm`.

Both templates also ship inside the wheel: `keel init-config` writes the dev one (`mode: paper`,
places nothing) and `keel init-config --live` writes the exact release asset.

## Bootstrapping a deployment

Seeding and migrating are deliberately **separate** operations:

```
keel init      # FRESH deployment: write config.yaml + seed the strategy (rules) library
keel migrate   # EXISTING database: apply outstanding schema migrations. Never seeds.
```

- **`keel init`** = `init-config` + `rules seed`. Rules are seeded as `candidate`, so nothing
  trades until you deliberately `keel rules promote` them.
- **`keel migrate`** is idempotent and schema-only — safe to re-run, and safe against a live
  database. It never re-seeds, because that would resurrect rules deliberately deleted or refuted.
  `--db <path>` targets a database directly.

The **Migrate database** workflow (Actions → Migrate database → Run workflow) is manual-only. Give
it a `db_path` to migrate that database; leave it empty and it verifies the migration chain
instead (a fresh DB and a downgraded DB both reach `SCHEMA_VERSION`). CI has no database to reach
while `keel.db` is local and git-ignored — once the app is server-hosted, that deployment's
database becomes the `db_path` target and the release can call this job.

## Release notes come from PRs

The change list in each release is **auto-generated from the PRs merged since the previous tag**.
The unit is the **pull request**. Issues and issue↔commit linking are **not** required and are not
enforced: good PRs are the source.

Each entry **inlines the PR's description**, not a link to it — a reader should never have to
click through to learn what shipped. So the PR body *is* the release note: write it for someone
reading the release page. `scripts/release_notes.py` composes them (unit-tested in
`tests/test_release_notes.py`), stripping the Claude Code footer, HTML comments and
`Co-Authored-By:` trailers. A PR with an empty body renders as `_(no description)_` — visible,
so it gets fixed.

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
