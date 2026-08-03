"""`scripts/rule_manifest.py` -- the strategy library as a reviewable artifact.

The rules table is deployment state that no config file records, and `keel init` re-seeds it from
CONSTRUCTOR DEFAULTS -- so a hand-tuned rule silently reverts on a fresh box. These tests pin the
two safety properties that make rebuilding from a manifest safe to automate: an existing rule's
params are never rewritten from a file, and `live` rules are never created without an explicit
opt-in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from keel.data.db import connect, migrate  # noqa: E402
from keel.data.repository import Repository  # noqa: E402
from scripts.rule_manifest import apply, export  # noqa: E402

DCA = {
    "kind": "dca",
    "status": "candidate",
    "params": {
        "product_id": "BTC-USD",
        "cadence_days": 7,
        "budget_usd": "25",
        "dip_bonus_pct": "0",
        "lookback_days": 90,
    },
}


def _db(tmp_path: Path, name: str = "t.db") -> str:
    path = str(tmp_path / name)
    migrate(connect(path))
    return path


def _manifest(tmp_path: Path, rules: list[dict]) -> Path:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"rules": rules}))
    return path


def _rules(db: str) -> list[dict]:
    return Repository(connect(db)).get_rules()


def test_export_then_apply_round_trips(tmp_path: Path) -> None:
    source = _db(tmp_path, "source.db")
    Repository(connect(source)).insert_rule(DCA["kind"], DCA["params"], status="candidate")

    out = tmp_path / "deploy" / "live-rules.json"
    assert export(source, out) == 1

    target = _db(tmp_path, "target.db")
    assert apply(target, out, write=True) == 0

    rebuilt = _rules(target)
    assert len(rebuilt) == 1
    assert rebuilt[0]["kind"] == "dca"
    assert rebuilt[0]["params"] == DCA["params"]
    assert rebuilt[0]["status"] == "candidate"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    manifest = _manifest(tmp_path, [DCA])

    assert apply(db, manifest) == 0
    assert _rules(db) == []


def test_apply_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    manifest = _manifest(tmp_path, [DCA])

    assert apply(db, manifest, write=True) == 0
    assert apply(db, manifest, write=True) == 0
    assert len(_rules(db)) == 1


def test_live_rules_refused_without_allow_live(tmp_path: Path) -> None:
    """Seeding straight to `live` bypasses the promotion gate, so it takes an explicit flag."""
    db = _db(tmp_path)
    manifest = _manifest(tmp_path, [{**DCA, "status": "live"}])

    assert apply(db, manifest, write=True) == 1
    assert _rules(db) == []

    assert apply(db, manifest, write=True, allow_live=True) == 0
    assert _rules(db)[0]["status"] == "live"


def test_param_drift_is_reported_and_never_rewritten(tmp_path: Path) -> None:
    """A manifest must not be able to resize a rule that already exists -- especially a live one."""
    db = _db(tmp_path)
    Repository(connect(db)).insert_rule(DCA["kind"], DCA["params"], status="live")

    fatter = {**DCA["params"], "budget_usd": "500"}
    manifest = _manifest(tmp_path, [{**DCA, "status": "live", "params": fatter}])

    assert apply(db, manifest, write=True, allow_live=True) == 1
    assert _rules(db)[0]["params"]["budget_usd"] == "25"


def test_status_drift_is_reported_not_promoted(tmp_path: Path) -> None:
    """Promotion is `keel rules promote`'s job; a file edit must not arm a candidate."""
    db = _db(tmp_path)
    Repository(connect(db)).insert_rule(DCA["kind"], DCA["params"], status="candidate")

    manifest = _manifest(tmp_path, [{**DCA, "status": "live"}])

    assert apply(db, manifest, write=True, allow_live=True) == 1
    assert _rules(db)[0]["status"] == "candidate"


def test_rules_match_on_kind_and_product(tmp_path: Path) -> None:
    """Same kind, different product is a different rule -- matching `keel rules seed`'s key."""
    db = _db(tmp_path)
    eth = {**DCA, "params": {**DCA["params"], "product_id": "ETH-USD"}}
    manifest = _manifest(tmp_path, [DCA, eth])

    assert apply(db, manifest, write=True) == 0
    assert {r["params"]["product_id"] for r in _rules(db)} == {"BTC-USD", "ETH-USD"}


def test_committed_manifest_is_valid(tmp_path: Path) -> None:
    """The checked-in manifest must actually rebuild -- a stale or malformed one is worthless."""
    committed = REPO_ROOT / "deploy" / "live-rules.json"
    assert committed.exists(), "deploy/live-rules.json is missing"

    db = _db(tmp_path)
    assert apply(db, committed, write=True, allow_live=True) == 0

    rebuilt = _rules(db)
    assert len(rebuilt) == len(json.loads(committed.read_text())["rules"])
    dca = [r for r in rebuilt if r["kind"] == "dca"]
    assert len(dca) == 1, "the live DCA rule is missing from the manifest"
    assert dca[0]["params"]["budget_usd"] == "25", "DCA budget must not revert to the default 50"
