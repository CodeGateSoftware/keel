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


# -- the export answers like every other route when it cannot (#703 review) ---------------------


def test_a_query_parameter_the_export_does_not_read_is_harmless(running: Any) -> None:  # noqa: F811
    """`?limit=` belongs to the paged JSON route. The export deliberately does not read it -- it
    carries the whole scope -- so a value that would be refused there is simply not consulted
    here, and the file still comes back."""
    status, headers, _body = _get(running, EXPORT + "?limit=abc", cookie=_session(running))

    assert status == 200
    assert headers["Content-Type"] == "text/csv; charset=utf-8"


def test_an_export_that_raises_answers_instead_of_dropping_the_connection(
    running: Any,  # noqa: F811
    monkeypatch: Any,
) -> None:
    """`respond` is what normally guarantees this server never answers a GET by raising: it turns
    an `ApiRefusal` into a 400 and anything else into a 500 envelope. The CSV branch does not go
    through it, so before this it propagated -- the client saw a reset connection with no
    response at all, and a traceback of absolute source paths reached the stderr `log_message` is
    overridden to keep quiet.

    A JSON envelope is the right answer even from a route whose success case is CSV: the failure
    is not a file, and a browser handed a truncated download learns nothing.
    """
    from keel.web import api as web_api

    def boom(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        raise RuntimeError("the log is on fire")

    monkeypatch.setattr(web_api, "export_timeline_csv", boom)

    status, headers, body = _get(running, EXPORT, cookie=_session(running))

    assert status == 500, "a failed export must still answer"
    assert headers["Content-Type"].startswith("application/json")
    assert "the log is on fire" in body


def test_head_returns_no_body(running: Any) -> None:  # noqa: F811
    """`_send`, `_send_json` and `_serve_static` all guard the write with
    `if self.command != "HEAD"`. This sender did not, and `do_HEAD` delegates to `do_GET` -- so a
    HEAD returned the headers plus the whole CSV. On a keep-alive connection those bytes are
    framed as the next response: a same-origin response desync, and a violation of RFC 9110.

    Read off a RAW SOCKET, not through `http.client`: that library knows a HEAD carries no body
    and never reads one, so it reports an empty body whether or not the server sent bytes. A test
    written through it passes against the bug -- which is what the first version of this test did.
    """
    import socket

    request = (
        f"HEAD {EXPORT} HTTP/1.1\r\n"
        f"Host: {running.host}:{running.port}\r\n"
        f"Cookie: {_session(running)}\r\n"
        "Connection: close\r\n\r\n"
    )
    with socket.create_connection((running.host, running.port), timeout=10) as sock:
        sock.sendall(request.encode("ascii"))
        raw = b""
        while chunk := sock.recv(4096):
            raw += chunk

    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"text/csv" in head, "the headers still describe the resource"
    assert body == b"", f"a HEAD must send no body; got {len(body)} bytes"


def test_the_export_carries_every_row_in_scope_not_the_pages_worth(tmp_path: Path) -> None:
    """The page is capped because it polls every 15 seconds. The export is a deliberate download
    of an audit record, and inheriting that cap made it a PARTIAL record that reads as complete
    -- 200 rows of a 5,000-event deployment, with nothing in the file saying so, handed to a tax
    preparer.

    Seeded above `MAX_TIMELINE_LIMIT`, not merely above the paged default. The first version of
    this test used `DEFAULT_TIMELINE_LIMIT + 25` = 225 rows and passed while the export was still
    capped at 2000: `export_rows` handed a huge `limit` to `gather_timeline`, which clamps it with
    `min(limit, MAX_TIMELINE_LIMIT)`. A test whose fixture sits under the real cap cannot see the
    cap -- which is the same failure this PR exists to fix, committed inside the fix.
    """
    from keel.commands.timeline import MAX_TIMELINE_LIMIT, export_rows, to_csv

    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    repo = Repository(conn)
    total = MAX_TIMELINE_LIMIT + 25
    for index in range(total):
        repo.upsert_transaction(
            {
                "coinbase_id": f"cb-{index}",
                "source": "coinbase",
                "type": "deposit",
                "asset": "USD",
                "ts": 1_800_000_000 - index * 60,
                "qty": Decimal("1"),
                "total": Decimal("1"),
                "notes": "",
            }
        )

    text = to_csv(export_rows(repo, now_ts=1_800_000_000, scope="all"))
    data_rows = list(csv.reader(io.StringIO(text)))[1:]

    assert len(data_rows) == total, (
        f"the export must carry the whole scope; got {len(data_rows)} of {total}"
    )


def test_the_export_names_the_chain_status_beside_the_hash(running: Any) -> None:  # noqa: F811
    """#721. The hash column stopped being a placeholder; a hash with no verdict beside it is a
    number a reader takes on trust, and the reading taken on trust is the flattering one."""
    _status, _headers, body = _csv(running)

    header = next(csv.reader(io.StringIO(body)))

    assert header[-2:] == ["row_hash", "chain_status"]
