"""keel command-line interface (P3 Task 9).

Wires the merged Phase 1-3 modules into a `click` CLI: `db import` (`data.csv_import.import_dir`),
`monitor` (`data.market_feed`), `agent` (`agent.run_once`/`agent.loop`), `autonomy on|off|show`
(the `profile` row `agent.run_once` itself re-reads each cycle),
`rules list|backtest|promote|demote|disable|enable|seed|add` (`data.repository` +
`strategy.backtest`/`promotion`; `seed` populates the otherwise-empty `rules` table from
`agent.RULE_REGISTRY`, Issue #81, and `add` inserts ONE `candidate` row from operator-supplied
`--params` JSON so a proposed parameter set can reach `rules backtest` without hand-written
Python),
`pnl` (`analysis.pnl`), `kill`/`resume` (the `agent_state` kill-switch),
`resume-entries` (clear an armed rail-16 consecutive-loss halt), `record-flow`
(declare a deposit/withdrawal so rail 11 does not read it as P&L) and `reset-hwm`
(reset rail 11's high-water mark),
`subscription attest|set|show` (the per-venue, user-attested allowance rail 14 reads live),
`status` (`commands.status`: the read-only, no-broker operator dashboard the paper-mode-fidelity
spec deferred -- mode/kill-switch/autonomy/Rail 11/rail 17 attestation freshness/positions/rules/
data freshness, plus `--json`),
and `insights summary|journal` (`commands.insights`: a read-only VIEW over the same substrate --
per-rule promotion-gate distance and a filterable trade journal, also with `--json`).

**Dangerous commands ask a human; nothing needs a stored secret.** The former scrypt passphrase
gate is gone (see `2026-07-21-security-simplification-design.md`). Five commands re-permit trading
after a safety halt -- `resume`, `resume-entries`, `record-flow`, `reset-hwm` and
`withdrawals attest --enabled` (rail 17) -- and each
demands a typed `yes` via `_require_interactive_confirmation`, **failing closed off a TTY** so a
script or cron job can never release a halt. `kill` (engaging the kill-switch) and `autonomy off`
are *safe* actions -- they only ever reduce capability -- and are always allowed, from anywhere.
Every other command here is read-only or a local rules-table/DB mutation with no live-trading
blast radius.

**Autonomy is a profile choice, checked in-process.** `keel autonomy on` (typed `yes`, TTY
required) sets `profile.autonomous`; `agent.run_once` re-reads it every cycle via
`_effective_mode`, so the check cannot be skipped by a caller driving `run_once` in-process, and
turning autonomy off binds on the next cycle. It changes who is asked, never what is allowed:
`guards.check` runs first in every mode, and autonomy never releases a halt.

**The confirm gate says where its numbers came from.** `_interactive_confirm` is the only place
in keel that renders an order preview to a human. It renders the provenance of the figures --
a broker's own quote versus an estimate keel synthesized -- above them rather than below, and
escalates from `[y/N]` to a typed phrase when the preview is unpriced, carries errors, or cannot
be read at all. See that function and `_ask_to_place` for why that is friction rather than a
refusal.

**No interactive hangs in tests.** `_is_interactive()` is the single TTY predicate, with
deliberately no env-var or flag override -- any such seam would be settable from cron and would
defeat every fail-closed built on it. Tests patch the predicate.

**Disclaimer.** The money-touching and dangerous commands print the halal + not-financial/
religious-advice disclaimer footer via the `with_disclaimer` decorator on their callback --
always, even when the command errors out or is refused at a confirmation prompt. (Pure-reporting
commands such as `trials *`, `withdrawals show` and `assets list` deliberately omit it.)

**No live network in tests.** `_build_broker` is the one seam that would construct a real,
network-talking broker (a `CoinbaseClient` for the default/absent `broker:` section, or the
configured venue's adapter otherwise — venue selection, #370 B2); tests monkeypatch it to
inject a fake broker instead, exactly like `tests/test_agent.py`'s `FakeBroker` (the
venue-selection branches themselves are driven against fakes and network-free construction
in `tests/test_paper_equities_profile.py`).

**Module layout.** This file is the composition root: it defines the root `cli` group, the
broker-touching commands (`fetch`, `agent`, `monitor`, `simulate`, `assets`) that share the
`_build_broker` seam, and the remaining top-level commands. The broker-free command groups live
in `keel/commands/*` and are registered here via `cli.add_command(...)`: `db`, `trials`,
`withdrawals`, `autonomy`, `rules`, `subscription`, `versions`. The shared seams
(`with_disclaimer`, the confirmation gate, `_open_repo`/`_load_cfg`/`_build_broker`) live in
`keel.commands._common` and are re-imported here; `_is_interactive` is reached as
`_common._is_interactive()` so a single patch point in `keel.commands._common` drives every gate
wherever its command is defined.

**Thin by construction (issue #387 C1, the TUI PRD's O2).** Every command body here that used
to carry logic now delegates to a service in `keel/commands/*`, so the CLI and the TUI are two
front-ends over ONE implementation: `fetch` -> `commands.fetch.run_fetch`, `monitor` ->
`commands.monitor.run_monitor`, `simulate` -> `commands.simulate.run_simulation`, the `assets`
decision layer -> `commands.assets` (`screen_product` is THE admission gate), the order-preview
confirm gate -> `commands.confirm._interactive_confirm`, `kill`/`resume`/`resume-entries`/
`record-flow`/`reset-hwm` -> `commands.trading`, `pnl` -> `commands.pnl`,
`purification`'s rendering -> `commands.purification`. A command body here parses click
options, builds the broker at the `_build_broker` seam (LAZILY, where the old body did), calls
the service, and prints/raises -- nothing else. The names the tests pin through THIS module
(`_screen_product`, `_assess_products`, `_SIM_SLIPPAGE_PCT`, `_interactive_confirm`, the
preview markers, `history_mod`/`repair_mod`) are re-imports of those same service objects, not
copies: `tests/commands/test_service_parity.py` pins the identity, and
`tests/commands/test_service_isolation.py` pins that the service layer never imports this file.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import click

from keel import agent
from keel.commands._common import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    DISCLAIMER,
    _build_broker,
    _load_cfg,
    _open_repo,
    _require_interactive_confirmation,
    with_disclaimer,
)
from keel.commands._products import (
    _default_sim_products,
    # No longer used in this file (holdings derives products inside the service layer now), but
    # imported on purpose: `tests/compliance/test_assets_cli.py` pins the product-id derivation
    # THROUGH `keel.cli` as the surface an operator's config meets.
    _history_product,  # noqa: F401 -- pinned by tests
    parse_products_option,
)
from keel.commands.assets import VENUE as _VENUE
from keel.commands.assets import (
    broker_auth_hint,
    gather_holdings,
    render_discover,
    render_holdings,
    render_screened_asset,
    run_discovery,
    screen_products,
)
from keel.commands.assets import screen_product as _screen_product
from keel.commands.autonomy import autonomy_group
from keel.commands.brokers import brokers_group

# The order-preview confirmation gate lives in `keel.commands.confirm` (issue #387 C1: the CLI
# and the TUI must hand the executor ONE confirm function, never a front-end copy). Re-imported
# here so the tests' `cli_module.*` pins -- the marker strings, and `_interactive_confirm`'s
# identity as the very object `agent` receives -- keep resolving to these exact objects.
from keel.commands.confirm import (  # noqa: F401 -- deliberate re-export, pinned by tests
    DEGRADED_PREVIEW_PHRASE,
    NATIVE_PREVIEW_MARKER,
    SYNTHETIC_PREVIEW_MARKER,
    UNPRICED_PREVIEW_MARKER,
    UNREADABLE_PREVIEW_MARKER,
    _interactive_confirm,
)
from keel.commands.db import db_group
from keel.commands.fetch import assess_products as _assess_products  # noqa: F401 -- pinned by tests

# The fetch flow (freshness sweep, --check verdict, ensure/repair pass) lives in
# `keel.commands.fetch` (issue #387 C1) so the TUI's Data menu dispatches to exactly what the
# CLI calls. `_assess_products` is re-imported because the fetch tests pin the sweep's
# window-bounded invariant THROUGH this module's name.
from keel.commands.fetch import run_fetch
from keel.commands.insights import insights_group
from keel.commands.monitor import run_monitor
from keel.commands.pnl import build_pnl_report, render_pnl_report
from keel.commands.purification import render_purification_report
from keel.commands.rules import rules_group, rules_seed
from keel.commands.simulate import (
    SIM_SLIPPAGE_PCT as _SIM_SLIPPAGE_PCT,  # noqa: F401 -- pinned by tests
)

# The whole simulate assembly (constants, candle loading, slippage pass, account metrics, tier
# matrix, report write) lives in `keel.commands.simulate` (issue #387 C1). `_SIM_SLIPPAGE_PCT`
# is re-imported because the simulate tests pin, through THIS module's name, that the flat rate
# is structurally the engine's floor.
from keel.commands.simulate import run_simulation
from keel.commands.status import status_cmd
from keel.commands.subscription import subscription_group
from keel.commands.trading import (
    KILL_ENGAGED_LINE,
    RECORD_FLOW_DETAIL,
    RESET_HWM_ACTION,
    RESET_HWM_DETAIL,
    RESET_HWM_DONE_LINE,
    RESUME_ACTION,
    RESUME_DETAIL,
    RESUME_DISENGAGED_LINE,
    RESUME_ENTRIES_ACTION,
    RESUME_ENTRIES_CLEARED_LINE,
    RESUME_ENTRIES_DETAIL,
    clear_consecutive_loss_halt,
    disengage_kill_switch,
    engage_kill_switch,
    parse_flow_amount,
    record_flow_action,
    render_blocked_entries,
    render_flow_recorded,
    render_loop_result,
    reset_high_water_mark,
)
from keel.commands.trading import record_flow as record_declared_flow
from keel.commands.trials import trials_group
from keel.commands.tui import tui_cmd
from keel.commands.versions import versions_cmd
from keel.commands.withdrawals import withdrawals_group
from keel.compliance import purification as purification_mod
from keel.compliance import screen as screen_mod
from keel.config import Config
from keel.data import freshness as freshness_mod
from keel.data import history as history_mod  # noqa: F401 -- fetch/simulate tests patch this alias
from keel.data import repair as repair_mod  # noqa: F401 -- fetch tests patch cli_module.repair_mod
from keel.data.db import connect, migrate
from keel.research import ledger as trials_ledger
from keel.version import build_info, check_install

# -- root group ---------------------------------------------------------------------------------


def _print_version(ctx: click.Context, param: object, value: bool) -> None:
    """Eager `--version`: print the build identity and exit before any command runs.

    Prints the working-tree state too. For a tool that can place orders, "0.1.0 (abc123, DIRTY)"
    and "0.1.0 (abc123)" are materially different claims -- the first corresponds to no commit
    and cannot be reproduced.

    The line describes the `keel-trader` distribution ONLY, which is exactly how a deployment came
    to run `keel-trader 0.5.7` against `keel-core 0.5.5` while this reported the new number. It
    cannot be widened without changing what `--version` means, so instead it warns when the rest
    of the install disagrees and points at `keel versions`, which reports all of them and exits
    non-zero. The warning goes to stderr so the string this prints stays exactly what it was.
    """
    if not value or ctx.resilient_parsing:
        return
    info = build_info()
    click.echo(info.describe())
    if not info.is_reproducible:
        click.echo(
            "warning: this build is NOT reproducible -- it does not correspond to a commit. "
            "Do not run it against live funds.",
            err=True,
        )
    if not check_install(source=info.source).is_consistent:
        click.echo(
            "warning: PARTIAL INSTALL -- this line reports the keel-trader distribution only, "
            "and the other keel distributions do not agree with it. Run `keel versions`.",
            err=True,
        )
    ctx.exit()


@click.group()
@click.option(
    "--version",
    is_flag=True,
    callback=_print_version,
    expose_value=False,
    is_eager=True,
    help="Show the running version, commit and working-tree state, then exit.",
)
@click.option(
    "--db", "db_path", default=DEFAULT_DB_PATH, show_default=True, help="SQLite DB path."
)
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    help="config.yaml path.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Log major operations/decisions (INFO), not just errors -- overrides config.yaml's "
    "logging.verbose. Errors/exceptions are always logged regardless of this flag.",
)
@click.pass_context
def cli(
    ctx: click.Context, db_path: str, config_path: str, verbose: bool
) -> None:
    """keel: an offline-first, halal, guard-railed Coinbase auto-trading agent."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    ctx.obj["config_path"] = config_path
    ctx.obj["verbose"] = verbose


