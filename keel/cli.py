"""keel command-line interface (P3 Task 9).

Wires the merged Phase 1-3 modules into a `click` CLI: `db import` (`data.csv_import.import_dir`),
`monitor` (`data.market_feed`), `agent` (`agent.run_once`/`agent.loop`), `autonomy on|off|show`
(the `profile` row `agent.run_once` itself re-reads each cycle),
`rules list|backtest|promote|demote|disable|seed` (`data.repository` + `strategy.backtest`/
`promotion`; `seed` populates the otherwise-empty `rules` table from `agent.RULE_REGISTRY`,
Issue #81), `pnl` (`analysis.pnl`), `kill`/`resume` (the `agent_state` kill-switch),
`resume-entries` (clear an armed rail-16 consecutive-loss halt), `record-flow`
(declare a deposit/withdrawal so rail 11 does not read it as P&L) and `reset-hwm`
(reset rail 11's high-water mark),
`subscription attest|set|show` (the per-venue, user-attested allowance rail 14 reads live), and a
Phase-4 `insights` stub.

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

**No interactive hangs in tests.** `_is_interactive()` is the single TTY predicate, with
deliberately no env-var or flag override -- any such seam would be settable from cron and would
defeat every fail-closed built on it. Tests patch the predicate.

**Disclaimer.** The money-touching and dangerous commands print the halal + not-financial/
religious-advice disclaimer footer via the `with_disclaimer` decorator on their callback --
always, even when the command errors out or is refused at a confirmation prompt. (Pure-reporting
commands such as `trials *`, `withdrawals show` and `assets list` deliberately omit it.)

**No live network in tests.** `_build_broker` is the one seam that would construct a real
`CoinbaseClient` (from `.env` secrets via `coinbase.rest.RESTClient`); tests monkeypatch it
to inject a fake broker instead, exactly like `tests/test_agent.py`'s `FakeBroker`.

**Module layout.** This file is the composition root: it defines the root `cli` group, the
broker-touching commands (`fetch`, `agent`, `monitor`, `simulate`, `assets`) that share the
`_build_broker` seam, and the remaining top-level commands. The broker-free command groups live
in `keel/commands/*` and are registered here via `cli.add_command(...)`: `db`, `trials`,
`withdrawals`, `autonomy`, `rules`, `subscription`. The shared seams (`with_disclaimer`, the
confirmation gate, `_open_repo`/`_load_cfg`/`_build_broker`) live in `keel.commands._common` and
are re-imported here; `_is_interactive` is reached as `_common._is_interactive()` so a single
patch point in `keel.commands._common` drives every gate wherever its command is defined.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import click
from keel_core.products import quote_currency_of

from keel import agent
from keel.analysis import pnl as pnl_analysis
from keel.commands import _common
from keel.commands._common import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    _build_broker,
    _load_cfg,
    _open_repo,
    _require_interactive_confirmation,
    with_disclaimer,
)
from keel.commands._products import _default_sim_products, _history_product
from keel.commands.autonomy import autonomy_group
from keel.commands.db import db_group
from keel.commands.rules import rules_group, rules_seed
from keel.commands.subscription import subscription_group
from keel.commands.trials import trials_group
from keel.commands.withdrawals import withdrawals_group
from keel.compliance import purification as purification_mod
from keel.compliance import screen as screen_mod
from keel.config import Config
from keel.data import freshness as freshness_mod
from keel.data import history as history_mod
from keel.data import market_feed
from keel.data import repair as repair_mod
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import equity as equity_mod
from keel.research import ledger as trials_ledger
from keel.sim import artifact as artifact_mod
from keel.sim import benchmark as benchmark_mod
from keel.sim import metrics as metrics_mod
from keel.sim import portfolio_sim
from keel.sim import report as report_mod
from keel.sim import tiers as tiers_mod
from keel.strategy import promotion as promotion_mod
from keel.types import Candle, Granularity
from keel.version import build_info

# -- root group ---------------------------------------------------------------------------------


def _print_version(ctx: click.Context, param: object, value: bool) -> None:
    """Eager `--version`: print the build identity and exit before any command runs.

    Prints the working-tree state too. For a tool that can place orders, "0.1.0 (abc123, DIRTY)"
    and "0.1.0 (abc123)" are materially different claims -- the first corresponds to no commit
    and cannot be reproduced.
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


def _assess_products(
    repo: Repository, products: list[str], now_ts: int, start_ts: int, tolerance_bars: int
) -> list[tuple[freshness_mod.Freshness, int]]:
    """Read-only sweep over every (product, granularity). No network.

    Returns `(freshness, unexplained_gaps)` -- the second being missing bars NOT yet proven
    absent at the venue. `--fail-on-gaps` judges that number, not the raw gap count, because a
    hole the venue genuinely does not have can never be closed and would keep the alert red
    forever.
    """
    out: list[tuple[freshness_mod.Freshness, int]] = []
    for product in products:
        for granularity in _SIM_GRANULARITIES:
            info = history_mod.coverage(repo, product, granularity, start_ts)
            unexplained = repair_mod.unexplained_gap_count(repo, product, granularity)
            out.append((freshness_mod.assess(info, now_ts, tolerance_bars), unexplained))
    return out


