// @ts-check

/**
 * SVG for the equity curve. **Contains no arithmetic, for the same reason `render.js` does not.**
 *
 * ── THE COORDINATES ARE COMPUTED IN PYTHON, AND THAT IS THE WHOLE DESIGN ─────────────────────
 *
 * A chart module normally does two things: it decides where the points go, and it draws them.
 * This one only draws. `keel.commands.insights.build_equity_curve` decides, and its own module
 * note carries the argument in full; the short version is that scaling a chart is a JUDGEMENT
 * about the data, not a drawing detail. Where the vertical axis starts is what makes a $3 wobble
 * look like a collapse or like a $3 wobble, and keel does not let a front-end decide whether a
 * number is bad (`payload.py`'s closed `state` vocabulary) for exactly the same reason.
 *
 * The second reason is the one that is checkable: normalising a series means subtracting,
 * dividing and comparing it, and this is the one place in the system where those happen in
 * IEEE-754 doubles over values that were exact `Decimal`s a moment earlier. With the geometry
 * arriving finished, `tests/web/test_client_assets.py` runs the SAME lexer-based arithmetic scan
 * over this file that it runs over `render.js` -- so "the client performs no arithmetic" (the
 * design spec's §Dependencies, the sentence that removes the need for a decimal library) stays a
 * property of the whole client rather than of one file in it.
 *
 * The rules that keep that lexer small apply here too and are not style: **no template literals
 * and no regular-expression literals.** `render.js`'s module docstring explains both. `Array
 * .join` is what builds every attribute string below, because `+` is unavailable and it would be
 * the wrong tool anyway.
 *
 * ── WHY A LIBRARY WAS NOT CONSIDERED FOR LONG ────────────────────────────────────────────────
 *
 * The design spec admits a dependency only where getting it wrong ourselves would undermine
 * keel's principles. An equity curve is a polyline. The whole of this file is `createElementNS`,
 * two `join`s and an accessible name; a charting library would be tens of thousands of lines
 * inside `js/external/`, would have to survive `default-src 'self'` by being vendored, and would
 * bring its own opinion about axis scaling -- the exact decision this design just took the
 * trouble to move into Python.
 *
 * ── THE TEXT EQUIVALENT IS NOT OPTIONAL, AND IT IS NOT WRITTEN HERE ──────────────────────────
 *
 * A chart is data made visible; a reader who cannot see it must be told the same thing. Two
 * equivalents ship and neither is a fallback for the other:
 *
 *   1. **The `<figcaption>` below**, which is also the SVG's accessible name via
 *      `aria-labelledby` -- one string, one element, so the sentence a reader hears and the
 *      sentence a reader sees cannot drift apart. It is `curve.reading.display`, written by
 *      `payload.equity_curve_payload`, because summarising a curve is a judgement too.
 *   2. **The table `render.js` puts beside the chart**, for anyone who wants the figures rather
 *      than the shape.
 *
 * ── #602: A CURSOR LEGEND AND A TRADE HIGHLIGHT, STILL PLACED RATHER THAN DECIDED ─────────────
 *
 * `equityChart` now also builds two overlays, both hidden until `main.js` has something to show
 * in them:
 *
 *   * **The cursor legend** (`showTradeAt`) -- a vertical guideline plus a readout of the one
 *     point nearest the pointer. "Nearest" is a comparison over pixel positions, which is exactly
 *     the arithmetic this file is not allowed to do, so `main.js` (which owns interface
 *     arithmetic -- see `tests/web/test_client_assets.py::_DERIVATION_FREE`'s own note on why it
 *     is not in that list) finds the point and hands this file the one to draw. What this file
 *     reads off it is `display` strings and the bare, non-`Field` position `x` -- never `.value`,
 *     the same rule `render.js` keeps.
 *
 *   * **The trade highlight** (`highlightTrade`) -- the segment a hovered journal row drew on the
 *     curve, and the two points that bound it. keel's journal has no order-level breakdown of a
 *     closed trade (`JournalEntry` is one aggregated entry fill and one exit fill, not a list of
 *     orders), so "highlight its orders" is read here as highlighting the two EQUITY POINTS that
 *     bound the trade's own contribution to the curve -- the running total just before it closed
 *     and just after -- which is the nearest thing keel's data model has to an entry and an exit.
 *     That reading is recorded here and in the PR description, not left for a reader to guess.
 *
 *     Coloured by `point.pnl.state`, which `payload.money(signed=True)` already set from the
 *     trade's own sign -- no judgement made here. **Colour is never the only signal**: a losing
 *     segment is also dashed and its end markers point down rather than up, so the distinction
 *     survives greyscale, e-ink and red-green colour deficiency exactly as `payload.py`'s
 *     ▲/▼ glyphs already do for the figures beside it (#532).
 *
 * Both overlays live inside the viewBox-scaled `<svg>` except the legend's text, which is an
 * ordinary HTML `<div>` overlaid on the figure: SVG text sized in the chart's internal 1000x300
 * user units would grow or shrink with `main.js`'s zoom, and a legend that resizes itself while
 * the reader is trying to read it would be worse than one that does not move at all.
 */

