"""The in-app profile switcher (#814), and the review of #815 that rebuilt it.

#815 shipped every running console's `http://.../?token=...` inside `/api/config` and wrote it
into `<option value>`: the paper console's page carried the live console's session token, and
its own, which `security.py` says "must never be written into the page". These tests pin the
shape that replaced it:

* The page learns `{label, href, current}` per console and nothing else. `href` is a
  token-free `/switch/<port>` on the CURRENT console.
* `/switch/<port>` resolves the peer's record server-side, at click time, and redirects into the
  peer's own `?token=` hand-off -- the one path a token already travels (`keel open`). The page's
  JavaScript never sees it.
* The current console is always listed, from its own arguments, whether or not it wrote a record
  (a terminal `keel serve` writes none).
* No mode in the label: a mode recorded at start-up goes stale, and the peer's own badge reads
  it live the moment you arrive.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from keel.web import api, payload, runtime, security
from keel.web import server as web_server
from tests.web.test_client_assets import _code_only, _source
from tests.web.test_server import _request, _serving, _session

PEER_TOKEN = "peer-session-token-that-must-never-reach-a-page"


def _write_record(run: Path, port: int, **extra: object) -> None:
    body = {"pid": 4242, "host": "127.0.0.1", "port": port, "token": PEER_TOKEN, **extra}
    (run / f"serve-{port}.json").write_text(json.dumps(body))


@pytest.fixture
def run_dir(tmp_path: Path) -> Iterator[Path]:
    """A deployment root whose run directory holds records, every one of them reading as live."""
    root = tmp_path / "root"
    run = root / runtime.RUN_DIR_NAME
    run.mkdir(parents=True)
    with (
        patch("keel.web.runtime.state_root", return_value=root),
        patch("keel.web.runtime.process_alive", return_value=True),
        patch("keel.web.runtime._port_answers", return_value=True),
    ):
        yield run


# -- runtime ---------------------------------------------------------------------------------------


def test_record_serving_writes_the_profile_and_no_mode(tmp_path: Path) -> None:
    """The profile names the record's console in a peer's list. A mode is NOT recorded: it would
    be read once at start-up and shown until the process died, while `_auto_trade_mode` re-reads
    the config per request precisely so a badge never shows a mode the config no longer says."""
    with patch("keel.web.runtime.state_root", return_value=tmp_path):
        named = runtime.record_serving(
            host="127.0.0.1", port=8765, token="t", interactive=False, profile="keel-live"
        )
        unnamed = runtime.record_serving(host="127.0.0.1", port=8766, token="t", interactive=False)

    assert named is not None
    assert unnamed is not None
    assert set(json.loads(named.read_text())) == {
        "pid",
        "host",
        "port",
        "token",
        "started_ts",
        "profile",
    }
    assert json.loads(named.read_text())["profile"] == "keel-live"
    assert "profile" not in json.loads(unnamed.read_text())


def test_live_peers_names_the_other_consoles_by_port_and_profile_only(run_dir: Path) -> None:
    """Port and profile, nothing else -- no token, no URL -- and never the caller's own record."""
    _write_record(run_dir, 8766, profile="keel")
    _write_record(run_dir, 8765, profile="keel-live")
    _write_record(run_dir, 8767)

    assert runtime.live_peers(exclude_port=8765) == [
        {"port": 8766, "profile": "keel"},
        {"port": 8767, "profile": ""},
    ]


def test_live_peers_skips_a_record_whose_process_is_gone(run_dir: Path) -> None:
    _write_record(run_dir, 8766, profile="keel", pid=1)
    _write_record(run_dir, 8767, profile="keel-paperhourly", pid=2)

    with patch("keel.web.runtime.process_alive", side_effect=lambda pid: pid == 2):
        assert runtime.live_peers(exclude_port=8765) == [
            {"port": 8767, "profile": "keel-paperhourly"}
        ]


# -- payload ---------------------------------------------------------------------------------------


def test_the_switcher_offers_the_other_consoles_by_token_free_href() -> None:
    """Choices are where to GO, so never this console; the card's `display` names everyone, this
    one marked -- from its own arguments, since a terminal `keel serve` writes no record and #815,
    built from records alone, then named a different console as this one."""
    peers = [{"port": 8766, "profile": "keel"}, {"port": 8764, "profile": ""}]

    assert payload.profile_switcher("keel-live", 8765, peers, local=True) == {
        "switchable": True,
        "choices": [
            {"label": "port 8764", "href": "/switch/8764"},
            {"label": "keel", "href": "/switch/8766"},
        ],
        "display": "port 8764, keel-live (this console), keel",
    }