# -- init (scaffold a working directory) ----------------------------------------------------


def _template_config_text(live: bool = False) -> str:
    """A config.yaml template shipped inside the wheel (see pyproject `artifacts`).

    `live=False` returns the dev template (`mode: paper` -- places nothing). `live=True` returns
    the production template (`mode: confirm` -- previews every order and waits for approval),
    which is also the `config.yaml` attached to a GitHub Release.
    """
    from importlib.resources import files

    name = "config.live.yaml" if live else "config.yaml"
    return (files("keel.templates") / name).read_text(encoding="utf-8")


@cli.command("init-config")
@click.option(
    "--config", "config_path", default=DEFAULT_CONFIG_PATH, show_default=True,
    help="Where to write the config file.",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite an existing config.")
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Write the PRODUCTION template (mode: confirm) instead of the dev one (mode: paper).",
)
def init_config(config_path: str, force: bool, live: bool) -> None:
    """Write a default `config.yaml` into the current directory, ready to edit.

    The installed wheel ships both templates so a fresh working directory has a config to start
    from -- edit `allowlist`, `caps`, and `auto_trade.mode` before running anything live.

    `--live` writes the same production config that is attached to a GitHub Release: real
    allowlist/caps in `mode: confirm`, which previews every order and waits for your approval.
    Without it you get the dev template in `mode: paper`, which places nothing at all.
    """
    path = Path(config_path)
    if path.exists() and not force:
        raise click.ClickException(f"{path} already exists; pass --force to overwrite")
    path.write_text(_template_config_text(live=live), encoding="utf-8")
    which = "production/confirm" if live else "dev/paper"
    click.echo(f"wrote {path} [{which}]. Review allowlist/caps/auto_trade before going live.")


