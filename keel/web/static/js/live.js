// @ts-check

/**
 * The `EventSource` subscription and its reconnect behaviour. **The only place `EventSource`
 * appears in this client**, the way `api.js` is the only place `fetch` does, and
 * `tests/web/test_client_assets.py::test_no_client_module_opens_a_second_kind_of_connection`
 * holds both properties by naming this file as the single exception.
 *
 * ── WHAT THIS BUYS OVER THE POLL THAT WAS ALREADY THERE ──────────────────────────────────────
 *
 * #536 shipped a fifteen-second poll and it is still here. The stream is not a faster poll; it
 * answers a question polling cannot:
 *
 *   **A dead server and an unchanged one look identical to a poll.** A `fetch` that fails is
 *   discovered whenever the next one is due, and until then the page shows figures that may be
 *   hours out of date beside a timestamp that says when it last *asked*. A connection has a state
 *   of its own -- `EventSource` fires `error` the moment the socket goes -- so the page can say
 *   "I have lost contact" within a heartbeat instead of within a poll.
 *
 * The agent runs DAILY, so this was never about a ticking feed. `keel/web/events.py` carries the
 * server half of that argument: a tick is an envelope whose `data` is one revision marker, with
 * no figures on it at all, and the client re-reads through `api.js` when the marker moves.
 *
 * ── THE THREE STATES A SUBSCRIBER IS TOLD ABOUT, AND WHY NONE OF THEM IS SILENCE ─────────────
 *
 * Every path below ends in a `Reading` handed to the same callback, so the page has one way to
 * learn about the world and the banner cannot be left holding a stale sentence:
 *
 *   1. **A tick arrived.** The envelope, read by `api.readingFrom` -- the same function the
 *      `fetch` path uses, so a tick and a poll can never disagree about what a document means.
 *   2. **The connection dropped.** `EventSource` reconnects on its own, which is exactly why this
 *      has to be SAID: an interface that silently retries is one where "nothing has happened for
 *      an hour" and "nothing has reached me for an hour" look the same. A minted `bad` reading,
 *      through `api.stopped` so the wording and the judgement match the `fetch` path's.
 *   3. **Ticks stopped without an error.** The watchdog. A socket that is open but silent is a
 *      real state -- a suspended laptop, a machine that slept, a proxy holding a connection it
 *      is no longer feeding -- and it fires no event at all. `STALE_AFTER_MS` is what turns that
 *      into the second case rather than into a page that quietly stops updating.
 *
 * ── WHAT IS NOT HERE ─────────────────────────────────────────────────────────────────────────
 *
 * **No backoff, and no reconnect loop.** `EventSource` owns reconnection; the server sends a
 * `retry:` field telling it how long to wait (`events.RETRY_MS`). Writing one here would mean
 * two schedulers racing over the same socket. The reversal condition: if the server ever needs
 * to shed load, it says so with a `204`, which `EventSource` treats as "stop" -- and at that
 * point a client-side decision about whether to come back becomes a real question.
 *
 * **No arithmetic that touches data.** The only numbers in this file are millisecond constants.
 * The revision marker is compared with `!==` and never parsed: `events.revision`'s only contract
 * is that it CHANGES, and anything more would be this file inventing a meaning for it.
 */

import { readingFrom, stopped } from "./api.js";

/**
 * @typedef {import("./api.js").Reading} Reading
 */

/** The stream's path. One string, spelled once, matching `server.EVENTS_PATH`. */
const EVENTS_PATH = "/api/events";

/** The server's event name for a tick, matching `events.TICK_EVENT`. */
const TICK_EVENT = "tick";

/**
 * How long without a tick before the connection is called stale, in milliseconds.
 *
 * Twenty seconds against `events.HEARTBEAT_SEC`'s five, so three heartbeats have to be missed
 * before the page says anything. One missed heartbeat is a scheduler hiccup and reporting it
 * would make the banner flicker on a healthy machine; three in a row is not.
 *
 * It is deliberately longer than the `retry:` delay too (two seconds), so an ordinary
 * end-of-stream reconnection -- which happens on purpose every ten minutes, see
 * `events.MAX_STREAM_SEC` -- completes long before this fires.
 *
 * @type {number}
 */
