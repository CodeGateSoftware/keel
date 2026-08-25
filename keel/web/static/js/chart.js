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
  return points.map(/** @param {any} point */ (point) => [point.x, point.y].join(",")).join(" ");
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
  });

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

  const figure = document.createElement("figure");
  figure.className = "chart";
  const caption = document.createElement("figcaption");
  caption.className = "note";
  caption.id = id;
  caption.textContent = curve.reading.display;
  figure.append(canvas, caption);
  return figure;
}