/**
 * @typedef {import("./api.js").Field} Field
 */

/** The SVG namespace. `createElement` produces an inert HTML element for these tags. */
const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * An SVG element with attributes.
 *
 * Attributes are set with `setAttribute` and never through properties: SVG geometry attributes
 * are not reflected as writable DOM properties the way an HTML `id` is, and assigning `node.x1`
 * silently does nothing.
 *
 * @param {string} tag
 * @param {Record<string, string>} attributes
 * @returns {SVGElement}
 */
function svg(tag, attributes) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

/**
 * The `points` attribute of a polyline: `"x,y x,y x,y"`.
 *
 * @param {any[]} points
 * @returns {string}
 */
function polylinePoints(points) {
  return polylineOn(points, "y");
}

/**
 * The `points` attribute of a polyline over one named vertical coordinate.
 *
 * `key` picks which line a point contributes to -- its equity (`"y"`), the high-water mark in
 * force at that instant (`"hwm_y"`), or the drawdown floor beneath it (`"dd_floor_y"`). All
 * three arrive finished from `build_equity_series`; choosing between them is a lookup, not the
 * arithmetic this file is not allowed to do.
 *
 * @param {any[]} points
 * @param {string} key
 * @returns {string}
 */
function polylineOn(points, key) {
  return points.map(/** @param {any} point */ (point) => [point.x, point[key]].join(",")).join(" ");
}

/**
 * The two end markers a highlighted trade wears, pointing the direction its `pnl` went.
 *
 * Plain `<path>`s, not `<symbol>`s: a `<use>` of a symbol maps the symbol's OWN viewBox onto the
 * `x`/`y`/`width`/`height` it is given, which is a second coordinate transform -- placing one at
 * a point would need the symbol's half-width subtracted from that point's `x` to centre it, and
 * that subtraction is arithmetic this file does not do. A `<use>` of a bare shape has no viewBox
 * to map: `x`/`y` are a plain translation of the shape's own coordinates, so a triangle drawn
 * centred on `(0, 0)` lands centred on whatever point `main.js` gives it, with nothing computed
 * here at all.
 *
 * @returns {SVGElement}
 */
function markerDefs() {
  const defs = svg("defs", {});
  defs.append(svg("path", { id: "mk-up", d: "M0,-7 L7,7 L-7,7 Z" }));
  defs.append(svg("path", { id: "mk-down", d: "M0,7 L7,-7 L-7,-7 Z" }));
  return defs;
}

