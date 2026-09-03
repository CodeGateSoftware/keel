"""Parity pins for the service extraction (issue #387 C1): the CLI command path and a direct
service call must produce IDENTICAL results for every extracted operation.

PRD O2's whole point is "one implementation, two front-ends" -- the TUI will dispatch to
`keel/commands/*` services, not to the CLI. That only holds if the service layer is not a
near-copy the CLI happens not to use. Each test here drives the SAME fixtures through BOTH
paths and asserts byte-equality of what a human would see (the CLI's stdout, minus the
`with_disclaimer` footer the CLI appends and a service caller would render itself) and of the
state each path leaves behind. The identity pins at the top are the cheapest, strongest form:
the object the CLI resolves IS the object the service module exports.

These tests are NEW (nothing pre-existing moved): the pre-existing CLI tests stay the
behavior pin; these are the architecture pin.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from keel_broker_api.results import Balance

import keel.cli as cli_module
from keel.cli import cli
from keel.commands import assets as assets_service
from keel.commands import confirm as confirm_service
from keel.commands import fetch as fetch_service
from keel.commands import monitor as monitor_service
from keel.commands import pnl as pnl_service
from keel.commands import purification as purification_service
from keel.commands import simulate as simulate_service
from keel.commands import trading as trading_service
from keel.commands._common import DISCLAIMER
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

NOW_TS = 1_799_971_200  # midnight UTC, day-aligned (same anchor the fetch tests freeze at)
DAY = 86_400


class _FrozenClock:
    """`keel.cli`'s `time` stand-in, so the CLI run computes the same `now_ts` the direct call
    is handed explicitly (mirrors `tests/data/test_fetch_cli.py`'s clock)."""

    def __init__(self, now_ts: int) -> None:
        self._now_ts = now_ts

    def time(self) -> float:
        return float(self._now_ts)

    def sleep(self, seconds: float) -> None:
        return None


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def _state_snapshot(repo: Repository) -> dict[str, Any]:
    return {
        key: repo.get_state(key)
        for key in (
            "kill_switch",
            "streak_halt_until",
            "consecutive_losses",
            "equity_high_water_mark",
            "drawdown_total_pct",
            "drawdown_weekly_pct",
            "equity_history",
        )
    }


def _seed_days(repo: Repository, product: str, bars: int) -> None:
    repo.upsert_candles(
        product,
        Granularity.ONE_DAY,
        [
            Candle(
                ts=NOW_TS - i * DAY,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1000000"),
            )
            for i in range(bars, 0, -1)
        ],
    )


def _disclaimerless(output: str) -> str:
    """The CLI's stdout minus the two things a front-end renders itself: the `with_disclaimer`
    footer (`""` + DISCLAIMER, printed in the decorator's `finally`), and -- for failing
    commands -- the trailing `Error: <message>` line click's exception handling appends AFTER
    that footer (the message itself is compared separately, via `FetchResult.error` etc.)."""
    footer = "\n" + DISCLAIMER + "\n"
    assert footer in output, "expected the disclaimer footer exactly once"
    body, _, tail = output.rpartition(footer)
    if tail.startswith("Error: ") and tail.endswith("\n"):
        tail = ""
    assert tail == "", f"unexpected content after the disclaimer footer: {tail!r}"
    return body


def _normalized(text: str, *paths: Path) -> str:
    """Blank out per-run filesystem paths (the two runs rightly use different db/report files;
    everything else must match byte for byte)."""
    for path in sorted(paths, key=lambda p: len(str(p)), reverse=True):
        text = text.replace(str(path), "<PATH>")
    return text


# -- the CLI's names ARE the services (one implementation, two front-ends) -----------------------


def test_the_clis_pinned_names_are_the_service_objects() -> None:
    """The re-imports are aliases, not copies: same object identity for every audited operation
    the tests (and, later, the TUI) reach through `keel.cli`."""
    assert cli_module._screen_product is assets_service.screen_product
    assert cli_module._VENUE is assets_service.VENUE
    assert cli_module._interactive_confirm is confirm_service._interactive_confirm
    assert cli_module._assess_products is fetch_service.assess_products
    assert cli_module._SIM_SLIPPAGE_PCT is simulate_service.SIM_SLIPPAGE_PCT


# -- fetch ----------------------------------------------------------------------------------------


class _ExplodingBroker:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"no broker method may be called under --check ({name})")


_GRAN_STEPS = [
    (Granularity.FIFTEEN_MINUTE, 900),
    (Granularity.ONE_HOUR, 3600),
    (Granularity.ONE_DAY, 86_400),
]


