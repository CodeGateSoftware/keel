"""The JSON API (#534) -- routing, one bounded read per endpoint, and no rendering at all.

This module is the READ half of `keel/web/`: which report an endpoint needs, which rows a query
string asks to be ordered by, and what a client is told when there is no deployment to read. It
formats nothing. Every string a user will see was written by `keel/web/payload.py`, which is the
one place a `Decimal` becomes text and the one place the closed `state` vocabulary is spelled --
`tests/web/test_api.py::test_the_routing_layer_formats_nothing` fails the build on a `format(...)`
call or a format spec appearing here, because the second money renderer is never the one with
`_plain`'s no-exponent guarantee in it.

**Reads only.** Not one route below answers a POST. The write surface is still
`keel.commands.setup.ACTIONS` reached through `SETUP_ACTION_PREFIX`, still behind
`X-Keel-Client: 1` for anything under `API_PREFIX`, and this issue added nothing to either --
`test_no_api_route_answers_a_post` asserts that for every route in the table below rather than for
a sample of them.

**Why the deployment's state is read on EVERY endpoint, including the three that do not need it.**
`engine` is a uniform field so that #536's single `fetch` wrapper can render "keel isn't running"
from whatever response it happens to hold, without a table of which endpoints carry the word.
Measured cost: `keel.commands.setup.inspect` is 3.6 ms per call on this machine against a migrated
database, against 0.07 ms for `gather_status` -- so the liveness probe is roughly fifty times the
report it accompanies, and still small next to a loopback HTTP round trip. If a client is ever
written that polls `/api/config` in a loop, the fix is to cache the `DeploymentState` for the life
of one response (it already is) or per second -- not to make the field optional, because an
optional field puts the branch back on the client.

**Where the sort is, and where it is not.** Deciding WHICH column a query string asked for, and
refusing one that does not exist, is routing and lives here. The ordering itself -- `Decimal`
comparison over `Field.value` -- is `payload.order_rows`, next to the code that wrote those
strings and under the same contract. A `float()` on a wire value would be the whole money
guarantee dying in one expression, and `test_rule_6_holds_in_the_api_layer_too` runs #533's own
AST scan over this file for exactly that reason.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from keel.web import payload
from keel.web.security import csrf_token

if TYPE_CHECKING:  # pragma: no cover - typing only
    from keel.web.server import ServeConfig

#: Journal rows served when a caller names no `limit`. The same cap the HTML insights page uses,
#: so the two front-ends answer "how has this been going" with the same amount of history.
DEFAULT_JOURNAL_LIMIT = 50

#: The ceiling on `?limit=`. A caller-supplied row count is a memory and time primitive against a
#: server with no proxy in front of it, and a journal is not a bulk export -- `keel insights
#: journal` is, and it runs in the operator's own process against their own machine's limits.
MAX_JOURNAL_LIMIT = 1000

#: The two directions, and no third spelling. `desc`/`descending`/`down` would all have to be
#: accepted forever once accepted once, and a client reading `sort.direction` back off the
#: response needs one word to compare against.
DIRECTIONS: tuple[str, ...] = ("asc", "desc")


# -- the reads ------------------------------------------------------------------------------------
#
# These four moved here from `keel/web/server.py` when this module arrived, unchanged. They are
# the seam BOTH front-ends read keel through -- the HTML pages and the JSON endpoints -- and a
# copy in each would be two places deciding whether a page may migrate a live database. The
# direction of the dependency is one-way on purpose: `server` imports `api`, `api` imports nothing
# from `server` at runtime, so there is no cycle to reason about.


def open_repo(db_path: str) -> Any:
    """A plain connection -- deliberately WITHOUT `migrate`.

    Every CLI command migrates on the way in, which is right for a command: it runs once, and a
    schema behind the code is a thing to fix rather than to fail on. It is wrong here. These
    pages auto-reload every 15 seconds, so migrating per request would have a view that calls
    itself read-only take a write lock on the deployment database four times a minute -- against
    a database the agent may be mid-cycle on. `server.ensure_schema` does it ONCE, at bind time,
    before anything is served."""
    from keel.data.db import connect
    from keel.data.repository import Repository

    return Repository(connect(db_path))


def load_config(config_path: str) -> Any:
    """`load_config` only -- deliberately NOT `_common._load_cfg`, which also calls
    `configure_logging` and `bind_venue`. Those are process-entry side effects; re-applying them
    on every page load would have the web UI quietly reconfiguring the running deployment's
    logging."""
    from keel.config import load_config as _load

    return _load(config_path)


def deployment_state(cfg: ServeConfig) -> Any:
    from keel.commands.setup import inspect

    return inspect(cfg.config_path, cfg.db_path)


def close_repo(repo: Any) -> None:
    conn = getattr(repo, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # pragma: no cover - a close that fails leaks nothing that matters
            pass


# -- refusals -------------------------------------------------------------------------------------


class ApiRefusal(Exception):
    """A request this API understood and declined -- a column that does not exist, a limit out of
    range. Carried as an exception rather than returned, so a reader can refuse from wherever it
    discovers the problem without every caller in between having to pass a failure along.

    Distinct from an unexpected exception, which becomes a 500: this one is the API stating a rule
    the caller broke, and its `detail` is written to be shown to whoever wrote the caller."""

    def __init__(self, status: int, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail


# -- the readers ----------------------------------------------------------------------------------


def _status_report(cfg: ServeConfig, now_ts: int) -> Any:
    """`gather_status`'s frozen report, over its own connection.

    A named module-level function rather than an inline call inside `read_status` so a test can
    replace it -- pinning that a report which cannot be built becomes a stated 500 rather than a
    200 with an empty payload, which is the failure mode this whole envelope exists to prevent."""
    from keel.commands.status import gather_status

    repo = open_repo(cfg.db_path)
    try:
        return gather_status(repo, load_config(cfg.config_path), now_ts=now_ts)
    finally:
        close_repo(repo)


def read_config(cfg: ServeConfig, _query: Query, _state: Any, _now_ts: int) -> dict[str, Any]:
    """The running build, and the deployment that build is serving (#597).

    Still reads NO database -- it answers on a machine with nothing set up, which is why its
    route is `needs_database=False`. The deployment half arrives as this process's own
    arguments (`cfg.db_path`, `cfg.config_path`) plus one read of the config FILE, which opens
    no database and forks no subprocess, unlike the `inspect` probe the envelope has already run
    for `engine` by the time this returns.

    **`_mode` degrades rather than raising, and the whole shell depends on that.** The client
    boots from this one endpoint -- worker registration, docs links, the footer build line and
    the header's mode badge -- so a config that is missing (a first run) or unreadable (a hand
    edit gone wrong) must cost the page its badge, not its boot. `payload.config_payload`
    records the read-only boundary: nothing in this package writes `auto_trade.mode`.
    """
    return payload.config_payload(
        cfg.build_info,
        describe=cfg.build,
        mode=_auto_trade_mode(cfg.config_path),
        db_path=cfg.db_path,
        config_path=cfg.config_path,
    )


def _auto_trade_mode(config_path: str) -> str:
    """The served config's own word for `auto_trade.mode`, or `""` when it cannot be read.

    The config file is re-read per request rather than resolved at bind time on purpose: mode
    changes are config-file edits by design, and `read_status` re-reads the same file on every
    poll already -- a badge that showed a mode the served config no longer declares would be
    worse than one that says nothing.

    A report of absence rather than an exception, for every way a config can fail here: the
    file is missing (a first run), the YAML is malformed, or `load_config` refused a value
    (`ConfigError`). Naming the narrow ones would leave this reader deciding which failure is
    which, and the caller's response is the same for all three.
    """
    try:
        config = load_config(config_path)
    except Exception:  # every unreadable config is one answer here: the mode is unknown
        return ""
    return str(getattr(getattr(config, "auto_trade", None), "mode", "") or "")


def read_status(cfg: ServeConfig, _query: Query, _state: Any, now_ts: int) -> dict[str, Any]:
    return payload.status_payload(_status_report(cfg, now_ts))


def read_setup(cfg: ServeConfig, _query: Query, state: Any, _now_ts: int) -> dict[str, Any]:
    """The first-run checklist -- the ONE deployment-reading endpoint that must answer when there
    is no deployment, because it is the thing that says how to make one. `needs_database=False`
    for that reason: an endpoint that refused to answer until a deployment existed would refuse
    precisely the operator who has none.

    `state` is the `DeploymentState` the envelope already read for `engine`, passed in rather than
    re-inspected: one 3.6 ms probe per response, not two."""
    from keel.commands import jobs
    from keel.commands.setup import ACTIONS, NOT_AUTOMATED_YET

    return payload.setup_payload(
        state,
        actions=ACTIONS,
        not_automated=NOT_AUTOMATED_YET,
        job=jobs.status(),
        # The write token for this session, on the one endpoint whose view performs writes. Scoped
        # to that endpoint rather than put on `/api/config` (which every view reads at boot) so it
        # travels only to the page that needs it -- see `payload.setup_payload` for why it is in a
        # body at all, which was a reversal.
        csrf=csrf_token(cfg.token),
    )


def read_activity(cfg: ServeConfig, query: Query, _state: Any, _now_ts: int) -> dict[str, Any]:
    """The engine's own log, scoped.

    An unrecognised `?scope=` is NORMALISED rather than refused, unlike an unrecognised `?sort=`,
    and the difference is not an inconsistency. `normalise_scope` is the activity SERVICE's own
    function with the CLI's own default behind it, so refusing here would mean the browser and the
    terminal disagreeing about the same input -- and the resolved value is echoed back in
    `data.scope`, so a client can see that its input was changed. A sort column has no service, no
    default and no echo that would make a silent substitution visible, so it is refused."""
    from keel.commands.activity import (
        apply_scope,
        feed_from_lines,
        normalise_scope,
        read_log_window,
        resolve_log_path,
    )

    scope = normalise_scope(_first(query, "scope"))
    path = resolve_log_path(load_config(cfg.config_path))
    window = read_log_window(path)
    feed = feed_from_lines(window.lines, source=str(path), truncated=window.truncated)
    if window.status != "ok" and feed.status == "empty":
        # A read that failed and a window that held nothing are different facts; the reader's
        # status is the more specific one and must not be flattened into "empty".
        feed = feed_from_lines((), source=str(path))
    return payload.activity_payload(apply_scope(feed, scope, now_ts=time.time()))


def read_orders(cfg: ServeConfig, query: Query, _state: Any, _now_ts: int) -> dict[str, Any]:
    """The audit trail: what keel actually bought and sold, and at what price (#659).

    **No `mode` reaches the service, and none may.** `gather_orders` calls `get_orders()`
    unfiltered because each deployment book holds exactly one mode; a `?mode=` here would put
    that failure back through the query string, so this endpoint has no such parameter. The mode
    is on every row.

    `?scope=` is NORMALISED rather than refused, `activity`'s reasoning exactly: it is the
    SERVICE's own function with the CLI's own default behind it, and refusing here would have
    the browser and the terminal disagreeing about the same input. The resolved value is echoed
    back in `data.scope`. `?limit=` IS refused when it is not a whole number or is out of range,
    because a caller who asked for two thousand rows and silently got fifty has been told
    nothing -- unlike a scope, whose echo makes the substitution visible.
    """
    from keel.commands.orders import (
        DEFAULT_ORDERS_LIMIT,
        MAX_ORDERS_LIMIT,
        gather_orders,
        normalise_scope,
    )

    scope = normalise_scope(_first(query, "scope"))
    raw_limit = _first(query, "limit")
    limit = DEFAULT_ORDERS_LIMIT if not raw_limit else _whole_number(raw_limit, MAX_ORDERS_LIMIT)
    repo = open_repo(cfg.db_path)
    try:
        report = gather_orders(repo, now_ts=int(time.time()), scope=scope, limit=limit)
    finally:
        close_repo(repo)
    return payload.orders_payload(report)


def read_insights(cfg: ServeConfig, _query: Query, _state: Any, now_ts: int) -> dict[str, Any]:
    """The per-rule track records and the promotion-gate distances.

    The journal the HTML `/insights` page renders below these is `/api/journal` instead of a second
    table here. One sortable collection per endpoint keeps `?sort=` unambiguous without a
    `?table=` beside it, and it gives the journal somewhere to carry its own `?limit=` -- the cap
    the HTML page apologises for in a comment ("a cap, not a paginator")."""
    from keel.commands.insights import build_insights_report

    repo = open_repo(cfg.db_path)
    try:
        config = load_config(cfg.config_path)
        report = build_insights_report(repo, config, _status_report(cfg, now_ts), now_ts)
    finally:
        close_repo(repo)
    return payload.insights_payload(report)


def read_journal(cfg: ServeConfig, query: Query, _state: Any, now_ts: int) -> dict[str, Any]:
    """The trade journal, and the equity curve drawn from the SAME entries.

    `build_equity_curve` is called on `report.entries` -- not on the ledger, and not on a second
    read -- so the chart and the table under it describe one list of trades. A curve built from
    its own query could disagree with the rows beside it after a `?limit=`, and a chart that
    disagrees with the table below it is worse than no chart.

    The curve is NOT part of `route.collection`, so `?sort=` reorders `entries` and leaves the
    curve alone. That is deliberate: the curve's horizontal axis is trade order, and a curve
    redrawn in `pnl` order would be a cumulative total of a sequence that never happened.
    """
    from keel.commands.insights import build_equity_curve, build_journal_report

    limit = _journal_limit(query)
    repo = open_repo(cfg.db_path)
    try:
        report = build_journal_report(repo, _status_report(cfg, now_ts), now_ts, limit=limit)
    finally:
        close_repo(repo)
    return payload.journal_payload(report, curve=build_equity_curve(report.entries))


def read_rules(cfg: ServeConfig, _query: Query, _state: Any, _now_ts: int) -> dict[str, Any]:
    repo = open_repo(cfg.db_path)
    try:
        rows = repo.get_rules(None)
    finally:
        close_repo(repo)
    return payload.rules_payload(rows)


def read_venues(cfg: ServeConfig, _query: Query, state: Any, _now_ts: int) -> dict[str, Any]:
    """Capability declarations (unchanged), plus this deployment's venue readiness (#233 PR4).

    `needs_database=False` on this route (`API_ROUTES`) is UNCHANGED: this still answers with
    nothing set up at all, which is why `state` is read rather than re-probed -- `state` is
    `None` on a machine dispatch could not even build a `DeploymentState` for, and
    `has_usable_database` is `False` on a fresh one, both of which mean "do not open the repo at
    all" here, same as `read_setup` already reads `state` instead of re-inspecting the
    deployment a second time. `gather_readiness` then degrades every record to `None` (rail 20's
    own "unknown") on anything it cannot read, so a database that exists but predates the #233
    PR1 migration still answers 200 rather than 500.
    """
    from keel_broker_api.registry import discover_brokers

    from keel.commands.brokers import list_installed_brokers
    from keel.venue_readiness import gather_readiness

    infos = list_installed_brokers()
    usable = bool(getattr(state, "has_usable_database", False))
    readiness = gather_readiness(discover_brokers(), db_path=cfg.db_path if usable else None)
    return payload.venues_payload(infos, readiness)


def read_gates(_cfg: ServeConfig, _query: Query, _state: Any, _now_ts: int) -> dict[str, Any]:
    """Read from `keel.capabilities`, which is a pure declaration -- no config, no database, no
    network. It describes the binary that is serving the response."""
    from keel.capabilities import CAPABILITIES, GATES

    return payload.gates_payload(GATES, CAPABILITIES)


# -- the route table -------------------------------------------------------------------------------

#: A parsed query string, as `urllib.parse.parse_qs` returns it.
Query = dict[str, list[str]]

Reader = Callable[["ServeConfig", Query, Any, int], "dict[str, Any]"]


@dataclass(frozen=True)
class ApiRoute:
    """One `GET /api/*` endpoint.

    `html_route` names the rendered page this endpoint carries the data for, so
    `test_the_api_routes_cover_every_html_route_that_reads` can compare the two tables mechanically
    -- an HTML route added without a JSON counterpart fails that test rather than being discovered
    by a client that cannot render it. `""` means there is no rendered counterpart at all, which
    today is only `/api/config`.

    `needs_database` is the JSON counterpart of `server.needs_database`: rather than serving the
    setup checklist in place of a broken page, it answers `data: null` with `engine: "stopped"`,
    which is the same statement in the shape a client can act on.

    `collection` and `sortable` are the sort surface. `collection` is the key of the one list in
    `data` that `?sort=` orders; `sortable` is the closed set of columns it may be ordered by, and
    it is a hand-written list on purpose -- deriving it from the first row would make the API's
    answer to `?sort=x` depend on whether any rows exist, which is the sort of behaviour that shows
    up only on an empty deployment.
    """

    html_route: str
    read: Reader
    needs_database: bool = True
    collection: str = ""
    sortable: tuple[str, ...] = field(default=())


#: THE READ SURFACE, in full. Every one of the rendered routes in `server.ROUTES` appears here as
#: its `html_route` except `/glossary`, which gets no counterpart: it becomes an outbound
#: keeltrading.com link in #539 and `render_glossary` is deleted in #540, so an `/api/glossary`
#: would be a surface built in order to be removed -- and any client written against it would break
#: on the release that removes it.
API_ROUTES: dict[str, ApiRoute] = {
    "/api/config": ApiRoute(html_route="", read=read_config, needs_database=False),
    "/api/status": ApiRoute(
        html_route="/",
        read=read_status,
        collection="open_positions",
        # The one collection on this payload with money in it. `rule_counts` is already ordered by
        # status to match `render_human`, `data_freshness` follows the config's product order and
        # `live_rules` the ledger's -- three intrinsic orders that a display sort would destroy
        # rather than improve.
        sortable=(
            "id",
            "product_id",
            "rule_name",
            "qty",
            "entry_price",
            "opened_at",
            "bracket",
        ),
    ),
    "/api/setup": ApiRoute(
        html_route="/setup",
        read=read_setup,
        # No sortable collection: the steps are in RUNBOOK order, and that order is the
        # information. A checklist sorted by title is a checklist you cannot work down.
        needs_database=False,
    ),
    "/api/activity": ApiRoute(
        html_route="/activity",
        read=read_activity,
        collection="cycles",
        sortable=(
            "key",
            "cycle_id",
            "started_at",
            "ended_at",
            "mode",
            "signals",
            "blocked",
            "entered",
            "exited",
            "errors",
            "events_dropped",
        ),
    ),
    "/api/insights": ApiRoute(
        html_route="/insights",
        read=read_insights,
        collection="rules",
        sortable=(
            "rule_name",
            "status",
            "promotion_class",
            "n_trades",
            "win_rate",
            "avg_win",
            "avg_loss",
            "realized_rr",
            "expectancy",
            "profit_factor",
            "max_drawdown",
        ),
    ),
    "/api/journal": ApiRoute(
        html_route="/insights",
        read=read_journal,
        collection="entries",
        sortable=(
            "closed_at",
            "opened_at",
            "rule_name",
            "product_id",
            "qty",
            "entry_fill",
            "exit_fill",
            "pnl",
            "fees",
            "r_multiple",
            "outcome",
        ),
    ),
    "/api/orders": ApiRoute(
        html_route="/orders",
        read=read_orders,
        collection="rows",
        # The money columns and the audit ones. `placement` sorts the rows an operator most
        # needs to find -- the autonomous ones -- to one end of the list, which is the reason
        # this endpoint exists at all.
        sortable=(
            "id",
            "placement",
            "mode",
            "product_id",
            "side",
            "status",
            "qty",
            "filled_quantity",
            "expected_fill",
            "actual_fill",
            "fill_divergence",
            "fee",
            "created_at",
        ),
    ),
    "/api/rules": ApiRoute(
        html_route="/rules",
        read=read_rules,
        collection="rules",
        sortable=("id", "kind", "status", "created_at", "promoted_at", "demoted_at"),
    ),
    "/api/venues": ApiRoute(
        html_route="/venues",
        read=read_venues,
        needs_database=False,
        collection="venues",
        sortable=("name", "venue", "deployment", "package_version"),
    ),
    "/api/gates": ApiRoute(
        html_route="/gates",
        read=read_gates,
        needs_database=False,
        # No sortable collection: a gate's rows are the actions it covers, nested one level down,
        # and `?sort=` names a column of ONE top-level list. Sorting the gates themselves would
        # order a tuple that has one member.
    ),
}

#: `/api/*` -> the rendered route it carries the data for. Derived rather than written twice, so
#: the two cannot disagree.
HTML_ROUTE_FOR: dict[str, str] = {path: route.html_route for path, route in API_ROUTES.items()}


# -- the query surface -----------------------------------------------------------------------------


def _first(query: Query, name: str) -> str:
    """The first value for `name`, or `""`. Repeated parameters take the first rather than the last
    or a refusal: `?sort=a&sort=b` is a client bug, not an attack, and picking one deterministically
    is a better answer than a 400 the client's author will read as "sorting is broken"."""
    values = query.get(name) or [""]
    return values[0]


def _whole_number(raw: str, ceiling: int) -> int:
    """A `?limit=` that is a whole number inside `1..ceiling`, or an `ApiRefusal`.

    `_journal_limit`'s body, generalised over the ceiling when `/api/orders` arrived (#659)
    rather than copied: two functions refusing the same input with two texts is how the API's
    error vocabulary starts to drift one endpoint at a time."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiRefusal(400, "Bad limit", f"limit={raw!r} is not a whole number.") from exc
    if value < 1 or value > ceiling:
        raise ApiRefusal(400, "Bad limit", f"limit={raw!r} is outside 1..{ceiling}.")
    return value


def _journal_limit(query: Query) -> int:
    """How many journal rows to build, from `?limit=`.

    Bounded at both ends. A zero or a negative would reach `build_journal_report` as a slice bound
    and answer an empty journal that looks like an empty ledger, and an unbounded upper end is a
    caller choosing how much of this machine's memory to spend."""
    raw = _first(query, "limit")
    if not raw:
        return DEFAULT_JOURNAL_LIMIT
    return _whole_number(raw, MAX_JOURNAL_LIMIT)


def _sort_request(path: str, route: ApiRoute, query: Query) -> tuple[str, bool]:
    """`(column, descending)` from `?sort=` and `?dir=`, or a refusal.

    **Two parameters rather than one signed token.** `?sort=-expectancy` was the alternative and it
    needs a grammar -- what a leading `-` means, what happens to a column whose name starts with
    one, how a client strips it before comparing against `sort.columns`. Two orthogonal facts, two
    parameters, no parsing beyond a table lookup.

    **An unknown column is REFUSED, never ignored.** Silently ignoring one is how a client ships a
    sort header that does nothing and nobody notices for a release; the refusal names the columns
    that do exist, so the fix is in the response that reported the problem.
    """
    column = _first(query, "sort")
    direction = _first(query, "dir") or DIRECTIONS[0]
    if direction not in DIRECTIONS:
        raise ApiRefusal(
            400,
            "No such direction",
            f"dir={direction!r} is not a direction; use one of: {', '.join(DIRECTIONS)}.",
        )
    if not column:
        return "", False
    if not route.sortable:
        raise ApiRefusal(
            400,
            "Nothing to sort",
            f"{path} serves no sortable table -- its rows carry an intrinsic order.",
        )
    if column not in route.sortable:
        raise ApiRefusal(
            400,
            "No such column",
            f"sort={column!r} is not a column of {path}; sortable columns are: "
            f"{', '.join(route.sortable)}.",
        )
    return column, direction == DIRECTIONS[1]


def _sort_echo(route: ApiRoute, column: str, descending: bool) -> dict[str, Any] | None:
    """What was sorted, and what could be.

    `columns` is echoed even when nothing was sorted, so #537 renders sortable headers by reading
    the response rather than by holding a second copy of the list above -- a column added there
    reaches the interface without a second edit, and one removed there cannot leave a header that
    sorts by nothing."""
    if not route.sortable:
        return None
    return {
        "column": column,
        "direction": DIRECTIONS[1] if descending else DIRECTIONS[0],
        "columns": list(route.sortable),
    }


def _apply_sort(
    data: dict[str, Any], route: ApiRoute, column: str, descending: bool
) -> dict[str, Any]:
    rows = data.get(route.collection)
    if not isinstance(rows, list):  # pragma: no cover - a declared collection is always a list
        return data
    ordered = payload.order_rows(rows, column=column, descending=descending)
    return {**data, route.collection: ordered}


# -- the response ----------------------------------------------------------------------------------


def respond(cfg: ServeConfig, path: str, query: Query) -> tuple[int, dict[str, Any]]:
    """One `GET /api/*`, as `(status, document)`. Never raises; never renders.

    The order of the steps is the contract:

    1. **Unmapped path first**, so an unknown endpoint costs no database read at all.
    2. **The deployment probe**, which fills `engine` on every success. A probe that itself fails
       reports a STOPPED engine rather than a 500: `inspect` is documented as read-only and total,
       and if it ever is not, the honest reading of "we could not tell whether keel is set up" is
       the same one the rest of this file takes -- do not show figures.
    3. **The query surface**, before the read. A bad `?sort=` should not cost a report build, and
       more importantly it should not be able to arrive AFTER one and discard it.
    4. **The read**, then the sort, then the envelope.

    A stopped engine answers **200**, not a 4xx. #538's service worker and #536's `fetch` wrapper
    both read a non-ok status as "the server is unreachable", which would put a first-run user into
    an outage state when their actual position is that they have not set anything up yet -- and the
    two need different words on screen. The request succeeded; the answer is that there is nothing
    to report.
    """
    now_ts = int(time.time())
    route = API_ROUTES.get(path)
    if route is None:
        return 404, payload.error_envelope(
            now_ts,
            status=404,
            title="No such endpoint",
            detail=f"Nothing is served at {path}.",
        )

    try:
        state: Any = deployment_state(cfg)
        running = bool(state.has_usable_database)
    except Exception:  # pragma: no cover - `inspect` is total; this is the belt to its braces
        state, running = None, False

    try:
        column, descending = _sort_request(path, route, query)
    except ApiRefusal as refusal:
        return refusal.status, payload.error_envelope(
            now_ts, status=refusal.status, title=refusal.title, detail=refusal.detail
        )

    if route.needs_database and not running:
        return 200, payload.envelope(
            now_ts,
            running=False,
            data=None,
            sort=_sort_echo(route, column, descending),
        )

    try:
        data = route.read(cfg, query, state, now_ts)
    except ApiRefusal as refusal:
        return refusal.status, payload.error_envelope(
            now_ts, status=refusal.status, title=refusal.title, detail=refusal.detail
        )
    except Exception as exc:
        # A broken report is a stated failure, never a 200 with an empty payload -- that shape is
        # precisely the "blank view, or worse, stale figures" this envelope exists to prevent. The
        # detail carries the exception's TYPE and message and no traceback: a traceback in a
        # browser is a stack of file paths from someone else's machine.
        return 500, payload.error_envelope(
            now_ts,
            status=500,
            title="That report could not be built",
            detail=f"{type(exc).__name__}: {exc}",
        )

    if column:
        data = _apply_sort(data, route, column, descending)
    return 200, payload.envelope(
        now_ts,
        running=running,
        data=data,
        sort=_sort_echo(route, column, descending),
    )


def refusal_document(status: int, title: str, detail: str) -> dict[str, Any]:
    """A refusal raised BEFORE routing -- a failed host check, a missing session cookie.

    Lives here rather than in the handler so that every `/api/*` body, admitted or not, is built by
    one function against one contract. It carries no `engine`, and that is the reason the success
    and failure documents are told apart by HTTP status rather than by a field: these refusals
    happen before admission, and filling in `engine` would mean an unauthenticated request reading
    the deployment state off disk."""
    return payload.error_envelope(int(time.time()), status=status, title=title, detail=detail)


def action_document(result: Any) -> dict[str, Any]:
    """One completed setup action, as JSON (#540).

    **`changed` is the field the client actually needs**, and it is not a success flag: it is the
    difference between "created" and "already there". `keel.commands.setup`'s own note on it calls
    it "the property that makes a double-click safe" -- every action is idempotent, so a repeated
    submission succeeds and reports `changed: false`, which is a true statement about the
    deployment rather than a soft failure.

    Judged here, in the serialiser, exactly as every other payload value is: the client is handed
    `display` and `state` and never decides what a result means.
    """
    return payload.envelope(
        int(time.time()),
        # `running=True` is a statement of fact rather than a probe: this document is built only
        # after an action has RUN in this process, so the engine's presence is not in question and
        # re-inspecting the deployment to say so would be one disk probe spent on a known answer.
        running=True,
        data={
            "step_key": str(getattr(result, "step_key", "")),
            "changed": payload.flag(
                bool(getattr(result, "changed", False)),
                on="done",
                off="already done — nothing to change",
                on_state=payload.GOOD,
                off_state=payload.NEUTRAL,
            ),
            "message": payload.label(str(getattr(result, "message", ""))),
        },
    )


def sortable_columns() -> Mapping[str, Sequence[str]]:
    """The declared sort surface, for a test to read rather than restate."""
    return {path: route.sortable for path, route in API_ROUTES.items() if route.sortable}
