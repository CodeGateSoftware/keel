// @ts-check

/**
 * Builds DOM from API payloads. **Contains no arithmetic and no money formatting.**
 *
 * ── HOW THE ABSENCE OF ARITHMETIC IS PROVED, RATHER THAN PROMISED ────────────────────────────
 *
 * The spec asks for this file specifically so "a reviewer can confirm the absence by reading one
 * file". Reading is the point, but reading is not a gate, so
 * `tests/web/test_client_assets.py::test_render_contains_no_arithmetic` is: it strips comments
 * and string literals with a small lexer and asserts the remaining code contains no `+`, `-`,
 * `*`, `/`, `%`, `++`, `--`, and none of `Number`, `parseInt`, `parseFloat`, `Math`, `BigInt`,
 * `toFixed`, `toPrecision` or `NumberFormat`.
 *
 * That scan is NOT sufficient on its own, which was found by mutating this file rather than by
 * reasoning about it: `return v.value < 0 ? "bad" : "good"` contains no arithmetic operator and
 * no numeric identifier, and it is precisely the forbidden thing -- a judgement re-derived in the
 * client from a sign. `test_render_never_judges_a_value_itself` closes it with two more rules:
 * **this file never reads `Field.value`**, and it contains **no relational comparison** (`<`,
 * `>`, arrow functions excepted). Between them, `display` is all this file can place and `state`
 * is all it can style by.
 *
 * Two rules below exist ONLY to keep that lexer small enough to be obviously correct, and they
 * are written down here so nobody removes them as pointless style:
 *
 *   **No regular-expression literals in this file.** Telling `/` as division from `/` as the
 *   start of a regex is the one genuinely hard problem in lexing JavaScript -- it needs the
 *   previous significant token. With no regex literals, every `/` outside a comment or a string
 *   is division, and the scanner is a character loop with four states instead of a parser.
 *
 *   **No template literals in this file.** `${...}` re-enters code inside a string, so a scanner
 *   that strips template literals whole would strip code with it, and one that tracks the nesting
 *   needs a depth counter. Neither is hard; both are more machinery than a rule.
 *
 * ── THE SECOND PROPERTY THAT RULE BUYS: THIS FILE CANNOT PRODUCE MARKUP ──────────────────────
 *
 * With template literals and `+` both gone there is no way to build an HTML string here, and
 * indeed there is no `innerHTML` anywhere in this client (the same test greps for it, along with
 * `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` and `new Function`). Everything is
 * `createElement` and `textContent`.
 *
 * That is a security property, and it is the one `render.py` needs a whole function for.
 * `render.py::esc` exists because "rule names, product ids and adapter error strings all
 * originate outside this process; none of them is trusted markup" -- and every one of those
 * strings passes through this file too. `textContent` cannot interpret markup, so there is no
 * escaping to get right, no `esc()` to forget at one call site out of ninety, and no injection
 * sink to audit. The rule adopted for the arithmetic scanner turns out to close the larger hole.
 *
 * ── WHAT "PLACES, NEVER DERIVES" MEANS CONCRETELY ────────────────────────────────────────────
 *
 * Every displayable string arrives as `{value, display, state}` and this file writes `display`
 * into a text node. It never reads `value` to build a display string, and it never reads a sign
 * to decide a colour -- `state` exists for that, and #532's `▲`/`▼` glyphs are already inside
 * `display` where `payload.money` put them. If a number is wanted that the API does not send,
 * the fix is in `keel/web/payload.py`. There is nowhere in this file to put it.
 */

import { termLink } from "./docs.js";
import { instant } from "./format.js";

/**
 * One value, ready to place.
 * @typedef {import("./api.js").Field} Field
 */

/**
 * @typedef {import("./api.js").Reading} Reading
 */

/**
 * The API's five `state` words mapped to `keel/web/render.py`'s four CSS classes.
 *
 * A TABLE, not a computation, and #532's classes rather than new ones -- the brief for this
 * issue is explicit that the palette's convention is reused and not doubled. Two of the five
 * need explaining:
 *
 *   `neutral` maps to NO class. It is the default judgement on most fields (`payload.NEUTRAL`),
 *   and a class that means "style this like body text" is a class that exists to be deleted.
 *
 *   `unknown` maps to `muted`, not to nothing. The distinction is load-bearing and `payload.py`
 *   spells it out: "`unknown` is NOT a synonym for `neutral`" -- `_rail11_status` returns
 *   `unknown` when a drawdown scalar was never written, "explicitly because reporting an
 *   unwritten value as a confident 'ok' would be a lie". Rendering it in body text would put
 *   that lie back visually after the payload took care to avoid it in words.
 *
 * An unrecognised word yields `undefined` and therefore no class, which is the right behaviour
 * for a server one version ahead of this page: the `display` string still renders.
 *
 * @type {Record<string, string>}
 */
const STATE_CLASS = {
  good: "good",
  warn: "warn",
  bad: "bad",
  neutral: "",
  unknown: "muted",
};

/**
 * An element, optionally with a class and text.
 *
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text]
 * @returns {HTMLElement}
 */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * A `Field`, as a `<span>` carrying its judgement.
 *
 * The one place a payload value becomes a visible node, and the whole of the placement rule in
 * three lines: `display` goes in, `state` picks a class, `value` is never read.
 *
 * @param {any} maybeField  a `Field` from a payload, or anything else.
 * @returns {HTMLElement}
 */
export function field(maybeField) {
  if (!maybeField || typeof maybeField.display !== "string") {
    // A field the payload did not carry. `payload.ABSENT` is `—` and means "never recorded";
    // this means "the server did not send this key at all", which is a different fact and gets
    // a different mark. Neither is ever rendered as `0`: `payload.py`'s own note on `ABSENT`
    // records what collapsing "not recorded" into "recorded as zero" cost last time (#198, the
    // always-passing fee rail).
    return el("span", "muted", "?");
  }
  return el("span", STATE_CLASS[maybeField.state], maybeField.display);
}

/**
 * A bare identifier from a payload -- `product_id`, `kind`, `venue`. Not a `Field`: these cross
 * the wire as plain strings because they are names, not judged values.
 *
 * `String(value)` was the first spelling and it is wrong in the one case that matters: `String`
 * of a key the server did not send is the literal text `"undefined"`, and of a `null` it is
 * `"null"` -- two words that would appear in a table cell on a trading dashboard looking exactly
 * like data. `""` is the honest answer to "the server sent no name here", and it is what
 * `render.py` renders for the same absence.
 *
 * @param {any} value
 * @returns {string}
 */
function plain(value) {
  return typeof value === "string" ? value : "";
}

/**
 * A label-over-value pair, matching `render.py::kv`.
 *
 * @param {string} label
 * @param {any} value  a `Field`, a `Node` already built, or a bare string.
 * @returns {HTMLElement}
 */
function kv(label, value) {
  const wrap = el("div", "kv");
  // #539: a label that names a documented term becomes an outbound link to its definition,
  // here rather than at each call site. `docs.TERMS` is the whole answer to "which words on
  // this screen are defined somewhere", and a label it does not know stays plain text.
  const linked = termLink(label);
  const key = el("span", "k");
  if (linked) key.append(linked);
  else key.textContent = label;
  wrap.append(key);
  const holder = el("span", "v");
  if (value instanceof Node) holder.append(value);
  else if (typeof value === "string") holder.textContent = value;
  else holder.append(field(value));
  wrap.append(holder);
  return wrap;
}

/**
 * A card wrapping one auto-fit grid of `kv` pairs.
 *
 * @param {HTMLElement[]} pairs
 * @returns {HTMLElement}
 */
function gridCard(pairs) {
  const card = el("div", "card");
  const grid = el("div", "grid");
  grid.append(...pairs);
  card.append(grid);
  return card;
}

/**
 * One column of a table.
 *
 * `key` is the name `?sort=` uses for this column, and it is OPTIONAL: a column with no key is a
 * column the server does not offer to order by, which is most of them. `api.py`'s `sortable`
 * tuples are hand-written for that reason -- "the API's answer to `?sort=x` should not depend on
 * whether any rows exist" -- and a `key` here that is absent from the response's `sort.columns`
 * simply renders as an ordinary header rather than as a button that would 400.
 *
 * @typedef {{label: string, numeric: boolean, key?: string}} Column
 */

/**
 * The sorting surface of one table: what the server reported, and what to do about a click.
 *
 * `sort` is the envelope's own `sort` echo, unread and unmodified. `_sort_echo`'s docstring says
 * why it is echoed even when nothing was sorted: "#537 renders sortable headers by reading the
 * response rather than by holding a second copy of the list" -- so a column added to `api.py`
 * reaches the interface with no edit here, and one removed there cannot leave a header behind
 * that sorts by nothing.
 *
 * @typedef {{sort: any, onSort: (column: string, direction: string) => void}} Sorting
 */

/**
 * The two directions, and the marks that show them.
 *
 * **Not `▲`/`▼`.** Those two glyphs are #532's gain/loss signal -- `payload.money` puts them
 * inside `display` for a figure whose SIGN matters -- and reusing them for a sort direction would
 * put a "this is a loss" mark on a column header. `↑`/`↓` are unambiguous and are used nowhere
 * else on the page.
 *
 * `aria-sort` carries the same fact for a reader, so the mark is reinforcement rather than the
 * only channel -- the rule #532 established for colour, applied to a glyph.
 *
 * @type {Record<string, {next: string, mark: string, aria: string}>}
 */
const SORT_DIRECTIONS = {
  asc: { next: "desc", mark: " ↑", aria: "ascending" },
  desc: { next: "asc", mark: " ↓", aria: "descending" },
};

/**
 * A header cell that sorts, or a plain one.
 *
 * The clickable thing is a `<button>` INSIDE the `<th>`, never a click handler on the `<th>`
 * itself. A `th` with an `onclick` is not focusable, not activated by Enter or Space, and
 * announces itself as a column header rather than as something that can be pressed; a button is
 * all three for free, and this page's `:focus-visible` rule already gives it a ring.
 *
 * @param {string} id  the table's id, so a sort button's focus key is unique on the page.
 * @param {Column} column
 * @param {Sorting|undefined} sorting
 * @returns {HTMLElement}
 */
function headerCell(id, column, sorting) {
  const cell = el("th", column.numeric ? "num" : undefined);
  // `scope` is what tells a screen reader that this cell labels its column rather than being
  // data in it. Cheap, and the difference between a table that can be navigated cell by cell
  // and one that reads as an undifferentiated run of numbers.
  cell.setAttribute("scope", "col");

  const key = column.key;
  const sortable = Boolean(
    key && sorting && sorting.sort && (sorting.sort.columns || []).includes(key),
  );
  if (!key || !sorting || !sortable) {
    cell.textContent = column.label;
    return cell;
  }

  const active = sorting.sort.column === key;
  const shown = SORT_DIRECTIONS[sorting.sort.direction] || SORT_DIRECTIONS.asc;
  // An inactive column offers ascending first. Ascending rather than "whatever was last used"
  // because a header whose first press depends on hidden state is a header nobody can predict.
  const next = active ? shown.next : "asc";
  cell.setAttribute("aria-sort", active ? shown.aria : "none");

  const trigger = el("button", "sortkey", column.label);
  trigger.setAttribute("type", "button");
  // Pressing this replaces the whole view, which destroys this button. `data-focus` is how
  // `main.js` finds its replacement and puts focus back on it -- otherwise sorting a column by
  // keyboard would sort the table and drop the reader at the top of the document. Scoped by the
  // table's id as well as the column, because two tables in one view can offer the same column
  // name (`product_id` is a column of both `/api/holdings` and `/api/orders` on Holdings).
  trigger.setAttribute("data-focus", [id, key].join(":"));
  if (active) trigger.append(el("span", "mark", shown.mark));
  trigger.addEventListener("click", () => sorting.onSort(key, next));
  cell.append(trigger);
  return cell;
}

/**
 * A `<details>` disclosure: a summary line that is always shown, and a body that is not.
 *
 * Native `<details>`, never a scripted show/hide. It is keyboard-operable, announced as a
 * disclosure, searchable by the browser's own find-in-page in current engines, and it survives
 * this client having no state to remember it by. It is also how the TUI's expand-a-cycle key
 * lands in a medium with no keybindings to teach.
 *
 * @param {string} summaryText
 * @param {HTMLElement[]} body
 * @param {string} [className]
 * @returns {HTMLElement}
 */
function disclosure(summaryText, body, className) {
  const node = el("details", className);
  node.append(el("summary", undefined, summaryText));
  node.append(...body);
  return node;
}

/**
 * A list of plain strings, or nothing at all for an empty one.
 *
 * `null` rather than an empty `<ul>`: a list element with no items is a thing a screen reader
 * announces ("list, 0 items") and a sighted reader sees as blank space, and neither is worth the
 * fact that a payload sent `[]`.
 *
 * @param {any} values
 * @param {string} [className]
 * @returns {HTMLElement|null}
 */
function stringList(values, className) {
  if (!Array.isArray(values) || values.length === 0) return null;
  const list = el("ul", className);
  for (const value of values) list.append(el("li", undefined, plain(value)));
  return list;
}

/**
 * A paragraph of muted prose. The `.note` class `render.py` uses, for the same kind of sentence.
 *
 * @param {string} text
 * @returns {HTMLElement}
 */
function note(text) {
  return el("p", "note", text);
}

/**
 * A short word in a rounded outline -- `render.py`'s `.pill`. Used for a KIND or a SURFACE: a
 * classification the row belongs to, never a judgement, which is what `field` is for.
 *
 * @param {string} text
 * @param {string} [tone]  an extra class, for the one case (`payload`'s step kinds) where the
 *                         classification does carry a state.
 * @returns {HTMLElement}
 */
function pill(text, tone) {
  return el("span", tone ? "pill ".concat(tone) : "pill", text);
}

/**
 * A `Field`'s state as a class, for a container rather than for the value itself.
 *
 * @param {any} maybeField
 * @returns {string}
 */
function toneOf(maybeField) {
  return (maybeField && STATE_CLASS[maybeField.state]) || "";
}

/**
 * A headed, keyboard-scrollable table, or an empty-state paragraph when there are no rows.
 *
 * ── THE THREE ACCESSIBILITY DECISIONS IN THIS FUNCTION ───────────────────────────────────────
 *
 *   1. **The `<h2>` names the table.** `aria-labelledby` on both the scroll region and the
 *      `<table>` points at the heading that is already on screen, rather than a `<caption>` that
 *      would either duplicate it visually or be hidden and drift out of sync with it. One
 *      string, one place, two things named by it.
 *
 *   2. **The scroll container is a focusable `role="region"`.** A container with `overflow-x:
 *      auto` is reachable by mouse and by touch and, without `tabindex`, by nothing else -- a
 *      keyboard user cannot scroll to a column they cannot reach. `tabindex="0"` fixes that, and
 *      it is `role="region"` with a name rather than a bare `tabindex` because an unnamed tab
 *      stop announces itself as "group" and tells a reader nothing about what they just landed
 *      in.
 *
 *   3. **The table scrolls; the page does not.** See `css/keel.css`'s layout note. A row of
 *      `white-space: nowrap` figures has a real minimum width, and the alternative to scrolling
 *      it is scrolling the document -- which takes the header and the nav off screen.
 *
 * @param {string} id       the id of the `<h2>` that names this table.
 * @param {Column[]} columns
 * @param {Array<Array<any>>} rows  cells: a `Field`, a `Node`, or a plain string.
 * @param {string} empty    what to say when `rows` is empty.
 * @param {Sorting} [sorting]  the response's sort echo and what to do about a header press.
 *                             Omitted for a table whose order is intrinsic -- see `api.py`'s note
 *                             on why `rule_counts`, `data_freshness` and `live_rules` are not
 *                             sortable ("three intrinsic orders that a display sort would destroy
 *                             rather than improve").
 * @returns {HTMLElement}
 */
function table(id, columns, rows, empty, sorting) {
  if (rows.length === 0) return el("p", "empty", empty);

  const head = el("tr");
  for (const column of columns) head.append(headerCell(id, column, sorting));

  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    // `entries()` walks indices without an operator. `index++` would say the same thing in one
    // character, and the scanner described at the top of this file bans `++` along with every
    // other arithmetic operator -- a rule with "except this one, in a loop, where it is
    // obviously fine" carved out of it is a rule that can no longer be checked mechanically.
    for (const [index, value] of row.entries()) {
      const column = columns[index];
      const cell = el("td", column && column.numeric ? "num" : undefined);
      if (value instanceof Node) cell.append(value);
      else if (typeof value === "string") cell.textContent = value;
      else cell.append(field(value));
      tr.append(cell);
    }
    body.append(tr);
  }

  const element = el("table");
  element.setAttribute("aria-labelledby", id);
  const thead = el("thead");
  thead.append(head);
  element.append(thead, body);

  const wrap = el("div", "tablewrap");
  wrap.setAttribute("role", "region");
  wrap.setAttribute("aria-labelledby", id);
  wrap.setAttribute("tabindex", "0");
  wrap.append(element);
  return wrap;
}

