"""`keel versions` -- the deploy check that can actually fail.

`keel --version` answers "which build is this?" for the `keel-trader` distribution and nothing
else, so it cannot see the failure it is used to rule out: installing the `keel_trader` wheel
alone leaves `keel-core` and the adapters at their old versions, and `--version` still prints the
new number. `~/keel` ran `keel-trader 0.5.7` against `keel-core 0.5.5` across two releases with
that check passing every time. A verification step blind to the failure mode is worse than none,
because it is trusted.

This command prints the same build-identity line and then every `keel-*` distribution installed
in the running interpreter's environment, and **exits non-zero** when they disagree -- so it can
be the last line of a deploy script and mean something. The rules live in
`keel.version.InstallReport.problems`, which is a pure value and unit-tested without installing
anything; this module is only the rendering and the exit code.

No config, no database, no network: nothing about it can fail for environmental reasons, which is
what makes a non-zero exit unambiguous.

**Why a CLI command and not `scripts/`.** `scripts/` is operator tooling that is not shipped in
the wheel, and a deployment is a `.venv` beside a `Release/` directory with no checkout of this
repository at all -- a script there could not be run without first fetching it. The check has to
travel inside the artifact it is checking.
"""

from __future__ import annotations

import click

from keel.version import build_info, check_install


@click.command("versions")
@click.pass_context
def versions_cmd(ctx: click.Context) -> None:
    """Verify the whole install: every keel distribution's version, not just keel-trader's."""
    info = build_info()
    report = check_install(source=info.source)

    click.echo(info.describe())
    if not info.is_reproducible:
        click.echo(
            "warning: this build is NOT reproducible -- it does not correspond to a commit. "
            "Do not run it against live funds.",
            err=True,
        )

    if not report.distributions:
        # Nothing installed: a source checkout run via `uv run` with the workspace on the path.
        # There is no install to disagree with itself, so there is nothing to fail on.
        click.echo("no keel distributions installed -- nothing to compare.")
        return

    width = max(len(name) for name in report.distributions) + 2
    click.echo("")
    for name, version in sorted(report.distributions.items()):
        click.echo(f"{name.ljust(width)}{version}")
    click.echo("")

    if not report.problems:
        n = len(report.distributions)
        click.echo(f"ok: {n} keel distributions, all at {report.versions[0]}.")
        return

    for problem in report.problems:
        click.echo(f"error: {problem}", err=True)
    ctx.exit(1)
