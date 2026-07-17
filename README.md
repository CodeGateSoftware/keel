# keel

An offline-first, halal (long-only, no-leverage) auto-trading agent for Coinbase. See
`docs/superpowers/specs/2026-07-15-keel-autotrade-design.md` for the full design and
`docs/superpowers/plans/2026-07-15-keel-phase1-offline-foundation.md` for the Phase 1 build plan.

## Development

```bash
uv sync                 # install deps (Python 3.12)
uv run pytest           # run tests
uv run ruff check       # lint
```

Copy `.env.example` to `.env` and fill in your Coinbase Developer Platform (CDP) API key/secret
for any live (network) commands. `.env` is git-ignored and never committed. Offline commands
(config loading, analysis, backtests on imported CSVs) work without it.

Runtime settings (allowlist, target weights, risk caps, market data granularities, etc.) live in
`config.yaml` at the repo root — see `keel/config.py` for the schema and validation rules.