/**
 * A heading that also serves as a table's accessible name.
 *
 * @param {string} id
 * @param {string} text
 * @returns {HTMLElement}
 */
function heading(id, text) {
  const h = el("h2", undefined, text);
  h.id = id;
  return h;
}

/**
 * Fill the engine banner -- the page's one `aria-live` region (see `index.html` for why it is
 * the only one).
 *
 * `replaceChildren` rather than clearing and appending: it is one mutation, so a screen reader
 * announces the region once rather than announcing an empty region and then a full one.
 *
 * @param {HTMLElement} node
 * @param {Reading} reading
 */
export function engineBanner(node, reading) {
  const what = el("span", "what");
  what.append(field(reading.engine));

  // "read at", not "updated": the figures may be hours old (the agent runs daily); what this
  // timestamp actually asserts is when this page last asked. Saying "updated" would claim
  // freshness the value does not carry -- and the freshness of the DATA is a separate,
  // server-judged fact already on screen in the `age` column.
  const when = el("span", "when");
  const stamp = instant(reading.as_of);
  // `append` takes strings and makes text nodes of them, so two pieces of text need no
  // concatenation to become one node's content.
  if (stamp) when.append("read at ", stamp);
  else when.append("no timestamp on that answer");

  node.className = "engine";
  const tone = STATE_CLASS[reading.engine.state];
  if (tone) node.classList.add(tone);
  node.replaceChildren(what, when);
}

/**
 * The "keel isn't running" view. Rendered from `engine`, never from an empty payload.
 *
 * The API answers a stopped engine with **HTTP 200 and `data: null`**, on purpose, and this is
 * the view that makes that decision pay off: a first-run user is not in an outage, they have
 * simply not set anything up, and the page they need is the checklist. `payload.envelope` is
 * explicit that `data` is `null` and never `{}` because "an empty object is a payload in which
 * every figure is missing, and a view handed one renders zeros" -- so this view is reached by
 * checking `data === null`, and there is no code path in which a zero could be rendered instead.
 *
 * @param {Reading} reading
 * @param {string} setupHref  where the first-run checklist lives. Passed in rather than spelled
 *                            here: `/static/` is written in exactly three files
 *                            (`staticfiles.STATIC_PREFIX`, `index.html`, `main.js`'s `BASE`) and
 *                            `test_the_mount_prefix_is_spelled_the_same_everywhere` pins that the
 *                            three agree, so #540's move to `/` stays a three-line edit. A fourth
 *                            spelling in this file would be a fourth place to miss.
 * @returns {DocumentFragment}
 */
export function stoppedView(reading, setupHref) {
  const fragment = document.createDocumentFragment();
  const card = el("div", "card stopped");
  card.append(el("h1", undefined, "keel isn't running"));
  card.append(el("p", undefined, reading.engine.display));

  if (reading.error) {
    card.append(el("p", "detail", reading.error.detail || reading.error.title));
  } else {
    // No error means the server answered normally and simply has no deployment to read -- the
    // first-run path. The link goes to the client's Audit & Setup view, whose PRIMARY reading is
    // `/api/setup` (console PR4): it is declared `needs_database=False` precisely so the checklist answers on a
    // machine with nothing on it, which is the only machine that ever reaches this branch.
    const p = el("p");
    const link = el("a", undefined, "Set keel up on this machine");
    link.setAttribute("href", setupHref);
    p.append(link);
    card.append(p);
  }

  fragment.append(card);
  return fragment;
}

/**
 * The `keel open` invocation that recovers THIS console's address.
 *
 * It returns a NODE and joins nothing, because this file may use neither template literals nor
 * `+` -- `test_render_contains_no_arithmetic` bans the operator outright rather than try to tell
 * string concatenation from money, and that bluntness is the point of the gate. `append` takes
 * several strings, so the port is placed beside the command instead of being joined to it.
 *
 * A port the browser cannot report -- the default 80/443, where `location.port` is the empty
 * string -- yields the bare command, which is still correct: `keel open` has its own default.
 *
 * @param {string} port  `window.location.port`, empty when the URL carries no explicit port.
 * @returns {HTMLElement}
 */
function openCommandNode(port) {
  const code = el("code");
  if (port) code.append("keel open --port ", port);
  else code.append("keel open");
  return code;
}

/**
 * The "keel refused this browser" view (#634).
 *
 * ── WHY THIS IS NOT `stoppedView` ───────────────────────────────────────────────────────────
 * A 403 arrives here as `data: null` with an `error`, which is the same shape a stopped engine
 * arrives in, so it used to render `stoppedView` -- headed **"keel isn't running"**. That is
 * false in the one case it is shown: keel is running, answering on this port, and has declined
 * this browser. Telling an operator the server is down when it is up sends them to restart the
 * thing that is working, which mints a new token and makes the situation worse.
 *
 * ── WHY IT OFFERS A FIELD RATHER THAN A SENTENCE ────────────────────────────────────────────
 * `keel serve` mints a token per run and never writes it to disk, so after a restart there is
 * nothing on this device that can authorise it -- and the installed console is a window with no
 * address bar, so "open the address keel printed" is an instruction it cannot follow. The one
 * action available from inside the app is to accept the new run's token by hand. That is what
 * this field is: not a login, and not a credential store -- a way to hand over a value the
 * operator can already see, from a surface that has nowhere else to put it.
 *
 * `onReconnect` rather than a navigation here: this module places nodes and never decides where
 * the browser goes, the same division `sortableHeader` and the scope switch already keep. It is
 * also the division that keeps the origin check in ONE place -- `main.js`'s `reconnect`, which
 * builds its target from `window.location` and never from the pasted text.
 *
 * `FormData` rather than reading the input's `.value`: `test_render_never_judges_a_value_itself`
 * bans the `.value` attribute from this file outright, and the ban is worth more than the two
 * characters it costs here.
 *
 * @param {Reading} reading
 * @param {(address: any) => string} onReconnect  handed the pasted text; returns what to show.
 * @returns {DocumentFragment}
 */
export function refusedView(reading, onReconnect) {
  const fragment = document.createDocumentFragment();
  const card = el("div", "card stopped reconnect");
  card.append(el("h1", undefined, "keel is running, and did not admit this browser"));
  if (reading.error) {
    card.append(el("p", "detail", reading.error.detail || reading.error.title));
  }

  // ── HOW TO GET THE VALUE THIS FIELD WANTS ──────────────────────────────────────────────
  // Added because the view told an operator what to paste and never where to get it. The
  // refusal's own prose says the token "is never written to disk", which since #756 is only
  // true of a server attached to a TERMINAL: run detached -- launchd, a pipe, a container --
  // `runtime.py` records the address in a 0600 file precisely so `keel open` can hand it back.
  // A reader who believes the sentence concludes the token is unrecoverable and restarts the
  // server, which mints a NEW one and is the single worst move available. The command is the
  // remedy, and it was named in `server.py`, in the launchd plist and in `runtime.py`'s own
  // docstring -- everywhere except the screen the locked-out operator is looking at.
  //
  // The PORT is taken from `window.location`, the same source and the same reason as
  // `timelineExportUrl` and the deployment card: it is a fact about where this page is, not a
  // claim from a payload that just refused us. Nothing else about the deployment is named --
  // the path in particular -- because the port is the one thing the reader demonstrably
  // already knows (they reached this page on it), so naming it discloses nothing to a request
  // that has NOT been admitted. That matters more once #648 lets this console answer beyond
  // loopback.
  const how = el("div", "detail how-to-get-in");
  how.append(el("p", undefined, "To get this run's address, from your keel deployment directory:"));
  how.append(openCommandNode(window.location.port));
  const howNote = el("p");
  howNote.append(
    "It opens the console, and prints the address so you can paste it below. ",
    "Attached to a terminal keel prints the address and keeps nothing; run as a service it ",
    "records the address for that command and deletes it on shutdown.",
  );
  how.append(howNote);
  card.append(how);

  const form = el("form");
  const label = el("label", undefined, "Paste the address from that command, or just its token");
  label.setAttribute("for", "reconnect-address");
  const input = el("input");
  input.setAttribute("id", "reconnect-address");
  input.setAttribute("name", "address");
  input.setAttribute("type", "text");
  input.setAttribute("autocomplete", "off");
  input.setAttribute("spellcheck", "false");
  const submit = el("button", undefined, "Reconnect");
  submit.setAttribute("type", "submit");

  // `role="status"`, so a refusal typed into this field is announced rather than only drawn --
  // the operator who most needs this view is the one who cannot see a terminal.
  const note = el("p", "detail");
  note.setAttribute("role", "status");

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    note.textContent = onReconnect(new FormData(form).get("address"));
  });

  form.append(label, input, submit);
  card.append(form, note);
  fragment.append(card);
  return fragment;
}

/**
 * Pulse, the home view: `/api/pulse`'s payload -- what keel did today, what stopped it, what it
 * will do next, and the one control that can stop it (console PR1).
 *
 * It replaced Status, Activity and Gates, and keeps their information in one scroll rather than
 * three pages: a glance page with tabs hides the thing you came to glance at, so this is not
 * `sectioned`. The order is the order of the questions an operator brings -- can I stop it, is it
 * alive, what did it do, why did it not trade, what comes next, what is it holding, is its data
 * fresh -- with the cycle log last, because it is the longest and the least urgent.
 *
 * Everything on it crosses the wire already judged. The two sentences the page shows that the
 * payload does not carry as a `Field` -- the why-not reasons and the DCA note -- are the engine's
 * own words (`keel/commands/pulse.py`, `payload.NEXT_DCA_NOTE`) and are placed unchanged.
 *
 * @param {any} data  `/api/pulse`'s `data`, known non-null by the caller.
 * @returns {DocumentFragment}
 */
export function pulseView(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Pulse"));

  const sub = el("p", "sub");
  sub.append(field(data.generated_at));
  fragment.append(sub);

  fragment.append(haltControl(data));
  fragment.append(pulseState(data));
  fragment.append(pulseToday(data));
  fragment.append(pulseWhyNot(data));
  fragment.append(pulseNextDca(data));
  fragment.append(pulsePositions(data));
  fragment.append(pulseRules(data));
  fragment.append(pulseFreshness(data));
  fragment.append(pulseSubscriptions(data));
  fragment.append(pulseCycles(data));
  return fragment;
}

/**
 * The halt: one button while keel is running, one line while it is halted.
 *
 * **The branch is the PRESENCE of `data.halt.resume_command`**, which the payload sends as the
 * command when the kill switch is engaged and as `""` otherwise. This file may not read a
 * `Field`'s value to decide anything, so "button or line" has to arrive as structure, and the
 * accepted pattern for that is branching on a payload string being there (the activity note did,
 * and so does the Holdings view's stop column on a list). The command itself is the payload's too: this source never spells
 * it, so the console can say how to resume without ever being able to.
 *
 * **There is no resume control, here or anywhere in the client.** Releasing the halt is a Tier 1
 * ceremony that stays at a terminal; the line below names the command and offers nothing to
 * press. The confirm and the request belong to `main.js` and `actions.js` -- this builds a form
 * with `data-action="halt"` and a button, and nothing in it can send.
 *
 * @param {any} data
 * @returns {HTMLElement}
 */
function haltControl(data) {
  const card = el("div", "card halt");
  if (plain(data.halt.resume_command)) {
    const line = el("p", "halted");
    line.append(
      el("strong", undefined, "HALTED"),
      " — resume with ",
      el("code", undefined, plain(data.halt.resume_command)),
      " at a terminal.",
    );
    card.append(line);
    return card;
  }
  const form = el("form", "action");
  form.setAttribute("data-action", "halt");
  const button = el("button", "halt", "HALT");
  button.setAttribute("type", "submit");
  form.append(button, el("span", "action-outcome"));
  card.append(form);
  return card;
}

/**
 * The four state cards: mode and autonomy, equity and drawdown, rail 17, market session.
 *
 * No heading of its own -- the page's `<h1>` says "Pulse". The tables below DO carry one, because
 * a table needs an accessible name. The labels are the ones `docs.js::TERMS` links to the
 * glossary (`test_doc_links.py` fails if one stops being a `kv` label here), which is why they
 * moved with the cards rather than being reworded.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseState(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(
    gridCard([
      kv("mode", plain(data.mode)),
      kv("kill switch", data.kill_switch),
      kv("autonomy", data.autonomy.live),
      kv("autonomy configured", data.autonomy.configured),
      kv("autonomy lapses", data.autonomy.lapses_at),
      kv("rail 11", data.drawdown.rail11),
      kv("last cycle", data.last_cycle_at),
    ]),
  );

  fragment.append(
    gridCard([
      kv("equity state", data.equity.state_mode),
      kv("high water mark", data.equity.high_water_mark),
      kv("paper cash", data.equity.paper_cash),
      kv("drawdown (total)", data.drawdown.total),
      kv("drawdown (weekly)", data.drawdown.weekly),
      kv("max total dd", data.drawdown.max_total),
      kv("max weekly dd", data.drawdown.max_weekly),
    ]),
  );

  fragment.append(
    gridCard([
      kv("withdrawal attestation (rail 17)", data.withdrawal_attestation.state),
      kv("withdrawals", data.withdrawal_attestation.enabled),
      kv("attested at", data.withdrawal_attestation.attested_at),
      kv("expires in", data.withdrawal_attestation.expires_in),
      kv("expired for", data.withdrawal_attestation.expired_for),
    ]),
  );

  fragment.append(
    gridCard([
      kv("market session", data.market_session.state),
      kv("recorded at", data.market_session.recorded_at),
      kv("stale data", data.market_session.defused),
      kv("profile readable", data.autonomy.profile_readable),
    ]),
  );

  return fragment;
}

/**
 * Today's cycle counts, and how many open tranches have no bracket.
 *
 * The counts are summed by the service over the cycles in today's UTC day, and are ABSENT when the
 * log could not be read; `data.today.basis` says which (complete, partial from a window that began
 * after midnight, or unavailable), so a zero here is never an unread log. The bracket count is
 * the #641 hazard (a live position with no stop) and is judged `warn` when non-zero, here in the
 * payload rather than by this file.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseToday(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-today", "Today"));
  fragment.append(
    gridCard([
      kv("log coverage", data.today.basis),
      kv("cycles", data.today.cycles),
      kv("signals", data.today.signals),
      kv("blocked", data.today.blocked),
      kv("entered", data.today.entered),
      kv("exited", data.today.exited),
      kv("errors", data.today.errors),
      kv("brackets missing", data.unbracketed_count),
    ]),
  );
  return fragment;
}

/**
 * Why keel did not trade: the engine's own sentences, in the order the service wrote them.
 *
 * `stringList` answers `null` for an empty list, and that absence is the branch -- this file
 * never inspects the list to choose the empty sentence, and never words it either: whether
 * "nothing stopped keel" is TRUE depends on whether the log could be read back to midnight, which
 * the service judged (`data.why_not_none`).
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseWhyNot(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-why", "Why keel didn't trade"));
  const reasons = stringList(data.why_not, "reasons");
  if (reasons) fragment.append(reasons);
  else fragment.append(note(data.why_not_none));
  return fragment;
}

/**
 * The next DCA buy per live rule: a cadence DAY, not an order time.
 *
 * The fixed sentence under the table says how that day becomes an order, once, rather than a
 * time per row that this page would have to promise and the scheduler would have to keep.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseNextDca(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-next-dca", "Next DCA buy"));
  fragment.append(
    table(
      "h-next-dca",
      [
        { label: "product", numeric: false },
        { label: "cadence", numeric: false },
        { label: "budget", numeric: true },
        { label: "budget basis", numeric: false },
        { label: "next cadence day (UTC)", numeric: false },
      ],
      data.next_dca.map(
        /** @param {any} row */ (row) => [
          plain(row.product_id),
          row.cadence,
          row.budget,
          row.budget_inferred,
          row.next_cadence_day,
        ],
      ),
      "No live DCA rules.",
    ),
  );
  fragment.append(note(plain(data.next_dca_note)));
  return fragment;
}

/**
 * Open positions, with the `bracket` column: an unbracketed position is a position with no stop.
 *
 * Compact on purpose -- the roll-call that keeps the bracket warning on the glance page.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulsePositions(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-positions", "Open positions"));
  fragment.append(
    table(
      "h-positions",
      [
        { label: "product", numeric: false },
        { label: "rule", numeric: false },
        { label: "qty", numeric: true },
        { label: "entry", numeric: true },
        { label: "opened (UTC)", numeric: false },
        { label: "bracket", numeric: false },
      ],
      data.open_positions.map(
        /** @param {any} row */ (row) => [
          plain(row.product_id),
          plain(row.rule_name),
          row.qty,
          row.entry_price,
          row.opened_at,
          row.bracket,
        ],
      ),
      "No open positions.",
    ),
  );
  return fragment;
}