/**
 * The trade-highlight overlay: the entry/exit markers and the segment between them, all hidden
 * until `highlightTrade` has a trade to show.
 *
 * `.hide` rather than the SVG `hidden` attribute or an inline `display`, for one reason: it is
 * the same mechanism `showTradeAt` below uses for the cursor legend, and `main.js` toggles both
 * the same way -- one CSS hook, not two conventions for "nothing to show right now".
 *
 * @returns {SVGElement}
 */
function highlightOverlay() {
  const group = svg("g", { class: "highlight hide", "aria-hidden": "true" });
  group.append(svg("line", { class: "segment" }));
  group.append(svg("use", { class: "marker entry", href: "#mk-up" }));
  group.append(svg("use", { class: "marker exit", href: "#mk-up" }));
  return group;
}

/**
 * The cursor-legend overlay: a guideline inside the `<svg>` (so it tracks the data, not the
 * pixels) plus a readout beside it that is ordinary HTML -- see the module note on why its text
 * is not SVG.
 *
 * @returns {{line: SVGElement, legend: HTMLElement}}
 */
function cursorOverlay() {
  const line = svg("line", { class: "cursor-line hide", "aria-hidden": "true" });
  const legend = document.createElement("div");
  legend.className = "legend hide";
  legend.setAttribute("aria-hidden", "true");
  const at = legendRow("legend-at");
  const where = legendRow("legend-where");
  const pnl = legendRow("legend-pnl");
  const cumulative = legendRow("legend-cumulative");
  legend.append(at, where, pnl, cumulative);
  return { line, legend };
}

/**
 * A `<p>`, for the legend above -- `render.js::el` is not imported, since importing from the
 * module that imports `equityChart` would be circular.
 *
 * @param {string} className
 * @returns {HTMLElement}
 */
function legendRow(className) {
  const node = document.createElement("p");
  node.className = className;
  return node;
}

/**
 * A trade's outcome, as the one word this file's own overlay classes key off.
 *
 * Not `render.js`'s `STATE_CLASS` table -- that maps the API's five `state` words onto #532's
 * text classes (`good`/`warn`/`bad`/`muted`), and importing it here would be the same circular
 * import `legendRow` above avoids. `point.pnl.state` (`payload.money(signed=True)`'s own
 * judgement) is only ever `"good"`, `"bad"` or `"neutral"` -- a P&L figure has no `warn` or
 * `unknown` -- so the three words this module's CSS understands are exactly the three `money`
 * can send.
 *
 * @param {any} point
 * @returns {string}
 */
function outcomeOf(point) {
  const state = point && point.pnl && point.pnl.state;
  return state === "good" || state === "bad" ? state : "neutral";
}

/**
 * The equity curve, as a `<figure>` — or `null` when there is no curve to draw.
 *
 * `null` rather than an empty chart, and the caller renders its own empty state. An axis with no
 * line on it is a picture of a track record that is flat, and a deployment with no closed trades
 * does not have a flat track record: it has none. `payload.equity_curve_payload` makes the same
 * distinction in words, and the two must agree.
 *
 * @param {any} curve  `/api/journal`'s `data.curve`, or anything else.
 * @param {string} id  the id given to the caption, which names the chart for a screen reader.
 * @returns {HTMLElement|null}
 */