def _print_freshness(rows: list[tuple[freshness_mod.Freshness, int]]) -> None:
    for row, unexplained in rows:
        # A series can be BOTH stale and gapped. The state label reports the most urgent
        # condition, but the detail always carries BOTH numbers -- an earlier version showed
        # only the label and silently hid gaps behind staleness.
        if row.missing:
            state = "MISSING"
        elif row.stale:
            state = "STALE"
        elif row.gaps:
            state = "GAPS"
        else:
            state = "ok"
        if row.missing:
            detail = "nothing cached"
        else:
            proven = row.gaps - unexplained
            suffix = f" ({proven} proven absent at venue)" if proven else ""
            detail = f"{row.bars_behind} bars behind, {row.gaps} internal gaps{suffix}"
        click.echo(
            f"  {state:<8} {row.product:<12} {row.granularity.value:<9} "
            f"n={row.n_candles:<7} {detail}"
        )


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
    """
    now_ts = int(time.time())
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)

    product_list = _parse_products_option(products, config)
    start_ts = now_ts - years * _DAYS_PER_YEAR * 86400

    before = _assess_products(repo, product_list, now_ts, start_ts, tolerance_bars)
    click.echo(f"data cached in: {ctx.obj['db_path']}")
    _print_freshness(before)

    if check:
        actionable = [r for r, _ in before if r.needs_fetch]
        unexplained = [r for r, gaps in before if gaps > 0]
        if actionable:
            raise click.ClickException(f"{len(actionable)} series missing or stale")
        if unexplained and fail_on_gaps:
            raise click.ClickException(
                f"{len(unexplained)} series have unexplained gaps -- run `keel fetch "
                "--repair-gaps`"
            )
        if unexplained:
            click.echo(
                f"\nall series current. {len(unexplained)} have UNEXPLAINED gaps -- run "
                "`keel fetch --repair-gaps` to probe them."
            )
        else:
            click.echo("\nall series current")
        return

    if not refresh and not repair_gaps and not freshness_mod.any_needs_fetch(
        [r for r, _ in before]
    ):
        click.echo("\nall series current -- nothing to fetch")
        return

    click.echo("\nfetching...")
    client = _build_broker(config)
    history_mod.ensure_history(
        client,
        repo,
        product_list,
        _SIM_GRANULARITIES,
        years,
        now_ts,
        sleep_fn=time.sleep,
        refresh=refresh,
    )

    if repair_gaps:
        click.echo("\nrepairing interior gaps...")
        for product in product_list:
            for granularity in _SIM_GRANULARITIES:
                outcome = repair_mod.repair_series(
                    client,
                    repo,
                    product,
                    granularity,
                    now_ts=now_ts,
                    reprobe_known_absent=reprobe_absent,
                    sleep_fn=time.sleep,
                )
                if not outcome.windows_found:
                    continue
                click.echo(
                    f"  {product:<12} {granularity.value:<9} "
                    f"windows={outcome.windows_found} probed={outcome.windows_probed} "
                    f"skipped={outcome.windows_skipped_known_absent} "
                    f"recovered={outcome.bars_recovered} "
                    f"absent_at_source={outcome.windows_absent_at_source}"
                )
                for error in outcome.errors:
                    click.echo(f"    error: {error}", err=True)

    after = _assess_products(repo, product_list, now_ts, start_ts, tolerance_bars)
    click.echo("\nafter fetch:")
    _print_freshness(after)
    if freshness_mod.any_needs_fetch([r for r, _ in after]):
        # Not an error: an asset younger than the window, or a venue simply not serving the
        # most recent bar yet, both land here legitimately. Say so rather than failing a
        # scheduled run that did everything it could.
        click.echo(
            "\nsome series are still short. Common and usually benign: an asset younger than "
            "the requested window (PAXG-USD), or a bar the venue has not published yet."
        )


# -- assets (allowlist admission screening) -------------------------------------------------


@cli.group("assets")
def assets_group() -> None:
    """Allowlist admission screening (KB §28.4/§65.5) -- a CURATION gate, not a per-trade rail."""


def _market_facts(repo: Repository, product: str, quote: str) -> screen_mod.MarketFacts:
    """Everything the screen can compute for itself from data we already hold."""
    asset = product.split("-")[0]
    candles = repo.get_candles(product, Granularity.ONE_DAY)
    volumes = sorted(c.volume * c.close for c in candles)
    median = volumes[len(volumes) // 2] if volumes else Decimal(0)
    return screen_mod.MarketFacts(
        asset=asset,
        daily_bars=len(candles),
        median_daily_volume=median,
        # A REAL check: does this product settle in the currency this deployment trades in?
        # The former `or bool(candles)` fallback made it vacuous -- every screened product is
        # `-USD`, so it always fell through to "do we have bars", which the history criterion
        # already covers. One of four admission criteria was doing nothing.
        quotable_in_settlement_currency=quote_currency_of(product) == quote.upper(),
    )


def _screen_product(
    repo: Repository, product: str, quote: str
) -> tuple[screen_mod.MarketFacts, screen_mod.ScreenResult]:
    """THE admission decision, for every candidate source.

    `assets screen`, `assets holdings --screen` and any future proposer (an LLM shortlist, say)
    all route through here, so none of them can drift onto a laxer path -- which is what makes
    "the same vetting process" a property of the code rather than an intention. Returns the facts
    alongside the verdict so a caller can explain WHY without recomputing them.
    """
    asset = product.split("-")[0]
    facts = _market_facts(repo, product, quote)
    raw = repo.get_asset_attestation(asset)
    attestation = (
        screen_mod.AssetAttestation(
            asset=raw["asset"],
            sector=raw["sector"],
            backing=raw["backing"],
            pays_yield=bool(raw["pays_yield"]),
            source=raw["source"],
            attested_by=raw["attested_by"],
            attested_at=raw["attested_at"],
        )
        if raw is not None
        else None
    )
    return facts, screen_mod.screen_asset(facts, attestation)


# Failure classes that are DOWNSTREAM of having no cached history: with zero bars `liquidity`
# reports on our data (median volume is 0 *because* there are no bars), not on the asset, so
# `assets holdings` must not print it as a verdict. `settlement` is deliberately NOT here -- it
# compares the product's quote leg to the settlement currency and never touches candles, so it
# stays a real, assessable verdict even with zero bars.
_DATA_DERIVED_FAILURES = frozenset({"liquidity"})

# Never candidates: you cannot trade the currency you settle in, and fiat is funding rather than
# a position. Coinbase quotes many fiats, so the list is deliberately broad -- a missing one is
# only cosmetic (an extra row), never an admission.
_FIAT_CURRENCIES = frozenset(
    {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "SGD", "BRL", "MXN", "TRY", "INR", "KRW"}
)

# Stablecoins are cash EQUIVALENTS -- funding you hold between positions, not positions. They are
# excluded for the same reason fiat is: proposing the money as something to buy with the money is
# noise. (They would be REJECTED anyway -- unattested, and a yield-bearing one fails §28.4 -- so
# this only removes a redundant row, never an admission.)
_CASH_EQUIVALENTS = frozenset({"USDC", "USDT", "DAI", "PYUSD", "TUSD", "USDP", "GUSD"})


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
    quote = config.quote_currency
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
        # Includes broker CONSTRUCTION, so a missing/!invalid `.env` credential surfaces here
        # rather than as a raw traceback. Reporting an empty list instead would read as
        # "you hold nothing", which is not what we learned.
        raise click.ClickException(
            f"could not read balances from the broker: {exc}\n"
            "  If this is an authentication error, check CDP_API_KEY/CDP_API_SECRET in .env."
        ) from exc

    excluded = _FIAT_CURRENCIES | _CASH_EQUIVALENTS | {quote.upper()}
    # Currency codes are compared UPPERCASED: a `usdc` balance is still the settlement currency,
    # and must not be presented as something tradable on a casing accident.
    holdings = sorted(
        (
            a
            for a in accounts
            if (a.get("currency") or "").upper() not in excluded
            and a["available_balance"] > floor
        ),
        key=lambda a: (a.get("currency") or "").upper(),
    )

    if not holdings:
        click.echo(f"no holdings above {floor} (excluding {quote} and fiat).")
        return

    allowlist = {asset.upper() for asset in config.allowlist}
    click.echo(f"{len(holdings)} holding(s) above {floor}, excluding {quote} and fiat:\n")

    for account in holdings:
        # Uppercase here too, not just for the exclusion set: screening the raw code would look
        # up `btc` (UNATTESTED) while the allowlist check matched `BTC`, and would hand the
        # operator `keel fetch --products btc-USD`, a product id that never resolves.
        asset = (account.get("currency") or "").upper()
        on_allowlist = "on-allowlist" if asset.upper() in allowlist else "not-on-allowlist"
        attested = "attested" if repo.get_asset_attestation(asset) else "UNATTESTED"
        click.echo(f"  {asset:<8} balance={account['available_balance']:<18} "
                   f"{on_allowlist:<16} {attested}")

        if not run_screen:
            continue

        product = _history_product(asset, quote)
        facts, result = _screen_product(repo, product, quote)
        click.echo(f"      {result.summary}  ({facts.daily_bars} daily bars cached)")

        failures = result.failures
        if facts.daily_bars == 0:
            # The likeliest misreading of this whole feature. With no cached bars `liquidity`
            # cannot say anything about the asset -- median volume is 0 *because* there are no
            # bars -- so printing it as a finding would assert about the asset exactly what this
            # message exists to deny. It is shown as derived, not as a verdict.
            # NOTE: `settlement` is deliberately NOT suppressed. It compares the product's quote
            # leg to the settlement currency and never reads candles, so it stays assessable at
            # zero bars. Do not add it to `_DATA_DERIVED_FAILURES` -- no test would catch that
            # here (a derived product can never fail settlement), and it would hide a real
            # verdict on any externally supplied product.
            derived = [f for f in failures if f.split(":")[0] in _DATA_DERIVED_FAILURES]
            failures = [f for f in failures if f not in derived]
            click.echo(
                f"      ! no local history -- run `keel fetch --products {product}` first, then "
                "re-screen.\n"
                "        This is a MISSING-DATA verdict, not a verdict about the asset."
            )
            for failure in derived:
                click.echo(f"      · ({failure.split(':')[0]}: not assessable without history)")
        for failure in failures:
            click.echo(f"      ✗ {failure}")
        # Warnings carry compliance constraints that apply even to an ADMITted asset (§65.5's
        # bay' al-sarf regime for gold/silver backing, say). Dropping them would make this
        # command quietly less informative than `assets screen` for the same asset.
        for warning in result.warnings:
            click.echo(f"      ! {warning}")

    click.echo(
        "\n⚠️  Holdings are CANDIDATES, not admissions. Nothing here has been admitted to "
        "trading:\nthat needs `keel assets attest` with a source, a passing screen, and a "
        "deliberate edit to\n`allowlist` in config.yaml."
    )


@assets_group.command("discover")
@click.option("--quote", default=None, help="Settlement currency (default: config.quote_currency).")
@click.option(
    "--min-volume-24h", default="5000000", show_default=True,
    help="Cheap pre-filter on the venue's reported 24h quote volume.",
)
@click.option("--limit", default=25, show_default=True, help="Show at most this many candidates.")
@click.option(
    "--probe-history",
    is_flag=True,
    default=False,
    help="One extra request per candidate: does daily history exist at the 4-year mark? A "
    "candidate that fails this can never clear the screen, so probing first avoids spending "
    "attestation effort on it.",
)
@click.pass_context
@with_disclaimer
def assets_discover(
    ctx: click.Context, quote: str | None, min_volume_24h: str, limit: int, probe_history: bool
) -> None:
    """PROPOSE allowlist candidates from venue metadata. Admits nothing.

    A cheap pre-filter whose only job is to cut ~900 products to a shortlist worth pulling five
    years of candles for. Sector and backing are NOT considered here and cannot be -- every
    candidate below is still REJECTED by `keel assets screen` until a human attests it.
    """
    config = _load_cfg(ctx)
    client = _build_broker(config)
    products = client.list_products()

    policy = screen_mod.DiscoveryPolicy(
        quote_currency=quote or config.quote_currency,
        min_quote_24h_volume=Decimal(min_volume_24h),
    )
    candidates = screen_mod.discover_candidates(
        products, policy, exclude_assets=frozenset(config.allowlist)
    )

    click.echo(
        f"{len(products)} venue products -> {len(candidates)} candidates "
        f"(quote={policy.quote_currency}, 24h volume >= {policy.min_quote_24h_volume:,.0f}, "
        f"excluding the current allowlist)\n"
    )
    header = f"{'#':>3}  {'product':<14} {'asset':<8} {'24h quote volume':>18}"
    click.echo(header + ("  4yr?  name" if probe_history else "  name"))

    now_ts = int(time.time())
    four_years_ago = now_ts - 4 * _DAYS_PER_YEAR * 86400
    for index, candidate in enumerate(candidates[:limit], start=1):
        line = (
            f"{index:>3}  {candidate.product_id:<14} {candidate.asset:<8} "
            f"{candidate.quote_24h_volume:>18,.0f}"
        )
        if probe_history:
            try:
                probed = client.get_candles(
                    candidate.product_id,
                    Granularity.ONE_DAY,
                    four_years_ago,
                    four_years_ago + 30 * 86400,
                )
                marker = "yes " if probed else "NO  "
            except Exception:  # noqa: BLE001 -- a probe failure is unknown, not a verdict
                marker = "?   "
            line += f"  {marker}"
        click.echo(line + f"  {candidate.base_name}")

    click.echo(
        "\n⚠️  These are PROPOSALS, not admissions. Nothing above has been screened for sector "
        "or backing -- those cannot be derived from market data. Each one needs "
        "`keel assets attest` with a source before `keel assets screen` can admit it."
    )


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
    product_list = _parse_products_option(products, config)

    admitted = 0
    for product in product_list:
        asset = product.split("-")[0]
        facts, result = _screen_product(repo, product, config.quote_currency)
        admitted += int(result.admitted)

        click.echo(
            f"\n{result.summary:<7} {asset:<8} bars={facts.daily_bars} "
            f"median_daily_volume={facts.median_daily_volume:.0f}"
        )
        for failure in result.failures:
            click.echo(f"    ✗ {failure}")
        for warning in result.warnings:
            click.echo(f"    ! {warning}")

    click.echo(f"\n{admitted}/{len(product_list)} admitted")


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


@assets_group.command("list")
@click.pass_context
def assets_list(ctx: click.Context) -> None:
    """List recorded attestations."""
    repo = _open_repo(ctx)
    rows = repo.get_asset_attestations()
    if not rows:
        click.echo("no attestations recorded")
        return
    for row in rows:
        click.echo(
            f"{row['asset']:<8} sector={row['sector']:<16} backing={row['backing']:<8} "
            f"pays_yield={bool(row['pays_yield'])!s:<5} by={row['attested_by']}"
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

    if not report.entries and not report.needs_review:
        click.echo("no non-compliant credits found")
        return

    if report.entries:
        click.echo(f"non-compliant credits: {len(report.entries)}")
        click.echo(f"\n{'asset':<8} {'units received':>20} {'owed (USD)':>14}")
        qty_by_asset = report.qty_by_asset
        for asset, owed in report.owed_by_asset.items():
            click.echo(f"{asset:<8} {qty_by_asset.get(asset, Decimal(0)):>20} {owed:>14.2f}")
        click.echo(f"\nTOTAL OWED TO CHARITY: ${report.total_owed_usd:.2f}")
        click.echo(
            "\nThis is excluded from realised P&L and from the equity base sizing computes "
            "from -- otherwise riba would compound into position size (§65.9). Zakat, if "
            "estimated, is on purified wealth, so this runs first."
        )

    if report.needs_review:
        click.echo(f"\n⚠️  {len(report.needs_review)} credit(s) of UNRECOGNISED type need review:")
        for entry in report.needs_review[:20]:
            click.echo(f"    {entry.tx_type!r} {entry.asset} qty={entry.qty} ${entry.amount_usd}")
        click.echo(
            "    Classified neither way on purpose: calling them clean would let riba into "
            "P&L, calling them non-compliant would state an obligation as fact."
        )


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
    """Poll fresh candles for every allowlisted product (read-only)."""
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    broker = _build_broker(config)
    products = _default_sim_products(config)
    granularities = list(config.market_data.granularities)
    interval = interval_sec if interval_sec is not None else config.auto_trade.interval_sec

    cycles = 0
    while True:
        now_ts = int(time.time())
        written = market_feed.poll_once(broker, repo, products, granularities, now_ts=now_ts)
        click.echo(f"[{now_ts}] polled {written} new candle row(s) across {products}")
        cycles += 1
        if not loop or (max_cycles is not None and cycles >= max_cycles):
            break
        time.sleep(interval)


# -- agent ------------------------------------------------------------------------------


def _interactive_confirm(preview: dict) -> bool:
    """Human-in-the-loop order confirmation for `mode="confirm"`.

    Called by the executor ONLY after the intent has already passed every hard rail -- this is
    an additional human gate, never a replacement for the rails. Renders the broker's preview
    and asks for an explicit yes.

    Fails closed: a non-TTY invocation (a script, a cron job, a headless run) declines rather
    than blocking on stdin, so `mode="confirm"` never trades unattended.
    """
    click.echo("\nRails PASSED. Coinbase order preview:")
    if isinstance(preview, dict) and preview:
        for key, value in preview.items():
            click.echo(f"    {key}: {value}")
    else:
        click.echo(f"    {preview!r}")
    if not _common._is_interactive():
        click.echo("no TTY -- declining (confirm mode fails closed).", err=True)
        return False
    return click.confirm("Place this order?", default=False)


def _print_loop_result(result: agent.LoopResult) -> None:
    if result.skipped:
        click.echo(f"[{result.ts}] skipped: {result.skip_reason}")
        return
    entered = sum(1 for r in result.enter_results if r.placed)
    exited = sum(1 for r in result.exit_results if r.placed)
    click.echo(
        f"[{result.ts}] mode={result.mode} polled={result.polled} "
        f"products={result.products} stale={result.stale_products} "
        f"signals={len(result.enter_signals)} entered={entered} exited={exited}"
    )


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
        _print_loop_result(
            agent.run_once(broker, repo, config, now_ts=int(time.time()), confirm_fn=confirm_fn)
        )
        return

    interval = interval_sec if interval_sec is not None else config.auto_trade.interval_sec

    def stop_flag(_count: list[int] = [0]) -> bool:  # noqa: B006 - intentional mutable counter
        if max_cycles is not None and _count[0] >= max_cycles:
            return True
        _count[0] += 1
        return False

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
    transactions = repo.get_transactions(asset)
    marks = _parse_marks(raw_marks)

    if asset is not None:
        realized = pnl_analysis.realized_pnl(transactions, asset)
        pos = pnl_analysis.position(transactions, asset)
        click.echo(f"{asset}: realized={realized} open_qty={pos.qty} avg_cost={pos.avg_cost}")
        if asset in marks:
            unrealized = (marks[asset] - pos.avg_cost) * pos.qty
            click.echo(f"{asset}: mark={marks[asset]} unrealized={unrealized}")
        return

    total_realized = pnl_analysis.realized_pnl(transactions)
    click.echo(f"total realized P&L: {total_realized}")
    unrealized_by_asset = pnl_analysis.unrealized_pnl(transactions, marks) if marks else {}
    for a in sorted({tx["asset"] for tx in transactions}):
        pos = pnl_analysis.position(transactions, a)
        if not pos.qty:
            continue
        line = f"  {a}: open_qty={pos.qty} avg_cost={pos.avg_cost}"
        if a in unrealized_by_asset:
            line += f" unrealized={unrealized_by_asset[a]}"
        click.echo(line)


# -- simulate (Sim Task 8) -------------------------------------------------------------------

# Match `strategy/backtest.backtest`'s / `sim/portfolio_sim.run`'s own defaults so the edge
# table, the account pass, and the benchmarks all price fills identically.
_SIM_FEE_PCT = Decimal("0.006")
_SIM_SLIPPAGE_PCT = Decimal("0.0005")
_SIM_GRANULARITIES = [Granularity.ONE_HOUR, Granularity.ONE_DAY]
_DAYS_PER_YEAR = 365


def _parse_products_option(products: str | None, config: Config) -> list[str]:
    if not products:
        return _default_sim_products(config)
    return [p.strip() for p in products.split(",") if p.strip()]


def _sim_asset(product_id: str) -> str:
    return product_id.split("-")[0]


def _load_sim_candles(
    repo: Repository, products: list[str], start_ts: int, end_ts: int
) -> tuple[dict[str, dict[Granularity, list[Candle]]], dict[str, list[tuple[int, Decimal]]]]:
    """Load cached candles for `products` into `(candles_by_asset, prices_by_asset)`.

    `candles_by_asset`/`prices_by_asset` are keyed by bare asset code (`"BTC"`, not
    `"BTC-USD"`) to match `sim.portfolio_sim`/`sim.benchmark`'s convention (rules bind to an
    asset via `Rule.product_id`, `Config.target_weights` is asset-keyed).
    """
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]] = {}
    prices_by_asset: dict[str, list[tuple[int, Decimal]]] = {}
    for product in products:
        asset = _sim_asset(product)
        per_tf = {
            gran: repo.get_candles(product, gran, start_ts, end_ts) for gran in _SIM_GRANULARITIES
        }
        candles_by_asset[asset] = per_tf
        prices_by_asset[asset] = [(c.ts, c.close) for c in per_tf[Granularity.ONE_DAY]]
    return candles_by_asset, prices_by_asset


def _sim_coverage(
    repo: Repository, products: list[str], start_ts: int
) -> dict[tuple[str, Granularity], history_mod.CoverageInfo]:
    """Per-asset cached-candle coverage (no network -- reads whatever's already in the DB)."""
    coverage: dict[tuple[str, Granularity], history_mod.CoverageInfo] = {}
    for product in products:
        asset = _sim_asset(product)
        for gran in _SIM_GRANULARITIES:
            coverage[(asset, gran)] = history_mod.coverage(repo, product, gran, start_ts)
    return coverage


def _print_sim_coverage(
    db_path: str, coverage: dict[tuple[str, Granularity], history_mod.CoverageInfo]
) -> None:
    click.echo(f"data cached in: {db_path}")
    for (asset, granularity), info in sorted(
        coverage.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
    ):
        click.echo(
            f"  coverage {asset} {granularity.value}: n_candles={info.n_candles} "
            f"first_ts={info.first_ts} last_ts={info.last_ts} gaps={info.gaps}"
        )


def _build_account_metrics(
    sim: portfolio_sim.SimResult, start_ts: int, end_ts: int
) -> dict[str, Any]:
    """Reduce `sim.equity_curve`/`sim.contributions`/`sim.trades` into the account-metrics
    dict `report.build_verdict`/`report.render_markdown` consume (see their docstrings for the
    keys each reads)."""
    equity_curve = sim.equity_curve
    contributed = sum((amount for _, amount in sim.contributions), Decimal("0"))
    ending_value = equity_curve[-1][1] if equity_curve else Decimal("0")
    total_return_pct = (
        (ending_value - contributed) / contributed if contributed > 0 else Decimal("0")
    )
    max_dd = metrics_mod.max_drawdown_pct(equity_curve)
    returns = metrics_mod.daily_returns(equity_curve)
    # Money-weighted IRR/CAGR treat contributions as outflows (negative) and the ending
    # portfolio value as the single inflow -- see `sim.metrics.irr`/`cagr_money_weighted`.
    cashflows = [(ts, -amount) for ts, amount in sim.contributions]

    closed_trades = [t for t in sim.trades if t.outcome != "open"]
    per_asset_pnl: dict[str, Decimal] = {}
    for trade in closed_trades:
        per_asset_pnl[trade.asset] = per_asset_pnl.get(trade.asset, Decimal("0")) + (
            trade.pnl or Decimal("0")
        )
    avg_hold_hours = (
        sum(
            (Decimal(t.exit_ts - t.entry_ts) / Decimal(3600) for t in closed_trades),
            Decimal("0"),
        )
        / len(closed_trades)
        if closed_trades
        else Decimal("0")
    )

    return {
        "contributed": contributed,
        "ending_value": ending_value,
        "net_pnl_usd": ending_value - contributed,
        "total_return_pct": total_return_pct,
        "irr": metrics_mod.irr(cashflows, ending_value),
        "cagr": metrics_mod.cagr_money_weighted(cashflows, ending_value, start_ts, end_ts),
        "max_drawdown_pct": max_dd,
        "return_per_drawdown": metrics_mod.return_per_drawdown(total_return_pct, max_dd),
        "sharpe": metrics_mod.sharpe(returns),
        "sortino": metrics_mod.sortino(returns),
        "trade_count": len(closed_trades),
        "avg_hold_hours": avg_hold_hours,
        "per_asset_pnl": per_asset_pnl,
    }


def _build_tier_results(
    config: Config,
    rules: list[Any],
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]],
    sim_natural: portfolio_sim.SimResult,
    natural_metrics: dict[str, Any],
    start_ts: int,
    now_ts: int,
    monthly_contribution: Decimal,
    skip_within_cap: bool,
) -> list[tiers_mod.TierFeeResult]:
    """Assemble the Coinbase One tier/fee analysis matrix (Issue #86) -- one `OVER_CAP` row per
    `config.tiers` entry (always, from `sim_natural`, the already-computed natural/unthrottled
    run) plus one `WITHIN_CAP` row per tier: an unlimited tier (`free_volume_usd is None`, e.g.
    Premium) reuses `sim_natural` (nothing to throttle); a finite-free-volume tier gets its own
    separate throttled `portfolio_sim.run(..., monthly_volume_cap=tier.free_volume_usd)` pass
    UNLESS `skip_within_cap`, in which case that tier's within-cap row is simply omitted (only
    the cheap over-cap overlay, reusing `sim_natural`, is computed).
    """
    natural_n_months = len(sim_natural.contributions)
    natural_gross_pnl = natural_metrics.get("net_pnl_usd", Decimal("0"))

    results: list[tiers_mod.TierFeeResult] = []
    for tier in config.tiers:
        results.append(
            tiers_mod.compute_tier_fee_result(
                monthly_volume=sim_natural.monthly_volume,
                n_months=natural_n_months,
                tier=tier,
                mode=tiers_mod.OVER_CAP,
                taker_pct=config.fees.taker_pct,
                gross_pnl_usd=natural_gross_pnl,
            )
        )

        if tier.free_volume_usd is None:
            # Unlimited allowance -- nothing to throttle; within-cap == over-cap (Premium).
            results.append(
                tiers_mod.compute_tier_fee_result(
                    monthly_volume=sim_natural.monthly_volume,
                    n_months=natural_n_months,
                    tier=tier,
                    mode=tiers_mod.WITHIN_CAP,
                    taker_pct=config.fees.taker_pct,
                    gross_pnl_usd=natural_gross_pnl,
                )
            )
            continue

        if skip_within_cap:
            continue

        within_sim = portfolio_sim.run(
            rules,
            candles_by_asset,
            config,
            start_ts=start_ts,
            end_ts=now_ts,
            monthly_contribution=monthly_contribution,
            fee_pct=_SIM_FEE_PCT,
            slippage_pct=_SIM_SLIPPAGE_PCT,
            monthly_volume_cap=tier.free_volume_usd,
        )
        within_metrics = _build_account_metrics(within_sim, start_ts, now_ts)
        results.append(
            tiers_mod.compute_tier_fee_result(
                monthly_volume=within_sim.monthly_volume,
                n_months=len(within_sim.contributions),
                tier=tier,
                mode=tiers_mod.WITHIN_CAP,
                taker_pct=config.fees.taker_pct,
                gross_pnl_usd=within_metrics.get("net_pnl_usd", Decimal("0")),
            )
        )

    return results