const STALE_AFTER_MS = 20000;

/**
 * What the banner says when the stream is gone. Two sentences' worth in one line: what happened,
 * and that the page is doing something about it -- because "lost contact" alone reads as a
 * failure requiring a reload, and a reload is the one thing that is not needed.
 *
 * @type {string}
 */
const DROPPED = "lost contact with keel — the page is trying to reconnect";

/**
 * And when the socket is open but nothing is coming down it. A DIFFERENT sentence from `DROPPED`
 * on purpose: they are different faults with different fixes (a stopped server against a sleeping
 * machine or a stalled connection), and collapsing them would send someone to restart a process
 * that never stopped.
 *
 * @type {string}
 */
const STALLED = "no update from keel for a while — the connection may have stalled";

/**
 * Whether this browser can subscribe at all.
 *
 * Checked rather than assumed, and the caller acts on the answer: `main.js` keeps rebuilding the
 * view on its poll when this is `false`, so a browser with no `EventSource` gets a page that is
 * fifteen seconds behind rather than a page that never refreshes. Every browser that runs ES
 * modules has had `EventSource` for a decade; this costs one line and removes the class of bug
 * where a missing API turns into a blank dashboard.
 *
 * @returns {boolean}
 */
export function available() {
  return typeof EventSource === "function";
}

/**
 * Subscribe to the server's tick stream.
 *
 * @param {(reading: Reading, revision: string) => void} onReading
 *   Called for every tick and for every loss of contact. `revision` is `""` when there is no tick
 *   behind the reading -- a dropped connection carries no revision, and passing the LAST one
 *   would tell the caller "nothing has changed" on the strength of an answer that never arrived.
 * @returns {() => void}  unsubscribes and closes the socket.
 */
export function subscribe(onReading) {
  if (!available()) return () => {};

  const source = new EventSource(EVENTS_PATH, { withCredentials: true });
  /** @type {number} */
  let watchdog = 0;
  let closed = false;

  /** Report a loss of contact, once, with the sentence that fits the cause. @param {string} why */
  function lost(why) {
    window.clearTimeout(watchdog);
    if (closed) return;
    onReading(
      {
        as_of: "",
        engine: stopped(why),
        data: null,
        // No `error` document: nothing refused this and nothing answered it. `stoppedView` reads
        // a null `error` as "the server answered normally and has no deployment", which is why
        // the ENGINE field carries the whole sentence instead.
        error: null,
        sort: null,
      },
      "",
    );
  }

  /** Restart the silence timer. Called on every frame that arrives, whatever it says. */
  function heard() {
    window.clearTimeout(watchdog);
    watchdog = window.setTimeout(() => lost(STALLED), STALE_AFTER_MS);
  }

  source.addEventListener("open", heard);

  source.addEventListener(TICK_EVENT, (event) => {
    heard();
    let document_;
    try {
      document_ = JSON.parse(/** @type {MessageEvent} */ (event).data);
    } catch (cause) {
      // Something is answering on this port that is not keel -- the same conclusion `api.read`
      // draws from an unparseable body, and worth saying rather than swallowing, because a
      // silently ignored frame is indistinguishable from a server that has gone quiet.
      lost("Something on this port is streaming, but not keel's events");
      return;
    }
    // `true` for `ok`: a frame that arrived is a frame the server chose to send. There is no 4xx
    // over an event stream -- a refusal happens before the stream opens and surfaces as `error`.
    const reading = readingFrom(document_, true, "200");
    const revision =
      reading.data && typeof reading.data.revision === "string" ? reading.data.revision : "";
    onReading(reading, revision);
  });

  source.addEventListener("error", () => {
    // `error` fires both when a connection drops (readyState CONNECTING -- the browser is already
    // reconnecting) and when it is given up on (readyState CLOSED). Both are a loss of contact
    // and both are reported: the difference is whether it comes back on its own, which the next
    // tick answers better than a second sentence here would.
    lost(DROPPED);
  });

  return () => {
    closed = true;
    window.clearTimeout(watchdog);
    source.close();
  };
}
