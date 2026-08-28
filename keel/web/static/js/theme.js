// @ts-check

/**
 * The pre-paint theme restore (#597). **The one classic script this client loads.**
 *
 * ── WHAT IT DOES, IN FULL ─────────────────────────────────────────────────────────────────────
 * Reads the reader's stored light/dark choice and sets `data-theme` on `<html>` before the
 * page has painted anything. That is the whole file: nine lines of work, and the reason they
 * get a file of their own rather than a home in `main.js` is timing, not tidiness.
 *
 * ── WHY IT IS NOT AN ES MODULE, AND NOT INLINE ────────────────────────────────────────────────
 * A `type="module"` script is deferred by definition, so a module doing this would run after
 * the document had parsed -- and the page would paint in whatever `prefers-color-scheme` says
 * before flipping to the stored choice a moment later. A flash of the wrong theme on every
 * load, for every reader who has chosen one, is not a cost worth the consistency of "all the
 * client's files are modules".
 *
 * The site this app shares an identity with (keeltrading.com) solves the same problem with an
 * inline `<script>` in `<head>`. That form is not available here: the shell's CSP is
 * `default-src 'self'` with no `'unsafe-inline'`, so an inline block would be silently refused
 * by the browser -- `index.html`'s own head comment and the test that pins it both record this.
 * A same-origin classic script is the form that policy permits, and it is the standard
 * CSP-compatible spelling of the same pattern: parser-blocking, placed before the stylesheet
 * link, so `data-theme` is on the root before a single rule is applied to a single element.
 *
 * ── THE STATE MACHINE, AND WHERE THE REST OF IT LIVES ─────────────────────────────────────────
 * Two states, light and dark, and one stored key: `keel-theme` -- the same key the site's
 * toggle writes, because the two products describe one mechanism. No stored choice, or one
 * this reader cannot make (private mode, storage disabled): the attribute is left unset and
 * the stylesheet follows `prefers-color-scheme` -- the OS decides until the reader does. A
 * stored choice pins the theme on any OS, via the `:root[data-theme="dark"]` block `keel.css`
 * carries beside the media query.
 *
 * Writing the choice is NOT this file's job. The toggle in the header is hydrated by
 * `main.js`, which flips the same attribute and stores the new value under the same key;
 * `tests/web/test_client_assets.py::test_the_theme_choice_is_spelled_where_it_is_stored` pins
 * that the two files agree on the spelling, because a drift there is not an error anywhere --
 * it is a choice that silently stops surviving the reload it was stored for.
 */

(function () {
  try {
    var stored = window.localStorage.getItem("keel-theme");
    if (stored === "light" || stored === "dark") {
      document.documentElement.dataset.theme = stored;
    }
  } catch (cause) {
    // Storage refused (private mode, disabled by policy) is the OS-preference case wearing a
    // different hat, and needs no handling beyond leaving the attribute unset. `String(cause)`
    // and nothing else, for the same reason `api.js` mints its own short sentences: anything
    // richer would be invented.
    void cause;
  }
})();