def _default_report_path(now_ts: int) -> Path:
    date_str = datetime.fromtimestamp(now_ts, tz=UTC).strftime("%Y-%m-%d")
    return Path("docs/superpowers/reports") / f"{date_str}-engine-validation.md"


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
    now_ts = int(time.time())
    config = _load_cfg(ctx)
    repo = _open_repo(ctx)

    product_list = _parse_products_option(products, config)
    months = years * 12
    monthly_contribution = Decimal(contribution)
    start_ts = now_ts - years * _DAYS_PER_YEAR * 86400

    if not no_fetch:
        client = _build_broker(config)
        history_mod.ensure_history(
            client,
            repo,
            product_list,
            _SIM_GRANULARITIES,
            years,
            now_ts,
            sleep_fn=time.sleep,
            refresh=refresh,
        )

    coverage = _sim_coverage(repo, product_list, start_ts)
    _print_sim_coverage(ctx.obj["db_path"], coverage)

    candles_by_asset, prices_by_asset = _load_sim_candles(repo, product_list, start_ts, now_ts)
    rules = [agent._build_rule(row) for row in repo.get_rules()]

    edge = report_mod.edge_table(rules, candles_by_asset, _SIM_FEE_PCT, _SIM_SLIPPAGE_PCT)

    sim = portfolio_sim.run(
        rules,
        candles_by_asset,
        config,
        start_ts=start_ts,
        end_ts=now_ts,
        monthly_contribution=monthly_contribution,
        fee_pct=_SIM_FEE_PCT,
        slippage_pct=_SIM_SLIPPAGE_PCT,
    )
    sim.coverage = coverage

    account_metrics = _build_account_metrics(sim, start_ts, now_ts)

    benchmark = benchmark_mod.dca_into_allowlist(
        prices_by_asset,
        config.target_weights,
        monthly_contribution,
        months,
        _SIM_FEE_PCT,
        _SIM_SLIPPAGE_PCT,
    )
    # Secondary benchmark (spec: DCA-into-BTC); not fed into the verdict gate, but computed so
    # a future report revision can surface it without another sim pass.
    benchmark_mod.dca_into_btc(
        prices_by_asset, monthly_contribution, months, _SIM_FEE_PCT, _SIM_SLIPPAGE_PCT
    )

    promo_cfg = promotion_mod.PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )
    # G2 is checked per rule class (KB §25.5): each class's pooled edge sample is judged
    # against its own floor, so a low-win/high-R:R trend-follower isn't rejected by the global
    # 55%-win floor `promo_cfg` carries. Classes without a fixed floor fall back to `promo_cfg`.
    pooled_by_class = report_mod.group_trades_by_class(edge, rules)
    floors = {
        cls: promotion_mod.floor_for_class(cls, promo_cfg) for cls in pooled_by_class
    }
    verdict = report_mod.build_verdict(
        edge[report_mod.POOLED_KEY],
        account_metrics,
        benchmark,
        sim.coverage,
        promo_cfg,
        pooled_by_class=pooled_by_class,
        floors=floors,
    )
    gaps = report_mod.analyze_gaps(
        sim.telemetry, sim.coverage, move_threshold_pct=portfolio_sim.MOVE_THRESHOLD_PCT
    )

    tier_results = _build_tier_results(
        config,
        rules,
        candles_by_asset,
        sim,
        account_metrics,
        start_ts,
        now_ts,
        monthly_contribution,
        skip_within_cap,
    )

    if not no_trial_record:
        # One ledger row per simulate run: the run IS one configuration of the whole rule set,
        # and its account equity curve is that configuration's per-bar P&L column. Deposits are
        # stripped by `bar_pnl` -- new capital is not profit (spec §4.5).
        series = metrics_mod.bar_pnl(sim.equity_curve, sim.contributions)
        trials_ledger.append_trial(
            trials_ledger.DEFAULT_LEDGER_PATH,
            trial_id=f"simulate-{now_ts}",
            session="keel simulate",
            rule=",".join(sorted({rule.name for rule in rules})) or "none",
            params={
                "products": product_list,
                "years": years,
                "monthly_contribution": str(monthly_contribution),
                "rules": [rule.describe() for rule in rules],
            },
            provenance=trial_provenance,
            kind="sweep_node",
            decision=trial_decision,
            per_bar_pnl=series,
            series_missing=not series,
            summary={"trade_count": len(sim.trades)},
        )

    md = report_mod.render_markdown(
        sim,
        edge,
        account_metrics,
        benchmark,
        verdict,
        gaps,
        in_sample=True,
        tier_results=tier_results,
    )

    out = Path(out_path) if out_path is not None else _default_report_path(now_ts)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)

    click.echo(f"verdict: {verdict.status}")
    click.echo(f"report written to {out}")
    if verdict.reasons:
        click.echo("failing gates: " + "; ".join(verdict.reasons))

    if artifact:
        html = artifact_mod.render_html(
            sim, benchmark, verdict, gaps, account_metrics, in_sample=True
        )
        html_path = out.with_suffix(".html")
        html_path.write_text(html)
        click.echo(f"artifact written to {html_path}")


