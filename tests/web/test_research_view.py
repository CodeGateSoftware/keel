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


def _cfg(config_path: str) -> Any:
    """A `ServeConfig` with nothing in it but the path `_ledger_path` reads. No database, no
    token: the ledger resolution is a pure function of `config_path` and the working directory,
    and a fixture that bound a server would hide that."""
    from keel.web.server import ServeConfig

    return ServeConfig(host="127.0.0.1", port=0, token="", db_path="", config_path=config_path)


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
    refuses to.

    **What this scan does NOT cover**, stated because an overclaim about a rail is worse than no
    claim. It walks KEY PATHS on the serialised document, so it sees no values at all -- and
    `params` and `summary` cross as already-written lines rather than as mappings, which means a
    `profit_factor` inside a summary is a value here and not a key. It catches a ranking field
    added to the payload's own shape, which is where one would actually be added, and nothing
    else. The enforcement that matters is the endpoint's empty `sortable` and the service's
    ledger ordering, both pinned separately."""
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


# -- the empty ledger, which is the case the badge got wrong ---------------------------------------


def test_an_empty_but_present_ledger_never_reads_verified(tmp_path: Path) -> None:
    """The fourth state, and the one the first cut of this payload got wrong.

    `verify_chain` returns no errors for a file with no rows, so `chain_intact` is `True` and the
    badge read GOOD -- "chain verified, every row still hashes to the next" -- over a file with no
    rows in it at all. That is vacuously true and reads as a positive assertion, which is exactly
    the green-badge-over-an-unverified-file this whole view was built to refuse. A ledger with
    nothing in it has nothing to verify, and the honest word for that is UNKNOWN.
    """
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    document = web_payload.trials_payload(gather_trials(path, now_ts=RESEARCH_NOW_TS))

    assert document["chain"]["state"] == web_payload.UNKNOWN
    # And still distinct from a MISSING ledger: the file is there and was read.
    assert document["ledger"]["state"] == web_payload.NEUTRAL


def test_the_empty_table_says_which_kind_of_empty_this_is(tmp_path: Path) -> None:
    """The service draws the distinction (`test_an_empty_ledger_is_distinct_from_a_missing_one`)
    and the view collapsed it: one hard-coded sentence, "this deployment has no research ledger
    beside it", rendered over a ledger that was present and simply empty.

    The sentence is written in Python for the same reason every other sentence is (Rule 2), and
    it is the two facts stated apart: no file, versus a file with no trials in it yet."""
    present = tmp_path / "empty.jsonl"
    present.write_text("", encoding="utf-8")

    empty_ledger = web_payload.trials_payload(gather_trials(present, now_ts=RESEARCH_NOW_TS))
    no_ledger = web_payload.trials_payload(
        gather_trials(tmp_path / "absent.jsonl", now_ts=RESEARCH_NOW_TS)
    )

    assert "no trials" in empty_ledger["empty_note"].lower()
    assert "no research ledger" in no_ledger["empty_note"].lower()
    assert empty_ledger["empty_note"] != no_ledger["empty_note"]


def test_the_view_takes_its_empty_sentence_from_the_payload() -> None:
    """A hard-coded empty-state string in the renderer is a judgement made in JavaScript, and it
    was the wrong judgement here for a year of possible deployments. Pinned on the source so the
    convenient literal cannot come back."""
    body = _view_body()

    assert "empty_note" in body
    assert "no research ledger beside it" not in body




# -- where the ledger is looked for ----------------------------------------------------------------
#
# All of these set `DEFAULT_LEDGER_PATH` to a name of their own rather than using the real one, and
# that is not fixture convenience. It is the assertion: a resolution that READS the constant passes
# these, and one that restates `docs/experiments/trials-ledger.jsonl` as a literal fails them --
# which is exactly the drift the first cut of this function shipped with, under a comment claiming
# the literal WAS the constant.
#
# `tests/conftest.py::_isolate_trials_ledger` is autouse and already points the constant at an
# ABSOLUTE tmp path so no test can append to the real git-tracked ledger. That is why the absolute
# case below is a real branch and not a hypothetical: it is the value every test in this repo runs
# under, and a resolution that joined a deployment root onto it would silently discard the root.


def _relative_default(monkeypatch: Any, name: str = "rec/trials.jsonl") -> Path:
    """Point `DEFAULT_LEDGER_PATH` at a RELATIVE name of this test's choosing."""
    relative = Path(name)
    monkeypatch.setattr("keel.research.ledger.DEFAULT_LEDGER_PATH", relative)
    return relative


