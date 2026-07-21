"""`scripts/migration_smoke.py` -- the default job of the manual migrate workflow.

Running it here means CI green implies the workflow's no-target path is green too.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.migration_smoke import main  # noqa: E402


def test_the_migration_chain_reaches_head(capsys):
    main()  # raises AssertionError if a fresh or downgraded DB stops short of SCHEMA_VERSION
    assert "migration smoke test OK" in capsys.readouterr().out
