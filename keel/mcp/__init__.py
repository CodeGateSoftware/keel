"""The read-only MCP surface (#477): what a research assistant may ask, and nothing it could act on.

The package is pinned by `tests/mcp/test_readonly.py`, which was written before any of this
existed: a write-verb vocabulary over tool names and descriptions, an AST write-deny scan with
an empty allowlist, a no-gate-call-site scan, a registry-reference ban, a docs pin and a
click-free import check. Nothing here consults those walls at runtime -- they are tests, and
the safety property they state is "no write surface exists", not "a gate watches the writes".
"""
