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

import { equityChart } from "./chart.js";
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
 * One declared action, as a DESCRIPTION of what keel would do and what it would ask for.
 *
 * The inputs are listed, never rendered as a form. A disabled form is worse than a list: it looks
 * like something that could be filled in, and a `secret` field drawn as an input someone types
 * into and cannot submit is a password typed into a page for no reason at all.
 *
 * `secret` arrives carrying its own words -- `_action_input_payload` sends "never echoed back"
 * against "shown as typed" -- rather than as a boolean this file would have to translate, which
 * is the same rule that keeps `state` out of the client's hands.
 *
 * @param {any} action
 * @returns {HTMLElement}
 */
function actionCard(action) {
  const card = el("div", "action");
  card.append(el("p", "muted", plain(action.detail)));

  const inputs = action.inputs || [];
  if (inputs.length !== 0) {
    const list = el("ul", "inputs");
    for (const input of inputs) {
      const item = el("li");
      item.append(el("strong", undefined, plain(input.label)), " ");
      item.append(field(input.secret));
      if (input.hint) item.append(el("div", "muted", plain(input.hint)));
      const choices = stringList(input.choices, "choices");
      if (choices) item.append(choices);
      list.append(item);
    }
    card.append(list);
  }

  // An ordinary link to the rendered page, so a real navigation: `main.js` intercepts only hrefs
  // under its own `BASE`, and this is not one.
  const link = el("a", "runlink", plain(action.title));
  link.setAttribute("href", "/setup");
  card.append(link);
  card.append(note("Runs on keel's own setup page, which is where this session's write token is."));
  return card;
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
 * @param {string} current
 * @param {(scope: string) => void} onScope
 * @returns {HTMLElement}
 */
function scopeSwitch(current, onScope) {
  const wrap = el("nav", "scopes");
  wrap.setAttribute("aria-label", "Activity scope");
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

  const chart = equityChart(journal.curve, "h-curve");
  if (chart) fragment.append(chart);
  else fragment.append(el("p", "empty", plain(journal.curve.reading.display)));

  fragment.append(
    table(
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
      (journal.entries || []).map(
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
    ),
  );
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
