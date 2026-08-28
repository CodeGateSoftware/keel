"""The confirm gate is the last thing standing between a rule and real money.

`Preview`'s docstring states the requirement these tests enforce: "approving an estimate must
never look identical to approving a broker's own quote." The gate used to take a raw `dict` and
had nowhere to render `Preview.synthetic`, so it could not tell a human which of the two they
were looking at. Today that is latent (Coinbase has a native preview endpoint), but the first
synthesizing venue makes it real -- and a synthesized preview that could not be priced comes back
as zeroes, which on an undecorated key/value screen reads as a harmless "$0.00 order" rather than
as "keel has no idea what this costs".

So these tests assert on the *rendered text a human sees*, not on a return value: the failure
mode being defended against is a human misreading the screen, and only the screen can be wrong.
The gate reads ONE shape -- the port's `Preview` -- since #524 finished the broker-port
migration; a dict is refused as unreadable, fail-closed, because nothing on the live path
produces one anymore.
"""

from __future__ import annotations

from decimal import Decimal

import click
import pytest
from keel_broker_api.results import Preview
from keel_core.types import Side

import keel.cli as cli_module


@pytest.fixture
def at_a_terminal(monkeypatch):
    """A human is watching. The TTY predicate lives in `keel.commands._common`."""
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: True)


@pytest.fixture
def never_asked(monkeypatch):
    """Make both prompts explode, so a test can prove which one the gate reached."""

    def _confirm(*args, **kwargs):
        raise AssertionError("the gate used the ordinary yes/no confirm")

    def _prompt(*args, **kwargs):
        raise AssertionError("the gate used the typed-phrase prompt")

    monkeypatch.setattr(cli_module.click, "confirm", _confirm)
    monkeypatch.setattr(cli_module.click, "prompt", _prompt)


def _preview(**overrides) -> Preview:
    fields: dict = {
        "product_id": "BTC-USD",
        "side": Side.BUY,
        "est_base_size": Decimal("0.00005000"),
        "est_quote_size": Decimal("5.00"),
        "est_fee": Decimal("0.03"),
        "synthetic": False,
    }
    fields.update(overrides)
    return Preview(**fields)


def _answers(monkeypatch, *, confirm=None, prompt=None) -> None:
    if confirm is not None:
        monkeypatch.setattr(cli_module.click, "confirm", lambda *a, **k: confirm)
    if prompt is not None:
        monkeypatch.setattr(cli_module.click, "prompt", lambda *a, **k: prompt)


# -- native vs synthetic ------------------------------------------------------------------------


def test_native_preview_renders_as_a_broker_quote(monkeypatch, capsys, at_a_terminal):
    """A venue-priced preview says so, and asks the ordinary yes/no question."""
    _answers(monkeypatch, confirm=True)
    monkeypatch.setattr(
        cli_module.click,
        "prompt",
        lambda *a, **k: pytest.fail("a clean native preview must not demand a typed phrase"),
    )

    assert cli_module._interactive_confirm(_preview()) is True

    out = capsys.readouterr().out
    assert cli_module.NATIVE_PREVIEW_MARKER in out
    assert cli_module.SYNTHETIC_PREVIEW_MARKER not in out
    assert "est_quote_size: 5.00" in out


def test_synthetic_preview_is_unmistakably_marked(monkeypatch, capsys, at_a_terminal):
    """The same numbers, computed by keel instead of quoted by the venue, must not look the
    same. A priced synthetic preview is still approvable with the ordinary confirm -- the
    warning is the banner, not extra ceremony on every Robinhood exit."""
    _answers(monkeypatch, confirm=True)
    monkeypatch.setattr(
        cli_module.click,
        "prompt",
        lambda *a, **k: pytest.fail("a priced synthetic preview must not demand a typed phrase"),
    )

    assert cli_module._interactive_confirm(_preview(synthetic=True)) is True

    out = capsys.readouterr().out
    assert cli_module.SYNTHETIC_PREVIEW_MARKER in out
    assert cli_module.NATIVE_PREVIEW_MARKER not in out
    # Not a footnote: the warning is on screen before the numbers it qualifies.
    assert out.index(cli_module.SYNTHETIC_PREVIEW_MARKER) < out.index("est_quote_size")


def test_detail_can_never_shadow_a_money_field(monkeypatch, capsys, at_a_terminal):
    """`Preview.detail` is free-form text the adapter chose. If a detail key could overwrite
    `est_fee`, an adapter bug would put a wrong number on a spending screen with nothing marking
    it as substituted. The real figure must survive, and the impostor must be namespaced."""
    _answers(monkeypatch, confirm=False)

    cli_module._interactive_confirm(
        _preview(est_fee=Decimal("0.03"), detail={"est_fee": "not-a-fee", "price": "103700.00"})
    )

    out = capsys.readouterr().out
    assert "    est_fee: 0.03" in out
    assert "    detail.est_fee: not-a-fee" in out
    assert "    price: 103700.00" in out


# -- errors and unpriced previews ---------------------------------------------------------------


def test_preview_errors_are_shown(monkeypatch, capsys, at_a_terminal):
    """`Preview.errors` is the adapter saying "this did not go cleanly". It must be on screen."""
    _answers(monkeypatch, prompt=cli_module.DEGRADED_PREVIEW_PHRASE)

    cli_module._interactive_confirm(
        _preview(synthetic=True, errors=("INSUFFICIENT_FUND", "PREVIEW_INVALID_BASE_SIZE"))
    )

    out = capsys.readouterr().out
    assert "PREVIEW ERRORS" in out
    assert "INSUFFICIENT_FUND" in out
    assert "PREVIEW_INVALID_BASE_SIZE" in out


