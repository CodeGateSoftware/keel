"""#811: is anything still watching a held tranche? Pure findings over plain rows."""

from __future__ import annotations

from decimal import Decimal

from keel.commands.doctor import OK, WARN, position_watch_findings

#: PAXG tranche 3 exactly as #811 printed it from the live database.
PAXG_TRANCHE_3 = {
    "id": 3,
    "product_id": "PAXG-USD",
    "rule_name": "turtle_breakout",
    "rule_id": None,
    "opened_at": 1_756_128_000,
    "qty": Decimal("0.01320427494019137563114227965"),
    "entry_fill": Decimal("4673.23"),
    "initial_stop": Decimal("4521.76390215979454"),
    "bracket_order_id": None,
    "status": "open",
}
BTC_DCA = {
    "id": 1,
    "product_id": "BTC-USD",
    "rule_name": "dca",
    "rule_id": None,
    "opened_at": 1_755_000_000,
    "qty": Decimal("0.0005"),
    "entry_fill": Decimal("100000"),
    "initial_stop": None,
    "bracket_order_id": None,
    "status": "open",
}
LIVE_BTC_DCA_RULE = {"id": 6, "kind": "dca", "status": "live", "params": {"product_id": "BTC-USD"}}
#: rule 3 exactly as #811 found it: demoted from `live` to `paper` on 2026-09-15, its tranche
#: still open. This is the row `position.unmanaged` must resolve PAXG-USD's status against.
PAXG_PAPER_TURTLE_RULE = {
    "id": 3,
    "kind": "turtle_breakout",
    "status": "paper",
    "params": {"product_id": "PAXG-USD"},
}


def _by_name(findings):
    return {f.name: f for f in findings}


def test_the_live_database_shape_reports_paxg_under_both_findings() -> None:
    found = _by_name(
        position_watch_findings(
            [BTC_DCA, PAXG_TRANCHE_3],
            [LIVE_BTC_DCA_RULE, PAXG_PAPER_TURTLE_RULE],
            lambda p: False,
            set(),
        )
    )
    assert found["position.unmanaged"].status == WARN
    assert found["position.unmanaged"].products == ("PAXG-USD",)
    assert "tranche 3" in found["position.unmanaged"].detail
    # #811's first acceptance bullet: the WARN names the owning rule's CURRENT status, not
    # just its name -- rule 3 was demoted `live` -> `paper`, and that demotion is the whole
    # reason PAXG-USD stopped being watched.
    assert "paper" in found["position.unmanaged"].detail
    assert found["position.unprotected"].status == WARN
    assert found["position.unprotected"].products == ("PAXG-USD",)
    assert "4521.76" in found["position.unprotected"].detail


def test_a_dca_tranche_produces_neither() -> None:
    found = _by_name(
        position_watch_findings([BTC_DCA], [LIVE_BTC_DCA_RULE], lambda p: False, set())
    )
    assert found["position.unmanaged"].status == OK
    assert found["position.unprotected"].status == OK


def test_a_resting_bracket_or_a_retry_record_clears_unprotected() -> None:
    with_bracket = position_watch_findings([PAXG_TRANCHE_3], [], lambda p: True, set())
    with_retry = position_watch_findings([PAXG_TRANCHE_3], [], lambda p: False, {"PAXG-USD"})
    assert _by_name(with_bracket)["position.unprotected"].status == OK
    assert _by_name(with_retry)["position.unprotected"].status == OK


def test_unmanaged_matches_on_product_not_on_rule_id() -> None:
    """`positions.rule_id` is NULL on everything before #803, so ownership cannot be the key."""
    live_turtle = {
        "id": 3,
        "kind": "turtle_breakout",
        "status": "live",
        "params": {"product_id": "PAXG-USD"},
    }
    found = _by_name(
        position_watch_findings([PAXG_TRANCHE_3], [live_turtle], lambda p: True, set())
    )
    assert found["position.unmanaged"].status == OK


def test_a_demoted_dca_rule_surfaces_its_tranches_as_unmanaged() -> None:
    paper_dca = {"id": 9, "kind": "dca", "status": "paper", "params": {"product_id": "BTC-USD"}}
    found = _by_name(position_watch_findings([BTC_DCA], [paper_dca], lambda p: False, set()))
    assert found["position.unmanaged"].status == WARN
    assert "paper" in found["position.unmanaged"].detail


def test_a_product_with_no_rule_at_all_names_that_in_the_status() -> None:
    found = _by_name(position_watch_findings([PAXG_TRANCHE_3], [], lambda p: True, set()))
    assert found["position.unmanaged"].status == WARN
    assert "no rule" in found["position.unmanaged"].detail


def test_unprotected_is_skipped_on_a_paper_profile() -> None:
    """#881, 4th finding: a paper fill never has a bracket to rest, so `resting` is always False
    and there is never a retry record -- the exact same tranche this module WARNs on for a live
    profile is normal on a paper one, every cycle. `managed_status="paper"` is how the caller
    tells this function it is looking at a paper profile (`doctor.py`'s own wiring derives it
    from `config.auto_trade.mode`), so the same signal that already fixes `position.unmanaged`
    also has to silence `position.unprotected`."""
    found = _by_name(
        position_watch_findings(
            [PAXG_TRANCHE_3],
            [PAXG_PAPER_TURTLE_RULE],
            lambda p: False,
            set(),
            managed_status="paper",
        )
    )
    assert found["position.unprotected"].status == OK
    assert found["position.unprotected"].products == ()