/**
 * The live rules, under the counts by status.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseRules(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-live-rules", "Rules"));
  const counts = el("p", "note");
  if (data.rule_counts.length === 0) {
    counts.textContent = "no rules";
  } else {
    // `entries()` again, for the separator: index 0 gets none, every later entry gets one.
    // The order is the server's -- `payload._rule_counts_payload` emits `rule_counts` as a LIST
    // of pairs rather than an object precisely so that ordering stays a presentation decision
    // made in Python, matching `render_human`'s own sort.
    for (const [index, count] of data.rule_counts.entries()) {
      if (index) counts.append(el("span", "muted", " · "));
      counts.append(plain(count.status), " ");
      counts.append(field(count.count));
    }
  }
  fragment.append(counts);
  fragment.append(
    table(
      "h-live-rules",
      [
        { label: "live rule", numeric: false },
        { label: "kind", numeric: false },
        { label: "status", numeric: false },
        { label: "product", numeric: false },
      ],
      data.live_rules.map(
        /** @param {any} row */ (row) => [
          plain(row.id),
          plain(row.kind),
          row.status,
          plain(row.product_id),
        ],
      ),
      "No live rules.",
    ),
  );
  return fragment;
}

/**
 * How old the newest candle is, per allowlisted product.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseFreshness(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-freshness", "Data freshness"));
  fragment.append(
    table(
      "h-freshness",
      [
        { label: "product", numeric: false },
        { label: "granularity", numeric: false },
        { label: "last candle (UTC)", numeric: false },
        { label: "age", numeric: false },
      ],
      data.data_freshness.map(
        /** @param {any} row */ (row) => [
          plain(row.product_id),
          plain(row.granularity),
          row.last_candle_at,
          row.age,
        ],
      ),
      "No market data yet.",
    ),
  );
  return fragment;
}

/**
 * The venue subscription attestations, stored beside effective, with the cap each one sets.
 *
 * On the glance page because rail 14 caps BUYS: a lapsed subscription is a reason keel did not
 * trade, and it belongs where the other reasons are.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseSubscriptions(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-subscriptions", "Subscriptions"));
  fragment.append(
    table(
      "h-subscriptions",
      [
        { label: "venue", numeric: false },
        { label: "tier", numeric: false },
        { label: "pacing", numeric: false },
        { label: "stored", numeric: false },
        { label: "effective", numeric: false },
        { label: "cap", numeric: true },
      ],
      data.subscriptions.map(
        /** @param {any} row */ (row) => [
          plain(row.venue),
          plain(row.tier_name),
          plain(row.pacing),
          row.stored_status,
          row.effective_status,
          row.effective_cap,
        ],
      ),
      "No subscription attestations.",
    ),
  );
  return fragment;
}

/**
 * Today's cycles from the engine's own log: the table, each cycle's records as a disclosure, and
 * the feed's caveats.
 *
 * The scope is fixed at today by the server (`api.read_pulse`), so there is no scope control and
 * no sort: cycles arrive newest first, which is the order a glance wants. `data.activity` is
 * `activity_payload`'s document, unchanged, and the helpers below take it as `feed`.
 *
 * The table's own empty text is scope-neutral ("Nothing in this scope.", as the Activity view
 * said): an empty list is also what an unreadable log and a partial window look like, so the
 * table never says whether keel ran. Only `data.no_cycles_line` does, below.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function pulseCycles(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-cycles", "Cycles today"));

  // Shown whenever the server sent one, which it does for every status except `ok` -- so the
  // presence of the note IS the branch, and this file never inspects the status word to make it.
  if (plain(data.activity.status_note) || plain(data.activity.detail)) {
    const card = el("div", "card");
    const what = el("strong", toneOf(data.activity.status));
    what.append(field(data.activity.status));
    card.append(what, " ");
    card.append(el("span", "muted", plain(data.activity.status_note)));
    if (data.activity.detail) card.append(el("div", "muted", plain(data.activity.detail)));
    fragment.append(card);
  }

  fragment.append(
    table(
      "h-cycles",
      [
        { label: "started (UTC)", numeric: false },
        { label: "cycle", numeric: false },
        { label: "mode", numeric: false },
        { label: "products", numeric: false },
        { label: "signals", numeric: true },
        { label: "blocked", numeric: true },
        { label: "entered", numeric: true },
        { label: "exited", numeric: true },
        { label: "errors", numeric: true },
        { label: "quiet", numeric: false },
        { label: "highlights", numeric: false },
      ],
      data.activity.cycles.map(
        /** @param {any} cycle */ (cycle) => [
          cycle.started_at,
          plain(cycle.cycle_id) || "uncorrelated",
          plain(cycle.mode) || "—",
          (cycle.products || []).join(", ") || "—",
          cycle.signals,
          cycle.blocked,
          cycle.entered,
          cycle.exited,
          cycle.errors,
          cycle.quiet,
          (cycle.highlights || []).join("; "),
        ],
      ),
      "Nothing in this scope.",
    ),
  );

  const noCycles = plain(data.no_cycles_line);
  if (noCycles !== "") fragment.append(noCyclesToday(noCycles, data));

  const detailed = data.activity.cycles.filter(
    /** @param {any} cycle */ (cycle) => (cycle.events || []).length !== 0,
  );
  if (detailed.length !== 0) {
    fragment.append(heading("h-events", "Cycle detail"));
    fragment.append(note("The engine's own records, per cycle, in the order they were written."));
    for (const cycle of detailed) fragment.append(cycleEvents(cycle));
  }

  fragment.append(feedNotes(data));
  return fragment;
}

/**
 * What to say when today holds no cycles.
 *
 * Never a blank panel, and never "no activity" either. `test_tui.py` pins the distinction
 * (`test_activity_overlay_with_nothing_today_is_never_blank_and_names_the_last_run`): a day with
 * nothing in it is either "keel has not run yet today", which is an ordinary morning for a daily
 * agent, or a window that could not prove it reached midnight -- which is not an observation
 * about the engine at all. The second is carried by `scope_fully_covered`, already judged, and
 * is in `feedNotes` below. The sentence itself is the service's (`data.no_cycles_line`, judged per
 * `today.basis`, and empty when the log could not be read): this file shows the card on its
 * presence and never words "keel has not run" from the emptiness of a list.
 *
 * Takes `data` and reads `data.activity.<key>`, NOT a parameter named `feed`: `_view_keys` checks
 * every `data.<key>` written in this region against `/api/pulse`, and a renamed root made these
 * reads invisible to it -- `feed.lines_reed` passed the suite (review F5).
 *
 * @param {string} line  `data.no_cycles_line`, the service's sentence.
 * @param {any} data  `/api/pulse`'s `data`.
 * @returns {HTMLElement}
 */
function noCyclesToday(line, data) {
  const card = el("div", "card");
  card.append(el("strong", undefined, line));
  const last = data.activity.last_cycle_before_scope;
  if (last) {
    const line = el("p", "note");
    line.append("Last cycle: ", field(last.started_at));
    card.append(line);
  } else {
    card.append(note("There is no earlier cycle in the window either."));
  }
  return card;
}

/**
 * One cycle's events, as a disclosure.
 *
 * An event's fields are rendered as `key=value` and NOT projected onto a fixed schema, matching
 * `_event_payload`'s reason for keeping them whole: "the event vocabulary grows with every
 * `log_event` call site, and an overlay that silently dropped a field it had not been taught
 * about would be worse than one that renders it as key=value."
 *
 * The `<summary>` is what names the inner table for a screen reader -- it is on screen, it says
 * which cycle this is, and using it means no hidden heading exists to drift out of sync with it.
 *
 * @param {any} cycle
 * @returns {HTMLElement}
 */
function cycleEvents(cycle) {
  const id = "h-cycle-".concat(plain(cycle.key));
  const node = el("details", "cycle");
  const summary = el("summary");
  summary.id = id;
  summary.append(
    [
      plain(cycle.started_at.display),
      plain(cycle.cycle_id) || "uncorrelated",
      (cycle.highlights || []).join("; "),
    ].join(" · "),
  );
  node.append(summary);

  node.append(
    table(
      id,
      [
        { label: "at (UTC)", numeric: false },
        { label: "level", numeric: false },
        { label: "event", numeric: false },
        { label: "fields", numeric: false },
      ],
      (cycle.events || []).map(
        /** @param {any} event */ (event) => [
          event.at,
          plain(event.level),
          plain(event.event),
          eventFields(event.fields),
        ],
      ),
      "No records for this cycle.",
    ),
  );

  // Rendered unconditionally rather than only when non-zero. Hiding a zero would mean deciding
  // that this count is uninteresting by INSPECTING it, and this file may not read a value to
  // decide anything; inside a collapsed disclosure the line costs nothing anyway.
  const dropped = el("p", "note");
  dropped.append(field(cycle.events_dropped), " record(s) beyond the display cap");
  node.append(dropped);
  return node;
}

/**
 * An event's fields, as `key=value` pairs.
 *
 * @param {any} fields
 * @returns {string}
 */
function eventFields(fields) {
  if (!fields || typeof fields !== "object") return "";
  return Object.entries(fields)
    .map(/** @param {any[]} pair */ (pair) => pair.join("="))
    .join(" ");
}

/**
 * The feed's own caveats, as one muted line -- `render_activity`'s `notes`, plus the two flags it
 * shows as prose and the payload sends judged.
 *
 * Every one of these is a reason a short or empty feed is NOT evidence that nothing happened,
 * which is why none is filtered out: `activity_payload`'s docstring calls `scope_fully_covered`
 * "the one field a client must not ignore".
 *
 * Takes `data` for `noCyclesToday`'s reason.
 *
 * @param {any} data  `/api/pulse`'s `data`.
 * @returns {HTMLElement}
 */
function feedNotes(data) {
  const line = el("p", "note");
  const counts = [
    ["hidden by the scope", data.activity.cycles_out_of_scope],
    ["lines read", data.activity.lines_read],
    ["unusable lines skipped", data.activity.lines_skipped],
    ["cycles beyond the display cap", data.activity.cycles_dropped],
  ];
  for (const [label, value] of counts) line.append(field(value), " ", label, " · ");
  line.append(
    field(data.activity.scope_fully_covered),
    " · ",
    field(data.activity.window_truncated),
  );
  return line;
}

// -- the remaining six views (#537) ---------------------------------------------------------------
//
// ── PARITY IS ON THE INFORMATION, NEVER ON THE MARKUP ──────────────────────────────────────────
//
// Each view below is judged against two records, and neither of them is the HTML's source:
// `keel/web/render.py`'s server-rendered pages (deleted at #540; this JS shell is the console now)
// and `tests/commands/test_tui.py`, which was the specification of what the curses screens show and
// is what #541 deleted against. Where those two disagree, or where one of them is WRONG, the
// divergence is written into the view's own docstring rather than reproduced.
//
// The wrongness is not hypothetical. `render.py` reads report fields through
// `getattr(obj, name, default)` and several of the names do not exist, so a blank or a default
// renders with no error anywhere -- #548 records five in the status renderer, and porting these
// six turned up two more. Both are named where they land.
//
// ── EVERY DISPLAY STRING BELOW STILL COMES OFF THE WIRE ────────────────────────────────────────
//
// Two prose lookups that `render.py` performs in the front-end -- the activity feed's status
// explanation and the setup step's kind note -- are `status_note` and `kind_note` on the payload
// now, and the reason is this file's own rule rather than tidiness: choosing a paragraph by a
// state word is a branch on `Field.value`, and `test_render_never_judges_a_value_itself` forbids
// this file from reading one at all. A client that may not read a value may not select prose with
// it either, which is the same principle one level up.

/**
 * Audit & Setup (console PR4): the tamper-evident timeline, the setup checklist and venue
 * readiness, on one page. It replaced the Timeline, Setup and Venues views.
 *
 * ── `/api/setup` IS THE PRIMARY READING, AND THAT DECIDES WHAT THIS PAGE CAN SURVIVE ─────────
 *
 * `/api/setup` is `needs_database=False`: it answers on a machine with nothing on it, which is the
 * only machine the stopped view's "Set keel up on this machine" link is ever followed from -- and
 * that link points here (`main.js`'s `SETUP_ROUTE`). The timeline and the venue readiness are
 * SECONDARY reads: a timeline with no database to read arrives as `null` and its section says so,
 * leaving the checklist that creates the database standing below it. The engine banner reports
 * the setup reading (planner ruling 14).
 *
 * In page order: the timeline (opening on keel's own record, `kind=keel` -- `main.js` seeds it),
 * then Setup (its own stage tabs and the page-level job panel), then venue readiness. Each is an
 * exported section builder of its own, so `tests/web/test_client_assets.py::_view_keys` checks
 * each one's reads against its own endpoint.
 *
 * @param {any} setup  `/api/setup`'s `data`, known non-null by the caller.
 * @param {string} section  which Setup section to show -- a `SETUP_SECTIONS` key, or `""`.
 * @param {(key: string) => void} onSection
 * @param {any} timeline  `/api/timeline`'s `data`, or `null` when there was none to read.
 * @param {any} timelineError  that read's `error`, or `null`.
 * @param {any} timelineSort
 * @param {(column: string, direction: string) => void} onTimelineSort
 * @param {(kind: string) => void} onKind
 * @param {any} venues  `/api/venues`'s `data`, or `null` when that read failed.
 * @returns {DocumentFragment}
 */
export function auditView(
  setup,
  section,
  onSection,
  timeline,
  timelineError,
  timelineSort,
  onTimelineSort,
  onKind,
  venues,
) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Audit & Setup"));
  fragment.append(timelineSection(timeline, timelineError, timelineSort, onTimelineSort, onKind));
  fragment.append(setupSection(setup, section, onSection));
  fragment.append(readinessSection(venues));
  return fragment;
}

/**
 * The setup checklist: `/api/setup`'s payload, in RUNBOOK order -- the Setup part of Audit &
 * Setup.
 *
 * Every action keel offers is listed with its title, its detail and the inputs it asks for, and
 * each MECHANICAL one is a form that performs it (`actionCard`): `/api/setup` is the one document
 * that carries this session's CSRF write token, and `main.js` keeps it off this reading. Refusal
 * stays the server's job regardless: a `/api/setup/*` POST reaches only
 * `keel.commands.setup.ACTIONS` and the server refuses anything else whatever any front-end draws.
 *
 * The kind note for an `operator_input` step is carried on the payload (`_STEP_KIND_NOTES` has
 * all four kinds), so the step that asks for a credential explains itself.
 *
 * @param {any} data  `/api/setup`'s `data`, known non-null by the caller.
 * @param {string} [section]  which section to show -- a `SETUP_SECTIONS` key, or `""`.
 * @param {(key: string) => void} [onSection]  omitted, and every section renders. See `sectioned`.
 * @returns {DocumentFragment}
 */
export function setupSection(data, section, onSection) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-setup", "Setup"));
  fragment.append(el("p", "sub", plain(data.root)));

  // ABOVE the switch, and outside every section (#775). `market_data` is a `Stage.PAPER` step,
  // so the button that starts this job is on the "To run in paper" tab -- and the first spelling
  // put the panel reporting it inside the "Setup" section, where the operator who pressed it is
  // not looking. The state line, the elapsed time, the streamed transcript and `job.error` were
  // all invisible to the one reader they exist for, which defeats `jobPanel`'s own reason for
  // keeping a failure on screen: "the whole point of running something in the background is that
  // nobody was watching when it broke."
  if (data.job) fragment.append(jobPanel(data.job));

  fragment.append(sectioned(data, SETUP_SECTIONS, section, onSection, "Setup section"));
  return fragment;
}

/**
 * The three sections of the setup page: where this deployment stands, then the two runbook
 * stages in the order the runbook works down.
 *
 * **This is the `STAGES` table it replaces, with the tab bar folded into it.** The stage wording
 * is `render.py::render_setup`'s own, because it carries an argument rather than a label: paper
 * "places nothing" is the sentence that makes paper the safe default, and paraphrasing it in a
 * second front-end weakens it. It is written HERE, once, and reaches both the tab and the
 * stage's heading through `entry.label` -- a tab and a heading that could disagree about which
 * stage a reader is looking at is precisely what one table forecloses.
 *
 * `blurb` is what the stage sections add and the summary has none of: the summary's own
 * argument is the deployment card under it.
 *
 * @type {Section[]}
 */