@cli.command("init")
@click.option(
    "--config", "config_path", default=DEFAULT_CONFIG_PATH, show_default=True,
    help="Config file to write.",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite an existing config.")
@click.pass_context
def init_cmd(ctx: click.Context, config_path: str, force: bool) -> None:
    """Scaffold a working directory: write `config.yaml`, then seed the rules table (candidates).

    A convenience for a fresh install -- equivalent to `keel init-config` followed by
    `keel rules seed`. Seeds `candidate` rules only; promoting to paper/live is a separate,
    deliberate step.
    """
    ctx.invoke(init_config, config_path=config_path, force=force)
    ctx.invoke(rules_seed, products=None, kinds=None, force=False, status="candidate")


# -- migrate (schema evolution for an EXISTING database) -------------------------------------


def _current_schema_version(conn: Any) -> int:
    """The stored schema version, or 0 when the database has no schema at all yet."""
    present = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if present is None:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row["version"]) if row is not None else 0


@cli.command("migrate")
@click.option(
    "--db",
    "db_override",
    default=None,
    help="Database file to migrate (default: the global --db / keel.db).",
)
@click.pass_context
def migrate_cmd(ctx: click.Context, db_override: str | None) -> None:
    """Apply outstanding schema migrations to an existing database (idempotent, schema-only).

    This is the counterpart to `keel init`, and the two are deliberately NOT the same thing:

    * `keel init` bootstraps a FRESH deployment -- it writes a config and seeds the strategy
      (rules) library as `candidate`s.
    * `keel migrate` evolves the SCHEMA of an EXISTING database and **never seeds**. Re-seeding
      on migrate would resurrect rules that were deliberately deleted or refuted.

    Runs `keel.data.db.migrate`, which steps the stored `schema_version` up incrementally and is
    safe to call repeatedly. No network, no confirmation gate, no orders -- safe against a
    live database.
    """
    path = db_override or ctx.obj["db_path"]
    conn = connect(path)

    before = _current_schema_version(conn)
    migrate(conn)
    after = _current_schema_version(conn)

    if after > before:
        click.echo(f"migrated {path}: schema {before} -> {after}")
    else:
        click.echo(f"{path}: already at schema {after}, nothing to do")


# -- db import ------------------------------------------------------------------------------

# The `db` group is defined in `keel.commands.db`; register it here.
cli.add_command(db_group)


# -- fetch ----------------------------------------------------------------------------------


@cli.command("fetch")
@click.option("--products", default=None, help="Comma-separated product ids (default: allowlist).")
@click.option("--years", default=5, show_default=True, help="Years of history to ensure.")
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Report freshness and exit WITHOUT touching the network. Exits non-zero if any "
    "series is missing or stale -- so a scheduler can alert on it.",
)
@click.option(
    "--fail-on-gaps",
    is_flag=True,
    default=False,
    help="Also fail --check on internal gaps. Off by default: ensure_history cannot repair "
    "them, and an alert on an unfixable condition is an alert you learn to ignore.",
)
@click.option(
    "--refresh", is_flag=True, default=False, help="Re-pull from scratch, ignoring cache."
)
@click.option(
    "--repair-gaps",
    is_flag=True,
    default=False,
    help="Also re-fetch interior holes window by window. A window the venue cannot supply is "
    "recorded as absent at source and skipped on later runs.",
)
@click.option(
    "--reprobe-absent",
    is_flag=True,
    default=False,
    help="With --repair-gaps, ignore previously recorded absences and ask again.",
)
@click.option(
    "--tolerance-bars",
    default=freshness_mod.DEFAULT_TOLERANCE_BARS,
    show_default=True,
    help="Bars of lag tolerated before a series counts as stale.",
)
@click.pass_context
@with_disclaimer
def fetch(
    ctx: click.Context,
    products: str | None,
    years: int,
    check: bool,
    fail_on_gaps: bool,
    refresh: bool,
    repair_gaps: bool,
    reprobe_absent: bool,
    tolerance_bars: int,
) -> None:
    """Ensure cached candle history is current for every allowlisted product.

    READ-ONLY with respect to money: this command fetches market data and writes candles. It
    places no orders, touches no rails and reads no credentials beyond the venue's public
    market-data endpoints -- which is why it is safe to schedule (see
    `docs/operations/scheduled-fetch.md`).

    `--check` is the dry-run a scheduler should alert on: it never opens a network connection
    and exits non-zero when anything is missing, stale or gapped.

    The flow itself lives in `keel.commands.fetch.run_fetch` (issue #387 C1); this wrapper
    parses the options, hands the service a lazy `_build_broker` factory (so `--check` and the
    all-current skip still never construct a broker), and turns the service's `error` into the
    command's exit code.
    """
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    product_list = _parse_products_option(products, config)

    result = run_fetch(
        repo,
        config,
        lambda: _build_broker(config),
        db_path=ctx.obj["db_path"],
        products=product_list,
        years=years,
        now_ts=int(time.time()),
        tolerance_bars=tolerance_bars,
        check=check,
        fail_on_gaps=fail_on_gaps,
        refresh=refresh,
        repair_gaps=repair_gaps,
        reprobe_absent=reprobe_absent,
        echo=click.echo,
        echo_err=lambda message: click.echo(message, err=True),
    )
    if result.error is not None:
        raise click.ClickException(result.error)


# -- assets (allowlist admission screening) -------------------------------------------------


@cli.group("assets")
def assets_group() -> None:
    """Allowlist admission screening (KB §28.4/§65.5) -- a CURATION gate, not a per-trade rail."""


@assets_group.command("holdings")
@click.option(
    "--min-balance", default="0", show_default=True,
    help="Ignore balances at or below this (dust from airdrops, forks and rounding).",
)
@click.option(
    "--screen", "run_screen", is_flag=True, default=False,
    help="Also run each holding through the admission screen.",
)
@click.pass_context
@with_disclaimer
def assets_holdings(ctx: click.Context, min_balance: str, run_screen: bool) -> None:
    """List the assets you actually hold at the broker, as allowlist CANDIDATES.

    This is a SOURCE, not a gate. **Holding an asset is not a reason to trade it**, so this
    command admits nothing and mutates nothing -- no attestation, no allowlist change, no data
    write. (Opening the database does apply any pending schema migration, as every command
    does.) It answers "what do I already own that this system might trade?", where
    `keel assets discover` answers "what could anyone trade?".

    With `--screen`, each holding goes through the SAME fail-closed screen as
    `keel assets screen`: unattested assets are REJECTED, because sector and backing cannot be
    derived from a balance any more than from a price.
    """
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    try:
        floor = Decimal(min_balance)
    except InvalidOperation as exc:
        raise click.BadParameter(f"--min-balance must be a number; got {min_balance!r}") from exc
    if not floor.is_finite() or floor < 0:
        # A NaN floor makes every `balance > floor` comparison raise; a negative one lists every
        # dust and zero balance as a candidate. Same guard `record-flow`/`subscription set` use.
        raise click.BadParameter(f"--min-balance must be finite and >= 0; got {min_balance!r}")

    try:
        accounts = _build_broker(config).get_accounts()
    except Exception as exc:  # noqa: BLE001 -- an unreachable venue is an error, not "nothing held"
        # Includes broker CONSTRUCTION, so a missing/invalid `.env` credential surfaces here
        # rather than as a raw traceback. Reporting an empty list instead would read as
        # "you hold nothing", which is not what we learned. The auth hint (`broker_auth_hint`,
        # shared with the TUI) names the keys the CONFIG'S venue actually reads.
        raise click.ClickException(
            f"could not read balances from the broker: {exc}\n"
            f"  If this is an authentication error, check {broker_auth_hint(config)}."
        ) from exc

    report = gather_holdings(repo, config, accounts, floor, run_screen=run_screen)
    for line in render_holdings(report):
        click.echo(line)


