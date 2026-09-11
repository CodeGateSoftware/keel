"""The cockpit's attestation banner (#793).

The evidence this exists for: rail 17 expired on 2026-09-08 and the Status page read
`expired · EXPIRED FOR 2d ago` for three days while live placed no orders and nobody noticed.
The state was on screen. It was a field in a card, below the fold, on one page of eleven.

So the banner is carried by the ENVELOPE rather than by the status payload -- every page gets
it, including the ones an operator is actually looking at when the attestation lapses. The key
is present on every success envelope and is `null` when there is no deployment to read, which is
`envelope`'s own constant-key-set rule (Rule 3: a client must never branch on payload SHAPE).
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from keel import attestations
from keel.web import payload
from tests.web.test_client_assets import _comments_only
from tests.web.test_view_sections import _JS, _decl

NOW = 1_789_000_000
HOUR = 3_600


def _at(state: str) -> attestations.Attestation:
    """One attestation of `key` "withdrawal", positioned to be in `state` at `NOW`."""
    definition = attestations.definition("withdrawal")
    if state == attestations.MISSING:
        return definition
    offsets = {
        attestations.OK: definition.warn_within_sec + 5 * HOUR,
        attestations.EXPIRING: HOUR,
        attestations.EXPIRED: -3 * 86_400,
    }
    expires_at = NOW + offsets[state]
    made = expires_at - attestations.withdrawal_expiry(attested_at=0)
    assert definition.__class__ is attestations.Attestation
    return attestations.Attestation(
        key=definition.key,
        rail=definition.rail,
        label=definition.label,
        remedy=definition.remedy,
        warn_within_sec=definition.warn_within_sec,
        attested_at=made,
        expires_at=expires_at,
    )


def test_a_healthy_attestation_raises_no_banner() -> None:
    assert payload.attestation_alerts([_at(attestations.OK)], NOW) == []


@pytest.mark.parametrize(
    "state", [attestations.EXPIRING, attestations.EXPIRED, attestations.MISSING]
)
def test_anything_but_ok_reaches_the_banner(state: str) -> None:
    """`MISSING` included, and that is the case that was live on 2026-09-11: rail 22 had never
    been attested at all, so there was no expiry to warn about -- the attestation simply did not
    exist, and every live entry was refused for it."""
    (alert,) = payload.attestation_alerts([_at(state)], NOW)
    assert alert["state"]["value"] == state


def test_the_banner_names_the_remedy_because_the_browser_cannot_run_it() -> None:
    """Rails 17 and 22 are attested from a TTY and #781 deliberately did not put them behind the
    browser gate. A banner that says "expired" without the command is a dead end."""
    (alert,) = payload.attestation_alerts([_at(attestations.EXPIRED)], NOW)
    assert alert["remedy"] == "keel withdrawals attest --enabled"


def test_an_expiry_is_bad_and_an_approaching_one_is_a_warning() -> None:
    """Rule 3: the client is handed the judgement, it does not make one."""
    (expiring,) = payload.attestation_alerts([_at(attestations.EXPIRING)], NOW)
    (expired,) = payload.attestation_alerts([_at(attestations.EXPIRED)], NOW)
    assert expiring["state"]["state"] == payload.WARN
    assert expired["state"]["state"] == payload.BAD


def test_the_banner_says_how_long_is_left_in_the_CLIs_own_words() -> None:
    (alert,) = payload.attestation_alerts([_at(attestations.EXPIRING)], NOW)
    assert (alert["when_label"], alert["when"]["display"]) == ("expires in", "1h")


def test_no_wire_value_in_the_banner_is_ever_a_json_number() -> None:
    """Rule 1, on the new rows. `seconds_left` is an `int` in Python and must not cross as one."""
    alerts = payload.attestation_alerts(
        [_at(attestations.EXPIRING), _at(attestations.EXPIRED)], NOW
    )

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            raise AssertionError(f"bool on the wire: {node!r}")
        if isinstance(node, (int, float)):
            raise AssertionError(f"number on the wire: {node!r}")
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        if isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(json.dumps(alerts)))


def test_every_success_envelope_carries_the_key_even_with_nothing_to_report() -> None:
    """Constant key set. A client that tests for the key's PRESENCE is branching on shape."""
    running = payload.envelope(NOW, running=True, data={}, attestations=[])
    stopped = payload.envelope(NOW, running=False, data=None)
    assert running["attestations"] == []
    assert stopped["attestations"] is None