const SETUP_SECTIONS = [
  { key: "setup", label: "Setup", build: setupSummary },
  {
    key: "paper",
    label: "To run in paper",
    build: setupPaper,
    blurb: "Evaluates rules against real market data and places nothing.",
  },
  {
    key: "live",
    label: "To go live",
    build: setupLive,
    blurb: "Everything the go-live runbook adds before real money moves.",
  },
];

/**
 * Where this deployment stands, and the one thing to do next.
 *
 * No heading -- the Setup section's own heading says "Setup" and the tab bar says which is on.
 *
 * **The running job's panel is NOT here**, and the first spelling's argument for putting it here
 * was backwards -- see `setupSection`. A panel inside a section reports to whoever is reading that
 * section, which is never the operator who pressed the button, because every action lives in a
 * stage section and the panel did not.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
function setupSummary(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(
    gridCard([
      kv("deployment", data.is_new),
      kv("database", data.has_usable_database),
      kv("paper stage", data.ready_for_paper),
      kv("config file", plain(data.config_path)),
      kv("database file", plain(data.db_path)),
    ]),
  );

  const steps = data.steps || [];
  const next = steps.find(/** @param {any} step */ (step) => step.key === data.next_step);
  const nextCard = el("div", "card");
  if (next) {
    nextCard.append(kv("next", plain(next.title)));
    nextCard.append(note(plain(next.how)));
  } else {
    nextCard.append(el("p", undefined, "Nothing outstanding."));
  }
  fragment.append(nextCard);
  return fragment;
}

/**
 * The paper stage's steps.
 *
 * @param {any} data
 * @param {Section} entry
 * @returns {DocumentFragment}
 */
function setupPaper(data, entry) {
  return stageSteps(data, entry);
}

/**
 * The live stage's steps.
 *
 * A function of its own rather than a shared one bound to a key, so that `SETUP_SECTIONS` reads
 * as three sections with three builders and a table pointing two entries at one function -- the
 * mutation that renders the paper checklist under the "To go live" tab -- is not spellable.
 *
 * @param {any} data
 * @param {Section} entry
 * @returns {DocumentFragment}
 */
function setupLive(data, entry) {
  return stageSteps(data, entry);
}

/**
 * One runbook stage: its heading, its argument, and the steps that belong to it.
 *
 * The step cards, the actions map and the not-automated map are all built HERE rather than once
 * for the page, because only one stage renders at a time now -- building the other stage's maps
 * would be work for a section nobody is looking at.
 *
 * @param {any} data
 * @param {Section} entry
 * @returns {DocumentFragment}
 */
function stageSteps(data, entry) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-stage-".concat(entry.key), entry.label));
  fragment.append(note(entry.blurb || ""));

  const steps = data.steps || [];
  const here = steps.filter(/** @param {any} step */ (step) => step.stage === entry.key);
  if (here.length === 0) {
    fragment.append(el("p", "empty", "No steps in this stage."));
    return fragment;
  }

  const notAutomated = new Map(
    (data.not_automated || []).map(/** @param {any} row */ (row) => [row.key, row.why]),
  );
  const actions = new Map(
    (data.actions || []).map(/** @param {any} action */ (action) => [action.key, action]),
  );
  for (const step of here) fragment.append(stepCard(step, actions, notAutomated));
  return fragment;
}

/**
 * One checklist step.
 *
 * `done` is three-valued and stays three-valued all the way to the screen: `_step_payload` sends
 * `flag(None)` for "could not be determined", which arrives as `—`, and `StepState.done`'s own
 * comment says why -- "an unreadable database is not an unseeded one, and reporting it as
 * incomplete would send an operator to re-run a step that may already be done".
 *
 * @param {any} step
 * @param {Map<string, any>} actions
 * @param {Map<string, any>} notAutomated
 * @returns {HTMLElement}
 */
function stepCard(step, actions, notAutomated) {
  const card = el("div", "card step");
  const head = el("div", "stephead");
  head.append(field(step.done));
  head.append(pill(plain(step.kind.display), toneOf(step.kind)));
  head.append(el("strong", undefined, plain(step.title)));
  card.append(head);

  if (step.detail) card.append(el("p", "muted", plain(step.detail)));

  const action = actions.get(step.key);
  if (action) {
    card.append(actionCard(action));
  } else if (step.how) {
    const how = el("p");
    how.append(el("code", undefined, plain(step.how)));
    card.append(how);
  }

  const why = notAutomated.get(step.key);
  if (why) {
    const line = el("p", "note");
    line.append("Not a button, deliberately: ", plain(why));
    card.append(line);
  }

  const explain = el("p", "note");
  explain.append(plain(step.why), " ", plain(step.kind_note));
  card.append(explain);
  return card;
}

/**
 * One declared action, as a FORM that performs it (#540).
 *
 * It was a description until #540 -- a list of the fields keel would ask for, and a link to the
 * server-rendered `/setup` page, "which is where this session's write token is". That page is
 * deleted; the write token now arrives in `/api/setup`'s own document, and this is where the
 * action runs.
 *
 * **The gate did not move with the button, and that is the property to keep in view.** What may be
 * performed here is `keel.commands.setup.ACTIONS` and nothing else: idempotent, non-destructive,
 * `MECHANICAL` steps, asserted disjoint from the ten capability-increasing actions. A client
 * that hides a button is not a gate, so nothing is hidden -- and nothing needed to be, because the
 * server refuses what is not in that set regardless of what this file draws.
 *
 * `secret` arrives carrying its own words -- `_action_input_payload` sends "never echoed back"
 * against "shown as typed" -- rather than as a boolean this file would have to translate, which
 * is the same rule that keeps `state` out of the client's hands. The INPUT TYPE comes from a
 * separate `kind` string for exactly that reason: a password field drawn as `type="text"` puts a
 * credential on screen and into the browser's autofill history, and choosing which to draw must
 * not require this file to read a `Field`'s value.
 *
 * @param {any} action
 * @returns {HTMLElement}
 */
function actionCard(action) {
  const card = el("div", "action");
  card.append(el("p", "muted", plain(action.detail)));

  const key = plain(action.key);
  const form = el("form", "action-form");
  form.setAttribute("data-action", key);

  for (const input of action.inputs || []) {
    form.append(actionField(key, input));
  }

  const button = el("button", "run");
  button.setAttribute("type", "submit");
  button.append(document.createTextNode(plain(action.title)));
  form.append(button);

  // Filled by `main.js` with the server's own `display` for the result. Present in the markup
  // rather than created on submit, because it is an `aria-live` region and a live region has to
  // exist BEFORE the text it announces is put in it -- `index.html`'s engine banner carries the
  // same note for the same reason.
  const outcome = el("p", "action-outcome");
  outcome.setAttribute("role", "status");
  outcome.setAttribute("aria-live", "polite");
  form.append(outcome);

  card.append(form);
  return card;
}

/**
 * One field of an action's form.
 *
 * `choices` renders a `<select>` whose first option is disabled and selected with an EMPTY value,
 * so nothing is pre-chosen. A select that arrives with a valid answer already in it is a form
 * that can be submitted without a decision having been made, and every one of these fields is a
 * decision about a deployment.
 *
 * @param {string} key    the action's key, for ids that cannot collide across cards.
 * @param {any} input
 * @returns {HTMLElement}
 */
function actionField(key, input) {
  const name = plain(input.name);
  const id = "f-".concat(key, "-", name);
  const wrap = el("div", "field");
  const label = el("label", undefined, plain(input.label));
  label.setAttribute("for", id);
  wrap.append(label);

  const choices = input.choices || [];
  let control;
  if (choices.length !== 0) {
    control = el("select");
    const placeholder = el("option", undefined, "choose…");
    placeholder.setAttribute("value", "");
    placeholder.setAttribute("disabled", "disabled");
    placeholder.setAttribute("selected", "selected");
    control.append(placeholder);
    for (const choice of choices) {
      const option = el("option", undefined, plain(choice));
      option.setAttribute("value", plain(choice));
      control.append(option);
    }
  } else {
    control = el("input");
    // `input.kind`, not `input.secret.value`: the server sends the rendering instruction as a
    // plain string precisely so this file never reads a `Field`'s value. See the note beside
    // `kind` in `payload._action_input_payload`.
    control.setAttribute("type", plain(input.kind) === "secret" ? "password" : "text");
    control.setAttribute("autocomplete", "off");
    control.setAttribute("spellcheck", "false");
  }
  control.id = id;
  control.setAttribute("name", name);
  wrap.append(control);

  if (input.hint) wrap.append(el("div", "muted", plain(input.hint)));
  // The server's own sentence about what `secret` means for this field, shown beside it rather
  // than translated: "never echoed back" is a promise about handling, and restating it here in
  // this file's words would be this file making that promise instead.
  wrap.append(field(input.secret));
  return wrap;
}

/**
 * A running, finished or failed background job.
 *
 * **This is the only `aria-live` region on any view, and the restraint is the decision.**
 * `index.html`'s note argues that a live region around DATA is a denial of service against anyone
 * using a screen reader -- this dashboard re-reads itself, and re-announcing every table twice a
 * minute is not an accessibility feature. A background job is the exception that argument itself
 * implies: it is the one thing on any of these views that changes while the reader is sitting
 * still waiting for it to, and "it finished" is a fact they are waiting to be TOLD rather than
 * one they can go back and re-read for.
 *
 * Scoped to the STATE LINE and not to the panel: the progress lines are the transcript of a
 * command, which is exactly what nobody wants re-spoken every five seconds.
 *
 * @param {any} job
 * @returns {HTMLElement}
 */
function jobPanel(job) {
  const card = el("div", "card job");

  const line = el("div", "kv");
  line.append(el("span", "k", plain(job.key)));
  const value = el("span", "v");
  value.append(field(job.state));
  line.append(value);
  line.setAttribute("role", "status");
  line.setAttribute("aria-live", "polite");
  line.setAttribute("aria-atomic", "true");
  card.append(line);

  const elapsed = el("p", "note");
  elapsed.append(field(job.elapsed), " elapsed");
  card.append(elapsed);

  // A failure stays on screen rather than being cleared on the next poll. `_job_panel`'s own
  // reason, kept: "the whole point of running something in the background is that nobody was
  // watching when it broke."
  if (job.error) card.append(el("p", "bad", plain(job.error)));

  const lines = job.lines || [];
  if (lines.length !== 0) {
    // Newest LAST and unscrolled, exactly as the CLI prints them -- `_job_payload`'s note: "an
    // operator who has run `keel fetch` in a terminal should recognise what they are looking at
    // rather than have to learn a second vocabulary for the same thing."
    card.append(el("pre", "joblines", lines.join("\n")));
  }
  return card;
}

/**
 * The timeline (#703), the first section of Audit & Setup: one chronology over four stores that
 * never knew about each other.
 *
 * ── PROVENANCE IS A COLUMN, NOT A FOOTNOTE ───────────────────────────────────────────────────
 *
 * The engine's log, the orders book, an imported ledger and a human's attestation are four
 * different kinds of evidence. Merging them into one feed is only an improvement if the feed
 * keeps them apart -- otherwise it is four tables with their labels removed. So every row
 * carries `provenance`, decided in `keel/commands/timeline.py` and styled by `payload.py`;
 * `simulated` is the one that warns, because a paper fill sitting in a chronology beside real
 * ones is the single row a reader must not skim past.
 *
 * ── THE CHIPS COME FROM THE WINDOW, AND FILTER ON THE SERVER ─────────────────────────────────
 *
 * `data.kinds` is what the SCOPED window holds, not the four kinds keel can emit and not what
 * survived the current chip -- a bar built from the rows on screen deletes its own alternatives.
 * Pressing one re-asks the server, because the page is capped and a client-side filter would be
 * filtering the rows that happened to arrive.
 *
 * ── THE EXPORT IS A LINK, NOT A FETCH ────────────────────────────────────────────────────────
 *
 * A plain `<a download>` to `/api/timeline/export.csv`, carrying the same `?scope=`/`?kind=` the
 * page is showing so the file matches the screen. It is a navigation rather than a scripted
 * download because the response is `Content-Disposition: attachment` and the browser's own
 * handling of that is the behaviour we want -- and because this file writes no bytes itself.
 *
 * @param {any} data  `/api/timeline`'s `data`, or `null` when there was none to read.
 * @param {any} error  that read's `error`, or `null`.
 * @param {any} sort
 * @param {(column: string, direction: string) => void} onSort
 * @param {(kind: string) => void} onKind
 * @returns {DocumentFragment}
 */
export function timelineSection(data, error, sort, onSort, onKind) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-audit-timeline", "Timeline"));
  // A SECONDARY read. No database here is the first-run machine the stopped view sends to this
  // page, and the checklist below is what creates one -- so this says which of the two it is and
  // leaves the rest of the page standing.
  if (data === null) {
    const gap = el("p", "empty");
    if (error) gap.append("The timeline could not be read: ", plain(error.detail || error.title));
    else gap.append("No timeline yet: there is no database on this machine. Setup creates one.");
    fragment.append(gap);
    return fragment;
  }

  const sub = el("p", "sub");
  sub.append(field(data.generated_at), " · ");
  sub.append(field(data.shown_count), " of ", field(data.filtered_count), " shown");
  fragment.append(sub);

  if (onKind) fragment.append(kindSwitch(plain(data.kind), data.kinds || [], onKind));

  fragment.append(
    gridCard([
      kv("scope", plain(data.scope)),
      kv("since", data.scope_start_at),
      kv("in this window", data.scoped_count),
      kv("in this chip", data.filtered_count),
    ]),
  );

  // The export, carrying the page's own scope and chip so the file matches the screen.
  const actions = el("p", "note");
  const link = el("a", undefined, "Export CSV");
  // Encoded, like every other URL this client builds (`api.js` uses `searchParams`). `kind` is
  // the server's echo of caller-supplied text, and a value carrying `&` or `#` would otherwise
  // reshape the query rather than travel in it. Not reachable with hostile input today -- the
  // params are in-memory and seeded from `data.kinds` -- so this is the convention, kept.
  const target = new URL("/api/timeline/export.csv", window.location.origin);
  target.searchParams.set("scope", plain(data.scope));
  target.searchParams.set("kind", plain(data.kind));
  link.setAttribute("href", target.pathname.concat(target.search));
  link.setAttribute("download", "");
  actions.append(link);
  actions.append(" — every row with its provenance, its hash and whether the chain verifies it.");
  fragment.append(actions);

  // The chain's verdict over the whole record, above the rows it applies to. A per-row status
  // answers "is this row evidence"; this answers "has this record been altered", which is the
  // question an operator opening an audit page is actually asking.
  const chain = el("p", "note");
  chain.append(field(data.chain));
  fragment.append(chain);

  // Why `system` rows may be missing. The keel-only default is mostly those rows, and an engine
  // log that did not reach the report would otherwise leave an emptier table with no reason.
  if (data.engine_log) {
    const logNote = el("p", "note");
    logNote.append(field(data.engine_log));
    fragment.append(logNote);
  }

  fragment.append(heading("h-timeline", "What happened"));
  fragment.append(
    table(
      "h-timeline",
      [
        // `at`, matching the payload and `sortable`. It was left as `ts` when the server side
        // was renamed, and `headerCell` only draws a sort control for a key the server declares
        // -- so the timestamp column of a CHRONOLOGY page silently became an unclickable label.
        { label: "when (UTC)", numeric: false, key: "at" },
        { label: "kind", numeric: false, key: "kind" },
        { label: "how we know", numeric: false, key: "provenance" },
        { label: "source", numeric: false, key: "source" },
        { label: "reference", numeric: false },
        { label: "product", numeric: false, key: "product_id" },
        { label: "amount", numeric: true },
        { label: "what", numeric: false },
        // Both columns, never one. A hash with nothing saying whether it verifies is a number a
        // reader takes on trust, and the reading taken on trust is the flattering one.
        { label: "row hash", numeric: false },
        { label: "tamper-evidence", numeric: false },
      ],
      (data.rows || []).map(/** @param {any} row */ (row) => [
        row.at,
        plain(row.kind) || "—",
        row.provenance,
        plain(row.source) || "—",
        plain(row.reference) || "—",
        plain(row.product_id) || "—",
        row.amount,
        plain(row.summary) || "—",
        row.row_hash,
        row.chain_status,
      ]),
      "Nothing recorded in this window.",
      { sort: sort, onSort: onSort },
    ),
  );

  return fragment;
}


/**
 * The Timeline's kind chips (#703).
 *
 * Built from `kinds` -- what the window holds -- with a leading "all" that clears the filter,
 * the same shape `statusSwitch` takes for Orders and for the same reasons: a bar listing every
 * kind keel can emit invites a reader into empty chips, and a filter with no way back is a trap.
 *
 * @param {string} current
 * @param {string[]} kinds
 * @param {(kind: string) => void} onKind
 * @returns {HTMLElement}
 */
