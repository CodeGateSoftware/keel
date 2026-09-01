"""What a frozen bundle must be told (#438), and the silent failures behind each item.

Every assertion here traces to something an actual PyInstaller build got wrong on 2026-08-20.
Three of the four produced a binary that started cleanly and was WRONG, which is why the inputs
are computed from the environment and pinned here rather than written into a build script:

* metadata absent  -> `keel versions` answers "no keel distributions installed" -- the deploy
  check whose entire purpose is to catch a partial upgrade reports success by having nothing to
  compare;
* entry points absent -> `keel brokers list` answers `0 adapter(s)`, and `load_broker` raises for
  every venue. A packaged keel with no adapters is not a trading tool;
* adapter modules absent (metadata present) -> `ModuleNotFoundError` at first use, which is at
  least loud;
* templates absent -> `init-config` cannot write a config, so a first run cannot start.
"""

from __future__ import annotations

import ast
from pathlib import Path

from keel.freeze import (
    EXCLUDED_MODULES,
    STATIC_PACKAGE,
    TEMPLATE_PACKAGE,
    broker_modules,
    freeze_inputs,
    hidden_imports,
    metadata_distributions,
)
from keel.version import DEV_ONLY_DISTRIBUTIONS

_ROOT = Path(__file__).resolve().parents[1]


def test_every_registered_adapter_is_a_hidden_import() -> None:
    """`discover_brokers()` loads adapters THROUGH the entry point, so PyInstaller's static
    analysis never sees the import. Computed from the entry points themselves, so a fifth adapter
    cannot be added without the bundle learning about it -- the hardcoded-list version of this
    would ship the silent `0 adapter(s)` bundle one release later."""
    from importlib.metadata import entry_points

    from keel_broker_api.registry import ENTRY_POINT_GROUP

    registered = {
        (entry.value or "").split(":", 1)[0].split(".", 1)[0]
        for entry in entry_points(group=ENTRY_POINT_GROUP)
    }
    shippable = {name for name in registered if name and name not in EXCLUDED_MODULES}
    assert shippable, "no adapters are installed, which would make this test vacuous"
    assert shippable <= set(broker_modules())
    assert shippable <= set(hidden_imports())


def test_the_dev_only_fake_venue_is_never_bundled() -> None:
    """`keel-broker-fake` is a deterministic in-process test venue. Freezing it would put a FAKE
    VENUE in the venue list of a signed install a real person downloaded -- which is worse than
    shipping nothing, because it looks like a supported option."""
    assert DEV_ONLY_DISTRIBUTIONS, "the exclusion list must not be empty"
    for distribution in DEV_ONLY_DISTRIBUTIONS:
        assert distribution not in metadata_distributions()
        module = distribution.replace("-", "_")
        assert module in EXCLUDED_MODULES
        assert module not in broker_modules()
        assert module not in hidden_imports()


def test_the_shipping_distributions_carry_their_metadata() -> None:
    """Without this, `keel versions` -- the one check that exists to catch a partial upgrade --
    has nothing to compare and reports success by saying nothing."""
    shipped = set(metadata_distributions())
    assert {"keel-trader", "keel-core", "keel-broker-api"} <= shipped
    assert all(name.startswith("keel-") for name in shipped)


def test_the_config_templates_are_collected() -> None:
    """`init-config` reads them through `importlib.resources`; without them a first run cannot
    write a config at all."""
    inputs = freeze_inputs()
    assert TEMPLATE_PACKAGE in inputs["collect_data"]
    assert TEMPLATE_PACKAGE in inputs["hiddenimports"]


def test_the_web_ui_static_assets_are_collected() -> None:
    """(#535) `keel/web/staticfiles.py` finds its assets with `Path(__file__).parent / "static"`
    -- a filesystem lookup relative to the frozen module's OWN location -- so `/static/*` (and
    every request #536's client makes for its own shell) 404s in the desktop bundle unless
    PyInstaller actually copies `keel/web/static/` in. Not asserted against `hiddenimports`,
    unlike the templates above: nothing calls `importlib.import_module` or
    `importlib.resources.files` on `STATIC_PACKAGE` by string, so there is nothing for PyInstaller
    to fail to statically discover -- only `collect_data`, the copy step, applies here."""
    inputs = freeze_inputs()
    assert STATIC_PACKAGE in inputs["collect_data"]