def test_the_switcher_drops_this_consoles_own_record() -> None:
    peers = [{"port": 8765, "profile": "keel-live"}, {"port": 8766, "profile": "keel"}]

    switcher = payload.profile_switcher("keel-live", 8765, peers, local=True)

    assert switcher["choices"] == [{"label": "keel", "href": "/switch/8766"}]
    assert switcher["display"] == "keel-live (this console), keel"


@pytest.mark.parametrize(
    ("peers", "local"),
    [([], True), ([{"port": 8765, "profile": "keel-live"}], True), ([{"port": 8766}], False)],
    ids=["alone", "only-itself", "remote"],
)
def test_the_switcher_offers_nothing_when_there_is_nowhere_to_go(
    peers: list[dict[str, object]], local: bool
) -> None:
    """Alone, there is nothing to switch to. Remote, the peers' loopback addresses cannot be
    opened from the viewer's device, and `/switch/` refuses there anyway."""
    assert payload.profile_switcher("keel-live", 8765, peers, local=local) == {
        "switchable": False,
        "choices": [],
        "display": "",
    }


# -- the wire --------------------------------------------------------------------------------------


def test_api_config_carries_no_session_token(deployment: tuple[str, str], run_dir: Path) -> None:
    """The #815 defect, end to end: a peer's token, and this console's own, reach no page."""
    _write_record(run_dir, 8766, profile="keel-live")

    with _serving(deployment) as cfg:
        status, _headers, body = _request(cfg, "/api/config", cookie=_session(cfg))

    assert status == 200
    assert PEER_TOKEN not in body
    assert cfg.token not in body
    data = json.loads(body)["data"]
    assert "peers" not in data
    assert data["switcher"]["choices"] == [{"label": "keel-live", "href": "/switch/8766"}]


def test_read_config_offers_no_switch_on_a_remote_deployment(
    deployment: tuple[str, str], run_dir: Path
) -> None:
    _write_record(run_dir, 8766, profile="keel-live")
    db_path, config_path = deployment
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=8765,
        token="t",
        db_path=db_path,
        config_path=config_path,
        external_hosts=frozenset({"keel.example.com"}),
    )

    assert api.read_config(cfg, {}, None, 0)["switcher"]["switchable"] is False


# -- /switch/<port> --------------------------------------------------------------------------------


def test_switch_redirects_into_the_peers_own_hand_off(
    deployment: tuple[str, str], run_dir: Path
) -> None:
    """The token travels in a `Location` header, to the peer, which exchanges it for its cookie
    and strips it from the URL -- exactly the hand-off `keel open` performs. No cookie is set
    here: this console's session is not the peer's."""
    _write_record(run_dir, 8766, profile="keel-live")

    with _serving(deployment) as cfg:
        status, headers, _body = _request(cfg, "/switch/8766", cookie=_session(cfg))

    assert status == 303
    assert headers["Location"] == f"http://127.0.0.1:8766/?token={PEER_TOKEN}"
    assert "Set-Cookie" not in headers


def test_switch_to_this_console_goes_home(deployment: tuple[str, str], run_dir: Path) -> None:
    with _serving(deployment) as cfg:
        status, headers, _body = _request(cfg, f"/switch/{cfg.port}", cookie=_session(cfg))

    assert (status, headers["Location"]) == (303, "/")


@pytest.mark.parametrize("path", ["/switch/8799", "/switch/abc", "/switch/", "/switch/8766/x"])
def test_switch_to_no_running_console_is_a_404(
    deployment: tuple[str, str], run_dir: Path, path: str
) -> None:
    _write_record(run_dir, 8766, profile="keel-live")

    with _serving(deployment) as cfg:
        status, headers, body = _request(cfg, path, cookie=_session(cfg))

    assert status == 404
    assert "Location" not in headers
    assert PEER_TOKEN not in body


def test_switch_without_a_session_is_refused(deployment: tuple[str, str], run_dir: Path) -> None:
    """Admission first: without this console's cookie, nothing about any other is revealed."""
    _write_record(run_dir, 8766, profile="keel-live")

    with _serving(deployment) as cfg:
        status, headers, body = _request(cfg, "/switch/8766")

    assert status == 403
    assert "Location" not in headers
    assert PEER_TOKEN not in body


def test_switch_is_refused_on_a_remote_deployment(
    deployment: tuple[str, str], run_dir: Path
) -> None:
    """A tunnel forwards to loopback, so the peer check alone would pass; the declared remote
    origin is what refuses -- the same floor `gated_action_permitted` holds for a release."""
    _write_record(run_dir, 8766, profile="keel-live")
    remote = frozenset({"keel.example.com"})

    with _serving(deployment, external_hosts=remote) as cfg:
        status, headers, body = _request(cfg, "/switch/8766", cookie=_session(cfg))

    assert status == 403
    assert "Location" not in headers
    assert PEER_TOKEN not in body