/**
 * The account-equity series over time (#698), drawn ABOVE the closed-trade curve.
 *
 * ── WHY IT IS A SECOND CHART AND NOT A REPLACEMENT ───────────────────────────────────────────
 *
 * `equityChart` plots cumulative net P&L over closed TRADES, on a trade-order axis, and
 * `build_equity_curve` argues for that axis on its own terms: a quiet week must not carry the
 * visual weight of fifty trades when the subject is statistical expectancy. This plots what the
 * ACCOUNT was worth every cycle, traded or not, where the quiet week is the information. Two
 * questions, two charts, stacked -- portfolio reality above, expectancy below.
 *
 * ── ONE POLYLINE PER SEGMENT, NEVER ONE PER SERIES ───────────────────────────────────────────
 *
 * Paper and live are unrelated accounts that share a database and flip within it. The payload
 * hands this file `segments`, already split by `build_equity_series`, precisely so that joining
 * them is not something this file can do by accident: a single line from a $10,000 paper account
 * to a $250 live one would draw a collapse that never happened. The modes are told apart by
 * DASH as well as by colour, the same rule the losing-trade segment below follows -- paper is
 * dashed because it is the synthetic one.
 *
 * ── THE TWO OVERLAYS ARE READ, NOT DERIVED ───────────────────────────────────────────────────
 *
 * Each point carries `hwm_y` (the rail-11 high-water mark in force when the agent acted) and
 * `dd_floor_y` (the equity at which rail 11 starts vetoing entries). Both are computed in
 * Python from the recorded row, never here -- and `dd_floor_y` is `null` rather than `"0"` when
 * the rail setting is unknown, because a zero coordinate is the TOP of the box and would draw a
 * ceiling that is in force above every reading.
 *
 * ── NO ZOOM, NO PAN, NO CURSOR LEGEND ────────────────────────────────────────────────────────
 *
 * `main.js`'s #602 gestures bind to `svg.curve`, and this canvas is `svg.series` on purpose: the
 * two would otherwise fight over `contentNode.querySelector("svg.curve")`, which takes the FIRST
 * match and would find this chart instead of the one the gestures were written for. Adding them
 * here is a separate piece of work with its own arithmetic to place in `main.js`.
 *
 * @param {any} series  `/api/insights`'s `data.equity_series`.
 * @param {string} id  the `<figcaption>` id this chart is named by.
 * @returns {HTMLElement|null}
 */
export function equitySeriesChart(series, id) {
  if (!series || !Array.isArray(series.segments) || series.segments.length === 0) return null;

  const canvas = svg("svg", {
    viewBox: ["0", "0", series.width, series.height].join(" "),
    preserveAspectRatio: "none",
    class: "series",
    role: "img",
    "aria-labelledby": id,
  });

  for (const segment of series.segments) {
    // The floor first, then the ceiling, then the account: the equity line is the subject and
    // sits over both rails rather than under them.
    const floored = segment.points.filter(
      /** @param {any} point */ (point) => point.dd_floor_y !== null,
    );
    // `!== 0`, never `> 0`: `test_render_never_judges_a_value_itself` bans relational operators
    // in this file, and an emptiness check has no business needing an ordering anyway.
    if (floored.length !== 0) {
      canvas.append(
        svg("polyline", {
          class: ["floor", segment.mode].join(" "),
          points: polylineOn(floored, "dd_floor_y"),
        }),
      );
    }
    canvas.append(
      svg("polyline", {
        class: ["hwm", segment.mode].join(" "),
        points: polylineOn(segment.points, "hwm_y"),
      }),
    );
    const line = svg("polyline", {
      class: ["line", segment.mode].join(" "),
      points: polylinePoints(segment.points),
    });
    // Names the account this line belongs to on hover. Redundant for a screen reader -- the
    // `role="img"` above flattened this subtree and `aria-labelledby` names the modes in a
    // sentence -- which is why it is a convenience and not the accessible name.
    const lineTip = svg("title", {});
    lineTip.textContent = [segment.mode, "equity"].join(" ");
    line.append(lineTip);
    canvas.append(line);

    // A marker per reading. Same reason `equityChart` draws them: a segment of one point is a
    // polyline with nothing to draw between, so the first cycle after an upgrade -- or the
    // first live cycle after a flip -- would otherwise render as empty space.
    for (const point of segment.points) {
      const dot = svg("circle", {
        class: ["dot", segment.mode].join(" "),
        cx: point.x,
        cy: point.y,
        r: "3",
      });
      const tip = svg("title", {});
      tip.textContent = [point.at.display, point.equity.display, point.mode].join(" · ");
      dot.append(tip);
      canvas.append(dot);
    }
  }

  const figure = document.createElement("figure");
  figure.className = "chart";
  const caption = document.createElement("figcaption");
  caption.className = "note";
  caption.id = id;
  caption.textContent = series.reading.display;
  figure.append(canvas, caption);
  return figure;
}

