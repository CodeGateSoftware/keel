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

import { equityChart, equitySeriesChart } from "./chart.js";
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
  // name (`rule_name` is a column of both `/api/insights` and `/api/journal`).
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
    // first-run path. The link goes to the CLIENT's own setup view now that #537 has built one:
    // `/api/setup` is declared `needs_database=False` precisely so the checklist answers on a
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

  const form = el("form");
  const label = el("label", undefined, "Paste the address keel printed, or just its token");
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
 * The status view: `/api/status`'s payload, in the order `/` renders it today.
 *
 * ── PARITY, AND THE FIVE PLACES IT IS DELIBERATELY NOT BYTE-FOR-BYTE ─────────────────────────
 *
 * The acceptance criterion is parity with what `/` renders today. Measured against the running
 * code rather than read off the source, today's `render_status` reaches for five attributes that
 * do not exist on the report dataclasses, through `getattr(..., default)` calls that swallow the
 * mismatch, so the page renders a wrong or blank value with no error anywhere:
 *
 *   `autonomy.enabled`      -- `AutonomyStatus` has `live` / `autonomous`. The page prints "off"
 *                              for a deployment placing orders unattended.
 *   `attestation.expired`   -- `WithdrawalAttestationStatus` has `state`. The page prints
 *                              "fresh", in green, for an EXPIRED rail-17 attestation, which is a
 *                              state that halts live entries.
 *   `position.entry_fill`   -- the field is `entry_price`. Every entry price renders `--`.
 *   `rule.name`             -- `RuleSummary` has `id` / `kind` / `status`. The column is blank.
 *   `subscription.status`,
 *   `subscription.attested_ts` -- the fields are `stored_status` / `effective_status`; there is
 *                              no attested timestamp on the row at all. Both columns are blank.
 *
 * This view shows the values the API sends, which are the correct ones. Parity is on the
 * INFORMATION -- the same sections, in the same order, answering the same questions -- and not
 * on the five wrong outputs. Reproducing them would mean writing code whose only purpose is to
 * be wrong in the same way, and the fix belongs in `render.py` (or arrives free when #540
 * deletes it).
 *
 * Two things this view adds that `/` does not show at all: the market session card, because
 * `MarketSessionStatus` exists to "name the state that gates the cycle, on its own line, before
 * an operator has to wonder why nothing trades", and the `bracket` column on open positions,
 * because an unbracketed position is a position with no stop.
 *
 * @param {any} data  `/api/status`'s `data`, known non-null by the caller.
 * @returns {DocumentFragment}
 */
