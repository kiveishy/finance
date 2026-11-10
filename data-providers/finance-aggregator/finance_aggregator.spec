# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec shared by build.cmd (Windows) and build.sh (Linux).
#   python -m PyInstaller --noconfirm --clean --distpath dist/<os> --workpath build/<os> finance_aggregator.spec
import json
import os
import sys
from pathlib import Path

app_dir = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
dist_dir = Path(DISTPATH).resolve()  # noqa: F821 - provided by PyInstaller
yahoo_db_dir = Path(os.environ.get("YAHOO_DB_DIR", app_dir.parent / "yahoo-db")).resolve()

# yahoo-db's REST client is loaded by path at runtime; bundle it as a data file.
client_file = yahoo_db_dir / "yahoodb-api.py"
if not client_file.is_file():
    raise SystemExit(f"yahoo-db client not found: {client_file} (set YAHOO_DB_DIR)")

a = Analysis(  # noqa: F821
    [str(app_dir / "main.py")],
    pathex=[str(app_dir)],
    datas=[(str(client_file), "yahoo_db")],
    # requests is only imported by the dynamically loaded yahoo-db client
    hiddenimports=["requests"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="finance-aggregator",
    console=True,
    upx=False,
    strip=False,
    # Long-running 'serve': keep the onefile extraction out of %TEMP% (Storage Sense cleans it).
    runtime_tmpdir=r"%LOCALAPPDATA%\finance-aggregator\runtime" if sys.platform == "win32" else None,
)


def _rebase(value: str) -> str:
    """Make a path that was relative to the sources relative to the dist folder."""
    if not value or Path(value).is_absolute():
        return value
    target = (app_dir / value).resolve()
    try:
        return os.path.relpath(target, dist_dir)
    except ValueError:  # different drive on Windows
        return str(target)


dist_config = dist_dir / "config.json"
if dist_config.exists():
    print(f"[spec] keeping existing {dist_config}")
else:
    config = json.loads((app_dir / "config.json").read_text(encoding="utf-8"))
    for provider in config.get("providers", []):
        options = provider.get("options") or {}
        for key in ("db_path", "client_source_dir"):
            if isinstance(options.get(key), str):
                options[key] = _rebase(options[key])
    dist_config.write_text(json.dumps(config, indent=4) + "\n", encoding="utf-8")
    print(f"[spec] wrote {dist_config}")
