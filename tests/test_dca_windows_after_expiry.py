"""#1010: which live DCA windows fall after rail 17's expiry -- one helper, three commands.

Live evidence: rail 17 attested 2026-10-10T19:25Z and expired 2026-10-17T19:25Z while the
biweekly DCA rule's next window was the 2026-10-22 bar (placed 10-23). Nothing said so. These
tests pin the arithmetic that lets `attest`, `status` and `doctor` say it from one place.
"""

from __future__ import annotations

import pytest

from keel import attestations
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import executor
from keel.strategy.rules.dca import next_dca_bar_ts

ATTESTED = 1_791_660_300  # 2026-10-10T19:25Z
DAY = 86_400


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


def test_next_dca_bar_is_the_next_cadence_bar_not_yet_acted_on() -> None:
    assert next_dca_bar_ts(ATTESTED, 14) == 20_748 * DAY  # 2026-10-22 bar
    assert next_dca_bar_ts(ATTESTED, 7) == 20_741 * DAY  # 2026-10-15 bar
    # on the placement day itself, yesterday's cadence bar is still the due one
    assert next_dca_bar_ts(20_749 * DAY + 4_800, 14) == 20_748 * DAY


def test_the_live_lapse_is_reproduced_biweekly_flagged_weekly_not() -> None:
    found = attestations.dca_windows_after_expiry(
        [(6, "BTC-USD", 7), (9, "PAXG-USD", 14)], attested_at=ATTESTED, now_ts=ATTESTED
    )
    assert [w.rule_id for w in found] == [9]
    (w,) = found
    assert (w.bar_ts, w.due_ts, w.expires_at, w.reattest_from_ts) == (
        20_748 * DAY,
        20_749 * DAY,
        1_792_265_100,
        20_743 * DAY,
    )
    assert w.line() == (
        "rule 9 PAXG-USD (every 14d): next DCA buy due 2026-10-23 (bar 2026-10-22), after the "
        "withdrawal attestation expires 2026-10-17 19:25Z; rail 17 will veto it -- re-attest on "
        "or after 2026-10-17 to cover it"
    )


def test_expiry_is_read_from_the_executor_at_call_time(monkeypatch) -> None:
    monkeypatch.setattr(executor, "WITHDRAWAL_ATTESTATION_TTL_SEC", 14 * DAY)
    assert (
        attestations.dca_windows_after_expiry(
            [(9, "PAXG-USD", 14)], attested_at=ATTESTED, now_ts=ATTESTED
        )
        == ()
    )


def test_an_attestation_lasting_exactly_through_the_placement_day_is_not_reported() -> None:
    # attested_at chosen so expires_at == due_ts + DAY for the 2026-10-22 bar
    at = 20_750 * DAY - 7 * DAY
    rules = [(9, "PAXG-USD", 14)]
    assert attestations.dca_windows_after_expiry(rules, attested_at=at, now_ts=at) == ()
    assert len(attestations.dca_windows_after_expiry(rules, attested_at=at - 1, now_ts=at)) == 1


def test_live_wrapper_reads_only_live_dca_rows(repo) -> None:
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", ATTESTED)
    live14 = repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    repo.insert_rule("dca", {"product_id": "ADA-USD", "cadence_days": 14}, status="paper")
    repo.insert_rule("dca", {"product_id": "XLM-USD", "cadence_days": 14}, status="disabled")
    repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")  # default cadence 7: covered
    found = attestations.live_dca_windows_after_expiry(repo, ATTESTED)
    assert [w.rule_id for w in found] == [live14]


def test_live_wrapper_skips_a_live_row_that_will_not_build_and_reports_the_rest(repo) -> None:
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", ATTESTED)
    # `Dca.__init__` refuses cadence_days <= 0, so build_rule_from_params raises on this row
    repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 0}, status="live")
    good = repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    assert len(repo.get_rules("live")) == 2, "fixture: both rows reach the wrapper"
    found = attestations.live_dca_windows_after_expiry(repo, ATTESTED)
    assert [w.rule_id for w in found] == [good]


