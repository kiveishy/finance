# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec shared by build.cmd (Windows) and build.sh (Linux).
#   python -m PyInstaller --noconfirm --clean --distpath dist/<os> --workpath build/<os> snp-monitor.spec
import json
import os
import sys
from pathlib import Path

app_dir = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
dist_dir = Path(DISTPATH).resolve()  # noqa: F821 - provided by PyInstaller

a = Analysis(  # noqa: F821
    [str(app_dir / "main.py")],
    pathex=[str(app_dir)],
    datas=[
        (str(app_dir / "web"), "web"),
        (str(app_dir / "snp_monitor" / "sp500_snapshot.json"), "snp_monitor"),
    ],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "_pytest"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="snp-monitor",
    # Windows: no terminal window - the UI is the Chrome window, errors go to logs/ and a message box.
    console=sys.platform != "win32",
    upx=False,
    # Long-running server: keep the onefile extraction out of %TEMP% (Storage Sense cleans it).
    runtime_tmpdir=r"%LOCALAPPDATA%\snp-monitor\runtime" if sys.platform == "win32" else None,
)

# Standalone layout: snp-monitor(.exe) + config.json, with chrome_dir rebased to the dist folder.
config_path = dist_dir / "config.json"
if config_path.exists():
    print(f"[spec] keeping existing {config_path}")
else:
    config = json.loads((app_dir / "config.json").read_text(encoding="utf-8"))
    browser = config.setdefault("browser", {})
    chrome_dir = browser.get("chrome_dir") or "../../common/chrome"
    if not Path(chrome_dir).is_absolute():
        target = (app_dir / chrome_dir).resolve()
        try:
            browser["chrome_dir"] = os.path.relpath(target, dist_dir)
        except ValueError:  # different drive on Windows
            browser["chrome_dir"] = str(target)
    config_path.write_text(json.dumps(config, indent=4) + "\n", encoding="utf-8")
    print(f"[spec] wrote {config_path}")
