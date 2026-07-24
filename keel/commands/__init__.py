"""Per-topic `click` command groups for the keel CLI.

`keel/cli.py` is the composition root: it defines the root `cli` group, the broker-touching
commands (`fetch`, `agent`, `monitor`, `simulate`, `assets`) that share the `_build_broker`
seam, and the other top-level commands, then registers each group defined here via
`cli.add_command(...)`.

The broker-free command groups live here as standalone modules -- `db`, `trials`, `withdrawals`,
`autonomy`, `rules`, `subscription`, `status` -- so the large CLI file stays a thin wiring layer.
They draw
the shared seams they need (`_open_repo`, `_load_cfg`, the confirmation gate, the disclaimer
decorator) from `_common`, and the shared product-id derivation from `_products`; neither of those
helper modules imports `keel.cli`, so there is no cycle. The `assets` group stays in `keel/cli.py`
on purpose: it uses the `_build_broker` seam the top-level commands also patch, and keeping it
alongside them keeps that seam a single, coherent monkeypatch target.
"""
