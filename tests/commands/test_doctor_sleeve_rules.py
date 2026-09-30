"""`doctor.sleeve_rule_findings` -- spec §6's failure modes (a) and (b) for `reverse_dca` (#857,
plan P10 Task 10.2).

(a) `sleeve.buy_and_sell_same_asset`: a `dca` that buys and a `reverse_dca` that sells the same
product -- legal, and a round trip of two taker legs. (b) `sleeve.price_floor_stale`: a
`min_price_floor` under half the latest close protects nothing.

Always exactly two findings, OK when there is nothing to report (PR #888): the exact-set test in
`test_doctor.py` would otherwise lose both names on a deployment with no sleeve rules.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from keel.commands import doctor
from keel.commands.doctor import OK, WARN, sleeve_rule_findings
from keel.config import load_config
from keel.types import Candle, Granularity
from tests.commands.test_doctor import NOW, _seeded_repo

NAMES = ["sleeve.buy_and_sell_same_asset", "sleeve.price_floor_stale"]


def _by_name(findings) -> dict:
    assert [f.name for f in findings] == NAMES, "exactly the two findings, in order"
    return {f.name: f for f in findings}


def test_a_live_dca_beside_a_live_or_paper_reverse_dca_is_named() -> None:
    rules = [
        {"id": 6, "kind": "dca", "status": "live", "params": {"product_id": "BTC-USD"}},
        {
            "id": 20,
            "kind": "reverse_dca",
            "status": "paper",
            "params": {"product_id": "BTC-USD", "min_price_floor": "60000"},
        },
    ]
    found = _by_name(sleeve_rule_findings(rules, {"BTC-USD": Decimal("100000")}))
    same = found["sleeve.buy_and_sell_same_asset"]
    assert same.status == WARN
    assert same.products == ("BTC-USD",)
    assert same.detail == (
        "BTC-USD: each unit bought and later distributed is a round trip of two taker legs at "
        "the venue's fee; the pipeline refuses only the days both cadences share"
    ), "no hardcoded live rate (spec §2.2, Q5)"
    assert found["sleeve.price_floor_stale"].status == OK


def test_a_floor_below_half_the_close_protects_nothing() -> None:
    rules = [
        {
            "id": 20,
            "kind": "reverse_dca",
            "status": "live",
            "params": {"product_id": "BTC-USD", "min_price_floor": "49999"},
        }
    ]
    found = _by_name(sleeve_rule_findings(rules, {"BTC-USD": Decimal("100000")}))
    assert found["sleeve.price_floor_stale"].status == WARN
    assert found["sleeve.price_floor_stale"].products == ("BTC-USD",)
    assert found["sleeve.buy_and_sell_same_asset"].status == OK


def test_a_floor_at_exactly_half_the_close_still_protects() -> None:
    """Spec §6 (b): WARN when `min_price_floor < 0.5 * close` -- strictly under."""
    rules = [
        {
            "id": 20,
            "kind": "reverse_dca",
            "status": "live",
            "params": {"product_id": "BTC-USD", "min_price_floor": "50000"},
        }
    ]
    found = _by_name(sleeve_rule_findings(rules, {"BTC-USD": Decimal("100000")}))
    assert found["sleeve.price_floor_stale"].status == OK


def test_a_floor_with_no_close_is_not_judged() -> None:
    rules = [
        {
            "id": 20,
            "kind": "reverse_dca",
            "status": "live",
            "params": {"product_id": "BTC-USD", "min_price_floor": "1"},
        }
    ]
    found = _by_name(sleeve_rule_findings(rules, {}))
    assert found["sleeve.price_floor_stale"].status == OK


def test_no_sleeve_rules_at_all_still_reports_both_findings_ok() -> None:
    found = _by_name(sleeve_rule_findings([], {}))
    assert found["sleeve.buy_and_sell_same_asset"].status == OK
    assert found["sleeve.price_floor_stale"].status == OK
    assert all(f.products == () for f in found.values())


@pytest.mark.parametrize(
    ("dca_status", "sell_status", "product", "warns"),
    [
        ("live", "live", "BTC-USD", True),
        ("paper", "live", "BTC-USD", False),  # a paper dca buys nothing on a live profile
        ("candidate", "paper", "BTC-USD", False),
        ("live", "candidate", "BTC-USD", False),  # a candidate seller is never asked
        ("live", "disabled", "BTC-USD", False),
        ("live", "live", "ETH-USD", False),  # different products
    ],
)
def test_only_a_buyer_and_a_seller_the_cycle_runs_collide(
    dca_status: str, sell_status: str, product: str, warns: bool
) -> None:
    rules = [
        {"id": 6, "kind": "dca", "status": dca_status, "params": {"product_id": "BTC-USD"}},
        {
            "id": 20,
            "kind": "reverse_dca",
            "status": sell_status,
            "params": {"product_id": product, "min_price_floor": "60000"},
        },
    ]
    found = _by_name(sleeve_rule_findings(rules, {}))
    assert (found["sleeve.buy_and_sell_same_asset"].status == WARN) is warns


def test_a_paper_profile_reads_its_paper_rules() -> None:
    """A paper cycle runs `paper` dca and `paper` sleeve rules only (`agent._sleeve_rules`)."""
    rules = [
        {"id": 6, "kind": "dca", "status": "paper", "params": {"product_id": "BTC-USD"}},
        {
            "id": 20,
            "kind": "reverse_dca",
            "status": "paper",
            "params": {"product_id": "BTC-USD", "min_price_floor": "1"},
        },
        {
            "id": 21,
            "kind": "reverse_dca",
            "status": "live",
            "params": {"product_id": "ETH-USD", "min_price_floor": "1"},
        },
    ]
    closes = {"BTC-USD": Decimal("100000"), "ETH-USD": Decimal("4000")}
    found = _by_name(sleeve_rule_findings(rules, closes, managed_status="paper"))
    assert found["sleeve.buy_and_sell_same_asset"].products == ("BTC-USD",)
    assert found["sleeve.price_floor_stale"].products == ("BTC-USD",)


# -- wired into gather_findings ------------------------------------------------------------------


def test_gather_findings_reads_the_rules_and_the_latest_daily_close(
    tmp_path: Path, valid_config_path: Path
) -> None:
    repo = _seeded_repo(tmp_path / "keel.db")
    loaded = load_config(valid_config_path)
    config = replace(loaded, auto_trade=replace(loaded.auto_trade, mode="live"))
    repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="live")
    repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "100", "min_price_floor": "40000"},
        status="live",
    )
    old, new = Decimal("30000"), Decimal("100000")
    repo.upsert_candles(
        "BTC-USD",
        Granularity.ONE_DAY,
        [
            Candle(ts=NOW - 2 * 86_400, open=old, high=old, low=old, close=old, volume=Decimal(1)),
            Candle(ts=NOW - 86_400, open=new, high=new, low=new, close=new, volume=Decimal(1)),
        ],
    )
    conn = repo._conn  # noqa: SLF001 -- total_changes IS the read-only proof
    before = conn.total_changes

    findings = doctor.gather_findings(repo, config, [], NOW)

    assert conn.total_changes == before, "gather_findings wrote to the database"
    found = {f.name: f for f in findings}
    assert found["sleeve.buy_and_sell_same_asset"].status == WARN
    assert found["sleeve.buy_and_sell_same_asset"].products == ("BTC-USD",)
    # 40000 < 0.5 x the LATEST close (100000); against the older close (30000) it would be OK.
    assert found["sleeve.price_floor_stale"].status == WARN
    assert found["sleeve.price_floor_stale"].products == ("BTC-USD",)


# -- sleeve.exit_watch (#857, plan P15 Task 15.3; spec §7 "Doctor: the state is rendered") -------


def test_exit_watch_findings_warn_on_near_and_breached_only() -> None:
    [warn] = doctor.exit_watch_findings(
        {"PAXG-USD": {"level": "near", "close": "4300", "dd_level": "3055"}}
    )
    assert (warn.name, warn.status, warn.products) == ("sleeve.exit_watch", WARN, ("PAXG-USD",))
    [breached] = doctor.exit_watch_findings(
        {"PAXG-USD": {"level": "breached", "close": "2800", "dd_level": "3055"}}
    )
    assert (breached.status, breached.products) == (WARN, ("PAXG-USD",))
    [ok] = doctor.exit_watch_findings({"BTC-USD": {"level": "insufficient_history"}})
    assert (ok.status, ok.products) == (OK, ())
    assert ok.headline == doctor.EXIT_WATCH_NOT_JUDGED.format(product="BTC-USD")
    [clear] = doctor.exit_watch_findings({"BTC-USD": {"level": "clear"}})
    assert (clear.status, clear.products) == (OK, ())


def test_one_exit_watch_finding_per_record_in_product_order() -> None:
    findings = doctor.exit_watch_findings(
        {"PAXG-USD": {"level": "breached"}, "BTC-USD": {"level": "clear"}}
    )
    assert [(f.name, f.status) for f in findings] == [
        ("sleeve.exit_watch", OK),
        ("sleeve.exit_watch", WARN),
    ]
    assert findings[1].products == ("PAXG-USD",)


def test_no_exit_watch_records_still_reports_the_finding_ok() -> None:
    """A deployment with nothing watched yet -- the exact shape
    `test_gather_findings_covers_every_check_over_a_seeded_db` runs over -- still lists the name
    (PR #888), as P3's `ledger.drift` does for its own empty case."""
    [ok] = doctor.exit_watch_findings({})
    assert (ok.name, ok.status, ok.products) == ("sleeve.exit_watch", OK, ())


def test_gather_findings_renders_the_cycles_exit_watch_records(
    tmp_path: Path, valid_config_path: Path
) -> None:
    """Doctor reads the `sleeve_exit:` records the cycle wrote -- and skips one the cycle
    CLEARED (a product no longer watched) -- and writes nothing."""
    repo = _seeded_repo(tmp_path / "keel.db")
    config = load_config(valid_config_path)
    repo.set_state(
        "sleeve_exit:PAXG-USD",
        {"level": "breached", "ts": NOW - 86_400, "close": "2800", "dd_level": "3055"},
    )
    repo.set_state("sleeve_exit:ETH-USD", None)
    conn = repo._conn  # noqa: SLF001 -- total_changes IS the read-only proof
    before = conn.total_changes

    findings = doctor.gather_findings(repo, config, [], NOW)

    assert conn.total_changes == before, "gather_findings wrote to the database"
    watch = [f for f in findings if f.name == "sleeve.exit_watch"]
    assert [(f.status, f.products) for f in watch] == [(WARN, ("PAXG-USD",))]


@pytest.mark.parametrize("record", [{}, {"level": None}, {"level": "flat"}])
def test_an_unreadable_exit_watch_record_warns_rather_than_reading_clear(record) -> None:
    """Review round 1 (held, fixed): a record with no recognisable level is not `clear` -- it
    fails toward telling the operator, WARN, naming the product."""
    [finding] = doctor.exit_watch_findings({"PAXG-USD": record})
    assert (finding.status, finding.products) == (WARN, ("PAXG-USD",))
    assert finding.headline == doctor.EXIT_WATCH_UNREADABLE.format(product="PAXG-USD")


@pytest.mark.parametrize(
    "record",
    ["breached", ["near"], {"level": "near", "ts": 1_759_000_000_000_000}],
)
def test_an_unreadable_exit_record_is_one_warn_not_a_crash(record) -> None:
    """#942: `keel dca exit --preview`'s SKIPPED line points here, so doctor must not crash on
    the record the preview skipped. A record that is not a mapping, or whose fields cannot be
    rendered, is ONE WARN naming its product -- and the other products are still judged."""
    findings = doctor.exit_watch_findings({"BTC-USD": {"level": "clear"}, "PAXG-USD": record})
    assert [(f.name, f.status, f.products) for f in findings] == [
        ("sleeve.exit_watch", OK, ()),
        ("sleeve.exit_watch", WARN, ("PAXG-USD",)),
    ]
    assert findings[1].headline == doctor.EXIT_WATCH_UNREADABLE.format(product="PAXG-USD")
