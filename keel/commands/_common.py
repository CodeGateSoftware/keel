"""Shared seams and plumbing for the keel CLI command groups.

These helpers are the boundaries that the CLI tests drive: the disclaimer footer, the
interactive-confirmation gate, DB/config construction, and the one broker-construction seam
that tests monkeypatch to keep the network out. They live here (rather than in `keel/cli.py`)
so that command groups extracted into `keel/commands/*` can share them without importing the
composition root, and so the seams have a single, obvious home.

**Monkeypatch targets.** Historically tests patched `keel.cli._build_broker`, `keel.cli._open_repo`
and `keel.cli._load_cfg`; `keel/cli.py` re-imports those names, so patching `keel.cli.X` still
rebinds the copy the top-level commands defined there resolve. The TTY predicate `_is_interactive`
is different: it is called *internally* by `_require_interactive_confirmation`, so every caller --
here and in `keel/cli.py` -- reaches it as `_common._is_interactive()` (attribute access on this
module), and tests patch `keel.commands._common._is_interactive`. That keeps a single, consistent
patch point no matter which module the calling command lives in.

Note the asymmetry the other way: an extracted command group (`keel/commands/*`) resolves
`_open_repo`/`_load_cfg`/`_build_broker` in *its own* namespace -- the copies imported from here --
so patching `keel.cli._open_repo` affects only cli-resident commands (e.g. `agent`). A test that
drives an extracted group either patches `keel.commands._common.<name>` or, as the group tests
do, runs against a real `--db` temp path instead of patching at all.
"""

from __future__ import annotations

import functools
import sys
from dataclasses import replace
from typing import Any

import click
from keel_core import paths as _paths
from keel_core.telemetry import bind_venue, current_venue

from keel.config import Config, load_config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution.guards import DEFAULT_VENUE
from keel.logging_setup import configure_logging

DISCLAIMER = (
    "keel is a personal tool, not financial advice and not religious (Shariah) advice. "
    "Consult a qualified financial advisor and a knowledgeable scholar before trading. "
    "You are solely responsible for your own trading decisions."
)

#: The bare filenames a deployment folder uses. Kept as the historical constants because the
#: profile/console code compares against them by name, and because a deployment folder's layout is
#: unchanged by #434 -- what changed is only WHICH folder a bare invocation resolves them against.
DEFAULT_DB_PATH = "keel.db"
DEFAULT_CONFIG_PATH = "config.yaml"


def default_db_path() -> str:
    """`--db`'s default, resolved when the command runs rather than when this module is imported.

    A callable default matters here: Click evaluates a literal `default=` once, at decoration time,
    so a literal would freeze whatever directory the process happened to start in -- including
    under test, where `monkeypatch.chdir` moves cwd after import. Resolving per invocation is also
    the only way `KEEL_HOME` and deployment detection can mean anything.
    """
    return str(_paths.default_db_path())


def default_config_path() -> str:
    """`--config`'s default. See `default_db_path` for why this is a callable."""
    return str(_paths.default_config_path())


# -- disclaimer -------------------------------------------------------------------------------


def _print_disclaimer() -> None:
    click.echo("")
    click.echo(DISCLAIMER)


def with_disclaimer(f: Any) -> Any:
    """Print the disclaimer footer after `f` runs, whether it succeeds, errors, or aborts."""

    @functools.wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return f(*args, **kwargs)
        finally:
            _print_disclaimer()

    return wrapper


# -- interactive confirmation (no interactive hangs under CliRunner) -----------------------------


def _is_interactive() -> bool:
    """True when a human is at a terminal.

    Deliberately has NO env-var or flag override: any such seam would be settable from cron and
    would defeat the fail-closed behaviour of every gate built on it. Tests patch this predicate.
    """
    return sys.stdin is not None and sys.stdin.isatty()