function kindSwitch(current, kinds, onKind) {
  const wrap = el("nav", "scopes");
  wrap.setAttribute("aria-label", "Timeline kind");
  wrap.append(el("span", "k", "kind"));
  const all = el("button", "scopekey", "all");
  all.setAttribute("type", "button");
  all.setAttribute("data-focus", "kind:");
  if (!current) all.setAttribute("aria-current", "true");
  all.addEventListener("click", () => onKind(""));
  wrap.append(all);
  for (const name of kinds) {
    const button = el("button", "scopekey", name);
    button.setAttribute("type", "button");
    button.setAttribute("data-focus", "kind:".concat(name));
    if (name === current) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => onKind(name));
    wrap.append(button);
  }
  return wrap;
}


/**
 * The Holdings view (console PR2): what is held, what each tranche cost, how it was entered, and
 * the orders keel has placed lately. It replaced the Positions and Orders views.
 *
 * ── NO CLOSE ACTION, NO CANCEL ACTION, AND THAT IS THE DESIGN ────────────────────────────────
 *
 * Alpaca's positions page has a per-row close. This one does not, ever: an exit goes through the
 * typed-phrase friction of the terminal path, because a panic tap on a table row must not be the
 * last line of defence between an operator and an unplanned market sell. The orders section has no
 * cancel cell either -- `keel orders cancel` and the exit path stay at the terminal, and the console
 * is a reader plus the one halt on Pulse. The absence is pinned by
 * `tests/web/test_holdings_view.py::test_the_holdings_view_has_no_close_or_cancel_action_anywhere`,
 * which counts the buttons and listeners in the builders below (none), so it survives the day it
 * looks like an obvious convenience to add.
 *
 * ── TWO READINGS, ONE PAGE ───────────────────────────────────────────────────────────────────
 *
 * `data` is `/api/holdings`, the primary: it supplies the engine banner and the tranche sort.
 * `orders` is `/api/orders`, a SECONDARY read with its own `?scope=`, `?status=` and `?sort=`
 * surface -- which is why the two payloads are named differently here. `_view_keys` scans this
 * view's region once per root, and a shared name would check one endpoint's keys against the other.
 *
 * @param {any} data  `/api/holdings`'s `data`.
 * @param {any} sort
 * @param {(column: string) => void} onSort
 * @param {any} orders  `/api/orders`'s `data`, or `null` when that read failed.
 * @param {any} ordersSort
 * @param {(column: string) => void} onOrdersSort
 * @param {(scope: string) => void} onScope
 * @param {(status: string) => void} onStatus
 * @returns {DocumentFragment}
 */
export function holdingsView(
  data,
  sort,
  onSort,
  orders,
  ordersSort,
  onOrdersSort,
  onScope,
  onStatus,
) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Holdings"));

  const sub = el("p", "sub");
  sub.append(field(data.generated_at), " · ");
  sub.append(field(data.open_count), " open tranche(s)");
  fragment.append(sub);

  fragment.append(holdingsSection(data, sort, onSort));
  fragment.append(recentOrdersSection(orders, ordersSort, onOrdersSort, onScope, onStatus));
  return fragment;
}

/**
 * The open tranches, one heading and table per rule, then each tranche's realized legs.
 *
 * ── GROUPED BY THE REPORT'S OWN RULE LIST ────────────────────────────────────────────────────
 *
 * `data.rules` rather than a set this file assembles from the rows: two answers to "which rules
 * hold tranches" is one too many, and the ordered one is already on the report. A rule holds
 * several TRANCHES -- that is what tranches are for -- so each section is a table of them rather
 * than one row pretending to be the position. Filtering the rows by that key is string equality on
 * a bare name, the same thing the Positions view did with the product; no value is judged.
 *
 * ── THE COLUMNS ARE THE ENTRY'S, NOT THE MARKET'S ────────────────────────────────────────────
 *
 * There is no mark, market value, unrealized figure or distance to the stop: this view is about
 * what each tranche cost and how it was entered, and `keel positions` keeps the marked figures.
 * `cost` is the service's (`keel/commands/holdings.py`); nothing here multiplies anything.
 *
 * `entry gate` is the ENTRY GATE's verdict, not a data age -- `missing`/`behind`/`unconfirmed` are
 * the agent's own reasons for refusing to open here, the most common answer to "why has nothing
 * happened". It is a COLUMN and not a per-rule chip because the gate granularity is the one the
 * rule that opened each tranche declares. `attestation` sits BESIDE it and never merged with it:
 * one is a claim about the WORLD (what this token is, and whether the claim is in date) and the
 * other a claim about DATA, and they disagree in both directions.
 *
 * ── THE STOP COLUMN IS DRAWN ON A LIST ───────────────────────────────────────────────────────
 *
 * Spliced into the header AND the cells only when `data.stop_rows_present` -- the tranche ids that
 * carry a stop -- is non-empty. List emptiness is the sanctioned branch (this file may not read a
 * `Field`'s `display` or `value` to decide anything), and a column of "—" on a book where no
 * tranche has a stop would say nothing. One guard serves both splices so they cannot disagree.
 *
 * @param {any} data
 * @param {any} sort
 * @param {(column: string) => void} onSort
 * @returns {DocumentFragment}
 */
function holdingsSection(data, sort, onSort) {
  const fragment = document.createDocumentFragment();

  const rows = data.rows || [];
  if (rows.length === 0) {
    // A real answer, not a blank panel: an account holding nothing is an ordinary state for a
    // daily agent between entries, and it is not the same as a page that failed to load.
    fragment.append(el("p", "empty", "No open tranches. keel is holding nothing right now."));
    return fragment;
  }

  const withStop = (data.stop_rows_present || []).length !== 0;

  for (const rule of data.rules || []) {
    const held = rows.filter(/** @param {any} row */ (row) => row.rule_name === rule);
    const id = ["h-hold", rule].join("-");
    fragment.append(heading(id, plain(rule) || "unattributed"));
    if (held.length === 0) continue;

    fragment.append(
      table(
        id,
        [
          // The id `keel positions close` takes, paired with its own row.
          { label: "tranche", numeric: false },
          { label: "opened (UTC)", numeric: false, key: "opened_at" },
          { label: "product", numeric: false, key: "product_id" },
          { label: "qty", numeric: true, key: "qty" },
          { label: "entry", numeric: true, key: "entry_fill" },
          { label: "fee", numeric: true },
          { label: "cost", numeric: true, key: "cost" },
          ...(withStop ? [{ label: "stop", numeric: true }] : []),
          { label: "bracket", numeric: false },
          // PER TRANCHE, not per rule: see the note above.
          { label: "entry gate", numeric: false },
          { label: "attestation", numeric: false },
          // How the entry order was placed and what it filled at. All three fill figures say
          // "unknown" when no entry order was identified -- never a zero, never a blank.
          { label: "placement", numeric: false },
          { label: "expected", numeric: true },
          { label: "actual", numeric: true },
          { label: "slippage", numeric: true, key: "fill_divergence" },
        ],
        held.map(/** @param {any} row */ (row) => [
          row.id,
          row.opened_at,
          plain(row.product_id) || "—",
          row.qty,
          row.entry_fill,
          row.entry_fee,
          row.cost,
          ...(withStop ? [row.stop] : []),
          row.bracket,
          row.freshness,
          row.attestation,
          row.placement,
          row.expected_fill,
          row.actual_fill,
          row.fill_divergence,
        ]),
        "No open tranches for this rule.",
        { sort: sort, onSort: onSort },
      ),
    );

    for (const row of held) {
      // The realized side, under a disclosure: a scaled-out tranche has legs already booked, and
      // they belong beside the running position rather than in the closed-trade journal's separate
      // account of the same trade (`keel insights journal`). Collapsed because most tranches have never been scaled out. There is no
      // "marked at" here because there is no mark.
      const node = el("details", "row");
      const summary = el("summary");
      summary.append("tranche ", plain(row.id), " · ", plain(row.product_id) || "—", " · realized legs");
      node.append(summary);
      node.append(
        gridCard([
          kv("realized qty", row.realized_qty),
          kv("realized proceeds", row.realized_proceeds),
          kv("realized fees", row.realized_fees),
        ]),
      );
      fragment.append(node);
    }
  }

  return fragment;
}

/**
 * What keel has placed lately (#659): the orders book, compact, under the tranches.
 *
 * **`placement` is the first column, and that is the argument this table exists to make.** On a
 * deployment running `autonomy: ON`, "did I approve this, or did keel place it alone" is the first
 * question about an order, not the last -- so it leads the row. The word and its tone arrive
 * already decided (`payload._order_row_payload`), because `bypass` meaning the same thing as
 * `autonomous` is a judgement, and judgements are made in Python.
 *
 * **`expected`, `actual` and `slippage` are adjacent.** The last is the difference between the
 * first two, with a side-aware tone from the server for the reason `_order_row_payload` states:
 * paying more than expected is bad on a buy and good on a sell, so a client colouring the minus
 * sign would be wrong on half the rows.
 *
 * **`orders` is `null` when the secondary read failed**, and the section then says so in a
 * sentence and draws nothing else. An empty table there would read as "keel placed no orders",
 * which is a claim nobody made. The heading is drawn first, so the sentence has a place.
 *
 * **`raw_response` is not in the payload at all**, so this function could not render it if it tried.
 *
 * @param {any} orders  `/api/orders`'s `data`, or `null`.
 * @param {any} ordersSort
 * @param {(column: string) => void} onOrdersSort
 * @param {(scope: string) => void} onScope
 * @param {(status: string) => void} onStatus
 * @returns {DocumentFragment}
 */
function recentOrdersSection(orders, ordersSort, onOrdersSort, onScope, onStatus) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-orders", "Recent orders by keel"));
  if (!orders) {
    fragment.append(
      el(
        "p",
        "empty",
        "The recent orders could not be read just now, so none are shown. That is a failed read, not an empty book.",
      ),
    );
    return fragment;
  }

  const sub = el("p", "sub");
  // #707. The badge says what this console does about orders: it places and cancels none.
  if (orders.write_posture) sub.append(field(orders.write_posture));
  fragment.append(sub);

  // A server-side scope switch: filtering the received page here would present the rows that
  // happened to arrive under `?limit=` as the whole scope.
  fragment.append(scopeSwitch(plain(orders.scope), onScope, "Orders scope"));
  // Guarded, so a caller that has not wired the tabs renders the section unchanged rather than a
  // bar whose buttons do nothing.
  if (onStatus) {
    fragment.append(statusSwitch(plain(orders.status), orders.statuses || [], onStatus));
  }

  const placed = orders.rows || [];
  if (placed.length === 0) {
    // The specific fact, not a bare empty table: "this book has never held an order" and "the
    // window you chose excluded them" are different claims.
    fragment.append(emptyOrders(orders));
    return fragment;
  }

  fragment.append(
    table(
      "h-orders",
      [
        { label: "placement", numeric: false, key: "placement" },
        // The rule's NAME, beside the placement rather than buried in a disclosure: on a book with
        // several rules live, which one placed an order is a scanning question. Not sortable --
        // `/api/orders` declares no such column -- so it carries no `key`.
        { label: "rule", numeric: false },
        { label: "side", numeric: false, key: "side" },
        { label: "product", numeric: false, key: "product_id" },
        { label: "status", numeric: false, key: "status" },
        { label: "expected", numeric: true, key: "expected_fill" },
        { label: "actual", numeric: true, key: "actual_fill" },
        { label: "slippage", numeric: true, key: "fill_divergence" },
        { label: "placed (UTC)", numeric: false, key: "created_at" },
      ],
      placed.map(
        /** @param {any} row */ (row) => [
          row.placement,
          // Positional: `table()` pairs `columns[index]` with this array by index, so this cell
          // sits where the "rule" header does and every later one shifts if it is missing.
          row.rule_name,
          row.side,
          plain(row.product_id) || "—",
          row.status,
          row.expected_fill,
          row.actual_fill,
          row.fill_divergence,
          row.created_at,
        ],
      ),
      "No orders to show.",
      { sort: ordersSort, onSort: onOrdersSort },
    ),
  );
  return fragment;
}

/**
 * What to say when there are no rows, which is NEVER a bare empty table.
 *
 * "This book has never held an order" and "the window you chose excluded them" are different
 * facts, and an empty table that could mean either is a surface asserting something it has not
 * established. The sentence arrives already chosen (`payload._EMPTY_NOTES`) because selecting
 * prose from an enum word is a branch on a value, which this file does not do.
 *
 * @param {any} orders  `/api/orders`'s `data`, named for the root `_view_keys` scans it under.
 * @returns {HTMLElement}
 */
function emptyOrders(orders) {
  const card = el("div", "card");
  card.append(el("strong", undefined, plain(orders.empty_note) || "No orders to show."));
  return card;
}

/**
 * The Orders status tabs (#700).
 *
 * **Built from `statuses`, never from a constant.** A tab bar listing every status keel CAN
 * write would invite a reader to click into four empty tabs and conclude something about the
 * engine from what is really a list of possibilities. `statuses` is what this book actually
 * recorded, which is why the service carries it and why it comes from the whole book rather than
 * the open scope -- a tab that vanishes on a quiet day is worse than one that is empty.
 *
 * **`current` is what the report APPLIED**, not what the client last asked for. If the two ever
 * disagree the report is right, and a bar drawn from the request would show a filter that is not
 * in force.
 *
 * The leading "all" tab is not decoration: without it, a reader who has clicked into a status has
 * no way back short of knowing that the empty string means every status.
 *
 * @param {string} current  `data.status` -- `""` when unfiltered.
 * @param {string[]} statuses  `data.statuses`.
 * @param {(status: string) => void} onStatus
 * @returns {HTMLElement}
 */
function statusSwitch(current, statuses, onStatus) {
  const wrap = el("nav", "scopes");
  wrap.setAttribute("aria-label", "Order status");
  wrap.append(el("span", "k", "status"));
  const all = el("button", "scopekey", "all");
  all.setAttribute("type", "button");
  // Pressing a tab replaces the whole view, which destroys the button that was pressed.
  // `data-focus` is how `main.js` puts focus back on its replacement -- without it a keyboard
  // user is returned to the top of the document on every tab press, which is exactly what the
  // note above `sortTrigger` says this attribute exists to prevent.
  all.setAttribute("data-focus", "status:");
  if (!current) all.setAttribute("aria-current", "true");
  all.addEventListener("click", () => onStatus(""));
  wrap.append(all);
  for (const name of statuses) {
    const button = el("button", "scopekey", name);
    button.setAttribute("type", "button");
    button.setAttribute("data-focus", "status:".concat(name));
    if (name === current) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => onStatus(name));
    wrap.append(button);
  }
  return wrap;
}

/**
 * The three scopes, and the one that is on.
 *
 * The order is `keel.commands.activity.ACTIVITY_SCOPES`', which is the order the TUI's `t` key
 * cycles them in.
 *
 * **All three stay buttons, including the current one**, where the rendered page makes the
 * current scope a bare `<strong>`. The rendered page is right for a LINK -- a link to the page
 * you are on is a keyboard stop that goes nowhere -- and wrong for this: pressing the current
 * scope re-reads it, which is a refresh, and taking the control away is what makes focus vanish
 * when the view is rebuilt underneath a keyboard user who just pressed it. `aria-current` and the
 * underline carry which one is on, from one attribute, so nothing is lost by keeping it pressable.
 *
 * The `label` argument arrived with #659's second caller. Two scope switches on one site
 * announcing themselves identically would leave a screen-reader user unable to tell which view's
 * window they had just landed in, and hard-coding another view's name onto the orders section would be
 * worse than no name at all. (Activity was the first caller until Pulse replaced it.)
 *
 * @param {string} current
 * @param {(scope: string) => void} onScope
 * @param {string} [label]  how the control announces itself. Defaults to a generic wording.
 * @returns {HTMLElement}
 */
function scopeSwitch(current, onScope, label) {
  const wrap = el("nav", "scopes");
  wrap.setAttribute("aria-label", label || "Scope");
  wrap.append(el("span", "k", "scope"));
  for (const name of SCOPES) {
    const button = el("button", "scopekey", name);
    button.setAttribute("type", "button");
    button.setAttribute("data-focus", "scope:".concat(name));
    if (name === current) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => onScope(name));
    wrap.append(button);
  }
  return wrap;
}

/** `ACTIVITY_SCOPES`, in the order `t` cycles them. @type {string[]} */
const SCOPES = ["today", "7d", "all"];

/**
 * One section of a page that shows one section at a time.
 *
 * `label` is the tab's wording AND the section's own heading -- `build` is handed the entry it
 * was reached through so that it can write the second from the first. `blurb` is optional and is
 * the sentence a section makes about itself under that heading.
 *
 * @typedef {{key: string, label: string, build: Function, blurb?: string}} Section
 */

