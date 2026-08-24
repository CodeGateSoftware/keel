"""The local web UI (#435, D2) -- a read-only browser view over `keel/commands/*`.

Why a browser and not a native window: section 3 of
`docs/superpowers/specs/2026-08-20-desktop-distribution-prd.md`. In one line, it retires
three separate blockers with one artifact: Windows has no `curses`, a
macOS app launched from Finder has no controlling terminal at all, and the GUI human gate (#436)
needs somewhere to live that is architecturally distinct from `_is_interactive`.

**This package's write surface is a closed set, and that is structural rather than a matter of
discipline.**

The sentence that used to be here -- "the request handler implements `do_GET` and `do_HEAD` and
nothing else" -- stopped being true at #437 and is corrected rather than deleted, because
`server.do_POST` still points a reader at this file for the rule it must not break. The guarantee
now is stronger than the one it replaced, and it is the one anyone actually cares about:

* `do_POST` exists, and it routes ONLY through `keel.commands.setup.ACTIONS` -- idempotent,
  non-destructive steps -- so a first-run user on a machine with no terminal can create a
  deployment. "No POST at all" was a clean property that was also satisfied by a server which
  could not set anything up.
* **Not one of the eleven capability-increasing actions in `keel/capabilities.py` is reachable
  from this package**, asserted by a test that scans this source rather than by inspection. The
  server cannot arm, release or spend. Attesting, promoting, releasing a halt and arming autonomy
  remain CLI-only, behind the TTY gate; D3 (#436) is where a browser gate for those would go.
* The JSON API (#534, `keel/web/api.py`) is **reads only**. Every route in its table answers a GET
  and 404s a POST, and it did not widen `API_PREFIX`'s existing `X-Keel-Client` gate by one byte.
"""
