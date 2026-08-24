"""What a frozen bundle has to be told, because PyInstaller cannot work it out (#438).

Five things break when keel is frozen, and four of them break SILENTLY. That is what makes this
module worth having rather than a handful of flags in a build script: every one of these was found
by building a bundle and running it, and every one of them produced a binary that started
cleanly and was wrong.

**1. No distribution metadata, so `keel versions` sees nothing.** `importlib.metadata` has no
`.dist-info` to read inside a bundle, so the deploy check that exists to catch a partial upgrade
answers `no keel distributions installed -- nothing to compare`. The one check whose whole
purpose is to fail loudly instead reports success by having nothing to say.

**2. No entry points, so the venue adapters do not exist.** `keel brokers list` reported
`0 adapter(s) installed under keel.brokers`, and `load_broker` raises `LookupError` for every
venue. A packaged keel with no adapters is not a trading tool at all -- and it says so only if
you ask it to list them.

**3. Metadata alone is not enough: the adapter MODULES are imported dynamically.**
`discover_brokers()` loads them through the entry point, so PyInstaller's static analysis never
sees the import. With metadata collected but the module absent, `brokers list` stops answering
zero and starts raising `ModuleNotFoundError` instead -- which is at least loud.

**4. The config templates are package DATA.** `init-config` reads them through
`importlib.resources`; without them a first run cannot write a config at all.

**5. The web UI's static assets are package DATA too (#535).** `keel/web/staticfiles.py` finds
them with `Path(__file__).parent / "static"` rather than `importlib.resources` -- a plain
filesystem lookup relative to the module's OWN frozen location, which resolves correctly only if
PyInstaller actually copied `keel/web/static/` alongside the frozen `staticfiles.py`. Without
this, `keel serve` binds and every rendered page loads (they carry no static reference), so the
bundle looks healthy right up until #536's client -- or anyone hitting `/static/*` today -- gets
a 404 with no terminal to diagnose it from, on the exact double-click path #535 exists to make
work.

Everything here is computed from the build environment rather than hardcoded. A hardcoded list is
the same failure one release later: adding a fifth adapter and forgetting to add it here would
produce exactly the silent `0 adapter(s)` bundle that (2) describes.
"""

from __future__ import annotations

from keel.version import DEV_ONLY_DISTRIBUTIONS, installed_distributions

#: The package holding `config.yaml` / `config.live.yaml`, read via `importlib.resources`.
TEMPLATE_PACKAGE = "keel.templates"

#: The package holding the web UI's static assets (#535), read via a plain filesystem path
#: (`keel/web/staticfiles.py`'s `STATIC_ROOT`), not `importlib.resources` -- so unlike
#: `TEMPLATE_PACKAGE` this name never needs to reach `hidden_imports()`: nothing anywhere calls
#: `importlib.import_module` or `importlib.resources.files` on it by string. It still has to
#: reach `collect_data` below, because that is the step that copies the directory into the
#: bundle at all; naming it here is what stops that copy from being forgotten the way the
#: config templates already were once (see the module docstring's point 5).
STATIC_PACKAGE = "keel.web.static"


def _module_of(distribution: str) -> str:
    """`keel-broker-fake` -> `keel_broker_fake`. The import package for a keel distribution is
    its name with hyphens replaced, which holds for every member of the family."""
    return distribution.replace("-", "_")


#: Import packages that must never enter a shipped bundle. `keel-broker-fake` is a deterministic
#: in-process test venue; `keel.version.DEV_ONLY_DISTRIBUTIONS` already names it as dev-only and
#: `tests/test_packaging.py` keeps it out of every runtime dependency list. Freezing it would put
#: a FAKE VENUE in the venue list of a signed install a real person downloaded -- which is worse
#: than shipping nothing, because it looks like a supported option.
EXCLUDED_MODULES: frozenset[str] = frozenset(_module_of(name) for name in DEV_ONLY_DISTRIBUTIONS)


def metadata_distributions() -> tuple[str, ...]:
    """Every `keel-*` distribution installed in the BUILD environment, for `copy_metadata`.

    Read from the environment because that is what a bundle will contain, and because a hardcoded
    list silently omits whatever was added since it was written -- which is the failure this whole
    module exists to prevent."""
    return tuple(
        sorted(name for name in installed_distributions() if name not in DEV_ONLY_DISTRIBUTIONS)
    )


def broker_modules() -> tuple[str, ...]:
    """Top-level module of every adapter registered under `keel.brokers`.

    These are imported dynamically through the entry point, so PyInstaller never sees them and
    they must be named as hidden imports. Derived from the entry points themselves so the answer
    cannot drift from what `discover_brokers()` will actually try to load."""
    from keel_broker_api.registry import ENTRY_POINT_GROUP

    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - the stdlib is present
        return ()

    modules: set[str] = set()
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        # `module:attr` -- the module half is what has to be importable.
        module = (entry.value or "").split(":", 1)[0].strip()
        if not module:
            continue
        root = module.split(".", 1)[0]
        if root in EXCLUDED_MODULES:
            continue
        modules.add(module)
        modules.add(root)
    return tuple(sorted(modules))


def hidden_imports() -> tuple[str, ...]:
    """Everything the bundle must contain that nothing statically imports."""
    return tuple(sorted({TEMPLATE_PACKAGE, *broker_modules()}))


def freeze_inputs() -> dict[str, tuple[str, ...]]:
    """The whole answer, as one value -- what the spec file reads and what the tests check."""
    return {
        "hiddenimports": hidden_imports(),
        "copy_metadata": metadata_distributions(),
        "collect_data": (TEMPLATE_PACKAGE, STATIC_PACKAGE),
    }
