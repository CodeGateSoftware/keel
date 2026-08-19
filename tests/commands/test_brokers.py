"""Tests for `keel.commands.brokers` -- the O7 brokers service, its CLI surface and the
TUI Venues browser (issue #394 C7; PRD O7).

One service, two front-ends, pinned here as the PRD's acceptance states it: `keel brokers
list` and the Venues browser show IDENTICAL information from ONE service payload. The
service reads ONLY the adapter registry's capability declarations -- a display of what an
adapter SAYS it can do, never an inference about what keys an operator holds (#233-aligned)
-- and no secret material appears anywhere in it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from click.testing import CliRunner

from keel.cli import cli
from keel.commands import brokers
from keel.commands import console as console_mod
from keel.config import load_config

#: Every field the payload may carry -- the closed capability vocabulary. A field outside
#: this set (an env read, a key-presence probe, a config content) is a scope violation,
#: which is what makes this pin stronger than "assert the good fields are there".
PAYLOAD_FIELDS = {
    "name",
    "venue",
    "deployment",
    "session_bound",
    "quote_currencies",
    "asset_classes",
    "supported_orders",
    "preview",
    "supports_fee_summary",
    "declared_endpoints",
    "supported_data_feeds",
    "package_version",
    "error",
}

_INSTALLED = {"alpaca", "coinbase", "fake", "robinhood"}


def _service_payload() -> list[dict[str, Any]]:
    """The service's rows as the JSON front-ends carry them (tuples become lists), so
    `json.loads(cli_output) == _service_payload()` is an exact equality, not a
    tuple-vs-list false negative."""
    return json.loads(json.dumps([asdict(i) for i in brokers.list_installed_brokers()]))


def _infos() -> dict[str, brokers.BrokerInfo]:
    return {info.name: info for info in brokers.list_installed_brokers()}


# -- the service (O7) ------------------------------------------------------------------------------


def test_every_installed_adapter_is_listed_with_a_stable_order() -> None:
    """The registry IS the list: every `keel.brokers` entry point appears exactly once,
    in name order -- the same walk `venue_session_bound`/`load_broker` make, surfaced."""
    names = [info.name for info in brokers.list_installed_brokers()]
    assert set(names) == _INSTALLED
    assert names == sorted(names)


def test_the_payload_carries_only_the_capability_vocabulary() -> None:
    for info in brokers.list_installed_brokers():
        assert set(asdict(info)) == PAYLOAD_FIELDS, info.name


def test_coinbase_is_the_wired_default_crypto_venue() -> None:
    cb = _infos()["coinbase"]
    assert cb.venue == "coinbase"
    assert cb.deployment == "wired-for-deployment"
    assert cb.session_bound is False  # crypto trades 24/7
    assert cb.quote_currencies == ("USD", "USDC")
    assert cb.asset_classes == ("spot",)
    assert cb.preview == "native"  # Coinbase serves its own preview quotes
    assert cb.supports_fee_summary is True
    assert cb.declared_endpoints == ()  # no endpoint vocabulary is declared
    assert cb.supported_data_feeds == ()


def test_alpaca_is_the_wired_session_bound_equities_venue() -> None:
    al = _infos()["alpaca"]
    assert al.venue == "alpaca"
    assert al.deployment == "wired-for-deployment"
    assert al.session_bound is True  # the regular session binds equities
    assert al.quote_currencies == ("USD",)
    assert al.asset_classes == ("equity",)
    assert al.preview == "synthesized"  # no native preview endpoint
    assert al.supports_fee_summary is False
    assert al.declared_endpoints == ("live", "paper")
    assert al.supported_data_feeds == ("iex", "sip")


def test_fake_and_robinhood_are_optional_dev_venues() -> None:
    fk = _infos()["fake"]
    rh = _infos()["robinhood"]
    assert fk.deployment == "optional-dev-venue"
    assert rh.deployment == "optional-dev-venue"
    assert fk.preview == "none"  # neither native nor synthesized
    assert rh.preview == "synthesized"
    assert fk.session_bound is False and rh.session_bound is False


def test_every_field_derives_from_the_adapters_own_declarations() -> None:
    """The single-source pin: each row is a rendering of `capabilities()` (and, for
    endpoints/feeds, the adapter class's own declared vocabulary) -- never a service-side
    table that could drift from the adapter."""
    from keel_broker_api.registry import discover_brokers

    rows = _infos()
    for name, adapter_cls in discover_brokers().items():
        cap = adapter_cls().capabilities()
        row = rows[name]
        assert row.venue == cap.venue
        assert row.session_bound == cap.session_bound
        assert row.supports_fee_summary == cap.supports_fee_summary
        assert row.quote_currencies == tuple(sorted(cap.quote_currencies))
        assert row.asset_classes == tuple(sorted(cap.asset_classes))
        assert row.supported_orders == tuple(sorted(cap.supported_orders))
        assert row.preview == (
            "native"
            if cap.supports_native_preview
            else "synthesized" if cap.synthesizes_preview else "none"
        )


def test_the_classification_constant_names_exactly_the_wired_venues() -> None:
    """Wired-for-deployment vs optional-dev-venue is ONE explicit constant in the service
    (with its reasoning in a comment there) -- coinbase (every config without a `broker:`
    section) and alpaca (config.paper-equities.yaml), nothing else."""
    assert brokers.WIRED_FOR_DEPLOYMENT == frozenset({"coinbase", "alpaca"})
    for info in brokers.list_installed_brokers():
        expected = "wired-for-deployment" if info.name in brokers.WIRED_FOR_DEPLOYMENT else (
            "optional-dev-venue"
        )
        assert info.deployment == expected, info.name


def test_no_secret_material_anywhere_in_the_payload() -> None:
    """Capability display only (#233-aligned): no key-presence inference, no credential
    names, no config contents. The payload is JSON-dumped and scanned so a field NAME or
    a VALUE that smuggled secret vocabulary in would fail here."""
    blob = json.dumps([asdict(info) for info in brokers.list_installed_brokers()]).lower()
    for needle in (
        "api_key",
        "apikey",
        "secret",
        "password",
        "credential",
        "token",
        "private",
        "passphrase",
    ):
        assert needle not in blob, needle


def test_the_payload_is_jsonable_and_the_versions_are_resolved() -> None:
    rows = [asdict(info) for info in brokers.list_installed_brokers()]
    assert json.dumps(rows)  # no raise, no default= needed
    # The dev workspace installs every adapter; the version travels with the row.
    for info in brokers.list_installed_brokers():
        assert info.package_version is not None, info.name
        assert re.fullmatch(r"\d+\.\d+\.\d+.*", info.package_version), info.package_version


def test_render_human_lines_name_every_adapter_and_both_deployments() -> None:
    lines = brokers.render_brokers_lines(brokers.list_installed_brokers())
    text = "\n".join(lines)
    for name in _INSTALLED:
        assert name in text, name
    assert "wired-for-deployment" in text
    assert "optional-dev-venue" in text
    assert "24/7" in text and "session-bound" in text


# -- one poisoned registry entry must not kill the listing (#406 review) ---------------------------


class _BrokenAdapter:
    """An adapter whose construction explodes -- the review's poisoned registry entry."""

    def __init__(self) -> None:
        raise RuntimeError("adapter metadata unreadable")


def _capabilities() -> SimpleNamespace:
    return SimpleNamespace(
        venue="ok-venue",
        session_bound=False,
        quote_currencies=("USD",),
        asset_classes=("spot",),
        supported_orders=("market",),
        supports_native_preview=True,
        synthesizes_preview=False,
        supports_fee_summary=True,
    )


class _HealthyAdapter:
    def capabilities(self) -> SimpleNamespace:
        return _capabilities()


def _poison_the_registry(monkeypatch: Any) -> None:
    """The registry walk the service makes, with one healthy and one broken entry."""
    monkeypatch.setattr(
        "keel_broker_api.registry.discover_brokers",
        lambda: {"broken": _BrokenAdapter, "healthy": _HealthyAdapter},
    )


def test_a_poisoned_registry_entry_renders_an_error_row_and_keeps_the_rest(
    monkeypatch: Any,
) -> None:
    """[review #406] One raising adapter must not kill `list_installed_brokers` (and with
    it `keel brokers list` AND the console's Venues browser, which ride the same service
    -- the TUI's Profile->Venues entry calls it outside every try in the loop). The
    service stays total: the broken entry becomes an honest error row (its name plus the
    error), and every healthy adapter still renders."""
    _poison_the_registry(monkeypatch)
    rows = brokers.list_installed_brokers()
    by_name = {row.name: row for row in rows}
    assert set(by_name) == {"broken", "healthy"}
    broken = by_name["broken"]
    assert broken.error is not None
    assert "adapter metadata unreadable" in broken.error
    assert by_name["healthy"].error is None
    # the honest block carries name + error, wrapped to the console's budget
    block = brokers.adapter_error_block(broken)
    assert any("broken" in line for line in block)
    assert any("adapter metadata unreadable" in line for line in block)
    assert all(len(line) <= 78 for line in block)
    # and the human rendering shows the error row AND the healthy adapter
    text = "\n".join(brokers.render_brokers_lines(rows))
    assert "broken" in text and "adapter metadata unreadable" in text
    assert "healthy" in text and "ok-venue" in text


def test_a_poisoned_registry_entry_does_not_crash_the_cli_nor_the_venues_browser(
    monkeypatch: Any,
) -> None:
    """Both front-ends render the SAME resilient payload: the CLI exits 0 with the error
    row and the healthy row, and the console's Venues browser (the TUI's rendering of
    the service) renders both within the 80-column clip -- a broken adapter is a row on
    a screen, never a dead console."""
    _poison_the_registry(monkeypatch)
    result = CliRunner().invoke(cli, ["brokers", "list"])
    assert result.exit_code == 0, result.output
    assert "broken" in result.output and "adapter metadata unreadable" in result.output
    assert "healthy" in result.output

    infos = brokers.list_installed_brokers()
    lines = console_mod.build_venues_lines(
        infos,
        selected_venue="healthy",
        profile=None,
        binding_pair=None,
    )
    texts = [line.text for line in lines]
    joined = "\n".join(texts)
    assert "broken" in joined and "adapter metadata unreadable" in joined
    assert "healthy" in joined and "ok-venue" in joined
    assert all(len(text) <= 80 for text in texts)


# -- the wired/optional classification is pinned to the tracked configs ----------------------------


def test_the_wired_set_is_what_the_tracked_configs_actually_select() -> None:
    """[review #406] Drift guard: `WIRED_FOR_DEPLOYMENT` is a hand-maintained constant,
    so it is derived HERE from the tracked config files' own `broker.name` selections --
    loaded the way the profile convention loads them (`load_config`, whose absent
    `broker:` section means coinbase) and unioned. A newly wired adapter with a tracked
    config fails this test until the constant (and its reasoning comment) is updated."""
    root = Path(__file__).resolve().parents[2]
    configs = sorted(root.glob("config*.yaml"))
    found = {path.name for path in configs}
    assert found >= {
        profile.config_path for profile in console_mod.KNOWN_PROFILES
    } | {"config.yaml"}, found
    selected = {load_config(path).broker.name for path in configs}
    assert brokers.WIRED_FOR_DEPLOYMENT == frozenset(selected)


# -- the CLI (O7): `keel brokers list` --------------------------------------------------------


def test_brokers_list_renders_the_service_rows() -> None:
    result = CliRunner().invoke(cli, ["brokers", "list"])
    assert result.exit_code == 0, result.output
    for name in _INSTALLED:
        assert name in result.output, name
    assert "wired-for-deployment" in result.output
    assert "optional-dev-venue" in result.output
    # No config, no db, no network: the command must not require a deployment to exist.
    assert "no such file" not in result.output.lower()


def test_brokers_list_json_is_exactly_the_service_payload() -> None:
    """`--json` follows `status --json`'s convention (forward-compatible machine shape,
    no disclaimer footer) and is BYTE-IDENTICAL to the service's own rows -- the CLI is a
    rendering of the service, never a second source."""
    result = CliRunner().invoke(cli, ["brokers", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _service_payload()


def test_brokers_list_help_carries_the_readme_trademark_line() -> None:
    result = CliRunner().invoke(cli, ["brokers", "--help"])
    assert result.exit_code == 0, result.output
    assert (
        "Alpaca, Coinbase, and Robinhood are trademarks of their respective owners."
        in result.output
    )


# -- the TUI Venues browser (O7): under Profile, over the same service ---------------------------


def _venues_lines(selected: str = "alpaca") -> list[str]:
    infos = brokers.list_installed_brokers()
    lines = console_mod.build_venues_lines(
        infos,
        selected_venue=selected,
        profile=console_mod.active_profile(
            "config.paper-equities.yaml", "keel-equities.db"
        ),
        binding_pair=("config.paper-equities.yaml", "keel-equities.db"),
        endpoint="paper",
        data_feed="iex",
    )
    return [line.text for line in lines]


def test_the_venues_browser_renders_the_service_rows_with_one_selected_mark() -> None:
    texts = _venues_lines()
    joined = "\n".join(texts)
    for info in brokers.list_installed_brokers():
        assert info.name in joined, info.name
    selected_rows = [t for t in texts if "[selected]" in t]
    assert len(selected_rows) == 1 and "alpaca" in selected_rows[0]


def test_the_venues_browser_shows_the_active_deployments_binding() -> None:
    joined = "\n".join(_venues_lines())
    assert "config.paper-equities.yaml + keel-equities.db" in joined
    assert "endpoint paper" in joined
    assert "data feed iex" in joined


def test_the_venues_browser_and_the_cli_render_one_service_payload() -> None:
    """THE O7 acceptance pin: both front-ends over one payload. The browser's rows are
    built from the service's return, and the CLI's `--json` is the same return asdict-ed
    -- asserted in one place so a front-end that starts sourcing its own data fails here."""
    infos = brokers.list_installed_brokers()
    lines = console_mod.build_venues_lines(
        infos,
        selected_venue="coinbase",
        profile=console_mod.active_profile("config.paperforward.yaml", "keel.db"),
        binding_pair=("config.paperforward.yaml", "keel.db"),
        endpoint=None,
        data_feed=None,
    )
    joined = "\n".join(line.text for line in lines)
    for info in infos:
        assert info.name in joined
        # the capability FACTS the CLI json carries appear in the browser's rows
        assert info.deployment in joined
        for quote in info.quote_currencies:
            assert quote in joined
        if info.preview != "none":
            assert info.preview in joined
    result = CliRunner().invoke(cli, ["brokers", "list", "--json"])
    assert json.loads(result.output) == _service_payload()


def test_the_venues_browser_renders_no_secret_material() -> None:
    """The browser's ROWS carry no secret vocabulary -- the one line that may say the
    word "secret" is the posture note itself (`NO_KEY_INFERENCE_LINE`), which is the
    honesty statement, not material."""
    import textwrap

    posture = set(textwrap.wrap(brokers.NO_KEY_INFERENCE_LINE, width=78))
    posture |= set(
        textwrap.wrap(
            brokers.NO_KEY_INFERENCE_LINE, width=78, initial_indent="", subsequent_indent=""
        )
    )
    texts = [t for t in _venues_lines() if t not in posture]
    joined = "\n".join(texts).lower()
    for needle in ("api_key", "apikey", "secret", "password", "credential", "passphrase"):
        assert needle not in joined, needle


def test_the_venues_browser_fits_the_80_column_clip() -> None:
    for text in _venues_lines(selected="coinbase") + _venues_lines(selected="alpaca"):
        assert len(text) <= 80, text


def test_the_profile_menu_lists_the_venues_entry_under_the_deployments() -> None:
    profiles = [p for p in console_mod.KNOWN_PROFILES if p.key != "live"]
    lines = console_mod.build_profile_menu_lines(profiles, cursor=0, binding_pair=None)
    texts = [line.text for line in lines]
    assert any("Venues" in t for t in texts), texts
    # the Venues entry rides BELOW the deployments (the PRD tree: Profile -> Venues)
    venues_at = next(i for i, t in enumerate(texts) if "Venues" in t)
    last_profile_at = max(i for i, t in enumerate(texts) if "config.paperforward.yaml" in t)
    assert venues_at > last_profile_at


def test_the_venues_browser_states_its_no_inference_posture() -> None:
    """Capability display, not key-presence inference: the screen SAYS so, so an operator
    does not read 'wired' as 'my keys are set up'."""
    joined = "\n".join(_venues_lines())
    assert "capabilit" in joined.lower()
    assert "no key" in joined.lower()