@assets_group.command("discover")
@click.option("--quote", default=None, help="Settlement currency (default: config.quote_currency).")
@click.option(
    "--min-volume-24h", default="100000", show_default=True,
    help="Cheap pre-filter on the venue's reported 24h quote volume. Bounds the request count; it "
    "is NOT a liquidity criterion -- a 24h snapshot is a different statistic from the median the "
    "gate applies, so use --probe-liquidity for that. Deliberately set well BELOW the admission "
    "floor, not equal to it: a 24h snapshot and the gate's all-cached-history median are "
    "different statistics, so an equal number does not make discovery non-stricter than the gate "
    "it feeds -- a quiet trading day can push the snapshot under a floor the asset's own median "
    "clears many times over.",
)
@click.option(
    # Raised from 25 to 100 on 2026-08-15: lowering --min-volume-24h's floor (see that option's
    # own help text) grew a typical sweep from ~35 candidates to ~130, sorted by descending 24h
    # volume, so the assets that floor change exists to surface can land well past rank 25 and
    # get cut off by this option before an operator ever sees them. 100 shows nearly all of a
    # typical sweep at no extra cost: with NEITHER probe flag, `discover` makes exactly ONE venue
    # request regardless of --limit -- filtering and sorting are local. --probe-history and
    # --probe-liquidity are the ones with a per-row cost, called out below.
    "--limit", default=100, show_default=True,
    help="Show at most this many candidates. With neither --probe-history nor --probe-liquidity, "
    "raising this costs nothing extra -- discovery still makes exactly one venue request. Each "
    "probe flag adds one venue request PER CANDIDATE SHOWN (two requests per row if both are "
    "given), so a large --limit combined with a probe flag multiplies the request count.",
)
@click.option(
    "--probe-history",
    is_flag=True,
    default=False,
    help="One extra request per candidate: does daily history exist at the 4-year mark? A "
    "candidate that fails this can never clear the screen, so probing first avoids spending "
    "attestation effort on it.",
)
@click.option(
    "--probe-liquidity",
    is_flag=True,
    default=False,
    help="One extra request per candidate: sample recent daily candles and compute the SAME "
    "median-quote-volume statistic the admission screen applies. The default 24h filter is a "
    "one-day snapshot and can sit 100x above or 6x below an asset's typical day, so a candidate "
    "can clear the sweep and then fail the gate on liquidity (or nearly be dropped when it would "
    "have passed comfortably). Estimator, not a verdict -- see the note below the table.",
)
@click.pass_context
@with_disclaimer
def assets_discover(
    ctx: click.Context,
    quote: str | None,
    min_volume_24h: str,
    limit: int,
    probe_history: bool,
    probe_liquidity: bool,
) -> None:
    """PROPOSE allowlist candidates from venue metadata. Admits nothing.

    A cheap pre-filter whose only job is to cut ~900 products to a shortlist worth pulling five
    years of candles for. Sector and backing are NOT considered here and cannot be -- every
    candidate below is still REJECTED by `keel assets screen` until a human attests it.
    """
    config = _load_cfg(ctx)
    client = _build_broker(config)
    products = client.list_products()

    sweep = run_discovery(
        client,
        products,
        config,
        quote=quote,
        min_volume_24h=Decimal(min_volume_24h),
        limit=limit,
        probe_history=probe_history,
        probe_liquidity=probe_liquidity,
    )
    for line in render_discover(sweep):
        click.echo(line)


@assets_group.command("screen")
@click.option("--products", default=None, help="Comma-separated product ids (default: allowlist).")
@click.pass_context
@with_disclaimer
def assets_screen(ctx: click.Context, products: str | None) -> None:
    """Screen assets for allowlist admission. Unattested assets FAIL CLOSED.

    Sector and backing cannot be derived from price data, so an asset nobody has classified is
    unknown -- and unknown is a rejection, not a default pass.
    """
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    # Deliberately UNVALIDATED, unlike every other `--products` caller: screening is the command
    # that answers "may keel trade this, and why not", and `screen_asset` has BOTH id-derived
    # criteria of its own -- `settlement` (the quote leg vs `config.quote_currency`) and
    # `spot_instrument` (the whole id vs rail 19's spot grammar) -- so it REJECTs a cross-settled
    # or derivative-shaped id with the same reason a usage error would have carried, plus the
    # history/liquidity/attestation verdicts a usage error would have suppressed. Validating
    # here would replace that reasoned answer with a refusal to answer.
    #
    # That exemption is only honest because those criteria are real. Settlement alone was not
    # enough: `quote_currency_of("BTC-PERP-USD")` is `"USD"`, so before the `spot_instrument`
    # criterion this command ADMITted the one product shape rail 19 exists to veto.
    product_list, _ = parse_products_option(products, config, validate=False)

    screened = screen_products(repo, config, product_list)
    for entry in screened:
        for line in render_screened_asset(entry):
            click.echo(line)
    click.echo(f"\n{sum(1 for s in screened if s.admitted)}/{len(screened)} admitted")


@assets_group.command("propose")
@click.option(
    "--from", "from_file", required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON shortlist file produced OUTSIDE keel (an LLM + web-search scout).",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
@click.pass_context
def assets_propose(ctx: click.Context, from_file: str, as_json: bool) -> None:
    """Screen an externally-produced LLM asset shortlist. ADMITS NOTHING.

    The shortlist is produced outside keel (you, or your Claude + the firecrawl skills). Each
    candidate is routed through the SAME admission gate as `assets screen`; unattested or
    history-less candidates fail closed. This command never attests, never edits the allowlist,
    never writes to the DB -- it only reports verdicts and next steps.
    """
    from keel.proposer import (
        ProposalError,
        build_proposal_report,
        parse_proposal,
        render_proposal_report,
        report_to_jsonable,
    )

    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    try:
        raw = json.loads(Path(from_file).read_text())
    # `UnicodeDecodeError` subclasses ValueError, NOT OSError, so it needs naming here or a
    # non-UTF-8 shortlist (a scout run on Windows writes UTF-16LE+BOM -- valid JSON, undecodable
    # as UTF-8) crashes out with a raw traceback instead of this message. Same fix, same reason,
    # as `keel.commands.admission.build_propose_view`'s own read.
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"could not read/parse {from_file}: {exc}") from exc
    try:
        parsed = parse_proposal(raw)
    except ProposalError as exc:
        raise click.ClickException(str(exc)) from exc

    report = build_proposal_report(
        parsed, repo, config.quote_currency, config.allowlist, _screen_product
    )
    if as_json:
        click.echo(json.dumps(report_to_jsonable(report), indent=2, default=str))
        return
    for line in render_proposal_report(report):
        click.echo(line)
    click.echo("")
    click.echo(DISCLAIMER)


