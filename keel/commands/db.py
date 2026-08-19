"""`keel db` -- local data import/maintenance (read-only with respect to the exchange)."""

from __future__ import annotations

from pathlib import Path

import click

from keel.commands._common import _open_repo, with_disclaimer
from keel.data.csv_import import ImportResult, import_dir


def validated_import_dir(dir_path: str) -> Path:
    """The DIR_PATH validation the CLI's argument performs, ONE implementation for both
    front-ends (issue #391 C5): click's own `Path(exists=True, file_okay=False)` type,
    re-raised with the CLI argument's own hint so `exc.format_message()` reads EXACTLY as
    the CLI's usage error ("Invalid value for 'DIR_PATH': ...") -- a caller renders
    `f"Error: {exc.format_message()}"` and gets the CLI's line, byte for byte, instead of
    a second wording. Returns the resolved Path.
    """
    param_type = click.Path(exists=True, file_okay=False)
    try:
        return Path(str(param_type.convert(dir_path, None, None)))
    except click.BadParameter as exc:
        raise click.BadParameter(str(exc), param_hint="'DIR_PATH'") from None


def render_import_result(result: ImportResult) -> list[str]:
    """The lines `keel db import` prints -- the shared twin every front-end shows, so an
    import reads identically wherever it renders (warnings included, verbatim)."""
    lines = [f"imported={result.imported} skipped={result.skipped}"]
    lines.extend(f"  warning: {warning}" for warning in result.warnings)
    return lines


@click.group("db")
def db_group() -> None:
    """Local data import/maintenance commands."""


@db_group.command("import")
@click.argument("dir_path", type=click.Path(exists=True, file_okay=False))
@click.pass_context
@with_disclaimer
def db_import(ctx: click.Context, dir_path: str) -> None:
    """Import every `*.csv` Coinbase export in DIR_PATH (read-only w.r.t. the exchange)."""
    repo = _open_repo(ctx)
    for line in render_import_result(import_dir(validated_import_dir(dir_path), repo)):
        click.echo(line)