def test_local_deployment_is_the_first_two_conditions_of_a_gated_action() -> None:
    """One definition of "this deployment is local", shared by the release gate and the
    switcher, so the two cannot drift into different answers."""
    assert security.local_deployment(external_hosts=frozenset(), bound_host="127.0.0.1")
    assert not security.local_deployment(
        external_hosts=frozenset({"keel.example.com"}), bound_host="127.0.0.1"
    )
    assert not security.local_deployment(external_hosts=frozenset(), bound_host="192.168.1.5")


# -- the service worker ----------------------------------------------------------------------------


def test_the_worker_sends_a_switch_navigation_to_the_network() -> None:
    """The worker answers every navigation from its cached shell. A `/switch/` answered that way
    never reaches the server, and the select would appear to do nothing."""
    source = (Path(web_server.__file__).parent / "static" / "sw.js").read_text(encoding="utf-8")
    branch = re.search(r'if \(request\.mode === "navigate"\) \{(.*?)\n  \}', source, re.DOTALL)
    assert branch is not None
    body = branch.group(1)

    guard = re.search(
        r"^\s*if \(url\.pathname\.startsWith\(SWITCH_PREFIX\)\) return;\s*$", body, re.MULTILINE
    )
    assert guard is not None
    assert guard.start() < body.index("caches.match(")
    assert f'const SWITCH_PREFIX = "{web_server.SWITCH_PREFIX}";' in source


# -- the client ------------------------------------------------------------------------------------


def _function(source: str, name: str) -> str:
    match = re.search(r"export function " + name + r"\(.*?\n\}\n", source, re.DOTALL)
    assert match is not None, f"{name} is not in the shape this test understands"
    return _code_only(match.group(0))


def test_no_element_is_built_with_spread_children() -> None:
    """`el(tag, className, text)` has no children form. #815's `el("select", "...", ...options)`
    set the select's text to "[object HTMLOptionElement]" and dropped every option."""
    code = _code_only(_source("render.js"))
    assert re.search(r"\bel\([^;]*\.\.\.", code) is None


def test_the_switcher_builds_a_select_of_the_choices_behind_a_placeholder() -> None:
    code = _function(_source("render.js"), "consoleSwitcher")

    assert "config.switcher.switchable" in code
    # `el("select", "console-select")` -- tag and class, and no text argument to swallow a child.
    assert re.search(r"= el\( ,  \);", code) is not None
    assert "for (const choice of config.switcher.choices)" in code
    assert code.count(".append(") == 2  # the placeholder, then each choice
    assert "choice.href" in code
    assert "choice.label" in code


def test_the_switcher_can_only_navigate() -> None:
    """The #704 rule's counterpart for the one control #814 adds: it may offer a way to another
    running console and nothing else -- no button, link, form or input, and no handler of its own
    (`main.js` binds the one `change`, which only follows an option's value)."""
    code = _function(_source("render.js"), "consoleSwitcher")
    for forbidden in ("button", "addEventListener", "onclick", '"a"', "form", "input"):
        assert forbidden not in code, f"consoleSwitcher builds something interactive: {forbidden}"


def test_the_chip_does_not_hold_the_switcher() -> None:
    """Beside the chip, never inside it: #704 forbids the chip being a control, and #815's
    dropdown lived in `sessionChip` and got past that test only because `select` is not on its
    list. The chip neither reads the switcher nor builds a select."""
    chip = _function(_source("render.js"), "sessionChip")
    assert "switcher" not in chip
    assert "select" not in chip


def test_the_switcher_node_sits_in_the_header_beside_the_chip_and_ships_empty() -> None:
    from tests.web.test_client_assets import _INDEX, _markup_only

    html = _markup_only(_INDEX.read_text(encoding="utf-8"))
    assert '<span id="console-switcher"></span>' in html
    chip_end = html.index("</div>", html.index('<div id="session-chip">'))
    assert chip_end < html.index('id="console-switcher"') < html.index('id="view"')


def test_the_deployment_card_places_the_switchers_display() -> None:
    card = _function(_source("render.js"), "deploymentCard")

    assert "config.switcher.display" in card
    assert "peers" not in card


def test_choosing_a_console_navigates_to_its_href() -> None:
    main = _code_only(_source("main.js"))
    handler = re.search(
        r"consoleSwitcherNode\.addEventListener\( , \(event\) => \{(.*?)\n\}\);", main, re.DOTALL
    )
    assert handler is not None
    assert "window.location.assign(" in handler.group(1)
    assert "sessionProfileNode.addEventListener" not in main