@assets_group.command("attest")
@click.option("--asset", required=True, help="Asset code, e.g. BTC.")
@click.option("--sector", required=True, help="Core business line / purpose of the token.")
@click.option(
    "--backing",
    required=True,
    type=click.Choice(sorted(screen_mod.KNOWN_BACKINGS)),
    help="'ayn (an owned thing), dayn (a claim on an issuer), or native (a base-layer coin).",
)
@click.option("--pays-yield", is_flag=True, default=False, help="Holding it earns a return.")
@click.option("--source", required=True, help="Where this was established: URL, standard, ruling.")
@click.option("--attested-by", required=True, help="Who established it.")
@click.pass_context
@with_disclaimer
def assets_attest(
    ctx: click.Context,
    asset: str,
    sector: str,
    backing: str,
    pays_yield: bool,
    source: str,
    attested_by: str,
) -> None:
    """Record an asset's shariah classification. These are facts about the world, not defaults.

    Not passphrase-gated: an attestation cannot itself place an order or raise a cap, and the
    screen it feeds only ever ADMITS to a list that `guards.py` rail 1 still enforces per-trade.
    """
    repo = _open_repo(ctx)
    repo.upsert_asset_attestation(
        asset=asset,
        sector=sector,
        backing=backing,
        pays_yield=pays_yield,
        source=source,
        attested_by=attested_by,
        attested_at=int(time.time()),
    )
    click.echo(f"attested {asset}: sector={sector} backing={backing} pays_yield={pays_yield}")


@assets_group.command("attest-instrument")
@click.option("--venue", default=_VENUE, show_default=True, help="Venue the product is listed on.")
@click.option("--product", required=True, help="Venue product id, e.g. BTC-USD.")
@click.option(
    "--wrapper",
    required=True,
    type=click.Choice(sorted(screen_mod.KNOWN_WRAPPERS)),
    help="What CONTRACT this listing is. Only 'spot' admits; every other value is a refusal.",
)
@click.option(
    "--source",
    required=True,
    help="Where this was established: the venue's contract spec, its API docs, a filing.",
)
@click.option("--attested-by", required=True, help="Who established it.")
@click.pass_context
@with_disclaimer
def assets_attest_instrument(
    ctx: click.Context,
    venue: str,
    product: str,
    wrapper: str,
    source: str,
    attested_by: str,
) -> None:
    """Record what CONTRACT a venue listing is. A claim about the PRODUCT, not the asset.

    `keel assets attest` says what the underlying is -- sector, backing, yield. This says what
    you actually get when you buy this listing, and the two are genuinely independent: the honest
    asset attestation for the underlying of a BTC CFD is BTC's existing, already-admitted one, so
    nothing recorded there can ever surface the leverage, swap financing or counterparty exposure
    that the CFD adds (issue #202).

    Keyed per `(venue, product)` rather than per asset because one venue lists several contracts
    on the same base leg -- Coinbase quotes both `BTC-USD` and `BTC-PERP-USD` -- so a per-asset
    wrapper claim would be wrong on the venue keel already uses, not merely imprecise later.

    This is ATTESTED and cannot be derived. The id's shape does not answer it (a CFD broker
    spells its contract `BTC-USD`, identical to spot), and the venue's own `product_type` field
    is its self-report about its own product -- excellent evidence to cite in `--source`, and not
    a substitute for a human making the claim.

    Not passphrase-gated, for the same reason `keel assets attest` is not: an attestation cannot
    itself place an order or raise a cap, and the screen it feeds only ever ADMITS to a list that
    `guards.py` rail 1 still enforces per-trade.
    """
    product = product.upper()  # matches the uppercase ids `_screen_product` looks up by
    repo = _open_repo(ctx)
    repo.upsert_instrument_attestation(
        venue=venue,
        product_id=product,
        wrapper=wrapper,
        source=source,
        attested_by=attested_by,
        attested_at=int(time.time()),
    )
    click.echo(f"attested {product} on {venue}: wrapper={wrapper}")


@assets_group.command("exempt")
@click.option("--asset", required=True, help="Asset code, e.g. PAXG.")
@click.option(
    "--criterion",
    required=True,
    type=click.Choice(sorted(screen_mod.WAIVABLE_CRITERIA)),
    help="The admission criterion to waive. Restricted to WAIVABLE_CRITERIA -- a DATA/market "
    "criterion, never a shariah one.",
)
@click.option("--rationale", required=True, help="Why this waiver is granted.")
@click.option("--granted-by", required=True, help="Who granted it.")
@click.pass_context
@with_disclaimer
def assets_exempt(
    ctx: click.Context, asset: str, criterion: str, rationale: str, granted_by: str
) -> None:
    """Record a DOCUMENTED exception waiving one admission criterion for one asset.

    This waives ONLY a computed DATA/market criterion (history depth today) -- never a shariah
    one (a missing attestation, haram sector, riba yield, or dayn/unknown backing): the
    `--criterion` Choice is restricted to `screen_mod.WAIVABLE_CRITERIA`, so this command cannot
    reach those checks no matter what is typed. The exception is recorded and then surfaced
    loudly by `keel assets screen` as a WARNING, never silently -- it is not a default pass. It
    is also self-retiring: once the underlying condition it was granted for no longer holds (the
    asset accumulates enough history, say), `screen_asset` stops mentioning it at all.

    Not passphrase-gated, for the same reason `keel assets attest` is not: recording an exception
    cannot itself place an order or raise a cap, and the screen it feeds only ever ADMITS to a
    list that `guards.py` rail 1 still enforces per-trade regardless.
    """
    if not rationale.strip():
        # Mirrors the unsourced-attestation guard in `screen_asset` (`if not
        # attestation.source.strip()`): an unsourced claim is not evidence, and a blank rationale
        # is not documentation -- it would be an "undocumented documented exception."
        raise click.BadParameter(
            "rationale must be a non-empty documented reason", param_hint="--rationale"
        )
    asset = asset.upper()  # matches the uppercase asset `_screen_product` looks waivers up by
    repo = _open_repo(ctx)
    repo.upsert_screen_exception(
        asset=asset,
        criterion=criterion,
        rationale=rationale,
        granted_by=granted_by,
        granted_at=int(time.time()),
    )
    click.echo(f"recorded exception: {asset} waives '{criterion}' criterion (by {granted_by})")