/**
 * A long page, cut into sections, with a tab bar to choose between them (#773).
 *
 * ── WHY THIS IS NOT A FILTER, AND WHAT FOLLOWS FROM THAT ─────────────────────────────────────
 *
 * `statusSwitch` and `scopeSwitch` above both re-ask the SERVER, because both narrow a collection
 * the server capped on the way out -- filtering a received page in the browser would present "the
 * rows that happened to arrive" as the whole answer. This control narrows NOTHING. `/api/setup`
 * sends every section of the setup page in one document and always did; choosing which of them
 * to draw is presentation, which is what this file is for, and no section is ever unreachable --
 * every one of them has a tab, always, so there is no filter here to be stuck inside.
 *
 * That is also why there is no "all" tab. The Orders bar needs one because a reader who has
 * clicked Canceled otherwise has no way back short of knowing that the empty string means every
 * status. Here, the way back to any section is that section's own tab, and an "all" tab would
 * re-create the page these two views were cut up to stop being.
 *
 * ── AN UNWIRED SWITCH RENDERS THE WHOLE PAGE ─────────────────────────────────────────────────
 *
 * `onSection` is optional, and a caller that omits it gets every section, in order, with no tab
 * bar -- the page exactly as it read before it was cut up. The alternative degradation, one
 * section and no way to reach the others, would be a client hiding information that is sitting
 * in a payload it already has. Same guard as `recentOrdersSection`'s `onStatus`, and the same
 * reason: a control nobody wired must not cost a reader anything.
 *
 * `current` is resolved against the table rather than trusted: a key that names no section --
 * a section renamed while a reader had its tab open -- falls back to the first, which is a page
 * that renders rather than a blank one.
 *
 * ── TWO PARAMETER NAMES THAT ARE LOAD-BEARING (#775) ─────────────────────────────────────────
 *
 * **`sections`, not `table`.** `table` is this module's own row renderer, three hundred lines
 * up. Shadowing it here breaks nothing today because neither function calls it -- and
 * `test_every_call_resolves_to_something_the_module_has` scopes parameters per function, so it
 * would go on passing while a `table(...)` added inside either one resolved to an array and
 * shipped a runtime `TypeError`. That test's own docstring records this class of miss from #701.
 *
 * **`payload`, not `data` -- HERE, and only here.** These two helpers sit between `holdingsView`
 * and `rulesView`, so `_view_keys` counts them inside `holdingsView`'s region: any
 * `data.<key>` written here would be checked against `/api/holdings`, which is not the endpoint
 * it came from. Naming the parameter something else makes that mistake unspellable.
 *
 * The same rename applied to `setupPaper`/`setupLive` was an own-goal and is reverted (#776
 * review): those two sit INSIDE `setupSection`'s region, where `data.` is exactly what the parity
 * pin scans for, so `payload` there would hide a wrong key rather than expose one. Proved with a
 * controlled pair -- `payload.made_up_key` in `setupPaper` escaped the suite; the identical
 * `data.made_up_key` in `setupSummary` was caught. The rule is regional, not global.
 *
 * @param {any} payload  the view's payload, passed through to whichever builder runs.
 * @param {Section[]} sections
 * @param {string} [current]  a `sections` key, or `""` for the first section.
 * @param {(key: string) => void} [onSection]
 * @param {string} [label]  how this page's tab bar announces itself.
 * @returns {DocumentFragment}
 */
function sectioned(payload, sections, current, onSection, label) {
  const fragment = document.createDocumentFragment();
  if (!onSection) {
    for (const entry of sections) fragment.append(entry.build(payload, entry));
    return fragment;
  }
  const chosen = sections.find(/** @param {Section} entry */ (entry) => entry.key === current);
  const here = chosen || sections[0];
  fragment.append(sectionSwitch(here.key, sections, onSection, label));
  fragment.append(here.build(payload, here));
  return fragment;
}

/**
 * The tab bar itself.
 *
 * Built from the same table the page draws from, so a tab that leads nowhere is not spellable.
 *
 * **Every tab stays a button, including the current one**, for `scopeSwitch`'s reason: pressing
 * the section you are on re-reads it, which is a refresh, and taking the control away is what
 * makes focus vanish when the view is rebuilt underneath a keyboard user who just pressed it.
 *
 * `data-focus` is how `main.js` puts focus back on the button's replacement -- pressing a tab
 * replaces the whole view, which destroys the button that was pressed, and without this a
 * keyboard user is returned to the top of the document on every press.
 *
 * `aria-label` is REQUIRED to differ per page. Two tab bars on one site announcing themselves
 * identically would leave a screen-reader user unable to tell which page's sections they had
 * landed in -- the lesson `scopeSwitch` learned at #659 when it grew a second caller.
 *
 * @param {string} current  the resolved section key -- never the raw request.
 * @param {Section[]} sections
 * @param {(key: string) => void} onSection
 * @param {string} [label]
 * @returns {HTMLElement}
 */
function sectionSwitch(current, sections, onSection, label) {
  const wrap = el("nav", "scopes");
  wrap.setAttribute("aria-label", label || "Section");
  wrap.append(el("span", "k", "section"));
  for (const entry of sections) {
    const button = el("button", "scopekey", entry.label);
    button.setAttribute("type", "button");
    button.setAttribute("data-focus", "section:".concat(entry.key));
    if (entry.key === current) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => onSection(entry.key));
    wrap.append(button);
  }
  return wrap;
}

/**
 * Rules: the rule ledger, each rule's track record, the DCA proposal and the research record --
 * one page for everything keel knows about its rules (console PR3 folded the Insights and
 * Research views in).
 *
 * **Read-only, and the page says so out loud.** `rules_payload`'s docstring: "promotion happens in
 * the CLI, behind the TTY gate, and nothing this API serves can change a rule's status."
 *
 * The lifecycle timestamps -- created, promoted, demoted -- are shown here and are not on the
 * rendered page, which shows id, kind, status and a Python `repr` of the params dict. The params
 * are a disclosure of `key = value` rows instead, because `_rule_row_payload` stringifies each
 * value individually and a `repr` of a dict is a thing only a Python programmer can read.
 *
 * In page order: the ledger (the one sortable table), the parameters, the track records with
 * their distance to the promotion gate (`recordsSection`, unsorted), the DCA proposal card
 * (`dcaPlanCard`), then the research record -- trials, evidence matrix, gauntlet, slippage --
 * each a section function of its own. Every reading after the ledger is SECONDARY: a read that
 * failed arrives as `null` and its section says so, leaving the rest of the page standing.
 *
 * @param {any} data  `/api/rules`'s `data`.
 * @param {any} sort
 * @param {(column: string, direction: string) => void} onSort
 * @param {any} [plan]  `/api/dca-plan`'s `data`, or `null` when that read failed.
 * @param {any} [planError]  that read's `error`, or `null`.
 * @param {any} [trials]    `/api/research/trials`'s `data`, or `null`.
 * @param {any} [gauntlet]  `/api/research/gauntlet`'s `data`, or `null`.
 * @param {any} [slippage]  `/api/research/slippage`'s `data`, or `null`.
 * @param {any} [matrix]    `/api/research/matrix`'s `data`, or `null`.
 * @returns {DocumentFragment}
 */
export function rulesView(data, sort, onSort, plan, planError, trials, gauntlet, slippage, matrix) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Rules"));
  fragment.append(el("p", "sub", "read-only · promotion happens in the CLI"));

  const rules = data.rules || [];
  fragment.append(heading("h-rules-all", "Every rule in the ledger"));
  fragment.append(
    table(
      "h-rules-all",
      [
        { label: "id", numeric: true, key: "id" },
        { label: "kind", numeric: false, key: "kind" },
        { label: "status", numeric: false, key: "status" },
        { label: "created (UTC)", numeric: false, key: "created_at" },
        { label: "promoted (UTC)", numeric: false, key: "promoted_at" },
        { label: "demoted (UTC)", numeric: false, key: "demoted_at" },
      ],
      rules.map(
        /** @param {any} row */ (row) => [
          plain(row.id),
          plain(row.kind),
          row.status,
          row.created_at,
          row.promoted_at,
          row.demoted_at,
        ],
      ),
      "No rules.",
      { sort: sort, onSort: onSort },
    ),
  );

  const parameterised = rules.filter(
    /** @param {any} row */ (row) => Object.keys(row.params || {}).length !== 0,
  );
  if (parameterised.length !== 0) {
    fragment.append(heading("h-params", "Parameters"));
    fragment.append(
      note("Operator-supplied and open-ended -- any rule kind may invent its own, so they cross the wire as strings."),
    );
    for (const row of parameterised) fragment.append(paramsCard(row));
  }
  fragment.append(recordsSection(data));
  fragment.append(dcaPlanCard(plan || null, planError || null));

  // The intro sentence is `trialsSection`'s, printed only once the trials were read: said here it
  // would claim a record the page could not read.
  fragment.append(heading("h-research", "Research record"));
  fragment.append(trialsSection(trials));
  fragment.append(matrixSection(matrix));
  fragment.append(gauntletSection(gauntlet));
  fragment.append(slippageSection(slippage));
  return fragment;
}

/**
 * Each rule's track record and its distance to the promotion gate (`/api/rules`'s `records`; the
 * Insights view's until console PR3).
 *
 * **No sort control, on any column.** `records` is not the route's sortable collection, and a
 * track record ordered by expectancy is the leaderboard the research record below refuses. The
 * records arrive in the service's order and are drawn in it.
 *
 * **What the records cover.** The promotion pipeline -- `candidate` and `paper` rules -- exactly
 * as `keel insights summary` reports by default; the empty sentence says that, because an empty
 * list means "no rule in the pipeline", which is not "no rule has traded". A live rule's record is
 * `keel insights summary --mode live` at a terminal.
 *
 * The gate cards are drawn on the PRESENCE of `gate`, which the payload sends only for a `paper`
 * rule (a candidate has not backtested yet; a live rule has already cleared it).
 *
 * @param {any} data  `/api/rules`'s `data`.
 * @returns {DocumentFragment}
 */
function recordsSection(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-records", "Rule track records"));

  const sub = el("p", "sub");
  sub.append(field(data.closed_trade_count), " closed trade(s) in the ledger");
  fragment.append(sub);

  const records = data.records || [];
  fragment.append(
    table(
      "h-records",
      [
        { label: "rule", numeric: false },
        { label: "status", numeric: false },
        { label: "class", numeric: false },
        { label: "trades", numeric: true },
        { label: "win rate", numeric: true },
        { label: "avg win", numeric: true },
        { label: "avg loss", numeric: true },
        { label: "realized rr", numeric: true },
        { label: "expectancy", numeric: true },
        { label: "profit factor", numeric: true },
        { label: "max dd", numeric: true },
        { label: "sample", numeric: false },
      ],
      records.map(
        /** @param {any} row */ (row) => [
          plain(row.rule_name),
          row.status,
          plain(row.promotion_class),
          row.n_trades,
          row.win_rate,
          row.avg_win,
          row.avg_loss,
          row.realized_rr,
          row.expectancy,
          row.profit_factor,
          row.max_drawdown,
          samplePill(row.significant),
        ],
      ),
      "No candidate or paper rules in the ledger, so no track record to show.",
    ),
  );
  fragment.append(
    note(
      "Rules in the promotion pipeline (candidate and paper). Below 30 closed trades a win rate is not yet distinguishable from random entry, which is why the sample column says so rather than leaving the number to speak for itself. A live rule's record is `keel insights summary --mode live` at a terminal.",
    ),
  );

  const gated = records.filter(/** @param {any} row */ (row) => Boolean(row.gate));
  if (gated.length !== 0) {
    fragment.append(heading("h-gates", "Distance to the promotion gate"));
    fragment.append(
      note("Only rules in paper have one: a candidate has not backtested yet, and a live rule has already cleared it."),
    );
    for (const [index, row] of gated.entries()) fragment.append(gateCard(row, index));
  }
  return fragment;
}

/**
 * The `n≥30` marker, as a pill carrying its own judgement.
 *
 * `significant` is a `flag` whose `off` wording is "below the n=30 floor" and whose `off_state` is
 * `warn` -- so the sentence AND the colour both come from `payload._track_record_payload`, and
 * this file decides neither.
 *
 * @param {any} significant
 * @returns {HTMLElement}
 */
function samplePill(significant) {
  return pill(plain(significant.display), toneOf(significant));
}

/**
 * One rule's distance to its promotion floor.
 *
 * Every figure here is a pair -- what the rule has, and what the floor is -- because a single
 * number answers "how is it doing" and only the pair answers "is it close". `_gate_payload` sends
 * both and computes neither: "`passing` is the engine's verdict, copied; the floors are the
 * config's own values, copied. Nothing here re-runs `check_floors`."
 *
 * The summary's id carries the card's POSITION as well as the rule's name. A record is named by
 * its rule KIND, and a ledger can hold several paper rules of one kind, so a name-only id repeats
 * -- and `rebuildInto` reopens disclosures by `summary[id]`, so opening one such card reopened
 * every card of that kind on the next poll.
 *
 * @param {any} rule
 * @param {number} position  the card's place among the gated records, for its id.
 * @returns {HTMLElement}
 */
function gateCard(rule, position) {
  const gate = rule.gate;
  const node = el("details", "gate");
  const summary = el("summary");
  // An id on every summary, and it is not decoration: `main.js` restores which disclosures were
  // open across a rebuild by matching these, so a reader who expanded one does not watch it shut
  // the moment the engine writes a row. Derived from the row it describes and its place in the
  // service's order, so it is stable across a re-read that did not change the ledger.
  summary.id = ["h-gatedist", plain(gate.rule_name), String(position)].join("-");
  summary.append(plain(gate.rule_name), " · ");
  summary.append(field(gate.passing));
  node.append(summary);

  node.append(
    gridCard([
      kv("class", plain(gate.promotion_class)),
      kv("trades", gate.n_trades),
      kv("trades needed", gate.min_trades),
      kv("trades remaining", gate.trades_remaining),
      kv("win rate", gate.win_rate),
      kv("win rate floor", gate.min_win_rate),
      kv("realized rr", gate.realized_rr),
      kv("rr floor", gate.min_rr),
      kv("expectancy", gate.expectancy),
      kv("expectancy floor", gate.min_expectancy),
    ]),
  );

  const reasons = stringList(gate.blocking_reasons, "reasons");
  if (reasons) {
    node.append(note("Blocked by:"));
    node.append(reasons);
  }
  return node;
}

/**
 * One rule's parameters, as a disclosure of `key = value` rows.
 *
 * @param {any} row
 * @returns {HTMLElement}
 */
function paramsCard(row) {
  const node = el("details", "params");
  const summary = el("summary");
  summary.id = "h-ruleparams-".concat(plain(row.id));
  summary.append([plain(row.kind), plain(row.id)].join(" · "));
  node.append(summary);

  const list = el("dl", "paramlist");
  for (const [name, value] of Object.entries(row.params || {})) {
    list.append(el("dt", undefined, name));
    list.append(el("dd", undefined, plain(value)));
  }
  node.append(list);
  return node;
}

/**
 * The trials ledger (#708 view 1): every experiment keel has run, rejected ones included. The
 * first section of the research record on the Rules view (console PR3 folded the Research view in;
 * this was `researchView`'s own body).
 *
 * ── NO SORT CONTROL, ANYWHERE IN THE RESEARCH RECORD ─────────────────────────────────────────
 *
 * Not one of these columns declares a `key`, and no section is handed a sort pair. That is the
 * Strathern rail, and it is deliberate to the point of being awkward.
 *
 * `headerCell` draws a clickable header for any column with a `key`, and `/api/research/trials`
 * declares an empty `sortable` -- so a key here would render a control the server answers with a
 * 400. But that is the small reason. The real one is in `keel/commands/research_record.py`: a research
 * record that can be ordered best-first is a leaderboard, and a leaderboard turns a record of
 * what was TRIED into an argument for what to TRADE. The rail is held in three places (service,
 * endpoint, view) because a reader who could sort this table would stop reading it as evidence.
 *
 * ── AND NO ACTION ────────────────────────────────────────────────────────────────────────────
 *
 * No promote, no re-run, no listener of any kind, pinned by
 * `tests/web/test_research_view.py::test_the_research_record_offers_no_action_of_any_kind`. A
 * record whose entire argument is that selection happened under a discipline must not put the
 * selection behind a control on the same screen.
 *
 * `null` when the read failed: on Rules every research read is SECONDARY, so a ledger that could
 * not be read leaves the rule ledger above it standing and says so here. The parameter is
 * `trials`, never `data` -- in the Rules region `data` is `/api/rules`, and the key scans in
 * `tests/web/test_client_assets.py` tell the two payloads apart by that name.
 *
 * @param {any} trials  `/api/research/trials`'s `data`, or `null` if that read failed.
 * @returns {DocumentFragment}
 */