def test_live_wrapper_filters_on_kind_not_on_having_a_cadence(repo, monkeypatch) -> None:
    """Only `kind == "dca"` rows are DCA rules. A live row of another kind that happens to build
    a rule carrying `cadence_days` must not be reported; no shipped kind has one, so the probe
    kind is a registered `Dca` subclass."""
    from keel import agent
    from keel.strategy.rules.dca import Dca

    class _DcaLike(Dca):
        pass

    monkeypatch.setitem(agent.RULE_REGISTRY, "dca_like", _DcaLike)
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", ATTESTED)
    repo.insert_rule("dca_like", {"product_id": "BTC-USD", "cadence_days": 14}, status="live")
    real = repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    assert [r["kind"] for r in repo.get_rules("live")] == ["dca_like", "dca"]
    found = attestations.live_dca_windows_after_expiry(repo, ATTESTED)
    assert [w.rule_id for w in found] == [real]


@pytest.mark.parametrize(
    "enabled, attested_at",
    # suspended, unknown, never attested, EXPIRED
    [(False, ATTESTED), (None, ATTESTED), (True, 0), (True, ATTESTED - 8 * DAY)],
)
def test_live_wrapper_is_silent_without_an_enabled_attestation(repo, enabled, attested_at) -> None:
    # the fixture reaches the reader: with a fresh enabled attestation this same rule IS flagged
    repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    if enabled is not None:
        repo.set_state("withdrawals_enabled", enabled)
    repo.set_state("withdrawals_attested_at", attested_at)
    assert attestations.live_dca_windows_after_expiry(repo, ATTESTED) == ()


def test_the_silent_cases_are_silent_only_because_of_the_attestation(repo) -> None:
    repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", ATTESTED)
    assert len(attestations.live_dca_windows_after_expiry(repo, ATTESTED)) == 1


def test_live_wrapper_never_raises() -> None:
    class _Broken:
        def get_state(self, *a, **k):
            raise RuntimeError("locked")

        def get_rules(self, *a, **k):
            raise RuntimeError("locked")

    assert attestations.live_dca_windows_after_expiry(_Broken(), ATTESTED) == ()


# -- #1014: the placement day ----------------------------------------------------------------
# 2026-10-09 19:25Z is the day of the issue's first veto: the 01:20 cycle already acted on the
# 2026-10-08 cadence bar (20_734), and the attestation made now expires 10-16, before the
# 2026-10-22 bar's placement day (10-23).
PLACEMENT_DAY_AT = 20_735 * DAY + 69_900


def test_on_a_placement_day_the_following_bar_is_checked_when_nothing_is_known() -> None:
    found = attestations.dca_windows_after_expiry(
        [(9, "PAXG-USD", 14)], attested_at=PLACEMENT_DAY_AT, now_ts=PLACEMENT_DAY_AT
    )
    assert [(w.rule_id, w.bar_ts) for w in found] == [(9, 20_748 * DAY)]


def test_a_bar_with_a_counted_buy_is_not_the_next_due_one() -> None:
    rules = [(9, "PAXG-USD", 14)]
    at = PLACEMENT_DAY_AT
    # yesterday's bar (20_734) was bought at 01:20 today: the next due bar is 20_748
    found = attestations.dca_windows_after_expiry(
        rules, attested_at=at, now_ts=at, counted_bar_days={9: 20_734}
    )
    assert [w.bar_ts for w in found] == [20_748 * DAY]


def test_a_known_absence_of_buys_checks_only_yesterdays_bar() -> None:
    rules = [(9, "PAXG-USD", 14)]
    at = PLACEMENT_DAY_AT
    # no counted buy, attestation covers yesterday's bar (placement day 20_735): nothing to say
    assert (
        attestations.dca_windows_after_expiry(
            rules, attested_at=at, now_ts=at, counted_bar_days={9: None}
        )
        == ()
    )


def test_live_wrapper_skips_a_bar_the_rule_already_bought(repo) -> None:
    from decimal import Decimal

    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", PLACEMENT_DAY_AT)
    rid = repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")
    # before the 01:20 buy is in, yesterday's bar is the due one and the attestation covers it
    assert attestations.live_dca_windows_after_expiry(repo, PLACEMENT_DAY_AT) == ()
    placed = 20_735 * DAY + 4_800  # the 01:20 cycle's live buy, acting on bar 20_734
    repo.insert_order(
        dict(
            mode="live",
            product_id="PAXG-USD",
            side="BUY",
            order_type="market",
            qty=Decimal("1"),
            status="filled",
            fee=Decimal("0"),
            confirmation="autonomous",
            rule_id=rid,
            created_at=placed,
            updated_at=placed,
        )
    )
    found = attestations.live_dca_windows_after_expiry(repo, PLACEMENT_DAY_AT)
    assert [w.bar_ts for w in found] == [20_748 * DAY]


