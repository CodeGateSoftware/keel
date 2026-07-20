"""Assemble the CSCV (T x N) matrix from ledger trials (spec §5.4).

Algorithm 2.3's two conditions on the matrix (§78.6): it must be a TRUE matrix -- same rows
for every column, observations synchronous across trials -- and the metric must be estimable
on subsamples of each column. The first is enforced here; the second is `cscv.py`'s problem.

Backfilled rows (`series_missing`) are refused by construction: §78.4's warning already came
true for them -- the sweeps that produced them destroyed the per-bar series needed to score
them. They still count toward M (spec §4.6); they just cannot be columns.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from keel.research.ledger import TrialRecord

#: §78.6: "if the investor is sensitive to values of phi < 1/10, it is clear that the range of
#: values that the logits can adopt must be greater than 10, and so N >> 10 is required."
MIN_RECOMMENDED_COLUMNS = 10


@dataclass(frozen=True)
class MatrixBuild:
    columns: list[list[Decimal]]
    trial_ids: list[str]
    refused: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_matrix(trials: Iterable[TrialRecord], session: str | None = None) -> MatrixBuild:
    columns: list[list[Decimal]] = []
    trial_ids: list[str] = []
    refused: list[str] = []

    for trial in trials:
        if session is not None and trial.session != session:
            continue
        if trial.series_missing or not trial.per_bar_pnl:
            refused.append(trial.trial_id)
            continue
        columns.append(list(trial.per_bar_pnl))
        trial_ids.append(trial.trial_id)

    lengths = {len(column) for column in columns}
    if len(lengths) > 1:
        raise ValueError(
            f"columns are not synchronous: found lengths {sorted(lengths)}; §78.6 requires a "
            "true matrix with the same rows for every column"
        )

    warnings: list[str] = []
    if 0 < len(columns) < MIN_RECOMMENDED_COLUMNS:
        warnings.append(
            f"N={len(columns)} columns is below the recommended N >> {MIN_RECOMMENDED_COLUMNS}; "
            "the relative rank will be coarse and f(lambda) discontinuous (§78.6)"
        )
    return MatrixBuild(columns=columns, trial_ids=trial_ids, refused=refused, warnings=warnings)