def _require_interactive_confirmation(action: str, detail: str) -> None:
    """Demand an explicit typed `yes` from a human at a terminal before a dangerous action.

    This replaces the former scrypt passphrase gate. Once placing a real, money-spending order
    needs only a typed confirmation, requiring a remembered secret to release a safety halt is
    ceremony without a matching threat model -- on a single-user machine the honest boundary is
    the OS account, as the old gate's own docstring conceded. One rule ("dangerous actions need a
    human at a terminal; nothing needs a stored secret") is easier to reason about and to audit.

    Demands the full word `yes` rather than a bare `y`: these actions are rarer and heavier than
    an order confirmation. **Fails closed off a TTY**, so cron jobs, pipes and scripts can never
    release a halt.
    """
    if not _is_interactive():
        raise click.ClickException(
            f"refusing to {action}: this needs confirmation from an interactive terminal."
        )
    click.echo(f"About to {action}.")
    click.echo(f"  {detail}")
    if click.prompt('Type "yes" to confirm', default="", show_default=False).strip() != "yes":
        raise click.ClickException("aborted (confirmation not given).")


# -- DB / config / broker construction ---------------------------------------------------------


def _open_repo(ctx: click.Context) -> Repository:
    conn = connect(ctx.obj["db_path"])
    migrate(conn)
    return Repository(conn)


def _load_cfg(ctx: click.Context) -> Config:
    """Load `config.yaml` and wire up engine-activity logging from it.

    `--verbose`/`-v` on the root `cli` group (`ctx.obj["verbose"]`) overrides
    `config.logging.verbose` to `True` before `configure_logging` is called, so the flag always
    wins over whatever `config.yaml` says. Every command that loads config (agent/monitor/
    simulate/etc.) gets logging configured this way, right when the config it's built from
    becomes available.
    """
    config = load_config(ctx.obj["config_path"])
    if ctx.obj.get("verbose"):
        config = replace(config, logging=replace(config.logging, verbose=True))
    configure_logging(config.logging)
    # Spec §10.2 names `venue` a stable field on every event. Bound once here, at the one
    # process entry point, rather than passed into ~26 `log_event` call sites -- the engine
    # was single-venue until #370 B2, and threading a constant through every payload would
    # have meant revisiting all of them again the moment it stopped being one. The venue now
    # comes from the config's own `broker:` selection (Coinbase for every config that omits
    # the section, so the bound string is byte-identical to the old `DEFAULT_VENUE`
    # constant); a process driving several venues rebinds per cycle instead; nothing else
    # changes.
    bind_venue(config.broker.name)
    return config


# -- venue resolution --------------------------------------------------------------------------


def _bound_venue_or_default(venue: str | None) -> str:
    """An explicit `--venue` wins; otherwise the venue THIS deployment trades.

    That is the same binding rail 14 and rail 20 gate every order on -- the one `_load_cfg` makes
    at process entry for telemetry (`bind_venue(config.broker.name)`) -- with coinbase when
    nothing is bound. A `--venue` default frozen at coinbase would make an alpaca operator type
    `--venue alpaca` on every invocation or silently write a record nothing reads.

    Must be called AFTER `_load_cfg(ctx)` has run, or there is nothing bound to read.

    Shared here (rather than kept local to `keel.commands.subscription`, where it was first
    written) the same way `_default_sim_products` lives in `keel.commands._products`: more than
    one command group needs the identical venue-resolution rule, and a second, independently
    maintained copy in `keel.commands.scope` is exactly the kind of silent drift this module
    exists to prevent.
    """
    if venue is not None:
        return venue
    return current_venue() or DEFAULT_VENUE