def _seed_current_series(repo: Repository, products: tuple[str, ...]) -> None:
    """Every configured granularity present and current -- the all-green `--check` fixture."""
    for product in products:
        for granularity, step in _GRAN_STEPS:
            repo.upsert_candles(
                product,
                granularity,
                [
                    Candle(
                        ts=NOW_TS - i * step,
                        open=Decimal("100"),
                        high=Decimal("101"),
                        low=Decimal("99"),
                        close=Decimal("100"),
                        volume=Decimal("1000000"),
                    )
                    for i in range(30, 0, -1)
                ],
            )


@pytest.mark.parametrize("check_args", [[], ["--fail-on-gaps"]])
def test_fetch_check_failing_parity(tmp_path, valid_config_path, monkeypatch, check_args):
    """`fetch --check` over a cold cache: same lines, same verdict, same non-zero exit."""
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    _repo_at(db_cli)
    _repo_at(db_svc)

    monkeypatch.setattr(cli_module, "time", _FrozenClock(NOW_TS))
    monkeypatch.setattr(cli_module, "_build_broker", lambda config, **_kw: _ExplodingBroker())

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_cli), "--config", str(valid_config_path), "fetch", "--check", *check_args],
    )
    assert result.exit_code != 0

    from keel.config import load_config

    cfg = load_config(str(valid_config_path))
    lines: list[str] = []
    outcome = fetch_service.run_fetch(
        _repo_at(db_svc),
        cfg,
        lambda: _ExplodingBroker(),  # would raise if the check path ever built+used a broker
        db_path=str(db_svc),
        products=["BTC-USD", "ETH-USD", "PAXG-USD"],
        years=5,
        now_ts=NOW_TS,
        tolerance_bars=12,
        check=True,
        fail_on_gaps="--fail-on-gaps" in check_args,
        echo=lines.append,
    )
    assert outcome.error is not None
    assert outcome.error in result.output
    assert _normalized(_disclaimerless(result.output), db_cli, db_svc) == _normalized(
        "".join(line + "\n" for line in lines), db_cli, db_svc
    )


