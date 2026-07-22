"""`keel assets` -- the allowlist admission gate's CLI surface."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

import keel.cli as cli_module
from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

_DAY = 86400


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def _seed_history(repo: Repository, product: str, bars: int = 2000) -> None:
    repo.upsert_candles(
        product,
        Granularity.ONE_DAY,
        [
            Candle(
                ts=i * _DAY,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("100000"),
            )
            for i in range(bars)
        ],
    )


def _attest(runner, db_path, config_path, asset, **over):
    args = {
        "--asset": asset,
        "--sector": "payments",
        "--backing": "native",
        "--source": "https://example.invalid/ruling",
        "--attested-by": "tester",
    }
    args.update(over)
    flat = [item for pair in args.items() for item in pair]
    return runner.invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), "assets", "attest", *flat]
    )


def test_an_unattested_asset_is_rejected_even_with_perfect_market_data(
    tmp_path, valid_config_path
):
    """The gate's whole point: good candles do not substitute for a classification."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    for asset in ("BTC", "ETH", "PAXG"):
        _seed_history(repo, f"{asset}-USD")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "screen"]
    )
    assert result.exit_code == 0, result.output
    assert "0/3 admitted" in result.output
    assert "attestation: MISSING" in result.output


def test_attesting_admits_an_otherwise_clean_asset(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    for asset in ("BTC", "ETH", "PAXG"):
        _seed_history(repo, f"{asset}-USD")

    runner = CliRunner()
    for asset in ("BTC", "ETH", "PAXG"):
        assert _attest(runner, db_path, valid_config_path, asset).exit_code == 0

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "screen"]
    )
    assert "3/3 admitted" in result.output


def test_a_haram_sector_attestation_still_rejects(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")

    runner = CliRunner()
    _attest(runner, db_path, valid_config_path, "BTC", **{"--sector": "gambling"})

    result = runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "screen", "--products", "BTC-USD"],
    )
    assert "0/1 admitted" in result.output
    assert "haram_sector" in result.output


def test_short_history_rejects_regardless_of_attestation(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)

    runner = CliRunner()
    _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})

    result = runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "screen", "--products", "PAXG-USD"],
    )
    assert "0/1 admitted" in result.output
    assert "history" in result.output


def test_attest_rejects_an_unknown_backing_at_the_cli_boundary(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    result = _attest(
        CliRunner(), db_path, valid_config_path, "BTC", **{"--backing": "probably-fine"}
    )
    assert result.exit_code != 0


def test_attestations_round_trip_through_list(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    runner = CliRunner()
    _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "list"]
    )
    assert "PAXG" in result.output
    assert "ayn" in result.output


# -- discover ------------------------------------------------------------------


class _FakeVenue:
    """Serves canned product metadata and canned history probes. No network."""

    def __init__(self, products, history_for=frozenset()):
        self._products = products
        self._history_for = history_for
        self.probe_calls: list[str] = []

    def list_products(self, product_type="SPOT"):
        return self._products

    def get_candles(self, product_id, granularity, start, end):
        self.probe_calls.append(product_id)
        return [1] if product_id in self._history_for else []


def _venue_product(pid, volume, **over):
    base = {
        "product_id": pid,
        "base_name": pid.split("-")[0],
        "quote_currency_id": "USDC",
        "status": "online",
        "trading_disabled": False,
        "is_disabled": False,
        "view_only": False,
        "quote_24h_volume": volume,
    }
    base.update(over)
    return base


def test_discover_proposes_and_says_so_loudly(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue([_venue_product("SOL-USDC", "50000000")])
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )
    assert result.exit_code == 0, result.output
    assert "SOL" in result.output
    # The proposal/admission boundary must be unmissable in the output.
    assert "PROPOSALS, not admissions" in result.output
    assert "attest" in result.output


def test_discover_excludes_the_current_allowlist(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue(
        [_venue_product("BTC-USDC", "90000000"), _venue_product("SOL-USDC", "50000000")]
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )
    assert "SOL" in result.output
    assert "BTC-USDC" not in result.output


def test_probe_history_marks_candidates_without_a_four_year_series(
    tmp_path, valid_config_path, monkeypatch
):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue(
        [_venue_product("SOL-USDC", "50000000"), _venue_product("NEW-USDC", "40000000")],
        history_for=frozenset({"SOL-USDC"}),
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "discover", "--probe-history"],
    )
    assert result.exit_code == 0, result.output
    assert set(venue.probe_calls) == {"SOL-USDC", "NEW-USDC"}
    sol_line = next(ln for ln in result.output.splitlines() if "SOL-USDC" in ln)
    new_line = next(ln for ln in result.output.splitlines() if "NEW-USDC" in ln)
    assert "yes" in sol_line
    assert "NO" in new_line