def _build_broker(config: Config, *, timeout: int | None = None) -> Any:
    """Construct the real, network-talking broker for the venue `config.broker` selects.

    **Every name resolves through the registry (issue #524).** The `broker:` config section
    selects a venue; the `keel.brokers` entry points (`keel_broker_api.registry.load_broker`)
    decide which adapter class that name means -- coinbase included, so the default venue has
    no second, direct construction path to drift against the conformance-tested adapter. The
    CLI's per-venue knowledge is the TRANSPORT it hands the resolved adapter: coinbase wiring
    is `load_secrets()` from `.env` into a `coinbase.rest.RESTClient`; alpaca wiring is the
    paper/live endpoint, the iex/sip feed and `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`. An
    adapter that resolves but has no wiring is refused by name rather than constructed
    credential-less, and a name with no entry point at all fails through the registry's own
    LookupError, which lists what IS installed.

    Tests monkeypatch this function; the branches are additionally driven against fakes and
    the real (network-free at construction) Alpaca and Coinbase classes by
    `tests/test_paper_equities_profile.py`.

    **The alpaca branch verifies the account posture before returning** (#372): one
    `verify_cash_account()` read of the venue's own account classification, refusing a
    margin-postured account (and failing closed on an unreadable one) so no engine path
    ever sees a broker on a venue posture keel does not trade -- cash only, no margin
    borrowing (riba), which is the posture's whole claim: it sidesteps nothing on PDT
    (keel's PDT safety is the cadence, not the posture). The refusal names the posture
    and the fix; the runbook's "Account posture" section is the operator-facing half.

    `timeout` (seconds) is optional and defaults to `None` -- the SDK's own default (no
    timeout), matching every existing caller (the agent/executor broker path) exactly.
    Callers that cannot tolerate a hung network call pass an explicit bound.
    """
    venue = config.broker.name

    from keel_broker_api.registry import load_broker

    adapter_cls = load_broker(venue)

    module_root = adapter_cls.__module__.split(".")[0]
    if module_root == "keel_broker_coinbase":
        from coinbase.rest import RESTClient

        from keel.config import load_secrets

        secrets = load_secrets()
        transport = RESTClient(
            api_key=secrets.get("api_key"),
            api_secret=secrets.get("api_secret"),
            timeout=timeout,
        )
        # The registry-resolved adapter, not a hand-imported client -- the same
        # conformance-tested class every other venue resolves through.
        return adapter_cls(transport)

    if module_root != "keel_broker_alpaca":
        raise RuntimeError(
            f"broker.name {venue!r} resolved to an installed adapter, but the CLI does not "
            "yet know how to give it credentials -- venue wiring exists for 'coinbase' and "
            "'alpaca' only. For robinhood the missing piece is the Ed25519 credential wiring "
            "its transport signs with, which the CLI does not carry yet by choice -- the "
            "venue is dev-only. Constructing it anyway would hand the engine a broker that "
            "cannot reach its venue."
        )

    from keel.config import load_alpaca_secrets

    secrets = load_alpaca_secrets()
    if not secrets.get("key_id") or not secrets.get("secret_key"):
        raise RuntimeError(
            f"broker {venue!r} needs Alpaca credentials: set ALPACA_API_KEY_ID and "
            "ALPACA_API_SECRET_KEY in the environment or in .env. Paper keys suffice for the "
            "paper profile -- generate them from the Alpaca dashboard's paper trading "
            "account; broker.endpoint selects paper-api.alpaca.markets, and there is no URL "
            "knob anywhere that could point these at the live venue."
        )

    from keel_broker_alpaca.transport import AlpacaTransport

    transport = AlpacaTransport(
        secrets["key_id"] or "",
        secrets["secret_key"] or "",
        endpoint=config.broker.endpoint,
        data_feed=config.broker.data_feed,
        timeout=10.0 if timeout is None else float(timeout),
    )
    # `adapter_cls` IS the class the entry point registered -- constructed through discovery
    # (not a direct import) so the installed adapter, not a hard dependency, is what runs.
    broker = adapter_cls(
        transport, endpoint=config.broker.endpoint, data_feed=config.broker.data_feed
    )
    # Cash-account posture (#372, PRD §5): the one venue-state check this seam makes. The
    # adapter reads the venue's own account classification and refuses a margin-postured
    # account (`CashAccountRequired`, a RuntimeError like this function's other refusals),
    # fail-closed on a classification it cannot read. Refusing HERE -- rather than at order
    # time -- is what makes it a posture and not a rail: guards are broker-less by design,
    # and a per-order raise could fire on an exit path, where a refusal can trap a
    # position. Every command that builds a broker (agent cycle, fetch, monitor, the order
    # paths) inherits it; one extra `/v2/account` read per build sits far inside FR-11's
    # rate budget at the daily equities cadence.
    broker.verify_cash_account()
    return broker