# -- subscription (rail 14, per-venue attested allowance) ----------------------------------------

# The `subscription` group is defined in `keel.commands.subscription`; register it here.
cli.add_command(subscription_group)


# -- kill / resume ------------------------------------------------------------------------------


@cli.command()
@click.pass_context
@with_disclaimer
def kill(ctx: click.Context) -> None:
    """Engage the kill-switch, halting all trading immediately. Always allowed (safe action)."""
    repo = _open_repo(ctx)
    repo.set_state("kill_switch", True)
    click.echo("kill-switch ENGAGED: all trading halted.")


@cli.command()
@click.pass_context
@with_disclaimer
def resume(ctx: click.Context) -> None:
    """Disengage the kill-switch (dangerous: asks for confirmation)."""
    _require_interactive_confirmation(
        "disengage the kill-switch",
        "Trading resumes immediately: the agent may place orders on its next cycle.",
    )
    repo = _open_repo(ctx)
    repo.set_state("kill_switch", False)
    click.echo("kill-switch disengaged: trading resumed.")


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
    _require_interactive_confirmation(
        "clear the consecutive-loss halt (rail 16)",
        "New entries are re-permitted; the loss counter is reset with it.",
    )
    repo = _open_repo(ctx)
    repo.set_state("streak_halt_until", 0)
    repo.set_state("consecutive_losses", 0)
    click.echo("consecutive-loss breaker cleared: new entries permitted.")


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
    _require_interactive_confirmation(
        f"rebase rail 11's high-water mark by {amount}",
        "A wrong amount or sign here silently mis-states drawdown from now on "
        "(positive = deposit, negative = withdrawal).",
    )
    try:
        parsed = Decimal(amount)
    except InvalidOperation:
        raise click.BadParameter(f"--amount must be a number, got {amount!r}") from None
    # `Decimal("nan")`/`Decimal("inf")` parse without raising above (same trap `subscription
    # attest` documents). Either would be written straight into the high-water mark, and NaN
    # poisons it permanently: every subsequent `equity > hwm` comparison is False, so the HWM
    # can never re-seed and every drawdown comparison silently misbehaves.
    if not parsed.is_finite():
        raise click.BadParameter(f"--amount must be a finite number, got {amount!r}")

    repo = _open_repo(ctx)
    equity_mod.record_external_flow(repo, amount=parsed)
    hwm = repo.get_state("equity_high_water_mark")
    if hwm is None:
        click.echo(
            f"flow of {parsed} recorded. No high-water mark yet -- the next cycle will seed it "
            "from observed equity, which already includes this flow."
        )
    else:
        click.echo(f"flow of {parsed} recorded. High-water mark rebased to {hwm}.")


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
    _require_interactive_confirmation(
        "reset rail 11's high-water mark",
        "Any real, unrecovered drawdown stops being visible to the rail.",
    )
    repo = _open_repo(ctx)
    repo.set_state("equity_high_water_mark", None)
    repo.set_state("drawdown_total_pct", Decimal("0"))
    repo.set_state("drawdown_weekly_pct", Decimal("0"))
    repo.set_state("equity_history", [])
    click.echo("equity high-water mark reset: it will re-seed from the next cycle's equity.")


# -- insights (Phase 4 stub) ---------------------------------------------------------------------


@cli.command()
@click.pass_context
@with_disclaimer
def insights(ctx: click.Context) -> None:
    """Portfolio insights (Phase 4 -- not yet implemented)."""
    click.echo("insights: not yet implemented (see Phase 4 of the roadmap).")


if __name__ == "__main__":  # pragma: no cover
    cli()