function trialsSection(trials) {
  const fragment = document.createDocumentFragment();
  if (!trials) {
    fragment.append(el("p", "empty", "The research record could not be read."));
    return fragment;
  }

  const sub = el("p", "sub");
  sub.append(field(trials.generated_at), " · ");
  sub.append(field(trials.shown_count), " trials in the record");
  fragment.append(sub);

  fragment.append(
    note(
      "Every trial keel has run, in the order it ran them — the rejected ones beside the ".concat(
        "selected ones. A record showing only its selections would be a highlight reel.",
      ),
    ),
  );

  fragment.append(
    gridCard([
      kv("ledger", trials.ledger),
      kv("tamper check", trials.chain),
      kv("trials run", trials.trials_run),
      kv("decisions", trials.decisions),
    ]),
  );

  const breaks = stringList(trials.chain_errors, "breaks");
  if (breaks) {
    fragment.append(heading("h-breaks", "Rows that do not verify"));
    fragment.append(
      note(
        "Each line names the row whose recorded hash no longer matches its content. ".concat(
          "Every row after the first break is affected.",
        ),
      ),
    );
    fragment.append(breaks);
  }

  fragment.append(heading("h-explored", "What each rule has been tried on"));
  fragment.append(
    note(
      "Trials held in this ledger, against the cells the rule declares. Two numbers and not a "
        .concat("coverage figure: pricing a swept range against its declaration is a judgement ")
        .concat("the research driver makes by refusing, not one a page computes."),
    ),
  );
  fragment.append(
    table(
      "h-explored",
      [
        { label: "rule", numeric: false },
        { label: "trials here", numeric: true },
        { label: "cells declared", numeric: true },
      ],
      (trials.exploration || []).map(/** @param {any} row */ (row) => [
        plain(row.rule) || "—",
        row.trials,
        row.declared_cells,
      ]),
      "No trials in this ledger.",
    ),
  );

  fragment.append(heading("h-trials", "Every trial"));
  fragment.append(
    table(
      "h-trials",
      [
        { label: "when (UTC)", numeric: false },
        { label: "rule", numeric: false },
        { label: "kind", numeric: false },
        { label: "outcome", numeric: false },
        { label: "how the parameters were chosen", numeric: false },
        { label: "series", numeric: false },
        { label: "parameters", numeric: false },
        { label: "summary", numeric: false },
        { label: "row hash", numeric: false },
      ],
      (trials.rows || []).map(/** @param {any} row */ (row) => [
        row.at,
        plain(row.rule) || "—",
        plain(row.kind) || "—",
        row.decision,
        row.provenance,
        row.series,
        plain(row.params) || "—",
        plain(row.summary) || "—",
        row.row_hash,
      ]),
      // From the payload, never written here. There are two kinds of empty -- no ledger, and a
      // ledger with no trials in it -- and a hard-coded sentence said the first over both, which
      // is a false statement about a deployment that simply has not run anything yet. Choosing
      // between them is a judgement, and judgements are made in Python (Rule 2).
      plain(trials.empty_note),
    ),
  );

  return fragment;
}

/**
 * The Evidence Matrix (#708 view 2): recorded CSCV runs, never a computed one.
 *
 * **The empty state is the point of this section, and it is the state it ships in.** Building a
 * matrix costs 11.9-14.3 seconds per session on the real ledger and raises over the ledger as a
 * whole, on a page that re-polls every 15 seconds — so the console shows what an operator RAN and
 * hands them the command when they have not run one.
 *
 * That command comes off the payload and names a session that actually has columns.
 * `--session all` is the obvious thing to print and would filter to a session literally called
 * "all", find nothing, and refuse — teaching the reader that the page does not know what it is
 * talking about.
 *
 * ⛔ No sort control on any column. A matrix ordered by PBO is a leaderboard of overfitting
 * scores, and `cscv.py` forbids PBO as a ranking key in its own source.
 *
 * @param {any} matrix  `/api/research/matrix`'s `data`, or `null` if that read failed.
 * @returns {DocumentFragment}
 */
function matrixSection(matrix) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-matrix", "Evidence matrix"));
  if (!matrix) {
    fragment.append(el("p", "empty", "The evidence matrix could not be read."));
    return fragment;
  }

  const state = el("p", "sub");
  state.append(field(matrix.state));
  fragment.append(state);

  // The command, only where there is one to give: the payload sends an empty string once a run
  // has been recorded, and a page still telling an operator to run something they have run would
  // be reading its own table wrong.
  if (plain(matrix.invocation)) {
    fragment.append(el("pre", "invocation", plain(matrix.invocation)));
  }

  fragment.append(
    table(
      "h-matrix",
      [
        { label: "when (UTC)", numeric: false },
        { label: "session", numeric: false },
        { label: "columns", numeric: true },
        { label: "blocks", numeric: true },
        { label: "combinations", numeric: true },
        { label: "rows used", numeric: true },
        { label: "rows dropped", numeric: true },
        { label: "PBO", numeric: true },
        { label: "degradation slope", numeric: true },
        { label: "P[OOS < 0]", numeric: true },
        { label: "1st-order dominance", numeric: false },
        { label: "2nd-order dominance", numeric: false },
      ],
      (matrix.rows || []).map(/** @param {any} row */ (row) => [
        row.at,
        plain(row.session) || "—",
        row.n_columns,
        row.n_blocks,
        row.n_combinations,
        row.rows_used,
        row.rows_dropped,
        // PBO carries no state, deliberately: a high PBO beside a flat, positive OOS scatter is
        // the GOOD outcome, so a colour here would be a verdict the number does not support.
        row.pbo,
        row.degradation_slope,
        row.prob_loss,
        row.dominance_1st,
        row.dominance_2nd,
      ]),
      // From the payload: "no ledger", "no run yet" and "no session with columns" are three
      // different sentences and choosing between them is a judgement (Rule 2).
      plain(matrix.state.display),
    ),
  );
  return fragment;
}


/**
 * The Promotion Gauntlet scorecard (#708, view 3) -- as RECORDED, never as computed.
 *
 * ── WHY THIS SECTION COMPUTES NOTHING ────────────────────────────────────────────────────────
 *
 * Every part of the gauntlet needs work a page cannot do, measured before this was written: DSR
 * needs a `--sharpe` the ledger does not store, CSCV costs 12-14 s per session and raises over
 * the ledger as a whole, and Monte Carlo runs a backtest. `keel/commands/gauntlet.py` carries the
 * figures; #726 is the engine issue for persisting the distributions.
 *
 * So a gauntlet run is an IMMUTABLE HISTORICAL ARTIFACT here. What is on this table is what an
 * operator's run wrote into the append-only ledger, seed included, at the moment they ran it --
 * not a fresh answer computed for this page load, and not one that changes between polls.
 *
 * ── THREE STATES ─────────────────────────────────────────────────────────────────────────────
 *
 * Ran, attempted-and-refused, and never run. The third is a COUNT rather than eighty-seven empty
 * rows: a table of blanks would present the gauntlet as having covered the whole record.
 *
 * @param {any} gauntlet  the `/api/research/gauntlet` payload, or `null`.
 * @returns {DocumentFragment}
 */
function gauntletSection(gauntlet) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-gauntlet", "The promotion gauntlet, as it was run"));

  if (!gauntlet) {
    fragment.append(note("The gauntlet record could not be read for this deployment."));
    return fragment;
  }

  fragment.append(
    note(
      "Recorded outcomes, not fresh calculations. Each row is what an operator's run wrote ".concat(
        "into the append-only ledger at the time — seed included, so it can be repeated.",
      ),
    ),
  );

  fragment.append(
    gridCard([
      kv("trials in the record", gauntlet.trials_total),
      kv("gauntlet attempted", gauntlet.recorded_count),
      kv("gauntlet ran", gauntlet.available_count),
      kv("no gauntlet record", gauntlet.not_run_count),
      kv("passed the gate", gauntlet.gate_passed_count),
    ]),
  );

  fragment.append(
    table(
      "h-gauntlet",
      [
        { label: "when (UTC)", numeric: false },
        { label: "trial", numeric: false },
        { label: "rule", numeric: false },
        // A PBO is only defined WITHIN a session -- `build_matrix` requires synchronous
        // columns -- so the session is context, not decoration.
        { label: "session", numeric: false },
        { label: "ran", numeric: false },
        { label: "PBO", numeric: true },
        { label: "train expectancy", numeric: true },
        { label: "held out", numeric: true },
        { label: "bars", numeric: true },
        { label: "seed", numeric: true },
        { label: "gate", numeric: false },
      ],
      (gauntlet.rows || []).map(/** @param {any} row */ (row) => [
        row.at,
        plain(row.trial_id) || "—",
        plain(row.rule) || "—",
        plain(row.session) || "—",
        row.ran,
        row.pbo,
        row.train_expectancy,
        row.held_out_expectancy,
        row.bars,
        row.seed,
        row.gate_passed,
      ]),
      "No gauntlet has been run against this ledger.",
    ),
  );

  fragment.append(
    note(
      "Read PBO beside the degradation slope, never alone: a high PBO over a flat, positive ".concat(
        "out-of-sample scatter is the good outcome. The slope is not recorded yet — see #726.",
      ),
    ),
  );

  return fragment;
}


/**
 * The Slippage Universe (#708, view 4), as a section of the research record on the Rules view.
 *
 * A SECTION and not a route of its own: one client route whose endpoints are read concurrently
 * and rendered under one page, with no sub-router that `staticfiles.CLIENT_ROUTES` would then
 * have to learn about.
 *
 * `null` when the read failed -- `mount` leaves the primary view standing when a SECONDARY
 * endpoint errors, so this says the section is missing rather than taking the rule ledger down
 * with it.
 *
 * ── EVERY NUMBER IN HERE IS AN ASSUMPTION ────────────────────────────────────────────────────
 *
 * The `basis` field says so and is rendered first, before any rate. keel stores no order books
 * and no realised spreads; these figures are scaled from cached candle volume. #708's own scope
 * note calls them "measured", which is the mistake this section is written not to repeat.
 *
 * @param {any} slippage  the `/api/research/slippage` payload, or `null`.
 * @returns {DocumentFragment}
 */
function slippageSection(slippage) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-slippage", "What a fill is assumed to cost"));

  if (!slippage) {
    fragment.append(note("The slippage figures could not be read for this deployment."));
    return fragment;
  }

  const basis = el("p", "note");
  basis.append(field(slippage.basis));
  fragment.append(basis);

  fragment.append(
    gridCard([
      kv("products", slippage.product_count),
      kv("priced from cached candles", slippage.priced_count),
      kv("reaching the floor", slippage.at_floor_count),
      kv("at the cap", slippage.capped_count),
      kv("no liquidity statistic", slippage.fallback_count),
    ]),
  );

  fragment.append(
    note(
      "Every experiment document in this repository prices its fills at the floor rate. ".concat(
        "The count above is how many products actually reach it.",
      ),
    ),
  );

  fragment.append(
    gridCard([
      kv("floor", slippage.floor_bp),
      kv("cap", slippage.cap_bp),
      kv("anchor volume", slippage.anchor_quote_volume),
    ]),
  );

  fragment.append(
    table(
      "h-slippage",
      [
        { label: "product", numeric: false },
        { label: "assumed bp per leg", numeric: true },
        { label: "vs floor", numeric: true },
        { label: "median daily quote volume", numeric: true },
        { label: "daily bars", numeric: true },
        { label: "priced from", numeric: false },
        { label: "cap", numeric: false },
      ],
      (slippage.rows || []).map(/** @param {any} row */ (row) => [
        plain(row.product_id) || "—",
        row.slippage_bp,
        row.floor_multiple,
        row.median_daily_quote_volume,
        row.bars,
        row.fallback,
        row.capped,
      ]),
      // Not "an empty allowlist": `config._parse_allowlist` raises on a missing or empty one,
      // so that sentence would describe a deployment which cannot load. The reachable emptiness
      // is a table whose rows the report could not build.
      "No products to price.",
    ),
  );

  return fragment;
}

/**
 * The DCA proposal (`/api/dca-plan`), as a card under the rules ledger.
 *
 * **Read-only, and structurally so.** There is no control on it: it builds no button, anchor or
 * field to type in, and its one `table()` is drawn without a sort pair. The only way to apply the
 * plan is the CLI command it shows, run at a terminal, where `[Y]` writes `candidate` rules. The
 * browser performs no capability increase (`keel/capabilities.py`).
 *
 * Its inputs come from this page's own address (`/rules?budget=500&buffer=0.1`); `main.js`
 * copies them into the endpoint's query. Without them the payload's `awaiting_budget` state says
 * what to add. A refused read (a malformed budget, a config the service refuses) leaves the rules
 * ledger standing and places the server's reason here, in the service's own words.
 *
 * "checked against the cap" is #847's figure: the worst UTC calendar month for the plan's
 * cadence, which is what rail 14's cap was actually compared with -- the same sentence the CLI
 * prints, written once by `dca_plan.worst_month_text`.
 *
 * @param {any} plan   `/api/dca-plan`'s `data`, or `null` when that read failed.
 * @param {any} error  that read's `error`, or `null`.
 * @returns {HTMLElement}
 */
export function dcaPlanCard(plan, error) {
  const card = el("section", "card dca-plan");
  card.append(heading("h-dca-plan", "DCA plan proposal"));
  if (!plan) {
    card.append(el("p", "note", "The DCA proposal could not be read."));
    if (error) card.append(el("p", "note bad", plain(error.detail)));
    return card;
  }
  const stateLine = el("p");
  stateLine.append(field(plan.state));
  card.append(stateLine);
  // #856: the admission screen behind this plan is cached for up to 5 minutes, so this figure
  // can lag a fix (a re-fetched candle, a fresh attestation) by that long -- said here, in the
  // server's own words, rather than left for the operator to notice on their own.
  const screenAgeLine = el("p", "note");
  screenAgeLine.append(field(plan.screen_age));
  card.append(screenAgeLine);
  card.append(
    dcaPlanFigure("spend / month", plan.summary.spend),
    dcaPlanFigure("budget", plan.summary.budget),
    dcaPlanFigure("held back", plan.summary.buffer),
    dcaPlanFigure("rail 14 monthly buy cap", plan.summary.cap),
    dcaPlanFigure("checked against the cap", plan.cap_check),
    dcaPlanFigure("planned / month", plan.summary.planned),
    dcaPlanFigure("est. fees / month", plan.summary.fees),
    dcaPlanFigure("fee rate", plan.fee_rate),
    dcaPlanFigure("assets", plan.buy_count),
  );
  card.append(el("p", "note", plain(plan.cap_note)));
  card.append(dcaPlanRows(plan));
  card.append(
    dcaPlanList(
      "Excluded",
      (plan.excluded || []).map(
        /** @param {any} item */ (item) =>
          [plain(item.asset), plain(item.reasons), plain(item.detail)]
            .filter((part) => part !== "")
            .join(" — "),
      ),
      "Nothing excluded.",
    ),
  );
  card.append(
    dcaPlanList(
      "Existing DCA rules (unchanged)",
      (plan.existing || []).map(
        /** @param {any} rule */ (rule) => {
          const line = el("span");
          line.append(
            ["rule", plain(rule.rule_id), plain(rule.product_id), plain(rule.status)].join(" "),
            " · ",
            field(rule.per_buy),
            " ",
            field(rule.cadence),
          );
          // The CLI's `(dip_bonus_pct X)` marker: this rule's buys can exceed `per_buy`.
          const bonus = plain(rule.dip_bonus);
          if (bonus !== "") line.append(" (", bonus, ")");
          return line;
        },
      ),
      "None.",
    ),
  );
  card.append(dcaPlanList("Cannot approve", plan.blockers || [], "Nothing blocks it."));
  card.append(dcaPlanList("Notes", plan.warnings || [], "None."));
  card.append(el("h3", undefined, "To apply, at a terminal:"));
  card.append(el("code", "dca-command", plain(plan.command)));
  return card;
}

/**
 * One label and one `Field`. Deliberately NOT `kv()`: `kv` turns a label naming a documented term
 * into an outbound link (#539), and this card builds no anchor at all.
 *
 * @param {string} label
 * @param {any} value
 * @returns {HTMLElement}
 */
function dcaPlanFigure(label, value) {
  const wrap = el("div", "kv");
  const key = el("span", "k", label);
  const holder = el("span", "v");
  holder.append(field(value));
  wrap.append(key, holder);
  return wrap;
}

/**
 * The per-asset schedule, one row per planned buy, in the order the service planned them.
 *
 * @param {any} plan
 * @returns {HTMLElement}
 */
