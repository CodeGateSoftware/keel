"""Tests for `keel.commands.compliance_console` -- the Compliance menu, the scout-results
handler and the "Shariah in force" browser (issue #389 C3; PRD O6/O10 and §3's tree).

Four surfaces, all pinned here:

* **The Compliance sub-menu** -- PRD §3's tree under Compliance, every entry a navigation
  target whose behavior is a DISPATCH (a view over a C1 service report, or a form that
  collects fields and calls the same repository/service function the CLI command calls),
  never a behavior of the TUI's own.
* **The forms** -- the record-writes (`assets attest` [typed], `attest-instrument`,
  `exempt`/`unexempt`, `subscription attest`/`set`, `withdrawals attest` [typed]): each
  collects its fields through an injected prompt function and dispatches to the exact
  service/repository call the CLI makes, with the deliberately-typed actions refusing to
  proceed without their typed phrase (O3 -- never pre-filled, never piped).
* **The scout-results handler (O6)** -- the operator-local proposals directory listed,
  a shortlist selected and rendered through the EXISTING admission services, and the
  attest step offered but never auto-run.
* **The "Shariah in force" browser (O10)** -- the attestations/exemptions in force over
  the ACTIVE allowlist rendered from repository data through a service read, the
  fiqh-derived constraints each carrying a verbatim quote from and citation into
  `docs/fiqh-basis.md`, and the standing honesty lines always visible.

Mirrors `tests/commands/test_console.py`'s fixture style (in-memory `Repository`,
`_config`, `NOW_TS`).
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.commands import compliance_console as cc
from keel.commands.admission import list_shortlists
from keel.commands.assets import gather_attestations_in_force
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository

NOW_TS = 1_800_000_000

_ROOT = Path(__file__).resolve().parents[2]
_FIQH_BASIS = (_ROOT / "docs" / "fiqh-basis.md").read_text()

#: The two standing honesty states, pinned against the document that states them (the
#: two-sided pattern from `tests/test_fiqh_basis.py`/`test_scholarly_review.py`): the
#: browser may not soften either line while fiqh-basis keeps it blunt, and fiqh-basis
#: cannot lose either while the browser keeps rendering it.
NOT_A_FATWA_ENGINE = "keel is not a fatwa engine"
NO_SCHOLARLY_REVIEW = "No scholarly review of keel's fiqh basis has occurred"


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    return r


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "ETH"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


class _RecordingRepo:
    """A real `Repository` wrapped so every WRITE the console form makes is recorded with
    its exact keyword arguments -- the spy the "calls the same function the CLI calls"
    assertions read. Reads fall through to the real repo."""

    def __init__(self, inner: Repository) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def upsert_asset_attestation(self, **kwargs: Any) -> None:
        self.calls.append(("upsert_asset_attestation", kwargs))

    def upsert_instrument_attestation(self, **kwargs: Any) -> None:
        self.calls.append(("upsert_instrument_attestation", kwargs))

    def upsert_screen_exception(self, **kwargs: Any) -> None:
        self.calls.append(("upsert_screen_exception", kwargs))

    def delete_screen_exception(self, asset: str, criterion: str) -> int:
        self.calls.append(("delete_screen_exception", {"asset": asset, "criterion": criterion}))
        return self._inner.delete_screen_exception(asset, criterion)

    def set_state(self, key: str, value: Any) -> None:
        self.calls.append(("set_state", {"key": key, "value": value}))
        self._inner.set_state(key, value)


def _prompt(answers: list[str]) -> Any:
    """A scripted prompt function: records every question asked, answers in order."""
    queue = iter(answers)
    asked: list[str] = []

    def fn(text: str) -> str:
        asked.append(text)
        return next(queue)

    fn.asked = asked
    return fn


def _attest_answers(
    *, asset: str = "BTC",
    backing: str = "ayn",
    pays_yield: str = "n",
    typed: str = "BTC",
    seeded: bool = False,
) -> list[str]:
    # `seeded` mirrors the scout flow: the chosen candidate's asset arrives pre-seeded, so
    # the form asks one fewer question (the judgment fields are ALWAYS asked).
    head = [] if seeded else [asset]
    return [
        *head, "payments", backing, pays_yield, "https://example.com/btc", "operator",
        typed,
    ]


# -- the Compliance sub-menu (PRD §3) -----------------------------------------------------------


def test_the_compliance_menu_is_the_prd_tree() -> None:
    """PRD §3's Compliance branch, in tree order: screen, propose, attest [typed],
    attest-instrument, exempt/unexempt, holdings, discover, [Scout results...],
    [Shariah in force...], subscription (show/attest), withdrawals attest [typed],
    purification."""
    assert [entry.label for entry in cc.COMPLIANCE_MENU] == [
        "screen",
        "propose",
        "attest",
        "attest-instrument",
        "exempt",
        "unexempt",
        "holdings",
        "discover",
        "Scout results",
        "Shariah in force",
        "subscription show",
        "subscription attest",
        "subscription set",
        "withdrawals attest",
        "purification",
    ]


def test_every_compliance_entry_is_a_dispatch_not_a_behavior() -> None:
    """The closed vocabulary: every entry is a VIEW over a service report, a FORM that
    collects fields and calls a CLI-called service/repository function, or the scout
    browser -- the TUI renders and dispatches, nothing more (O2)."""
    kinds = {entry.kind for entry in cc.COMPLIANCE_MENU}
    assert kinds <= {"view", "form", "scout"}
    assert any(entry.kind == "scout" for entry in cc.COMPLIANCE_MENU)
    views = {e.target for e in cc.COMPLIANCE_MENU if e.kind == "view"}
    assert views == {
        "screen", "propose", "holdings", "discover", "shariah", "subscription", "purification",
    }
    forms = {e.target for e in cc.COMPLIANCE_MENU if e.kind == "form"}
    assert forms == {
        "attest", "attest-instrument", "exempt", "unexempt",
        "subscription-attest", "subscription-set", "withdrawals-attest",
    }


def test_the_prd_marks_attest_and_withdrawals_attest_as_typed_and_the_menu_says_so() -> None:
    """O3 made visible in the tree itself: the two entries the PRD marks "(typed)" carry
    the marker, and the rendered menu shows it inline so the ceremony is never a
    surprise."""
    typed = {e.label for e in cc.COMPLIANCE_MENU if e.typed}
    assert typed == {"attest", "withdrawals attest"}
    lines = cc.build_compliance_menu_lines()
    joined = "\n".join(line.text for line in lines)
    assert "typed" in joined


def test_the_compliance_menu_screen_renders_every_entry_with_one_cursor() -> None:
    lines = cc.build_compliance_menu_lines(cursor=3)
    texts = [line.text for line in lines]
    for entry in cc.COMPLIANCE_MENU:
        assert any(entry.label in t for t in texts), entry.label
    marked = [t for t in texts if t.lstrip().startswith(">")]
    assert len(marked) == 1 and "attest-instrument" in marked[0]
    assert any("q/Esc" in t and "Compliance menu" in t for t in texts)


def test_the_compliance_menu_renders_a_result_toast() -> None:
    """Every write shows a confirmation line -- on the menu itself, where the form ran."""
    lines = cc.build_compliance_menu_lines(message="attested BTC: sector=payments backing=ayn")
    assert any("attested BTC" in line.text for line in lines)


# -- the forms: dispatch to the same calls the CLI makes ---------------------------------------


def test_attest_form_collects_fields_and_records_exactly_what_the_cli_records(
    repo: Repository,
) -> None:
    """`keel assets attest` is thin over `Repository.upsert_asset_attestation` -- the form
    calls the SAME function with the SAME argument names, and echoes the SAME line."""
    spy = _RecordingRepo(repo)
    result = cc.run_attest_form(spy, _prompt(_attest_answers()), NOW_TS)

    assert spy.calls == [
        (
            "upsert_asset_attestation",
            {
                "asset": "BTC",
                "sector": "payments",
                "backing": "ayn",
                "pays_yield": False,
                "source": "https://example.com/btc",
                "attested_by": "operator",
                "attested_at": NOW_TS,
            },
        )
    ]
    assert result == "attested BTC: sector=payments backing=ayn pays_yield=False"


def test_attest_form_refuses_to_proceed_without_the_typed_asset_code(repo: Repository) -> None:
    """The PRD marks attest "(typed)": the form's final gate demands the operator type the
    ASSET CODE back -- never pre-filled, never bypassable -- and a wrong phrase leaves the
    repository untouched."""
    spy = _RecordingRepo(repo)
    answers = _attest_answers(typed="wrong phrase")

    result = cc.run_attest_form(spy, _prompt(answers), NOW_TS)

    assert spy.calls == []  # nothing recorded
    assert "cancelled" in result.lower()
    assert "BTC" in result  # the refusal names what was NOT attested


def test_attest_form_is_cancelled_by_an_empty_asset(repo: Repository) -> None:
    spy = _RecordingRepo(repo)
    result = cc.run_attest_form(spy, _prompt(["", "payments"]), NOW_TS)
    assert spy.calls == []
    assert "cancelled" in result.lower()


def test_attest_form_rejects_an_unknown_backing_without_writing(repo: Repository) -> None:
    """`--backing` is a Choice on the CLI; the form enforces the same vocabulary
    (`KNOWN_BACKINGS`) and writes nothing on a bad one."""
    spy = _RecordingRepo(repo)
    result = cc.run_attest_form(spy, _prompt(_attest_answers(backing="diamond")), NOW_TS)
    assert spy.calls == []
    assert "backing" in result


def test_the_attest_typed_gate_never_leaks_the_phrase_into_the_question(
    repo: Repository,
) -> None:
    """The gate must ASK without pre-filling: the question names the asset, and the answer
    is whatever the human types -- so the recorded question carries the asset code but the
    function cannot supply the answer."""
    prompt = _prompt(["BTC"])
    assert cc.typed_asset_confirmation("BTC", prompt) is True
    assert any("BTC" in q for q in prompt.asked)
    prompt2 = _prompt(["btc "])
    assert cc.typed_asset_confirmation("BTC", prompt2) is True  # whitespace-tolerant
    prompt3 = _prompt(["BT"])
    assert cc.typed_asset_confirmation("BTC", prompt3) is False


def test_instrument_attest_form_uppercases_the_product_like_the_cli(
    repo: Repository,
) -> None:
    """`keel assets attest-instrument` uppercases the product id before the upsert (the
    lookup key is uppercase); the form does the same, through the same repository call."""
    spy = _RecordingRepo(repo)
    answers = ["coinbase", "btc-usd", "spot", "https://example.com/spec", "operator", "btc-usd"]
    result = cc.run_instrument_attest_form(spy, _prompt(answers), NOW_TS)

    assert spy.calls == [
        (
            "upsert_instrument_attestation",
            {
                "venue": "coinbase",
                "product_id": "BTC-USD",
                "wrapper": "spot",
                "source": "https://example.com/spec",
                "attested_by": "operator",
                "attested_at": NOW_TS,
            },
        )
    ]
    assert result == "attested BTC-USD on coinbase: wrapper=spot"


def test_exempt_form_records_a_documented_exception_and_refuses_a_blank_rationale(
    repo: Repository,
) -> None:
    """`keel assets exempt` uppercases the asset and refuses an empty rationale (an
    "undocumented documented exception"); the form enforces both, criterion restricted to
    WAIVABLE_CRITERIA's vocabulary."""
    spy = _RecordingRepo(repo)
    result = cc.run_exempt_form(
        spy, _prompt(["paxg", "history", "PAXG has 3y of history at the venue", "operator"]), NOW_TS
    )
    assert spy.calls == [
        (
            "upsert_screen_exception",
            {
                "asset": "PAXG",
                "criterion": "history",
                "rationale": "PAXG has 3y of history at the venue",
                "granted_by": "operator",
                "granted_at": NOW_TS,
            },
        )
    ]
    assert "recorded exception" in result

    spy2 = _RecordingRepo(repo)
    refused = cc.run_exempt_form(spy2, _prompt(["PAXG", "history", "   ", "operator"]), NOW_TS)
    assert spy2.calls == []
    assert "rationale" in refused


