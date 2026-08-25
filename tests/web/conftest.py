"""Fixtures shared by the modules that drive a real `keel serve` (#435, #536).

`deployment` and `running` live here rather than in `test_server.py` because two modules now need
them: `test_server.py`, which pins the wire's security properties, and `test_client_assets.py`,
which pins that the client shell and its assets are actually served. They are that module's
original fixtures, moved unchanged.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from keel.data.db import connect, migrate
from keel.web import server as web_server
from keel.web.security import new_session_token
from tests.conftest import VALID_CONFIG_YAML


@pytest.fixture
def deployment(tmp_path: Path) -> tuple[str, str]:
    db_path = tmp_path / "keel.db"
    conn = connect(str(db_path))
    migrate(conn)
    conn.close()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG_YAML)
    return str(db_path), str(config_path)


@pytest.fixture
def running(deployment: tuple[str, str]) -> Iterator[web_server.ServeConfig]:
    db_path, config_path = deployment
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=0,
        token=new_session_token(),
        db_path=db_path,
        config_path=config_path,
    )
    server = web_server.build_server(cfg)
    bound = web_server.ServeConfig(
        host=cfg.host,
        port=int(server.server_address[1]),
        token=cfg.token,
        db_path=db_path,
        config_path=config_path,
    )
    server.RequestHandlerClass.cfg = bound  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bound
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
