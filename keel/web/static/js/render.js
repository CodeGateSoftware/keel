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
  wrap.append(el("span", "k", label));
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
 * @typedef {{label: string, numeric: boolean}} Column
 */

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
 * @returns {HTMLElement}
 */
function table(id, columns, rows, empty) {
  if (rows.length === 0) return el("p", "empty", empty);

  const head = el("tr");
  for (const column of columns) {
    const cell = el("th", column.numeric ? "num" : undefined, column.label);
    // `scope` is what tells a screen reader that this cell labels its column rather than being
    // data in it. Cheap, and the difference between a table that can be navigated cell by cell
    // and one that reads as an undifferentiated run of numbers.
    cell.setAttribute("scope", "col");
    head.append(cell);
  }

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
 * @returns {DocumentFragment}
 */
export function stoppedView(reading) {
  const fragment = document.createDocumentFragment();
  const card = el("div", "card stopped");
  card.append(el("h1", undefined, "keel isn't running"));
  card.append(el("p", undefined, reading.engine.display));

  if (reading.error) {
    card.append(el("p", "detail", reading.error.detail || reading.error.title));
  } else {
    // No error means the server answered normally and simply has no deployment to read -- the
    // first-run path. The link goes to `/setup`, the server-rendered checklist, because that is
    // the page that exists and works today. #537 builds the client's own setup view, at which
    // point this href becomes `/static/setup` and this comment goes away.
    const p = el("p");
    const link = el("a", undefined, "Set keel up on this machine");
    link.setAttribute("href", "/setup");
    p.append(link);
    card.append(p);
  }

  fragment.append(card);
  return fragment;
}

/**
 * A view #537 has not built yet.
 *
 * Shipped rather than left as a dead nav link, and the difference matters for the two acceptance
 * criteria this issue is judged on: a dead link is a keyboard path that goes nowhere, and a
 * router with one route is a router nothing has exercised. This makes the shell's navigation
 * complete and honest at the same time.
 *
 * @param {string} label
 * @returns {DocumentFragment}
 */
export function placeholderView(label) {
  const fragment = document.createDocumentFragment();
  fragment.append(el("h1", undefined, label));
  const card = el("div", "card stopped");
  card.append(el("p", undefined, "This view has not been built yet."));
  card.append(
    el(
      "p",
      "detail",
      "The client shell and the status view are issue #536; the remaining views are #537. The same data is on the server-rendered pages in the meantime.",
    ),
  );
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