# -- the wiring ------------------------------------------------------------------------------
#
# The payload function above is inert until `respond` calls it, and a route-level test that only
# reads `data` would stay green with the call deleted. These drive `respond` itself.


def _cfg(deployment: tuple[str, str]) -> Any:
    from keel.web.server import ServeConfig

    db_path, config_path = deployment
    return ServeConfig(
        host="127.0.0.1", port=0, token="t", db_path=db_path, config_path=config_path
    )


def test_every_endpoint_answers_with_the_banner_not_just_the_status_one(
    deployment: tuple[str, str],
) -> None:
    """The point of putting it on the envelope. A fresh deployment has attested nothing, so rails
    17 and 22 are both `MISSING` and every page says so."""
    from keel.web import api as web_api

    for path in ("/api/status", "/api/orders", "/api/positions"):
        _, document = web_api.respond(_cfg(deployment), path, {})
        rails = {alert["rail"] for alert in document["attestations"]}
        assert rails == {"17", "22"}, path


def test_an_attested_deployment_raises_no_banner(deployment: tuple[str, str]) -> None:
    """The negative control: without it the test above passes on a `respond` that always returns
    both rails regardless of what the database says."""
    import time

    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.web import api as web_api

    db_path, _ = deployment
    repo = Repository(connect(db_path))
    now = int(time.time())
    repo.set_state("withdrawals_attested_at", str(now))
    repo.set_state("withdrawals_enabled", "true")
    web_api.close_repo(repo)

    _, document = web_api.respond(_cfg(deployment), "/api/status", {})
    assert [alert["rail"] for alert in document["attestations"]] == ["22"]


def test_a_stopped_engine_reports_null_rather_than_nothing_is_wrong(tmp_path: Any) -> None:
    from keel.web import api as web_api
    from keel.web.server import ServeConfig

    cfg = ServeConfig(
        host="127.0.0.1",
        port=0,
        token="t",
        db_path=str(tmp_path / "absent.db"),
        config_path=str(tmp_path / "absent.yaml"),
    )
    _, document = web_api.respond(cfg, "/api/status", {})
    assert document["engine"]["value"] != "running"
    assert document["attestations"] is None


def test_an_expired_attestation_shows_the_date_it_lapsed_not_a_countdown_to_zero() -> None:
    """`seconds_left` floors at zero, so an expired row offered a countdown renders "expires in
    0s" beside the word "expired" -- two claims about the same instant, one of them false."""
    (alert,) = payload.attestation_alerts([_at(attestations.EXPIRED)], NOW)
    assert alert["when_label"] == "expired"
    assert alert["when"]["display"] != payload.absent()["display"]


def test_a_never_attested_rail_does_not_claim_to_have_lapsed() -> None:
    """Rail 22 on the live deployment: never attested once, so there is no expiry and no date.
    Calling that "expired" would name an event that never happened."""
    (alert,) = payload.attestation_alerts([_at(attestations.MISSING)], NOW)
    assert alert["when_label"] == "never attested"
    assert alert["when"]["display"] == payload.absent()["display"]


# -- the client ------------------------------------------------------------------------------
#
# No JavaScript runtime in this suite, so every assertion below is over the SOURCE. That makes
# the rule from #775 binding here: **a source-text scan passes on a declaration alone.** Naming
# `attestationBanner` proves someone typed it, never that anything calls it. So each test asserts
# a count or a pairing, and names the mutation it exists to reject.


def _code(name: str) -> str:
    return _comments_only((_JS / name).read_text(encoding="utf-8"))


def test_the_shell_paints_the_banner_from_the_envelope_on_every_route() -> None:
    """Rejects: `attestationBanner` declared and never called; called from `mount`, where a route
    whose `data` is `null` never reaches it; and handed `primary.data`, which is the report and
    has no attestations on it."""
    paint = _decl(_code("main.js"), "paint")
    assert paint.count("attestationBanner(attestBannerNode, primary.attestations)") == 1


