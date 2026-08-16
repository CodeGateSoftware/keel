<!--
  The bar is stated in CONTRIBUTING.md so it can be met, not guessed:
  the documentation standard (say why, name what was measured, record
  what would change the decision), tests first, and Conventional Commits.
-->

## What & why

<!-- What changes, and the constraint or measured fact that makes it necessary.
     A PR whose "why" is only "what" will be asked for its reasoning. -->

## Tests-first evidence

<!-- The failing test(s) this PR makes pass, and evidence they failed for the RIGHT
     reason — the assertion meant to assert, not an import error. Paste the red run. -->

- [ ] Tests written first, seen failing for the right reason

## Gates (all must pass)

- [ ] `uv run ruff check` clean
- [ ] `uv run mypy` clean
- [ ] `uv run pytest -q` green

## Scope check

- [ ] **This PR touches a rail or a default classification** — checked means it DOES;
      leave checked only if true, and if so: cite the source and open the discussion
      BEFORE review (CONTRIBUTING.md, "Governance: rulings vs. machinery").
- [ ] New dependency added (needs discussion first)

<!-- Delete sections that do not apply to your change rather than leaving them empty. -->
