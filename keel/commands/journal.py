"""The discretionary journal: what the operator says about their own conduct (#705).

The `journal` table has been in the schema from the beginning with no repository method and no
caller -- dead schema, which is worse than no schema because a reader assumes a declared table is
a used one. This is its wiring: a CLI that writes it, a report both front-ends read, and a place
in the audit chain beside everything else a human swore to.

── WHY THIS RECORD IS DIFFERENT FROM EVERY OTHER RECORD HERE ────────────────────────────────────

Everything else keel keeps is either a machine's observation or a claim about the world. An order
is what a venue reported. A transaction is a line out of a venue's own export. An asset
attestation says PAXG is backed by allocated gold -- a claim a prospectus could contradict.

A journal entry has no external referent at all. "I felt rushed", "I broke my own rule", "it cost
me forty dollars" cannot be checked against anything, ever. That is not a defect: self-assessment
is the only way this information exists, and no competitor keeps it because no venue can produce
it. But it means the record's value depends entirely on it staying visibly separate from the ones
that can be checked -- hence `SELF_REPORTED`, which is a sixth provenance in the timeline's closed
vocabulary rather than a reuse of `human-attested`.

── THE CLI IS THE ONLY WAY IN ───────────────────────────────────────────────────────────────────

Attestations are human-sourced or refused. `keel journal add` prompts, requires a terminal, and
accepts NO value options -- not merely "it prompts by default". A `--emotion 3` would make the
whole entry scriptable, and the TTY gate would then be guarding a ceremony that no longer needed a
human to supply anything. There is no web write path, and a test asserts it over
`keel.commands.setup.ACTIONS` -- the only surface `server.do_POST` will route to, and one that
already carries an attestation writer (`attest_asset`), which is precisely why a journal box is
the plausible next addition.

**WHAT THE TERMINAL CHECK IS AND IS NOT.** `sys.stdin.isatty()` refuses a pipe, a redirect and a
cron job as ordinarily written. It does NOT refuse a determined script: a `pty.fork()` driver
allocates a real terminal and feeds the prompts, and this command answers it. That is true of
every gate in this codebase built on the same predicate, and it is the honest boundary -- the
check makes automated entry a thing someone has to MEAN, not a thing they can do by accident. A
record whose whole value is that a person wrote it cannot be enforced by software beyond that.

── AND THERE IS NO EDIT ─────────────────────────────────────────────────────────────────────────

Append-only, with no update method anywhere. A journal you can go back and change is a journal
that records what you wish you had thought.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import click

from keel.data.repository import Repository

#: The word that must appear beside every entry, on every surface. Not "journal" and not
#: "attested": the reader has to know that nothing outside this operator's head produced it.
SELF_REPORTED = "SELF-REPORTED"

#: The emotion scale, stated in the prompt and enforced on the way in.
#:
#: A "score" with no scale cannot be compared with itself next week, which is the only thing an
#: emotion column is for. 1-5 rather than free text, and a value off the scale is REFUSED rather
#: than quietly kept -- a journal holding "9", "very bad" and "3" in one column has three
#: vocabularies and no series.
EMOTION_MIN = 1
EMOTION_MAX = 5

#: What `keel journal list` says over an empty table. Distinct from a bare header, which reads as
#: a table that failed to load rather than a deployment nobody has written in yet.
EMPTY_NOTE = "No journal entries yet. `keel journal add` writes one, and only a person can."

#: What the TTY gate announces. Named here so the command and its test cannot disagree about
#: what the operator is being asked to confirm.
JOURNAL_ADD_ACTION = "write a journal entry"


@dataclass(frozen=True)
class JournalEntry:
    """One entry, as both front-ends read it.

    Every field but `id`/`ts` is optional, and `None` means DID NOT SAY -- never a zero, an empty
    string, or a `False`. `rules_followed` in particular is three-valued: `False` is the operator
    confessing they broke their own rules, and nobody should make that confession by leaving a
    prompt blank.
    """

    id: int
    ts: int
    emotion_score: str | None
    rules_followed: bool | None
    errors_made: str | None
    dollar_impact: Decimal | None
    chart_note: str | None
    screenshot_ref: str | None


#: How many entries the console shows without being asked for more.
#:
#: The journal has its OWN cap and is deliberately not paged by `/api/journal`'s `?limit=`. That
#: parameter is the closed-trade table's page control; applying it here was a coincidence of the
#: two records sharing a route, and it meant narrowing to one trade silently hid 300 of an
#: operator's 301 notes.
DEFAULT_NOTES_LIMIT = 50


@dataclass(frozen=True)
class JournalReport:
    now_ts: int
    entries: tuple[JournalEntry, ...]
    #: How many entries the window holds BEFORE `limit` truncated `entries`.
    #:
    #: Carried, not derived, because a caller that bounds a read is showing a WINDOW of the record
    #: and must say so -- the rule `Repository.get_equity_points` states and `count_equity_points`
    #: exists to serve. The first cut shipped `shown_count` alone, so a capped journal was
    #: indistinguishable on the page from a complete one.
    total_count: int = 0

    @property
    def entry_count(self) -> int:
        """Derived rather than stored, and held here because `keel/web/payload.py` may not call
        `len()` (Rule 6e)."""
        return len(self.entries)

    @property
    def any_recorded(self) -> bool:
        """Whether this deployment has a journal at all.

        Reads `total_count`, not `entries`: a window that returned nothing because a cap or a date
        bound excluded everything is not a deployment with no journal, and the renderers say
        different things about the two.
        """
        return self.total_count > 0

    @property
    def truncated(self) -> bool:
        """Whether this report is a PAGE of a longer journal. What the page says "50 of 301"
        from, and the flag a renderer needs to say anything at all rather than showing a short
        list that looks complete."""
        return self.total_count > self.entry_count


def gather_journal(repo: Repository, *, now_ts: int, limit: int | None = None) -> JournalReport:
    """Every entry, oldest first -- a journal reads forwards.

    `limit` keeps the NEWEST entries and still returns them forwards, so a cap changes how much of
    the journal a reader sees and never which way it reads. `total_count` comes off a separate
    COUNT over the same window, so the report always knows what the cap left out.

    A NEGATIVE OR ZERO LIMIT IS REFUSED rather than obeyed. SQLite reads a negative `LIMIT` as
    unbounded, so `--limit -1` would silently print everything; a zero returns no rows, and an
    empty result is indistinguishable from an empty journal on both front-ends -- which is exactly
    the hazard `keel/web/api.py::_journal_limit` was written to name. Refusing is the only reading
    that cannot lie.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be 1 or more (or omitted for all); got {limit}")
    entries = tuple(
        JournalEntry(
            id=int(row["id"]),
            ts=int(row["ts"]),
            emotion_score=_optional_text(row.get("emotion_score")),
            rules_followed=row.get("rules_followed"),
            errors_made=_optional_text(row.get("errors_made")),
            dollar_impact=row.get("dollar_impact"),
            chart_note=_optional_text(row.get("chart_note")),
            screenshot_ref=_optional_text(row.get("screenshot_ref")),
        )
        for row in repo.get_journal_entries(limit=limit)
    )
    return JournalReport(now_ts=now_ts, entries=entries, total_count=repo.count_journal_entries())


