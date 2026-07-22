"""`keel db` -- local data import/maintenance (read-only with respect to the exchange)."""

from __future__ import annotations

import click

from keel.commands._common import _open_repo, with_disclaimer
from keel.data.csv_import import import_dir


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
    result = import_dir(dir_path, repo)
    click.echo(f"imported={result.imported} skipped={result.skipped}")
    for warning in result.warnings:
        click.echo(f"  warning: {warning}")
