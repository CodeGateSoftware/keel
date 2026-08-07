"""Tests for `keel.commands.admission` -- the OFFLINE report layer behind the TUI's `screen`,
`propose` and `discover` overlays.

Mirrors `tests/commands/test_insights.py`/`tests/commands/test_tui.py`'s fixture style: an
in-memory `Repository` via `connect(":memory:")` + `migrate`, a `_config(**overrides)` helper, a
`NOW_TS` constant. `cli_module._screen_product` (THE admission gate) is used as the injected
`screen_fn` throughout, exactly as the module docstring says every verdict must come from it --
using anything else here would test a screen this module does not actually ship with.

Two tests are the correctness headline of this file and are docstringed as such where they live:
`test_render_screen_report_zero_bars_reads_as_missing_data_not_an_asset_verdict` and
`test_render_screen_report_insufficient_bars_reads_as_a_real_history_failure`. Together they pin
that "never fetched" and "genuinely too new" stay distinguishable in the rendered report, which is
the exact bug `keel.compliance.screen.split_failures`/`missing_history_lines` exist to prevent.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import keel.cli as cli_module
from keel.commands.admission import (
    DEFAULT_PROPOSALS_DIR,
    DiscoverReport,
    ProposeView,
    ScreenReport,
    build_discover_report,
    build_propose_view,
    build_screen_report,
    latest_shortlist,
    render_discover_report,
    render_propose_view,
    render_screen_report,
)
from keel.compliance import screen as screen_mod
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

NOW_TS = 1_800_000_000
_DAY = 86400


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    return r


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "SOL"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(
            granularities=[Granularity.ONE_DAY, Granularity.ONE_HOUR], history_days=365
        ),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _seed_history(repo: Repository, product: str, bars: int) -> None:
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


def _write_shortlist(directory: Path, candidates: list[dict], name: str = "shortlist.json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps({"candidates": candidates}))
    return path


_SOL_CANDIDATE = {
    "asset": "sol",
    "rationale": "high liquidity and developer activity",
    "sources": ["https://coinmarketcap.com/currencies/solana/"],
}


# -- latest_shortlist (offline, filesystem only) ------------------------------------------------


def test_latest_shortlist_picks_newest_by_mtime(tmp_path):
    directory = tmp_path / "proposals"
    directory.mkdir()
    older = directory / "a.json"
    newer = directory / "b.json"
    older.write_text("{}")
    newer.write_text("{}")
    now = 1_800_000_000
    os.utime(older, (now, now))
    os.utime(newer, (now + 100, now + 100))

    assert latest_shortlist(directory) == newer


def test_latest_shortlist_ties_broken_by_name(tmp_path):
    directory = tmp_path / "proposals"
    directory.mkdir()
    a = directory / "a.json"
    z = directory / "z.json"
    a.write_text("{}")
    z.write_text("{}")
    same_ts = 1_800_000_000
    os.utime(a, (same_ts, same_ts))
    os.utime(z, (same_ts, same_ts))

    assert latest_shortlist(directory) == z


def test_latest_shortlist_missing_directory_returns_none_and_creates_nothing(tmp_path):
    directory = tmp_path / "does-not-exist"

    assert latest_shortlist(directory) is None
    assert not directory.exists()  # never creates the directory it was asked to read


def test_latest_shortlist_non_directory_returns_none(tmp_path):
    not_a_dir = tmp_path / "a_file"
    not_a_dir.write_text("not a directory")

    assert latest_shortlist(not_a_dir) is None


def test_latest_shortlist_empty_directory_returns_none(tmp_path):
    directory = tmp_path / "proposals"
    directory.mkdir()

    assert latest_shortlist(directory) is None


def test_latest_shortlist_ignores_non_json_files(tmp_path):
    directory = tmp_path / "proposals"
    directory.mkdir()
    (directory / "readme.txt").write_text("not json")

    assert latest_shortlist(directory) is None


def test_default_proposals_dir_matches_configs_default():
    """Kept in sync manually (per the module's own comment) -- pinned here so a drift breaks a
    test instead of silently disagreeing with `Config.proposals_dir`'s default."""
    assert DEFAULT_PROPOSALS_DIR == "~/keel/proposals"
    assert Config.__dataclass_fields__["proposals_dir"].default == DEFAULT_PROPOSALS_DIR


# -- build_screen_report / render_screen_report (offline, DB reads only) ------------------------


def test_build_screen_report_routes_every_allowlist_product_through_injected_screen_fn(
    repo: Repository,
):
    """No candidate can reach a laxer path -- every product the report screens must come from
    the SAME injected gate, called once per allowlist product with the right product id."""
    config = _config(allowlist=["BTC", "SOL"])
    calls: list[tuple[str, str]] = []

    def fake_screen(repo: Repository, product: str, quote: str):
        calls.append((product, quote))
        facts = screen_mod.MarketFacts(
            asset=product.split("-")[0],
            daily_bars=2000,
            median_daily_volume=Decimal("2000000"),
            quotable_in_settlement_currency=True,
            product_id=product,
        )
        result = screen_mod.ScreenResult(asset=facts.asset, admitted=True)
        return facts, result

    report = build_screen_report(repo, config, fake_screen)

    assert calls == [("BTC-USD", "USD"), ("SOL-USD", "USD")]
    assert isinstance(report, ScreenReport)
    assert [s.product for s in report.screened] == ["BTC-USD", "SOL-USD"]
    assert report.admitted_count == 2


def test_build_screen_report_empty_allowlist_produces_no_screened_products(repo: Repository):
    config = _config(allowlist=[])

    report = build_screen_report(repo, config, cli_module._screen_product)

    assert report.screened == []
    assert report.admitted_count == 0


def test_render_screen_report_empty_allowlist_says_so_plainly(repo: Repository):
    config = _config(allowlist=[])
    report = build_screen_report(repo, config, cli_module._screen_product)

    lines = render_screen_report(report)

    assert lines  # never renders nothing
    text = "\n".join(lines).lower()
    assert "empty" in text


def test_render_screen_report_zero_bars_reads_as_missing_data_not_an_asset_verdict(
    repo: Repository,
):
    """THE correctness headline (1/2). With zero cached candles, `history`/`liquidity` measure
    OUR CACHE, not the asset -- see `screen.split_failures`'s docstring. The rendered report must
    say "no local history, run keel fetch" and must NOT print a `✗ history:` line, or a candidate
    never fetched would read as indistinguishable from one genuinely too young.
    """
    config = _config(allowlist=["SOL"])  # no candles seeded for SOL-USD at all

    report = build_screen_report(repo, config, cli_module._screen_product)
    lines = render_screen_report(report)
    text = "\n".join(lines)

    assert "no local history" in text
    assert "keel fetch --products SOL-USD" in text
    assert "✗ history" not in text


def test_render_screen_report_insufficient_bars_reads_as_a_real_history_failure(repo: Repository):
    """THE correctness headline (2/2), the counterpart to the zero-bars test above. An asset with
    SOME cached history that still falls short of the floor really IS too young, and that verdict
    must not be silenced -- `✗ history:` must appear, proving "never fetched" and "genuinely too
    new" are distinguishable in the report."""
    config = _config(allowlist=["SOL"])
    _seed_history(repo, "SOL-USD", bars=500)  # < 4*365 required, but > 0

    report = build_screen_report(repo, config, cli_module._screen_product)
    lines = render_screen_report(report)
    text = "\n".join(lines)

    assert "✗ history" in text
    assert "no local history" not in text


def test_render_screen_report_ends_with_admitted_count(repo: Repository):
    config = _config(allowlist=["SOL"])
    _seed_history(repo, "SOL-USD", bars=500)

    report = build_screen_report(repo, config, cli_module._screen_product)
    lines = render_screen_report(report)

    assert lines[-1] == f"{report.admitted_count}/{len(report.screened)} admitted"


# -- build_propose_view / render_propose_view (offline, fail-soft) ------------------------------


def test_build_propose_view_missing_directory_is_fail_soft(repo: Repository, tmp_path):
    config = _config()
    missing = tmp_path / "no-such-dir"

    view = build_propose_view(repo, config, cli_module._screen_product, directory=missing)

    assert view.status == "no-directory"
    assert view.detail is not None
    assert view.report is None
    assert not missing.exists()  # never creates the directory it looked in


def test_build_propose_view_empty_directory_is_fail_soft(repo: Repository, tmp_path):
    config = _config()
    directory = tmp_path / "proposals"
    directory.mkdir()

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert view.status == "no-shortlist"
    assert view.detail is not None
    assert view.report is None


def test_build_propose_view_unreadable_file_is_fail_soft(repo: Repository, tmp_path):
    """A directory named `*.json` triggers `IsADirectoryError` (an `OSError` subclass) on
    `.read_text()` -- a portable way to force an unreadable-file path without relying on chmod
    semantics that differ across platforms/CI users (e.g. running as root)."""
    config = _config()
    directory = tmp_path / "proposals"
    directory.mkdir()
    (directory / "shortlist.json").mkdir()

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert view.status == "unreadable"
    assert view.detail is not None
    assert view.report is None


def test_build_propose_view_non_utf8_shortlist_is_fail_soft(repo: Repository, tmp_path):
    """A UTF-16LE+BOM shortlist -- valid JSON, and exactly what a scout run on a Windows box
    writes -- makes `source.read_text()` raise `UnicodeDecodeError`, which subclasses
    **ValueError, not OSError** (`issubclass(UnicodeDecodeError, OSError)` is False). The original
    `except OSError` therefore let it escape a function whose docstring promises it never raises,
    which in the TUI meant the propose overlay repainting `propose read failed: 'utf-8' codec
    can't decode byte 0xff...` every poll forever, naming neither the file nor a next step. The
    fail-soft branch must own this exactly like it owns a permissions error."""
    config = _config()
    directory = tmp_path / "proposals"
    directory.mkdir()
    path = directory / "shortlist.json"
    path.write_bytes(json.dumps({"candidates": [_SOL_CANDIDATE]}).encode("utf-16"))

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert view.status == "unreadable"
    assert view.source == path
    assert view.detail is not None
    assert str(path) in view.detail  # the operator is told WHICH file, unlike the raw traceback
    assert view.report is None


def test_build_propose_view_defaults_the_directory_to_config_proposals_dir(
    repo: Repository, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Every other test in this file passes `directory=` explicitly, so the `directory is None`
    branch -- `Path(config.proposals_dir).expanduser()` -- was never executed by the suite at all:
    hardcoding any path there left 2062 tests green. It resolves to `~/keel/proposals` by default,
    INSIDE the live deployment root, so a silent regression means the overlay quietly reads a
    right-looking wrong place. `HOME` is redirected at `tmp_path` so this never touches a real
    deployment."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # `expanduser` on Windows reads this instead
    config = _config(proposals_dir="~/scout/shortlists")
    shortlist = _write_shortlist(tmp_path / "scout" / "shortlists", [_SOL_CANDIDATE])

    view = build_propose_view(repo, config, cli_module._screen_product)

    assert view.status == "ok"
    assert view.source == shortlist


def test_build_propose_view_default_proposals_dir_is_keel_proposals_under_home(
    repo: Repository, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """The other half of the same branch: with `proposals_dir` left at its own default, the
    directory actually looked in is `<home>/keel/proposals` -- `~` EXPANDED. A missing
    `.expanduser()` would look in a literal `./~/keel/proposals` relative to the cwd, find
    nothing, and report `no proposals directory` forever while the operator's real shortlists sat
    untouched. Pinned via the reported path, which the overlay shows verbatim."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config = _config()
    assert config.proposals_dir == DEFAULT_PROPOSALS_DIR  # guards the premise of this test

    view = build_propose_view(repo, config, cli_module._screen_product)

    assert view.status == "no-directory"
    assert view.detail is not None
    assert str(tmp_path / "keel" / "proposals") in view.detail


def test_build_propose_view_invalid_json_is_fail_soft(repo: Repository, tmp_path):
    config = _config()
    directory = tmp_path / "proposals"
    directory.mkdir()
    (directory / "shortlist.json").write_text("{not json")

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert view.status == "malformed"
    assert view.detail is not None
    assert view.report is None


def test_build_propose_view_malformed_top_level_proposal_is_fail_soft(repo: Repository, tmp_path):
    config = _config()
    directory = tmp_path / "proposals"
    directory.mkdir()
    (directory / "shortlist.json").write_text(json.dumps({"candidates": "nope"}))

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert view.status == "malformed"
    assert view.detail is not None
    assert view.report is None


def test_build_propose_view_happy_path(repo: Repository, tmp_path):
    config = _config()
    directory = tmp_path / "proposals"
    shortlist = _write_shortlist(directory, [_SOL_CANDIDATE])

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert view.status == "ok"
    assert view.source == shortlist
    assert view.detail is None
    assert view.report is not None
    assert view.report.screened[0].candidate.asset == "SOL"


def test_build_propose_view_explicit_path_skips_the_newest_file_search(repo: Repository, tmp_path):
    config = _config()
    directory = tmp_path / "proposals"
    directory.mkdir()
    older = _write_shortlist(directory, [_SOL_CANDIDATE], name="older.json")
    os.utime(older, (NOW_TS, NOW_TS))
    newer = _write_shortlist(
        directory, [{"asset": "btc", "rationale": "r", "sources": ["https://x.invalid"]}],
        name="newer.json",
    )
    os.utime(newer, (NOW_TS + 100, NOW_TS + 100))

    view = build_propose_view(
        repo, config, cli_module._screen_product, directory=directory, path=older
    )

    assert view.source == older
    assert view.report.screened[0].candidate.asset == "SOL"


@pytest.mark.parametrize(
    "bad_candidate",
    [
        {"rationale": "r", "sources": ["https://x.invalid"]},  # missing asset
        {"asset": "SOL", "rationale": "r"},  # missing sources
        {"asset": "SOL", "rationale": "r", "sources": ["not-a-url"]},  # non-http source
        {"asset": "sol-usd", "rationale": "r", "sources": ["https://x.invalid"]},  # non-alnum
    ],
)
def test_build_propose_view_never_raises_and_always_returns_a_status(
    repo: Repository, tmp_path, bad_candidate: dict
):
    """The fail-soft contract, exercised across the whole matrix in one parametrized sweep --
    this function must never raise regardless of what is on disk."""
    config = _config()
    directory = tmp_path / "proposals"
    _write_shortlist(directory, [bad_candidate])

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert isinstance(view, ProposeView)
    assert view.status == "ok"  # the top-level structure is still valid; the ENTRY is invalid
    assert len(view.report.invalid) == 1
    assert view.report.screened == []


def test_invalid_shortlist_entries_are_reported_not_dropped(repo: Repository, tmp_path):
    config = _config()
    directory = tmp_path / "proposals"
    _write_shortlist(directory, [{"asset": "SOL", "rationale": "r", "sources": []}])

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)
    lines = render_propose_view(view)
    text = "\n".join(lines)

    assert "INVALID" in text


def test_render_propose_view_never_crashes_on_a_missing_directory(repo: Repository, tmp_path):
    config = _config()
    view = build_propose_view(
        repo, config, cli_module._screen_product, directory=tmp_path / "missing"
    )

    lines = render_propose_view(view)

    assert lines
    assert all(isinstance(line, str) for line in lines)
    joined = "\n".join(lines).lower()
    assert "traceback" not in joined
    assert "exception" not in joined


def test_render_propose_view_never_crashes_on_an_empty_directory(repo: Repository, tmp_path):
    config = _config()
    directory = tmp_path / "empty"
    directory.mkdir()
    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    lines = render_propose_view(view)

    assert lines
    assert all(isinstance(line, str) for line in lines)
    joined = "\n".join(lines).lower()
    assert "traceback" not in joined
    assert "exception" not in joined


def test_render_propose_view_happy_path_reuses_render_proposal_report_verbatim(
    repo: Repository, tmp_path
):
    """Pins the reuse: `render_propose_view` must not grow a parallel rendering path. `"source:
    <url>"` is a distinctive substring ONLY `render_proposal_report` produces (see
    `keel/proposer.py`), so its presence here proves the real renderer ran."""
    config = _config()
    directory = tmp_path / "proposals"
    shortlist = _write_shortlist(directory, [_SOL_CANDIDATE])

    view = build_propose_view(repo, config, cli_module._screen_product, directory=directory)
    lines = render_propose_view(view)
    text = "\n".join(lines)

    assert str(shortlist) in lines[0]
    assert "source: https://coinmarketcap.com/currencies/solana/" in text
    assert "admitted" in text  # the trailing "N/M admitted" summary line


# -- writes nothing (screen + propose are both purely read-only) --------------------------------


def test_build_screen_report_and_propose_view_write_nothing(tmp_path):
    """Mirrors `test_propose_writes_nothing` in `tests/compliance/test_assets_cli.py`: reopen the
    DB from its path (not the handle held from before the calls) so a stray write to ANY
    asset/table would actually be caught."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    config = _config(allowlist=["SOL"])
    directory = tmp_path / "proposals"
    _write_shortlist(directory, [_SOL_CANDIDATE])

    build_screen_report(repo, config, cli_module._screen_product)
    build_propose_view(repo, config, cli_module._screen_product, directory=directory)

    assert _repo_at(db_path).get_asset_attestations() == []


# -- build_discover_report / render_discover_report (pure over already-fetched products) --------


def _venue_product(
    asset: str, volume: str = "10000000", quote: str = "USD", **overrides: Any
) -> dict:
    base = {
        "product_id": f"{asset}-{quote}",
        "quote_currency_id": quote,
        "status": "online",
        "trading_disabled": False,
        "is_disabled": False,
        "view_only": False,
        "base_name": asset.title(),
        "quote_24h_volume": volume,
    }
    base.update(overrides)
    return base


def test_build_discover_report_is_offline_no_broker_constructed(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
):
    """Pure over `products` -- the caller fetches; this module never touches the network. Patch
    `_build_broker` to blow up if anything here ever reaches for it."""

    def _must_not_build_broker(config: Config) -> Any:
        raise AssertionError("build_discover_report must never construct a broker")

    monkeypatch.setattr(cli_module, "_build_broker", _must_not_build_broker)
    config = _config()
    products = [_venue_product("DOGE")]

    report = build_discover_report(products, config)

    assert isinstance(report, DiscoverReport)
    assert report.venue_product_count == 1


def test_build_discover_report_excludes_allowlist_assets(repo: Repository):
    config = _config(allowlist=["BTC"])
    products = [_venue_product("BTC"), _venue_product("DOGE")]

    report = build_discover_report(products, config)

    assets = [c.asset for c in report.candidates]
    assert "BTC" not in assets
    assert "DOGE" in assets


def test_build_discover_report_applies_default_volume_floor_matching_assets_discover(repo):
    """`keel assets discover --min-volume-24h` defaults to `5000000` -- read straight from the
    CLI option's own default (by name, not position, so a decorator reorder cannot silently
    break this pin) so the two can never drift apart silently."""
    cli_option = next(
        p for p in cli_module.assets_discover.params if p.name == "min_volume_24h"
    )
    default_floor = Decimal(cli_option.default)
    assert default_floor == Decimal("5000000")

    config = _config(allowlist=[])
    below = _venue_product("DOGE", volume="4999999")
    above = _venue_product("SHIB", volume="5000001")

    report = build_discover_report([below, above], config)

    assert report.min_quote_24h_volume == default_floor
    assets = [c.asset for c in report.candidates]
    assert "DOGE" not in assets
    assert "SHIB" in assets


def test_build_discover_report_custom_volume_floor(repo: Repository):
    config = _config(allowlist=[])
    products = [_venue_product("DOGE", volume="1000")]

    report = build_discover_report(products, config, min_quote_24h_volume=Decimal("500"))

    assert [c.asset for c in report.candidates] == ["DOGE"]


def test_build_discover_report_limit_respected(repo: Repository):
    config = _config(allowlist=[])
    products = [
        _venue_product(f"COIN{i}", volume=str(10_000_000 + i)) for i in range(10)
    ]

    report = build_discover_report(products, config, limit=3)

    assert len(report.candidates) == 3
    # sorted descending by volume, so the top 3 by volume survive the limit
    assert report.candidates[0].quote_24h_volume >= report.candidates[1].quote_24h_volume
    assert report.candidates[1].quote_24h_volume >= report.candidates[2].quote_24h_volume


def test_render_discover_report_shows_table_and_ends_with_proposals_not_admissions_warning():
    config = _config(allowlist=[])
    products = [_venue_product("DOGE")]
    report = build_discover_report(products, config)

    lines = render_discover_report(report)
    text = "\n".join(lines)

    assert "DOGE-USD" in text
    assert "PROPOSALS, not admissions" in lines[-1]
    assert "keel assets attest" in text


def test_render_discover_report_never_includes_probe_history_marker():
    """Out of scope by design: `--probe-history` is one extra network request per candidate,
    which this offline module must never make."""
    config = _config(allowlist=[])
    products = [_venue_product("DOGE")]
    report = build_discover_report(products, config)

    lines = render_discover_report(report)
    text = "\n".join(lines).lower()

    assert "4yr?" not in text
