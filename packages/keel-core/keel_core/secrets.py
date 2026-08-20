"""Where a credential comes from, and the OS keychain as one of the places it can (#437).

Until now a credential lived in exactly one place: a git-ignored `.env` beside the deployment.
That is a fine answer for an operator who chose the folder and can see the file. It is the wrong
answer for the desktop product, where the person installing keel has no terminal, no editor open
on a dotfile, and no way to create one -- and it is a worse answer than it needs to be even for
an operator, because a plaintext secret at rest is a plaintext secret at rest.

So there are three sources now, and the ORDER is the whole design:

1. **The real environment.** Explicit, ephemeral, and set by whoever launched the process. It has
   always won for the venues that read it, and it still does.
2. **The `.env` file.** The operator's own artifact, in a folder they chose, which they can read
   and diff and delete. Deliberately ABOVE the keychain: every existing deployment keeps
   behaving byte-identically, and a value someone can see beats one they cannot when the two
   disagree. A stale keychain entry silently overriding an edited `.env` is a debugging session
   nobody should have to have.
3. **The OS keychain** -- macOS Keychain, Windows Credential Manager, Secret Service on Linux,
   through `keyring`. What the first-run wizard writes, because it is the only one of the three a
   person with no terminal can populate.

`ResolvedSecret` carries the SOURCE alongside the value, and that is not decoration: "keel cannot
see your key" and "keel is using a different key than the one you just typed" are the two support
questions this module exists to make answerable, and only the source distinguishes them.

**Nothing here logs a value, ever.** `ResolvedSecret.__repr__` is overridden for the same reason:
a dataclass that prints its own secret in a traceback has published it to every log the traceback
reaches.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

#: The keychain "service" every keel secret is filed under. One namespace, so an operator can
#: find and revoke the whole set in Keychain Access or Credential Manager without knowing which
#: names keel happens to use this release.
KEYCHAIN_SERVICE = "keel-trader"


class SecretSource(str, Enum):
    ENVIRONMENT = "environment"
    ENV_FILE = "env-file"
    KEYCHAIN = "keychain"
    ABSENT = "absent"


@dataclass(frozen=True)
class ResolvedSecret:
    name: str
    value: str | None
    source: SecretSource

    @property
    def found(self) -> bool:
        return self.value is not None

    def __repr__(self) -> str:
        """Never the value. A dataclass that prints its own secret in a traceback has published
        it to every log that traceback reaches, and tracebacks travel further than anything else
        in a program."""
        state = "set" if self.found else "unset"
        return f"ResolvedSecret(name={self.name!r}, source={self.source.value!r}, {state})"


def keychain_available() -> bool:
    """Whether a real keychain backend is present.

    `keyring` always imports and always answers; on a machine with no usable backend it selects
    `fail.Keyring`, whose every operation raises. Detecting that HERE means the caller can offer
    the `.env` path instead of showing someone a form that will throw when they submit it --
    which is the difference between a headless Linux box and a broken install.
    """
    try:
        import keyring
        from keyring.backends import fail
    except Exception:
        return False
    try:
        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def _from_keychain(name: str) -> str | None:
    if not keychain_available():
        return None
    try:
        import keyring

        return keyring.get_password(KEYCHAIN_SERVICE, name)
    except Exception:
        # A locked keychain, a denied prompt, a backend that broke after being selected. None of
        # those is a reason to take the process down: the caller falls through to "absent" and
        # says so, which is the same message it would give for a credential never set.
        return None


def read_secret(name: str, *, env_path: str | Path | None = None) -> ResolvedSecret:
    """Resolve one secret, reporting WHERE it came from. Never raises."""
    from keel_core.config import default_env_path

    value = os.environ.get(name)
    if value:
        return ResolvedSecret(name, value, SecretSource.ENVIRONMENT)

    resolved = Path(env_path) if env_path is not None else default_env_path()
    if resolved.exists():
        try:
            from dotenv import dotenv_values

            file_value = dotenv_values(resolved).get(name)
        except Exception:
            file_value = None
        if file_value:
            return ResolvedSecret(name, file_value, SecretSource.ENV_FILE)

    stored = _from_keychain(name)
    if stored:
        return ResolvedSecret(name, stored, SecretSource.KEYCHAIN)
    return ResolvedSecret(name, None, SecretSource.ABSENT)


def store_secret(name: str, value: str) -> None:
    """Write one secret to the OS keychain.

    Raises when there is no usable backend rather than silently discarding the value -- a form
    that appears to save a credential and does not is worse than one that refuses, because the
    operator only finds out at the first request that needs it.
    """
    if not value:
        raise ValueError(f"refusing to store an empty value for {name}")
    if not keychain_available():
        raise RuntimeError(
            "no OS keychain is available on this machine, so the credential was NOT saved. "
            "Put it in a .env file beside your deployment instead."
        )
    import keyring

    keyring.set_password(KEYCHAIN_SERVICE, name, value)


def delete_secret(name: str) -> bool:
    """Remove one secret from the keychain. `False` when there was nothing to remove.

    Only ever touches the keychain: a `.env` file is the operator's own artifact and keel does not
    edit it. Deleting a line out of a file someone hand-wrote, on their behalf, is not a thing a
    setup flow should do.
    """
    if not keychain_available():
        return False
    try:
        import keyring
        import keyring.errors

        keyring.delete_password(KEYCHAIN_SERVICE, name)
        return True
    except Exception:
        return False


def describe_sources(
    names: tuple[str, ...], *, env_path: str | Path | None = None
) -> list[ResolvedSecret]:
    """Resolve several secrets for display. Values are carried but must not be rendered."""
    return [read_secret(name, env_path=env_path) for name in names]
