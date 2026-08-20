"""The local web UI (#435, D2) -- a read-only browser view over `keel/commands/*`.

Why a browser and not a native window: section 3 of
`docs/superpowers/specs/2026-08-20-desktop-distribution-prd.md`. In one line, it retires
three separate blockers with one artifact: Windows has no `curses`, a
macOS app launched from Finder has no controlling terminal at all, and the GUI human gate (#436)
needs somewhere to live that is architecturally distinct from `_is_interactive`.

**This package has no write surface, and that is structural rather than a matter of discipline.**
The request handler implements `do_GET` and `do_HEAD` and nothing else, so every other method is
refused by `BaseHTTPRequestHandler` before any keel code runs. Write actions land in D3, behind a
gate of their own; until then there is no code path here that could grow one by accident.
"""
