"""`keel credentials` -- put an API key in the OS keychain instead of a plaintext file.

Three subcommands, and the shape of each is a security decision rather than a UI one.

`set` NEVER takes the value as an argument. A secret on a command line is in shell history, in
`ps` output for every other process on the machine while it runs, and in any terminal recording.
It is prompted for with echo off, or piped in on stdin for a script that already has it.

`show` never prints a value. It answers "is it set, and which of the three places is keel actually
reading it from" -- the two questions that are worth asking, and the only ones answerable without
putting the secret on a screen someone may be sharing.

`forget` only ever touches the keychain. A `.env` file is the operator's own artifact and keel does
not edit it; deleting a line out of a file someone hand-wrote, on their behalf, is not something a
credential command should do. It says so when a `.env` value is what is actually in use, because
otherwise "forget" would appear not to work.
"""

from __future__ import annotations

import sys

import click
from keel_core.secrets import (
    KEYCHAIN_SERVICE,
    SecretSource,
    delete_secret,
    describe_sources,
    keychain_available,
    read_secret,
    store_secret,
)

#: The credentials keel knows how to talk about, with what each is for. Not a closed list of what
#: may be STORED -- an adapter's own names work too -- but these are the ones `show` reports on
#: unasked, because a blank report is not an answer.
KNOWN: tuple[tuple[str, str], ...] = (
    ("CDP_API_KEY", "Coinbase Developer Platform key -- market data, and orders if trade-enabled"),
    ("CDP_API_SECRET", "the matching CDP secret"),
    ("ALPACA_API_KEY_ID", "Alpaca key id (US equities)"),
    ("ALPACA_API_SECRET_KEY", "the matching Alpaca secret"),
    ("ROBINHOOD_API_KEY_CREDENTIAL", "Robinhood API credential identifier"),
    ("ROBINHOOD_PRIVATE_KEY", "the matching Robinhood Ed25519 private seed"),
)

_SOURCE_NOTE = {
    SecretSource.ENVIRONMENT: "from the environment (set by whoever launched this process)",
    SecretSource.ENV_FILE: "from your .env file",
    SecretSource.KEYCHAIN: "from the OS keychain",
    SecretSource.ABSENT: "not set anywhere keel looks",
}


@click.group("credentials")
def credentials_group() -> None:
    """Store API credentials in the OS keychain, and see which one keel is using."""


@credentials_group.command("show")
@click.pass_context
def credentials_show(ctx: click.Context) -> None:
    """Which credentials keel can see, and where each comes from. Never prints a value.

    The source is the useful half. "keel cannot see your key" and "keel is using a different key
    than the one you just typed" are the two questions worth asking, and only the source tells
    them apart.
    """
    obj = ctx.obj or {}
    env_path = obj.get("env_path")
    resolved = describe_sources(tuple(name for name, _why in KNOWN), env_path=env_path)
    notes = dict(KNOWN)
    for item in resolved:
        mark = "set  " if item.found else "unset"
        click.echo(f"{mark} {item.name}")
        click.echo(f"        {_SOURCE_NOTE[item.source]}")
        click.echo(f"        {notes[item.name]}")
    click.echo("")
    if keychain_available():
        click.echo(f"keychain: available (service {KEYCHAIN_SERVICE!r})")
    else:
        click.echo(
            "keychain: NOT available on this machine -- use a .env file beside your deployment."
        )
    click.echo(
        "precedence: environment, then .env, then the keychain. A value you can see wins over "
        "one you cannot."
    )


@credentials_group.command("set")
@click.argument("name")
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help="Read the value from stdin instead of prompting (for scripts that already hold it).",
)
def credentials_set(name: str, from_stdin: bool) -> None:
    """Store NAME in the OS keychain. The value is never taken as an argument.

    A secret on a command line is in shell history, in `ps` output for every other process on the
    machine while the command runs, and in any terminal recording. So it is prompted for with echo
    off, or piped in.

    This does not touch your `.env`. If a `.env` already holds this name it will keep winning --
    `keel credentials show` says which one is in use, and that is deliberate: a value you can see
    beats one you cannot.
    """
    if from_stdin:
        value = sys.stdin.read().strip()
    else:
        value = click.prompt(f"value for {name}", hide_input=True, default="", show_default=False)
    value = value.strip()
    if not value:
        raise click.ClickException("no value given; nothing was stored.")

    try:
        store_secret(name, value)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    # Read it back through the SAME resolver a real caller uses, so the confirmation reflects what
    # keel will actually do rather than what was just written. If a `.env` shadows it, this is
    # where the operator finds out -- not at the first request that used the wrong key.
    resolved = read_secret(name)
    click.echo(f"stored {name} in the OS keychain.")
    if resolved.source is not SecretSource.KEYCHAIN:
        click.echo(
            f"note: keel will still read {name} {_SOURCE_NOTE[resolved.source]}, which takes "
            "precedence. Remove it there if you meant the keychain value to be used."
        )


@credentials_group.command("forget")
@click.argument("name")
def credentials_forget(name: str) -> None:
    """Remove NAME from the OS keychain. Never edits your `.env`."""
    removed = delete_secret(name)
    if removed:
        click.echo(f"removed {name} from the OS keychain.")
    else:
        click.echo(f"{name} was not in the OS keychain; nothing removed.")

    resolved = read_secret(name)
    if resolved.found:
        click.echo(
            f"note: keel can still see {name} {_SOURCE_NOTE[resolved.source]}. keel does not edit "
            "that file -- it is yours."
        )