def _optional_text(value: object) -> str | None:
    """`None` for absent AND for empty, because a column holding `""` says nothing a `NULL` does
    not, and two spellings of "did not say" would render as two different states."""
    if value is None:
        return None
    text = str(value)
    return text or None


# -- the prompts -------------------------------------------------------------------------------
#
# Each returns `None` for a blank answer and RAISES for an answer it cannot honour. Refusing is
# the right response to "9" on a 1-5 scale or to "lots" as a dollar figure: storing either would
# put a value in the record that the operator did not mean and cannot be compared with the rest.


def parse_emotion(raw: str) -> str | None:
    """A 1-5 score, as the digit it will be stored as, or `None` for a blank answer."""
    text = raw.strip()
    if not text:
        return None
    try:
        score = int(text)
    except ValueError:
        raise click.ClickException(
            f"emotion score must be a whole number from {EMOTION_MIN} to {EMOTION_MAX} "
            f"(or blank to skip); got {text!r}"
        ) from None
    if not EMOTION_MIN <= score <= EMOTION_MAX:
        raise click.ClickException(
            f"emotion score must be from {EMOTION_MIN} to {EMOTION_MAX} (or blank to skip); "
            f"got {score}"
        )
    return str(score)


def parse_rules_followed(raw: str) -> bool | None:
    """`y`/`n`, or `None` for a blank answer.

    Blank is NOT `False`. "I broke my rules" is the single most consequential sentence in this
    table, and an operator who skipped the question has not said it.
    """
    text = raw.strip().lower()
    if not text:
        return None
    if text in ("y", "yes"):
        return True
    if text in ("n", "no"):
        return False
    raise click.ClickException(f"answer y or n (or blank to skip); got {raw.strip()!r}")


