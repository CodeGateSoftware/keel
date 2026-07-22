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
from keel_core.telemetry import bind_venue

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

DEFAULT_DB_PATH = "keel.db"
DEFAULT_CONFIG_PATH = "config.yaml"


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
    # process entry point, rather than passed into ~26 `log_event` call sites -- the engine is
    # single-venue today, so threading a constant through every payload would mean revisiting
    # all of them again the moment it stops being one. A process driving several venues rebinds
    # per cycle instead; nothing else changes.
    bind_venue(DEFAULT_VENUE)
    return config


def _build_broker(config: Config) -> Any:  # pragma: no cover -- exercised only against fakes
    """Construct the real, network-talking `CoinbaseClient`. Tests monkeypatch this function."""
    from coinbase.rest import RESTClient

    from keel.config import load_secrets
    from keel.data.cb_client import CoinbaseClient

    secrets = load_secrets()
    transport = RESTClient(api_key=secrets.get("api_key"), api_secret=secrets.get("api_secret"))
    return CoinbaseClient(transport)