def test_fetch_check_current_parity(tmp_path, valid_config_path, monkeypatch):
    """`fetch --check` over a warm cache: same lines, exit zero, `error is None`."""
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    _seed_current_series(_repo_at(db_cli), ("BTC-USD", "ETH-USD", "PAXG-USD"))
    _seed_current_series(_repo_at(db_svc), ("BTC-USD", "ETH-USD", "PAXG-USD"))

    monkeypatch.setattr(cli_module, "time", _FrozenClock(NOW_TS))
    monkeypatch.setattr(cli_module, "_build_broker", lambda config, **_kw: _ExplodingBroker())

    result = CliRunner().invoke(
        cli, ["--db", str(db_cli), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "all series current" in result.output

    from keel.config import load_config

    lines: list[str] = []
    outcome = fetch_service.run_fetch(
        _repo_at(db_svc),
        load_config(str(valid_config_path)),
        lambda: (_ for _ in ()).throw(AssertionError("check must not build a broker")),
        db_path=str(db_svc),
        products=["BTC-USD", "ETH-USD", "PAXG-USD"],
        years=5,
        now_ts=NOW_TS,
        tolerance_bars=12,
        check=True,
        echo=lines.append,
    )
    assert outcome.error is None
    assert _normalized(_disclaimerless(result.output), db_cli, db_svc) == _normalized(
        "".join(line + "\n" for line in lines), db_cli, db_svc
    )


# -- monitor --------------------------------------------------------------------------------------


class _FakePollBroker:
    def get_candles(self, product_id: str, granularity: Any, start: int, end: int) -> list:
        return []

    def capabilities(self) -> Any:
        raise AttributeError("no session surface: a 24/7 venue")


def test_monitor_single_poll_parity(tmp_path, valid_config_path, monkeypatch):
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    _repo_at(db_cli)
    _repo_at(db_svc)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(NOW_TS))
    monkeypatch.setattr(cli_module, "_build_broker", lambda config, **_kw: _FakePollBroker())

    result = CliRunner().invoke(
        cli, ["--db", str(db_cli), "--config", str(valid_config_path), "monitor"]
    )
    assert result.exit_code == 0, result.output

    from keel.config import load_config

    cfg = load_config(str(valid_config_path))
    lines: list[str] = []
    cycles = monitor_service.run_monitor(
        _FakePollBroker(),
        _repo_at(db_svc),
        cfg,
        ["BTC-USD", "ETH-USD", "PAXG-USD"],
        list(cfg.market_data.granularities),
        cfg.auto_trade.interval_sec,
        loop=False,
        echo=lines.append,
        now_fn=lambda: NOW_TS,
    )
    assert len(cycles) == 1
    assert _normalized(_disclaimerless(result.output), db_cli, db_svc) == _normalized(
        "".join(line + "\n" for line in lines), db_cli, db_svc
    )


# -- simulate -------------------------------------------------------------------------------------


def _seed_sim_candles(repo: Repository, now_ts: int) -> None:
    hour = now_ts - (now_ts % 3600)
    for product in ("BTC-USD", "ETH-USD", "PAXG-USD"):
        hourly = [
            Candle(
                ts=hour - i * 3600,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
            for i in range(72, -1, -1)
        ]
        daily = [
            Candle(
                ts=hour - i * 86400,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
            for i in range(5, -1, -1)
        ]
        repo.upsert_candles(product, Granularity.ONE_HOUR, hourly)
        repo.upsert_candles(product, Granularity.ONE_DAY, daily)


def test_simulate_report_parity(tmp_path, valid_config_path, monkeypatch):
    """The whole assembly: the CLI's report file and stdout equal the service's, given the same
    frozen clock, the same seeded DB, and `--no-fetch` (no broker on either path)."""
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    out_cli = tmp_path / "cli-report.md"
    out_svc = tmp_path / "svc-report.md"
    _seed_sim_candles(_repo_at(db_cli), NOW_TS)
    _seed_sim_candles(_repo_at(db_svc), NOW_TS)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(NOW_TS))
    monkeypatch.setattr(
        cli_module,
        "_build_broker",
        lambda config, **_kw: (_ for _ in ()).throw(AssertionError("no network under --no-fetch")),
    )

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_cli),
            "--config",
            str(valid_config_path),
            "simulate",
            "--no-fetch",
            "--years",
            "1",
            "--out",
            str(out_cli),
            "--no-trial-record",
        ],
    )
    assert result.exit_code == 0, result.output

    from keel.commands._products import _default_sim_products
    from keel.config import load_config

    cfg = load_config(str(valid_config_path))
    lines: list[str] = []
    outcome = simulate_service.run_simulation(
        _repo_at(db_svc),
        cfg,
        None,  # --no-fetch: no broker is ever constructed
        db_path=str(db_svc),
        products=_default_sim_products(cfg),
        years=1,
        monthly_contribution=Decimal("500"),
        now_ts=NOW_TS,
        out_path=out_svc,
        no_trial_record=True,
        echo=lines.append,
    )
    assert out_cli.read_text() == out_svc.read_text()
    assert outcome.report_markdown == out_cli.read_text()
    assert _normalized(
        _disclaimerless(result.output), db_cli, db_svc, out_cli, out_svc
    ) == _normalized("".join(line + "\n" for line in lines), db_cli, db_svc, out_cli, out_svc)


# -- assets: the one admission gate, and the two rendered reports ----------------------------------


def _attest_btc(repo: Repository) -> None:
    """BOTH halves of the admission key: the asset's shariah classification AND the listing's
    contract (issue #202 -- the wrapper criterion fails closed on an unattested instrument)."""
    repo.upsert_asset_attestation(
        asset="BTC",
        sector="store of value",
        backing="native",
        pays_yield=False,
        source="https://example.invalid/btc",
        attested_by="test",
        attested_at=NOW_TS,
    )
    repo.upsert_instrument_attestation(
        venue="coinbase",
        product_id="BTC-USD",
        wrapper="spot",
        source="https://example.invalid/btc-usd",
        attested_by="test",
        attested_at=NOW_TS,
    )


def _seed_deep_daily_history(repo: Repository, product: str, bars: int = 1500) -> None:
    """Enough history+liquidity for the DATA criteria, so only attestation can REJECT (the same
    fixture shape `tests/commands/test_tui.py`'s single-gate tests seed)."""
    repo.upsert_candles(
        product,
        Granularity.ONE_DAY,
        [
            Candle(
                ts=NOW_TS - i * DAY,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("100000"),
            )
            for i in range(bars, 0, -1)
        ],
    )