def test_unprotected_still_warns_on_a_live_profile_with_the_same_shape() -> None:
    """Control for the test above: the paper skip must not silence the live-profile WARN this
    finding exists for in the first place (#811's PAXG tranche 3 fixture)."""
    found = _by_name(
        position_watch_findings([PAXG_TRANCHE_3], [PAXG_PAPER_TURTLE_RULE], lambda p: False, set())
    )
    assert found["position.unprotected"].status == WARN


def test_it_always_returns_exactly_the_two_findings_in_order() -> None:
    """Shape, not prose: a caller keys on these names, and an OK pair is still a pair."""
    for rows in ([], [BTC_DCA], [BTC_DCA, PAXG_TRANCHE_3]):
        findings = position_watch_findings(rows, [LIVE_BTC_DCA_RULE], lambda p: False, set())
        assert [f.name for f in findings] == ["position.unmanaged", "position.unprotected"]


def test_ok_findings_carry_no_products() -> None:
    """#642: an OK finding has nothing for a wrapper to gate a per-product decision on."""
    findings = position_watch_findings([BTC_DCA], [LIVE_BTC_DCA_RULE], lambda p: False, set())
    assert [f.products for f in findings] == [(), ()]


def test_the_status_named_is_the_most_recent_rule_for_the_product() -> None:
    """Two rules have named PAXG-USD over time; the WARN names the newer one's status."""
    older = {
        "id": 2,
        "kind": "turtle_breakout",
        "status": "disabled",
        "params": {"product_id": "PAXG-USD"},
    }
    found = _by_name(
        position_watch_findings(
            [PAXG_TRANCHE_3], [PAXG_PAPER_TURTLE_RULE, older], lambda p: True, set()
        )
    )
    assert found["position.unmanaged"].status == WARN
    assert "paper" in found["position.unmanaged"].detail
    assert "disabled" not in found["position.unmanaged"].detail


def test_a_paper_rule_manages_its_product_on_a_paper_profile() -> None:
    """#881: a paper engine promotes to `status="paper"`, never `"live"`."""
    found = _by_name(
        position_watch_findings(
            [PAXG_TRANCHE_3],
            [PAXG_PAPER_TURTLE_RULE],
            lambda p: False,
            set(),
            managed_status="paper",
        )
    )
    assert found["position.unmanaged"].status == OK
    assert found["position.unmanaged"].products == ()


#: #897: a LIVE dca rule on PAXG-USD, alongside the paper turtle rule that actually owns the
#: tranche. `agent._handle_exits` resolves the owning rule by `r.name == position["rule_name"]`
#: among the product's live rules -- a dca rule's `name` is `"dca"`, never `"turtle_breakout"`,
#: and its `exit_signal` is always False, so this rule cannot close PAXG tranche 3 no matter its
#: status.
LIVE_PAXG_DCA_RULE = {
    "id": 10,
    "kind": "dca",
    "status": "live",
    "params": {"product_id": "PAXG-USD"},
}


def test_unmanaged_matches_on_kind_not_just_product() -> None:
    """#897: a live dca rule on the same product must not mark a turtle_breakout tranche as
    managed -- membership requires the rule's `kind` to match the tranche's `rule_name` too."""
    found = _by_name(
        position_watch_findings(
            [PAXG_TRANCHE_3],
            [LIVE_PAXG_DCA_RULE, PAXG_PAPER_TURTLE_RULE],
            lambda p: False,
            set(),
        )
    )
    assert found["position.unmanaged"].status == WARN
    assert found["position.unmanaged"].products == ("PAXG-USD",)
    # the status named is the turtle rule's (paper), not the unrelated live dca rule's.
    assert "(turtle_breakout, paper," in found["position.unmanaged"].detail
    assert "(turtle_breakout, live," not in found["position.unmanaged"].detail


def test_unprotected_detail_matches_on_kind_not_just_product() -> None:
    """Same mixed shape as above: `position.unprotected`'s detail text must resolve status by
    (product, kind) too, even though its membership test is unrelated to rule ownership."""
    found = _by_name(
        position_watch_findings(
            [PAXG_TRANCHE_3],
            [LIVE_PAXG_DCA_RULE, PAXG_PAPER_TURTLE_RULE],
            lambda p: False,
            set(),
        )
    )
    assert found["position.unprotected"].status == WARN
    assert "(turtle_breakout, paper," in found["position.unprotected"].detail
    assert "(turtle_breakout, live," not in found["position.unprotected"].detail


def test_headline_and_detail_name_the_managed_status_not_literal_live() -> None:
    """#897 suggestion: on a paper profile the wording must say 'paper', not hardcode 'live' --
    membership is already decided by `managed_status`, the prose should agree."""
    found = _by_name(
        position_watch_findings([PAXG_TRANCHE_3], [], lambda p: True, set(), managed_status="paper")
    )
    assert found["position.unmanaged"].status == WARN
    assert "no paper rule" in found["position.unmanaged"].headline


def test_the_resting_predicate_is_asked_about_each_stopped_tranche() -> None:
    """The predicate must actually be consulted, and per tranche -- a constant stand-in would
    make the bracket clause decorative."""
    asked: list[int] = []

    def resting(position):
        asked.append(position["id"])
        return False

    position_watch_findings([BTC_DCA, PAXG_TRANCHE_3], [], resting, set())
    assert asked == [3]