export function statusView(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Status"));

  const sub = el("p", "sub");
  sub.append(field(data.generated_at));
  fragment.append(sub);

  fragment.append(
    gridCard([
      kv("mode", plain(data.mode)),
      kv("kill switch", data.kill_switch),
      kv("autonomy", data.autonomy.live),
      kv("autonomy configured", data.autonomy.configured),
      kv("autonomy lapses", data.autonomy.lapses_at),
      kv("rail 11", data.drawdown.rail11),
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

  fragment.append(heading("h-rules", "Rules"));
  const counts = el("p", "note");
  if (data.rule_counts.length === 0) {
    counts.textContent = "no rules";
  } else {
    // `entries()` again, for the separator: index 0 gets none, every later entry gets one.
    // The order is the server's -- `payload.status_payload` emits `rule_counts` as a LIST of
    // pairs rather than an object precisely so that ordering stays a presentation decision made
    // in Python, matching `render_human`'s own sort.
    for (const [index, entry] of data.rule_counts.entries()) {
      if (index) counts.append(el("span", "muted", " · "));
      counts.append(plain(entry.status), " ");
      counts.append(field(entry.count));
    }
  }
  fragment.append(counts);
  fragment.append(
    table(
      "h-rules",
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

// -- the remaining six views (#537) ---------------------------------------------------------------
//
// ── PARITY IS ON THE INFORMATION, NEVER ON THE MARKUP ──────────────────────────────────────────
//
// Each view below is judged against two records, and neither of them is the HTML's source:
// `keel/web/render.py`'s rendered pages, which are what an operator sees today, and
// `tests/commands/test_tui.py`, which is the specification of what the curses screens show and is
// what #541 deletes against. Where those two disagree, or where one of them is WRONG, the
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
 * The setup checklist: `/api/setup`'s payload, in RUNBOOK order.
 *
 * ── THIS VIEW READS. THE WRITE SURFACE IS UNCHANGED AND STAYS WHERE IT IS ────────────────────
 *
 * The rendered `/setup` page carries a `<form method=post action="/setup/{key}">` per MECHANICAL
 * step. This view carries none, and that is neither a hidden button nor a gate:
 *
 *   * **`/api/setup` ships no CSRF token, deliberately.** `payload.setup_payload`'s docstring is
 *     explicit -- minting a live write credential into a GET response "would put it into every
 *     cached copy, every proxy log and every paste of 'here is what the API returned'". A client
 *     that cannot obtain the token cannot submit the form, and inventing a way to hand it one
 *     would be a change to the write surface, which this issue may not make.
 *
 *   * **A client that hides a button is not a gate**, so nothing is hidden: every action keel
 *     offers is listed, with its title, its detail, and the inputs it would ask for. Only the
 *     button is elsewhere -- on the page that holds the token. Refusal stays the server's job
 *     regardless: `server.do_POST` routes only through `keel.commands.setup.ACTIONS` and would
 *     refuse anything else whatever any front-end chose to draw.
 *
 * **The gap that leaves is real and belongs to the milestone rather than to this file.** #540
 * deletes the rendered `/setup`, and on that day the link below has nowhere to point. Closing it
 * needs a decision nobody has taken -- where a browser client gets a write token from -- and
 * taking it here, by adding a POST under `/api/*`, is precisely what this issue forbids.
 *
 * ── DIVERGENCE FROM THE RENDERED PAGE ────────────────────────────────────────────────────────
 *
 * The kind note for an `operator_input` step is BLANK on the rendered page: `_STEP_KIND_NOTE` has
 * three entries against `StepKind`'s four, and the missing one is the kind of the real
 * `credentials` step. `payload._STEP_KIND_NOTES` now carries all four and this view shows what it
 * is sent, so the step that asks for a credential explains itself here and does not there. Same
 * shape as #548, found while porting this view.
 *
 * @param {any} data  `/api/setup`'s `data`, known non-null by the caller.
 * @returns {DocumentFragment}
 */
export function setupView(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Setup"));
  fragment.append(el("p", "sub", plain(data.root)));

  fragment.append(
    gridCard([
      kv("deployment", data.is_new),
      kv("database", data.has_usable_database),
      kv("paper stage", data.ready_for_paper),
      kv("config file", plain(data.config_path)),
      kv("database file", plain(data.db_path)),
    ]),
  );

  if (data.job) fragment.append(jobPanel(data.job));

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

  const notAutomated = new Map(
    (data.not_automated || []).map(/** @param {any} row */ (row) => [row.key, row.why]),
  );
  const actions = new Map(
    (data.actions || []).map(/** @param {any} action */ (action) => [action.key, action]),
  );

  for (const stage of STAGES) {
    const here = steps.filter(/** @param {any} step */ (step) => step.stage === stage.key);
    fragment.append(heading("h-stage-".concat(stage.key), stage.heading));
    fragment.append(note(stage.blurb));
    if (here.length === 0) {
      fragment.append(el("p", "empty", "No steps in this stage."));
      continue;
    }
    for (const step of here) fragment.append(stepCard(step, actions, notAutomated));
  }

  return fragment;
}

/**
 * The two stages, in the order the runbook works down.
 *
 * A table rather than a branch, and the wording is `render.py::render_setup`'s own, because it
 * carries an argument rather than a label: paper "places nothing" is the sentence that makes
 * paper the safe default, and paraphrasing it in a second front-end weakens it.
 *
 * @type {Array<{key: string, heading: string, blurb: string}>}
 */
const STAGES = [
  {
    key: "paper",
    heading: "To run in paper",
    blurb: "Evaluates rules against real market data and places nothing.",
  },
  {
    key: "live",
    heading: "To go live",
    blurb: "Everything the go-live runbook adds before real money moves.",
  },
];

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
 * `MECHANICAL` steps, asserted disjoint from the nine capability-increasing actions. A client
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
 * The activity feed: `/api/activity`'s payload, scoped.
 *
 * ── WHAT THE TUI HAS THAT THE RENDERED PAGE DOES NOT, AND HOW IT LANDS HERE ──────────────────
 *
 * `tests/commands/test_tui.py` pins three things about the activity screen, and #541 verifies
 * against that file:
 *
 *   * **The scope, cycled by `t`.** `today -> 7d -> all`, reopening at `today`. The rendered page
 *     has the three as links; they are here as buttons, and the CURRENT one is read off
 *     `data.scope` -- the value `read_activity` echoes back after `normalise_scope` had its say
 *     -- rather than off what this client asked for. That is the difference between showing what
 *     was requested and showing what happened.
 *   * **"keel has not run yet today."** An empty scope is never a blank panel: the TUI names the
 *     last cycle before the scope and says how to widen it. `activity_payload` sends
 *     `last_cycle_before_scope` for exactly this.
 *   * **Expanding a cycle to its events.** The TUI's Enter. Here it is a native `<details>` per
 *     cycle, so there is no key to teach and no expansion state to remember.
 *
 * The rendered page's columns are kept in its order, plus the `quiet` flag, which the payload
 * judges (`neutral`, never `warn` -- a cycle where nothing happened is a POSITIVE observation)
 * and the HTML drops.
 *
 * @param {any} data      `/api/activity`'s `data`.
 * @param {any} sort      the response's `sort` echo, placed unread.
 * @param {(column: string, direction: string) => void} onSort
 * @param {(scope: string) => void} onScope
 * @returns {DocumentFragment}
 */
/**
 * The Timeline view (#703): one chronology over four stores that never knew about each other.
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
 * @param {any} data  `/api/timeline`'s `data`.
 * @param {any} sort
 * @param {(column: string) => void} onSort
 * @param {(kind: string) => void} onKind
 * @returns {DocumentFragment}
 */
export function timelineView(data, sort, onSort, onKind) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Timeline"));

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
  const link = el("a", "chartaction", "Export CSV");
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
 * The Balances view (#702): what the account holds, as the last cycle recorded it.
 *
 * ── EVERY FIGURE IS STAMPED, BECAUSE EVERY FIGURE IS RECORDED ────────────────────────────────
 *
 * Nothing here came from a network call -- `keel serve` reads SQLite and nothing else, and
 * `keel/commands/balances.py` carries why that is the design rather than a limitation. What
 * makes that honest instead of merely quiet is the as-of stamp beside each tile: this is the
 * cash the engine sized against when it last evaluated the rails, and the page says when.
 *
 * ── NO BUYING POWER, NO DEPOSIT, NO TRANSFER, NO CTA ─────────────────────────────────────────
 *
 * #702's refusal. Cash is a fact, not an affordance. keel is cash-spot by constitution, so a
 * "buying power" tile would advertise leverage the engine refuses to take, and a deposit button
 * would be this page's version of the close button the Positions view also does not have.
 * Pinned by `tests/web/test_balances_view.py`.
 *
 * ── THE SETTLED SPLIT IS NAMED AS MISSING WHEN IT IS, NOT OMITTED ────────────────────────────
 *
 * `settled_breakdown` says whether `cycle_balances` (#719) has recorded the pair for this
 * mode/currency yet -- it renders "unrecorded" until a live cycle has, and the venue's actual
 * settled/total figures once one has. Leaving the tiles out before that would let a reader
 * assume the available figure IS the settled one; saying so is the only rendering that cannot
 * be misread either way.
 *
 * @param {any} data  `/api/balances`'s `data`.
 * @param {any} sort
 * @param {(column: string) => void} onSort
 * @returns {DocumentFragment}
 */
/**
 * The Research Hub's trials ledger (#708): every experiment keel has run, rejected ones included.
 *
 * ── NO SORT CONTROL ON THIS PAGE, ANYWHERE ───────────────────────────────────────────────────
 *
 * Note the signature: `data` and nothing else. Every other table view here takes `(data, sort,
 * onSort)`; this one takes neither, and not one of its columns declares a `key`. That is the
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
 * `tests/web/test_research_view.py::test_the_research_view_offers_no_action_of_any_kind`. A page
 * whose entire argument is that selection happened under a discipline must not put the selection
 * behind a control on the same screen.
 *
 * @param {any} data      the `/api/research/trials` payload.
 * @param {any} gauntlet  the `/api/research/gauntlet` payload, or `null` if that read failed.
 * @param {any} slippage  the `/api/research/slippage` payload, or `null` if that read failed.
 * @returns {DocumentFragment}
 */
export function researchView(data, gauntlet, slippage) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Research"));

  const sub = el("p", "sub");
  sub.append(field(data.generated_at), " · ");
  sub.append(field(data.shown_count), " trials in the record");
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
      kv("ledger", data.ledger),
      kv("tamper check", data.chain),
      kv("trials run", data.trials_run),
      kv("decisions", data.decisions),
    ]),
  );

  const breaks = stringList(data.chain_errors, "breaks");
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
      (data.exploration || []).map(/** @param {any} row */ (row) => [
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
      (data.rows || []).map(/** @param {any} row */ (row) => [
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
      plain(data.empty_note),
    ),
  );

  fragment.append(gauntletSection(gauntlet));
  fragment.append(slippageSection(slippage));

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
 * The Slippage Universe (#708, view 4), as a section of the Research page.
 *
 * A SECTION and not a route of its own, following `insightsView`'s precedent: one client route
 * whose two endpoints are read concurrently and rendered under one page. Tabs become worth
 * building when there are four of these; two under headings is navigable and needs no sub-router
 * that `staticfiles.CLIENT_ROUTES` would then have to learn about.
 *
 * `null` when the read failed -- `mount` leaves the primary view standing when a SECONDARY
 * endpoint errors, so this says the section is missing rather than taking the trials table down
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


export function balancesView(data, sort, onSort) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Balances"));

  const sub = el("p", "sub");
  sub.append(field(data.generated_at), " · ", field(data.recorded));
  fragment.append(sub);

  fragment.append(
    gridCard([
      kv("mode", plain(data.mode) || "unstamped"),
      kv("available cash", data.cash),
      kv("as of", data.cash_as_of),
      kv("equity", data.equity),
      kv("unrealized", data.unrealized),
      kv("high water mark", data.hwm),
    ]),
  );

  fragment.append(heading("h-settled", "Settled and unsettled"));
  const settled = el("p", "note");
  settled.append(field(data.settled_breakdown));
  fragment.append(settled);
  fragment.append(
    gridCard([
      kv("settled", data.settled_cash),
      kv("total", data.total_cash),
      kv("as of", data.settled_as_of),
    ]),
  );

  if (plain(data.mode) === "paper") {
    fragment.append(heading("h-paper", "Synthetic account"));
    const note = el("p", "note");
    note.append("The paper account's cash right now, beside the cycle's recorded reading above.");
    fragment.append(note);
    fragment.append(gridCard([kv("paper cash", data.paper_cash)]));
  }

  fragment.append(heading("h-assets", "Held assets"));
  const assets = data.assets || [];
  fragment.append(
    table(
      "h-assets",
      [
        { label: "product", numeric: false, key: "product_id" },
        { label: "qty held", numeric: true, key: "qty" },
        { label: "mark", numeric: true, key: "mark" },
        // No `key`: `/api/balances` does not declare `mark_as_of` sortable, and a key the
        // server will not order by renders a header that looks clickable and is not.
        { label: "marked at", numeric: false },
        { label: "value", numeric: true, key: "market_value" },
      ],
      assets.map(/** @param {any} row */ (row) => [
        plain(row.product_id) || "—",
        row.qty,
        row.mark,
        row.mark_as_of,
        row.market_value,
      ]),
      "No held assets. keel is holding nothing right now.",
      { sort: sort, onSort: onSort },
    ),
  );

  return fragment;
}


/**
 * The Positions view (#701): what is held, what it is worth, and how close it is to its stop.
 *
 * ── NO CLOSE ACTION, AND THAT IS THE DESIGN ──────────────────────────────────────────────────
 *
 * Alpaca's positions page has a per-row close. This one does not, ever. An exit goes through the
 * typed-phrase friction of the terminal path, because a panic tap on a table row must not be the
 * last line of defence between an operator and an unplanned market sell. The absence is pinned by
 * `tests/web/test_positions_view.py::test_the_positions_view_has_no_close_action_anywhere`, so it
 * survives the day it looks like an obvious convenience to add.
 *
 * ── GROUPED BY THE REPORT'S OWN PRODUCT LIST ─────────────────────────────────────────────────
 *
 * `data.products` rather than a set this file assembles from the rows: two answers to "which
 * products does this book hold" is one too many, and the ordered one is already on the report.
 * A product holds several TRANCHES -- that is what tranches are for -- so each section is a
 * table of them rather than one row pretending to be the position.
 *
 * ── THE CHIP EXPLAINS THE IDLE DEPLOYMENT ────────────────────────────────────────────────────
 *
 * `freshness` is the ENTRY GATE's verdict, not a data age: `missing`/`behind`/`unconfirmed` are
 * the agent's own reasons for refusing to open here. It is the most common answer to "why has
 * nothing happened", which is why it sits beside the money rather than under a disclosure.
 *
 * It is a COLUMN and not a per-product chip, because the gate granularity is the one the RULE
 * that opened the tranche declares. Two tranches of one product, opened by rules on different
 * timeframes, have two verdicts -- and a chip above the table would have to pick one.
 *
 * @param {any} data  `/api/positions`'s `data`.
 * @param {any} sort
 * @param {(column: string) => void} onSort
 * @returns {DocumentFragment}
 */
export function positionsView(data, sort, onSort) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Positions"));

  const sub = el("p", "sub");
  sub.append(field(data.generated_at), " · ");
  sub.append(field(data.open_count), " open tranche(s)");
  fragment.append(sub);

  const rows = data.rows || [];
  if (rows.length === 0) {
    // A real answer, not a blank panel: an account holding nothing is an ordinary state for a
    // daily agent between entries, and it is not the same as a page that failed to load.
    fragment.append(el("p", "empty", "No open positions. keel is holding nothing right now."));
    return fragment;
  }

  for (const product of data.products || []) {
    const held = rows.filter(/** @param {any} row */ (row) => row.product_id === product);
    const id = ["h-pos", product].join("-");
    fragment.append(heading(id, product));

    // The chip belongs to the PRODUCT, not the tranche: the entry gate asks about a series, so
    // every tranche of one product shares one verdict and repeating it per row would suggest
    // they could differ.
    if (held.length === 0) continue;

    fragment.append(
      table(
        id,
        [
          { label: "opened (UTC)", numeric: false, key: "opened_at" },
          { label: "rule", numeric: false, key: "rule_name" },
          { label: "qty held", numeric: true, key: "qty" },
          { label: "entry", numeric: true, key: "entry_fill" },
          { label: "entry fee", numeric: true, key: "entry_fee" },
          { label: "mark", numeric: true, key: "mark" },
          { label: "value", numeric: true, key: "market_value" },
          { label: "unrealized", numeric: true, key: "unrealized" },
          { label: "stop", numeric: true, key: "initial_stop" },
          { label: "to stop", numeric: true, key: "stop_distance" },
          { label: "to stop %", numeric: true, key: "stop_distance_pct" },
          // PER TRANCHE, not per product. The gate granularity comes from the RULE that opened
          // this tranche (`_gate_granularity_for`), so one product holding tranches from rules
          // on different timeframes has two verdicts -- a single chip above the table would
          // state one of them over the other.
          { label: "entry gate", numeric: false, key: "freshness" },
        ],
        held.map(/** @param {any} row */ (row) => [
          row.opened_at,
          plain(row.rule_name) || "—",
          row.qty,
          row.entry_fill,
          row.entry_fee,
          row.mark,
          row.market_value,
          row.unrealized,
          row.initial_stop,
          row.stop_distance,
          row.stop_distance_pct,
          row.freshness,
        ]),
        "No open tranches for this product.",
        { sort: sort, onSort: onSort },
      ),
    );

    for (const row of held) {
      // The realized side, under a disclosure: a scaled-out tranche has legs already booked, and
      // they belong beside the running position rather than in the journal's separate account of
      // the same trade. Collapsed because most tranches have never been scaled out.
      const node = el("details", "row");
      const summary = el("summary");
      summary.append("tranche ", plain(row.id), " · realized legs");
      node.append(summary);
      node.append(
        gridCard([
          kv("realized qty", row.realized_qty),
          kv("realized proceeds", row.realized_proceeds),
          kv("realized fees", row.realized_fees),
          kv("marked at", row.mark_at),
        ]),
      );
      fragment.append(node);
    }
  }

  return fragment;
}


/**
 * The orders view (#659): what keel actually bought and sold, and whether anybody agreed to it.
 *
 * **`placement` is the first column, and that is the argument this view exists to make.** On a
 * deployment running `autonomy: ON`, "did I approve this, or did keel place it alone" is the
 * first question about an order, not the last -- so it leads the row, ahead of the product and
 * ahead of the price. The word and its tone both arrive already decided
 * (`payload._order_row_payload`), because `bypass` meaning the same thing as `autonomous` is a
 * judgement, and judgements are made in Python.
 *
 * **`expected` and `actual` are adjacent, with the divergence between them.** That difference is
 * realised slippage. Its tone is side-aware and comes from the server for the reason
 * `_order_row_payload` states: paying more than expected is bad on a buy and good on a sell, so
 * a client colouring the minus sign would be wrong on half the rows.
 *
 * **`fee` is the figure as recorded and never a rate.** No percentage is computed here, and none
 * crosses the wire. A paper row's fee carries a `modelled` badge instead, because
 * `PaperTrader` derives it from the configured rate and reading it back as a measurement of that
 * rate is circular.
 *
 * **`raw_response` is not in the payload at all**, so this function could not render it if it
 * tried. The venue's order id is, when there is one, and a sentence saying why there is not,
 * when there is not.
 *
 * @param {any} data
 * @param {any} sort
 * @param {(column: string) => void} onSort
 * @param {(scope: string) => void} onScope
 * @returns {DocumentFragment}
 */
export function ordersView(data, sort, onSort, onScope, onStatus) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Orders"));

  const sub = el("p", "sub");
  sub.append(field(data.generated_at));
  fragment.append(sub);

  fragment.append(scopeSwitch(plain(data.scope), onScope, "Orders scope"));
  // Guarded, so a caller that has not wired the tabs renders the view unchanged rather
  // than a bar whose buttons do nothing.
  if (onStatus) {
    fragment.append(statusSwitch(plain(data.status), data.statuses || [], onStatus));
  }

  fragment.append(
    gridCard([
      kv("shown", data.shown_count),
      // Only when a status is actually on. Unfiltered, `filtered_count` equals `scoped_count`
      // by construction, so the row would repeat the number below it under a label naming a
      // filter that is not in force. With a tab open it is the denominator "shown" is a page
      // of -- and the report is the only place allowed to work that out (Rule 2 keeps the
      // subtraction out of the browser).
      ...(plain(data.status) ? [kv("in this status", data.filtered_count)] : []),
      kv("in scope", data.scoped_count),
      kv("in this book", data.total_count),
      // Which modes this book actually holds. A deployment book holds one, and saying which
      // means a reader never concludes it from an empty section -- the same reason `mode` is
      // on every row below.
      kv("modes", (data.modes || []).join(", ") || "none"),
    ]),
  );

  const rows = data.rows || [];
  fragment.append(heading("h-orders", "Placed orders"));
  fragment.append(
    table(
      "h-orders",
      [
        { label: "placement", numeric: false, key: "placement" },
        { label: "id", numeric: true, key: "id" },
        { label: "mode", numeric: false, key: "mode" },
        { label: "side", numeric: false, key: "side" },
        { label: "product", numeric: false, key: "product_id" },
        { label: "status", numeric: false, key: "status" },
        // The rule's NAME, beside the status rather than buried in the disclosure: on a
        // book with several rules live, which one placed an order is a scanning
        // question, and a foreign key in a detail panel does not answer it.
        { label: "rule", numeric: false, key: "rule_name" },
        { label: "qty", numeric: true, key: "qty" },
        { label: "filled", numeric: true, key: "filled_quantity" },
        { label: "expected", numeric: true, key: "expected_fill" },
        { label: "actual", numeric: true, key: "actual_fill" },
        { label: "divergence", numeric: true, key: "fill_divergence" },
        { label: "fee", numeric: true, key: "fee" },
        { label: "placed (UTC)", numeric: false, key: "created_at" },
      ],
      rows.map(
        /** @param {any} row */ (row) => [
          row.placement,
          row.id,
          row.mode,
          row.side,
          plain(row.product_id) || "—",
          row.status,
          // Positional: `table()` pairs `columns[index]` with this array by index, so this cell
          // sits where the "rule" header does and every later one shifts if it is missing.
          row.rule_name,
          row.qty,
          row.filled_quantity,
          row.expected_fill,
          row.actual_fill,
          row.fill_divergence,
          row.fee,
          row.created_at,
        ],
      ),
      // Never reached when `rows` is empty, because `emptyOrders` below answers first with the
      // specific fact; kept as the table's own fallback rather than a lie about which empty it
      // is.
      "No orders to show.",
      { sort: sort, onSort: onSort },
    ),
  );

  if (rows.length === 0) fragment.append(emptyOrders(data));
  else for (const row of rows) fragment.append(orderDetail(row));

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
 * @param {any} data
 * @returns {HTMLElement}
 */
function emptyOrders(data) {
  const card = el("div", "card");
  card.append(el("strong", undefined, plain(data.empty_note) || "No orders to show."));
  return card;
}

/**
 * One order's full record, as a disclosure under the table.
 *
 * The table is the scan; this is the audit. It carries the three things a row cannot fit and an
 * operator needs anyway: what the placement WORD means in a sentence, whether the fee was
 * charged or modelled, and the venue's own order id (or why there is none).
 *
 * @param {any} row
 * @returns {HTMLElement}
 */
function orderDetail(row) {
  const node = el("details", "cycle");
  const summary = el("summary");
  summary.append(field(row.placement), " · ", field(row.mode), " ", field(row.side), " ");
  summary.append(plain(row.product_id) || "—", " · ");
  summary.append(field(row.created_at));
  node.append(summary);
  node.append(
    gridCard([
      kv("placement", field(row.placement)),
      kv("what that means", plain(row.placement_note)),
      kv("order type", row.order_type),
      kv("status", row.status),
      kv("expected fill", row.expected_fill),
      kv("actual fill", row.actual_fill),
      kv("divergence", row.fill_divergence),
      kv("divergence direction", row.fill_divergence_adverse),
      kv("fee", row.fee),
      kv("fee basis", field(row.fee_modelled)),
      kv("about this fee", plain(row.fee_note)),
      // The venue's book at submit (#626), as the PAIR. No spread is derived here: the two
      // columns exist precisely so that the three different spread questions stay askable, and
      // a view that answered one of them silently would be choosing for the reader.
      kv("best bid at submit", row.submit_best_bid),
      kv("best ask at submit", row.submit_best_ask),
      kv("book at submit", plain(row.submit_book_note) || field(row.submit_book_observed)),
      // The ONLY thing read out of `raw_response`, upstream, already bounded. When it is empty
      // the note says why -- a blank cell reads as missing data, and "the paper trader placed
      // this" reads as the answer it is.
      kv(
        "venue order id",
        plain(row.venue_order_id) || plain(row.venue_order_id_note) || "—",
      ),
      // The name is in the table; this is the id it resolved from, plus the sentence
      // that tells the two absences apart -- no rule recorded at all, versus a rule
      // that has left the book. A blank cell would collapse them.
      kv("rule", plain(row.rule_note) || field(row.rule_name)),
      kv("rule id", row.rule_id),
      kv("last updated", row.updated_at),
    ]),
  );
  return node;
}

export function activityView(data, sort, onSort, onScope) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Activity"));

  const sub = el("p", "sub");
  sub.append(plain(data.source) || "no source", " · ");
  sub.append(field(data.generated_at));
  fragment.append(sub);

  fragment.append(scopeSwitch(plain(data.scope), onScope));

  // Shown whenever the server sent one, which it does for every status except `ok` -- so the
  // presence of the note IS the branch, and this file never inspects the status word to make it.
  if (plain(data.status_note) || plain(data.detail)) {
    const card = el("div", "card");
    const what = el("strong", toneOf(data.status));
    what.append(field(data.status));
    card.append(what, " ");
    card.append(el("span", "muted", plain(data.status_note)));
    if (data.detail) card.append(el("div", "muted", plain(data.detail)));
    fragment.append(card);
  }

  const cycles = data.cycles || [];
  fragment.append(heading("h-cycles", "Cycles"));
  fragment.append(
    table(
      "h-cycles",
      [
        { label: "started (UTC)", numeric: false, key: "started_at" },
        { label: "cycle", numeric: false, key: "cycle_id" },
        { label: "mode", numeric: false, key: "mode" },
        { label: "products", numeric: false },
        { label: "signals", numeric: true, key: "signals" },
        { label: "blocked", numeric: true, key: "blocked" },
        { label: "entered", numeric: true, key: "entered" },
        { label: "exited", numeric: true, key: "exited" },
        { label: "errors", numeric: true, key: "errors" },
        { label: "quiet", numeric: false },
        { label: "highlights", numeric: false },
      ],
      cycles.map(
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
      { sort: sort, onSort: onSort },
    ),
  );

  if (cycles.length === 0) fragment.append(emptyScope(data));

  const detailed = cycles.filter(
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
 * window they had just landed in, and hard-coding "Activity scope" onto the Orders view would be
 * worse than no name at all.
 *
 * @param {string} current
 * @param {(scope: string) => void} onScope
 * @param {string} [label]  how the control announces itself. Defaults to Activity's wording.
 * @returns {HTMLElement}
 */
function scopeSwitch(current, onScope, label) {
  const wrap = el("nav", "scopes");
  wrap.setAttribute("aria-label", label || "Activity scope");
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
 * What to say when the chosen scope holds no cycles.
 *
 * Never a blank panel, and never "no activity" either. `test_tui.py` pins the distinction
 * (`test_activity_overlay_with_nothing_today_is_never_blank_and_names_the_last_run`): a scope
 * with nothing in it is either "keel has not run yet today", which is an ordinary morning for a
 * daily agent, or a window that could not prove it reached the scope's boundary -- which is not
 * an observation about the engine at all. The second is carried by `scope_fully_covered`, already
 * judged, and is in `feedNotes` below.
 *
 * @param {any} data
 * @returns {HTMLElement}
 */
function emptyScope(data) {
  const card = el("div", "card");
  card.append(el("strong", undefined, "keel has not run yet in this scope."));
  const last = data.last_cycle_before_scope;
  if (last) {
    const line = el("p", "note");
    line.append("Last cycle: ", field(last.started_at));
    card.append(line);
    card.append(note("Widen the scope above to see it."));
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
 * @param {any} data
 * @returns {HTMLElement}
 */
function feedNotes(data) {
  const line = el("p", "note");
  const counts = [
    ["hidden by the scope", data.cycles_out_of_scope],
    ["lines read", data.lines_read],
    ["unusable lines skipped", data.lines_skipped],
    ["cycles beyond the display cap", data.cycles_dropped],
  ];
  for (const [label, value] of counts) line.append(field(value), " ", label, " · ");
  line.append(field(data.scope_fully_covered), " · ", field(data.window_truncated));
  return line;
}

/**
 * Insights: `/api/insights` and `/api/journal`, as one view.
 *
 * Two endpoints and one view, which `api.py` planned for: `/insights` and `/journal` both name
 * `/insights` as their `html_route`, and the split exists so that "one sortable collection per
 * endpoint keeps `?sort=` unambiguous without a `?table=` beside it". The rendered page shows
 * both under one heading and so does this.
 *
 * ── WHAT THIS ADDS OVER THE RENDERED PAGE ────────────────────────────────────────────────────
 *
 *   * **The equity curve.** `chart.js` draws it from coordinates `build_equity_curve` computed;
 *     see that module for why the geometry is not computed here.
 *   * **The promotion gate, per rule.** The rendered page shows a track record and no distance to
 *     the floor; the TUI shows a `gate:` line and `test_tui.py` pins its two states (`PASSING`
 *     ok, `blocked` warn) and its blocking reasons. `_gate_payload` carries all of it, so it is a
 *     disclosure under each rule that has one -- and `GateDistance` is `None` for any status
 *     other than `paper`, which is why some rules have none.
 *   * **`avg_win` / `avg_loss` / `realized_rr`**, which the payload sends and the HTML drops.
 *
 * ── DIVERGENCE FROM THE RENDERED PAGE (SECOND #548-CLASS BUG, FOUND WHILE PORTING) ───────────
 *
 * `render.py::pct` appends a literal `%` to `drawdown_total_pct`, `drawdown_weekly_pct`,
 * `max_total_dd_pct` and `max_weekly_dd_pct` -- and those four hold FRACTIONS, not percent units.
 * `keel/templates/config.yaml` ships `max_total_dd_pct: 0.20` and `execution/guards.py` compares
 * the raw drawdown against it directly, so a 20% ceiling renders on the HTML pages as **0.20%**
 * and a 5% drawdown as 0.05% -- a hundredfold understatement of a risk limit, in green. The CLI,
 * the TUI and `payload.py` all print the bare fraction (`payload.ratio`, no suffix); `render.py`
 * is the only surface that adds the sign. This view shows the payload's value, which is the
 * correct one. `RuleTrackRecord.win_rate` really is in percent units and keeps its `%`.
 *
 * @param {any} insights  `/api/insights`'s `data`.
 * @param {any} journal   `/api/journal`'s `data`, or `null` if that read failed.
 * @param {any} sort      `/api/insights`'s `sort` echo (the rule table's).
 * @param {(column: string, direction: string) => void} onSort
 * @param {any} journalSort  `/api/journal`'s own `sort` echo. A SECOND one, because the two
 *                           tables are two endpoints with two sortable collections and each
 *                           refuses the other's columns -- see `main.js`'s `params`.
 * @param {(column: string, direction: string) => void} onJournalSort
 * @returns {DocumentFragment}
 */
export function insightsView(insights, journal, sort, onSort, journalSort, onJournalSort) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Insights"));

  const sub = el("p", "sub");
  sub.append(field(insights.generated_at), " · ");
  sub.append(field(insights.closed_trade_count), " closed trade(s)");
  fragment.append(sub);

  const account = insights.account;
  fragment.append(
    gridCard([
      kv("mode", plain(account.mode)),
      kv("equity state", account.state_mode),
      kv("rail 11", account.rail11),
      kv("high water mark", account.high_water_mark),
      kv("paper cash", account.paper_cash),
      kv("drawdown (total)", account.drawdown_total),
      kv("drawdown (weekly)", account.drawdown_weekly),
      kv("max total dd", account.max_total_dd),
      kv("max weekly dd", account.max_weekly_dd),
    ]),
  );

  fragment.append(heading("h-records", "Rule track records"));
  const rules = insights.rules || [];
  fragment.append(
    table(
      "h-records",
      [
        { label: "rule", numeric: false, key: "rule_name" },
        { label: "status", numeric: false, key: "status" },
        { label: "class", numeric: false, key: "promotion_class" },
        { label: "trades", numeric: true, key: "n_trades" },
        { label: "win rate", numeric: true, key: "win_rate" },
        { label: "avg win", numeric: true, key: "avg_win" },
        { label: "avg loss", numeric: true, key: "avg_loss" },
        { label: "realized rr", numeric: true, key: "realized_rr" },
        { label: "expectancy", numeric: true, key: "expectancy" },
        { label: "profit factor", numeric: true, key: "profit_factor" },
        { label: "max dd", numeric: true, key: "max_drawdown" },
        { label: "sample", numeric: false },
      ],
      rules.map(
        /** @param {any} rule */ (rule) => [
          plain(rule.rule_name),
          rule.status,
          plain(rule.promotion_class),
          rule.n_trades,
          rule.win_rate,
          rule.avg_win,
          rule.avg_loss,
          rule.realized_rr,
          rule.expectancy,
          rule.profit_factor,
          rule.max_drawdown,
          samplePill(rule.significant),
        ],
      ),
      "No rules with a track record yet.",
      { sort: sort, onSort: onSort },
    ),
  );
  fragment.append(
    note(
      "Below 30 closed trades a win rate is not yet distinguishable from random entry, which is why the sample column says so rather than leaving the number to speak for itself.",
    ),
  );

  const gated = rules.filter(/** @param {any} rule */ (rule) => Boolean(rule.gate));
  if (gated.length !== 0) {
    fragment.append(heading("h-gates", "Distance to the promotion gate"));
    fragment.append(
      note("Only rules in paper have one: a candidate has not backtested yet, and a live rule has already cleared it."),
    );
    for (const rule of gated) fragment.append(gateCard(rule));
  }

  fragment.append(heading("h-journal", "Journal"));
  if (!journal) {
    fragment.append(el("p", "empty", "The journal could not be read."));
    return fragment;
  }

  const shown = el("p", "note");
  shown.append(field(journal.shown_count), " of ", field(journal.total_count), " closed trade(s)");
  fragment.append(shown);

  // #698: portfolio reality above statistical expectancy. The account-equity series is what the
  // account was WORTH each cycle; the curve below it is what the closed trades DID. Stacked
  // rather than merged because they are different quantities on different axes -- see
  // `chart.js::equitySeriesChart`.
  //
  // It comes off `/api/insights`, not `/api/journal`, so it is NOT narrowed by the journal's
  // `?limit=`. The note below says so: a reader looking at two stacked charts would otherwise
  // reasonably assume the row cap above applies to both.
  const series = equitySeriesChart(insights.equity_series, "h-series");
  if (series) {
    // What the top chart's span actually is, in words, next to the chart. `is_truncated` is a
    // `flag` whose `display` already says which case this is -- the client never compares
    // `point_count` against `total_recorded` to find out, because that comparison is arithmetic
    // and the answer is a claim about how much of the record is on screen.
    const scope = el("p", "note");
    scope.append(
      field(insights.equity_series.point_count),
      " of ",
      field(insights.equity_series.total_recorded),
      " recorded cycle(s) — ",
      field(insights.equity_series.is_truncated),
      ". Not narrowed by the journal row cap below.",
    );
    fragment.append(series, scope);
  } else if (insights.equity_series) {
    fragment.append(el("p", "empty", plain(insights.equity_series.reading.display)));
  }
  // No `else` for a payload with no `equity_series` at all. That is not a deployment with no
  // readings -- it is a RESPONSE FROM BEFORE THIS FIELD EXISTED, which the service worker can
  // still be holding after an upgrade. Reading `.reading.display` off it would throw and blank
  // the whole insights view over a stale cache entry the next refresh fixes on its own.

  const chart = equityChart(journal.curve, "h-curve");
  if (chart) {
    fragment.append(chart);
    // #602: wired in `main.js`, which owns the arithmetic both actions need (resetting a
    // `viewBox`, reading a pointer position and sizing a canvas -- see `chart.js`'s own note on
    // why that cannot live here). `data-chart-action` is the whole interface between the two
    // files; this one never imports `main.js`, which would be circular (`main.js` imports this
    // module already).
    const actions = el("div", "chartactions");
    const reset = el("button", "chartaction", "Reset view");
    reset.setAttribute("type", "button");
    reset.setAttribute("data-chart-action", "reset-view");
    const save = el("button", "chartaction", "Save as image");
    save.setAttribute("type", "button");
    save.setAttribute("data-chart-action", "save-image");
    actions.append(reset, save);
    fragment.append(actions);
  } else {
    fragment.append(el("p", "empty", plain(journal.curve.reading.display)));
  }

  const entries = journal.entries || [];
  const journalTable = table(
    "h-journal",
    [
      { label: "closed (UTC)", numeric: false, key: "closed_at" },
      { label: "product", numeric: false, key: "product_id" },
      { label: "rule", numeric: false, key: "rule_name" },
      { label: "qty", numeric: true, key: "qty" },
      { label: "entry", numeric: true, key: "entry_fill" },
      { label: "exit", numeric: true, key: "exit_fill" },
      { label: "net p&l", numeric: true, key: "pnl" },
      { label: "fees", numeric: true, key: "fees" },
      { label: "r", numeric: true, key: "r_multiple" },
      { label: "outcome", numeric: false, key: "outcome" },
    ],
    entries.map(
      /** @param {any} entry */ (entry) => [
        entry.closed_at,
        plain(entry.product_id),
        plain(entry.rule_name) || "—",
        entry.qty,
        entry.entry_fill,
        entry.exit_fill,
        entry.pnl,
        entry.fees,
        entry.r_multiple,
        entry.outcome,
      ],
    ),
    "No closed trades.",
    { sort: journalSort, onSort: onJournalSort },
  );

  // #602: a row whose trade has a point on the curve above is both hoverable and focusable, so
  // `main.js` can highlight that point for a mouse user and a keyboard user alike --
  // `tbody tr[data-point-index]:focus-within` in `keel.css` is the visible half.
  // `entries()` pairs each built `<tr>` with the entry that produced it by POSITION, which is
  // exactly the order `table()` renders them in -- it builds one `<tr>` per row, in the order
  // `rows` (built from `entries` just above) gave them.
  for (const [index, row] of journalTable.querySelectorAll("tbody tr").entries()) {
    const entry = entries[index];
    if (!entry || entry.point_index === null) continue;
    row.setAttribute("data-point-index", entry.point_index);
    row.setAttribute("tabindex", "0");
  }
  fragment.append(journalTable);
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
 * @param {any} rule
 * @returns {HTMLElement}
 */
function gateCard(rule) {
  const gate = rule.gate;
  const node = el("details", "gate");
  const summary = el("summary");
  // An id on every summary, and it is not decoration: `main.js` restores which disclosures were
  // open across a rebuild by matching these, so a reader who expanded one does not watch it shut
  // the moment the engine writes a row. Derived from the row it describes, so it is stable across
  // a re-read that did not change that row.
  summary.id = "h-gatedist-".concat(plain(gate.rule_name));
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
 * Rules: `/api/rules`'s payload.
 *
 * **Read-only, and the page says so out loud.** `rules_payload`'s docstring: "promotion happens in
 * the CLI, behind the TTY gate, and nothing this API serves can change a rule's status."
 *
 * The lifecycle timestamps -- created, promoted, demoted -- are shown here and are not on the
 * rendered page, which shows id, kind, status and a Python `repr` of the params dict. The params
 * are a disclosure of `key = value` rows instead, because `_rule_row_payload` stringifies each
 * value individually and a `repr` of a dict is a thing only a Python programmer can read.
 *
 * @param {any} data  `/api/rules`'s `data`.
 * @param {any} sort
 * @param {(column: string, direction: string) => void} onSort
 * @returns {DocumentFragment}
 */
export function rulesView(data, sort, onSort) {
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
  return fragment;
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
 * Venues: `/api/venues`'s payload.
 *
 * **What each installed adapter DECLARES, never whether it is configured or reachable.** That
 * distinction is #233 and `_venue_payload` states it: "a row here is not a claim that the venue is
 * configured". An adapter that failed to construct still gets a row, judged `bad` -- a missing row
 * would read as "not installed", which is a different fact and a worse one to be wrong about.
 *
 * The rendered page collapses a failed adapter into a row of blanks with the error in it; this
 * shows the error in its own column and keeps the declaration columns, because an adapter that
 * failed to construct still declares what it supports and that is what someone is looking at the
 * page to find out.
 *
 * The four list-valued declarations the HTML drops -- quote currencies, data feeds, endpoints, and
 * the two capability flags -- are in a disclosure per adapter.
 *
 * @param {any} data
 * @param {any} sort
 * @param {(column: string, direction: string) => void} onSort
 * @returns {DocumentFragment}
 */
export function venuesView(data, sort, onSort) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Venues"));
  fragment.append(
    el("p", "sub", "what each installed adapter declares — not whether it is configured"),
  );

  const venues = data.venues || [];
  fragment.append(heading("h-venues", "Installed adapters"));
  fragment.append(
    table(
      "h-venues",
      [
        { label: "adapter", numeric: false, key: "name" },
        { label: "venue", numeric: false, key: "venue" },
        { label: "deployment", numeric: false, key: "deployment" },
        { label: "asset classes", numeric: false },
        { label: "orders", numeric: false },
        { label: "version", numeric: false, key: "package_version" },
        { label: "constructed", numeric: false },
      ],
      venues.map(
        /** @param {any} info */ (info) => [
          plain(info.name),
          plain(info.venue),
          plain(info.deployment),
          (info.asset_classes || []).join(", ") || "—",
          (info.supported_orders || []).join(", ") || "—",
          plain(info.package_version) || "—",
          info.error,
        ],
      ),
      "No adapters installed.",
      { sort: sort, onSort: onSort },
    ),
  );

  if (venues.length !== 0) {
    fragment.append(heading("h-declared", "Full declaration"));
    for (const info of venues) fragment.append(venueCard(info));
  }

  // A SEPARATE section, not a column added to the table above (#233). `venues` above answers
  // "what does the adapter declare"; `readiness` answers "can a live entry actually be placed
  // on THIS deployment, right now" -- `payload.py`'s own `_readiness_payload` docstring keeps
  // the two collections apart for the same reason, and merging them back together here would
  // undo it at the one layer nothing else checks.
  const readiness = data.readiness || [];
  fragment.append(heading("h-readiness", "Venue readiness"));
  const readinessSub = el("p", "sub");
  readinessSub.append(
    "can a live entry actually be placed on this deployment, right now — ",
    "not what the adapter merely declares",
  );
  fragment.append(readinessSub);
  fragment.append(
    table(
      "h-readiness",
      [
        { label: "venue", numeric: false },
        { label: "state", numeric: false },
        { label: "explanation", numeric: false },
        { label: "fix", numeric: false },
      ],
      readiness.map(
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
 * One adapter's full declared capabilities.
 *
 * @param {any} info
 * @returns {HTMLElement}
 */
function venueCard(info) {
  const node = el("details", "venue");
  const summary = el("summary");
  summary.id = "h-venue-".concat(plain(info.name));
  summary.append(plain(info.name));
  node.append(summary);

  node.append(
    gridCard([
      kv("preview", plain(info.preview)),
      kv("session", info.session_bound),
      kv("funding", info.cash_only),
      kv("fee summary", info.supports_fee_summary),
      kv("package version", plain(info.package_version) || "—"),
    ]),
  );

  for (const [label, values] of [
    ["asset classes", info.asset_classes],
    ["supported orders", info.supported_orders],
    ["quote currencies", info.quote_currencies],
    ["data feeds", info.supported_data_feeds],
    ["declared endpoints", info.declared_endpoints],
  ]) {
    const list = stringList(values, "declared");
    node.append(note(label));
    node.append(list || el("p", "muted", "—"));
  }
  return node;
}

/**
 * Gates: `/api/gates`'s payload -- the capability inventory (#436).
 *
 * **This view is the reason a browser interface can be honest about its own limits.** The read
 * surface cannot reach one of these actions: the server implements no verb that would, and the
 * page says so beside the list of what it cannot do and who can. `gates_payload` reads a pure
 * declaration -- no config, no database, no network -- which is why this view has something to
 * show on a machine with nothing set up.
 *
 * The action count in each heading is `actions.length`, and `gates_payload`'s own docstring
 * sanctions it: "a client renders `actions.length`, which is a list length in the language that
 * owns the list, not a figure this layer invented." `Gate` holds no count, and Rule 6e bans
 * `len()` in the serialiser, so this is the one number on any of these views the server did not
 * send.
 *
 * @param {any} data
 * @returns {DocumentFragment}
 */
export function gatesView(data) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, "Gates"));
  fragment.append(
    el(
      "p",
      "sub",
      "every action that increases what keel can do without asking again",
    ),
  );

  const card = el("div", "card");
  card.append(el("strong", undefined, "This view cannot perform any of them."));
  card.append(" ");
  card.append(
    el(
      "span",
      "muted",
      "keel's JSON API answers GET only and implements no action verb, so there is no request this page can make that changes anything. Each action below needs a human at a terminal.",
    ),
  );
  fragment.append(card);

  const gates = data.gates || [];
  if (gates.length === 0) {
    fragment.append(el("p", "empty", "No gates are declared."));
    return fragment;
  }

  for (const gate of gates) {
    const id = "h-gate-".concat(plain(gate.name));
    const title = el("h2");
    title.id = id;
    title.append(plain(gate.name), " · ", gate.actions.length, " action(s)");
    fragment.append(title);

    const detail = el("div", "card");
    detail.append(kv("evidence required", plain(gate.evidence)));
    const closes = el("p", "note");
    closes.append("Fails closed against ", plain(gate.fails_closed_against), ".");
    detail.append(closes);
    const where = el("p", "note");
    where.append("Implemented once, at ", el("code", undefined, plain(gate.implementation)));
    detail.append(where);
    fragment.append(detail);

    fragment.append(
      table(
        id,
        [
          { label: "surface", numeric: false },
          { label: "action", numeric: false },
          { label: "grants", numeric: false },
          { label: "call site", numeric: false },
        ],
        (gate.actions || []).map(
          /** @param {any} action */ (action) => [
            pill(plain(action.surface)),
            invocationCell(action),
            plain(action.increases),
            el("code", undefined, plain(action.call_site)),
          ],
        ),
        "No actions are covered by this gate.",
      ),
    );
  }
  return fragment;
}

/**
 * An action's invocation, and what it mirrors.
 *
 * @param {any} action
 * @returns {HTMLElement}
 */
function invocationCell(action) {
  const cell = el("span");
  cell.append(el("code", undefined, plain(action.invocation)));
  if (action.mirrors) cell.append(" ", pill("mirrors ".concat(plain(action.mirrors))));
  return cell;
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