def test_assets_screen_verdicts_match_the_service(tmp_path, valid_config_path):
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    repo_cli = _repo_at(db_cli)
    repo_svc = _repo_at(db_svc)
    _attest_btc(repo_cli)
    _attest_btc(repo_svc)
    _seed_deep_daily_history(repo_cli, "BTC-USD")
    _seed_deep_daily_history(repo_svc, "BTC-USD")
    # ETH stays unattested: the pair (ADMIT, REJECT) is what makes this a verdict comparison
    # rather than a smoke test.

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_cli),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD,ETH-USD",
        ],
    )
    assert result.exit_code == 0, result.output

    from keel.config import load_config

    screened = assets_service.screen_products(
        _repo_at(db_svc), load_config(str(valid_config_path)), ["BTC-USD", "ETH-USD"]
    )
    verdicts = {s.asset: s.result.summary for s in screened}
    for entry, expected in verdicts.items():
        assert expected in result.output, f"{entry}'s service verdict missing from CLI output"
    assert verdicts["BTC"] == "ADMIT"
    assert verdicts["ETH"] == "REJECT"
    admitted = sum(1 for s in screened if s.admitted)
    assert f"{admitted}/{len(screened)} admitted" in _disclaimerless(result.output)


class _AccountsBroker(_FakePollBroker):
    def get_balances(self) -> list[Balance]:
        return [
            Balance(currency="BTC", available=Decimal("1.5"), total=Decimal("1.5")),
            Balance(currency="USDC", available=Decimal("900"), total=Decimal("900")),
            Balance(currency="SOL", available=Decimal("10"), total=Decimal("10")),
        ]


def test_assets_holdings_render_parity(tmp_path, valid_config_path, monkeypatch):
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    repo_cli = _repo_at(db_cli)
    repo_svc = _repo_at(db_svc)
    _attest_btc(repo_cli)
    _attest_btc(repo_svc)
    monkeypatch.setattr(cli_module, "_build_broker", lambda config, **_kw: _AccountsBroker())

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_cli), "--config", str(valid_config_path), "assets", "holdings"],
    )
    assert result.exit_code == 0, result.output

    from keel.config import load_config

    cfg = load_config(str(valid_config_path))
    report = assets_service.gather_holdings(
        _repo_at(db_svc), cfg, _AccountsBroker().get_balances(), Decimal("0")
    )
    rendered = "".join(line + "\n" for line in assets_service.render_holdings(report))
    assert _disclaimerless(result.output) == rendered
    # USDC is a cash equivalent: never listed as a candidate.
    assert "USDC" not in _disclaimerless(result.output)


class _DiscoverBroker(_FakePollBroker):
    def list_products(self) -> list[dict[str, Any]]:
        return [
            {
                "product_id": "SOL-USD",
                "base_name": "Solana",
                "quote_currency": "USD",
                "quote_volume_24h": Decimal("5_000_000"),
            },
            {  # below the floor: excluded, and named in the exclusion summary
                "product_id": "DUST-USD",
                "base_name": "Dust",
                "quote_currency": "USD",
                "quote_volume_24h": Decimal("10"),
            },
        ]


def test_assets_discover_render_parity(tmp_path, valid_config_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config, **_kw: _DiscoverBroker())

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(tmp_path / "cli.db"),
            "--config",
            str(valid_config_path),
            "assets",
            "discover",
            "--min-volume-24h",
            "100000",
        ],
    )
    assert result.exit_code == 0, result.output

    from keel.config import load_config

    cfg = load_config(str(valid_config_path))
    client = _DiscoverBroker()
    sweep = assets_service.run_discovery(
        client,
        client.list_products(),
        cfg,
        min_volume_24h=Decimal("100000"),
        now_ts=NOW_TS,
    )
    rendered = "".join(line + "\n" for line in assets_service.render_discover(sweep))
    assert _disclaimerless(result.output) == rendered


# -- pnl / purification ---------------------------------------------------------------------------


def _buy_tx(tx_id: str, asset: str, qty: str, price: str, tx_type: str = "Buy") -> dict[str, Any]:
    return dict(
        coinbase_id=tx_id,
        source="csv_import",
        type=tx_type,
        asset=asset,
        ts=1_700_000_000,
        qty=Decimal(qty),
        price=Decimal(price),
        subtotal=None,
        total=None,
        fees=Decimal("0"),
        notes=None,
        rule_id=None,
        order_id=None,
    )


@pytest.mark.parametrize(
    ("extra_args", "asset", "marks"),
    [
        (["--asset", "BTC"], "BTC", {}),
        (["--asset", "BTC", "--mark", "BTC=150"], "BTC", {"BTC": Decimal("150")}),
        ([], None, {}),
    ],
)
def test_pnl_render_parity(tmp_path, extra_args, asset, marks):
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    _repo_at(db_cli).upsert_transaction(_buy_tx("tx-1", "BTC", "1", "100"))
    _repo_at(db_svc).upsert_transaction(_buy_tx("tx-1", "BTC", "1", "100"))

    result = CliRunner().invoke(cli, ["--db", str(db_cli), "pnl", *extra_args])
    assert result.exit_code == 0, result.output

    report = pnl_service.build_pnl_report(_repo_at(db_svc).get_transactions(asset), asset, marks)
    rendered = "".join(line + "\n" for line in pnl_service.render_pnl_report(report))
    assert _disclaimerless(result.output) == rendered


