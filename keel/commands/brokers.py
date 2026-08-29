"""`keel brokers` -- venues/brokers visibility (issue #394 C7; PRD O7): one service over
the entry-point registry and the adapters' capability declarations, two front-ends.

The service (`list_installed_brokers`) walks `keel_broker_api.registry.discover_brokers()`
-- the same registry walk `load_broker` makes -- constructs each adapter WITHOUT a
transport (every first-party adapter's `capabilities()` is a constant, answerable offline),
and renders one `BrokerInfo` row per installed adapter: name (the entry-point name), venue
id, wired-for-deployment vs optional-dev-venue, session-bound or 24/7, quote currencies,
asset classes, the declared order kinds, preview synthesis (native / synthesized / none),
the fee-summary declaration, the adapter's DECLARED endpoint vocabulary and data feeds
where it has them (Alpaca's paper/live hosts and iex/sip tiers), and the adapter package's
installed version.

**Capability display, never key-presence inference (#233-aligned).** Nothing here reads an
environment variable, a config's contents, or a credential store, and no secret VALUE is
ever carried or shown: `BrokerInfo` is a closed vocabulary of adapter declarations, pinned
by test. "wired-for-deployment" says a shipped deployment's config selects this venue --
it does NOT say any operator's keys are present.

The CLI (`keel brokers list`, with `--json` following `keel status --json`'s convention)
and the console's Venues browser (`keel.commands.console.build_venues_lines`) are both
renderings of this one payload -- the O7 acceptance ("identical information from one
service") is pinned by test against both front-ends.

Alpaca, Coinbase, and Robinhood are trademarks of their respective owners.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import Any

import click

from keel.commands._common import DISCLAIMER
from keel.venue_readiness import VenueReadinessRow, gather_readiness

#: The venues a SHIPPED deployment selects -- the whole wired/optional classification, in
#: ONE explicit place:
#
#:   coinbase   `BrokerConfig.name`'s default (`keel_core.config`): every tracked config
#:              without a `broker:` section (paper-forward, paper-hourly, live) is
#:              coinbase -- keel's original venue.
#:   alpaca     selected by `config.paper-equities.yaml` (the keel-equities wrapper's
#:              paper deployment; `broker: name: alpaca, endpoint: paper`).
#
#:   fake       the in-repo deterministic dev/test venue -- never selected by a tracked
#:              config (`keel-broker-fake` exists so tests and local runs need no venue).
#:   robinhood  an optional install (the README's "optional venue") -- no tracked config
#:              selects it; it is a `uv add keel-broker-robinhood` away, not a deployment.
#
#: No registry entry carries this signal (the entry points say only that an adapter IS
#: installed), so the constant states it -- with its reasoning here -- rather than leaving
#: every front-end to re-derive it. An adapter not named here is optional by default.
WIRED_FOR_DEPLOYMENT: frozenset[str] = frozenset({"coinbase", "alpaca"})

#: The deployment classification's two words, spelled once so the CLI and the Venues
#: browser cannot disagree about them.
WIRED = "wired-for-deployment"
OPTIONAL = "optional-dev-venue"


@dataclass(frozen=True)
class BrokerInfo:
    """One installed adapter, as a CAPABILITY DISPLAY row. Every field is a rendering of
    the adapter's own declarations (`capabilities()`, its class's declared endpoint/feed
    vocabulary, its installed version) -- never an inference about the operator's keys or
    config, and never secret material (pinned by test: the field set is closed)."""

    #: The entry-point name -- the `broker: name:` a config selects, and the
    #: registry key `load_broker` resolves.
    name: str
    #: The venue id the adapter itself declares (`capabilities().venue`).
    venue: str
    #: `WIRED` or `OPTIONAL` (see `WIRED_FOR_DEPLOYMENT`).
    deployment: str
    #: Whether the venue CLOSES (`capabilities().session_bound`): session-bound
    #: venues render with a market clock; the rest are 24/7.
    session_bound: bool
    #: Whether the adapter spends settled cash only (`capabilities().cash_only`, #372)
    #: -- the declared funding posture. Every first-party adapter declares True; a False
    #: row would be an adapter announcing a borrowing path the engine can refuse at load.
    cash_only: bool
    quote_currencies: tuple[str, ...]
    asset_classes: tuple[str, ...]
    supported_orders: tuple[str, ...]
    #: How order previews exist here: "native" (the venue serves preview quotes),
    #: "synthesized" (the adapter prices them itself, labelled synthetic), or "none".
    preview: str
    supports_fee_summary: bool
    #: The endpoint vocabulary the ADAPTER declares (Alpaca: paper/live), empty where the
    #: venue has no such knob -- read off the adapter class, never a service-side table.
    declared_endpoints: tuple[str, ...]
    #: The market-data tiers the adapter declares (Alpaca: iex/sip), empty where undeclared.
    supported_data_feeds: tuple[str, ...]
    #: The adapter package's installed version, or `None` when the metadata will not say.
    package_version: str | None
    #: NOT a capability: set (with the construction error) on the one honest row an
    #: adapter that RAISED while being built gets (#406 review) -- an adapter that could
    #: not be constructed has declared nothing, so every capability field on such a row
    #: is the empty/neutral placeholder and never a fact. Both human renderers check
    #: this FIRST and render only `adapter_error_block`, never the placeholders; the
    #: JSON front-end carries it so a script can tell a failed row from a sparse one.
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        """The row as a JSON-ready dict (tuples become lists) -- the `--json` shape."""
        return asdict(self)


def _declared(adapter_cls: Any, attribute: str) -> tuple[str, ...]:
    """An adapter class's declared vocabulary (`DECLARED_ENDPOINTS`/`DECLARED_DATA_FEEDS`),
    sorted, or `()` when the adapter declares none -- a display of what the ADAPTER says,
    never a service-side list keyed by venue name."""
    return tuple(sorted(getattr(adapter_cls, attribute, ())))


def _preview_of(capabilities: Any) -> str:
    """The preview story in one word, derived ONLY from the capability booleans -- the
    same pair `BrokerCapabilities.can_preview` reads."""
    if capabilities.supports_native_preview:
        return "native"
    if capabilities.synthesizes_preview:
        return "synthesized"
    return "none"


def _package_version_of(adapter_cls: Any) -> str | None:
    """The adapter's installed version, derived from its own module's distribution
    name (`keel_broker_alpaca` -> `keel-broker-alpaca`), or `None` when the metadata
    is absent (a checkout on the path with no install)."""
    top_level = adapter_cls.__module__.split(".")[0]
    try:
        return dist_version(top_level.replace("_", "-"))
    except PackageNotFoundError:
        return None


def _safe_version(adapter_cls: Any) -> str | None:
    """`_package_version_of`, made total: version metadata is display sugar on an error
    row, never worth a second raise inside the error handler itself."""
    try:
        return _package_version_of(adapter_cls)
    except Exception:
        return None


#: How much of a raising adapter's error the error row will ever carry -- the same
#: truncation discipline `run_live`'s discover overlay keeps: a stray huge or sensitive
#: blob (an HTTP error body, say) must never be painted or JSON-dumped in full.
_MAX_ADAPTER_ERROR_CHARS = 200


def list_installed_brokers() -> list[BrokerInfo]:
    """Every installed adapter as one `BrokerInfo` row, in name order -- the ONE payload
    both front-ends (`keel brokers list`, the console's Venues browser) render. Adapters
    are constructed WITHOUT transports and only their offline declarations are read: no
    broker handle, no network, no config, no credentials.

    TOTAL by design (#406 review): a raising adapter (construction, `capabilities()`,
    any of it) becomes one honest error row -- its name and the error -- instead of
    killing the listing, because both front-ends ride this service and one broken
    third-party package must never take the `brokers` command or the console's Venues
    screen down with it."""
    from keel_broker_api.registry import discover_brokers

    rows: list[BrokerInfo] = []
    for name, adapter_cls in discover_brokers().items():
        try:
            capabilities = adapter_cls().capabilities()
            rows.append(
                BrokerInfo(
                    name=name,
                    venue=capabilities.venue,
                    deployment=WIRED if name in WIRED_FOR_DEPLOYMENT else OPTIONAL,
                    session_bound=bool(capabilities.session_bound),
                    cash_only=bool(capabilities.cash_only),
                    quote_currencies=tuple(sorted(capabilities.quote_currencies)),
                    asset_classes=tuple(sorted(capabilities.asset_classes)),
                    supported_orders=tuple(sorted(capabilities.supported_orders)),
                    preview=_preview_of(capabilities),
                    supports_fee_summary=bool(capabilities.supports_fee_summary),
                    declared_endpoints=_declared(adapter_cls, "DECLARED_ENDPOINTS"),
                    supported_data_feeds=_declared(adapter_cls, "DECLARED_DATA_FEEDS"),
                    package_version=_package_version_of(adapter_cls),
                )
            )
        except Exception as exc:
            # The deployment classification is NAME-derived and needs no construction,
            # so the row keeps its honest half; every capability field is the empty
            # placeholder (see `BrokerInfo.error`) and the renderers never print them.
            rows.append(
                BrokerInfo(
                    name=name,
                    venue="",
                    deployment=WIRED if name in WIRED_FOR_DEPLOYMENT else OPTIONAL,
                    session_bound=False,
                    cash_only=False,
                    quote_currencies=(),
                    asset_classes=(),
                    supported_orders=(),
                    preview="none",
                    supports_fee_summary=False,
                    declared_endpoints=(),
                    supported_data_feeds=(),
                    package_version=_safe_version(adapter_cls),
                    error=f"{type(exc).__name__}: {exc}"[:_MAX_ADAPTER_ERROR_CHARS],
                )
            )
    rows.sort(key=lambda row: row.name)
    return rows


# -- the human rendering --------------------------------------------------------------------------

#: The posture line both front-ends carry, spelled once: what these screens show is the
#: adapter's own declarations, never an inference about the operator's keys.
NO_KEY_INFERENCE_LINE = (
    "capability display only -- no key presence is read or implied, and no secret is shown"
)


def capability_facts(info: BrokerInfo) -> str:
    """The row's capability facts as one " · "-joined phrase -- the wording
    `render_brokers_lines` prints for `keel brokers list`. (The console Venues browser
    that once wrapped this same phrase is gone -- #541 deleted the console layer -- so
    "the shared wording two front-ends render" describes nothing today; the phrase stays
    a single PURE function so the next front-end inherits it rather than re-deriving
    it.)"""
    hours = "session-bound (opens and closes)" if info.session_bound else "24/7"
    funding = "cash only" if info.cash_only else "MARGIN-CAPABLE"
    facts = [
        info.deployment,
        hours,
        funding,
        f"quotes {'/'.join(info.quote_currencies)}",
        f"asset classes {'/'.join(info.asset_classes)}",
        f"preview {info.preview}",
        f"fee summary {'yes' if info.supports_fee_summary else 'no'}",
    ]
    if info.declared_endpoints:
        facts.append(f"endpoints {'/'.join(info.declared_endpoints)}")
    if info.supported_data_feeds:
        facts.append(f"data feeds {'/'.join(info.supported_data_feeds)}")
    return " · ".join(facts)


def adapter_error_block(info: BrokerInfo) -> list[str]:
    """The honest block a raising adapter's row renders as: its name and installed
    version, then the construction error wrapped to the 78-column budget -- spelled
    once here so a broken adapter reads the same everywhere (the one-phrase discipline
    `capability_facts` keeps). PURE, and deliberately states NO
    capability fact: an adapter that could not be constructed has declared nothing."""
    return [
        f"{info.name} ({info.package_version or 'unknown version'}) -- unavailable",
        *textwrap.wrap(
            f"construction failed: {info.error}", width=78, initial_indent="  ",
            subsequent_indent="  ",
        ),
    ]


def render_brokers_lines(infos: list[BrokerInfo]) -> list[str]:
    """The human payload: one block per adapter -- its name and installed version, its
    capability facts, and its declared order kinds. PURE over the service's rows; a row
    whose construction raised renders `adapter_error_block` instead, never a fabricated
    capability line."""
    lines: list[str] = [f"{len(infos)} adapter(s) installed under keel.brokers:"]
    for info in infos:
        if info.error is not None:
            lines.extend(adapter_error_block(info))
            continue
        version = info.package_version or "unknown version"
        lines.append(f"{info.name} ({version}) -- {info.venue}")
        lines.append(f"  {capability_facts(info)}")
        lines.append(f"  order kinds: {', '.join(info.supported_orders)}")
    lines.append(NO_KEY_INFERENCE_LINE)
    return lines


# -- venue readiness (#233 PR4): a SEPARATE block, never merged into the one above -----------------
#
# `render_brokers_lines` answers "what does the adapter DECLARE" and ends on `NO_KEY_INFERENCE_LINE`
# -- a statement that is true of THAT block and must stay true of it. Readiness answers a different
# question ("can THIS deployment actually place a live entry on this venue, right now"), and it is
# rendered as its own headed block, AFTER the declarations block, with its own honesty line. Merging
# the two would re-blur exactly the distinction #233 exists to draw -- see `keel.venue_readiness`'s
# module docstring.

#: What the readiness block reads, spelled out because `NO_KEY_INFERENCE_LINE` above it does NOT
#: cover this block (that line is deliberately scoped to the declarations it terminates). Unlike
#: the declarations block, this one genuinely reads credential PRESENCE and runs LOCAL
#: well-formedness arithmetic on the value -- e.g. deriving a public key in-process to compare it
#: against what is in the identifier slot (the 2026-08-19 incident) -- but it never displays a
#: secret and never makes a network call.
READINESS_HONESTY_LINE = (
    "readiness reads credential PRESENCE (environment, .env, keychain) and runs LOCAL "
    "well-formedness arithmetic on the value -- no secret is ever shown, and no network call "
    "is ever made"
)

#: The header text that OPENS the readiness block -- distinctive enough that a test can assert
#: it never appears inside `render_brokers_lines`' own output, proving the two blocks are built
#: by two different functions rather than merely printed in a hopeful order.
READINESS_HEADER = "venue readiness (this deployment, #233):"


def render_readiness_lines(rows: list[VenueReadinessRow]) -> list[str]:
    """The readiness block's human rendering -- one block per venue, PURE over
    `VenueReadinessRow`s the caller already gathered. Never called from inside
    `render_brokers_lines`; always printed after it, by `brokers_list` below."""
    lines: list[str] = ["", READINESS_HEADER]
    for row in rows:
        lines.append(f"{row.venue}: {row.state.value}")
        lines.append(f"  {row.explanation}")
        if row.next_step is not None:
            lines.append(f"  fix: {row.next_step}")
    lines.append(READINESS_HONESTY_LINE)
    return lines


def _readiness_rows(ctx: click.Context) -> list[VenueReadinessRow]:
    """Gathers this deployment's readiness rows for `keel brokers list` -- `gather_readiness`'s
    CLI wiring, the same service `/api/venues` wires to a repo it knows is migrated.

    `keel brokers list` must keep working with NO deployment at all (pinned by
    `test_brokers_list_renders_the_service_rows`): `db_path` is passed to `gather_readiness`
    only when a file already exists there, so this never CREATES a database as a side effect of
    a display command -- a `brokers list` on a fresh checkout reads exactly as much of the
    filesystem as it always has, plus the credential env/`.env`/keychain reads readiness needs.
    """
    from keel_broker_api.registry import discover_brokers

    obj = ctx.obj or {}
    db_path = obj.get("db_path")
    usable = db_path is not None and Path(db_path).exists()
    return gather_readiness(discover_brokers(), db_path=db_path if usable else None)


# -- the CLI ---------------------------------------------------------------------------------------


@click.group("brokers")
def brokers_group() -> None:
    """Venues/brokers visibility: what every installed adapter declares it can do.

    Lists the installed broker adapters (the `keel.brokers` entry points) with their
    capabilities -- wired-for-deployment vs optional-dev-venue, session-bound or 24/7,
    quote currencies, asset classes, order kinds, preview synthesis, declared endpoints
    and data feeds -- capability display only: never key-presence inference, never a
    secret value. One service behind `keel brokers list` and the console's Venues browser.

    Alpaca, Coinbase, and Robinhood are trademarks of their respective owners.
    """


@brokers_group.command("list")
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON."
)
@click.pass_context
def brokers_list(ctx: click.Context, as_json: bool) -> None:
    """Every installed adapter with its declared capabilities (read-only, offline), followed by
    this deployment's venue readiness (#233 PR4): can a live entry actually be placed, right now.

    `--json` follows `keel status --json`'s convention: the service payload verbatim -- the
    DECLARATIONS only, UNCHANGED by #233 -- with no disclaimer footer after it, for scripting and
    the TUI. Readiness is not in `--json` here: turning this into a dict, or adding readiness
    fields to these rows, would both break existing consumers of this exact shape and re-merge
    the two facts #233 keeps apart (see `render_readiness_lines`'s module comment). The machine
    surface for readiness is the web payload (`/api/venues`'s `readiness` key), not this flag.

    The human rendering carries the disclaimer, and the readiness block, like every other keel
    command; `render_brokers_lines`'s own block ends unchanged at `NO_KEY_INFERENCE_LINE`.
    """
    infos = list_installed_brokers()
    if as_json:
        click.echo(json.dumps([info.as_json() for info in infos], indent=2))
        return
    for line in render_brokers_lines(infos):
        click.echo(line)
    for line in render_readiness_lines(_readiness_rows(ctx)):
        click.echo(line)
    click.echo("")
    click.echo(DISCLAIMER)