def test_a_counted_buy_older_than_the_due_bar_does_not_skip_it() -> None:
    rules = [(9, "PAXG-USD", 14)]
    at = PLACEMENT_DAY_AT
    # the newest counted buy is for bar 20_720, long before yesterday's bar 20_734: that bar is
    # still the due one, and the attestation covers its placement day (20_735)
    assert (
        attestations.dca_windows_after_expiry(
            rules, attested_at=at, now_ts=at, counted_bar_days={9: 20_720}
        )
        == ()
    )
    # the same mapping with the bar itself counted skips to the following bar (20_748)
    found = attestations.dca_windows_after_expiry(
        rules, attested_at=at, now_ts=at, counted_bar_days={9: 20_734}
    )
    assert [w.bar_ts for w in found] == [20_748 * DAY]


def test_an_unknown_rule_whose_yesterdays_bar_is_uncovered_gets_exactly_one_window() -> None:
    # expires 20_735 * DAY: before yesterday's bar (20_734) is placed-through (needs 20_736 * DAY),
    # and the following bar (20_748) is uncovered too -- only the first is reported
    at = 20_728 * DAY
    found = attestations.dca_windows_after_expiry(
        [(9, "PAXG-USD", 14)], attested_at=at, now_ts=PLACEMENT_DAY_AT
    )
    assert [(w.rule_id, w.bar_ts, w.due_ts) for w in found] == [(9, 20_734 * DAY, 20_735 * DAY)]


def _insert_live_buy(repo, rid: int, created_at: int) -> None:
    from decimal import Decimal

    repo.insert_order(
        dict(
            mode="live",
            product_id="BTC-USD",
            side="BUY",
            order_type="market",
            qty=Decimal("1"),
            status="filled",
            fee=Decimal("0"),
            confirmation="autonomous",
            rule_id=rid,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def test_live_wrapper_counts_a_buy_against_the_bar_before_its_own_day(repo) -> None:
    # cadence 1, buy placed on day 20_735 (acting on bar 20_734), viewed on day 20_736: the due
    # bar is 20_735, NOT yet bought, placed on 20_736. The attestation expires inside day 20_737,
    # so it covers that placement day: silent. Counting the buy's own day as its bar (20_735)
    # would mark the due bar bought and report the following one (needs expiry past 20_738).
    now = 20_736 * DAY + 69_900
    repo.set_state("withdrawals_enabled", True)
    repo.set_state(
        "withdrawals_attested_at", 20_730 * DAY + 50_000
    )  # expires 20_737 * DAY + 50_000
    rid = repo.insert_rule("dca", {"product_id": "BTC-USD", "cadence_days": 1}, status="live")
    _insert_live_buy(repo, rid, 20_735 * DAY + 4_800)
    assert repo.last_live_buy_created_at(rid) == 20_735 * DAY + 4_800, "fixture: the buy is read"
    assert attestations.live_dca_windows_after_expiry(repo, now) == ()


def test_live_wrapper_treats_a_failed_buy_read_as_unknown_and_checks_both_bars(
    repo, monkeypatch
) -> None:
    repo.set_state("withdrawals_enabled", True)
    repo.set_state("withdrawals_attested_at", PLACEMENT_DAY_AT)
    rid = repo.insert_rule("dca", {"product_id": "PAXG-USD", "cadence_days": 14}, status="live")

    def _boom(rule_id):
        raise RuntimeError("locked")

    monkeypatch.setattr(repo, "last_live_buy_created_at", _boom)
    found = attestations.live_dca_windows_after_expiry(repo, PLACEMENT_DAY_AT)
    # yesterday's bar (20_734) is covered; unknown means the following bar is checked as well
    assert [(w.rule_id, w.bar_ts) for w in found] == [(rid, 20_748 * DAY)]