def parse_impact(raw: str) -> Decimal | None:
    """A signed dollar figure as `Decimal`, or `None` for a blank answer.

    `Decimal`, like every other money value here, and REFUSED rather than coerced: a journal whose
    dollar column holds "lots" cannot be summed, and one that silently read it as zero would say
    the day cost nothing.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise click.ClickException(
            f"dollar impact must be a number like -42.50 (or blank to skip); got {text!r}"
        ) from None


def render_human(report: JournalReport) -> str:
    """The terminal rendering. Chronological, with the provenance marker on the header.

    The marker is not decoration. `keel insights journal` already exists and is a filterable view
    of closed TRADES -- venue facts, a different thing wearing a similar name -- and an operator
    reading one after the other must not have to remember which is which.
    """
    lines = [f"journal ({SELF_REPORTED} — the operator's own account, nothing verified it)", ""]
    if not report.any_recorded:
        lines.append(EMPTY_NOTE)
        return "\n".join(lines)

    for entry in report.entries:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime(entry.ts))
        lines.append(f"[{entry.id}] {stamp} UTC")
        lines.append(f"  emotion       : {entry.emotion_score or 'not said'}")
        lines.append(f"  rules followed: {_rules_word(entry.rules_followed)}")
        lines.append(f"  errors made   : {entry.errors_made or 'not said'}")
        lines.append(f"  dollar impact : {_impact_word(entry.dollar_impact)}")
        lines.append(f"  chart note    : {entry.chart_note or 'not said'}")
        lines.append(f"  screenshot    : {entry.screenshot_ref or 'not said'}")
        lines.append("")
    if report.truncated:
        lines.append(f"{report.entry_count} of {report.total_count} entries (newest).")
    else:
        lines.append(f"{report.entry_count} entr{'y' if report.entry_count == 1 else 'ies'}.")
    return "\n".join(lines)


def _rules_word(value: bool | None) -> str:
    """Three words for three values. "no" is a confession and must never be what "not said"
    prints as."""
    if value is None:
        return "not said"
    return "yes" if value else "NO"


def _impact_word(value: Decimal | None) -> str:
    return "not said" if value is None else format(value, "f")


# -- the CLI -----------------------------------------------------------------------------------


@click.group("journal")
def journal_group() -> None:
    """Your own account of your own trading -- what you felt, whether you followed your rules.

    NOT `keel insights journal`, which is a filterable view of closed TRADES: venue facts, a
    different thing wearing a similar name. Nothing here was verified by anything.
    """


@journal_group.command("add")
@click.pass_context
def journal_add(ctx: click.Context) -> None:
    """Write one entry. Prompts for every field; blank skips it.

    Needs a terminal, and takes no value options -- see the module docstring. Every question may
    be skipped, including all of them: an operator recording one sentence about one day should not
    have to invent an emotion score to do it.
    """
    from keel.commands._common import _is_interactive, _open_repo

    # `_is_interactive`, NOT `_require_interactive_confirmation`. The heavier gate demands a typed
    # `yes` and exists for DANGEROUS actions -- releasing a kill-switch, spending money -- and its
    # own docstring warns against ceremony without a matching threat model. Writing a sentence
    # about your own trading is not dangerous; it is unverifiable, which is a different problem
    # and one a confirmation prompt does nothing about.
    #
    # What IS load-bearing is the terminal. The constitution's rule is that an attestation is
    # human-sourced or refused, and off a TTY there is no human -- so cron, a pipe and a script
    # are all refused here, using the same predicate the heavier gate is built on. That predicate
    # has no env-var or flag override, deliberately, so nothing can reach past it.
    if not _is_interactive():
        raise click.ClickException(
            f"refusing to {JOURNAL_ADD_ACTION}: this needs an interactive terminal. "
            f"A journal entry is {SELF_REPORTED} by definition -- there is no other source it "
            "could come from, so there is no way to supply one from a script."
        )
    click.echo(f"{SELF_REPORTED} — nothing verifies this, and it cannot be edited afterwards.")
    click.echo("Every question may be skipped; a blank answer records that you did not say.")

    emotion = parse_emotion(
        click.prompt(f"emotion ({EMOTION_MIN}-{EMOTION_MAX})", default="", show_default=False)
    )
    rules = parse_rules_followed(
        click.prompt("did you follow your rules? (y/n)", default="", show_default=False)
    )
    errors = click.prompt("errors made", default="", show_default=False).strip()
    impact = parse_impact(click.prompt("dollar impact", default="", show_default=False))
    note = click.prompt("chart note", default="", show_default=False).strip()
    shot = click.prompt("screenshot reference", default="", show_default=False).strip()

    repo = _open_repo(ctx)
    entry_id = repo.append_journal_entry(
        ts=int(time.time()),
        emotion_score=emotion,
        rules_followed=rules,
        errors_made=errors or None,
        dollar_impact=impact,
        chart_note=note or None,
        screenshot_ref=shot or None,
    )
    click.echo(f"recorded journal entry {entry_id} ({SELF_REPORTED}).")


@journal_group.command("list")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=None,
    help="Show only the most recent N entries. They still read forwards.",
)
@click.pass_context
def journal_list(ctx: click.Context, limit: int | None) -> None:
    """Read the journal back, oldest first."""
    from keel.commands._common import _open_repo

    report = gather_journal(_open_repo(ctx), now_ts=int(time.time()), limit=limit)
    click.echo(render_human(report))
