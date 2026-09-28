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
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from keel.data.db import connect, migrate  # noqa: E402
from keel.data.repository import Repository  # noqa: E402
from keel.strategy.rules.dca import Dca  # noqa: E402
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
    assert dca, "the live DCA rules are missing from the manifest"

    # History: this used to pin the single DCA rule's budget at "25", then an AGREEMENT with
    # `config.dca.budget_usd`, then (after #840 made the live executor spend the RULE's own
    # `budget_usd` -- see `_dca_budget`) a pinned COINCIDENCE that the one DCA rule's params equal
    # `Dca`'s constructor defaults. #855 regenerated the manifest from the live DB: seven DCA rules
    # (BTC $40/7d, ETH $25/7d, PAXG $25/14d, ADA/XLM/DOGE/FET $15/14d), none at the default $50,
    # and the turtles at `paper`. So the coincidence no longer holds, and the assertions below say
    # what IS true now.
    #
    # SCOPE: this asserts the COMMITTED FILE, not a deployment's database, so it catches a
    # reseeded box's state being COMMITTED -- not the reseed itself. `rule_manifest.py apply`
    # is what reports that drift against a live DB.

    # (1) NOT SEED-SHAPED BY STATUS -- `keel init` always seeds fresh rules at `candidate`
    # (`docs/RELEASING.md`), and nothing in this test path promotes them. A deliberately
    # provisioned deployment has every DCA rule at `live` and no rule at `candidate`.
    assert [r["status"] for r in dca] == ["live"] * len(dca), (
        "a DCA rule in the committed manifest is not status=live -- if it is a candidate, keel "
        "init likely reseeded it from Dca's constructor defaults rather than preserving an "
        "operator's tuned value"
    )
    assert all(r["status"] != "candidate" for r in rebuilt), (
        "a rule in the committed live manifest is status=candidate -- that shape matches a fresh "
        "`keel init` reseed from constructor defaults, not a deliberately-provisioned deployment"
    )

    # (2) NOT SEED-SHAPED BY VALUE -- every live DCA rule's params differ from `Dca`'s constructor
    # defaults on at least one key, so a reseed would show up as a VALUE change in this file, not
    # only as a status change. If an operator ever deliberately sets a rule back to the defaults,
    # this fails: status in (1) is then the only discriminator left for that rule. Update this
    # comment when you relax it, do not just delete the assertion.
    for rule in dca:
        params = rule["params"]
        default_params = Dca(product_id=params["product_id"]).describe()["params"]
        differing = sorted(
            key
            for key, expected in default_params.items()
            if key != "product_id" and Decimal(str(params[key])) != Decimal(str(expected))
        )
        assert differing, (
            f"deploy/live-rules.json's {params['product_id']} DCA rule equals Dca's constructor "
            "defaults on every param, so only its status distinguishes it from a `keel init` "
            "reseed (see comment)"
        )