def test_a_failed_probe_reads_as_UNKNOWN_not_as_a_rejection(
    tmp_path, valid_config_path, monkeypatch
):
    """A request that did not complete is not evidence of absence."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)

    class _BrokenProbe(_FakeVenue):
        def get_candles(self, *a, **k):
            raise RuntimeError("timeout")

    venue = _BrokenProbe([_venue_product("SOL-USDC", "50000000")])
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "discover", "--probe-history"],
    )
    assert result.exit_code == 0
    sol_line = next(ln for ln in result.output.splitlines() if "SOL-USDC" in ln)
    assert "?" in sol_line
    assert "NO" not in sol_line


# -- assets holdings: the user's own broker balances as a candidate SOURCE ------
#
# A source, not a gate. Holding an asset is not a reason to trade it, so this command admits
# nothing and writes nothing -- it routes the user's balances through the SAME screen.


class _FakeBroker:
    """Duck-types the bits of CoinbaseClient this command uses."""

    def __init__(self, accounts, fail=False):
        self._accounts = accounts
        self._fail = fail
        self.calls = 0

    def get_accounts(self):
        self.calls += 1
        if self._fail:
            raise RuntimeError("venue unreachable")
        return self._accounts


def _account(currency, balance):
    return {
        "uuid": f"u-{currency}",
        "currency": currency,
        "available_balance": Decimal(balance),
        "default": False,
        "active": True,
    }


def _with_broker(monkeypatch, broker):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: broker)


def _holdings(db_path, config_path, *extra):
    return CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "assets", "holdings", *extra],
    )


def test_holdings_lists_what_the_user_holds(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5"), _account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path)

    assert result.exit_code == 0, result.output
    assert "BTC" in result.output and "SOL" in result.output


def test_holdings_excludes_the_settlement_currency_and_fiat(
    tmp_path, valid_config_path, monkeypatch
):
    """You cannot trade the currency you settle in; listing it as a candidate is noise."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(
        monkeypatch,
        _FakeBroker([_account("BTC", "0.5"), _account("USDC", "500"), _account("USD", "100")]),
    )

    result = _holdings(db_path, valid_config_path)

    holding_lines = [ln for ln in result.output.splitlines() if ln.startswith("  ")]
    assets_listed = {ln.split()[0] for ln in holding_lines if ln.split()}
    assert "BTC" in assets_listed
    assert "USDC" not in assets_listed, "cannot trade the currency you settle in"
    assert "USD" not in assets_listed, "fiat is funding, not a position"


def test_holdings_filters_dust_by_min_balance(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5"), _account("XLM", "0.00001")]))

    result = _holdings(db_path, valid_config_path, "--min-balance", "0.001")

    assert "BTC" in result.output
    assert "XLM" not in result.output


def test_a_HELD_but_unattested_asset_is_still_REJECTED(tmp_path, valid_config_path, monkeypatch):
    """The point of the whole feature: owning it changes nothing about admission."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "SOL-USD")  # perfect market data...
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert result.exit_code == 0, result.output
    assert "REJECT" in result.output
    assert "attestation: MISSING" in result.output


def test_holdings_screen_agrees_with_assets_screen_for_the_same_asset(
    tmp_path, valid_config_path, monkeypatch
):
    """One gate, shared by construction -- a proposer must not get a laxer path."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5")]))

    screened = runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "screen", "--products", "BTC-USD"],
    )
    held = _holdings(db_path, valid_config_path, "--screen")

    # Assert the verdict POSITIVELY -- `x in a == x in b` also passes when both are False, or
    # when holdings prints ADMIT unconditionally.
    assert "ADMIT" in screened.output
    assert "ADMIT" in held.output


def test_no_local_history_is_reported_as_MISSING_DATA_not_a_bad_asset(
    tmp_path, valid_config_path, monkeypatch
):
    """The likeliest misreading: a rejection for zero cached bars says nothing about the asset."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # no candles seeded at all
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "no local history" in result.output
    assert "keel fetch" in result.output


def test_holdings_writes_nothing(tmp_path, valid_config_path, monkeypatch):
    """A read-only report: no attestation, no allowlist change, no DB mutation."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "SOL-USD")
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    _holdings(db_path, valid_config_path, "--screen")

    assert repo.get_asset_attestations() == []
    assert _repo_at(db_path).get_asset_attestation("SOL") is None


