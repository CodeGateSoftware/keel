"""`doctor` reports WHEN a recorded asset/instrument attestation window closes -- #718.

A recorded window that nothing reads is a column nobody benefits from: `keel assets attest
--attest-due` and `keel assets attest-instrument --attest-due` can now RECORD one, and this is
where an operator learns it passed or is approaching. Deliberately REPORTING ONLY: `screen_asset`
never reads `attest_due_ts` and an expired window does not veto an entry -- that would be a
change to which trades happen, out of scope for a recording issue (see `keel/compliance/
screen.py`). Both status kinds share doctor's fail-safe direction: NULL means "no window
recorded", never "expired" and never "valid forever" -- so a row with no window must never be
reported passed or approaching.
"""

from __future__ import annotations

from keel.commands.doctor import (
    OK,
    WARN,
    asset_attestation_window_findings,
    instrument_attestation_window_findings,
)

NOW = 1_800_000_000
DAY = 86_400


def _asset(asset: str = "BTC", attest_due_ts: int | None = None) -> dict:
    return {
        "asset": asset,
        "sector": "store of value",
        "backing": "native",
        "pays_yield": 0,
        "source": "s",
        "attested_by": "tester",
        "attested_at": NOW - 10 * DAY,
        "attest_due_ts": attest_due_ts,
    }


def _instrument(
    venue: str = "coinbase", product_id: str = "BTC-USD", attest_due_ts: int | None = None
) -> dict:
    return {
        "venue": venue,
        "product_id": product_id,
        "wrapper": "spot",
        "source": "s",
        "attested_by": "tester",
        "attested_at": NOW - 10 * DAY,
        "attest_due_ts": attest_due_ts,
    }


# -- asset attestations ----------------------------------------------------------------------



def test_no_attestations_is_ok():
    (finding,) = asset_attestation_window_findings([], now_ts=NOW)
    assert finding.status == OK


def test_a_null_window_is_not_reported_as_expired_or_approaching():
    """The core NULL convention: no window recorded is neither expired nor valid forever."""
    (finding,) = asset_attestation_window_findings([_asset(attest_due_ts=None)], now_ts=NOW)
    assert finding.status == OK
    assert "expired" not in finding.headline.lower()
    assert "expired" not in finding.detail.lower()


def test_a_passed_window_is_reported():
    findings = asset_attestation_window_findings(
        [_asset(attest_due_ts=NOW - 3 * DAY)], now_ts=NOW
    )
    (finding,) = [f for f in findings if f.status != OK]
    assert finding.status == WARN
    assert "BTC" in finding.detail
    assert "3 day" in finding.detail
    assert "keel assets attest" in finding.fix


def test_an_approaching_window_is_reported():
    findings = asset_attestation_window_findings(
        [_asset(attest_due_ts=NOW + 2 * DAY)], now_ts=NOW
    )
    (finding,) = [f for f in findings if f.status != OK]
    assert finding.status == WARN
    assert "BTC" in finding.detail


def test_a_window_far_in_the_future_is_ok():
    findings = asset_attestation_window_findings(
        [_asset(attest_due_ts=NOW + 365 * DAY)], now_ts=NOW
    )
    assert all(f.status == OK for f in findings)


def test_screening_never_vetoes_and_the_finding_says_so():
    """Reporting only -- the hard boundary from #718."""
    findings = asset_attestation_window_findings(
        [_asset(attest_due_ts=NOW - DAY)], now_ts=NOW
    )
    (finding,) = [f for f in findings if f.status != OK]
    assert finding.status == WARN  # never FAIL: nothing is actually blocked
    assert "does not veto" in finding.detail or "reporting only" in finding.detail.lower()


def test_multiple_assets_are_all_named():
    findings = asset_attestation_window_findings(
        [
            _asset("BTC", attest_due_ts=NOW - DAY),
            _asset("ETH", attest_due_ts=NOW - 2 * DAY),
        ],
        now_ts=NOW,
    )
    (finding,) = [f for f in findings if f.status != OK]
    assert "BTC" in finding.detail
    assert "ETH" in finding.detail


# -- instrument attestations ------------------------------------------------------------------


def test_no_instrument_attestations_is_ok():
    (finding,) = instrument_attestation_window_findings([], now_ts=NOW)
    assert finding.status == OK


def test_an_instrument_null_window_is_not_reported_as_expired():
    (finding,) = instrument_attestation_window_findings(
        [_instrument(attest_due_ts=None)], now_ts=NOW
    )
    assert finding.status == OK
    assert "expired" not in finding.headline.lower()


def test_an_instrument_passed_window_is_reported():
    findings = instrument_attestation_window_findings(
        [_instrument(attest_due_ts=NOW - 3 * DAY)], now_ts=NOW
    )
    (finding,) = [f for f in findings if f.status != OK]
    assert finding.status == WARN
    assert "coinbase" in finding.detail
    assert "BTC-USD" in finding.detail
    assert "keel assets attest-instrument" in finding.fix


def test_an_instrument_approaching_window_is_reported():
    findings = instrument_attestation_window_findings(
        [_instrument(attest_due_ts=NOW + 2 * DAY)], now_ts=NOW
    )
    (finding,) = [f for f in findings if f.status != OK]
    assert finding.status == WARN


def test_an_instrument_window_far_in_the_future_is_ok():
    findings = instrument_attestation_window_findings(
        [_instrument(attest_due_ts=NOW + 365 * DAY)], now_ts=NOW
    )
    assert all(f.status == OK for f in findings)


def test_a_window_closing_exactly_now_is_already_passed() -> None:
    """`due <= now_ts`, not `<`. A row landing exactly on the second would otherwise fall into
    neither the passed nor the approaching group -- and if it were the only dated row the check
    would answer a clean "nothing overdue or approaching". A false all-clear is the one answer a
    compliance check must never give by accident.

    Survived mutation before this test existed: flipping `<=` to `<` left the whole suite green.
    """
    (finding,) = asset_attestation_window_findings([_asset(attest_due_ts=NOW)], now_ts=NOW)

    assert finding.status != OK, "a window closing exactly now is closed, not still open"


def test_a_row_from_a_database_that_predates_the_column_is_not_an_error() -> None:
    """`keel mcp`'s `_open_readonly_repo` deliberately does NOT migrate, so this seam is reached
    with v19-shaped rows on a database nobody has upgraded. `row["attest_due_ts"]` raised
    `KeyError` there and took down the WHOLE `gather_findings` call, not just this check -- a
    reporting feature breaking a diagnostic tool on the deployments least able to spare it.

    A column that does not exist yet is the same "not recorded" a NULL is.
    """
    row = _asset()
    del row["attest_due_ts"]  # a v19-shaped row: the column does not exist yet

    (finding,) = asset_attestation_window_findings([row], now_ts=NOW)

    assert finding.status == OK