def test_unpriced_synthetic_preview_cannot_render_as_a_real_quote(
    monkeypatch, capsys, at_a_terminal
):
    """Zeroes are the dangerous case: they render as a legitimate, very cheap order. The gate
    must say the size is UNKNOWN, not that it is zero, and must not accept a reflexive `y`."""
    _answers(monkeypatch, prompt="")
    monkeypatch.setattr(
        cli_module.click,
        "confirm",
        lambda *a, **k: pytest.fail("an unpriced preview must not be approvable with a bare y/n"),
    )

    assert (
        cli_module._interactive_confirm(
            _preview(synthetic=True, est_base_size=Decimal("0"), est_quote_size=Decimal("0"))
        )
        is False
    )

    out = capsys.readouterr().out
    assert cli_module.UNPRICED_PREVIEW_MARKER in out
    assert cli_module.SYNTHETIC_PREVIEW_MARKER in out


def test_the_typed_phrase_gates_a_degraded_preview(monkeypatch, capsys, at_a_terminal):
    """Wrong phrase declines; the exact phrase places. `yes` is deliberately not enough."""
    unpriced = _preview(synthetic=True, est_quote_size=Decimal("0"))

    _answers(monkeypatch, prompt="yes")
    assert cli_module._interactive_confirm(unpriced) is False

    _answers(monkeypatch, prompt=cli_module.DEGRADED_PREVIEW_PHRASE.upper() + "  ")
    assert cli_module._interactive_confirm(unpriced) is True


def test_a_degraded_preview_is_never_a_silent_block(monkeypatch, at_a_terminal):
    """The friction must not become a wall: a human closing a position has to be able to act
    even when the preview is unpriced AND carries errors. Refusing outright would trap a
    position behind a broken preview endpoint."""
    _answers(monkeypatch, prompt=cli_module.DEGRADED_PREVIEW_PHRASE)

    stuck_exit = _preview(
        side=Side.SELL,
        synthetic=True,
        est_base_size=Decimal("0"),
        est_quote_size=Decimal("0"),
        est_fee=Decimal("0"),
        errors=("no price available for BTC-USD",),
    )
    assert cli_module._interactive_confirm(stuck_exit) is True


def test_an_aborted_typed_prompt_declines(monkeypatch, at_a_terminal):
    """Ctrl-C / EOF at the phrase prompt is a decline, never a traceback out of the gate."""

    def _abort(*args, **kwargs):
        raise click.Abort()

    monkeypatch.setattr(cli_module.click, "prompt", _abort)
    assert cli_module._interactive_confirm(_preview(est_quote_size=Decimal("0"))) is False


# -- a shape the port deleted (#524) ------------------------------------------------------------


def test_a_dict_preview_is_refused_as_an_unreadable_shape(monkeypatch, capsys, at_a_terminal):
    """#524 deleted the gate's legacy dict arm: every broker the live path can now construct --
    the default venue's registry-resolved adapter included -- answers `preview_order` in the
    port's `Preview` type, so a dict is a shape nothing produces anymore. The gate must fail
    closed on it rather than render it as a quote: unrecognized means degraded, and degraded
    means the typed phrase, never a bare y/n."""
    _answers(monkeypatch, prompt="")
    monkeypatch.setattr(
        cli_module.click,
        "confirm",
        lambda *a, **k: pytest.fail("an unreadable preview must not take a bare y/n"),
    )

    assert (
        cli_module._interactive_confirm(
            {
                "order_total": Decimal("5.00"),
                "commission_total": Decimal("0.03"),
                "errs": [],
                "warning": [],
                "best_bid": Decimal("49990"),
                "best_ask": Decimal("50000"),
            }
        )
        is False
    )

    out = capsys.readouterr().out
    assert cli_module.UNREADABLE_PREVIEW_MARKER in out
    # The dict renders as NOTHING readable: not the numbers...
    assert "order_total: 5.00" not in out
    # ...not the broker-quote banner...
    assert cli_module.NATIVE_PREVIEW_MARKER not in out
    # ...and not the venue-specific header the legacy arm used to draw.
    assert "Coinbase order preview" not in out


def test_the_header_is_venue_neutral_for_every_readable_preview(monkeypatch, capsys, at_a_terminal):
    """The "Coinbase order preview" header existed because the dict shape WAS Coinbase's own
    response, and only Coinbase's. A `Preview` can come from any venue, so the header names
    none of them."""
    _answers(monkeypatch, confirm=True)

    assert cli_module._interactive_confirm(_preview()) is True

    out = capsys.readouterr().out
    assert "Rails PASSED. Order preview:" in out
    assert "Coinbase order preview" not in out


def test_an_unreadable_preview_is_treated_as_degraded(monkeypatch, capsys, at_a_terminal):
    """Nothing to render is not a reason to render nothing alarming."""
    _answers(monkeypatch, prompt="")
    monkeypatch.setattr(
        cli_module.click,
        "confirm",
        lambda *a, **k: pytest.fail("an unreadable preview must not take a bare y/n"),
    )

    assert cli_module._interactive_confirm(None) is False
    assert cli_module.UNREADABLE_PREVIEW_MARKER in capsys.readouterr().out


# -- fails closed -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preview",
    [
        {"order_total": Decimal("5.00")},
        _preview(),
        _preview(synthetic=True),
        _preview(synthetic=True, est_quote_size=Decimal("0")),
    ],
)
def test_fails_closed_without_a_tty(monkeypatch, preview, never_asked):
    """No human, no order -- for every shape, degraded or not."""
    monkeypatch.setattr("keel.commands._common._is_interactive", lambda: False)
    assert cli_module._interactive_confirm(preview) is False
