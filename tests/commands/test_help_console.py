"""Tests for `keel.commands.help_console` -- the O8 help & glossary system (issue #394 C7).

Three surfaces, pinned here:

* **The glossary** -- `docs/glossary.md` is the ONE hand-written home for console term
  definitions. The TUI help renders it (bounded read, mtime-cached like the research
  readers), the fiqh terms' definitions are ANCHORED to `docs/fiqh-basis.md` (verbatim
  quotes, or an honest "not stated" where the document is silent, exactly as C3's shariah
  screen handles gharar), and no other surface defines console terms (the shariah screen's
  vocabulary is pinned BYTE-EQUAL to the glossary here, so the two cannot drift).
* **Parameter help** -- the help system does NOT duplicate `describe_params`: it renders
  that service's own doc strings, types, defaults and choices for every rule kind.
* **The contextual help registry** -- every console mode contributes "what am I looking
  at" / "what will this do" text from the module that owns the screen, reachable with `?`
  from every mode, and the typed actions' help says the prompt cannot be pre-filled.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from keel import agent
from keel.commands import compliance_console as cc
from keel.commands import help_console as hc
from keel.commands import tui
from keel.commands.rules import describe_params

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIQH_BASIS = (_REPO_ROOT / "docs" / "fiqh-basis.md").read_text(encoding="utf-8")

#: The console's needed vocabulary, as the mission statement of this slice names it: what
#: the screens render, what the gates demand, what the fiqh terms mean, what the data
#: concepts are. A term missing from the glossary is a screen a newcomer cannot read.
_NEEDED_TERMS = {
    "rail",
    "attestation",
    "exemption",
    "screening",
    "promotion gate",
    "paper mode",
    "live mode",
    "kill switch",
    "autonomy",
    "qabd",
    "riba",
    "gharar",
    "maysir",
    "purification",
    "session-bound venue",
    "market clock",
    "trust window",
    "DCA benchmark",
    "granularity",
    "trials ledger",
}


def _terms() -> dict[str, hc.GlossaryTerm]:
    return {t.term: t for t in hc.load_glossary()}


# -- the glossary: ONE source, every needed term --------------------------------------------------


def test_the_glossary_file_exists_and_defines_every_needed_term_once() -> None:
    terms = hc.load_glossary()
    names = [t.term for t in terms]
    assert len(set(names)) == len(names), "duplicate glossary terms"
    missing = _NEEDED_TERMS - set(names)
    assert not missing, missing
    # and it is THE file: the loader's default path is the repo's docs/glossary.md
    assert hc.GLOSSARY_PATH.name == "glossary.md"
    assert hc.GLOSSARY_PATH.is_file()


def test_every_glossary_definition_is_nonempty_and_points_at_a_source() -> None:
    for term in hc.load_glossary():
        assert term.definition.strip(), term.term
        assert term.source.strip(), term.term
        assert term.citation is None or term.citation in _FIQH_BASIS, term.term


def test_fiqh_definitions_are_anchored_verbatim_to_fiqh_basis() -> None:
    """The anchoring rule: a fiqh term's definition is a VERBATIM passage of
    docs/fiqh-basis.md (whitespace-normalized), carrying the document's own section as its
    citation -- never a help-authored summary that could drift from the document."""
    squashed = " ".join(_FIQH_BASIS.split())
    for name in (
        "qabd",
        "riba",
        "maisir",
        "purification",
        "attestation",
        "exemption",
        "screening",
        "instrument attestation",
    ):
        term = _terms()[name]
        assert term.fiqh, name
        assert term.stated, name
        assert term.citation in _FIQH_BASIS, name
        assert " ".join(term.definition.split()) in squashed, name


def test_two_fiqh_definitions_spot_pinned_word_for_word() -> None:
    """Two spot-pins, word for word, from the document itself -- the two-sided pin style
    of `tests/test_fiqh_basis.py`: the sentence must be IN fiqh-basis.md and the glossary
    must carry it unchanged."""
    qabd = _terms()["qabd"]
    assert qabd.definition == (
        "possession is the ability to dispose, not physical custody"
    )
    riba = _terms()["riba"]
    assert riba.definition == (
        "Coinbase pays USDC rewards on idle balances, that interest is riba, and it "
        "accrues with no order placed"
    )
    squashed = " ".join(_FIQH_BASIS.split())
    for definition in (qabd.definition, riba.definition):
        assert definition in squashed  # squashed: the doc hard-wraps mid-sentence


def test_gharar_is_honestly_not_stated_in_fiqh_basis_like_c3_rendered_it() -> None:
    gharar = _terms()["gharar"]
    assert gharar.fiqh
    assert not gharar.stated
    assert "not stated" in gharar.definition.lower()


def test_the_rail_entry_counts_eighteen_and_cites_the_table_honestly() -> None:
    """[review #406] SAFETY-CRITICAL honesty pin. The glossary is the console's single
    source of vocabulary, and this entry claimed NINETEEN rails while both authorities
    say eighteen -- `keel/execution/guards.py` ("eighteen in all, since there is no
    rail 15") and docs/fiqh-basis.md ("Eighteen rails exist (1-14, 16, 17, 18, 19 --
    there is no rail 15)"). The Source line must not overclaim either: the fiqh-basis
    rails TABLE enumerates the prudential rails (2-14, 16); rails 1, 17, 18 and 19 are
    stated in that document's own prose sections, and the citation says so."""
    rail = _terms()["rail"]
    definition = " ".join(rail.definition.split())
    assert "Eighteen exist" in definition
    assert "1-14, 16, 17, 18, 19" in definition
    assert "nineteen" not in definition.lower()
    source = " ".join(rail.source.split())
    assert "nineteen" not in source.lower()
    # the honest split: the table's coverage is named as the prudential rails, and the
    # prose-only rails are named as prose -- not folded into "the table enumerates all".
    assert "2-14" in source
    assert "prose" in source


def test_the_promotion_gate_entry_states_the_real_gate() -> None:
    """[review #406] SAFETY-CRITICAL honesty pin. The entry previously invented a
    DCA-benchmark floor (there is none in the promote path -- the DCA comparison lives
    in the simulate report), hid two of the four real floors, and described pooling as
    kind-wide when `keel/strategy/promotion.py` pools per parameter SET, on the
    sample-size axis only, with the overfitting gate explicitly NOT pooled."""
    gate = _terms()["promotion gate"]
    d = " ".join(gate.definition.split()).lower()
    # the four performance floors, by their own names
    for floor in ("min_trades", "min_expectancy", "min_rr", "min_win_rate"):
        assert floor in d, floor
    # the overfitting gate is the PBO/degradation-slope CONJUNCTION, not a bare bound
    assert "pbo" in d
    assert "degradation slope" in d
    # pooling's real scope, in promotion.py's own docstring wording
    assert "parameter set" in d
    assert "sample-size" in d
    assert "not pooled" in d
    # the DCA comparison lives in the simulate report, and is not a floor of this gate
    assert "dca" in d
    assert "simulate" in d
    assert "not a floor" in d


def test_the_glossary_agrees_with_the_shariah_screens_vocabulary() -> None:
    """No drifted duplicates: C3's shariah screen renders its vocabulary from its own
    anchored quotes; every one of those terms must carry the SAME definition in the ONE
    glossary, pinned here so neither surface can quietly redefine a fiqh term."""
    terms = _terms()
    for term in cc.VOCABULARY:
        assert term.term in terms, term.term
        assert " ".join(terms[term.term].definition.split()) == " ".join(
            term.definition.split()
        ), term.term


def test_the_glossary_read_is_bounded() -> None:
    """The glossary rides the SAME bounded reader the research corpus uses -- a runaway
    writer's megabyte file can never make the help screen read it whole (the bound itself
    is pinned by the research readers' own tests; this pins that the glossary uses it)."""
    assert hc.MAX_GLOSSARY_BYTES == 1024 * 1024


def test_the_glossary_is_cached_per_mtime(tmp_path: Path) -> None:
    """Repaints do not re-read an unchanged file; a changed mtime refreshes -- the
    research doc-view cache's contract, applied to the glossary."""
    path = tmp_path / "glossary.md"
    path.write_text(
        "## rail\none of keel's per-order guards\nSource: keel's own vocabulary\n",
        encoding="utf-8",
    )
    cache: dict[tuple[str, int], list[hc.GlossaryTerm]] = {}
    first = hc.cached_glossary(path, cache)
    assert [t.term for t in first] == ["rail"]
    # an unchanged mtime re-uses the cache (identity, not equality)
    assert hc.cached_glossary(path, cache) is first
    # a rewrite (newer mtime) refreshes
    path.write_text(
        "## rail\none of keel's per-order guards\nSource: keel's own vocabulary\n\n"
        "## qabd\npossession is the ability to dispose, not physical custody\n"
        'Source: docs/fiqh-basis.md -- "### Rail 17"\n',
        encoding="utf-8",
    )
    stamp = path.stat()
    import os

    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
    second = hc.cached_glossary(path, cache)
    assert [t.term for t in second] == ["rail", "qabd"]


def test_parse_glossary_is_pure_over_text() -> None:
    terms = hc.parse_glossary(
        "# The keel glossary\n\nintro prose is ignored.\n\n"
        "## rail\none of keel's per-order guards.\nSource: keel's own vocabulary\n\n"
        "## qabd\npossession is the ability to dispose,\nnot physical custody.\n"
        "Source: docs/fiqh-basis.md -- \"### Rail 17 -- withdrawal capability\"\n"
    )
    assert [t.term for t in terms] == ["rail", "qabd"]
    assert terms[0].definition == "one of keel's per-order guards."
    assert not terms[0].fiqh
    # a wrapped definition is joined; a fiqh-basis source anchors the term
    assert terms[1].definition == (
        "possession is the ability to dispose, not physical custody."
    )
    assert terms[1].fiqh
    assert terms[1].citation == "### Rail 17 -- withdrawal capability"


def test_an_absent_glossary_renders_a_calm_empty_state(tmp_path: Path) -> None:
    """A deployment runs from an installed wheel with no docs/ checkout -- the help screen
    must say the glossary file is absent and where it lives, never traceback."""
    missing = tmp_path / "no-such-glossary.md"
    lines = hc.build_glossary_lines(hc.load_glossary(missing), path=missing)
    joined = " ".join(" ".join(line.text for line in lines).split())
    assert "glossary" in joined.lower()
    # A long path wraps mid-word (no hyphen), so the containment check concatenates
    # the wrapped rows with NO separator: the wrap is layout, not content.
    assert str(missing) in "".join(line.text for line in lines)
    assert all(len(line.text) <= 80 for line in lines)


def test_the_glossary_view_renders_every_term_wrapped_to_the_budget() -> None:
    lines = hc.build_glossary_lines(hc.load_glossary())
    joined = "\n".join(line.text for line in lines)
    for term in hc.load_glossary():
        assert term.term in joined, term.term
    assert all(len(line.text) <= 80 for line in lines)


# -- parameter help: LINKS to describe_params, never duplicates -----------------------------------


def test_params_help_renders_describe_params_own_doc_strings() -> None:
    """The O8 single-source rule, pinned: the help view for a kind carries
    `describe_params`' ACTUAL doc strings, defaults, types and choices -- introspected
    from the rule classes. A duplicated table would fail the moment a class changed."""
    lines = hc.build_params_help_lines("turtle_breakout")
    squashed = " ".join(" ".join(line.text for line in lines).split())
    params = describe_params("turtle_breakout")
    assert set(params) == {
        "granularity",
        "entry_lookback",
        "exit_lookback",
        "adx_period",
        "adx_threshold",
        "atr_period",
        "atr_stop_mult",
        "use_macd_confirm",
        "s1_filter",
        "min_volume_filter",
        "volume_ma_period",
        "volume_mult",
        "target_rr",
    }
    for name, help_ in params.items():
        assert name in squashed, name
        assert " ".join(help_.doc.split()) in squashed, name
        assert help_.type_name in squashed or name == "granularity", name


def test_params_help_names_its_single_source() -> None:
    joined = " ".join(
        " ".join(line.text for line in hc.build_params_help_lines("dca")).split()
    )
    assert "describe_params" in joined or "the rule classes" in joined


def test_params_kinds_view_lists_every_rule_kind() -> None:
    lines = hc.build_params_kinds_lines()
    joined = "\n".join(line.text for line in lines)
    for kind in sorted(agent.RULE_REGISTRY):
        assert kind in joined, kind
    assert all(len(line.text) <= 80 for line in lines)


def test_params_help_for_an_unknown_kind_is_a_calm_refusal() -> None:
    lines = hc.build_params_help_lines("no_such_kind")
    joined = "\n".join(line.text for line in lines)
    assert "unknown rule kind" in joined
    assert all(len(line.text) <= 80 for line in lines)


# -- the contextual help registry (O8) ------------------------------------------------------------


def test_every_console_mode_contributes_contextual_help() -> None:
    """`?` is reachable from EVERY console mode and renders that mode's own contribution:
    the registry is closed (a mode without an entry fails here, not silently in the TUI),
    each entry is one subject plus a plain-English description, and the rendering fits the
    80-column budget."""
    assert hc.CONSOLE_MODES, "the registry is empty"
    for mode in hc.CONSOLE_MODES:
        entries = hc.contextual_help(mode)
        assert entries, mode
        assert all(e.subject.strip() and e.description.strip() for e in entries), mode
        lines = hc.build_context_help_lines(mode)
        assert len(lines) > 1, mode
        assert all(len(line.text) <= 80 for line in lines), mode
        squashed = " ".join(" ".join(row.text for row in lines).split())
        for entry in entries:
            assert entry.subject in squashed, (mode, entry.subject)


def test_the_registry_covers_every_mode_the_live_loop_dispatches_on() -> None:
    """No dead key and no dead registry row: every `mode ==` literal in `run_live`'s
    dispatch is in the registry (or is the overlay/help machinery itself), and every
    registry key is a real mode."""
    source = inspect.getsource(tui.run_live)
    dispatched = set(re.findall(r'mode == "([a-z-]+)"', source))
    unregistered = dispatched - set(hc.CONSOLE_MODES)
    assert not unregistered, unregistered
    unknown = set(hc.CONSOLE_MODES) - dispatched
    assert not unknown, unknown


def _dispatched_modes(test: ast.expr) -> list[str]:
    """The `mode == "..."` constants an `if` test carries (`mode == "x" and guard`
    included) -- an `if` whose test names none is not a dispatch branch. PURE."""
    if (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "mode"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and isinstance(test.comparators[0].value, str)
    ):
        return [test.comparators[0].value]
    if isinstance(test, ast.BoolOp):
        return [m for value in test.values for m in _dispatched_modes(value)]
    return []


def test_the_question_key_is_handled_in_every_console_mode_branch() -> None:
    """The `?` overlay is reachable from EVERY console mode -- pinned structurally: each
    `mode ==` branch of `run_live` must handle `ord("?")` (open the overlay, or close it
    for the overlay's own mode), so a future mode cannot silently ship without it.

    [review #406] The extraction is AST-based, and each `If` node's TRUE span
    (`lineno`..`end_lineno`) is the block. The previous regex scan never stopped at a
    dedent, so the last block silently absorbed every line after it -- including the
    normal mode's own `?` handler -- and a mode that shipped without the key (discover,
    in that review) passed this test dishonestly. Nothing outside a branch's own span
    can satisfy the assertion now."""
    source = inspect.getsource(tui.run_live)
    lines = source.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.If):
            for mode in _dispatched_modes(node.test):
                blocks.append((mode, lines[node.lineno - 1 : node.end_lineno]))
    assert len(blocks) >= 20, "the scan found no dispatch blocks -- it has rotted"
    for mode, body in blocks:
        assert any('ord("?")' in line for line in body), mode


def test_the_typed_actions_help_says_the_prompt_cannot_be_pre_filled() -> None:
    """O3's contract stated in O8's words: every typed action's help text says explicitly
    that the prompt cannot be pre-filled -- the console never pipes, pre-fills or
    bypasses."""
    joined = " ".join(
        entry.description for mode in hc.CONSOLE_MODES for entry in hc.contextual_help(mode)
    )
    assert joined.count("cannot be pre-filled") >= 4, joined
    # and specifically on the screens that carry the typed gates
    for mode in ("trading", "compliance", "strategy"):
        mode_text = " ".join(e.description for e in hc.contextual_help(mode))
        assert "cannot be pre-filled" in mode_text, mode


def test_record_flow_and_reset_hwm_help_disclose_their_own_typed_gates() -> None:
    """[review #406] record-flow and reset-hwm ARE typed gates in the CLI
    (`_require_interactive_confirmation`, exactly like resume/resume-entries), so their
    help row must scope the typed disclosure to ALL three actions it names -- not leave
    it attached to resume-entries alone, which read as though the other two could be
    pre-filled."""
    row = next(
        e
        for e in hc.contextual_help("trading")
        if e.subject == "resume-entries, reset-hwm, record-flow"
    )
    lowered = row.description.lower()
    assert "cannot be pre-filled" in lowered
    # the disclosure LEADS the row, before any per-action sentence, so it reads as the
    # rule for all three -- not as resume-entries' parenthetical (which left the other
    # two reading as pre-fillable)
    assert lowered.index("cannot be pre-filled") < lowered.index("resume-entries")
    for action in ("resume-entries", "reset-hwm", "record-flow"):
        assert action in lowered, action


def test_the_help_menu_lists_the_prd_help_branch() -> None:
    """The Help menu becomes real: glossary, the per-screen/per-action catalog, parameter
    help and the keys/safety notes -- the PRD §3 Help branch."""
    labels = [entry.label for entry in hc.HELP_MENU]
    assert labels == ["glossary", "screens & actions", "rule parameters", "keys & safety"]
    lines = hc.build_help_menu_lines()
    joined = "\n".join(line.text for line in lines)
    for label in labels:
        assert label in joined, label
    assert all(len(line.text) <= 80 for line in lines)


def test_the_screens_catalog_renders_every_modes_contribution() -> None:
    lines = hc.build_screens_catalog_lines()
    joined = "\n".join(line.text for line in lines)
    for mode in hc.CONSOLE_MODES:
        assert mode in joined, mode
    assert all(len(line.text) <= 80 for line in lines)