@assets_group.command("unexempt")
@click.option("--asset", required=True, help="Asset code, e.g. PAXG.")
@click.option(
    "--criterion",
    required=True,
    type=click.Choice(sorted(screen_mod.WAIVABLE_CRITERIA)),
    help="The waived criterion to revoke.",
)
@click.pass_context
@with_disclaimer
def assets_unexempt(ctx: click.Context, asset: str, criterion: str) -> None:
    """Revoke a documented allowlist-screen exception. A de-risking action, always allowed.

    After this, `keel assets screen` re-evaluates the criterion normally -- if it still fails,
    the asset is rejected again.
    """
    asset = asset.upper()  # matches the uppercase asset `assets exempt` records under
    repo = _open_repo(ctx)
    removed = repo.delete_screen_exception(asset, criterion)
    if removed:
        click.echo(f"revoked exception: {asset} no longer waives '{criterion}' criterion")
    else:
        click.echo(f"no such exception: {asset} has no '{criterion}' waiver")


@assets_group.command("list")
@click.pass_context
def assets_list(ctx: click.Context) -> None:
    """List recorded attestations and any documented screen exceptions.

    Both KINDS of attestation are shown, because admission now requires both and an operator
    reading only the asset list would see a fully-attested allowlist that still screens REJECT.
    """
    repo = _open_repo(ctx)
    rows = repo.get_asset_attestations()
    instruments = repo.get_instrument_attestations()
    exceptions = repo.list_screen_exceptions()
    if not rows and not instruments and not exceptions:
        click.echo("no attestations recorded")
        return
    for row in rows:
        click.echo(
            f"{row['asset']:<8} sector={row['sector']:<16} backing={row['backing']:<8} "
            f"pays_yield={bool(row['pays_yield'])!s:<5} by={row['attested_by']}"
        )
    if instruments:
        click.echo("\ninstruments:")
        for row in instruments:
            click.echo(
                f"{row['product_id']:<14} venue={row['venue']:<10} "
                f"wrapper={row['wrapper']:<16} by={row['attested_by']}"
            )
    if exceptions:
        click.echo("\nexceptions:")
        for row in exceptions:
            click.echo(
                f"{row['asset']:<8} waives={row['criterion']:<10} by={row['granted_by']} -- "
                f"{row['rationale']}"
            )


# -- withdrawals ------------------------------------------------------------------------------

# The `withdrawals` group is defined in `keel.commands.withdrawals`; register it here.
cli.add_command(withdrawals_group)


# -- purification ------------------------------------------------------------------------------


@cli.command("purification")
@click.pass_context
@with_disclaimer
def purification(ctx: click.Context) -> None:
    """Report non-compliant income owed to charity (KB §65.9).

    ⛔ REPORT-ONLY. The agent never disposes of funds -- it computes an amount owed and says so,
    exactly as the zakat estimate does. Moving it is the operator's act.

    Any credit that is not sale proceeds, an own deposit, or an asset transfer -- interest,
    rewards, staking, rebates, promotional yield -- is segregated here, excluded from realised
    P&L, and reported as owed. Unrecognised types are surfaced for review rather than silently
    treated either way.
    """
    repo = _open_repo(ctx)
    report = purification_mod.build_report(repo.get_transactions())
    for line in render_purification_report(report):
        click.echo(line)


# -- trials ledger --------------------------------------------------------------------------

# The `trials` group is defined in `keel.commands.trials` (a seam-free, ledger-only group);
# register it on the root CLI here.
cli.add_command(trials_group)


# -- monitor ------------------------------------------------------------------------------


@cli.command()
@click.option("--loop", is_flag=True, default=False, help="Poll repeatedly instead of once.")
@click.option(
    "--interval",
    "interval_sec",
    type=float,
    default=None,
    help="Seconds between polls with --loop (default: config auto_trade.interval_sec).",
)
@click.option(
    "--max-cycles",
    type=int,
    default=None,
    help="Stop --loop after N cycles (default: run until interrupted).",
)
@click.pass_context
@with_disclaimer
def monitor(
    ctx: click.Context, loop: bool, interval_sec: float | None, max_cycles: int | None
) -> None:
    """Poll fresh candles for every allowlisted product (read-only).

    The loop itself lives in `keel.commands.monitor.run_monitor` (issue #387 C1); this wrapper
    parses the options and echoes.
    """
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    broker = _build_broker(config)
    products = _default_sim_products(config)
    granularities = list(config.market_data.granularities)
    interval = interval_sec if interval_sec is not None else config.auto_trade.interval_sec

    run_monitor(
        broker,
        repo,
        config,
        products,
        granularities,
        interval,
        loop=loop,
        max_cycles=max_cycles,
        echo=click.echo,
        sleep_fn=time.sleep,
        now_fn=lambda: int(time.time()),
    )


# -- agent ------------------------------------------------------------------------------


def _print_loop_result(result: agent.LoopResult) -> None:
    for line in render_loop_result(result):
        click.echo(line)


@cli.command()
@click.option(
    "--loop", is_flag=True, default=False, help="Run the scheduled loop, not one cycle."
)
@click.option(
    "--interval",
    "interval_sec",
    type=float,
    default=None,
    help="Seconds between cycles with --loop (default: config auto_trade.interval_sec).",
)
@click.option(
    "--max-cycles",
    type=int,
    default=None,
    help="Stop --loop after N cycles (default: run until interrupted).",
)
@click.pass_context
@with_disclaimer
def agent_cmd(
    ctx: click.Context,
    loop: bool,
    interval_sec: float | None,
    max_cycles: int | None,
) -> None:
    """Run the agent loop. Every order is hard-rail-guarded, in every mode.

    Whether orders are placed at all comes from `config.auto_trade.mode` (`paper` simulates,
    `confirm` is live). Whether you are ASKED comes from your profile: with autonomy off (the
    default) each order needs your approval at the terminal; with `keel autonomy on` it does not.
    """
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    broker = _build_broker(config)

    if not loop:
        confirm_fn = _interactive_confirm
        result = agent.run_once(
            broker, repo, config, now_ts=int(time.time()), confirm_fn=confirm_fn
        )
        _print_loop_result(result)
        if result.skipped and result.skip_reason == "market_clock_unavailable":
            # A clock that could not be READ is a transient outage, not the day's work: exit
            # nonzero so a day-stamping wrapper (paper-equities-run.sh) declines to stamp and
            # the next trigger retries -- see `agent.MARKET_CLOCK_UNAVAILABLE_EXIT`'s
            # docstring. The market_closed skip deliberately falls through to exit 0: stamping
            # a closed day is correct cadence bookkeeping.
            ctx.exit(agent.MARKET_CLOCK_UNAVAILABLE_EXIT)
        if result.blocked_entries:
            # Finding 1 (HIGH): a green exit here is exactly what lets a cron/LaunchAgent
            # wrapper stamp the day as done and never retry -- see `agent.DATA_NOT_READY_EXIT`'s
            # docstring for the mechanism this closes (duplicate order -> bounded delay).
            for blocked_line in render_blocked_entries(result):
                click.echo(blocked_line, err=True)
            ctx.exit(agent.DATA_NOT_READY_EXIT)
        return

    interval = interval_sec if interval_sec is not None else config.auto_trade.interval_sec

    def stop_flag(_count: list[int] = [0]) -> bool:  # noqa: B006 - intentional mutable counter
        if max_cycles is not None and _count[0] >= max_cycles:
            return True
        _count[0] += 1
        return False

    # Deliberately NO `ctx.exit(agent.DATA_NOT_READY_EXIT)` here, unlike the non-`--loop` branch
    # above: a long-running loop process is supposed to skip a blocked cycle and try again next
    # `interval`, exactly like it already does for a stale feed or the kill-switch -- exiting
    # the process would take the whole scheduled loop down over what is usually a transient
    # publication lag, and `agent.loop` has no supervisor watching for this exit code to restart
    # it. `_print_loop_result`'s `blocked=N` token still surfaces it for whoever reads the log.
    confirm_fn = _interactive_confirm
    for result in agent.loop(broker, repo, config, interval, stop_flag, confirm_fn=confirm_fn):
        _print_loop_result(result)


