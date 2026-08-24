# This directory is empty, and that is the intended end state

Not a stage, not a placeholder waiting for its first entry. The design spec
(`docs/superpowers/specs/2026-08-23-web-ui-rewrite-design.md`, §"Dependencies") is explicit:
"In **js/external** there is nothing, and that is the intended end state rather than a stage."

`tests/web/test_client_assets.py::test_the_client_ships_no_third_party_code` fails the build if
any executable file appears beside this one.

## Why keel clears a bar the reference implementation did not

The spec's reference, [youperiod.app](https://github.com/getify/youperiod.app), admits three
client libraries — argon2, base64↔ArrayBuffer, IndexedDB normalisation — each because
implementing it incorrectly would undermine that app's own principles. The same bar applied to
keel admits none, and the two reasons are **structural rather than fortunate**:

- **keel's cryptography is not in the browser.** It lives in Python and in the OS keychain
  (`keel/web/security.py`'s session token, `keel/secrets/`). All three of the reference's
  dependencies are browser-crypto concerns keel does not have.
- **The client performs no arithmetic**, so no decimal library is required. `keel/web/payload.py`
  sends every monetary figure as a pre-formatted `display` string precisely so that
  `JSON.parse` — which yields IEEE-754 doubles — never touches a number keel cares about.

| need | answer |
|---|---|
| routing | History API (`js/main.js`) |
| dates | `Intl.DateTimeFormat` (`js/format.js`) |
| money formatting | none — the server did it |
| DOM | the DOM |
| decimal arithmetic | not required |
| cryptography | Python and the OS keychain |

## Why this file exists at all

Git does not track empty directories, so a directory that is meant to exist *and* be empty needs
one tracked file in it or it does not survive a clone — and a `js/external/` that only exists on
the author's machine cannot be the thing a test asserts about. A Markdown file is not JavaScript,
carries no third-party code, and says why it is here, which a zero-byte `.gitkeep` does not.

## Admitting one, if it ever comes to that

Any future exception clears the reference's bar: justified because getting it wrong ourselves
would undermine keel's principles. Convenience does not qualify. It would also have to survive
`Content-Security-Policy: default-src 'self'` (`keel/web/server.py::_STATIC_CSP`), which means
vendoring the file into this directory — there is no CDN path, by design.
