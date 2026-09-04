"""The Research Hub's trials ledger, on the wire -- issue #708, view 1.

The service decides (`keel/commands/research_record.py`); this pins that the browser gets
the decision unaltered, and that two properties survive the crossing:

**No green badge over an unverified file.** A missing ledger, an intact chain and a broken chain
are three states, not two. `chain_intact=False` means "nothing verified" for the first and "the
file was edited" for the third, and a payload that collapsed them would show a reader a verdict
about a file nothing had read.

**No leaderboard.** `/api/research/trials` declares NO sortable column -- not "no performance
columns", none at all -- and no research column carries a sort key. The Strathern rail is
enforced at the endpoint rather than only in the renderer, because a `?sort=profit_factor` that
merely rendered no header would still order the rows for anything that asked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from keel.commands.research_record import gather_trials
from keel.web import payload as web_payload

RESEARCH_NOW_TS = 1_800_000_000


def _walk(node: Any, path: str = "$") -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        out: list[tuple[str, Any]] = []
        for key, value in node.items():
            out.extend(_walk(value, f"{path}.{key}"))
        return out
    if isinstance(node, list):
        out = []
        for index, value in enumerate(node):
            out.extend(_walk(value, f"{path}[{index}]"))
        return out
    return [(path, node)]


def _ledger(tmp_path: Path, *rows: dict[str, Any]) -> Path:
    from tests.commands.test_research_record import _ledger as build
    from tests.commands.test_research_record import _trial

    return build(tmp_path, *(rows or (_trial(),)))


def _report(tmp_path: Path, *rows: dict[str, Any]) -> Any:
    return gather_trials(_ledger(tmp_path, *rows), now_ts=RESEARCH_NOW_TS)


def _tamper(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["summary"]["profit_factor"] = "9.99"
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# -- the wire -------------------------------------------------------------------------------------


def test_no_wire_value_in_the_trials_payload_is_ever_a_json_number(tmp_path: Path) -> None:
    """Rule 1. The ledger's summaries are `Decimal` in Python and would cross as floats the
    moment anyone let them -- `profit_factor` at 0.1 is the canonical binary-fraction victim."""
    document = json.loads(json.dumps(web_payload.trials_payload(_report(tmp_path))))
    numbers = [
        path
        for path, leaf in _walk(document)
        if not isinstance(leaf, bool) and isinstance(leaf, (int, float))
    ]

    assert numbers == [], f"JSON numbers on the wire: {numbers}"


def test_every_trial_crosses_including_the_rejected_ones(tmp_path: Path) -> None:
    """THE point of the view, on the wire. A research record showing only its selections is a
    highlight reel; the rejected rows are what make the selected one evidence."""
    from tests.commands.test_research_record import _trial

    document = web_payload.trials_payload(
        _report(
            tmp_path,
            _trial(trial_id="t-1", decision="rejected"),
            _trial(trial_id="t-2", decision="selected"),
        )
    )

    assert [row["decision"]["value"] for row in document["rows"]] == ["rejected", "selected"]


def test_the_counts_cross_as_trials_run_and_decisions(tmp_path: Path) -> None:
    """M and N, separately. A single "trials" figure would hide the multiple-comparisons
    denominator DSR corrects against, which is the number the page exists to publish."""
    from tests.commands.test_research_record import _trial

    document = web_payload.trials_payload(
        _report(
            tmp_path,
            _trial(trial_id="t-1", decision="rejected"),
            _trial(trial_id="t-2", decision="diagnostic_only"),
        )
    )

    assert document["trials_run"]["value"] == "2"
    assert document["decisions"]["value"] == "1"


# -- the chain badge, which must never lie --------------------------------------------------------


def test_an_intact_chain_reads_verified(tmp_path: Path) -> None:
    document = web_payload.trials_payload(_report(tmp_path))

    assert document["chain"]["state"] == web_payload.GOOD
    assert document["chain_errors"] == []


def test_a_tampered_row_turns_the_badge_bad_and_names_the_break(tmp_path: Path) -> None:
    """The acceptance criterion. Editing any row must flip the badge -- a badge that could not go
    red would be decoration -- and the page must say WHICH row, or an operator has a red light
    and nothing to look at."""
    from tests.commands.test_research_record import _trial

    path = _ledger(tmp_path, _trial(trial_id="t-1"), _trial(trial_id="t-2"))
    _tamper(path)

    document = web_payload.trials_payload(gather_trials(path, now_ts=RESEARCH_NOW_TS))

    assert document["chain"]["state"] == web_payload.BAD
    assert document["chain_errors"], "a broken chain must name the rows that broke"


def test_a_missing_ledger_is_unknown_and_never_verified(tmp_path: Path) -> None:
    """The three-state badge, and the reason it is not a `flag`. `chain_intact` is `False` both
    when the file was edited and when there was no file, and styling the second as a BREAK would
    report a tamper that never happened -- while styling it as verified would assert a check that
    never ran. Neither. It reads UNKNOWN."""
    document = web_payload.trials_payload(
        gather_trials(tmp_path / "absent.jsonl", now_ts=RESEARCH_NOW_TS)
    )

    assert document["chain"]["state"] == web_payload.UNKNOWN
    assert document["ledger"]["state"] == web_payload.UNKNOWN
    assert document["rows"] == []


# -- the Strathern rail ---------------------------------------------------------------------------


def test_the_trials_endpoint_declares_no_sortable_column_at_all() -> None:
    """The rail, at the endpoint. Not "no performance columns" -- NONE. `?sort=` naming a column
    the route does not declare is refused by `api._sort_request`, so an empty `sortable` makes
    every ordering request a 400 and leaves ledger order the only order this endpoint has.

    Declaring the harmless columns (`rule`, `at`) was the tempting middle road and is refused:
    a sortable collection invites the next column, and the next column is the one that ranks."""
    from keel.web import api as web_api

    route = web_api.API_ROUTES["/api/research/trials"]

    assert route.sortable == ()
    assert route.collection == ""


def test_the_payload_carries_no_ranking_key_of_any_kind(tmp_path: Path) -> None:
    """A `rank`, `best`, `score` or `leaderboard` key would let a client do what the endpoint
    refuses to. Checked on the SERIALISED document, so a key nested in a summary counts."""
    document = json.loads(json.dumps(web_payload.trials_payload(_report(tmp_path))))

    keys = " ".join(path for path, _ in _walk(document)).lower()
    for banned in ("rank", "best", "score", "leaderboard", "top_"):
        assert banned not in keys, f"the research payload must carry no {banned!r}"


def test_the_decision_word_is_never_styled_good_or_bad(tmp_path: Path) -> None:
    """A rejected trial is not a failure -- it is the evidence. Styling `selected` green and
    `rejected` red would rank the table by colour, which is a leaderboard drawn with CSS instead
    of with `ORDER BY`."""
    from tests.commands.test_research_record import _trial

    document = web_payload.trials_payload(
        _report(
            tmp_path,
            _trial(trial_id="t-1", decision="selected"),
            _trial(trial_id="t-2", decision="rejected"),
            _trial(trial_id="t-3", decision="diagnostic_only"),
        )
    )

    states = [row["decision"]["state"] for row in document["rows"]]
    assert web_payload.GOOD not in states
    assert web_payload.BAD not in states


def test_no_research_column_in_the_view_carries_a_sort_key() -> None:
    """The rail's grep-level pin, which #708 asks for by name. `render.js`'s `table()` renders a
    clickable header for any column with a `key`, so a research column that declared one would
    put a sort control on the page even though the endpoint refuses to honour it."""
    body = _view_body()

    assert "key:" not in body, "no research column may declare a sort key"
    assert "onSort" not in body, "the research tables take no sort handler"


# -- provenance and the gauntlet's own refusal ----------------------------------------------------


def test_a_fitted_parameter_warns_and_an_a_priori_one_does_not(tmp_path: Path) -> None:
    """The one judgement this page does make, and it is about EVIDENCE rather than performance:
    a parameter chosen by fitting is weaker evidence than one declared before the data was seen.
    `provenance` is the ledger's own word for that, and Rule 3 keeps the styling here."""
    from tests.commands.test_research_record import _trial

    document = web_payload.trials_payload(
        _report(
            tmp_path,
            _trial(trial_id="t-1", provenance="a_priori"),
            _trial(trial_id="t-2", provenance="fitted"),
        )
    )

    assert document["rows"][0]["provenance"]["state"] == web_payload.NEUTRAL
    assert document["rows"][1]["provenance"]["state"] == web_payload.WARN


