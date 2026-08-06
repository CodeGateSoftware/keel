"""`scripts/rule_manifest.py` -- the strategy library as a reviewable artifact.

The rules table is deployment state that no config file records, and `keel init` re-seeds it from
CONSTRUCTOR DEFAULTS -- so a hand-tuned rule silently reverts on a fresh box. These tests pin the
two safety properties that make rebuilding from a manifest safe to automate: an existing rule's
params are never rewritten from a file, and `live` rules are never created without an explicit
opt-in.
"""

from __future__ import annotations

import json
import re
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
    assert len(dca) == 1, "the live DCA rule is missing from the manifest"

    # This used to pin the rule's budget at "25" with the rationale "must not revert to the
    # default 50". That rationale was backwards, because the number it protected was never the
    # number being spent: the live executor sizes DCA from `config.dca.budget_usd`
    # (`execution/executor.py::_build_intent`) and ignores the RULE's `budget_usd` entirely. So
    # the rule row said 25, the config said 50, and 50 was what moved. The rule's value is read
    # only by the account simulator, which means the divergence also made the sim model a
    # position size the live path would never take.
    #
    # 50 is the intended budget, so all three now agree, and assertion (1) below checks the
    # AGREEMENT rather than a literal -- a hardcoded number here would just re-create the drift
    # it is supposed to catch, one deploy later.
    #
    # But agreement alone is now a WEAKER guard than the "25" literal it replaced, and that
    # weakness needs to be guarded explicitly rather than left as a silent regression: 50 is
    # *also* `Dca.__init__`'s constructor default (see `keel/strategy/rules/dca.py`), and
    # `deploy/live-rules.json`'s DCA params are, right now, EXACTLY those constructor defaults.
    # That means "the manifest's budget equals the config's budget" is satisfied both by a
    # correctly-provisioned box AND by a box where `keel init` silently reseeded the rule at
    # `candidate` from constructor defaults -- the exact failure mode this test used to exist to
    # catch. Values can no longer tell those two states apart. Two more assertions below make up
    # for that: (2) status, which is the only thing that still can, and (3) a pinned check on
    # the coincidence itself, so that if the operator ever moves the budget off the default, the
    # value check regains its old power and (3) is what tells them so.

    # (1) AGREEMENT -- the manifest's budget must match config.dca.budget_usd, the value the live
    # executor actually spends.
    live_config = REPO_ROOT / "config.live-sandbox.yaml"
    configured = re.search(r"^dca:\n(?:.*\n)*?  budget_usd: (\S+)$", live_config.read_text(), re.M)
    assert configured is not None, "config.live-sandbox.yaml no longer declares dca.budget_usd"
    assert Decimal(dca[0]["params"]["budget_usd"]) == Decimal(configured.group(1)), (
        "the live DCA rule's budget_usd must match config.dca.budget_usd, which is the value the "
        "executor actually spends -- if they diverge, the simulator and the live path disagree"
    )

    # (2) NOT SEED-SHAPED -- status is the only discriminator assertion (1) leaves standing
    # between "the operator's 50" and "keel init's 50". `keel init` always seeds fresh rules at
    # `candidate` (`docs/RELEASING.md`), and nothing in this test path promotes them, so a
    # reseeded box's manifest would show `candidate` even though its budget_usd matches the
    # config byte-for-byte. A correctly-provisioned deployment has every rule at `live`.
    assert dca[0]["status"] == "live", (
        "the live DCA rule is not status=live -- if this is a candidate, keel init likely "
        "reseeded it from Dca's constructor defaults rather than preserving an operator's tuned "
        "value, and assertion (1) above cannot catch that on its own (see comment)"
    )
    assert all(r["status"] != "candidate" for r in rebuilt), (
        "a rule in the committed live manifest is status=candidate -- that shape matches a fresh "
        "`keel init` reseed from constructor defaults, not a deliberately-provisioned deployment"
    )

    # (3) THE COINCIDENCE IS PINNED, NOT ASSUMED -- assertion (2) only carries the weight it does
    # because the operator's intended DCA params happen, today, to equal Dca's constructor
    # defaults. Pin that equality explicitly instead of taking it on faith. If it ever stops
    # being true -- e.g. the operator deliberately moves the budget off 50 -- THIS assertion is
    # what fails, and failing here is good news dressed as a test failure: it means assertion (1)
    # has regained the ability to catch a `keel init` revert on its own (a reseed would then
    # produce a *different* number, not a coincidentally-matching one), and the status check in
    # (2) is no longer the last line of defense. Whoever hits this failure should update this
    # comment, not just delete the assertion.
    manifest_params = dca[0]["params"]
    default_params = Dca(product_id=manifest_params["product_id"]).describe()["params"]
    for key, expected in default_params.items():
        if key == "product_id":
            continue  # trivially equal -- it's the constructor arg we just passed in
        assert Decimal(str(manifest_params[key])) == Decimal(str(expected)), (
            f"deploy/live-rules.json's DCA {key} ({manifest_params[key]!r}) no longer matches "
            f"Dca's constructor default ({expected!r}). Assertions (1)/(2) above still hold, but "
            "the reasoning behind assertion (2) -- that status is the ONLY thing distinguishing "
            "a deliberate value from a reseeded default -- no longer applies to this parameter: "
            "a reseed would now produce a visibly different value, so assertion (1) alone would "
            "catch it again."
        )