def test_the_inputs_are_computed_and_not_hardcoded() -> None:
    """The failure this module exists to prevent is a list that goes stale. If `freeze.py` ever
    grows a literal adapter name, this is where that shows up."""
    source = (_ROOT / "keel" / "freeze.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for adapter in broker_modules():
        assert adapter not in literals, (
            f"{adapter!r} is hardcoded in keel/freeze.py -- the inputs must be read from the "
            "environment, or the next adapter added will be silently missing from the bundle"
        )


# -- the spec file -----------------------------------------------------------------------------


def test_the_spec_exists_and_reads_its_inputs_from_this_module() -> None:
    """A spec that hardcoded its own lists would be a second place to forget an adapter."""
    spec = (_ROOT / "packaging" / "keel.spec").read_text(encoding="utf-8")
    assert "from keel.freeze import freeze_inputs" in spec
    for adapter in broker_modules():
        assert adapter not in spec


def test_the_spec_uses_every_input_this_module_produces() -> None:
    """An input computed and then not passed to PyInstaller is the same as not computing it."""
    spec = (_ROOT / "packaging" / "keel.spec").read_text(encoding="utf-8")
    for key in freeze_inputs():
        assert key in spec, f"the spec never reads {key!r}"


def test_the_spec_builds_onedir_and_does_not_compress() -> None:
    """`--onedir` for faster start, simpler per-binary notarisation signing, and a lower AV
    false-positive rate. UPX is off for the same reason signing is on: compression raises AV
    false positives, which is the opposite of what a signed artifact is buying."""
    spec = (_ROOT / "packaging" / "keel.spec").read_text(encoding="utf-8")
    assert "COLLECT(" in spec
    assert "upx=False" in spec
    assert "onefile" not in spec.lower().replace("--onefile`", "")


def test_the_spec_entry_point_exists_and_calls_the_repositorys_cli() -> None:
    """Pointing PyInstaller at the INSTALLED console script would freeze whatever that script
    happened to be in the build environment rather than this repository's CLI.

    It must import from `keel.cli` -- and specifically `main`, not `cli`, since #663: `main` is
    where the closed-stdout handler lives, and a frozen bundle that called `cli` directly would
    be the ONE build without it. That is the build the Windows release leg runs, and the one
    that died on `brokers list | head -1`.
    """
    entry = _ROOT / "packaging" / "entry.py"
    assert entry.exists()
    source = entry.read_text(encoding="utf-8")
    assert "from keel.cli import main" in source, (
        "the frozen entry point must import this repository's CLI entry, not the installed script"
    )
    assert "    main()" in source, "the frozen entry point must CALL main, not merely import it"
    assert "from keel.cli import cli" not in source, (
        "calling `cli` directly skips main()'s closed-stdout handler -- the frozen Windows "
        "bundle is exactly where that failure surfaced (#663)"
    )


def test_the_packaging_directory_ships_no_python_package_marker() -> None:
    """`packaging` is also the name of a real PyPI distribution that keel depends on
    transitively. A directory here with an `__init__.py` would be importable as `packaging` from
    the repository root and would shadow it."""
    assert not (_ROOT / "packaging" / "__init__.py").exists()


# -- the first-run bug the build found ---------------------------------------------------------


def test_serve_does_not_refuse_to_start_when_there_is_no_deployment(tmp_path: Path) -> None:
    """`keel serve` used to call `sqlite3.connect` on a database whose PARENT DIRECTORY did not
    exist, so on a machine with no deployment it raised `unable to open database file` and
    refused to start -- before serving the setup page that exists to fix exactly that.

    Found by running a frozen bundle on a clean machine, and it reproduces unfrozen."""
    from keel.web.server import ensure_schema

    missing = tmp_path / "nothing-here" / "keel.db"
    ensure_schema(str(missing))  # must not raise
    assert not missing.exists()
    assert not missing.parent.exists(), (
        "a read-only view must not bring a deployment into existence by being started"
    )


def test_ensure_schema_still_migrates_a_database_that_does_exist(tmp_path: Path) -> None:
    """The fix must not turn migration off. An existing database is still brought up to date at
    startup, which is what makes an upgrade self-heal."""
    from keel.data.db import connect
    from keel.web.server import ensure_schema

    db_path = tmp_path / "keel.db"
    connect(str(db_path)).close()  # an empty file, no schema
    ensure_schema(str(db_path))
    conn = connect(str(db_path))
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert "rules" in tables and "schema_version" in tables
