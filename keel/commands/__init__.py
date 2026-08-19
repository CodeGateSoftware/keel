"""The shared service layer behind the keel CLI -- and, per the TUI PRD (issue #387), the TUI.

`keel/cli.py` is the composition root: it defines the root `cli` group, the broker-touching
commands (`fetch`, `agent`, `monitor`, `simulate`, `assets`) that share the `_build_broker`
seam, and the other top-level commands, then registers each group defined here via
`cli.add_command(...)`.

The broker-free command groups live here as standalone modules -- `db`, `trials`, `withdrawals`,
`autonomy`, `rules`, `subscription`, `status` -- so the large CLI file stays a thin wiring layer.
They draw
the shared seams they need (`_open_repo`, `_load_cfg`, the confirmation gate, the disclaimer
decorator) from `_common`, and the shared product-id derivation from `_products`; neither of those
helper modules imports `keel.cli`, so there is no cycle. The `assets` group's COMMANDS stay in
`keel/cli.py` on purpose: they use the `_build_broker` seam the top-level commands also patch, and
keeping them alongside those keeps the seam a single, coherent monkeypatch target -- while their
DECISION layer (`screen_product`, the admission gate every candidate source must route through)
lives here in `assets`.

Since issue #387 C1 (the TUI-operator-console PRD, objective O2 -- "one implementation, two
front-ends"), this package is also where the logic that used to live inside `keel/cli.py`
command bodies lives: `fetch` (the freshness/ensure/repair flow), `monitor` (the session-aware
poll loop), `simulate` (the report assembly), `assets` (the admission decision, holdings and
discovery), `confirm` (the order-preview gate), `trading` (the kill-switch / halt / flow / HWM
state mutations), `pnl` and `purification` (report builders and renderers). NOTHING in this
package imports `keel.cli` -- the composition root imports these modules, never the reverse --
which is what lets the TUI dispatch to exactly what the CLI calls. That boundary is pinned by
`tests/commands/test_service_isolation.py`; the CLI/service parity is pinned by
`tests/commands/test_service_parity.py`.
"""