function dcaPlanRows(plan) {
  return table(
    "h-dca-plan",
    [
      { label: "asset", numeric: false },
      { label: "weight", numeric: true },
      { label: "cadence", numeric: false },
      { label: "per buy", numeric: true },
      { label: "monthly", numeric: true },
      { label: "est. fee", numeric: true },
      { label: "venue minimum", numeric: false },
    ],
    (plan.buys || []).map(
      /** @param {any} row */ (row) => [
        plain(row.asset),
        row.weight,
        row.cadence,
        row.per_buy,
        row.monthly,
        row.fee,
        row.min_order,
      ],
    ),
    "No buys planned.",
  );
}

/**
 * A small heading over a bullet list -- the card's four lists. Each item is a string the server
 * wrote or a node already built from one; either is appended as it is.
 *
 * @param {string} title
 * @param {any[]} items
 * @param {string} empty
 * @returns {HTMLElement}
 */
function dcaPlanList(title, items, empty) {
  const wrap = el("div");
  wrap.append(el("h3", undefined, title));
  if (items.length === 0) {
    wrap.append(el("p", "empty", empty));
    return wrap;
  }
  const list = el("ul");
  for (const item of items) {
    const entry = el("li");
    entry.append(item);
    list.append(entry);
  }
  wrap.append(list);
  return wrap;
}

/**
 * Venue readiness, the last section of Audit & Setup: `/api/venues`'s `readiness` collection --
 * can a live entry actually be placed on THIS deployment, right now (#233, rail 20).
 *
 * **Readiness only.** `/api/venues` still carries each installed adapter's capability
 * declarations (planner ruling 16 kept the endpoint whole), but what an adapter DECLARES is
 * `keel brokers list`'s job; the question this page answers is whether the deployment can trade.
 * `payload._readiness_payload` keeps the two collections apart for the same reason.
 *
 * @param {any} data  `/api/venues`'s `data`, or `null` when that read failed.
 * @returns {DocumentFragment}
 */
export function readinessSection(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(heading("h-readiness", "Venue readiness"));
  if (data === null) {
    fragment.append(el("p", "empty", "Venue readiness could not be read."));
    return fragment;
  }
  const sub = el("p", "sub");
  sub.append(
    "can a live entry actually be placed on this deployment, right now — ",
    "not what the adapter merely declares",
  );
  fragment.append(sub);
  fragment.append(
    table(
      "h-readiness",
      [
        { label: "venue", numeric: false },
        { label: "state", numeric: false },
        { label: "explanation", numeric: false },
        { label: "fix", numeric: false },
      ],
      (data.readiness || []).map(
        /** @param {any} row */ (row) => [
          plain(row.venue),
          field(row.state),
          plain(row.explanation),
          plain(row.next_step) || "—",
        ],
      ),
      "No readiness rows.",
    ),
  );
  return fragment;
}

/**
 * The emphasis each mode word carries in the badge. See `modeBadge` below for why paper is the
 * quiet one and why there is no `state` anywhere in this table.
 *
 * @type {Record<string, string>}
 */
const MODE_CLASS = {
  paper: "mode-paper",
  confirm: "mode-confirm",
  live: "mode-live",
};

/**
 * The header's mode badge, from `/api/config` (#597).
 *
 * **A readout, not a control.** The word placed here is the served deployment's own
 * `auto_trade.mode` (`paper` or `confirm` today), copied verbatim: changing it is a config-file
 * edit plus a terminal action by design, and there is no control on this page and no route
 * behind one. What the badge answers is the question the buried status row never could -- "is
 * THIS browser looking at the paper deployment or the live one?" -- and the tooltip names the
 * db and config in play so the answer is checkable rather than trusted.
 *
 * `mode` crosses the wire as a bare string, not a `Field`, and this function honours that: no
 * `state`, no judgement, because `confirm` beside `paper` is neither good nor bad. What differs
 * is EMPHASIS, and the table above is the whole of it -- paper is the quiet one (muted, like any
 * label), confirm and live carry the accent, because a deployment that can place orders should
 * sit up in the header without being graded by it. `live` is in the table for the day the
 * config vocabulary grows it; an unknown word renders UNGRADED but still renders, which is the
 * same forward-compatibility `STATE_CLASS` gives a server one version ahead of this page.
 *
 * No mode in the document (a first run, or a config that could not be read) empties the node,
 * and `keel.css` hides it while empty: an absent answer is not `paper`, and guessing a mode on
 * a trading console is the one thing this badge must never do.
 *
 * @param {HTMLElement} node
 * @param {any} config  `/api/config`'s `data`, or `null`.
 */
export function modeBadge(node, config) {
  const mode = config && typeof config.mode === "string" ? config.mode : "";
  if (!mode) {
    node.className = "pill mode";
    node.removeAttribute("title");
    node.replaceChildren();
    return;
  }
  node.className = "pill mode ".concat(MODE_CLASS[mode] || "");
  node.replaceChildren(document.createTextNode(mode));
  // The "where am I" half: one process serves one --db/--config pair, and naming both makes
  // paper-vs-live confusion answerable at a glance instead of by asking the terminal.
  node.title = (config.db_path || "").concat(" · config ", config.config_path || "");
}

//: What the card says where a control would be. Static copy, not payload prose: `paperBanner`'s
//: sentence arrives from Python because that function CHOOSES between sentences on the basis of a
//: config, which is a judgement. This one is the same words on every deployment, so it is UI copy
//: in the class of a view's subtitle, and Rule 2 has nothing to say about it.
//: The switcher's closed state: an instruction, never a console's name, so the header names this
//: console in exactly one place -- the chip.
const SWITCHER_PLACEHOLDER = "switch console\u2026";

const SWITCHING_NOTE = "Mode and profile switching are terminal ceremonies with a runbook. ".concat(
  "This console reports the deployment it was pointed at and cannot change it.",
);

/**
 * The deployment identity card (#755) -- the `title` tooltip's contents, reachable by touch.
 *
 * `modeBadge` writes `--db` and `--config` into a `title`, and `title` is a HOVER tooltip. There
 * is no hover on a phone, which is the device #648/#656 exist for, so the half of the badge that
 * actually IDENTIFIES a deployment was unreachable exactly where the terminal is least available.
 * The mode word alone identifies nothing: two paper deployments both say `paper`, and a live
 * sandbox and a paper-equities profile can both say `confirm`.
 *
 * **It reveals and it does not offer.** The disclosure is `<details>` in the shell, so there is no
 * handler to bind and no button to build, and this function is held to the same
 * no-interactive-node test as `sessionChip` and `paperBanner` rather than exempted from it. A
 * "switch to live" affordance arriving through a door marked accessibility is still the growth
 * funnel the chip's docstring refuses.
 *
 * The origin comes from `window.location`, not the payload -- it is a fact about where this
 * browser is, and in an installed app there is no address bar to read it from.
 * `timelineSection`'s CSV link takes it from the same place for the same reason.
 *
 * Empty when the config could not be read, matching `modeBadge`: an absent answer is not `paper`,
 * and a card naming a deployment nothing could identify would be inventing one.
 *
 * @param {HTMLElement} node
 * @param {any} config  `/api/config`'s `data`, or `null`.
 */
export function deploymentCard(node, config) {
  const mode = config && typeof config.mode === "string" ? config.mode : "";
  if (!mode) {
    node.replaceChildren();
    return;
  }
  const rows = [
    kv("deployment", config.profile || "\u2014"),
    kv("mode", mode),
    kv("config", config.config_path || "\u2014"),
    kv("database", config.db_path || "\u2014"),
    kv("origin", window.location.origin),
  ];
  // The consoles running beside this one (#814), composed by `payload.profile_switcher`; empty,
  // and so absent, when there are none.
  if (config.switcher && config.switcher.display) {
    rows.push(kv("running consoles", config.switcher.display));
  }
  node.replaceChildren(
    gridCard(rows),
    el("p", "muted", SWITCHING_NOTE),
  );
}

/**
 * The session chip's two halves, from `/api/config` (#704).
 *
 * **The spine, without what usually rides on it.** Making paper-vs-live visible on every page
 * rather than buried in settings is the one organizing idea worth taking from a retail broker
 * console. What their version attaches -- an "Open Live Account" button on the paper banner --
 * is a growth funnel wrapped around real money, and is refused here and in `paperBanner` below.
 *
 * **Display and navigation only. It never mutates.** Switching profile or mode is a config-file
 * edit plus a typed terminal ceremony with a runbook; there is no control here, no route behind
 * one, and no capability added. The chip surrounds the existing `modeBadge` node rather than
 * replacing it, so the badge keeps owning the mode word and its db/config tooltip -- three facts
 * reading `profile · mode · equity state`, each written by the one function that knows it.
 *
 * The two halves are filled separately because they are separately absent: a profile is always
 * knowable (it is the database's filename), while the equity state can be genuinely unrecorded
 * on a deployment that has never flipped modes. `equity_state` is a `Field` carrying that
 * distinction as a state, so `field()` places the unknown reading without this function deciding
 * anything about it.
 *
 * @param {HTMLElement} profileNode
 * @param {HTMLElement} equityNode
 * @param {any} config  `/api/config`'s `data`, or `null`.
 */
export function sessionChip(profileNode, equityNode, config) {
  const profile = config && typeof config.profile === "string" ? config.profile : "";
  if (profile) {
    profileNode.replaceChildren(document.createTextNode(profile));
  } else {
    // Empty rather than a placeholder, and `keel.css` hides it while empty -- the same rule the
    // mode badge follows. A deployment with no name is not a deployment called "unknown".
    profileNode.replaceChildren();
  }
  if (config && config.equity_state) {
    equityNode.replaceChildren(field(config.equity_state));
  } else {
    equityNode.replaceChildren();
  }
}

/**
 * The console switcher (#814): the OTHER keel consoles serving from this deployment root, as a
 * `<select>` beside the session chip -- never inside it.
 *
 * **Beside, because the chip may not be a control.** #704 refused the retail pattern of a chip
 * that is also a dropdown (`test_neither_the_chip_nor_the_banner_builds_anything_clickable`), and
 * #815 put one there anyway. The chip keeps naming THIS console in plain text; nothing here can
 * make it name another.
 *
 * **Navigation only.** Each choice's `href` is `/switch/<port>` on this console, which redirects
 * into that console's own session hand-off. It starts, stops and changes nothing -- switching a
 * deployment's profile or mode is still the terminal ceremony it was -- and it never holds a
 * token (see `payload.profile_switcher`). The first option is a disabled placeholder, so no
 * console is ever shown as selected and choosing any real one is a `change`.
 *
 * `switchable` decides whether it appears at all, and is stated by the payload rather than read
 * off a list's length. Built, then filled: `el` has no children form, and #815's
 * `el("select", ..., ...options)` rendered an empty select.
 *
 * @param {HTMLElement} node
 * @param {any} config  `/api/config`'s `data`, or `null`.
 */
export function consoleSwitcher(node, config) {
  if (!(config && config.switcher && config.switcher.switchable)) {
    node.replaceChildren();
    return;
  }
  const select = el("select", "console-select");
  select.setAttribute("aria-label", "Open another running keel console");
  const placeholder = el("option", undefined, SWITCHER_PLACEHOLDER);
  placeholder.setAttribute("value", "");
  placeholder.setAttribute("disabled", "disabled");
  placeholder.setAttribute("selected", "selected");
  select.append(placeholder);
  for (const choice of config.switcher.choices) {
    const option = el("option", undefined, plain(choice.label));
    option.setAttribute("value", plain(choice.href));
    select.append(option);
  }
  node.replaceChildren(select);
}

/**
 * The persistent mode banner (#704).
 *
 * **Zero buttons, zero links, forever.** In paper it states that no real money is involved; in
 * confirm it restates the mode/equity-state pairing the operator is about to verify against the
 * venue's own UI. It never offers a way to go live, because going live is a ceremony with a
 * runbook that this console may explain and must never funnel toward.
 *
 * The SENTENCE comes from the payload (`payload._session_banner`) and is placed here unread:
 * choosing between two sentences on the basis of what a config says is a judgement, and Rule 2
 * keeps judgements in Python. This function decides only the emphasis, from the same
 * `MODE_CLASS` table the badge uses -- so the banner and the badge cannot disagree about which
 * mode is the quiet one.
 *
 * An empty banner means the config could not be read, and the node empties: an absent answer is
 * not `paper`, and a banner is a claim about whether real money is involved, which has no safe
 * default.
 *
 * @param {HTMLElement} node
 * @param {any} config  `/api/config`'s `data`, or `null`.
 */
export function paperBanner(node, config) {
  const text = config && typeof config.banner === "string" ? config.banner : "";
  const mode = config && typeof config.mode === "string" ? config.mode : "";
  if (!text) {
    node.className = "modebanner";
    node.replaceChildren();
    return;
  }
  node.className = "modebanner ".concat(MODE_CLASS[mode] || "");
  node.replaceChildren(document.createTextNode(text));
}

/**
 * The attestation banner (#793) -- what is about to halt keel, or has already.
 *
 * Rail 17 expired on 2026-09-08 and live placed nothing for three days. The state was on the
 * Status page (now Pulse) the whole time, in a card, below the fold, on what was then one page
 * of eleven. So this is carried by the ENVELOPE and drawn from the shell, above `#view`: it is
 * on whatever page the operator is actually reading.
 *
 * **Renders `alerts` and judges nothing.** The server sends only the rows that need someone
 * (`payload.attestation_alerts`), already toned, so "is there a banner" is answered in one place
 * rather than here and in `keel doctor` separately -- which is the divergence this whole feature
 * exists to close.
 *
 * `null` (no deployment to read) and `[]` (nothing wrong) both draw nothing, and they must:
 * `envelope` sends `null` rather than `[]` precisely so a response that read nothing cannot be
 * mistaken for one that checked and found all clear, and a client that renders "all clear" for
 * either would throw that distinction away.
 *
 * **No `aria-live`, deliberately.** This page keeps exactly one live region (see the note beside
 * `#engine` in `index.html`) and it is spent on the engine banner. The trade is real: a screen
 * reader is not interrupted when an attestation lapses. It is the same trade `#mode-banner`
 * already makes for the statement "this is real money", and the alternative here is worse --
 * the shell repaints every 15 seconds, so an alert region would announce the same halt four
 * times a minute for as long as it lasts.
 *
 * @param {HTMLElement} node
 * @param {any} alerts  the envelope's `attestations`: rows, `[]`, or `null`.
 */
export function attestationBanner(node, alerts) {
  const rows = Array.isArray(alerts) ? alerts : [];
  if (rows.length === 0) {
    node.className = "attestbanner";
    node.replaceChildren();
    return;
  }
  // The banner's own tone is the worst tone in it: one expired attestation among three
  // approaching ones is an outage, and styling the whole strip as a warning would file it under
  // "soon" when entries are already being refused.
  let tone = "warn";
  for (const row of rows) {
    const state = row.state && typeof row.state.state === "string" ? row.state.state : "";
    if (state === "bad") tone = "bad";
  }
  node.className = "attestbanner ".concat(STATE_CLASS[tone] || "");
  node.replaceChildren(...rows.map(attestationLine));
}

/**
 * One row of the banner: which rail, what it is, what state, and the command that fixes it.
 *
 * The remedy is a `<code>` and not a link because there is nothing to link to -- rails 17 and 22
 * are attested from a TTY, and #781 deliberately left them outside the browser gate. A banner
 * that says "expired" without the words to type is a dead end.
 *
 * @param {any} alert
 * @returns {HTMLElement}
 */
function attestationLine(alert) {
  const line = el("p", "attestline");
  line.append(el("span", "rail", "Rail ".concat(plain(alert.rail))));
  line.append(el("span", "what", plain(alert.label)));
  line.append(field(alert.state));

  // ONE instant, with the words the server chose for it -- "expires in 1h", "expired
  // 2026-09-08", "never attested". There is no branch here on purpose: picking between a
  // countdown and a date would have to read the state off `Field.value`, which this file does
  // not do, or off the rendered text, which is worse.
  line.append(el("span", "left", plain(alert.when_label)));
  line.append(field(alert.when));

  line.append(el("code", "remedy", plain(alert.remedy)));
  return line;
}

/**
 * The footer's build line, from `/api/config`.
 *
 * `reproducible` is the one judged field on that payload, and `payload.config_payload` calls it
 * "keel's central honesty signal": `False` means the running code corresponds to no commit. It
 * is placed with its state class like any other field, so a non-reproducible build is visible on
 * every page rather than discoverable in a CLI subcommand.
 *
 * @param {HTMLElement} node
 * @param {any} data  `/api/config`'s `data`, or `null`.
 */
export function buildLine(node, data) {
  if (!data) {
    node.replaceChildren();
    return;
  }
  const parts = [el("span", undefined, plain(data.build) || plain(data.version) || "unknown build")];
  parts.push(el("span", "muted", " · "));
  parts.push(field(data.reproducible));
  node.replaceChildren(...parts);
}
