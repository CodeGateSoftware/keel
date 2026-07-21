"""Prove the migration chain reaches HEAD from both a fresh and a downgraded database.

This is the default job of `.github/workflows/migrate.yml`: until a hosted database exists to
point the workflow at, the useful thing it can do on demand is verify that the migration chain
in `keel.data.db` is not broken -- a fresh DB lands on `SCHEMA_VERSION`, and a DB stamped below
HEAD is stepped all the way up to it.

Release tooling: deliberately NOT shipped in the wheel.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

from keel.data.db import SCHEMA_VERSION, connect, migrate


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT version FROM schema_version").fetchone()["version"])


def main() -> None:
    """Raise `AssertionError` if the migration chain does not reach `SCHEMA_VERSION`."""
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    try:
        conn = connect(path)

        migrate(conn)
        fresh = _version(conn)
        assert fresh == SCHEMA_VERSION, f"fresh DB stopped at schema {fresh}, want {SCHEMA_VERSION}"

        # Stamp it back to the oldest version and prove every step re-runs cleanly.
        conn.execute("UPDATE schema_version SET version = 1")
        conn.commit()
        migrate(conn)
        upgraded = _version(conn)
        assert upgraded == SCHEMA_VERSION, (
            f"downgraded DB stopped at schema {upgraded}, want {SCHEMA_VERSION}"
        )

        conn.close()
        print(f"migration smoke test OK: fresh and downgraded both reach schema {SCHEMA_VERSION}")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