def test_the_ledger_is_looked_for_beside_the_config_first(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A deployment directory (`~/keel`), which is where `keel serve` actually runs. Untested when
    first written, which for two branches and a fallback is how a resolution path ends up
    reporting "no ledger" on every deployment and nobody noticing."""
    from keel.web import api as web_api

    relative = _relative_default(monkeypatch)
    deployment = tmp_path / "deployment"
    ledger = deployment / relative
    ledger.parent.mkdir(parents=True)
    ledger.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert web_api._ledger_path(_cfg(str(deployment / "config.yaml"))) == ledger


def test_the_checkout_is_the_second_place_looked(tmp_path: Path, monkeypatch: Any) -> None:
    """A repository checkout, which is what a relative `DEFAULT_LEDGER_PATH` is relative to and
    where a developer runs `keel serve` from."""
    from keel.web import api as web_api

    relative = _relative_default(monkeypatch)
    checkout = tmp_path / "checkout"
    ledger = checkout / relative
    ledger.parent.mkdir(parents=True)
    ledger.write_text("", encoding="utf-8")
    monkeypatch.chdir(checkout)

    deployment = tmp_path / "deployment"
    deployment.mkdir()

    assert web_api._ledger_path(_cfg(str(deployment / "config.yaml"))) == ledger


def test_neither_root_having_one_names_the_deployment_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Not a fallback for a broken install: a deployment that never had the research repo beside
    it has no ledger, and `gather_trials` reports that as the ordinary state it is. The path
    returned is the deployment's, so that anything logging the miss names where an operator would
    actually put the file -- and it never reaches a browser."""
    from keel.web import api as web_api

    relative = _relative_default(monkeypatch)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    resolved = web_api._ledger_path(_cfg(str(deployment / "config.yaml")))

    assert resolved == deployment / relative
    assert not resolved.exists()


def test_an_absolute_default_is_used_exactly_as_it_stands(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The branch every test in this repo actually runs under, and the one a naive
    `root / DEFAULT_LEDGER_PATH` gets silently wrong -- `Path("/a") / Path("/b")` is `/b`, so the
    join would appear to work while discarding the deployment root it was searching from.

    Stating it as its own branch also makes the constant usable as a real override: a deployment
    that keeps its research record somewhere other than beside the config can say so, and this
    stops treating that absolute answer as a fragment to search two roots for."""
    from keel.web import api as web_api

    ledger = tmp_path / "somewhere" / "trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("", encoding="utf-8")
    monkeypatch.setattr("keel.research.ledger.DEFAULT_LEDGER_PATH", ledger)

    deployment = tmp_path / "deployment"
    deployment.mkdir()

    assert web_api._ledger_path(_cfg(str(deployment / "config.yaml"))) == ledger


def test_the_resolved_path_never_reaches_the_browser(tmp_path: Path) -> None:
    """A filesystem path in a payload is disclosure with no reader benefit, and this is the one
    report whose source is a file rather than the database everything else reads."""
    document = json.loads(
        json.dumps(
            web_payload.trials_payload(
                gather_trials(_ledger(tmp_path), now_ts=RESEARCH_NOW_TS)
            )
        )
    )

    text = json.dumps(document)
    assert str(tmp_path) not in text
    assert "trials-ledger" not in text


# -- the slippage universe (#708, view 4) ----------------------------------------------------------


def _slippage(tmp_path: Path, **volumes: str) -> Any:
    """A slippage payload over the fixture allowlist, with `volumes` naming the priced products.

    Keyword names are ASSET codes (`BTC=...`). The product id comes from
    `_products._default_sim_products` -- the derivation the report itself uses -- rather than
    from an `f"{asset}-USD"` here: a test that hard-codes the settlement currency passes on a
    `quote_currency: USDC` deployment while the report under test would read nothing.
    """
    from keel.commands._products import _default_sim_products
    from keel.commands.slippage import gather_slippage
    from tests.commands.test_slippage import _config, _daily, _repo

    config = _config(tmp_path)
    ids = {product.split("-")[0]: product for product in _default_sim_products(config)}
    repo = _repo(tmp_path)
    for asset, volume in volumes.items():
        _daily(repo, ids[asset], volume_usd=volume)
    return web_payload.slippage_payload(
        gather_slippage(repo, config, now_ts=RESEARCH_NOW_TS)
    )


def test_no_wire_value_in_the_slippage_payload_is_ever_a_json_number(tmp_path: Path) -> None:
    """Rule 1, and this payload is full of the values that tempt it: a rate, a multiple, a
    volume in dollars. `0.01838 * 10000` is `183.79999999999998` as a float."""
    from tests.commands.test_slippage import AT_ANCHOR

    document = json.loads(json.dumps(_slippage(tmp_path, BTC=str(AT_ANCHOR), PAXG="1000")))
    numbers = [
        path
        for path, leaf in _walk(document)
        if not isinstance(leaf, bool) and isinstance(leaf, (int, float))
    ]

    assert numbers == [], f"JSON numbers on the wire: {numbers}"


def test_the_payload_says_these_figures_are_an_assumption(tmp_path: Path) -> None:
    """THE correction this view exists to carry, and the one #708's own scope note gets wrong by
    calling these figures "measured". keel stores no order books and no realised spreads;
    `slippage_for_quote_volume` opens by saying so. A cost table read as a measurement is a page
    inventing evidence, so the word crosses as a field rather than living in a comment."""
    document = _slippage(tmp_path, BTC="500000000")

    assert document["basis"]["value"] == "assumption"
    assert "not a measurement" in document["basis"]["display"]


def test_the_model_parameters_cross_so_any_row_can_be_recomputed(tmp_path: Path) -> None:
    """`sim/report.py`'s rule about its own table: "a number whose assumptions cannot be
    recovered is not evidence"."""
    document = _slippage(tmp_path, BTC="500000000")

    assert document["floor_bp"]["display"] == "5.0bp"
    assert document["cap_bp"]["display"] == "183.8bp"
    assert document["anchor_quote_volume"]["value"] == "500000000"


def test_a_capped_row_warns_that_it_is_a_lower_bound(tmp_path: Path) -> None:
    """Where the cap fires, the model's bound decided rather than the asset's liquidity, so the
    real cost is at least this. Flattering direction, so it warns."""
    document = _slippage(tmp_path, PAXG="1000")
    row = next(r for r in document["rows"] if r["product_id"] == "PAXG-USD")

    assert row["capped"]["state"] == web_payload.WARN
    assert row["slippage_bp"]["display"] == "183.8bp"


def test_a_fallback_row_warns_and_shows_no_multiple(tmp_path: Path) -> None:
    """The cheapest rate in the model, handed to the asset keel knows least about. The rate is
    the floor, but the "vs floor" cell is ABSENT rather than `1.0` -- a multiple there would read
    "as cheap as the most liquid asset in the corpus" about an asset with no cached history."""
    document = _slippage(tmp_path, BTC="500000000")
    row = next(r for r in document["rows"] if r["product_id"] == "ETH-USD")

    assert row["fallback"]["state"] == web_payload.WARN
    assert row["floor_multiple"]["display"] == web_payload.ABSENT
    assert row["median_daily_quote_volume"]["display"] == web_payload.ABSENT
    assert row["bars"]["value"] == "0"


def test_the_floor_count_is_reported_against_the_products_actually_priced(
    tmp_path: Path,
) -> None:
    """The headline, with an honest denominator. `product_count` would flatter it by counting
    rows that were never priced at all -- a deployment that had cached nothing would read "0 of
    3 reach the floor" as though three assets had been checked and found expensive."""
    from tests.commands.test_slippage import AT_ANCHOR

    document = _slippage(tmp_path, BTC=str(AT_ANCHOR), PAXG="1000")

    assert document["product_count"]["value"] == "3"
    assert document["priced_count"]["value"] == "2"
    assert document["at_floor_count"]["value"] == "1"
    assert document["fallback_count"]["value"] == "1"


def test_the_slippage_endpoint_declares_no_sortable_column_either(tmp_path: Path) -> None:
    """The rail across the whole `/research` surface, not just its first view. A cost table
    ordered cheapest-first reads as a shortlist of what to trade."""
    from keel.web import api as web_api

    route = web_api.API_ROUTES["/api/research/slippage"]

    assert route.sortable == ()
    assert route.collection == ""


def test_the_slippage_section_is_wired_into_the_research_view() -> None:
    main_code = _code("main.js")

    assert '"research/slippage"' in main_code
    assert "function slippageSection" in _code("render.js")
    assert "export function researchView(data, slippage)" in _code("render.js")


def test_the_slippage_section_states_the_basis_and_declares_no_sort_key() -> None:
    """Both rails at once: the assumption notice is rendered, and the cost table carries no
    sort control -- `researchView`'s own body is already scanned for `key:`, and this section
    lives inside it."""
    after = _code("render.js").split("function slippageSection")[1]
    end = after.find("export function ")
    body = after if end == -1 else after[:end]

    assert "basis" in body
    assert "key:" not in body, "no slippage column may declare a sort key"
    assert "measured" not in body.lower(), "these figures are an assumption, never a measurement"


# -- units on the wire -----------------------------------------------------------------------------


def test_a_rate_in_basis_points_carries_its_unit(tmp_path: Path) -> None:
    """The bug this fixes: `ratio` emits a bare `5.0`, and the summary tiles have no column
    header to supply the unit -- so the floor, the cap and every rate read as unitless numbers
    on a page whose entire subject is a cost in basis points.

    `basis_points` is `percent`'s sibling and nothing more: it SUFFIXES, it does not rescale. The
    x10000 stays in `keel/commands/slippage.py`, where `ratio`'s own docstring says a units change
    belongs -- and a suffix is not a units change, which is why `percent` has one.
    """
    document = _slippage(tmp_path, BTC="500000000")

    assert document["floor_bp"]["display"] == "5.0bp"
    assert document["cap_bp"]["display"] == "183.8bp"
    assert document["rows"][0]["slippage_bp"]["display"].endswith("bp")


def test_a_floor_multiple_carries_its_unit_too(tmp_path: Path) -> None:
    """`1.1` in a cell headed "vs floor" is readable; `1.1x` is unambiguous, and the tile it may
    end up in tomorrow has no header at all."""
    repo_document = _slippage(tmp_path, BTC="125000000")
    row = next(r for r in repo_document["rows"] if r["product_id"] == "BTC-USD")

    assert row["floor_multiple"]["display"] == "2.0x"


def test_the_wire_value_of_a_suffixed_field_stays_a_bare_number(tmp_path: Path) -> None:
    """`display` carries the unit; `value` never does. A client that compared `value` against a
    threshold would otherwise be parsing "5.0bp" -- and `_plain`'s no-exponent guarantee is
    about the value, not the label."""
    document = _slippage(tmp_path, BTC="500000000")

    assert document["floor_bp"]["value"] == "5"
    assert document["cap_bp"]["value"] == "183.8"


# -- the flag says what its name says --------------------------------------------------------------


def test_the_fallback_flag_is_true_when_the_row_fell_back(tmp_path: Path) -> None:
    """It was named `priced_from`, whose `value` was `"true"` when the product was NOT priced
    from its own liquidity -- a key reading as the opposite of what it carried. Nothing rendered
    wrong, because Rule 3 clients read `display` and `state`; it was a trap for the next consumer
    that read `value`. Named `fallback` now, matching `SlippageRow` and `SlippageAssumption`."""
    from tests.commands.test_slippage import AT_ANCHOR

    document = _slippage(tmp_path, BTC=str(AT_ANCHOR))
    priced = next(r for r in document["rows"] if r["product_id"] == "BTC-USD")
    fell_back = next(r for r in document["rows"] if r["product_id"] == "ETH-USD")

    assert priced["fallback"]["value"] == "false"
    assert fell_back["fallback"]["value"] == "true"
    assert fell_back["fallback"]["state"] == web_payload.WARN


# -- the empty state describes a state that can happen ---------------------------------------------


def test_the_slippage_table_makes_no_claim_about_an_empty_allowlist() -> None:
    """`config._parse_allowlist` raises on a missing or empty allowlist, so "this deployment has
    an empty allowlist" describes a deployment that cannot load. Same class as the empty-ledger
    sentence #724 fixed: an unreachable message asserting something impossible."""
    after = _code("render.js").split("function slippageSection")[1]
    end = after.find("export function ")
    body = after if end == -1 else after[:end]

    assert "empty allowlist" not in body
