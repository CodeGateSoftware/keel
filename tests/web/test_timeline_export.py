"""The CSV export's own MIME and header suite -- issue #703.

**This route is deliberately outside `test_api.py`'s `API_ROUTES` loop.** Everything in that
table answers the JSON envelope, and every pin there is parametrised over it; a `text/csv` route
inside it would force each of those to grow an exception, and an exception carved into a security
pin is how the pin stops meaning anything. So the export gets its own file, and the headers that
matter for a downloaded file get asserted here rather than assumed.

What matters for a file a browser is told to save is not the same set that matters for JSON:
`nosniff` counts for MORE (a sniffing browser would be deciding what a downloaded file IS),
`Content-Disposition` has to make it a download rather than an inline render, and the filename
has to come from the server rather than from anything a caller sent.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from tests.web.test_api import _get, _session, deployment, running  # noqa: F401

EXPORT = "/api/timeline/export.csv"


def _csv(server: Any, path: str = EXPORT) -> tuple[int, dict[str, str], str]:
    status, headers, body = _get(server, path, cookie=_session(server))
    return status, {k.lower(): v for k, v in headers.items()}, body


def test_the_export_answers_csv_and_not_json(running: Any) -> None:  # noqa: F811
    status, headers, body = _csv(running)

    assert status == 200
    assert headers["content-type"] == "text/csv; charset=utf-8"
    assert not body.lstrip().startswith("{"), "a JSON envelope would defeat the whole export"


def test_the_export_is_a_download_with_a_dated_server_chosen_name(running: Any) -> None:  # noqa: F811
    """`attachment` keeps it a saved file rather than a document rendered inline from this
    origin, and the date makes it an artefact an operator can file rather than the tenth
    `export.csv` in their downloads folder."""
    _status, headers, _body = _csv(running)

    disposition = headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert "keel-activity-" in disposition
    assert disposition.endswith('.csv"')


def test_the_export_carries_nosniff_and_no_store(running: Any) -> None:  # noqa: F811
    """`nosniff` matters more here than on the JSON routes: this body is a file a browser is
    being told to save. `no-store` because it is the operator's whole audit trail, and a copy of
    it in a shared cache is a copy nobody chose to make."""
    _status, headers, _body = _csv(running)

    assert headers["x-content-type-options"] == "nosniff"
    assert "no-store" in headers["cache-control"]


def test_the_filename_cannot_be_chosen_by_the_caller(running: Any) -> None:  # noqa: F811
    """A caller-supplied filename would put attacker-controlled text into a response header --
    the header-injection twin of the formula injection `csv_safe` defends the body against."""
    _status, headers, _body = _csv(running, EXPORT + '?filename=evil";DROP')

    assert "evil" not in headers["content-disposition"]
    assert "DROP" not in headers["content-disposition"]


def test_the_export_requires_the_same_session_as_every_other_route(running: Any) -> None:  # noqa: F811
    """An export of the whole audit trail must not be reachable more easily than the page it
    came from. Checked inside the same admission the JSON routes pass."""
    status, _headers, _body = _get(running, EXPORT, cookie=None)

    assert status in (401, 403), "an unauthenticated export must be refused"


def test_the_export_has_a_header_row_naming_provenance(running: Any) -> None:  # noqa: F811
    """The point of the file: a reader can tell a venue-reported fill from a line someone
    imported from a spreadsheet. If provenance were not a column, the export would be a list of
    events with no way to weigh any of them."""
    _status, _headers, body = _csv(running)

    header = next(csv.reader(io.StringIO(body)))

    assert "provenance" in header
    assert "source" in header
    assert "row_hash" in header


def test_a_hostile_value_that_starts_a_cell_cannot_execute(tmp_path: Path) -> None:
    """End to end, through the real exporter.

    The cell that matters is one whose WHOLE content is attacker-influenced, because a
    spreadsheet evaluates only text that BEGINS with a trigger. `coinbase_id` is exactly that:
    it comes out of a venue's CSV export and lands in `reference` by itself.

    (A hostile `notes` value is diluted by accident -- `summary` prefixes it with "deposit USD
    -- " so the cell no longer starts with `=`. That is not a defence to rely on, which is why
    `csv_safe` is applied to every cell rather than to the ones currently reachable.)
    """
    from keel.commands.timeline import gather_timeline, to_csv

    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    repo = Repository(conn)
    repo.upsert_transaction(
        {
            "coinbase_id": '=cmd|" /C calc"!A0',
            "source": "coinbase",
            "type": "deposit",
            "asset": "USD",
            "ts": 1_800_000_000 - 60,
            "qty": Decimal("1"),
            "total": Decimal("1"),
            "notes": "",
        }
    )

    text = to_csv(gather_timeline(repo, now_ts=1_800_000_000, scope="all"))
    rows = list(csv.reader(io.StringIO(text)))
    reference = rows[1][rows[0].index("reference")]

    assert "=cmd" in reference, "the evidence must survive -- this is an audit record"
    assert not reference.startswith("="), "and it must not be a formula when opened"
    assert reference.startswith("'"), "OWASP's defence: quote it into inert text"


def test_a_negative_amount_is_inert_and_still_legible(tmp_path: Path) -> None:
    """`-` is a formula trigger, and the amount column is full of real negative figures. The
    export quotes them: a spreadsheet shows the text rather than evaluating it, and the value is
    still there to read and to re-import."""
    from keel.commands.timeline import csv_safe

    assert csv_safe("-500.25") == "'-500.25"
