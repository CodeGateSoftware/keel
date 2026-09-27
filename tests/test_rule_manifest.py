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
    assert len(dca) == 1, "the live DCA rule is missing from the manifest"

    # This used to pin the rule's budget at "25" with the rationale "must not revert to the
    # default 50", then (still before #840) switched to an AGREEMENT assertion that the
    # manifest's budget must equal `config.dca.budget_usd`. Both versions were reasoning about a
    # world where the live executor sized DCA from `config.dca.budget_usd`
    # (`execution/executor.py::_build_intent`) and ignored the RULE's `budget_usd` entirely --
    # so the rule row could say 25, the config could say 50, and 50 is what moved live while the
    # account simulator, which read the rule's value, modeled a position size live never took.
    # AGREEMENT closed that gap by requiring the manifest and the config to match.
    #
    # #840 reverses which value is real: the live executor now spends the RULE's own `budget_usd`
    # (the `size_usd` its setup carries -- see `_dca_budget`), and `config.dca.budget_usd` is only
    # the FALLBACK for a setup whose `size_usd` is absent. The rule row is now the number that
    # actually moves, live and in the sim alike, and it is MEANT to be free to diverge from the
    # config -- letting an operator tune one rule's budget away from the shared config value is
    # exactly what #840 made possible. Requiring the manifest to agree with the config would break
    # the moment that divergence is actually used, so this test no longer asserts that agreement.
    # Right now `deploy/live-rules.json` defines a single DCA rule, at $50 -- matching this
    # config's value -- but that coincidence is what assertion (2) below pins down explicitly,
    # not a fact this test takes for granted or asserts on its own.
    #
    # What is still true, and still worth guarding: `deploy/live-rules.json`'s DCA params are,
    # right now, EXACTLY `Dca.__init__`'s constructor defaults (see `keel/strategy/rules/dca.py`).
    # That means the VALUE alone cannot prove this rule wasn't reseeded by a fresh `keel init`
    # rather than deliberately provisioned, since the operator's intended value and `keel init`'s
    # default are, today, the same number. Two assertions below make up for that: (1) status,
    # which is the only thing that still can, and (2) a pinned check on the coincidence itself,
    # so that if the operator ever moves the budget off the default, the value check regains its
    # own power and (2) is what tells them so.
    #
    # SCOPE: this asserts the COMMITTED FILE, not a deployment's database, so it catches a
    # reseeded box's state being COMMITTED -- not the reseed itself. `rule_manifest.py apply`
    # is what reports that drift against a live DB.

    # (1) NOT SEED-SHAPED -- status is the only discriminator standing between "the operator's
    # 50" and "keel init's 50" now that the manifest's budget is not checked against anything
    # else. `keel init` always seeds fresh rules at `candidate` (`docs/RELEASING.md`), and
    # nothing in this test path promotes them, so a reseeded box's manifest would show
    # `candidate` even though its budget_usd matches the constructor default byte-for-byte. A
    # correctly-provisioned deployment has every rule at `live`.
    assert dca[0]["status"] == "live", (
        "the live DCA rule is not status=live -- if this is a candidate, keel init likely "
        "reseeded it from Dca's constructor defaults rather than preserving an operator's tuned "
        "value, and nothing else here can catch that on its own (see comment)"
    )
    assert all(r["status"] != "candidate" for r in rebuilt), (
        "a rule in the committed live manifest is status=candidate -- that shape matches a fresh "
        "`keel init` reseed from constructor defaults, not a deliberately-provisioned deployment"
    )

    # (2) THE COINCIDENCE IS PINNED, NOT ASSUMED -- assertion (1) only carries the weight it does
    # because the operator's intended DCA params happen, today, to equal Dca's constructor
    # defaults. Pin that equality explicitly instead of taking it on faith. If it ever stops
    # being true -- e.g. the operator deliberately moves the budget off 50 -- THIS assertion is
    # what fails, and failing here is good news dressed as a test failure: it means the
    # manifest's value would now visibly differ from a reseed's, so the VALUE alone regains the
    # power to catch a `keel init` revert, and the status check in (1) is no longer the last
    # line of defense. Whoever hits this failure should update this comment, not just delete the
    # assertion.
    manifest_params = dca[0]["params"]
    default_params = Dca(product_id=manifest_params["product_id"]).describe()["params"]
    for key, expected in default_params.items():
        if key == "product_id":
            continue  # trivially equal -- it's the constructor arg we just passed in
        assert Decimal(str(manifest_params[key])) == Decimal(str(expected)), (
            f"deploy/live-rules.json's DCA {key} ({manifest_params[key]!r}) no longer matches "
            f"Dca's constructor default ({expected!r}). Assertion (1) above still holds, but the "
            "reasoning behind it -- that status is the ONLY thing distinguishing a deliberate "
            "value from a reseeded default -- no longer applies to this parameter: a reseed "
            "would now produce a visibly different value here, and THIS comparison catches that "
            "on its own, without needing assertion (1)'s status check as the last line of "
            "defense."
        )
