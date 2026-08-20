# PyInstaller spec for the desktop bundle (#438). Run from the repository root:
#
#     pyinstaller packaging/keel.spec --noconfirm
#
# `--onedir`, not `--onefile`: faster start (no self-extraction per run), simpler per-binary
# signing for notarisation, and a lower AV/SmartScreen false-positive rate.
#
# Everything that has to be told to PyInstaller is computed by `keel.freeze`, not written here.
# That module's docstring records what each input is for and what breaks without it -- three of
# the four failures are silent, so the list is not something to maintain by hand.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH).parent))

from PyInstaller.utils.hooks import collect_data_files, copy_metadata  # noqa: E402

from keel.freeze import freeze_inputs  # noqa: E402

_inputs = freeze_inputs()

datas = []
for package in _inputs["collect_data"]:
    datas += collect_data_files(package)
for distribution in _inputs["copy_metadata"]:
    datas += copy_metadata(distribution)

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(Path(SPECPATH).parent)],
    binaries=[],
    datas=datas,
    hiddenimports=list(_inputs["hiddenimports"]),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="keel",
    debug=False,
    strip=False,
    upx=False,  # UPX raises AV false positives, which is the opposite of what signing buys us
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="keel")
