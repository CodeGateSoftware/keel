"""Unit and integration tests for in-app profile switcher discovery (#814)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from keel.web import payload, runtime
from tests.web.test_client_assets import _source


def test_record_serving_includes_profile_and_mode(tmp_path: Path) -> None:
    """record_serving writes profile and mode into serve-<port>.json when provided."""
    with patch("keel.web.runtime.state_root", return_value=tmp_path):
        record_path = runtime.record_serving(
            host="127.0.0.1",
            port=8765,
            token="test-token",
            interactive=False,
            profile="keel-live",
            mode="confirm",
        )
        assert record_path is not None
        assert record_path.exists()
        data = json.loads(record_path.read_text())
        assert data["profile"] == "keel-live"
        assert data["mode"] == "confirm"
        assert data["port"] == 8765
        assert data["token"] == "test-token"


def test_live_peers_discovers_active_daemons(tmp_path: Path) -> None:
    """live_peers discovers all responsive peer records and sets current flag."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)

    # Write two records
    (run_dir / "serve-8765.json").write_text(
        json.dumps(
            {
                "pid": 12345,
                "host": "127.0.0.1",
                "port": 8765,
                "token": "tok-1",
                "profile": "keel-live",
                "mode": "confirm",
            }
        )
    )
    (run_dir / "serve-8766.json").write_text(
        json.dumps(
            {
                "pid": 12346,
                "host": "127.0.0.1",
                "port": 8766,
                "token": "tok-2",
                "profile": "keel",
                "mode": "paper",
            }
        )
    )

    with (
        patch("keel.web.runtime.state_root", return_value=tmp_path),
        patch("keel.web.runtime.process_alive", return_value=True),
        patch("keel.web.runtime._port_answers", return_value=True),
    ):
        peers = runtime.live_peers(current_port=8765)
        assert len(peers) == 2
        assert peers[0]["port"] == "8765"
        assert peers[0]["profile"] == "keel-live"
        assert peers[0]["mode"] == "confirm"
        assert peers[0]["current"] is True
        assert "tok-1" in peers[0]["url"]

        assert peers[1]["port"] == "8766"
        assert peers[1]["profile"] == "keel"
        assert peers[1]["mode"] == "paper"
        assert peers[1]["current"] is False
        assert "tok-2" in peers[1]["url"]


def test_live_peers_filters_unresponsive_or_dead_records(tmp_path: Path) -> None:
    """Dead processes or unreachable ports are excluded from peer discovery."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)

    (run_dir / "serve-8765.json").write_text(
        json.dumps(
            {
                "pid": 12345,
                "host": "127.0.0.1",
                "port": 8765,
                "token": "tok-1",
                "profile": "keel-live",
            }
        )
    )
    (run_dir / "serve-8766.json").write_text(
        json.dumps(
            {
                "pid": 99999,
                "host": "127.0.0.1",
                "port": 8766,
                "token": "tok-2",
                "profile": "keel-dead",
            }
        )
    )

    def mock_alive(pid: int) -> bool:
        return pid == 12345

    with (
        patch("keel.web.runtime.state_root", return_value=tmp_path),
        patch("keel.web.runtime.process_alive", side_effect=mock_alive),
        patch("keel.web.runtime._port_answers", return_value=True),
    ):
        peers = runtime.live_peers(current_port=8765)
        assert len(peers) == 1
        assert peers[0]["port"] == "8765"
        assert peers[0]["profile"] == "keel-live"


def test_config_payload_serializes_peers_strictly() -> None:
    """Rule 1 and Rule 3 hold on peers list: no json numbers, pure strings and bools."""
    peers = [
        {
            "port": "8765",
            "profile": "keel-live",
            "mode": "confirm",
            "url": "http://127.0.0.1:8765/?token=abc",
            "current": True,
        }
    ]
    data = payload.config_payload(
        None,
        describe="test",
        mode="confirm",
        profile="keel-live",
        peers=peers,
    )
    assert data["peers"] == peers
    assert isinstance(data["peers"][0]["port"], str)
    assert isinstance(data["peers"][0]["current"], bool)


def test_render_js_contains_profile_select_wiring() -> None:
    """render.js generates select.profile-select when peers > 1 and main.js listens to change."""
    render_src = _source("render.js")
    assert "profile-select" in render_src
    assert "config.peers" in render_src

    main_src = _source("main.js")
    assert "sessionProfileNode.addEventListener" in main_src
    assert '"change"' in main_src