# -- autonomy (the user's own choice, stored in their profile) ------------------------------

# The `autonomy` group is defined in `keel.commands.autonomy`; register it here.
cli.add_command(autonomy_group)


# -- rules ------------------------------------------------------------------------------

# The `rules` group is defined in `keel.commands.rules`; register it here. `rules_seed` is also
# imported by `init` below, which invokes it to seed candidate rules on a fresh install.
cli.add_command(rules_group)


# -- pnl ------------------------------------------------------------------------------


def _parse_marks(raw_marks: tuple[str, ...]) -> dict[str, Decimal]:
    marks: dict[str, Decimal] = {}
    for entry in raw_marks:
        asset, _, price = entry.partition("=")
        if not price:
            raise click.BadParameter(f"expected ASSET=PRICE, got {entry!r}")
        marks[asset] = Decimal(price)
    return marks


@cli.command()
@click.option("--asset", default=None, help="Scope the report to one asset.")
@click.option(
    "--mark",
    "raw_marks",
    multiple=True,
    help="ASSET=PRICE mark for unrealized P&L (repeatable, e.g. --mark BTC=65000).",
)
@click.pass_context
@with_disclaimer
def pnl(ctx: click.Context, asset: str | None, raw_marks: tuple[str, ...]) -> None:
    """Realized + unrealized FIFO P&L report from imported transactions (read-only)."""
    repo = _open_repo(ctx)
    report = build_pnl_report(repo.get_transactions(asset), asset, _parse_marks(raw_marks))
    for line in render_pnl_report(report):
        click.echo(line)


# -- simulate (Sim Task 8) -------------------------------------------------------------------


def _parse_products_option(products: str | None, config: Config) -> list[str]:
    """`--products` for `fetch`/`simulate`: a malformed id is refused, a cross-settled one warns.

    The parse itself lives in `commands._products` so `rules seed` uses the same one. This
    wrapper turns its `ValueError` into a `click.BadParameter` -- a usage error naming the
    offending ids rather than a traceback -- and prints the non-fatal reasons it hands back
    (feasibility study R2).

    `settlement_is_fatal=False` is the difference from `rules seed`, and it is about what each
    command WRITES. Seeding a rule for a product rail 18 vetoes puts a row in the table that the
    agent polls forever; fetching its candles puts market data in the cache, which is exactly
    what an operator needs before `assets screen` can tell them anything about the asset. See
    `parse_products_option`. `monitor` is deliberately absent from this list: it has no
    `--products` option and polls `_default_sim_products` directly.
    """
    try:
        product_list, warnings = parse_products_option(
            products, config, settlement_is_fatal=False
        )
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--products") from exc
    for warning in warnings:
        # Loud, and on stderr-adjacent footing with the other ⚠️ notices: this run is legitimate,
        # but no ORDER for such a product ever will be under the current config.
        click.echo(f"⚠️  {warning}\n    Fetching/simulating it is fine; trading it is not.")
    return product_list


@cli.command()
@click.option("--years", type=int, default=5, show_default=True, help="History depth to simulate.")
@click.option(
    "--products",
    default=None,
    help="Comma-separated product ids (default: the allowlist, in the configured "
    "settlement currency).",
)
@click.option(
    "--contribution",
    default="500",
    show_default=True,
    help="Simulated monthly USD/USDC contribution.",
)
@click.option(
    "--out", "out_path", default=None, type=click.Path(), help="Report output path (Markdown)."
)
@click.option(
    "--artifact", is_flag=True, default=False, help="Also emit the HTML artifact (Sim Task 9)."
)
@click.option(
    "--refresh", is_flag=True, default=False, help="Re-fetch history instead of trusting the cache."
)
@click.option(
    "--no-fetch",
    is_flag=True,
    default=False,
    help="Never touch the network; simulate over whatever is already cached in the DB.",
)
@click.option(
    "--trial-decision",
    type=click.Choice(sorted(trials_ledger.DECISIONS)),
    default="diagnostic_only",
    show_default=True,
    help=(
        "How this run counts in the trials ledger. A plain validation run of the shipped "
        "config is a diagnostic and does NOT increment N (spec §4.4)."
    ),
)
@click.option(
    "--trial-provenance",
    type=click.Choice(sorted(trials_ledger.PROVENANCE)),
    default="a_priori",
    show_default=True,
    help="Whether this configuration came from the KB (a_priori) or from fitting (fitted).",
)
@click.option(
    "--no-trial-record",
    is_flag=True,
    default=False,
    help="Skip appending this run to the trials ledger.",
)
@click.option(
    "--skip-within-cap",
    is_flag=True,
    default=False,
    help=(
        "Skip the extra throttled sim runs for the tier/fee analysis (Issue #86) -- only the "
        "cheap over-cap fee overlay is computed, from the natural run already being done "
        "anyway. Finite-free-volume tiers' within-cap rows are omitted."
    ),
)
@click.pass_context
@with_disclaimer
def simulate(
    ctx: click.Context,
    years: int,
    products: str | None,
    contribution: str,
    out_path: str | None,
    artifact: bool,
    refresh: bool,
    no_fetch: bool,
    trial_decision: str,
    trial_provenance: str,
    no_trial_record: bool,
    skip_within_cap: bool,
) -> None:
    """Simulate the deterministic engine over historical candles (read-only; no confirmation gate).

    Pulls (unless `--no-fetch`) and caches ~`--years` years of candle history in the persistent
    DB (`--db`, never in-memory), replays the real rule set through the engine + a dollar
    account, compares it to a DCA benchmark, and writes a GO-LIVE/TRAIN-MORE report. See
    `docs/superpowers/plans/2026-07-17-engine-validation-simulation.md` Task 8.

    Also computes a Coinbase One subscription-tier/fee analysis (Issue #86): for each configured
    tier (`config.tiers`), whether staying WITHIN its fee-free monthly trading-volume allowance
    (a separate, throttled sim run per finite-free-volume tier) or trading freely and paying the
    taker fee on volume EXCEEDING it ("over cap") nets out ahead. This means up to 3 total sim
    passes (natural + one throttled run per finite-free-volume tier) unless `--skip-within-cap`.
    """
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)
    product_list = _parse_products_option(products, config)
    monthly_contribution = Decimal(contribution)

    # `build_client=None` IS `--no-fetch`: the service never constructs a broker then, which is
    # the no-network contract the tests pin. Otherwise it builds exactly where the old body
    # did -- after option parsing, before the coverage read.
    run_simulation(
        repo,
        config,
        None if no_fetch else (lambda: _build_broker(config)),
        db_path=ctx.obj["db_path"],
        products=product_list,
        years=years,
        monthly_contribution=monthly_contribution,
        now_ts=int(time.time()),
        out_path=Path(out_path) if out_path is not None else None,
        artifact=artifact,
        refresh=refresh,
        trial_decision=trial_decision,
        trial_provenance=trial_provenance,
        no_trial_record=no_trial_record,
        skip_within_cap=skip_within_cap,
        echo=click.echo,
    )


