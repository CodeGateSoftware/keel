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
from types import SimpleNamespace
from typing import Any

from click.testing import CliRunner

from keel.cli import cli
from keel.commands import brokers

#: Every field the payload may carry -- the closed capability vocabulary. A field outside
#: this set (an env read, a key-presence probe, a config content) is a scope violation,
#: which is what makes this pin stronger than "assert the good fields are there".
PAYLOAD_FIELDS = {
    "name",
    "venue",
    "deployment",
    "session_bound",
    "cash_only",
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

#: `kraken` joined the set with its stub adapter (#313): port-complete, every data/market
#: method raising `NotImplementedError`, so it registers but can serve nothing.
_INSTALLED = {"alpaca", "coinbase", "fake", "kraken", "robinhood"}


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
    assert al.cash_only is True  # #372: the cash-account posture, declared and enforced at build
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
        assert row.cash_only == cap.cash_only
        assert row.supports_fee_summary == cap.supports_fee_summary
        assert row.quote_currencies == tuple(sorted(cap.quote_currencies))
        assert row.asset_classes == tuple(sorted(cap.asset_classes))
        assert row.supported_orders == tuple(sorted(cap.supported_orders))
        assert row.preview == (
            "native"
            if cap.supports_native_preview
            else "synthesized" if cap.synthesizes_preview else "none"
        )


def test_every_first_party_adapter_declares_the_cash_only_posture() -> None:
    """#372: the borrowing question is answered by every installed adapter, and the answer
    is uniform -- cash only. That uniformity is the charter, made visible: the one adapter
    that someday declares `False` is declaring a posture the engine can refuse at load,
    and this test is what makes its appearance a DECISION rather than a drift."""
    for info in brokers.list_installed_brokers():
        assert info.cash_only is True, info.name


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


# -- the wired/optional classification is pinned to the tracked configs ----------------------------


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


# The Venues-browser section stood here: `_venues_lines` and five tests over
# `console.build_venues_lines`, the TUI's rendering of the same service `keel brokers list`
# renders. #541 deleted the console layer, so the renderer is gone and its tests with it.
#
# The SERVICE is untouched and still covered above -- `list_installed_brokers` is what both
# front-ends always called, and `keel brokers list` is the surface that remains. What is no
# longer asserted is a second rendering of it, because there is no second rendering.