export function equityChart(curve, id) {
  if (!curve || !Array.isArray(curve.points) || curve.points.length === 0) return null;

  const canvas = svg("svg", {
    viewBox: ["0", "0", curve.width, curve.height].join(" "),
    // The box is a fixed internal grid, not a size: `preserveAspectRatio="none"` lets the card's
    // width decide the width and the CSS height decide the height, which is what makes the chart
    // responsive without a resize listener, a `ResizeObserver` or a redraw.
    preserveAspectRatio: "none",
    class: "curve",
    // `role="img"` collapses the whole drawing into one node for assistive technology. Without
    // it a reader walks the polyline, the baseline and every circle as separate graphics objects
    // and is told nothing by any of them.
    role: "img",
    "aria-labelledby": id,
    // #602: the one keyboard path to the wheel-zoom and drag-to-pan `main.js` owns -- arrow keys
    // pan, `+`/`-` zoom, `0` resets, handled there for the same arithmetic reason the pointer
    // gestures are. `role="img"` is unaffected by a focusable, keyboard-operable `img`: it still
    // flattens this subtree for assistive technology, which reads `aria-labelledby`'s sentence
    // instead and never reaches a key this attribute adds a path for.
    tabindex: "0",
  });

  canvas.append(markerDefs());

  // The zero line, drawn first so the curve sits over it. It is the only gridline: on a
  // CUMULATIVE net P&L chart it is the line between having made money and having lost it, and
  // every other horizontal rule would be decoration competing with the one that means something.
  canvas.append(
    svg("line", {
      class: "baseline",
      x1: "0",
      y1: curve.baseline_y,
      x2: curve.width,
      y2: curve.baseline_y,
    }),
  );

  canvas.append(svg("polyline", { class: "line", points: polylinePoints(curve.points) }));

  // A marker per trade. Not decoration: a curve of one point is a polyline with nothing to draw
  // between, so without these the first closed trade of a deployment's life renders as an empty
  // box. They also give a mouse a target for the native `<title>` tooltip below.
  for (const point of curve.points) {
    const dot = svg("circle", { class: "dot", cx: point.x, cy: point.y, r: "4" });
    // `<title>` inside a shape is SVG's own tooltip and needs no script. It is redundant for a
    // screen reader (the `role="img"` above already flattened this subtree), which is why the
    // figures are in the table beside the chart as well.
    const tip = svg("title", {});
    tip.textContent = [point.at.display, point.cumulative.display].join(" · ");
    dot.append(tip);
    canvas.append(dot);
  }

  // The two overlays (#602), drawn LAST so a highlighted trade and the cursor guideline sit over
  // every dot and over the curve itself rather than under them.
  canvas.append(highlightOverlay());
  const cursor = cursorOverlay();
  canvas.append(cursor.line);

  const figure = document.createElement("figure");
  figure.className = "chart";
  const caption = document.createElement("figcaption");
  caption.className = "note";
  caption.id = id;
  caption.textContent = curve.reading.display;
  figure.append(canvas, cursor.legend, caption);
  return figure;
}

/**
 * Show the cursor legend at `point`, or hide it when `point` is `null`.
 *
 * `main.js` has already decided which point (if any) is nearest the pointer; everything here is
 * placement. `point.x` is a bare position (not a `Field`), so moving the guideline reads no
 * `.value`; the four readout lines place `.display` strings only.
 *
 * @param {HTMLElement} figure  what `equityChart` returned.
 * @param {any} point  one of `curve.points`, or `null` to hide the legend.
 */
