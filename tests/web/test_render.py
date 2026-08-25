"""The pure renderers (#435).

The rendering layer's one security-relevant property is that nothing reaching the page is trusted
markup. Rule names, product ids, log highlights and adapter error strings all originate outside
this process -- a rule name is operator-supplied, a highlight is engine-supplied, and an adapter
error is whatever a third-party package chose to raise.
"""

from __future__ import annotations

from decimal import Decimal

from keel.commands.brokers import BrokerInfo
from keel.web import render

XSS = '<script>alert("x")</script>'


def test_escaping_covers_every_field_a_caller_can_influence() -> None:
    rows = [{"id": 1, "kind": XSS, "status": XSS, "params": XSS}]
    html = render.render_rules(rows)
    assert "<script" not in html.lower()
    assert "&lt;script&gt;" in html


def test_an_adapter_error_string_is_escaped() -> None:
    """The one field on a broker row that is verbatim third-party text."""
    info = BrokerInfo(
        name="broken",
        venue="",
        deployment="",
        session_bound=False,
        quote_currencies=(),
        asset_classes=(),
        supported_orders=(),
        preview="none",
        supports_fee_summary=False,
        declared_endpoints=(),
        supported_data_feeds=(),
        package_version=None,
        error=XSS,
    )
    html = render.render_venues([info])
    assert "<script" not in html.lower()
    assert "adapter failed to construct" in html


def test_a_failed_adapter_row_shows_the_error_and_not_the_placeholders() -> None:
    """A row for an adapter that could not be constructed has declared NOTHING, so every
    capability cell on it would be a placeholder rather than a fact. `keel brokers list` renders
    only the error block for such a row; the web view must do the same or it invents claims."""
    info = BrokerInfo(
        name="broken",
        venue="pretend-venue",
        deployment="WIRED",
        session_bound=False,
        quote_currencies=(),
        asset_classes=(),
        supported_orders=(),
        preview="none",
        supports_fee_summary=False,
        declared_endpoints=(),
        supported_data_feeds=(),
        package_version=None,
        error="boom",
    )
    html = render.render_venues([info])
    assert "pretend-venue" not in html
    assert "WIRED" not in html


# `test_a_fiqh_term_that_fiqh_basis_does_not_state_says_so` lived here and went with
# `render_glossary` at #539. The property it protected did NOT go: the "not stated" disclaimer is
# written into the definition text in `docs/glossary.md` itself -- which is why
# `help_console.parse_glossary` can DERIVE `stated` from it (`stated = "not stated" not in
# source.lower()`), and why `tests/commands/test_help_console.py` asserts it on the gharar entry.
# A reader following the deep link lands on that prose. What was deleted is a renderer for a file
# no installed deployment has ever had.


def test_utc_is_used_and_a_broken_timestamp_does_not_raise() -> None:
    """Every day boundary in keel is UTC; rendering in local time is what made the activity feed
    show a stale date (#381). And a corrupt timestamp in a log line must render as a dash, not
    take the page down."""
    assert render.utc(0) == "1970-01-01 00:00:00"
    assert render.utc(None) == "--"
    assert render.utc(float("nan")) == "--"
    assert render.utc(1e30) == "--"


def test_age_is_coarse_and_total() -> None:
    assert render.age(None, 100) == ""
    assert render.age(100, None) == ""
    assert render.age(100, 130) == "30s ago"
    assert render.age(0, 3600) == "60m ago"
    assert render.age(0, 86400 * 3) == "3d ago"
    assert render.age(200, 100) == "in the future"


def test_money_and_pct_render_absence_as_a_dash_not_a_zero() -> None:
    """`None` means "not recorded"; `0` means "recorded as zero". Collapsing the first into the
    second is the shape of the always-passing fee rail (#198) -- a missing number that reads as a
    real one."""
    assert render.money(None) == "--"
    assert render.money(Decimal("0")) == "0.00"
    assert render.pct(None) == "--"
    assert render.pct(Decimal("0")) == "0.00%"
    assert render.money(Decimal("1234.5")) == "1,234.50"


def test_the_nav_marks_the_current_page_and_nothing_else() -> None:
    html = render.page(title="Status", path="/activity", body="")
    assert '<a href="/activity" class="on">Activity</a>' in html
    assert '<a href="/" class="on">' not in html


def test_the_shell_emits_a_meta_refresh_only_when_asked() -> None:
    assert 'http-equiv="refresh"' in render.page(title="t", path="/", body="", refresh_sec=15)
    assert 'http-equiv="refresh"' not in render.page(title="t", path="/", body="")


def test_the_shell_ships_no_javascript() -> None:
    html = render.page(title="t", path="/", body="<p>hi</p>", refresh_sec=15)
    assert "<script" not in html.lower()
    assert "onclick" not in html.lower()