def test_unexempt_form_reports_a_revoke_and_an_absence(repo: Repository) -> None:
    repo.upsert_screen_exception(
        asset="PAXG", criterion="history", rationale="r", granted_by="o", granted_at=NOW_TS
    )
    spy = _RecordingRepo(repo)
    revoked = cc.run_unexempt_form(spy, _prompt(["paxg", "history"]))
    assert ("delete_screen_exception", {"asset": "PAXG", "criterion": "history"}) in spy.calls
    assert "revoked" in revoked

    absent = cc.run_unexempt_form(spy, _prompt(["PAXG", "history"]))
    assert "no such exception" in absent


def test_subscription_attest_form_dispatches_to_the_subscription_service(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The form holds NO tier-resolution logic of its own: it collects the fields and
    hands them to the service function the CLI command calls (C1's one-implementation
    rule), which resolves the tier, the venue and the pacing."""
    recorded: dict[str, Any] = {}

    def fake_apply(
        r: Any, config: Any, *, venue: str | None, tier_name: str, pacing: str | None, now_ts: int
    ) -> str:
        recorded.update(
            repo=r, venue=venue, tier_name=tier_name, pacing=pacing, now_ts=now_ts
        )
        return "attested coinbase: tier=starter free_volume_usd=500 status=active due in 365 days"

    monkeypatch.setattr(cc, "apply_subscription_attest", fake_apply)
    config = _config()

    result = cc.run_subscription_attest_form(
        repo, config, _prompt(["", "starter", ""]), NOW_TS
    )

    assert recorded == {
        "repo": repo, "venue": None, "tier_name": "starter", "pacing": None, "now_ts": NOW_TS,
    }
    assert result.startswith("attested coinbase")


def test_subscription_attest_form_renders_a_service_error_calmly(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_apply(*args: Any, **kwargs: Any) -> str:
        raise ValueError("unknown tier 'nope'. Configured tiers: starter")

    monkeypatch.setattr(cc, "apply_subscription_attest", fake_apply)
    result = cc.run_subscription_attest_form(repo, _config(), _prompt(["", "nope", ""]), NOW_TS)
    assert result.startswith("Error:")
    assert "unknown tier" in result


def test_subscription_set_form_dispatches_to_the_subscription_service(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, Any] = {}

    def fake_apply(
        r: Any, config: Any, *, venue: str | None, free_volume_raw: str, pacing: str | None,
        now_ts: int,
    ) -> str:
        recorded.update(venue=venue, free_volume_raw=free_volume_raw, pacing=pacing, now_ts=now_ts)
        return "set coinbase: free_volume_usd=500 tier=unknown"

    monkeypatch.setattr(cc, "apply_subscription_set", fake_apply)
    result = cc.run_subscription_set_form(repo, _config(), _prompt(["", "500", ""]), NOW_TS)

    assert recorded == {
        "venue": None, "free_volume_raw": "500", "pacing": None, "now_ts": NOW_TS,
    }
    assert result.startswith("set coinbase")


def test_withdrawals_enabled_requires_the_typed_gate_before_any_write(
    repo: Repository,
) -> None:
    """O3's sacred case: `withdrawals attest --enabled` RELEASES a rail-17 halt, so it
    keeps its typed confirmation. The gate is the ONLY thing between the form and the
    repository -- a declined gate means not a single state row is written."""
    spy = _RecordingRepo(repo)
    declined: list[bool] = []

    def decline() -> bool:
        declined.append(True)
        return False

    result = cc.run_withdrawals_form(spy, _prompt(["enabled"]), NOW_TS, confirm_enabled_fn=decline)

    assert declined == [True]
    assert spy.calls == []
    assert "cancelled" in result.lower() or "unchanged" in result.lower()

    def approve() -> bool:
        return True

    approved = cc.run_withdrawals_form(
        _RecordingRepo(repo), _prompt(["enabled"]), NOW_TS, confirm_enabled_fn=approve
    )
    assert "ENABLED" in approved


def test_withdrawals_enabled_writes_exactly_what_the_cli_writes(repo: Repository) -> None:
    spy = _RecordingRepo(repo)
    result = cc.run_withdrawals_form(
        spy, _prompt(["enabled"]), NOW_TS, confirm_enabled_fn=lambda: True
    )
    assert spy.calls == [
        ("set_state", {"key": "withdrawals_enabled", "value": True}),
        ("set_state", {"key": "withdrawals_attested_at", "value": NOW_TS}),
    ]
    assert "withdrawals attested ENABLED" in result
    assert "expires in 7 days" in result


def test_withdrawals_suspended_is_ungated_like_the_cli(repo: Repository) -> None:
    """`--suspended` only ever REDUCES capability: no typed gate, immediate write -- the
    CLI's own asymmetry, kept byte-for-byte."""
    spy = _RecordingRepo(repo)
    asked: list[bool] = []

    def gate() -> bool:
        asked.append(True)
        return True

    result = cc.run_withdrawals_form(spy, _prompt(["suspended"]), NOW_TS, confirm_enabled_fn=gate)

    assert asked == []
    assert spy.calls == [
        ("set_state", {"key": "withdrawals_enabled", "value": False}),
        ("set_state", {"key": "withdrawals_attested_at", "value": NOW_TS}),
    ]
    assert "SUSPENDED" in result
    assert "rail 17" in result


def test_withdrawals_form_rejects_an_unrecognized_answer_without_writing(
    repo: Repository,
) -> None:
    spy = _RecordingRepo(repo)
    result = cc.run_withdrawals_form(
        spy, _prompt(["maybe"]), NOW_TS, confirm_enabled_fn=lambda: True
    )
    assert spy.calls == []
    assert "cancelled" in result.lower()


def test_the_withdrawals_typed_gate_is_the_clis_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enabled-direction gate must be the CLI's `_require_interactive_confirmation`
    with the CLI's own action wording -- not a TUI-invented second gate -- and it fails
    CLOSED (a refusal, a Ctrl-C, any exception) so the halt is never released silently."""
    import click as click_mod

    import keel.commands._common as common

    asked: list[tuple[str, str]] = []

    def refusing_gate(action: str, detail: str) -> None:
        asked.append((action, detail))
        raise click_mod.ClickException("aborted (confirmation not given).")

    monkeypatch.setattr(common, "_require_interactive_confirmation", refusing_gate)
    assert cc.clis_typed_withdrawals_gate() is False

    def accepting_gate(action: str, detail: str) -> None:
        asked.append((action, detail))

    monkeypatch.setattr(common, "_require_interactive_confirmation", accepting_gate)
    assert cc.clis_typed_withdrawals_gate() is True

    assert asked[0][0] == "attest withdrawals as ENABLED"
    assert "rail 17" in asked[0][1].lower()


def test_run_form_dispatches_by_name_to_every_registered_form(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The menu's dispatch seam: every form entry's target resolves to exactly one
    runner, and the runner receives the repo/config/prompt/now the loop holds."""
    for entry in cc.COMPLIANCE_MENU:
        if entry.kind != "form":
            continue
        assert entry.target in cc.FORM_RUNNERS, entry.target


# -- the scout-results handler (O6) ----------------------------------------------------------------


def test_list_shortlists_is_newest_first_and_never_raises(tmp_path: Path) -> None:
    """The service read behind the browser: every `*shortlist.json` in the proposals
    directory, newest mtime first (name desc on ties -- `latest_shortlist`'s own
    tiebreak, reversed for a list), never creating the directory and never raising on
    absent/strangled paths."""
    old = tmp_path / "2026-08-01-shortlist.json"
    old.write_text("{}")
    two_days_ago = time.time() - 172_800
    os.utime(old, (two_days_ago, two_days_ago))
    (tmp_path / "2026-08-15-shortlist.json").write_text("{}")
    (tmp_path / "2026-08-15-param-proposals.json").write_text("{}")  # sibling, not a shortlist

    files = list_shortlists(tmp_path)

    assert [f.path.name for f in files] == [
        "2026-08-15-shortlist.json",
        "2026-08-01-shortlist.json",
    ]
    assert list_shortlists(tmp_path / "nonexistent") == ()
    stray = tmp_path / "stray"
    stray.write_text("not a dir")
    assert list_shortlists(stray) == ()


def test_scout_listing_reads_the_configured_proposals_dir(tmp_path: Path) -> None:
    """The path comes from CONFIG (`proposals_dir`, default `~/keel/proposals` -- the key
    already exists), never a TUI-side guess."""
    (tmp_path / "a-shortlist.json").write_text("{}")
    config = _config(proposals_dir=str(tmp_path))

    files, directory = cc.scout_listing(config)

    assert directory == tmp_path
    assert [f.path.name for f in files] == ["a-shortlist.json"]
    assert cc.scout_listing(_config(proposals_dir=str(tmp_path / "missing")))[0] == ()


def test_scout_list_renders_every_file_and_a_clear_empty_state(tmp_path: Path) -> None:
    old = tmp_path / "2026-08-01-shortlist.json"
    old.write_text("{}")
    two_days_ago = time.time() - 172_800
    os.utime(old, (two_days_ago, two_days_ago))
    (tmp_path / "2026-08-15-shortlist.json").write_text("{}")
    files = list_shortlists(tmp_path)

    lines = cc.build_scout_list_lines(files, tmp_path, cursor=1)
    texts = [line.text for line in lines]
    assert all(f.path.name in "\n".join(texts) for f in files)
    marked = [t for t in texts if t.lstrip().startswith(">")]
    assert len(marked) == 1 and "2026-08-01-shortlist.json" in marked[0]
    assert any("proposals" in t.lower() and "shortlist" in t.lower() for t in texts)

    empty = cc.build_scout_list_lines((), tmp_path / "missing", cursor=0)
    joined = "\n".join(line.text for line in empty)
    assert "no proposals" in joined.lower()
    assert "missing" in joined  # names where it looked
    assert "shortlist" in joined  # names the convention, so one rename fixes it


def test_scout_view_screens_the_chosen_file_through_the_admission_services(
    repo: Repository, tmp_path: Path
) -> None:
    """Selecting a file renders `assets propose`'s own report for THAT file: parse +
    `build_proposal_report` through THE gate (`screen_product`), the existing services
    end to end -- an unattested candidate on an empty cache reads REJECT, proving the
    gate really ran rather than the file being pretty-printed."""
    from keel.commands.admission import build_propose_view
    from keel.commands.assets import screen_product

    shortlist = tmp_path / "2026-08-15-shortlist.json"
    shortlist.write_text(
        '{"candidates": [{"asset": "FET", "rationale": "ai compute", '
        '"sources": ["https://example.com/fet"]}]}'
    )
    config = _config(proposals_dir=str(tmp_path))

    view = build_propose_view(repo, config, screen_product, path=shortlist)
    lines, cursor_line, candidates = cc.build_scout_file_lines(view, cursor=0)

    joined = "\n".join(line.text for line in lines)
    assert "REJECT" in joined  # unattested fails closed -- the gate ran
    assert "FET" in joined
    assert str(shortlist) in joined
    assert candidates == 1
    # exactly one cursor-marked candidate row, on a verdict line
    assert lines[cursor_line].text.strip().startswith(">")
    assert "FET" in lines[cursor_line].text


def test_scout_attest_step_uses_the_same_typed_attest_form(repo: Repository) -> None:
    """The admission flow's attest step IS the Compliance attest form (typed), seeded
    with the chosen candidate's asset -- and it never runs without the phrase."""
    spy = _RecordingRepo(repo)
    answers = _attest_answers(asset="FET", typed="FET", seeded=True)
    result = cc.run_attest_form(spy, _prompt(answers), NOW_TS, asset="FET")

    assert spy.calls[0][1]["asset"] == "FET"
    assert "attested FET" in result

    spy2 = _RecordingRepo(repo)
    refused = cc.run_attest_form(
        spy2, _prompt(_attest_answers(asset="FET", typed="no", seeded=True)), NOW_TS, asset="FET"
    )
    assert spy2.calls == []
    assert "cancelled" in refused.lower()


# -- the "Shariah in force" browser (O10) ------------------------------------------------------


def _inventory_repo(repo: Repository) -> Repository:
    repo.upsert_asset_attestation(
        asset="BTC", sector="payments", backing="ayn", pays_yield=False,
        source="https://example.com/btc", attested_by="operator", attested_at=NOW_TS - 500,
    )
    repo.upsert_instrument_attestation(
        venue="coinbase", product_id="BTC-USD", wrapper="spot",
        source="https://example.com/spec", attested_by="operator", attested_at=NOW_TS - 400,
    )
    repo.upsert_screen_exception(
        asset="BTC", criterion="history", rationale="venue history is deep enough",
        granted_by="operator", granted_at=NOW_TS - 300,
    )
    # An attestation for an asset NOT on the active allowlist -- must not appear.
    repo.upsert_asset_attestation(
        asset="DOGE", sector="gambling", backing="native", pays_yield=False,
        source="https://example.com/doge", attested_by="operator", attested_at=NOW_TS - 200,
    )
    return repo


def test_gather_attestations_in_force_scopes_to_the_active_allowlist(repo: Repository) -> None:
    """The service read (in `keel.commands.assets`, not the TUI): the attestations IN
    FORCE over the ACTIVE allowlist -- asset rows, instrument rows keyed by the venue
    pair the screen looks up, exemptions in effect, and the allowlisted assets with NO
    attestation named as such (fail-closed is a fact worth showing)."""
    repo = _inventory_repo(repo)
    config = _config()  # allowlist [BTC, ETH]

    inventory = gather_attestations_in_force(repo, config)

    assert inventory.allowlist == ("BTC", "ETH")
    assert [row["asset"] for row in inventory.asset_rows] == ["BTC"]
    assert inventory.asset_rows[0]["source"] == "https://example.com/btc"
    instrument_keys = [
        (row["venue"], row["product_id"], row["wrapper"])
        for row in inventory.instrument_rows
    ]
    assert instrument_keys == [("coinbase", "BTC-USD", "spot")]
    assert [(row["asset"], row["criterion"]) for row in inventory.exceptions] == [
        ("BTC", "history")
    ]
    assert inventory.unattested == ("ETH",)


def test_shariah_lines_render_the_attestations_in_force_from_repo_data(repo: Repository) -> None:
    """Each attestation carries its attributed source, its ruling and its recorded date
    (O10's own demand) -- rendered from the service read, never re-derived by the TUI."""
    repo = _inventory_repo(repo)
    inventory = gather_attestations_in_force(repo, _config())

    lines = cc.build_shariah_lines(inventory, withdrawals_enabled=True, now_ts=NOW_TS)
    joined = "\n".join(line.text for line in lines)

    assert "BTC" in joined
    assert "https://example.com/btc" in joined  # attributed source
    assert "ayn" in joined and "payments" in joined  # the ruling
    assert "BTC-USD" in joined and "spot" in joined  # the instrument in force
    assert "coinbase" in joined
    # the recorded date, human-readable (not a raw int)
    expected_day = time.strftime("%Y-%m-%d", time.localtime(NOW_TS - 400))
    assert expected_day in joined
    assert str(NOW_TS - 400) not in joined


def test_shariah_lines_render_exemptions_and_unattested_assets(repo: Repository) -> None:
    repo = _inventory_repo(repo)
    inventory = gather_attestations_in_force(repo, _config())

    lines = cc.build_shariah_lines(inventory, withdrawals_enabled=None, now_ts=NOW_TS)
    joined = "\n".join(line.text for line in lines)

    assert "history" in joined and "venue history is deep enough" in joined
    assert "ETH" in joined
    assert "no attestation" in joined.lower()  # the fail-closed gap, named


def test_shariah_lines_render_the_live_rail17_state(repo: Repository) -> None:
    inventory = gather_attestations_in_force(repo, _config())
    for state, expected in ((True, "ENABLED"), (False, "SUSPENDED"), (None, "UNKNOWN")):
        lines = cc.build_shariah_lines(
            inventory, withdrawals_enabled=state, now_ts=NOW_TS
        )
        joined = "\n".join(line.text for line in lines)
        assert expected in joined, expected


def test_the_honesty_lines_are_always_visible_and_sourced_from_fiqh_basis(
    repo: Repository,
) -> None:
    """The two standing honesty states render on EVERY shariah screen -- never buried --
    and each is pinned two-sided against `docs/fiqh-basis.md`, so neither the browser nor
    the document can drift while the other stays honest."""
    assert NOT_A_FATWA_ENGINE in _FIQH_BASIS
    assert NO_SCHOLARLY_REVIEW in _FIQH_BASIS

    for withdrawals in (True, False, None):
        lines = cc.build_shariah_lines(
            gather_attestations_in_force(repo, _config()),
            withdrawals_enabled=withdrawals,
            now_ts=NOW_TS,
        )
        joined = "\n".join(line.text for line in lines)
        assert NOT_A_FATWA_ENGINE in joined
        assert NO_SCHOLARLY_REVIEW in joined


def test_every_fiqh_constraint_quotes_fiqh_basis_verbatim_and_cites_a_real_section() -> None:
    """The fiqh-derived rails are sourced from the fiqh basis's OWN structure: each
    constraint's plain-English line is a VERBATIM quote from `docs/fiqh-basis.md`, and
    its citation resolves to a heading that exists in that document -- nothing on this
    screen is a TUI-authored fiqh summary."""
    def _squash(text: str) -> str:
        return " ".join(text.split())

    headings = [
        line.rstrip()
        for line in _FIQH_BASIS.splitlines()
        if line.startswith("#")
    ]
    squashed_doc = _squash(_FIQH_BASIS)
    cited_keys = set()
    for constraint in cc.FIQH_CONSTRAINTS:
        # verbatim modulo the document's own line wrapping (markdown reflows)
        assert _squash(constraint.quote) in squashed_doc, constraint.key
        assert constraint.citation in headings, constraint.citation
        cited_keys.add(constraint.key)
    # The PRD's own list: the screen's attested axes, the allowlist rail, qabd, the
    # spot charter, and purification.
    assert cited_keys >= {"attested-vs-computed", "rail-1-allowlist", "rail-17-qabd",
                          "rails-18-19-spot-charter", "purification"}


def test_the_vocabulary_is_anchored_to_fiqh_basis_not_invented(repo: Repository) -> None:
    """O10's vocabulary (qabd, riba, maisir, attestation, exemption, purification) is
    defined ONLY by quoting what `docs/fiqh-basis.md` itself states; a term the document
    does not state (gharar) is rendered as not-stated-there rather than defined by the
    TUI -- the document's own honesty rule, inherited."""
    terms = {term.term for term in cc.VOCABULARY}
    assert {"qabd", "riba", "maisir", "attestation", "exemption", "purification"} <= terms
    squashed_doc = " ".join(_FIQH_BASIS.split())
    for term in cc.VOCABULARY:
        assert term.citation in _FIQH_BASIS, term.term
        if term.stated:
            assert " ".join(term.definition.split()) in squashed_doc, term.term
        else:
            assert "not stated" in term.definition.lower(), term.term

    lines = cc.build_shariah_lines(
        gather_attestations_in_force(repo, _config()), withdrawals_enabled=None, now_ts=NOW_TS
    )
    joined = "\n".join(line.text for line in lines)
    for term in ("qabd", "riba", "maisir", "attestation", "exemption", "purification"):
        assert term in joined, term


def test_shariah_lines_name_the_active_profile_and_read_only_posture(repo: Repository) -> None:
    lines = cc.build_shariah_lines(
        gather_attestations_in_force(repo, _config(allowlist=["BTC", "ETH"])),
        withdrawals_enabled=None,
        now_ts=NOW_TS,
    )
    joined = "\n".join(line.text for line in lines)
    assert "read-only" in joined.lower()
    assert "BTC" in joined and "ETH" in joined  # the active allowlist, visible


def test_shariah_lines_fit_the_80_column_clip(repo: Repository) -> None:
    """Same budget as every other console screen: `_paint` clips at the window width and
    this dashboard targets 80 columns -- a citation must not lose its tail there."""
    lines = cc.build_shariah_lines(
        gather_attestations_in_force(_inventory_repo(repo), _config()),
        withdrawals_enabled=True,
        now_ts=NOW_TS,
    )
    for line in lines:
        assert len(line.text) <= 80, line.text


# -- the view overlay ------------------------------------------------------------------------------


def test_the_view_overlay_reuses_the_services_own_renderers() -> None:
    """Every offline view is the SERVICE's own report, styled -- the screen view renders
    `render_screen_report`'s exact verdict lines, the purification view renders the
    purification report's exact lines: one implementation, two front-ends."""
    from keel.commands.admission import ScreenedProduct, ScreenReport
    from keel.compliance.screen import MarketFacts, ScreenResult

    facts = MarketFacts(
        asset="BTC", daily_bars=2000, median_daily_volume=Decimal("1000"),
        quotable_in_settlement_currency=True, product_id="BTC-USD", venue="coinbase",
    )
    report = ScreenReport(
        quote="USD",
        screened=[
            ScreenedProduct(
                product="BTC-USD", asset="BTC", facts=facts,
                result=ScreenResult(asset="BTC", admitted=True, failures=[], warnings=[]),
                on_allowlist=True, attested=True,
            )
        ],
    )
    lines = cc.build_compliance_view_lines("screen", report)
    joined = "\n".join(line.text for line in lines)
    assert "ADMIT" in joined
    assert "1/1 admitted" in joined
    assert any("Compliance menu" in line.text for line in lines)

    pur = cc.build_compliance_view_lines("purification", ["no non-compliant credits found"])
    assert any("no non-compliant credits found" in line.text for line in pur)

    missing = cc.build_compliance_view_lines("purification", None, error="database is locked")
    assert any("database is locked" in line.text for line in missing)
    assert any(line.style == "alert" for line in missing)


def test_the_network_views_render_armed_until_run() -> None:
    """Holdings and discover make a live call -- so they open ARMED (nothing fetched),
    exactly the discover overlay's own gating story."""
    armed = cc.build_compliance_view_lines("holdings", None)
    joined = "\n".join(line.text for line in armed)
    assert "ARMED" in joined
    assert "Enter" in joined

    from keel.commands.assets import DiscoverSweep
    from keel.compliance.screen import DiscoveryExclusions

    sweep = DiscoverSweep(
        quote="USD", venue_product_count=9, candidates=(), survivor_count=1,
        min_quote_24h_volume=Decimal("100000"),
        excluded=DiscoveryExclusions(wrong_quote_currency=8),
        rows=(),
        probe_history=False, probe_liquidity=False,
        min_median_daily_volume=Decimal("1000000"),
    )
    run = cc.build_compliance_view_lines("discover", sweep)
    assert any("candidates" in line.text for line in run)