export function showTradeAt(figure, point) {
  const canvas = figure.querySelector("svg.curve");
  const line = figure.querySelector(".cursor-line");
  const legend = figure.querySelector(".legend");
  if (!(canvas instanceof SVGSVGElement) || !(line instanceof SVGElement)) return;
  if (!(legend instanceof HTMLElement)) return;

  if (!point) {
    line.classList.add("hide");
    legend.classList.add("hide");
    return;
  }

  // The guideline spans the box's own height, read off the `viewBox` rather than off `point.y`:
  // a trade's own running total can sit anywhere in the box, and a guideline that stopped at
  // `point.y` would draw a different length for every point it is shown at.
  const height = canvas.viewBox.baseVal.height;
  line.classList.remove("hide");
  line.setAttribute("x1", point.x);
  line.setAttribute("x2", point.x);
  line.setAttribute("y1", "0");
  line.setAttribute("y2", height);

  legend.classList.remove("hide");
  const at = legend.querySelector(".legend-at");
  const where = legend.querySelector(".legend-where");
  const pnl = legend.querySelector(".legend-pnl");
  const cumulative = legend.querySelector(".legend-cumulative");
  if (at) at.textContent = point.at.display;
  if (where) where.textContent = [point.product_id, point.rule_name || "—"].join(" · ");
  if (pnl) {
    pnl.textContent = ["trade:", point.pnl.display].join(" ");
    pnl.className = ["legend-pnl", outcomeOf(point)].join(" ");
  }
  if (cumulative) cumulative.textContent = ["total:", point.cumulative.display].join(" ");
}

/**
 * Highlight one trade's contribution to the curve, or clear the highlight when `point` is `null`.
 *
 * `previous` is the point just before `point` on the curve -- the running total right before this
 * trade closed, which is the nearest thing to "where it entered" that a cumulative-P&L curve has
 * (see the module note). It is `null` for the curve's first point, which has nothing before it:
 * the segment and the entry marker both stay hidden, and only the exit marker shows, at `point`
 * alone.
 *
 * @param {HTMLElement} figure  what `equityChart` returned.
 * @param {any} previous  the point before `point`, or `null`.
 * @param {any} point  the hovered trade's own point, or `null` to clear the highlight.
 */
export function highlightTrade(figure, previous, point) {
  const group = figure.querySelector(".highlight");
  if (!(group instanceof SVGElement)) return;

  if (!point) {
    group.classList.add("hide");
    return;
  }
  group.classList.remove("hide");

  const outcome = outcomeOf(point);
  const segment = group.querySelector(".segment");
  // Named for the DOM class they carry, not for `payload.py`'s "journal entry" -- a plain
  // `entry`/`exit` pair here would read, at a glance, like the two halves of a trade record
  // rather than the two `<use>` elements they are.
  const entryMarker = group.querySelector(".entry");
  const exitMarker = group.querySelector(".exit");
  const marker = outcome === "bad" ? "#mk-down" : "#mk-up";

  if (exitMarker) {
    exitMarker.setAttribute("x", point.x);
    exitMarker.setAttribute("y", point.y);
    exitMarker.setAttribute("href", marker);
    exitMarker.setAttribute("class", ["marker", "exit", outcome].join(" "));
  }

  if (!previous) {
    if (segment) segment.setAttribute("class", "segment hide");
    if (entryMarker) entryMarker.setAttribute("class", "marker entry hide");
    return;
  }

  if (segment) {
    segment.setAttribute("x1", previous.x);
    segment.setAttribute("y1", previous.y);
    segment.setAttribute("x2", point.x);
    segment.setAttribute("y2", point.y);
    segment.setAttribute("class", ["segment", outcome].join(" "));
  }
  if (entryMarker) {
    entryMarker.setAttribute("x", previous.x);
    entryMarker.setAttribute("y", previous.y);
    entryMarker.setAttribute("href", marker);
    entryMarker.setAttribute("class", ["marker", "entry", outcome].join(" "));
  }
}