def test_a_broker_failure_is_an_ERROR_not_an_empty_clean_result(
    tmp_path, valid_config_path, monkeypatch
):
    """An unreachable venue must not read as 'you hold nothing suspicious'."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([], fail=True))

    result = _holdings(db_path, valid_config_path)

    assert result.exit_code != 0
    assert "unreachable" in result.output.lower() or "error" in result.output.lower()


def test_holdings_marks_assets_already_on_the_allowlist(
    tmp_path, valid_config_path, monkeypatch
):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5"), _account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path)

    btc_line = next(ln for ln in result.output.splitlines() if ln.strip().startswith("BTC"))
    sol_line = next(ln for ln in result.output.splitlines() if ln.strip().startswith("SOL"))
    # "not-on-allowlist" CONTAINS "on-allowlist", so the negative must be excluded explicitly --
    # asserting the substring alone passes even if the check is inverted.
    assert "on-allowlist" in btc_line and "not-on-allowlist" not in btc_line
    assert "not-on-allowlist" in sol_line


def test_holdings_screen_does_not_DROP_compliance_warnings(
    tmp_path, valid_config_path, monkeypatch
):
    """`ScreenResult.warnings` carry constraints that bind even on an ADMITted asset (§65.5's
    bay' al-sarf regime for gold/silver backing). Dropping them made this command quietly less
    informative than `assets screen` for the very same asset."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD")
    runner = CliRunner()
    assert _attest(
        runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"}
    ).exit_code == 0
    _with_broker(monkeypatch, _FakeBroker([_account("PAXG", "3")]))

    screened = runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "screen", "--products", "PAXG-USD"],
    )
    held = _holdings(db_path, valid_config_path, "--screen")

    assert "bay' al-sarf" in screened.output, "fixture no longer triggers the warning"
    assert "bay' al-sarf" in held.output, "holdings dropped a compliance warning"


def test_derivative_failures_are_not_asserted_as_verdicts_without_history(
    tmp_path, valid_config_path, monkeypatch
):
    """With zero cached bars, liquidity and settlement report on our DATA, not the asset:
    median volume is 0 because there are no bars. Printing them as findings would assert
    exactly what the missing-data message exists to deny."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "no local history" in result.output
    assert "✗ settlement" not in result.output, "settlement is a naming artifact here"
    assert "✗ liquidity" not in result.output, "median volume is 0 only because bars are 0"
    assert "not assessable without history" in result.output
    assert "✗ history" in result.output  # the REAL, primary finding stays


def test_a_lowercase_settlement_currency_is_still_excluded(
    tmp_path, valid_config_path, monkeypatch
):
    """A casing accident must not present the currency you settle in as tradable."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("btc", "0.5"), _account("usdc", "500")]))

    result = _holdings(db_path, valid_config_path)

    listed = {ln.split()[0].upper() for ln in result.output.splitlines() if ln.startswith("  ")}
    assert "USDC" not in listed
    assert "BTC" in listed


def test_min_balance_rejects_garbage_and_non_finite_values(
    tmp_path, valid_config_path, monkeypatch
):
    """A NaN floor makes every comparison raise; a negative one lists every zero balance."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5")]))

    for bad in ("abc", "nan", "-1", "inf"):
        result = _holdings(db_path, valid_config_path, "--min-balance", bad)
        assert result.exit_code != 0, f"--min-balance {bad} should be rejected: {result.output}"


def test_the_derived_failure_tags_actually_match_screen_asset_output():
    """Pins the string coupling that the zero-bars suppression depends on.

    `assets holdings` suppresses derivative failures by matching `failure.split(":")[0]` against
    `_DATA_DERIVED_FAILURES`. Renaming a tag in `screen_asset` would silently stop the
    suppression -- reintroducing 'data artifacts printed as verdicts about the asset' with a
    fully green suite. This asserts the tags are real.
    """
    from keel.compliance import screen as screen_mod

    facts = screen_mod.MarketFacts(
        asset="SOL",
        daily_bars=0,
        median_daily_volume=Decimal(0),
        quotable_in_settlement_currency=False,
    )
    tags = {f.split(":")[0] for f in screen_mod.screen_asset(facts, None).failures}

    missing = cli_module._DATA_DERIVED_FAILURES - tags
    assert not missing, (
        f"{missing} no longer appear as failure tags in screen_asset -- the holdings "
        "suppression is now silently inert; update _DATA_DERIVED_FAILURES"
    )


def test_a_lowercase_holding_is_screened_as_the_attested_uppercase_asset(
    tmp_path, valid_config_path, monkeypatch
):
    """A `btc` balance must not read as UNATTESTED while `BTC` is attested, nor be handed a
    `btc-USD` fetch hint that will never resolve."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    _with_broker(monkeypatch, _FakeBroker([_account("btc", "0.5")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "UNATTESTED" not in result.output
    assert "btc-USD" not in result.output
    assert "ADMIT" in result.output


def test_an_account_with_no_currency_field_does_not_crash(
    tmp_path, valid_config_path, monkeypatch
):
    """`CoinbaseClient.get_accounts` defaults a missing currency to None."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    broken = {"uuid": "u", "currency": None, "available_balance": Decimal("1"), "active": True}
    _with_broker(monkeypatch, _FakeBroker([broken, _account("BTC", "0.5")]))

    result = _holdings(db_path, valid_config_path)

    assert result.exit_code == 0, result.output
    assert "BTC" in result.output
