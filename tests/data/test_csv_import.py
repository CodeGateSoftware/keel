"""Tests for halal_cb.data.csv_import."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from halal_cb.data.csv_import import ImportResult, import_csv, import_dir
from halal_cb.data.db import connect, migrate
from halal_cb.data.repository import Repository

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


# -- "Transactions" export shape ---------------------------------------------


def test_import_csv_imports_new_rows(repo):
    result = import_csv(FIXTURES / "transactions_ok.csv", repo)

    assert result == ImportResult(imported=3, skipped=0, warnings=[])
    rows = repo.get_transactions()
    assert {row["coinbase_id"] for row in rows} == {"tx-001", "tx-002", "tx-003"}


def test_import_csv_is_idempotent_on_reimport(repo):
    import_csv(FIXTURES / "transactions_ok.csv", repo)

    result = import_csv(FIXTURES / "transactions_ok.csv", repo)

    assert result.imported == 0
    assert result.skipped == 3
    assert len(repo.get_transactions()) == 3


def test_import_csv_parses_comma_thousands_price_as_exact_decimal(repo):
    import_csv(FIXTURES / "transactions_ok.csv", repo)

    rows = {row["coinbase_id"]: row for row in repo.get_transactions()}
    assert rows["tx-003"]["price"] == Decimal("114194.285")
    assert rows["tx-002"]["price"] == Decimal("4001.54")


def test_import_csv_skips_malformed_row_but_imports_others(repo):
    result = import_csv(FIXTURES / "transactions_malformed.csv", repo)

    assert result.imported == 2
    assert result.skipped == 1
    assert len(result.warnings) == 1
    ids = {row["coinbase_id"] for row in repo.get_transactions()}
    assert ids == {"tx-101", "tx-103"}


def test_import_csv_malformed_row_reason_in_warning(repo):
    result = import_csv(FIXTURES / "transactions_malformed.csv", repo)

    assert any("tx-102" in w or "malformed" in w.lower() for w in result.warnings)


# -- Gain/Loss report shape ---------------------------------------------------


def test_import_csv_detects_gain_loss_shape_and_skips_without_importing(repo):
    result = import_csv(FIXTURES / "gain_loss_sample.csv", repo)

    assert result.imported == 0
    assert result.skipped == 2
    assert len(result.warnings) == 1
    assert repo.get_transactions() == []


# -- unrecognized shape --------------------------------------------------------


def test_import_csv_unknown_shape_warns_and_skips_without_raising(repo):
    result = import_csv(FIXTURES / "unknown_shape.csv", repo)

    assert result.imported == 0
    assert result.skipped > 0
    assert len(result.warnings) >= 1
    assert repo.get_transactions() == []


# -- import_dir -----------------------------------------------------------------


def test_import_dir_imports_all_csvs_in_directory(repo):
    result = import_dir(FIXTURES / "transactions_dir", repo)

    assert result.imported == 3
    assert result.skipped == 0
    ids = {row["coinbase_id"] for row in repo.get_transactions()}
    assert ids == {"dir-tx-001", "dir-tx-002", "dir-tx-003"}


def test_import_dir_is_idempotent_on_reimport(repo):
    import_dir(FIXTURES / "transactions_dir", repo)

    result = import_dir(FIXTURES / "transactions_dir", repo)

    assert result.imported == 0
    assert result.skipped == 3
