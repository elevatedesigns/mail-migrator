# PyInstaller spec for Mail Migrator. Build with:  python build.py app
import sys
from pathlib import Path

sys.path.insert(0, SPECPATH)
from mailmigrator import __version__

name = "Mail Migrator"
here = Path(SPECPATH)

# The standalone imapsync binary is copied in by build.py after this spec runs:
# PyInstaller would try to re-sign it on macOS, which PAR-packed binaries do not allow.
datas = []

# Only the Qt modules the app uses; keeps the download small.
excludes = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtBluetooth",
            "tkinter"]

a = Analysis(["run_app.py"], pathex=[SPECPATH], datas=datas, excludes=excludes)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=False,
          upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name=name, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{name}.app",
        bundle_identifier="info.lamiral.imapsync.mailmigrator",
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