# -- subscription (rail 14, per-venue attested allowance) ----------------------------------------

# The `subscription` group is defined in `keel.commands.subscription`; register it here.
cli.add_command(subscription_group)


# -- status (interim operator-observability dashboard, no broker call) --------------------------

# The paper-mode-fidelity spec deferred a dedicated `keel status` command as a follow-up; it is
# defined in `keel.commands.status` and registered here.
cli.add_command(status_cmd)


# -- tui (live, full-screen operator dashboard, with a help menu and a few gated actions) --------

# `keel status` was built as the substrate for this: `tui_cmd` is a curses view over the same
# `gather_status` report, defined in `keel.commands.tui` and registered here.
cli.add_command(tui_cmd)


# -- insights (read-only promotion-gate + journal reporting, no broker call) --------------------

# A pure VIEW over `gather_status`/`StatusReport`, the repository read methods, and the
# promotion/track-record machinery -- defined in `keel.commands.insights` and registered here.
cli.add_command(insights_group)


# -- versions (the deploy check: every keel distribution, not just this one) ---------------------

# `--version` above answers for `keel-trader` alone and therefore cannot see a partial upgrade;
# this reports the whole install and exits non-zero when it disagrees with itself. Defined in
# `keel.commands.versions` and registered here.
cli.add_command(versions_cmd)


# -- brokers (venues/brokers visibility: capability display over the adapter registry) ------------

# The O7 brokers service (issue #394 C7) -- one payload over `discover_brokers()` and the
# adapters' capability declarations, rendered by BOTH front-ends: this `keel brokers list`
# surface and the console's Venues browser (`keel.commands.console.build_venues_lines`).
# Read-only and offline; capability display only, never key-presence inference.
cli.add_command(brokers_group)


# -- kill / resume ------------------------------------------------------------------------------


@cli.command()
@click.pass_context
@with_disclaimer
def kill(ctx: click.Context) -> None:
    """Engage the kill-switch, halting all trading immediately. Always allowed (safe action)."""
    engage_kill_switch(_open_repo(ctx))
    click.echo(KILL_ENGAGED_LINE)


@cli.command()
@click.pass_context
@with_disclaimer
def resume(ctx: click.Context) -> None:
    """Disengage the kill-switch (dangerous: asks for confirmation)."""
    _require_interactive_confirmation(RESUME_ACTION, RESUME_DETAIL)
    disengage_kill_switch(_open_repo(ctx))
    click.echo(RESUME_DISENGAGED_LINE)


@cli.command(name="resume-entries")
@click.pass_context
@with_disclaimer
def resume_entries(ctx: click.Context) -> None:
    """Clear an armed consecutive-loss halt (rail 16), re-permitting new entries.

    This is the ONLY way to release the halt early: rail 16 reads `streak_halt_until` and never
    the threshold, so setting `money_mgmt.max_consecutive_losses: 0` disables future trips but
    does NOT clear one already armed. The rail's own violation message names this command.

    The loss counter is reset alongside the halt -- leaving it at or above the threshold would
    re-arm the breaker on the very next loss, which is not what an operator clearing a halt
    means. Exits, sells and DCA are never affected by rail 16 and are unaffected here.
    """
    _require_interactive_confirmation(RESUME_ENTRIES_ACTION, RESUME_ENTRIES_DETAIL)
    clear_consecutive_loss_halt(_open_repo(ctx))
    click.echo(RESUME_ENTRIES_CLEARED_LINE)


@cli.command(name="record-flow")
@click.option(
    "--amount",
    required=True,
    help="Signed flow in quote currency: positive for a deposit, negative for a withdrawal.",
)
@click.pass_context
@with_disclaimer
def record_flow(ctx: click.Context, amount: str) -> None:
    """Declare an external deposit or withdrawal so rail 11 does not mistake it for P&L.

    Equity is `cash + positions`, so money moving in or out shifts it -- but neither is a
    trading result. Because the high-water mark never falls, an unrecorded deposit ratchets it
    up and a later withdrawal of the same money then reads as a drawdown that never recovers:
    rail 11 vetoes every entry on an account that lost nothing. Declaring the flow shifts the
    HWM (and the rolling weekly peak) by the same amount, so the drawdown keeps measuring
    trading performance.

    Run it whenever you move money, with the signed amount:

        keel record-flow --amount 500        # deposited 500
        keel record-flow --amount -250       # withdrew 250

    The agent WARNS (`equity.unexplained_jump`) when equity moves sharply between cycles, but it
    will never infer a flow on its own: guessing a withdrawal would lower the HWM and silently
    mask a real trading drawdown, which is the one direction a circuit breaker must not fail in.
    """
    _require_interactive_confirmation(record_flow_action(amount), RECORD_FLOW_DETAIL)
    # `Decimal("nan")`/`Decimal("inf")` parse without raising (same trap `subscription
    # attest` documents). Either would be written straight into the high-water mark, and NaN
    # poisons it permanently: every subsequent `equity > hwm` comparison is False, so the HWM
    # can never re-seed and every drawdown comparison silently misbehaves -- the validation
    # (including that finite check) lives in `parse_flow_amount`, its one home.
    try:
        parsed = parse_flow_amount(amount)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from None

    hwm = record_declared_flow(_open_repo(ctx), parsed)
    for line in render_flow_recorded(parsed, hwm):
        click.echo(line)


@cli.command(name="reset-hwm")
@click.pass_context
@with_disclaimer
def reset_hwm(ctx: click.Context) -> None:
    """Reset rail 11's equity high-water mark, clearing a stuck drawdown halt.

    The HWM is MONOTONIC by design -- it never falls -- so any equity reading that was wrong or
    is no longer comparable is permanent. The common cause is not a loss at all: depositing
    ratchets the HWM up, and a later withdrawal then reads as a drawdown that never recovers.
    Without this, the only remedy is editing sqlite by hand.

    Clearing the key (rather than writing a number) lets the next cycle re-seed it from observed
    equity, which is the same path a fresh install takes. `drawdown_total_pct` is zeroed so the
    rail is not left vetoing on a stale scalar in the window before that next cycle runs.
    """
    _require_interactive_confirmation(RESET_HWM_ACTION, RESET_HWM_DETAIL)
    reset_high_water_mark(_open_repo(ctx))
    click.echo(RESET_HWM_DONE_LINE)


if __name__ == "__main__":  # pragma: no cover
    cli()