def test_purification_render_parity(tmp_path):
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    # A reward credit (non-compliant income, §65.9) and an unrecognized type (needs review) --
    # both branches of the report.
    _repo_at(db_cli).upsert_transaction(_buy_tx("tx-1", "ETH", "2", "100", "Reward Income"))
    _repo_at(db_cli).upsert_transaction(_buy_tx("tx-2", "SOL", "3", "10", "Something Odd"))
    _repo_at(db_svc).upsert_transaction(_buy_tx("tx-1", "ETH", "2", "100", "Reward Income"))
    _repo_at(db_svc).upsert_transaction(_buy_tx("tx-2", "SOL", "3", "10", "Something Odd"))

    result = CliRunner().invoke(cli, ["--db", str(db_cli), "purification"])
    assert result.exit_code == 0, result.output

    from keel.compliance import purification as purification_mod

    report = purification_mod.build_report(_repo_at(db_svc).get_transactions())
    rendered = "".join(
        line + "\n" for line in purification_service.render_purification_report(report)
    )
    assert _disclaimerless(result.output) == rendered


# -- the trading-state mutations ------------------------------------------------------------------


def _run_confirmed(db_path: Path, args: list[str], monkeypatch) -> Any:
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)
    return CliRunner().invoke(cli, ["--db", str(db_path), *args], input="yes\n")


@pytest.mark.parametrize(
    ("cli_args", "service_call"),
    [
        (["kill"], lambda repo: trading_service.engage_kill_switch(repo)),
        (["resume"], lambda repo: trading_service.disengage_kill_switch(repo)),
        (["resume-entries"], lambda repo: trading_service.clear_consecutive_loss_halt(repo)),
        (["reset-hwm"], lambda repo: trading_service.reset_high_water_mark(repo)),
    ],
)
def test_trading_state_mutations_parity(tmp_path, monkeypatch, cli_args, service_call):
    """CLI command vs direct service call leave the SAME agent_state behind (the arm/no-arm of
    the typed prompt is front-end business; the state change is the shared operation)."""
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    repo_cli = _repo_at(db_cli)
    repo_svc = _repo_at(db_svc)
    for repo in (repo_cli, repo_svc):  # a prior HWM/armed halt, so the mutations have day-1 work
        repo.set_state("equity_high_water_mark", Decimal("1000"))
        repo.set_state("streak_halt_until", NOW_TS + DAY)
        repo.set_state("consecutive_losses", 3)

    result = _run_confirmed(db_cli, cli_args, monkeypatch)
    assert result.exit_code == 0, result.output

    service_call(repo_svc)
    assert _state_snapshot(repo_cli) == _state_snapshot(repo_svc)


def test_record_flow_state_parity(tmp_path, monkeypatch):
    db_cli = tmp_path / "cli.db"
    db_svc = tmp_path / "svc.db"
    _repo_at(db_cli).set_state("equity_high_water_mark", Decimal("1000"))
    _repo_at(db_svc).set_state("equity_high_water_mark", Decimal("1000"))

    result = _run_confirmed(db_cli, ["record-flow", "--amount", "500"], monkeypatch)
    assert result.exit_code == 0, result.output
    cli_hwm_line = [ln for ln in result.output.splitlines() if "High-water mark" in ln]

    hwm = trading_service.record_flow(_repo_at(db_svc), Decimal("500"))
    assert hwm is not None
    assert f"High-water mark rebased to {hwm}." in cli_hwm_line[0]
    assert _state_snapshot(_repo_at(db_cli)) == _state_snapshot(_repo_at(db_svc))


def test_agent_cycle_lines_come_from_the_shared_renderer() -> None:
    """`keel agent`'s per-cycle output is `trading.render_loop_result` -- the TUI's Trading menu
    will show the same lines because they come from the same function, pinned here at the data
    level (a skip, a normal cycle, and the paper-equity line)."""
    from keel import agent

    skipped = agent.LoopResult(
        ts=NOW_TS, skipped=True, skip_reason="market_closed", mode="paper", polled=0
    )
    assert trading_service.render_loop_result(skipped) == [f"[{NOW_TS}] skipped: market_closed"]
