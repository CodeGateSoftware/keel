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
from dataclasses import replace as dataclasses_replace
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import brokers
from keel.venue_readiness import VenueReadiness

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


@pytest.mark.parametrize(
    ("cash_only", "funding_word"),
    [(True, "cash only"), (False, "MARGIN-CAPABLE")],
    ids=["cash-only-row", "margin-capable-row"],
)
def test_capability_facts_names_the_funding_posture_loudly_in_both_directions(
    cash_only: bool, funding_word: str
) -> None:
    """The `cash_only=False` branch of `capability_facts` is dead in practice -- every
    installed adapter declares True (the uniformity test above) -- which is exactly the
    state in which a wording regression ships unnoticed. The PR's own rationale calls
    that branch the LOUD declaration: an adapter announcing a borrowing path must read
    as "MARGIN-CAPABLE", never as a quieter synonym that a skimming operator misses.
    Pinned on a synthesized row (a `replace` of a real one) because no installed adapter
    carries `False`, and that is the point."""
    row = dataclasses_replace(_infos()["coinbase"], cash_only=cash_only)
    assert funding_word in brokers.capability_facts(row).split(" · ")


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
        cash_only=True,
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


# -- venue readiness (#233 PR4): a SEPARATE block, never merged into the declarations one ---------


def test_no_key_inference_line_is_unchanged_text() -> None:
    """A pin against silent rewording, independent of where it appears."""
    assert brokers.NO_KEY_INFERENCE_LINE == (
        "the declarations above are a capability display only -- no key presence is read or "
        "implied, and no secret is shown"
    )


def test_the_no_key_inference_line_scopes_itself_to_its_own_block() -> None:
    """It is no longer the last line of the command, only of its own block -- a readiness block
    that DOES read key presence now follows it about twenty lines later. A line that still said a
    bare "capability display only" would read, mid-output, as a claim about everything below it,
    which is the blur #233 exists to remove. So it must name what it is scoping."""
    assert "the declarations above" in brokers.NO_KEY_INFERENCE_LINE


def test_render_brokers_lines_ends_on_the_no_key_inference_line() -> None:
    """`render_brokers_lines` -- called DIRECTLY, not through the CLI -- must still end on the
    declarations' own honesty line. This is the half of the pin a merge-readiness-in mutant that
    inserts rows BEFORE the line (keeping it last) would still pass; see the test below for the
    half that catches exactly that mutant."""
    lines = brokers.render_brokers_lines(brokers.list_installed_brokers())
    assert lines[-1] == brokers.NO_KEY_INFERENCE_LINE


def test_render_brokers_lines_never_emits_readiness_vocabulary() -> None:
    """The other half: `render_brokers_lines` must not contain the readiness block's header or
    its own honesty line ANYWHERE in its output, regardless of position. A mutant that merges
    `render_readiness_lines`' rows into `render_brokers_lines` -- even if it is careful to insert
    them before the last line, so the pin above still passes -- fails HERE, because the
    readiness header/honesty-line text would then appear inside this function's own output."""
    text = "\n".join(brokers.render_brokers_lines(brokers.list_installed_brokers()))
    assert brokers.READINESS_HEADER not in text
    assert brokers.READINESS_HONESTY_LINE not in text


def test_brokers_list_prints_declarations_before_readiness_in_that_order() -> None:
    """End to end through the CLI: the declarations' honesty line and the readiness block's
    header both appear, in that order, with every venue's readiness row strictly after the
    declarations' honesty line -- not merely "somewhere after some other string"."""
    result = CliRunner().invoke(cli, ["brokers", "list"])
    assert result.exit_code == 0, result.output
    text = result.output

    decl_idx = text.index(brokers.NO_KEY_INFERENCE_LINE)
    header_idx = text.index(brokers.READINESS_HEADER)
    honesty_idx = text.index(brokers.READINESS_HONESTY_LINE)
    assert decl_idx < header_idx < honesty_idx

    # ⚠️ This assertion used to be `text.index(f"{name}: ", decl_idx) > decl_idx`, which is
    # TAUTOLOGICAL: `str.index` with a start offset can never return a position below it, so it
    # held against every possible output. Split the text at the header instead and make two
    # claims that can actually fail.
    declarations_block, readiness_block = text[:header_idx], text[header_idx:]

    for name in _INSTALLED:
        assert f"{name}: " in readiness_block, (
            f"{name} has no readiness row after the header -- rows are being rendered into the "
            "declarations block, or not at all"
        )

    for state in VenueReadiness:
        assert state.value not in declarations_block, (
            f"the readiness state {state.value!r} leaked into the declarations block, which "
            "NO_KEY_INFERENCE_LINE terminates and claims reads no key presence"
        )


def test_brokers_list_json_still_carries_no_readiness_vocabulary() -> None:
    """`--json` stays the declarations-only shape (unchanged by #233): no `readiness` key, no
    state word, no honesty line -- proving the machine surface was not quietly widened."""
    result = CliRunner().invoke(cli, ["brokers", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert "readiness" not in result.output
    assert brokers.READINESS_HEADER not in result.output


def test_brokers_list_shows_a_readiness_row_for_every_installed_adapter() -> None:
    result = CliRunner().invoke(cli, ["brokers", "list"])
    assert result.exit_code == 0, result.output
    for name in _INSTALLED:
        assert f"{name}: " in result.output


def test_brokers_list_still_works_with_no_deployment_readiness_included() -> None:
    """The existing no-deployment contract (`test_brokers_list_renders_the_service_rows`) must
    keep holding even though the command now also gathers readiness -- no crash, no "no such
    file", on a run with nothing set up."""
    result = CliRunner().invoke(cli, ["--db", "/nonexistent/keel.db", "brokers", "list"])
    assert result.exit_code == 0, result.output
    assert "no such file" not in result.output.lower()
    assert brokers.READINESS_HEADER in result.output


def test_render_readiness_lines_names_state_explanation_and_fix() -> None:
    from keel.venue_readiness import VenueReadiness, VenueReadinessRow

    rows = [
        VenueReadinessRow(
            venue="acme",
            state=VenueReadiness.NOT_PERMITTED,
            explanation="acme has never attested or confirmed a live trade scope",
            next_step="keel scope attest --trading --venue acme",
        ),
        VenueReadinessRow(
            venue="zen", state=VenueReadiness.READY, explanation="all clear", next_step=None
        ),
    ]
    lines = brokers.render_readiness_lines(rows)
    text = "\n".join(lines)
    assert "acme: not_permitted" in text
    assert "acme has never attested" in text
    assert "fix: keel scope attest --trading --venue acme" in text
    assert "zen: ready" in text
    assert "all clear" in text
    assert lines[-1] == brokers.READINESS_HONESTY_LINE
    # READY carries no "fix:" line -- there is nothing left to do.
    zen_idx = text.index("zen: ready")
    assert "fix:" not in text[zen_idx:]