def test_the_banner_node_the_client_demands_is_the_one_the_page_ships() -> None:
    """Rejects an id typo: `must` throws at boot, so a mismatch is a blank dashboard rather than
    a missing banner."""
    (node_id,) = re.findall(r'must\("(attest[a-z-]*)"\)', _code("main.js"))
    html = (_JS.parent / "index.html").read_text(encoding="utf-8")
    assert html.count('id="' + node_id + '"') == 1


def test_a_healthy_deployment_clears_the_banner_rather_than_leaving_it() -> None:
    """**The mutation that matters most.** Dropping `replaceChildren()` from the empty branch
    leaves the last alert on screen forever: an operator renews the attestation, the banner keeps
    saying it expired, and the next real one is indistinguishable from the stale one."""
    body = _decl(_code("render.js"), "attestationBanner")
    empty = body[body.index("if (rows.length === 0)") : body.index("let tone")]
    assert empty.count("node.replaceChildren()") == 1
    assert empty.count("return;") == 1


def test_null_and_an_empty_list_both_draw_nothing() -> None:
    """Rejects a banner that renders `null` as a row -- the stopped-engine and tick envelopes both
    send `null`, so this would put a phantom alert on the first-run page."""
    body = _decl(_code("render.js"), "attestationBanner")
    assert body.count("Array.isArray(alerts) ? alerts : []") == 1


def test_one_expired_attestation_tones_the_whole_banner_as_an_outage() -> None:
    """Rejects a tone loop that never escalates: three approaching attestations and one expired
    one would read as "soon" while entries are already being refused."""
    body = _decl(_code("render.js"), "attestationBanner")
    assert body.count('if (state === "bad") tone = "bad";') == 1
    assert body.index('let tone = "warn"') < body.index('tone = "bad"')


def test_every_alert_row_carries_its_remedy() -> None:
    """Rejects dropping the `<code>`: rails 17 and 22 are attested from a TTY, so a banner
    without the command is a dead end for the one person who can act on it."""
    body = _decl(_code("render.js"), "attestationLine")
    assert body.count('el("code", "remedy", plain(alert.remedy))') == 1
    # Six appends: the rail, the label, the state, the words, the instant, the remedy.
    assert body.count("line.append") == 6


def test_the_row_renders_the_instant_the_server_chose_and_picks_none_itself() -> None:
    """Rejects a client-side branch between a countdown and a date. It would have to read
    `Field.value` (which this file does not do) or the rendered text (worse), and the server
    already decided -- `when_label` and `when` are one statement, sent together."""
    body = _decl(_code("render.js"), "attestationLine")
    assert body.count("alert.when_label") == 1 and body.count("field(alert.when)") == 1
    assert "alert.when.value" not in body
    assert " if " not in body and "?" not in body


def test_an_empty_banner_takes_up_no_room() -> None:
    """Rejects the missing `:empty` rule: a sticky strip with a border and no text, on every page,
    forever."""
    css = (_JS.parent / "css" / "keel.css").read_text(encoding="utf-8")
    assert css.count(".attestbanner:empty { display: none; }") == 1
    assert css.count("position: sticky;") >= 1


def test_the_banner_has_words_for_every_state_the_model_can_be_in() -> None:
    """A state added to `keel.attestations` and missed in the tone or wording table is a
    `KeyError` raised while building the envelope -- which is every page of the cockpit, not a
    missing row. Derived from the model so adding one fails HERE."""
    states = {
        getattr(attestations, name)
        for name in dir(attestations)
        if name.isupper() and isinstance(getattr(attestations, name), str)
    }
    covered = set(payload._ATTESTATION_WHEN) | {attestations.OK}
    assert states - covered == set()


def test_a_suspended_attestation_reads_as_the_refusal_it_is() -> None:
    """Rail 17 with a fresh attestation that says NO. The expiry is in the future, so the date
    that means anything is when it was asked."""
    definition = attestations.definition("withdrawal")
    refused = attestations.Attestation(
        key=definition.key,
        rail=definition.rail,
        label=definition.label,
        remedy=definition.remedy,
        attested_at=NOW - 86_400,
        expires_at=NOW + 6 * 86_400,
        satisfied=False,
    )
    (alert,) = payload.attestation_alerts([refused], NOW)
    assert alert["state"]["state"] == payload.BAD
    assert alert["when_label"] == "attested"
    assert alert["when"]["value"] == payload.iso(NOW - 86_400)