def test_a_trial_with_no_series_says_so(tmp_path: Path) -> None:
    """`cscv` and `deflate` REFUSE a series-missing trial, so a reader comparing two trials needs
    to know which of them the statistics could even be computed for. Without the chip the row
    looks like an ordinary trial that simply scored nothing."""
    from tests.commands.test_research_record import _trial

    document = web_payload.trials_payload(
        _report(
            tmp_path,
            _trial(trial_id="t-1", per_trade_pnl=[], series_missing=True),
        )
    )

    assert document["rows"][0]["series"]["state"] == web_payload.WARN


def test_params_and_summary_cross_presentation_ready(tmp_path: Path) -> None:
    """Rule 2. Both are open-ended mappings whose shape is the driver's, and a client handed the
    raw mapping would have to join keys to values to show it -- which is derivation, and would
    put `Object.entries` and a template literal into a module that has neither."""
    from tests.commands.test_research_record import _trial

    document = web_payload.trials_payload(
        _report(tmp_path, _trial(params={"entry_lookback": 20}, summary={"n_trades": 58}))
    )
    row = document["rows"][0]

    assert row["params"] == "entry_lookback=20"
    assert row["summary"] == "n_trades=58"


# -- the client -----------------------------------------------------------------------------------


def _source(name: str) -> str:
    from keel.web import staticfiles

    return (Path(staticfiles.__file__).parent / "static" / "js" / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    from tests.web.test_client_assets import _comments_only

    return _comments_only(_source(name))


def _view_body() -> str:
    after = _code("render.js").split("export function researchView")[1]
    end = after.find("export function ")
    return after if end == -1 else after[:end]


def test_the_research_view_is_wired_into_the_client_router() -> None:
    from keel.web import staticfiles

    assert "research" in staticfiles.CLIENT_ROUTES
    main_code = _code("main.js")
    assert "researchView" in main_code
    assert 'route.name === "research"' in main_code
    assert "export function researchView" in _code("render.js")


def test_the_view_shows_the_chain_verdict_and_the_breaks() -> None:
    """A tamper-evidence badge with no list behind it tells an operator something is wrong and
    gives them nowhere to look."""
    body = _view_body()

    assert "chain" in body
    assert "chain_errors" in body


def test_the_research_view_offers_no_action_of_any_kind() -> None:
    """A research record is read. A "promote this trial" control on a page whose whole argument
    is that selection happened under a discipline would put the selection in a button."""
    body = _view_body().lower()

    for banned in ("promote", "button", "addeventlistener", "onclick"):
        assert banned not in body, f"the research view must offer no {banned}"
